"""The Tier 2 batch path shows the steps of a native MAF 1.19 agent.

Owner report, 2026-10-05: the Projects assistant showed "Thinking…", a todo
list and artifact cards, and none of its steps. A native agent that falls back
from Tier 1 to the Tier 2 batch path streams a tool row only for a tool that
path wrapped in its shim. MAF 1.19 holds an ``Agent``'s tools in
``default_options["tools"]``, which the older ``agent.tools`` lookup never
reached, so nothing was wrapped.

R7 fence: ``_tier2_tool_view`` wraps every ``FunctionTool`` there, on a
per-run copy, and changes neither the shared agent nor a shared tool.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from agent_framework import FunctionTool

from orchestrator.executor import _tier2_tool_view


async def create_task(title: str) -> str:
    """Create a task."""
    return f"created {title}"


def _agent_with(tools: list) -> SimpleNamespace:
    return SimpleNamespace(name="projects-assistant", default_options={"tools": tools, "model": "m"})


def test_each_default_option_tool_is_wrapped_on_a_copy():
    calls: list[tuple[str, dict]] = []

    def make_shim(fn, name):
        async def shim(*args, **kwargs):
            calls.append((name, kwargs))
            return await fn(*args, **kwargs)
        return shim

    tool = FunctionTool(name="create_task", description="Create a task.", func=create_task)
    agent = _agent_with([tool])
    view = _tier2_tool_view(agent, make_shim)

    assert view is not agent
    (wrapped,) = view.default_options["tools"]
    assert wrapped is not tool
    assert wrapped.name == "create_task"
    assert wrapped.description == "Create a task."
    assert view.default_options["model"] == "m"
    # The shared agent and the shared tool are untouched.
    assert agent.default_options["tools"] == [tool]
    assert tool.func is create_task

    result = asyncio.run(wrapped.invoke(arguments={"title": "Ship"}))
    assert "created Ship" in " ".join(str(getattr(c, "text", c)) for c in result)
    assert calls == [("create_task", {"title": "Ship"})]


def test_an_agent_with_nothing_to_wrap_is_returned_as_it_is():
    plain = SimpleNamespace(name="legacy")
    assert _tier2_tool_view(plain, lambda f, n: f) is plain
    empty = _agent_with([])
    assert _tier2_tool_view(empty, lambda f, n: f) is empty


def test_a_view_is_never_wrapped_twice():
    tool = FunctionTool(name="create_task", description="d", func=create_task)

    def make_shim(fn, name):
        async def shim(*args, **kwargs):
            return await fn(*args, **kwargs)
        return shim

    once = _tier2_tool_view(_agent_with([tool]), make_shim)
    twice = _tier2_tool_view(once, make_shim)
    assert twice is once


def test_a_wrapped_platform_tool_keeps_its_egress_answer():
    """H-236 trusts a platform tool by the identity of its callable. The shim
    is a new callable, so it must inherit that trust. Otherwise a covered run
    that falls back to Tier 2 withholds every platform tool it holds."""
    from acb_skills.egress import is_egress_tool
    from acb_skills.sandbox_tools import run_command

    original = FunctionTool(name="run_command", description="Run a command.", func=run_command)

    def make_shim(fn, name):
        async def shim(*args, **kwargs):
            return await fn(*args, **kwargs)
        return shim

    view = _tier2_tool_view(_agent_with([original]), make_shim)
    (clone,) = view.default_options["tools"]
    assert clone.func is not run_command
    assert is_egress_tool(original) is False, "the fence needs a tool a covered run keeps"
    assert is_egress_tool(clone) == is_egress_tool(original)
