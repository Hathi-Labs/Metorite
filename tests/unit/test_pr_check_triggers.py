"""pr-check must run on `push`, not only on `pull_request` (H-10, R7).

The defect this fences: `pull_request` builds check out `refs/pull/N/merge`, and
GitHub does not compute that ref while a PR is conflicted. PR #439 sat `dirty`
and reported `check_runs: 0` — no jobs at all — so the window in which a
cross-branch migration-number collision is most likely is exactly the window in
which `test_migration_prefixes.py` never runs.

These are assertions about the TRIGGER, not about any job: a job that never
starts cannot fail, which is the whole shape of the bug.
"""

from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[2]
_WORKFLOW = _ROOT / ".github/workflows/pr-check.yml"


def _triggers() -> dict:
    """The `on:` block, around PyYAML's oldest trap.

    YAML 1.1 resolves the bare key `on` to the BOOLEAN True, so
    `doc["on"]` raises KeyError on a file that plainly contains `on:`.
    A test that looked it up by name would fail for a reason having
    nothing to do with CI, so look it up the way the parser stored it.
    """
    doc = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    return doc.get("on", doc.get(True))


def test_pr_check_runs_on_push_as_well_as_pull_request() -> None:
    on = _triggers()
    assert "pull_request" in on, "the PR trigger must stay — it is the one GitHub shows on the PR"
    assert "push" in on, (
        "pr-check must also trigger on `push`. Without it a CONFLICTED PR runs "
        "zero jobs (refs/pull/N/merge does not exist while a PR is dirty) and "
        "GitHub renders that identically to 'nothing failing'."
    )


def test_the_push_trigger_is_not_narrowed_back_to_nothing() -> None:
    """`branches-ignore` is the only narrowing allowed, and only for published refs.

    A `branches:` allow-list would reintroduce the bug for every branch somebody
    forgot to name — which is how the `pull_request` trigger acquired its own
    "deliberately NOT restricted" comment after stacked PRs ran no checks.

    The ignore-list is pinned exactly rather than merely checked for `main`: each
    name on it is a ref where this workflow does NOT run, so an addition is a
    fence removed. The three allowed are the ones already checked upstream of the
    push — `main` by deploy.yml, `release` because deploy.yml's `publish-release`
    only ever fast-forwards it to a SHA that has been on `main`, and `staging`
    for the same reason once T-6/T-7 land the ladder.
    """
    push = _triggers()["push"]
    assert "branches" not in push, (
        "an allow-list silently excludes every branch not named in it; "
        "use branches-ignore so new branches are covered by default"
    )
    assert push.get("branches-ignore") == ["main", "release", "staging"], (
        "main is covered by deploy.yml and release/staging are published refs "
        "fast-forwarded to already-checked SHAs; every other branch must run"
    )


def test_the_secret_scan_range_survives_a_push_event() -> None:
    """`github.event.pull_request.base.sha` is empty on a push.

    Left unguarded it yields RANGE="..HEAD", which git reads as the whole
    history — precisely the historical leak (BO-8) the scan is scoped to skip.
    """
    body = _WORKFLOW.read_text(encoding="utf-8")
    assert 'if [ "${{ github.event_name }}" = "pull_request" ]' in body, (
        "the secret scan must branch on the event; the PR-only base sha is "
        "empty under the push trigger"
    )
    assert "git merge-base origin/main HEAD" in body, (
        "a push build needs a base to measure its commit range against"
    )
