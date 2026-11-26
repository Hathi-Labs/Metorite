"""TLY-2d: the two machine switches of the setup page (setup guide, step 6).

The route, the state, the page, and the callback of the entry point.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
ROOT = REPO / "apps" / "connectors" / "tally_bridge"
sys.path.insert(0, str(ROOT))

from starlette.testclient import TestClient  # noqa: E402
from tally_bridge import __main__ as entry  # noqa: E402
from tally_bridge.secret_store import MemorySecretStore, new_token, token_sha256  # noqa: E402
from tally_bridge.settings import FileSettingsStore, LiveSettings, Settings  # noqa: E402
from tally_bridge.transport import build_address_app  # noqa: E402

PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"
KEY = new_token()
EXE = r"C:\Program Files\TallyPrime\tally.exe"
STATIC = ROOT / "tally_bridge" / "setup" / "static"


class _Link:
    """The one thing the page asks of the link controller: its state."""

    def __init__(self, mode: str = "not_linked") -> None:
        self.mode = mode

    def state(self) -> dict:
        return {"mode": self.mode}


class _Reader:
    async def is_up(self) -> bool:
        return False

    async def post(self, envelope):  # pragma: no cover
        raise AssertionError


def _admin_path(tmp_path, exe: str = EXE) -> None:
    """What an administrator does: TALLY_EXE in the settings file, which a user
    cannot write. The page never sets it."""
    FileSettingsStore(tmp_path / "settings.json").save(Settings(tally_exe=exe))


def _client(tmp_path, link=None, on_machine_saved=None) -> TestClient:
    store = FileSettingsStore(tmp_path / "settings.json")
    live = LiveSettings(store.load() if (tmp_path / "settings.json").exists() else Settings())
    secrets = MemorySecretStore(None, token_sha256(KEY))
    app = build_address_app(
        live, secrets, _Reader(), store, link=link, on_machine_saved=on_machine_saved
    )
    return TestClient(app, base_url=BASE)


def _post(c, body, key=KEY, origin=BASE):
    headers = {"X-Setup-Key": key}
    if origin:
        headers["Origin"] = origin
    return c.post("/setup/api/machine", json=body, headers=headers)


def _state(c) -> dict:
    return c.get("/setup/api/state", headers={"X-Setup-Key": KEY}).json()


def _body(start=False, awake=False) -> dict:
    return {"start_tally_at_signin": start, "keep_awake": awake}


def test_the_start_switch_saves_and_reloads(tmp_path):
    _admin_path(tmp_path)
    with _client(tmp_path, _Link()) as c:
        r = _post(c, _body(start=True))
        assert r.status_code == 200 and r.json()["note"] is None
        assert _state(c)["machine"] == {
            "start_tally_at_signin": True,
            "tally_exe": EXE,
            "keep_awake": False,
            "problem": None,
        }
    saved = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert saved["START_TALLY_AT_SIGNIN"] == "true" and saved["TALLY_EXE"] == EXE
    assert FileSettingsStore(tmp_path / "settings.json").load().start_tally_at_signin is True
    with _client(tmp_path, _Link()) as c2:  # a new app over the same store
        assert _state(c2)["machine"]["start_tally_at_signin"] is True
        assert _state(c2)["machine"]["tally_exe"] == EXE
        assert _post(c2, _body(start=False)).status_code == 200
        assert _state(c2)["machine"]["start_tally_at_signin"] is False
    assert FileSettingsStore(tmp_path / "settings.json").load().start_tally_at_signin is False


def test_the_keep_awake_switch_saves_and_reloads(tmp_path):
    with _client(tmp_path, _Link()) as c:
        assert _state(c)["machine"]["keep_awake"] is False
        assert _post(c, _body(awake=True)).status_code == 200
        assert _state(c)["machine"]["keep_awake"] is True
    saved = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert saved["KEEP_AWAKE"] == "true"
    assert FileSettingsStore(tmp_path / "settings.json").load().keep_awake is True
    with _client(tmp_path, _Link()) as c2:
        assert _state(c2)["machine"]["keep_awake"] is True
        assert _post(c2, _body(awake=False)).status_code == 200
        assert _state(c2)["machine"]["keep_awake"] is False
    assert FileSettingsStore(tmp_path / "settings.json").load().keep_awake is False


def test_the_start_switch_needs_a_path_that_an_administrator_set(tmp_path):
    """Review P0: the page never sets the path. With no path saved, start is
    refused, and nothing is saved."""
    with _client(tmp_path, _Link()) as c:
        r = _post(c, _body(start=True))
        assert r.status_code == 409 and "administrator" in r.json()["error"]
        assert _state(c)["machine"]["start_tally_at_signin"] is False
    assert not (tmp_path / "settings.json").exists()


@pytest.mark.parametrize("exe", [r"C:\Users\bob\tally.exe", "", r"D:\TallyPrime\tally.exe"])
def test_the_page_cannot_change_the_path(tmp_path, exe):
    """Review P0: a person at the page (maybe not an administrator) put their
    own tally.exe in their profile. The sign-in task would run it for every
    person who signs in. A body path other than the saved one is refused."""
    _admin_path(tmp_path)
    before = (tmp_path / "settings.json").read_text(encoding="utf-8")
    with _client(tmp_path, _Link()) as c:
        r = _post(c, {"start_tally_at_signin": True, "tally_exe": exe, "keep_awake": False})
        assert r.status_code == 403 and "administrator" in r.json()["error"]
    assert (tmp_path / "settings.json").read_text(encoding="utf-8") == before


def test_the_saved_path_may_come_back_unchanged(tmp_path):
    _admin_path(tmp_path)
    with _client(tmp_path, _Link()) as c:
        r = _post(c, {"start_tally_at_signin": True, "tally_exe": EXE, "keep_awake": False})
    assert r.status_code == 200 and r.json()["machine"]["tally_exe"] == EXE


@pytest.mark.parametrize(
    "body",
    [
        [],
        {"start_tally_at_signin": "yes", "keep_awake": False},
        {"start_tally_at_signin": False, "keep_awake": 1},
        {"start_tally_at_signin": False},
    ],
)
def test_a_body_of_the_wrong_type_gets_400(tmp_path, body):
    with _client(tmp_path, _Link()) as c:
        assert _post(c, body).status_code == 400


@pytest.mark.parametrize(
    ("key", "origin"), [("x" + KEY, BASE), (KEY, "http://evil.example"), (KEY, None)]
)
def test_the_route_needs_the_key_and_the_origin(tmp_path, key, origin):
    with _client(tmp_path, _Link()) as c:
        assert _post(c, _body(awake=True), key, origin).status_code == 403
    assert not (tmp_path / "settings.json").exists()


@pytest.mark.parametrize("link", [None, _Link("off")])
def test_with_link_mode_off_the_route_gets_409_and_state_has_no_machine(tmp_path, link):
    with _client(tmp_path, link) as c:
        assert _post(c, _body(awake=True)).status_code == 409
        assert "machine" not in _state(c)
    assert not (tmp_path / "settings.json").exists()


def test_the_callback_gets_the_new_settings_and_its_error_is_the_note(tmp_path):
    seen: list[Settings] = []

    def apply(new: Settings) -> str | None:
        seen.append(new)
        return "No luck."

    _admin_path(tmp_path)
    with _client(tmp_path, _Link(), apply) as c:
        r = _post(c, _body(start=True, awake=True))
    assert r.status_code == 200 and r.json()["note"] == "No luck."
    assert len(seen) == 1
    assert (seen[0].start_tally_at_signin, seen[0].tally_exe, seen[0].keep_awake) == (
        True,
        EXE,
        True,
    )


def test_the_page_has_the_five_elements_and_the_script_never_uses_innerhtml():
    page = (STATIC / "setup.html").read_text(encoding="utf-8")
    for name in ("machine-start", "machine-exe", "machine-awake", "machine-save", "machine-line"):
        assert f'id="{name}"' in page
    assert 'id="machine" hidden' in page
    script = (STATIC / "setup.js").read_text(encoding="utf-8")
    assert "/setup/api/machine" in script
    assert "innerHTML" not in script


# --- The callback of the entry point ---------------------------------------------


class _Awake:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def start(self) -> bool:
        self.calls.append("start")
        return True

    def stop(self) -> None:
        self.calls.append("stop")


class _Runner:
    """Plays schtasks. *task* says whether the sign-in task exists."""

    def __init__(self, task: bool = False, fail: bool = False) -> None:
        self.commands: list[list[str]] = []
        self.task = task
        self.fail = fail

    def __call__(self, command: list[str]) -> int:
        self.commands.append(list(command))
        if "/Query" in command:
            return 0 if self.task else 1
        return 5 if self.fail else 0


def _has(runner: _Runner, flag: str) -> bool:
    return any(flag in c for c in runner.commands)


def test_without_windows_there_is_no_callback():
    assert entry._machine_applier(False, _Awake(), _Runner()) is None


def test_the_callback_makes_the_task_and_starts_keep_awake():
    awake, runner = _Awake(), _Runner()
    apply = entry._machine_applier(True, awake, runner, lambda path: None)
    settings = Settings(start_tally_at_signin=True, tally_exe=EXE, keep_awake=True)
    assert asyncio.run(apply(settings)) is None
    assert awake.calls == ["start"]
    assert _has(runner, "/Create")


def test_the_callback_removes_the_task_and_stops_keep_awake():
    awake, runner = _Awake(), _Runner(task=True)
    apply = entry._machine_applier(True, awake, runner, lambda path: None)
    assert asyncio.run(apply(Settings(keep_awake=False))) is None
    assert awake.calls == ["stop"]
    assert _has(runner, "/Delete")


def test_the_callback_leaves_the_task_alone_when_none_exists_and_none_is_wanted():
    runner = _Runner(task=False)
    assert asyncio.run(entry._machine_applier(True, _Awake(), runner)(Settings())) is None
    assert not _has(runner, "/Create") and not _has(runner, "/Delete")


def test_a_failed_task_call_gives_a_note():
    runner = _Runner(fail=True)
    apply = entry._machine_applier(True, _Awake(), runner)
    note = asyncio.run(apply(Settings(start_tally_at_signin=True, tally_exe=EXE)))
    assert note and "sign-in task" in note


def test_an_error_of_the_task_call_is_a_note_not_a_500():
    """Review P2: a runner that raises (a temp file that cannot be written)."""

    def broken(command):
        raise OSError("disk full")

    apply = entry._machine_applier(True, _Awake(), broken)
    note = asyncio.run(apply(Settings(start_tally_at_signin=True, tally_exe=EXE)))
    assert note and "did not change" in note


def test_schtasks_runs_off_the_event_loop_and_keep_awake_on_it():
    """Review P2: schtasks blocks; the loop must stay free. SetThreadExecution-
    State binds to the calling thread, so keep-awake stays on the loop thread."""
    import threading

    threads: dict[str, int] = {}

    class Awake(_Awake):
        def start(self):
            threads["awake"] = threading.get_ident()
            return super().start()

    def runner(command):
        threads["schtasks"] = threading.get_ident()
        return 1 if "/Query" in command else 0

    async def go():
        threads["loop"] = threading.get_ident()
        apply = entry._machine_applier(True, Awake(), runner)
        await apply(Settings(start_tally_at_signin=True, tally_exe=EXE, keep_awake=True))

    asyncio.run(go())
    assert threads["awake"] == threads["loop"] != threads["schtasks"]


@pytest.mark.parametrize("exe", [r"C:\Users\Public\evil.exe", r"tally.exe",
                                 r"\\server\share\tally.exe", r"C:tally.exe"])
def test_the_path_must_be_tallyprime_on_a_drive(exe):
    """TLY-2d: an administrator sets the path, and the sign-in task runs it
    for each person who signs in. So only a full path to tally.exe on a drive
    letter passes, as a second guard behind the admin-only settings file."""
    from tally_bridge.settings import Settings, SettingsError

    with pytest.raises(SettingsError):
        Settings(start_tally_at_signin=True, tally_exe=exe).validate()
    Settings(start_tally_at_signin=True,
             tally_exe=r"D:\Apps\TallyPrime\TALLY.EXE").validate()
