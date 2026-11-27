#!/usr/bin/env python3
"""Split the slice rows of project-docs/specs/ai_studio.md section 14 into one file each.

CIV-HS slice 2 (task 3337, docs/merge_hotspots.md). Two PRs that edit two rows of
the one section 14 table collide, because a row is one long line and rows sit side
by side. After the split, section 14 holds one short INDEX line for each row, and the
row text lives in project-docs/specs/ai_studio_rows/<id>.md. Two PRs conflict only
when they edit the same row.

    python scripts/ai_studio_rows.py split     # one-off: rewrite the spec and write the row files
    python scripts/ai_studio_rows.py check     # the fence reads this: exit 1 and name each problem

The index line is `| **ID** | status | tier | gate | [row](ai_studio_rows/FILE.md) |`.
The row file holds the original four cells, so a round trip loses nothing.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "project-docs" / "specs" / "ai_studio.md"
ROWS = ROOT / "project-docs" / "specs" / "ai_studio_rows"
INDEX_HEAD = "| Id | Status | Tier | Gate | Row |"
INDEX_RE = re.compile(
    r"^\| \*\*(?P<id>[^*|]+)\*\* \| (?P<status>[^|]{1,100}) \| (?P<tier>[^|]{1,40}) \| (?P<gate>[^|]{1,60}) \| "
    r"\[row\]\(ai_studio_rows/(?P<file>[A-Za-z0-9._-]+\.md)\) \|$"
)
MAX_INDEX_LINE = 260


def split_cells(line: str) -> list[str]:
    """Split a table line on `|` outside backtick spans."""
    cells, cur, tick = [], [], False
    for ch in line:
        if ch == "`":
            tick = not tick
        if ch == "|" and not tick:
            cells.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    cells.append("".join(cur))
    # Drop the text before the first pipe, and the text after the last pipe when
    # the line ends with one. A row may lack the closing pipe: keep its last cell.
    return cells[1:-1] if line.rstrip().endswith("|") else cells[1:]


def section(lines: list[str]) -> tuple[int, int]:
    a = next(i for i, ln in enumerate(lines) if ln.startswith("## 14."))
    b = next(i for i, ln in enumerate(lines) if ln.startswith("## 15."))
    return a, b


def status_of(what: str) -> str:
    m = re.match(r"\s*(?:🔨\s*)?\*\*([^*]{1,90})\*\*", what)
    if m and "BUILT" in m.group(1).upper():
        return "🔨 " + m.group(1).strip().rstrip(".")
    return "open"


def tier_token(cell: str) -> str:
    m = re.match(r"\s*(LOW|STANDARD|STD|HIGH)\b", cell)
    return m.group(1) if m else cell.strip()[:20]


def gate_token(cell: str) -> str:
    """The short gate for the index line. OWNER-GATE wins over AGENT-SAFE."""
    if "OWNER-GATE" in cell:
        return "OWNER-GATE"
    return "AGENT-SAFE" if "AGENT-SAFE" in cell else "see row"


def split() -> int:
    lines = SPEC.read_text(encoding="utf-8").split("\n")
    a, b = section(lines)
    ROWS.mkdir(exist_ok=True)
    seen: dict[str, int] = {}
    out = lines[:a]
    for ln in lines[a:b]:
        if ln.startswith("| Id | What |"):
            out.append(INDEX_HEAD)
            continue
        if not ln.startswith("| **"):
            out.append(ln)
            continue
        cells = split_cells(ln)
        if len(cells) != 5:
            sys.exit(f"row with {len(cells)} cells: {ln[:80]}")
        rid = cells[0].strip().strip("*")
        seen[rid] = seen.get(rid, 0) + 1
        fname = f"{rid}.md" if seen[rid] == 1 else f"{rid}-dup{seen[rid]}.md"
        what, done, tier, gate = (c.strip() for c in cells[1:])
        (ROWS / fname).write_text(
            f"# {rid}\n\nTier: {tier}\n\nGate: {gate}\n\n## What\n\n{what}\n\n## Done when\n\n{done}\n",
            encoding="utf-8",
        )
        out.append(f"| **{rid}** | {status_of(what)} | {tier_token(tier)} | {gate_token(gate)} | [row](ai_studio_rows/{fname}) |")
    out += lines[b:]
    SPEC.write_text("\n".join(out), encoding="utf-8")
    print(f"{sum(seen.values())} rows split into {ROWS}")
    return 0


def check() -> int:
    """Section 14 holds index lines only: every `| **` line is an index line, short,
    and points at a row file that exists and names the same id."""
    lines = SPEC.read_text(encoding="utf-8").split("\n")
    a, b = section(lines)
    bad: list[str] = []
    files: list[str] = []
    for n, ln in enumerate(lines[a:b], start=a + 1):
        if ln.startswith("| Id | What |"):
            bad.append(f"line {n}: an old table head. A head is `{INDEX_HEAD}`.")
        if not ln.startswith("| **"):
            continue
        m = INDEX_RE.match(ln)
        if not m or len(ln) > MAX_INDEX_LINE:
            bad.append(f"line {n}: not an index line (put the row text in its row file): {ln[:70]}")
            continue
        files.append(m["file"])
        f = ROWS / m["file"]
        if not f.is_file():
            bad.append(f"line {n}: {m['file']} does not exist")
        elif not f.read_text(encoding="utf-8").startswith(f"# {m['id'].strip()}\n"):
            bad.append(f"line {n}: {m['file']} does not start with `# {m['id'].strip()}`")
    for dup in sorted({x for x in files if files.count(x) > 1}):
        bad.append(f"two index lines point at {dup}")
    for f in sorted(ROWS.glob("*.md")):
        if f.name not in files:
            bad.append(f"{f.name} has no index line in section 14")
    print("\n".join(bad))
    return 1 if bad else 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "split":
        sys.exit(split())
    if cmd == "check":
        sys.exit(check())
    sys.exit(__doc__)
