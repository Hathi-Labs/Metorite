"""The history of a page: one old version with its body (WS-61 KB-2g).

Spec: ``project-docs/specs/knowledge_base.md`` §5.3 and §21.7 (KB-2g).

    GET /knowledge/pages/{id}/versions/{n}   one version, with its title and body

``GET /knowledge/pages/{id}`` lists the versions without their bodies. The
history modal of the app reads one body at a time through this route, to show
an old version and to compare two of them.

The read takes three steps, in this order:

1. ``core.load_page``, through the one visibility rule. A page the caller
   cannot see, or a page of another organization, answers 404.
2. ``core.history_floor``. A version from before the last move to another
   space answers 404, as ``pages.revert_page`` does. So a page written in a
   ``me`` space and then shared does not show its private drafts.
3. The row of ``kb_page_versions``, by organization, page and number.

⚠️ ``tests/unit/test_kb_visibility_one_seam.py`` does not see a read of
``kb_page_versions``. So a read here that skipped step 1 would stay green
there. ``tests/unit/test_kb_history_r8.py`` is the fence: it asks for a
version of a page that the member cannot see, and for a version under the
floor of a moved page, against a real database.
"""

from __future__ import annotations

from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends
from gateway.routes.knowledge import core, pages
from gateway.routes.knowledge.core import router
from sqlalchemy import text


@router.get("/pages/{page_id}/versions/{version}")
async def get_version(
    page_id: str,
    version: int,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """One version of a page the caller can see. 404 under the history floor."""
    async with core._tenant_session() as db:
        page = await core.load_page(db, user, page_id)
        if page is None:
            raise core.not_found()
        space = await pages._space_of(db, user, page)
        if version < core.history_floor(user, page, space):
            # A version from before the last move to another space.
            raise core.not_found()
        row = (await db.execute(
            text("SELECT version, title, body_md, author_email, reason, via, created_at "
                 "FROM kb_page_versions "
                 "WHERE organization_id = CAST(:org AS uuid) "
                 "AND page_id = CAST(:pid AS uuid) AND version = :v"),
            {"org": core.org_of(user), "pid": str(page.id), "v": version},
        )).first()
        if row is None:
            raise core.not_found()
    return {
        "page_id": str(page.id),
        "version": row.version,
        "title": row.title,
        "body_md": row.body_md,
        "author_email": row.author_email,
        "reason": row.reason,
        "via": row.via,
        "created_at": row.created_at.isoformat() if row.created_at is not None else None,
    }
