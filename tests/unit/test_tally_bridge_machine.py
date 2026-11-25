"""TLY-2d: the two machine switches of the setup page (setup guide, step 6).

The route, the state, the page, and the callback of the entry point.
"""

from __future__ import annotations

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


def _body(start=False, exe="", awake=False) -> dict:
    return {"start_tally_at_signin": start, "tally_exe": exe, "keep_awake": awake}


def test_the_start_switch_saves_and_reloads(tmp_path):
    with _client(tmp_path, _Link()) as c:
        r = _post(c, _body(start=True, exe=EXE))
        assert r.status_code == 200 and r.json()["note"] is None
        assert _state(c)["machine"] == {
            "start_tally_at_signin": True,
            "tally_exe": EXE,
            "keep_awake": False,
        }
    saved = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert saved["START_TALLY_AT_SIGNIN"] == "true" and saved["TALLY_EXE"] == EXE
    assert FileSettingsStore(tmp_path / "settings.json").load().start_tally_at_signin is True
    with _client(tmp_path, _Link()) as c2:  # a new app over the same store
        assert _state(c2)["machine"]["start_tally_at_signin"] is True
        assert _state(c2)["machine"]["tally_exe"] == EXE
        assert _post(c2, _body(start=False, exe=EXE)).status_code == 200
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


@pytest.mark.parametrize(
    "body",
    [
        _body(start=True, exe=""),
        _body(start=True, exe=r"C:\Tally\tally.bat"),
        _body(exe=r"C:\Tally\"tally.exe"),
        _body(exe=r"C:\Tally\notes.txt"),
    ],
)
def test_a_bad_path_gets_400_and_saves_nothing(tmp_path, body):
    with _client(tmp_path, _Link()) as c:
        r = _post(c, body)
        assert r.status_code == 400 and "TALLY_EXE" in r.json()["error"]
        assert _state(c)["machine"]["start_tally_at_signin"] is False
    assert not (tmp_path / "settings.json").exists()


@pytest.mark.parametrize(
    "body",
    [
        [],
        {"start_tally_at_signin": "yes", "tally_exe": "", "keep_awake": False},
        {"start_tally_at_signin": False, "tally_exe": 5, "keep_awake": False},
        {"start_tally_at_signin": False, "tally_exe": "", "keep_awake": 1},
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

    with _client(tmp_path, _Link(), apply) as c:
        r = _post(c, _body(start=True, exe=EXE, awake=True))
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
    apply = entry._machine_applier(True, awake, runner)
    assert apply(Settings(start_tally_at_signin=True, tally_exe=EXE, keep_awake=True)) is None
    assert awake.calls == ["start"]
    assert _has(runner, "/Create")


def test_the_callback_removes_the_task_and_stops_keep_awake():
    awake, runner = _Awake(), _Runner(task=True)
    apply = entry._machine_applier(True, awake, runner)
    assert apply(Settings(keep_awake=False)) is None
    assert awake.calls == ["stop"]
    assert _has(runner, "/Delete")


def test_the_callback_leaves_the_task_alone_when_none_exists_and_none_is_wanted():
    runner = _Runner(task=False)
    assert entry._machine_applier(True, _Awake(), runner)(Settings()) is None
    assert not _has(runner, "/Create") and not _has(runner, "/Delete")


def test_a_failed_task_call_says_to_run_as_an_administrator():
    runner = _Runner(fail=True)
    apply = entry._machine_applier(True, _Awake(), runner)
    note = apply(Settings(start_tally_at_signin=True, tally_exe=EXE))
    assert note == "Run the setup as an administrator to change the sign-in task."
