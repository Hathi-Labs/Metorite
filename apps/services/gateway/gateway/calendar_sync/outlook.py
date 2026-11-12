"""The Outlook (Microsoft Graph) calendar provider, CE-10b and the Outlook half
of CE-13.

Spec: ``project-docs/specs/calendar_events_reminders.md`` §8.1.

It implements :class:`gateway.calendar_sync.provider.CalendarProvider`. It owns
NO loop, NO connection table and NO row writer: the shared core does. All
mapping is in ``outlook_graph`` as pure functions.

Four rules:

* **One door.** Every call goes through the ``Http`` object (the guarded
  ``CalendarHttp`` in production), on :data:`OUTLOOK_HOSTS`.
* **The Microsoft app of mail.** The client id and secret come from the
  caller (``oauth_app("microsoft")``). The authority is ``common``.
* **A cursor is a map.** Graph's delta is per calendar, so the cursor string is
  a JSON map ``{calendar_id: deltaLink}``. A delta link holds a token, so the
  cursor stays out of every log (``http.redact_cursors``).
* **No revoke.** The v2 endpoint has no per-app revoke. ``revoke`` returns
  False. The connection row goes, the mail grant of the same account stays.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote, urlencode

from gateway.calendar_sync import outlook_graph as g
from gateway.calendar_sync.http import Http
from gateway.calendar_sync.provider import (
    AccountIdentity,
    CalendarInfo,
    CursorExpired,
    NeedsReconnect,
    OutEvent,
    OutResult,
    ProviderError,
    RemoteEvent,
    SyncResult,
    Window,
)
from gateway.crm_sources.base import SourceCredential

PROVIDER = "outlook_calendar"
SOURCE = "outlook"

AUTHORITY = "common"
OAUTH_BASE = f"https://login.microsoftonline.com/{AUTHORITY}/oauth2/v2.0"
AUTHORIZE_URL = f"{OAUTH_BASE}/authorize"
TOKEN_URL = f"{OAUTH_BASE}/token"

#: The hosts this provider may call. The consent host is only in the URL that
#: the browser opens, and the token host is called by the server.
OUTLOOK_HOSTS: frozenset[str] = frozenset({
    "login.microsoftonline.com", "graph.microsoft.com",
})

#: The scopes of the consent. ``offline_access`` is REQUIRED, or Microsoft
#: returns no refresh token. ``User.Read`` names the account.
SCOPES: tuple[str, ...] = (
    "offline_access", "openid", "email",
    "https://graph.microsoft.com/User.Read",
    "https://graph.microsoft.com/Calendars.ReadWrite",
)
_NEEDED_SCOPES = frozenset({"calendars.read", "calendars.readwrite"})

META_ACCOUNT_EMAIL = "account_email"
META_ACCOUNT_ID = "account_id"

_EXPIRY_SKEW = timedelta(minutes=5)
_TOKEN_STOP = timedelta(seconds=30)
_MAX_PAGES = 100
#: A full read of a busy calendar: 400 pages of up to 50 events.
_MAX_EVENT_PAGES = 400
_RECONNECT = frozenset({"invalid_grant", "interaction_required"})

Clock = Callable[[], datetime]


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _code(value: object) -> str:
    text = str(value or "").strip()
    if text and len(text) <= 60 and text.replace("_", "").replace(".", "").isalnum():
        return text
    return ""


def _graph_code(body: object) -> str:
    if isinstance(body, Mapping):
        err = body.get("error")
        if isinstance(err, Mapping):
            return _code(err.get("code"))
        return _code(err)
    return ""


def _short_scope(scope: str) -> str:
    return scope.rsplit("/", 1)[-1].lower()


class PageTooLarge(ProviderError):
    """The guard cut the reply at its size cap. The caller asks for fewer events."""

    def __init__(self) -> None:
        super().__init__("response_too_large")


class OutlookProvider:
    provider = PROVIDER
    source = SOURCE
    #: Graph takes the client id as a ``transactionId``, NOT as the event id. The
    #: writer must not adopt or delete an Outlook event by it.
    client_id_is_event_id = False
    #: Graph dedupes a create on the transactionId: a repeat with the same value
    #: answers with the event that the first create made (see task_out, CE-13c).
    dedupes_by_client_id = True

    def __init__(
        self, http: Http, *, client_id: str, client_secret: str, redirect_uri: str,
        clock: Clock = _utcnow,
    ) -> None:
        self._http = http
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._clock = clock

    # ------------------------------------------------------------ OAuth leg

    def consent_url(self, *, state: str, login_hint: str | None = None) -> str:
        params = {
            "client_id": self._client_id,
            "response_type": "code",
            "redirect_uri": self._redirect_uri,
            "response_mode": "query",
            "scope": " ".join(SCOPES),
            "state": state,
            "prompt": "select_account",
        }
        if login_hint:
            params["login_hint"] = login_hint
        return f"{AUTHORIZE_URL}?{urlencode(params)}"

    async def _token_call(self, form: dict[str, str]) -> Mapping[str, Any]:
        form = {
            "client_id": self._client_id, "client_secret": self._client_secret,
            "scope": " ".join(SCOPES), **form,
        }
        answer = await self._http.post_form(
            TOKEN_URL, form=form, headers={"Accept-Encoding": "identity"})
        body = answer.json()
        if answer.status_code != 200 or not isinstance(body, Mapping) \
                or not body.get("access_token"):
            code = _graph_code({"error": body.get("error")}) if isinstance(body, Mapping) else ""
            exc_type = NeedsReconnect if code in _RECONNECT else ProviderError
            if exc_type is NeedsReconnect:
                exc: Exception = NeedsReconnect(f"Microsoft refused the token call ({code})")
                exc.code = code  # type: ignore[attr-defined]
                raise exc
            raise ProviderError(code or f"token_{answer.status_code}")
        return body

    def _expiry(self, body: Mapping[str, Any]) -> datetime:
        try:
            seconds = int(body.get("expires_in") or 3600)
        except (TypeError, ValueError):
            seconds = 3600
        return self._clock() + timedelta(seconds=max(60, seconds))

    @staticmethod
    def _scopes(body: Mapping[str, Any]) -> tuple[str, ...]:
        raw = body.get("scope")
        return tuple(raw.split()) if isinstance(raw, str) else ()

    async def exchange_code(self, code: str) -> SourceCredential:
        body = await self._token_call({
            "grant_type": "authorization_code", "code": code,
            "redirect_uri": self._redirect_uri,
        })
        scopes = self._scopes(body)
        # Entra ID returns every scope the member consented for this client id,
        # so a grant that holds Mail scopes is normal: refusing it would lock
        # out every member who connected Outlook mail. Keep the whole grant.
        if not _NEEDED_SCOPES & {_short_scope(s) for s in scopes}:
            raise ProviderError("scope_missing")
        if not body.get("refresh_token"):
            raise ProviderError("no_refresh_token")
        return SourceCredential(
            access_token=str(body["access_token"]),
            refresh_token=str(body["refresh_token"]),
            expires_at=self._expiry(body), scopes=scopes,
        )

    async def refresh(self, cred: SourceCredential) -> SourceCredential:
        if not cred.refresh_token:
            raise NeedsReconnect("The calendar connection holds no refresh token")
        body = await self._token_call({
            "grant_type": "refresh_token", "refresh_token": cred.refresh_token,
        })
        # Microsoft ROTATES the refresh token. Keep the new one.
        return replace(
            cred, access_token=str(body["access_token"]),
            refresh_token=str(body.get("refresh_token") or cred.refresh_token),
            expires_at=self._expiry(body), scopes=self._scopes(body) or cred.scopes,
        )

    def needs_refresh(self, cred: SourceCredential, now: datetime) -> bool:
        return cred.expires_at - _EXPIRY_SKEW <= now

    async def revoke(self, cred: SourceCredential) -> bool:
        return False  # see the module note

    # ----------------------------------------------------------- Graph calls

    async def _graph(
        self, method: str, url: str, cred: SourceCredential, *, body: Any = None,
        ok_missing: bool = False, page_size: int = g.PAGE_SIZE,
    ) -> Any:
        headers = g.read_headers(cred.access_token, page_size)
        if method == "GET":
            answer = await self._http.get(url, headers=headers)
        else:
            answer = await self._http.send_json(method, url, body=body, headers=headers)
        status = answer.status_code
        if getattr(answer, "truncated", False):
            raise PageTooLarge()  # fail closed, never a cut body
        if status in (200, 201, 202):
            return answer.json()
        if status == 204:
            return None
        if ok_missing and status in (404, 410):
            return None
        parsed = answer.json()
        if status == 410 or g.delta_expired(status, parsed):
            raise CursorExpired("the delta link is dead")
        if status == 401:
            raise NeedsReconnect("Microsoft Graph refused the access token")
        reason = _graph_code(parsed)
        if status in (429, 503, 504):
            raise ProviderError(f"rate_limited_{status}")
        # The writer (CE-13) speaks one vocabulary: a missing calendar or event is
        # ``not_found`` (Graph says ErrorItemNotFound), a taken transactionId is
        # ``exists``. The Graph code stays for every other failure.
        if status == 404 or reason == "ErrorItemNotFound":
            raise ProviderError("not_found")
        if status == 409:
            raise ProviderError("exists")
        raise ProviderError(reason or f"graph_{status}")

    async def account_identity(self, cred: SourceCredential) -> AccountIdentity:
        data = await self._graph("GET", f"{g.GRAPH_BASE}/me?$select=id,mail,userPrincipalName", cred)
        if not isinstance(data, Mapping):
            raise ProviderError("identity_missing")
        email = str(data.get("mail") or data.get("userPrincipalName") or "").strip().lower()
        account_id = str(data.get("id") or "")
        if not email or not account_id:
            raise ProviderError("identity_missing")
        return AccountIdentity(email=email, account_id=account_id)

    async def list_calendars(self, cred: SourceCredential) -> list[CalendarInfo]:
        out: list[CalendarInfo] = []
        for cal in await self._all_calendars(cred):
            out.append(CalendarInfo(
                id=cal["id"], name=str(cal.get("name") or ""),
                primary=bool(cal.get("isDefaultCalendar")),
                writable=bool(cal.get("canEdit", True)),
            ))
        return out

    async def _all_calendars(self, cred: SourceCredential) -> list[dict[str, Any]]:
        cals: list[dict[str, Any]] = []
        url: str | None = g.calendars_url()
        for _ in range(_MAX_PAGES):
            if url is None:
                break
            data = await self._graph("GET", url, cred)
            if not isinstance(data, Mapping):
                break
            cals.extend(c for c in data.get("value", [])
                        if isinstance(c, dict) and isinstance(c.get("id"), str))
            nxt = data.get("@odata.nextLink")
            url = nxt if isinstance(nxt, str) and nxt else None
        return cals

    # ----------------------------------------------------------------- read

    @staticmethod
    def _load_cursor(cursor: str | None) -> dict[str, str]:
        if not cursor:
            return {}
        try:
            data = json.loads(cursor)
        except ValueError:
            return {}
        if not isinstance(data, dict):
            return {}
        return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}

    async def sync(
        self, cred: SourceCredential, cursor: str | None, window: Window,
        *, calendar_id: str | None = None,
    ) -> SyncResult:
        """One read of the default calendar (never "Metorite"). ``cursor`` None
        lists the whole window (``full``). A dead delta link raises
        :class:`CursorExpired`, and the engine lists again. ``calendar_id``
        reads that ONE calendar (CE-13 reads the "Metorite" calendar so)."""
        links = self._load_cursor(cursor)
        full = not links
        if calendar_id:
            cals = [calendar_id]
        else:
            cals = g.pick_calendar_ids({"value": await self._all_calendars(cred)})
        if not cals:
            # An empty list on a full read would prune every row. Fail instead.
            raise ProviderError("no_calendar")
        changes = []
        next_links: dict[str, str] = {}
        for cal_id in cals:
            url = links.get(cal_id) or g.delta_url(cal_id, window.start, window.end)
            result = await self._read_calendar(cred, url)
            next_links[cal_id] = result.delta_link or ""
            changes.extend(self._to_changes(result))
        if any(not v for v in next_links.values()):
            raise ProviderError("delta_incomplete")
        return SyncResult(
            changes=changes, next_cursor=json.dumps(next_links, sort_keys=True), full=full,
        )

    async def _read_calendar(self, cred: SourceCredential, first_url: str) -> g.DeltaResult:
        pages: list[g.DeltaPage] = []
        url: str | None = first_url
        size = g.PAGE_SIZE
        while url is not None:
            if len(pages) >= _MAX_EVENT_PAGES:
                raise ProviderError("delta_paging")
            if self._clock() >= cred.expires_at - _TOKEN_STOP:
                # A long full read must not outlive the token: a 401 half way
                # would mark a healthy connection needs_reconnect. Stop here,
                # write nothing. The next tick refreshes the token first.
                raise ProviderError("token_expiring")
            try:
                data = await self._graph("GET", url, cred, page_size=size)
            except PageTooLarge:
                if size <= g.MIN_PAGE_SIZE:
                    raise
                size = max(g.MIN_PAGE_SIZE, size // 2)
                continue  # the same page again, with fewer events
            try:
                page = g.parse_delta_page(data if isinstance(data, Mapping) else {})
            except g.GraphParseError:
                raise ProviderError("bad_reply") from None
            pages.append(page)
            url = g.next_delta_link(page)
        return g.merge_pages(pages)

    @staticmethod
    def _to_changes(result: g.DeltaResult) -> list[RemoteEvent]:
        changes = list(result.events)
        changes.extend(RemoteEvent(external_id=rid, deleted=True) for rid in result.removed_ids)
        return changes

    # ---------------------------------------------------------------- write

    async def create_calendar(self, cred: SourceCredential, name: str) -> str:
        data = await self._graph("POST", g.calendars_url(), cred, body={"name": name})
        cal_id = data.get("id") if isinstance(data, Mapping) else None
        if not isinstance(cal_id, str) or not cal_id:
            raise ProviderError("calendar_not_created")
        return cal_id

    async def upsert_event(
        self, cred: SourceCredential, calendar_id: str, event: OutEvent,
        external_id: str | None,
    ) -> OutResult:
        payload = g.out_event_payload(event)
        if external_id is None:
            if event.client_id:
                # Graph answers a repeat of the same transactionId with the event it
                # already made, so a create whose answer was lost makes no copy.
                payload["transactionId"] = event.client_id[:255]
            url = f"{g.calendars_url()}/{quote(calendar_id, safe='')}/events"
            data = await self._graph("POST", url, cred, body=payload)
        else:
            url = f"{g.GRAPH_BASE}/me/events/{quote(external_id, safe='')}"
            data = await self._graph("PATCH", url, cred, body=payload)
        if not isinstance(data, Mapping) or not isinstance(data.get("id"), str):
            raise ProviderError("event_not_saved")
        etag = data.get("@odata.etag")
        web = data.get("webLink")
        return OutResult(
            external_id=data["id"], etag=etag if isinstance(etag, str) else None,
            url=web if isinstance(web, str) else None,
        )

    async def delete_event(
        self, cred: SourceCredential, calendar_id: str, external_id: str,
    ) -> None:
        url = f"{g.GRAPH_BASE}/me/events/{quote(external_id, safe='')}"
        await self._graph("DELETE", url, cred, ok_missing=True)


def factory() -> OutlookProvider | None:
    """The provider on the Microsoft app of Outlook mail, or ``None`` when the
    app is not set up (the loop skips it)."""
    from email_ingestion.providers.app_credentials import oauth_app

    from gateway.calendar_sync.http import CalendarHttp
    from gateway.routes.email.transport.oauth import _build_redirect_uri

    app = oauth_app("microsoft")
    if not app.configured:
        return None
    return OutlookProvider(
        CalendarHttp(OUTLOOK_HOSTS), client_id=app.client_id,
        client_secret=app.client_secret,
        redirect_uri=_build_redirect_uri("microsoft"),
    )


__all__ = [
    "META_ACCOUNT_EMAIL", "META_ACCOUNT_ID", "OUTLOOK_HOSTS", "OutlookProvider",
    "PROVIDER", "SCOPES", "SOURCE", "factory",
]
