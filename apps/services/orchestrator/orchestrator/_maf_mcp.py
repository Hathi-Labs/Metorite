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
7. **No stdio.** Nothing here builds an ``MCPStdioTool`` (D85). Fence:
   ``tests/unit/test_no_mcp_stdio.py``.

Fences: ``tests/unit/test_maf_mcp_injection.py``,
``tests/unit/test_maf_mcp_org_scope_r8.py`` and
``tests/unit/test_maf_mcp_confirm.py`` (H-299).
"""
from __future__ import annotations

import asyncio
import contextlib
import functools
import itertools
import json
import re
import unicodedata
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
#: The MCP hint names, from the ``annotations`` of one tool (MCP 2025-03-26).
HINT_NAMES = ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint")
#: The longest server description that a card carries.
_DESCRIPTION_CHARS = 1000


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
        async with asyncio.timeout(0.5):
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
    """
    from agent_framework import MCPStreamableHTTPTool

    class RunScopedMCPTool(MCPStreamableHTTPTool):
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
    return {n for n in held if n}


async def attach_mcp_tools(
    agent: Any, servers: list[McpServer], opened: Opened,
    *, member_present: bool = False,
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
    default, refuses each tool that needs a card."""
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
        _log_tool_hints(tool)

    if not tools:
        return []
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
    _log.info("mcp.attached", servers=names)
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


def _hint_line(hints: dict[str, bool] | None) -> str:
    if not hints:
        return "The server sent no hints for this tool."
    if hints.get("destructiveHint") is True:
        return "The server marks this tool as destructive."
    return "The server does not mark this tool as read-only."


def card_text(server: str, tool: str, arguments: dict[str, Any],
              meta: ToolMeta | None, fallback_description: str = "",
              local: str = "") -> tuple[str, str, str]:
    """The title, detail and body of the card for one MCP call.

    The title names the LOCAL tool name of MAF (*local*), in
    ``[A-Za-z0-9_.-]`` only. The detail starts with the real hint line, and
    only then names the server and the remote tool, each through
    :func:`_shown_name`. So a tool name cannot spoof the hint line. The body
    shows EVERY argument, with :func:`redact_arguments`, then the lines that
    name each hidden value, and then the server's description under
    :data:`DESCRIPTION_LABEL`. Only the description is cut to fit.
    Arguments that do not fit raise :class:`CardTooLong`, because a member
    must see the whole call that Approve runs."""
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
    detail = (f"{_hint_line(meta.hints if meta else None)} "
              f"Server: {shown_server} · Tool: {shown_tool}")
    return title, detail, head + description


def _card_refusal(member_present: bool) -> tuple[str, str] | None:
    """Why this run cannot show a card, as ``(text, code)``, or None.

    WhatsApp first: its profile parks only the acts of
    ``whatsapp_acts.ALLOWED_ACTS``, and an MCP tool is not one, so it is
    refused and never approved. Then a run with no member, and a run with no
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


def _owner_of(tools: list[Any], function: Any) -> tuple[str, str, ToolMeta | None]:
    """The server name, the remote tool name and its hints, for *function*.

    The same object first. Else a name that exactly ONE tool holds. Else an
    unknown server with no hints, which needs the card."""
    name = str(getattr(function, "name", "") or "")
    props = getattr(function, "additional_properties", None) or {}
    remote = str(props.get("_mcp_remote_name") or name)
    same = [t for t in tools if any(f is function for f in _tool_functions(t))]
    if not same:
        same = [t for t in tools
                if any(getattr(f, "name", None) == name for f in _tool_functions(t))]
    if len(same) != 1:
        return "(unknown server)", remote, None
    owner = same[0]
    return str(getattr(owner, "name", "") or ""), remote, tool_meta(owner, remote)


def _make_gate_class() -> type:
    from agent_framework import FunctionMiddleware

    class _McpPermissionGate(FunctionMiddleware):
        """Runs ``permission_policy.decide()`` for each MCP function call.

        A call that the policy refuses in ``enforce`` mode never reaches the
        server. Other functions pass, because their own wrapper gates them.

        H-299: ``decide()`` gets the server's hints, and every code but
        ``mcp_read_only`` needs the member's card before the call reaches
        the server (``ask_tools.request_confirmation``). *tools* are the MCP
        tools of the run, so the gate can find the server of a function.
        *member_present* is False for a run with no member, and then a tool
        that needs a card is refused."""

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
            if function is None or not _from_mcp(function):
                await call_next()
                return
            name = tool_name(function)
            arguments = _arguments(context)
            if arguments is None:
                _log.info("mcp.confirm", tool=name, outcome="unreadable_arguments")
                context.result = UNREADABLE_ARGUMENTS_TEXT.format(tool=_shown_name(name))
                return
            server, remote, meta = self.owner_of(function)
            allowed, reason = _permission_gate(
                name, arguments, surface="mcp_tool",
                facts={"mcp_tool": True,
                       "mcp_annotations": meta.hints if meta is not None else None},
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
                                 local=name)
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
    "NO_CARD_CHANNEL_TEXT",
    "NO_MEMBER_TEXT",
    "REDACTED",
    "UNREADABLE_ARGUMENTS_TEXT",
    "McpPermissionGate",
    "McpServer",
    "ToolMeta",
    "attach_mcp_tools",
    "card_text",
    "close_mcp_tools",
    "is_native_maf_agent",
    "maf_mcp_org_allowed",
    "maf_mcp_orgs",
    "mcp_service",
    "redact_arguments",
    "tool_hints",
    "tool_meta",
]
