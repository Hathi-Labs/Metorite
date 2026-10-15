"""The four space routes of ``/knowledge`` (WS-61 KB-1).

Spec: ``project-docs/specs/knowledge_base.md`` §4.1 and §5.3.

    GET   /knowledge/spaces               the spaces the caller can open
    POST  /knowledge/spaces               make a team space (knowledge:manage)
    PATCH /knowledge/spaces/{id}          rename, re-icon, change the purpose
    PUT   /knowledge/spaces/{id}/grants   replace the grants (never on chat)

A space the caller cannot open answers 404, never 403. A space the caller
can open but not manage answers 403 on a write. A ``me`` space gets no grant.
"""

from __future__ import annotations

import re
from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException
from gateway.routes.knowledge import core
from gateway.routes.knowledge.core import router
from pydantic import BaseModel, Field
from sqlalchemy import text

#: A team space slug, after the `team-` prefix is added.
_TEAM_SLUG = re.compile(r"^team-[a-z0-9][a-z0-9-]{0,56}$")

#: The most grants one space holds.
MAX_GRANTS = 100


class GrantIn(BaseModel):
    subject: str = Field(..., min_length=1, max_length=320)
    level: str = Field(..., min_length=1, max_length=20)


class SpaceCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    slug: str | None = Field(None, max_length=63)
    purpose: str = Field("", max_length=300)
    icon: str = Field("", max_length=40)
    grants: list[GrantIn] = Field(default_factory=list, max_length=MAX_GRANTS)


class SpacePatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    purpose: str | None = Field(None, max_length=300)
    icon: str | None = Field(None, max_length=40)


class GrantsPut(BaseModel):
    grants: list[GrantIn] = Field(default_factory=list, max_length=MAX_GRANTS)


def _team_slug(name: str, slug: str | None) -> str:
    """``team-<slug>``, from the slug the caller gave or from the name."""
    base = (slug or name or "").strip().lower()
    if base.startswith("team-"):
        base = base[len("team-"):]
    base = re.sub(r"[^a-z0-9]+", "-", base).strip("-")
    value = f"team-{base}"
    if not _TEAM_SLUG.match(value):
        raise HTTPException(
            status_code=422,
            detail="A space slug is letters, digits and hyphens, at most 56 after 'team-'.",
        )
    return value


@router.get("/spaces")
async def list_spaces(
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """The spaces the caller can open, each with its count of visible pages.

    The first call in an organization makes the ``company`` space, with an
    ``org`` read grant (§4.1).
    """
    async with core._tenant_session() as db:
        await core.ensure_company_space(db, user)
        spaces, sparams = core.readable_spaces_clause(user, "s")
        vis, vparams = core.visible_pages_clause(user, "p")
        rows = (await db.execute(
            text(
                f"SELECT {core.SPACE_COLUMNS}, "
                f"(SELECT count(*) FROM kb_pages p WHERE p.space_id = s.id "
                f"AND p.state <> 'archived' AND {vis}) AS page_count "
                f"FROM kb_spaces s WHERE s.archived_at IS NULL AND {spaces} "
                "ORDER BY CASE s.kind WHEN 'company' THEN 0 WHEN 'team' THEN 1 "
                "WHEN 'agent' THEN 2 ELSE 3 END, lower(s.name)"
            ),
            {**sparams, **vparams},
        )).fetchall()
        by_grant = await core.levels_by_grant(db, user, [str(r.id) for r in rows])
    out = []
    for row in rows:
        level = core.level_on(user, row, by_grant.get(str(row.id))) or "read"
        item = core.space_dict(row, level)
        item["page_count"] = int(row.page_count or 0)
        out.append(item)
    return {"spaces": out}


@router.post("/spaces", status_code=201)
async def create_space(
    body: SpaceCreate,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Make a team space. Needs ``knowledge:manage`` (§5.2)."""
    if not core.is_manager(user):
        raise core.forbidden("Making a space needs knowledge:manage.")
    if not body.name.strip():
        raise HTTPException(status_code=422, detail="A space needs a name.")
    slug = _team_slug(body.name, body.slug)
    grants = core.clean_grants(body.grants)
    async with core._tenant_session() as db:
        made = (await db.execute(
            text(
                "INSERT INTO kb_spaces "
                "(organization_id, slug, name, purpose, icon, kind, owner_email) "
                "VALUES (CAST(:org AS uuid), :slug, :name, :purpose, :icon, "
                "'team', :owner) "
                "ON CONFLICT (organization_id, slug) WHERE kind <> 'me' DO NOTHING "
                "RETURNING id"
            ),
            {"org": core.org_of(user), "slug": slug, "name": body.name.strip(),
             "purpose": body.purpose.strip(), "icon": body.icon.strip(),
             "owner": core.viewer_email(user) or None},
        )).first()
        if made is None:
            raise HTTPException(
                status_code=409, detail=f"A space with the slug '{slug}' exists.",
            )
        await core.write_grants(db, user, str(made.id), grants)
        space = await core.load_space(db, user, str(made.id))
        result = core.space_dict(space, "manage")
        result["grants"] = await core.read_grants(db, user, str(made.id))
    core.audit(user, "knowledge.space_create", result["id"],
               {"slug": slug, "grants": [list(g) for g in grants]})
    return result


@router.patch("/spaces/{space_id}")
async def update_space(
    space_id: str,
    body: SpacePatch,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Rename, re-icon, or change the purpose. Needs ``manage`` on the space."""
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    if not changes:
        raise HTTPException(status_code=422, detail="Nothing to change.")
    if "name" in changes and not str(changes["name"]).strip():
        raise HTTPException(status_code=422, detail="A space needs a name.")
    async with core._tenant_session() as db:
        space = await core.load_space(db, user, space_id)
        if space is None:
            raise core.not_found()
        if await core.space_level(db, user, space) != "manage":
            raise core.forbidden("Changing a space needs manage on it.")
        sets = ", ".join(f"{col} = :{col}" for col in changes)
        await db.execute(
            text(f"UPDATE kb_spaces SET {sets} "
                 "WHERE organization_id = CAST(:org AS uuid) "
                 "AND id = CAST(:sid AS uuid)"),
            {**{k: str(v).strip() for k, v in changes.items()},
             "org": core.org_of(user), "sid": str(space.id)},
        )
        space = await core.load_space(db, user, str(space.id))
    core.audit(user, "knowledge.space_update", str(space.id), {"changed": sorted(changes)})
    return core.space_dict(space, "manage")


@router.put("/spaces/{space_id}/grants")
async def replace_grants(
    space_id: str,
    body: GrantsPut,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Replace the grants of a space. Needs ``manage`` on it. Never on chat.

    A ``me`` space gets no grant (§4.1): to share a page from it, the member
    moves the page.
    """
    grants = core.clean_grants(body.grants)
    async with core._tenant_session() as db:
        space = await core.load_space(db, user, space_id)
        if space is None:
            raise core.not_found()
        if space.kind == "me":
            raise HTTPException(
                status_code=422,
                detail="A 'me' space cannot get grants. Move the page to share it.",
            )
        if await core.space_level(db, user, space) != "manage":
            raise core.forbidden("Changing the grants of a space needs manage on it.")
        # §5.2: to grant `org` on a space is an admin act with a blast radius.
        # A `manage` grant on the space may change its groups and emails, and
        # only `knowledge:manage` may add, change or remove the `org` subject.
        current = await core.read_grants(db, user, str(space.id))
        before = core.org_grant([(g["subject"], g["level"]) for g in current])
        if core.org_grant(grants) != before and not core.is_manager(user):
            raise core.forbidden("Granting 'org' on a space needs knowledge:manage.")
        await db.execute(
            text("DELETE FROM kb_space_grants "
                 "WHERE organization_id = CAST(:org AS uuid) "
                 "AND space_id = CAST(:sid AS uuid)"),
            {"org": core.org_of(user), "sid": str(space.id)},
        )
        await core.write_grants(db, user, str(space.id), grants)
        result = {"space_id": str(space.id),
                  "grants": await core.read_grants(db, user, str(space.id))}
    core.audit(user, "knowledge.space_grants", str(space.id),
               {"grants": [list(g) for g in grants]})
    return result
