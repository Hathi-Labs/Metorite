"""Unit tests for the /v1 context-window guard (``_fit_context_window``).

Single-agent chat audit C1/CX1-CX3: /v1 is the choke point every agent runtime
POSTs through — including Copilot-SDK sessions whose backend compaction is
disabled for BYOK models, so their session-accumulated history arrives here
unbounded. An over-long prompt must degrade gracefully (shrunk output
reservation, then oldest-turn eviction) instead of reaching the provider as a
hard 4xx mid-conversation.

The window/counter are monkeypatched on ``acb_llm.context`` (the guard imports
them at call time) so the tests control the math without a live tokenizer.
"""
from __future__ import annotations

from typing import Any

import acb_llm.context as ctx
import pytest
from gateway.routes.v1_compat import (
    _CONTEXT_FIT_MIN_OUTPUT,
    _CONTEXT_FIT_SAFETY_TOKENS,
    _fit_context_window,
)

WINDOW = 8000


def _count(msgs: list[dict[str, Any]], _model: str = "") -> int:
    # Deterministic stand-in for count_message_tokens: chars/4 + 8/msg.
    return sum(len(str(m.get("content") or "")) // 4 + 8 for m in msgs)


@pytest.fixture(autouse=True)
def _small_window(monkeypatch):
    monkeypatch.setattr(ctx, "context_window_for", lambda _m: WINDOW)
    monkeypatch.setattr(ctx, "count_message_tokens", _count)


def _msg(role: str, content: str, **extra: Any) -> dict[str, Any]:
    return {"role": role, "content": content, **extra}


def test_fitting_request_is_untouched():
    msgs = [_msg("system", "s" * 400), _msg("user", "u" * 400)]
    out, max_tokens = _fit_context_window(msgs, None, "m", 1000)
    assert out is msgs
    assert max_tokens == 1000


def test_output_reservation_shrinks_before_prompt_is_touched():
    # prompt ≈ 4008 tokens; 32000 output cannot fit an 8000 window.
    msgs = [_msg("user", "u" * 16000)]
    out, max_tokens = _fit_context_window(msgs, None, "m", 32000)
    assert out is msgs, "messages must not be trimmed when shrinking output fits"
    room = WINDOW - _count(msgs) - _CONTEXT_FIT_SAFETY_TOKENS
    assert max_tokens == room
    assert max_tokens >= _CONTEXT_FIT_MIN_OUTPUT


def test_oldest_turns_evicted_system_and_current_kept():
    msgs = (
        [_msg("system", "s" * 400)]
        + [_msg("user" if i % 2 == 0 else "assistant", f"turn-{i} " + "x" * 4000)
           for i in range(10)]
        + [_msg("user", "the current question")]
    )
    out, max_tokens = _fit_context_window(msgs, None, "m", 32000)
    assert out[0]["role"] == "system"
    assert out[-1]["content"] == "the current question"
    assert len(out) < len(msgs)
    budget = WINDOW - _CONTEXT_FIT_MIN_OUTPUT - _CONTEXT_FIT_SAFETY_TOKENS
    assert _count(out) <= budget
    assert max_tokens >= _CONTEXT_FIT_MIN_OUTPUT
    # Newest history survives, oldest goes.
    assert any("turn-9" in str(m.get("content")) for m in out)
    assert not any("turn-0" in str(m.get("content")) for m in out)


def test_orphaned_tool_results_evicted_with_their_turn():
    msgs = [
        _msg("system", "s" * 200),
        _msg("assistant", "a" * 12000, tool_calls=[{"id": "c1"}]),
        _msg("tool", "t" * 12000, tool_call_id="c1"),
        _msg("user", "u" * 8000),
    ]
    out, _max_tokens = _fit_context_window(msgs, None, "m", 32000)
    # The assistant turn was evicted; its tool result must not survive as an
    # orphan (providers reject a tool message with no matching tool_calls).
    assert not any(m.get("role") == "tool" for m in out)
    assert out[0]["role"] == "system"
    assert out[-1]["role"] == "user"


def test_only_system_and_current_left_falls_back_to_char_trim():
    msgs = [_msg("system", "s" * 400), _msg("user", "u" * 60000)]
    out, max_tokens = _fit_context_window(msgs, None, "m", 32000)
    assert len(out) == 2
    assert _count(out) < _count(msgs), "oversized current turn must be trimmed"
    assert max_tokens >= _CONTEXT_FIT_MIN_OUTPUT


def test_tool_schema_tokens_count_against_the_window():
    msgs = [_msg("user", "u" * 16000)]
    tools = [{"type": "function", "function": {"name": "t", "description": "d" * 8000}}]
    _out_no_tools, max_no_tools = _fit_context_window(msgs, None, "m", 32000)
    _out_tools, max_with_tools = _fit_context_window(msgs, tools, "m", 32000)
    assert max_with_tools < max_no_tools


def test_guard_never_breaks_a_call_on_internal_error(monkeypatch):
    def _boom(*_a: Any, **_k: Any) -> int:
        raise RuntimeError("tokenizer unavailable")

    monkeypatch.setattr(ctx, "count_message_tokens", _boom)
    msgs = [_msg("user", "u" * 16000)]
    out, max_tokens = _fit_context_window(msgs, None, "m", 32000)
    assert out is msgs
    assert max_tokens == 32000


# ── The output floor and whole-turn eviction (2026-10-22) ──────────────────


def _orphans(out: list[dict[str, Any]]) -> tuple[set[str], set[str]]:
    """Return (tool results with no call, tool calls with no result)."""
    calls = {
        str(c.get("id"))
        for m in out if m.get("role") == "assistant"
        for c in (m.get("tool_calls") or [])
    }
    results = {str(m.get("tool_call_id")) for m in out if m.get("role") == "tool"}
    return results - calls, calls - results


def test_history_is_evicted_before_output_drops_below_the_floor():
    """A long chat drops old turns before the reply shrinks under the floor.

    Mutation: return ``min(max_tokens, room)`` whenever ``room`` is at least
    ``_CONTEXT_FIT_MIN_OUTPUT`` (the old step 1). The reply then gets about
    1500 tokens and every old turn stays, so this test fails.
    """
    # 8000 window, floor = 2000. Prompt ~= 5900 tokens, room ~= 1500.
    msgs = (
        [_msg("system", "s" * 400)]
        + [_msg("user" if i % 2 == 0 else "assistant", f"turn-{i} " + "x" * 4000)
           for i in range(5)]
        + [_msg("user", "the current question " + "q" * 2000)]
    )
    room = WINDOW - _count(msgs) - _CONTEXT_FIT_SAFETY_TOKENS
    assert _CONTEXT_FIT_MIN_OUTPUT <= room < WINDOW // 4
    out, max_tokens = _fit_context_window(msgs, None, "m", 32000)
    assert len(out) < len(msgs)
    assert max_tokens >= WINDOW // 4
    assert out[-1]["content"].startswith("the current question")


def test_large_window_keeps_an_8k_reply(monkeypatch):
    """On a 64k window the floor is ``_CONTEXT_FIT_PREFERRED_OUTPUT``.

    Mutation: set the floor to ``_CONTEXT_FIT_MIN_OUTPUT``. The guard then
    keeps every turn and returns about 3000 tokens, so this test fails.
    """
    from gateway.routes.v1_compat import _CONTEXT_FIT_PREFERRED_OUTPUT

    monkeypatch.setattr(ctx, "context_window_for", lambda _m: 64000)
    msgs = (
        [_msg("system", "s" * 400)]
        + [_msg("user" if i % 2 == 0 else "assistant", f"turn-{i} " + "x" * 20000)
           for i in range(12)]
        + [_msg("user", "now")]
    )
    room = 64000 - _count(msgs) - _CONTEXT_FIT_SAFETY_TOKENS
    assert _CONTEXT_FIT_MIN_OUTPUT <= room < _CONTEXT_FIT_PREFERRED_OUTPUT
    out, max_tokens = _fit_context_window(msgs, None, "m", 32000)
    assert max_tokens >= _CONTEXT_FIT_PREFERRED_OUTPUT
    assert out[-1]["content"] == "now"
    assert any("turn-11" in str(m.get("content")) for m in out)


def test_small_request_is_never_raised():
    """The floor never grows ``max_tokens`` past what the caller asked for.

    Mutation: drop ``min(requested, ...)`` in ``_output_floor``.
    """
    msgs = [_msg("user", "u" * 29000)]
    _out, max_tokens = _fit_context_window(msgs, None, "m", 900)
    assert max_tokens <= 900


def test_tool_loop_keeps_the_current_user_turn():
    """Inside a tool loop the last message is a tool result, not the request.

    Mutation: protect only ``out[-1]`` (the old step 2). The current user
    turn is then the oldest evictable message and goes first.
    """
    msgs = [
        _msg("system", "s" * 200),
        _msg("user", "the current request"),
        _msg("assistant", "", tool_calls=[{"id": "c1"}]),
        _msg("tool", "r" * 12000, tool_call_id="c1"),
        _msg("assistant", "", tool_calls=[{"id": "c2"}]),
        _msg("tool", "r" * 12000, tool_call_id="c2"),
        _msg("assistant", "", tool_calls=[{"id": "c3"}]),
        _msg("tool", "small", tool_call_id="c3"),
    ]
    out, _max_tokens = _fit_context_window(msgs, None, "m", 32000)
    assert any(m.get("content") == "the current request" for m in out)
    assert out[0]["role"] == "system"
    assert _orphans(out) == (set(), set())
    # The newest tool round survives, the oldest goes.
    assert any(m.get("tool_call_id") == "c3" for m in out)
    assert not any(m.get("tool_call_id") == "c1" for m in out)


def test_a_tool_turn_that_ends_the_list_goes_whole():
    """An evicted tool-call turn takes its tool results, even the last message.

    Mutation: stop the tool-result loop one short of the end (the old
    ``idx < len(out) - 1``). The last tool result then stays as an orphan.
    """
    msgs = [
        _msg("system", "s" * 200),
        _msg("user", "the current request"),
        _msg("assistant", "a" * 14000, tool_calls=[{"id": "c1"}, {"id": "c2"}]),
        _msg("tool", "t" * 14000, tool_call_id="c1"),
        _msg("tool", "t" * 14000, tool_call_id="c2"),
    ]
    out, _max_tokens = _fit_context_window(msgs, None, "m", 32000)
    assert _orphans(out) == (set(), set())
    assert any(m.get("content") == "the current request" for m in out)
