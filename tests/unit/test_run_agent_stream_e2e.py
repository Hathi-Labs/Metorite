"""End-to-end characterization of run_agent_stream (audit BO-13 enabler).

Nothing drove the 1,600-line run_agent_stream in a test before — sub-components
were covered, the whole streamed run was not. This drives it with a mocked
agent + loader (no git clone, no LLM, no Redis) and pins the AG-UI SSE event
CONTRACT the frontend depends on: a well-formed RUN_STARTED … RUN_FINISHED
envelope with the assistant text streamed in between. It is the regression net
for decomposing run_agent_stream into its tier handlers.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from orchestrator import executor


@pytest.fixture(autouse=True)
def _clean_run_breadcrumbs():
    """These tests drive the REAL run path, which stamps the cross-runtime
    HITL breadcrumbs (``_WRITE_ARTIFACT_CONTEXT["session_id"]``, run queues)
    as a production feature. In a shared test process a stale session id makes
    ``resolve_relay_thread_id`` resolve a dead relay later — blocking HITL
    tools then park on a Future instead of failing closed. Clear after each
    test so state never leaks past this file."""
    yield
    from acb_skills.write_artifact import _WRITE_ARTIFACT_CONTEXT

    _WRITE_ARTIFACT_CONTEXT.clear()
    executor._RUN_QUEUES.clear()
    executor._pending_user_input.clear()

# The Tier-1→Tier-2 fallback discards one un-awaited run() coroutine from the
# mock (it doesn't implement the native streaming interface); that's the whole
# point of the fallback and is benign here. Filter just that RuntimeWarning.
pytestmark = pytest.mark.filterwarnings(
    "ignore:coroutine .*run.* was never awaited:RuntimeWarning"
)


class _Resp:
    def __init__(self, text: str) -> None:
        self.text = text
        self.messages: list[Any] = []


class _MockMafAgent:
    """Minimal native-MAF-shaped agent: a name, empty tools, an async run().

    ``run`` is a coroutine function (so the Tier-2 batch path's
    ``inspect.iscoroutinefunction`` check + ``await agent.run(input)`` work and
    the reply streams). It does NOT implement the ``run(..., stream=True)``
    async-iterator interface, so run_agent_stream's Tier-1 native-streaming
    attempt no-ops and falls through to the proven Tier-2 batch path — exactly
    the fallback the code documents. That fallback discards one un-awaited
    ``run()`` coroutine (a benign RuntimeWarning filtered at module level).
    """

    def __init__(self, reply: str) -> None:
        self.name = "test-agent"
        self.tools: list[Any] = []
        self.default_options: dict[str, Any] = {}
        self._reply = reply

    async def run(self, *_a: Any, **_k: Any) -> _Resp:
        return _Resp(self._reply)

    async def __aenter__(self) -> "_MockMafAgent":
        return self

    async def __aexit__(self, *_a: Any) -> bool:
        return False


class _LoadedStub:
    def __init__(self, reply: str) -> None:
        self.agent_dir = Path("/tmp")
        self.agent_name = "test-agent"
        self.config: dict[str, Any] = {}
        self._reply = reply

    def build_agents(self) -> list[Any]:
        return [_MockMafAgent(self._reply)]


class _LoadCtx:
    def __init__(self, reply: str) -> None:
        self._reply = reply

    def __enter__(self) -> _LoadedStub:
        return _LoadedStub(self._reply)

    def __exit__(self, *_a: Any) -> bool:
        return False


def _parse_frames(frames: list[str]) -> list[dict[str, Any]]:
    """Turn raw ``data: {...}\\n\\n`` SSE lines into event dicts."""
    events: list[dict[str, Any]] = []
    for line in frames:
        for part in line.split("\n"):
            part = part.strip()
            if part.startswith("data:"):
                body = part[len("data:"):].strip()
                if body and body != "[DONE]":
                    try:
                        events.append(json.loads(body))
                    except json.JSONDecodeError:
                        pass
    return events


class _RaisingAgent(_MockMafAgent):
    async def run(self, *_a: Any, **_k: Any) -> _Resp:
        raise RuntimeError("boom in the agent")


class _RaisingLoadCtx(_LoadCtx):
    def __enter__(self) -> _LoadedStub:
        stub = _LoadedStub(self._reply)
        stub.build_agents = lambda: [_RaisingAgent(self._reply)]  # type: ignore[method-assign]
        return stub


def _drive(
    monkeypatch,
    reply: str = "hello world from the agent",
    *,
    ctx_factory: Any = None,
    run_id: str | None = None,
    thread_id: str | None = None,
) -> list[dict[str, Any]]:
    factory = ctx_factory or (lambda r: _LoadCtx(r))
    monkeypatch.setattr(executor, "load_agent", lambda *a, **k: factory(reply))
    monkeypatch.setattr(executor, "build_integrations", lambda *a, **k: ({}, {}))

    async def _collect() -> list[str]:
        out: list[str] = []
        async for line in executor.run_agent_stream(
            "test-agent-unregistered", {"message": "hi"},
            run_id=run_id, thread_id=thread_id,
        ):
            out.append(line)
        return out

    return _parse_frames(asyncio.run(_collect()))


def test_stream_opens_with_run_started_and_closes_with_run_finished(monkeypatch):
    events = _drive(monkeypatch)
    types = [e.get("type") for e in events]
    assert types, "no SSE events produced"
    assert types[0] == "RUN_STARTED"
    assert "RUN_FINISHED" in types
    # RUN_FINISHED is terminal — nothing after it.
    assert types.index("RUN_FINISHED") == len(types) - 1 - types[::-1].index("RUN_FINISHED")


def test_stream_emits_assistant_text(monkeypatch):
    events = _drive(monkeypatch, reply="the quick brown fox")
    text = "".join(
        str(e.get("delta") or e.get("content") or "")
        for e in events
        if e.get("type") == "TEXT_MESSAGE_CONTENT"
    )
    assert "quick brown fox" in text


def test_stream_no_run_error_on_happy_path(monkeypatch):
    events = _drive(monkeypatch)
    assert not [e for e in events if e.get("type") == "RUN_ERROR"]


def test_stream_propagates_run_and_thread_ids(monkeypatch):
    events = _drive(monkeypatch, run_id="fixed-run", thread_id="fixed-thread")
    started = next(e for e in events if e.get("type") == "RUN_STARTED")
    assert started.get("runId") == "fixed-run"
    assert started.get("threadId") == "fixed-thread"


def test_agent_exception_surfaces_as_run_error_not_a_crash(monkeypatch):
    # A raising agent must become a RUN_ERROR frame (the stream stays a valid
    # AG-UI envelope), never an unhandled exception out of the generator.
    events = _drive(monkeypatch, ctx_factory=lambda r: _RaisingLoadCtx(r))
    types = [e.get("type") for e in events]
    assert types[0] == "RUN_STARTED"
    assert "RUN_ERROR" in types


# ── Tier-1: native MAF streaming ────────────────────────────────────────────
# A native MAF agent's run(input, stream=True) returns an async iterator of
# "update" objects (agent_framework AgentRunResponseUpdate shape). The executor
# translates each update's contents into AG-UI events via the event_translator.
# These builders mirror that shape (see event_translator.translate_update).

def _text(t: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=t)


def _fc(name: str, arguments: str, call_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        type="function_call", name=name, arguments=arguments, call_id=call_id,
    )


def _fr(call_id: str, result: str) -> SimpleNamespace:
    return SimpleNamespace(
        type="function_result", call_id=call_id, result=result, exception=None,
    )


def _update(contents: list[Any], *, role: str = "assistant",
            message_id: str = "m1") -> SimpleNamespace:
    return SimpleNamespace(role=role, contents=contents, message_id=message_id)


class _NativeStreamingAgent:
    """A native-MAF agent whose run(stream=True) yields real update objects, so
    run_agent_stream takes its Tier-1 native-streaming path (not the batch
    fallback) and translates the updates into AG-UI events."""

    def __init__(self, updates: list[Any]) -> None:
        self.name = "test-agent"
        self.tools: list[Any] = []
        self.default_options: dict[str, Any] = {}
        self._updates = updates

    def run(self, *_a: Any, **_k: Any) -> Any:
        return self._stream()

    async def _stream(self) -> Any:
        for u in self._updates:
            yield u

    async def __aenter__(self) -> "_NativeStreamingAgent":
        return self

    async def __aexit__(self, *_a: Any) -> bool:
        return False


def _drive_native(monkeypatch, updates: list[Any]) -> list[dict[str, Any]]:
    class _Loaded(_LoadedStub):
        def build_agents(self_inner) -> list[Any]:  # noqa: N805
            return [_NativeStreamingAgent(updates)]

    class _Ctx:
        def __enter__(self) -> _LoadedStub:
            return _Loaded("")

        def __exit__(self, *_a: Any) -> bool:
            return False

    monkeypatch.setattr(executor, "load_agent", lambda *a, **k: _Ctx())
    monkeypatch.setattr(executor, "build_integrations", lambda *a, **k: ({}, {}))

    async def _collect() -> list[str]:
        out: list[str] = []
        async for line in executor.run_agent_stream(
            "test-agent-unregistered", {"message": "hi"},
        ):
            out.append(line)
        return out

    return _parse_frames(asyncio.run(_collect()))


def test_native_streaming_emits_text_message_lifecycle(monkeypatch):
    events = _drive_native(monkeypatch, [
        _update([_text("the quick ")]),
        _update([_text("brown fox")]),
    ])
    types = [e.get("type") for e in events]
    assert types[0] == "RUN_STARTED"
    assert "TEXT_MESSAGE_START" in types
    assert "TEXT_MESSAGE_END" in types
    assert types[-1] == "RUN_FINISHED"
    text = "".join(
        e.get("delta", "") for e in events if e.get("type") == "TEXT_MESSAGE_CONTENT"
    )
    assert text == "the quick brown fox"
    assert not [e for e in events if e.get("type") == "RUN_ERROR"]


def test_native_streaming_emits_tool_call_and_result_events(monkeypatch):
    events = _drive_native(monkeypatch, [
        _update([_fc("query_inbox", '{"account_id": "123"}', "call_1")]),
        _update([_fr("call_1", "42 unread")], role="tool"),
        _update([_text("you have 42 unread")]),
    ])
    starts = [e for e in events if e.get("type") == "TOOL_CALL_START"]
    results = [e for e in events if e.get("type") == "TOOL_CALL_RESULT"]
    assert len(starts) == 1
    assert starts[0]["toolCallName"] == "query_inbox"
    assert starts[0]["toolCallId"] == "call_1"
    assert results and results[0]["toolCallId"] == "call_1"
    assert results[0]["success"] is True
    # The tool call closes the narration segment; the final answer still streams.
    text = "".join(
        e.get("delta", "") for e in events if e.get("type") == "TEXT_MESSAGE_CONTENT"
    )
    assert "42 unread" in text


# ── HITL: ask_user parking → user_input_requested frame → resolve ───────────
# The Copilot SDK's native ask_user is bridged by _make_user_input_handler: it
# emits a `user_input_requested` frame to the relay, parks a Future, and blocks
# until the gateway's /agent/respond-input calls resolve_user_input. These pin
# that round-trip (the branch the batch/native tests don't exercise).

def test_resolve_user_input_unknown_request_returns_false():
    assert executor.resolve_user_input("no-such-request", "x", thread_id="t-1") is False


def test_resolve_user_input_sets_the_pending_future():
    async def _run() -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        executor._pending_user_input.park("req-1", fut, "t-1")
        try:
            # H-201: only the thread that owns the request may answer it.
            assert not executor.resolve_user_input(
                "req-1", "the answer", was_freeform=True, thread_id="t-2")
            assert executor.resolve_user_input(
                "req-1", "the answer", was_freeform=True, thread_id="t-1")
            return await asyncio.wait_for(fut, timeout=1)
        finally:
            executor._pending_user_input.pop("req-1", None)

    assert asyncio.run(_run()) == {"answer": "the answer", "wasFreeform": True}


def test_hitl_handler_emits_prompt_parks_then_returns_answer(monkeypatch):
    captured: list[tuple[str, str]] = []

    async def _fake_push(thread_id: str, line: str) -> None:
        captured.append((thread_id, line))

    monkeypatch.setattr(executor, "_push_sse_to_stream", _fake_push)
    handler = executor._make_user_input_handler("thread-x")

    async def _run() -> dict[str, Any]:
        task = asyncio.ensure_future(
            handler({"question": "Pick one?", "choices": ["a", "b"],
                     "allowFreeform": False}, None)
        )
        # Wait for the handler to register a parked request, then resolve it.
        req_id = None
        for _ in range(400):
            if executor._pending_user_input:
                req_id = next(iter(executor._pending_user_input))
                break
            await asyncio.sleep(0.005)
        assert req_id, "handler never parked a pending request"
        assert executor.resolve_user_input(req_id, "b", was_freeform=False,
                                           thread_id="thread-x")
        return await asyncio.wait_for(task, timeout=2)

    result = asyncio.run(_run())
    assert result == {"answer": "b", "wasFreeform": False}

    # The prompt frame reached the relay with the question + choices intact.
    assert captured and captured[0][0] == "thread-x"
    frame = _parse_frames([captured[0][1]])[0]
    assert frame.get("name") == "user_input_requested"
    assert frame["value"]["question"] == "Pick one?"
    assert frame["value"]["choices"] == ["a", "b"]
    assert frame["value"]["allowFreeform"] is False


# ── Loop detection: identical repeated tool calls must trip CLEANLY ─────────
# Regression for the loop-trip crash: `_next_task` is already None when the
# loop-detected branch runs, so an unconditional `.cancel()` raised
# AttributeError — the user saw the real "loop detected" RUN_ERROR followed by
# a bogus "'NoneType' object has no attribute 'cancel'" RUN_ERROR, and the
# stream closed without a clean terminal event.


def _loop_updates(n: int) -> list[Any]:
    ups: list[Any] = []
    for i in range(n):
        cid = f"call_{i}"
        ups.append(_update([_fc("poll_status", "{}", cid)]))
        ups.append(_update([_fr(cid, "pending")], role="tool"))
    return ups


def test_loop_trip_emits_single_clean_run_error(monkeypatch):
    monkeypatch.setenv("TOOL_LOOP_MAX_REPEATS", "3")
    events = _drive_native(monkeypatch, _loop_updates(3))
    errors = [e for e in events if e.get("type") == "RUN_ERROR"]
    assert len(errors) == 1, f"expected exactly one RUN_ERROR, got {errors}"
    assert "loop detected" in errors[0]["message"]
    assert "NoneType" not in errors[0]["message"]
    # The loop RUN_ERROR is the terminal event — no RUN_FINISHED may follow
    # and contradict it (the client would render the run as successful).
    assert not [e for e in events if e.get("type") == "RUN_FINISHED"]


def test_below_loop_threshold_finishes_normally(monkeypatch):
    monkeypatch.setenv("TOOL_LOOP_MAX_REPEATS", "3")
    events = _drive_native(
        monkeypatch, _loop_updates(2) + [_update([_text("done")])],
    )
    assert not [e for e in events if e.get("type") == "RUN_ERROR"]
    assert events[-1]["type"] == "RUN_FINISHED"


# ── Tier 1.5: stale-session retry must not replay an emitting turn ──────────
# Regression for the duplicated-turn bug: the stale-session classifier's broad
# substring match ("session" + "error") also caught MID-STREAM provider/session
# failures, and the retry re-ran the whole turn with fresh message ids — the
# user saw all partial output twice. A genuine stale resume fails inside the
# SDK's session setup BEFORE any event is emitted, so the emitted-output guard
# distinguishes the two.


class _CopilotShapedAgent:
    """Copilot-SDK-shaped agent (non-None ``_default_options`` → Tier 1.5).

    Scripted per attempt: each entry is ``(updates_to_yield, exception|None)``;
    the last entry repeats if the executor retries more times than scripted.
    """

    def __init__(
        self, script: list[tuple[list[Any], Exception | None]],
    ) -> None:
        self.name = "test-agent"
        self.tools: list[Any] = []
        self._default_options: dict[str, Any] = {"model": "tier-balanced"}
        self._script = list(script)
        self.attempts = 0

    def get_session(self, _sid: str) -> Any:
        self.get_session_calls = getattr(self, "get_session_calls", 0) + 1
        return object()

    def run(self, *_a: Any, **_k: Any) -> Any:
        updates, exc = self._script[min(self.attempts, len(self._script) - 1)]
        self.attempts += 1
        return self._replay(updates, exc)

    async def _replay(
        self, updates: list[Any], exc: Exception | None,
    ) -> Any:
        for u in updates:
            yield u
        if exc is not None:
            raise exc

    async def __aenter__(self) -> "_CopilotShapedAgent":
        return self

    async def __aexit__(self, *_a: Any) -> bool:
        return False


def _drive_copilot(
    monkeypatch,
    script: list[tuple[list[Any], Exception | None]],
    thread_id: str,
    label: str = "github-copilot",
) -> tuple[list[dict[str, Any]], "_CopilotShapedAgent"]:
    agent = _CopilotShapedAgent(script)

    class _Loaded(_LoadedStub):
        def build_agents(self_inner) -> list[Any]:  # noqa: N805
            return [agent]

    class _Ctx:
        def __enter__(self) -> _LoadedStub:
            return _Loaded("")

        def __exit__(self, *_a: Any) -> bool:
            return False

    monkeypatch.setattr(executor, "load_agent", lambda *a, **k: _Ctx())
    monkeypatch.setattr(executor, "build_integrations", lambda *a, **k: ({}, {}))
    # Register the agent under the given runtime label. Session continuity is
    # gated by CAPABILITY (non-None _default_options), so a "maf"-labelled
    # Copilot-shaped agent must behave the same as a "github-copilot" one.
    import gateway.routes.agent as _agent_routes
    monkeypatch.setattr(_agent_routes, "_load_dynamic_agents", lambda: [
        {"name": "test-agent-unregistered", "agent_runtime": label,
         "repo_name": None, "local_path": None},
    ])
    # A stored session id is what makes the stale-resume classifier eligible.
    executor._copilot_session_store[thread_id] = "sess-stale-1"

    async def _collect() -> list[str]:
        out: list[str] = []
        async for line in executor.run_agent_stream(
            "test-agent-unregistered", {"message": "hi"}, thread_id=thread_id,
        ):
            out.append(line)
        return out

    try:
        return _parse_frames(asyncio.run(_collect())), agent
    finally:
        executor._copilot_session_store.pop(thread_id, None)


def test_midstream_session_error_does_not_replay_turn(monkeypatch):
    events, agent = _drive_copilot(
        monkeypatch,
        [
            ([_update([_text("partial answer")])],
             RuntimeError("GitHub Copilot session error: upstream 400")),
            ([_update([_text("partial answer")])], None),
        ],
        thread_id="t-c2-dup",
    )
    assert agent.attempts == 1, "mid-stream error must not trigger a retry"
    text = "".join(
        e.get("delta", "") for e in events
        if e.get("type") == "TEXT_MESSAGE_CONTENT"
    )
    assert text.count("partial answer") == 1, f"duplicated output: {text!r}"
    errors = [e for e in events if e.get("type") == "RUN_ERROR"]
    assert len(errors) == 1
    assert "session error" in errors[0]["message"]


def test_stale_session_before_emission_still_retries(monkeypatch):
    events, agent = _drive_copilot(
        monkeypatch,
        [
            ([], RuntimeError(
                "Failed to create GitHub Copilot session: CLI process died")),
            ([_update([_text("recovered answer")])], None),
        ],
        thread_id="t-c2-stale",
    )
    assert agent.attempts == 2, "pre-emission stale resume must retry"
    text = "".join(
        e.get("delta", "") for e in events
        if e.get("type") == "TEXT_MESSAGE_CONTENT"
    )
    assert "recovered answer" in text
    assert not [e for e in events if e.get("type") == "RUN_ERROR"]


def test_mislabeled_copilot_agent_still_resumes_session(monkeypatch):
    """Session continuity keys on CAPABILITY, not the registry label.

    Regression: restore/save/working_directory were gated on
    agent_runtime == "github-copilot" while Tier-1.5 entry is by capability —
    so a Copilot-built agent registered "maf" (task-manager, apis-config)
    never resumed its Copilot session and re-injected history every turn.
    """
    events, agent = _drive_copilot(
        monkeypatch,
        [([_update([_text("hello again")])], None)],
        thread_id="t-mislabel",
        label="maf",
    )
    assert getattr(agent, "get_session_calls", 0) == 1, (
        "a maf-labelled Copilot-shaped agent must consult its stored session"
    )
    text = "".join(
        e.get("delta", "") for e in events
        if e.get("type") == "TEXT_MESSAGE_CONTENT"
    )
    assert "hello again" in text
    assert not [e for e in events if e.get("type") == "RUN_ERROR"]


def test_context_pressure_surfaces_a_progress_line(monkeypatch):
    """CX6: silent history eviction now emits one PROGRESS_UPDATE notice."""
    import acb_llm.context as _ctx

    monkeypatch.setattr(_ctx, "context_window_for", lambda _m: 2000)
    monkeypatch.setattr(
        _ctx, "count_message_tokens",
        lambda msgs, _m: sum(
            len(str(m.get("content") or "")) // 4 + 8 for m in msgs
        ),
    )

    class _Loaded(_LoadedStub):
        def build_agents(self_inner) -> list[Any]:  # noqa: N805
            return [_NativeStreamingAgent([_update([_text("short answer")])])]

    class _Ctx:
        def __enter__(self) -> _LoadedStub:
            return _Loaded("")

        def __exit__(self, *_a: Any) -> bool:
            return False

    monkeypatch.setattr(executor, "load_agent", lambda *a, **k: _Ctx())
    monkeypatch.setattr(executor, "build_integrations", lambda *a, **k: ({}, {}))

    history = [
        {"role": "user" if i % 2 == 0 else "assistant",
         "content": f"turn {i}: " + "x" * 2000}
        for i in range(12)
    ]

    async def _collect() -> list[str]:
        out: list[str] = []
        async for line in executor.run_agent_stream(
            "test-agent-unregistered",
            {"message": "current question", "messages": history},
        ):
            out.append(line)
        return out

    events = _parse_frames(asyncio.run(_collect()))
    notices = [
        e for e in events
        if e.get("type") == "PROGRESS_UPDATE"
        and "no longer fit" in str(e.get("message"))
    ]
    assert notices, "evicting history must surface a context-pressure notice"


def test_no_pressure_notice_when_history_fits(monkeypatch):
    events = _drive_native(monkeypatch, [_update([_text("hi there")])])
    assert not [
        e for e in events
        if e.get("type") == "PROGRESS_UPDATE"
        and "no longer fit" in str(e.get("message"))
    ]
