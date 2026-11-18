"""The ONE write seam of the failure store (TOF-2b, D225).

Spec: ``project-docs/specs/tool_failure_log.md`` §6. Fences:
``tests/unit/test_failure_log_queue.py`` (P7) and
``tests/unit/test_failure_store_r8.py`` (R8, P3 to P5).

Every capture site of §4 calls :func:`record_tool_failure`, and nothing else
writes a ``failure_*`` table. The name differs from
``gateway.jobs.unattended.record_failure``, which stays the job ledger.

How a failure travels:

1. The site calls :func:`record_tool_failure` inside its ``except`` block. It
   passes the exception (or None), the source, the code, the tool name and
   the surface. It passes no tenant and no set of built-in names.
2. The tenant comes from ``acb_common.db.current_tenant()`` only (R5). With no
   tenant, or with the org off ``TOOL_FAILURE_LOG_ORGS``, the call returns.
3. The call computes the platform fields at once, while the traceback is
   there, and keeps strings only. It keeps no reference to the exception.
4. One small record goes on a bounded queue of :data:`QUEUE_MAX`. A full
   queue drops the record and logs ``failure_log.dropped`` with a count.
5. One drain task for each event loop takes the queue every
   :data:`JOIN_WINDOW_S`. It joins equal records into one ``failure_tally``
   call with ``p_n``.
6. For each tenant, the drain opens ``tenant_session(org)``. It inserts the
   event rows and calls ``failure_tally`` in one transaction, with a timeout.
7. The sample cap: at most :data:`SAMPLE_CAP` event rows for each org,
   fingerprint and hour. A counter in tenant Redis holds the window (R5 c).
   The count goes on past the cap. When Redis fails, the drain writes the rows.

**It never fails a tool call.** :func:`record_tool_failure` never raises and
never waits on the database. A failure of the drain logs
``failure_log.write_failed`` with the class only. The counts are a floor,
because a crash loses the queue.

This module imports no ``acb_skills`` module. ``acb_skills.builtin_tools``
gives the set of built-in names to :func:`set_builtin_names` at its import
(§3.2 rule 1).
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import re
import sys
import threading
import time
from collections import defaultdict
from collections.abc import AsyncIterator, Callable, Collection, Iterable, Iterator
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any

from acb_common import failure_fingerprint as ff
from acb_common._log import get_logger

_log = get_logger(__name__)

#: The bound of the queue in the process (§6 item 4).
QUEUE_MAX = 500
#: The window in which equal records join (§6 item 5).
JOIN_WINDOW_S = 2.0
#: The bound of one write of one tenant (§6 item 6).
WRITE_TIMEOUT_S = 2.0
#: The event rows of one org, fingerprint and hour (§6 item 7).
SAMPLE_CAP = 20
SAMPLE_NAMESPACE = "failure-sample"
SAMPLE_TTL_S = 2 * 3600
REDIS_TIMEOUT_S = 0.5
#: The limit of ``p_n`` in ``failure_tally`` (§5.3).
P_N_MAX = 1000
#: The drain stops after this many empty windows. The next record starts it.
DRAIN_IDLE_WINDOWS = 30

_RELEASE_RE = re.compile(r"^[0-9a-f]{7,40}$")
_TEXT_MAX = 200
_EMAIL_MAX = 320

_INSERT_EVENT = (
    "INSERT INTO failure_events (fingerprint, source, surface, error_code, tool, "
    "exc_type, code_site, tool_name, member_email, run_id, session_id, request_id, "
    "model, release) VALUES (:fingerprint, :source, :surface, :error_code, :tool, "
    ":exc_type, :code_site, :tool_name, :member_email, :run_id, :session_id, "
    ":request_id, :model, :release)"
)
_TALLY = (
    "SELECT failure_tally(:fp, :tool, :code, :exc, :site, :source, :surface, "
    ":release, :n)"
)


@dataclass(frozen=True)
class Record:
    """One failure, as strings. It holds no exception and no message."""

    org: str
    fingerprint: str
    tool: str
    error_code: str
    exc_type: str
    code_site: str
    source: str
    surface: str
    release: str | None
    tool_name: str | None
    member_email: str | None
    run_id: str | None
    session_id: str | None
    request_id: str | None
    model: str | None

    def key(self) -> tuple[str, str, str, str, str | None]:
        """Records with one key join into one tally call."""
        return (self.org, self.fingerprint, self.source, self.surface, self.release)


_LOCK = threading.Lock()
_PENDING: list[Record] = []
_DROPPED = 0
_BUILTIN: frozenset[str] = frozenset()
_RELEASE: str | None = None
_DRAIN: asyncio.Task[None] | None = None
_CHANNEL_READER: Callable[[], object] | None = None


# ── The process state that callers set once ──────────────────────────────────


def set_builtin_names(names: Iterable[str]) -> None:
    """Store the set of built-in tool names. ``acb_skills.builtin_tools``
    calls it once, at its import. A name off the grammar never enters."""
    global _BUILTIN
    _BUILTIN = frozenset(n for n in names if isinstance(n, str) and ff.TOOL_RE.match(n))


def builtin_names() -> frozenset[str]:
    """The set that :func:`set_builtin_names` stored. Empty until then."""
    return _BUILTIN


def set_release(sha: object) -> None:
    """Store the release of this process. A value off ``^[0-9a-f]{7,40}$``
    stores None, and a row then holds NULL."""
    global _RELEASE
    value = sha.strip().lower() if isinstance(sha, str) else ""
    _RELEASE = value if _RELEASE_RE.match(value) else None


def set_channel_reader(reader: Callable[[], object] | None) -> None:
    """Store the reader of the run's channel (TOF-2c, §4.2 "The surfaces").

    ``acb_skills.builtin_tools`` gives it at its import. The reader returns
    the ``channel`` key of the run context that the executor binds. This
    module imports no ``acb_skills`` module, so the reader comes in here."""
    global _CHANNEL_READER
    _CHANNEL_READER = reader


#: The ``source`` values of a run that no member started (§4.2 "The surfaces").
#: ``workflow`` is ``routes/workflows/service.py``. ``projects.agent_dispatch``
#: is ``routes/projects/agent_dispatch.py`` (an agent works a task).
_AUTOMATION_SOURCES = frozenset({"workflow", "automation", "projects.agent_dispatch"})
#: The agent of the Projects assistant rail (``lib/nav.ts``, the Projects pane).
_PROJECTS_AGENT = "projects-assistant"


def run_channel(parent: object, source: object, *, agent: object = "",
                batch: bool = False) -> str:
    """The surface of a chat run, a value of the closed list (§4.2). Pure.

    The executor binds it as the ``channel`` key of each run context.

    * A delegated run keeps the channel of the run that asked (*parent*).
    * ``whatsapp`` is the WhatsApp channel.
    * A source in :data:`_AUTOMATION_SOURCES`, and a batch run with no
      source, is ``automation``. A person waits on no such run.
    * ``chat``, or no source on the stream path, is ``chat``. The Projects
      assistant agent there is ``projects_assistant``.
    * Any other source is ``other``. The source is a label of the caller. A
      forged label mislabels the surface only, and carries no text."""
    if isinstance(parent, str) and parent in ff.SURFACES:
        return parent
    value = source.strip().lower() if isinstance(source, str) else ""
    if value == "whatsapp":
        return "whatsapp"
    if value in _AUTOMATION_SOURCES or (not value and batch):
        return "automation"
    if value in ("", "chat"):
        name = agent.strip().lower() if isinstance(agent, str) else ""
        return "projects_assistant" if name == _PROJECTS_AGENT else "chat"
    return "other"


def run_fields() -> dict[str, Any]:
    """The surface and the pointers of the run on this frame, for a chat
    capture site (C1 to C4). Never raises.

    The surface is the run's ``channel`` key, or ``other`` with none. The
    run id, the session and the member come from the run context of
    ``acb_common._log``. The member counts only when the run bound it as
    verified (H-73), so a claim in a request body never enters a row (R5)."""
    out: dict[str, Any] = {"surface": "other", "run_id": None, "session_id": None,
                           "member_email": None}
    try:
        reader = _CHANNEL_READER
        channel = reader() if reader is not None else None
        if isinstance(channel, str) and channel in ff.SURFACES:
            out["surface"] = channel
    except Exception:  # the class only: never fail the capture site
        _log.debug("failure_log.channel_unread")
    try:
        from acb_common._log import get_run_context

        ctx = get_run_context()
        out["run_id"] = ctx.get("run_id") or None
        out["session_id"] = ctx.get("thread_id") or None
        if ctx.get("member_verified") == "1":
            out["member_email"] = ctx.get("user") or None
    except Exception:
        _log.debug("failure_log.run_context_unread")
    return out


def door_surface(actor_via: object) -> str:
    """The surface of a gateway request, from the prefix of ``X-Actor-Via``
    (§4.2 "The surfaces", TOF-2d). Pure.

    ``chat:`` is ``chat``, ``mcp:`` is ``mcp_edge``, and ``workflow:`` and
    ``system:`` are ``automation``. Any other value is ``app``. Only the
    prefix counts, so a forged header mislabels the surface and carries no
    text."""
    value = actor_via.strip().lower() if isinstance(actor_via, str) else ""
    prefix = value.split(":", 1)[0] + ":" if ":" in value else ""
    return _DOOR_SURFACES.get(prefix, "app")


_DOOR_SURFACES = {"chat:": "chat", "mcp:": "mcp_edge", "workflow:": "automation",
                  "system:": "automation"}


# ── The request mark (TOF-2d, §4.2 "One writer for each request") ────────────


class RequestMark:
    """The mark of one gateway request. :func:`record_tool_failure` sets
    ``recorded``, and the door site D2 then writes no record for the answer.

    The door puts ONE holder in a ``ContextVar`` before it calls the app. A
    child task and the thread pool copy the context, so they hold the same
    object, and a flag that they set reaches the door. A ``ContextVar.set()``
    lower down would not reach it."""

    __slots__ = ("recorded",)

    def __init__(self) -> None:
        self.recorded = False


_REQUEST_MARK: ContextVar[RequestMark | None] = ContextVar("failure_log_request_mark",
                                                           default=None)


def open_request_mark() -> tuple[RequestMark, Token[RequestMark | None]]:
    """Open the mark of one request. The door calls it before the app, and
    :func:`close_request_mark` after."""
    mark = RequestMark()
    return mark, _REQUEST_MARK.set(mark)


def close_request_mark(token: Token[RequestMark | None]) -> None:
    """Close the mark that :func:`open_request_mark` opened. Never raises."""
    try:
        _REQUEST_MARK.reset(token)
    except (ValueError, RuntimeError):
        _REQUEST_MARK.set(None)


def _mark_request() -> None:
    mark = _REQUEST_MARK.get()
    if mark is not None:
        mark.recorded = True


# ── The MCP map (TOF-2d, §4.3) ───────────────────────────────────────────────

#: The classes of the map that live in a service. This module imports no
#: service, so it finds each class in ``sys.modules``. A module that no code
#: imported cannot have raised an instance of its class.
_TOO_LARGE = (("gateway.outbound_guard", "AnswerTooLarge"),
              ("orchestrator._maf_mcp", "McpListTooLong"))
_REFUSED = (("gateway.outbound_guard", "OutboundRefused"),)
_UNREACHABLE = (("httpx", "TransportError"), ("httpx", "HTTPStatusError"),
                ("anyio", "ClosedResourceError"), ("anyio", "BrokenResourceError"))
_TOOL_EXECUTION = (("agent_framework.exceptions", "ToolExecutionException"),)
_MCP_ERROR = (("mcp.shared.exceptions", "McpError"),)
_LIST_FAILED = (("gateway.connections.mcp_tools", "ListFailed"),)
#: ``httpx.codes.REQUEST_TIMEOUT`` (``mcp/shared/session.py``) and
#: ``mcp.types.CONNECTION_CLOSED``.
_MCP_TIMEOUT_CODE = 408
_MCP_CLOSED_CODE = -32000
#: The ``reason`` of ``ListFailed`` that ``shape_result`` raises for ``isError``.
_REASON_TOOL_ERROR = "tool_error"
_MAP_WALK_MAX = 64


def _classes(names: Iterable[tuple[str, str]]) -> tuple[type, ...]:
    out = []
    for module, name in names:
        space = getattr(sys.modules.get(module), "__dict__", None)
        found = space.get(name) if isinstance(space, dict) else None
        if isinstance(found, type):
            out.append(found)
    return tuple(out)


def _mcp_chain(exc: BaseException) -> list[BaseException]:
    """*exc*, each member of its groups, and each cause and context, once.
    The walk of ``mcp_tools._reason`` (§4.3)."""
    seen: list[BaseException] = []
    stack: list[Any] = [exc]
    while stack and len(seen) < _MAP_WALK_MAX:
        cur = stack.pop()
        if not isinstance(cur, BaseException) or any(cur is s for s in seen):
            continue
        seen.append(cur)
        if isinstance(cur, BaseExceptionGroup):
            stack.extend(cur.exceptions)
        stack.extend((cur.__cause__, cur.__context__))
    return seen


def _mcp_deepest(exc: BaseException) -> BaseException | None:
    """The deepest ``Exception``: the cause, else the context, else the first
    member of a group, to the end. A ``CancelledError`` is no ``Exception``,
    so the ``TimeoutError`` of ``asyncio.timeout`` is never the deepest."""
    found: BaseException | None = None
    cur: BaseException | None = exc
    seen: set[int] = set()
    while cur is not None and id(cur) not in seen and len(seen) < _MAP_WALK_MAX:
        seen.add(id(cur))
        if isinstance(cur, Exception):
            found = cur
        nxt = cur.__cause__ or cur.__context__
        if nxt is None and isinstance(cur, BaseExceptionGroup) and cur.exceptions:
            nxt = cur.exceptions[0]
        cur = nxt
    return found


def _ours(exc: BaseException) -> bool:
    """True when the last frame of the traceback of *exc* is in one of the
    four trees of §3.2 rule 4, or its class has one of our roots (rule 3)."""
    tb = exc.__traceback__
    last = None
    while tb is not None:
        last = tb
        tb = tb.tb_next
    if last is not None and ff._in_tree(last.tb_frame.f_code.co_filename,
                                        ff._repository_root()):
        return True
    module = getattr(type(exc), "__module__", None)
    root = module.split(".", 1)[0] if isinstance(module, str) else ""
    return root in ff._OUR_ROOTS or root.startswith(ff._OUR_PREFIXES)


def _mcp_code_of(err: BaseException) -> object:
    return getattr(getattr(err, "error", None), "code", None)


def mcp_error_code(exc: BaseException) -> str:
    """The ``mcp_*`` code of a failure of an MCP server, by the map of §4.3.

    The capture sites C5, C6 and C7 call it. Each row in order tests every
    exception of the chain. The first row that matches wins. The map reads
    classes, numbers and frames only, never a message. Never raises: a fault
    of the map gives ``mcp_unreachable``, a code that we do not own."""
    try:
        found = _mcp_chain(exc)
        mcp_error = _classes(_MCP_ERROR)

        def any_of(match: Callable[[BaseException], bool]) -> bool:
            return any(match(e) for e in found)

        def is_mcp(err: BaseException, *codes: int) -> bool:
            return bool(mcp_error) and isinstance(err, mcp_error) and (
                not codes or _mcp_code_of(err) in codes)

        if any_of(lambda e: isinstance(e, TimeoutError) or is_mcp(e, _MCP_TIMEOUT_CODE)):
            return "mcp_timeout"
        too_large = _classes(_TOO_LARGE)
        if too_large and any_of(lambda e: isinstance(e, too_large)):
            return "mcp_too_large"
        refused = _classes(_REFUSED)
        if refused and any_of(lambda e: isinstance(e, refused)):
            return "mcp_refused"
        unreachable = (OSError, *_classes(_UNREACHABLE))
        if any_of(lambda e: isinstance(e, unreachable) or is_mcp(e, _MCP_CLOSED_CODE)):
            return "mcp_unreachable"
        execution = _classes(_TOOL_EXECUTION)
        listed = _classes(_LIST_FAILED)

        def tool_error(e: BaseException) -> bool:
            return ((bool(execution) and isinstance(e, execution) and e.__cause__ is None)
                    or is_mcp(e)
                    or (bool(listed) and isinstance(e, listed)
                        and getattr(e, "reason", None) == _REASON_TOOL_ERROR))

        if any_of(tool_error):
            return "mcp_tool_error"
        deepest = _mcp_deepest(exc)
        if deepest is not None and _ours(deepest):
            return "mcp_exception"
    except Exception as err:  # the class only: the map never fails a site
        with contextlib.suppress(Exception):
            _log.warning("failure_log.mcp_map_failed", error=type(err).__name__)
    return "mcp_unreachable"


@functools.lru_cache(maxsize=16)
def _parse_orgs(raw: str) -> frozenset[str]:
    return frozenset(p.strip().lower() for p in raw.split(",") if p.strip())


def failure_log_on(organization_id: str | None) -> bool:
    """True when ``TOOL_FAILURE_LOG_ORGS`` holds the org, or holds ``*``.

    The ONE reader of the flag. It reads the settings on each call, so a
    reload applies a change (``docs/flag_reload.md``). It fails closed."""
    org = str(organization_id or "").strip().lower()
    if not org:
        return False
    try:
        from acb_common.settings import get_settings

        raw = str(getattr(get_settings(), "tool_failure_log_orgs", "") or "")
    except Exception:
        return False
    listed = _parse_orgs(raw)
    return "*" in listed or org in listed


# ── The capture call ─────────────────────────────────────────────────────────


def _text(value: object, limit: int = _TEXT_MAX) -> str | None:
    if value is None:
        return None
    from acb_common.pg_text import storable

    out = storable(str(value))[:limit]
    return out or None


def record_tool_failure(
    exc: BaseException | None,
    *,
    source: str,
    error_code: str,
    tool: object,
    surface: str,
    kind: str = "chat",
    route_paths: Collection[str] = frozenset(),
    member_email: object = None,
    run_id: object = None,
    session_id: object = None,
    request_id: object = None,
    model: object = None,
) -> None:
    """Record one failure of the bound tenant. Never raises, never waits.

    *tool* is the raw name: a chat tool name, the MCP server and tool, a
    skill, or ``"<METHOD> <path template>"`` with ``kind="route"``. The
    platform field comes from the rules of §3.2 rule 1. When the platform
    field is a placeholder, the raw name goes to the tenant column
    ``tool_name`` only, cut to 200 characters.

    *route_paths* is the set of declared route templates, read once at start
    by the door site (TOF-2d). With none, a route reads ``route:other``.

    Each call marks the request scope of the gateway, when one is open
    (:class:`RequestMark`). So the door site D2 writes no second record for
    the answer of that request (§4.2, one writer for each request).
    """
    global _DROPPED
    try:
        _mark_request()
        from acb_common.db import current_tenant

        org = current_tenant()
        if not org or not failure_log_on(org):
            return
        if source not in ff.SOURCES:
            _log.warning("failure_log.refused", reason="source")
            return
        if error_code not in ff.ERROR_CODES:
            _log.warning("failure_log.refused", reason="error_code")
            return
        fields = ff.normalise(exc, error_code=error_code, tool=tool, kind=kind,
                              builtin_names=_BUILTIN, route_paths=route_paths)
        if ff.fields_problem(fields.tool, fields.error_code, fields.exc_type,
                             fields.code_site) is not None:
            _log.warning("failure_log.refused", reason="fields")
            return
        placeholder = fields.tool in ff.PLACEHOLDERS and kind != "route"
        email = _text(member_email, _EMAIL_MAX)
        rec = Record(
            org=str(org),
            fingerprint=fields.fingerprint,
            tool=fields.tool,
            error_code=fields.error_code,
            exc_type=fields.exc_type,
            code_site=fields.code_site,
            source=source,
            surface=surface if surface in ff.SURFACES else "other",
            release=_RELEASE,
            tool_name=_text(tool) if placeholder else None,
            member_email=email.lower() if email else None,
            run_id=_text(run_id),
            session_id=_text(session_id),
            request_id=_text(request_id),
            model=_text(model),
        )
        with _LOCK:
            if len(_PENDING) >= QUEUE_MAX:
                _DROPPED += 1
                count = _DROPPED
                rec = None  # type: ignore[assignment]
            else:
                _PENDING.append(rec)
        if rec is None:
            if count == 1 or count % 100 == 0:
                _log.warning("failure_log.dropped", count=count)
            return
        _ensure_drain()
    except Exception as err:  # never fail the tool; the class only
        with contextlib.suppress(Exception):
            _log.warning("failure_log.record_failed", error=type(err).__name__)


def pending() -> int:
    """The records that wait for the drain."""
    with _LOCK:
        return len(_PENDING)


def dropped() -> int:
    """The records that a full queue dropped in this process."""
    return _DROPPED


def _peek() -> list[Record]:
    with _LOCK:
        return list(_PENDING)


def _take() -> list[Record]:
    with _LOCK:
        out = list(_PENDING)
        _PENDING.clear()
    return out


# ── The drain ────────────────────────────────────────────────────────────────


def _ensure_drain() -> None:
    """Start the drain of this event loop, when none runs. With no running
    loop, the record waits for the next call on a loop."""
    global _DRAIN
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        _drain_from_thread()
        return
    task = _DRAIN
    if task is not None and not task.done() and task.get_loop() is loop:
        return
    _DRAIN = loop.create_task(_drain(), name="failure_log.drain")


_THREAD_DRAIN = threading.Event()


def _drain_from_thread() -> None:
    """A record from a thread with no running loop (a sync tool in an executor)
    still drains: one short thread runs the drain with its own loop. One such
    thread runs at a time, and the queue is still bounded."""
    if _THREAD_DRAIN.is_set():
        return
    _THREAD_DRAIN.set()

    def run() -> None:
        try:
            asyncio.run(_drain())
        except Exception as err:  # never raises into the caller's process
            with contextlib.suppress(Exception):
                _log.warning("failure_log.thread_drain_failed", error=type(err).__name__)
        finally:
            _THREAD_DRAIN.clear()

    threading.Thread(target=run, name="failure_log.drain", daemon=True).start()


async def _drain() -> None:
    idle = 0
    while idle < DRAIN_IDLE_WINDOWS:
        await asyncio.sleep(JOIN_WINDOW_S)
        took = await flush()
        idle = 0 if took else idle + 1


async def stop_drain() -> None:
    """Stop the drain of this loop, and write what waits. For a shutdown and
    for tests."""
    global _DRAIN
    task, _DRAIN = _DRAIN, None
    if task is not None and not task.done():
        task.cancel()
        with contextlib.suppress(BaseException):
            await task
    await flush()


def _chunks(n: int) -> Iterator[int]:
    """*n* as parts of :data:`P_N_MAX` at most, the limit of ``p_n``."""
    while n > 0:
        part = min(n, P_N_MAX)
        yield part
        n -= part


def _redis() -> Any:
    from acb_common.tenant_redis import get_tenant_redis

    return get_tenant_redis()


async def _real_sample_slots(org: str, fingerprint: str, n: int) -> int:
    """How many of *n* new records may write an event row (§6 item 7).

    A tenant Redis counter holds the count of the hour. When Redis fails, all
    *n* may write, up to the cap."""
    try:
        from acb_common.tenant_redis import key, organization_scope

        hour = time.strftime("%Y%m%d%H", time.gmtime())
        with organization_scope(org):
            client = _redis()
            counter = key(SAMPLE_NAMESPACE, fingerprint, hour)
            after = int(await asyncio.wait_for(client.incr(counter, n), REDIS_TIMEOUT_S))
            await asyncio.wait_for(client.expire(counter, SAMPLE_TTL_S), REDIS_TIMEOUT_S)
    except Exception as err:
        _log.warning("failure_log.sample_unavailable", error=type(err).__name__)
        return min(n, SAMPLE_CAP)
    before = after - n
    return max(0, min(n, SAMPLE_CAP - before))


_sample_slots = _real_sample_slots


@contextlib.asynccontextmanager
async def _open_session(org: str) -> AsyncIterator[Any]:
    """The one session of a write: the tenant seam, bound to *org*."""
    from acb_common.db import tenant_session

    async with tenant_session(org) as session:
        yield session


async def _write_org(org: str, groups: list[list[Record]]) -> None:
    from sqlalchemy import text

    # At the same time, so a hung Redis costs one wait of REDIS_TIMEOUT_S for the
    # whole org and not one for each group (review of TOF-2b, P2).
    slots = list(await asyncio.gather(
        *(_sample_slots(org, group[0].fingerprint, len(group)) for group in groups)))
    async with _open_session(org) as db:
        await db.execute(text("SELECT set_config('statement_timeout', :ms, true)"),
                         {"ms": str(int(WRITE_TIMEOUT_S * 1000))})
        for group, keep in zip(groups, slots, strict=True):
            rows = [_event_params(rec) for rec in group[:keep]]
            if rows:
                await db.execute(text(_INSERT_EVENT), rows)
            head = group[0]
            for n in _chunks(len(group)):
                await db.execute(text(_TALLY), {
                    "fp": head.fingerprint, "tool": head.tool, "code": head.error_code,
                    "exc": head.exc_type, "site": head.code_site, "source": head.source,
                    "surface": head.surface, "release": head.release, "n": n,
                })


def _event_params(rec: Record) -> dict[str, Any]:
    """The columns of one event row. No organization id: the column takes the
    bound tenant, and FORCE RLS checks it."""
    return {
        "fingerprint": rec.fingerprint, "source": rec.source, "surface": rec.surface,
        "error_code": rec.error_code, "tool": rec.tool, "exc_type": rec.exc_type,
        "code_site": rec.code_site, "tool_name": rec.tool_name,
        "member_email": rec.member_email, "run_id": rec.run_id,
        "session_id": rec.session_id, "request_id": rec.request_id, "model": rec.model,
        "release": rec.release,
    }


async def flush() -> int:
    """Write each record that waits now. Returns the count it took.

    Never raises. A failure of one tenant's write logs the class only, and
    the other tenants still write."""
    batch = _take()
    if not batch:
        return 0
    by_org: dict[str, dict[tuple[Any, ...], list[Record]]] = defaultdict(dict)
    for rec in batch:
        by_org[rec.org].setdefault(rec.key(), []).append(rec)
    for org, groups in by_org.items():
        try:
            await asyncio.wait_for(_write_org(org, list(groups.values())), WRITE_TIMEOUT_S)
        except Exception as err:  # the class only: a value never reaches a log
            with contextlib.suppress(Exception):
                _log.warning("failure_log.write_failed", error=type(err).__name__)
    return len(batch)


def _reset_for_tests() -> None:
    """Empty the queue, the drop count and the release. Tests only. The set
    of built-in names stays, because its giver runs once, at import."""
    global _DROPPED, _RELEASE, _DRAIN
    with _LOCK:
        _PENDING.clear()
    _DROPPED = 0
    _RELEASE = None
    task, _DRAIN = _DRAIN, None
    if task is not None and not task.done():
        with contextlib.suppress(Exception):
            task.cancel()
