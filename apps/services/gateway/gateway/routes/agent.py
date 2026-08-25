"""Agent event routing endpoints (Metorite v2 — Core FastAPI router).

Endpoints
---------
POST /agent/run
    Synchronously run a named agent and wait for the result.

POST /agent/run/async
    Fire-and-forget: enqueue the run as a background task, return run_id immediately.

GET  /agent/run/{run_id}/status
    Query the Postgres checkpoint for a run's current state.

POST /agent/webhook/{source}
    Receive an external webhook (ClickUp, Zoho, Gmail, WhatsApp) and route
    it to the correct specialist agent based on the built-in routing table.
    In Phase 2 this table will be driven by each agent's ``config.json``.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # import-cycle-free: the runtime import is function-local
    from orchestrator.steer import TurnDecision

from acb_auth import (
    UserContext,
    assert_can_run_agent_in_session,
    get_current_user,
    require_internal_auth,
    require_permission,
)
from acb_common import get_logger, get_settings
from fastapi import (APIRouter, BackgroundTasks, Depends, Header,
                     HTTPException, Request, status)
from fastapi.responses import JSONResponse, Response, StreamingResponse
from gateway.db import current_tenant
from gateway.room_stream import publish_room_event
from pydantic import BaseModel

_log = get_logger("gateway.agent")

router = APIRouter(prefix="/agent", tags=["agents"])


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class AgentRunRequest(BaseModel):
    agent: str
    """Bare agent name, e.g. ``"task-manager"``.  Core prepends ``agent-`` when cloning."""
    payload: dict[str, Any] = {}
    thread_id: str | None = None
    run_id: str | None = None
    model: str | None = None
    """Optional model override.  If it is a LiteLLM model (contains '/' or starts
    with 'tier'), the executor injects a BYOK provider block so the Copilot SDK
    routes completions through the gateway /v1 (litellm SDK) instead of github.com."""
    assistant_message_id: str | None = None
    """Frontend-minted id of this turn's assistant message row.  The gateway's
    fold-and-persist at run end (core_loop_unification Phase 1, P0-3) upserts
    the SAME row the live translator checkpoints, keeping the two writers
    idempotent.  Falls back to ``assistant-{thread}-{run_id}`` when absent."""
    think_mode: str = "auto"
    """Reasoning depth the chat UI selected: ``auto`` | ``thinking`` | ``max``.

    Previously honoured only on ``/copilot/chat``, so the thinking toggle did
    nothing for named agents.  Porting it here is the prerequisite for retiring
    that endpoint (agent_architecture.md §11.1.1); ``auto`` is a no-op, so the
    default path is unchanged."""


#: Reasoning depths the chat UI can request.  Anything else falls back to auto.
_THINK_MODES: frozenset[str] = frozenset({"auto", "thinking", "max"})


def _resolve_think_mode(req: AgentRunRequest) -> str:
    """Resolve the requested reasoning depth from either place it can arrive.

    ``route.ts``'s named-agent branch has been sending ``think_mode`` inside
    ``payload`` all along, where nothing read it — which is why the chat UI's
    thinking toggle silently did nothing for named agents while working on
    ``/copilot/chat`` (agent_architecture.md §11.1.1).  Reading both means the
    existing frontend is fixed with no frontend change, and the top-level field
    is there for future callers.

    Unknown values resolve to ``"auto"`` rather than being passed through: the
    downstream mapping ignores them anyway, and normalising here keeps the run
    log honest about what was actually applied.
    """
    top = (req.think_mode or "").strip().lower()
    if top in _THINK_MODES and top != "auto":
        return top
    nested = str(req.payload.get("think_mode") or "").strip().lower()
    if nested in _THINK_MODES and nested != "auto":
        return nested
    return "auto"


async def _refuse_if_another_run_is_active(thread_id: str, actor: str) -> None:
    """409 when starting a run would destroy somebody ELSE's in-flight run.

    Starting a run on an already-active thread cancels the previous one *and*
    deletes its event log (``run_detached`` → ``mark_active(reset=True)``). That
    is correct when you supersede your OWN run — steer, retry, Quick action —
    and destructive the moment two people share a thread: the first person's
    work vanishes with no error, because the cancel path deliberately suppresses
    ``RUN_ERROR`` (it treats cancellation as a supersede, not a failure).
    See ``docs/multiplayer/README.md`` §3.3.

    So the guard keys on WHO owns the live run, not merely on one existing.
    Every same-user path is untouched; only a different person is refused.

    Fails OPEN — unknown owner (a run predating actor tracking, a Redis hiccup)
    or an anonymous caller (cron, service-to-service) proceeds. That matches
    ``_thread_owner_ok``'s contract and the asymmetry behind it: a false refusal
    blocks legitimate work, while a false allow merely restores today's
    behaviour.

    Superseded as the DEFAULT path by steer (§QM-1): a second person's message
    now folds into the live run instead of being refused, so this raises only
    for the cases steer cannot take — see :func:`_route_incoming_turn`. The
    refusal is kept because the destructive path must stay closed even when
    steering is unavailable (Redis down, an unreachable owner, a caller that
    never routes).
    """
    if not actor:
        return
    try:
        from orchestrator.stream_relay import get_run_actor, is_active

        if not await is_active(thread_id):
            return
        owner = await get_run_actor(thread_id)
    except Exception:  # never block a run because the check itself failed
        _log.warning("agent.active_run_check_failed", thread_id=thread_id[:12])
        return

    if not owner or owner == actor:
        return

    _log.info(
        "agent.run_refused_active_run",
        thread_id=thread_id[:12], owner=owner[:20], actor=actor[:20],
    )
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "error": "run_in_progress",
            "message": (
                f"{owner} has a run in progress on this conversation. "
                "Starting another would cancel it and discard its transcript."
            ),
            "threadId": thread_id,
            "holder": owner,
        },
    )


async def _route_incoming_turn(
    thread_id: str, actor: str, text: str,
) -> "TurnDecision":
    """Decide what an arriving message does to *thread_id*: the impure half.

    Every judgement lives in :func:`orchestrator.steer.route_turn`, which is
    pure and therefore testable without a Redis. This function only gathers the
    three facts that function needs and hands them over, so "what is the rule"
    and "what is the world" never get tangled.

    Fails to ``ENGAGE`` when the world cannot be read at all: an unreadable
    Redis must not silently convert someone's message into a steer that nothing
    will ever apply. ``run_detached``'s own guard still stands behind that, so
    engaging on a bad read cannot destroy a transcript — it raises instead.
    """
    from orchestrator.steer import Route, TurnDecision, route_turn

    if not thread_id:
        return TurnDecision(Route.ENGAGE, "no_thread")
    try:
        from orchestrator.stream_relay import get_run_source, is_active

        active = await is_active(thread_id)
        source = await get_run_source(thread_id) if active else ""
    except Exception:  # noqa: BLE001
        _log.warning("agent.turn_route_probe_failed", thread_id=thread_id[:12])
        return TurnDecision(Route.ENGAGE, "probe_failed")

    return route_turn(
        author_kind="human" if actor else "agent",
        text=text,
        run_active=active,
        target_run_kind=_run_kind_for_source(source),
    )


#: Sources whose runs nobody is standing in. A person's message arriving during
#: one of these starts its own run rather than steering into it (§QM-1's one
#: carve-out) — the executor already stamps every run with one of these strings
#: as its correlation ``source``, so this needs no new concept, only a reading
#: of an existing one. ``workflows/service.py`` calls the same set unattended.
_AUTOMATION_SOURCES: frozenset[str] = frozenset({
    "schedule", "webhook", "event", "workflow", "cron", "reconciler",
})


def _run_kind_for_source(source: str) -> str:
    """``"automation"`` for an unattended run, ``"human"`` for everything else.

    Unknown and empty both resolve to ``"human"``: mistaking a conversation for
    a cron would spawn a second concurrent run on a thread someone is actively
    using, which is the exact failure steer exists to remove.
    """
    return (
        "automation"
        if (source or "").strip().lower() in _AUTOMATION_SOURCES
        else "human"
    )


async def _apply_turn_decision(
    decision: "TurnDecision",
    req: AgentRunRequest,
    agent_name: str,
    actor: str,
    room: Any,
) -> Response | None:
    """Carry out a routing decision. ``None`` means "start the run".

    The three non-engaging outcomes all answer **202 Accepted with a JSON body
    and no stream**, which is the mechanism behind "the second caller stands
    down". A caller that gets a stream believes it owns the answer and renders
    it; qm's Slack handler carries the comment that delivering from both sides
    "is how one answer got posted twice". A 202 is unambiguous: your words
    landed, and somebody else's turn is going to speak them.

    ``ENGAGE`` additionally drains the durable steer store into this run's
    message. That is the replay half of §QM-1's durability requirement: a steer
    whose target run terminated mid-send is still on disk, and this is where it
    is spoken instead of lost.
    """
    from orchestrator.steer import (
        Route, format_replayed, is_inside_run_floor, send_steer,
        take_pending_steers,
    )

    thread_id = req.thread_id or ""

    if decision.route is Route.ENGAGE:
        if thread_id:
            pending = await take_pending_steers(thread_id)
            replayed = format_replayed(pending)
            if replayed:
                _log.info(
                    "agent.steer_replayed",
                    thread_id=thread_id[:12], count=len(pending),
                )
                # Prepended, not appended: what somebody said while the last
                # run was dying came BEFORE this message in wall-clock order,
                # and a transcript that reorders people is a transcript that
                # misattributes intent.
                existing = str(req.payload.get("message") or "")
                req.payload["message"] = (
                    f"{replayed}\n\n{existing}" if existing else replayed
                )
        return None

    if decision.route is Route.DROP:
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={
                "steered": False, "dropped": True,
                "reason": decision.reason, "threadId": thread_id,
            },
        )

    if decision.route is Route.ABORT:
        # A bare "stop" is the one message that should reach the run as a verb
        # rather than as words. Cancel is already attributed and already
        # cross-worker; this only routes to it.
        from orchestrator.stream_relay import cancel_run

        stopped = await cancel_run(thread_id)
        _log.info(
            "agent.turn_aborted_run",
            thread_id=thread_id[:12], actor=actor[:20], confirmed=stopped,
        )
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={
                "steered": False, "aborted": True, "confirmed": stopped,
                "reason": decision.reason, "threadId": thread_id,
            },
        )

    # ── STEER ────────────────────────────────────────────────────────────────
    # Authority first (groups_sessions_authority.md §3). The run is executing
    # under an access intersection folded at run start; admitting a principal
    # who was not in that fold would move the floor under a turn already using
    # it. We refuse rather than narrow mid-run, and we say why — a silent
    # downgrade is the failure mode §3 names ("the agent could do this
    # yesterday").
    from orchestrator.stream_relay import get_run_floor

    floor = await get_run_floor(thread_id)
    if not is_inside_run_floor(
        actor, floor,
        room_admits=bool(room is not None and getattr(room, "can_send", False)),
    ):
        _log.info(
            "agent.steer_refused_outside_floor",
            thread_id=thread_id[:12], actor=actor[:20],
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "steer_outside_run_floor",
                "message": (
                    "This run started before you joined the room, so it is "
                    "acting with an access level that was agreed without you. "
                    "Adding your words to it now would change what it is "
                    "allowed to do mid-task. Send again once the current turn "
                    "finishes and the next run will include you."
                ),
                "threadId": thread_id,
            },
        )

    text = str(req.payload.get("message") or req.payload.get("user_query") or "")
    signal = await send_steer(thread_id, actor, text, run_id=req.run_id)

    # The room sees WHO redirected the run and when. Deliberately on
    # `cc:room:` and not on the run stream: run events are folded into the
    # transcript by both gateway/chat_fold.py and lib/chatStream.ts, so a new
    # run-event type is a two-sided change; a steer is a room fact (§4.4).
    if room is not None and getattr(room, "is_shared", False):
        await publish_room_event(thread_id, {
            "type": "STEER_INJECTED",
            "author": actor,
            "agentName": agent_name,
            "text": text[:500],
            "appliedAt": "tool_boundary",
            "delivered": bool(signal.get("delivered")),
        })

    _log.info(
        "agent.turn_steered",
        thread_id=thread_id[:12], actor=actor[:20],
        delivered=bool(signal.get("delivered")),
    )
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={
            # THE marker the second surface keys off. Anything that receives it
            # must not render an assistant turn of its own.
            "steered": True,
            "delivered": bool(signal.get("delivered")),
            "pendingReplay": not bool(signal.get("delivered")),
            "reason": decision.reason,
            "threadId": thread_id,
            "signalId": signal.get("id"),
        },
    )


class AgentRunResponse(BaseModel):
    run_id: str
    agent: str
    status: str  # "completed" | "failed" | "queued"
    result: Any | None = None
    mutation_pr: str | None = None
    error: str | None = None


class UserInputResponseRequest(BaseModel):
    """Answer to a native ask_user (on_user_input_request) prompt."""

    request_id: str
    answer: str
    was_freeform: bool = True
    # Thread the parked run belongs to — used to relay the answer to whichever
    # worker owns the run when it's not parked on THIS worker (P1-2).
    thread_id: str | None = None


class WebhookEvent(BaseModel):
    source: str
    event_type: str
    payload: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# Agent name allowlist (security: never clone arbitrary user-supplied names)
# ---------------------------------------------------------------------------

_KNOWN_AGENTS: frozenset[str] = frozenset(
    [
        "task-manager",
        "apis-config",
        "email-assistant",
        "whatsapp-assistant",
        "app-builder",
        "crm-assistant",
    ]
)

# Human-readable metadata for the Control Plane agent picker.
# Keys match the bare agent names in _KNOWN_AGENTS.
_AGENT_REGISTRY: list[dict] = [
    {
        "name": "task-manager",
        "description": "GTD task manager — capture, clarify, organize, and status/workload Q&A with citations.",
        # ⚠️ ClickUp removed 2026-08-25 (D52, WS-39 S1 repair round 1). The
        # catalog JSON was cleaned in the S1 sweep and this runtime copy was
        # not, so the picker still rendered the tile with a ClickUp integration
        # — and `/integrations/status` reads `integrations` to decide whether an
        # agent is "configured", i.e. the tile advertised a connection to a
        # product that no longer exists. The three `webhook_routes` went with
        # it: their receiver (`/webhooks/clickup`) was deleted in S1.
        "tags": ["tasks", "project-management"],
        "status": "live",
        # Runs through MAF (MetoriteCopilotAgent wrapper) with BYOK model support.
        "agent_runtime": "github-copilot",
        "local_path": "apps/agents/agent-task-manager",
        "integrations": [],
        "optional_integrations": [],
        "webhook_routes": [],
    },
    {
        "name": "apis-config",
        "description": (
            "API Configuration Assistant — discovers any API by name, "
            "finds its documentation via web search, and guides you "
            "through credential setup step by step."
        ),
        "tags": ["configuration", "apis", "setup", "admin"],
        "status": "live",
        "agent_runtime": "github-copilot",
        "local_path": "apps/agents/agent-apis-config",
        "integrations": [],
        "optional_integrations": ["serpapi"],
    },
    {
        "name": "email-assistant",
        "description": (
            "Email Assistant — checks the inbox, categorizes mail, and drafts "
            "context-aware replies, handing off to the sales and task-manager "
            "agents and reading memory when an email needs their context."
        ),
        "tags": ["email", "gmail", "outlook", "drafting", "apps"],
        "status": "live",
        # email-assistant is a MAF agent (see apps/agents/agent-email-assistant:
        # agents.py build_agents() + config.json "runtime": "maf"). It must NOT
        # be labelled github-copilot — that routes it through the Copilot SDK
        # session, which fails with a GitHub 402 quota error instead of using
        # the BYOK LiteLLM tiers (tier-balanced → deepseek). Keep this "maf".
        "agent_runtime": "maf",
        "local_path": "apps/agents/agent-email-assistant",
        "integrations": [],
        "optional_integrations": [],
    },
    {
        "name": "app-builder",
        "description": (
            "App Workshop builder — turns chat into small internal web apps "
            "(Custom Apps). Each Workshop session is bound to its app's "
            "workspace; the agent edits index.html and the app.json manifest "
            "and keeps the preview renderable after every round."
        ),
        "tags": ["apps", "builder", "workshop"],
        "status": "live",
        # Copilot-SDK engine: the builder needs native file/shell tools to
        # edit the app workspace. BYOK-routed through the gateway tiers like
        # the sales-assistant (no GitHub quota dependency).
        "agent_runtime": "github-copilot",
        "local_path": "apps/agents/agent-app-builder",
        "integrations": [],
        "optional_integrations": [],
    },
    {
        "name": "whatsapp-assistant",
        "description": (
            "WhatsApp Assistant — briefs and triages a WhatsApp Business inbox, "
            "summarizes groups, transcribes voice notes, and drafts replies and "
            "follow-up nudges in the founder's voice. Drafts only; never sends."
        ),
        "tags": ["whatsapp", "messaging", "triage", "drafting", "apps"],
        "status": "live",
        # whatsapp-assistant is a MAF agent (apps/agents/agent-whatsapp-assistant:
        # agents.py build_agents() + config.json "runtime": "maf"). Like the
        # email-assistant it must stay "maf" — labelling it github-copilot routes
        # it through the Copilot SDK and fails with a 402 instead of using the
        # BYOK LiteLLM tiers.
        "agent_runtime": "maf",
        "local_path": "apps/agents/agent-whatsapp-assistant",
        "integrations": [],
        "optional_integrations": [],
    },
    {
        "name": "crm-assistant",
        "description": (
            "CRM Assistant — works the native CRM: finds leads, deals, "
            "contacts and organizations, reads the deal pipeline by stage with "
            "its counts and ₹ totals, opens a record in full, and reads its "
            "history of notes, calls, meetings and stage changes. It can also "
            "create a lead, move a deal to another stage, log a note, call, "
            "meeting or task, and convert a lead into a deal — each write asks "
            "the acting user to approve it first and does nothing if nobody is "
            "there to ask. It cannot delete a CRM record."
        ),
        "tags": ["crm", "sales", "pipeline", "leads", "deals", "apps"],
        "status": "live",
        # crm-assistant is a MAF agent (apps/agents/agent-crm: agents.py
        # build_agents() + config.json "runtime": "maf"). Like its two
        # siblings it must stay "maf" — labelling it github-copilot routes it
        # through the Copilot SDK and fails with a 402 instead of using the
        # BYOK LiteLLM tiers.
        "agent_runtime": "maf",
        # The directory is agent-crm; the agent is crm-assistant. This entry is
        # what maps one to the other — there is no derivation from the name.
        "local_path": "apps/agents/agent-crm",
        # Reads the NATIVE CRM through the gateway, never Zoho: the Zoho
        # credential belongs to the sync engine (D-CRM-7/D-CRM-8), so this
        # agent needs no integration of its own.
        "integrations": [],
        "optional_integrations": [],
    },
    {
        "name": "orchestrator",
        "description": (
            "Orchestrator — the default chat agent. Routes to specialist agents, "
            "retrieves company data across ClickUp/Zoho/Odoo, and carries "
            "cross-session memory."
        ),
        "tags": ["orchestrator", "core", "routing"],
        "status": "live",
        # Native MAF: apps/agents/agent-orchestrator/agents.py returns
        # build_orchestrator_agent(), a real agent_framework.Agent. Labelling it
        # github-copilot would route it through the Copilot SDK and 402.
        "agent_runtime": "maf",
        "local_path": "apps/agents/agent-orchestrator",
        "integrations": [],
        "optional_integrations": [],
        # Why this entry exists (agent_architecture.md §11.1.1): the wrapper at
        # apps/agents/agent-orchestrator/ was written to "eliminate the separate
        # /copilot/chat endpoint path in main.py and the isOrchestrator branching
        # in route.ts" — but it was never registered, so _validate_agent_name
        # 422'd "orchestrator" and the wrapper was unreachable. Registering it
        # makes the named-agent path real; retiring /copilot/chat is a separate,
        # frontend-side step that depends on this one.
    },
]


# ---------------------------------------------------------------------------
# Dynamic (user-registered) agent persistence — Postgres-backed.
# Survives git reset --hard, deploys, and reboots.
# Falls back to agents.json on first read for backward-compatible migration.
# ---------------------------------------------------------------------------

def _normalize_runtime(val: object) -> str | None:
    """Normalize a declared runtime string to 'maf' or 'github-copilot'.

    Returns None for anything unrecognised/empty so callers can fall back.
    """
    if not isinstance(val, str):
        return None
    v = val.strip().lower()
    if v == "maf":
        return "maf"
    if v in ("github-copilot", "github_copilot", "githubcopilot",
             "copilot", "copilot-sdk"):
        return "github-copilot"
    return None


def _declared_runtime(agent_name: str, local_path: str | None = None) -> str | None:
    """Return the runtime an agent DECLARES in its config.json (normalized), or
    None if it declares nothing.

    An agent's own ``config.json`` ``runtime`` field is authoritative over the
    registration-time heuristic (which only knew "came from GitHub" vs "local
    path" and so mislabelled MAF agents — e.g. email-assistant, which declares
    ``"runtime": "maf"`` — as Copilot SDK).  Checked in the clone first (always
    reflects the current repo), then the ``local_path`` source.
    """
    candidates: list[Path] = []
    try:
        from gateway.routes.workspace import \
            _agent_workspace_dir  # noqa: PLC0415
        ws = _agent_workspace_dir(agent_name)
        if ws is not None:
            candidates.append(ws / "config.json")
    except Exception:  # noqa: BLE001
        pass
    if local_path:
        candidates.append(Path(local_path) / "config.json")
    for cfg_path in candidates:
        try:
            if cfg_path.is_file():
                data = json.loads(
                    cfg_path.read_text(encoding="utf-8", errors="replace")
                )
                rt = _normalize_runtime(data.get("runtime"))
                if rt:
                    return rt
        except Exception:  # noqa: BLE001
            continue
    return None


def _load_dynamic_agents() -> list[dict]:
    """Return user-registered agents from the dynamic_agents Postgres table.

    On every call, also imports any agents found in agents.json that are
    missing from the database — this ensures agents registered while Postgres
    was temporarily unavailable eventually sync into the DB.

    On first call after migration, imports any existing agents.json data
    into the DB so nothing is lost.
    """
    try:
        from acb_graph import get_session  # noqa: PLC0415
        from sqlalchemy import text  # noqa: PLC0415

        # ── Always sync agents.json → DB for missing agents ─────────────
        _sync_file_into_db()

        with get_session() as s:
            rows = s.execute(
                text(
                    "SELECT name, description, tags, status, agent_runtime, "
                    "repo_url, repo_name, local_path, integrations, "
                    "optional_integrations FROM dynamic_agents ORDER BY name"
                )
            ).fetchall()
        if rows:
            out = [
                {
                    "name": (r[0] or "").strip(),
                    "description": (r[1] or "").strip(),
                    "tags": r[2] if isinstance(r[2], list) else [],
                    "status": (r[3] or "live").strip(),
                    "agent_runtime": (r[4] or "maf").strip(),
                    "repo_url": (r[5] or "").strip() or None,
                    "repo_name": (r[6] or "").strip() or None,
                    "local_path": (r[7] or "").strip() or None,
                    "integrations": r[8] if isinstance(r[8], list) else [],
                    "optional_integrations": (
                        r[9] if isinstance(r[9], list) else []
                    ),
                    "dynamic": True,
                }
                for r in rows
            ]
            # Honor each agent's declared config.json runtime over the stored
            # value, so the executor and the UI route/label by what the agent
            # actually is rather than the registration heuristic.
            for a in out:
                rt = _declared_runtime(a["name"], a.get("local_path"))
                if rt:
                    a["agent_runtime"] = rt
            return out
        # DB empty — import everything from file
        return _migrate_from_file()
    except Exception:  # noqa: BLE001
        return _migrate_from_file()


def _sync_file_into_db() -> None:
    """Read agents.json and upsert any entries not yet in the DB.

    Does NOT delete DB entries that are absent from the file — the DB is
    the authority.  This only fills in missing agents so a file-only write
    (e.g. Postgres temporarily unavailable during registration) eventually
    makes it into the database.
    """
    try:
        path = _get_agents_file()
        if not path.exists():
            return
        file_agents: list[dict] = json.loads(path.read_text(encoding="utf-8"))
        if not file_agents:
            return

        import json as _json  # noqa: PLC0415

        from acb_graph import get_session  # noqa: PLC0415
        from sqlalchemy import text  # noqa: PLC0415

        with get_session() as s:
            # Get names already in DB
            existing = {
                r[0] for r in s.execute(
                    text("SELECT name FROM dynamic_agents")
                ).fetchall()
            }
            # Upsert any file agents missing from DB
            for a in file_agents:
                if a.get("name") in existing:
                    continue
                # Validate name before inserting — only accept well-formed slugs
                aname = a.get("name", "")
                if not aname or not re.match(r"^[a-z0-9][a-z0-9-]{0,48}[a-z0-9]$", aname):
                    _log.warning("agent.sync_skipped_invalid_name", name=aname)
                    continue
                s.execute(
                    text(
                        "INSERT INTO dynamic_agents "
                        "(name, description, tags, status, agent_runtime, "
                        "repo_url, repo_name, local_path, integrations, "
                        "optional_integrations, updated_at) "
                        "VALUES (:n,:d,CAST(:t AS jsonb),:s,:r,:ru,:rn,:lp,"
                        "CAST(:i AS jsonb),CAST(:oi AS jsonb),now()) "
                        "ON CONFLICT (name) DO NOTHING"
                    ),
                    {
                        "n": a["name"],
                        "d": a.get("description", ""),
                        "t": _json.dumps(a.get("tags", [])),
                        "s": a.get("status", "live"),
                        "r": a.get("agent_runtime", "maf"),
                        "ru": a.get("repo_url"),
                        "rn": a.get("repo_name"),
                        "lp": a.get("local_path"),
                        "i": _json.dumps(a.get("integrations", [])),
                        "oi": _json.dumps(
                            a.get("optional_integrations", [])
                        ),
                    },
                )
            s.commit()
    except Exception:
        pass  # Best-effort; DB is the authority


def _strip_agent_strings(a: dict) -> dict:
    """Return a copy of the agent dict with all top-level string values stripped."""
    return {
        k: (v.strip() if isinstance(v, str) else v)
        for k, v in a.items()
    }


def _migrate_from_file() -> list[dict]:
    """One-time import from agents.json.  Writes to DB on success."""
    try:
        path = _get_agents_file()
        if not path.exists():
            return []
        agents: list[dict] = json.loads(path.read_text(encoding="utf-8"))
        agents = [_strip_agent_strings(a) for a in agents]
        if agents:
            _save_dynamic_agents(agents)  # persist to DB
        return agents
    except Exception:  # noqa: BLE001
        return []


def _get_agents_file() -> Path:
    """Locate agents.json for backward-compatible migration reads."""
    candidate = Path(__file__).resolve()
    for _ in range(8):
        candidate = candidate.parent
        if (candidate / "pyproject.toml").exists():
            return candidate / "agents.json"
    return Path.cwd() / "agents.json"


def _save_dynamic_agents(agents: list[dict]) -> None:
    """Write the dynamic agent list to the dynamic_agents Postgres table."""
    try:
        import json as _json

        from acb_graph import get_session  # noqa: PLC0415
        from sqlalchemy import text  # noqa: PLC0415
        with get_session() as s:
            for a in agents:
                s.execute(
                    text(
                        "INSERT INTO dynamic_agents "
                        "(name, description, tags, status, agent_runtime, "
                        "repo_url, repo_name, local_path, integrations, "
                        "optional_integrations, updated_at) "
                        "VALUES (:n,:d,CAST(:t AS jsonb),:s,:r,:ru,:rn,:lp,"
                        "CAST(:i AS jsonb),CAST(:oi AS jsonb),now()) "
                        "ON CONFLICT (name) DO UPDATE SET "
                        "description=EXCLUDED.description, "
                        "tags=EXCLUDED.tags, "
                        "status=EXCLUDED.status, "
                        "agent_runtime=EXCLUDED.agent_runtime, "
                        "repo_url=EXCLUDED.repo_url, "
                        "repo_name=EXCLUDED.repo_name, "
                        "local_path=EXCLUDED.local_path, "
                        "integrations=EXCLUDED.integrations, "
                        "optional_integrations=EXCLUDED.optional_integrations, "
                        "updated_at=now()"
                    ),
                    {
                        "n": a["name"], "d": a.get("description", ""),
                        "t": _json.dumps(a.get("tags", [])),
                        "s": a.get("status", "live"),
                        "r": a.get("agent_runtime", "maf"),
                        "ru": a.get("repo_url"),
                        "rn": a.get("repo_name"),
                        "lp": a.get("local_path"),
                        "i": _json.dumps(a.get("integrations", [])),
                        "oi": _json.dumps(a.get("optional_integrations", [])),
                    },
                )
            s.commit()
    except Exception:
        # Fallback: write to agents.json so data is not lost if DB is down
        try:
            path = _get_agents_file()
            path.write_text(
                json.dumps(agents, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:  # noqa: BLE001
            pass


def _validate_agent_name(name: str) -> str:
    """Reject agent names not in the static or dynamic allowlist.

    Performs case-insensitive matching against the registry so names
    stored with mixed case in the DB still resolve correctly.
    """
    safe = name.lower().strip()
    # Strip optional 'agent-' prefix so 'agent-project-manager' matches 'project-manager'
    if safe.startswith("agent-"):
        safe_no_prefix = safe[len("agent-"):]
    else:
        safe_no_prefix = safe

    # Build case-insensitive lookup maps
    registry_names = {a["name"].lower(): a["name"] for a in _AGENT_REGISTRY}
    dynamic_names = {a["name"].lower(): a["name"] for a in _load_dynamic_agents()}
    known_lower = {n.lower() for n in _KNOWN_AGENTS}
    all_allowed_lower = known_lower | set(registry_names.keys()) | set(dynamic_names.keys())

    if safe not in all_allowed_lower and safe_no_prefix not in all_allowed_lower:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Unknown agent {name!r}. "
                f"Registered: {sorted(all_allowed_lower)}"
            ),
        )

    # Return the canonical (DB-stored) name to preserve original casing
    if safe in all_allowed_lower:
        return registry_names.get(safe) or dynamic_names.get(safe) or safe
    return registry_names.get(safe_no_prefix) or dynamic_names.get(safe_no_prefix) or safe_no_prefix


# The sentinel a session carries when its real agent hasn't been resolved yet
# (e.g. /chat/active-sessions returns it for a Redis-active thread with no
# chat_session row yet). It must NEVER reach a run dispatch as a literal agent
# name — see _resolve_agent_for_run below.
_UNRESOLVED_AGENT_SENTINELS = {"unknown", "", "undefined", "null", "none"}


#: A leading ``@name`` addresses one agent in a room. Only leading, and only
#: one: "@sales what about @finance's number" asks sales about finance, it does
#: not start two runs. Multi-agent rooms work by ADDRESSING, not by broadcast —
#: a message that reaches every agent produces N answers to one question and
#: N times the cost, which is not what anyone means by "ask the room".
_MENTION_RE = re.compile(r"^\s*@([A-Za-z0-9][A-Za-z0-9._-]*)\s*")


def _resolve_room(thread_id: str, email: str):
    """This person's place in the room, or ``None`` when rooms are unavailable.

    ``None`` — not a refusal — means the room layer is not there AT ALL: no
    ``thread_id``, no ``email``, or the ``gateway.rooms`` import failed. The run
    path predates rooms and must keep working without them, and the caller
    reads ``None`` as "no room to enforce".

    A *failed lookup* is a different thing and does NOT come back as ``None``:
    ``resolve_room_access`` handles its own database errors and returns a
    RoomAccess with ``resolve_failed`` set and every capability false, so the
    caller refuses with "try again in a moment" rather than sending into a room
    it could not identify. Returning ``None`` there would have quietly restored
    the old permissive behaviour on every Postgres hiccup.
    """
    if not thread_id or not email:
        return None
    try:
        from gateway.rooms import resolve_room_access
        return resolve_room_access(thread_id, email)
    except Exception:
        _log.warning("agent.room_resolve_failed", thread_id=thread_id[:12], exc_info=True)
        return None


def _room_agents(thread_id: str) -> list[tuple[str, str]]:
    """[(agent_name, role)] for a room, primary first. Empty on any failure."""
    if not thread_id:
        return []
    try:
        from acb_graph import get_session
        from sqlalchemy import text
        with get_session() as s:
            rows = s.execute(
                text(
                    "SELECT agent_name, role FROM chat_session_agent "
                    "WHERE session_id = :sid ORDER BY role DESC, added_at"
                ),
                {"sid": thread_id},
            ).fetchall()
        return [(r.agent_name, r.role) for r in rows]
    except Exception:
        return []


def _match_mention(mention: str, candidates: list[str]) -> str | None:
    """Match a typed ``@name`` against the agents present, forgivingly.

    People type ``@sales`` for ``agent-sales-assistant``. Exact match wins,
    then the ``agent-`` prefix is optional, then a unique prefix match — but
    only if it is unique: an ambiguous mention falls through to the primary
    agent rather than guessing which colleague's agent to spend money on.
    """
    want = mention.strip().lower()
    if not want:
        return None
    lowered = {c.lower(): c for c in candidates}
    if want in lowered:
        return lowered[want]
    if f"agent-{want}" in lowered:
        return lowered[f"agent-{want}"]
    starts = [
        original for low, original in lowered.items()
        if low.startswith(want) or low.removeprefix("agent-").startswith(want)
    ]
    return starts[0] if len(starts) == 1 else None


def _address_agent(req: AgentRunRequest, room) -> str:
    """Which agent answers this turn.

    A room can hold several agents (migration 139). An unaddressed turn goes to
    the primary; ``@name`` addresses one of the others. Outside a room — and
    for any mention that names nobody present — this is exactly
    ``_resolve_agent_for_run``, so nothing about single-agent chat changes.
    """
    requested = _resolve_agent_for_run(req.agent, req.thread_id)
    if room is None or not req.thread_id:
        return requested

    message = str(req.payload.get("message") or "")
    match = _MENTION_RE.match(message)
    if not match:
        return requested

    present = [name for name, _role in _room_agents(req.thread_id)]
    if not present:
        return requested
    addressed = _match_mention(match.group(1), present)
    if not addressed or addressed == requested:
        return requested
    try:
        return _validate_agent_name(addressed)
    except Exception:
        return requested


#: How much of a turn the room sees before it is persisted. The transcript is
#: the durable record; this is the "Vijay asked…" line in the live rail, and a
#: whole message there would duplicate content the history endpoint serves with
#: the room's redaction rules applied.
_ROOM_PREVIEW_CHARS = 280


def _room_preview(req: AgentRunRequest) -> str:
    text_ = str(req.payload.get("message") or "").strip()
    if len(text_) <= _ROOM_PREVIEW_CHARS:
        return text_
    return text_[:_ROOM_PREVIEW_CHARS] + "…"


def _resolve_agent_for_run(agent: str | None, thread_id: str | None) -> str:
    """Resolve the agent name for a run, recovering an unresolved sentinel.

    A chat session can carry ``agent_name='unknown'`` (the placeholder that
    ``/chat/active-sessions`` returns for a Redis-active thread whose
    ``chat_session`` row doesn't exist yet). If that poisoned value is dispatched
    verbatim, ``_validate_agent_name`` 422s with the raw registry list — the
    "Unknown agent 'unknown'" error users hit.

    When the requested agent is such a sentinel AND we have a ``thread_id``, the
    thread's most recent ``agent_run`` trace records the REAL agent that ran on
    it — recover from there before validating. Otherwise fall through to
    ``_validate_agent_name``, which now gives an actionable message for the
    sentinel instead of dumping the registry.
    """
    raw = (agent or "").strip()
    if raw.lower() not in _UNRESOLVED_AGENT_SENTINELS:
        return _validate_agent_name(raw)

    # Sentinel — try to recover the real agent from the run trace.
    if thread_id:
        try:
            from acb_graph import get_session  # noqa: PLC0415
            from sqlalchemy import text  # noqa: PLC0415

            with get_session() as s:
                rows = s.execute(
                    text(
                        "SELECT agent_name FROM agent_run "
                        "WHERE thread_id = :tid AND agent_name <> '' "
                        "ORDER BY started_at DESC LIMIT 10"
                    ),
                    {"tid": thread_id},
                ).fetchall()
            # Take the most recent trace whose agent is a REAL agent (skip any
            # sentinel that a prior run might itself have recorded). Filtering
            # in Python keeps the SQL free of expanding-bindparam subtleties.
            for r in rows:
                name = (r.agent_name or "").strip()
                if name and name.lower() not in _UNRESOLVED_AGENT_SENTINELS:
                    # Validate the recovered name (still guards against a stale
                    # trace pointing at a since-removed agent).
                    return _validate_agent_name(name)
        except Exception:  # noqa: BLE001 — fall through to the actionable error
            pass

    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail=(
            "This conversation isn't linked to an agent yet — please pick "
            "an agent to continue (the session's agent could not be resolved"
            f"{' from its history' if thread_id else ''})."
        ),
    )


# ---------------------------------------------------------------------------
# Webhook routing table
# Maps (source, event_type) → agent name.
# Phase 2: driven by each agent's config.json; here it is hard-coded for Phase 0.
# ---------------------------------------------------------------------------

#
# 🔴 **EMPTY SINCE 2026-08-25 (D52, WS-39 S1 repair round 1), and that is the
# decision.** Its three entries were all `("clickup", …)`; D52 deleted the
# ClickUp receiver, so they routed events nothing could send. An unmatched
# `(source, event_type)` is answered by `_route_webhook`'s no-route branch (it
# reports `known_routes`), which is the correct outcome — the alternative was a
# table promising a route into an integration that no longer exists.
_WEBHOOK_ROUTES: dict[tuple[str, str], str] = {}


# ---------------------------------------------------------------------------
# Request model for registering a new agent
# ---------------------------------------------------------------------------

class RegisterAgentRequest(BaseModel):
    name: str
    """Unique slug, e.g. ``"my-agent"``."""
    description: str = ""
    repo_url: str = ""
    """GitHub repo as ``owner/repo`` or full ``https://github.com/owner/repo`` URL."""
    local_path: str | None = None
    """Absolute path to a local agent directory (dev mode).  Takes priority over repo_url."""
    tags: list[str] = []
    integrations: list[str] = []
    optional_integrations: list[str] = []


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/config", summary="Fetch config.json from a GitHub agent repo or local path")
async def get_agent_config(
    repo: str,
    user: UserContext = Depends(get_current_user),
) -> dict:
    """Fetch and return the agent's config.json.

    ``repo`` may be:
    - ``owner/repo`` or ``https://github.com/owner/repo`` — fetched from GitHub
    - An absolute local path (``/path/to/dir`` or ``C:\\path\\to\\dir``) — read from disk

    Returns the parsed config dict, or raises 404 if not found.
    """
    import httpx  # noqa: PLC0415

    raw = repo.strip().rstrip("/")

    # ── Local path ─────────────────────────────────────────────────────────
    local = Path(raw)
    if local.is_absolute():
        # Resolve to prevent traversal tricks
        resolved = local.resolve()
        config_file = resolved / "config.json"
        if not resolved.is_dir():
            raise HTTPException(status_code=404, detail=f"Directory not found: {raw}")
        if not config_file.exists():
            raise HTTPException(status_code=404, detail="config.json not found in that directory.")
        try:
            return json.loads(config_file.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            raise HTTPException(status_code=422, detail="config.json is not valid JSON.")

    # ── GitHub ──────────────────────────────────────────────────────────────
    slug = raw.removeprefix("https://github.com/").removeprefix("http://github.com/")

    settings = get_settings()
    headers: dict[str, str] = {"Accept": "application/vnd.github.raw+json"}
    token: str = getattr(settings, "github_token", "") or ""
    if token:
        headers["Authorization"] = f"token {token}"

    async with httpx.AsyncClient(timeout=8) as client:
        for branch in ("main", "master", "HEAD"):
            url = f"https://raw.githubusercontent.com/{slug}/{branch}/config.json"
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                try:
                    return resp.json()
                except Exception:  # noqa: BLE001
                    raise HTTPException(status_code=422, detail="config.json is not valid JSON.")

    raise HTTPException(
        status_code=404,
        detail=f"config.json not found in {slug!r} (tried main, master, HEAD).",
    )


# ---------------------------------------------------------------------------
# Agent display-name (alias) overlay.
#
# An alias is a user-editable friendly name shown in the Agents page, chat, and
# observability.  It is a pure DISPLAY overlay: the canonical ``name`` stays the
# key for runs, avatars, localStorage, dispatch and DB rows.  Stored as ONE blob
# in the model_config table (survives deploys/reboots) keyed by canonical name,
# so it works uniformly for BOTH static built-in and dynamically-registered
# agents — the dynamic_agents.display_name column is deliberately NOT used (it
# can't cover built-ins).
# ---------------------------------------------------------------------------
_AGENT_ALIASES_KEY = "agent_aliases"


def _load_agent_aliases() -> dict[str, str]:
    """Return ``{canonical_name: alias}``.  Best-effort → ``{}`` on any error.

    MT-1j slice 5: scoped to the request's bound organization. Both call sites
    (`list_agents`/`get_agent` here and `observability.roster`) are HTTP
    handlers under the app-wide `require_authenticated`, so the tenant
    `_with_resolved_access` bound is in context; ``None`` outside a request
    hands `model_config._resolve_org` the argument it already receives today.
    Never taken from request input — R5 / `user_management_contract.md` R11.
    """
    try:
        from acb_llm.model_config import load_blob  # noqa: PLC0415
        blob = load_blob(
            _AGENT_ALIASES_KEY, {}, organization_id=current_tenant()
        )
        if isinstance(blob, dict):
            return {
                str(k): str(v).strip()
                for k, v in blob.items()
                if str(v).strip()
            }
    except Exception:  # noqa: BLE001
        pass
    return {}


def _set_agent_alias(name: str, alias: str) -> str:
    """Set (or clear, when *alias* is empty/blank) an agent's display name.

    Returns the stored alias ("" when cleared).  Raises only on a real DB write
    failure so the caller can surface it.
    """
    from acb_llm.model_config import load_blob, save_blob  # noqa: PLC0415
    org = current_tenant()          # MT-1j slice 5 — see _load_agent_aliases
    blob = load_blob(_AGENT_ALIASES_KEY, {}, organization_id=org)
    if not isinstance(blob, dict):
        blob = {}
    alias = (alias or "").strip()
    if alias:
        blob[name] = alias
    else:
        blob.pop(name, None)
    save_blob(_AGENT_ALIASES_KEY, blob, organization_id=org)
    return alias


@router.get("", summary="List all registered agents")
async def list_agents(
    user: UserContext = Depends(get_current_user),
) -> list[dict]:
    """Return the merged static + dynamic agent registry.

    Includes ``behind_by`` (int) for GitHub Copilot agents: the number of
    commits the local clone is behind the remote.  Zero or absent means
    up-to-date.  Non-blocking — git operations time out after 5 s.
    """
    dynamic = _load_dynamic_agents()
    dynamic_names = {a["name"] for a in dynamic}
    # Static agents not overridden by dynamic entries come first
    static = [a for a in _AGENT_REGISTRY if a["name"] not in dynamic_names]
    # Back-fill agent_runtime for legacy dynamic entries that predate the field
    # or have NULL in the DB column.  Rule: only entries registered FROM a
    # GitHub repo URL are "github-copilot"; everything else (local path,
    # unknown) is plain MAF.
    for a in dynamic:
        if not a.get("agent_runtime"):
            a["agent_runtime"] = (
                "github-copilot"
                if (a.get("repo_name") or a.get("repo_url"))
                and not a.get("local_path")
                else "maf"
            )
    merged = static + dynamic

    # Honor each agent's declared config.json runtime (authoritative over the
    # registration heuristic) so the picker groups MAF vs Copilot SDK by what
    # the agent actually is.  Covers static built-ins too (e.g. email-assistant
    # declares "runtime": "maf").  _load_dynamic_agents already applied this to
    # dynamic entries; re-applying here is idempotent and also fixes statics.
    for a in merged:
        rt = _declared_runtime(a["name"], a.get("local_path"))
        if rt:
            a["agent_runtime"] = rt

    # ── Git status: how many commits behind is each agent's clone? ─────
    settings = get_settings()
    agents_clone_dir = getattr(
        settings, "agents_clone_dir", "/tmp/acb_agents"
    )
    repos_root = Path(agents_clone_dir) / "repos"

    for a in merged:
        if a.get("agent_runtime") != "github-copilot":
            continue
        clone_path = repos_root / a["name"]
        if not (clone_path / ".git").is_dir():
            continue
        behind = await _git_behind_count(str(clone_path))
        if behind > 0:
            a["behind_by"] = behind

    # Attach dependency-install health so the agents page can warn about unmet
    # deps (and any apt/system packages a build needs).
    try:
        from acb_skills.loader import read_dep_status  # noqa: PLC0415
        from gateway.routes.workspace import \
            _agent_workspace_dir  # noqa: PLC0415
        for a in merged:
            try:
                ws = _agent_workspace_dir(a["name"])
                ds = read_dep_status(ws) if ws is not None else None
                if ds:
                    a["dep_status"] = ds
            except Exception:  # noqa: BLE001
                continue
    except Exception:  # noqa: BLE001
        pass

    # Overlay the user-set display name (alias) onto every agent, static or
    # dynamic.  Empty string when unset — the UI falls back to ``name``.
    aliases = _load_agent_aliases()
    for a in merged:
        a["display_name"] = aliases.get(a["name"], "")

    # ── Access filter (org access control, enforcement seam 2) ────────────
    # This list feeds both the chat agent picker and the /agents management
    # pane. Anyone who can manage agents sees the whole registry; everyone
    # else sees only what they may actually run, so the picker never offers a
    # choice that would 403 on use.
    if not user.has_permission("agents:manage"):
        merged = [a for a in merged if user.can_run_agent(a["name"])]

    return merged


@router.post("/{name}/pull", summary="Pull latest commits for an agent's local clone", dependencies=[require_permission("agents:manage")])
async def pull_agent(
    name: str,
    user: UserContext = Depends(get_current_user),
) -> dict:
    """Pull the latest commits from origin into the agent's local clone.

    Runs ``git pull --rebase`` (via ``_pull_latest``) so local pending
    commits are preserved on top of the updated remote.  Returns the
    before/after HEAD SHAs and how many commits were pulled.
    """
    import asyncio  # noqa: PLC0415

    agent_name = _validate_agent_name(name)
    settings = get_settings()
    agents_clone_dir = getattr(
        settings, "agents_clone_dir", "/tmp/acb_agents"
    )
    clone_path = Path(agents_clone_dir) / "repos" / agent_name

    if not (clone_path / ".git").is_dir():
        raise HTTPException(
            status_code=404,
            detail=f"No local clone found for {agent_name!r}. "
                   f"Run the agent once to create it.",
        )

    # Capture HEAD before pull
    head_before = ""
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "rev-parse", "HEAD",
            cwd=str(clone_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        if proc.returncode == 0:
            head_before = out.decode(errors="replace").strip()
    except Exception:  # noqa: BLE001
        pass

    # Pull latest
    pull_info: dict[str, Any] = {"strategy": "skipped"}
    try:
        from acb_skills.loader import _pull_latest  # noqa: PLC0415
        pull_info = _pull_latest(clone_path)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500,
            detail=f"git pull failed: {exc}",
        ) from exc

    # Capture HEAD after pull
    head_after = ""
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "rev-parse", "HEAD",
            cwd=str(clone_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        if proc.returncode == 0:
            head_after = out.decode(errors="replace").strip()
    except Exception:  # noqa: BLE001
        pass

    # Count how many new commits were pulled
    pulled_count = 0
    if head_before and head_after and head_before != head_after:
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "rev-list", "--count",
                f"{head_before}..{head_after}",
                cwd=str(clone_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            if proc.returncode == 0:
                pulled_count = int(
                    out.decode(errors="replace").strip() or "0"
                )
        except (ValueError, Exception):  # noqa: BLE001
            pass

    # Re-check behind_by for the response
    behind_after = await _git_behind_count(str(clone_path))

    _log.info(
        "agent.pulled",
        agent=agent_name,
        head_before=head_before[:8],
        head_after=head_after[:8],
        pulled=pulled_count,
        still_behind=behind_after,
        strategy=pull_info.get("strategy", "unknown"),
    )

    return {
        "agent": agent_name,
        "pulled": pulled_count,
        "behind_by": behind_after,
        "head_before": head_before[:8] if head_before else None,
        "head_after": head_after[:8] if head_after else None,
        "strategy": pull_info.get("strategy", "unknown"),
        "conflicts_resolved_by_llm": pull_info.get(
            "conflicts_resolved_by_llm", False
        ),
    }


@router.post("", status_code=status.HTTP_201_CREATED, summary="Register an agent from a GitHub repo", dependencies=[require_permission("agents:manage")])
async def register_agent(
    req: RegisterAgentRequest,
    background_tasks: BackgroundTasks,
    user: UserContext = Depends(get_current_user),
) -> dict:
    """Add a new agent to the dynamic registry and persist it to agents.json.

    Accepts either a GitHub URL (``repo_url``) or an absolute local directory
    path (``local_path``).  In both cases, if metadata fields are empty the
    endpoint reads ``config.json`` to fill them.  For GitHub repos a background
    git clone is also triggered so the agent is warm before its first run.
    """
    import httpx  # noqa: PLC0415

    # Validate name format
    if not re.match(r"^[a-z0-9][a-z0-9-]{0,48}[a-z0-9]$", req.name):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Agent name must be 2-50 lowercase letters, digits, or hyphens (no leading/trailing hyphens).",
        )

    dynamic = _load_dynamic_agents()
    all_names = {a["name"] for a in _AGENT_REGISTRY} | {a["name"] for a in dynamic}
    if req.name in all_names:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Agent {req.name!r} is already registered.",
        )

    description = req.description or ""
    tags = req.tags or []
    integrations = req.integrations or []
    optional_integrations = req.optional_integrations or []

    # ── Determine source: local path or GitHub ──────────────────────────────
    local_path: str | None = None
    repo_url: str = (req.repo_url or "").strip().rstrip("/")
    repo_name: str = ""

    # Detect local path: req.local_path set, or repo_url is an absolute path
    raw_input = req.local_path or (repo_url if Path(repo_url).is_absolute() else None)
    if raw_input:
        resolved = Path(raw_input).resolve()
        if not resolved.is_dir():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Local path does not exist: {raw_input}",
            )
        local_path = str(resolved)
        # Auto-read config.json from disk if metadata is missing
        if not description or not integrations:
            config_file = resolved / "config.json"
            if config_file.exists():
                try:
                    cfg: dict = json.loads(config_file.read_text(encoding="utf-8"))
                    description = description or cfg.get("description", "")
                    tags = tags or cfg.get("tags", [])
                    integrations = integrations or cfg.get("integrations", [])
                    optional_integrations = optional_integrations or cfg.get("optional_integrations", [])
                    _log.info("agent.config_read_local", name=req.name, path=local_path)
                except Exception as exc:  # noqa: BLE001
                    _log.warning("agent.config_parse_failed", name=req.name, error=str(exc))
    else:
        # GitHub URL
        repo_name = repo_url.removeprefix("https://github.com/").removeprefix("http://github.com/")
        if not description or not integrations:
            settings = get_settings()
            gh_token: str = getattr(settings, "github_token", "") or ""
            headers: dict[str, str] = {"Accept": "application/vnd.github.raw+json"}
            if gh_token:
                headers["Authorization"] = f"token {gh_token}"
            last_status: int = 0
            try:
                async with httpx.AsyncClient(timeout=8) as client:
                    cfg = {}
                    for branch in ("main", "master", "HEAD"):
                        url = (
                            "https://raw.githubusercontent.com"
                            f"/{repo_name}/{branch}/config.json"
                        )
                        resp = await client.get(url, headers=headers)
                        last_status = resp.status_code
                        if resp.status_code == 200:
                            try:
                                cfg = resp.json()
                            except Exception:  # noqa: BLE001
                                cfg = {}
                            break
                    if cfg:
                        description = description or cfg.get("description", "")
                        tags = tags or cfg.get("tags", [])
                        integrations = integrations or cfg.get(
                            "integrations", []
                        )
                        optional_integrations = (
                            optional_integrations
                            or cfg.get("optional_integrations", [])
                        )
                        _log.info(
                            "agent.config_fetched",
                            name=req.name,
                            repo=repo_name,
                        )
                    elif last_status in (403, 404):
                        _log.warning(
                            "agent.config_not_found_or_forbidden",
                            name=req.name,
                            repo=repo_name,
                            status=last_status,
                            hint=(
                                "Repo may be private or the GitHub token "
                                "may not have access to this organisation."
                            ),
                        )
            except Exception as exc:  # noqa: BLE001
                _log.warning(
                    "agent.config_fetch_failed",
                    name=req.name,
                    error=str(exc),
                )

    # agent_runtime: only agents registered FROM a GitHub repo URL run via the
    # GitHub Copilot SDK (GitHubCopilotAgent). Local-path agents are plain MAF.
    agent_runtime = "github-copilot" if (repo_name and not local_path) else "maf"

    entry: dict = {
        "name": req.name,
        "description": description,
        "tags": tags,
        "status": "live",
        "agent_runtime": agent_runtime,
        "repo_url": repo_url or None,
        "repo_name": repo_name or None,
        "local_path": local_path,
        "integrations": integrations,
        "optional_integrations": optional_integrations,
        "dynamic": True,
    }
    dynamic.append(entry)
    _save_dynamic_agents(dynamic)
    _log.info("agent.registered", name=req.name, actor=user.email, source="local" if local_path else "github")

    # Eager background clone — only for GitHub repos (local paths need no cloning)
    if not local_path and repo_name:
        def _eager_clone(agent_name: str, repo_slug: str) -> None:
            try:
                from acb_skills.loader import load_agent  # noqa: PLC0415

                # Pass the full org/repo slug — load_agent splits when needed
                with load_agent(agent_name, repo_name=repo_slug):
                    pass
                _log.info("agent.eager_clone_done", name=agent_name)
            except Exception as exc:  # noqa: BLE001
                _log.warning("agent.eager_clone_failed", name=agent_name, error=str(exc))

        background_tasks.add_task(_eager_clone, req.name, repo_name)

    return entry


def _cleanup_agent_workspace(agent_name: str) -> bool:
    """Delete the clone-cache directory for *agent_name*.

    Returns ``True`` if the directory existed and was removed, ``False``
    if it didn't exist.  Errors (permissions, etc.) are logged and swallowed
    so cleanup failures don't block the API response.
    """
    import shutil as _shutil  # noqa: PLC0415

    try:
        from acb_common import get_settings  # noqa: PLC0415
        settings = get_settings()
        clone_root = Path(
            getattr(settings, "agents_clone_dir", "/tmp/acb_agents")
        ) / "repos"
        target = clone_root / agent_name
        if target.is_dir():
            _shutil.rmtree(target, ignore_errors=True)
            _log.info(
                "agent.workspace_cleaned",
                name=agent_name,
                path=str(target),
            )
            return True
        return False
    except Exception as exc:
        _log.warning(
            "agent.workspace_cleanup_failed",
            name=agent_name,
            error=str(exc),
        )
        return False


@router.delete("/{name}", summary="Remove a user-registered agent", dependencies=[require_permission("agents:manage")])
async def remove_agent(
    name: str,
    user: UserContext = Depends(get_current_user),
) -> dict:
    """Delete a dynamic agent from the registry and clean up its workspace.

    Built-in agents cannot be removed.  The agent's clone directory on disk
    is also deleted so stale artifacts don't linger in the file browser.
    """
    if name in _KNOWN_AGENTS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Built-in agent {name!r} cannot be removed via the API.",
        )
    dynamic = _load_dynamic_agents()
    new_dynamic = [a for a in dynamic if a["name"] != name]
    if len(new_dynamic) == len(dynamic):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent {name!r} not found.",
        )

    # ── Delete the DB row (not just stop upserting) ──────────────────
    try:
        from acb_graph import get_session  # noqa: PLC0415
        from sqlalchemy import text  # noqa: PLC0415
        with get_session() as s:
            s.execute(
                text("DELETE FROM dynamic_agents WHERE name = :n"),
                {"n": name},
            )
            s.commit()
    except Exception as exc:
        _log.warning(
            "agent.db_delete_failed",
            name=name,
            error=str(exc),
        )

    _save_dynamic_agents(new_dynamic)

    # ── Clean up workspace files on disk ─────────────────────────────
    import asyncio as _asyncio  # noqa: PLC0415
    await _asyncio.get_event_loop().run_in_executor(
        None, _cleanup_agent_workspace, name,
    )

    _log.info("agent.removed", name=name, actor=user.email)
    return {"deleted": name}


class PatchAgentRequest(BaseModel):
    display_name: str | None = None
    description: str | None = None
    tags: list[str] | None = None
    integrations: list[str] | None = None
    optional_integrations: list[str] | None = None
    status: str | None = None


@router.patch("/{name}", summary="Update metadata for a user-registered agent", dependencies=[require_permission("agents:manage")])
async def patch_agent(
    name: str,
    req: PatchAgentRequest,
    user: UserContext = Depends(get_current_user),
) -> dict:
    """Partially update an agent's metadata.

    The **display name (alias)** can be set for ANY known agent — static
    built-in or dynamic — because it is a separate display overlay that never
    mutates the built-in's code definition.  All OTHER metadata edits
    (description/tags/integrations/status) remain dynamic-only: built-ins are
    defined in code.

    When an agent's status is changed from ``"live"`` to anything else, its
    workspace files on disk are automatically cleaned up so stale artifacts
    don't linger in the file browser.
    """
    dynamic = _load_dynamic_agents()
    entry = next((a for a in dynamic if a["name"] == name), None)
    is_builtin = name in _KNOWN_AGENTS
    if entry is None and not is_builtin:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent {name!r} not found.",
        )

    # ── Display name (alias) — allowed for built-ins AND dynamic agents ──
    if req.display_name is not None:
        _set_agent_alias(name, req.display_name)

    # ── Other metadata edits are dynamic-only ────────────────────────
    other_fields = (
        req.description,
        req.tags,
        req.integrations,
        req.optional_integrations,
        req.status,
    )
    if any(f is not None for f in other_fields):
        if entry is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Built-in agent {name!r}: only the display name can be "
                    "changed via the API."
                ),
            )
        prev_status = entry.get("status", "live")
        if req.description is not None:
            entry["description"] = req.description
        if req.tags is not None:
            entry["tags"] = req.tags
        if req.integrations is not None:
            entry["integrations"] = req.integrations
        if req.optional_integrations is not None:
            entry["optional_integrations"] = req.optional_integrations
        if req.status is not None:
            entry["status"] = req.status
        _save_dynamic_agents(dynamic)

        # ── Clean up workspace when deactivating ─────────────────────
        if (
            req.status is not None
            and prev_status == "live"
            and req.status != "live"
        ):
            import asyncio as _asyncio  # noqa: PLC0415
            await _asyncio.get_event_loop().run_in_executor(
                None, _cleanup_agent_workspace, name,
            )

    _log.info("agent.patched", name=name, actor=user.email)
    # Return the current view (entry for dynamic; a minimal dict for built-ins),
    # always carrying the resolved display name.
    result = dict(entry) if entry is not None else {"name": name}
    result["display_name"] = _load_agent_aliases().get(name, "")
    return result


@router.post("/run/stream", summary="Stream a named agent run as AG-UI SSE events")
async def run_agent_stream_endpoint(
    req: AgentRunRequest,
    _request: Request,
    user: UserContext = Depends(get_current_user),
    background_tasks: BackgroundTasks = BackgroundTasks(),
) -> Response:
    """Stream a named-agent run as AG-UI Server-Sent Events.

    Returns an ``text/event-stream`` response emitting AG-UI protocol events:
    ``RUN_STARTED``, ``TOOL_CALL_START``, ``TOOL_CALL_ARGS``, ``TOOL_CALL_RESULT``,
    ``TEXT_MESSAGE_CONTENT``, ``RUN_FINISHED`` / ``RUN_ERROR``.

    The Next.js ``/api/agent/chat`` route already knows how to translate this
    stream to the frontend SSE format — it is the same translation layer used
    for the orchestrator's ``/copilot/chat`` endpoint.

    Use this instead of ``POST /agent/run`` whenever the caller wants live
    tool-call visibility (e.g. the control-plane chat UI).
    """
    import asyncio

    from orchestrator.executor import run_agent_stream  # noqa: PLC0415

    # Room guard: sending is a contributor's act. A viewer invited to WATCH a
    # conversation must not be able to drive its agents — otherwise "add them
    # as a viewer" is a one-click way around the room's roles entirely.
    actor_email = getattr(user, "email", "") or ""
    room = await asyncio.to_thread(
        _resolve_room, req.thread_id or "", actor_email,
    )
    if room is not None and not room.can_send:
        raise HTTPException(status_code=403, detail=room.denied("send messages"))

    agent_name = _address_agent(req, room)
    # Org access control, enforcement seam 2: the picker is filtered, but the
    # endpoint is the boundary of record — a hand-crafted request naming an
    # agent the member cannot run is refused here, not in the UI.
    await assert_can_run_agent_in_session(user, agent_name, req.thread_id)

    # The other people in the room find out what was asked, and by whom, the
    # moment it is asked — the run stream carries only the agent's side.
    if room is not None and room.is_shared:
        await publish_room_event(req.thread_id or "", {
            "type": "USER_MESSAGE",
            "author": actor_email,
            "agentName": agent_name,
            "content": _room_preview(req),
        })

    # ── Steer instead of 409 (§4.6, §QM-1) ────────────────────────────────────
    # Before anything expensive — before memory assembly, before the executor —
    # decide what this message DOES. Four outcomes; only one of them starts a
    # run. See _route_incoming_turn / orchestrator.steer.route_turn.
    _incoming_text = str(
        req.payload.get("message") or req.payload.get("user_query") or ""
    )
    _decision = await _route_incoming_turn(
        req.thread_id or "", actor_email, _incoming_text,
    )
    _steered = await _apply_turn_decision(
        _decision, req, agent_name, actor_email, room,
    )
    if _steered is not None:
        return _steered

    run_id = req.run_id or str(uuid.uuid4())
    user_id: str = getattr(user, "email", "") or "anonymous"

    # ── Set user + agent context for memory tools ─────────────────────────
    # user_id scopes THIS user's private memory (remember/save_memory); the
    # agent name scopes the agent's cross-user memory (recall_agent/…). Org
    # memory is a fixed global scope, so it needs no per-run context.
    #
    # In a SHARED room this is switched off entirely. One person's private
    # facts must not be stitched into a context whose output the whole room
    # reads, and the room's turns must not be extracted into the typer's
    # personal store — both directions are wrong, and the second is worse
    # because it is silent (groups_sessions_authority.md §3, "personal memory
    # in shared rooms"). Agent and org memory are unaffected: they were never
    # one person's. A room of one takes neither branch.
    _room_is_shared = room is not None and room.is_shared

    # Which compartments this run may read, and the one it may write. Solo
    # resolves to exactly the three scopes and the write target it always had.
    from acb_memory import resolve_clearance
    _clearance = resolve_clearance(
        actor=user_id if user_id != "anonymous" else "",
        agent_name=agent_name,
        thread_id=req.thread_id,
        shared=_room_is_shared,
    )

    try:
        from acb_skills.memory_tools import (  # noqa: PLC0415
            _set_memory_user_id,
            _set_memory_agent_name,
        )
        # remember/save_memory read and write THIS compartment. In a room that
        # is the room's own, so the tools keep working and file what they learn
        # where the room can see it — rather than into whoever happened to type,
        # which is the write rule's whole point.
        _set_memory_user_id(_clearance.write)
        _set_memory_agent_name(agent_name)
    except ImportError:
        pass

    # ── Memory enrichment: inject relevant past facts into the agent's context ──
    # Phase 4 (specs/llm_caching_memory.md): the assembled memory block is
    # cached per session (thread) in Redis so it stays byte-stable across turns
    # — otherwise Mem0's per-query semantic search returns a different block
    # every turn and defeats cross-turn prompt caching on the memory portion.
    _mem_thread_id = req.thread_id or f"{agent_name}:{run_id}"
    try:
        from acb_memory import (  # noqa: PLC0415
            get_memory_context,
            get_scoped_context,
            get_session_memory,
            scope_key,
            search_entity_timeline,
        )
        user_msg = (
            req.payload.get("message")
            or req.payload.get("user_query")
            or ""
        )
        # Agent + org memory are NOT user-scoped, so they load even for an
        # anonymous user; the per-user Mem0/Graphiti blocks stay gated on a
        # real user_id.
        if user_msg:
            _has_user = user_id != "anonymous" and not _room_is_shared

            async def _build_memory_block() -> str:
                parts: list[str] = []
                # Mem0: this user's private episodic facts. Never in a room —
                # not the owner's, not the typer's. The scope key is simply not
                # passed, so there is no retrieval to leak (memory-clearance.md
                # §3.3: a boundary, not a request in a system prompt).
                if _has_user:
                    mem_ctx = await get_memory_context(user_id, user_msg)
                    if mem_ctx:
                        parts.append("## Memory from past conversations\n" + mem_ctx)
                # What this room has established. Replaces the personal
                # compartment rather than adding to it: shared work is
                # remembered where everyone in the room can see it.
                if _clearance.room:
                    room_ctx = await get_scoped_context(
                        _clearance.room, user_msg, header="Room memory",
                    )
                    if room_ctx:
                        parts.append(
                            "## What this room has established\n" + room_ctx
                        )
                # How the person being answered likes to work. Safe to carry
                # into a room: they are in it, and preferences describe them
                # rather than disclosing what they told the agent in private.
                if _clearance.prefs:
                    prefs_ctx = await get_scoped_context(
                        _clearance.prefs, user_msg, header="Preferences",
                    )
                    if prefs_ctx:
                        parts.append("## How to answer this person\n" + prefs_ctx)
                # Agent memory: shared across every user of this agent
                agent_ctx = await get_scoped_context(
                    scope_key(agent=agent_name), user_msg, header="Agent memory"
                )
                if agent_ctx:
                    parts.append("## This agent's shared memory\n" + agent_ctx)
                # Org memory: organisation-wide, shared by every agent + user
                org_ctx = await get_scoped_context(
                    scope_key(org=True), user_msg, header="Organisation memory"
                )
                if org_ctx:
                    parts.append("## Organisation-wide memory\n" + org_ctx)
                # Graphiti: time-aware facts about entities in the query
                if _has_user:
                    graph_ctx = await search_entity_timeline(user_msg[:80], user_msg)
                    if graph_ctx:
                        parts.append(
                            "## Timeline facts from knowledge graph\n" + graph_ctx
                        )
                return "\n\n".join(parts)

            _redis = None
            try:
                from orchestrator.stream_relay import (  # noqa: PLC0415
                    _get_client,
                )
                _redis = await _get_client()
            except Exception:  # noqa: BLE001 — no Redis → fetch fresh each turn
                _redis = None

            memory_context = await get_session_memory(
                redis=_redis,
                thread_id=_mem_thread_id,
                build=_build_memory_block,
                # Without this a thread cached while solo would keep serving
                # the owner's private block to the room for the rest of the
                # TTL, undoing the read rule above.
                clearance=_clearance.fingerprint,
            )
            if memory_context:
                req.payload["memory_context"] = memory_context
                _log.debug(
                    "agent.memory_enriched",
                    agent=agent_name,
                    user=user_id[:20],
                )
    except ImportError:
        pass

    # ── Memory extraction: save conversation facts after the run completes ──
    # NOTE: Mem0 episodic extraction is handled by the Next.js route
    # (/api/agent/chat) which captures the FULL conversation INCLUDING the
    # assistant's response streamed back.  The gateway only has access to
    # the request payload (user messages + history) BEFORE the agent runs,
    # so a background task here would save an incomplete conversation and
    # produce poor-quality memory facts.
    #
    # Graphiti knowledge-graph ingestion is still done here because it
    # operates on entity mentions in the user's query — it doesn't need
    # the assistant's response.
    try:
        from acb_memory import add_episode  # noqa: PLC0415

        user_msg = req.payload.get("message") or ""
        if user_msg and user_id != "anonymous":
            background_tasks.add_task(
                add_episode,
                name=f"agent:{agent_name}:{user_id[:20]}",
                content=user_msg[:500],
                source_description=f"agent_{agent_name}",
                group_id=user_id,
            )
    except ImportError:
        pass

    _log.info("agent.stream_run_start", agent=agent_name, run_id=run_id, actor=user.email)

    # ── Detached execution (spec_stream_reconnection) ──────────────────────
    # The agent generator runs in a background task that pushes ALL events to
    # the per-thread Redis stream (executor self-tees via _sse).  This HTTP
    # response is merely a Redis subscriber: if the client disconnects,
    # uvicorn cancels the subscriber but the agent keeps running.  A
    # reconnecting client replays from its cursor via GET .../reconnect.
    from orchestrator.stream_relay import (  # noqa: PLC0415
        SupersedeRefused, run_detached,
    )

    from gateway.chat_fold import \
        persist_final_assistant_message  # noqa: PLC0415

    thread_id = req.thread_id or f"{agent_name}:{run_id}"

    # C2 — server-side history rebuild for non-chat callers. When the caller
    # sent no `messages` (an API/webhook client that doesn't keep a browser
    # store) but we have a thread_id, hand the executor's assembler a loader
    # that rebuilds history from the authoritative chat_message store — so
    # every caller gets the SAME context the browser client would. The browser
    # chat path keeps sending `messages`, so the loader is only consulted on the
    # empty-history case (see acb_llm.assemble_run_context).
    if req.thread_id and not (req.payload.get("messages") or []):
        _hist_uid = (user.email or "").strip() or "anonymous"

        def _load_history_from_store() -> list[dict[str, str]]:
            from gateway.routes.chat import _get_messages  # noqa: PLC0415
            rows = _get_messages(thread_id, _hist_uid, limit=50)
            return [
                {"role": str(r.get("role") or "user"),
                 "content": str(r.get("content") or "")}
                for r in rows
                if r.get("role") in ("user", "assistant")
                and str(r.get("content") or "").strip()
            ]

        req.payload["_history_loader"] = _load_history_from_store

    # Authoritative persistence at run end (core_loop_unification Phase 1):
    # the detached task folds the run's Redis event log into the chat_message
    # row this turn renders as — the tail survives even when the browser and
    # the Next translator are long gone (P0-3).
    _persist_message_id = (
        req.assistant_message_id or f"assistant-{thread_id}-{run_id}"
    )
    _mem_user = (user.email or "").strip()
    # Which compartment the run's turns are extracted into. What several people
    # said in a room is the room's, so it files under `room:<thread_id>` and
    # never into one participant's private store — a disclosure nobody
    # consented to, and a silent one. Deliberately NOT `_mem_user`, which also
    # names the session's owner and the acting member for authorship and must
    # stay a real email.
    _extract_user = _clearance.write if _room_is_shared else _mem_user
    _mem_message = str(req.payload.get("message") or "")
    _mem_history = [
        {"role": str(m.get("role") or "user"), "content": str(m.get("content") or "")}
        for m in (req.payload.get("messages") or [])
        if isinstance(m, dict) and m.get("role") in ("user", "assistant")
        and m.get("content")
    ]

    async def _persist_on_complete() -> None:
        folded = await persist_final_assistant_message(
            thread_id, _persist_message_id,
            user_id=_mem_user, agent_name=agent_name,
            run_id=run_id, model=req.model,
        )
        # Memory extraction at the SAME run boundary (review P1-9): the Next
        # translator only extracted while its reader was alive, so turns
        # completed after a browser-gone/reconnect contributed nothing to
        # Mem0. The gateway is now the single extraction owner for this path
        # (route.ts no longer extracts for named agents). Best-effort.
        if not (_extract_user and folded):
            return
        try:
            from acb_memory import add_memories_background  # noqa: PLC0415

            from gateway.chat_fold import (  # noqa: PLC0415
                build_extraction_conversation,
            )
            conv = build_extraction_conversation(
                _mem_history, _mem_message, folded,
            )
            if conv:
                await add_memories_background(
                    _extract_user, conv, agent_id=agent_name,
                )
        except Exception:  # noqa: BLE001 — extraction must never kill the relay
            _log.warning("agent.run_end_memory_extraction_failed",
                         thread_id=thread_id[:12])

    _actor = (getattr(user, "email", "") or "").strip()
    await _refuse_if_another_run_is_active(thread_id, _actor)

    _think_mode = _resolve_think_mode(req)

    # ── Tenant for this run's writes (WS-29 MT-1d / H4, slice 2 — DARK) ────────
    # The organization_id is taken from the SERVER-SIDE resolved identity
    # (``user.organization_id``, filled by ``_with_resolved_access`` from the
    # authenticated session) and passed explicitly into the executor, because the
    # detached run outlives this request scope and worker-thread writes cannot
    # read the request's ambient tenant binding. It MUST NEVER come from
    # ``req.payload`` / the event payload — that is agent/client-visible, so
    # sourcing the tenant from it is a tenant-spoofing hole (R11,
    # user_management_contract.md; §0.9.3). No DB write is converted this slice.
    _organization_id = getattr(user, "organization_id", None)

    agent_gen = run_agent_stream(
        agent_name,
        req.payload,
        run_id=run_id,
        thread_id=thread_id,
        model=req.model,
        think_mode=_think_mode,
        organization_id=_organization_id,
    )

    # The roster this run's authority intersection is folded over, recorded so a
    # mid-run steer can be checked against the floor the turn is actually
    # executing under (groups_sessions_authority.md §3). A solo thread records
    # nothing, so nothing about single-player behaviour changes.
    _floor = list(room.members) if (room is not None and room.is_shared) else None

    async def _serve():
        try:
            async for evt in run_detached(
                thread_id, agent_gen, tee=False,
                on_complete=_persist_on_complete,
                actor=_actor or None,
                source=str(req.payload.get("source") or "chat"),
                floor=_floor,
                # Server-side tenant only (see the run_agent_stream call above) —
                # never req.payload. Binds the detached drain task's own scope so
                # the on_complete persist hook sees the right tenant (R11).
                organization_id=_organization_id,
            ):
                yield f"data: {json.dumps(evt)}\n\n"
        except SupersedeRefused:
            # The inner §5.2 invariant fired: between routing this turn and
            # starting it, somebody else's run took the thread. Do NOT fall
            # through to the degraded direct-stream path below — that would run
            # the agent anyway, which is the destruction this guard exists to
            # prevent. Report it in-band; the client re-sends and routes to
            # steer on the second attempt.
            _log.warning(
                "agent.stream_supersede_refused",
                agent=agent_name, thread_id=thread_id[:12],
            )
            yield "data: " + json.dumps({
                "type": "RUN_ERROR",
                "message": (
                    "Another participant's run started on this conversation "
                    "while yours was starting. Nothing was discarded — send "
                    "again to add to it."
                ),
                "code": "run_in_progress",
            }) + "\n\n"
            return
        except Exception:  # noqa: BLE001
            from orchestrator.stream_relay import \
                get_detached_task  # noqa: PLC0415
            if get_detached_task(thread_id) is not None:
                # Drain task already owns the generator — losing the Redis
                # subscription mid-run must not double-consume it.  The run
                # continues in the background; the client can reconnect.
                _log.warning("agent.stream_subscribe_lost", agent=agent_name)
                return
            # Redis was unavailable from the start — degrade to direct
            # streaming with no relay (old behaviour).
            _log.warning("agent.stream_relay_unavailable", agent=agent_name)
            async for line in agent_gen:
                yield line

    return StreamingResponse(
        _serve(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.post(
    "/respond-input",
    summary="Answer a native ask_user prompt for a running agent",
)
async def respond_user_input(
    req: UserInputResponseRequest,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Resolve a pending ``ask_user`` request so the blocked agent resumes.

    The Copilot SDK's native ``ask_user`` tool blocks the agent turn on an
    ``on_user_input_request`` handler.  That handler emitted a
    ``user_input_requested`` SSE frame carrying a ``request_id`` and is now
    parked on a Future.  The frontend POSTs the user's answer here to unblock
    it — the agent continues in the SAME run/stream, so the answer is never
    queued as a separate chat message.
    """
    from orchestrator.executor import resolve_user_input  # noqa: PLC0415

    # Fast path: the run is parked on THIS worker — resolve its Future inline.
    delivered = resolve_user_input(
        req.request_id, req.answer, req.was_freeform
    )
    # Cross-worker (P1-2): the run may be parked on another worker.  Relay the
    # answer over the control bus so the owning worker resolves its own Future.
    if not delivered and req.thread_id:
        from orchestrator.stream_relay import dispatch_control  # noqa: PLC0415

        delivered = await dispatch_control(
            req.thread_id,
            {
                "cmd": "respond_input",
                "request_id": req.request_id,
                "answer": req.answer,
                "was_freeform": req.was_freeform,
            },
        )
    if not delivered:
        # The run may have ended or the request id is stale.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No pending question matches that request_id "
            "(the run may have already finished).",
        )
    _log.info("agent.user_input_resolved", request_id=req.request_id[:12])
    return {"ok": True}


@router.get(
    "/run/{thread_id}/reconnect",
    summary="Reconnect to a running (or recently finished) agent stream",
)
async def reconnect_agent_stream(
    thread_id: str,
    since: str = "0-0",
    user: UserContext = Depends(get_current_user),
) -> StreamingResponse:
    """Replay missed SSE events and subscribe to live ones.

    Called by the frontend after a page refresh or reconnect to catch up
    on everything the agent did while the browser was closed.  If the agent
    is still running, the stream continues with live events after replay.

    Query params:
        since:  Redis stream ID to replay FROM (exclusive).
                Default ``"0-0"`` replays everything.

    Returns ``text/event-stream`` with the same AG-UI event format as
    ``POST /agent/run/stream``.

    Falls back to an empty ``"done"`` event if the stream has expired.
    """
    import asyncio as _asyncio
    import json as _json

    from orchestrator.stream_relay import is_active  # noqa: PLC0415
    from orchestrator.stream_relay import (replay_events, stream_exists,
                                           subscribe_events)

    # Membership guard: the replayed stream contains the whole conversation —
    # text, tool args, reasoning — so it must never be readable by someone
    # outside the room. Participants and viewers may hold it open, which is how
    # a second person watches a run work; ephemeral threads (no session row)
    # stay reachable for the person who started them.
    actor = getattr(user, "email", None) or "default"
    if not await _asyncio.to_thread(_thread_owner_ok, thread_id, actor):
        raise HTTPException(status_code=403, detail="Not your conversation")

    _log.info(
        "agent.reconnect_request",
        thread_id=thread_id[:12],
        since=since[:20],
        actor=actor[:20],
    )

    async def _event_generator():
        # Phase 1: Replay missed events.
        # Local _stream_id values (e.g. "local-1718123456789-5#42") are minted
        # by the executor for the initial SSE stream (before the Redis entry ID
        # is known).  Redis XREAD doesn't understand them, so fall back to
        # "0-0".  Streams are reset per run (mark_active(reset=True)), so a
        # full replay covers exactly the current run, and the frontend clears
        # its partial message before replay (deltas have no id-dedup, so
        # re-appending would double text) — full replay is the SAFE default.
        #
        # P1-5 note: the trailing "#<n>" ordinal on a local cursor is a real
        # per-thread emit count (stream is reset per run; events push in
        # emission order), so a future client that PRESERVES its partial could
        # resume by skipping the first <n> entries. That optimisation is gated
        # on client-side delta de-duplication + a UI drive to prove no doubling
        # (see core_loop_unification §D1 follow-ups); the plumbing is in place.
        _since = since
        if _since.startswith("local-"):
            _since = "0-0"

        # Track the replay cursor so Phase 2 subscribes from the exact spot —
        # subscribing from "$" would silently drop any events pushed between
        # replay end and subscribe start.
        _cursor = _since
        if await stream_exists(thread_id):
            # Drain in batches until exhausted — a single 500-event read would
            # silently truncate the tail of long, tool-heavy runs on reconnect.
            while True:
                missed = await replay_events(thread_id, since_id=_cursor, count=500)
                if not missed:
                    break
                for evt in missed:
                    _eid = evt.get("_stream_id")
                    if _eid:
                        _cursor = _eid
                    yield f"data: {_json.dumps(evt)}\n\n"
                if len(missed) < 500:
                    break

        # Phase 2: If agent is still active, subscribe to live events from
        # the cursor (no gap with Phase 1).
        if await is_active(thread_id):
            async for evt in subscribe_events(thread_id, since_id=_cursor):
                yield f"data: {_json.dumps(evt)}\n\n"
        else:
            # Agent finished — emit RUN_FINISHED so the frontend translator
            # (translateAndPersistStream) maps it to {"type":"done"} and the
            # UI exits the reconnecting state.
            yield f"data: {_json.dumps({'type': 'RUN_FINISHED', 'runId': thread_id, 'threadId': thread_id})}\n\n"

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


def _thread_owner_ok(thread_id: str, user_id: str) -> bool:
    """True if *user_id* may WATCH *thread_id*, or it is not a persisted session.

    Ownership became membership when sessions became rooms (migration 138): a
    viewer invited to watch a run must be able to hold the replay stream open,
    which is the whole of read-only multiplayer.

    The two "we don't know" cases no longer answer the same way, and
    ``gateway.rooms`` owns that split, not this function. An **absent session
    row** (ephemeral / legacy thread) still resolves to True — it belongs to
    nobody, so it belongs to whoever is asking. A **failed lookup** now
    resolves to False: handing out read access on a room we cannot identify is
    exactly the wrong direction for the one input we know nothing about.

    The ``except`` here is not that case and is not dead code: the only thing
    inside the ``try`` that can still raise is the ``import`` — and an import
    failure means the rooms layer is absent altogether, i.e. the pre-138 world
    where every thread was solo and this guard did not exist. True is the right
    answer to that, and it cannot be reached by a database outage.

    The name is kept because it is the guard two endpoints already name; what
    it means is now "may read this room".
    """
    try:
        from gateway.rooms import resolve_room_access
        return resolve_room_access(thread_id, user_id).can_read
    except Exception:  # noqa: BLE001 — rooms layer absent (import), not a DB error
        return True


def _thread_control_ok(thread_id: str, user_id: str) -> bool:
    """True if *user_id* may STOP a run in *thread_id*.

    Watching and stopping are different rights: a viewer sees the transcript
    but cannot end somebody else's work. Same fallbacks as
    :func:`_thread_owner_ok`, with the same split: an unsaved thread is yours,
    a failed lookup is nobody's, and the ``except`` below only covers the rooms
    module failing to import.
    """
    try:
        from gateway.rooms import resolve_room_access
        return resolve_room_access(thread_id, user_id).can_cancel
    except Exception:  # noqa: BLE001 — rooms layer absent (import), not a DB error
        return True


@router.post(
    "/run/{thread_id}/cancel",
    summary="Cancel a running agent (actually stops backend execution)",
)
async def cancel_agent_run(
    thread_id: str,
    user: UserContext = Depends(get_current_user),
) -> dict:
    """Stop the in-flight agent run for *thread_id*.

    Unlike simply dropping the SSE connection (which leaves the agent running
    detached in the background, continuing to burn tokens and write files),
    this cancels the background task, marks the thread inactive, and pushes a
    terminal RUN_FINISHED event so any live/reconnecting subscribers close.

    Works for any runtime (MAF / Copilot SDK) because cancellation happens at
    the detached-task layer that wraps every agent generator.
    """
    import asyncio  # noqa: PLC0415

    from orchestrator.stream_relay import cancel_run  # noqa: PLC0415

    # Control guard: stopping a run is a contributor's act, not a viewer's.
    # A viewer who could cancel would be able to end work they were only
    # invited to watch. Allowed when the session is not found (ephemeral/legacy
    # thread — it belongs to nobody, so it belongs to the caller); REFUSED when
    # the lookup itself fails, because the room may be somebody else's and a
    # cancel we cannot justify ends real work. That failure surfaces as this
    # 403, so an outage can look like a permissions problem here — see
    # RoomAccess.resolve_failed, which carries the "try again" wording for the
    # paths that render a reason.
    actor = getattr(user, "email", None) or "default"
    if not await asyncio.to_thread(_thread_control_ok, thread_id, actor):
        raise HTTPException(
            status_code=403,
            detail="You are watching this conversation and cannot stop its run.",
        )

    _log.info(
        "agent.cancel_request",
        thread_id=thread_id[:12],
        actor=actor[:20],
    )
    cancelled = await cancel_run(thread_id)
    return {"ok": True, "cancelled": cancelled, "threadId": thread_id}


@router.post("/run", response_model=AgentRunResponse)
async def run_agent_sync(
    req: AgentRunRequest,
    user: UserContext = Depends(get_current_user),
) -> AgentRunResponse:
    """Synchronously run a named agent and return the final state.

    Use this for interactive queries where the caller can wait.
    For long-running background tasks prefer ``POST /agent/run/async``.
    """
    from orchestrator.executor import AgentRunError, run_agent  # noqa: PLC0415

    agent = _resolve_agent_for_run(req.agent, req.thread_id)
    await assert_can_run_agent_in_session(user, agent, req.thread_id)
    run_id = req.run_id or str(uuid.uuid4())

    try:
        final_state = await run_agent(
            agent,
            req.payload,
            run_id=run_id,
            thread_id=req.thread_id,
            model=req.model,
        )
        return AgentRunResponse(
            run_id=run_id,
            agent=agent,
            status="completed",
            result=final_state.get("result"),
        )
    except AgentRunError as exc:
        return AgentRunResponse(
            run_id=run_id,
            agent=agent,
            status="failed",
            error=str(exc.original),
            mutation_pr=exc.mutation_pr,
        )


@router.post("/run/async", status_code=status.HTTP_202_ACCEPTED)
async def run_agent_async(
    req: AgentRunRequest,
    background_tasks: BackgroundTasks,
    user: UserContext = Depends(get_current_user),
) -> dict[str, str]:
    """Enqueue an agent run and return ``run_id`` immediately (202 Accepted).

    The run executes as a FastAPI background task.  Poll
    ``GET /agent/run/{run_id}/status`` for progress.
    """
    from orchestrator.executor import run_agent  # noqa: PLC0415

    agent = _resolve_agent_for_run(req.agent, req.thread_id)
    await assert_can_run_agent_in_session(user, agent, req.thread_id)
    run_id = req.run_id or str(uuid.uuid4())

    async def _run() -> None:
        try:
            await run_agent(agent, req.payload, run_id=run_id,
                            thread_id=req.thread_id, model=req.model)
        except Exception as exc:  # noqa: BLE001
            _log.error(
                "agent.async_run_error",
                run_id=run_id,
                agent=agent,
                error=str(exc),
            )

    background_tasks.add_task(_run)
    return {"run_id": run_id, "status": "queued", "agent": agent}


@router.get("/run/{run_id}/status")
async def get_run_status(
    run_id: str,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Return the latest recorded status for a given run.

    Queries the audit_event table for agent_run_start / agent_run_complete events
    matching the run_id.  LangGraph PostgresSaver removed in WBS 0.7.
    """
    try:
        from acb_graph import get_session  # noqa: PLC0415
        from sqlalchemy import text  # noqa: PLC0415

        with get_session() as sess:
            result = sess.execute(
                text(
                    "SELECT action, at FROM audit_event "
                    "WHERE payload->>'run_id' = :run_id "
                    "ORDER BY at DESC LIMIT 10"
                ),
                {"run_id": run_id},
            )
            events = [{"action": r.action, "at": str(r.at)} for r in result]

        if not events:
            return {"run_id": run_id, "status": "not_found"}
        actions = {e["action"] for e in events}
        if "agent_run_complete" in actions:
            status_str = "completed"
        elif "agent_run_error" in actions:
            status_str = "failed"
        else:
            status_str = "running"
        return {"run_id": run_id, "status": status_str, "events": events}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/mutations")
async def list_mutations(
    limit: int = 50,
    user: UserContext = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """Return recent self-mutation events for the Control Plane HITL queue.

    Returns a merged view of:
    - ``pending_commit`` rows (commit-gate flow, M2.7) with full status info
    - ``audit_event`` rows from legacy sandbox failures (for observability)

    Items are sorted newest-first.
    """
    try:
        from acb_graph import get_session  # noqa: PLC0415
        from sqlalchemy import text  # noqa: PLC0415

        rows: list[dict[str, Any]] = []

        with get_session() as sess:
            # 1. Pending commits (primary HITL queue)
            pc_result = sess.execute(
                text(
                    "SELECT id, agent_name, run_id, commit_sha, commit_message, "
                    "       test_summary, status, reviewed_by, reviewed_at, created_at, "
                    "       mutation_mode, pr_url, target_path "
                    "FROM pending_commit "
                    "ORDER BY created_at DESC LIMIT :limit"
                ),
                {"limit": max(1, min(limit, 200))},
            )
            for r in pc_result:
                rows.append(
                    {
                        "type": "pending_commit",
                        "id": str(r.id),
                        "agent": r.agent_name,
                        "run_id": r.run_id,
                        "commit_sha": r.commit_sha,
                        "commit_message": r.commit_message,
                        "test_summary": r.test_summary,
                        "status": r.status,
                        "reviewed_by": r.reviewed_by,
                        "reviewed_at": str(r.reviewed_at) if r.reviewed_at else None,
                        "at": str(r.created_at),
                        # Native-MAF → monorepo PR fields (null for push-mode).
                        "mutation_mode": r.mutation_mode,
                        "pr_url": r.pr_url,
                        "target_path": r.target_path,
                        # approve / reject links for the Control Plane UI
                        "approve_url": f"/agent/mutations/pending/{r.id}/approve",
                        "reject_url": f"/agent/mutations/pending/{r.id}/reject",
                        "diff_url": f"/agent/mutations/pending/{r.id}/diff",
                    }
                )

            # 2. Legacy audit events (sandbox failures / older runs)
            ae_result = sess.execute(
                text(
                    "SELECT action, target, at, payload FROM audit_event "
                    "WHERE actor = 'system:mutation' "
                    "ORDER BY at DESC LIMIT :limit"
                ),
                {"limit": max(1, min(limit, 200))},
            )
            for r in ae_result:
                payload = r.payload if isinstance(r.payload, dict) else {}
                # Skip audit events that correspond to a pending_commit row
                # already in the list above (they share the run_id)
                rows.append(
                    {
                        "type": "audit_event",
                        "agent": str(r.target).removeprefix("agent:"),
                        "at": str(r.at),
                        "run_id": payload.get("run_id"),
                        "commit_sha": payload.get("commit_sha"),
                        "pending_commit_id": payload.get("pending_commit_id"),
                        "test_summary": payload.get("test_summary"),
                        "status": (
                            "commit_pending"
                            if r.action == "mutation_commit_pending"
                            else "commit_pending"
                            if r.action == "mutation_eval_failed"
                            else "failed"
                            if r.action == "mutation_sandbox_failed"
                            else "started"
                            if r.action == "mutation_start"
                            else r.action
                        ),
                    }
                )

        # Sort by timestamp descending (pending_commit.created_at, audit_event.at)
        rows.sort(key=lambda x: x.get("at", ""), reverse=True)
        return rows[:limit]
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Pending commit HITL endpoints (commit-gate flow — M2.7)
# ---------------------------------------------------------------------------# ---------------------------------------------------------------------------
# Pending commit HITL endpoints (commit-gate flow — M2.7)
# ---------------------------------------------------------------------------

@router.get("/mutations/pending")
async def list_pending_commits(
    limit: int = 50,
    user: UserContext = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """Return pending commit rows for the inbox (unreviewed agent self-fixes)."""
    try:
        from acb_graph import get_session  # noqa: PLC0415
        from sqlalchemy import text  # noqa: PLC0415

        with get_session() as sess:
            result = sess.execute(
                text(
                    "SELECT id, agent_name, run_id, commit_sha, commit_message, "
                    "       test_summary, status, reviewed_by, reviewed_at, created_at, "
                    "       mutation_mode, pr_url, target_path "
                    "FROM pending_commit "
                    "ORDER BY created_at DESC LIMIT :limit"
                ),
                {"limit": max(1, min(limit, 200))},
            )
            rows = []
            for r in result:
                rows.append(
                    {
                        "id": str(r.id),
                        "agent_name": r.agent_name,
                        "run_id": r.run_id,
                        "commit_sha": r.commit_sha,
                        "commit_message": r.commit_message,
                        "test_summary": r.test_summary,
                        "status": r.status,
                        "reviewed_by": r.reviewed_by,
                        "reviewed_at": str(r.reviewed_at) if r.reviewed_at else None,
                        "created_at": str(r.created_at),
                        "mutation_mode": r.mutation_mode,
                        "pr_url": r.pr_url,
                        "target_path": r.target_path,
                    }
                )
        return rows
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/mutations/pending/{commit_id}/diff")
async def get_pending_commit_diff(
    commit_id: str,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Return the unified diff stored for a pending commit (for inline review)."""
    try:
        from acb_graph import get_session  # noqa: PLC0415
        from sqlalchemy import text  # noqa: PLC0415

        with get_session() as sess:
            result = sess.execute(
                text(
                    "SELECT id, agent_name, commit_sha, commit_message, "
                    "       diff_text, test_summary, status FROM pending_commit "
                    "WHERE id = :id"
                ),
                {"id": commit_id},
            )
            row = result.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="pending commit not found")
        return {
            "id": str(row.id),
            "agent_name": row.agent_name,
            "commit_sha": row.commit_sha,
            "commit_message": row.commit_message,
            "diff_text": row.diff_text,
            "test_summary": row.test_summary,
            "status": row.status,
        }
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post(
    "/mutations/pending/{commit_id}/approve", status_code=200,
    dependencies=[Depends(require_internal_auth)],
)
async def approve_pending_commit(
    commit_id: str,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Approve a pending commit: push it to origin/HEAD from the local clone.

    Pushes the commit that the mutation sandbox staged locally.  On success
    the row status is set to ``approved``.  Merge conflicts are resolved
    automatically by ``git push --force-with-lease`` from the authenticated
    clone (the sandbox always commits on top of the current HEAD; conflicts
    would only arise if another push landed between sandbox commit and approval,
    in which case we rebase and push).
    """
    import asyncio  # noqa: PLC0415

    from acb_audit import AuditEvent, record  # noqa: PLC0415

    try:
        from acb_graph import get_session  # noqa: PLC0415
        from sqlalchemy import text  # noqa: PLC0415

        # Fetch the row
        with get_session() as sess:
            result = sess.execute(
                text(
                    "SELECT id, agent_name, run_id, local_clone_dir, commit_sha, "
                    "       commit_message, status FROM pending_commit WHERE id = :id"
                ),
                {"id": commit_id},
            )
            row = result.fetchone()

        if row is None:
            raise HTTPException(status_code=404, detail="pending commit not found")
        if row.status not in ("pending", "eval_failed"):
            raise HTTPException(
                status_code=409,
                detail=f"commit is already {row.status}",
            )

        commit_sha: str = row.commit_sha
        clone_dir: str = row.local_clone_dir

        # Verify the commit exists in the local clone before trying to push.
        verify = await asyncio.create_subprocess_exec(
            "git", "cat-file", "-t", commit_sha,
            cwd=clone_dir,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await verify.communicate()
        if verify.returncode != 0:
            raise HTTPException(
                status_code=422,
                detail=f"commit {commit_sha[:8]} not found in local clone at {clone_dir}",
            )

        # Find the local branch that contains the commit and check it out.
        # This avoids a detached HEAD state which breaks `git push origin HEAD`.
        # Push only up to this specific commit (not the full branch tip) so that
        # approving an earlier commit in a chain doesn't push unapproved later ones.
        branch_proc = await asyncio.create_subprocess_exec(
            "git", "branch", "--contains", commit_sha, "--format=%(refname:short)",
            cwd=clone_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        branch_out, _ = await branch_proc.communicate()
        local_branch = branch_out.decode(errors="replace").strip().splitlines()[0].strip() if branch_out else "main"
        if not local_branch:
            local_branch = "main"
        await _git_exec(clone_dir, ["checkout", local_branch])

        # Detect local-only repos (no remote origin).  For these, approval
        # simply keeps the commit — there is no remote to push to.
        remote_proc = await asyncio.create_subprocess_exec(
            "git", "remote", "get-url", "origin",
            cwd=clone_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await remote_proc.communicate()
        has_remote = remote_proc.returncode == 0

        reviewer = getattr(user, "sub", None) or getattr(user, "email", "unknown")
        new_sha: str | None = None
        if has_remote:
            # Push.  If the fast-forward fails, rebase and retry.
            push_ok, new_sha = await _git_push_with_rebase(clone_dir, commit_sha)
            if not push_ok:
                raise HTTPException(
                    status_code=500,
                    detail="git push failed after rebase — check gateway logs",
                )
            # If rebase changed the commit SHA, update it in the DB so the
            # row stays consistent (diff URL, cascade, and future audits
            # all reference this SHA).
            effective_sha = new_sha or commit_sha
            _log.info("mutation.commit_pushed",
                      agent=row.agent_name, commit_sha=effective_sha[:8])
        else:
            # No git remote — a NATIVE MAF agent. Instead of the old no-op
            # "kept local" (which the next deploy re-seed would clobber), open a
            # PR against the Metorite monorepo so the fix lands in the
            # agent's source (apps/agents/agent-<name>/) and becomes durable.
            from gateway.routes.monorepo_pr import (  # noqa: PLC0415
                MonorepoPRError,
                monorepo_pr_configured,
                open_monorepo_pr,
            )

            if monorepo_pr_configured():
                try:
                    pr = await open_monorepo_pr(
                        agent_name=row.agent_name,
                        clone_dir=clone_dir,
                        commit_sha=commit_sha,
                        commit_message=row.commit_message,
                    )
                except MonorepoPRError as pr_exc:
                    raise HTTPException(
                        status_code=502,
                        detail=f"Could not open monorepo PR: {pr_exc}",
                    ) from pr_exc

                # PR is open — mark the row and return. This path does NOT use
                # the git-push cascade below (there is no shared remote history
                # to walk); each approved native-MAF mutation is its own PR.
                with get_session() as sess:
                    sess.execute(
                        text(
                            "UPDATE pending_commit SET status = 'pr_open', "
                            "mutation_mode = 'monorepo_pr', pr_url = :pr_url, "
                            "target_path = :tp, reviewed_by = :by, reviewed_at = now() "
                            "WHERE id = :id"
                        ),
                        {
                            "pr_url": pr.pr_url,
                            "tp": pr.target_path,
                            "by": reviewer,
                            "id": commit_id,
                        },
                    )
                    sess.commit()
                record(
                    AuditEvent(
                        actor=f"human:{reviewer}",
                        action="mutation_monorepo_pr_opened",
                        target=f"agent:{row.agent_name}",
                        payload={
                            "pending_commit_id": commit_id,
                            "commit_sha": commit_sha,
                            "pr_url": pr.pr_url,
                            "target_path": pr.target_path,
                        },
                    )
                )
                _log.info(
                    "mutation.monorepo_pr_opened",
                    agent=row.agent_name,
                    commit_sha=commit_sha[:8],
                    pr_url=pr.pr_url,
                )
                return {
                    "status": "pr_open",
                    "commit_sha": commit_sha,
                    "pr_url": pr.pr_url,
                    "target_path": pr.target_path,
                }

            # Monorepo-PR not configured — fall back to the legacy keep-local
            # behaviour (durable only while the volume survives).
            effective_sha = commit_sha
            _log.info(
                "mutation.commit_kept_local",
                agent=row.agent_name,
                commit_sha=effective_sha[:8],
                hint="Local-only repo, monorepo PR not configured. Commit kept.",
            )

        # (reviewer resolved above, before the push/PR fork.)
        with get_session() as sess:
            update_sql = (
                "UPDATE pending_commit "
                "SET status = 'approved', reviewed_by = :by, reviewed_at = now()"
            )
            params: dict = {"id": commit_id, "by": reviewer}
            if new_sha and new_sha != commit_sha:
                update_sql += ", commit_sha = :new_sha"
                params["new_sha"] = new_sha
            update_sql += " WHERE id = :id"
            sess.execute(text(update_sql), params)
            sess.commit()

        # ── Cascade: auto-approve any other pending commits for the same agent
        # that are ancestors of the approved commit.  These were all pushed to
        # the remote as part of the same push (git sends all commits between
        # origin/<branch> and HEAD in one push).
        cascade_ids: list[str] = []
        try:
            with get_session() as sess:
                others = sess.execute(
                    text(
                        "SELECT id, commit_sha FROM pending_commit "
                        "WHERE agent_name = :agent AND id != :id "
                        "AND status IN ('pending', 'eval_failed')"
                    ),
                    {"agent": row.agent_name, "id": commit_id},
                ).fetchall()

            for other in others:
                # is_ancestor returns 0 when other_sha IS an ancestor
                # of the effective (post-rebase) SHA
                anc_proc = await asyncio.create_subprocess_exec(
                    "git", "merge-base", "--is-ancestor",
                    other.commit_sha, effective_sha,
                    cwd=clone_dir,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await anc_proc.communicate()
                if anc_proc.returncode == 0:
                    cascade_ids.append(str(other.id))

            if cascade_ids:
                with get_session() as sess:
                    sess.execute(
                        text(
                            "UPDATE pending_commit "
                            "SET status = 'approved',"
                            " reviewed_by = :by, reviewed_at = now() "
                            "WHERE id = ANY(:ids)"
                        ),
                        {"by": f"cascade:{reviewer}", "ids": cascade_ids},
                    )
                    sess.commit()
                _log.info(
                    "mutation.cascade_approved",
                    agent=row.agent_name,
                    approved_sha=effective_sha,
                    cascade_count=len(cascade_ids),
                )
        except Exception as _cascade_exc:  # noqa: BLE001
            # Non-fatal — the primary commit is already approved
            _log.warning("mutation.cascade_failed", error=str(_cascade_exc))

        record(
            AuditEvent(
                actor=f"human:{reviewer}",
                action="mutation_commit_approved",
                target=f"agent:{row.agent_name}",
                payload={
                    "pending_commit_id": commit_id,
                    "commit_sha": effective_sha,
                    "run_id": row.run_id,
                },
            )
        )
        _log.info(
            "mutation.commit_approved",
            agent=row.agent_name,
            commit_sha=effective_sha,
            reviewer=reviewer,
            cascade=len(cascade_ids),
        )
        return {
            "status": "approved",
            "commit_sha": effective_sha,
            "cascade_approved": len(cascade_ids),
        }

    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post(
    "/mutations/pending/{commit_id}/reject", status_code=200,
    dependencies=[Depends(require_internal_auth)],
)
async def reject_pending_commit(
    commit_id: str,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Reject a pending commit: drop it from the local clone with git reset HEAD~1.

    The commit is undone locally so the clone is clean for the next mutation
    attempt.  The row status is set to ``rejected``.
    """
    import asyncio  # noqa: PLC0415

    from acb_audit import AuditEvent, record  # noqa: PLC0415

    try:
        from acb_graph import get_session  # noqa: PLC0415
        from sqlalchemy import text  # noqa: PLC0415

        with get_session() as sess:
            result = sess.execute(
                text(
                    "SELECT id, agent_name, run_id, local_clone_dir, commit_sha, status "
                    "FROM pending_commit WHERE id = :id"
                ),
                {"id": commit_id},
            )
            row = result.fetchone()

        if row is None:
            raise HTTPException(status_code=404, detail="pending commit not found")
        if row.status not in ("pending", "eval_failed"):
            raise HTTPException(status_code=409, detail=f"commit is already {row.status}")

        clone_dir: str = row.local_clone_dir
        commit_sha: str = row.commit_sha

        # Reset HEAD~1 only if HEAD is still the mutation commit (safety check)
        head_proc = await asyncio.create_subprocess_exec(
            "git", "rev-parse", "HEAD",
            cwd=clone_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        head_out, _ = await head_proc.communicate()
        head_sha = head_out.decode().strip()
        if head_sha == commit_sha:
            await _git_exec(clone_dir, ["reset", "HEAD~1", "--mixed"])

        reviewer = getattr(user, "sub", None) or getattr(user, "email", "unknown")
        with get_session() as sess:
            sess.execute(
                text(
                    "UPDATE pending_commit "
                    "SET status = 'rejected', reviewed_by = :by, reviewed_at = now() "
                    "WHERE id = :id"
                ),
                {"id": commit_id, "by": reviewer},
            )
            sess.commit()

        record(
            AuditEvent(
                actor=f"human:{reviewer}",
                action="mutation_commit_rejected",
                target=f"agent:{row.agent_name}",
                payload={
                    "pending_commit_id": commit_id,
                    "commit_sha": commit_sha,
                    "run_id": row.run_id,
                },
            )
        )
        _log.info(
            "mutation.commit_rejected",
            agent=row.agent_name,
            commit_sha=commit_sha,
            reviewer=reviewer,
        )
        return {"status": "rejected", "commit_sha": commit_sha}

    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post(
    "/mutations/pending/{commit_id}/remutate", status_code=200,
    dependencies=[Depends(require_internal_auth)],
)
async def remutate_pending_commit(
    commit_id: str,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Re-mutate an eval_failed commit: reset the local clone and clear the row.

    This is the "try again" action for commits where tests failed.  It:
    1. Resets the local clone with ``git reset HEAD~1 --mixed`` so the
       working tree is clean for a fresh mutation attempt.
    2. Marks the row as ``rejected`` (with reviewed_by = 'system:remutate').

    The next time the same agent fails at runtime a new mutation will be
    triggered automatically (max_mutation_attempts = 1 per *run*, so a fresh
    run resets the counter).  To manually trigger, re-run the agent from chat.
    """
    import asyncio  # noqa: PLC0415

    from acb_audit import AuditEvent, record  # noqa: PLC0415

    try:
        from acb_graph import get_session  # noqa: PLC0415
        from sqlalchemy import text  # noqa: PLC0415

        with get_session() as sess:
            result = sess.execute(
                text(
                    "SELECT id, agent_name, run_id, local_clone_dir, commit_sha, status "
                    "FROM pending_commit WHERE id = :id"
                ),
                {"id": commit_id},
            )
            row = result.fetchone()

        if row is None:
            raise HTTPException(status_code=404, detail="pending commit not found")
        if row.status not in ("eval_failed", "pending"):
            raise HTTPException(status_code=409, detail=f"commit is already {row.status}")

        clone_dir: str = row.local_clone_dir
        commit_sha: str = row.commit_sha

        # Only reset if HEAD is still the mutation commit (safety check)
        head_proc = await asyncio.create_subprocess_exec(
            "git", "rev-parse", "HEAD",
            cwd=clone_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        head_out, _ = await head_proc.communicate()
        head_sha = head_out.decode().strip()
        if head_sha == commit_sha:
            await _git_exec(clone_dir, ["reset", "HEAD~1", "--mixed"])

        reviewer = getattr(user, "sub", None) or getattr(user, "email", "unknown")
        with get_session() as sess:
            sess.execute(
                text(
                    "UPDATE pending_commit "
                    "SET status = 'rejected', reviewed_by = 'system:remutate', reviewed_at = now() "
                    "WHERE id = :id"
                ),
                {"id": commit_id},
            )
            sess.commit()

        record(
            AuditEvent(
                actor=f"human:{reviewer}",
                action="mutation_commit_remutate_requested",
                target=f"agent:{row.agent_name}",
                payload={
                    "pending_commit_id": commit_id,
                    "commit_sha": commit_sha,
                    "run_id": row.run_id,
                },
            )
        )
        _log.info(
            "mutation.remutate_requested",
            agent=row.agent_name,
            commit_sha=commit_sha,
            by=reviewer,
        )
        return {
            "status": "reset",
            "commit_sha": commit_sha,
            "message": (
                "Commit cleared from local clone. "
                "Trigger a fresh agent run to attempt a new fix."
            ),
        }

    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.delete(
    "/mutations/pending/{commit_id}", status_code=200,
    dependencies=[Depends(require_internal_auth)],
)
async def delete_pending_commit(
    commit_id: str,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Delete a pending_commit row regardless of status.

    Used by the UI X button to clear any commit entry (pending, approved,
    rejected, failed) from the agent card view.
    """
    try:
        from acb_graph import get_session  # noqa: PLC0415
        from sqlalchemy import text  # noqa: PLC0415

        reviewer = getattr(user, "sub", None) or getattr(user, "email", "unknown")
        with get_session() as sess:
            result = sess.execute(
                text("DELETE FROM pending_commit WHERE id = :id"),
                {"id": commit_id},
            )
            deleted = result.rowcount
            sess.commit()

        _log.info("mutation.commit_deleted", commit_id=commit_id, rows=deleted, by=reviewer)
        return {"deleted": commit_id, "rows_deleted": deleted}

    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.delete(
    "/mutations/audit/{run_id}", status_code=200,
    dependencies=[Depends(require_internal_auth)],
)
async def dismiss_mutation_event(
    run_id: str,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Remove failed mutation audit_event rows for a given run_id from the inbox.

    Only affects rows where actor = 'system:mutation' so human-created audit
    records are never touched.  Non-fatal if run_id not found.
    """
    try:
        from acb_graph import get_session  # noqa: PLC0415
        from sqlalchemy import text  # noqa: PLC0415

        reviewer = getattr(user, "sub", None) or getattr(user, "email", "unknown")
        with get_session() as sess:
            result = sess.execute(
                text(
                    "DELETE FROM audit_event "
                    "WHERE actor = 'system:mutation' "
                    "AND payload->>'run_id' = :run_id"
                ),
                {"run_id": run_id},
            )
            deleted = result.rowcount
            sess.commit()

        _log.info("mutation.audit_dismissed", run_id=run_id, rows=deleted, by=reviewer)
        return {"dismissed": run_id, "rows_deleted": deleted}

    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Git helpers for the approve endpoint
# ---------------------------------------------------------------------------

async def _git_behind_count(clone_dir: str) -> int:
    """Return how many commits the local clone is behind origin/HEAD.

    Runs ``git fetch`` first (5 s timeout) to get the latest remote state,
    then ``git rev-list --count HEAD..origin/HEAD``.

    Returns 0 on any error (clone missing, no remote, fetch timeout, etc.)
    so the UI never breaks on a stale count.
    """
    import asyncio  # noqa: PLC0415

    # Quick check: does this clone even have a remote?
    rc = await _git_exec(clone_dir, ["remote", "get-url", "origin"])
    if rc != 0:
        return 0

    # Fetch latest (short timeout — non-blocking for the UI)
    try:
        fetch_proc = await asyncio.create_subprocess_exec(
            "git", "fetch", "origin",
            cwd=clone_dir,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(fetch_proc.communicate(), timeout=5)
    except (TimeoutError, Exception):
        return 0  # fetch timed out — return 0, don't block the response

    # Count commits we're behind
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "rev-list", "--count", "HEAD..origin/HEAD",
            cwd=clone_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        if proc.returncode == 0:
            return int(out.decode(errors="replace").strip() or "0")
    except (ValueError, TimeoutError, Exception):
        pass

    return 0


async def _git_exec(cwd: str, args: list[str]) -> int:
    """Run a git command, return the return code."""
    import asyncio  # noqa: PLC0415

    proc = await asyncio.create_subprocess_exec(
        "git", *args,
        cwd=cwd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.communicate()
    return proc.returncode


async def _git_push_with_rebase(
    clone_dir: str, commit_sha: str | None = None,
) -> tuple[bool, str | None]:
    """Push a specific commit (or HEAD) to the remote default branch.

    ``commit_sha``: if given, push exactly this commit — not the full branch
    tip.  This ensures approving commit A in a chain A→B→C only pushes A,
    not B or C (which may not yet be approved).  When approving the tip commit
    all ancestors are pushed automatically by git.

    Returns ``(success, new_commit_sha)``.  ``new_commit_sha`` is the HEAD SHA
    after rebase (may differ from the input if rebase was needed).  Callers
    MUST update ``pending_commit.commit_sha`` when this changes.
    """
    import asyncio  # noqa: PLC0415

    # Discover the remote's default branch (HEAD branch from
    # `git remote show origin`).  Falls back to "master" then "main".
    remote_branch = "master"
    try:
        rb_proc = await asyncio.create_subprocess_exec(
            "git", "remote", "show", "origin",
            cwd=clone_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        rb_out, _ = await asyncio.wait_for(rb_proc.communicate(), timeout=15)
        for line in rb_out.decode(errors="replace").splitlines():
            line = line.strip()
            if line.startswith("HEAD branch:"):
                remote_branch = line.split("HEAD branch:", 1)[1].strip()
                break
    except Exception:  # noqa: BLE001
        pass

    # Push <commit_sha>:<remote_branch> (or HEAD if no specific sha given)
    push_src = commit_sha if commit_sha else "HEAD"
    push_target = f"{push_src}:{remote_branch}"

    # First attempt — fast-forward.
    # --no-verify bypasses the pre-push hook (which blocks agent pushes
    # but also accidentally blocks the legitimate approval push).
    proc = await asyncio.create_subprocess_exec(
        "git", "push", "--no-verify", "origin", push_target,
        cwd=clone_dir,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=60)
    if proc.returncode == 0:
        return True, commit_sha  # SHA unchanged on clean fast-forward

    stderr = stderr_bytes.decode(errors="replace")
    _log.warning(
        "mutation.push_rejected",
        remote_branch=remote_branch, reason=stderr[:300],
    )

    # Fetch + rebase on top of the remote branch, then retry.
    fetch_rc = await _git_exec(clone_dir, ["fetch", "origin"])
    if fetch_rc != 0:
        return False, None

    # ── Stash uncommitted changes so rebase has a clean tree ──────────
    # Agent runs often leave modified files (memory DBs, outputs, etc.)
    # that block git rebase.  Stash them, rebase, then pop.
    stash_rc = await _git_exec(clone_dir, [
        "stash", "--include-untracked",
        "-m", "metorite-approve-auto-stash",
    ])
    stashed = stash_rc == 0

    rebase_ok = False
    try:
        rebase_proc = await asyncio.create_subprocess_exec(
            "git", "rebase", f"origin/{remote_branch}",
            cwd=clone_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**__import__("os").environ,
                 "GIT_SEQUENCE_EDITOR": "true"},
        )
        _, rebase_err = await asyncio.wait_for(
            rebase_proc.communicate(), timeout=60,
        )
        if rebase_proc.returncode == 0:
            rebase_ok = True
        else:
            # Rebase hit merge conflicts — auto-resolve with ours
            _log.warning(
                "mutation.rebase_conflict",
                hint="auto-resolving with checkout --ours",
                stderr=rebase_err.decode(errors="replace")[:200],
            )
            await _git_exec(clone_dir, ["checkout", "--ours", "."])
            await _git_exec(clone_dir, ["add", "-A"])
            rc2 = await _git_exec(
                clone_dir, ["rebase", "--continue"],
            )
            if rc2 == 0:
                rebase_ok = True
            else:
                # Rebase is broken — abort and let the caller handle
                await _git_exec(clone_dir, ["rebase", "--abort"])
                _log.error(
                    "mutation.rebase_failed",
                    hint="Could not rebase even after conflict resolution.",
                )
    finally:
        # Pop the stash regardless of rebase outcome
        if stashed:
            await _git_exec(clone_dir, ["stash", "pop"])

    if not rebase_ok:
        return False, None

    # After rebase the SHA of our commit changes — read the new HEAD SHA
    # so the caller can update the pending_commit row.
    new_sha: str | None = None
    try:
        sha_proc = await asyncio.create_subprocess_exec(
            "git", "rev-parse", "HEAD",
            cwd=clone_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        sha_out, _ = await asyncio.wait_for(sha_proc.communicate(), timeout=10)
        if sha_proc.returncode == 0:
            new_sha = sha_out.decode(errors="replace").strip()
            _log.info(
                "mutation.rebase_new_sha",
                old_sha=(commit_sha or "HEAD")[:8],
                new_sha=new_sha[:8],
            )
    except Exception:  # noqa: BLE001
        pass

    # Push the new HEAD (which includes our rebased commit)
    new_head = new_sha if new_sha else "HEAD"
    push_target_retry = f"{new_head}:{remote_branch}"

    retry_proc = await asyncio.create_subprocess_exec(
        "git", "push", "--no-verify", "origin", push_target_retry,
        cwd=clone_dir,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await asyncio.wait_for(retry_proc.communicate(), timeout=60)
    success = retry_proc.returncode == 0
    return success, new_sha if success else None


_SIGNATURE_HEADER = "X-CC-Signature"


def _webhook_secret(source: str) -> str:
    """Shared secret for signing ``/agent/webhook/{source}``.

    Per-source (``AGENT_WEBHOOK_SECRET_<SOURCE>``) wins over the global
    ``AGENT_WEBHOOK_SECRET``, so one leaked integration does not authorise
    every agent on the platform.
    """
    slug = "".join(c if c.isalnum() else "_" for c in source).upper()
    return (
        os.environ.get(f"AGENT_WEBHOOK_SECRET_{slug}", "").strip()
        or os.environ.get("AGENT_WEBHOOK_SECRET", "").strip()
    )


def verify_webhook_signature(source: str, body: bytes, presented: str | None) -> None:
    """HMAC-SHA256 over the raw body, or raise.

    Unlike the bridge/bot-token checks elsewhere in the gateway, this one
    **fails closed when unconfigured**. Those endpoints receive data; this one
    *starts an agent run* — arbitrary tool use against real systems — and it is
    reachable from the internet through Caddy. An unconfigured deployment
    should not have that endpoint open, so a missing secret is a 503 naming
    the fix rather than an implicit allow.

    Safe to make strict: the audit found nothing calls this path today
    (`FOUNDATION_AUDIT_REPORT.md` — the provider receivers live in
    `ingestion/sources/*/webhook.py` and dispatch no agents), so there is no
    working sender to break.
    """
    secret = _webhook_secret(source)
    if not secret:
        raise HTTPException(
            status_code=503,
            detail=(
                "Agent webhooks are not configured. Set AGENT_WEBHOOK_SECRET "
                f"(or AGENT_WEBHOOK_SECRET_{source.upper()}) and sign the "
                f"request body with HMAC-SHA256 in {_SIGNATURE_HEADER}."
            ),
        )

    supplied = (presented or "").strip()
    if supplied.startswith("sha256="):
        supplied = supplied[len("sha256="):]
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not supplied or not hmac.compare_digest(expected, supplied):
        raise HTTPException(status_code=401, detail="Invalid webhook signature.")


@router.post("/webhook/{source}", status_code=status.HTTP_202_ACCEPTED)
async def receive_webhook(
    source: str,
    event: WebhookEvent,
    background_tasks: BackgroundTasks,
    request: Request,
    x_cc_signature: str | None = Header(default=None, alias=_SIGNATURE_HEADER),
) -> dict[str, Any]:
    """Receive a signed webhook from an external source and route it to an agent.

    **Authenticated by HMAC signature** (BO-2 residual #3). This endpoint
    dispatches an agent run, so leaving it open made it a fourth run path that
    bypassed ``assert_can_run_agent`` entirely — anyone who could reach the
    gateway could trigger any agent with any payload.

    A valid signature IS the authorization here: it proves the platform's own
    secret produced the request, which is the same trust level as the internal
    service token. There is no member to resolve, so no per-agent check
    applies — which is exactly why the signature has to be mandatory.

    Routing logic:
    1. Look up (source, event_type) in ``_WEBHOOK_ROUTES`` for static MAF agents.
    2. If not found there, scan the dynamic agent registry for a route with
       a matching ``webhook_routes`` entry.
    3. Dispatch to the MAF executor (the sole agent execution runtime; the
       Copilot SDK is used only for self-mutation containers).
    """
    verify_webhook_signature(source, await request.body(), x_cc_signature)

    agent_name: str | None = _WEBHOOK_ROUTES.get((source, event.event_type))
    agent_runtime = "maf"  # default for static routes — MAF executor (WBS 0.7)

    # If the static table had no match, check dynamic agents for a webhook route
    if not agent_name:
        for dyn in _load_dynamic_agents():
            for route in dyn.get("webhook_routes", []):
                if route.get("source") == source and route.get("event_type") == event.event_type:
                    agent_name = dyn["name"]
                    agent_runtime = dyn.get("agent_runtime", "maf")
                    break
            if agent_name:
                break

    # Workflows can bind to the same (source, event_type) events (workflow
    # event triggers — routes/workflows/triggers.py). Dispatch is best-effort
    # and independent of agent routing: the same event may fan out to both.
    workflow_runs: list[dict[str, str]] = []
    try:
        from gateway.routes.workflows.triggers import dispatch_event  # noqa: PLC0415

        workflow_runs = await dispatch_event(source, event.event_type, event.payload)
    except Exception as exc:  # noqa: BLE001
        _log.warning("webhook.workflow_dispatch_failed", error=str(exc)[:160])

    if not agent_name:
        if workflow_runs:
            _log.info(
                "webhook.workflow_routed",
                source=source,
                event_type=event.event_type,
                runs=len(workflow_runs),
            )
            return {
                "status": "workflow_routed",
                "source": source,
                "event_type": event.event_type,
                "workflow_runs": workflow_runs,
            }
        _log.warning(
            "webhook.no_route",
            source=source,
            event_type=event.event_type,
        )
        return {
            "status": "no_route",
            "source": source,
            "event_type": event.event_type,
            "known_routes": [f"{s}/{et}" for s, et in _WEBHOOK_ROUTES],
        }

    run_id = str(uuid.uuid4())

    # MAF is the sole agent execution runtime (Copilot SDK is mutation-only).
    from orchestrator.executor import run_agent  # noqa: PLC0415

    async def _run() -> None:
        try:
            await run_agent(
                agent_name,
                {"source": source, "event_type": event.event_type, **event.payload},
                run_id=run_id,
            )
        except Exception as exc:  # noqa: BLE001
            _log.error(
                "webhook.agent_error",
                run_id=run_id,
                agent=agent_name,
                error=str(exc),
            )

    background_tasks.add_task(_run)
    _log.info(
        "webhook.routed",
        source=source,
        event_type=event.event_type,
        agent=agent_name,
        run_id=run_id,
    )

    return {
        "status": "queued",
        "run_id": run_id,
        "agent": agent_name,
        "runtime": agent_runtime,
        "workflow_runs": workflow_runs,
    }
