"""The Router marks each chat step for its vendor's prompt cache. Task 2875.

Measured 2026-11-15 in ``usage_event``: since the chat moved to Anthropic on 2026-11-04, 0 of 1,766
Claude requests reported a cached token, and the orchestrator's prompt averages 47,000 tokens. The
gateway marks a request for the cache in ``acb_llm.prompt_cache`` only on the path that calls litellm
itself. A call that the Router serves leaves before that point, and only the Router knows the model of
the step that runs. ``customer_console.prompt_cache`` is the Router's copy of the rule.

R7 fences named here:
  * ``parity``: the copy and ``acb_llm.apply_prompt_caching`` give the same messages and tools on a
    table of requests, for every vendor. A change to one without the other turns this red.
  * ``off-is-identity``: with the flag off, the same dict comes back and the sentinel stays.
  * ``marks``: an Anthropic step gets the mark on the stable system block and the last tool, and the
    memory block after the break has none.
  * ``strips``: any other step loses the sentinel and gets no mark.
  * ``never-raises-never-mutates``: junk comes back as it came, and the input is never changed.
  * ``wired-once-per-step``: the chat route calls it once, inside ``_kwargs_for``.
  * ``no-acb-import``: the module names no ``acb_*`` package (the tenant boundary).

Mutation 1: drop the mark on the last tool. ``marks`` and ``parity`` turn red.
Mutation 2: mark a non-Anthropic step. ``strips`` and ``parity`` turn red.
Mutation 3: ignore the flag. ``off-is-identity`` turns red.
Mutation 4: remove the call from ``_kwargs_for``. ``wired-once-per-step`` turns red.
The door test for a mixed chain is ``test_customer_console_router.py::TestPromptCachePerStep`` (R8).
"""
from __future__ import annotations

import copy
import pathlib
import re

import pytest

pytest.importorskip("acb_llm")

from acb_llm import prompt_cache as gateway_side
from customer_console import prompt_cache as router_side
from customer_console.prompt_cache import CACHE_BREAK, prompt_cache_for_step

ROOT = pathlib.Path(__file__).resolve().parents[2]
CLAUDE = "anthropic/claude-haiku-5-5"
STABLE = "You are Mira. Stable persona and rules."
DYNAMIC = "Member memory: prefers short answers."
SYSTEM = f"{STABLE}\n\n{CACHE_BREAK}\n\n{DYNAMIC}"
TOOL_A = {"type": "function", "function": {"name": "find_priority", "parameters": {"type": "object"}}}
TOOL_B = {"type": "function", "function": {"name": "read_thread", "parameters": {"type": "object"}}}
BARE_TOOL = {"name": "bare", "description": "no function wrapper"}

MODELS = [
    CLAUDE,
    "anthropic/claude-sonnet-5-5",
    "claude-haiku-4-5",
    "openrouter/anthropic/claude-sonnet-4-5",
    "deepseek/deepseek-chat",
    "openai/gpt-4o-mini",
    "gemini/gemini-3.1-flash-lite",
]

MESSAGE_SETS = {
    "sentinel": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": "hi"}],
    "no_sentinel": [{"role": "system", "content": STABLE}, {"role": "user", "content": "hi"}],
    "no_system": [{"role": "user", "content": "hi"}],
    "blank_system": [{"role": "system", "content": "  "}, {"role": "user", "content": "hi"}],
    "block_system": [
        {"role": "system", "content": [{"type": "text", "text": STABLE}]},
        {"role": "user", "content": "hi"},
    ],
    "two_system": [
        {"role": "system", "content": SYSTEM},
        {"role": "system", "content": f"later {CACHE_BREAK} note"},
        {"role": "user", "content": "hi"},
    ],
    "sentinel_in_user": [{"role": "system", "content": STABLE}, {"role": "user", "content": f"a {CACHE_BREAK} b"}],
    "tool_turn": [
        {"role": "system", "content": SYSTEM},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "1", "type": "function", "function": {"name": "f", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "1", "content": "ok"},
    ],
}

TOOL_SETS = {"none": None, "empty": [], "one": [TOOL_A], "two": [TOOL_A, TOOL_B], "bare": [BARE_TOOL]}


@pytest.fixture(autouse=True)
def _flag_on(monkeypatch):
    monkeypatch.setenv("CUSTOMER_CONSOLE_PROMPT_CACHE", "1")
    router_side._LOGGED.clear()
    yield
    router_side._LOGGED.clear()


def _body(messages, tools=None):
    body = {"messages": copy.deepcopy(messages), "temperature": 0.2, "max_tokens": 64}
    if tools is not None:
        body["tools"] = copy.deepcopy(tools)
    return body


def test_the_sentinel_is_the_one_of_the_gateway():
    assert CACHE_BREAK == gateway_side.CACHE_BREAK


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("mset", sorted(MESSAGE_SETS))
@pytest.mark.parametrize("tset", sorted(TOOL_SETS))
def test_parity_with_the_gateway_rule(model, mset, tset):
    """The copy and the original give the same messages and tools."""
    messages, tools = MESSAGE_SETS[mset], TOOL_SETS[tset]
    want_messages, want_tools, _ = gateway_side.apply_prompt_caching(
        model=model, messages=copy.deepcopy(messages),
        tools=copy.deepcopy(tools), cache_key=None, extra={},
    )
    got = prompt_cache_for_step(_body(messages, tools), model)
    assert got["messages"] == want_messages
    assert got.get("tools") == want_tools


def test_off_is_identity(monkeypatch):
    monkeypatch.delenv("CUSTOMER_CONSOLE_PROMPT_CACHE", raising=False)
    body = _body(MESSAGE_SETS["sentinel"], [TOOL_A])
    assert prompt_cache_for_step(body, CLAUDE) is body
    assert CACHE_BREAK in body["messages"][0]["content"]
    for off in ("0", "false", "no", "", "off"):
        monkeypatch.setenv("CUSTOMER_CONSOLE_PROMPT_CACHE", off)
        assert prompt_cache_for_step(body, CLAUDE) is body


def test_an_anthropic_step_is_marked_on_the_stable_block_and_the_last_tool():
    body = _body(MESSAGE_SETS["sentinel"], [TOOL_A, TOOL_B])
    out = prompt_cache_for_step(body, CLAUDE)
    system = out["messages"][0]["content"]
    assert system == [
        {"type": "text", "text": STABLE, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": DYNAMIC},
    ]
    assert "cache_control" not in system[1]
    assert CACHE_BREAK not in str(out)
    assert "cache_control" not in out["tools"][0]["function"]
    assert out["tools"][-1]["function"]["cache_control"] == {"type": "ephemeral"}
    # The rest of the request is untouched.
    assert out["temperature"] == 0.2 and out["max_tokens"] == 64
    assert out["messages"][1] == {"role": "user", "content": "hi"}


def test_a_system_prompt_with_no_break_is_cached_whole():
    out = prompt_cache_for_step(_body(MESSAGE_SETS["no_sentinel"]), CLAUDE)
    assert out["messages"][0]["content"] == [
        {"type": "text", "text": STABLE, "cache_control": {"type": "ephemeral"}},
    ]


@pytest.mark.parametrize("model", [m for m in MODELS if not router_side.is_anthropic_model(m)])
def test_any_other_step_loses_the_sentinel_and_gets_no_mark(model):
    out = prompt_cache_for_step(_body(MESSAGE_SETS["sentinel"], [TOOL_A]), model)
    assert CACHE_BREAK not in str(out)
    assert "cache_control" not in str(out)
    assert out["messages"][0]["content"].startswith(STABLE)
    assert DYNAMIC in out["messages"][0]["content"]


def test_openrouter_claude_is_not_an_anthropic_step():
    assert not router_side.is_anthropic_model("openrouter/anthropic/claude-sonnet-4-5")
    assert router_side.is_anthropic_model("anthropic/claude-haiku-5-5")
    assert router_side.is_anthropic_model("claude-haiku-4-5")


def test_nothing_to_change_gives_the_same_dict():
    body = _body(MESSAGE_SETS["no_system"])
    assert prompt_cache_for_step(body, CLAUDE) is body
    block = _body(MESSAGE_SETS["block_system"])
    assert prompt_cache_for_step(block, CLAUDE) is block
    plain = _body(MESSAGE_SETS["no_sentinel"])
    assert prompt_cache_for_step(plain, "deepseek/deepseek-chat") is plain


def test_it_never_changes_its_input():
    body = _body(MESSAGE_SETS["sentinel"], [TOOL_A, TOOL_B])
    before = copy.deepcopy(body)
    out = prompt_cache_for_step(body, CLAUDE)
    assert body == before
    assert out is not body


@pytest.mark.parametrize("junk", [
    {}, {"messages": None}, {"messages": "x"}, {"messages": [None, 3, "x"]},
    {"messages": [{"role": "system", "content": None}]},
    {"messages": [{"role": "system"}], "tools": "x"},
    {"messages": [{"role": "system", "content": "a"}], "tools": [None]},
    {"messages": [{"role": "system", "content": "a"}], "tools": [3, "x"]},
])
def test_junk_comes_back_as_it_came(junk):
    before = copy.deepcopy(junk)
    out = prompt_cache_for_step(junk, CLAUDE)
    assert junk == before
    assert isinstance(out, dict)


def test_a_bad_model_comes_back_as_it_came():
    body = _body(MESSAGE_SETS["sentinel"])
    assert prompt_cache_for_step(body, None) is body  # type: ignore[arg-type]


def test_the_log_names_the_model_and_never_the_text(caplog):
    with caplog.at_level("INFO", logger="customer_console.prompt_cache"):
        prompt_cache_for_step(_body(MESSAGE_SETS["sentinel"], [TOOL_A]), CLAUDE)
        prompt_cache_for_step(_body(MESSAGE_SETS["sentinel"], [TOOL_A]), CLAUDE)
    lines = [r for r in caplog.records if r.getMessage() == "router.prompt_cache"]
    assert len(lines) == 1
    assert lines[0].pc_model == CLAUDE and lines[0].pc_outcome == "marked"
    assert STABLE not in caplog.text and DYNAMIC not in caplog.text


def test_litellm_carries_the_marks_to_the_anthropic_payload():
    """Fails LOUD if a litellm upgrade stops carrying ``cache_control`` to the vendor body.

    Measured 2026-11-15 on the pinned litellm: the system block keeps its mark, the dynamic block
    has none, and the last tool keeps its mark. The Router runs litellm with no ``drop_params``.
    """
    pytest.importorskip("litellm")
    from litellm.llms.anthropic.chat.transformation import AnthropicConfig

    tool = {"type": "function", "function": {"description": "d", "parameters": {"type": "object", "properties": {}}}}
    tools = [{**tool, "function": {**tool["function"], "name": "a"}}, {**tool, "function": {**tool["function"], "name": "b"}}]
    out = prompt_cache_for_step(_body(MESSAGE_SETS["sentinel"], tools), CLAUDE)
    cfg = AnthropicConfig()
    optional = cfg.map_openai_params({"tools": out["tools"]}, {}, "claude-haiku-5-5", False)
    sent = cfg.transform_request("claude-haiku-5-5", out["messages"], optional, {}, {})
    assert sent["system"] == [
        {"type": "text", "text": STABLE, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": DYNAMIC},
    ]
    assert [t.get("cache_control") for t in sent["tools"]] == [None, {"type": "ephemeral"}]


def test_litellm_reports_cached_tokens_inside_the_prompt_count():
    """The billing code reads cached tokens as a SUBSET of the prompt count (`credits.py`).

    For Anthropic, litellm adds the cache reads to ``prompt_tokens`` and also reports them as
    ``cache_read_input_tokens``, so ``cached <= prompt`` holds and no ``UsagePartitionError`` comes
    from turning the cache on. A cache WRITE counts as plain prompt tokens.
    """
    pytest.importorskip("litellm")
    from litellm.llms.anthropic.chat.transformation import AnthropicConfig

    cfg = AnthropicConfig()
    read = cfg.calculate_usage(
        usage_object={"input_tokens": 200, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 47000, "output_tokens": 50},
        reasoning_content=None,
    )
    assert read.prompt_tokens == 47200 and read.cache_read_input_tokens == 47000
    assert read.cache_read_input_tokens <= read.prompt_tokens
    write = cfg.calculate_usage(
        usage_object={"input_tokens": 30, "cache_creation_input_tokens": 46000, "cache_read_input_tokens": 0, "output_tokens": 50},
        reasoning_content=None,
    )
    assert write.prompt_tokens == 46030 and write.cache_read_input_tokens == 0


def test_wired_once_per_step():
    main = (ROOT / "apps/services/customer_console/customer_console/main.py").read_text(encoding="utf-8")
    assert main.count("prompt_cache_for_step(") == 1
    start = main.index("def _kwargs_for(step: ResolvedTier) -> dict[str, Any]:")
    body = main[start:main.index("def _byok_served", start)]
    assert "prompt_cache_for_step(passthrough, step.model)" in body
    # After the content repair of the step, and before the params that a model refuses.
    assert body.index("messages_for_step(") < body.index("prompt_cache_for_step(") < body.index("reasoning_params_for(")


def test_the_module_names_no_acb_package():
    src = (ROOT / "apps/services/customer_console/customer_console/prompt_cache.py").read_text(encoding="utf-8")
    code = "\n".join(line for line in src.splitlines() if not line.lstrip().startswith(("#", '"', "*")))
    assert not re.search(r"^\s*(from|import)\s+acb_", code, re.M)
