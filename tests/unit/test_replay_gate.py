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
