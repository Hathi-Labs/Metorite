"""Replay the CRM answer check offline and print its false-note rate.

WS-53 CRM-Q29 round 8 (``project-docs/specs/crm_platform.md`` §18.11.2).
The owner turns ``CRM_ANSWER_CHECK`` from ``shadow`` to ``on`` only when
fewer than 1 in 20 true answers would show a note. This script measures it.

Two inputs, both read-only, both offline:

``--journal FILE``
    The ``answer_check.checked`` lines of the gateway journal, as
    ``journalctl -u acb-gateway --since "7 days ago" -o cat`` prints them.
    A line holds counts only, never a figure or a text. The script takes
    the LAST check of each run (the answer that the member read). A line
    of round 8 names the kind of its note (``note=``), and the script
    counts it. An older line has no kind, so the script applies the rule
    of round 8 to its counts. That is an upper bound, because the check
    of round 8 also passes more figures.

``--cases FILE``
    JSON lines, one answer each: ``{"answer": ..., "member_message": ...,
    "tools": [{"name": ..., "args": ..., "result": ...}]}``. The script
    runs each answer through the check and the note of THIS checkout. Make
    the file from the ``chat_message`` rows of the runs with
    :data:`EXPORT_SQL`. It reads another org's chat, so an operator runs
    it, never an agent, and the output stays off the repo.

Each answer of the input is taken as true. So the rate is the share of true
answers that would show a note: ``named`` (a warning that names figures),
``how`` (how the answer read the CRM), ``question`` or ``explained``.

Usage::

    uv run python scripts/crm_answer_check_replay.py --journal journal.txt
    uv run python scripts/crm_answer_check_replay.py --cases cases.jsonl
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any

#: The bar of the owner: fewer than 1 in 20 true answers show a note.
BAR = 0.05

#: The export of the cases. Bind the org of each session first
#: (``set_config('app.tenant_id', ...)``) or run it as the owner role in a
#: read-only transaction. ``:sessions`` is the list of thread ids of the
#: journal.
EXPORT_SQL = """\
SELECT m.session_id, m.role, m.content, m.timestamp_ms, m.tool_events
FROM chat_message m
WHERE m.session_id = ANY(:sessions)
ORDER BY m.session_id, m.timestamp_ms;
"""

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_FIELD = re.compile(r"(\w+)=(\S+)")
#: The note of round 7, which a stored answer may still hold.
_OLD_NOTE = "\n\nI could not check these figures against the CRM data:"


def parse_journal(lines: Iterable[str]) -> dict[str, dict[str, str]]:
    """The last ``answer_check.checked`` line of each run, as its fields."""
    last: dict[str, dict[str, str]] = {}
    for raw in lines:
        line = _ANSI.sub("", raw)
        if "answer_check.checked" not in line:
            continue
        fields = dict(_FIELD.findall(line))
        run = fields.get("run_id")
        if run:
            last[run] = fields
    return last


def kind_from_counts(numbers: int, mismatches: int) -> str:
    """The kind of note that round 8 gives to these counts, when nothing
    passes that failed before: ``none``, ``named`` or ``how``."""
    from acb_skills.answer_check import NOTE_HOW, NOTE_NAMED, NOTE_NONE, many_failed

    if mismatches == 0:
        return NOTE_NONE
    return NOTE_HOW if many_failed(mismatches, numbers) else NOTE_NAMED


def journal_kinds(runs: dict[str, dict[str, str]]) -> Counter[str]:
    kinds: Counter[str] = Counter()
    for fields in runs.values():
        if "note" in fields:
            kinds[fields["note"]] += 1
        else:
            kinds[kind_from_counts(int(fields.get("numbers", 0)),
                                   int(fields.get("mismatches", 0)))] += 1
    return kinds


def case_kind(case: dict[str, Any]) -> str:
    """Run one stored answer through the check and the note of round 8."""
    from acb_common.number_check import check
    from acb_skills.answer_check import NOTE_NONE, RunLedger, display_text, note_text

    ledger = RunLedger()
    shown: list[str] = []
    for tool in case.get("tools") or []:
        name = str(tool.get("name") or "")
        ledger.record(name, str(tool.get("result") or ""), arguments=tool.get("args"))
        part = display_text(name, tool.get("args"))
        if part:
            shown.append(part)
    if not ledger.armed:
        return "not_armed"
    answer = str(case.get("answer") or "").split(_OLD_NOTE, 1)[0]
    # The member's words are a source of this check only (item 4), as
    # ``RunLedger.sources`` adds them in a run.
    book = ledger.book.layer()
    book.add_text(str(case.get("member_message") or ""))
    result = check("\n".join(p for p in (answer, *shown) if p), book)
    return note_text(result, ledger)[0] if not result.ok else NOTE_NONE


def cases_from_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """The cases of :data:`EXPORT_SQL` rows: each assistant row, with the
    user row before it in its session."""
    cases: list[dict[str, Any]] = []
    words: dict[str, str] = {}
    for row in rows:
        session = str(row.get("session_id") or "")
        if row.get("role") == "user":
            words[session] = str(row.get("content") or "")
        elif row.get("role") == "assistant":
            cases.append({"answer": row.get("content") or "",
                          "member_message": words.get(session, ""),
                          "tools": row.get("tool_events") or []})
    return cases


def report(kinds: Counter[str], label: str) -> float:
    """Print the counts and the rate of a note, and return the rate."""
    from acb_skills.answer_check import NOTE_NAMED, NOTE_NONE

    answers = sum(n for kind, n in kinds.items() if kind != "not_armed")
    noted = sum(n for kind, n in kinds.items() if kind not in (NOTE_NONE, "not_armed"))
    warned = kinds.get(NOTE_NAMED, 0)
    rate = noted / answers if answers else 0.0
    print(f"{label}: {answers} checked answers")
    for kind, n in sorted(kinds.items()):
        print(f"  {kind}: {n}")
    if answers:
        print(f"  any note: {noted} of {answers} ({rate:.1%})")
        print(f"  a note that names figures: {warned} of {answers} ({warned / answers:.1%})")
        print(f"  bar: under {BAR:.0%} -> {'MET' if rate < BAR else 'NOT MET'}")
    return rate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--journal", type=Path, help="journal lines of the gateway")
    parser.add_argument("--cases", type=Path, help="JSON lines of stored answers")
    parser.add_argument("--rows", type=Path, help="JSON lines of EXPORT_SQL rows")
    parser.add_argument("--print-sql", action="store_true", help="print EXPORT_SQL")
    args = parser.parse_args(argv)
    if args.print_sql:
        print(EXPORT_SQL)
        return 0
    if not (args.journal or args.cases or args.rows):
        parser.error("give --journal, --cases or --rows")
    if args.journal:
        runs = parse_journal(args.journal.read_text(encoding="utf-8").splitlines())
        report(journal_kinds(runs), f"journal ({len(runs)} runs)")
    cases: list[dict[str, Any]] = []
    if args.cases:
        cases += [json.loads(line) for line in
                  args.cases.read_text(encoding="utf-8").splitlines() if line.strip()]
    if args.rows:
        cases += cases_from_rows(json.loads(line) for line in
                                 args.rows.read_text(encoding="utf-8").splitlines()
                                 if line.strip())
    if cases:
        report(Counter(case_kind(case) for case in cases), f"cases ({len(cases)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
