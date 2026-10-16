"""CRM · the mapping plan — the versions of the Field map (WS-53 CRM-Q18-1).

Spec: ``project-docs/specs/crm_platform.md`` §18.9.6, "CRM-Q18 · One plan".

    GET  /crm/mapping/plans                      the versions, newest first,
                                                 each with no snapshot
    GET  /crm/mapping/plans/{version}            one version and its snapshot
    POST /crm/mapping/plans                      a draft from a list of changes
    POST /crm/mapping/plans/{version}/approve    approve a draft
    POST /crm/mapping/plans/{version}/restore    approve a copy of an old version

**Two tables, two jobs.** ``crm_mapping_plans`` (migration 277) holds the
versions: ``draft``, ``approved`` and ``superseded``, one ``approved`` at most
for each org. ``crm_field_rules`` stays the live set that the sync reads. A
draft writes no row of ``crm_field_rules``.

**One approve.** :func:`commit` is the one function that approves a version.
The approve route, the restore route, the per-rule ``POST`` and ``PATCH`` of
``field_rules.py`` all call it. In one transaction it marks the old approved
version ``superseded`` and the new one ``approved``. Then it makes
``crm_field_rules`` match the snapshot:

* a rule that stays keeps its row and its id, so the sync neither clears nor
  backfills it again (the applied state of the sync is keyed by the id);
* a rule that leaves the plan gets ``status = 'rejected'``, and the sync undoes
  it through the path it has today;
* a new rule updates the row of its field in place, or inserts one.

The writes keep ``crm_field_rules_one_approved`` whole at each statement: the
rules that leave go first, and a rule that moves to another column first
parks with a NULL column.

**Each rule that the approve must write is checked again** with the checks of
``POST /crm/field-rules`` (``_field_type`` and ``_check_legal``), so a restore
of an old version cannot bring back a rule that is not legal now.

**The plan follows the live set.** The sync can make an approved rule
``stale`` (``crm_sync/field_rules.mark_stale``). Before each change, and on a
read, :func:`lock_plan` compares the approved snapshot with the approved rows.
When they differ, it writes a new approved version of the live set, by
``system``.

**One lock order.** A writer takes the plan lock, then every rule row of
the provider through ``core.FIELD_RULE_LOCK_SQL`` (by entity, then field),
and only then reads the profile or locks one row. The sync's ``mark_stale``
locks through the same statement, so the two never wait in a cycle. A
deadlock that still occurs is a 409 ``plan_busy``, and a retry is safe.

**The first read builds version 1** from the approved rules of the org, under
the unique index on ``(organization_id, version)``, so two first reads write
one row.

⚠️ **Dark twice, and admin only.** Each route has the guards of the Field map:
the CRM switch, ``CRM_MIRROR`` (else 404), ``admin:access:manage`` (else 403),
and Mirror mode (else 409 ``not_mirror``). The session binds the organization
of the caller, and the actor comes from ``UserContext`` only (R5, R11).
Fence: ``tests/unit/test_crm_map_plan_r8.py``.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from acb_auth import UserContext, get_current_user
from acb_common import get_logger
from fastapi import Depends, HTTPException
from gateway.routes.crm.core import (
    FIELD_RULE_LOCK_ORDER,
    FIELD_RULE_LOCK_SQL,
    _tenant_session,
    actor,
    router,
)
from gateway.routes.crm.field_rules import (
    _GUARDS,
    PROVIDER,
    _check_legal,
    _db_conflict,
    _entity,
    _field_type,
    _organization,
    _profile,
    _require_mirror,
    _taken,
)
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

_log = get_logger("crm.mapping_plans")

#: The kind of a rule that maps a provider field to one of our columns. CRM-Q20
#: adds other kinds to the snapshot only, and this module keeps them as they are.
KIND_FIELD = "field"
#: The actor of a version that records a change the sync made.
SYSTEM_ACTOR = "system"
#: The changes of one draft.
MAX_CHANGES = 200

_PLAN_COLUMNS = (
    "version, state, rules, created_by, created_at, approved_by, approved_at, based_on"
)
#: The list carries no snapshot, only its count. One snapshot is
#: ``GET /crm/mapping/plans/{version}``.
_LIST_SQL = (
    "SELECT version, state, jsonb_array_length(rules) AS rule_count, created_by, "
    "created_at, approved_by, approved_at, based_on FROM crm_mapping_plans "
    "ORDER BY version DESC"
)
_ANY_SQL = "SELECT 1 FROM crm_mapping_plans LIMIT 1"
#: The lock of the plan of the bound org: every row of it. Each writer takes
#: it first, so two writers of one org run one after the other.
_LOCK_SQL = "SELECT version FROM crm_mapping_plans ORDER BY version FOR UPDATE"
_APPROVED_SQL = f"SELECT {_PLAN_COLUMNS} FROM crm_mapping_plans WHERE state = 'approved'"
_VERSION_SQL = f"SELECT {_PLAN_COLUMNS} FROM crm_mapping_plans WHERE version = :v"
_NEXT_SQL = "SELECT COALESCE(max(version), 0) + 1 FROM crm_mapping_plans"
#: Version 1. With no conflict target, each unique index is an arbiter, so a
#: race of two first reads inserts one row and raises nothing.
_BOOT_SQL = (
    "INSERT INTO crm_mapping_plans (version, state, rules, created_by, "
    "approved_by, approved_at) VALUES (1, 'approved', CAST(:rules AS jsonb), "
    ":by, :by, now()) ON CONFLICT DO NOTHING"
)
_SUPERSEDE_SQL = (
    "UPDATE crm_mapping_plans SET state = 'superseded' WHERE state = 'approved'"
)
_INSERT_APPROVED_SQL = (
    "INSERT INTO crm_mapping_plans (version, state, rules, created_by, "
    "approved_by, approved_at, based_on) VALUES (:v, 'approved', "
    "CAST(:rules AS jsonb), :by, :by, now(), :based_on) "
    f"RETURNING {_PLAN_COLUMNS}"
)
_INSERT_DRAFT_SQL = (
    "INSERT INTO crm_mapping_plans (version, state, rules, created_by, based_on) "
    "VALUES (:v, 'draft', CAST(:rules AS jsonb), :by, :based_on) "
    f"RETURNING {_PLAN_COLUMNS}"
)
_APPROVE_DRAFT_SQL = (
    "UPDATE crm_mapping_plans SET state = 'approved', rules = CAST(:rules AS jsonb), "
    "approved_by = :by, approved_at = now() WHERE version = :v AND state = 'draft' "
    f"RETURNING {_PLAN_COLUMNS}"
)

_LIVE_COLUMNS = (
    "id, entity, field_api_name, field_type, profile_version, target_column, status"
)
#: Every rule row of the provider, in any status.
_LIVE_SQL = (
    f"SELECT {_LIVE_COLUMNS} FROM crm_field_rules WHERE ext_source = :src "
    f"ORDER BY {FIELD_RULE_LOCK_ORDER}"
)
#: The same rows, locked in the one order that ``mark_stale`` uses too.
_LIVE_LOCK_SQL = FIELD_RULE_LOCK_SQL

_LEAVE_SQL = (
    "UPDATE crm_field_rules SET status = 'rejected', decided_by = :by, "
    "decided_at = now(), updated_at = now() "
    "WHERE id = CAST(:id AS uuid) AND status = 'approved'"
)
_PARK_SQL = (
    "UPDATE crm_field_rules SET target_column = NULL "
    "WHERE id = CAST(:id AS uuid) AND status = 'approved'"
)
#: A rule enters the live set on the row of its field. ``proposed_by``, the
#: confidence and the reason stay only when the admin approves the column that
#: the open proposal names. The right side reads the old row.
_ENTER_SQL = (
    "UPDATE crm_field_rules SET status = 'approved', "
    "target_column = CAST(:column AS text), field_type = :type, "
    "profile_version = :version, "
    "proposed_by = CASE WHEN status = 'proposed' AND "
    "target_column IS NOT DISTINCT FROM CAST(:column AS text) "
    "THEN proposed_by ELSE 'admin' END, "
    "confidence = CASE WHEN status = 'proposed' AND "
    "target_column IS NOT DISTINCT FROM CAST(:column AS text) "
    "THEN confidence ELSE NULL END, "
    "reason = CASE WHEN status = 'proposed' AND "
    "target_column IS NOT DISTINCT FROM CAST(:column AS text) "
    "THEN reason ELSE NULL END, "
    "decided_by = :by, decided_at = now(), updated_at = now() "
    "WHERE id = CAST(:id AS uuid)"
)
_INSERT_RULE_SQL = (
    "INSERT INTO crm_field_rules (ext_source, entity, field_api_name, field_type, "
    "profile_version, target_column, status, proposed_by, decided_by, decided_at) "
    "VALUES (:src, :entity, :field, :type, :version, :column, 'approved', 'admin', "
    ":by, now())"
)


# ── The snapshot ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PlanRule:
    """One rule of kind ``field`` in a snapshot: the columns of an approved
    ``crm_field_rules`` row that define it."""

    entity: str
    field_api_name: str
    field_type: str
    target_column: str
    profile_version: int

    @property
    def key(self) -> tuple[str, str]:
        return (self.entity, self.field_api_name)

    @property
    def identity(self) -> tuple[str, str, str]:
        return (self.entity, self.field_api_name, self.target_column)

    def as_json(self) -> dict[str, Any]:
        return {
            "kind": KIND_FIELD, "entity": self.entity,
            "field_api_name": self.field_api_name, "field_type": self.field_type,
            "target_column": self.target_column,
            "profile_version": self.profile_version,
        }


def _rule_of(item: Mapping[str, Any]) -> PlanRule | None:
    values = [item.get(k) for k in ("entity", "field_api_name", "field_type", "target_column")]
    if not all(isinstance(v, str) and v for v in values):
        return None
    version = item.get("profile_version")
    return PlanRule(
        *values,  # type: ignore[arg-type]
        profile_version=version if isinstance(version, int) else 0,
    )


def split_rules(items: Any) -> tuple[list[PlanRule], list[dict[str, Any]]]:
    """The ``field`` rules of a snapshot, and its items of another kind.

    An item of another kind stays in the snapshot as it is. A ``field`` item
    that does not parse is dropped.
    """
    fields: list[PlanRule] = []
    others: list[dict[str, Any]] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, Mapping):
            continue
        if item.get("kind", KIND_FIELD) != KIND_FIELD:
            others.append(dict(item))
            continue
        rule = _rule_of(item)
        if rule is not None:
            fields.append(rule)
    return fields, others


def _dump(fields: Iterable[PlanRule], others: Sequence[Mapping[str, Any]] = ()) -> str:
    ordered = sorted(fields, key=lambda r: r.key)
    return json.dumps([r.as_json() for r in ordered] + [dict(o) for o in others])


def _identity(fields: Iterable[PlanRule]) -> set[tuple[str, str, str]]:
    return {r.identity for r in fields}


def _rule_from_row(row: Any) -> PlanRule:
    return PlanRule(
        str(row.entity), str(row.field_api_name), str(row.field_type),
        str(row.target_column), int(row.profile_version),
    )


async def _live_rows(db: Any, *, lock: bool = False) -> list[Any]:
    sql = _LIVE_LOCK_SQL if lock else _LIVE_SQL
    return list((await db.execute(text(sql), {"src": PROVIDER})).fetchall())


async def lock_rules(db: Any) -> list[Any]:
    """Lock every rule row of the provider, in the one order. A writer calls
    it BEFORE it locks any single rule row, so it never waits on the sync's
    ``mark_stale`` in a cycle."""
    return await _live_rows(db, lock=True)


async def _live_approved(db: Any) -> list[PlanRule]:
    return [
        _rule_from_row(r) for r in await _live_rows(db)
        if r.status == "approved" and r.target_column is not None
    ]


def _check_unique(fields: Sequence[PlanRule]) -> None:
    """409 when two rules of a snapshot map one field, or fill one column."""
    keys = [r.key for r in fields]
    columns = [(r.entity, r.target_column) for r in fields]
    if len(set(keys)) != len(keys) or len(set(columns)) != len(columns):
        raise _taken()


# ── The lock, version 1 and the live set ────────────────────────────────────


async def _approved(db: Any) -> Any | None:
    return (await db.execute(text(_APPROVED_SQL))).first()


async def _next_version(db: Any) -> int:
    return int((await db.execute(text(_NEXT_SQL))).scalar() or 1)


async def _bootstrap(db: Any, by: str) -> None:
    """Version 1 of an org with no version: its approved rules."""
    if (await db.execute(text(_ANY_SQL))).first() is not None:
        return
    await db.execute(text(_BOOT_SQL), {"rules": _dump(await _live_approved(db)), "by": by})


async def _insert_approved(
    db: Any, fields: Iterable[PlanRule], others: Sequence[Mapping[str, Any]],
    by: str, based_on: int | None,
) -> Any:
    """Supersede the approved version, then add a new approved one. The order
    keeps the partial unique index of migration 277 whole."""
    await db.execute(text(_SUPERSEDE_SQL))
    return (await db.execute(text(_INSERT_APPROVED_SQL), {
        "v": await _next_version(db), "rules": _dump(fields, others),
        "by": by, "based_on": based_on,
    })).first()


async def _follow_live(db: Any) -> Any:
    """The approved version, after it follows the live set. Call it under the
    lock."""
    approved = await _approved(db)
    live = await _live_approved(db)
    fields, others = split_rules(approved.rules) if approved is not None else ([], [])
    if approved is not None and _identity(fields) == _identity(live):
        return approved
    row = await _insert_approved(
        db, live, others, SYSTEM_ACTOR, approved.version if approved is not None else None,
    )
    _log.info("crm.mapping_plan_follows_live version=%s rules=%s", row.version, len(live))
    return row


async def lock_plan(db: Any, by: str) -> Any:
    """Take the lock of the plan of the bound org, and return its approved
    version. It builds version 1 first when the org has none, and it writes
    a new approved version when the live set changed under the plan."""
    await _bootstrap(db, by)
    await db.execute(text(_LOCK_SQL))
    return await _follow_live(db)


# ── The one approve ─────────────────────────────────────────────────────────


async def _checked(db: Any, rule: PlanRule, profile: Any, version: int | None) -> PlanRule:
    """The checks of ``POST /crm/field-rules``, and the rule with the type and
    the profile version of today. A 422 names the field."""
    try:
        kind = await _field_type(db, rule.entity, rule.field_api_name, profile)
        _check_legal(rule.entity, kind, rule.target_column, profile)
    except HTTPException as exc:
        if isinstance(exc.detail, dict):
            exc.detail = {
                **exc.detail, "entity": rule.entity, "field_api_name": rule.field_api_name,
            }
        raise
    return PlanRule(
        rule.entity, rule.field_api_name, kind, rule.target_column, int(version or 0),
    )


async def _write_live_set(
    db: Any, fields: Sequence[PlanRule], rows: Mapping[tuple[str, str], Any], by: str,
) -> None:
    """Make ``crm_field_rules`` match *fields*. *rows* are the locked rows of
    the provider, by ``(entity, field_api_name)``."""
    wanted = {r.key: r for r in fields}
    # 1. A rule that leaves goes first. A rule that moves parks with no column.
    for key, row in rows.items():
        if row.status != "approved":
            continue
        rule = wanted.get(key)
        if rule is None:
            await db.execute(text(_LEAVE_SQL), {"id": str(row.id), "by": by})
        elif rule.target_column != row.target_column:
            await db.execute(text(_PARK_SQL), {"id": str(row.id)})
    # 2. A rule enters on the row of its field, or on a new row.
    for rule in fields:
        row = rows.get(rule.key)
        params = {
            "column": rule.target_column, "type": rule.field_type,
            "version": rule.profile_version, "by": by,
        }
        if row is None:
            await db.execute(text(_INSERT_RULE_SQL), {
                **params, "src": PROVIDER, "entity": rule.entity,
                "field": rule.field_api_name,
            })
        elif row.status == "approved" and row.target_column == rule.target_column:
            continue
        else:
            await db.execute(text(_ENTER_SQL), {**params, "id": str(row.id)})


async def commit(
    db: Any,
    *,
    org: str,
    fields: Sequence[PlanRule],
    others: Sequence[Mapping[str, Any]],
    by: str,
    based_on: int | None,
    draft: int | None = None,
) -> Any:
    """Approve a snapshot, in the caller's transaction, under :func:`lock_plan`.

    With *draft*, the draft of that version becomes the approved version.
    Without it, a new version is added. A rule that the live set holds as it
    is stays as it is. Each other rule is checked again. Returns the approved
    version row.
    """
    # The rule rows are locked first, so the profile and the checks read the
    # state that the writes below act on.
    rows = {
        (str(r.entity), str(r.field_api_name)): r
        for r in await lock_rules(db)
    }
    version, profile = await _profile(db, org)
    final: list[PlanRule] = []
    for rule in fields:
        row = rows.get(rule.key)
        if row is not None and row.status == "approved" and row.target_column == rule.target_column:
            final.append(_rule_from_row(row))
        else:
            final.append(await _checked(db, rule, profile, version))
    _check_unique(final)

    if draft is None:
        plan = await _insert_approved(db, final, others, by, based_on)
    else:
        await db.execute(text(_SUPERSEDE_SQL))
        plan = (await db.execute(text(_APPROVE_DRAFT_SQL), {
            "v": draft, "rules": _dump(final, others), "by": by,
        })).first()
    await _write_live_set(db, final, rows, by)
    _log.info("crm.mapping_plan_approved version=%s rules=%s", plan.version, len(final))
    return plan


async def change_rule(
    db: Any,
    approved: Any,
    *,
    org: str,
    by: str,
    put: PlanRule | None = None,
    remove: tuple[str, str] | None = None,
) -> Any:
    """One change of the approved version, approved at once. It is the path of
    ``POST`` and ``PATCH /crm/field-rules``. *approved* is the row that
    :func:`lock_plan` returned."""
    fields, others = split_rules(approved.rules)
    current = {r.key: r for r in fields}
    if remove is not None:
        current.pop(remove, None)
    if put is not None:
        current[put.key] = put
    return await commit(
        db, org=org, fields=list(current.values()), others=others, by=by,
        based_on=int(approved.version),
    )


# ── The wire ────────────────────────────────────────────────────────────────


class MappingPlanModel(BaseModel):
    version: int
    state: Literal["draft", "approved", "superseded"]
    #: Each item has a ``kind``. A ``field`` item holds ``entity``,
    #: ``field_api_name``, ``field_type``, ``target_column`` and
    #: ``profile_version``.
    rules: list[dict[str, Any]]
    created_by: str
    created_at: datetime | None = None
    approved_by: str | None = None
    approved_at: datetime | None = None
    based_on: int | None = None


class MappingPlanSummaryModel(BaseModel):
    """One version in the list: no snapshot, only the count of its items."""

    version: int
    state: Literal["draft", "approved", "superseded"]
    rule_count: int
    created_by: str
    created_at: datetime | None = None
    approved_by: str | None = None
    approved_at: datetime | None = None
    based_on: int | None = None


class MappingPlansModel(BaseModel):
    #: The approved version.
    approved: int | None = None
    #: The versions, newest first, with no snapshot.
    versions: list[MappingPlanSummaryModel]


class PlanChangeIn(BaseModel):
    """``put`` adds or replaces the rule of a field. ``remove`` takes it out."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["put", "remove"]
    entity: str
    field_api_name: str
    target_column: str | None = None


class PlanDraftIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    changes: list[PlanChangeIn] = Field(min_length=1, max_length=MAX_CHANGES)


def _wire(row: Any) -> MappingPlanModel:
    rules = row.rules if isinstance(row.rules, list) else json.loads(row.rules or "[]")
    return MappingPlanModel(
        version=int(row.version), state=row.state, rules=rules,
        created_by=row.created_by, created_at=row.created_at,
        approved_by=row.approved_by, approved_at=row.approved_at,
        based_on=row.based_on,
    )


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Plan version not found.")


async def _version(db: Any, version: int) -> Any:
    row = (await db.execute(text(_VERSION_SQL), {"v": version})).first()
    if row is None:
        raise _not_found()
    return row


# ── The routes ──────────────────────────────────────────────────────────────


@router.get("/mapping/plans", response_model=MappingPlansModel, dependencies=_GUARDS)
async def list_mapping_plans(user: UserContext = Depends(get_current_user)) -> MappingPlansModel:
    """The versions of the plan. The first call builds version 1."""
    org = _organization(user)
    by = actor(user).lower()
    async with _tenant_session() as db:
        await _require_mirror(db, org)
        await _bootstrap(db, by)
        approved = await _approved(db)
        fields, _others = split_rules(approved.rules) if approved is not None else ([], [])
        if approved is None or _identity(fields) != _identity(await _live_approved(db)):
            await lock_plan(db, by)
        rows = (await db.execute(text(_LIST_SQL))).fetchall()
    versions = [
        MappingPlanSummaryModel(
            version=int(r.version), state=r.state, rule_count=int(r.rule_count or 0),
            created_by=r.created_by, created_at=r.created_at,
            approved_by=r.approved_by, approved_at=r.approved_at, based_on=r.based_on,
        )
        for r in rows
    ]
    return MappingPlansModel(
        approved=next((v.version for v in versions if v.state == "approved"), None),
        versions=versions,
    )


@router.get(
    "/mapping/plans/{version}", response_model=MappingPlanModel, dependencies=_GUARDS,
)
async def get_mapping_plan(
    version: int, user: UserContext = Depends(get_current_user),
) -> MappingPlanModel:
    """One version with its snapshot. It reads only."""
    org = _organization(user)
    async with _tenant_session() as db:
        await _require_mirror(db, org)
        row = await _version(db, version)
    return _wire(row)


@router.post(
    "/mapping/plans", response_model=MappingPlanModel, status_code=201,
    dependencies=_GUARDS,
)
async def create_mapping_plan_draft(
    payload: PlanDraftIn, user: UserContext = Depends(get_current_user),
) -> MappingPlanModel:
    """A draft: the approved version with the changes. It writes no row of
    ``crm_field_rules``. Each ``put`` has the checks of a new field rule."""
    org = _organization(user)
    by = actor(user).lower()
    async with _tenant_session() as db:
        await _require_mirror(db, org)
        approved = await lock_plan(db, by)
        version, profile = await _profile(db, org)
        fields, others = split_rules(approved.rules)
        current = {r.key: r for r in fields}
        for change in payload.changes:
            key = (_entity(change.entity), change.field_api_name.strip())
            if change.op == "remove":
                if key not in current:
                    raise HTTPException(status_code=422, detail={
                        "code": "not_in_plan",
                        "message": "That field has no rule in the approved plan.",
                        "entity": key[0], "field_api_name": key[1],
                    })
                del current[key]
                continue
            column = (change.target_column or "").strip()
            if not column:
                raise HTTPException(status_code=422, detail={
                    "code": "target_required",
                    "message": "A put needs a target column.",
                    "entity": key[0], "field_api_name": key[1],
                })
            current[key] = await _checked(
                db, PlanRule(key[0], key[1], "", column, 0), profile, version,
            )
        drafted = list(current.values())
        _check_unique(drafted)
        row = (await db.execute(text(_INSERT_DRAFT_SQL), {
            "v": await _next_version(db), "rules": _dump(drafted, others),
            "by": by, "based_on": int(approved.version),
        })).first()
    return _wire(row)


@router.post(
    "/mapping/plans/{version}/approve", response_model=MappingPlanModel,
    dependencies=_GUARDS,
)
async def approve_mapping_plan(
    version: int, user: UserContext = Depends(get_current_user),
) -> MappingPlanModel:
    """Approve a draft. The draft must be built on the approved version, else
    409 ``plan_moved``: an approve would undo a change made after it."""
    org = _organization(user)
    by = actor(user).lower()
    try:
        async with _tenant_session() as db:
            await _require_mirror(db, org)
            approved = await lock_plan(db, by)
            draft = await _version(db, version)
            if draft.state != "draft":
                raise HTTPException(status_code=409, detail={
                    "code": "not_draft", "message": "Only a draft can be approved.",
                })
            if draft.based_on != approved.version:
                raise HTTPException(status_code=409, detail={
                    "code": "plan_moved",
                    "message": "The plan changed after this draft. Make a new draft.",
                })
            fields, others = split_rules(draft.rules)
            row = await commit(
                db, org=org, fields=fields, others=others, by=by,
                based_on=draft.based_on, draft=int(draft.version),
            )
    except DBAPIError as exc:
        conflict = _db_conflict(exc)
        if conflict is not None:
            raise conflict from exc
        raise
    return _wire(row)


@router.post(
    "/mapping/plans/{version}/restore", response_model=MappingPlanModel,
    status_code=201, dependencies=_GUARDS,
)
async def restore_mapping_plan(
    version: int, user: UserContext = Depends(get_current_user),
) -> MappingPlanModel:
    """Go back by rolling forward: approve a new version with the content of
    *version*. A draft cannot be restored. Approve it instead."""
    org = _organization(user)
    by = actor(user).lower()
    try:
        async with _tenant_session() as db:
            await _require_mirror(db, org)
            await lock_plan(db, by)
            old = await _version(db, version)
            if old.state == "draft":
                raise HTTPException(status_code=409, detail={
                    "code": "not_restorable",
                    "message": "A draft cannot be restored. Approve it instead.",
                })
            fields, others = split_rules(old.rules)
            row = await commit(
                db, org=org, fields=fields, others=others, by=by,
                based_on=int(old.version),
            )
    except DBAPIError as exc:
        conflict = _db_conflict(exc)
        if conflict is not None:
            raise conflict from exc
        raise
    return _wire(row)


__all__ = [
    "KIND_FIELD",
    "PlanRule",
    "change_rule",
    "commit",
    "lock_plan",
    "split_rules",
]
