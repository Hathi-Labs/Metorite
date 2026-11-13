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

``r8-covered`` (CIV-4c) reads the report of deploy.yml's no-database unit run
and the ``affected_tests.py --r8`` list. It fails when a file skipped for an
R8 reason and is not in the list, because the release gate never runs it.

``check --lists-from`` (CIV-30) reads the three lists from one step of a
workflow file, not from its own command line. deploy.yml's R8 gate gives it
the release commit's copy of deploy.yml.

Stdlib only: the ``Unit tests`` job runs it without ``uv sync``.
Fence: tests/unit/test_ci_unit_guard.py, and tests/unit/test_green_pointer.py
for ``--lists-from``.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shlex
import sys
import textwrap
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


# ── The lists of the release commit (CIV-30) ──────────────────────────────
# On a slot run, GitHub runs deploy.yml from the TIP of main, and the R8 gate
# tests the release commit (`green/main`). The tip's lists can name a suite
# that landed after the release commit, and the guard then fails on a file
# that is not in the release tree. So deploy.yml's guard step gives
# `--lists-from` the release commit's copy of deploy.yml, and the guard reads
# the lists from the same step of that copy. Stdlib only, so no YAML parser:
# the reader takes the `run: |` block of one named step, and fails closed on
# any other shape.
LIST_FLAGS = ("--disarmed", "--never-skip", "--must-run")
_GUARD_CMD = re.compile(r"(?:^|\s)python3\s+scripts/ci_unit_guard\.py\s+check(?:\s|$)")


class GuardListError(ValueError):
    """The guard cannot read its lists. The guard then fails closed."""


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _step_run(workflow: str, step: str) -> str:
    """The `run: |` text of the ONE step named ``step``, dedented."""
    lines = workflow.splitlines()
    heads = [
        i
        for i, ln in enumerate(lines)
        if ln.lstrip().startswith("- name:")
        and ln.split("- name:", 1)[1].strip().strip("\"'") == step
    ]
    if len(heads) != 1:
        raise GuardListError(f"found {len(heads)} steps named {step!r}, and the guard needs one")
    dash = _indent(lines[heads[0]])
    run_at = None
    for i in range(heads[0] + 1, len(lines)):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if _indent(line) <= dash:
            break
        if _indent(line) == dash + 2 and line.strip() in ("run: |", "run: |-"):
            run_at = i
            break
    if run_at is None:
        raise GuardListError(f"the step {step!r} has no `run: |` block")
    body: list[str] = []
    for line in lines[run_at + 1 :]:
        if line.strip() and _indent(line) <= dash + 2:
            break
        body.append(line)
    return textwrap.dedent("\n".join(body))


def guard_lists(workflow: str, step: str) -> dict[str, list[str]]:
    """``--disarmed``, ``--never-skip`` and ``--must-run`` of the one
    ``ci_unit_guard.py check`` command in the step ``step`` of ``workflow``.

    Fails closed: no such step, no such command, an empty ``--never-skip``
    or ``--must-run`` list, or a path that is not a test path."""
    commands = [
        ln for ln in _step_run(workflow, step).replace("\\\n", " ").splitlines() if _GUARD_CMD.search(ln)
    ]
    if len(commands) != 1:
        raise GuardListError(
            f"the step {step!r} holds {len(commands)} `ci_unit_guard.py check` commands, not one"
        )
    words = shlex.split(commands[0], comments=True)
    out: dict[str, list[str]] = {flag: [] for flag in LIST_FLAGS}
    flag = ""
    for word in words[words.index("check") + 1 :]:
        if word.startswith("--"):
            flag = word
        elif flag in out:
            out[flag].append(word)
    for flag in ("--never-skip", "--must-run"):
        if not out[flag]:
            raise GuardListError(f"the step {step!r} gives no {flag} list")
        bad = [p for p in out[flag] if not _SAFE_PATH.match(p)]
        if bad:
            raise GuardListError(f"the {flag} list of {step!r} holds a value that is not a test path: {bad[0]}")
    return out


def list_drift(ours: dict[str, list[str]], theirs: dict[str, list[str]]) -> list[str]:
    """One line for each list where the command line (``ours``, the tip)
    and the release commit (``theirs``) name different files."""
    lines: list[str] = []
    for flag in ("--never-skip", "--must-run"):
        tip_only = sorted(set(ours.get(flag, [])) - set(theirs.get(flag, [])))
        release_only = sorted(set(theirs.get(flag, [])) - set(ours.get(flag, [])))
        if tip_only:
            lines.append(f"the tip's {flag} list names files that the release commit's does not: {' '.join(tip_only)}")
        if release_only:
            lines.append(f"the release commit's {flag} list names files that the tip's does not: {' '.join(release_only)}")
    return lines


# ── r8-covered (CIV-4c) ──────────────────────────────────────────────────
# deploy.yml's `test` job has no database, so each R8 suite skips there. The
# release gate runs only the files that `affected_tests.py --r8` lists. A file
# that skips for an R8 reason and is not in that list runs nowhere.
R8_VARS = ("CUSTOMER_CONSOLE_DATABASE_URL", "TENANT_LADDER_DATABASE_URL")
_R8_WORD = re.compile(r"(?<![A-Za-z0-9_])R8(?![A-Za-z0-9_])")
_REAL_POSTGRES = re.compile(r"real postgres", re.IGNORECASE)


def is_r8_reason(reason: str) -> bool:
    """True when a skip reason says that an R8 database was not set.

    The reason starts with an R8 variable, or it names "R8" and also names a
    variable or "real Postgres". A Redis skip that names a variable later,
    with no "R8", is not an R8 reason. The collection text carries a path and
    a "Skipped:" prefix, which this drops first."""
    text = reason.split("Skipped: ", 1)[1] if "Skipped: " in reason else reason
    text = text.strip()
    if text.startswith(R8_VARS):
        return True
    if not _R8_WORD.search(text):
        return False
    return any(v in text for v in R8_VARS) or bool(_REAL_POSTGRES.search(text))


def _skip_paths(case: ET.Element) -> list[str]:
    """The files that own a skipped case: where the test is DEFINED (``file``)
    and the module that COLLECTED it (read from ``classname``). They differ when
    a module subclasses a gated class of another module. Both must be in the
    --r8 list, or the collecting module runs nowhere (CIV-4c verifier)."""
    paths = [_file(case)]
    parts = case.get("classname", "").split(".")
    while parts and not parts[-1].startswith("test"):
        parts.pop()
    if parts:
        collected = "/".join(parts) + ".py"
        if collected not in paths:
            paths.append(collected)
    return paths


def r8_covered(junit_dir: Path, r8_list: Path) -> list[str]:
    """Each file that skipped for an R8 reason and is not in ``r8_list``.

    Fails closed: no report, a report with no test, and a missing or empty
    list each give a problem. An empty list means the selector broke. A
    report with no test means no run. The check never passes on nothing."""
    try:
        listed = {
            line.strip().replace("\\", "/")
            for line in r8_list.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }
    except OSError as exc:
        return [f"cannot read the --r8 list {r8_list}: {exc}"]
    if not listed:
        return [f"the --r8 list {r8_list} is empty. affected_tests.py --r8 broke."]
    reports = sorted(junit_dir.rglob("*.xml")) if junit_dir.is_dir() else []
    if not reports:
        return [f"no JUnit report under {junit_dir}. The unit run wrote nothing."]
    try:
        cases = [c for r in reports for c in ET.parse(r).getroot().iter("testcase")]
    except ET.ParseError as exc:
        return [f"a JUnit report under {junit_dir} does not parse: {exc}"]
    if not cases:
        return [f"the JUnit reports under {junit_dir} hold no test. No unit run happened."]
    skipped: dict[str, str] = {}
    for case in cases:
        reason = _skip_reason(case)
        if reason is not None and is_r8_reason(reason):
            for path in _skip_paths(case):
                skipped.setdefault(path, f"{case.get('name')}: {reason}")
    missed = sorted(p for p in skipped if p not in listed)
    if not missed:
        print(f"R8 skips checked: {len(skipped)} files, all in the --r8 list")
    return [
        f"{p} skipped for an R8 reason and is not in the --r8 list, so the release "
        f"gate never runs it. Name an R8 mark in the file. {skipped[p]}"
        for p in missed
    ]


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


# CIV-17. How many unit shards a selection needs. CIV-17b (2026-11-07) counts
# measured SECONDS, not files: a Python PR selected 400 to 500 files, so a file
# count gave 6 shards for every one of them, and the tiers never applied. The
# seconds come from `.test_durations`, the file that pytest-split uses to cut
# the shards. A file with no timing counts DEFAULT_FILE_S. Without the timing
# file, the old tiers by file count apply, so `changes` and the shards still
# agree on the count.
ALL_SHARDS = 6
SHARD_TIERS = ((40, 1), (120, 3))
SHARD_TARGET_S = 600
DEFAULT_FILE_S = 10.0
DURATIONS_PATH = Path(".test_durations")


def file_seconds(path: Path | None = None) -> dict[str, float] | None:
    """Seconds of each test file, summed over its tests. None when unreadable."""
    path = path or DURATIONS_PATH
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        out: dict[str, float] = {}
        for node, secs in data.items():
            out[node.split("::", 1)[0]] = out.get(node.split("::", 1)[0], 0.0) + float(secs)
        return out or None
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def shard_count(mode: str, paths: list[str], seconds: dict[str, float] | None = None) -> int:
    """The shards that ``mode`` (from ``select_shard``) needs: 0 for an empty
    selection, ALL_SHARDS for a full run. A selection takes one shard for each
    SHARD_TARGET_S of measured time, at most ALL_SHARDS."""
    if mode == "empty":
        return 0
    if mode != "selection":
        return ALL_SHARDS
    if seconds:
        total = sum(seconds.get(p, DEFAULT_FILE_S) for p in paths)
        return max(1, min(ALL_SHARDS, math.ceil(total / SHARD_TARGET_S)))
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
    return [*lines, f"shards={shard_list(shard_count(mode, paths, file_seconds()))}"]


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
    c.add_argument(
        "--lists-from",
        type=Path,
        default=None,
        help="a workflow file: take --disarmed, --never-skip and --must-run from its "
        "step --lists-step. The lists on this command line are then only compared (CIV-30)",
    )
    c.add_argument("--lists-step", default="", help="the name of that step")
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
    r = sub.add_parser("r8-covered", help="every R8 skip of a no-database run is in the --r8 list")
    r.add_argument("--junit", type=Path, required=True)
    r.add_argument("--list", type=Path, required=True, help="the output of affected_tests.py --r8")
    args = parser.parse_args(argv)

    if args.cmd == "r8-covered":
        problems = r8_covered(args.junit, args.list)
        for p in problems:
            print(f"::error::{p}")
        return 1 if problems else 0
    if args.cmd == "gate-output":
        print(chr(10).join(gate_output(args.json, args.ignored)))
        return 0
    if args.cmd == "select":
        mode, paths = select_shard(args.full, args.files, args.ignored, args.changes_result)
        print(f"mode={mode}")
        print(f"paths={' '.join(paths)}")
        print(f"splits={shard_count(mode, paths, file_seconds()) or ALL_SHARDS}")
        return 0
    if args.cmd == "empty-report":
        print("wrote an empty report" if empty_report(args.out) else "report exists")
        return 0

    if args.cmd == "durations":
        data = durations(args.junit)
        args.out.write_text(json.dumps(data, indent=0) + "\n", encoding="utf-8")
        print(f"wrote {len(data)} timings to {args.out}")
        return 0

    must_run, never_skip, disarmed = args.must_run, args.never_skip, args.disarmed or DISARMED
    if args.lists_from is not None:
        # CIV-30: the lists of the release commit. Fail closed: a list that
        # cannot be read never becomes an empty list or the tip's list.
        try:
            if not args.lists_step:
                raise GuardListError("--lists-from needs --lists-step")
            lists = guard_lists(args.lists_from.read_text(encoding="utf-8"), args.lists_step)
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            print(
                f"::error title=R8 guard lists unknown::cannot read the guard lists of "
                f"{args.lists_from}: {exc}. The guard fails closed."
            )
            return 1
        for line in list_drift({"--never-skip": never_skip, "--must-run": must_run}, lists):
            print(f"::notice title=R8 guard lists of the release commit::{line}. The release commit's lists apply.")
        must_run, never_skip = lists["--must-run"], lists["--never-skip"]
        disarmed = lists["--disarmed"] or DISARMED
        print(f"guard lists from {args.lists_from}: {len(must_run)} must-run, {len(never_skip)} never-skip")
    problems = check(
        args.junit,
        must_run,
        never_skip,
        disarmed,
        args.only_selected,
        args.tree_root,
    )
    for p in problems:
        print(f"::error::{p}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
