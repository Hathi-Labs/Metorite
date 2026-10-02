"""EM-T4c — a 401 during a sync refreshes once, and the request goes again.

Spec: ``project-docs/specs/email_app_master_plan.md`` §10.4.6, EM-T4c.

The Gmail and the Outlook client carried the bearer as a header that
``_get_client`` wrote once. A token that expired during a sync then failed
each later request. The sweep swallows a failed folder, so the sync wrote a
"full snapshot" with whole folders missing.

``RefreshingBearer`` (``providers/base.py``) now sets the bearer on each
request. On a 401 it refreshes once under ``provider._refresh_lock`` and sends
the same request once more.

These tests use ``httpx.MockTransport``, because an ``AsyncMock`` client skips
the auth flow. Each provider client, the ``authenticate`` probe and the token
refresh all reach ONE fake service, so the real refresh code runs.

**Hermetic.**

* One 401 refreshes once and sends again with the new token.
* Two 401s at the same time cause one refresh.
* A second 401 after the refresh goes back to the caller, with no third try.
* A refresh that fails raises.
* A JSON body goes out whole on the second try.
* An Outlook sweep, and a Gmail sweep, whose third page gets a 401 return
  each page.
* A fence: neither ``_get_client`` sets an ``Authorization`` header.
* The error path of ``_sync_account`` writes the refreshed credentials.

**Fix round 1.**

* A 401 that a refresh cannot cure costs one refresh for each token, not
  one for each request. A failed refresh is not tried again for the same
  token. A success with that token clears it.
* One ``_sync_account`` tick with a 401 on each request posts to the token
  endpoint twice. The ``authenticate`` probe does not refresh a token that
  a refresh on the same instance made.
* ``list_folders`` does not take the 400 of a failed refresh for a rejected
  ``$select``.
* A refresh on a body fetch of phase (e) reaches the database after a
  successful sync. A refresh before phase (d) is written once.

**R8.** Against the two-org catalog of ``test_h3_rls_promotion_rehearsal``,
as the non-privileged role, the ``credentials_encrypted`` column of the
account holds the new refresh token in two cases: the error path after a
refresh, and a successful sync whose phase (e) refreshed.

Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_email_provider_401_retry.py -v -rs
"""
from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest

pytest.importorskip("sqlalchemy")

import email_ingestion.scheduler as sched
from acb_common.db import clear_tenant, release_tenant
from email_ingestion.providers.app_credentials import MICROSOFT_OAUTH_BASE, OAuthApp
from email_ingestion.providers.base import RefreshingBearer
from email_ingestion.providers.gmail import GmailProvider
from email_ingestion.providers.outlook import GRAPH_API_BASE, OutlookProvider
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope

# ``promoted`` and ``app_engine`` are used by name for fixture injection, so
# the import is load-bearing even though it reads as unused.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

# ── the fake service ────────────────────────────────────────────────────────

Route = Callable[[httpx.Request], httpx.Response]


class _FakeService:
    """One fake mail API and its OAuth token endpoint.

    The access token ``at-1`` works until it expires. A refresh with the
    CURRENT refresh token mints ``at-<n>`` and ``r-<n>``, and each older
    access token stops working. A stale refresh token gets a 400, as
    Microsoft answers for a rotated one.
    """

    def __init__(self, token_url: str, routes: dict[str, Route]) -> None:
        self.token_url = token_url
        self.routes = routes
        self.n = 1
        self.valid = {"at-1"}
        self.refreshes = 0
        #: (path, query params, bearer, body) for each API request.
        self.seen: list[tuple[str, dict[str, str], str, bytes]] = []
        #: When this matches a request, ``at-1`` expires before the answer.
        self.expire_on: Callable[[httpx.Request], bool] | None = None
        #: Each request that carries ``at-1`` waits here before its answer.
        self.barrier: asyncio.Barrier | None = None
        self.always_401 = False
        #: Each path here answers 401 to each token.
        self.refuse_paths: set[str] = set()
        self.refuse_refresh = False
        #: Each POST to the token endpoint, refused or not.
        self.token_posts = 0

    async def handle(self, request: httpx.Request) -> httpx.Response:
        if str(request.url) == self.token_url:
            self.token_posts += 1
            return await self._token(request)
        bearer = request.headers.get("authorization", "").removeprefix("Bearer ")
        self.seen.append((request.url.path, dict(request.url.params), bearer,
                          request.content))
        if self.expire_on is not None and self.expire_on(request):
            self.valid.discard("at-1")
        if self.barrier is not None and bearer == "at-1":
            await self.barrier.wait()
        if (self.always_401 or request.url.path in self.refuse_paths
                or bearer not in self.valid):
            return httpx.Response(401, json={"error": "InvalidAuthenticationToken"})
        route = self.routes.get(request.url.path)
        if route is None:
            return httpx.Response(404, json={"error": "no route"})
        return route(request)

    async def _token(self, request: httpx.Request) -> httpx.Response:
        form = parse_qs(request.content.decode())
        # Let a second request run while this refresh is in flight.
        await asyncio.sleep(0)
        if self.refuse_refresh or form.get("refresh_token") != [f"r-{self.n}"]:
            return httpx.Response(400, json={"error": "invalid_grant"})
        self.refreshes += 1
        self.n += 1
        self.valid = {f"at-{self.n}"}
        return httpx.Response(200, json={"access_token": f"at-{self.n}",
                                         "refresh_token": f"r-{self.n}"})

    def calls(self, path: str) -> list[tuple[dict[str, str], str]]:
        """The params and the bearer of each request to *path*, in order."""
        return [(params, bearer) for p, params, bearer, _ in self.seen if p == path]


def _ok(body: dict[str, Any] | None = None) -> Route:
    return lambda _request: httpx.Response(200, json=body or {})


@dataclass(frozen=True)
class _Kind:
    """One provider, its token endpoint and its API prefix."""

    name: str
    make: Callable[..., Any]
    token_url: str
    prefix: str
    probe: str


_OUTLOOK = _Kind("outlook", OutlookProvider, f"{MICROSOFT_OAUTH_BASE}/token",
                 "/v1.0", "/v1.0/me")
_GMAIL = _Kind("gmail", GmailProvider, "https://oauth2.googleapis.com/token",
               "/gmail/v1", "/gmail/v1/users/me/profile")
KINDS = pytest.mark.parametrize("kind", [_OUTLOOK, _GMAIL], ids=lambda k: k.name)


def _provider(kind: _Kind, *, refresh_token: str | None = "r-1") -> Any:
    creds = {"access_token": "at-1"}
    if refresh_token:
        creds["refresh_token"] = refresh_token
    return kind.make(creds, app=OAuthApp(client_id="cid", client_secret="secret"))


class _NetworkLikeTransport(httpx.AsyncBaseTransport):
    """Reads a body the way a network transport does.

    It iterates the stream and never calls ``aread``. ``MockTransport``
    reads the body into the request, and that hides a stream that cannot
    go out a second time."""

    def __init__(self, service: _FakeService) -> None:
        self.service = service

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        body = b"".join([chunk async for chunk in request.stream])
        return await self.service.handle(httpx.Request(
            request.method, request.url, headers=request.headers, content=body))


@pytest.fixture()
def wire(monkeypatch):
    """Send each ``httpx.AsyncClient`` that a provider builds to *service*.

    The provider client, the ``authenticate`` probe and the token refresh
    each build their own client, so all three reach the one fake."""
    real = httpx.AsyncClient

    def _wire(service: _FakeService, *, network_like: bool = False) -> _FakeService:
        transport: httpx.AsyncBaseTransport = (
            _NetworkLikeTransport(service) if network_like
            else httpx.MockTransport(service.handle))

        def _client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
            kwargs["transport"] = transport
            return real(*args, **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", _client)
        return service

    return _wire


def _service(kind: _Kind, routes: dict[str, Route] | None = None) -> _FakeService:
    return _FakeService(kind.token_url, {kind.probe: _ok(), **(routes or {})})


# ── the auth flow ───────────────────────────────────────────────────────────


@KINDS
async def test_a_401_refreshes_once_and_sends_again_with_the_new_token(kind, wire):
    path = f"{kind.prefix}/x"
    service = wire(_service(kind, {path: _ok({"ok": True})}))
    service.expire_on = lambda r: r.url.path == path
    p = _provider(kind)

    client = await p._get_client()
    resp = await client.get("/x")

    assert resp.status_code == 200
    assert [bearer for _, bearer in service.calls(path)] == ["at-1", "at-2"]
    assert service.refreshes == 1
    # The refresh marks the credentials dirty, so the caller persists them.
    assert p.credentials_dirty() is True
    assert p.export_credentials()["refresh_token"] == "r-2"
    assert p.export_credentials()["access_token"] == "at-2"


@KINDS
async def test_two_401s_at_the_same_time_cause_one_refresh(kind, wire):
    path = f"{kind.prefix}/x"
    service = wire(_service(kind, {path: _ok({"ok": True})}))
    p = _provider(kind)
    client = await p._get_client()
    # Both requests carry at-1 and wait for each other. Then at-1 expires,
    # and each gets its 401 before either one refreshes.
    service.barrier = asyncio.Barrier(2)
    service.valid.discard("at-1")

    first, second = await asyncio.wait_for(
        asyncio.gather(client.get("/x"), client.get("/x")), timeout=5)

    assert (first.status_code, second.status_code) == (200, 200)
    assert service.refreshes == 1, "each 401 refreshed on its own"
    assert sorted(bearer for _, bearer in service.calls(path)) == [
        "at-1", "at-1", "at-2", "at-2"]


@KINDS
async def test_a_second_401_after_the_refresh_goes_back_to_the_caller(kind, wire):
    path = f"{kind.prefix}/x"
    service = wire(_service(kind, {path: _ok({"ok": True})}))
    p = _provider(kind)
    client = await p._get_client()
    service.always_401 = True

    resp = await client.get("/x")

    assert resp.status_code == 401
    assert [bearer for _, bearer in service.calls(path)] == ["at-1", "at-2"], (
        "the transport saw a third try, or no second one"
    )
    assert service.refreshes == 1


@KINDS
async def test_a_failed_refresh_raises_and_sends_nothing_more(kind, wire):
    path = f"{kind.prefix}/x"
    service = wire(_service(kind, {path: _ok({"ok": True})}))
    p = _provider(kind)
    client = await p._get_client()
    service.valid.discard("at-1")
    service.refuse_refresh = True

    with pytest.raises(httpx.HTTPStatusError):
        await client.get("/x")

    assert [bearer for _, bearer in service.calls(path)] == ["at-1"]
    assert service.refreshes == 0
    assert p.credentials_dirty() is False


# ── a 401 that a refresh cannot cure (fix round 1, notes 1 and 3) ───────────


@KINDS
async def test_a_401_that_a_refresh_does_not_cure_costs_one_refresh(kind, wire):
    """Each request gets a 401, and the token endpoint works. The first 401
    refreshes, and its retry gets a 401 too. So a refresh does not help this
    token, and each later 401 goes back to the caller with no refresh."""
    path = f"{kind.prefix}/x"
    service = wire(_service(kind, {path: _ok({"ok": True})}))
    p = _provider(kind)
    client = await p._get_client()
    service.always_401 = True

    statuses = [(await client.get("/x")).status_code for _ in range(5)]

    assert statuses == [401] * 5
    assert service.token_posts == 1, "each 401 posted to the token endpoint"
    assert [bearer for _, bearer in service.calls(path)] == [
        "at-1", "at-2", "at-2", "at-2", "at-2", "at-2"]


@KINDS
async def test_a_failed_refresh_is_not_tried_again_for_the_same_token(kind, wire):
    """A refresh that failed for a token is not tried again for it. The
    first request raises the refresh error. Each later 401 with the same
    token goes back to the caller, with no second post and no retry."""
    path = f"{kind.prefix}/x"
    service = wire(_service(kind, {path: _ok({"ok": True})}))
    p = _provider(kind)
    client = await p._get_client()
    service.valid.discard("at-1")
    service.refuse_refresh = True

    with pytest.raises(httpx.HTTPStatusError):
        await client.get("/x")
    later = [(await client.get("/x")).status_code for _ in range(3)]

    assert later == [401] * 3
    assert service.token_posts == 1
    assert [bearer for _, bearer in service.calls(path)] == ["at-1"] * 4


@KINDS
async def test_a_request_with_no_token_can_still_refresh(kind, wire):
    """``None`` is never a remembered token. A client that sends a request
    before any access token exists still refreshes on its 401."""
    path = f"{kind.prefix}/x"
    service = wire(_service(kind, {path: _ok({"ok": True})}))
    p = kind.make({"refresh_token": "r-1"},
                  app=OAuthApp(client_id="cid", client_secret="secret"))
    client = httpx.AsyncClient(auth=RefreshingBearer(p))
    try:
        resp = await client.get(f"https://mail.test{path}")
    finally:
        await client.aclose()

    assert resp.status_code == 200
    assert [bearer for _, bearer in service.calls(path)] == ["None", "at-2"]


@KINDS
async def test_two_401s_at_the_same_time_and_a_failed_refresh_post_once(
    kind, wire,
):
    """Two requests get a 401 at the same time, and the refresh fails. The
    request that took the lock raises the refresh error. The other one gets
    no new token, so it gets its 401 back with no post and no second try."""
    path = f"{kind.prefix}/x"
    service = wire(_service(kind, {path: _ok({"ok": True})}))
    p = _provider(kind)
    client = await p._get_client()
    service.barrier = asyncio.Barrier(2)
    service.valid.discard("at-1")
    service.refuse_refresh = True

    results = await asyncio.wait_for(asyncio.gather(
        client.get("/x"), client.get("/x"), return_exceptions=True), timeout=5)

    raised = [r for r in results if isinstance(r, httpx.HTTPStatusError)]
    answered = [r.status_code for r in results if isinstance(r, httpx.Response)]
    assert (len(raised), answered) == (1, [401])
    assert service.token_posts == 1
    assert [bearer for _, bearer in service.calls(path)] == ["at-1", "at-1"], (
        "the request that waited sent the dead token a second time"
    )


@KINDS
async def test_a_token_that_works_again_can_be_refreshed_again(kind, wire):
    """A 401 on ONE path does not stop the refresh for good. The new token
    works on another path, so the flow forgets the refusal. When that token
    expires later, the next 401 refreshes again."""
    bad, ok = f"{kind.prefix}/bad", f"{kind.prefix}/ok"
    service = wire(_service(kind, {bad: _ok(), ok: _ok()}))
    service.refuse_paths = {bad}
    p = _provider(kind)
    client = await p._get_client()

    assert (await client.get("/bad")).status_code == 401
    assert (await client.get("/ok")).status_code == 200
    service.valid.discard("at-2")
    assert (await client.get("/ok")).status_code == 200

    assert service.token_posts == 2
    assert [bearer for _, bearer in service.calls(ok)] == ["at-2", "at-2", "at-3"]


@KINDS
async def test_a_tick_where_each_request_gets_a_401_posts_twice(
    kind, monkeypatch, wire,
):
    """One ``_sync_account`` tick, with a 401 on each mail request and a
    working token endpoint. The ``authenticate`` probe refreshes once, and
    the flow refreshes once. Before fix round 1 each request posted again,
    and a probe saw 32 posts in one tick. Phase (e) has three bodies to
    fetch, so the tick sends more requests than it may post."""
    from acb_llm import key_store

    service = wire(_service(kind))
    service.always_401 = True
    provider = _provider(kind)
    log: list = []

    async def _no_op(*_a, **_k):
        return None

    monkeypatch.setattr(sched, "tenant_session", _logged_sessions(log, [
        SimpleNamespace(id=f"row-{i}", provider_message_id=f"m{i}")
        for i in (1, 2, 3)]))
    monkeypatch.setattr(sched, "upsert_message", _no_op)
    monkeypatch.setattr(sched, "run_label_learn_hook", _no_op)
    monkeypatch.setattr(key_store, "get_key_store", lambda: _Store())
    monkeypatch.setattr(sched, "build_provider", lambda name, creds: provider)

    await sched._sync_account("acc-1", organization_id="org-1")

    assert service.token_posts == 2, (
        f"{service.token_posts} posts to the token endpoint in one tick")
    # The bound is not vacuous: the tick sent at least four mail requests
    # after the second post, and each one of them got its 401.
    assert len(service.seen) >= 6


async def test_a_failed_refresh_in_list_folders_is_not_a_rejected_select(wire):
    """``list_folders`` drops ``$select`` and tries again on a 400. The 400
    of a failed refresh comes from the token endpoint, not from Graph, so
    it goes back to the caller with no second try."""
    service = wire(_service(_OUTLOOK, {"/v1.0/me/mailFolders": _ok({"value": []})}))
    p = _provider(_OUTLOOK)
    await p._get_client()
    service.valid.discard("at-1")
    service.refuse_refresh = True

    with pytest.raises(httpx.HTTPStatusError) as err:
        await p.list_folders()

    assert err.value.response.status_code == 400
    assert str(err.value.request.url) == _OUTLOOK.token_url
    assert service.token_posts == 1
    assert len(service.calls("/v1.0/me/mailFolders")) == 1


@KINDS
async def test_a_401_with_no_refresh_token_goes_back_to_the_caller(kind, wire):
    path = f"{kind.prefix}/x"
    service = wire(_service(kind, {path: _ok({"ok": True})}))
    p = _provider(kind, refresh_token=None)
    client = await p._get_client()
    service.valid.discard("at-1")

    resp = await client.get("/x")

    assert resp.status_code == 401
    assert [bearer for _, bearer in service.calls(path)] == ["at-1"]
    assert service.refreshes == 0


@KINDS
async def test_a_json_body_goes_out_whole_on_the_second_try(kind, wire):
    """Item 4: each body on these clients is JSON or a query, so httpx can
    send it again."""
    path = f"{kind.prefix}/x"
    service = wire(_service(kind, {path: _ok({"ok": True})}))
    service.expire_on = lambda r: r.url.path == path
    p = _provider(kind)
    client = await p._get_client()

    resp = await client.post("/x", json={"subject": "hello", "n": 1})

    assert resp.status_code == 200
    bodies = [body for p_, _, _, body in service.seen if p_ == path]
    assert len(bodies) == 2
    assert bodies[0] == bodies[1]
    assert json.loads(bodies[1]) == {"subject": "hello", "n": 1}


@KINDS
async def test_a_streamed_body_goes_out_whole_on_the_second_try(kind, wire):
    """No caller streams a body today. The flow reads a body into memory
    before the first try anyway, so a stream added later cannot reach the
    second try empty. This test reads bodies the way a network transport
    does, because ``MockTransport`` would read the stream for the flow."""
    path = f"{kind.prefix}/x"
    service = wire(_service(kind, {path: _ok({"ok": True})}), network_like=True)
    service.expire_on = lambda r: r.url.path == path
    p = _provider(kind)
    client = await p._get_client()

    async def _chunks():
        yield b'{"part": '
        yield b'"one"}'

    resp = await client.post("/x", content=_chunks())

    assert resp.status_code == 200
    bodies = [body for p_, _, _, body in service.seen if p_ == path]
    assert bodies == [b'{"part": "one"}', b'{"part": "one"}']


# ── a sweep whose third page gets a 401 ─────────────────────────────────────


def _graph_message(i: int) -> dict[str, Any]:
    return {"id": f"m{i}", "subject": f"s{i}", "conversationId": f"c{i}",
            "receivedDateTime": "2026-10-01T00:00:00Z"}


def _outlook_inbox(request: httpx.Request) -> httpx.Response:
    page = int(request.url.params.get("page", "1"))
    body: dict[str, Any] = {"value": [_graph_message(page)]}
    if page < 4:
        body["@odata.nextLink"] = (
            f"{GRAPH_API_BASE}/me/mailFolders/inbox/messages?page={page + 1}")
    return httpx.Response(200, json=body)


async def test_an_outlook_sweep_whose_third_page_gets_a_401_returns_each_page(
    wire,
):
    """The deep sweep of the inbox pages through four ``@odata.nextLink``
    pages. The token expires when page 3 is asked for. Before EM-T4c the
    sweep dropped the inbox, and each folder after it, and still said
    ``full_snapshot``."""
    routes: dict[str, Route] = {
        "/v1.0/me/mailFolders": _ok({"value": []}),
        "/v1.0/me/mailFolders/inbox/messages": _outlook_inbox,
    }
    for folder in ("sentitems", "drafts", "archive", "junkemail", "deleteditems"):
        routes[f"/v1.0/me/mailFolders/{folder}/messages"] = _ok({"value": []})
    service = wire(_service(_OUTLOOK, routes))
    service.expire_on = lambda r: r.url.params.get("page") == "3"
    p = _provider(_OUTLOOK)

    res = await p.sync_messages(
        deep=True, since=datetime.now(UTC) - timedelta(days=30))

    assert [m.provider_message_id for m in res.messages] == ["m1", "m2", "m3", "m4"]
    assert res.full_snapshot is True
    assert service.refreshes == 1
    pages = [(params.get("page"), bearer) for params, bearer
             in service.calls("/v1.0/me/mailFolders/inbox/messages")]
    assert pages == [(None, "at-1"), ("2", "at-1"), ("3", "at-1"),
                     ("3", "at-2"), ("4", "at-2")]
    # The folders after the inbox ran with the new token.
    assert service.calls("/v1.0/me/mailFolders/sentitems/messages")[0][1] == "at-2"
    assert p.export_credentials()["refresh_token"] == "r-2"


def _gmail_list(request: httpx.Request) -> httpx.Response:
    if request.url.params.get("labelIds") != "INBOX":
        return httpx.Response(200, json={})
    token = request.url.params.get("pageToken")
    page = int(token[1:]) if token else 1
    body: dict[str, Any] = {"messages": [{"id": f"g{page}"}]}
    if page < 4:
        body["nextPageToken"] = f"p{page + 1}"
    return httpx.Response(200, json=body)


def _gmail_message(request: httpx.Request) -> httpx.Response:
    mid = request.url.path.rsplit("/", 1)[-1]
    return httpx.Response(200, json={
        "id": mid, "threadId": f"t-{mid}", "labelIds": ["INBOX"],
        "snippet": "", "internalDate": "1790000000000",
        "payload": {"headers": [{"name": "Subject", "value": mid}], "body": {}},
    })


async def test_a_gmail_sweep_whose_third_page_gets_a_401_returns_each_page(wire):
    """The deep sweep of INBOX pages through four ``nextPageToken`` pages.
    The token expires when page 3 is asked for."""
    routes: dict[str, Route] = {
        "/gmail/v1/users/me/labels": _ok({"labels": []}),
        "/gmail/v1/users/me/messages": _gmail_list,
    }
    for i in (1, 2, 3, 4):
        routes[f"/gmail/v1/users/me/messages/g{i}"] = _gmail_message
    service = wire(_service(_GMAIL, routes))
    service.expire_on = lambda r: r.url.params.get("pageToken") == "p3"
    p = _provider(_GMAIL)

    res = await p.sync_messages(
        deep=True, since=datetime.now(UTC) - timedelta(days=30))

    assert [m.provider_message_id for m in res.messages] == ["g1", "g2", "g3", "g4"]
    assert service.refreshes == 1
    inbox = [(params.get("pageToken"), bearer) for params, bearer
             in service.calls("/gmail/v1/users/me/messages")
             if params.get("labelIds") == "INBOX"]
    assert inbox == [(None, "at-1"), ("p2", "at-1"), ("p3", "at-1"),
                     ("p3", "at-2"), ("p4", "at-2")]
    assert p.export_credentials()["refresh_token"] == "r-2"


# ── the fence: no Authorization header on the client ────────────────────────


def _header_problems(client: httpx.AsyncClient, provider: Any) -> list[str]:
    """What is wrong with how *client* carries the bearer of *provider*."""
    problems = []
    if "authorization" in client.headers:
        problems.append("the client carries a fixed Authorization header")
    auth = client.auth
    if not isinstance(auth, RefreshingBearer) or auth._provider is not provider:
        problems.append("the client does not use RefreshingBearer(provider)")
    return problems


@KINDS
async def test_neither_get_client_sets_an_authorization_header(kind, wire):
    """R7 fence ``email-provider-bearer-per-request``. A fixed header holds
    the token of the moment the client was built, and a 401 retry cannot
    change it."""
    wire(_service(kind))
    p = _provider(kind)
    client = await p._get_client()
    assert _header_problems(client, p) == []
    # The Content-Type default stays on the client.
    assert client.headers["content-type"] == "application/json"


async def test_the_header_fence_is_not_vacuous():
    p = _provider(_OUTLOOK)
    fixed = httpx.AsyncClient(headers={"Authorization": "Bearer at-1"})
    other = httpx.AsyncClient(auth=RefreshingBearer(_provider(_GMAIL)))
    try:
        assert _header_problems(fixed, p) == [
            "the client carries a fixed Authorization header",
            "the client does not use RefreshingBearer(provider)",
        ]
        assert _header_problems(other, p) == [
            "the client does not use RefreshingBearer(provider)"]
    finally:
        await fixed.aclose()
        await other.aclose()


# ── the error path of _sync_account keeps the new refresh token ─────────────


class _Store:
    def decrypt(self, raw: str) -> str:
        return json.dumps({"access_token": "at-1", "refresh_token": "r-1"})

    def encrypt(self, raw: str) -> str:
        return f"enc:{raw}"


def _outlook_sync_routes() -> dict[str, Route]:
    """A recurring Outlook poll: two inbox pages, every other folder empty."""
    def _inbox(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", "1"))
        body: dict[str, Any] = {"value": [_graph_message(page)]}
        if page < 2:
            body["@odata.nextLink"] = (
                f"{GRAPH_API_BASE}/me/mailFolders/inbox/messages?page=2")
        return httpx.Response(200, json=body)

    routes: dict[str, Route] = {
        "/v1.0/me/mailFolders": _ok({"value": []}),
        "/v1.0/me/mailFolders/inbox/messages": _inbox,
    }
    for folder in ("sentitems", "drafts", "archive", "junkemail", "deleteditems"):
        routes[f"/v1.0/me/mailFolders/{folder}/messages"] = _ok({"value": []})
    return routes


def _refreshing_outlook(wire) -> tuple[_FakeService, OutlookProvider]:
    """A real Outlook provider whose token expires on inbox page 2."""
    service = wire(_service(_OUTLOOK, _outlook_sync_routes()))
    service.expire_on = lambda r: r.url.params.get("page") == "2"
    return service, _provider(_OUTLOOK)


def _full_graph_message(request: httpx.Request) -> httpx.Response:
    mid = request.url.path.rsplit("/", 1)[-1]
    return httpx.Response(200, json={
        **_graph_message(int(mid.removeprefix("m"))),
        "body": {"contentType": "text", "content": f"the body of {mid}"}})


def _outlook_refreshing_in_phase_e(wire) -> tuple[_FakeService, OutlookProvider]:
    """A real Outlook provider whose token expires on the first body fetch of
    phase (e), after phase (d) has written the account row."""
    routes = _outlook_sync_routes()
    for i in (1, 2):
        routes[f"/v1.0/me/messages/m{i}"] = _full_graph_message
    service = wire(_service(_OUTLOOK, routes))
    service.expire_on = lambda r: r.url.path.startswith("/v1.0/me/messages/")
    return service, _provider(_OUTLOOK)


#: The candidate read of phase (e).
_BODY_CANDIDATES = "(body_text IS NULL OR body_text = '')"


class _Res:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def fetchone(self):
        return SimpleNamespace(
            id="log-1", provider="microsoft", credentials_encrypted="old",
            last_history_id=None, sync_interval_secs=300,
            initial_sync_done=True, categories=[])

    def fetchall(self):
        return list(self._rows)

    def scalar(self):
        return None


class _LogDb:
    def __init__(self, block: int, log: list, body_rows: list) -> None:
        self.block = block
        self.log = log
        self.body_rows = body_rows

    async def execute(self, stmt, params=None, *_a, **_k):
        sql = " ".join(str(stmt).split())
        self.log.append((self.block, sql, dict(params or {})))
        return _Res(self.body_rows if _BODY_CANDIDATES in sql else [])


def _logged_sessions(log: list, body_rows: list | None = None):
    state = {"opens": 0}

    @asynccontextmanager
    async def _ts(org=None):
        state["opens"] += 1
        yield _LogDb(state["opens"], log, body_rows or [])

    return _ts


def _credential_writes(log: list) -> list[tuple[int, dict]]:
    return [(block, json.loads(params["creds"].removeprefix("enc:")))
            for block, sql, params in log if "SET credentials_encrypted" in sql]


def _error_blocks(log: list) -> list[int]:
    return [block for block, sql, _ in log if "sync_status = 'error'" in sql]


async def test_a_refresh_during_a_sync_then_a_failure_writes_the_new_token(
    monkeypatch, wire,
):
    """Item 6. The token expires on inbox page 2, so the provider refreshes
    during ``sync_messages``. Phase (c) then fails. The error path writes
    the new tokens in its own block, beside the error status."""
    from acb_llm import key_store

    service, provider = _refreshing_outlook(wire)
    log: list = []

    async def _upsert_fails(db, account_id, msg):
        raise RuntimeError("phase (c) failed")

    monkeypatch.setattr(sched, "tenant_session", _logged_sessions(log))
    monkeypatch.setattr(sched, "upsert_message", _upsert_fails)
    monkeypatch.setattr(key_store, "get_key_store", lambda: _Store())
    monkeypatch.setattr(sched, "build_provider", lambda name, creds: provider)

    res = await sched._sync_account("acc-1", organization_id="org-1")

    assert res == {"error": "phase (c) failed"}
    assert service.refreshes == 1
    [error_block] = _error_blocks(log)
    writes = _credential_writes(log)
    assert writes == [(error_block, {"access_token": "at-2",
                                     "refresh_token": "r-2"})], (
        "the error path lost the refreshed tokens"
    )


async def test_a_failed_sync_with_no_refresh_writes_no_credentials(monkeypatch):
    """The error path writes the credentials only when they changed."""
    from acb_llm import key_store

    class _Refuses:
        async def authenticate(self) -> bool:
            return False

        def credentials_dirty(self) -> bool:
            return False

        def export_credentials(self) -> dict:
            return {"access_token": "at-1", "refresh_token": "r-1"}

    log: list = []
    monkeypatch.setattr(sched, "tenant_session", _logged_sessions(log))
    monkeypatch.setattr(key_store, "get_key_store", lambda: _Store())
    monkeypatch.setattr(sched, "build_provider", lambda name, creds: _Refuses())

    res = await sched._sync_account("acc-1", organization_id="org-1")

    assert res == {"error": "Provider authentication failed"}
    assert len(_error_blocks(log)) == 1
    assert _credential_writes(log) == []


async def test_a_refresh_in_phase_e_is_written_after_a_successful_sync(
    monkeypatch, wire,
):
    """Fix round 1, P2. The token expires on the first body fetch of phase
    (e). Phase (d) has already written the account row, with no token to
    write. The sync succeeds, and a short block after phase (e) writes the
    new tokens."""
    from acb_llm import key_store

    service, provider = _outlook_refreshing_in_phase_e(wire)
    log: list = []

    async def _no_op(*_a, **_k):
        return None

    monkeypatch.setattr(sched, "tenant_session", _logged_sessions(
        log, [SimpleNamespace(id="row-1", provider_message_id="m1")]))
    monkeypatch.setattr(sched, "upsert_message", _no_op)
    monkeypatch.setattr(sched, "run_label_learn_hook", _no_op)
    monkeypatch.setattr(key_store, "get_key_store", lambda: _Store())
    monkeypatch.setattr(sched, "build_provider", lambda name, creds: provider)

    res = await sched._sync_account("acc-1", organization_id="org-1")

    assert res == {"synced": 2, "history_id": None}
    assert service.refreshes == 1
    assert _error_blocks(log) == []
    [body_block] = [block for block, sql, _ in log if "SET body_text" in sql]
    assert _credential_writes(log) == [
        (body_block + 1, {"access_token": "at-2", "refresh_token": "r-2"})], (
        "a successful sync left the tokens of phase (e) out of the database"
    )


async def test_a_sync_with_no_refresh_writes_no_credentials(monkeypatch, wire):
    """The write after phase (e) runs only when the tokens changed after
    phase (d) wrote them."""
    from acb_llm import key_store

    service = wire(_service(_OUTLOOK, _outlook_sync_routes()))
    provider = _provider(_OUTLOOK)
    log: list = []

    async def _no_op(*_a, **_k):
        return None

    monkeypatch.setattr(sched, "tenant_session", _logged_sessions(log))
    monkeypatch.setattr(sched, "upsert_message", _no_op)
    monkeypatch.setattr(sched, "run_label_learn_hook", _no_op)
    monkeypatch.setattr(key_store, "get_key_store", lambda: _Store())
    monkeypatch.setattr(sched, "build_provider", lambda name, creds: provider)

    res = await sched._sync_account("acc-1", organization_id="org-1")

    assert res == {"synced": 2, "history_id": None}
    assert service.token_posts == 0
    assert _credential_writes(log) == []


async def test_a_refresh_before_phase_d_is_written_once(monkeypatch, wire):
    """A refresh during ``sync_messages`` is written by phase (d). The write
    after phase (e) sees no change since then, so it writes nothing more."""
    from acb_llm import key_store

    service, provider = _refreshing_outlook(wire)
    log: list = []

    async def _no_op(*_a, **_k):
        return None

    monkeypatch.setattr(sched, "tenant_session", _logged_sessions(log))
    monkeypatch.setattr(sched, "upsert_message", _no_op)
    monkeypatch.setattr(sched, "run_label_learn_hook", _no_op)
    monkeypatch.setattr(key_store, "get_key_store", lambda: _Store())
    monkeypatch.setattr(sched, "build_provider", lambda name, creds: provider)

    res = await sched._sync_account("acc-1", organization_id="org-1")

    assert res == {"synced": 2, "history_id": None}
    assert service.refreshes == 1
    [phase_d] = [block for block, sql, _ in log if "sync_status = 'idle'" in sql]
    assert _credential_writes(log) == [
        (phase_d, {"access_token": "at-2", "refresh_token": "r-2"})]


# ── R8: the refreshed tokens reach the database under FORCE RLS ─────────────


def _purge_account(admin_engine, account_id: str) -> None:
    with admin_engine.begin() as c:
        c.execute(text("DELETE FROM email_sync_log WHERE account_id = "
                       "CAST(:a AS uuid)"), {"a": account_id})
        c.execute(text("DELETE FROM email_accounts WHERE id = "
                       "CAST(:a AS uuid)"), {"a": account_id})


@_DB_GATE
class TestRefreshedTokensReachTheDatabase:

    async def test_a_refresh_in_phase_e_reaches_credentials_encrypted(
        self, promoted, app_engine, monkeypatch, wire,  # noqa: F811
    ):
        """Fix round 1, P2. Phase (c) writes two messages with no body.
        Phase (e) fetches both bodies, and the token expires on the first
        fetch. After a successful sync the column holds the new token."""
        from acb_llm import key_store

        p = promoted
        with p.admin_engine.begin() as c:
            account_id = str(c.execute(text(
                "INSERT INTO email_accounts (user_id, provider, email_address, "
                "credentials_encrypted, sync_enabled, sync_interval_secs, "
                "sync_status, initial_sync_done, organization_id) "
                "VALUES ('b@em-t4c.test', 'microsoft', :m, 'old', true, 300, "
                "'idle', true, CAST(:o AS uuid)) RETURNING id"),
                {"m": f"box-{uuid.uuid4().hex[:8]}@em-t4c.test",
                 "o": p.org_b}).scalar_one())

        service, provider = _outlook_refreshing_in_phase_e(wire)

        async def _no_op(*_a, **_k):
            return None

        monkeypatch.setattr(sched, "run_label_learn_hook", _no_op)
        monkeypatch.setattr(key_store, "get_key_store", lambda: _Store())
        monkeypatch.setattr(sched, "build_provider", lambda name, creds: provider)
        app_dsn = p.app_url.render_as_string(hide_password=False)
        token = clear_tenant()
        try:
            async with tenant_engine_scope(app_dsn):
                res = await sched._sync_account(account_id,
                                                organization_id=p.org_b)
            assert res == {"synced": 2, "history_id": None}
            assert service.refreshes == 1
            with p.admin_engine.connect() as c:
                acct = c.execute(text(
                    "SELECT sync_status, credentials_encrypted "
                    "FROM email_accounts WHERE id = CAST(:a AS uuid)"),
                    {"a": account_id}).mappings().one()
                bodies = c.execute(text(
                    "SELECT body_text FROM email_messages "
                    "WHERE account_id = CAST(:a AS uuid) "
                    "ORDER BY provider_message_id"),
                    {"a": account_id}).scalars().all()
            assert acct["sync_status"] == "idle"
            assert bodies == ["the body of m1", "the body of m2"]
            assert acct["credentials_encrypted"] != "old", (
                "a successful sync left the tokens of phase (e) out of the row"
            )
            creds = json.loads(acct["credentials_encrypted"].removeprefix("enc:"))
            assert creds == {"access_token": "at-2", "refresh_token": "r-2"}
        finally:
            release_tenant(token)
            with p.admin_engine.begin() as c:
                c.execute(text("DELETE FROM email_messages WHERE account_id = "
                               "CAST(:a AS uuid)"), {"a": account_id})
            _purge_account(p.admin_engine, account_id)

    async def test_credentials_encrypted_holds_the_new_refresh_token(
        self, promoted, app_engine, monkeypatch, wire,  # noqa: F811
    ):
        from acb_llm import key_store

        p = promoted
        with p.admin_engine.begin() as c:
            account_id = str(c.execute(text(
                "INSERT INTO email_accounts (user_id, provider, email_address, "
                "credentials_encrypted, sync_enabled, sync_interval_secs, "
                "sync_status, initial_sync_done, organization_id) "
                "VALUES ('b@em-t4c.test', 'microsoft', :m, 'old', true, 300, "
                "'idle', true, CAST(:o AS uuid)) RETURNING id"),
                {"m": f"box-{uuid.uuid4().hex[:8]}@em-t4c.test",
                 "o": p.org_b}).scalar_one())

        service, provider = _refreshing_outlook(wire)

        async def _upsert_fails(db, account_id, msg):
            raise RuntimeError("phase (c) failed")

        monkeypatch.setattr(sched, "upsert_message", _upsert_fails)
        monkeypatch.setattr(key_store, "get_key_store", lambda: _Store())
        monkeypatch.setattr(sched, "build_provider", lambda name, creds: provider)
        app_dsn = p.app_url.render_as_string(hide_password=False)
        token = clear_tenant()
        try:
            async with tenant_engine_scope(app_dsn):
                res = await sched._sync_account(account_id,
                                                organization_id=p.org_b)
            assert res == {"error": "phase (c) failed"}
            assert service.refreshes == 1
            with p.admin_engine.connect() as c:
                acct = c.execute(text(
                    "SELECT sync_status, credentials_encrypted "
                    "FROM email_accounts WHERE id = CAST(:a AS uuid)"),
                    {"a": account_id}).mappings().one()
                log = c.execute(text(
                    "SELECT status FROM email_sync_log "
                    "WHERE account_id = CAST(:a AS uuid)"),
                    {"a": account_id}).scalars().all()
            assert acct["sync_status"] == "error"
            assert log == ["error"]
            creds = json.loads(acct["credentials_encrypted"].removeprefix("enc:"))
            assert creds == {"access_token": "at-2", "refresh_token": "r-2"}
        finally:
            release_tenant(token)
            _purge_account(p.admin_engine, account_id)
