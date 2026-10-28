"""The job worker: the handler registry and the loop (WS-BRIEF BR-1a).

Reference: ``docs/job_queue.md``. The shape is ``gateway/reminders/loop.py``:
a tick every :data:`TICK_SECONDS`, the one tenant-discovery read of the
orgs, then ``tenant_session(org)`` for each org and a claim with ``FOR UPDATE
SKIP LOCKED``.

**The registry.** A consumer calls :func:`register` with a kind and an async
``handler(session, payload)``. The worker claims only the kinds that it holds
a handler for, so a job of an unknown kind waits and is never failed by a
worker that does not know it.

**One job, two transactions.**

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
from datetime import UTC, datetime
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

#: A handler takes the tenant session of the run and the payload.
Handler = Callable[[Any, dict[str, Any]], Awaitable[None]]

_HANDLERS: dict[str, Handler] = {}

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


def register(kind: str, handler: Handler, *, replace: bool = False) -> None:
    """Run *handler* for each job of *kind*. A second handler of one kind is
    an error, unless *replace* is True."""
    queue.check_kind(kind)
    if kind in _HANDLERS and not replace:
        raise ValueError(f"a handler of kind {kind!r} is already registered")
    _HANDLERS[kind] = handler


def unregister(kind: str) -> None:
    """Forget the handler of *kind*. Safe when none is registered."""
    _HANDLERS.pop(kind, None)


def kinds() -> list[str]:
    """The kinds that this worker claims, in name order."""
    return sorted(_HANDLERS)


def _describe(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[: queue.ERROR_CHARS]


# ── One job ─────────────────────────────────────────────────────────────────


async def run_one(org: str, now: datetime) -> str | None:
    """Claim one job of *org* and run it. Answers the outcome: ``done``,
    ``retry``, ``failed``, ``lost`` (the claim was gone), ``refused`` (a row
    of another org), or None when no job is due."""
    held = kinds()
    async with tenant_session(org) as db:
        job = await queue.claim(db, held, now=now)
    if job is None:
        return None
    if job.organization_id != str(org):
        # RLS makes this impossible. Refuse all the same, and run nothing.
        _log.error("jobs.claim_wrong_org", job_id=job.id)
        return "refused"
    handler = _HANDLERS.get(job.kind)
    try:
        async with tenant_session(org) as db:
            if not await queue.hold(db, job):
                _log.info("jobs.lease_lost", job_id=job.id, kind=job.kind)
                return "lost"
            try:
                if handler is None:
                    raise LookupError(f"no handler of kind {job.kind}")
                async with db.begin_nested():
                    await handler(db, dict(job.payload))
            except Exception as exc:
                _log.warning("jobs.attempt_failed", job_id=job.id, kind=job.kind,
                             attempt=job.attempts, error=type(exc).__name__)
                state = await queue.fail(db, job, _describe(exc), now=now)
                return "lost" if state is None else ("retry" if state == "queued" else "failed")
            changed = await queue.complete(db, job, now=now)
            return "done" if changed == 1 else "lost"
    except Exception as exc:
        # The session itself broke. Settle the attempt in a new session.
        _log.warning("jobs.run_failed", job_id=job.id, error=type(exc).__name__)
        async with tenant_session(org) as db:
            state = await queue.fail(db, job, _describe(exc), now=now)
        return "lost" if state is None else ("retry" if state == "queued" else "failed")


# ── One org, and the tick ───────────────────────────────────────────────────


async def run_org(org: str, now: datetime | None = None) -> Counter[str]:
    """Up to :data:`BATCH` due jobs of *org*. Answers the count of each
    outcome. Opens no session when no handler is registered.

    The org is bound for the whole call, so a handler that opens
    ``tenant_session()`` with no argument acts for the org of its job."""
    at = now or _now()
    counts: Counter[str] = Counter()
    if not _HANDLERS:
        return counts
    token = bind_tenant(org)
    try:
        for _ in range(BATCH):
            if _stopping.is_set():
                break
            try:
                outcome = await run_one(org, at)
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


async def _loop() -> None:
    while not _stopping.is_set():
        try:
            await run_tick()
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
