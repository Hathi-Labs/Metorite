"""HRD-1b (task 3230): no model of a batch path reads MAF's "not found" text.

HRD-1 (PR 2290) gave the stream run of the orchestrator a typed answer for a
call of a tool name that the run does not hold. The batch path did not have
it: a WhatsApp run, a delegated agent, a worker of ``spawn_subagents`` and a
self-anneal retry. Their model read ``Error: Requested function "x" not
found.``, and it could quote that text to the member. That is the symptom
that the owner saw ("Function query_inbox not found").

ONE hook, ``executor._unknown_tool_view``, makes the provider for every
path. ``_log_request_tools_view`` calls it for the stream run of each agent
(HRD-1c), and ``_assemble_batch_agents`` calls it for each batch run. A
sub-agent runs through the batch run. A run with a parent, or with no chat, is a step
(``step=True``). Its answer names a tool of the request, or it is
``unknown_tool.NOT_IN_STEP``. It never names a pack and never asks a loader.
A top-level run of a chat (WhatsApp) gets the HRD-1 text as the stream run
gets it with ``CHAT_TOOL_AUTOLOAD`` off.

The tests run the REAL ``run_agent_stream``, ``run_agent``,
``_assemble_batch_agents``, ``_run_with_maf_agent`` and
``_run_sub_agent_streaming``. ``CHAT_TOOL_AUTOLOAD`` stays dark on each path:
the batch tests run with the flag on too, and nothing loads. Only the load, the
store, the integrations and the platform tool lists are stubbed. A scripted
model client reads each request as the OpenAI client sends it.

Mutations, each red:

* detach the view (``_unknown_tool_view`` returns *agents*)
  -> every path test, and ``test_with_the_view_detached_the_raw_text_comes_back``
* drop ``step=`` at the batch run       -> test_a_delegated_run_gets_the_step_text
* drop ``step=`` at the retry           -> test_the_retry_keeps_the_mode_of_the_first_attempt
* read the loader in a step              -> test_a_step_never_asks_the_loader
* name a gone pack in a step             -> test_a_step_never_names_a_pack
* drop the providers in ``_tier2_tool_view`` -> test_tier_2_of_the_orchestrator_keeps_the_answer

Run: ``uv run pytest tests/unit/test_unknown_tool_batch_paths.py -q``.
"""
from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import json
import re
import types
from collections.abc import AsyncIterable, Iterator
from typing import Any

import pytest

pytest.importorskip("agent_framework", reason="MAF not installed")

from acb_skills import tool_packs as tp
from acb_skills import unknown_tool as ut
from acb_skills.tool_guard import tool_name
from agent_framework import (
    Agent,
    BaseChatClient,
    ChatMiddlewareLayer,
    ChatResponse,
    ChatResponseUpdate,
    Content,
    FunctionInvocationLayer,
    Message,
    tool,
)

AGENT = "agent-hrd1b"
ORG = "0a0a0a0a-0000-4000-8000-0000000003b1"
MEMBER = "meera@acme.test"
SESSION = "chat-hrd1b"
#: The words that only MAF writes. No text that a model reads may hold them.
_MAF = re.compile(r'requested function|not found\.|function "', re.I)


# ── The scripted model, as the OpenAI client sends each request ─────────────


def _wire_client() -> Any:
    from agent_framework.openai import OpenAIChatCompletionClient

    return OpenAIChatCompletionClient(model="m", api_key="k", base_url="http://127.0.0.1:9/v1")


class _Scripted(FunctionInvocationLayer, ChatMiddlewareLayer, BaseChatClient):
    """The layers of ``OpenAIChatCompletionClient``, with a scripted model.
    *fail* raises at the first request, as a dropped connection does."""

    def __init__(self, script: list[tuple[Any, ...]], fail: bool = False) -> None:
        super().__init__()
        self.script = list(script)
        self.fail = fail
        self.wire: list[list[dict[str, Any]]] = []
        self.tools: list[list[str]] = []
        self._out = _wire_client()

    def _inner_get_response(self, *, messages, stream, options, **kwargs):  # type: ignore[override]
        if self.fail:
            raise RuntimeError("connection reset by peer")
        self.wire.append(self._out._prepare_messages_for_openai(list(messages)))
        self.tools.append([tool_name(t) for t in (options or {}).get("tools") or []])
        step = self.script.pop(0)
        n = len(self.wire)
        if step[0] == "call":
            content = Content.from_function_call(f"c{n}", step[1], arguments=json.dumps(step[2]))
        else:
            content = Content.from_text(step[1])
        if stream:
            async def _gen() -> AsyncIterable[ChatResponseUpdate]:
                yield ChatResponseUpdate(
                    contents=[content], role="assistant", response_id=f"r{n}", message_id=f"m{n}")
            return self._build_response_stream(_gen())

        async def _one() -> ChatResponse:
            return ChatResponse(messages=[Message("assistant", [content])], response_id=f"r{n}")
        return _one()


def _tool_texts(client: _Scripted) -> list[str]:
    """The text of each tool result that the model read, in order."""
    return [str(m.get("content")) for request in client.wire for m in request
            if m.get("role") == "tool"]


def _assert_no_maf_text(client: _Scripted) -> None:
    for request in client.wire:
        for message in request:
            assert not _MAF.search(json.dumps(message, default=str)), message


async def read_inbox(query: str = "") -> str:
    """The agent's own tool: read the member's inbox."""
    return "3 emails match."


# ── The batch run, real from run_agent to the model client ──────────────────


@pytest.fixture
def batch(tmp_path, monkeypatch):
    """The REAL batch path of one agent whose model is scripted.

    ``state["script"]`` is the model's script. ``state["fail_first"]`` makes
    the first model call fail, so the self-anneal retry runs. Each build
    makes a new client, and ``state["clients"]`` keeps them in order."""
    import acb_memory
    import acb_skills.integrations as integ
    import acb_skills.loader as loader
    from acb_common import get_settings
    from gateway.routes import agent as agent_routes
    from orchestrator import _tool_injection as ti
    from orchestrator import app_tools, executor, mutation, workflow_tools

    clone = tmp_path / "agents" / "repos" / AGENT
    clone.mkdir(parents=True)
    settings = get_settings()
    monkeypatch.setattr(settings, "agents_clone_dir", str(tmp_path / "agents"))
    monkeypatch.setattr(settings, "custom_apps_root", str(tmp_path / "agents" / "apps"))
    monkeypatch.setattr(agent_routes, "_AGENT_REGISTRY", [
        {"name": AGENT, "repo_name": f"Org/{AGENT}", "local_path": str(clone)},
    ])
    monkeypatch.setattr(agent_routes, "_load_dynamic_agents", lambda: [])

    state: dict[str, Any] = {"script": [], "fail_first": False, "clients": []}

    def build() -> list[Any]:
        fail = state["fail_first"] and not state["clients"]
        client = _Scripted(list(state["script"]), fail=fail)
        state["clients"].append(client)
        return [Agent(client=client, name=AGENT, tools=[read_inbox])]

    @contextlib.contextmanager
    def load(name: str, **_kw: Any):
        yield types.SimpleNamespace(
            agent_dir=clone, agent_name=name, config={"name": AGENT},
            build_agents=build, cleanup=lambda: None,
        )

    def integrations(m, o, _s, *, is_authorized=None):
        return {}, {}

    async def authorizer(_payload, _thread=None, *, session_user=None):
        return None

    async def _quiet(*_a, **_k):
        return ""

    async def _none(*_a, **_k):
        return None

    async def _zero(*_a, **_k):
        return 0

    real_sleep = asyncio.sleep

    async def _fast_sleep(*_a, **_k):
        # No wait for the self-anneal back-off, but each sleep still yields,
        # so a polling loop of the stream run cannot spin.
        await real_sleep(0)

    # A run that fails past its retries asks no model and opens no pull request.
    monkeypatch.setattr(executor, "_llm_recovery", _none)
    monkeypatch.setattr(mutation, "attempt_self_mutation", _none)
    monkeypatch.setattr(executor, "load_agent", load)
    monkeypatch.setattr(loader, "load_agent", load)
    monkeypatch.setattr(executor, "build_integrations", integrations)
    monkeypatch.setattr(integ, "build_integrations", integrations)
    monkeypatch.setattr(executor, "_integration_authorizer", authorizer)
    monkeypatch.setattr(executor, "_install_push_guard", _quiet)
    monkeypatch.setattr(executor, "_get_current_head", _quiet)
    monkeypatch.setattr(executor, "_detect_agent_commits", _none)
    monkeypatch.setattr(executor, "_assert_may_run_agent", _none)
    monkeypatch.setattr(acb_memory, "rehydrate_workspace", _zero)
    monkeypatch.setattr(asyncio, "sleep", _fast_sleep)
    monkeypatch.setattr(ti, "_collect_injectable_platform_tools", lambda *_a, **_k: [])
    monkeypatch.setattr(ti, "_load_disabled_skill_families", lambda *_a, **_k: frozenset())
    monkeypatch.setattr(app_tools, "load_app_action_tools", lambda *_a, **_k: [])
    monkeypatch.setattr(workflow_tools, "load_workflow_tools", lambda *_a, **_k: [])
    return state


def _ran(state: dict[str, Any]) -> _Scripted:
    """The client of the agent that the run called. A sub-run builds its
    agents twice, and only the batch run's agents call the model."""
    (client,) = [c for c in state["clients"] if c.wire]
    return client


@contextlib.contextmanager
def _parent() -> Iterator[None]:
    """The frame of the orchestrator's stream run, which delegates. It binds
    the tenant, as the stream run does."""
    from acb_common.db import bind_tenant, release_tenant
    from acb_skills.write_artifact import artifact_context_scope, bind_artifact_context

    token = bind_tenant(ORG)
    try:
        with artifact_context_scope():
            bind_artifact_context(run_id="r-parent", session_id=SESSION, member=MEMBER,
                                  agent_name="orchestrator")
            yield
    finally:
        release_tenant(token)


async def _run_agent(*, thread_id: str | None = None) -> dict[str, Any]:
    from orchestrator import executor

    return await executor.run_agent(
        AGENT, {"message": "check my inbox"}, run_id="r-hrd1b", thread_id=thread_id,
        organization_id=ORG,
    )


_UNKNOWN = [("call", "query_inbox", {"query": "Priya"}), ("text", "Done.")]


def _autoload(monkeypatch, on: bool) -> None:
    """``CHAT_TOOL_AUTOLOAD`` on or off for the run. A batch run has no
    loader, and a step never asks one, so the answer is the same."""
    monkeypatch.setattr(ut, "autoload_enabled", lambda: on)


@pytest.mark.parametrize("autoload", [False, True], ids=["dark", "flag-on"])
async def test_a_top_level_chat_run_gets_the_hrd1_text(batch, monkeypatch, autoload) -> None:
    """A WhatsApp run: a chat, no parent. It has no loader, so the text is
    HRD-1's with the auto-load off, also with the flag on. Mutation: detach
    the view → the model reads MAF's text → red."""
    _autoload(monkeypatch, autoload)
    batch["script"] = _UNKNOWN
    out = await _run_agent(thread_id="wa-thread-1")
    client = _ran(batch)
    assert _tool_texts(client) == [ut.NOT_HERE.format(name="query_inbox")]
    _assert_no_maf_text(client)
    assert [(c["name"], c["result"], c["success"]) for c in out["tool_calls"]] == [
        ("query_inbox", ut.MEMBER_LINE, False)]
    assert out["answer"] == "Done."


async def test_a_run_with_no_chat_gets_the_step_text(batch) -> None:
    """A run with no chat (a workflow step, a draft) answers no member."""
    batch["script"] = _UNKNOWN
    out = await _run_agent()
    client = _ran(batch)
    assert _tool_texts(client) == [ut.NOT_IN_STEP.format(name="query_inbox")]
    _assert_no_maf_text(client)
    assert [c["result"] for c in out["tool_calls"]] == [ut.MEMBER_LINE]


@pytest.mark.parametrize("autoload", [False, True], ids=["dark", "flag-on"])
async def test_a_delegated_run_gets_the_step_text(batch, monkeypatch, autoload) -> None:
    """``delegate_to_agent``, ``call_agent`` and a worker run here under the
    parent's frame. With the flag on too, the answer is the typed text only.
    Mutation: pass no ``step=`` at the batch run → NOT_HERE → red."""
    _autoload(monkeypatch, autoload)
    batch["script"] = _UNKNOWN
    with _parent():
        out = await _run_agent()
    client = _ran(batch)
    assert _tool_texts(client) == [ut.NOT_IN_STEP.format(name="query_inbox")]
    _assert_no_maf_text(client)
    assert [c["result"] for c in out["tool_calls"]] == [ut.MEMBER_LINE]


async def test_a_step_names_the_closest_tool_that_it_holds(batch) -> None:
    batch["script"] = [("call", "read_inbx", {}), ("text", "Done.")]
    with _parent():
        await _run_agent()
    assert _tool_texts(_ran(batch)) == [
        ut.SUGGEST_LIVE.format(name="read_inbx", near="read_inbox")]


@pytest.mark.parametrize("parent", [True, False], ids=["delegated", "chat"])
async def test_the_retry_keeps_the_mode_of_the_first_attempt(batch, parent: bool) -> None:
    """The first model call drops, and the self-anneal retry assembles
    again. Mutation: drop ``step=inputs.step`` at the retry → the delegated
    retry reads NOT_HERE → red. Detach the view → MAF's text → red."""
    batch["script"] = _UNKNOWN
    batch["fail_first"] = True
    with _parent() if parent else contextlib.nullcontext():
        out = await _run_agent(thread_id=None if parent else "wa-thread-2")
    assert len(batch["clients"]) == 2, "the retry built the agents again"
    client = _ran(batch)
    assert client is batch["clients"][1]
    want = ut.NOT_IN_STEP if parent else ut.NOT_HERE
    assert _tool_texts(client) == [want.format(name="query_inbox")]
    _assert_no_maf_text(client)
    assert [c["result"] for c in out["tool_calls"]] == [ut.MEMBER_LINE]


@pytest.mark.parametrize("autoload", [False, True], ids=["dark", "flag-on"])
async def test_the_sub_run_stream_shows_the_member_line(batch, monkeypatch, autoload) -> None:
    """``_run_sub_agent_streaming``, the seam of each specialist tool, of
    ``call_agent`` and of ``delegate_to_agent`` with a queue. Its native
    branch runs ``run_agent``. The model reads the step text, with the flag
    on too, the trail shows the member line, and no event holds MAF's text."""
    from orchestrator import executor

    _autoload(monkeypatch, autoload)
    batch["script"] = _UNKNOWN
    queue: asyncio.Queue = asyncio.Queue()
    with _parent():
        text = await executor._run_sub_agent_streaming(
            AGENT, "check my inbox", "r-sub", queue)
    assert text == "Done."
    client = _ran(batch)
    assert _tool_texts(client) == [ut.NOT_IN_STEP.format(name="query_inbox")]
    _assert_no_maf_text(client)
    events: list[dict[str, Any]] = []
    while not queue.empty():
        events.append(queue.get_nowait())
    results = [e for e in events if e.get("type") == "SUB_AGENT_TOOL_CALL_RESULT"]
    assert [e.get("content") for e in results] == [ut.MEMBER_LINE], events
    for event in events:
        assert not _MAF.search(json.dumps(event, default=str)), event


# ── The differential: what the view changes, and what it does not ──────────


async def test_with_the_view_detached_the_raw_text_comes_back(batch, monkeypatch) -> None:
    """The old behaviour, to show that the tests above read the right wire.
    With the view detached, the model reads MAF's own text."""
    from orchestrator import executor

    monkeypatch.setattr(executor, "_unknown_tool_view", lambda agents, *, step: agents)
    batch["script"] = _UNKNOWN
    with _parent():
        await _run_agent()
    (text,) = _tool_texts(_ran(batch))
    assert ut.is_raw(text) == "query_inbox", text


@pytest.mark.parametrize("parent", [True, False], ids=["delegated", "chat"])
async def test_a_turn_with_no_unknown_call_is_byte_identical(
    batch, monkeypatch, parent: bool,
) -> None:
    """The same turn with and without the view: the requests, the tools and
    the result are equal."""
    from orchestrator import executor

    script = [("call", "read_inbox", {"query": "Priya"}), ("text", "Done.")]

    async def once() -> tuple[Any, ...]:
        batch["script"] = list(script)
        batch["clients"].clear()
        with _parent() if parent else contextlib.nullcontext():
            out = await _run_agent(thread_id=None if parent else "wa-thread-3")
        client = _ran(batch)
        return client.wire, client.tools, out["tool_calls"], out["answer"]

    with_view = await once()
    monkeypatch.setattr(executor, "_unknown_tool_view", lambda agents, *, step: agents)
    without = await once()
    assert with_view == without
    assert with_view[2][0]["result"] == "3 emails match."


# ── The step: no loader, no pack ────────────────────────────────────────────


def _record(gone: tuple[str, ...] = ()) -> tp.PackRun:
    turn = tp.PackTurn(ORG, SESSION, tp._member_part(MEMBER), 3, (), "", True)
    choice = tp.choose(turn, allowed=frozenset(), base_tools=0)
    return dataclasses.replace(tp.PackRun.of(turn, choice, []), gone=tuple(gone))


async def _view_run(script, providers: list[Any], ctx: dict[str, Any]) -> _Scripted:
    from acb_common.db import bind_tenant, release_tenant
    from acb_skills.write_artifact import artifact_context_scope, bind_artifact_context
    from orchestrator._native_run_context import agent_with_providers

    client = _Scripted(script)
    view = agent_with_providers(Agent(client=client, name=AGENT, tools=[read_inbox]), providers)
    token = bind_tenant(ORG)
    try:
        with artifact_context_scope():
            bind_artifact_context(run_id="r-step", session_id=SESSION, member=MEMBER,
                                  agent_name=AGENT, **ctx)
            await view.run([Message("user", ["check my inbox"])])
    finally:
        release_tenant(token)
    return client


async def test_a_step_never_asks_the_loader(monkeypatch) -> None:
    """A loader that offers the email pack sits on the view, and the
    auto-load is on. The step asks it nothing, loads nothing and names no
    pack. Mutation: keep the loader in a step → it loads «email» → red."""
    monkeypatch.setattr(ut, "autoload_enabled", lambda: True)
    asks: list[str] = []

    async def build(slug: str, ctx: Any, holds: frozenset[str]) -> Any:
        asks.append(slug)
        return types.SimpleNamespace(tools=(tool(read_inbox, name="query_inbox"),), held=False)

    loader = tp.LoadSkillTools(offered=("email",), run_key="key-step", record=_record(),
                               build=build)
    real = loader.process

    async def counting(context: Any, call_next: Any) -> None:
        asks.append("process")
        await real(context, call_next)

    loader.process = counting  # type: ignore[method-assign]
    client = await _view_run(
        _UNKNOWN, [tp.LoadSkillToolsProvider(loader), ut.UnknownToolProvider(step=True)],
        {tp.PACK_RUN_KEY: loader.run_key, tp.PACKS_KEY: loader.record})
    assert asks == [], asks
    assert loader.record.holds() == ()
    (text,) = [t for t in _tool_texts(client) if t.startswith("Not run")]
    assert text == ut.NOT_IN_STEP.format(name="query_inbox")
    assert "«" not in text and "load_skill_tools" not in text


async def test_a_step_never_names_a_pack() -> None:
    """A record whose email pack left the chat would give NEEDS_PACK on the
    stream run. A step reads no record. Mutation: read the record in a step
    → the text names the email tools → red."""
    record = _record(gone=("email",))
    stream = await _view_run(_UNKNOWN, [ut.UnknownToolProvider()], {tp.PACKS_KEY: record})
    step = await _view_run(_UNKNOWN, [ut.UnknownToolProvider(step=True)],
                           {tp.PACKS_KEY: record})
    assert _tool_texts(stream) == [ut.NEEDS_PACK.format(
        name="query_inbox", slug=tp._label("email"))], "the stream run is as before"
    assert _tool_texts(step) == [ut.NOT_IN_STEP.format(name="query_inbox")]


def test_the_step_text_is_plain() -> None:
    """It starts as each HRD-1 text does, orders no call and names no pack.
    Each sentence is 25 words or fewer (STE, a description)."""
    text = ut.NOT_IN_STEP.format(name="query_inbox")
    assert text == ("Not run: query_inbox is not available in this step. "
                    "Use only the tools that you hold.")
    assert ut.NOT_IN_STEP in ut.TEXTS
    assert not re.search(r"\bagain\b|\bcall\b|load_skill_tools|«|pack", text, re.I)
    for sentence in filter(None, (s.strip() for s in text.split("."))):
        assert len(sentence.split()) <= 25, sentence
    assert ut.answer_for("query_inbox", live=["read_inbox"], offered=("email",),
                         gone=("email",), step=True) == text


# ── Tier 2 of the orchestrator: the view of the stream run carries it ───────


async def test_tier_2_of_the_orchestrator_keeps_the_answer() -> None:
    """Tier 2 runs ``agents[0]`` after ``_log_request_tools_view``, through
    ``_tier2_tool_view``, ``_agent_for_run`` and ``_with_tier_policy``. Each
    is a copy that keeps the providers, so the HRD-1 answer stays. Its shim
    wraps real tools only, so an unknown name draws no shim row. Mutation:
    drop the providers in ``_tier2_tool_view`` → MAF's text → red."""
    from orchestrator import executor as ex

    rows: list[str] = []

    def make_shim(fn: Any, name: str) -> Any:
        async def shim(*a: Any, **k: Any) -> Any:
            rows.append(name)
            return await fn(*a, **k)
        return shim

    client = _Scripted(_UNKNOWN)
    agent = Agent(client=client, name="orchestrator", tools=[read_inbox])
    view = ex._log_request_tools_view([agent], "orchestrator")[0]
    tier2 = ex._with_tier_policy(ex._agent_for_run(ex._tier2_tool_view(view, make_shim), None),
                                 None)
    assert type(tier2.context_providers[-1]) is ut.UnknownToolProvider
    await tier2.run("check my inbox")
    assert _tool_texts(client) == [ut.NOT_HERE.format(name="query_inbox")]
    _assert_no_maf_text(client)
    assert rows == [], "no shim row for a name that the run does not hold"


async def test_the_stream_run_of_the_orchestrator_reads_the_same_hook(batch) -> None:
    """The REAL ``run_agent_stream`` of the orchestrator, the HRD-1 path. It
    reaches the hook through ``_log_request_tools_view``. With no pack on
    there is no loader, and ``CHAT_TOOL_AUTOLOAD`` is off by default: the
    model reads the typed text, and the trail shows the member line."""
    from acb_common.db import bind_tenant, release_tenant
    from acb_skills.write_artifact import bind_artifact_context
    from orchestrator import executor

    from tests.unit._native_maf_harness import parse_frames

    assert ut.autoload_enabled() is False, "the auto-load is dark by default"
    batch["script"] = _UNKNOWN
    token = bind_tenant(ORG)
    try:
        lines = [line async for line in executor.run_agent_stream(
            "orchestrator", {"message": "check my inbox"}, run_id="r-stream",
            thread_id="chat-stream", shows_cards=True, organization_id=ORG)]
    finally:
        release_tenant(token)
        bind_artifact_context()
        executor._RUN_QUEUES.clear()
        executor._pending_user_input.clear()
    client = _ran(batch)
    assert _tool_texts(client) == [ut.NOT_HERE.format(name="query_inbox")]
    _assert_no_maf_text(client)
    events = parse_frames(lines)
    results = [e for e in events if e.get("type") == "TOOL_CALL_RESULT"]
    assert [e.get("content") for e in results] == [ut.MEMBER_LINE], events
    for event in events:
        assert not _MAF.search(json.dumps(event, default=str)), event


def test_one_hook_serves_every_path() -> None:
    """ONE hook makes the provider: ``_unknown_tool_view``. The stream view
    of the orchestrator and the batch assembly call it, and a sub-agent runs
    through the batch run. The batch assembly calls it after the injection
    and before the MCP servers, as the stream path does."""
    import inspect

    from orchestrator import executor as ex

    src = inspect.getsource(ex)
    assert src.count("UnknownToolProvider(") == 1, "one hook makes the provider"
    assert "UnknownToolProvider(" in inspect.getsource(ex._unknown_tool_view)
    callers = [name for name, fn in inspect.getmembers(ex, inspect.isfunction)
               if fn.__module__ == ex.__name__ and name != "_unknown_tool_view"
               and "_unknown_tool_view(" in inspect.getsource(fn)]
    assert sorted(callers) == ["_assemble_batch_agents", "_log_request_tools_view"], callers
    body = inspect.getsource(ex._assemble_batch_agents)
    at = body.index("_unknown_tool_view(agents, step=step)")
    assert body.index("_log_agent_tools_resolved(") < at < body.index("_inject_mcp_servers(")
    assert inspect.getsource(ex._run_agent_inner).count("step=_answer_step") == 3
    assert "step=inputs.step" in inspect.getsource(ex._self_anneal)
    # The sub-agent seam runs a native agent through the batch run.
    assert "result = await run_agent(" in inspect.getsource(ex._run_sub_agent_streaming)
