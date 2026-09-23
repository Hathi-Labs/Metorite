"""Projects · capacity — who has the hours for the open work (WS-27bm S7a).

Spec: ``project-docs/specs/projects_ai_chat.md`` §13.2, §13.3, §10.3.

    GET /projects/analytics/capacity?project_id=&include_subtree=&horizon_days=

One row for each person who holds open work in scope, and one row for the work
nobody holds. The Analytics app's Capacity panel, the report section
``capacity`` and the chat tool ``team_capacity`` all read this one body
(:func:`capacity_body`), so the three cannot disagree.

**Two scopes, on purpose, and the response says which figure is which.**

* The ROWS and their task half — ``open_tasks``, ``overdue``, the estimate
  left — are the work IN SCOPE, counted with Load's own query
  (``analytics.load_sql`` over ``analytics.load_open_where``). For one scope,
  a row's ``open_tasks`` IS Load's count for that person (§10.3 item 1). The
  dashboard's ``_OPEN`` and ``project_clause`` are not used (§13.3 rule 1).
* The HOURS are measured over every open task the CALLER can see, in any
  project. Somebody who is full on another project has no spare hours for
  this one, and a figure that counted only this project would call them free.
  ``all_work`` carries the counts the hours were measured against.

**The arithmetic is not here.** Hours, spare hours, the at-risk walk and the
pill are :func:`gateway.capacity.person_capacity`, which the People dashboard
calls too (§13.3 rule 2). This module fetches aggregates and projects them.

⚠️ **The HR tier is the CALLER's grant, and it is absent, never null**
(§13.2 rule 3). Without ``admin:members:read`` a row carries the task half and
nothing else, and the response says ``hr_visible: false``. A null hours field
would still tell the reader the field exists for this person, and a zero would
read as "free". :data:`HR_KEYS` is the list, and a test holds every row to it.

⚠️ **No estimate, no hours** (§13.2 rule 4). When no open task a person holds
carries an estimate, or the person has no contracted week, the row carries no
committed or spare hours and ``hours_note`` says why.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException
from gateway.capacity import (
    MAX_HORIZON_DAYS,
    MIN_HORIZON_DAYS,
    absences_for,
    absences_in_window,
    horizon_window,
    person_capacity,
    skills_for,
    week_window,
)
from gateway.routes.projects.analytics import (
    load_open_where,
    load_params,
    load_sql,
    scope_clause,
    total_open_sql,
)
from gateway.routes.projects.core import (
    STARTED_CATEGORY,
    _tenant_session,
    resolve_visibility,
    router,
)
from gateway.routes.tasks.core import can_read_hr_fields
from gateway.workload import HORIZON_DAYS
from sqlalchemy import text

#: Every key a row carries only for an ``admin:members:read`` holder.
#:
#: ⚠️ The fence reads THIS tuple, and it is the whole of the HR tier on a row.
#: A key added to the HR block below and left out of here is a key a caller
#: without the grant would receive.
HR_KEYS: tuple[str, ...] = (
    "all_work",
    "contracted_hours_per_week",
    "working_hours_this_week",
    "working_hours_horizon",
    "committed_hours_this_week",
    "committed_hours_horizon",
    "spare_hours_this_week",
    "spare_hours_horizon",
    "hours_basis",
    "hours_note",
    "absences",
    "end_date",
    "leaving_in_window",
    "at_risk",
    "pill",
    "pill_reason",
    "flags",
    "max_concurrent_tasks",
    "over_concurrency",
    "skills",
)

#: The hours keys a row DROPS when ``hours_basis`` is false (§13.2 rule 4).
#: Working hours stay: they are a fact about the schedule, not a comparison.
HOURS_KEYS: tuple[str, ...] = (
    "committed_hours_this_week",
    "committed_hours_horizon",
    "spare_hours_this_week",
    "spare_hours_horizon",
)


def capacity_totals_sql(open_where: str) -> str:
    """Each holder's open work across the caller's whole view, one statement.

    ``open_where`` is :func:`~gateway.routes.projects.analytics.load_open_where`
    at the portfolio scope. ``:holders`` is the lowercased addresses of the
    people holding work in scope, so this reads nobody else's plate.

    ``unestimated`` beside ``mins`` is the coverage the pill needs:
    :func:`gateway.workload.classify` turns the hours signals off when every
    open task is unestimated. ``in_progress`` is what
    ``max_concurrent_tasks`` is compared with.
    """
    return (
        f"SELECT lower(a.assignee) AS who,"
        f"       count(*) AS open_tasks,"
        f"       coalesce(sum(t.estimate_mins), 0) AS mins,"
        f"       count(*) FILTER (WHERE t.estimate_mins IS NULL) AS unestimated,"
        f"       count(*) FILTER ("
        f"         WHERE t.due_at IS NOT NULL AND t.due_at < now()"
        f"       ) AS overdue,"
        f"       count(*) FILTER (WHERE s.category = :started_cat) AS in_progress,"
        f"       min(t.due_at) FILTER (WHERE t.due_at >= now()) AS next_due"
        f"  FROM pm_tasks t"
        f"  JOIN pm_task_statuses s ON s.id = t.status_id"
        f"  JOIN pm_task_assignees a ON a.task_id = t.id"
        f" WHERE {open_where}"
        f"   AND lower(a.assignee) = ANY(CAST(:holders AS text[]))"
        f" GROUP BY 1"
    )


def capacity_dated_sql(open_where: str) -> str:
    """Each holder's dated open work, due inside the horizon or already late.

    The rows the at-risk walk reads. Bounded by the horizon, never by a
    ``LIMIT``: a limit would drop the deadline that mattered, and a read that
    under-reports risk is worse than one that reports none.
    """
    return (
        f"SELECT lower(a.assignee) AS who, t.id, t.title, t.due_at,"
        f"       t.estimate_mins, p.name AS project_name"
        f"  FROM pm_tasks t"
        f"  JOIN pm_task_statuses s ON s.id = t.status_id"
        f"  JOIN pm_task_assignees a ON a.task_id = t.id"
        f"  LEFT JOIN pm_projects p ON p.id = t.project_id"
        f" WHERE {open_where}"
        f"   AND lower(a.assignee) = ANY(CAST(:holders AS text[]))"
        f"   AND t.due_at IS NOT NULL AND t.due_at < CAST(:until AS date)"
        f" ORDER BY t.due_at"
    )


#: The directory half of a holder. `name` is directory tier (§3.1) and every
#: caller reads it. The rest feeds the HR block and is read only for a caller
#: who holds the grant.
#:
#: ⚠️ No `status` filter. A colleague who left and still holds open work is the
#: row that most needs a name and an end date, and the People dashboard keeps
#: the same people visible for the same reason.
_PEOPLE_SQL = (
    "SELECT id, name, lower(email) AS email, working_hours, end_date,"
    "       max_concurrent_tasks"
    "  FROM people"
    " WHERE lower(email) = ANY(CAST(:emails AS text[]))"
)


def check_horizon(horizon_days: Any) -> int:
    """``horizon_days`` as an int from 1 to 90, or a 422 that names the range."""
    try:
        days = int(horizon_days)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=422, detail="horizon_days must be a whole number.",
        ) from None
    if not MIN_HORIZON_DAYS <= days <= MAX_HORIZON_DAYS:
        raise HTTPException(
            status_code=422,
            detail=(
                f"horizon_days must be between {MIN_HORIZON_DAYS} and"
                f" {MAX_HORIZON_DAYS}."
            ),
        )
    return days


@router.get("/analytics/capacity")
async def capacity(
    project_id: str | None = None,
    include_subtree: bool = True,
    horizon_days: int = HORIZON_DAYS,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Who holds the open work in scope, and whether they have the hours.

    Omit ``project_id`` for the portfolio. ``horizon_days`` (1 to 90, default
    14) sets the spare-hours and at-risk window. The pill always compares
    this Monday-to-Sunday week, and the response prints both windows.
    """
    days = check_horizon(horizon_days)
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        return await capacity_body(
            db, vis,
            hr_visible=can_read_hr_fields(user),
            project_id=project_id,
            include_subtree=include_subtree,
            horizon_days=days,
        )


async def capacity_body(
    db: Any,
    vis: Any,
    *,
    hr_visible: bool,
    project_id: str | None,
    include_subtree: bool,
    horizon_days: int = HORIZON_DAYS,
    today: date | None = None,
) -> dict[str, Any]:
    """The capacity answer, on a session and a visibility the caller resolved.

    Shared by the route and by the report section ``capacity``, so a report
    and the panel it came from are one computation. ``hr_visible`` is the
    CALLER's grant and is taken as an argument rather than read here, because
    this function has no request to read it from.
    """
    days = check_horizon(horizon_days)
    today = today or date.today()
    monday, sunday = week_window(today)
    _, horizon_end = horizon_window(today, days)

    # ── The task half: Load's query, over Load's predicate, for this scope ─
    scope_sql = await scope_clause(db, vis, project_id, include_subtree)
    scope_where = load_open_where(scope_sql, vis)
    scope_params = load_params(vis, project_id)
    load_rows = (
        await db.execute(text(load_sql(scope_where)), scope_params)
    ).fetchall()
    total_tasks = int(
        (await db.execute(text(total_open_sql(scope_where)), scope_params)).scalar()
        or 0
    )

    by_who = {str(r.who or ""): r for r in load_rows}
    holders = [w for w in by_who if w]
    people_emails = [w for w in holders if not w.startswith("agent:")]

    directory: dict[str, Any] = {}
    if people_emails:
        directory = {
            str(r.email): r
            for r in (
                await db.execute(text(_PEOPLE_SQL), {"emails": people_emails})
            ).fetchall()
        }

    # ── The HR half: every open task the caller can see, for these people ──
    totals: dict[str, Any] = {}
    dated: dict[str, list[dict[str, Any]]] = {}
    policy: dict[str, Any] | None = None
    absences: dict[str, list[dict]] = {}
    skills: dict[str, list[dict[str, Any]]] = {}
    if hr_visible and people_emails:
        from gateway.work_schedule import load_policy

        all_where = load_open_where("TRUE", vis)
        all_params = {
            **load_params(vis, None),
            "holders": people_emails,
            "started_cat": STARTED_CATEGORY,
        }
        totals = {
            str(r.who): r
            for r in (
                await db.execute(text(capacity_totals_sql(all_where)), all_params)
            ).fetchall()
        }
        dated_params = {**all_params, "until": horizon_end}
        dated_params.pop("started_cat")
        for row in (
            await db.execute(text(capacity_dated_sql(all_where)), dated_params)
        ).fetchall():
            dated.setdefault(str(row.who), []).append({
                "id": str(row.id),
                "title": row.title,
                "due_at": row.due_at,
                "estimate_mins": row.estimate_mins,
                "project_name": getattr(row, "project_name", None),
                "_due": row.due_at.date() if row.due_at else None,
            })
        policy = await load_policy(db)
        ids = [str(p.id) for p in directory.values()]
        absences = await absences_for(db, ids)
        skills = await skills_for(db, ids)

    rows: list[dict[str, Any]] = []
    for who in holders:
        load_row = by_who[who]
        person = directory.get(who)
        row = _task_half(who, load_row, person)
        if hr_visible and row["kind"] == "person":
            row.update(_hr_half(
                person=person, policy=policy,
                spans=absences.get(str(person.id), []) if person else [],
                skills=skills.get(str(person.id), []) if person else [],
                totals=totals.get(who), dated=dated.get(who, []),
                today=today, horizon_days=days, horizon_end=horizon_end,
            ))
        rows.append(row)
    # ⚠️ The unassigned row is ALWAYS there, and always last. It is the work
    # nobody holds, and on a real board it is usually the finding — so "zero"
    # is said rather than left for the reader to infer from an absence.
    rows.append(_task_half("", by_who.get(""), None))

    return {
        "project_id": project_id,
        "scope": "portfolio" if project_id is None else "node",
        "include_subtree": include_subtree,
        "horizon_days": days,
        "hr_visible": hr_visible,
        # §13.3 rule 3. Two windows, and each figure names the one it used.
        "windows": {
            "week": {
                "starts_on": monday.isoformat(),
                "ends_on": sunday.isoformat(),
                "used_for": "pill",
            },
            "horizon": {
                "starts_on": today.isoformat(),
                "ends_on": horizon_end.isoformat(),
                "days": days,
                "used_for": "spare_hours_and_at_risk",
            },
        },
        # The task half is this scope. The hours are every open task the
        # caller can see. Said in the payload so no client has to know it.
        "task_scope": "this_scope",
        "hours_scope": "all_visible_work",
        # True when the caller's grants are narrower than the tenant, so the
        # hours count only the work they may open. Not "how much is hidden":
        # counting that means running the read the grants forbid.
        "partial": not bool(getattr(vis, "unrestricted", False)),
        "total_tasks": total_tasks,
        "people_total": len(holders),
        "rows": rows,
    }


def _task_half(who: str, load_row: Any, person: Any) -> dict[str, Any]:
    """A row's task half: what every caller who may see the scope may read."""
    agent = who.startswith("agent:")
    if not who:
        kind, name = "unassigned", None
    elif agent:
        kind, name = "agent", who.split(":", 1)[1] or who
    else:
        kind = "person"
        name = (getattr(person, "name", None) or None) if person else None
    open_tasks = int(getattr(load_row, "open_tasks", 0) or 0) if load_row else 0
    est_mins = int(getattr(load_row, "est_mins", 0) or 0) if load_row else 0
    return {
        "assignee": who or None,
        "name": name,
        "kind": kind,
        "in_directory": person is not None,
        "open_tasks": open_tasks,
        "overdue": int(getattr(load_row, "overdue", 0) or 0) if load_row else 0,
        "due_next_7d": (
            int(getattr(load_row, "due_next_7d", 0) or 0) if load_row else 0
        ),
        "later": int(getattr(load_row, "later", 0) or 0) if load_row else 0,
        # ⚠️ Estimated, never logged. `estimated` of `open_tasks` is how much
        # of the plate somebody sized at all.
        "estimated_hours_left": round(est_mins / 60.0, 1),
        "estimated": int(getattr(load_row, "estimated", 0) or 0) if load_row else 0,
    }


def _hr_half(
    *,
    person: Any,
    policy: dict[str, Any] | None,
    spans: list[dict[str, Any]],
    skills: list[dict[str, Any]],
    totals: Any,
    dated: list[dict[str, Any]],
    today: date,
    horizon_days: int,
    horizon_end: date,
) -> dict[str, Any]:
    """A row's HR half. Only called for a caller who holds the grant."""
    from gateway.work_schedule import person_schedule

    schedule = person_schedule(policy or {}, person) if person is not None else None
    m = person_capacity(
        schedule=schedule, spans=spans, totals=totals, dated=dated,
        today=today, horizon_days=horizon_days,
    )
    in_progress = int(getattr(totals, "in_progress", 0) or 0) if totals else 0
    ceiling = getattr(person, "max_concurrent_tasks", None) if person else None
    end = getattr(person, "end_date", None) if person else None

    out: dict[str, Any] = {
        "all_work": {
            "open_tasks": m["open_tasks"],
            "overdue": m["overdue"],
            "unestimated": m["unestimated"],
            "in_progress": in_progress,
        },
        "contracted_hours_per_week": m["contracted_hours"],
        "working_hours_this_week": m["available_this_week"],
        "working_hours_horizon": m["available_horizon"],
        "committed_hours_this_week": m["committed_this_week"],
        "committed_hours_horizon": m["committed_horizon"],
        "spare_hours_this_week": m["spare_this_week"],
        "spare_hours_horizon": m["spare_horizon"],
        "hours_basis": m["hours_basis"],
        "hours_note": m["note"],
        "absences": absences_in_window(spans, today, horizon_end),
        "end_date": end.isoformat() if end else None,
        "leaving_in_window": bool(end is not None and end <= horizon_end),
        "at_risk": m["at_risk"],
        "pill": m["pill"],
        "pill_reason": m["reason"],
        "flags": m["flags"],
        "max_concurrent_tasks": ceiling,
        "over_concurrency": bool(ceiling is not None and in_progress > int(ceiling)),
        "skills": skills,
    }
    if not m["hours_basis"]:
        # §13.2 rule 4: a zero is never shown as "free". The keys go, and the
        # note says why.
        for key in HOURS_KEYS:
            out.pop(key, None)
        if not out["hours_note"]:
            out["hours_note"] = (
                "No open task carries an estimate, so there are no committed"
                " or spare hours for this row."
            )
    return out
