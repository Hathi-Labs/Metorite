"""The maintenance list of Knowledge (WS-61 KB-AI-4).

Spec: ``project-docs/specs/knowledge_base.md`` §22.3, the KB-AI-4 row.

    GET /knowledge/maintenance?reason=&limit=    the published pages that need care

A manager (``knowledge:manage``) sees the list. A page is on it when it is
**expired** (``review_at`` at or before now), **unowned** (``owner_email`` is
NULL) or **stale** (``updated_at`` older than ``core.STALE_DAYS`` days).

ONE SELECT builds the list. Each reason is a boolean column of that row, so
a page that is expired, unowned and stale is ONE row with three reasons. The
query goes through ``core.visible_pages_clause``, so a page in a ``me`` space,
a draft and a page of another organization never appear. The query
also names ``s.kind <> 'me'``, because the owner of a ``me`` space sees a
published page there through the visibility clause. The tenant and the
member come from the session (R5). Fences: ``tests/unit/test_kb_verified.py``
and, against a real database, ``tests/unit/test_kb_verified_r8.py``.
"""

from __future__ import annotations

from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException, Query
from gateway.routes.knowledge import core
from gateway.routes.knowledge.core import router
from sqlalchemy import text

#: The values of ``reason``.
REASONS = ("all", "expired", "unowned", "stale")

#: The rows of one answer.
DEFAULT_LIMIT, MAX_LIMIT = 100, 500

_EXPIRED = "(p.review_at IS NOT NULL AND p.review_at <= now())"
_UNOWNED = "(p.owner_email IS NULL)"
_STALE = f"(p.updated_at < now() - make_interval(days => {core.STALE_DAYS}))"

_FILTERS = {
    "all": f"({_EXPIRED} OR {_UNOWNED} OR {_STALE})",
    "expired": _EXPIRED,
    "unowned": _UNOWNED,
    "stale": _STALE,
}


@router.get("/maintenance")
async def list_maintenance(
    reason: str = Query("all", max_length=20),
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """The published pages that are expired, unowned or stale, each page once.

    ``counts`` counts the whole list that the caller sees, not the cut at *limit*.
    The order is expired (the oldest review date first), then unowned, then
    stale, then the id.
    """
    if reason not in _FILTERS:
        raise HTTPException(
            status_code=422, detail=f"Unknown reason '{reason}'. One of: {list(REASONS)}.",
        )
    if not core.is_manager(user):
        raise core.forbidden("The maintenance list needs knowledge:manage.")
    vis, params = core.visible_pages_clause(user, "p")
    async with core._tenant_session() as db:
        rows = (await db.execute(
            text(
                "SELECT p.id, p.title, p.space_id, s.name AS space_name, "
                "p.owner_email, p.review_at, p.updated_at, "
                f"{_EXPIRED} AS is_expired, {_UNOWNED} AS is_unowned, "
                f"{_STALE} AS is_stale "
                "FROM kb_pages p JOIN kb_spaces s "
                "ON s.id = p.space_id AND s.organization_id = p.organization_id "
                f"WHERE p.state = 'published' AND s.kind <> 'me' AND {vis} AND {_FILTERS[reason]} "
                "ORDER BY (CASE WHEN "
                f"{_EXPIRED} THEN 0 WHEN {_UNOWNED} THEN 1 ELSE 2 END), "
                f"(CASE WHEN {_EXPIRED} THEN p.review_at END) ASC NULLS LAST, p.id "
            ),
            params,
        )).all()
    counts = {
        "expired": sum(1 for r in rows if r.is_expired),
        "unowned": sum(1 for r in rows if r.is_unowned),
        "stale": sum(1 for r in rows if r.is_stale),
    }
    pages = [
        {
            "id": str(r.id), "title": r.title, "space_id": str(r.space_id),
            "space_name": r.space_name, "owner_email": r.owner_email,
            "review_at": core._iso(r.review_at), "updated_at": core._iso(r.updated_at),
            "reasons": [
                name for name, on in (
                    ("expired", r.is_expired), ("unowned", r.is_unowned),
                    ("stale", r.is_stale),
                ) if on
            ],
        }
        for r in rows[:limit]
    ]
    return {"pages": pages, "counts": counts, "scope": "org"}
