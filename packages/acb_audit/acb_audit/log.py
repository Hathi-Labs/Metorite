"""Append-only audit log.

Phase-0: writes to Postgres `audit_event` (schema in infra/postgres/01_schema.sql)
AND mirrors to structlog so traces show up locally even if the DB is down.
The Annealer (Phase 4) reads from this table to mine intervention patterns.

**The DB write is synchronous** — it goes through ``acb_graph.get_session()``,
which is a sync SQLAlchemy engine, and most callers here are sync code
(``orchestrator.executor``, the pull agents). But the gateway's route handlers
are ``async`` and call :func:`record` on the event loop, where a blocking
psycopg round-trip stalls *every* other request in the process for its duration
— and this write is best-effort, so it can stall them behind a DB that is
timing out. BO-10's remaining item. :func:`record` therefore keeps its sync
signature (25-odd call sites, most of them sync) and dispatches the write to a
worker thread **only when it is called from a running event loop**.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from acb_common import get_logger

_log = get_logger("acb_audit")

#: Strong references to in-flight off-loop writes.
#:
#: ``asyncio`` holds only a weak reference to a task, so a fire-and-forget task
#: with no other referent can be garbage-collected mid-write. Discarded by the
#: done-callback, so this is bounded by concurrency, not by uptime.
_PENDING: set[asyncio.Task[None]] = set()


@dataclass(slots=True)
class AuditEvent:
    actor: str                                # e.g. "agent:sales", "user:vijay@..."
    action: str                               # e.g. "draft_email", "approve", "reject"
    target: str                               # e.g. "deal:<uuid>"
    payload: dict[str, Any] = field(default_factory=dict)
    id: UUID = field(default_factory=uuid4)
    at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    #: The owning tenant, captured ON THE EVENT by the caller (WS-29 acb_graph
    #: slice 5). It rides here — not a ContextVar — so :func:`_persist` can bind
    #: the right tenant AFTER the ``asyncio.to_thread`` hop, which does not carry
    #: a ContextVar reliably (the design mandates never relying on that copy).
    #: ``None`` with ``system`` set ⇒ a tenant-less system event, bound to the
    #: operator/DEFAULT org by :func:`_persist` so the row is RETAINED.
    #: ``None`` with no ``system`` ⇒ :func:`record` refuses the event while
    #: ``ACB_GRAPH_TENANT_BIND`` is on (task 2221 slice 3).
    organization_id: str | None = None
    #: The explicit system marker (task 2221 slice 3, owner answer A). Set it
    #: ONLY on an event that no tenant can own: a deployment-level tick with
    #: no tenant data in its payload. With the bind on, the operator org takes
    #: a tenant-less event only when this is ``True``. :func:`record` refuses
    #: any other tenant-less event, so a writer that forgets the org loses the
    #: row and never files a tenant's text under the operator org. An event
    #: with an org ignores the marker. The column is not stored. Fence:
    #: ``tests/unit/test_audit_tick_writers_org.py`` names each ``system=True``.
    system: bool = False


#: The operator/DEFAULT org id, resolved at most once and cached. A tenant-less
#: system/cron audit event is BOUND to this org (not skipped) so it is RETAINED
#: under FORCE RLS — the owner-ratified Option-A for WS-29 acb_graph slice 5.
_OPERATOR_ORG_ID: str | None = None


def _resolve_operator_org_id() -> str | None:
    """The operator/DEFAULT organization id, resolved at most once and cached.

    Resolution order:
      1. the ``OPERATOR_ORG_ID`` env — an explicit operator override.
      2. the RLS-EXEMPT ``organization`` table row with ``slug = 'default'``
         (readable on the unbound ``get_session`` even under phase-4 FORCE RLS —
         ``gen_tenant_migration.EXEMPT``; the ``default`` org is seeded by
         migration 130), the SAME source ``acb_auth.access.ensure_owner_bootstrap``
         resolves the bootstrap org from.

    A ``None``/absent lookup returns ``None`` WITHOUT caching, so a transient DB
    outage at first-audit does not permanently disable the fallback; the first
    successful answer is cached for the life of the process. A ``slug = 'default'``
    fallback is CORRECT here (unlike the acb_llm credential path, which must
    fail closed): this is audit RETENTION, not key service — it never serves one
    tenant's secrets to another, it only files a tenant-less system row where the
    operator can see it.
    """
    global _OPERATOR_ORG_ID
    if _OPERATOR_ORG_ID:
        return _OPERATOR_ORG_ID
    env = os.getenv("OPERATOR_ORG_ID", "").strip()
    if env:
        _OPERATOR_ORG_ID = env
        return _OPERATOR_ORG_ID
    try:
        from acb_graph import get_session
        from sqlalchemy import text

        with get_session() as s:
            row = s.execute(
                text("SELECT id::text FROM organization WHERE slug = 'default'")
            ).first()
        if row and row[0]:
            _OPERATOR_ORG_ID = str(row[0])
            return _OPERATOR_ORG_ID
    except Exception as exc:  # a lookup failure must never break audit
        _log.warning("audit.operator_org_lookup_failed", error=str(exc))
    return None


def _persist(event: AuditEvent) -> None:
    """The blocking half: one row, its own session, never raises.

    WS-29 acb_graph slice 5 — tenant-bind ``audit_event`` behind
    ``ACB_GRAPH_TENANT_BIND`` (the ONE reader in ``acb_graph.db``; default OFF ⇒
    byte-identical):

    * flag OFF → the unbound :func:`acb_graph.get_session`, exactly as before —
      pre-phase-4 ``audit_event`` has no ``organization_id`` column at all.
    * flag ON  → :func:`acb_graph.tenant_session` bound to
      ``event.organization_id`` (captured onto the EVENT on the calling frame —
      never a ContextVar read here, this may run on a worker thread) OR, for a
      tenant-less event that carries the ``system`` marker, the operator/DEFAULT
      org so the row is RETAINED under FORCE RLS. A tenant-less event with no
      marker never takes the operator org: :func:`record` refuses it first,
      and this skips it too, for a direct caller (task 2221 slice 3). The GUC
      bind stamps the column through its phase-1 DEFAULT — the ORM INSERT names
      no ``organization_id`` (the §1.3 "no INSERT had to learn about
      organization_id" property the chat_session/pending_commit paths also rely
      on). The write is SKIPPED and logged (``audit.persist_skipped_no_org``)
      when the event has no org and either has no marker or the operator-org
      lookup fails. So a tenant-less row is lost, never filed under the wrong
      tenant, and only the log line keeps its action and actor.
    """
    try:
        # Local import avoids a hard dep from acb_audit -> acb_graph at import time
        # (acb_audit is intentionally usable without the DB layer).
        from acb_graph import get_session, tenant_bind_enabled, tenant_session
        from acb_graph.models import AuditEvent as AuditRow

        if tenant_bind_enabled():
            org = event.organization_id or (
                _resolve_operator_org_id() if event.system else None
            )
            if not org:
                _log.warning(
                    "audit.persist_skipped_no_org",
                    action=event.action,
                    actor=event.actor,
                )
                return
            session_cm = tenant_session(org)
        else:
            session_cm = get_session()

        with session_cm as s:
            s.add(
                AuditRow(
                    id=event.id,
                    at=event.at,
                    actor=event.actor,
                    action=event.action,
                    target=event.target,
                    payload=event.payload,
                )
            )
    except Exception as exc:  # never block the caller on audit-DB failures
        _log.warning("audit.persist_failed", error=str(exc))


def refuse_tenantless(event: AuditEvent) -> bool:
    """``True`` when a TENANT event has no org and must not be recorded (task 2189).

    Some events always belong to one tenant: a broker action, a model call, a
    meeting dispatch. Their payload names that tenant's work. Before task
    2221 slice 3, :func:`_persist` filed an event with no org under the
    OPERATOR org (Option-A). For a tenant event that is a cross-tenant leak,
    so the writer calls this first::

        if refuse_tenantless(event):
            return
        record(event)

    With ``ACB_GRAPH_TENANT_BIND`` on and no ``organization_id`` on the event,
    it logs ``audit.skipped_no_tenant`` and answers ``True``. The writer then
    records nothing. With the flag off, ``audit_event`` has no tenant column,
    so nothing can leak, and it answers ``False``: the old row is kept. If
    ``acb_graph`` cannot load, it fails closed and answers ``True``.

    The Option-A fallback in :func:`_persist` stays for real system events,
    and only for an event with the ``system`` marker. This check ignores the
    marker: a writer that calls it holds tenant events only, so a marked
    event that comes here is refused too. :func:`record` refuses an unmarked
    tenant-less event by itself (task 2221 slice 3), so this check is a
    second layer for the writers that already call it. Fences:
    ``tests/unit/test_action_broker_audit_org_r8.py``,
    ``test_llm_usage_audit_org_r8.py``, ``test_notes_dispatch_audit_org_r8.py``.
    """
    if event.organization_id:
        return False
    try:
        from acb_graph import tenant_bind_enabled

        bind = tenant_bind_enabled()
    except Exception:  # no DB layer: fail closed, never the operator org
        bind = True
    if not bind:
        return False
    _log.warning(
        "audit.skipped_no_tenant",
        action=event.action,
        actor=event.actor,
        target=event.target,
    )
    return True


def _writer_module() -> str | None:
    """The module that called :func:`record`, for the refusal log line only."""
    try:
        return sys._getframe(3).f_globals.get("__name__")
    except Exception:  # an odd frame stack must never break audit
        return None


def _refuse_unmarked(event: AuditEvent) -> bool:
    """``True`` when :func:`record` must refuse *event* (task 2221 slice 3).

    Owner answer A. With ``ACB_GRAPH_TENANT_BIND`` on, an event with no
    ``organization_id`` and no ``system`` marker is refused. The writer
    forgot its org, and the operator org must never take a tenant's text.
    This logs ``audit.refused_unmarked_tenantless`` with the action, the
    actor and the writer module. It never logs the target or the payload,
    because those can hold tenant data. It never raises into the caller.
    With the bind off, nothing changes. If ``acb_graph`` cannot load, it
    fails closed and refuses, like :func:`refuse_tenantless`.
    """
    if event.organization_id or event.system:
        return False
    try:
        from acb_graph import tenant_bind_enabled

        bind = tenant_bind_enabled()
    except Exception:  # no DB layer: fail closed, never the operator org
        bind = True
    if not bind:
        return False
    # A broken logger must never raise into the writer.
    with contextlib.suppress(Exception):
        _log.warning(
            "audit.refused_unmarked_tenantless",
            action=event.action,
            actor=event.actor,
            writer=_writer_module(),
        )
    return True


def record(event: AuditEvent) -> None:
    """Persist an audit event. Logs always; DB write is best-effort.

    ⚠️ One exception (task 2221 slice 3, owner answer A): with
    ``ACB_GRAPH_TENANT_BIND`` on, an event with no ``organization_id`` and no
    ``system`` marker is REFUSED. Nothing is written and the ``audit`` line
    with the payload is not logged. :func:`_refuse_unmarked` logs the
    refusal, and the caller gets no error. With the bind off, every event is
    written as before.

    Synchronous by signature, and synchronous in fact for sync callers. On an
    event loop the DB write is handed to a worker thread and this returns
    immediately, so an async handler is never blocked by it.

    ⚠️ What that costs, stated plainly: for an async caller the row is no longer
    written by the time this returns. Nothing depended on that ordering — the
    write has always been in its own session, outside the caller's transaction,
    and has always been allowed to fail silently (see
    ``routes/admin/members.py::purge_member``, which reasons explicitly about
    both) — but a caller that wants the row *now* must ``await drain()``.
    """
    if _refuse_unmarked(event):
        return
    _log.info("audit", **asdict(event))
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No event loop: we are sync code (or already on a worker thread).
        # Blocking here blocks only this caller, which is what it asked for.
        _persist(event)
        return

    task = loop.create_task(asyncio.to_thread(_persist, event))
    _PENDING.add(task)
    task.add_done_callback(_PENDING.discard)


async def drain(timeout: float = 5.0) -> None:
    """Wait for off-loop audit writes to finish. Call on shutdown.

    Without this, a process that exits right after a write cancels the task and
    loses the row — which would be a regression against the old blocking
    behaviour, where the write was complete before the handler returned. The
    timeout is a bound, not a promise: a wedged audit DB must not hold the
    shutdown open, and a lost audit row is by contract survivable.
    """
    pending = [t for t in _PENDING if not t.done()]
    if not pending:
        return
    _done, still_running = await asyncio.wait(pending, timeout=timeout)
    if still_running:
        _log.warning("audit.drain_timeout", pending=len(still_running))
