#!/usr/bin/env python3
"""Pick the tests that a merge can break and a single PR cannot (CIV-32, task 3277).

premerge.sh calls this on the trial merge. It reads two file lists: the files
the PR changed (``--pr``) and the files main changed since the merge base
(``--main``). It prints one test file a line:

  * the tests that BOTH sides can affect (the intersection of the two
    ``affected_tests.select`` sets). Each side passed alone, so only a test that
    both sides reach can go red from the merge; and
  * each test file that either side added or changed. A new fence on one side
    meets the new code of the other even when the import graph links nothing.

When one side is FULL (a file such as pyproject.toml changed), the other side's
set stands alone. When both sides are FULL it prints ``FULL`` and nothing else:
the full suite is the job of pr-check and main, not of this script.

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


def pick(pr: list[str], main: list[str], root: Path = at.ROOT) -> list[str] | None:
    """The test files to run, or None when both sides are FULL.

    The test files that either side changed come FIRST, then the rest, each part
    sorted. The caller can run the first part alone, and report a red at once."""
    pr_sel, pr_full, _ = at.select(pr)
    main_sel, main_full, _ = at.select(main)
    if pr_full and main_full:
        return None
    if pr_full:
        both = set(main_sel)
    elif main_full:
        both = set(pr_sel)
    else:
        both = set(pr_sel) & set(main_sel)
    changed = {
        f for f in (*pr, *main)
        if f.startswith("tests/") and f.endswith(".py") and Path(f).name.startswith("test_") and (root / f).is_file()
    }
    return sorted(changed) + sorted(both - changed)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pr", required=True, help="file with the PR's changed files, one a line")
    ap.add_argument("--main", required=True, help="file with main's changed files since the merge base")
    ap.add_argument("--first-out", help="write the count of the leading changed-test part to this file")
    args = ap.parse_args(argv)
    out = pick(_read(args.pr), _read(args.main))
    if out is None:
        print("FULL")
        return 0
    if args.first_out:
        changed = {f for f in (*_read(args.pr), *_read(args.main)) if f in set(out) and f.startswith("tests/")}
        Path(args.first_out).write_text(f"{sum(1 for f in out if f in changed)}\n", encoding="utf-8")
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
