"""The calendar connection helpers (WS-CAL CE-10, CE-11).

Spec: ``project-docs/specs/calendar_events_reminders.md`` §8.1 item 3.

A calendar connection is a row of ``integration_connections`` with
``owning_app='calendar'``, ``scope_kind='member'`` and the member's address.
The token is in ``credentials_encrypted``, and ONLY ``connections/store.py``
reads it. The cursor, the account and the id of the "Metorite" calendar are
plain facts in ``provider_meta`` (no secret). The mail row of the same account
stays its own, so a disconnect here breaks nothing in the mail app.

Every function takes the ``db`` of a ``tenant_session``, and the org comes
from the server (R5). Each statement also names the org.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from acb_common import get_logger
from sqlalchemy import text

from gateway.calendar_sync.provider import (
    METORITE_CALENDAR_NAME,
    OWNING_APP,
    CalendarProvider,
)
from gateway.connections import shared_token, store
from gateway.crm_sources.base import SourceCredential

_log = get_logger("calendar_sync.connections")

META_CURSOR = "sync_cursor"
META_FULL_AT = "full_sync_at"
META_CALENDAR_ID = "metorite_calendar_id"
META_ACCOUNT_EMAIL = "account_email"
META_ACCOUNT_ID = "account_id"

#: Provider names on the connection row, and the word the API uses.
PROVIDER_ROWS = {"google": "google_calendar", "outlook": "outlook_calendar"}
PROVIDER_WORDS = {v: k for k, v in PROVIDER_ROWS.items()}
SOURCES = {"google_calendar": "google", "outlook_calendar": "outlook"}

_CLEAR_META_SQL = (
    "UPDATE integration_connections SET provider_meta = provider_meta "
    "- :k1 - :k2 - :k3, updated_at = now() "
    "WHERE id = CAST(:id AS uuid) AND organization_id = CAST(:org AS uuid)")

_DELETE_EXTERNAL_SQL = (
    "DELETE FROM calendar_entries WHERE organization_id = CAST(:org AS uuid) "
    "AND connection_id = CAST(:id AS uuid) AND kind = 'external'")

_CLEAR_OWN_SQL = (
    "UPDATE calendar_entries SET connection_id = NULL, external_id = NULL, "
    "etag = NULL, external_url = NULL, push_to_provider = NULL, "
    "provider_sync_state = NULL, provider_sync_error = NULL, updated_at = now() "
    "WHERE organization_id = CAST(:org AS uuid) "
    "AND connection_id = CAST(:id AS uuid) AND kind <> 'external'")

_COUNT_SQL = (
    "SELECT connection_id, count(*) AS n FROM calendar_entries "
    "WHERE organization_id = CAST(:org AS uuid) AND kind = 'external' "
    "AND connection_id = ANY(CAST(:ids AS uuid[])) GROUP BY connection_id")


def is_calendar(conn: store.Connection) -> bool:
    return (conn.owning_app == OWNING_APP and conn.scope_kind == store.SCOPE_MEMBER
            and conn.provider in SOURCES)


async def get_connection(db: Any, org: str, connection_id: str) -> store.Connection | None:
    """One calendar row by id, or ``None``. No blob."""
    conn = await store.get_by_id(db, org, connection_id)
    return conn if conn is not None and is_calendar(conn) else None


async def member_connections(db: Any, org: str, member: str) -> list[store.Connection]:
    """The calendar rows of one member. Never another member's."""
    me = (member or "").strip().lower()
    return [c for c in await store.list_all(db, org)
            if is_calendar(c) and (c.member_email or "").lower() == me]


async def all_connections(db: Any, org: str) -> list[store.Connection]:
    """Every calendar row of the org, for the loop."""
    return [c for c in await store.list_all(db, org) if is_calendar(c)]


async def entry_counts(db: Any, org: str, ids: list[str]) -> dict[str, int]:
    if not ids:
        return {}
    rows = (await db.execute(text(_COUNT_SQL), {"org": org, "ids": ids})).fetchall()
    return {str(r.connection_id): int(r.n) for r in rows}


async def fresh_credential(
    org: str, conn: store.Connection, provider: CalendarProvider,
) -> SourceCredential | None:
    """The token of the connection, refreshed and stored when due.

    Opens its own tenant session (``shared_token``): a second worker waits on
    the row lock and then reads the token the first one wrote. ``None`` when
    the row is gone or disconnected. Raises ``NeedsReconnect`` for a dead
    refresh token, and the row is marked first.
    """
    return await shared_token.fresh_credential(
        org, provider=conn.provider, owning_app=OWNING_APP,
        refresh=provider.refresh, needs_refresh=provider.needs_refresh,
        now=lambda: datetime.now(UTC),
        scope_kind=store.SCOPE_MEMBER, member_email=conn.member_email,
    )


def cursor(conn: store.Connection) -> str | None:
    value = conn.provider_meta.get(META_CURSOR)
    return value if isinstance(value, str) and value else None


def full_sync_at(conn: store.Connection) -> datetime | None:
    raw = conn.provider_meta.get(META_FULL_AT)
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


async def save_cursor(
    db: Any, org: str, connection_id: str, new_cursor: str | None,
    *, full_at: datetime | None = None,
) -> None:
    extra: dict[str, Any] = {META_CURSOR: new_cursor or ""}
    if full_at is not None:
        extra[META_FULL_AT] = full_at.isoformat()
    await store.merge_provider_meta(db, org, connection_id, extra)


def metorite_calendar_id(conn: store.Connection) -> str | None:
    value = conn.provider_meta.get(META_CALENDAR_ID)
    return value if isinstance(value, str) and value else None


async def save_metorite_calendar_id(
    db: Any, org: str, connection_id: str, calendar_id: str,
) -> None:
    await store.merge_provider_meta(db, org, connection_id, {META_CALENDAR_ID: calendar_id})


async def ensure_metorite_calendar(
    db: Any, org: str, conn: store.Connection, provider: CalendarProvider,
    cred: SourceCredential,
) -> str:
    """The id of the "Metorite" calendar: cached, found by name, or created.

    Idempotent. A calendar of that name that the account already has is used,
    so a reconnect makes no twin.
    """
    cached = metorite_calendar_id(conn)
    if cached:
        return cached
    for cal in await provider.list_calendars(cred):
        if cal.name == METORITE_CALENDAR_NAME and cal.writable and not cal.primary:
            await save_metorite_calendar_id(db, org, conn.id, cal.id)
            return cal.id
    new_id = await provider.create_calendar(cred, METORITE_CALENDAR_NAME)
    await save_metorite_calendar_id(db, org, conn.id, new_id)
    return new_id


async def disconnect_connection(db: Any, org: str, conn: store.Connection) -> int:
    """The three steps of spec §8.1 item 3, in the caller's transaction.

    1. Delete the ``external`` rows of the connection.
    2. Clear the five provider columns (and the etag and url) on the other
       rows that name the connection.
    3. Delete the blob and set ``disconnected``. The row stays as a record, so
       a reconnect reuses it. The cursor and the calendar id go.

    Returns how many external rows it deleted. The caller locked the row first
    (``store.lock_by_id``), so a sync that ends later sees ``disconnected`` and
    writes nothing.
    """
    params = {"org": org, "id": conn.id}
    removed = (await db.execute(text(_DELETE_EXTERNAL_SQL), params)).rowcount or 0
    await db.execute(text(_CLEAR_OWN_SQL), params)
    await store.disconnect(db, org, conn.id)
    await db.execute(text(_CLEAR_META_SQL), {
        **params, "k1": META_CURSOR, "k2": META_FULL_AT, "k3": META_CALENDAR_ID})
    return int(removed)


async def purge_calendar_member(db: Any, org: str, email: str) -> int:
    """Delete each calendar connection of a leaver, in the purge transaction.

    It runs AFTER the member's ``calendar_entries`` went (the purge list), so
    no entry names the connection and the delete succeeds (migration 331). The
    entries of a colleague never name a member's connection. No revoke is sent.
    """
    me = (email or "").strip().lower()
    if not me:
        return 0
    count = 0
    for conn in await member_connections(db, org, me):
        await db.execute(text(_CLEAR_OWN_SQL), {"org": org, "id": conn.id})
        await store.delete(db, org, conn.provider, OWNING_APP,
                           scope_kind=store.SCOPE_MEMBER, member_email=conn.member_email)
        count += 1
    if count:
        _log.info("calendar_sync.connections_purged", organization_id=org, count=count)
    return count
