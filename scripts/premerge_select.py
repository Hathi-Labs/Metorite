#!/usr/bin/env python3
"""Pick the tests that a merge can break and a single PR cannot (CIV-MT, task 3277).

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
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import affected_tests as at


def _read(path: str) -> list[str]:
    return [ln.strip().replace("\\", "/") for ln in Path(path).read_text(encoding="utf-8").splitlines() if ln.strip()]


def _tests(files: list[str], root: Path) -> set[str]:
    return {
        f for f in files
        if f.startswith("tests/") and f.endswith(".py") and Path(f).name.startswith("test_") and (root / f).is_file()
    }


def _durations(root: Path) -> dict[str, float]:
    """Seconds for each test file, from the pytest-split file `.test_durations` (per test id)."""
    import json

    try:
        raw = json.loads((root / ".test_durations").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out: dict[str, float] = {}
    for key, secs in raw.items():
        f = key.split("::", 1)[0]
        out[f] = out.get(f, 0.0) + float(secs)
    return out


def _by_size(files: set[str], root: Path) -> list[str]:
    """The fast files first (more tests a minute, so a red shows sooner). The cost is the
    recorded run time of the file. A file with no record costs a guess from its size."""
    known = _durations(root)

    def cost(f: str) -> float:
        if f in known:
            return known[f]
        try:
            return (root / f).stat().st_size / 50_000  # about 1 s for 50 KB
        except OSError:
            return 0.0
    return sorted(files, key=lambda f: (cost(f), f))


_CACHE: dict = {}

# A data file that tests read as TEXT is not reached by an import, so the selector cannot
# see the link (2026-11-15: PR 2248 re-indented one line of tests/fences.json, and
# test_merge_conflict_rules, which reads the file, went red on main). The picker finds
# those tests by NAME: every test file that names the data file.
DATA_SUFFIXES = (".json", ".md", ".yml", ".yaml", ".toml", ".txt", ".csv", ".sql")
# Names that almost every test mentions. They would select the whole tree.
GENERIC_NAMES = frozenset({
    "README.md", "AGENTS.md", "CLAUDE.md", "INDEX.md", "package.json", "package-lock.json",
    "tsconfig.json", "pyproject.toml", "uv.lock", "HANDOFF.md", "work_plan.md",
})
MAX_TESTS_PER_DATA_FILE = 40


def named_tests(files: list[str], root: Path = at.ROOT, limit: int = MAX_TESTS_PER_DATA_FILE) -> tuple[set[str], bool]:
    """(the test files that name a changed data file, whether a list was cut at `limit`)."""
    found: set[str] = set()
    cut = False
    for f in files:
        name = Path(f).name
        if not f.endswith(DATA_SUFFIXES) or f.startswith(("workbench/", "tests/unit/golden/")) or name in GENERIC_NAMES:
            continue
        try:
            out = subprocess.run(["git", "grep", "-l", "-F", "-e", name, "--", "tests/"], cwd=root,
                                 capture_output=True, text=True, encoding="utf-8", timeout=60).stdout
        except (OSError, subprocess.SubprocessError):
            cut = True  # cannot tell: say so
            continue
        hits = sorted(
            h.strip().replace("\\", "/") for h in out.splitlines()
            if Path(h.strip()).name.startswith("test_") and h.strip().endswith(".py") and h.strip() != f
        )
        if len(hits) > limit:
            cut = True
            hits = hits[:limit]
        found.update(hits)
    return found, cut


def _reach(files: list[str], root: Path, *, src_only: bool = False) -> tuple[set[str], bool]:
    key = (tuple(files), str(root), src_only)
    if key not in _CACHE:
        _CACHE[key] = _reach_uncached(files, root, src_only=src_only)
    return _CACHE[key]


def _reach_uncached(files: list[str], root: Path, *, src_only: bool = False) -> tuple[set[str], bool]:
    """(the test files these changes reach, whether the selector says FULL).

    This is the selector of pr-check, `affected_tests.gate`, without the diff text that its
    flag rule and fence rule read. So a data file, a registry and a scanner count as they
    do in pr-check, and a flag or fence-list change does not. If the selector fails
    it answers FULL, and the caller then says UNPROVEN. An older `affected_tests.py`
    with no `gate` falls back to `select`."""
    files = [f for f in files if f not in _tests(files, root)] if src_only else files
    # gate() reads tests/fences.json only with the text of the change, which this
    # picker does not have, and it answers FULL when it cannot. So the file stays out
    # here, and `pick_tiers` adds the tests that read it (FENCE_GUARD_TESTS) by name.
    files = [f for f in files if f != "tests/fences.json"]
    if not files:
        return set(), False
    if hasattr(at, "gate"):
        g = at.gate(files, root)
        got = {f for f in g.get("files", []) if f.startswith("tests/") and f.endswith(".py")}
        return got, bool(g.get("full"))
    picked, full, _ = at.select(files)
    return set(picked), bool(full)


def full_sides(pr: list[str], main: list[str], root: Path = at.ROOT) -> list[str]:
    """The sides that the selector calls FULL (a file such as pyproject.toml changed)."""
    sides = [name for name, files in (("pr", pr), ("main", main)) if _reach(files, root)[1]]
    if named_tests([*pr, *main], root)[1]:
        sides.append("data-files-cut")
    return sides


def pick_tiers(pr: list[str], main: list[str], root: Path = at.ROOT) -> tuple[list[str], int] | None:
    """(the test files in run order, the length of the first tier), or None when both sides are FULL.

    Tier 1: a test file that one side changed AND that the other side's SOURCE
    changes reach through an import. This is the shape of both collisions of
    2026-11-13 and 2026-11-14. Tier 2: every other test file that a side changed.
    Tier 3: the tests both sides reach. Each tier runs small files first."""
    pr_sel, pr_full = _reach(pr, root)
    main_sel, main_full = _reach(main, root)
    changed = _tests(pr, root) | _tests(main, root)
    named_pr, _cut = named_tests(pr, root)
    named_main, _cut = named_tests(main, root)
    named = named_pr | named_main
    changed |= {t for t in named if (root / t).is_file()}
    if "tests/fences.json" in (*pr, *main):
        # A changed fence list: the guards that read tests/fences.json run (tier 2 at least).
        changed |= {t for t in getattr(at, "FENCE_GUARD_TESTS", ()) if (root / t).is_file()}
    if pr_full and main_full:
        return (["FULL", *_by_size(changed, root)], 0) if changed else None
    if pr_full:
        both = set(main_sel)
    elif main_full:
        both = set(pr_sel)
    else:
        both = set(pr_sel) & set(main_sel)
    # Scanner tests are left out here: any .py change picks all of them.
    pr_src, _ = _reach(pr, root, src_only=True)
    main_src, _ = _reach(main, root, src_only=True)
    # Tier 0: the tests that name a data file of the PR itself. A red there is the PR's own
    # (PR 2248, 2026-11-15) and the list is short, so it runs before everything else.
    tier0 = {t for t in named_pr if (root / t).is_file()}
    tier1 = (((_tests(main, root) & pr_src) | (_tests(pr, root) & main_src)) | (named & changed)) - tier0
    tier2 = changed - tier1 - tier0
    tier3 = both - changed
    return _by_size(tier0, root) + _by_size(tier1, root) + _by_size(tier2, root) + _by_size(tier3, root), len(tier0) + len(tier1)


def pick(pr: list[str], main: list[str], root: Path = at.ROOT) -> list[str] | None:
    """The test files to run in order, or None when both sides are FULL."""
    got = pick_tiers(pr, main, root)
    return None if got is None else got[0]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pr", required=True, help="file with the PR's changed files, one a line")
    ap.add_argument("--main", required=True, help="file with main's changed files since the merge base")
    ap.add_argument("--first-out", help="write the count of the leading changed-test part to this file")
    ap.add_argument("--note-out", help="write the sides that are FULL (pr, main) to this file; empty when none")
    args = ap.parse_args(argv)
    got = pick_tiers(_read(args.pr), _read(args.main))
    out, first = got if got else (["FULL"], 0)
    if args.note_out:
        pr_files, main_files = _read(args.pr), _read(args.main)
        with open(args.note_out, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(" ".join(full_sides(pr_files, main_files)) + "\n")
    if args.first_out:
        with open(args.first_out, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(f"{first}\n")
    # Bytes with LF only, on every platform: premerge.sh reads these lines in Git Bash,
    # and a CR on a path made pytest stop with "file not found" (review of 2026-11-15).
    sys.stdout.buffer.write(("".join(f"{line}\n" for line in out)).encode("utf-8"))
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
