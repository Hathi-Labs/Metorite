"""CRM · the Excel export of a query, in the shape the caller asks (DOC-6).

Spec: ``project-docs/specs/chat_parity.md`` §DOC row DOC-6.

``GET /crm/export/workbook.xlsx?spec=<json>`` gives one ``.xlsx``. ``spec`` is
``{"sheets": [{"source": {"type": "crm", "module": ..., <filters>}, <shape>}]}``.
The chat's ``make_spreadsheet`` calls it as the member, so the rows go from
the database to the file and never through the model.

**The read is the list's read.** A core entity goes through
:func:`export.read_filtered`, the read of the CSV export. A mirrored module
goes through :func:`modules.list_module_records`, page by page. So the filters,
the tenant and the access rules of the router apply, and no second filter
parser exists. A filter that the source does not know is refused, and never
dropped.

It is complete or it is refused: above ``MAX_EXPORT_ROWS`` rows it gives no
file. Like ``export.py``, it writes nothing.
"""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import urlencode

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException, Query, Response
from gateway import xlsx_export as xe
from gateway.routes.crm.core import (
    CONTACTS,
    ENTITIES,
    MAX_PAGE_SIZE,
    ORGANIZATIONS,
    Entity,
    _tenant_session,
    from_jsonb,
    router,
)
from gateway.routes.crm.export import (
    COLUMNS,
    ExportParams,
    custom_cell,
    custom_columns,
    read_filtered,
)
from gateway.routes.crm.field_studio import attach_lookup_names
from gateway.routes.crm.modules import list_module_records
from sqlalchemy import text
from starlette.requests import Request

#: The filters of a core entity's source: the list's own parameters.
_CORE_FILTERS = frozenset({
    "module", "q", "owner", "status_id", "source", "include_converted", "pipeline_id",
    "company_id", "contact_id", "created_from", "created_to", "updated_from",
    "updated_to", "close_from", "close_to", "custom",
})
#: The filters of a mirrored module's source: those of its list route.
_MODULE_FILTERS = frozenset({
    "module", "q", "owner", "updated_from", "updated_to", "custom",
})
#: Columns that are ids or sync bookkeeping, never a column of the file.
_HIDDEN = frozenset({"id", "custom", "zoho_id", "organization_id", "ext_url", "ext_source"})
#: The keys that lead a core sheet, before the other fields.
_LEAD_KEYS = ("name", "lead_name", "first_name", "last_name")


def _request(pairs: list[tuple[str, str]]) -> Request:
    """A request whose query string holds *pairs*, so the list code reads the
    ``custom.<key>`` filters and its NUL guard exactly as for a browser."""
    return Request({"type": "http", "method": "GET", "path": "/", "headers": [],
                    "query_string": urlencode(pairs).encode()})


def _custom_pairs(args: dict[str, Any]) -> list[tuple[str, str]]:
    custom = args.get("custom") or {}
    if not isinstance(custom, dict):
        raise xe.SpreadsheetError('"custom" is an object of field key to value')
    return [(f"custom.{k}.in", ",".join(map(str, v))) if isinstance(v, list)
            else (f"custom.{k}", str(v)) for k, v in custom.items()]


def _text(value: Any) -> str | None:
    return None if value in (None, "") else str(value)


def _label(key: str, known: dict[str, str]) -> str:
    if key in known:
        return known[key]
    return key.replace("_", " ").replace(".", " ").capitalize()


def _ordered(keys: list[str], known: dict[str, str]) -> list[str]:
    first = [k for k in (*_LEAD_KEYS, *known) if k in keys]
    seen = dict.fromkeys([*first, *keys])
    return list(seen)


async def _companies(db: Any, ids: list[str]) -> dict[str, Any]:
    if not ids:
        return {}
    rows = (await db.execute(
        text(f"SELECT id, name, address FROM {ORGANIZATIONS.table} "
             "WHERE id = ANY(CAST(:ids AS uuid[]))"),
        {"ids": ids},
    )).fetchall()
    return {str(r.id): r for r in rows}


def _spread(row: dict[str, Any], key: str, value: Any) -> None:
    """A dict value (an address) as one field for each of its parts."""
    if isinstance(value, dict):
        for part, inner in value.items():
            if not isinstance(inner, (dict, list)):
                row[f"{key}.{part}"] = inner
    else:
        row[key] = value


async def _core_table(entity: Entity, args: dict[str, Any], user: UserContext) -> xe.Table:
    if not isinstance(args.get("include_converted", False), bool):
        raise xe.SpreadsheetError('"include_converted" is true or false')
    filters = {k: v for k, v in args.items() if k not in ("module", "custom")}
    pairs = [(k, str(v)) for k, v in filters.items()] + _custom_pairs(args)
    kwargs = {k: (v if k == "include_converted" else _text(v)) for k, v in filters.items()}
    params = ExportParams(
        direction="desc", request=_request(pairs),
        **{"q": None, "sort": None, "status_id": None, "owner": None, "source": None,
           "pipeline_id": None, "company_id": None, "contact_id": None,
           "created_from": None, "created_to": None, "updated_from": None,
           "updated_to": None, "close_from": None, "close_to": None,
           "include_converted": False, **kwargs},
    )
    params.include_converted = bool(args.get("include_converted"))
    async with _tenant_session() as db:
        records = await read_filtered(db, entity, params, user)
        await attach_lookup_names(db, entity.slug, records)
        extra = await custom_columns(db, entity)
        companies: dict[str, Any] = {}
        if entity is not ORGANIZATIONS:
            companies = await _companies(db, sorted(
                {str(r["company_id"]) for r in records if r.get("company_id")}))

    known = dict(COLUMNS.get(entity.slug, ()))
    if entity is CONTACTS:
        known["first_name"] = "First name"
    rows: list[dict[str, Any]] = []
    keys: dict[str, None] = {}
    labels: dict[str, str] = {}
    for record in records:
        row: dict[str, Any] = {}
        if entity is CONTACTS:
            row["name"] = " ".join(
                p for p in (record.get("first_name"), record.get("last_name")) if p)
        for key, value in record.items():
            if key in _HIDDEN or key.endswith("_id"):
                continue
            _spread(row, key, value)
        company = companies.get(str(record.get("company_id") or ""))
        if company is not None:
            row["company_name"] = company.name
            address = from_jsonb(company.address)
            if isinstance(address, dict):
                _spread(row, "company_address", address)
        stored = record.get("custom") or {}
        for column in extra:
            key = column.key if column.key not in row else f"custom.{column.key}"
            labels[key] = column.label
            row[key] = custom_cell(stored.get(column.key), column.options)
        keys.update(dict.fromkeys(row))
        rows.append(row)
    if not records:
        keys.update(dict.fromkeys(("name", *known)))
        keys.update(dict.fromkeys(c.key for c in extra))
        labels.update({c.key: c.label for c in extra})
    labels = {"name": "Name", "company_name": "Company", **labels}
    return xe.Table(
        fields=[xe.Field(k, labels.get(k) or _label(k, known)) for k in _ordered(list(keys), known)],
        rows=rows,
    )


async def _module_table(
    module: str, args: dict[str, Any], user: UserContext,
) -> xe.Table:
    """Every record of a mirrored module that the filters pick, through the
    module's own list route."""
    pairs = _custom_pairs(args)
    records: dict[str, dict[str, Any]] = {}
    definitions: list[Any] = []
    total = 0
    page = 1
    while True:
        found = await list_module_records(
            module=module, q=_text(args.get("q")), sort=None, direction="desc",
            page=page, page_size=MAX_PAGE_SIZE, owner=_text(args.get("owner")),
            updated_from=_text(args.get("updated_from")),
            updated_to=_text(args.get("updated_to")), user=user,
            request=_request([*pairs, *((k, str(v)) for k, v in args.items() if k != "custom")]),
        )
        total = found.total
        if total > xe.MAX_EXPORT_ROWS:
            raise xe.too_many(total)
        if page == 1:
            definitions = list(found.field_defs)
            known = {d.key for d in definitions}
            # The list route drops a custom key that no field has. Here that
            # would export every record, so it is refused.
            bad = sorted(n for n, _ in pairs
                         if n.removeprefix("custom.").removesuffix(".in") not in known)
            if bad:
                raise xe.SpreadsheetError(
                    f"the module {found.module} has no field for the filter "
                    f"{', '.join(bad)}. Its fields are: {', '.join(sorted(known))}")
        records.update({r["id"]: r for r in found.rows})
        if len(found.rows) < MAX_PAGE_SIZE or page * MAX_PAGE_SIZE >= total:
            break
        page += 1
    if len(records) < total:
        raise xe.SpreadsheetError(
            "the records changed while they were read, so no file was made. Try again.")

    shown = sorted((d for d in definitions if d.visible), key=lambda d: d.position)
    options = {
        d.key: {str(o["value"]): str(o.get("label") or o["value"])
                for o in (d.options or []) if isinstance(o, dict) and o.get("value") is not None}
        for d in shown
    }
    own = ("name", "owner_email", "ext_created_at", "ext_modified_at")
    fields = [xe.Field("name", "Name")]
    custom_keys: dict[str, str] = {}
    for d in shown:
        key = f"fields.{d.key}" if d.key in own else d.key
        custom_keys[d.key] = key
        if key != "name":
            fields.append(xe.Field(key, d.label or d.key))
    fields += [xe.Field("owner_email", "Owner"), xe.Field("ext_created_at", "Created"),
               xe.Field("ext_modified_at", "Last modified")]
    rows = []
    for record in records.values():
        row = {k: record.get(k) for k in own}
        stored = record.get("fields") or {}
        for raw, key in custom_keys.items():
            row[key] = custom_cell(stored.get(raw), options.get(raw))
        rows.append(row)
    return xe.Table(fields=fields, rows=rows)


async def _table(plan: xe.SheetPlan, user: UserContext) -> xe.Table:
    module = str(plan.source.get("module") or "").strip()
    if not module:
        raise xe.SpreadsheetError(
            f'sheet "{plan.name}": the source needs "module": leads, deals, contacts, '
            "organizations, or the name of a module")
    entity = ENTITIES.get(module.lower())
    allowed = _CORE_FILTERS if entity else _MODULE_FILTERS
    args = xe.source_args(plan, allowed)
    if entity:
        return await _core_table(entity, args, user)
    return await _module_table(module, args, user)


@router.get("/export/workbook.xlsx")
async def export_workbook(
    spec: str = Query(..., max_length=xe.MAX_SPEC_CHARS),
    user: UserContext = Depends(get_current_user),
) -> Response:
    """The records that the sources of *spec* pick, as one Excel file."""
    try:
        plans = xe.parse_query_spec(spec, "crm")
        tables: list[xe.Table] = []
        for plan in plans:
            tables.append(await _table(plan, user))
            read = sum(len(t.rows) for t in tables)
            if read > xe.MAX_EXPORT_ROWS:
                raise xe.too_many(read, at_least=True)
        built = await asyncio.to_thread(xe.build, plans, tables)
    except xe.SpreadsheetError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return xe.workbook_response(built)
