"""The fill rank in the sync (WS-53 CRM-Q17a-1, migration 274).

Spec: ``project-docs/specs/crm_platform.md`` §18.9.3.

At the end of a run, after the module step, the sync counts for each key of
an entity how many records fill it, how many records there are, and how
many different filled values it has. It writes ``crm_field_fill``. The
vocabulary of the keys and the rule of "filled" live in
``routes/crm/fill_rank.py``, the read side.

**Which entity.** An entity is due when this run changed it, when a change
waits from an earlier run, or when it has no numbers yet. A change is an
``upserted`` or ``deleted`` count of the entity (the slug of a core entity,
or ``module:<ApiName>``), or a field rule that this run backfilled or undid.
An entity whose first import is not done has no numbers yet.

**The cost bound.** An entity is computed again at most once in
``every`` (the engine passes ``DISCOVERY_EVERY``, 24 hours). An entity whose
first import finished in this run is the exception. A change inside the bound
waits in the state of the cursor row :data:`FILL_ROW`, so the next run after
the bound computes it. One SQL statement computes AND writes one entity: it
reads the rows once, upserts each key, and deletes each key that left.

**Where it touches the database.** One ``ctx.page()`` for each entity: a
``tenant_session(org)`` that reads the mode ``FOR SHARE`` (the CRM-X0 start
over waits for it), and that writes the cursor row in the same transaction
as the numbers (R5). No provider call. The statement runs under a LOCAL
``statement_timeout`` of :data:`TIMEOUT_MS`. A database error on one entity,
a timeout too, is logged by class and skipped, and it never fails the run.
The attempt keeps its stamp and the entity stays pending, so the bound holds
for a failure too.

**Ship dark.** Nothing here runs for an org outside ``CRM_FILL_RANK_ORGS``
(``flags.fill_rank_allowed``).
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Any

from acb_common import get_logger
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from gateway.crm_sync import field_rules, runs
from gateway.crm_sync.runs import CursorRow
from gateway.routes.crm import fill_rank, flags
from gateway.routes.crm.core import CUSTOM_PREFIX, FIELD_ENTITY_BY_SLUG

_log = get_logger("crm.sync.fill")

#: The cursor row that keeps, for each entity, when its numbers were computed
#: (``at``) and the entities whose change waits for the bound (``pending``).
FILL_ROW = "zoho:fill_rank"
#: The cursor row of the field rules (``engine.FIELD_RULES_ROW``).
_RULES_ROW = "zoho:field_rules"
#: The prefix of a changed-list cursor row (``engine.changed_module``).
_CURSOR_PREFIX = "zoho:"
_MODULE_PREFIX = "module:"

#: The API slug of each core entity in the counts → its entity word.
ENTITY_BY_COUNT_SLUG: dict[str, str] = dict(FIELD_ENTITY_BY_SLUG)
#: The order of the core entities: the order of the engine's reads.
CORE_ORDER: tuple[str, ...] = ("company", "contact", "lead", "deal")
_SLUG_BY_ENTITY: dict[str, str] = {v: k for k, v in FIELD_ENTITY_BY_SLUG.items()}

#: The longest one statement may run. 30,000 module rows x 236 fields take
#: about 4 s (35 s when each value differs) on a developer machine.
TIMEOUT_MS = 120_000
_TIMEOUT_SQL = "SELECT set_config('statement_timeout', :ms, true)"

#: The rule of "filled" on one jsonb value ``value`` (``fill_rank`` docstring).
FILLED_SQL = (
    "NOT (value IN ('null'::jsonb, '\"\"'::jsonb, '[]'::jsonb, '{}'::jsonb, "
    "'false'::jsonb) "
    "OR (jsonb_typeof(value) = 'string' AND btrim(value #>> '{}') = ''))"
)

#: A core entity: each standard key from the row, each custom key from
#: ``custom``. ``{table}`` is a literal of the mapping, never request input.
_CORE_SOURCE = (
    "src AS ("
    " SELECT to_jsonb(t) AS doc FROM {table} t"
    " WHERE t.organization_id = CAST(:org AS uuid)"
    "), "
    "kv AS ("
    " SELECT e.key, e.value FROM src, jsonb_each(src.doc) e"
    " WHERE e.key = ANY(CAST(:std AS text[]))"
    " UNION ALL"
    " SELECT :prefix || e.key, e.value FROM src, jsonb_each("
    "  CASE WHEN jsonb_typeof(src.doc -> 'custom') = 'object'"
    "  THEN src.doc -> 'custom' ELSE '{{}}'::jsonb END) e"
    ")"
)

#: A generic module: each key of ``fields``. The module is a bound parameter.
_MODULE_SOURCE = (
    "src AS ("
    " SELECT r.fields AS doc FROM crm_module_records r"
    " WHERE r.organization_id = CAST(:org AS uuid) AND r.module = :module"
    "), "
    "kv AS ("
    " SELECT e.key, e.value FROM src, jsonb_each("
    "  CASE WHEN jsonb_typeof(src.doc) = 'object'"
    "  THEN src.doc ELSE '{}'::jsonb END) e"
    ")"
)

#: The shared tail: count, list the keys (standard, found, and defined), then
#: upsert each key and delete each key that left. ONE statement.
#:
#: A defined key that is a standard key is the overlay row of a fixed column
#: (CRM-C4a), so it is left out by its key. The studio refuses a custom key
#: that names a system field, and the sync never names a studio column
#: (``test_crm_mirror_readonly.py``).
_TAIL = (
    "marked AS (SELECT key, value, " + FILLED_SQL + " AS filled FROM kv), "
    # Two stages, because a hash of (key, value) is about six times faster
    # than ``count(DISTINCT value)`` per key, and never slower (measured on
    # 30,000 rows x 236 fields, test_crm_fill_rank_r8.py).
    "pairs AS ("
    " SELECT key, value, bool_or(filled) AS filled, count(*) AS n"
    " FROM marked GROUP BY key, value"
    "), "
    "agg AS ("
    " SELECT key, COALESCE(sum(n) FILTER (WHERE filled), 0) AS filled,"
    " count(*) FILTER (WHERE filled) AS distinct_count"
    " FROM pairs GROUP BY key"
    "), "
    "keys AS ("
    " SELECT unnest(CAST(:std AS text[])) AS key"
    " UNION SELECT key FROM agg"
    " UNION SELECT :prefix || d.key FROM crm_field_defs d"
    " WHERE d.organization_id = CAST(:org AS uuid) AND d.entity = :entity"
    " AND d.key <> ALL(CAST(:std AS text[]))"
    "), "
    "n AS (SELECT count(*) AS total FROM src), "
    "written AS ("
    " INSERT INTO crm_field_fill (organization_id, entity, key, filled, total,"
    " distinct_count, computed_at)"
    " SELECT CAST(:org AS uuid), :entity, k.key, COALESCE(a.filled, 0), n.total,"
    " COALESCE(a.distinct_count, 0), now()"
    " FROM keys k CROSS JOIN n LEFT JOIN agg a ON a.key = k.key"
    " ON CONFLICT (organization_id, entity, key) DO UPDATE SET"
    " filled = EXCLUDED.filled, total = EXCLUDED.total,"
    " distinct_count = EXCLUDED.distinct_count, computed_at = EXCLUDED.computed_at"
    " RETURNING key"
    "), "
    "gone AS ("
    " DELETE FROM crm_field_fill f"
    " WHERE f.organization_id = CAST(:org AS uuid) AND f.entity = :entity"
    " AND NOT EXISTS (SELECT 1 FROM keys k WHERE k.key = f.key)"
    " RETURNING f.key"
    ") "
    "SELECT (SELECT count(*) FROM written) AS written,"
    " (SELECT count(*) FROM gone) AS gone"
)


def statement(entity: str, mapping: Any) -> tuple[str, dict[str, Any]]:
    """The ONE statement that computes and writes *entity*, and its params
    (without ``org``). A core entity reads its table, a module reads
    ``crm_module_records``."""
    module = mapping.module_of(entity)
    if module is not None:
        sql = "WITH " + _MODULE_SOURCE + ", " + _TAIL
        return sql, {"entity": entity, "module": module, "std": [], "prefix": ""}
    slug = _SLUG_BY_ENTITY[entity]
    sql = "WITH " + _CORE_SOURCE.format(table=mapping.ENTITY_TABLES[entity]) + ", " + _TAIL
    return sql, {
        "entity": entity, "std": list(fill_rank.standard_keys(slug)),
        "prefix": CUSTOM_PREFIX,
    }


async def compute(db: Any, org: str, entity: str, mapping: Any) -> tuple[int, int]:
    """Compute and write the numbers of one entity. Returns ``(keys written,
    keys deleted)``. The caller owns the transaction.

    The statement runs under a LOCAL ``statement_timeout`` of
    :data:`TIMEOUT_MS`, the idiom of ``routes/shell/feed_measure.py``. A
    timeout is a database error, so :func:`refresh` logs it and goes on.
    """
    await db.execute(text(_TIMEOUT_SQL), {"ms": str(TIMEOUT_MS)})
    sql, params = statement(entity, mapping)
    row = (await db.execute(text(sql), {**params, "org": str(org)})).first()
    return (int(row.written or 0), int(row.gone or 0)) if row is not None else (0, 0)


def _now() -> datetime:
    return datetime.now(UTC)


def _parse(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _done(cursors: dict[str, CursorRow], entity: str) -> bool:
    row = cursors.get(f"{_CURSOR_PREFIX}{entity}")
    return row is not None and row.initial_done_at is not None


def _rule_entities(start: CursorRow | None, end: CursorRow | None) -> set[str]:
    """The entities of each field rule that this run backfilled or undid: a
    rule whose applied state differs between the start and the end."""
    def marks(row: CursorRow | None) -> set[tuple[Any, ...]]:
        state = row.state if row is not None else None
        return {(r.key, r.backfilling) for r in field_rules.applied_of(state)}

    return {key[1] for key, _flag in marks(start) ^ marks(end)}


def changed_entities(ctx: Any) -> set[str]:
    """The entities that this run changed (§18.9.3 item 1)."""
    out: set[str] = set()
    for key, values in (ctx.counts or {}).items():
        if not isinstance(values, dict):
            continue
        if not (values.get("upserted") or values.get("deleted")):
            continue
        if key.startswith(_MODULE_PREFIX):
            out.add(key)
        elif key in ENTITY_BY_COUNT_SLUG:
            out.add(ENTITY_BY_COUNT_SLUG[key])
    out |= _rule_entities(ctx.handle.cursors.get(_RULES_ROW), ctx.cursors.get(_RULES_ROW))
    return out


def _candidates(ctx: Any, pending: set[str]) -> list[str]:
    """The core entities, then each module with a cursor, then any other
    pending entity. Each once, in that order."""
    modules = sorted(
        name[len(_CURSOR_PREFIX):] for name in ctx.cursors
        if name.startswith(f"{_CURSOR_PREFIX}{_MODULE_PREFIX}")
        and not name.endswith(":deleted")
    )
    out: list[str] = []
    for entity in (*CORE_ORDER, *modules, *sorted(pending)):
        if entity not in out:
            out.append(entity)
    return out


async def refresh(ctx: Any, *, every: timedelta) -> None:
    """The fill step of one run (§18.9.3). See the module docstring.

    *ctx* is the engine's run state: ``org``, ``counts``, ``cursors``,
    ``handle.cursors``, ``mapping``, ``should_stop`` and ``page()``.
    """
    if not flags.fill_rank_allowed(ctx.org):
        return
    saved = ctx.cursors.get(FILL_ROW)
    state = (saved.state if saved is not None else None) or {}
    at: dict[str, str] = {
        str(k): str(v) for k, v in (state.get("at") or {}).items() if isinstance(v, str)
    }
    pending = {str(e) for e in state.get("pending") or ()} | changed_entities(ctx)
    first_at = (saved.initial_done_at if saved is not None else None) or _now()
    stored = {"at": dict(at), "pending": sorted(state.get("pending") or ())}

    def snapshot() -> dict[str, Any]:
        return {"at": dict(at), "pending": sorted(pending)}

    for entity in _candidates(ctx, pending):
        if ctx.should_stop():
            break
        if not _done(ctx.cursors, entity):
            continue  # no full read yet: the numbers would be partial
        old = at.get(entity)
        stamp = _parse(old)
        if stamp is not None and entity not in pending:
            continue
        first_now = not _done(ctx.handle.cursors, entity)
        if stamp is not None and not first_now and _now() - stamp < every:
            continue  # inside the bound: the change stays pending
        started = time.monotonic()
        at[entity] = _now().isoformat()
        pending.discard(entity)
        try:
            async with ctx.page() as db:
                written, gone = await compute(db, ctx.org, entity, ctx.mapping)
                await runs.write_cursor(
                    db, ctx.org, CursorRow(FILL_ROW, None, snapshot(), first_at, None), "ok",
                )
        except SQLAlchemyError as exc:
            # The page rolled back, so no number of this entity is stored.
            # The attempt keeps its stamp, so the bound holds for a failure
            # too: a slow entity is tried once in 24 hours, never each run.
            pending.add(entity)
            _log.warning("crm.sync.fill_failed entity=%s class=%s", entity, type(exc).__name__)
            continue
        stored = snapshot()
        _log.info(
            "crm.sync.fill entity=%s keys=%s gone=%s ms=%s",
            entity, written, gone, int((time.monotonic() - started) * 1000),
        )
    if snapshot() != stored:
        # A change that waits for the bound, or a failed entity, is kept for
        # the next run.
        async with ctx.page() as db:
            await runs.write_cursor(
                db, ctx.org, CursorRow(FILL_ROW, None, snapshot(), first_at, None), "ok",
            )
    ctx.cursors[FILL_ROW] = CursorRow(FILL_ROW, None, snapshot(), first_at, None)


__all__ = [
    "FILLED_SQL",
    "FILL_ROW",
    "changed_entities",
    "compute",
    "refresh",
    "statement",
]
