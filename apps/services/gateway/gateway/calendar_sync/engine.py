"""One sync of one calendar connection (WS-CAL CE-10).

Spec: ``project-docs/specs/calendar_events_reminders.md`` §8.1 item 4.

The steps, and why they are shaped so:

1. **Read** (short session): the row, and its flag. A flag-off org, a member
   off ``CALENDAR_SYNC_MEMBERS``, a ``disconnected`` or ``needs_reconnect``
   row ends here. NO request leaves.
2. **The token** (``connections.fresh_credential``, its own session).
3. **The provider call, with no session open.** A cursor of Google or Graph
   that is dead (``CursorExpired``) gets ONE full list. A connection whose
   last full list is older than a day gets a full list too (it also prunes
   the rows outside the window, spec audit G3).
4. **Write** (one transaction): the row is locked ``FOR NO KEY UPDATE`` (the
   same lock as the disconnect). If the row is no longer ``connected``, or the
   cursor moved while the call ran (another worker), the result is dropped.
   Else the writer runs, the cursor is saved and ``last_sync_at`` is set.

An error sets ``last_error`` to a short word. ``NeedsReconnect`` is marked by
``shared_token`` and is never retried.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from acb_common import get_logger
from acb_common.db import tenant_session

from gateway.calendar_sync import connections, flags
from gateway.calendar_sync.provider import (
    CalendarProvider,
    CursorExpired,
    NeedsReconnect,
    ProviderError,
    RateLimited,
    SourceError,
    Window,
)
from gateway.calendar_sync.writer import WriteStats, apply_changes
from gateway.connections import store

_log = get_logger("calendar_sync.engine")

#: A full list at least this often.
FULL_EVERY = timedelta(hours=24)

#: Connections in a sync in THIS worker. Another worker is caught by the cursor
#: check of step 4.
_running: set[str] = set()


@dataclass(frozen=True)
class SyncOutcome:
    status: str  # ok | skipped | busy | needs_reconnect | error
    added: int = 0
    updated: int = 0
    removed: int = 0
    code: str | None = None


def _now() -> datetime:
    return datetime.now(UTC)


async def sync_connection(
    org: str, connection_id: str, provider: CalendarProvider,
    *, force_full: bool = False,
) -> SyncOutcome:
    """Sync one connection. Never raises for a provider failure."""
    if connection_id in _running:
        return SyncOutcome("busy")
    _running.add(connection_id)
    try:
        return await _sync(org, connection_id, provider, force_full)
    finally:
        _running.discard(connection_id)


async def _sync(
    org: str, connection_id: str, provider: CalendarProvider, force_full: bool,
) -> SyncOutcome:
    if not flags.read_allowed(org):
        return SyncOutcome("skipped", code="flag_off")
    async with tenant_session(org) as db:
        conn = await connections.get_connection(db, org, connection_id)
    if conn is None or conn.provider != provider.provider:
        return SyncOutcome("skipped", code="not_found")
    if not flags.read_allowed(org, conn.member_email):
        return SyncOutcome("skipped", code="flag_off")
    if conn.status == store.STATUS_NEEDS_RECONNECT:
        return SyncOutcome("needs_reconnect", code=conn.last_error)
    if conn.status != store.STATUS_CONNECTED:
        return SyncOutcome("skipped", code=conn.status)
    start_cursor = connections.cursor(conn)
    last_full = connections.full_sync_at(conn)
    now = _now()
    full = (force_full or start_cursor is None or last_full is None
            or now - last_full >= FULL_EVERY)
    try:
        cred = await connections.fresh_credential(org, conn, provider)
        if cred is None:
            return SyncOutcome("skipped", code="no_credential")
        window = Window.around(now)
        try:
            result = await provider.sync(cred, None if full else start_cursor, window)
        except CursorExpired:
            full = True
            result = await provider.sync(cred, None, window)
    except NeedsReconnect as exc:
        code = store.safe_error_code(exc.code or "unauthorized")
        await _mark_reconnect(org, connection_id, code)
        return SyncOutcome("needs_reconnect", code=code)
    except (RateLimited, ProviderError, SourceError) as exc:
        code = store.safe_error_code(getattr(exc, "code", None) or "provider_error")
        await _record_error(org, connection_id, code)
        _log.warning("calendar_sync.failed", organization_id=org, code=code)
        return SyncOutcome("error", code=code)
    except asyncio.CancelledError:
        raise
    async with tenant_session(org) as db:
        locked = await store.lock_by_id(db, org, connection_id)
        if locked is None or locked.status != store.STATUS_CONNECTED:
            return SyncOutcome("skipped", code="disconnected")
        if connections.cursor(locked) != start_cursor:
            return SyncOutcome("busy")  # another worker wrote first
        if (locked.provider_meta.get(connections.META_ACCOUNT_ID)
                != conn.provider_meta.get(connections.META_ACCOUNT_ID)):
            return SyncOutcome("busy")  # the member connected another account
        stats: WriteStats = await apply_changes(db, org, locked, provider.source, result)
        await connections.save_cursor(
            db, org, connection_id, result.next_cursor,
            full_at=now if full else None)
        await store.set_status(db, org, connection_id, store.STATUS_CONNECTED, None)
        await store.mark_synced(db, org, connection_id)
    _log.info("calendar_sync.ok", organization_id=org, added=stats.added,
              updated=stats.updated, removed=stats.removed, full=full)
    return SyncOutcome("ok", stats.added, stats.updated, stats.removed)


async def _record_error(org: str, connection_id: str, code: str) -> None:
    try:
        async with tenant_session(org) as db:
            row = await store.get_by_id(db, org, connection_id)
            if row is not None and row.status == store.STATUS_CONNECTED:
                await store.set_status(db, org, connection_id, store.STATUS_CONNECTED, code)
    except Exception as exc:  # the record never hides the sync error
        _log.warning("calendar_sync.error_record_failed", error=type(exc).__name__)


async def _mark_reconnect(org: str, connection_id: str, code: str) -> None:
    """Mark the row (``shared_token`` already did for a dead refresh token; an
    API 401 with a fresh token comes here)."""
    try:
        async with tenant_session(org) as db:
            await store.mark_needs_reconnect(db, org, connection_id, code)
    except Exception as exc:
        _log.warning("calendar_sync.reconnect_mark_failed", error=type(exc).__name__)
