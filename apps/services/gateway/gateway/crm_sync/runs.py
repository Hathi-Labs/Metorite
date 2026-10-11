"""The SQL of the sync: runs and cursors (WS-53 CRM-Z4 and CRM-Z5, migration 246).

Every function takes the ``db`` that ``tenant_session(org)`` gave the caller.
Row level security binds the tenant. Each query also names the organization, so
a session with row level security off still reads one organization. The
organization comes from the server, never from request input (R5).

**The run lock.** A partial unique index allows ONE ``running`` row for each
organization. :func:`start` inserts that row in a SAVEPOINT. A second insert
fails with a unique violation, and :func:`start` raises :class:`SyncRunning`.
A row whose heartbeat is older than :data:`STALE_AFTER_SECONDS` is taken over
first: it becomes ``failed`` with the code ``stale``.

**No free text.** The ``error`` column holds a short code from this package.
It never holds a message from Zoho.
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

KIND_IMPORT = "import"
KIND_INCREMENTAL = "incremental"
KIND_RECONCILE = "reconcile"

STATUS_RUNNING = "running"
STATUS_OK = "ok"
STATUS_FAILED = "failed"
STATUS_STOPPED = "stopped"


class SyncRunning(Exception):
    """A run is already running for this organization."""


class RunLost(Exception):
    """This run is no longer the running row. Another run took it over."""


@dataclass(frozen=True)
class RunRow:
    id: str
    kind: str
    status: str
    started_at: datetime | None
    finished_at: datetime | None
    heartbeat_at: datetime | None
    counts: dict[str, Any]
    estimate: dict[str, Any] | None
    credits_used: int | None
    error: str | None


_RUN_COLUMNS = (
    "id, kind, status, started_at, finished_at, heartbeat_at, counts, "
    "estimate, credits_used, error"
)

_TAKEOVER_SQL = (
    "UPDATE crm_sync_runs SET status = 'failed', error = 'stale', "
    "finished_at = now() WHERE organization_id = CAST(:org AS uuid) "
    "AND status = 'running' "
    "AND heartbeat_at < now() - make_interval(secs => :secs)"
)

_INSERT_SQL = (
    "INSERT INTO crm_sync_runs (organization_id, connection_id, kind, status, "
    "counts, estimate) VALUES (CAST(:org AS uuid), CAST(:connection AS uuid), "
    ":kind, 'running', CAST(:counts AS jsonb), CAST(:estimate AS jsonb)) "
    "RETURNING id"
)

_PROGRESS_SQL = (
    "UPDATE crm_sync_runs SET heartbeat_at = now(), "
    "counts = CAST(:counts AS jsonb), estimate = CAST(:estimate AS jsonb), "
    "credits_used = :credits WHERE id = CAST(:id AS uuid) "
    "AND organization_id = CAST(:org AS uuid) AND status = 'running'"
)

_FINISH_SQL = (
    "UPDATE crm_sync_runs SET status = :status, error = :error, "
    "finished_at = now(), heartbeat_at = now(), "
    "counts = CAST(:counts AS jsonb), estimate = CAST(:estimate AS jsonb), "
    "credits_used = :credits WHERE id = CAST(:id AS uuid) "
    "AND organization_id = CAST(:org AS uuid) AND status = 'running'"
)

_LATEST_SQL = (
    f"SELECT {_RUN_COLUMNS} FROM crm_sync_runs "
    "WHERE organization_id = CAST(:org AS uuid) "
    "ORDER BY started_at DESC LIMIT 1"
)

_LAST_OK_SQL = (
    "SELECT max(finished_at) AS at FROM crm_sync_runs "
    "WHERE organization_id = CAST(:org AS uuid) AND status = 'ok'"
)

_CREDITS_24H_SQL = (
    "SELECT COALESCE(sum(credits_used), 0) AS used FROM crm_sync_runs "
    "WHERE organization_id = CAST(:org AS uuid) "
    "AND started_at > now() - interval '24 hours'"
)

_RESUMABLE_IMPORT_SQL = (
    "SELECT counts, estimate FROM crm_sync_runs "
    "WHERE organization_id = CAST(:org AS uuid) AND kind = 'import' "
    "AND status IN ('failed', 'stopped') ORDER BY started_at DESC LIMIT 1"
)

_DUE_SQL = (
    "SELECT now() AS db_now, "
    "       (SELECT finished_at FROM crm_sync_runs "
    "         WHERE organization_id = CAST(:org AS uuid) AND finished_at IS NOT NULL "
    "         ORDER BY finished_at DESC LIMIT 1) AS last_finished, "
    "       (SELECT credits_used FROM crm_sync_runs "
    "         WHERE organization_id = CAST(:org AS uuid) AND finished_at IS NOT NULL "
    "         ORDER BY finished_at DESC LIMIT 1) AS last_credits, "
    "       EXISTS (SELECT 1 FROM crm_sync_runs "
    "                WHERE organization_id = CAST(:org AS uuid) "
    "                  AND status = 'running' "
    "                  AND heartbeat_at >= now() - make_interval(secs => :stale)) "
    "       AS running"
)

_MEMBERS_SQL = (
    "SELECT lower(email) AS email FROM app_user "
    "WHERE organization_id = CAST(:org AS uuid) AND status = 'active'"
)


def _json(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = json.loads(value)
    return dict(value)


def _row(row: Any) -> RunRow:
    return RunRow(
        id=str(row.id),
        kind=row.kind,
        status=row.status,
        started_at=row.started_at,
        finished_at=row.finished_at,
        heartbeat_at=row.heartbeat_at,
        counts=_json(row.counts) or {},
        estimate=_json(row.estimate),
        credits_used=row.credits_used,
        error=row.error,
    )


async def start(
    db: Any,
    org: str,
    connection_id: str,
    kind: str,
    *,
    counts: dict[str, Any] | None = None,
    estimate: dict[str, Any] | None = None,
) -> str:
    """Take the run lock: insert the ``running`` row, and return its id.

    Raises :class:`SyncRunning` when another run holds the lock. The insert
    runs in a SAVEPOINT, so the refusal leaves the caller's transaction usable.
    """
    await db.execute(text(_TAKEOVER_SQL), {"org": org, "secs": float(STALE_AFTER_SECONDS)})
    try:
        async with db.begin_nested():
            row = (await db.execute(text(_INSERT_SQL), {
                "org": org,
                "connection": connection_id,
                "kind": kind,
                "counts": json.dumps(counts or {}),
                "estimate": json.dumps(estimate) if estimate is not None else None,
            })).first()
    except IntegrityError as exc:
        raise SyncRunning("a sync run is already running for this organization") from exc
    return str(row.id)


async def progress(
    db: Any,
    org: str,
    run_id: str,
    counts: dict[str, Any],
    estimate: dict[str, Any] | None,
    credits: int,
) -> None:
    """Write the counts and the heartbeat. Raises :class:`RunLost` if the lock moved."""
    result = await db.execute(text(_PROGRESS_SQL), {
        "id": run_id, "org": org, "counts": json.dumps(counts),
        "estimate": json.dumps(estimate) if estimate is not None else None,
        "credits": credits,
    })
    if (result.rowcount or 0) == 0:
        raise RunLost("the run lock was taken over")


async def finish(
    db: Any,
    org: str,
    run_id: str,
    status: str,
    error: str | None,
    counts: dict[str, Any],
    estimate: dict[str, Any] | None,
    credits: int,
) -> None:
    """Close the run. A run that was taken over stays as the takeover wrote it."""
    await db.execute(text(_FINISH_SQL), {
        "id": run_id, "org": org, "status": status, "error": error,
        "counts": json.dumps(counts),
        "estimate": json.dumps(estimate) if estimate is not None else None,
        "credits": credits,
    })


async def latest(db: Any, org: str) -> RunRow | None:
    row = (await db.execute(text(_LATEST_SQL), {"org": org})).first()
    return _row(row) if row is not None else None


async def last_success_at(db: Any, org: str) -> datetime | None:
    row = (await db.execute(text(_LAST_OK_SQL), {"org": org})).first()
    return row.at if row is not None else None


async def credits_used_24h(db: Any, org: str) -> int:
    row = (await db.execute(text(_CREDITS_24H_SQL), {"org": org})).first()
    return int(row.used or 0)


async def resumable_import(db: Any, org: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """The counts and the estimate of the last import that did not end.

    A resumed import carries them on, so the progress bar does not go back to 0.
    """
    row = (await db.execute(text(_RESUMABLE_IMPORT_SQL), {"org": org})).first()
    if row is None:
        return {}, None
    return _json(row.counts) or {}, _json(row.estimate)


@dataclass(frozen=True)
class DueInfo:
    db_now: datetime
    last_finished: datetime | None
    last_credits: int | None
    running: bool


async def due_info(db: Any, org: str) -> DueInfo:
    """When the last run ended, what it spent, and whether a live run holds the lock."""
    row = (await db.execute(text(_DUE_SQL), {
        "org": org, "stale": float(STALE_AFTER_SECONDS),
    })).first()
    return DueInfo(row.db_now, row.last_finished, row.last_credits, bool(row.running))


async def member_emails(db: Any, org: str) -> frozenset[str]:
    """The active members of the organization, in lower case."""
    rows = (await db.execute(text(_MEMBERS_SQL), {"org": org})).fetchall()
    return frozenset(str(r.email) for r in rows if r.email)


# ── Cursors (crm_sync_cursors, keyed organization_id + module) ──────────────


@dataclass(frozen=True)
class CursorRow:
    """The stored state of one read. ``module`` is ``zoho:<entity>`` or
    ``zoho:<entity>:deleted``."""

    module: str
    last_pulled_at: datetime | None
    state: dict[str, Any] | None
    initial_done_at: datetime | None
    last_reconciled_at: datetime | None


_CURSORS_SQL = (
    "SELECT module, last_pulled_at, cursor_state, initial_done_at, "
    "last_reconciled_at FROM crm_sync_cursors "
    "WHERE organization_id = CAST(:org AS uuid) AND module LIKE :prefix"
)

_CURSOR_UPSERT_SQL = (
    "INSERT INTO crm_sync_cursors (organization_id, module, last_pulled_at, "
    "last_run_at, last_status, cursor_state, initial_done_at, "
    "last_reconciled_at) VALUES (CAST(:org AS uuid), :module, :pulled, now(), "
    ":status, CAST(:state AS jsonb), :initial, :reconciled) "
    "ON CONFLICT (organization_id, module) DO UPDATE SET "
    "last_pulled_at = EXCLUDED.last_pulled_at, last_run_at = now(), "
    "last_status = EXCLUDED.last_status, cursor_state = EXCLUDED.cursor_state, "
    "initial_done_at = EXCLUDED.initial_done_at, "
    "last_reconciled_at = EXCLUDED.last_reconciled_at"
)


async def read_cursors(db: Any, org: str, prefix: str) -> dict[str, CursorRow]:
    rows = (await db.execute(
        text(_CURSORS_SQL), {"org": org, "prefix": f"{prefix}%"},
    )).fetchall()
    return {
        str(r.module): CursorRow(
            module=str(r.module),
            last_pulled_at=r.last_pulled_at,
            state=_json(r.cursor_state),
            initial_done_at=r.initial_done_at,
            last_reconciled_at=r.last_reconciled_at,
        )
        for r in rows
    }


async def write_cursor(db: Any, org: str, row: CursorRow, status: str) -> None:
    await db.execute(text(_CURSOR_UPSERT_SQL), {
        "org": org,
        "module": row.module,
        "pulled": row.last_pulled_at,
        "status": status,
        "state": json.dumps(row.state) if row.state is not None else None,
        "initial": row.initial_done_at,
        "reconciled": row.last_reconciled_at,
    })


__all__ = [
    "KIND_IMPORT",
    "KIND_INCREMENTAL",
    "KIND_RECONCILE",
    "STALE_AFTER_SECONDS",
    "STATUS_FAILED",
    "STATUS_OK",
    "STATUS_RUNNING",
    "STATUS_STOPPED",
    "CursorRow",
    "DueInfo",
    "RunLost",
    "RunRow",
    "SyncRunning",
    "credits_used_24h",
    "due_info",
    "finish",
    "last_success_at",
    "latest",
    "member_emails",
    "progress",
    "read_cursors",
    "resumable_import",
    "start",
    "write_cursor",
]
