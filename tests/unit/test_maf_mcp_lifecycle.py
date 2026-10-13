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
* **A late owner.** When the MAF task ends after the wait of ``_stop_owner``,
  its own cancel fails the close. That is a failed close, not a Stop.

**No fence here reads a wall clock against a budget.** CI run 38301866619
failed ``took < 2.5`` at 3.04 s on a stalled runner: the same test call took
8.5 s, against 1.2 s in a healthy run, and 5.4 s of it was outside the
measured window. 350 local runs, 150 of them under 84 busy processes, never
saw the close wait on the connect. So each fence asserts the ORDER of the
events (:func:`_trace_close`), and fires a budget at an event, not at a time
(:class:`_Clock`). A budget that a fence does not fire is set long, so a slow
runner cannot reach it, and a regression fails on the order, not on a timer.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
import structlog

pytest.importorskip("agent_framework", reason="agent_framework not installed")
pytest.importorskip("mcp", reason="mcp not installed")

from gateway import outbound_guard
from orchestrator import _maf_mcp

from tests.unit._maf_mcp_fakes import RegistrySpy, native_agent, row, set_flag
from tests.unit._native_maf_harness import (
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
    """An MCP server behind ``httpx.MockTransport``. Each knob is one repro.

    ``hang_init`` holds the ``initialize`` until the request is cancelled, and
    calls ``on_init`` when it arrives. ``events`` gets ``init.start``,
    ``init.cancelled``, ``delete.start`` and ``delete.end``, in order."""

    def __init__(self, *, hang_init: bool = False, endless: bool = False,
                 tools_per_page: int = 1, slow_delete: float = 0.0,
                 events: list[str] | None = None,
                 on_init: Callable[[], None] | None = None) -> None:
        self.hang_init = hang_init
        self.endless = endless
        self.tools_per_page = tools_per_page
        self.slow_delete = slow_delete
        self.events = events if events is not None else []
        self.on_init = on_init
        self.pages = 0
        self.deletes = 0

    async def handle(self, request: httpx.Request) -> httpx.Response:
        sid = {"mcp-session-id": "s1"}
        if request.method == "DELETE":
            self.deletes += 1
            self.events.append("delete.start")
            await asyncio.sleep(self.slow_delete)
            self.events.append("delete.end")
            return httpx.Response(204)
        if request.method == "GET":
            return httpx.Response(405)
        body = json.loads(request.content or b"{}")
        rid, method = body.get("id"), body.get("method")
        if method == "initialize" and self.hang_init:
            self.events.append("init.start")
            if self.on_init is not None:
                self.on_init()
            try:
                await asyncio.Event().wait()  # no answer, ever
            except asyncio.CancelledError:
                self.events.append("init.cancelled")
                raise
        if method == "initialize":
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


#: A budget that only :meth:`_Clock.fire` spends. No real budget is this long.
HELD_CONNECT_S = 1001.0
HELD_STOP_S = 1002.0
#: A budget that a fence does not fire. A slow runner cannot reach it, so the
#: fence passes on the order alone. A regression costs this long, then fails.
LONG_S = 30.0


class _Clock:
    """``asyncio`` as ``_maf_mcp`` reads it, with one change.

    A timeout of a HELD number of seconds never fires by the clock. The fence
    fires it at an event with :meth:`fire`. Every other timeout is the real
    one. This is the lesson of #921: compare events, not a wall clock."""

    def __init__(self, *held: float) -> None:
        self.held = set(held)
        self.made: list[tuple[float, Any]] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(asyncio, name)

    def timeout(self, delay: float | None) -> Any:
        if delay not in self.held:
            return asyncio.timeout(delay)
        cm = asyncio.timeout(None)
        self.made.append((delay, cm))
        return cm

    def fire(self, delay: float) -> None:
        """Spend each entered timeout of *delay* seconds, at the next loop turn."""
        now = asyncio.get_running_loop().time()
        for held, cm in self.made:
            if held == delay and not cm.expired():
                with contextlib.suppress(RuntimeError):  # not entered, or done
                    cm.reschedule(now)

    def install(self, monkeypatch: pytest.MonkeyPatch) -> _Clock:
        monkeypatch.setattr(_maf_mcp, "asyncio", self)
        return self


def _trace_close(monkeypatch: pytest.MonkeyPatch, events: list[str]) -> None:
    """Record each step of the close of a failed server in *events*, in order.

    ``client.closed``: the pinned client closed. ``stop.called`` and
    ``owner.ended``: ``_stop_owner`` cancelled a MAF task that ran, and the
    task ended. ``stop.returned``: ``_stop_owner`` gave the run back.
    ``close.returned ok=<bool>``: ``_close_tool`` ended, and ``ok=False``
    says that ``tool.close()`` did not finish in its budget."""
    real_client = outbound_guard.mcp_client
    real_stop = _maf_mcp._stop_owner
    real_close_tool = _maf_mcp._close_tool

    async def _client(url: str) -> Any:
        client = await real_client(url)
        real_aclose = client.aclose

        async def _aclose() -> None:
            await real_aclose()
            events.append("client.closed")

        client.aclose = _aclose
        return client

    async def _stop(tool: Any) -> None:
        task = getattr(tool, "_lifecycle_owner_task", None)
        if task is not None and not task.done():
            events.append("stop.called")
            task.add_done_callback(lambda _t: events.append("owner.ended"))
        await real_stop(tool)
        events.append("stop.returned")

    async def _close_tool(tool: Any) -> bool:
        ok = await real_close_tool(tool)
        events.append(f"close.returned ok={ok}")
        return ok

    monkeypatch.setattr(outbound_guard, "mcp_client", _client)
    monkeypatch.setattr(_maf_mcp, "_stop_owner", _stop)
    monkeypatch.setattr(_maf_mcp, "_close_tool", _close_tool)


#: The close of a server whose connect ran past its budget, as #869 round 2
#: wrote it: the client closes FIRST, then the MAF task is cancelled and ends
#: (and the held ``initialize`` with it), and only then does the tool close.
#: The tool close does not reach its budget, because no connect holds it.
FAILURE_CLOSE = [
    "init.start", "client.closed", "stop.called", "init.cancelled",
    "owner.ended", "stop.returned", "close.returned ok=True",
]


def _long_close_budgets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_maf_mcp, "CLOSE_BUDGET_S", LONG_S)
    monkeypatch.setattr(_maf_mcp, "STOP_WAIT_S", LONG_S)


# ── slow_connect.py and leak.py ─────────────────────────────────────────────


async def test_a_slow_initialize_costs_the_budget_and_leaks_no_task(monkeypatch):
    """The connect budget fires when the server holds the ``initialize``.

    The fence reads no clock. A close that waited behind the connect would
    show ``close.returned ok=False``, after :data:`LONG_S`."""
    clock = _Clock(HELD_CONNECT_S).install(monkeypatch)
    monkeypatch.setattr(_maf_mcp, "CONNECT_BUDGET_S", HELD_CONNECT_S)
    _long_close_budgets(monkeypatch)
    events: list[str] = []
    srv = _Server(hang_init=True, events=events,
                  on_init=lambda: clock.fire(HELD_CONNECT_S)).install(monkeypatch)
    _trace_close(monkeypatch, events)
    agent, opened = native_agent(), []
    with structlog.testing.capture_logs() as logs:
        names = await _maf_mcp.attach_mcp_tools(agent, _server_spec(), opened)
    assert names == [] and opened == [] and agent.mcp_tools == []
    assert events == FAILURE_CLOSE, events
    assert [e for e in logs if e["event"] == "mcp.unreachable"]
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


async def test_a_cancel_during_the_attach_reaches_the_run(monkeypatch):
    """The Stop arrives while the server holds the ``initialize``. The close
    takes the same steps as a spent budget, and the cancel reaches the run."""
    monkeypatch.setattr(_maf_mcp, "CONNECT_BUDGET_S", LONG_S)
    _long_close_budgets(monkeypatch)
    events: list[str] = []
    held = asyncio.Event()
    _Server(hang_init=True, events=events, on_init=held.set).install(monkeypatch)
    _trace_close(monkeypatch, events)
    agent, opened = native_agent(), []
    task = asyncio.create_task(_maf_mcp.attach_mcp_tools(agent, _server_spec(), opened))
    await asyncio.wait_for(held.wait(), LONG_S)  # a guard against a hang only
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert events == FAILURE_CLOSE, f"the cancel waited on the half-open server: {events}"
    assert opened == [] and agent.mcp_tools == []
    assert _live_owner_tasks() == []


async def test_a_late_owner_fails_the_close_and_never_stops_the_run(monkeypatch):
    """The MAF task ends AFTER the wait of ``_stop_owner`` runs out.

    The close is then in the queue of that task, and the task fails it with
    its own ``CancelledError``. Before this fix, that error went up, and an
    unreachable server stopped the run as a Stop does. A loaded box can
    make the wait run out. Here the wait is spent at an event, so the order
    is the same on each run."""
    clock = _Clock(HELD_CONNECT_S, HELD_STOP_S).install(monkeypatch)
    monkeypatch.setattr(_maf_mcp, "CONNECT_BUDGET_S", HELD_CONNECT_S)
    monkeypatch.setattr(_maf_mcp, "STOP_WAIT_S", HELD_STOP_S)
    monkeypatch.setattr(_maf_mcp, "CLOSE_BUDGET_S", LONG_S)
    events: list[str] = []
    _Server(hang_init=True, events=events,
            on_init=lambda: clock.fire(HELD_CONNECT_S)).install(monkeypatch)
    _trace_close(monkeypatch, events)
    tool_cls = _maf_mcp._run_scoped_tool_class()
    real_cleanup = tool_cls._close_and_check_cancelled

    async def _late_cleanup(self: Any, ex: BaseException) -> Any:
        # The MAF task is in its cancel. Spend the stop wait, and go on only
        # when the close of the attach is in this task's queue.
        clock.fire(HELD_STOP_S)
        for _ in range(10_000):
            queue = self._lifecycle_queue
            if queue is not None and not queue.empty():
                break
            await asyncio.sleep(0)
        else:
            pytest.fail("the close never reached the queue of the MAF task")
        events.append("owner.cleanup")
        return await real_cleanup(self, ex)

    monkeypatch.setattr(tool_cls, "_close_and_check_cancelled", _late_cleanup)
    agent, opened = native_agent(), []
    with structlog.testing.capture_logs() as logs:
        names = await _maf_mcp.attach_mcp_tools(agent, _server_spec(), opened)
    assert names == [] and opened == [] and agent.mcp_tools == []
    # The MAF task fails the queued close in its ``finally``, and ends in the
    # same step. Its done callback (``owner.ended``) runs at the next loop
    # turn, after the attach returned, so the live-task check below proves it.
    assert events == [
        "init.start", "client.closed", "stop.called", "stop.returned",
        "owner.cleanup", "init.cancelled", "close.returned ok=False",
    ], events
    assert [e for e in logs if e["event"] == "mcp.unreachable"]
    assert [e for e in logs if e["event"] == "mcp.close_owner_stopped"]
    assert _live_owner_tasks() == []


async def test_close_tool_still_lets_a_stop_through_with_an_owner_that_runs() -> None:
    """The quiet path of a late owner never swallows a Stop of the CALLER."""

    class _Tool:
        name = "probe"

        def __init__(self) -> None:
            self._lifecycle_owner_task = asyncio.create_task(asyncio.Event().wait())

        async def close(self) -> None:
            await asyncio.Event().wait()

    tool = _Tool()
    closer = asyncio.create_task(_maf_mcp._close_tool(tool))
    await asyncio.sleep(0)
    tool._lifecycle_owner_task.cancel()
    await asyncio.sleep(0)
    closer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await closer


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
    """The close returns while the 40 s DELETE still runs. No clock is read:
    a close that waited for the DELETE would return after ``delete.end``."""
    monkeypatch.setattr(_maf_mcp, "CLOSE_BUDGET_S", 1.0)
    monkeypatch.setattr(_maf_mcp, "CLOSE_TOTAL_S", 3.0)
    monkeypatch.setattr(_maf_mcp, "STOP_WAIT_S", LONG_S)
    srv = _Server(slow_delete=40.0).install(monkeypatch)
    agent, opened = native_agent(), []
    assert await _maf_mcp.attach_mcp_tools(agent, _server_spec(), opened) == ["probe"]
    await _maf_mcp.close_mcp_tools(opened)
    assert srv.deletes == 1
    assert srv.events == ["delete.start"], f"the close waited for the DELETE: {srv.events}"
    assert opened == []
    assert _live_owner_tasks() == []


async def test_the_close_of_a_run_has_one_total_cap(monkeypatch):
    """A client close that hangs cannot hold the run either."""
    monkeypatch.setattr(_maf_mcp, "CLOSE_TOTAL_S", 1.0)

    class _Tool:
        async def close(self) -> None:
            return None

    steps: list[str] = []

    class _HangingClient:
        async def aclose(self) -> None:
            steps.append("aclose.start")
            await asyncio.sleep(LONG_S)
            steps.append("aclose.end")

    opened: list[Any] = [(_Tool(), _HangingClient()), (_Tool(), _HangingClient())]
    await _maf_mcp.close_mcp_tools(opened)
    # No clock is read: a close with no cap returns after an ``aclose.end``.
    assert "aclose.start" in steps and "aclose.end" not in steps, steps
    assert opened == []


@pytest.mark.usefixtures("_a_tenant")
def test_a_40s_delete_never_keeps_the_run_live_end_to_end(monkeypatch):
    """The REAL run: the run is marked inactive before the MCP close starts,
    and the whole end of the run stays under the close cap."""
    from orchestrator import stream_relay

    order: list[str] = []
    real_close = _maf_mcp.close_mcp_tools

    async def _mark_inactive(*_a: Any, **_k: Any) -> None:
        order.append("mark_inactive")

    async def _close(opened: Any) -> None:
        order.append("close")
        await real_close(opened)
        # No clock is read: a close that waited for the DELETE logs it here.
        order.extend(f"server:{e}" for e in srv.events)
        order.append("close.returned")

    monkeypatch.setattr(stream_relay, "mark_inactive", _mark_inactive)
    monkeypatch.setattr(_maf_mcp, "close_mcp_tools", _close)
    srv = _Server(slow_delete=40.0).install(monkeypatch)
    set_flag(monkeypatch, "*")
    RegistrySpy([row("probe-srv", org="org-native-maf-probe", url=URL)]).install(monkeypatch)
    model = ScriptedModel([text_turn("done")])
    events, built = drive_native(
        "apis-config", "apps/agents/agent-apis-config", monkeypatch, model,
        thread_id="thread-ws8c-slow-delete",
    )
    assert not [e for e in events if e.get("type") == "RUN_ERROR"], events
    assert [t.name for t in built[0].mcp_tools] == ["probe-srv"]
    assert order == ["mark_inactive", "close", "server:delete.start", "close.returned"], (
        f"the 40 s DELETE held the end of the run: {order}")
    assert srv.deletes == 1


# ── Review of #869, the two P2s: a second cancel ────────────────────────────


async def test_stop_owner_lets_an_external_cancel_through() -> None:
    """``_stop_owner`` waits a moment for the MAF task to end. A Stop that
    arrives in that wait must reach the run, not be swallowed."""

    async def _stubborn() -> None:
        for _ in range(40):  # ignores its cancel for a while
            try:
                await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                continue

    class _Tool:
        def __init__(self) -> None:
            self._lifecycle_owner_task = asyncio.create_task(_stubborn())

    tool = _Tool()
    waiter = asyncio.create_task(_maf_mcp._stop_owner(tool))
    await asyncio.sleep(0.1)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    tool._lifecycle_owner_task.cancel()
    with contextlib.suppress(BaseException):
        await tool._lifecycle_owner_task


async def test_stop_owner_is_quiet_when_only_its_own_cancel_ends_the_task() -> None:
    class _Tool:
        def __init__(self) -> None:
            self._lifecycle_owner_task = asyncio.create_task(asyncio.sleep(30))

    tool = _Tool()
    await _maf_mcp._stop_owner(tool)  # must not raise
    assert tool._lifecycle_owner_task.cancelled()


@pytest.mark.usefixtures("_a_tenant")
def test_a_second_cancel_in_the_finally_still_closes_the_mcp_tools(monkeypatch):
    """A cancel during an earlier await of the run's ``finally`` (here,
    ``mark_inactive``) must not skip the MCP close, or the sockets and the
    MAF task stay until the gateway restarts."""
    from orchestrator import stream_relay

    closed: list[int] = []
    real_close = _maf_mcp.close_mcp_tools

    async def _cancelled_mark(*_a: Any, **_k: Any) -> None:
        raise asyncio.CancelledError

    async def _close(opened: Any) -> None:
        closed.append(len(opened))
        await real_close(opened)

    monkeypatch.setattr(stream_relay, "mark_inactive", _cancelled_mark)
    monkeypatch.setattr(_maf_mcp, "close_mcp_tools", _close)
    _Server().install(monkeypatch)
    set_flag(monkeypatch, "*")
    RegistrySpy([row("probe-srv", org="org-native-maf-probe", url=URL)]).install(monkeypatch)
    with pytest.raises(asyncio.CancelledError):
        drive_native(
            "apis-config", "apps/agents/agent-apis-config", monkeypatch,
            ScriptedModel([text_turn("done")]), thread_id="thread-ws8c-second-cancel",
        )
    assert closed == [1], "the second cancel skipped the MCP close"
