"""CIV-LIVE (task 4060): the weekly review gets the time lost by lane.

The whole ``delivery_review.sh`` runs with a fake ``git`` and a fake
``claude`` on PATH. The fake claude writes its argv to a file, so the test
reads the prompt text that the review session gets.
"""

import datetime as dt
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "home_server" / "delivery_review.sh"
COMMAND = ROOT / ".claude" / "commands" / "delivery-review.md"
NOW = 1_800_000_000

pytestmark = pytest.mark.skipif(
    not shutil.which("bash") or not shutil.which("python3"), reason="needs bash and python3"
)

FAKE_GIT = """#!/usr/bin/env bash
# git -C <repo> worktree add --quiet --detach <dir> origin/main: link the
# scripts of the real checkout into the throwaway dir.
if [ "$3" = worktree ] && [ "$4" = add ] && [ -n "$FAKE_SCRIPTS" ]; then
  ln -s "$FAKE_SCRIPTS" "$7/scripts"
fi
exit 0
"""
FAKE_CLAUDE = """#!/usr/bin/env python3
import json, os, sys
open(os.environ["FAKE_ARGV"], "w").write(json.dumps(sys.argv[1:]))
"""


def _iso(t):
    return dt.datetime.fromtimestamp(t, dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _run(tmp_path, rows, *, scripts=True):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("git", FAKE_GIT), ("claude", FAKE_CLAUDE)):
        f = bin_dir / name
        f.write_text(body, encoding="utf-8")
        f.chmod(f.stat().st_mode | stat.S_IEXEC)
    stops = tmp_path / "lane-stops.tsv"
    stops.write_text("".join("\t".join(r) + "\n" for r in rows), encoding="utf-8")
    argv = tmp_path / "argv.json"
    env = {
        "PATH": f"{bin_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}",
        "HOME": str(tmp_path),
        "TMPDIR": str(tmp_path),
        "METORITE_REPO": str(tmp_path / "repo"),
        "LANE_STOPS": str(stops),
        "LANE_STOPS_NOW": str(NOW),
        "FAKE_ARGV": str(argv),
        "FAKE_SCRIPTS": str(ROOT / "scripts") if scripts else "",
    }
    r = subprocess.run(
        ["bash", str(SCRIPT)], capture_output=True, text=True, timeout=60, env=env, encoding="utf-8"
    )
    assert r.returncode == 0, r.stderr
    args = json.loads(argv.read_text(encoding="utf-8"))
    assert args[:2] == ["-p", "/delivery-review"]
    return args[args.index("--append-system-prompt") + 1], args


def test_the_review_prompt_holds_hours_by_cause_for_each_lane(tmp_path):
    prompt, _ = _run(
        tmp_path,
        [
            (_iso(NOW - 10 * 86400), _iso(NOW - 9 * 86400), "lane-old", "usage"),
            (_iso(NOW - 2 * 86400), _iso(NOW - 2 * 86400 + 1800), "lane-ci", "network"),
            (_iso(NOW - 2 * 86400), _iso(NOW - 2 * 86400 + 4 * 3600), "lane-ci", "usage"),
            (_iso(NOW - 86400), _iso(NOW - 86400 + 3600), "lane-pro", "memory"),
            (_iso(NOW - 7200), "-", "lane-pro", "signout"),
        ],
    )
    assert "Time lost by lane (last 7 days, hours)" in prompt
    assert "| Lane | network | usage | signout | memory | Total |" in prompt
    assert "| lane-ci | 0.5 | 4.0 | 0.0 | 0.0 | 4.5 |" in prompt
    assert "| lane-pro | 0.0 | 0.0 | 2.0 | 1.0 | 3.0 |" in prompt
    assert "| Total | 0.5 | 4.0 | 2.0 | 1.0 | 7.5 |" in prompt
    assert "lane-old" not in prompt


def test_no_stop_log_gives_a_plain_line(tmp_path):
    prompt, _ = _run(tmp_path, [])
    assert "No lane stopped on a fault" in prompt


def test_a_table_step_that_fails_still_runs_the_review(tmp_path):
    # No lane_keeper.py in the throwaway checkout: the table step fails.
    prompt, args = _run(tmp_path, [], scripts=False)
    assert "the table step failed" in prompt
    assert "--allowedTools" in args


def test_no_file_in_tmp_carries_the_table():
    sh = SCRIPT.read_text(encoding="utf-8")
    assert "lane_time_lost" not in sh and "--append-system-prompt" in sh
    cmd = COMMAND.read_text(encoding="utf-8")
    assert "Time lost by lane" in cmd and "lane_time_lost" not in cmd
