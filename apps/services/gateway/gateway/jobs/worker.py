"""The job worker: one tick over every org, one job at a time (WS-BRIEF BR-1a).

Spec: ``project-docs/specs/job_queue.md`` §5. The shape is
``gateway/reminders/loop.py``: a tick every :data:`TICK_SECONDS`, the one
tenant-discovery read of the orgs, then ``bind_tenant(org)`` and
:func:`run_org` for each org.

**One job** (:func:`_run_one`). A job past its try limit goes ``dead`` with
``lease_expired`` and its handler does not run. A kind with no handler goes
``dead`` with ``unknown_kind``. The handler runs under
``asyncio.wait_for(..., HANDLER_TIMEOUT_S)`` with no session open. A
``CancelledError`` raises again with no settle, so the lease carries the job
across a restart. ``PermanentJobError`` settles ``dead``. Any other error and
a timeout settle ``retry``, or ``dead`` at the try limit. A return settles
``succeeded``.

The handler has closed its sessions when its exception gets here. So the
``except`` of :func:`_run_one` swallows no error inside a session.

**Tenancy (R5, H4).** The orgs come from
``email_ingestion.scheduler._list_organizations``, the one unbound read that
``reminders/loop.py`` uses too. This module adds no ``get_db()`` site. The
worker binds the org that discovery returned, the claim and the settle run in
``tenant_session(org)``, and the handler gets ``job.organization_id`` from the
row. Nothing reads an org from a payload or a request.

**Dark by default.** With ``job_queue_enabled`` off,
:func:`start_job_worker` starts no task and :func:`run_tick` does nothing.
:func:`worker_enabled` is the one reader of the flag. ``enqueue`` still
writes, so a caller can be built and tested dark.

**Scale.** Each tick opens at least one transaction for each org, every
:data:`TICK_SECONDS`. That is fine for tens of orgs. Hundreds of orgs need a
discovery read of the orgs that have due jobs (a later slice).

Fences: ``tests/unit/test_job_queue_r8.py`` (R8) and
``tests/unit/test_job_queue.py``.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections import Counter
from typing import Any

from acb_common import get_logger, get_settings
from acb_common.db import (
    TenantUnbound,
    bind_tenant,
    mark_background,
    release_background,
    release_tenant,
)

from gateway.jobs import registry, store
from gateway.jobs.store import Job, PermanentJobError

_log = get_logger("jobs.worker")

#: Seconds between two ticks.
TICK_SECONDS = 10
#: The jobs that one tick takes from one org, at most. So one busy org holds
#: the others back by this many jobs at most.
ORG_BATCH = 20
#: The bound of one handler run. It is shorter than ``store.LEASE_S``, so a
#: slow handler fails before its lease expires.
HANDLER_TIMEOUT_S = 240.0
#: The seconds that a stop waits for a tick in progress, then cancels it.
STOP_GRACE_SECONDS = 10.0

_task: asyncio.Task[None] | None = None
_stopping = asyncio.Event()


def worker_enabled() -> bool:
    """The switch, ``job_queue_enabled`` (default off). The ONE reader of the
    flag. ``/version`` calls it as ``job_queue``."""
    return bool(getattr(get_settings(), "job_queue_enabled", False))


async def _orgs() -> list[str]:
    from email_ingestion.scheduler import _list_organizations

    return [str(org) for org in await _list_organizations()]


def _ms(seconds: float) -> int:
    return round(seconds * 1000)


def _lag_ms(job: Job) -> int | None:
    """``started_at`` minus ``run_at``: how late the job started."""
    if job.started_at is None or job.run_at is None:
        return None
    return _ms((job.started_at - job.run_at).total_seconds())


async def _settle(org: str, job: Job, outcome: str, *, error: str | None,
                  failure: str | None, started: float) -> str:
    """Settle *job* and write its one log line. Answers the outcome, or
    ``lease_lost`` when the row was no longer this worker's.

    The log line names ids, numbers and a class name only: no payload value
    and no ``last_error`` text (§5).
    """
    settled = await store.settle(org, job, outcome, error)
    if not settled:
        _log.warning("jobs.lease_lost", job_id=job.id, kind=job.kind, org=org,
                     tries=job.tries)
        return "lease_lost"
    fields: dict[str, Any] = {
        "job_id": job.id, "kind": job.kind, "org": org,
        "state": store.STATE_AFTER[outcome], "tries": job.tries,
        "duration_ms": _ms(time.monotonic() - started), "lag_ms": _lag_ms(job),
    }
    if failure is not None:
        fields["error"] = failure
    if outcome == "succeeded":
        _log.info("jobs.settled", **fields)
    else:
        _log.warning("jobs.settled", **fields)
    return outcome


async def _run_one(org: str, job: Job) -> str:
    """Run one claimed job and settle it. Answers the outcome."""
    started = time.monotonic()
    if job.tries > job.max_tries:
        # The claim after a crash on the last try. The handler does not run.
        return await _settle(org, job, "dead", error="lease_expired",
                             failure="lease_expired", started=started)
    handler = registry.get(job.kind)
    if handler is None:
        return await _settle(org, job, "dead", error="unknown_kind",
                             failure="unknown_kind", started=started)
    try:
        await asyncio.wait_for(handler(job), HANDLER_TIMEOUT_S)
    except asyncio.CancelledError:
        # A stop. No settle: the job keeps its lease, and the next worker
        # claims it when the lease expires.
        raise
    except PermanentJobError as exc:
        return await _settle(org, job, "dead", error=store.describe(exc),
                             failure=type(exc).__name__, started=started)
    except Exception as exc:  # the handler failed, or ran past its timeout
        outcome = "retry" if job.tries < job.max_tries else "dead"
        return await _settle(org, job, outcome, error=store.describe(exc),
                             failure=type(exc).__name__, started=started)
    return await _settle(org, job, "succeeded", error=None, failure=None,
                         started=started)


async def run_org(org: str) -> Counter[str]:
    """Up to :data:`ORG_BATCH` due jobs of *org*, one at a time. Answers the
    count of each outcome. It stops early when the worker stops.

    With no org it raises ``TenantUnbound``. An error of a claim or a settle
    goes out, and :func:`run_tick` logs it for the org.
    """
    if not org:
        raise TenantUnbound("run_org acts for one explicit org (job_queue.md §5)")
    counts: Counter[str] = Counter()
    for _ in range(ORG_BATCH):
        if _stopping.is_set():
            break
        jobs = await store.claim(org)
        if not jobs:
            break
        for job in jobs:
            counts[await _run_one(org, job)] += 1
    return counts


async def run_tick() -> dict[str, Counter[str]]:
    """One pass over every org. Answers ``{}`` with the flag off. An org that
    fails is logged with its id and the class name only, and the next org
    runs."""
    if not worker_enabled():
        return {}
    out: dict[str, Counter[str]] = {}
    for org in await _orgs():
        if _stopping.is_set():
            break
        token = bind_tenant(org)
        try:
            counts = await run_org(org)
            if counts:
                out[org] = counts
        except Exception as exc:
            _log.warning("jobs.org_failed", org=org, error=type(exc).__name__)
        finally:
            release_tenant(token)
    return out


async def _loop() -> None:
    # Background work: its sessions take the background lane (H-390), so a
    # burst of jobs never starves member reads.
    token = mark_background()
    try:
        while not _stopping.is_set():
            try:
                await run_tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _log.warning("jobs.loop_error", error=type(exc).__name__)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(_stopping.wait(), timeout=TICK_SECONDS)
    finally:
        release_background(token)


async def start_job_worker() -> bool:
    """Start the worker when ``job_queue_enabled`` is on. Idempotent.

    With the flag off it logs ``jobs.worker_disabled``, starts no task and
    returns False.
    """
    global _task, _stopping
    if not worker_enabled():
        _log.info("jobs.worker_disabled")
        return False
    if _task is not None and not _task.done():
        return True
    # A new event for each start: an event that waited on an earlier loop
    # cannot wait on this one.
    _stopping = asyncio.Event()
    _task = asyncio.get_running_loop().create_task(_loop(), name="jobs.worker")
    _log.info("jobs.worker_started", tick_secs=TICK_SECONDS)
    return True


async def stop_job_worker() -> None:
    """Stop the worker: wait :data:`STOP_GRACE_SECONDS` for a tick in
    progress, then cancel it. A cancelled job gets no settle. Safe when the
    worker never started."""
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
