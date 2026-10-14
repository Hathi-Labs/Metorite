"""CRM · the lead-conversion mapping — Native mode (WS-53 CRM-C6a).

Spec: ``project-docs/specs/crm_platform.md`` §20.9, the CRM-C6a contract.

    GET /crm/conversion-mapping    each custom lead field, and its target in
                                   each of contact, company and deal
    PUT /crm/conversion-mapping    the whole map, in one transaction

An admin says which contact, company and deal field takes the value of each
custom lead field when a lead converts. ``pipeline.convert_lead`` reads the map
through :func:`conversion_plan`, ``ConversionPlan.new_custom`` and
:func:`fill_existing`. The fixed columns keep their mapping in code.

**What a row of ``crm_convert_mappings`` means.**

- ``target_key`` NULL and ``skip`` false is "auto". No row is "auto" too.
  "Auto" takes the target field with the same key, when that field is live and
  its type is compatible (:func:`compatible`). Else nothing is copied.
- ``skip`` true is "unmapped". The value is not copied.
- A ``target_key`` is "explicit". It wins over "auto" for its target, and a
  target takes one explicit row at most.

⚠️ **Dark, admin only, Native only.** Each route needs the CRM kill switch (at
the router), a place on ``CRM_FIELD_STUDIO_ORGS`` (``flags.require_field_studio``,
else 404) and ``admin:access:manage`` (else 403). The table is in
``core.MIRROR_SETTINGS_TABLES``, so Mirror mode gives 409. The conversion
applies the map only when the flag allows the org, so with the flag off a
conversion is the conversion of before.

**No tenant in the request.** The session binds the organization of the caller
(R5, R11). The acting admin comes from ``UserContext`` only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from acb_auth import UserContext, get_current_user, require_permission
from fastapi import Depends, HTTPException
from gateway.routes.crm.access import ADMIN_PERMISSION
from gateway.routes.crm.core import (
    CustomMerge,
    _tenant_session,
    assert_writable,
    current_tenant,
    from_jsonb,
    is_mirror_mode,
    refuse_in_mirror_mode,
    router,
    update_row,
)
from gateway.routes.crm.field_studio import (
    TYPE_CHANGES,
    CustomField,
    check_value,
    is_empty,
)
from gateway.routes.crm.flags import field_studio_allowed, require_field_studio
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

_GUARDS = [Depends(require_field_studio), require_permission(ADMIN_PERMISSION)]

#: The target entities, as ``crm_convert_mappings.target_entity`` and
#: ``crm_field_defs.entity`` both hold them, in the order of the screen.
TARGET_ENTITIES: tuple[str, ...] = ("contact", "company", "deal")

TargetEntity = Literal["contact", "company", "deal"]
Reason = Literal["auto", "skip", "explicit"]

#: The most rows a ``PUT`` may hold: three targets for each of 500 lead fields.
MAX_ROWS = 1500

MIRROR_MESSAGE = "The conversion mapping comes from Zoho. Change it in Zoho."

#: One ``PUT`` at a time for each org, until the commit (C6a review, P2). The
#: idiom of ``connection.py``: a transaction lock on a key of the org. Without
#: it, two writes each delete the old rows and insert their own, and the
#: second insert meets the first one's rows.
_LOCK_SQL = "SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"


def _unprocessable(code: str, message: str, **extra: Any) -> HTTPException:
    return HTTPException(
        status_code=422, detail={"code": code, "message": message, **extra},
    )


# ── The rules, with no database ─────────────────────────────────────────────


def compatible(source: CustomField, target: CustomField) -> bool:
    """True when a value of *source* can fill *target*.

    The types are equal, or the type change matrix (§20.7 item 9) allows the
    change. A lookup pairs only with a lookup of the same target entity,
    because its value is the id of a record of that entity.
    """
    if source.type == "lookup" or target.type == "lookup":
        return (
            source.type == target.type == "lookup"
            and source.lookup_target == target.lookup_target
        )
    return source.type == target.type or target.type in TYPE_CHANGES.get(
        source.type, frozenset(),
    )


@dataclass(frozen=True)
class MapRow:
    """One stored row of ``crm_convert_mappings``."""

    source_key: str
    target_entity: str
    target_key: str | None
    skip: bool


@dataclass(frozen=True)
class Cell:
    """The effective target of one lead field in one target entity."""

    target_key: str | None
    reason: Reason
    #: Why an explicit target does not take a value now, or None.
    problem: str | None = None


def target_problem(source: CustomField, target: CustomField | None) -> str | None:
    """Why *target* cannot take a value of *source* now, or None."""
    if target is None:
        return "target_missing"
    if target.retired:
        return "target_retired"
    if not compatible(source, target):
        return "type_mismatch"
    return None


def effective_map(
    sources: dict[str, CustomField],
    targets: dict[str, dict[str, CustomField]],
    rows: list[MapRow],
) -> dict[tuple[str, str], Cell]:
    """The effective target of each live lead field in each target entity.

    *sources* are the custom lead fields, *targets* the custom fields of each
    target entity by key. A retired lead field is not in the map. "Auto" never
    takes a target that an explicit row names.

    An explicit row whose target now has another type still claims it. The
    auto cell that it blocks gets the problem ``type_mismatch``, so the
    conversion reports the value in ``skipped`` (C6a review, note 4).
    """
    stored = {(r.source_key, r.target_entity): r for r in rows}
    claimed = {
        (r.target_entity, r.target_key): r for r in rows
        if r.target_key is not None and not r.skip
        and r.source_key in sources and not sources[r.source_key].retired
    }
    out: dict[tuple[str, str], Cell] = {}
    for key, source in sources.items():
        if source.retired:
            continue
        for entity in TARGET_ENTITIES:
            row = stored.get((key, entity))
            fields = targets.get(entity, {})
            if row is not None and row.skip:
                out[(key, entity)] = Cell(None, "skip")
            elif row is not None and row.target_key is not None:
                out[(key, entity)] = Cell(
                    row.target_key, "explicit",
                    target_problem(source, fields.get(row.target_key)),
                )
            else:
                same = fields.get(key)
                open_ = same is not None and target_problem(source, same) is None
                claim = claimed.get((entity, key))
                if open_ and claim is None:
                    out[(key, entity)] = Cell(key, "auto")
                elif open_ and claim is not None and target_problem(
                    sources[claim.source_key], same,
                ) == "type_mismatch":
                    out[(key, entity)] = Cell(None, "auto", "type_mismatch")
                else:
                    out[(key, entity)] = Cell(None, "auto")
    return out


def choices(source: CustomField, fields: dict[str, CustomField]) -> list[str]:
    """The keys of the live target fields that a value of *source* can fill."""
    return [k for k, f in fields.items() if not f.retired and compatible(source, f)]


def as_target_value(source: CustomField, target: CustomField, value: Any) -> Any:
    """*value* in the shape of *target*. A select value becomes a list of one
    for a multi-select, as the type change of item 9 does."""
    if source.type == "select" and target.type == "multiselect" and isinstance(value, str):
        return [value]
    return value


def inactive_options(target: CustomField, value: Any) -> list[str]:
    """The values in *value* that are not an active option of *target*."""
    if target.type not in ("select", "multiselect"):
        return []
    active = {
        str(o.get("value")) for o in target.options
        if isinstance(o, dict) and o.get("value") is not None and not o.get("retired")
    }
    items = value if isinstance(value, list) else [value]
    return [str(v) for v in items if str(v) not in active]


# ── The reads ───────────────────────────────────────────────────────────────

_FIELDS_SQL = (
    "SELECT key, label, type, options, required, default_value::text AS "
    "default_value, lookup_target, is_system, retired_at FROM crm_field_defs "
    "WHERE entity = :entity AND NOT is_system ORDER BY position, key"
)

_ROWS_SQL = (
    "SELECT source_key, target_entity, target_key, skip FROM crm_convert_mappings "
    "ORDER BY source_key, target_entity"
)


async def _fields(db: Any, entity: str) -> dict[str, CustomField]:
    """The custom fields of one stored entity (``lead``, ``contact`` …), in order."""
    rows = (await db.execute(text(_FIELDS_SQL), {"entity": entity})).fetchall()
    return {r.key: CustomField.from_row(r) for r in rows}


async def _rows(db: Any) -> list[MapRow]:
    rows = (await db.execute(text(_ROWS_SQL))).fetchall()
    return [
        MapRow(r.source_key, r.target_entity, r.target_key, bool(r.skip)) for r in rows
    ]


@dataclass
class _Read:
    sources: dict[str, CustomField]
    targets: dict[str, dict[str, CustomField]]
    rows: list[MapRow]

    @property
    def cells(self) -> dict[tuple[str, str], Cell]:
        return effective_map(self.sources, self.targets, self.rows)


async def _read(db: Any) -> _Read:
    return _Read(
        sources=await _fields(db, "lead"),
        targets={e: await _fields(db, e) for e in TARGET_ENTITIES},
        rows=await _rows(db),
    )


# ── The wire ────────────────────────────────────────────────────────────────


class TargetField(BaseModel):
    key: str
    label: str
    type: str
    retired: bool = False


class MappingCell(BaseModel):
    """The target of one lead field in one entity."""

    #: The target field that takes the value, or None for none.
    target_key: str | None
    reason: Reason
    #: Why an explicit target takes no value now: ``target_missing``,
    #: ``target_retired`` or ``type_mismatch``. None when it does.
    problem: str | None = None
    #: The live target fields that a value of the lead field can fill.
    choices: list[str]


class LeadFieldMapping(BaseModel):
    key: str
    label: str
    type: str
    mapping: dict[str, MappingCell]


class ConversionMapping(BaseModel):
    """``GET /crm/conversion-mapping``: each live custom lead field, in order."""

    fields: list[LeadFieldMapping]
    #: The custom fields of each target entity, also the retired ones, so the
    #: screen can name an explicit target that is retired now.
    targets: dict[str, list[TargetField]]


def _wire(read: _Read) -> ConversionMapping:
    cells = read.cells
    return ConversionMapping(
        fields=[
            LeadFieldMapping(
                key=key, label=source.label, type=source.type,
                mapping={
                    entity: MappingCell(
                        target_key=cells[(key, entity)].target_key,
                        reason=cells[(key, entity)].reason,
                        problem=cells[(key, entity)].problem,
                        choices=choices(source, read.targets[entity]),
                    )
                    for entity in TARGET_ENTITIES
                },
            )
            for key, source in read.sources.items() if not source.retired
        ],
        targets={
            entity: [
                TargetField(key=f.key, label=f.label, type=f.type, retired=f.retired)
                for f in read.targets[entity].values()
            ]
            for entity in TARGET_ENTITIES
        },
    )


# ── The routes ──────────────────────────────────────────────────────────────


class MappingIn(BaseModel):
    """One row of a ``PUT``. No ``target_key`` and no ``skip`` means "auto"."""

    model_config = ConfigDict(extra="forbid")

    source_key: str = Field(min_length=1, max_length=100)
    target_entity: TargetEntity
    target_key: str | None = Field(default=None, min_length=1, max_length=100)
    skip: bool = False


class ConversionMappingIn(BaseModel):
    """``PUT /crm/conversion-mapping``: the whole map. A row that the body
    leaves out is gone after the write, so its lead field goes back to auto."""

    model_config = ConfigDict(extra="forbid")

    rows: list[MappingIn] = Field(max_length=MAX_ROWS)


@router.get(
    "/conversion-mapping", response_model=ConversionMapping, dependencies=_GUARDS,
)
async def get_conversion_mapping(
    user: UserContext = Depends(get_current_user),
) -> ConversionMapping:
    """Each live custom lead field, and its effective target in each entity."""
    async with _tenant_session() as db:
        await refuse_in_mirror_mode(db, "mirror_mode_settings", MIRROR_MESSAGE)
        return _wire(await _read(db))


def _check_target(
    source: CustomField, target: CustomField | None, entity: str, key: str,
    where: dict[str, Any],
) -> None:
    """422 when a new or changed explicit row names a target that cannot take
    the value of *source*: missing, retired, or of an incompatible type."""
    if target is None:
        raise _unprocessable(
            "mapping_target", f"'{key}' is not a custom field of the {entity}.", **where,
        )
    if target.retired:
        raise _unprocessable(
            "mapping_target_retired",
            f"{target.label} is retired. Set this cell to Auto or Skip, or restore "
            "the field.",
            **where,
        )
    if not compatible(source, target):
        raise _unprocessable(
            "mapping_type",
            f"{source.label} ({source.type}) cannot fill {target.label} ({target.type}).",
            **where,
        )


def check_rows(read: _Read, rows: list[MappingIn]) -> None:
    """422 for the first row that the map cannot hold. Nothing is written.

    An explicit row that equals its stored row passes unchecked, also when its
    target is retired or has another type now (C6a review, P1). The screen
    sends the whole map, so a broken row must not block a change to another
    cell. The read names its problem, and the conversion skips it. Only a new
    or a changed row is checked against the target.
    """
    kept = {
        (r.source_key, r.target_entity, r.target_key) for r in read.rows
        if r.target_key is not None and not r.skip
    }
    seen: set[tuple[str, str]] = set()
    taken: dict[tuple[str, str], str] = {}
    for row in rows:
        source = read.sources.get(row.source_key)
        if source is None or source.retired:
            raise _unprocessable(
                "mapping_source",
                f"'{row.source_key}' is not a live custom field of leads.",
                source_key=row.source_key,
            )
        pair = (row.source_key, row.target_entity)
        if pair in seen:
            raise _unprocessable(
                "mapping_twice",
                f"{source.label} has two rows for the {row.target_entity}.",
                source_key=row.source_key, target_entity=row.target_entity,
            )
        seen.add(pair)
        if row.target_key is None:
            continue
        if row.skip:
            raise _unprocessable(
                "mapping_skip_target",
                f"{source.label}: a skipped field has no target.",
                source_key=row.source_key, target_entity=row.target_entity,
            )
        target = read.targets[row.target_entity].get(row.target_key)
        where = {"source_key": row.source_key, "target_entity": row.target_entity,
                 "target_key": row.target_key}
        if (row.source_key, row.target_entity, row.target_key) not in kept:
            _check_target(source, target, row.target_entity, row.target_key, where)
        other = taken.get((row.target_entity, row.target_key))
        if other is not None:
            name = target.label if target is not None else row.target_key
            raise _unprocessable(
                "mapping_target_twice",
                f"{name} takes one lead field only. "
                f"{read.sources[other].label} maps to it too.",
                **where,
            )
        taken[(row.target_entity, row.target_key)] = row.source_key


@router.put(
    "/conversion-mapping", response_model=ConversionMapping, dependencies=_GUARDS,
)
async def put_conversion_mapping(
    payload: ConversionMappingIn, user: UserContext = Depends(get_current_user),
) -> ConversionMapping:
    """Write the whole map in one transaction. A refused body writes nothing."""
    try:
        async with _tenant_session() as db:
            await assert_writable(db, current_tenant(), "crm_convert_mappings", None)
            await db.execute(
                text(_LOCK_SQL), {"key": f"crm.convert_mapping:{current_tenant()}"},
            )
            check_rows(await _read(db), payload.rows)
            await db.execute(text("DELETE FROM crm_convert_mappings"))
            for row in payload.rows:
                await db.execute(text(
                    "INSERT INTO crm_convert_mappings (source_key, target_entity, "
                    "target_key, skip) VALUES (:source_key, :target_entity, "
                    ":target_key, :skip)"
                ), {
                    "source_key": row.source_key, "target_entity": row.target_entity,
                    "target_key": row.target_key, "skip": row.skip,
                })
            return _wire(await _read(db))
    except DBAPIError as exc:
        code = getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "pgcode", None)
        if code == "23505":
            raise HTTPException(status_code=409, detail={
                "code": "mapping_changed",
                "message": "Another admin changed the map at the same time. Try again.",
            }) from exc
        raise


# ── The conversion (pipeline.convert_lead) ──────────────────────────────────


@dataclass
class ConversionPlan:
    """The values of one lead for each target entity, and what the map skips.

    ``values[entity]`` maps a target key to ``(source_key, value)``. Each value
    passed the check of its target field. ``skipped[entity]`` lists the values
    that the map names and the target cannot take, each with its reason.
    """

    values: dict[str, dict[str, tuple[str, Any]]] = field(default_factory=dict)
    skipped: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    def new_custom(self, entity: str) -> dict[str, Any]:
        """The mapped values of a new record of *entity*, by target key."""
        return {k: v for k, (_, v) in self.values.get(entity, {}).items()}


def _skip(entity: str, source: str, target: str | None, reason: str, message: str) -> dict[str, Any]:
    return {
        "entity": entity, "source_key": source, "target_key": target,
        "reason": reason, "message": message,
    }


_PROBLEM_TEXT = {
    "target_missing": "The target field does not exist.",
    "target_retired": "The target field is retired.",
    "type_mismatch": "The target field has a type that cannot take the value.",
}


async def conversion_plan(db: Any, lead: Any, organization_id: Any) -> ConversionPlan:
    """The mapped values of *lead*. Empty when the flag does not allow the org,
    and in Mirror mode, so the conversion is the conversion of before."""
    plan = ConversionPlan()
    if not field_studio_allowed(organization_id):
        return plan
    if await is_mirror_mode(db, current_tenant()):
        return plan
    read = await _read(db)
    stored = from_jsonb(getattr(lead, "custom", None)) or {}
    if not isinstance(stored, dict):
        return plan
    for (source_key, entity), cell in read.cells.items():
        value = stored.get(source_key)
        if is_empty(value):
            continue
        if cell.reason == "auto" and cell.problem is not None:
            plan.skipped.setdefault(entity, []).append(_skip(
                entity, source_key, source_key, cell.problem,
                "An explicit row names the field with the same key, and its type "
                "cannot take the value of that row. So the auto copy does not run.",
            ))
            continue
        if cell.target_key is None:
            continue
        if cell.problem is not None:
            plan.skipped.setdefault(entity, []).append(_skip(
                entity, source_key, cell.target_key, cell.problem,
                _PROBLEM_TEXT[cell.problem],
            ))
            continue
        source = read.sources[source_key]
        target = read.targets[entity][cell.target_key]
        value = as_target_value(source, target, value)
        bad = inactive_options(target, value)
        if bad:
            plan.skipped.setdefault(entity, []).append(_skip(
                entity, source_key, cell.target_key, "option_inactive",
                f"{target.label} has no active option {', '.join(bad)}.",
            ))
            continue
        try:
            checked = await check_value(db, target, value)
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            plan.skipped.setdefault(entity, []).append(_skip(
                entity, source_key, cell.target_key, "invalid_value",
                str(detail.get("message") or exc.detail),
            ))
            continue
        plan.values.setdefault(entity, {})[cell.target_key] = (source_key, checked)
    return plan


async def fill_existing(
    db: Any, plan: ConversionPlan, entity: str, table: str, row: Any,
) -> tuple[Any, list[dict[str, Any]]]:
    """Fill the empty values of a record that the conversion reuses.

    A contact matched by email, a company matched by name, or a record that
    the caller names. The map never replaces a value that the record holds:
    such a value is in the skipped list with ``record_has_value``. One merge
    per key (``core.CustomMerge``), through the guarded ``update_row``.
    """
    wanted = plan.values.get(entity, {})
    if not wanted:
        return row, []
    # C6a review, P2: read `custom` again under a row lock, so a write that
    # lands between the match and this fill is seen and never replaced.
    # FOR NO KEY UPDATE, not FOR UPDATE: the deal and link inserts take FOR KEY
    # SHARE on this row through their foreign keys. FOR UPDATE conflicts with
    # that, so two conversions that reuse one contact and one company could
    # deadlock. FOR NO KEY UPDATE is the lock that update_row takes anyway.
    locked = (await db.execute(
        text(f"SELECT custom FROM {table} WHERE id = CAST(:id AS uuid) FOR NO KEY UPDATE"),
        {"id": str(row.id)},
    )).first()
    held = from_jsonb(getattr(locked, "custom", None)) if locked is not None else None
    held = held or {}
    if not isinstance(held, dict):
        held = {}
    put: dict[str, Any] = {}
    kept: list[dict[str, Any]] = []
    for target_key, (source_key, value) in wanted.items():
        if is_empty(held.get(target_key)):
            put[target_key] = value
        else:
            kept.append(_skip(
                entity, source_key, target_key, "record_has_value",
                "The record already holds a value, and the conversion keeps it.",
            ))
    if not put:
        return row, kept
    written = await update_row(db, table, str(row.id), {"custom": CustomMerge(put=put)})
    return written, kept


__all__ = [
    "TARGET_ENTITIES",
    "Cell",
    "ConversionPlan",
    "MapRow",
    "as_target_value",
    "check_rows",
    "choices",
    "compatible",
    "conversion_plan",
    "effective_map",
    "fill_existing",
    "inactive_options",
    "target_problem",
]
