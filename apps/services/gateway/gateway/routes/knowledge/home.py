"""The Knowledge home: recent pages and a member's favourites (WS-61 KB-2f).

Spec: ``project-docs/specs/knowledge_base.md`` §21.7, the KB-2f row.

    GET    /knowledge/recent?cursor=&limit=&mine=   recently updated pages
    GET    /knowledge/favorites?cursor=&limit=      the caller's favourites
    POST   /knowledge/pages/{id}/favorite           star a page
    DELETE /knowledge/pages/{id}/favorite           remove the star

**Recently updated** lists what ``core.visible_pages_clause`` shows the
caller, with no archived page: published pages, and the caller's own drafts
and the drafts that name the caller as a reviewer. It orders by
``updated_at DESC, id DESC`` and pages with a keyset cursor, so a page that
changes while the member reads cannot repeat a row or skip one. ``mine``
keeps the pages the caller wrote, for "Created by me".

**A favourite** is one ``kb_favorites`` row. The member is
``core.viewer_email(user)``, never a value from the request (R5, R11). Each
read of the table joins ``kb_pages`` under the one visibility clause, so a
page that becomes hidden drops out of the list at once, and an archived
page waits there until a restore. A star goes through ``core.load_page``
first, so a page the caller cannot see answers 404. A DELETE needs no read,
because it removes the caller's own row only.

Fences: ``tests/unit/test_kb_visibility_one_seam.py`` (each read of
``kb_favorites`` joins ``kb_pages`` under the clause) and, against a real
database, ``tests/unit/test_kb_home_r8.py``.
"""

from __future__ import annotations

import base64
import binascii
from datetime import datetime
from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException, Query, Response
from gateway.routes.knowledge import core
from gateway.routes.knowledge.core import router
from sqlalchemy import text

#: The rows of one answer, by default and at most.
DEFAULT_LIMIT, MAX_LIMIT = 20, 500


def encode_cursor(stamp: datetime, row_id: Any) -> str:
    """The cursor after a row: its time and its id, in URL-safe base64."""
    raw = f"{stamp.isoformat()}|{row_id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str | None) -> tuple[datetime, str] | None:
    """The time and the id of a cursor, or None for no cursor. 422 on a bad one.

    The time goes back as a ``datetime``: the driver binds a parameter by
    its Python type, and it refuses a string for a ``timestamptz``.
    """
    if cursor is None or not cursor.strip():
        return None
    try:
        padded = cursor.strip() + "=" * (-len(cursor.strip()) % 4)
        raw_stamp, row_id = base64.urlsafe_b64decode(padded).decode().split("|", 1)
        stamp = datetime.fromisoformat(raw_stamp)
        if stamp.tzinfo is None:
            raise ValueError("a cursor time names its zone")
    except (ValueError, binascii.Error, UnicodeDecodeError):
        raise HTTPException(status_code=422, detail="'cursor' is not a cursor of this list.") from None
    pid = core.parse_id(row_id)
    if pid is None:
        raise HTTPException(status_code=422, detail="'cursor' is not a cursor of this list.")
    return stamp, pid


def _listed(row: Any) -> dict[str, Any]:
    """One row of a home list: the page with no body, and its space name."""
    out = core.page_dict(row, body=False)
    out["space_name"] = row.space_name
    return out


# ── Recently updated ────────────────────────────────────────────────────────

@router.get("/recent")
async def recent_pages(
    cursor: str | None = Query(None, max_length=200),
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    mine: bool = Query(False, description="True: only the pages the caller wrote"),
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """The live pages the caller sees, newest change first, with a cursor."""
    after = decode_cursor(cursor)
    async with core._tenant_session() as db:
        vis, vparams = core.visible_pages_clause(user, "p")
        params: dict[str, Any] = {**vparams, "limit": limit + 1}
        extra = ""
        if mine:
            extra += " AND p.author_email = :kb_viewer"
        if after is not None:
            extra += (" AND (p.updated_at, p.id) < "
                      "(CAST(:at AS timestamptz), CAST(:after AS uuid))")
            params["at"], params["after"] = after
        rows = (await db.execute(
            text(f"SELECT {core.PAGE_COLUMNS}, s.name AS space_name FROM kb_pages p "
                 "JOIN kb_spaces s ON s.id = p.space_id "
                 "AND s.organization_id = p.organization_id "
                 f"WHERE {vis} AND p.state <> 'archived'{extra} "
                 "ORDER BY p.updated_at DESC, p.id DESC LIMIT :limit"),
            params,
        )).fetchall()
    more = len(rows) > limit
    rows = rows[:limit]
    last = rows[-1] if more and rows else None
    return {
        "pages": [_listed(r) for r in rows],
        "next_cursor": encode_cursor(last.updated_at, last.id) if last is not None else None,
    }


# ── Favourites ──────────────────────────────────────────────────────────────

@router.get("/favorites")
async def list_favorites(
    cursor: str | None = Query(None, max_length=200),
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """The caller's starred pages that the caller still sees, newest star first.

    An archived page stays starred and leaves the list until a restore.
    """
    after = decode_cursor(cursor)
    async with core._tenant_session() as db:
        vis, vparams = core.visible_pages_clause(user, "p")
        params: dict[str, Any] = {**vparams, "limit": limit + 1}
        extra = ""
        if after is not None:
            extra = (" AND (f.created_at, f.page_id) < "
                     "(CAST(:at AS timestamptz), CAST(:after AS uuid))")
            params["at"], params["after"] = after
        rows = (await db.execute(
            text(f"SELECT {core.PAGE_COLUMNS}, s.name AS space_name, "
                 "f.created_at AS starred_at FROM kb_favorites f "
                 "JOIN kb_pages p ON p.id = f.page_id "
                 "AND p.organization_id = f.organization_id "
                 "JOIN kb_spaces s ON s.id = p.space_id "
                 "AND s.organization_id = p.organization_id "
                 "WHERE f.organization_id = CAST(:kb_org AS uuid) "
                 f"AND f.member_email = :kb_viewer AND {vis} "
                 f"AND p.state <> 'archived'{extra} "
                 "ORDER BY f.created_at DESC, f.page_id DESC LIMIT :limit"),
            params,
        )).fetchall()
    more = len(rows) > limit
    rows = rows[:limit]
    last = rows[-1] if more and rows else None
    pages = []
    for row in rows:
        out = _listed(row)
        out["starred_at"] = row.starred_at.isoformat() if row.starred_at else None
        pages.append(out)
    return {
        "pages": pages,
        "next_cursor": encode_cursor(last.starred_at, last.id) if last is not None else None,
    }


@router.post("/pages/{page_id}/favorite")
async def star_page(
    page_id: str,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Star a page the caller sees. A second star changes nothing."""
    me = core.viewer_email(user)
    if not me:
        raise core.forbidden("Knowledge needs a member with an email address.")
    async with core._tenant_session() as db:
        page = await core.load_page(db, user, page_id)
        if page is None:
            raise core.not_found()
        await db.execute(
            text("INSERT INTO kb_favorites (organization_id, member_email, page_id) "
                 "VALUES (CAST(:org AS uuid), :me, CAST(:pid AS uuid)) "
                 "ON CONFLICT DO NOTHING"),
            {"org": core.org_of(user), "me": me, "pid": str(page.id)},
        )
    return {"page_id": str(page.id), "favorite": True}


@router.delete("/pages/{page_id}/favorite", status_code=204)
async def unstar_page(
    page_id: str,
    user: UserContext = Depends(get_current_user),
) -> Response:
    """Remove the caller's star. A page with no star answers 204 too."""
    pid = core.parse_id(page_id)
    if pid is None:
        raise core.not_found()
    async with core._tenant_session() as db:
        await db.execute(
            text("DELETE FROM kb_favorites "
                 "WHERE organization_id = CAST(:org AS uuid) "
                 "AND member_email = :me AND page_id = CAST(:pid AS uuid)"),
            {"org": core.org_of(user), "me": core.viewer_email(user), "pid": pid},
        )
    return Response(status_code=204)
