"""HRD-6 (task 2985): one line for the member while a slow tool runs.

Spec: ``project-docs/specs/chat_failure_modes.md`` §4 row B6 and §6 HRD-6,
``chat_run_continuity.md`` §4.11. Module: ``acb_skills.progress_notice``.

The owner's rule (2026-11-13): a large read can take minutes, so the member
hears that it takes a while. These tests hold each rule of the module:

1. The flag ``CHAT_PROGRESS_NOTICE_ORGS`` is empty by default, and then the
   injection seam attaches nothing, so a run is as before.
2. A call that outlives the timer gets ONE line, with its call id. A fast
   call gets none.
3. A tool's own line wins, and the timer sends nothing after it.
4. Parallel calls do not stack lines.
5. A delegation tool and a tool that waits for the member get no line.
6. WhatsApp gets the line as its early message, once in a reply.
7. The fold never keeps the line, and it is no answer event.
8. The native stream survives a tool that runs past the bare idle cap, and
   the line reaches the stream between the tool's start and its result.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterable
from types import SimpleNamespace
from typing import Any

import pytest
from acb_skills import progress_notice as pn
from agent_framework import (
    Agent,
    BaseChatClient,
    ChatMiddlewareLayer,
    ChatResponse,
    ChatResponseUpdate,
    Content,
    FunctionInvocationLayer,
    Message,
)
from orchestrator import _tool_injection, executor

ORG = "org-hrd6"
FAST = 0.05  # the timer of a test run, in seconds


@pytest.fixture
def flag_on(monkeypatch):
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "chat_progress_notice_orgs", ORG)
    from acb_common.db import bind_tenant, release_tenant

    token = bind_tenant(ORG)
    try:
        yield
    finally:
        release_tenant(token)


@pytest.fixture(autouse=True)
def _clean_queue_binding():
    yield
    executor._RUN_QUEUES.clear()


# ── A scripted model ────────────────────────────────────────────────────────


class ScriptedClient(FunctionInvocationLayer, ChatMiddlewareLayer, BaseChatClient):
    """Answers each request from a step: ``("calls", [(name, args), ...])`` or
    ``("text", words)``."""

    def __init__(self, script: list[Any]) -> None:
        super().__init__()
        self.script = list(script)
        self.n = 0

    def _inner_get_response(self, *, messages, stream, options, **kwargs):  # type: ignore[override]
        self.n += 1
        step = self.script.pop(0)
        if step[0] == "calls":
            contents = [
                Content.from_function_call(f"c{self.n}-{i}", name, arguments=json.dumps(args))
                for i, (name, args) in enumerate(step[1])
            ]
        else:
            contents = [Content.from_text(step[1])]
        n = self.n
        if stream:
            async def _gen() -> AsyncIterable[ChatResponseUpdate]:
                for content in contents:
                    yield ChatResponseUpdate(contents=[content], role="assistant",
                                             response_id=f"r{n}", message_id="m")
            return self._build_response_stream(_gen())

        async def _one() -> ChatResponse:
            return ChatResponse(messages=[Message("assistant", contents)], response_id=f"r{n}")
        return _one()


def _view(agent: Agent) -> Any:
    """The per-run view of the real seam, with the test's short timer."""
    agents: list[Any] = [agent]
    _tool_injection._apply_progress_notice(agents)
    assert agents[0] is not agent, "a native agent gets a per-run view"
    view = agents[0]
    for provider in view.context_providers:
        if isinstance(provider, pn.ProgressNoticeProvider):
            provider.after_s = FAST
    return view


def _drain(queue: asyncio.Queue) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    while not queue.empty():
        out.append(queue.get_nowait())
    return out


def _lines(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in events if e.get("type") == "CUSTOM" and e.get("name") == pn.EVENT]


async def _run_with_queue(view: Any) -> list[dict[str, Any]]:
    queue: asyncio.Queue = asyncio.Queue()
    token = executor._active_run_queue.set(queue)
    try:
        await view.run("hi")
    finally:
        executor._active_run_queue.reset(token)
    return _drain(queue)


# ── 1. The flag ─────────────────────────────────────────────────────────────


def test_the_flag_is_empty_by_default_and_reads_the_bound_tenant(monkeypatch):
    from acb_common import get_settings
    from acb_common.settings import Settings

    assert Settings.model_fields["chat_progress_notice_orgs"].default == ""
    monkeypatch.setattr(get_settings(), "chat_progress_notice_orgs", "")
    assert not pn.progress_notice_on(ORG)
    monkeypatch.setattr(get_settings(), "chat_progress_notice_orgs", f"x, {ORG.upper()}")
    assert pn.progress_notice_on(ORG)
    assert not pn.progress_notice_on("org-other")
    monkeypatch.setattr(get_settings(), "chat_progress_notice_orgs", "*")
    assert pn.progress_notice_on("org-other")


def test_the_flag_is_named_in_the_three_places_of_a_flag():
    from pathlib import Path

    from acb_common import env_guard

    root = Path(__file__).resolve().parents[2]
    env = (root / ".env.example").read_text(encoding="utf-8")
    assert "\nCHAT_PROGRESS_NOTICE_ORGS=\n" in env
    # Only the operator writes it: no tenant route may set it.
    assert env_guard.is_platform_env("CHAT_PROGRESS_NOTICE_ORGS")
    from gateway import flag_reload

    assert flag_reload.reloadable("CHAT_PROGRESS_NOTICE_ORGS")


def test_flag_off_attaches_nothing(monkeypatch):
    """Rule 6: empty means each run keeps its own object, byte for byte."""
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "chat_progress_notice_orgs", "")
    agent = Agent(client=ScriptedClient([]), name="probe")
    agents: list[Any] = [agent]
    _tool_injection._apply_progress_notice(agents)
    assert agents[0] is agent
    assert not asyncio.run(pn.emit_progress("Adding up 31 totals."))


def test_flag_on_gives_a_per_run_view_and_leaves_the_shared_agent(flag_on):
    agent = Agent(client=ScriptedClient([]), name="probe")
    view = _view(agent)
    assert any(isinstance(p, pn.ProgressNoticeProvider) for p in view.context_providers)
    assert not any(isinstance(p, pn.ProgressNoticeProvider) for p in agent.context_providers)


def test_the_seam_runs_last_in_inject_agent_tools():
    import inspect

    src = inspect.getsource(_tool_injection._inject_agent_tools)
    assert src.rindex("_apply_progress_notice(agents)") > src.rindex(
        "_apply_answer_check(agents, agent_name)")


# ── 2 to 5. The timer on each call ──────────────────────────────────────────


def test_a_slow_call_gets_one_line_with_its_call_id(flag_on):
    async def crm_aggregate(entity: str) -> str:
        """Add up a large CRM set."""
        await asyncio.sleep(FAST * 6)
        return "31 totals"

    client = ScriptedClient([("calls", [("crm_aggregate", {"entity": "deals"})]), ("text", "Done.")])
    view = _view(Agent(client=client, name="crm", tools=[crm_aggregate]))
    lines = _lines(asyncio.run(_run_with_queue(view)))
    assert lines == [{
        "type": "CUSTOM", "name": "progress",
        "value": {"text": pn.LONG_READ_TEXT, "tool_call_id": "c1-0"},
    }]


def test_a_fast_call_gets_no_line(flag_on):
    async def lookup(q: str) -> str:
        """A quick read."""
        return "ok"

    client = ScriptedClient([("calls", [("lookup", {"q": "x"})]), ("text", "Done.")])
    view = _view(Agent(client=client, name="probe", tools=[lookup]))
    assert _lines(asyncio.run(_run_with_queue(view))) == []


def test_a_tool_line_first_stops_the_timer_line(flag_on):
    async def crm_aggregate(entity: str) -> str:
        """Add up a large CRM set."""
        assert await pn.emit_progress("Adding up 31 totals.  This can take two minutes.")
        assert not await pn.emit_progress("A second line"), "one line for each call"
        await asyncio.sleep(FAST * 6)
        return "31 totals"

    client = ScriptedClient([("calls", [("crm_aggregate", {"entity": "deals"})]), ("text", "Done.")])
    view = _view(Agent(client=client, name="crm", tools=[crm_aggregate]))
    lines = _lines(asyncio.run(_run_with_queue(view)))
    assert [ln["value"]["text"] for ln in lines] == [
        "Adding up 31 totals. This can take two minutes."]
    assert lines[0]["value"]["tool_call_id"] == "c1-0"


def test_parallel_slow_calls_do_not_stack_lines(flag_on):
    async def slow_read(part: str) -> str:
        """A slow read."""
        await asyncio.sleep(FAST * 6)
        return part

    client = ScriptedClient([
        ("calls", [("slow_read", {"part": "a"}), ("slow_read", {"part": "b"})]),
        ("text", "Done."),
    ])
    view = _view(Agent(client=client, name="probe", tools=[slow_read]))
    assert len(_lines(asyncio.run(_run_with_queue(view)))) == 1


def test_a_later_slow_call_gets_its_own_line(flag_on):
    """The mark of a line ends with its call, so the next slow call shows."""
    async def slow_read(part: str) -> str:
        """A slow read."""
        await asyncio.sleep(FAST * 6)
        return part

    client = ScriptedClient([
        ("calls", [("slow_read", {"part": "a"})]),
        ("calls", [("slow_read", {"part": "b"})]),
        ("text", "Done."),
    ])
    view = _view(Agent(client=client, name="probe", tools=[slow_read]))
    lines = _lines(asyncio.run(_run_with_queue(view)))
    assert [ln["value"]["tool_call_id"] for ln in lines] == ["c1-0", "c2-0"]


@pytest.mark.parametrize("name", ["call_agent", "request_confirmation", "ask_questions"])
def test_a_delegation_or_a_wait_for_the_member_gets_no_line(flag_on, name):
    async def tool(message: str) -> str:
        """A long wait by design."""
        await asyncio.sleep(FAST * 6)
        return "answer"

    tool.__name__ = name
    client = ScriptedClient([("calls", [(name, {"message": "x"})]), ("text", "Done.")])
    view = _view(Agent(client=client, name="probe", tools=[tool]))
    assert _lines(asyncio.run(_run_with_queue(view))) == []


def test_no_queue_and_no_channel_sends_nothing_and_never_fails_the_call(flag_on):
    async def slow_read(part: str) -> str:
        """A slow read."""
        await asyncio.sleep(FAST * 3)
        return "fine"

    client = ScriptedClient([("calls", [("slow_read", {"part": "a"})]), ("text", "Done.")])
    view = _view(Agent(client=client, name="probe", tools=[slow_read]))
    result = asyncio.run(view.run("hi"))
    assert "Done." in result.text


# ── 6. WhatsApp: the channel's early message ────────────────────────────────


def test_whatsapp_gets_the_line_as_its_early_message_once(flag_on):
    from acb_skills.whatsapp_ui import whatsapp_run

    sent: list[str] = []

    async def notify(text: str) -> bool:
        sent.append(text)
        return True

    async def slow_read(part: str) -> str:
        """A slow read."""
        await asyncio.sleep(FAST * 6)
        return part

    client = ScriptedClient([
        ("calls", [("slow_read", {"part": "a"})]),
        ("calls", [("slow_read", {"part": "b"})]),
        ("text", "Done."),
    ])
    view = _view(Agent(client=client, name="orchestrator", tools=[slow_read]))

    async def run() -> Any:
        with whatsapp_run("orchestrator", notify=notify) as wa:
            await view.run("hi")
            return wa

    wa = asyncio.run(run())
    assert sent == [pn.LONG_READ_TEXT], "one early message in a reply"
    assert wa.notified is True, "the channel's own 20 s line then stays quiet"


# ── 7. The fold and the profile mark ────────────────────────────────────────


def test_the_fold_never_keeps_the_line_and_it_is_no_answer_event():
    from gateway.chat_fold import fold_run_events

    line = {"type": "CUSTOM", "name": pn.EVENT, "value": {"text": pn.LONG_READ_TEXT}}
    assert executor._is_answer_event(line) is False
    folded = fold_run_events([
        {"type": "TEXT_MESSAGE_CONTENT", "messageId": "m", "delta": "31 totals."},
        line,
    ])
    assert folded is not None and folded["custom_events"] == []
    assert fold_run_events([line]) is None


# ── 8. The stream survives a tool past the bare idle cap ────────────────────


class _SlowToolAgent:
    """A native-MAF-shaped agent. Its stream starts a call, runs the REAL
    middleware around a tool that sleeps *tool_s*, then answers."""

    def __init__(self, tool_s: float, *, call: bool = True) -> None:
        self.name = "test-agent"
        self.tools: list[Any] = []
        self.default_options: dict[str, Any] = {}
        self.tool_s = tool_s
        self.call = call

    def run(self, *_a: Any, **_k: Any) -> Any:
        return self._stream()

    async def _stream(self) -> Any:
        if self.call:
            yield SimpleNamespace(role="assistant", message_id="m1", contents=[SimpleNamespace(
                type="function_call", name="crm_aggregate", arguments='{"entity": "deals"}',
                call_id="call_1")])

        async def call_next() -> None:
            await asyncio.sleep(self.tool_s)

        ctx = SimpleNamespace(function=SimpleNamespace(name="crm_aggregate"),
                              metadata={"call_id": "call_1"})
        await pn.ProgressNotice(after_s=FAST).process(ctx, call_next)
        if self.call:
            yield SimpleNamespace(role="tool", message_id="m2", contents=[SimpleNamespace(
                type="function_result", call_id="call_1", result="31 totals", exception=None)])
        yield SimpleNamespace(role="assistant", message_id="m3", contents=[
            SimpleNamespace(type="text", text="You have 31 totals.")])

    async def __aenter__(self) -> _SlowToolAgent:
        return self

    async def __aexit__(self, *_a: Any) -> bool:
        return False


def _drive(monkeypatch, tmp_path, agent: Any) -> list[dict[str, Any]]:
    from acb_common import get_settings
    from orchestrator.watchdog import WatchdogPolicy

    from tests.unit.test_run_agent_stream_e2e import _LoadedStub, _parse_frames

    monkeypatch.setattr(get_settings(), "agents_clone_dir", str(tmp_path / "agents"))
    # The bare idle cap is far shorter than the tool. Only the budget of an
    # open tool keeps the run alive.
    monkeypatch.setattr(executor, "_WATCHDOG", WatchdogPolicy(
        idle=FAST * 4, tool_open=30.0, hitl_pending=30.0, tool_execution=30.0))

    class _Loaded(_LoadedStub):
        def build_agents(self_inner) -> list[Any]:
            return [agent]

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
            "test-agent-unregistered", {"message": "add up the deals"},
        ):
            out.append(line)
        return out

    from acb_skills.write_artifact import bind_artifact_context

    try:
        return _parse_frames(asyncio.run(_collect()))
    finally:
        bind_artifact_context()
        executor._RUN_QUEUES.clear()
        executor._pending_user_input.clear()


def test_the_stream_survives_a_tool_past_the_idle_cap_and_shows_the_line(
        monkeypatch, tmp_path, flag_on):
    events = _drive(monkeypatch, tmp_path, _SlowToolAgent(tool_s=FAST * 12))
    types = [e.get("type") for e in events]
    assert "RUN_ERROR" not in types, events
    assert types[-1] == "RUN_FINISHED"
    start = types.index("TOOL_CALL_START")
    result = types.index("TOOL_CALL_RESULT")
    lines = [i for i, e in enumerate(events)
             if e.get("type") == "CUSTOM" and e.get("name") == pn.EVENT]
    assert len(lines) == 1 and start < lines[0] < result
    assert events[lines[0]]["value"] == {"text": pn.LONG_READ_TEXT, "tool_call_id": "call_1"}


def test_the_same_wait_with_no_tool_open_trips_the_idle_cap(monkeypatch, tmp_path, flag_on):
    """The control of the test above: the cap is real, so the open tool's
    budget is what keeps a long read alive."""
    events = _drive(monkeypatch, tmp_path, _SlowToolAgent(tool_s=FAST * 12, call=False))
    errors = [e for e in events if e.get("type") == "RUN_ERROR"]
    assert errors and errors[0].get("code") == "timeout"
