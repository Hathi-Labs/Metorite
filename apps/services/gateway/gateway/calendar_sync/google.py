"""The Google Calendar provider (WS-CAL CE-10a, CE-13).

Spec: ``project-docs/specs/calendar_events_reminders.md`` §8.1 and §8.2.

Metorite owns ONE Google OAuth client (D-EM-5). Mail, Drive and the calendar
use it. The route reads the client id and secret and passes them in as an
:class:`OAuthClientConfig`. This module reads no settings.

Rules that bind this module:

* **Two calendar scopes.** ``calendar.readonly`` reads the events.
  ``calendar.app.created`` lets Metorite make its own "Metorite" calendar
  (CE-13) and write only inside calendars that it made. Metorite never writes
  to the calendars of the member.
* **Incremental consent (D215 point 1).** The consent URL sends
  ``include_granted_scopes=true``, so the calendar grant adds to the Gmail
  grant of the same account. The grant may then list other scopes. The check
  needs the read scope and a refresh token.
* **One redirect URI (D215 point 2).** The flow reuses the mail redirect URI
  ``<workbench>/api/email/oauth/gmail/callback``. The mail callback hands a
  calendar state over to ``routes/tasks/calendar_connections.finish_consent``.
* **The primary calendar only** (audit G4).
* **A sync token cannot go with a time window** (audit G3). The first read
  lists the window (30 days back, 180 ahead) with ``timeMin`` and ``timeMax``.
  The next reads send the ``syncToken`` alone. A 410 means the token is dead,
  and the engine lists the window again.
* **No revoke.** A revoke ends the WHOLE Google grant, the Gmail grant of the
  same account included. A disconnect here deletes the token and revokes
  nothing; the member may remove the app in the Google account page.
* **Instances.** ``singleEvents=true`` gives each instance of a series its
  own event, with ``recurringEventId`` set.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import quote, urlencode

from gateway.calendar_sync.http import CalendarHttp, Http
from gateway.calendar_sync.provider import (
    AccountIdentity,
    CalendarInfo,
    CursorExpired,
    NeedsReconnect,
    OutEvent,
    OutResult,
    ProviderError,
    RateLimited,
    RemoteEvent,
    SyncResult,
    Window,
)
from gateway.crm_sources.base import OAuthClientConfig, SourceCredential
from gateway.kb_sources.google_drive import auth as google_auth

PROVIDER = "google_calendar"
SOURCE = "google"

SCOPE_READ = "https://www.googleapis.com/auth/calendar.readonly"
SCOPE_APP = "https://www.googleapis.com/auth/calendar.app.created"
SCOPES: tuple[str, ...] = ("openid", "email", SCOPE_READ, SCOPE_APP)

#: The scopes a calendar grant is expected to carry. The check refuses a grant
#: only for a missing read scope or refresh token (D215 point 1 allows more).
ALLOWED_SCOPES: frozenset[str] = frozenset({
    SCOPE_READ, SCOPE_APP, "openid", "email", "profile",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
})

HOSTS: frozenset[str] = frozenset({
    "accounts.google.com", "oauth2.googleapis.com", "www.googleapis.com"})

API = "https://www.googleapis.com/calendar/v3"
PAGE_SIZE = 100
#: The guard reads at most 64 KiB of an answer. A page that was cut is asked
#: again with half the events, down to this size, and then the read fails. A
#: cut page is NEVER used: it would look like an empty feed and the writer
#: would prune every row of the connection.
MIN_PAGE_SIZE = 10
#: Only the fields the mapper reads, so a page is small.
EVENT_FIELDS = (
    "nextPageToken,nextSyncToken,items(id,etag,updated,status,htmlLink,summary,"
    "description,location,start,end,transparency,eventType,recurringEventId,"
    "originalStartTime,hangoutLink,conferenceData/entryPoints(entryPointType,uri))")
#: A safety stop: 100 pages of up to 100 events in one read.
MAX_PAGES = 100

#: Event types the calendar does not show as a time of the member.
_SKIP_TYPES = frozenset({"workingLocation"})


class _FormShim:
    """``google_auth`` posts with ``http.post(url, form=)``. The door names it
    ``post_form``, so this shim lends the one call."""

    def __init__(self, http: Http) -> None:
        self._http = http

    async def post(self, url: str, *, form: Mapping[str, str], headers: Any = None) -> Any:
        return await self._http.post_form(url, form=form, headers=headers)


def grant_refusal(cred: SourceCredential) -> str | None:
    """The bounce reason when a grant cannot serve the calendar, else ``None``."""
    granted = set(cred.scopes)
    if SCOPE_READ not in granted:
        return "scope_missing"
    if not cred.refresh_token:
        return "no_refresh_token"
    return None


# ── Mapping ─────────────────────────────────────────────────────────────────


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _text(value: Any, limit: int) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()[:limit]


def _conference(item: Mapping[str, Any]) -> str | None:
    link = _text(item.get("hangoutLink"), 2000)
    if link:
        return link
    data = item.get("conferenceData")
    if isinstance(data, Mapping):
        for entry in data.get("entryPoints") or ():
            if isinstance(entry, Mapping) and entry.get("entryPointType") == "video":
                return _text(entry.get("uri"), 2000)
    return None


def _updated(value: Any) -> datetime | None:
    try:
        return _utc(str(value)) if value else None
    except (ValueError, TypeError):
        return None


def map_event(item: Mapping[str, Any]) -> RemoteEvent | None:
    """One Google event as a :class:`RemoteEvent`, or ``None`` to skip it.

    A cancelled event with no times (the short form of a change feed) comes
    back ``deleted``. A cancelled event that still has times keeps
    ``status='cancelled'``.
    """
    ext_id = _text(item.get("id"), 1024)
    if not ext_id or item.get("eventType") in _SKIP_TYPES:
        return None
    start, end = item.get("start"), item.get("end")
    cancelled = item.get("status") == "cancelled"
    if not isinstance(start, Mapping) or not isinstance(end, Mapping):
        return RemoteEvent(external_id=ext_id, deleted=True) if cancelled else None
    common: dict[str, Any] = dict(
        external_id=ext_id,
        etag=_text(item.get("etag"), 200),
        url=_text(item.get("htmlLink"), 2000),
        title=str(item.get("summary") or ""),
        notes=_text(item.get("description"), 20000),
        location=_text(item.get("location"), 1000),
        busy=item.get("transparency") != "transparent",
        status=("cancelled" if cancelled
                else "tentative" if item.get("status") == "tentative" else "confirmed"),
        conference_url=_conference(item),
        external_series_id=_text(item.get("recurringEventId"), 1024),
        updated=_updated(item.get("updated")),
    )
    original = item.get("originalStartTime")
    try:
        if "date" in start:
            first = date.fromisoformat(str(start["date"]))
            last = date.fromisoformat(str(end.get("date") or start["date"]))
            if last <= first:
                last = first + timedelta(days=1)
            if isinstance(original, Mapping) and "date" in original:
                common["original_start_date"] = date.fromisoformat(str(original["date"]))
            return RemoteEvent(start_date=first, end_date=last, **common)
        begins = _utc(str(start["dateTime"]))
        ends = _utc(str(end["dateTime"]))
        if ends < begins:
            ends = begins
        if isinstance(original, Mapping) and "dateTime" in original:
            common["original_start"] = _utc(str(original["dateTime"]))
        return RemoteEvent(starts_at=begins, ends_at=ends,
                           tz=_text(start.get("timeZone"), 100), **common)
    except (KeyError, ValueError, TypeError):
        return None  # a malformed time never fails the batch


def _out_body(event: OutEvent) -> dict[str, Any]:
    body: dict[str, Any] = {
        "summary": event.title,
        "transparency": "opaque" if event.busy else "transparent",
        # Google keeps the id of a deleted event as a CANCELLED event. A create
        # that reuses our client id adopts it by PATCH, and only this revives it.
        "status": "confirmed",
    }
    if event.notes:
        body["description"] = event.notes
    if event.location:
        body["location"] = event.location
    if event.task_url:
        body["source"] = {"title": "Metorite", "url": event.task_url}
    if event.start_date and event.end_date:
        body["start"] = {"date": event.start_date.isoformat()}
        body["end"] = {"date": event.end_date.isoformat()}
    elif event.starts_at and event.ends_at:
        zone = {"timeZone": event.tz} if event.tz else {}
        body["start"] = {"dateTime": event.starts_at.astimezone(UTC).isoformat(), **zone}
        body["end"] = {"dateTime": event.ends_at.astimezone(UTC).isoformat(), **zone}
    else:
        raise ValueError("an out event has a time")
    return body


# ── The provider ────────────────────────────────────────────────────────────


class GoogleCalendar:
    provider = PROVIDER
    source = SOURCE

    def __init__(self, client: OAuthClientConfig, http: Http | None = None) -> None:
        self._client = client
        self._http: Http = http or CalendarHttp(HOSTS)

    # OAuth

    def consent_url(self, *, state: str, login_hint: str | None = None) -> str:
        params = {
            "client_id": self._client.client_id,
            "redirect_uri": self._client.redirect_uri,
            "response_type": "code",
            "scope": " ".join(SCOPES),
            "access_type": "offline",
            "prompt": "consent",  # a new refresh token on each consent
            "include_granted_scopes": "true",
            "state": state,
        }
        if login_hint:
            params["login_hint"] = login_hint
        return f"{google_auth.CONSENT_URL}?{urlencode(params)}"

    async def exchange_code(self, code: str) -> SourceCredential:
        cred = await google_auth.exchange_code(
            self._client, _FormShim(self._http), code=code)  # type: ignore[arg-type]
        refusal = grant_refusal(cred)
        if refusal is not None:
            raise ProviderError(refusal)
        return cred

    def needs_refresh(self, cred: SourceCredential, now: datetime) -> bool:
        return google_auth.needs_refresh(cred, now)

    async def refresh(self, cred: SourceCredential) -> SourceCredential:
        return await google_auth.refresh(self._client, _FormShim(self._http), cred)  # type: ignore[arg-type]

    async def revoke(self, cred: SourceCredential) -> bool:
        """Never revokes (module doc). Always ``False``."""
        return False

    # Reads

    async def _call(
        self, cred: SourceCredential, method: str, path: str, *,
        params: Mapping[str, str] | None = None, body: Any = None,
    ) -> Any:
        headers = {"Authorization": f"Bearer {cred.access_token}"}
        url = f"{API}/{path}"
        if method == "GET":
            answer = await self._http.get(url, params=params, headers=headers)
        else:
            if params:
                url = f"{url}?{urlencode(dict(params))}"
            answer = await self._http.send_json(method, url, body=body, headers=headers)
        status = answer.status_code
        if 200 <= status < 300:
            if answer.truncated:
                raise PageTooLarge("the answer was cut by the size cap")
            body_json = answer.json()
            if answer.body and not isinstance(body_json, Mapping):
                raise ProviderError("bad_answer")
            return body_json
        reason = _reason(answer.json())
        if status == 410:
            raise CursorExpired("The Google sync token is dead")
        if status == 401:
            raise NeedsReconnect("Google refused the access token")
        if status == 429 or (status == 403 and reason in _RATE_REASONS):
            exc = RateLimited("Google limited the calendar call", tries=1)
            exc.code = "rate_limited"
            raise exc
        if status == 404:
            raise ProviderError("not_found")
        if status == 409:
            raise ProviderError("exists")
        if status == 403:
            raise ProviderError("forbidden")
        raise ProviderError("server_error" if status >= 500 else "bad_request")

    async def account_identity(self, cred: SourceCredential) -> AccountIdentity:
        body = await self._call(cred, "GET", "calendars/primary")
        email = str(body.get("id") or "").strip().lower() if isinstance(body, Mapping) else ""
        if not email:
            raise ProviderError("account_unreadable")
        return AccountIdentity(email=email[:320], account_id=email[:200])

    async def list_calendars(self, cred: SourceCredential) -> list[CalendarInfo]:
        out: list[CalendarInfo] = []
        token = ""
        for _ in range(MAX_PAGES):
            params = {"maxResults": "250", **({"pageToken": token} if token else {})}
            body = await self._call(cred, "GET", "users/me/calendarList", params=params)
            for item in body.get("items") or ():
                if isinstance(item, Mapping) and item.get("id"):
                    out.append(CalendarInfo(
                        id=str(item["id"]), name=str(item.get("summary") or ""),
                        primary=bool(item.get("primary")),
                        writable=item.get("accessRole") in ("owner", "writer")))
            token = str(body.get("nextPageToken") or "")
            if not token:
                break
        return out

    async def sync(
        self, cred: SourceCredential, cursor: str | None, window: Window,
        *, calendar_id: str | None = None,
    ) -> SyncResult:
        cal = quote(calendar_id, safe="") if calendar_id else "primary"
        base: dict[str, str] = {
            "singleEvents": "true", "showDeleted": "true", "fields": EVENT_FIELDS}
        if cursor:
            base["syncToken"] = cursor
        else:
            base["timeMin"] = window.start.astimezone(UTC).isoformat().replace("+00:00", "Z")
            base["timeMax"] = window.end.astimezone(UTC).isoformat().replace("+00:00", "Z")
        changes: list[RemoteEvent] = []
        page = ""
        size = PAGE_SIZE
        next_sync: str | None = None
        for _ in range(MAX_PAGES):
            params = {**base, "maxResults": str(size)}
            if page:
                params["pageToken"] = page
            try:
                body = await self._call(cred, "GET", f"calendars/{cal}/events", params=params)
            except PageTooLarge:
                if size <= MIN_PAGE_SIZE:
                    raise
                size = max(MIN_PAGE_SIZE, size // 2)
                continue  # the same page again, with fewer events
            for item in body.get("items") or ():
                if isinstance(item, Mapping):
                    mapped = map_event(item)
                    if mapped is not None:
                        changes.append(mapped)
            page = str(body.get("nextPageToken") or "")
            if not page:
                next_sync = _text(body.get("nextSyncToken"), 2000)
                break
        else:
            raise ProviderError("too_many_pages")
        if next_sync is None:
            # The read did not finish: never hand a half read to the writer,
            # whose full-list prune would delete the rows it did not see.
            raise ProviderError("no_sync_token")
        return SyncResult(changes=changes, next_cursor=next_sync, full=not cursor)

    # Writes (CE-13)

    async def create_calendar(self, cred: SourceCredential, name: str) -> str:
        body = await self._call(cred, "POST", "calendars", body={"summary": name})
        cal_id = str(body.get("id") or "") if isinstance(body, Mapping) else ""
        if not cal_id:
            raise ProviderError("no_calendar_id")
        return cal_id

    async def upsert_event(
        self, cred: SourceCredential, calendar_id: str, event: OutEvent,
        external_id: str | None,
    ) -> OutResult:
        cal = quote(calendar_id, safe="")
        if external_id:
            path, method = f"calendars/{cal}/events/{quote(external_id, safe='')}", "PATCH"
        else:
            path, method = f"calendars/{cal}/events", "POST"
        out = _out_body(event)
        if not external_id and event.client_id:
            out["id"] = event.client_id  # a 409 on a retry means the create landed
        body = await self._call(
            cred, method, path, params={"sendUpdates": "none"}, body=out)
        new_id = str(body.get("id") or "") if isinstance(body, Mapping) else ""
        if not new_id:
            raise ProviderError("no_event_id")
        return OutResult(external_id=new_id, etag=_text(body.get("etag"), 200),
                         url=_text(body.get("htmlLink"), 2000))

    async def delete_event(
        self, cred: SourceCredential, calendar_id: str, external_id: str,
    ) -> None:
        path = f"calendars/{quote(calendar_id, safe='')}/events/{quote(external_id, safe='')}"
        try:
            await self._call(cred, "DELETE", path, params={"sendUpdates": "none"})
        except (ProviderError, CursorExpired) as exc:
            if getattr(exc, "code", "") in ("not_found", "cursor_expired"):
                return  # 404 and 410 mean the event is gone
            raise


class PageTooLarge(ProviderError):
    """A page passed the size cap of the guard. The caller asks for less."""

    def __init__(self, message: str) -> None:
        super().__init__("page_too_large", message)


_RATE_REASONS = frozenset({"rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded"})


def _reason(body: Any) -> str:
    err = body.get("error") if isinstance(body, Mapping) else None
    if isinstance(err, Mapping):
        for item in err.get("errors") or ():
            if isinstance(item, Mapping) and item.get("reason"):
                return str(item["reason"])
    return ""


def factory() -> GoogleCalendar | None:
    """The provider on the one Google client, or ``None`` when it is unset."""
    from email_ingestion.providers.app_credentials import oauth_app

    from gateway.routes.email.transport.oauth import _build_redirect_uri

    app = oauth_app("gmail")
    if not app.configured:
        return None
    return GoogleCalendar(OAuthClientConfig(
        client_id=app.client_id, client_secret=app.client_secret,
        redirect_uri=_build_redirect_uri("gmail")))
