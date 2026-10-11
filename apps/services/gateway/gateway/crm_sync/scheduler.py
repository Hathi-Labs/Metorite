"""The loop of the CRM mirror sync (WS-53 CRM-Z5, ``crm_platform.md`` §4.3).

Every 60 seconds the loop looks at each organization. An organization is DUE
when all of these hold:

* the CRM kill switch lets it in (``flags.crm_org_allowed``),
* ``crm_settings.mode`` is ``mirror``,
* it has a ``connected`` Zoho connection (``needs_reconnect`` is skipped until
  an admin connects again),
* no run holds the lock, and the last run ended more than
  :data:`engine.INTERVAL_SECONDS` ago.

The loop runs the due organizations one at a time. Each run binds its own
``tenant_session(org)`` for each step (``engine.py``).

**Dark by default.** With ``CRM_MIRROR`` off, or the CRM kill switch off,
:func:`start_crm_mirror_sync` starts no task and logs once.

**The one unbound read.** :func:`_organization_ids` lists the organizations
from the ``organization`` table. That table is exempt from row level
security, so the read does not go blind. It is a tenant-discovery site in
``tests/unit/test_db_engine_seam.py``. Every other read binds an organization.

**The first import.** A successful callback calls :func:`start_first_import`.
It starts a run in the background when this loop is running, and does nothing
when it is not. A failure to start is logged. The loop picks the org up.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from acb_common import get_logger
from acb_common.db import get_db, tenant_session
from sqlalchemy import text

from gateway.connections import store
from gateway.crm_sync import engine, runs
from gateway.crm_sync.engine import NotConnected
from gateway.crm_sync.runs import SyncRunning
from gateway.routes.crm import flags
from gateway.routes.crm.access import read_settings

_log = get_logger("crm.sync.scheduler")

#: How often the loop looks for a due organization.
TICK_SECONDS = 60.0

#: Time to wait for a run in progress when the gateway stops.
STOP_GRACE_SECONDS = 30.0

_task: asyncio.Task[None] | None = None
_stopping = asyncio.Event()
#: Runs started by a request. Kept so the task is not collected, and so a stop
#: can wait for it.
_background: set[asyncio.Task[Any]] = set()


async def _organization_ids() -> list[str]:
    """Every organization id, from the RLS-EXEMPT ``organization`` table."""
    db = await get_db()
    try:
        rows = (await db.execute(text("SELECT id FROM organization ORDER BY id"))).fetchall()
    finally:
        await db.close()
    return [str(r.id) for r in rows]


async def is_due(org: str) -> bool:
    """Is a run due for ``org``? One tenant session, no provider call."""
    if not flags.crm_org_allowed(org):
        return False
    async with tenant_session(org) as db:
        settings = await read_settings(db, org)
        if settings is None or getattr(settings, "mode", None) != "mirror":
            return False
        connection = await store.get(db, org, engine.PROVIDER, engine.OWNING_APP)
        if connection is None or connection.status != store.STATUS_CONNECTED:
            return False
        due, running = await runs.due(db, org, engine.INTERVAL_SECONDS)
    return due and not running


async def due_orgs() -> list[str]:
    due: list[str] = []
    for org in await _organization_ids():
        try:
            if await is_due(org):
                due.append(org)
        except Exception as exc:  # one org must not hide the others
            _log.warning("crm.sync.due_check_failed class=%s", type(exc).__name__)
    return due


async def run_tick() -> int:
    """Run each due organization once, one at a time. Returns how many ran."""
    ran = 0
    for org in await due_orgs():
        if _stopping.is_set():
            break
        try:
            await engine.run_org(org, should_stop=_stopping.is_set)
            ran += 1
        except (SyncRunning, NotConnected):
            continue  # a request took the lock, or the admin disconnected
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _log.warning("crm.sync.tick_failed class=%s", type(exc).__name__)
    return ran


async def _loop() -> None:
    while not _stopping.is_set():
        try:
            await run_tick()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # never let one bad tick end the loop
            _log.warning("crm.sync.loop_error class=%s", type(exc).__name__)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(_stopping.wait(), timeout=TICK_SECONDS)


async def start_crm_mirror_sync() -> bool:
    """Start the loop when the mirror and the CRM are on. Idempotent.

    With either switch off it starts no task and logs once, then returns False.
    """
    global _task
    if not flags.mirror_sync_enabled():
        _log.info("crm.mirror_sync.disabled")
        return False
    if _task is not None and not _task.done():
        return True
    _stopping.clear()
    _task = asyncio.get_running_loop().create_task(_loop(), name="crm-mirror-sync")
    _log.info("crm.mirror_sync.started interval_secs=%s", engine.INTERVAL_SECONDS)
    return True


async def stop_crm_mirror_sync() -> None:
    """Stop the loop and wait for a run in progress. Safe when it never started."""
    global _task
    _stopping.set()
    tasks = [t for t in (_task, *_background) if t is not None and not t.done()]
    _task = None
    if not tasks:
        return
    done, pending = await asyncio.wait(tasks, timeout=STOP_GRACE_SECONDS)
    for task in pending:
        _log.warning("crm.mirror_sync.stop_timed_out")
        task.cancel()
    for task in pending:
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task


def loop_running() -> bool:
    return _task is not None and not _task.done()


def spawn(handle: engine.RunHandle) -> asyncio.Task[Any]:
    """Run a locked handle in the background. The request does not wait."""
    task = asyncio.get_running_loop().create_task(
        engine.execute(handle, should_stop=_stopping.is_set), name="crm-mirror-run",
    )
    _background.add(task)
    task.add_done_callback(_background.discard)
    return task


async def start_first_import(org: str) -> bool:
    """Start the first run of a new connection in the background. Best effort.

    It does nothing unless this loop is running, so a gateway with the mirror
    off, and a test with no lifespan, start no run. A failure is logged and
    never raised: the loop picks the organization up within a minute.
    """
    if not loop_running():
        return False
    try:
        spawn(await engine.acquire(org))
    except (SyncRunning, NotConnected):
        return False
    except Exception as exc:
        _log.warning("crm.sync.first_import_not_started class=%s", type(exc).__name__)
        return False
    return True


__all__ = [
    "STOP_GRACE_SECONDS",
    "TICK_SECONDS",
    "due_orgs",
    "is_due",
    "loop_running",
    "run_tick",
    "spawn",
    "start_crm_mirror_sync",
    "start_first_import",
    "stop_crm_mirror_sync",
]
