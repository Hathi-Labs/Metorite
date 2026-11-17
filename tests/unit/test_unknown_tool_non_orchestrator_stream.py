"""HRD-1c (task 3679): the stream run of an agent that is not the orchestrator
answers an unknown tool name with the HRD-1 text.

HRD-1 gave the stream run of the orchestrator a typed answer for a call of a
tool name that the run does not hold, and HRD-1b gave it to each batch run.
The stream run of another agent still let its model read MAF's own text,
``Error: Requested function "x" not found.``: a member who writes in the
Email app chat (``email-assistant``) or the Projects rail
(``projects-assistant``), or who names an agent in a room. The model could
quote that text to the member ("Function query_inbox not found").

ONE hook, ``executor._unknown_tool_view``, makes the provider. Its stream
caller, ``_log_request_tools_view``, now calls it for the stream run of each
agent. The log view of OA-8f stays the orchestrator's only. A stream run is
a top-level chat run, never a step, so its text is the chat text of HRD-1.
An agent that is not the orchestrator holds no loader, so its answer names
only a tool of its own request, and ``CHAT_TOOL_AUTOLOAD`` loads nothing.

The tests run the REAL ``run_agent_stream`` from the run boundary to a
scripted model client. Only the load, the integrations and the platform tool
lists are stubbed. ``executor._llm_recovery`` and
``mutation.attempt_self_mutation`` are stubbed too, so a failed run asks no
model and opens no pull request. A tripwire (``_no_network``) records each
socket, DNS and database connect of the run, and each test fails on one.

Mutations, each red:

* put back ``agent_name != _PACK_AGENT`` before the hook in
  ``_log_request_tools_view`` -> every run test here, and
  ``test_unknown_tool_answer.py::test_the_executor_puts_the_answer_on_the_orchestrator_view_last``
* pass ``step=True`` for the stream run -> test_a_direct_chat_gets_the_chat_text
* detach the view (``_unknown_tool_view`` returns *agents*)
  -> test_with_the_view_detached_the_raw_text_comes_back shows MAF's text
* drop the providers in ``_tier2_tool_view`` -> the two Tier 2 tests

Run: ``uv run pytest tests/unit/test_unknown_tool_non_orchestrator_stream.py -q``.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import re
import socket
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
)

ORG = "0a0a0a0a-0000-4000-8000-0000000003c1"
#: The two agents that a member chats with directly (Email app, Projects rail).
AGENTS = ("email-assistant", "projects-assistant")
#: The words that only MAF writes. No text that a model reads may hold them.
_MAF = re.compile(r'requested function|not found\.|function "', re.I)


# ── The tripwire: the run reaches no database, no Redis and no model ────────


@pytest.fixture(autouse=True)
def _no_network(monkeypatch) -> Iterator[list[str]]:
    """Record each outbound connect of the test, and fail the test on one.

    The executor turns most faults of a side read into a log line, so a
    tripwire that only raises proves nothing. This one records each try
    first. The event loop of Windows makes its own socket pair on a
    loopback port that it listens on, and that connect is allowed."""
    attempts: list[str] = []
    listening: set[tuple[Any, ...]] = set()
    real_listen = socket.socket.listen
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def _refuse(where: str, addr: Any) -> None:
        if isinstance(addr, tuple) and tuple(addr[:2]) in listening:
            return
        attempts.append(f"{where}:{addr!r}")
        raise ConnectionRefusedError(f"tripwire: {where} {addr!r}")

    def listen(self: socket.socket, *args: Any) -> Any:
        with contextlib.suppress(OSError):
            listening.add(tuple(self.getsockname()[:2]))
        return real_listen(self, *args)

    def connect(self: socket.socket, addr: Any) -> Any:
        _refuse("connect", addr)
        return real_connect(self, addr)

    def connect_ex(self: socket.socket, addr: Any) -> Any:
        _refuse("connect_ex", addr)
        return real_connect_ex(self, addr)

    def getaddrinfo(host: Any, *_a: Any, **_k: Any) -> Any:
        attempts.append(f"dns:{host!r}")
        raise socket.gaierror(f"tripwire: dns {host!r}")

    monkeypatch.setattr(socket.socket, "listen", listen)
    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)

    # The proactor loop of Windows connects through ConnectEx, not through
    # ``socket.connect``. An IP literal skips the DNS step.
    from asyncio import base_events

    async def create_connection(self: Any, *_a: Any, **k: Any) -> Any:
        _refuse("create_connection", (_a[1:3] if len(_a) > 2 else k.get("host")))
        raise AssertionError("unreachable")

    monkeypatch.setattr(base_events.BaseEventLoop, "create_connection", create_connection)
    with contextlib.suppress(ImportError):
        from asyncio import proactor_events

        real_sock_connect = proactor_events.BaseProactorEventLoop.sock_connect

        async def sock_connect(self: Any, sock: Any, address: Any) -> Any:
            _refuse("sock_connect", address)
            return await real_sock_connect(self, sock, address)

        monkeypatch.setattr(proactor_events.BaseProactorEventLoop, "sock_connect", sock_connect)

    # libpq connects in C, so no Python socket sees a Postgres connect.
    def _db(name: str) -> Any:
        def refuse(*_a: Any, **_k: Any) -> Any:
            attempts.append(f"db:{name}")
            raise ConnectionRefusedError(f"tripwire: {name}")
        return refuse

    with contextlib.suppress(ImportError):
        import psycopg2

        monkeypatch.setattr(psycopg2, "connect", _db("psycopg2.connect"))
    with contextlib.suppress(ImportError):
        import psycopg

        async def _async_db(_cls: Any, *_a: Any, **_k: Any) -> Any:
            attempts.append("db:psycopg.AsyncConnection.connect")
            raise ConnectionRefusedError("tripwire: psycopg.AsyncConnection.connect")

        monkeypatch.setattr(psycopg, "connect", _db("psycopg.connect"))
        monkeypatch.setattr(psycopg.Connection, "connect",
                            classmethod(lambda _cls, *a, **k: _db("psycopg.Connection.connect")()))
        monkeypatch.setattr(psycopg.AsyncConnection, "connect", classmethod(_async_db))
    with contextlib.suppress(ImportError):
        import asyncpg

        monkeypatch.setattr(asyncpg, "connect", _db("asyncpg.connect"))

    yield attempts
    assert attempts == [], f"the run tried to reach outside the process: {attempts}"


def test_the_tripwire_catches_a_connect(_no_network) -> None:
    """The tripwire itself: a connect to a port that nothing in the test
    opened is recorded, and the real connect never starts. Without this, an
    empty record proves nothing."""
    with socket.socket() as sock, pytest.raises(ConnectionRefusedError):
        sock.connect(("127.0.0.1", 9))
    with pytest.raises(socket.gaierror):
        socket.getaddrinfo("api.deepseek.com", 443)
    psycopg = pytest.importorskip("psycopg")
    with pytest.raises(ConnectionRefusedError):
        psycopg.connect("postgresql://acb@127.0.0.1:9/acb")
    with pytest.raises(ConnectionRefusedError):
        psycopg.Connection.connect("postgresql://acb@127.0.0.1:9/acb")
    assert _no_network == [
        "connect:('127.0.0.1', 9)", "dns:'api.deepseek.com'", "db:psycopg.connect",
        "db:psycopg.Connection.connect"]
    _no_network.clear()


# ── The scripted model, as the OpenAI client sends each request ─────────────


def _wire_client() -> Any:
    from agent_framework.openai import OpenAIChatCompletionClient

    return OpenAIChatCompletionClient(model="m", api_key="k", base_url="http://127.0.0.1:9/v1")


class _Scripted(FunctionInvocationLayer, ChatMiddlewareLayer, BaseChatClient):
    """The layers of ``OpenAIChatCompletionClient``, with a scripted model.
    *fail_stream* raises at each streamed request, before any output, so the
    stream run falls back to Tier 2, which asks with no stream."""

    def __init__(self, script: list[tuple[Any, ...]], fail_stream: bool = False) -> None:
        super().__init__()
        self.script = list(script)
        self.fail_stream = fail_stream
        self.wire: list[list[dict[str, Any]]] = []
        self.tools: list[list[str]] = []
        self.streamed: list[bool] = []
        self._out = _wire_client()

    def _inner_get_response(self, *, messages, stream, options, **kwargs):  # type: ignore[override]
        if stream and self.fail_stream:
            raise RuntimeError("connection reset by peer")
        self.wire.append(self._out._prepare_messages_for_openai(list(messages)))
        self.tools.append([tool_name(t) for t in (options or {}).get("tools") or []])
        self.streamed.append(bool(stream))
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
    """The text of each tool result that the model read, in order. A result
    that a later request holds again counts once, by its call id."""
    seen: dict[str, str] = {}
    for request in client.wire:
        for message in request:
            if message.get("role") == "tool":
                seen.setdefault(str(message.get("tool_call_id")), str(message.get("content")))
    return list(seen.values())


def _assert_no_maf_text(client: _Scripted) -> None:
    assert client.wire, "the model was asked"
    for request in client.wire:
        for message in request:
            assert not _MAF.search(json.dumps(message, default=str)), message


# ── Each agent's own tools ──────────────────────────────────────────────────


async def search_emails(query: str = "") -> str:
    """The email assistant's own tool: search the member's mail."""
    return "3 emails match."


async def find_tasks(query: str = "") -> str:
    """The Projects assistant's own tool: find tasks."""
    return "2 tasks match."


#: Each agent with its own tool, a call of a name it does not hold, a near
#: name of its own tool, and that tool's result.
CASES = {
    "email-assistant": (search_emails, "query_inbox", "search_email", "3 emails match."),
    "projects-assistant": (find_tasks, "create_task", "find_task", "2 tasks match."),
}


# ── The stream run, real from run_agent_stream to the model client ──────────


@pytest.fixture
def stream(tmp_path, monkeypatch):
    """The REAL stream run of one agent whose model is scripted.

    ``state["script"]`` is the model's script. ``state["fail_stream"]`` makes
    each streamed request fail, so Tier 2 runs. Each build makes a new client,
    and ``state["clients"]`` keeps them in order."""
    import acb_memory
    import acb_skills.integrations as integ
    import acb_skills.loader as loader
    from acb_common import activity, get_settings
    from gateway.routes import agent as agent_routes
    from orchestrator import _tool_injection as ti
    from orchestrator import app_tools, executor, mutation, stream_relay, workflow_tools

    settings = get_settings()
    monkeypatch.setattr(settings, "agents_clone_dir", str(tmp_path / "agents"))
    monkeypatch.setattr(settings, "custom_apps_root", str(tmp_path / "agents" / "apps"))
    registry = []
    for name in AGENTS:
        clone = tmp_path / "agents" / "repos" / name
        clone.mkdir(parents=True)
        registry.append({"name": name, "repo_name": f"Org/{name}", "local_path": str(clone)})
    monkeypatch.setattr(agent_routes, "_AGENT_REGISTRY", registry)
    monkeypatch.setattr(agent_routes, "_load_dynamic_agents", lambda: [])

    state: dict[str, Any] = {"script": [], "fail_stream": False, "clients": []}

    @contextlib.contextmanager
    def load(name: str, **_kw: Any):
        own = CASES[name][0]

        def build() -> list[Any]:
            client = _Scripted(list(state["script"]), fail_stream=state["fail_stream"])
            state["clients"].append(client)
            return [Agent(client=client, name=name, tools=[own])]

        yield types.SimpleNamespace(
            agent_dir=tmp_path / "agents" / "repos" / name, agent_name=name,
            config={"name": name}, build_agents=build, cleanup=lambda: None,
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
        await real_sleep(0)

    # A run that fails asks no model for a repair and opens no pull request.
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
    # The side reads and writes of every stream run, which the tripwire found:
    # the relay and the activity feed in Redis, and two reads of the per-turn
    # context in Postgres. None of them changes what the model reads.
    for name in ("mark_active", "mark_inactive", "register_live_run",
                 "unregister_live_run", "push_event"):
        monkeypatch.setattr(stream_relay, name, _none)
    monkeypatch.setattr(activity, "_axadd", _none)
    monkeypatch.setattr(activity, "_axpresence", _none)
    monkeypatch.setattr(executor, "_read_org_display_name", lambda *_a, **_k: "")
    monkeypatch.setattr(executor, "_read_number_system", lambda *_a, **_k: "")
    return state


def _ran(state: dict[str, Any]) -> _Scripted:
    (client,) = [c for c in state["clients"] if c.wire]
    return client


async def _run_stream(agent_name: str, *, solo_room: bool = True,
                      run_id: str = "r-hrd1c") -> list[dict[str, Any]]:
    """One chat turn of *agent_name*, as the gateway route starts it: a chat,
    cards drawn, and the org from the server. *solo_room* False is a room
    where the member named the agent."""
    from acb_common.db import bind_tenant, release_tenant
    from acb_skills.write_artifact import bind_artifact_context
    from orchestrator import executor

    from tests.unit._native_maf_harness import parse_frames

    token = bind_tenant(ORG)
    try:
        lines = [line async for line in executor.run_agent_stream(
            agent_name, {"message": "find the mail from Priya"}, run_id=run_id,
            thread_id=f"chat-{agent_name}", shows_cards=True, organization_id=ORG,
            solo_room=solo_room)]
    finally:
        release_tenant(token)
        bind_artifact_context()
        executor._RUN_QUEUES.clear()
        executor._pending_user_input.clear()
    return parse_frames(lines)


@pytest.fixture
def made(monkeypatch) -> list[tuple[Any, bool]]:
    """The loader and the mode of each ``UnknownToolAnswer`` that a run made,
    and each call of a pack loader."""
    out: list[tuple[Any, bool]] = []
    real_init = ut.UnknownToolAnswer.__init__

    def init(self: Any, loader: Any = None, *, step: bool = False) -> None:
        out.append((loader, step))
        real_init(self, loader, step=step)

    async def process(self: Any, context: Any, call_next: Any) -> None:
        out.append(("loader-asked", False))
        await call_next()

    monkeypatch.setattr(ut.UnknownToolAnswer, "__init__", init)
    monkeypatch.setattr(tp.LoadSkillTools, "process", process)
    return out


def _results(events: list[dict[str, Any]]) -> list[Any]:
    return [e.get("content") for e in events if e.get("type") == "TOOL_CALL_RESULT"]


def _assert_no_maf_event(events: list[dict[str, Any]]) -> None:
    for event in events:
        assert not _MAF.search(json.dumps(event, default=str)), event


# ── 1. The answer on the stream run of each agent ───────────────────────────


@pytest.mark.parametrize("autoload", [False, True], ids=["dark", "flag-on"])
@pytest.mark.parametrize("solo_room", [True, False], ids=["direct", "room"])
@pytest.mark.parametrize("agent_name", AGENTS)
async def test_a_direct_chat_gets_the_chat_text(
    stream, made, monkeypatch, agent_name: str, solo_room: bool, autoload: bool,
) -> None:
    """The model calls a name that the agent does not hold. The next request
    holds the chat text of HRD-1, never MAF's text. The trail shows the member
    line. No loader exists or is asked, with ``CHAT_TOOL_AUTOLOAD`` on too.
    Mutation: gate the hook on the orchestrator → MAF's text → red. Mutation:
    pass ``step=True`` → NOT_IN_STEP → red."""
    monkeypatch.setattr(ut, "autoload_enabled", lambda: autoload)
    _own, unknown, _near, _result = CASES[agent_name]
    stream["script"] = [("call", unknown, {"query": "Priya"}), ("text", "Done.")]
    events = await _run_stream(agent_name, solo_room=solo_room)
    client = _ran(stream)
    assert client.streamed == [True, True], "Tier 1, the live stream"
    assert _tool_texts(client) == [ut.NOT_HERE.format(name=unknown)]
    _assert_no_maf_text(client)
    assert _results(events) == [ut.MEMBER_LINE], events
    _assert_no_maf_event(events)
    assert made == [(None, False)], "one answer, with no loader, in the chat mode"


@pytest.mark.parametrize("agent_name", AGENTS)
async def test_a_near_name_names_only_a_tool_of_the_request(
    stream, made, agent_name: str,
) -> None:
    """The answer names the closest tool of the request, and never a pack
    or the loader."""
    own, _unknown, near, _result = CASES[agent_name]
    stream["script"] = [("call", near, {}), ("text", "Done.")]
    await _run_stream(agent_name)
    client = _ran(stream)
    (text,) = _tool_texts(client)
    assert text == ut.SUGGEST_LIVE.format(name=near, near=own.__name__)
    assert own.__name__ in client.tools[1], "the name is a tool of the request"
    assert "«" not in text and "load_skill_tools" not in text and "pack" not in text
    assert [m for m in made if m[0] == "loader-asked"] == []


async def test_a_pack_tool_name_names_no_pack(stream, made, monkeypatch) -> None:
    """``create_task`` is a tool of the «projects» pack. The orchestrator
    with a loader would offer that pack. The Projects assistant holds no
    loader, so its text names no pack, with the auto-load on too."""
    monkeypatch.setattr(ut, "autoload_enabled", lambda: True)
    assert any("create_task" in tp.pack(s).tools for s in tp.PACKS if tp.is_pack(s))
    stream["script"] = [("call", "create_task", {"title": "t"}), ("text", "Done.")]
    await _run_stream("projects-assistant")
    (text,) = _tool_texts(_ran(stream))
    assert text == ut.NOT_HERE.format(name="create_task")
    assert "«" not in text and "load_skill_tools" not in text
    assert made == [(None, False)]


@pytest.mark.parametrize("agent_name", AGENTS)
async def test_a_name_of_the_agents_own_tools_is_untouched(
    stream, monkeypatch, agent_name: str,
) -> None:
    """A call of the agent's own tool runs, and its result reaches the model
    and the trail as the tool wrote it. The differential: the same turn with
    the view detached sends the same requests, the same tools and the same
    events."""
    from orchestrator import executor

    own, _unknown, _near, result = CASES[agent_name]
    script = [("call", own.__name__, {"query": "Priya"}), ("text", "Done.")]

    async def once() -> tuple[Any, ...]:
        stream["script"] = list(script)
        stream["clients"].clear()
        events = await _run_stream(agent_name)
        client = _ran(stream)
        shown = [(e.get("type"), e.get("delta"), e.get("content"), e.get("toolCallName"))
                 for e in events if str(e.get("type", "")).startswith(("TOOL_CALL", "TEXT"))]
        return json.dumps(client.wire, default=str), client.tools, shown

    with_view = await once()
    monkeypatch.setattr(executor, "_unknown_tool_view", lambda agents, *, step: agents)
    without = await once()
    assert with_view == without
    assert _tool_texts(_ran(stream)) == [result]
    assert [s[2] for s in with_view[2] if s[0] == "TOOL_CALL_RESULT"] == [result]


async def test_with_the_view_detached_the_raw_text_comes_back(stream, monkeypatch) -> None:
    """The old behaviour, to show that the tests above read the right wire.
    With the view detached, the email assistant's model reads MAF's own text,
    the symptom that the owner saw."""
    from orchestrator import executor

    monkeypatch.setattr(executor, "_unknown_tool_view", lambda agents, *, step: agents)
    stream["script"] = [("call", "query_inbox", {"query": "Priya"}), ("text", "Done.")]
    events = await _run_stream("email-assistant")
    (text,) = _tool_texts(_ran(stream))
    assert ut.is_raw(text) == "query_inbox", text
    assert _results(events) == [ut.MEMBER_LINE], "the trail of task 3599 holds"


# ── 2. Tier 2 keeps the view ────────────────────────────────────────────────


@pytest.mark.parametrize("agent_name", AGENTS)
async def test_tier_2_keeps_the_answer(stream, made, agent_name: str) -> None:
    """Tier 1 fails before any output, and the run falls back to Tier 2, which
    asks with no stream. Tier 2 runs ``agents[0]`` through
    ``_tier2_tool_view``, ``_agent_for_run`` and ``_with_tier_policy``. Each
    is a copy that keeps the providers. Tier 2 draws a row only for a tool
    that it wrapped, so the unknown call has no row there. Mutation: drop the
    providers in ``_tier2_tool_view`` → MAF's text → red."""
    _own, unknown, _near, _result = CASES[agent_name]
    stream["script"] = [("call", unknown, {"query": "Priya"}), ("text", "Done.")]
    stream["fail_stream"] = True
    events = await _run_stream(agent_name)
    client = _ran(stream)
    assert client.streamed == [False, False], "Tier 2, with no stream"
    assert _tool_texts(client) == [ut.NOT_HERE.format(name=unknown)]
    _assert_no_maf_text(client)
    _assert_no_maf_event(events)
    assert _results(events) == [], "no Tier 2 row for a name that the run does not hold"
    assert [m for m in made if m[0] == "loader-asked"] == []


@pytest.mark.parametrize("agent_name", AGENTS)
async def test_the_tier_2_copies_keep_the_provider(agent_name: str) -> None:
    """The copies of Tier 2, one by one, on the view of each agent."""
    from orchestrator import executor as ex

    own, unknown, _near, _result = CASES[agent_name]
    rows: list[str] = []

    def make_shim(fn: Any, name: str) -> Any:
        async def shim(*a: Any, **k: Any) -> Any:
            rows.append(name)
            return await fn(*a, **k)
        return shim

    client = _Scripted([("call", unknown, {}), ("text", "Done.")])
    agent = Agent(client=client, name=agent_name, tools=[own])
    view = ex._log_request_tools_view([agent], agent_name)[0]
    tier2 = ex._with_tier_policy(ex._agent_for_run(ex._tier2_tool_view(view, make_shim), None),
                                 None)
    assert type(tier2.context_providers[-1]) is ut.UnknownToolProvider
    await tier2.run("find the mail from Priya")
    assert _tool_texts(client) == [ut.NOT_HERE.format(name=unknown)]
    _assert_no_maf_text(client)
    assert rows == []


# ── 3. What stays out ───────────────────────────────────────────────────────


def test_an_agent_that_is_not_a_maf_agent_gets_no_view() -> None:
    """A Copilot SDK object is not a MAF ``Agent``, so MAF's middleware never
    runs for it, and the hook leaves it as it is."""
    from orchestrator import executor as ex

    copilot = types.SimpleNamespace(run=lambda *_a, **_k: None, context_providers=[])
    assert ex._log_request_tools_view([copilot], "task-manager")[0] is copilot
    assert ex._log_request_tools_view([], "email-assistant") == []
