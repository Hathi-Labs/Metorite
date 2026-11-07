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
3. ``loop.resolve``, BEFORE any row exists. ``identity_failed`` raises, so
   the queue retries. A member who is gone gets no row: a ``planned`` row of
   an earlier try turns ``failed`` with the reason (``member_departed``).
4. The member's preferences, read again now (``assistant_profile.
   member_app_prefs``). A member who turned the brief off (``opted_out``)
   gets no brief. A member who moved the brief time away from this slot
   (``moved``) gets no brief at this slot. A ``planned`` row of an earlier
   try goes (``store.discard_planned``), so the day has no row, and the
   planner plans the new time.
5. ``store.create_run``, and the link to the ledger row of this try. A run
   that is past ``planned`` ends the job with no second gather.
6. ``loop.snapshot``. An incomplete read raises, so the queue retries, and
   no claim comes from a read that lost a source.
7. One claim for each shown card (``gather.claims``), then ``gathered``, the
   template lead (``plan.brief_lead``), and ``ready``. With the in-app
   surface on (``flags.in_app``), the run then moves ``ready`` to
   ``delivered`` (guarded) and stamps ``delivered_at``. Off, it stays
   ``ready``. No card: ``empty``, no lead and no claims. The writes and
   ``complete`` commit together.
8. ``unattended.finish(ok)``, only for a gather that committed or a run that
   was past ``planned``.

**The close-out** (BR-2b-1, ``core_brief.md`` §4.3). The payload may carry
``kind: "closeout"``, and a payload with no ``kind`` is a ``brief``. A
close-out runs the same steps with these changes. The switch is
``flags.closeout``. The ledger job is ``brief.closeout``. The slot is
checked against ``closeout_time``. Step 7 reads the snapshot for ``moved``
and ``handled.handled_run`` for ``done`` (:func:`_closeout_reads`). A
Handled read that is not ok raises :class:`HandledIncomplete`, and the queue
retries. The lead is the template lead (``gather.closeout_lead``), and
:func:`_composes` is False for a close-out, so it makes no model call.

**The acts** (BR-7b-2, ``core_brief.md`` §8). After ``done`` and ``moved``,
the close-out reads the acts of the member's day (:func:`_acted_reads`):
the allow-listed ``audit_event`` rows and the sent ``reach_outbox`` rows,
from 00:00 to 24:00 in the offset of the slot. They make the section
``acted`` (``gather.acted_claims``). A read that fails raises
:class:`ActedIncomplete`, and the queue retries.

**The ledger slot goes back** (``unattended.release``) when the run gathered
nothing: an exception, a cancel (the worker's ``HANDLER_TIMEOUT``), a skip,
or a lost claim. So a retry or a moved slot can begin it again. The release
runs under ``asyncio.shield``. A crash after ``begin`` leaves an orphan, and
AUTO-2 marks it ``unknown``: that brief is lost for the day on purpose, and
BR-2d reports it.

**Delivered** (BR-2c-1, ``core_brief.md`` §5). ``delivered`` means that
the run is on an in-app surface: ``GET /brief/today`` and the chat's first
turn serve it. The system sets it here, never a read, so a member who
never opens the app still has ``delivered_at``. BR-2d compares
``delivered_at`` with ``slot_at``. A "seen" signal is a separate
``seen_at``, which only an explicit POST from the card writes.

**The phrase step** (BR-2a-2, ``core_brief.md`` §4.4). It runs only when
``flags.compose(org)`` is on, the run has ``phrase.MIN_CLAIMS`` claims or
more, and ``acb_llm.routed.routing_is_on()``. Else step 7 is as above, byte
for byte, and no call runs. On:

7a. ONE session commits the claims, ``gathered``, the template lead, and
    ``phrasing`` with ``model_request_id`` (``store.mark_phrasing``, the
    intent mark). The job stays claimed, and the session closes.
7b. ``phrase.phrase`` makes the ONE call, with NO session open.
7c. A new session holds the job claim, moves ``phrasing`` to ``ready``,
    writes the model lead with its claim ids, delivers when
    ``flags.in_app`` is on, and completes the job. A fence refusal
    (``brief.phrase_refused``), a ``RoutedRefusal``, a timeout or any other
    fault keeps the template lead (``brief.phrase_failed``), and the run
    still reaches ``ready``.

A cancel during the call gives the ledger slot back, as above. The retry
finds the run in ``phrasing`` (:func:`_finish_stuck`). It makes no call,
keeps the template lead, or writes it from the stored claims, and reaches
``ready``. So one run makes at most one call.

``phrase.py`` is the one model site of the package. This module sends
nothing. The two kinds register at import, and :func:`register_jobs` adds
the seed hook and the prune hook.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from acb_common import assistant_profile, get_logger
from acb_common.db import tenant_session

from gateway.brief import flags, gather, phrase, planner, store
from gateway.jobs import queue, unattended, worker
from gateway.reach import plan

_log = get_logger("brief.run")

#: The ledger job of a brief run: ``brief.<kind>``.
LEDGER_JOB = f"brief.{planner.KIND}"
#: The ledger job of a close-out run (BR-2b-1).
CLOSEOUT_LEDGER_JOB = f"brief.{planner.CLOSEOUT}"
#: The time that the close-out gives the Handled read (``handled_run``).
#: The run is a job and not a feed read, so it may wait longer than the
#: feed's 1.5 seconds.
HANDLED_BUDGET_S = 10.0
#: The reason of a slot whose brief time moved. Its row goes, and the day
#: stays open for the new time.
MOVED = "moved"
#: The days a run stays (``core_brief.md`` §9 Q6, owner 2026-10-29).
RETENTION_DAYS = 90


class SnapshotIncomplete(Exception):
    """A provider or the state join failed, so the cards are not the truth.
    The queue retries the run."""


class HandledIncomplete(Exception):
    """A Handled read of the close-out failed (``HandledRun.ok`` is False),
    so the ``done`` list is not the truth. The queue retries the run."""


class ActedIncomplete(Exception):
    """A read of the acts of the close-out failed (BR-7b-2), so the
    ``acted`` list is not the truth. The queue retries the run."""


class IdentityReadFailed(Exception):
    """The identity read of the member failed. The queue retries the run."""


def _now() -> datetime:
    return datetime.now(UTC)


async def _give_back(org: str, ledger: unattended.Run) -> None:
    """Give the ledger slot back (``unattended.release``), shielded from a
    cancel. A release that fails is logged, and the caller's own error
    stays the error of the attempt."""
    try:
        await asyncio.shield(unattended.release(org, ledger))
    except Exception as exc:
        _log.warning("brief.release_failed", error=type(exc).__name__)


def kind_of(payload: dict[str, Any]) -> str:
    """The kind of the run that *payload* asks for. A payload with no
    ``kind`` is a ``brief``: a job queued before BR-2b-1 has none."""
    kind = str(payload.get("kind") or planner.KIND)
    if kind not in (planner.KIND, planner.CLOSEOUT):
        raise ValueError(f"a brief.run payload names the kind brief or closeout, not {kind!r}")
    return kind


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


#: The outcomes after which the ledger slot is done. Any other outcome
#: gathered nothing, so the slot goes back.
_FINISHED = frozenset({"ready", "delivered", "empty", "exists"})


async def _skip(org: str, member: str, local_date: date, claim: queue.Claim,
                now: datetime, reason: str, kind: str = planner.KIND) -> str:
    """End a slot that gets no brief, and make no row. A ``planned`` row of
    an earlier try turns ``failed`` with *reason*. For :data:`MOVED` the
    row goes instead, so the day stays open for the new time. Answers
    ``skipped``, or ``lost`` when the claim is gone."""
    _log.info("brief.run_skipped", reason=reason)
    async with tenant_session(org) as db:
        if not await queue.hold(db, claim):
            return "lost"
        found = await store.read_run(db, member, kind=kind, local_date=local_date)
        if found is not None and found.state == "planned":
            if reason == MOVED:
                await store.discard_planned(db, member, found.id)
            else:
                await store.set_state(db, member, found.id, "failed", now=now,
                                      error=reason, from_state="planned")
        await queue.complete(db, claim, now=now)
    return "skipped"


def _why_not(prefs: dict[str, Any], slot_at: datetime, kind: str = planner.KIND) -> str | None:
    """Why the member's preferences of now refuse this slot, or None. The
    slot carries the member's offset, so its clock reads their brief time
    (or their close-out time, for a close-out)."""
    if not prefs["enabled"]:
        return "opted_out"
    pref = "closeout_time" if kind == planner.CLOSEOUT else "brief_time"
    if slot_at.strftime("%H:%M") != prefs[pref]:
        return MOVED
    return None


def day_window(local_date: date, slot_at: datetime) -> tuple[datetime, datetime]:
    """The member's day of *local_date*, from 00:00 to 24:00, in the offset
    of *slot_at*. The planner made the slot in the member's zone, so its
    offset is the member's (BR-7b-2)."""
    if slot_at.tzinfo is None:
        raise ValueError("the day window needs an aware slot")
    since = datetime.combine(local_date, time.min, tzinfo=slot_at.tzinfo)
    return since, since + timedelta(days=1)


async def _acted_reads(org: str, member: str, local_date: date,
                       slot_at: datetime) -> tuple[list[Any], list[Any]]:
    """The acts of the member's day (BR-7b-2): the allow-listed
    ``audit_event`` rows (``acb_audit.read.acts``) and the sent
    ``reach_outbox`` rows (``outbox.sent_rows``). The org comes from the
    worker and the member from the payload, never from request input. A
    read that fails raises :class:`ActedIncomplete`, so the queue retries,
    as a Handled read that is not ok does."""
    from acb_audit import read as audit_read

    from gateway.reach.outbox import sent_rows

    since, until = day_window(local_date, slot_at)
    try:
        acts = await asyncio.to_thread(audit_read.acts, org, member, since, until)
        sent = await sent_rows(org, member, since, until)
    except Exception as exc:
        _log.warning("brief.acted_read_failed", error=type(exc).__name__)
        raise ActedIncomplete(member) from exc
    return acts, sent


async def _closeout_reads(org: str, member: str, user: Any, run: store.Run, snap: Any,
                          now: datetime, slot_at: datetime,
                          ) -> tuple[list[store.ClaimIn], str | None]:
    """The claims and the template lead of a close-out (BR-2b-1). The
    Handled read runs here, as the member, because ``loop.snapshot`` does not
    run it. A read that is not ok raises, so the queue retries. Then the
    acts of the day (BR-7b-2, :func:`_acted_reads`) make the section
    ``acted``, after ``done`` and ``moved``."""
    from gateway.routes.shell import handled

    closed = await handled.handled_run(user, now, budget_s=HANDLED_BUDGET_S)
    if not closed.ok:
        raise HandledIncomplete(member)
    claims = gather.closeout_claims(closed.closed, snap.cards, now)
    acts, sent = await _acted_reads(org, member, run.local_date, slot_at)
    acted = gather.acted_claims(acts, sent, snap.cards, closed.closed, len(claims))
    if acted:
        _log.info("brief.acted", acts=len(acted), no_policy=gather.no_policy_count(acted))
    claims += acted
    return claims, gather.closeout_lead(run.local_date, claims)


async def _gather(org: str, member: str, user: Any, run: store.Run, claim: queue.Claim,
                  now: datetime, slot_at: datetime) -> str:
    """Steps 6 and 7. Answers ``ready``, ``delivered``, ``empty``,
    ``exists`` or ``lost``. A close-out takes the claims of
    :func:`_closeout_reads`, and the rest is the same. *slot_at* is the
    aware slot of the payload, in the member's offset."""
    from gateway.reach import loop

    snap = await loop.snapshot(org, user, now)
    if not snap.complete:
        raise SnapshotIncomplete(member)
    if run.kind == planner.CLOSEOUT:
        claims, lead = await _closeout_reads(org, member, user, run, snap, now, slot_at)
    else:
        claims = gather.claims(snap.cards, now)
        parts = plan.brief_parts(snap.cards, run.local_date, now=now)
        lead = plan.brief_lead(parts) if parts is not None else None
    if claims and lead is not None and _composes(org, len(claims), run.kind):
        return await _gather_and_phrase(org, member, run, claim, claims, lead, now)
    async with tenant_session(org) as db:
        if not await queue.hold(db, claim):
            return "lost"
        if not claims or lead is None:
            await store.set_state(db, member, run.id, "empty", now=now, from_state="planned")
            state = "empty"
        else:
            await store.add_claims(db, member, run.id, claims)
            await store.set_state(db, member, run.id, "gathered", now=now,
                                  from_state="planned")
            await store.set_lead(db, member, run.id, lead, source="template")
            await store.set_state(db, member, run.id, "ready", now=now, from_state="gathered")
            state = "ready"
            # BR-2c-1: on an in-app surface now, so the system delivers it.
            state = await _deliver(db, org, member, run.id)
        await queue.complete(db, claim, now=now)
    _log.info("brief.run_done", state=state, claims=len(claims))
    return state


async def _deliver(db: Any, org: str, member: str, run_id: str) -> str:
    """``delivered`` when the in-app surface is on and the guarded move of a
    ``ready`` run wins, else ``ready``. The stamp is the clock of this
    moment, not of the run's start, so BR-2d reads the true lag behind
    ``slot_at``."""
    if flags.in_app(org) and await store.set_state(
            db, member, run_id, "delivered", now=_now(), from_state="ready"):
        return "delivered"
    return "ready"


def _composes(org: str, count: int, kind: str = planner.KIND) -> bool:
    """True when the run phrases its lead (BR-2a-2): ``BRIEF_COMPOSE`` is
    on for the org, the run has :data:`phrase.MIN_CLAIMS` claims or more,
    and the Router is on. A call off the Router writes no usage row, so
    with the Router off the run makes no call (``core_brief.md`` §4.5). A
    close-out never phrases (BR-2b-1): its lead is the template lead."""
    from acb_llm import routed

    if kind != planner.KIND:
        return False
    return count >= phrase.MIN_CLAIMS and flags.compose(org) and routed.routing_is_on()


async def _block(db: Any, org: str, member: str) -> str:
    """The member's block for the prompt, read under a SAVEPOINT, so a
    failed read costs the block and never the claims. Empty on a fault."""
    try:
        async with db.begin_nested():
            return await phrase.member_block(db, org, member)
    except Exception as exc:
        _log.warning("brief.phrase_block_failed", error=type(exc).__name__)
        return ""


async def _gather_and_phrase(org: str, member: str, run: store.Run, claim: queue.Claim,
                             claims: list[store.ClaimIn], lead: str,
                             now: datetime) -> str:
    """Steps 7a to 7c. Answers ``ready``, ``delivered``, ``exists`` or
    ``lost``."""
    from acb_llm import routed

    ref = phrase.new_ref()
    async with tenant_session(org) as db:
        if not await queue.hold(db, claim):
            return "lost"
        await store.add_claims(db, member, run.id, claims)
        await store.set_state(db, member, run.id, "gathered", now=now, from_state="planned")
        if not await store.mark_phrasing(db, member, run.id, ref=ref):
            # Another try moved the run on. It owns the run, and no call runs.
            # This try writes no lead, so it cannot overwrite a model lead.
            _log.info("brief.phrase_skipped", reason="not_gathered")
            await queue.complete(db, claim, now=now)
            return "exists"
        await store.set_lead(db, member, run.id, lead, source="template")
        stored = await store.read_claims(db, member, run.id)
        block = await _block(db, org, member)
    # The session is closed: the call holds no connection and no lock.
    phrased: phrase.Phrased | None = None
    try:
        phrased = await phrase.phrase(member, stored, ref=ref, block=block)
    except routed.RoutedRefusal as exc:
        _log.warning("brief.phrase_failed", error="RoutedRefusal", router_status=exc.status)
    except Exception as exc:
        _log.warning("brief.phrase_failed", error=type(exc).__name__)
    async with tenant_session(org) as db:
        if not await queue.hold(db, claim):
            return "lost"
        state = await _ready(db, org, member, run.id, now, phrased)
        await queue.complete(db, claim, now=now)
    _log.info("brief.run_done", state=state, claims=len(stored),
              lead_source="model" if phrased is not None else "template")
    return state


async def _ready(db: Any, org: str, member: str, run_id: str, now: datetime,
                 phrased: phrase.Phrased | None) -> str:
    """Move ``phrasing`` to ``ready`` with a guard, write the model lead
    when there is one, and deliver. The template lead is in the row
    already. ``exists`` when another try moved the run on: its lead
    stays."""
    if not await store.set_state(db, member, run_id, "ready", now=now,
                                 from_state="phrasing"):
        return "exists"
    if phrased is not None:
        await store.set_lead(db, member, run_id, phrased.lead, source="model",
                             claim_ids=phrased.claim_ids)
    return await _deliver(db, org, member, run_id)


def _stored_lead(run: store.Run, claims: list[store.Claim]) -> str | None:
    """The template lead of a run from its stored claims, as
    ``chat_brief.stored_parts`` reads a stored run: the date title, one
    line for each claim, and no "more" count. None with no claim."""
    lines = [c.text for c in sorted(claims, key=lambda c: c.position)]
    if not lines:
        return None
    title = f"Your day, {plan._date_words(run.local_date)}"
    return plan.brief_lead(plan.BriefParts(title, lines, 0))


async def _finish_stuck(org: str, member: str, run: store.Run, claim: queue.Claim,
                        now: datetime) -> str:
    """A run that a try left in ``phrasing``: a cancel, or a crash during
    the call. The retry makes NO call (``core_brief.md`` §4.2 step 6). It
    keeps the template lead of the row, or writes one from the stored
    claims, moves ``phrasing`` to ``ready``, and delivers. Answers
    ``ready``, ``delivered``, ``exists`` or ``lost``."""
    async with tenant_session(org) as db:
        if not await queue.hold(db, claim):
            return "lost"
        if not (run.lead or "").strip():
            lead = _stored_lead(run, await store.read_claims(db, member, run.id))
            if lead is not None:
                await store.set_lead(db, member, run.id, lead, source="template")
        state = await _ready(db, org, member, run.id, now, None)
        await queue.complete(db, claim, now=now)
    _log.info("brief.phrase_recovered", state=state)
    return state


async def _run_slot(org: str, member: str, local_date: date, slot_at: datetime,
                    claim: queue.Claim, ledger: unattended.Run, now: datetime,
                    kind: str = planner.KIND) -> str:
    """Steps 3 to 7. Answers the outcome: ``ready``, ``delivered``,
    ``empty``, ``exists``, ``skipped`` or ``lost``."""
    from gateway.reach import loop

    user = await loop.resolve(org, member)
    if user == "identity_failed":
        raise IdentityReadFailed(member)
    if isinstance(user, str):
        return await _skip(org, member, local_date, claim, now, f"member_{user}", kind)
    async with tenant_session(org) as db:
        prefs = await assistant_profile.member_app_prefs(db, org, member, planner.APP)
        why = _why_not(prefs, slot_at, kind)
        if why is None:
            created = await store.create_run(db, member, kind=kind,
                                             local_date=local_date, slot_at=slot_at)
            if ledger.id is not None:
                await store.link_run(db, member, created.run.id, ledger.id)
    if why is not None:
        return await _skip(org, member, local_date, claim, now, why, kind)
    if created.run.state == "phrasing":
        return await _finish_stuck(org, member, created.run, claim, now)
    if created.run.state != "planned":
        _log.info("brief.run_exists", state=created.run.state)
        await _settle_done(org, claim, now)
        return "exists"
    return await _gather(org, member, user, created.run, claim, now, slot_at)


async def run(org: str, payload: dict[str, Any], claim: queue.Claim) -> None:
    """The detached handler of :data:`planner.RUN_KIND`."""
    member, local_date, slot_at = _payload(payload)
    kind = kind_of(payload)
    now = _now()
    if not (flags.closeout(org) if kind == planner.CLOSEOUT else flags.org_allowed(org)):
        _log.info("brief.run_off", kind=kind)
        await _settle_done(org, claim, now)
        return
    job = CLOSEOUT_LEDGER_JOB if kind == planner.CLOSEOUT else LEDGER_JOB
    ledger = await unattended.begin(org, job, f"{member}:{local_date.isoformat()}",
                                    scheduled_for=slot_at, now=now)
    if not ledger.fresh:
        _log.info("brief.slot_taken", state=ledger.state)
        await _settle_done(org, claim, now)
        return
    try:
        outcome = await _run_slot(org, member, local_date, slot_at, claim, ledger, now, kind)
    except BaseException:
        # A cancel is a BaseException: the worker's HANDLER_TIMEOUT cancels
        # the handler, and so does a stop. The slot goes back in each case,
        # under a shield, so a second cancel cannot cut the release. The
        # ``brief_runs`` row stays ``planned``, so the retry gathers it, or
        # ``phrasing``, so the retry takes the template lead with no call.
        await _give_back(org, ledger)
        raise
    if outcome in _FINISHED:
        await unattended.finish(org, ledger, state="ok", now=now)
    else:
        # Nothing was gathered: a skip, or a claim that another try holds.
        await _give_back(org, ledger)


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
                    replace=replace, effect="own_rows")
    worker.register(planner.RUN_KIND, run, mode="detached", idempotent=True,
                    replace=replace, effect="own_rows")
    if planner.seed not in worker.seed_hooks:
        worker.seed_hooks.append(planner.seed)
    if prune not in worker.prune_hooks:
        worker.prune_hooks.append(prune)


register_jobs()
