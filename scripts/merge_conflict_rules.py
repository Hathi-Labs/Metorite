#!/usr/bin/env python3
"""CIV-34a (D218): conflict rules for the merge bot.

The bot merges main into a PR branch and a conflict stops the merge. This
helper tries a short list of safe rules on each conflicted file. It resolves a
file only when EVERY hunk of it fits a rule. Any other file stays unresolved,
and the bot then logs a BACK.

The rules, in order, per hunk:
  trivial     one side equals the base, or both sides are equal
  table-rows  rows of a markdown table (*.md only), keyed by the first cell.
              Each side keeps its own rows. The rule refuses a row that changed
              on both sides, a header or separator row that changed, and a row
              with a cell count that is not the cell count of the header
  additions   both sides only ADDED lines, in a Python test file or an import
              block: keep ours, then theirs. ruff must find the imports sorted
One file has a rule of its own:
  union       tests/fences.json: the helper merges it as JSON, and unites the
              list arrays with no repeat

.gitattributes gives INDEX.md, HANDOFF.md and .env.example merge=union, so git
never leaves a conflict in them. There is no rerere rule. There is no rule for
infra/postgres/, because the tier floor of the bot stops a PR that touches it.

The helper checks each resolved file before it stages it:
  - no line of the result is a conflict marker
  - a Python result compiles, and no function or class name occurs more often
    in one scope than it does in ours or in theirs
It refuses a conflicted path when a side holds a NUL byte or is not UTF-8, or
when the path has the `binary` or `-merge` attribute.

Usage, inside a merge that stopped on a conflict:
    python3 scripts/merge_conflict_rules.py resolve --high-re REGEX
It prints "RULE <rule> <file>" for each resolution and "UNRESOLVED <file>" for
each other file. It exits 0 when every conflicted file is resolved.

    python3 scripts/merge_conflict_rules.py tests <resolved file>...
It prints the tests that the resolution reaches, in the tree of the current
folder: "SECONDS <n>" (from .test_durations), then one "TEST <file>" line each.
It prints "FULL" when a resolved file reaches every test.

It uses the standard library only. The callers are
scripts/home_server/merge_bot.sh and the tests.
Fence: tests/unit/test_merge_conflict_rules.py and tests/unit/test_merge_bot.py.
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import warnings
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

JSON_LIST_FILES = {"tests/fences.json"}
TEST_PY_RE = re.compile(r"(^|/)(tests/.*|test_[^/]*)\.py$")
TEST_FILE_RE = re.compile(r"(^|/)test_[^/]*\.py$")
IMPORT_LINE_RE = re.compile(r"^\s*(import\s+\S|from\s+\S+\s+import\s)")
# git merge-file writes markers of this size. The parser matches each marker
# line exactly, so an RST border or a line of "=" in a docstring is text.
MARKER_SIZE = 31
M_OURS = "<" * MARKER_SIZE + " ours"
M_BASE = "|" * MARKER_SIZE + " base"
M_SEP = "=" * MARKER_SIZE
M_THEIRS = ">" * MARKER_SIZE + " theirs"
OWN_MARKERS = {M_OURS, M_BASE, M_SEP, M_THEIRS}
# A marker that git leaves at its default size. No result may hold one.
GIT_MARKER_RE = re.compile(r"^(<{7}|={7}|>{7}|\|{7})( |$)")
# A separator row of a markdown table: | --- | :--: |
SEP_RE = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$")


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
    """Split text from `git merge-file --diff3` into clean lines and Hunk objects.

    Only a line that equals a marker of MARKER_SIZE starts or ends a section.
    """
    out: list[str | Hunk] = []
    state = "clean"
    cur: Hunk | None = None
    for ln in _lines(marked):
        bare = ln.rstrip("\r\n")
        if state == "clean" and bare == M_OURS:
            cur, state = Hunk([], [], []), "ours"
        elif state == "ours" and bare == M_BASE:
            state = "base"
        elif state in ("ours", "base") and bare == M_SEP:
            state = "theirs"
        elif state == "theirs" and bare == M_THEIRS and cur is not None:
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
                f"--marker-size={MARKER_SIZE}",
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


def _has_marker(text: str, *, own_only: bool = False) -> bool:
    """True when a line equals a marker of MARKER_SIZE, or (own_only False) looks like a git marker."""
    for ln in _norm(_lines(text)):
        if ln in OWN_MARKERS or (not own_only and GIT_MARKER_RE.match(ln)):
            return True
    return False


# ── rules on one hunk ────────────────────────────────────────────────────────


def rule_trivial(h: Hunk, path: str) -> list[str] | None:
    o, b, t = _norm(h.ours), _norm(h.base), _norm(h.theirs)
    if o == t or t == b:
        return h.ours
    if o == b:
        return h.theirs
    return None


def _is_sep(line: str) -> bool:
    s = line.rstrip("\r\n")
    return "-" in s and SEP_RE.match(s) is not None


def _cells(line: str) -> int:
    """The cell count of a table row. An escaped pipe (\\|) is text."""
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    return len(re.split(r"(?<!\\)\|", s))


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
    if not path.lower().endswith(".md"):
        return None
    if any(_is_sep(ln) for ln in (*h.ours, *h.base, *h.theirs)):
        return None  # a separator row in the hunk: the header changed
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


def _table_ok(lines: list[str], start: int, end: int, versions: list[set[str]]) -> bool:
    """The table around lines[start:end] keeps its header, and each row has its cell count.

    The header and the separator row sit above the hunk, and base, ours and
    theirs all hold them unchanged.
    """
    top = start
    while top > 0 and lines[top - 1].lstrip().startswith("|"):
        top -= 1
    bottom = end
    while bottom < len(lines) and lines[bottom].lstrip().startswith("|"):
        bottom += 1
    if start - top < 2:
        return False  # the hunk holds the header or the separator row
    header, sep = lines[top].rstrip("\r\n"), lines[top + 1].rstrip("\r\n")
    if _is_sep(header) or not _is_sep(sep):
        return False
    if any(header not in v or sep not in v for v in versions):
        return False  # one side changed the header or the separator row
    width = _cells(header)
    return all(_cells(ln) == width for ln in lines[top + 1 : bottom])


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
    ("table-rows", rule_table_rows),
    ("additions", rule_additions),
)
RULE_ORDER = ["union", *(n for n, _ in RULES)]


def _def_counts(text: str) -> Counter | None:
    """How often each function and class name occurs, per module or class scope."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return None
    counts: Counter = Counter()

    def walk(body: list[ast.stmt], scope: str) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                counts[(scope, node.name)] += 1
            if isinstance(node, ast.ClassDef):
                walk(node.body, f"{scope}.{node.name}")

    walk(tree.body, "")
    return counts


def python_ok(path: str, result: str, ours: str, theirs: str) -> bool:
    """The result compiles, and the merge adds no second def or class of one name.

    Python keeps the last of two defs with one name, so a test or a fixture
    would vanish with no error. A name that one side already repeats (a
    property setter, an overload) does not count.
    """
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            compile(result, path, "exec")
    except (SyntaxError, ValueError):
        return False
    got = _def_counts(result)
    if got is None:
        return False
    sides = [_def_counts(x) or Counter() for x in (ours, theirs)]
    return all(n < 2 or n <= max(s[key] for s in sides) for key, n in got.items())


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
        return (text, ["union"]) if text is not None and not _has_marker(text) else None
    # A text line that equals a marker of MARKER_SIZE would confuse the parser.
    if any(_has_marker(x, own_only=True) for x in (base, ours, theirs)):
        return None
    add_add = base == "" and ours != "" and theirs != ""
    try:
        parts = parse_diff3(three_way_marked(base, ours, theirs))
    except ValueError:
        return None
    text: list[str] = []
    rules: list[str] = []
    tables: list[tuple[int, int]] = []
    for part in parts:
        if isinstance(part, str):
            text.append(part)
            continue
        for name, rule in RULES:
            if add_add and name != "trivial":
                continue
            fixed = rule(part, path)
            if fixed is not None:
                if name == "table-rows":
                    tables.append((len(text), len(text) + len(fixed)))
                text.extend(fixed)
                rules.append(name)
                break
        else:
            return None
    if tables:
        versions = [set(_norm(_lines(x))) for x in (base, ours, theirs)]
        if not all(_table_ok(text, s, e, versions) for s, e in tables):
            return None
    result = "".join(text)
    if _has_marker(result):
        return None
    if path.endswith(".py") and not python_ok(path, result, ours, theirs):
        return None
    # merge-file found no conflict that git found: the clean 3-way text is the answer.
    return result, sorted(set(rules or ["trivial"]), key=RULE_ORDER.index)


# ── the tests that a resolution reaches ──────────────────────────────────────


def _affected_tests():
    """scripts/affected_tests.py beside this file. It uses the standard library only."""
    name = "affected_tests"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _seconds(root: Path, files: set[str]) -> float:
    """The run time of the files, from .test_durations. A file with no entry counts 0."""
    try:
        data = json.loads((root / ".test_durations").read_text(encoding="utf-8"))
        per: Counter = Counter()
        for node, sec in data.items():
            per[node.split("::", 1)[0]] += float(sec)
    except (OSError, ValueError, TypeError, AttributeError):
        return 0.0
    return round(sum(per[f] for f in files), 1)


def select_tests(paths: list[str], root: Path) -> dict:
    """The tests that the resolved files reach: the conflicted test files, and the
    selection of affected_tests.select with no scanners.

    pr-check runs the full gate on the new head. This set is the check before
    the push, so it stays small: no scanners and no safety floor.
    """
    picked, full, _frontend = _affected_tests().select(list(paths), root, scanners=False)
    files = set(picked) | {p for p in paths if TEST_FILE_RE.search(p) and (root / p).is_file()}
    return {"full": bool(full), "files": sorted(files), "seconds": _seconds(root, files)}


# ── the git layer ────────────────────────────────────────────────────────────


def _git(*args: str, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], capture_output=True, check=check)


def _stage(n: int, path: str) -> bytes | None:
    r = _git("show", f":{n}:{path}")
    return r.stdout if r.returncode == 0 else None


def no_text_merge(path: str) -> bool:
    """True when an attribute says that git must not merge the path as text."""
    r = _git("check-attr", "-z", "binary", "merge", "--", path)
    if r.returncode != 0:
        return True
    parts = r.stdout.decode("utf-8", "replace").split("\0")
    vals = {parts[i + 1]: parts[i + 2] for i in range(0, len(parts) - 2, 3)}
    return vals.get("binary") == "set" or vals.get("merge") in ("unset", "binary")


def _ruff_cmd() -> list[str] | None:
    """ruff from the venv of the checkout, else through uv. None means no ruff, so refuse.

    The unit PATH of the box has no ruff. The bot runs in its own checkout, and
    the venv of that checkout has one.
    """
    forced = os.environ.get("CONFLICT_RULES_RUFF")
    if forced:
        return [forced]
    for rel in (".venv/bin/ruff", ".venv/Scripts/ruff.exe"):
        p = Path.cwd() / rel
        if p.is_file():
            return [str(p)]
    uv = shutil.which("uv")
    if uv:
        cmd = [uv, "run", "--no-sync", "ruff"]
        if subprocess.run([*cmd, "--version"], capture_output=True).returncode == 0:
            return cmd
    return None


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
    if path.startswith((".github/workflows/", "infra/postgres/")):
        return True
    return bool(high_re) and re.search(high_re, path) is not None


def resolve_merge(high_re: str | None) -> tuple[list[tuple[str, str]], list[str]]:
    """Resolve every unmerged file of the merge in progress. Returns (resolutions, unresolved)."""
    unmerged = _git("diff", "--name-only", "--diff-filter=U", "-z").stdout.decode("utf-8")
    paths = [p for p in unmerged.split("\0") if p]
    done: list[tuple[str, str]] = []
    bad: list[str] = []
    for path in paths:
        if blocked(path, high_re) or no_text_merge(path):
            bad.append(path)
            continue
        raw = [_stage(n, path) for n in (1, 2, 3)]
        if raw[1] is None or raw[2] is None:
            bad.append(path)  # a delete against an edit
            continue
        if any(b"\0" in x for x in raw if x is not None):
            bad.append(path)  # a binary file
            continue
        try:
            base, ours, theirs = (x.decode("utf-8") if x is not None else "" for x in raw)
        except UnicodeDecodeError:
            bad.append(path)
            continue
        res = resolve_file(path, base, ours, theirs)
        if res is None:
            bad.append(path)
            continue
        Path(path).write_bytes(res[0].encode("utf-8"))
        if "additions" in res[1] and not sort_imports(path):
            bad.append(path)
            continue
        _git("add", "--", path)
        done.extend((rule, path) for rule in res[1])
    return done, bad


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    rp = sub.add_parser("resolve", help="resolve the merge in progress")
    rp.add_argument("--high-re", default=None)
    tp = sub.add_parser("tests", help="print the tests that the resolved files reach")
    tp.add_argument("paths", nargs="*")
    args = ap.parse_args(argv)
    if args.cmd == "tests":
        sel = select_tests(args.paths, Path.cwd())
        if sel["full"]:
            print("FULL")
            return 0
        print(f"SECONDS {math.ceil(sel['seconds'])}")
        for f in sel["files"]:
            print(f"TEST {f}")
        return 0
    done, bad = resolve_merge(args.high_re)
    for rule, path in done:
        print(f"RULE {rule} {path}")
    for path in bad:
        print(f"UNRESOLVED {path}")
    return 1 if bad or not (done or bad) else 0


if __name__ == "__main__":
    sys.exit(main())
