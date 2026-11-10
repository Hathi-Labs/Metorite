"""Live observability API — the operator-facing face of the activity bus (E2).

Where ``/debug`` (routes/debug.py) is the *post-hoc* view over the durable
``agent_run`` trace store, this is the *live* view: a real-time feed of every
agent run and model call as it happens, across chat AND every app, plus a
"running right now" snapshot. Backed by the tenant's ``cc:<org>:activity`` Redis
stream (``acb_common.activity``), not Postgres — this is the ephemeral signal
layer.

    GET /observability/activity/recent?limit=   → recent activations (backfill)
    GET /observability/activity/stream          → SSE live feed (new events)
    GET /observability/active                    → agent runs in flight now
    GET /observability/roster                    → all agents + working/idle,
                                                     plus any Custom App
                                                     currently consuming tokens
    GET /observability/cost?days=                → daily LLM cost rollup
    GET /observability/runs?agent=&status=       → DURABLE history (agent_run)

Open to any AUTHENTICATED caller (this is the operator's own dashboard). It
returns operational *metadata* — agent/model/tier/user/duration/tokens/cost and
error messages — but NEVER the full message-content trace, which stays
EXECUTIVE-gated behind ``/debug/runs/{id}``. The Redis feed (recent/stream) is
ephemeral (~2000 events for each tenant, latest-first); ``/runs`` is the
durable timeline.

Tenancy (H-201c). The feed, the stream and the presence set are keyed for each
tenant. Each route takes the org from the authenticated session
(``viewer.organization_id``), never from the request, and passes it to the
reader. A viewer with no org gets an empty answer.

The cost view (``/cost``) is keyed for each tenant too (H-201e). It reads
``cc:<viewer org>:cost:<day>`` only. A viewer with no org gets the empty
summary. The deployment-level hash of the priced calls with no tenant
(``cc:_unattributed:cost:<day>``) is never read here.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any, AsyncIterator

from acb_auth import UserContext, UserRole, require_role
from acb_common import (
    active_runs,
    cost_summary,
    get_logger,
    read_activity_since,
    recent_activity,
)
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

_log = get_logger("gateway.observability")

# Any AUTHENTICATED caller may read the live views. These expose operational
# METADATA (agent/model/tokens/cost/status/duration) — NOT message content — so
# unlike /debug (full traces, EXECUTIVE-only) this is the operator's own
# dashboard and must not silently 403 for a non-"executive" logged-in operator.
# EMPLOYEE is the default role the SSO proxy sends; AGENT is internal tooling.
_VIEWER = require_role(UserRole.EXECUTIVE, UserRole.AGENT, UserRole.EMPLOYEE)
# Avatar writes + Pixel Lab generation are cosmetic operator actions on this
# internal dashboard. Gate them to any AUTHENTICATED caller (same set as the
# viewer) rather than EXECUTIVE-only — the SSO proxy sends EMPLOYEE by default
# unless the operator's email is in EXECUTIVE_EMAILS, so an EXECUTIVE gate would
# silently 403 the very operator using the office (the Phase 6.3 lesson).
_WRITER = require_role(UserRole.EXECUTIVE, UserRole.AGENT, UserRole.EMPLOYEE)

router = APIRouter(prefix="/observability", tags=["observability"])


@router.get("/activity/recent")
async def activity_recent(
    limit: int = 100,
    viewer: UserContext = _VIEWER,
) -> dict[str, Any]:
    """Recent activations of the viewer's tenant, oldest-first — the backfill.

    ``limit`` is clamped to [1, 500].
    """
    events = await recent_activity(limit=limit, organization_id=viewer.organization_id)
    return {"events": events, "count": len(events)}


@router.get("/active")
async def active(
    viewer: UserContext = _VIEWER,
) -> dict[str, Any]:
    """Agent runs currently in flight (the "running right now" panel).

    Self-healing: a run whose end event was lost ages out of the presence set
    after ``acb_common.activity.LIVE_TTL_SECONDS``.
    """
    runs = await active_runs(organization_id=viewer.organization_id)
    return {"runs": runs, "count": len(runs)}


def _effective_runtime(name: str, entry: dict[str, Any], org: str | None) -> str:
    """The runtime that a run of *name* in *org* takes, not only its label.

    WS-43h (review P3): app-builder is labelled ``"maf"``, and its factory
    builds the Copilot agent for an org outside ``app_builder:<org>``.
    """
    label = str(entry.get("agent_runtime") or entry.get("runtime") or "maf")
    try:
        from orchestrator.code_session import APP_BUILDER_AGENT, app_builder_runtime_for
    except ImportError:
        return label
    return app_builder_runtime_for(org) if name == APP_BUILDER_AGENT else label


@router.get("/roster")
async def roster(
    viewer: UserContext = _VIEWER,
) -> dict[str, Any]:
    """Every registered agent + its live status — the office view's cast list.

    Merges the static + dynamically-registered agent registry with the live
    presence set so each agent reports ``working`` (an active run) or ``idle``.
    Deep per-agent history/errors come from ``/debug/runs?agent=`` on demand.
    """
    try:
        from gateway.routes.agent import (  # noqa: PLC0415
            _AGENT_REGISTRY,
            _load_agent_aliases,
            _load_dynamic_agents,
        )
        registry = list(_load_dynamic_agents()) + list(_AGENT_REGISTRY)
    except Exception as exc:  # noqa: BLE001
        _log.warning("observability.roster_registry_failed", error=str(exc))
        registry = []

    # Display-name (alias) overlay, keyed by canonical name — same source the
    # Agents page uses.  Best-effort → {} so the office still renders on error.
    try:
        aliases = _load_agent_aliases()
    except Exception:  # noqa: BLE001
        aliases = {}

    # The orchestrator is the default-chat agent but isn't a registered
    # specialist — seed it so it's always on stage (idle between chats, working
    # during them).
    registry = [{
        "name": "orchestrator",
        "description": "Core orchestrator — routes chat to the right specialist",
        "agent_runtime": "maf",
    }] + registry

    # The tenant of the session (H-201c): the presence set and the avatars.
    org = viewer.organization_id
    runs = await active_runs(organization_id=org)
    live_by_agent: dict[str, list[dict[str, Any]]] = {}
    for r in runs:
        live_by_agent.setdefault(str(r.get("agent") or ""), []).append(r)

    # Avatar overrides (Avatar Studio) — merged so every viewer sees the pinned
    # look/sprite. Best-effort; the office falls back to deriveAvatar() without it.
    avatars = _load_avatars(org)

    seen: set[str] = set()
    agents: list[dict[str, Any]] = []
    for a in registry:
        name = str(a.get("name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        live = live_by_agent.get(name, [])
        agents.append({
            "kind": "agent",
            "name": name,
            "display_name": aliases.get(name, ""),
            "description": str(a.get("description") or ""),
            "runtime": _effective_runtime(name, a, org),
            "status": "working" if live else "idle",
            "active_runs": len(live),
            "last_ts": live[0].get("ts") if live else None,
            "source": live[0].get("source") if live else None,
            "avatar": avatars.get(name),
        })

    # Include agents that are LIVE but not in the registry — most importantly the
    # "orchestrator" (the default-chat agent, which isn't a registered specialist)
    # and any ad-hoc sub-agent. Without this the primary agent never shows in the
    # office even while it's clearly working. Custom Apps ("app:<slug>" presence,
    # RFC docs/app-workshop/README.md §4.6) are handled separately below — they
    # need their real name/icon from the apps table, not agent-shaped fields.
    for name, live in live_by_agent.items():
        if not name or name in seen or name.startswith("app:"):
            continue
        seen.add(name)
        agents.append({
            "kind": "agent",
            "name": name,
            "display_name": aliases.get(name, ""),
            "description": "Core orchestrator" if name == "orchestrator" else "",
            "runtime": "maf",
            "status": "working",
            "active_runs": len(live),
            "last_ts": live[0].get("ts") if live else None,
            "source": live[0].get("source") if live else None,
            "avatar": avatars.get(name),
        })

    # Custom Apps currently consuming tokens — an app only ever appears here
    # WHILE live (unlike agents, whose full registry shows even when idle):
    # there is no "idle app" roster row, matching "show apps being used AT
    # THE MOMENT" rather than cluttering the office with every published app.
    app_slugs = [
        name[len("app:"):] for name in live_by_agent if name.startswith("app:")
    ]
    if app_slugs:
        app_details = _load_app_details(app_slugs, org)
        for slug in app_slugs:
            live = live_by_agent[f"app:{slug}"]
            details = app_details.get(slug, {})
            agents.append({
                "kind": "app",
                "name": f"app:{slug}",
                "slug": slug,
                "display_name": details.get("name") or slug,
                "description": "Custom App — consuming AI tokens right now",
                "icon": details.get("icon") or "",
                "runtime": "app",
                "status": "working",
                "active_runs": len(live),
                "last_ts": live[0].get("ts") if live else None,
                "source": live[0].get("source") if live else None,
                "avatar": None,
            })

    agents.sort(key=lambda e: (e["status"] != "working", e["name"]))
    return {"agents": agents, "count": len(agents)}


def _load_app_details(
    slugs: list[str], organization_id: str | None,
) -> dict[str, dict[str, Any]]:
    """``{slug: {name, icon}}`` for the given Custom App slugs.

    Best-effort → ``{}`` on any error, same contract as ``_load_avatars`` —
    a lookup failure must degrade to showing the bare slug, never break the
    roster for every other agent/app.

    H-201: the read binds the viewer's tenant. With no tenant it reads
    nothing, so another org's app name never shows.
    """
    if not slugs or not organization_id:
        return {}
    try:
        from acb_graph import tenant_session  # noqa: PLC0415
        from sqlalchemy import text  # noqa: PLC0415

        with tenant_session(organization_id) as s:
            rows = s.execute(
                text("SELECT slug, name, icon FROM apps WHERE slug = ANY(:slugs)"),
                {"slugs": slugs},
            ).fetchall()
        return {r.slug: {"name": r.name, "icon": r.icon or ""} for r in rows}
    except Exception as exc:  # noqa: BLE001
        _log.warning("observability.app_details_load_failed", error=str(exc))
        return {}


@router.get("/cost")
async def cost(
    days: int = 7,
    viewer: UserContext = _VIEWER,
) -> dict[str, Any]:
    """Daily LLM cost rollup (USD) of the viewer's tenant for the last *days* days.

    Per-day totals + by-model + by-source breakdowns, from the always-on Redis
    rollup (best-effort litellm pricing). ``days`` is clamped to [1, 90].
    H-201e: the org comes from the session, never from the request.
    """
    return await cost_summary(days=days, organization_id=viewer.organization_id)


_RUN_STATUSES = frozenset({"running", "completed", "error", "cancelled"})


@router.get("/runs")
async def runs(
    agent: str | None = None,
    status: str | None = None,
    since_hours: int | None = None,
    limit: int = 50,
    viewer: UserContext = _VIEWER,
) -> dict[str, Any]:
    """DURABLE activity history from the ``agent_run`` trace store.

    Unlike the ephemeral Redis feed (recent/stream, ~2000 events lost on a Redis
    flush), this reads the persisted per-run rows — so it survives restarts and
    goes back as far as retention keeps them. Lean rows only: metadata + error
    message, NEVER the full trace blob (that stays EXECUTIVE-gated at
    ``/debug/runs/{id}``). Powers the History tab + the per-agent drawer.
    Newest-first; ``limit`` clamped to [1, 200].

    H-201: the read binds the viewer's tenant. With no tenant the answer is
    empty, and no unbound session opens.
    """
    org = viewer.organization_id
    if not org:
        return {"runs": [], "count": 0}
    limit = max(1, min(limit, 200))
    where: list[str] = []
    params: dict[str, Any] = {"limit": limit}
    if agent:
        where.append("agent_name = :agent")
        params["agent"] = agent
    if status in _RUN_STATUSES:
        where.append("status = :status")
        params["status"] = status
    if since_hours:
        where.append("started_at > now() - make_interval(hours => :sh)")
        params["sh"] = int(since_hours)
    clause = (" WHERE " + " AND ".join(where)) if where else ""

    try:
        from acb_graph import tenant_session  # noqa: PLC0415
        from sqlalchemy import text  # noqa: PLC0415

        with tenant_session(org) as s:
            rows = s.execute(
                text(
                    "SELECT run_id, thread_id, agent_name, user_id, model, "
                    "status, started_at, ended_at, duration_ms, total_tokens, "
                    "tool_count, error_type, error_message FROM agent_run"
                    + clause + " ORDER BY started_at DESC LIMIT :limit"
                ),
                params,
            ).fetchall()
    except Exception as exc:  # noqa: BLE001 — degrade to empty, never 500 the UI
        _log.warning("observability.runs_failed", error=str(exc))
        return {"runs": [], "count": 0}

    out = [{
        "run_id": r.run_id,
        "thread_id": r.thread_id,
        "agent": r.agent_name,
        "user": r.user_id,
        "model": r.model,
        "status": r.status,
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "ended_at": r.ended_at.isoformat() if r.ended_at else None,
        "duration_ms": r.duration_ms,
        "total_tokens": r.total_tokens,
        "tool_count": r.tool_count,
        "error_type": r.error_type,
        "error_message": r.error_message,
    } for r in rows]
    return {"runs": out, "count": len(out)}


async def _sse_activity(organization_id: str | None) -> AsyncIterator[bytes]:
    """Yield SSE frames for each new activation of one tenant; heartbeat on idle.

    Starts from ``$`` (new events only) — the client backfills history via
    ``/activity/recent`` first, then opens this for the live tail. A heartbeat
    comment on each idle cycle keeps proxies from closing the connection.

    *organization_id* is an argument, taken from the session by the route.
    The body of this generator runs after the middleware released the tenant,
    so it must never read the ambient value. With no org, the stream sends
    the opening comment and ends: no event, and no busy loop.
    """
    cursor = "$"
    # Prime the client so EventSource fires ``onopen`` promptly.
    yield b": connected\n\n"
    if not organization_id:
        return
    while True:
        try:
            events, cursor = await read_activity_since(
                cursor, organization_id=organization_id,
                block_ms=15_000, count=200,
            )
        except asyncio.CancelledError:  # client disconnected
            raise
        except Exception as exc:  # noqa: BLE001 — degrade, keep the stream open
            _log.warning("observability.stream_read_failed", error=str(exc))
            await asyncio.sleep(1.0)
            yield b": error\n\n"
            continue
        if not events:
            yield b": ping\n\n"  # heartbeat
            continue
        for evt in events:
            frame = f"data: {json.dumps(evt, default=str)}\n\n"
            yield frame.encode("utf-8")


@router.get("/activity/stream")
async def activity_stream(
    viewer: UserContext = _VIEWER,
) -> StreamingResponse:
    """Server-Sent Events stream of the tenant's live activations (agent + model)."""
    return StreamingResponse(
        _sse_activity(viewer.organization_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # disable nginx proxy buffering
        },
    )


# ── Avatar customization (Office / Avatar Studio) ────────────────────────────
# The office renders each agent as a pixel-art character (deriveAvatar + the
# generated role sprites). These endpoints are the OVERRIDE layer: pin a custom
# look or a Pixel Lab-generated sprite per agent, persisted in ``agent_avatars``
# (migration 64) and merged into ``/roster`` so every viewer sees the same cast.

_AGENT_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
# Cap a stored sprite data-URI so a pathological payload can't bloat the row /
# the roster response. A 200x200 transparent PNG is ~25-30KB base64.
_MAX_SPRITE_CHARS = 600_000


def _load_avatars(organization_id: str | None) -> dict[str, dict[str, Any]]:
    """All avatar overrides of the tenant, keyed by agent name.

    Best-effort → {} on any error. H-201: the read binds the tenant, and with
    no tenant it reads nothing.
    """
    if not organization_id:
        return {}
    try:
        from acb_graph import tenant_session  # noqa: PLC0415
        from sqlalchemy import text  # noqa: PLC0415

        with tenant_session(organization_id) as s:
            rows = s.execute(
                text("SELECT agent_name, config, sprite FROM agent_avatars")
            ).fetchall()
        out: dict[str, dict[str, Any]] = {}
        for r in rows:
            cfg = r.config if isinstance(r.config, dict) else {}
            out[r.agent_name] = {"config": cfg, "sprite": r.sprite}
        return out
    except Exception as exc:  # noqa: BLE001 — degrade to no overrides, never 500
        _log.warning("observability.avatars_load_failed", error=str(exc))
        return {}


def _save_avatar(
    name: str, config: dict[str, Any], sprite: str | None,
    organization_id: str | None,
) -> None:
    """Upsert one agent's avatar override (raises on DB failure).

    H-201: the write binds the tenant, and the column default stamps the row
    with it. ``tenant_session`` commits on exit and refuses no tenant.

    H-201d (task 1719): the conflict target is ``(organization_id,
    agent_name)``, the unique index of migration 354. A save never matches
    a row of another org.

    ⚠️ Expand phase. The old primary key on ``agent_name`` alone still
    exists, so a save of a name that another org holds fails with a unique
    violation on ``agent_avatars_pkey``. The contract release drops that
    key (HANDOFF H-443), and then the save succeeds.
    """
    from acb_graph import tenant_session  # noqa: PLC0415
    from sqlalchemy import text  # noqa: PLC0415

    with tenant_session(organization_id) as s:
        s.execute(
            text(
                "INSERT INTO agent_avatars (agent_name, config, sprite, updated_at) "
                "VALUES (:n, CAST(:c AS jsonb), :s, now()) "
                "ON CONFLICT (organization_id, agent_name) DO UPDATE SET "
                "config = CAST(:c AS jsonb), sprite = :s, updated_at = now()"
            ),
            {"n": name, "c": json.dumps(config or {}), "s": sprite},
        )


def _delete_avatar(name: str, organization_id: str | None) -> None:
    """Remove one avatar override of the tenant. H-201: the write binds it."""
    from acb_graph import tenant_session  # noqa: PLC0415
    from sqlalchemy import text  # noqa: PLC0415

    with tenant_session(organization_id) as s:
        s.execute(
            text("DELETE FROM agent_avatars WHERE agent_name = :n"), {"n": name}
        )


class AvatarPut(BaseModel):
    config: dict[str, Any] = {}
    sprite: str | None = None


class AvatarGenerate(BaseModel):
    description: str
    size: int = 200


@router.get("/avatars")
async def avatars(viewer: UserContext = _VIEWER) -> dict[str, Any]:
    """Every stored avatar override (config + custom sprite), keyed by agent."""
    return {"avatars": _load_avatars(viewer.organization_id)}


def _writer_org(writer: UserContext) -> str:
    """The tenant of an avatar write. 403 with no tenant, so nothing is written."""
    org = writer.organization_id
    if not org:
        raise HTTPException(
            status_code=403, detail="No organization is bound to this session.",
        )
    return str(org)


@router.put("/avatars/{name}")
async def put_avatar(
    name: str, body: AvatarPut, writer: UserContext = _WRITER,
) -> dict[str, Any]:
    """Pin an agent's avatar override (partial config + optional custom sprite)."""
    name = name.strip().lower()
    if not _AGENT_NAME_RE.match(name):
        raise HTTPException(status_code=400, detail="invalid agent name")
    if body.sprite and len(body.sprite) > _MAX_SPRITE_CHARS:
        raise HTTPException(status_code=413, detail="sprite too large")
    if body.sprite and not body.sprite.startswith("data:image/"):
        raise HTTPException(status_code=400, detail="sprite must be an image data-URI")
    org = _writer_org(writer)
    try:
        _save_avatar(name, body.config or {}, body.sprite, org)
    except Exception as exc:  # noqa: BLE001
        _log.warning("observability.avatar_save_failed", agent=name, error=str(exc))
        raise HTTPException(status_code=500, detail="failed to save avatar") from exc
    return {"ok": True, "agent": name, "config": body.config, "sprite": body.sprite}


@router.delete("/avatars/{name}")
async def delete_avatar(
    name: str, writer: UserContext = _WRITER,
) -> dict[str, Any]:
    """Remove an agent's override → it reverts to the derived/role look."""
    name = name.strip().lower()
    if not _AGENT_NAME_RE.match(name):
        raise HTTPException(status_code=400, detail="invalid agent name")
    org = _writer_org(writer)
    try:
        _delete_avatar(name, org)
    except Exception as exc:  # noqa: BLE001
        _log.warning("observability.avatar_delete_failed", agent=name, error=str(exc))
        raise HTTPException(status_code=500, detail="failed to delete avatar") from exc
    return {"ok": True, "agent": name}


@router.post("/avatars/generate")
async def generate_avatar(
    body: AvatarGenerate, _writer: UserContext = _WRITER,
) -> dict[str, Any]:
    """Generate a pixel-art sprite via Pixel Lab and return it as a data-URI.

    The API key stays server-side (``PIXELLAB_API_KEY``); the browser never sees
    it. The caller can then PUT the returned sprite onto an agent. Transparent
    background, waist-up bust framing to match the office cast.
    """
    key = os.environ.get("PIXELLAB_API_KEY", "").strip()
    if not key:
        raise HTTPException(
            status_code=503,
            detail="Pixel Lab is not configured (set PIXELLAB_API_KEY on the gateway).",
        )
    desc = (body.description or "").strip()
    if not desc:
        raise HTTPException(status_code=400, detail="description is required")
    size = max(64, min(int(body.size or 200), 400))
    style = (
        "cute chibi pixel art office worker, seated at a desk, waist up, facing "
        "forward, symmetrical, clean dark outline, soft shading, centered"
    )

    import httpx  # noqa: PLC0415

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                "https://api.pixellab.ai/v1/generate-image-pixflux",
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
                json={
                    "description": f"{desc}, {style}",
                    "image_size": {"width": size, "height": size},
                    "no_background": True,
                    "text_guidance_scale": 7.5,
                },
            )
    except Exception as exc:  # noqa: BLE001 — network/timeout
        _log.warning("observability.avatar_generate_failed", error=str(exc))
        raise HTTPException(status_code=502, detail="Pixel Lab request failed") from exc

    if resp.status_code == 401:
        raise HTTPException(status_code=502, detail="Pixel Lab rejected the API key")
    if resp.status_code >= 400:
        raise HTTPException(
            status_code=502, detail=f"Pixel Lab error {resp.status_code}"
        )
    data = resp.json()
    b64 = (data.get("image") or {}).get("base64") or ""
    if not b64:
        raise HTTPException(status_code=502, detail="Pixel Lab returned no image")
    sprite = b64 if b64.startswith("data:") else f"data:image/png;base64,{b64}"
    return {"sprite": sprite, "usage": data.get("usage")}
