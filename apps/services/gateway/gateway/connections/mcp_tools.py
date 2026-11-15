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

**A failed read.** A refresh notes its start time when it takes its slot. A
failed read drops the stored list unless that list is NEWER than the start.
So a late failure keeps the list of a newer refresh, and it drops an older
list also when the slot has expired.

**The server identity.** Each cached list holds a hash of the url, the
``connected_by`` and the ``created_at`` of the server (``_identity``, I11a).
``enabled_meta`` gives the three with no secret. The catalog serves a list
only when its hash equals the hash of the server of NOW, so a new server
with an old name never gets the tools of the old one.

**The live read of a preview (STU-I11a).** :func:`call_read_tool` is the
one path of the action door that calls a tool, for a class A ``mcp:`` action
in a preview only. It never takes the class from the catalog. It reads the
list through :func:`fresh_tool_list`, the ONE helper that holds both checks:
younger than :data:`FRESH_S`, and the identity of the server of now. Else it
reads a live ``tools/list`` first. It computes the class from THAT list,
calls the gate seam ``mcp_needs_confirmation``, and refuses unless both say
class A. Then ONE ``tools/call`` (:func:`_sdk_call`) goes through the same
pinned client and budget as the list. The answer keeps :data:`MAX_ROWS`
items of each array, each text goes through ``strip_hidden``, and a cut
sets ``truncated``. An answer with ``isError`` is an error, never data. The
answer cache is ``tenant_redis``, namespace :data:`LIVE_NAMESPACE`, TTL
:data:`ANSWER_TTL_S`. Its key holds the org (the scope), a hash of the
member, the server, the tool and a hash of the input with the server
identity. Its value holds the cut, cleaned answer only.

The live read first needs the grant ``integrations:use:mcp-<server>`` of the
session member, as the agent run does, and a server whose ``agent_scope``
holds ``*``. Each read that calls the network passes a rate limit
(:data:`MEMBER_CALLS_PER_WINDOW`, :data:`ORG_CALLS_PER_WINDOW`) and one of
:data:`LIVE_PER_ORG` slots. A cache hit passes no limit.

**A changed tool (STU-B1b-3).** An admin approves the text of a tool with
:func:`approve_tool`. The approval holds the hash of the CURRENT cached item
(``mcp_actions.tool_text_hash``) and needs a list that is fresh and of the
identity of now. :func:`catalog_tools` reads the approvals of the org in ONE
query, and a tool with no approval or another hash is ``changed: true`` and
class B. :func:`call_read_tool` reads them before any network step, and it
refuses a changed tool with :data:`LIVE_TOOL_CHANGED`. So a new tool, and a
tool whose text a server changed, never reads live until an admin approves
it. A failed read of the approvals makes each tool changed (fail closed).

Fences: ``tests/unit/test_studio_mcp_tools.py``,
``tests/unit/test_studio_mcp_tools_r8.py`` (R8),
``tests/unit/test_studio_live_preview.py``,
``tests/unit/test_studio_live_preview_r8.py`` (R8),
``tests/unit/test_studio_mcp_approvals.py`` and
``tests/unit/test_studio_mcp_approvals_r8.py`` (R8).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from itertools import islice
from typing import Any

from acb_common import get_logger
from acb_common.studio_manifest import MCP_TOOL_NAME
from acb_skills.hidden_text import strip_hidden
from acb_skills.permission_policy import MCP_HINT_NAMES, mcp_needs_confirmation

from gateway.connections import mcp as mcp_registry
from gateway.studio import mcp_actions, mcp_approvals

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
CACHE_VERSION = 3

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
    #: The hash of the server that gave the list (``_identity``).
    identity: str = ""


def _identity(server: Any) -> str:
    """The hash of the connection identity of one server: its url, its
    ``connected_by`` and its ``created_at``.

    None of the three is a secret, and ``enabled_meta`` gives them with no
    blob opened. A new server with an old name gets another hash: another
    url, and also the same url that another admin or a new row connected
    (STU-I11a, the rule of the B1b-2 review)."""
    created = getattr(server, "created_at", None)
    parts = [str(getattr(server, "url", "") or ""), str(getattr(server, "connected_by", "") or ""),
             created.isoformat() if hasattr(created, "isoformat") else str(created or "")]
    text = json.dumps(parts, ensure_ascii=True)
    return hashlib.sha256(text.encode("ascii")).hexdigest()[:32]


def _answer_identity(server: Any) -> str:
    """:func:`_identity` and the ``updated_at`` of the row, for the key of a
    live answer only.

    A re-save by the same admin with a new header or a new token keeps
    :func:`_identity`, so the answer of the old token must not serve. Each
    write of the row sets ``updated_at``. The time is not a secret, and
    ``enabled_meta`` gives it with no blob opened. It is NOT in
    :func:`_identity`: a token refresh and a status write set it too, so the
    catalog would drop the rows of an OAuth server at each refresh."""
    updated = getattr(server, "updated_at", None)
    stamp = updated.isoformat() if hasattr(updated, "isoformat") else str(updated or "")
    text = json.dumps([_identity(server), stamp], ensure_ascii=True)
    return hashlib.sha256(text.encode("ascii")).hexdigest()[:32]


def _scope_open(meta: Any) -> bool:
    """True when the ``agent_scope`` of the server holds ``*``. A preview and a
    refresh are no agent, so a server of ``[]`` or of named agents fails
    closed."""
    return "*" in tuple(getattr(meta, "agent_scope", None) or ())


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
    """The description as clean, capped text: ``mcp_actions.clean_description``,
    the one cleaning that the approved hash of B1b-3 reads too."""
    return mcp_actions.clean_description(raw)


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
    """The raw ``tools/list`` items of ONE server, through the pinned client
    (:func:`_pinned`)."""
    return await _pinned(server, _pages, total=MAX_READ_BYTES)


async def _pinned(server: Any, work_of: Callable[[Any, str], Awaitable[Any]], *,
                  total: int) -> Any:
    """Run *work_of* on ONE session of the pinned client of *server*.

    The one place here that builds a client, so :func:`_sdk_list` and
    :func:`_sdk_call` are the only functions that send a request.
    ``outbound_guard`` refuses an address that is not public, another origin
    and each redirect. The budget caps the bytes while they are read. The
    SDK can swallow the error of its own reader, so the read also waits on
    ``budget.tripped`` and stops the session at once when a cap fires."""
    from gateway import outbound_guard

    budget = outbound_guard.AnswerBudget(per_answer=MAX_ANSWER_BYTES, total=total)
    client = await outbound_guard.mcp_client(server.url, budget=budget)
    try:
        # The pinned transport sends to the one origin of the URL only, so
        # the headers of the server can never reach another host.
        client.headers.update(dict(server.headers))
        # Set AFTER the stored headers, so a stored header cannot ask for a
        # coded answer. The transport sets it on each request too.
        client.headers["Accept-Encoding"] = "identity"
        work = asyncio.ensure_future(work_of(client, server.url))
        trip = asyncio.ensure_future(budget.tripped.wait())
        try:
            await asyncio.wait({work, trip}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (work, trip):
                task.cancel()
            # ``gather`` keeps the faults of the two tasks, and a cancel of
            # this read itself still reaches the caller.
            await asyncio.gather(work, trip, return_exceptions=True)
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
    return await _list_stored(await _stored(org, server), lister)


async def _stored(org: str, server: str) -> Any:
    """The connected server *server* of *org*, with its headers, through the
    one run reader ``servers_for_org``, or :class:`ServerNotFound`."""
    found = await mcp_registry.servers_for_org(org, oauth_wanted=lambda s: s.name == server)
    stored = next((s for s in found if s.name == server), None)
    if stored is None:
        raise ServerNotFound(server)
    return stored


async def _list_stored(stored: Any, lister: Lister | None) -> ToolList:
    """One ``tools/list`` read of the server *stored*, cleaned, under the one
    deadline. A failure raises :class:`ListFailed` with a reason code."""
    server = str(stored.name)
    try:
        async with asyncio.timeout(READ_TIMEOUT_S):
            raw = await (lister or _sdk_list)(stored)
    except Exception as exc:
        reason = _reason(exc)
        _log.warning("studio.mcp_tools.failed", name=server, reason=reason,
                     kind=type(exc).__name__)
        raise ListFailed(reason) from None
    return replace(clean_tools(raw), identity=_identity(stored))


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


def _decode(raw: Any) -> tuple[list[dict[str, Any]], float, str] | None:
    """The tools, ``fetched_at`` and identity of one cached value, or None for
    a value that is not ours."""
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
    identity = value.get("identity")
    if (not isinstance(tools, list) or isinstance(fetched, bool)
            or not isinstance(fetched, int | float) or not isinstance(identity, str)):
        return None
    return [t for t in tools if isinstance(t, dict)], float(fetched), identity


async def _store(org: str, server: str, listed: ToolList) -> None:
    value = json.dumps({
        "v": CACHE_VERSION,
        "tools": listed.tools,
        "truncated": listed.truncated,
        "fetched_at": _now(),
        "identity": listed.identity,
    })
    client = _redis()
    with _scope(org):
        await client.setex(_key("tools", server), LIST_TTL_S, value)
        index = _key("index")
        await client.hset(index, server, "1")
        await client.expire(index, LIST_TTL_S)


async def _forget(org: str, server: str, started: float) -> bool:
    """Drop the list of ONE server after a failed read that began at *started*.

    A list that is NEWER than *started* comes from a newer refresh, so it
    stays. Each other list goes, also when the slot of this refresh has
    expired. The lists of the other servers always stay."""
    client = _redis()
    with _scope(org):
        key = _key("tools", server)
        decoded = _decode(await client.get(key))
        if decoded is not None and decoded[1] > started:
            return False
        await client.delete(key)
        await client.hdel(_key("index"), server)
    return True


async def _claim_refresh(org: str, server: str) -> float:
    """Take the refresh slot of one server and give the start time, or raise
    :class:`RefreshTooSoon`."""
    client = _redis()
    with _scope(org):
        slot = _key("refresh", server)
        if await client.set(slot, "1", nx=True, ex=REFRESH_INTERVAL_S):
            return _now()
        left = int(await client.ttl(slot))
    raise RefreshTooSoon(left if left > 0 else REFRESH_INTERVAL_S)


class RefreshRefused(Exception):
    """The member may not refresh this server, or the org holds no such
    connected server. One class for each case, so the answer does not say
    which (I11a review). ``reason`` is a ``LIVE_*`` code, for the log only."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


async def refresh(org: str, server: str, *, may_use: Callable[[str], bool],
                  lister: Lister | None = None) -> Refreshed:
    """Read ONE server of *org* and fill its cache (the member action).

    A bad key is :class:`ServerNotFound`. Then, before any slot and any read
    of a credential, the same two checks as the live read (I11a review):
    the member's access allows ``integrations:use:mcp-<server>`` (*may_use*,
    the ``has_permission`` of the session), and the connected server of
    *org* has ``*`` in its ``agent_scope``. A miss of either, and a server
    that the org does not hold, raise :class:`RefreshRefused`. A failed read
    drops the list of that server only, unless the stored list is newer than
    the start of this refresh. ``RefreshTooSoon`` reaches the caller. A
    Redis fault reaches the caller too."""
    if not _server_key(server):
        raise ServerNotFound(server)
    if not _may_use(may_use, server):
        raise RefreshRefused(LIVE_NO_GRANT)
    meta = next((s for s in await mcp_registry.enabled_meta(org)
                 if s.name == server and s.enabled is True), None)
    if meta is None:
        raise RefreshRefused(LIVE_NO_SERVER)
    if not _scope_open(meta):
        raise RefreshRefused(LIVE_NO_SCOPE)
    started = await _claim_refresh(org, server)
    try:
        listed = await list_tools(org, server, lister=lister)
    except ListFailed as failed:
        await _forget(org, server, started)
        return Refreshed(server=server, ok=False, reason=failed.reason)
    await _store(org, server, listed)
    return Refreshed(server=server, ok=True, tools=len(listed.tools),
                     truncated=listed.truncated)


# ── The approval of the text of one tool (STU-B1b-3) ────────────────────────

#: The fewest seconds between two approvals of one tool of one org.
APPROVE_INTERVAL_S = 5


class ToolNotListed(LookupError):
    """The fresh list of the server holds no tool of that name."""


class ListNotFresh(Exception):
    """The server has no cached list that is fresh and of the identity of
    now. The admin refreshes the server first."""


@dataclass(frozen=True)
class Approved:
    """What an approval gives the route. It holds no text of the tool."""

    server: str
    tool: str
    cls: str
    text_hash: str


async def _claim_approval(org: str, server: str, tool: str) -> None:
    """Take the approval slot of one tool, or raise :class:`RefreshTooSoon`."""
    client = _redis()
    with _scope(org):
        slot = _key("approve", server, tool)
        if await client.set(slot, "1", nx=True, ex=APPROVE_INTERVAL_S):
            return
        left = int(await client.ttl(slot))
    raise RefreshTooSoon(left if left > 0 else APPROVE_INTERVAL_S)


async def _save_approval(org: str, server: str, tool: str, text_hash: str,
                         member: str) -> None:
    """Write ONE approval of *org* under ``tenant_session(org)``."""
    from gateway.db import tenant_session

    async with tenant_session(org) as db:
        await mcp_approvals.upsert(db, server=server, tool=tool, text_hash=text_hash,
                                   member=member)


async def approve_tool(org: str, server: str, tool: str, *, member: str) -> Approved:
    """Approve the CURRENT text of ONE tool of a server of *org* (B1b-3).

    *org* and *member* come from the session, never from request input
    (R5). The route checks the right of the member first. The order:

    1. A bad server key or tool name is :class:`ServerNotFound`.
    2. The server is a connected server of *org* (``enabled_meta``), else
       :class:`ServerNotFound`. So a server of another org writes nothing.
    3. The cached list of the server is younger than :data:`FRESH_S` and of
       the ``_identity`` of the server of now, else :class:`ListNotFresh`.
       The approval never reads a server itself.
    4. The tool is in that list (``unique_tools``), else :class:`ToolNotListed`.
    5. One slot of :data:`APPROVE_INTERVAL_S` holds for each tool, else
       :class:`RefreshTooSoon`.
    6. The hash of THAT item (``mcp_actions.tool_text_hash``) is written for
       the member. A Redis or database fault reaches the caller."""
    if not _server_key(server) or not isinstance(tool, str) or not _TOOL_NAME.fullmatch(tool):
        raise ServerNotFound(server)
    meta = next((s for s in await mcp_registry.enabled_meta(org)
                 if s.name == server and s.enabled is True), None)
    if meta is None:
        raise ServerNotFound(server)
    client = _redis()
    with _scope(org):
        decoded = _decode(await client.get(_key("tools", server)))
    if decoded is None:
        raise ListNotFresh(server)
    tools, fetched, identity = decoded
    if identity != _identity(meta) or not 0 <= _now() - fetched <= FRESH_S:
        raise ListNotFresh(server)
    item = next((t for t in mcp_actions.unique_tools(tools) if t.get("name") == tool), None)
    if item is None:
        raise ToolNotListed(tool)
    await _claim_approval(org, server, tool)
    text_hash = mcp_actions.tool_text_hash(item)
    await _save_approval(org, server, tool, text_hash, member)
    cls = mcp_actions.mcp_tool_class(mcp_actions.tool_hints(item.get("annotations")),
                                     always_confirm=meta.always_confirm)
    return Approved(server=server, tool=tool, cls=cls, text_hash=text_hash)


async def _approvals(org: str) -> dict[tuple[str, str], str]:
    """The approved hashes of the tools of *org* (B1b-3), in ONE query under
    ``tenant_session(org)``. *org* comes from the session, never from request
    input. A fault reaches the caller, and each caller fails closed."""
    from gateway.db import tenant_session

    async with tenant_session(org) as db:
        return await mcp_approvals.approved_hashes(db)


async def _approvals_or_none(org: str) -> dict[tuple[str, str], str] | None:
    """:func:`_approvals`, or None when the read fails. None makes each tool
    changed."""
    try:
        return await _approvals(org)
    except Exception as exc:
        _log.warning("studio.mcp_tools.approvals_unavailable", kind=type(exc).__name__)
        return None


async def catalog_tools(org: str) -> tuple[list[dict[str, Any]], bool]:
    """The ``mcp`` rows of the catalog of *org*, and ``stale``.

    It reads the cache and ``mcp.enabled_meta``, and NEVER a server. It
    serves the last good list of each connected server, at any age up to
    :data:`LIST_TTL_S`, when the hash of the list equals the hash of the
    server of now. ``stale`` is true when the index is empty, when a
    connected server has no list or a list of another identity, or when a
    list is older than :data:`FRESH_S`. A Redis or database fault gives no row and
    ``stale: true``, because the catalog must still answer.

    B1b-3: the approvals of the org come in ONE query, after the cache read.
    A tool with no approval or another hash is ``changed: true`` and class
    B. A failed read of the approvals makes each tool changed."""
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
    for server, raw in zip(servers, values, strict=True):
        decoded = _decode(raw)
        if decoded is None or decoded[2] != _identity(server):
            stale = True
            continue
        tools[server.name], fetched, _ = decoded
        if now - fetched > FRESH_S:
            stale = True
    approved = await _approvals_or_none(org) if tools else {}
    return mcp_actions.mcp_rows(servers, tools, approved=approved), stale


# ── The live read of one class A tool (STU-I11a) ────────────────────────────

#: The namespace of each key of a live answer: ``cc:<org>:studio-live:...``.
LIVE_NAMESPACE = "studio-live"
#: The seconds that one live answer lives in the cache.
ANSWER_TTL_S = 60
#: The most bytes of all the answers of one ``tools/call`` session.
MAX_CALL_BYTES = MAX_ANSWER_BYTES
#: The most items that a live answer keeps of each array: its rows.
MAX_ROWS = 50
#: The most characters of each text of a live answer.
TEXT_CHARS = 4000
#: The most characters of all the texts of one live answer.
MAX_RESULT_CHARS = 200_000
#: The most values of one live answer.
MAX_NODES = 10_000
#: The deepest nesting that a live answer keeps.
MAX_DEPTH = 8
#: The most keys that a live answer keeps of each object.
MAX_KEYS = 100
#: The version of the shape of a cached live answer.
ANSWER_VERSION = 1

#: The reason of a ``tools/call`` answer with ``isError``. It is an error,
#: never data.
REASON_TOOL_ERROR = "tool_error"
#: The reason of a cache that does not answer. The live read fails closed.
REASON_CACHE = "cache_unavailable"

#: The reasons of :class:`LiveRefused`. Each one sends nothing more.
LIVE_NO_SERVER = "server_not_found"
LIVE_NO_TOOL = "tool_unknown"
LIVE_NOT_A = "not_class_a"
LIVE_NEEDS_CARD = "needs_card"
LIVE_CHANGED = "server_changed"
LIVE_BAD_INPUT = "bad_input"
LIVE_NO_GRANT = "grant_denied"
LIVE_NO_SCOPE = "scope_denied"
#: B1b-3: the tool has no approval, or its text differs from the approved one.
LIVE_TOOL_CHANGED = "tool_changed"

#: The most live reads that make a network call, for one member in one
#: window of :data:`RATE_WINDOW_S`, and for one org. A cache hit is free.
MEMBER_CALLS_PER_WINDOW = 10
ORG_CALLS_PER_WINDOW = 60
RATE_WINDOW_S = 60
#: The most live reads of one org that hold the network at one time, in this
#: process. The gateway runs one uvicorn worker (``invoke.WRITERS_PER_ORG``
#: has the same reason), so a count in the process is the count of the box,
#: and a crash cannot leave a count behind as a Redis counter can.
LIVE_PER_ORG = 3

_LIVE_IN_FLIGHT: dict[str, int] = {}


def mcp_service(name: str) -> str:
    """The service of an MCP server in ``integrations:use:<service>``.

    The same rule as ``orchestrator._maf_mcp.mcp_service``, which the agent
    run checks. A service never imports another service, so the parity test
    ``test_studio_live_preview.py::test_the_service_name_is_the_rule_of_the_run``
    holds the two equal."""
    return f"mcp-{name}"


class LiveLimited(Exception):
    """The live read is over a limit, and it sends nothing. ``retry_after`` is
    the seconds to wait."""

    def __init__(self, retry_after: int) -> None:
        super().__init__(str(retry_after))
        self.retry_after = retry_after


class LiveRefused(Exception):
    """The live read does not call the tool. ``reason`` is a ``LIVE_*`` code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class LiveRead:
    """One live answer of a class A tool. It holds no url and no header."""

    server: str
    result: dict[str, Any]
    truncated: bool
    fetched_at: float
    cached: bool = False


#: The type of the function that sends one ``tools/call``. A test gives a
#: fake one. The production function is :func:`_sdk_call`.
Caller = Callable[[Any, str, dict[str, Any]], Awaitable[Any]]


async def _call_once(client: Any, url: str, tool: str, args: dict[str, Any]) -> Any:
    """ONE ``tools/call`` on one session. It uses ``send_request`` and not
    ``call_tool``, because ``call_tool`` reads ``tools/list`` again to check
    an output schema. So the session sends ``initialize`` and one call."""
    from mcp import ClientSession, types
    from mcp.client.streamable_http import streamable_http_client

    request = types.ClientRequest(types.CallToolRequest(
        params=types.CallToolRequestParams(name=tool, arguments=args)))
    async with (streamable_http_client(url, http_client=client) as streams,
                ClientSession(streams[0], streams[1]) as session):
        await session.initialize()
        return await session.send_request(request, types.CallToolResult)


async def _sdk_call(server: Any, tool: str, args: dict[str, Any]) -> Any:
    """The raw ``tools/call`` answer of ONE tool of ONE server, through the
    pinned client with a budget (:func:`_pinned`)."""
    async def _work(client: Any, url: str) -> Any:
        return await _call_once(client, url, tool, args)

    return await _pinned(server, _work, total=MAX_CALL_BYTES)


class _Shaper:
    """The cut and the clean of one live answer. Each text goes through
    ``strip_hidden``, before and after its cut. Each array keeps
    :data:`MAX_ROWS` items. Each cut sets ``truncated``."""

    def __init__(self) -> None:
        self.truncated = False
        self.chars = 0
        self.nodes = 0

    def text(self, raw: str, cap: int = TEXT_CHARS) -> str:
        clean = strip_hidden(raw)
        room = min(cap, max(0, MAX_RESULT_CHARS - self.chars))
        if len(clean) > room:
            self.truncated = True
            clean = strip_hidden(clean[:room])
        self.chars += len(clean)
        return clean

    def value(self, raw: Any, depth: int = 0) -> Any:
        self.nodes += 1
        if self.nodes > MAX_NODES or depth > MAX_DEPTH:
            self.truncated = True
            return None
        if raw is None or isinstance(raw, bool):
            return raw
        if isinstance(raw, int):
            return raw
        if isinstance(raw, float):
            return raw if math.isfinite(raw) else None
        if isinstance(raw, str):
            return self.text(raw)
        if isinstance(raw, list | tuple):
            if len(raw) > MAX_ROWS:
                self.truncated = True
            return [self.value(v, depth + 1) for v in raw[:MAX_ROWS]]
        if isinstance(raw, Mapping):
            if len(raw) > MAX_KEYS:
                self.truncated = True
            out: dict[str, Any] = {}
            for k, v in islice(raw.items(), MAX_KEYS):
                whole = strip_hidden(str(k))
                key = strip_hidden(whole[:200])
                if len(whole) > 200:
                    self.truncated = True
                if self.chars + len(key) > MAX_RESULT_CHARS:
                    # No room is left, so the pair goes, and its key is never cut.
                    self.truncated = True
                    break
                if key in out:
                    # Two keys that clean to one text: the first stays.
                    self.truncated = True
                    continue
                self.chars += len(key)
                out[key] = self.value(v, depth + 1)
            return out
        self.truncated = True
        return None

    def item(self, raw: Any) -> dict[str, Any]:
        kind = _field(raw, "type")
        kind = kind if isinstance(kind, str) else ""
        if kind != "text":
            # An image, an audio clip or a resource: its kind only, no data.
            return {"type": self.text(kind, 32)}
        text = _field(raw, "text")
        text = text if isinstance(text, str) else ""
        if text.lstrip()[:1] in ("[", "{"):
            try:
                return {"type": "json", "data": self.value(json.loads(text))}
            except (ValueError, RecursionError):
                pass
        return {"type": "text", "text": self.text(text)}


def shape_result(raw: Any) -> tuple[dict[str, Any], bool]:
    """The capped, cleaned answer of one ``tools/call``, and ``truncated``.

    An answer with ``isError`` raises :class:`ListFailed` with
    :data:`REASON_TOOL_ERROR`, so an error of the tool never reads as data.
    The answer holds ``content`` (each item as text, as JSON data, or as its
    kind only) and ``structured``, and nothing else of the server."""
    if _field(raw, "isError") is True:
        raise ListFailed(REASON_TOOL_ERROR)
    shaper = _Shaper()
    content = _field(raw, "content")
    items: list[dict[str, Any]] = []
    if isinstance(content, list | tuple):
        if len(content) > MAX_ROWS:
            shaper.truncated = True
        items = [shaper.item(c) for c in content[:MAX_ROWS]]
    structured = _field(raw, "structuredContent")
    shaped = shaper.value(structured) if isinstance(structured, Mapping) else None
    return {"content": items, "structured": shaped}, shaper.truncated


def _live_key(*parts: str) -> Any:
    from acb_common import tenant_redis

    return tenant_redis.key(LIVE_NAMESPACE, *parts)


def _member_part(member: str) -> str:
    """The member as a key part: a hash, so no address sits in a key name."""
    return hashlib.sha256(member.strip().lower().encode("utf-8", "surrogatepass")).hexdigest()[:32]


def _input_part(identity: str, args: dict[str, Any]) -> str:
    """The hash of the input and of the server identity. So a new server with
    an old name never gets the answer of the old one."""
    try:
        text = json.dumps([identity, args], sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError):
        raise LiveRefused(LIVE_BAD_INPUT) from None
    return hashlib.sha256(text.encode("ascii")).hexdigest()


async def fresh_tool_list(org: str, meta: Any, load: Callable[[], Awaitable[Any]], *,
                          lister: Lister | None = None) -> ToolList:
    """THE one read of a tool list that a live call may trust (I11a).

    It holds both checks of the rule of the B1b-2 review. A cached list
    serves only when it is younger than :data:`FRESH_S` AND its identity
    equals ``_identity`` of the server *meta* of now. Else it reads a live
    ``tools/list`` of the server that *load* gives, and stores it as a
    refresh does. That server must have the identity of *meta*, else
    :class:`LiveRefused`. A cache that does not answer raises
    :class:`ListFailed` with :data:`REASON_CACHE` (fail closed)."""
    want = _identity(meta)
    try:
        client = _redis()
        with _scope(org):
            decoded = _decode(await client.get(_key("tools", meta.name)))
    except Exception as exc:
        _log.warning("studio.mcp_tools.cache_unavailable", kind=type(exc).__name__)
        raise ListFailed(REASON_CACHE) from None
    if decoded is not None:
        tools, fetched, identity = decoded
        if identity == want and 0 <= _now() - fetched <= FRESH_S:
            return ToolList(tools=tools, truncated=False, identity=identity)
    stored = await load()
    if _identity(stored) != want:
        raise LiveRefused(LIVE_CHANGED)
    listed = await _list_stored(stored, lister)
    try:
        await _store(org, meta.name, listed)
    except Exception as exc:
        # The list is live, so the call may still trust it.
        _log.warning("studio.mcp_tools.cache_unavailable", kind=type(exc).__name__)
    return listed


def _class_a_item(listed: ToolList, meta: Any, tool: str,
                  approved: Mapping[tuple[str, str], str] | None) -> None:
    """Refuse unless *tool* is class A in the list *listed* of the server
    *meta*, its text is the approved text, and the permission gate of the run
    asks no card for it.

    The class is the one class rule (``mcp_actions.mcp_tool_class``) on the
    list, never the label of the catalog. A duplicate name takes the
    stricter item (``unique_tools``). B1b-3: the item of THAT list must hash
    as the approval in *approved*, else :data:`LIVE_TOOL_CHANGED`. Then the
    gate seam ``mcp_needs_confirmation`` runs on the same hints, at run time."""
    item = next((t for t in mcp_actions.unique_tools(listed.tools) if t.get("name") == tool), None)
    if item is None:
        raise LiveRefused(LIVE_NO_TOOL)
    if mcp_actions.tool_changed(approved, meta.name, item):
        raise LiveRefused(LIVE_TOOL_CHANGED)
    hints = mcp_actions.tool_hints(item.get("annotations"))
    if mcp_actions.mcp_tool_class(hints, always_confirm=meta.always_confirm) != "A":
        raise LiveRefused(LIVE_NOT_A)
    if meta.always_confirm is not False or mcp_needs_confirmation(hints):
        raise LiveRefused(LIVE_NEEDS_CARD)


def _decode_answer(raw: Any) -> tuple[dict[str, Any], bool, float] | None:
    if not isinstance(raw, str | bytes):
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(value, dict) or value.get("v") != ANSWER_VERSION:
        return None
    result, truncated, fetched = value.get("result"), value.get("truncated"), value.get("fetched_at")
    if (not isinstance(result, dict) or not isinstance(truncated, bool)
            or isinstance(fetched, bool) or not isinstance(fetched, int | float)):
        return None
    return result, truncated, float(fetched)


async def _spend(org: str, member: str) -> None:
    """Count one live read that makes a network call, or raise
    :class:`LiveLimited`. A fixed window of :data:`RATE_WINDOW_S` in
    ``tenant_redis``, with SET NX EX and then INCR: one counter for the member and
    one for the org. A member over the limit does not count for the org. A
    cache that does not answer raises :class:`ListFailed` (fail closed)."""
    now = _now()
    window = str(int(now // RATE_WINDOW_S))
    left = max(1, int(RATE_WINDOW_S - now % RATE_WINDOW_S))
    try:
        client = _redis()
        with _scope(org):
            for key, cap in ((_live_key("rate", "m", _member_part(member), window),
                              MEMBER_CALLS_PER_WINDOW),
                             (_live_key("rate", "org", window), ORG_CALLS_PER_WINDOW)):
                # SET NX EX makes the key WITH its TTL in one command, so no
                # key ever lives without one. A key that is there takes INCR.
                if await client.set(key, "1", nx=True, ex=RATE_WINDOW_S + 1):
                    count = 1
                else:
                    count = int(await client.incr(key))
                    if int(await client.ttl(key)) == -1:
                        # INCR after the key expired made a new key: give it a TTL.
                        await client.expire(key, RATE_WINDOW_S + 1)
                if count > cap:
                    raise LiveLimited(left)
    except LiveLimited:
        raise
    except Exception as exc:
        _log.warning("studio.mcp_tools.cache_unavailable", kind=type(exc).__name__)
        raise ListFailed(REASON_CACHE) from None


def _take_live_slot(org: str) -> None:
    """Take one of the :data:`LIVE_PER_ORG` slots of *org*, or raise
    :class:`LiveLimited`."""
    if _LIVE_IN_FLIGHT.get(org, 0) >= LIVE_PER_ORG:
        raise LiveLimited(1)
    _LIVE_IN_FLIGHT[org] = _LIVE_IN_FLIGHT.get(org, 0) + 1


def _free_live_slot(org: str) -> None:
    left = _LIVE_IN_FLIGHT.get(org, 0) - 1
    if left > 0:
        _LIVE_IN_FLIGHT[org] = left
    else:
        _LIVE_IN_FLIGHT.pop(org, None)


def _may_use(may_use: Callable[[str], bool], server: str) -> bool:
    """True only when the member's access allows ``integrations:use:mcp-<server>``.
    A fault of the check is False (fail closed)."""
    from acb_auth import integration_use_permission

    try:
        return may_use(integration_use_permission(mcp_service(server))) is True
    except Exception:
        return False


async def call_read_tool(org: str, member: str, server: str, tool: str, args: dict[str, Any], *,
                         may_use: Callable[[str], bool],
                         lister: Lister | None = None, caller: Caller | None = None) -> LiveRead:
    """The live read of ONE class A tool for a preview of the door (I11a).

    *org* and *member* come from the session of the caller, never from
    request input (R5). The order:

    0. The member's access allows ``integrations:use:mcp-<server>``
       (*may_use*, the ``has_permission`` of the session), the grant that
       the agent run checks. Else :data:`LIVE_NO_GRANT`, before any read.
    1. The server is a connected server of *org* (``enabled_meta``), and its
       ``agent_scope`` holds ``*``. A server that the admin gave to no agent,
       or to named agents only, is :data:`LIVE_NO_SCOPE`: a preview is no
       agent, so it fails closed.
    1a. B1b-3: the approvals of *org* (one query). A tool with no approval
       is :data:`LIVE_TOOL_CHANGED` before any network step, and so is a
       failed read.
    2. :func:`fresh_tool_list` gives a list that is fresh and of the
       identity of now, or a live one.
    3. The tool is class A in THAT list, its text hashes as the approval
       (else :data:`LIVE_TOOL_CHANGED`), and the gate asks no card.
    4. A cached answer of this org, member, server, tool and input, younger
       than :data:`ANSWER_TTL_S`, is the answer. It makes no call.
    The first network step (a live list or the call) first passes the
    limits: :func:`_spend` and one of :data:`LIVE_PER_ORG` slots, else
    :class:`LiveLimited`. A cache hit with a fresh list passes no limit.

    5. Else ONE ``tools/call`` through :func:`_sdk_call`, under
       :data:`READ_TIMEOUT_S`. The answer is cut and cleaned
       (:func:`shape_result`), and only that goes to the cache.

    A refusal raises :class:`LiveRefused`, and a failed server or an error
    of the tool raises :class:`ListFailed`. Neither holds a url, a header or
    an exception text."""
    if (not _server_key(server) or not isinstance(tool, str) or not _TOOL_NAME.fullmatch(tool)
            or not isinstance(member, str) or not member.strip() or not isinstance(args, dict)):
        raise LiveRefused(LIVE_BAD_INPUT)
    if not _may_use(may_use, server):
        raise LiveRefused(LIVE_NO_GRANT)
    meta = next((s for s in await mcp_registry.enabled_meta(org)
                 if s.name == server and s.enabled is True), None)
    if meta is None:
        raise LiveRefused(LIVE_NO_SERVER)
    if not _scope_open(meta):
        raise LiveRefused(LIVE_NO_SCOPE)
    approved = await _approvals_or_none(org)
    if approved is None or (server, tool) not in approved:
        raise LiveRefused(LIVE_TOOL_CHANGED)
    loaded: list[Any] = []
    held: list[str] = []

    async def _load() -> Any:
        if not loaded:
            await _spend(org, member)
            _take_live_slot(org)
            held.append(org)
            try:
                loaded.append(await _stored(org, server))
            except ServerNotFound:
                raise LiveRefused(LIVE_NO_SERVER) from None
        return loaded[0]

    try:
        return await _live(org, member, server, tool, args, meta, _load, lister, caller,
                           approved)
    finally:
        for slot in held:
            _free_live_slot(slot)


async def _live(org: str, member: str, server: str, tool: str, args: dict[str, Any], meta: Any,
                _load: Callable[[], Awaitable[Any]], lister: Lister | None,
                caller: Caller | None,
                approved: Mapping[tuple[str, str], str] | None) -> LiveRead:
    """Steps 2 to 5 of :func:`call_read_tool`."""
    listed = await fresh_tool_list(org, meta, _load, lister=lister)
    _class_a_item(listed, meta, tool, approved)
    identity = _identity(meta)
    parts = ("answer", _member_part(member), server, tool,
             _input_part(_answer_identity(meta), args))
    try:
        client = _redis()
        with _scope(org):
            hit = _decode_answer(await client.get(_live_key(*parts)))
    except Exception as exc:
        _log.warning("studio.mcp_tools.cache_unavailable", kind=type(exc).__name__)
        raise ListFailed(REASON_CACHE) from None
    if hit is not None:
        return LiveRead(server=server, result=hit[0], truncated=hit[1], fetched_at=hit[2],
                        cached=True)
    stored = await _load()
    if _identity(stored) != identity:
        raise LiveRefused(LIVE_CHANGED)
    try:
        async with asyncio.timeout(READ_TIMEOUT_S):
            raw = await (caller or _sdk_call)(stored, tool, args)
    except Exception as exc:
        reason = _reason(exc)
        _log.warning("studio.mcp_tools.call_failed", name=server, reason=reason,
                     kind=type(exc).__name__)
        raise ListFailed(reason) from None
    result, truncated = shape_result(raw)
    fetched = _now()
    value = json.dumps({"v": ANSWER_VERSION, "result": result, "truncated": truncated,
                        "fetched_at": fetched})
    try:
        with _scope(org):
            await client.setex(_live_key(*parts), ANSWER_TTL_S, value)
    except Exception as exc:
        _log.warning("studio.mcp_tools.cache_unavailable", kind=type(exc).__name__)
    return LiveRead(server=server, result=result, truncated=truncated, fetched_at=fetched)


__all__ = [
    "ANSWER_TTL_S",
    "APPROVE_INTERVAL_S",
    "FRESH_S",
    "LIST_TTL_S",
    "LIVE_NAMESPACE",
    "MAX_ANSWER_BYTES",
    "MAX_LISTED",
    "MAX_PAGES",
    "MAX_READ_BYTES",
    "MAX_ROWS",
    "MAX_TOOLS",
    "NAMESPACE",
    "READ_TIMEOUT_S",
    "REFRESH_INTERVAL_S",
    "Approved",
    "ListFailed",
    "ListNotFresh",
    "LiveLimited",
    "LiveRead",
    "LiveRefused",
    "RefreshRefused",
    "RefreshTooSoon",
    "Refreshed",
    "ServerNotFound",
    "ToolList",
    "ToolNotListed",
    "approve_tool",
    "call_read_tool",
    "catalog_tools",
    "clean_tools",
    "fresh_tool_list",
    "list_tools",
    "mcp_service",
    "refresh",
    "shape_result",
]
