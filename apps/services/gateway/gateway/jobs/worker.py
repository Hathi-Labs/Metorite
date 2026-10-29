"""The job worker: the handler registry and the loop (WS-BRIEF BR-1a, BR-1b).

Reference: ``docs/job_queue.md``. The shape is ``gateway/reminders/loop.py``:
a tick every :data:`TICK_SECONDS`, the one tenant-discovery read of the
orgs, then ``tenant_session(org)`` for each org and a claim with ``FOR UPDATE
SKIP LOCKED``.

**The registry.** A consumer calls :func:`register` with a kind, an async
handler and a mode. The worker claims only the kinds that it holds a handler
for, so a job of an unknown kind waits and is never failed by a worker that
does not know it.

**The two modes.** A ``session`` handler (the default) is
``handler(session, payload)``. It runs inside the session of the run. A
``detached`` handler is ``handler(org, payload, claim)``. It runs with NO
session open, so it may call a provider or a model. Use it for any work
that waits on a party outside the database (``docs/job_queue.md`` §5a). A
detached kind gets one try, unless it registers with ``idempotent=True``.

**A session job: one job, two transactions.**

1. The claim: ``tenant_session(org)``, :func:`queue.claim`, commit. The row
   is ``running`` with a lease and a new fence token.
2. The run: a second ``tenant_session(org)``. :func:`queue.hold` locks the
   row and checks the token. The handler runs in a SAVEPOINT on the same
   session. Then :func:`queue.complete` or :func:`queue.fail` settles the row
   in the same transaction. So the writes of a handler and ``done`` commit
   together, or neither does.

⚠️ A handler must not call ``commit()``. A commit ends ``SET LOCAL`` and the
row lock. A handler that raises fails one attempt, and the queue retries it
with a backoff.

⚠️ A handler must use only the session that it receives. A second session is
a second transaction: its writes commit even when the attempt fails, and its
locks can wait on the row lock of the run.

**A detached job.** The claim commits as above. Then the worker calls the
handler with no session open, inside the same time limit. The handler opens
its own short ``tenant_session(org)`` for each step: it commits an intent
mark on its domain row, calls the provider with no session, and then runs
:func:`queue.hold`, its writes and :func:`queue.complete` in one last
session. After the handler returns, the worker reads the row. A row that is
still ``running`` with this claim's token fails the attempt with
``Unsettled``. A handler that raises fails the attempt in a new session.
A handler that must not try again calls :func:`queue.give_up`.

**The settle clock.** The claim reads the clock, and each settle of the
worker reads it again after the handler returns. So the backoff of a slow
try starts at its end, and a try of 240 s does not come back at once.

**Retention.** The kind ``jobs.prune`` (:data:`PRUNE_KIND`) deletes the
``done`` and ``failed`` rows of one org that finished more than
``queue.RETENTION`` (30 days) ago. :func:`run_org` seeds one prune job for
each org on each UTC day, with the dedupe key ``prune:<day>``. A cache in
memory remembers the seeded day of each org, so a tick that seeded already
costs no statement. :data:`prune_hooks` runs after the delete, on the same
session, for the retention of another table.

**The time limit.** A handler runs for at most :data:`HANDLER_TIMEOUT`. Past
it, the worker cancels the handler, and the attempt fails as an error does.
The order ``HANDLER_TIMEOUT < queue.LEASE < idle_in_transaction_session_timeout``
is fenced, so one hung handler cannot stop the queue of every org. When the
limit passes while the handler waits on the database, the session breaks,
and the settle can raise a second error. The worker keeps the timeout as the
error of the attempt all the same, so ``last_error`` says ``HandlerTimeout``.

**The signals.** ``jobs.attempt_failed`` (warning) is a failed attempt that
the queue retries. ``jobs.failed`` (error) is a job that failed for good.
``jobs.lease_expired`` comes from :func:`queue.claim`. ``jobs.lease_lost``
(info) is a claim that another worker took. ``jobs.run_failed`` (warning) is
a session that broke during a run. ``jobs.claim_failed`` (warning) is a run
that raised out of :func:`run_one`. ``jobs.tick`` (info) sums one tick that
ran a job.

**Restart and a killed worker.** The state is in Postgres only. A worker
that dies mid-job drops its connection, so the row lock goes. The row stays
``running`` until ``lease_until`` passes, and then the next claim takes it.

**Tenancy (R5).** The orgs come from ``email_ingestion.scheduler.
_list_organizations``, the one discovery read that the reminder loop also
uses. Every statement runs inside ``tenant_session(org)``. The worker never
reads an org from a payload. A claimed row of another org is refused.

**Dark by default.** With ``job_queue`` off, :func:`start_job_worker` starts
no task and :func:`run_tick` does nothing.

Fence: ``tests/unit/test_job_queue.py`` (R8, frozen clock, two orgs, two
workers).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from acb_common import get_logger, get_settings
from acb_common.db import bind_tenant, release_tenant, tenant_session

from gateway.jobs import queue

_log = get_logger("jobs.worker")

#: Seconds between two ticks.
TICK_SECONDS = 15
#: The jobs that one org runs in one tick, at most.
BATCH = 20
#: The seconds that a stop waits for a tick in progress.
STOP_GRACE_SECONDS = 30
#: The seconds that one handler may run. It must stay below ``queue.LEASE``
#: and below the 600 s ``idle_in_transaction_session_timeout`` of
#: ``acb_common.db``. Fence: ``test_the_handler_timeout_is_below_the_lease_and_the_idle_timeout``.
HANDLER_TIMEOUT = 240.0

#: A ``session`` handler takes the tenant session of the run and the payload.
Handler = Callable[[Any, dict[str, Any]], Awaitable[None]]
#: A ``detached`` handler takes the org, the payload and the claim. It opens
#: its own sessions, and it settles the job itself.
DetachedHandler = Callable[[str, dict[str, Any], queue.Claim], Awaitable[None]]
#: A prune hook takes the session of the prune job and the UTC day.
PruneHook = Callable[[Any, date], Awaitable[None]]

#: The modes of :func:`register`.
MODES: tuple[str, ...] = ("session", "detached")


@dataclass(frozen=True)
class _Entry:
    """One registered kind: its handler, its mode, and whether a second try
    of a detached handler is safe."""

    handler: Any
    mode: str
    idempotent: bool = False


_HANDLERS: dict[str, _Entry] = {}

_task: asyncio.Task[None] | None = None
_stopping = asyncio.Event()


def worker_enabled() -> bool:
    """The switch, ``job_queue`` (default off). The ONE reader of it."""
    return bool(getattr(get_settings(), "job_queue", False))


def _now() -> datetime:
    """The worker's one clock. A test passes ``now`` instead."""
    return datetime.now(UTC)


async def _orgs() -> list[str]:
    from email_ingestion.scheduler import _list_organizations

    return [str(org) for org in await _list_organizations()]


# ── The registry ────────────────────────────────────────────────────────────


def register(kind: str, handler: Handler | DetachedHandler, *, mode: str = "session",
             idempotent: bool = False, replace: bool = False) -> None:
    """Run *handler* for each job of *kind*, in *mode* (``session`` or
    ``detached``). A second handler of one kind is an error, unless *replace*
    is True.

    *idempotent* binds the ``detached`` mode only, and it fails closed. When
    it is False, the worker gives up a claim of a second or later try before
    the handler runs (:data:`NOT_IDEMPOTENT`). Set it True only when a second
    try is safe, for example when the handler reads an intent mark first. A
    ``session`` handler needs no flag: a failed try rolls back its writes."""
    queue.check_kind(kind)
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    if not isinstance(idempotent, bool):
        raise ValueError("idempotent must be a bool")
    if kind in _HANDLERS and not replace:
        raise ValueError(f"a handler of kind {kind!r} is already registered")
    _HANDLERS[kind] = _Entry(handler, mode, idempotent)


def unregister(kind: str) -> None:
    """Forget the handler of *kind*. Safe when none is registered."""
    _HANDLERS.pop(kind, None)


def kinds() -> list[str]:
    """The kinds that this worker claims, in name order."""
    return sorted(_HANDLERS)


#: The ``last_error`` of a detached job that the worker gave up before a
#: second try, because its kind is not idempotent.
NOT_IDEMPOTENT = "not idempotent: refusing a second try"


class HandlerTimeout(Exception):
    """A handler ran over :data:`HANDLER_TIMEOUT`, and the worker cancelled it."""


class Unsettled(Exception):
    """A detached handler returned, and the row is still ``running`` with the
    token of its claim. The worker fails the attempt."""


class _ClaimGone(Exception):
    """``complete`` changed no row after ``hold`` passed. The raise rolls back
    the writes of the handler, so no side effect commits for a job that stays
    ``running``."""


def _describe(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[: queue.ERROR_CHARS]


class _Attempt:
    """What :func:`_call` saw in one attempt. ``timeout`` is set when the
    limit passed, so the outer settle of :func:`run_one` keeps the label
    after the broken session raised a second error."""

    def __init__(self) -> None:
        self.timeout: HandlerTimeout | None = None


async def _call(run: Callable[[], Awaitable[None]], attempt: _Attempt) -> None:
    """Await *run* for at most :data:`HANDLER_TIMEOUT` seconds.

    The timeout scope cancels the handler at the limit, in this same task, so
    a stop of the worker cancels the handler too. A TimeoutError that the
    handler raises itself stays its own error, so ``last_error`` tells the two
    apart. Any error after the limit passed is a :class:`HandlerTimeout`: a
    cancelled database call can raise a driver error instead of the
    cancellation."""
    limit = HANDLER_TIMEOUT
    scope = asyncio.timeout(limit)
    try:
        async with scope:
            await run()
    except Exception as exc:
        if scope.expired():
            attempt.timeout = HandlerTimeout(f"the handler timed out after {limit:g} s")
            raise attempt.timeout from (None if isinstance(exc, TimeoutError) else exc)
        raise


async def _settle_failure(db: Any, job: queue.Claim, exc: BaseException, org: str,
                          now: datetime) -> str:
    """Settle a failed attempt, and log it. ``jobs.failed`` (error) marks the
    last attempt, and ``jobs.attempt_failed`` (warning) marks a retry."""
    state = await queue.fail(db, job, _describe(exc), now=now)
    if state is None:
        _log.info("jobs.lease_lost", job_id=job.id, kind=job.kind)
        return "lost"
    if state == "failed":
        _log.error("jobs.failed", job_id=job.id, kind=job.kind, attempts=job.attempts,
                   org=org, error=type(exc).__name__)
        return "failed"
    _log.warning("jobs.attempt_failed", job_id=job.id, kind=job.kind,
                 attempt=job.attempts, org=org, error=type(exc).__name__)
    return "retry"


# ── One job ─────────────────────────────────────────────────────────────────


async def run_one(org: str, now: datetime, *,
                  clock: Callable[[], datetime] | None = None) -> str | None:
    """Claim one job of *org* and run it. Answers the outcome: ``done``,
    ``retry``, ``failed``, ``lost`` (the claim was gone), ``refused`` (a row
    of another org), or None when no job is due.

    *now* is the time of the claim. The settle reads *clock* again after the
    handler returns, so a slow try gets its backoff from the end of the try,
    and not from its start. With no *clock*, the settle uses *now*."""
    settled = clock if clock is not None else (lambda: now)
    held = kinds()
    async with tenant_session(org) as db:
        job = await queue.claim(db, held, now=now)
    if job is None:
        return None
    if job.organization_id != str(org):
        # RLS makes this impossible. Refuse all the same, and run nothing.
        _log.error("jobs.claim_wrong_org", job_id=job.id)
        return "refused"
    entry = _HANDLERS.get(job.kind)
    if entry is not None and entry.mode == "detached":
        return await _run_detached(org, job, entry, settled)
    attempt = _Attempt()
    try:
        async with tenant_session(org) as db:
            if not await queue.hold(db, job):
                _log.info("jobs.lease_lost", job_id=job.id, kind=job.kind)
                return "lost"
            try:
                if entry is None:
                    raise LookupError(f"no handler of kind {job.kind}")
                handler, payload = entry.handler, dict(job.payload)
                async with db.begin_nested():
                    await _call(lambda: handler(db, payload), attempt)
            except Exception as exc:
                return await _settle_failure(
                    db, job, attempt.timeout or exc, org, settled())
            if await queue.complete(db, job, now=settled()) != 1:
                # Raise inside the session, so the writes of the handler roll back.
                raise _ClaimGone(job.id)
            return "done"
    except _ClaimGone:
        _log.warning("jobs.complete_lost", job_id=job.id, kind=job.kind)
        return "lost"
    except Exception as exc:
        # The session itself broke. Settle the attempt in a new session. A
        # timeout that broke the session stays the error of the attempt.
        _log.warning("jobs.run_failed", job_id=job.id, error=type(exc).__name__)
        async with tenant_session(org) as db:
            return await _settle_failure(db, job, attempt.timeout or exc, org, settled())


#: The outcome of a detached job that the handler settled, by the row state.
_SETTLED = {"done": "done", "queued": "retry", "failed": "failed"}


async def _run_detached(org: str, job: queue.Claim, entry: _Entry,
                        settled: Callable[[], datetime]) -> str:
    """Run a detached handler with no session open, then check the row.

    A kind that is not idempotent gets one try only. A claim of a later try
    (a retry, or a claim after a lost lease) fails for good with
    :data:`NOT_IDEMPOTENT`, and the handler does not run. The handler settles
    the job in its own session. A handler that raises fails the attempt in a
    new session. A handler that returns and leaves the row ``running`` with
    this token fails the attempt as ``Unsettled``. A row that another claim
    holds is ``lost``. Each settle of the worker reads *settled* at its own
    time, after the handler."""
    if not entry.idempotent and job.attempts > 1:
        async with tenant_session(org) as db:
            if await queue.give_up(db, job, NOT_IDEMPOTENT, now=settled()):
                return "failed"
        _log.info("jobs.lease_lost", job_id=job.id, kind=job.kind)
        return "lost"
    handler: DetachedHandler = entry.handler
    attempt = _Attempt()
    try:
        await _call(lambda: handler(org, dict(job.payload), job), attempt)
    except Exception as exc:
        async with tenant_session(org) as db:
            return await _settle_failure(db, job, attempt.timeout or exc, org, settled())
    async with tenant_session(org) as db:
        row = await queue.get_job(db, job.id)
        if row is not None and row.state == "running" and str(row.locked_by) == job.token:
            return await _settle_failure(
                db, job, Unsettled("the handler returned without settling"), org, settled())
    if row is None or row.state not in _SETTLED:
        _log.info("jobs.lease_lost", job_id=job.id, kind=job.kind)
        return "lost"
    return _SETTLED[str(row.state)]


# ── Retention ───────────────────────────────────────────────────────────────

#: The kind of the daily retention job.
PRUNE_KIND = "jobs.prune"
#: The tries of one prune job.
PRUNE_ATTEMPTS = 3
#: More retention, on the session of the prune job, after the ``jobs`` rows.
#: Each hook is ``async hook(db, today)``. A hook that raises fails the prune
#: attempt, so its writes and the delete of the ``jobs`` rows roll back
#: together. BR-4 adds the retention of ``brief_runs`` here.
prune_hooks: list[PruneHook] = []
#: The UTC day that :func:`run_org` last seeded a prune job for, by org.
_PRUNE_SEEDED: dict[str, str] = {}


async def prune_job(db: Any, payload: dict[str, Any]) -> None:
    """The ``session`` handler of :data:`PRUNE_KIND`. It deletes the old
    ``done`` and ``failed`` rows of the bound org, then runs each hook of
    :data:`prune_hooks`."""
    now = _now()
    deleted = await queue.prune(db, now=now)
    for hook in list(prune_hooks):
        await hook(db, now.date())
    if deleted:
        _log.info("jobs.pruned", deleted=deleted)


async def _seed_prune(org: str, now: datetime) -> None:
    """Queue the prune job of *org* for the UTC day of *now*, once a day.

    The cache answers each later tick of the day with no statement. The
    dedupe key keeps one live job when two workers seed the same day."""
    today = now.astimezone(UTC).date().isoformat()
    if _PRUNE_SEEDED.get(org) == today:
        return
    async with tenant_session(org) as db:
        await queue.enqueue(db, PRUNE_KIND, {}, dedupe_key=f"prune:{today}",
                            max_attempts=PRUNE_ATTEMPTS, now=now)
    _PRUNE_SEEDED[org] = today


# ── One org, and the tick ───────────────────────────────────────────────────


async def run_org(org: str, now: datetime | None = None, *,
                  clock: Callable[[], datetime] | None = None) -> Counter[str]:
    """Up to :data:`BATCH` due jobs of *org*. Answers the count of each
    outcome. Opens no session when no handler is registered.

    The cost of one call: one session for the daily prune seed, on the first
    tick of a UTC day only (``jobs.prune`` is registered at import). Then one
    claim session, and for each job that it claims, the sessions of the run.
    A call with no due job opens the one claim session only.

    The worker reads the clock for each claim, and again for each settle,
    because a try can run for minutes. *clock* replaces :func:`_now`. A fixed
    *now* freezes it.

    The org is bound for the whole call. A ``session`` handler gets its
    session as an argument and must use only that session. A ``detached``
    handler gets the org, and opens its own sessions."""
    if clock is None:
        clock = (lambda: now) if now is not None else _now
    counts: Counter[str] = Counter()
    if not _HANDLERS:
        return counts
    token = bind_tenant(org)
    try:
        if PRUNE_KIND in _HANDLERS:
            try:
                await _seed_prune(org, clock())
            except Exception as exc:
                _log.warning("jobs.prune_seed_failed", error=type(exc).__name__)
        for _ in range(BATCH):
            if _stopping.is_set():
                break
            try:
                outcome = await run_one(org, clock(), clock=clock)
            except Exception as exc:
                _log.warning("jobs.claim_failed", error=type(exc).__name__)
                counts["error"] += 1
                break
            if outcome is None:
                break
            counts[outcome] += 1
    finally:
        release_tenant(token)
    return counts


async def run_tick(now: datetime | None = None) -> dict[str, Counter[str]]:
    """One pass over every org. Never raises."""
    if not worker_enabled() or not _HANDLERS:
        return {}
    out: dict[str, Counter[str]] = {}
    for org in await _orgs():
        if _stopping.is_set():
            break
        try:
            counts = await run_org(org, now)
            if counts:
                out[org] = counts
        except Exception as exc:
            _log.warning("jobs.org_failed", error=type(exc).__name__)
    return out


def _log_tick(out: dict[str, Counter[str]]) -> None:
    """One info line for a tick that ran a job. A quiet tick logs nothing."""
    if not out:
        return
    total: Counter[str] = Counter()
    for counts in out.values():
        total.update(counts)
    _log.info("jobs.tick", orgs=len(out), **{k: total[k] for k in sorted(total)})


async def _loop() -> None:
    while not _stopping.is_set():
        try:
            _log_tick(await run_tick())
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _log.warning("jobs.loop_error", error=type(exc).__name__)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(_stopping.wait(), timeout=TICK_SECONDS)


async def start_job_worker() -> bool:
    """Start the worker when ``job_queue`` is on. Idempotent."""
    global _task
    if not worker_enabled():
        _log.info("jobs.worker_disabled")
        return False
    if _task is not None and not _task.done():
        return True
    _stopping.clear()
    _task = asyncio.get_running_loop().create_task(_loop(), name="jobs")
    _log.info("jobs.worker_started", tick_secs=TICK_SECONDS, kinds=len(_HANDLERS))
    return True


async def stop_job_worker() -> None:
    """Stop the worker and wait for a tick in progress. Safe when it never
    started."""
    global _task
    _stopping.set()
    task, _task = _task, None
    if task is None or task.done():
        return
    _done, pending = await asyncio.wait({task}, timeout=STOP_GRACE_SECONDS)
    for still in pending:
        still.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await still


register(PRUNE_KIND, prune_job)
