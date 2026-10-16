"""Print the dispatch board (work_plan.md section 2) as a short index.

    uv run python scripts/board.py                 # one line per workstream
    uv run python scripts/board.py --open          # only rows not done
    uv run python scripts/board.py --row WS-39     # one row, notes capped

Section 2 is about 470 KB, near 117,000 tokens, because each row's notes cell
keeps its whole history (one cell is 83 KB). `/next-ticket` read all of it on
every cycle only to pick one row. This prints the same rows in about 6,000
tokens, and `--row` prints one row with its notes capped.

The board stays the only current-state authority. This reads it and writes
nothing, so it cannot drift from it.

Fence: tests/unit/test_board_index.py.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "project-docs" / "work_plan.md"
_ROW = re.compile(r"^\|\s*\**(WS-[\w.-]+)\**\s*\|")
_DONE = ("✅", "DONE", "Done", "done", "RETIRED", "retired", "WITHDRAWN")


def _plain(cell: str) -> str:
    cell = re.sub(r"~~.*?~~", "", cell)  # struck-out history
    cell = re.sub(r"[*`]", "", cell)
    return re.sub(r"\s+", " ", cell).strip()


def _cut(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def section_two(text: str) -> str:
    start = text.index("\n## 2.")
    end = text.find("\n## 3.", start)
    return text[start : end if end != -1 else len(text)]


def rows(text: str) -> list[dict[str, str]]:
    out = []
    for line in section_two(text).splitlines():
        m = _ROW.match(line)
        if not m:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        cells += [""] * (5 - len(cells))
        out.append(
            {
                "id": m.group(1),
                "title": cells[1],
                "state": cells[2],
                "spec": cells[3],
                "notes": "|".join(cells[4:]),
            }
        )
    return out


def is_open(row: dict[str, str]) -> bool:
    state = _plain(row["state"])
    return not any(state.startswith(d) for d in _DONE)


def index_line(row: dict[str, str]) -> str:
    return " | ".join(
        [
            row["id"],
            _cut(_plain(row["state"]), 70),
            _cut(_plain(row["title"]), 70),
            _cut(_plain(row["spec"]), 60),
        ]
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--open", action="store_true", help="hide rows whose state reads done")
    ap.add_argument("--row", help="print one row in full, notes capped")
    ap.add_argument("--chars", type=int, default=4000, help="cap for the notes cell")
    args = ap.parse_args(argv)
    # The board is full of emoji, and a Windows console defaults to cp1252.
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

    all_rows = rows(PLAN.read_text(encoding="utf-8"))
    if args.row:
        hit = [r for r in all_rows if r["id"].lower() == args.row.lower()]
        if not hit:
            print(f"{args.row}: no such row in work_plan.md section 2", file=sys.stderr)
            return 1
        r = hit[0]
        notes = _plain(r["notes"])
        print(f"{r['id']} · {_plain(r['title'])}\nstate: {_plain(r['state'])}")
        print(f"spec: {_plain(r['spec'])}\nnotes ({len(notes)} chars):\n{_cut(notes, args.chars)}")
        if len(notes) > args.chars:
            print(f"[cut at {args.chars}. Read the row in work_plan.md for the rest.]")
        return 0

    shown = [r for r in all_rows if is_open(r)] if args.open else all_rows
    print("WS | state | workstream | owning spec")
    for r in shown:
        print(index_line(r))
    print(f"[{len(shown)} of {len(all_rows)} rows. --row WS-n for one row.]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
