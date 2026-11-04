"""Every development session runs the Metorite task routine (D135).

Spec: root `CLAUDE.md` §4 "WORK UNDER A METORITE TASK" ·
`.claude/commands/next-ticket.md` "The task board" · Metorite task 1552.

Owner, 2026-11-03: "Why aren't you properly using Metorite MCP and updating
the task status and approvals over there?" In one session, claimed runs went
15 to 34 hours with no line, five branches in flight had no reference, and 16
new spec slices had no task. The rule existed in `CLAUDE.md`, but the loop
command never bound it to its steps. These tests fail when an edit drops a
step of the routine from either file, or drops the pointer between them.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CLAUDE = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
NEXT_TICKET = (ROOT / ".claude" / "commands" / "next-ticket.md").read_text(encoding="utf-8")

# The calls that make a session's work visible on the board, from the first
# claim to the last line. A step that loses one goes dark in silence.
ROUTINE = (
    "my_work",
    "claim_task",
    "post_agent_activity",
    "add_task_reference",
    "set_task_status",
    "finish_agent_run",
    "release_task",
)


def _flat(text: str) -> str:
    """One space between words, so a line break never hides a phrase."""
    return " ".join(text.split())


def _claude_routine() -> str:
    start = CLAUDE.index("WORK UNDER A METORITE TASK")
    return _flat(CLAUDE[start:CLAUDE.index("Rules that make it work:", start)])


def _task_board() -> str:
    start = NEXT_TICKET.index("## The task board")
    return _flat(NEXT_TICKET[start:NEXT_TICKET.index("## The cycle", start)])


def test_claude_md_names_every_call_of_the_routine():
    routine = _claude_routine()
    for call in ROUTINE + ("search_tasks", "create_task", "get_agent_run"):
        assert f"`{call}`" in routine, call


def test_claude_md_points_to_the_task_board_and_its_fence():
    routine = _claude_routine()
    assert "The task board" in routine
    assert "tests/unit/test_task_routine.py" in routine


def test_a_wait_gets_a_line_on_every_open_run():
    routine = _claude_routine()
    assert "Before each wait" in routine
    assert "EVERY open run" in routine


def test_each_spec_slice_gets_a_task():
    routine = _claude_routine()
    assert "Give each spec slice a task" in routine
    assert "Write each task number in its row" in routine


def test_the_loop_binds_every_call_to_a_step():
    board = _task_board()
    for call in ROUTINE + ("ask_on_task", "get_agent_run", "create_task"):
        assert f"`{call}`" in board, call


def test_the_loop_sets_done_only_after_the_merge_then_finishes_the_run():
    board = _task_board()
    land = board[board.index("7 Land"):]
    assert "After the merge" in land
    assert land.index("After the merge") < land.index("`finish_agent_run`")


def test_the_loop_reads_the_answer_before_it_reports_awaiting_owner():
    board = _task_board()
    assert "Do not report `AWAITING-OWNER` before that read" in board


def test_an_answer_in_chat_is_recorded_on_the_task():
    """WS-55 MCP-G15 (Metorite task 1567). The owner may answer a question in
    the chat and not on the task. The session records that answer with the
    tool, or the task keeps waiting on the owner. Both files name the tool."""
    assert "`record_outside_decision`" in _claude_routine()
    board = _task_board()
    preview = board[board.index("4b Preview"):board.index("7 Land")]
    assert "`record_outside_decision`" in preview
    assert "answers in chat" in preview


def test_the_final_report_states_the_run():
    report = NEXT_TICKET[NEXT_TICKET.index("## Final report"):]
    assert "run state" in report
