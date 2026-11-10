"""The cap on a sub-agent answer keeps the start AND the end (2026-10-22).

The orchestrator relays a drafted email or document whole. The old cap was
8000 characters and kept only the head, so a long draft lost its sign-off
and the questions at its end, with no word on how much went.
"""
from __future__ import annotations

import inspect

from orchestrator import executor
from orchestrator.executor import (
    _SUB_AGENT_RESULT_CHARS_DEFAULT,
    _cap_sub_agent_result,
)


def _draft(lines: int) -> str:
    body = "\n".join(f"line {i:05d} " + "x" * 40 for i in range(lines))
    return "Dear Asha,\n" + body + "\nCan you confirm by Friday?\nRegards, Ravi"


def test_short_answer_is_untouched():
    """Mutation: cut every answer. This answer then changes."""
    text = _draft(10)
    assert _cap_sub_agent_result(text, 24000) == text


def test_cut_keeps_head_and_tail_with_a_marker():
    """The end of a long draft survives the cut.

    Mutation: keep only ``text[:cap]`` (the old head-only cut). The sign-off
    and the question at the end then go, and this test fails.
    """
    text = _draft(2000)
    out = _cap_sub_agent_result(text, 6000)
    assert out.startswith("Dear Asha,")
    assert out.endswith("Regards, Ravi")
    assert "Can you confirm by Friday?" in out
    assert "[Sub-agent result cut:" in out
    assert len(out) <= 6000 + 200


def test_marker_names_the_characters_left_out():
    """Mutation: write a marker with no count. This test then fails."""
    text = "a" * 30000
    out = _cap_sub_agent_result(text, 9000)
    head, rest = out.split("\n\n[Sub-agent result cut: ", 1)
    marker, tail = rest.split("]\n\n", 1)
    left_out = int(marker.split(" ", 1)[0])
    assert left_out == len(text) - len(head) - len(tail)
    assert f"of {len(text)} characters" in marker


def test_default_holds_a_whole_draft():
    """Mutation: put the default back to 8000. This test then fails."""
    assert _SUB_AGENT_RESULT_CHARS_DEFAULT >= 24000
    src = inspect.getsource(executor._run_sub_agent_streaming)
    assert "str(_SUB_AGENT_RESULT_CHARS_DEFAULT)" in src
    assert "_cap_sub_agent_result(raw_result, _MAX_SUB_RESULT_CHARS)" in src
