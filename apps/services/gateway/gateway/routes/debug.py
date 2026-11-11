"""Diagnostics API over the durable run-trace store (E2 Phase 3).

Read-only endpoints for post-hoc debugging of agent runs — the queryable face
of the ``agent_run`` table (see ``gateway/run_trace.py``, migration 50). Lets an
engineer answer "error X happened with agent Y" WITHOUT SSHing into the box:

    GET  /debug/runs?agent=&status=&user=&since_hours=&limit=   → list (lean)
    GET  /debug/runs/{run_id}                                    → full trace
    POST /debug/runs/{run_id}/flag                               → keep trace

Gated to EXECUTIVE (and AGENT, for service-to-service tooling) because a run
trace can contain message content. Every query is bounded (LIMIT) and the list
view never returns the heavy ``trace`` blob — only ``GET /{run_id}`` does.

H-201: every query binds the caller's tenant through ``acb_graph.tenant_session``
(``agent_run`` has FORCE RLS). With no tenant, the list is empty and a get or a
flag is 404. No route opens an unbound session, and none gives a cross-tenant
view.
"""
from __future__ import annotations

from typing import Any

from acb_auth import UserContext, UserRole, require_role
from acb_common import get_logger
from fastapi import APIRouter, HTTPException

_log = get_logger("gateway.debug")

# EXECUTIVE (humans) + AGENT (internal service calls / CI harness) may read.
_ADMIN = require_role(UserRole.EXECUTIVE, UserRole.AGENT)

router = APIRouter(prefix="/debug", tags=["debug"])

# Valid status filter values (mirrors agent_run.status).
_STATUSES = frozenset({"running", "completed", "error", "cancelled"})


def _row_summary(r: Any) -> dict[str, Any]:
    """Lean list-row projection — NO trace blob (that's the detail view)."""
    return {
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
        "tool_summary": r.tool_summary or [],
        "error_type": r.error_type,
        "error_message": r.error_message,
        "flagged": r.flagged,
    }


@router.get("/runs")
async def list_runs(
    agent: str | None = None,
    status: str | None = None,
    user: str | None = None,
    thread_id: str | None = None,
    since_hours: int | None = None,
    limit: int = 50,
    admin: UserContext = _ADMIN,
) -> dict[str, Any]:
    """List recent agent runs, newest-first, with optional filters.

    Filters (all AND-combined, all optional):
      agent, status (running|completed|error|cancelled), user, thread_id,
      since_hours (only runs started within the last N hours).
    ``limit`` is clamped to [1, 500]. The heavy ``trace`` is omitted here —
    fetch it via ``GET /debug/runs/{run_id}``.
    """
    if status and status not in _STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status '{status}'. One of: {sorted(_STATUSES)}.",
        )
    limit = max(1, min(limit, 500))
    org = admin.organization_id
    if not org:
        return {"count": 0, "runs": []}

    where: list[str] = []
    params: dict[str, Any] = {"limit": limit}
    if agent:
        where.append("agent_name = :agent")
        params["agent"] = agent
    if status:
        where.append("status = :status")
        params["status"] = status
    if user:
        where.append("user_id = :user")
        params["user"] = user
    if thread_id:
        where.append("thread_id = :thread_id")
        params["thread_id"] = thread_id
    if since_hours and since_hours > 0:
        where.append("started_at > now() - make_interval(hours => :since_hours)")
        params["since_hours"] = int(since_hours)
    clause = (" WHERE " + " AND ".join(where)) if where else ""

    try:
        from acb_graph import tenant_session
        from sqlalchemy import text

        with tenant_session(org) as s:
            rows = s.execute(
                text(
                    "SELECT run_id, thread_id, agent_name, user_id, model, "
                    "status, started_at, ended_at, duration_ms, total_tokens, "
                    "tool_count, tool_summary, error_type, error_message, flagged "
                    "FROM agent_run" + clause
                    + " ORDER BY started_at DESC LIMIT :limit"
                ),
                params,
            ).fetchall()
        return {"count": len(rows), "runs": [_row_summary(r) for r in rows]}
    except Exception as exc:
        _log.warning("debug.list_runs_failed", error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/runs/{run_id}")
async def get_run(
    run_id: str,
    admin: UserContext = _ADMIN,
) -> dict[str, Any]:
    """Full record for one run: metadata + error + traceback + folded trace.

    The ``trace`` (message content + tool results + reasoning) is present only
    for runs the retention policy kept it for (errored / cancelled / flagged);
    it's ``null`` for a clean successful run.
    """
    org = admin.organization_id
    if not org:
        raise HTTPException(status_code=404, detail=f"run '{run_id}' not found")
    try:
        from acb_graph import tenant_session
        from sqlalchemy import text

        with tenant_session(org) as s:
            r = s.execute(
                text(
                    "SELECT run_id, thread_id, agent_name, user_id, model, "
                    "status, started_at, ended_at, duration_ms, prompt_tokens, "
                    "completion_tokens, total_tokens, tool_count, tool_summary, "
                    "error_message, error_type, error_traceback, trace, flagged, "
                    "created_at FROM agent_run WHERE run_id = :rid"
                ),
                {"rid": run_id},
            ).first()
        if r is None:
            raise HTTPException(status_code=404, detail=f"run '{run_id}' not found")
        out = _row_summary(r)
        out.update({
            "prompt_tokens": r.prompt_tokens,
            "completion_tokens": r.completion_tokens,
            "error_traceback": r.error_traceback,
            "trace": r.trace,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        })
        return out
    except HTTPException:
        raise
    except Exception as exc:
        _log.warning("debug.get_run_failed", run_id=run_id[:40], error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/runs/{run_id}/flag")
async def flag_run(
    run_id: str,
    admin: UserContext = _ADMIN,
) -> dict[str, Any]:
    """Mark a run ``flagged`` so its full trace is retained going forward.

    Useful for a run that succeeded but behaved oddly — flagging keeps it out of
    any future trace-pruning and marks it for review. (Note: the full ``trace``
    blob is only captured for errored/cancelled/flagged runs AT WRITE TIME; a
    late flag on a clean run marks it but cannot resurrect a body never stored.)

    ``tenant_session`` commits on exit, so the UPDATE lands. Another tenant's
    run is not visible to the UPDATE, so it is 404 and no row changes.
    """
    org = admin.organization_id
    if not org:
        raise HTTPException(status_code=404, detail=f"run '{run_id}' not found")
    try:
        from acb_graph import tenant_session
        from sqlalchemy import text

        with tenant_session(org) as s:
            res = s.execute(
                text(
                    "UPDATE agent_run SET flagged = true "
                    "WHERE run_id = :rid RETURNING run_id"
                ),
                {"rid": run_id},
            ).first()
        if res is None:
            raise HTTPException(status_code=404, detail=f"run '{run_id}' not found")
        return {"run_id": run_id, "flagged": True}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
