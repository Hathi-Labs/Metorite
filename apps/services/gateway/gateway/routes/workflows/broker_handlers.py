"""Action Broker handlers owned by the Workflows package.

``workflow.resume_run`` — approving the proposal a paused run filed into the
approvals inbox resumes that run (spec F11). Registered at import time from
``main.py``, mirroring ``routes/tasks/broker_handlers.py``; the flat handler
registry is shared across owners, so the name is namespaced.
"""

from __future__ import annotations

from typing import Any

from acb_common import get_logger

_log = get_logger("gateway.workflows.broker")


async def _resume_run_handler(proposal: Any) -> dict[str, Any]:
    """Broker ``execute`` calls this on approve — resume the paused run.

    **The org is the approver's (2026-10-15).** ``broker.approve`` runs in the
    approver's request, so ``current_tenant()`` is the org that the auth
    dependency bound from the session. It never comes from the proposal or
    its payload (R5, R11). ``resume_run`` reads and writes in that org, so a
    run of another org reads as ``no run``. With no bound org this refuses
    and reads nothing. Fence: ``tests/unit/test_workflows_tool_rls_r8.py``.
    """
    from acb_common.db import current_tenant
    from gateway.routes.workflows.service import resume_run

    payload = getattr(proposal, "payload", None) or {}
    run_id = str(payload.get("run_id") or "")
    if not run_id:
        return {"ok": False, "error": "proposal has no run_id"}
    org = current_tenant()
    if not org:
        _log.warning("workflows.approval_resume_unbound", run_id=run_id)
        return {"ok": False, "error": "no organization is bound, so the run cannot resume"}
    result = await resume_run(run_id, resumed_by="approvals-inbox", organization_id=str(org))
    _log.info(
        "workflows.approval_resume",
        run_id=run_id,
        ok=result.get("ok"),
        node_id=result.get("node_id"),
    )
    return result


def register_handlers() -> None:
    """Idempotent registration (main.py calls this once at import wiring)."""
    from action_broker.broker import register_action_handler

    register_action_handler("workflow.resume_run", _resume_run_handler)
