"""Projects · rebalancing — who could help whom, in one scope (WS-27bm S7b).

Spec: ``project-docs/specs/projects_ai_chat.md`` §13.2, §13.4 rule 6, §10.4
item 9.

    GET /projects/analytics/rebalance?project_id=&include_subtree=&horizon_days=

The People suggester (``/people/dashboard/suggestions``), scoped to a project
subtree. Two lists:

* ``at_risk`` — the at-risk tasks IN SCOPE, each with up to three helpers
  who fit it.
* ``pickups`` — the idle people, each with the unassigned tasks in scope that
  fit them, and the at-risk tasks where they are a listed helper.

**The join is not here.** It is :func:`gateway.capacity.rebalance_join`,
which the People suggester calls too. This module fetches the rows and
chooses the ranker, which is :func:`~gateway.routes.projects.candidates.rank_for_text`
over ``rank_candidates``, so §13.4 rule 2 holds here as it does for one task.

Where each list comes from:

* The people, their hours and their pills are
  :func:`~gateway.routes.projects.candidates.pool_capacity`: every open task
  the caller can see. The helpers and the idle people are its ACTIVE rows
  (the rule 4 pool), so an idle person with no work in this scope still
  appears. A person is idle when ``person_capacity`` gives the pill ``idle``.
* The at-risk tasks come from the same walk, and only the ones in this scope
  stay. A task outside the scope, or outside the caller's grant, never
  appears.
* The unassigned tasks come from Load's predicate over this scope
  (``analytics.load_open_where``).

⚠️ **No HR grant, no lists** (§13.4 rule 1). A caller without
``admin:members:read`` gets 200, ``hr_visible: false`` and neither an
``at_risk`` nor a ``pickups`` key. A helper list names who holds which skill.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends
from gateway.capacity import horizon_window, rebalance_join
from gateway.routes.projects.analytics import (
    load_open_where,
    load_params,
    scope_clause,
)
from gateway.routes.projects.analytics_capacity import check_horizon
from gateway.routes.projects.candidates import (
    DESCRIPTION_CHARS,
    match_text,
    pool_capacity,
    rank_for_text,
)
from gateway.routes.projects.core import (
    _tenant_session,
    resolve_visibility,
    router,
)
from gateway.routes.tasks.core import can_read_hr_fields
from gateway.workload import HORIZON_DAYS
from sqlalchemy import text


def scoped_tasks_sql(open_where: str) -> str:
    """The open tasks in scope among ``:ids``, with their match text.

    The at-risk walk reads every open task the caller can see. This keeps the
    ones in THIS scope, through Load's own predicate, and carries the tags
    and the description that the match text needs (§13.4 rule 3).
    """
    return (
        f"SELECT t.id, t.tags, left(coalesce(t.description, ''), {DESCRIPTION_CHARS})"
        f"       AS description"
        f"  FROM pm_tasks t"
        f"  JOIN pm_task_statuses s ON s.id = t.status_id"
        f" WHERE {open_where}"
        f"   AND t.id = ANY(CAST(:ids AS uuid[]))"
    )


def unassigned_sql(open_where: str) -> str:
    """Open tasks in scope that nobody holds, soonest first, capped."""
    return (
        f"SELECT t.id, t.title, t.tags,"
        f"       left(coalesce(t.description, ''), {DESCRIPTION_CHARS}) AS description,"
        f"       p.name AS project_name"
        f"  FROM pm_tasks t"
        f"  JOIN pm_task_statuses s ON s.id = t.status_id"
        f"  LEFT JOIN pm_projects p ON p.id = t.project_id"
        f" WHERE {open_where}"
        f"   AND NOT EXISTS (SELECT 1 FROM pm_task_assignees a"
        f"                    WHERE a.task_id = t.id)"
        f" ORDER BY t.due_at NULLS LAST, t.id"
        f" LIMIT :cap"
    )


@router.get("/analytics/rebalance")
async def rebalance(
    project_id: str | None = None,
    include_subtree: bool = True,
    horizon_days: int = HORIZON_DAYS,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """At-risk tasks with their helpers, and idle people with work to pick up.

    Omit ``project_id`` for the portfolio. ``horizon_days`` (1 to 90, default
    14) sets the spare-hours and at-risk window, and the response prints it.
    """
    days = check_horizon(horizon_days)
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        return await rebalance_body(
            db, vis,
            hr_visible=can_read_hr_fields(user),
            project_id=project_id,
            include_subtree=include_subtree,
            horizon_days=days,
        )


async def rebalance_body(
    db: Any,
    vis: Any,
    *,
    hr_visible: bool,
    project_id: str | None,
    include_subtree: bool,
    horizon_days: int = HORIZON_DAYS,
    today: date | None = None,
) -> dict[str, Any]:
    """The rebalance answer, on a session and a visibility the caller resolved."""
    from gateway.routes.people.suggestions import (
        MAX_AT_RISK_TASKS,
        MAX_PICKUPS_PER_PERSON,
        MAX_UNASSIGNED_TASKS,
    )

    days = check_horizon(horizon_days)
    today = today or date.today()
    _, horizon_end = horizon_window(today, days)
    # 404 for a node the caller cannot see, before anything else is read.
    scope_sql = await scope_clause(db, vis, project_id, include_subtree)

    body: dict[str, Any] = {
        "project_id": project_id,
        "scope": "portfolio" if project_id is None else "node",
        "include_subtree": include_subtree,
        "horizon_days": days,
        "hr_visible": hr_visible,
        "window": {
            "starts_on": today.isoformat(),
            "ends_on": horizon_end.isoformat(),
            "days": days,
        },
    }
    if not hr_visible:
        return body

    from gateway.work_schedule import absent_on

    people = await pool_capacity(db, vis, today=today, horizon_days=days)
    for person in people:
        # The People suggester's availability: away TODAY. A rebalance is a
        # question about this week, not about one task's due date.
        span = absent_on(today, person["spans"])
        person["away"] = (
            {"kind": span["kind"], "until": span["ends_on"].isoformat()} if span else None
        )
    pool = [p for p in people if p["in_pool"]]

    # ── The at-risk tasks, kept to this scope ──────────────────────────────
    risky: list[dict[str, Any]] = []
    for person in people:
        for task in person["capacity"]["at_risk"]:
            risky.append({
                "task_id": str(task.get("task_id")),
                "title": str(task.get("title") or ""),
                "project_name": task.get("project_name"),
                "due_on": task.get("due_on"),
                "shortfall_hours": task.get("shortfall_hours"),
                "holder": {"person_id": person["person_id"], "name": person["name"],
                           "email": person["email"]},
            })
    scope_where = load_open_where(scope_sql, vis)
    scope_params = load_params(vis, project_id)
    in_scope: dict[str, Any] = {}
    if risky:
        in_scope = {
            str(r.id): r
            for r in (await db.execute(
                text(scoped_tasks_sql(scope_where)),
                {**scope_params, "ids": sorted({t["task_id"] for t in risky})},
            )).fetchall()
        }
    at_risk = [t for t in risky if t["task_id"] in in_scope]
    for task in at_risk:
        row = in_scope[task["task_id"]]
        task["match_text"] = match_text(task["title"], row.tags, row.description)
    # Soonest deadline first, then the bigger shortfall: the cap keeps the
    # tasks that need help first.
    at_risk.sort(key=lambda t: (t["due_on"] or "", -float(t["shortfall_hours"] or 0)))

    # ── The unassigned tasks in scope ──────────────────────────────────────
    unassigned = [
        {
            "task_id": str(r.id), "title": r.title,
            "project_name": getattr(r, "project_name", None),
            "match_text": match_text(r.title, r.tags, r.description),
        }
        for r in (await db.execute(
            text(unassigned_sql(scope_where)),
            {**scope_params, "cap": MAX_UNASSIGNED_TASKS},
        )).fetchall()
    ]

    idle = [p for p in pool if p["capacity"]["pill"] == "idle"]

    def _rank(text_: str, helpers: list[dict[str, Any]], holder: str):
        return rank_for_text(text_, helpers, this_year=today.year, exclude_email=holder)

    joined = rebalance_join(
        at_risk=at_risk, helpers=pool, idle=idle, unassigned=unassigned,
        this_year=today.year, rank=_rank,
        max_at_risk=MAX_AT_RISK_TASKS, max_pickups=MAX_PICKUPS_PER_PERSON,
    )

    at_risk_out: list[dict[str, Any]] = []
    for suggestion in joined["at_risk"]:
        task = suggestion["task"]
        note = suggestion["hours_note"]
        candidates = []
        for c in suggestion["candidates"]:
            row = c.model_dump()
            if note:
                # §13.2 rule 4 and §13.4 rule 2: no spare figure on any row.
                row.pop("spare_hours", None)
            candidates.append(row)
        entry = {k: v for k, v in task.items() if k != "match_text"}
        entry["candidates"] = candidates
        entry["hours_basis"] = note is None
        if note:
            entry["hours_note"] = note
        at_risk_out.append(entry)

    body.update({
        "hours_scope": "all_visible_work",
        "partial": not bool(getattr(vis, "unrestricted", False)),
        "at_risk": at_risk_out,
        "pickups": joined["pickups"],
        "at_risk_total": joined["total_at_risk"],
        "idle_total": len(idle),
        "truncated": (
            joined["total_at_risk"] > MAX_AT_RISK_TASKS
            or len(unassigned) >= MAX_UNASSIGNED_TASKS
        ),
    })
    return body
