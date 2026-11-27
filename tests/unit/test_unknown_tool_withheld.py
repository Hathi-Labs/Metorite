"""HRD-1d (task 3719): a tool of an MCP server that the run held back on
purpose gets an answer that says why, and never "no tool is named".

Production, 2026-11-17: a member's chat held the email pack, so the executor
logged ``mcp.untrusted_pack_skipped`` and attached no MCP server of the org
(WS-67 OA-7b). The model still called ``<server>_call_operation``, because
the name was in the history of the chat. ``UnknownToolAnswer`` answered
``NOT_HERE``: "no tool is named X in this chat". That is false, and the model
could tell the member that the tool does not exist.

The skip (``_tool_injection._inject_mcp_servers``) now hands the provider of
the run the tool-name prefix of each server that it held back
(``_note_withheld_servers`` -> ``unknown_tool.note_withheld``). The value
comes from the registry rows of the run's org, through the one row rule of
the lookahead (``_attachable_rows`` and ``_row_may_attach``), never from a tool argument or
the member's text. A call of a tool of such a server gets
``unknown_tool.WITHHELD_MAIL``. A truly unknown name keeps its answer.

The run tests drive the REAL ``run_agent_stream`` to a scripted model, in
the harness of ``test_unknown_tool_non_orchestrator_stream.py``.
``executor._llm_recovery`` and ``mutation.attempt_self_mutation`` are stubbed,
so a failed run asks no model and opens no pull request. A tripwire
(``_no_network``) records each socket, DNS and database connect, and each
test fails on one. The registry read (``enabled_meta``) is a stub.

The stream run uses ``projects-assistant`` with one tool that carries the
owner mark of the email pack, because the skip reads that mark and not the
agent. The orchestrator test drives its view with a real pack loader.

Mutations, each red:

* drop the withheld branch of ``answer_for`` -> test_a_held_back_server_tool_gets_the_reason
  and test_tier_2_keeps_the_reason
* drop the ``_note_withheld_servers`` call at the skip -> the same tests
* match ``startswith(server)`` with no ``_`` -> test_the_match_needs_the_whole_prefix and
  the run test (``ledgerx_`` reads the reason)
* let a held name ask the loader -> test_a_held_back_name_never_asks_the_loader
* read the registry with the flag off -> test_with_the_flag_off_nothing_is_read
* drop the grant check of the row rule -> test_a_server_that_the_grant_refuses_is_not_named

Run: ``uv run pytest tests/unit/test_unknown_tool_withheld.py -q``.
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
    tool,
)

ORG = "0a0a0a0a-0000-4000-8000-0000000003d1"
AGENT = "projects-assistant"
MEMBER = "kiran@acme.test"
SESSION = "chat-hrd1d"
RUN_KEY = "key-hrd1d"
#: The words that only MAF writes. No text that a model reads may hold them.
_MAF = re.compile(r'requested function|not found\.|function "', re.I)
#: The same rule as ``test_unknown_tool_answer._RETRY``.
_RETRY = re.compile(r"\bagain\b|\bthen call\b|\bnow call\b|\bcall (?:it|them)\b", re.I)

HELD = "ledger_call_operation"
#: A name that starts with the name of a held server, but not with its prefix.
NEAR_PREFIX = ("ledgerx_call_operation", "ledger-x_call_operation")
UNKNOWN = "make_coffee"


def _row(name: str, **extra: Any) -> Any:
    """One row of ``enabled_meta``: the metadata, with no header."""
    base = {"name": name, "enabled": True, "agent_scope": ("*",), "transport": "http-sse",
            "url": f"https://{name}.example/mcp"}
    return types.SimpleNamespace(**{**base, **extra})


#: The servers of the org. Two attach for every agent. One is for the
#: orchestrator only, one is stdio (D85), and one has no url.
ROWS = (
    _row("ledger"), _row("zoho-crm"), _row("orch-only", agent_scope=("orchestrator",)),
    _row("local", transport="stdio"), _row("no-url", url=""),
)


# ── The tripwire: the run reaches no database, no Redis and no model ────────


@pytest.fixture(autouse=True)
def _no_network(monkeypatch) -> Iterator[list[str]]:
    """Record each outbound connect of the test, and fail the test on one.
    A copy of the tripwire of ``test_unknown_tool_non_orchestrator_stream.py``.
    The event loop of Windows makes its own socket pair on a loopback port
    that it listens on, and that connect is allowed."""
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
    """The tripwire itself, so an empty record proves something."""
    with socket.socket() as sock, pytest.raises(ConnectionRefusedError):
        sock.connect(("127.0.0.1", 9))
    psycopg = pytest.importorskip("psycopg")
    with pytest.raises(ConnectionRefusedError):
        psycopg.connect("postgresql://acb@127.0.0.1:9/acb")
    assert _no_network == ["connect:('127.0.0.1', 9)", "db:psycopg.connect"]
    _no_network.clear()


# ── 1. The value, the match and the text ─────────────────────────────────────


@pytest.mark.parametrize("server", ["ledger", "zoho-crm", "a1", "Odd Name!", "trail_.-"])
def test_the_prefix_is_mafs_own(server: str) -> None:
    """``_maf_mcp.tool_prefix`` gives the start of each tool name that MAF
    builds from ``tool_name_prefix``. Mutation: drop the ``_`` → red."""
    from agent_framework._mcp import _build_prefixed_mcp_name
    from orchestrator import _maf_mcp

    prefix = _maf_mcp.tool_prefix(server)
    for tool_part in ("call_operation", "list_mcp_tools", "_load_tool"):
        built = _build_prefixed_mcp_name(tool_part, server)
        assert built.startswith(prefix), (built, prefix)
        assert built == prefix + tool_part.lstrip("_.-")
    assert _maf_mcp.tool_prefix("") == "" and _maf_mcp.tool_prefix("_-.") == ""


def test_the_match_needs_the_whole_prefix() -> None:
    """The name must start with the server name AND its ``_``. Server names
    hold hyphens and never ``_`` (``NAME_RE``), so ``ledger-x`` is another
    server. Mutation: match ``startswith("ledger")`` → red."""
    held = ut.Withheld(prefixes=frozenset({"ledger_", "zoho_"}), reason=ut.WITHHELD_MAIL_REASON)
    assert held.holds(HELD)
    assert held.holds("ledger_list_mcp_tools"), "a loader of the server counts"
    for name in (*NEAR_PREFIX, "ledger", "ledger_", "xledger_call", "zoho-crm_find", ""):
        assert not held.holds(name), name


def test_a_platform_name_is_never_a_servers() -> None:
    """A server named ``create`` and a call of ``create_task``, a tool of the
    «projects» pack: the answer stays the answer of HRD-1c, with or without
    a loader. Mutation: drop ``_first_party`` → the reason → red."""
    assert any("create_task" in tp.pack(s).tools for s in tp.PACKS if tp.is_pack(s))
    held = ut.Withheld(prefixes=frozenset({"create_"}), reason=ut.WITHHELD_MAIL_REASON)
    assert not held.holds("create_task")
    for offered in ((), ("projects",)):
        assert ut.answer_for("create_task", live=(), offered=offered, withheld=held) == (
            ut.answer_for("create_task", live=(), offered=offered))


def test_an_exact_tool_keeps_its_answer_and_a_close_name_gives_way() -> None:
    """A tool of the request keeps LIVE_NOW. A held name beats a close name
    of the request, which the model did not mean."""
    held = ut.Withheld(prefixes=frozenset({"ledger_"}), reason=ut.WITHHELD_MAIL_REASON)
    assert ut.answer_for(HELD, live=[HELD], offered=(), withheld=held) == (
        ut.LIVE_NOW.format(name=HELD))
    close = "ledger_call_operations"
    assert ut.answer_for(HELD, live=[close], offered=()) == (
        ut.SUGGEST_LIVE.format(name=HELD, near=close)), "non-vacuity: a close name exists"
    assert ut.answer_for(HELD, live=[close], offered=(), withheld=held) == (
        ut.WITHHELD_MAIL.format(name=HELD))


def test_a_server_named_like_a_platform_family_keeps_the_close_name_answer() -> None:
    """Review P2: a server named ``crm`` must not turn a typo of a tool of the
    platform into the "connected system" text. The close name keeps its answer."""
    held = ut.Withheld(prefixes=frozenset({"crm_"}), reason=ut.WITHHELD_MAIL_REASON)
    real = "crm_update_record"
    typo = "crm_update_records"
    assert not held.holds(typo)
    assert ut.answer_for(typo, live=[real], offered=(), withheld=held) == (
        ut.SUGGEST_LIVE.format(name=typo, near=real))
    # Non-vacuity: a name of that server that no tool of the platform is near to
    # still gets the reason.
    assert held.holds("crm_zzzz_unrelated_operation")


def test_a_step_ignores_the_value() -> None:
    """A delegated run or a run with no chat keeps NOT_IN_STEP (HRD-1b)."""
    held = ut.Withheld(prefixes=frozenset({"ledger_"}), reason=ut.WITHHELD_MAIL_REASON)
    assert ut.answer_for(HELD, live=(), offered=(), step=True, withheld=held) == (
        ut.NOT_IN_STEP.format(name=HELD))
    assert ut.UnknownToolAnswer(step=True, withheld=held)._withheld is None
    agent = types.SimpleNamespace(context_providers=[ut.UnknownToolProvider(step=True)])
    assert not ut.reads_withheld(agent)
    assert not ut.note_withheld(agent, ["ledger_"], reason=ut.WITHHELD_MAIL_REASON)
    assert agent.context_providers[0].withheld is None


def test_note_withheld_takes_only_a_real_value() -> None:
    """An empty set, a reason with no text and a view with no provider change
    nothing. A chat provider takes a frozen value."""
    provider = ut.UnknownToolProvider()
    agent = types.SimpleNamespace(context_providers=[provider])
    assert not ut.note_withheld(agent, [], reason=ut.WITHHELD_MAIL_REASON)
    assert not ut.note_withheld(agent, ["", None], reason=ut.WITHHELD_MAIL_REASON)
    assert not ut.note_withheld(agent, ["ledger_"], reason="no_such_reason")
    assert provider.withheld is None
    assert not ut.note_withheld(types.SimpleNamespace(), ["ledger_"],
                                reason=ut.WITHHELD_MAIL_REASON)
    assert ut.note_withheld(agent, ["ledger_", "ledger_"], reason=ut.WITHHELD_MAIL_REASON)
    assert provider.withheld == ut.Withheld(frozenset({"ledger_"}), ut.WITHHELD_MAIL_REASON)
    with pytest.raises(AttributeError):
        provider.withheld.reason = "x"  # type: ignore[misc]


def test_the_text_reads_well_to_a_member() -> None:
    """The text says what happened, why, and what the member can do. It
    names only the called tool, orders no retry, and holds no internal word.
    Each sentence keeps to the STE limit of 25 words."""
    text = ut.WITHHELD_MAIL.format(name=HELD)
    assert text.startswith("Not run: ")
    assert not _RETRY.search(text)
    assert not re.search(rf"\bcall\s+[\"«]?{re.escape(HELD)}\b", text, re.I)
    assert ut.WITHHELD_MAIL in ut.TEXTS
    for word in ("MCP", "pack", "flag", "server", "untrusted", "load_skill_tools", "zoho"):
        assert word.lower() not in text.lower(), word
    for sentence in re.split(r"(?<=\.)\s+", text):
        assert len(sentence.split()) <= 25, sentence
    long = "ledger_" + "x" * 200
    assert len(ut.answer_for(long, live=(), offered=(), withheld=ut.Withheld(
        frozenset({"ledger_"}), ut.WITHHELD_MAIL_REASON))) < len(ut.WITHHELD_MAIL) + 60


# ── 2. The scripted model, as the OpenAI client sends each request ──────────


def _wire_client() -> Any:
    from agent_framework.openai import OpenAIChatCompletionClient

    return OpenAIChatCompletionClient(model="m", api_key="k", base_url="http://127.0.0.1:9/v1")


class _Scripted(FunctionInvocationLayer, ChatMiddlewareLayer, BaseChatClient):
    """The layers of ``OpenAIChatCompletionClient``, with a scripted model.
    *fail_stream* raises at each streamed request, so Tier 2 runs."""

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
    """The text of each tool result that the model read, once by call id."""
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


def _results(events: list[dict[str, Any]]) -> list[Any]:
    return [e.get("content") for e in events if e.get("type") == "TOOL_CALL_RESULT"]


def _calls(*names: str) -> list[tuple[Any, ...]]:
    return [*(("call", n, {}) for n in names), ("text", "Done.")]


# ── 3. The agent's tools ────────────────────────────────────────────────────


async def find_tasks(query: str = "") -> str:
    """The Projects assistant's own tool: find tasks."""
    return "2 tasks match."


async def search_emails(query: str = "") -> str:
    """A tool of the email pack: search the member's mail."""
    return "3 emails match."


def _mark_mail(fn: Any) -> Any:
    """Give *fn* the owner mark of the email pack, as a pack build does."""
    from orchestrator import _tool_injection as ti

    setattr(fn, ti.PACK_OWNER_ATTR, "email-assistant")
    return fn


# ── 4. The stream run, real from run_agent_stream to the model client ──────


@pytest.fixture
def stream(tmp_path, monkeypatch):
    """The REAL stream run of ``projects-assistant``, whose model is scripted.

    ``state["mail"]`` gives the agent the tool with the mail owner mark.
    ``state["rows"]`` is the answer of ``enabled_meta``, or an exception to
    raise. ``state["flag"]`` is ``MAF_MCP_ORGS`` for the org. Each registry
    read goes into ``state["reads"]``."""
    import acb_memory
    import acb_skills.integrations as integ
    import acb_skills.loader as loader
    from acb_common import activity, get_settings
    from gateway.connections import mcp as mcp_registry
    from gateway.routes import agent as agent_routes
    from orchestrator import _maf_mcp, app_tools, executor, mutation, stream_relay, workflow_tools
    from orchestrator import _tool_injection as ti

    settings = get_settings()
    monkeypatch.setattr(settings, "agents_clone_dir", str(tmp_path / "agents"))
    monkeypatch.setattr(settings, "custom_apps_root", str(tmp_path / "agents" / "apps"))
    clone = tmp_path / "agents" / "repos" / AGENT
    clone.mkdir(parents=True)
    monkeypatch.setattr(agent_routes, "_AGENT_REGISTRY", [
        {"name": AGENT, "repo_name": f"Org/{AGENT}", "local_path": str(clone)}])
    monkeypatch.setattr(agent_routes, "_load_dynamic_agents", lambda: [])

    state: dict[str, Any] = {"script": [], "fail_stream": False, "clients": [], "mail": True,
                             "rows": list(ROWS), "flag": True, "reads": []}

    @contextlib.contextmanager
    def load(name: str, **_kw: Any):
        def build() -> list[Any]:
            client = _Scripted(list(state["script"]), fail_stream=state["fail_stream"])
            state["clients"].append(client)
            tools: list[Any] = [find_tasks]
            if state["mail"]:
                tools.append(_mark_mail(search_emails))
            return [Agent(client=client, name=name, tools=tools)]

        yield types.SimpleNamespace(
            agent_dir=clone, agent_name=name, config={"name": name}, build_agents=build,
            cleanup=lambda: None,
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

    async def enabled_meta(org: str) -> list[Any]:
        state["reads"].append(org)
        if isinstance(state["rows"], Exception):
            raise state["rows"]
        return list(state["rows"])

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
    for name in ("mark_active", "mark_inactive", "register_live_run",
                 "unregister_live_run", "push_event"):
        monkeypatch.setattr(stream_relay, name, _none)
    monkeypatch.setattr(activity, "_axadd", _none)
    monkeypatch.setattr(activity, "_axpresence", _none)
    monkeypatch.setattr(executor, "_read_org_display_name", lambda *_a, **_k: "")
    monkeypatch.setattr(executor, "_read_number_system", lambda *_a, **_k: "")
    # The registry: the flag of the org and the metadata read, with no DB.
    monkeypatch.setattr(_maf_mcp, "maf_mcp_org_allowed", lambda _org: state["flag"])
    monkeypatch.setattr(mcp_registry, "enabled_meta", enabled_meta)

    async def servers_for_org(*_a: Any, **_k: Any) -> list[Any]:
        raise AssertionError("a run that holds the mail tool never reads the headers")

    monkeypatch.setattr(mcp_registry, "servers_for_org", servers_for_org)
    return state


def _ran(state: dict[str, Any]) -> _Scripted:
    (client,) = [c for c in state["clients"] if c.wire]
    return client


async def _run_stream(run_id: str = "r-hrd1d") -> list[dict[str, Any]]:
    """One chat turn, as the gateway route starts it: a chat, cards drawn,
    and the org from the server."""
    from acb_common.db import bind_tenant, release_tenant
    from acb_skills.write_artifact import bind_artifact_context
    from orchestrator import executor

    from tests.unit._native_maf_harness import parse_frames

    token = bind_tenant(ORG)
    try:
        lines = [line async for line in executor.run_agent_stream(
            AGENT, {"message": "run the ledger report"}, run_id=run_id,
            thread_id=f"chat-{AGENT}", shows_cards=True, organization_id=ORG,
            solo_room=True)]
    finally:
        release_tenant(token)
        bind_artifact_context()
        executor._RUN_QUEUES.clear()
        executor._pending_user_input.clear()
    return parse_frames(lines)


async def test_a_held_back_server_tool_gets_the_reason(stream) -> None:
    """(a), (b), (c) and (f) in ONE run, so (b) and (c) cannot pass on an
    empty set. A tool of the held server ``ledger`` gets the reason. A name
    that only starts with ``ledger``, a server of another agent, a stdio
    server and a truly unknown name keep NOT_HERE. The trail shows the member
    line of task 3599 for each. MAF stops the calls of a request after 3
    failed calls in a row, so a call of the agent's own tool comes after each
    second unknown name. Mutation: drop the withheld branch, or the note at
    the skip → NOT_HERE for the first call → red."""
    own = "find_tasks"
    names = (HELD, NEAR_PREFIX[0], own, NEAR_PREFIX[1], "orch-only_find", own,
             "local_find", UNKNOWN)
    stream["script"] = _calls(*names)
    events = await _run_stream()
    client = _ran(stream)
    assert client.streamed == [True] * (len(names) + 1), "Tier 1, the live stream"
    assert "search_emails" in client.tools[0], "the run holds the mail tool"
    want = {n: ut.NOT_HERE.format(name=n) for n in names}
    want[HELD] = ut.WITHHELD_MAIL.format(name=HELD)
    want[own] = "2 tasks match."
    assert _tool_texts(client) == [want[n] for n in names]
    assert stream["reads"] == [ORG], "one metadata read, for the org of the run"
    _assert_no_maf_text(client)
    assert _results(events) == [
        "2 tasks match." if n == own else ut.MEMBER_LINE for n in names], events
    for event in events:
        assert not _MAF.search(json.dumps(event, default=str)), event
        assert "zoho" not in json.dumps(event, default=str), "no other server is named"
    assert not any("zoho" in t for t in _tool_texts(client))


async def test_tier_2_keeps_the_reason(stream) -> None:
    """Tier 1 fails before any output, and Tier 2 asks with no stream. Its
    copies of the view keep the same provider, so the value holds. Mutation:
    rebuild the provider in a Tier 2 copy → NOT_HERE → red."""
    stream["script"] = _calls(HELD, UNKNOWN)
    stream["fail_stream"] = True
    await _run_stream()
    client = _ran(stream)
    assert client.streamed == [False, False, False], "Tier 2, with no stream"
    assert _tool_texts(client) == [
        ut.WITHHELD_MAIL.format(name=HELD), ut.NOT_HERE.format(name=UNKNOWN)]
    _assert_no_maf_text(client)


async def test_with_the_flag_off_nothing_is_read(stream) -> None:
    """An org that ``MAF_MCP_ORGS`` does not name attaches no server, so it
    holds none back. The registry is not read, and the answer is NOT_HERE.
    Mutation: read the registry before the flag → a read → red."""
    stream["flag"] = False
    stream["script"] = _calls(HELD)
    await _run_stream()
    assert _tool_texts(_ran(stream)) == [ut.NOT_HERE.format(name=HELD)]
    assert stream["reads"] == []


async def test_a_run_with_no_mail_tool_reads_nothing(stream, monkeypatch) -> None:
    """A run with no mail pack goes to the attach, and the skip never runs.
    The attach reads ``servers_for_org`` (here: no server), never
    ``enabled_meta``, and no value is set."""
    from gateway.connections import mcp as mcp_registry

    attached: list[str] = []

    async def no_servers(org: str, **_k: Any) -> list[Any]:
        attached.append(org)
        return []

    monkeypatch.setattr(mcp_registry, "servers_for_org", no_servers)
    stream["mail"] = False
    stream["script"] = _calls(HELD)
    await _run_stream()
    client = _ran(stream)
    assert "search_emails" not in client.tools[0]
    assert _tool_texts(client) == [ut.NOT_HERE.format(name=HELD)]
    assert stream["reads"] == [] and attached == [ORG], "non-vacuity: the attach ran"


async def test_a_registry_fault_keeps_the_old_answer(stream) -> None:
    """A fault of the metadata read leaves no value. The run goes on, and
    the answer is NOT_HERE."""
    stream["rows"] = RuntimeError("the registry is down")
    stream["script"] = _calls(HELD)
    await _run_stream()
    assert _tool_texts(_ran(stream)) == [ut.NOT_HERE.format(name=HELD)]
    assert stream["reads"] == [ORG]


async def test_with_no_held_server_the_run_is_byte_identical(stream, monkeypatch) -> None:
    """(d): with no row to hold back, the turn sends the same requests, the
    same tools and the same events as a turn with no note at all, the
    behaviour of HRD-1c."""
    from orchestrator import _tool_injection as ti

    stream["rows"] = []
    script = _calls(HELD, UNKNOWN)

    async def once() -> tuple[Any, ...]:
        stream["script"] = list(script)
        stream["clients"].clear()
        events = await _run_stream()
        client = _ran(stream)
        shown = [(e.get("type"), e.get("delta"), e.get("content"), e.get("toolCallName"))
                 for e in events if str(e.get("type", "")).startswith(("TOOL_CALL", "TEXT"))]
        return json.dumps(client.wire, default=str), client.tools, shown

    with_note = await once()

    async def no_note(*_a: Any, **_k: Any) -> None:
        return None

    monkeypatch.setattr(ti, "_note_withheld_servers", no_note)
    without = await once()
    assert with_note == without
    assert _tool_texts(_ran(stream)) == [
        ut.NOT_HERE.format(name=HELD), ut.NOT_HERE.format(name=UNKNOWN)]


# ── 5. The orchestrator's view with a real loader, and a step ──────────────


def _record() -> tp.PackRun:
    turn = tp.PackTurn(ORG, SESSION, tp._member_part(MEMBER), 3, (), "", True)
    choice = tp.choose(turn, allowed=frozenset(), base_tools=0)
    return tp.PackRun.of(turn, choice, [])


def _loader(ran: list[str]) -> tp.LoadSkillTools:
    async def body(title: str = "") -> str:
        ran.append(f"create_task:{title}")
        return "made"

    body.__name__ = "create_task"
    made = {"projects": [tool(body, name="create_task", approval_mode="never_require")]}

    async def build(slug: str, ctx: Any, holds: frozenset[str]) -> Any:
        return types.SimpleNamespace(tools=tuple(made.get(slug, [])), held=False)

    return tp.LoadSkillTools(offered=("projects",), run_key=RUN_KEY, record=_record(),
                             build=build)


@contextlib.contextmanager
def _in_run(loader: tp.LoadSkillTools) -> Iterator[None]:
    """The frame that the executor binds: the tenant, the run key, the record."""
    from acb_common.db import bind_tenant, release_tenant
    from acb_skills.write_artifact import artifact_context_scope, bind_artifact_context

    token = bind_tenant(ORG)
    try:
        with artifact_context_scope():
            bind_artifact_context(
                run_id="r-hrd1d", session_id=SESSION, agent_name="orchestrator", member=MEMBER,
                batch_thread=False, no_egress=False,
                **{tp.PACK_RUN_KEY: loader.run_key, tp.PACKS_KEY: loader.record})
            yield
    finally:
        release_tenant(token)


async def _orchestrator_turn(
    monkeypatch, script: list[tuple[Any, ...]], *, rows: list[Any], grant: Any = None,
    step: bool = False,
) -> tuple[_Scripted, tp.LoadSkillTools, list[str], list[str]]:
    """The orchestrator's view (loader, then the one hook), the MCP step on
    that view, then the run. Gives the client, the loader, the tools that the
    loader built and ran, and the registry reads."""
    from gateway.connections import mcp as mcp_registry
    from orchestrator import _maf_mcp
    from orchestrator import _tool_injection as ti
    from orchestrator import executor as ex
    from orchestrator._native_run_context import agent_with_providers

    reads: list[str] = []

    async def enabled_meta(org: str) -> list[Any]:
        reads.append(org)
        return list(rows)

    monkeypatch.setattr(mcp_registry, "enabled_meta", enabled_meta)
    monkeypatch.setattr(_maf_mcp, "maf_mcp_org_allowed", lambda _org: True)
    ran: list[str] = []
    loader = _loader(ran)
    client = _Scripted(script)
    agent = Agent(client=client, name="orchestrator",
                  tools=[find_tasks, _mark_mail(search_emails)])
    agent = agent_with_providers(agent, [tp.LoadSkillToolsProvider(loader)])
    if step:
        view = ex._unknown_tool_view([agent], step=True)[0]
    else:
        view = ex._log_request_tools_view([agent], "orchestrator")[0]
    with _in_run(loader):
        await ti._inject_mcp_servers(view, "orchestrator", organization_id=ORG,
                                     is_authorized=grant)
        await view.run([Message("user", ["run the crm report"])])
    return client, loader, ran, reads


async def test_a_held_back_name_never_asks_the_loader(monkeypatch) -> None:
    """(e): ``CHAT_TOOL_AUTOLOAD`` on. With the server ``ledger`` held back,
    the loader is not asked and nothing loads. Non-vacuity: with no row, the
    same name keeps the answer of HRD-1c. Mutation: let a held name reach
    ``_autoload`` → a load → red. (A server named like a family of the
    platform, such as ``crm``, keeps the close-name answer: review P2.)"""
    monkeypatch.setattr(ut, "autoload_enabled", lambda: True)
    name = "ledger_call_operation"
    client, loader, ran, reads = await _orchestrator_turn(
        monkeypatch, _calls(name), rows=[_row("ledger")])
    assert _tool_texts(client) == [ut.WITHHELD_MAIL.format(name=name)]
    assert loader.record.holds() == (), "a held name never loads a pack"
    assert reads == [ORG] and ran == []
    _assert_no_maf_text(client)

    client, loader, _ran2, _reads = await _orchestrator_turn(
        monkeypatch, _calls(name), rows=[])
    assert loader.record.holds() == (), "nothing near the name: nothing loads"
    assert _tool_texts(client)[0].startswith(f"Not run: no tool is named {name}"), (
        "non-vacuity: with no row the old answer returns")


async def test_the_auto_load_stays_dark(monkeypatch) -> None:
    """(e): the flag reads off by default, and a held name reads the reason."""
    from acb_common import get_settings

    monkeypatch.delenv("CHAT_TOOL_AUTOLOAD", raising=False)
    get_settings.cache_clear() if hasattr(get_settings, "cache_clear") else None
    assert ut.autoload_enabled() is False
    client, loader, _ran, _reads = await _orchestrator_turn(
        monkeypatch, _calls("ledger_call_operation"), rows=[_row("ledger")])
    assert _tool_texts(client) == [ut.WITHHELD_MAIL.format(name="ledger_call_operation")]
    assert loader.record.holds() == ()


async def test_a_server_that_the_grant_refuses_is_not_named(monkeypatch) -> None:
    """The member's grant refuses ``mcp-crm``, so the run would never attach
    it. The answer keeps the text of HRD-1c, and a member learns nothing of
    the server. Mutation: drop the grant check of the row rule → the reason
    → red."""
    monkeypatch.setattr(ut, "autoload_enabled", lambda: False)
    asked: list[str] = []

    def grant(service: str) -> bool:
        asked.append(service)
        return service != "mcp-crm"

    name = "crm_create_task"
    client, _, _, reads = await _orchestrator_turn(
        monkeypatch, _calls(name), rows=[_row("crm")], grant=grant)
    assert asked == ["mcp-crm"] and reads == [ORG]
    want = ut.answer_for(name, live=(), offered=("projects",))
    assert _tool_texts(client) == [want]
    assert want != ut.WITHHELD_MAIL.format(name=name)


async def test_a_step_reads_nothing_and_keeps_not_in_step(monkeypatch) -> None:
    """(4): a step (a delegated run, a worker, a run with no chat) holds a
    step provider. The MCP step reads no registry for it, and its answer
    stays NOT_IN_STEP. Mutation: read for a step → a read → red."""
    client, _, _, reads = await _orchestrator_turn(
        monkeypatch, _calls("crm_create_task"), rows=[_row("crm")], step=True)
    assert reads == []
    assert _tool_texts(client) == [ut.NOT_IN_STEP.format(name="crm_create_task")]


# ── 6. The one rule and the one hook ───────────────────────────────────────


def test_one_row_rule_and_one_hook() -> None:
    """The lookahead and the held-back servers read ONE row rule. The skip
    hands the value over, and no second maker of the provider exists.

    The rule is ``_row_may_attach``, read through ``_attachable_rows`` (one
    ``enabled_meta`` read). The merge of CSY-4 and HRD-1d made the two rules
    one, so each of the four readers calls ``_attachable_rows`` and none of
    them repeats a row test. ``_reconnect_rows`` (task 3866) reads
    ``needs_reconnect_meta``, so it calls ``_row_may_attach`` itself, with
    ``parked``. TOF-5a-1: the skill map (``_mcp_reach_rows``) is a fifth
    reader of ``_attachable_rows``."""
    import inspect

    from orchestrator import _tool_injection as ti
    from orchestrator import executor as ex

    rows = inspect.getsource(ti._attachable_rows)
    assert "_row_may_attach(" in rows and "enabled_meta(" in rows
    assert "servers_for_org" not in rows, "no header read and no OAuth refresh"
    note = inspect.getsource(ti._note_withheld_servers)
    assert "servers_for_org" not in note, "no header read and no OAuth refresh"
    for reader in (ti._mcp_may_attach, ti._note_withheld_servers, ti._note_mail_skipped,
                   ti._with_server_words, ti._mcp_reach_rows):
        src = inspect.getsource(reader)
        assert "_attachable_rows(" in src, reader.__name__
        assert "_MAF_HTTP_TRANSPORTS" not in src, f"{reader.__name__} repeats the row rule"
    parked = inspect.getsource(ti._reconnect_rows)
    assert "_row_may_attach(" in parked and "parked=True" in parked
    assert "_MAF_HTTP_TRANSPORTS" not in parked, "_reconnect_rows repeats the row rule"
    assert not hasattr(ti, "_attachable_server_names"), "a second row rule"
    inject = inspect.getsource(ti._inject_mcp_servers)
    assert inject.index("mcp.untrusted_pack_skipped") < inject.index("_note_withheld_servers(")
    assert inspect.getsource(ex).count("UnknownToolProvider(") == 1
    assert "UnknownToolProvider(" not in inspect.getsource(ti)
