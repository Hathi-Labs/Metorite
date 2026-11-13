"""Workflows routes — shared kernel (spec: project-docs/specs/workflows_app.md).

The shared ``router``, DB infrastructure, and the small helpers every feature
module uses. Mirrors ``routes/apps/_common.py`` / ``routes/tasks/core.py``
(the leaf module: it imports nothing from siblings outside ``engine/``).

Canonical store: the ``workflows`` / ``workflow_*`` tables from
``infra/postgres/132_workflows.sql``. The whole prefix is feature-gated on
``workflows``; the single exemption is the inbound webhook trigger, which
authenticates itself by its unguessable per-workflow token (+ optional HMAC)
— see ``hooks.py`` and the matching entry in ``main.PUBLIC_ROUTES``.

**The authoring gate (STU-J1-3, ``ai_studio.md`` §14 J1-3).** A workflow write
needs ``feature:studio`` on top of the router gate. A write is a create, a
duplicate, an edit, a delete, a Copilot turn, or a test run of the draft. The
route table and the class of each route are in
``tests/unit/test_workflows_author_gate.py``. ``AUTHOR_GATE`` is the route
dependency, and ``check_author`` is the same check for the draft branch of
``POST /{id}/run``. The check is the seam's own ``require_feature("studio")``.
It applies only where ``WORKFLOW_STUDIO_GATE_ORGS`` holds the org (default
empty, so the rule before J1-3 holds until the flip).
"""

from __future__ import annotations

import functools
import json
import secrets
from typing import Annotated, Any

from acb_auth import UserContext, get_current_user, require_feature, require_feature_router
from acb_common import get_logger, get_settings
from fastapi import APIRouter, Depends, HTTPException

# The shared gateway engine (BO-10) — see the DB section below.
from gateway.db import get_db as _get_db  # noqa: F401
from gateway.db import get_session_factory as _get_session_factory  # noqa: F401

# The tenant-bound half of the seam (MT-1c / H2). `_tenant_session` IS
# `acb_common.db.tenant_session`, aliased per-package exactly like
# `routes/projects/core.py`: member-facing submodules import it from here BY
# NAME, and the hermetic tests patch it on the module under test. The tenant
# comes from the request context (bound in `_with_resolved_access`); a call
# outside a bound request raises `TenantUnbound` — fail closed, never "the
# usual org".
from gateway.db import tenant_session as _tenant_session  # noqa: F401
from sqlalchemy import text

_log = get_logger("gateway.workflows")

#: Route templates exempt from the feature gate (self-authenticating).
EXEMPT_ROUTES = ("/workflows/hooks/{hook_token}",)

router = APIRouter(
    prefix="/workflows",
    tags=["workflows"],
    dependencies=[require_feature_router("workflows", exempt=EXEMPT_ROUTES)],
)

# ── The authoring gate (STU-J1-3) ────────────────────────────────────────────
#
# The router gate stays `feature:workflows` on every route: it is the surface
# gate, and a person who cannot read workflows does not write them. A write
# ADDS `feature:studio`. So the gate of a write is both features. A deny of
# either one refuses it, and the router gate answers first. A read, a run of
# the published version, a module route and the four live routes (publish,
# rollback, disable, enable) add nothing here (owner rules 1 and 2, task 1818).

#: The feature that a workflow write needs on top of the router gate.
AUTHOR_FEATURE = "studio"

#: The check of the one entitlement seam (`acb_auth.deps.require_feature`):
#: 401 with no member, 403 that names `feature:studio`. This module adds no
#: rule of its own.
_studio_check = require_feature(AUTHOR_FEATURE).dependency


@functools.lru_cache(maxsize=16)
def _parse_orgs(raw: str) -> frozenset[str]:
    """The entries of a comma list, trimmed and in lower case."""
    return frozenset(p.strip().lower() for p in raw.split(",") if p.strip())


def author_gate_on(organization_id: str | None) -> bool:
    """True when ``WORKFLOW_STUDIO_GATE_ORGS`` holds the org, or holds ``*``.

    The ONE reader of the flag. It reads the settings on each call, so a
    SIGHUP reload applies a flip (``docs/flag_reload.md``). The org comes from
    the session, never from request input (R5). With ``*``, a caller with no
    org is checked too, so the gate fails closed.
    """
    listed = _parse_orgs(str(getattr(get_settings(), "workflow_studio_gate_orgs", "") or ""))
    if "*" in listed:
        return True
    return bool(organization_id) and str(organization_id).strip().lower() in listed


async def check_author(user: UserContext) -> None:
    """Refuse a workflow write of a caller without ``feature:studio``.

    With the org off the flag, it checks nothing, and the router gate alone
    decides, as before J1-3. ``runs.run_workflow`` calls it for a test run of
    the draft, before it opens a session.
    """
    if author_gate_on(user.organization_id):
        await _studio_check(user)


async def _require_author(
    user: Annotated[UserContext, Depends(get_current_user)],
) -> UserContext:
    await check_author(user)
    return user


#: The route dependency of each authoring route. It runs after the router gate
#: and before the body check, so a refused write reads nothing and parses
#: nothing.
AUTHOR_GATE = Depends(_require_author)

# ── Limits ───────────────────────────────────────────────────────────────────

MAX_GRAPH_BYTES = 512 * 1024  # one edit-model document
MAX_TRIGGER_PAYLOAD_BYTES = 256 * 1024  # inbound webhook/API trigger body
MAX_RUNS_PAGE = 100
MAX_RUN_STEPS = 200  # human steps in one answer of GET /runs/{run_id}/steps (STU-E4b)
HOOK_RATE_LIMIT_PER_MINUTE = 60  # per hook token

# ── DB (the one shared gateway engine — gateway/db.py, BO-10) ────────────────
#
# This package used to build its own engine here with its own 5+10 pool. It now
# has none: `_get_db` / `_get_session_factory` / `_tenant_session` at the top
# of this module are re-exports of the shared seam.
#
# Two names, one pool, and the split is the H2 conversion boundary:
#
# * `_tenant_session` — every MEMBER-REACHED handler (crud, publish, catalog,
#   search, modules, copilot, runs) acquires its session as
#   `async with _tenant_session() as db:`; the wrapper owns the transaction
#   (commit on clean exit, rollback on raise) and binds `app.tenant_id` from
#   the request context.
# * `_get_db` — still here for the UNATTENDED half of this package (service,
#   scheduler, triggers, hooks): engine runs, cron ticks, event dispatch and
#   token-authenticated webhooks have no member session to inherit a tenant
#   from, and H4 owns threading an explicit one through (see the H4 notes in
#   each of those modules). The H2 ratchet in `test_db_engine_seam.py` counts
#   these sites; they only go down.


# ── Small helpers ────────────────────────────────────────────────────────────


def _uid(user: UserContext | None) -> str:
    return getattr(user, "email", None) or "system:internal"


def iso(val: Any) -> str | None:
    return val.isoformat() if val is not None else None


def parse_jsonb(val: Any, default: Any = None) -> Any:
    """DB jsonb → Python (asyncpg returns str or dict depending on codec)."""
    if isinstance(val, (dict, list)):
        return val
    if isinstance(val, str):
        try:
            return json.loads(val)
        except ValueError:
            return default
    return default if val is None else val


def new_hook_token() -> str:
    """The webhook trigger URL credential: unguessable, URL-safe."""
    return secrets.token_urlsafe(24)


#: Path of the inbound webhook trigger, relative to the gateway's own origin.
HOOK_PATH = "/workflows/hooks/{token}"


def hook_url(token: str | None) -> str:
    """The URL an EXTERNAL system should POST to fire this workflow.

    Deliberately absolute and gateway-origin (``public_api_base_url``), not
    the control-plane origin the browser happens to be on. The control plane's
    ``/api`` proxy parses and re-serializes JSON bodies, which changes the
    bytes — so an HMAC computed by the sender cannot verify — and it drops
    non-JSON payloads entirely. Providers that post form-encoded or signed
    bodies need the raw request, and only the gateway route gets it.

    Empty when the origin is unconfigured: the UI then shows the path and says
    so, which is honest. Inventing an origin would hand out a URL that 404s in
    production and nobody would know why until a webhook silently stopped.
    """
    if not token:
        return ""
    base = (get_settings().public_api_base_url or "").rstrip("/")
    return f"{base}{HOOK_PATH.format(token=token)}" if base else ""


async def load_workflow_or_404(db: Any, workflow_id: str) -> Any:
    row = (
        await db.execute(
            text("SELECT * FROM workflows WHERE id = :id"),
            {"id": workflow_id},
        )
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return row


async def load_triggers(db: Any, workflow_id: str) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            text(
                "SELECT id, kind, config, enabled, last_fired_at "
                "FROM workflow_triggers WHERE workflow_id = :id "
                "ORDER BY created_at"
            ),
            {"id": workflow_id},
        )
    ).fetchall()
    return [
        {
            "id": str(r.id),
            "kind": r.kind,
            "config": parse_jsonb(r.config, {}),
            "enabled": bool(r.enabled),
            "last_fired_at": iso(r.last_fired_at),
        }
        for r in rows
    ]


def publish_workflow_activity(
    workflow_id: str,
    name: str,
    *,
    user: str | None = None,
    organization_id: str | None = None,
    **fields: Any,
) -> None:
    """Publish a workflow activation to the tenant's activity bus (best-effort).

    Workflows are a distinct actor class on the feed: ``kind="workflow"`` with
    ``agent="workflow:<name>"`` so runs show up in /observability alongside
    agent and app activity (spec G6).

    *organization_id* is the org of the run row (H-201c). A scheduler or a
    resume path often has no bound tenant, so each caller passes it from the
    row. ``None`` falls back to the bound tenant, and with neither the feed
    drops the event.
    """
    try:
        from acb_common import publish_activity

        publish_activity(
            organization_id=organization_id,
            kind="workflow",
            agent=f"workflow:{name or workflow_id}",
            user=user,
            source="workflows",
            **fields,
        )
    except Exception:
        pass
