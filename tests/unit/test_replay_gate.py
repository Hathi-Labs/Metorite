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
