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
        self.refuse = 0  # answer the next N polls with a 401 that is not a revoke
        self.bad_answer = 0  # answer the next N polls with a bad deadline_s
        self.revoke_on_reply = False  # a reply answers 401 revoked; polls still pass
        self.hold = 0.0  # seconds that each poll is held before its answer, as Metorite does
        self.revoke_delay = 0.0  # seconds that a revoke takes, as a network call does

    def _auth(self, request: Request) -> bool:
        return request.headers.get("authorization") == f"Bearer {CREDENTIAL}" and not self.revoked

    def _refused(self) -> JSONResponse:
        error = "revoked" if self.revoked else "unauthorized"
        return JSONResponse({"error": error}, status_code=401)

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
            return self._refused()
        if self.refuse:
            self.refuse -= 1
            return JSONResponse({"error": "org_not_allowed"}, status_code=401)
        if self.bad_answer:
            self.bad_answer -= 1
            return JSONResponse({"requests": [{"id": "x", "method": "POST", "deadline_s": None}]})
        if self.hold:
            await asyncio.sleep(self.hold)
        if self.poll_fail:
            self.poll_fail -= 1
            return JSONResponse({"error": "busy"}, status_code=503)
        self.polls.append(await request.json())
        out, self.queue = self.queue, []
        return JSONResponse({"requests": out})

    async def reply(self, request: Request) -> JSONResponse:
        if self.revoke_on_reply:
            return JSONResponse({"error": "revoked"}, status_code=401)
        if not self._auth(request):
            return self._refused()
        self.replies.append(await request.json())
        return JSONResponse({"ok": True})

    async def revoke(self, request: Request) -> JSONResponse:
        if self.revoke_delay:
            await asyncio.sleep(self.revoke_delay)
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

    async def run(store):
        # The stop and the timeout make a missing check fail, never hang.
        stop = asyncio.Event()
        asyncio.get_running_loop().call_later(0.2, stop.set)
        return await asyncio.wait_for(
            run_link(link.api(), store, _echo, _up, "0.1.0", stop, Sleeps(), min_gap_s=0), 1)

    unconfirmed = MemoryLinkStore(LinkState(CREDENTIAL, "conn-1", "Acme Traders"))
    with pytest.raises(NotConfirmed):
        asyncio.run(run(unconfirmed))
    assert link.polls == []
    with pytest.raises(NotConfirmed):
        asyncio.run(run(MemoryLinkStore()))
    assert link.polls == []


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
    # The exact name: another case is a different answer (rule j).
    assert linkcli.do_confirm(_args(company="acme traders"), store) == 2
    assert store.state.confirmed is False
    assert linkcli.do_confirm(_args(company="  Acme Traders "), store) == 0
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
            run_link(link.api(), store, dispatch, up, "0.1.0", stop, sleep or Sleeps(),
                     min_gap_s=0), 5)
        watcher.cancel()
        return result

    return asyncio.run(go())


def test_each_poll_carries_the_tally_state_and_the_version():
    link = FakeLink()

    async def down() -> bool:
        return False

    _run(link, _confirmed(), up=down, polls=2)
    assert link.polls[0] == {"tally": "down", "version": "0.1.0", "copy_at": None}


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
        await asyncio.sleep(3)  # short, so a missing stop fails and does not hang
        return JSONResponse({"requests": []})

    api = LinkApi("https://link.test", transport=httpx.ASGITransport(
        app=Starlette(routes=[Route(p.LINK_POLL, held, methods=["POST"])])))

    async def go():
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        loop.call_later(0.1, stop.set)
        started = loop.time()
        result = await asyncio.wait_for(
            run_link(api, _confirmed(), _echo, _up, "0.1.0", stop, Sleeps(), min_gap_s=0), 2)
        return result, loop.time() - started

    result, took = asyncio.run(go())
    assert result == "stopped" and took < 1.0


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
    (_req(method="GET"), 405),
    (_req(method="DELETE"), 405),
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
                                            stop, Sleeps(), min_gap_s=0), 10)
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
    # A blob that does not unprotect reads as no link, even when its bytes
    # hold a whole state (a plain file that a user put there).
    from tally_bridge.transport.link.store import state_to_bytes

    path.write_bytes(state_to_bytes(LinkState(CREDENTIAL, "conn-9", "Other Co", confirmed=True)))
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


# --- Review fixes (Opus diff-reviewer of TLY-2a) ---------------------------------


def test_a_401_that_is_not_a_revoke_keeps_the_credential_and_backs_off():
    """Finding 1: a pause of the org (rule i) answers 401 too. Only
    {"error": "revoked"} deletes the credential."""
    link = FakeLink()
    link.refuse = 2
    store = _confirmed()
    sleeps = Sleeps()
    assert _run(link, store, sleep=sleeps, polls=1) == "stopped"
    assert store.state is not None and len(sleeps.calls) == 2


def test_a_bad_field_in_a_poll_answer_does_not_end_the_link():
    """Finding 2: a deadline_s of null is a bad answer: log, back off, go on."""
    link = FakeLink()
    link.bad_answer = 1
    sleeps = Sleeps()
    assert _run(link, _confirmed(), sleep=sleeps, polls=1) == "stopped"
    assert len(sleeps.calls) == 1 and len(link.polls) >= 1


@pytest.mark.parametrize(("raw", "seconds"), [(1e9, 30.0), (-5, 0.0), ("12.5", 12.5)])
def test_deadline_s_is_clamped_to_30_seconds(raw, seconds):
    (req,) = p.requests_from({"requests": [{"id": "r", "method": "POST", "deadline_s": raw}]})
    assert req.deadline_s == seconds


@pytest.mark.parametrize("raw", [None, "30s", float("nan")])
def test_a_deadline_s_that_is_not_a_number_is_a_bad_answer(raw):
    with pytest.raises(p.LinkError):
        p.requests_from({"requests": [{"id": "r", "method": "POST", "deadline_s": raw}]})


def test_a_poll_answer_with_too_many_requests_is_refused():
    with pytest.raises(p.LinkError):
        p.requests_from({"requests": [{"id": str(i)} for i in range(p.MAX_REQUESTS_PER_POLL + 1)]})


def test_a_slow_request_does_not_hold_up_the_next_poll():
    """Finding 3: a connect needs three round trips in 10 s. A slow report of
    another member must not hold the poll."""
    link = FakeLink()
    link.queue = [{"id": "slow", "method": "POST", "body": "{}", "deadline_s": 30}]
    release = asyncio.Event()

    async def slow(request: p.LinkRequest) -> p.LinkReply:
        await release.wait()
        return await _echo(request)

    async def go():
        stop = asyncio.Event()
        loop = asyncio.create_task(run_link(link.api(), _confirmed(), slow, _up, "0.1.0", stop,
                                            Sleeps(), min_gap_s=0))
        while len(link.polls) < 3:
            await asyncio.sleep(0.005)
        assert link.replies == []  # the slow one still runs, and polls go on
        release.set()
        while not link.replies:
            await asyncio.sleep(0.005)
        stop.set()
        return await asyncio.wait_for(loop, 2)

    assert asyncio.run(go()) == "stopped"
    assert [r["id"] for r in link.replies] == ["slow"]


def test_an_unlink_stops_a_running_loop_at_the_next_poll():
    """Finding 5, case A: unlink while serve runs. The loop reads the store
    again before each poll and stops."""
    link = FakeLink()
    store = _confirmed()

    async def go():
        stop = asyncio.Event()
        loop = asyncio.create_task(run_link(link.api(), store, _echo, _up, "0.1.0", stop,
                                            Sleeps(), min_gap_s=0))
        while not link.polls:
            await asyncio.sleep(0.005)
        store.delete()
        return await asyncio.wait_for(loop, 2)

    assert asyncio.run(go()) == "unlinked"


def test_a_revoke_of_an_old_link_keeps_a_new_pairing():
    """Finding 5, case B: the old loop gets a revoke after a new pairing.
    It deletes only its own credential."""
    from tally_bridge.transport.link.runner import _forget

    store = MemoryLinkStore(LinkState("mlc_" + "B" * 43, "conn-2", "Acme Traders"))
    _forget(store, CREDENTIAL)
    assert store.state is not None and store.state.connector_id == "conn-2"
    _forget(store, "mlc_" + "B" * 43)
    assert store.state is None


def test_a_new_pairing_revokes_an_old_unconfirmed_one():
    """Finding 7: an unconfirmed pairing that the customer does not take is
    revoked, so it does not stay live at Metorite (§3.8.4 step 11)."""
    link = FakeLink()
    store = MemoryLinkStore(LinkState(CREDENTIAL, "conn-0", "Old Co"))
    assert linkcli.do_pair(_args(domain="example.com"), Settings(link_url="https://link.test"),
                           store, link.api(), Sleeps()) == 0
    assert link.revokes == 1 and store.state.connector_id == "conn-1"


def test_an_empty_answer_at_once_waits_before_the_next_poll():
    link = FakeLink()

    async def go():
        stop = asyncio.Event()
        asyncio.get_running_loop().call_later(0.25, stop.set)
        await run_link(link.api(), _confirmed(), _echo, _up, "0.1.0", stop, Sleeps(),
                       min_gap_s=0.1)

    asyncio.run(go())
    assert 2 <= len(link.polls) <= 4


@pytest.mark.parametrize("url", ["https://:pw@x.test", "https://@x.test"])
def test_link_url_refuses_any_userinfo(url):
    with pytest.raises(SettingsError):
        Settings(link_url=url).validate()


# --- Review round 2 ------------------------------------------------------------------


def test_the_wait_for_a_slot_comes_off_the_deadline(monkeypatch):
    """Round 2, finding 1: a queued request gets only the time that is left,
    and none when its time ran out while it waited."""
    from tally_bridge.transport.link import runner

    monkeypatch.setattr(runner, "MAX_IN_FLIGHT", 1)
    link = FakeLink()
    link.queue = [
        {"id": "slow", "method": "POST", "body": "{}", "deadline_s": 30},
        {"id": "short", "method": "POST", "body": "{}", "deadline_s": 0.2},
        {"id": "later", "method": "POST", "body": "{}", "deadline_s": 30},
    ]
    seen: dict[str, float] = {}

    async def dispatch(request: p.LinkRequest) -> p.LinkReply:
        seen[request.id] = request.deadline_s
        if request.id == "slow":
            await asyncio.sleep(0.4)
        return await _echo(request)

    async def go():
        stop = asyncio.Event()
        loop = asyncio.create_task(run_link(link.api(), _confirmed(), dispatch, _up, "0.1.0",
                                            stop, Sleeps(), min_gap_s=0))
        while len(link.replies) < 2:
            await asyncio.sleep(0.01)
        stop.set()
        await asyncio.wait_for(loop, 2)

    asyncio.run(go())
    assert [r["id"] for r in link.replies] == ["slow", "later"]  # "short" ran out
    assert "short" not in seen and seen["later"] < 29.8


def test_the_loop_stops_polling_while_the_backlog_is_full(monkeypatch):
    """Round 2, finding 1: under load the backlog stays bounded."""
    from tally_bridge.transport.link import runner

    monkeypatch.setattr(runner, "MAX_BACKLOG", 2)
    link = FakeLink()
    link.queue = [{"id": str(i), "method": "POST", "body": "{}", "deadline_s": 30}
                  for i in range(2)]
    release = asyncio.Event()

    async def held(request: p.LinkRequest) -> p.LinkReply:
        await release.wait()
        return await _echo(request)

    async def go():
        stop = asyncio.Event()
        loop = asyncio.create_task(run_link(link.api(), _confirmed(), held, _up, "0.1.0", stop,
                                            Sleeps(), min_gap_s=0))
        while not link.polls:
            await asyncio.sleep(0.005)
        await asyncio.sleep(0.1)
        polls_while_full = len(link.polls)
        release.set()
        while len(link.replies) < 2:
            await asyncio.sleep(0.005)
        await asyncio.sleep(0.05)
        stop.set()
        await asyncio.wait_for(loop, 2)
        return polls_while_full

    assert asyncio.run(go()) == 1
    assert len(link.polls) > 1


def test_a_store_read_error_backs_off_and_does_not_end_the_link():
    """Round 2: a scanner that holds link.bin for a moment does not end it."""

    class Flaky(MemoryLinkStore):
        def __init__(self, state):
            super().__init__(state)
            self.fail = 0

        def load(self):
            if self.fail:
                self.fail -= 1
                raise OSError("sharing violation")
            return super().load()

    store = Flaky(LinkState(CREDENTIAL, "conn-1", "Acme Traders", confirmed=True))
    store.fail = 0
    link = FakeLink()
    sleeps = Sleeps()

    async def go():
        stop = asyncio.Event()
        loop = asyncio.create_task(run_link(link.api(), store, _echo, _up, "0.1.0", stop, sleeps,
                                            min_gap_s=0))
        await asyncio.sleep(0.01)
        store.fail = 2
        while store.fail or len(link.polls) < 3:
            await asyncio.sleep(0.005)
        stop.set()
        return await asyncio.wait_for(loop, 2)

    assert asyncio.run(go()) == "stopped"
    assert len(sleeps.calls) >= 1 and store.state is not None


def test_a_revoke_on_a_reply_ends_the_loop_as_revoked():
    """Round 2: the customer sees "Disconnected by your admin"."""
    link = FakeLink()
    link.revoke_on_reply = True
    link.queue = [{"id": "r", "method": "POST", "body": "{}", "deadline_s": 30}]
    store = _confirmed()
    assert _run(link, store, polls=5) == "revoked"
    assert store.state is None


def test_unlink_says_so_when_metorite_refuses_the_revoke():
    """Round 2 note: during a pause (403) the credential stays live at
    Metorite, so unlink must not say that all is done."""

    def paused(request):
        return httpx.Response(403, json={"error": "org_not_allowed"})

    api = LinkApi("https://link.test", transport=httpx.MockTransport(paused))
    store = _confirmed()
    assert linkcli.do_unlink(Settings(link_url="https://link.test"), store, api) == 1
    assert store.state is None


# --- The service stop and the Windows commands (review of the merge with TLY-5) ----


def test_a_service_stop_ends_the_link_at_once_and_a_link_error_is_no_crash():
    """The stop of the Windows service stops the link poll first. An error of
    the link loop is logged, so Windows does not see a crash and restart."""
    import threading

    from tally_bridge import __main__ as cli

    order: list[str] = []

    class Server:
        _exit = False

        @property
        def should_exit(self):
            return self._exit

        @should_exit.setter
        def should_exit(self, value):
            order.append("server exit")
            self._exit = value

        async def serve(self):
            while not self._exit:
                await asyncio.sleep(0.01)

    class Watched(asyncio.Event):
        def set(self):
            order.append("link stop")
            super().set()

    async def link(link_stop: asyncio.Event) -> str:
        await link_stop.wait()
        raise RuntimeError("the link loop failed")

    server = Server()
    stop = threading.Event()
    threading.Timer(0.1, stop.set).start()
    real_event = asyncio.Event
    asyncio.Event = Watched  # the link_stop of _serve_with_copy
    try:
        asyncio.run(asyncio.wait_for(
            cli._serve_with_copy(server, None, None, None, stop, link), 5))
    finally:
        asyncio.Event = real_event
    assert order[:2] == ["link stop", "server exit"]


def test_a_link_command_without_the_rights_says_run_as_an_administrator(monkeypatch, capsys):
    from tally_bridge import __main__ as cli

    def denied(args):
        raise PermissionError("access is denied")

    monkeypatch.setattr(linkcli, "link_store", denied)
    assert cli.main(["unlink", "--windows"]) == 2
    assert "administrator" in capsys.readouterr().err
