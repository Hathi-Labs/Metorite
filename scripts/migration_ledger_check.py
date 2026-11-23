#!/usr/bin/env python3
"""Check the migration numbers of a branch against the box ledger (task 2813, R1).

``reserve_migration.sh`` (on the home box) books a number for a lane and writes
one row to ``~/lanes/migration-numbers.tsv``:

    number<TAB>lane<TAB>task<TAB>pr<TAB>booked_utc

``premerge.sh`` calls this check on EVERY merge, not only when the branch is
behind main. It takes the numbers of the migrations that the branch adds. It
fails when:

* a number is at or below main's highest (R1), or
* a number is booked in the ledger for ANOTHER pull request.

The message names the next free number: one above the highest of main and the
ledger. The ledger is the one seam. This script only reads it, never writes it,
and it never books a number. With no ledger file (a PC), only the main check runs.

    migration_ledger_check.py --main-max N [--ledger F] [--pr N] NUMBER...

Exit 0 means every number is fine. Exit 1 prints one problem each.
Stdlib only. Fence: tests/unit/test_migration_reservation.py.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

DEFAULT_LEDGER = Path.home() / "lanes" / "migration-numbers.tsv"


def read_ledger(path: Path) -> list[dict[str, str]]:
    """The rows of the ledger. A missing file, a short row or a header is skipped."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    rows = []
    for line in text.splitlines():
        cols = line.split("\t")
        if len(cols) >= 4 and cols[0].strip().isdigit():
            rows.append({"number": cols[0].strip(), "lane": cols[1], "task": cols[2], "pr": cols[3].strip()})
    return rows


def next_free(main_max: int, rows: list[dict[str, str]]) -> int:
    top = max([main_max] + [int(r["number"]) for r in rows])
    return top + 1


def check(numbers: list[int], main_max: int, rows: list[dict[str, str]], pr: str | None) -> list[str]:
    problems = []
    free = next_free(main_max, rows)
    for n in numbers:
        if n <= main_max:
            problems.append(
                f"migration {n} is not above main's highest number ({main_max}). Book {free} "
                f"(ssh metorite-home '~/lanes/reserve_migration.sh <lane> <task>'), then "
                f"scripts/renumber_migration.sh {n} {free} (R1)."
            )
            continue
        others = [
            r for r in rows
            if int(r["number"]) == n and r["pr"] not in ("", "-") and pr is not None and r["pr"] != pr
        ]
        for r in others:
            problems.append(
                f"migration {n} is booked for pull request {r['pr']} (lane {r['lane']}, task {r['task']}), "
                f"not for this one. Book {free}, then scripts/renumber_migration.sh {n} {free} (R1)."
            )
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("numbers", type=int, nargs="*", help="the migration numbers the branch adds")
    ap.add_argument("--main-max", type=int, required=True, help="the highest number on main")
    ap.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    ap.add_argument("--pr", default=None, help="the pull request number of this branch")
    args = ap.parse_args(argv)
    problems = check(args.numbers, args.main_max, read_ledger(Path(args.ledger)), args.pr)
    for p in problems:
        print(p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
