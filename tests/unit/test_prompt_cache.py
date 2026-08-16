"""Unit tests for the provider-aware prompt-cache transform.

Covers specs/llm_caching_memory.md Phases 2 + 3 as implemented via the single
``acb_llm.prompt_cache.apply_prompt_caching`` transform (SDK-direct — no proxy).

The invariant we most care about: the CACHE BREAK sentinel is NEVER visible to
the model. On Anthropic it becomes the cached-block boundary; on every other
provider it is stripped. And a stable Anthropic system prompt / tool array
carries ``cache_control`` so the provider caches the prefix.
"""
from __future__ import annotations

from acb_llm.prompt_cache import (
    CACHE_BREAK,
    apply_prompt_caching,
    is_anthropic_model,
    is_openai_model,
    strip_sentinel,
)

STABLE = "You are an agent. " * 40  # a chunky, stable system prefix
DYNAMIC = "## Relevant memories\n- User prefers concise replies.\n"


def _sys(content: str) -> dict:
    return {"role": "system", "content": content}


def _sys_with_break() -> dict:
    return _sys(f"{STABLE}\n{CACHE_BREAK}\n{DYNAMIC}")


def _tools(n: int = 3) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": f"tool_{i}",
                "description": f"does thing {i}",
                "parameters": {"type": "object", "properties": {}},
            },
        }
        for i in range(n)
    ]


# ── Provider detection ──────────────────────────────────────────────────────


def test_provider_detection() -> None:
    assert is_anthropic_model("anthropic/claude-sonnet-4-5")
    assert is_anthropic_model("claude-haiku-4-5")
    assert is_anthropic_model("copilot/claude-sonnet")  # Copilot-proxied Anthropic
    # OpenRouter proxies Anthropic but does not honour our cache_control blocks.
    assert not is_anthropic_model("openrouter/anthropic/claude-sonnet-4-5")

    assert is_openai_model("openai/gpt-4o")
    assert is_openai_model("gpt-4o-mini")
    assert is_openai_model("o3-mini")
    assert not is_openai_model("openrouter/openai/gpt-4o")
    assert not is_openai_model("copilot/gpt-4o")  # proxied, treated separately

    # DeepSeek / Groq / Gemini are neither — automatic/no-op caching.
    assert not is_anthropic_model("deepseek/deepseek-chat")
    assert not is_openai_model("deepseek/deepseek-chat")
    assert not is_anthropic_model("groq/llama-3.3-70b-versatile")


# ── Sentinel handling (the safety invariant) ────────────────────────────────


def test_strip_sentinel_removes_marker_and_padding() -> None:
    text = f"{STABLE}\n{CACHE_BREAK}\n{DYNAMIC}"
    out = strip_sentinel(text)
    assert CACHE_BREAK not in out
    assert STABLE.strip() in out
    assert DYNAMIC.strip() in out
    # No dangling triple-newline where the sentinel was.
    assert "\n\n\n" not in out


def test_deepseek_strips_sentinel_no_blocks() -> None:
    msgs, tools, extra = apply_prompt_caching(
        model="deepseek/deepseek-chat",
        messages=[_sys_with_break(), {"role": "user", "content": "hi"}],
        tools=_tools(),
    )
    sys_msg = msgs[0]
    # Non-Anthropic: content stays a plain string, sentinel gone.
    assert isinstance(sys_msg["content"], str)
    assert CACHE_BREAK not in sys_msg["content"]
    # No cache_control added anywhere; tools untouched.
    assert tools == _tools()
    assert "prompt_cache_key" not in extra


def test_sentinel_never_reaches_model_any_provider() -> None:
    for model in (
        "anthropic/claude-sonnet-4-5",
        "openai/gpt-4o",
        "deepseek/deepseek-chat",
        "openrouter/anthropic/claude-sonnet-4-5",
        "groq/llama-3.3-70b-versatile",
    ):
        msgs, _tools_out, _extra = apply_prompt_caching(
            model=model,
            messages=[_sys_with_break(), {"role": "user", "content": "hi"}],
            tools=None,
        )
        blob = str(msgs)
        assert CACHE_BREAK not in blob, f"sentinel leaked for {model}"


# ── Anthropic: system split + tool marking ──────────────────────────────────


def test_anthropic_splits_system_into_cached_stable_plus_dynamic() -> None:
    msgs, _t, _e = apply_prompt_caching(
        model="anthropic/claude-sonnet-4-5",
        messages=[_sys_with_break(), {"role": "user", "content": "hi"}],
        tools=None,
    )
    blocks = msgs[0]["content"]
    assert isinstance(blocks, list)
    assert len(blocks) == 2
    stable_block, dynamic_block = blocks
    assert stable_block["cache_control"] == {"type": "ephemeral"}
    assert STABLE.strip() in stable_block["text"]
    assert CACHE_BREAK not in stable_block["text"]
    # Dynamic block is NOT cached (memory changes per turn).
    assert "cache_control" not in dynamic_block
    assert DYNAMIC.strip() in dynamic_block["text"]


def test_anthropic_no_sentinel_caches_whole_system() -> None:
    # An agent with no memory context sends a stable system prompt with no
    # sentinel — the whole thing should still become one cached block.
    msgs, _t, _e = apply_prompt_caching(
        model="claude-haiku-4-5",
        messages=[_sys(STABLE), {"role": "user", "content": "hi"}],
        tools=None,
    )
    blocks = msgs[0]["content"]
    assert isinstance(blocks, list) and len(blocks) == 1
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}
    assert STABLE.strip() in blocks[0]["text"]


def test_anthropic_marks_last_tool_only() -> None:
    tools = _tools(3)
    _m, out_tools, _e = apply_prompt_caching(
        model="anthropic/claude-sonnet-4-5",
        messages=[_sys(STABLE), {"role": "user", "content": "hi"}],
        tools=tools,
    )
    assert out_tools is not None
    # Only the LAST tool carries cache_control (caches the whole prefix).
    assert "cache_control" not in out_tools[0]["function"]
    assert "cache_control" not in out_tools[1]["function"]
    assert out_tools[-1]["function"]["cache_control"] == {"type": "ephemeral"}
    # Input list not mutated in place.
    assert "cache_control" not in tools[-1].get("function", {})


def test_anthropic_bare_tool_shape_marked_top_level() -> None:
    tools = [{"name": "raw_tool", "description": "x"}]
    _m, out_tools, _e = apply_prompt_caching(
        model="anthropic/claude-sonnet-4-5",
        messages=[_sys(STABLE)],
        tools=tools,
    )
    assert out_tools[-1]["cache_control"] == {"type": "ephemeral"}


# ── OpenAI: automatic + routing key ─────────────────────────────────────────


def test_openai_adds_prompt_cache_key_and_no_blocks() -> None:
    msgs, tools, extra = apply_prompt_caching(
        model="openai/gpt-4o",
        messages=[_sys_with_break(), {"role": "user", "content": "hi"}],
        tools=_tools(),
        cache_key="sales-agent",
    )
    # OpenAI caching is automatic — no cache_control blocks are injected.
    assert isinstance(msgs[0]["content"], str)
    assert CACHE_BREAK not in msgs[0]["content"]
    assert tools == _tools()  # tools not marked
    assert extra["prompt_cache_key"] == "sales-agent"


def test_openai_without_cache_key_is_noop_extra() -> None:
    _m, _t, extra = apply_prompt_caching(
        model="gpt-4o-mini",
        messages=[_sys(STABLE)],
        tools=None,
    )
    assert "prompt_cache_key" not in extra


# ── Stable-prefix headroom (≥1024 tokens is the Anthropic minimum) ───────────


def test_stable_block_preserves_full_prefix_no_truncation() -> None:
    # The Anthropic minimum cacheable prefix is 1,024 tokens; the real
    # Metorite stable prefix (instructions + tool addendum) is ~3.5k, well
    # above it. The transform must never DROP any of the stable content — the
    # cached block must contain the entire stable prefix verbatim, so whatever
    # headroom the real prefix has is preserved through the transform.
    big_stable = ("Detailed agent instruction line.\n" * 200).strip()
    msgs, _t, _e = apply_prompt_caching(
        model="anthropic/claude-sonnet-4-5",
        messages=[
            _sys(f"{big_stable}\n{CACHE_BREAK}\n{DYNAMIC}"),
            {"role": "user", "content": "hi"},
        ],
        tools=None,
    )
    stable_block = msgs[0]["content"][0]
    assert stable_block["text"] == big_stable  # verbatim, nothing dropped
    assert len(stable_block["text"]) >= 4096  # comfortably past the 1024-tok min
