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
import threading
import time
from collections import defaultdict
from collections.abc import AsyncIterator, Collection, Iterable, Iterator
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
    """
    global _DROPPED
    try:
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
        return
    task = _DRAIN
    if task is not None and not task.done() and task.get_loop() is loop:
        return
    _DRAIN = loop.create_task(_drain(), name="failure_log.drain")


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

    slots = [await _sample_slots(org, group[0].fingerprint, len(group)) for group in groups]
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
