"""The Zoho CRM read adapter for one connection (WS-53 CRM-Z1).

This is a copy of the read half of the ingestion Zoho client, and not an
import of it. That module reads global credentials, so ``crm_sources`` must not
depend on it. The old copy retires at CRM-Z11. What changed in the copy:

* **The credential and the client config come in through the constructor.**
  The base URL of every call is ``credential.api_domain``, checked against the
  allowlist in ``auth.py`` before each request.
* **One page for each call.** :meth:`ZohoSource.list_changed` and
  :meth:`ZohoSource.list_deleted` return a :class:`SourcePage` and a next
  cursor, so the sync engine can stop at a budget and resume.
* **One door for every API call.** :meth:`ZohoSource._get` counts the credits,
  refuses a call past the budget, and backs off on a rate limit.

The adapter reads and never writes. Its only POST is the OAuth token call in
``auth.py``.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any

import httpx
import structlog

from gateway.crm_sources.base import (
    BudgetExhausted,
    NeedsReconnect,
    OAuthClientConfig,
    RateLimited,
    SourceCredential,
    SourceCursor,
    SourceError,
    SourcePage,
    SourceRecord,
)
from gateway.crm_sources.zoho import auth

_log = structlog.get_logger("gateway.crm_sources.zoho")

Sleep = Callable[[float], Awaitable[None]]

#: The record reads stay on the version the ingestion client used.
RECORDS_API_VERSION = "v2"

#: Zoho's largest page.
PER_PAGE = 200

#: Five tries in all, so four waits: 1, 2, 4 and 8 seconds.
MAX_TRIES = 5
MAX_BACKOFF_SECONDS = 60.0

_API_TIMEOUT = 60.0

#: Canonical entity -> Zoho module API name. The Meetings module is ``Events``
#: in the API. Field mapping is CRM-Z3 and CRM-Z4, not this table.
ZOHO_MODULES: Mapping[str, str] = MappingProxyType(
    {
        "lead": "Leads",
        "deal": "Deals",
        "contact": "Contacts",
        "company": "Accounts",
        "note": "Notes",
        "call": "Calls",
        "meeting": "Events",
    }
)

_RATE_LIMIT_CODE = "TOO_MANY_REQUESTS"


def _with_modified_since(
    headers: dict[str, str],
    since: datetime | None,
) -> dict[str, str]:
    """Add Zoho's ``If-Modified-Since`` header, in the RFC 1123 form it wants.

    One helper because the record modules and the deleted-records endpoint
    both take the same cursor: a second copy of this formatting is a second
    place for the incremental read to quietly stop being incremental.
    """
    if since is None:
        return headers
    if since.tzinfo is None:
        since = since.replace(tzinfo=UTC)
    # e.g. "Tue, 01 Jan 2026 00:00:00 +0000".
    return {
        **headers,
        "If-Modified-Since": since.strftime("%a, %d %b %Y %H:%M:%S %z"),
    }


# ── Pipeline metadata (WS-26f f1) ───────────────────────────────────────────

#: The settings reads are a deliberate divergence from the ``v2`` path every
#: record reader here uses: ``settings/pipeline`` does not exist on v2. The
#: version is named ONCE, so a tenant that refuses it produces one reported
#: outcome (:class:`ZohoApiVersionError`) rather than a silent retry ladder
#: down the version list.
SETTINGS_API_VERSION = "v8"

#: The OAuth scopes these two reads need. The tenant's refresh token was minted
#: without them (``crm_app.md`` §7.1), so "no scope" is the EXPECTED first
#: outcome — and re-minting the token is the owner's act, which is why the
#: caller has to be able to print the exact string an owner must add.
LAYOUTS_SCOPE = "ZohoCRM.settings.layouts.READ"
PIPELINE_SCOPE = "ZohoCRM.settings.pipeline.READ"

#: The scope of the field definitions read. CRM-Z0 confirms the exact name.
FIELDS_SCOPE = "ZohoCRM.settings.fields.READ"

#: Zoho's own error codes. A scope refusal and a version refusal arrive as the
#: same 401/404 shape as any other failure, and telling them apart is the whole
#: of done-when 3 — "the token cannot see this" and "this tenant has no such
#: endpoint" are different sentences to an owner.
_SCOPE_ERROR_CODES = frozenset({"OAUTH_SCOPE_MISMATCH"})
_VERSION_ERROR_CODES = frozenset({"INVALID_URL_PATTERN", "INVALID_MODULE"})


class ZohoScopeError(SourceError):
    """The refresh token was not minted with the scope this read needs."""

    def __init__(self, scope: str, detail: str = "") -> None:
        super().__init__(f"Zoho refused the read: the token lacks {scope}")
        self.scope = scope
        self.detail = detail


class ZohoApiVersionError(SourceError):
    """The tenant does not serve this endpoint on the version we ask for."""

    def __init__(self, version: str, path: str, detail: str = "") -> None:
        super().__init__(f"Zoho refused /crm/{version}/{path}")
        self.version = version
        self.path = path
        self.detail = detail


def _error_body(response: httpx.Response) -> dict[str, Any]:
    """Zoho's JSON error envelope, or an empty one for a non-JSON body."""
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


# ── Small pure helpers ──────────────────────────────────────────────────────


def _is_rate_limited(response: httpx.Response) -> bool:
    """HTTP 429, or the body code ``TOO_MANY_REQUESTS`` at any status."""
    if response.status_code == 429:
        return True
    return _error_body(response).get("code") == _RATE_LIMIT_CODE


def _backoff_seconds(response: httpx.Response, attempt: int) -> float:
    """``Retry-After`` when it is a finite number, else 1, 2, 4, 8. Never past 60.

    A ``nan`` must not reach the sleep, because ``asyncio.sleep(nan)`` never
    returns. So a value that is not finite, or below zero, takes the step.
    """
    step = float(2 ** (attempt - 1))
    header = response.headers.get("Retry-After", "")
    try:
        wait = float(header)
    except ValueError:
        wait = step
    if not math.isfinite(wait) or wait < 0:
        wait = step
    return min(wait, MAX_BACKOFF_SECONDS)


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _module(entity: str) -> str:
    try:
        return ZOHO_MODULES[entity]
    except KeyError:
        raise ValueError(f"Zoho has no module for the entity {entity!r}") from None


def _page_params(cursor: SourceCursor, extra: Mapping[str, Any]) -> dict[str, Any]:
    """``page`` and ``per_page``, or ``page_token`` and ``per_page`` with no page."""
    params: dict[str, Any] = {**extra, "per_page": PER_PAGE}
    if cursor.page_token:
        params["page_token"] = cursor.page_token
    else:
        params["page"] = cursor.page
    return params


def _next_cursor(cursor: SourceCursor, body: Mapping[str, Any], count: int) -> SourceCursor | None:
    info = body.get("info") or {}
    if not isinstance(info, dict) or not info.get("more_records") or count == 0:
        return None
    token = info.get("next_page_token")
    if token:
        return replace(cursor, page=cursor.page + 1, page_token=str(token))
    return replace(cursor, page=cursor.page + 1, page_token=None)


def _records(entity: str, rows: object, time_key: str) -> tuple[SourceRecord, ...]:
    if not isinstance(rows, list):
        return ()
    out: list[SourceRecord] = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        out.append(
            SourceRecord(
                entity=entity,
                ext_id=str(row["id"]),
                modified_at=_parse_time(row.get(time_key)),
                fields=row,
            )
        )
    skipped = len(rows) - len(out)
    if skipped:
        _log.warning("crm_sources.zoho.rows_without_id", entity=entity, count=skipped)
    return tuple(out)


# ── The adapter ─────────────────────────────────────────────────────────────


class ZohoSource:
    """The Zoho CRM adapter. It satisfies :class:`gateway.crm_sources.base.CrmSource`.

    ``transport`` lets a test put an ``httpx.MockTransport`` under every call.
    ``sleep`` is the wait of the backoff. ``credit_budget`` is the most API
    calls this instance may send. ``None`` means no limit.
    """

    provider = "zoho"

    def __init__(
        self,
        client_config: OAuthClientConfig,
        credential: SourceCredential | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Sleep = asyncio.sleep,
        credit_budget: int | None = None,
        clock: auth.Clock = auth.utcnow,
    ) -> None:
        if credit_budget is not None and credit_budget < 0:
            raise ValueError("credit_budget must be zero or more")
        self._config = client_config
        self._credential = credential
        self._transport = transport
        self._sleep = sleep
        self._clock = clock
        self.credit_budget = credit_budget
        self._credits_used = 0
        # Single flight: one refresh at a time on this instance. A call that
        # waited re-checks the expiry, so N calls at expiry send ONE token
        # request. ``_dead`` keeps a NeedsReconnect, so the waiters do not each
        # ask again about a dead token.
        self._refresh_lock = asyncio.Lock()
        self._dead: NeedsReconnect | None = None

    # ── State the caller reads ──────────────────────────────────────────────

    @property
    def credential(self) -> SourceCredential | None:
        """The current credential. A refresh replaces it, and the caller stores it."""
        return self._credential

    @property
    def credits_used(self) -> int:
        """API calls sent by this instance. A token call adds nothing."""
        return self._credits_used

    # ── 1. Consent ──────────────────────────────────────────────────────────

    def consent_url(self, *, scopes: Sequence[str], state: str) -> str:
        return auth.consent_url(self._config, scopes=scopes, state=state)

    async def finish_consent(
        self,
        *,
        params: Mapping[str, str],
        scopes: Sequence[str],
    ) -> SourceCredential:
        """Finish the consent from the query of Zoho's redirect.

        Zoho sends ``code``, ``accounts-server`` and ``location``, or ``error``
        when the admin refused. The caller has checked ``state`` already.
        """
        if params.get("error"):
            refused = auth.safe_code(params.get("error"))
            raise SourceError(f"Zoho consent was refused ({refused})")
        code = params.get("code") or ""
        accounts_server = params.get("accounts-server") or ""
        if not code or not accounts_server:
            raise SourceError("The Zoho redirect has no code or no accounts-server")
        self._credential = await auth.exchange_code(
            self._config,
            code=code,
            accounts_server=accounts_server,
            scopes=scopes,
            location=params.get("location") or None,
            transport=self._transport,
            clock=self._clock,
        )
        self._dead = None
        return self._credential

    # ── 2. Refresh ──────────────────────────────────────────────────────────

    async def refresh(self) -> SourceCredential:
        """Refresh now, inside the single-flight lock."""
        async with self._refresh_lock:
            return await self._refresh_locked()

    async def _refresh_locked(self) -> SourceCredential:
        if self._dead is not None:
            raise NeedsReconnect(str(self._dead))
        try:
            self._credential = await auth.refresh(
                self._config,
                self._connected(),
                transport=self._transport,
                clock=self._clock,
            )
        except NeedsReconnect as exc:
            self._dead = exc
            raise
        return self._credential

    async def _fresh_credential(self) -> SourceCredential:
        """The credential, refreshed first when it expires within five minutes."""
        credential = self._connected()
        if self._dead is None and not auth.needs_refresh(credential, self._clock()):
            return credential
        async with self._refresh_lock:
            credential = self._connected()
            if self._dead is not None or auth.needs_refresh(credential, self._clock()):
                credential = await self._refresh_locked()
        return credential

    def _connected(self) -> SourceCredential:
        if self._credential is None:
            raise SourceError("This Zoho source has no credential. Connect first")
        return self._credential

    # ── The one door for an API call ────────────────────────────────────────

    async def _get(
        self,
        path: str,
        *,
        params: Mapping[str, Any],
        since: datetime | None = None,
    ) -> httpx.Response:
        """One GET to ``api_domain``, with credits, the budget and the backoff."""
        credential = self._connected()
        self._check_budget()
        auth.check_api_domain(auth.meta(credential, auth.META_API_DOMAIN))
        credential = await self._fresh_credential()
        base = auth.check_api_domain(auth.meta(credential, auth.META_API_DOMAIN))
        headers = _with_modified_since(
            {"Authorization": f"Zoho-oauthtoken {credential.access_token}"},
            since,
        )
        for attempt in range(1, MAX_TRIES + 1):
            self._spend_credit()
            async with httpx.AsyncClient(
                timeout=_API_TIMEOUT,
                transport=self._transport,
            ) as http:
                r = await http.get(f"{base}{path}", headers=headers, params=dict(params))
            if not _is_rate_limited(r):
                return r
            if attempt == MAX_TRIES:
                break
            wait = _backoff_seconds(r, attempt)
            _log.warning(
                "crm_sources.zoho.rate_limited",
                path=path,
                status=r.status_code,
                attempt=attempt,
                wait=wait,
            )
            await self._sleep(wait)
        raise RateLimited(
            f"Zoho still refused {path} after {MAX_TRIES} tries (rate limit)",
            tries=MAX_TRIES,
        )

    def _check_budget(self) -> None:
        """Refuse the next call before it leaves when it would pass the budget."""
        budget = self.credit_budget
        if budget is not None and self._credits_used + 1 > budget:
            raise BudgetExhausted(
                f"The Zoho credit budget of {budget} is spent",
                used=self._credits_used,
                budget=budget,
            )

    def _spend_credit(self) -> None:
        self._check_budget()
        self._credits_used += 1

    @staticmethod
    def _raise_for_status(r: httpx.Response, path: str) -> None:
        if r.is_success:
            return
        code = auth.safe_code(_error_body(r).get("code"))
        detail = f" ({code})" if code else ""
        raise SourceError(f"Zoho answered HTTP {r.status_code} on {path}{detail}")

    async def _page(
        self,
        entity: str,
        path: str,
        cursor: SourceCursor | None,
        extra: Mapping[str, Any],
        time_key: str,
    ) -> SourcePage:
        cursor = cursor or SourceCursor()
        r = await self._get(path, params=_page_params(cursor, extra), since=cursor.since)
        # 204 = nothing there; 304 = nothing changed since the cursor.
        if r.status_code in (204, 304):
            return SourcePage(records=(), next_cursor=None)
        self._raise_for_status(r, path)
        body = _error_body(r)
        rows = body.get("data") or []
        records = _records(entity, rows, time_key)
        count = len(rows) if isinstance(rows, list) else 0
        return SourcePage(records=records, next_cursor=_next_cursor(cursor, body, count))

    # ── 4 and 5. Changed and deleted records ────────────────────────────────

    async def list_changed(
        self,
        entity: str,
        cursor: SourceCursor | None = None,
    ) -> SourcePage:
        """One page of the records changed since ``cursor.since``.

        Stable pagination. Zoho's default order is by Modified_Time DESCENDING,
        so a record edited (by anyone, including our own push) between page 1
        and page 2 jumps to the front and shifts every later record back one
        slot — the record that was at the page boundary is never returned.
        Ascending by the same key we cursor on makes the sequence append-only
        for the duration of the pull: a concurrent edit lands at the END, past
        the pages we have already read, and the next cycle picks it up.
        """
        module = _module(entity)
        return await self._page(
            entity,
            f"/crm/{RECORDS_API_VERSION}/{module}",
            cursor,
            {"sort_by": "Modified_Time", "sort_order": "asc"},
            "Modified_Time",
        )

    async def list_deleted(
        self,
        entity: str,
        cursor: SourceCursor | None = None,
        *,
        kind: str = "all",
    ) -> SourcePage:
        """One page of the records Zoho DELETED — ``GET /crm/v2/{module}/deleted``.

        A record removed in Zoho simply stops appearing in the changed list, so
        an incremental pull can never see it: the only honest way to propagate
        a Zoho delete is to ask for the tombstones. ``kind`` is Zoho's ``type``
        filter — ``all`` (default), ``recycle`` (still restorable) or
        ``permanent``. ``cursor.since`` is sent as ``If-Modified-Since``.
        """
        module = _module(entity)
        return await self._page(
            entity,
            f"/crm/{RECORDS_API_VERSION}/{module}/deleted",
            cursor,
            {"type": kind},
            "deleted_time",
        )

    # ── 6. Users ────────────────────────────────────────────────────────────

    async def list_users(self) -> list[SourceRecord]:
        path = f"/crm/{RECORDS_API_VERSION}/users"
        r = await self._get(path, params={"type": "AllUsers"})
        if r.status_code in (204, 304):
            return []
        self._raise_for_status(r, path)
        return list(_records("user", _error_body(r).get("users"), "Modified_Time"))

    # ── 3. Field definitions and pipelines ──────────────────────────────────

    async def _settings_json(
        self,
        path: str,
        params: dict[str, Any],
        *,
        scope: str,
    ) -> dict[str, Any]:
        """One settings GET, with the two refusals it can produce named."""
        r = await self._get(f"/crm/{SETTINGS_API_VERSION}/{path}", params=params)
        if r.status_code in (204, 304):
            return {}
        body = _error_body(r)
        code = str(body.get("code") or "")
        message = str(body.get("message") or "")[:200]
        if code in _SCOPE_ERROR_CODES or (
            r.status_code in (401, 403) and "scope" in message.lower()
        ):
            raise ZohoScopeError(scope, message)
        if code in _VERSION_ERROR_CODES or r.status_code == 404:
            raise ZohoApiVersionError(SETTINGS_API_VERSION, path, message)
        self._raise_for_status(r, path)
        return body

    async def list_field_defs(self, entity: str) -> list[dict[str, Any]]:
        """The field definitions of one module — ``GET settings/fields?module=…``."""
        body = await self._settings_json(
            "settings/fields",
            {"module": _module(entity)},
            scope=FIELDS_SCOPE,
        )
        fields = body.get("fields")
        return list(fields) if isinstance(fields, list) else []

    async def list_deal_layouts(self) -> list[dict[str, Any]]:
        """The Deals module's layouts — ``GET settings/layouts?module=Deals``.

        Two things come out of one read: the ``layout_id`` the pipeline call
        needs, and the Stage field's ``pick_list_values``, which is where a
        per-stage ``probability`` lives when the tenant exposes one at all.
        """
        body = await self._settings_json(
            "settings/layouts",
            {"module": "Deals"},
            scope=LAYOUTS_SCOPE,
        )
        layouts = body.get("layouts")
        return list(layouts) if isinstance(layouts, list) else []

    async def list_deal_pipelines(self, layout_id: str) -> list[dict[str, Any]]:
        """The pipelines configured on one Deals layout, with their stages.

        ⚠️ The response key is read tolerantly (``pipeline`` is what the API
        documents, ``pipelines`` is what some versions return) because getting
        it wrong reads as "this tenant has no pipelines" — indistinguishable
        from the honest empty answer, and the caller writes nothing on either.
        Tolerance here is not a retry ladder: it is one response, parsed twice.
        """
        body = await self._settings_json(
            "settings/pipeline",
            {"layout_id": layout_id},
            scope=PIPELINE_SCOPE,
        )
        for key in ("pipeline", "pipelines"):
            found = body.get(key)
            if isinstance(found, list):
                return list(found)
        return []

    async def list_pipelines(self, entity: str) -> list[dict[str, Any]]:
        """The pipelines of an entity. In Zoho only deals have pipelines.

        It reads the Deals layouts, then the pipelines of each layout. Each
        pipeline row gains ``layout_id`` so the caller knows where it came from.
        """
        if entity != "deal":
            return []
        out: list[dict[str, Any]] = []
        for layout in await self.list_deal_layouts():
            layout_id = str(layout.get("id") or "") if isinstance(layout, dict) else ""
            if not layout_id:
                continue
            for pipeline in await self.list_deal_pipelines(layout_id):
                if isinstance(pipeline, dict):
                    out.append({**pipeline, "layout_id": layout_id})
        return out

    # ── 7 and 8. Wait for CRM-Z0 ────────────────────────────────────────────

    def deep_link(self, entity: str, ext_id: str) -> str:
        raise NotImplementedError(
            "The Zoho deep link waits for CRM-Z0, which confirms its shape for each data centre"
        )

    async def credits_remaining(self) -> int:
        raise NotImplementedError(
            "The credits that Zoho has left wait for CRM-Z0. Read credits_used for "
            "the credits this instance spent"
        )


__all__ = [
    "FIELDS_SCOPE",
    "LAYOUTS_SCOPE",
    "MAX_TRIES",
    "PER_PAGE",
    "PIPELINE_SCOPE",
    "RECORDS_API_VERSION",
    "SETTINGS_API_VERSION",
    "ZOHO_MODULES",
    "ZohoApiVersionError",
    "ZohoScopeError",
    "ZohoSource",
]
