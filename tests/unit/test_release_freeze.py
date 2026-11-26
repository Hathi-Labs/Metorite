"""Task 3415: scripts/release_freeze.sh, the ONE copy of the release freeze.

No merge from 04:20, 12:20 and 20:20 UTC until the nightly release run of that
slot starts, or slot + 30 minutes. premerge.sh (step 00) and merge_bot.sh run it.
Each case pins the clock (PREMERGE_NOW) and the gh answer (PREMERGE_RUNS_CMD).
"""

import calendar
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "release_freeze.sh"
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="needs bash")

NO_RUN = "true"  # gh answers, and lists no dispatch run


def _at(h: int, m: int, day: int = 16) -> int:
    """Epoch seconds of h:m UTC on 2026-11-<day>."""
    return calendar.timegm((2026, 11, day, h, m, 0))


def _runs(*lines: str) -> str:
    """A PREMERGE_RUNS_CMD that prints one '<createdAt><TAB><title>' line per run."""
    return "printf '%s\\n' " + " ".join(f"'{line}'" for line in lines)


def _run(tmp_path: Path, now: int, runs: str = NO_RUN, **extra: str):
    env = {k: v for k, v in os.environ.items() if not k.startswith("PREMERGE_")}
    env.update({"PREMERGE_NOW": str(now), "PREMERGE_RUNS_CMD": runs, **extra})
    # Another cwd: the bot calls the script by its path, from anywhere.
    return subprocess.run([BASH, SCRIPT.as_posix()], cwd=tmp_path, capture_output=True,
                          text=True, encoding="utf-8", env=env, timeout=60)


@pytest.mark.parametrize("hm,slot", [((4, 20), "05:30"), ((5, 0), "05:30"), ((12, 20), "13:30"),
                                     ((13, 29), "13:30"), ((20, 20), "21:30"), ((21, 45), "21:30")])
def test_a_merge_inside_a_freeze_window_is_refused(tmp_path, hm, slot):
    res = _run(tmp_path, _at(*hm))
    assert res.returncode == 75, res.stdout + res.stderr
    assert "release freeze" in res.stderr and slot in res.stderr


@pytest.mark.parametrize("hm", [(4, 19), (6, 0), (12, 19), (14, 0), (20, 19), (22, 0), (0, 0)])
def test_a_merge_outside_the_windows_is_allowed(tmp_path, hm):
    res = _run(tmp_path, _at(*hm))
    assert res.returncode == 0, res.stderr


def test_the_window_closes_at_the_slot_plus_30_minutes(tmp_path):
    assert _run(tmp_path, _at(13, 59)).returncode == 75
    assert _run(tmp_path, _at(14, 0)).returncode == 0


def test_a_nightly_run_created_from_slot_minus_15_ends_the_window(tmp_path):
    res = _run(tmp_path, _at(21, 0), _runs("2026-11-16T21:15:00Z\tdeploy (nightly)"))
    assert res.returncode == 0, res.stderr
    res = _run(tmp_path, _at(21, 40), _runs("2026-11-16T21:31:02Z\tdeploy (nightly)"))
    assert res.returncode == 0, res.stderr


def test_a_nightly_run_before_slot_minus_15_does_not_end_it(tmp_path):
    # 21:14 is the run of a slot before, or a test of the box. The day before does not count either.
    runs = _runs("2026-11-16T21:14:59Z\tdeploy (nightly)", "2026-11-15T21:31:02Z\tdeploy (nightly)")
    res = _run(tmp_path, _at(21, 20), runs)
    assert res.returncode == 75, res.stdout


def test_a_dispatch_that_is_not_nightly_does_not_end_it(tmp_path):
    # A hotfix or a plain dispatch at 21:20 is not the release run (deploy.yml run-name).
    runs = _runs("2026-11-16T21:20:00Z\tdeploy", "2026-11-16T21:21:00Z\thotfix: x")
    res = _run(tmp_path, _at(21, 25), runs)
    assert res.returncode == 75, res.stdout
    assert "release freeze" in res.stderr


def test_no_answer_from_gh_ends_the_window_at_the_slot(tmp_path):
    res = _run(tmp_path, _at(21, 29), "exit 1")
    assert res.returncode == 75 and "release freeze" in res.stderr
    res = _run(tmp_path, _at(21, 30), "exit 1")
    assert res.returncode == 0, res.stderr


def test_the_override_names_the_task_and_passes(tmp_path):
    res = _run(tmp_path, _at(12, 30), PREMERGE_FREEZE_OK="3415")
    assert res.returncode == 0, res.stderr
    assert "OVERRIDDEN for task 3415" in res.stdout


@pytest.mark.parametrize("value", ["yes please", " ", "3415 x", "abc", "-1"])
def test_the_override_must_be_a_task_number(tmp_path, value):
    res = _run(tmp_path, _at(12, 30), PREMERGE_FREEZE_OK=value)
    assert res.returncode == 1 and "must be a task number" in res.stderr


def test_the_override_is_not_needed_outside_a_window(tmp_path):
    res = _run(tmp_path, _at(10, 0), PREMERGE_FREEZE_OK="3415")
    assert res.returncode == 0 and "OVERRIDDEN" not in res.stdout


def test_the_default_gh_call_reads_the_title_of_each_dispatch_run():
    """No case runs the real gh. Its call must ask for the title, and the filter must
    read the word 'nightly' that deploy.yml puts in the run name of a slot run."""
    body = SCRIPT.read_text(encoding="utf-8")
    assert "--workflow deploy.yml --event workflow_dispatch" in body
    assert "--json createdAt,displayTitle" in body
    assert "*nightly*" in body
    deploy = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")
    assert "'deploy (nightly)'" in deploy


def test_premerge_and_the_bot_run_this_one_script():
    premerge = (ROOT / "scripts" / "premerge.sh").read_text(encoding="utf-8")
    assert '"$(dirname "$LEDGER_CHECK")/release_freeze.sh"' in premerge
    bot = (ROOT / "scripts" / "home_server" / "merge_bot.sh").read_text(encoding="utf-8")
    assert '"$ROOT/scripts/release_freeze.sh"' in bot


def test_claude_md_names_the_freeze_windows():
    text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert "04:20" in text and "12:20" in text and "20:20" in text
    assert "tests/unit/test_release_freeze.py" in text
