"""Tasks · calendar entries — the event store of the Calendar.

Spec: ``project-docs/specs/calendar_events_reminders.md`` §3.3, §3.4, §4.1,
§9 and §10 CE-4 · **D138** · board WS-CAL.

    POST   /tasks/calendar/entries              → make one event, with my organizer row
    PATCH  /tasks/calendar/entries/{entry_id}   → change one of my events
    DELETE /tasks/calendar/entries/{entry_id}   → delete one of my events

**The read is not here.** ``GET /projects/my/calendar`` stays the one read of
the Calendar (§3.4). It calls :func:`read_my_entries` inside its own tenant
session and puts the answer under the key ``entries``. The SQL of who sees an
entry lives here, beside the table it reads.

**Who sees an entry (§4.1, D138 Q2).** RLS binds the ORGANIZATION only. The
person is a second axis: the owner, and each member with an attendee row that
names them in ``member_email``. Nobody else. A guest row (``member_email``
NULL) gives nobody the entry. :data:`VISIBLE_SQL` holds the read rule once.

**Who changes an entry.** Only its owner, and only an entry of kind
``event``. :data:`MINE_SQL` holds that rule once, and the load, the UPDATE
and the DELETE each use it. Any other row answers 404, never 403: a
colleague's entry, an entry I only attend, and every ``external`` copy of a
provider event. So the code does not tell a caller that the row exists.

**Identity.** The owner is ``user.email``, lower case (:func:`_member`), never
a body field or a query parameter (R5). The body model forbids extra keys, so
``owner_email``, ``kind``, ``source`` and ``attendees`` answer 422.

**Attendees.** CE-4 writes ONE attendee row: the organizer row of the owner,
in the transaction of the entry. Attendee input, invite cards and RSVP belong
to CE-9.

**Time.** A timed entry takes ``starts_at`` and ``ends_at`` WITH a UTC offset.
A naive time is a 422, because it reads in the zone of the database session.
An all-day entry takes ``start_date`` and ``end_date`` as dates, with no zone
shift, and ``end_date`` is EXCLUSIVE (migration 324). ``tz`` is stored once
``zoneinfo`` knows it, and nothing here applies it. ``recurrence``,
``series_id`` and ``original_start`` are stored as given. CE-8 expands a
series, and nothing here does.

**The gate.** The ``/tasks`` router gives ``feature:tasks`` (D138 Q10).
``CALENDAR_EVENTS`` off makes every route here answer 404 before the shape of
the body is checked (:func:`_require_events_on`, a route dependency), and the
read runs no entry query.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from acb_auth import UserContext, get_current_user
from acb_common import get_settings
from acb_common.pg_text import storable
from fastapi import Depends, HTTPException, Response
from gateway.routes.tasks.core import _tenant_session, router
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

TITLE_MAX = 200
NOTES_MAX = 10_000
LOCATION_MAX = 500
URL_MAX = 2_000
RECURRENCE_MAX = 2_000
#: Migration 324's status CHECK.
STATUSES: tuple[str, ...] = ("confirmed", "tentative", "cancelled")

_TIMED: tuple[str, str] = ("starts_at", "ends_at")
_DAYS: tuple[str, str] = ("start_date", "end_date")


# ── The flag ───────────────────────────────────────────────────────────────


def events_enabled() -> bool:
    """The switch of the event store (``calendar_events``, default off).

    The ONE reader of the setting. ``/version``, :func:`_require_events_on`
    and the read in ``routes/projects/personal.py::my_calendar`` call it.
    """
    return bool(getattr(get_settings(), "calendar_events", False))


async def _require_events_on() -> None:
    """404 while the flag is off, and nothing is written.

    A route dependency, so it runs before FastAPI checks the SHAPE of the
    body: with the flag off, valid JSON of the wrong shape answers 404, not
    422. A body that is not JSON at all still answers 422, because FastAPI
    decodes the JSON before any dependency runs. Neither writes a row.
    """
    if not events_enabled():
        raise HTTPException(status_code=404, detail="Not Found")


_ON = [Depends(_require_events_on)]


# ── Models ─────────────────────────────────────────────────────────────────


class EntryIn(BaseModel):
    """A new event. A timed pair or an all-day pair, never both.

    ``extra="forbid"``: the server decides the owner, the kind and the
    source, and CE-4 takes no attendees. A body that names one is a 422.
    """

    model_config = ConfigDict(extra="forbid")

    title: str
    notes: str | None = Field(None, max_length=NOTES_MAX)
    location: str | None = Field(None, max_length=LOCATION_MAX)
    starts_at: str | None = None
    ends_at: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    tz: str | None = None
    recurrence: str | None = Field(None, max_length=RECURRENCE_MAX)
    series_id: UUID | None = None
    original_start: str | None = None
    original_start_date: str | None = None
    status: Literal["confirmed", "tentative", "cancelled"] = "confirmed"
    busy: bool = True
    conference_url: str | None = Field(None, max_length=URL_MAX)
    task_id: UUID | None = None
    crm_deal_id: UUID | None = None
    crm_contact_id: UUID | None = None
    meeting_id: UUID | None = None


class EntryPatch(BaseModel):
    """A change to one of my events. Only the fields sent change. ``null``
    clears a nullable field. A time pair sent whole replaces the other pair."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    notes: str | None = Field(None, max_length=NOTES_MAX)
    location: str | None = Field(None, max_length=LOCATION_MAX)
    starts_at: str | None = None
    ends_at: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    tz: str | None = None
    recurrence: str | None = Field(None, max_length=RECURRENCE_MAX)
    series_id: UUID | None = None
    original_start: str | None = None
    original_start_date: str | None = None
    status: Literal["confirmed", "tentative", "cancelled"] | None = None
    busy: bool | None = None
    conference_url: str | None = Field(None, max_length=URL_MAX)
    task_id: UUID | None = None
    crm_deal_id: UUID | None = None
    crm_contact_id: UUID | None = None
    meeting_id: UUID | None = None


class EntryModel(BaseModel):
    """One entry on the wire. It carries no tenant, and none of the sync
    columns (``connection_id``, ``external_id``, ``etag`` and the write-out
    state). ``my_role``, ``my_rsvp`` and ``my_lead_minutes`` come from MY
    attendee row, and are NULL when I have none."""

    id: str
    kind: str
    source: str
    owner_email: str
    is_mine: bool
    my_role: str | None = None
    my_rsvp: str | None = None
    my_lead_minutes: int | None = None
    title: str
    notes: str | None = None
    location: str | None = None
    all_day: bool
    starts_at: str | None = None
    ends_at: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    tz: str | None = None
    recurrence: str | None = None
    series_id: str | None = None
    original_start: str | None = None
    original_start_date: str | None = None
    status: str
    busy: bool
    conference_url: str | None = None
    external_url: str | None = None
    task_id: str | None = None
    crm_deal_id: str | None = None
    crm_contact_id: str | None = None
    meeting_id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


def _wire(value: Any) -> Any:
    """A value for JSON. An instant goes out in UTC, so the answer does not
    depend on the zone of the database session. A date goes out as it is."""
    if isinstance(value, datetime):
        return (value.astimezone(UTC) if value.tzinfo else value).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    return value


def _out(row: Any, me: str) -> dict[str, Any]:
    data = {
        name: _wire(getattr(row, name, None))
        for name in EntryModel.model_fields
        if name not in ("is_mine", "all_day")
    }
    data["is_mine"] = row.owner_email == me
    data["all_day"] = row.start_date is not None
    return EntryModel(**data).model_dump()


# ── Inputs, checked before any write ─────────────────────────────────────


def _member(user: Any) -> str:
    """The owner, from the authenticated session only (R5), lower case.

    A caller with no address would share one bucket with every other caller
    with no address, so it is refused.
    """
    email = (getattr(user, "email", None) or "").strip().lower()
    if "@" not in email:
        raise HTTPException(
            status_code=403, detail="A calendar entry needs a signed-in member.",
        )
    return email


def _instant(value: Any, field: str) -> datetime:
    """An ISO 8601 instant WITH an offset, as UTC. A naive time is a 422."""
    raw = str(value or "").strip()
    try:
        when = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(
            status_code=422, detail=f"{field} is not an ISO 8601 time: {raw!r}.",
        ) from None
    if when.tzinfo is None or when.utcoffset() is None:
        raise HTTPException(
            status_code=422,
            detail=f"{field} needs a UTC offset, such as 2026-10-31T09:00:00+05:30.",
        )
    try:
        # A time at the edge of the calendar has no UTC form.
        return when.astimezone(UTC)
    except OverflowError:
        raise HTTPException(
            status_code=422, detail=f"{field} is out of range: {raw!r}.",
        ) from None


def _day(value: Any, field: str) -> date:
    """A calendar date, ``YYYY-MM-DD``. It has no zone and never moves."""
    raw = str(value or "").strip()
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise HTTPException(
            status_code=422, detail=f"{field} is not a date (YYYY-MM-DD): {raw!r}.",
        ) from None


def _zone(value: str | None) -> str | None:
    """An IANA zone, stored as given, or None. An unknown zone is a 422."""
    clean = (value or "").strip()
    if not clean:
        return None
    try:
        ZoneInfo(clean)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        # OSError: a key that names a directory of the zone database, such as
        # "America", fails to open as a file, and is not a zone.
        raise HTTPException(
            status_code=422, detail=f"tz is not an IANA zone: {clean!r}.",
        ) from None
    return clean


def _title(value: str | None) -> str:
    clean: str = storable((value or "").strip())
    if not clean:
        raise HTTPException(status_code=422, detail="An entry needs a title.")
    if len(clean) > TITLE_MAX:
        raise HTTPException(
            status_code=422, detail=f"A title is at most {TITLE_MAX} characters.",
        )
    return clean


def _text(value: str | None) -> str | None:
    """Free text, with each character that Postgres refuses replaced."""
    if value is None:
        return None
    clean: str = storable(value.strip())
    return clean or None


def _url(value: str | None) -> str | None:
    """A join link: ``https://`` or ``http://`` only, so a link that runs
    script never reaches the event sheet."""
    clean: str = storable((value or "").strip())
    if not clean:
        return None
    if not clean.lower().startswith(("https://", "http://")):
        raise HTTPException(
            status_code=422, detail="conference_url must start with https:// or http://.",
        )
    return clean


def _time_pair(values: dict[str, Any]) -> dict[str, Any]:
    """Exactly one complete pair, checked. The other pair comes back None.

    A timed pair is two instants with ``ends_at >= starts_at``. An all-day
    pair is two dates with ``end_date > start_date``: the end is EXCLUSIVE,
    so a one-day entry on 8 March ends on 9 March. A value that is already a
    ``datetime`` or a ``date`` comes from the stored row and is not parsed
    again.
    """
    starts, ends = values.get("starts_at"), values.get("ends_at")
    first, last = values.get("start_date"), values.get("end_date")
    timed = starts is not None or ends is not None
    all_day = first is not None or last is not None
    if timed == all_day:
        raise HTTPException(
            status_code=422,
            detail=(
                "Send one pair: starts_at and ends_at for a timed entry, or "
                "start_date and end_date for an all-day entry."
            ),
        )
    if timed:
        if starts is None or ends is None:
            raise HTTPException(
                status_code=422, detail="A timed entry needs starts_at and ends_at.",
            )
        start = starts if isinstance(starts, datetime) else _instant(starts, "starts_at")
        end = ends if isinstance(ends, datetime) else _instant(ends, "ends_at")
        if end < start:
            raise HTTPException(
                status_code=422, detail="ends_at must not be before starts_at.",
            )
        return {"starts_at": start, "ends_at": end, "start_date": None, "end_date": None}
    if first is None or last is None:
        raise HTTPException(
            status_code=422, detail="An all-day entry needs start_date and end_date.",
        )
    day0 = first if isinstance(first, date) else _day(first, "start_date")
    day1 = last if isinstance(last, date) else _day(last, "end_date")
    if day1 <= day0:
        raise HTTPException(
            status_code=422,
            detail=(
                "end_date is exclusive, so it must be after start_date. A "
                "one-day entry on 2026-03-08 ends on 2026-03-09."
            ),
        )
    return {"starts_at": None, "ends_at": None, "start_date": day0, "end_date": day1}


def _plain_fields(fields: dict[str, Any]) -> dict[str, Any]:
    """The fields that are not a time pair, checked. Only the keys sent."""
    out: dict[str, Any] = {}
    if "title" in fields:
        out["title"] = _title(fields["title"])
    for name in ("notes", "location", "recurrence"):
        if name in fields:
            out[name] = _text(fields[name])
    if "tz" in fields:
        out["tz"] = _zone(fields["tz"])
    if "conference_url" in fields:
        out["conference_url"] = _url(fields["conference_url"])
    if "original_start" in fields:
        raw = fields["original_start"]
        out["original_start"] = None if raw is None else _instant(raw, "original_start")
    if "original_start_date" in fields:
        raw = fields["original_start_date"]
        out["original_start_date"] = None if raw is None else _day(raw, "original_start_date")
    for name in ("status", "busy"):
        if name in fields:
            out[name] = fields[name]
    for name in ("series_id", "task_id", "crm_deal_id", "crm_contact_id", "meeting_id"):
        if name in fields:
            out[name] = None if fields[name] is None else str(fields[name])
    return out


# ── SQL ────────────────────────────────────────────────────────────────────
#
# RLS adds the organization to each statement. The person is the predicate.

#: The ONE rule of who changes an entry: its owner, and only an event made in
#: Metorite. The load, the UPDATE and the DELETE each use it. An `external`
#: copy belongs to the sync of CE-10, never to a route.
MINE_SQL = "owner_email = :me AND kind = 'event'"

_MINE_FOR_UPDATE_SQL = (
    "SELECT id, starts_at, ends_at, start_date, end_date FROM calendar_entries "
    f"WHERE id = CAST(:id AS uuid) AND {MINE_SQL} FOR UPDATE"
)

#: The ONE rule of who sees an entry (§4.1, D138 Q2): the owner, or a member
#: with an attendee row that names them in `member_email`. The join below
#: matches `member_email` only, so a guest row never matches a member.
VISIBLE_SQL = "(e.owner_email = :me OR a.id IS NOT NULL)"

_SELECT_SQL = (
    "SELECT e.*, a.role AS my_role, a.rsvp AS my_rsvp, "
    "a.lead_minutes AS my_lead_minutes "
    "FROM calendar_entries e "
    "LEFT JOIN calendar_entry_attendees a "
    "ON a.entry_id = e.id AND a.member_email = :me "
)

#: The window `[start, end)`. A timed entry overlaps it, and an entry of no
#: length counts at its start. An all-day entry is compared by date, on the
#: UTC window with one day of slack on each side, as `projects/calendar.py`
#: does. The client places it, and only the client knows the zone of the
#: viewer.
_WINDOW_SQL = (
    "((e.starts_at IS NOT NULL AND e.starts_at < :end "
    "  AND (e.ends_at > :start OR e.starts_at = :start)) "
    " OR (e.start_date IS NOT NULL AND e.start_date < :end_date "
    "  AND e.end_date > :start_date))"
)

_ORDER_SQL = (
    " ORDER BY COALESCE(e.start_date, (e.starts_at AT TIME ZONE 'UTC')::date), "
    "e.starts_at NULLS FIRST, e.created_at, e.id"
)

#: Each column that a POST or a PATCH may write, in a fixed order.
#: Identifiers come from this tuple only. Every value is a bound parameter.
_WRITE_COLUMNS: tuple[str, ...] = (
    "title", "notes", "location", "starts_at", "ends_at", "start_date",
    "end_date", "tz", "recurrence", "series_id", "original_start",
    "original_start_date", "status", "busy", "conference_url", "task_id",
    "crm_deal_id", "crm_contact_id", "meeting_id",
)
_UUID_COLUMNS = frozenset({"series_id", "task_id", "crm_deal_id", "crm_contact_id", "meeting_id"})


def _bind(column: str) -> str:
    return f"CAST(:{column} AS uuid)" if column in _UUID_COLUMNS else f":{column}"


async def read_my_entries(
    db: Any, me: str, start: datetime, end: datetime,
) -> list[dict[str, Any]]:
    """The entries in ``[start, end)`` that I own or attend, on *db*.

    The caller opened *db* with the tenant session, so RLS gives the org, and
    :data:`VISIBLE_SQL` gives the person. *me* is the session's address,
    lower case. A cancelled entry comes back with its ``status`` (CE-8 hides
    it). A repeat rule is not expanded (CE-8).
    """
    if "@" not in me:
        return []
    params = {
        "me": me, "start": start, "end": end,
        "start_date": start.astimezone(UTC).date() - timedelta(days=1),
        "end_date": end.astimezone(UTC).date() + timedelta(days=1),
    }
    rows = (await db.execute(
        text(f"{_SELECT_SQL} WHERE {VISIBLE_SQL} AND {_WINDOW_SQL}{_ORDER_SQL}"),
        params,
    )).fetchall()
    return [_out(row, me) for row in rows]


async def _read_one(db: Any, entry_id: Any, me: str) -> dict[str, Any]:
    """One entry in the shape of the read, under the same rule, or 404."""
    row = (await db.execute(
        text(f"{_SELECT_SQL} WHERE e.id = CAST(:id AS uuid) AND {VISIBLE_SQL}"),
        {"id": str(entry_id), "me": me},
    )).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Entry not found")
    return _out(row, me)


async def _load_mine(db: Any, entry_id: UUID, me: str) -> Any:
    """My event, locked for this transaction, with its time pair, or 404.

    The lock serialises two writes to one entry. Any row that is not mine,
    and every `external` row, is a 404, the same answer as a row that does
    not exist.
    """
    row = (await db.execute(
        text(_MINE_FOR_UPDATE_SQL), {"id": str(entry_id), "me": me},
    )).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Entry not found")
    return row


async def _require_my_series(db: Any, series_id: str, me: str, *, entry_id: str | None) -> None:
    """``series_id`` names one of MY events, and not the entry itself.

    It is stored as given (CE-8 expands it). The check stops a link from my
    entry to an entry of a colleague, which CE-8 could otherwise read.
    """
    if entry_id is not None and series_id == entry_id:
        raise HTTPException(status_code=422, detail="An entry cannot be its own series.")
    row = (await db.execute(
        text(f"SELECT 1 FROM calendar_entries WHERE id = CAST(:sid AS uuid) AND {MINE_SQL}"),
        {"sid": series_id, "me": me},
    )).fetchone()
    if row is None:
        raise HTTPException(status_code=422, detail="series_id names no event of yours.")


# ── Routes ─────────────────────────────────────────────────────────────────


@router.post("/calendar/entries", status_code=201, dependencies=_ON)
async def create_entry(
    payload: EntryIn, user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Make one event of kind ``event`` and source ``metorite``, and the
    organizer row of the caller, in one transaction."""
    me = _member(user)
    fields = payload.model_dump()
    values = _plain_fields(fields)
    values.update(_time_pair(fields))
    columns = [c for c in _WRITE_COLUMNS if c in values]
    async with _tenant_session() as db:
        if values.get("series_id") is not None:
            await _require_my_series(db, values["series_id"], me, entry_id=None)
        row = (await db.execute(
            text(
                f"INSERT INTO calendar_entries (owner_email, kind, source, "
                f"{', '.join(columns)}) "
                f"VALUES (:me, 'event', 'metorite', "
                f"{', '.join(_bind(c) for c in columns)}) RETURNING id"
            ),
            {"me": me, **{c: values[c] for c in columns}},
        )).fetchone()
        await db.execute(
            text(
                "INSERT INTO calendar_entry_attendees (entry_id, member_email, role, rsvp) "
                "VALUES (CAST(:id AS uuid), :me, 'organizer', 'accepted')"
            ),
            {"id": str(row.id), "me": me},
        )
        return await _read_one(db, row.id, me)


@router.patch("/calendar/entries/{entry_id}", dependencies=_ON)
async def update_entry(
    entry_id: UUID, payload: EntryPatch,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Change one of my events. Any other row is a 404 and changes nothing.

    A time pair sent whole replaces the stored pair, so a PATCH can turn a
    timed entry into an all-day one and back. Half a pair keeps the other
    half of the stored pair, and the result must still be one complete pair.
    """
    me = _member(user)
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=422, detail="Nothing to change.")
    for field in ("title", "status", "busy"):
        if field in changes and changes[field] is None:
            raise HTTPException(status_code=422, detail=f"{field} cannot be null.")
    values = _plain_fields(changes)
    sent = [k for k in (*_TIMED, *_DAYS) if k in changes]
    async with _tenant_session() as db:
        stored = await _load_mine(db, entry_id, me)
        if sent:
            merged: dict[str, Any] = {k: getattr(stored, k) for k in (*_TIMED, *_DAYS)}
            new_timed = any(changes.get(k) is not None for k in _TIMED)
            new_days = any(changes.get(k) is not None for k in _DAYS)
            if new_timed and not new_days:
                merged.update(dict.fromkeys(_DAYS))
            if new_days and not new_timed:
                merged.update(dict.fromkeys(_TIMED))
            merged.update({k: changes[k] for k in sent})
            values.update(_time_pair(merged))
        if values.get("series_id") is not None:
            await _require_my_series(db, values["series_id"], me, entry_id=str(entry_id))
        columns = [c for c in _WRITE_COLUMNS if c in values]
        assignments = [f"{c} = {_bind(c)}" for c in columns]
        assignments.append("updated_at = now()")
        await db.execute(
            text(
                f"UPDATE calendar_entries SET {', '.join(assignments)} "
                f"WHERE id = CAST(:id AS uuid) AND {MINE_SQL}"
            ),
            {**{c: values[c] for c in columns}, "id": str(entry_id), "me": me},
        )
        return await _read_one(db, entry_id, me)


@router.delete("/calendar/entries/{entry_id}", status_code=204, dependencies=_ON)
async def delete_entry(
    entry_id: UUID, user: UserContext = Depends(get_current_user),
) -> Response:
    """Delete one of my events, and its attendee rows by the cascade of
    migration 324. Any other row is a 404 and deletes nothing.

    A changed instance of a deleted series keeps its ``series_id``: the
    column has no foreign key, and CE-8 decides what a series delete does.
    """
    me = _member(user)
    async with _tenant_session() as db:
        await _load_mine(db, entry_id, me)
        await db.execute(
            text(f"DELETE FROM calendar_entries WHERE id = CAST(:id AS uuid) AND {MINE_SQL}"),
            {"id": str(entry_id), "me": me},
        )
    return Response(status_code=204)
