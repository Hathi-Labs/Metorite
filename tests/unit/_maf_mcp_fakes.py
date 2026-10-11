"""Shared fakes for the WS-8c fences (MCP servers on a native MAF agent).

* :class:`RegistrySpy` replaces ``acb_graph.get_session`` for the registry
  read of ``_tool_injection._inject_mcp_servers``. It records each statement
  and its binds, and answers the rows of the org that the binds name. So a
  test proves both "the run read no row" and "the filter named the org".
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

#: The columns of ``_tool_injection._MCP_ROWS_SQL``, in order.
COLUMNS = ("name", "transport", "url", "headers", "agent_scope")


def row(name: str, *, org: str, transport: str = "http-sse",
        url: str = "https://mcp.example.com/mcp",
        headers: dict[str, str] | None = None,
        scope: list[str] | None = None) -> dict[str, Any]:
    """One ``mcp_servers`` row, with its org."""
    return {
        "org": org, "name": name, "transport": transport, "url": url,
        "headers": headers or {}, "agent_scope": scope or ["*"],
    }


@dataclass
class RegistrySpy:
    """A fake ``get_session`` over a list of rows of several orgs."""

    rows: list[dict[str, Any]] = field(default_factory=list)
    statements: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def install(self, monkeypatch: Any) -> RegistrySpy:
        import acb_graph

        monkeypatch.setattr(acb_graph, "get_session", self._session)
        return self

    @contextlib.contextmanager
    def _session(self) -> Iterator[Any]:
        spy = self

        class _Result:
            def __init__(self, rows: list[tuple[Any, ...]]) -> None:
                self._rows = rows

            def fetchall(self) -> list[tuple[Any, ...]]:
                return self._rows

        class _Session:
            def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _Result:
                sql = str(stmt)
                binds = dict(params or {})
                if "mcp_servers" not in sql:
                    # Another read of the run (skill settings, for example).
                    return _Result([])
                spy.statements.append((sql, binds))
                org = binds.get("org")
                if "organization_id = :org" not in sql:
                    # An unfiltered read answers every org: that is the leak.
                    picked = spy.rows
                else:
                    picked = [r for r in spy.rows if r["org"] == org]
                return _Result([tuple(r[c] for c in COLUMNS) for r in picked])

        yield _Session()

    @property
    def reads(self) -> int:
        return len(self.statements)


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


@contextlib.contextmanager
def local_mcp_server() -> Iterator[LocalServer]:
    """A streamable-HTTP MCP server with one tool, ``echo``, on 127.0.0.1."""
    import uvicorn
    from mcp.server.fastmcp import FastMCP

    calls: list[str] = []
    headers_seen: list[dict[str, str]] = []
    server = FastMCP("probe", stateless_http=True, json_response=True)

    @server.tool()
    def echo(text: str) -> str:
        """Echo the text back."""
        calls.append(text)
        return f"echo:{text}"

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
