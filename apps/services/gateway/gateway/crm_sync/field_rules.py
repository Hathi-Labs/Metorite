"""The field rules in the sync (WS-53 CRM-Q7a, migration 253).

Spec: ``project-docs/specs/crm_platform.md`` §18.7, "What an approved rule
does" and "When the profile changes".

An admin approves a rule in CRM settings → Field map. The route writes only
``crm_field_rules``. The sync does the rest, with no model call and no
provider call:

* **The backfill.** A rule that the sync has not applied yet copies
  ``custom -> field_api_name`` into its column, only on the mirrored rows
  where the column is NULL, in batches of :data:`BATCH`. Each value goes
  through the coercion of the mapping module. A value that does not coerce
  stays NULL.
* **The undo.** A rule that the sync applied and that is no longer approved
  (rejected or stale) is undone. A free column goes back to NULL on the
  mirrored rows. A fixed column asks for one full read of its entity, which
  writes the fixed map's value again (the engine's ``_changed_pass``).

Every write goes through ``core.apply_field_rule_from_source``. The fence
``tests/unit/test_crm_upsert_from_source_fence.py`` lets only this package
name it. The functions here take the ``db`` of one ``tenant_session(org)``,
and the caller owns the transaction.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import ModuleType
from typing import Any

from acb_common import get_logger
from sqlalchemy import text

from gateway.routes.crm.core import (
    FIELD_RULE_BATCH,
    FIELD_RULE_LOCK_SQL,
    apply_field_rule_from_source,
)

_log = get_logger("crm.sync.field_rules")

#: The rows of one backfill or clear batch.
BATCH = FIELD_RULE_BATCH


@dataclass(frozen=True)
class Rule:
    """One approved rule, as the sync applies it.

    ``backfilling`` is True while the backfill of the rule runs. The rule
    enters the applied state with it in the transaction of the FIRST batch,
    so a stop between two batches still leaves a rule that a reject undoes.
    The last batch clears it. It is not part of :attr:`key`.
    """

    id: str
    entity: str
    field_api_name: str
    target_column: str
    backfilling: bool = False

    @property
    def key(self) -> tuple[str, str, str, str]:
        return (self.id, self.entity, self.field_api_name, self.target_column)

    def as_state(self) -> dict[str, Any]:
        state: dict[str, Any] = {
            "id": self.id, "entity": self.entity,
            "field": self.field_api_name, "column": self.target_column,
        }
        if self.backfilling:
            state["backfilling"] = True
        return state


_APPROVED_SQL = (
    "SELECT id, entity, field_api_name, target_column FROM crm_field_rules "
    "WHERE ext_source = :src AND status = 'approved' AND target_column IS NOT NULL "
    "ORDER BY entity, target_column, id"
)

_STALE_SQL = (
    "UPDATE crm_field_rules SET status = 'stale', updated_at = now() "
    "WHERE id = CAST(:id AS uuid) AND status = 'approved'"
)


async def approved_rules(db: Any, ext_source: str) -> list[Rule]:
    """The approved rules of the bound organization."""
    rows = (await db.execute(text(_APPROVED_SQL), {"src": ext_source})).fetchall()
    return [
        Rule(str(r.id), str(r.entity), str(r.field_api_name), str(r.target_column))
        for r in rows
    ]


def by_entity(rules: Iterable[Rule]) -> dict[str, dict[str, str]]:
    """``{entity: {target_column: field_api_name}}``, the shape ``map_record``
    takes."""
    out: dict[str, dict[str, str]] = {}
    for rule in rules:
        out.setdefault(rule.entity, {})[rule.target_column] = rule.field_api_name
    return out


def applied_of(state: Mapping[str, Any] | None) -> list[Rule]:
    """The rules the sync applied, from the state of the rules cursor row."""
    out: list[Rule] = []
    for item in (state or {}).get("applied") or []:
        if not isinstance(item, Mapping):
            continue
        values = [item.get(k) for k in ("id", "entity", "field", "column")]
        if all(isinstance(v, str) and v for v in values):
            out.append(Rule(*values, backfilling=item.get("backfilling") is True))  # type: ignore[arg-type]
    return out


def state_of(applied: Iterable[Rule], reread: Iterable[str]) -> dict[str, Any]:
    """The state of the rules cursor row: the applied rules and the entities
    that wait for a full read."""
    return {
        "applied": [r.as_state() for r in sorted(applied, key=lambda r: r.key)],
        "reread": sorted(set(reread)),
    }


_BACKFILL_SQL = (
    "SELECT id, custom -> :field AS value FROM {table} "
    "WHERE ext_source = :src AND {column} IS NULL AND custom ? :field "
    "AND (CAST(:after AS uuid) IS NULL OR id > CAST(:after AS uuid)) "
    "ORDER BY id LIMIT {limit}"
)


async def backfill_batch(
    db: Any, mapping: ModuleType, rule: Rule, ext_source: str, after: str | None,
    *, organization_id: str,
) -> tuple[str | None, int, int]:
    """Copy one batch of ``custom -> field`` into the rule's column.

    The rows are the mirrored rows where the column is NULL and ``custom``
    holds the field, after the row id *after*. Returns ``(last id, rows read,
    rows written)``. A batch shorter than :data:`BATCH` is the last one. A row
    whose value does not coerce is read and skipped, and the keyset moves past
    it, so it is never read again in this backfill.
    """
    target = mapping.rule_target(rule.entity, rule.target_column)
    if target is None:
        return None, 0, 0
    table = mapping.ENTITY_TABLES[rule.entity]
    # The table and the column are a legal target of the mapping module (the
    # door checks them again). The field name is a bound parameter.
    rows = (await db.execute(
        text(_BACKFILL_SQL.format(table=table, column=target.column, limit=BATCH)),
        {"field": rule.field_api_name, "src": ext_source, "after": after},
    )).fetchall()
    if not rows:
        return None, 0, 0
    pairs = [
        (str(r.id), mapping.coerce_target_value(target, r.value))
        for r in rows
    ]
    written = await apply_field_rule_from_source(
        db, table, target.column, ext_source, pairs, organization_id=organization_id,
    )
    return str(rows[-1].id), len(rows), written


async def clear_batch(
    db: Any, mapping: ModuleType, rule: Rule, ext_source: str,
    *, organization_id: str,
) -> int:
    """Set a free column to NULL on one batch of mirrored rows. Returns the
    rows written. The caller repeats it until it gives 0."""
    target = mapping.rule_target(rule.entity, rule.target_column)
    if target is None or target.standard_field is not None:
        return 0
    return await apply_field_rule_from_source(
        db, mapping.ENTITY_TABLES[rule.entity], target.column, ext_source, None,
        organization_id=organization_id,
    )


async def mark_stale(
    db: Any,
    mapping: ModuleType,
    ext_source: str,
    profile: Mapping[str, Any] | None,
) -> list[str]:
    """Mark ``stale`` each approved rule that the new profile breaks.

    The profile holds the type of each field that goes to ``custom``
    (``custom_field_types``) and the fields on the layouts (``layout_fields``).
    An entity that the profile does not know keeps the status of its rules.
    The three cases are in ``mapping.rule_stale_reason``. A rule with
    ``opened_empty`` (CRM-Q18-3) never goes stale for its standard field on
    a layout. Returns the ids that
    went stale. The log gets the id and the reason, never a field value.

    It first locks every rule row of the provider through
    ``core.FIELD_RULE_LOCK_SQL``, in the one order that the plan approve
    uses too (CRM-Q18-1). So the sync and an approve never wait on each other
    in a cycle.
    """
    rows = (await db.execute(text(FIELD_RULE_LOCK_SQL), {"src": ext_source})).fetchall()
    approved = [
        (
            Rule(str(r.id), str(r.entity), str(r.field_api_name), str(r.target_column)),
            r.opened_empty is True,
        )
        for r in rows
        if r.status == "approved" and r.target_column is not None
    ]
    stale: list[str] = []
    for rule, opened in approved:
        # CRM-Q18-3: an empty column opened the rule, so its standard field on
        # a layout does not make it stale. The other two reasons still apply.
        reason = mapping.rule_stale_reason(
            rule.entity, rule.field_api_name, rule.target_column,
            mapping.entity_field_types(profile, rule.entity),
            mapping.entity_layout_fields(profile, rule.entity),
            opened=opened,
        )
        if reason is None:
            continue
        await db.execute(text(_STALE_SQL), {"id": rule.id})
        stale.append(rule.id)
        _log.info("crm.sync.field_rule_stale rule=%s reason=%s", rule.id, reason)
    return stale


__all__ = [
    "BATCH",
    "Rule",
    "applied_of",
    "approved_rules",
    "backfill_batch",
    "by_entity",
    "clear_batch",
    "mark_stale",
    "state_of",
]
