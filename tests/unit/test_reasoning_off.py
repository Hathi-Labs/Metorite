"""Thinking off for an `auto` chat turn of a listed agent (chat speed, 2026-10-25).

DeepSeek V4 thinks by default. An `auto` request carried no reasoning
parameter, so the orchestrator spent most of its output tokens on reasoning
before it picked a specialist. `reasoning_effort: "none"` turns thinking off
(litellm sends `thinking: {type: disabled}`). The turn decides once, so no
request of it mixes thinking on and off.

Mutations this file catches (R7):

* `reasoning_off` ignores the effort: a Thinking or Max turn loses thinking.
* `reasoning_off` ignores the kind: a plan, analysis or code turn loses it.
* The flag reads as ON when it is empty, or when the settings read fails.
* The middleware overwrites a reasoning effort that the request carries.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest
from acb_skills import tier_policy as tp


@pytest.fixture()
def listed(monkeypatch: pytest.MonkeyPatch):
    from acb_common.settings import get_settings

    def _set(value: str | None) -> None:
        if value is None:
            monkeypatch.delenv("AI_REASONING_OFF_AGENTS", raising=False)
        else:
            monkeypatch.setenv("AI_REASONING_OFF_AGENTS", value)
        get_settings.cache_clear()

    yield _set
    monkeypatch.delenv("AI_REASONING_OFF_AGENTS", raising=False)
    get_settings.cache_clear()


def test_only_an_auto_chat_turn_of_a_listed_agent(listed) -> None:
    listed("orchestrator")
    assert tp.reasoning_off("orchestrator", kind="chat", effort="auto")
    assert tp.reasoning_off("orchestrator", kind="chat", effort=None), "unset is auto"
    for effort in ("thinking", "max"):
        assert not tp.reasoning_off("orchestrator", kind="chat", effort=effort)
    for kind in ("plan", "analysis", "code"):
        assert not tp.reasoning_off("orchestrator", kind=kind, effort="auto")
    assert not tp.reasoning_off("email-assistant", kind="chat", effort="auto")
    assert not tp.reasoning_off("", kind="chat", effort="auto")


def test_empty_means_off_and_star_means_every_agent(listed) -> None:
    listed(None)
    assert not tp.reasoning_off("orchestrator", kind="chat", effort="auto")
    listed("")
    assert not tp.reasoning_off("orchestrator", kind="chat", effort="auto")
    listed("*")
    assert tp.reasoning_off("crm-assistant", kind="chat", effort="auto")


def test_a_settings_fault_reads_as_off(monkeypatch: pytest.MonkeyPatch) -> None:
    import acb_common

    def boom() -> Any:
        raise RuntimeError("settings broke")

    monkeypatch.setattr(acb_common, "get_settings", boom)
    assert not tp.reasoning_off("orchestrator", kind="chat", effort="auto")


class _Context:
    def __init__(self, options: dict[str, Any]) -> None:
        self.options = options


def _run(policy: tp.RunTierPolicy, options: dict[str, Any]) -> dict[str, Any]:
    ctx = _Context(options)

    async def call_next() -> None:
        return None

    asyncio.run(tp.TierPolicyMiddleware(policy).process(ctx, call_next))
    return ctx.options


def _policy(kind: str = "chat", effort: str = "auto") -> tp.RunTierPolicy:
    return tp.RunTierPolicy(agent="orchestrator", run_id="r", default=tp.TIER_BALANCED,
                            kind=kind, effort=effort)


def test_every_request_of_the_turn_carries_it(listed) -> None:
    listed("orchestrator")
    policy = _policy()
    first = _run(policy, {"tools": []})
    second = _run(policy, {"tools": []})
    for options in (first, second):
        assert options["reasoning_effort"] == tp.REASONING_OFF
        assert options["model"] == tp.TIER_BALANCED


def test_a_thinking_turn_sends_no_reasoning_option(listed) -> None:
    listed("orchestrator")
    assert "reasoning_effort" not in _run(_policy(effort="thinking"), {})
    assert "reasoning_effort" not in _run(_policy(kind="plan"), {})


def test_the_request_keeps_an_effort_it_already_carries(listed) -> None:
    listed("orchestrator")
    options = _run(_policy(), {"reasoning_effort": "high"})
    assert options["reasoning_effort"] == "high"


def test_with_the_flag_unset_the_options_do_not_change(listed) -> None:
    listed(None)
    options = _run(_policy(), {"tools": ["x"]})
    assert options == {"tools": ["x"], "model": tp.TIER_BALANCED}
