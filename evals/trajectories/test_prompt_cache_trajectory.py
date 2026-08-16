"""Golden trajectories: provider-aware prompt caching (C4).

Locks the end-to-end caching contract (specs/llm_caching_memory.md Phase 2/3):

  1. The executor marks the stable/dynamic boundary with the CACHE BREAK
     sentinel where memory context is appended (stable prefix first).
  2. The single ``apply_prompt_caching`` transform — called by BOTH the
     orchestrator-internal ``acb_llm.complete*`` path AND the ``/v1`` agent
     choke point — consumes that sentinel: Anthropic gets an explicit
     ``cache_control`` breakpoint at exactly that seam; every other provider
     gets the sentinel stripped so it never reaches the model.

The invariant that must never regress: the sentinel is invisible to the model,
and a stable Anthropic prefix/tool-array is always cache-marked so the provider
caches it (the single biggest cost lever in the platform).
"""
from __future__ import annotations

from acb_llm.prompt_cache import (
    CACHE_BREAK,
    apply_prompt_caching,
)

# Mirrors how executor.py assembles the system prompt: stable prefix
# (instructions + tool addendum) FIRST, sentinel, then the dynamic memory block.
_STABLE_PREFIX = (
    "You are Metorite, an autonomous agent.\n"
    "## Tools\n" + ("- call_agent / web_search / write_artifact ...\n" * 60)
)
_MEMORY = "## Relevant memories\n- The user prefers terse answers.\n"


def _executor_merged_system() -> dict:
    """Reproduce the executor's stable\\n<sentinel>\\ndynamic merge shape."""
    content = f"{_STABLE_PREFIX}\n{CACHE_BREAK}\n{_MEMORY}"
    return {"role": "system", "content": content}


def test_anthropic_run_caches_stable_prefix_at_the_seam():
    """The stable prefix is one cached block; memory is a separate uncached one."""
    msgs, _tools, _extra = apply_prompt_caching(
        model="anthropic/claude-sonnet-4-5",
        messages=[_executor_merged_system(), {"role": "user", "content": "go"}],
        tools=None,
    )
    blocks = msgs[0]["content"]
    assert [b.get("cache_control") for b in blocks] == [
        {"type": "ephemeral"},  # stable prefix → cached
        None,                    # dynamic memory → not cached
    ]
    assert _STABLE_PREFIX.strip() in blocks[0]["text"]
    assert _MEMORY.strip() in blocks[1]["text"]
    assert CACHE_BREAK not in str(msgs)


def test_deepseek_run_strips_sentinel_no_caching_blocks():
    """Our DEFAULT tier is DeepSeek — the seam must vanish, no blocks added."""
    msgs, tools, extra = apply_prompt_caching(
        model="deepseek/deepseek-chat",
        messages=[_executor_merged_system(), {"role": "user", "content": "go"}],
        tools=[{"type": "function", "function": {"name": "x", "parameters": {}}}],
        cache_key="cc",
    )
    assert isinstance(msgs[0]["content"], str)
    assert CACHE_BREAK not in msgs[0]["content"]
    # DeepSeek is neither Anthropic nor OpenAI → nothing marked, no routing key.
    assert "cache_control" not in str(tools)
    assert "prompt_cache_key" not in extra


def test_function_tool_agent_caches_the_tool_array_first():
    """Anthropic caches tools→system→messages; the tool array is the first,
    highest-value block for a function-tool agent (email-assistant ~63 tools)."""
    tools = [
        {"type": "function", "function": {"name": f"t{i}", "parameters": {}}}
        for i in range(63)
    ]
    _m, out_tools, _e = apply_prompt_caching(
        model="anthropic/claude-sonnet-4-5",
        messages=[_executor_merged_system()],
        tools=tools,
    )
    # Exactly one breakpoint on the tool array — the last entry — which caches
    # the whole prefix. Anthropic allows only 4 breakpoints; we spend one here.
    marked = [
        i for i, t in enumerate(out_tools)
        if "cache_control" in t.get("function", {})
    ]
    assert marked == [62]


def test_openai_run_routes_by_agent_no_manual_blocks():
    """OpenAI caches automatically; we only add the routing key, no blocks."""
    _m, _t, extra = apply_prompt_caching(
        model="openai/gpt-4o",
        messages=[_executor_merged_system()],
        tools=None,
        cache_key="sales-agent",
    )
    assert extra["prompt_cache_key"] == "sales-agent"


def test_breakpoint_budget_respected_system_plus_tools():
    """System-split (1) + tool-array (1) = 2 breakpoints, within Anthropic's 4."""
    tools = [{"type": "function", "function": {"name": "x", "parameters": {}}}]
    msgs, out_tools, _e = apply_prompt_caching(
        model="anthropic/claude-sonnet-4-5",
        messages=[_executor_merged_system()],
        tools=tools,
    )
    sys_breaks = sum(
        1 for b in msgs[0]["content"] if "cache_control" in b
    )
    tool_breaks = sum(
        1 for t in out_tools if "cache_control" in t.get("function", {})
    )
    assert sys_breaks + tool_breaks == 2
    assert sys_breaks + tool_breaks <= 4
