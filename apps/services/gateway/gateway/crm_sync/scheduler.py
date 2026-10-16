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

**The note labels** (WS-53 CRM-Q26, ``crm_platform.md`` §18.11.2). After a
run ends, :func:`start_labels` starts ``note_labels.label_after_run`` as a
background task: after ``engine.run_org`` in :func:`_run_one`, and after the
run of :func:`spawn`. Both return after ``runs.finish`` released the run lock,
so the step runs outside it. With the ``crm.note_labels`` mode ``off`` it
starts no task. One step runs at a time for each org (``_labeling``), so two
runs that end close together never ask twice about one note.

**The mapping pipeline** (WS-53 CRM-Q20-1, ``crm_platform.md`` §18.9.6). At
the same two points, :func:`start_mapping` starts
``map_pipeline.map_after_run`` as a background task, outside the run lock.
It starts no task while all three ``crm.map_*`` modes are ``off`` for the
org. One run of it goes at a time for each org (``_mapping``).
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from acb_common import get_logger
from acb_common.db import get_db, tenant_session
from sqlalchemy import text

from gateway.connections import store
from gateway.crm_sync import engine, map_pipeline, note_labels, profiles, runs
from gateway.crm_sync.engine import NotConnected
from gateway.crm_sync.runs import SyncRunning
from gateway.routes.crm import access as crm_access
from gateway.routes.crm import flags

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
#: The orgs whose label step runs now (CRM-Q26). The gateway is one process,
#: so an in-process set is the whole state.
_labeling: set[str] = set()
#: The orgs whose mapping pipeline runs now (CRM-Q20-1).
_mapping: set[str] = set()


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
    if not flags.crm_mirror_allowed(org):
        return False
    async with tenant_session(org) as db:
        settings = await crm_access.read_settings(db, org)
        if settings is None or getattr(settings, "mode", None) != "mirror":
            return False
        connection = await store.get(db, org, engine.PROVIDER, engine.OWNING_APP)
        if connection is None or connection.status != store.STATUS_CONNECTED:
            return False
        info = await runs.due_info(db, org)
        budget = await profiles.budget_for(db, org)
    if info.running:
        return False
    if info.last_finished is None:
        return True
    wait = engine.next_interval(info.last_credits, budget)
    return (info.db_now - info.last_finished).total_seconds() >= wait


async def due_orgs() -> list[str]:
    due: list[str] = []
    for org in await _organization_ids():
        try:
            if await is_due(org):
                due.append(org)
        except Exception as exc:  # one org must not hide the others
            _log.warning("crm.sync.due_check_failed class=%s", type(exc).__name__)
    return due


#: Organizations that run at the same time. Each one is single-flight by the run lock.
CONCURRENCY = 3


async def _run_one(org: str, gate: asyncio.Semaphore) -> int:
    async with gate:
        if _stopping.is_set():
            return 0
        try:
            await engine.run_org(org, should_stop=_stopping.is_set)
        except (SyncRunning, NotConnected):
            return 0  # a request took the lock, or the admin disconnected
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _log.warning("crm.sync.tick_failed class=%s", type(exc).__name__)
            return 0
        # `run_org` returns after `runs.finish` released the lock (CRM-Q26).
        start_labels(org)
        start_mapping(org)
        return 1


async def run_tick() -> int:
    """Run the due organizations, up to :data:`CONCURRENCY` at a time.

    One long import must not hold back the others. Returns how many ran.
    """
    gate = asyncio.Semaphore(CONCURRENCY)
    done = await asyncio.gather(*(_run_one(org, gate) for org in await due_orgs()))
    return sum(done)


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
    _done, pending = await asyncio.wait(tasks, timeout=STOP_GRACE_SECONDS)
    for task in pending:
        _log.warning("crm.mirror_sync.stop_timed_out")
        task.cancel()
    for task in pending:
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task


def loop_running() -> bool:
    return _task is not None and not _task.done()


def _track(task: asyncio.Task[Any]) -> asyncio.Task[Any]:
    _background.add(task)
    task.add_done_callback(_background.discard)
    return task


def start_labels(org: str) -> asyncio.Task[Any] | None:
    """Start the label step of ``org`` in the background (CRM-Q26). Never raises.

    It starts nothing when the loop stops, when the ``crm.note_labels`` mode
    of the org is ``off``, or when a step of the org runs already.
    """
    try:
        if _stopping.is_set() or org in _labeling or not note_labels.enabled_for(org):
            return None
        _labeling.add(org)
        task = asyncio.get_running_loop().create_task(
            note_labels.label_after_run(org, should_stop=_stopping.is_set),
            name="crm-note-labels",
        )
        # A done-callback, not a `finally` in the coroutine: a task that is
        # cancelled before its first step never runs that `finally`, and the
        # org would stay in `_labeling` for the life of the process.
        task.add_done_callback(lambda _t: _labeling.discard(org))
        return _track(task)
    except Exception as exc:  # a label step never breaks a sync run
        _labeling.discard(org)
        _log.warning("crm.note_labels.not_started class=%s", type(exc).__name__)
        return None


def start_mapping(org: str) -> asyncio.Task[Any] | None:
    """Start the mapping pipeline of ``org`` in the background (CRM-Q20-1).
    Never raises.

    It starts nothing when the loop stops, when each ``crm.map_*`` mode of the
    org is ``off``, or when a run of it goes for the org already.
    """
    try:
        if _stopping.is_set() or org in _mapping or not map_pipeline.enabled_for(org):
            return None
        _mapping.add(org)
        task = asyncio.get_running_loop().create_task(
            map_pipeline.map_after_run(org, should_stop=_stopping.is_set),
            name="crm-map-pipeline",
        )
        # A done-callback, as `start_labels` does, so a cancelled task frees the org.
        task.add_done_callback(lambda _t: _mapping.discard(org))
        return _track(task)
    except Exception as exc:  # the pipeline never breaks a sync run
        _mapping.discard(org)
        _log.warning("crm.map_pipeline.not_started class=%s", type(exc).__name__)
        return None


async def _execute_then_label(handle: engine.RunHandle) -> engine.RunResult:
    """Run the handle, then start the label step and the mapping pipeline.
    The run's result is the task's result, as before CRM-Q26."""
    result = await engine.execute(handle, should_stop=_stopping.is_set)
    start_labels(handle.org)
    start_mapping(handle.org)
    return result


def spawn(handle: engine.RunHandle) -> asyncio.Task[Any]:
    """Run a locked handle in the background. The request does not wait.

    After the run, the label step of the org starts (CRM-Q26)."""
    return _track(asyncio.get_running_loop().create_task(
        _execute_then_label(handle), name="crm-mirror-run",
    ))


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
    "start_labels",
    "start_mapping",
    "stop_crm_mirror_sync",
]
