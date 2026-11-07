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


def test_the_unit_shards_follow_the_changes_job_and_the_aggregate_is_never_skipped():
    # CIV-3. The shards run a selection. A FAILED `changes` job must not skip
    # them (a skip counts as a pass), and the required aggregate stays `always()`.
    jobs = _jobs()
    assert "changes" in jobs["test"]["needs"]
    cond = jobs["test"]["if"]
    assert "!cancelled()" in cond and "always()" not in cond
    assert "needs.changes.outputs.full" not in cond, "the shards decide inside, not by an if"
    assert jobs["unit-tests"]["if"] == "always()"
    assert jobs["unit-tests"]["name"] == "Unit tests"
    assert "paths" not in (yaml.safe_load(_PR_CHECK.read_text(encoding="utf-8"))[True]["pull_request"] or {})


def test_the_frontend_unit_job_is_never_skipped_by_path():
    # Its tests read files across the whole repo.
    assert "if" not in _jobs()["frontend"]


def test_the_visual_review_line_runs_in_a_required_job():
    # The UI gate (DESIGN_SYSTEM.md §11). "Changed areas" is not a required
    # check, and a red `changes` job fails open, so the step lives in the
    # Frontend job. The body comes from the event FILE: an expression would
    # paste the body into the script, and the local CI cannot render one.
    step = _step("frontend", "Visual review line")
    run = step["run"]
    assert step["if"] == "!cancelled()"
    assert "${{" not in run
    assert "GITHUB_EVENT_PATH" in run
    assert "git fetch" in run and "--depth=1" in run
    assert "frontend=true" in run, "an unreadable diff must require the line"
    assert "^workbench/control_plane/src/" in run
    assert "^Visual review:" in run
    assert "github.event.pull_request.body" not in _PR_CHECK.read_text(encoding="utf-8")
    assert not any(s.get("name", "").startswith("Visual review") for s in _jobs()["changes"]["steps"])


def test_the_visual_review_line_reads_the_live_body_first():
    """CIV-8. A rerun replays the first EVENT, so the event body alone made a
    missing line cost a push and a whole run. The step reads the live body."""
    step = _step("frontend", "Visual review line")
    assert step["env"] == {"GH_TOKEN": "${{ github.token }}"}
    assert "${{" not in step["run"]
    assert 'gh api "repos/${GITHUB_REPOSITORY}/pulls/${pr}"' in step["run"]
    assert "gh run rerun <run id> --failed" in step["run"]


_BASH = shutil.which("bash")


def _run_visual_review(tmp_path: Path, *, event_body, live: str | None, token: bool = True,
                       touch_frontend: bool = True, event: bool = True):
    """Run the step's script in a tiny repo, with a `gh` shim for the live body."""
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args: str) -> str:
        return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                              check=True, env={**os.environ, "GIT_AUTHOR_NAME": "t",
                              "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                              "GIT_COMMITTER_EMAIL": "t@t"}).stdout.strip()

    git("init", "-q", "-b", "main")
    (repo / "a.txt").write_text("a", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "base")
    base = git("rev-parse", "HEAD")
    target = repo / ("workbench/control_plane/src/x.ts" if touch_frontend else "docs/x.md")
    target.parent.mkdir(parents=True)
    target.write_text("x", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "change")
    ev = tmp_path / "event.json"
    ev.write_text(json.dumps({"pull_request": {"number": 77, "body": event_body,
                                               "base": {"sha": base}}}), encoding="utf-8")
    shim = tmp_path / "bin"
    shim.mkdir()
    gh = shim / "gh"
    gh.write_text("#!/usr/bin/env bash\n" + (
        f"printf '%s' {json.dumps(live)}\n" if live is not None
        else "echo '{\"message\":\"Bad credentials\"}'\nexit 1\n"), encoding="utf-8")
    gh.chmod(0o755)
    env = {**os.environ, "PATH": f"{shim}:{os.environ['PATH']}", "GITHUB_REPOSITORY": "o/r"}
    env.pop("GH_TOKEN", None)
    if token:
        env["GH_TOKEN"] = "t"
    if event:
        env["GITHUB_EVENT_PATH"] = str(ev)
    else:
        env["GITHUB_EVENT_PATH"] = str(tmp_path / "missing.json")
    run = _step("frontend", "Visual review line")["run"]
    return subprocess.run(["bash", "-eo", "pipefail", "-c", run], cwd=repo, env=env, capture_output=True, text=True)


_LINE = "Visual review: no UI change"


@pytest.mark.skipif(shutil.which("bash") is None or os.name == "nt", reason="needs a POSIX bash")
@pytest.mark.parametrize("kw,code,says", [
    # the live body names the review, the event body does not: pass (the fix)
    (dict(event_body="x", live=_LINE), 0, "live body"),
    # the live body lost the line after the event had it: the live body wins
    (dict(event_body=_LINE, live="no line"), 1, "no line of its body"),
    # the API fails: the event body decides, a line in it passes
    (dict(event_body=_LINE, live=None), 0, "event is checked"),
    # the API fails and the event body has no line: fail closed
    (dict(event_body="x", live=None), 1, "no line of its body"),
    # no token (a fork PR or the local CI): the event body decides
    (dict(event_body="x", live=_LINE, token=False), 1, "no line of its body"),
    # an empty live body is a body: it has no line
    (dict(event_body=_LINE, live=""), 1, "no line of its body"),
    # a PR that changes nothing under the control plane needs no line
    (dict(event_body="x", live="x", touch_frontend=False), 0, "no change under"),
    # no event file (the local CI): nothing to check
    (dict(event_body="x", live="x", event=False), 0, "no pull request event"),
])
def test_the_visual_review_line_step_behaves(tmp_path, kw, code, says):
    got = _run_visual_review(tmp_path, **kw)
    assert got.returncode == code, got.stdout + got.stderr
    assert says in got.stdout + got.stderr, got.stdout + got.stderr




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
