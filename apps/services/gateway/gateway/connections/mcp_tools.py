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
  calls a server. It serves the last good list of each server, and
  ``stale: true`` says that a list is older than :data:`FRESH_S` or missing.

**The one network path** is :func:`_sdk_list`. It takes the client of
``outbound_guard.mcp_client`` (the SSRF guard, the address pin, one origin,
no redirect). The headers come from ``mcp.servers_for_org``, which decrypts
the stored blob and refreshes an OAuth token inside its own seam. This
module stores no credential and writes no credential. It gives the headers
to the pinned client as an argument, and never logs them or returns them.

**The caps.** ONE deadline, :data:`READ_TIMEOUT_S`, covers the connect and
every page together. The registry read caps its own OAuth refresh.
:data:`MAX_PAGES` pages at most. An ``outbound_guard.AnswerBudget`` caps the
bytes of each answer (:data:`MAX_ANSWER_BYTES`) and of the whole read
(:data:`MAX_READ_BYTES`), while the answer is read. A cap that fires ends
the read at once with the code ``too_large``. The read stops at
:data:`MAX_LISTED` raw items. The cache keeps :data:`MAX_TOOLS` tools at
most, and ``truncated`` says that the server listed more.

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
holds the last good list of one server, with ``fetched_at`` in the value
and a TTL of :data:`LIST_TTL_S`. So the catalog and each caller of
``catalog_rows`` keep the rows between two refreshes. One hash (``index``)
names the servers that hold a list. A catalog read of an org with an empty
index opens no database session.

**The slot token.** A refresh writes a new token as the value of its slot
key. A failed read drops the list only while the slot still holds that
token, so a late failure never drops the list of a newer refresh.

Fences: ``tests/unit/test_studio_mcp_tools.py`` and
``tests/unit/test_studio_mcp_tools_r8.py`` (R8).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import time
import uuid
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from itertools import islice
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
#: The age in seconds past which a cached list is stale. A stale list is
#: still served.
FRESH_S = 300
#: The seconds that the last good list of one server lives in the cache.
LIST_TTL_S = 7 * 24 * 3600
#: The fewest seconds between two refreshes of one server of one org.
REFRESH_INTERVAL_S = 30
#: The seconds for the whole read of one server.
READ_TIMEOUT_S = 20.0
#: The most ``tools/list`` pages that one read follows.
MAX_PAGES = 5
#: The most tools that the cache keeps for one server.
MAX_TOOLS = 200
#: The most raw items that one read keeps. The read stops paging there.
MAX_LISTED = 1000
#: The most bytes of one answer of the server.
MAX_ANSWER_BYTES = 1024 * 1024
#: The most bytes of all the answers of one read.
MAX_READ_BYTES = 4 * 1024 * 1024
#: The longest description that the cache keeps.
DESCRIPTION_CHARS = mcp_actions.DESCRIPTION_CHARS
#: The version of the shape of a cached value. A value of another version
#: reads as cold.
CACHE_VERSION = 2

_TOOL_NAME = re.compile(MCP_TOOL_NAME)

#: The reason codes of a failed read. A reason never holds the text of an
#: exception, because that text can hold a URL or a header.
REASON_TIMEOUT = "timeout"
REASON_REFUSED = "refused"
REASON_UNREACHABLE = "unreachable"
REASON_TOO_LARGE = "too_large"


def _now() -> float:
    """The wall clock in seconds. A test replaces it."""
    return time.time()


def _server_key(server: Any) -> bool:
    """True for a server key that the registry takes. ``fullmatch``, so a
    trailing new line is refused too."""
    return isinstance(server, str) and mcp_registry.NAME_RE.fullmatch(server) is not None


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
    :data:`MAX_TOOLS` items at most. It reads :data:`MAX_LISTED` raw items
    at most, and more than that sets ``truncated``."""
    raw = list(islice(items, MAX_LISTED + 1))
    cut = len(raw) > MAX_LISTED
    cleaned: list[dict[str, Any]] = []
    for item in raw[:MAX_LISTED]:
        name = _field(item, "name")
        if not isinstance(name, str) or not _TOOL_NAME.fullmatch(name):
            continue
        cleaned.append({
            "name": name,
            "description": _description(_field(item, "description")),
            "annotations": _annotations(_field(item, "annotations")),
        })
    unique = [dict(t) for t in mcp_actions.unique_tools(cleaned)]
    return ToolList(tools=unique[:MAX_TOOLS], truncated=cut or len(unique) > MAX_TOOLS)


# ── The read of one server ──────────────────────────────────────────────────


async def _pages(client: Any, url: str) -> list[Any]:
    """The ``tools/list`` pages of one session, at most :data:`MAX_PAGES`
    pages and :data:`MAX_LISTED` + 1 items."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async with streamable_http_client(url, http_client=client) as streams:
        read, write = streams[0], streams[1]
        async with ClientSession(read, write) as session:
            await session.initialize()
            items: list[Any] = []
            cursor: str | None = None
            for _ in range(MAX_PAGES):
                page = await session.list_tools(cursor)
                room = MAX_LISTED + 1 - len(items)
                items.extend(islice(getattr(page, "tools", None) or [], room))
                cursor = getattr(page, "nextCursor", None)
                if not cursor or len(items) > MAX_LISTED:
                    break
            return items


async def _sdk_list(server: Any) -> Sequence[Any]:
    """The raw ``tools/list`` items of ONE server, through the pinned client.

    The only function here that sends a request. ``outbound_guard`` refuses
    an address that is not public, another origin and each redirect. The
    budget caps the bytes while they are read. The SDK can swallow the
    error of its own reader, so the read also waits on ``budget.tripped``
    and stops the session at once when a cap fires."""
    from gateway import outbound_guard

    budget = outbound_guard.AnswerBudget(per_answer=MAX_ANSWER_BYTES, total=MAX_READ_BYTES)
    client = await outbound_guard.mcp_client(server.url, budget=budget)
    try:
        # The pinned transport sends to the one origin of the URL only, so
        # the headers of the server can never reach another host.
        client.headers.update(dict(server.headers))
        work = asyncio.ensure_future(_pages(client, server.url))
        trip = asyncio.ensure_future(budget.tripped.wait())
        try:
            await asyncio.wait({work, trip}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (work, trip):
                if not task.done():
                    task.cancel()
                    with contextlib.suppress(BaseException):
                        await task
        if budget.tripped.is_set():
            raise outbound_guard.AnswerTooLarge(_host(server.url), "too large")
        return work.result()
    finally:
        await client.aclose()


def _host(url: str) -> str:
    from urllib.parse import urlsplit

    try:
        return urlsplit(url).hostname or ""
    except ValueError:
        return ""


def _causes(exc: BaseException) -> list[BaseException]:
    """*exc*, each exception of its groups and each cause and context, once."""
    seen: list[BaseException] = []
    stack = [exc]
    while stack and len(seen) < 64:
        cur = stack.pop()
        if cur is None or any(cur is s for s in seen):
            continue
        seen.append(cur)
        if isinstance(cur, BaseExceptionGroup):
            stack.extend(cur.exceptions)
        stack.extend((cur.__cause__, cur.__context__))
    return seen


def _reason(exc: BaseException) -> str:
    """The reason code of a failed read. An exception inside the task group
    of the SDK comes back in a group or as a cause, so each one is read."""
    from gateway import outbound_guard

    found = _causes(exc)
    if any(isinstance(e, outbound_guard.AnswerTooLarge) for e in found):
        return REASON_TOO_LARGE
    if any(isinstance(e, outbound_guard.OutboundRefused) for e in found):
        return REASON_REFUSED
    if any(isinstance(e, TimeoutError) for e in found):
        return REASON_TIMEOUT
    return REASON_UNREACHABLE


async def list_tools(org: str, server: str, *, lister: Lister | None = None) -> ToolList:
    """One ``tools/list`` read of the server *server* of *org*, cleaned.

    *org* comes from the session of the caller, never from request input
    (R5). The server must be a connected server of that org, else
    :class:`ServerNotFound`. A failure or a timeout raises
    :class:`ListFailed` with a reason code."""
    if not _server_key(server):
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


def _decode(raw: Any) -> tuple[list[dict[str, Any]], float] | None:
    """The tools and ``fetched_at`` of one cached value, or None for a value
    that is not ours."""
    if not isinstance(raw, str | bytes):
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(value, dict) or value.get("v") != CACHE_VERSION:
        return None
    tools = value.get("tools")
    fetched = value.get("fetched_at")
    if not isinstance(tools, list) or isinstance(fetched, bool) or not isinstance(
            fetched, int | float):
        return None
    return [t for t in tools if isinstance(t, dict)], float(fetched)


async def _store(org: str, server: str, listed: ToolList) -> None:
    value = json.dumps({
        "v": CACHE_VERSION,
        "tools": listed.tools,
        "truncated": listed.truncated,
        "fetched_at": _now(),
    })
    client = _redis()
    with _scope(org):
        await client.setex(_key("tools", server), LIST_TTL_S, value)
        index = _key("index")
        await client.hset(index, server, "1")
        await client.expire(index, LIST_TTL_S)


async def _forget(org: str, server: str, token: str) -> bool:
    """Drop the list of ONE server, only while its slot holds *token*.

    A slot that holds another token belongs to a newer refresh, so its list
    stays. The lists of the other servers always stay."""
    client = _redis()
    with _scope(org):
        if await client.get(_key("refresh", server)) != token:
            return False
        await client.delete(_key("tools", server))
        await client.hdel(_key("index"), server)
    return True


async def _claim_refresh(org: str, server: str) -> str:
    """Take the refresh slot of one server and give its token, or raise
    :class:`RefreshTooSoon`."""
    client = _redis()
    token = uuid.uuid4().hex
    with _scope(org):
        slot = _key("refresh", server)
        if await client.set(slot, token, nx=True, ex=REFRESH_INTERVAL_S):
            return token
        left = int(await client.ttl(slot))
    raise RefreshTooSoon(left if left > 0 else REFRESH_INTERVAL_S)


async def refresh(org: str, server: str, *, lister: Lister | None = None) -> Refreshed:
    """Read ONE server of *org* and fill its cache (the member action).

    The slot comes first, so a name that the org does not hold costs a slot
    too. A failed read drops the list of that server only, and only while
    the slot holds the token of this refresh. ``ServerNotFound`` and
    ``RefreshTooSoon`` reach the caller. A Redis fault reaches the caller too."""
    if not _server_key(server):
        raise ServerNotFound(server)
    token = await _claim_refresh(org, server)
    try:
        listed = await list_tools(org, server, lister=lister)
    except ListFailed as failed:
        await _forget(org, server, token)
        return Refreshed(server=server, ok=False, reason=failed.reason)
    await _store(org, server, listed)
    return Refreshed(server=server, ok=True, tools=len(listed.tools),
                     truncated=listed.truncated)


async def catalog_tools(org: str) -> tuple[list[dict[str, Any]], bool]:
    """The ``mcp`` rows of the catalog of *org*, and ``stale``.

    It reads the cache and ``mcp.enabled_meta``, and NEVER a server. It
    serves the last good list of each connected server, at any age up to
    :data:`LIST_TTL_S`. ``stale`` is true when the index is empty, when a
    connected server has no list, or when a list is older than
    :data:`FRESH_S`. A Redis or database fault gives no row and
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
    now = _now()
    stale = False
    for name, raw in zip(names, values, strict=True):
        decoded = _decode(raw)
        if decoded is None:
            stale = True
            continue
        tools[name], fetched = decoded
        if now - fetched > FRESH_S:
            stale = True
    return mcp_actions.mcp_rows(servers, tools), stale


__all__ = [
    "FRESH_S",
    "LIST_TTL_S",
    "MAX_ANSWER_BYTES",
    "MAX_LISTED",
    "MAX_PAGES",
    "MAX_READ_BYTES",
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
