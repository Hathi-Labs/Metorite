"""A failure of the mail provider gets an honest status, never a bare 500.

Defect (production gateway log, October 2026): ``GET
/email/messages/{id}/full-body`` answered 500 right after ``full_body.failed
error="Client error '429 Too Many Requests' ... graph.microsoft.com"``. The
chat agent fetched several full bodies at once. Rule actions also logged
``email.rule_action_failed ... 404 Not Found`` as a warning for a message
that had moved or been deleted.

R7 fences named here:

* ``email-provider-429``: a provider 429 is a route 429 with
  ``Retry-After`` (the provider's own value, else 30) and the detail
  "The mail provider is throttling requests. Try again in N seconds."
* ``email-provider-404``: a provider 404 is a route 404 with "This message
  is no longer in the mailbox (moved or deleted)."
* ``email-provider-sign-in``: a provider 401 or 403, or a failed token
  refresh, is a 409 with a reconnect hint, never a 401 of the gateway.
* ``email-graph-read-retry``: ``OutlookProvider._graph_read`` retries a 429
  once, only for a ``Retry-After`` of at most 5 seconds (1 second when the
  header is absent). A longer wait gets no retry.
* ``email-full-body-no-held-session``: the full-body route holds no session
  across the provider call, and maps a provider failure through the helper.
* ``email-rule-action-message-gone``: a rule action that gets a provider 404
  logs ``email.rule_action_skipped`` with ``reason=message_gone`` at info,
  and no ``email.rule_action_failed`` warning.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from email_ingestion.providers import outlook as outlook_mod
from email_ingestion.providers.base import ProviderRateLimited
from email_ingestion.providers.outlook import GRAPH_API_BASE, OutlookProvider
from fastapi import HTTPException
from gateway.routes.email import provider_errors as pe
from gateway.routes.email.automation import actions as actions_mod
from gateway.routes.email.transport import messages as messages_mod

from tests.unit._email_fakes import bind_db

_MID = "8b0c7a52-3f8e-4d1a-9c55-2a9a3b4c5d6e"
_ACC = "1d2e3f40-5a6b-4c7d-8e9f-a0b1c2d3e4f5"


def _status_error(status: int, *, headers: dict[str, str] | None = None,
                  url: str = f"{GRAPH_API_BASE}/me/messages/pm-1") -> httpx.HTTPStatusError:
    request = httpx.Request("GET", url)
    response = httpx.Response(status, headers=headers or {}, request=request)
    return httpx.HTTPStatusError(f"{status}", request=request, response=response)


# ── The mapper ──────────────────────────────────────────────────────────────


def test_a_429_with_retry_after_is_a_429_with_that_wait():
    err = pe.provider_http_error(
        _status_error(429, headers={"Retry-After": "7"}), route="t")
    assert err.status_code == 429
    assert err.headers == {"Retry-After": "7"}
    assert err.detail == ("The mail provider is throttling requests. "
                          "Try again in 7 seconds.")


@pytest.mark.parametrize("raw", ["inf", "1e999", "-inf", "nan", "soon"])
def test_a_retry_after_that_is_no_number_waits_the_default(raw):
    err = pe.provider_http_error(
        _status_error(429, headers={"Retry-After": raw}), route="t")
    assert err.headers == {"Retry-After": str(pe.DEFAULT_RETRY_AFTER_SECS)}


def test_a_429_with_no_retry_after_waits_the_default():
    err = pe.provider_http_error(_status_error(429), route="t")
    assert err.status_code == 429
    assert err.headers == {"Retry-After": str(pe.DEFAULT_RETRY_AFTER_SECS)}
    assert f"Try again in {pe.DEFAULT_RETRY_AFTER_SECS} seconds." in err.detail


def test_a_rate_limit_with_its_tries_spent_and_no_answer_is_a_429():
    class _Spent(ProviderRateLimited):
        pass

    err = pe.provider_http_error(_Spent(), route="t")
    assert err.status_code == 429


def test_a_429_in_the_cause_chain_counts():
    try:
        try:
            raise _status_error(429, headers={"Retry-After": "3"})
        except httpx.HTTPStatusError as inner:
            raise RuntimeError("wrapped") from inner
    except RuntimeError as outer:
        err = pe.provider_http_error(outer, route="t")
    assert (err.status_code, err.headers) == (429, {"Retry-After": "3"})


def test_a_404_says_the_message_moved_or_was_deleted():
    err = pe.provider_http_error(_status_error(404), route="t")
    assert err.status_code == 404
    assert err.detail == "This message is no longer in the mailbox (moved or deleted)."


@pytest.mark.parametrize("status", [401, 403])
def test_a_refused_sign_in_is_a_409_with_a_reconnect_hint(status):
    err = pe.provider_http_error(_status_error(status), route="t")
    assert err.status_code == 409
    assert "Reconnect" in err.detail


def test_a_failed_token_refresh_is_a_409():
    err = pe.provider_http_error(_status_error(
        400, url="https://login.microsoftonline.com/common/oauth2/v2.0/token"),
        route="t")
    assert err.status_code == 409


def test_an_unknown_failure_is_a_500_that_logs_the_route(monkeypatch):
    log = MagicMock()
    monkeypatch.setattr(pe, "_log", log)
    err = pe.provider_http_error(RuntimeError("boom"), route="get_full_body")
    assert err.status_code == 500
    assert "boom" not in err.detail
    assert log.error.call_args.args[0] == "email.provider_failed"
    assert log.error.call_args.kwargs["route"] == "get_full_body"


# ── Outlook: one bounded retry of a read ────────────────────────────────────


class _Graph:
    """A fake Graph that answers each GET from a queue."""

    def __init__(self, *answers: httpx.Response):
        self.answers = list(answers)
        self.gets = 0

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.gets += 1
        return self.answers.pop(0)


def _ok() -> httpx.Response:
    return httpx.Response(200, json={
        "id": "pm-1", "subject": "s", "body": {"contentType": "text", "content": "b"},
    })


def _provider(graph: _Graph, monkeypatch, *, retry: bool = True,
              ) -> tuple[OutlookProvider, list[float]]:
    waits: list[float] = []

    async def _sleep(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr(outlook_mod.asyncio, "sleep", _sleep)
    p = OutlookProvider({"access_token": "t", "refresh_token": "r"})
    p._http = httpx.AsyncClient(base_url=GRAPH_API_BASE,
                                transport=httpx.MockTransport(graph.handle))
    p.read_retry_429 = retry
    return p, waits


@pytest.mark.parametrize("method", ["get_message", "get_message_body"])
async def test_the_retry_is_off_unless_the_caller_asks(monkeypatch, method):
    """H-352: a caller that holds a session across the read must never
    sleep on a pool slot. The default makes no retry and no wait."""
    assert OutlookProvider({"access_token": "t"}).read_retry_429 is False
    graph = _Graph(httpx.Response(429, headers={"Retry-After": "2"}))
    p, waits = _provider(graph, monkeypatch, retry=False)
    with pytest.raises(httpx.HTTPStatusError):
        await getattr(p, method)("pm-1")
    assert (graph.gets, waits) == (1, [])


async def test_the_body_hydrate_with_its_session_open_never_sleeps(monkeypatch):
    """``hydrate_message_body`` holds the caller's session across the fetch.
    A throttled Outlook read there fails at once and keeps the stored text."""
    from gateway.routes.email import core

    graph = _Graph(httpx.Response(429, headers={"Retry-After": "2"}))
    p, waits = _provider(graph, monkeypatch, retry=False)

    async def _authed() -> bool:
        return True

    p.authenticate = _authed  # type: ignore[method-assign]

    async def _row(_db, _mid):
        return SimpleNamespace(body_text="", body_html=None, snippet="s",
                               received_at=None, stored_bytes=0)

    async def _for_message(_db, _mid, _user):
        return p, "pm-1", _ACC, _Store()

    monkeypatch.setattr(core, "_hydrate_row", _row)
    monkeypatch.setattr(core, "_provider_for_message", _for_message)
    got = await core.hydrate_message_body(AsyncMock(), _MID, "u@x.example")
    assert got == ""
    assert (graph.gets, waits) == (1, [])


def test_only_the_full_body_route_turns_the_retry_on():
    """The flag is set in one place, a route that holds no session."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "apps"
    hits = [f.relative_to(root).as_posix() for f in root.rglob("*.py")
            if "read_retry_429 = True" in f.read_text(encoding="utf-8")]
    assert hits == [
        "services/gateway/gateway/routes/email/transport/messages.py"]
    src = (root / hits[0]).read_text(encoding="utf-8")
    body = src[src.index("async def get_full_body"):]
    body = body[:body.index("@router", 1)]
    assert "read_retry_429 = True" in body


@pytest.mark.parametrize("method", ["get_message", "get_message_body"])
async def test_a_short_429_is_retried_once_after_its_wait(monkeypatch, method):
    graph = _Graph(httpx.Response(429, headers={"Retry-After": "2"}), _ok())
    p, waits = _provider(graph, monkeypatch)
    msg = await getattr(p, method)("pm-1")
    assert msg.body_text == "b"
    assert (graph.gets, waits) == (2, [2.0])


async def test_a_429_with_no_header_waits_one_second(monkeypatch):
    graph = _Graph(httpx.Response(429), _ok())
    p, waits = _provider(graph, monkeypatch)
    await p.get_message("pm-1")
    assert waits == [1.0]


async def test_a_long_429_is_not_retried_and_keeps_its_header(monkeypatch):
    graph = _Graph(httpx.Response(429, headers={"Retry-After": "30"}))
    p, waits = _provider(graph, monkeypatch)
    with pytest.raises(httpx.HTTPStatusError) as exc:
        await p.get_message("pm-1")
    assert (graph.gets, waits) == (1, [])
    err = pe.provider_http_error(exc.value, route="t")
    assert (err.status_code, err.headers) == (429, {"Retry-After": "30"})


async def test_a_second_429_goes_back_to_the_caller(monkeypatch):
    graph = _Graph(httpx.Response(429, headers={"Retry-After": "1"}),
                   httpx.Response(429, headers={"Retry-After": "4"}))
    p, waits = _provider(graph, monkeypatch)
    with pytest.raises(httpx.HTTPStatusError) as exc:
        await p.get_message_body("pm-1")
    assert (graph.gets, waits) == (2, [1.0])
    assert pe.provider_http_error(exc.value, route="t").headers == {"Retry-After": "4"}


async def test_a_404_is_not_retried(monkeypatch):
    graph = _Graph(httpx.Response(404))
    p, waits = _provider(graph, monkeypatch)
    with pytest.raises(httpx.HTTPStatusError) as exc:
        await p.get_message("pm-1")
    assert (graph.gets, waits) == (1, [])
    assert pe.provider_http_error(exc.value, route="t").status_code == 404


# ── The full-body route ─────────────────────────────────────────────────────


class _Store:
    def decrypt(self, blob: str) -> str:
        return json.dumps({"access_token": "a", "refresh_token": "r"})

    def encrypt(self, value: str) -> str:
        return "enc:" + value


class _RowDb:
    def __init__(self, row: Any):
        self.row = row
        self.open = 0
        self.statements: list[str] = []

    async def execute(self, stmt, params=None):
        self.statements.append(str(stmt))
        return SimpleNamespace(fetchone=lambda: self.row)


class _Raises:
    """A provider whose fetch raises *exc*, and checks no session is open."""

    def __init__(self, exc: Exception, sessions: list[int]):
        self.exc = exc
        self.sessions = sessions
        self.open_at_fetch: int | None = None
        self.read_retry_429 = False
        self.retry_at_fetch: bool | None = None
        self.auth_ok = True

    async def authenticate(self) -> bool:
        return self.auth_ok

    async def get_message(self, _pmid: str):
        self.open_at_fetch = self.sessions[0]
        self.retry_at_fetch = self.read_retry_429
        raise self.exc

    def credentials_dirty(self) -> bool:
        return False


def _wire_full_body(monkeypatch, exc: Exception) -> _Raises:
    from acb_llm import key_store

    db = _RowDb(SimpleNamespace(
        provider_message_id="pm-1", account_id=_ACC, provider="microsoft",
        credentials_encrypted="x"))
    open_now = [0]
    inner = bind_db(db)

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _session():
        open_now[0] += 1
        try:
            async with inner() as s:
                yield s
        finally:
            open_now[0] -= 1

    provider = _Raises(exc, open_now)
    monkeypatch.setattr(messages_mod, "_tenant_session", _session)
    monkeypatch.setattr(messages_mod, "_instantiate_provider",
                        lambda name, creds: provider)
    monkeypatch.setattr(key_store, "get_key_store", lambda: _Store())
    return provider


@pytest.mark.parametrize(("exc", "status"), [
    (_status_error(429, headers={"Retry-After": "9"}), 429),
    (_status_error(404), 404),
    (_status_error(401), 409),
    (RuntimeError("boom"), 500),
])
async def test_the_full_body_route_maps_a_provider_failure(monkeypatch, exc, status):
    provider = _wire_full_body(monkeypatch, exc)
    with pytest.raises(HTTPException) as got:
        await messages_mod.get_full_body(_MID, user=SimpleNamespace(email="u@x.example"))
    assert got.value.status_code == status
    if status == 429:
        assert got.value.headers == {"Retry-After": "9"}
    assert provider.open_at_fetch == 0  # no session held across the fetch
    assert provider.retry_at_fetch is True  # so it may wait on a 429


async def test_a_false_sign_in_probe_is_a_503_not_a_reconnect(monkeypatch):
    """``authenticate()`` gives False for a throttled probe too. The answer
    must not tell the member to reconnect a mailbox that may work."""
    provider = _wire_full_body(monkeypatch, RuntimeError("never reached"))
    provider.auth_ok = False
    with pytest.raises(HTTPException) as got:
        await messages_mod.get_full_body(_MID, user=SimpleNamespace(email="u@x.example"))
    assert got.value.status_code == 503
    assert got.value.detail == pe.SIGN_IN_UNCHECKED_DETAIL
    assert got.value.headers == {"Retry-After": str(pe.SIGN_IN_RETRY_AFTER_SECS)}
    assert provider.open_at_fetch is None


async def test_the_full_body_route_answers_404_for_an_id_that_is_no_uuid(monkeypatch):
    _wire_full_body(monkeypatch, RuntimeError("never reached"))
    with pytest.raises(HTTPException) as got:
        await messages_mod.get_full_body("ALL", user=SimpleNamespace(email="u@x.example"))
    assert got.value.status_code == 404


# ── A rule action on a gone message ─────────────────────────────────────────


async def test_a_rule_action_on_a_gone_message_is_skipped_at_info(monkeypatch):
    log = MagicMock()
    monkeypatch.setattr(actions_mod, "_log", log)
    provider = MagicMock()
    provider.move_to_folder = AsyncMock(side_effect=_status_error(404))
    db = AsyncMock()
    errors: list[dict[str, str]] = []
    done = await actions_mod._apply_rule_actions(
        db, provider, _MID, "pm-1", [{"type": "ARCHIVE"}],
        account_id=_ACC, errors_out=errors)
    assert done == []
    db.execute.assert_not_awaited()  # no local folder write
    log.info.assert_any_call("email.rule_action_skipped", action="ARCHIVE",
                             reason="message_gone")
    assert not any(c.args and c.args[0] == "email.rule_action_failed"
                   for c in log.warning.call_args_list)
    # The History row still records the issue, so the run is never APPLIED.
    assert errors == [{"type": "ARCHIVE", "error": pe.MESSAGE_GONE_DETAIL}]


async def test_a_rule_action_on_a_throttled_mailbox_still_fails(monkeypatch):
    log = MagicMock()
    monkeypatch.setattr(actions_mod, "_log", log)
    provider = MagicMock()
    provider.trash_message = AsyncMock(side_effect=_status_error(429))
    errors: list[dict[str, str]] = []
    await actions_mod._apply_rule_actions(
        AsyncMock(), provider, _MID, "pm-1", [{"type": "TRASH"}],
        account_id=_ACC, errors_out=errors)
    assert log.warning.call_args.args[0] == "email.rule_action_failed"
    assert errors and errors[0]["type"] == "TRASH"
