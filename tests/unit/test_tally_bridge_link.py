"""TLY-2: the program's link mode, against a fake Metorite link.

The Metorite side (LNK-1 to LNK-3) does not exist yet. ``FakeLink`` plays
the five routes of ``integrations_console.md`` §3.8.4 and §3.8.5 by their
rules: the device grant of RFC 8628, a held poll that hands out requests,
a reply by request id, 401 after a revoke. The tests run on Linux.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import httpx
import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

REPO = Path(__file__).resolve().parents[2]
ROOT = REPO / "apps" / "connectors" / "tally_bridge"
sys.path.insert(0, str(ROOT))

from tally_bridge import linkcli  # noqa: E402
from tally_bridge.settings import Settings, SettingsError  # noqa: E402
from tally_bridge.transport.link import protocol as p  # noqa: E402
from tally_bridge.transport.link.api import LinkApi  # noqa: E402
from tally_bridge.transport.link.dispatch import MAX_BODY_BYTES, McpDispatcher  # noqa: E402
from tally_bridge.transport.link.pairing import pair  # noqa: E402
from tally_bridge.transport.link.runner import NotConfirmed, run_link  # noqa: E402
from tally_bridge.transport.link.store import (  # noqa: E402
    BlobLinkStore,
    DevFileLinkStore,
    LinkState,
    MemoryLinkStore,
)

CREDENTIAL = "mlc_" + "A" * 43
DEVICE = "d" * 43


class FakeLink:
    """The Metorite side of the five routes, in memory."""

    def __init__(self, pending: int = 0, slow_down_at: tuple[int, ...] = (),
                 outcome: str = "approve", company: str = "Acme Traders") -> None:
        self.pending = pending
        self.slow_down_at = slow_down_at
        self.outcome = outcome
        self.company = company
        self.starts: list[dict] = []
        self.pair_polls = 0
        self.polls: list[dict] = []
        self.queue: list[dict] = []
        self.replies: list[dict] = []
        self.revoked = False
        self.revokes = 0
        self.poll_fail = 0  # answer the next N polls with HTTP 503

    def _auth(self, request: Request) -> bool:
        return request.headers.get("authorization") == f"Bearer {CREDENTIAL}" and not self.revoked

    async def start(self, request: Request) -> JSONResponse:
        self.starts.append(await request.json())
        return JSONResponse({"device_code": DEVICE, "user_code": "WDJB-MJHT",
                             "expires_in": 600, "interval": 5})

    async def pair_poll(self, request: Request) -> JSONResponse:
        assert (await request.json())["device_code"] == DEVICE
        self.pair_polls += 1
        if self.pair_polls in self.slow_down_at:
            return JSONResponse({"error": "slow_down"}, status_code=400)
        if self.pair_polls <= self.pending:
            return JSONResponse({"error": "authorization_pending"}, status_code=400)
        if self.outcome != "approve":
            return JSONResponse({"error": self.outcome}, status_code=400)
        return JSONResponse({"credential": CREDENTIAL, "connector_id": "conn-1",
                             "company_name": self.company})

    async def link_poll(self, request: Request) -> JSONResponse:
        if not self._auth(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        if self.poll_fail:
            self.poll_fail -= 1
            return JSONResponse({"error": "busy"}, status_code=503)
        self.polls.append(await request.json())
        out, self.queue = self.queue, []
        return JSONResponse({"requests": out})

    async def reply(self, request: Request) -> JSONResponse:
        if not self._auth(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        self.replies.append(await request.json())
        return JSONResponse({"ok": True})

    async def revoke(self, request: Request) -> JSONResponse:
        self.revokes += 1
        self.revoked = True
        return JSONResponse({"ok": True})

    def app(self) -> Starlette:
        return Starlette(routes=[
            Route(p.PAIR_START, self.start, methods=["POST"]),
            Route(p.PAIR_POLL, self.pair_poll, methods=["POST"]),
            Route(p.LINK_POLL, self.link_poll, methods=["POST"]),
            Route(p.LINK_REPLY, self.reply, methods=["POST"]),
            Route(p.LINK_REVOKE, self.revoke, methods=["POST"]),
        ])

    def api(self) -> LinkApi:
        return LinkApi("https://link.test", transport=httpx.ASGITransport(app=self.app()))


class Sleeps:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, s: float) -> None:
        self.calls.append(s)


# --- The pairing (§3.8.4) ----------------------------------------------------------


def test_pairing_shows_the_user_code_and_returns_the_company_after_the_approval():
    link = FakeLink(pending=2, slow_down_at=(2,))
    shown: list[str] = []
    sleeps = Sleeps()
    paired = asyncio.run(pair(link.api(), " Example.COM ", "OFFICE-PC", "0.1.0",
                              shown.append, sleeps))
    assert shown == ["WDJB-MJHT"]
    assert link.starts == [{"domain": "example.com", "machine": "OFFICE-PC",
                            "version": "0.1.0", "kind": "tally"}]
    # Three polls: pending, slow_down (it adds 5 s to the interval), approved.
    assert sleeps.calls == [5, 5, 10] and link.pair_polls == 3
    assert paired.company_name == "Acme Traders" and paired.connector_id == "conn-1"
    assert CREDENTIAL not in repr(paired)


@pytest.mark.parametrize(("outcome", "error"), [("expired_token", p.PairExpired),
                                                 ("access_denied", p.PairDenied)])
def test_pairing_ends_on_expiry_or_refusal(outcome, error):
    with pytest.raises(error):
        asyncio.run(pair(FakeLink(outcome=outcome).api(), "example.com", "PC", "0.1.0",
                         lambda c: None, Sleeps()))


def test_pairing_stops_at_its_ten_minutes_by_the_clock():
    now = [0.0]

    async def sleep(s):
        now[0] += s

    with pytest.raises(p.PairExpired):
        asyncio.run(pair(FakeLink(pending=10_000).api(), "example.com", "PC", "0.1.0",
                         lambda c: None, sleep, clock=lambda: now[0]))


@pytest.mark.parametrize("domain", ["", "https://example.com", "a b.com"])
def test_pairing_takes_a_domain_only(domain):
    with pytest.raises(p.LinkError):
        asyncio.run(pair(FakeLink().api(), domain, "PC", "0.1.0", lambda c: None, Sleeps()))


@pytest.mark.parametrize("code", ["WDJB-MJH", "WDJB-MJHI", "WDJB-MJHO", "WDJB-MJHUU"])
def test_a_user_code_that_is_not_8_crockford_characters_is_refused(code):
    with pytest.raises(p.LinkError):
        p.pair_start_from({"device_code": DEVICE, "user_code": code})


def test_a_pairing_answer_with_no_proper_credential_is_refused():
    with pytest.raises(p.LinkError):
        p.paired_from({"credential": "abc", "connector_id": "c", "company_name": "A"})
    with pytest.raises(p.LinkError):
        p.paired_from({"credential": CREDENTIAL, "connector_id": "c", "company_name": " "})


# --- Rule j: no call before the customer confirms the company -------------------


def _confirmed() -> MemoryLinkStore:
    return MemoryLinkStore(LinkState(CREDENTIAL, "conn-1", "Acme Traders", confirmed=True))


async def _echo(request: p.LinkRequest) -> p.LinkReply:
    return p.LinkReply(request.id, 200, {"content-type": "application/json"}, request.body)


async def _up() -> bool:
    return True


def test_the_link_serves_nothing_before_the_customer_confirms():
    """Rule j (§3.8.7): the fence that the spec names for TLY-2."""
    link = FakeLink()
    store = MemoryLinkStore(LinkState(CREDENTIAL, "conn-1", "Acme Traders"))
    with pytest.raises(NotConfirmed):
        asyncio.run(run_link(link.api(), store, _echo, _up, "0.1.0"))
    assert link.polls == []
    with pytest.raises(NotConfirmed):
        asyncio.run(run_link(link.api(), MemoryLinkStore(), _echo, _up, "0.1.0"))


def _args(**kw) -> argparse.Namespace:
    return argparse.Namespace(**{"windows": False, "dev_link_file": None, "domain": None,
                                 "company": None, **kw})


def test_pair_then_confirm_with_the_exact_name_then_serve(capsys):
    link = FakeLink()
    store = MemoryLinkStore()
    settings = Settings(link_url="https://link.test")
    assert linkcli.do_pair(_args(domain="example.com"), settings, store, link.api(),
                           Sleeps()) == 0
    out = capsys.readouterr().out
    assert "WDJB-MJHT" in out and "Acme Traders" in out
    assert DEVICE not in out and CREDENTIAL not in out
    assert store.state is not None and store.state.confirmed is False
    assert linkcli.do_confirm(_args(company="Other Co"), store) == 2
    assert store.state.confirmed is False
    assert linkcli.do_confirm(_args(company="acme traders"), store) == 0
    assert store.state.confirmed is True


def test_a_refusal_revokes_the_credential_and_deletes_it():
    """Rule j, second half: a refusal calls /connectors/link/revoke."""
    link = FakeLink()
    store = MemoryLinkStore(LinkState(CREDENTIAL, "conn-1", "Stranger Ltd"))
    assert linkcli.do_unlink(Settings(link_url="https://link.test"), store, link.api()) == 0
    assert link.revokes == 1 and store.state is None


def test_unlink_deletes_here_even_when_metorite_cannot_be_reached():
    def down(request):
        raise httpx.ConnectError("no route", request=request)

    api = LinkApi("https://link.test", transport=httpx.MockTransport(down))
    store = _confirmed()
    assert linkcli.do_unlink(Settings(link_url="https://link.test"), store, api) == 1
    assert store.state is None


def test_pair_refuses_a_machine_that_is_already_linked():
    store = _confirmed()
    assert linkcli.do_pair(_args(domain="example.com"), Settings(link_url="https://x.test"),
                           store, FakeLink().api(), Sleeps()) == 2
    assert store.state.confirmed is True


def test_serve_starts_no_link_loop_before_the_confirmation():
    settings = Settings(link_url="https://link.test")
    unconfirmed = MemoryLinkStore(LinkState(CREDENTIAL, "conn-1", "Acme Traders"))
    assert linkcli.link_loop(settings, unconfirmed, object(), object()) is None
    assert linkcli.link_loop(Settings(), _confirmed(), object(), object()) is None
    assert linkcli.link_loop(settings, _confirmed(), object(), object()) is not None


# --- The poll, the reply, revoke and a cut link (§3.8.5, §3.8.6) -----------------


def _run(link: FakeLink, store, dispatch=_echo, up=_up, sleep=None, polls: int = 1) -> str:
    """Run the loop until the fake link has seen *polls* polls."""

    async def go() -> str:
        stop = asyncio.Event()

        async def watch():
            while len(link.polls) < polls and not link.revoked:
                await asyncio.sleep(0.005)
            await asyncio.sleep(0.02)
            stop.set()

        watcher = asyncio.create_task(watch())
        result = await asyncio.wait_for(
            run_link(link.api(), store, dispatch, up, "0.1.0", stop, sleep or Sleeps()), 5)
        watcher.cancel()
        return result

    return asyncio.run(go())


def test_each_poll_carries_the_tally_state_and_the_version():
    link = FakeLink()

    async def down() -> bool:
        return False

    _run(link, _confirmed(), up=down, polls=2)
    assert link.polls[0] == {"tally": "down", "version": "0.1.0"}


def test_a_request_goes_to_the_mcp_layer_and_its_reply_comes_back_by_id():
    link = FakeLink()
    link.queue = [{"id": "r-1", "method": "POST", "headers": {"Content-Type": "application/json"},
                   "body": '{"x": 1}', "deadline_s": 30}]
    _run(link, _confirmed(), polls=2)
    assert link.replies == [{"id": "r-1", "status": 200,
                             "headers": {"content-type": "application/json"},
                             "body": '{"x": 1}'}]


def test_a_request_past_its_deadline_gets_no_reply():
    link = FakeLink()
    link.queue = [{"id": "late", "method": "POST", "body": "{}", "deadline_s": 0}]
    _run(link, _confirmed(), polls=2)
    assert link.replies == []


def test_a_revoke_deletes_the_credential_and_ends_the_loop():
    link = FakeLink()
    link.revoked = True
    store = _confirmed()
    assert _run(link, store) == "revoked"
    assert store.state is None


def test_a_cut_link_comes_back_with_a_growing_wait_and_no_manual_step():
    link = FakeLink()
    link.poll_fail = 4
    sleeps = Sleeps()
    assert _run(link, _confirmed(), sleep=sleeps, polls=1) == "stopped"
    # 1, 2, 4 and 8 s, each within 20 % of jitter; then the poll works.
    assert len(sleeps.calls) == 4
    for got, base in zip(sleeps.calls, [1, 2, 4, 8], strict=True):
        assert 0.8 * base <= got <= 1.2 * base
    assert len(link.polls) >= 1


def test_a_stop_ends_a_held_poll_at_once():
    """A held poll waits up to 25 s. A service stop must not wait for it."""

    async def held(request: Request) -> JSONResponse:
        await asyncio.sleep(30)
        return JSONResponse({"requests": []})

    api = LinkApi("https://link.test", transport=httpx.ASGITransport(
        app=Starlette(routes=[Route(p.LINK_POLL, held, methods=["POST"])])))

    async def go():
        stop = asyncio.Event()
        asyncio.get_running_loop().call_later(0.1, stop.set)
        return await asyncio.wait_for(run_link(api, _confirmed(), _echo, _up, "0.1.0", stop), 2)

    assert asyncio.run(go()) == "stopped"


def test_the_api_follows_no_redirect():
    def moved(request):
        return httpx.Response(302, headers={"location": "https://elsewhere.test/"})

    api = LinkApi("https://link.test", transport=httpx.MockTransport(moved))
    with pytest.raises(p.LinkError):
        asyncio.run(api.poll(CREDENTIAL, True, "0.1.0"))


# --- The dispatch to the MCP layer -----------------------------------------------


def _req(method="POST", body="{}", headers=None, rid="r") -> p.LinkRequest:
    return p.LinkRequest(rid, method, headers or {}, body, 5.0)


def _app_seen():
    seen: dict = {}

    async def mcp(request: Request) -> JSONResponse:
        seen["path"] = request.url.path
        seen["headers"] = dict(request.headers)
        return JSONResponse({"ok": True}, headers={"mcp-session-id": "s1", "set-cookie": "x=1"})

    return Starlette(routes=[Route("/mcp", mcp, methods=["POST", "GET", "DELETE"])]), seen


def test_the_dispatch_passes_only_the_mcp_headers_both_ways():
    app, seen = _app_seen()
    reply = asyncio.run(McpDispatcher(app, 8765)(_req(headers={
        "content-type": "application/json", "mcp-session-id": "s1",
        "authorization": "Bearer stolen", "cookie": "a=b"})))
    assert seen["path"] == "/mcp"
    assert "authorization" not in seen["headers"] and "cookie" not in seen["headers"]
    assert seen["headers"]["host"] == "127.0.0.1:8765"
    assert reply.status == 200 and reply.headers.get("mcp-session-id") == "s1"
    assert "set-cookie" not in reply.headers


@pytest.mark.parametrize(("req", "status"), [
    (_req(method="PUT"), 405),
    (_req(body="x" * (MAX_BODY_BYTES + 1)), 413),
])
def test_the_dispatch_refuses_a_method_or_a_body_it_does_not_serve(req, status):
    app, seen = _app_seen()
    reply = asyncio.run(McpDispatcher(app, 8765)(req))
    assert reply.status == status and reply.id == req.id and seen == {}


def test_the_dispatch_refuses_a_redirect_of_the_layer():
    async def moved(request):
        from starlette.responses import RedirectResponse
        return RedirectResponse("/elsewhere")

    app = Starlette(routes=[Route("/mcp", moved, methods=["POST"])])
    assert asyncio.run(McpDispatcher(app, 8765)(_req())).status == 502


def test_a_real_mcp_initialize_goes_through_the_link():
    """End to end: the fake link hands out an MCP initialize, the dispatch
    runs it against the real read server of this program, and the reply holds
    its server info."""
    from tally_bridge.secret_store import MemorySecretStore
    from tally_bridge.transport import build_address_app

    class Reader:
        async def is_up(self) -> bool:
            return True

        async def post(self, envelope):  # pragma: no cover - not reached
            raise AssertionError

    guard = build_address_app(Settings(), MemorySecretStore(None), Reader())
    link = FakeLink()
    link.queue = [{"id": "init-1", "method": "POST", "deadline_s": 10, "headers": {
        "content-type": "application/json", "accept": "application/json, text/event-stream"},
        "body": json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "test", "version": "1"}}})}]

    async def go():
        async with guard.inner.router.lifespan_context(guard.inner):
            stop = asyncio.Event()

            async def watch():
                while not link.replies:
                    await asyncio.sleep(0.01)
                stop.set()

            watcher = asyncio.create_task(watch())
            await asyncio.wait_for(run_link(link.api(), _confirmed(),
                                            McpDispatcher(guard.inner, 8765), _up, "0.1.0",
                                            stop, Sleeps()), 10)
            watcher.cancel()

    asyncio.run(go())
    (reply,) = link.replies
    assert reply["id"] == "init-1" and reply["status"] == 200, reply
    assert json.loads(reply["body"])["result"]["serverInfo"]["name"]


# --- The stores, and the setting ---------------------------------------------------


def test_the_dev_link_file_is_private_and_round_trips(tmp_path):
    store = DevFileLinkStore(tmp_path / "link.json")
    assert store.load() is None
    store.save(LinkState(CREDENTIAL, "conn-1", "Acme Traders"))
    assert store.load() == LinkState(CREDENTIAL, "conn-1", "Acme Traders")
    if sys.platform != "win32":
        assert (tmp_path / "link.json").stat().st_mode & 0o777 == 0o600
    store.delete()
    assert store.load() is None


class FakeProtector:
    def protect(self, data: bytes) -> bytes:
        return b"P" + data[::-1]

    def unprotect(self, blob: bytes) -> bytes:
        from tally_bridge.windows.dpapi import ProtectError

        if not blob.startswith(b"P"):
            raise ProtectError("not ours")
        return blob[1:][::-1]


def test_the_windows_link_blob_is_protected_and_fails_closed(tmp_path):
    from tally_bridge.windows.stores import DpapiBlobFile

    path = tmp_path / "link.bin"
    store = BlobLinkStore(DpapiBlobFile(path, FakeProtector()))
    store.save(LinkState(CREDENTIAL, "conn-1", "Acme Traders", confirmed=True))
    assert CREDENTIAL.encode() not in path.read_bytes()
    assert store.load().confirmed is True
    path.write_bytes(b"damaged")
    assert store.load() is None
    store.delete()
    assert not path.exists()


@pytest.mark.parametrize("url", ["https://metorite.example", "https://x.test/",
                                 "http://127.0.0.1:9900"])
def test_link_url_takes_https_or_a_loopback_test(url):
    Settings(link_url=url).validate()


@pytest.mark.parametrize("url", ["http://x.example", "ftp://x.example", "https://x.test/path",
                                 "https://user@x.test", "x.example"])
def test_link_url_refuses_anything_else(url):
    with pytest.raises(SettingsError):
        Settings(link_url=url).validate()


def test_link_mode_is_off_by_default_and_names_no_host():
    assert Settings().link_url == ""
