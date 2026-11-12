"""Microsoft Graph calendar client pieces for CE-10b (Outlook read) and CE-13.

Everything that maps Graph JSON to ``calendar_entries`` values is a PURE
function here: no database, no network, no clock. The async ``GraphCalendar``
class at the bottom is the only part that sends a request, and it takes an
``httpx.AsyncClient`` from its caller. The shared sync core of CE-10a owns the
loop, the token and the row writer. This module never builds a second one.

Graph shapes, as recorded in ``tests/unit/fixtures/outlook_calendar``:

* ``calendarView/delta`` answers pages. A page holds ``value`` and ONE of
  ``@odata.nextLink`` (more pages) or ``@odata.deltaLink`` (the cursor).
* A removed event is ``{"id": ..., "@removed": {"reason": "deleted"}}``.
* With ``Prefer: outlook.timezone="UTC"`` each ``dateTime`` is naive UTC.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Mapping, Optional
from urllib.parse import quote, urlencode

from gateway.calendar_sync.provider import RemoteEvent, OutEvent, clean_title

GRAPH_BASE = "https://graph.microsoft.com/v1.0"

#: Calendar scopes added by incremental consent. ``Calendars.ReadWrite``
#: because CE-13 makes the "Metorite" calendar.
CALENDAR_SCOPES = (
    "https://graph.microsoft.com/Calendars.ReadWrite",
)

#: Window of §8.1 item 4: 30 days back, 180 days ahead.
WINDOW_BACK_DAYS = 30
WINDOW_AHEAD_DAYS = 180

#: The name of the calendar that CE-13 creates.
METORITE_CALENDAR_NAME = "Metorite"

_TITLE_MAX = 200
_ERROR_MAX = 500


class GraphParseError(ValueError):
    """A Graph reply that does not hold the shape we need."""


# --------------------------------------------------------------------- types


@dataclass(frozen=True)
class DeltaPage:
    """One parsed page of a ``calendarView/delta`` reply."""

    events: tuple[RemoteEvent, ...] = ()
    removed_ids: tuple[str, ...] = ()
    next_link: Optional[str] = None
    delta_link: Optional[str] = None
    skipped: int = 0  # items with no usable id or time


@dataclass
class DeltaResult:
    """All pages of one sync of one calendar."""

    events: list[RemoteEvent] = field(default_factory=list)
    removed_ids: list[str] = field(default_factory=list)
    delta_link: Optional[str] = None
    skipped: int = 0


# ------------------------------------------------------------------- parsing


def _parse_dt(value: Mapping[str, Any] | None) -> Optional[datetime]:
    """``{"dateTime": "2026-11-12T10:00:00.0000000", "timeZone": "UTC"}``.

    A zone other than UTC is refused: the request sets
    ``outlook.timezone="UTC"``, so any other value is a reply we do not
    understand and we must not guess an offset.
    """
    if not isinstance(value, Mapping):
        return None
    raw = value.get("dateTime")
    zone = (value.get("timeZone") or "UTC").upper()
    if not isinstance(raw, str) or not raw:
        return None
    if zone not in ("UTC", "ETC/UTC", "GMT"):
        raise GraphParseError(f"unexpected time zone {zone!r}")
    text = raw.rstrip("Z")
    # Graph sends 7 fraction digits. fromisoformat takes 6 at most.
    if "." in text:
        head, frac = text.split(".", 1)
        text = f"{head}.{frac[:6]}"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise GraphParseError(f"bad dateTime {raw!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _status_and_busy(ev: Mapping[str, Any]) -> tuple[str, bool]:
    if ev.get("isCancelled") is True:
        status = "cancelled"
    else:
        # Graph ``responseStatus.response`` is tentativelyAccepted for Maybe.
        resp = (ev.get("responseStatus") or {}).get("response")
        show_as = ev.get("showAs")
        status = (
            "tentative"
            if resp == "tentativelyAccepted" or show_as == "tentative"
            else "confirmed"
        )
    # "free" and "workElsewhere" do not block time. Unknown values block.
    busy = ev.get("showAs") not in ("free", "workElsewhere")
    return status, busy


def _conference_url(ev: Mapping[str, Any]) -> Optional[str]:
    meeting = ev.get("onlineMeeting")
    if isinstance(meeting, Mapping) and isinstance(meeting.get("joinUrl"), str):
        return meeting["joinUrl"] or None
    url = ev.get("onlineMeetingUrl")
    return url if isinstance(url, str) and url else None


def parse_event(ev: Mapping[str, Any]) -> Optional[RemoteEvent]:
    """Map one Graph event to column values, or None when it is unusable.

    ``@removed`` items do not come here: ``parse_delta_page`` sorts them out.
    """
    ext_id = ev.get("id")
    if not isinstance(ext_id, str) or not ext_id:
        return None
    start = _parse_dt(ev.get("start"))
    end = _parse_dt(ev.get("end"))
    if start is None or end is None:
        return None
    status, busy = _status_and_busy(ev)

    starts_at: Optional[datetime] = start
    ends_at: Optional[datetime] = end
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    if ev.get("isAllDay") is True:
        # An all-day event is midnight to midnight. The table wants dates,
        # with end_date after start_date (exclusive end, as Graph gives it).
        # The reply is converted to UTC, so a midnight in another zone is not
        # midnight. +12 h picks the intended day for zones from UTC-12 to UTC+12.
        half = timedelta(hours=12)
        start_date = (start + half).date()
        end_date = (end + half).date()
        if end_date <= start_date:
            end_date = start_date + timedelta(days=1)
        starts_at = ends_at = None
    elif end < start:
        ends_at = start

    loc = ev.get("location")
    location = loc.get("displayName") if isinstance(loc, Mapping) else None
    preview = ev.get("bodyPreview")
    master = ev.get("seriesMasterId")
    return RemoteEvent(
        external_id=ext_id,
        title=clean_title(ev.get("subject") if isinstance(ev.get("subject"), str) else None),
        status=status,
        busy=busy,
        starts_at=starts_at,
        ends_at=ends_at,
        start_date=start_date,
        end_date=end_date,
        tz="UTC" if starts_at is not None else None,
        location=location.strip() or None if isinstance(location, str) else None,
        notes=preview.strip() or None if isinstance(preview, str) else None,
        conference_url=_conference_url(ev),
        external_series_id=master if isinstance(master, str) and master else None,
        original_start=None,
        original_start_date=None,
        etag=ev.get("@odata.etag") if isinstance(ev.get("@odata.etag"), str) else None,
        url=ev.get("webLink") if isinstance(ev.get("webLink"), str) else None,
    )


def parse_delta_page(page: Mapping[str, Any]) -> DeltaPage:
    """Parse one page of ``calendarView/delta``."""
    if not isinstance(page, Mapping):
        raise GraphParseError("page is not an object")
    value = page.get("value")
    if not isinstance(value, list):
        raise GraphParseError("page has no value list")
    events: list[RemoteEvent] = []
    removed: list[str] = []
    skipped = 0
    for item in value:
        if not isinstance(item, Mapping):
            skipped += 1
            continue
        if "@removed" in item:
            rid = item.get("id")
            if isinstance(rid, str) and rid:
                removed.append(rid)
            else:
                skipped += 1
            continue
        parsed = parse_event(item)
        if parsed is None:
            skipped += 1
        else:
            events.append(parsed)
    nxt = page.get("@odata.nextLink")
    delta = page.get("@odata.deltaLink")
    return DeltaPage(
        events=tuple(events),
        removed_ids=tuple(removed),
        next_link=nxt if isinstance(nxt, str) and nxt else None,
        delta_link=delta if isinstance(delta, str) and delta else None,
        skipped=skipped,
    )


def merge_pages(pages: list[DeltaPage]) -> DeltaResult:
    """Fold pages into one result. A later page wins for one event id.

    An id that a later page removes drops out of ``events``, and an id that
    a later page re-adds drops out of ``removed_ids``.
    """
    by_id: dict[str, RemoteEvent] = {}
    removed: dict[str, None] = {}
    out = DeltaResult()
    for pg in pages:
        for ev in pg.events:
            by_id[ev.external_id] = ev
            removed.pop(ev.external_id, None)
        for rid in pg.removed_ids:
            by_id.pop(rid, None)
            removed[rid] = None
        out.skipped += pg.skipped
        if pg.delta_link:
            out.delta_link = pg.delta_link
    out.events = list(by_id.values())
    out.removed_ids = list(removed)
    return out


def next_delta_link(page: DeltaPage) -> Optional[str]:
    """The link to fetch next, or None when the sync is complete."""
    return page.next_link


# ---------------------------------------------------------------- skip rule


def skip_series_instance(
    ev: RemoteEvent, metorite_series_external_ids: set[str]
) -> bool:
    """§8.1 item 5: skip an instance of a series that Metorite wrote out.

    ``metorite_series_external_ids`` holds the ``external_id`` of each
    ``metorite`` row on the SAME connection. The caller loads it under the
    tenant seam.
    """
    return (
        ev.external_series_id is not None
        and ev.external_series_id in metorite_series_external_ids
    )


# ------------------------------------------------------------ request builds


def window(now: datetime) -> tuple[datetime, datetime]:
    now = now.astimezone(timezone.utc)
    return (
        now - timedelta(days=WINDOW_BACK_DAYS),
        now + timedelta(days=WINDOW_AHEAD_DAYS),
    )


def _iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def calendars_url() -> str:
    return f"{GRAPH_BASE}/me/calendars"


def delta_url(calendar_id: str, start: datetime, end: datetime) -> str:
    """First request of a calendar. A later sync uses the stored deltaLink."""
    q = urlencode(
        {"startDateTime": _iso_z(start), "endDateTime": _iso_z(end)}
    )
    return (
        f"{GRAPH_BASE}/me/calendars/{quote(calendar_id, safe='')}"
        f"/calendarView/delta?{q}"
    )


def read_headers(access_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
                # identity: the guard reads the raw bytes and never inflates, so a
        # gzip reply would parse as {} (and an empty list would prune rows).
        "Accept-Encoding": "identity",
        # 10 events a page keeps a reply under the 64 KiB cap of the guard.
        "Prefer": 'outlook.timezone="UTC", odata.maxpagesize=10',
    }


def event_payload(
    *,
    title: str,
    starts_at: Optional[datetime] = None,
    ends_at: Optional[datetime] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    notes: Optional[str] = None,
    location: Optional[str] = None,
    busy: bool = True,
) -> dict[str, Any]:
    """Body of ``POST /events`` and ``PATCH /events/{id}`` (CE-13)."""
    body: dict[str, Any] = {
        "subject": title[:_TITLE_MAX],
        "showAs": "busy" if busy else "free",
    }
    if start_date is not None and end_date is not None:
        body["isAllDay"] = True
        body["start"] = {"dateTime": f"{start_date.isoformat()}T00:00:00", "timeZone": "UTC"}
        body["end"] = {"dateTime": f"{end_date.isoformat()}T00:00:00", "timeZone": "UTC"}
    elif starts_at is not None and ends_at is not None:
        body["isAllDay"] = False
        body["start"] = {"dateTime": _iso_z(starts_at).rstrip("Z"), "timeZone": "UTC"}
        body["end"] = {"dateTime": _iso_z(ends_at).rstrip("Z"), "timeZone": "UTC"}
    else:
        raise ValueError("an event needs a time range or a date range")
    if notes:
        body["body"] = {"contentType": "text", "content": notes}
    if location:
        body["location"] = {"displayName": location}
    return body


def out_event_payload(ev: OutEvent) -> dict[str, Any]:
    """``OutEvent`` of CE-13 to a Graph body. A task link goes in the notes."""
    notes = ev.notes or ""
    if ev.task_url:
        notes = f"{notes}\n\n{ev.task_url}".strip()
    return event_payload(
        title=ev.title, starts_at=ev.starts_at, ends_at=ev.ends_at,
        start_date=ev.start_date, end_date=ev.end_date, notes=notes or None,
        location=ev.location, busy=ev.busy,
    )


def pick_calendar_ids(calendars_reply: Mapping[str, Any]) -> list[str]:
    """Ids of the calendars to read: the member's DEFAULT calendar only (the
    same rule as the Google provider, audit G4). A calendar that a colleague
    shares is not the member's own time. The "Metorite" calendar is never read."""
    out: list[str] = []
    for cal in calendars_reply.get("value") or []:
        if not isinstance(cal, Mapping):
            continue
        cid = cal.get("id")
        if (isinstance(cid, str) and cid and cal.get("isDefaultCalendar") is True
                and cal.get("name") != METORITE_CALENDAR_NAME):
            out.append(cid)
    return out


def find_metorite_calendar_id(calendars_reply: Mapping[str, Any]) -> Optional[str]:
    for cal in calendars_reply.get("value") or []:
        if isinstance(cal, Mapping) and cal.get("name") == METORITE_CALENDAR_NAME:
            cid = cal.get("id")
            if isinstance(cid, str) and cid:
                return cid
    return None


def short_error(status: int, body: Any) -> str:
    """A 500-character reason for ``provider_sync_error``. No token, no body
    echo beyond the Graph error code and message."""
    code = msg = ""
    if isinstance(body, Mapping):
        err = body.get("error")
        if isinstance(err, Mapping):
            code = str(err.get("code") or "")
            msg = str(err.get("message") or "")
    return f"graph {status} {code} {msg}".strip()[:_ERROR_MAX]


#: Graph says 410 Gone with ``syncStateNotFound`` when a deltaLink is too old.
def delta_expired(status: int, body: Any) -> bool:
    if status == 410:
        return True
    return (
        status == 400
        and isinstance(body, Mapping)
        and isinstance(body.get("error"), Mapping)
        and "syncState" in str(body["error"].get("code", ""))
    )
