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
* A shared grant is not revoked: when migration 263's function answers true,
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

    async def delete_subscription(self, subscription_id: str) -> int:
        """EM-G11f1b: the Graph leg lives in ``teardown.py`` now, so it
        builds its provider through the same seam as the filter leg."""
        self.ledger.events.append(("graph_delete", self.ledger.open, subscription_id))
        return 204


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
    # The path id is upper case. The account_id reader gives the canonical
    # id to the route, so the store row is keyed by it.
    rows = {ACC: _row(provider="gmail", sub=None)}
    rows[ACC]["email"] = "Box@Example.test"
    g = gmail(rows)
    assert await accounts.delete_account(ACC.upper(), user=_user()) is None
    check = g.ledger.first("grant_check")
    assert check[1] == 1, "the check ran outside a block"
    assert check[2] == {"email": "Box@Example.test", "exclude": ACC}
    kinds = g.ledger.kinds()
    assert kinds.index("delete") < kinds.index("grant_check") < kinds.index("google")


@pytest.mark.parametrize("shared,level,reason", [
    (True, "info", "shared_grant"),
    (None, "warning", "owner_blind"),
    ("raise", "warning", "check_failed"),
], ids=["another-row", "cannot-tell", "check-raises"])
async def test_a_shared_or_unknown_grant_is_not_revoked(gmail, shared, level, reason):
    rows = {ACC: _row(provider="gmail", sub=None, filters=("flt-1",))}
    g = gmail(rows, shared=shared)
    assert await accounts.delete_account(ACC, user=_user()) is None
    assert ACC not in rows, "the mailbox was not deleted"
    assert g.requests == [], "a shared grant was revoked at Google"
    assert (level, "email.revoke_skipped",
            {"account_id": ACC, "provider": "gmail", "reason": reason}) in g.log.lines
    # A NULL answer is not an error, so it writes no check-failed line.
    assert ("email.disconnect.grant_check_failed" in g.log.names()) is (shared == "raise")
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


# ── test_outlook_removes_its_rules_and_revokes_nothing ──────────────────────


async def test_outlook_removes_its_rules_and_revokes_nothing(gmail):
    """EM-G11f1b (§12.3.13.9.2, acceptance 3). The stored value of an Outlook
    row is ``microsoft``. Its plan reads the rule ids that ``senders.py``
    stored, and each rule goes after the commit, before the Graph call. The
    grant stays, because Microsoft has no revoke for one delegated grant."""
    rows = {ACC: _row(provider="microsoft", sub="sub-g11f1",
                      filters=("rule-1", "rule-2"))}
    g = gmail(rows)
    assert await accounts.delete_account(ACC, user=_user()) is None
    assert g.requests == [], "an Outlook disconnect reached Google"
    kinds = g.ledger.kinds()
    assert g.ledger.first("filter_read")[1] == 1, "the rule ids were read outside a block"
    assert kinds.index("filter_read") < kinds.index("delete")
    deletes = [e for e in g.ledger.events if e[0] == "filter_delete"]
    assert [e[2] for e in deletes] == ["rule-1", "rule-2"], "delete_filter ran not twice"
    assert all(e[1] == 0 for e in deletes), "a rule went with a block open"
    assert kinds.index("filter_delete") > kinds.index("delete")
    assert ("graph_delete", 0, "sub-g11f1") in g.ledger.events
    assert "grant_check" not in kinds and "revoke" not in kinds
    assert not [n for n in g.log.names() if "revoke" in n]
    assert ("info", "email.disconnect.filters_removed",
            {"account_id": ACC, "removed": 2, "failed": 0}) in g.log.lines
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
    rows = {ACC: _row(provider="microsoft", sub=None)}  # canonical, as above
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


# ════════════════════════════════════════════════════════════════════════════
# EM-G11f1b (§12.3.13.9.2, PR-G7b): one teardown, three paths
#
# The disconnect and the member purge call ``read_teardown_plans`` inside
# their transaction, before their delete, and ``finish_teardowns`` after the
# commit. PR-G7c adds the org purge as the third path.
# ════════════════════════════════════════════════════════════════════════════

import ast  # noqa: E402
import contextlib  # noqa: E402
from pathlib import Path  # noqa: E402

import email_ingestion.scheduler as sched  # noqa: E402
from gateway.routes import operator as operator_door  # noqa: E402
from gateway.routes.admin import members as admin_members  # noqa: E402

_REPO = Path(__file__).resolve().parents[2]
_GW = _REPO / "apps" / "services" / "gateway" / "gateway"
_ACCOUNTS = _GW / "routes" / "email" / "transport" / "accounts.py"
_MEMBERS = _GW / "routes" / "admin" / "members.py"
_OPERATOR = _GW / "routes" / "operator.py"
_TEARDOWN = _GW / "routes" / "email" / "teardown.py"


def _function(source: str, name: str) -> ast.AsyncFunctionDef:
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"no async function {name!r}")


def _called(node: ast.AST, name: str) -> list[ast.Call]:
    out = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            func = sub.func
            hit = (isinstance(func, ast.Name) and func.id == name) or (
                isinstance(func, ast.Attribute) and func.attr == name)
            if hit:
                out.append(sub)
    return out


def _session_blocks(fn: ast.AST) -> list[tuple[int, int]]:
    """The line spans of each ``async with _tenant_session(...)`` block."""
    spans = []
    for sub in ast.walk(fn):
        if isinstance(sub, ast.AsyncWith) and any(
                _called(item.context_expr, "_tenant_session") for item in sub.items):
            spans.append((sub.lineno, sub.end_lineno or sub.lineno))
    return sorted(spans)


def _inside(line: int, spans: list[tuple[int, int]]) -> bool:
    return any(a <= line <= b for a, b in spans)


def _door_violations(source: str, fn_name: str) -> list[str]:
    """A door: the shared body inside its session block, and the teardown
    after the block exits, with the plans of the body and a stop."""
    fn = _function(source, fn_name)
    blocks = _session_blocks(fn)
    problems = []
    bodies = _called(fn, "purge_member_rows")
    finishes = _called(fn, "finish_teardowns")
    if not bodies:
        return [f"{fn_name} calls no purge_member_rows"]
    if not finishes:
        return [f"{fn_name} calls no finish_teardowns"]
    for call in bodies:
        if not _inside(call.lineno, blocks):
            problems.append(f"{fn_name}: purge_member_rows runs outside the session block")
    body_block = next((b for b in blocks if _inside(bodies[0].lineno, [b])), None)
    for call in finishes:
        if _inside(call.lineno, blocks):
            problems.append(f"{fn_name}: finish_teardowns runs inside a session block")
        elif body_block and call.lineno < body_block[1]:
            problems.append(f"{fn_name}: finish_teardowns runs before the commit")
        if not (len(call.args) >= 2 and isinstance(call.args[1], ast.Name)
                and call.args[1].id == "plans"):
            problems.append(f"{fn_name}: finish_teardowns does not get the plans")
        stop = {k.arg: k.value for k in call.keywords}.get("stop_loops")
        if not (isinstance(stop, ast.Constant) and stop.value is True):
            problems.append(f"{fn_name}: the purge does not stop the sync loops")
        # Fix round 1: the record of the purge and the Console mirror go
        # BEFORE the teardown wait, which a stop of the process can cut.
        records = [c.lineno for c in _called(fn, "_mirror_removal_to_console")]
        records += [c.lineno for c in ast.walk(fn) if isinstance(c, ast.Call)
                    and isinstance(c.func, ast.Attribute)
                    and isinstance(c.func.value, ast.Name) and c.func.value.id == "_log"
                    and c.args and isinstance(c.args[0], ast.Constant)
                    and "purged" in str(c.args[0].value)]
        if not records:
            problems.append(f"{fn_name}: the door logs no purge")
        elif call.lineno < max(records):
            problems.append(f"{fn_name}: finish_teardowns runs before the log or the mirror")
    return problems


def _body_violations(source: str) -> list[str]:
    """The shared purge body: the plans and the purge rows before the first
    delete, and no session and no teardown of its own."""
    fn = _function(source, "purge_member_rows")
    reads = _called(fn, "read_teardown_plans")
    if not reads:
        return ["purge_member_rows reads no teardown plans"]
    rows_at = [n.lineno for n in ast.walk(fn)
               if isinstance(n, ast.Name) and n.id == "RECORD_MEMBER_PURGES_SQL"]
    deletes_at = [n.lineno for n in ast.walk(fn)
                  if isinstance(n, ast.Attribute) and n.attr == "delete_sql"]
    problems = []
    if not rows_at:
        problems.append("purge_member_rows writes no purge rows")
    if not deletes_at:
        problems.append("purge_member_rows deletes nothing")
    if rows_at and deletes_at and min(rows_at) > min(deletes_at):
        problems.append("the purge rows come after the first delete")
    if deletes_at and reads[0].lineno > min(deletes_at):
        problems.append("the plans are read after the first delete")
    if _called(fn, "finish_teardowns") or _called(fn, "_tenant_session"):
        problems.append("the shared body tears down or opens a session itself")
    return problems


def _disconnect_violations(source: str) -> list[str]:
    fn = _function(source, "delete_account")
    blocks = _session_blocks(fn)
    reads = _called(fn, "read_teardown_plans")
    finishes = _called(fn, "finish_teardowns")
    deletes = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Constant)
               and isinstance(n.value, str) and "DELETE FROM email_accounts" in n.value]
    if not reads or not finishes or not deletes:
        return ["delete_account lost a call of the teardown or its DELETE"]
    delete_block = next(b for b in blocks if _inside(deletes[0], [b]))
    problems = []
    if not _inside(reads[0].lineno, blocks) or reads[0].lineno > delete_block[0]:
        problems.append("the disconnect reads its plan outside a block or after the DELETE")
    for call in finishes:
        if _inside(call.lineno, blocks) or call.lineno < delete_block[1]:
            problems.append("the disconnect tears down before its DELETE commits")
    return problems


_OFFBOARD = _REPO / "packages" / "acb_auth" / "acb_auth" / "offboard.py"

#: The calls that open a session or a transaction of their own.
_SESSION_OPENERS = ("_tenant_session", "tenant_session", "get_db", "_get_db",
                    "_detached_session", "get_session_factory", "begin")


def _callback_violations(operator_src: str, name: str) -> list[str]:
    """The callback of the org purge: the org lock FIRST, then the plans and
    the detached purge rows, in the session of the purge, with no session and
    no teardown of its own. The record call is the one SECURITY DEFINER
    function of migration 276, which also carries the open purge rows."""
    fn = _function(operator_src, name)
    reads = _called(fn, "read_teardown_plans")
    executes = sorted(_called(fn, "execute"), key=lambda c: c.lineno)
    rows_at = [n.lineno for n in ast.walk(fn)
               if isinstance(n, ast.Name) and n.id == "RECORD_DETACHED_PURGES_SQL"]
    problems = []
    if not reads:
        problems.append("the org purge callback reads no teardown plans")
    if not rows_at:
        problems.append("the org purge callback writes no detached purge rows")
    first = executes[0] if executes else None
    lock_first = first is not None and any(
        isinstance(n, ast.Name) and n.id == "ORG_LOCK_SQL" for n in ast.walk(first))
    if not lock_first or (reads and first.lineno > reads[0].lineno):
        problems.append("the org purge callback does not lock the org row first")
    if any(_called(fn, opener) for opener in _SESSION_OPENERS) or any(
            isinstance(n, ast.AsyncWith) for n in ast.walk(fn)):
        problems.append("the org purge callback opens a session of its own")
    if _called(fn, "finish_teardowns"):
        problems.append("the org purge callback tears down itself")
    return problems


def _offboard_violations(offboard_src: str) -> list[str]:
    """``purge_tenant_organization`` runs ``before_delete`` in its tenant
    session, before its first delete."""
    fn = _function(offboard_src, "purge_tenant_organization")
    blocks = sorted(
        (sub.lineno, sub.end_lineno or sub.lineno) for sub in ast.walk(fn)
        if isinstance(sub, ast.AsyncWith) and any(
            _called(item.context_expr, "tenant_session") for item in sub.items))
    calls = _called(fn, "before_delete")
    deletes = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Name)
               and n.id in ("_CRM_DELETES", "_DELETE_ORG_SQL")]
    if not calls:
        return ["the org purge never runs before_delete"]
    if not deletes:
        return ["the org purge deletes nothing"]
    problems = []
    if not _inside(calls[0].lineno, blocks):
        problems.append("before_delete runs outside the session of the org purge")
    if calls[0].lineno > min(deletes):
        problems.append("before_delete runs after a delete of the org purge")
    return problems


def _org_door_violations(operator_src: str, offboard_src: str) -> list[str]:
    """The org purge (PR-G7c): the door passes its callback to the purge, and
    tears down after the purge returns, with the plans, a stop and a log."""
    fn = _function(operator_src, "purge_organization")
    purges = _called(fn, "purge_tenant_organization")
    finishes = _called(fn, "finish_teardowns")
    if not purges:
        return ["purge_organization calls no purge_tenant_organization"]
    if not finishes:
        return ["purge_organization calls no finish_teardowns"]
    problems = []
    callback = {k.arg: k.value for k in purges[0].keywords}.get("before_delete")
    if not isinstance(callback, ast.Name):
        problems.append("purge_organization passes no before_delete callback")
    else:
        problems += _callback_violations(operator_src, callback.id)
    if any(isinstance(n, ast.AsyncWith) for n in ast.walk(fn)):
        problems.append("purge_organization opens a session block of its own")
    records = [c.lineno for c in ast.walk(fn) if isinstance(c, ast.Call)
               and isinstance(c.func, ast.Attribute)
               and isinstance(c.func.value, ast.Name) and c.func.value.id == "_log"
               and c.args and isinstance(c.args[0], ast.Constant)
               and "purged" in str(c.args[0].value)]
    for call in finishes:
        if call.lineno < purges[0].end_lineno:
            problems.append("purge_organization: finish_teardowns runs before the commit")
        if not (len(call.args) >= 2 and isinstance(call.args[1], ast.Name)
                and call.args[1].id == "plans"):
            problems.append("purge_organization: finish_teardowns does not get the plans")
        stop = {k.arg: k.value for k in call.keywords}.get("stop_loops")
        if not (isinstance(stop, ast.Constant) and stop.value is True):
            problems.append("purge_organization: the purge does not stop the sync loops")
        if not records:
            problems.append("purge_organization: the door logs no purge")
        elif call.lineno < max(records):
            problems.append("purge_organization: finish_teardowns runs before the log")
    return problems + _offboard_violations(offboard_src)


def _three_paths(accounts_src: str, members_src: str, operator_src: str,
                 offboard_src: str | None = None) -> list[str]:
    offboard_src = _read(_OFFBOARD) if offboard_src is None else offboard_src
    return (_disconnect_violations(accounts_src)
            + _body_violations(members_src)
            + _door_violations(members_src, "purge_member")
            + _door_violations(operator_src, "remove_organization_member")
            + _org_door_violations(operator_src, offboard_src))


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_three_paths_call_one_teardown():
    """R7 fence ``email-teardown-paths``: the disconnect, the member purge
    with its two doors, and the org purge with its callback (PR-G7c)."""
    assert _three_paths(_read(_ACCOUNTS), _read(_MEMBERS), _read(_OPERATOR)) == []


def test_the_org_path_check_can_fail():
    """Each mutation of the org purge turns the fence red."""
    accounts_src, members_src = _read(_ACCOUNTS), _read(_MEMBERS)
    operator_src, offboard_src = _read(_OPERATOR), _read(_OFFBOARD)

    def problems(op: str = operator_src, off: str = offboard_src) -> list[str]:
        return _three_paths(accounts_src, members_src, op, off)

    # The org purge skips the teardown. Only the org door changes: the member
    # door holds the same call.
    head, sep, tail = operator_src.partition("async def purge_organization(")
    door_src, sep2, rest = tail.partition('@router.delete("/organizations/{slug}/members/')
    assert sep and sep2 and "await finish_teardowns(" in door_src
    skipped = head + sep + door_src.replace(
        "await finish_teardowns(", "await _no_teardown(") + sep2 + rest
    assert "purge_organization calls no finish_teardowns" in problems(skipped)
    # The door passes no callback.
    passed = "slug=slug, before_delete=plan_org_teardown)"
    assert passed in operator_src
    assert "purge_organization passes no before_delete callback" in problems(
        operator_src.replace(passed, "slug=slug)"))
    # The callback writes its rows in a session of its own.
    write = "    recorded = int((await db.execute(\n"
    assert write in operator_src
    own = operator_src.replace(write, (
        "    async with _tenant_session(org_id) as own:\n"
        "        await own.execute(text(RECORD_DETACHED_PURGES_SQL), {})\n" + write))
    assert "the org purge callback opens a session of its own" in problems(own)
    # The callback forgets to lock the org row (fix round 1, item 4).
    lock = "    await db.execute(text(ORG_LOCK_SQL), {\"org\": org_id})\n"
    assert lock in operator_src
    assert ("the org purge callback does not lock the org row first"
            in problems(operator_src.replace(lock, "")))
    # The purge runs the callback after its deletes.
    run = ("        if before_delete is not None:\n"
           "            answer = await before_delete(session, org_id)\n")
    after = "        deleted[\"organization\"] = result.rowcount or 0\n"
    assert run in offboard_src and after in offboard_src
    late = offboard_src.replace(run, "").replace(after, after + run)
    assert "before_delete runs after a delete of the org purge" in problems(off=late)
    # The purge runs the callback in a session of its own, outside its block.
    outside = offboard_src.replace(run, "").replace(
        "    async with tenant_session(org_id) as session:\n",
        "    if before_delete is not None:\n"
        "        answer = await before_delete(None, org_id)\n"
        "    async with tenant_session(org_id) as session:\n")
    assert "before_delete runs outside the session of the org purge" in problems(off=outside)


def test_the_path_check_can_fail():
    members_src, operator_src = _read(_MEMBERS), _read(_OPERATOR)
    accounts_src = _read(_ACCOUNTS)
    # The member purge skips the teardown.
    skipped = operator_src.replace("mailboxes = await finish_teardowns(",
                                   "mailboxes = await _no_teardown(")
    assert skipped != operator_src
    assert any("calls no finish_teardowns" in p
               for p in _three_paths(accounts_src, members_src, skipped))
    # The admin door tears down inside its transaction.
    anchor = "            db, org_id, member, actor_email=admin.email,\n        )\n"
    assert anchor in members_src
    inside = members_src.replace(anchor, anchor + (
        "        await finish_teardowns(org_id, plans, open_session=None,\n"
        "                               stop_loops=True, bump_owners=False)\n"))
    assert any("inside a session block" in p
               for p in _three_paths(accounts_src, inside, operator_src))
    # The purge rows move after the deletes.
    write = '    await db.execute(text(RECORD_MEMBER_PURGES_SQL), {"email": addr})\n'
    audit = "    # Before the commit, on its own connection"
    assert write in members_src and audit in members_src
    late = members_src.replace(write, "").replace(audit, write + audit)
    assert "the purge rows come after the first delete" in _three_paths(
        accounts_src, late, operator_src)
    # The disconnect tears down inside its DELETE block.
    finish = "    await finish_teardowns(\n        org, [plan.with_deleted_row(deleted)]"
    assert finish in accounts_src
    early = accounts_src.replace(finish, "    await finish_teardowns(\n        org, [plan]")
    early = early.replace(
        "            deleted = result.fetchone()\n",
        "            deleted = result.fetchone()\n"
        "            await finish_teardowns(org, [plan], open_session=None,\n"
        "                                   stop_loops=False, bump_owners=True)\n")
    assert "the disconnect tears down before its DELETE commits" in _three_paths(
        early, members_src, operator_src)


#: The legs. Only ``teardown.py`` calls them, through ``finish_teardowns``.
_LEGS = ("remove_metorite_filters", "revoke_grant", "drop_mailbox_cache",
         "drop_graph_subscription", "_teardown_one", "_run_teardowns")

#: The two calls and the stop, and the only modules that may call each.
_ALLOWED = {
    "read_teardown_plans": {"accounts.py", "members.py", "operator.py"},
    "finish_teardowns": {"accounts.py", "members.py", "operator.py"},
    # The disconnect stops its loop before its DELETE (EM-T4f).
    "stop_sync": {"accounts.py"},
}


def _python_files() -> list[Path]:
    out = []
    for root in ("apps", "packages", "scripts"):
        for path in (_REPO / root).rglob("*.py"):
            if set(path.parts) & {".venv", "node_modules", "__pycache__"}:
                continue
            out.append(path)
    return out


def _leg_callers() -> list[str]:
    names = set(_LEGS) | set(_ALLOWED)
    problems = []
    for path in _python_files():
        if path == _TEARDOWN:
            continue
        source = path.read_text(encoding="utf-8", errors="replace")
        if not any(name in source for name in names):
            continue
        tree = ast.parse(source)
        for name in sorted(names):
            if not _called(tree, name):
                continue
            if name in _LEGS or path.name not in _ALLOWED[name]:
                problems.append(f"{path.relative_to(_REPO).as_posix()} calls {name}")
    return problems


def test_no_other_caller_reaches_a_leg():
    assert _leg_callers() == []


#: Each statement in the tree that deletes a mailbox, with the path it is.
#: A new hit fails the fence until it joins a path or this map with a reason.
_KNOWN_MAILBOX_DELETES = {
    ("apps/services/gateway/gateway/routes/email/transport/accounts.py",
     "DELETE FROM email_accounts"): "the disconnect, delete_account",
    ("apps/services/gateway/gateway/routes/admin/members.py",
     "_PersonRows(email_accounts)"): "the member purge, _PURGE_DELETES",
    ("packages/acb_auth/acb_auth/offboard.py", "DELETE FROM organization"):
        "the org purge. It cascades each mailbox of the org. Its callback "
        "(operator.py::plan_org_teardown) reads the plans and writes the "
        "detached purge rows, and the door tears down after it (PR-G7c)",
    ("scripts/demo_seed/seed_demo.py", "DELETE FROM email_accounts"):
        "a demo seed on a dev box, which removes its own seeded mailbox",
}


def _mailbox_deletes() -> set[tuple[str, str]]:
    found = set()
    for path in _python_files():
        source = path.read_text(encoding="utf-8", errors="replace")
        rel = path.relative_to(_REPO).as_posix()
        if "DELETE FROM email_accounts" in source:
            found.add((rel, "DELETE FROM email_accounts"))
        if "DELETE FROM organization " in source or 'DELETE FROM organization"' in source:
            found.add((rel, "DELETE FROM organization"))
        for line in source.splitlines():
            if "_PersonRows(" in line and '"email_accounts"' in line:
                found.add((rel, "_PersonRows(email_accounts)"))
    return found


def test_each_delete_of_a_mailbox_is_a_known_path():
    assert _mailbox_deletes() == set(_KNOWN_MAILBOX_DELETES)


def test_the_three_clauses_name_one_set_of_mailboxes():
    """The plans, the purge rows and the delete of the member purge use ONE
    clause, so no mailbox goes without its plan or its purge row."""
    from gateway.routes.email.memory_purge import RECORD_MEMBER_PURGES_SQL

    [rows] = [r for r in admin_members._PURGE_DELETES if r.table == "email_accounts"]
    assert rows.where == teardown.MEMBER_MAILBOXES_WHERE
    assert teardown.MEMBER_MAILBOXES_WHERE in teardown.MEMBER_PLANS_SQL
    assert teardown.MEMBER_MAILBOXES_WHERE in RECORD_MEMBER_PURGES_SQL
    assert "FOR UPDATE" in teardown.MEMBER_PLANS_SQL


# ── the operator door, with a recording database ────────────────────────────


DOOR_ORG = "22222222-3333-4444-5555-666666666666"
GONE = "Gone@Purge.Test"


class _DoorResult:
    def __init__(self, rows: list[Any] | None = None, scalar: Any = None,
                 rowcount: int = 0) -> None:
        self._rows = rows or []
        self._scalar = scalar
        self.rowcount = rowcount

    def fetchall(self) -> list[Any]:
        return self._rows

    def fetchone(self) -> Any:
        return self._rows[0] if self._rows else None

    def scalar(self) -> Any:
        return self._scalar


class _DoorDB:
    """Answers the statements of the member purge and records each one."""

    def __init__(self, events: list[tuple[Any, ...]], mailboxes: list[dict[str, Any]],
                 owners: int) -> None:
        self.events = events
        self.mailboxes = mailboxes
        self.owners = owners

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None):
        sql = " ".join(str(stmt).split())
        p = params or {}
        self.events.append(("sql", sql.split(" ")[0], sql[:60]))
        if "r.slug = 'owner'" in sql:
            return _DoorResult(scalar=self.owners)
        if sql.startswith("SELECT id, organization_id, provider"):
            return _DoorResult(rows=[SimpleNamespace(
                id=m["id"], organization_id=DOOR_ORG, provider=m["provider"],
                credentials_encrypted="blob-door", user_id=GONE.lower(),
                email_address=m.get("email", "box@purge.test"),
                webhook_subscription_id=m.get("sub"),
            ) for m in self.mailboxes][: p["limit"]])
        if sql.startswith("SELECT count(*) FROM email_accounts"):
            return _DoorResult(scalar=len(self.mailboxes))
        if "FROM email_newsletters n" in sql:
            return _DoorResult(rows=[SimpleNamespace(account_id=m["id"], fid=f)
                                     for m in self.mailboxes
                                     for f in m.get("filters", ())])
        if sql.startswith("SELECT public.email_gmail_grant_shared"):
            self.events.append(("grant_check",))
            return _DoorResult(scalar=False)
        if sql.startswith("SELECT public.record_detached_purges("):
            return _DoorResult(scalar=len(self.mailboxes))
        if sql.startswith("DELETE FROM organization"):
            return _DoorResult(rowcount=1)
        if sql.startswith("SELECT count(*)"):
            return _DoorResult(scalar=0)
        return _DoorResult()


@pytest.fixture()
def door(monkeypatch):  # noqa: C901
    """The REAL operator door and the REAL shared body, over ``_DoorDB``.
    The provider, Google, the scheduler and Redis are recorders."""
    from acb_auth import offboard

    def _setup(  # noqa: C901
            mailboxes: list[dict[str, Any]], *, owners: int = 1,
               slow_stop: float = 0.0, slow_revoke: float = 0.0,
               google: int = 200):
        events: list[tuple[Any, ...]] = []
        state = {"open": 0}
        db = _DoorDB(events, mailboxes, owners)

        @contextlib.asynccontextmanager
        async def _session(org: str):
            assert org == DOOR_ORG
            state["open"] += 1
            events.append(("open",))
            try:
                yield db
            except BaseException:
                events.append(("rollback",))
                raise
            else:
                events.append(("commit",))
            finally:
                state["open"] -= 1

        async def _org_for(slug: str) -> str:
            return DOOR_ORG

        async def _find(db_: Any, org: str, email: str) -> dict[str, Any]:
            return {"id": "uid-gone", "email": GONE}

        async def _noop(*_a: Any, **_k: Any) -> None:
            return None

        running = {"now": 0, "max": 0}

        async def _stop(account_id: str) -> None:
            running["now"] += 1
            running["max"] = max(running["max"], running["now"])
            events.append(("stop", state["open"], account_id))
            try:
                if slow_stop:
                    await asyncio.sleep(slow_stop)
            except asyncio.CancelledError:
                # As the real ``remove_account_sync`` does: it swallows the
                # cancel of its wait for the loop task (scheduler.py).
                events.append(("stop_cancel_swallowed", account_id))
            finally:
                running["now"] -= 1

        class _Provider:
            async def delete_filter(self, filter_id: str) -> None:
                events.append(("filter_delete", state["open"], filter_id))

            async def delete_subscription(self, sub: str) -> int:
                events.append(("graph_delete", state["open"], sub))
                return 204

        def _build(name: str, creds: Any) -> Any:
            events.append(("build", state["open"], name))
            return _Provider()

        async def _revoke(token: str) -> int:
            if slow_revoke:
                await asyncio.sleep(slow_revoke)
            events.append(("revoke", state["open"]))
            return google

        class _DoorLog:
            def info(self, event: str, **kw: Any) -> None:
                events.append(("door_log", event))

            warning = error = info

        log = _Log()
        monkeypatch.setenv("GATEWAY_OPERATOR_TOKEN", "op-token")
        monkeypatch.setattr(offboard, "organization_id_for_slug", _org_for)
        monkeypatch.setattr(offboard, "tenant_session", _session)
        monkeypatch.setattr(offboard, "_log", _DoorLog())
        monkeypatch.setattr(operator_door, "_tenant_session", _session)
        monkeypatch.setattr(operator_door, "find_member", _find)
        monkeypatch.setattr(operator_door, "purge_identity_shadow", _noop)
        monkeypatch.setattr(operator_door, "invalidate_for", lambda *_a: None)
        monkeypatch.setattr(operator_door, "_log", _DoorLog())
        monkeypatch.setattr(admin_members, "record_admin_change", lambda *a, **k: None)
        monkeypatch.setattr(sched, "remove_account_sync", _stop)
        monkeypatch.setattr(sched, "get_scheduler_status", lambda: {"accounts": []})
        monkeypatch.setattr(teardown, "_instantiate_provider", _build)
        monkeypatch.setattr(teardown, "_post_revoke", _revoke)
        monkeypatch.setattr(teardown, "_redis", lambda: _FakeRedis(set()))
        monkeypatch.setattr(teardown, "_log", log)
        monkeypatch.setattr(key_store_mod, "get_key_store", lambda: SimpleNamespace(
            decrypt=lambda blob: json.dumps({"refresh_token": REFRESH})))

        async def _purge() -> Any:
            return await operator_door.remove_organization_member(
                "acme", GONE, confirm=GONE, mode="purge", x_operator_token="op-token")

        async def _purge_org() -> Any:
            return await operator_door.purge_organization(
                "acme", confirm="acme", x_operator_token="op-token")

        return SimpleNamespace(events=events, log=log, purge=_purge,
                               purge_org=_purge_org, running=running)

    return _setup


def _mailbox(i: int, provider: str = "gmail", **kw: Any) -> dict[str, Any]:
    return {"id": f"00000000-0000-4000-8000-{i:012d}", "provider": provider, **kw}


def _kinds(events: list[tuple[Any, ...]]) -> list[str]:
    return [e[0] for e in events]


async def test_the_member_purge_tears_down_after_the_commit(door):
    d = door([_mailbox(1, "gmail", filters=("flt-1",)),
              _mailbox(2, "microsoft", filters=("rule-1", "rule-2"), sub="sub-2")])
    out = await d.purge()
    kinds = _kinds(d.events)
    commit = kinds.index("commit")
    assert out["mailboxes"] == {"planned": 2, "finished": 2, "failed": 0,
                                "continuing": 0}
    # The plans and the purge rows, then the deletes, all in the transaction.
    sqls = [e[2] for e in d.events if e[0] == "sql"]
    plan_at = next(i for i, q in enumerate(sqls) if q.startswith("SELECT id, organization_id"))
    rows_at = next(i for i, q in enumerate(sqls) if q.startswith("INSERT INTO email_mailbox"))
    first_delete = next(i for i, q in enumerate(sqls) if q.startswith("DELETE FROM"))
    assert plan_at < rows_at < first_delete
    # Each leg after the commit, with no block open, and each loop stopped.
    for kind in ("stop", "filter_delete", "graph_delete", "revoke", "grant_check"):
        assert kind in kinds, f"no {kind}"
        assert kinds.index(kind) > commit, f"{kind} ran before the commit"
    assert all(e[1] == 0 for e in d.events
               if e[0] in ("stop", "filter_delete", "graph_delete", "revoke"))
    assert sorted(e[2] for e in d.events if e[0] == "stop") == [
        _mailbox(1)["id"], _mailbox(2)["id"]]
    assert sorted(e[2] for e in d.events if e[0] == "filter_delete") == [
        "flt-1", "rule-1", "rule-2"]
    assert [e for e in d.events if e[0] == "revoke"] == [("revoke", 0)], (
        "the Outlook mailbox revoked, or the Gmail one did not")
    assert "email.teardown.finished" in d.log.names()
    _no_secret(d.log.lines)


async def test_a_refused_purge_reaches_no_provider(door):
    """Acceptance 2. ``assert_owner_survives`` refuses, so the transaction
    rolls back, and no provider, no stop and no plan read runs."""
    d = door([_mailbox(1, "gmail", filters=("flt-1",))], owners=0)
    with pytest.raises(HTTPException) as err:
        await d.purge()
    assert err.value.status_code == 409
    kinds = _kinds(d.events)
    assert "rollback" in kinds and "commit" not in kinds
    for kind in ("stop", "build", "filter_delete", "revoke", "graph_delete",
                 "grant_check"):
        assert kind not in kinds, f"a refused purge ran {kind}"
    assert not [e for e in d.events if e[0] == "sql" and "email_accounts" in e[2]]
    assert "email.teardown.started" not in d.log.names()


async def test_the_mailbox_cap(door):
    """Acceptance 4. 1001 mailboxes answer 409 with the count, and the purge
    deletes nothing and writes no purge row."""
    d = door([_mailbox(i, "imap") for i in range(1, 1002)])
    with pytest.raises(HTTPException) as err:
        await d.purge()
    assert err.value.status_code == 409
    assert "1001" in err.value.detail
    sqls = [e[2] for e in d.events if e[0] == "sql"]
    assert not [q for q in sqls if q.startswith(("DELETE", "INSERT"))], (
        "a purge over the cap deleted or wrote")
    assert "rollback" in _kinds(d.events) and "stop" not in _kinds(d.events)


async def test_the_mailbox_cap_reads_one_row_above_it():
    seen: list[dict[str, Any]] = []

    class _DB:
        async def execute(self, stmt: Any, params: dict[str, Any] | None = None):
            seen.append(dict(params or {}))
            return _DoorResult(rows=[])

    assert await teardown.read_teardown_plans(
        _DB(), teardown.MemberScope("a@b.test")) == []
    assert seen == [{"email": "a@b.test", "limit": 1001}]


async def test_the_door_waits_a_bounded_time(door, monkeypatch):
    """Acceptance 5. Ten mailboxes and a slow leg. The door answers at the
    bound with the count that continues. The held task finishes the rest,
    four at a time, and logs the end line."""
    monkeypatch.setattr(teardown, "TEARDOWN_WAIT_S", 0.2)
    d = door([_mailbox(i, "imap") for i in range(1, 11)], slow_stop=0.5)
    # An order, not a wall-clock budget (test_no_new_clock_flakes): the door
    # answers while the held task still runs its stops.
    out = await asyncio.wait_for(d.purge(), timeout=30)
    counts = out["mailboxes"]
    assert counts["planned"] == 10 and counts["continuing"] > 0
    assert counts["finished"] + counts["failed"] + counts["continuing"] == 10
    assert ("warning", "email.teardown.continuing",
            {"org": DOOR_ORG, "mailboxes": counts["continuing"],
             "finished": counts["finished"], "failed": 0}) in d.log.lines
    assert teardown.live_teardown_count() == 1, "the drain does not see the held task"
    await asyncio.wait_for(asyncio.gather(*teardown._TEARDOWNS), timeout=10)
    assert teardown.live_teardown_count() == 0
    assert len([e for e in d.events if e[0] == "stop"]) == 10
    assert d.running["max"] <= teardown.TEARDOWN_CONCURRENCY
    assert ("info", "email.teardown.finished",
            {"org": DOOR_ORG, "mailboxes": 10, "finished": 10,
             "failed": 0}) in d.log.lines


async def test_one_slow_leg_is_bounded(door, monkeypatch):
    """A leg past ``MAILBOX_TEARDOWN_BOUND_S`` counts as failed, never as
    finished."""
    monkeypatch.setattr(teardown, "MAILBOX_TEARDOWN_BOUND_S", 0.05)
    d = door([_mailbox(1, "gmail")], slow_revoke=5)
    out = await d.purge()
    assert out["mailboxes"] == {"planned": 1, "finished": 0, "failed": 1,
                                "continuing": 0}
    assert ("warning", "email.teardown.mailbox_failed",
            {"account_id": _mailbox(1)["id"], "error": "TimeoutError"}) in d.log.lines


async def test_a_slow_stop_keeps_the_time_of_the_revoke(door, monkeypatch):
    """Fix round 1, item 3. The stop has its own bound, before the bound of
    the legs. The fake stop swallows the cancel of its wait, as the real
    ``remove_account_sync`` does. A stop that takes most of the leg bound
    must not cancel the revoke after it."""
    monkeypatch.setattr(teardown, "STOP_SYNC_BOUND_S", 1.0)
    monkeypatch.setattr(teardown, "MAILBOX_TEARDOWN_BOUND_S", 0.4)
    d = door([_mailbox(1, "gmail")], slow_stop=0.3, slow_revoke=0.2)
    out = await d.purge()
    assert ("revoke", 0) in d.events, "the slow stop used the time of the revoke"
    assert out["mailboxes"] == {"planned": 1, "finished": 1, "failed": 0,
                                "continuing": 0}


async def test_a_stop_past_its_bound_still_lets_the_legs_run(door, monkeypatch):
    """The real stop swallows the cancel, so its bound raises nothing. The
    teardown asks the bound, logs the line, and runs the legs."""
    monkeypatch.setattr(teardown, "STOP_SYNC_BOUND_S", 0.05)
    d = door([_mailbox(1, "gmail")], slow_stop=5)
    # A 30 s hang guard. The event below proves the bound cut the stop.
    out = await asyncio.wait_for(d.purge(), timeout=30)
    assert ("stop_cancel_swallowed", _mailbox(1)["id"]) in d.events
    assert ("warning", "email.teardown.stop_timed_out",
            {"account_id": _mailbox(1)["id"]}) in d.log.lines
    assert ("revoke", 0) in d.events
    assert out["mailboxes"]["finished"] == 1


async def test_a_failed_leg_counts_as_failed(door):
    """Fix round 1, item 5. ``finished`` means success."""
    d = door([_mailbox(1, "gmail"), _mailbox(2, "imap")], google=400)
    out = await d.purge()
    assert out["mailboxes"] == {"planned": 2, "finished": 1, "failed": 1,
                                "continuing": 0}


async def test_the_door_logs_before_it_waits(door):
    """Fix round 1, item 1. The purge record goes BEFORE the teardown wait,
    so a stop of the process in the wait cannot lose it."""
    d = door([_mailbox(1, "imap")])
    await d.purge()
    kinds = [e[1] if e[0] == "door_log" else e[0] for e in d.events]
    assert kinds.index("operator_member_purged") < kinds.index("stop"), (
        "the door waited for the teardown before it logged the purge")


async def test_a_cancelled_teardown_names_what_it_did_not_finish(door, monkeypatch):
    """Fix round 1, item 2. A stop of the process cancels the held task. The
    line names each mailbox that did not finish, and no ledger keeps a
    token."""
    monkeypatch.setattr(teardown, "TEARDOWN_WAIT_S", 0.05)
    d = door([_mailbox(1, "imap"), _mailbox(2, "gmail")], slow_revoke=5)
    out = await d.purge()
    assert out["mailboxes"]["continuing"] >= 1
    [held] = list(teardown._TEARDOWNS)
    held.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await held
    [line] = [ln for ln in d.log.lines if ln[1] == "email.teardown.cancelled"]
    assert line[0] == "warning"
    assert line[2]["unfinished"] == [_mailbox(2)["id"]]
    _no_secret(d.log.lines)


def test_the_teardown_bounds():
    assert teardown.TEARDOWN_MAX_MAILBOXES == 1000
    assert teardown.TEARDOWN_CONCURRENCY == 4
    assert teardown.MAILBOX_TEARDOWN_BOUND_S == 30.0
    assert teardown.TEARDOWN_WAIT_S == 60.0
    assert teardown.STOP_SYNC_BOUND_S == 15.0
    assert teardown.SUBSCRIPTION_DELETE_TIMEOUT_S == 5.0


# ── the org purge, with a recording database (EM-G11f1c, PR-G7c) ────────────


async def test_the_org_purge_tears_down_after_the_commit(door):
    """The org door. The callback reads the plans and writes the detached
    purge rows inside the purge, before its first delete. Each leg runs after
    the commit, with no block open. The plans never reach the receipt."""
    d = door([_mailbox(1, "gmail", filters=("flt-1",)),
              _mailbox(2, "microsoft", filters=("rule-1",), sub="sub-2")])
    out = await d.purge_org()
    kinds = _kinds(d.events)
    commit = kinds.index("commit")
    assert out["deleted"]["organization"] == 1
    assert out["mailboxes"] == {"planned": 2, "finished": 2, "failed": 0,
                                "continuing": 0}
    assert "before_delete" not in out, "the plans reached the receipt"
    assert "blob-door" not in json.dumps(out, default=str)
    sqls = [e[2] for e in d.events if e[0] == "sql"]
    lock_at = next(i for i, q in enumerate(sqls)
                   if q.startswith("SELECT 1 FROM organization"))
    plan_at = next(i for i, q in enumerate(sqls)
                   if q.startswith("SELECT id, organization_id"))
    rows_at = next(i for i, q in enumerate(sqls)
                   if q.startswith("SELECT public.record_detached_purges"))
    first_delete = next(i for i, q in enumerate(sqls) if q.startswith("DELETE FROM"))
    org_delete = next(i for i, q in enumerate(sqls)
                      if q.startswith("DELETE FROM organization"))
    assert lock_at == 0, "the callback did not lock the org row first"
    assert lock_at < plan_at < rows_at < first_delete <= org_delete
    # Fix round 1, item 3: the receipt names the table that outlives the org.
    assert any(k.startswith("email_detached_purges") for k in out["kept"])
    for kind in ("stop", "filter_delete", "graph_delete", "revoke", "grant_check"):
        assert kind in kinds, f"no {kind}"
        assert kinds.index(kind) > commit, f"{kind} ran before the commit"
    assert all(e[1] == 0 for e in d.events
               if e[0] in ("stop", "filter_delete", "graph_delete", "revoke"))
    assert sorted(e[2] for e in d.events if e[0] == "filter_delete") == [
        "flt-1", "rule-1"]
    assert [e for e in d.events if e[0] == "revoke"] == [("revoke", 0)]
    door_logs = [e[1] for e in d.events if e[0] == "door_log"]
    assert kinds.index("stop") > kinds.index("door_log"), (
        "the door waited for the teardown before it logged the purge")
    assert "operator_org_purged" in door_logs
    _no_secret(d.log.lines)


async def test_the_org_mailbox_cap(door):
    """Acceptance 4 for the org purge. 1001 mailboxes answer 409 with the
    count, not a 502. The purge deletes nothing and writes no row."""
    d = door([_mailbox(i, "imap") for i in range(1, 1002)])
    with pytest.raises(HTTPException) as err:
        await d.purge_org()
    assert err.value.status_code == 409
    assert "1001" in err.value.detail and "organization" in err.value.detail
    sqls = [e[2] for e in d.events if e[0] == "sql"]
    assert not [q for q in sqls if q.startswith(
        ("DELETE", "INSERT", "SELECT public.record_detached_purges"))], (
        "an org purge over the cap deleted or wrote")
    assert "rollback" in _kinds(d.events) and "stop" not in _kinds(d.events)


async def test_an_org_with_no_mailbox_answers_zero_counts(door):
    d = door([])
    out = await d.purge_org()
    assert out["mailboxes"] == {"planned": 0, "finished": 0, "failed": 0,
                                "continuing": 0}
    assert "stop" not in _kinds(d.events)


async def test_the_org_door_waits_a_bounded_time(door, monkeypatch):
    """Acceptance 5 for the org purge: the same bounds as the member door."""
    monkeypatch.setattr(teardown, "TEARDOWN_WAIT_S", 0.2)
    d = door([_mailbox(i, "imap") for i in range(1, 11)], slow_stop=0.5)
    out = await asyncio.wait_for(d.purge_org(), timeout=30)
    counts = out["mailboxes"]
    assert counts["planned"] == 10 and counts["continuing"] > 0
    assert teardown.live_teardown_count() == 1, "the drain does not see the held task"
    await asyncio.wait_for(asyncio.gather(*teardown._TEARDOWNS), timeout=10)
    assert len([e for e in d.events if e[0] == "stop"]) == 10
    assert d.running["max"] <= teardown.TEARDOWN_CONCURRENCY


def test_the_three_org_clauses_name_one_set_of_mailboxes():
    """The plans, the detached rows and the cascade name one org. The rows
    come from the SECURITY DEFINER function of migration 276, which reads the
    mailboxes of ``p_org`` only."""
    from gateway.routes.email.memory_purge import RECORD_DETACHED_PURGES_SQL

    assert teardown.ORG_MAILBOXES_WHERE in teardown.ORG_PLANS_SQL
    assert teardown.ORG_MAILBOXES_WHERE in teardown.ORG_FILTER_IDS_SQL
    assert RECORD_DETACHED_PURGES_SQL == (
        "SELECT public.record_detached_purges(CAST(:org AS uuid))")
    [migration] = [p for p in (_REPO / "infra" / "postgres").glob("[0-9]*_*.sql")
                   if "FUNCTION public.record_detached_purges" in p.read_text(
                       encoding="utf-8")]
    body = migration.read_text(encoding="utf-8")
    assert "WHERE a.organization_id = p_org" in body
    assert "AND p.organization_id = p_org" in body
    assert "FOR UPDATE" in teardown.ORG_PLANS_SQL
