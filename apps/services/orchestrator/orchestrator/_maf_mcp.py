"""MCP servers on a native MAF agent (WS-8c).

Spec: ``project-docs/specs/agent_architecture.md`` §12.2 WS-8c.

Before WS-8c, a registry MCP server reached only the Copilot agent class,
which reads ``_default_options["mcp_servers"]``. A native MAF ``Agent`` has no
such field, so ``merge_mcp_servers`` did nothing for it. This module gives a
native agent one ``MCPStreamableHTTPTool`` for each server row that
``_tool_injection._inject_mcp_servers`` hands it.

The rules:

1. **The flag.** ``MAF_MCP_ORGS`` allows an org (:func:`maf_mcp_org_allowed`).
   Empty, the default, allows none. ``*`` allows each org. The org is the run
   binding, never request input.
2. **The pinned client.** Each tool gets ``gateway.outbound_guard.mcp_client``:
   ``https`` on port 443 only, one DNS answer for the whole run, no redirect,
   no proxy from the environment. MAF's own default client follows redirects,
   so a tool never runs without this client.
3. **A server that fails is left out.** A refused URL, a refused redirect, a
   timeout or any connect fault leaves that one server out, and the run goes
   on. The log names the host and a short reason, never a header or a path.
4. **The run closes what it opened.** Each tool and its client go into the
   ``opened`` list of the run. The executor calls :func:`close_mcp_tools` in
   the ``finally`` of the run.
5. **The tool gate.** An MCP function goes through the same
   ``permission_policy.decide()`` gate as an injected platform tool
   (:class:`McpPermissionGate`). The tools keep the ``approval_mode`` of MAF.
6. **No stdio.** Nothing here builds an ``MCPStdioTool`` (D85). Fence:
   ``tests/unit/test_no_mcp_stdio.py``.

Fences: ``tests/unit/test_maf_mcp_injection.py`` and
``tests/unit/test_maf_mcp_org_scope_r8.py``.
"""
from __future__ import annotations

import asyncio
import contextlib
import functools
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from acb_common import get_logger, get_settings

_log = get_logger("orchestrator.maf_mcp")

#: The value in ``MAF_MCP_ORGS`` that allows each organization.
ALL_ORGS = "*"
#: Seconds to connect to ONE server and list its tools. A slow server costs
#: the run this much, and then the run goes on without it.
CONNECT_BUDGET_S = 10.0
#: Seconds for one MCP request after the connect.
REQUEST_TIMEOUT_S = 30
#: Seconds to close ONE tool. A close that runs past it is abandoned, and its
#: client is closed (review round 2 of PR #869).
CLOSE_BUDGET_S = 3.0
#: Seconds to close EVERY tool of one run. The servers close at the same time.
CLOSE_TOTAL_S = 5.0
#: The most ``tools/list`` pages that one server may send in one load.
MAX_TOOL_PAGES = 10
#: The most tools that one server may expose. A server past it is left out,
#: because hundreds of tools crowd the model and each one is egress.
MAX_TOOLS = 128
#: The longest reason that a log line carries.
_REASON_CHARS = 80


@dataclass(frozen=True)
class McpServer:
    """One enabled ``http-sse`` row, as the injection read it.

    ``headers`` is a credential, so it stays out of ``repr``."""

    name: str
    url: str
    headers: dict[str, str] = field(default_factory=dict, repr=False)


#: An open server: the tool and the client that the run must close.
Opened = list[tuple[Any, Any]]


@functools.lru_cache(maxsize=16)
def _parse_orgs(raw: str) -> frozenset[str]:
    """The ids in *raw*, trimmed and in lower case. Cached on the raw value."""
    return frozenset(p.strip().lower() for p in raw.split(",") if p.strip())


def maf_mcp_orgs() -> frozenset[str]:
    """The organization ids in ``MAF_MCP_ORGS``. Empty means none."""
    return _parse_orgs(str(getattr(get_settings(), "maf_mcp_orgs", "") or ""))


def maf_mcp_org_allowed(organization_id: str | None) -> bool:
    """True when ``MAF_MCP_ORGS`` allows *organization_id*.

    No organization is false, also with ``*``."""
    org = str(organization_id or "").strip().lower()
    if not org:
        return False
    orgs = maf_mcp_orgs()
    return ALL_ORGS in orgs or org in orgs


def is_native_maf_agent(agent: Any) -> bool:
    """True for a native MAF ``Agent``, and false for a Copilot agent."""
    try:
        from agent_framework import Agent
    except ImportError:
        return False
    return isinstance(agent, Agent) and not hasattr(agent, "_permission_handler")


def mcp_service(name: str) -> str:
    """The service name of an MCP server, for ``integrations:use:<service>``."""
    return f"mcp-{name}"


def _host(url: str) -> str:
    """The host of *url* for a log line. Never the path or the query."""
    try:
        import httpx

        return httpx.URL(url).raw_host.decode("ascii")[:100]
    except Exception:  # a URL that does not parse names no host
        return ""


def _reason(exc: BaseException) -> str:
    """A short reason for *exc*, safe to log.

    An ``OutboundRefused`` anywhere in the chain gives its own reason. Other
    faults give the class name of the deepest error, never its message, which
    can hold a URL or a header."""
    from gateway.outbound_guard import OutboundRefused

    seen: set[int] = set()
    stack: list[BaseException] = [exc]
    leaf = type(exc).__name__
    while stack and len(seen) < 32:
        cur = stack.pop()
        if id(cur) in seen:
            continue
        seen.add(id(cur))
        if isinstance(cur, OutboundRefused):
            return str(cur.reason)[:_REASON_CHARS]
        nested = list(getattr(cur, "exceptions", None) or [])
        for nxt in (cur.__cause__, cur.__context__, *nested):
            if isinstance(nxt, BaseException):
                stack.append(nxt)
        if cur.__cause__ is None and cur.__context__ is None and not nested:
            leaf = type(cur).__name__
    return leaf[:_REASON_CHARS]


async def _aclose_client(client: Any) -> None:
    """Close one pinned client. Never raises an ``Exception``."""
    if client is not None:
        with contextlib.suppress(Exception):
            await client.aclose()


async def _stop_owner(tool: Any) -> None:
    """Cancel the MAF lifecycle task of *tool*, if one still runs.

    MAF runs each connect and close on one task per tool. A cancel of the
    waiter leaves that task running, so a close abandons it here, or it
    would keep the socket and the session for ever."""
    task = getattr(tool, "_lifecycle_owner_task", None)
    if task is None or task.done():
        return
    task.cancel()
    with contextlib.suppress(Exception, asyncio.CancelledError):
        async with asyncio.timeout(0.5):
            await asyncio.shield(task)


async def _close_tool(tool: Any) -> bool:
    """Close one tool within :data:`CLOSE_BUDGET_S`. True when it closed.

    A close that runs past the budget stops the lifecycle task of MAF."""
    if tool is None:
        return True
    try:
        async with asyncio.timeout(CLOSE_BUDGET_S):
            await tool.close()
        return True
    except Exception:  # a timeout or a transport fault: the caller goes on
        await _stop_owner(tool)
        return False


async def _close_one(tool: Any, client: Any, *, client_first: bool = False) -> None:
    """Close one tool and its client. Raises nothing but a cancel.

    ``client_first`` is the failure path (review round 2 of PR #869). When a
    connect runs past its budget, MAF cancels only the waiter, and its
    lifecycle task goes on with the connect. ``tool.close()`` waits behind
    that connect. Closing the client first makes the connect fail at once.
    At the end of a run the tool closes first, so the server gets its
    session DELETE, and then the client closes.

    It suppresses ``Exception`` only, never ``BaseException``: a Stop is a
    ``CancelledError``, and the run must see it (review round 2).
    """
    if client_first:
        await _aclose_client(client)
        await _stop_owner(tool)
        await _close_tool(tool)
        return
    await _close_tool(tool)
    await _aclose_client(client)


async def close_mcp_tools(opened: Opened) -> None:
    """Close each tool and client of *opened*, and empty it.

    The servers close at the same time, under one cap of
    :data:`CLOSE_TOTAL_S`. A slow session DELETE cannot hold the end of the
    run (review round 2 of PR #869). Each client closes in the end, also when
    the cap fires. Raises nothing but a cancel."""
    pairs = list(opened)
    opened.clear()
    if not pairs:
        return
    try:
        async with asyncio.timeout(CLOSE_TOTAL_S):
            await asyncio.gather(
                *(_close_one(tool, client) for tool, client in pairs),
                return_exceptions=True,
            )
    except TimeoutError:
        _log.warning("mcp.close_timeout", servers=len(pairs))
    finally:
        # Each client closes, also when the cap fired. A client close sends
        # nothing, so one more second is enough, and it is capped too.
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(1.0):
                for _tool, client in pairs:
                    await _aclose_client(client)


class McpListTooLong(Exception):
    """A server sent more ``tools/list`` pages or tools than the caps allow."""


def _cap_list_tools(session: Any) -> None:
    """Cap the ``tools/list`` paging of ONE MCP session.

    MAF follows ``nextCursor`` with no end, so a server that always sends a
    cursor holds the connect for ever (review round 2 of PR #869). A load
    starts at the page with no cursor. Past :data:`MAX_TOOL_PAGES` pages or
    :data:`MAX_TOOLS` tools, it raises :class:`McpListTooLong`, the connect
    fails, and the server is left out."""
    real = getattr(session, "list_tools", None)
    if real is None or getattr(real, "__ws8c_capped__", False):
        return
    count = {"pages": 0, "tools": 0}

    async def _capped(*args: Any, **kwargs: Any) -> Any:
        params = kwargs.get("params", args[0] if args else None)
        if getattr(params, "cursor", None) is None:
            count["pages"] = count["tools"] = 0
        count["pages"] += 1
        if count["pages"] > MAX_TOOL_PAGES:
            raise McpListTooLong("pages")
        result = await real(*args, **kwargs)
        count["tools"] += len(getattr(result, "tools", None) or [])
        if count["tools"] > MAX_TOOLS:
            raise McpListTooLong("tools")
        return result

    _capped.__ws8c_capped__ = True  # type: ignore[attr-defined]
    with contextlib.suppress(AttributeError, TypeError):
        session.list_tools = _capped


@functools.lru_cache(maxsize=1)
def _run_scoped_tool_class() -> type:
    """``MCPStreamableHTTPTool``, with the connection owned by the run.

    MAF enters each tool of ``agent.mcp_tools`` again: ``Agent.__aenter__``
    (the native tier of the executor) and ``agent.run`` both do. The base
    ``__aenter__`` connects again and lists the tools again, with no time
    budget, and a failure there fails the run (review of PR #869). So:

    * a connected tool is not entered again;
    * a tool that is not connected gets one try within
      :data:`CONNECT_BUDGET_S`. A failure is logged like the first one, and
      the tool stays with no function, so the run goes on without it;
    * ``__aexit__`` does nothing. The run closes the tool in its ``finally``
      (:func:`close_mcp_tools`).

    Each session it opens gets the ``tools/list`` caps (:func:`_cap_list_tools`).
    """
    from agent_framework import MCPStreamableHTTPTool

    class RunScopedMCPTool(MCPStreamableHTTPTool):
        @property  # type: ignore[override]
        def session(self) -> Any:
            return self.__dict__.get("_ws8c_session")

        @session.setter
        def session(self, value: Any) -> None:
            if value is not None:
                _cap_list_tools(value)
            self.__dict__["_ws8c_session"] = value

        async def __aenter__(self) -> Any:
            if self.is_connected:
                return self
            try:
                async with asyncio.timeout(CONNECT_BUDGET_S):
                    await self.connect()
            except asyncio.CancelledError:
                await _close_one(self, self._httpx_client, client_first=True)
                raise
            except Exception as exc:
                _log.warning("mcp.unreachable", name=self.name, host=_host(self.url),
                             reason=_reason(exc))
                await _close_one(self, self._httpx_client, client_first=True)
            return self

        async def __aexit__(self, *_exc: Any) -> None:
            return None

    return RunScopedMCPTool


def _names_held(agent: Any) -> set[str]:
    """The names of the functions that *agent* already holds."""
    from acb_skills.tool_guard import tool_name

    opts = getattr(agent, "default_options", None)
    held = {tool_name(t) for t in (opts.get("tools") or [])} if isinstance(opts, dict) else set()
    for tool in getattr(agent, "mcp_tools", None) or []:
        held |= {tool_name(f) for f in getattr(tool, "functions", None) or []}
    return {n for n in held if n}


async def attach_mcp_tools(
    agent: Any, servers: list[McpServer], opened: Opened,
) -> list[str]:
    """Connect each server and add its tool to ``agent.mcp_tools``.

    Returns the names of the servers that the agent got. Each one goes into
    *opened*, and the run closes it. A server that fails is left out.

    So is a server whose function names collide with a function that the
    agent already holds, for example a server ``web`` with a tool ``search``
    beside the platform's ``web_search``. MAF refuses a duplicate name when
    the run starts, so one such server would fail every run of the agent
    (review of PR #869)."""
    try:
        tool_cls = _run_scoped_tool_class()
        from gateway import outbound_guard
    except ImportError as exc:
        _log.warning("mcp.client_unavailable", reason=type(exc).__name__)
        return []

    held = _names_held(agent)
    tools: list[Any] = []
    for server in servers:
        host = _host(server.url)
        try:
            client = await outbound_guard.mcp_client(server.url)
        except outbound_guard.OutboundRefused as exc:
            _log.warning("mcp.refused", name=server.name, host=exc.host, reason=exc.reason)
            continue
        except Exception as exc:  # a lookup fault is a refusal too
            _log.warning("mcp.refused", name=server.name, host=host, reason=_reason(exc))
            continue
        tool = None
        try:
            tool = tool_cls(
                name=server.name,
                url=server.url,
                tool_name_prefix=server.name,
                http_client=client,
                static_headers=dict(server.headers) or None,
                load_prompts=False,
                request_timeout=REQUEST_TIMEOUT_S,
            )
            async with asyncio.timeout(CONNECT_BUDGET_S):
                await tool.connect()
        except asyncio.CancelledError:
            # A Stop. Close what this server opened, then let the run see it.
            await _close_one(tool, client, client_first=True)
            raise
        except Exception as exc:
            _log.warning("mcp.unreachable", name=server.name, host=host, reason=_reason(exc))
            await _close_one(tool, client, client_first=True)
            continue
        exposed = {getattr(f, "name", "") for f in tool.functions}
        clash = exposed & held
        if clash:
            _log.warning("mcp.name_collision", name=server.name, tools=sorted(clash)[:5])
            await _close_one(tool, client)
            continue
        held |= exposed
        opened.append((tool, client))
        tools.append(tool)

    if not tools:
        return []
    # New lists, so an agent object that another run holds never changes.
    agent.mcp_tools = [*list(getattr(agent, "mcp_tools", None) or []), *tools]
    middleware = list(getattr(agent, "middleware", None) or [])
    if not any(isinstance(m, McpPermissionGate) for m in middleware):
        agent.middleware = [*middleware, McpPermissionGate()]
    names = [t.name for t in tools]
    _log.info("mcp.attached", servers=names)
    return names


def _arguments(context: Any) -> dict[str, Any]:
    """The call arguments of a MAF function invocation, as a dict."""
    args = getattr(context, "arguments", None)
    if isinstance(args, Mapping):
        return dict(args)
    dump: Callable[[], Any] | None = getattr(args, "model_dump", None)
    if callable(dump):
        with contextlib.suppress(Exception):
            out = dump()
            if isinstance(out, Mapping):
                return dict(out)
    return {}


def _make_gate_class() -> type:
    from agent_framework import FunctionMiddleware

    class _McpPermissionGate(FunctionMiddleware):
        """Runs ``permission_policy.decide()`` for each MCP function call.

        A call that the policy refuses in ``enforce`` mode never reaches the
        server. Other functions pass, because their own wrapper gates them."""

        async def process(self, context: Any, call_next: Callable[[], Any]) -> None:
            from acb_skills.egress import _from_mcp
            from acb_skills.tool_guard import tool_name

            from orchestrator._tool_injection import _permission_gate

            function = getattr(context, "function", None)
            if function is not None and _from_mcp(function):
                allowed, reason = _permission_gate(
                    tool_name(function), _arguments(context), surface="mcp_tool",
                )
                if not allowed:
                    context.result = f"[blocked by permission policy: {reason}]"
                    return
            await call_next()

    return _McpPermissionGate


McpPermissionGate: type = _make_gate_class()


__all__ = [
    "ALL_ORGS",
    "McpPermissionGate",
    "McpServer",
    "attach_mcp_tools",
    "close_mcp_tools",
    "is_native_maf_agent",
    "maf_mcp_org_allowed",
    "maf_mcp_orgs",
    "mcp_service",
]
