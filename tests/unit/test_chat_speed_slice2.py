"""Chat speed slice 2: no tier question that cannot change the tier, and the
timing lines that measure the wait (chat speed review, 2026-10-25).

Production measured the System-1 tier question at 1.3 to 3.0 s per run. A
delegated email turn asked it twice, and the email assistant's default is
``tier-powerful``, so its own answer could change nothing.

Mutations this file catches (R7):

* ``kind_can_move`` reads the top rung, or a model off the ladder, as movable:
  the email assistant asks again.
* ``turn_kind`` skips the question while the member's words still need the
  effort item: a member's "think hard" stops raising the effort.
* ``choose`` starts to move a top-rung default: the skip is no longer safe.
* ``_RunTiming`` keeps the last mark of a type, not the first, or reads text.
"""
from __future__ import annotations

import asyncio
import itertools
from types import SimpleNamespace
from typing import Any

import pytest
from acb_skills import tier_policy as tp

LONG = "please look through my inbox and tell me which emails need a reply today"


def test_only_a_rung_below_the_top_can_move() -> None:
    assert tp.kind_can_move(tp.TIER_FAST)
    assert tp.kind_can_move(tp.TIER_BALANCED)
    assert not tp.kind_can_move(tp.TIER_POWERFUL)
    assert not tp.kind_can_move("openai/gpt-4o"), "an admin's model never moves"


@pytest.mark.parametrize("default", [tp.TIER_POWERFUL, "openai/gpt-4o"])
def test_the_skip_is_safe_because_nothing_moves_such_a_default(default: str) -> None:
    """Every kind, effort and turn size gives the same tier, so the answer of
    the question could never change the run."""
    tiers = {
        tp.choose(default=default, kind=kind, effort=effort, context_tokens=size).tier
        for kind, effort, size in itertools.product(
            tp.TURN_KINDS, ("auto", "thinking", "max"),
            (0, tp.CONTEXT_POWERFUL_TOKENS))
    }
    assert tiers == {default}


def _no_question(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    from acb_skills import system_one

    asked: list[Any] = []

    async def ask(*a: Any, **k: Any) -> Any:
        asked.append(a)
        return [system_one.Answer(id="turn", choice="plan", confidence=0.99,
                                  reason="")]

    monkeypatch.setattr(system_one, "ask", ask)
    return asked


def test_a_top_rung_default_asks_no_question(monkeypatch: pytest.MonkeyPatch) -> None:
    asked = _no_question(monkeypatch)
    turn = asyncio.run(tp.turn_kind(LONG, ["read_email"], "auto", default=tp.TIER_POWERFUL))
    assert (turn.kind, turn.source, asked) == ("chat", "pinned", [])


def test_the_member_turn_still_asks_for_the_effort(monkeypatch: pytest.MonkeyPatch) -> None:
    """The member's words can raise ``auto`` to ``thinking``, and that sets
    the reasoning effort even on the top rung. So the question goes out."""
    asked = _no_question(monkeypatch)
    turn = asyncio.run(tp.turn_kind(
        LONG, ["read_email"], "auto", read_effort=True, default=tp.TIER_POWERFUL))
    assert turn.source != "pinned"
    assert len(asked) == 1


def test_a_balanced_default_still_asks(monkeypatch: pytest.MonkeyPatch) -> None:
    asked = _no_question(monkeypatch)
    asyncio.run(tp.turn_kind(LONG, ["read_email"], "auto", default=tp.TIER_BALANCED))
    assert len(asked) == 1


def test_no_default_keeps_the_old_behaviour(monkeypatch: pytest.MonkeyPatch) -> None:
    asked = _no_question(monkeypatch)
    asyncio.run(tp.turn_kind(LONG, ["read_email"], "auto"))
    assert len(asked) == 1


async def test_the_executor_passes_the_agent_default(monkeypatch: pytest.MonkeyPatch) -> None:
    from orchestrator import executor as ex

    seen: dict[str, Any] = {}

    async def kind(message: str, tools: Any, effort: Any, **kw: Any) -> Any:
        seen.update(kw)
        return tp.TurnKind("chat", "pinned")

    monkeypatch.setattr(ex, "_tier_policy_covers", lambda name: True)
    monkeypatch.setattr(tp, "turn_kind", kind)
    agent = SimpleNamespace(client=SimpleNamespace(model=tp.TIER_POWERFUL),
                            default_options={"tools": []})
    run = await ex._tier_policy_for_run(
        "email-assistant", agent, run_id="r", thread_id=None, model=None,
        think_mode="auto", event_payload={"message": LONG}, config={},
        agent_md_model="", is_copilot=False, emit=False,
    )
    assert seen["default"] == tp.TIER_POWERFUL
    assert run.run_tier() == tp.TIER_POWERFUL


def test_the_copilot_test_is_one_rule() -> None:
    from orchestrator import executor as ex

    assert ex._is_copilot_built(SimpleNamespace(_default_options={}), "maf")
    assert not ex._is_copilot_built(SimpleNamespace(_default_options=None), "maf")
    assert not ex._is_copilot_built(SimpleNamespace(), "maf")
    assert ex._is_copilot_built(SimpleNamespace(), "github-copilot")


def test_the_run_timing_keeps_the_first_mark_of_each_type(monkeypatch) -> None:
    from gateway.routes import agent as route

    now = [0.0]
    monkeypatch.setattr(route.time, "monotonic", lambda: now[0])
    timing = route._RunTiming(0.0)
    for at, evt in (
        (0.2, {"type": "RUN_STARTED"}),
        (1.0, {"type": "TOOL_CALL_START", "toolCallName": "email_assistant"}),
        (1.5, {"type": "TOOL_CALL_START", "toolCallName": "second"}),
        (2.0, "not an event"),
        (3.0, {"type": "TEXT_MESSAGE_CONTENT", "delta": "secret words"}),
        (9.0, {"type": "TEXT_MESSAGE_CONTENT", "delta": "more"}),
        (11.0, {"type": "RUN_FINISHED"}),
    ):
        now[0] = at
        timing.see(evt)
    now[0] = 12.0
    fields = timing.fields()
    assert fields == {"started_ms": 200, "first_tool_ms": 1000, "first_text_ms": 3000,
                      "total_ms": 12000, "outcome": "finished"}
    assert "secret" not in repr(fields), "the line holds no text"


def test_a_stream_that_ends_early_says_so(monkeypatch) -> None:
    from gateway.routes import agent as route

    monkeypatch.setattr(route.time, "monotonic", lambda: 2.0)
    timing = route._RunTiming(0.0)
    timing.see({"type": "RUN_STARTED"})
    assert timing.fields()["outcome"] == "disconnected"
    timing.see({"type": "RUN_ERROR"})
    assert timing.fields()["outcome"] == "error"
