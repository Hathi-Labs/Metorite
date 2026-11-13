"""Replay the PR gate on past main commits, and print the FULL share (CIV-33c).

    uv run python scripts/replay_gate.py                 # last 300 commits
    uv run python scripts/replay_gate.py --count 50
    uv run python scripts/replay_gate.py --module /path/to/old_affected_tests.py

Each first-parent commit of ``--ref`` is one merged PR. The replay decides
FULL for the files of that commit and for its flag diff, with the selector's
own rules. It does not pick files. It reads the test files of the CURRENT
tree, so a commit that touched a file that no longer exists is replayed on
today's tests. The share is a measure, not a proof. ``--module`` loads another copy of the selector, so a run can
compare main's rules with the branch's rules.

Fence: tests/unit/test_replay_gate.py.
"""

from __future__ import annotations

import argparse
import collections
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _git(*args: str) -> str:
    out = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True, encoding="utf-8"
    )
    return out.stdout


def load_selector(path: Path):
    spec = importlib.util.spec_from_file_location("affected_tests_replay", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    sys.path.insert(0, str(ROOT / "scripts"))  # fences.py sits beside the selector
    spec.loader.exec_module(mod)
    # The tree does not change during a replay, so read each file set once.
    reads: dict[tuple, dict] = {}
    real = mod._texts

    def cached(files, root):
        key = (tuple(files), str(root))
        if key not in reads:
            reads[key] = real(files, root)
        return reads[key]

    mod._texts = cached
    return mod


def reason_key(reason: str) -> str:
    """Group the reasons: a module or a path becomes its rule."""
    if "test files import it" in reason:
        return "hub"
    if reason.startswith("selector error"):
        return "selector error"
    path = reason.split(":", 1)[0]
    if path.startswith(".github/"):
        return ".github/"
    if path.startswith("packages/acb_common/"):
        return "packages/acb_common/"
    if path.startswith("scripts/ci_pc/"):
        return "scripts/ci_pc/"
    if "conftest" in reason:
        return "conftest.py"
    if "shared test helper" in reason:
        return "shared test helper"
    return path


def _hub_index(selector) -> tuple[dict[str, set[str]], int]:
    """Map each dotted name to its importing test files, for the current tree.

    One pass over the tree. `gate` builds this index for each diff, and that
    takes about 90 s on this PC, so a 300-commit replay would take hours."""
    root = ROOT
    tests = selector._texts(sorted((root / selector.TESTS).glob("test_*.py")), root)
    sources = selector._texts(selector._source_files(root), root)
    mods = {
        m
        for f in sources
        if f.endswith(".py")
        for m in selector.module_names(f, root)
    }
    _, test_users = selector._import_index(mods, sources, tests, root)
    return test_users, len(tests)


def replay(selector, commits: list[str]) -> list[tuple[str, dict]]:
    """The FULL decision of `gate` for each commit, without the file selection.

    The static rules and the hub rule are the ones that decide FULL. The replay
    calls the selector's own `static_reasons` and `hub_reasons`. A selector that
    lacks them (main's copy, before CIV-33c-1) is replayed on its own
    `_full_reason` and its HUB_LIMIT."""
    new = hasattr(selector, "hub_reasons")
    test_users, n_tests = _hub_index(selector)
    out = []
    for c in commits:
        names = _git("diff", "--name-only", "--no-renames", f"{c}^1", c)
        changed = sorted(n.strip() for n in names.splitlines() if n.strip())
        py = [
            f for f in changed
            if f.endswith(".py") and not f.startswith(selector.FRONTEND_APPS)
        ]
        if new:
            diff = selector.gate_diff(f"{c}^1", ROOT, c)
            texts = selector.gate_flag_texts(f"{c}^1", ROOT, c)
            needles = selector._flag_additions(changed, diff, texts)
            reasons = selector.static_reasons(changed, needles)
            # A flag-only diff leaves the two flag files out of `select`.
            if needles is not None:
                py = [f for f in py if f not in selector.FLAG_FILES]
        else:
            reasons = [r for r in (selector._full_reason(f) for f in changed) if r]
        mods = {m for f in py for m in selector.module_names(f, ROOT)}
        if new:
            reasons += selector.hub_reasons(mods, test_users, n_tests)
        else:
            for m in sorted(mods):
                n = len(test_users.get(m, set()))
                if n > selector.HUB_LIMIT:
                    reasons.append(f"{m}: {n} test files import it, more than {selector.HUB_LIMIT}")
        out.append((c, {"full": bool(reasons), "files": [], "reasons": reasons}))
    return out


def summarize(results: list[tuple[str, dict]]) -> str:
    full = [r for _, r in results if r["full"]]
    share = 100 * len(full) / max(len(results), 1)
    first = collections.Counter()
    any_reason = collections.Counter()
    for r in full:
        keys = [reason_key(x) for x in r["reasons"]]
        for k in sorted(set(keys)):
            any_reason[k] += 1
        # The commit counts once under its first rule, in a fixed order.
        first[sorted(set(keys))[0]] += 1
    only_github = sum(
        1 for r in full if {reason_key(x) for x in r["reasons"]} == {".github/"}
    )
    lines = [f"{len(full)} of {len(results)} commits FULL ({share:.1f}%)"]
    lines.append(f"FULL because of .github/ alone: {only_github}")
    lines.append("commits per reason (a commit may have several):")
    for k, n in any_reason.most_common(12):
        lines.append(f"  {n:4d}  {k}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ref", default="origin/main")
    ap.add_argument("--count", type=int, default=300)
    ap.add_argument("--module", type=Path, default=ROOT / "scripts" / "affected_tests.py")
    args = ap.parse_args(argv)
    shas = _git("log", "--first-parent", f"-{args.count}", "--format=%H", args.ref).split()
    shas = [s for s in shas if re.fullmatch(r"[0-9a-f]{40}", s)]
    print(summarize(replay(load_selector(args.module), shas)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
