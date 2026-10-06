"""Unit tests for own_tool_scope filtering of agent-baked tools (HH-5).

``tool_scope`` filters which PLATFORM tools the executor injects;
``own_tool_scope`` (config.json) is its counterpart for tools the agent repo
ships itself — previously an agent baking 60+ tools (email-assistant) could
not be narrowed per deployment.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from orchestrator import _tool_injection as _ti
from orchestrator._tool_injection import (
    _count_agent_tools,
    _log_agent_tools_resolved,
    _own_tool_pools,
)
from orchestrator.executor import _apply_own_tool_scope, _tool_name


def _fn(name: str):
    def tool():  # pragma: no cover - never called
        return None
    tool.__name__ = name
    return tool


def test_filters_maf_agent_tools_in_place():
    agent = SimpleNamespace(name="email-assistant",
                            tools=[_fn("read_email"), _fn("send_email"),
                                   _fn("get_digest")])
    _apply_own_tool_scope([agent], ["read_email", "get_digest"])
    assert [_tool_name(t) for t in agent.tools] == ["read_email", "get_digest"]


def test_filters_copilot_private_tools_list():
    agent = SimpleNamespace(name="x", _tools=[_fn("a"), _fn("b")], tools=[])
    _apply_own_tool_scope([agent], ["b"])
    assert [_tool_name(t) for t in agent._tools] == ["b"]


def test_no_match_keeps_all_tools():
    tools = [_fn("a"), _fn("b")]
    agent = SimpleNamespace(name="x", tools=list(tools))
    _apply_own_tool_scope([agent], ["nonexistent"])
    assert len(agent.tools) == 2  # fail open, mirrors tool_scope semantics


def test_none_scope_is_a_no_op():
    agent = SimpleNamespace(name="x", tools=[_fn("a")])
    _apply_own_tool_scope([agent], None)
    assert len(agent.tools) == 1


def test_tool_name_handles_wrappers_and_dicts():
    assert _tool_name(_fn("plain")) == "plain"
    assert _tool_name(SimpleNamespace(name="ai_function")) == "ai_function"
    assert _tool_name({"function": {"name": "dict_spec"}}) == "dict_spec"
    assert _tool_name({"name": "flat_dict"}) == "flat_dict"
    assert _tool_name(object()) == ""


# ── WS-8o: a real native MAF Agent (email_app_master_plan.md §10.4.14) ──────
#
# The tests above use a SimpleNamespace with ``tools``. A native MAF 1.19
# ``Agent`` has no ``tools`` attribute: ``RawAgent.__init__`` moves the tools
# into ``default_options["tools"]``. The filter read only ``tools`` and
# ``_tools``, so it did nothing for a native agent, and these tests could not
# see it. The tests below build a real ``Agent`` with no client, so no model
# and no network.


def _real_agent(names: list[str]):
    agent_framework = pytest.importorskip("agent_framework")
    return agent_framework.Agent(
        client=None, name="email-assistant", tools=[_fn(n) for n in names],
    )


def _held(agent) -> list[str]:
    return [_tool_name(t) for t in agent.default_options["tools"]]


def test_filters_a_real_maf_agent_in_its_default_options():
    agent = _real_agent(["read_email", "send_email", "get_digest"])
    assert not hasattr(agent, "tools"), "MAF moved its tools: re-read the pools"
    pool = agent.default_options["tools"]
    _apply_own_tool_scope([agent], ["read_email", "get_digest"])
    assert _held(agent) == ["read_email", "get_digest"]
    # In place: each run copies THIS list, so the change holds for the run.
    assert agent.default_options["tools"] is pool


def test_a_real_maf_agent_fails_open_with_no_match():
    agent = _real_agent(["read_email", "send_email"])
    _apply_own_tool_scope([agent], ["nonexistent"])
    assert _held(agent) == ["read_email", "send_email"]


def test_mcp_tools_are_not_own_tools():
    agent = _real_agent(["read_email", "send_email"])
    marker = object()
    agent.mcp_tools = [marker]
    _apply_own_tool_scope([agent], ["read_email"])
    assert agent.mcp_tools == [marker]


def test_one_list_under_two_names_is_one_pool():
    shared = [_fn("a"), _fn("b")]
    agent = SimpleNamespace(name="x", tools=shared, _tools=shared)
    assert [label for label, _ in _own_tool_pools(agent)] == ["tools"]
    assert _count_agent_tools([agent]) == 2


def test_the_count_reads_the_real_agent():
    agent = _real_agent(["a", "b", "c"])
    _apply_own_tool_scope([agent], ["a", "c"])
    assert _count_agent_tools([agent]) == 2


def test_the_resolved_log_names_own_and_total(monkeypatch):
    seen: list[tuple[str, dict]] = []

    class _Log:
        def info(self, event, **kw):
            seen.append((event, kw))

    monkeypatch.setattr(_ti, "_log", _Log())
    agent = _real_agent(["a", "b"])
    own = _count_agent_tools([agent])
    agent.default_options["tools"].append(_fn("call_agent"))  # an injection
    _log_agent_tools_resolved("email-assistant", [agent], own)
    assert seen == [(
        "executor.agent_tools_resolved",
        {"agent": "email-assistant", "own": 2, "total": 3},
    )]
