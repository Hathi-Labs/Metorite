"""WS-8c items 22 to 30 — the MCP tools of a run load on demand, under a budget.

Spec: ``project-docs/specs/agent_architecture.md`` §12.2 WS-8c, items 22 to 30
and acceptance A23 to A30.

Before this slice, each MCP server sent the full schema of every tool to the
model when the run started, up to 128 tools, and nothing capped the number of
servers. Now MAF's own progressive disclosure is on (no Metorite meta-tool):

* **A23** A server with more than 12 tools, or with schemas past 1,500
  tokens, shows the three MAF loaders only. When all servers together pass
  4,000 tokens, the largest server that still shows full schemas changes to
  the loaders, until the sum fits.
* **A24** The list loader returns an INDEX: the name, one line of 80
  characters or fewer, and ``read_only``. No ``parameters``.
* **A25** List and load show no card. A loaded tool keeps its card rule, and
  the H-300 switch ``always_confirm`` applies to it.
* **A26** A loader is egress (``_acb_mcp_loader``), so a ``no_egress`` run
  withholds it.
* **A27** A 129th tool leaves its server out, and so does a 9th server.
* **A28** ``ExperimentalWarning`` set to error fails nothing.
* **A29** The experimental MAF API is pinned, so an upgrade fails loudly.
* **A30** A hidden tool that clashes with a platform tool leaves its server
  out.

The end-to-end tests drive the REAL apis-config agent through the REAL
``run_agent_stream`` against a REAL FastMCP server of 13 tools on 127.0.0.1.
Only the model wire is scripted. The offline tests use the REAL pinned client
and the REAL ``MCPStreamableHTTPTool`` over an ``httpx.MockTransport``.

Mutation table (written before the code. Each mutation was made by hand, and
the named test failed):

| # | Mutation | Test that fails |
|---|---|---|
| M1 | the threshold is ignored: every server keeps its full schemas | ``test_a_server_past_the_threshold_shows_only_the_loaders``, ``test_a_run_of_eight_servers_fits_the_budget`` |
| M2 | the threshold is ignored: every server shows the loaders only | ``test_a_small_server_attaches_its_tools_directly`` |
| M3 | the full schema leaks into the index (``parameters`` kept) | ``test_the_list_is_an_index``, ``test_the_model_sees_the_index_loads_one_tool_and_calls_it_end_to_end`` |
| M4 | the loader marker is missing | ``test_a_loader_is_egress``, ``test_a_loader_that_no_tool_of_the_run_holds_is_refused`` |
| M5 | the loaders carry MAF's ``_mcp_is_tool`` in place of the marker, so the gate cards them | ``test_the_model_sees_the_index_loads_one_tool_and_calls_it_end_to_end``, ``test_a_loader_passes_the_gate_with_no_card_and_is_logged`` |
| M6 | the per-server budget is not enforced | ``test_a_small_server_with_large_schemas_shows_only_the_loaders`` |
| M7 | the total budget is not enforced | ``test_a_run_of_eight_servers_fits_the_budget`` |
| M8 | the server cap is not enforced | ``test_a_ninth_server_is_left_out_in_name_order_and_logged`` |
| M9 | a loaded tool skips its card | ``test_the_model_sees_the_index_loads_one_tool_and_calls_it_end_to_end``, ``test_a_loaded_tool_keeps_its_card_at_the_gate`` |
| M10 | ``always_confirm`` is ignored for a loaded tool | ``test_a_loaded_read_only_tool_of_an_always_confirm_server_waits_end_to_end`` |
| M11 | the clash check reads ``functions`` only | ``test_a_hidden_tool_that_clashes_leaves_its_server_out`` |
| M12 | the constructor runs with no warning filter | ``test_an_experimental_warning_set_to_error_does_not_fail_the_attach`` |
"""
from __future__ import annotations

import asyncio
import contextlib
import inspect
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
from acb_skills import ask_tools, egress
from acb_skills import whatsapp_ui as wui
from agent_framework._feature_stage import ExperimentalWarning
from gateway import outbound_guard
from orchestrator import _maf_mcp, executor

from tests.unit._maf_mcp_fakes import (
    RegistrySpy,
    local_mcp_server,
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

_AGENT = "apis-config"
#: The org that ``_a_tenant`` binds.
_ORG = "org-native-maf-probe"
_LOADERS = ("list_mcp_tools", "load_tool", "unload_tool")


@pytest.fixture(autouse=True)
def _clean():
    clear_tenant()
    executor._pending_user_input.clear()
    yield
    clear_tenant()
    executor._pending_user_input.clear()


# ── The price of a schema, measured the way the model pays it ───────────────


def _count(text: str) -> int:
    from acb_llm.context import count_message_tokens

    return count_message_tokens([{"role": "system", "content": text}])


def _seen_cost(tool: Any) -> int:
    """What the model pays for *tool* at run start: the schema of each
    function that MAF sends (``Agent._prepare_run`` reads ``functions``)."""
    return sum(_count(json.dumps(f.to_json_schema_spec())) for f in tool.functions)


def _full_cost(tool: Any) -> int:
    """What *tool* would cost with every schema shown."""
    return sum(_count(json.dumps(f.to_json_schema_spec())) for f in tool._functions)


# ── A fleet of MCP servers behind one MockTransport ─────────────────────────


def _tool(name: str, *, params: int = 5, desc: str = "", hints: dict[str, Any] | None = None,
          ) -> dict[str, Any]:
    props = {
        f"field_{j}": {"type": "string",
                       "description": f"The value of field {j} of the record, as plain text."}
        for j in range(params)
    }
    out: dict[str, Any] = {
        "name": name,
        "description": desc or ("Find the records of the store that match the fields, "
                                "and return each one with its id and its title."),
        "inputSchema": {"type": "object", "properties": props, "required": ["field_0"]},
    }
    if hints is not None:
        out["annotations"] = hints
    return out


def _many(n: int, prefix: str = "t", **kw: Any) -> list[dict[str, Any]]:
    return [_tool(f"{prefix}{i:03d}", **kw) for i in range(n)]


class _Fleet:
    """MCP servers by host. Each host answers ``initialize``, ``tools/list``
    and ``tools/call``, and records each request."""

    def __init__(self, servers: dict[str, list[dict[str, Any]]]) -> None:
        self.servers = servers
        self.requests: list[tuple[str, str]] = []
        self.calls: list[tuple[str, str]] = []

    async def handle(self, request: httpx.Request) -> httpx.Response:
        # The pinned client sends to the resolved address, with the name in
        # the Host header (and as SNI).
        host = request.headers.get("host", request.url.host).split(":")[0]
        sid = {"mcp-session-id": f"s-{host}"}
        if request.method == "DELETE":
            return httpx.Response(204)
        if request.method == "GET":
            return httpx.Response(405)
        body = json.loads(request.content or b"{}")
        rid, method = body.get("id"), body.get("method")
        self.requests.append((host, str(method)))
        if method == "initialize":
            return httpx.Response(200, headers=sid, json={"jsonrpc": "2.0", "id": rid, "result": {
                "protocolVersion": body["params"]["protocolVersion"],
                "capabilities": {"tools": {}}, "serverInfo": {"name": host, "version": "1"},
            }})
        if rid is None:
            return httpx.Response(202, headers=sid)
        if method == "tools/list":
            return httpx.Response(200, headers=sid, json={
                "jsonrpc": "2.0", "id": rid, "result": {"tools": self.servers[host]}})
        if method == "tools/call":
            self.calls.append((host, body["params"]["name"]))
            return httpx.Response(200, headers=sid, json={"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": "ran"}]}})
        return httpx.Response(200, headers=sid, json={"jsonrpc": "2.0", "id": rid, "result": {}})

    def install(self, monkeypatch: pytest.MonkeyPatch) -> _Fleet:
        async def _check(url: str) -> outbound_guard.Target:
            return outbound_guard.Target(url=httpx.URL(url), host=httpx.URL(url).host,
                                         ip="93.184.216.34")

        monkeypatch.setattr(outbound_guard, "check_url", _check)
        monkeypatch.setattr(outbound_guard, "_mcp_inner_transport",
                            lambda: httpx.MockTransport(self.handle))
        return self

    def hosts_reached(self) -> set[str]:
        return {h for h, _m in self.requests}


def _spec(name: str, *, always_confirm: bool = False) -> _maf_mcp.McpServer:
    return _maf_mcp.McpServer(name=name, url=f"https://{name}.example.com/mcp",
                              always_confirm=always_confirm)


async def _attach(monkeypatch: pytest.MonkeyPatch, servers: dict[str, list[dict[str, Any]]],
                  *, agent: Any = None, member: bool = True,
                  always_confirm: frozenset[str] = frozenset()) -> tuple[Any, list, _Fleet, list]:
    """Attach *servers* (name to tools) to a real native agent, offline."""
    fleet = _Fleet({f"{n}.example.com": tools for n, tools in servers.items()})
    fleet.install(monkeypatch)
    agent = agent if agent is not None else native_agent()
    opened: list[Any] = []
    with structlog.testing.capture_logs() as logs:
        await _maf_mcp.attach_mcp_tools(
            agent, [_spec(n, always_confirm=n in always_confirm) for n in servers], opened,
            member_present=member)
    return agent, opened, fleet, logs


def _by_name(agent: Any) -> dict[str, Any]:
    return {t.name: t for t in agent.mcp_tools}


def _loaders(tool: Any) -> list[Any]:
    return [f for f in tool.functions if f.name.endswith(_LOADERS)]


def _gate(agent: Any) -> Any:
    return next(m for m in agent.middleware if isinstance(m, _maf_mcp.McpPermissionGate))


class _Ran:
    def __init__(self) -> None:
        self.count = 0

    async def __call__(self) -> None:
        self.count += 1


@contextlib.contextmanager
def _card_channel():
    queue: asyncio.Queue[Any] = asyncio.Queue()
    token = executor._active_run_queue.set(queue)
    try:
        yield queue
    finally:
        executor._active_run_queue.reset(token)


def _cards(queue: asyncio.Queue[Any]) -> list[Any]:
    out = []
    while not queue.empty():
        event = queue.get_nowait()
        if event and event.get("name") == "confirmation_requested":
            out.append(event["value"])
    return out


def _ctx(fn: Any, arguments: dict[str, Any] | None = None) -> Any:
    return SimpleNamespace(function=fn, arguments=dict(arguments or {}), result=None, tools=[])


# The fleet of A23: 8 servers. ``big`` has 128 tools. The small ones fit
# 1,500 tokens each, and together they pass 4,000.
_FLEET = {
    "big": _many(128),
    "s2": _many(4, "a"), "s3": _many(5, "b"), "s4": _many(6, "c"), "s5": _many(7, "d"),
    "s6": _many(7, "e"), "s7": _many(3, "f"), "s8": _many(2, "g"),
}


# ── A23: the budget at run start ─────────────────────────────────────────────


async def test_a_run_of_eight_servers_fits_the_budget(monkeypatch):
    agent, opened, _fleet, logs = await _attach(monkeypatch, _FLEET)
    try:
        tools = _by_name(agent)
        assert sorted(tools) == sorted(_FLEET), "a server of the fixture was left out"
        small = [t for n, t in tools.items() if n != "big"]
        # The precondition: with every schema shown, the small ones pass 4,000.
        assert sum(_full_cost(t) for t in small) > _maf_mcp.RUN_SCHEMA_TOKENS
        assert all(_full_cost(t) <= _maf_mcp.SERVER_SCHEMA_TOKENS for t in small)
        costs = {n: _seen_cost(t) for n, t in tools.items()}
        assert all(c <= 1500 for c in costs.values()), costs
        assert sum(costs.values()) <= 4000, costs
        assert tools["big"].use_progressive_disclosure is True
        assert [f.name for f in tools["big"].functions] == [f"big_{n}" for n in _LOADERS]
        assert any(not t.use_progressive_disclosure for t in small), (
            "the budget turned every server to the index, not only the largest")
        budget = [e for e in logs if e["event"] == "mcp.schema_budget"]
        assert budget and budget[-1]["total"] == sum(costs.values())
    finally:
        await _maf_mcp.close_mcp_tools(opened)


async def test_the_largest_server_that_shows_full_schemas_changes_first(monkeypatch):
    agent, opened, _fleet, _logs = await _attach(monkeypatch, _FLEET)
    try:
        small = [t for n, t in _by_name(agent).items() if n != "big"]
        index = [_full_cost(t) for t in small if t.use_progressive_disclosure]
        direct = [_full_cost(t) for t in small if not t.use_progressive_disclosure]
        assert index and direct
        assert min(index) >= max(direct), (index, direct)
    finally:
        await _maf_mcp.close_mcp_tools(opened)


async def test_a_small_server_attaches_its_tools_directly(monkeypatch):
    agent, opened, _fleet, _logs = await _attach(monkeypatch, {"few": _many(12, params=1)})
    try:
        tool = _by_name(agent)["few"]
        assert tool.use_progressive_disclosure is False
        assert sorted(f.name for f in tool.functions) == [f"few_t{i:03d}" for i in range(12)]
        assert _loaders(tool) == []
    finally:
        await _maf_mcp.close_mcp_tools(opened)


async def test_a_server_past_the_threshold_shows_only_the_loaders(monkeypatch):
    agent, opened, _fleet, _logs = await _attach(monkeypatch, {"more": _many(13, params=1)})
    try:
        tool = _by_name(agent)["more"]
        assert tool.use_progressive_disclosure is True
        assert [f.name for f in tool.functions] == [f"more_{n}" for n in _LOADERS]
        assert len(tool._functions) == 13, "the hidden tools must stay loadable"
    finally:
        await _maf_mcp.close_mcp_tools(opened)


async def test_a_small_server_with_large_schemas_shows_only_the_loaders(monkeypatch):
    agent, opened, _fleet, _logs = await _attach(monkeypatch, {"wide": _many(12)})
    try:
        tool = _by_name(agent)["wide"]
        # Past the per-server budget, and inside the run budget, so only the
        # per-server rule can turn the loaders on.
        assert 1500 < _full_cost(tool) <= _maf_mcp.RUN_SCHEMA_TOKENS
        assert tool.use_progressive_disclosure is True
        assert _seen_cost(tool) <= 1500
    finally:
        await _maf_mcp.close_mcp_tools(opened)


def test_tool_json_schema_prices_a_maf_function_by_its_own_schema() -> None:
    """The one pricing seam (``skill_families.tool_json_schema``) prices a MAF
    ``FunctionTool`` by the schema that MAF sends, not by the docstring of
    the ``FunctionTool`` class."""
    from acb_skills import skill_families as sf
    from agent_framework import FunctionTool

    fn = FunctionTool(name="srv_find", func=None, description="Find one record.",
                      input_model={"type": "object", "properties": {"q": {"type": "string"}}})
    assert sf.tool_json_schema(fn) == fn.to_json_schema_spec()

    def plain(q: str) -> str:
        """A plain callable."""
        return q

    assert sf.tool_json_schema(plain)["function"]["description"] == "A plain callable."


# ── A24: the list is an index ────────────────────────────────────────────────


async def test_the_list_is_an_index(monkeypatch):
    tools = [
        _tool("reader", hints={"readOnlyHint": True, "destructiveHint": False}),
        _tool("wiper", hints={"readOnlyHint": False, "destructiveHint": True}),
        _tool("quiet"),
        *_many(12, params=1),
    ]
    agent, opened, _fleet, _logs = await _attach(monkeypatch, {"idx": tools})
    try:
        tool = _by_name(agent)["idx"]
        listed = tool._list_progressive_mcp_tools(SimpleNamespace(tools=[]))
        assert len(listed) == 15
        for entry in listed:
            assert set(entry) == {"name", "description", "read_only"}, entry
            assert len(entry["description"]) <= 80 and "\n" not in entry["description"]
        by = {e["name"]: e for e in listed}
        assert by["idx_reader"]["read_only"] is True
        assert by["idx_wiper"]["read_only"] is False
        assert by["idx_quiet"]["read_only"] is False, "no hints is not read-only"
        assert "parameters" not in json.dumps(listed)
        assert _count(json.dumps(listed)) < _full_cost(tool) / 3
    finally:
        await _maf_mcp.close_mcp_tools(opened)


async def test_the_index_line_escapes_control_characters_and_fits_80(monkeypatch):
    long_first = "Move \x1b[2Kthe record‮ to the next stage, " + "and say why " * 10
    tools = [_tool("mover", desc=long_first + "\nSecond line that must not show."),
             *_many(12, params=1)]
    agent, opened, _fleet, _logs = await _attach(monkeypatch, {"esc": tools})
    try:
        listed = _by_name(agent)["esc"]._list_progressive_mcp_tools(SimpleNamespace(tools=[]))
        line = next(e for e in listed if e["name"] == "esc_mover")["description"]
        assert len(line) <= 80
        assert "\x1b" not in line and "‮" not in line
        assert line.startswith("Move \\u001b[2Kthe record\\u202e to the next")
        assert "Second line" not in line
    finally:
        await _maf_mcp.close_mcp_tools(opened)


async def test_read_only_in_the_index_is_false_on_an_always_confirm_server(monkeypatch):
    tools = [_tool("reader", hints={"readOnlyHint": True}), *_many(12, params=1)]
    agent, opened, _fleet, _logs = await _attach(monkeypatch, {"ac": tools},
                                                 always_confirm=frozenset({"ac"}))
    try:
        listed = _by_name(agent)["ac"]._list_progressive_mcp_tools(SimpleNamespace(tools=[]))
        assert next(e for e in listed if e["name"] == "ac_reader")["read_only"] is False
    finally:
        await _maf_mcp.close_mcp_tools(opened)


# ── A25: list and load show no card, and a loaded tool keeps its card ────────


async def test_a_loader_passes_the_gate_with_no_card_and_is_logged(monkeypatch):
    agent, opened, _fleet, _logs = await _attach(monkeypatch, {"gl": _many(13, params=1)})
    try:
        tool = _by_name(agent)["gl"]
        gate = _gate(agent)
        with _card_channel() as queue, structlog.testing.capture_logs() as logs:
            for fn, args in zip(_loaders(tool), ({}, {"tool": "gl_t000"}, {"tool": "gl_t000"}),
                                strict=True):
                ran = _Ran()
                await asyncio.wait_for(gate.process(_ctx(fn, args), ran), timeout=5)
                assert ran.count == 1, f"{fn.name} did not run"
        assert _cards(queue) == [], "a loader showed a card"
        actions = [(e["server"], e["action"]) for e in logs if e["event"] == "mcp.loader"]
        assert actions == [("gl", "list"), ("gl", "load"), ("gl", "unload")]
    finally:
        await _maf_mcp.close_mcp_tools(opened)


async def test_a_loader_that_no_tool_of_the_run_holds_is_refused(monkeypatch):
    agent, opened, _fleet, _logs = await _attach(monkeypatch, {"own": _many(13, params=1)})
    other_agent, other_opened, _f2, _l2 = await _attach(monkeypatch, {"own": _many(13, params=1)})
    try:
        stranger = _loaders(_by_name(other_agent)["own"])[1]  # same name, not this run's
        ran = _Ran()
        ctx = _ctx(stranger, {"tool": "own_t000"})
        with _card_channel() as queue:
            await asyncio.wait_for(_gate(agent).process(ctx, ran), timeout=5)
        assert ran.count == 0, "a loader of another run passed the gate"
        assert _cards(queue) == []
        assert "not run" in str(ctx.result)
    finally:
        await _maf_mcp.close_mcp_tools(opened)
        await _maf_mcp.close_mcp_tools(other_opened)


async def test_a_loaded_tool_keeps_its_card_at_the_gate(monkeypatch):
    tools = [_tool("wiper", hints={"destructiveHint": True}),
             _tool("reader", hints={"readOnlyHint": True}), *_many(12, params=1)]
    agent, opened, _fleet, _logs = await _attach(monkeypatch, {"lt": tools})
    try:
        tool, gate = _by_name(agent)["lt"], _gate(agent)
        hidden = {f.name: f for f in tool._functions}
        ran = _Ran()
        with _card_channel() as queue:
            task = asyncio.create_task(gate.process(_ctx(hidden["lt_wiper"], {"field_0": "x"}),
                                                    ran))
            event = await asyncio.wait_for(queue.get(), timeout=5)
            assert event["name"] == "confirmation_requested"
            assert ran.count == 0, "the loaded tool ran before its card"
            executor._pending_user_input[event["value"]["request_id"]].set_result(
                {"answer": "REJECT"})
            await asyncio.wait_for(task, timeout=5)
        assert ran.count == 0
        ran = _Ran()
        with _card_channel() as queue:
            await asyncio.wait_for(gate.process(_ctx(hidden["lt_reader"], {"field_0": "x"}),
                                                ran), timeout=5)
        assert ran.count == 1 and _cards(queue) == [], "a read-only loaded tool waited"
    finally:
        await _maf_mcp.close_mcp_tools(opened)


@pytest.mark.parametrize("writes", [False, True], ids=["reads-only", "writes-on"])
async def test_on_whatsapp_a_loader_runs_and_a_loaded_tool_that_needs_a_card_is_refused(
        monkeypatch, writes):
    tools = [_tool("wiper", hints={"destructiveHint": True}),
             _tool("reader", hints={"readOnlyHint": True}), *_many(12, params=1)]
    agent, opened, _fleet, _logs = await _attach(monkeypatch, {"wa": tools})
    try:
        tool, gate = _by_name(agent)["wa"], _gate(agent)
        hidden = {f.name: f for f in tool._functions}
        results: dict[str, Any] = {}
        with _card_channel() as queue, ask_tools.refuse_cards(), \
                wui.whatsapp_run("apis-config", writes=writes) as run:
            for label, fn in (("list", _loaders(tool)[0]), ("wiper", hidden["wa_wiper"]),
                              ("reader", hidden["wa_reader"])):
                ran = _Ran()
                ctx = _ctx(fn, {"field_0": "x"} if label != "list" else {})
                await asyncio.wait_for(gate.process(ctx, ran), timeout=5)
                results[label] = (ran.count, ctx.result)
        assert results["list"][0] == 1
        assert results["wiper"] == (0, _maf_mcp.NO_CARD_CHANNEL_TEXT.format(tool="wa_wiper"))
        assert results["reader"][0] == 1
        assert _cards(queue) == [] and run.parked is None
    finally:
        await _maf_mcp.close_mcp_tools(opened)


# ── A25 end to end: the real agent, the real executor, a real server ─────────


def _stub_pin(monkeypatch: pytest.MonkeyPatch, allowed_url: str) -> None:
    real = outbound_guard.check_mcp_url

    async def _check(url: str) -> outbound_guard.Target:
        if url == allowed_url:
            return outbound_guard.Target(url=httpx.URL(url), host="127.0.0.1", ip="127.0.0.1")
        return await real(url)

    monkeypatch.setattr(outbound_guard, "check_mcp_url", _check)


def _a_member(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    async def _authorizer(*_a: Any, **_k: Any) -> Any:
        return lambda _service: True

    monkeypatch.setattr(executor, "_integration_authorizer", _authorizer)
    return {"user_email": "member@budget.test"}


def _tool_results(model: ScriptedModel) -> list[str]:
    return [str(m.get("content")) for m in model.bodies[-1].get("messages") or []
            if m.get("role") == "tool"]


def _offered(body: dict[str, Any], prefix: str = "probe-srv_") -> list[str]:
    return sorted(t["function"]["name"] for t in body.get("tools") or []
                  if t.get("function", {}).get("name", "").startswith(prefix))


def _drive(monkeypatch, srv, model, *, payload=None, answer: str = "REJECT",
           always_confirm: bool | None = None):
    set_flag(monkeypatch, _ORG)
    RegistrySpy([row("probe-srv", org=_ORG, url=srv.url,
                     always_confirm=always_confirm)]).install(monkeypatch)
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
            thread_id="thread-mcp-budget", extra_payload=payload, on_event=_on_event)
    return events, seen, logs


#: What the model sees of the probe server at run start: the index only.
_INDEX_ONLY = sorted(f"probe-srv_{n}" for n in _LOADERS)


def _load_and_call(tool: str, arguments: str) -> ScriptedModel:
    return ScriptedModel([
        tool_turn("probe-srv_list_mcp_tools", "{}", call_id="call_1"),
        tool_turn("probe-srv_load_tool", json.dumps({"tool": f"probe-srv_{tool}"}),
                  call_id="call_2"),
        tool_turn(f"probe-srv_{tool}", arguments, call_id="call_3"),
        text_turn("done"),
    ])


@pytest.mark.usefixtures("_a_tenant", "warn_again")
@pytest.mark.filterwarnings("error::agent_framework._feature_stage.ExperimentalWarning")
def test_the_model_sees_the_index_loads_one_tool_and_calls_it_end_to_end(monkeypatch):
    with local_mcp_server(filler=10) as srv:
        model = _load_and_call("delete_record", '{"record_id": "r-9"}')
        events, seen, logs = _drive(monkeypatch, srv, model, payload=_a_member(monkeypatch),
                                    answer="APPROVE")
    assert not [e for e in events if e.get("type") == "RUN_ERROR"], events
    assert _offered(model.bodies[0]) == _INDEX_ONLY, "the model saw more than the index"
    listed = next(m["content"] for m in model.bodies[1]["messages"] if m.get("role") == "tool")
    assert "probe-srv_delete_record" in listed and "parameters" not in listed
    assert "probe-srv_delete_record" in _offered(model.bodies[2]), "the load added nothing"
    assert len(seen["cards"]) == 1, "list, load or the loaded tool showed the wrong cards"
    assert "delete_record" in seen["cards"][0]["detail"]
    assert seen["calls_at_card"] == [], "the server ran the tool before the card"
    assert srv.calls == ["delete:r-9"]
    assert any("deleted:r-9" in r for r in _tool_results(model))
    assert [e["action"] for e in logs if e["event"] == "mcp.loader"] == ["list", "load"]


@pytest.mark.usefixtures("_a_tenant")
def test_a_loaded_read_only_tool_runs_with_no_card_end_to_end(monkeypatch):
    with local_mcp_server(filler=10) as srv:
        model = _load_and_call("echo", '{"text": "ping"}')
        events, seen, _logs = _drive(monkeypatch, srv, model, payload=_a_member(monkeypatch))
    assert not [e for e in events if e.get("type") == "RUN_ERROR"], events
    assert _offered(model.bodies[0]) == _INDEX_ONLY
    assert seen["cards"] == []
    assert srv.calls == ["ping"]


@pytest.mark.usefixtures("_a_tenant")
def test_a_loaded_read_only_tool_of_an_always_confirm_server_waits_end_to_end(monkeypatch):
    with local_mcp_server(filler=10) as srv:
        model = _load_and_call("echo", '{"text": "ping"}')
        _events, seen, _logs = _drive(monkeypatch, srv, model, payload=_a_member(monkeypatch),
                                      answer="APPROVE", always_confirm=True)
    assert _offered(model.bodies[0]) == _INDEX_ONLY
    assert len(seen["cards"]) == 1, "always_confirm did not reach the loaded tool"
    assert seen["calls_at_card"] == []
    assert seen["cards"][0]["detail"].startswith("This server is set to always confirm.")
    assert srv.calls == ["ping"]


@pytest.mark.usefixtures("_a_tenant")
def test_a_run_with_no_member_loads_but_refuses_end_to_end(monkeypatch):
    with local_mcp_server(filler=10) as srv:
        model = _load_and_call("delete_record", '{"record_id": "r-9"}')
        events, seen, _logs = _drive(monkeypatch, srv, model, answer="APPROVE")
    assert not [e for e in events if e.get("type") == "RUN_ERROR"], events
    assert _offered(model.bodies[0]) == _INDEX_ONLY
    assert seen["cards"] == []
    assert srv.calls == []
    assert any("no member can confirm" in r for r in _tool_results(model))


@pytest.mark.usefixtures("_a_tenant")
def test_a_server_of_three_tools_still_attaches_them_directly_end_to_end(monkeypatch):
    with local_mcp_server() as srv:
        model = ScriptedModel([tool_turn("probe-srv_echo", '{"text": "ping"}'), text_turn("ok")])
        _events, seen, _logs = _drive(monkeypatch, srv, model, payload=_a_member(monkeypatch))
    assert _offered(model.bodies[0]) == [
        "probe-srv_delete_record", "probe-srv_echo", "probe-srv_plain_note"]
    assert seen["cards"] == [] and srv.calls == ["ping"]


# ── A26: a loader is egress ──────────────────────────────────────────────────


async def test_a_loader_is_egress(monkeypatch):
    from agent_framework import FunctionTool

    agent, opened, _fleet, _logs = await _attach(monkeypatch, {"eg": _many(13, params=1)})
    try:
        loaders = _loaders(_by_name(agent)["eg"])
        assert len(loaders) == 3
        for fn in loaders:
            assert fn.additional_properties.get(egress.MCP_LOADER_KEY) is True
            assert not fn.additional_properties.get("_mcp_is_tool"), (
                "a loader must not carry MAF's key, or MAF's MCP loops change")
            assert egress._from_mcp(fn), f"{fn.name} is not seen as MCP"
            assert egress.is_egress_tool(fn)
        # A loader that joins a no_egress run late is withheld from the model
        # and refused at call time. A platform tool that says open_world=False
        # stays, so the withhold is not "drop everything".
        def keep(q: str) -> str:
            """Kept."""
            return q

        keep.__tool_risk__ = {"open_world": False}
        safe = FunctionTool(name="keep", func=keep)
        ctx = SimpleNamespace(options={"tools": [*loaders, safe]})

        async def nxt() -> None:
            return None

        await egress.WithholdEgressTools().process(ctx, nxt)
        assert [t.name for t in ctx.options["tools"]] == ["keep"]
        call = SimpleNamespace(function=loaders[1], result=None)
        await egress.RefuseEgressTools().process(call, nxt)
        assert "off in this run" in str(call.result)
    finally:
        await _maf_mcp.close_mcp_tools(opened)


# ── A27: the caps ────────────────────────────────────────────────────────────


async def test_a_server_with_a_129th_tool_is_left_out_and_logged(monkeypatch):
    agent, opened, _fleet, logs = await _attach(
        monkeypatch, {"huge": _many(129, params=1), "ok": _many(2, params=1)})
    try:
        assert [t.name for t in agent.mcp_tools] == ["ok"]
        down = [e for e in logs if e["event"] == "mcp.unreachable" and e["name"] == "huge"]
        assert down and down[0]["reason"] == "McpListTooLong"
    finally:
        await _maf_mcp.close_mcp_tools(opened)


async def test_a_ninth_server_is_left_out_in_name_order_and_logged(monkeypatch):
    names = ["s9", "s3", "s1", "s7", "s5", "s2", "s8", "s4", "s6"]
    agent, opened, fleet, logs = await _attach(
        monkeypatch, {n: _many(1, params=1) for n in names})
    try:
        assert _maf_mcp.MAX_SERVERS == 8
        assert [t.name for t in agent.mcp_tools] == [f"s{i}" for i in range(1, 9)]
        assert "s9.example.com" not in fleet.hosts_reached(), "the 9th server was reached"
        cap = [e for e in logs if e["event"] == "mcp.server_cap"]
        assert cap and cap[0]["left_out"] == ["s9"]
    finally:
        await _maf_mcp.close_mcp_tools(opened)


# ── A28: the experimental warning ────────────────────────────────────────────


@pytest.fixture
def warn_again(monkeypatch):
    """MAF warns once for each feature in a process (``_WARNED_FEATURES``).
    An earlier test spends that one warning, so each A28 test resets it."""
    from agent_framework import _feature_stage

    monkeypatch.setattr(_feature_stage, "_WARNED_FEATURES", set())


@pytest.mark.usefixtures("warn_again")
async def test_an_experimental_warning_set_to_error_does_not_fail_the_attach(monkeypatch):
    with warnings.catch_warnings():
        warnings.simplefilter("error", ExperimentalWarning)
        agent, opened, _fleet, logs = await _attach(monkeypatch, {"xw": _many(13, params=1)})
    try:
        assert [t.name for t in agent.mcp_tools] == ["xw"], logs
        assert agent.mcp_tools[0].use_progressive_disclosure is True
    finally:
        await _maf_mcp.close_mcp_tools(opened)


async def test_the_attach_filters_no_warning_for_the_process(monkeypatch):
    before = list(warnings.filters)
    _agent, opened, _fleet, _logs = await _attach(monkeypatch, {"pf": _many(13, params=1)})
    try:
        assert list(warnings.filters) == before, "the attach changed the process filters"
    finally:
        await _maf_mcp.close_mcp_tools(opened)


# ── A29: the experimental MAF API is pinned ──────────────────────────────────


def test_the_experimental_maf_api_is_pinned() -> None:
    """Items 22 to 27 rest on private MAF names. An upgrade that changes one
    of them must fail here, not quietly send every schema again."""
    from agent_framework import MCPStreamableHTTPTool
    from agent_framework import _mcp as maf_mcp

    params = inspect.signature(MCPStreamableHTTPTool.__init__).parameters
    assert params["use_progressive_disclosure"].default is False
    assert "always_load" in params
    # ``functions`` reads the attribute each time, so a change after connect holds.
    source = inspect.getsource(maf_mcp.MCPTool.functions.fget)
    assert "self.use_progressive_disclosure" in source
    assert (maf_mcp._MCP_PROGRESSIVE_LIST_TOOL_NAME, maf_mcp._MCP_PROGRESSIVE_LOAD_TOOL_NAME,
            maf_mcp._MCP_PROGRESSIVE_UNLOAD_TOOL_NAME) == _LOADERS
    sig = inspect.signature(maf_mcp.MCPTool._list_progressive_mcp_tools)
    assert list(sig.parameters) == ["self", "ctx"]

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ExperimentalWarning)
        tool = MCPStreamableHTTPTool(name="p", url="https://p.example.com/mcp",
                                     tool_name_prefix="p", use_progressive_disclosure=True)
    assert tool._functions == []
    assert tool._progressive_loader_functions is None
    built = tool.functions
    assert [f.name for f in built] == [f"p_{n}" for n in _LOADERS]
    assert tool._progressive_loader_functions == built
    # MAF builds the loaders with no ``_mcp_is_tool`` key (item 26).
    assert all(not (f.additional_properties or {}).get("_mcp_is_tool") for f in built)
    # Our tool class overrides the list, and its loader calls the override.
    scoped = _maf_mcp._run_scoped_tool_class()
    assert scoped._list_progressive_mcp_tools is not maf_mcp.MCPTool._list_progressive_mcp_tools


# ── A30: the clash check reads every tool ────────────────────────────────────


async def test_a_hidden_tool_that_clashes_leaves_its_server_out(monkeypatch):
    from agent_framework import Agent, FunctionTool
    from agent_framework.openai import OpenAIChatCompletionClient

    def clash_t005(field_0: str) -> str:
        """A platform tool."""
        return field_0

    agent = Agent(
        client=OpenAIChatCompletionClient(model="m", api_key="k",
                                          base_url="http://127.0.0.1:9/v1"),
        name="probe", tools=[FunctionTool(name="clash_t005", func=clash_t005)],
    )
    agent, opened, _fleet, logs = await _attach(
        monkeypatch, {"clash": _many(13, params=1), "fine": _many(13, "u", params=1)},
        agent=agent)
    try:
        assert [t.name for t in agent.mcp_tools] == ["fine"]
        hit = [e for e in logs if e["event"] == "mcp.name_collision"]
        assert hit and hit[0]["name"] == "clash" and "clash_t005" in hit[0]["tools"]
    finally:
        await _maf_mcp.close_mcp_tools(opened)
