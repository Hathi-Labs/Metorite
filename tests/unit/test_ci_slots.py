"""The run slots of a local CI lane (CIV-6, task 1634): scripts/ci_pc/slots.sh.

R7 — the rule names its fence. A lane used to run ONE suite at a time. It now
takes several, and the only thing between that and an out-of-memory box (the
WSL2 VM stopped on 2026-10-20) is this admission code. Each case here is a
way that goes wrong in silence:

* a dead run's reservation counts, and the lane starves;
* a live run's reservation does not count, and two runs overfill the box;
* two runs that start at once both see the same free memory and both start.

The tests run the real bash with a fake /proc/meminfo and a temp slot folder.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SLOTS = _ROOT / "scripts" / "ci_pc" / "slots.sh"

pytestmark = pytest.mark.skipif(shutil.which("flock") is None, reason="needs flock(1)")


def _meminfo(tmp_path: Path, total_gb: int, avail_gb: int) -> Path:
    p = tmp_path / "meminfo"
    p.write_text(
        f"MemTotal:       {total_gb * 1048576} kB\nMemAvailable:   {avail_gb * 1048576} kB\n",
        encoding="utf-8",
    )
    return p


def _env(tmp_path: Path, total_gb: int = 24, avail_gb: int = 20) -> dict[str, str]:
    return {
        **os.environ,
        "CI_SLOT_DIR": str(tmp_path / "slots"),
        "CI_MEMINFO": str(_meminfo(tmp_path, total_gb, avail_gb)),
        "CI_SHARD_GB": "2",
        "CI_FIXED_GB": "6",
        "CI_OTHER_GB": "6",
        "CI_SLOT_POLL_S": "1",
        "CI_SLOT_WAIT_S": "3",
    }


def _bash(script: str, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", f'source "{_SLOTS}"\n{script}'],
        env=env, capture_output=True, text=True, timeout=60,
    )


def _grant(env: dict[str, str], want: int, minimum: int) -> str:
    return _bash(f"ci_slot_grant {want} {minimum}", env).stdout.strip()


def test_an_empty_lane_grants_all_the_shards_asked_for(tmp_path):
    # 24 GB total - 6 other = 18 budget. 6 fixed + 6 shards * 2 = 18. Fits.
    assert _grant(_env(tmp_path), 6, 2) == "6"


def test_the_grant_shrinks_to_what_fits(tmp_path):
    # 18 budget, 6 fixed: 4 shards fit when avail is 14 GB. Asking for 6 gives 4.
    assert _grant(_env(tmp_path, avail_gb=14), 6, 2) == "4"


def test_nothing_is_granted_when_the_smallest_run_does_not_fit(tmp_path):
    # avail 7 < need of 1 shard (8).
    assert _grant(_env(tmp_path, avail_gb=7), 6, 1) == ""


def test_a_live_reservation_counts_against_the_budget(tmp_path):
    env = _env(tmp_path)
    slots = Path(env["CI_SLOT_DIR"]); slots.mkdir()
    res = slots / "a.res"; res.write_text("10\n", encoding="utf-8")
    holder = subprocess.Popen(["flock", str(res), "sleep", "30"])
    try:
        time.sleep(0.5)
        # budget 18 - 10 live = 8 left: 6 fixed + 1 shard (2) = 8. One shard.
        assert _grant(env, 6, 1) == "1"
        assert _grant(env, 6, 2) == ""
    finally:
        holder.kill(); holder.wait()


def test_a_dead_reservation_counts_for_nothing(tmp_path):
    # A crashed run keeps its file but drops its flock.
    env = _env(tmp_path)
    slots = Path(env["CI_SLOT_DIR"]); slots.mkdir()
    (slots / "dead.res").write_text("18\n", encoding="utf-8")
    assert _grant(env, 6, 2) == "6"


def test_acquire_holds_the_reservation_while_the_shell_lives(tmp_path):
    env = _env(tmp_path)
    script = (
        'ci_slot_acquire 3 1 || exit 9\n'
        'echo "granted=$CI_SHARDS_GRANTED"\n'
        'echo "live=$(_ci_live_gb)"\n'
    )
    out = _bash(script, env)
    assert out.returncode == 0, out.stderr
    assert "granted=3" in out.stdout
    assert "live=12" in out.stdout  # 6 fixed + 3 * 2
    # The shell has exited, so the reservation is dead and counts for 0.
    assert _bash("_ci_live_gb", env).stdout.strip() == "0"


def test_two_runs_that_start_at_once_cannot_both_take_the_last_room(tmp_path):
    # Room for ONE run of 1 shard (8 GB) in a budget of 12, never two.
    env = {**_env(tmp_path, total_gb=18, avail_gb=18), "CI_SLOT_WAIT_S": "2"}
    cmd = ["bash", "-c", f'source "{_SLOTS}"; ci_slot_acquire 1 1 && echo GOT && sleep 4']
    procs = [subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, text=True) for _ in range(2)]
    outs = [p.communicate(timeout=30)[0] for p in procs]
    assert sum("GOT" in o for o in outs) == 1, outs


def test_the_browser_suite_reserves_its_own_memory(tmp_path):
    # --e2e starts a 10 GB container. 6 fixed + 1 shard (2) + 10 = 18 fits a
    # budget of 18; with 1 GB less free it must not be granted.
    assert _grant(_env(tmp_path, avail_gb=18), 1, 1, ) == "1"
    out = _bash("ci_slot_grant 1 1 10", _env(tmp_path, avail_gb=17))
    assert out.stdout.strip() == ""
    out = _bash("ci_slot_grant 1 1 10", _env(tmp_path, avail_gb=18))
    assert out.stdout.strip() == "1"


def test_the_sweep_runs_before_the_admit_lock_is_released():
    # A sweep outside the lock could delete a reservation another run has
    # written and not yet locked: that run is then counted as 0 GB.
    text = _SLOTS.read_text(encoding="utf-8")
    body = text.split("ci_slot_acquire() {")[1]
    assert body.index("rm -f") < body.index("flock -u 8\n      return 0")


def test_the_defaults_are_the_measured_peaks():
    # 2026-11-04, task 1634: 3.2 to 4.4 GB per shard, 5 GB around it. A default
    # below the measurement lets two full runs fill the box.
    text = _SLOTS.read_text(encoding="utf-8")
    assert ': "${CI_SHARD_GB:=3}"' in text
    assert ': "${CI_FIXED_GB:=5}"' in text
