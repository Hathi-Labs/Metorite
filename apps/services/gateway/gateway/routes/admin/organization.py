"""``PATCH /admin/organization`` — rename the caller's organization.

Only ``display_name`` is editable. The slug is the stable handle the resolver
and every URL look up, and the domain is a sign-in boundary, so neither
changes here.

The ``organization`` table is RLS-exempt, so the tenant session does not scope
this UPDATE. The row is chosen by ``get_org_id`` (the caller's own tenant) and
nothing from the request body can name another one.
"""

from __future__ import annotations

from acb_auth import UserContext, require_permission
from fastapi import Depends, HTTPException
from gateway.routes.admin._common import (
    _log,
    _tenant_session,
    get_org_id,
    record_admin_change,
    require_admin_user,
    router,
)
from pydantic import BaseModel
from sqlalchemy import text

DISPLAY_NAME_MAX = 80


class OrganizationPatch(BaseModel):
    display_name: str


class OrganizationEntry(BaseModel):
    id: str
    slug: str
    display_name: str


def clean_display_name(raw: str) -> str:
    """Trim and validate. Raises ``HTTPException(422)`` with a sentence for the admin."""
    name = " ".join(raw.split())
    if not name:
        raise HTTPException(status_code=422, detail="The organization name cannot be empty.")
    if len(name) > DISPLAY_NAME_MAX:
        raise HTTPException(
            status_code=422,
            detail=f"The organization name is {len(name)} characters; the limit is {DISPLAY_NAME_MAX}.",
        )
    if any(not ch.isprintable() for ch in name):
        raise HTTPException(
            status_code=422,
            detail="The organization name contains a character that cannot be shown.",
        )
    return name


@router.patch(
    "/organization",
    summary="Rename the caller's organization",
    dependencies=[require_permission("admin:settings:manage")],
)
async def update_organization(
    patch: OrganizationPatch,
    admin: UserContext = Depends(require_admin_user),
) -> OrganizationEntry:
    name = clean_display_name(patch.display_name)
    async with _tenant_session() as db:
        org_id = await get_org_id(db, admin)
        row = (
            await db.execute(
                text(
                    "UPDATE organization SET display_name = :name, updated_at = now() "
                    " WHERE id = CAST(:id AS uuid) "
                    "RETURNING id, slug, display_name"
                ),
                {"name": name, "id": org_id},
            )
        ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Organization not found.")

    _log.info("organization_renamed", org=str(row["id"]), by=admin.email)
    record_admin_change(admin.email, "org.renamed", f"org:{row['slug']}", display_name=name)
    return OrganizationEntry(
        id=str(row["id"]), slug=row["slug"], display_name=row["display_name"]
    )
