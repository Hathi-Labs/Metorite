"""Persistent Action Broker handlers for task-provider writes (audit BO-1 / A2).

Registered at gateway startup (see ``main.py``). When ``ACTION_BROKER_ENFORCE``
queues a provider write, approving it in the ``/actions`` inbox calls
``action_broker.execute()``, which dispatches here. The account's token is
**re-resolved** from the ``account_id`` stored in the queued proposal (the token
itself is NEVER persisted) and the raw provider write runs — completing the
enqueue → approve → execute loop end-to-end.

🔴 **``_WRITERS`` IS EMPTY SINCE D52 (2026-08-24, board WS-39 S1), and that is
the decision.** Its six entries were all ``clickup.*`` and their ``_raw_*``
targets lived on ``ClickUpProvider``, which is deleted — Metorite is the
project-management system of record, so there is no outward task write to gate.

⚠️ **What this means for a stale queued row.** A ``pending_actions`` row
enqueued before the retirement is still approvable (``work_plan.md`` §6). With
no entry here, approving one falls into ``broker.execute()``'s no-handler branch
and the row is marked ``failed`` — which is the correct outcome: the write
cannot be performed, and failing loudly beats reporting a success that reached
nothing. Check with ``SELECT action, status FROM pending_actions`` before
flipping anything.

This module goes with the provider layer in WS-39 **S3a**; it is kept now so the
registration call in ``main.py`` stays a no-op rather than an import error.
"""
from __future__ import annotations

import json
from typing import Any

from acb_common import get_logger

_log = get_logger("gateway.tasks.broker_handlers")

# broker action → (raw-writer method on the provider, ordered arg keys in `args`)
#
# ⚠️ EVERY action name `providers.py` routes through `_broker_gate` must appear
# here, or approving that queued write falls into `broker.execute()`'s no-handler
# branch and the `pending_actions` row is marked `failed` (BO-1a). The keys of an
# entry must match the `args` dict the gate's `audit_payload` carries at that
# call site — the handler reads them positionally. Both directions are fenced by
# `tests/unit/test_task_broker_handlers.py`.
_WRITERS: dict[str, tuple[str, tuple[str, ...]]] = {}


async def _resolve_provider(account_id: str):
    """Rebuild a provider (with its token) from a ``task_accounts`` id."""
    # ⚠️ H4, DELIBERATELY NOT H2 (`saas_multitenancy_handover.md`): this module
    # is an ACTION-BROKER CONSUMER, not a request handler — `execute()` runs
    # when an owner approves a queued proposal, outside the request (and tenant
    # binding) that enqueued it. The runbook's rule for that category is "do
    # not let a job inherit an ambient tenant", so this site stays on the
    # unbound `get_db()` until H4 threads an EXPLICIT tenant through the queued
    # proposal (`tenant_session(org_id)`). Sequencing is safe: RLS phase 4 is
    # gated on H2+H4 both being complete.
    from gateway.routes.tasks.core import _get_db, _key_store
    from gateway.routes.tasks.providers import build_provider
    from sqlalchemy import text

    db = await _get_db()
    try:
        row = (await db.execute(
            text("SELECT provider, workspace_id, credentials_encrypted "
                 "FROM task_accounts WHERE id = :id"),
            {"id": account_id},
        )).mappings().first()
    finally:
        await db.close()
    if row is None:
        raise RuntimeError(f"task account {account_id} not found")
    creds = json.loads(_key_store().decrypt(row["credentials_encrypted"]))
    return build_provider(
        row["provider"], creds, row["workspace_id"], account_id,
    )


async def _handle_task_write(proposal) -> dict[str, Any]:
    """Execute a queued task write on approval (the registered broker handler)."""
    spec = proposal.payload or {}
    account_id = spec.get("account_id")
    args = spec.get("args") or {}
    entry = _WRITERS.get(proposal.action)
    if entry is None:
        raise RuntimeError(f"no writer for action {proposal.action!r}")
    if not account_id:
        raise RuntimeError(
            f"cannot execute {proposal.action}: no account_id in the queued spec")
    method_name, keys = entry
    provider = await _resolve_provider(account_id)
    writer = getattr(provider, method_name)
    result = await writer(*[args.get(k) for k in keys])
    _log.info(
        "broker.task_write_applied", action=proposal.action, account_id=account_id,
    )
    return result


def register_task_broker_handlers() -> None:
    """Wire the persistent handlers so queued task writes execute on approval.
    Idempotent — safe to call once at startup."""
    from action_broker import register_action_handler

    for action in _WRITERS:
        register_action_handler(action, _handle_task_write)
    _log.info("broker.task_handlers_registered", actions=list(_WRITERS))
