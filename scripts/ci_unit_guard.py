"""Check the sharded unit run as one run, and refresh the split timings.

pr-check.yml runs ``tests/unit/`` in parallel shards (pytest-split). Each
shard writes a JUnit report. The ``Unit tests`` job reads all of them here.

``check`` fails when:

* a test skipped because ``CUSTOMER_CONSOLE_DATABASE_URL`` or
  ``TENANT_LADDER_DATABASE_URL`` was unset (the R8 gate is disarmed),
* a ``--must-run`` file has no test in any report (no shard ran it), or
* a ``--never-skip`` file skipped a test (a database gate leaked into a fence
  that needs none).

This replaces a step that ran about 40 R8 files a second time only to grep
their skip reasons. The reports already hold those reasons.

``durations`` merges the reports into the ``.test_durations`` format, so the
timings file can be refreshed from a real CI run without a 50-minute local run.

``--only-selected`` (CIV-3) limits ``--must-run`` to the files that the PR tier
selected. ``gate-output``, ``select`` and ``empty-report`` carry that selection
between the ``changes`` job, the shards and this guard. A selection run is a
subset, so the shards and this guard never treat an absent file as lost.

Stdlib only: the ``Unit tests`` job runs it without ``uv sync``.
Fence: tests/unit/test_ci_unit_guard.py.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

# The reason must START with the variable. A test elsewhere skips with
# "AGENT_WRITES_REDIS_URL or TENANT_LADDER_DATABASE_URL unset" when CI has no
# Redis, and that skip is expected. The workflow passes these with --disarmed.
# A job output has a 1 MB limit. A selection longer than this runs the full
# suite instead, and a path outside this shape does the same.
MAX_SELECTION_CHARS = 200_000
_SAFE_PATH = re.compile(r"^tests/[A-Za-z0-9_][A-Za-z0-9_./-]*\.py$")

DISARMED = ("CUSTOMER_CONSOLE_DATABASE_URL unset", "TENANT_LADDER_DATABASE_URL unset")


def _cases(junit_dir: Path) -> list[ET.Element]:
    reports = sorted(junit_dir.rglob("*.xml"))
    if not reports:
        raise SystemExit(f"no JUnit reports under {junit_dir}")
    return [case for r in reports for case in ET.parse(r).getroot().iter("testcase")]


def _file(case: ET.Element) -> str:
    # xunit1 carries `file`. Fall back to the dotted classname for xunit2.
    if case.get("file"):
        return case.get("file", "").replace("\\", "/")
    parts = case.get("classname", "").split(".")
    while parts and not parts[-1].startswith("test"):
        parts.pop()
    return "/".join(parts) + ".py"


def _skip_reason(case: ET.Element) -> str | None:
    """The skip reason, or None when the case did not skip.

    A module that skips at collection (`allow_module_level=True`) is written
    with message "collection skipped" and the real reason in the element text.
    """
    skipped = case.find("skipped")
    if skipped is None:
        return None
    message = skipped.get("message", "")
    if message == "collection skipped":
        return " ".join((skipped.text or "").split()) or message
    return message


def _collection_skipped(case: ET.Element) -> bool:
    skipped = case.find("skipped")
    return skipped is not None and skipped.get("message") == "collection skipped"


def check(
    junit_dir: Path,
    must_run: list[str],
    never_skip: list[str],
    disarmed: tuple[str, ...] | list[str] = DISARMED,
    only_selected: list[str] | None = None,
    tree_root: Path | None = None,
) -> list[str]:
    """``only_selected`` None is a full run. A list (even empty) is a selection
    run, and then only the must-run files in that list have to run.

    ``tree_root`` set: EVERY must-run path has to exist there, selection or
    not. A PR that deletes an R8 suite but keeps its line would otherwise pass
    here and turn the next full run red."""
    missing = [
        f"a --must-run file is not in the tree: {p}. Delete its line in pr-check.yml too."
        for p in must_run
        if tree_root is not None and not (tree_root / p).is_file()
    ]
    if only_selected is not None:
        chosen = {p.replace("\\", "/") for p in only_selected}
        must_run = [p for p in must_run if p in chosen]
    cases = _cases(junit_dir)
    problems: list[str] = list(missing)
    ran: set[str] = set()
    for case in cases:
        path = _file(case)
        # A module skipped whole at collection ran nothing.
        if not _collection_skipped(case):
            ran.add(path)
        reason = _skip_reason(case)
        if reason is None:
            continue
        # The collection text carries a "Skipped:" prefix, so search it.
        if reason.startswith(tuple(disarmed)) or (
            _collection_skipped(case) and any(f"Skipped: {d}" in reason for d in disarmed)
        ):
            problems.append(f"R8 gate disarmed: {path}::{case.get('name')} skipped: {reason}")
        if path in never_skip:
            problems.append(f"a no-database fence skipped: {path}::{case.get('name')}: {reason}")
    for path in must_run:
        if path not in ran:
            problems.append(f"no shard ran {path}")
    print(f"checked {len(cases)} tests from {len(ran)} files")
    return problems


def durations(junit_dir: Path) -> dict[str, float]:
    out: dict[str, float] = {}
    for case in _cases(junit_dir):
        path = _file(case)
        module = path[: -len(".py")].replace("/", ".")
        klass = case.get("classname", "")
        inner = klass[len(module) + 1 :] if klass.startswith(module + ".") else ""
        node = "::".join([path, *inner.split(".")] if inner else [path])
        out[f"{node}::{case.get('name')}"] = round(float(case.get("time", 0) or 0), 3)
    return dict(sorted(out.items()))


# CIV-17. How many unit shards a selection needs. A tier is (the most test
# files it takes, the shards it gets). A larger selection, and a full run,
# take ALL_SHARDS. The limits are a first guess, not a measurement: pr-check
# prints the count in each run, so re-pick them from the next 10 PRs.
ALL_SHARDS = 6
SHARD_TIERS = ((40, 1), (120, 3))


def shard_count(mode: str, paths: list[str]) -> int:
    """The shards that ``mode`` (from ``select_shard``) needs: 0 for an empty
    selection, ALL_SHARDS for a full run or a large selection."""
    if mode == "empty":
        return 0
    if mode != "selection":
        return ALL_SHARDS
    for limit, shards in SHARD_TIERS:
        if len(paths) <= limit:
            return shards
    return ALL_SHARDS


def shard_list(count: int) -> str:
    """The ``shards`` job output: a JSON list that the ``test`` matrix reads."""
    return json.dumps(list(range(1, count + 1)), separators=(",", ":"))


def gate_output(gate_json: Path, ignored: str = "") -> list[str]:
    """``full=``, ``files=`` and ``shards=`` lines for $GITHUB_OUTPUT.

    Fails open to full (and so to all shards). ``ignored`` is the output of
    ``fences.py pytest-ignore``: a shard drops those files from the selection,
    so the count has to drop them too."""
    lines = _gate_lines(gate_json)
    full = lines[0].partition("=")[2]
    files = lines[1].partition("=")[2]
    mode, paths = select_shard(full, files, ignored, "success")
    return [*lines, f"shards={shard_list(shard_count(mode, paths))}"]


def _gate_lines(gate_json: Path) -> list[str]:
    try:
        data = json.loads(gate_json.read_text(encoding="utf-8"))
        full = bool(data["full"])
        files = [str(f) for f in data["files"]]
        if not full and not all(_SAFE_PATH.match(f) for f in files):
            return ["full=true", "files="]
        text = " ".join(files)
        if len(text) > MAX_SELECTION_CHARS:
            return ["full=true", "files="]
    except Exception:  # unreadable or malformed: run everything
        return ["full=true", "files="]
    return [f"full={'true' if full else 'false'}", f"files={'' if full else text}"]


def select_shard(
    full: str, files: str, ignored: str = "", changes_result: str = "success"
) -> tuple[str, list[str]]:
    """What a shard runs: ``full``, ``selection`` or ``empty``.

    Only the exact string ``false`` selects. An empty or unknown ``full`` (the
    ``changes`` job failed) runs the whole suite. A selection never reaches
    pytest as an empty list, because pytest then collects the whole tree."""
    if full != "false" or changes_result != "success":
        return "full", []
    paths = files.split()
    if not all(_SAFE_PATH.match(p) for p in paths):
        return "full", []
    skip = {t.removeprefix("--ignore=") for t in ignored.split()}
    paths = [p for p in paths if p not in skip]
    return ("selection", paths) if paths else ("empty", [])


_EMPTY_REPORT = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<testsuites><testsuite name="pytest" errors="0" failures="0" skipped="0" '
    'tests="0" time="0.0"></testsuite></testsuites>'
)


def empty_report(out: Path) -> bool:
    """Write a zero-test JUnit report unless pytest already wrote one."""
    if out.exists():
        return False
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_EMPTY_REPORT, encoding="utf-8")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--junit", type=Path, required=True)
    c.add_argument("--must-run", nargs="*", default=[])
    c.add_argument("--never-skip", nargs="*", default=[])
    c.add_argument(
        "--only-selected",
        nargs="*",
        default=None,
        help="a selection run: check only the must-run files in this list",
    )
    c.add_argument(
        "--tree-root",
        type=Path,
        default=Path("."),
        help="the checkout: every --must-run path has to exist under it",
    )
    c.add_argument(
        "--disarmed",
        action="append",
        help="a skip reason that means an R8 gate is disarmed (repeatable)",
    )
    d = sub.add_parser("durations")
    d.add_argument("--junit", type=Path, required=True)
    d.add_argument("--out", type=Path, required=True)
    g = sub.add_parser("gate-output", help="turn affected_tests.py --gate JSON into step outputs")
    g.add_argument("--json", type=Path, required=True)
    g.add_argument("--ignored", default="", help="the output of fences.py pytest-ignore")
    sel = sub.add_parser("select", help="what this shard runs, as step outputs")
    sel.add_argument("--full", default="")
    sel.add_argument("--files", default="")
    sel.add_argument("--changes-result", default="", help="needs.changes.result")
    sel.add_argument("--ignored", default="", help="the output of fences.py pytest-ignore")
    e = sub.add_parser("empty-report", help="write a zero-test JUnit report if none exists")
    e.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    if args.cmd == "gate-output":
        print(chr(10).join(gate_output(args.json, args.ignored)))
        return 0
    if args.cmd == "select":
        mode, paths = select_shard(args.full, args.files, args.ignored, args.changes_result)
        print(f"mode={mode}")
        print(f"paths={' '.join(paths)}")
        print(f"splits={shard_count(mode, paths) or ALL_SHARDS}")
        return 0
    if args.cmd == "empty-report":
        print("wrote an empty report" if empty_report(args.out) else "report exists")
        return 0

    if args.cmd == "durations":
        data = durations(args.junit)
        args.out.write_text(json.dumps(data, indent=0) + "\n", encoding="utf-8")
        print(f"wrote {len(data)} timings to {args.out}")
        return 0

    problems = check(
        args.junit,
        args.must_run,
        args.never_skip,
        args.disarmed or DISARMED,
        args.only_selected,
        args.tree_root,
    )
    for p in problems:
        print(f"::error::{p}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
