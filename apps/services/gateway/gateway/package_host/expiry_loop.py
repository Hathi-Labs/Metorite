"""The daily expiry loop of the package host (WS-PKG PKG-10b).

Spec: ``project-docs/specs/package_host.md`` §6.2 and the PKG-10b row of
§10. An app made in chat lives 30 days unless the member keeps it. Then it
stays 30 days in the archive, and then it goes. ``store.expire_due`` does
both steps for one org. This loop calls it one time for each org, each day.

**One tick.** It lists the orgs from the RLS-EXEMPT ``organization`` table
and keeps each org where ``flags.package_host_allowed`` is true. Each org
runs in its own ``tenant_session(org)``, and one session never binds a
second org. The tick calls ``store.expire_due`` ONE time in that session,
with a time from :func:`utc_now`. The session commits at the end of the
block. An error in one org is logged with the org id and the error type,
and the tick goes on to the next org.

**The clock guard.** Before ``expire_due``, the tick reads ``now()`` from the
database in the same session. When the host clock and the database clock
differ by more than :data:`MAX_CLOCK_SKEW`, the tick skips that org. It does
not call ``expire_due``, and the session rolls back. A host clock that jumps
forward would otherwise delete installs early, and the delete is one-way.
The log line ``package_host.expiry.clock_skew`` gives the org id and the
skew in seconds only.

**The model.** ``routes/knowledge/proposal_expiry.py`` and
``agent_run_sweep.py``. The start, the stop and the error rules are theirs.
One change: the first tick waits :data:`FIRST_DELAY_SECONDS` after the
start, so a restart does not delete data in the first second.

**The log.** One line for each org with counts only (archived and
deleted), and one line for each tick. Never a name, a title or an address.

**Dark.** ``PACKAGE_EXPIRY_ENABLED`` (default OFF) gates the START inside
:func:`start_package_expiry`. A SIGHUP reload does not change it, because
``flag_reload`` applies only the ``*_ORGS`` and ``*_MODES`` fields. The
gateway reads it from the settings at the start, so only a restart changes
it. Each tick checks it too, and with the flag off a tick reads nothing, not
even the org list. ``PACKAGE_HOST_ORGS`` DOES reload on SIGHUP, and it
selects the orgs where a tick may delete. 🔴 A flip on production is
the owner's act only, because the tick deletes member data and the delete is
one-way. ``_organization_ids`` is the one unbound read, a tenant-discovery
site registered in ``tests/unit/test_db_engine_seam.py``. Fence, R8:
``tests/unit/test_package_expiry_loop_r8.py``.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from acb_common import get_logger
from sqlalchemy import text

from gateway.db import get_db, tenant_session
from gateway.package_host import flags, store

_log = get_logger("gateway.package_host.expiry_loop")

#: Seconds between two ticks: one day.
TICK_SECONDS = 86400.0

#: Seconds from the start to the first tick. A restart does not run a tick
#: at once.
FIRST_DELAY_SECONDS = 600.0

#: Seconds to wait for a tick in progress when the gateway stops.
STOP_GRACE_SECONDS = 30.0

#: The largest difference between the host clock and the database clock that
#: a tick accepts. A larger difference skips the org, and deletes nothing.
MAX_CLOCK_SKEW = timedelta(minutes=5)

_task: asyncio.Task[None] | None = None
_stopping = asyncio.Event()


def utc_now() -> datetime:
    """The time of an expiry, with a time zone. The one clock of the loop."""
    return datetime.now(UTC)


@dataclass
class TickResult:
    """What one tick did. A test reads it, and the log line prints it."""

    orgs: int = 0
    archived: int = 0
    deleted: int = 0
    failed: int = 0
    skipped_skew: int = 0


class _ClockSkew(Exception):
    """Raised inside the tenant session so that the session rolls back."""

    def __init__(self, seconds: float) -> None:
        super().__init__(seconds)
        self.seconds = seconds


def expiry_enabled() -> bool:
    """True when ``PACKAGE_EXPIRY_ENABLED`` is on. The one reader is ``flags``."""
    return flags.package_expiry_enabled()


async def _organization_ids() -> list[str]:
    """Every organization id, from the RLS-EXEMPT ``organization`` table.

    The one unbound read of this loop (a tenant-discovery site).
    """
    db = await get_db()
    try:
        rows = (await db.execute(text("SELECT id FROM organization ORDER BY id"))).fetchall()
    finally:
        await db.close()
    return [str(r.id) for r in rows]


async def db_now(db: Any) -> datetime:
    """The clock of the database: ``now()``, the start of this transaction."""
    return (await db.execute(text("SELECT now()"))).scalar_one()


async def run_org(org: str, result: TickResult, *,
                  clock: Callable[[], datetime] = utc_now,
                  db_clock: Callable[[Any], Awaitable[datetime]] = db_now) -> None:
    """One org's share of a tick, in ONE tenant session and one transaction.

    When the host clock and the database clock differ by more than
    :data:`MAX_CLOCK_SKEW`, the org is skipped and its session rolls back.
    """
    try:
        async with tenant_session(org) as db:
            host_now = clock()
            skew = abs(await db_clock(db) - host_now)
            if skew > MAX_CLOCK_SKEW:
                raise _ClockSkew(skew.total_seconds())
            got = await store.expire_due(db, now=host_now)
    except _ClockSkew as exc:
        result.skipped_skew += 1
        _log.warning("package_host.expiry.clock_skew", org=org,
                     skew_secs=round(exc.seconds))
        return
    result.archived += got["archived"]
    result.deleted += got["deleted"]
    _log.info("package_host.expiry.org", org=org, archived=got["archived"],
              deleted=got["deleted"])


async def run_tick(*, clock: Callable[[], datetime] = utc_now) -> TickResult:
    """One pass over the orgs of the package host. With the flag off it
    reads nothing."""
    result = TickResult()
    if not expiry_enabled():
        return result
    orgs = [o for o in await _organization_ids() if flags.package_host_allowed(o)]
    result.orgs = len(orgs)
    for org in orgs:
        if _stopping.is_set():
            break
        try:
            await run_org(org, result, clock=clock)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # one org must not hide the others
            result.failed += 1
            _log.warning("package_host.expiry.org_failed", org=org,
                         error=type(exc).__name__)
    _log.info("package_host.expiry.tick", orgs=result.orgs, archived=result.archived,
              deleted=result.deleted, failed=result.failed,
              skipped_skew=result.skipped_skew)
    return result


async def _wait(seconds: float) -> None:
    """Wait *seconds*, or less when the gateway stops."""
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(_stopping.wait(), timeout=seconds)


async def _loop() -> None:
    await _wait(FIRST_DELAY_SECONDS)
    while not _stopping.is_set():
        try:
            await run_tick()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # never let one bad tick end the loop
            _log.warning("package_host.expiry.loop_error", error=type(exc).__name__)
        await _wait(TICK_SECONDS)


async def start_package_expiry() -> bool:
    """Start the loop when ``PACKAGE_EXPIRY_ENABLED`` is on. Idempotent.

    With the flag off it starts no task, logs once, and returns False.
    """
    global _task
    if not expiry_enabled():
        _log.info("package_host.expiry.disabled")
        return False
    if _task is not None and not _task.done():
        return True
    _stopping.clear()
    _task = asyncio.get_running_loop().create_task(_loop(), name="package-expiry")
    _log.info("package_host.expiry.started", tick_secs=TICK_SECONDS,
              first_delay_secs=FIRST_DELAY_SECONDS)
    return True


async def stop_package_expiry() -> None:
    """Stop the loop and wait for a tick in progress. Safe when it never
    started."""
    global _task
    _stopping.set()
    task, _task = _task, None
    if task is None or task.done():
        return
    _done, pending = await asyncio.wait({task}, timeout=STOP_GRACE_SECONDS)
    for still in pending:
        _log.warning("package_host.expiry.stop_timed_out")
        still.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await still


def loop_running() -> bool:
    return _task is not None and not _task.done()


__all__ = [
    "FIRST_DELAY_SECONDS",
    "MAX_CLOCK_SKEW",
    "STOP_GRACE_SECONDS",
    "TICK_SECONDS",
    "TickResult",
    "db_now",
    "expiry_enabled",
    "loop_running",
    "run_org",
    "run_tick",
    "start_package_expiry",
    "stop_package_expiry",
    "utc_now",
]
