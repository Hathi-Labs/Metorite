"""CRM · the mapping plan — the versions of the Field map (WS-53 CRM-Q18-1).

Spec: ``project-docs/specs/crm_platform.md`` §18.9.6, "CRM-Q18 · One plan".

    GET  /crm/mapping/plans                      the versions, newest first,
                                                 each with no snapshot
    GET  /crm/mapping/plans/{version}            one version and its snapshot
    POST /crm/mapping/plans                      a draft from a list of changes
    POST /crm/mapping/plans/{version}/approve    approve a draft
    POST /crm/mapping/plans/{version}/restore    approve a copy of an old version
    POST /crm/mapping/preview                    a draft on sampled records, in
                                                 memory (CRM-Q19)

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

**An empty column opens (CRM-Q18-3).** Each ``field`` item of a snapshot
carries ``opened_empty``, and so does its row. The check passes the empty keys
of the entity (``field_rules._empty``) and sets the mark when only they open
the column. Once the rule fills its column, the fill is no longer 0. So
``_checked`` skips the empty test for an item that carries the mark: a
restore, a draft approve or a park of an opened rule gives no 422. A put with
a new ``target_column`` clears the mark (:func:`_put_rule`), so the empty test
runs again for the new column.

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
CRM-Q18-1b: the statement sorts with ``COLLATE "C"``, and a writer of more
than one rule row writes in the order of ``core.field_rule_key``. Those two
orders are the same. The proposer of ``field_rules.py`` locks first too.

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
from dataclasses import dataclass, replace
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Literal

from acb_auth import UserContext, get_current_user
from acb_common import get_logger
from fastapi import Depends, HTTPException
from gateway.routes.crm.core import (
    ENTITIES,
    FIELD_RULE_LOCK_ORDER,
    FIELD_RULE_LOCK_SQL,
    _tenant_session,
    actor,
    attach_ext_urls,
    attach_next_activity,
    field_rule_key,
    from_jsonb,
    project_joined,
    router,
    row_to_dict,
)
from gateway.routes.crm.field_rules import (
    _GUARDS,
    _SLUG_BY_ENTITY,
    PROVIDER,
    _check_legal,
    _db_conflict,
    _empty,
    _entity,
    _field_type,
    _organization,
    _profile,
    _require_mirror,
    _taken,
)
from gateway.routes.crm.field_studio import attach_lookup_names
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

_log = get_logger("crm.mapping_plans")

#: The kind of a rule that maps a provider field to one of our columns. CRM-Q20
#: adds other kinds to the snapshot only, and this module keeps them as they are.
KIND_FIELD = "field"
#: The three kinds of CRM-Q20-1 (§18.9.6, "The three new kinds of rule"). A
#: ``role`` names the module that plays an empty core slot. A ``target`` and a
#: ``stage`` are legal only beside a ``role`` of the same slot and module
#: (rule 1). :func:`check_kinds` holds the three rules.
KIND_ROLE = "role"
KIND_TARGET = "target"
KIND_STAGE = "stage"
#: The actor of a version that records a change the sync made.
SYSTEM_ACTOR = "system"
#: The actor of a draft that the mapping pipeline writes (CRM-Q20-1).
SUGGEST_ACTOR = "suggestion"
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
    "id, entity, field_api_name, field_type, profile_version, target_column, status, "
    "opened_empty"
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
    "profile_version = :version, opened_empty = :opened, "
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
    "profile_version, target_column, status, proposed_by, decided_by, decided_at, "
    "opened_empty) "
    "VALUES (:src, :entity, :field, :type, :version, :column, 'approved', 'admin', "
    ":by, now(), :opened)"
)


# ── The snapshot ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PlanRule:
    """One rule of kind ``field`` in a snapshot: the columns of an approved
    ``crm_field_rules`` row that define it.

    ``opened_empty`` (CRM-Q18-3) is True when an empty column opened the rule.
    It is not part of :attr:`identity`.
    """

    entity: str
    field_api_name: str
    field_type: str
    target_column: str
    profile_version: int
    opened_empty: bool = False

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
            "opened_empty": self.opened_empty,
        }


def _rule_of(item: Mapping[str, Any]) -> PlanRule | None:
    values = [item.get(k) for k in ("entity", "field_api_name", "field_type", "target_column")]
    if not all(isinstance(v, str) and v for v in values):
        return None
    version = item.get("profile_version")
    return PlanRule(
        *values,  # type: ignore[arg-type]
        profile_version=version if isinstance(version, int) else 0,
        # A snapshot from before CRM-Q18-3 has no mark: False.
        opened_empty=item.get("opened_empty") is True,
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
        # NULL on a row from before CRM-Q18-3: False.
        opened_empty=row.opened_empty is True,
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


# ── The kinds of CRM-Q20-1 ──────────────────────────────────────────────────


def _kind_error(code: str, message: str, item: Mapping[str, Any]) -> HTTPException:
    """A 422 that names the kind, the slot and the module of an item."""
    return HTTPException(status_code=422, detail={
        "code": code, "message": message, "kind": item.get("kind"),
        "slot": item.get("slot"), "module": item.get("module"),
    })


def kind_key(item: Mapping[str, Any]) -> tuple[Any, ...] | None:
    """The identity of an item of a CRM-Q20-1 kind, or ``None`` for another
    kind. One ``role`` for each slot, one ``target`` for each slot, module
    and target, and one ``stage`` for each slot and module."""
    kind = item.get("kind")
    if kind == KIND_ROLE:
        return (KIND_ROLE, item.get("slot"))
    if kind == KIND_TARGET:
        return (KIND_TARGET, item.get("slot"), item.get("module"), item.get("target"))
    if kind == KIND_STAGE:
        return (KIND_STAGE, item.get("slot"), item.get("module"))
    return None


def _roles(others: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    """The ``role`` items of a snapshot: slot to module. 422 for a role that
    names no known slot or no module."""
    # Imported here: ``gateway.crm_sync`` imports ``routes.crm.core``.
    from gateway.crm_sync import executor

    roles: dict[str, str] = {}
    for item in others:
        if item.get("kind") != KIND_ROLE:
            continue
        slot, module = item.get("slot"), item.get("module")
        if slot not in executor.SLOTS or not isinstance(module, str) or not module:
            raise _kind_error("bad_role", "A role names a core slot and a module.", item)
        roles[slot] = module
    return roles


def check_kinds(others: Sequence[Mapping[str, Any]]) -> None:
    """The three rules of the kinds of CRM-Q20-1, on the items of one
    snapshot. A 422 names the item.

    1. A ``target`` or a ``stage`` item is legal only beside a ``role`` item
       of the same slot and module, in the same snapshot (``role_missing``).
    2. A ``target`` names a legal target of its slot and a legal transform
       (``bad_target``, ``executor.target_problem``).
    3. Each value of a ``stage`` maps to ``won``, ``lost`` or ``open``
       (``bad_stage``, ``executor.stage_problem``).

    Two items with one identity (:func:`kind_key`) are a 422 ``duplicate``.
    An item of another kind is not checked here.
    """
    from gateway.crm_sync import executor

    roles = _roles(others)
    seen: set[tuple[Any, ...]] = set()
    for item in others:
        key = kind_key(item)
        if key is None:
            continue
        if key in seen:
            raise _kind_error("duplicate", "Two items of the plan say the same thing.", item)
        seen.add(key)
        kind = item.get("kind")
        if kind == KIND_ROLE:
            continue
        if roles.get(item.get("slot")) != item.get("module"):
            raise _kind_error(
                "role_missing",
                "This item needs the module to play its slot in the same plan.", item)
        if kind == KIND_TARGET:
            problem = executor.target_problem(
                item.get("slot"), item.get("target"), item.get("fields"), item.get("transform"))
            if problem is not None:
                raise _kind_error("bad_target", f"The target is not legal ({problem}).", item)
        else:
            problem = executor.stage_problem(item.get("field_api_name"), item.get("values"))
            if problem is not None:
                raise _kind_error("bad_stage", f"The stage map is not legal ({problem}).", item)


def merge_kinds(
    others: Sequence[Mapping[str, Any]], items: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """*others* with *items* put in. An item replaces the item of its
    identity (:func:`kind_key`). A ``role`` that moves a slot to another
    module drops the ``target`` and ``stage`` items of the old module, so
    the result keeps rule 1. An item of another kind stays as it is."""
    merged = [dict(o) for o in others]
    for item in items:
        key = kind_key(item)
        if key is None:
            continue
        if item.get("kind") == KIND_ROLE:
            old = next((o for o in merged if kind_key(o) == key), None)
            if old is not None and old.get("module") != item.get("module"):
                merged = [o for o in merged if not (
                    o.get("kind") in (KIND_TARGET, KIND_STAGE)
                    and o.get("slot") == item.get("slot")
                    and o.get("module") == old.get("module"))]
        merged = [o for o in merged if kind_key(o) != key]
        merged.append(dict(item))
    return merged


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


async def _checked(
    db: Any, rule: PlanRule, profile: Any, version: int | None, empty: frozenset[str],
) -> PlanRule:
    """The checks of ``POST /crm/field-rules``, and the rule with the type and
    the profile version of today. A 422 names the field.

    *empty* holds the empty keys of the entity of the rule (CRM-Q18-3). An
    item that carries ``opened_empty`` skips the empty test and keeps its
    mark. Else the mark is True when only *empty* opens the column.
    """
    try:
        kind = await _field_type(db, rule.entity, rule.field_api_name, profile)
        opened = _check_legal(
            rule.entity, kind, rule.target_column, profile, empty,
            opened=rule.opened_empty,
        )
    except HTTPException as exc:
        if isinstance(exc.detail, dict):
            exc.detail = {
                **exc.detail, "entity": rule.entity, "field_api_name": rule.field_api_name,
            }
        raise
    return PlanRule(
        rule.entity, rule.field_api_name, kind, rule.target_column, int(version or 0),
        opened_empty=opened,
    )


def _put_rule(old: PlanRule | None, new: PlanRule) -> PlanRule:
    """The item that a ``put`` of *new* writes over *old*, the item of the same
    field (CRM-Q18-3).

    On the same column the item keeps the mark of *old*, because the rule
    may have filled the column since an empty column opened it. A new
    ``target_column`` clears the mark, so the empty test runs again for the
    new column. With no *old*, *new* stays as it is.
    """
    if old is None:
        return new
    if old.target_column == new.target_column:
        return replace(new, opened_empty=new.opened_empty or old.opened_empty)
    return replace(new, opened_empty=False)


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
    # 2. A rule enters on the row of its field, or on a new row. The writes go
    #    in the one key order of the lock (CRM-Q18-1b), so two writers that
    #    insert new rows never wait on each other in a cycle.
    for rule in sorted(fields, key=lambda r: field_rule_key(r.entity, r.field_api_name)):
        row = rows.get(rule.key)
        params = {
            "column": rule.target_column, "type": rule.field_type,
            "version": rule.profile_version, "by": by, "opened": rule.opened_empty,
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

    CRM-Q20-1: the items of the kinds ``role``, ``target`` and ``stage`` pass
    :func:`check_kinds` first, so a draft approve, a restore and a per-rule
    change all keep the three rules of the kinds.
    """
    check_kinds(others)
    # The rule rows are locked first, so the profile and the checks read the
    # state that the writes below act on.
    rows = {
        (str(r.entity), str(r.field_api_name)): r
        for r in await lock_rules(db)
    }
    version, profile = await _profile(db, org)
    # CRM-Q18-3: the empty keys of each entity, read once and only when a rule
    # of it is checked.
    empties: dict[str, frozenset[str]] = {}
    final: list[PlanRule] = []
    for rule in fields:
        row = rows.get(rule.key)
        if row is not None and row.status == "approved" and row.target_column == rule.target_column:
            final.append(_rule_from_row(row))
            continue
        if rule.entity not in empties:
            empties[rule.entity] = await _empty(db, org, rule.entity)
        final.append(await _checked(db, rule, profile, version, empties[rule.entity]))
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
        current[put.key] = _put_rule(current.get(put.key), put)
    return await commit(
        db, org=org, fields=list(current.values()), others=others, by=by,
        based_on=int(approved.version),
    )


#: The open draft: the newest draft that is built on a version.
_OPEN_DRAFT_SQL = (
    f"SELECT {_PLAN_COLUMNS} FROM crm_mapping_plans WHERE state = 'draft' "
    "AND based_on = :base ORDER BY version DESC LIMIT 1"
)


def _canonical(items: Sequence[Mapping[str, Any]]) -> list[str]:
    return sorted(json.dumps(dict(i), sort_keys=True) for i in items)


def _rules_of(row: Any) -> Any:
    return row.rules if isinstance(row.rules, list) else json.loads(row.rules or "[]")


async def open_draft(db: Any, approved: Any) -> Any | None:
    """The open draft: the newest draft whose base is *approved*, the row of
    the version in use. ``lib/fieldMap.ts`` ``openDraft`` reads the same.

    Each new draft builds on it, so a pick by hand joins a suggestion of the
    pipeline, and a suggestion keeps the picks by hand (Batch M review P1,
    §18.9.6 "a pick by hand joins it"). Without it, each draft built on the
    version in use, and the newer draft dropped the items of the older one.
    """
    return (await db.execute(text(_OPEN_DRAFT_SQL), {
        "base": int(approved.version),
    })).first()


async def carry_draft(
    db: Any, draft: Any, approved: Any, *, drop_slot: str | None = None,
) -> Any | None:
    """CRM-Q20-2: move the open draft *draft* onto the new approved version
    *approved*, in the caller's transaction, under :func:`lock_plan`.

    A suggestion approve or an Undo approves the items of one slot only, so
    the open draft is then built on an older version, and the card drops it.
    This writes a new draft on *approved* with the field rules of *draft*
    and its items of the kinds, so each pick by hand and each other
    suggestion stays. *drop_slot* leaves out the items of a slot that the
    admin undid, so an Undo does not come back through the draft. Returns
    the new draft, or ``None`` when it would change nothing, or when its
    items no longer pass :func:`check_kinds`.
    """
    fields, others = split_rules(_rules_of(draft))
    live_fields, live_others = split_rules(_rules_of(approved))
    kinds = [o for o in others if kind_key(o) is not None
             and not (drop_slot is not None and o.get("slot") == drop_slot)]
    merged = merge_kinds(live_others, kinds)
    if (_identity(fields) == _identity(live_fields)
            and _canonical(merged) == _canonical(live_others)):
        return None
    try:
        check_kinds(merged)
    except HTTPException:
        return None
    return (await db.execute(text(_INSERT_DRAFT_SQL), {
        "v": await _next_version(db), "rules": _dump(fields, merged),
        "by": draft.created_by, "based_on": int(approved.version),
    })).first()


#: The approved version of the bound org. The org is also in the predicate,
#: so the read names the org of the caller twice: here and through RLS.
_APPROVED_OF_SQL = (
    "SELECT rules FROM crm_mapping_plans WHERE organization_id = CAST(:org AS uuid) "
    "AND state = 'approved'"
)


def roles_of(items: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    """The ``role`` items of *items*: slot to module. An item with no known
    slot or no module is left out."""
    # Imported here: ``gateway.crm_sync`` imports ``routes.crm.core``.
    from gateway.crm_sync import executor

    out: dict[str, str] = {}
    for item in items:
        slot, module = item.get("slot"), item.get("module")
        if (item.get("kind") == KIND_ROLE and slot in executor.SLOTS
                and isinstance(module, str) and module):
            out[str(slot)] = module
    return out


async def approved_overlays(db: Any, org: str) -> dict[str, str]:
    """CRM-Q20-2 (§18.9.6, the read path of option A): the module of each
    approved ``role``, by slot, in the caller's tenant session.

    It names only a module that the bound org mirrors now
    (``modules.mirrored_modules``), so a rail row never points at a list
    that answers 404. Empty in Native mode and with no approved role.
    """
    from gateway.routes.crm.modules import mirrored_modules

    row = (await db.execute(text(_APPROVED_OF_SQL), {"org": org})).first()
    if row is None:
        return {}
    _fields, others = split_rules(_rules_of(row))
    roles = roles_of(others)
    if not roles:
        return {}
    mirrored = await mirrored_modules(db)
    return {slot: module for slot, module in roles.items() if module in mirrored}


async def _not_dismissed(
    db: Any, org: str | None, items: Sequence[Mapping[str, Any]], digest: str | None,
) -> list[dict[str, Any]]:
    """*items* less the items of each slot whose role the admin dismissed or
    undid on the schema of today, read again under the plan lock. *digest*
    is the schema hash of the roles of the run. With none, the stored hash
    of the cursor row serves.

    ⚠️ Round 2 review P2: the run reads the dismissals when it starts
    (``map_pipeline.load``). A dismiss or an Undo that commits while the run
    asks would else come back through the cached items of the run.
    """
    # Imported here: ``gateway.crm_sync`` imports ``routes.crm.core``.
    from acb_common.db import current_tenant
    from gateway.crm_sync import map_pipeline, runs

    org = org or current_tenant()
    if not org:
        return [dict(i) for i in items]
    cursors = await runs.read_cursors(db, str(org), "zoho:map_")
    digest = digest or map_pipeline.slots_digest(
        getattr(cursors.get(map_pipeline.CURSOR_ROW), "state", None))
    dismissed = map_pipeline.dismissed_of(
        getattr(cursors.get(map_pipeline.DISMISSED_ROW), "state", None))
    gone = {
        (slot, module) for slot, module in roles_of(items).items()
        if map_pipeline.is_dismissed(dismissed, slot, module, digest)
    }
    return [dict(i) for i in items
            if not (kind_key(i) is not None and (i.get("slot"), i.get("module")) in gone)]


async def write_draft(
    db: Any, *, items: Sequence[Mapping[str, Any]], by: str = SUGGEST_ACTOR,
    org: str | None = None, digest: str | None = None,
) -> Any | None:
    """A draft of the mapping pipeline (CRM-Q20-1 item 3), in the caller's
    tenant session, under :func:`lock_plan`.

    The draft is the open draft (:func:`open_draft`), or the approved
    version when no draft is open, with *items* put in (:func:`merge_kinds`).
    So it keeps each pick by hand of the open draft. It passes
    :func:`check_kinds`, so an admin can approve it. It writes no row of
    ``crm_field_rules``, exactly as ``POST /crm/mapping/plans`` does not.
    Returns the new draft row, or ``None`` when the draft would change
    nothing: the open draft, or with none the approved version, holds the
    items already.

    Under the lock it drops the items of a slot that the admin dismissed or
    undid (:func:`_not_dismissed`). A draft on an open draft of the admin
    keeps the admin as its author, because it holds the admin's picks
    (round 2 review): the history then never says "Suggested by Metorite"
    for them.
    """
    # Version 1 of an org with no plan is the work of the system, not of the
    # pipeline, so the lock names the system.
    approved = await lock_plan(db, SYSTEM_ACTOR)
    items = await _not_dismissed(db, org, items, digest)
    draft = await open_draft(db, approved)
    base = draft or approved
    fields, others = split_rules(_rules_of(base))
    merged = merge_kinds(others, items)
    check_kinds(merged)
    if _canonical(merged) == _canonical(others):
        return None
    author = by
    if draft is not None and draft.created_by not in (SUGGEST_ACTOR, SYSTEM_ACTOR):
        author = draft.created_by
    row = (await db.execute(text(_INSERT_DRAFT_SQL), {
        "v": await _next_version(db), "rules": _dump(fields, merged),
        "by": author, "based_on": int(approved.version),
    })).first()
    _log.info("crm.mapping_plan_suggested version=%s items=%s", row.version, len(merged))
    return row


# ── The wire ────────────────────────────────────────────────────────────────


class MappingPlanModel(BaseModel):
    version: int
    state: Literal["draft", "approved", "superseded"]
    #: Each item has a ``kind``. A ``field`` item holds ``entity``,
    #: ``field_api_name``, ``field_type``, ``target_column``,
    #: ``profile_version`` and ``opened_empty`` (CRM-Q18-3).
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
    """A draft: the open draft with the changes, or the approved version
    with them when no draft is open (:func:`open_draft`). So a pick by hand
    keeps the items of a suggestion. It writes no row of
    ``crm_field_rules``. Each ``put`` has the checks of a new field rule."""
    org = _organization(user)
    by = actor(user).lower()
    async with _tenant_session() as db:
        await _require_mirror(db, org)
        approved = await lock_plan(db, by)
        version, profile = await _profile(db, org)
        base = await open_draft(db, approved) or approved
        fields, others = split_rules(_rules_of(base))
        current = {r.key: r for r in fields}
        empties: dict[str, frozenset[str]] = {}
        for change in payload.changes:
            key = (_entity(change.entity), change.field_api_name.strip())
            if change.op == "remove":
                if key not in current:
                    raise HTTPException(status_code=422, detail={
                        "code": "not_in_plan",
                        "message": "That field has no rule in the draft.",
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
            if key[0] not in empties:
                empties[key[0]] = await _empty(db, org, key[0])
            current[key] = await _checked(
                db, _put_rule(current.get(key), PlanRule(key[0], key[1], "", column, 0)),
                profile, version, empties[key[0]],
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


# ── The preview (CRM-Q19) ───────────────────────────────────────────────────

#: The records of each entity that the preview reads: the lowest ids.
PREVIEW_ROWS = 200
#: The order of the entities in a preview: the order of the rule targets.
_PREVIEW_ORDER: tuple[str, ...] = ("company", "contact", "lead", "deal")

#: CRM-Q20-2: the records of one generic module, the lowest ids, and their
#: count. The module name is a bound parameter that passed the mirrored set.
_OVERLAY_ROWS_SQL = (
    "SELECT id, fields FROM crm_module_records WHERE module = :m "
    "AND ext_source = :src ORDER BY id LIMIT :limit"
)
_OVERLAY_COUNT_SQL = (
    "SELECT count(*) FROM crm_module_records WHERE module = :m AND ext_source = :src"
)

#: The mirrored rows of one core table, the lowest ids first. The table is a
#: constant of ``core.ENTITIES``, never caller text. FORCE RLS keeps the read
#: in the bound org.
_PREVIEW_SQL = (
    "SELECT * FROM {table} WHERE ext_source = :src ORDER BY id LIMIT :limit"
)

#: The preview writes nothing. The database refuses a write in this
#: transaction, so a later edit that adds one fails at once.
_READ_ONLY_SQL = "SET TRANSACTION READ ONLY"


class MappingPreviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: The version of a draft.
    version: int
    #: CRM-Q20-2: the core slot of a ``role`` item of the draft. With it, the
    #: preview shows the records of the module that would play the slot, and
    #: no core entity.
    slot: Literal["company", "contact", "lead", "deal"] | None = None


class PreviewEntityModel(BaseModel):
    #: The entity word of the rules (``company``, ``contact``, ``lead``,
    #: ``deal``).
    entity: str
    #: The slug of its list route (``organizations``, ``contacts``...).
    slug: str
    #: Up to :data:`PREVIEW_ROWS` records, the lowest ids, in the shape of the
    #: list route of the entity, with the draft applied.
    rows: list[dict]
    #: For each target column of a draft rule of the entity, the count of
    #: filled values that did not map. A count only, never a value.
    unmapped: dict[str, int]


class OverlayRowModel(BaseModel):
    """One record of the module, as the slot would read it."""

    id: str
    #: The value of each target column, and ``stage`` (``won``, ``lost``,
    #: ``open`` or ``null``), from the items of the draft.
    values: dict[str, Any]


class OverlayPreviewModel(BaseModel):
    """CRM-Q20-2: the records of the module that a ``role`` item of a draft
    names, through the ``target`` and ``stage`` items of the same slot and
    module. It reads only."""

    slot: str
    module: str
    #: The target columns of the items, in the order of the slot, then
    #: ``stage`` when the draft has a stage item.
    columns: list[str]
    #: Up to :data:`PREVIEW_ROWS` records of the module, the lowest ids.
    rows: list[OverlayRowModel]
    #: Every mirrored record of the module.
    total: int
    #: For each target column, the count of filled values in the rows that
    #: did not map. A count only, never a value.
    unmapped: dict[str, int]
    #: The rows whose stage value has no entry in the stage item.
    unsorted: int


class MappingPreviewModel(BaseModel):
    version: int
    entities: list[PreviewEntityModel]
    #: CRM-Q20-2: with ``slot`` in the request, the records of its module.
    #: Left out of the body otherwise, so the body of CRM-Q19 stays as it is.
    overlay: OverlayPreviewModel | None = Field(
        default=None, exclude_if=lambda value: value is None,
    )


def _rules_by_entity(fields: Iterable[PlanRule]) -> dict[str, dict[str, str]]:
    """``{entity: {target_column: field_api_name}}``, the shape of the
    executor."""
    out: dict[str, dict[str, str]] = {}
    for rule in fields:
        out.setdefault(rule.entity, {})[rule.target_column] = rule.field_api_name
    return out


async def _preview_entity(
    db: Any, word: str, live: Mapping[str, str], draft: Mapping[str, str],
) -> PreviewEntityModel:
    """Read the sample of one entity and apply the draft to it, in memory."""
    # Imported here: ``gateway.crm_sync`` imports ``routes.crm.core``.
    from gateway.crm_sync import executor

    slug = _SLUG_BY_ENTITY[word]
    entity = ENTITIES[slug]
    inner = _PREVIEW_SQL.format(table=entity.table)
    rows = (await db.execute(
        text(project_joined(entity, inner, "base.id")),
        {"src": PROVIDER, "limit": PREVIEW_ROWS},
    )).fetchall()
    unmapped = dict.fromkeys(draft, 0)
    wired: list[dict[str, Any]] = []
    for row in rows:
        values = dict(row._mapping)
        custom = from_jsonb(values.get("custom")) or {}
        for column in executor.preview_record(word, values, custom, live, draft):
            unmapped[column] += 1
        wired.append(row_to_dict(SimpleNamespace(**values), entity.model))
    # The same steps as the list route (``records._list``). Each one reads.
    await attach_ext_urls(db, entity.table, wired)
    await attach_next_activity(db, entity, wired)
    await attach_lookup_names(db, entity.slug, wired)
    return PreviewEntityModel(entity=word, slug=slug, rows=wired, unmapped=unmapped)


def _no_overlay(code: str, message: str) -> HTTPException:
    return HTTPException(status_code=404, detail={"code": code, "message": message})


async def _preview_overlay(
    db: Any, others: Sequence[Mapping[str, Any]], slot: str,
) -> OverlayPreviewModel:
    """CRM-Q20-2: the records of the module that the ``role`` of *slot*
    names, through the ``target`` and ``stage`` items of the same slot and
    module, in memory. It reads only."""
    from gateway.crm_sync import executor
    from gateway.routes.crm.modules import mirrored_modules

    module = roles_of(others).get(slot)
    if module is None:
        raise _no_overlay("no_role", "This draft names no module for that slot.")
    if module not in await mirrored_modules(db):
        raise _no_overlay("unknown_module", "This module is not mirrored.")
    order = executor.slot_targets(slot)
    targets = sorted(
        (o for o in others if o.get("kind") == KIND_TARGET and o.get("slot") == slot
         and o.get("module") == module and o.get("target") in order),
        key=lambda o: order.index(o["target"]),
    )
    stage = next((o for o in others if o.get("kind") == KIND_STAGE
                  and o.get("slot") == slot and o.get("module") == module), None)
    columns = [str(t["target"]) for t in targets] + (["stage"] if stage else [])
    params = {"m": module, "src": PROVIDER}
    rows = (await db.execute(text(_OVERLAY_ROWS_SQL),
                             {**params, "limit": PREVIEW_ROWS})).fetchall()
    total = int((await db.execute(text(_OVERLAY_COUNT_SQL), params)).scalar() or 0)
    unmapped = {str(t["target"]): 0 for t in targets}
    unsorted = 0
    wired: list[OverlayRowModel] = []
    for row in rows:
        fields = from_jsonb(row.fields) or {}
        values: dict[str, Any] = {}
        for item in targets:
            try:
                outcome = executor.run_target(slot, item, fields)
            except ValueError:  # an item that is not legal maps nothing
                values[item["target"]] = None
                continue
            values[item["target"]] = outcome.value
            unmapped[item["target"]] += int(outcome.unmapped)
        if stage is not None:
            try:
                outcome = executor.run_stage(stage, fields)
            except ValueError:
                values["stage"] = None
            else:
                values["stage"] = outcome.value
                unsorted += int(outcome.unmapped)
        wired.append(OverlayRowModel(id=str(row.id), values=values))
    return OverlayPreviewModel(
        slot=slot, module=module, columns=columns, rows=wired, total=total,
        unmapped=unmapped, unsorted=unsorted,
    )


@router.post(
    "/mapping/preview", response_model=MappingPreviewModel, dependencies=_GUARDS,
)
async def preview_mapping_plan(
    payload: MappingPreviewIn, user: UserContext = Depends(get_current_user),
) -> MappingPreviewModel:
    """What a draft would put into real records, before an admin approves it.

    It reads up to :data:`PREVIEW_ROWS` mirrored records of each entity that
    the draft or the live rules name, the lowest ids, in the tenant session.
    It applies the draft in memory through the executor, the path that the
    sync runs, and returns the rows in the shape of the list route. It counts
    each value that a rule could not map, by entity and target column.

    It writes nothing. The transaction is READ ONLY, and it takes no plan
    lock, so it builds no version and follows no live set. A version that is
    not a draft is a 409 ``not_draft``. The org comes from the server.
    """
    org = _organization(user)
    async with _tenant_session() as db:
        await db.execute(text(_READ_ONLY_SQL))
        await _require_mirror(db, org)
        draft = await _version(db, payload.version)
        if draft.state != "draft":
            raise HTTPException(status_code=409, detail={
                "code": "not_draft", "message": "Only a draft has a preview.",
            })
        fields, others = split_rules(_rules_of(draft))
        if payload.slot is not None:
            # CRM-Q20-2: the records of the module of one role, and no core
            # entity.
            overlay = await _preview_overlay(db, others, payload.slot)
            return MappingPreviewModel(version=int(draft.version), entities=[],
                                       overlay=overlay)
        drafted = _rules_by_entity(fields)
        live = _rules_by_entity(await _live_approved(db))
        entities = [
            await _preview_entity(db, word, live.get(word, {}), drafted.get(word, {}))
            for word in _PREVIEW_ORDER if word in drafted or word in live
        ]
    return MappingPreviewModel(version=int(draft.version), entities=entities)


__all__ = [
    "KIND_FIELD",
    "KIND_ROLE",
    "KIND_STAGE",
    "KIND_TARGET",
    "PREVIEW_ROWS",
    "SUGGEST_ACTOR",
    "PlanRule",
    "approved_overlays",
    "carry_draft",
    "change_rule",
    "check_kinds",
    "commit",
    "kind_key",
    "lock_plan",
    "merge_kinds",
    "open_draft",
    "preview_mapping_plan",
    "roles_of",
    "split_rules",
    "write_draft",
]
