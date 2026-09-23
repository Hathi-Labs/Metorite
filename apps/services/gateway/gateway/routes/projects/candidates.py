"""Projects · fit — who should take this task (WS-27bm S7b).

Spec: ``project-docs/specs/projects_ai_chat.md`` §13.2, §13.4, §10.4.

    GET /projects/tasks/{task_id}/candidates     → ranked people for one task
    GET /projects/candidates?title=&tags=&due=   → the same, for a draft task

Both routes answer with one body (:func:`candidates_body`), so a task and a
draft with the same text and due date rank the same people. The task panel's
"Suggested" list and the chat tool ``fit_for_task`` read these routes.

**The ranker is ``rank_candidates``, and nothing here ranks again** (§13.2
rule 1). The People suggester owns it: §5.5's skill score, times the spare
hours, times the availability factor. This module chooses the INPUTS — the
match text, the pool, the window — and wraps each candidate with warnings.
It imports the People helpers inside the functions, as ``assignees.py``
does, because the People package imports this one.

The six decisions of §13.4, and where each one lives:

1. **No HR grant, no candidates.** A caller without ``admin:members:read``
   gets 200, ``hr_visible: false`` and NO ``candidates`` key. A ranked list of
   names still says who holds which skill (``people_center_app.md`` §4.2).
2. **No estimate, rank by skill and availability — for the whole list.**
   :func:`rank_for_text`. If ANY person whose skills match lacks
   ``hours_basis``, every one of them is ranked with a neutral spare figure
   of 1, the response drops ``spare_hours`` from every candidate, and one
   ``hours_note`` says why.
3. **The match text** is the title, the tag names and the first
   :data:`DESCRIPTION_CHARS` characters of the description (:func:`match_text`).
4. **The pool and the window.** Every active person in ``people`` with an
   email, less the task's assignees. Spare hours come from
   :func:`gateway.capacity.person_capacity` over every open task the caller
   can see. The window runs from today to the due date, clamped to 1..90
   days, or 14 days with no due date (:func:`horizon_for`).
5. **Warnings wrap the candidate** (:func:`candidate_warnings`). The
   ``Candidate`` model does not change.
6. The rebalancing join is ``gateway.capacity.rebalance_join``, and the
   rebalance route (``analytics_rebalance.py``) ranks through
   :func:`rank_for_text` and :func:`pool_capacity` from here.

Read-only. It suggests and never assigns (D-PC-13). A pick goes through the
ordinary ``PUT /projects/tasks/{id}/assignees``.
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
    dated_until,
    horizon_window,
    person_capacity,
    skill_rows_for,
)
from gateway.routes.projects.analytics import load_open_where, load_params
from gateway.routes.projects.analytics_capacity import (
    capacity_dated_sql,
    capacity_totals_sql,
)
from gateway.routes.projects.core import (
    STARTED_CATEGORY,
    _tenant_session,
    load_visible_task,
    resolve_visibility,
    router,
)
from gateway.routes.tasks.core import can_read_hr_fields
from gateway.workload import HORIZON_DAYS
from sqlalchemy import text

#: §13.4 rule 3. A long description mentions skills in passing, and a
#: passing mention is not what the task needs.
DESCRIPTION_CHARS = 500

#: The draft form's shortest title. One character matches no skill name
#: (``score_skills`` skips names under two), so it would rank nobody and look
#: like an answer.
MIN_TITLE_CHARS = 2

#: The most tags a draft may send: the task write's own cap
#: (``tags.MAX_TAGS_PER_TASK``), so a draft never matches on more than a task
#: could carry.
MAX_DRAFT_TAGS = 25

#: The one sentence of §13.4 rule 2.
HOURS_NOTE = (
    "One or more of the matching people has no estimated work, so their spare"
    " hours mean nothing. Every rank here is skill times availability, and no"
    " candidate shows spare hours."
)

#: The directory half of the pool, one statement for the tenant. ``alumni``
#: rows are left out because they hold no future work. ``status`` travels so
#: the pool can keep only the ACTIVE people (§13.4 rule 4), while the
#: rebalance route still reads the at-risk work of a contractor.
_POOL_SQL = (
    "SELECT id, name, lower(email) AS email, status, working_hours,"
    "       end_date, max_concurrent_tasks"
    "  FROM people"
    " WHERE status <> 'alumni' AND coalesce(email, '') <> ''"
    " ORDER BY name"
)


def match_text(title: Any, tags: Any, description: Any) -> str:
    """The text the skills are matched against (§13.4 rule 3).

    A tag often names the skill the title does not, so a task titled "Fix
    the mount" and tagged ``CAD`` ranks the CAD people.
    """
    parts = [str(title or "").strip()]
    parts.extend(str(t).strip() for t in (tags or []) if str(t or "").strip())
    body = str(description or "").strip()[:DESCRIPTION_CHARS]
    if body:
        parts.append(body)
    return "\n".join(p for p in parts if p)


def horizon_for(due_on: date | None, today: date) -> tuple[int, str]:
    """The window in days, and what it came from (§13.4 rule 4).

    From today to the due date, clamped to 1..90. A task due today or in the
    past still gets one day, because a window of zero days has no working
    hours in it and would call everybody full.
    """
    if due_on is None:
        return HORIZON_DAYS, "default"
    days = (due_on - today).days
    return max(MIN_HORIZON_DAYS, min(MAX_HORIZON_DAYS, days)), "due_date"


async def pool_capacity(
    db: Any, vis: Any, *, today: date, horizon_days: int,
) -> list[dict[str, Any]]:
    """Every directory person with an email, with their hours for the window.

    One statement each for the directory, the open-work totals, the dated
    work, the policy, the absences and the skills — never one per person.
    The hours read every open task the caller can see (``load_open_where``
    at the portfolio scope), so a person full on another project has no
    spare hours for this one. That is the S7a capacity route's rule, over
    its own SQL.

    Each row carries ``in_pool``: true for an ACTIVE person. The candidates
    route ranks only those. Every other key is the ranker's
    (``rank_candidates``) or a warning's input.
    """
    from gateway.work_schedule import load_policy, person_schedule

    people = (await db.execute(text(_POOL_SQL))).fetchall()
    emails = [str(p.email) for p in people]
    if not emails:
        return []

    where = load_open_where("TRUE", vis)
    params = {
        **load_params(vis, None),
        "holders": emails,
        "started_cat": STARTED_CATEGORY,
    }
    totals = {
        str(r.who): r
        for r in (await db.execute(text(capacity_totals_sql(where)), params)).fetchall()
    }
    dated_params = {**params, "until": dated_until(today, horizon_days)}
    dated_params.pop("started_cat")
    dated: dict[str, list[dict[str, Any]]] = {}
    for row in (
        await db.execute(text(capacity_dated_sql(where)), dated_params)
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
    ids = [str(p.id) for p in people]
    absences = await absences_for(db, ids)
    skills = await skill_rows_for(db, ids)

    out: list[dict[str, Any]] = []
    for person in people:
        email = str(person.email)
        pid = str(person.id)
        spans = absences.get(pid, [])
        m = person_capacity(
            schedule=person_schedule(policy or {}, person), spans=spans,
            totals=totals.get(email), dated=dated.get(email, []),
            today=today, horizon_days=horizon_days,
        )
        work = totals.get(email)
        out.append({
            "person_id": pid,
            "name": person.name,
            "email": email,
            "in_pool": person.status == "active",
            "skill_rows": skills.get(pid, []),
            # The ranker's spare figure. A zero is a real "no time"; the
            # ranker drops that person, which is right: help that does not
            # exist cannot be suggested.
            "spare_hours": m["spare_horizon"] or 0.0,
            "hours_basis": m["hours_basis"],
            "away": None,
            "spans": spans,
            "end_date": getattr(person, "end_date", None),
            "max_concurrent_tasks": getattr(person, "max_concurrent_tasks", None),
            "in_progress": int(getattr(work, "in_progress", 0) or 0) if work else 0,
            "capacity": m,
        })
    return out


def rank_for_text(
    text_: str,
    helpers: list[dict[str, Any]],
    *,
    this_year: int,
    exclude_email: str = "",
) -> tuple[list[Any], str | None]:
    """``rank_candidates`` over one match text, with §13.4 rule 2 applied.

    First, WHO matches: each helper alone, with the neutral spare figure, so
    the ranker's own skill test decides and nothing here scores a skill.
    Then, if any match lacks ``hours_basis``, every match is ranked with the
    neutral figure and the note comes back. Otherwise the real spare hours
    rank them. A mixed list would put a person with no basis 30 times below
    a person with 30 spare hours, from a number the reader cannot see.
    """
    from gateway.routes.people.suggestions import rank_candidates

    exclude = (exclude_email or "").lower()

    def _neutral(helper: dict[str, Any]) -> dict[str, Any]:
        return {**helper, "spare_hours": 1.0}

    matched = [
        h for h in helpers
        if rank_candidates(text_, [_neutral(h)], this_year=this_year, exclude_email=exclude)
    ]
    if any(not h.get("hours_basis", True) for h in matched):
        ranked = rank_candidates(
            text_, [_neutral(h) for h in matched], this_year=this_year,
            exclude_email=exclude,
        )
        return ranked, HOURS_NOTE
    return rank_candidates(text_, matched, this_year=this_year, exclude_email=exclude), None


def availability_day(due_on: date | None, today: date) -> date:
    """The day availability is checked on: the due date, never a past one.

    ⚠️ An overdue task is needed NOW. Checking its past due date found no
    absence for a person on leave from today, and in note mode (rule 2, the
    neutral spare figure) nothing else held them back, so they ranked first
    (S7b review round 1, P1). So the day is the later of the two.
    """
    if due_on is None:
        return today
    return max(due_on, today)


def candidate_warnings(
    helper: dict[str, Any], due_on: date | None, *, today: date | None = None,
) -> list[str]:
    """The three warnings of §13.4 rule 5. Shown, never enforced.

    Away on the due date, an end date before the due date, and more tasks in
    progress than ``max_concurrent_tasks``. The first two need a due date;
    without one there is no date to be away on or to leave before. For an
    overdue task, "away" is checked TODAY (:func:`availability_day`).
    """
    from gateway.work_schedule import absent_on

    today = today or date.today()
    out: list[str] = []
    if due_on is not None:
        day = availability_day(due_on, today)
        span = absent_on(day, helper.get("spans") or [])
        if span is not None:
            when = (
                f"on the due date {due_on.isoformat()}" if day == due_on
                else "today, and the task is already overdue"
            )
            out.append(
                f"Away ({span['kind']}) {when}, until {span['ends_on'].isoformat()}"
            )
        end = helper.get("end_date")
        if end is not None and end < due_on:
            out.append(
                f"Engagement ends {end.isoformat()}, before the due date"
                f" {due_on.isoformat()}"
            )
    ceiling = helper.get("max_concurrent_tasks")
    busy = int(helper.get("in_progress") or 0)
    if ceiling is not None and busy > int(ceiling):
        out.append(f"{busy} tasks in progress, over the limit of {int(ceiling)}")
    return out


def _away_on(day: date, spans: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The ranker's ``away`` value for one day, in the dashboard's shape."""
    from gateway.work_schedule import absent_on

    span = absent_on(day, spans)
    if span is None:
        return None
    return {"kind": span["kind"], "until": span["ends_on"].isoformat()}


def _window(today: date, days: int, basis: str) -> dict[str, Any]:
    _, end = horizon_window(today, days)
    return {
        "starts_on": today.isoformat(),
        "ends_on": end.isoformat(),
        "days": days,
        "basis": basis,
    }


async def candidates_body(
    db: Any,
    vis: Any,
    *,
    hr_visible: bool,
    title: Any,
    tags: Any,
    description: Any,
    due_on: date | None,
    exclude: set[str],
    today: date | None = None,
) -> dict[str, Any]:
    """The candidates answer, shared by the task route and the draft route.

    ``exclude`` is the lowercased addresses already on the task. The draft
    form passes none. ``hr_visible`` is the CALLER's grant.
    """
    today = today or date.today()
    days, basis = horizon_for(due_on, today)
    body: dict[str, Any] = {
        "hr_visible": hr_visible,
        "due_on": due_on.isoformat() if due_on else None,
        "window": _window(today, days, basis),
    }
    if not hr_visible:
        # §13.4 rule 1: no `candidates` key at all, and no HR statement ran.
        return body

    pool = [
        h for h in await pool_capacity(db, vis, today=today, horizon_days=days)
        if h["in_pool"] and h["email"] not in exclude
    ]
    # Availability is the due date's, because that is the day the task needs
    # somebody, and today's for an overdue task (review round 1, P1).
    # Absences inside the window already reduced the spare hours.
    day = availability_day(due_on, today)
    for helper in pool:
        helper["away"] = _away_on(day, helper["spans"])

    ranked, note = rank_for_text(
        match_text(title, tags, description), pool, this_year=today.year,
    )
    by_email = {h["email"]: h for h in pool}
    candidates: list[dict[str, Any]] = []
    for c in ranked:
        row = c.model_dump()
        if note:
            # §13.4 rule 2: absent on EVERY candidate, never zero and never 1.
            row.pop("spare_hours", None)
        row["warnings"] = candidate_warnings(by_email.get(c.email, {}), due_on, today=today)
        candidates.append(row)

    body.update({
        "hours_scope": "all_visible_work",
        "partial": not bool(getattr(vis, "unrestricted", False)),
        "pool_size": len(pool),
        "hours_basis": note is None,
        "candidates": candidates,
    })
    if note:
        body["hours_note"] = note
    return body


def _due_of(value: Any) -> date | None:
    if value is None:
        return None
    return value.date() if hasattr(value, "date") else value


@router.get("/tasks/{task_id}/candidates")
async def task_candidates(
    task_id: str, user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Who fits this task, best first, at most three.

    404 when the caller cannot see the task. The task's assignees are left
    out of the pool, so the list never suggests somebody who already has it.
    """
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        task = await load_visible_task(db, vis, task_id)
        rows = (await db.execute(
            text(
                "SELECT lower(assignee) AS who FROM pm_task_assignees"
                " WHERE task_id = CAST(:task_id AS uuid)"
            ),
            {"task_id": str(task.id)},
        )).fetchall()
        return await candidates_body(
            db, vis,
            hr_visible=can_read_hr_fields(user),
            title=task.title,
            tags=getattr(task, "tags", None) or [],
            description=getattr(task, "description", None),
            due_on=_due_of(task.due_at),
            exclude={str(r.who) for r in rows if r.who},
        )


@router.get("/candidates")
async def draft_candidates(
    title: str = "",
    tags: str = "",
    due: str = "",
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Who fits a task that does not exist yet, for planning.

    ``tags`` is comma-separated and ``due`` is ``YYYY-MM-DD``. The body is
    the task route's for the same text and due date.
    """
    clean = (title or "").strip()
    if len(clean) < MIN_TITLE_CHARS:
        raise HTTPException(
            status_code=422,
            detail=f"title needs at least {MIN_TITLE_CHARS} characters.",
        )
    due_on: date | None = None
    if (due or "").strip():
        try:
            due_on = date.fromisoformat(due.strip()[:10])
        except ValueError:
            raise HTTPException(
                status_code=422, detail="due must be a date, YYYY-MM-DD.",
            ) from None
    tag_list = [t.strip() for t in (tags or "").split(",") if t.strip()][:MAX_DRAFT_TAGS]
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        return await candidates_body(
            db, vis,
            hr_visible=can_read_hr_fields(user),
            title=clean, tags=tag_list, description=None,
            due_on=due_on, exclude=set(),
        )

