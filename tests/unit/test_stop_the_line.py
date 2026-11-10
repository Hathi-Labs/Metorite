"""CIV-25 2a/2c: the stop-the-line script and the pins on the trial merge."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/home_server/stop_the_line.sh"


def run(runs, title="feat: x"):
    env = {**os.environ, "STL_RUNS_JSON": json.dumps(runs), "STL_PR_TITLE": title, "STL_FAILED_JOBS": "Unit tests"}
    return subprocess.run(["bash", str(SCRIPT), "7"], env=env, capture_output=True, text=True)


def r(i, c):
    return {"databaseId": i, "conclusion": c, "headSha": "abcdef1234567"}


def test_green_main_lets_the_pr_go():
    p = run([r(2, "success"), r(1, "failure")])
    assert p.returncode == 0 and p.stdout.startswith("GO")


def test_red_main_stops_a_feature_pr():
    p = run([r(2, "failure"), r(1, "success")])
    assert p.returncode == 10
    assert p.stdout.startswith("STOP 2 abcdef123 Unit tests")


def test_red_main_lets_a_marked_fix_go():
    p = run([r(2, "failure")], title="Fix pin [fixes-main]")
    assert p.returncode == 0 and p.stdout.startswith("GO")


def test_a_cancelled_newest_run_is_skipped():
    p = run([r(3, "cancelled"), r(2, "success")])
    assert p.returncode == 0 and "GO" in p.stdout


def test_no_run_does_not_freeze_the_queue():
    p = run([])
    assert p.returncode == 0 and "UNKNOWN" in p.stderr


def test_a_bad_argument_exits_2():
    assert subprocess.run(["bash", str(SCRIPT), "x"], capture_output=True).returncode == 2


def test_premerge_runs_the_exact_list_pins_on_the_trial_merge():
    body = (ROOT / "scripts/premerge.sh").read_text(encoding="utf-8")
    for t in ("test_projects_chat_x_pin.py", "test_index_union.py", "test_chat_parity.py"):
        assert t in body
        assert (ROOT / "tests/unit" / t).is_file(), t
