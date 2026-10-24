"""The expiry of Knowledge proposals (WS-61 KB-5c, ``knowledge_base.md`` §8.4).

The assistant files a proposal and a person answers it in the Review view.
A proposal that nobody answers must not stay open for ever. Every
:data:`TICK_SECONDS` this loop expires the proposals that waited
:data:`EXPIRY_DAYS` days.

**One tick.** It lists the orgs from the RLS-EXEMPT ``organization`` table
and keeps each org where ``flags.knowledge_org_allowed`` is true. It does not
ask for ``KNOWLEDGE_PROPOSALS``: a proposal filed while that flag was on must
still expire after the flag goes off. Each org runs in its own
``tenant_session(org)``, and one session never binds a second org.

**An edit proposal** (``kb_page_proposals``) with status ``pending`` and
``created_at`` older than the limit becomes ``expired``. The row gets
``decided_at = now()`` (the CHECK of migration 292 demands it) and
``decided_by = 'system'``. The page is not touched, so no version is made.

**A page proposal** is a draft with ``origin.kind = 'agent_proposal'``. The
tick archives it when it is still a draft and its ``updated_at`` is older
than the limit. The age is ``updated_at`` and not ``created_at``, because a
draft that a person edited is not unanswered. The archive sets
``origin.expired``, so the Review view reads ``expired`` and not
``rejected``. Each archived page gets ONE version, in the same transaction:
reason ``archive``, author ``system``, via ``sync``. The chunks of the page
go, as in the reject path, and so do its page links.

**Flat cost.** At most :data:`MAX_PER_ORG` rows of each kind for each org in
a tick. The loop reads no page text. A page row returns by ``RETURNING`` to
write the version, and nothing is logged from it.

**The log.** One line for each tick with counts only. Never a title, a body
or an address.

**Dark.** ``KNOWLEDGE_PROPOSAL_EXPIRY`` (default on) gates the START inside
:func:`start_kb_proposal_expiry`. ``_organization_ids`` is the one unbound
read, a tenant-discovery site registered in
``tests/unit/test_db_engine_seam.py``. Fences:
``tests/unit/test_kb_proposal_expiry.py`` and, R8,
``tests/unit/test_kb_proposal_expiry_r8.py``.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from typing import Any

from acb_common import get_logger
from gateway.db import get_db, tenant_session
from gateway.kb_index import index as kb_index
from gateway.routes.knowledge import core, flags, pages
from sqlalchemy import text

_log = get_logger("gateway.routes.knowledge.proposal_expiry")

#: Seconds between two ticks.
TICK_SECONDS = 3600.0

#: The days a proposal waits before it expires (§8.4).
EXPIRY_DAYS = 14

#: The most rows of each kind for one org in one tick.
MAX_PER_ORG = 200

#: Seconds to wait for a tick in progress when the gateway stops.
STOP_GRACE_SECONDS = 30.0

#: Who decides an expiry, and who writes its version.
SYSTEM = "system"

#: The reason and the channel of the version that an expiry writes. ``sync``
#: is the one channel of the CHECK that names no person and no request.
VERSION_REASON = "archive"
VERSION_VIA = "sync"

_task: asyncio.Task[None] | None = None
_stopping = asyncio.Event()

_EXPIRE_EDITS_SQL = (
    "WITH due AS ("
    "SELECT q.id FROM kb_page_proposals q "
    "WHERE q.organization_id = CAST(:org AS uuid) AND q.status = 'pending' "
    "AND q.created_at < now() - make_interval(days => :days) "
    "ORDER BY q.created_at, q.id LIMIT :n FOR UPDATE SKIP LOCKED) "
    "UPDATE kb_page_proposals AS p SET status = 'expired', decided_by = :by, "
    "decided_at = now() FROM due "
    "WHERE p.id = due.id AND p.organization_id = CAST(:org AS uuid) "
    "AND p.status = 'pending' RETURNING p.id"
)

# The bound reads ids only, in the same statement as the update. The row that
# comes back is the archived one, for its version.
_ARCHIVE_PAGES_SQL = (
    "WITH due AS ("
    "SELECT d.id FROM kb_pages d "
    "WHERE d.organization_id = CAST(:org AS uuid) AND d.state = 'draft' "
    "AND d.origin->>'kind' = 'agent_proposal' "
    "AND d.updated_at < now() - make_interval(days => :days) "
    "ORDER BY d.updated_at, d.id LIMIT :n FOR UPDATE SKIP LOCKED) "
    f"UPDATE kb_pages AS p SET {pages.ARCHIVE_SETS}, "
    "origin = p.origin || CAST(:flag AS jsonb), updated_at = now() FROM due "
    "WHERE p.id = due.id AND p.organization_id = CAST(:org AS uuid) "
    f"AND p.state = 'draft' RETURNING {core.PAGE_COLUMNS}"
)

_VERSION_SQL = (
    "INSERT INTO kb_page_versions "
    "(organization_id, page_id, version, body_md, title, author_email, reason, via) "
    "SELECT CAST(:org AS uuid), CAST(:pid AS uuid), "
    "COALESCE(MAX(v.version), 0) + 1, :body, :title, :author, :reason, :via "
    "FROM kb_page_versions v "
    "WHERE v.organization_id = CAST(:org AS uuid) AND v.page_id = CAST(:pid AS uuid)"
)

_DROP_LINKS_SQL = (
    "DELETE FROM kb_links WHERE organization_id = CAST(:org AS uuid) "
    "AND page_id = CAST(:pid AS uuid) AND target_kind = 'page'"
)


@dataclass
class TickResult:
    """What one tick did. A test reads it, and the log line prints it."""

    orgs: int = 0
    edits: int = 0
    pages: int = 0
    failed: int = 0


def expiry_enabled() -> bool:
    """True when ``KNOWLEDGE_PROPOSAL_EXPIRY`` is on. The one reader is ``flags``."""
    return flags.knowledge_proposal_expiry_enabled()


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


async def _expire_edits(db: Any, org: str) -> int:
    rows = (await db.execute(
        text(_EXPIRE_EDITS_SQL),
        {"org": org, "days": EXPIRY_DAYS, "n": MAX_PER_ORG, "by": SYSTEM},
    )).fetchall()
    return len(rows)


async def _archive_pages(db: Any, org: str) -> int:
    rows = (await db.execute(
        text(_ARCHIVE_PAGES_SQL),
        {"org": org, "days": EXPIRY_DAYS, "n": MAX_PER_ORG,
         "flag": '{"expired": true}'},
    )).fetchall()
    for row in rows:
        pid = str(row.id)
        await db.execute(text(_VERSION_SQL), {
            "org": org, "pid": pid, "body": row.body_md, "title": row.title,
            "author": SYSTEM, "reason": VERSION_REASON, "via": VERSION_VIA,
        })
        # As the reject path: an archived draft is no source of links, and it
        # holds no chunk. It calls no embed door.
        await db.execute(text(_DROP_LINKS_SQL), {"org": org, "pid": pid})
        await kb_index.reindex_page(db, row, organization_id=org)
    return len(rows)


async def run_org(org: str, result: TickResult) -> None:
    """One org's share of a tick, in ONE tenant session and one transaction."""
    async with tenant_session(org) as db:
        result.edits += await _expire_edits(db, org)
        result.pages += await _archive_pages(db, org)


async def run_tick() -> TickResult:
    """One pass over the orgs that Knowledge serves. One log line."""
    result = TickResult()
    orgs = [o for o in await _organization_ids() if flags.knowledge_org_allowed(o)]
    result.orgs = len(orgs)
    for org in orgs:
        if _stopping.is_set():
            break
        try:
            await run_org(org, result)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # one org must not hide the others
            result.failed += 1
            _log.warning("knowledge.proposal_expiry.org_failed", error=type(exc).__name__)
    _log.info("knowledge.proposal_expiry.tick", orgs=result.orgs, edits=result.edits,
              pages=result.pages, failed=result.failed)
    return result


async def _loop() -> None:
    while not _stopping.is_set():
        try:
            await run_tick()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # never let one bad tick end the loop
            _log.warning("knowledge.proposal_expiry.loop_error", error=type(exc).__name__)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(_stopping.wait(), timeout=TICK_SECONDS)


async def start_kb_proposal_expiry() -> bool:
    """Start the loop when ``KNOWLEDGE_PROPOSAL_EXPIRY`` is on. Idempotent.

    With the switch off it starts no task, logs once, and returns False.
    """
    global _task
    if not expiry_enabled():
        _log.info("knowledge.proposal_expiry.disabled")
        return False
    if _task is not None and not _task.done():
        return True
    _stopping.clear()
    _task = asyncio.get_running_loop().create_task(_loop(), name="kb-proposal-expiry")
    _log.info("knowledge.proposal_expiry.started", tick_secs=TICK_SECONDS,
              days=EXPIRY_DAYS, max_per_org=MAX_PER_ORG)
    return True


async def stop_kb_proposal_expiry() -> None:
    """Stop the loop and wait for a tick in progress. Safe when it never started."""
    global _task
    _stopping.set()
    task, _task = _task, None
    if task is None or task.done():
        return
    _done, pending = await asyncio.wait({task}, timeout=STOP_GRACE_SECONDS)
    for still in pending:
        _log.warning("knowledge.proposal_expiry.stop_timed_out")
        still.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await still


def loop_running() -> bool:
    return _task is not None and not _task.done()


__all__ = [
    "EXPIRY_DAYS",
    "MAX_PER_ORG",
    "TICK_SECONDS",
    "TickResult",
    "expiry_enabled",
    "loop_running",
    "run_org",
    "run_tick",
    "start_kb_proposal_expiry",
    "stop_kb_proposal_expiry",
]
