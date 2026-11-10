"""Workflows routes — shared kernel (spec: project-docs/specs/workflows_app.md).

The shared ``router``, DB infrastructure, and the small helpers every feature
module uses. Mirrors ``routes/apps/_common.py`` / ``routes/tasks/core.py``
(the leaf module: it imports nothing from siblings outside ``engine/``).

Canonical store: the ``workflows`` / ``workflow_*`` tables from
``infra/postgres/132_workflows.sql``. The whole prefix is feature-gated on
``workflows``; the single exemption is the inbound webhook trigger, which
authenticates itself by its unguessable per-workflow token (+ optional HMAC)
— see ``hooks.py`` and the matching entry in ``main.PUBLIC_ROUTES``.
"""

from __future__ import annotations

import json
import secrets
from typing import Any

from acb_auth import UserContext, require_feature_router
from acb_common import get_logger, get_settings
from fastapi import APIRouter, HTTPException

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

# ── Limits ───────────────────────────────────────────────────────────────────

MAX_GRAPH_BYTES = 512 * 1024  # one edit-model document
MAX_TRIGGER_PAYLOAD_BYTES = 256 * 1024  # inbound webhook/API trigger body
MAX_RUNS_PAGE = 100
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
