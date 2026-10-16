"""CRM · aggregate — totals and stage dwell for an agent (WS-53 CRM-C7, CRM-Q25).

Spec: ``project-docs/specs/crm_platform.md`` §20.10 item 2, and §18.11
CRM-Q25 (the typed aggregate).

    GET /crm/aggregate               one measure, by one group
    GET /crm/aggregate/stage-dwell   days in each deal stage, and the deals
                                     that sit past their limit

**Read only.** Any member that the CRM access rule admits may call them: the
routes sit on the shared ``core.router``, so the kill switch, ``feature:crm``
and the access rule apply. The tenant comes from the session that the router
binds, never from the request (R5).

**The entity.** A core entity (``leads``, ``deals`` …), or a mirrored generic
module as ``module:<ApiName>`` (CRM-Q25). A module that the profile does not
mirror is a 404 (``modules._require_module``), before any read of a record.

**The measure.** ``count``, ``sum_amount`` and ``weighted`` (deals), or
``sum``, ``avg``, ``min`` and ``max`` of one number field, given as
``field=<key>``.

**The group.** ``group_by`` names a fixed key of :data:`GROUPS`, or of
:data:`MODULE_GROUPS` for a module. ``group_field=<key>`` groups by one field
instead, with ``bucket`` for a date. A call gives one of the two.

**One filter builder.** A core entity takes the filters of the list routes,
through the same ``records.ListParams`` and ``records.list_query``. So an
aggregate counts exactly the rows that the list with the same filters shows.
Do not copy a filter here. Fence:
``tests/unit/test_crm_aggregate.py::test_the_aggregate_reuses_the_list_filter_builder``.
A module takes only the field filters ``field.<key>=<value>`` and
``field.<key>.from`` / ``.to``.

**The field SQL lives in ``field_sql.py``.** Each field name is checked
against the field definitions there, and every key and value is bound. This
module holds no jsonb operator. Fence:
``tests/unit/test_crm_aggregate.py::test_the_aggregate_holds_no_field_sql``.

**Fixed SQL.** Every identifier in a statement comes from a literal of this
module or of ``field_sql``, and every caller value is a bound parameter. The
statement runs under a LOCAL ``statement_timeout`` of 10 s.

**At most 50 groups.** The answer keeps the 50 largest groups. ``other`` holds
the measure over the rest, and ``omitted`` counts them. The order stays: the
status groups in board order, the month and date buckets in time order, and
each other group largest first.

**Weighted ₹ reuses ``core.WEIGHTED_SQL``.** That expression binds the lane's
own probability as ``:stage_probability``, so the weighted measure runs one
grouped statement for each open or ongoing deal lane and adds the results.
The deal's own probability wins, and the stage probability fills a NULL. That
is the effective value of today. CRM-C3 changes the stage half.

**Stage dwell reads ``crm_status_changes``.** A Mirror org has no such rows,
because the sync writes none. So in Mirror mode the route answers an empty
result with a ``reason``, and never a number that looks measured.

**Two reads for My Day** (WS-58 MD-3e). ``my_overdue_deals`` and
``my_close_passed_deals`` are not routes. The feed's ``crm`` provider calls
them with its own session, after the kill switch and the access rule. Each
reads only the member's own deals, and each statement names the tenant.
Fence: ``tests/unit/test_feed_crm_kinds_r8.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException, Query, Request
from gateway.routes.crm.core import (
    CLOSING_TYPES,
    ENTITIES,
    MAX_PAGE_SIZE,
    WEIGHTED_SQL,
    WEIGHTED_TYPES,
    Entity,
    _tenant_session,
    current_tenant,
    is_mirror_mode,
    router,
)
from gateway.routes.crm.field_sql import (
    DATE_TYPES,
    FIELD_MEASURES,
    FIELD_PREFIX,
    FieldRef,
    FieldScope,
    check_group,
    check_measure,
    core_scope,
    field_pairs,
    group_sql,
    measure_sql,
    module_scope,
    module_where,
)
from gateway.routes.crm.modules import _require_module, mirrored_modules
from gateway.routes.crm.records import ListParams, list_query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

#: ``measure`` → the SQL aggregate. ``weighted`` is not here: it reuses
#: ``core.WEIGHTED_SQL`` lane by lane (see the module doc). The field measures
#: are ``field_sql.FIELD_MEASURES``.
MEASURES: dict[str, str] = {
    "count": "count(*)",
    "sum_amount": "COALESCE(SUM(amount), 0)",
    "weighted": "",
}

#: The measures that read ``amount``, which only a deal has.
DEAL_MEASURES = frozenset({"sum_amount", "weighted"})

#: The measures whose groups add up to the total.
SUMMING_MEASURES = frozenset({"count", "sum_amount", "weighted", "sum"})

#: ``group_by`` → the SQL key. Each value is a literal of this module.
GROUPS: dict[str, str] = {
    "status": "status_id",
    "owner": "lower(owner_email)",
    "pipeline": "pipeline_id",
    "source": "source",
    "created_month": "to_char(date_trunc('month', created_at), 'YYYY-MM')",
    # The provider's modified time when the row has one, as the Q8 filter.
    "updated_month": (
        "to_char(date_trunc('month', coalesce(ext_modified_at, updated_at)), "
        "'YYYY-MM')"
    ),
    "close_month": (
        "to_char(date_trunc('month', expected_close_date), 'YYYY-MM')"
    ),
}

#: ``group_by`` on a generic module (CRM-Q25). A module record keeps Zoho's
#: created time in ``ext_created_at``, so its created month is a real one.
#: The owner is the member email of the Zoho owner, NULL for a non-member.
MODULE_GROUPS: dict[str, str] = {
    "owner": "lower(owner_email)",
    "created_month": "to_char(date_trunc('month', ext_created_at), 'YYYY-MM')",
    "updated_month": (
        "to_char(date_trunc('month', coalesce(ext_modified_at, updated_at)), "
        "'YYYY-MM')"
    ),
}

#: The table of every generic module record.
MODULE_TABLE = "crm_module_records"

#: The entity word of a generic module: ``module:<ApiName>``.
MODULE_PREFIX = "module:"

#: The query parameters a module aggregate takes, beside ``field.<key>``. A
#: list filter on a module is a 422, never ignored.
MODULE_PARAMS = frozenset({
    "entity", "measure", "group_by", "group_field", "field", "bucket",
    "page", "page_size", "sort", "dir",
})

#: The groups that only a deal has.
DEAL_GROUPS = frozenset({"pipeline", "close_month"})

#: The month groups sort by month. Every other group sorts by value.
MONTH_GROUPS = frozenset({"created_month", "updated_month", "close_month"})

#: The most groups one answer holds. The rest fold into ``other``.
MAX_GROUPS = 50

#: The limit of each statement of the aggregate, in milliseconds (CRM-Q25).
STATEMENT_TIMEOUT_MS = 10_000

#: ``SET LOCAL``: it ends with the read's transaction. ``set_config`` takes a
#: bind, and ``SET`` does not (``shell/feed_measure.py``).
_TIMEOUT_SQL = "SELECT set_config('statement_timeout', :ms, true)"

#: The SQLSTATE of a statement that the timeout cancelled.
_QUERY_CANCELED = "57014"

#: What a member reads when the statement runs past the timeout.
TIMEOUT_DETAIL = {
    "code": "aggregate_timeout",
    "message": "The total took longer than 10 seconds. Narrow the filter.",
}

#: What a Mirror org gets from the dwell route.
MIRROR_DWELL_REASON = (
    "This CRM mirrors Zoho, and the sync records no stage changes. "
    "So the days in each stage are not known here."
)


class AggregateGroup(BaseModel):
    #: The raw key: a status id, an email, a month (YYYY-MM), a quarter
    #: (YYYY-Qn), a year, a field value or a lookup id.
    key: str | None
    #: What a person calls it: the stage, pipeline, option or lookup name.
    label: str
    #: None when no record of the group holds a value (``avg``, ``min``, ``max``).
    value: float | None


class AggregateResponse(BaseModel):
    entity: str
    measure: str
    #: The fixed key, or None when the answer groups by ``group_field``.
    group_by: str | None = None
    group_field: str | None = None
    #: The field of a ``sum``, ``avg``, ``min`` or ``max``, and its type.
    field: str | None = None
    field_type: str | None = None
    bucket: str | None = None
    groups: list[AggregateGroup]
    #: The measure over every group, the omitted ones too.
    total: float | None
    #: The measure over the groups past :data:`MAX_GROUPS`.
    other: float | None = 0
    #: The count of groups past :data:`MAX_GROUPS`.
    omitted: int = 0


class StageDwell(BaseModel):
    status_id: str
    name: str
    type: str
    max_dwell_days: int | None = None
    #: Measured departures from this stage. None of the numbers below is a
    #: number without it.
    samples: int
    median_days: float | None = None
    p90_days: float | None = None


class OverdueDeal(BaseModel):
    id: str
    name: str
    stage: str
    owner_email: str | None = None
    amount: float | None = None
    currency: str | None = None
    days_in_stage: float
    limit_days: int


class StageDwellResponse(BaseModel):
    stages: list[StageDwell]
    overdue: list[OverdueDeal]
    overdue_total: int
    #: Set when the numbers are not known, for example in Mirror mode.
    reason: str | None = None


def _refuse(detail: str) -> HTTPException:
    return HTTPException(status_code=422, detail=detail)


def _check(
    entity: str, measure: str, group_by: str | None, group_field: str | None,
    field: str | None, bucket: str | None,
) -> tuple[Entity | None, str | None]:
    """The core entity or the module name, or a 422 for an unknown or unfit
    value. A field name is checked later, against the field definitions."""
    module: str | None = None
    found: Entity | None = None
    if entity.startswith(MODULE_PREFIX):
        module = entity[len(MODULE_PREFIX):]
        if not module:
            raise _refuse("Name the module: 'module:<ApiName>'.")
    else:
        found = ENTITIES.get(entity)
        if found is None:
            raise _refuse(
                f"Unknown entity '{entity}'. One of: {sorted(ENTITIES)}, "
                "or 'module:<ApiName>'.",
            )
    if measure not in MEASURES and measure not in FIELD_MEASURES:
        raise _refuse(
            f"Unknown measure '{measure}'. One of: "
            f"{[*MEASURES, *FIELD_MEASURES]}.",
        )
    if measure in FIELD_MEASURES and not field:
        raise _refuse(f"'{measure}' needs a number field, given as field=<key>.")
    if field and measure not in FIELD_MEASURES:
        raise _refuse(
            f"'field' goes with {list(FIELD_MEASURES)}, not with '{measure}'.",
        )
    if (group_by is None) == (group_field is None):
        raise _refuse("Give group_by or group_field, and only one of them.")
    if bucket is not None and group_field is None:
        raise _refuse("'bucket' goes with group_field.")
    allowed = MODULE_GROUPS if module is not None else GROUPS
    if group_by is not None and group_by not in allowed:
        raise _refuse(f"Unknown group_by '{group_by}'. One of: {list(allowed)}.")
    if module is not None:
        if measure in DEAL_MEASURES:
            raise _refuse(f"'{measure}' needs deals. A module has no amount.")
        return None, module
    assert found is not None
    if found.table != "crm_deals" and (
        measure in DEAL_MEASURES or group_by in DEAL_GROUPS
    ):
        raise _refuse(
            f"'{measure}' by '{group_by}' needs deals. "
            f"{entity} have no amount, pipeline or close date.",
        )
    if group_by == "status" and not found.status_table:
        raise _refuse(f"{entity} have no status pipeline. Group by another key.")
    return found, None


def _check_filters(module: str | None, request: Request) -> list[tuple[str, str]]:
    """The ``field.<key>`` filters. A core entity takes none, and a module
    takes nothing else. The names are checked against the fields later."""
    pairs = field_pairs(request.query_params)
    if module is None:
        if pairs:
            raise _refuse(
                "A field.<key> filter takes a module entity. A core entity "
                "takes the list filters.",
            )
        return pairs
    other = sorted({
        k for k in request.query_params
        if k not in MODULE_PARAMS and not k.startswith(FIELD_PREFIX)
    })
    if other:
        raise _refuse(
            f"A module aggregate takes field.<key> filters only, not {other}.",
        )
    return pairs


def _and(where: str, clause: str) -> str:
    """*where* with one more clause. Both are SQL of this package."""
    return f"{where} AND {clause}" if where else f" WHERE {clause}"


async def _labels(db: Any, entity: Entity, group_by: str) -> dict[str, str]:
    """Key → name for the status and pipeline groups."""
    if group_by == "status" and entity.status_table:
        rows = (await db.execute(
            text(f"SELECT id, name FROM {entity.status_table}"), {},
        )).fetchall()
        return {str(r.id): str(r.name) for r in rows}
    if group_by == "pipeline":
        rows = (await db.execute(
            text("SELECT id, name FROM crm_pipelines"), {},
        )).fetchall()
        return {str(r.id): str(r.name) for r in rows}
    return {}


async def _status_order(db: Any, entity: Entity) -> dict[str, int]:
    if not entity.status_table:
        return {}
    rows = (await db.execute(
        text(f"SELECT id, position FROM {entity.status_table}"), {},
    )).fetchall()
    return {str(r.id): int(r.position or 0) for r in rows}


@dataclass
class _Acc:
    """One group as the statement gave it: the value, the field's sum and
    count of values (for an ``avg`` total), and a name."""

    value: float | None
    s: float = 0.0
    n: int = 0
    name: str | None = None


def _key(value: Any) -> str | None:
    return None if value is None else str(value)


async def _weighted(
    db: Any, key_sql: str, label_sql: str, where: str, params: dict[str, Any],
) -> dict[str | None, _Acc]:
    """Weighted ₹ for each key, one statement per open or ongoing lane."""
    lanes = (await db.execute(
        text("SELECT id, type, probability FROM crm_deal_statuses"), {},
    )).fetchall()
    out: dict[str | None, _Acc] = {}
    for lane in lanes:
        if getattr(lane, "type", "open") not in WEIGHTED_TYPES:
            continue
        rows = (await db.execute(
            text(
                f"SELECT {key_sql} AS key, {label_sql} AS label, {WEIGHTED_SQL} "
                f"FROM crm_deals{_and(where, 'status_id = CAST(:agg_lane AS uuid)')} "
                "GROUP BY 1"
            ),
            {
                **params,
                "agg_lane": str(lane.id),
                "stage_probability": int(getattr(lane, "probability", 0) or 0),
            },
        )).fetchall()
        for row in rows:
            acc = out.setdefault(_key(row.key), _Acc(value=0.0))
            acc.value = (acc.value or 0.0) + float(row.weighted or 0)
            acc.name = acc.name or getattr(row, "label", None)
    return out


def _combine(measure: str, accs: list[_Acc]) -> float | None:
    """The measure over *accs*: a sum, the mean of every value, or the least
    or greatest group value."""
    if measure in SUMMING_MEASURES:
        return sum(a.value or 0.0 for a in accs)
    if measure == "avg":
        n = sum(a.n for a in accs)
        return sum(a.s for a in accs) / n if n else None
    values = [a.value for a in accs if a.value is not None]
    if not values:
        return None
    return min(values) if measure == "min" else max(values)


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(value, 2)


@dataclass
class _Read:
    """What the statements of one aggregate gave."""

    values: dict[str | None, _Acc]
    labels: dict[str, str]
    order: dict[str, int]
    measured: FieldRef | None
    grouped: FieldRef | None


def _is_timeout(exc: DBAPIError) -> bool:
    """True when the LOCAL ``statement_timeout`` cancelled the statement."""
    orig = getattr(exc, "orig", None)
    code = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    return code == _QUERY_CANCELED or "statement timeout" in str(exc)


async def _read(
    db: Any, found: Entity | None, module: str | None, *,
    measure: str, group_by: str | None, field: str | None,
    group_field: str | None, bucket: str | None,
    pairs: list[tuple[str, str]], params: ListParams, user: UserContext,
) -> _Read:
    """Every statement of one aggregate, in the caller's tenant session."""
    await db.execute(text(_TIMEOUT_SQL), {"ms": str(STATEMENT_TIMEOUT_MS)})
    scope: FieldScope | None = None
    if module is not None:
        _require_module(await mirrored_modules(db), module)
        scope = await module_scope(db, module)
        where = module_where(scope, module, pairs)
        table, where_sql, bound = MODULE_TABLE, where.sql, dict(where.params)
    else:
        assert found is not None
        if group_by == "created_month" and await is_mirror_mode(db, current_tenant()):
            # The created date of a mirrored row is the import time (Q8).
            raise HTTPException(status_code=422, detail={
                "code": "created_range_mirror",
                "message": "The created date of a synced record is not kept. "
                           "Group by the updated month instead.",
            })
        query = await list_query(db, found, params, user)
        table, where_sql, bound = found.table, query.where, dict(query.params)
        if field or group_field:
            scope = await core_scope(db, found.slug)
    measured = check_measure(scope, field) if field and scope else None
    grouped = check_group(scope, group_field, bucket) if group_field and scope else None
    if grouped is not None and scope is not None:
        group = group_sql(scope, grouped, bucket)
        key_sql, label_sql = group.key, group.label
        bound.update(group.params)
    else:
        key_sql = (MODULE_GROUPS if module is not None else GROUPS)[group_by or ""]
        label_sql = "NULL"
    if measure == "weighted":
        values = await _weighted(db, key_sql, label_sql, where_sql, bound)
    else:
        value_sql, sum_sql, count_sql = MEASURES.get(measure, ""), "0", "0"
        if measured is not None and scope is not None:
            sql = measure_sql(scope, measured, measure)
            value_sql, sum_sql, count_sql = sql.value, sql.sum, sql.count
            bound.update(sql.params)
        rows = (await db.execute(
            text(
                f"SELECT {key_sql} AS key, {label_sql} AS label, "
                f"{value_sql} AS value, {sum_sql} AS s, {count_sql} AS n "
                f"FROM {table}{where_sql} GROUP BY 1"
            ),
            bound,
        )).fetchall()
        values = {
            _key(r.key): _Acc(
                value=None if r.value is None else float(r.value),
                s=float(r.s or 0), n=int(r.n or 0), name=r.label,
            )
            for r in rows
        }
    labels = await _labels(db, found, group_by) if found and group_by else {}
    order = (
        await _status_order(db, found) if found and group_by == "status" else {}
    )
    return _Read(values, labels, order, measured, grouped)


@router.get("/aggregate", response_model=AggregateResponse)
async def aggregate(
    request: Request,
    entity: str,
    group_by: str | None = None,
    measure: str = "count",
    field: str | None = None,
    group_field: str | None = None,
    bucket: str | None = None,
    params: ListParams = Depends(),
    user: UserContext = Depends(get_current_user),
) -> AggregateResponse:
    """One measure of one entity or module, by one group, over the filters."""
    found, module = _check(entity, measure, group_by, group_field, field, bucket)
    pairs = _check_filters(module, request)
    try:
        async with _tenant_session() as db:
            read = await _read(
                db, found, module, measure=measure, group_by=group_by,
                field=field, group_field=group_field, bucket=bucket,
                pairs=pairs, params=params, user=user,
            )
    except DBAPIError as exc:
        if _is_timeout(exc):
            raise HTTPException(status_code=503, detail=TIMEOUT_DETAIL) from None
        raise
    measured, grouped = read.measured, read.grouped
    bucketed = grouped is not None and grouped.type in DATE_TYPES
    options = grouped.options if grouped is not None else {}
    groups = [
        (
            AggregateGroup(
                key=key,
                label=(
                    read.labels.get(key or "") or acc.name or options.get(key or "")
                    or key or "(none)"
                ),
                value=_rounded(acc.value),
            ),
            acc,
        )
        for key, acc in read.values.items()
    ]
    # The 50 largest groups stay. The rest fold into `other`.
    groups.sort(key=lambda g: (g[0].value is None, -(g[0].value or 0.0), g[0].label))
    kept, rest = groups[:MAX_GROUPS], groups[MAX_GROUPS:]
    shown = [g for g, _acc in kept]
    if group_by == "status":
        shown.sort(key=lambda g: (read.order.get(g.key or "", 1 << 30), g.label))
    elif bucketed or group_by in MONTH_GROUPS:
        shown.sort(key=lambda g: (g.key is None, g.key or ""))
    return AggregateResponse(
        entity=found.slug if found is not None else f"{MODULE_PREFIX}{module}",
        measure=measure, group_by=group_by, group_field=group_field,
        field=measured.key if measured is not None else None,
        field_type=measured.type if measured is not None else None,
        bucket=bucket if bucketed else None,
        groups=shown,
        total=_rounded(_combine(measure, [acc for _g, acc in groups])),
        other=_rounded(_combine(measure, [acc for _g, acc in rest])) if rest else 0,
        omitted=len(rest),
    )


# ── Stage dwell (the read half of CRM-I1) ───────────────────────────────────

#: Median and 90th percentile of the measured departures from each stage.
#: The log stores status NAMES (``reports.py`` fact 2), so the join is by name.
_DWELL_SQL = (
    "SELECT from_status AS name, count(*) AS samples, "
    "percentile_cont(0.5) WITHIN GROUP (ORDER BY dwell_seconds) AS p50, "
    "percentile_cont(0.9) WITHIN GROUP (ORDER BY dwell_seconds) AS p90 "
    "FROM crm_status_changes "
    "WHERE entity_type = 'deal' AND from_status IS NOT NULL "
    "AND dwell_seconds IS NOT NULL GROUP BY from_status"
)

#: The open deals past their limit. ``:days`` replaces each stage's
#: ``max_dwell_days`` when the caller gives it. A stage with no limit, and a
#: deal with no stage clock, is never overdue (``board.ts`` rule).
_OVERDUE_FROM = (
    "FROM crm_deals d JOIN crm_deal_statuses s ON s.id = d.status_id "
    "WHERE s.type <> ALL(CAST(:closing AS text[])) "
    "AND d.status_changed_at IS NOT NULL "
    "AND COALESCE(CAST(:days AS int), CAST(s.max_dwell_days AS int)) IS NOT NULL "
    "AND d.status_changed_at < now() - make_interval(days => "
    "COALESCE(CAST(:days AS int), CAST(s.max_dwell_days AS int)))"
)

_OVERDUE_SQL = (
    "SELECT d.id, d.name, d.owner_email, d.amount, d.currency, "
    "s.name AS stage, "
    "EXTRACT(EPOCH FROM (now() - d.status_changed_at)) / 86400.0 AS days_in, "
    "COALESCE(CAST(:days AS int), CAST(s.max_dwell_days AS int)) AS limit_days "
    f"{_OVERDUE_FROM} "
    "ORDER BY d.status_changed_at ASC, d.id LIMIT :limit OFFSET :offset"
)

_SECONDS_PER_DAY = 86_400.0


# ── My deals, for the My Day feed (WS-58 MD-3e) ─────────────────────────────
#
# Two reads of the deals that I own, for `deal_quiet` and `deal_close_passed`
# (`my_day_feed.md` §5.9 item 13). The caller gives the session, so the feed's
# three CRM reads share one. ⚠️ Each statement names the bound tenant AND the
# member. RLS holds the gateway role (`acb_app`), so the predicate is defence
# in depth, and an R8 fence proves it on a role that RLS does not hold. "I
# own" is `lower(owner_email)`, which reads
# through `idx_crm_deals_owner_email`. Never `stage_dwell`: that read covers
# the whole organization.

#: The tenant and the owner, for each read below. Both sides of the join name
#: the tenant, so no stage of another organization joins.
_MINE = (
    " AND d.organization_id = CAST(:org AS uuid)"
    " AND s.organization_id = CAST(:org AS uuid)"
    " AND lower(d.owner_email) = :member"
)

#: My open deals past the limit of their own stage. ``_OVERDUE_FROM`` with
#: no ``:days`` override, so a stage with no limit never fires.
_MY_OVERDUE_SQL = (
    "SELECT d.id, d.name, s.name AS stage, d.status_changed_at, "
    "CAST(s.max_dwell_days AS int) AS limit_days "
    f"{_OVERDUE_FROM}{_MINE} "
    "ORDER BY d.status_changed_at ASC, d.id LIMIT :limit"
)

#: My open deals whose expected close date is before my local today.
_MY_CLOSE_PASSED_SQL = (
    "SELECT d.id, d.name, s.name AS stage, d.expected_close_date "
    "FROM crm_deals d JOIN crm_deal_statuses s ON s.id = d.status_id "
    "WHERE s.type <> ALL(CAST(:closing AS text[])) "
    "AND d.expected_close_date < :today"
    f"{_MINE} "
    "ORDER BY d.expected_close_date ASC, d.id LIMIT :limit"
)


def _mine(user: UserContext) -> dict[str, Any] | None:
    """The binds of the tenant and the member, or None when one is missing."""
    org = current_tenant()
    member = (getattr(user, "email", None) or "").strip().lower()
    if not org or not member:
        return None
    return {"org": str(org), "member": member, "closing": list(CLOSING_TYPES)}


async def my_overdue_deals(db: Any, user: UserContext, *, limit: int) -> list[dict[str, Any]]:
    """My open deals that passed the ``max_dwell_days`` of their own stage,
    the longest in the stage first. Empty in Mirror mode, because the sync
    writes no stage change, so the stage clock is not known (the reason of
    ``stage_dwell``). Each row is ``{id, name, stage, status_changed_at,
    limit_days}``."""
    binds = _mine(user)
    if binds is None or await is_mirror_mode(db, binds["org"]):
        return []
    rows = (await db.execute(text(_MY_OVERDUE_SQL), {
        **binds, "days": None, "limit": max(1, int(limit)),
    })).fetchall()
    return [{"id": str(r.id), "name": r.name, "stage": r.stage,
             "status_changed_at": r.status_changed_at, "limit_days": int(r.limit_days)}
            for r in rows]


async def my_close_passed_deals(
    db: Any, user: UserContext, *, today: Any, limit: int,
) -> list[dict[str, Any]]:
    """My open deals whose ``expected_close_date`` is before ``today``, the
    member's local date. A won or lost deal is closed, so it never returns.
    Each row is ``{id, name, stage, expected_close_date}``."""
    binds = _mine(user)
    if binds is None:
        return []
    rows = (await db.execute(text(_MY_CLOSE_PASSED_SQL), {
        **binds, "today": today, "limit": max(1, int(limit)),
    })).fetchall()
    return [{"id": str(r.id), "name": r.name, "stage": r.stage,
             "expected_close_date": r.expected_close_date} for r in rows]


async def my_day_rows(user: UserContext, *, now: datetime, limit: int) -> dict[str, Any]:
    """The rows of the feed's ``crm`` source, in ONE tenant session (WS-58
    MD-3e, ``my_day_feed.md`` §5.9 item 13): the member's zone, then my due
    follow-ups, my quiet deals and my deals past their close date.

    ``now`` is the feed's one clock. The zone comes from
    ``personal.stored_zone``, so "today" is the member's own, and the
    follow-ups follow the one CRM day rule (``activities._MY_DUE_SQL``). The caller has applied the kill switch and the access rule
    before it calls this. Each read takes ``limit`` rows at most.
    """
    from gateway.routes.crm import activities
    from gateway.routes.projects.personal import stored_zone, zone_name

    async with _tenant_session() as db:
        zone = zone_name(await stored_zone(db, user.email))
        tz = ZoneInfo(zone)
        today = now.astimezone(tz).date()
        return {
            "zone": zone,
            "today": today,
            "due": await activities.my_due_activities(db, user, today=today, tz=tz,
                                                      limit=limit),
            "quiet": await my_overdue_deals(db, user, limit=limit),
            "passed": await my_close_passed_deals(db, user, today=today, limit=limit),
        }


def _days(seconds: Any) -> float | None:
    return None if seconds is None else round(float(seconds) / _SECONDS_PER_DAY, 1)


@router.get("/aggregate/stage-dwell", response_model=StageDwellResponse)
async def stage_dwell(
    days: int | None = Query(None, ge=1, le=32767),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=MAX_PAGE_SIZE),
    user: UserContext = Depends(get_current_user),
) -> StageDwellResponse:
    """Days in each deal stage, and the open deals past their limit.

    ``days`` replaces the limit of each stage (``max_dwell_days``). With no
    ``days``, a stage with no limit lists no deal.
    """
    async with _tenant_session() as db:
        if await is_mirror_mode(db, current_tenant()):
            return StageDwellResponse(
                stages=[], overdue=[], overdue_total=0,
                reason=MIRROR_DWELL_REASON,
            )
        lanes = (await db.execute(
            text("SELECT * FROM crm_deal_statuses ORDER BY position, name"), {},
        )).fetchall()
        measured = {
            str(r.name): r
            for r in (await db.execute(text(_DWELL_SQL), {})).fetchall()
        }
        bound = {"days": days, "closing": list(CLOSING_TYPES)}
        total = (await db.execute(
            text(f"SELECT count(*) {_OVERDUE_FROM}"), bound,
        )).scalar() or 0
        rows = (await db.execute(text(_OVERDUE_SQL), {
            **bound, "limit": page_size, "offset": (page - 1) * page_size,
        })).fetchall()
    stages = []
    for lane in lanes:
        seen = measured.get(str(lane.name))
        stages.append(StageDwell(
            status_id=str(lane.id), name=str(lane.name),
            type=str(getattr(lane, "type", "open") or "open"),
            max_dwell_days=getattr(lane, "max_dwell_days", None),
            samples=int(seen.samples) if seen is not None else 0,
            median_days=_days(seen.p50) if seen is not None else None,
            p90_days=_days(seen.p90) if seen is not None else None,
        ))
    return StageDwellResponse(
        stages=stages,
        overdue=[
            OverdueDeal(
                id=str(r.id), name=str(r.name), stage=str(r.stage),
                owner_email=r.owner_email,
                amount=None if r.amount is None else float(r.amount),
                currency=r.currency,
                days_in_stage=round(float(r.days_in or 0), 1),
                limit_days=int(r.limit_days),
            )
            for r in rows
        ],
        overdue_total=int(total),
    )
