"""The SQL of the Knowledge sync: runs and cursors (WS-61 KC-3, migration 308).

Spec: ``project-docs/specs/knowledge_curation.md`` §7.2 step 5 and §16, and
``project-docs/specs/knowledge_base.md`` §7.1.

Every function takes the ``db`` that ``tenant_session(org)`` gave the caller.
Row level security binds the tenant. Each query also names the organization,
so a session with row level security off still reads one organization. The
organization comes from the server, never from request input (R5).

This module copies the shape of ``crm_sync/runs.py`` and imports nothing from
the CRM engine. The lock is per SOURCE, not per organization.

**The run lock.** A partial unique index allows ONE ``running`` row for each
source. :func:`start` inserts that row in a SAVEPOINT. A second insert fails
with a unique violation, and :func:`start` raises :class:`SyncRunning`. A row
whose heartbeat is older than :data:`STALE_AFTER_SECONDS` is taken over
first: it becomes ``failed`` with the code ``stale``.

**A discovery comes first.** A member's consent starts a discovery, and the
loop must never leave the source in ``discovering``. So ``start(...,
preempt=True)`` also ends a fresh ``changes`` or ``reconcile`` run of the
source, with the code ``superseded``. That run then gets :class:`RunLost` at
its next write, and its page rolls back. Two discoveries never preempt each
other.

**The cursor moves with its page.** :func:`write_cursor` runs in the
transaction of the page it covers. Its guard takes ``FOR KEY SHARE`` on the
source row, as the node upsert of ``discovery.py`` does. A disconnect that
holds the row ``FOR UPDATE`` wins: the write waits, reads ``disconnected``
and writes nothing.

**No free text, and no token in a log.** The ``error`` column holds a short
code from this package. The page token sits in ``cursor_state`` only, and no
log line carries it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

#: A running row with an older heartbeat is dead. A new run may take it over.
STALE_AFTER_SECONDS = 15 * 60

KIND_DISCOVER = "discover"
KIND_CHANGES = "changes"
KIND_RECONCILE = "reconcile"
#: The access pass (KC-4a, migration 309): the readers of each node.
KIND_ACCESS = "access"

STATUS_RUNNING = "running"
STATUS_OK = "ok"
STATUS_FAILED = "failed"
STATUS_STOPPED = "stopped"

#: The two cursor rows of a source. ``changes`` holds the page token,
#: ``reconcile`` the time of the last relist.
CURSOR_CHANGES = "changes"
CURSOR_RECONCILE = "reconcile"
#: Where an access pass stands (KC-4a): the listing page, the last node it
#: finished, and the start of the pass. NULL state: no pass is open.
CURSOR_ACCESS = "access"

#: The ``last_status`` of a cursor row: at a page boundary, or caught up.
CURSOR_PARTIAL = "partial"
CURSOR_OK = "ok"


class SyncRunning(Exception):
    """A run is already running for this source."""


class RunLost(Exception):
    """This run is no longer the running row. Another run took it over."""


_TAKEOVER_SQL = (
    "UPDATE kb_sync_runs SET status = 'failed', error = 'stale', finished_at = now() "
    "WHERE organization_id = CAST(:org AS uuid) AND source_id = CAST(:source AS uuid) "
    "AND status = 'running' AND heartbeat_at < now() - make_interval(secs => :secs)"
)

#: A discovery ends a live loop run of its source (the module doc).
_PREEMPT_SQL = (
    "UPDATE kb_sync_runs SET status = 'failed', error = 'superseded', finished_at = now() "
    "WHERE organization_id = CAST(:org AS uuid) AND source_id = CAST(:source AS uuid) "
    "AND status = 'running' AND kind <> 'discover'"
)

_INSERT_SQL = (
    "INSERT INTO kb_sync_runs (organization_id, source_id, kind, status, counts) "
    "VALUES (CAST(:org AS uuid), CAST(:source AS uuid), :kind, 'running', "
    "CAST(:counts AS jsonb)) RETURNING id, started_at"
)

_PROGRESS_SQL = (
    "UPDATE kb_sync_runs SET heartbeat_at = now(), counts = CAST(:counts AS jsonb) "
    "WHERE id = CAST(:id AS uuid) AND organization_id = CAST(:org AS uuid) "
    "AND status = 'running'"
)

_FINISH_SQL = (
    "UPDATE kb_sync_runs SET status = :status, error = :error, finished_at = now(), "
    "heartbeat_at = now(), counts = CAST(:counts AS jsonb) "
    "WHERE id = CAST(:id AS uuid) AND organization_id = CAST(:org AS uuid) "
    "AND status = 'running'"
)

_CURSOR_SQL = (
    "SELECT kind, cursor_state, last_run_at, last_status FROM kb_sync_cursors "
    "WHERE organization_id = CAST(:org AS uuid) AND source_id = CAST(:source AS uuid) "
    "AND kind = :kind"
)

#: ⚠️ ``FOR KEY SHARE`` in the guard, as ``discovery.UPSERT_NODES_SQL`` has it.
#: A disconnect holds the source row ``FOR UPDATE`` while it deletes the
#: cursors. The guard waits for that lock, reads the row again, sees
#: ``disconnected``, and the upsert writes nothing. Without the lock it read
#: the old state and wrote a cursor for a disconnected source. Fence, R8:
#: ``test_kc_sync_r8.py::test_a_cursor_write_that_waits_on_a_disconnect_writes_nothing``.
UPSERT_CURSOR_SQL = (
    "INSERT INTO kb_sync_cursors (organization_id, source_id, kind, cursor_state, "
    "last_run_at, last_status, created_at, updated_at) "
    "SELECT CAST(:org AS uuid), CAST(:source AS uuid), :kind, CAST(:state AS jsonb), "
    "now(), :status, now(), now() "
    "WHERE EXISTS (SELECT 1 FROM kb_sources s WHERE s.id = CAST(:source AS uuid) "
    "AND s.organization_id = CAST(:org AS uuid) AND s.state <> 'disconnected' "
    "FOR KEY SHARE OF s) "
    "ON CONFLICT (organization_id, source_id, kind) DO UPDATE SET "
    "cursor_state = EXCLUDED.cursor_state, last_run_at = now(), "
    "last_status = EXCLUDED.last_status, updated_at = now() "
    "RETURNING kind"
)

_DELETE_CURSORS_SQL = (
    "DELETE FROM kb_sync_cursors "
    "WHERE organization_id = CAST(:org AS uuid) AND source_id = CAST(:source AS uuid)"
)

#: The loop's view of every source of one org (``scheduler.due_kind``).
#: ``last_finished`` is the end of the NEWEST run of the loop (``changes`` or
#: ``reconcile``), read by ``started_at DESC LIMIT 1`` on the index
#: ``kb_sync_runs_source_started_idx``, so the read does not grow with the
#: history (review P2). A newest run that has not ended answers NULL, and the
#: lock decides. ``running`` is a live lock: a heartbeat newer than
#: :data:`STALE_AFTER_SECONDS`.
#:
#: KC-4a: ``access_finished`` and ``access_status`` are the end and the
#: status of the NEWEST ``access`` run, read the same way. An access run
#: never moves ``last_finished``, so the change feed keeps its 15 minutes.
_DUE_SQL = (
    "SELECT s.id, s.provider, s.state, now() AS db_now, "
    "s.discovered_at, s.access_read_at, "
    "(SELECT r.finished_at FROM kb_sync_runs r "
    " WHERE r.organization_id = s.organization_id AND r.source_id = s.id "
    " AND r.kind IN ('changes', 'reconcile') "
    " ORDER BY r.started_at DESC LIMIT 1) "
    "AS last_finished, "
    "(SELECT r.finished_at FROM kb_sync_runs r "
    " WHERE r.organization_id = s.organization_id AND r.source_id = s.id "
    " AND r.kind = 'access' ORDER BY r.started_at DESC LIMIT 1) "
    "AS access_finished, "
    "(SELECT r.status FROM kb_sync_runs r "
    " WHERE r.organization_id = s.organization_id AND r.source_id = s.id "
    " AND r.kind = 'access' ORDER BY r.started_at DESC LIMIT 1) "
    "AS access_status, "
    "EXISTS (SELECT 1 FROM kb_sync_runs r "
    " WHERE r.organization_id = s.organization_id AND r.source_id = s.id "
    " AND r.status = 'running' "
    " AND r.heartbeat_at >= now() - make_interval(secs => :stale)) AS running, "
    "(SELECT c.last_run_at FROM kb_sync_cursors c "
    " WHERE c.organization_id = s.organization_id AND c.source_id = s.id "
    " AND c.kind = 'reconcile') AS reconciled_at "
    "FROM kb_sources s WHERE s.organization_id = CAST(:org AS uuid) "
    "ORDER BY s.created_at, s.id"
)


#: How many ended runs the retention sweep keeps for each source.
RUNS_KEPT = 50

#: The retention sweep of one org (review P2): each run past the newest
#: :data:`RUNS_KEPT` of its source goes. A running row never goes.
_PRUNE_SQL = (
    "DELETE FROM kb_sync_runs d "
    "WHERE d.organization_id = CAST(:org AS uuid) AND d.status <> 'running' "
    "AND d.id IN (SELECT ranked.id FROM (SELECT r.id, row_number() OVER ("
    "PARTITION BY r.source_id ORDER BY r.started_at DESC, r.id DESC) AS n "
    "FROM kb_sync_runs r WHERE r.organization_id = CAST(:org AS uuid)) ranked "
    "WHERE ranked.n > :keep)"
)


@dataclass(frozen=True)
class Started:
    """The run lock, held: the run id and the database time it started."""

    id: str
    started_at: datetime


@dataclass(frozen=True)
class Cursor:
    """The stored state of one cursor row. The page token stays in here."""

    kind: str
    state: dict[str, Any] | None
    last_run_at: datetime | None
    last_status: str | None

    @property
    def page_token(self) -> str | None:
        token = (self.state or {}).get("page_token")
        return str(token) if token else None


@dataclass(frozen=True)
class DueRow:
    """What the loop needs to know of one source, with no provider call."""

    source_id: str
    provider: str
    state: str
    db_now: datetime
    last_finished: datetime | None
    running: bool
    reconciled_at: datetime | None
    #: KC-4a: the last discovery, the end of the last FULL access pass, and
    #: the end and the status of the newest access run.
    discovered_at: datetime | None
    access_read_at: datetime | None
    access_finished: datetime | None
    access_status: str | None


def _json(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = json.loads(value)
    return dict(value)


async def start(
    db: Any, org: str, source_id: str, kind: str, *,
    counts: dict[str, Any] | None = None, preempt: bool = False,
) -> Started:
    """Take the run lock: insert the ``running`` row, and return it.

    Raises :class:`SyncRunning` when another run holds the lock. The insert
    runs in a SAVEPOINT, so the refusal leaves the caller's transaction usable.
    *preempt* (a discovery) first ends a live ``changes`` or ``reconcile`` run.
    """
    params = {"org": org, "source": source_id}
    await db.execute(text(_TAKEOVER_SQL), {**params, "secs": float(STALE_AFTER_SECONDS)})
    if preempt:
        await db.execute(text(_PREEMPT_SQL), params)
    try:
        async with db.begin_nested():
            row = (await db.execute(text(_INSERT_SQL), {
                **params, "kind": kind, "counts": json.dumps(counts or {}),
            })).first()
    except IntegrityError as exc:
        raise SyncRunning("a sync run is already running for this source") from exc
    return Started(id=str(row.id), started_at=row.started_at)


async def progress(db: Any, org: str, run_id: str, counts: dict[str, Any]) -> None:
    """Write the counts and the heartbeat. Raises :class:`RunLost` if the lock moved."""
    result = await db.execute(text(_PROGRESS_SQL), {
        "id": run_id, "org": org, "counts": json.dumps(counts),
    })
    if (result.rowcount or 0) == 0:
        raise RunLost("the run lock was taken over")


async def finish(
    db: Any, org: str, run_id: str, status: str, error: str | None,
    counts: dict[str, Any],
) -> bool:
    """Close the run. False when another run took it over first: that run's
    row stays as the takeover wrote it."""
    result = await db.execute(text(_FINISH_SQL), {
        "id": run_id, "org": org, "status": status,
        "error": (error or None) and str(error)[:64],
        "counts": json.dumps(counts),
    })
    return (result.rowcount or 0) > 0


async def read_cursor(db: Any, org: str, source_id: str, kind: str) -> Cursor | None:
    row = (await db.execute(text(_CURSOR_SQL), {
        "org": org, "source": source_id, "kind": kind,
    })).first()
    if row is None:
        return None
    return Cursor(kind=str(row.kind), state=_json(row.cursor_state),
                  last_run_at=row.last_run_at, last_status=row.last_status)


async def write_cursor(
    db: Any, org: str, source_id: str, kind: str, state: dict[str, Any] | None,
    status: str,
) -> bool:
    """Upsert one cursor row under the source guard. False when the guard
    wrote nothing: the source is disconnected."""
    row = (await db.execute(text(UPSERT_CURSOR_SQL), {
        "org": org, "source": source_id, "kind": kind,
        "state": json.dumps(state) if state is not None else None,
        "status": status,
    })).first()
    return row is not None


async def delete_cursors(db: Any, org: str, source_id: str) -> None:
    """Delete both cursor rows of one source. The disconnect calls it in its
    own transaction. The run rows stay as the record."""
    await db.execute(text(_DELETE_CURSORS_SQL), {"org": org, "source": source_id})


async def prune(db: Any, org: str, keep: int = RUNS_KEPT) -> int:
    """Delete each ended run of *org* past the newest *keep* of its source.
    Returns how many went. A running row stays."""
    result = await db.execute(text(_PRUNE_SQL), {"org": org, "keep": int(keep)})
    return int(result.rowcount or 0)


async def due_rows(db: Any, org: str) -> list[DueRow]:
    """Each source of *org*, with its lock and its last runs. No provider call."""
    rows = (await db.execute(text(_DUE_SQL), {
        "org": org, "stale": float(STALE_AFTER_SECONDS),
    })).fetchall()
    return [
        DueRow(
            source_id=str(r.id), provider=str(r.provider), state=str(r.state),
            db_now=r.db_now, last_finished=r.last_finished, running=bool(r.running),
            reconciled_at=r.reconciled_at, discovered_at=r.discovered_at,
            access_read_at=r.access_read_at, access_finished=r.access_finished,
            access_status=r.access_status,
        )
        for r in rows
    ]


__all__ = [
    "CURSOR_ACCESS",
    "CURSOR_CHANGES",
    "CURSOR_OK",
    "CURSOR_PARTIAL",
    "CURSOR_RECONCILE",
    "KIND_ACCESS",
    "KIND_CHANGES",
    "KIND_DISCOVER",
    "KIND_RECONCILE",
    "RUNS_KEPT",
    "STALE_AFTER_SECONDS",
    "STATUS_FAILED",
    "STATUS_OK",
    "STATUS_RUNNING",
    "STATUS_STOPPED",
    "UPSERT_CURSOR_SQL",
    "Cursor",
    "DueRow",
    "RunLost",
    "Started",
    "SyncRunning",
    "delete_cursors",
    "due_rows",
    "finish",
    "progress",
    "prune",
    "read_cursor",
    "start",
    "write_cursor",
]
