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

* ✅ **BO-1a** (2026-08-11) — every gated action name now has a handler;
  an ``ast``-derived fence over ``routes/tasks/providers.py`` fails if a seventh
  arrives without one.
* ✅ **BO-1b** (2026-08-11) — a broker-QUEUED push writes
  ``gtd_items.sync_state='awaiting_approval'`` instead of a false ``'synced'``.
* ☐ **BO-1d** — four callers of a gated write still index the pending
  marker as if it were a result: ``routes/tasks/accounts.py`` (create project,
  create folder) and ``routes/tasks/planning.py`` (plan apply) hard-**500**
  under enforcement, and ``routes/tasks/items.py::_push_patch_upstream``
  silently swallows a queued update. **This is what still blocks flipping
  ``ACTION_BROKER_ENFORCE``** — BO-1a + BO-1b did not make the flip safe.
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
# which point :func:`execute` runs the registered handler. DB access uses the
# same sync ``acb_graph.get_session`` + ``text()`` recipe as
# ``mutation._register_pending_commit``; each write is best-effort and returns a
# sentinel (never raises) so a broker call is not lost on a DB blip.


def enqueue(proposal: ActionProposal) -> str | None:
    """Persist *proposal* to ``pending_actions`` (status ``pending``).

    Returns the row id (the proposal's own UUID) or ``None`` if the DB write
    fails. Call this for a ``NEEDS_APPROVAL`` disposition.
    """
    import json

    try:
        from acb_graph import get_session
        from sqlalchemy import text

        row_id = str(proposal.id)
        with get_session() as sess:
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
            sess.commit()
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
    """Return the pending approval queue (newest first). ``[]`` on DB failure."""
    try:
        from acb_graph import get_session
        from sqlalchemy import text

        with get_session() as sess:
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
    """Load a queued row and rebuild its :class:`ActionProposal` + current status."""
    from acb_graph import get_session
    from sqlalchemy import text

    with get_session() as sess:
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
    result: dict[str, Any] | None = None,
) -> None:
    """Update a queued row's status (+ reviewer / result). Best-effort."""
    import json

    try:
        from acb_graph import get_session
        from sqlalchemy import text

        reviewed_at_expr = "now()" if reviewed_by is not None else "reviewed_at"
        with get_session() as sess:
            sess.execute(
                text(
                    "UPDATE pending_actions SET status = :status, "
                    "reviewed_by = COALESCE(:reviewed_by, reviewed_by), "
                    f"reviewed_at = {reviewed_at_expr}, "
                    "result = CAST(:result AS jsonb) "
                    "WHERE id = :id"
                ),
                {
                    "id": action_id,
                    "status": status,
                    "reviewed_by": reviewed_by,
                    "result": json.dumps(result) if result is not None else None,
                },
            )
            sess.commit()
    except Exception:
        pass


def reject(action_id: str, reviewed_by: str) -> dict[str, Any]:
    """Reject a pending action — it is never executed. Audited."""
    _mark(action_id, "rejected", reviewed_by=reviewed_by)
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
