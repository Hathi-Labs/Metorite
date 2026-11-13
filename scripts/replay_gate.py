"""Replay the PR gate on past main commits, and print the FULL share (CIV-33c).

    uv run python scripts/replay_gate.py                 # last 300 commits
    uv run python scripts/replay_gate.py --count 50
    uv run python scripts/replay_gate.py --module /path/to/old_affected_tests.py

Each first-parent commit of ``--ref`` is one merged PR. The replay runs
``affected_tests.gate`` on the files of that commit and on its flag diff.
It reads the test files of the CURRENT tree, so a commit that touched a file
that no longer exists is replayed on today's tests. The share is a measure,
not a proof. ``--module`` loads another copy of the selector, so a run can
compare main's rules with the branch's rules.

Fence: tests/unit/test_replay_gate.py.
"""

from __future__ import annotations

import argparse
import collections
import importlib.util
import inspect
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


def replay(selector, commits: list[str]) -> list[tuple[str, dict]]:
    takes_diff = "diff" in inspect.signature(selector.gate).parameters
    out = []
    for c in commits:
        names = _git("diff", "--name-only", "--no-renames", f"{c}^1", c)
        changed = sorted(n.strip() for n in names.splitlines() if n.strip())
        kwargs = {}
        if takes_diff:
            kwargs["diff"] = selector.gate_diff(f"{c}^1", ROOT, c)
        out.append((c, selector.gate(changed, ROOT, **kwargs)))
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
