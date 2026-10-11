"""CRM · broker_handlers — the Action-Broker gate every Zoho push goes through.

Spec: ``project-docs/specs/crm_app.md`` D-CRM-8 · §7.1 · ticket WS-26b
done-when 1 and 5. Modelled 1:1 on the tasks app's ClickUp path —
``routes/tasks/providers.py::_broker_gate`` (the gate) plus
``routes/tasks/broker_handlers.py`` (the persistent handlers registered from
``main.py``; S8 PR 1 deleted both on 2026-09-23) — because that pair is *"the single audited chokepoint for
source-of-truth writes"* and root ``AGENTS.md`` #4 applies to Zoho exactly as
it applies to ClickUp.

Three action names, one per verb: ``crm.zoho_create`` / ``crm.zoho_update`` /
``crm.zoho_delete``.

**Default disposition auto-applies.** With ``ACTION_BROKER_ENFORCE`` off (the
default, and the state of every environment) the broker AUDITS the push and the
write runs immediately — the sync stays continuous. Flipping enforcement on
turns each push into a queued proposal in the ``/actions`` inbox and the sync
becomes *supervised* rather than continuous. That consequence is accepted
deliberately (D-CRM-8), and unlike the ClickUp gate — which routes six action
names through the gate but registers only four handlers, so approving one of
the other two is marked ``failed`` (BO-1a) — **all three CRM actions have a
handler here**, so an approved CRM push really executes.

⚠️ **This module deliberately does not import the writer.** The single-caller
rule (§7.1) says ``ingestion/sources/zoho/writer.py`` has exactly one caller,
the sync engine; an approved queued push therefore re-enters through
``sync_zoho.execute_push`` rather than reaching Zoho from here. Keeping the
writer's import site singular is the property the grep assertion in
``tests/unit/test_crm_zoho_sync.py`` measures.
"""

from __future__ import annotations

import contextlib
import os
from typing import Any

from acb_common import get_logger
from gateway.routes.crm.core import ZOHO_TRACKED_TABLES

_log = get_logger("gateway.crm.broker")

#: The native tables a Zoho push records its outcome on: the four tracked
#: record tables, the activity spine and the tombstone queue. The approval
#: handler refuses any other ``table`` in a proposal, because that name
#: reaches an f-string in ``core.load_row`` and ``core.update_row``.
PUSH_TABLES: frozenset[str] = ZOHO_TRACKED_TABLES | {
    "crm_activities", "crm_zoho_tombstones",
}

#: The three broker actions a Zoho push can be. Registered as handlers and used
#: as the gate's action names — one list, so a verb cannot be gated without
#: also being executable on approval.
CRM_ZOHO_ACTIONS: tuple[str, ...] = (
    "crm.zoho_create", "crm.zoho_update", "crm.zoho_delete",
)

#: The actor recorded on every proposal and audit row. Never a credential.
BROKER_ACTOR = "crm:zoho-sync"


def broker_enforced(action: str) -> bool:
    """Whether ``ACTION_BROKER_ENFORCE`` routes *action* to the approval QUEUE.

    The SAME environment variable the tasks app reads, on purpose: the owner
    gets one kill-switch for outward writes, not one per app. Unset / ``none``
    / ``off`` → ``False`` → auto-apply. ``1``/``all``/``on`` → queue
    everything. A comma-list queues the named actions only.

    Re-implemented here rather than imported from ``routes/tasks/providers``:
    importing another app package's private helper is the cross-app coupling
    D-CRM-4 rejects, and this is four lines of env parsing.
    """
    raw = (os.environ.get("ACTION_BROKER_ENFORCE") or "").strip().lower()
    if not raw or raw in ("0", "none", "off", "false"):
        return False
    if raw in ("1", "all", "on", "true"):
        return True
    return action in {a.strip() for a in raw.split(",") if a.strip()}


async def broker_gate(
    action: str, target: str, audit_payload: dict[str, Any], do_write: Any,
) -> dict[str, Any]:
    """Route one outward Zoho write through the Action Broker.

    Returns the writer's result on the auto-apply path, or a
    ``{"pending": True, ...}`` marker when enforcement queued it. **The caller
    must check for that marker**: a queued push has not happened, so clearing
    ``zoho_dirty`` on it would lose the change permanently — the row would
    look synced while Zoho never saw it. (That is BO-1b's bug on the ClickUp
    side; ``sync_zoho`` checks.)

    Fail-safe in the same direction as the tasks gate: a broker-LAYER failure
    (module missing, DB down) never silently drops the sync — the write still
    runs and is logged as a bypass. ``do_write`` executes **exactly once**; its
    own errors propagate untouched so the engine can count them and leave the
    row dirty for the next cycle.
    """
    try:
        from action_broker import AuthorityTier, Disposition, enqueue, propose

        queued = broker_enforced(action)
        proposal = propose(
            BROKER_ACTOR, action, target, audit_payload,
            authority=(
                AuthorityTier.SUGGEST_APPLY if queued else AuthorityTier.AUTONOMOUS
            ),
            destructive=queued,
        )
        disposition = proposal.disposition
    except Exception as exc:  # broker unavailable → never lose the write
        _log.warning("crm.broker.gate_bypass", action=action, error=str(exc)[:200])
        return await do_write()

    if disposition == Disposition.NEEDS_APPROVAL:
        existing = pending_action_for(action, target)
        if existing is not None:
            # ⚠️ Do NOT enqueue a second proposal for the same write. The row
            # stays dirty by design while its push is unapproved, so it is
            # re-offered every cycle — at a ten-minute interval that is 144
            # identical rows per day per stuck record, and an approvals inbox
            # nobody can read is an approvals inbox nobody uses.
            _log.info(
                "crm.broker.push_already_queued",
                action=action, target=target, action_id=existing,
            )
            return {"pending": True, "pending_action_id": existing}
        action_id = None
        with contextlib.suppress(Exception):
            action_id = enqueue(proposal)
        _log.info("crm.broker.push_queued", action=action, action_id=action_id)
        return {"pending": True, "pending_action_id": action_id}
    if disposition == Disposition.REJECTED:
        raise RuntimeError(f"Zoho push {action} rejected by authority policy")
    return await do_write()


def pending_action_for(action: str, target: str) -> str | None:
    """The id of an already-queued proposal for this exact write, if any.

    ``(action, target)`` identifies the write: ``crm.zoho_update`` +
    ``Deals:5540…001`` is the same intent no matter how many cycles re-offer
    it. Best-effort — a broker/DB failure answers "none", which at worst
    duplicates one inbox row and never blocks a push.
    """
    try:
        from action_broker import list_pending

        for row in list_pending():
            if row.get("action") == action and row.get("target") == target:
                return str(row.get("id"))
    except Exception as exc:  # pragma: no cover — best-effort by design
        _log.warning("crm.broker.pending_lookup_failed", error=str(exc)[:200])
    return None


async def _handle_crm_zoho_write(proposal: Any) -> dict[str, Any]:
    """Execute a queued Zoho push on approval (the registered handler).

    The proposal's payload is the whole spec of the write — module, verb, the
    Zoho id when there is one, the field body, **and the native row to record
    the outcome on** — so approving it months later needs nothing that was
    only in memory. Nothing sensitive is stored: the OAuth token is re-derived
    by the client at call time and is never persisted on the proposal.

    ⚠️ **It writes the local state back, exactly as the inline path does.**
    Without that the approval performs the Zoho write and records nothing: the
    row stays dirty, the next cycle offers it again, the operator approves
    again, and every approval mints another copy upstream. The two paths share
    ``sync_zoho.apply_push_result`` so they cannot converge differently.

    **The tenant, in this order** (WS-53 CRM-T3, ``crm_platform.md`` §4.3):

    1. ``org = current_tenant()`` — the tenant the ``approve`` call bound.
       ``action_broker/broker.py`` forbids a tenant taken from a proposal, a
       payload or a request field. RLS on ``pending_actions`` makes the
       approver's tenant equal the proposal's organization. With no tenant
       bound, this raises before any Zoho call.
    2. A first ``tenant_session(org)`` confirms that the native row exists in
       that tenant. A row that the tenant cannot see, a table outside
       :data:`PUSH_TABLES`, or a proposal with no native row is refused, with
       no Zoho call. Before CRM-T3 the last case wrote to Zoho and recorded
       nothing. Now it writes nothing, because a write with nowhere to record
       it is the duplicate-upstream defect above.
    3. ``execute_push`` runs with NO session open, because a provider call
       must not hold a pool slot or a transaction.
    4. ``apply_push_result`` runs in a second ``tenant_session(org)``, with no
       ``.commit()`` inside it.
    """
    from acb_common.db import TenantUnbound, current_tenant
    from gateway.routes.crm.core import _tenant_session, load_row
    from gateway.routes.crm.sync_zoho import apply_push_result, execute_push

    spec = proposal.payload or {}
    args = spec.get("args") or {}
    if proposal.action not in CRM_ZOHO_ACTIONS:
        raise RuntimeError(f"no Zoho writer for action {proposal.action!r}")
    org = current_tenant()
    if not org:
        _log.warning("crm.broker.push_unbound", action=proposal.action)
        raise TenantUnbound(
            "a CRM Zoho push needs the tenant the approve call bound — "
            "refused before any Zoho call"
        )
    op = args.get("op")
    table, native_id = args.get("table"), args.get("native_id")
    if not (table and native_id) or table not in PUSH_TABLES:
        _log.error(
            "crm.broker.push_refused", action=proposal.action,
            module=args.get("module"), table=table,
            reason="proposal names no native row this handler may record on",
        )
        raise RuntimeError(
            f"Zoho push {proposal.action} names no native row to record on — "
            "refused before any Zoho call"
        )
    async with _tenant_session(org) as db:
        visible = await load_row(db, table, str(native_id)) is not None
    if not visible:
        _log.error(
            "crm.broker.push_refused", action=proposal.action,
            module=args.get("module"), table=table, record_id=native_id,
            reason="the native row is not visible in the bound tenant",
        )
        raise RuntimeError(
            f"Zoho push {proposal.action}: native row {table}/{native_id} is "
            "not in this tenant — refused before any Zoho call"
        )

    result = await execute_push(
        module=args.get("module"),
        op=op,
        record_id=args.get("record_id"),
        body=args.get("body") or {},
    )

    async with _tenant_session(org) as db:
        await apply_push_result(
            db, table=table, native_id=native_id, op=op, result=result,
        )
    _log.info(
        "crm.broker.push_applied",
        action=proposal.action, module=args.get("module"),
        table=table, record_id=native_id,
    )
    return result


def register_crm_broker_handlers() -> None:
    """Wire the three handlers so a queued Zoho push executes on approval.

    Called once from the gateway lifespan/startup block in ``main.py``, exactly
    like ``register_task_broker_handlers``. Idempotent.
    """
    from action_broker import register_action_handler

    for action in CRM_ZOHO_ACTIONS:
        register_action_handler(action, _handle_crm_zoho_write)
    _log.info("crm.broker.handlers_registered", actions=list(CRM_ZOHO_ACTIONS))


__all__ = [
    "BROKER_ACTOR",
    "CRM_ZOHO_ACTIONS",
    "PUSH_TABLES",
    "broker_enforced",
    "broker_gate",
    "pending_action_for",
    "register_crm_broker_handlers",
]
