"""Projects · the personal lens — planning my day out of the ONE task store.

Spec: ``calendar_focus_os.md`` §10 · ``task_manager_app.md`` §13 · **D53/D54** ·
board **WS-39 slice S3a-client**.

    POST /projects/my/calendar/plan            → rebuild my day
    POST /projects/my/calendar/replan          → fit what is left
    POST /projects/my/calendar/rollover        → release yesterday's leftovers
    GET  /projects/my/calendar/estimate-stats  → planned-vs-actual signal

**None of these writes anything.** Each returns a PROPOSAL the client reviews
and then applies through the ordinary overlay PATCH — which is why the apply
path needed no work here: slice 1 already routed it.

⚠️ **This module deliberately contains no planner.** The packer, the LLM
ranker, the horizon parser, the capacity arithmetic and the eviction rules all
live in ``routes/tasks/calendar.py`` and are called from here unchanged. That
module is not "the old app" — ``task_manager_app.md`` §13.3 is explicit that the
calendar routes are NOT superseded by D53; they move to the Calendar app rather
than being deleted. Re-deriving the packer against ``pm_*`` would be a second
implementation of the one piece of this system where the behaviour actually
lives, and the two would drift on the first bug fix that only one of them got.

What this module DOES own is the answer to "which rows", for the new store:
``_LensSource`` below. It sits here rather than beside its sibling in
``routes/tasks/calendar.py`` because it needs ``MY_TASKS_FROM``,
``derive_disposition`` and ``resolve_organization_id`` from this package — and
this package imports that module. Defining it there would close an import cycle
whose failure depends on which package a process loads first.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends
from gateway.routes.projects.core import resolve_organization_id, router
from gateway.routes.projects.personal import MY_TASKS_FROM, derive_disposition
from gateway.routes.tasks.calendar import (
    DayPlan,
    PlanDayRequest,
    TaskSource,
    estimate_stats_for,
    plan_day_for,
    replan_day_for,
    rollover_day_for,
)
from sqlalchemy import text

# ── The one store ───────────────────────────────────────────────────────────

#: The planner's row shape over `pm_tasks` + `pm_task_personal`.
#:
#: Composed onto `MY_TASKS_FROM` — the membership + tenancy skeleton the personal
#: inbox uses — so "which tasks are mine" has exactly one definition. Aliased
#: BARE (`scheduled_start`, not `p_scheduled_start`) because the packer reads
#: these names off the row directly; there is no collision, since none of the
#: overlay's columns exists on `pm_tasks`.
_PM_SELECT = """
SELECT t.id::text                AS id,
       t.title,
       t.description             AS notes,
       t.due_at,
       t.project_id::text        AS project_id,
       t.parent_task_id,
       s.category                AS status_category,
       p.disposition             AS stated_disposition,
       p.next_action, p.context, p.energy, p.time_estimate_mins,
       p.scheduled_start, p.scheduled_end, p.flexible, p.is_hard_date,
       p.actual_start, p.actual_end,
       p.important, p.leveraged, p.deep_work, p.kept_mine, p.sort_key,
       (SELECT count(*) FROM pm_task_assignees a2 WHERE a2.task_id = t.id)
                                 AS assignee_count,
       EXISTS (SELECT 1 FROM pm_task_assignees a3
               WHERE a3.task_id = t.id AND lower(a3.assignee) = :who)
                                 AS is_mine
"""

#: ⚠️ Every predicate below prunes with the STATED disposition and never decides
#: with it. `(p.disposition IS NULL OR …)` keeps untriaged rows in the result so
#: `derive_disposition` can rule on them in Python; the `NOT IN` half throws away
#: rows whose stated value already settles it. That is a cheap index-friendly
#: filter over a question SQL cannot answer, NOT a second copy of the rule — and
#: the difference matters, because a SQL copy of `derive_disposition` is a mirror
#: and mirrors go stale and then lie.
_PM_ALIVE = " AND (p.disposition IS NULL OR p.disposition NOT IN ('DONE','TRASH'))"
_PM_MINE = (
    " AND EXISTS (SELECT 1 FROM pm_task_assignees a4"
    "             WHERE a4.task_id = t.id AND lower(a4.assignee) = :who)"
)
_PM_TODAY_WHERE = (
    " AND t.parent_task_id IS NULL"
    " AND (p.disposition IS NULL OR p.disposition <> 'TRASH')"
    " AND p.scheduled_start IS NOT NULL"
    " AND p.scheduled_start >= :day0 AND p.scheduled_start < :day1"
)
_PM_CARRY_WHERE = (
    " AND t.parent_task_id IS NULL" + _PM_ALIVE + _PM_MINE
    + " AND coalesce(p.flexible, true) = true"
    " AND p.scheduled_start IS NOT NULL AND p.scheduled_start < :day0"
)
_PM_CANDIDATE_WHERE = (
    " AND t.parent_task_id IS NULL" + _PM_MINE
    + " AND (p.disposition IS NULL OR p.disposition = 'NEXT')"
    " AND p.scheduled_start IS NULL"
)
_PM_OVERDUE_WHERE = (
    " AND t.parent_task_id IS NULL" + _PM_ALIVE
    + " AND coalesce(p.flexible, true) = true"
    " AND p.scheduled_start IS NOT NULL AND p.scheduled_end < :now"
)
_PM_BUSY_WHERE = (
    " AND t.parent_task_id IS NULL" + _PM_ALIVE
    + " AND p.scheduled_start IS NOT NULL"
    " AND p.scheduled_start < :win_end AND p.scheduled_end > :win_start"
)

#: The learned-estimate signal, over the overlay. Same shape as the `gtd_items`
#: query it mirrors, against the columns migration 187 moved.
_PM_RATIO_SQL = """
SELECT count(*) AS n,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY r) AS median_ratio
  FROM (
    SELECT EXTRACT(EPOCH FROM (actual_end - actual_start)) / 60.0
             / NULLIF(planned, 0) AS r
      FROM (
        SELECT actual_start, actual_end,
               COALESCE(
                 EXTRACT(EPOCH FROM (scheduled_end - scheduled_start)) / 60.0,
                 time_estimate_mins) AS planned
          FROM pm_task_personal
         WHERE lower(member_email) = :who
           AND actual_start IS NOT NULL AND actual_end IS NOT NULL
           AND actual_end > actual_start
           AND actual_end > now() - interval '90 days'
      ) s
     WHERE planned > 0
  ) t
"""


def _pm_row(row: Any) -> SimpleNamespace:
    """A `pm_*` row, wearing the names the planner already reads.

    The one substantive translation is `disposition`: STATED where the member
    has triaged, DERIVED otherwise — the same rule `/projects/my/inbox` applies,
    called rather than restated. Everything else is a passthrough, so the packer
    below cannot tell which store it is working on, which is exactly the property
    that keeps the two from drifting.
    """
    stated = getattr(row, "stated_disposition", None)
    return SimpleNamespace(
        **{k: getattr(row, k) for k in _PM_PASSTHROUGH},
        disposition=stated or derive_disposition(
            status_category=str(getattr(row, "status_category", "") or ""),
            is_mine=bool(getattr(row, "is_mine", False)),
            has_assignee=int(getattr(row, "assignee_count", 0) or 0) > 0,
        ),
    )


_PM_PASSTHROUGH = (
    "id", "title", "notes", "due_at", "project_id", "parent_task_id",
    "next_action", "context", "energy", "time_estimate_mins",
    "scheduled_start", "scheduled_end", "flexible", "is_hard_date",
    "actual_start", "actual_end",
    "important", "leveraged", "deep_work", "kept_mine", "sort_key", "is_mine",
)


@dataclass(frozen=True)
class _LensSource(TaskSource):
    """`pm_tasks` + `pm_task_personal` — the one store, D53."""

    async def _rows(self, db, uid, where, params, keep) -> list[Any]:
        """Run one planner query and rule on the rows SQL could not rule on.

        `keep` receives the EFFECTIVE disposition. It exists because the stated
        column cannot answer the question — see `_PM_ALIVE`.
        """
        binds = {
            "who": uid.lower(),
            "vis_org": await resolve_organization_id(db, uid.lower()),
            # A planner never schedules filed work, and there is no view here
            # that would want it to.
            "archived": False,
            **params,
        }
        rows = (await db.execute(
            text(_PM_SELECT + MY_TASKS_FROM + where), binds)).fetchall()
        out = [_pm_row(r) for r in rows]
        return [r for r in out if keep(r.disposition)]

    async def scheduled_today(self, db, uid, day0, day1):
        return await self._rows(
            db, uid, _PM_TODAY_WHERE, {"day0": day0, "day1": day1},
            lambda d: d != "TRASH")

    async def carry_forward(self, db, uid, day0):
        return await self._rows(
            db, uid, _PM_CARRY_WHERE, {"day0": day0},
            lambda d: d not in ("DONE", "TRASH"))

    async def candidates(self, db, uid):
        return await self._rows(
            db, uid, _PM_CANDIDATE_WHERE, {}, lambda d: d == "NEXT")

    async def overdue(self, db, uid, now):
        return await self._rows(
            db, uid, _PM_OVERDUE_WHERE, {"now": now},
            lambda d: d not in ("DONE", "TRASH"))

    async def busy_window(self, db, uid, win_start, win_end):
        return await self._rows(
            db, uid, _PM_BUSY_WHERE,
            {"win_start": win_start, "win_end": win_end},
            lambda d: d not in ("DONE", "TRASH"))

    def to_item(self, row):
        return row

    async def estimate_ratio(self, db, uid):
        row = (await db.execute(
            text(_PM_RATIO_SQL), {"who": uid.lower()})).first()
        n = int(row.n or 0) if row else 0
        ratio = float(row.median_ratio) if row and row.median_ratio else 1.0
        return ratio, n

LENS_SOURCE: TaskSource = _LensSource(name="pm_tasks")


# ── The routes ───────────────────────────────────────────────────
#
# Four thin wrappers, and thin on purpose. The store is chosen by WHICH ROUTE the
# client calls, not by a parameter and not by a server flag — so there is exactly
# one flag in the whole cutover (`NEXT_PUBLIC_TASKS_LENS`, in the browser) and no
# second one that could disagree with it. The old `/tasks/calendar/*` routes stay
# until S3c, serving the retiring store, unchanged.


@router.post("/my/calendar/plan", response_model=DayPlan)
async def my_plan_day(
    req: PlanDayRequest, user: UserContext = Depends(get_current_user),
) -> DayPlan:
    """Rebuild my day, out of `pm_tasks` + `pm_task_personal`."""
    return await plan_day_for(req, user, LENS_SOURCE)


@router.post("/my/calendar/replan", response_model=DayPlan)
async def my_replan_day(
    req: PlanDayRequest, user: UserContext = Depends(get_current_user),
) -> DayPlan:
    """Fit what is left of my day, out of the one store."""
    return await replan_day_for(req, user, LENS_SOURCE)


@router.post("/my/calendar/rollover", response_model=DayPlan)
async def my_rollover_day(
    req: PlanDayRequest, user: UserContext = Depends(get_current_user),
) -> DayPlan:
    """Release yesterday's unfinished blocks back onto my list."""
    return await rollover_day_for(req, user, LENS_SOURCE)


@router.get("/my/calendar/estimate-stats")
async def my_estimate_stats(
    user: UserContext = Depends(get_current_user),
) -> dict:
    """How long my work actually takes against what I planned."""
    return await estimate_stats_for(user, LENS_SOURCE)
