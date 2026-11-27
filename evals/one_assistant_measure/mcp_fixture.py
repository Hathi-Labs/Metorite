"""A fake MCP server for the connected-systems chats (CSY-3, ``chat_connected_systems.md`` §8.2).

Only the SERVER EDGE is fake. The run reads the org's servers from the registry, applies
the grant, connects, builds the tools, writes the "Connected systems" block and gates
each call, all by the real code: ``_tool_injection._inject_mcp_servers``,
``_maf_mcp.attach_mcp_tools``, ``RunScopedMCPTool``, ``McpPermissionGate`` and
``server_brief``. These things stand in for the world:

* the registry read (``RegistrySpy``) answers one ``http-sse`` row for the eval org,
* the URL guard accepts the one local URL of the server (every other URL is checked),
* a real MCP server answers on ``127.0.0.1`` in a thread, with one read-only tool,
* the member's grant answers yes for this one server,
* the daily use counter of CSY-8 writes to memory, never to a Redis of the machine.

The server holds one fact, and only the server holds it. No chat reaches the network, no
real server is named and no key is read. Nothing in this file calls a model.
"""

from __future__ import annotations

import contextlib
import socket
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import httpx

#: The transport send of httpx as it is on import, BEFORE the harness puts its guard on it.
#: The only request that goes through it is the one to the fake server on 127.0.0.1.
_REAL_SEND = httpx.AsyncHTTPTransport.handle_async_request

#: The name of the server in the registry. The tool prefix is this name (``attach_mcp_tools``).
SERVER = "opsapp"
#: The one tool of the server, and the name that the model sees.
TOOL = "search_operations"
FULL = f"{SERVER}_{TOOL}"
#: The fact that only the server holds. No fixture of Metorite holds it.
FACT = "Circuit C-17 was last serviced on 2026-03-02 by Vendor Nova."
#: The answer of the server to a query that it does not know.
NOTHING = "No operation matches that query."


def serves(query: str) -> str:
    """What the server answers to *query*: the fact for a circuit, else nothing."""
    return FACT if "c-17" in query.lower() or "circuit" in query.lower() else NOTHING


@dataclass
class FakeServer:
    """The state of the running fake: the queries that the SERVER received."""

    url: str
    calls: list[str] = field(default_factory=list)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@contextlib.contextmanager
def running() -> Iterator[FakeServer]:
    """A streamable-HTTP MCP server on 127.0.0.1 with one read-only tool."""
    import uvicorn
    from mcp.server.fastmcp import FastMCP
    from mcp.types import ToolAnnotations

    calls: list[str] = []
    server = FastMCP(SERVER, stateless_http=True, json_response=True)

    @server.tool(name=TOOL, annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False))
    def search_operations(query: str) -> str:
        """Search the operations records of the plant: circuits, tickets and services."""
        calls.append(query)
        return serves(query)

    port = _free_port()
    config = uvicorn.Config(server.streamable_http_app(), host="127.0.0.1", port=port,
                            log_level="warning", lifespan="on")
    uv = uvicorn.Server(config)
    thread = threading.Thread(target=uv.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not uv.started and time.monotonic() < deadline:
        time.sleep(0.05)
    if not uv.started:
        raise RuntimeError("the fake MCP server did not start")
    try:
        yield FakeServer(url=f"http://127.0.0.1:{port}/mcp", calls=calls)
    finally:
        uv.should_exit = True
        thread.join(timeout=10)


class _MemoryCounter:
    """The three Redis writes of ``mcp_use``, in memory."""

    def __init__(self) -> None:
        self.values: dict[str, Any] = {}

    async def incr(self, key: str, amount: int = 1) -> int:
        self.values[key] = int(self.values.get(key, 0)) + amount
        return int(self.values[key])

    async def expire(self, key: str, seconds: int) -> bool:
        return True

    async def set(self, key: str, value: Any, **_kw: Any) -> bool:
        self.values[key] = value
        return True


def install(mp: Any, org: str, stack: contextlib.ExitStack) -> FakeServer:
    """Wire the fake server into the run of *org*, on the monkeypatch *mp*.

    The flags ``MAF_MCP_ORGS`` and ``CONNECTED_SYSTEMS_ORGS`` (CSY-1) are set in the eval
    process only. The fixture never writes them on a box. ``MCP_MAIL_PRECEDENCE_ORGS``
    (CSY-4) is held empty, so the ``.env`` of a developer does not change the mail chat.
    *stack* closes the server."""
    from acb_common import get_settings, mcp_use
    from acb_common.tenant_redis import TenantRedis
    from gateway import outbound_guard

    from tests.unit._maf_mcp_fakes import RegistrySpy, row

    srv = stack.enter_context(running())
    # ``raising=True``: a renamed flag fails the run, and is not set in silence.
    settings = get_settings()
    mp.setattr(settings, "maf_mcp_orgs", org)
    mp.setattr(settings, "connected_systems_orgs", org)
    mp.setattr(settings, "mcp_mail_precedence_orgs", "")
    counter = TenantRedis(_MemoryCounter())
    mp.setattr(mcp_use, "_client", lambda: counter)
    RegistrySpy([row(SERVER, org=org, url=srv.url)]).install(mp)
    real = outbound_guard.check_mcp_url

    async def _check(url: str) -> Any:
        if url == srv.url:
            return outbound_guard.Target(url=httpx.URL(url), host="127.0.0.1", ip="127.0.0.1")
        return await real(url)

    mp.setattr(outbound_guard, "check_mcp_url", _check)

    # The harness guard refuses every httpx send. The one URL of the fake server passes it,
    # to the real transport. Any other URL still reaches the guard, so a stray send fails.
    guard = httpx.AsyncHTTPTransport.handle_async_request

    async def _edge(transport: Any, request: httpx.Request) -> httpx.Response:
        if str(request.url).startswith(srv.url):
            return await _REAL_SEND(transport, request)
        return await guard(transport, request)

    mp.setattr(httpx.AsyncHTTPTransport, "handle_async_request", _edge)

    # The lookahead of the loader plan reads ``enabled_meta``, the same rows with no header.
    from gateway.connections import mcp as registry

    async def _meta(org_id: str) -> list[Any]:
        return await registry.servers_for_org(org_id)

    mp.setattr(registry, "enabled_meta", _meta)

    # The member holds ``integrations:use:mcp-opsapp``, and so a card can reach them. The
    # real authorizer reads the member's access from the database, and the eval has none,
    # so it fails closed and refuses the server ("mcp.not_authorized"). Only the grant is
    # faked. ``_inject_mcp_servers`` still applies it, server by server.
    from orchestrator import executor

    async def _authorizer(*_a: Any, **_k: Any) -> Any:
        return lambda service: service == f"mcp-{SERVER}"

    mp.setattr(executor, "_integration_authorizer", _authorizer)
    return srv
