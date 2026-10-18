"""The embed sweep of Knowledge (WS-61 KB-3c, ``knowledge_base.md`` §6.4).

A page gets its vectors from the job that its write schedules. Some chunks
miss that job: a page published before ``KNOWLEDGE_EMBED`` named its org, a
job that the door refused, a page larger than ``index.MAX_PENDING``. Every
:data:`TICK_SECONDS` this loop indexes them, so the whole knowledge base can
be found by meaning.

**One tick.**

1. With no org on ``KNOWLEDGE_EMBED`` (``flags.knowledge_embed_named``), the
   tick ends before it reads anything.
2. It lists the orgs from the RLS-EXEMPT ``organization`` table and keeps
   each org where ``flags.knowledge_embed_allowed`` is true.
3. For each org it reads at most :data:`MAX_CHUNKS` chunks with a NULL
   vector, of a published page, older than :data:`MIN_AGE_S`, in one short
   ``tenant_session(org)``. It groups them by page.
4. For each page it calls the door with NO session open, inside
   ``job_member_scope(member)``. The member is the page's author, or else
   the member of its newest version, and only while that member is still
   active in THIS org (``my_day_loop.departed``). So the Router's member
   proof holds, and no member bills another org.
5. It writes with ``index.write_vectors``: the ``content_sha`` and
   ``embedding IS NULL`` guard, in a new short session.

**Each chunk once.** A request-time job embeds only the shas that its write
inserted (H-330). The sweep takes only chunks older than :data:`MIN_AGE_S`.
That is more than the longest request-time job, four requests of
``embed.TIMEOUT_S``. So the sweep never takes a chunk that a job still holds.

**Flat cost.** At most :data:`MAX_CHUNKS` chunks for each org in a tick. A
chunk that the door refused, or a page with no member to bind, waits
:data:`RETRY_AFTER_S` before the sweep takes it again, so one bad chunk
cannot hold every tick. A request of two or more chunks with no vector at
all is an outage: the org stops for this tick, and nothing waits.

**The log.** One line for each tick: the org count, the chunk count, the
vectors written. Never page text, never a member.

**Dark.** ``KNOWLEDGE_EMBED_SWEEP`` (default on) gates the START inside
:func:`start_kb_embed_sweep`, never in ``main.py``. With it on, the loop
does no work until ``KNOWLEDGE_EMBED`` names an org. ``_organization_ids``
is the one unbound read, a tenant-discovery site registered in
``tests/unit/test_db_engine_seam.py``. Fences:
``tests/unit/test_kb_embed_sweep.py`` and, R8,
``tests/unit/test_kb_search_r8.py``.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass

from acb_common import get_logger
from sqlalchemy import text

from gateway.db import get_db, tenant_session
from gateway.kb_index import embed as embed_door
from gateway.kb_index import index as kb_index
from gateway.kb_index.chunk import embed_text
from gateway.routes.knowledge import flags

_log = get_logger("gateway.kb_index.sweep")

#: Seconds between two ticks.
TICK_SECONDS = 120.0

#: The most chunks of one org in one tick.
MAX_CHUNKS = 128

#: The age a chunk must have before the sweep takes it. More than the
#: longest request-time job: ``index.MAX_PENDING / embed.BATCH`` requests of
#: ``embed.TIMEOUT_S`` each, so 80 s.
MIN_AGE_S = 120

#: Seconds a refused chunk waits before the sweep takes it again.
RETRY_AFTER_S = 3600.0

#: Seconds to wait for a tick in progress when the gateway stops.
STOP_GRACE_SECONDS = 30.0

_task: asyncio.Task[None] | None = None
_stopping = asyncio.Event()

#: ``(org, chunk id) -> monotonic time`` after which the sweep takes the
#: chunk again. The gateway is one process, so in-process state is enough.
_waiting: dict[tuple[str, int], float] = {}


@dataclass(frozen=True)
class Waiting:
    """One chunk with no vector, and the members who may pay for it."""

    pending: kb_index.Pending
    page_id: str
    members: tuple[str, ...]


@dataclass
class TickResult:
    """What one tick did. A test reads it, and the log line prints it."""

    orgs: int = 0
    chunks: int = 0
    written: int = 0
    no_member: int = 0
    refused: int = 0
    outages: int = 0


def sweep_enabled() -> bool:
    """True when ``KNOWLEDGE_EMBED_SWEEP`` is on. The one reader is ``flags``."""
    return flags.knowledge_embed_sweep_enabled()


async def _organization_ids() -> list[str]:
    """Every organization id, from the RLS-EXEMPT ``organization`` table.

    The one unbound read of this loop (a tenant-discovery site).
    """
    db = await get_db()
    try:
        rows = (await db.execute(text("SELECT id FROM organization ORDER BY id"))).fetchall()
    finally:
        await db.close()
    return [str(r.id) for r in rows]


def _skipped(org: str, now: float) -> list[int]:
    """The chunk ids of *org* that still wait. Drops the ids whose wait is over."""
    for key in [k for k, until in _waiting.items() if until <= now]:
        _waiting.pop(key, None)
    return [cid for (o, cid) in _waiting if o == org]


def _park(org: str, chunk_ids: list[int], now: float) -> None:
    for cid in chunk_ids:
        _waiting[(org, cid)] = now + RETRY_AFTER_S


async def select_waiting(org: str, limit: int, skip: list[int]) -> list[Waiting]:
    """The oldest chunks of *org* with no vector, of a published page.

    An integrity read, listed in the visibility fence with its reason. It
    runs for the sweep, with no member, and it hands the text to the embed
    door only, never to a person. The two member columns name who may pay.
    """
    async with tenant_session(org) as db:
        rows = (await db.execute(
            text("SELECT c.id, c.content_sha, c.heading_path, c.raw, "
                 "c.source_id::text AS page_id, p.author_email, "
                 "(SELECT v.author_email FROM kb_page_versions v "
                 " WHERE v.organization_id = p.organization_id AND v.page_id = p.id "
                 " ORDER BY v.version DESC LIMIT 1) AS editor_email "
                 "FROM kb_chunks c "
                 "JOIN kb_pages p ON p.id = c.source_id "
                 "AND p.organization_id = c.organization_id AND p.state = 'published' "
                 "WHERE c.organization_id = CAST(:org AS uuid) "
                 "AND c.source_kind = 'page' AND c.embedding IS NULL "
                 "AND c.created_at < now() - make_interval(secs => :age) "
                 "AND NOT (c.id = ANY(CAST(:skip AS bigint[]))) "
                 "ORDER BY c.created_at, c.id LIMIT :n"),
            {"org": org, "age": int(MIN_AGE_S), "skip": [int(s) for s in skip],
             "n": int(limit)},
        )).fetchall()
    out: list[Waiting] = []
    for r in rows:
        members = tuple(dict.fromkeys(
            m for m in (str(r.author_email or "").strip(), str(r.editor_email or "").strip())
            if "@" in m
        ))
        out.append(Waiting(
            pending=kb_index.Pending(id=int(r.id), content_sha=str(r.content_sha),
                                     text=embed_text(r.heading_path, r.raw)),
            page_id=str(r.page_id), members=members,
        ))
    return out


async def _payer(org: str, members: tuple[str, ...], known: dict[str, bool]) -> str | None:
    """The first of *members* who is still active in *org*, or ``None``.

    The one departed rule of the gateway (``my_day_loop.departed``). A failed
    read proves nothing, so that member does not pay. *known* holds the
    answers of this tick, so each member costs one read.
    """
    from gateway import my_day_loop

    for member in members:
        if member not in known:
            try:
                known[member] = await my_day_loop.departed(org, member) is None
            except Exception:  # a failed read does not prove the member
                known[member] = False
        if known[member]:
            return member
    return None


def _by_page(found: list[Waiting]) -> dict[str, list[Waiting]]:
    pages: dict[str, list[Waiting]] = {}
    for w in found:
        pages.setdefault(w.page_id, []).append(w)
    return pages


async def run_org(org: str, result: TickResult) -> None:
    """One org's share of a tick. Raises nothing that the tick must see."""
    from acb_common._log import job_member_scope

    now = time.monotonic()
    found = await select_waiting(org, MAX_CHUNKS, _skipped(org, now))
    result.chunks += len(found)
    known: dict[str, bool] = {}
    for group in _by_page(found).values():
        if _stopping.is_set():
            return
        ids = [w.pending.id for w in group]
        member = await _payer(org, group[0].members, known)
        if member is None:
            result.no_member += len(group)
            _park(org, ids, now)
            continue
        # No session is open here: the read has closed, the write opens its own.
        with job_member_scope(member, app=embed_door.DEFAULT_MODULE):
            vectors = await embed_door.embed([w.pending.text for w in group], org=org)
        vectors = (list(vectors) + [None] * len(group))[:len(group)]
        if len(group) > 1 and not any(v is not None for v in vectors):
            # The door is down. Nothing waits, and the next tick tries again.
            result.outages += 1
            return
        rows = [(w.pending, v) for w, v in zip(group, vectors, strict=True) if v is not None]
        lost = [w.pending.id for w, v in zip(group, vectors, strict=True) if v is None]
        if lost:
            result.refused += len(lost)
            _park(org, lost, now)
        if rows:
            result.written += await kb_index.write_vectors(org, rows)


async def run_tick() -> TickResult:
    """One pass over the orgs on ``KNOWLEDGE_EMBED``. One log line."""
    result = TickResult()
    if not flags.knowledge_embed_named():
        return result
    orgs = [o for o in await _organization_ids() if flags.knowledge_embed_allowed(o)]
    result.orgs = len(orgs)
    for org in orgs:
        if _stopping.is_set():
            break
        try:
            await run_org(org, result)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # one org must not hide the others
            _log.warning("knowledge.embed_sweep.org_failed", error=type(exc).__name__)
    _log.info("knowledge.embed_sweep.tick", orgs=result.orgs, chunks=result.chunks,
              written=result.written, no_member=result.no_member,
              refused=result.refused, outages=result.outages)
    return result


async def _loop() -> None:
    while not _stopping.is_set():
        try:
            await run_tick()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # never let one bad tick end the loop
            _log.warning("knowledge.embed_sweep.loop_error", error=type(exc).__name__)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(_stopping.wait(), timeout=TICK_SECONDS)


async def start_kb_embed_sweep() -> bool:
    """Start the sweep when ``KNOWLEDGE_EMBED_SWEEP`` is on. Idempotent.

    With the switch off it starts no task, logs once, and returns False.
    """
    global _task
    if not sweep_enabled():
        _log.info("knowledge.embed_sweep.disabled")
        return False
    if _task is not None and not _task.done():
        return True
    _stopping.clear()
    _task = asyncio.get_running_loop().create_task(_loop(), name="kb-embed-sweep")
    _log.info("knowledge.embed_sweep.started", tick_secs=TICK_SECONDS,
              max_chunks=MAX_CHUNKS)
    return True


async def stop_kb_embed_sweep() -> None:
    """Stop the sweep and wait for a tick in progress. Safe when it never started."""
    global _task
    _stopping.set()
    task, _task = _task, None
    if task is None or task.done():
        return
    _done, pending = await asyncio.wait({task}, timeout=STOP_GRACE_SECONDS)
    for still in pending:
        _log.warning("knowledge.embed_sweep.stop_timed_out")
        still.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await still


def loop_running() -> bool:
    return _task is not None and not _task.done()


__all__ = [
    "MAX_CHUNKS",
    "MIN_AGE_S",
    "RETRY_AFTER_S",
    "TICK_SECONDS",
    "TickResult",
    "Waiting",
    "run_org",
    "run_tick",
    "select_waiting",
    "start_kb_embed_sweep",
    "stop_kb_embed_sweep",
    "sweep_enabled",
]
