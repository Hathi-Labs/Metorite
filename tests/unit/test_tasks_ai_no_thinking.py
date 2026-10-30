"""Each AI call of the Tasks package asks for no reasoning (task #1122).

Measured on production, 2026-10-25 to 10-30 (``usage_event``, agent
``tasks.*``): 19 of 20 calls stopped at exactly their output cap. The Router
binds the tiers to ``deepseek/deepseek-flash`` and ``deepseek/deepseek-v4-pro``,
and both think by default. The reasoning used up the cap, ``content`` came
back empty, and Rebuild my day showed "JSONDecodeError: Expecting value".

Mutations this file catches (R7). Each one ran red:

* a Tasks call drops ``thinking=`` -> ``test_every_tasks_call_asks_for_no_reasoning``;
* ``NO_THINKING`` becomes ``{"type": "enabled"}`` ->
  ``test_the_value_is_disabled`` and ``test_the_day_plan_reads_the_answer``;
* ``_llm_rank_day`` loses its empty-reply check ->
  ``test_an_empty_reply_says_why``.
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import acb_llm.context as llm_context
import pytest
from gateway.routes.tasks import calendar as tasks_calendar
from gateway.routes.tasks.core import NO_THINKING

TASKS_DIR = (Path(__file__).resolve().parents[2]
             / "apps/services/gateway/gateway/routes/tasks")
CALL = "acompletion_with_fallback"
ANSWER = ('{"plan": [{"id": "t1", "preferred_energy": "high", '
          '"rationale": "Due today."}], "notes": null}')


def _calls() -> list[tuple[str, int, ast.Call]]:
    out = []
    for path in sorted(TASKS_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and getattr(node.func, "id", getattr(node.func, "attr", "")) == CALL):
                out.append((path.name, node.lineno, node))
    return out


def _sends_no_thinking(call: ast.Call) -> bool:
    for kw in call.keywords:
        if kw.arg != "thinking":
            continue
        src = ast.unparse(kw.value)
        return src in ("NO_THINKING", "dict(NO_THINKING)")
    return False


class TestTheFence:

    def test_every_tasks_call_asks_for_no_reasoning(self) -> None:
        calls = _calls()
        # Ten calls on 2026-10-31. A scan that finds none proves nothing.
        assert len(calls) >= 10, f"the scan found only {len(calls)} calls"
        missing = [f"{name}:{line}" for name, line, call in calls
                   if not _sends_no_thinking(call)]
        assert not missing, (
            "These Tasks AI calls let the model think, and the reasoning uses "
            f"up the cap before the JSON. Add thinking=dict(NO_THINKING): {missing}")

    def test_the_value_is_disabled(self) -> None:
        assert NO_THINKING == {"type": "disabled"}

    def test_the_router_forwards_thinking(self) -> None:
        from acb_llm.routed import _FORWARDABLE

        assert "thinking" in _FORWARDABLE


def _response(content: str, finish: str, tokens: int) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content),
                                 finish_reason=finish)],
        usage=SimpleNamespace(completion_tokens=tokens))


class ThinkingVendor:
    """The measured behaviour: unless the request carries
    ``thinking={"type": "disabled"}``, the reasoning fills the cap and
    ``content`` comes back empty with ``finish_reason="length"``."""

    def __init__(self, *, ignores_flag: bool = False) -> None:
        self.ignores_flag = ignores_flag
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, *, model, messages, max_tokens, temperature, **extra):
        self.calls.append({"model": model, "max_tokens": max_tokens, **extra})
        if self.ignores_flag or extra.get("thinking") != {"type": "disabled"}:
            return _response("", "length", max_tokens), model
        return _response(ANSWER, "stop", 40), model


CANDS = [{"id": "t1", "title": "Send the quotation", "estimate_mins": 30,
          "leveraged": False, "important": True, "deep_work": False,
          "due_in_days": 0, "energy": None, "context": None}]


async def _rank() -> tuple[list[dict] | None, str | None, str | None]:
    return await tasks_calendar._llm_rank_day(
        CANDS, "only 2 hours of work", 120, "tier-powerful")


class TestTheDayPlan:

    async def test_the_day_plan_reads_the_answer(self, monkeypatch) -> None:
        vendor = ThinkingVendor()
        monkeypatch.setattr(llm_context, CALL, vendor)
        ordered, _notes, reason = await _rank()
        assert reason is None
        assert ordered and ordered[0]["id"] == "t1"
        assert vendor.calls[0]["thinking"] == {"type": "disabled"}

    async def test_an_empty_reply_says_why(self, monkeypatch) -> None:
        monkeypatch.setattr(llm_context, CALL, ThinkingVendor(ignores_flag=True))
        ordered, _notes, reason = await _rank()
        assert ordered is None
        assert reason == "the AI model ran out of room before it answered"
        assert "JSONDecodeError" not in reason


@pytest.fixture(autouse=True)
def _no_real_ai(monkeypatch) -> None:
    """A test that forgets its fake must fail, never call a vendor."""
    async def _refuse(**_kw):
        raise AssertionError("this test reached the real AI client")

    monkeypatch.setattr(llm_context, CALL, _refuse)
