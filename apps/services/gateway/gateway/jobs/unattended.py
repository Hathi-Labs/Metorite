"""The ledger of unattended runs (WS-BRIEF AUTO-2, Metorite #814).

Reference: ``docs/job_queue.md`` §9. Tables: migration ``unattended_runs``
and ``unattended_incidents`` (313 at build). This module is the ONE writer of
both tables.

**At most once for each slot.** :func:`begin` inserts the row of one run of a
job in one slot, and its session commits BEFORE the caller starts the work.
A second :func:`begin` of the same slot answers ``fresh=False``, so a restart
finds the slot taken and does not run the job again. The unique key is
``(organization_id, job, slot)``.

**A run with no end.** A ``started`` row whose runner is gone (another
process, so a restart) or whose ``progress_at`` is older than
:data:`STALL_AFTER` is an orphan. :func:`recover` and :func:`begin` mark it
``unknown`` and never run it again. A send that possibly reached the
provider is ``unknown`` too, and nobody retries it.

**Incidents.** :func:`record_failure` keeps one open incident for each job
and error class of an org, with a count and ``last_seen``. The first failure
logs ``unattended.incident_opened`` at error. A repeat logs at debug only, so
one incident gives one alert line.

**The stall guard.** :func:`run_guarded` runs the work with a
:class:`StallGuard`. Work that does not call ``guard.progress()`` for
:data:`STALL_AFTER` is cancelled, and its row turns ``stalled``.

**Tenancy (R5).** Each function opens ``tenant_session(org)`` with the org
that the caller resolved from its own list, never from request input. No
statement names an org: RLS binds each one, and the column DEFAULT writes it.

**Dark by default.** With ``unattended_ledger`` off, :func:`begin` answers a
run with no id and writes nothing, and each other function does nothing. The
one reader of the switch is :func:`ledger_enabled`.

**The clock.** Each function takes a ``now``. A test freezes it.

Fence: ``tests/unit/test_unattended_ledger.py`` (R8 and a fake clock).
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import uuid
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from acb_common import get_logger, get_settings
from acb_common.db import tenant_session
from sqlalchemy import text

_log = get_logger("jobs.unattended")


#: The five states, as migration 313's CHECK ``unattended_runs_state`` holds them.
STATES: tuple[str, ...] = ("started", "ok", "failed", "unknown", "stalled")
#: A run with no progress for this long is stalled, or an orphan.
STALL_AFTER = timedelta(minutes=10)
#: The seconds between two checks of :func:`run_guarded`.
POLL_SECONDS = 30.0
#: The error class of a run whose outcome nobody knows.
UNKNOWN_CLASS = "OutcomeUnknown"
#: The error class of a run that the stall guard stopped.
STALLED_CLASS = "Stalled"
#: This process. A restart has a new value, so the rows of the earlier
#: process read as orphans.
RUNNER = str(uuid.uuid4())

#: The CHECKs ``unattended_runs_job`` and ``unattended_runs_error_class``.
_JOB = re.compile(r"[a-z][a-z0-9_.]{0,63}")
_ERROR_CLASS = re.compile(r"[A-Za-z][A-Za-z0-9_.:]{0,63}")
#: The bound of the CHECK ``unattended_runs_slot``.
SLOT_CHARS = 200

_BEGIN_SQL = (
    "INSERT INTO unattended_runs (job, slot, scheduled_for, runner, started_at, "
    "progress_at) VALUES (:j, :s, :at, CAST(:r AS uuid), :now, :now) "
    "ON CONFLICT (organization_id, job, slot) DO NOTHING RETURNING id::text"
)

_STATE_SQL = "SELECT id::text, state FROM unattended_runs WHERE job = :j AND slot = :s"

#: An orphan: a started run of another runner, or with no progress since
#: the cutoff. The state check makes the mark happen once.
_ORPHAN_SQL = (
    "UPDATE unattended_runs SET state = 'unknown', error_class = :e, "
    "finished_at = :now WHERE job = :j AND slot = ANY(CAST(:slots AS text[])) "
    "AND state = 'started' AND (runner <> CAST(:r AS uuid) OR progress_at < :cutoff) "
    "RETURNING id::text"
)

_FINISH_SQL = (
    "UPDATE unattended_runs SET state = :st, error_class = :e, finished_at = :now, "
    "progress_at = GREATEST(progress_at, :now) "
    "WHERE id = CAST(:id AS uuid) AND state = 'started'"
)

_TOUCH_SQL = (
    "UPDATE unattended_runs SET progress_at = GREATEST(progress_at, :now) "
    "WHERE id = CAST(:id AS uuid) AND state = 'started'"
)

_INCIDENT_SQL = (
    "INSERT INTO unattended_incidents (job, error_class, first_seen, last_seen) "
    "VALUES (:j, :e, :now, :now) "
    "ON CONFLICT (organization_id, job, error_class) WHERE state = 'open' "
    "DO UPDATE SET count = unattended_incidents.count + 1, "
    "last_seen = GREATEST(unattended_incidents.last_seen, EXCLUDED.last_seen) "
    "RETURNING id::text, count"
)

_LINK_SQL = (
    "UPDATE unattended_runs SET incident_id = CAST(:inc AS uuid) "
    "WHERE id = CAST(:id AS uuid)"
)


class LedgerError(ValueError):
    """A job name or a slot that the ledger refuses."""


class OutcomeUnknown(Exception):
    """Raise it from the work of :func:`once` when a send possibly ran. The
    run turns ``unknown``, and nobody retries it."""


class RunStalled(Exception):
    """:func:`run_guarded` stopped work that made no progress."""


@dataclass(frozen=True)
class Run:
    """The answer of :func:`begin`.

    ``fresh`` is True when this call began the run, so the caller does the
    work. False means that the slot is taken, and ``state`` is the state of
    the earlier run. ``id`` is None when the ledger is off."""

    id: str | None
    job: str
    slot: str
    fresh: bool
    state: str


@dataclass(frozen=True)
class Incident:
    """One open incident after :func:`record_failure`. ``opened`` is True on
    the first failure only."""

    id: str
    count: int
    opened: bool


def ledger_enabled() -> bool:
    """The switch, ``unattended_ledger`` (default off). The ONE reader of it."""
    return getattr(get_settings(), "unattended_ledger", False) is True


def _now() -> datetime:
    return datetime.now(UTC)


def check_job(job: object) -> str:
    """Answers *job* when it matches the CHECK, else raises."""
    if not isinstance(job, str) or not _JOB.fullmatch(job):
        raise LedgerError("job must match [a-z][a-z0-9_.]{0,63}")
    return job


def check_slot(slot: object) -> str:
    """Answers *slot* when it is 1 to :data:`SLOT_CHARS` characters, else raises."""
    if not isinstance(slot, str) or not 1 <= len(slot) <= SLOT_CHARS:
        raise LedgerError(f"slot must be 1 to {SLOT_CHARS} characters")
    return slot


def error_class(value: object) -> str:
    """A legal error class: an exception's type name, or a short reason.
    Anything else is ``Error``, so no text of a failure reaches the row."""
    name = type(value).__name__ if isinstance(value, BaseException) else str(value)
    return name if _ERROR_CLASS.fullmatch(name) else "Error"


# ── The ledger ──────────────────────────────────────────────────────────────


async def begin(org: str, job: str, slot: str, *, scheduled_for: datetime | None = None,
                now: datetime | None = None) -> Run:
    """Insert the row of one run, and commit it before the caller works.

    A slot that is taken answers ``fresh=False``. An orphan of the slot is
    marked ``unknown`` first (:func:`recover`), so the caller sees it."""
    check_job(job)
    check_slot(slot)
    if not ledger_enabled():
        return Run(None, job, slot, True, "started")
    at = now or _now()
    async with tenant_session(org) as db:
        row = (await db.execute(text(_BEGIN_SQL), {
            "j": job, "s": slot, "at": scheduled_for, "r": RUNNER, "now": at})).first()
        if row is not None:
            return Run(str(row[0]), job, slot, True, "started")
        marked = await _mark_orphans(db, job, [slot], at)
        got = (await db.execute(text(_STATE_SQL), {"j": job, "s": slot})).first()
    for run_id in marked:
        await _after_unknown(org, job, run_id, at)
    _log.info("unattended.slot_taken", job=job, state=got[1] if got else None)
    if got is None:  # deleted between the two statements
        return Run(None, job, slot, False, "unknown")
    return Run(str(got[0]), job, slot, False, str(got[1]))


async def recover(org: str, job: str, *slots: str, now: datetime | None = None) -> bool:
    """Mark the orphans of *slots* ``unknown``. True when one was marked.

    Call it before a check that would skip the slot for another reason, so
    a run that a crash cut is marked in every case."""
    check_job(job)
    for slot in slots:
        check_slot(slot)
    if not ledger_enabled() or not slots:
        return False
    at = now or _now()
    async with tenant_session(org) as db:
        marked = await _mark_orphans(db, job, list(slots), at)
    for run_id in marked:
        await _after_unknown(org, job, run_id, at)
    return bool(marked)


async def state_of(org: str, job: str, slot: str) -> str | None:
    """The state of the run of *slot*, or None: no row, or the ledger is off."""
    check_job(job)
    check_slot(slot)
    if not ledger_enabled():
        return None
    async with tenant_session(org) as db:
        got = (await db.execute(text(_STATE_SQL), {"j": job, "s": slot})).first()
    return None if got is None else str(got[1])


async def _mark_orphans(db: Any, job: str, slots: list[str], now: datetime) -> list[str]:
    rows = (await db.execute(text(_ORPHAN_SQL), {
        "e": UNKNOWN_CLASS, "now": now, "j": job, "slots": slots, "r": RUNNER,
        "cutoff": now - STALL_AFTER})).all()
    return [str(r[0]) for r in rows]


async def _after_unknown(org: str, job: str, run_id: str, now: datetime) -> None:
    _log.warning("unattended.run_unknown", job=job)
    await record_failure(org, job, UNKNOWN_CLASS, run_id=run_id, now=now)


async def finish(org: str, run: Run, *, state: str = "ok", error: object = None,
                 now: datetime | None = None) -> bool:
    """End a started run with *state*: ``ok``, ``failed``, ``unknown`` or
    ``stalled``. A state other than ``ok`` records a failure of *error*.
    True when the row changed. A run that already ended changes nothing."""
    if state not in STATES or state == "started":
        raise LedgerError(f"state must be one of {STATES[1:]}")
    if run.id is None or not ledger_enabled():
        return False
    at = now or _now()
    klass = None if state == "ok" else error_class(
        error if error is not None else {"unknown": UNKNOWN_CLASS,
                                         "stalled": STALLED_CLASS}.get(state, "Error"))
    async with tenant_session(org) as db:
        result: Any = await db.execute(text(_FINISH_SQL), {
            "st": state, "e": klass, "now": at, "id": run.id})
    changed = bool(getattr(result, "rowcount", 0))
    if changed and klass is not None:
        await record_failure(org, run.job, klass, run_id=run.id, now=at)
    elif not changed:
        _log.warning("unattended.finish_lost", job=run.job, state=state)
    return changed


async def mark_unknown(org: str, run: Run, *, now: datetime | None = None) -> bool:
    """End a started run as ``unknown``: a send that possibly reached the
    provider. Nobody retries it."""
    return await finish(org, run, state="unknown", error=UNKNOWN_CLASS, now=now)


async def touch(org: str, run: Run, *, now: datetime | None = None) -> None:
    """Move ``progress_at`` of a started run, so it does not read as an orphan."""
    if run.id is None or not ledger_enabled():
        return
    async with tenant_session(org) as db:
        await db.execute(text(_TOUCH_SQL), {"now": now or _now(), "id": run.id})


async def record_failure(org: str, job: str, error: object, *, run_id: str | None = None,
                         now: datetime | None = None) -> Incident | None:
    """Count one failure of *job* in the open incident of its error class.

    The first failure opens the incident and logs ``unattended.incident_opened``
    at error. A repeat adds 1 to the count and logs at debug only."""
    check_job(job)
    if not ledger_enabled():
        return None
    klass = error_class(error)
    async with tenant_session(org) as db:
        row = (await db.execute(text(_INCIDENT_SQL), {
            "j": job, "e": klass, "now": now or _now()})).one()
        if run_id is not None:
            await db.execute(text(_LINK_SQL), {"inc": row[0], "id": run_id})
    incident = Incident(str(row[0]), int(row[1]), int(row[1]) == 1)
    if incident.opened:
        _log.error("unattended.incident_opened", job=job, error_class=klass)
    else:
        _log.debug("unattended.incident_repeat", job=job, error_class=klass,
                   count=incident.count)
    return incident


# ── The stall guard ─────────────────────────────────────────────────────────


class StallGuard:
    """The progress of one run. The work calls :meth:`progress` as it moves."""

    def __init__(self, clock: Callable[[], datetime] = _now,
                 stall_after: timedelta = STALL_AFTER) -> None:
        self._clock = clock
        self.stall_after = stall_after
        self.last = clock()
        self.reported = self.last

    def progress(self) -> None:
        """The work moved. Cheap: it writes nothing."""
        self.last = self._clock()

    def stalled(self) -> bool:
        """True when the work made no progress for :data:`STALL_AFTER`."""
        return self._clock() - self.last >= self.stall_after


async def run_guarded[T](org: str, run: Run, work: Callable[[StallGuard], Coroutine[Any, Any, T]], *,
                      clock: Callable[[], datetime] = _now, poll: float = POLL_SECONDS,
                      stall_after: timedelta = STALL_AFTER) -> T:
    """Run ``work(guard)``. Stop it when it stalls, and mark the run
    ``stalled``. Raises :class:`RunStalled` then. Each poll that saw
    progress writes ``progress_at``."""
    guard = StallGuard(clock, stall_after)
    task: asyncio.Task[T] = asyncio.get_running_loop().create_task(
        work(guard), name=f"unattended-{run.job}")
    try:
        while True:
            done, _pending = await asyncio.wait({task}, timeout=poll)
            if task in done:
                return task.result()
            if guard.stalled():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
                await finish(org, run, state="stalled", error=STALLED_CLASS, now=clock())
                _log.warning("unattended.run_stalled", job=run.job)
                raise RunStalled(run.job)
            if guard.last > guard.reported:
                guard.reported = guard.last
                await touch(org, run, now=guard.last)
    finally:
        if not task.done():
            task.cancel()


async def once[T](org: str, job: str, slot: str, work: Callable[[StallGuard], Coroutine[Any, Any, T]], *,
               scheduled_for: datetime | None = None, clock: Callable[[], datetime] = _now,
               poll: float = POLL_SECONDS) -> tuple[Run, T | None]:
    """Run *work* at most once for *slot*. Answers the run and the result.

    A taken slot answers ``(run, None)`` and runs nothing. Work that returns
    ends ``ok``. :class:`OutcomeUnknown` ends ``unknown``. Another error ends
    ``failed`` and is raised again. A stall ends ``stalled``."""
    run = await begin(org, job, slot, scheduled_for=scheduled_for, now=clock())
    if not run.fresh:
        return run, None
    try:
        out = await run_guarded(org, run, work, clock=clock, poll=poll)
    except OutcomeUnknown:
        await mark_unknown(org, run, now=clock())
        return run, None
    except RunStalled:
        return run, None
    except Exception as exc:
        await finish(org, run, state="failed", error=exc, now=clock())
        raise
    await finish(org, run, state="ok", now=clock())
    return run, out
