"""Fence for CIV-23 (D201): which pr-check jobs may run on the box runners.

A job that can reach the label `metorite-box` must test the variable
`USE_BOX_RUNNERS` AND the same-repo condition, so a fork PR never runs code
on the box. The default is `ubuntu-24.04`. No job may use `self-hosted`.
"""

from __future__ import annotations

from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[2]
_WORKFLOWS = sorted(
    p for p in (_ROOT / ".github" / "workflows").iterdir() if p.suffix in (".yml", ".yaml")
)
_RUNS_ON = (
    "${{ (vars.USE_BOX_RUNNERS == '1' && (github.event_name != 'pull_request' || "
    "github.event.pull_request.head.repo.full_name == github.repository)) "
    "&& 'metorite-box' || 'ubuntu-24.04' }}"
)
_BOX = "metorite-box"


def _jobs():
    for wf in _WORKFLOWS:
        doc = yaml.safe_load(wf.read_text(encoding="utf-8"))
        for name, job in (doc.get("jobs") or {}).items():
            yield wf.name, name, str(job.get("runs-on"))


def test_no_job_uses_the_self_hosted_label() -> None:
    bad = [f"{w}:{j}" for w, j, r in _jobs() if "self-hosted" in r]
    assert not bad, f"`self-hosted` is banned (D201): {bad}"


def test_a_box_job_has_the_switch_and_the_same_repo_check() -> None:
    box = [(w, j, r) for w, j, r in _jobs() if _BOX in r]
    assert box, "no job routes to the box: remove this fence or restore CIV-23"
    for w, j, r in box:
        assert r == _RUNS_ON, f"{w}:{j} runs-on is not the one reviewed expression"


def test_a_runs_on_that_is_not_a_literal_cannot_hide_the_box() -> None:
    """`runs-on: ${{ matrix.runner }}` would reach the box with no label text."""
    for w, j, r in _jobs():
        if r != _RUNS_ON:
            assert "${{" not in r, f"{w}:{j} has a computed runs-on that is not reviewed"


def test_every_pr_check_job_but_e2e_may_reach_the_box_and_e2e_never_does() -> None:
    pr = {j: r for w, j, r in _jobs() if w == "pr-check.yml"}
    assert "e2e" in pr and _BOX not in pr["e2e"], "e2e stays on ubuntu-24.04"
    assert pr["e2e"] == "ubuntu-24.04"
    assert pr["backup-restore"] == "ubuntu-24.04", "its verify containers share the daemon (CIV-6)"
    assert pr["test"] == "ubuntu-24.04", "unit shards ran 40+ min on the box against 14 hosted (D201)"
    for j, r in pr.items():
        if j not in ("e2e", "backup-restore", "test"):
            assert r == _RUNS_ON, f"pr-check.yml:{j} is not routed by the reviewed expression"


def test_no_other_workflow_reaches_the_box() -> None:
    assert {w for w, _j, r in _jobs() if _BOX in r} == {"pr-check.yml"}


def test_a_sudo_step_in_a_box_job_runs_only_on_github_hosted() -> None:
    for wf in _WORKFLOWS:
        doc = yaml.safe_load(wf.read_text(encoding="utf-8"))
        for name, job in (doc.get("jobs") or {}).items():
            if _BOX not in str(job.get("runs-on")):
                continue
            for step in job.get("steps", []):
                run = str(step.get("run", ""))
                if "sudo" in run or "apt-get" in run:
                    assert "runner.environment == 'github-hosted'" in str(step.get("if", "")), (wf.name, name, step)


def test_pr_check_is_triggered_by_pull_request_only() -> None:
    """`pull_request_target` or `workflow_run` would send fork code to the box."""
    doc = yaml.safe_load((_ROOT / ".github" / "workflows" / "pr-check.yml").read_text(encoding="utf-8"))
    assert set(doc[True] if True in doc else doc["on"]) == {"pull_request"}
