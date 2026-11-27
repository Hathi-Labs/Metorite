"""No two tracked files in one folder differ only in letter case.

Task 1103. `BriefCard.tsx` and `briefCard.ts` sat in one folder. Linux sees two
files. Windows and macOS see one, so `tsc` resolved both imports to the same file
and failed there, while Linux CI stayed green.
"""

from __future__ import annotations

import subprocess
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _tracked() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=REPO, capture_output=True, check=True
    ).stdout.decode("utf-8")
    return [p for p in out.split("\0") if p]


def case_collisions(paths: list[str]) -> list[list[str]]:
    groups: dict[str, set[str]] = defaultdict(set)
    for p in paths:
        groups[p.lower()].add(p)
    return sorted(sorted(g) for g in groups.values() if len(g) > 1)


def test_no_two_tracked_files_differ_only_in_case() -> None:
    assert case_collisions(_tracked()) == []


def test_the_reader_finds_a_case_only_pair() -> None:
    """Prove the fence bites on the pair that caused task 1103."""
    pair = [
        "workbench/control_plane/src/lib/shell/BriefCard.tsx",
        "workbench/control_plane/src/lib/shell/briefcard.tsx",
        "workbench/control_plane/src/lib/shell/other.ts",
    ]
    assert case_collisions(pair) == [sorted(pair[:2])]
