"""Fence for scripts/ci_unit_guard.py and the sharded unit job that calls it.

The reports come from a REAL pytest run, not a hand-written XML sample, so a
change in how pytest writes a skip reason breaks this test, not the gate.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "scripts"))

import ci_unit_guard as guard  # noqa: E402

_SUITE = '''
import pytest

def test_runs():
    pass

@pytest.mark.skip(reason="TENANT_LADDER_DATABASE_URL unset — R8 requires a REAL Postgres.")
def test_ladder_gate():
    pass

@pytest.mark.skip(reason="AGENT_WRITES_REDIS_URL or TENANT_LADDER_DATABASE_URL unset — no Redis")
def test_redis_gate():
    pass

class TestGroup:
    @pytest.mark.parametrize("n", [1, 2])
    def test_param(self, n):
        pass
'''


def _report(tmp_path: Path, body: str = _SUITE, name: str = "test_sample.py") -> Path:
    (tmp_path / name).write_text(body, encoding="utf-8")
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    out = tmp_path / "junit"
    out.mkdir(exist_ok=True)
    subprocess.run(
        [sys.executable, "-m", "pytest", name, "-q", "-p", "no:cacheprovider",
         "-o", "junit_family=xunit1", f"--junitxml={out / 'shard-1.xml'}"],
        cwd=tmp_path, check=False, capture_output=True,
    )
    return out


def test_a_skip_that_names_the_variable_first_is_a_disarmed_gate(tmp_path: Path) -> None:
    problems = guard.check(_report(tmp_path), [], [])
    assert len(problems) == 1
    assert "test_ladder_gate" in problems[0]


def test_a_redis_skip_that_mentions_the_variable_later_passes(tmp_path: Path) -> None:
    problems = guard.check(_report(tmp_path), [], [])
    assert not any("test_redis_gate" in p for p in problems)


def test_a_file_no_shard_ran_fails(tmp_path: Path) -> None:
    problems = guard.check(_report(tmp_path), ["test_sample.py", "test_missing.py"], [])
    assert "no shard ran test_missing.py" in problems
    assert "no shard ran test_sample.py" not in problems


def test_any_skip_in_a_never_skip_file_fails(tmp_path: Path) -> None:
    problems = guard.check(_report(tmp_path), [], ["test_sample.py"])
    assert any("no-database fence skipped" in p and "test_redis_gate" in p for p in problems)


def test_durations_use_pytest_split_node_ids(tmp_path: Path) -> None:
    data = guard.durations(_report(tmp_path))
    assert "test_sample.py::test_runs" in data
    assert "test_sample.py::TestGroup::test_param[1]" in data


# ── The workflow wiring ────────────────────────────────────────────────────

_WORKFLOW = _ROOT / ".github/workflows/pr-check.yml"


def _jobs() -> dict:
    return yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))["jobs"]


def test_the_required_check_name_is_the_aggregator() -> None:
    """Branch protection requires a check named exactly "Unit tests"."""
    jobs = _jobs()
    named = [k for k, j in jobs.items() if j.get("name") == "Unit tests"]
    assert named == ["unit-tests"]
    assert jobs["unit-tests"]["needs"] == "test"
    run = "\n".join(s.get("run", "") for s in jobs["unit-tests"]["steps"])
    assert "scripts/ci_unit_guard.py check" in run


def test_every_shard_runs_and_writes_a_report() -> None:
    test = _jobs()["test"]
    shards = test["strategy"]["matrix"]["shard"]
    run = "\n".join(s.get("run", "") for s in test["steps"])
    assert f"--splits {len(shards)}" in run
    assert "--group ${{ matrix.shard }}" in run
    assert "junit_family=xunit1" in run
    assert "--junitxml=" in run


def test_the_timings_file_exists() -> None:
    """Without it pytest-split divides by test COUNT, and one 7-minute file
    lands in a shard beside a normal share."""
    assert (_ROOT / ".test_durations").is_file()
