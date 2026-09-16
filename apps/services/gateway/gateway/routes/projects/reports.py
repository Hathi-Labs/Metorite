"""Projects · reports — a saved question, rendered from §9.12.7's own numbers.

Spec: ``project-docs/specs/project_management_app.md`` §9.12.8.

    GET    /projects/reports              → every report in the tenant
    POST   /projects/reports              → save a definition
    GET    /projects/reports/{id}
    PATCH  /projects/reports/{id}
    DELETE /projects/reports/{id}
    GET    /projects/reports/{id}/render  → the body, computed now

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

from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException
from gateway.routes.projects.analytics import (
    MAX_PEOPLE,
    MAX_WEEKS,
    cycle_summary_sql,
    finished_period_sql,
    finished_sql,
    load_sql,
    overdue_by_project_sql,
    scope_clause,
    scope_params,
    total_open_sql,
    weekly_sql,
)
from gateway.routes.projects.core import (
    CLOSING_CATEGORIES,
    COMPLETED_CATEGORY,
    STARTED_CATEGORY,
    _tenant_session,
    actor,
    insert_row,
    load_visible_project,
    require_row,
    resolve_visibility,
    router,
    row_to_dict,
    task_visibility_clause,
    triage_exclusion_clause,
    update_row,
)
from sqlalchemy import text

#: The sections a definition may ask for, and the order they render in.
#:
#: ⚠️ **Order is DECLARED here, not taken from the caller's list.** A report
#: that renders its sections in whatever order they were saved reads
#: differently every time somebody edits it, and two people comparing last
#: week's copy to this week's would find the difference in the layout rather
#: than in the numbers. What we finished leads, because that is the answer the
#: reader came for.
SECTIONS: tuple[str, ...] = ("finished", "throughput", "load", "stuck")

#: What a definition means when it does not say.
#:
#: `skip_current_week` is TRUE here and FALSE on the dashboard, and the
#: difference is the whole of §9.12.8's shape. See the module header.
_DEFAULTS: dict[str, Any] = {
    "weeks": 1,
    "skip_current_week": True,
    "include_subtree": True,
    "sections": list(SECTIONS),
}

#: A name long enough to be useful and short enough for a subject line.
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


def _report_dict(row: Any) -> dict[str, Any]:
    out = row_to_dict(row)
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
    """Every report the caller's tenant holds, newest first.

    Not filtered by project: a report list is a small set a person scans, and
    the scope is on each row. RLS bounds it to one tenant.
    """
    async with _tenant_session() as db:
        await resolve_visibility(db, user)
        rows = (
            await db.execute(
                text(
                    "SELECT * FROM pm_reports ORDER BY created_at DESC LIMIT 200"
                )
            )
        ).fetchall()
        return {"reports": [_report_dict(r) for r in rows]}


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
        return _report_dict(row)


@router.get("/reports/{report_id}")
async def get_report(
    report_id: str,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        return _report_dict(await _visible_report(db, vis, report_id))


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
        values["updated_at"] = text("now()")
        row = await update_row(db, "pm_reports", report_id, values)
        return _report_dict(row)


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
        open_where = (
            f"{past_where}"
            f" AND t.archived_at IS NULL"
            f" AND s.category <> ALL(CAST(:closed AS text[]))"
        )
        params: dict[str, Any] = {
            **vis.params,
            **scope_params(project_id),
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
                    "median_hours": (
                        None if totals.median_hours is None
                        else round(float(totals.median_hours), 2)
                    ),
                    "measured": int(totals.measured or 0),
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
                        }
                        for p in people[:MAX_PEOPLE]
                    ],
                    "total_tasks": total,
                }
            elif name == "stuck":
                # The one number a report needs from (a): what is overdue, by
                # project. The ageing bands are a dashboard shape — four
                # buckets asking to be looked at, not read aloud.
                #
                # ⚠️ The builder is ANALYTICS', imported. A count written
                # here would be the second set of numbers §9.12.8 forbids.
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
                }

        return {
            "report": _report_dict(row),
            # ⚠️ From the SERVER. A client that derives the window from its own
            # clock disagrees across a timezone, and two copies of one report
            # then name different weeks.
            "period_start": window.period_start.isoformat(),
            "period_end": window.period_end.isoformat(),
            "sections": sections,
        }
