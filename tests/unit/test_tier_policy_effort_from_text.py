"""The effort from the member's words. WS-45, the amendment of D90 §5 and §7.

Spec: ``project-docs/specs/ai_tier_routing.md``, the box "Amended by the owner,
2026-10-07". The member chooses no effort in the chat, and the chat sends
``think_mode`` "auto". So the turn-kind question also asks whether the
member's words explicitly ask for deep, careful or thorough work. A yes at or
above the threshold makes the run's effort ``thinking``, never ``max``.

* It is the SAME request as the turn kind: a second item in one
  ``system_one.ask`` batch, on ``tier-fast``, inside the 1.5 s budget.
* A message under ``SHORT_MESSAGE_WORDS`` sends no request. A keyword check
  with no model (``asks_for_depth``) reads it instead.
* Only the member's own turn reads it (the stream path). A sub-agent and the
  batch path read a model's words, so they do not.

The run tests drive the REAL projects-assistant through the REAL
``run_agent_stream``, as ``test_tier_policy.py`` does. Hermetic: no SQL runs
on this path, so R8 binds nothing here.

Mutations this file catches (R7), each run red before the change:

* the effort item goes in a second request -> ``test_one_request_holds_both_items``;
* a confidence under the threshold still raises the effort ->
  ``test_under_the_threshold_the_effort_stays_auto``;
* the words give ``max`` -> ``test_the_words_never_give_max`` and
  ``test_a_covered_run_takes_thinking_from_the_words``;
* a short message sends a request, or the keyword path goes ->
  ``test_a_short_message_uses_the_keyword_check_and_sends_nothing``;
* a sub-agent or the batch path reads the words -> ``test_off_by_default``;
* the run ignores the raised effort -> ``test_a_covered_run_takes_thinking_from_the_words``;
* an agent the flag does not cover changes -> ``test_an_uncovered_run_is_as_today``.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from acb_skills import tier_policy

from tests.unit._native_maf_harness import (
    ScriptedModel,
    _a_tenant,  # noqa: F401 — a fixture, used by name
    drive_native,
    text_turn,
)
from tests.unit.test_tier_policy import (
    PA,
    PA_DIR,
    _answers,
    _flags,
    _fresh_settings,  # noqa: F401 — an autouse fixture, used by name
    _ok,
    _routed,  # noqa: F401 — a fixture, used by name
    _tap,
    _wire,
)

#: A long message (12 words or more) that explicitly asks for deep work.
DEEP = (
    "Please think hard and be thorough about how the invoice exporter for "
    "Acme should change next quarter"
)
#: A long message that asks for nothing deep.
PLAIN = (
    "Show me the open tasks of the invoice exporter project for Acme and "
    "who owns each one"
)


def _items(body: dict[str, Any]) -> list[dict[str, Any]]:
    return json.loads(body["messages"][-1]["content"])["items"]


def _reply(
    *, kind: str = "chat", kind_conf: float = 0.95,
    deep: str | None = "yes", deep_conf: float = 0.95,
):
    """System 1's answer to the turn-kind request, with or without the effort."""
    def reply(_body: dict[str, Any]):
        rows = [{"id": "turn", "choice": kind, "confidence": kind_conf, "reason": "x"}]
        if deep is not None:
            rows.append({"id": "effort", "choice": deep, "confidence": deep_conf,
                         "reason": "the member asks for it"})
        return _answers(*rows)
    return reply


@pytest.fixture
def _patient(monkeypatch):
    """A wait long enough for a cold process.

    The first System-1 call of a process imports and loads more than 1.5 s
    of work on a dev box. A test of the timeout sets its own wait.
    """
    monkeypatch.setattr(tier_policy, "TURN_KIND_TIMEOUT_S", 30.0)


def test_the_budget_does_not_move() -> None:
    assert tier_policy.TURN_KIND_TIMEOUT_S == 1.5
    assert tier_policy.SHORT_MESSAGE_WORDS == 12


def _kind(message: str, effort: str = "auto", *, read_effort: bool = True):
    return asyncio.run(
        tier_policy.turn_kind(message, ("vocabulary",), effort, read_effort=read_effort),
    )


@pytest.mark.usefixtures("_routed", "_patient")
class TestTheQuestion:
    def test_one_request_holds_both_items(self, monkeypatch) -> None:
        wire = _wire(monkeypatch, _reply(kind="analysis"))
        turn = _kind(DEEP)
        assert (turn.kind, turn.source) == ("analysis", "system_one")
        assert (turn.effort, turn.effort_source) == ("thinking", "system_one")
        # ONE request, on tier-fast, with the effort as a second item.
        assert len(wire.requests) == 1
        body = wire.bodies[0]
        assert body["model"] == "tier-fast"
        items = _items(body)
        assert [i["id"] for i in items] == ["turn", "effort"]
        assert items[1]["kind"] == "yes_no" and items[1]["options"] == ["yes", "no"]
        assert items[1]["question"] == tier_policy.EFFORT_QUESTION

    def test_under_the_threshold_the_effort_stays_auto(self, monkeypatch) -> None:
        """Auto's threshold is 0.70: 0.65 raises nothing, 0.70 does."""
        _wire(monkeypatch, _reply(deep_conf=0.65))
        assert _kind(DEEP).effort is None
        _wire(monkeypatch, _reply(deep_conf=0.70))
        assert _kind(DEEP).effort == "thinking"

    def test_a_no_raises_nothing(self, monkeypatch) -> None:
        _wire(monkeypatch, _reply(deep="no"))
        assert _kind(PLAIN).effort is None
        # An answer with no effort item raises nothing either.
        _wire(monkeypatch, _reply(deep=None))
        assert _kind(DEEP).effort is None

    def test_an_unsure_kind_keeps_a_sure_effort(self, monkeypatch) -> None:
        _wire(monkeypatch, _reply(kind="plan", kind_conf=0.4))
        turn = _kind(DEEP)
        assert (turn.kind, turn.source, turn.effort) == ("chat", "unsure", "thinking")

    def test_the_words_never_give_max(self, monkeypatch) -> None:
        assert tier_policy.TEXT_EFFORT == "thinking"
        _wire(monkeypatch, _reply())
        assert _kind(DEEP).effort != "max"

    def test_off_by_default(self, monkeypatch) -> None:
        """The batch path and a sub-agent call ``turn_kind`` with no
        ``read_effort``: one item, and no effort, as before."""
        wire = _wire(monkeypatch, _reply())
        turn = asyncio.run(tier_policy.turn_kind(DEEP, ("vocabulary",), "auto"))
        assert turn.effort is None
        assert [i["id"] for i in _items(wire.bodies[0])] == ["turn"]
        assert _kind("think hard", read_effort=False).effort is None

    def test_thinking_and_max_ask_no_effort(self, monkeypatch) -> None:
        wire = _wire(monkeypatch, _reply())
        turn = _kind(DEEP, effort="thinking")
        assert turn.effort is None
        assert [i["id"] for i in _items(wire.bodies[0])] == ["turn"]
        assert _kind(DEEP, effort="max").source == "max"
        assert len(wire.requests) == 1

    def test_a_short_message_uses_the_keyword_check_and_sends_nothing(
        self, monkeypatch,
    ) -> None:
        wire = _wire(monkeypatch, _reply())
        turn = _kind("Think hard about the Acme plan")
        assert (turn.kind, turn.source) == ("chat", "short")
        assert (turn.effort, turn.effort_source) == ("thinking", "keyword")
        assert _kind("What is due on Friday?").effort is None
        assert wire.requests == []

    def test_a_failure_raises_nothing(self, monkeypatch) -> None:
        async def slow(_body: dict[str, Any]):
            await asyncio.sleep(1.0)
            return _reply()(_body)

        _wire(monkeypatch, slow)
        monkeypatch.setattr(tier_policy, "TURN_KIND_TIMEOUT_S", 0.1)
        turn = _kind(DEEP)
        assert (turn.source, turn.effort) == ("timeout", None)


@pytest.mark.parametrize("text", [
    "think hard", "Think harder about it", "please think it through",
    "be thorough", "be careful here", "check it thoroughly", "a thorough review",
    "take your time", "a detailed analysis", "an in-depth look", "in depth please",
    "do a deep dive",
])
def test_the_keyword_check_finds_an_explicit_ask(text: str) -> None:
    assert tier_policy.asks_for_depth(text)


@pytest.mark.parametrize("text", [
    "hi", "thanks", "what is due today", "is the review thorough?",
    "I think the plan is hard", "careful", "show the depth chart", "",
])
def test_the_keyword_check_ignores_other_words(text: str) -> None:
    assert not tier_policy.asks_for_depth(text)


# ── A real projects-assistant run, through the REAL executor ────────────────


def _effort_lines(logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in logs if r.get("event") == "ai_route.effort_from_text"]


@pytest.mark.usefixtures("_routed", "_a_tenant", "_patient")
class TestARealRun:
    def test_a_covered_run_takes_thinking_from_the_words(self, monkeypatch) -> None:
        _flags(monkeypatch, PA)
        wire = _wire(monkeypatch, _reply(kind="chat"))
        model = ScriptedModel([text_turn("done")])
        logs = _tap(monkeypatch)
        events, _ = drive_native(PA, PA_DIR, monkeypatch, model, think_mode="auto",
                                 message=DEEP)
        _ok(events)
        # One System-1 request, as before, now with two items.
        assert len(wire.requests) == 1
        assert [i["id"] for i in _items(wire.bodies[0])] == ["turn", "effort"]
        # The run's effort is Thinking: reasoning effort medium, never Max's high.
        assert [b.get("reasoning_effort") for b in model.bodies] == ["medium"]
        assert [b["model"] for b in model.bodies] == ["tier-balanced"]
        lines = _effort_lines(logs)
        assert len(lines) == 1
        assert (lines[0]["effort"], lines[0]["source"]) == ("thinking", "system_one")
        assert "Acme" not in json.dumps(lines, default=str)

    def test_a_short_ask_raises_the_run_with_no_request(self, monkeypatch) -> None:
        _flags(monkeypatch, PA)
        wire = _wire(monkeypatch, _reply())
        model = ScriptedModel([text_turn("done")])
        logs = _tap(monkeypatch)
        events, _ = drive_native(PA, PA_DIR, monkeypatch, model, message="think hard about it")
        _ok(events)
        assert wire.requests == []
        assert [b.get("reasoning_effort") for b in model.bodies] == ["medium"]
        assert [r["source"] for r in _effort_lines(logs)] == ["keyword"]

    def test_a_plain_turn_stays_auto(self, monkeypatch) -> None:
        _flags(monkeypatch, PA)
        _wire(monkeypatch, _reply(deep="no"))
        model = ScriptedModel([text_turn("done")])
        logs = _tap(monkeypatch)
        events, _ = drive_native(PA, PA_DIR, monkeypatch, model, message=PLAIN)
        _ok(events)
        assert [b.get("reasoning_effort") for b in model.bodies] == [None]
        assert _effort_lines(logs) == []

    def test_an_uncovered_run_is_as_today(self, monkeypatch) -> None:
        def boom(*_a: Any, **_k: Any) -> Any:
            raise AssertionError("the policy ran for an agent the flag does not cover")

        monkeypatch.setattr(tier_policy, "turn_kind", boom)
        _flags(monkeypatch, "crm-assistant")
        model = ScriptedModel([text_turn("done")])
        logs = _tap(monkeypatch)
        events, _ = drive_native(PA, PA_DIR, monkeypatch, model, message="think hard about it")
        _ok(events)
        assert [b.get("reasoning_effort") for b in model.bodies] == [None]
        assert not [r for r in logs if str(r.get("event", "")).startswith("ai_route.")]
