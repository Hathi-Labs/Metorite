"""EM-G11f1 — one mailbox teardown: the Gmail filters, the revoke, the cache.

Spec: ``project-docs/specs/email_app_master_plan.md`` §12.3.13.9 (PR-G7).

R7 fence named here: ``email-mailbox-teardown``. EM-G11f1 wires
``routes/email/teardown.py`` into ``DELETE /email/accounts/{id}`` only.

**Hermetic.** The doubles of ``test_email_disconnect_order.py`` (the
``wired`` fixture) stand in for the session, the scheduler and the key store.
The revoke runs the REAL ``httpx`` client over ``httpx.MockTransport``, so the
test reads the request that Google would get.

* ``test_filters_go_after_the_commit``: the ownership read keeps each filter
  id of a Gmail row, because the cascade deletes the ids. Each filter goes at
  Gmail after the ``DELETE`` commits and before the revoke, with no block
  open (review round 1). A refused ``DELETE`` removes no filter. A filter that
  fails does not stop the next one or the 204.
* ``test_gmail_revokes_the_refresh_token``: one POST to
  ``https://oauth2.googleapis.com/revoke``, after the ``DELETE``, with the
  refresh token in the form body and no query.
* ``test_a_failed_revoke_still_answers_204``: a 400, a transport error, a slow
  Google and an unreadable blob each log ``email.revoke_failed`` with a reason,
  and the route answers 204.
* ``test_outlook_revokes_nothing``: an Outlook row makes no Google call and
  reads no filter. Its Graph subscription goes, as on ``main``.
* A shared grant is not revoked: when migration 262's function answers true,
  NULL, or fails, the revoke is skipped (review round 1, P1).
* The Redis keys of the mailbox go, and the keys of another mailbox and of
  another organization stay.

The R8 case is in ``test_email_disconnect_order.py``
(``TestTheDisconnectUnderForceRLS``): the filter ids are read under FORCE RLS
before the ``DELETE``, and the real shared-grant function sees a row of the
same address in the same org and in another org.

Run::

    uv run pytest tests/unit/test_email_mailbox_teardown.py -v
"""
from __future__ import annotations

import asyncio
import fnmatch
import json
import time
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs

import pytest

pytest.importorskip("sqlalchemy")

import acb_llm.key_store as key_store_mod
import httpx
from acb_common.tenant_redis import key, organization_scope
from fastapi import HTTPException
from gateway.routes.email import teardown
from gateway.routes.email.transport import accounts

# ``wired`` is a fixture, used by name, so the import is load-bearing even
# though it reads as unused.
from tests.unit.test_email_disconnect_order import (  # noqa: F401
    OWNER,
    ROW_ORG,
    _lock_timeout_error,
    _row,
    _user,
    wired,
)

REFRESH = "rt-em-g11f1-must-not-leak"
ACCESS = "at-em-g11f1-must-not-leak"
ACC = "6f1c2d3e-4a5b-4c6d-8e7f-90a1b2c3d4e5"
OTHER_ACC = "0a1b2c3d-4e5f-4a6b-8c7d-8e9f0a1b2c3d"
OTHER_ORG = "99999999-8888-7777-6666-555555555555"


class _Log:
    def __init__(self) -> None:
        self.lines: list[tuple[str, str, dict[str, Any]]] = []

    def info(self, event: str, **kw: Any) -> None:
        self.lines.append(("info", event, kw))

    def warning(self, event: str, **kw: Any) -> None:
        self.lines.append(("warning", event, kw))

    def names(self) -> list[str]:
        return [name for _level, name, _kw in self.lines]


class _Gmail:
    """A Gmail provider double that records each filter delete."""

    def __init__(self, ledger: Any, fail: set[str]) -> None:
        self.ledger = ledger
        self.fail = fail

    async def delete_filter(self, filter_id: str) -> None:
        self.ledger.events.append(("filter_delete", self.ledger.open, filter_id))
        if filter_id in self.fail:
            request = httpx.Request("DELETE", "https://gmail.test/f")
            raise httpx.HTTPStatusError(
                f"refused, bearer {ACCESS}", request=request,
                response=httpx.Response(500, request=request))


@pytest.fixture()
def gmail(wired, monkeypatch):  # noqa: F811
    """Wire a Gmail row: the doubles of ``wired``, a recording teardown log,
    a key store with distinct tokens, and the REAL revoke over a mock
    transport. Returns a setup function."""

    def _setup(rows: dict[str, dict[str, Any]], *, fail: set[str] = frozenset(),
               google: Any = 200, blob_ok: bool = True, **wired_kw: Any):
        ledger = wired(rows, **wired_kw)
        log = _Log()
        monkeypatch.setattr(teardown, "_log", log)

        def _decrypt(blob: str) -> str:
            ledger.events.append(("decrypt", ledger.open, blob))
            if not blob_ok:
                raise ValueError("bad blob")
            return json.dumps({"access_token": ACCESS, "refresh_token": REFRESH})

        monkeypatch.setattr(key_store_mod, "get_key_store",
                            lambda: SimpleNamespace(decrypt=_decrypt))
        provider = _Gmail(ledger, set(fail))
        monkeypatch.setattr(teardown, "_instantiate_provider",
                            lambda name, creds: provider)
        requests: list[httpx.Request] = []

        async def _handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            ledger.events.append(("google", ledger.open))
            if google == "raise":
                raise httpx.ConnectError("unreachable", request=request)
            if google == "slow":
                await asyncio.sleep(30)
            return httpx.Response(google if isinstance(google, int) else 200)

        monkeypatch.setattr(teardown, "_post_revoke", _REAL_POST_REVOKE)
        monkeypatch.setattr(teardown, "_http_client", lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(_handler)))
        return SimpleNamespace(ledger=ledger, log=log, requests=requests)

    return _setup


#: The real POST, kept before ``wired`` replaces it with a stub.
_REAL_POST_REVOKE = teardown._post_revoke


def _no_secret(*records: Any) -> None:
    text_of = repr(records)
    assert REFRESH not in text_of and ACCESS not in text_of, "a token reached a log"


# ── test_filters_go_after_the_commit ────────────────────────────────────────


async def test_filters_go_after_the_commit(gmail):
    rows = {ACC: _row(provider="gmail", sub=None, filters=("flt-1", "flt-2", "flt-1"))}
    g = gmail(rows)
    assert await accounts.delete_account(ACC, user=_user()) is None
    kinds = g.ledger.kinds()
    deletes = [e for e in g.ledger.events if e[0] == "filter_delete"]
    assert [e[2] for e in deletes] == ["flt-1", "flt-2"], "each id goes once, in order"
    assert all(e[1] == 0 for e in deletes), "a filter went with a block open"
    assert g.ledger.first("filter_read")[1] == 1, "the ids were read outside a block"
    assert kinds.index("filter_read") < kinds.index("delete"), (
        "the cascade deleted the ids before they were read")
    delete_at = kinds.index("delete")
    commit_at = next(i for i, k in enumerate(kinds) if k == "close" and i > delete_at)
    first_filter = kinds.index("filter_delete")
    assert first_filter > commit_at, "a filter went before the DELETE committed"
    last_filter = max(i for i, k in enumerate(kinds) if k == "filter_delete")
    assert last_filter < kinds.index("google"), "the revoke ended the grant first"
    assert ACC not in rows
    assert ("info", "email.disconnect.filters_removed",
            {"account_id": ACC, "removed": 2, "failed": 0}) in g.log.lines


async def test_a_failed_filter_does_not_stop_the_next_one_or_the_204(gmail):
    rows = {ACC: _row(provider="gmail", sub=None, filters=("flt-bad", "flt-ok"))}
    g = gmail(rows, fail={"flt-bad"})
    assert await accounts.delete_account(ACC, user=_user()) is None
    assert [e[2] for e in g.ledger.events if e[0] == "filter_delete"] == [
        "flt-bad", "flt-ok"]
    assert ACC not in rows
    assert ("warning", "email.disconnect.filter_remove_failed",
            {"account_id": ACC, "error": "HTTPStatusError", "status": 500}) in g.log.lines
    assert ("info", "email.disconnect.filters_removed",
            {"account_id": ACC, "removed": 1, "failed": 1}) in g.log.lines
    _no_secret(g.log.lines)


async def test_a_slow_filter_removal_is_bounded(gmail, monkeypatch):
    monkeypatch.setattr(teardown, "FILTER_REMOVAL_TIMEOUT_S", 0.05)
    rows = {ACC: _row(provider="gmail", sub=None, filters=("flt-1",))}
    g = gmail(rows)

    async def _slow(filter_id: str) -> None:
        await asyncio.sleep(30)

    monkeypatch.setattr(_Gmail, "delete_filter", lambda self, fid: _slow(fid))
    started = time.monotonic()
    assert await accounts.delete_account(ACC, user=_user()) is None
    assert time.monotonic() - started < 5, "the bound did not hold"
    assert ACC not in rows
    assert ("warning", "email.disconnect.filters_failed",
            {"account_id": ACC, "error": "TimeoutError", "removed": 0,
             "failed": 0}) in g.log.lines


async def test_a_gmail_row_with_no_filter_builds_no_provider(gmail):
    rows = {ACC: _row(provider="gmail", sub=None)}
    g = gmail(rows)
    assert await accounts.delete_account(ACC, user=_user()) is None
    assert "filter_delete" not in g.ledger.kinds()
    assert "email.disconnect.filters_removed" not in g.log.names()


# ── test_gmail_revokes_the_refresh_token ────────────────────────────────────


async def test_gmail_revokes_the_refresh_token(gmail):
    rows = {ACC: _row(provider="gmail", sub=None)}
    g = gmail(rows)
    assert await accounts.delete_account(ACC, user=_user()) is None
    [req] = g.requests
    assert req.method == "POST"
    assert str(req.url) == teardown.GOOGLE_REVOKE_URL == (
        "https://oauth2.googleapis.com/revoke")
    assert req.url.query == b"", "the token must never be in the URL"
    assert req.headers["content-type"] == "application/x-www-form-urlencoded"
    assert parse_qs(req.content.decode()) == {"token": [REFRESH]}
    kinds = g.ledger.kinds()
    assert kinds.index("google") > kinds.index("delete"), "the revoke ran before the DELETE"
    assert g.ledger.first("google")[1] == 0, "the revoke ran with a block open"
    assert ("info", "email.disconnect.grant_revoked",
            {"account_id": ACC, "provider": "gmail"}) in g.log.lines
    _no_secret(g.log.lines, [str(r.url) for r in g.requests])


async def test_the_access_token_is_the_fallback(gmail, monkeypatch):
    rows = {ACC: _row(provider="gmail", sub=None)}
    g = gmail(rows)
    monkeypatch.setattr(key_store_mod, "get_key_store", lambda: SimpleNamespace(
        decrypt=lambda blob: json.dumps({"access_token": ACCESS})))
    await accounts.delete_account(ACC, user=_user())
    [req] = g.requests
    assert parse_qs(req.content.decode()) == {"token": [ACCESS]}


# ── test_a_failed_revoke_still_answers_204 ──────────────────────────────────


@pytest.mark.parametrize("google,blob_ok,reason", [
    (400, True, "http_400"),
    (503, True, "http_503"),
    ("raise", True, "transport_error"),
    ("slow", True, "timeout"),
    (200, False, "unreadable_credentials"),
], ids=["google-400", "google-503", "unreachable", "slow", "unreadable-blob"])
async def test_a_failed_revoke_still_answers_204(
    gmail, monkeypatch, google, blob_ok, reason,
):
    monkeypatch.setattr(teardown, "REVOKE_TIMEOUT_S", 0.05)
    rows = {ACC: _row(provider="gmail", sub=None)}
    g = gmail(rows, google=google, blob_ok=blob_ok)
    started = time.monotonic()
    assert await accounts.delete_account(ACC, user=_user()) is None
    assert time.monotonic() - started < 5, "the revoke bound did not hold"
    assert ACC not in rows, "the mailbox was not deleted"
    assert ("warning", "email.revoke_failed",
            {"account_id": ACC, "provider": "gmail", "reason": reason}) in g.log.lines
    assert "email.disconnect.grant_revoked" not in g.log.names()
    _no_secret(g.log.lines)


async def test_a_grant_with_no_token_logs_the_reason(gmail, monkeypatch):
    rows = {ACC: _row(provider="gmail", sub=None)}
    g = gmail(rows)
    monkeypatch.setattr(key_store_mod, "get_key_store", lambda: SimpleNamespace(
        decrypt=lambda blob: json.dumps({"token_expiry": "x"})))
    assert await accounts.delete_account(ACC, user=_user()) is None
    assert g.requests == []
    assert ("warning", "email.revoke_failed",
            {"account_id": ACC, "provider": "gmail", "reason": "no_token"}) in g.log.lines


@pytest.mark.parametrize("failure", ["lock-timeout", "error"])
async def test_a_refused_delete_removes_no_filter_and_revokes_nothing(gmail, failure):
    """Review round 1. A 409 or a failed DELETE keeps the mailbox connected,
    so its filters and its grant must stay. The loop starts again."""
    rows = {ACC: _row(provider="gmail", sub=None, filters=("flt-1", "flt-2"))}
    fails = _lock_timeout_error() if failure == "lock-timeout" else RuntimeError("reset")
    g = gmail(rows, delete_fails=fails, loops=(ACC,))
    with pytest.raises((HTTPException, RuntimeError)) as err:
        await accounts.delete_account(ACC, user=_user())
    if failure == "lock-timeout":
        assert err.value.status_code == 409
    assert ACC in rows
    assert "filter_delete" not in g.ledger.kinds(), "a refused disconnect removed a filter"
    assert g.requests == [], "a refused disconnect revoked the grant"
    assert "grant_check" not in g.ledger.kinds()
    assert ("restart", 0, ACC, ROW_ORG) in g.ledger.events
    assert "email.disconnect.cache_dropped" not in g.log.names()


async def test_a_cancel_during_the_delete_still_restarts_and_removes_no_filter(gmail):
    """Review round 1, P2. No await sits between the stop and the ``try``
    that starts the loop again. A cancel during the DELETE restarts the loop,
    and no filter goes."""
    gate = asyncio.Event()
    rows = {ACC: _row(provider="gmail", sub=None, filters=("flt-1",))}
    g = gmail(rows, delete_gate=gate, loops=(ACC,))
    route = asyncio.create_task(accounts.delete_account(ACC, user=_user()))
    while "delete" not in g.ledger.kinds():
        await asyncio.sleep(0)
    kinds = g.ledger.kinds()
    assert kinds.index("stop") < kinds.index("delete")
    assert "filter_delete" not in kinds, "a network call ran between the stop and the try"
    route.cancel()
    with pytest.raises(asyncio.CancelledError):
        await route
    await asyncio.wait_for(asyncio.gather(*accounts._RESTARTS), timeout=5)
    assert ("restart", 0, ACC, ROW_ORG) in g.ledger.events
    assert "filter_delete" not in g.ledger.kinds()
    assert ACC in rows


# ── a shared grant is not revoked (review round 1, P1) ──────────────────────


async def test_the_grant_check_asks_with_the_deleted_address(gmail):
    rows = {ACC.upper(): _row(provider="gmail", sub=None)}
    rows[ACC.upper()]["email"] = "Box@Example.test"
    g = gmail(rows)
    assert await accounts.delete_account(ACC.upper(), user=_user()) is None
    check = g.ledger.first("grant_check")
    assert check[1] == 1, "the check ran outside a block"
    assert check[2] == {"email": "Box@Example.test", "exclude": ACC}
    kinds = g.ledger.kinds()
    assert kinds.index("delete") < kinds.index("grant_check") < kinds.index("google")


@pytest.mark.parametrize("shared,reason", [
    (True, "shared_grant"),
    (None, "check_failed"),
    ("raise", "check_failed"),
], ids=["another-row", "cannot-tell", "check-raises"])
async def test_a_shared_or_unknown_grant_is_not_revoked(gmail, shared, reason):
    rows = {ACC: _row(provider="gmail", sub=None, filters=("flt-1",))}
    g = gmail(rows, shared=shared)
    assert await accounts.delete_account(ACC, user=_user()) is None
    assert ACC not in rows, "the mailbox was not deleted"
    assert g.requests == [], "a shared grant was revoked at Google"
    assert ("info", "email.revoke_skipped",
            {"account_id": ACC, "provider": "gmail", "reason": reason}) in g.log.lines
    # The filters belong to this mailbox, so they still go.
    assert [e[2] for e in g.ledger.events if e[0] == "filter_delete"] == ["flt-1"]
    if shared == "raise":
        assert ("warning", "email.disconnect.grant_check_failed",
                {"account_id": ACC, "error": "RuntimeError"}) in g.log.lines
    _no_secret(g.log.lines)


async def test_an_outlook_row_asks_no_grant_check(gmail):
    rows = {ACC: _row(provider="microsoft", sub=None)}
    g = gmail(rows)
    await accounts.delete_account(ACC, user=_user())
    assert "grant_check" not in g.ledger.kinds()


# ── test_outlook_revokes_nothing ────────────────────────────────────────────


async def test_outlook_revokes_nothing(gmail):
    rows = {ACC: _row(provider="microsoft", sub="sub-g11f1", filters=("rule-1",))}
    g = gmail(rows)
    assert await accounts.delete_account(ACC, user=_user()) is None
    assert g.requests == [], "an Outlook disconnect reached Google"
    kinds = g.ledger.kinds()
    assert "filter_read" not in kinds and "filter_delete" not in kinds, (
        "the Outlook filters stay as on main (open point, §12.3.13.9)")
    assert ("graph_delete", 0, "sub-g11f1") in g.ledger.events
    assert not [n for n in g.log.names() if "revoke" in n]
    assert ACC not in rows


@pytest.mark.parametrize("provider", ["imap"])
async def test_an_imap_row_revokes_nothing(gmail, provider):
    rows = {ACC: _row(provider=provider, sub=None)}
    g = gmail(rows)
    assert await accounts.delete_account(ACC, user=_user()) is None
    assert g.requests == []


# ── the Redis keys of the mailbox ────────────────────────────────────────────


class _FakeRedis:
    """A tenant Redis double that holds wire keys and answers a SCAN."""

    def __init__(self, keys: set[str]) -> None:
        self.keys = set(keys)

    async def scan_iter(self, pattern: Any, *, count: int = 200):
        for name in sorted(self.keys):
            if fnmatch.fnmatchcase(name, pattern.value):
                yield name

    async def delete(self, *keys: Any) -> int:
        gone = 0
        for k in keys:
            if k.value in self.keys:
                self.keys.discard(k.value)
                gone += 1
        return gone


def _wire_keys(org: str, *specs: tuple[str, ...]) -> set[str]:
    with organization_scope(org):
        return {key(*spec).value for spec in specs}


async def test_the_mailbox_redis_keys_go_and_no_other(gmail, monkeypatch):
    mine = _wire_keys(
        ROW_ORG,
        ("email-brief", ACC, "2026-10-14", "abc123"),
        ("email-status-backoff", ACC, "thread-1", "msg-1"),
        ("email-llm", ACC, "2026-10-14"),
    )
    kept = _wire_keys(
        ROW_ORG,
        ("email-brief", OTHER_ACC, "2026-10-14", "abc123"),
        ("email-llm", OTHER_ACC, "2026-10-14"),
        ("email-html", "row-1"),
    ) | _wire_keys(OTHER_ORG, ("email-llm", ACC, "2026-10-14"))
    fake = _FakeRedis(mine | kept)
    rows = {ACC.upper(): _row(provider="microsoft", sub=None)}
    g = gmail(rows)
    monkeypatch.setattr(teardown, "_redis", lambda: fake)
    assert await accounts.delete_account(ACC.upper(), user=_user()) is None
    assert fake.keys == kept, "a key of the mailbox stayed, or another key went"
    assert ("info", "email.disconnect.cache_dropped",
            {"account_id": ACC, "keys": 3}) in g.log.lines


def test_the_namespaces_are_the_writers_own():
    assert teardown._mailbox_namespaces() == (
        "email-brief", "email-status-backoff", "email-llm")


async def test_a_redis_failure_still_answers_204(gmail, monkeypatch):
    class _Down:
        async def scan_iter(self, pattern: Any, *, count: int = 200):
            raise ConnectionError("redis down")
            yield  # pragma: no cover

    rows = {ACC: _row(provider="microsoft", sub=None)}
    g = gmail(rows)
    monkeypatch.setattr(teardown, "_redis", _Down)
    assert await accounts.delete_account(ACC, user=_user()) is None
    assert ("warning", "email.disconnect.cache_drop_failed",
            {"account_id": ACC, "error": "ConnectionError", "dropped": 0}) in g.log.lines


async def test_the_owner_my_day_is_bumped_with_the_row_org(gmail, monkeypatch):
    from gateway.routes.shell import cache as feed_cache

    bumped: list[tuple[Any, Any]] = []

    async def _bump(org: Any, email: Any) -> None:
        bumped.append((org, email))

    monkeypatch.setattr(feed_cache, "bump_member", _bump)
    rows = {ACC: _row(provider="microsoft", sub=None)}
    gmail(rows)
    await accounts.delete_account(ACC, user=_user())
    assert bumped == [(ROW_ORG, OWNER)], "the bump must use the org of the row"


def test_the_bounds():
    assert teardown.REVOKE_TIMEOUT_S == 5.0
    assert teardown.FILTER_REMOVAL_TIMEOUT_S == 10.0
    assert teardown.CACHE_DROP_TIMEOUT_S == 5.0
