"""WS-17 EM-S1 — the HTML hot window and the HTML route.

Spec: ``project-docs/specs/email_app_master_plan.md`` §14.4.1, §14.4.2 and
§14.6.1. Owner decisions D-EM-49 and D-EM-53.

R7 fences named here, each a test class:

* ``email-html-window`` (:class:`TestTheWindow`). ``is_cold`` and
  ``hot_cutoff`` at the edge of 90 days, with a naive and an aware time. A
  message with no ``received_at`` is never cold.
* ``email-html-flags`` (:class:`TestTheFlags`). Both flags are off by default.
  ``hot_only()`` is false when only one of the two flags is true.
* ``email-html-route`` (:class:`TestTheRoute`). With the flag off the route is
  404. A message of another member is 404, and the route reads no cache key
  for it. A cache hit makes no provider call. The cache key holds the id of
  the row and passes through ``get_tenant_redis``, so two spellings share one
  entry and org B never reads the entry of org A. A plain-text message
  answers ``none``, and a second call makes no provider call. ``prefetch=1``
  answers 503 while ``recently_busy()`` is true, and an open does not. No
  path writes ``email_messages``, and no session is open across the provider
  call.
* ``email-html-provider-429`` (:class:`TestTheProvider429`). A provider 429
  answers 503 with ``Retry-After``, and gives one ``email.html.provider_429``
  line that holds the mailbox id and no mail text.
* ``email-html-remote`` (:class:`TestHtmlRemote`). ``html_remote`` is true
  only with the flag on, ``body_html`` NULL and a cold message.
  Fix round 1: a hydrated cold row on the open answers false
  (:class:`TestTheOpenAnswersTheTruth`), and so does each row of the light
  search (:class:`TestTheLightSearch`). The route reads the body alone, and
  the Outlook read sends no ``$expand`` (:class:`TestTheBodyOnlyFetch`). A
  fetch with no token rotation opens one session, and the cache keeps each
  letter as itself.
* ``email-html-one-owner`` (:class:`TestTheOneOwner`). No module outside
  ``html_tier.py`` holds the number 90 next to ``received_at``, and no module
  outside it reads the two flags. Synthetic sources prove that each scan can
  still go red. ⚠️ Limit (R7): the number scan reads two lines on each side
  of ``received_at``, and each ``timedelta(days=90)`` in an email module. A
  cutoff that a module builds far from its use passes it.
* ``email-html-owner-r8`` (:class:`TestTheOwnerCheckOnARealDatabase`, R8). The
  owner read runs on the real catalog as the non-privileged role
  ``acb_app_h3rls``. Org B gets 404 for a message of org A, and a member gets
  404 for a message of another member of the same org. The read leaves the
  row as it was.

Run::

    bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_email_html_tier.py -v -rs
"""
from __future__ import annotations

import ast
import json
import re
import uuid
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from acb_auth.roles import UserContext, UserRole
from acb_common import db_busy
from acb_common import tenant_redis as tr
from acb_common.db import bind_tenant, release_tenant
from acb_common.settings import Settings, get_settings
from acb_common.tenant_redis import TenantRedis
from email_ingestion import html_tier
from email_ingestion.providers.base import ProviderRateLimited
from fastapi import HTTPException
from gateway.routes.email import core
from gateway.routes.email.transport import messages as m
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope

# Reuse the two-org phase-4 fixture and its DB gate (non-priv role
# acb_app_h3rls). ``promoted`` and ``app_engine`` are used by name for fixture
# injection, so the import is load-bearing even though it reads as unused.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

REPO = Path(__file__).resolve().parents[2]
ORG_A = "11111111-1111-4111-8111-111111111111"
ORG_B = "22222222-2222-4222-8222-222222222222"
MSG_ID = "33333333-3333-4333-8333-333333333333"
OWNER = "owner@em-s1.test"
SECOND = "second@em-s1.test"
NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
#: The writes that no path of the route may make.
_MESSAGE_WRITE = re.compile(
    r"\b(?:UPDATE|INSERT\s+INTO|DELETE\s+FROM)\s+email_messages\b", re.IGNORECASE)


# ── Flags ───────────────────────────────────────────────────────────────────


def _flags(monkeypatch: pytest.MonkeyPatch, *, from_provider: bool,
           hot_only: bool = False) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "email_html_from_provider", from_provider, raising=False)
    monkeypatch.setattr(settings, "email_html_hot_only", hot_only, raising=False)


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch):
    """Each test starts with the flag on, no Redis tenant bound, and a
    database that is not busy."""
    _flags(monkeypatch, from_provider=True)
    token = tr._ORGANIZATION_ID.set(None)
    db_busy.reset()
    try:
        yield
    finally:
        tr._ORGANIZATION_ID.reset(token)
        db_busy.reset()


# ── 1. The window ───────────────────────────────────────────────────────────


class TestTheWindow:

    def test_the_window_is_90_days(self) -> None:
        assert html_tier.HTML_HOT_DAYS == 90

    def test_the_cutoff_is_90_days_back_in_utc(self) -> None:
        cutoff = html_tier.hot_cutoff(NOW)
        assert cutoff == NOW - timedelta(days=90)
        assert cutoff.utcoffset() == timedelta(0)

    def test_a_naive_now_is_read_as_utc(self) -> None:
        naive = NOW.replace(tzinfo=None)
        assert html_tier.hot_cutoff(naive) == NOW - timedelta(days=90)

    def test_an_aware_now_in_another_zone_gives_the_same_cutoff(self) -> None:
        india = NOW.astimezone(timezone(timedelta(hours=5, minutes=30)))
        assert html_tier.hot_cutoff(india) == html_tier.hot_cutoff(NOW)

    def test_the_cutoff_with_no_now_is_in_utc(self) -> None:
        cutoff = html_tier.hot_cutoff()
        assert cutoff.tzinfo is not None and cutoff.utcoffset() == timedelta(0)

    @pytest.mark.parametrize("aware", [True, False], ids=["aware", "naive"])
    def test_the_edge_of_90_days(self, aware: bool) -> None:
        edge = NOW - timedelta(days=90)
        if not aware:
            edge = edge.replace(tzinfo=None)
        assert html_tier.is_cold(edge, NOW) is False, "the cutoff itself is hot"
        assert html_tier.is_cold(edge - timedelta(microseconds=1), NOW) is True
        assert html_tier.is_cold(edge + timedelta(microseconds=1), NOW) is False

    def test_a_received_at_in_another_zone_compares_in_utc(self) -> None:
        # 89 days and 23 hours back in UTC, written in India time.
        hot = (NOW - timedelta(days=89, hours=23)).astimezone(
            timezone(timedelta(hours=5, minutes=30)))
        assert html_tier.is_cold(hot, NOW) is False
        cold = (NOW - timedelta(days=90, hours=1)).astimezone(
            timezone(timedelta(hours=-7)))
        assert html_tier.is_cold(cold, NOW) is True

    def test_no_received_at_is_never_cold(self) -> None:
        assert html_tier.is_cold(None, NOW) is False
        assert html_tier.is_cold(None) is False


# ── 2. The flags ────────────────────────────────────────────────────────────


class TestTheFlags:

    def test_both_flags_are_off_by_default(self) -> None:
        fields = Settings.model_fields
        assert fields["email_html_from_provider"].default is False
        assert fields["email_html_hot_only"].default is False

    @pytest.mark.parametrize("value", [True, False])
    def test_from_provider_reads_its_flag(self, monkeypatch, value: bool) -> None:
        _flags(monkeypatch, from_provider=value)
        assert html_tier.from_provider() is value

    @pytest.mark.parametrize(("from_provider", "hot_only", "expected"), [
        (False, False, False),
        (True, False, False),
        (False, True, False),
        (True, True, True),
    ])
    def test_hot_only_needs_both_flags(
        self, monkeypatch, from_provider: bool, hot_only: bool, expected: bool,
    ) -> None:
        _flags(monkeypatch, from_provider=from_provider, hot_only=hot_only)
        assert html_tier.hot_only() is expected


# ── 3. The route, hermetic ──────────────────────────────────────────────────


class _TextRedis:
    """A redis-py stand-in for the pool that decodes. It records each call."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.calls: list[tuple[str, str]] = []

    async def get(self, name: str) -> str | None:
        self.calls.append(("get", name))
        return self.store.get(name)

    async def setex(self, name: str, seconds: int, value: str) -> bool:
        self.calls.append(("setex", name))
        self.store[name] = value
        return True


class _Harness:
    """Patches the seams of the route for one test.

    The fake session honours the owner predicate as text: it returns the row
    only to an owner, and only when the SQL holds ``ea.user_id = :user_id``.
    A query without that predicate gives the row to anyone, as a real one
    would.
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch, *, html: str | None = "<p>old</p>",
                 stored_html: str | None = None,
                 owners: frozenset[str] = frozenset({OWNER})) -> None:
        self.redis = _TextRedis()
        self.html = html
        self.provider_calls = 0
        self.auth_ok = True
        self.rotate = False
        self.on_provider: BaseException | None = None
        self.sessions_opened = 0
        self.session_open = False
        self.sql: list[str] = []
        self.redis_wrapped = 0
        self.row = SimpleNamespace(
            id=MSG_ID, provider_message_id="prov-msg", account_id="acct-1",
            body_html=stored_html, provider="microsoft", credentials_encrypted="blob",
        )
        harness = self

        class _Db:
            async def execute(self, statement: Any, params: dict[str, Any]) -> Any:
                sql = str(statement)
                harness.sql.append(sql)
                owned = (params.get("user_id") in owners
                         or "ea.user_id = :user_id" not in sql)
                return SimpleNamespace(fetchone=lambda: harness.row if owned else None)

        @asynccontextmanager
        async def _tenant_session():
            harness.sessions_opened += 1
            harness.session_open = True
            try:
                yield _Db()
            finally:
                harness.session_open = False

        class _Provider:
            def __init__(self) -> None:
                self._dirty = False

            async def authenticate(self) -> bool:
                assert not harness.session_open, "a session is open across authenticate"
                self._dirty = harness.rotate
                return harness.auth_ok

            async def get_message(self, _pmid: str) -> Any:
                raise AssertionError("the HTML route must use get_message_body")

            async def get_message_body(self, _pmid: str) -> Any:
                assert not harness.session_open, "a session is open across the fetch"
                harness.provider_calls += 1
                if harness.on_provider is not None:
                    raise harness.on_provider
                return SimpleNamespace(body_html=harness.html, body_text="old text")

            def credentials_dirty(self) -> bool:
                return self._dirty

            def export_credentials(self) -> dict[str, str]:
                return {"access_token": "new"}

        class _Store:
            def encrypt(self, value: str) -> str:
                return "enc:" + value

        def _get_tenant_redis(*, binary: bool = False) -> TenantRedis:
            assert binary is False, "the HTML cache uses the pool that decodes"
            harness.redis_wrapped += 1
            return TenantRedis(harness.redis)

        monkeypatch.setattr(m, "_tenant_session", _tenant_session)
        monkeypatch.setattr(m, "_decrypt_credentials", lambda _blob: ({}, _Store()))
        monkeypatch.setattr(m, "_instantiate_provider", lambda _name, _creds: _Provider())
        monkeypatch.setattr(m, "get_tenant_redis", _get_tenant_redis)

    def message_writes(self) -> list[str]:
        return [s for s in self.sql if _MESSAGE_WRITE.search(s)]


def _user(email: str = OWNER, org: str | None = ORG_A) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE, organization_id=org)


async def _html(mid: str = MSG_ID, *, email: str = OWNER, org: str | None = ORG_A,
                prefetch: bool = False) -> m.MessageHtmlModel:
    return await m.get_message_html(mid, prefetch=prefetch, user=_user(email, org))


def _key(org: str = ORG_A, row_id: str = MSG_ID) -> str:
    return f"cc:{org}:email-html:{row_id}"


class TestTheRoute:

    async def test_the_flag_off_is_404_with_no_session(self, monkeypatch) -> None:
        h = _Harness(monkeypatch)
        _flags(monkeypatch, from_provider=False)
        with pytest.raises(HTTPException) as err:
            await _html()
        assert err.value.status_code == 404
        assert h.sessions_opened == 0 and h.redis.calls == [] and h.provider_calls == 0

    async def test_a_message_of_another_member_is_404_with_no_cache_read(
        self, monkeypatch,
    ) -> None:
        h = _Harness(monkeypatch)
        # The owner fills the cache first, so a read before the owner check
        # would find an entry.
        assert (await _html()).source == "provider"
        h.redis.calls.clear()
        with pytest.raises(HTTPException) as err:
            await _html(email=SECOND)
        assert err.value.status_code == 404
        assert h.redis.calls == [], "the route read a cache key for a message it does not own"
        assert h.provider_calls == 1

    async def test_a_miss_fetches_and_caches_then_a_hit_makes_no_provider_call(
        self, monkeypatch,
    ) -> None:
        h = _Harness(monkeypatch, html="<p>from the provider</p>")
        first = await _html()
        assert (first.source, first.body_html) == ("provider", "<p>from the provider</p>")
        assert first.message_id == MSG_ID
        assert json.loads(h.redis.store[_key()]) == {"body_html": "<p>from the provider</p>"}
        second = await _html()
        assert (second.source, second.body_html) == ("cache", "<p>from the provider</p>")
        assert h.provider_calls == 1, "a cache hit called the provider"

    async def test_the_key_holds_the_row_id_through_the_tenant_wrapper(
        self, monkeypatch,
    ) -> None:
        """Two spellings of one id share one entry (S1-M2), and each key comes
        through ``get_tenant_redis`` with the tenant of the session (R5 (c))."""
        h = _Harness(monkeypatch)
        await _html(MSG_ID.upper())
        again = await _html(MSG_ID.replace("-", ""))
        assert again.source == "cache"
        assert sorted(h.redis.store) == [_key()]
        assert {name for _verb, name in h.redis.calls} == {_key()}
        assert h.redis_wrapped == len(h.redis.calls)

    async def test_org_b_never_reads_the_entry_of_org_a(self, monkeypatch) -> None:
        """The same row id in two organizations: the fake session gives the row
        to both, so only the tenant in the key keeps the entries apart."""
        h = _Harness(monkeypatch, html="<p>org A</p>")
        assert (await _html(org=ORG_A)).source == "provider"
        h.html = "<p>org B</p>"
        got = await _html(org=ORG_B)
        assert (got.source, got.body_html) == ("provider", "<p>org B</p>")
        assert h.provider_calls == 2
        assert sorted(h.redis.store) == sorted([_key(ORG_A), _key(ORG_B)])

    async def test_a_fetch_with_no_rotation_opens_one_session(self, monkeypatch) -> None:
        """Item 5 of fix round 1: Block B opens only for a rotated token."""
        h = _Harness(monkeypatch)
        assert (await _html()).source == "provider"
        assert h.sessions_opened == 1

    async def test_the_cache_keeps_each_letter_as_itself(self, monkeypatch) -> None:
        """Item 3 of fix round 1: no ASCII escape, so Cyrillic and CJK HTML
        takes no more room in the cache than in the answer."""
        html = "<p>" + "Привет, мир. 你好世界。" * 200 + "</p>"
        h = _Harness(monkeypatch, html=html)
        await _html()
        stored = h.redis.store[_key()]
        assert "Привет" in stored and "你好" in stored
        assert len(stored.encode("utf-8")) <= len(html.encode("utf-8")) + 32
        assert (await _html()).body_html == html

    async def test_a_plain_text_message_is_none_and_is_cached(self, monkeypatch) -> None:
        h = _Harness(monkeypatch, html=None)
        first = await _html()
        assert (first.source, first.body_html) == ("none", None)
        second = await _html()
        assert (second.source, second.body_html) == ("none", None)
        assert h.provider_calls == 1, "the second call reached the provider"

    async def test_blank_html_is_none(self, monkeypatch) -> None:
        _Harness(monkeypatch, html="   \n")
        assert (await _html()).source == "none"

    async def test_a_stored_body_answers_stored_with_no_cache_and_no_provider(
        self, monkeypatch,
    ) -> None:
        h = _Harness(monkeypatch, stored_html="<p>stored</p>")
        got = await _html()
        assert (got.source, got.body_html) == ("stored", "<p>stored</p>")
        assert h.redis.calls == [] and h.provider_calls == 0

    async def test_the_html_is_cut_at_the_cap(self, monkeypatch) -> None:
        _Harness(monkeypatch, html="x" * (m.MAX_BODY_HTML_BYTES + 10))
        got = await _html()
        assert got.body_html is not None
        assert len(got.body_html.encode("utf-8")) <= m.MAX_BODY_HTML_BYTES

    async def test_a_prefetch_is_503_while_the_database_is_busy(self, monkeypatch) -> None:
        h = _Harness(monkeypatch)
        db_busy.mark()
        assert db_busy.recently_busy()
        with pytest.raises(HTTPException) as err:
            await _html(prefetch=True)
        assert err.value.status_code == 503
        assert err.value.headers == {"Retry-After": "30"}
        assert h.sessions_opened == 0 and h.provider_calls == 0

    async def test_an_open_is_never_refused_while_busy(self, monkeypatch) -> None:
        _Harness(monkeypatch)
        db_busy.mark()
        assert (await _html(prefetch=False)).source == "provider"

    async def test_a_prefetch_runs_when_the_database_is_not_busy(self, monkeypatch) -> None:
        _Harness(monkeypatch)
        assert (await _html(prefetch=True)).source == "provider"

    async def test_an_id_that_is_not_a_uuid_is_404_before_a_session(
        self, monkeypatch,
    ) -> None:
        h = _Harness(monkeypatch)
        for bad in ("not-a-uuid", "{" + MSG_ID + "}", "urn:uuid:" + MSG_ID):
            with pytest.raises(HTTPException) as err:
                await _html(bad)
            assert err.value.status_code == 404, bad
        assert h.sessions_opened == 0

    async def test_no_organization_means_no_cache(self, monkeypatch) -> None:
        h = _Harness(monkeypatch)
        await _html(org=None)
        await _html(org=None)
        assert h.redis.calls == [] and h.provider_calls == 2

    async def test_a_failed_auth_is_401_and_caches_nothing(self, monkeypatch) -> None:
        h = _Harness(monkeypatch)
        h.auth_ok = False
        with pytest.raises(HTTPException) as err:
            await _html()
        assert err.value.status_code == 401
        assert h.redis.store == {}

    async def test_a_provider_fault_is_502_and_caches_nothing(self, monkeypatch) -> None:
        h = _Harness(monkeypatch)
        h.on_provider = RuntimeError("graph is down")
        with pytest.raises(HTTPException) as err:
            await _html()
        assert err.value.status_code == 502
        assert h.redis.store == {}

    async def test_a_rotated_token_is_kept_in_a_second_block(self, monkeypatch) -> None:
        h = _Harness(monkeypatch)
        h.rotate = True
        await _html()
        assert h.sessions_opened == 2
        assert any("UPDATE email_accounts" in s for s in h.sql)

    async def test_a_rotated_token_is_kept_after_a_failed_fetch(self, monkeypatch) -> None:
        h = _Harness(monkeypatch)
        h.rotate = True
        h.on_provider = RuntimeError("graph is down")
        with pytest.raises(HTTPException):
            await _html()
        assert any("UPDATE email_accounts" in s for s in h.sql)

    async def test_no_path_writes_email_messages(self, monkeypatch) -> None:
        """Each path of the route: stored, provider, cache, none, a rotated
        token, a failed fetch, another member and a prefetch refusal."""
        runs: list[_Harness] = []

        h = _Harness(monkeypatch, stored_html="<p>s</p>")
        runs.append(h)
        await _html()

        h = _Harness(monkeypatch)
        runs.append(h)
        h.rotate = True
        await _html()
        await _html()

        h = _Harness(monkeypatch, html=None)
        runs.append(h)
        await _html()

        h = _Harness(monkeypatch)
        runs.append(h)
        h.rotate = True
        h.on_provider = RuntimeError("down")
        with pytest.raises(HTTPException):
            await _html()
        with pytest.raises(HTTPException):
            await _html(email=SECOND)
        db_busy.mark()
        with pytest.raises(HTTPException):
            await _html(prefetch=True)

        for run in runs:
            assert run.sql, "the harness saw no SQL"
            assert run.message_writes() == []

    def test_the_route_source_holds_no_write_of_email_messages(self) -> None:
        import inspect

        source = inspect.getsource(m.get_message_html)
        assert not _MESSAGE_WRITE.search(source)
        assert "ea.user_id = :user_id" in source


# ── 3b. The provider 429 ────────────────────────────────────────────────────


class _LogSpy:
    """Records each call of the route's logger: the event and its fields."""

    def __init__(self) -> None:
        self.lines: list[tuple[str, str, dict[str, Any]]] = []

    def __getattr__(self, level: str) -> Any:
        def _record(event: str, **fields: Any) -> None:
            self.lines.append((level, event, fields))
        return _record

    def events(self, name: str) -> list[dict[str, Any]]:
        return [fields for _lvl, event, fields in self.lines if event == name]


def _http_429(retry_after: str | None) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://graph.microsoft.com/v1.0/me/messages/prov-msg")
    headers = {"Retry-After": retry_after} if retry_after is not None else {}
    response = httpx.Response(429, headers=headers, request=request)
    return httpx.HTTPStatusError("429 Too Many Requests", request=request, response=response)


class _SpentRateLimit(ProviderRateLimited):
    """A provider that spent its own tries, with no answer to read."""


class TestTheProvider429:

    @pytest.mark.parametrize(("error", "wait"), [
        (_http_429("12"), "12"),
        (_http_429(None), "30"),
        (_http_429("86400"), "30"),
        (_http_429("Wed, 21 Oct 2026 07:28:00 GMT"), "30"),
        (_SpentRateLimit("spent"), "30"),
    ], ids=["seconds", "no-header", "too-long", "a-date", "provider-rate-limited"])
    async def test_a_429_is_a_503_with_retry_after_and_one_log_line(
        self, monkeypatch, error: BaseException, wait: str,
    ) -> None:
        h = _Harness(monkeypatch, html="<p>the secret body</p>")
        spy = _LogSpy()
        monkeypatch.setattr(m, "_log", spy)
        h.on_provider = error
        with pytest.raises(HTTPException) as err:
            await _html(prefetch=True)
        assert err.value.status_code == 503
        assert err.value.headers == {"Retry-After": wait}
        lines = spy.events("email.html.provider_429")
        assert lines == [{"account_id": "acct-1", "retry_after": int(wait)}]
        assert spy.events("email.html.fetch_failed") == []
        assert h.redis.store == {}, "a 429 was cached"

    async def test_the_log_line_holds_no_mail_text(self, monkeypatch) -> None:
        h = _Harness(monkeypatch, html="<p>the secret body</p>")
        spy = _LogSpy()
        monkeypatch.setattr(m, "_log", spy)
        h.on_provider = _http_429("5")
        with pytest.raises(HTTPException):
            await _html()
        logged = repr(spy.lines)
        for text_of_mail in ("secret body", "old text", "prov-msg", "graph.microsoft.com"):
            assert text_of_mail not in logged, text_of_mail

    async def test_a_429_in_the_cause_chain_counts(self, monkeypatch) -> None:
        h = _Harness(monkeypatch)
        wrapped = RuntimeError("the fetch failed")
        wrapped.__cause__ = _http_429("7")
        h.on_provider = wrapped
        with pytest.raises(HTTPException) as err:
            await _html()
        assert (err.value.status_code, err.value.headers) == (503, {"Retry-After": "7"})

    async def test_another_provider_status_stays_502(self, monkeypatch) -> None:
        h = _Harness(monkeypatch)
        request = httpx.Request("GET", "https://graph.microsoft.com/x")
        h.on_provider = httpx.HTTPStatusError(
            "500", request=request, response=httpx.Response(500, request=request))
        with pytest.raises(HTTPException) as err:
            await _html()
        assert err.value.status_code == 502

    async def test_a_429_keeps_a_rotated_token(self, monkeypatch) -> None:
        h = _Harness(monkeypatch)
        h.rotate = True
        h.on_provider = _http_429("3")
        with pytest.raises(HTTPException):
            await _html()
        assert any("UPDATE email_accounts" in s for s in h.sql)
        assert h.message_writes() == []


# ── 4. html_remote ──────────────────────────────────────────────────────────


def _row(*, body_html: str | None, received_at: datetime | None) -> SimpleNamespace:
    return SimpleNamespace(
        id=MSG_ID, provider_message_id="p", thread_id="t", account_id="a",
        folder="inbox", labels=[], from_address=None, to_addresses=[],
        cc_addresses=[], bcc_addresses=[], subject="s", body_text="text",
        body_html=body_html, snippet="", has_attachments=False, is_read=False,
        is_starred=False, is_flagged=False, importance="normal", categories=[],
        received_at=received_at, synced_at=None, snoozed_until=None,
    )


class TestHtmlRemote:

    COLD = datetime.now(UTC) - timedelta(days=120)
    HOT = datetime.now(UTC) - timedelta(days=10)

    def test_a_cold_message_with_no_html_is_remote(self) -> None:
        assert core._row_to_message(_row(body_html=None, received_at=self.COLD)).html_remote

    def test_the_flag_off_is_never_remote(self, monkeypatch) -> None:
        _flags(monkeypatch, from_provider=False)
        msg = core._row_to_message(_row(body_html=None, received_at=self.COLD))
        assert msg.html_remote is False

    @pytest.mark.parametrize(("body_html", "when"), [
        ("<p>kept</p>", "cold"),
        ("", "cold"),
        (None, "hot"),
        (None, "none"),
    ])
    def test_the_other_rows_are_not_remote(self, body_html, when) -> None:
        received = {"cold": self.COLD, "hot": self.HOT, "none": None}[when]
        msg = core._row_to_message(_row(body_html=body_html, received_at=received))
        assert msg.html_remote is False

    def test_a_list_row_carries_the_field(self) -> None:
        dumped = core._row_to_message(_row(body_html=None, received_at=self.COLD)).model_dump()
        assert dumped["html_remote"] is True
        assert core.EmailMessageModel.model_fields["html_remote"].default is False


# ── 4b. Fix round 1: the open, the light search, the body-only fetch ────────


class _OpenDb:
    """The session of the open route: one row, and each write recorded."""

    def __init__(self, row: Any) -> None:
        self.row = row
        self.sql: list[str] = []

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> Any:
        sql = str(statement)
        self.sql.append(sql)
        return SimpleNamespace(fetchone=lambda: self.row, fetchall=lambda: [])


def _open_harness(monkeypatch: pytest.MonkeyPatch, *, fetched_html: str | None) -> _OpenDb:
    """Patch the open route for a cold row stored as headers only."""
    row = _row(body_html=None, received_at=TestHtmlRemote.COLD)
    row.body_text = ""
    row.stored_bytes = 0
    db = _OpenDb(row)

    @asynccontextmanager
    async def _tenant_session():
        yield db

    class _Provider:
        async def authenticate(self) -> bool:
            return True

        async def get_message(self, _pmid: str) -> Any:
            return SimpleNamespace(body_text="the text", body_html=fetched_html,
                                   has_attachments=False, attachments=[])

        def credentials_dirty(self) -> bool:
            return False

    async def _provider_for_message(*_a: Any, **_k: Any) -> Any:
        return _Provider(), "prov-msg", "acct-1", object()

    monkeypatch.setattr(m, "_tenant_session", _tenant_session)
    monkeypatch.setattr(m, "_provider_for_message", _provider_for_message)
    return db


class TestTheOpenAnswersTheTruth:
    """Item 1 of fix round 1. The open route read the row before it fetched
    the body, so a hydrated cold row answered with HTML and ``html_remote``."""

    async def test_a_hydrated_cold_row_is_not_remote(self, monkeypatch) -> None:
        _open_harness(monkeypatch, fetched_html="<p>fetched</p>")
        msg = await m.get_message(MSG_ID, user=_user())
        assert msg.body_html == "<p>fetched</p>"
        assert msg.html_remote is False

    async def test_a_cold_row_with_no_html_stays_remote(self, monkeypatch) -> None:
        _open_harness(monkeypatch, fetched_html=None)
        msg = await m.get_message(MSG_ID, user=_user())
        assert msg.body_html is None
        assert msg.html_remote is True


async def _search(monkeypatch: pytest.MonkeyPatch, *, light: bool) -> dict[str, Any]:
    """One search row: a cold message, through the real route."""
    from gateway.routes.email.transport import search as s

    from tests.unit._email_fakes import bind_db

    row = _row(body_html=None, received_at=TestHtmlRemote.COLD)
    row.rank, row.sim, row.highlight = 0.0, 0.0, ""

    async def _execute(_stmt: Any, _params: dict[str, Any] | None = None) -> Any:
        return SimpleNamespace(scalar=lambda: 1, fetchall=lambda: [row])

    async def _also_in(*_a: Any, **_k: Any) -> dict[str, Any]:
        return {}

    monkeypatch.setattr(s, "_tenant_session", bind_db(SimpleNamespace(execute=_execute)))
    monkeypatch.setattr(s, "_also_in_by_message", _also_in)
    resp = await s.search_messages(
        q=None, account_id="acc-1", folder=None, label=None, labels=None,
        uncategorized=False, from_addr=None, to_addr=None, received_after=None,
        received_before=None, is_read=None, is_starred=None, has_attachments=None,
        sender_category=None, importance=None, hybrid=False, light=light,
        page=1, page_size=50, user=_user(),
    )
    rows = resp["emails"] if isinstance(resp, dict) else resp.emails
    assert len(rows) == 1
    return rows[0]


class TestTheLightSearch:
    """Item 4 of fix round 1. The light search selects ``NULL AS body_html``,
    so its rows cannot say where the HTML is."""

    async def test_a_light_row_is_never_remote(self, monkeypatch) -> None:
        assert (await _search(monkeypatch, light=True))["html_remote"] is False

    async def test_a_full_row_keeps_the_rule(self, monkeypatch) -> None:
        assert (await _search(monkeypatch, light=False))["html_remote"] is True


class TestTheBodyOnlyFetch:
    """Item 2 of fix round 1. ``get_message`` of Outlook expands each file,
    so the HTML route reads the body alone."""

    async def test_outlook_reads_the_body_with_no_files(self) -> None:
        from unittest.mock import AsyncMock

        from email_ingestion.providers.outlook import OutlookProvider

        request = httpx.Request("GET", "https://graph.microsoft.com/v1.0/me/messages/m-1")
        client = AsyncMock()
        client.get.return_value = httpx.Response(200, request=request, json={
            "id": "m-1", "body": {"contentType": "html", "content": "<p>old</p>"},
        })
        provider = OutlookProvider({"access_token": "t", "refresh_token": "r"})
        provider._http = client
        got = await provider.get_message_body("m-1")
        assert got.body_html == "<p>old</p>"
        client.get.assert_awaited_once()
        path = client.get.await_args.args[0]
        params = client.get.await_args.kwargs.get("params") or {}
        assert path.endswith("/me/messages/m-1")
        assert "$expand" not in params, "the body read expanded the attachments"
        assert params.get("$select") == "id,body"

    async def test_the_base_default_reads_get_message(self) -> None:
        from email_ingestion.providers.base import BaseEmailProvider

        sentinel = SimpleNamespace(body_html="<p>x</p>")

        class _Fake:
            get_message_body = BaseEmailProvider.get_message_body

            async def get_message(self, _pmid: str) -> Any:
                return sentinel

        assert await _Fake().get_message_body("p") is sentinel

    async def test_the_route_uses_the_body_only_fetch(self, monkeypatch) -> None:
        h = _Harness(monkeypatch)
        assert (await _html()).source == "provider"
        assert h.provider_calls == 1


# ── 5. One owner of the window and the flags ────────────────────────────────

_SCAN_ROOTS = (REPO / "apps", REPO / "packages")
_EMAIL_ROOTS = (
    REPO / "apps/services/email_ingestion/email_ingestion",
    REPO / "apps/services/gateway/gateway/routes/email",
)
_HTML_TIER = REPO / "apps/services/email_ingestion/email_ingestion/html_tier.py"
_SETTINGS = REPO / "packages/acb_common/acb_common/settings.py"
_NINETY = re.compile(r"(?<![\w.])90(?![\w.])")
_NINETY_DAYS = re.compile(r"timedelta\(\s*days\s*=\s*90\s*\)")
_FLAG_NAMES = frozenset({"email_html_from_provider", "email_html_hot_only"})
_FLAG_ENV = frozenset({"EMAIL_HTML_FROM_PROVIDER", "EMAIL_HTML_HOT_ONLY"})


def _python_files(roots: tuple[Path, ...]) -> list[Path]:
    out: list[Path] = []
    for root in roots:
        for path in root.rglob("*.py"):
            if any(p in {".venv", "node_modules", "__pycache__"} for p in path.parts):
                continue
            out.append(path)
    return out


def _ninety_near_received_at(source: str) -> list[int]:
    """The lines that hold the number 90 within two lines of ``received_at``."""
    lines = source.splitlines()
    hits: list[int] = []
    for i, line in enumerate(lines):
        if "received_at" not in line:
            continue
        for j in range(max(0, i - 2), min(len(lines), i + 3)):
            if _NINETY.search(lines[j]):
                hits.append(j + 1)
    return sorted(set(hits))


def _flag_reads(source: str) -> list[int]:
    """The lines that read one of the two flags: an attribute, a name, or a
    string that is the flag or its environment name."""
    hits: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if (isinstance(node, ast.Attribute) and node.attr in _FLAG_NAMES) or (
            isinstance(node, ast.Name) and node.id in _FLAG_NAMES
        ) or (
            isinstance(node, ast.Constant) and isinstance(node.value, str)
            and node.value.strip() in (_FLAG_NAMES | _FLAG_ENV)
        ):
            hits.append(node.lineno)
    return sorted(set(hits))


class TestTheOneOwner:

    def test_no_module_outside_html_tier_holds_90_next_to_received_at(self) -> None:
        bad = []
        for path in _python_files(_SCAN_ROOTS):
            if path == _HTML_TIER:
                continue
            for line in _ninety_near_received_at(path.read_text(encoding="utf-8-sig")):
                bad.append(f"{path.relative_to(REPO)}:{line}")
        assert bad == [], (
            "the HTML hot window is html_tier.HTML_HOT_DAYS, never a literal 90 "
            f"next to received_at: {bad}"
        )

    def test_no_email_module_outside_html_tier_builds_a_90_day_delta(self) -> None:
        bad = [
            str(path.relative_to(REPO)) for path in _python_files(_EMAIL_ROOTS)
            if path != _HTML_TIER and _NINETY_DAYS.search(path.read_text(encoding="utf-8-sig"))
        ]
        assert bad == [], f"use html_tier.hot_cutoff(): {bad}"

    def test_no_module_outside_html_tier_reads_the_two_flags(self) -> None:
        bad = []
        for path in _python_files(_SCAN_ROOTS):
            if path in (_HTML_TIER, _SETTINGS):
                continue
            for line in _flag_reads(path.read_text(encoding="utf-8-sig")):
                bad.append(f"{path.relative_to(REPO)}:{line}")
        assert bad == [], f"html_tier is the one reader of the two flags: {bad}"

    def test_html_tier_reads_both_flags(self) -> None:
        assert _flag_reads(_HTML_TIER.read_text(encoding="utf-8-sig"))

    def test_the_scans_can_go_red(self) -> None:
        near = "cutoff = 90\nrows = [r for r in rows if r.received_at < cutoff]\n"
        assert _ninety_near_received_at(near) == [1]
        assert _ninety_near_received_at("x = 90\n\n\n\nreceived_at = 1\n") == []
        assert _NINETY_DAYS.search("old = now - timedelta(days=90)")
        assert _flag_reads("x = settings.email_html_hot_only\n") == [1]
        assert _flag_reads("os.environ.get('EMAIL_HTML_FROM_PROVIDER')\n") == [1]
        assert _flag_reads("getattr(s, 'email_html_from_provider')\n") == [1]
        assert _flag_reads('"""The flag EMAIL_HTML_HOT_ONLY is read elsewhere."""\n') == []


# ── 6. R8: the owner check on a real database ───────────────────────────────


@contextmanager
def _bound(org: str):
    token = bind_tenant(org)
    try:
        yield
    finally:
        release_tenant(token)


def _assert_non_priv(app_eng) -> None:
    with app_eng.connect() as c:
        role = c.execute(text(
            "SELECT rolsuper, rolbypassrls FROM pg_roles "
            "WHERE rolname = current_user")).first()
    assert role is not None and not role[0] and not role[1], (
        "this suite connects as a SUPERUSER/BYPASSRLS role — RLS is bypassed"
    )


def _seed_cold_message(admin, *, org: str, owner: str) -> tuple[str, str]:
    """A mailbox of *owner* with one cold mail and no stored HTML.
    Returns (account id, message id)."""
    with admin.begin() as c:
        acc = str(c.execute(text(
            "INSERT INTO email_accounts (user_id, provider, email_address, "
            "credentials_encrypted, organization_id) VALUES (:u, 'microsoft', :m, "
            "'x', CAST(:o AS uuid)) RETURNING id"),
            {"u": owner, "m": f"box-{uuid.uuid4().hex[:8]}@em-s1.test",
             "o": org}).scalar_one())
        msg = str(c.execute(text(
            "INSERT INTO email_messages (account_id, provider_message_id, thread_id, "
            "folder, from_address, to_addresses, subject, body_text, body_html, "
            "received_at, organization_id) VALUES (CAST(:a AS uuid), :pm, :t, "
            "'inbox', CAST(:f AS jsonb), '[]'::jsonb, 'old', 'old text', NULL, :r, "
            "CAST(:o AS uuid)) RETURNING id"),
            {"a": acc, "pm": f"pm-{uuid.uuid4().hex[:12]}",
             "t": f"t-{uuid.uuid4().hex[:8]}",
             "f": json.dumps({"email": "sender@contoso.test", "name": "S"}),
             "r": datetime.now(UTC) - timedelta(days=200), "o": org}).scalar_one())
    return acc, msg


def _row_state(admin, message_id: str) -> tuple[Any, ...]:
    with admin.connect() as c:
        return tuple(c.execute(text(
            "SELECT xmin::text, updated_at, body_html FROM email_messages "
            "WHERE id = CAST(:m AS uuid)"), {"m": message_id}).one())


def _purge(admin, account_ids: list[str]) -> None:
    with admin.begin() as c:
        for acc in account_ids:
            c.execute(text("DELETE FROM email_accounts WHERE id = CAST(:a AS uuid)"),
                      {"a": acc})


@pytest.fixture()
def fake_provider(monkeypatch) -> dict[str, Any]:
    """The provider and the cache are fakes. The owner SQL is real."""
    state: dict[str, Any] = {"calls": 0}
    redis = _TextRedis()

    class _Provider:
        async def authenticate(self) -> bool:
            return True

        async def get_message_body(self, _pmid: str) -> Any:
            state["calls"] += 1
            return SimpleNamespace(body_html="<p>the old mail of member A</p>")

        def credentials_dirty(self) -> bool:
            return False

    monkeypatch.setattr(m, "_decrypt_credentials", lambda _blob: ({}, object()))
    monkeypatch.setattr(m, "_instantiate_provider", lambda _name, _creds: _Provider())
    monkeypatch.setattr(m, "get_tenant_redis", lambda *, binary=False: TenantRedis(redis))
    state["redis"] = redis
    return state


@_DB_GATE
class TestTheOwnerCheckOnARealDatabase:

    async def test_only_the_owner_reads_the_html(
        self, promoted, app_engine, fake_provider,  # noqa: F811
    ) -> None:
        """Two members of ONE organization: row level security does not hide
        the row, so only the owner predicate of the route does. And two
        organizations: a session of org A never reads a message of org B."""
        _assert_non_priv(app_engine)
        p = promoted
        member_a = f"a-{uuid.uuid4().hex[:8]}@em-s1.test"
        member_b = f"b-{uuid.uuid4().hex[:8]}@em-s1.test"
        acc_a, msg_a = _seed_cold_message(p.admin_engine, org=p.org_b, owner=member_a)
        before = _row_state(p.admin_engine, msg_a)
        app_dsn = p.app_url.render_as_string(hide_password=False)
        try:
            async with tenant_engine_scope(app_dsn):
                with _bound(p.org_b):
                    owned = await m.get_message_html(
                        msg_a, prefetch=False, user=_user(member_a, p.org_b))
                    assert (owned.source, owned.body_html) == (
                        "provider", "<p>the old mail of member A</p>")
                    again = await m.get_message_html(
                        msg_a.upper(), prefetch=False, user=_user(member_a, p.org_b))
                    assert again.source == "cache"
                    calls_before = list(fake_provider["redis"].calls)
                    with pytest.raises(HTTPException) as err:
                        await m.get_message_html(
                            msg_a, prefetch=False, user=_user(member_b, p.org_b))
                    assert err.value.status_code == 404, "another member of org B"
                    assert fake_provider["redis"].calls == calls_before, (
                        "the route read a cache key for a message of another member"
                    )
                with _bound(p.org_a):
                    with pytest.raises(HTTPException) as err:
                        await m.get_message_html(
                            msg_a, prefetch=False, user=_user(member_a, p.org_a))
                    assert err.value.status_code == 404, (
                        "a session of org A read a message of org B"
                    )
            assert fake_provider["calls"] == 1, "only the first owner read may reach the provider"
            assert sorted(fake_provider["redis"].store) == [f"cc:{p.org_b}:email-html:{msg_a}"]
            assert _row_state(p.admin_engine, msg_a) == before, (
                "the route wrote email_messages"
            )
        finally:
            _purge(p.admin_engine, [acc_a])
