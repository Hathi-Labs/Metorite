"""TLY-5 PR B: the Windows service and the installer build of the Tally bridge.

The service class needs ``pywin32`` and Windows. Its run loop
(``ServiceRunner``) is plain Python, and these tests drive it on Linux. The
installer files are text, so these tests read them: the Inno Setup script must
run the same restart rule as the code, in an order that works, and the
uninstall must undo every change (brief §6.7).
"""

from __future__ import annotations

import asyncio
import importlib.util
import re
import sys
import threading
import time
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
ROOT = REPO / "apps" / "connectors" / "tally_bridge"
INSTALLER = ROOT / "installer"
sys.path.insert(0, str(ROOT))

from tally_bridge import __main__ as cli  # noqa: E402
from tally_bridge.windows import service  # noqa: E402

ISS = (INSTALLER / "tally_bridge.iss").read_text(encoding="utf-8")


def _section(name: str) -> list[str]:
    """The lines of one ``[Section]`` of the Inno script, comments left out."""
    m = re.search(rf"^\[{name}\]\n(.*?)(?=^\[|\Z)", ISS, re.M | re.S)
    assert m, name
    return [ln for ln in m.group(1).splitlines() if ln.strip() and not ln.startswith(";")]


# --- The service runner ------------------------------------------------------------


def test_the_runner_serves_until_stop():
    started = threading.Event()

    def serve(stop):
        started.set()
        stop.wait()

    runner = service.ServiceRunner(serve)
    runner.start()
    assert started.wait(2)
    assert runner.stop(timeout_s=2) is True
    assert runner.error is None


def test_an_error_of_the_server_ends_the_runner_and_is_kept():
    def serve(stop):
        raise OSError("port 8765 is in use")

    runner = service.ServiceRunner(serve)
    runner.start()
    assert runner.wait(2) is True
    assert isinstance(runner.error, OSError)


def test_a_runner_that_never_started_waits_for_nothing():
    assert service.ServiceRunner(lambda stop: None).stop(timeout_s=0.1) is True


def test_the_recovery_rule_word_for_word():
    assert service.recovery_commands() == [
        ["sc", "failure", "TallyBridge", "reset=", "86400", "actions=",
         "restart/5000/restart/15000/restart/60000"],
        ["sc", "failureflag", "TallyBridge", "1"],
    ]


class FakeServer:
    """Serves until ``should_exit``, or gives up after 3 s, so a stop that
    never arrives fails the test and does not hang it."""

    def __init__(self) -> None:
        self.should_exit = False

    async def serve(self) -> None:
        for _ in range(60):
            if self.should_exit:
                return
            await asyncio.sleep(0.05)


def test_the_stop_event_makes_the_server_exit():
    stop = threading.Event()
    server = FakeServer()
    threading.Timer(0.3, stop.set).start()
    start = time.monotonic()
    asyncio.run(cli._serve_with_copy(server, None, None, None, stop))
    assert server.should_exit and time.monotonic() - start < 3


def test_serve_until_starts_with_no_secret_and_the_product_stores(monkeypatch):
    """The installer starts the service before any key exists. ``serve``
    alone refuses that, so the service must ask for no secret."""
    seen = {}

    def fake_serve(args, stop=None, need_secret=True):
        seen.update(windows=args.windows, stop=stop, need_secret=need_secret)
        return 0

    monkeypatch.setattr(cli, "_serve", fake_serve)
    stop = threading.Event()
    cli.serve_until(stop)
    assert seen == {"windows": True, "stop": stop, "need_secret": False}


def test_serve_until_raises_when_the_bridge_cannot_start(monkeypatch):
    monkeypatch.setattr(cli, "_serve", lambda args, stop=None, need_secret=True: 2)
    with pytest.raises(RuntimeError):
        cli.serve_until(threading.Event())


def test_a_stop_waits_for_open_requests_ten_seconds_at_most():
    from tally_bridge.settings import Settings

    options = cli._server_options(Settings())
    assert options["timeout_graceful_shutdown"] == cli.GRACEFUL_SHUTDOWN_S == 10
    assert options["access_log"] is False and options["host"] == "127.0.0.1"


def test_setup_link_writes_the_address_to_a_private_file(tmp_path, capsys):
    url_file = tmp_path / "setup-url.txt"
    args = cli.argparse.Namespace(
        windows=False, settings_file=str(tmp_path / "s.json"),
        dev_secret_file=str(tmp_path / "sec.json"), dev_copy_key_file=None,
        wait=False, url_file=str(url_file))
    assert cli._setup_link(args) == 0
    text = url_file.read_text(encoding="utf-8")
    assert text.startswith("http://127.0.0.1:8765/setup?k=")
    assert "setup?k=" not in capsys.readouterr().out
    if sys.platform != "win32":
        assert url_file.stat().st_mode & 0o777 == 0o600


def test_setup_link_waits_for_the_bridge(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    answers = iter([500, 500, 200])
    assert cli._wait_up("http://127.0.0.1:8765/health", get=lambda u: next(answers)) is True
    assert cli._wait_up("http://127.0.0.1:8765/health", timeout_s=0, get=lambda u: 503) is False


# --- The installer -----------------------------------------------------------------


def test_the_installer_runs_the_same_recovery_rule_as_the_code():
    run = "\n".join(_section("Run"))
    for argv in service.recovery_commands():
        assert f'Parameters: "{" ".join(argv[1:])}"' in run, argv


def _step(line: str) -> str:
    m = re.search(r'Parameters: "((?:[^"]|"")*)"', line)
    return m.group(1) if m else re.search(r'Filename: "([^"]*)"', line).group(1)


def test_the_install_steps_run_in_an_order_that_works():
    steps = [_step(ln) for ln in _section("Run")]
    order = ["--startup auto install", "failure TallyBridge", "failureflag TallyBridge 1",
             "apply-windows --windows", "start", "setup-link --windows --wait --url-file",
             "{code:SetupUrl}"]
    found = [next(i for i, s in enumerate(steps) if s.startswith(o)) for o in order]
    assert found == sorted(found), steps


def test_the_page_opens_as_the_person_who_signed_in():
    """Review finding 7: the elevated step only writes the address. The page
    opens through the shell as the original user, never as the admin."""
    last = _section("Run")[-1]
    assert last.startswith('Filename: "{code:SetupUrl}"')
    for flag in ("shellexec", "runasoriginaluser", "postinstall", "nowait"):
        assert flag in last
    assert "function SetupUrl" in ISS and r"{tmp}\setup-url.txt" in ISS
    # Verifier note N1: no address file (the bridge did not start), no step.
    assert last.endswith("Check: HasSetupUrl") and "function HasSetupUrl: Boolean" in ISS
    assert not any("--open" in ln for ln in _section("Run"))


def test_the_uninstall_undoes_every_change():
    steps = [re.search(r'Parameters: "([^"]*)"', ln).group(1) for ln in _section("UninstallRun")]
    assert steps == ["stop", "remove", "uninstall-windows --windows"]
    assert all("RunOnceId" in ln for ln in _section("UninstallRun"))
    assert _section("UninstallDelete") == [
        r'Type: filesandordirs; Name: "{commonappdata}\TallyBridge"']


def test_the_installer_needs_an_administrator_and_a_64_bit_windows_10():
    setup = "\n".join(_section("Setup"))
    assert "PrivilegesRequired=admin" in setup
    assert "MinVersion=10.0" in setup
    assert "ArchitecturesAllowed=x64compatible" in _section("Setup")
    assert "ArchitecturesInstallIn64BitMode=x64compatible" in _section("Setup")
    dirs = "\n".join(_section("Dirs"))
    assert r'"{commonappdata}\TallyBridge"' in dirs
    assert "users-" not in dirs and "Permissions" not in dirs


def test_the_build_makes_both_programs_and_bundles_the_setup_page():
    spec = (INSTALLER / "tally_bridge.spec").read_text(encoding="utf-8")
    assert 'name="tally_bridge"' in spec and 'name="tally_bridge_service"' in spec
    assert "win32timezone" in spec
    assert (ROOT / "tally_bridge" / "setup" / "static" / "setup.html").exists()
    # Review finding 3: the frozen build puts the page where the setup routes
    # read it: resources.files("tally_bridge.setup") / "static".
    assert '"tally_bridge/setup/static")' in spec
    routes = (ROOT / "tally_bridge" / "setup" / "routes.py").read_text(encoding="utf-8")
    assert 'resources.files("tally_bridge.setup").joinpath("static"' in routes
    build = (INSTALLER / "build.ps1").read_text(encoding="utf-8")
    assert r"installer\tally_bridge.spec" in build and r"installer\tally_bridge.iss" in build
    assert "pyinstaller" in build


@pytest.mark.parametrize("name", ["cli_entry.py", "service_entry.py"])
def test_the_entry_scripts_import_on_any_system(name):
    spec = importlib.util.spec_from_file_location(f"tally_entry_{name[:-3]}", INSTALLER / name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # not run as __main__, so nothing starts


def test_pywin32_installs_on_windows_only():
    deps = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    pywin = [d for d in deps["project"]["dependencies"] if d.startswith("pywin32")]
    assert pywin == ["pywin32>=306; sys_platform == 'win32'"]


# --- Review fixes: the data folder, the upgrade, the locked build ----------------


def _icacls_calls() -> list[str]:
    body = ISS.split("procedure ProtectDataDir;", 1)[1].split("\nend;", 1)[0]
    return re.findall(r"Icacls\('\"' \+ Dir \+ '([^']*)'\);", body)


def test_the_data_folder_is_for_admins_and_system_only():
    """Review finding 1: %ProgramData% lets users create files, and the
    LocalSystem service trusts them. The installer takes the folder back:
    admins own every item, explicit rights go, inheritance stops, and Users
    may read only."""
    assert _icacls_calls() == [
        '" /setowner *S-1-5-32-544 /T /C',
        '" /reset /T /C',
        '" /inheritance:r /grant:r *S-1-5-18:(OI)(CI)F *S-1-5-32-544:(OI)(CI)F'
        ' *S-1-5-32-545:(OI)(CI)RX',
        '" /setowner *S-1-5-32-544 /T /C',
        '\\*" /reset /T /C',
    ]
    assert "RaiseException" in ISS.split("procedure Icacls", 1)[1].split("\nend;", 1)[0]


def test_a_fresh_install_deletes_what_a_user_put_in_the_folder():
    body = ISS.split("procedure ProtectDataDir;", 1)[1].split("\nend;", 1)[0]
    assert "if not WasInstalled then\n    DelTree(Dir + '\\*', False, True, True);" in body
    step = ISS.split("procedure CurStepChanged", 1)[1].split("\nend;", 1)[0]
    assert "CurStep = ssPostInstall" in step and "ProtectDataDir" in step


def test_an_upgrade_stops_the_service_and_keeps_the_settings():
    prep = ISS.split("function PrepareToInstall", 1)[1].split("\nend;", 1)[0]
    assert "WasInstalled := RegKeyExists(HKLM, ServiceKey);" in prep
    assert "'stop TallyBridge'" in prep
    assert r"'SYSTEM\CurrentControlSet\Services\TallyBridge'" in ISS


def test_the_build_installs_only_the_locked_versions():
    """Review finding 4: no `pip install` of the newest versions. The build
    syncs the lock, with PyInstaller in a locked group."""
    build = (INSTALLER / "build.ps1").read_text(encoding="utf-8")
    assert "uv sync --frozen --group build" in build
    assert "uv run --frozen --group build pyinstaller" in build
    assert "pip install" not in build
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["dependency-groups"]["build"] == ["pyinstaller>=6,<7"]
    lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
    assert '\nname = "pyinstaller"\n' in lock and '\nname = "pywin32"\n' in lock
