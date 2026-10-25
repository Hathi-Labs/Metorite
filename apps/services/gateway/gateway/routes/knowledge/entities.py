"""The entity routes of ``/knowledge`` (WS-61 KC-8a).

Spec: ``project-docs/specs/knowledge_curation.md`` §6.2 to §6.4.

    GET  /knowledge/entities?type=&q=        the directory of one type, or all
    GET  /knowledge/entities/{id}            one entity
    POST /knowledge/entities                 make a product or a topic
    POST /knowledge/entities/by-ref          get or make the entity of an owner row
    POST /knowledge/entities/{id}/page       make the entity page, lazily

Each read goes through ``kb_entities.registry.owner_rules``: the rule of the
app that owns the record (§6.4). A row the caller cannot see, or a row of
another org, answers 404 and never 403. The router gate (``KNOWLEDGE_ENABLED``,
``KNOWLEDGE_ORGS`` and ``feature:knowledge``) runs first, so no new flag is
needed.

A product or a topic needs ``write`` on its space, or ``knowledge:manage``
(``pages.writable_space``). A directory row of another app's record has no
``id`` until something needs the pointer. "Write the page" on such a row
calls ``by-ref`` first, which reads the owner row under its rule and then
makes the pointer, and then calls the page route with the entity id. That is
the one door, and the page route takes an entity id only.

An entity page is a draft made through the same seam as ``POST
/knowledge/pages``. A Knowledge-owned entity keeps its page in its own space.
The page of a person, a customer, a workstream, a workflow or the company
goes into the org's ``company`` space by default, or into another space the
caller names and can write. It never goes into a ``me`` space, because the
one page of an entity serves the whole org (§6.2). Nothing makes pages in
bulk: a page exists only through this route, or through the first accepted
fact of KC-9.

Fences: ``tests/unit/test_kc_entities.py`` and, against a real database,
``tests/unit/test_kc_entities_r8.py``.
"""

from __future__ import annotations

from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException, Query
from gateway.kb_entities import registry, resolver
from gateway.routes.knowledge import core, pages
from gateway.routes.knowledge.core import router
from pydantic import BaseModel, Field
from sqlalchemy import text

#: The most rows one list answers.
LIST_MAX = 200


class EntityCreate(BaseModel):
    #: ``product`` or ``topic`` (``registry.CREATABLE_TYPES``).
    type: str = Field(..., max_length=20)
    name: str = Field(..., max_length=400)
    space_id: str = Field(..., max_length=64)
    aliases: list[str] = Field(default_factory=list, max_length=200)


class EntityRef(BaseModel):
    #: A type whose record another app owns (``registry.OWNED_ELSEWHERE``).
    type: str = Field(..., max_length=20)
    ref_id: str = Field(..., max_length=64)


class EntityPage(BaseModel):
    #: The space of the page, for an entity of another app. None is the
    #: org's ``company`` space. A Knowledge-owned entity keeps its page in
    #: its own space.
    space_id: str | None = Field(None, max_length=64)


def _kind(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    kind = value.strip().lower()
    if kind not in registry.ENTITY_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown entity type '{value}'. One of: {list(registry.ENTITY_TYPES)}.",
        )
    return kind


@router.get("/entities")
async def list_entities(
    type: str | None = Query(None, max_length=20),
    q: str | None = Query(None, max_length=120),
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """The directory: the owner rows of a type the caller may see.

    With no ``type``, each type in the order of §6.1. ``q`` matches the
    folded name or an alias, in SQL before the cap. ``truncated`` is true
    when a rule read ``registry.DIRECTORY_CAP`` rows, so a row may be missing.
    """
    kind = _kind(type)
    wanted = resolver.fold(q) if q else ""
    out: list[dict[str, Any]] = []
    truncated = False
    async with core._tenant_session() as db:
        for each in (kind,) if kind else registry.ENTITY_TYPES:
            rows, cut = await registry.directory_rows(db, user, each, q=wanted or None)
            truncated = truncated or cut
            out.extend(r for r in rows if resolver.matches_query(r, wanted))
    return {"entities": out[:LIST_MAX], "truncated": truncated or len(out) > LIST_MAX}


@router.get("/entities/{entity_id}")
async def get_entity(
    entity_id: str, user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    async with core._tenant_session() as db:
        row = await registry.get_entity(db, user, entity_id)
    if row is None:
        raise core.not_found()
    return row


@router.post("/entities", status_code=201)
async def create_entity(
    body: EntityCreate, user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Make a product or a topic in a space the caller can write in."""
    kind = _kind(body.type)
    if kind not in registry.CREATABLE_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"Only these types are made here: {list(registry.CREATABLE_TYPES)}.",
        )
    name = registry.clean_name(body.name)
    aliases = registry.clean_aliases(body.aliases)
    async with core._tenant_session() as db:
        space = await pages.writable_space(db, user, body.space_id)
        made = await registry.ensure_entity(
            db, registry.KNOWLEDGE_TABLE, None, type=kind, name=name,
            space_id=str(space.id), aliases=aliases,
        )
        row = await registry.get_entity(db, user, made) if made else None
    if row is None:  # pragma: no cover — the space was checked in this session
        raise core.not_found()
    return row


@router.post("/entities/by-ref")
async def entity_by_ref(
    body: EntityRef, user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Get or make the entity of one owner row, under its owner rule.

    404 for a row the caller cannot see or a row of another org, and then
    nothing is written. 422 for a Knowledge-owned type, which has no owner
    row elsewhere.
    """
    kind = _kind(body.type)
    if kind not in registry.OWNED_ELSEWHERE:
        raise HTTPException(
            status_code=422,
            detail=f"by-ref takes one of: {list(registry.OWNED_ELSEWHERE)}.",
        )
    async with core._tenant_session() as db:
        row = await registry.ensure_by_ref(db, user, kind, body.ref_id)
    if row is None:
        raise core.not_found()
    return row


async def _company_space_id(db: Any, user: UserContext) -> str | None:
    """The org's ``company`` space, made on first need as ``GET /spaces`` does."""
    await core.ensure_company_space(db, user)
    row = (await db.execute(
        text("SELECT s.id FROM kb_spaces s "
             "WHERE s.organization_id = CAST(:org AS uuid) "
             "AND s.kind = 'company' AND s.slug = :slug"),
        {"org": core.org_of(user), "slug": core.COMPANY_SLUG},
    )).first()
    return str(row.id) if row is not None else None


@router.post("/entities/{entity_id}/page", status_code=201)
async def create_entity_page(
    entity_id: str, body: EntityPage | None = None,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Make the page of one entity, as a draft titled with its live name.

    404 for an entity the caller cannot see. 409 when the entity has a page,
    seen or not, so a page is never replaced. A Knowledge-owned entity's page
    lands in its own space. Another entity's page lands in ``space_id``, or
    in the ``company`` space, and never in a ``me`` space (422).
    """
    wanted_space = body.space_id if body is not None else None
    async with core._tenant_session() as db:
        entity = await registry.get_entity(db, user, entity_id)
        if entity is None:
            raise core.not_found()
        if entity["ref_table"] == registry.KNOWLEDGE_TABLE:
            if wanted_space is not None and core.parse_id(wanted_space) != entity["space_id"]:
                raise HTTPException(
                    status_code=422,
                    detail="The page of this entity lives in the entity's own space.",
                )
            space_id = entity["space_id"]
        else:
            space_id = wanted_space or await _company_space_id(db, user)
            if space_id is None:  # pragma: no cover — ensure_company_space made it
                raise core.not_found()
        space = await pages.writable_space(db, user, space_id)
        if entity["ref_table"] != registry.KNOWLEDGE_TABLE and space.kind == "me":
            raise HTTPException(
                status_code=422,
                detail="An entity page serves the whole org, so it never lives in a 'me' space.",
            )
        if await registry.lock_page_slot(db, user, entity["id"]) is not None:
            raise HTTPException(status_code=409, detail="This entity has a page.")
        row = await pages.insert_draft(
            db, user, space, parent_id=None, title=registry.page_title(entity["name"]),
            body_md="", reviewers=[],
            origin={"kind": "entity", "entity_id": entity["id"]},
        )
        page = await pages._written(db, user, row, "create")
        await registry.set_page(db, user, entity["id"], str(row.id))
        entity = await registry.get_entity(db, user, entity["id"])
    return {"entity": entity, "page": page}
