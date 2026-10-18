"""CRM · the catalogue for the AI (WS-53 CRM-Q31-1, ``crm_platform.md`` §18.13.4).

    GET /crm/catalogue           one entry for each core entity and each mirrored module
    PUT /crm/catalogue/{module}  an admin changes a part (CRM-Q31-4, CRM-Q32-2)

The catalogue tells the AI what each entity and module is for, which field of
it holds the amount, the date, the status and the customer, and where its
lookups point. ``crm_list_modules`` renders it. The code rules live in
``crm_sync/catalogue.py``, as pure functions. This module reads their inputs.

**What it reads.** The CRM mode, the latest source profile, the record count
of each core entity and each mirrored module, the field definitions and the
fill rows of the module entities, and the ``role`` and ``catalogue`` items of
the approved plan. It never reads a value of ``crm_module_records.fields``
and never reads an owner email, so the body holds neither.

**Native mode.** The four core entities, from code. A Native CRM stores no
plan, so the route reads none.

**Dark in each mode.** An org outside ``CRM_CATALOGUE_ORGS`` gets 404
``catalogue_off``, in Native mode too (``flags.catalogue_allowed``). The list
ships empty. ``crm_list_modules`` then answers as before.

**One reader.** :func:`entry_of` gives the entry of one mirrored module. The
aggregate reaches the catalogue through it only (``field_sql.module_scope``),
and never reads the plan a second time.

**Who may read.** The route is on the CRM router, so the kill switch, the
``feature:crm`` gate and the access rule apply. The organization comes from
the server (``UserContext.organization_id``), and the read runs in ONE
``_tenant_session()``, with the org in the predicate of each statement and
FORCE row level security under it (R5).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Annotated, Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException
from gateway.routes.crm.core import (
    ENTITIES,
    SYSTEM_FIELDS,
    _tenant_session,
    from_jsonb,
    router,
)
from gateway.routes.crm.field_rules import _GUARDS
from gateway.routes.crm.flags import catalogue_allowed
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

#: The code of the 404 that an org outside ``CRM_CATALOGUE_ORGS`` gets.
CATALOGUE_OFF = "catalogue_off"

#: CRM-Q32-9: the list of keys of a sum, on the wire. The shape stops a long
#: list at the edge. :func:`check_parts` checks each key against the fields.
SumKeys = Annotated[list[str], Field(min_length=1, max_length=8)]

#: The order of the core entries: the slots of the mapping preview.
CORE_ORDER: tuple[str, ...] = ("organizations", "contacts", "leads", "deals")

#: The name column of each core entity.
CORE_NAMES: Mapping[str, str] = {
    "organizations": "name", "contacts": "first_name", "leads": "lead_name",
    "deals": "name",
}

#: What a Native CRM calls each core entity.
NATIVE_LABELS: Mapping[str, str] = {
    "organizations": "Organizations", "contacts": "Contacts", "leads": "Leads",
    "deals": "Deals",
}

#: The owner of a record. Each entry has it (§18.13.4, the table).
OWNER = ("owner_email", "Owner", "email")

#: The records of each core entity, in one statement. Each table name is a
#: literal of ``core.ENTITIES``. The org is in each predicate, and RLS too.
_CORE_COUNTS_SQL = "SELECT " + ", ".join(
    f"(SELECT count(*) FROM {ENTITIES[slug].table} "
    f"WHERE organization_id = CAST(:org AS uuid)) AS {slug}"
    for slug in CORE_ORDER
)

#: The records of each mirrored module. The module names are bound.
_MODULE_COUNTS_SQL = (
    "SELECT module, count(*) AS n FROM crm_module_records "
    "WHERE organization_id = CAST(:org AS uuid) "
    "AND module = ANY(CAST(:modules AS text[])) GROUP BY module"
)

#: The field definitions of the module entities, metadata only.
_DEFS_SQL = (
    "SELECT entity, key, label, type, options FROM crm_field_defs "
    "WHERE organization_id = CAST(:org AS uuid) "
    "AND entity = ANY(CAST(:entities AS text[])) ORDER BY entity, position, key"
)


class KeyField(BaseModel):
    """One field of an entry: its key, its label, its type and, for a
    lookup, the API name of the module it points at.

    CRM-Q32-9: a sum of fields holds each field in ``parts``. Its key is the
    keys joined with ``+``, and its label the labels joined with " + "."""

    key: str
    label: str
    type: str
    target: str | None = None
    parts: list[KeyField] | None = None


class CatalogueEntry(BaseModel):
    """One entry (§18.13.4). ``fields`` maps a key part to its field: ``None``
    when it was asked and no answer was sure, absent when not answered.
    ``open`` names each part that waits for a question. ``stale`` names each
    stored part whose field is gone or has another type."""

    entity: str
    module: str
    kind: str
    label: str
    records: int
    purpose: str | None = None
    by: dict[str, str] = Field(default_factory=dict)
    fields: dict[str, KeyField | None] = Field(default_factory=dict)
    name: KeyField | None = None
    owner: KeyField
    links: list[KeyField] = Field(default_factory=list)
    open: list[str] = Field(default_factory=list)
    stale: list[str] = Field(default_factory=list)
    #: CRM-Q31-2 item 7: a core entity only. The label of each module whose
    #: purpose fits the entity and that holds records.
    alike: list[str] = Field(default_factory=list)
    #: CRM-Q32 (§18.14.5): the ids of the open questions of this entry, only
    #: while ``CRM_QUESTIONS_ORGS`` serves the org. ``None`` outside it. A
    #: member learns that a question is open, and never its samples.
    questions: list[str] | None = None


class RevenueModel(BaseModel):
    """The meaning of revenue, from the ``@org`` item (§18.14.4). ``None``
    and ``None`` with no answer: a reader then uses billed, the default."""

    value: str | None = None
    by: str | None = None


class CatalogueResponse(BaseModel):
    mode: str
    entries: list[CatalogueEntry]
    #: CRM-Q32 (§18.14.5): the meaning of revenue.
    revenue: RevenueModel = Field(default_factory=RevenueModel)


def _off() -> HTTPException:
    return HTTPException(status_code=404, detail={
        "code": CATALOGUE_OFF,
        "message": "The catalogue is off for this organization.",
    })


def _key_field(f: Any) -> KeyField:
    parts = getattr(f, "parts", ())
    return KeyField(key=f.key, label=f.label, type=f.type, target=f.target,
                    parts=[_key_field(p) for p in parts] if parts else None)


def _wire(entry: Any, questions: list[str] | None = None) -> CatalogueEntry:
    return CatalogueEntry(
        entity=entry.entity, module=entry.module, kind=entry.kind,
        label=entry.label, records=entry.records, purpose=entry.purpose,
        by=dict(entry.by),
        fields={
            part: (_key_field(f) if f is not None else None)
            for part, f in entry.fields.items()
        },
        name=_key_field(entry.name) if entry.name is not None else None,
        owner=KeyField(key=OWNER[0], label=OWNER[1], type=OWNER[2]),
        links=[_key_field(f) for f in entry.links],
        open=list(entry.open), stale=list(entry.stale), alike=list(entry.alike),
        questions=questions,
    )


# ── The inputs ───────────────────────────────────────────────────────────────


def _core_sources(
    counts: Mapping[str, int], labels: Mapping[str, Any] | None,
) -> list[Any]:
    """The four core entities. *labels* holds the source labels of a Mirror
    CRM (``fill_rank.labels_of``), or ``None`` for a Native CRM."""
    from gateway.crm_sync import catalogue as cat
    from gateway.crm_sync.map_pipeline import _singular, norm

    module_of = {slug: e.zoho_module for slug, e in ENTITIES.items()}
    out = []
    for slug in CORE_ORDER:
        module = str(module_of[slug])
        found = (labels or {}).get(slug)
        label = found.label if found is not None else NATIVE_LABELS[slug]
        standard = found is None or _singular(norm(label)) == _singular(norm(module))
        fields = tuple(
            cat.Field(
                key=f.key, label=f.label, type=f.type,
                target=module_of.get(f.lookup_target or "") if f.lookup_target else None,
            )
            for f in SYSTEM_FIELDS[slug]
        )
        out.append(cat.Source(
            module=module, entity=slug, kind="entity", label=label,
            records=int(counts.get(slug, 0)),
            singular=found.singular_label if found is not None else None,
            standard_label=standard, fields=fields, display_field=CORE_NAMES[slug],
        ))
    return out


def _shares(fills: Mapping[str, Any] | None) -> dict[str, float] | None:
    """Each key → its share of filled records. ``None`` with no fill row."""
    if not fills:
        return None
    return {key: f.share for key, f in fills.items() if f.share is not None}


async def _module_sources(
    db: Any, org: str, mirrored: Mapping[str, Mapping[str, Any]], only: str | None,
    *, count: bool = True,
) -> list[Any]:
    """The mirrored modules. With *only*, the defs, the fill and the count of
    that module alone: the others need their names only. With *count*
    False, no record count is read, and each module counts 0 records: the
    key fields of an entry need no count (fix round, item 6)."""
    from gateway.crm_sources.zoho.mapping import module_entity
    from gateway.crm_sync import catalogue as cat
    from gateway.routes.crm.fill_rank import fills_of

    read = [m for m in mirrored if only is None or m == only]
    entities = [module_entity(m) for m in read]
    counts: dict[str, int] = {}
    defs: dict[str, list[Any]] = {}
    fills: dict[str, Any] | None = None
    if read and count:
        counts = {
            str(r.module): int(r.n) for r in (await db.execute(
                text(_MODULE_COUNTS_SQL), {"org": org, "modules": read},
            )).fetchall()
        }
    if read:
        for r in (await db.execute(
            text(_DEFS_SQL), {"org": org, "entities": entities},
        )).fetchall():
            options = from_jsonb(r.options)
            target = options.get("lookup_module") if isinstance(options, dict) else None
            defs.setdefault(str(r.entity), []).append(cat.Field(
                key=str(r.key), label=str(r.label or r.key), type=str(r.type or "text"),
                target=str(target) if target else None,
            ))
        fills = await fills_of(db, org, entities)
    out = []
    for api_name, row in mirrored.items():
        entity = module_entity(api_name)
        out.append(cat.Source(
            module=api_name, entity=entity, kind="module",
            label=str(row.get("label") or api_name),
            records=counts.get(api_name, 0),
            singular=row.get("singular_label") or None,
            custom=bool(row.get("custom")),
            fields=tuple(defs.get(entity, ())),
            fills=_shares((fills or {}).get(entity)) if fills is not None else None,
            display_field=row.get("display_field") or None,
        ))
    return out


async def _load(
    db: Any, org: str, *, only: str | None = None, count: bool = True,
) -> tuple[str, list[Any], dict[str, str], list[dict[str, Any]]]:
    """The mode, the sources, the approved roles (slot → module) and the
    approved items of other kinds, in the caller's tenant session."""
    from gateway.crm_sync import profiles
    from gateway.routes.crm.core import is_mirror_mode
    from gateway.routes.crm.fill_rank import labels_of
    from gateway.routes.crm.mapping_plans import approved_others, roles_of
    from gateway.routes.crm.modules import mirrored_of

    mirror = await is_mirror_mode(db, org)
    latest = await profiles.latest(db, org) if mirror else None
    counts: dict[str, int] = {}
    if only is None:
        row = (await db.execute(text(_CORE_COUNTS_SQL), {"org": org})).first()
        counts = {slug: int(getattr(row, slug, 0) or 0) for slug in CORE_ORDER} if row else {}
    if not mirror:
        return "native", _core_sources(counts, None), {}, []
    profile = latest.profile if latest is not None else {}
    labels = labels_of(profile.get("modules"))
    mirrored = mirrored_of(profile)
    sources = _core_sources(counts, labels)
    sources += await _module_sources(db, org, mirrored, only, count=count)
    others = await approved_others(db, org)
    roles = {slot: m for slot, m in roles_of(others).items() if m in mirrored}
    return "mirror", sources, roles, others


async def core_sources(db: Any, org: str) -> list[Any]:
    """The four core entities of a Mirror CRM as catalogue sources: their
    record counts and their source labels, in the caller's tenant session.
    The mapping pipeline reads them for its catalogue step (CRM-Q31-3), so
    the step and this route start from the same sources."""
    from gateway.crm_sync import profiles
    from gateway.routes.crm.fill_rank import labels_of

    row = (await db.execute(text(_CORE_COUNTS_SQL), {"org": org})).first()
    counts = {slug: int(getattr(row, slug, 0) or 0) for slug in CORE_ORDER} if row else {}
    latest = await profiles.latest(db, org)
    profile = latest.profile if latest is not None else {}
    return _core_sources(counts, labels_of(profile.get("modules")))


def _build(sources: Sequence[Any], roles: Mapping[str, str],
           others: Sequence[Mapping[str, Any]]) -> list[Any]:
    from gateway.crm_sync import catalogue as cat

    return cat.build(sources, roles, others)


async def entry_of(db: Any, org: str | None, module: str) -> Any | None:
    """The catalogue entry of the mirrored *module* (``crm_sync.catalogue.
    Entry``), or ``None``: the org is outside ``CRM_CATALOGUE_ORGS``, the CRM
    is not a Mirror, or the profile does not mirror the module.

    The ONE reader of one entry. ``field_sql.module_scope`` calls it, in the
    caller's tenant session. *org* is the bound tenant, never request input.
    """
    if not org or not catalogue_allowed(str(org)):
        return None
    # The aggregate reads the key fields of an entry, never its count, so
    # the count(*) over the module is skipped (fix round, item 6).
    mode, sources, roles, others = await _load(db, str(org), only=module, count=False)
    if mode != "mirror":
        return None
    return next(
        (e for e in _build(sources, roles, others)
         if e.kind == "module" and e.module == module),
        None,
    )


@router.get("/catalogue", response_model=CatalogueResponse)
async def get_catalogue(
    user: UserContext = Depends(get_current_user),
) -> CatalogueResponse:
    """The catalogue: one entry for each core entity and each mirrored module.

    404 ``catalogue_off`` for an org outside ``CRM_CATALOGUE_ORGS``, in each
    mode. The body holds no record value and no owner email.
    """
    from gateway.crm_sync import catalogue as cat
    from gateway.routes.crm.flags import questions_allowed
    from gateway.routes.crm.questions import module_question_ids
    from gateway.routes.crm.settings import _organization

    org = _organization(user)
    if not catalogue_allowed(org):
        raise _off()
    async with _tenant_session() as db:
        mode, sources, roles, others = await _load(db, org)
        # CRM-Q32 (§18.14.5): the open question ids of each entry, only while
        # the org is on CRM_QUESTIONS_ORGS, and only in Mirror mode.
        on = mode == "mirror" and questions_allowed(org)
        ids = (await module_question_ids(db, org, (mode, sources, roles, others))
               if on else {})
    value, by = cat.revenue_of(cat.stored_items(others))
    return CatalogueResponse(
        mode=mode,
        entries=[_wire(e, list(ids.get(e.module, ())) if on else None)
                 for e in _build(sources, roles, others)],
        revenue=RevenueModel(value=value, by=by),
    )


# ── The corrections of CRM-Q31-4 (CRM-Q32-2, §18.14.6) ─────────────────────

#: The value of a part that puts back the answer of the pipeline or of code.
#: A Zoho API name cannot start with ``@``, so it never names a field.
AUTO = "@auto"


def bad_answer(message: str, part: str | None = None) -> HTTPException:
    """The 422 of an answer that is not a choice, or a field of the wrong
    type (§18.14.6, "Errors")."""
    return HTTPException(status_code=422, detail={
        "code": "bad_answer", "message": message, "part": part,
    })


def _bad_field(src: Any, part: str, value: Any) -> bool:
    """True unless *value* names a field of *src* that can hold *part*.
    CRM-Q32-9: ``amount`` also takes a list of 2 to 5 distinct keys, each a
    ``currency`` field of *src*. The shape is checked before any field."""
    from gateway.crm_sync import catalogue as cat

    if isinstance(value, list):
        if part != cat.SUM_PART or cat.sum_shape_problem(value) or src is None:
            return True
        return cat.problem(src, part, value) is not None
    if not isinstance(value, str) or src is None or src.field(value) is None:
        return True
    if cat.problem(src, part, value) is not None:
        return True
    return part == "parent" and not src.field(value).target


def check_parts(module: str, parts: Mapping[str, Any], src: Any) -> None:
    """422 ``bad_answer`` unless each value of *parts* is legal for *module*:
    a purpose word, a field of the right type, ``null`` or ``"@auto"``, and
    ``invoice`` or ``payment_in`` for ``revenue`` of ``@org``. *src* is the
    catalogue source of the module (``None`` for ``@org``). CRM-Q32-9:
    ``amount`` also takes a list of 2 to 5 currency fields, a sum."""
    from gateway.crm_sync import catalogue as cat

    legal = cat.parts_of(module)
    for part, value in parts.items():
        if part not in legal:
            raise bad_answer(f"{module} takes no part {part}.", part)
        if value == AUTO:
            continue
        if part == "revenue":
            if value not in cat.REVENUE_VALUES:
                raise bad_answer("Revenue is billed (invoice) or collected (payment_in).", part)
        elif part == "purpose":
            if value is not None and value not in cat.PURPOSES:
                raise bad_answer("That is not a purpose of the catalogue.", part)
        elif value is not None and _bad_field(src, part, value):
            raise bad_answer(f"That field cannot hold the {part} of {module}.", part)


async def write_answer(
    db: Any, org: str, *, by: str, module: str, parts: Mapping[str, Any],
) -> Any | None:
    """THE writer of an answer and of a correction (§18.14.6): one door for
    ``POST /crm/questions/{id}/answer`` and ``PUT /crm/catalogue/{module}``.

    Each part takes a field or a purpose word, or ``null``, stored with
    ``by: admin``. ``"@auto"`` puts the part back to the answer of the
    pipeline, or of code (``mapping_plans.apply_catalogue`` with ``clear``).
    One ``apply_catalogue`` call, so one plan version under the plan lock.
    The caller checked the values (:func:`check_parts`). Returns the new
    approved version, or ``None`` when nothing changed."""
    from gateway.crm_sync import catalogue as cat
    from gateway.routes.crm.mapping_plans import apply_catalogue

    item: dict[str, Any] = {"kind": cat.KIND_CATALOGUE, "module": module, "by": {}}
    clear: list[str] = []
    for part, value in parts.items():
        if value == AUTO:
            clear.append(part)
            continue
        item[part] = value
        item["by"][part] = "admin"
    return await apply_catalogue(
        db, [item] if item["by"] else [], by=by, org=org,
        clear={module: clear} if clear else None,
    )


class CatalogueChangeIn(BaseModel):
    """``PUT /crm/catalogue/{module}`` (CRM-Q31-4 item 4). Each part that
    the body names takes a field API name or a purpose word, ``null``, or
    ``"@auto"``. A part that the body leaves out does not change.

    CRM-Q32-9: a key part takes a list too, so :func:`check_parts` answers
    each list with ``bad_answer``. Only ``amount`` keeps a list of 2 to 5
    currency fields."""

    model_config = ConfigDict(extra="forbid")

    purpose: str | None = None
    amount: str | SumKeys | None = None
    date: str | SumKeys | None = None
    status: str | SumKeys | None = None
    parent: str | SumKeys | None = None
    revenue: str | None = None


class CatalogueChangeOut(BaseModel):
    module: str
    #: The approved version of the plan after the change.
    version: int | None = None
    #: The entry of the module after the change. ``None`` for ``@org``.
    entry: CatalogueEntry | None = None
    revenue: RevenueModel = Field(default_factory=RevenueModel)


async def _source_of(db: Any, org: str, module: str) -> Any:
    """The catalogue source of *module*, or a 404 ``unknown_module``.
    ``None`` for the org-level item ``@org``."""
    from gateway.crm_sync import catalogue as cat

    if module == cat.ORG_MODULE:
        return None
    _mode, sources, _roles, _others = await _load(db, org, only=module, count=False)
    src = next((s for s in sources if s.module == module), None)
    if src is None:
        raise HTTPException(status_code=404, detail={
            "code": "unknown_module", "message": "The catalogue has no entry of that name.",
        })
    return src


@router.put("/catalogue/{module}", response_model=CatalogueChangeOut,
            dependencies=_GUARDS)
async def put_catalogue(
    module: str, payload: CatalogueChangeIn, user: UserContext = Depends(get_current_user),
) -> CatalogueChangeOut:
    """An admin changes a part of the catalogue (CRM-Q31-4, §18.14.6).

    The guards of the Field map, and ``questions_allowed`` (else 404
    ``questions_off``). It writes through :func:`write_answer`, the writer
    of the answer route. A field of the wrong type gives 422 ``bad_answer``.
    The actor comes from ``UserContext`` only (R5, R11)."""
    from gateway.crm_sync import catalogue as cat
    from gateway.routes.crm.core import actor
    from gateway.routes.crm.field_rules import _db_conflict, _organization, _require_mirror
    from gateway.routes.crm.flags import questions_allowed
    from gateway.routes.crm.mapping_plans import _approved
    from gateway.routes.crm.questions import questions_off

    org = _organization(user)
    if not questions_allowed(org):
        raise questions_off()
    parts = {name: getattr(payload, name) for name in payload.model_fields_set}
    if not parts:
        raise bad_answer("Name at least one part to change.")
    by = actor(user).lower()
    try:
        async with _tenant_session() as db:
            await _require_mirror(db, org)
            check_parts(module, parts, await _source_of(db, org, module))
            row = await write_answer(db, org, by=by, module=module, parts=parts)
            if row is None:
                row = await _approved(db)
            _mode, sources, roles, others = await _load(db, org)
    except DBAPIError as exc:
        conflict = _db_conflict(exc)
        if conflict is not None:
            raise conflict from exc
        raise
    entry = next((e for e in _build(sources, roles, others) if e.module == module), None)
    value, by_word = cat.revenue_of(cat.stored_items(others))
    return CatalogueChangeOut(
        module=module, version=int(row.version) if row is not None else None,
        entry=_wire(entry) if entry is not None else None,
        revenue=RevenueModel(value=value, by=by_word),
    )


__all__ = ["AUTO", "CATALOGUE_OFF", "CatalogueChangeIn", "CatalogueChangeOut",
           "CatalogueEntry", "CatalogueResponse", "KeyField", "RevenueModel", "SumKeys",
           "bad_answer", "check_parts", "core_sources", "entry_of", "get_catalogue",
           "put_catalogue", "write_answer"]
