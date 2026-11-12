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
_NO_TITLE = "(No title)"
_ERROR_MAX = 500


class GraphParseError(ValueError):
    """A Graph reply that does not hold the shape we need."""


# --------------------------------------------------------------------- types


@dataclass(frozen=True)
class ParsedEvent:
    """One live event, as ``calendar_entries`` columns (no org, owner, conn)."""

    external_id: str
    title: str
    status: str  # confirmed | tentative | cancelled
    busy: bool
    starts_at: Optional[datetime]
    ends_at: Optional[datetime]
    start_date: Optional[date]
    end_date: Optional[date]
    tz: Optional[str]
    location: Optional[str]
    notes: Optional[str]
    conference_url: Optional[str]
    external_series_id: Optional[str]
    original_start: Optional[datetime]
    etag: Optional[str]
    external_url: Optional[str]

    def as_columns(self) -> dict[str, Any]:
        return {
            "external_id": self.external_id,
            "title": self.title,
            "status": self.status,
            "busy": self.busy,
            "starts_at": self.starts_at,
            "ends_at": self.ends_at,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "tz": self.tz,
            "location": self.location,
            "notes": self.notes,
            "conference_url": self.conference_url,
            "external_series_id": self.external_series_id,
            "original_start": self.original_start,
            "etag": self.etag,
            "external_url": self.external_url,
        }


@dataclass(frozen=True)
class DeltaPage:
    """One parsed page of a ``calendarView/delta`` reply."""

    events: tuple[ParsedEvent, ...] = ()
    removed_ids: tuple[str, ...] = ()
    next_link: Optional[str] = None
    delta_link: Optional[str] = None
    skipped: int = 0  # items with no usable id or time


@dataclass
class DeltaResult:
    """All pages of one sync of one calendar."""

    events: list[ParsedEvent] = field(default_factory=list)
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


def _clean_title(subject: Any) -> str:
    text = subject.strip() if isinstance(subject, str) else ""
    if not text:
        return _NO_TITLE
    return text[:_TITLE_MAX]


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


def parse_event(ev: Mapping[str, Any]) -> Optional[ParsedEvent]:
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
        start_date = start.date()
        end_date = end.date()
        if end_date <= start_date:
            end_date = start_date + timedelta(days=1)
        starts_at = ends_at = None
    elif end < start:
        ends_at = start

    loc = ev.get("location")
    location = loc.get("displayName") if isinstance(loc, Mapping) else None
    preview = ev.get("bodyPreview")
    master = ev.get("seriesMasterId")
    return ParsedEvent(
        external_id=ext_id,
        title=_clean_title(ev.get("subject")),
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
        etag=ev.get("@odata.etag") if isinstance(ev.get("@odata.etag"), str) else None,
        external_url=ev.get("webLink") if isinstance(ev.get("webLink"), str) else None,
    )


def parse_delta_page(page: Mapping[str, Any]) -> DeltaPage:
    """Parse one page of ``calendarView/delta``."""
    if not isinstance(page, Mapping):
        raise GraphParseError("page is not an object")
    value = page.get("value")
    if not isinstance(value, list):
        raise GraphParseError("page has no value list")
    events: list[ParsedEvent] = []
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
    by_id: dict[str, ParsedEvent] = {}
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
    ev: ParsedEvent, metorite_series_external_ids: set[str]
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
        # naive UTC dateTime in every reply; 100 events a page
        "Prefer": 'outlook.timezone="UTC", odata.maxpagesize=100',
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


def pick_calendar_ids(calendars_reply: Mapping[str, Any]) -> list[str]:
    """Ids of the calendars to read: every calendar except the "Metorite" one
    that CE-13 writes (its rows are ours already)."""
    out: list[str] = []
    for cal in calendars_reply.get("value") or []:
        if not isinstance(cal, Mapping):
            continue
        cid = cal.get("id")
        if isinstance(cid, str) and cid and cal.get("name") != METORITE_CALENDAR_NAME:
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


# -------------------------------------------------------------------- client


class GraphCalendarError(RuntimeError):
    def __init__(self, status: int, reason: str, *, retry_after: float | None = None):
        super().__init__(reason)
        self.status = status
        self.reason = reason
        self.retry_after = retry_after


class GraphCalendar:
    """Thin Graph calendar client. The caller owns the ``httpx.AsyncClient``
    and the access token (the shared core refreshes it)."""

    def __init__(self, http: Any, access_token: str):
        self._http = http
        self._token = access_token

    async def _send(self, method: str, url: str, **kw: Any) -> Any:
        headers = read_headers(self._token)
        resp = await self._http.request(method, url, headers=headers, **kw)
        status = resp.status_code
        if status == 204:
            return None
        try:
            data = resp.json()
        except ValueError:
            data = None
        if status >= 400:
            ra = resp.headers.get("Retry-After") if hasattr(resp, "headers") else None
            try:
                retry = float(ra) if ra is not None else None
            except ValueError:
                retry = None
            raise GraphCalendarError(status, short_error(status, data), retry_after=retry)
        return data

    async def list_calendars(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        url: Optional[str] = calendars_url()
        while url:
            data = await self._send("GET", url)
            out.extend(c for c in (data or {}).get("value", []) if isinstance(c, dict))
            url = (data or {}).get("@odata.nextLink")
        return out

    async def create_calendar(self, name: str = METORITE_CALENDAR_NAME) -> dict[str, Any]:
        return await self._send("POST", calendars_url(), json={"name": name})

    async def fetch_delta(self, first_url: str, *, max_pages: int = 50) -> DeltaResult:
        """Follow ``@odata.nextLink`` until a ``deltaLink`` arrives."""
        pages: list[DeltaPage] = []
        url: Optional[str] = first_url
        for _ in range(max_pages):
            if url is None:
                break
            page = parse_delta_page(await self._send("GET", url))
            pages.append(page)
            url = next_delta_link(page)
        else:
            raise GraphCalendarError(0, "delta paging did not end")
        result = merge_pages(pages)
        if result.delta_link is None:
            raise GraphCalendarError(0, "delta reply had no deltaLink")
        return result

    async def upsert_event(
        self, calendar_id: str, payload: Mapping[str, Any], event_id: Optional[str] = None
    ) -> dict[str, Any]:
        base = f"{GRAPH_BASE}/me/calendars/{quote(calendar_id, safe='')}/events"
        if event_id is None:
            return await self._send("POST", base, json=dict(payload))
        return await self._send(
            "PATCH", f"{GRAPH_BASE}/me/events/{quote(event_id, safe='')}", json=dict(payload)
        )

    async def delete_event(self, event_id: str) -> None:
        try:
            await self._send("DELETE", f"{GRAPH_BASE}/me/events/{quote(event_id, safe='')}")
        except GraphCalendarError as exc:
            if exc.status != 404:  # already gone is a success
                raise
