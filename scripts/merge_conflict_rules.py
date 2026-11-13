#!/usr/bin/env python3
"""CIV-34a (D218): conflict rules for the merge bot.

The bot merges main into a PR branch and a conflict stops the merge. This
helper tries a short list of safe rules on each conflicted file. It resolves a
file only when EVERY hunk of it fits a rule. Any other file stays unresolved,
and the bot then logs a BACK.

The rules, in order, per hunk:
  trivial     one side equals the base, or both sides are equal
  union       an append-only file (UNION_FILES): keep ours, then new lines of theirs.
              tests/fences.json is merged as JSON: list arrays are united, no repeat
  table-rows  a markdown table keyed by its first cell: each side keeps its own
              rows, and one row changed on both sides is not resolvable
  additions   both sides only ADDED lines, in a Python test file or an import
              block: keep ours, then theirs. The file must compile, and ruff
              must find its imports sorted
  rebuild     infra/postgres/generated/* is rebuilt by scripts/gen_tenant_migration.py
  rerere      git rerere stored the resolution of the same conflict

The caller (scripts/home_server/merge_bot.sh) stops HIGH paths, workflow files
and infra/postgres/ outside generated/ before it calls this helper, and this
helper checks them again. It uses the standard library only.

Usage, inside a merge that stopped on a conflict:
    python3 scripts/merge_conflict_rules.py resolve --high-re REGEX
It prints "RULE <rule> <file>" for each resolution and "UNRESOLVED <file>" for
each other file. It exits 0 when every conflicted file is resolved.

Fence: tests/unit/test_merge_conflict_rules.py and tests/unit/test_merge_bot.py.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

# Append-only files. A new line of one side never replaces a line of the other.
UNION_FILES = {"project-docs/INDEX.md", "project-docs/HANDOFF.md"}
UNION_RE = re.compile(r"(^|/)CHANGELOG[^/]*\.md$", re.I)
JSON_LIST_FILES = {"tests/fences.json"}
TEST_PY_RE = re.compile(r"(^|/)(tests/.*|test_[^/]*)\.py$")
IMPORT_LINE_RE = re.compile(r"^\s*(import\s+\S|from\s+\S+\s+import\s)")
GENERATED_PREFIX = "infra/postgres/generated/"
GENERATOR = "scripts/gen_tenant_migration.py"


@dataclass
class Hunk:
    ours: list[str]
    base: list[str]
    theirs: list[str]


def _lines(text: str) -> list[str]:
    return text.splitlines(keepends=True)


def _norm(lines: list[str]) -> list[str]:
    return [ln.rstrip("\r\n") for ln in lines]


def parse_diff3(marked: str) -> list[str | Hunk]:
    """Split text from `git merge-file --diff3` into clean lines and Hunk objects."""
    out: list[str | Hunk] = []
    state = "clean"
    cur: Hunk | None = None
    for ln in _lines(marked):
        if state == "clean" and ln.startswith("<<<<<<< "):
            cur, state = Hunk([], [], []), "ours"
        elif state == "ours" and ln.startswith("||||||| "):
            state = "base"
        elif state == "base" and ln.startswith("=======") and cur is not None:
            state = "theirs"
        elif state == "ours" and ln.startswith("=======") and cur is not None:
            state = "theirs"  # a merge with no base section
        elif state == "theirs" and ln.startswith(">>>>>>> ") and cur is not None:
            out.append(cur)
            cur, state = None, "clean"
        elif state == "clean":
            out.append(ln)
        elif cur is not None:
            getattr(cur, state).append(ln)
    if state != "clean":
        raise ValueError("unbalanced conflict markers")
    return out


def three_way_marked(base: str, ours: str, theirs: str) -> str:
    """Run git merge-file on three texts and return the text with diff3 markers."""
    with tempfile.TemporaryDirectory() as d:
        paths = []
        for name, text in (("ours", ours), ("base", base), ("theirs", theirs)):
            p = Path(d) / name
            p.write_bytes(text.encode("utf-8"))
            paths.append(str(p))
        r = subprocess.run(
            [
                "git",
                "merge-file",
                "-p",
                "--diff3",
                "-L",
                "ours",
                "-L",
                "base",
                "-L",
                "theirs",
                *paths,
            ],
            capture_output=True,
        )
        if r.returncode < 0 or r.returncode > 127:
            raise ValueError("git merge-file failed")
        return r.stdout.decode("utf-8")


# ── rules on one hunk ────────────────────────────────────────────────────────


def rule_trivial(h: Hunk, path: str) -> list[str] | None:
    o, b, t = _norm(h.ours), _norm(h.base), _norm(h.theirs)
    if o == t or t == b:
        return h.ours
    if o == b:
        return h.theirs
    return None


def rule_union(h: Hunk, path: str) -> list[str] | None:
    if path not in UNION_FILES and not UNION_RE.search(path):
        return None
    have = set(_norm(h.ours))
    extra = [ln for ln in h.theirs if ln.rstrip("\r\n") not in have or not ln.strip()]
    return h.ours + extra


def _row_key(line: str) -> str:
    cell = line.strip().strip("|").split("|", 1)[0]
    return cell.strip().strip("`*").strip()


def _rows(lines: list[str]) -> dict[str, str] | None:
    rows: dict[str, str] = {}
    for ln in lines:
        s = ln.strip()
        if not s.startswith("|"):
            return None
        key = _row_key(ln)
        if not key or key in rows:
            return None
        rows[key] = ln.rstrip("\r\n")
    return rows


def rule_table_rows(h: Hunk, path: str) -> list[str] | None:
    o, b, t = _rows(h.ours), _rows(h.base), _rows(h.theirs)
    if o is None or b is None or t is None or not (o or t):
        return None
    final: dict[str, str] = {}
    for key in {*o, *b, *t}:
        ov, bv, tv = o.get(key), b.get(key), t.get(key)
        if ov == tv:
            val = ov
        elif ov == bv:
            val = tv
        elif tv == bv:
            val = ov
        else:
            return None  # the same row changed on both sides
        if val is not None:
            final[key] = val
    order = [k for k in o if k in final]
    t_keys = list(t)
    for i, key in enumerate(t_keys):
        if key in final and key not in order:
            prev = next((k for k in reversed(t_keys[:i]) if k in order), None)
            order.insert(order.index(prev) + 1 if prev is not None else 0, key)
    for key in final:  # a key that only the base had cannot be here, so this is a safety net
        if key not in order:
            order.append(key)
    return [final[k] + "\n" for k in order]


def _is_import_block(lines: list[str]) -> bool:
    body = [ln for ln in lines if ln.strip()]
    return bool(body) and all(IMPORT_LINE_RE.match(ln) for ln in body)


def rule_additions(h: Hunk, path: str) -> list[str] | None:
    if not path.endswith(".py"):
        return None
    o, b, t = _norm(h.ours), _norm(h.base), _norm(h.theirs)
    if o[: len(b)] != b or t[: len(b)] != b:
        return None  # a side removed or changed a base line
    o_new, t_new = h.ours[len(b) :], h.theirs[len(b) :]
    if not (o_new or t_new):
        return None
    if not (TEST_PY_RE.search(path) or _is_import_block(o_new + t_new)):
        return None
    return h.base + o_new + t_new


RULES = (
    ("trivial", rule_trivial),
    ("union", rule_union),
    ("table-rows", rule_table_rows),
    ("additions", rule_additions),
)


def _merge_json(b, o, t, missing):
    if o == t:
        return o
    if o == b:
        return t
    if t == b:
        return o
    if isinstance(o, dict) and isinstance(t, dict) and isinstance(b, (dict, type(missing))):
        base = b if isinstance(b, dict) else {}
        out = {}
        for key in [*o, *[k for k in t if k not in o]]:
            val = _merge_json(
                base.get(key, missing), o.get(key, missing), t.get(key, missing), missing
            )
            if val is missing:
                continue
            out[key] = val
        return out
    if isinstance(o, list) and isinstance(t, list) and isinstance(b, list):
        if any(isinstance(x, (dict, list)) for x in (*o, *t, *b)):
            raise ValueError("a list of objects")
        kept = [x for x in o if not (x in b and x not in t)]
        merged = kept + [x for x in t if x not in b and x not in kept]
        merged = list(dict.fromkeys(merged))
        return sorted(merged) if o == sorted(o) and t == sorted(t) else merged
    raise ValueError("a value changed on both sides")


def resolve_json(base: str, ours: str, theirs: str) -> str | None:
    """Merge a JSON file whose conflicts are list arrays. Returns None when it cannot."""
    missing = object()
    try:
        b, o, t = (json.loads(x) if x.strip() else missing for x in (base, ours, theirs))
        if missing in (o, t):
            return None
        merged = _merge_json(b if b is not missing else {}, o, t, missing)
    except (ValueError, TypeError):
        return None
    return json.dumps(merged, indent=2, ensure_ascii=False) + "\n"


def resolve_file(path: str, base: str, ours: str, theirs: str) -> tuple[str, list[str]] | None:
    """Resolve one file from its three versions. Returns (text, rules) or None."""
    if path in JSON_LIST_FILES:
        text = resolve_json(base, ours, theirs)
        return (text, ["union"]) if text is not None else None
    add_add = base == "" and ours != "" and theirs != ""
    try:
        parts = parse_diff3(three_way_marked(base, ours, theirs))
    except ValueError:
        return None
    text, rules = [], []
    for part in parts:
        if isinstance(part, str):
            text.append(part)
            continue
        for name, rule in RULES:
            if add_add and name != "trivial":
                continue
            fixed = rule(part, path)
            if fixed is not None:
                text.extend(fixed)
                rules.append(name)
                break
        else:
            return None
    result = "".join(text)
    if path.endswith(".py") and "additions" in rules:
        try:
            compile(result, path, "exec")
        except (SyntaxError, ValueError):
            return None
    return result, sorted(set(rules), key=[n for n, _ in RULES].index)


# ── the git layer ────────────────────────────────────────────────────────────


def _git(*args: str, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], capture_output=True, check=check)


def _stage(n: int, path: str) -> str | None:
    r = _git("show", f":{n}:{path}")
    return r.stdout.decode("utf-8") if r.returncode == 0 else None


def _ruff_cmd() -> list[str] | None:
    forced = os.environ.get("CONFLICT_RULES_RUFF")
    if forced:
        return [forced]
    exe = shutil.which("ruff")
    if exe:
        return [exe]
    r = subprocess.run([sys.executable, "-m", "ruff", "--version"], capture_output=True)
    return [sys.executable, "-m", "ruff"] if r.returncode == 0 else None


def sort_imports(path: str) -> bool:
    """Sort the imports of one file with ruff, and confirm they are sorted."""
    ruff = _ruff_cmd()
    if ruff is None:
        return False
    fix = subprocess.run(
        [*ruff, "check", "--select", "I", "--fix", "--quiet", path], capture_output=True
    )
    check = subprocess.run([*ruff, "check", "--select", "I", "--quiet", path], capture_output=True)
    return fix.returncode in (0, 1) and check.returncode == 0


def blocked(path: str, high_re: str | None) -> bool:
    if path.startswith(".github/workflows/"):
        return True
    if path.startswith("infra/postgres/") and not path.startswith(GENERATED_PREFIX):
        return True
    return (
        bool(high_re)
        and path.startswith(GENERATED_PREFIX) is False
        and re.search(high_re, path) is not None
    )


def rebuild_generated() -> bool:
    if not Path(GENERATOR).is_file():
        return False
    r = subprocess.run([sys.executable, GENERATOR], capture_output=True)
    return r.returncode == 0


def resolve_merge(high_re: str | None) -> tuple[list[tuple[str, str]], list[str]]:
    """Resolve every unmerged file of the merge in progress. Returns (resolutions, unresolved)."""
    unmerged = _git("diff", "--name-only", "--diff-filter=U", "-z").stdout.decode("utf-8")
    paths = [p for p in unmerged.split("\0") if p]
    remaining = {
        p
        for p in _git("-c", "rerere.enabled=true", "rerere", "remaining")
        .stdout.decode("utf-8")
        .splitlines()
    }
    done: list[tuple[str, str]] = []
    bad: list[str] = []
    generated: list[str] = []
    for path in paths:
        if blocked(path, high_re):
            bad.append(path)
            continue
        if path.startswith(GENERATED_PREFIX):
            generated.append(path)
            continue
        text = (
            Path(path).read_text(encoding="utf-8", errors="replace") if Path(path).is_file() else ""
        )
        if path not in remaining and text and not re.search(r"^<{7} ", text, re.M):
            _git("add", "--", path)
            done.append(("rerere", path))
            continue
        base, ours, theirs = _stage(1, path), _stage(2, path), _stage(3, path)
        if ours is None or theirs is None:
            bad.append(path)  # a delete against an edit
            continue
        res = resolve_file(path, base or "", ours, theirs)
        if res is None:
            bad.append(path)
            continue
        Path(path).write_bytes(res[0].encode("utf-8"))
        if "additions" in res[1] and not sort_imports(path):
            bad.append(path)
            continue
        _git("add", "--", path)
        done.extend((rule, path) for rule in res[1])
    if generated:
        if rebuild_generated():
            for path in generated:
                _git("add", "--", path)
            done.extend(("rebuild", p) for p in generated)
        else:
            bad.extend(generated)
    return done, bad


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    rp = sub.add_parser("resolve", help="resolve the merge in progress")
    rp.add_argument("--high-re", default=None)
    args = ap.parse_args(argv)
    done, bad = resolve_merge(args.high_re)
    for rule, path in done:
        print(f"RULE {rule} {path}")
    for path in bad:
        print(f"UNRESOLVED {path}")
    return 1 if bad or not (done or bad) else 0


if __name__ == "__main__":
    sys.exit(main())
