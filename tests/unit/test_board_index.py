"""Fence for scripts/board.py, the short index of work_plan.md section 2."""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "scripts"))

import board  # noqa: E402

_PLAN = """# Plan

## 1. Contract

| WS-99 | not on the board | 🟢 | x | y |

## 2. The dispatch board

| WS | Workstream | State | Owning spec | Gates · next |
|---|---|---|---|---|
| WS-1 | **Broker** (BO-1) | 🟢 | `spec.md` §1 | ~~old note~~ next: slice 2 |
| **WS-2** | Secrets | ✅ DONE 2026-08-01 | `s.md` | nothing |
| WS-3 | Ladder | 🔴 | `l.md` | a | b |

## 3. Decisions
"""


def test_only_section_two_rows_are_read() -> None:
    ids = [r["id"] for r in board.rows(_PLAN)]
    assert ids == ["WS-1", "WS-2", "WS-3"]


def test_a_pipe_in_the_notes_stays_in_the_notes() -> None:
    assert board.rows(_PLAN)[2]["notes"] == "a|b"


def test_open_hides_a_done_row() -> None:
    assert [r["id"] for r in board.rows(_PLAN) if board.is_open(r)] == ["WS-1", "WS-3"]


def test_the_index_line_drops_markdown_and_struck_history() -> None:
    line = board.index_line(board.rows(_PLAN)[0])
    assert line == "WS-1 | 🟢 | Broker (BO-1) | spec.md §1"
    assert "old note" not in board._plain(board.rows(_PLAN)[0]["notes"])


def test_the_real_board_parses_and_is_much_shorter() -> None:
    text = board.PLAN.read_text(encoding="utf-8")
    rows = board.rows(text)
    assert len(rows) > 30
    index = "\n".join(board.index_line(r) for r in rows)
    assert len(index) * 10 < len(board.section_two(text))
