"""WS-54 GH-5 — the fold of the CI state (K1, ``github_integration.md`` §8.5).

Pure: ``github_sync.fold_checks`` over a table of check runs and combined
statuses, and the name checks that build a GitHub path. The read itself, the
old-head rule (K2) and the no-session rule (K3) run on a real database in
``test_github_reconcile_r8.py``.

R7, the mutations. Each one turns a test here red (run 2026-10-18):

* K-M1 ``failing`` checked after ``pending`` -> the "a failure beats a pending run" case.
* K-M2 a ``neutral`` conclusion counts as passed -> the "neutral is skipped" case.
* K-M3 a combined ``pending`` with no context is pending -> the "empty combined" case.
* K-M4 ``split_repo`` takes ``..`` -> ``test_a_path_name_is_checked``.
"""

from __future__ import annotations

import pytest
from gateway import github_sync


def run(status: str = "completed", conclusion: str | None = "success") -> dict:
    return {"status": status, "conclusion": conclusion}


def combined(state: str, *contexts: str) -> dict:
    return {"state": state, "statuses": [{"state": s, "context": f"c{i}"}
                                         for i, s in enumerate(contexts)]}


def counts(passed=0, failed=0, pending=0, skipped=0) -> dict:
    return {"passed": passed, "failed": failed, "pending": pending, "skipped": skipped,
            "total": passed + failed + pending + skipped}


CASES = [
    ("nothing at all is none", [], None, "none", counts()),
    ("an empty combined status is none", [], combined("pending"), "none", counts()),
    ("one success passes", [run()], None, "passing", counts(passed=1)),
    ("one failure fails", [run(conclusion="failure")], None, "failing", counts(failed=1)),
    ("a timeout fails", [run(conclusion="timed_out")], None, "failing", counts(failed=1)),
    ("a cancel fails", [run(conclusion="cancelled")], None, "failing", counts(failed=1)),
    ("an action required fails", [run(conclusion="action_required")], None, "failing",
     counts(failed=1)),
    ("a startup failure fails", [run(conclusion="startup_failure")], None, "failing",
     counts(failed=1)),
    ("a queued run is pending", [run("queued", None)], None, "pending", counts(pending=1)),
    ("an in progress run is pending", [run("in_progress", None), run()], None, "pending",
     counts(passed=1, pending=1)),
    ("a waiting run is pending", [run("waiting", None)], None, "pending", counts(pending=1)),
    ("a failure beats a pending run", [run("in_progress", None), run(conclusion="failure")],
     None, "failing", counts(failed=1, pending=1)),
    ("neutral is skipped", [run(conclusion="neutral"), run()], None, "passing",
     counts(passed=1, skipped=1)),
    ("only skipped runs still pass", [run(conclusion="skipped")], None, "passing",
     counts(skipped=1)),
    ("a combined failure fails", [run()], combined("failure", "failure"), "failing",
     counts(passed=1, failed=1)),
    ("a combined error fails", [], combined("error", "error"), "failing", counts(failed=1)),
    ("a combined pending with a context is pending", [run()], combined("pending", "pending"),
     "pending", counts(passed=1, pending=1)),
    ("a combined success passes", [], combined("success", "success", "success"), "passing",
     counts(passed=2)),
    ("statuses and runs add up", [run(), run(conclusion="skipped")],
     combined("success", "success"), "passing", counts(passed=2, skipped=1)),
]


@pytest.mark.parametrize(("name", "runs", "status", "state", "summary"), CASES,
                         ids=[c[0] for c in CASES])
def test_the_fold(name: str, runs: list, status: dict | None, state: str, summary: dict) -> None:
    assert github_sync.fold_checks(runs, status) == (state, summary)


def test_the_table_has_at_least_fifteen_cases() -> None:
    assert len(CASES) >= 15


def test_a_path_name_is_checked() -> None:
    assert github_sync.split_repo("acme/widgets") == ("acme", "widgets")
    for bad in ("acme", "acme/..", "../x", "a/b/c", "a b/c", "", "acme/wid?gets"):
        assert github_sync.split_repo(bad) is None, bad
    assert github_sync.split_ref("acme/widgets#12") == ("acme/widgets", 12)
    for bad in ("acme/widgets", "acme/widgets#x", "acme#1", "acme/w#12345678901"):
        assert github_sync.split_ref(bad) is None, bad
