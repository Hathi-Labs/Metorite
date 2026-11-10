"""Each AI call of the Tasks package asks for no reasoning (task #1122).

Measured on production, 2026-10-25 to 10-30 (``usage_event``, agent
``tasks.*``): 19 of 20 calls stopped at exactly their output cap. The Router
binds the tiers to ``deepseek/deepseek-flash`` and ``deepseek/deepseek-v4-pro``,
and both think by default. The reasoning used up the cap and ``content`` came
back empty.

The day plan (``calendar.py``, ``_llm_rank_day``) asks for no reasoning too,
since 2026-11-01. With thinking on, one call used all 8000 tokens and the plan
outran the 30 s limit of the BFF. No Tasks call is exempt now.

Mutations this file catches (R7). Each one ran red:

* a Tasks call drops ``thinking=`` -> ``test_every_tasks_call_asks_for_no_reasoning``;
* ``NO_THINKING`` becomes ``{"type": "enabled"}`` ->
  ``test_the_value_is_disabled`` and ``test_the_profile_parse_reads_the_answer``;
* the day plan thinks again, or loses room for its JSON ->
  ``test_the_day_plan_answers_fast``.
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import acb_llm.context as llm_context
import pytest
from gateway.routes.tasks import calendar as tasks_calendar
from gateway.routes.tasks import resume_parse
from gateway.routes.tasks.core import NO_THINKING

TASKS_DIR = (Path(__file__).resolve().parents[2]
             / "apps/services/gateway/gateway/routes/tasks")
CALL = "acompletion_with_fallback"
#: The calls that keep their reasoning, by file, with the reason. None today.
EXEMPT: dict[str, str] = {}
ANSWER = '{"skills": ["cad"], "experience_summary": "Designs parts.", "domain": "mechanical"}'


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
        if kw.arg == "thinking":
            return ast.unparse(kw.value) in ("NO_THINKING", "dict(NO_THINKING)")
    return False


class TestTheFence:

    def test_every_tasks_call_asks_for_no_reasoning(self) -> None:
        calls = [c for c in _calls() if c[0] not in EXEMPT]
        # Nine calls on 2026-10-31. A scan that finds none proves nothing.
        assert len(calls) >= 9, f"the scan found only {len(calls)} calls"
        missing = [f"{name}:{line}" for name, line, call in calls
                   if not _sends_no_thinking(call)]
        assert not missing, (
            "These Tasks AI calls let the model think, and the reasoning uses "
            f"up the cap before the JSON. Add thinking=dict(NO_THINKING): {missing}")

    def test_each_exempt_file_still_has_its_call(self) -> None:
        """An exemption for a call that is gone would hide a new call there."""
        names = {name for name, _line, _call in _calls()}
        assert set(EXEMPT) <= names

    def test_the_day_plan_answers_fast(self) -> None:
        """The day plan runs on the fast tier, with room for its JSON only."""
        assert tasks_calendar.PLAN_MODEL == "tier-fast"
        assert 1500 <= tasks_calendar.PLAN_MAX_TOKENS <= 3000
        assert "calendar.py" in {name for name, _line, _call in _calls()}

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

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, *, model, messages, max_tokens, temperature, **extra):
        self.calls.append({"model": model, "max_tokens": max_tokens, **extra})
        if extra.get("thinking") != {"type": "disabled"}:
            return _response("", "length", max_tokens), model
        return _response(ANSWER, "stop", 40), model


class TestOneCall:

    async def test_the_profile_parse_reads_the_answer(self, monkeypatch) -> None:
        vendor = ThinkingVendor()
        monkeypatch.setattr(llm_context, CALL, vendor)
        out = await resume_parse.llm_extract_profile("Ten years of CAD work.")
        assert out["skills"] == ["cad"]
        assert vendor.calls[0]["thinking"] == {"type": "disabled"}


@pytest.fixture(autouse=True)
def _no_real_ai(monkeypatch) -> None:
    """A test that forgets its fake must fail, never call a vendor."""
    async def _refuse(**_kw):
        raise AssertionError("this test reached the real AI client")

    monkeypatch.setattr(llm_context, CALL, _refuse)
