"""Custom Apps · pins — a viewer's personal "pin to sidebar" bookmark.

Separate from ``app_grants`` (who can access an app — an owner/admin
decision): a pin is the viewer's own preference and needs no grant beyond
already being able to see the app. Registered in ``__init__.py`` BEFORE
``lifecycle`` so the literal ``GET /pins`` route matches before
``lifecycle``'s ``GET /{slug}`` would otherwise swallow it (first-registered
route wins on an ambiguous path in FastAPI/Starlette).
"""

from __future__ import annotations

from acb_auth import UserContext
from fastapi import Depends
from gateway.routes.apps._common import (
    _tenant_session,
    _uid,
    get_app_or_404,
    require_app_user,
    router,
)
from pydantic import BaseModel
from sqlalchemy import text


class PinnedApp(BaseModel):
    slug: str
    name: str
    icon: str = ""


@router.get("/pins", response_model=list[PinnedApp])
async def list_pinned_apps(
    user: UserContext = Depends(require_app_user),
) -> list[PinnedApp]:
    """This viewer's pinned apps, most-recently-pinned first — the sidebar's
    query. Deliberately minimal payload (no manifest/status/etc.).

    A Studio package (format 'manifest', WS-STU A3) is not an App Workshop
    app, so its pin never shows. Fence: test_studio_packages_r8.py."""
    async with _tenant_session() as db:
        rows = (await db.execute(
            text(
                """SELECT a.slug, a.name, a.icon
                   FROM app_pins p
                   JOIN apps a ON a.id = p.app_id
                   WHERE p.user_email = :email
                     AND a.format IS DISTINCT FROM 'manifest'
                   ORDER BY p.created_at DESC"""
            ),
            {"email": _uid(user)},
        )).fetchall()
    return [PinnedApp(slug=r.slug, name=r.name, icon=r.icon or "") for r in rows]


@router.post("/{slug}/pin")
async def pin_app(
    slug: str,
    user: UserContext = Depends(require_app_user),
) -> dict[str, bool]:
    """Pin *slug* for the current viewer. Idempotent — pinning twice is a no-op."""
    async with _tenant_session() as db:
        row, _grants = await get_app_or_404(db, slug, user)
        await db.execute(
            text(
                """INSERT INTO app_pins (app_id, user_email)
                   VALUES (:app_id, :email)
                   ON CONFLICT (app_id, user_email) DO NOTHING"""
            ),
            {"app_id": str(row.id), "email": _uid(user)},
        )
    return {"pinned": True}


@router.delete("/{slug}/pin")
async def unpin_app(
    slug: str,
    user: UserContext = Depends(require_app_user),
) -> dict[str, bool]:
    """Unpin *slug* for the current viewer. Idempotent — unpinning an
    already-unpinned app is a no-op, not a 404."""
    async with _tenant_session() as db:
        row, _grants = await get_app_or_404(db, slug, user)
        await db.execute(
            text(
                "DELETE FROM app_pins WHERE app_id = :app_id AND user_email = :email"
            ),
            {"app_id": str(row.id), "email": _uid(user)},
        )
    return {"pinned": False}
