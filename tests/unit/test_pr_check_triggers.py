"""Two triggers, two files: pr-check on pull_request, push-check on push (R7).

Two defects, and each one is the other's opposite:

* H-10. `pull_request` builds check out `refs/pull/N/merge`, and GitHub does
  not compute that ref while a PR is conflicted. PR #439 sat `dirty` and
  reported `check_runs: 0`, so `test_migration_prefixes.py` never ran in the
  window where a cross-branch migration-number collision is most likely. A
  push build closes it.
* #1077 (2026-10-16). The push build lived in pr-check.yml. Its push run made
  a SKIPPED "Unit tests" check on the PR's head SHA, and GitHub counts a
  skipped check as passed. So a PR with a failed shard read as mergeable for
  about 7 minutes.

So pr-check.yml has no push trigger, and push-check.yml runs only the R1
guard, under a job name that no required check uses.

These are assertions about the TRIGGER, not about any job: a job that never
starts cannot fail, which is the whole shape of the bug.
"""

from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[2]
_WORKFLOWS = _ROOT / ".github/workflows"
_WORKFLOW = _WORKFLOWS / "pr-check.yml"
_PUSH = _WORKFLOWS / "push-check.yml"


def _doc(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _triggers(path: Path = _WORKFLOW) -> dict:
    """The `on:` block, around PyYAML's oldest trap.

    YAML 1.1 resolves the bare key `on` to the BOOLEAN True, so
    `doc["on"]` raises KeyError on a file that plainly contains `on:`.
    """
    doc = _doc(path)
    return doc.get("on", doc.get(True))


def test_pr_check_runs_on_pull_request_only() -> None:
    on = _triggers()
    assert "pull_request" in on, "the PR trigger must stay — it is the one GitHub shows on the PR"
    assert "push" not in on, (
        "a push run of pr-check makes a SKIPPED twin of each required check on "
        "the PR head SHA, and GitHub counts a skipped check as passed"
    )


def test_push_check_runs_the_migration_prefix_guard_on_push() -> None:
    on = _triggers(_PUSH)
    assert "push" in on
    run = "\n".join(
        step.get("run", "") for job in _doc(_PUSH)["jobs"].values() for step in job["steps"]
    )
    assert "pytest tests/unit/test_migration_prefixes.py" in run


def test_the_push_trigger_is_not_narrowed_back_to_nothing() -> None:
    """`branches-ignore` is the only narrowing allowed, and only for published refs.

    A `branches:` allow-list would reintroduce the bug for every branch somebody
    forgot to name. Each name on the ignore-list is a ref where the guard does
    NOT run, so an addition is a fence removed.
    """
    push = _triggers(_PUSH)["push"]
    assert "branches" not in push, (
        "an allow-list silently excludes every branch not named in it; "
        "use branches-ignore so new branches are covered by default"
    )
    assert push.get("branches-ignore") == ["main", "release", "staging"]


def test_no_push_job_shares_a_name_with_a_pr_check_job() -> None:
    """A push job named like a required check twins it on the same SHA."""
    pr_names = {j.get("name") for j in _doc(_WORKFLOW)["jobs"].values()}
    push_names = {j.get("name") for j in _doc(_PUSH)["jobs"].values()}
    assert not pr_names & push_names


def test_the_secret_scan_range_survives_a_push_event() -> None:
    """`github.event.pull_request.base.sha` is empty on a push.

    Left unguarded it yields RANGE="..HEAD", which git reads as the whole
    history — precisely the historical leak (BO-8) the scan is scoped to skip.
    The scan runs on pull_request today. The guard stays for any trigger
    added later.
    """
    body = _WORKFLOW.read_text(encoding="utf-8")
    assert 'if [ "${{ github.event_name }}" = "pull_request" ]' in body
    assert "git merge-base origin/main HEAD" in body
