"""The ``tools/list`` read of the MCP servers of one org (WS-STU STU-B1b-2).

Spec: ``project-docs/specs/ai_studio.md`` §3.2 source 3 and §14 B1b-2. The
gateway owns this client. It never imports ``orchestrator/_maf_mcp.py``,
because a service never imports another service.

**Two paths, and only one of them calls a server.**

* :func:`refresh` is the member action of ``POST /studio/mcp/{server}/refresh``.
  It calls :func:`list_tools` for ONE server of the caller's org and writes
  the cleaned list to the cache. A minimum interval of
  :data:`REFRESH_INTERVAL_S` holds for each org and server.
* :func:`catalog_tools` is the read of ``GET /studio/actions``. It reads the
  cache and the server list of the org (``mcp.enabled_meta``), and it NEVER
  calls a server. A cold cache gives no row and ``stale: true``.

**The one network path** is :func:`_sdk_list`. It takes the client of
``outbound_guard.mcp_client`` (the SSRF guard, the address pin, one origin,
no redirect). The headers come from ``mcp.servers_for_org``, which decrypts
the stored blob and refreshes an OAuth token inside its own seam. This
module stores no credential and writes no credential. It gives the headers
to the pinned client as an argument, and never logs them or returns them.

**The caps.** :data:`READ_TIMEOUT_S` covers the connect and every page. The
registry read caps its own OAuth refresh. :data:`MAX_PAGES` pages at most. The
cache keeps :data:`MAX_TOOLS` tools at most, and ``truncated`` says that
the server listed more.

**Each tool is data** (:func:`clean_tools`). A name that the ``uses``
grammar refuses is left out, so a name with a dot or with more than 64
characters gives no row (fail closed, trap 4). A description goes through
``acb_skills.hidden_text.strip_hidden``, so a lone surrogate or a hidden
character never reaches the cache or a row (trap 1). Annotations of the SDK
come as an object, and this seam makes a mapping of them (trap 3). A
duplicate name takes the stricter class through
``gateway.studio.mcp_actions.unique_tools`` (trap 2).

**The cache** is ``acb_common.tenant_redis``, namespace :data:`NAMESPACE`,
under ``organization_scope(org)``. The key builder takes the org from that
scope, so a key of org A cannot be built or read for org B (R5). One key
holds the list of one server, with a TTL of :data:`CACHE_TTL_S`. One hash
(``index``) names the servers that hold a list. A catalog read of an org
with an empty index opens no database session.

Fences: ``tests/unit/test_studio_mcp_tools.py`` and
``tests/unit/test_studio_mcp_tools_r8.py`` (R8).
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from acb_common import get_logger
from acb_common.studio_manifest import MCP_TOOL_NAME
from acb_skills.hidden_text import strip_hidden
from acb_skills.permission_policy import MCP_HINT_NAMES

from gateway.connections import mcp as mcp_registry
from gateway.studio import mcp_actions

_log = get_logger("connections.mcp_tools")

#: The namespace of each cache key: ``cc:<org>:studio-mcp:...``.
NAMESPACE = "studio-mcp"
#: The seconds that one cached list lives.
CACHE_TTL_S = 300
#: The fewest seconds between two refreshes of one server of one org.
REFRESH_INTERVAL_S = 30
#: The seconds for the whole read of one server.
READ_TIMEOUT_S = 20.0
#: The most ``tools/list`` pages that one read follows.
MAX_PAGES = 5
#: The most tools that the cache keeps for one server.
MAX_TOOLS = 200
#: The longest description that the cache keeps.
DESCRIPTION_CHARS = mcp_actions.DESCRIPTION_CHARS
#: The version of the shape of a cached value. A value of another version
#: reads as cold.
CACHE_VERSION = 1

_TOOL_NAME = re.compile(MCP_TOOL_NAME)

#: The reason codes of a failed read. A reason never holds the text of an
#: exception, because that text can hold a URL or a header.
REASON_TIMEOUT = "timeout"
REASON_REFUSED = "refused"
REASON_UNREACHABLE = "unreachable"


class ServerNotFound(LookupError):
    """The org holds no connected server of that name."""


class ListFailed(Exception):
    """The server gave no list. ``reason`` is one of the ``REASON_*`` codes."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class RefreshTooSoon(Exception):
    """A refresh of the same server ran less than the interval ago."""

    def __init__(self, retry_after: int) -> None:
        super().__init__(str(retry_after))
        self.retry_after = retry_after


#: The type of the function that reads the raw items of one server. A test
#: gives a fake one. The production function is :func:`_sdk_list`.
Lister = Callable[[Any], Awaitable[Sequence[Any]]]


@dataclass(frozen=True)
class ToolList:
    """The cleaned list of one server."""

    tools: list[dict[str, Any]]
    truncated: bool


@dataclass(frozen=True)
class Refreshed:
    """What a refresh gives the route. It holds no url and no header."""

    server: str
    ok: bool
    tools: int = 0
    truncated: bool = False
    reason: str | None = None


# ── The cleaning of one list ────────────────────────────────────────────────


def _field(item: Any, name: str) -> Any:
    if isinstance(item, Mapping):
        return item.get(name)
    return getattr(item, name, None)


def _annotations(raw: Any) -> dict[str, bool] | None:
    """The hints of one tool as a plain mapping, or None.

    An SDK client gives a ``ToolAnnotations`` object, and a JSON client gives
    a mapping. Both become a dict of the real booleans of
    ``MCP_HINT_NAMES``, so ``mcp_actions.tool_hints`` reads the same hints
    as the gate does (trap 3)."""
    if raw is None:
        return None
    if isinstance(raw, Mapping):
        found = {n: raw.get(n) for n in MCP_HINT_NAMES}
    else:
        found = {n: getattr(raw, n, None) for n in MCP_HINT_NAMES}
    hints = {n: v for n, v in found.items() if isinstance(v, bool)}
    return hints or None


def _description(raw: Any) -> str:
    """The description as clean, capped text.

    The strip runs before and after the cut, because a cut can leave a
    joiner at the end of the text, where it is hidden."""
    if not isinstance(raw, str):
        return ""
    return strip_hidden(strip_hidden(raw)[:DESCRIPTION_CHARS])


def clean_tools(items: Iterable[Any]) -> ToolList:
    """The cleaned list of one server: one mapping for each tool.

    Each mapping holds ``name``, ``description`` and ``annotations``, and
    nothing else. A name that the ``uses`` grammar refuses is left out.
    Two items of one name give one, with the stricter class. The list keeps
    :data:`MAX_TOOLS` items at most."""
    cleaned: list[dict[str, Any]] = []
    for item in items:
        name = _field(item, "name")
        if not isinstance(name, str) or not _TOOL_NAME.fullmatch(name):
            continue
        cleaned.append({
            "name": name,
            "description": _description(_field(item, "description")),
            "annotations": _annotations(_field(item, "annotations")),
        })
    unique = [dict(t) for t in mcp_actions.unique_tools(cleaned)]
    return ToolList(tools=unique[:MAX_TOOLS], truncated=len(unique) > MAX_TOOLS)


# ── The read of one server ──────────────────────────────────────────────────


async def _sdk_list(server: Any) -> Sequence[Any]:
    """The raw ``tools/list`` items of ONE server, through the pinned client.

    The only function here that sends a request. ``outbound_guard`` refuses
    an address that is not public, another origin and each redirect."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    from gateway import outbound_guard

    client = await outbound_guard.mcp_client(server.url)
    try:
        # The pinned transport sends to the one origin of the URL only, so
        # the headers of the server can never reach another host.
        client.headers.update(dict(server.headers))
        async with streamable_http_client(server.url, http_client=client) as streams:
            read, write = streams[0], streams[1]
            async with ClientSession(read, write) as session:
                await session.initialize()
                items: list[Any] = []
                cursor: str | None = None
                for _ in range(MAX_PAGES):
                    page = await session.list_tools(cursor)
                    items.extend(getattr(page, "tools", None) or [])
                    cursor = getattr(page, "nextCursor", None)
                    if not cursor:
                        break
                return items
    finally:
        await client.aclose()


def _reason(exc: BaseException) -> str:
    from gateway import outbound_guard

    if isinstance(exc, TimeoutError):
        return REASON_TIMEOUT
    if isinstance(exc, outbound_guard.OutboundRefused):
        return REASON_REFUSED
    return REASON_UNREACHABLE


async def list_tools(org: str, server: str, *, lister: Lister | None = None) -> ToolList:
    """One ``tools/list`` read of the server *server* of *org*, cleaned.

    *org* comes from the session of the caller, never from request input
    (R5). The server must be a connected server of that org, else
    :class:`ServerNotFound`. A failure or a timeout raises
    :class:`ListFailed` with a reason code."""
    if not isinstance(server, str) or not mcp_registry.NAME_RE.match(server):
        raise ServerNotFound(server)
    # The registry seam caps its own OAuth refresh (``mcp.REFRESH_BUDGET_S``),
    # so the timeout below does not cancel a refresh under its row lock.
    found = await mcp_registry.servers_for_org(org, oauth_wanted=lambda s: s.name == server)
    stored = next((s for s in found if s.name == server), None)
    if stored is None:
        raise ServerNotFound(server)
    try:
        async with asyncio.timeout(READ_TIMEOUT_S):
            raw = await (lister or _sdk_list)(stored)
    except Exception as exc:
        reason = _reason(exc)
        _log.warning("studio.mcp_tools.failed", name=server, reason=reason,
                     kind=type(exc).__name__)
        raise ListFailed(reason) from None
    return clean_tools(raw)


# ── The cache ───────────────────────────────────────────────────────────────


def _redis() -> Any:
    from acb_common import tenant_redis

    return tenant_redis.get_tenant_redis()


def _scope(org: str) -> Any:
    from acb_common import tenant_redis

    return tenant_redis.organization_scope(org)


def _key(*parts: str) -> Any:
    from acb_common import tenant_redis

    return tenant_redis.key(NAMESPACE, *parts)


def _decode(raw: Any) -> list[dict[str, Any]] | None:
    """The tools of one cached value, or None for a value that is not ours."""
    if not isinstance(raw, str | bytes):
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(value, dict) or value.get("v") != CACHE_VERSION:
        return None
    tools = value.get("tools")
    if not isinstance(tools, list):
        return None
    return [t for t in tools if isinstance(t, dict)]


async def _store(org: str, server: str, listed: ToolList) -> None:
    value = json.dumps({
        "v": CACHE_VERSION,
        "tools": listed.tools,
        "truncated": listed.truncated,
        "read_at": datetime.now(UTC).isoformat(),
    })
    client = _redis()
    with _scope(org):
        await client.setex(_key("tools", server), CACHE_TTL_S, value)
        index = _key("index")
        await client.hset(index, server, "1")
        await client.expire(index, CACHE_TTL_S)


async def _forget(org: str, server: str) -> None:
    """Drop the list of ONE server. The lists of the other servers stay."""
    client = _redis()
    with _scope(org):
        await client.delete(_key("tools", server))
        await client.hdel(_key("index"), server)


async def _claim_refresh(org: str, server: str) -> None:
    """Take the refresh slot of one server, or raise :class:`RefreshTooSoon`."""
    client = _redis()
    with _scope(org):
        slot = _key("refresh", server)
        if await client.set(slot, "1", nx=True, ex=REFRESH_INTERVAL_S):
            return
        left = int(await client.ttl(slot))
    raise RefreshTooSoon(left if left > 0 else REFRESH_INTERVAL_S)


async def refresh(org: str, server: str, *, lister: Lister | None = None) -> Refreshed:
    """Read ONE server of *org* and fill its cache (the member action).

    The slot comes first, so a name that the org does not hold costs a slot
    too. A failed read drops the list of that server only, so the catalog
    shows no stale row of it. ``ServerNotFound`` and ``RefreshTooSoon``
    reach the caller. A Redis fault reaches the caller too."""
    if not isinstance(server, str) or not mcp_registry.NAME_RE.match(server):
        raise ServerNotFound(server)
    await _claim_refresh(org, server)
    try:
        listed = await list_tools(org, server, lister=lister)
    except ListFailed as failed:
        await _forget(org, server)
        return Refreshed(server=server, ok=False, reason=failed.reason)
    await _store(org, server, listed)
    return Refreshed(server=server, ok=True, tools=len(listed.tools),
                     truncated=listed.truncated)


async def catalog_tools(org: str) -> tuple[list[dict[str, Any]], bool]:
    """The ``mcp`` rows of the catalog of *org*, and ``stale``.

    It reads the cache and ``mcp.enabled_meta``, and NEVER a server.
    ``stale`` is true when no server of the org has a cached list, or when
    a connected server has none. A Redis or database fault gives no row and
    ``stale: true``, because the catalog must still answer."""
    try:
        client = _redis()
        with _scope(org):
            cached = await client.hgetall(_key("index"))
        if not cached:
            return [], True
        servers = await mcp_registry.enabled_meta(org)
        names = [s.name for s in servers]
        with _scope(org):
            values = await client.mget([_key("tools", n) for n in names])
    except Exception as exc:
        _log.warning("studio.mcp_tools.cache_unavailable", kind=type(exc).__name__)
        return [], True
    tools: dict[str, list[dict[str, Any]]] = {}
    for name, raw in zip(names, values, strict=True):
        decoded = _decode(raw)
        if decoded is not None:
            tools[name] = decoded
    stale = len(tools) < len(names)
    return mcp_actions.mcp_rows(servers, tools), stale


__all__ = [
    "CACHE_TTL_S",
    "MAX_PAGES",
    "MAX_TOOLS",
    "NAMESPACE",
    "READ_TIMEOUT_S",
    "REFRESH_INTERVAL_S",
    "ListFailed",
    "RefreshTooSoon",
    "Refreshed",
    "ServerNotFound",
    "ToolList",
    "catalog_tools",
    "clean_tools",
    "list_tools",
    "refresh",
]
