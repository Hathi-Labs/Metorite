"""Fence for CIV-23 (D198): which pr-check jobs may run on the box runners.

A job that can reach the label `metorite-box` must test the variable
`USE_BOX_RUNNERS` AND the same-repo condition, so a fork PR never runs code
on the box. The default is `ubuntu-24.04`. No job may use `self-hosted`.
"""

from __future__ import annotations

from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[2]
_WORKFLOWS = sorted((_ROOT / ".github" / "workflows").glob("*.yml"))
_BOX = "metorite-box"


def _jobs():
    for wf in _WORKFLOWS:
        doc = yaml.safe_load(wf.read_text(encoding="utf-8"))
        for name, job in (doc.get("jobs") or {}).items():
            yield wf.name, name, str(job.get("runs-on"))


def test_no_job_uses_the_self_hosted_label() -> None:
    bad = [f"{w}:{j}" for w, j, r in _jobs() if "self-hosted" in r]
    assert not bad, f"`self-hosted` is banned (D198): {bad}"


def test_a_box_job_has_the_switch_and_the_same_repo_check() -> None:
    box = [(w, j, r) for w, j, r in _jobs() if _BOX in r]
    assert box, "no job routes to the box: remove this fence or restore CIV-23"
    for w, j, r in box:
        assert "vars.USE_BOX_RUNNERS == '1'" in r, f"{w}:{j} lacks the switch"
        assert "github.event.pull_request.head.repo.full_name == github.repository" in r, (
            f"{w}:{j} lacks the same-repo check"
        )
        assert "github.event_name != 'pull_request'" in r, f"{w}:{j} lacks the event check"
        assert r.rstrip().endswith("|| 'ubuntu-24.04' }}"), f"{w}:{j} default is not ubuntu-24.04"


def test_only_the_unit_shard_job_of_pr_check_reaches_the_box() -> None:
    box = {(w, j) for w, j, r in _jobs() if _BOX in r}
    assert box == {("pr-check.yml", "test")}, box


def test_a_sudo_step_in_a_box_job_runs_only_on_github_hosted() -> None:
    for wf in _WORKFLOWS:
        doc = yaml.safe_load(wf.read_text(encoding="utf-8"))
        for name, job in (doc.get("jobs") or {}).items():
            if _BOX not in str(job.get("runs-on")):
                continue
            for step in job.get("steps", []):
                run = str(step.get("run", ""))
                if "sudo" in run or "apt-get" in run:
                    assert "github-hosted" in str(step.get("if", "")), (wf.name, name, step)
