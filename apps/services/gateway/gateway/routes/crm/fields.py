"""CRM · fields — what each custom field means (WS-53 CRM-Q4, CRM-C4a).

Spec: ``project-docs/specs/crm_platform.md`` §18.3 step 2 item 3, §18.5 and
§20.7 items 11 and 12.

    GET  /crm/fields/{entity}
    GET  /crm/fields/{entity}?layout=1
    GET  /crm/fields-search?q=<words>[&module=<ApiName>]

``entity`` is one of ``leads``, ``contacts``, ``organizations`` and ``deals``.
The route reads ``crm_field_defs``, which the sync and the field studio write,
and it returns the rows in order of ``position``. It lets the UI show a custom
field with its label, its order and its type. Without it, the UI has only the
key.

**The plain read lists the custom keys.** A row with ``is_system = true``
overlays a fixed column (CRM-C4a), so it is not a custom key and the plain
read leaves it out. A retired field stays in the list with ``retired: true``,
because a record keeps its value.

**``?layout=1`` is the one layout** (§20.7 item 12). It returns the sections,
the system fields of :data:`core.SYSTEM_FIELDS` and the custom fields, in
order. A system field with no stored row takes its label and its place from
``SYSTEM_FIELDS``. The forms, the Fields tab, the Columns control, the filter
bar and the CSV export read this order.

**The fill rank** (CRM-Q17a-1, §18.9.3). For an org on
``CRM_FILL_RANK_ORGS``, each definition carries ``fill`` and ``distinct``
from ``crm_field_fill``, which only the sync writes. The layout also carries
``empty_columns``, from ``fill_rank.empty_columns``, the one function that
the CSV export reads too. The plain read is a bare list, so it carries the
numbers on each row and no ``empty_columns``. While the numbers are stale
(``fill_rank.stale_entities``), ``empty_columns`` is ``[]``. For any other
org each of these is ``null``. No request computes them. A mirrored module
(``module:<ApiName>``) carries ``fill`` and ``distinct`` on each row too.

**The field search** (CRM-Q34-6, task 3699). ``GET /crm/fields-search``
finds the fields of one mirrored module, or of each mirrored module, by the
words of ``q``. A field matches when each word of ``q`` hits a word of its
label or of its key (:func:`field_matches`). ``crm_sync.terms.words`` splits
both, so a key splits at ``_`` and at each change to upper case. ONE
statement reads the definitions of the mirrored set, and ONE more reads the
fill of the modules with a hit (``fill_rank.fills_of``). An org outside
``CRM_MODULE_FIND_ORGS`` gets 404 ``module_find_off`` before any other check,
and the route opens no session. ``q`` never reaches the SQL text, and the
module name must be in the mirrored set first (``modules._require_module``).

Read only. Any member that the CRM access rule admits may read it. The
organization comes from the server and the session binds it, so the route
never takes a tenant from the request (R5). The writes live in
``field_studio.py``.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException, Query
from gateway.crm_sync.terms import words
from gateway.routes.crm.access import ADMIN_PERMISSION, read_settings
from gateway.routes.crm.core import (
    ENTITIES,
    FIELD_ENTITY_BY_SLUG,
    SYSTEM_FIELDS,
    _tenant_session,
    from_jsonb,
    router,
)
from gateway.routes.crm.fill_rank import annotate, empty_columns, fills_for, fills_of
from gateway.routes.crm.flags import field_studio_allowed, module_find_allowed
from pydantic import BaseModel
from sqlalchemy import text

#: The API slug of each entity → the value that ``crm_field_defs.entity`` holds.
#: One map, in ``core``, because the list filters read it too.
ENTITY_BY_SLUG = FIELD_ENTITY_BY_SLUG

#: ⚠️ ``default_value`` is read as its JSON text. A scalar jsonb value comes
#: back from psycopg as the Python value and from asyncpg as JSON text, so a
#: default of ``"123"`` would read as the number 123 under one of them.
DEF_COLUMNS = (
    "key, label, type, options, source, provider_api_name, position, visible, "
    "section_id, required, help_text, default_value::text AS default_value, "
    "lookup_target, is_system, retired_at"
)
_COLUMNS = DEF_COLUMNS

#: The custom keys of one entity. A row that overlays a fixed column
#: (``is_system``) is not one (§20.7 item 11).
_FIELDS_SQL = (
    f"SELECT {_COLUMNS} FROM crm_field_defs WHERE entity = :entity "
    "AND NOT is_system ORDER BY position, key"
)

#: Every row of one entity, the system overlays too. The layout reads it.
_LAYOUT_FIELDS_SQL = (
    f"SELECT {_COLUMNS} FROM crm_field_defs WHERE entity = :entity "
    "ORDER BY position, key"
)

_SECTIONS_SQL = (
    "SELECT id, label, position FROM crm_field_sections WHERE entity = :entity "
    "ORDER BY position, label, id"
)


class FieldDef(BaseModel):
    key: str
    label: str
    type: str
    options: list[Any] | None = None
    position: int
    visible: bool
    source: Literal["zoho", "native"]
    api_name: str | None = None
    #: CRM-C4a. ``None`` puts the field in no section.
    section_id: str | None = None
    required: bool = False
    help_text: str | None = None
    default_value: Any = None
    #: The entity slug that a lookup names.
    lookup_target: str | None = None
    #: True for a fixed column (``core.SYSTEM_FIELDS``). Only the layout lists one.
    is_system: bool = False
    #: True after ``DELETE /crm/fields/{entity}/{key}``. A record keeps its value.
    retired: bool = False
    #: CRM-Q17a-1: the share of records that fill the field, from 0 to 1.
    #: ``None`` outside ``CRM_FILL_RANK_ORGS``, with no record, or no numbers yet.
    fill: float | None = None
    #: CRM-Q17a-1: the count of different filled values. ``None`` as ``fill``.
    distinct: int | None = None


class FieldSection(BaseModel):
    id: str
    label: str
    position: int


class FieldLayout(BaseModel):
    """The one layout of an entity (§20.7 item 12)."""

    entity: str
    sections: list[FieldSection]
    fields: list[FieldDef]
    #: True when ``CRM_FIELD_STUDIO_ORGS`` allows the caller's organization.
    #: The UI reads it, because it cannot read the setting (CRM-C4b, C4c).
    studio: bool = False
    #: True only when the caller may write fields: the flag allows the org,
    #: the caller holds ``admin:access:manage``, and the mode is Native.
    studio_admin: bool = False
    #: CRM-Q17a-1: the standard keys that no record fills
    #: (``fill_rank.empty_columns``). ``None`` outside ``CRM_FILL_RANK_ORGS``,
    #: and ``[]`` while the numbers are stale.
    empty_columns: list[str] | None = None


def _source(value: str | None) -> Literal["zoho", "native"]:
    return "zoho" if value == "zoho" else "native"


def _uuid_text(value: Any) -> str | None:
    return str(value) if value is not None else None


def field_wire(row: Any, *, slug: str | None = None) -> FieldDef:
    """A ``crm_field_defs`` row → its wire model.

    ``getattr`` with a default reads a row from before migration 260 (and a
    test fake) as a plain custom field. A system row takes ``required`` from
    the entity: a field that the gateway needs stays required (§20.7 item 3).
    """
    is_system = bool(getattr(row, "is_system", False))
    required = bool(getattr(row, "required", False))
    if is_system and slug is not None:
        required = row.key in ENTITIES[slug].required
    options = from_jsonb(getattr(row, "options", None))
    return FieldDef(
        key=row.key, label=row.label, type=row.type,
        options=options if isinstance(options, list) else None,
        position=int(row.position or 0), visible=bool(row.visible),
        source=_source(getattr(row, "source", None)),
        api_name=getattr(row, "provider_api_name", None),
        section_id=_uuid_text(getattr(row, "section_id", None)),
        required=required,
        help_text=getattr(row, "help_text", None),
        default_value=from_jsonb(getattr(row, "default_value", None)),
        lookup_target=getattr(row, "lookup_target", None),
        is_system=is_system,
        retired=getattr(row, "retired_at", None) is not None,
    )


def system_wire(slug: str, index: int, field: Any) -> FieldDef:
    """A system field with no stored row: its label and place from
    ``SYSTEM_FIELDS``."""
    return FieldDef(
        key=field.key, label=field.label, type=field.type, options=None,
        position=index, visible=True, source="native", api_name=None,
        required=field.key in ENTITIES[slug].required,
        lookup_target=field.lookup_target, is_system=True,
    )


def checked_slug(entity: str) -> str:
    """The stored entity word of *entity*, or 422."""
    stored = ENTITY_BY_SLUG.get(entity)
    if stored is None:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown entity '{entity}'. One of: {list(ENTITY_BY_SLUG)}.",
        )
    return stored


async def studio_flags(db: Any, user: UserContext) -> dict[str, bool]:
    """``studio`` and ``studio_admin`` of :class:`FieldLayout` for *user*.

    The organization comes from the session (R5). The flag has one reader,
    ``flags.field_studio_allowed``.
    """
    org = user.organization_id
    studio = field_studio_allowed(org)
    admin = False
    if studio and user.has_permission(ADMIN_PERMISSION):
        row = await read_settings(db, str(org))
        admin = getattr(row, "mode", None) == "native"
    return {"studio": studio, "studio_admin": admin}


async def read_layout(db: Any, slug: str) -> FieldLayout:
    """The sections, the system fields and the custom fields of *slug*, in order.

    The studio routes answer with it too, so a write returns what the next
    read shows.
    """
    stored = ENTITY_BY_SLUG[slug]
    rows = (await db.execute(text(_LAYOUT_FIELDS_SQL), {"entity": stored})).fetchall()
    sections = (await db.execute(text(_SECTIONS_SQL), {"entity": stored})).fetchall()
    kept = {r.key: r for r in rows if getattr(r, "is_system", False)}
    fields = [
        field_wire(r, slug=slug) for r in rows if not getattr(r, "is_system", False)
    ]
    for index, field in enumerate(SYSTEM_FIELDS[slug]):
        row = kept.get(field.key)
        if row is None:
            fields.append(system_wire(slug, index, field))
        else:
            # The type and the target of a fixed column never change.
            fields.append(field_wire(row, slug=slug).model_copy(update={
                "type": field.type, "lookup_target": field.lookup_target,
            }))
    fields.sort(key=lambda f: (f.position, not f.is_system, f.key))
    return FieldLayout(
        entity=slug,
        sections=[
            FieldSection(id=str(s.id), label=s.label, position=int(s.position or 0))
            for s in sections
        ],
        fields=fields,
    )


async def custom_defs(db: Any, slug: str) -> list[FieldDef]:
    """The custom fields of one core entity, by position. A system overlay is
    not one, and a retired field is marked. ``GET /crm/fields/{entity}`` and
    the typed aggregate (``field_sql.core_scope``, CRM-Q25) both read it."""
    rows = (await db.execute(
        text(_FIELDS_SQL), {"entity": ENTITY_BY_SLUG[slug]},
    )).fetchall()
    return [field_wire(r) for r in rows]


#: The entity word of a generic module: ``module:<ApiName>`` (CRM-Q25).
MODULE_PREFIX = "module:"


async def module_field_defs(module: str, org: str | None = None) -> list[FieldDef]:
    """The field definitions of a mirrored module, or 404 ``unknown_module``.

    The one resolver of ``modules.py`` (``resolve_module``) runs first. A
    label gives its API name (CRM-Q34-12), and a name outside the mirrored
    set never reaches a statement. The import is local because
    ``modules.py`` imports :class:`FieldDef` from here. With *org* on
    ``CRM_FILL_RANK_ORGS``, each definition carries ``fill`` and ``distinct``
    (CRM-Q34-6). *org* is the caller's organization, never request input.
    """
    from gateway.routes.crm import modules

    async with _tenant_session() as db:
        module = modules.resolve_module(await modules.mirrored_modules(db), module)
        return list(await modules._field_defs(db, module, org))


@router.get("/fields/{entity}", response_model=list[FieldDef] | FieldLayout)
async def list_fields(
    entity: str,
    layout: bool = Query(False),
    user: UserContext = Depends(get_current_user),
) -> list[FieldDef] | FieldLayout:
    """The custom fields of one entity, ordered by position.

    With ``?layout=1``, the whole layout of :func:`read_layout`. The entity
    ``module:<ApiName>`` names a mirrored module (CRM-Q25). It has no layout.
    """
    if entity.startswith(MODULE_PREFIX):
        if layout:
            raise HTTPException(
                status_code=422, detail="A module has no layout. Drop '?layout'.",
            )
        return await module_field_defs(
            entity[len(MODULE_PREFIX):], user.organization_id,
        )
    stored = checked_slug(entity)
    async with _tenant_session() as db:
        # CRM-Q17a-1: one read of the fill rows, and none outside the list.
        fills = await fills_for(db, user.organization_id, stored)
        if layout:
            found = await read_layout(db, entity)
            update: dict[str, Any] = await studio_flags(db, user)
            if fills is not None:
                update["fields"] = annotate(found.fields, fills)
                update["empty_columns"] = await empty_columns(
                    db, user.organization_id, entity, fills,
                )
            return found.model_copy(update=update)
        # CRM-Q25: the same read as the typed aggregate (`custom_defs`).
        defs = await custom_defs(db, entity)
    return annotate(defs, fills)


# ── The field search (CRM-Q34-6) ─────────────────────────────────────────────

#: The code of the 404 that an org outside ``CRM_MODULE_FIND_ORGS`` gets.
MODULE_FIND_OFF = "module_find_off"

#: The longest ``q``, in characters.
SEARCH_QUERY_MAX = 200
#: The most hits of one answer. ``total`` counts each hit.
SEARCH_HITS_MAX = 200
#: The shortest word that matches as the start of a longer word.
PREFIX_MIN = 3

#: The live custom fields of the mirrored set, in ONE statement. A system
#: overlay and a retired field are no hit. It names the org, and FORCE row
#: level security binds the tenant session too.
_SEARCH_SQL = (
    "SELECT entity, key, label, type, position FROM crm_field_defs "
    "WHERE organization_id = CAST(:org AS uuid) "
    "AND entity = ANY(CAST(:entities AS text[])) "
    "AND NOT coalesce(is_system, false) AND retired_at IS NULL"
)


class FieldHit(BaseModel):
    """One field that the words of ``q`` find."""

    #: The API name of the module.
    module: str
    #: The plural label of the module, as the rail shows it.
    module_label: str
    key: str
    label: str
    type: str
    #: The share of records that fill the field, from 0 to 1. ``None``
    #: outside ``CRM_FILL_RANK_ORGS``, or with no numbers yet.
    fill: float | None = None


class FieldSearch(BaseModel):
    """``GET /crm/fields-search``."""

    #: The first :data:`SEARCH_HITS_MAX` hits, by module order, then position.
    hits: list[FieldHit]
    #: Each hit, also past :data:`SEARCH_HITS_MAX`.
    total: int


def word_hit(word: str, field_words: Sequence[str]) -> bool:
    """True when *word* is a word of the field, or when one of the two
    starts the other and the shorter has :data:`PREFIX_MIN` letters or more.
    So "price" finds "Prices", and "deals" finds "Deal"."""
    for found in field_words:
        if found == word:
            return True
        short, long = sorted((found, word), key=len)
        if len(short) >= PREFIX_MIN and long.startswith(short):
            return True
    return False


def field_matches(wanted: Sequence[str], label: Any, key: Any) -> bool:
    """True when each word of *wanted* hits a word of *label* or of *key*.
    ``terms.words`` splits a key at ``_`` and at each change to upper case."""
    found = [*words(label), *words(key)]
    return bool(wanted) and all(word_hit(w, found) for w in wanted)


def _find_off() -> HTTPException:
    return HTTPException(status_code=404, detail={
        "code": MODULE_FIND_OFF,
        "message": "The field search is off for this organization.",
    })


@router.get("/fields-search", response_model=FieldSearch)
async def search_fields(
    q: str | None = None,
    module: str | None = None,
    user: UserContext = Depends(get_current_user),
) -> FieldSearch:
    """The fields of one mirrored module, or of each mirrored module, that
    the words of *q* find (CRM-Q34-6). Each hit names its module, key, label
    and type, and its fill on ``CRM_FILL_RANK_ORGS``.

    404 ``module_find_off`` for an org outside ``CRM_MODULE_FIND_ORGS``,
    before any other check. 422 for a *q* with no word or past
    :data:`SEARCH_QUERY_MAX`. 404 ``unknown_module`` for a *module* that the
    org does not mirror. No argument has a type that FastAPI checks first,
    so a dark org gets the 404 for each request.
    """
    org = user.organization_id
    if not module_find_allowed(org):
        raise _find_off()
    if q is not None and len(q) > SEARCH_QUERY_MAX:
        raise HTTPException(
            status_code=422, detail=f"q is longer than {SEARCH_QUERY_MAX} characters.",
        )
    wanted = list(dict.fromkeys(words(q)))
    if not wanted:
        raise HTTPException(
            status_code=422,
            detail="Give q one word or more of the label or the key of a field.",
        )
    from gateway.routes.crm import modules

    async with _tenant_session() as db:
        mirrored = await modules.mirrored_modules(db)
        names = list(mirrored)
        if module:
            # The name must be in the mirrored set before any statement.
            module = modules.resolve_module(mirrored, module)
            names = [module]
        by_entity = {modules._mapping().module_entity(n): n for n in names}
        if not by_entity:
            return FieldSearch(hits=[], total=0)
        rows = (await db.execute(text(_SEARCH_SQL), {
            "org": str(org), "entities": list(by_entity),
        })).fetchall()
        place = {entity: index for index, entity in enumerate(by_entity)}
        found = sorted(
            (r for r in rows if field_matches(wanted, r.label, r.key)),
            key=lambda r: (place[str(r.entity)], int(r.position or 0), str(r.key)),
        )
        shown = found[:SEARCH_HITS_MAX]
        fills = await fills_of(db, org, sorted({str(r.entity) for r in shown}))
    hits = []
    for r in shown:
        name = by_entity[str(r.entity)]
        fill = (fills or {}).get(str(r.entity), {}).get(str(r.key))
        hits.append(FieldHit(
            module=name, module_label=str(mirrored[name].get("label") or name),
            key=str(r.key), label=str(r.label or r.key), type=str(r.type),
            fill=fill.share if fill is not None else None,
        ))
    return FieldSearch(hits=hits, total=len(found))
