"""The day-plan AI call is a fast, structured choice with no reasoning.

On 2026-10-30 every "Rebuild my day" call on production failed. The member's
chat model was tier-powerful (deepseek-v4-pro), which thinks before it answers.
Its thinking counts against max_tokens. With a cap of 1200, the Router logged
exactly 1200 completion tokens for each call, and the content came back empty
(task 1123). Then the cap went to 8000 with thinking on. On 2026-11-01 one call
used all 8000 tokens, another 6023, and the plan outran the 30 s limit of the
BFF, so the member saw "Projects gateway unreachable".

These tests fake `acompletion_with_fallback` and pin four things:
- the call runs on `PLAN_MODEL` (tier-fast) and asks for no reasoning;
- its cap `PLAN_MAX_TOKENS` fits the JSON answer, not a long think;
- an empty answer that ended on the cap gives a reason that names the cap;
- a good JSON answer still gives the ordered plan.

Mutations: drop `thinking=` from the call, or set the cap back to 8000, and
`test_the_call_asks_for_no_reasoning_on_the_fast_tier` fails.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import acb_llm.context as llm_context
import pytest
from gateway.routes.tasks import calendar as cal

_CANDS = [
    {"id": "a1", "title": "Call the supplier", "estimate_mins": 30,
     "leveraged": False, "important": False, "due_in_days": None,
     "energy": None, "context": "calls"},
    {"id": "a2", "title": "Write the report", "estimate_mins": 60,
     "leveraged": True, "important": True, "due_in_days": 1,
     "energy": "high", "context": None},
]


def _reply(content: str | None, finish: str = "stop"):
    msg = SimpleNamespace(content=content)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason=finish)])


@pytest.fixture
def fake_llm(monkeypatch):
    calls: list[dict] = []
    box: dict = {"reply": _reply('{"plan": [], "notes": null}')}

    async def fake(**kwargs):
        calls.append(kwargs)
        return box["reply"], kwargs.get("model")

    monkeypatch.setattr(llm_context, "acompletion_with_fallback", fake)
    return SimpleNamespace(calls=calls, box=box)


def _rank(note: str | None = "calls after 8 am"):
    return asyncio.run(cal._llm_rank_day(_CANDS, note, 360, cal.PLAN_MODEL))


def test_the_call_asks_for_no_reasoning_on_the_fast_tier(fake_llm):
    _rank()
    assert len(fake_llm.calls) == 1
    call = fake_llm.calls[0]
    assert call["model"] == cal.PLAN_MODEL == "tier-fast"
    assert call["thinking"] == {"type": "disabled"}
    assert call["max_tokens"] == cal.PLAN_MAX_TOKENS
    # The JSON answer needs about 1000 tokens. No room for a long think.
    assert 1500 <= cal.PLAN_MAX_TOKENS <= 3000


def test_the_plan_route_uses_the_fast_tier_not_the_members_chat_model():
    import inspect

    src = inspect.getsource(cal)
    assert 'task_models(db, uid))["chat"]' not in src
    assert "req.capacity_mins, PLAN_MODEL, one_thing_id" in src


def test_an_empty_answer_at_the_cap_names_the_cap(fake_llm):
    fake_llm.box["reply"] = _reply("", finish="length")
    ordered, notes, reason = _rank()
    assert ordered is None and notes is None
    assert reason is not None
    assert "whole answer budget" in reason
    assert "JSONDecodeError" not in reason


def test_an_empty_answer_for_another_reason_says_no_plan(fake_llm):
    fake_llm.box["reply"] = _reply(None, finish="stop")
    ordered, _notes, reason = _rank()
    assert ordered is None
    assert reason == "the AI model gave no plan"


def test_a_good_answer_gives_the_ordered_plan(fake_llm):
    fake_llm.box["reply"] = _reply(
        '{"plan": [{"id": "a2", "preferred_energy": "high", "rationale": "due soon"},'
        ' {"id": "a1", "preferred_energy": "low", "rationale": "a call"},'
        ' {"id": "zz", "preferred_energy": "low", "rationale": "not a candidate"}],'
        ' "notes": "calls after 8"}')
    ordered, notes, reason = _rank()
    assert reason is None
    assert [o["id"] for o in ordered] == ["a2", "a1"]
    assert ordered[0]["preferred_energy"] == "high"
    assert notes == "calls after 8"


def test_the_rank_is_bounded_inside_the_proxy_limit():
    """A stalled model must not outrun the 30 s of the BFF proxy.

    Mutation: remove the `asyncio.wait_for`, and this test fails.
    """
    import inspect

    assert cal.PLAN_RANK_BOUND_S <= 20
    src = inspect.getsource(cal._replan_core)
    assert "asyncio.wait_for(" in src and "PLAN_RANK_BOUND_S" in src
    assert "the AI model took too long" in src


def test_the_note_reaches_the_model(fake_llm):
    _rank("reschedule all calls after 8 am")
    user_msg = fake_llm.calls[0]["messages"][1]["content"]
    assert "reschedule all calls after 8 am" in user_msg
