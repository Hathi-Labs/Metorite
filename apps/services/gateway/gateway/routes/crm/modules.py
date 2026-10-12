"""CRM · modules — the views of the generic Zoho modules (WS-53 CRM-Q6b).

Spec: ``project-docs/specs/crm_platform.md`` §18.7 "CRM-Q6b: the views".

    GET  /crm/modules                          the mirrored generic modules
    GET  /crm/modules/{module}/records         one page of a module's records
    GET  /crm/modules/{module}/records/{id}    one record, its fields and links

CRM-Q6a mirrors each Zoho module that has no table of its own into
``crm_module_records`` (Products, Quotes, each custom module). These routes
read that table. They are read only, and the sync is the only writer.

**Which modules.** The latest source profile names them: a row with
``generic`` and ``mirrored`` true. The order is the profile order of
``generic_modules``: the standard modules of §18.7, then each custom module in
alphabetical order. A Native org gets an empty list, and so does an org that
has no profile yet.

**The module name never reaches SQL text (R5).** Each route checks the name in
the path against the mirrored set of the profile FIRST. A name outside the set
is a 404, before any read of a record. The name then goes into each statement
as a bound parameter. The sort column comes from :data:`MODULE_RECORDS`, an
allowlist, through ``core.list_contract``.

**Lookups become links.** A lookup value stays in ``fields`` as Zoho sends it,
``{id, name}``. The field definition (``crm_field_defs``, entity
``module:<ApiName>``) names its target module in ``options.lookup_module``.

* A known target (Leads, Contacts, Accounts, Deals) links to the record page of
  the local record with that ``ext_id``.
* A mirrored generic target links to the generic record page of that record.
* Any other target, or a record that is not here yet, stays plain text.

**Who may read.** Every route is on the CRM router, so the kill switch, the
``feature:crm`` gate and the CRM access rule hold. The tenant comes from the
session that the router binds, never from the request. Each read runs in one
``_tenant_session()``, so FORCE row level security keeps it in the tenant.
"""

from __future__ import annotations

from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException, Query
from gateway.routes.crm.core import (
    ENTITIES,
    MAX_PAGE_SIZE,
    Entity,
    ListResponse,
    _tenant_session,
    checked_uuid,
    current_tenant,
    from_jsonb,
    is_mirror_mode,
    list_contract,
    router,
    wire,
)
from gateway.routes.crm.fields import FieldDef
from pydantic import BaseModel, Field
from sqlalchemy import text

#: The provider whose profile names the modules. One provider today.
PROVIDER = "zoho"


class ModuleSummary(BaseModel):
    """One row of ``GET /crm/modules``."""

    api_name: str
    #: The plural label, as the rail shows it ("Products").
    label: str
    #: The singular label, as the record page names one record ("Product").
    singular_label: str | None = None
    #: The records of the module that the mirror holds.
    count: int = 0


class ModuleRecordModel(BaseModel):
    """One record of a generic module, on the list and on the record page."""

    id: str
    module: str
    name: str | None = None
    owner_email: str | None = None
    #: Each field value, keyed by its API name, as Zoho sends it.
    fields: dict = {}
    ext_source: str | None = None
    ext_id: str | None = None
    #: The deep link ("Open in Zoho"). The sync stores it (CRM-Q1 rules).
    ext_url: str | None = None
    ext_created_at: str | None = None
    ext_modified_at: str | None = None


class ModuleFieldDef(FieldDef):
    """A field definition of a generic module. A lookup names its target."""

    lookup_module: str | None = None


class ModuleLink(BaseModel):
    """Where a lookup value opens.

    ``kind`` is ``record`` for a known record (``entity`` is its collection,
    for example ``deals``) and ``module`` for a generic record (``module`` is
    its API name). ``id`` is the local id.
    """

    kind: str
    entity: str | None = None
    module: str | None = None
    id: str
    name: str | None = None


class ModuleListResponse(ListResponse):
    """``GET /crm/modules/{module}/records``: the list shape of every CRM list,
    and the field definitions of the module, so the list can offer its fields
    as columns by the CRM-Q4 rules."""

    field_defs: list[ModuleFieldDef] = Field(default_factory=list)


class ModuleRecordPage(BaseModel):
    """``GET /crm/modules/{module}/records/{id}``."""

    module: ModuleSummary
    record: ModuleRecordModel
    field_defs: list[ModuleFieldDef]
    #: Field key to its link. A lookup with no link is absent: it stays text.
    links: dict[str, ModuleLink]


#: The list contract of a generic module. ``core.list_contract`` reads the sort
#: allowlist, the default sort and the search columns from it. The table is a
#: literal, and the module filter is added as a bound parameter.
MODULE_RECORDS = Entity(
    slug="modules",
    table="crm_module_records",
    label="Record",
    activity_column="",
    model=ModuleRecordModel,
    payload=BaseModel,
    required=(),
    sorts={
        "name": "name",
        "owner_email": "owner_email",
        "ext_modified_at": "ext_modified_at",
        "ext_created_at": "ext_created_at",
    },
    default_sort="ext_modified_at",
    search=("name",),
)

_COLUMNS = (
    "id, module, name, owner_email, fields, ext_source, ext_id, ext_url, "
    "ext_created_at, ext_modified_at"
)

_COUNTS_SQL = (
    "SELECT module, count(*) AS n FROM crm_module_records "
    "WHERE module = ANY(CAST(:modules AS text[])) GROUP BY module"
)

_ONE_SQL = (
    f"SELECT {_COLUMNS} FROM crm_module_records "
    "WHERE id = CAST(:id AS uuid) AND module = :module"
)

_DEFS_SQL = (
    "SELECT key, label, type, options, source, provider_api_name, position, "
    "visible FROM crm_field_defs WHERE entity = :entity ORDER BY position, key"
)

_GENERIC_TARGETS_SQL = (
    "SELECT id, ext_id FROM crm_module_records WHERE module = :module "
    "AND ext_source = :ext_source AND ext_id = ANY(CAST(:ids AS text[]))"
)

#: The known Zoho module → its entity. ``Entity.zoho_module`` is the one answer
#: to "which Zoho module is a deal", so this map is read from it.
KNOWN_TARGETS: dict[str, Entity] = {
    e.zoho_module: e for e in ENTITIES.values() if e.zoho_module
}


def _mapping() -> Any:
    from gateway.crm_sources.registry import mapping_for

    return mapping_for(PROVIDER)


async def mirrored_modules(db: Any) -> dict[str, dict[str, Any]]:
    """The mirrored generic modules of the caller's org, by API name, in the
    profile order. Empty in Native mode and before the first discovery.

    The organization is the bound tenant. Row level security binds the read
    too.
    """
    from gateway.crm_sync import profiles

    org = current_tenant()
    if not org or not await is_mirror_mode(db, str(org)):
        return {}
    latest = await profiles.latest(db, str(org))
    if latest is None:
        return {}
    return {
        str(row["api_name"]): row
        for row in _mapping().generic_modules(latest.profile)
        if row.get("mirrored") and row.get("api_name")
    }


def _summary(row: dict[str, Any], count: int = 0) -> ModuleSummary:
    api_name = str(row["api_name"])
    return ModuleSummary(
        api_name=api_name,
        label=str(row.get("label") or api_name),
        singular_label=row.get("singular_label") or None,
        count=count,
    )


def _require_module(mirrored: dict[str, dict[str, Any]], module: str) -> dict[str, Any]:
    """The profile row of *module*, or 404. Runs before any read of a record,
    so a name outside the mirrored set never reaches a statement."""
    row = mirrored.get(module)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "unknown_module", "message": "This module is not mirrored."},
        )
    return row


def _record(row: Any) -> ModuleRecordModel:
    fields = from_jsonb(row.fields)
    return ModuleRecordModel(
        id=str(row.id),
        module=row.module,
        name=row.name,
        owner_email=row.owner_email,
        fields=fields if isinstance(fields, dict) else {},
        ext_source=row.ext_source,
        ext_id=row.ext_id,
        ext_url=row.ext_url,
        ext_created_at=wire(row.ext_created_at),
        ext_modified_at=wire(row.ext_modified_at),
    )


def _field_def(row: Any) -> ModuleFieldDef:
    options = from_jsonb(row.options)
    target = options.get("lookup_module") if isinstance(options, dict) else None
    return ModuleFieldDef(
        key=row.key, label=row.label, type=row.type,
        options=options if isinstance(options, list) else None,
        position=int(row.position or 0), visible=bool(row.visible),
        source="zoho" if row.source == "zoho" else "native",
        api_name=row.provider_api_name,
        lookup_module=str(target) if target else None,
    )


async def _field_defs(db: Any, module: str) -> list[ModuleFieldDef]:
    """The field definitions of a module (``module:<ApiName>``), by position."""
    rows = (await db.execute(
        text(_DEFS_SQL), {"entity": _mapping().module_entity(module)},
    )).fetchall()
    return [_field_def(r) for r in rows]


def _lookup_ids(
    record: ModuleRecordModel, defs: list[ModuleFieldDef],
) -> dict[str, list[tuple[str, str, str | None]]]:
    """Target module → ``(field key, ext id, name)`` of each lookup value."""
    out: dict[str, list[tuple[str, str, str | None]]] = {}
    for d in defs:
        if d.type != "lookup" or not d.lookup_module:
            continue
        value = record.fields.get(d.key)
        if not isinstance(value, dict) or not value.get("id"):
            continue
        name = value.get("name")
        out.setdefault(d.lookup_module, []).append(
            (d.key, str(value["id"]), str(name) if name else None),
        )
    return out


async def resolve_links(
    db: Any,
    record: ModuleRecordModel,
    defs: list[ModuleFieldDef],
    mirrored: dict[str, dict[str, Any]],
) -> dict[str, ModuleLink]:
    """The link of each lookup of *record*. A lookup with no local target is
    left out, so the UI draws its name as plain text.

    Each table name comes from :data:`KNOWN_TARGETS` (a literal of ``core``),
    and each id and module name is a bound parameter.
    """
    source = record.ext_source or PROVIDER
    links: dict[str, ModuleLink] = {}
    for target, items in _lookup_ids(record, defs).items():
        ids = sorted({ext_id for _key, ext_id, _name in items})
        known = KNOWN_TARGETS.get(target)
        if known is not None:
            rows = (await db.execute(
                text(
                    f"SELECT id, ext_id FROM {known.table} "
                    "WHERE ext_source = :ext_source "
                    "AND ext_id = ANY(CAST(:ids AS text[]))"
                ),
                {"ext_source": source, "ids": ids},
            )).fetchall()
            found = {str(r.ext_id): str(r.id) for r in rows}
            for key, ext_id, name in items:
                if ext_id in found:
                    links[key] = ModuleLink(
                        kind="record", entity=known.slug, id=found[ext_id], name=name,
                    )
        elif target in mirrored:
            rows = (await db.execute(
                text(_GENERIC_TARGETS_SQL),
                {"module": target, "ext_source": source, "ids": ids},
            )).fetchall()
            found = {str(r.ext_id): str(r.id) for r in rows}
            for key, ext_id, name in items:
                if ext_id in found:
                    links[key] = ModuleLink(
                        kind="module", module=target, id=found[ext_id], name=name,
                    )
    return links


@router.get("/modules", response_model=list[ModuleSummary])
async def list_modules(
    user: UserContext = Depends(get_current_user),
) -> list[ModuleSummary]:
    """The mirrored generic modules, in the profile order, with a row count.

    A Native org, and an org with no profile, gets an empty list.
    """
    async with _tenant_session() as db:
        mirrored = await mirrored_modules(db)
        if not mirrored:
            return []
        counts = {
            str(r.module): int(r.n)
            for r in (await db.execute(
                text(_COUNTS_SQL), {"modules": list(mirrored)},
            )).fetchall()
        }
    return [_summary(row, counts.get(name, 0)) for name, row in mirrored.items()]


@router.get("/modules/{module}/records", response_model=ModuleListResponse)
async def list_module_records(
    module: str,
    q: str | None = None,
    sort: str | None = None,
    direction: str = Query("desc", alias="dir"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=MAX_PAGE_SIZE),
    user: UserContext = Depends(get_current_user),
) -> ModuleListResponse:
    """One page of a mirrored module's records. 404 for a module that the
    profile does not mirror. ``q`` matches ``name``, and ``sort`` is one of
    :data:`MODULE_RECORDS` ``sorts``.
    """
    async with _tenant_session() as db:
        _require_module(await mirrored_modules(db), module)
        query = list_contract(
            MODULE_RECORDS, q=q, sort=sort, direction=direction,
            page=page, page_size=page_size,
            extra_where=("module = :module",), extra_params={"module": module},
        )
        total = (await db.execute(
            text(f"SELECT count(*) FROM crm_module_records{query.where}"), query.params,
        )).scalar() or 0
        rows = (await db.execute(
            text(
                f"SELECT {_COLUMNS} FROM crm_module_records{query.where} "
                f"ORDER BY {query.order_by} LIMIT :limit OFFSET :offset"
            ),
            {**query.params, "limit": query.limit, "offset": query.offset},
        )).fetchall()
        defs = await _field_defs(db, module)
    return ModuleListResponse(
        rows=[_record(r).model_dump() for r in rows], total=int(total),
        field_defs=defs,
    )


@router.get("/modules/{module}/records/{record_id}", response_model=ModuleRecordPage)
async def get_module_record(
    module: str,
    record_id: str,
    user: UserContext = Depends(get_current_user),
) -> ModuleRecordPage:
    """One record of a mirrored module, with its field definitions and the
    link of each lookup. 404 for an unknown module or record."""
    async with _tenant_session() as db:
        mirrored = await mirrored_modules(db)
        row = _require_module(mirrored, module)
        wanted = checked_uuid(record_id, "id")
        found = (await db.execute(
            text(_ONE_SQL), {"id": wanted, "module": module},
        )).first()
        if found is None:
            raise HTTPException(status_code=404, detail="Record not found")
        record = _record(found)
        defs = await _field_defs(db, module)
        links = await resolve_links(db, record, defs, mirrored)
    return ModuleRecordPage(
        module=_summary(row), record=record, field_defs=defs, links=links,
    )
