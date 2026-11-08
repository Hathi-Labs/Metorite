"""The home box starts each release slot (CIV-13 D178, CIV-21 D194).

The slots are 05:30, 13:30 and 21:30 UTC (11:00, 19:00 and 03:00 IST).

GitHub started the ``30 21 * * *`` cron of ``deploy.yml`` 3.5 to 5.5 h late
(run 41949347780 started at 01:33 UTC), so the release window often closed
first. A user timer on the home box now dispatches ``deploy.yml`` with
``nightly=true``. The workflow side is fenced in ``test_daily_release.py``.

What this file fences, in ``scripts/home_server/``:

1. The timer fires at each slot, catches up after a stop, and is enabled
   under ``timers.target``. The service runs the installed copy of the script.
2. The script dispatches on ``main`` with ``nightly=true`` and its reason.
3. It refuses outside 21:00 to 01:00, 05:00 to 09:00 and 13:00 to 17:00
   UTC, and it says so.
4. It stops with a clear message when ``gh`` is not signed in, and it never
   prints a token.
5. It logs the URL of THIS dispatch's run, not of an older one, or it exits
   non-zero.
6. ``install`` turns on linger and enables the timer.
"""

from __future__ import annotations

import configparser
import json
import os
import pathlib
import shutil
import subprocess

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_DIR = _ROOT / "scripts/home_server"
_SCRIPT = _DIR / "nightly_release.sh"
_SERVICE = _DIR / "metorite-nightly-release.service"
_TIMER = _DIR / "metorite-nightly-release.timer"

_BASH = shutil.which("bash") if os.name != "nt" else None
_DATE = shutil.which("date")
needs_bash = pytest.mark.skipif(_BASH is None or _DATE is None, reason="needs bash and GNU date")


def _unit(path: pathlib.Path) -> configparser.ConfigParser:
    cp = configparser.ConfigParser(interpolation=None, strict=False)
    cp.optionxform = str  # systemd keys are case-sensitive
    cp.read_string(path.read_text(encoding="utf-8"))
    return cp


# ── 1. The units ─────────────────────────────────────────────────────────────


_SLOTS = ["05:30", "13:30", "21:30"]


def _on_calendar() -> list[str]:
    # configparser keeps only the last of keys that repeat, so read the
    # lines of the file.
    lines = _TIMER.read_text(encoding="utf-8").splitlines()
    return [ln.split("=", 1)[1].strip() for ln in lines if ln.startswith("OnCalendar=")]


def test_the_timer_fires_at_each_slot_and_catches_up() -> None:
    assert _on_calendar() == [f"*-*-* {s}:00 UTC" for s in _SLOTS]
    timer = _unit(_TIMER)
    assert timer["Timer"]["Persistent"] == "true"
    assert timer["Install"]["WantedBy"] == "timers.target"


@pytest.mark.parametrize("slot", _SLOTS)
def test_each_slot_has_a_timer_line(slot: str) -> None:
    assert f"*-*-* {slot}:00 UTC" in _on_calendar(), f"the {slot} UTC slot has no OnCalendar line"


def test_the_service_runs_the_installed_copy() -> None:
    service = _unit(_SERVICE)
    assert service["Service"]["Type"] == "oneshot"
    assert service["Service"]["ExecStart"] == "%h/.local/lib/metorite/nightly_release.sh run"
    # The timer starts the service of its own name.
    assert _TIMER.stem == _SERVICE.stem


# ── 2. to 5. The script, with a fake gh and a fake clock ─────────────────────

_FAKE_GH = r"""#!/usr/bin/env bash
echo "$*" >> "$FAKE_DIR/calls.log"
case "$1 $2" in
  "auth status")
    # The real command can print a part of the token.
    echo "Token: gho_FAKESECRET123"
    [ "${FAKE_AUTH:-ok}" = ok ] ;;
  "workflow run")
    [ "${FAKE_DISPATCH:-ok}" = ok ] ;;
  "run list")
    cat "$FAKE_DIR/runs.json" ;;
  *) exit 1 ;;
esac
"""

# A fake `date` that pins "now" to FAKE_NOW (epoch seconds). A call with -d
# goes to the real date, so the script's own arithmetic still runs.
_FAKE_DATE = r"""#!/usr/bin/env bash
for a in "$@"; do
  case "$a" in -d|-d*|--date*) exec "$REAL_DATE" "$@" ;; esac
done
exec "$REAL_DATE" -d "@$FAKE_NOW" "$@"
"""


def _epoch(iso: str) -> int:
    out = subprocess.run([_DATE, "-u", "-d", iso, "+%s"], capture_output=True, text=True, check=True)
    return int(out.stdout)


def _run(tmp_path: pathlib.Path, *, now: str = "2026-11-05T21:30:00Z", runs: list | None = None,
         auth: str = "ok", dispatch: str = "ok", poll: str = "0") -> tuple[subprocess.CompletedProcess, list[str]]:
    fake = tmp_path / "fake"
    (fake / "bin").mkdir(parents=True)
    for name, body in (("gh", _FAKE_GH), ("date", _FAKE_DATE)):
        f = fake / "bin" / name
        f.write_text(body, encoding="utf-8")
        f.chmod(0o755)
    (fake / "runs.json").write_text(json.dumps(runs or []), encoding="utf-8")
    env = {**os.environ, "FAKE_DIR": str(fake), "FAKE_NOW": str(_epoch(now)),
           "REAL_DATE": _DATE, "FAKE_AUTH": auth, "FAKE_DISPATCH": dispatch,
           "NIGHTLY_POLL_SECONDS": poll, "NIGHTLY_POLL_INTERVAL": "0",
           "PATH": f"{fake / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}"}
    done = subprocess.run([_BASH, str(_SCRIPT), "run"], env=env, capture_output=True,
                          text=True, timeout=60)
    log = fake / "calls.log"
    return done, (log.read_text(encoding="utf-8").splitlines() if log.exists() else [])


_URL = "https://github.com/Hathi-Labs/Metorite/actions/runs/"


@needs_bash
def test_it_dispatches_a_nightly_run_on_main_and_logs_its_url(tmp_path: pathlib.Path) -> None:
    runs = [{"url": _URL + "2", "createdAt": "2026-11-05T21:30:03Z", "displayTitle": "deploy (nightly)"}]
    done, calls = _run(tmp_path, runs=runs)
    assert done.returncode == 0, done.stderr
    assert ("workflow run deploy.yml -R Hathi-Labs/Metorite --ref main -f nightly=true "
            "-f reason=nightly release (home box)") in calls
    assert f"started: {_URL}2" in done.stdout


@needs_bash
def test_the_poll_finds_this_dispatch_and_not_an_older_run(tmp_path: pathlib.Path) -> None:
    runs = [
        # Last night's nightly run, and today's plain dispatch by a person.
        {"url": _URL + "1", "createdAt": "2026-11-04T21:30:04Z", "displayTitle": "deploy (nightly)"},
        {"url": _URL + "41969146897", "createdAt": "2026-11-05T21:29:30Z", "displayTitle": "deploy"},
    ]
    done, calls = _run(tmp_path, runs=runs)
    assert done.returncode == 6, done.stdout
    assert "no nightly run showed" in done.stderr
    assert any(c.startswith("run list ") for c in calls)
    assert "started:" not in done.stdout


@needs_bash
@pytest.mark.parametrize("now", ["2026-11-05T21:00:00Z", "2026-11-05T23:59:00Z", "2026-11-06T00:59:00Z",
                                 # CIV-21: the 05:30 and 13:30 slots.
                                 "2026-11-06T05:00:00Z", "2026-11-06T05:30:00Z", "2026-11-06T08:59:00Z",
                                 "2026-11-06T13:00:00Z", "2026-11-06T13:30:00Z", "2026-11-06T16:59:00Z"])
def test_the_clock_guard_lets_each_slot_through(tmp_path: pathlib.Path, now: str) -> None:
    runs = [{"url": _URL + "3", "createdAt": now, "displayTitle": "deploy (nightly)"}]
    done, _ = _run(tmp_path, now=now, runs=runs)
    assert done.returncode == 0, done.stderr


@needs_bash
@pytest.mark.parametrize("now", ["2026-11-05T20:59:00Z", "2026-11-06T01:00:00Z",
                                 "2026-11-06T04:59:00Z", "2026-11-06T09:00:00Z",
                                 "2026-11-06T09:30:00Z", "2026-11-06T12:59:00Z",
                                 "2026-11-06T17:00:00Z", "2026-11-06T20:00:00Z"])
def test_the_clock_guard_refuses_outside_the_slots(tmp_path: pathlib.Path, now: str) -> None:
    done, calls = _run(tmp_path, now=now)
    assert done.returncode == 3
    assert "21:00 to 01:00, 05:00 to 09:00 or 13:00 to 17:00 UTC" in done.stderr
    assert not any(c.startswith("workflow run") for c in calls), "it must not dispatch"


@needs_bash
def test_a_signed_out_gh_stops_with_a_clear_message(tmp_path: pathlib.Path) -> None:
    done, calls = _run(tmp_path, auth="bad")
    assert done.returncode == 4
    assert "gh auth login" in done.stderr
    assert not any(c.startswith("workflow run") for c in calls)


@needs_bash
def test_a_failed_dispatch_exits_non_zero(tmp_path: pathlib.Path) -> None:
    done, _ = _run(tmp_path, dispatch="bad")
    assert done.returncode == 5
    assert "gh workflow run failed" in done.stderr


@needs_bash
@pytest.mark.parametrize("auth", ["ok", "bad"])
def test_it_never_prints_a_token(tmp_path: pathlib.Path, auth: str) -> None:
    runs = [{"url": _URL + "2", "createdAt": "2026-11-05T21:30:03Z", "displayTitle": "deploy (nightly)"}]
    done, _ = _run(tmp_path, runs=runs, auth=auth)
    assert "gho_" not in done.stdout + done.stderr


def test_the_script_has_no_way_to_print_a_token() -> None:
    text = _SCRIPT.read_text(encoding="utf-8")
    for bad in ("auth token", "--show-token", "auth status -t", "GH_TOKEN", "GITHUB_TOKEN", "set -x"):
        assert bad not in text, f"the script must not hold {bad!r}"


# ── 6. Install ───────────────────────────────────────────────────────────────


def test_install_turns_on_linger_and_enables_the_timer() -> None:
    text = _SCRIPT.read_text(encoding="utf-8")
    assert 'sudo loginctl enable-linger "$user"' in text
    assert "systemctl --user daemon-reload" in text
    assert 'systemctl --user enable --now "${UNIT}.timer"' in text
    assert 'UNIT="metorite-nightly-release"' in text
    # It installs the copy that the service runs.
    assert '"$lib/nightly_release.sh"' in text and 'lib="$HOME/.local/lib/metorite"' in text


def test_the_script_is_executable_in_git() -> None:
    out = subprocess.run(["git", "ls-files", "-s", str(_SCRIPT.relative_to(_ROOT))],
                         cwd=_ROOT, capture_output=True, text=True, check=True).stdout
    assert out.startswith("100755"), "the service runs the installed copy, and install keeps the mode"
