#!/usr/bin/env python3
"""CIV-22 (task 2288): the verdict of a local CI run on the home box.

run_suite.sh calls this file. It reads ``box_known_env.txt``, the list of unit
tests that fail on the box for a reason of the box.

    box_verdict.py shard <known file> <junit xml>
        exit 0 and print the failed ids when the shard has at least one failed
        test and EVERY failed test is in the list WITH its failure text. Exit 1 and print the ids
        that are not in the list when there are any, or when the report holds
        no failed test (the shard failed for another reason: a crash).
    box_verdict.py line GREEN|RED <sha> <mode> <expected> [<unexpected jobs>...]
        print the one verdict line.

Standard library only. Fence: tests/unit/test_box_verdict.py.
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from junit_failed_ids import node_id


def load_known(path: Path) -> dict[str, tuple[str, str, str]]:
    """test id -> (reason, task, text). A line that is not
    <id> TAB <reason> TAB <task> TAB <text> is an error. <text> is a piece of the
    failure message of the box fault: the same test with another message is a
    real failure."""
    known: dict[str, tuple[str, str, str]] = {}
    for n, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        parts = raw.split("\t")
        if len(parts) != 4 or not all(p.strip() for p in parts) or not parts[2].strip().isdigit():
            raise ValueError(
                f"{path}:{n}: want <test id> TAB <reason> TAB <task number> TAB <text>"
            )
        known[parts[0].strip()] = (parts[1].strip(), parts[2].strip(), parts[3].strip())
    return known


def failures(path: Path) -> dict[str, str]:
    """Failed test id -> the text of its failure or error (message and body)."""
    out: dict[str, str] = {}
    for case in ET.parse(path).getroot().iter("testcase"):
        bad = [c for c in case if c.tag in ("failure", "error")]
        if bad:
            tid = node_id(case.get("classname", ""), case.get("name", ""), case.get("file", ""))
            out[tid] = "\n".join(f"{c.get('message') or ''}\n{c.text or ''}" for c in bad)
    return out


def classify(
    failed: dict[str, str], known: dict[str, tuple[str, str, str]]
) -> tuple[set[str], set[str]]:
    """(expected, unexpected). A failed test is expected only when its id is listed
    AND its failure text holds the listed text."""
    expected = {t for t, msg in failed.items() if t in known and known[t][2] in msg}
    return expected, set(failed) - expected


def verdict_line(verdict: str, sha: str, mode: str, expected: int, bad: list[str]) -> str:
    tail = f" unexpected={','.join(bad)}" if bad else ""
    return f"{verdict} {sha} {mode} expected={expected}{tail}"


def main(argv: list[str]) -> int:
    if len(argv) >= 3 and argv[0] == "shard":
        try:
            known = load_known(Path(argv[1]))
            failed = failures(Path(argv[2]))
        except (OSError, ValueError, ET.ParseError) as exc:
            print(f"box_verdict: {exc}", file=sys.stderr)
            return 1
        expected, unexpected = classify(failed, known)
        if unexpected or not expected:
            print("\n".join(sorted(unexpected)) or "no failed test in the report")
            return 1
        print("\n".join(sorted(expected)))
        return 0
    if len(argv) >= 5 and argv[0] == "line" and argv[1] in ("GREEN", "RED"):
        print(verdict_line(argv[1], argv[2], argv[3], int(argv[4]), argv[5:]))
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
