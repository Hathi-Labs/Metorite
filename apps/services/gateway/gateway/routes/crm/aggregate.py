"""CRM · aggregate — totals and stage dwell for an agent (WS-53 CRM-C7).

Spec: ``project-docs/specs/crm_platform.md`` §20.10 item 2.

    GET /crm/aggregate               count, ₹ or weighted ₹, by one group
    GET /crm/aggregate/stage-dwell   days in each deal stage, and the deals
                                     that sit past their limit

**Read only.** Any member that the CRM access rule admits may call them: the
routes sit on the shared ``core.router``, so the kill switch, ``feature:crm``
and the access rule apply. The tenant comes from the session that the router
binds, never from the request (R5).

**One filter builder.** ``GET /crm/aggregate`` takes the filters of the list
routes, through the same ``records.ListParams`` and ``records.list_query``.
So an aggregate counts exactly the rows that the list with the same filters
shows. Do not copy a filter here. Fence:
``tests/unit/test_crm_aggregate.py::test_the_aggregate_reuses_the_list_filter_builder``.

**Fixed SQL.** ``measure`` and ``group_by`` are keys of the two allowlists
below. Any other value is a 422. Every identifier in a statement comes from
a literal of this module, and every caller value is a bound parameter.

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

from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException, Query
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
from gateway.routes.crm.records import ListParams, list_query
from pydantic import BaseModel
from sqlalchemy import text

#: ``measure`` → the SQL aggregate. ``weighted`` is not here: it reuses
#: ``core.WEIGHTED_SQL`` lane by lane (see the module doc).
MEASURES: dict[str, str] = {
    "count": "count(*)",
    "sum_amount": "COALESCE(SUM(amount), 0)",
    "weighted": "",
}

#: The measures that read ``amount``, which only a deal has.
DEAL_MEASURES = frozenset({"sum_amount", "weighted"})

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

#: The groups that only a deal has.
DEAL_GROUPS = frozenset({"pipeline", "close_month"})

#: The month groups sort by month. Every other group sorts by value.
MONTH_GROUPS = frozenset({"created_month", "updated_month", "close_month"})

#: The most groups one answer holds. More are counted in ``omitted``.
MAX_GROUPS = 200

#: What a Mirror org gets from the dwell route.
MIRROR_DWELL_REASON = (
    "This CRM mirrors Zoho, and the sync records no stage changes. "
    "So the days in each stage are not known here."
)


class AggregateGroup(BaseModel):
    #: The raw key: a status id, an email, a month (YYYY-MM) or a source.
    key: str | None
    #: What a person calls it: the stage or pipeline name, else the key.
    label: str
    value: float


class AggregateResponse(BaseModel):
    entity: str
    measure: str
    group_by: str
    groups: list[AggregateGroup]
    #: The sum of every group, the omitted ones too.
    total: float
    #: Groups past :data:`MAX_GROUPS`, counted and not returned.
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


def _check(entity: str, measure: str, group_by: str) -> Entity:
    """The entity, or a 422 for an unknown or unfit value."""
    found = ENTITIES.get(entity)
    if found is None:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown entity '{entity}'. One of: {sorted(ENTITIES)}.",
        )
    if measure not in MEASURES:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown measure '{measure}'. One of: {list(MEASURES)}.",
        )
    if group_by not in GROUPS:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown group_by '{group_by}'. One of: {list(GROUPS)}.",
        )
    if found.table != "crm_deals" and (
        measure in DEAL_MEASURES or group_by in DEAL_GROUPS
    ):
        raise HTTPException(
            status_code=422,
            detail=(
                f"'{measure}' by '{group_by}' needs deals. "
                f"{entity} have no amount, pipeline or close date."
            ),
        )
    if group_by == "status" and not found.status_table:
        raise HTTPException(
            status_code=422,
            detail=f"{entity} have no status pipeline. Group by another key.",
        )
    return found


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


async def _weighted(
    db: Any, key_sql: str, where: str, params: dict[str, Any],
) -> dict[str | None, float]:
    """Weighted ₹ for each key, one statement per open or ongoing lane."""
    lanes = (await db.execute(
        text("SELECT id, type, probability FROM crm_deal_statuses"), {},
    )).fetchall()
    out: dict[str | None, float] = {}
    for lane in lanes:
        if getattr(lane, "type", "open") not in WEIGHTED_TYPES:
            continue
        rows = (await db.execute(
            text(
                f"SELECT {key_sql} AS key, {WEIGHTED_SQL} FROM crm_deals"
                f"{_and(where, 'status_id = CAST(:agg_lane AS uuid)')} "
                "GROUP BY 1"
            ),
            {
                **params,
                "agg_lane": str(lane.id),
                "stage_probability": int(getattr(lane, "probability", 0) or 0),
            },
        )).fetchall()
        for row in rows:
            key = None if row.key is None else str(row.key)
            out[key] = out.get(key, 0.0) + float(row.weighted or 0)
    return out


@router.get("/aggregate", response_model=AggregateResponse)
async def aggregate(
    entity: str,
    group_by: str,
    measure: str = "count",
    params: ListParams = Depends(),
    user: UserContext = Depends(get_current_user),
) -> AggregateResponse:
    """One measure of one entity, by one group, over the list's filters."""
    found = _check(entity, measure, group_by)
    key_sql = GROUPS[group_by]
    async with _tenant_session() as db:
        if group_by == "created_month" and await is_mirror_mode(
            db, current_tenant(),
        ):
            # The created date of a mirrored row is the import time (Q8).
            raise HTTPException(status_code=422, detail={
                "code": "created_range_mirror",
                "message": "The created date of a synced record is not kept. "
                           "Group by the updated month instead.",
            })
        query = await list_query(db, found, params, user)
        if measure == "weighted":
            values = await _weighted(db, key_sql, query.where, query.params)
        else:
            rows = (await db.execute(
                text(
                    f"SELECT {key_sql} AS key, {MEASURES[measure]} AS value "
                    f"FROM {found.table}{query.where} GROUP BY 1"
                ),
                query.params,
            )).fetchall()
            values = {
                (None if r.key is None else str(r.key)): float(r.value or 0)
                for r in rows
            }
        labels = await _labels(db, found, group_by)
        order = (
            await _status_order(db, found) if group_by == "status" else {}
        )
    groups = [
        AggregateGroup(
            key=key,
            label=labels.get(key or "", key or "(none)"),
            value=round(value, 2),
        )
        for key, value in values.items()
    ]
    if group_by in MONTH_GROUPS:
        groups.sort(key=lambda g: (g.key is None, g.key or ""))
    elif group_by == "status":
        groups.sort(key=lambda g: (order.get(g.key or "", 1 << 30), g.label))
    else:
        groups.sort(key=lambda g: (-g.value, g.label))
    return AggregateResponse(
        entity=found.slug, measure=measure, group_by=group_by,
        groups=groups[:MAX_GROUPS],
        total=round(sum(values.values()), 2),
        omitted=max(0, len(groups) - MAX_GROUPS),
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
# member, because production connects as the role that owns the tables, and
# RLS does not hold that role. "I own" is `lower(owner_email)`, which reads
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
    ``personal.stored_zone``, so "today" and "the start of tomorrow" are the
    member's own. The caller has applied the kill switch and the access rule
    before it calls this. Each read takes ``limit`` rows at most.
    """
    from gateway.routes.crm import activities
    from gateway.routes.projects.personal import stored_zone, zone_name

    async with _tenant_session() as db:
        zone = zone_name(await stored_zone(db, user.email))
        tz = ZoneInfo(zone)
        today = now.astimezone(tz).date()
        tomorrow0 = datetime.combine(today + timedelta(days=1), time.min, tzinfo=tz)
        return {
            "zone": zone,
            "today": today,
            "due": await activities.my_due_activities(db, user, before=tomorrow0, limit=limit),
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
