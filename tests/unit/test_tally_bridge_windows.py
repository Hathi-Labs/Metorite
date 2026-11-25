"""TLY-5: the Windows side of the Tally bridge (brief §6.7, build step 4).

Each Windows call sits behind a seam, so these tests run on Linux with a
fake: a protector for DPAPI, a runner for the commands, and a power API for
keep-awake. The one test of the real DPAPI runs only on Windows (the
``windows-latest`` job of TLY-5 PR C).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
ROOT = REPO / "apps" / "connectors" / "tally_bridge"
sys.path.insert(0, str(ROOT))

from tally_bridge import __main__ as cli  # noqa: E402
from tally_bridge.secret_store import token_sha256  # noqa: E402
from tally_bridge.settings import (  # noqa: E402
    FileSettingsStore,
    Settings,
    SettingsError,
    settings_from_mapping,
    settings_to_mapping,
)
from tally_bridge.windows import dpapi, stores, system  # noqa: E402

TAG = b"FAKE-DPAPI:"
HASH = token_sha256("a-token")


class FakeProtector:
    """Reversible, and refuses a blob that it did not make."""

    def protect(self, data: bytes) -> bytes:
        return TAG + bytes(b ^ 0x5A for b in data)

    def unprotect(self, blob: bytes) -> bytes:
        if not blob.startswith(TAG):
            raise dpapi.ProtectError("not ours")
        return bytes(b ^ 0x5A for b in blob[len(TAG):])


# --- Settings ----------------------------------------------------------------------


@pytest.mark.parametrize("raw,value", [
    ("true", True), ("1", True), ("ON", True), ("yes", True),
    ("false", False), ("0", False), ("off", False), ("No", False),
])
def test_an_on_off_setting_parses(raw, value):
    s = settings_from_mapping({"KEEP_AWAKE": raw})
    assert s.keep_awake is value


def test_an_unknown_on_off_value_is_refused_not_read_as_true():
    with pytest.raises(SettingsError):
        settings_from_mapping({"KEEP_AWAKE": "maybe"})


def test_start_at_signin_needs_the_tally_program():
    with pytest.raises(SettingsError):
        Settings(start_tally_at_signin=True).validate()
    with pytest.raises(SettingsError):
        Settings(tally_exe=r"C:\Tally\tally.bat").validate()
    ok = Settings(start_tally_at_signin=True, tally_exe=r"C:\TallyPrime\tally.exe").validate()
    back = settings_from_mapping(settings_to_mapping(ok))
    assert back.start_tally_at_signin and back.tally_exe == ok.tally_exe
    assert settings_to_mapping(Settings())["KEEP_AWAKE"] == "false"


# --- DPAPI stores ------------------------------------------------------------------


def test_the_real_protector_refuses_off_windows():
    if sys.platform == "win32":
        pytest.skip("this is the Linux side")
    with pytest.raises(dpapi.ProtectError):
        dpapi.DpapiProtector()


@pytest.mark.skipif(sys.platform != "win32", reason="real DPAPI exists on Windows only")
def test_the_real_dpapi_round_trip_at_machine_scope(tmp_path):
    p = dpapi.DpapiProtector()
    blob = p.protect(b"hello")
    assert blob != b"hello" and p.unprotect(blob) == b"hello"
    store = stores.DpapiSecretStore(tmp_path / "s.bin", p)
    store.set_read_token_sha256(HASH)
    assert store.read_token_sha256() == HASH
    with pytest.raises(dpapi.ProtectError):
        dpapi.DpapiProtector(entropy=b"another program").unprotect(blob)


def test_the_secret_store_round_trip_and_no_plain_hash_on_disk(tmp_path):
    path = tmp_path / "TallyBridge" / stores.SECRETS_FILE
    store = stores.DpapiSecretStore(path, FakeProtector())
    assert store.read_token_sha256() is None and store.setup_secret_sha256() is None
    store.set_read_token_sha256(HASH)
    store.set_setup_secret_sha256(token_sha256("setup"))
    again = stores.DpapiSecretStore(path, FakeProtector())
    assert again.read_token_sha256() == HASH
    assert again.setup_secret_sha256() == token_sha256("setup")
    assert HASH.encode() not in path.read_bytes()
    assert path.read_bytes().startswith(TAG)


def test_a_damaged_or_foreign_file_reads_as_empty(tmp_path):
    path = tmp_path / stores.SECRETS_FILE
    path.write_bytes(b"not a dpapi blob")
    store = stores.DpapiSecretStore(path, FakeProtector())
    assert store.read_token_sha256() is None
    store.set_read_token_sha256(HASH)
    assert store.read_token_sha256() == HASH


def test_the_secret_store_refuses_a_value_that_is_not_a_hash(tmp_path):
    store = stores.DpapiSecretStore(tmp_path / "s.bin", FakeProtector())
    with pytest.raises(ValueError):
        store.set_read_token_sha256("a-plain-token")


def test_delete_removes_the_hashes(tmp_path):
    path = tmp_path / "s.bin"
    store = stores.DpapiSecretStore(path, FakeProtector())
    store.set_read_token_sha256(HASH)
    store.delete()
    assert not path.exists() and store.read_token_sha256() is None


def test_the_copy_key_is_made_once_and_kept_protected(tmp_path):
    path = tmp_path / stores.COPY_KEY_FILE
    first = stores.DpapiKeyProvider(path, FakeProtector()).key()
    assert stores.DpapiKeyProvider(path, FakeProtector()).key() == first
    assert first not in path.read_bytes()
    path.write_bytes(b"damaged")
    fresh = stores.DpapiKeyProvider(path, FakeProtector()).key()
    assert fresh != first and len(fresh) == 44


def test_the_data_folder_is_under_programdata():
    assert stores.data_dir({"PROGRAMDATA": r"C:\ProgramData"}) == (
        Path(r"C:\ProgramData") / "TallyBridge")
    with pytest.raises(RuntimeError):
        stores.data_dir({})


# --- The commands ------------------------------------------------------------------


def test_the_signin_task_command_word_for_word():
    exe = r"C:\Program Files\TallyPrime\tally.exe"
    assert system.startup_on(exe) == [
        "schtasks", "/Create", "/TN", "Tally Bridge - Start TallyPrime",
        "/TR", f'"{exe}"', "/SC", "ONLOGON", "/RL", "LIMITED", "/F"]
    assert system.startup_off() == [
        "schtasks", "/Delete", "/TN", "Tally Bridge - Start TallyPrime", "/F"]


@pytest.mark.parametrize("bad", ["", r"C:\Tally\tally.bat", 'C:\\x".exe', "tally"])
def test_the_signin_task_refuses_a_path_that_is_not_a_program(bad):
    with pytest.raises(ValueError):
        system.startup_on(bad)


def test_the_firewall_rule_blocks_tally_and_the_bridge_from_the_network():
    on = system.firewall_on()
    assert on[:5] == ["netsh", "advfirewall", "firewall", "add", "rule"]
    assert "dir=in" in on and "action=block" in on and "protocol=TCP" in on
    assert "localport=9000,8765" in on
    assert system.firewall_off() == [
        "netsh", "advfirewall", "firewall", "delete", "rule",
        "name=Tally Bridge - block network access"]


class FakePower:
    def __init__(self) -> None:
        self.calls: list[int] = []

    def SetThreadExecutionState(self, flags: int) -> int:
        self.calls.append(flags)
        return 1


def test_keep_awake_asks_once_and_lets_go_on_stop():
    api = FakePower()
    awake = system.KeepAwake(api)
    assert awake.start() and awake.start()
    assert api.calls == [system.ES_CONTINUOUS | system.ES_SYSTEM_REQUIRED]
    awake.stop()
    awake.stop()
    assert api.calls[-1] == system.ES_CONTINUOUS and len(api.calls) == 2


def test_keep_awake_does_nothing_off_windows():
    if sys.platform == "win32":
        pytest.skip("this is the Linux side")
    awake = system.KeepAwake()
    assert awake.start() is False
    awake.stop()


# --- The CLI -----------------------------------------------------------------------


@pytest.fixture
def windows_box(tmp_path, monkeypatch):
    """A fake %ProgramData%, and the fake protector in place of DPAPI."""
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path))
    monkeypatch.setattr(dpapi, "DpapiProtector", FakeProtector)
    folder = tmp_path / "TallyBridge"
    return folder


def _args() -> argparse.Namespace:
    return argparse.Namespace(windows=True, settings_file=None, dev_secret_file=None,
                              dev_copy_key_file=None)


def test_windows_stores_are_the_dpapi_ones(windows_box):
    settings_store, secrets = cli._stores(_args())
    assert isinstance(secrets, stores.DpapiSecretStore)
    secrets.set_setup_secret_sha256(HASH)
    assert (windows_box / stores.SECRETS_FILE).exists()
    settings_store.save(Settings(keep_awake=True))
    assert json.loads((windows_box / "settings.json").read_text())["KEEP_AWAKE"] == "true"


def test_apply_windows_runs_the_signin_task_and_one_firewall_rule(windows_box):
    FileSettingsStore(windows_box / "settings.json").save(
        Settings(start_tally_at_signin=True, tally_exe=r"C:\TallyPrime\tally.exe"))
    ran: list[list[str]] = []

    def runner(argv):
        ran.append(list(argv))
        return 0

    assert cli._apply_windows(_args(), runner=runner) == 0
    assert system.startup_on(r"C:\TallyPrime\tally.exe") in ran
    assert ran[-2:] == [system.firewall_off(), system.firewall_on()]


def test_apply_windows_removes_the_task_when_the_setting_is_off(windows_box):
    FileSettingsStore(windows_box / "settings.json").save(Settings())
    ran: list[list[str]] = []

    def runner(argv):
        ran.append(list(argv))
        return 0  # the query finds the task

    cli._apply_windows(_args(), runner=runner)
    assert system.startup_off() in ran
    assert not any(a[:2] == ["schtasks", "/Create"] for a in ran)


def test_apply_windows_reports_a_failed_change(windows_box, capsys):
    FileSettingsStore(windows_box / "settings.json").save(Settings())
    assert cli._apply_windows(_args(), runner=lambda argv: 1) == 1
    assert "administrator" in capsys.readouterr().out


def test_uninstall_windows_undoes_everything(windows_box, tmp_path):
    copy_dir = tmp_path / "copy"
    copy_dir.mkdir()
    (copy_dir / "abc.copy").write_bytes(b"x")
    FileSettingsStore(windows_box / "settings.json").save(Settings(copy_dir=str(copy_dir)))
    _settings, secrets = cli._stores(_args())
    secrets.set_read_token_sha256(HASH)
    stores.DpapiKeyProvider(windows_box / stores.COPY_KEY_FILE, FakeProtector()).key()
    ran: list[list[str]] = []
    assert cli._uninstall_windows(_args(), runner=lambda a: ran.append(list(a)) or 0) == 0
    assert system.startup_off() in ran and system.firewall_off() in ran
    assert not (windows_box / stores.SECRETS_FILE).exists()
    assert not (windows_box / stores.COPY_KEY_FILE).exists()
    assert list(copy_dir.iterdir()) == []


# --- Verifier findings: the flags, the entropy, the stop on a raise --------------


def test_the_dpapi_flags_are_machine_scope_and_silent():
    """Machine scope lets the service account and the person at the desk read
    the same blob. UI_FORBIDDEN keeps a service from waiting on a prompt."""
    assert dpapi.CRYPTPROTECT_LOCAL_MACHINE == 0x4
    assert dpapi.CRYPTPROTECT_UI_FORBIDDEN == 0x1
    assert dpapi.FLAGS == 0x5


def test_the_entropy_ties_each_blob_to_this_program():
    assert dpapi.ENTROPY == b"tally-bridge/dpapi/v1"


def test_keep_awake_stops_when_the_server_raises():
    api = FakePower()
    awake = system.KeepAwake(api)

    def boom():
        raise RuntimeError("the server stopped")

    with pytest.raises(RuntimeError):
        cli._awake_while(True, boom, awake)
    assert api.calls == [system.ES_CONTINUOUS | system.ES_SYSTEM_REQUIRED, system.ES_CONTINUOUS]


def test_keep_awake_off_asks_nothing():
    api = FakePower()
    cli._awake_while(False, lambda: None, system.KeepAwake(api))
    assert api.calls == []


def test_delete_all_removes_only_its_own_temporary_files(tmp_path):
    from tally_bridge.localcopy import CopyStore, MemoryKeyProvider

    folder = tmp_path / "copy"
    folder.mkdir()
    own = folder / ("a" * 32 + ".tmp")
    theirs = folder / "report.tmp"
    for f in (own, theirs, folder / ("b" * 32 + ".copy")):
        f.write_bytes(b"x")
    assert CopyStore(folder, MemoryKeyProvider()).delete_all() == 1
    assert not own.exists() and theirs.exists()


def test_a_child_command_gets_only_the_windows_variables_it_needs():
    """test_child_env_seam.py names this file in LOCAL_ALLOWLISTS: each spawn
    passes env=child_env(), and no secret of the bridge reaches the child."""
    env = {"SystemRoot": r"C:\Windows", "Path": r"C:\Windows\system32",
           "TALLY_BRIDGE_READ_TOKEN_SHA256": "a" * 64, "SECRET": "x"}
    assert system.child_env(env) == {"SYSTEMROOT": r"C:\Windows", "PATH": r"C:\Windows\system32"}
