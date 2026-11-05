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
    for provider in ("anthropic", "claude-sonnet-4", "bedrock/anthropic.claude"):
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
    """main._kwargs_for runs content_for_step once per step."""
    import inspect

    from customer_console import main
    from customer_console.reasoning import content_for_step

    assert "content_for_step(" in inspect.getsource(main)
    wire = [{"role": "assistant", "content": None, "tool_calls": [TOOL_CALL]}]
    first = content_for_step(wire, "deepseek/deepseek-chat")
    second = content_for_step(wire, "anthropic/claude-haiku")
    assert first[0]["content"] == ""
    assert second[0]["content"] is None
    # A step must never see the edit that the step before it made.
    assert wire[0]["content"] is None


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


def test_history_builder_sends_no_empty_content_for_a_tool_call_turn():
    from acb_llm.context import assemble_run_context

    out = assemble_run_context(
        history=[
            {"role": "user", "content": "find it"},
            {"role": "assistant", "content": "", "tool_calls": [TOOL_CALL]},
            {"role": "assistant", "content": "Found it."},
        ],
        current_message="thanks",
    )
    assert not any(m.get("content") == "" for m in out)
    cleaned = clean_model_messages(out)
    assert not any(c == "" for c in _all_contents(cleaned))


def test_the_string_history_block_drops_the_placeholder():
    from orchestrator.executor import _render_history_block

    block = _render_history_block(
        [{"role": "assistant",
          "content": f"{EMPTY_TEXT_PLACEHOLDER}\nHi there."}],
        "next", "",
    )
    assert "sanitised" not in block
    assert "Hi there." in block
