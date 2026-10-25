"""The Knowledge reads of My Day (WS-61 KB-9b).

Spec: ``project-docs/specs/knowledge_base.md`` §9 and §17 (the KB-9b row),
``my_day_feed.md`` §5.8 and MD-3.

    read(user, gaps_on=...)  the rows of ``kb_gap``, ``doc_needs_review`` and
                            ``doc_shared_with_me`` for the viewer

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

Both key on ``knowledge:page:<id>``, so one page makes one card. Fences:
``tests/unit/test_kb_my_day.py`` and ``tests/unit/test_kb_my_day_r8.py``.
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


@dataclass(frozen=True)
class Rows:
    """What one read found: each list is in the order of its kind."""

    review: list[dict[str, Any]] = field(default_factory=list)
    gaps: list[dict[str, Any]] = field(default_factory=list)
    shared: list[dict[str, Any]] = field(default_factory=list)


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


async def read(user: UserContext, *, gaps_on: bool, limit: int) -> Rows:
    """The three lists, in ONE tenant session.

    *gaps_on* is true when ``KNOWLEDGE_GAPS`` names the org. ``gaps`` still
    gives rows to a manager only. *limit* is the cut of the review list and
    of the gap list. A viewer with no email gets no page row.
    """
    viewer = core.viewer_email(user)
    manager = gaps_on and core.is_manager(user)
    if not viewer and not manager:
        return Rows()
    async with core._tenant_session() as db:
        review = await _review_rows(db, user, min(limit, REVIEW_MAX)) if viewer else []
        found = await gaps.my_day_rows_in(db, user, limit) if manager else []
        shared = await _shared_rows(db, user, SHARED_MAX) if viewer else []
    return Rows(review=review, gaps=found, shared=shared)
