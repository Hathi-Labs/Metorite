"""CRM · the fill rank, the read side (WS-53 CRM-Q17a-1).

Spec: ``project-docs/specs/crm_platform.md`` §18.9.3 and the CRM-Q17a-1
done-when list of §18.9.6.

The sync writes ``crm_field_fill`` (``crm_sync/fill.py``, migration 274). This
module holds the vocabulary of its keys, the rule of staleness, and every read
of it. It registers no route. ``fields.py``, ``modules.py``, ``export.py`` and
``settings.py`` call it.

**The keys.** One row for each key of an entity:

* a core entity (``lead``, ``contact``, ``company``, ``deal``) has its
  STANDARD keys (:func:`standard_keys`): the real column behind each list
  column of ``export.COLUMNS``, then each key of ``core.SYSTEM_FIELDS``. A
  list column that is a join names its real column through
  :data:`LIST_ALIASES` (``status_name`` is ``status_id``, a deal's
  ``organization_name`` is ``company_id``). Each custom field is
  ``custom.<key>``, the prefix of the custom list filters;
* a generic module (``module:<ApiName>``) has the API name of each field.

**Filled.** A value counts as filled unless it is NULL, JSON ``null``, an
empty or blank string, ``[]``, ``{}`` or ``false``. ``false`` counts as empty:
Zoho sends ``false`` for an unticked checkbox, and an unticked box is the
default, not an answer. ``0`` counts as filled.

**Distinct.** ``distinct`` counts the different filled values in a sample of
at most ``fill.DISTINCT_SAMPLE`` (2,000) records, the ones with the lowest
ids. For an entity of 2,000 records or fewer it is exact. Above that it is a
lower bound, and it is never more than 2,000. A count over every record cost
too much on a large module of unique values.

**The wire.** ``fill`` on a field definition is the share ``filled / total``,
from 0 to 1, with four decimals. It is ``None`` when the entity holds no
record, or when the key has no row yet.

**Stale numbers** (:func:`stale_entities`). The numbers of an entity are
stale when it has no stamp in the cursor row ``zoho:fill_rank``, when it is
``pending`` there, when a field rule of it changed since the last fill step,
or when a sync run that STARTED after its stamp counted an ``upserted``, a
``deleted`` or a ``relinked`` row of it. A run that found no change moves the
``checked`` mark of the entity to its own start, and the scan starts there. ``progress`` writes those counts in the transaction of
each page, so a run that raised after a page still counts, and so does a run
of a time when the flag was off.

**Still empty** (CRM-Q17a-1b). While the numbers of a core entity are stale,
``empty_columns`` serves the keys of the last "still empty" check of the sync
(``crm_sync/fill.py``), the key :data:`STILL_EMPTY` of the fill row. It serves
them only when four things are true (:func:`checked_keys` and
:func:`changed_since`):

* the entity has a stamp, and none of its field rules changed;
* the check has a time;
* no run that started after that time changed or re-linked a row of it;
* no field rule of the entity has ``backfilling`` true.

Else ``empty_columns`` is ``[]``: no column is known to be empty. A served key
must also be empty in the stored numbers, so a key that they show as filled is
never served.

**Ship dark.** Each read first asks ``flags.fill_rank_allowed``. An org
outside ``CRM_FILL_RANK_ORGS`` gets ``None`` from each function here, and the
routes serve ``fill``, ``distinct`` and ``empty_columns`` as ``null`` and
leave ``labels`` out. That is the shape of the CRM before this slice.

**Tenancy (R5).** The organization comes from the server: the caller passes
``UserContext.organization_id`` or the bound tenant, never request input.
Each read names the organization and runs in the caller's session, so FORCE
row level security binds it too.
"""

from __future__ import annotations

import functools
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from gateway.routes.crm.core import (
    CUSTOM_PREFIX,
    ENTITIES,
    FIELD_ENTITY_BY_SLUG,
    SYSTEM_FIELDS,
    from_jsonb,
    is_mirror_mode,
)
from gateway.routes.crm.flags import fill_rank_allowed
from pydantic import BaseModel
from sqlalchemy import text

#: The provider whose profile holds the labels. One provider today.
PROVIDER = "zoho"

#: The cursor row of the fill step: ``at`` (the stamp of each entity),
#: ``pending`` (the entities that wait) and ``rules`` (the marks of the field
#: rules at the last fill step).
FILL_ROW = f"{PROVIDER}:fill_rank"
#: The cursor row of the field rules (``engine.FIELD_RULES_ROW``).
RULES_ROW = f"{PROVIDER}:field_rules"
#: The key of the "still empty" checks in the fill row (CRM-Q17a-1b):
#: ``{entity: {"keys": [...], "at": <database time of the statement>}}``.
STILL_EMPTY = "still_empty"
_MODULE_PREFIX = "module:"

#: A list column that is a join → the real column it shows, per entity slug.
#: Every other list column is a real column of its own table.
LIST_ALIASES: dict[str, dict[str, str]] = {
    "leads": {"status_name": "status_id"},
    "deals": {"status_name": "status_id", "organization_name": "company_id"},
}

#: The name column of each entity. A list always shows it, so it is never in
#: ``empty_columns``. Contacts draw ``first_name last_name`` under one header.
NAME_KEYS: frozenset[str] = frozenset({"name", "lead_name", "first_name"})

#: The keys that never show in ``empty_columns``: the name column and the
#: time of the last activity, which the list shows in each case.
#: ``owner_email`` is NULL on each record whose Zoho owner is not a member.
#: The Owner column then shows ``custom._owner_name`` with "Zoho only"
#: (CRM-Q17a-2 item 4), so the column is not empty (Batch M review P1).
NEVER_EMPTY: frozenset[str] = NAME_KEYS | {"last_activity_at", "owner_email"}

#: The entity word → the API slug, which is also its key in the run counts.
_SLUG_BY_ENTITY: dict[str, str] = {v: k for k, v in FIELD_ENTITY_BY_SLUG.items()}

_FILL_SQL = (
    "SELECT key, filled, total, distinct_count FROM crm_field_fill "
    "WHERE organization_id = CAST(:org AS uuid) AND entity = :entity"
)

_CURSORS_SQL = (
    "SELECT module, cursor_state FROM crm_sync_cursors "
    "WHERE organization_id = CAST(:org AS uuid) AND module = ANY(CAST(:rows AS text[]))"
)

#: The start of the latest run after *since* that counted a change of each
#: count key. The index ``crm_sync_runs (organization_id, started_at)``
#: serves it.
_CHANGED_SQL = (
    "SELECT k.key, max(r.started_at) AS changed_at "
    "FROM crm_sync_runs r, jsonb_each(r.counts) k "
    "WHERE r.organization_id = CAST(:org AS uuid) AND r.started_at > :since "
    "AND k.key = ANY(CAST(:keys AS text[])) AND jsonb_typeof(k.value) = 'object' "
    "AND COALESCE((k.value ->> 'upserted')::int, 0) "
    "  + COALESCE((k.value ->> 'deleted')::int, 0) "
    "  + COALESCE((k.value ->> 'relinked')::int, 0) > 0 "
    "GROUP BY k.key"
)

#: Only the module list of the newest profile, never the whole row.
_MODULES_SQL = (
    "SELECT profile -> 'modules' AS modules FROM crm_source_profiles "
    "WHERE organization_id = CAST(:org AS uuid) "
    "ORDER BY fetched_at DESC, version DESC LIMIT 1"
)


@dataclass(frozen=True)
class Fill:
    """The fill numbers of one key."""

    filled: int
    total: int
    distinct: int

    @property
    def share(self) -> float | None:
        """``filled / total``, from 0 to 1. ``None`` with no record."""
        if self.total <= 0:
            return None
        return round(self.filled / self.total, 4)


def list_columns(slug: str) -> tuple[str, ...]:
    """The keys of the list columns of *slug*, as ``export.COLUMNS`` names
    them. One vocabulary with the list and the export.

    A late import: ``export`` imports this module.
    """
    from gateway.routes.crm.export import COLUMNS

    return tuple(key for key, _header in COLUMNS[slug])


@functools.cache
def standard_keys(slug: str) -> tuple[str, ...]:
    """The standard keys of a core entity, in order, with no repeat: the real
    column of each list column, then each system field."""
    aliases = LIST_ALIASES.get(slug, {})
    keys: list[str] = []
    for key in (
        *(aliases.get(k, k) for k in list_columns(slug)),
        *(f.key for f in SYSTEM_FIELDS[slug]),
    ):
        if key not in keys:
            keys.append(key)
    return tuple(keys)


def fill_key(key: str, *, is_system: bool, module: bool = False) -> str:
    """The ``crm_field_fill.key`` of a field definition."""
    if module or is_system:
        return key
    return f"{CUSTOM_PREFIX}{key}"


def count_key(entity: str) -> str:
    """The key of *entity* in ``crm_sync_runs.counts``."""
    if entity.startswith(_MODULE_PREFIX):
        return entity
    return _SLUG_BY_ENTITY[entity]


async def read_fill(db: Any, org: str, entity: str) -> dict[str, Fill]:
    """Every fill row of one entity (``crm_field_defs.entity`` word)."""
    rows = (await db.execute(
        text(_FILL_SQL), {"org": str(org), "entity": entity},
    )).fetchall()
    return {
        str(r.key): Fill(int(r.filled or 0), int(r.total or 0), int(r.distinct_count or 0))
        for r in rows
    }


async def fills_for(db: Any, org: str | None, entity: str) -> dict[str, Fill] | None:
    """The fill rows of *entity*, or ``None`` when the flag shuts *org* out.

    No read happens for an org outside the list.
    """
    if not org or not fill_rank_allowed(org):
        return None
    return await read_fill(db, str(org), entity)


def annotate[M: BaseModel](
    defs: Sequence[M], fills: dict[str, Fill] | None, *, module: bool = False,
) -> list[M]:
    """Each definition with ``fill`` and ``distinct`` from *fills*.

    ``fills`` of ``None`` (the flag is off) leaves each definition as it was.
    A key with no row gets ``None`` in both.
    """
    if fills is None:
        return list(defs)
    out: list[M] = []
    for d in defs:
        key = str(getattr(d, "key", ""))
        system = bool(getattr(d, "is_system", False))
        found = fills.get(fill_key(key, is_system=system, module=module))
        out.append(d.model_copy(update={
            "fill": found.share if found is not None else None,
            "distinct": found.distinct if found is not None else None,
        }))
    return out


# ── Staleness ────────────────────────────────────────────────────────────────


def rule_marks(rules_state: dict[str, Any] | None) -> set[tuple[Any, ...]]:
    """The marks of the applied field rules: ``(id, entity, field, column,
    backfilling)`` for each. A late import: the routes import ``crm_sync``
    lazily."""
    from gateway.crm_sync import field_rules

    return {(*r.key, r.backfilling) for r in field_rules.applied_of(rules_state)}


def saved_marks(state: dict[str, Any] | None) -> set[tuple[Any, ...]]:
    """The rule marks that the last fill step stored in its cursor row."""
    return {
        tuple(m) for m in (state or {}).get("rules") or ()
        if isinstance(m, list) and len(m) == 5
    }


def rule_entities(old: set[tuple[Any, ...]], new: set[tuple[Any, ...]]) -> set[str]:
    """The entities of each field rule whose mark differs: a rule that a run
    backfilled, finished or undid."""
    return {str(mark[1]) for mark in old ^ new}


def stamps(state: dict[str, Any] | None, key: str = "at") -> dict[str, datetime]:
    """The stamp of each entity in the state of the fill row. *key* ``checked``
    gives the start of the last run that found the entity not stale."""
    out: dict[str, datetime] = {}
    for entity, value in ((state or {}).get(key) or {}).items():
        parsed = _parse_time(value)
        if parsed is not None:
            out[str(entity)] = parsed
    return out


def _parse_time(value: Any) -> datetime | None:
    """An ISO time of the fill row, or ``None``. A naive time is UTC."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


async def read_cursors(db: Any, org: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """The state of the fill row and of the field rules row."""
    rows = (await db.execute(
        text(_CURSORS_SQL), {"org": str(org), "rows": [FILL_ROW, RULES_ROW]},
    )).fetchall()
    found = {str(r.module): from_jsonb(r.cursor_state) or {} for r in rows}
    return found.get(FILL_ROW, {}), found.get(RULES_ROW, {})


async def stale_entities(
    db: Any,
    org: str,
    entities: Iterable[str],
    *,
    state: dict[str, Any] | None = None,
    rules_state: dict[str, Any] | None = None,
) -> set[str]:
    """The ONE rule of stale numbers (module docstring). The sync passes the
    states it holds. A route passes neither, and this reads them."""
    wanted = list(dict.fromkeys(entities))
    if state is None or rules_state is None:
        read_state, read_rules = await read_cursors(db, org)
        state = read_state if state is None else state
        rules_state = read_rules if rules_state is None else rules_state
    at = stamps(state)
    checked = stamps(state, "checked")
    pending = {str(e) for e in state.get("pending") or ()}
    stale = {e for e in wanted if e not in at or e in pending}
    stale |= rule_entities(saved_marks(state), rule_marks(rules_state)) & set(wanted)
    rest = [e for e in wanted if e not in stale]
    if not rest:
        return stale
    # A run that STARTED after the later of the stamp and the checked mark is
    # new. The checked mark is the start of a run that found no change, so the
    # scan of an entity that never changes stays short.
    since = {e: max(at[e], checked.get(e, at[e])) for e in rest}
    return stale | await changed_since(db, org, since)


async def changed_since(db: Any, org: str, since: dict[str, datetime]) -> set[str]:
    """The entities of *since* that a sync run changed or re-linked, in a run
    that STARTED after the time of the entity. One statement for all of them
    (``_CHANGED_SQL``)."""
    if not since:
        return set()
    rows = (await db.execute(text(_CHANGED_SQL), {
        "org": str(org), "since": min(since.values()),
        "keys": sorted({count_key(e) for e in since}),
    })).fetchall()
    changed = {str(r.key): r.changed_at for r in rows}
    out: set[str] = set()
    for entity, after in since.items():
        when = changed.get(count_key(entity))
        if when is not None and when > after:
            out.add(entity)
    return out


# ── Still empty (CRM-Q17a-1b) ────────────────────────────────────────────────


def backfilling_entities(rules_state: dict[str, Any] | None) -> set[str]:
    """The entities of each applied field rule with ``backfilling`` true. A
    backfill can take more than one run, and no run count records its rows."""
    from gateway.crm_sync import field_rules

    return {r.entity for r in field_rules.applied_of(rules_state) if r.backfilling}


def still_empty_of(state: dict[str, Any] | None) -> dict[str, tuple[list[str], datetime]]:
    """The "still empty" checks in the state of the fill row: the keys that
    stayed empty and the time of the check, for each entity. A malformed
    entry is left out, so its entity serves ``[]``."""
    out: dict[str, tuple[list[str], datetime]] = {}
    found = (state or {}).get(STILL_EMPTY)
    for entity, entry in (found if isinstance(found, dict) else {}).items():
        if not isinstance(entry, dict):
            continue
        keys, at = entry.get("keys"), _parse_time(entry.get("at"))
        if at is None or not isinstance(keys, list):
            continue
        if not all(isinstance(k, str) for k in keys):
            continue
        out[str(entity)] = (list(keys), at)
    return out


def checked_keys(
    entity: str, state: dict[str, Any] | None, rules_state: dict[str, Any] | None,
) -> tuple[list[str], datetime] | None:
    """The check of a stale *entity* that may serve, or ``None``.

    The three tests that need no database: the entity has a stamp and none of
    its field rules changed (the rule test of :func:`stale_entities`), the
    check has a time, and no rule of the entity has ``backfilling`` true. The
    caller then asks :func:`changed_since` with the time of the check.
    """
    if entity not in stamps(state):
        return None
    if entity in rule_entities(saved_marks(state), rule_marks(rules_state)):
        return None
    if entity in backfilling_entities(rules_state):
        return None
    return still_empty_of(state).get(entity)


def still_empty_set(slug: str, fills: dict[str, Fill], keys: Iterable[str]) -> list[str]:
    """The served keys of a check: each checked key that :func:`empty_set`
    also gives from the stored numbers, in the words of :func:`empty_set`. So
    a key that the stored numbers show as filled is never served."""
    checked = set(keys)
    stored = empty_set(slug, fills)
    real = {k for k in stored if k in checked and k in standard_keys(slug)}
    served = set(real)
    for alias, column in LIST_ALIASES.get(slug, {}).items():
        if column in real:
            served.add(alias)
    return sorted(served)


def empty_set(slug: str, fills: dict[str, Fill]) -> list[str]:
    """The ONE rule of the empty standard keys (§18.9.6 CRM-Q17a-1 item 4).

    A standard key of *slug* with ``total > 0`` and ``filled = 0``. The name
    column and ``last_activity_at`` never show. Each key shows as its real
    column AND, for a joined list column, as the list key too, so the list,
    the layout and the export each find it in their own words.
    """
    empty = {
        key for key in standard_keys(slug)
        if key not in NEVER_EMPTY
        and (f := fills.get(key)) is not None and f.total > 0 and f.filled == 0
    }
    for alias, real in LIST_ALIASES.get(slug, {}).items():
        if real in empty:
            empty.add(alias)
    return sorted(empty)


async def empty_columns(
    db: Any, org: str | None, slug: str, fills: dict[str, Fill] | None = None,
) -> list[str] | None:
    """``empty_columns`` of *slug* for *org*: ``None`` when the flag shuts
    *org* out, else :func:`empty_set`. While the numbers are stale, the keys
    of the last "still empty" check (:func:`still_empty_set`) when it may
    serve (module docstring), else ``[]``.

    ``GET /crm/fields/{entity}?layout=1`` serves it and the CSV export reads
    it. Both call this function. *fills* saves a read when the caller holds
    them already.
    """
    if not org or not fill_rank_allowed(org):
        return None
    entity = FIELD_ENTITY_BY_SLUG[slug]
    if fills is None:
        fills = await read_fill(db, str(org), entity)
    state, rules_state = await read_cursors(db, str(org))
    if not await stale_entities(db, str(org), [entity], state=state, rules_state=rules_state):
        return empty_set(slug, fills)
    check = checked_keys(entity, state, rules_state)
    if check is None:
        return []
    keys, at = check
    if await changed_since(db, str(org), {entity: at}):
        return []
    return still_empty_set(slug, fills, keys)


# ── The source labels ────────────────────────────────────────────────────────


class EntityLabel(BaseModel):
    """The source's own names of one core entity (§18.9.1)."""

    label: str
    singular_label: str | None = None


#: The Zoho module of each core entity → its API slug. ``Entity.zoho_module``
#: is the one answer to "which Zoho module is a deal".
_SLUG_BY_MODULE: dict[str, str] = {
    e.zoho_module: e.slug for e in ENTITIES.values() if e.zoho_module
}


def labels_of(modules: Any) -> dict[str, EntityLabel]:
    """The plural and singular label of each core entity in *modules*, the
    module list of a profile.

    ``_module_rows`` in the Zoho mapping stores ``label`` (the plural) and
    ``singular_label`` for each module.
    """
    out: dict[str, EntityLabel] = {}
    for m in modules if isinstance(modules, list) else ():
        if not isinstance(m, dict):
            continue
        slug = _SLUG_BY_MODULE.get(str(m.get("api_name") or ""))
        label = m.get("label")
        if slug is None or not isinstance(label, str) or not label.strip():
            continue
        singular = m.get("singular_label")
        if not isinstance(singular, str) or not singular.strip():
            singular = None
        out[slug] = EntityLabel(
            label=label.strip(), singular_label=singular.strip() if singular else None,
        )
    return out


async def source_labels(db: Any, org: str | None) -> dict[str, EntityLabel] | None:
    """The source's labels of the four core entities (CRM-Q17a-1 item 6).

    ``None`` for an org outside ``CRM_FILL_RANK_ORGS``, in Native mode, or
    with no source profile yet. It reads only ``profile -> 'modules'``,
    because an agent run reads ``GET /crm/mode`` often.
    """
    if not org or not fill_rank_allowed(org):
        return None
    if not await is_mirror_mode(db, str(org)):
        return None
    row = (await db.execute(text(_MODULES_SQL), {"org": str(org)})).first()
    if row is None:
        return None
    return labels_of(from_jsonb(row.modules)) or None


# ── The state of each core entity (CRM-Q17b) ─────────────────────────────────


class EntityState(BaseModel):
    """What the rail and the first screen need to know about one core entity
    (§18.9.6, CRM-Q17b items 2 to 4)."""

    #: The org holds at least one record of the entity in its own table.
    records: bool
    #: Zoho refused the module the last time the sync asked: it is turned off
    #: in Zoho, or hidden from the user who connected (CRM-Q15, the cursor
    #: reason ``hidden``). A missing scope is not "turned off".
    turned_off: bool


#: The slug of each core entity, in a fixed order, for the one statement.
_CORE_SLUGS: tuple[str, ...] = tuple(sorted(ENTITIES))

#: One row: for each core entity, whether the org holds a record of it. Each
#: ``EXISTS`` reads one row at most, through the index on ``organization_id``
#: (``241_crm_tenancy.sql``). The table names are constants of ``core.py``.
_HOLDS_SQL = "SELECT " + ", ".join(
    f"EXISTS (SELECT 1 FROM {ENTITIES[slug].table} "
    f"WHERE organization_id = CAST(:org AS uuid)) AS {slug}"
    for slug in _CORE_SLUGS
)


def turned_off_of(state: Any) -> bool:
    """True when a cursor state says that Zoho refused the module. The engine
    writes ``unavailable`` with the reason ``hidden`` (``engine._mark_unavailable``)."""
    return isinstance(state, dict) and bool(state.get("unavailable")) and (
        state.get("reason") == "hidden"
    )


async def entity_states(db: Any, org: str | None) -> dict[str, EntityState] | None:
    """The state of each core entity, by API slug, for ``GET /crm/mode``.

    ``None`` for an org outside ``CRM_FILL_RANK_ORGS`` and outside Mirror
    mode, as :func:`source_labels`. That keeps CRM-Q17b dark until the flip.
    It reads one row of four ``EXISTS`` and the four cursor rows of the core
    entities. It never counts a table.
    """
    if not org or not fill_rank_allowed(org):
        return None
    if not await is_mirror_mode(db, str(org)):
        return None
    holds = (await db.execute(text(_HOLDS_SQL), {"org": str(org)})).first()
    rows = [f"{PROVIDER}:{FIELD_ENTITY_BY_SLUG[slug]}" for slug in _CORE_SLUGS]
    cursors = {
        str(r.module): from_jsonb(r.cursor_state)
        for r in (await db.execute(
            text(_CURSORS_SQL), {"org": str(org), "rows": rows},
        )).fetchall()
    }
    return {
        slug: EntityState(
            records=bool(getattr(holds, slug, False)) if holds is not None else False,
            turned_off=turned_off_of(cursors.get(f"{PROVIDER}:{FIELD_ENTITY_BY_SLUG[slug]}")),
        )
        for slug in _CORE_SLUGS
    }


__all__ = [
    "FILL_ROW",
    "LIST_ALIASES",
    "NAME_KEYS",
    "NEVER_EMPTY",
    "RULES_ROW",
    "STILL_EMPTY",
    "EntityLabel",
    "EntityState",
    "Fill",
    "annotate",
    "backfilling_entities",
    "changed_since",
    "checked_keys",
    "count_key",
    "empty_columns",
    "empty_set",
    "entity_states",
    "fill_key",
    "fills_for",
    "labels_of",
    "list_columns",
    "read_cursors",
    "read_fill",
    "rule_entities",
    "rule_marks",
    "saved_marks",
    "source_labels",
    "stale_entities",
    "stamps",
    "standard_keys",
    "still_empty_of",
    "still_empty_set",
    "turned_off_of",
]
