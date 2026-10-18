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
6. **The confirmation card** (H-299). The gate gives ``decide()`` the hints
   that the server sent in ``tools/list``. A tool needs the member's card
   unless it says read-only and does not say destructive, so a tool with no
   hints needs the card. The card is ``ask_tools.request_confirmation``, the
   one card seam. Deny and a timeout return a refusal, and the tool never
   runs. A run with no member, a batch run and a WhatsApp run cannot show the
   card, so such a tool is refused there. The hints are the server's claim,
   and a hostile server can lie. Each attach logs them as
   ``mcp.tool_annotations``, so an admin can audit them.
   **Always confirm** (H-300): an admin can set a server to "always
   confirm" (``provider_meta.always_confirm`` on its registry row). Then
   EVERY tool of that server needs the card, whatever its hints say. The flag
   goes from the row to :class:`McpServer`, onto the tool at the attach
   (:func:`server_always_confirms`), and to ``decide()`` as the fact
   ``mcp_always_confirm``. ``decide()`` stays the one gate.
7. **No stdio.** Nothing here builds an ``MCPStdioTool`` (D85). Fence:
   ``tests/unit/test_no_mcp_stdio.py``.
8. **Tools on demand** (WS-8c items 22 to 30). Each tool is built with MAF's
   own progressive disclosure on. So the model sees three loaders
   (``<prefix>_list_mcp_tools``, ``_load_tool`` and ``_unload_tool``) in
   place of the full schemas. A Metorite meta-tool would be a second seam.
   After the connect, a server with :data:`DIRECT_TOOLS_MAX` tools or fewer
   whose schemas fit :data:`SERVER_SCHEMA_TOKENS` shows its tools directly.
   When the servers of a run pass :data:`RUN_SCHEMA_TOKENS`, the largest
   server that still shows full schemas changes to the loaders, until the
   sum fits. The list loader returns an index only (:func:`mcp_index`). The
   loaders carry ``egress.MCP_LOADER_KEY``, so they are egress, and the gate
   lets them pass with no card. A loaded tool is an MCP function, and its
   card rule holds. A run attaches :data:`MAX_SERVERS` servers or fewer.
9. **The 401 of an OAuth server** (WS-54 IN-8b,
   ``integrations_console.md`` §3.6.7). The registry refreshed the token
   BEFORE the connect, so the header is fixed for the run
   (``static_headers``). A 401 on the connect leaves the server out, logs
   ``mcp.oauth_unauthorized`` with the name only, and the run goes on. Each
   such 401 calls ``on_unauthorized``, and the server says whether the
   registry refreshed the token in THIS run. The registry decides: after a
   refresh, ``needs_reconnect``. With no refresh, it ends the stored token,
   so the next run refreshes, and an ``invalid_grant`` then marks the row.
   A token that cannot refresh gets ``needs_reconnect`` at once. So one
   expired token never asks an admin to connect again, and a token that
   never says when it ends cannot stay dead for ever (review of #1011).
10. **The acts-as line** (WS-54 IN-8c, D98, ``integrations_console.md``
    §3.6.9). The card of a tool of an OAuth server says "Acts as <admin
    email> at <host>." right after the hint line. The admin is the
    ``connected_by`` of the row, and the attach keeps it on the tool
    (:func:`server_acts_as`). The card escapes both values as it escapes
    the names (item 19). A header server gets no line.

Fences: ``tests/unit/test_maf_mcp_injection.py``,
``tests/unit/test_maf_mcp_org_scope_r8.py``,
``tests/unit/test_maf_mcp_confirm.py`` (H-299),
``tests/unit/test_maf_mcp_budget.py`` (items 22 to 30),
``tests/unit/test_mcp_oauth_refresh.py`` and
``tests/unit/test_mcp_oauth_refresh_r8.py`` (rule 9).
"""
from __future__ import annotations

import asyncio
import contextlib
import functools
import itertools
import json
import re
import unicodedata
import warnings
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from acb_common import get_logger, get_settings

if TYPE_CHECKING:
    from agent_framework import FunctionInvocationContext

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
#: Seconds that :func:`_stop_owner` waits for the MAF task to end after its
#: cancel. A task that ends later is left to end on its own.
STOP_WAIT_S = 0.5
#: The most ``tools/list`` pages that one server may send in one load.
MAX_TOOL_PAGES = 10
#: The most tools that one server may expose. A server past it is left out,
#: because hundreds of tools crowd the model and each one is egress.
MAX_TOOLS = 128
#: A server with this many tools or fewer may show its full schemas (WS-8c
#: item 23). A server with more shows the three MAF loaders only.
DIRECT_TOOLS_MAX = 12
#: The most MCP schema tokens that one server may cost at run start (item 24).
SERVER_SCHEMA_TOKENS = 1500
#: The most MCP schema tokens that all servers of one run may cost (item 24).
RUN_SCHEMA_TOKENS = 4000
#: The most servers that one run attaches, in name order (item 28).
MAX_SERVERS = 8
#: The longest line of one tool in the index (item 25).
INDEX_LINE_CHARS = 80
#: The action of each MAF loader, in the order that MAF builds them.
LOADER_ACTIONS = ("list", "load", "unload")
#: The longest reason that a log line carries.
_REASON_CHARS = 80
#: The MCP hint names, from the ``annotations`` of one tool (MCP 2025-03-26).
HINT_NAMES = ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint")
#: The longest server description that a card carries.
_DESCRIPTION_CHARS = 1000
#: Seconds for ``on_unauthorized`` to mark ONE row (rule 9). The run waits
#: on it, so it is capped. A mark past it is dropped, and the run goes on.
UNAUTHORIZED_MARK_S = 5.0


@dataclass(frozen=True)
class McpServer:
    """One enabled ``http-sse`` row, as the injection read it.

    ``headers`` is a credential, so it stays out of ``repr``."""

    name: str
    url: str
    headers: dict[str, str] = field(default_factory=dict, repr=False)
    #: H-300: every tool of this server needs the card. False keeps H-299.
    always_confirm: bool = False
    #: IN-8b: the header is the bearer token of an OAuth consent.
    oauth: bool = False
    #: IN-8b: the registry refreshed that token in THIS run, before the
    #: connect. The registry reads it on a 401 (rule 9).
    oauth_refreshed: bool = False
    #: IN-8c (D98): the admin that an OAuth connection acts as, from the
    #: ``connected_by`` of its row. Empty for a header server.
    acts_as: str = ""


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
    # Wait a moment for the task to end. Only two outcomes are quiet: the
    # wait runs out, or the task ends with the cancel this function sent. A
    # cancel of the CALLER (a Stop) arrives as a CancelledError while the
    # task is not cancelled itself, and it must reach the run (review of
    # #869, P2).
    current = asyncio.current_task()
    # A caller already in a cancel (the attach's own Stop path) counts once.
    # Only a cancel that arrives DURING this wait is a new one.
    before = current.cancelling() if current is not None else 0
    try:
        async with asyncio.timeout(STOP_WAIT_S):
            await asyncio.shield(task)
    except TimeoutError:
        return
    except asyncio.CancelledError:
        if current is not None and current.cancelling() > before:
            raise
        return
    except Exception:  # the task failed as it stopped: it is stopped
        return


async def _close_tool(tool: Any) -> bool:
    """Close one tool within :data:`CLOSE_BUDGET_S`. True when it closed.

    A close that runs past the budget stops the lifecycle task of MAF.

    MAF puts the close in the queue of its lifecycle task when that task
    still runs. When :func:`_stop_owner` cancelled the task and its wait ran
    out, the task ends after the close is in its queue. Then MAF fails the
    close with the task's OWN ``CancelledError``. That is a failed close, not
    a Stop, so it gives False. If it went up, an unreachable server would
    stop the run. A cancel of the CALLER (a Stop) still goes up: only a task
    that ran when the close started, and that ended in a cancel, can cause
    the quiet path."""
    if tool is None:
        return True
    owner = getattr(tool, "_lifecycle_owner_task", None)
    queued_on_owner = owner is not None and not owner.done()
    current = asyncio.current_task()
    before = current.cancelling() if current is not None else 0
    try:
        async with asyncio.timeout(CLOSE_BUDGET_S):
            await tool.close()
        return True
    except asyncio.CancelledError:
        caller_cancelled = current is None or current.cancelling() > before
        if caller_cancelled or not queued_on_owner or not owner.cancelled():
            raise
        _log.info("mcp.close_owner_stopped", name=str(getattr(tool, "name", ""))[:100])
        return False
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


def _cap_list_tools(session: Any, record: Callable[[Any], None] | None = None) -> None:
    """Cap the ``tools/list`` paging of ONE MCP session.

    MAF follows ``nextCursor`` with no end, so a server that always sends a
    cursor holds the connect for ever (review round 2 of PR #869). A load
    starts at the page with no cursor. Past :data:`MAX_TOOL_PAGES` pages or
    :data:`MAX_TOOLS` tools, it raises :class:`McpListTooLong`, the connect
    fails, and the server is left out.

    *record* gets each page that passes the caps. The tool keeps the hints
    of each listed tool from it (H-299), because MAF drops them."""
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
        if record is not None:
            # A fault here loses the hints only, and a tool with no hints
            # needs the card. So it fails closed.
            with contextlib.suppress(Exception):
                record(result)
        return result

    _capped.__ws8c_capped__ = True  # type: ignore[attr-defined]
    with contextlib.suppress(AttributeError, TypeError):
        session.list_tools = _capped


def tool_hints(annotations: Any) -> dict[str, bool] | None:
    """The MCP hints of one tool as a dict, or None when it sent none.

    Only a real boolean is kept, so a hint of ``"true"`` counts as missing."""
    if annotations is None:
        return None
    if isinstance(annotations, Mapping):
        raw = dict(annotations)
    else:
        raw = {name: getattr(annotations, name, None) for name in HINT_NAMES}
    hints = {n: raw[n] for n in HINT_NAMES if isinstance(raw.get(n), bool)}
    return hints or None


@dataclass(frozen=True)
class ToolMeta:
    """What one server said about one of its tools in ``tools/list``.

    Both fields are the server's own claim. Nothing here checks them."""

    hints: dict[str, bool] | None
    description: str


def _record_tool_list(meta: dict[str, ToolMeta], result: Any, server: str = "") -> None:
    """Keep the hints and the description of each tool on one page.

    A later list (``notifications/tools/list_changed``) can only make a tool
    stricter in this run. A tool that needed the card keeps its old hints,
    so a server cannot list a tool as destructive at the attach and then
    relax it. Each change of the hints of a known tool is logged as
    ``mcp.tool_annotations_changed``, so the audit sees every claim that
    decided a call (review of PR #888, P2)."""
    from acb_skills.permission_policy import mcp_needs_confirmation

    for listed in getattr(result, "tools", None) or []:
        remote = str(getattr(listed, "name", "") or "")
        if not remote:
            continue
        hints = tool_hints(getattr(listed, "annotations", None))
        description = str(getattr(listed, "description", "") or "")[:_DESCRIPTION_CHARS]
        before = meta.get(remote)
        if before is not None and before.hints != hints:
            relaxed = (mcp_needs_confirmation(before.hints)
                       and not mcp_needs_confirmation(hints))
            _log.warning("mcp.tool_annotations_changed", server=server,
                         tool=remote[:100], before=before.hints, after=hints,
                         kept_before=relaxed)
            if relaxed:
                hints = before.hints
        meta[remote] = ToolMeta(hints=hints, description=description)


#: The key on a tool's ``__dict__`` that holds its server's H-300 flag.
_ALWAYS_CONFIRM_KEY = "_ws8c_always_confirm"
#: IN-8c: the key that holds ``(admin email, host)`` of an OAuth server.
_ACTS_AS_KEY = "_in8c_acts_as"


def server_acts_as(tool: Any) -> tuple[str, str] | None:
    """The admin email and the host that the server of *tool* acts as at
    (D98, IN-8c), or None for a server that is not an OAuth connection.

    The attach writes it from the registry row. A tool with no key, or with
    a value of another shape, reads as None, so the card shows no line."""
    value = getattr(tool, "__dict__", {}).get(_ACTS_AS_KEY)
    if (isinstance(value, tuple) and len(value) == 2
            and all(isinstance(v, str) and v for v in value)):
        return value[0], value[1]
    return None


def server_always_confirms(tool: Any) -> bool:
    """True when an admin set the server of *tool* to "always confirm" (H-300).

    The attach writes the flag from the registry row. A tool with no flag
    reads as false, as a row with no key does."""
    return getattr(tool, "__dict__", {}).get(_ALWAYS_CONFIRM_KEY) is True


def tool_meta(tool: Any, remote_name: str) -> ToolMeta | None:
    """What the server of *tool* said about its tool *remote_name*, or None."""
    meta = getattr(tool, "__dict__", {}).get("_ws8c_tool_meta")
    return meta.get(remote_name) if isinstance(meta, dict) else None


def _log_tool_hints(tool: Any) -> None:
    """Log the hints of each tool of one attached server (H-299).

    The hints are the server's claim, and a hostile server can mark a
    destructive tool read-only. This line lets an admin audit what it said."""
    meta = getattr(tool, "__dict__", {}).get("_ws8c_tool_meta") or {}
    for remote, info in sorted(meta.items())[:MAX_TOOLS]:
        _log.info("mcp.tool_annotations", server=getattr(tool, "name", ""),
                  tool=str(remote)[:100], hints=info.hints)


# ── Tools on demand (WS-8c items 22 to 30) ───────────────────────────────────


def schema_tokens(functions: Any) -> int:
    """What the schemas of *functions* cost the model, in tokens.

    Measured as ``tests/unit/test_tool_schema_diet.py`` measures the platform
    tools: ``skill_families.tool_json_schema`` and the tokenizer of
    ``acb_llm.context.count_message_tokens``, one schema at a time."""
    from acb_llm.context import count_message_tokens
    from acb_skills.skill_families import tool_json_schema

    return sum(
        count_message_tokens([{"role": "system", "content": json.dumps(tool_json_schema(fn))}])
        for fn in list(functions or [])
    )


def _loader_functions(tool: Any) -> list[Any]:
    """The MAF loaders of *tool* that exist now. It never builds them."""
    return list(getattr(tool, "__dict__", {}).get("_progressive_loader_functions") or [])


def is_mcp_loader(function: Any) -> bool:
    """True for a loader tool that :func:`_mark_loaders` marked."""
    from acb_skills.egress import MCP_LOADER_KEY

    props = getattr(function, "additional_properties", None)
    return isinstance(props, Mapping) and props.get(MCP_LOADER_KEY) is True


def _mark_loaders(loaders: list[Any]) -> list[Any]:
    """Mark each MAF loader with ``egress.MCP_LOADER_KEY`` (item 26).

    MAF builds the loaders with no ``_mcp_is_tool`` key, so the egress rule
    and the gate would not see them as MCP tools. The key is Metorite's, so
    the loops of MAF that read ``_mcp_is_tool`` do not change."""
    from acb_skills.egress import MCP_LOADER_KEY

    for fn in loaders:
        props = dict(getattr(fn, "additional_properties", None) or {})
        if props.get(MCP_LOADER_KEY) is not True:
            props[MCP_LOADER_KEY] = True
            fn.additional_properties = props
    return loaders


def _index_line(text: Any) -> str:
    """The first line of *text* that holds a character, for the index.

    Each control, format and line separator character is written as
    ``\\uXXXX``, so a description cannot reorder the line or start a fake
    one. The line is cut at :data:`INDEX_LINE_CHARS`."""
    first = next((ln.strip() for ln in str(text or "").splitlines() if ln.strip()), "")
    shown = "".join(
        f"\\u{ord(ch):04x}" if unicodedata.category(ch) in ("Cc", "Cf", "Co", "Cs", "Zl", "Zp")
        else ch
        for ch in first
    )
    return shown if len(shown) <= INDEX_LINE_CHARS else shown[: INDEX_LINE_CHARS - 1] + "…"


def mcp_index(tool: Any) -> list[dict[str, Any]]:
    """The index that the list loader of *tool* returns (item 25).

    Each entry holds the name to load, one line of the server's description
    (:func:`_index_line`), and ``read_only``, and nothing else. MAF's own list
    returns the full ``parameters()`` of each tool, so one list call cost what
    the disclosure saved. ``read_only`` is true only when the gate would
    answer ``mcp_read_only`` (item 13): the hints say read-only and not
    destructive, and the server is not set to always confirm (H-300). The
    model gets the full schema when it loads the tool. This is the two-tier
    model of ``acb_skills.artifact_kit``."""
    from acb_skills.permission_policy import mcp_needs_confirmation

    always = server_always_confirms(tool)
    loaders = {getattr(f, "name", "") for f in _loader_functions(tool)}
    out: list[dict[str, Any]] = []
    for fn in list(getattr(tool, "_functions", None) or []):
        name = str(getattr(fn, "name", "") or "")
        if not name or name in loaders:
            continue
        props = getattr(fn, "additional_properties", None) or {}
        remote = str(props.get("_mcp_remote_name") or name)
        meta = tool_meta(tool, remote)
        text = (meta.description if meta is not None else "") or str(
            getattr(fn, "description", "") or "")
        out.append({
            "name": name,
            "description": _index_line(text),
            "read_only": not always and not mcp_needs_confirmation(
                meta.hints if meta is not None else None),
        })
    return out


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

    Each session it opens gets the ``tools/list`` caps (:func:`_cap_list_tools`),
    and the tool keeps the hints of each listed tool (:func:`tool_meta`).

    With progressive disclosure on (WS-8c items 25 and 26), each loader that
    MAF builds is marked (:func:`_mark_loaders`), and the list loader returns
    the index of :func:`mcp_index`, not the full schemas.
    """
    from agent_framework import MCPStreamableHTTPTool

    class RunScopedMCPTool(MCPStreamableHTTPTool):
        def _progressive_loader_tools(self) -> list[Any]:
            return _mark_loaders(super()._progressive_loader_tools())

        def _list_progressive_mcp_tools(  # type: ignore[override]
            self, ctx: FunctionInvocationContext,
        ) -> list[dict[str, Any]]:
            # The annotation names the class, so MAF passes the context in.
            return mcp_index(self)

        @property
        def session(self) -> Any:
            return self.__dict__.get("_ws8c_session")

        @session.setter
        def session(self, value: Any) -> None:
            if value is not None:
                meta = self.__dict__.setdefault("_ws8c_tool_meta", {})
                _cap_list_tools(value, functools.partial(
                    _record_tool_list, meta, server=str(getattr(self, "name", "") or "")))
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
        held |= {tool_name(f) for f in getattr(tool, "_functions", None) or []}
    return {n for n in held if n}


def _choose_disclosure(tool: Any) -> None:
    """Turn the loaders off for a small server, after its connect (item 23).

    A server with :data:`DIRECT_TOOLS_MAX` tools or fewer, whose full
    schemas fit :data:`SERVER_SCHEMA_TOKENS`, shows its tools directly. Each
    other server keeps the loaders. MAF reads the attribute each time it
    reads ``functions``, so the change holds."""
    functions = list(getattr(tool, "_functions", None) or [])
    direct = (len(functions) <= DIRECT_TOOLS_MAX
              and schema_tokens(functions) <= SERVER_SCHEMA_TOKENS)
    tool.use_progressive_disclosure = not direct


def _fit_run_budget(tools: list[Any]) -> None:
    """Keep the MCP schemas of one run within :data:`RUN_SCHEMA_TOKENS` (item 24).

    While the sum passes the budget, the largest server that still shows
    full schemas changes to the loaders. Logs ``mcp.schema_budget`` with the
    cost of each server, the servers that show the index, and the total."""
    cost = {id(t): schema_tokens(t.functions) for t in tools}
    while sum(cost.values()) > RUN_SCHEMA_TOKENS:
        direct = [t for t in tools if not t.use_progressive_disclosure]
        if not direct:
            break
        largest = max(direct, key=lambda t: cost[id(t)])
        largest.use_progressive_disclosure = True
        cost[id(largest)] = schema_tokens(largest.functions)
    total = sum(cost.values())
    _log.info("mcp.schema_budget", total=total,
              servers={t.name: cost[id(t)] for t in tools},
              index=[t.name for t in tools if t.use_progressive_disclosure])
    if total > RUN_SCHEMA_TOKENS:
        _log.warning("mcp.schema_over_budget", total=total, budget=RUN_SCHEMA_TOKENS)


def _watch_status(client: Any) -> list[int]:
    """The status code of each answer that *client* gets, in order (rule 9).

    A response hook of ``httpx``, so the codes are read before MAF wraps a
    failure. MAF wraps a 401 in a ``ToolException`` whose chain holds a
    cancel and not the HTTP error, and whose message holds the URL. A client
    with no hooks (a test double) gives an empty list."""
    codes: list[int] = []
    hooks = getattr(client, "event_hooks", None)
    if not isinstance(hooks, dict):
        return codes

    async def _record(response: Any) -> None:
        codes.append(int(getattr(response, "status_code", 0) or 0))

    try:
        client.event_hooks = {**hooks, "response": [*hooks.get("response", []), _record]}
    except Exception:  # a client that refuses hooks: no 401 is seen
        return codes
    return codes


async def _report_unauthorized(
    on_unauthorized: Callable[[McpServer], Awaitable[Any]], server: McpServer,
) -> None:
    """Call *on_unauthorized* for *server* within :data:`UNAUTHORIZED_MARK_S`.

    Raises nothing but a cancel: a failed mark never fails the run. The log
    line names the server and the class of the fault only."""
    try:
        async with asyncio.timeout(UNAUTHORIZED_MARK_S):
            await on_unauthorized(server)
    except Exception as exc:  # a timeout or a store fault: the run goes on
        _log.warning("mcp.oauth_mark_failed", name=server.name, error=type(exc).__name__)


async def attach_mcp_tools(
    agent: Any, servers: list[McpServer], opened: Opened,
    *, member_present: bool = False,
    on_unauthorized: Callable[[McpServer], Awaitable[Any]] | None = None,
) -> list[str]:
    """Connect each server and add its tool to ``agent.mcp_tools``.

    Returns the names of the servers that the agent got. Each one goes into
    *opened*, and the run closes it. A server that fails is left out.

    So is a server whose function names collide with a function that the
    agent already holds, for example a server ``web`` with a tool ``search``
    beside the platform's ``web_search``. MAF refuses a duplicate name when
    the run starts, so one such server would fail every run of the agent
    (review of PR #869).

    *member_present* says that a member can answer a card in this run
    (H-299). It goes to the run's :class:`McpPermissionGate`. False, the
    default, refuses each tool that needs a card.

    WS-8c items 23 to 30: the run attaches :data:`MAX_SERVERS` servers or
    fewer, in name order, and logs ``mcp.server_cap`` with the rest. The cap
    counts the servers that ATTACH. A server that is refused, unreachable or
    in a clash takes no slot (review of #922, P2). Each
    tool is built with progressive disclosure on, and
    :func:`_choose_disclosure` and :func:`_fit_run_budget` keep the schemas
    within the budget. The clash check reads every tool of the server, also
    the tools that the loaders hide.

    *on_unauthorized* (WS-54 IN-8b, rule 9) is called for an OAuth server
    whose connect gets a 401. The caller reads ``oauth_refreshed`` and marks
    the row."""
    try:
        tool_cls = _run_scoped_tool_class()
        from agent_framework._feature_stage import ExperimentalWarning
        from gateway import outbound_guard
    except ImportError as exc:
        _log.warning("mcp.client_unavailable", reason=type(exc).__name__)
        return []

    # WS-8c item 28: 8 servers or fewer, in name order. The cap counts the
    # servers that attach, so a server that fails takes no slot. The rest
    # never open.
    servers = sorted(servers, key=lambda s: s.name)
    held = _names_held(agent)
    tools: list[Any] = []
    for index, server in enumerate(servers):
        if len(tools) >= MAX_SERVERS:
            _log.warning("mcp.server_cap", cap=MAX_SERVERS,
                         left_out=[s.name for s in servers[index:]])
            break
        host = _host(server.url)
        try:
            client = await outbound_guard.mcp_client(server.url)
        except outbound_guard.OutboundRefused as exc:
            _log.warning("mcp.refused", name=server.name, host=exc.host, reason=exc.reason)
            continue
        except Exception as exc:  # a lookup fault is a refusal too
            _log.warning("mcp.refused", name=server.name, host=host, reason=_reason(exc))
            continue
        # IN-8b, rule 9: the answers of an OAuth server, so a 401 is seen.
        codes = _watch_status(client) if server.oauth else []
        tool = None
        try:
            # WS-8c item 30: progressive disclosure is experimental in MAF, and
            # its constructor raises ExperimentalWarning. Only that class is
            # ignored, and only here. Nothing filters it for the process.
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", ExperimentalWarning)
                tool = tool_cls(
                    name=server.name,
                    url=server.url,
                    tool_name_prefix=server.name,
                    http_client=client,
                    static_headers=dict(server.headers) or None,
                    load_prompts=False,
                    request_timeout=REQUEST_TIMEOUT_S,
                    use_progressive_disclosure=True,
                )
            # H-300: the gate reads the server's flag from the tool.
            tool.__dict__[_ALWAYS_CONFIRM_KEY] = server.always_confirm is True
            # IN-8c (D98): the card names the admin that an OAuth row acts as.
            if server.oauth and server.acts_as and host:
                tool.__dict__[_ACTS_AS_KEY] = (str(server.acts_as), host)
            async with asyncio.timeout(CONNECT_BUDGET_S):
                await tool.connect()
        except asyncio.CancelledError:
            # A Stop. Close what this server opened, then let the run see it.
            await _close_one(tool, client, client_first=True)
            raise
        except Exception as exc:
            if server.oauth and 401 in codes:
                # Rule 9. The name only: no host, no reason, no header.
                _log.warning("mcp.oauth_unauthorized", name=server.name)
                await _close_one(tool, client, client_first=True)
                if on_unauthorized is not None:
                    await _report_unauthorized(on_unauthorized, server)
                continue
            _log.warning("mcp.unreachable", name=server.name, host=host, reason=_reason(exc))
            await _close_one(tool, client, client_first=True)
            continue
        _choose_disclosure(tool)
        # WS-8c item 29: with disclosure on, ``functions`` holds the loaders
        # only. So the check reads every tool of the server, and the names of
        # the loaders, which the budget below may still turn on.
        exposed = {getattr(f, "name", "") for f in (
            *tool._functions, *tool.functions, *tool._progressive_loader_tools())}
        exposed.discard("")
        clash = exposed & held
        if clash:
            _log.warning("mcp.name_collision", name=server.name, tools=sorted(clash)[:5])
            await _close_one(tool, client)
            continue
        held |= exposed
        opened.append((tool, client))
        tools.append(tool)
        _log_tool_hints(tool)

    if not tools:
        return []
    _fit_run_budget(tools)
    # New lists, so an agent object that another run holds never changes.
    agent.mcp_tools = [*list(getattr(agent, "mcp_tools", None) or []), *tools]
    # One gate, with the facts of THIS attach. An older gate is replaced, so
    # it never carries the member or the tools of another run.
    middleware = [m for m in list(getattr(agent, "middleware", None) or [])
                  if not isinstance(m, McpPermissionGate)]
    agent.middleware = [*middleware, McpPermissionGate(
        tools=list(agent.mcp_tools), member_present=member_present,
    )]
    names = [t.name for t in tools]
    _log.info("mcp.attached", servers=names,
              always_confirm=[t.name for t in tools if server_always_confirms(t)])
    return names


def _arguments(context: Any) -> dict[str, Any] | None:
    """The call arguments of a MAF function invocation, as a dict.

    No arguments is an empty call. A shape that the gate cannot read gives
    None, and the gate refuses the call, so the card never shows less than
    what runs (review of PR #888)."""
    args = getattr(context, "arguments", None)
    if args is None:
        return {}
    if isinstance(args, Mapping):
        return dict(args)
    dump: Callable[[], Any] | None = getattr(args, "model_dump", None)
    if callable(dump):
        try:
            out = dump()
        except Exception:
            return None
        if isinstance(out, Mapping):
            return dict(out)
    return None


# ── The confirmation card (H-299) ────────────────────────────────────────────

#: What the model reads when the member says no, or the card times out.
DENIED_TEXT = (
    "[not run: the member did not approve {tool}, or the card timed out. "
    "Do not call it again unless the member asks you to.]"
)
#: What the model reads in a run that has no member to answer a card.
NO_MEMBER_TEXT = (
    "[not run: {tool} can change data, so a member must confirm it on a "
    "card, and no member can confirm in this run. Do not call it again in "
    "this run.]"
)
#: What the model reads in a chat that cannot show a card (WhatsApp, and
#: each stream that does not say ``shows_cards=True``).
NO_CARD_CHANNEL_TEXT = (
    "[not run: {tool} can change data, so a member must confirm it on a "
    "card, and this chat cannot show that card. Tell the member to run it "
    "from the Metorite web chat.]"
)

#: What the model reads when the card cannot show the whole call.
CARD_TOO_LONG_TEXT = (
    "[not run: the arguments of {tool} are too long or too deeply nested "
    "for the member to see them all on the card, and a card that hides part "
    "of a call is not consent. Make a smaller call, or split it.]"
)
#: What the model reads when it calls a loader that no server of this run holds.
LOADER_REFUSED_TEXT = (
    "[not run: {tool} is not a loader of an MCP server of this run.]"
)
#: What the model reads when the gate cannot read the arguments at all.
UNREADABLE_ARGUMENTS_TEXT = (
    "[not run: Metorite could not read the arguments of {tool}, so it cannot "
    "check the call or show it to the member.]"
)

#: The value that takes the place of a sensitive argument on the card.
REDACTED = "[redacted]"
#: Name parts that mark a value as sensitive. A name is split on ``_``, ``-``,
#: ``.``, a space and camelCase, and each WHOLE part is compared. So
#: ``access_token`` and ``refreshToken`` match, and ``max_tokens`` and
#: ``keyword`` do not (review of PR #888, P2).
_SECRET_WORDS = frozenset({
    "password", "passwd", "passphrase", "passcode", "pwd", "pass", "secret", "token",
    "credential", "credentials", "authorization", "auth", "cookie", "bearer",
    "apikey", "pin", "otp", "cvv", "cvc", "ssn", "jwt",
})
#: A part that ENDS with one of these is sensitive too, so a name with no
#: separator (``accesstoken``, ``dbpassword``, ``clientsecret``) is hidden
#: (review of PR #897). ``tokens`` does not end with ``token``, so
#: ``max_tokens`` stays.
_SECRET_SUFFIXES = (
    "token", "password", "passwd", "pwd", "secret", "credential", "credentials", "passphrase",
)
#: Joined pairs with no separator. Each one is a whole part.
_SECRET_JOINED = frozenset({
    "privatekey", "accesskey", "secretkey", "clientsecret", "apikey", "sessionid", "sessionkey",
})
#: Two parts in a row that mark a value as sensitive. A bare ``key`` is a
#: call target (``delete_object(bucket, key)``), so it is not one.
_SECRET_PAIRS = frozenset({
    ("api", "key"), ("access", "key"), ("private", "key"), ("secret", "key"),
    ("session", "id"), ("session", "key"), ("client", "secret"),
})
#: A value that IS one of these, whole, is hidden. Prose that holds the word
#: "bearer" stays, because only the WHOLE value is compared.
_WHOLE_SCHEMES = (
    re.compile(r"bearer\s+[A-Za-z0-9._~+/=\-]+", re.IGNORECASE),
    re.compile(r"basic\s+[A-Za-z0-9+/=]{8,}", re.IGNORECASE),
)
#: Keys with a known prefix, hidden wherever they stand in a value: OpenAI,
#: Stripe and GitHub (review of PR #897).
_KEY_PREFIXES = (
    re.compile(r"(?<![A-Za-z0-9_])sk-[A-Za-z0-9_\-]{6,}"),
    re.compile(r"(?<![A-Za-z0-9_])(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{8,}"),
    re.compile(r"(?<![A-Za-z0-9_])pk_live_[A-Za-z0-9]{16,}"),
    re.compile(r"(?<![A-Za-z0-9_])gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"(?<![A-Za-z0-9_])github_pat_[A-Za-z0-9_]{20,}"),
)
#: Tokens with a known shape, hidden wherever they stand in a value: Slack,
#: AWS, Google and a JWT (three base64url segments).
_TOKEN_SHAPES = (
    re.compile(r"(?<![A-Za-z0-9_])xox[abprs]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"(?<![A-Za-z0-9])(?:AKIA|ASIA)[0-9A-Z]{16}(?![0-9A-Z])"),
    re.compile(r"(?<![A-Za-z0-9_])AIza[0-9A-Za-z_\-]{30,}"),
    re.compile(r"(?<![A-Za-z0-9_\-])eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]+"),
)
#: A PEM private key, from its BEGIN line through its END line (or to the
#: end of the value, when the END line is missing).
_PEM_KEY = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----(?:.*?-----END [A-Z0-9 ]*PRIVATE KEY-----|.*)",
    re.DOTALL,
)
#: The password in the user-info of a URL or a DSN: ``scheme://user:PASS@``.
_URL_PASSWORD = re.compile(r"(?P<head>[A-Za-z][A-Za-z0-9+.\-]*://[^\s/:@]+:)[^\s@/]+(?=@)")
#: A ``key=value`` or ``key: value`` pair. The value is hidden only when the
#: key is a secret name (``Password=...``, ``api_key=...``).
_KEY_VALUE = re.compile(
    r"(?<![A-Za-z0-9_.\-])(?P<key>[A-Za-z][A-Za-z0-9_.\-]*)(?P<sep>\"?\s*[=:]\s*)"
    r"(?P<value>(?:(?:bearer|basic|token)\s+)?(?:\"[^\"]*\"|'[^']*'|[^\s;&,\"']+))",
    re.IGNORECASE,
)
#: The deepest nesting that a card shows. A deeper call is refused, never cut.
_ARG_DEPTH = 8
#: The most characters of arguments that a card shows. ``request_confirmation``
#: keeps 4000 characters of the body, and the server's description takes the
#: rest. A longer call is refused, never cut (review of PR #888, P1).
_ARGS_CHARS = 2800
#: The most characters of the whole card body (``ask_tools.request_confirmation``).
_BODY_CHARS = 4000
#: The longest server name or tool name that a card shows.
NAME_CHARS = 64
#: The most names that the line of hidden values lists.
_HIDDEN_NAMES = 20
#: The label above the server's description on the card.
DESCRIPTION_LABEL = "Description from the MCP server (Metorite did not check this text):"


class CardTooLong(ValueError):
    """The card cannot show the whole call. ``ask_tools`` says a cut card is
    not consent, so the gate refuses the call."""


def _name_parts(name: Any) -> list[str]:
    """*name* in lower-case parts, split on separators and camelCase."""
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(name))
    text = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", text)
    return [p for p in re.split(r"[^a-z0-9]+", text.lower()) if p]


def _secret_part(part: str) -> bool:
    return (part in _SECRET_WORDS or part in _SECRET_JOINED
            or part.endswith(_SECRET_SUFFIXES))


def _sensitive_name(name: Any) -> bool:
    parts = _name_parts(name)
    return (any(_secret_part(p) for p in parts)
            or any(pair in _SECRET_PAIRS for pair in itertools.pairwise(parts)))


def _secret_value(text: str) -> bool:
    """True when the WHOLE value is a credential."""
    stripped = text.strip()
    return any(p.fullmatch(stripped) for p in (
        *_WHOLE_SCHEMES, *_KEY_PREFIXES, *_TOKEN_SHAPES, _PEM_KEY))


def _key_value(match: re.Match[str]) -> str:
    if not _sensitive_name(match.group("key")):
        return match.group(0)
    return match.group("key") + match.group("sep") + REDACTED


def _mask_secrets(text: str) -> str:
    """*text* with each credential INSIDE it replaced by :data:`REDACTED`.

    The rest of the value stays, so a member still reads the call."""
    text = _PEM_KEY.sub(REDACTED, text)
    text = _URL_PASSWORD.sub(lambda m: m.group("head") + REDACTED, text)
    text = _KEY_VALUE.sub(_key_value, text)
    for pattern in (*_KEY_PREFIXES, *_TOKEN_SHAPES):
        text = pattern.sub(REDACTED, text)
    return text


def _visible(text: str) -> str:
    """*text* with each invisible format character written as ``\\uXXXX``.

    A bidi override or a zero-width character can hide part of a value on
    the card. JSON already escapes the control characters."""
    return "".join(
        f"\\u{ord(ch):04x}" if unicodedata.category(ch) == "Cf" else ch for ch in text
    )


def _shown_name(name: Any) -> str:
    """A server or tool name as the card shows it: escaped and capped.

    The server writes its tool names. Each control, format, private or line
    separator character is written as ``\\uXXXX``, so a name cannot reorder
    the card or start a fake line (review of PR #888, P2). The name is cut
    at :data:`NAME_CHARS`. A cut name hides nothing that runs."""
    text = "".join(
        f"\\u{ord(ch):04x}" if unicodedata.category(ch)[0] in "CZ" and ch != " " else ch
        for ch in str(name)
    )
    return text if len(text) <= NAME_CHARS else text[: NAME_CHARS - 1] + "…"


def _local_name(name: Any) -> str:
    """The local tool name for the title: ``[A-Za-z0-9_.-]`` only, capped.

    MAF already normalises the local name. This keeps the title safe when a
    caller passes another name."""
    text = re.sub(r"[^A-Za-z0-9_.\-]", "-", str(name))
    return text if len(text) <= NAME_CHARS else text[: NAME_CHARS - 1] + "…"


def _redact(value: Any, depth: int, path: str, hidden: list[str], partly: list[str]) -> Any:
    if depth > _ARG_DEPTH:
        raise CardTooLong("depth")
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for name, inner in value.items():
            where = f"{path}.{name}" if path else str(name)
            if _sensitive_name(name):
                hidden.append(where)
                out[_visible(str(name))] = REDACTED
            else:
                out[_visible(str(name))] = _redact(inner, depth + 1, where, hidden, partly)
        return out
    if isinstance(value, list | tuple):
        return [_redact(v, depth + 1, f"{path}[{i}]", hidden, partly)
                for i, v in enumerate(value)]
    if value is None or isinstance(value, bool | int | float):
        return value
    text = str(value)
    if _secret_value(text):
        hidden.append(path or "(value)")
        return REDACTED
    masked = _mask_secrets(text)
    if masked != text:
        partly.append(path or "(value)")
    return _visible(masked)


def redact_arguments(value: Any, hidden: list[str] | None = None,
                     partly: list[str] | None = None) -> Any:
    """*value* with each sensitive value replaced by :data:`REDACTED`.

    A value is hidden whole when a part of its argument name is a secret
    word (``password``, ``api_key``, ``accesstoken``), or when the WHOLE
    value is a credential (a bearer token, a key with a known prefix, a
    JWT). A credential INSIDE a value is hidden in place: a key with a known
    prefix, a PEM key, the password of a URL, and ``Password=...``. *hidden*
    gets the path of each value hidden whole, and *partly* of each value
    hidden in part. Nothing else is cut: a call nested deeper than
    :data:`_ARG_DEPTH` raises :class:`CardTooLong`. No shared redaction
    helper fits structured arguments, so this one is local to the card."""
    return _redact(value, 0, "", hidden if hidden is not None else [],
                   partly if partly is not None else [])


def _count_line(paths: list[str], one: str, many: str) -> str:
    if not paths:
        return ""
    names = ", ".join(_shown_name(h) for h in paths[:_HIDDEN_NAMES])
    if len(paths) > _HIDDEN_NAMES:
        names += f", and {len(paths) - _HIDDEN_NAMES} more"
    if len(paths) == 1:
        return f"1 value {one}: {names}\n"
    return f"{len(paths)} values {many}: {names}\n"


def _hidden_line(hidden: list[str], partly: list[str] | None = None) -> str:
    """The card lines that say how many values are hidden, and which."""
    return (_count_line(hidden, "is hidden", "are hidden")
            + _count_line(list(partly or []), "is partly hidden", "are partly hidden"))


#: The first sentence of the card of a server that is set to always confirm.
ALWAYS_CONFIRM_LINE = "This server is set to always confirm."


def _hint_line(hints: dict[str, bool] | None, always_confirm: bool = False) -> str:
    if always_confirm:
        from acb_skills.permission_policy import mcp_needs_confirmation

        if not mcp_needs_confirmation(hints):
            return f"{ALWAYS_CONFIRM_LINE} The server marks this tool as read-only."
        return f"{ALWAYS_CONFIRM_LINE} {_hint_line(hints)}"
    if not hints:
        return "The server sent no hints for this tool."
    if hints.get("destructiveHint") is True:
        return "The server marks this tool as destructive."
    return "The server does not mark this tool as read-only."


def acts_as_line(acts_as: tuple[str, str] | None) -> str:
    """The card line of an OAuth server (D98, IN-8c), or an empty string.

    "Acts as <admin email> at <host>." The email is member data and the host
    comes from a URL that an admin typed, so each goes through
    :func:`_shown_name`, as the server and tool names do (item 19)."""
    if not acts_as:
        return ""
    email, host = acts_as
    return f"Acts as {_shown_name(email)} at {_shown_name(host)}."


def card_text(server: str, tool: str, arguments: dict[str, Any],
              meta: ToolMeta | None, fallback_description: str = "",
              local: str = "", always_confirm: bool = False,
              acts_as: tuple[str, str] | None = None) -> tuple[str, str, str]:
    """The title, detail and body of the card for one MCP call.

    The title names the LOCAL tool name of MAF (*local*), in
    ``[A-Za-z0-9_.-]`` only. The detail starts with the real hint line, and
    only then names the server and the remote tool, each through
    :func:`_shown_name`. So a tool name cannot spoof the hint line. The body
    shows EVERY argument, with :func:`redact_arguments`, then the lines that
    name each hidden value, and then the server's description under
    :data:`DESCRIPTION_LABEL`. Only the description is cut to fit.
    Arguments that do not fit raise :class:`CardTooLong`, because a member
    must see the whole call that Approve runs. With *always_confirm* (H-300)
    the detail first says that the server is set to always confirm.

    *acts_as* (IN-8c, D98) is ``(admin email, host)`` for an OAuth server.
    Its line (:func:`acts_as_line`) comes right after the hint line, and
    before the server and tool names. The detail stays one line, because
    the card draws it as one."""
    hidden: list[str] = []
    partly: list[str] = []
    shown = json.dumps(redact_arguments(arguments, hidden, partly), indent=2,
                       ensure_ascii=False, default=str)
    if len(shown) > _ARGS_CHARS:
        raise CardTooLong("size")
    head = f"Arguments:\n{shown}\n{_hidden_line(hidden, partly)}\n{DESCRIPTION_LABEL}\n"
    description = (meta.description if meta is not None else fallback_description) or ""
    description = _visible(description.strip()) or "(no description)"
    room = max(0, _BODY_CHARS - len(head))
    if len(description) > room:
        description = description[: max(0, room - 1)] + "…"
    shown_server, shown_tool = _shown_name(server), _shown_name(tool)
    title = f"Run the MCP tool {_local_name(local or tool)}?"
    acts = acts_as_line(acts_as)
    detail = (f"{_hint_line(meta.hints if meta else None, always_confirm)} "
              + (f"{acts} " if acts else "")
              + f"Server: {shown_server} · Tool: {shown_tool}")
    return title, detail, head + description


def _card_refusal(member_present: bool) -> tuple[str, str] | None:
    """Why this run cannot show a card, as ``(text, code)``, or None.

    WhatsApp first: an MCP call needs the run's live server session, so its
    card cannot park for a Confirm, and it is refused and never approved.
    This is the one card that a WhatsApp member still cannot answer (WS-17
    incident 2026-10-18, ``email_app_master_plan.md`` 12.3.13.8, a residual
    that ``tests/unit/test_mail_handoff_incident.py`` names). The
    untrusted-answer card of ``_tool_injection`` does not come here on
    WhatsApp. Then a run with no member, and a run with no
    channel that can show a card. A fault here refuses: it fails closed."""
    try:
        from acb_skills.ask_tools import cards_refused, confirmation_channel_open
        from acb_skills.whatsapp_ui import current_run

        if current_run() is not None or cards_refused():
            return NO_CARD_CHANNEL_TEXT, "no_card_channel"
        if not member_present:
            return NO_MEMBER_TEXT, "no_member"
        if not confirmation_channel_open():
            return NO_MEMBER_TEXT, "no_channel"
    except Exception:
        return NO_MEMBER_TEXT, "card_check_failed"
    return None


def _tool_functions(tool: Any) -> list[Any]:
    return list(getattr(tool, "_functions", None) or getattr(tool, "functions", None) or [])


def _owner_tool(tools: list[Any], function: Any) -> Any | None:
    """The MCP tool (the server) that holds *function*, or None.

    The same object first. Else a name that exactly ONE tool holds."""
    name = str(getattr(function, "name", "") or "")
    same = [t for t in tools if any(f is function for f in _tool_functions(t))]
    if not same:
        same = [t for t in tools
                if any(getattr(f, "name", None) == name for f in _tool_functions(t))]
    return same[0] if len(same) == 1 else None


def _owner_of(tools: list[Any], function: Any) -> tuple[str, str, ToolMeta | None]:
    """The server name, the remote tool name and its hints, for *function*.

    The owner is :func:`_owner_tool`. With none, an unknown server with no
    hints, which needs the card."""
    name = str(getattr(function, "name", "") or "")
    props = getattr(function, "additional_properties", None) or {}
    remote = str(props.get("_mcp_remote_name") or name)
    owner = _owner_tool(tools, function)
    if owner is None:
        return "(unknown server)", remote, None
    return str(getattr(owner, "name", "") or ""), remote, tool_meta(owner, remote)


def _loader_owner(tools: list[Any], function: Any) -> tuple[str, str] | None:
    """The server and the action of the loader *function*, or None.

    The match is BY IDENTITY among the loaders of *tools*, so a loader of
    another run, or a function that only carries the key, matches nothing."""
    for tool in tools:
        for index, loader in enumerate(_loader_functions(tool)):
            if loader is function:
                action = LOADER_ACTIONS[index] if index < len(LOADER_ACTIONS) else "?"
                return str(getattr(tool, "name", "") or ""), action
    return None


async def _pass_or_refuse_loader(tools: list[Any], context: Any, function: Any,
                                 call_next: Callable[[], Any]) -> None:
    """The gate's path for a function that is not an MCP tool, and for a loader.

    A function that is not a loader passes, because its own wrapper gates
    it. List, load and unload show no card (WS-8c item 27): the gate finds
    the loader BY IDENTITY among the loaders of an MCP tool of this run, and
    logs ``mcp.loader`` with the server and the action. A loader that no tool
    of the run holds is refused. A loaded tool is an MCP function, so its own
    card rule decides each call to it."""
    if function is None or not is_mcp_loader(function):
        await call_next()
        return
    found = _loader_owner(tools, function)
    if found is not None:
        _log.info("mcp.loader", server=found[0], action=found[1])
        await call_next()
        return
    name = _shown_name(getattr(function, "name", "") or "")
    _log.warning("mcp.loader", server="(unknown server)", action="refused", tool=name)
    context.result = LOADER_REFUSED_TEXT.format(tool=name)


def _make_gate_class() -> type:
    from agent_framework import FunctionMiddleware

    class _McpPermissionGate(FunctionMiddleware):
        """Runs ``permission_policy.decide()`` for each MCP function call.

        A call that the policy refuses in ``enforce`` mode never reaches the
        server. Other functions pass, because their own wrapper gates them.

        H-299: ``decide()`` gets the server's hints, and every code but
        ``mcp_read_only`` needs the member's card before the call reaches
        the server (``ask_tools.request_confirmation``). H-300: it also gets
        the server's "always confirm" flag, and then ``decide()`` never
        answers ``mcp_read_only``. *tools* are the MCP
        tools of the run, so the gate can find the server of a function.
        *member_present* is False for a run with no member, and then a tool
        that needs a card is refused.

        WS-8c item 27: a loader of progressive disclosure passes with no
        card, when it is a loader of a tool of this run
        (:func:`_pass_or_refuse_loader`)."""

        def __init__(self, tools: list[Any] | None = None,
                     member_present: bool = False) -> None:
            self.tools = list(tools or [])
            self.member_present = bool(member_present)

        def owner_of(self, function: Any) -> tuple[str, str, ToolMeta | None]:
            """The server name, the remote tool name and its hints (:func:`_owner_of`)."""
            return _owner_of(self.tools, function)

        async def process(self, context: Any, call_next: Callable[[], Any]) -> None:
            from acb_skills.egress import _from_mcp
            from acb_skills.permission_policy import MCP_READ_ONLY
            from acb_skills.tool_guard import tool_name

            from orchestrator._tool_injection import _permission_gate

            function = getattr(context, "function", None)
            if function is None or not _from_mcp(function) or is_mcp_loader(function):
                # Not an MCP function, or a loader (WS-8c item 27).
                await _pass_or_refuse_loader(self.tools, context, function, call_next)
                return
            name = tool_name(function)
            arguments = _arguments(context)
            if arguments is None:
                _log.info("mcp.confirm", tool=name, outcome="unreadable_arguments")
                context.result = UNREADABLE_ARGUMENTS_TEXT.format(tool=_shown_name(name))
                return
            server, remote, meta = self.owner_of(function)
            # H-300: the admin's switch on the server. The owner holds it.
            always = server_always_confirms(_owner_tool(self.tools, function))
            # WS-17 EM-G11d round 3: a chat session that read an untrusted
            # agent's answer asks a card for each MCP call. The read of the
            # mark fails closed.
            from acb_skills.egress import session_is_marked

            allowed, reason = _permission_gate(
                name, arguments, surface="mcp_tool",
                facts={"mcp_tool": True,
                       "mcp_annotations": meta.hints if meta is not None else None,
                       "mcp_always_confirm": always},
                after_untrusted_answer=await session_is_marked(),
            )
            if not allowed:
                context.result = f"[blocked by permission policy: {reason}]"
                return
            if reason == MCP_READ_ONLY:
                await call_next()
                return
            refusal = _card_refusal(self.member_present)
            if refusal is not None:
                text, code = refusal
                _log.info("mcp.confirm", server=server, tool=name, outcome=code)
                context.result = text.format(tool=name)
                return
            try:
                card = card_text(server, remote, arguments, meta,
                                 str(getattr(function, "description", "") or ""),
                                 local=name, always_confirm=always,
                                 acts_as=server_acts_as(_owner_tool(self.tools, function)))
            except Exception:  # CardTooLong, or a fault: no whole card, no call
                _log.info("mcp.confirm", server=server, tool=name, outcome="card_too_long")
                context.result = CARD_TOO_LONG_TEXT.format(tool=name)
                return
            if not await self._confirmed(card):
                _log.info("mcp.confirm", server=server, tool=name, outcome="denied")
                context.result = DENIED_TEXT.format(tool=name)
                return
            _log.info("mcp.confirm", server=server, tool=name, outcome="approved")
            await call_next()

        @staticmethod
        async def _confirmed(card: tuple[str, str, str]) -> bool:
            """True only when the member approved the card. A fault is a no."""
            from acb_skills.ask_tools import request_confirmation

            try:
                return await request_confirmation(*card) is True
            except asyncio.CancelledError:
                raise
            except Exception:
                return False

    return _McpPermissionGate


McpPermissionGate: type = _make_gate_class()


__all__ = [
    "ALL_ORGS",
    "CARD_TOO_LONG_TEXT",
    "DENIED_TEXT",
    "DIRECT_TOOLS_MAX",
    "LOADER_REFUSED_TEXT",
    "MAX_SERVERS",
    "NO_CARD_CHANNEL_TEXT",
    "NO_MEMBER_TEXT",
    "REDACTED",
    "RUN_SCHEMA_TOKENS",
    "SERVER_SCHEMA_TOKENS",
    "UNREADABLE_ARGUMENTS_TEXT",
    "McpPermissionGate",
    "McpServer",
    "ToolMeta",
    "acts_as_line",
    "attach_mcp_tools",
    "card_text",
    "close_mcp_tools",
    "is_mcp_loader",
    "is_native_maf_agent",
    "maf_mcp_org_allowed",
    "maf_mcp_orgs",
    "mcp_index",
    "mcp_service",
    "redact_arguments",
    "schema_tokens",
    "server_acts_as",
    "server_always_confirms",
    "tool_hints",
    "tool_meta",
]
