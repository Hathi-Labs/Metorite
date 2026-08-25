"""Org administration — groups (the teams that scope Centers, rooms, agents).

Spec: ``project-docs/specs/department_centers.md`` §3 Phase B, over the
``org_group`` / ``org_group_member`` tables from
``groups_sessions_authority.md`` §1.

Groups SCOPE, they never AUTHORIZE (the spec's rule): membership decides what
a person *shares* — rooms via ``group:<slug>`` participant subjects, team
agent instances, a Center's data slice — while permissions keep coming from
roles and overrides. The one deliberate bridge is the membership-add option
that also grants the matching ``center.<slug>`` feature override (Phase B's
"one admin action"): it writes through the same ``user_permission_override``
table the member editor uses, with the same provenance columns, so the member
access screen explains it like any other override.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from acb_auth import UserContext, require_permission
from fastapi import Depends, HTTPException
from gateway.routes.admin._common import (
    _log,
    _tenant_session,
    get_member,
    get_org_id,
    invalidate_for,
    record_admin_change,
    require_admin_user,
    router,
)
from pydantic import BaseModel, Field
from sqlalchemy import text

#: The six groups whose slug pairs 1:1 with a `center.<slug>` feature
#: (department_centers.md §1 naming rule 1). These are seeded by migration —
#: the pairing is what makes "grant a department" one admin action — so they
#: cannot be deleted from the UI, same as system roles.
CENTER_GROUP_SLUGS: tuple[str, ...] = (
    "sales", "marketing", "finance", "operations", "people", "company",
)

MEMBER_ROLES = ("lead", "member")
_SLUG_MAX = 48


# ── Models ──────────────────────────────────────────────────────────────────

class GroupMemberEntry(BaseModel):
    email: str
    display_name: str = ""
    #: `lead` manages this group's membership; it is group-scoped, not a
    #: permission bundle (groups_sessions_authority.md §1).
    role: str = "member"
    added_by: str = ""
    added_at: str = ""


class GroupEntry(BaseModel):
    slug: str
    display_name: str
    description: str = ""
    #: True when the slug pairs with a `center.<slug>` feature — the UI shows
    #: the grant-access toggle and hides delete for these.
    is_center: bool = False
    member_count: int = 0
    members: list[GroupMemberEntry] = Field(default_factory=list)


class GroupCreate(BaseModel):
    slug: str
    display_name: str = ""
    description: str = ""


class GroupPatch(BaseModel):
    display_name: str | None = None
    description: str | None = None


class GroupMemberAdd(BaseModel):
    email: str
    role: str = "member"
    #: Also grant the member the matching `center.<slug>` feature override —
    #: Phase B's "one admin action" backfill. Only meaningful when the group
    #: slug names a Center; ignored otherwise.
    grant_center_access: bool = True


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    return "" if value is None else str(value)


def _clean_slug(raw: str) -> str:
    slug = (raw or "").strip().lower().replace(" ", "-")
    if not slug or len(slug) > _SLUG_MAX:
        raise HTTPException(
            status_code=400, detail=f"slug must be 1-{_SLUG_MAX} characters."
        )
    if not all(c.isalnum() or c in "_-" for c in slug):
        raise HTTPException(
            status_code=400,
            detail="slug may contain only letters, digits, '_' and '-'.",
        )
    return slug


async def _get_group(db: Any, org_id: str, slug: str) -> dict[str, Any]:
    """Fetch one group row by slug, or 404."""
    row = (
        await db.execute(
            text(
                "SELECT id::text AS id, slug, display_name, description "
                "  FROM org_group "
                " WHERE organization_id = CAST(:org AS uuid) AND slug = :slug"
            ),
            {"org": org_id, "slug": slug},
        )
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"No group '{slug}'.")
    return dict(row)


async def _members_of(db: Any, group_id: str) -> list[GroupMemberEntry]:
    rows = (
        await db.execute(
            text(
                "SELECT u.email, u.display_name, gm.role, gm.added_by, gm.added_at "
                "  FROM org_group_member gm "
                "  JOIN app_user u ON u.id = gm.user_id "
                " WHERE gm.group_id = CAST(:gid AS uuid) "
                " ORDER BY gm.role, u.email"
            ),
            {"gid": group_id},
        )
    ).mappings().all()
    return [
        GroupMemberEntry(
            email=r["email"],
            display_name=r["display_name"] or "",
            role=r["role"],
            added_by=r["added_by"] or "",
            added_at=_iso(r["added_at"]),
        )
        for r in rows
    ]


async def _entry(db: Any, group: dict[str, Any]) -> GroupEntry:
    members = await _members_of(db, group["id"])
    return GroupEntry(
        slug=group["slug"],
        display_name=group["display_name"],
        description=group["description"] or "",
        is_center=group["slug"] in CENTER_GROUP_SLUGS,
        member_count=len(members),
        members=members,
    )


# ── List ────────────────────────────────────────────────────────────────────

@router.get("/groups", summary="List groups with their members")
async def list_groups(
    admin: UserContext = Depends(require_admin_user),
) -> list[GroupEntry]:
    """Every group, with members inline.

    Readable at the package floor (``admin:members:read``, the same seam as
    the roster) — the org has tens of people, so the full expansion is cheap
    and saves the UI a per-group round trip.
    """
    async with _tenant_session() as db:
        org_id = await get_org_id(db, admin)
        rows = (
            await db.execute(
                text(
                    "SELECT g.id::text AS id, g.slug, g.display_name, g.description "
                    "  FROM org_group g "
                    " WHERE g.organization_id = CAST(:org AS uuid) "
                    " ORDER BY g.slug"
                ),
                {"org": org_id},
            )
        ).mappings().all()
        out = [await _entry(db, dict(r)) for r in rows]
    return out


# ── CRUD ────────────────────────────────────────────────────────────────────

@router.post("/groups", summary="Create a group",
             dependencies=[require_permission("admin:members:manage")])
async def create_group(
    req: GroupCreate,
    admin: UserContext = Depends(require_admin_user),
) -> GroupEntry:
    slug = _clean_slug(req.slug)
    async with _tenant_session() as db:
        org_id = await get_org_id(db, admin)
        clash = (
            await db.execute(
                text(
                    "SELECT 1 FROM org_group "
                    " WHERE organization_id = CAST(:org AS uuid) AND slug = :slug"
                ),
                {"org": org_id, "slug": slug},
            )
        ).first()
        if clash:
            raise HTTPException(
                status_code=409, detail=f"Group '{slug}' already exists."
            )
        await db.execute(
            text(
                "INSERT INTO org_group (organization_id, slug, display_name, "
                "                       description, created_by) "
                "VALUES (CAST(:org AS uuid), :slug, :name, :desc, :by)"
            ),
            {"org": org_id, "slug": slug,
             "name": (req.display_name or slug).strip() or slug,
             "desc": req.description or "", "by": admin.email},
        )

    _log.info("group_created", slug=slug, by=admin.email)
    record_admin_change(admin.email, "org.group_created", f"group:{slug}")
    return GroupEntry(
        slug=slug,
        display_name=(req.display_name or slug).strip() or slug,
        description=req.description or "",
        is_center=slug in CENTER_GROUP_SLUGS,
    )


@router.patch("/groups/{slug}", summary="Rename or re-describe a group",
              dependencies=[require_permission("admin:members:manage")])
async def update_group(
    slug: str,
    patch: GroupPatch,
    admin: UserContext = Depends(require_admin_user),
) -> GroupEntry:
    """Edit ``display_name`` / ``description`` only.

    The slug is deliberately immutable: it is the wire reference everywhere —
    ``group:<slug>`` participant subjects, ``t:<slug>`` agent instance keys,
    the ``center.<slug>`` pairing — and renaming it would orphan all three.
    """
    async with _tenant_session() as db:
        org_id = await get_org_id(db, admin)
        group = await _get_group(db, org_id, slug)
        await db.execute(
            text(
                "UPDATE org_group SET "
                "  display_name = COALESCE(:name, display_name), "
                "  description  = COALESCE(:desc, description), "
                "  updated_at   = now() "
                " WHERE id = CAST(:gid AS uuid)"
            ),
            {"name": patch.display_name, "desc": patch.description,
             "gid": group["id"]},
        )
        group = await _get_group(db, org_id, slug)
        entry = await _entry(db, group)

    _log.info("group_updated", slug=slug, by=admin.email)
    record_admin_change(admin.email, "org.group_updated", f"group:{slug}")
    return entry


@router.delete("/groups/{slug}", summary="Delete a group",
               dependencies=[require_permission("admin:members:manage")])
async def delete_group(
    slug: str,
    admin: UserContext = Depends(require_admin_user),
) -> dict[str, str]:
    """Delete an empty, non-Center group.

    The six Center groups are the platform pairing (`center.<slug>` feature ↔
    group of the same slug) and are refused, same as system roles. A group
    that still has members is refused too: silently unassigning N people —
    and silently emptying every room shared to ``group:<slug>`` — is a bigger
    action than the admin asked for.
    """
    async with _tenant_session() as db:
        org_id = await get_org_id(db, admin)
        group = await _get_group(db, org_id, slug)
        if slug in CENTER_GROUP_SLUGS:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"'{slug}' is a Center group — it pairs with the "
                    f"center.{slug} feature and cannot be deleted."
                ),
            )
        member_count = (
            await db.execute(
                text(
                    "SELECT count(*) FROM org_group_member "
                    " WHERE group_id = CAST(:gid AS uuid)"
                ),
                {"gid": group["id"]},
            )
        ).scalar() or 0
        if int(member_count):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"{member_count} member(s) are still in '{slug}'. "
                    "Remove them first."
                ),
            )
        await db.execute(
            text("DELETE FROM org_group WHERE id = CAST(:gid AS uuid)"),
            {"gid": group["id"]},
        )

    _log.info("group_deleted", slug=slug, by=admin.email)
    record_admin_change(admin.email, "org.group_deleted", f"group:{slug}")
    return {"status": "deleted", "slug": slug}


# ── Membership ──────────────────────────────────────────────────────────────

@router.post("/groups/{slug}/members", summary="Add a member to a group",
             dependencies=[require_permission("admin:members:manage")])
async def add_group_member(
    slug: str,
    req: GroupMemberAdd,
    admin: UserContext = Depends(require_admin_user),
) -> dict[str, Any]:
    """Add (or re-role) a member; optionally grant the Center feature.

    Adding to one of the six Center groups with ``grant_center_access`` (the
    default) also writes an ``allow feature:center.<slug>`` override — the
    same row, columns, and provenance the member editor's override screen
    uses. Because that is an access change, it additionally requires
    ``admin:access:manage`` (the override endpoint's gate); membership alone
    needs only ``admin:members:manage``. The insert is ON CONFLICT DO
    NOTHING: an existing override for the permission — including an explicit
    deny — is an admin decision this shortcut must not silently flip.
    """
    if req.role not in MEMBER_ROLES:
        raise HTTPException(
            status_code=400, detail=f"role must be one of {list(MEMBER_ROLES)}."
        )
    wants_grant = bool(req.grant_center_access) and slug in CENTER_GROUP_SLUGS
    if wants_grant and not admin.has_permission("admin:access:manage"):
        raise HTTPException(
            status_code=403,
            detail=(
                "Granting Center access writes a permission override, which "
                "needs 'admin:access:manage'. Add the member without the "
                "grant, or ask an access admin."
            ),
        )

    async with _tenant_session() as db:
        org_id = await get_org_id(db, admin)
        group = await _get_group(db, org_id, slug)
        member = await get_member(db, org_id, req.email)

        await db.execute(
            text(
                # H6 slice 4 (D48) DUAL-WRITE: shadow `user_identity_id` via the
                # lower(email) bridge so migration 184's column stays current.
                # app_user.id stays authoritative; this only READS user_identity.
                "INSERT INTO org_group_member (group_id, user_id, role, added_by, user_identity_id) "
                "VALUES (CAST(:gid AS uuid), CAST(:uid AS uuid), :role, :by, "
                "        (SELECT ui.id FROM user_identity ui "
                "           JOIN app_user au ON lower(au.email) = lower(ui.email) "
                "          WHERE au.id = CAST(:uid AS uuid))) "
                "ON CONFLICT (group_id, user_id) DO UPDATE SET role = EXCLUDED.role"
            ),
            {"gid": group["id"], "uid": member["id"], "role": req.role,
             "by": admin.email},
        )

        granted = False
        if wants_grant:
            result = await db.execute(
                text(
                    # H6 slice 4 (D48) DUAL-WRITE: shadow `user_identity_id` via
                    # the lower(email) bridge so migration 184's column stays
                    # current. app_user.id stays authoritative; READS user_identity.
                    "INSERT INTO user_permission_override "
                    "  (user_id, permission, effect, reason, set_by, user_identity_id) "
                    "VALUES (CAST(:uid AS uuid), :perm, 'allow', :reason, :by, "
                    "        (SELECT ui.id FROM user_identity ui "
                    "           JOIN app_user au ON lower(au.email) = lower(ui.email) "
                    "          WHERE au.id = CAST(:uid AS uuid))) "
                    "ON CONFLICT (user_id, permission) DO NOTHING"
                ),
                {"uid": member["id"], "perm": f"feature:center.{slug}",
                 "reason": f"group membership: {slug}", "by": admin.email},
            )
            granted = bool(getattr(result, "rowcount", 0))

    invalidate_for(member["email"])
    _log.info("group_member_added", group=slug, email=member["email"],
              role=req.role, by=admin.email, center_access_granted=granted)
    record_admin_change(admin.email, "org.group_member_added",
                        f"group:{slug}", email=member["email"], role=req.role,
                        center_access_granted=granted)
    return {
        "status": "added",
        "slug": slug,
        "email": member["email"],
        "role": req.role,
        # False either because this is not a Center group, the admin opted
        # out, or an override for the permission already existed.
        "center_access_granted": granted,
    }


@router.delete("/groups/{slug}/members/{email}",
               summary="Remove a member from a group",
               dependencies=[require_permission("admin:members:manage")])
async def remove_group_member(
    slug: str,
    email: str,
    admin: UserContext = Depends(require_admin_user),
) -> dict[str, str]:
    """Remove the membership row.

    Deliberately does NOT auto-revoke the `feature:center.<slug>` override
    the add path may have granted: revoking access is an explicit admin
    decision, made where every other access decision is made — the member's
    access editor — not a side effect of a roster change. (Leaving the group
    DOES immediately remove the member from rooms shared to
    ``group:<slug>``: those expand membership at read time.)
    """
    async with _tenant_session() as db:
        org_id = await get_org_id(db, admin)
        group = await _get_group(db, org_id, slug)
        member = await get_member(db, org_id, email)
        result = await db.execute(
            text(
                "DELETE FROM org_group_member "
                " WHERE group_id = CAST(:gid AS uuid) "
                "   AND user_id = CAST(:uid AS uuid)"
            ),
            {"gid": group["id"], "uid": member["id"]},
        )
        if not getattr(result, "rowcount", 0):
            raise HTTPException(
                status_code=404,
                detail=f"'{email}' is not a member of '{slug}'.",
            )

    invalidate_for(member["email"])
    _log.info("group_member_removed", group=slug, email=member["email"],
              by=admin.email)
    record_admin_change(admin.email, "org.group_member_removed",
                        f"group:{slug}", email=member["email"])
    return {"status": "removed", "slug": slug, "email": member["email"]}
