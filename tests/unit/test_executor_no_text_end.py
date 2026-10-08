"""Unit tests — graceful handling of a Copilot run that ends with no text.

Regression for the "The agent produced no text output" dead-end (2026-07-16).

A tool-heavy Copilot-SDK run (e.g. deepseek-v4-pro authoring many files via
bash) can exhaust its output-token budget part-way through the turn — the
final tool call's JSON is truncated ("Unterminated string in JSON ...") and
the model never writes its closing answer. The stream ends with
``text_started == False``.

Previously the executor surfaced a hard ``RUN_ERROR`` and returned, which
(a) recorded the run as ``error`` and (b) wiped all the folded tool work from
the UI even though real work had happened. ``_copilot_no_text_end`` now
distinguishes the two cases:

* tool work happened → synthesised closing message + fall through to
  RUN_FINISHED (records ``completed``, keeps the work).
* no tool work at all → genuine empty response → RUN_ERROR (hard stop).

FOLLOW-UP (2026-07-21) — the missing third case. The handler above assumed
"tools ran + no text" could only mean truncation, and said so in the message
("the response hit its output length limit"). The first time it fired in
production (run 252c24dc, technical-project-planner) the real reason was
different: the agent's last tool was ``ask_user``, i.e. it had asked the user a
question and was waiting for the answer. No closing text was due — the question
card IS the output. The message told the user their answer had been truncated
and to say "continue", when the correct move was to answer the question. The
gateway's own usage log for that run showed a 4,473-token completion, nowhere
near any limit, disproving the asserted cause.

So a turn ending on a blocking HITL tool must finish SILENTLY, and the message
for the genuine case must stop asserting a cause the code cannot observe.
"""
from __future__ import annotations

import orchestrator.executor as ex


def test_tool_activity_soft_finishes_without_run_error() -> None:
    events, finish = ex._copilot_no_text_end(run_id="r1", tool_activity=True)

    # The caller must fall through to its shared RUN_FINISHED.
    assert finish is True

    types = [e["type"] for e in events]
    # A real assistant text segment so the message persists / folds, and the
    # turn does not read as an empty bubble.
    assert types == [
        "TEXT_MESSAGE_START",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END",
    ]
    # Crucially: no RUN_ERROR — that is what previously marked the run failed
    # and discarded the work.
    assert not any(e["type"] == "RUN_ERROR" for e in events)

    # One coherent message id across the segment.
    ids = {e["messageId"] for e in events}
    assert len(ids) == 1
    assert events[1]["delta"].strip(), "fallback message must carry content"


def test_no_tool_activity_is_a_hard_error() -> None:
    events, finish = ex._copilot_no_text_end(run_id="r2", tool_activity=False)

    # No work happened — stop hard, do not reach RUN_FINISHED.
    assert finish is False
    assert len(events) == 1
    err = events[0]
    assert err["type"] == "RUN_ERROR"
    assert err["runId"] == "r2"
    # A code from the run-error vocabulary (acb_llm.run_errors), which the
    # chat maps to member-facing words. It was "NO_OUTPUT" until 2026-10-08.
    assert err["code"] == "model_refused"
    assert err["ref"] == "r2"


def test_message_ids_are_unique_per_call() -> None:
    # Distinct runs must not collide on a reused id (AG-UI dedups by id).
    a, _ = ex._copilot_no_text_end(run_id="r", tool_activity=True)
    b, _ = ex._copilot_no_text_end(run_id="r", tool_activity=True)
    assert a[0]["messageId"] != b[0]["messageId"]


# ---------------------------------------------------------------------------
# The third case: the turn is PARKED on a human, not broken
# ---------------------------------------------------------------------------

def test_ending_on_ask_user_finishes_silently() -> None:
    """The exact production shape of run 252c24dc.

    ``ask_user`` is the Copilot SDK's native elicitation tool: it renders a
    question and requires the agent to stop. Emitting anything here talks over
    the question card AND misreports a normal wait as a truncated answer.
    """
    events, finish = ex._copilot_no_text_end(
        run_id="r3", tool_activity=True, last_tool="ask_user")

    assert finish is True, "must still reach RUN_FINISHED (run is healthy)"
    assert events == [], "nothing to say — the question card is the output"


def test_every_hitl_tool_parks_silently() -> None:
    """All three blocking tools park the turn the same way, so all three must
    be treated the same — ours and the SDK's native one alike."""
    from acb_skills.ask_tools import HITL_BLOCKING_TOOLS

    assert HITL_BLOCKING_TOOLS, "the canonical set must not be empty"
    for name in HITL_BLOCKING_TOOLS:
        events, finish = ex._copilot_no_text_end(
            run_id="r", tool_activity=True, last_tool=name)
        assert events == [], f"{name} must finish silently"
        assert finish is True, f"{name} must not error the run"


def test_hitl_match_is_case_and_space_insensitive() -> None:
    events, _ = ex._copilot_no_text_end(
        run_id="r", tool_activity=True, last_tool="  Ask_User  ")
    assert events == []


def test_a_normal_last_tool_still_gets_the_fallback_message() -> None:
    """The HITL exemption must not swallow the case it was built for: real
    work that genuinely lost its closing summary."""
    events, finish = ex._copilot_no_text_end(
        run_id="r4", tool_activity=True, last_tool="bash")

    assert finish is True
    assert [e["type"] for e in events] == [
        "TEXT_MESSAGE_START", "TEXT_MESSAGE_CONTENT", "TEXT_MESSAGE_END",
    ]


def test_hitl_last_tool_does_not_rescue_a_zero_tool_run() -> None:
    """No tool work at all is still a genuine empty response. A stray tool name
    must not turn a real provider failure into a silent success."""
    events, finish = ex._copilot_no_text_end(
        run_id="r5", tool_activity=False, last_tool="ask_user")
    assert finish is False
    assert events[0]["type"] == "RUN_ERROR"


def test_message_does_not_assert_an_unverifiable_cause() -> None:
    """The bug behind this whole fix: the text stated a specific cause the code
    cannot observe, and was confidently wrong the first time it mattered."""
    events, _ = ex._copilot_no_text_end(
        run_id="r6", tool_activity=True, last_tool="bash")
    msg = events[1]["delta"].lower()

    for claim in ("output length limit", "token limit", "cut off"):
        assert claim not in msg, f"must not assert cause: {claim!r}"
    # It should still tell the user the work survived and how to resume.
    assert "continue" in msg
