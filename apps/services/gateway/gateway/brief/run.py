"""The ``brief.run`` handler: one brief, at most once (WS-BRIEF BR-2a-1,
``core_brief.md`` §4.2).

The handler is detached (``docs/job_queue.md`` §5a) and idempotent: the
gather reads mail, calendar, tasks and CRM through each app's own session,
and a session handler may open no second session. A retry is safe because
``brief_runs`` holds one row for each slot, and ``add_claims`` skips a
position that has a claim.

The order, for the run of *member* on *local_date*:

1. ``flags.org_allowed(org)``. Off, the job settles ``done`` with no row.
2. ``unattended.begin(org, "brief.brief", "<member>:<local_date>")``. A
   taken slot (``fresh=False``) ends the run. With the ledger off, ``begin``
   answers fresh with no id, and the UNIQUE of ``brief_runs`` holds once.
3. ``store.create_run``, and the link to the ledger row. A run that is past
   ``planned`` ends the job with no second gather.
4. ``loop.resolve``. A reason string skips the member. ``identity_failed``
   raises, so the queue retries.
5. ``loop.snapshot``. An incomplete read raises, so the queue retries, and
   no claim comes from a read that lost a source.
6. One claim for each shown card (``gather.claims``), then ``gathered``, the
   template lead (``plan.brief_lead``), and ``ready``. No card: ``empty``,
   no lead and no claims. The writes and ``complete`` commit together.
7. ``unattended.finish(ok)``.

On an exception before ``ready``, the handler gives the ledger slot back
(``unattended.release``) and raises, so a retry can begin the slot again. A
crash after ``begin`` leaves an orphan, and AUTO-2 marks it ``unknown``: that
brief is lost for the day on purpose, and BR-2d reports it.

This slice calls no model and sends nothing. ``delivered`` is BR-2c, and the
phrase step is BR-2a-2. The two kinds register at import, and
:func:`register_jobs` adds the seed hook and the prune hook.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from acb_common import get_logger
from acb_common.db import tenant_session

from gateway.brief import flags, gather, planner, store
from gateway.jobs import queue, unattended, worker
from gateway.reach import plan

_log = get_logger("brief.run")

#: The ledger job of a brief run: ``brief.<kind>``.
LEDGER_JOB = f"brief.{planner.KIND}"
#: The days a run stays (``core_brief.md`` §9 Q6, owner 2026-10-29).
RETENTION_DAYS = 90


class SnapshotIncomplete(Exception):
    """A provider or the state join failed, so the cards are not the truth.
    The queue retries the run."""


class IdentityReadFailed(Exception):
    """The identity read of the member failed. The queue retries the run."""


def _now() -> datetime:
    return datetime.now(UTC)


def _payload(payload: dict[str, Any]) -> tuple[str, date, datetime]:
    member = str(payload.get("member") or "").strip().lower()
    local_date = date.fromisoformat(str(payload["local_date"]))
    slot_at = datetime.fromisoformat(str(payload["slot_at"]))
    if not member or slot_at.tzinfo is None:
        raise ValueError("a brief.run payload names the member and an aware slot")
    return member, local_date, slot_at


async def _settle_done(org: str, claim: queue.Claim, now: datetime) -> None:
    async with tenant_session(org) as db:
        if await queue.hold(db, claim):
            await queue.complete(db, claim, now=now)


async def _gather(org: str, member: str, run: store.Run, claim: queue.Claim,
                  now: datetime) -> str:
    """Steps 4 to 6. Answers the final state, or ``skipped``/``lost``."""
    from gateway.reach import loop

    user = await loop.resolve(org, member)
    if user == "identity_failed":
        raise IdentityReadFailed(member)
    if isinstance(user, str):
        _log.info("brief.run_skipped", reason=user)
        await _settle_done(org, claim, now)
        return "skipped"
    snap = await loop.snapshot(org, user, now)
    if not snap.complete:
        raise SnapshotIncomplete(member)
    claims = gather.claims(snap.cards, now)
    parts = plan.brief_parts(snap.cards, run.local_date, now=now)
    async with tenant_session(org) as db:
        if not await queue.hold(db, claim):
            return "lost"
        if not claims or parts is None:
            await store.set_state(db, member, run.id, "empty", now=now, from_state="planned")
            state = "empty"
        else:
            await store.add_claims(db, member, run.id, claims)
            await store.set_state(db, member, run.id, "gathered", now=now,
                                  from_state="planned")
            await store.set_lead(db, member, run.id, plan.brief_lead(parts), source="template")
            await store.set_state(db, member, run.id, "ready", now=now, from_state="gathered")
            state = "ready"
        await queue.complete(db, claim, now=now)
    _log.info("brief.run_done", state=state, claims=len(claims))
    return state


async def run(org: str, payload: dict[str, Any], claim: queue.Claim) -> None:
    """The detached handler of :data:`planner.RUN_KIND`."""
    member, local_date, slot_at = _payload(payload)
    now = _now()
    if not flags.org_allowed(org):
        _log.info("brief.run_off")
        await _settle_done(org, claim, now)
        return
    ledger = await unattended.begin(org, LEDGER_JOB, f"{member}:{local_date.isoformat()}",
                                    scheduled_for=slot_at, now=now)
    if not ledger.fresh:
        _log.info("brief.slot_taken", state=ledger.state)
        await _settle_done(org, claim, now)
        return
    try:
        async with tenant_session(org) as db:
            created = await store.create_run(db, member, kind=planner.KIND,
                                             local_date=local_date, slot_at=slot_at)
            if ledger.id is not None:
                await store.link_run(db, member, created.run.id, ledger.id)
        if created.run.state != "planned":
            _log.info("brief.run_exists", state=created.run.state)
            await _settle_done(org, claim, now)
        else:
            await _gather(org, member, created.run, claim, now)
    except Exception:
        await unattended.release(org, ledger)
        raise
    await unattended.finish(org, ledger, state="ok", now=now)


async def prune(db: Any, today: date) -> None:
    """The ``worker.prune_hooks`` hook: the runs older than
    :data:`RETENTION_DAYS` go, with their claims."""
    deleted = await store.prune_runs(db, today - timedelta(days=RETENTION_DAYS))
    if deleted:
        _log.info("brief.pruned", deleted=deleted)


def register_jobs(*, replace: bool = False) -> None:
    """Register the two kinds, the seed hook and the prune hook. Safe to
    call twice: a hook is added once."""
    worker.register(planner.PLAN_KIND, planner.plan, mode="detached", idempotent=True,
                    replace=replace)
    worker.register(planner.RUN_KIND, run, mode="detached", idempotent=True,
                    replace=replace)
    if planner.seed not in worker.seed_hooks:
        worker.seed_hooks.append(planner.seed)
    if prune not in worker.prune_hooks:
        worker.prune_hooks.append(prune)


register_jobs()
