"""Shared fakes for the WS-8c fences (MCP servers on a native MAF agent).

* :class:`RegistrySpy` replaces ``gateway.connections.mcp.servers_for_org``,
  the one registry read of ``_tool_injection._inject_mcp_servers`` (WS-54
  IN-4). It records each call and its org, and answers the servers of that
  org. So a test proves both "the run read nothing" and "the read named the
  org".
* :func:`native_agent` builds a real MAF ``Agent`` that never reaches a model.
* :class:`CopilotStub` has the one field the Copilot path writes.
* :func:`local_mcp_server` runs a real streamable-HTTP MCP server on
  ``127.0.0.1`` in a thread, for the end-to-end test.

This module holds no test, so pytest collects nothing from it.
"""
from __future__ import annotations

import contextlib
import json
import socket
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any


def row(name: str, *, org: str, transport: str = "http-sse",
        url: str = "https://mcp.example.com/mcp",
        headers: dict[str, str] | None = None,
        scope: list[str] | None = None,
        always_confirm: bool | None = None) -> dict[str, Any]:
    """One registered MCP server, with its org.

    *always_confirm* None stands for a row with no H-300 key, which reads as
    false."""
    out = {
        "org": org, "name": name, "transport": transport, "url": url,
        "headers": headers or {}, "agent_scope": scope or ["*"],
    }
    if always_confirm is not None:
        out["always_confirm"] = always_confirm
    return out


#: The one reader call that the spy records, with the org it was given.
READ = "servers_for_org"


@dataclass
class RegistrySpy:
    """A fake ``gateway.connections.mcp.servers_for_org`` over several orgs.

    WS-54 IN-4 moved the servers into the connection registry, and the
    orchestrator reads them through that one function. The spy answers the
    servers of the org that the call names, and records each call. The R8
    half (``tests/unit/test_mcp_registry_r8.py``) proves the real reader.
    """

    rows: list[dict[str, Any]] = field(default_factory=list)
    statements: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def install(self, monkeypatch: Any) -> RegistrySpy:
        from gateway.connections import mcp

        monkeypatch.setattr(mcp, READ, self._servers_for_org)
        return self

    async def _servers_for_org(self, org: str) -> list[Any]:
        from gateway.connections.mcp import StoredServer

        self.statements.append((READ, {"org": org}))
        return [
            StoredServer(
                name=r["name"], label=r["name"], description="",
                transport=r["transport"], url=r["url"],
                agent_scope=tuple(r["agent_scope"]), enabled=True,
                header_names=tuple(sorted(r["headers"])), headers=dict(r["headers"]),
                **({"always_confirm": r["always_confirm"]} if "always_confirm" in r else {}),
            )
            for r in self.rows if r["org"] == org
        ]

    @property
    def reads(self) -> int:
        return len(self.statements)


def refuse_registry(monkeypatch: Any, message: str) -> None:
    """Make every MCP registry call fail, for a path that must not reach it."""
    from gateway.connections import mcp

    def _refuse(*_a: Any, **_k: Any) -> Any:
        raise AssertionError(message)

    monkeypatch.setattr(mcp, "tenant_session", _refuse)


def native_agent(name: str = "probe") -> Any:
    """A real MAF ``Agent`` whose client points at a closed port."""
    from agent_framework import Agent
    from agent_framework.openai import OpenAIChatCompletionClient

    return Agent(
        client=OpenAIChatCompletionClient(
            model="m", api_key="k", base_url="http://127.0.0.1:9/v1",
        ),
        name=name,
    )


class CopilotStub:
    """The one field the Copilot path writes (H-181)."""

    def __init__(self) -> None:
        self._default_options: dict[str, Any] = {}

    @property
    def servers(self) -> dict[str, Any]:
        return dict(self._default_options.get("mcp_servers") or {})


def set_flag(monkeypatch: Any, value: str) -> None:
    """Set ``MAF_MCP_ORGS`` on the cached settings object."""
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "maf_mcp_orgs", value, raising=False)


def logged_text(events: list[dict[str, Any]], caplog: Any = None) -> str:
    """Every captured structlog event and stdlib record, as one string."""
    parts = [json.dumps(e, default=str) for e in events]
    if caplog is not None:
        parts += [r.getMessage() for r in caplog.records]
        parts += [str(r.__dict__) for r in caplog.records]
    return "\n".join(parts)


# ── A real MCP server, for the end-to-end test ──────────────────────────────


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@dataclass
class LocalServer:
    url: str
    calls: list[str]
    headers_seen: list[dict[str, str]]


#: The description of each filler tool of a large server. It has two lines,
#: and the first is longer than the 80 characters of an index line.
FILLER_DESCRIPTION = (
    "Search the bulk store {i} for the records that match a query, and return "
    "them with their fields.\n"
    "Returns at most limit records, and a cursor for the next page. Each record "
    "holds its id, its title, its owner, its stage and the time it changed last."
)


def _filler_tool(i: int, calls: list[str]) -> Any:
    def bulk(query: str, limit: int = 10, cursor: str = "",
             fields: list[str] | None = None, owner: str = "") -> str:
        calls.append(f"bulk_{i:02d}:{query}")
        return f"bulk_{i:02d}:{query}"

    return bulk


@contextlib.contextmanager
def local_mcp_server(filler: int = 0) -> Iterator[LocalServer]:
    """A streamable-HTTP MCP server on 127.0.0.1 with three tools.

    * ``echo`` says it is read-only, so it runs with no card (H-299).
    * ``delete_record`` says it is destructive, so it needs the card.
    * ``plain_note`` sends no hints, so it needs the card too.

    *filler* adds that many read-only tools ``bulk_00``, ``bulk_01`` and so
    on, each with a long description and five arguments. With 10 or more,
    the server has more than 12 tools, so it gets the index (WS-8c item 23).
    """
    import uvicorn
    from mcp.server.fastmcp import FastMCP
    from mcp.types import ToolAnnotations

    calls: list[str] = []
    headers_seen: list[dict[str, str]] = []
    server = FastMCP("probe", stateless_http=True, json_response=True)
    for i in range(filler):
        server.add_tool(
            _filler_tool(i, calls), name=f"bulk_{i:02d}",
            description=FILLER_DESCRIPTION.format(i=i),
            annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False),
        )

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False))
    def echo(text: str) -> str:
        """Echo the text back."""
        calls.append(text)
        return f"echo:{text}"

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True))
    def delete_record(record_id: str) -> str:
        """Delete one record for ever."""
        calls.append(f"delete:{record_id}")
        return f"deleted:{record_id}"

    @server.tool()
    def plain_note(text: str) -> str:
        """Write a note."""
        calls.append(f"note:{text}")
        return f"noted:{text}"

    inner = server.streamable_http_app()

    async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") == "http":
            headers_seen.append({
                k.decode("latin-1"): v.decode("latin-1") for k, v in scope.get("headers", [])
            })
        await inner(scope, receive, send)

    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning",
                            lifespan="on")
    uv = uvicorn.Server(config)
    thread = threading.Thread(target=uv.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not uv.started and time.monotonic() < deadline:
        time.sleep(0.05)
    if not uv.started:
        raise RuntimeError("the local MCP server did not start")
    try:
        yield LocalServer(url=f"http://127.0.0.1:{port}/mcp", calls=calls,
                          headers_seen=headers_seen)
    finally:
        uv.should_exit = True
        thread.join(timeout=10)


# ── No network: the pinned client is a recorder ─────────────────────────────


class FakeClient:
    """Stands for the pinned client. Records its close."""

    def __init__(self, url: str) -> None:
        self.url = url
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


def install_offline(monkeypatch: Any, *, secret: str = "") -> dict[str, Any]:
    """Replace the pinned client with :class:`FakeClient`, and make ``connect``
    succeed with no socket. ``state["fail"]`` names the servers whose connect
    raises an error that carries *secret*, so a log fence can look for it."""
    import httpx
    from agent_framework import MCPStreamableHTTPTool
    from gateway import outbound_guard

    state: dict[str, Any] = {"clients": [], "fail": set(), "closed": []}

    async def _client(url: str) -> FakeClient:
        c = FakeClient(url)
        state["clients"].append(c)
        return c

    async def _connect(self: Any, *, reset: bool = False) -> None:
        if self.name in state["fail"]:
            raise httpx.ConnectError(f"cannot reach {self.url} with {secret}")

    async def _close(self: Any) -> None:
        state["closed"].append(self.name)

    monkeypatch.setattr(outbound_guard, "mcp_client", _client)
    monkeypatch.setattr(MCPStreamableHTTPTool, "connect", _connect)
    monkeypatch.setattr(MCPStreamableHTTPTool, "close", _close)
    return state
