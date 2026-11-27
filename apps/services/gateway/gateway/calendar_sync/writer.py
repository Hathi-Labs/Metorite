"""The row writer of the calendar sync (WS-CAL CE-10).

Spec: ``project-docs/specs/calendar_events_reminders.md`` §8.1 items 3 and 5,
and migration 331.

One statement per event: an INSERT on the unique key
``calendar_entries_external_uq`` (org, connection, external id). A second
sync of the same event adds no row. An ``external`` row takes every provider
field. A row that Metorite wrote out (``kind='event'``, CE-11) keeps its own
fields: the sync updates its etag and url only, and calls ``on_matched`` so
the write-out code (CE-13) can move the block.

⚠️ **The series skip (§8.1 item 5).** With ``singleEvents`` the provider sends
each instance of a series with its own id. An instance whose master id is the
``external_id`` of a ``metorite`` row on the same connection comes from a
series that Metorite wrote out, so it is skipped.

The title is cut to 200 characters and an untitled event gets a title
(``provider.clean_title``), so the CHECK of 331 never fails the batch.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text

from gateway.calendar_sync.provider import RemoteEvent, SyncResult, clean_title
from gateway.connections import store

_COLS = ("title", "notes", "location", "starts_at", "ends_at", "start_date",
         "end_date", "tz", "status", "busy", "conference_url",
         "external_series_id", "original_start", "original_start_date")
_KEEP = ", ".join(
    f"{c} = CASE WHEN calendar_entries.kind = 'external' "
    f"THEN EXCLUDED.{c} ELSE calendar_entries.{c} END" for c in _COLS)

_UPSERT_SQL = (
    "INSERT INTO calendar_entries (organization_id, owner_email, kind, title, "
    "notes, location, starts_at, ends_at, start_date, end_date, tz, status, "
    "busy, conference_url, source, connection_id, external_id, etag, "
    "external_url, external_series_id, original_start, original_start_date) "
    "VALUES (CAST(:org AS uuid), :owner, 'external', :title, :notes, :location, "
    ":starts_at, :ends_at, :start_date, :end_date, :tz, :status, :busy, "
    ":conference_url, :source, CAST(:conn AS uuid), :external_id, :etag, :url, "
    ":series, :original_start, :original_start_date) "
    "ON CONFLICT (organization_id, connection_id, external_id) "
    "WHERE connection_id IS NOT NULL AND external_id IS NOT NULL DO UPDATE SET "
    f"{_KEEP}, etag = EXCLUDED.etag, external_url = EXCLUDED.external_url, "
    "updated_at = now() "
    "WHERE EXCLUDED.etag IS NULL OR calendar_entries.etag IS DISTINCT FROM EXCLUDED.etag "
    "RETURNING id, kind, (xmax = 0) AS inserted")

_DELETE_SQL = (
    "DELETE FROM calendar_entries WHERE organization_id = CAST(:org AS uuid) "
    "AND connection_id = CAST(:conn AS uuid) AND kind = 'external' "
    "AND external_id = :external_id")

_PRUNE_SQL = (
    "DELETE FROM calendar_entries WHERE organization_id = CAST(:org AS uuid) "
    "AND connection_id = CAST(:conn AS uuid) AND kind = 'external' "
    "AND NOT (external_id = ANY(:seen))")

_OWN_MASTERS_SQL = (
    "SELECT external_id FROM calendar_entries "
    "WHERE organization_id = CAST(:org AS uuid) AND connection_id = CAST(:conn AS uuid) "
    "AND kind = 'event' AND external_id = ANY(:ids)")

OnMatched = Callable[[Any, str, RemoteEvent], Awaitable[None]]


@dataclass(frozen=True)
class WriteStats:
    added: int = 0
    updated: int = 0
    removed: int = 0
    skipped: int = 0


async def apply_changes(
    db: Any, org: str, conn: store.Connection, source: str, result: SyncResult,
    *, on_matched: OnMatched | None = None,
) -> WriteStats:
    """Write one read into ``calendar_entries``. The owner is the connection's
    member, from the row, never from a provider field."""
    owner = (conn.member_email or "").strip().lower()
    if not owner:
        raise ValueError("a calendar connection has a member")
    series_ids = sorted({e.external_series_id for e in result.changes
                         if e.external_series_id and not e.deleted})
    own_masters: set[str] = set()
    if series_ids:
        rows = (await db.execute(text(_OWN_MASTERS_SQL), {
            "org": org, "conn": conn.id, "ids": series_ids})).fetchall()
        own_masters = {r.external_id for r in rows}
    added = updated = removed = skipped = 0
    seen: list[str] = []
    for ev in result.changes:
        if ev.deleted:
            removed += (await db.execute(text(_DELETE_SQL), {
                "org": org, "conn": conn.id, "external_id": ev.external_id})).rowcount or 0
            continue
        if ev.external_series_id and ev.external_series_id in own_masters:
            skipped += 1
            continue
        seen.append(ev.external_id)
        row = (await db.execute(text(_UPSERT_SQL), {
            "org": org, "owner": owner, "title": clean_title(ev.title),
            "notes": ev.notes, "location": ev.location,
            "starts_at": ev.starts_at, "ends_at": ev.ends_at,
            "start_date": ev.start_date, "end_date": ev.end_date, "tz": ev.tz,
            "status": ev.status, "busy": ev.busy, "conference_url": ev.conference_url,
            "source": source, "conn": conn.id, "external_id": ev.external_id,
            "etag": ev.etag, "url": ev.url, "series": ev.external_series_id,
            "original_start": ev.original_start,
            "original_start_date": ev.original_start_date,
        })).first()
        if row is None:
            continue  # same etag: nothing to do
        if row.kind == "event":
            if on_matched is not None:
                await on_matched(db, str(row.id), ev)
        elif row.inserted:
            added += 1
        else:
            updated += 1
    if result.full:
        removed += (await db.execute(text(_PRUNE_SQL), {
            "org": org, "conn": conn.id, "seen": seen})).rowcount or 0
    return WriteStats(added, updated, removed, skipped)
