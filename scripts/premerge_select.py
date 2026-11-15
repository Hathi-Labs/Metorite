#!/usr/bin/env python3
"""Pick the tests that a merge can break and a single PR cannot (CIV-32, task 3277).

premerge.sh calls this on the trial merge. It reads two file lists: the files
the PR changed (``--pr``) and the files main changed since the merge base
(``--main``). It prints one test file a line, in run order, and ``--first-out`` names a file
that gets the length of tier 1:

  1. a test file that one side changed and that the other side's source changes
     reach through an import (the shape of both collisions of 2026-11-13/14);
  2. every other test file that either side added or changed. A new fence on
     one side meets the new code of the other even when no import links them;
  3. the tests that BOTH sides can affect (the intersection of the two
     ``affected_tests.select`` sets). Each side passed alone, so only a test
     that both sides reach can go red from the merge.

Each tier runs its small files first. When one side is FULL (a file such as
pyproject.toml changed), the other side's set stands alone. When both sides are
FULL it prints ``FULL`` on the first line, then the changed test files. The
full suite is the job of pr-check and main, not of this script.

Fence: tests/unit/test_premerge_select.py.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import affected_tests as at  # noqa: E402


def _read(path: str) -> list[str]:
    return [ln.strip().replace("\\", "/") for ln in Path(path).read_text(encoding="utf-8").splitlines() if ln.strip()]


def _tests(files: list[str], root: Path) -> set[str]:
    return {
        f for f in files
        if f.startswith("tests/") and f.endswith(".py") and Path(f).name.startswith("test_") and (root / f).is_file()
    }


def _by_size(files: set[str], root: Path) -> list[str]:
    """Small files first: more tests a minute, so a red shows sooner."""
    def size(f: str) -> int:
        try:
            return (root / f).stat().st_size
        except OSError:
            return 0
    return sorted(files, key=lambda f: (size(f), f))


def _src_reach(files: list[str], root: Path) -> set[str]:
    src = [f for f in files if f not in _tests(files, root)]
    try:
        return set(at.select(src, scanners=False)[0])
    except TypeError:  # an older affected_tests.py has no scanners switch
        return set(at.select(src)[0])


def pick_tiers(pr: list[str], main: list[str], root: Path = at.ROOT) -> tuple[list[str], int] | None:
    """(the test files in run order, the length of the first tier), or None when both sides are FULL.

    Tier 1: a test file that one side changed AND that the other side's SOURCE
    changes reach through an import. This is the shape of both collisions of
    2026-11-13 and 2026-11-14. Tier 2: every other test file that a side changed.
    Tier 3: the tests both sides reach. Each tier runs small files first."""
    pr_sel, pr_full, _ = at.select(pr)
    main_sel, main_full, _ = at.select(main)
    changed = _tests(pr, root) | _tests(main, root)
    if pr_full and main_full:
        return (["FULL", *_by_size(changed, root)], 0) if changed else None
    if pr_full:
        both = set(main_sel)
    elif main_full:
        both = set(pr_sel)
    else:
        both = set(pr_sel) & set(main_sel)
    # Scanner tests are left out here: any .py change picks all of them.
    pr_src = _src_reach(pr, root)
    main_src = _src_reach(main, root)
    tier1 = ((_tests(main, root) & pr_src) | (_tests(pr, root) & main_src))
    tier2 = changed - tier1
    tier3 = both - changed
    return _by_size(tier1, root) + _by_size(tier2, root) + _by_size(tier3, root), len(tier1)


def pick(pr: list[str], main: list[str], root: Path = at.ROOT) -> list[str] | None:
    """The test files to run in order, or None when both sides are FULL."""
    got = pick_tiers(pr, main, root)
    return None if got is None else got[0]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pr", required=True, help="file with the PR's changed files, one a line")
    ap.add_argument("--main", required=True, help="file with main's changed files since the merge base")
    ap.add_argument("--first-out", help="write the count of the leading changed-test part to this file")
    args = ap.parse_args(argv)
    got = pick_tiers(_read(args.pr), _read(args.main))
    out, first = got if got else (["FULL"], 0)
    if args.first_out:
        Path(args.first_out).write_text(f"{first}\n", encoding="utf-8")
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
