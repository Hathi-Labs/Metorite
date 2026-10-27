"""``PATCH /admin/organization`` — rename the caller's organization.

Only ``display_name`` is editable. The slug is the stable handle the resolver
and every URL look up, and the domain is a sign-in boundary, so neither
changes here.

The ``organization`` table is RLS-exempt, so the tenant session does not scope
this UPDATE. The row is chosen by ``get_org_id`` (the caller's own tenant) and
nothing from the request body can name another one.

**CP-8 "Rename an organization" (owner request, 2026-10-27).** One rename
changes the display name on both planes. Two things here serve that:

* :func:`rename_org_row` is the ONE UPDATE body. This route and the operator
  door ``PATCH /internal/operator/organizations/{slug}``
  (``routes/operator.py``) both call it, so a rename means one thing
  whichever door does it.
* After the commit, :func:`_mirror_rename_to_console` tells the Customer
  Console, so the Console's ``organization.name`` follows. It is best-effort,
  like the member-removal mirror in ``routes/admin/members.py``.
"""

from __future__ import annotations

from typing import Any

from acb_auth import UserContext, require_permission
from acb_auth.console_resolve import is_wired, rename_org_on_console
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

#: The ONE rename statement. It sets the display name and never the slug.
RENAME_ORG_SQL = (
    "UPDATE organization SET display_name = :name, updated_at = now() "
    " WHERE id = CAST(:id AS uuid) "
    "RETURNING id, slug, display_name"
)


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


async def rename_org_row(db: Any, org_id: str, name: str) -> dict[str, Any] | None:
    """Set one organization's display name. Returns the row, or None.

    ONE copy, two callers: this module's admin route and the operator door in
    ``routes/operator.py``. Each caller opens its own tenant session, cleans
    the name with :func:`clean_display_name` first, and passes both in. This
    function opens nothing and commits nothing.
    """
    row = (
        await db.execute(text(RENAME_ORG_SQL), {"name": name, "id": org_id})
    ).mappings().first()
    if row is None:
        return None
    return {"id": str(row["id"]), "slug": row["slug"], "display_name": row["display_name"]}


async def _mirror_rename_to_console(*, actor_email: str, slug: str, name: str) -> None:
    """Tell the Customer Console about a committed rename. Never raises.

    POST-COMMIT and best-effort, the member-removal mirror's posture. The
    tenant rename has committed, so no Console outcome changes the answer.

    An unwired box has no Console, so this makes no call and logs nothing. A
    wired box that fails logs ``org_rename_mirror_failed`` with the slug and
    the name. The two names then differ until an operator rename writes both
    planes again, and the line holds what that repair needs.

    ⚠️ ``actor_email`` is the AUTHENTICATED ADMIN (R11). The Console derives
    the org from placement and membership, so this names no organization.
    That is also the one way this can go WRONG: an admin who is an active
    Console member of a different org on this deployment renames THAT org.
    So a 200 must name the slug this route renamed. A different slug logs
    ``org_rename_mirror_wrong_org`` at error, with both slugs and the name the
    Console replaced, so an operator can put it back.
    """
    if not is_wired():
        return
    try:
        status, body = await rename_org_on_console(actor_email=actor_email, name=name)
    except Exception as exc:
        # An outage, or an exception type nobody foresaw. Neither may turn a
        # committed rename into a 500.
        _log.warning(
            "org_rename_mirror_failed", slug=slug, name=name, error=str(exc)[:200],
        )
        return
    if status >= 400:
        # A verdict: an actor in two orgs on this box (409), an actor the
        # Console does not hold as an active member (403), a bad name (422).
        _log.warning(
            "org_rename_mirror_failed", slug=slug, name=name, status=status,
            body=str(body)[:200],
        )
        return
    console_slug = body.get("slug")
    if console_slug != slug:
        # The Console renamed an org, and it is not this one. The tenant
        # plane is right, and another org's Console name is now wrong.
        _log.error(
            "org_rename_mirror_wrong_org", slug=slug, console_slug=console_slug,
            name=name, console_previous_name=body.get("previous_name"),
        )


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
        row = await rename_org_row(db, org_id, name)
    if not row:
        raise HTTPException(status_code=404, detail="Organization not found.")

    _log.info("organization_renamed", org=row["id"], by=admin.email)
    record_admin_change(admin.email, "org.renamed", f"org:{row['slug']}", display_name=name)
    await _mirror_rename_to_console(
        actor_email=admin.email or "", slug=row["slug"], name=name,
    )
    return OrganizationEntry(
        id=row["id"], slug=row["slug"], display_name=row["display_name"]
    )
