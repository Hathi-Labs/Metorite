"""Email cards draw in the one assistant (``navigation_shell.md`` §7.1.1 rule 5).

R7 fence ``one-assistant-email-cards`` (server half). The browser half is
``workbench/control_plane/src/lib/delegatedSteps.test.ts``.

* A batch MAF sub-run streams nothing, so ``executor._sub_call_events`` sends
  each of its calls to the parent as ``SUB_AGENT_TOOL_CALL_START`` (with the
  arguments as JSON text) and ``SUB_AGENT_TOOL_CALL_RESULT`` (the result
  capped at ``RESULT_CAP``, the outcome kept).
* ``chat_fold.fold_run_events``, which saves a turn the browser left, puts
  the steps of a specialist tool (``email_assistant``) on that tool's row,
  each with its arguments, as ``route.ts`` does live. Before this, only
  ``call_agent`` kept them.

Run::

    uv run pytest tests/unit/test_one_assistant_email_cards.py -v
"""
from __future__ import annotations

import json


def test_a_batch_call_becomes_a_start_and_a_result() -> None:
    from orchestrator.event_translator import RESULT_CAP
    from orchestrator.executor import _sub_call_events

    events = _sub_call_events([
        {"id": "c1", "name": "draft_reply", "args": {"email_id": "e-1", "account_id": "a-1"},
         "result": "x" * (RESULT_CAP + 50), "success": True},
        {"id": "c2", "name": "send_draft", "args": {}, "result": "Refused: held", "success": False},
    ], agent_name="email-assistant", run_id="r-1")
    assert [e["type"] for e in events] == [
        "SUB_AGENT_TOOL_CALL_START", "SUB_AGENT_TOOL_CALL_RESULT",
        "SUB_AGENT_TOOL_CALL_START", "SUB_AGENT_TOOL_CALL_RESULT",
    ]
    start, result = events[0], events[1]
    assert start["agentName"] == "email-assistant" and start["toolCallId"] == "c1"
    assert start["toolCallName"] == "draft_reply"
    assert json.loads(start["args"]) == {"email_id": "e-1", "account_id": "a-1"}
    assert len(result["content"]) == RESULT_CAP and result["success"] is True
    assert events[3]["success"] is False


def _fold(tool_name: str) -> dict:
    from gateway.chat_fold import fold_run_events

    return fold_run_events([
        {"type": "TOOL_CALL_START", "toolCallId": "t1", "toolCallName": tool_name,
         "args": '{"task": "reply to Dr Sagar"}'},
        {"type": "SUB_AGENT_TOOL_CALL_START", "agentName": "email-assistant",
         "toolCallId": "s1", "toolCallName": "draft_reply",
         "args": '{"email_id": "e-1", "account_id": "a-1"}'},
        {"type": "SUB_AGENT_TOOL_CALL_RESULT", "agentName": "email-assistant",
         "toolCallId": "s1", "content": "Draft: draft_id=d-9 | subject: Re", "success": True},
        {"type": "SUB_AGENT_TEXT_DELTA", "agentName": "email-assistant", "delta": "Saved."},
        {"type": "TOOL_CALL_RESULT", "toolCallId": "t1", "content": "Saved.", "success": True},
        {"type": "RUN_FINISHED"},
    ]) or {}


def _row(folded: dict) -> dict:
    events = folded.get("tool_events") or folded.get("toolEvents") or []
    (row,) = events
    return row


def test_the_saved_turn_keeps_a_specialist_tools_steps_with_their_args() -> None:
    row = _row(_fold("email_assistant"))
    assert row["subAgentName"] == "email-assistant"
    assert row["subAgentTools"] == [{
        "id": "s1", "name": "draft_reply", "status": "done",
        "args": {"email_id": "e-1", "account_id": "a-1"},
        "result": "Draft: draft_id=d-9 | subject: Re",
    }]


def test_call_agent_keeps_them_too_and_another_tool_does_not() -> None:
    assert _row(_fold("call_agent"))["subAgentTools"][0]["args"]["email_id"] == "e-1"
    assert "subAgentTools" not in _row(_fold("projects_assistant"))
