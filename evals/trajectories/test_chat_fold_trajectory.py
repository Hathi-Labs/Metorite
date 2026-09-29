"""Golden trajectories: server-side fold + authoritative persistence (P0-3).

``gateway.chat_fold`` must stay event-for-event equivalent to the Next
translator's fold (route.ts) and the client reducer (chatStream.ts) until the
protocol goes message-id-native (core_loop_unification Phase 3). These
trajectories are that contract: a synthetic run event log in, the persisted
message shape out.
"""
from __future__ import annotations

import asyncio
import json

from gateway.chat_fold import (
    build_extraction_conversation,
    fold_run_events,
    group_reasoning_blocks,
    persist_final_assistant_message,
    unfold_trailing_answer,
)
from orchestrator import stream_relay

#: The run's tenant. WS-27bm S15 (#531) made `organization_id` a required
#: keyword of `persist_final_assistant_message`. A call without it raised
#: TypeError inside the relay's `on_complete`, which the relay swallows, so
#: these trajectories persisted nothing and failed as "0 == 1".
TRAJ_ORG = "00000000-0000-0000-0000-00000000a001"

def _ev(t: str, ms: int, **kw) -> dict:
    return {"type": t, "_stream_id": f"{ms}-0", **kw}


def test_full_turn_fold_with_failed_tool():
    """Narration → tool (fails) → reasoning → answer: narration folds into
    the timeline, the failed tool persists as error, the answer survives."""
    events = [
        _ev("TEXT_MESSAGE_CONTENT", 1000, delta="Let me check the inbox."),
        _ev("TOOL_CALL_START", 1100, toolCallId="t1", toolCallName="query_inbox"),
        _ev("TOOL_CALL_ARGS", 1150, toolCallId="t1", delta='{"account_id":'),
        _ev("TOOL_CALL_ARGS", 1160, toolCallId="t1", delta='"a1"}'),
        _ev("REASONING_MESSAGE_CONTENT", 1200, delta="The query failed, retrying differently."),
        _ev("TOOL_CALL_RESULT", 1300, toolCallId="t1",
            content="timeout contacting provider", success=False),
        _ev("TEXT_MESSAGE_CONTENT", 1400, delta="I couldn't reach your inbox."),
        _ev("RUN_FINISHED", 1500),
    ]
    folded = fold_run_events(events)
    assert folded is not None
    assert folded["content"] == "I couldn't reach your inbox."
    assert folded["timestamp"] == 1500

    [tool] = folded["tool_events"]
    assert tool["name"] == "query_inbox"
    assert tool["args"] == {"account_id": "a1"}
    assert tool["status"] == "error"  # honours success=False (P0-5)
    assert tool["startedAt"] == 1100
    assert tool["endedAt"] == 1300

    blocks = json.loads(folded["reasoning"])
    # Narration folded at tool start; reasoning followed in a later block.
    assert blocks[0] == "Let me check the inbox."
    assert any("retrying differently" in b for b in blocks)
    assert tool["reasoningCutoff"] == 1


def test_turn_ending_on_tool_unfolds_answer():
    """When the run ends on a tool call, the folded answer is promoted back
    to content and its block blanked (indices stay aligned)."""
    events = [
        _ev("TEXT_MESSAGE_CONTENT", 1000, delta="Here is your summary: all good."),
        _ev("TOOL_CALL_START", 1100, toolCallId="t1", toolCallName="save_memory"),
        _ev("TOOL_CALL_RESULT", 1200, toolCallId="t1", content="saved", success=True),
        _ev("RUN_FINISHED", 1300),
    ]
    folded = fold_run_events(events)
    assert folded is not None
    assert folded["content"] == "Here is your summary: all good."
    blocks = json.loads(folded["reasoning"])
    assert blocks[0] == ""  # blanked sentinel — cutoff indices stay aligned
    assert folded["tool_events"][0]["status"] == "done"


def test_sub_agent_timeline_attaches_to_delegate_tool():
    events = [
        _ev("TOOL_CALL_START", 1000, toolCallId="d1", toolCallName="call_agent"),
        _ev("SUB_AGENT_TEXT_DELTA", 1100, agentName="task-manager", delta="Checking tasks."),
        _ev("SUB_AGENT_TOOL_CALL_START", 1200, toolCallId="s1", toolCallName="sql"),
        _ev("SUB_AGENT_TOOL_CALL_RESULT", 1300, toolCallId="s1", content="3 rows", success=True),
        _ev("TOOL_CALL_RESULT", 1400, toolCallId="d1", content="3 overdue tasks", success=True),
        _ev("TEXT_MESSAGE_CONTENT", 1500, delta="You have 3 overdue tasks."),
        _ev("RUN_FINISHED", 1600),
    ]
    folded = fold_run_events(events)
    assert folded is not None
    [tool] = folded["tool_events"]
    assert tool["subAgentName"] == "task-manager"
    assert tool["subAgentText"] == "Checking tasks."
    assert tool["subAgentTools"] == [
        {"id": "s1", "name": "sql", "status": "done", "result": "3 rows"},
    ]


def test_todos_and_custom_events_persist():
    todos = [{"id": "1", "title": "Plan", "status": "completed"}]
    events = [
        _ev("TODO_LIST", 1000, todos=todos),
        _ev("CUSTOM", 1100, name="artifact_created", value={"path": "outputs/r.pdf"}),
        _ev("TEXT_MESSAGE_CONTENT", 1200, delta="Report written."),
        _ev("RUN_FINISHED", 1300),
    ]
    folded = fold_run_events(events)
    assert folded is not None
    assert folded["agent_state"] == {"todos": todos}
    assert folded["custom_events"] == [
        {"name": "artifact_created", "value": {"path": "outputs/r.pdf"}},
    ]


def test_segment_ground_truth_rescues_cancelled_run_answer():
    """Phase 3a: the un-fold heuristic only runs at RUN_FINISHED — a run
    cancelled mid-tool (Stop/steer, then persisted by the on_complete hook)
    has no terminal event, so its answer stayed stranded in the timeline and
    the row persisted an empty bubble. With real segment ids the LAST
    segment is the answer by ground truth, terminal event or not."""
    events = [
        _ev("TEXT_MESSAGE_START", 900, messageId="m-1"),
        _ev("TEXT_MESSAGE_CONTENT", 1000, delta="Here is the summary.",
            messageId="m-1"),
        _ev("TOOL_CALL_START", 1100, toolCallId="t1", toolCallName="save_memory"),
        # Cancelled here: no TOOL_CALL_RESULT, no RUN_FINISHED.
    ]
    folded = fold_run_events(events)
    assert folded is not None
    # Without segments this persisted content="" (answer folded, never
    # un-folded); ground truth wins.
    assert folded["content"] == "Here is the summary."
    assert folded["agent_state"]["segments"] == [
        {"id": "m-1", "text": "Here is the summary."},
    ]
    # Phase 3b: with real segment ids the narration is NOT double-folded into
    # reasoning_blocks (it lives only in the segment), so this run has no
    # reasoning at all — the answer is rescued straight from the last segment.
    # (The tool was cancelled before its RESULT, so no tool event persists.)
    assert folded["reasoning"] is None
    assert folded["tool_events"] == []


def test_segments_persist_alongside_todos():
    todos = [{"id": "1", "title": "Plan", "status": "completed"}]
    events = [
        _ev("TODO_LIST", 900, todos=todos),
        _ev("TEXT_MESSAGE_START", 950, messageId="m-1"),
        _ev("TEXT_MESSAGE_CONTENT", 1000, delta="Done.", messageId="m-1"),
        _ev("RUN_FINISHED", 1100),
    ]
    folded = fold_run_events(events)
    assert folded is not None
    assert folded["agent_state"] == {
        "todos": todos,
        "segments": [{"id": "m-1", "text": "Done."}],
    }


def test_segment_native_all_text_is_body_vscode_style():
    """Phase 3c (VS Code parity): EVERY assistant text segment is answer body,
    including text emitted BEFORE a tool call. `content` is the full answer =
    all non-empty segments joined; genuine chain-of-thought stays in
    reasoning_blocks; assistant text is never double-folded there. Each tool
    still records segmentCutoff = segment count at its start (for interleaving
    the tool card between the body segments on render)."""
    events = [
        _ev("TEXT_MESSAGE_START", 900, messageId="m-1"),
        _ev("TEXT_MESSAGE_CONTENT", 1000, delta="Let me check the inbox.",
            messageId="m-1"),
        _ev("TOOL_CALL_START", 1100, toolCallId="t1", toolCallName="query_inbox"),
        _ev("REASONING_MESSAGE_CONTENT", 1150, delta="The provider was slow."),
        _ev("TOOL_CALL_RESULT", 1200, toolCallId="t1", content="ok", success=True),
        _ev("TEXT_MESSAGE_START", 1250, messageId="m-2"),
        _ev("TEXT_MESSAGE_CONTENT", 1300, delta="You have 2 new emails.",
            messageId="m-2"),
        _ev("RUN_FINISHED", 1400),
    ]
    folded = fold_run_events(events)
    assert folded is not None
    # BOTH assistant text segments are the answer body — the pre-tool text is
    # answer content, not thinking-pane narration (the startup-guru fix).
    assert folded["content"] == "Let me check the inbox.\n\nYou have 2 new emails."
    # Both real segments preserved in order (renderer reads these for inline
    # display; content is the flat mirror).
    assert folded["agent_state"]["segments"] == [
        {"id": "m-1", "text": "Let me check the inbox."},
        {"id": "m-2", "text": "You have 2 new emails."},
    ]
    # reasoning_blocks holds ONLY the genuine chain-of-thought — no assistant
    # text ("Let me check the inbox.") is ever double-folded there.
    blocks = json.loads(folded["reasoning"])
    assert "The provider was slow." in blocks
    assert not any("Let me check the inbox." in b for b in blocks)
    # segmentCutoff records "1 segment existed when t1 started".
    [tool] = folded["tool_events"]
    assert tool["segmentCutoff"] == 1
    assert tool["reasoningCutoff"] == 0


def test_id_less_run_still_folds_no_segment_cutoff():
    """Fallback contract: an id-less stream (litellm/langgraph) has no segments,
    so the fold heuristic still runs and NO segmentCutoff is emitted — id-less
    persisted rows stay byte-identical to the pre-3b shape."""
    events = [
        _ev("TEXT_MESSAGE_CONTENT", 1000, delta="Let me look."),  # no messageId
        _ev("TOOL_CALL_START", 1100, toolCallId="t1", toolCallName="search"),
        _ev("TOOL_CALL_RESULT", 1200, toolCallId="t1", content="found", success=True),
        _ev("TEXT_MESSAGE_CONTENT", 1300, delta="Here it is."),
        _ev("RUN_FINISHED", 1400),
    ]
    folded = fold_run_events(events)
    assert folded is not None
    assert folded["content"] == "Here it is."
    # No segments captured → none persisted.
    assert folded["agent_state"] is None
    # The narration folded into reasoning (heuristic path).
    assert json.loads(folded["reasoning"])[0] == "Let me look."
    # No segmentCutoff key on the tool (id-less shape unchanged).
    [tool] = folded["tool_events"]
    assert "segmentCutoff" not in tool
    assert tool["reasoningCutoff"] == 1


def test_extraction_conv_appends_folded_answer_named_agent_shape():
    """P1-9: the named-agent path passes prior turns + a SEPARATE current
    message; the folded answer is appended so Mem0 sees the assistant turn
    (which reader-lifetime-bound extraction could never guarantee)."""
    history = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
    conv = build_extraction_conversation(
        history, "what time is it?", {"content": "It's 3pm."},
    )
    assert conv == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
        {"role": "user", "content": "what time is it?"},
        {"role": "assistant", "content": "It's 3pm."},
    ]


def test_extraction_conv_copilot_shape_message_already_in_history():
    """P1-9: the copilot path passes the FULL messages array (current user turn
    already included) with message='' — no duplicate user turn, answer appended.
    """
    conv = build_extraction_conversation(
        [{"role": "user", "content": "any mail?"}], "", {"content": "2 unread."},
    )
    assert conv == [
        {"role": "user", "content": "any mail?"},
        {"role": "assistant", "content": "2 unread."},
    ]


def test_extraction_conv_dedups_current_message_against_history_tail():
    conv = build_extraction_conversation(
        [{"role": "user", "content": "x"}], "x", {"content": "y"},
    )
    # 'x' not duplicated even though passed as both history tail and message.
    assert conv == [
        {"role": "user", "content": "x"},
        {"role": "assistant", "content": "y"},
    ]


def test_extraction_conv_empty_without_user_anchor():
    # No user turn anywhere → nothing to extract.
    assert build_extraction_conversation(
        [{"role": "assistant", "content": "z"}], "", None,
    ) == []
    # Answerless run (folded None / empty) still returns the user-anchored conv.
    assert build_extraction_conversation(
        [{"role": "user", "content": "q"}], "", None,
    ) == [{"role": "user", "content": "q"}]


def test_empty_run_folds_to_none():
    assert fold_run_events([]) is None
    assert fold_run_events([_ev("RUN_FINISHED", 1000)]) is None


def test_reasoning_groups_on_paragraph_breaks():
    blocks: list[str] = []
    for chunk in ["First thought", " continues.", "\n\nSecond thought."]:
        blocks = group_reasoning_blocks(blocks, chunk)
    assert blocks == ["First thought continues.", "Second thought."]


def test_unfold_is_noop_when_answer_followed_tool():
    content, blocks = unfold_trailing_answer("real answer", ["folded"], 0)
    assert content == "real answer"
    assert blocks == ["folded"]


# ── End-to-end: run_detached → on_complete → persisted row ──────────────────

class _FakePubSub:
    """Minimal async pub/sub stand-in — one Redis-side channel queue per
    subscription, fed by :meth:`_FakeRedis.publish`. Mirrors the proven
    implementation in test_stream_replay_trajectory.py so run_detached's
    control listener (stream_relay._control_listener) works under FakeRedis."""

    def __init__(self, hub: _FakeRedis) -> None:
        self._hub = hub
        self._queues: dict[str, asyncio.Queue] = {}

    async def subscribe(self, channel: str) -> None:
        q: asyncio.Queue = asyncio.Queue()
        self._queues[channel] = q
        self._hub._subs.setdefault(channel, []).append(q)

    async def unsubscribe(self, channel: str) -> None:
        q = self._queues.pop(channel, None)
        subs = self._hub._subs.get(channel)
        if subs and q in subs:
            subs.remove(q)

    async def listen(self):
        while True:
            getters = [asyncio.ensure_future(q.get()) for q in self._queues.values()]
            if not getters:
                await asyncio.sleep(0.005)
                continue
            done, pending = await asyncio.wait(
                getters, return_when=asyncio.FIRST_COMPLETED,
            )
            for p in pending:
                p.cancel()
            for d in done:
                yield d.result()

    async def aclose(self) -> None:
        for ch in list(self._queues):
            await self.unsubscribe(ch)


class _FakeRedis:
    def __init__(self) -> None:
        self.streams: dict[str, list[tuple[str, dict]]] = {}
        self.kv: dict[str, str] = {}
        self._subs: dict[str, list[asyncio.Queue]] = {}
        self._seq = 0

    async def publish(self, channel: str, data: str) -> int:
        subs = self._subs.get(channel, [])
        for q in subs:
            q.put_nowait({"type": "message", "channel": channel, "data": data})
        return len(subs)

    def pubsub(self) -> _FakePubSub:
        return _FakePubSub(self)

    @staticmethod
    def _id(eid: str) -> tuple[int, int]:
        ms, _, seq = eid.partition("-")
        return int(ms), int(seq or 0)

    async def xadd(self, key, fields, maxlen=None, approximate=None):
        self._seq += 1
        eid = f"{self._seq}-0"
        self.streams.setdefault(key, []).append((eid, dict(fields)))
        return eid

    def _read(self, streams, count):
        out = []
        for key, since in streams.items():
            if since == "$":
                continue
            floor = (0, 0) if since in ("0", "0-0") else self._id(since)
            tail = [(e, f) for e, f in self.streams.get(key, [])
                    if self._id(e) > floor]
            if count is not None:
                tail = tail[:count]
            if tail:
                out.append((key, tail))
        return out

    async def xread(self, streams, count=None, block=None):
        # Simulate Redis blocking-read semantics: without it, the subscriber
        # races ahead of the drain task (real XREAD parks up to `block` ms).
        loop = asyncio.get_running_loop()
        deadline = loop.time() + (block or 0) / 1000
        while True:
            out = self._read(streams, count)
            if out or not block or loop.time() >= deadline:
                return out
            await asyncio.sleep(0.005)

    async def get(self, key):
        return self.kv.get(key)

    async def set(self, key, value, ex=None, xx=False):
        if xx and key not in self.kv:
            return None
        self.kv[key] = value
        return True

    async def delete(self, key):
        self.kv.pop(key, None)
        self.streams.pop(key, None)
        return 1

    async def expire(self, key, ttl):
        return True

    async def exists(self, key):
        return int(key in self.kv or key in self.streams)


async def test_detached_run_persists_final_message(monkeypatch):
    """The whole Phase-1 path: a detached run streams to Redis, the client
    never reconnects, and the on_complete hook still persists the full turn."""
    import gateway.routes.chat as chat_routes

    fake = _FakeRedis()
    monkeypatch.setattr(stream_relay, "_client", fake)

    # Track call ORDER: the parent session must be ensured before the message
    # FK insert (P0-3 self-sufficiency — a first-turn client-death must not
    # lose the message to the chat_message → chat_session foreign key).
    calls: list[str] = []
    persisted: list[tuple[str, list]] = []
    monkeypatch.setattr(
        chat_routes, "_ensure_session",
        lambda sid, uid, agent="orchestrator", **kw: calls.append(f"ensure:{sid}:{uid}"),
    )
    monkeypatch.setattr(
        chat_routes, "_upsert_messages",
        # **kw: the real signature grew authorship (actor_email, agent_name,
        # authority) when sessions became rooms. A positional-only double would
        # raise TypeError inside persist_final_assistant_message's blanket
        # except, which turns a signature drift into a silent no-persist.
        lambda sid, msgs, **kw: (calls.append(f"upsert:{sid}"),
                                 persisted.append((sid, msgs)))[-1],
    )

    # The E2 run-trace write (added after this test) hits Postgres; offline it
    # blocks on the connection timeout for minutes. Stub it — this test locks
    # message persistence, not the trace row (that has its own coverage).
    import gateway.run_trace as run_trace

    async def _noop_trace(**_kw):
        return None
    monkeypatch.setattr(run_trace, "record_run_trace", _noop_trace)

    # The room clearance (S13) reads the session's members from Postgres, and
    # offline that read waits on a connection. A solo run records none.
    import gateway.chat_fold as chat_fold

    async def _solo(*_a, **_kw):
        return None
    monkeypatch.setattr(chat_fold, "_run_authority", _solo)

    async def _agent_gen():
        yield 'data: {"type": "TEXT_MESSAGE_CONTENT", "delta": "Hello "}\n\n'
        yield 'data: {"type": "TEXT_MESSAGE_CONTENT", "delta": "world."}\n\n'
        yield 'data: {"type": "RUN_FINISHED"}\n\n'

    returned: list = []

    async def _on_complete() -> None:
        # Returns the folded dict so run-boundary consumers (memory
        # extraction, P1-9) can chain off the persisted content.
        returned.append(
            await persist_final_assistant_message(
                "traj-persist", "assistant-msg-1", user_id="u@x.io",
                organization_id=TRAJ_ORG,
            )
        )

    # Consume as the HTTP subscriber would (client stays connected here; the
    # disconnect case exercises the same finally via task cancellation below).
    events = [
        e async for e in stream_relay.run_detached(
            "traj-persist", _agent_gen(), tee=True,
            on_complete=_on_complete,
        )
    ]
    assert [e["type"] for e in events][-1] == "RUN_FINISHED"

    # Give the drain task's finally a tick to run on_complete.
    for _ in range(50):
        if persisted:
            break
        await asyncio.sleep(0.01)

    assert len(persisted) == 1
    sid, [record] = persisted[0]
    assert sid == "traj-persist"
    assert record.id == "assistant-msg-1"
    assert record.role == "assistant"
    assert record.content == "Hello world."
    # Session ensured (owned by the acting user) BEFORE the message insert.
    assert calls == ["ensure:traj-persist:u@x.io", "upsert:traj-persist"]
    # The folded dict is returned for run-boundary chaining (P1-9).
    assert returned[0] is not None
    assert returned[0]["content"] == "Hello world."


async def test_cancelled_run_still_persists_partial_turn(monkeypatch):
    """Stop/steer cancels the drain task — the partial turn must still be
    folded and persisted (the finally runs on every exit path)."""
    import gateway.routes.chat as chat_routes

    fake = _FakeRedis()
    monkeypatch.setattr(stream_relay, "_client", fake)

    persisted: list[list] = []
    monkeypatch.setattr(
        chat_routes, "_ensure_session",
        lambda sid, uid, agent="orchestrator", **kw: None,
    )
    monkeypatch.setattr(
        chat_routes, "_upsert_messages",
        lambda sid, msgs, **kw: persisted.append(msgs),
    )
    # Stub the E2 run-trace DB write (see the sibling test) — offline it blocks
    # on the Postgres connection timeout for minutes.
    import gateway.run_trace as run_trace

    async def _noop_trace(**_kw):
        return None
    monkeypatch.setattr(run_trace, "record_run_trace", _noop_trace)

    started = asyncio.Event()

    async def _slow_agent_gen():
        yield 'data: {"type": "TEXT_MESSAGE_CONTENT", "delta": "Partial answer"}\n\n'
        started.set()
        await asyncio.sleep(60)  # cancelled long before this completes
        yield 'data: {"type": "RUN_FINISHED"}\n\n'

    async def _on_complete() -> None:
        await persist_final_assistant_message(
            "traj-cancel", "assistant-msg-2", organization_id=TRAJ_ORG,
        )

    async def _subscriber() -> None:
        async for _ in stream_relay.run_detached(
            "traj-cancel", _slow_agent_gen(), tee=True,
            on_complete=_on_complete,
        ):
            pass

    sub_task = asyncio.create_task(_subscriber())
    await asyncio.wait_for(started.wait(), timeout=5)
    assert await stream_relay.cancel_run("traj-cancel") is True
    sub_task.cancel()
    try:
        await sub_task
    except (asyncio.CancelledError, Exception):  # noqa: BLE001
        pass

    for _ in range(50):
        if persisted:
            break
        await asyncio.sleep(0.01)

    assert len(persisted) == 1
    [record] = persisted[0]
    assert record.content == "Partial answer"
