"""A ``no_egress`` run may send a TYPED question to ``tier-decide``.

Owner decision of 2026-10-09. It amends ``data_narrowing_pipeline.md`` Q4 and
the D93 ``no_egress`` rule of ``ai_tier_routing.md`` §5, §6.1 and §6.6.

The decide vendor is already a sub-processor (email rule matching). The owner
allows a typed question with SHORT summaries to reach it from a ``no_egress``
run. Free-form work stays on our chat tiers. ``DECIDE_IN_NO_EGRESS`` is the
ONE switch that undoes it, and it ships ON.

The requests go through the REAL facade and the REAL Console client, on the
harness of ``test_system_one_tool.py``. Only the HTTP transports are scripts.

Hermetic: no SQL runs on this path, so R8 binds nothing here.

Mutations this file catches (R7), each run red on 2026-10-09 and then taken
back out:

* ``_decide_route`` ignores the switch (a ``no_egress`` run stays on
  ``tier-fast``) -> ``test_a_short_typed_question_goes_to_tier_decide``;
* ``_decide_route`` ignores ``no_egress`` with the switch off ->
  ``test_the_switch_off_restores_tier_fast``;
* the short bound goes (``short_only`` dropped) ->
  ``test_a_long_context_stays_on_tier_fast`` and
  ``test_a_long_option_stays_on_tier_fast``;
* the switch ships OFF, or a broken read opens it ->
  ``test_the_switch_ships_on`` and ``test_a_broken_read_reads_as_off``;
* the turn-kind question reaches the door ->
  ``test_the_turn_kind_question_stays_on_tier_fast``.
"""
from __future__ import annotations

import asyncio

import pytest
import structlog
from acb_common import bind_run_context, clear_run_context
from acb_common.settings import get_settings
from acb_skills import decide_tools, tier_policy
from acb_skills.write_artifact import bind_artifact_context

from tests.unit.test_system_one_tool import (
    FALLBACK,
    PA,
    _answers,
    _ask,
    _door,
    _fast_ok,
    _verdict,
    _wire,
)


@pytest.fixture(autouse=True)
def _no_egress_run(monkeypatch):
    """A routed box with the decide door on, and a bound ``no_egress`` run."""
    monkeypatch.setenv("ROUTER_SERVING_ENABLED", "1")
    monkeypatch.setenv("CUSTOMER_CONSOLE_URL", "https://console.test")
    monkeypatch.setenv("CUSTOMER_CONSOLE_ORG_KEY", "cc_live_fixture_notarealsecret")
    monkeypatch.delenv("CUSTOMER_CONSOLE_DEPLOYMENT_KEY", raising=False)
    monkeypatch.setenv("CUSTOMER_CONSOLE_ROUTER_USES_DEPLOYMENT_KEY", "false")
    monkeypatch.setenv("SYSTEM_ONE_ON_DECIDE", "true")
    monkeypatch.setenv("DECIDE_ENABLED", "true")
    monkeypatch.delenv("DECIDE_IN_NO_EGRESS", raising=False)
    monkeypatch.delenv("AI_TIER_ROUTING", raising=False)
    get_settings.cache_clear()
    clear_run_context()
    bind_run_context(run_id="run-ne", agent=PA, user="member@example.com", source="chat")
    bind_artifact_context(agent_name=PA, run_id="run-ne", think_mode="auto", no_egress=True)
    yield
    clear_run_context()
    bind_artifact_context()
    get_settings.cache_clear()


def _switch(monkeypatch, value: str) -> None:
    monkeypatch.setenv("DECIDE_IN_NO_EGRESS", value)
    get_settings.cache_clear()


def test_the_switch_ships_on() -> None:
    from acb_common.settings import Settings

    assert Settings.model_fields["decide_in_no_egress"].default is True
    assert decide_tools.decide_in_no_egress() is True


def test_a_broken_read_reads_as_off(monkeypatch) -> None:
    import acb_common

    def boom():
        raise RuntimeError("settings")

    monkeypatch.setattr(acb_common, "get_settings", boom)
    assert decide_tools.decide_in_no_egress() is False
    assert decide_tools._decide_route() is None


def test_a_short_typed_question_goes_to_tier_decide(monkeypatch) -> None:
    """The owner's default. One decide request, and no ``tier-fast`` one."""
    door = _door(monkeypatch, lambda b: _verdict(b))
    wire = _wire(monkeypatch, _fast_ok)
    out = _ask(question="Which status fits?", context="name: in progres",
               kind="choice", options="In progress\nDone")
    assert len(door.requests) == 1 and wire.requests == []
    assert door.bodies[0]["state"] == "name: in progres"
    assert out.splitlines()[1].startswith("In progress (confidence")


def test_the_switch_off_restores_tier_fast(monkeypatch) -> None:
    _switch(monkeypatch, "false")
    door = _door(monkeypatch, lambda b: _verdict(b))
    wire = _wire(monkeypatch, _fast_ok)
    _ask(question="Which status fits?", context="name: in progres",
         kind="choice", options="In progress\nDone")
    assert door.requests == []
    assert len(wire.requests) == 1 and wire.bodies[0]["model"] == "tier-fast"


def test_an_open_run_takes_a_long_context_to_tier_decide(monkeypatch) -> None:
    """The short bound binds a ``no_egress`` run only. D93 is unchanged."""
    bind_artifact_context(agent_name=PA, run_id="run-ne", think_mode="auto", no_egress=False)
    door = _door(monkeypatch, lambda b: _verdict(b))
    wire = _wire(monkeypatch, _fast_ok)
    _ask(question="Is it urgent?", context="x" * (decide_tools.NO_EGRESS_CONTEXT_MAX + 1))
    assert len(door.requests) == 1 and wire.requests == []


def test_a_long_context_stays_on_tier_fast(monkeypatch) -> None:
    """Free-form work stays on our chat tier, and the line logs a code."""
    door = _door(monkeypatch, lambda b: _verdict(b))
    wire = _wire(monkeypatch, _fast_ok)
    long = "member text " * (decide_tools.NO_EGRESS_CONTEXT_MAX // 10)
    with structlog.testing.capture_logs() as logs:
        _ask(question="Is it urgent?", context=long)
    assert door.requests == []
    assert len(wire.requests) == 1 and wire.bodies[0]["model"] == "tier-fast"
    codes = [e.get("reason") for e in logs if e["event"] == FALLBACK]
    assert codes == [decide_tools.NOT_SHORT]
    assert "member text" not in repr(logs)


def test_a_long_option_stays_on_tier_fast(monkeypatch) -> None:
    door = _door(monkeypatch, lambda b: _verdict(b))
    wire = _wire(monkeypatch, _fast_ok)
    option = "o" * (decide_tools.NO_EGRESS_OPTION_MAX + 1)
    _ask(question="Which fits?", context="short", kind="choice",
         options=f"{option}\nother")
    assert door.requests == [] and len(wire.requests) == 1


def test_a_batch_sends_only_its_short_items(monkeypatch) -> None:
    """One long question goes to ``tier-fast``, and the short one to the door."""
    import json

    door = _door(monkeypatch, lambda b: _verdict(b))
    wire = _wire(monkeypatch, _fast_ok)
    items = json.dumps([
        {"id": "a", "question": "Is it urgent?", "kind": "yes_no"},
        {"id": "b", "question": "q" * (decide_tools.NO_EGRESS_QUESTION_MAX + 1),
         "kind": "yes_no"},
    ])
    out = _ask(question="batch", context="short", items=items)
    assert [list(b["questions"]) for b in door.bodies] == [["a"]]
    assert len(wire.requests) == 1
    sent = json.loads(wire.bodies[0]["messages"][-1]["content"])
    assert [i["id"] for i in sent["items"]] == ["b"]
    assert out.splitlines()[1].startswith("a: yes")


def test_the_turn_kind_question_stays_on_tier_fast(monkeypatch) -> None:
    door = _door(monkeypatch, lambda b: _verdict(b))
    wire = _wire(monkeypatch, lambda _b: _answers(
        {"id": "turn", "choice": "plan", "confidence": 0.95, "reason": ""},
    ))
    message = "plan the launch of the beta across the three teams with every dependency named"
    kind = asyncio.run(tier_policy.turn_kind(message, ["decide"], "auto"))
    assert kind.kind == "plan"
    assert door.requests == []
    assert len(wire.requests) == 1 and wire.bodies[0]["model"] == "tier-fast"
