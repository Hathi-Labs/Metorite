"""Projects · reports — a saved question, rendered from §9.12.7's own numbers.

Spec: ``project-docs/specs/project_management_app.md`` §9.12.8.

    GET    /projects/reports              → every report the caller may open
    GET    /projects/reports/templates    → the template catalogue (R2)
    POST   /projects/reports              → save a definition
    GET    /projects/reports/{id}
    PATCH  /projects/reports/{id}
    DELETE /projects/reports/{id}
    GET    /projects/reports/{id}/render  → the body, computed now
    POST   /projects/reports/preview      → the body of an unsaved config

**The owner drew the line: analytics is what you look at, a report is what
gets delivered.** So this is built ON §9.12.7 and after it.

⚠️ **A REPORT STORES THE QUESTION, NEVER THE ANSWER.** Every section below
re-runs `analytics.py`'s own SQL at render time. §9.12.8 says a report with no
analytics behind it "would mint a second set of numbers, and two sets of
numbers disagree" — and a cached `results` column would be exactly that, ageing
quietly while the dashboard beside it moved on.

⚠️ **Render first, deliver second — §9.12.8's own order.** This slice is the
render. There are no recipients here, no schedule and no send. Those are
additive columns in a later migration (R6), and building them now would arm a
delivery path with nothing rendering through it.

⚠️ **The period a REPORT wants is not the period a dashboard wants.** A weekly
report describes a week that ENDED. §9.12.7(d) settled the mechanism —
`skip_current_week` — and `_DEFAULTS` below turns it ON, because a saved
definition that re-reports the running week sends a different number every
time nobody did anything.
"""

from __future__ import annotations

import copy
import os
from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException
from gateway.routes.projects.analytics import (
    MAX_PEOPLE,
    MAX_WEEKS,
    STALE_BANDS,
    _hours,
    cycle_summary_sql,
    finished_period_sql,
    finished_sql,
    hygiene_body,
    load_sql,
    outlook_body,
    overdue_by_project_sql,
    scope_clause,
    scope_params,
    stale_bands_sql,
    total_open_sql,
    weekly_sql,
)
from gateway.routes.projects.analytics_capacity import capacity_body
from gateway.routes.projects.analytics_conflicts import conflicts_body
from gateway.routes.projects.analytics_rebalance import rebalance_body
from gateway.routes.projects.core import (
    CLOSING_CATEGORIES,
    COMPLETED_CATEGORY,
    REPORTABLE_STATUSES,
    STARTED_CATEGORY,
    _tenant_session,
    actor,
    insert_row,
    load_visible_project,
    reportable_with_ancestors_clause,
    require_row,
    resolve_visibility,
    router,
    row_to_dict,
    task_visibility_clause,
    triage_exclusion_clause,
    update_row,
)
from gateway.routes.tasks.core import can_read_hr_fields
from pydantic import BaseModel
from sqlalchemy import text

#: The sections a definition may ask for, and the order they render in.
#:
#: ⚠️ **Order is DECLARED here, not taken from the caller's list.** A report
#: that renders its sections in whatever order they were saved reads
#: differently every time somebody edits it, and two people comparing last
#: week's copy to this week's would find the difference in the layout rather
#: than in the numbers. What we finished leads, because that is the answer the
#: reader came for.
#:
#: `capacity` (WS-27bm S7a) sits beside `load`, because it reads the same open
#: work and names who has the hours for it. `conflicts` (WS-27bm S7c) comes
#: after `stuck`: both say what is wrong with the open work, and conflicts
#: says what is wrong with the plan for it.
#:
#: WS-27bn R3 declares the whole order (`projects_reports.md` §8 R3):
#: finished, throughput, outlook, load, capacity, pulse, stuck, hygiene,
#: conflicts, rebalance. Each slice adds its own name in that place. R3a adds
#: `outlook`, after the past and before the open work, because a forecast
#: reads the rate that the two sections above it measured. R3b adds
#: `rebalance` LAST: it says what to do about the problems the sections
#: above it found. R3c adds `hygiene` after `stuck`: both read the open work,
#: and hygiene says which tasks lack the data the other sections need.
SECTIONS: tuple[str, ...] = (
    "finished", "throughput", "outlook", "load", "capacity", "stuck",
    "hygiene", "conflicts", "rebalance",
)

#: The sections a definition with no `sections` key renders.
#:
#: ⚠️ **An explicit list, never `list(SECTIONS)`.** `capacity` is OPT-IN
#: (`projects_ai_chat.md` §13.3 rule 4), and so is `conflicts` (§13.5 rule 12). While this was `list(SECTIONS)`,
#: adding a section to the vocabulary added it to every saved report that
#: never asked for it — and capacity carries hours, which is not what a
#: weekly delivery report was saved to say.
DEFAULT_SECTIONS: tuple[str, ...] = ("finished", "throughput", "load", "stuck")

#: What a definition means when it does not say.
#:
#: `skip_current_week` is TRUE here and FALSE on the dashboard, and the
#: difference is the whole of §9.12.8's shape. See the module header.
_DEFAULTS: dict[str, Any] = {
    "weeks": 1,
    "skip_current_week": True,
    "include_subtree": True,
    "sections": list(DEFAULT_SECTIONS),
}

#: Any scope a report can take once R5 adds people and teams.
_ANY_SCOPE: tuple[str, ...] = ("person", "team", "project", "org")


def _coming(
    key: str, name: str, question: str, scope_kinds: tuple[str, ...],
    waits_for: str,
) -> dict[str, Any]:
    """A template the gallery shows and a member cannot choose yet."""
    return {
        "key": key, "name": name, "question": question,
        "scope_kinds": list(scope_kinds), "available": False,
        "waits_for": waits_for,
    }


#: The template catalogue (`projects_reports.md` §4), in catalogue order.
#:
#: WS-27bn R2. A template is a named question with a preset of sections and a
#: period. The builder, the chat and the email read this one map. The client
#: holds NO copy of it: `GET /projects/reports/templates` serves it.
#:
#: ⚠️ **The keys are APPEND-ONLY.** A saved report keeps its template key in
#: `config.template`, and `normalise_report_config` runs on every READ. A key
#: removed here makes every list and render of such a report answer 422.
#: `test_projects_report_sections_lockstep.py` pins the set of 13.
#:
#: ⚠️ **An unavailable template carries NO sections.** It waits for a section,
#: a scope or a period that does not exist yet, and `waits_for` names it in
#: words a member reads on the gallery card (§8 names the slice). A
#: preset that named a missing section would be a save the server refuses.
#: `weekly_delivery` (T4) is live since R2, `project_status` (T5) since
#: R3a, and `data_hygiene` (T13) since R3c. T11 waits for a filter on the conflict kind, because `conflicts_body`
#: takes no kinds argument.
TEMPLATES: dict[str, dict[str, Any]] = {
    t["key"]: t
    for t in (
        _coming(
            "team_pulse", "Team pulse",
            "How is each person on the team today, and who needs help?",
            ("team", "project", "org"),
            "The pulse section and a today period",
        ),
        _coming(
            "my_day", "My day",
            "What do I work on today, and what waits on me?",
            ("me",),
            "The pulse section and the person scope",
        ),
        _coming(
            "what_changed", "What changed",
            "What happened since I last read this?",
            _ANY_SCOPE,
            "Stored runs and the changes section",
        ),
        {
            "key": "weekly_delivery", "name": "Weekly delivery",
            "question": "What did we finish last week, and how fast?",
            "scope_kinds": ["project", "org"], "available": True,
            # Exactly DEFAULT_SECTIONS. The lockstep test pins the equality.
            "sections": ["finished", "throughput", "load", "stuck"],
            "weeks": 1, "skip_current_week": True,
        },
        {
            # WS-27bn R3a. "This week" is the running week: one week, with
            # the current week kept. The sections are in SECTIONS order.
            "key": "project_status", "name": "Project status",
            "question": "Will this project finish on time, and what blocks it?",
            "scope_kinds": ["project"], "available": True,
            "sections": ["finished", "outlook", "stuck", "conflicts"],
            "weeks": 1, "skip_current_week": False,
        },
        _coming(
            "one_on_one", "1:1 prep",
            "How is this person doing over a month?",
            ("person",),
            "The pulse section and the person scope",
        ),
        _coming(
            "exceptions", "Exceptions",
            "What is wrong right now, and nothing else?",
            _ANY_SCOPE,
            "A today period, and a rule for which hygiene rows are high",
        ),
        _coming(
            "capacity_outlook", "Capacity outlook",
            "Do we have the people for the next weeks?",
            ("team", "org"),
            "The outlook section, and a forward period",
        ),
        _coming(
            "stakeholder_update", "Stakeholder update",
            "A short project summary to send outside the team",
            ("project",),
            "The outlook section, and a body with no per-person rows",
        ),
        _coming(
            "portfolio_health", "Portfolio health",
            "Which projects are healthy?",
            ("org",),
            "The outlook section, and a this-month period",
        ),
        _coming(
            "focus_switching", "Focus and switching",
            "Who is spread over too many projects?",
            ("team", "org"),
            "A filter on the kind of conflict",
        ),
        _coming(
            "retrospective", "Retrospective",
            "What slipped in the period, and why?",
            ("project",),
            "Stored runs and the changes section",
        ),
        {
            # WS-27bn R3c. The section reads the state now and ignores the
            # period, so one week with the current week kept says "now".
            "key": "data_hygiene", "name": "Data hygiene",
            "question": "Which tasks make every other report wrong?",
            "scope_kinds": ["project", "org"], "available": True,
            "sections": ["hygiene"],
            "weeks": 1, "skip_current_week": False,
        },
    )
}

#: A name long enough to be useful and short enough for a subject line.
#: The schedules the job can read. NULL in the table means "not scheduled",
#: which is a different thing from a word the job does not recognise.
SCHEDULES: tuple[str, ...] = ("weekly",)


def delivery_armed() -> bool:
    """Whether THIS deployment may send a report at all.

    ⚠️ The second of two locks. A member can arm a report's schedule; only the
    deployment can arm delivery, and §9.12.8 makes that the owner's: *"the
    schedule is owner-gated to arm. Build it dark, default off."*

    An exact match on `"true"`. `bool("false")` is `True`, and a flag that arms
    on the string "false" is the worst possible default for something that
    mails real people. The same rule `isReportEmailEnabled` applies on the
    other side of the seam.
    """
    return os.environ.get("PROJECT_REPORT_EMAIL_ENABLED", "") == "true"


MAX_NAME = 120


def normalise_report_config(raw: Any) -> dict[str, Any]:
    """Validate a saved definition, and fill what it leaves out.

    ⚠️ **It REJECTS rather than repairs an unknown section.** The instinct is
    to drop it and carry on, and that is wrong here: a report is read by
    somebody who was not in the room when it was saved. A section that silently
    vanishes leaves them with a document that is missing a part nobody can see
    is missing. `pm_views` can shrug off an unknown key because a view is a
    live surface the author is looking at; a report is not.

    Every bound is re-checked here rather than trusted from the row, because a
    definition saved before a limit changed is still in the table.
    """
    config = dict(_DEFAULTS)
    if raw is None:
        return config
    if not isinstance(raw, dict):
        raise HTTPException(
            status_code=422, detail="config must be an object.",
        )

    if "sections" in raw:
        asked = raw["sections"]
        if not isinstance(asked, list) or not asked:
            raise HTTPException(
                status_code=422,
                detail="config.sections must be a non-empty list.",
            )
        unknown = [s for s in asked if s not in SECTIONS]
        if unknown:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Unknown report section(s): {', '.join(map(str, unknown))}."
                    f" Known sections: {', '.join(SECTIONS)}."
                ),
            )
        # Declared order, de-duplicated. See SECTIONS.
        config["sections"] = [s for s in SECTIONS if s in set(asked)]

    if "weeks" in raw:
        try:
            weeks = int(raw["weeks"])
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=422, detail="config.weeks must be a whole number.",
            ) from None
        if not 1 <= weeks <= MAX_WEEKS:
            raise HTTPException(
                status_code=422,
                detail=f"config.weeks must be between 1 and {MAX_WEEKS}.",
            )
        config["weeks"] = weeks

    for flag in ("skip_current_week", "include_subtree"):
        if flag in raw:
            if not isinstance(raw[flag], bool):
                raise HTTPException(
                    status_code=422, detail=f"config.{flag} must be true or false.",
                )
            config[flag] = raw[flag]

    # WS-27bn R2. The template is an ORIGIN LABEL, and it presets nothing
    # here: a member may change the sections or the period and keep the
    # key. An absent or null template adds NO key, so a config saved with no
    # template holds exactly what R1 saved.
    template = raw.get("template")
    if template is not None:
        if not isinstance(template, str) or template not in TEMPLATES:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Unknown report template: {template!r}."
                    f" Known templates: {', '.join(TEMPLATES)}."
                ),
            )
        if not TEMPLATES[template]["available"]:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"The template {template!r} is coming soon. It waits for:"
                    f" {TEMPLATES[template]['waits_for']}."
                ),
            )
        config["template"] = template

    return config


def _clean_name(raw: Any) -> str:
    name = str(raw or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="A report needs a name.")
    if len(name) > MAX_NAME:
        raise HTTPException(
            status_code=422,
            detail=f"A report name is at most {MAX_NAME} characters.",
        )
    return name


async def _visible_report(db: Any, vis: Any, report_id: str) -> Any:
    """One report, or 404.

    The tenant is enforced by RLS on the row (migration 204), so a report from
    another organization is already invisible here. What this adds is the
    PROJECT check: a report scoped to a node the caller cannot see must not
    answer, because its name and its period describe that node.
    """
    row = await require_row(db, "pm_reports", report_id, "Report")
    if row.project_id is not None:
        await load_visible_project(db, vis, str(row.project_id))
    return row


class ReportModel(BaseModel):
    """One saved report, as the wire sees it.

    ⚠️ **This model did not exist, and `_report_dict` called
    `row_to_dict(row)` with no model at all.** `row_to_dict(row, model)` has
    always taken two arguments, so EVERY call raised `TypeError` and
    `POST /projects/reports` answered 500 from the day §9.12.8 slice 1
    merged. `GET /projects/reports` looked healthy only because the list was
    empty — the first saved report would have taken it down too.

    Nothing caught it because `test_projects_reports.py` reads the module's
    SOURCE. It asserts the route exists and the SQL has the right shape; it
    never calls the route. That is the same gap that shipped
    `/analytics/stuck` broken, two features apart.
    """

    id: str
    #: NULL means the PORTFOLIO. `_report_dict` says so as `scope` rather
    #: than leaving two clients to infer it two different ways.
    project_id: str | None = None
    name: str
    config: dict[str, Any] = {}
    #: Migration 205. Both default off — a member arms the schedule, and only
    #: the deployment arms delivery.
    schedule: str | None = None
    enabled: bool = False
    last_sent_at: Any | None = None
    created_by: str | None = None
    created_at: Any | None = None
    updated_at: Any | None = None


def _report_dict(row: Any, user: Any = None) -> dict[str, Any]:
    out = row_to_dict(row, ReportModel)
    if user is not None:
        # WS-27bn R2. "Your reports" on the home screen. The SERVER decides
        # who wrote a report, from the authenticated caller, never the client.
        out["mine"] = (
            (row.created_by or "").strip().lower() == actor(user).lower()
        )
    out["config"] = normalise_report_config(out.get("config"))
    out["project_id"] = (
        str(row.project_id) if row.project_id is not None else None
    )
    # ⚠️ Said explicitly, never inferred from a null id by the client. Two
    # clients would infer it two ways, and one of them would call it "all".
    out["scope"] = "portfolio" if row.project_id is None else "node"
    return out


@router.get("/reports")
async def list_reports(
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Every report the caller may open, newest first.

    RLS bounds the list to one tenant. A portfolio report (``project_id``
    NULL) is always listed.

    ⚠️ **A node report is listed only when the caller can see its node**
    (H-182, closed in WS-27bn R2). `_visible_report` refuses that row with 404
    on get, patch and render. A list that still showed it gave away its name
    and its scope, so the list told a reader more than the rest of the API.
    The clause is `Visibility.project_clause`, the one that
    `load_visible_project` uses, so the list and the 404 cannot disagree.

    Each row carries ``mine`` (WS-27bn R2): true when the caller wrote it.
    """
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        rows = (
            await db.execute(
                text(
                    "SELECT * FROM pm_reports"
                    " WHERE project_id IS NULL"
                    f" OR {vis.project_clause('project_id')}"
                    " ORDER BY created_at DESC LIMIT 200"
                ),
                vis.params,
            )
        ).fetchall()
        return {"reports": [_report_dict(r, user) for r in rows]}


@router.get("/reports/templates")
async def list_report_templates(
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """The template catalogue, in catalogue order (WS-27bn R2).

    ⚠️ **Declared ABOVE ``GET /reports/{report_id}``.** FastAPI matches in
    registration order, and the id route would capture "templates" as an id.
    `test_projects_report_templates.py` calls this path through the app
    router to prove it.

    It opens no session: the catalogue is code, the same for every tenant.
    """
    del user  # The app-wide gate authenticates. The answer is the same.
    return {"templates": copy.deepcopy(list(TEMPLATES.values()))}


@router.post("/reports", status_code=201)
async def create_report(
    payload: dict[str, Any],
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Save a definition.

    ``project_id`` may be absent or null, which is the PORTFOLIO — the scope a
    weekly report most often wants.
    """
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        project_id = payload.get("project_id")
        if project_id is not None:
            # Seeing the node is required to report on it.
            await load_visible_project(db, vis, str(project_id))

        row = await insert_row(
            db,
            "pm_reports",
            {
                "project_id": str(project_id) if project_id else None,
                # ⚠️ Set HERE, not left to the trigger. The trigger reads the
                # tenant off the parent project, and a portfolio report has no
                # parent — the same path a root project takes (migration 204).
                "organization_id": vis.organization_id,
                "name": _clean_name(payload.get("name")),
                "config": normalise_report_config(payload.get("config")),
                "created_by": actor(user),
            },
        )
        return _report_dict(row, user)


@router.get("/reports/{report_id}")
async def get_report(
    report_id: str,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        return _report_dict(await _visible_report(db, vis, report_id), user)


@router.patch("/reports/{report_id}")
async def update_report(
    report_id: str,
    payload: dict[str, Any],
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Rename a report, or change what it asks.

    ⚠️ ``config`` is REPLACED, never merged. A merge would let a half-sent
    payload leave a definition in a state nobody chose — the sections from one
    edit and the period from another — and the author would not see it until
    the next render.
    """
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        await _visible_report(db, vis, report_id)

        values: dict[str, Any] = {}
        if "name" in payload:
            values["name"] = _clean_name(payload["name"])
        if "config" in payload:
            values["config"] = normalise_report_config(payload["config"])
        if not values:
            raise HTTPException(
                status_code=422, detail="Nothing to change.",
            )
        # ⚠️ No `updated_at` here. `update_row` already appends
        # `updated_at = now()`, and a second assignment made Postgres refuse
        # every PATCH ("multiple assignments to same column"). Found by the
        # WS-27bn R1 real-DB test. A fake database accepted it.
        row = await update_row(db, "pm_reports", report_id, values)
        return _report_dict(row, user)


@router.delete("/reports/{report_id}", status_code=204)
async def delete_report(
    report_id: str,
    user: UserContext = Depends(get_current_user),
) -> None:
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        await _visible_report(db, vis, report_id)
        await db.execute(
            text("DELETE FROM pm_reports WHERE id = CAST(:i AS uuid)"),
            {"i": report_id},
        )


async def render_body(
    db: Any,
    vis: Any,
    user: UserContext,
    *,
    project_id: str | None,
    config: dict[str, Any],
) -> dict[str, Any]:
    """The period and the sections of one report config, computed NOW.

    WS-27bn R1. **The one render function** (`projects_reports.md` §8a). The
    saved-report render and the unsaved preview both call it, and a later
    schedule, chat tool or workflow step calls it too. A second render path
    is a defect: two paths give two answers for one config.

    ``config`` is already normalised by `normalise_report_config`. The
    caller has already checked that ``project_id`` is visible. ``user``
    decides the HR tier, so the reader's grant decides it, as before.
    """
    scope_sql = await scope_clause(
        db, vis, project_id, bool(config["include_subtree"]),
    )
    # The history predicate, matching `throughput` and `finished`:
    # deliberately WITHOUT `archived_at IS NULL`, because a report
    # describes the past and an archive sweep must not empty it.
    past_where = (
        f"{scope_sql}"
        f" AND ({task_visibility_clause(vis, 't')})"
        f" AND ({triage_exclusion_clause('t')})"
    )
    # The open-work predicate, matching `load` and `stuck`.
    #
    # ⚠️ **D-PM-32(b) lands HERE and not on `past_where`, and the split is
    # the point.** A stopped project's outstanding work leaves the report,
    # because nobody is going to do it. What it already FINISHED stays,
    # for the reason the comment above gives about archiving: a report
    # describes the past, and stopping a project in September must not
    # empty July. Putting the clause on `past_where` would rewrite every
    # week we have already sent.
    open_where = (
        f"{past_where}"
        f" AND t.archived_at IS NULL"
        f" AND ({reportable_with_ancestors_clause('t')})"
        f" AND s.category <> ALL(CAST(:closed AS text[]))"
    )
    params: dict[str, Any] = {
        **vis.params,
        **scope_params(project_id),
        "reportable_states": sorted(REPORTABLE_STATUSES),
        "weeks": int(config["weeks"]),
        "closing": sorted(CLOSING_CATEGORIES),
        "closed": sorted(CLOSING_CATEGORIES),
        "done_cat": COMPLETED_CATEGORY,
        "started_cat": STARTED_CATEGORY,
    }
    skip = bool(config["skip_current_week"])

    window = (
        await db.execute(
            text(finished_period_sql(skip_current_week=skip)),
            {"weeks": int(config["weeks"])},
        )
    ).one()

    sections: dict[str, Any] = {}
    for name in config["sections"]:
        if name == "finished":
            rows = (
                await db.execute(
                    text(finished_sql(past_where, skip_current_week=skip)),
                    params,
                )
            ).fetchall()
            totals = (
                await db.execute(
                    text(
                        cycle_summary_sql(past_where, skip_current_week=skip)
                    ),
                    params,
                )
            ).one()
            sections[name] = {
                "projects": [
                    {
                        "project_id": str(r.project_id),
                        "name": r.name,
                        "completed": int(r.completed),
                        "cancelled": int(r.cancelled),
                        # WS-27bn R2b. Passed through from `finished_sql`,
                        # which already selects it. The panel's tooltip
                        # reads it. No query and no arithmetic is added.
                        "median_hours": _hours(r.median_hours),
                    }
                    for r in rows
                ],
                "total_completed": int(totals.completed or 0),
                "total_cancelled": int(totals.cancelled or 0),
            }
        elif name == "throughput":
            # ⚠️ `weekly_sql` has no `skip_current_week` arm, and that is
            # correct: a trend wants every week including the running one,
            # and the summary beside it already answers the closed period.
            series = (
                await db.execute(text(weekly_sql(past_where)), params)
            ).fetchall()
            totals = (
                await db.execute(
                    text(
                        cycle_summary_sql(past_where, skip_current_week=skip)
                    ),
                    params,
                )
            ).one()
            sections[name] = {
                "series": [
                    {
                        "week_start": w.week.date().isoformat(),
                        "completed": int(w.completed or 0),
                    }
                    for w in series
                ],
                "median_hours": _hours(totals.median_hours),
                "measured": int(totals.measured or 0),
                # WS-27bn R2b. Passed through from `_CYCLE_MEASURES`, which
                # the summary row already selects. The panel draws them
                # beside the median, as the Analytics panel does.
                "p90_hours": _hours(totals.p90_hours),
                "no_start": int(totals.no_start or 0),
                "cancelled": int(totals.cancelled or 0),
            }
        elif name == "load":
            people = (
                await db.execute(text(load_sql(open_where)), params)
            ).fetchall()
            total = int(
                (
                    await db.execute(text(total_open_sql(open_where)), params)
                ).scalar()
                or 0
            )
            sections[name] = {
                "people": [
                    {
                        "assignee": p.who or None,
                        "open_tasks": int(p.open_tasks),
                        "overdue": int(p.overdue),
                        # WS-27bn R2b. The other two buckets `load_sql`
                        # already selects, so the panel draws the stacked
                        # bar the Analytics app draws.
                        "due_next_7d": int(p.due_next_7d),
                        "later": int(p.later),
                    }
                    for p in people[:MAX_PEOPLE]
                ],
                "total_tasks": total,
            }
        elif name == "outlook":
            # WS-27bn R3a. The outlook route's OWN body, imported, as
            # `capacity` does. The panel and the report are one computation.
            #
            # ⚠️ **No `weeks=` here, on purpose.** The forecast reads
            # FORECAST_WEEKS of history, whatever period the report names.
            # `config["weeks"]` of 1 would clamp to 2 and change the forecast,
            # so the report and the Analytics panel would disagree. The
            # outlook covers the report's scope only. A forecast for each
            # child project waits for the T10 slice.
            #
            # WS-27bm S11. Leave reduces the hours only for a READER who
            # holds `admin:members:read`, as in `capacity` below
            # (`projects_ai_chat.md` §17.3 rule 5).
            sections[name] = await outlook_body(
                db, vis,
                hr_visible=can_read_hr_fields(user),
                project_id=project_id,
                include_subtree=bool(config["include_subtree"]),
            )
        elif name == "capacity":
            # WS-27bm S7a. The capacity route's OWN body, imported: the
            # panel and the report are one computation. The HR tier is the
            # READER's grant, as every other number here is the reader's.
            cap = await capacity_body(
                db, vis,
                hr_visible=can_read_hr_fields(user),
                project_id=project_id,
                include_subtree=bool(config["include_subtree"]),
            )
            named = [r for r in cap["rows"] if r["kind"] != "unassigned"]
            nobody = [r for r in cap["rows"] if r["kind"] == "unassigned"]
            sections[name] = {
                # Capped like `load`, and the unassigned row survives the
                # cap: it is the one row a reader can act on today.
                "people": named[:MAX_PEOPLE] + nobody,
                "people_total": cap["people_total"],
                "total_tasks": cap["total_tasks"],
                "hr_visible": cap["hr_visible"],
                "horizon_days": cap["horizon_days"],
                "windows": cap["windows"],
            }
        elif name == "conflicts":
            # WS-27bm S7c. The conflicts route's OWN body, imported, as
            # `capacity` does. The HR kinds follow the READER's grant.
            found = await conflicts_body(
                db, vis,
                hr_visible=can_read_hr_fields(user),
                project_id=project_id,
                include_subtree=bool(config["include_subtree"]),
            )
            sections[name] = {
                # Capped like `load`. `total` and `by_kind` count every
                # row, so a reader sees how many the cap left out.
                "rows": found["rows"][:MAX_PEOPLE],
                "total": found["total"],
                "by_kind": found["by_kind"],
                "hr_visible": found["hr_visible"],
                "horizon_days": found["horizon_days"],
                "window": found["window"],
            }
        elif name == "rebalance":
            # WS-27bn R3b. The rebalance route's OWN body, imported, as
            # `conflicts` does. The HR gate is the READER's grant: without
            # `admin:members:read` the body has no `at_risk` and no
            # `pickups` key, so a reader who may not see skills sees none.
            #
            # ⚠️ **No `config["weeks"]` here, on purpose.** Rebalancing
            # reads its own 14-day horizon (`HORIZON_DAYS`), which is a
            # window ahead of today. The report period is a window behind
            # it, so passing the period would change which tasks are at
            # risk, and the report and the route would disagree.
            found = await rebalance_body(
                db, vis,
                hr_visible=can_read_hr_fields(user),
                project_id=project_id,
                include_subtree=bool(config["include_subtree"]),
            )
            # A copy of the body. `at_risk` stays as the route serves it,
            # because the join already caps it at eight tasks.
            section = dict(found)
            if "pickups" in found:
                # Capped like `load`. `pickups_total` counts every idle
                # person with a match, so a reader sees what the cap cut.
                section["pickups"] = found["pickups"][:MAX_PEOPLE]
                section["pickups_total"] = len(found["pickups"])
            sections[name] = section
        elif name == "hygiene":
            # WS-27bn R3c. `hygiene_body`, imported, as `outlook` does. Its
            # open-work predicate is Load's, bound from the reader's own
            # visibility, so every count is the reader's.
            #
            # ⚠️ **No period here, on purpose.** The section reads the
            # state now. A missing due date last week is not a question a
            # member can act on.
            sections[name] = await hygiene_body(
                db, vis,
                project_id=project_id,
                include_subtree=bool(config["include_subtree"]),
            )
        elif name == "stuck":
            # What is overdue, by project, and (WS-27bn R3a) how long open
            # work has sat untouched, in the route's four bands.
            #
            # ⚠️ The builders are ANALYTICS', imported. A count written
            # here would be the second set of numbers §9.12.8 forbids.
            # `open_where` above is the `stuck` route's predicate, so the
            # bands equal `/analytics/stuck` for the same scope and reader.
            band_sql, band_params = stale_bands_sql(open_where)
            stale_row = (
                await db.execute(text(band_sql), {**params, **band_params})
            ).fetchone()
            overdue = (
                await db.execute(
                    text(overdue_by_project_sql(open_where)), params,
                )
            ).fetchall()
            sections[name] = {
                "overdue": [
                    {
                        "project_id": str(o.project_id),
                        "name": o.name,
                        "overdue": int(o.overdue),
                    }
                    for o in overdue
                ],
                "overdue_total": sum(int(o.overdue) for o in overdue),
                # The route's own shape: a list of {band, n}, in STALE_BANDS
                # order. The panel then draws its band chart in a report.
                "stale": [
                    {"band": band, "n": int(getattr(stale_row, band, 0) or 0)}
                    for band, _, _ in STALE_BANDS
                ],
            }

    return {
        # ⚠️ From the SERVER. A client that derives the window from its own
        # clock disagrees across a timezone, and two copies of one report
        # then name different weeks.
        "period_start": window.period_start.isoformat(),
        "period_end": window.period_end.isoformat(),
        "sections": sections,
    }


@router.get("/reports/{report_id}/render")
async def render_report(
    report_id: str,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """The report's body, computed NOW from §9.12.7's own SQL.

    ⚠️ **Every number here comes from the analytics module's query builders,
    imported rather than rewritten.** That is the rule §9.12.8 states as its
    reason for existing: a report with its own queries would be a second set of
    numbers, and the dashboard and the report would disagree about one period
    while each looked right on its own.

    ⚠️ **Visibility is the CALLER's, not the author's.** A report saved by
    somebody who can see everything must not become a way to read what the
    reader cannot. So the clause is rebuilt per request from the caller's own
    grants, and two people opening one report legitimately see two different
    numbers. That is the correct answer, and the alternative is a disclosure
    channel with a name and a schedule.
    """
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        row = await _visible_report(db, vis, report_id)
        config = normalise_report_config(row.config)

        project_id = str(row.project_id) if row.project_id is not None else None
        body = await render_body(
            db, vis, user, project_id=project_id, config=config,
        )
        return {"report": _report_dict(row, user), **body}


#: The name a preview shows while the builder's name field is still empty.
PREVIEW_NAME = "Untitled report"


@router.post("/reports/preview")
async def preview_report(
    payload: dict[str, Any],
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Render a config that is NOT saved, and write no row (WS-27bn R1).

    The builder calls this on each change, so a member sees what a report
    says before they save it. It takes ``{project_id?, name?, config}``.

    ⚠️ **It validates exactly as create does.** The config goes through
    `normalise_report_config`, and a ``project_id`` goes through
    `load_visible_project`. A preview that accepted what a save refuses
    would show a member a report that they cannot keep.

    ⚠️ **It writes nothing**, so the chat manifest lists it in
    ``READ_ONLY_POSTS``. The ``report`` in the answer is a stub with no
    ``id``, because no row exists.
    """
    config = normalise_report_config(payload.get("config"))
    raw_name = payload.get("name")
    name = (
        _clean_name(raw_name) if str(raw_name or "").strip() else PREVIEW_NAME
    )
    raw_project = payload.get("project_id")
    project_id = str(raw_project) if raw_project else None

    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        if project_id is not None:
            # The same refusal as create: seeing the node is required.
            await load_visible_project(db, vis, project_id)
        body = await render_body(
            db, vis, user, project_id=project_id, config=config,
        )
        return {
            "report": {
                "id": None,
                "project_id": project_id,
                "name": name,
                "config": config,
                "scope": "portfolio" if project_id is None else "node",
            },
            **body,
        }


# ── Who a report goes to — §9.12.8 slice 3 ──────────────────────────────────
#
# ⚠️ **Two rulings, delegated by the owner on 2026-09-17, and they answer each
# other.**
#
# **A recipient is an address we ALREADY mail, never free text.** A free-text
# field turns an internal analytics tool into an open mail relay: anybody who
# can save a report could send company delivery figures to any address on the
# internet, on a timer, from our one verified sender. That is an exfiltration
# path, and a reputation risk to the only sending domain we have.
#
# **Any member may add any member, and the reason is not politeness.** The send
# renders ONCE PER RECIPIENT, with that recipient's own visibility —
# `render_report` already resolves visibility from the caller rather than the
# author, and the job keeps that property by rendering per address rather than
# once. So adding somebody to a report can never show them more than they could
# already see by opening the app. A permission check on "may I add you" would
# guard a door onto the room the person is already standing in.


async def _known_member(db: Any, email: str) -> bool:
    """Is this address one the directory already knows?

    ⚠️ The tenant bound is RLS on `pm_reports`, not this lookup. `app_user`
    is the login directory and its `email` is globally unique, so a match here
    means "somebody signs in with this address", not "somebody in YOUR
    organization does". The recipient row is written against a report that RLS
    has already scoped, and the send renders with the recipient's own grants —
    so a cross-tenant address would receive an empty report rather than
    somebody else's numbers. Refusing it here is still right: a report that
    mails a stranger is a mistake even when the mistake is empty.
    """
    row = (await db.execute(
        text("SELECT 1 FROM app_user WHERE lower(email) = :e LIMIT 1"),
        {"e": email},
    )).fetchone()
    return row is not None


@router.get("/reports/{report_id}/recipients")
async def list_recipients(
    report_id: str,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        await _visible_report(db, vis, report_id)
        rows = (await db.execute(
            text(
                "SELECT recipient, created_by, created_at"
                "  FROM pm_report_recipients"
                " WHERE report_id = CAST(:r AS uuid)"
                " ORDER BY recipient"
            ),
            {"r": report_id},
        )).fetchall()
        return {
            "report_id": report_id,
            "recipients": [
                {
                    "recipient": r.recipient,
                    "added_by": r.created_by,
                    "added_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in rows
            ],
        }


@router.post("/reports/{report_id}/recipients", status_code=201)
async def add_recipient(
    report_id: str,
    payload: dict[str, Any],
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Add one address to a report's audience.

    Idempotent: adding twice is adding. The UNIQUE constraint carries that, so
    this does not read first and then write — two requests racing would both
    pass the read.
    """
    email = str(payload.get("recipient") or "").strip().lower()
    if not email or "@" not in email:
        raise HTTPException(
            status_code=422, detail="A recipient must be an email address.",
        )
    if email.startswith("agent:"):
        # The CHECK refuses it too. Answering here names the actual mistake
        # instead of surfacing a constraint violation as a 500.
        raise HTTPException(
            status_code=422,
            detail="A report goes to a person, never to an agent.",
        )

    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        await _visible_report(db, vis, report_id)

        if not await _known_member(db, email):
            raise HTTPException(
                status_code=422,
                detail=(
                    f"{email} is not an address this directory knows. A report"
                    " goes to people who already sign in — see the note on"
                    " free text in the route module."
                ),
            )

        await db.execute(
            text(
                "INSERT INTO pm_report_recipients"
                " (report_id, recipient, created_by)"
                " VALUES (CAST(:r AS uuid), :e, :by)"
                " ON CONFLICT (report_id, recipient) DO NOTHING"
            ),
            {"r": report_id, "e": email, "by": actor(user)},
        )
        return {"report_id": report_id, "recipient": email}


@router.delete("/reports/{report_id}/recipients/{email}", status_code=204)
async def remove_recipient(
    report_id: str,
    email: str,
    user: UserContext = Depends(get_current_user),
) -> None:
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        await _visible_report(db, vis, report_id)
        await db.execute(
            text(
                "DELETE FROM pm_report_recipients"
                " WHERE report_id = CAST(:r AS uuid) AND recipient = :e"
            ),
            {"r": report_id, "e": email.strip().lower()},
        )


@router.patch("/reports/{report_id}/schedule")
async def set_schedule(
    report_id: str,
    payload: dict[str, Any],
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Arm or disarm a report's schedule.

    ⚠️ **This flips a ROW, and the row is not what sends.** Nothing leaves the
    building until `PROJECT_REPORT_EMAIL_ENABLED` is also true on the
    deployment, and that is an owner act (§9.12.8: *"the schedule is owner-gated
    to arm. Build it dark, default off."*). Two locks, and a member holds one.

    A report with no recipients cannot be enabled. Arming a send to nobody
    looks armed on every screen and delivers nothing, which is the failure this
    whole feature is written to avoid.
    """
    enabled = payload.get("enabled")
    if not isinstance(enabled, bool):
        raise HTTPException(
            status_code=422, detail="enabled must be true or false.",
        )
    schedule = payload.get("schedule", "weekly" if enabled else None)
    if schedule is not None and schedule not in SCHEDULES:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown schedule. Known: {', '.join(SCHEDULES)}.",
        )

    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        await _visible_report(db, vis, report_id)

        if enabled:
            count = int((await db.execute(
                text(
                    "SELECT count(*) FROM pm_report_recipients"
                    " WHERE report_id = CAST(:r AS uuid)"
                ),
                {"r": report_id},
            )).scalar() or 0)
            if count == 0:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "Add at least one recipient before you turn this on."
                        " A schedule with no audience reads as armed and"
                        " delivers nothing."
                    ),
                )

        row = await update_row(
            db,
            "pm_reports",
            report_id,
            {
                "enabled": enabled,
                "schedule": schedule if enabled else None,
                "updated_at": text("now()"),
            },
        )
        return {
            **_report_dict(row),
            # ⚠️ Read from the environment, never hardcoded. A member who
            # just turned this on would otherwise believe mail is going out.
            # A literal `False` here would become a lie the moment somebody
            # arms the deployment, and nobody would think to change it.
            "delivery_armed_on_this_deployment": delivery_armed(),
        }
