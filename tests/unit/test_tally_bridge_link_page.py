"""TLY-2b: the pair step of the setup page, the link controller, and the copy
time on each poll (``integrations_console.md`` §3.8.13 and §3.8.14).

The fake link of ``test_tally_bridge_link.py`` plays Metorite.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
ROOT = REPO / "apps" / "connectors" / "tally_bridge"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from starlette.testclient import TestClient  # noqa: E402
from tally_bridge.localcopy import CopyStore  # noqa: E402
from tally_bridge.localcopy.store import Snapshot  # noqa: E402
from tally_bridge.secret_store import MemorySecretStore, new_token, token_sha256  # noqa: E402
from tally_bridge.settings import FileSettingsStore, LiveSettings, Settings  # noqa: E402
from tally_bridge.transport import build_address_app  # noqa: E402
from tally_bridge.transport.link.controller import (  # noqa: E402
    DISCONNECTED,
    LINKED,
    NOT_LINKED,
    OFF,
    WAITING_APPROVAL,
    WAITING_CONFIRM,
    LinkController,
)
from tally_bridge.transport.link.store import LinkState, MemoryLinkStore  # noqa: E402
from test_tally_bridge_link import CREDENTIAL, DEVICE, FakeLink, _echo  # noqa: E402

PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"
KEY = new_token()
URL = "https://link.test"


async def _up() -> bool:
    return True


async def _fast(_s: float) -> None:
    await asyncio.sleep(0)


def _controller(link: FakeLink, store=None, url: str = URL, copy_at=None) -> LinkController:
    return LinkController(
        store if store is not None else MemoryLinkStore(), lambda: url, _up, "0.1.0", "OFFICE-PC",
        api_factory=lambda u: link.api(), copy_at=copy_at, sleep=_fast,
    )


async def _until(check, timeout: float = 2.0) -> None:
    end = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < end, "timed out"
        await asyncio.sleep(0.005)


# --- The controller ------------------------------------------------------------------


def test_the_controller_pairs_then_waits_for_the_exact_name_then_links():
    link = FakeLink(pending=1)
    store = MemoryLinkStore()
    ctl = _controller(link, store)

    async def go():
        assert ctl.state()["mode"] == NOT_LINKED
        state, error = await ctl.pair("example.com")
        assert error is None and state["mode"] == WAITING_APPROVAL
        assert state["code"] == "WDJB-MJHT"
        await _until(lambda: ctl.state()["mode"] == WAITING_CONFIRM)
        assert ctl.state()["company"] == "Acme Traders"
        _, error = ctl.confirm("ACME TRADERS")
        assert error and store.state.confirmed is False  # rule j: the exact name
        state, error = ctl.confirm("Acme Traders")
        assert error is None and state["mode"] == LINKED and store.state.confirmed

    asyncio.run(go())


def test_the_state_never_holds_the_credential_or_the_device_code():
    link = FakeLink(pending=2)
    ctl = _controller(link)
    seen: list[str] = []

    async def go():
        seen.append(json.dumps(ctl.state()))
        state, _ = await ctl.pair("example.com")
        seen.append(json.dumps(state))
        await _until(lambda: ctl.state()["mode"] == WAITING_CONFIRM)
        seen.append(json.dumps(ctl.state()))
        seen.append(json.dumps(ctl.confirm("Acme Traders")[0]))

    asyncio.run(go())
    text = "".join(seen)
    assert CREDENTIAL not in text and DEVICE not in text


def test_the_poll_starts_only_after_the_confirmation_and_carries_copy_at():
    link = FakeLink(pending=0)
    store = MemoryLinkStore()
    ctl = _controller(link, store, copy_at=lambda: "2026-11-25T14:05:00+00:00")
    ctl.dispatch = _echo

    async def go():
        stop = asyncio.Event()
        runner = asyncio.create_task(ctl.run(stop))
        await ctl.pair("example.com")
        await _until(lambda: ctl.state()["mode"] == WAITING_CONFIRM)
        await asyncio.sleep(0.05)
        assert link.polls == []  # rule j: nothing before the confirmation
        ctl.confirm("Acme Traders")
        await _until(lambda: len(link.polls) >= 1)
        stop.set()
        await asyncio.wait_for(runner, 3)

    asyncio.run(go())
    assert link.polls[0] == {"tally": "up", "version": "0.1.0",
                             "copy_at": "2026-11-25T14:05:00+00:00"}


def test_unlink_revokes_stops_the_poll_and_clears_the_state():
    link = FakeLink()
    store = MemoryLinkStore(LinkState(CREDENTIAL, "conn-1", "Acme Traders", confirmed=True))
    ctl = _controller(link, store)
    ctl.dispatch = _echo

    async def go():
        stop = asyncio.Event()
        runner = asyncio.create_task(ctl.run(stop))
        await _until(lambda: len(link.polls) >= 1)
        state, note = await ctl.unlink()
        assert note is None and state["mode"] == NOT_LINKED
        polls = len(link.polls)
        await asyncio.sleep(0.1)
        assert len(link.polls) == polls  # the poll stopped at once
        stop.set()
        await asyncio.wait_for(runner, 3)

    asyncio.run(go())
    assert link.revokes == 1 and store.state is None


def test_a_revoke_by_the_admin_shows_disconnected():
    link = FakeLink()
    link.revoked = True
    store = MemoryLinkStore(LinkState(CREDENTIAL, "conn-1", "Acme Traders", confirmed=True))
    ctl = _controller(link, store)
    ctl.dispatch = _echo

    async def go():
        stop = asyncio.Event()
        runner = asyncio.create_task(ctl.run(stop))
        await _until(lambda: ctl.state()["mode"] == DISCONNECTED)
        stop.set()
        await asyncio.wait_for(runner, 3)

    asyncio.run(go())
    assert store.state is None


def test_link_mode_is_off_with_no_link_url():
    ctl = _controller(FakeLink(), url="")
    assert ctl.state() == {"mode": OFF}
    state, error = asyncio.run(ctl.pair("example.com"))
    assert error and state == {"mode": OFF}


def test_a_second_pairing_is_refused_while_one_runs_or_the_machine_is_linked():
    linked = MemoryLinkStore(LinkState(CREDENTIAL, "conn-1", "Acme Traders", confirmed=True))
    _, error = asyncio.run(_controller(FakeLink(), linked).pair("example.com"))
    assert error and "Unlink first" in error

    link = FakeLink(pending=10_000)
    ctl = _controller(link)

    async def go():
        await ctl.pair("example.com")
        _, error = await ctl.pair("example.com")
        await ctl.unlink()
        return error

    assert "runs already" in asyncio.run(go())


def test_a_pairing_that_fails_shows_its_error():
    ctl = _controller(FakeLink(outcome="access_denied"))

    async def go():
        await ctl.pair("example.com")
        await _until(lambda: ctl.state()["error"] is not None)
        return ctl.state()

    state = asyncio.run(go())
    assert state["mode"] == NOT_LINKED and "refused" in state["error"]


# --- The routes of the page ------------------------------------------------------


def _client(tmp_path, ctl):
    store = FileSettingsStore(tmp_path / "settings.json")
    live = LiveSettings(Settings(link_url=URL))

    class Reader:
        async def is_up(self):
            return False

        async def post(self, envelope):  # pragma: no cover
            raise AssertionError

    secrets = MemorySecretStore(None, token_sha256(KEY))
    app = build_address_app(live, secrets, Reader(), store, link=ctl)
    return TestClient(app, base_url=BASE)


def _post(c, path, body, key=KEY, origin=BASE):
    headers = {"X-Setup-Key": key}
    if origin:
        headers["Origin"] = origin
    return c.post(path, json=body, headers=headers)


@pytest.mark.parametrize("path", ["/setup/api/link/pair", "/setup/api/link/confirm",
                                  "/setup/api/link/unlink"])
@pytest.mark.parametrize(("key", "origin"), [("x" + KEY, BASE), (KEY, "http://evil.example"),
                                             (KEY, None)])
def test_each_link_route_needs_the_key_and_the_origin(tmp_path, path, key, origin):
    link = FakeLink()
    with _client(tmp_path, _controller(link)) as c:
        r = _post(c, path, {"domain": "example.com", "company": "Acme Traders"}, key, origin)
    assert r.status_code == 403
    assert link.starts == [] and link.revokes == 0


def test_the_page_pairs_confirms_and_unlinks_over_http(tmp_path):
    link = FakeLink(pending=1)
    store = MemoryLinkStore()
    ctl = _controller(link, store)
    answers: list[str] = []
    with _client(tmp_path, ctl) as c:
        r = _post(c, "/setup/api/link/pair", {"domain": "example.com"})
        answers.append(r.text)
        assert r.status_code == 200 and r.json()["link"]["code"] == "WDJB-MJHT"
        for _ in range(200):
            s = c.get("/setup/api/state", headers={"X-Setup-Key": KEY})
            answers.append(s.text)
            if s.json()["link"]["mode"] == WAITING_CONFIRM:
                break
            time.sleep(0.01)
        assert s.json()["link"]["company"] == "Acme Traders"
        wrong = _post(c, "/setup/api/link/confirm", {"company": "Other Co"})
        assert wrong.status_code == 409 and "Acme Traders" in wrong.json()["error"]
        ok = _post(c, "/setup/api/link/confirm", {"company": "Acme Traders"})
        answers.append(ok.text)
        assert ok.json()["link"]["mode"] == LINKED and store.state.confirmed
        gone = _post(c, "/setup/api/link/unlink", {})
        answers.append(gone.text)
        assert gone.json()["link"]["mode"] == NOT_LINKED and gone.json()["note"] is None
    text = "".join(answers)
    assert CREDENTIAL not in text and DEVICE not in text
    assert link.revokes == 1 and store.state is None


def test_the_link_routes_answer_409_when_link_mode_is_off(tmp_path):
    with _client(tmp_path, _controller(FakeLink(), url="")) as c:
        assert _post(c, "/setup/api/link/pair", {"domain": "example.com"}).status_code == 409
        state = c.get("/setup/api/state", headers={"X-Setup-Key": KEY}).json()
    assert state["link"] == {"mode": OFF}


@pytest.mark.parametrize("body", [{"domain": ""}, {"domain": 5}, {"domain": "x" * 300}, []])
def test_the_pair_route_refuses_a_bad_domain(tmp_path, body):
    link = FakeLink()
    with _client(tmp_path, _controller(link)) as c:
        assert _post(c, "/setup/api/link/pair", body).status_code == 400
    assert link.starts == []


def test_the_page_has_the_link_step_and_sets_no_html_from_data():
    static = ROOT / "tally_bridge" / "setup" / "static"
    page = (static / "setup.html").read_text(encoding="utf-8")
    script = (static / "setup.js").read_text(encoding="utf-8")
    for element in ("link-domain", "link-code", "link-company", "link-confirm-name",
                    "link-refuse", "link-unlink", "link-cancel"):
        assert f'id="{element}"' in page
    assert "innerHTML" not in script and "insertAdjacentHTML" not in script


# --- The copy time (§3.8.13 point 7) ----------------------------------------------


class Keys:
    """One fixed copy key, for the test."""

    def __init__(self) -> None:
        from cryptography.fernet import Fernet

        self._k = Fernet.generate_key()

    def key(self) -> bytes:
        return self._k


def test_the_copy_time_is_the_oldest_copy_and_is_read_once_per_file(tmp_path, monkeypatch):
    copy = CopyStore(tmp_path, Keys())
    now = datetime(2026, 11, 25, 14, 5, tzinfo=UTC)
    copy.save(Snapshot("Alpha Co", now))
    copy.save(Snapshot("Beta Co", now - timedelta(hours=2)))
    assert copy.oldest_copied_at(["Alpha Co", "Beta Co", "No Copy"]) == now - timedelta(hours=2)
    assert copy.oldest_copied_at(["No Copy"]) is None
    loads = []
    real = CopyStore.load
    monkeypatch.setattr(CopyStore, "load", lambda self, c: loads.append(c) or real(self, c))
    for _ in range(5):
        copy.oldest_copied_at(["Alpha Co", "Beta Co"])
    assert loads == []  # cached: no file changed
    copy.save(Snapshot("Alpha Co", now + timedelta(minutes=30)))
    copy.oldest_copied_at(["Alpha Co"])
    assert loads == ["Alpha Co"]


# --- Verifier round 1: the second guards are fenced too ---------------------------


def test_an_unconfirmed_link_makes_the_controller_wait_and_not_retry(caplog):
    """_ready checks the confirmation. Without it, run_link refuses each try
    (NotConfirmed), and the controller would fail and log again and again."""
    link = FakeLink()
    store = MemoryLinkStore(LinkState(CREDENTIAL, "conn-1", "Acme Traders"))
    ctl = _controller(link, store)
    ctl.dispatch = _echo

    async def go():
        stop = asyncio.Event()
        runner = asyncio.create_task(ctl.run(stop))
        await asyncio.sleep(0.2)
        stop.set()
        await asyncio.wait_for(runner, 3)

    with caplog.at_level("ERROR", logger="tally_bridge.link"):
        asyncio.run(go())
    assert link.polls == []
    assert not [r for r in caplog.records if "link loop failed" in r.getMessage()]


def test_unlink_ends_a_held_poll_at_once():
    """Metorite holds each poll up to 25 s. The unlink stops the loop now,
    not at the end of the held poll."""
    link = FakeLink()
    link.hold = 5.0
    store = MemoryLinkStore(LinkState(CREDENTIAL, "conn-1", "Acme Traders", confirmed=True))
    ctl = _controller(link, store)
    ctl.dispatch = _echo

    async def go():
        stop = asyncio.Event()
        runner = asyncio.create_task(ctl.run(stop))
        await _until(lambda: ctl._loop_stop is not None)
        await asyncio.sleep(0.05)  # the poll is held now
        await ctl.unlink()
        await _until(lambda: ctl._loop_stop is None, timeout=0.3)
        stop.set()
        await asyncio.wait_for(runner, 3)

    asyncio.run(go())


# --- Review round 1 (Opus) ---------------------------------------------------------


def test_the_wait_polls_only_the_link_block_and_never_asks_tallyprime(tmp_path):
    """Finding 1: the 3 s timer read the whole state, rebuilt the company
    ticks and asked TallyPrime each time."""
    script = (ROOT / "tally_bridge" / "setup" / "static" / "setup.js").read_text(encoding="utf-8")
    assert "setInterval(loadLink, 3000)" in script and "setInterval(load," not in script
    asked: list[str] = []

    class Reader:
        async def is_up(self):
            asked.append("is_up")
            return False

        async def post(self, envelope):  # pragma: no cover
            raise AssertionError

    live = LiveSettings(Settings(link_url=URL))
    app = build_address_app(live, MemorySecretStore(None, token_sha256(KEY)), Reader(),
                            FileSettingsStore(tmp_path / "s.json"), link=_controller(FakeLink()))
    with TestClient(app, base_url=BASE) as c:
        ok = c.get("/setup/api/link/state", headers={"X-Setup-Key": KEY})
        no = c.get("/setup/api/link/state", headers={"X-Setup-Key": "x" + KEY})
    assert ok.status_code == 200 and ok.json() == {"link": _controller(FakeLink()).state()}
    assert no.status_code == 403 and asked == []


def test_a_pairing_can_be_cancelled_from_the_code_box():
    """Finding 2: a pairing that runs can be cancelled (unlink cancels it)."""
    link = FakeLink(pending=10_000)
    ctl = _controller(link)

    async def go():
        state, _ = await ctl.pair("example.com")
        assert state["mode"] == WAITING_APPROVAL
        state, _ = await ctl.unlink()
        await asyncio.sleep(0.01)
        return state, ctl.state()

    state, later = asyncio.run(go())
    assert state["mode"] == NOT_LINKED and later["mode"] == NOT_LINKED
    page = (ROOT / "tally_bridge" / "setup" / "static" / "setup.html").read_text(encoding="utf-8")
    code_box = page.split('id="link-code-box"', 1)[1].split("</div>", 1)[0]
    assert 'id="link-cancel"' in code_box


def test_an_unreadable_link_is_never_read_as_no_link():
    """Finding 3: a scanner holds link.bin. A pairing must not replace a
    confirmed credential that it cannot read (it would stay live)."""

    class Locked(MemoryLinkStore):
        locked = True

        def load(self):
            if self.locked:
                raise OSError("sharing violation")
            return super().load()

    store = Locked(LinkState(CREDENTIAL, "conn-1", "Acme Traders", confirmed=True))
    link = FakeLink()
    ctl = _controller(link, store)
    state, error = asyncio.run(ctl.pair("example.com"))
    assert error and "cannot be read" in error and "cannot be read" in state["error"]
    assert link.starts == []
    store.locked = False
    assert store.state.connector_id == "conn-1" and store.state.confirmed


def test_a_confirm_from_the_command_line_starts_the_poll_at_the_next_check(monkeypatch):
    """The CLI does not wake the controller; it looks at the store again."""
    from tally_bridge.transport.link import controller as ctl_module

    monkeypatch.setattr(ctl_module, "RECHECK_S", 0.05)
    link = FakeLink()
    store = MemoryLinkStore(LinkState(CREDENTIAL, "conn-1", "Acme Traders"))
    ctl = _controller(link, store)
    ctl.dispatch = _echo

    async def go():
        stop = asyncio.Event()
        runner = asyncio.create_task(ctl.run(stop))
        await asyncio.sleep(0.1)
        assert link.polls == []
        store.save(store.state.confirm())  # what `confirm-link` writes
        await _until(lambda: len(link.polls) >= 1)
        stop.set()
        await asyncio.wait_for(runner, 3)

    asyncio.run(go())


# --- Review round 2 (Opus) ---------------------------------------------------------


class _Locked(MemoryLinkStore):
    """A store that a scanner holds while ``locked`` is True."""

    locked = False

    def load(self):
        if self.locked:
            raise OSError("sharing violation")
        return super().load()


def test_an_unlink_on_an_unreadable_store_changes_nothing_and_says_so():
    """Round 2: before, the unlink said that it worked, stopped the poll, and
    the 30 s check then started the same link again with no revoke."""
    link = FakeLink()
    store = _Locked(LinkState(CREDENTIAL, "conn-1", "Acme Traders", confirmed=True))
    ctl = _controller(link, store)
    ctl.dispatch = _echo

    async def go():
        stop = asyncio.Event()
        runner = asyncio.create_task(ctl.run(stop))
        await _until(lambda: len(link.polls) >= 1)
        store.locked = True
        _state, note = await ctl.unlink()
        store.locked = False
        still_polling = ctl._loop_stop is not None and not ctl._loop_stop.is_set()
        stop.set()
        await asyncio.wait_for(runner, 3)
        return note, still_polling

    note, still_polling = asyncio.run(go())
    assert note is not None and "cannot be read" in note
    assert link.revokes == 0 and store.state is not None  # nothing changed
    assert still_polling  # the unlink did not stop the poll


def test_a_confirm_on_an_unreadable_store_says_it_cannot_be_read():
    store = _Locked(LinkState(CREDENTIAL, "conn-1", "Acme Traders"))
    store.locked = True
    _, error = _controller(FakeLink(), store).confirm("Acme Traders")
    store.locked = False
    assert "cannot be read" in error and store.state.confirmed is False


def test_two_pair_requests_at_once_start_one_pairing():
    """Round 2: the revoke of an old pairing awaited before the task existed,
    so a second request passed the busy check."""
    link = FakeLink(pending=10_000)
    store = MemoryLinkStore(LinkState(CREDENTIAL, "conn-0", "Old Co"))
    ctl = _controller(link, store)

    async def go():
        a, b = await asyncio.gather(ctl.pair("example.com"), ctl.pair("example.com"))
        await ctl.unlink()
        return a, b

    a, b = asyncio.run(go())
    errors = [r[1] for r in (a, b)]
    assert errors.count(None) == 1 and any(e and "runs already" in e for e in errors)
    assert len(link.starts) == 1
