"""WS-8c review round 2 — an MCP server can never hold a run, or swallow a Stop.

Spec: ``project-docs/specs/agent_architecture.md`` §12.2 WS-8c. These fences
are the repro scripts of the second review of PR #869 (``slow_connect.py``,
``leak.py`` and ``slow_delete.py``), turned into tests. Each server is an
in-process ``httpx.MockTransport`` under the REAL pinned client and the REAL
``MCPStreamableHTTPTool``, so the MCP handshake is real and no socket opens.

* **Slow connect.** An ``initialize`` that runs past the connect budget
  costs the run the budget and no more. MAF cancels only the waiter, so the
  client closes FIRST and the lifecycle task of MAF stops. No task leaks.
* **Endless paging.** A server that always sends ``nextCursor`` is cut at
  ``MAX_TOOL_PAGES``, and a server past ``MAX_TOOLS`` tools is left out.
* **Stop.** A cancel during the attach raises ``CancelledError`` to the run,
  after the half-open server is closed.
* **Slow DELETE.** The end of a run closes the servers at the same time,
  under a short cap. A 40 s session DELETE does not hold it, and the run is
  marked inactive BEFORE the close starts (the executor ``finally``).
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import httpx
import pytest
import structlog

pytest.importorskip("agent_framework", reason="agent_framework not installed")
pytest.importorskip("mcp", reason="mcp not installed")

from gateway import outbound_guard  # noqa: E402
from orchestrator import _maf_mcp  # noqa: E402

from tests.unit._maf_mcp_fakes import RegistrySpy, native_agent, row, set_flag  # noqa: E402
from tests.unit._native_maf_harness import (  # noqa: E402
    ScriptedModel,
    _a_tenant,  # noqa: F401 — a fixture, used by name
    drive_native,
    text_turn,
)

URL = "https://mcp.example.com/mcp"


def _init_result(body: dict[str, Any]) -> dict[str, Any]:
    return {
        "protocolVersion": body["params"]["protocolVersion"],
        "capabilities": {"tools": {}}, "serverInfo": {"name": "probe", "version": "1"},
    }


class _Server:
    """An MCP server behind ``httpx.MockTransport``. Each knob is one repro."""

    def __init__(self, *, slow_init: float = 0.0, endless: bool = False,
                 tools_per_page: int = 1, slow_delete: float = 0.0) -> None:
        self.slow_init = slow_init
        self.endless = endless
        self.tools_per_page = tools_per_page
        self.slow_delete = slow_delete
        self.pages = 0
        self.deletes = 0

    async def handle(self, request: httpx.Request) -> httpx.Response:
        sid = {"mcp-session-id": "s1"}
        if request.method == "DELETE":
            self.deletes += 1
            await asyncio.sleep(self.slow_delete)
            return httpx.Response(204)
        if request.method == "GET":
            return httpx.Response(405)
        body = json.loads(request.content or b"{}")
        rid, method = body.get("id"), body.get("method")
        if method == "initialize":
            await asyncio.sleep(self.slow_init)
            return httpx.Response(200, headers=sid, json={
                "jsonrpc": "2.0", "id": rid, "result": _init_result(body)})
        if rid is None:
            return httpx.Response(202, headers=sid)
        if method == "tools/list":
            self.pages += 1
            tools = [{"name": f"t{self.pages}_{i}", "inputSchema": {"type": "object"}}
                     for i in range(self.tools_per_page)]
            result: dict[str, Any] = {"tools": tools}
            if self.endless:
                result["nextCursor"] = str(self.pages)
            return httpx.Response(200, headers=sid, json={
                "jsonrpc": "2.0", "id": rid, "result": result})
        return httpx.Response(200, headers=sid, json={"jsonrpc": "2.0", "id": rid, "result": {}})

    def install(self, monkeypatch: pytest.MonkeyPatch) -> _Server:
        async def _check(url: str) -> outbound_guard.Target:
            return outbound_guard.Target(url=httpx.URL(url), host="mcp.example.com",
                                         ip="93.184.216.34")

        monkeypatch.setattr(outbound_guard, "check_url", _check)
        monkeypatch.setattr(outbound_guard, "_mcp_inner_transport",
                            lambda: httpx.MockTransport(self.handle))
        return self


def _live_owner_tasks() -> list[str]:
    return [t.get_name() for t in asyncio.all_tasks()
            if t.get_name().startswith("mcp-lifecycle") and not t.done()]


def _server_spec(name: str = "probe") -> list[_maf_mcp.McpServer]:
    return [_maf_mcp.McpServer(name=name, url=URL)]


@pytest.fixture
def fast_budgets(monkeypatch):
    monkeypatch.setattr(_maf_mcp, "CONNECT_BUDGET_S", 1.0)
    monkeypatch.setattr(_maf_mcp, "CLOSE_BUDGET_S", 3.0)
    monkeypatch.setattr(_maf_mcp, "CLOSE_TOTAL_S", 5.0)


# ── slow_connect.py and leak.py ─────────────────────────────────────────────


@pytest.mark.usefixtures("fast_budgets")
async def test_a_slow_initialize_costs_the_budget_and_leaks_no_task(monkeypatch):
    srv = _Server(slow_init=8.0).install(monkeypatch)
    agent, opened = native_agent(), []
    t0 = time.monotonic()
    with structlog.testing.capture_logs() as logs:
        names = await _maf_mcp.attach_mcp_tools(agent, _server_spec(), opened)
    took = time.monotonic() - t0
    assert names == [] and opened == [] and agent.mcp_tools == []
    assert took < _maf_mcp.CONNECT_BUDGET_S + 1.5, (
        f"attach took {took:.1f}s against a {_maf_mcp.CONNECT_BUDGET_S}s budget: the "
        "close waited behind the connect")
    assert [e for e in logs if e["event"] == "mcp.unreachable"]
    await asyncio.sleep(0.2)
    assert _live_owner_tasks() == [], "the lifecycle task of MAF outlived the attach"
    assert srv.pages == 0


@pytest.mark.usefixtures("fast_budgets")
async def test_endless_paging_is_cut_at_the_page_cap(monkeypatch):
    srv = _Server(endless=True).install(monkeypatch)
    agent, opened = native_agent(), []
    with structlog.testing.capture_logs() as logs:
        names = await _maf_mcp.attach_mcp_tools(agent, _server_spec(), opened)
    assert names == [] and opened == []
    assert srv.pages == _maf_mcp.MAX_TOOL_PAGES, srv.pages
    down = [e for e in logs if e["event"] == "mcp.unreachable"]
    assert down and down[0]["reason"] == "McpListTooLong"


@pytest.mark.usefixtures("fast_budgets")
async def test_a_server_past_the_tool_cap_is_left_out(monkeypatch):
    _Server(tools_per_page=_maf_mcp.MAX_TOOLS + 1).install(monkeypatch)
    agent, opened = native_agent(), []
    names = await _maf_mcp.attach_mcp_tools(agent, _server_spec(), opened)
    assert names == [] and opened == []


@pytest.mark.usefixtures("fast_budgets")
async def test_a_server_inside_the_caps_attaches(monkeypatch):
    _Server(tools_per_page=3).install(monkeypatch)
    agent, opened = native_agent(), []
    names = await _maf_mcp.attach_mcp_tools(agent, _server_spec(), opened)
    try:
        assert names == ["probe"]
        assert sorted(f.name for f in agent.mcp_tools[0].functions) == [
            "probe_t1_0", "probe_t1_1", "probe_t1_2"]
    finally:
        await _maf_mcp.close_mcp_tools(opened)


# ── Stop during the attach ──────────────────────────────────────────────────


@pytest.mark.usefixtures("fast_budgets")
async def test_a_cancel_during_the_attach_reaches_the_run(monkeypatch):
    monkeypatch.setattr(_maf_mcp, "CONNECT_BUDGET_S", 30.0)
    _Server(slow_init=20.0).install(monkeypatch)
    agent, opened = native_agent(), []
    task = asyncio.create_task(_maf_mcp.attach_mcp_tools(agent, _server_spec(), opened))
    await asyncio.sleep(0.5)
    task.cancel()
    t0 = time.monotonic()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert time.monotonic() - t0 < 5, "the cancel waited on the half-open server"
    assert opened == [] and agent.mcp_tools == []
    await asyncio.sleep(0.2)
    assert _live_owner_tasks() == []


async def test_close_one_lets_a_cancel_through() -> None:
    """``_close_one`` suppresses ``Exception`` only (review round 2)."""

    class _Tool:
        async def close(self) -> None:
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await _maf_mcp._close_one(_Tool(), None)


# ── slow_delete.py, and the cap of the end of the run ───────────────────────


@pytest.mark.usefixtures("fast_budgets")
async def test_a_slow_session_delete_does_not_hold_the_close(monkeypatch):
    monkeypatch.setattr(_maf_mcp, "CLOSE_BUDGET_S", 1.0)
    monkeypatch.setattr(_maf_mcp, "CLOSE_TOTAL_S", 3.0)
    srv = _Server(slow_delete=40.0).install(monkeypatch)
    agent, opened = native_agent(), []
    assert await _maf_mcp.attach_mcp_tools(agent, _server_spec(), opened) == ["probe"]
    t0 = time.monotonic()
    await _maf_mcp.close_mcp_tools(opened)
    took = time.monotonic() - t0
    assert srv.deletes == 1
    assert took < _maf_mcp.CLOSE_BUDGET_S + 1.0, f"close took {took:.1f}s"
    assert opened == []
    await asyncio.sleep(0.2)
    assert _live_owner_tasks() == []


async def test_the_close_of_a_run_has_one_total_cap(monkeypatch):
    """A client close that hangs cannot hold the run either."""
    monkeypatch.setattr(_maf_mcp, "CLOSE_TOTAL_S", 1.0)

    class _Tool:
        async def close(self) -> None:
            return None

    class _HangingClient:
        async def aclose(self) -> None:
            await asyncio.sleep(30)

    opened: list[Any] = [(_Tool(), _HangingClient()), (_Tool(), _HangingClient())]
    t0 = time.monotonic()
    await _maf_mcp.close_mcp_tools(opened)
    assert time.monotonic() - t0 < _maf_mcp.CLOSE_TOTAL_S + 1.5
    assert opened == []


@pytest.mark.usefixtures("_a_tenant")
def test_a_40s_delete_never_keeps_the_run_live_end_to_end(monkeypatch):
    """The REAL run: the run is marked inactive before the MCP close starts,
    and the whole end of the run stays under the close cap."""
    from orchestrator import stream_relay

    order: list[str] = []
    stamps: dict[str, float] = {}

    async def _mark_inactive(*_a: Any, **_k: Any) -> None:
        order.append("mark_inactive")
        stamps["mark_inactive"] = time.monotonic()

    real_close = _maf_mcp.close_mcp_tools

    async def _close(opened: Any) -> None:
        order.append("close")
        stamps["close_start"] = time.monotonic()
        await real_close(opened)
        stamps["close_end"] = time.monotonic()

    monkeypatch.setattr(stream_relay, "mark_inactive", _mark_inactive)
    monkeypatch.setattr(_maf_mcp, "close_mcp_tools", _close)
    srv = _Server(slow_delete=40.0).install(monkeypatch)
    set_flag(monkeypatch, "*")
    RegistrySpy([row("probe-srv", org="org-native-maf-probe", url=URL)]).install(monkeypatch)
    model = ScriptedModel([text_turn("done")])
    t0 = time.monotonic()
    events, built = drive_native(
        "apis-config", "apps/agents/agent-apis-config", monkeypatch, model,
        thread_id="thread-ws8c-slow-delete",
    )
    took = time.monotonic() - t0
    assert not [e for e in events if e.get("type") == "RUN_ERROR"], events
    assert [t.name for t in built[0].mcp_tools] == ["probe-srv"]
    assert order == ["mark_inactive", "close"], order
    assert stamps["close_end"] - stamps["close_start"] < _maf_mcp.CLOSE_TOTAL_S + 1.5
    assert took < 30, f"the run took {took:.1f}s: the 40 s DELETE held it"
    assert srv.deletes == 1
