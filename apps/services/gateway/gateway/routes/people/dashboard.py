"""People Center · the people-management dashboard, person rows (WS-28j1).

Spec: ``project-docs/specs/people_center_app.md`` §5.7.1, §5.7.2, §5.7.5 ·
**D-PC-14**.

    GET /people/dashboard   → one row per person, with their pill and its reason

**A read over the Projects app's tables — no new store, and no second
arithmetic.** ``pm_tasks`` / ``pm_task_assignees`` / ``pm_task_statuses`` /
``pm_projects`` / ``pm_activities``, joined to the People record on
``lower(email)`` the way every other seam in this app joins them (D-PC-1). The
classification is :mod:`gateway.workload`, which is pure and shared, so the
department rollup (j2) and the Center landing rollup (§5.9) project this
endpoint instead of counting again.

**Four aggregates, not four-per-person.** A roster of eighty people would be
three hundred round trips built the obvious way. Every figure on every row comes
from one of four statements keyed by ``lower(assignee)``, plus one absence query
for the page — the shape :func:`gateway.routes.people.absences.away_today`
already took for the directory.

⚠️ **Every figure is scoped by the VIEWER's grants** (§5.7.5), through
``resolve_visibility`` — the same closure ``/people/{id}/work`` walks. A rollup
is not a licence to see work you could not open.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException
from gateway.routes.people.core import (
    _tenant_session,
    can_manage_people,
    can_read_hr_fields,
    find_self_row,
    router,
)
from gateway.work_schedule import (
    _absence_spans,
    contracted_hours_per_week,
    load_policy,
    person_schedule,
    working_hours_between,
)
from gateway.workload import (
    HORIZON_DAYS,
    IDLE_FRACTION,
    PILLS,
    at_risk_tasks,
    classify,
    hours_of,
    rollup,
)
from pydantic import BaseModel
from sqlalchemy import text

#: How many project names travel on a row before it says "+N more". A person on
#: twenty projects is a finding in itself; twenty names in a table cell is not a
#: way to read it.
PROJECT_NAMES_SHOWN = 6

#: How far back "last activity" looks. Beyond a quarter the answer stops being
#: "quiet" and becomes "not using the tool", which is a different question and
#: not one this surface asks.
ACTIVITY_WINDOW_DAYS = 90

#: The open-task predicate, written once. Both task statements and the activity
#: statement have to agree about what "open" means, and two spellings of it is
#: how a count and its own expansion come to disagree.
_OPEN = "t.archived_at IS NULL AND s.category NOT IN ('done', 'cancelled')"


class DashboardRow(BaseModel):
    person_id: str | None
    name: str
    email: str | None = None
    department: str | None = None
    team: str | None = None
    avatar: str | None = None
    #: ``person`` or ``agent``. Agents hold tasks the same way people do
    #: (D-PM-4) and an activity report that silently omits half the workforce is
    #: wrong in the direction that matters — but they never carry a pill.
    kind: str = "person"

    open_tasks: int = 0
    overdue: int = 0
    unestimated: int = 0
    committed_hours: float = 0.0
    committed_this_week: float = 0.0
    contracted_hours: float = 0.0
    hours_available_this_week: float | None = None
    spare_hours_this_week: float | None = None
    #: Spare over the RISK HORIZON (today → +HORIZON_DAYS), absences applied,
    #: minus the estimates due inside it. The suggester's window (WS-28j3):
    #: help is needed before the deadline, not before Sunday — and "this week"
    #: is zero for the entire roster every Saturday (measured: the first live
    #: run of the suggester on a weekend returned no candidates for anybody).
    spare_hours_horizon: float | None = None
    next_due_at: str | None = None
    last_activity_at: str | None = None
    projects: list[dict[str, Any]] = []
    projects_total: int = 0
    at_risk: list[dict[str, Any]] = []
    away: dict[str, Any] | None = None
    #: An absence covering any day between today and Sunday — what the rollup's
    #: "who is away this week" reads. Distinct from ``away``, which is today
    #: only: somebody back tomorrow and somebody leaving on Thursday are both
    #: answers to "can I give them a deadline this week", and neither is
    #: "away right now".
    away_this_week: bool = False

    #: ``None`` for an agent, by design (§5.7.5).
    pill: str | None = None
    reason: str | None = None
    flags: list[str] = []
    hours_basis: bool = True
    note: str | None = None


class DashboardResponse(BaseModel):
    rows: list[DashboardRow]
    total: int
    #: True when the viewer's Projects grants are narrower than the whole
    #: portfolio, so every work figure here counts only what they may open.
    #:
    #: ⚠️ Deliberately NOT "how many rows were hidden". Computing that means
    #: running the same query without the scope, which is the query the scope
    #: exists to forbid — a leak of exactly one integer per person is still a
    #: leak, and it is the one somebody would use to probe. So the surface says
    #: the figures are partial and stops, which §5.7.5 prefers to a
    #: silently-truncated total presented as a whole.
    partial: bool = False
    #: False without ``feature:projects``: the work half comes back empty and
    #: the roster still renders. "This surface is not yours" and "nobody has any
    #: work" must not draw identically — the same call ``/people/{id}/work``
    #: already made.
    work_visible: bool = True
    #: WS-28j2 (§5.7.3), computed by :func:`gateway.workload.rollup` **over the
    #: serialized rows above** — so the rollup and the table it sits on cannot
    #: disagree, and §5.9's Center landing projects this instead of counting
    #: again.
    departments: list[dict[str, Any]] = []
    org: dict[str, Any] = {}
    can_manage: bool = False
    self_person_id: str | None = None
    horizon_days: int = HORIZON_DAYS
    idle_fraction: float = IDLE_FRACTION
    pills: list[str] = list(PILLS)


@router.get("/dashboard", response_model=DashboardResponse)
async def get_dashboard(
    user: UserContext = Depends(get_current_user),
) -> DashboardResponse:
    """The person rows (§5.7.1) and their pills (§5.7.2).

    **Gated on ``admin:members:read`` on top of ``feature:people``** (§5.7.5).
    It is skills, capacity and hours for everybody at once, so the oracle rule
    (§4.2) applies to the whole surface here rather than to a clause: there is
    no useful half of this page to show somebody who may not see the HR tier,
    and drawing an empty one would only invite a hunt for the permission.
    """
    if not can_read_hr_fields(user):
        raise HTTPException(
            status_code=403,
            detail="The people-management dashboard needs admin:members:read.")

    work_visible = bool(user.has_permission("feature:projects"))
    today = date.today()
    # Monday through Sunday, so "this week" means the same thing to everybody
    # looking at the same dashboard — the window `person_availability` already
    # uses.
    monday = today - timedelta(days=today.isoweekday() - 1)
    sunday = monday + timedelta(days=6)

    async with _tenant_session() as db:
        people = (await db.execute(text(
            "SELECT id, name, email, department, team, avatar, working_hours, "
            "       status "
            "  FROM people "
            " WHERE status <> 'alumni' "
            " ORDER BY department NULLS LAST, name"))).fetchall()
        policy = await load_policy(db)
        absences = await _absences_for(db, [str(p.id) for p in people])
        own = await find_self_row(db, user)

        totals: dict[str, Any] = {}
        dated: dict[str, list[dict[str, Any]]] = {}
        projects: dict[str, list[dict[str, Any]]] = {}
        activity: dict[str, Any] = {}
        if work_visible:
            vis = await _visibility(db, user)
            totals = await _totals(db, vis)
            dated = await _dated_tasks(db, vis, today)
            projects = await _projects(db, vis)
            activity = await _last_activity(db, vis)
            partial = not vis.unrestricted
        else:
            partial = False

    rows: list[DashboardRow] = []
    seen: set[str] = set()
    for person in people:
        who = (getattr(person, "email", None) or "").strip().lower()
        if who:
            seen.add(who)
        schedule = person_schedule(policy, person)
        spans = absences.get(str(person.id), [])
        rows.append(_row(
            person_id=str(person.id),
            name=str(person.name),
            email=getattr(person, "email", None),
            department=getattr(person, "department", None),
            team=getattr(person, "team", None),
            avatar=getattr(person, "avatar", None),
            kind="person",
            schedule=schedule,
            spans=spans,
            totals=totals.get(who),
            dated=dated.get(who, []),
            projects=projects.get(who, []),
            last_activity=activity.get(who),
            today=today, monday=monday, sunday=sunday,
        ))

    # Assignees the roster does not account for: agents (D-PM-4), people who
    # left, and addresses that were never in the directory. One mechanism for
    # three cases, and each of them is a thing somebody needs to see — work
    # assigned to a departed colleague is invisible everywhere else in the
    # product.
    for who in sorted(set(totals) - seen):
        rows.append(_row(
            person_id=None, name=who, email=(None if _is_agent(who) else who),
            department=None, team=None, avatar=None,
            kind="agent" if _is_agent(who) else "person",
            schedule=None, spans=[], totals=totals.get(who),
            dated=dated.get(who, []), projects=projects.get(who, []),
            last_activity=activity.get(who),
            today=today, monday=monday, sunday=sunday,
        ))

    # WS-28j2. Computed from `model_dump()` — the exact payload the client
    # receives — rather than from `rows` or from a second query. That is the
    # §5.9 guarantee as a mechanism: the rollup is arithmetic over the same
    # array, so it cannot disagree with the table under it, and it cannot read a
    # field the caller does not have.
    totals = rollup([r.model_dump() for r in rows])
    return DashboardResponse(
        rows=rows, total=len(rows), partial=partial, work_visible=work_visible,
        departments=totals["departments"], org=totals["org"],
        can_manage=can_manage_people(user),
        self_person_id=str(own.id) if own is not None else None,
    )


def _is_agent(assignee: str) -> bool:
    """``agent:<name>`` is the Projects app's own spelling (D-PM-4), so it is
    read rather than re-invented."""
    return assignee.startswith("agent:")


def _row(*, person_id, name, email, department, team, avatar, kind, schedule,
         spans, totals, dated, projects, last_activity, today, monday,
         sunday) -> DashboardRow:
    """One row, assembled from the four aggregates and the schedule.

    Everything here is arithmetic over numbers already fetched — no query, no
    per-row round trip — which is what keeps the endpoint four statements wide
    regardless of headcount.
    """
    open_tasks = int(getattr(totals, "open_tasks", 0) or 0) if totals else 0
    mins = int(getattr(totals, "mins", 0) or 0) if totals else 0
    unestimated = int(getattr(totals, "unestimated", 0) or 0) if totals else 0
    overdue = int(getattr(totals, "overdue", 0) or 0) if totals else 0
    next_due = getattr(totals, "next_due", None) if totals else None

    # This week's commitment: what is overdue plus what falls due on or before
    # Sunday. §5.7.2 says "for the week", and the alternative — every open task
    # ever, against one week of hours — compares a backlog to a week and calls
    # everybody overloaded.
    week_hours = sum(
        hours_of(t.get("estimate_mins")) for t in dated
        if (d := t.get("_due")) is not None and d <= sunday
    )

    contracted = contracted_hours_per_week(schedule) if schedule else 0.0
    available = (working_hours_between(schedule, today, sunday, spans)
                 if schedule else None)
    horizon_end = today + timedelta(days=HORIZON_DAYS)
    committed_horizon = sum(
        hours_of(t.get("estimate_mins")) for t in dated
        if (d := t.get("_due")) is not None and d <= horizon_end)
    available_horizon = (working_hours_between(schedule, today, horizon_end,
                                               spans)
                         if schedule else None)
    risky = at_risk_tasks(schedule, dated, spans, today) if schedule else []

    verdict = classify({
        "open_tasks": open_tasks, "unestimated": unestimated,
        "overdue": overdue, "contracted_hours": contracted,
        "committed_this_week": week_hours, "at_risk": risky,
    })
    # An agent is never given a pill: "idle" and "behind" are statements about
    # capacity and commitment, and neither means anything about a process
    # (§5.7.5). Its numbers still render — that is the whole reason it is here.
    agent = kind == "agent"

    return DashboardRow(
        person_id=person_id, name=name, email=email, department=department,
        team=team, avatar=avatar, kind=kind,
        open_tasks=open_tasks, overdue=overdue, unestimated=unestimated,
        committed_hours=round(mins / 60.0, 1),
        committed_this_week=round(week_hours, 1),
        contracted_hours=contracted,
        hours_available_this_week=available,
        spare_hours_this_week=(None if available is None
                               else round(max(0.0, available - week_hours), 1)),
        spare_hours_horizon=(None if available_horizon is None
                             else round(max(0.0, available_horizon
                                            - committed_horizon), 1)),
        next_due_at=next_due.isoformat() if next_due else None,
        last_activity_at=(last_activity.isoformat() if last_activity else None),
        projects=projects[:PROJECT_NAMES_SHOWN],
        projects_total=len(projects),
        at_risk=([] if agent else risky),
        away=_away(today, spans),
        away_this_week=any(s["starts_on"] <= sunday and s["ends_on"] >= today
                           for s in _absence_spans(spans)),
        pill=(None if agent else verdict["pill"]),
        reason=(None if agent else verdict["reason"]),
        flags=([] if agent else verdict["flags"]),
        hours_basis=(True if agent else verdict["hours_basis"]),
        note=(None if agent else verdict["note"]),
    )


def _away(today: date, spans: list[dict[str, Any]]) -> dict[str, Any] | None:
    from gateway.work_schedule import absent_on

    span = absent_on(today, spans)
    if span is None:
        return None
    return {"kind": span["kind"], "until": span["ends_on"].isoformat()}


# ── The four aggregates ──────────────────────────────────────────────────────


async def _visibility(db: Any, user: UserContext) -> Any:
    from gateway.routes.projects.core import resolve_visibility

    return await resolve_visibility(db, user)


def _scope(vis: Any, params: dict[str, Any], column: str = "t.root_project_id") -> str:
    """The viewer's grant closure as a WHERE fragment, binds folded in.

    ⚠️ **Applied for the unrestricted caller too.** ``data:org:read`` means
    unrestricted *within a tenant*, never across them, and
    :meth:`Visibility.project_clause` answers the tenant subquery rather than
    ``TRUE`` for exactly that reason (WS-29b). Skipping it here and relying on
    the session's row-level security would make this endpoint's correctness
    depend on an enforcement flip that is the owner's act — which is not a thing
    a read path should rest on when the clause costs one cheap subquery.
    ``/people/{id}/work`` does skip it; that is a finding for the board, not a
    pattern to copy.

    ``t.root_project_id`` is the column the Projects package scopes on and the
    one migration 161's trigger keeps in the same tenant.
    """
    params.update(vis.params)
    return vis.project_clause(column)


async def _totals(db: Any, vis: Any) -> dict[str, Any]:
    """Open tasks, hours, unestimated, overdue and the next deadline — one
    statement for the whole page, grouped by assignee."""
    params: dict[str, Any] = {}
    scope = _scope(vis, params)
    rows = (await db.execute(text(
        "SELECT lower(a.assignee) AS who, "
        "       count(*) AS open_tasks, "
        "       COALESCE(sum(t.estimate_mins), 0) AS mins, "
        "       count(*) FILTER (WHERE t.estimate_mins IS NULL) AS unestimated, "
        "       count(*) FILTER (WHERE t.due_at IS NOT NULL "
        "                          AND t.due_at < now()) AS overdue, "
        "       min(t.due_at) FILTER (WHERE t.due_at >= now()) AS next_due "
        "  FROM pm_tasks t "
        "  JOIN pm_task_statuses s ON s.id = t.status_id "
        "  JOIN pm_task_assignees a ON a.task_id = t.id "
        " WHERE " + _OPEN + " AND " + scope +
        " GROUP BY 1"), params)).fetchall()
    return {str(r.who): r for r in rows}


async def _dated_tasks(db: Any, vis: Any, today: date) -> dict[str, list[dict]]:
    """Every open task with a due date inside the horizon, or already past it.

    The rows the at-risk arithmetic walks, and the only place this endpoint
    fetches individual tasks. Bounded by the horizon rather than by a row
    limit: a LIMIT here would silently drop the deadline that mattered, and a
    dashboard that under-reports risk is worse than one that reports none.
    """
    params: dict[str, Any] = {"until": today + timedelta(days=HORIZON_DAYS + 1)}
    scope = _scope(vis, params)
    rows = (await db.execute(text(
        "SELECT lower(a.assignee) AS who, t.id, t.title, t.due_at, "
        "       t.estimate_mins, p.name AS project_name "
        "  FROM pm_tasks t "
        "  JOIN pm_task_statuses s ON s.id = t.status_id "
        "  JOIN pm_task_assignees a ON a.task_id = t.id "
        "  LEFT JOIN pm_projects p ON p.id = t.project_id "
        " WHERE " + _OPEN + " AND " + scope +
        "   AND t.due_at IS NOT NULL AND t.due_at < :until "
        " ORDER BY t.due_at"), params)).fetchall()
    out: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        due = row.due_at.date() if row.due_at else None
        out.setdefault(str(row.who), []).append({
            "id": str(row.id), "title": row.title, "due_at": row.due_at,
            "estimate_mins": row.estimate_mins,
            "project_name": getattr(row, "project_name", None),
            # Kept beside the timestamp so the week sum and the at-risk walk
            # agree about which day a task falls on. Two truncations of one
            # timestamp is how a task lands in one figure and not the other.
            "_due": due,
        })
    return out


async def _projects(db: Any, vis: Any) -> dict[str, list[dict[str, Any]]]:
    """*"What projects is this person on"* — the question a task list does not
    answer, and the one a reassignment conversation opens with."""
    params: dict[str, Any] = {}
    scope = _scope(vis, params)
    rows = (await db.execute(text(
        "SELECT DISTINCT lower(a.assignee) AS who, p.id, p.name "
        "  FROM pm_tasks t "
        "  JOIN pm_task_statuses s ON s.id = t.status_id "
        "  JOIN pm_task_assignees a ON a.task_id = t.id "
        "  JOIN pm_projects p ON p.id = t.project_id "
        " WHERE " + _OPEN + " AND " + scope +
        " ORDER BY 1, 3"), params)).fetchall()
    out: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        out.setdefault(str(row.who), []).append(
            {"id": str(row.id), "name": row.name})
    return out


async def _last_activity(db: Any, vis: Any) -> dict[str, Any]:
    """The difference between *"quiet because nothing is due"* and *"quiet
    because nothing is happening"* (§5.7.1).

    Scoped like everything else: an activity on a project the viewer cannot open
    must not become a timestamp they can read. The visibility clause names
    ``t.root_project_id`` so the alias is bound here too — an activity attached
    to a project rather than a task falls back to its own ``project_id``, which
    the closure also contains (it holds every visible project at any depth, not
    only the roots).
    """
    params: dict[str, Any] = {"days": ACTIVITY_WINDOW_DAYS}
    scope = _scope(vis, params, "COALESCE(t.root_project_id, v.project_id)")
    rows = (await db.execute(text(
        "SELECT lower(v.created_by) AS who, max(v.created_at) AS last_at "
        "  FROM pm_activities v "
        "  LEFT JOIN pm_tasks t ON t.id = v.task_id "
        " WHERE v.created_at > now() - make_interval(days => :days) "
        "   AND " + scope +
        " GROUP BY 1"), params)).fetchall()
    return {str(r.who): r.last_at for r in rows}


async def _absences_for(db: Any, person_ids: list[str]) -> dict[str, list[dict]]:
    """Every current-and-future span for the page, in the ARITHMETIC shape.

    One statement rather than one per person, and best-effort for the same
    reason :func:`~gateway.routes.people.absences.away_today` is: a database one
    deploy behind migration 174 should answer "nobody is away" rather than
    failing the whole dashboard.
    """
    if not person_ids:
        return {}
    try:
        rows = (await db.execute(text(
            "SELECT person_id, starts_on, ends_on, kind, hours_per_day "
            "  FROM people_absences "
            " WHERE person_id = ANY(CAST(:ids AS uuid[])) "
            "   AND ends_on >= CURRENT_DATE"), {"ids": person_ids})).fetchall()
    except Exception:
        return {}
    out: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        out.setdefault(str(row.person_id), []).append({
            "starts_on": row.starts_on, "ends_on": row.ends_on,
            "kind": row.kind, "hours_per_day": row.hours_per_day})
    return out
