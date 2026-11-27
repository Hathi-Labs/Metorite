"""The loop of the Knowledge sync (WS-61 KC-3).

Spec: ``project-docs/specs/knowledge_curation.md`` §7.2 steps 5 and 7, §17
and §22 (KC-3), and ``project-docs/specs/knowledge_base.md`` §7.1. A copy of
the shape of ``crm_sync/scheduler.py``. It imports nothing of the CRM.

Every :data:`TICK_SECONDS` the loop looks at each organization. A source is
DUE when all of these hold:

* ``KNOWLEDGE_SYNC`` names the org, and Knowledge and its sources serve it
  (``flags.knowledge_sync_allowed``);
* the flag of the source's provider names the org too
  (``flags.knowledge_provider_allowed``), so an org off
  ``KNOWLEDGE_SOURCE_GOOGLE`` gets no Google call;
* ``kb_sources.state`` is ``ready``. A source that is ``discovering``,
  ``failed``, ``needs_reconnect`` or ``disconnected`` is skipped, so a dead
  token gets no provider call until a member connects again;
* no running row of the source has a heartbeat newer than 15 minutes;
* the newest ``changes`` or ``reconcile`` run of the source ended
  :data:`INTERVAL_SECONDS` ago or more, or never ran.

A due source runs a ``reconcile`` when its reconcile cursor is absent or older
than :data:`RECONCILE_SECONDS`, and a ``changes`` run otherwise.

**The access pass (KC-4a).** A ready source with no live lock whose change
feed is NOT due gets an ``access`` run (``access.py``) when all of these
hold:

* ``kb_sources.access_read_at`` is NULL, older than :data:`ACCESS_SECONDS`,
  or older than ``discovered_at`` (a discovery wrote a new tree);
* the newest ``access`` run ended ``ok`` (a pass that stopped at its call
  cap goes on at the next tick), or ended :data:`ACCESS_RETRY_SECONDS` ago or
  more, or never ran.

**The index pass (KC-5a).** A ready source with no live lock whose change feed
is NOT due and whose access pass is NOT due gets an ``index`` run
(``index.py``) when all of these hold:

* ``KNOWLEDGE_INDEX`` names the org, as well as ``KNOWLEDGE_SYNC`` and the
  provider flag. With the flag empty the loop counts nothing, starts no index
  run and makes no provider call;
* the pending count is above 0: the in-scope files with no document or with a
  document of another stamp, and the documents of nodes that left scope;
* the newest ``index`` run ended ``ok`` (a run that stopped at its budget goes
  on at the next tick), or ended :data:`INDEX_RETRY_SECONDS` ago or more, or
  never ran.

The change feed comes first, and an access or index run never moves its 15
minutes. The access pass comes before the index pass. Each rides
``KNOWLEDGE_SYNC`` and the provider flag, as the feed does. Each
gateway worker runs its own loop. In one worker, up to :data:`CONCURRENCY`
sources run at the same time. Across the workers each source is
single-flight by its run lock. One failure for one source or one org is
logged, and the loop goes on.

**The retention sweep.** Each tick, each org that the flag names gets
``runs.prune`` in its own tenant session: each source keeps its newest
:data:`runs.RUNS_KEPT` ended runs. A failure there is logged, and the tick
goes on.

**Dark by default.** With ``KNOWLEDGE_SYNC`` empty, :func:`start_kb_sync`
starts no task, logs once and returns False. ``main.py`` calls the start and
the stop and reads no flag.

**The one unbound read.** :func:`_organization_ids` lists the organizations
from the ``organization`` table with ONE ``get_db()``. That table is exempt
from row level security, so the read does not go blind. It is a
tenant-discovery site in ``tests/unit/test_db_engine_seam.py``. Every other
read binds one organization with ``tenant_session``, and a flag-off org opens
no session.

**The adapter.** The default comes from ``routes.knowledge.sources``:
``google_client()`` reads the one Google client, and ``make_source()`` builds
the adapter. Both are imported in the function, so this module loads with no
route. With the client unset, ``google_client()`` raises HTTP 503: the tick
logs it and skips. A test passes *make_adapter*.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
from collections.abc import Callable
from typing import Any

from acb_common import get_logger
from acb_common.db import get_db, tenant_session
from sqlalchemy import text

from gateway.kb_sources.base import KbSource
from gateway.kb_sync import access, engine, index, runs
from gateway.kb_sync.runs import DueRow, SyncRunning
from gateway.routes.knowledge import flags

_log = get_logger("kb_sync.scheduler")

#: How often the loop looks for a due source.
TICK_SECONDS = 60.0

#: The least time between two runs of one source.
INTERVAL_SECONDS = 15 * 60

#: How often a source gets the slim relist.
RECONCILE_SECONDS = 24 * 60 * 60

#: How often a source gets the access pass (KC-4a, §7.2 step 6).
ACCESS_SECONDS = 6 * 60 * 60

#: The wait before the next access run after one that failed or stopped.
ACCESS_RETRY_SECONDS = 15 * 60

#: The wait before the next index run after one that failed or stopped (KC-5a).
INDEX_RETRY_SECONDS = 15 * 60

#: Sources that run at the same time in ONE gateway worker. Each worker runs
#: its own loop, and the run lock keeps each source single-flight across them.
CONCURRENCY = 3

#: Time to wait for a run in progress when the gateway stops.
STOP_GRACE_SECONDS = 30.0

MakeAdapter = Callable[[], KbSource]

_task: asyncio.Task[None] | None = None
_stopping = asyncio.Event()
#: The test hook of the loop: the adapter factory of the running loop.
_make_adapter: MakeAdapter | None = None


async def _organization_ids() -> list[str]:
    """Every organization id, from the RLS-EXEMPT ``organization`` table."""
    db = await get_db()
    try:
        rows = (await db.execute(text("SELECT id FROM organization ORDER BY id"))).fetchall()
    finally:
        await db.close()
    return [str(r.id) for r in rows]


def access_due(row: DueRow) -> bool:
    """True when *row* is due for an access pass (the module doc). No I/O."""
    read = row.access_read_at
    stale = (read is None
             or (row.db_now - read).total_seconds() >= ACCESS_SECONDS
             or (row.discovered_at is not None and read < row.discovered_at))
    if not stale:
        return False
    if row.access_finished is None or row.access_status == runs.STATUS_OK:
        return True
    return (row.db_now - row.access_finished).total_seconds() >= ACCESS_RETRY_SECONDS


def index_due(row: DueRow) -> bool:
    """True when *row* is due for an index pass (the module doc). No I/O.

    ``index_pending`` is ``None`` for an org that ``KNOWLEDGE_INDEX`` does not
    name, so such a row is never due.
    """
    if not row.index_pending or row.index_pending < 0:
        return False
    if row.index_finished is None or row.index_status == runs.STATUS_OK:
        return True
    return (row.db_now - row.index_finished).total_seconds() >= INDEX_RETRY_SECONDS


def due_kind(row: DueRow) -> str | None:
    """The kind of run that *row* is due for, or ``None``. No I/O."""
    if row.state != engine.STATE_READY or row.running:
        return None
    if (row.last_finished is not None
            and (row.db_now - row.last_finished).total_seconds() < INTERVAL_SECONDS):
        if access_due(row):
            return runs.KIND_ACCESS
        return runs.KIND_INDEX if index_due(row) else None
    if (row.reconciled_at is None
            or (row.db_now - row.reconciled_at).total_seconds() >= RECONCILE_SECONDS):
        return runs.KIND_RECONCILE
    return runs.KIND_CHANGES


async def _with_pending(db: Any, org: str, row: DueRow) -> DueRow:
    """*row* with its pending count, when an index pass could run for it now.

    The count is a recursive read, so it runs only for a source that the
    provider flag allows, that is ready, that is not locked, and whose feed
    and access pass are not due. The test uses the due rule with a count of 1.
    """
    if not flags.knowledge_provider_allowed(org, row.provider):
        return row
    if due_kind(dataclasses.replace(row, index_pending=1)) != runs.KIND_INDEX:
        return row
    return dataclasses.replace(row, index_pending=await index.pending_count(
        db, org, row.source_id))


async def due_sources(org: str) -> list[tuple[str, str, str]]:
    """``(source_id, provider, kind)`` of each due source of *org*.

    A flag-off org opens no session. One session reads every source of the
    org, and no provider call is made. An org that ``KNOWLEDGE_INDEX`` names
    also gets the pending count of each source that could run an index pass.
    An org that it does not name gets no count and no index run.
    """
    if not flags.knowledge_sync_allowed(org):
        return []
    counting = flags.knowledge_index_allowed(org)
    async with tenant_session(org) as db:
        rows = await runs.due_rows(db, org)
        if counting:
            rows = [await _with_pending(db, org, r) for r in rows]
    return [(r.source_id, r.provider, kind) for r in rows
            if flags.knowledge_provider_allowed(org, r.provider)
            and (kind := due_kind(r)) is not None]


async def prune_runs(org: str) -> int:
    """The retention sweep of one org, in its own tenant session. A flag-off
    org opens no session. A failure is logged and never raised."""
    if not flags.knowledge_sync_allowed(org):
        return 0
    try:
        async with tenant_session(org) as db:
            return await runs.prune(db, org)
    except Exception as exc:  # the sweep never stops a tick
        _log.warning("knowledge.sync_prune_failed", organization_id=org,
                     error=type(exc).__name__)
        return 0


async def due_work() -> list[tuple[str, str, str, str]]:
    """``(org, source_id, provider, kind)`` across the box. One failing org
    is logged, and the next one is read. With no org named it opens nothing."""
    work: list[tuple[str, str, str, str]] = []
    if not flags.knowledge_sync_named():
        return work
    for org in await _organization_ids():
        await prune_runs(org)
        try:
            work.extend((org, *item) for item in await due_sources(org))
        except Exception as exc:  # one org must not hide the others
            _log.warning("knowledge.sync_due_check_failed", organization_id=org,
                         error=type(exc).__name__)
    return work


def _default_adapter() -> KbSource:
    """The Google Drive adapter on the one Google client. HTTP 503 when unset."""
    from gateway.routes.knowledge.sources import google_client, make_source

    return make_source(google_client())


async def _run_one(org: str, source_id: str, kind: str, adapter: KbSource,
                   gate: asyncio.Semaphore) -> int:
    async with gate:
        if _stopping.is_set():
            return 0
        try:
            if kind == runs.KIND_ACCESS:
                await access.run_access(org, source_id, source=adapter,
                                        should_stop=_stopping.is_set)
            elif kind == runs.KIND_INDEX:
                await index.run_index(org, source_id, source=adapter,
                                      should_stop=_stopping.is_set)
            else:
                await engine.run_source(org, source_id, kind, source=adapter,
                                        should_stop=_stopping.is_set)
        except SyncRunning:
            return 0  # a discovery or another worker holds the lock
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # one source must not stop the others
            _log.warning("knowledge.sync_tick_failed", organization_id=org,
                         source_id=source_id, error=type(exc).__name__)
            return 0
        return 1


async def run_tick(make_adapter: MakeAdapter | None = None) -> int:
    """Run the due sources, up to :data:`CONCURRENCY` at a time.

    Returns how many ran. With no due source it builds no adapter. With the
    Google client unset it logs and skips the tick.
    """
    work = await due_work()
    if not work:
        return 0
    make = make_adapter or _make_adapter or _default_adapter
    try:
        adapter = make()
    except Exception as exc:  # HTTP 503 google_not_configured, among others
        detail = getattr(exc, "detail", None)
        _log.warning("knowledge.sync_adapter_unavailable", error=type(exc).__name__,
                     detail=detail if isinstance(detail, str) else None)
        return 0
    gate = asyncio.Semaphore(CONCURRENCY)
    jobs = []
    for org, source_id, provider, kind in work:
        if provider != adapter.provider:
            _log.info("knowledge.sync_provider_skipped", organization_id=org,
                      source_id=source_id, provider=provider)
            continue
        jobs.append(_run_one(org, source_id, kind, adapter, gate))
    return sum(await asyncio.gather(*jobs))


async def _loop() -> None:
    while not _stopping.is_set():
        try:
            await run_tick()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # never let one bad tick end the loop
            _log.warning("knowledge.sync_loop_error", error=type(exc).__name__)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(_stopping.wait(), timeout=TICK_SECONDS)


async def start_kb_sync(make_adapter: MakeAdapter | None = None) -> bool:
    """Start the loop when ``KNOWLEDGE_SYNC`` names an org. Idempotent.

    With the flag empty it starts no task, logs once and returns False.
    """
    global _task, _make_adapter
    if not flags.knowledge_sync_named():
        _log.info("knowledge.sync_disabled")
        return False
    if _task is not None and not _task.done():
        return True
    _stopping.clear()
    _make_adapter = make_adapter
    _task = asyncio.get_running_loop().create_task(_loop(), name="kb-sync")
    _log.info("knowledge.sync_started", interval_secs=INTERVAL_SECONDS)
    return True


async def stop_kb_sync() -> None:
    """Stop the loop and wait for a run in progress. Safe when it never started."""
    global _task
    _stopping.set()
    task, _task = _task, None
    if task is None or task.done():
        return
    _done, pending = await asyncio.wait({task}, timeout=STOP_GRACE_SECONDS)
    for t in pending:
        _log.warning("knowledge.sync_stop_timed_out")
        t.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await t


def loop_running() -> bool:
    return _task is not None and not _task.done()


__all__ = [
    "ACCESS_RETRY_SECONDS",
    "ACCESS_SECONDS",
    "CONCURRENCY",
    "INDEX_RETRY_SECONDS",
    "INTERVAL_SECONDS",
    "RECONCILE_SECONDS",
    "STOP_GRACE_SECONDS",
    "TICK_SECONDS",
    "access_due",
    "due_kind",
    "due_sources",
    "due_work",
    "index_due",
    "loop_running",
    "prune_runs",
    "run_tick",
    "start_kb_sync",
    "stop_kb_sync",
]
