"""The Zoho source adapter against a fake Zoho (WS-53 CRM-Z1, ``crm_platform.md`` §5).

Hermetic: every request goes to an ``httpx.MockTransport``. No test reaches
Zoho, and no test reads a setting, the environment or a file.

The acceptance of the slice, in order:

3. Rate limits back off on HTTP 429 and on the body code ``TOO_MANY_REQUESTS``.
4. Paging reads every page in order, follows ``next_page_token``, and ends on
   204 or 304.
5. The data centre comes from the token response, and a host off the allowlist
   raises before a request leaves.
6. A dead refresh token raises ``NeedsReconnect`` with no retry.
7. Each API call costs one credit, and the budget refuses the call past it.
8. No log line and no exception text holds a planted token or secret. The
   ``leaks`` fixture checks that in every test of 3 to 6.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, fields
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import structlog
from gateway.crm_sources import (
    BudgetExhausted,
    CrmSource,
    NeedsReconnect,
    OAuthClientConfig,
    RateLimited,
    SourceCredential,
    SourceCursor,
    SourceError,
    UntrustedHost,
)
from gateway.crm_sources.registry import SOURCES, source_class
from gateway.crm_sources.zoho import ZohoSource, auth
from gateway.crm_sources.zoho.client import ZohoScopeError

SECRET = "planted-client-secret-7f3a91"
ACCESS = "planted-access-token-91c2e0"
NEW_ACCESS = "planted-new-access-token-5b8d"
REFRESH = "planted-refresh-token-44d7a2"
CODE = "planted-consent-code-1203bf"
PLANTED = (SECRET, ACCESS, NEW_ACCESS, REFRESH, CODE)

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
CONFIG = OAuthClientConfig(
    client_id="1000.CLIENTID",
    client_secret=SECRET,
    redirect_uri="https://app.metorite.com/api/crm/oauth/zoho/callback",
)


def credential(
    *,
    api_domain: str = "https://www.zohoapis.com",
    accounts_server: str = "https://accounts.zoho.com",
    expires_at: datetime = NOW + timedelta(hours=1),
) -> SourceCredential:
    return SourceCredential(
        access_token=ACCESS,
        refresh_token=REFRESH,
        expires_at=expires_at,
        scopes=("ZohoCRM.modules.READ",),
        provider_meta={"accounts_server": accounts_server, "api_domain": api_domain},
    )


def callback(server: str = "https://accounts.zoho.eu", **extra: str) -> dict[str, str]:
    """The query of Zoho's consent redirect, as CRM-Z2 hands it over."""
    return {"code": CODE, "accounts-server": server, "state": "signed", **extra}


Handler = Callable[[httpx.Request], httpx.Response]


@dataclass
class Fake:
    """A fake Zoho. ``script`` answers the requests in order."""

    script: list[httpx.Response | Handler | Exception] = field(default_factory=list)
    requests: list[httpx.Request] = field(default_factory=list)
    sleeps: list[float] = field(default_factory=list)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self.script:
            raise AssertionError(f"unexpected request {request.method} {request.url}")
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step(request) if callable(step) else step

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)

    def source(
        self,
        cred: SourceCredential | None = None,
        **kw: Any,
    ) -> ZohoSource:
        return ZohoSource(
            CONFIG,
            credential() if cred is None else cred,
            transport=httpx.MockTransport(self.handle),
            sleep=self.sleep,
            clock=lambda: NOW,
            **kw,
        )


def ok(body: dict[str, Any] | None = None, status: int = 200, **headers: str) -> httpx.Response:
    return httpx.Response(status, json=body if body is not None else {}, headers=headers)


def rows(*ids: str, more: bool = False, token: str | None = None) -> httpx.Response:
    info: dict[str, Any] = {"more_records": more, "per_page": 200}
    if token:
        info["next_page_token"] = token
    return ok(
        {
            "data": [{"id": i, "Modified_Time": "2026-10-01T10:00:00+05:30"} for i in ids],
            "info": info,
        }
    )


def too_many(status: int = 429, **headers: str) -> httpx.Response:
    return ok({"code": "TOO_MANY_REQUESTS", "status": "error"}, status=status, **headers)


@pytest.fixture
def leaks(caplog: pytest.LogCaptureFixture) -> Iterator[list[str]]:
    """Collect every log line and exception text. Fail if one holds a secret.

    A test appends exception texts to the list it gets. The teardown adds every
    stdlib record (httpx logs each request URL) and every structlog event.
    """
    caplog.set_level(logging.DEBUG)
    # Something in the test process sets the httpx logger to WARNING, and
    # httpx logs each request URL at INFO. Lower it here, or a secret in a URL
    # passes unseen.
    caplog.set_level(logging.DEBUG, logger="httpx")
    texts: list[str] = []
    with structlog.testing.capture_logs() as events:
        yield texts
    # In teardown, ``caplog.records`` holds only the teardown records.
    texts.extend(r.getMessage() for r in caplog.get_records("call"))
    texts.extend(json.dumps(e, default=str) for e in events)
    assert texts, "the capture saw nothing, so the check would be vacuous"
    for text in texts:
        for secret in PLANTED:
            assert secret not in text, f"a secret leaked into: {text[:200]}"


def exc_texts(exc: BaseException) -> list[str]:
    return [str(exc), repr(exc), repr(exc.args)]


# ── 3. Rate limits ──────────────────────────────────────────────────────────


async def test_two_429s_then_a_200_send_three_requests_with_waits_1_and_2(
    leaks: list[str],
) -> None:
    fake = Fake([too_many(), too_many(), rows("1")])
    page = await fake.source().list_changed("deal")
    assert len(fake.requests) == 3
    assert fake.sleeps == [1.0, 2.0]
    assert [r.ext_id for r in page.records] == ["1"]


async def test_five_429s_raise_rate_limited_after_exactly_five_requests(
    leaks: list[str],
) -> None:
    fake = Fake([too_many() for _ in range(5)])
    with pytest.raises(RateLimited) as caught:
        await fake.source().list_changed("deal")
    assert len(fake.requests) == 5
    assert fake.sleeps == [1.0, 2.0, 4.0, 8.0]
    assert caught.value.tries == 5
    leaks.extend(exc_texts(caught.value))


@pytest.mark.parametrize("status", [200, 400])
async def test_the_body_code_too_many_requests_backs_off_at_any_status(
    leaks: list[str],
    status: int,
) -> None:
    fake = Fake([too_many(status), too_many(status), rows("1")])
    page = await fake.source().list_changed("contact")
    assert len(fake.requests) == 3
    assert fake.sleeps == [1.0, 2.0]
    assert len(page.records) == 1


async def test_five_body_codes_raise_rate_limited(leaks: list[str]) -> None:
    fake = Fake([too_many(200) for _ in range(5)])
    with pytest.raises(RateLimited) as caught:
        await fake.source().list_users()
    assert len(fake.requests) == 5
    leaks.extend(exc_texts(caught.value))


@pytest.mark.parametrize(("header", "wait"), [("7", 7.0), ("300", 60.0)])
async def test_retry_after_sets_the_wait_and_60_is_the_cap(
    leaks: list[str],
    header: str,
    wait: float,
) -> None:
    fake = Fake([too_many(**{"Retry-After": header}), rows("1")])
    await fake.source().list_changed("lead")
    assert fake.sleeps == [wait]


@pytest.mark.parametrize("header", ["nan", "inf", "-inf", "NaN"])
async def test_a_non_finite_retry_after_falls_back_to_the_step(
    leaks: list[str],
    header: str,
) -> None:
    """``asyncio.sleep(nan)`` never returns, so a non-finite value must not reach it."""
    fake = Fake([too_many(**{"Retry-After": header}), rows("1")])
    await fake.source().list_changed("lead")
    assert fake.sleeps == [1.0]


async def test_the_backoff_writes_a_log_line_with_no_token() -> None:
    """A positive control for the ``leaks`` capture: the warning is caught."""
    fake = Fake([too_many(), rows("1")])
    with structlog.testing.capture_logs() as events:
        await fake.source().list_changed("deal")
    warned = [e for e in events if e["event"] == "crm_sources.zoho.backoff"]
    assert len(warned) == 1
    assert warned[0]["reason"] == "rate limit"
    assert not any(s in json.dumps(warned, default=str) for s in PLANTED)


# ── Transport errors and server errors (fix round 1) ───────────────────────


def timeout() -> httpx.TimeoutException:
    return httpx.ReadTimeout("timed out")


@pytest.mark.parametrize(
    "first",
    [
        [ok({}, status=503), ok({}, status=504)],
        [timeout(), timeout()],
        [ok({}, status=502), timeout()],
    ],
    ids=["503-504", "two-timeouts", "502-timeout"],
)
async def test_a_5xx_or_a_timeout_backs_off_like_a_429(
    leaks: list[str],
    first: list[httpx.Response | Exception],
) -> None:
    fake = Fake([*first, rows("1")])
    source = fake.source()
    page = await source.list_changed("deal")
    assert len(fake.requests) == 3
    assert fake.sleeps == [1.0, 2.0]
    assert source.credits_used == 3
    assert [r.ext_id for r in page.records] == ["1"]


@pytest.mark.parametrize("kind", ["502", "timeout"])
async def test_five_5xx_or_timeouts_raise_a_source_error(leaks: list[str], kind: str) -> None:
    def failure() -> httpx.Response | Exception:
        return ok({}, status=502) if kind == "502" else timeout()

    fake = Fake([failure() for _ in range(5)])
    source = fake.source()
    with pytest.raises(SourceError) as caught:
        await source.list_changed("deal")
    assert not isinstance(caught.value, RateLimited)
    assert len(fake.requests) == 5
    assert fake.sleeps == [1.0, 2.0, 4.0, 8.0]
    assert source.credits_used == 5
    leaks.extend(exc_texts(caught.value))


async def test_a_connect_error_is_a_source_error_with_no_retry(leaks: list[str]) -> None:
    fake = Fake([httpx.ConnectError("refused"), rows("1")])
    source = fake.source()
    with pytest.raises(SourceError) as caught:
        await source.list_changed("deal")
    assert len(fake.requests) == 1
    assert fake.sleeps == []
    assert source.credits_used == 1
    leaks.extend(exc_texts(caught.value))


async def test_a_transport_error_on_the_token_call_is_a_source_error(leaks: list[str]) -> None:
    fake = Fake([httpx.ConnectError("refused")])
    with pytest.raises(SourceError) as caught:
        await fake.source().refresh()
    assert not isinstance(caught.value, NeedsReconnect)
    assert len(fake.requests) == 1
    leaks.extend(exc_texts(caught.value))


# ── 4. Paging ───────────────────────────────────────────────────────────────


async def test_three_pages_return_every_row_in_order_then_stop(leaks: list[str]) -> None:
    fake = Fake([rows("a", "b", more=True), rows("c", "d", more=True), rows("e")])
    source = fake.source()
    cursor: SourceCursor | None = SourceCursor()
    got: list[str] = []
    while cursor is not None:
        page = await source.list_changed("deal", cursor)
        got.extend(r.ext_id for r in page.records)
        cursor = page.next_cursor
    assert got == ["a", "b", "c", "d", "e"]
    assert len(fake.requests) == 3
    assert [r.url.params["page"] for r in fake.requests] == ["1", "2", "3"]
    for r in fake.requests:
        assert r.url.path == "/crm/v2/Deals"
        assert r.url.params["per_page"] == "200"
        assert r.url.params["sort_by"] == "Modified_Time"
        assert r.url.params["sort_order"] == "asc"
        assert r.headers["Authorization"] == f"Zoho-oauthtoken {ACCESS}"


async def test_a_next_page_token_is_sent_with_no_page(leaks: list[str]) -> None:
    fake = Fake([rows("a", more=True, token="tok-2"), rows("b")])
    source = fake.source()
    first = await source.list_changed("lead")
    assert first.next_cursor is not None
    assert first.next_cursor.page_token == "tok-2"
    second = await source.list_changed("lead", first.next_cursor)
    assert second.next_cursor is None
    sent = fake.requests[1].url.params
    assert sent["page_token"] == "tok-2"
    assert "page" not in sent
    assert sent["per_page"] == "200"


@pytest.mark.parametrize("status", [204, 304])
async def test_a_204_or_304_ends_the_read_with_no_error(leaks: list[str], status: int) -> None:
    fake = Fake([httpx.Response(status)])
    since = datetime(2026, 1, 1, tzinfo=UTC)
    page = await fake.source().list_changed("deal", SourceCursor(since=since))
    assert page.records == ()
    assert page.next_cursor is None
    assert fake.requests[0].headers["If-Modified-Since"] == "Thu, 01 Jan 2026 00:00:00 +0000"


async def test_more_records_with_an_empty_page_stops(leaks: list[str]) -> None:
    fake = Fake([ok({"data": [], "info": {"more_records": True}})])
    page = await fake.source().list_changed("deal")
    assert page.next_cursor is None


async def test_deleted_records_page_on_the_deleted_path(leaks: list[str]) -> None:
    body = {
        "data": [{"id": "9", "deleted_time": "2026-10-02T08:00:00+00:00", "type": "recycle"}],
        "info": {"more_records": False},
    }
    fake = Fake([ok(body)])
    page = await fake.source().list_deleted("company")
    assert fake.requests[0].url.path == "/crm/v2/Accounts/deleted"
    assert fake.requests[0].url.params["type"] == "all"
    assert page.records[0].ext_id == "9"
    assert page.records[0].modified_at == datetime(2026, 10, 2, 8, 0, tzinfo=UTC)


async def test_a_server_error_raises_a_source_error_with_no_token(leaks: list[str]) -> None:
    fake = Fake([ok({"code": "INTERNAL_ERROR"}, status=500)])
    with pytest.raises(SourceError) as caught:
        await fake.source().list_changed("deal")
    assert "500" in str(caught.value)
    leaks.extend(exc_texts(caught.value))


async def test_an_unknown_entity_sends_nothing() -> None:
    fake = Fake()
    with pytest.raises(ValueError):
        await fake.source().list_changed("invoice")
    assert fake.requests == []


# ── 5. Data centre ──────────────────────────────────────────────────────────


def token_body(**extra: Any) -> dict[str, Any]:
    return {
        "access_token": NEW_ACCESS,
        "refresh_token": REFRESH,
        "api_domain": "https://www.zohoapis.eu",
        "token_type": "Bearer",
        "expires_in": 3600,
        **extra,
    }


async def test_the_api_domain_of_the_token_response_takes_the_next_call(
    leaks: list[str],
) -> None:
    fake = Fake([ok(token_body()), rows("1")])
    source = ZohoSource(
        CONFIG,
        None,
        transport=httpx.MockTransport(fake.handle),
        sleep=fake.sleep,
        clock=lambda: NOW,
    )
    cred = await source.finish_consent(
        params=callback(location="eu"),
        scopes=["ZohoCRM.modules.READ"],
    )
    assert cred.provider_meta["api_domain"] == "https://www.zohoapis.eu"
    assert cred.provider_meta["accounts_server"] == "https://accounts.zoho.eu"
    assert cred.provider_meta["location"] == "eu"
    assert cred.scopes == ("ZohoCRM.modules.READ",)
    await source.list_changed("deal")
    token_req, api_req = fake.requests
    assert token_req.method == "POST"
    assert token_req.url.host == "accounts.zoho.eu"
    assert token_req.url.path == "/oauth/v2/token"
    assert SECRET not in str(token_req.url)
    assert CODE not in str(token_req.url)
    assert api_req.url.host == "www.zohoapis.eu"
    assert api_req.headers["Authorization"] == f"Zoho-oauthtoken {NEW_ACCESS}"
    # A token call costs no credit.
    assert source.credits_used == 1
    leaks.append(repr(cred))


@pytest.mark.parametrize(
    "domain",
    [
        "https://evil.example.com",
        "http://www.zohoapis.com",
        "https://www.zohoapis.com.evil.io",
        "https://www.zohoapis.com@evil.io",
        "https://www.zohoapis.com:8443",
        "https://www.zohoapis.com/crm",
        "",
    ],
)
async def test_an_api_domain_off_the_allowlist_raises_before_a_request(
    leaks: list[str],
    domain: str,
) -> None:
    fake = Fake([rows("1")])
    with pytest.raises(UntrustedHost) as caught:
        await fake.source(credential(api_domain=domain)).list_changed("deal")
    assert fake.requests == []
    leaks.extend(exc_texts(caught.value))


@pytest.mark.parametrize(
    "server",
    ["https://accounts.evil.com", "http://accounts.zoho.eu", "https://accounts.zoho.eu.evil.io"],
)
async def test_an_accounts_server_off_the_allowlist_raises_before_a_request(
    leaks: list[str],
    server: str,
) -> None:
    fake = Fake([ok(token_body())])
    source = fake.source()
    with pytest.raises(UntrustedHost) as caught:
        await source.finish_consent(params=callback(server), scopes=["s"])
    assert fake.requests == []
    leaks.extend(exc_texts(caught.value))


@pytest.mark.parametrize(
    ("server", "location"),
    [
        ("https://accounts.zoho.eu", "xx"),
        ("https://accounts.zoho.in", "eu"),
        ("https://accounts.zoho.com", "evil.example.com"),
    ],
    ids=["unknown-location", "location-of-another-centre", "a-host-as-location"],
)
async def test_a_location_off_the_allowlist_raises_before_a_request(
    leaks: list[str],
    server: str,
    location: str,
) -> None:
    fake = Fake([ok(token_body())])
    with pytest.raises(UntrustedHost) as caught:
        await fake.source().finish_consent(params=callback(server, location=location), scopes=["s"])
    assert fake.requests == []
    leaks.extend(exc_texts(caught.value))


@pytest.mark.parametrize(("location", "stored"), [("EU", "eu"), (None, "eu")])
async def test_the_location_is_normalised_or_derived_from_the_server(
    leaks: list[str],
    location: str | None,
    stored: str,
) -> None:
    fake = Fake([ok(token_body())])
    params = callback() if location is None else callback(location=location)
    cred = await fake.source().finish_consent(params=params, scopes=["s"])
    assert cred.provider_meta["location"] == stored


async def test_a_token_response_off_the_allowlist_is_refused(leaks: list[str]) -> None:
    fake = Fake([ok(token_body(api_domain="https://www.zohoapis.example"))])
    with pytest.raises(UntrustedHost) as caught:
        await fake.source().finish_consent(
            params=callback("https://accounts.zoho.com"),
            scopes=["s"],
        )
    assert len(fake.requests) == 1
    leaks.extend(exc_texts(caught.value))


def test_each_data_centre_is_on_the_allowlist() -> None:
    for dc in auth.ZOHO_DATA_CENTRES.values():
        assert auth.check_accounts_server(f"https://{dc.accounts_host}") == (
            f"https://{dc.accounts_host}"
        )
        assert auth.check_api_domain(f"https://{dc.api_host}/") == f"https://{dc.api_host}"
    # The nine of zoho.com/developer/oauth/multi-dc-support.html (2026-10-11).
    assert set(auth.ZOHO_DATA_CENTRES) == {"us", "eu", "in", "au", "jp", "ca", "cn", "sa", "uk"}
    assert auth.ZOHO_DATA_CENTRES["sa"] == auth.DataCentre("accounts.zoho.sa", "www.zohoapis.sa")
    assert auth.ZOHO_DATA_CENTRES["uk"] == auth.DataCentre("accounts.zoho.uk", "www.zohoapis.uk")


def test_the_consent_url_starts_at_the_us_server_and_holds_no_secret() -> None:
    url = httpx.URL(
        ZohoSource(CONFIG).consent_url(
            scopes=["ZohoCRM.modules.READ", "ZohoCRM.users.READ"], state="signed-state"
        )
    )
    assert url.scheme == "https"
    assert url.host == "accounts.zoho.com"
    assert url.path == "/oauth/v2/auth"
    assert url.params["scope"] == "ZohoCRM.modules.READ,ZohoCRM.users.READ"
    assert url.params["access_type"] == "offline"
    assert url.params["state"] == "signed-state"
    assert url.params["redirect_uri"] == CONFIG.redirect_uri
    assert SECRET not in str(url)


# ── 6. Refresh ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "answer",
    [
        ok({"error": "invalid_grant"}),
        ok({"error": "invalid_code"}),
        ok({"error": "invalid_grant"}, status=400),
        ok({"error": "invalid_code"}, status=401),
    ],
    ids=["invalid_grant", "invalid_code", "invalid_grant-400", "invalid_code-401"],
)
async def test_a_dead_refresh_token_raises_needs_reconnect_with_no_retry(
    leaks: list[str],
    answer: httpx.Response,
) -> None:
    fake = Fake([answer, answer, answer])
    source = fake.source()
    with pytest.raises(NeedsReconnect) as caught:
        await source.refresh()
    assert len(fake.requests) == 1
    assert fake.sleeps == []
    # The secret and the refresh token go in the form body, never in the URL.
    assert SECRET not in str(fake.requests[0].url)
    assert REFRESH not in str(fake.requests[0].url)
    assert REFRESH in fake.requests[0].content.decode()
    # The old credential stays as it was. The caller decides what to store.
    assert source.credential == credential()
    leaks.extend(exc_texts(caught.value))


@pytest.mark.parametrize(
    "answer",
    [
        ok({}, status=400),
        ok({}, status=401),
        ok({"error": "invalid_client"}, status=400),
        ok({"error": "invalid_redirect_uri"}, status=400),
    ],
    ids=["bare-400", "bare-401", "invalid_client", "invalid_redirect_uri"],
)
async def test_another_4xx_on_refresh_is_not_a_reconnect(
    leaks: list[str],
    answer: httpx.Response,
) -> None:
    """Only a dead refresh token asks an admin to connect again (fix round 1)."""
    fake = Fake([answer])
    source = fake.source()
    with pytest.raises(SourceError) as caught:
        await source.refresh()
    assert not isinstance(caught.value, NeedsReconnect)
    assert len(fake.requests) == 1
    leaks.extend(exc_texts(caught.value))


async def test_a_throttle_on_refresh_is_rate_limited(leaks: list[str]) -> None:
    """Zoho's accounts server throttles with ``Access Denied`` and HTTP 400."""
    throttle = ok(
        {
            "error": "Access Denied",
            "error_description": "You have made too many requests continuously. "
            "Please try again after some time.",
            "status": "failure",
        },
        status=400,
    )
    fake = Fake([throttle])
    with pytest.raises(RateLimited) as caught:
        await fake.source().refresh()
    assert len(fake.requests) == 1
    leaks.extend(exc_texts(caught.value))


async def test_a_server_error_on_refresh_is_not_a_reconnect(leaks: list[str]) -> None:
    fake = Fake([ok({}, status=503)])
    with pytest.raises(SourceError) as caught:
        await fake.source().refresh()
    assert not isinstance(caught.value, NeedsReconnect)
    leaks.extend(exc_texts(caught.value))


async def test_an_expired_token_refreshes_before_the_call(leaks: list[str]) -> None:
    expired = credential(expires_at=NOW + timedelta(minutes=2))
    fake = Fake([ok(token_body(api_domain="https://www.zohoapis.com")), rows("1")])
    source = fake.source(expired)
    await source.list_changed("deal")
    token_req, api_req = fake.requests
    assert token_req.url.path == "/oauth/v2/token"
    assert token_req.url.host == "accounts.zoho.com"
    assert api_req.headers["Authorization"] == f"Zoho-oauthtoken {NEW_ACCESS}"
    assert source.credential is not None
    assert source.credential.access_token == NEW_ACCESS
    assert source.credential.refresh_token == REFRESH
    assert source.credential.expires_at == NOW + timedelta(seconds=3600)
    assert source.credits_used == 1


@pytest.mark.parametrize("dead", [False, True], ids=["alive", "dead"])
async def test_concurrent_calls_at_expiry_send_one_token_request(
    leaks: list[str],
    dead: bool,
) -> None:
    """Single flight: N calls that find the token expired share one refresh.

    The token handler yields to the loop, so without the lock every call
    starts its own refresh. A dead token is asked about once, too.
    """
    calls = {"token": 0, "api": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/v2/token":
            calls["token"] += 1
            for _ in range(3):
                await asyncio.sleep(0)
            if dead:
                return ok({"error": "invalid_grant"})
            return ok(token_body(api_domain="https://www.zohoapis.com"))
        calls["api"] += 1
        assert request.headers["Authorization"] == f"Zoho-oauthtoken {NEW_ACCESS}"
        return rows("1")

    source = ZohoSource(
        CONFIG,
        credential(expires_at=NOW - timedelta(minutes=1)),
        transport=httpx.MockTransport(handler),
        clock=lambda: NOW,
    )
    results = await asyncio.gather(
        *(source.list_changed("deal") for _ in range(5)),
        return_exceptions=True,
    )
    assert calls["token"] == 1
    if dead:
        assert all(isinstance(r, NeedsReconnect) for r in results), results
        assert calls["api"] == 0
        leaks.extend(t for r in results if isinstance(r, BaseException) for t in exc_texts(r))
    else:
        assert not [r for r in results if isinstance(r, BaseException)], results
        assert calls["api"] == 5


async def test_an_expired_token_that_cannot_refresh_sends_no_api_call(
    leaks: list[str],
) -> None:
    expired = credential(expires_at=NOW - timedelta(minutes=1))
    fake = Fake([ok({"error": "invalid_grant"}), rows("1")])
    source = fake.source(expired)
    with pytest.raises(NeedsReconnect) as caught:
        await source.list_changed("deal")
    assert len(fake.requests) == 1
    assert source.credits_used == 0
    leaks.extend(exc_texts(caught.value))


# ── 7. Credits ──────────────────────────────────────────────────────────────


async def test_n_calls_spend_n_credits() -> None:
    fake = Fake([rows("1") for _ in range(4)])
    source = fake.source()
    for _ in range(4):
        await source.list_changed("deal")
    assert source.credits_used == 4


async def test_the_call_past_the_budget_raises_and_sends_nothing() -> None:
    fake = Fake([rows("1") for _ in range(4)])
    source = fake.source(credit_budget=3)
    for _ in range(3):
        await source.list_changed("deal")
    with pytest.raises(BudgetExhausted) as caught:
        await source.list_changed("deal")
    assert len(fake.requests) == 3
    assert source.credits_used == 3
    assert (caught.value.used, caught.value.budget) == (3, 3)


async def test_a_retry_spends_a_credit_and_the_budget_stops_it() -> None:
    fake = Fake([too_many(), too_many(), rows("1")])
    source = fake.source(credit_budget=2)
    with pytest.raises(BudgetExhausted):
        await source.list_changed("deal")
    assert len(fake.requests) == 2


async def test_a_zero_budget_sends_nothing_and_does_not_refresh() -> None:
    fake = Fake([ok(token_body()), rows("1")])
    expired = credential(expires_at=NOW - timedelta(minutes=1))
    with pytest.raises(BudgetExhausted):
        await fake.source(expired, credit_budget=0).list_changed("deal")
    assert fake.requests == []


# ── Field definitions, pipelines, users ─────────────────────────────────────


async def test_field_defs_read_the_settings_api() -> None:
    fake = Fake([ok({"fields": [{"api_name": "Deal_Name"}]})])
    got = await fake.source().list_field_defs("deal")
    assert got == [{"api_name": "Deal_Name"}]
    assert fake.requests[0].url.path == "/crm/v8/settings/fields"
    assert fake.requests[0].url.params["module"] == "Deals"


async def test_pipelines_read_each_deal_layout() -> None:
    fake = Fake(
        [
            ok({"layouts": [{"id": "L1"}, {"id": "L2"}]}),
            ok({"pipeline": [{"id": "P1"}]}),
            ok({"pipelines": [{"id": "P2"}]}),
        ]
    )
    got = await fake.source().list_pipelines("deal")
    assert got == [{"id": "P1", "layout_id": "L1"}, {"id": "P2", "layout_id": "L2"}]
    assert fake.requests[1].url.params["layout_id"] == "L1"
    assert await fake.source().list_pipelines("contact") == []


async def test_a_scope_refusal_names_the_scope() -> None:
    fake = Fake([ok({"code": "OAUTH_SCOPE_MISMATCH", "message": "invalid scope"}, status=401)])
    with pytest.raises(ZohoScopeError) as caught:
        await fake.source().list_deal_layouts()
    assert caught.value.scope == "ZohoCRM.settings.layouts.READ"


async def test_users_become_source_records() -> None:
    fake = Fake([ok({"users": [{"id": "u1", "email": "a@x.com"}, {"email": "no-id"}]})])
    users = await fake.source().list_users()
    assert [u.ext_id for u in users] == ["u1"]
    assert users[0].entity == "user"
    assert fake.requests[0].url.params["type"] == "AllUsers"


def users_page(*ids: str, more: bool) -> httpx.Response:
    return ok({"users": [{"id": i} for i in ids], "info": {"more_records": more}})


async def test_users_read_every_page(leaks: list[str]) -> None:
    fake = Fake([users_page("u1", "u2", more=True), users_page("u3", more=False)])
    source = fake.source()
    users = await source.list_users()
    assert [u.ext_id for u in users] == ["u1", "u2", "u3"]
    assert [r.url.params["page"] for r in fake.requests] == ["1", "2"]
    assert all(r.url.params["per_page"] == "200" for r in fake.requests)
    assert source.credits_used == 2


async def test_users_stop_on_an_empty_page() -> None:
    fake = Fake([users_page("u1", more=True), users_page(more=True)])
    users = await fake.source().list_users()
    assert [u.ext_id for u in users] == ["u1"]
    assert len(fake.requests) == 2


async def test_users_paging_stops_at_the_budget() -> None:
    fake = Fake([users_page("u1", more=True), users_page("u2", more=False)])
    with pytest.raises(BudgetExhausted):
        await fake.source(credit_budget=1).list_users()
    assert len(fake.requests) == 1


# ── The seam ────────────────────────────────────────────────────────────────


def test_the_credential_is_provider_neutral() -> None:
    """Zoho's data-centre words live in ``provider_meta``, not in the shape."""
    names = {f.name for f in fields(SourceCredential)}
    assert names == {"access_token", "refresh_token", "expires_at", "scopes", "provider_meta"}
    params = inspect.signature(CrmSource.finish_consent).parameters
    assert set(params) == {"self", "params", "scopes"}


@pytest.mark.parametrize(
    "params",
    [
        {"error": "access_denied", "state": "signed"},
        {"accounts-server": "https://accounts.zoho.eu"},
        {"code": CODE},
    ],
    ids=["denied", "no-code", "no-accounts-server"],
)
async def test_a_callback_with_no_code_sends_nothing(
    leaks: list[str],
    params: dict[str, str],
) -> None:
    fake = Fake([ok(token_body())])
    with pytest.raises(SourceError) as caught:
        await fake.source().finish_consent(params=params, scopes=["s"])
    assert fake.requests == []
    leaks.extend(exc_texts(caught.value))


def test_the_protocol_exposes_the_current_credential() -> None:
    """The engine reads it after a call, to store a token the adapter refreshed."""
    assert isinstance(inspect.getattr_static(CrmSource, "credential"), property)


def test_the_registry_has_one_line_for_zoho() -> None:
    assert dict(SOURCES) == {"zoho": ZohoSource}
    assert source_class("zoho") is ZohoSource
    with pytest.raises(KeyError):
        source_class("salesforce")


async def test_the_deep_link_and_credits_left_wait_for_crm_z0() -> None:
    source = ZohoSource(CONFIG, credential())
    with pytest.raises(NotImplementedError, match="CRM-Z0"):
        source.deep_link("deal", "1")
    with pytest.raises(NotImplementedError, match="CRM-Z0"):
        await source.credits_remaining()


def test_no_repr_holds_a_secret() -> None:
    texts = [repr(CONFIG), repr(credential()), str(credential())]
    for text in texts:
        for secret in PLANTED:
            assert secret not in text
