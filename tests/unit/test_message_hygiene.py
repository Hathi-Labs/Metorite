"""Fence for task 1787: litellm's empty-text placeholder stays out of the chat.

litellm turns an empty ``content`` of a user or assistant turn into
``[System: Empty message content sanitised to satisfy protocol]``. The model
then copies it into its own replies. Three things keep it out:

* the list that goes to the model never holds ``content == ""``,
* the placeholder line is stripped from model output (stream and store),
* a stored turn that holds it is cleaned when it is read back as history.
"""
from __future__ import annotations

from types import SimpleNamespace

from acb_llm.message_hygiene import (
    EMPTY_TEXT_PLACEHOLDER,
    PlaceholderStreamFilter,
    clean_model_messages,
    strip_placeholder_lines,
)

TOOL_CALL = {
    "id": "c1", "type": "function",
    "function": {"name": "lookup", "arguments": "{}"},
}


def _all_contents(messages):
    for m in messages:
        c = m.get("content")
        if isinstance(c, list):
            for b in c:
                yield b.get("text") if isinstance(b, dict) else b
        else:
            yield c


# ── The constant ────────────────────────────────────────────────────────────

def test_the_constant_is_the_one_litellm_writes():
    from litellm.litellm_core_utils.prompt_templates import factory

    assert EMPTY_TEXT_PLACEHOLDER == factory._EMPTY_TEXT_PLACEHOLDER


# ── Outbound: no "" reaches the model ───────────────────────────────────────

def test_tool_call_only_assistant_turn_goes_out_with_content_none():
    for empty in ("", "   ", "\n", None, []):
        sent = clean_model_messages([
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": empty, "tool_calls": [TOOL_CALL]},
            {"role": "tool", "tool_call_id": "c1", "content": "ok"},
            {"role": "user", "content": "go on"},
        ])
        assistant = sent[1]
        assert assistant["content"] is None, empty
        assert assistant["tool_calls"] == [TOOL_CALL]
        assert not any(c == "" for c in _all_contents(sent))


def test_the_gateway_seam_sends_none_not_empty_string():
    from gateway.routes.v1_compat import _sanitize_messages_for_provider

    history = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "", "tool_calls": [TOOL_CALL]},
        {"role": "tool", "tool_call_id": "c1", "content": "ok"},
        {"role": "user", "content": "next"},
    ]
    # The direct path passes "<litellm provider> <model>" (see the call site).
    for provider in (
        "anthropic anthropic/claude-sonnet-4",
        "bedrock bedrock/anthropic.claude-3",
        "vertex_ai vertex_ai/claude-3-5-sonnet",
        "anthropic",
        "claude-sonnet-4",
    ):
        sent = _sanitize_messages_for_provider(history, provider)
        assert sent[1]["content"] is None, provider
        assert not any(c == "" for c in _all_contents(sent))


def test_an_unknown_or_empty_provider_keeps_empty_string():
    """The Router hop passes provider "". DeepSeek refuses null beside
    tool_calls, so the gateway must not guess None there."""
    from gateway.routes.v1_compat import (
        _router_outbound,
        _sanitize_messages_for_provider,
    )

    history = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": None, "tool_calls": [TOOL_CALL]},
    ]
    for provider in ("", "openai", "mystery"):
        assert _sanitize_messages_for_provider(history, provider)[1][
            "content"] == "", provider
    body = _router_outbound({"model": "tier-fast", "messages": history})
    assert body["messages"][1]["content"] == ""


def test_the_router_gives_each_step_the_empty_content_its_vendor_accepts():
    from customer_console.reasoning import content_for_step
    from litellm.litellm_core_utils.prompt_templates.factory import (
        _sanitize_empty_text_content,
    )

    wire = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "", "tool_calls": [TOOL_CALL]},
        {"role": "assistant", "content": None, "tool_calls": [TOOL_CALL]},
    ]
    for model in ("deepseek/deepseek-chat", "groq/llama", "together_ai/x"):
        sent = content_for_step(wire, model)
        assert sent[1]["content"] == "" and sent[2]["content"] == "", model
    for model in ("anthropic/claude-sonnet-4", "bedrock/anthropic.claude-3"):
        sent = content_for_step(wire, model)
        assert sent[1]["content"] is None and sent[2]["content"] is None
        for m in sent:
            assert _sanitize_empty_text_content(m) == m
    # An unrelated vendor and a plain text turn are left as they are.
    assert content_for_step(wire, "openai/gpt-4o") is wire
    # The input is not changed.
    assert wire[1]["content"] == ""


def test_a_failover_chain_applies_the_rule_for_the_model_it_really_calls():
    """main._kwargs_for builds each step's messages with messages_for_step."""

    from customer_console.reasoning import messages_for_step

    wire = [{"role": "user", "content": "hi"},
            {"role": "assistant", "content": None, "tool_calls": [TOOL_CALL]}]
    first = messages_for_step(wire, "deepseek/deepseek-chat")
    second = messages_for_step(wire, "anthropic/claude-haiku")
    third = messages_for_step(wire, "deepseek/deepseek-chat")
    assert first[1]["content"] == ""
    assert second[1]["content"] is None
    assert third[1]["content"] == ""
    # A step must never see the edit that the step before it made.
    assert wire[1]["content"] is None
    # The prefix decides, as the credential lookup does.
    assert messages_for_step(wire, "anthropic/deepseek-x")[1]["content"] is None
    assert messages_for_step(wire, "deepseek/claude-like")[1]["content"] == ""


def test_a_last_user_turn_of_empty_blocks_is_never_sent_as_empty_list():
    src = [
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": [{"type": "text", "text": ""}]},
    ]
    sent = clean_model_messages(src)
    assert sent[-1] is src[-1]
    assert sent[-1]["content"] != []


def test_a_provider_that_refuses_null_content_still_gets_empty_string():
    from gateway.routes.v1_compat import _sanitize_messages_for_provider

    sent = _sanitize_messages_for_provider(
        [{"role": "user", "content": "hi"},
         {"role": "assistant", "content": None, "tool_calls": [TOOL_CALL]}],
        "deepseek",
    )
    assert sent[1]["content"] == ""


def test_litellm_has_nothing_to_rewrite_in_what_we_send():
    from litellm.litellm_core_utils.prompt_templates.factory import (
        _sanitize_empty_text_content,
    )

    sent = clean_model_messages([
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "", "tool_calls": [TOOL_CALL]},
        {"role": "assistant", "content": [{"type": "text", "text": " "}]},
        {"role": "user", "content": "again"},
    ])
    for m in sent:
        assert _sanitize_empty_text_content(m) == m
    assert EMPTY_TEXT_PLACEHOLDER not in repr(sent)


def test_an_empty_text_block_is_dropped_not_sent():
    sent = clean_model_messages([
        {"role": "user", "content": [
            {"type": "text", "text": ""},
            {"type": "image_url", "image_url": {"url": "x"}},
            {"type": "text", "text": "look"},
        ]},
    ])
    blocks = sent[0]["content"]
    assert [b["type"] for b in blocks] == ["image_url", "text"]


def test_an_assistant_turn_with_nothing_is_dropped():
    sent = clean_model_messages([
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": ""},
        {"role": "user", "content": "hello?"},
    ])
    assert [m["role"] for m in sent] == ["user", "user"]


def test_clean_messages_come_back_equal_and_the_input_is_not_changed():
    src = [{"role": "user", "content": "hi"},
           {"role": "assistant", "content": "", "tool_calls": [TOOL_CALL]}]
    before = repr(src)
    clean_model_messages(src)
    assert repr(src) == before


# ── Output: the placeholder line is stripped ────────────────────────────────

def test_strip_removes_a_placeholder_line_and_keeps_the_rest():
    text = f"{EMPTY_TEXT_PLACEHOLDER}\n{EMPTY_TEXT_PLACEHOLDER}\nHere is the answer."
    assert strip_placeholder_lines(text) == "Here is the answer."
    odd = "[System: Blank content sanitised to satisfy protocol]\nDone."
    assert strip_placeholder_lines(odd) == "Done."


def test_a_mid_sentence_mention_is_left_alone():
    text = (
        f'litellm writes "{EMPTY_TEXT_PLACEHOLDER}" when content is empty.\n'
        f"Use {EMPTY_TEXT_PLACEHOLDER} as a search term."
    )
    assert strip_placeholder_lines(text) == text


def test_the_stream_filter_drops_the_line_across_any_chunking():
    text = f"{EMPTY_TEXT_PLACEHOLDER}\n{EMPTY_TEXT_PLACEHOLDER}\nReal reply [1] ok"
    for size in (1, 2, 3, 7, 200):
        f = PlaceholderStreamFilter()
        out = "".join(
            f.feed(text[i:i + size]) for i in range(0, len(text), size)
        ) + f.flush()
        assert out == "Real reply [1] ok", size


def test_the_stream_filter_does_not_delay_normal_text():
    f = PlaceholderStreamFilter()
    assert f.feed("Hello, ") == "Hello, "
    assert f.feed("world") == "world"
    # A bracket that is not the placeholder is released as soon as it is clear.
    assert f.feed("\n[System down] now") == "\n[System down] now"
    assert f.flush() == ""


def test_the_stream_filter_flushes_a_held_partial_line():
    f = PlaceholderStreamFilter()
    assert f.feed("[Sys") == ""
    assert f.flush() == "[Sys"


def _text_update(text, message_id="m1"):
    return SimpleNamespace(
        role="assistant", message_id=message_id, raw_representation=None,
        contents=[SimpleNamespace(type="text", text=text)],
    )


def test_streamed_text_and_stored_text_have_no_placeholder():
    from gateway.chat_fold import fold_run_events
    from orchestrator.event_translator import (
        TranslationState,
        close_text_message,
        translate_update,
    )

    state = TranslationState("run-1")
    events = [{"type": "RUN_STARTED", "runId": "run-1"}]
    pieces = [
        "[System: Empty message ", "content sanitised to satisfy protocol]\n",
        f"{EMPTY_TEXT_PLACEHOLDER}\nThe answer ", "is 42.",
    ]
    for piece in pieces:
        events += translate_update(_text_update(piece), state)
    events += close_text_message(state)
    events.append({"type": "RUN_FINISHED"})

    streamed = "".join(
        e["delta"] for e in events if e["type"] == "TEXT_MESSAGE_CONTENT"
    )
    assert "sanitised to satisfy protocol" not in streamed
    assert streamed == "The answer is 42."

    folded = fold_run_events(events)
    assert folded["content"] == "The answer is 42."


def test_the_fold_strips_a_placeholder_that_got_past_the_stream():
    from gateway.chat_fold import fold_run_events

    events = [
        {"type": "RUN_STARTED", "runId": "r"},
        {"type": "TEXT_MESSAGE_START", "messageId": "m", "role": "assistant"},
        {"type": "TEXT_MESSAGE_CONTENT", "messageId": "m",
         "delta": f"{EMPTY_TEXT_PLACEHOLDER}\nHello"},
        {"type": "TEXT_MESSAGE_END", "messageId": "m"},
        {"type": "RUN_FINISHED"},
    ]
    folded = fold_run_events(events)
    assert folded["content"] == "Hello"
    assert "sanitised" not in repr(folded["agent_state"])


# ── History: a stored turn that holds the placeholder is cleaned ────────────

def test_history_builder_drops_the_placeholder_from_a_stored_turn():
    from acb_llm.context import assemble_run_context

    out = assemble_run_context(
        history=[
            {"role": "user", "content": "what is it?"},
            {"role": "assistant",
             "content": f"{EMPTY_TEXT_PLACEHOLDER}\nIt is a cat."},
            {"role": "assistant", "content": EMPTY_TEXT_PLACEHOLDER},
        ],
        current_message="and now?",
    )
    assert EMPTY_TEXT_PLACEHOLDER not in repr(out)
    assert out[1] == {"role": "assistant", "content": "It is a cat."}
    assert [m["role"] for m in out] == ["user", "assistant", "user"]


def test_a_stored_tool_call_turn_with_a_placeholder_block_is_cleaned():
    """The fitter drops the placeholder block and keeps the tool calls."""
    from acb_llm.context import fit_messages_to_context

    src = [
        {"role": "user", "content": "find it"},
        {"role": "assistant", "tool_calls": [TOOL_CALL], "content": [
            {"type": "text", "text": EMPTY_TEXT_PLACEHOLDER},
        ]},
        {"role": "assistant", "content": f"{EMPTY_TEXT_PLACEHOLDER}\nFound it."},
        {"role": "user", "content": "thanks"},
    ]
    fitted, _ = fit_messages_to_context(src, "deepseek/deepseek-chat")
    assert EMPTY_TEXT_PLACEHOLDER not in repr(fitted)
    assert fitted[1]["tool_calls"] == [TOOL_CALL]
    assert fitted[2]["content"] == "Found it."


def test_the_fitter_leaves_the_empty_content_of_a_tool_call_turn_alone():
    """The gateway sets "" for DeepSeek and None for Anthropic. The fitter
    must not undo either (it runs after the sanitizer on the direct path)."""
    from acb_llm.context import fit_messages_to_context
    from gateway.routes.v1_compat import _sanitize_messages_for_provider

    history = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": None, "tool_calls": [TOOL_CALL]},
        {"role": "tool", "tool_call_id": "c1", "content": "ok"},
        {"role": "user", "content": "next"},
    ]
    cases = {
        "deepseek deepseek/deepseek-chat": "",
        "groq groq/llama": "",
        "anthropic anthropic/claude-sonnet-4": None,
        "bedrock bedrock/anthropic.claude-3": None,
    }
    for provider_and_model, want in cases.items():
        model = provider_and_model.split(" ", 1)[1]
        sent = _sanitize_messages_for_provider(history, provider_and_model)
        assert sent[1]["content"] == want, provider_and_model
        fitted, _ = fit_messages_to_context(sent, model)
        assert fitted[1]["content"] == want, provider_and_model


def test_a_placeholder_that_starts_a_line_is_removed_and_the_text_kept():
    text = f"{EMPTY_TEXT_PLACEHOLDER} Here is the answer.\nSecond line."
    assert strip_placeholder_lines(text) == "Here is the answer.\nSecond line."
    for size in (1, 3, 11, 500):
        f = PlaceholderStreamFilter()
        out = "".join(
            f.feed(text[i:i + size]) for i in range(0, len(text), size)
        ) + f.flush()
        assert out == "Here is the answer.\nSecond line.", size


def test_a_batch_result_never_carries_the_placeholder():
    """run_agent's answer, result and steps lose the placeholder, so the
    WhatsApp bot and the project agent sink never send it."""
    import asyncio

    from orchestrator import executor

    class Msg:
        role = "assistant"
        text = f"{EMPTY_TEXT_PLACEHOLDER}\nStep one."

    class Resp:
        text = f"{EMPTY_TEXT_PLACEHOLDER}\nThe answer."
        messages = (Msg(),)

    assert asyncio.iscoroutinefunction(executor.run_agent)
    out = executor._batch_result(Resp(), "run-1", "agent-x")
    assert out["answer"] == out["result"] == "The answer."
    assert out["steps"] == ["Step one."]


def test_the_string_history_block_drops_the_placeholder():
    from orchestrator.executor import _render_history_block

    block = _render_history_block(
        [{"role": "assistant",
          "content": f"{EMPTY_TEXT_PLACEHOLDER}\nHi there."}],
        "next", "",
    )
    assert "sanitised" not in block
    assert "Hi there." in block


def _router_kwargs_for():
    """The REAL `_kwargs_for` of the Router chat route, with no database.

    The function is a closure inside the route, so this compiles its own
    source (read from `main.py`) against the module globals plus fakes for the
    two names it closes over: `credentials` and `req`. Change what the route
    passes to `messages_for_step` and this test fails.
    """
    import ast
    import inspect
    from types import SimpleNamespace

    from customer_console import main

    tree = ast.parse(inspect.getsource(main))
    node = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "_kwargs_for"
        and "passthrough" in ast.unparse(n)
    )
    module = ast.Module(body=[node], type_ignores=[])
    ns: dict = dict(vars(main))
    cred = SimpleNamespace(secret="sk-test", api_base=None, byok=False)
    ns["credentials"] = {
        p: cred for p in ("deepseek", "anthropic", "groq", "bedrock")
    }
    exec(compile(module, "main.py::_kwargs_for", "exec"), ns)
    return ns["_kwargs_for"], ns


def test_the_router_route_sends_each_step_its_own_empty_content():
    """Drives the real `_kwargs_for` for a DeepSeek step then an Anthropic
    step. Needs no database."""
    from types import SimpleNamespace

    kwargs_for, ns = _router_kwargs_for()
    wire = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": None, "tool_calls": [TOOL_CALL]},
        {"role": "tool", "tool_call_id": "c1", "content": "ok"},
    ]
    ns["req"] = SimpleNamespace(
        model_dump=lambda **_kw: {"messages": wire, "max_tokens": 64},
    )
    deepseek = kwargs_for(SimpleNamespace(model="deepseek/deepseek-chat"))
    claude = kwargs_for(SimpleNamespace(model="anthropic/claude-haiku"))
    again = kwargs_for(SimpleNamespace(model="deepseek/deepseek-chat"))
    assert deepseek["messages"][1]["content"] == ""
    assert claude["messages"][1]["content"] is None
    assert again["messages"][1]["content"] == ""
    assert deepseek["model"] == "deepseek/deepseek-chat"
    # No step sees the edit of the step before it.
    assert wire[1]["content"] is None


def test_the_gateway_and_the_router_agree_on_the_empty_content():
    """The gateway (positive Anthropic test) and the console (deny list) are
    two codes for one rule. They must give the same content for every vendor.

    Local path: the gateway sets the value itself, from `<provider> <model>`.
    Router hop: the gateway sends "" and the console sets the value per step.
    """
    from customer_console.reasoning import content_for_step
    from gateway.routes.v1_compat import (
        _router_outbound,
        _sanitize_messages_for_provider,
    )

    samples = {
        "deepseek/deepseek-chat": "deepseek",
        "groq/x": "groq",
        "together_ai/x": "together_ai",
        "anthropic/claude-x": "anthropic",
        "bedrock/anthropic.claude-x": "bedrock",
        "vertex_ai/claude-x": "vertex_ai",
        "openai/gpt-x": "openai",
        "claude-x": "anthropic",
    }
    history = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": None, "tool_calls": [TOOL_CALL]},
    ]
    for model, provider in samples.items():
        local = _sanitize_messages_for_provider(
            history, f"{provider} {model}",
        )[1]["content"]
        hop = _router_outbound({"model": "tier-fast", "messages": history})
        assert hop["messages"][1]["content"] == ""
        routed = content_for_step(hop["messages"], model)[1]["content"]
        assert local == routed, (model, local, routed)
