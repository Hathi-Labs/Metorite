"""CIV-LIVE (task 4060): the weekly review prints the time lost by lane.

``delivery_review.sh`` with ``DELIVERY_REVIEW_TABLE_ONLY=1`` prints the table
from a fixture ``lane-stops.tsv`` and stops before git and claude.
"""

import datetime as dt
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "home_server" / "delivery_review.sh"
COMMAND = ROOT / ".claude" / "commands" / "delivery-review.md"
NOW = 1_800_000_000


def _iso(t):
    return dt.datetime.fromtimestamp(t, dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _table(tmp_path, rows):
    stops = tmp_path / "lane-stops.tsv"
    stops.write_text("".join("\t".join(r) + "\n" for r in rows), encoding="utf-8")
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(tmp_path),
        "METORITE_REPO": str(ROOT),
        "LANE_STOPS": str(stops),
        "LANE_STOPS_NOW": str(NOW),
        "DELIVERY_REVIEW_TABLE_ONLY": "1",
    }
    r = subprocess.run(
        ["bash", str(SCRIPT)], capture_output=True, text=True, timeout=60, env=env, encoding="utf-8"
    )
    assert r.returncode == 0, r.stderr
    return r.stdout


@pytest.mark.skipif(not shutil.which("bash") or not shutil.which("python3"), reason="needs bash")
def test_the_review_prints_hours_by_cause_for_each_lane_from_the_stop_log(tmp_path):
    out = _table(
        tmp_path,
        [
            (_iso(NOW - 10 * 86400), _iso(NOW - 9 * 86400), "lane-old", "usage"),
            (_iso(NOW - 2 * 86400), _iso(NOW - 2 * 86400 + 1800), "lane-ci", "network"),
            (_iso(NOW - 2 * 86400), _iso(NOW - 2 * 86400 + 4 * 3600), "lane-ci", "usage"),
            (_iso(NOW - 86400), _iso(NOW - 86400 + 3600), "lane-pro", "memory"),
            (_iso(NOW - 7200), "-", "lane-pro", "signout"),
        ],
    )
    assert "Time lost by lane (last 7 days, hours)" in out
    assert "| Lane | network | usage | signout | memory | Total |" in out
    assert "| lane-ci | 0.5 | 4.0 | 0.0 | 0.0 | 4.5 |" in out
    assert "| lane-pro | 0.0 | 0.0 | 2.0 | 1.0 | 3.0 |" in out
    assert "| Total | 0.5 | 4.0 | 2.0 | 1.0 | 7.5 |" in out
    assert "lane-old" not in out


@pytest.mark.skipif(not shutil.which("bash") or not shutil.which("python3"), reason="needs bash")
def test_no_stop_log_gives_a_plain_line(tmp_path):
    out = _table(tmp_path, [])
    assert "No lane stopped on a fault" in out


def test_the_session_may_read_the_table_and_the_command_puts_it_in_the_report():
    sh = SCRIPT.read_text(encoding="utf-8")
    assert '"Read(//tmp/lane_time_lost.md)"' in sh
    assert 'time_lost "$WORK" > "$LOST_TABLE"' in sh
    cmd = COMMAND.read_text(encoding="utf-8")
    assert "/tmp/lane_time_lost.md" in cmd and "Time lost by lane" in cmd
