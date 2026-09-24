"""Projects · plan preview — fit and hours for a plan that does not exist yet (WS-27bm S7d).

Spec: ``project-docs/specs/projects_ai_chat.md`` §13.2, §13.6, §10.6.

    POST /projects/plan/preview   {rows: [{key, title, owner, effort_mins,
                                           start?, due, after?}]}

The chat tool ``propose_plan`` calls this twice: once to fill the plan card,
and once after the member submits it, so the confirm card repeats every mark
for the plan the member actually signs (§13.6 rule 3). It WRITES NOTHING, so
the chat manifest lists it in ``READ_ONLY_POSTS``.

What each row gets, and where each figure comes from. Nothing here computes a
number again (§13.2 rule 1):

* **Fit for the NAMED owner** (§13.6 rule 1, owner). ``rank_candidates`` over
  that one person with the neutral spare figure 1, as
  :func:`~gateway.routes.projects.candidates.rank_for_text` does. So a skilled
  owner with no spare hours still shows their skills, and the hours are
  reported beside them. An owner who matches no skill says "no skill match".
  It never falls back to the top three of §13.4.
* **Hours ACROSS the plan** (§13.6 rule 2, owner). The owner's plan rows join
  their existing visible work in due-date order, and
  :func:`gateway.capacity.person_capacity` walks them with ``at_risk_tasks``.
  A plan row that the walk reports is marked ``short_of_hours``.
* **The warnings** are the S7b predicates (``candidate_warnings``).
* **The dependency warnings** are ``gateway.conflicts.dependency_conflict``
  over the rows' ``start`` and ``due``. The skill carries no copy of the rule.

⚠️ **No HR grant, no fit** (§13.6 rule 4). Without ``admin:members:read`` each
row carries its ``key`` and nothing else, the body says ``hr_visible: false``,
and no HR statement runs. The dependency warnings are not HR data, so every
caller gets them.

⚠️ **The body names no tenant and no member** (R5). The viewer is the
authenticated caller, and the plan rows are the only input. A body key the
model does not declare is a 422.
"""

from __future__ import annotations

import re
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
from gateway.conflicts import dependency_conflict, interval, utc_day
from gateway.routes.projects.analytics import load_open_where, load_params
from gateway.routes.projects.analytics_capacity import (
    _PEOPLE_SQL,
    capacity_dated_sql,
    capacity_totals_sql,
)
from gateway.routes.projects.candidates import candidate_warnings, match_text
from gateway.routes.projects.core import (
    STARTED_CATEGORY,
    _tenant_session,
    resolve_visibility,
    router,
)
from gateway.routes.tasks.core import can_read_hr_fields
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

#: The most rows one preview takes. It is the chat's ``MAX_BATCH``: one card
#: never lists more tasks than this, so a preview never needs to either.
MAX_PLAN_ROWS = 50

#: A key is the model's own label for a row, for example ``t1``. It travels in
#: a sentence, so it is held to a plain alphabet.
_KEY = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")

#: The longest effort one row may carry, in minutes: a quarter of full days.
MAX_EFFORT_MINS = 90 * 24 * 60

#: §13.2 rule 3 and §13.6 rule 4. The one line a planner without the grant sees.
HR_NOTE = (
    "An admin can see capacity. Fit, hours and marks need the"
    " admin:members:read grant."
)

#: The owner decision in the S7d dispatch (gap 2). The plan rows carry an
#: effort, so the walk always has a basis. But when none of the owner's other
#: open work is estimated, the walk counts the plan alone.
ESTIMATE_NOTE = (
    "None of this owner's other open work carries an estimate, so these hours"
    " count the plan rows only."
)


class PlanRowIn(BaseModel):
    """One plan row. ``after`` lists the keys of the rows that block it."""

    model_config = ConfigDict(extra="forbid")

    key: str
    title: str
    owner: str
    effort_mins: int = Field(ge=0, le=MAX_EFFORT_MINS)
    start: str | None = None
    due: str
    after: list[str] = Field(default_factory=list)


class PlanPreviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rows: list[PlanRowIn]


def _day(value: str | None, what: str, key: str) -> date | None:
    if value is None or not str(value).strip():
        return None
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        raise HTTPException(
            status_code=422, detail=f"Row {key}: {what} must be a date, YYYY-MM-DD.",
        ) from None


def checked_rows(body: PlanPreviewIn) -> list[dict[str, Any]]:
    """The rows as plain dicts, or a 422 that names the row.

    Refused BEFORE a session opens: more than :data:`MAX_PLAN_ROWS` rows, a
    bad or repeated key, a date that is not one, a start after the due date,
    and an ``after`` key that names no row or the row itself. A cycle is not
    refused here, because a preview writes nothing. The skill refuses a cycle
    before its card, and ``assert_no_block_cycle`` guards the link write.
    """
    rows = body.rows
    if not rows:
        raise HTTPException(status_code=422, detail="A plan needs at least one row.")
    if len(rows) > MAX_PLAN_ROWS:
        raise HTTPException(
            status_code=422,
            detail=f"That is {len(rows)} rows. A plan preview takes at most {MAX_PLAN_ROWS}.",
        )
    keys = [r.key.strip() for r in rows]
    for key in keys:
        if not _KEY.match(key):
            raise HTTPException(
                status_code=422,
                detail="A row key is 1 to 40 letters, digits, dots, dashes or underscores.",
            )
    if len(set(keys)) != len(keys):
        raise HTTPException(status_code=422, detail="Two rows carry the same key.")
    known = set(keys)
    out: list[dict[str, Any]] = []
    for key, row in zip(keys, rows, strict=True):
        due = _day(row.due, "due", key)
        if due is None:
            raise HTTPException(status_code=422, detail=f"Row {key}: due is required.")
        start = _day(row.start, "start", key)
        if start is not None and start > due:
            raise HTTPException(
                status_code=422, detail=f"Row {key}: the start date is after the due date.",
            )
        after = [a.strip() for a in row.after if a.strip()]
        for blocker in after:
            if blocker == key:
                raise HTTPException(status_code=422, detail=f"Row {key} cannot block itself.")
            if blocker not in known:
                raise HTTPException(
                    status_code=422, detail=f"Row {key}: after names no row called {blocker}.",
                )
        if not row.title.strip() or not row.owner.strip():
            raise HTTPException(
                status_code=422, detail=f"Row {key}: a row needs a title and an owner.",
            )
        out.append({
            "key": key,
            "title": row.title.strip(),
            "owner": row.owner.strip().lower(),
            "effort_mins": int(row.effort_mins),
            "start": start,
            "due": due,
            "after": after,
        })
    return out


def plan_window(rows: list[dict[str, Any]], today: date) -> tuple[int, dict[str, Any]]:
    """The window the hours cover: today to the plan's last due date.

    Clamped to 1..90 days, the S7a bounds (§13.2 rule 5). A row due after
    the window is not walked, and its row says so.
    """
    last = max(r["due"] for r in rows)
    days = max(MIN_HORIZON_DAYS, min(MAX_HORIZON_DAYS, (last - today).days))
    _, end = horizon_window(today, days)
    return days, {
        "starts_on": today.isoformat(),
        "ends_on": end.isoformat(),
        "days": days,
        "basis": "plan",
    }


def dependency_warnings(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every ``after`` pair whose dates disagree, by the one rule (D-PM-12).

    ``gateway.conflicts.dependency_conflict`` decides. A plan row is not
    completed, so the rule's completed-blocker case never applies here.
    """
    by_key = {r["key"]: r for r in rows}
    out: list[dict[str, Any]] = []
    for row in rows:
        blocked = {"start_date": row["start"], "due_at": row["due"]}
        for key in row["after"]:
            other = by_key[key]
            blocker = {"start_date": other["start"], "due_at": other["due"]}
            if not dependency_conflict(blocker, blocked):
                continue
            ends = interval(blocker)
            starts = interval(blocked)
            assert ends is not None and starts is not None
            out.append({
                "kind": "dependency_order",
                "blocker": key,
                "blocked": row["key"],
                "blocker_ends": ends[1].isoformat(),
                "blocked_starts": starts[0].isoformat(),
                "sentence": (
                    f"Row {row['key']} starts on {starts[0].isoformat()}, but row {key},"
                    f" which blocks it, runs until {ends[1].isoformat()}."
                ),
            })
    return out


def _combined_totals(totals: Any, plan: list[dict[str, Any]], today: date) -> dict[str, Any]:
    """The owner's open-work aggregates with the plan rows added.

    The shape :func:`gateway.capacity.person_capacity` reads. A plan row is
    one more open, estimated task.
    """

    def field(name: str) -> int:
        if totals is None:
            return 0
        value = totals.get(name) if isinstance(totals, dict) else getattr(totals, name, 0)
        return int(value or 0)

    return {
        "open_tasks": field("open_tasks") + len(plan),
        "mins": field("mins") + sum(int(r["effort_mins"]) for r in plan),
        "unestimated": field("unestimated"),
        "overdue": field("overdue") + sum(1 for r in plan if r["due"] < today),
        "next_due": None,
    }


def _plan_dated(plan: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The owner's plan rows in the shape the at-risk walk reads."""
    return [
        {
            "id": f"plan:{r['key']}",
            "title": r["title"],
            "due_at": r["due"],
            "estimate_mins": r["effort_mins"],
            "project_name": None,
            "_due": r["due"],
        }
        for r in plan
    ]


def _fit(row: dict[str, Any], record: Any, skills: list[dict[str, Any]], today: date) -> dict:
    """The named owner's skill match for one row (§13.6 rule 1).

    ``rank_candidates`` over this one person, with the neutral spare figure 1.
    The ranker drops a person with no spare hours, and a full owner is still
    skilled, so the real hours travel separately.
    """
    from gateway.routes.people.suggestions import rank_candidates

    helper = {
        "person_id": str(record.id),
        "name": record.name,
        "email": str(record.email),
        "skill_rows": skills,
        "spare_hours": 1.0,
        "away": None,
    }
    ranked = rank_candidates(
        match_text(row["title"], [], None), [helper], this_year=today.year,
    )
    if not ranked:
        return {"matched": False, "skills": [], "text": "no skill match"}
    skills_hit = list(ranked[0].matched_skills)
    return {"matched": True, "skills": skills_hit, "text": ", ".join(skills_hit)}


async def plan_preview_body(
    db: Any,
    vis: Any,
    *,
    hr_visible: bool,
    rows: list[dict[str, Any]],
    today: date | None = None,
) -> dict[str, Any]:
    """The preview answer. ``rows`` come from :func:`checked_rows`."""
    today = today or date.today()
    days, window = plan_window(rows, today)
    body: dict[str, Any] = {
        "hr_visible": hr_visible,
        "window": window,
        "dependency_warnings": dependency_warnings(rows),
        "rows": [{"key": r["key"]} for r in rows],
    }
    if not hr_visible:
        # §13.6 rule 4: no fit, no hours and no mark, and no HR statement ran.
        body["hr_note"] = HR_NOTE
        return body

    from gateway.work_schedule import load_policy, person_schedule

    emails = sorted({r["owner"] for r in rows if "@" in r["owner"]})
    directory: dict[str, Any] = {}
    if emails:
        for rec in (await db.execute(text(_PEOPLE_SQL), {"emails": emails})).fetchall():
            directory.setdefault(str(rec.email), rec)
    known = [e for e in emails if e in directory]

    totals: dict[str, Any] = {}
    dated: dict[str, list[dict[str, Any]]] = {}
    spans_of: dict[str, list[dict[str, Any]]] = {}
    skills_of: dict[str, list[dict[str, Any]]] = {}
    policy: Any = None
    if known:
        # The hours read every open task the caller can see, in any project
        # (§13.3, as S7a and S7c do). Work outside the grant is not read.
        where = load_open_where("TRUE", vis)
        params = {**load_params(vis, None), "holders": known, "started_cat": STARTED_CATEGORY}
        totals = {
            str(r.who): r
            for r in (await db.execute(text(capacity_totals_sql(where)), params)).fetchall()
        }
        dated_params = {**params, "until": dated_until(today, days)}
        dated_params.pop("started_cat")
        for r in (await db.execute(text(capacity_dated_sql(where)), dated_params)).fetchall():
            dated.setdefault(str(r.who), []).append({
                "id": str(r.id),
                "title": r.title,
                "due_at": r.due_at,
                "estimate_mins": r.estimate_mins,
                "project_name": getattr(r, "project_name", None),
                "_due": utc_day(r.due_at),
            })
        policy = await load_policy(db)
        ids = [str(directory[e].id) for e in known]
        by_id = await absences_for(db, ids)
        skill_rows = await skill_rows_for(db, ids)
        for email in known:
            pid = str(directory[email].id)
            spans_of[email] = by_id.get(pid, [])
            skills_of[email] = skill_rows.get(pid, [])

    _, horizon_end = horizon_window(today, days)
    owner_rows: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        owner_rows.setdefault(r["owner"], []).append(r)

    walked: dict[str, dict[str, Any]] = {}
    for email in known:
        record = directory[email]
        schedule = person_schedule(policy or {}, record)
        spans = spans_of[email]
        base = person_capacity(
            schedule=schedule, spans=spans, totals=totals.get(email),
            dated=dated.get(email, []), today=today, horizon_days=days,
        )
        plan = owner_rows.get(email, [])
        across = person_capacity(
            schedule=schedule, spans=spans,
            totals=_combined_totals(totals.get(email), plan, today),
            dated=dated.get(email, []) + _plan_dated(plan),
            today=today, horizon_days=days,
        )
        risky = {
            str(a["task_id"])[len("plan:"):]: a
            for a in across["at_risk"] if str(a.get("task_id") or "").startswith("plan:")
        }
        work = totals.get(email)
        open_before = int(getattr(work, "open_tasks", 0) or 0) if work else 0
        unestimated = int(getattr(work, "unestimated", 0) or 0) if work else 0
        walked[email] = {
            "record": record,
            "base": base,
            "across": across,
            "risky": risky,
            "unestimated_only": open_before > 0 and unestimated >= open_before,
            "helper": {
                "spans": spans,
                "end_date": getattr(record, "end_date", None),
                "max_concurrent_tasks": getattr(record, "max_concurrent_tasks", None),
                "in_progress": int(getattr(work, "in_progress", 0) or 0) if work else 0,
            },
        }

    out_rows: list[dict[str, Any]] = []
    for r in rows:
        out_rows.append(_row_answer(r, walked.get(r["owner"]), skills_of, today, horizon_end))
    body.update({
        "hours_scope": "all_visible_work",
        "partial": not bool(getattr(vis, "unrestricted", False)),
        "rows": out_rows,
    })
    return body


def _row_answer(
    r: dict[str, Any],
    owner: dict[str, Any] | None,
    skills_of: dict[str, list[dict[str, Any]]],
    today: date,
    horizon_end: date,
) -> dict[str, Any]:
    """One row's fit, hours and marks, for a caller who holds the grant."""
    who = r["owner"]
    if who.startswith("agent:"):
        return {
            "key": r["key"], "owner": who,
            "fit": {"matched": False, "agent": True, "skills": [], "text": "an agent"},
            "hours": {"basis": False, "note": "An agent has no working hours."},
            "marks": [],
        }
    if owner is None:
        return {
            "key": r["key"], "owner": who,
            "fit": {"matched": False, "in_directory": False, "skills": [],
                    "text": "not in the directory"},
            "hours": {"basis": False, "note": "The directory has no schedule for this owner."},
            "marks": ["not_in_directory"],
        }
    record = owner["record"]
    fit = _fit(r, record, skills_of.get(who, []), today)
    fit["warnings"] = candidate_warnings(owner["helper"], r["due"], today=today)
    marks: list[str] = [] if fit["matched"] else ["no_skill_match"]
    base, across = owner["base"], owner["across"]
    hours: dict[str, Any]
    if not across["hours_basis"]:
        hours = {"basis": False, "note": "The directory holds no contracted hours for this owner."}
    else:
        hours = {"basis": True}
        if base["hours_basis"]:
            # The owner's spare hours BEFORE the plan. With no estimate on
            # their other work, a spare figure is a zero read as "free"
            # (§13.2 rule 4), so it is left out and the note says why.
            hours["spare_hours"] = base["spare_horizon"]
        risk = owner["risky"].get(r["key"])
        if risk is not None:
            hours.update({
                "fits": False,
                "needed_hours": risk["needed_hours"],
                "available_hours": risk["available_hours"],
                "shortfall_hours": risk["shortfall_hours"],
            })
            marks.append("short_of_hours")
        elif r["due"] < today:
            hours.update({"fits": None, "note": "The due date is in the past."})
        elif r["due"] > horizon_end:
            hours.update({"fits": None, "note": "Due after the window, so it was not walked."})
        else:
            hours["fits"] = True
        if owner["unestimated_only"]:
            hours["estimate_note"] = ESTIMATE_NOTE
    return {
        "key": r["key"], "owner": who, "name": record.name,
        "fit": fit, "hours": hours, "marks": marks,
    }


@router.post("/plan/preview")
async def plan_preview(
    body: PlanPreviewIn, user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Each plan row's owner fit, the hours across the plan, and the
    dependency warnings. Writes nothing.

    The HR half needs ``admin:members:read``. Without it, the body carries
    each row's key, the dependency warnings and one note.
    """
    rows = checked_rows(body)
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        return await plan_preview_body(
            db, vis, hr_visible=can_read_hr_fields(user), rows=rows,
        )
