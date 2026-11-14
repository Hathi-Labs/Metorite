"""Fence for scripts/replay_gate.py, the replay of the PR gate (CIV-33c)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import replay_gate as rg


def test_reasons_group_by_rule() -> None:
    assert rg.reason_key("a.b: 90 test files import it, more than 40") == "hub"
    assert rg.reason_key(".github/x.yml: shared config, CI or the DB seam") == ".github/"
    assert rg.reason_key("packages/acb_common/a.py: shared config") == "packages/acb_common/"
    assert rg.reason_key("selector error: boom") == "selector error"


def test_the_acb_common_rules_group_by_rule() -> None:
    # CIV-33c-3. Each rule that replaced the acb_common prefix is its own group.
    import affected_tests as sel

    a = "packages/acb_common/acb_common/"
    cases = {
        sel._full_reason(a + "db.py"): "acb_common: a seam by name",
        sel._full_reason(a + "settings.py"): "acb_common: settings.py or env_guard.py",
        sel.deleted_reasons([a + "gone.py"], lambda _p: False)[0]: "acb_common: a deleted file",
        "acb_common._log: a conftest.py loads it for each test": "acb_common: a conftest.py loads it",
    }
    for reason, key in cases.items():
        assert rg.reason_key(reason) == key, reason
    assert rg.reason_key("tests/unit/conftest.py: a conftest.py can change every test") == "conftest.py"


def test_the_summary_counts_the_full_share_and_github_alone() -> None:
    results = [
        ("a", {"full": True, "files": [], "reasons": [".github/x.yml: shared"]}),
        ("b", {"full": True, "files": [], "reasons": ["m: 9 test files import it, more than 1"]}),
        ("c", {"full": False, "files": ["t"], "reasons": []}),
        ("d", {"full": False, "files": ["t"], "reasons": []}),
    ]
    text = rg.summarize(results)
    assert "2 of 4 commits FULL (50.0%)" in text
    assert "FULL because of .github/ alone: 1" in text


def test_the_load_reason_groups_by_rule() -> None:
    reason = "acb_common.card_body: 450 test files load it, more than 360 (20% of the test files)"
    assert rg.reason_key(reason) == "acb_common: tests that load it, over the ceiling"


_LIST_DIFF = (
    "diff --git a/.github/workflows/pr-check.yml b/.github/workflows/pr-check.yml\n"
    "--- a/.github/workflows/pr-check.yml\n+++ b/.github/workflows/pr-check.yml\n"
    "@@ -800,0 +801 @@ jobs:\n+              tests/unit/test_workflows_timer_r8.py \\\n"
)


def test_a_list_edit_is_a_test_path_and_nothing_else() -> None:
    # Fix round 1: the CIV-33c-2 projection is in the repo, so the number repeats.
    assert rg.list_edit_only(_LIST_DIFF) is True
    assert rg.list_edit_only(_LIST_DIFF + "-  timeout-minutes: 20\n") is False
    assert rg.list_edit_only("") is False


def test_the_projection_drops_a_list_edit_and_keeps_each_other_reason() -> None:
    results = [
        ("list", {"full": True, "files": [], "reasons": [".github/workflows/x.yml: CI"]}),
        ("flow", {"full": True, "files": [], "reasons": [".github/workflows/x.yml: CI"]}),
        ("both", {"full": True, "files": [], "reasons": [
            ".github/workflows/x.yml: CI", "m: 9 test files import it, more than 1"]}),
        ("none", {"full": False, "files": ["t"], "reasons": []}),
    ]
    text = rg.summarize_projected(results, lambda c: c in {"list", "both"})
    assert "projected: 2 of 4 commits FULL (50.0%)" in text
    assert "     2    1  .github/" in text  # `flow` has no other reason
    assert "     1    0  hub" in text
