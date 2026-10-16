"""The fill rank in the sync (WS-53 CRM-Q17a-1, migration 274).

Spec: ``project-docs/specs/crm_platform.md`` §18.9.3.

At the end of a run, after the module step, the sync counts for each key of
an entity how many records fill it, how many records there are, and how
many different filled values it has. It writes ``crm_field_fill``. The
vocabulary of the keys, the rule of "filled", the rule of ``distinct`` and the
rule of stale numbers live in ``routes/crm/fill_rank.py``, the read side.

**Which entity.** An entity is due when its numbers are stale
(``fill_rank.stale_entities``): no stamp yet, ``pending``, a field rule of it
changed, or a run that started after its stamp changed a row of it. An entity
whose first import is not done waits, because its numbers would be partial.

**The cost bound.**

* An entity is computed again at most once in ``every`` (the engine passes
  ``DISCOVERY_EVERY``, 24 hours). An entity whose first import finished in
  this run is the exception. A due entity inside the bound waits in
  ``pending``.
* At most ONE entity above :data:`LARGE_ROWS` records is computed in a run.
  The others wait in ``pending`` for the next runs.
* One statement reads an entity: ``filled`` and ``total`` over each record,
  grouped by key only, and ``distinct`` over a sample of
  :data:`DISTINCT_SAMPLE` records. It runs under a LOCAL
  ``statement_timeout`` of :data:`TIMEOUT_MS`.

**The order for one entity.**

1. A short ``ctx.page()`` commits the attempt: the stamp, and the entity in
   ``pending``. A page also writes the run heartbeat, so a long fill step
   keeps the lock of a slow run. A stop after this point does not repeat the
   statement inside the bound.
2. The statement runs in a plain ``tenant_session(org)``, with no lock on
   ``crm_settings``. So an admin write of the settings does not wait for it.
3. A short ``ctx.page()`` writes the numbers, and takes the entity out of
   ``pending``. The page reads the mode ``FOR SHARE``, so a start over that
   came in between stops the write.

A database error, a timeout too, is logged by class. The entity keeps its
stamp and stays ``pending``, so it waits for the bound. A fill step never
fails the run. Each step is its own tenant session (R5).

**Ship dark.** Nothing here runs for an org outside ``CRM_FILL_RANK_ORGS``
(``flags.fill_rank_allowed``).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from acb_common import get_logger
from acb_common.db import tenant_session
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from gateway.crm_sync import runs
from gateway.crm_sync.holds import LARGE_MODULE_RECORDS
from gateway.crm_sync.runs import CursorRow
from gateway.routes.crm import fill_rank, flags
from gateway.routes.crm.core import CUSTOM_PREFIX, FIELD_ENTITY_BY_SLUG

_log = get_logger("crm.sync.fill")

#: The cursor row of the fill step (``fill_rank.FILL_ROW``).
FILL_ROW = fill_rank.FILL_ROW
_RULES_ROW = fill_rank.RULES_ROW
#: The prefix of a changed-list cursor row (``engine.changed_module``).
_CURSOR_PREFIX = "zoho:"
_MODULE_PREFIX = "module:"

#: The longest one statement may run. On a developer machine, 30,000 module
#: rows x 236 fields of unique values take about 5 s
#: (``test_crm_fill_rank_r8.py`` prints the time).
TIMEOUT_MS = 30_000
_TIMEOUT_SQL = "SELECT set_config('statement_timeout', :ms, true)"
#: The database clock, and the start of this run in the same clock. Every
#: stamp and mark compares with ``crm_sync_runs.started_at``, so all of them
#: come from the database, never from the app (review P2).
_CLOCK_SQL = (
    "SELECT now() AS now, (SELECT started_at FROM crm_sync_runs "
    "WHERE id = CAST(:run AS uuid)) AS started"
)
#: ``distinct`` reads at most this many records: the ones with the lowest ids.
DISTINCT_SAMPLE = 2_000
#: An entity above this many records is large, the threshold of a large
#: module in CRM-Q16. A run computes one large entity at most.
LARGE_ROWS = LARGE_MODULE_RECORDS

#: The API slug of each core entity in the counts → its entity word.
ENTITY_BY_COUNT_SLUG: dict[str, str] = dict(FIELD_ENTITY_BY_SLUG)
#: The order of the core entities: the order of the engine's reads.
CORE_ORDER: tuple[str, ...] = ("company", "contact", "lead", "deal")
_SLUG_BY_ENTITY: dict[str, str] = {v: k for k, v in FIELD_ENTITY_BY_SLUG.items()}

#: The rule of "filled" on one jsonb value ``value`` (``fill_rank`` docstring).
#: A CASE on the type costs about a third of five jsonb compares.
FILLED_SQL = (
    "CASE jsonb_typeof(value) WHEN 'null' THEN false "
    "WHEN 'boolean' THEN value = 'true'::jsonb "
    "WHEN 'string' THEN btrim(value #>> '{}') <> '' "
    "WHEN 'array' THEN jsonb_array_length(value) > 0 "
    "WHEN 'object' THEN value <> '{}'::jsonb ELSE true END"
)

#: The rows of one entity. ``{table}`` is a literal of the mapping, never
#: request input. The module is a bound parameter.
_CORE_ROWS = "SELECT * FROM {table} WHERE organization_id = CAST(:org AS uuid)"
_MODULE_ROWS = (
    "SELECT * FROM crm_module_records "
    "WHERE organization_id = CAST(:org AS uuid) AND module = :module"
)


def _core_pairs(rows: str) -> str:
    """``(key, value)`` of each standard key and each custom key of *rows*."""
    return (
        f"SELECT e.key, e.value FROM ({rows}) t, jsonb_each(to_jsonb(t)) e"
        " WHERE e.key = ANY(CAST(:std AS text[]))"
        " UNION ALL"
        f" SELECT :prefix || e.key, e.value FROM ({rows}) t, jsonb_each("
        "  CASE WHEN jsonb_typeof(t.custom) = 'object'"
        "  THEN t.custom ELSE '{}'::jsonb END) e"
    )


def _module_pairs(rows: str) -> str:
    """``(key, value)`` of each key of ``fields`` of *rows*."""
    return (
        f"SELECT e.key, e.value FROM ({rows}) t, jsonb_each("
        "  CASE WHEN jsonb_typeof(t.fields) = 'object'"
        "  THEN t.fields ELSE '{}'::jsonb END) e"
    )


def _read_sql(rows: str, pairs: Callable[[str], str]) -> str:
    """The ONE read statement of an entity. Subqueries, not a shared CTE, so
    the planner holds no record set in memory: ``filled`` groups by key only,
    and ``distinct`` reads the sample alone.

    A defined key that is a standard key is the overlay row of a fixed column
    (CRM-C4a), so it is left out by its key. The studio refuses a custom key
    that names a system field, and the sync never names a studio column
    (``test_crm_mirror_readonly.py``).
    """
    sample = f"{rows} ORDER BY id LIMIT :sample"
    return (
        "WITH agg AS ("
        f" SELECT key, count(*) FILTER (WHERE {FILLED_SQL}) AS filled"
        f" FROM ({pairs(rows)}) kv GROUP BY key"
        "), "
        "sdist AS ("
        " SELECT key, count(DISTINCT value) AS distinct_count"
        f" FROM ({pairs(sample)}) kv WHERE {FILLED_SQL} GROUP BY key"
        "), "
        "keys AS ("
        " SELECT unnest(CAST(:std AS text[])) AS key"
        " UNION SELECT key FROM agg"
        " UNION SELECT :prefix || d.key FROM crm_field_defs d"
        " WHERE d.organization_id = CAST(:org AS uuid) AND d.entity = :entity"
        " AND d.key <> ALL(CAST(:std AS text[]))"
        "), "
        f"n AS (SELECT count(*) AS total FROM ({rows}) t) "
        "SELECT k.key, COALESCE(a.filled, 0) AS filled, n.total,"
        " COALESCE(s.distinct_count, 0) AS distinct_count, now() AS computed_at"
        " FROM keys k CROSS JOIN n"
        " LEFT JOIN agg a ON a.key = k.key LEFT JOIN sdist s ON s.key = k.key"
    )


#: The write of one entity: upsert each key, delete each key that left.
_WRITE_SQL = (
    "WITH incoming AS ("
    " SELECT * FROM unnest(CAST(:keys AS text[]), CAST(:filled AS int[]),"
    " CAST(:distinct AS int[])) AS u(key, filled, distinct_count)"
    "), "
    "written AS ("
    " INSERT INTO crm_field_fill (organization_id, entity, key, filled, total,"
    " distinct_count, computed_at)"
    " SELECT CAST(:org AS uuid), :entity, key, filled, :total, distinct_count, :at"
    " FROM incoming"
    " ON CONFLICT (organization_id, entity, key) DO UPDATE SET"
    " filled = EXCLUDED.filled, total = EXCLUDED.total,"
    " distinct_count = EXCLUDED.distinct_count, computed_at = EXCLUDED.computed_at"
    " RETURNING key"
    "), "
    "gone AS ("
    " DELETE FROM crm_field_fill f"
    " WHERE f.organization_id = CAST(:org AS uuid) AND f.entity = :entity"
    " AND f.key <> ALL(CAST(:keys AS text[]))"
    " RETURNING f.key"
    ") "
    "SELECT (SELECT count(*) FROM written) AS written,"
    " (SELECT count(*) FROM gone) AS gone"
)


def _rows(entity: str, mapping: Any) -> tuple[str, dict[str, Any], Callable[[str], str]]:
    """The rows of *entity*, their params, and the pairs builder."""
    module = mapping.module_of(entity)
    if module is not None:
        return _MODULE_ROWS, {"module": module, "std": [], "prefix": ""}, _module_pairs
    slug = _SLUG_BY_ENTITY[entity]
    return (
        _CORE_ROWS.format(table=mapping.ENTITY_TABLES[entity]),
        {"std": list(fill_rank.standard_keys(slug)), "prefix": CUSTOM_PREFIX},
        _core_pairs,
    )


def statement(entity: str, mapping: Any) -> tuple[str, dict[str, Any]]:
    """The ONE read statement of *entity* and its params (without ``org``)."""
    rows, params, pairs = _rows(entity, mapping)
    return _read_sql(rows, pairs), {**params, "entity": entity, "sample": DISTINCT_SAMPLE}


def size_statement(entity: str, mapping: Any) -> tuple[str, dict[str, Any]]:
    """The record count of *entity*, for the cap on large entities."""
    rows, params, _pairs = _rows(entity, mapping)
    return f"SELECT count(*) FROM ({rows}) t", {
        k: v for k, v in params.items() if k == "module"
    }


async def read(db: Any, org: str, entity: str, mapping: Any) -> dict[str, Any]:
    """Run the read statement of one entity, under the LOCAL timeout.

    Returns ``keys``, ``filled``, ``distinct``, ``total`` and ``at`` (the
    time of the statement). The caller owns the session.
    """
    await db.execute(text(_TIMEOUT_SQL), {"ms": str(TIMEOUT_MS)})
    sql, params = statement(entity, mapping)
    rows = (await db.execute(text(sql), {**params, "org": str(org)})).fetchall()
    return {
        "keys": [str(r.key) for r in rows],
        "filled": [int(r.filled or 0) for r in rows],
        "distinct": [int(r.distinct_count or 0) for r in rows],
        "total": int(rows[0].total) if rows else 0,
        "at": rows[0].computed_at if rows else datetime.now(UTC),
    }


async def write(db: Any, org: str, entity: str, numbers: dict[str, Any]) -> tuple[int, int]:
    """Write the numbers of one entity. Returns ``(keys written, keys
    deleted)``. The caller owns the transaction."""
    row = (await db.execute(text(_WRITE_SQL), {
        "org": str(org), "entity": entity, "keys": numbers["keys"],
        "filled": numbers["filled"], "distinct": numbers["distinct"],
        "total": numbers["total"], "at": numbers["at"],
    })).first()
    return (int(row.written or 0), int(row.gone or 0)) if row is not None else (0, 0)


async def compute(db: Any, org: str, entity: str, mapping: Any) -> tuple[int, int]:
    """Read and write one entity in one session (the benchmark, a test)."""
    return await write(db, org, entity, await read(db, org, entity, mapping))


def _now() -> datetime:
    return datetime.now(UTC)


def _done(cursors: dict[str, CursorRow], entity: str) -> bool:
    row = cursors.get(f"{_CURSOR_PREFIX}{entity}")
    return row is not None and row.initial_done_at is not None


def changed_entities(ctx: Any) -> set[str]:
    """The entities that this run's counts show as changed (§18.9.3 item 1).

    A key that is no core slug and no ``module:`` key (``tasks``,
    ``activities``, ``activity_drops`` …) names no entity of the fill rank.
    """
    out: set[str] = set()
    for key, values in (ctx.counts or {}).items():
        if not isinstance(values, dict):
            continue
        if not (values.get("upserted") or values.get("deleted") or values.get("relinked")):
            continue
        if key.startswith(_MODULE_PREFIX):
            out.add(key)
        elif key in ENTITY_BY_COUNT_SLUG:
            out.add(ENTITY_BY_COUNT_SLUG[key])
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


class _State:
    """The state of the fill row while a step runs."""

    def __init__(self, ctx: Any) -> None:
        saved = ctx.cursors.get(FILL_ROW)
        state = (saved.state if saved is not None else None) or {}
        self.at: dict[str, str] = {
            str(k): str(v) for k, v in (state.get("at") or {}).items() if isinstance(v, str)
        }
        self.pending: set[str] = {str(e) for e in state.get("pending") or ()}
        self.checked: dict[str, str] = {
            str(k): str(v) for k, v in (state.get("checked") or {}).items()
            if isinstance(v, str)
        }
        self.rules = sorted(list(m) for m in fill_rank.saved_marks(state))
        self.raw = state
        self.first_at = (saved.initial_done_at if saved is not None else None) or _now()

    def as_dict(self) -> dict[str, Any]:
        return {
            "at": dict(self.at), "pending": sorted(self.pending), "rules": self.rules,
            "checked": dict(self.checked),
        }

    def row(self) -> CursorRow:
        return CursorRow(FILL_ROW, None, self.as_dict(), self.first_at, None)


async def _save(ctx: Any, st: _State) -> bool:
    """Write the fill row in a short page. False, and a log line, on a
    database error: a fill step never fails the run."""
    try:
        async with ctx.page() as db:
            await runs.write_cursor(db, ctx.org, st.row(), "ok")
    except SQLAlchemyError as exc:
        _log.warning("crm.sync.fill_state_failed class=%s", type(exc).__name__)
        return False
    ctx.cursors[FILL_ROW] = st.row()
    return True


async def _stamp(ctx: Any, st: _State, entity: str) -> bool:
    """Commit the attempt on *entity* in a short page: the stamp from the
    database clock (``now()`` of this page), and the entity in ``pending``.
    False, and a log line, on a database error."""
    before = (st.at.get(entity), entity in st.pending)
    try:
        async with ctx.page() as db:
            now = (await db.execute(text("SELECT now()"))).scalar()
            st.at[entity] = now.isoformat()
            st.pending.add(entity)
            await runs.write_cursor(db, ctx.org, st.row(), "ok")
    except SQLAlchemyError as exc:
        if before[0] is None:
            st.at.pop(entity, None)
        else:
            st.at[entity] = before[0]
        if not before[1]:
            st.pending.discard(entity)
        _log.warning("crm.sync.fill_state_failed class=%s", type(exc).__name__)
        return False
    ctx.cursors[FILL_ROW] = st.row()
    return True


async def _size(ctx: Any, entity: str) -> int | None:
    """The record count of *entity*, or None on a database error."""
    sql, params = size_statement(entity, ctx.mapping)
    try:
        async with tenant_session(ctx.org) as db:
            return int((await db.execute(
                text(sql), {**params, "org": ctx.org},
            )).scalar() or 0)
    except SQLAlchemyError as exc:
        _log.warning("crm.sync.fill_failed entity=%s class=%s", entity, type(exc).__name__)
        return None


async def _due(
    ctx: Any, st: _State, candidates: list[str],
) -> tuple[set[str], datetime] | None:
    """The stale candidates (``fill_rank.stale_entities``) and the entities of
    this run's counts, and the time of the database. None on a database error.

    It moves the changed field rules into ``pending`` and takes their new
    marks. Each candidate with a stamp that is not due takes the start of THIS
    run as its ``checked`` mark (review P2). The fill step is the last step of
    a run, so the counts of this run are final here. A later run starts after
    this one, so the stale rule stays sound and its scan stays short.
    """
    rules_row = ctx.cursors.get(_RULES_ROW)
    rules_state = (rules_row.state if rules_row is not None else None) or {}
    try:
        async with tenant_session(ctx.org) as db:
            due = await fill_rank.stale_entities(
                db, ctx.org, candidates, state=st.raw, rules_state=rules_state,
            )
            clock = (await db.execute(text(_CLOCK_SQL), {"run": ctx.handle.run_id})).first()
    except SQLAlchemyError as exc:
        _log.warning("crm.sync.fill_state_failed class=%s", type(exc).__name__)
        return None
    marks = fill_rank.rule_marks(rules_state)
    st.pending |= fill_rank.rule_entities(fill_rank.saved_marks(st.raw), marks)
    st.rules = sorted(list(m) for m in marks)
    due |= changed_entities(ctx)
    if clock is not None and clock.started is not None:
        for entity in candidates:
            if entity in st.at and entity not in due and entity not in st.pending:
                st.checked[entity] = clock.started.isoformat()
    return due, (clock.now if clock is not None else _now())


async def refresh(ctx: Any, *, every: timedelta) -> None:
    """The fill step of one run (§18.9.3). See the module docstring.

    *ctx* is the engine's run state: ``org``, ``counts``, ``cursors``,
    ``handle.cursors``, ``mapping``, ``should_stop`` and ``page()``.
    """
    if not flags.fill_rank_allowed(ctx.org):
        return
    st = _State(ctx)
    candidates = _candidates(ctx, st.pending)
    found = await _due(ctx, st, candidates)
    if found is None:
        return
    due, db_now = found
    stamps = fill_rank.stamps(st.raw)
    large_done = False

    for entity in candidates:
        if ctx.should_stop():
            break
        if entity not in due or not _done(ctx.cursors, entity):
            continue  # not due, or no full read yet (the numbers would be partial)
        stamp = stamps.get(entity)
        first_now = not _done(ctx.handle.cursors, entity)
        if stamp is not None and not first_now and db_now - stamp < every:
            st.pending.add(entity)  # inside the bound: it waits
            continue
        size = await _size(ctx, entity)
        if size is None:
            continue
        if size > LARGE_ROWS:
            if large_done:
                st.pending.add(entity)  # one large entity in each run
                continue
            large_done = True
        # 1. The attempt, committed first. The page writes the heartbeat too.
        if not await _stamp(ctx, st, entity):
            continue
        started = time.monotonic()
        # 2. The statement, with no lock on crm_settings.
        try:
            async with tenant_session(ctx.org) as db:
                numbers = await read(db, ctx.org, entity, ctx.mapping)
        except SQLAlchemyError as exc:
            _log.warning("crm.sync.fill_failed entity=%s class=%s", entity, type(exc).__name__)
            continue
        # 3. The numbers, in a page that reads the mode FOR SHARE.
        st.pending.discard(entity)
        try:
            async with ctx.page() as db:
                written, gone = await write(db, ctx.org, entity, numbers)
                await runs.write_cursor(db, ctx.org, st.row(), "ok")
        except SQLAlchemyError as exc:
            st.pending.add(entity)
            _log.warning("crm.sync.fill_failed entity=%s class=%s", entity, type(exc).__name__)
            continue
        ctx.cursors[FILL_ROW] = st.row()
        _log.info(
            "crm.sync.fill entity=%s keys=%s gone=%s rows=%s ms=%s",
            entity, written, gone, size, int((time.monotonic() - started) * 1000),
        )
    stored = ctx.cursors.get(FILL_ROW)
    if stored is None or stored.state != st.as_dict():
        # The waiting entities and the rule marks, for the next run.
        await _save(ctx, st)


__all__ = [
    "DISTINCT_SAMPLE",
    "FILLED_SQL",
    "FILL_ROW",
    "LARGE_ROWS",
    "TIMEOUT_MS",
    "changed_entities",
    "compute",
    "read",
    "refresh",
    "size_statement",
    "statement",
    "write",
]
