"""Task 3924: a listed MCP tool that is not loaded runs in the same step.

Spec: ``project-docs/specs/chat_connected_systems.md`` §3.5, and the module
docstring of ``orchestrator/_maf_mcp.py``, point 14.

The evidence (2026-11-23 03:50 UTC, a Zoho Books hosted server): the model
listed the tools of the server, then called ``ZohoMCP_listProducts`` at once,
with no ``load_tool``. MAF answered "not found", ``unknown_tool.answered``
logged ``loaded=None``, and the chat drew a red "failed" step. The model then
loaded the tool, called it again, and that call ran.

Now ``McpAutoload`` reads the model's answer before MAF runs its calls. When a
called name is a tool in the own list of a server ATTACHED to this run, and the
run has not loaded it, MAF's own load puts it in the run's live list. The call
then runs in the same step, through the same ``McpPermissionGate``: the same
``decide()``, card, grant, read-only and always-confirm rules, the same error
text and the same error cap. One ``mcp.loader action=autoload`` line names the
server and the tool. A name that no attached server lists keeps the
unknown-tool answer.
"""
from __future__ import annotations

import asyncio
import json
import warnings
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import structlog

pytest.importorskip("agent_framework", reason="agent_framework not installed")
pytest.importorskip("mcp", reason="mcp not installed")

from acb_common.db import clear_tenant
from acb_skills import egress
from acb_skills.unknown_tool import MEMBER_LINE
from agent_framework import ChatResponse, ChatResponseUpdate, Content, Message, ResponseStream
from agent_framework._feature_stage import ExperimentalWarning
from gateway import outbound_guard
from orchestrator import _maf_mcp, _tool_injection, executor

from tests.unit._maf_mcp_fakes import RegistrySpy, local_mcp_server, row, set_flag
from tests.unit._native_maf_harness import (
    ScriptedModel,
    _a_tenant,  # noqa: F401 — a fixture, used by name
    drive_native,
    text_turn,
    tool_turn,
)

_AGENT = "apis-config"
#: The org that ``_a_tenant`` binds.
_ORG = "org-native-maf-probe"
_SERVER = "probe-srv"


@pytest.fixture(autouse=True)
def _clean():
    clear_tenant()
    executor._pending_user_input.clear()
    yield
    clear_tenant()
    executor._pending_user_input.clear()


# ── Helpers ──────────────────────────────────────────────────────────────────


def _events(logs: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    return [e for e in logs if e.get("event") == name]


def _autoloads(logs: list[dict[str, Any]]) -> list[tuple[str, str]]:
    return [(e["server"], e["tool"]) for e in _events(logs, "mcp.loader")
            if e.get("action") == "autoload"]


def _scoped(name: str = "eg", tools: tuple[str, ...] = ("echo", "lookup")) -> Any:
    """A ``RunScopedMCPTool`` with disclosure on and *tools* in its own list,
    as MAF holds it after the connect. It never connects."""
    from agent_framework import FunctionTool

    cls = _maf_mcp._run_scoped_tool_class()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ExperimentalWarning)
        tool = cls(name=name, url=f"https://{name}.example.com/mcp", tool_name_prefix=name,
                   use_progressive_disclosure=True)
    tool._functions = [
        FunctionTool(name=f"{name}_{t}", func=lambda **_k: "ran", description=f"The {t} tool.",
                     additional_properties={"_mcp_is_tool": True, "_mcp_remote_name": t,
                                            "_mcp_normalized_name": t})
        for t in tools
    ]
    return tool


def _hidden(tool: Any, name: str) -> Any:
    return next(f for f in tool._functions if f.name == name)


def _calls(*names: str) -> ChatResponse:
    return ChatResponse(messages=[Message("assistant", [
        Content.from_function_call(call_id=f"c{i}", name=n, arguments="{}")
        for i, n in enumerate(names)])])


def _request(live: list[Any]) -> Any:
    """A chat request of the run, whose options hold the live list."""
    return SimpleNamespace(options={"tools": live}, result=None, stream=False)


async def _bounded(coro: Any) -> Any:
    return await asyncio.wait_for(coro, timeout=10)


# ── The middleware, offline ──────────────────────────────────────────────────


async def test_a_listed_tool_that_is_not_loaded_goes_into_the_live_list() -> None:
    tool = _scoped()
    live = list(tool.functions)  # the three loaders, as the run starts
    ctx = _request(live)

    async def answer() -> None:
        ctx.result = _calls("eg_echo")

    with structlog.testing.capture_logs() as logs:
        await _bounded(_maf_mcp.McpAutoload(tools=[tool]).process(ctx, answer))
    assert any(t is _hidden(tool, "eg_echo") for t in live), "the tool did not load"
    assert not any(t is _hidden(tool, "eg_lookup") for t in live), "a tool that no call named loaded"
    assert _autoloads(logs) == [("eg", "echo")]


async def test_a_streamed_answer_loads_before_maf_runs_the_call() -> None:
    """The stream path: the load runs when the stream is final, which is
    before MAF builds the tool map of the batch."""
    tool = _scoped()
    live = list(tool.functions)
    ctx = _request(live)
    ctx.stream = True

    async def updates() -> Any:
        yield ChatResponseUpdate(role="assistant", contents=[
            Content.from_function_call(call_id="c1", name="eg_lookup", arguments="{}")])

    async def answer() -> None:
        ctx.result = ResponseStream(updates(), finalizer=ChatResponse.from_updates)

    with structlog.testing.capture_logs() as logs:
        await _bounded(_maf_mcp.McpAutoload(tools=[tool]).process(ctx, answer))
        assert not any(t is _hidden(tool, "eg_lookup") for t in live), "it loaded before the answer"
        async for _update in ctx.result:
            pass
        await _bounded(ctx.result.get_final_response())
    assert any(t is _hidden(tool, "eg_lookup") for t in live)
    assert _autoloads(logs) == [("eg", "lookup")]


@pytest.mark.parametrize("name", ["eg_nope", "eg_echoo", "other_echo", "echo", "eg_list_mcp_tools"])
async def test_a_name_that_no_attached_server_lists_loads_nothing(name: str) -> None:
    tool = _scoped()
    live = list(tool.functions)
    before = list(live)
    ctx = _request(live)

    async def answer() -> None:
        ctx.result = _calls(name)

    with structlog.testing.capture_logs() as logs:
        await _bounded(_maf_mcp.McpAutoload(tools=[tool]).process(ctx, answer))
    assert live == before
    assert _autoloads(logs) == []


async def test_a_first_party_tool_name_never_loads_a_server_tool() -> None:
    """Review P2: a registry server ``crm`` that lists ``update_record`` has the
    local name ``crm_update_record``, which is a tool of the ``crm-records``
    pack. The model that calls it means the pack tool. So the name keeps the
    HRD-1 answer, and no record data goes to the outside server."""
    from tests.unit._maf_mcp_fakes import native_agent

    reserved = _tool_injection._reserved_tool_names(native_agent())
    assert "crm_update_record" in reserved, "the pack name left the reserved set"
    tool = _scoped("crm", ("update_record", "lookup"))
    assert _maf_mcp._autoload_target([tool], "crm_update_record", reserved) is None
    live = list(tool.functions)
    before = list(live)
    ctx = _request(live)

    async def answer() -> None:
        ctx.result = _calls("crm_update_record")

    with structlog.testing.capture_logs() as logs:
        await _bounded(_maf_mcp.McpAutoload(tools=[tool], reserved=reserved).process(ctx, answer))
    assert live == before, "a first-party name loaded the server's tool"
    assert _autoloads(logs) == []
    # A name of the same server that no first-party tool holds still loads.
    ctx = _request(live)

    async def other() -> None:
        ctx.result = _calls("crm_lookup")

    with structlog.testing.capture_logs() as logs:
        await _bounded(_maf_mcp.McpAutoload(tools=[tool], reserved=reserved).process(ctx, other))
    assert _autoloads(logs) == [("crm", "lookup")]


async def test_a_run_that_withholds_the_loader_loads_nothing() -> None:
    """The loader egress rule: a ``no_egress`` run withholds the loaders from
    the model (``WithholdEgressTools``). The model could not load the tool,
    so the autoload does not load it either."""
    tool = _scoped()
    live = list(tool.functions)
    ctx = _request(live)

    async def answer() -> None:
        # What WithholdEgressTools does inside the agent's middleware.
        await egress.WithholdEgressTools().process(ctx, _nothing)
        ctx.result = _calls("eg_echo")

    with structlog.testing.capture_logs() as logs:
        await _bounded(_maf_mcp.McpAutoload(tools=[tool]).process(ctx, answer))
    assert not any(t is _hidden(tool, "eg_echo") for t in live)
    assert _autoloads(logs) == []


async def _nothing() -> None:
    return None


async def test_a_middleware_with_no_attached_server_loads_nothing() -> None:
    tool = _scoped()
    live = list(tool.functions)
    before = list(live)
    ctx = _request(live)

    async def answer() -> None:
        ctx.result = _calls("eg_echo")

    with structlog.testing.capture_logs() as logs:
        await _bounded(_maf_mcp.McpAutoload(tools=[]).process(ctx, answer))
    assert live == before
    assert _autoloads(logs) == []


async def test_a_tool_that_is_not_run_scoped_never_loads() -> None:
    """Only a server that ``attach_mcp_tools`` attached to this run counts.
    A plain MAF MCP tool (not ``RunScopedMCPTool``) has no marked loaders and
    no gate of this run."""
    from agent_framework import FunctionTool, MCPStreamableHTTPTool

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ExperimentalWarning)
        plain = MCPStreamableHTTPTool(name="eg", url="https://eg.example.com/mcp",
                                      tool_name_prefix="eg", use_progressive_disclosure=True)
    plain._functions = [FunctionTool(name="eg_echo", func=lambda **_k: "ran",
                                     additional_properties={"_mcp_is_tool": True})]
    live = list(plain.functions)
    before = list(live)
    ctx = _request(live)

    async def answer() -> None:
        ctx.result = _calls("eg_echo")

    await _bounded(_maf_mcp.McpAutoload(tools=[plain]).process(ctx, answer))
    assert live == before


async def test_attach_puts_one_autoload_beside_the_gate_and_replaces_an_old_one(
        monkeypatch) -> None:
    """``attach_mcp_tools`` installs ONE autoload with the gate's own list of
    tools, and replaces the one of an older attach. It goes first among the
    agent's middleware, so it reads the run's live list before a provider
    middleware copies it."""
    from tests.unit._maf_mcp_fakes import native_agent
    from tests.unit.test_maf_mcp_budget import _attach, _many

    agent = native_agent()
    old_autoload = _maf_mcp.McpAutoload(tools=[])
    old_gate = _maf_mcp.McpPermissionGate(tools=[])
    agent.middleware = [old_gate, old_autoload]
    agent, opened, _fleet, _logs = await _attach(monkeypatch, {"au": _many(13, params=1)},
                                                 agent=agent)
    try:
        autoloads = [m for m in agent.middleware if isinstance(m, _maf_mcp.McpAutoload)]
        gates = [m for m in agent.middleware if isinstance(m, _maf_mcp.McpPermissionGate)]
        assert len(autoloads) == 1 and len(gates) == 1
        assert autoloads[0] is not old_autoload and gates[0] is not old_gate
        assert agent.middleware[0] is autoloads[0]
        assert autoloads[0].tools == gates[0].tools == list(agent.mcp_tools)
        assert [t.name for t in autoloads[0].tools] == ["au"]
    finally:
        await _maf_mcp.close_mcp_tools(opened)


# ── End to end: the real agent, the real executor, a real MCP server ─────────


def _stub_pin(monkeypatch: pytest.MonkeyPatch, allowed_url: str) -> None:
    real = outbound_guard.check_mcp_url

    async def _check(url: str) -> outbound_guard.Target:
        if url == allowed_url:
            return outbound_guard.Target(url=httpx.URL(url), host="127.0.0.1", ip="127.0.0.1")
        return await real(url)

    monkeypatch.setattr(outbound_guard, "check_mcp_url", _check)


def _a_member(monkeypatch: pytest.MonkeyPatch, *, refuse: str = "") -> dict[str, Any]:
    """A member whose grant allows each service but *refuse*."""
    async def _authorizer(*_a: Any, **_k: Any) -> Any:
        return lambda service: service != refuse

    monkeypatch.setattr(executor, "_integration_authorizer", _authorizer)
    return {"user_email": "member@autoload.test"}


def _offered(body: dict[str, Any]) -> list[str]:
    return sorted(t["function"]["name"] for t in body.get("tools") or []
                  if t.get("function", {}).get("name", "").startswith(f"{_SERVER}_"))


def _tool_results(model: ScriptedModel) -> list[str]:
    return [str(m.get("content")) for m in model.bodies[-1].get("messages") or []
            if m.get("role") == "tool"]


def _steps(events: list[dict[str, Any]]) -> list[tuple[str, bool]]:
    return [(str(e.get("content")), bool(e.get("success")))
            for e in events if e.get("type") == "TOOL_CALL_RESULT"]


def _drive(monkeypatch: pytest.MonkeyPatch, srv: Any, model: ScriptedModel, *,
           member: bool = True, refuse: str = "", answer: str = "APPROVE",
           flag: str = _ORG) -> tuple[list[dict], dict[str, Any], list[dict]]:
    payload = _a_member(monkeypatch, refuse=refuse) if member else None
    session_user = str(payload["user_email"]) if payload else None
    set_flag(monkeypatch, flag)
    RegistrySpy([row(_SERVER, org=_ORG, url=srv.url)]).install(monkeypatch)
    _stub_pin(monkeypatch, srv.url)
    seen: dict[str, Any] = {"cards": [], "calls_at_card": None}

    def _on_event(event: dict[str, Any]) -> None:
        if event.get("type") == "CUSTOM" and event.get("name") == "confirmation_requested":
            value = event["value"]
            seen["cards"].append(value)
            seen["calls_at_card"] = list(srv.calls)
            executor._pending_user_input[value["request_id"]].set_result({"answer": answer})

    with structlog.testing.capture_logs() as logs:
        events, _built = drive_native(
            _AGENT, "apps/agents/agent-apis-config", monkeypatch, model,
            thread_id="thread-mcp-autoload", extra_payload=payload, on_event=_on_event,
            session_user=session_user)
    assert not [e for e in events if e.get("type") == "RUN_ERROR"], events
    return events, seen, logs


def _assert_ran_once(srv: Any, events: list[dict], logs: list[dict], model: ScriptedModel,
                     *, call: str = "ping", tool: str = "echo") -> None:
    assert srv.calls == [call], "the call did not run exactly once"
    assert _events(logs, "unknown_tool.answered") == [], "the call still read as unknown"
    assert [e["outcome"] for e in _events(logs, "mcp.call")] == ["ok"]
    assert _autoloads(logs) == [(_SERVER, tool)]
    assert MEMBER_LINE not in json.dumps(events), "the chat drew a failed step"
    assert any(f"echo:{call}" in r for r in _tool_results(model))
    assert f"{_SERVER}_{tool}" in _offered(model.bodies[-1]), "the next request lost the tool"


@pytest.mark.usefixtures("_a_tenant")
@pytest.mark.filterwarnings("error::agent_framework._feature_stage.ExperimentalWarning")
def test_a_call_right_after_the_list_runs_in_the_same_step_end_to_end(monkeypatch) -> None:
    """The production sequence: list, then the call with no load."""
    with local_mcp_server(filler=10) as srv:
        model = ScriptedModel([
            tool_turn(f"{_SERVER}_list_mcp_tools", "{}", call_id="call_1"),
            tool_turn(f"{_SERVER}_echo", '{"text": "ping"}', call_id="call_2"),
            text_turn("done"),
        ])
        events, seen, logs = _drive(monkeypatch, srv, model)
    assert f"{_SERVER}_echo" not in _offered(model.bodies[0]), "the tool was loaded at the start"
    _assert_ran_once(srv, events, logs, model)
    assert seen["cards"] == []
    assert [e["action"] for e in _events(logs, "mcp.loader")] == ["list", "autoload"]
    assert _steps(events)[-1][1] is True


@pytest.mark.usefixtures("_a_tenant")
def test_a_first_call_from_the_history_runs_end_to_end(monkeypatch) -> None:
    """The first request of a run calls a tool that an earlier turn loaded."""
    with local_mcp_server(filler=10) as srv:
        model = ScriptedModel([tool_turn(f"{_SERVER}_echo", '{"text": "ping"}'), text_turn("ok")])
        events, seen, logs = _drive(monkeypatch, srv, model)
    _assert_ran_once(srv, events, logs, model)
    assert seen["cards"] == []
    assert [ok for _c, ok in _steps(events)] == [True]


@pytest.mark.usefixtures("_a_tenant")
def test_a_made_up_name_under_the_prefix_keeps_the_unknown_tool_answer_end_to_end(
        monkeypatch) -> None:
    with local_mcp_server(filler=10) as srv:
        model = ScriptedModel([
            tool_turn(f"{_SERVER}_ZohoMCP_listProducts", "{}"), text_turn("ok")])
        events, _seen, logs = _drive(monkeypatch, srv, model)
    assert srv.calls == []
    assert _autoloads(logs) == []
    assert len(_events(logs, "unknown_tool.answered")) == 1
    assert _events(logs, "mcp.call") == []
    assert (MEMBER_LINE, False) in _steps(events)


@pytest.mark.usefixtures("_a_tenant")
def test_a_name_under_no_attached_prefix_keeps_the_unknown_tool_answer_end_to_end(
        monkeypatch) -> None:
    with local_mcp_server(filler=10) as srv:
        model = ScriptedModel([tool_turn("other-srv_echo", '{"text": "ping"}'), text_turn("ok")])
        events, _seen, logs = _drive(monkeypatch, srv, model)
    assert srv.calls == []
    assert _autoloads(logs) == []
    assert len(_events(logs, "unknown_tool.answered")) == 1
    assert (MEMBER_LINE, False) in _steps(events)


@pytest.mark.usefixtures("_a_tenant")
def test_a_reserved_name_keeps_the_unknown_tool_answer_end_to_end(monkeypatch) -> None:
    """Review P2: ``_inject_mcp_servers`` hands the run's first-party names
    (``_reserved_tool_names``) to the autoload. Here the run reserves the
    local name of the server's ``echo``, so the call never reaches it."""
    real = _tool_injection._reserved_tool_names

    def _reserved(agent: Any) -> frozenset[str]:
        return frozenset(real(agent) | {f"{_SERVER}_echo"})

    monkeypatch.setattr(_tool_injection, "_reserved_tool_names", _reserved)
    with local_mcp_server(filler=10) as srv:
        model = ScriptedModel([tool_turn(f"{_SERVER}_echo", '{"text": "ping"}'), text_turn("ok")])
        events, _seen, logs = _drive(monkeypatch, srv, model)
    assert _events(logs, "mcp.attached"), "the server did not attach"
    assert srv.calls == []
    assert _autoloads(logs) == []
    assert len(_events(logs, "unknown_tool.answered")) == 1
    assert (MEMBER_LINE, False) in _steps(events)


@pytest.mark.usefixtures("_a_tenant")
def test_a_server_that_the_grant_refuses_never_loads_end_to_end(monkeypatch) -> None:
    """The member's grant refuses the server, so it never attaches. A call of
    its tool keeps today's answer, and the server sees no call."""
    with local_mcp_server(filler=10) as srv:
        model = ScriptedModel([tool_turn(f"{_SERVER}_echo", '{"text": "ping"}'), text_turn("ok")])
        events, _seen, logs = _drive(monkeypatch, srv, model,
                                     refuse=_maf_mcp.mcp_service(_SERVER))
    assert [e["name"] for e in _events(logs, "mcp.not_authorized")] == [_SERVER]
    assert srv.calls == []
    assert _autoloads(logs) == []
    assert len(_events(logs, "unknown_tool.answered")) == 1
    assert (MEMBER_LINE, False) in _steps(events)


@pytest.mark.usefixtures("_a_tenant")
def test_a_policy_refusal_still_refuses_an_autoloaded_call_end_to_end(monkeypatch) -> None:
    """The same ``decide()``: a policy that refuses the call refuses it."""
    real = _tool_injection._permission_gate

    def _refuse_mcp(name: str, arguments: Any, *, surface: str = "", **kw: Any) -> Any:
        if surface == "mcp_tool":
            return False, "test_refused"
        return real(name, arguments, surface=surface, **kw)

    monkeypatch.setattr(_tool_injection, "_permission_gate", _refuse_mcp)
    with local_mcp_server(filler=10) as srv:
        model = ScriptedModel([tool_turn(f"{_SERVER}_echo", '{"text": "ping"}'), text_turn("ok")])
        _events_out, _seen, logs = _drive(monkeypatch, srv, model)
    assert srv.calls == []
    assert _autoloads(logs) == [(_SERVER, "echo")]
    assert [e["outcome"] for e in _events(logs, "mcp.call")] == ["refused"]
    assert any("[blocked by permission policy: test_refused]" in r for r in _tool_results(model))


@pytest.mark.parametrize(("answer", "calls", "outcome"), [
    ("APPROVE", ["ping"], "card_approved"),
    ("REJECT", [], "card_denied"),
])
@pytest.mark.usefixtures("_a_tenant")
def test_an_autoloaded_call_in_a_chat_that_read_mail_asks_the_card_end_to_end(
        monkeypatch, answer: str, calls: list[str], outcome: str) -> None:
    """EM-G11d: each MCP call in a marked chat asks the member's card before
    it runs. An autoloaded call is no exception."""
    async def _marked(*_a: Any, **_k: Any) -> bool:
        return True

    monkeypatch.setattr(egress, "session_is_marked", _marked)
    with local_mcp_server(filler=10) as srv:
        model = ScriptedModel([tool_turn(f"{_SERVER}_echo", '{"text": "ping"}'), text_turn("ok")])
        _events_out, seen, logs = _drive(monkeypatch, srv, model, answer=answer)
    assert len(seen["cards"]) == 1, "the autoloaded call showed no card"
    assert seen["calls_at_card"] == [], "the call ran before the card"
    assert srv.calls == calls
    assert _autoloads(logs) == [(_SERVER, "echo")]
    assert [e["outcome"] for e in _events(logs, "mcp.call")] == [outcome]


@pytest.mark.usefixtures("_a_tenant")
def test_an_autoloaded_write_in_a_run_with_no_member_is_refused_end_to_end(monkeypatch) -> None:
    """MCP-NC1 and H-299: a run with no member refuses a tool that does not
    say read-only. The autoload changes nothing in that rule."""
    with local_mcp_server(filler=10) as srv:
        model = ScriptedModel([
            tool_turn(f"{_SERVER}_delete_record", '{"record_id": "r-9"}'), text_turn("ok")])
        _events_out, seen, logs = _drive(monkeypatch, srv, model, member=False)
    assert seen["cards"] == []
    assert srv.calls == []
    assert _autoloads(logs) == [(_SERVER, "delete_record")]
    assert any("no member can confirm" in r for r in _tool_results(model))


@pytest.mark.usefixtures("_a_tenant")
def test_a_run_with_no_mcp_server_never_autoloads_end_to_end(monkeypatch) -> None:
    """``MAF_MCP_ORGS`` is off for the org, so no server attaches. The call
    keeps today's answer."""
    with local_mcp_server(filler=10) as srv:
        model = ScriptedModel([tool_turn(f"{_SERVER}_echo", '{"text": "ping"}'), text_turn("ok")])
        events, _seen, logs = _drive(monkeypatch, srv, model, flag="")
    assert _events(logs, "mcp.attached") == []
    assert srv.calls == []
    assert _autoloads(logs) == []
    assert len(_events(logs, "unknown_tool.answered")) == 1
    assert (MEMBER_LINE, False) in _steps(events)
