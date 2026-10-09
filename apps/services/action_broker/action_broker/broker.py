"""Action Broker — the ONE component allowed to write back to source systems.

Every outward write (Zoho / Odoo / email) is meant to flow through
here so it is authority-gated and audited (root ``AGENTS.md`` non-negotiable #4).

This module now provides the real decision + execution core:

* :func:`decide_disposition` — the authority-tier policy (pure): given an actor's
  authority and whether the action is destructive/outward-facing, decide whether
  it auto-applies, needs a human, or is rejected. Destructive actions FAIL CLOSED
  (need a human) unless the authority is explicitly ``autonomous`` — mirroring the
  harness rule in ``AGENTS.md``.
* :func:`register_action_handler` / :func:`execute` — a fail-closed executor
  registry. A real source-of-truth write happens ONLY inside a registered
  handler, and an action with no handler is REFUSED (never silently applied).

This module itself registers nothing — handlers are wired in by the gateway at
startup / import (six sites as of 2026-08-05: task writes, workflow
resume, WhatsApp broadcast, two app-tool actions, and the CRM's three
``crm.zoho_*`` sync pushes). It is therefore **live**, not inert:
``pending_actions`` persistence, the Control Plane approval binding
(gateway ``routes/actions.py``) and the task-write reroute all shipped
2026-07-13.

Remaining per FOUNDATION_BUILDOUT_CHECKLIST §BO-1 — **corrected 2026-08-11; the
first two below read as open for a day after they shipped, and this is the
canonical file for the subsystem, so keep it true:**

* ✅ **BO-1a** (2026-08-11) — every gated action name had a handler. The
  task connector side of it is gone: D52 emptied the connector registry, and
  S8 PR 1 (2026-09-23) deleted ``routes/tasks/providers.py`` and
  ``broker_handlers.py``. ``tests/unit/test_no_task_provider_connectors.py``
  keeps them deleted.
* ✅ **BO-1b** (2026-08-11) — a broker-QUEUED push wrote
  ``sync_state='awaiting_approval'`` instead of a false ``'synced'``. The
  push path went with the retired task store (S8 PR 1).
* ☐ **BO-1d** — the three task callers that indexed the pending marker as a
  result (``accounts.py``, ``items.py::_push_patch_upstream``, and the
  ClickUp arm of ``planning.py``) are deleted. The rule stays: any new caller
  of a gated write must read the marker before it indexes the result. The
  ``ACTION_BROKER_ENFORCE`` flip stays owner-gated.
* ☐ **BO-1c** — email writes do not route through here at all.

⚠️ **A Zoho write client now exists** (2026-08-05, WS-26b —
``ingestion/sources/zoho/writer.py``), so the ``"zoho.email"`` example below is
no longer purely illustrative. It is reached only through
``gateway/routes/crm/broker_handlers.py``'s gate, all three of its actions have
registered handlers, and it is dormant until ``CRM_ZOHO_SYNC`` is turned on.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from acb_audit import AuditEvent, record
from acb_common import get_logger

_log = get_logger("action_broker")


class AuthorityTier(StrEnum):
    READ = "read"
    SUGGEST = "suggest"
    SUGGEST_APPLY = "suggest+apply"
    AUTONOMOUS = "autonomous"


class Disposition(StrEnum):
    """What the broker decided to do with a proposed action."""

    AUTO_APPLY = "auto_apply"          # execute now (still audited)
    NEEDS_APPROVAL = "needs_approval"  # hold for a human in the approval inbox
    REJECTED = "rejected"              # not permitted at this authority tier


@dataclass(slots=True)
class ActionProposal:
    id: UUID
    actor: str            # e.g. "agent:delivery"
    action: str           # e.g. "zoho.email", "odoo.write"
    target: str           # e.g. "lead:<zoho_id>"
    payload: dict[str, Any]
    authority: AuthorityTier
    # Whether the action is destructive / outward-facing (irreversible or leaves
    # the system). Defaults True so an un-annotated action FAILS CLOSED.
    destructive: bool = True
    disposition: Disposition | None = None


def decide_disposition(
    authority: AuthorityTier, *, destructive: bool
) -> Disposition:
    """Pure authority-tier policy — the single place the rules live.

    * ``read``          → REJECTED (a read-only actor may not write).
    * ``autonomous``    → AUTO_APPLY (trusted to act without a human).
    * ``suggest``       → NEEDS_APPROVAL (always propose, never auto-apply).
    * ``suggest+apply`` → AUTO_APPLY for reversible/idempotent actions, but
      NEEDS_APPROVAL for destructive/outward-facing ones (fail closed).
    """
    if authority == AuthorityTier.READ:
        return Disposition.REJECTED
    if authority == AuthorityTier.AUTONOMOUS:
        return Disposition.AUTO_APPLY
    if authority == AuthorityTier.SUGGEST:
        return Disposition.NEEDS_APPROVAL
    # SUGGEST_APPLY
    return Disposition.NEEDS_APPROVAL if destructive else Disposition.AUTO_APPLY


def propose(
    actor: str,
    action: str,
    target: str,
    payload: dict[str, Any],
    authority: AuthorityTier = AuthorityTier.SUGGEST_APPLY,
    *,
    destructive: bool = True,
) -> ActionProposal:
    """Create an action proposal, compute its disposition, and audit it.

    Does NOT execute — the caller (or the approval flow) calls :func:`execute`
    once the proposal is auto-apply or human-approved. Persisting a
    ``needs_approval`` proposal to the queue is the pending follow-up (BO-1).
    """
    disposition = decide_disposition(authority, destructive=destructive)
    proposal = ActionProposal(
        id=uuid4(), actor=actor, action=action, target=target,
        payload=payload, authority=authority, destructive=destructive,
        disposition=disposition,
    )
    record(AuditEvent(
        actor=actor, action=f"propose:{action}", target=target,
        payload={
            "authority": authority.value,
            "disposition": disposition.value,
            "destructive": destructive,
            **payload,
        },
    ))
    return proposal


# ── Executor registry — the ONLY place a real source-of-truth write happens ──
# A handler performs the actual provider write for a given action name. Nothing
# is registered by default, so the broker cannot write anything until handlers
# are wired in (deliberately inert + non-breaking).
_HANDLERS: dict[str, Callable[[ActionProposal], Awaitable[Any]]] = {}


def register_action_handler(
    action: str, handler: Callable[[ActionProposal], Awaitable[Any]]
) -> None:
    """Register the write handler for *action* (e.g. ``"zoho.email"``)."""
    _HANDLERS[action] = handler


def clear_action_handlers() -> None:
    """Drop all registered handlers (used in tests)."""
    _HANDLERS.clear()


async def execute(proposal: ActionProposal) -> dict[str, Any]:
    """Perform an auto-apply / approved proposal via its registered handler.

    Fails CLOSED:
    * a ``rejected`` proposal is never executed;
    * an action with no registered handler is REFUSED (never silently applied);
    both are audited. On success the handler's result is returned.
    """
    if proposal.disposition == Disposition.REJECTED:
        record(AuditEvent(
            actor="system:action_broker",
            action=f"execute_refused:{proposal.action}",
            target=proposal.target,
            payload={"reason": "rejected_by_authority"},
        ))
        return {"ok": False, "error": "rejected by authority policy"}

    handler = _HANDLERS.get(proposal.action)
    if handler is None:
        record(AuditEvent(
            actor="system:action_broker",
            action=f"execute_refused:{proposal.action}",
            target=proposal.target,
            payload={"reason": "no_handler"},
        ))
        return {
            "ok": False,
            "error": f"no handler registered for action {proposal.action!r}",
        }

    record(AuditEvent(
        actor="system:action_broker",
        action=f"execute:{proposal.action}",
        target=proposal.target,
        payload={"authority": proposal.authority.value},
    ))
    result = await handler(proposal)
    return {"ok": True, "result": result}


# ── Approval queue — persistence for NEEDS_APPROVAL proposals ─────────────────
# Mirrors ``pending_commit`` (self-mutation): a proposal the broker cannot
# auto-apply is parked in ``pending_actions`` until an operator approves it, at
# which point :func:`execute` runs the registered handler. Each write is
# best-effort and returns a sentinel (never raises) so a broker call is not lost
# on a DB blip.
#
# ⚠️ **Tenant-bound (H-201).** ``pending_actions`` has FORCE row level security.
# Every read and write opens ``acb_graph.tenant_session(org)``, the ONE sync GUC
# seam. ``org`` is the tenant the caller's context bound (``current_tenant()``,
# set by the auth dependency or by a job from its own record). It never comes
# from a proposal, a payload or a request field (R5 / R11). With no bound tenant
# a read answers empty, a write refuses, and ``action_broker.tenant_unbound``
# is logged. Nothing falls back to the unbound ``get_session``. The fence is
# ``tests/unit/test_action_broker_tenancy_r8.py``.


def _bound_tenant(op: str) -> str | None:
    """The tenant this broker call acts for, or ``None`` (logged)."""
    from acb_common.db import current_tenant

    org = current_tenant()
    if not org:
        _log.warning("action_broker.tenant_unbound", op=op)
        return None
    return str(org)


def enqueue(proposal: ActionProposal) -> str | None:
    """Persist *proposal* to ``pending_actions`` (status ``pending``).

    Returns the row id (the proposal's own UUID) or ``None`` if the DB write
    fails or no tenant is bound. Call this for a ``NEEDS_APPROVAL`` disposition.
    The row's ``organization_id`` comes from the GUC that ``tenant_session``
    sets, through the column's DEFAULT.
    """
    import json

    try:
        from acb_graph import tenant_session
        from sqlalchemy import text

        row_id = str(proposal.id)
        # With no tenant, ``tenant_session(None)`` raises ``TenantUnbound``
        # before it opens a connection, and the ``except`` below audits it.
        with tenant_session(_bound_tenant("enqueue")) as sess:
            sess.execute(
                text(
                    "INSERT INTO pending_actions "
                    "(id, actor, action, target, payload, authority, "
                    " destructive, disposition, status) "
                    "VALUES (:id, :actor, :action, :target, CAST(:payload AS jsonb), "
                    "        :authority, :destructive, :disposition, 'pending')"
                ),
                {
                    "id": row_id,
                    "actor": proposal.actor,
                    "action": proposal.action,
                    "target": proposal.target,
                    "payload": json.dumps(proposal.payload or {}),
                    "authority": proposal.authority.value,
                    "destructive": proposal.destructive,
                    "disposition": (proposal.disposition or Disposition.NEEDS_APPROVAL).value,
                },
            )
        record(AuditEvent(
            actor="system:action_broker",
            action=f"enqueue:{proposal.action}",
            target=proposal.target,
            payload={"pending_action_id": row_id, "authority": proposal.authority.value},
        ))
        return row_id
    except Exception as exc:  # best-effort: never lose the caller on a DB blip
        record(AuditEvent(
            actor="system:action_broker",
            action=f"enqueue_failed:{proposal.action}",
            target=proposal.target,
            payload={"error": str(exc)},
        ))
        return None


def list_pending() -> list[dict[str, Any]]:
    """Return the bound tenant's pending queue (newest first).

    ``[]`` on DB failure, and ``[]`` when no tenant is bound.
    """
    org = _bound_tenant("list_pending")
    if org is None:
        return []
    try:
        from acb_graph import tenant_session
        from sqlalchemy import text

        with tenant_session(org) as sess:
            rows = sess.execute(
                text(
                    "SELECT id, actor, action, target, payload, authority, "
                    "       destructive, disposition, status, created_at "
                    "FROM pending_actions WHERE status = 'pending' "
                    "ORDER BY created_at DESC"
                )
            ).mappings().all()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _load_proposal(action_id: str) -> tuple[ActionProposal | None, str | None]:
    """Load a queued row and rebuild its :class:`ActionProposal` + current status.

    The read sees only the bound tenant's rows. Another tenant's id, or no
    bound tenant, reads as ``(None, None)``, so :func:`approve` runs nothing.
    """
    from acb_graph import tenant_session
    from sqlalchemy import text

    org = _bound_tenant("load_proposal")
    if org is None:
        return None, None
    with tenant_session(org) as sess:
        row = sess.execute(
            text(
                "SELECT id, actor, action, target, payload, authority, "
                "       destructive, disposition, status "
                "FROM pending_actions WHERE id = :id"
            ),
            {"id": action_id},
        ).mappings().first()
    if row is None:
        return None, None
    proposal = ActionProposal(
        id=UUID(str(row["id"])),
        actor=row["actor"],
        action=row["action"],
        target=row["target"],
        payload=row["payload"] or {},
        authority=AuthorityTier(row["authority"]),
        destructive=row["destructive"],
        disposition=Disposition(row["disposition"]),
    )
    return proposal, row["status"]


def _mark(
    action_id: str, status: str, *, reviewed_by: str | None = None,
    result: dict[str, Any] | None = None, only_from: str | None = None,
) -> int:
    """Update a queued row's status (+ reviewer / result). Best-effort.

    Returns the number of rows it changed, 0 when it changed none. With
    ``only_from`` it changes the row only while it still has that status, so a
    late reject cannot rewrite an action that already ran (review, 2026-10-09).

    The UPDATE reaches only the bound tenant's rows. With no bound tenant it
    changes nothing.
    """
    import json

    org = _bound_tenant("mark")
    if org is None:
        return 0
    try:
        from acb_graph import tenant_session
        from sqlalchemy import text

        reviewed_at_expr = "now()" if reviewed_by is not None else "reviewed_at"
        guard = " AND status = :only_from" if only_from is not None else ""
        with tenant_session(org) as sess:
            res = sess.execute(
                text(
                    "UPDATE pending_actions SET status = :status, "
                    "reviewed_by = COALESCE(:reviewed_by, reviewed_by), "
                    f"reviewed_at = {reviewed_at_expr}, "
                    "result = CAST(:result AS jsonb) "
                    f"WHERE id = :id{guard}"
                ),
                {
                    "id": action_id,
                    "status": status,
                    "reviewed_by": reviewed_by,
                    "result": json.dumps(result) if result is not None else None,
                    "only_from": only_from,
                },
            )
            return int(getattr(res, "rowcount", 0) or 0)
    except Exception:
        return 0


def reject(action_id: str, reviewed_by: str) -> dict[str, Any]:
    """Reject a pending action — it is never executed. Audited.

    It answers ``ok: False`` when no pending row of the bound tenant has that
    id: another tenant's id, a missing id, or an action already decided. It
    used to answer ``ok`` for all three (review, 2026-10-09).
    """
    if not _mark(action_id, "rejected", reviewed_by=reviewed_by, only_from="pending"):
        return {"ok": False, "error": f"no pending action {action_id!r}"}
    record(AuditEvent(
        actor=reviewed_by,
        action="action_rejected",
        target=action_id,
        payload={},
    ))
    return {"ok": True, "status": "rejected", "action_id": action_id}


async def approve(action_id: str, reviewed_by: str) -> dict[str, Any]:
    """Approve a pending action and execute it via its registered handler.

    Fails CLOSED: a missing/non-pending row is not run; a handler error marks
    the row ``failed`` (not ``applied``). The handler result is persisted.
    """
    proposal, status = _load_proposal(action_id)
    if proposal is None:
        return {"ok": False, "error": f"no pending action {action_id!r}"}
    if status != "pending":
        return {"ok": False, "error": f"action {action_id!r} is {status}, not pending"}

    _mark(action_id, "approved", reviewed_by=reviewed_by)
    res = await execute(proposal)
    if res.get("ok"):
        _mark(action_id, "applied", result=res)
        return {"ok": True, "status": "applied", "action_id": action_id, "result": res.get("result")}
    _mark(action_id, "failed", result=res)
    return {"ok": False, "status": "failed", "action_id": action_id, "error": res.get("error")}


async def submit(proposal: ActionProposal) -> dict[str, Any]:
    """Route a proposal by its disposition — the one entry point callers use.

    * ``AUTO_APPLY``     → execute now (still audited).
    * ``NEEDS_APPROVAL`` → enqueue for a human; nothing is written yet.
    * ``REJECTED``       → refused (audited by ``execute``).
    """
    disposition = proposal.disposition or decide_disposition(
        proposal.authority, destructive=proposal.destructive
    )
    if disposition == Disposition.AUTO_APPLY:
        res = await execute(proposal)
        return {"status": "applied" if res.get("ok") else "failed",
                "disposition": disposition.value, **res}
    if disposition == Disposition.NEEDS_APPROVAL:
        action_id = enqueue(proposal)
        return {"ok": True, "status": "pending", "disposition": disposition.value,
                "action_id": action_id}
    # REJECTED
    res = await execute(proposal)  # execute() refuses + audits a rejected proposal
    return {"status": "rejected", "disposition": disposition.value, **res}
