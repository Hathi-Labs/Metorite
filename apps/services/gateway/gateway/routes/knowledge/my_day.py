"""The Knowledge reads of My Day (WS-61 KB-9b).

Spec: ``project-docs/specs/knowledge_base.md`` §9 and §17 (the KB-9b row),
``my_day_feed.md`` §5.8 and MD-3.

    read(user, gaps_on=..., sources_on=...)
                            the rows of ``kb_gap``, ``doc_needs_review``,
                            ``doc_shared_with_me`` and ``source_sync_failed``
                            for the viewer

**One session.** ``read`` opens ONE tenant session, and it reads the gaps,
the expired pages and the new pages in it. The provider in
``routes/shell/feed.py`` opens none.

**One predicate.** Each read of ``kb_pages`` puts in the result of
``core.visible_pages_clause``, and also names ``s.kind <> 'me'`` and
``s.archived_at IS NULL``. So a page in a ``me`` space, a draft, a page that
the viewer cannot read and a page of another organization never give a row.
The tenant and the viewer come from the session user (R5). A viewer with no
email gets no page row.

**The two kinds.**

* ``doc_needs_review``: a published page past its review date, for the owner,
  or for the author when the page has no owner (``COALESCE(owner, author)``).
* ``doc_shared_with_me``: a page that ANOTHER author published in the last 24
  hours, in a space that the viewer reads. At most :data:`SHARED_MAX`, the
  newest first.

Both key on ``knowledge:page:<id>``, so one page makes one card.

**The fourth kind (KB-9b slice 2).** ``source_sync_failed`` is for the member
who connected a Drive, and for nobody else: the read names
``lower(owner_email) = :kb_viewer``, the organization and
``state <> 'disconnected'``. A source fails when its state is
``needs_reconnect``, or when its three newest FINISHED runs of kind
``changes`` or ``reconcile`` all have status ``failed`` with an error other
than ``stale`` or ``superseded``. A stopped run is finished and is not
failed, so it breaks the three. A running run is not finished. The read runs
only with ``sources_on`` (``KNOWLEDGE_SOURCES`` names the org), in the SAME
session, and gives no error text and no account address. Each row also holds
``failed_since``, the one fact that changes once for each failure EPISODE:
for ``needs_reconnect`` it is ``kb_sources.updated_at`` (the only write while
that state holds is the transition, because the loop skips a source that is
not ``ready``), and for failed runs it is the ``started_at`` of the newest run
BEFORE the streak (an ok, stopped, stale or superseded run). That anchor stays
fixed when a 4th or 5th failed run lands, and it does not move when the
retention sweep prunes the oldest failed runs. It falls back to
``discovered_at`` when the sweep took the anchor. It reads at most
:data:`SOURCES_MAX` sources, and uses ``kb_sync_runs_source_started_idx``.

Fences: ``tests/unit/test_kb_my_day.py`` and ``tests/unit/test_kb_my_day_r8.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from acb_auth import UserContext
from gateway.routes.knowledge import core, gaps
from gateway.routes.knowledge.maintenance import EXPIRED
from sqlalchemy import text

#: The most ``doc_shared_with_me`` rows (OQ6).
SHARED_MAX = 5
#: The most ``doc_needs_review`` rows (OQ10). A dismissed card keeps its
#: slot, so the cap leaves room for the gap and new-page cards.
REVIEW_MAX = 10
#: How long a published page stays new, in hours (OQ5). The card expires
#: after the same time, and ``feed.py`` imports this value.
NEW_HOURS = 24
#: The most sources one member has in the read of ``source_sync_failed``.
SOURCES_MAX = 10
#: How many finished runs in a row must fail before a card shows.
FAILED_RUNS = 3


@dataclass(frozen=True)
class Rows:
    """What one read found: each list is in the order of its kind."""

    review: list[dict[str, Any]] = field(default_factory=list)
    gaps: list[dict[str, Any]] = field(default_factory=list)
    shared: list[dict[str, Any]] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)


def _page_row(r: Any) -> dict[str, Any]:
    return {"id": str(r.id), "title": r.title, "space": r.space_name,
            "review_at": r.review_at, "published_at": r.published_at}


async def _review_rows(db: Any, user: UserContext, limit: int) -> list[dict[str, Any]]:
    """Published pages past their review date, owned by the viewer."""
    vis, params = core.visible_pages_clause(user, "p")
    rows = (await db.execute(
        text(
            "SELECT p.id, p.title, s.name AS space_name, p.review_at, p.published_at "
            "FROM kb_pages p JOIN kb_spaces s "
            "ON s.id = p.space_id AND s.organization_id = p.organization_id "
            "WHERE p.state = 'published' AND s.kind <> 'me' AND s.archived_at IS NULL "
            f"AND {vis} AND {EXPIRED} "
            "AND lower(COALESCE(p.owner_email, p.author_email)) = :kb_viewer "
            "ORDER BY p.review_at ASC, p.id ASC LIMIT :kb_limit"
        ),
        {**params, "kb_limit": limit},
    )).fetchall()
    return [_page_row(r) for r in rows]


async def _shared_rows(db: Any, user: UserContext, limit: int) -> list[dict[str, Any]]:
    """Pages that another author published in the last day, in a space the
    viewer reads."""
    vis, params = core.visible_pages_clause(user, "p")
    rows = (await db.execute(
        text(
            "SELECT p.id, p.title, s.name AS space_name, p.review_at, p.published_at "
            "FROM kb_pages p JOIN kb_spaces s "
            "ON s.id = p.space_id AND s.organization_id = p.organization_id "
            "WHERE p.state = 'published' AND s.kind <> 'me' AND s.archived_at IS NULL "
            f"AND {vis} "
            f"AND p.published_at >= now() - interval '{NEW_HOURS} hours' "
            "AND lower(p.author_email) IS DISTINCT FROM :kb_viewer "
            "ORDER BY p.published_at DESC, p.id DESC LIMIT :kb_limit"
        ),
        {**params, "kb_limit": limit},
    )).fetchall()
    return [_page_row(r) for r in rows]


#: The sources of the viewer that fail. The run test reads the newest
#: ``FAILED_RUNS`` finished runs through ``kb_sync_runs_source_started_idx``.
#: A running row is not finished. A stopped row is, and it is not failed.
_SOURCES_SQL = (
    "SELECT s.id, s.provider, s.state, (CASE WHEN s.state = 'needs_reconnect' "
    "THEN s.updated_at ELSE COALESCE(( "
    "SELECT b.started_at FROM kb_sync_runs b "
    "WHERE b.source_id = s.id AND b.organization_id = s.organization_id "
    "AND b.kind IN ('changes', 'reconcile') AND b.status IN ('ok', 'stopped', 'failed') "
    "AND NOT (b.status = 'failed' AND COALESCE(b.error, '') NOT IN ('stale', 'superseded')) "
    "ORDER BY b.started_at DESC LIMIT 1), s.discovered_at, s.created_at) END) "
    "AS failed_since FROM kb_sources s "
    "WHERE s.organization_id = CAST(:kb_org AS uuid) "
    "AND lower(s.owner_email) = :kb_viewer AND s.state <> 'disconnected' "
    "AND (s.state = 'needs_reconnect' OR COALESCE(( "
    "SELECT count(*) = :kb_runs AND bool_and(x.status = 'failed' "
    "AND COALESCE(x.error, '') NOT IN ('stale', 'superseded')) FROM ( "
    "SELECT r.status, r.error FROM kb_sync_runs r "
    "WHERE r.source_id = s.id AND r.organization_id = s.organization_id "
    "AND r.kind IN ('changes', 'reconcile') AND r.status <> 'running' "
    "ORDER BY r.started_at DESC LIMIT :kb_runs) x), false)) "
    "ORDER BY s.created_at ASC, s.id ASC LIMIT :kb_limit"
)


async def _source_rows(db: Any, user: UserContext) -> list[dict[str, Any]]:
    """The sources that the viewer connected and that fail. No error text and
    no account address leaves this read."""
    rows = (await db.execute(text(_SOURCES_SQL), {
        "kb_org": core.org_of(user), "kb_viewer": core.viewer_email(user),
        "kb_runs": FAILED_RUNS, "kb_limit": SOURCES_MAX,
    })).fetchall()
    return [{"id": str(r.id), "provider": str(r.provider), "state": str(r.state),
             "failed_since": r.failed_since} for r in rows]


async def read(user: UserContext, *, gaps_on: bool, limit: int,
               sources_on: bool = False) -> Rows:
    """The three lists, in ONE tenant session.

    *gaps_on* is true when ``KNOWLEDGE_GAPS`` names the org. ``gaps`` still
    gives rows to a manager only. *limit* is the cut of the review list and
    of the gap list. *sources_on* is true when ``KNOWLEDGE_SOURCES`` names the
    org: only then does the read of the viewer's own failing sources run, in
    the same session. A viewer with no email gets no page row and no source.
    """
    viewer = core.viewer_email(user)
    manager = gaps_on and core.is_manager(user)
    if not viewer and not manager:
        return Rows()
    async with core._tenant_session() as db:
        review = await _review_rows(db, user, min(limit, REVIEW_MAX)) if viewer else []
        found = await gaps.my_day_rows_in(db, user, limit) if manager else []
        shared = await _shared_rows(db, user, SHARED_MAX) if viewer else []
        failing = await _source_rows(db, user) if viewer and sources_on else []
    return Rows(review=review, gaps=found, shared=shared, sources=failing)
