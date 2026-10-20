"""The two-speed CI wiring (owner directive, 2026-10-19). R7 — the rule names its fence.

While a feature is in build, only the SAFETY fences and the product tests
block a PR. The PROCESS fences are advisory until the hardening pass, when
the PR carries the `harden` label. This file proves the wiring that makes
that true, because each failure of it is silent:

* a listed file that moved drops out of every run, and nothing says so;
* a process fence left in the shards still blocks, so "advisory" is a lie;
* a safety fence behind `ci_advisory.sh` stops blocking at all;
* the `changes` job that fails CLOSED skips a job the PR did touch.

Lists: tests/fences.json. Reader: scripts/fences.py.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[2]
_PR_CHECK = _ROOT / ".github/workflows/pr-check.yml"
_ADVISORY = _ROOT / "scripts/ci_advisory.sh"

sys.path.insert(0, str(_ROOT / "scripts"))
import fences  # noqa: E402


def _jobs() -> dict:
    return yaml.safe_load(_PR_CHECK.read_text(encoding="utf-8"))["jobs"]


def _step(job: str, name_starts: str) -> dict:
    steps = [s for s in _jobs()[job]["steps"] if s.get("name", "").startswith(name_starts)]
    assert len(steps) == 1, f"{job}: expected one step named {name_starts!r}"
    return steps[0]


def test_every_listed_fence_exists_and_is_listed_once():
    assert fences.problems(fences.load()) == []


def test_the_lists_have_both_tiers_for_both_languages():
    data = json.loads((_ROOT / "tests/fences.json").read_text(encoding="utf-8"))
    for lang in ("python", "ts"):
        assert set(data[lang]) == {"safety", "process"}


def test_this_file_is_a_safety_fence_so_it_always_blocks():
    assert "tests/unit/test_fence_lists.py" in fences.load()["python"]["safety"]


def test_no_safety_fence_runs_behind_the_advisory_wrapper():
    # The safety fences block in the shards. A step that ran them through
    # ci_advisory.sh would turn a red safety fence into a warning.
    for job in _jobs().values():
        for step in job.get("steps", []):
            run = step.get("run", "")
            if "ci_advisory.sh" in run:
                assert "safety" not in run, step.get("name")


def test_lint_runs_the_process_fences_through_the_advisory_wrapper():
    step = _step("lint", "Process fences")
    assert "scripts/ci_advisory.sh" in step["run"]
    assert "fences.py run process" in step["run"]
    assert _jobs()["lint"]["permissions"]["pull-requests"] == "read"


def test_the_shards_skip_the_process_fences_and_report_every_failure():
    run = _step("test", "Run unit tests")["run"]
    assert "fences.py pytest-ignore" in run
    assert "--reruns 1" in run
    # `-x` stopped each shard at its first failure, so a run showed one
    # failure a round, at about 30 minutes a round.
    assert " -x " not in f" {run} "


def test_the_release_gate_skips_the_process_fences_too():
    # Advisory means advisory: a process fence must not hold the nightly
    # release of a main that pr-check let through.
    deploy = yaml.safe_load((_ROOT / ".github/workflows/deploy.yml").read_text(encoding="utf-8"))
    run = next(s["run"] for s in deploy["jobs"]["test"]["steps"] if s.get("name") == "Run unit tests")
    assert "fences.py pytest-ignore" in run


def test_pytest_ignore_names_only_process_fences():
    data = fences.load()
    out = subprocess.run(
        [sys.executable, str(_ROOT / "scripts/fences.py"), "pytest-ignore"],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    assert sorted(a.removeprefix("--ignore=") for a in out) == sorted(data["python"]["process"])
    assert not set(data["python"]["safety"]) & {a.removeprefix("--ignore=") for a in out}


def test_an_empty_tier_runs_nothing(monkeypatch, capsys):
    monkeypatch.setattr(fences, "load", lambda: {"python": {"safety": [], "process": []}, "ts": {"safety": [], "process": []}})
    assert fences.main(["run", "process"]) == 0
    assert "no process fences listed" in capsys.readouterr().out


def test_the_changes_job_fails_open():
    run = _jobs()["changes"]["steps"][-1]["run"]
    assert "cannot read the diff, so every area runs" in run
    assert 'echo "$a=true"' in run
    # A change to CI itself runs everything.
    assert "^\\.github/" in run


@pytest.mark.parametrize("job,area", [
    ("e2e", "frontend"),
    ("frontend-operator", "operator"),
    ("backup-restore", "database"),
    ("migrations", "database"),
    ("caddy", "caddy"),
])
def test_a_path_skip_is_an_if_on_the_job_never_a_trigger_filter(job, area):
    # GitHub counts a job skipped by `if:` as satisfied. A workflow skipped by
    # a trigger filter reports nothing, and a required check then waits forever.
    cond = _jobs()[job]["if"]
    assert f"needs.changes.outputs.{area} == 'true'" in cond
    # A FAILED `changes` job must run the job, or every required check below
    # it skips, and a skip counts as a pass.
    assert "needs.changes.result != 'success' ||" in cond
    # `always()` starts the job even in a run that a newer push cancelled.
    assert cond.startswith("!cancelled() &&")
    assert "always()" not in cond


def test_the_frontend_unit_job_is_never_skipped_by_path():
    # Its tests read files across the whole repo.
    assert "if" not in _jobs()["frontend"]


_BASH = shutil.which("bash")


@pytest.mark.skipif(_BASH is None or os.name == "nt", reason="needs a POSIX bash")
@pytest.mark.parametrize("cmd,code,says", [
    ("true", 0, ""),
    ("false", 0, "::warning::"),
])
def test_ci_advisory_outside_a_pr_never_blocks(cmd, code, says):
    env = {k: v for k, v in os.environ.items() if k != "PR"}
    res = subprocess.run([_BASH, str(_ADVISORY), "a check", "--", cmd], capture_output=True, text=True, env=env)
    assert res.returncode == code
    assert says in res.stdout
