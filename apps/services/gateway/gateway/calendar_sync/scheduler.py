"""The loop of the calendar sync (WS-CAL CE-10).

Spec: ``project-docs/specs/calendar_events_reminders.md`` §8.1 item 4. A copy
of the shape of ``kb_sync/scheduler.py``.

Every :data:`TICK_SECONDS` the loop looks at each organization that
``CALENDAR_SYNC_READ`` names. A connection is DUE when it is ``connected``,
its member passes ``CALENDAR_SYNC_MEMBERS``, and its ``last_sync_at`` is
:data:`INTERVAL_SECONDS` old or more (or never set). A ``needs_reconnect`` row
gets no call until the member connects again. Up to :data:`CONCURRENCY`
connections run at once in a worker, and the cursor check of the engine keeps
two workers from writing the same read twice.

**Dark by default.** With ``CALENDAR_SYNC_READ`` empty, :func:`start_calendar_sync`
starts no task, logs once and returns False. ``main.py`` reads no flag.

**The one unbound read.** :func:`_organization_ids` lists the ``organization``
table, which is exempt from row level security, so the read does not go
blind. It is a tenant-discovery site in ``tests/unit/test_db_engine_seam.py``.
Every other read binds one org with ``tenant_session``.

**Providers.** :data:`PROVIDERS` maps a ``provider`` name of the connection
row to a factory. A provider whose client is not set up returns ``None`` and
is skipped.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from datetime import datetime

from acb_common import get_logger
from acb_common.db import get_db, tenant_session
from sqlalchemy import text

from gateway.calendar_sync import connections, engine, flags
from gateway.calendar_sync.provider import CalendarProvider
from gateway.connections import store

_log = get_logger("calendar_sync.scheduler")

TICK_SECONDS = 60.0
INTERVAL_SECONDS = 60.0
CONCURRENCY = 3
STOP_GRACE_SECONDS = 30.0

#: provider name of the row -> factory. Outlook adds its line here.
def _google() -> CalendarProvider | None:
    from gateway.calendar_sync.google import factory

    return factory()


PROVIDERS: dict[str, Callable[[], CalendarProvider | None]] = {
    "google_calendar": _google,
}

_task: asyncio.Task[None] | None = None
_stopping = asyncio.Event()


async def _organization_ids() -> list[str]:
    """Every organization id, from the RLS-EXEMPT ``organization`` table."""
    db = await get_db()
    try:
        rows = (await db.execute(text("SELECT id FROM organization ORDER BY id"))).fetchall()
    finally:
        await db.close()
    return [str(r.id) for r in rows]


def is_due(conn: store.Connection, db_now: datetime) -> bool:
    """True when *conn* is due. No I/O."""
    if conn.status != store.STATUS_CONNECTED:
        return False
    if not flags.read_allowed(conn.organization_id, conn.member_email):
        return False
    retry = connections.retry_at(conn)
    if retry is not None and db_now < retry:
        return False  # backing off after an error
    last = conn.last_sync_at
    return last is None or (db_now - last).total_seconds() >= INTERVAL_SECONDS


async def due_work() -> list[tuple[str, str, str]]:
    """``(org, connection_id, provider)`` of each due connection."""
    work: list[tuple[str, str, str]] = []
    if not flags.read_named():
        return work
    for org in await _organization_ids():
        if not flags.read_allowed(org):
            continue  # a flag-off org opens no session
        try:
            async with tenant_session(org) as db:
                now = (await db.execute(text("SELECT now() AS n"))).first().n
                rows = await connections.all_connections(db, org)
            work.extend((org, c.id, c.provider) for c in rows if is_due(c, now))
        except Exception as exc:  # one org must not hide the others
            _log.warning("calendar_sync.due_check_failed", organization_id=org,
                         error=type(exc).__name__)
    return work


async def _task_pass(org: str, connection_id: str, provider: CalendarProvider) -> None:
    """CE-13: Metorite task blocks out and back. Dark behind CALENDAR_SYNC_WRITE.
    A failure here never fails the read."""
    try:
        from gateway.calendar_sync import task_out

        await task_out.run_pass(org, connection_id, provider=provider)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        _log.warning("calendar_sync.task_pass_failed", organization_id=org,
                     error=type(exc).__name__)


async def _run_one(org: str, connection_id: str, provider: CalendarProvider,
                   gate: asyncio.Semaphore) -> int:
    async with gate:
        if _stopping.is_set():
            return 0
        try:
            outcome = await engine.sync_connection(org, connection_id, provider)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # one connection must not stop the others
            _log.warning("calendar_sync.tick_failed", organization_id=org,
                         error=type(exc).__name__)
            return 0
        if outcome.status == "ok":
            await _task_pass(org, connection_id, provider)
        return 1 if outcome.status == "ok" else 0


async def run_tick(providers: dict[str, Callable[[], CalendarProvider | None]] | None = None) -> int:
    """Run the due connections. Returns how many ended ``ok``."""
    work = await due_work()
    if not work:
        return 0
    factories = providers if providers is not None else PROVIDERS
    built: dict[str, CalendarProvider | None] = {}
    gate = asyncio.Semaphore(CONCURRENCY)
    jobs = []
    for org, cid, name in work:
        if name not in built:
            factory = factories.get(name)
            try:
                built[name] = factory() if factory else None
            except Exception as exc:  # a client that is not set up
                _log.info("calendar_sync.provider_unavailable", provider=name,
                          error=type(exc).__name__)
                built[name] = None
        if built[name] is not None:
            jobs.append(_run_one(org, cid, built[name], gate))  # type: ignore[arg-type]
    return sum(await asyncio.gather(*jobs))


async def _loop() -> None:
    while not _stopping.is_set():
        try:
            await run_tick()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # never let one bad tick end the loop
            _log.warning("calendar_sync.loop_error", error=type(exc).__name__)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(_stopping.wait(), timeout=TICK_SECONDS)


async def start_calendar_sync() -> bool:
    """Start the loop when ``CALENDAR_SYNC_READ`` names an org. Idempotent."""
    global _task
    if not flags.read_named():
        _log.info("calendar_sync.disabled")
        return False
    if _task is not None and not _task.done():
        return True
    _stopping.clear()
    _task = asyncio.get_running_loop().create_task(_loop(), name="calendar-sync")
    _log.info("calendar_sync.started", interval_secs=INTERVAL_SECONDS)
    return True


async def stop_calendar_sync() -> None:
    """Stop the loop and wait for a run in progress. Safe when it never started."""
    global _task
    _stopping.set()
    task, _task = _task, None
    if task is None or task.done():
        return
    _done, pending = await asyncio.wait({task}, timeout=STOP_GRACE_SECONDS)
    for t in pending:
        t.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await t


def loop_running() -> bool:
    return _task is not None and not _task.done()
