"""Golden trajectories: the ONE event translator (core_loop_unification Ph. 2).

All four executor stream paths (native MAF, Copilot, Tier 2 batch, sub-agent)
now translate through ``orchestrator.event_translator``. These trajectories
lock the canonical mapping and the cross-tier contract: identical runtime
updates produce identical AG-UI events on every path — per-tier behaviour may
only enter through TranslatorHooks, never through divergent mapping copies.
"""
from __future__ import annotations

from types import SimpleNamespace

from orchestrator.event_translator import (
    TranslationState,
    TranslatorHooks,
    close_text_message,
    text_message_events,
    translate_update,
    wrap_sub_agent_events,
)


def _content(ctype: str, **kw) -> SimpleNamespace:
    return SimpleNamespace(type=ctype, **kw)


def _update(*contents, role=None, message_id=None, raw=None) -> SimpleNamespace:
    return SimpleNamespace(
        contents=list(contents), role=role,
        message_id=message_id, raw_representation=raw,
    )


def _drive(updates, state=None, hooks=None):
    state = state or TranslationState("run1")
    events: list[dict] = []
    for u in updates:
        events.extend(translate_update(u, state, hooks))
    return events, state


# ── Canonical mapping ────────────────────────────────────────────────────────

def test_full_turn_canonical_stream():
    """Text + reasoning + streamed tool call + result → the canonical
    message-id-native stream, identical for every consuming path."""
    updates = [
        _update(_content("text_reasoning", text="Planning the lookup.")),
        _update(_content("text", text="Let me check."), message_id="m-1"),
        _update(_content("function_call", call_id="c1", name="query_inbox",
                         arguments='{"account":')),
        _update(_content("function_call", call_id="", name=None,
                         arguments='"a1"}')),
        _update(_content("function_result", call_id="c1",
                         result="3 messages", exception=None)),
        _update(_content("text", text=" Found 3."), message_id="m-1"),
    ]
    events, state = _drive(updates)
    events.extend(close_text_message(state))

    # Phase 3c: the tool call closes the pre-tool segment; the post-tool text
    # opens a fresh one (the runtime reused m-1, so a new id is minted). Two
    # tool-round-aligned segments: narration "Let me check." then answer
    # " Found 3.".
    assert [e["type"] for e in events] == [
        "THINKING_TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_START", "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END",       # closed by the tool boundary (3c)
        "TOOL_CALL_START", "TOOL_CALL_ARGS", "TOOL_CALL_ARGS",
        "TOOL_CALL_RESULT",
        "TEXT_MESSAGE_START", "TEXT_MESSAGE_CONTENT",  # answer segment
        "TEXT_MESSAGE_END",
    ]
    # First segment keeps the runtime's id; the reused id is forced fresh for
    # the second so the two segments never collide.
    seg_ids = [
        e["messageId"] for e in events if e["type"] == "TEXT_MESSAGE_START"
    ]
    assert len(seg_ids) == 2 and seg_ids[0] == "m-1" and seg_ids[1] != "m-1"
    # Streamed args concatenate onto ONE tool row.
    args = "".join(
        e["delta"] for e in events if e["type"] == "TOOL_CALL_ARGS"
    )
    assert args == '{"account":"a1"}'
    starts = [e for e in events if e["type"] == "TOOL_CALL_START"]
    assert len(starts) == 1 and starts[0]["toolCallId"] == "c1"
    result = next(e for e in events if e["type"] == "TOOL_CALL_RESULT")
    assert result["success"] is True and result["content"] == "3 messages"


def test_failed_tool_and_result_cap():
    events, _ = _drive([
        _update(_content("function_call", call_id="c1", name="fetch_page",
                         arguments='{"url": "https://x"}')),
        _update(_content("function_result", call_id="c1",
                         result="y" * 5000, exception=RuntimeError("boom"))),
    ])
    result = next(e for e in events if e["type"] == "TOOL_CALL_RESULT")
    assert result["success"] is False
    assert result["content"] == "boom"  # exception wins over result payload
    ok_events, _ = _drive([
        _update(_content("function_result", call_id="c2",
                         result="z" * 5000, exception=None)),
    ])
    assert len(ok_events[0]["content"]) == 2000  # RESULT_CAP


def test_tool_role_text_routes_to_partial_or_progress():
    """Tool-role frames go to the thinking timeline, never the message —
    with a tool_call_id they stream INTO that tool's row."""
    raw_partial = SimpleNamespace(
        type="PARTIAL_RESULT", data=SimpleNamespace(tool_call_id="c9"),
    )
    events, state = _drive([
        _update(_content("text", text="[progress] cloning repo"), role="tool"),
        _update(_content("text", text="chunk of terminal output"),
                role="tool", raw=raw_partial),
        _update(_content("text", text="The real answer."), message_id="m-2"),
    ])
    assert [e["type"] for e in events] == [
        "PROGRESS_UPDATE", "TOOL_CALL_PARTIAL",
        "TEXT_MESSAGE_START", "TEXT_MESSAGE_CONTENT",
    ]
    assert events[0]["message"] == "cloning repo"  # [progress] prefix stripped
    assert events[1]["toolCallId"] == "c9"
    assert state.text_started  # tool-role frames never start the message


def test_intent_raw_event_becomes_intent_row():
    raw = SimpleNamespace(
        type="AGENT_INTENT", data=SimpleNamespace(intent="searching the web"),
    )
    events, _ = _drive([_update(role=None, raw=raw)])
    assert events == [{
        "type": "TOOL_CALL_START",
        "toolCallId": "run1:intent:searching the web",
        "toolCallName": "searching the web",
    }]


# ── Cross-tier parity ────────────────────────────────────────────────────────

def test_same_updates_translate_identically_on_all_paths():
    """The contract that kills the drift bug class: two independent consumers
    of the same update sequence see byte-identical canonical streams."""
    def updates():
        return [
            _update(_content("text", text="Hello"), message_id="m-1"),
            _update(_content("function_call", call_id="c1", name="t",
                             arguments='{"x":1}')),
            _update(_content("function_result", call_id="c1", result="ok",
                             exception=None)),
        ]

    path_a, _ = _drive(updates())   # e.g. native MAF loop
    path_b, _ = _drive(updates())   # e.g. Copilot loop (no hooks wired)
    assert path_a == path_b


def test_hooks_are_the_only_tier_divergence():
    """Copilot extras (TODO_LIST/elicitation) enter ONLY via hooks: extras are
    emitted before the TOOL_CALL_START, and results notify the cleanup hook."""
    seen_results: list[str] = []
    hooks = TranslatorHooks(
        extra_function_call_events=lambda tc_id, name, args, args_str: [
            {"type": "TODO_LIST", "todos": [], "_for": name},
        ],
        on_function_result=seen_results.append,
    )
    events, state = _drive([
        _update(_content("function_call", call_id="c1",
                         name="manage_todo_list", arguments='{"todoList":[]}')),
        _update(_content("function_result", call_id="c1", result="ok",
                         exception=None)),
    ], hooks=hooks)
    assert [e["type"] for e in events] == [
        "TODO_LIST", "TOOL_CALL_START", "TOOL_CALL_ARGS", "TOOL_CALL_RESULT",
    ]
    assert seen_results == ["c1"]
    # A re-sent id within the SAME run is deduped — no new row, and the
    # extras hook must not re-fire either.
    events2, _ = _drive([
        _update(_content("function_call", call_id="c1", name="manage_todo_list",
                         arguments='{"todoList":[]}')),
    ], state=state, hooks=hooks)
    assert events2 == []


# ── Tool-round-aligned segmentation (Phase 3a id change + Phase 3c tool close) ─

def test_tool_call_closes_the_open_text_segment():
    """Phase 3c: a tool call closes the open text message BEFORE the tool
    events, so pre-tool narration and post-tool answer are distinct segments —
    tool-round-aligned regardless of what the runtime does with message_id."""
    events, state = _drive([
        _update(_content("text", text="Let me check."), message_id="m-1"),
        _update(_content("function_call", call_id="c1", name="t",
                         arguments="")),
        _update(_content("function_result", call_id="c1", result="ok",
                         exception=None)),
        _update(_content("text", text="All done."), message_id="m-2"),
    ])
    events.extend(close_text_message(state))

    assert [e["type"] for e in events] == [
        "TEXT_MESSAGE_START", "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END",       # m-1 closed BY THE TOOL BOUNDARY (3c)
        "TOOL_CALL_START",
        "TOOL_CALL_RESULT",
        "TEXT_MESSAGE_START",     # m-2 opens after the tool
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END",
    ]
    ends = [e["messageId"] for e in events if e["type"] == "TEXT_MESSAGE_END"]
    assert ends == ["m-1", "m-2"]


def test_tool_close_forces_fresh_id_when_runtime_reuses_it():
    """Phase 3c: if the runtime reuses the SAME message_id for post-tool text,
    the reopen mints a FRESH id — distinct segments need distinct ids (AG-UI;
    the client dedups by id). Otherwise narration+answer would collide."""
    events, state = _drive([
        _update(_content("text", text="Let me check."), message_id="m-1"),
        _update(_content("function_call", call_id="c1", name="t",
                         arguments="")),
        _update(_content("function_result", call_id="c1", result="ok",
                         exception=None)),
        _update(_content("text", text="All done."), message_id="m-1"),  # REUSED
    ])
    events.extend(close_text_message(state))
    starts = [e["messageId"] for e in events if e["type"] == "TEXT_MESSAGE_START"]
    assert len(starts) == 2
    assert starts[0] == "m-1"
    assert starts[1] != "m-1"  # forced fresh — no id collision across segments


def test_id_less_run_still_splits_on_the_tool_boundary():
    """Id-less runtimes (Copilot may omit message_id) STILL segment correctly:
    the tool boundary splits narration from answer even with no ids at all —
    the exact bug behind the 'improper thinking container' report."""
    events, state = _drive([
        _update(_content("text", text="Let me check.")),          # no id
        _update(_content("function_call", call_id="c1", name="t",
                         arguments="")),
        _update(_content("function_result", call_id="c1", result="ok",
                         exception=None)),
        _update(_content("text", text="All done.")),              # no id
    ])
    events.extend(close_text_message(state))
    starts = [e for e in events if e["type"] == "TEXT_MESSAGE_START"]
    # Two tool-round-aligned segments even though the runtime sent NO ids.
    assert len(starts) == 2


def test_pure_text_run_stays_one_segment():
    """No tool call → no split: a plain text answer is one segment."""
    events, state = _drive([
        _update(_content("text", text="a")),
        _update(_content("text", text="b")),
    ])
    events.extend(close_text_message(state))
    starts = [e for e in events if e["type"] == "TEXT_MESSAGE_START"]
    assert len(starts) == 1


# ── Sub-agent envelope ───────────────────────────────────────────────────────

def test_sub_agent_wrap_maps_and_drops_correctly():
    state = TranslationState("sub1")
    events = []
    for u in [
        _update(_content("text_reasoning", text="internal thought")),
        _update(_content("text", text="Working on it."), message_id="m-9"),
        _update(_content("function_call", call_id="c1", name="sql",
                         arguments='{"q":"select 1"}')),
        _update(_content("function_result", call_id="c1", result="1 row",
                         exception=None)),
    ]:
        events.extend(translate_update(u, state))
    wrapped = wrap_sub_agent_events(events, agent_name="task-manager",
                                    run_id="sub1")

    assert [e["type"] for e in wrapped] == [
        "SUB_AGENT_TEXT_DELTA",
        "SUB_AGENT_TOOL_CALL_START",
        "SUB_AGENT_TOOL_CALL_RESULT",
    ]
    assert all(e["agentName"] == "task-manager" for e in wrapped)
    assert wrapped[1]["toolCallName"] == "sql"
    assert wrapped[1]["args"] == '{"q":"select 1"}'
    assert wrapped[2]["success"] is True and wrapped[2]["content"] == "1 row"


# ── Tier 2 batch protocol ────────────────────────────────────────────────────

def test_tier2_text_stream_speaks_message_id_protocol():
    text = "The quick brown fox jumps over the lazy dog"
    events = text_message_events(text, message_id="m-t2")
    assert events[0] == {
        "type": "TEXT_MESSAGE_START", "messageId": "m-t2", "role": "assistant",
    }
    assert events[-1] == {"type": "TEXT_MESSAGE_END", "messageId": "m-t2"}
    body = [e for e in events[1:-1]]
    assert all(e["type"] == "TEXT_MESSAGE_CONTENT" and e["messageId"] == "m-t2"
               for e in body)
    # Chunked deltas reassemble to the original text exactly.
    assert "".join(e["delta"] for e in body).strip() == text


# ── MAF 1.19: the provider id rides on EVERY argument chunk ─────────────────

def test_maf_119_streamed_args_reach_the_trail():
    """MAF 1.19 repeats the provider call id on every streamed chunk. A known
    id is then the next argument fragment, not a re-send. Dropping it sent no
    arguments at all, so the trail drew a script step with no command."""
    events, _ = _drive([
        _update(_content("function_call", call_id="c1", name="run_command",
                         arguments="")),
        _update(_content("function_call", call_id="c1", name="",
                         arguments='{"command": "python ')),
        _update(_content("function_call", call_id="c1", name="",
                         arguments='make.py"}')),
        _update(_content("function_result", call_id="c1", result="ok",
                         exception=None)),
    ])
    assert [e["type"] for e in events] == [
        "TOOL_CALL_START", "TOOL_CALL_ARGS", "TOOL_CALL_ARGS", "TOOL_CALL_RESULT",
    ]
    args = "".join(e["delta"] for e in events if e["type"] == "TOOL_CALL_ARGS")
    assert args == '{"command": "python make.py"}'


def test_parallel_calls_keep_their_own_args():
    """Two calls whose fragments interleave each keep their own arguments."""
    events, _ = _drive([
        _update(_content("function_call", call_id="a", name="read_file", arguments="")),
        _update(_content("function_call", call_id="b", name="read_file", arguments="")),
        _update(_content("function_call", call_id="a", name="", arguments='{"path": "x.md"')),
        _update(_content("function_call", call_id="b", name="", arguments='{"path": "y.md"}')),
        _update(_content("function_call", call_id="a", name="", arguments="}")),
    ])
    by_id: dict[str, str] = {}
    for e in events:
        if e["type"] == "TOOL_CALL_ARGS":
            by_id[e["toolCallId"]] = by_id.get(e["toolCallId"], "") + e["delta"]
    assert by_id == {"a": '{"path": "x.md"}', "b": '{"path": "y.md"}'}
    assert [e["type"] for e in events].count("TOOL_CALL_START") == 2


def test_complete_call_sent_again_after_its_fragments_adds_nothing():
    """Once the arguments are a whole object, the same id adds no row and no
    second copy of the arguments."""
    events, state = _drive([
        _update(_content("function_call", call_id="c1", name="create_task", arguments="")),
        _update(_content("function_call", call_id="c1", name="", arguments='{"title": "T"}')),
    ])
    again, _ = _drive([
        _update(_content("function_call", call_id="c1", name="create_task",
                         arguments='{"title": "T"}')),
    ], state=state)
    assert again == []
