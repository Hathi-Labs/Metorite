"""H-259 / WS-25d: a failed nightly backup reaches a person (R7 fence).

Spec: ``project-docs/specs/backup_and_restore.md``.

Two claims:

  1. ``vps-health.yml`` has a job that reads ``acb-backup.service`` and
     ``acb-backup.timer`` on the box, runs ``scripts/backup_alert_state.sh``,
     and opens an issue. A simulate input feeds a fake state to the same path.
  2. The rule script alerts on a failed unit, a stale success and an inactive
     timer. It stays quiet on a clean state, and on an empty state.
"""

from __future__ import annotations

import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "vps-health.yml"
SCRIPT = ROOT / "scripts" / "backup_alert_state.sh"

NOW = datetime(2026, 10, 28, 12, 0, 0, tzinfo=UTC)


def _job() -> dict:
    wf = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return wf["jobs"]["backup-timer"]


def test_workflow_reads_unit_and_timer_and_opens_issue() -> None:
    text = yaml.dump(_job())
    assert "acb-backup.service" in text
    assert "acb-backup.timer" in text
    assert "backup_alert_state.sh" in text
    assert "gh issue create" in text
    assert "Nightly backup failed" in text
    steps = " ".join(s.get("name", "") for s in _job()["steps"])
    assert "Reconcile the backup issue" in steps


def test_workflow_has_simulate_input_and_no_real_unit_start() -> None:
    wf = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    triggers = wf.get("on") or wf.get(True)
    assert "simulate_backup_failure" in triggers["workflow_dispatch"]["inputs"]
    text = yaml.dump(_job())
    assert "[test] " in text
    for forbidden in ("systemctl start", "systemctl restart", "systemctl stop"):
        assert forbidden not in text


def test_issue_is_not_the_outage_issue() -> None:
    text = yaml.dump(_job())
    assert "backup-failed" in text
    assert "vps-outage" not in text


def _run(state: str, *, last_read: datetime | None = None):
    bash = shutil.which("bash")
    assert bash
    env = {"NOW_EPOCH": str(int(NOW.timestamp())), "PATH": "/usr/bin:/bin"}
    if last_read:
        env["LAST_READ_EPOCH"] = str(int(last_read.timestamp()))
    return subprocess.run(
        [bash, str(SCRIPT)], input=state, capture_output=True, text=True, env=env, check=False
    )


def _state(
    *, timer="active", result="success", hours_ago: float | None = 3, journal_hours_ago=None
) -> str:
    """A box state. The unit timestamp is n/a, as it is after a reboot."""
    out = f"timer={timer}\nResult={result}\nExecMainStatus=0\nExecMainExitTimestamp=n/a\n"
    if hours_ago is not None:
        when = datetime.fromtimestamp(NOW.timestamp() - hours_ago * 3600, UTC)
        out += f"BackupTaken={when.strftime('%Y-%m-%dT%H%M%SZ')}\n"
    if journal_hours_ago is not None:
        when = datetime.fromtimestamp(NOW.timestamp() - journal_hours_ago * 3600, UTC)
        out += f"JournalComplete={when.strftime('%Y-%m-%dT%H:%M:%S+0000')}\n"
    return out + "---JOURNAL---\nsome log line\n"


def test_clean_state_is_ok() -> None:
    r = _run(_state())
    assert r.returncode == 0, r.stdout
    assert r.stdout.startswith("OK acb-backup.service")


def test_fresh_manifest_with_na_unit_timestamp_is_clean() -> None:
    # The post-reboot case: systemd keeps no run state, so the stamp is n/a.
    r = _run(_state(hours_ago=3))
    assert r.returncode == 0, r.stdout


def test_failed_result_alerts() -> None:
    r = _run(_state(result="failed"))
    assert r.returncode == 10
    assert "Result" in r.stdout


def test_old_manifest_alerts() -> None:
    r = _run(_state(hours_ago=27))
    assert r.returncode == 10
    assert "27 h old" in r.stdout


def test_manifest_boundary_at_26_hours() -> None:
    assert _run(_state(hours_ago=25.9)).returncode == 0
    assert _run(_state(hours_ago=26.1)).returncode == 10


def test_no_manifest_alerts() -> None:
    r = _run(_state(hours_ago=None))
    assert r.returncode == 10
    assert "No backup manifest" in r.stdout


def test_journal_fallback_when_manifest_unreadable() -> None:
    assert _run(_state(hours_ago=None, journal_hours_ago=3)).returncode == 0
    assert _run(_state(hours_ago=None, journal_hours_ago=27)).returncode == 10


def test_future_manifest_is_fresh_with_a_skew_warning() -> None:
    r = _run(_state(hours_ago=-2))
    assert r.returncode == 0
    assert "WARN" in r.stdout and "future" in r.stdout


def test_inactive_timer_alerts() -> None:
    r = _run(_state(timer="inactive"))
    assert r.returncode == 10
    assert "timer" in r.stdout


def test_remote_read_ignores_manual_dirs() -> None:
    # The remote glob matches only 20??-??-??T??????Z dirs, so manual-* is out.
    text = yaml.dump(_job())
    assert "20??-??-??T??????Z" in text
    assert "manual" not in text


def test_remote_glob_picks_newest_timestamp_dir(tmp_path: Path) -> None:
    for name in ("2026-10-28T023000Z", "2026-10-29T023119Z", "manual-20261030"):
        (tmp_path / name).mkdir()
    out = subprocess.run(
        ["bash", "-c", f"ls -1d {tmp_path}/20??-??-??T??????Z/ | sort | tail -1"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    assert out.strip().endswith("2026-10-29T023119Z/")


def test_empty_state_does_not_alert() -> None:
    r = _run("")
    assert r.returncode == 0
    assert r.stdout.startswith("UNREADABLE")


def test_empty_state_for_over_26h_is_an_error() -> None:
    r = _run("", last_read=datetime.fromtimestamp(NOW.timestamp() - 27 * 3600, UTC))
    assert r.returncode == 11


def test_empty_state_for_a_short_time_is_a_warning() -> None:
    r = _run("", last_read=datetime.fromtimestamp(NOW.timestamp() - 3 * 3600, UTC))
    assert r.returncode == 0


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash needed")
def test_script_parses() -> None:
    assert subprocess.run(["bash", "-n", str(SCRIPT)], check=False).returncode == 0
