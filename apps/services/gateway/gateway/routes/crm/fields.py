"""CRM · fields — what each custom field means (WS-53 CRM-Q4, CRM-C4a).

Spec: ``project-docs/specs/crm_platform.md`` §18.3 step 2 item 3, §18.5 and
§20.7 items 11 and 12.

    GET  /crm/fields/{entity}
    GET  /crm/fields/{entity}?layout=1

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
numbers on each row and no ``empty_columns``. For any other org each of these
is ``null``. No request computes them.

Read only. Any member that the CRM access rule admits may read it. The
organization comes from the server and the session binds it, so the route
never takes a tenant from the request (R5). The writes live in
``field_studio.py``.
"""

from __future__ import annotations

from typing import Any, Literal

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException, Query
from gateway.routes.crm.access import ADMIN_PERMISSION, read_settings
from gateway.routes.crm.core import (
    ENTITIES,
    FIELD_ENTITY_BY_SLUG,
    SYSTEM_FIELDS,
    _tenant_session,
    from_jsonb,
    router,
)
from gateway.routes.crm.fill_rank import annotate, empty_set, fills_for
from gateway.routes.crm.flags import field_studio_allowed
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
    #: (``fill_rank.empty_set``). ``None`` outside ``CRM_FILL_RANK_ORGS``.
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


@router.get("/fields/{entity}", response_model=list[FieldDef] | FieldLayout)
async def list_fields(
    entity: str,
    layout: bool = Query(False),
    user: UserContext = Depends(get_current_user),
) -> list[FieldDef] | FieldLayout:
    """The custom fields of one entity, ordered by position.

    With ``?layout=1``, the whole layout of :func:`read_layout`.
    """
    stored = checked_slug(entity)
    async with _tenant_session() as db:
        # CRM-Q17a-1: one read of the fill rows, and none outside the list.
        fills = await fills_for(db, user.organization_id, stored)
        if layout:
            found = await read_layout(db, entity)
            update: dict[str, Any] = await studio_flags(db, user)
            if fills is not None:
                update["fields"] = annotate(found.fields, fills)
                update["empty_columns"] = empty_set(entity, fills)
            return found.model_copy(update=update)
        rows = (await db.execute(text(_FIELDS_SQL), {"entity": stored})).fetchall()
    return annotate([field_wire(r) for r in rows], fills)
