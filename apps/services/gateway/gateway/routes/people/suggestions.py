"""People Center · the rebalancing suggestions (WS-28j3).

Spec: ``project-docs/specs/people_center_app.md`` §5.7.4 · **D-PC-13, D-PC-14**.

    GET /people/dashboard/suggestions   → who could help whom, with the numbers

The part the owner's directive was really asking for: *"suggestions about what
else can be assigned depending on capability, or what people who are idle can
help people who are behind with."* Two lists, and the join between them is the
one thing this surface can compute that no individual could:

* **For each at-risk task** — candidate helpers, ranked by *skill overlap with
  that task* x *spare hours this week* x *availability*. Each candidate shows
  ALL THREE numbers and the matched skill, because a ranking whose reasoning is
  hidden cannot be argued with, and the person reading it knows things the
  record does not.
* **For each idle person** — what they could pick up: unassigned open tasks in
  projects the VIEWER can see that match their skills, plus the at-risk tasks
  above where they are a credible helper.

**The ranker is §5.5's** — :func:`~gateway.routes.people.search.score_skills`,
called with the task's text instead of a typed query. A second ranker would be
a second answer to "who is good at this", which is the drift §5.5 exists to
prevent; the multiplication by spare hours and availability happens HERE, on
top of the shared skill score, and every factor travels on the row.

⚠️ **Every suggestion ends in a pre-filled assign action a human confirms.**
Nothing in this module writes anything (D-PC-13, fenced with the
prose-stripping grep) — the row carries the task id and the candidate's
address, and the click goes through the Projects app's ordinary assignees PUT.

⚠️ **Still not a ranking of people** (D-PC-14). Candidates are ranked FOR ONE
TASK, by fit-for-that-task — the same person ranks first for a firmware task
and last for a sales deck. There is no cross-task score and no leaderboard.
"""

from __future__ import annotations

from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends
from gateway.routes.people.core import _tenant_session, router
from gateway.routes.people.search import score_skills
from pydantic import BaseModel
from sqlalchemy import text

#: Caps, each logged in the response via ``partial`` flags rather than silent.
MAX_AT_RISK_TASKS = 8
MAX_CANDIDATES_PER_TASK = 3
MAX_UNASSIGNED_TASKS = 50
MAX_PICKUPS_PER_PERSON = 4

#: Availability multiplier for somebody away at any point this week. Not zero:
#: they are back within days and the reader sees the away warning beside the
#: number — zero would silently erase a strong match the reader might well
#: still choose, and the multiplication is shown, not hidden.
AWAY_FACTOR = 0.25


class Candidate(BaseModel):
    person_id: str | None
    name: str
    email: str
    #: The three factors, each visible (§5.7.4).
    skill_points: float
    matched_skills: list[str]
    spare_hours: float
    away: dict[str, Any] | None = None
    #: skill x spare x availability — the product the list is ordered by,
    #: recomputable from the three factors above.
    rank: float


class AtRiskSuggestion(BaseModel):
    task_id: str
    title: str
    project_name: str | None = None
    due_on: str | None = None
    shortfall_hours: float | None = None
    holder: dict[str, Any]
    candidates: list[Candidate]


class PickupSuggestion(BaseModel):
    person_id: str | None
    name: str
    email: str
    #: Unassigned tasks matching their skills + at-risk tasks where they are a
    #: credible helper — the idle↔behind join.
    tasks: list[dict[str, Any]]


class SuggestionsResponse(BaseModel):
    at_risk: list[AtRiskSuggestion]
    pickups: list[PickupSuggestion]
    #: True when a cap trimmed the lists — a silent cap reads as "covered
    #: everything" when it did not.
    truncated: bool = False
    partial: bool = False


def rank_candidates(
    task_title: str,
    helpers: list[dict[str, Any]],
    *,
    this_year: int,
    exclude_email: str | None = None,
) -> list[Candidate]:
    """The §5.7.4 ranking: §5.5's skill score x spare hours x availability.

    Pure, so the tests are numbers. ``helpers`` rows carry ``email``, ``name``,
    ``person_id``, ``skill_rows``, ``spare_hours``, ``away``. A candidate with
    no skill overlap is not credible and is dropped rather than ranked at the
    bottom — offering a random free colleague is how suggestions teach people
    to ignore them. So is one with no spare hours: help that does not exist
    cannot be suggested.
    """
    out: list[Candidate] = []
    for helper in helpers:
        email = (helper.get("email") or "").strip().lower()
        if not email or email == (exclude_email or ""):
            continue
        points, matched = score_skills(
            task_title, helper.get("skill_rows") or [], this_year)
        if points <= 0:
            continue
        spare = float(helper.get("spare_hours") or 0.0)
        if spare <= 0:
            continue
        away = helper.get("away")
        factor = AWAY_FACTOR if away else 1.0
        out.append(Candidate(
            person_id=helper.get("person_id"), name=helper.get("name") or email,
            email=email, skill_points=points,
            matched_skills=[m["skill"] for m in matched],
            spare_hours=round(spare, 1), away=away,
            rank=round(points * spare * factor, 2),
        ))
    out.sort(key=lambda c: (-c.rank, c.name))
    return out[:MAX_CANDIDATES_PER_TASK]


@router.get("/dashboard/suggestions", response_model=SuggestionsResponse)
async def get_suggestions(
    user: UserContext = Depends(get_current_user),
) -> SuggestionsResponse:
    """The rebalancing lists, computed over the dashboard's OWN rows.

    Reuses :func:`~gateway.routes.people.dashboard.get_dashboard` wholesale —
    the j2 rule again: a suggester that recomputed "who is at risk" would be a
    second answer to it. The same gate applies transitively
    (``admin:members:read``), refused there.
    """
    from datetime import date

    from gateway.routes.people.dashboard import get_dashboard

    board = await get_dashboard(user)
    this_year = date.today().year

    people_rows = [r for r in board.rows if r.kind != "agent" and r.email]

    async with _tenant_session() as db:
        skills = (await db.execute(text(
            "SELECT person_id, skill, level, last_used_year "
            "  FROM people_skills"))).fetchall()
        unassigned = []
        if board.work_visible:
            # Scoped by the VIEWER's grant closure (§5.7.4: "projects the
            # viewer can see") — the dashboard's own `_scope`, including its
            # refusal to short-circuit `data:org:read` to TRUE. A pickup
            # suggestion naming a task the viewer cannot open would leak the
            # title of exactly the work the closure exists to hide.
            from gateway.routes.people.dashboard import _scope, _visibility

            vis = await _visibility(db, user)
            params: dict[str, Any] = {"cap": MAX_UNASSIGNED_TASKS}
            scope = _scope(vis, params)
            try:
                unassigned = (await db.execute(text(
                    "SELECT t.id, t.title, t.due_at, t.estimate_mins, "
                    "       p.name AS project_name "
                    "  FROM pm_tasks t "
                    "  JOIN pm_task_statuses s ON s.id = t.status_id "
                    "  LEFT JOIN pm_projects p ON p.id = t.project_id "
                    " WHERE t.archived_at IS NULL "
                    "   AND s.category NOT IN ('done', 'cancelled') "
                    "   AND NOT EXISTS (SELECT 1 FROM pm_task_assignees a "
                    "                    WHERE a.task_id = t.id) "
                    "   AND " + scope +
                    " ORDER BY t.due_at NULLS LAST LIMIT :cap"),
                    params)).fetchall()
            except Exception:
                unassigned = []

    skills_by_person: dict[str, list[dict[str, Any]]] = {}
    for row in skills:
        skills_by_person.setdefault(str(row.person_id), []).append({
            "skill": row.skill, "level": row.level,
            "last_used_year": row.last_used_year})

    helpers = [
        {
            "person_id": r.person_id, "name": r.name,
            "email": (r.email or "").lower(),
            "skill_rows": skills_by_person.get(r.person_id or "", []),
            # The HORIZON window, not the calendar week — help is needed before
            # the deadline, and this-week spare is zero for everyone every
            # Saturday (the first weekend live run proved it).
            "spare_hours": r.spare_hours_horizon or 0.0,
            "away": r.away,
        }
        for r in people_rows
    ]

    # ── The join: helpers per at-risk task, pickups per idle person ────────
    #
    # ⚠️ The join itself is `gateway.capacity.rebalance_join` since WS-27bm
    # S7b, shared with `/projects/analytics/rebalance` (§13.4 rule 6). This
    # function keeps its own rows and its own ranker, which is plain
    # `rank_candidates` over the task title, so its output did not change.
    from gateway.capacity import rebalance_join

    def _rank(text_: str, pool: list[dict[str, Any]], holder: str):
        return (
            rank_candidates(text_, pool, this_year=this_year, exclude_email=holder),
            None,
        )

    joined = rebalance_join(
        at_risk=[
            {
                "task_id": str(task.get("task_id")),
                "title": str(task.get("title") or ""),
                "project_name": task.get("project_name"),
                "due_on": task.get("due_on"),
                "shortfall_hours": task.get("shortfall_hours"),
                "holder": {"person_id": row.person_id, "name": row.name,
                           "email": row.email},
            }
            for row in people_rows for task in row.at_risk
        ],
        helpers=helpers,
        idle=[
            {"person_id": r.person_id, "name": r.name, "email": r.email,
             "skill_rows": skills_by_person.get(r.person_id or "", [])}
            for r in people_rows if r.pill == "idle"
        ],
        unassigned=[
            {"task_id": str(t.id), "title": t.title,
             "project_name": getattr(t, "project_name", None)}
            for t in unassigned
        ],
        this_year=this_year,
        rank=_rank,
        max_at_risk=MAX_AT_RISK_TASKS,
        max_pickups=MAX_PICKUPS_PER_PERSON,
    )

    at_risk = [
        AtRiskSuggestion(
            task_id=s["task"]["task_id"], title=s["task"]["title"],
            project_name=s["task"]["project_name"], due_on=s["task"]["due_on"],
            shortfall_hours=s["task"]["shortfall_hours"],
            holder=s["task"]["holder"], candidates=s["candidates"],
        )
        for s in joined["at_risk"]
    ]
    pickups = [PickupSuggestion(**p) for p in joined["pickups"]]

    return SuggestionsResponse(
        at_risk=at_risk, pickups=pickups,
        truncated=(joined["total_at_risk"] > MAX_AT_RISK_TASKS
                   or len(unassigned) >= MAX_UNASSIGNED_TASKS),
        partial=board.partial,
    )
