"""WS-8c — MCP servers reach a native MAF agent, and only under the rules.

Spec: ``project-docs/specs/agent_architecture.md`` §12.2 WS-8c.

Each fence names the acceptance item it covers:

* **A1** With ``MAF_MCP_ORGS`` off, a native agent gets no MCP tool and the
  run reads no row. The Copilot path still gets its ``http-sse`` rows.
* **A3** With no org, nothing is read on either path.
* **A4** A ``no_egress`` run gets no MCP tool: the executor reads no row, and
  the guard view carries no ``mcp_tools``.
* **A5** A deny on ``integrations:use:mcp-<name>`` removes that server.
* **A6** (the run half) A refused server is left out, and the run goes on.
* **A8** A header value never reaches a log line: on success, on a failed
  connect and on a refusal.
* **A10** The WS-43q done-when lists WS-8c, and the §15.2 anchor is right.
* **A11** An unreachable server does not fail the run, and the run closes
  each MCP connection when it ends.

The end-to-end tests drive the REAL apis-config agent through the REAL
``run_agent_stream`` (``_native_maf_harness.drive_native``), against a REAL
streamable-HTTP MCP server on ``127.0.0.1``. Only the model wire is scripted.
The guard's pin is stubbed for that one test URL, inside the test.

The R8 half of the org rule (A2, two orgs) is
``tests/unit/test_maf_mcp_org_scope_r8.py``.
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import httpx
import pytest
import structlog

pytest.importorskip("agent_framework", reason="agent_framework not installed")
pytest.importorskip("mcp", reason="mcp not installed")

from acb_common.db import bind_tenant, clear_tenant, release_tenant
from gateway import outbound_guard
from orchestrator import _maf_mcp
from orchestrator._tool_injection import _inject_mcp_servers

from tests.unit._maf_mcp_fakes import (
    CopilotStub,
    FakeClient,
    RegistrySpy,
    install_offline,
    local_mcp_server,
    logged_text,
    native_agent,
    row,
    set_flag,
)
from tests.unit._native_maf_harness import (
    ScriptedModel,
    _a_tenant,  # noqa: F401 — a fixture, used by name
    drive_native,
    text_turn,
    tool_turn,
)

REPO = Path(__file__).resolve().parents[2]
ORG_A = "aaaaaaaa-0000-0000-0000-00000000000a"
ORG_B = "bbbbbbbb-0000-0000-0000-00000000000b"
SENTINEL = "Bearer sentinel-ws8c-9f3e"
PUBLIC_IP = "93.184.216.34"
_AGENT = "apis-config"


@pytest.fixture(autouse=True)
def _no_tenant():
    """Each test starts with no tenant bound and no run on the frame."""
    clear_tenant()
    yield
    clear_tenant()


@pytest.fixture
def offline(monkeypatch):
    """No network. See ``_maf_mcp_fakes.install_offline``."""
    return install_offline(monkeypatch, secret=SENTINEL)


def _names(agent: Any) -> list[str]:
    return [t.name for t in getattr(agent, "mcp_tools", [])]


# ── A1: the flag ─────────────────────────────────────────────────────────────


async def test_flag_off_a_native_agent_gets_nothing_and_reads_nothing(monkeypatch, offline):
    set_flag(monkeypatch, "")
    spy = RegistrySpy([row("srv-a", org=ORG_A)]).install(monkeypatch)
    agent = native_agent()
    await _inject_mcp_servers(agent, _AGENT, organization_id=ORG_A, opened=[])
    assert _names(agent) == []
    assert spy.reads == 0, "a flag-off native agent read the registry"


async def test_a_flag_for_another_org_reads_nothing(monkeypatch, offline):
    set_flag(monkeypatch, ORG_B)
    spy = RegistrySpy([row("srv-a", org=ORG_A)]).install(monkeypatch)
    agent = native_agent()
    await _inject_mcp_servers(agent, _AGENT, organization_id=ORG_A, opened=[])
    assert _names(agent) == [] and spy.reads == 0


@pytest.mark.parametrize("flag", ["", "*"], ids=["flag-off", "flag-on"])
async def test_a_copilot_agent_gets_no_registry_server(monkeypatch, flag, caplog):
    """Review round 2 of #869 (P0). Registry MCP is MAF-only. The Copilot
    CLI connects with its own client, with no pin, flag or grant, so a row
    must never reach it. Each in-scope row of the run's org is logged by name
    and dropped. The read still names the org, so org B's row is not even
    logged."""
    set_flag(monkeypatch, flag)
    spy = RegistrySpy([
        row("srv-a", org=ORG_A, url="https://169.254.169.254/latest",
            headers={"Authorization": SENTINEL}),
        row("srv-b", org=ORG_B),
    ]).install(monkeypatch)
    agent = CopilotStub()
    with structlog.testing.capture_logs() as logs:
        await _inject_mcp_servers(agent, _AGENT, organization_id=ORG_A)
    assert agent.servers == {} and agent._default_options == {}
    skipped = [e for e in logs if e["event"] == "mcp.copilot_registry_skipped"]
    assert skipped == [{"event": "mcp.copilot_registry_skipped", "name": "srv-a",
                        "log_level": "info"}]
    # IN-4: the one registry read, for the run's org only.
    assert spy.statements == [("servers_for_org", {"org": ORG_A})]
    text = logged_text(logs, caplog)
    assert SENTINEL not in text and "169.254" not in text


# ── A3: no org ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("make", [native_agent, CopilotStub], ids=["native", "copilot"])
async def test_no_org_reads_no_row(make, monkeypatch, offline):
    set_flag(monkeypatch, "*")
    spy = RegistrySpy([row("srv-a", org=ORG_A)]).install(monkeypatch)
    from orchestrator import executor

    assert executor._current_run_org() is None  # precondition
    agent = make()
    await _inject_mcp_servers(agent, _AGENT, opened=[])
    assert spy.reads == 0
    assert _names(agent) == [] and getattr(agent, "_default_options", {}) == {}


async def test_the_bound_tenant_is_the_org_when_none_is_passed(monkeypatch, offline):
    set_flag(monkeypatch, ORG_A)
    spy = RegistrySpy([row("srv-a", org=ORG_A), row("srv-b", org=ORG_B)]).install(monkeypatch)
    agent = native_agent()
    tok = bind_tenant(ORG_A)
    try:
        await _inject_mcp_servers(agent, _AGENT, opened=[])
    finally:
        release_tenant(tok)
    assert _names(agent) == ["srv-a"]
    assert spy.statements[0][1] == {"org": ORG_A}


# ── The tool shape ───────────────────────────────────────────────────────────


async def test_flag_on_builds_one_pinned_tool_per_row(monkeypatch, offline):
    from agent_framework import MCPStreamableHTTPTool

    set_flag(monkeypatch, ORG_A)
    RegistrySpy([
        row("srv-a", org=ORG_A, headers={"Authorization": SENTINEL}),
        row("srv-c", org=ORG_A, url="https://other.example.com/mcp"),
        row("srv-b", org=ORG_B),
    ]).install(monkeypatch)
    agent = native_agent()
    opened: list[Any] = []
    await _inject_mcp_servers(agent, _AGENT, organization_id=ORG_A, opened=opened)

    assert _names(agent) == ["srv-a", "srv-c"]
    for tool, client in zip(agent.mcp_tools, offline["clients"], strict=True):
        assert isinstance(tool, MCPStreamableHTTPTool)
        assert tool._httpx_client is client, "the tool did not get the pinned client"
        assert tool.url == client.url
        assert tool.tool_name_prefix == tool.name
        assert tool.approval_mode is None, "the injection must not set approval_mode"
    assert agent.mcp_tools[0]._static_headers == {"Authorization": SENTINEL}
    assert [t for t, _ in opened] == agent.mcp_tools
    assert any(isinstance(m, _maf_mcp.McpPermissionGate) for m in agent.middleware or [])


async def test_a_stdio_row_and_an_sse_row_never_reach_a_native_agent(monkeypatch, offline):
    set_flag(monkeypatch, "*")
    RegistrySpy([
        row("srv-io", org=ORG_A, transport="stdio", url=""),
        row("srv-sse", org=ORG_A, transport="sse"),
        row("srv-ok", org=ORG_A),
    ]).install(monkeypatch)
    agent = native_agent()
    await _inject_mcp_servers(agent, _AGENT, organization_id=ORG_A, opened=[])
    assert _names(agent) == ["srv-ok"]


# ── A5: the grant ────────────────────────────────────────────────────────────


async def test_a_denied_grant_removes_that_server(monkeypatch, offline):
    set_flag(monkeypatch, "*")
    RegistrySpy([row("srv-a", org=ORG_A), row("srv-x", org=ORG_A)]).install(monkeypatch)
    asked: list[str] = []

    def _authz(service: str) -> bool:
        asked.append(service)
        return service != "mcp-srv-x"

    agent = native_agent()
    with structlog.testing.capture_logs() as logs:
        await _inject_mcp_servers(
            agent, _AGENT, organization_id=ORG_A, is_authorized=_authz, opened=[],
        )
    assert _names(agent) == ["srv-a"]
    assert asked == ["mcp-srv-a", "mcp-srv-x"]
    denied = [e for e in logs if e["event"] == "mcp.not_authorized"]
    assert denied == [{"event": "mcp.not_authorized", "name": "srv-x", "log_level": "info"}]


def test_the_grant_is_the_integrations_use_permission() -> None:
    from acb_auth import integration_use_permission

    assert integration_use_permission(_maf_mcp.mcp_service("crm")) == "integrations:use:mcp-crm"


# ── A6 (run half) and A8: a refused server is left out, and logs no secret ───


async def test_a_refused_server_is_left_out_and_logs_no_header(monkeypatch, caplog):
    set_flag(monkeypatch, "*")
    RegistrySpy([
        row("srv-bad", org=ORG_A, url="https://inside.example.com/mcp?k=secret-q",
            headers={"Authorization": SENTINEL}),
    ]).install(monkeypatch)
    monkeypatch.setattr(outbound_guard, "_resolve", lambda host, port: ["10.0.0.7"])
    agent = native_agent()
    with structlog.testing.capture_logs() as logs:
        await _inject_mcp_servers(agent, _AGENT, organization_id=ORG_A, opened=[])
    assert _names(agent) == []
    refused = [e for e in logs if e["event"] == "mcp.refused"]
    assert refused and refused[0]["host"] == "inside.example.com"
    assert refused[0]["reason"] == "not public"
    text = logged_text(logs, caplog)
    assert SENTINEL not in text and "secret-q" not in text


# ── A11 and A8: an unreachable server does not fail, and is closed ──────────


async def test_an_unreachable_server_is_closed_and_the_rest_attach(monkeypatch, offline, caplog):
    set_flag(monkeypatch, "*")
    RegistrySpy([
        row("srv-down", org=ORG_A, url="https://down.example.com/mcp/path-secret",
            headers={"Authorization": SENTINEL}),
        row("srv-up", org=ORG_A),
    ]).install(monkeypatch)
    offline["fail"].add("srv-down")
    agent = native_agent()
    opened: list[Any] = []
    with structlog.testing.capture_logs() as logs:
        await _inject_mcp_servers(agent, _AGENT, organization_id=ORG_A, opened=opened)
    assert _names(agent) == ["srv-up"]
    assert offline["closed"] == ["srv-down"]
    assert offline["clients"][0].closed is True, "the failed server's client stayed open"
    down = [e for e in logs if e["event"] == "mcp.unreachable"]
    assert down and down[0]["host"] == "down.example.com"
    assert down[0]["reason"] == "ConnectError"
    text = logged_text(logs, caplog)
    assert SENTINEL not in text and "path-secret" not in text


async def test_close_mcp_tools_closes_each_tool_and_client(offline):
    from orchestrator._tool_injection import _close_mcp_tools

    class _Tool:
        def __init__(self, name: str) -> None:
            self.name = name

        async def close(self) -> None:
            offline["closed"].append(self.name)

    clients = [FakeClient("u1"), FakeClient("u2")]
    opened: list[Any] = [(_Tool("t1"), clients[0]), (_Tool("t2"), clients[1])]
    await _close_mcp_tools(opened)
    assert sorted(offline["closed"]) == ["t1", "t2"]
    assert all(c.closed for c in clients) and opened == []


def test_the_executor_closes_what_the_run_opened() -> None:
    """The stream run passes its ``opened`` list and closes it in ``finally``."""
    src = (REPO / "apps/services/orchestrator/orchestrator/executor.py").read_text(
        encoding="utf-8")
    tree = ast.parse(src)
    run = next(n for n in ast.walk(tree)
               if isinstance(n, ast.AsyncFunctionDef) and n.name == "run_agent_stream")
    calls = [n for n in ast.walk(run) if isinstance(n, ast.Call)
             and getattr(n.func, "id", None) == "_inject_mcp_servers"]
    assert len(calls) == 1
    kw = {k.arg: k.value for k in calls[0].keywords}
    assert getattr(kw.get("organization_id"), "id", None) == "organization_id", (
        "the org must be the server-side stamp, never the payload")
    assert getattr(kw.get("opened"), "id", None) == "_mcp_opened"
    assert getattr(kw.get("is_authorized"), "id", None) == "_authorizer"
    finals = [t for t in ast.walk(run) if isinstance(t, ast.Try) and t.finalbody]
    closes = [c for t in finals for s in t.finalbody for c in ast.walk(s)
              if isinstance(c, ast.Call) and getattr(c.func, "id", None) == "_close_mcp_tools"]
    assert closes, "no finally of run_agent_stream closes the MCP tools"


# ── The tool gate (decide) ───────────────────────────────────────────────────


async def test_an_mcp_call_goes_through_decide(monkeypatch):
    from acb_skills import permission_policy

    seen: list[str] = []

    def _deny(request: Any) -> tuple[bool, str, str]:
        seen.append(str(getattr(request, "tool_name", None) or request.get("tool_name")))
        return False, "probe_denied", ""

    monkeypatch.setattr(permission_policy, "decide", _deny)
    monkeypatch.setenv("AGENT_PERMISSION_MODE", "enforce")

    from types import SimpleNamespace

    mcp_fn = SimpleNamespace(name="srv-a_delete_all",
                             additional_properties={"_mcp_is_tool": True})
    ctx = SimpleNamespace(function=mcp_fn, arguments={"confirm": True}, result=None)
    ran: list[bool] = []

    async def _next() -> None:
        ran.append(True)

    await _maf_mcp.McpPermissionGate().process(ctx, _next)
    assert ran == [] and ctx.result == "[blocked by permission policy: probe_denied]"
    assert seen == ["srv-a_delete_all"]

    own = SimpleNamespace(name="web_search", additional_properties={})
    ctx2 = SimpleNamespace(function=own, arguments={}, result=None)
    await _maf_mcp.McpPermissionGate().process(ctx2, _next)
    assert ran == [True], "the MCP gate must leave a non-MCP function to its own gate"


# ── Review of PR #869: a server must never fail the run after the attach ────


async def test_a_server_whose_names_collide_is_left_out(monkeypatch, offline):
    """A server ``web`` with a tool ``search`` exposes ``web_search``, which
    the agent already holds. MAF refuses a duplicate name when the run starts,
    so the server must be left out at the attach, and closed."""
    from agent_framework import FunctionTool, MCPStreamableHTTPTool

    async def _connect(self: Any, *, reset: bool = False) -> None:
        self._functions.append(FunctionTool(
            name=f"{self.tool_name_prefix}_search", func=lambda: "x",
            additional_properties={"_mcp_is_tool": True},
        ))

    monkeypatch.setattr(MCPStreamableHTTPTool, "connect", _connect)
    set_flag(monkeypatch, "*")
    RegistrySpy([row("web", org=ORG_A), row("crm", org=ORG_A)]).install(monkeypatch)
    agent = native_agent()
    agent.default_options["tools"] = [FunctionTool(name="web_search", func=lambda: "y")]
    with structlog.testing.capture_logs() as logs:
        await _inject_mcp_servers(agent, _AGENT, organization_id=ORG_A, opened=[])
    assert _names(agent) == ["crm"]
    assert offline["closed"] == ["web"] and offline["clients"][0].closed
    clash = [e for e in logs if e["event"] == "mcp.name_collision"]
    assert clash and clash[0]["name"] == "web" and clash[0]["tools"] == ["web_search"]


async def test_a_connected_tool_is_not_connected_again_by_the_agent(monkeypatch, offline):
    """``Agent.__aenter__`` (the native tier) enters each MCP tool again. A
    connected tool must not reconnect and list its tools a second time."""
    from agent_framework import MCPStreamableHTTPTool

    set_flag(monkeypatch, "*")
    RegistrySpy([row("srv-a", org=ORG_A)]).install(monkeypatch)
    agent = native_agent()
    await _inject_mcp_servers(agent, _AGENT, organization_id=ORG_A, opened=[])
    tool = agent.mcp_tools[0]
    connects: list[str] = []

    async def _count(self: Any, *, reset: bool = False) -> None:
        connects.append(self.name)

    monkeypatch.setattr(MCPStreamableHTTPTool, "connect", _count)
    tool.is_connected = True  # MAF sets this instance flag after initialize
    async with tool:
        pass
    assert connects == [], "a connected MCP tool was connected again"
    assert offline["closed"] == [], "leaving the agent's context closed the run's tool"


async def test_a_failed_second_entry_does_not_fail_the_run(monkeypatch, offline, caplog):
    """A tool that lost its session and cannot reconnect stays with no
    function. ``agent.run`` then goes on without that server."""
    from agent_framework import MCPStreamableHTTPTool

    set_flag(monkeypatch, "*")
    RegistrySpy([row("srv-a", org=ORG_A, headers={"Authorization": SENTINEL})]).install(
        monkeypatch)
    agent = native_agent()
    await _inject_mcp_servers(agent, _AGENT, organization_id=ORG_A, opened=[])
    tool = agent.mcp_tools[0]

    async def _boom(self: Any, *, reset: bool = False) -> None:
        raise httpx.ConnectError(f"429 from {self.url} {SENTINEL}")

    monkeypatch.setattr(MCPStreamableHTTPTool, "connect", _boom)
    tool.is_connected = False  # the session was lost
    with structlog.testing.capture_logs() as logs:
        async with tool:
            pass
    assert tool.functions == []
    assert [e for e in logs if e["event"] == "mcp.unreachable"]
    assert SENTINEL not in logged_text(logs, caplog)


# ── A4: no_egress ────────────────────────────────────────────────────────────


def test_the_no_egress_view_drops_mcp_tools_and_the_rule_picks_them() -> None:
    from acb_skills.egress import is_egress_tool
    from agent_framework import FunctionTool, MCPStreamableHTTPTool
    from orchestrator._tool_injection import _apply_no_egress

    agent = native_agent()
    agent.mcp_tools = [MCPStreamableHTTPTool(name="srv-a", url="https://mcp.example.com/mcp")]
    agents = [agent]
    _apply_no_egress(agents, _AGENT)
    assert agents[0].mcp_tools == []
    fn = FunctionTool(name="srv-a_x", func=lambda: "x",
                      additional_properties={"_mcp_is_tool": True})
    assert is_egress_tool(fn), "WithholdEgressTools would not withhold a late MCP tool"


# ── A10: the WS-43q precondition ─────────────────────────────────────────────


def test_ws43q_lists_ws8c_and_the_anchor_is_right() -> None:
    spec = (REPO / "project-docs/specs/maf_coding_engine.md").read_text(encoding="utf-8")
    assert "**WS-8c is merged, and its flag `MAF_MCP_ORGS` is ON for every org that" in spec
    assert "`merge_mcp_servers` (`_tool_injection.py:2042` at `c1850b5d9`)" in spec


# ── End to end: the real agent, the real executor, a real MCP server ────────


def _stub_pin(monkeypatch: pytest.MonkeyPatch, allowed_url: str) -> None:
    """Let the guard pin the ONE local test URL. Every other URL is checked."""
    real = outbound_guard.check_mcp_url

    async def _check(url: str) -> outbound_guard.Target:
        if url == allowed_url:
            return outbound_guard.Target(url=httpx.URL(url), host="127.0.0.1", ip="127.0.0.1")
        return await real(url)

    monkeypatch.setattr(outbound_guard, "check_mcp_url", _check)


def _tool_names(body: dict[str, Any]) -> set[str]:
    return {t["function"]["name"] for t in body.get("tools") or []}


@pytest.mark.usefixtures("_a_tenant")
def test_native_agent_gets_the_server_tools_end_to_end(monkeypatch, caplog):
    """The model calls the MCP tool, the server runs it, the result goes back,
    and the run closes the session at the end. An ``organization_id`` in the
    payload changes nothing (A2), and no header value reaches a log (A8)."""
    with local_mcp_server() as srv:
        set_flag(monkeypatch, "org-native-maf-probe")
        spy = RegistrySpy([
            row("probe-srv", org="org-native-maf-probe", url=srv.url,
                headers={"Authorization": SENTINEL}),
            row("other-org-srv", org=ORG_B, url=srv.url),
        ]).install(monkeypatch)
        _stub_pin(monkeypatch, srv.url)
        model = ScriptedModel([
            tool_turn("probe-srv_echo", '{"text": "ping"}'), text_turn("done"),
        ])
        with structlog.testing.capture_logs() as logs:
            events, built = drive_native(
                _AGENT, "apps/agents/agent-apis-config", monkeypatch, model,
                thread_id="thread-ws8c-e2e",
                extra_payload={"organization_id": ORG_B},
            )

        assert not [e for e in events if e.get("type") == "RUN_ERROR"], events
        assert [e.get("type") for e in events][-1] == "RUN_FINISHED"
        assert srv.calls == ["ping"], "the MCP server never ran the tool"
        offered = _tool_names(model.bodies[0])
        assert "probe-srv_echo" in offered
        assert not any(n.startswith("other-org-srv") for n in offered), offered
        assert spy.statements[0][1] == {"org": "org-native-maf-probe"}
        tool_msgs = [m for m in model.bodies[1]["messages"] if m.get("role") == "tool"]
        assert any("echo:ping" in str(m.get("content")) for m in tool_msgs), tool_msgs
        assert any(h.get("authorization") == SENTINEL for h in srv.headers_seen), (
            "the stored header never reached the server")
        tool = built[0].mcp_tools[0]
        assert tool.is_connected is False, "the run left the MCP session open"
        text = logged_text(logs, caplog)
        assert SENTINEL not in text


@pytest.mark.usefixtures("_a_tenant")
def test_a_no_egress_run_reads_no_row_end_to_end(monkeypatch):
    from orchestrator import executor

    with local_mcp_server() as srv:
        set_flag(monkeypatch, "*")
        spy = RegistrySpy([
            row("probe-srv", org="org-native-maf-probe", url=srv.url),
        ]).install(monkeypatch)
        _stub_pin(monkeypatch, srv.url)
        monkeypatch.setattr(executor, "_run_no_egress", lambda *a, **k: True)
        model = ScriptedModel([text_turn("done")])
        events, _built = drive_native(
            _AGENT, "apps/agents/agent-apis-config", monkeypatch, model,
            thread_id="thread-ws8c-no-egress",
        )
        assert spy.reads == 0, "a no_egress run read the MCP registry"
        assert not any(n.startswith("probe-srv") for n in _tool_names(model.bodies[0]))
        assert srv.calls == [] and srv.headers_seen == []
        assert not [e for e in events if e.get("type") == "RUN_ERROR"], events


@pytest.mark.usefixtures("_a_tenant")
def test_an_unreachable_server_does_not_fail_the_run_end_to_end(monkeypatch, caplog):
    set_flag(monkeypatch, "*")
    dead = "http://127.0.0.1:9/mcp"
    RegistrySpy([
        row("dead-srv", org="org-native-maf-probe", url=dead,
            headers={"Authorization": SENTINEL}),
    ]).install(monkeypatch)
    _stub_pin(monkeypatch, dead)
    model = ScriptedModel([text_turn("done")])
    with structlog.testing.capture_logs() as logs:
        events, built = drive_native(
            _AGENT, "apps/agents/agent-apis-config", monkeypatch, model,
            thread_id="thread-ws8c-dead",
        )
    assert not [e for e in events if e.get("type") == "RUN_ERROR"], events
    assert [e.get("type") for e in events][-1] == "RUN_FINISHED"
    assert built[0].mcp_tools == []
    assert [e for e in logs if e["event"] == "mcp.unreachable"]
    assert SENTINEL not in logged_text(logs, caplog)
