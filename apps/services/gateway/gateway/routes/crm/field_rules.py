"""CRM · field rules — the Field map of the mirror (WS-53 CRM-Q7a).

Spec: ``project-docs/specs/crm_platform.md`` §18.7, "CRM-Q7a: the rule table
and the Field map".

    GET    /crm/field-rules?entity=      the fields, the rules and the targets
    POST   /crm/field-rules              an admin picks a target by hand
    PATCH  /crm/field-rules/{rule_id}    approve or reject

⚠️ **Dark twice, and admin only.** Each route needs the CRM kill switch (at
the router), ``CRM_MIRROR`` (``flags.require_crm_mirror``, else 404) and
``admin:access:manage`` (else 403). The organization must be in Mirror mode,
else 409 ``not_mirror``.

**The routes write no CRM record.** They write ``crm_field_rules`` only.
Approve and reject set ``status``. The next sync run applies the decision
(``gateway/crm_sync/field_rules.py``): it backfills an approved rule, and it
undoes a rejected one.

**What is legal.** The rules of ``mapping.rule_problem``: the target is one of
``FIELD_RULE_TARGETS``, the field's type agrees with the column, and a fixed
column opens only when its standard field is off every layout of the latest
source profile. Any other rule is a 422 that names the reason. The field must
be a field that goes to ``custom`` today (a ``crm_field_defs`` row of the
entity). Only one approved rule may fill a column, and only one rule exists for
each field. A second approved rule for a column is a 409.

**Entity words.** ``entity`` takes the API slug (``leads``, ``contacts``,
``organizations``, ``deals``) or the stored word (``lead``, ``contact``,
``company``, ``deal``). The answer carries the stored word.

**No tenant in the request.** The session binds the organization of the
caller (R5, R11). The acting admin comes from ``UserContext`` only.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from acb_auth import UserContext, get_current_user, require_permission
from fastapi import Depends, HTTPException, Query
from gateway.crm_sources.registry import mapping_for
from gateway.routes.crm.access import ADMIN_PERMISSION, read_settings
from gateway.routes.crm.core import (
    FIELD_ENTITY_BY_SLUG,
    _tenant_session,
    actor,
    checked_uuid,
    router,
)
from gateway.routes.crm.flags import require_crm_mirror
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

#: The provider of every rule this build writes.
PROVIDER = "zoho"

#: The entity words a rule can name, from either vocabulary.
_ENTITY_WORDS: dict[str, str] = {
    **FIELD_ENTITY_BY_SLUG,
    **{word: word for word in FIELD_ENTITY_BY_SLUG.values()},
}

_GUARDS = [Depends(require_crm_mirror), require_permission(ADMIN_PERMISSION)]

_COLUMNS = (
    "id, entity, field_api_name, field_type, profile_version, target_column, "
    "status, proposed_by, confidence, reason, decided_by, decided_at, "
    "created_at, updated_at"
)

_RULES_SQL = (
    f"SELECT {_COLUMNS} FROM crm_field_rules WHERE ext_source = :src "
    "AND entity = :entity ORDER BY field_api_name"
)
_RULE_SQL = f"SELECT {_COLUMNS} FROM crm_field_rules WHERE id = CAST(:id AS uuid)"
#: CRM-C4a (§20.7 item 11): a row with ``is_system`` overlays a fixed column,
#: so it is not a field that goes to ``custom``.
_FIELD_SQL = (
    "SELECT key, label, type, options, visible, position FROM crm_field_defs "
    "WHERE entity = :entity AND source = :src AND NOT is_system "
    "ORDER BY position, key"
)
_TAKEN_SQL = (
    "SELECT id, field_api_name FROM crm_field_rules WHERE ext_source = :src "
    "AND entity = :entity AND status = 'approved' "
    "AND (target_column = :column OR field_api_name = :field)"
)
_UPSERT_SQL = (
    "INSERT INTO crm_field_rules (ext_source, entity, field_api_name, field_type, "
    "profile_version, target_column, status, proposed_by, confidence, reason, "
    "decided_by, decided_at) VALUES (:src, :entity, :field, :type, :version, "
    ":column, 'approved', 'admin', NULL, NULL, :by, now()) "
    "ON CONFLICT (organization_id, ext_source, entity, field_api_name) DO UPDATE SET "
    "field_type = EXCLUDED.field_type, profile_version = EXCLUDED.profile_version, "
    "target_column = EXCLUDED.target_column, status = 'approved', "
    "proposed_by = 'admin', confidence = NULL, reason = NULL, "
    "decided_by = EXCLUDED.decided_by, decided_at = now(), updated_at = now() "
    # An approved rule is never replaced here: reject it first.
    "WHERE crm_field_rules.status <> 'approved' "
    f"RETURNING {_COLUMNS}"
)
_DECIDE_SQL = (
    "UPDATE crm_field_rules SET status = :status, decided_by = :by, "
    "decided_at = now(), updated_at = now(), field_type = :type, "
    "profile_version = :version WHERE id = CAST(:id AS uuid) "
    f"RETURNING {_COLUMNS}"
)

#: A sentence for each reason a rule is not legal (``mapping.rule_problem``).
_PROBLEMS: dict[str, str] = {
    "unknown_target": "That column is not a field map target for this entity.",
    "type_mismatch": "The field's type does not fit that column.",
    "standard_field_on_layout": (
        "Zoho still shows the standard field of that column on a layout, so "
        "the column is not free."
    ),
    "layout_unknown": (
        "The sync has not read the Zoho layouts yet, so a standard column "
        "cannot take a rule. Sync, then try again."
    ),
}


class FieldRuleModel(BaseModel):
    id: str
    entity: str
    field_api_name: str
    field_type: str
    profile_version: int
    target_column: str | None = None
    status: Literal["proposed", "approved", "rejected", "stale"]
    proposed_by: Literal["ai", "admin"]
    confidence: float | None = None
    reason: str | None = None
    decided_by: str | None = None
    decided_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class FieldMapField(BaseModel):
    """A field that goes to ``custom`` today. ``targets`` are the columns that
    it may fill now, by the rules of ``mapping.rule_problem``, so the client
    holds no copy of them."""

    key: str
    label: str
    type: str
    visible: bool = True
    options: list[Any] | None = None
    targets: list[str] = []


class FieldMapTarget(BaseModel):
    """One legal column. ``open`` is False for a fixed column whose standard
    field is still on a layout, or whose layouts are not read yet."""

    column: str
    column_type: str
    kind: Literal["free", "fixed"]
    standard_field: str | None = None
    open: bool
    reason: str | None = None


class FieldMapModel(BaseModel):
    entity: str
    profile_version: int | None = None
    fields: list[FieldMapField]
    rules: list[FieldRuleModel]
    targets: list[FieldMapTarget]


class FieldRuleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity: str
    field_api_name: str
    target_column: str


class FieldRulePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["approved", "rejected"]


def _entity(value: str) -> str:
    word = _ENTITY_WORDS.get((value or "").strip().lower())
    if word is None:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown entity '{value}'. One of: {list(FIELD_ENTITY_BY_SLUG)}.",
        )
    return word


def _organization(user: UserContext) -> str:
    if not user.organization_id:
        raise HTTPException(status_code=403, detail="Forbidden: you are in no organization.")
    return str(user.organization_id)


def _wire(row: Any) -> FieldRuleModel:
    confidence = row.confidence
    return FieldRuleModel(
        id=str(row.id), entity=row.entity, field_api_name=row.field_api_name,
        field_type=row.field_type, profile_version=int(row.profile_version),
        target_column=row.target_column, status=row.status,
        proposed_by=row.proposed_by,
        confidence=float(confidence) if isinstance(confidence, Decimal | float | int) else None,
        reason=row.reason, decided_by=row.decided_by, decided_at=row.decided_at,
        created_at=row.created_at, updated_at=row.updated_at,
    )


async def _require_mirror(db: Any, org: str) -> None:
    settings = await read_settings(db, org)
    if getattr(settings, "mode", None) != "mirror":
        raise HTTPException(status_code=409, detail={
            "code": "not_mirror",
            "message": "The Field map is for a CRM that mirrors Zoho.",
        })


async def _profile(db: Any, org: str) -> tuple[int | None, dict[str, Any] | None]:
    from gateway.crm_sync import profiles

    latest = await profiles.latest(db, org)
    return (latest.version, latest.profile) if latest is not None else (None, None)


async def _field_type(db: Any, entity: str, field_api_name: str, profile: Any) -> str:
    """The type of a field that goes to ``custom``, or 422.

    The field must have a ``crm_field_defs`` row. When the latest profile
    knows the fields of the entity, the profile is newer, so its type wins and
    a field that the profile no longer lists is a 422. The sync makes a rule
    stale by the same profile, so an admin cannot approve a rule that the next
    discovery would make stale again.
    """
    rows = (await db.execute(text(_FIELD_SQL), {"entity": entity, "src": PROVIDER})).fetchall()
    stored = next((str(r.type) for r in rows if r.key == field_api_name), None)
    if stored is None:
        raise HTTPException(status_code=422, detail={
            "code": "unknown_field",
            "message": "That field does not go to custom for this entity.",
        })
    known = mapping_for(PROVIDER).entity_field_types(profile, entity)
    if known is None:
        return stored
    if field_api_name not in known:
        raise HTTPException(status_code=422, detail={
            "code": "field_gone",
            "message": "Zoho no longer has that field.",
        })
    return known[field_api_name]


def _check_legal(entity: str, field_type: str, column: str, profile: Any) -> None:
    mapping = mapping_for(PROVIDER)
    problem = mapping.rule_problem(
        entity, field_type, column, mapping.entity_layout_fields(profile, entity),
    )
    if problem is not None:
        raise HTTPException(status_code=422, detail={
            "code": problem, "message": _PROBLEMS.get(problem, "That rule is not legal."),
        })


async def _check_free(db: Any, entity: str, column: str, field: str, rule_id: str | None) -> None:
    """409 when another approved rule fills *column* or maps *field*."""
    rows = (await db.execute(text(_TAKEN_SQL), {
        "src": PROVIDER, "entity": entity, "column": column, "field": field,
    })).fetchall()
    if any(str(r.id) != rule_id for r in rows):
        raise _taken()


def _taken() -> HTTPException:
    return HTTPException(status_code=409, detail={
        "code": "rule_taken",
        "message": (
            "Another approved rule already fills that column, or maps that "
            "field. Reject it first."
        ),
    })


def _unique_violation(exc: DBAPIError) -> bool:
    seen: BaseException | None = exc
    while seen is not None:
        code = getattr(seen, "sqlstate", None) or getattr(seen, "pgcode", None)
        if code == "23505":
            return True
        seen = getattr(seen, "orig", None) or seen.__cause__
    return False


def _targets(entity: str, profile: Any) -> list[FieldMapTarget]:
    mapping = mapping_for(PROVIDER)
    layout = mapping.entity_layout_fields(profile, entity)
    out: list[FieldMapTarget] = []
    for target in mapping.FIELD_RULE_TARGETS[entity].values():
        reason = None
        if target.standard_field is not None:
            if layout is None:
                reason = "layout_unknown"
            elif target.standard_field in layout:
                reason = "standard_field_on_layout"
        out.append(FieldMapTarget(
            column=target.column, column_type=target.column_type,
            kind="free" if target.standard_field is None else "fixed",
            standard_field=target.standard_field, open=reason is None, reason=reason,
        ))
    return out


@router.get("/field-rules", response_model=FieldMapModel, dependencies=_GUARDS)
async def get_field_rules(
    entity: str = Query(...), user: UserContext = Depends(get_current_user),
) -> FieldMapModel:
    """The Field map of one entity: the fields that go to ``custom``, the rules
    and the legal targets."""
    org = _organization(user)
    word = _entity(entity)
    async with _tenant_session() as db:
        await _require_mirror(db, org)
        version, profile = await _profile(db, org)
        fields = (await db.execute(
            text(_FIELD_SQL), {"entity": word, "src": PROVIDER},
        )).fetchall()
        rules = (await db.execute(
            text(_RULES_SQL), {"entity": word, "src": PROVIDER},
        )).fetchall()
    mapping = mapping_for(PROVIDER)
    layout = mapping.entity_layout_fields(profile, word)
    known = mapping.entity_field_types(profile, word)
    columns = list(mapping.FIELD_RULE_TARGETS[word])

    def legal(field_type: str) -> list[str]:
        return [c for c in columns if mapping.rule_problem(word, field_type, c, layout) is None]

    return FieldMapModel(
        entity=word,
        profile_version=version,
        fields=[
            FieldMapField(
                key=f.key, label=f.label, type=f.type, visible=bool(f.visible),
                options=f.options if isinstance(f.options, list) else None,
                # A field that the latest profile no longer lists fills nothing.
                targets=(
                    legal(known[f.key] if known is not None else f.type)
                    if known is None or f.key in known else []
                ),
            )
            for f in fields
        ],
        rules=[_wire(r) for r in rules],
        targets=_targets(word, profile),
    )


@router.post(
    "/field-rules", response_model=FieldRuleModel, status_code=201, dependencies=_GUARDS,
)
async def create_field_rule(
    payload: FieldRuleIn, user: UserContext = Depends(get_current_user),
) -> FieldRuleModel:
    """An admin picks a target by hand. The rule is approved at once, with
    ``proposed_by = 'admin'``. The next sync run applies it."""
    org = _organization(user)
    word = _entity(payload.entity)
    field = payload.field_api_name.strip()
    column = payload.target_column.strip()
    try:
        async with _tenant_session() as db:
            await _require_mirror(db, org)
            version, profile = await _profile(db, org)
            field_type = await _field_type(db, word, field, profile)
            _check_legal(word, field_type, column, profile)
            await _check_free(db, word, column, field, None)
            row = (await db.execute(text(_UPSERT_SQL), {
                "src": PROVIDER, "entity": word, "field": field, "type": field_type,
                "version": int(version or 0), "column": column,
                "by": actor(user).lower(),
            })).first()
            if row is None:  # the field has an approved rule already
                raise _taken()
    except DBAPIError as exc:
        if _unique_violation(exc):
            raise _taken() from exc
        raise
    return _wire(row)


@router.patch(
    "/field-rules/{rule_id}", response_model=FieldRuleModel, dependencies=_GUARDS,
)
async def decide_field_rule(
    rule_id: str, payload: FieldRulePatch, user: UserContext = Depends(get_current_user),
) -> FieldRuleModel:
    """Approve or reject one rule. Only ``status`` and the decision change.

    An approve checks the rule again against the fields and the profile of
    today, and it takes their type and version. A reject is always legal.
    """
    org = _organization(user)
    wanted = checked_uuid(rule_id, "rule_id")
    try:
        async with _tenant_session() as db:
            await _require_mirror(db, org)
            current = (await db.execute(text(_RULE_SQL), {"id": wanted})).first()
            if current is None:
                raise HTTPException(status_code=404, detail="Field rule not found.")
            version, profile = await _profile(db, org)
            field_type = str(current.field_type)
            if payload.status == "approved":
                if current.status == "approved":
                    return _wire(current)
                field_type = await _field_type(
                    db, current.entity, current.field_api_name, profile,
                )
                _check_legal(current.entity, field_type, current.target_column, profile)
                await _check_free(
                    db, current.entity, current.target_column, current.field_api_name, wanted,
                )
            row = (await db.execute(text(_DECIDE_SQL), {
                "status": payload.status, "by": actor(user).lower(), "id": wanted,
                "type": field_type,
                "version": int(version if version is not None else current.profile_version),
            })).first()
    except DBAPIError as exc:
        if _unique_violation(exc):
            raise _taken() from exc
        raise
    return _wire(row)
