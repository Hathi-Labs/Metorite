"""Projects · analytics — WS-27bk §9.12.7.

Spec: ``project-docs/specs/project_management_app.md`` §9.12.7.

    GET /projects/analytics/stuck   → where work is stuck

The owner asked four questions, and this is the first: **where is work stuck?**
Ageing, blocked, overdue — the operational view, the one that says what needs
attention today.

⚠️ **EVERY NUMBER IS A SERVER AGGREGATE, and that is not a preference.** The
task list is paginated. A count taken in the browser over the rows on screen is
a count of one page — it looks plausible, it is wrong, and nothing on the way
says so. That is the same rule ``filters.py`` states for filters, one level up:
a metric applied after ``LIMIT`` is a metric about the limit.

⚠️ **Visibility is the CALLER's.** The subtree walk runs over ``pm_projects``
unrestricted, because a subtree is a structural fact. Every task count goes
through ``vis.task_clause()``, so a member who can see a space but only one
project inside it gets numbers that match what they could reach by clicking.
Without that a roll-up becomes a disclosure channel — `tree.get_node_summary`
carries the same note for the same reason.

**A closed task is not stuck.** Everything here counts OPEN work only, through
``CLOSING_CATEGORIES`` rather than a second literal list. A category added to
the closed set must not leave this endpoint reporting finished work as late.
"""

from __future__ import annotations

from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends
from gateway.routes.projects.core import (
    CLOSING_CATEGORIES,
    _tenant_session,
    load_visible_project,
    resolve_visibility,
    router,
    task_visibility_clause,
    triage_exclusion_clause,
)
from sqlalchemy import text

#: Days since a task last changed, banded. DISJOINT and ascending.
#:
#: Cumulative bands ("7 or more", "14 or more") double-count, so the four
#: numbers do not add up to the total and a chart drawn from them lies about
#: its own proportions. The last band is open-ended, which is where the
#: genuinely forgotten work collects.
STALE_BANDS: tuple[tuple[str, int, int | None], ...] = (
    ("under_7d", 0, 7),
    ("7_to_14d", 7, 14),
    ("14_to_30d", 14, 30),
    ("over_30d", 30, None),
)

#: The most blocked tasks named in one response.
#:
#: A list, not a page: this is a dashboard panel, and somebody who needs all of
#: them wants the board with a filter, not an endpoint that scrolls. The total
#: travels beside it so the panel can say "12 of 47" rather than imply 12.
MAX_NAMED = 20


@router.get("/analytics/stuck")
async def stuck(
    project_id: str,
    include_subtree: bool = True,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Where work is stuck, under one node.

    Three answers in three grouped reads over the same scope: how long open
    work has sat untouched, what is blocked by something unfinished, and what
    is past due.

    ``project_id`` is required. A dashboard with no scope would be a read of
    every task in the tenant, which is a different endpoint with a different
    cost, and nothing here needs it.
    """
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        # Seeing the node is required to report on it, so an unreadable id
        # answers 404 rather than empty numbers — zeroes would tell the caller
        # the project exists and has no work.
        await load_visible_project(db, vis, project_id)

        if include_subtree:
            scope_sql = (
                "t.project_id IN ("
                "  WITH RECURSIVE sub AS ("
                "    SELECT id FROM pm_projects WHERE id = CAST(:pid AS uuid)"
                "    UNION ALL"
                "    SELECT p.id FROM pm_projects p JOIN sub s"
                "      ON p.parent_project_id = s.id"
                "  ) SELECT id FROM sub)"
            )
        else:
            scope_sql = "t.project_id = CAST(:pid AS uuid)"

        # The predicate every read below shares. Written once: three copies
        # would drift, and a dashboard whose panels disagree about what "open"
        # means is worse than one panel.
        open_where = (
            f"{scope_sql}"
            f" AND t.archived_at IS NULL"
            f" AND ({task_visibility_clause(vis, 't')})"
            f" AND ({triage_exclusion_clause('t')})"
            f" AND s.category <> ALL(CAST(:closed AS text[]))"
        )
        params: dict[str, Any] = {
            **vis.params,
            "pid": project_id,
            "closed": sorted(CLOSING_CATEGORIES),
        }

        # ── How long has open work sat untouched? ──────────────────────────
        #
        # `updated_at`, not "time in current status". The status history is in
        # `pm_activities` and answering from it means a walk of the whole
        # spine per task. This is the cheap question that is still worth
        # asking, and §9.12.7(c) is where the spine gets read.
        band_sql = " ".join(
            f"count(*) FILTER (WHERE "
            f"t.updated_at <= now() - CAST(:d{low} AS interval)"
            + (
                f" AND t.updated_at > now() - CAST(:d{high} AS interval)"
                if high is not None
                else ""
            )
            + f") AS {name},"
            for name, low, high in STALE_BANDS
        ).rstrip(",")
        for _, low, high in STALE_BANDS:
            params[f"d{low}"] = f"{low} days"
            if high is not None:
                params[f"d{high}"] = f"{high} days"

        stale_row = (await db.execute(
            text(
                f"SELECT {band_sql}"
                f"  FROM pm_tasks t"
                f"  JOIN pm_task_statuses s ON s.id = t.status_id"
                f" WHERE {open_where}"
            ),
            params,
        )).fetchone()
        stale = [
            {"band": name, "n": int(getattr(stale_row, name, 0) or 0)}
            for name, _, _ in STALE_BANDS
        ]

        # ── What is blocked by something unfinished? ────────────────────────
        #
        # ⚠️ A link to a task that is DONE is not a block. Counting every
        # `blocks` edge would report a project as blocked by work it already
        # finished, which is how a blocked count becomes noise people mute.
        blocker_join = (
            "EXISTS ("
            "  SELECT 1 FROM pm_task_links l"
            "    JOIN pm_tasks b ON b.id = l.source_task_id"
            "    JOIN pm_task_statuses bs ON bs.id = b.status_id"
            "   WHERE l.target_task_id = t.id"
            "     AND l.link_type = 'blocks'"
            "     AND b.archived_at IS NULL"
            "     AND bs.category <> ALL(CAST(:closed AS text[]))"
            ")"
        )
        blocked_total = int((await db.execute(
            text(
                f"SELECT count(*) FROM pm_tasks t"
                f"  JOIN pm_task_statuses s ON s.id = t.status_id"
                f" WHERE {open_where} AND {blocker_join}"
            ),
            params,
        )).scalar() or 0)

        blocked_rows = (await db.execute(
            text(
                f"SELECT t.id, t.title, t.task_number, t.due_at"
                f"  FROM pm_tasks t"
                f"  JOIN pm_task_statuses s ON s.id = t.status_id"
                f" WHERE {open_where} AND {blocker_join}"
                # Oldest first: the thing blocked longest is the thing to ask
                # about, and a dashboard that led with the newest would show a
                # different list every day while the real problem sat below.
                f" ORDER BY t.created_at ASC, t.id ASC"
                f" LIMIT {MAX_NAMED}"
            ),
            params,
        )).fetchall()

        overdue = int((await db.execute(
            text(
                f"SELECT count(*) FROM pm_tasks t"
                f"  JOIN pm_task_statuses s ON s.id = t.status_id"
                f" WHERE {open_where}"
                f"   AND t.due_at IS NOT NULL AND t.due_at < now()"
            ),
            params,
        )).scalar() or 0)

        return {
            "project_id": project_id,
            "include_subtree": include_subtree,
            "stale": stale,
            "blocked_total": blocked_total,
            "blocked": [
                {
                    "id": str(row.id),
                    "title": row.title,
                    "task_number": row.task_number,
                    "due_at": row.due_at.isoformat() if row.due_at else None,
                }
                for row in blocked_rows
            ],
            "overdue": overdue,
        }


#: The most people named in one load response.
#:
#: Same ruling as :data:`MAX_NAMED`: a dashboard panel, not a page. The total
#: travels beside the list so the panel can say "20 of 34" instead of implying
#: that thirty-four people are twenty.
MAX_PEOPLE = 20


def load_sql(open_where: str) -> str:
    """The per-assignee aggregate, as one string a test can RUN.

    ⚠️ A function rather than an f-string inline in the route, so
    ``test_projects_analytics_load.py`` executes THIS query against a real
    Postgres instead of a transcription of it. The spec's Done-when for
    §9.12.7 asks for exactly that, and a copied query agrees with itself
    forever while the route drifts.

    ``open_where`` is the caller's scope, visibility and open-only predicate.
    It is interpolated because it is built from trusted fragments — the same
    shape ``stuck`` uses — and every VALUE in it still travels as a bound
    parameter.

    `LEFT JOIN` and `coalesce` are what put unassigned work in the list instead
    of dropping it. An inner join would silently answer a different question —
    "who is overloaded, among tasks somebody already took" — and that question
    hides the backlog nobody owns.

    The buckets are DISJOINT, and the ``due_at IS NULL`` arm lands in ``later``
    on purpose. Undated work is real work, and a fourth "no date" column would
    split the one bar a reader wants to compare.
    """
    return (
        f"SELECT coalesce(lower(a.assignee), '') AS who,"
        f"       count(*) FILTER ("
        f"         WHERE t.due_at IS NOT NULL AND t.due_at < now()"
        f"       ) AS overdue,"
        f"       count(*) FILTER ("
        f"         WHERE t.due_at >= now()"
        f"           AND t.due_at < now() + interval '7 days'"
        f"       ) AS due_next_7d,"
        f"       count(*) FILTER ("
        f"         WHERE t.due_at IS NULL"
        f"            OR t.due_at >= now() + interval '7 days'"
        f"       ) AS later,"
        f"       count(*) AS open_tasks"
        f"  FROM pm_tasks t"
        f"  JOIN pm_task_statuses s ON s.id = t.status_id"
        f"  LEFT JOIN pm_task_assignees a ON a.task_id = t.id"
        f" WHERE {open_where}"
        f" GROUP BY 1"
        # Most loaded first, then the overdue half as the tiebreak — two people
        # with nine open tasks are not equally in trouble.
        f" ORDER BY open_tasks DESC, overdue DESC, who"
    )


#: Open work in scope, counted over TASKS rather than summed per person.
#:
#: ⚠️ A task with two assignees appears twice in `load_sql`, so adding its
#: `open_tasks` would report more open work than exists.
def total_open_sql(open_where: str) -> str:
    return (
        f"SELECT count(DISTINCT t.id) FROM pm_tasks t"
        f"  JOIN pm_task_statuses s ON s.id = t.status_id"
        f" WHERE {open_where}"
    )


@router.get("/analytics/load")
async def load(
    project_id: str,
    include_subtree: bool = True,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Who is overloaded, under one node — §9.12.7(b).

    Open work per assignee, split by when it is due: **overdue**, due in the
    **next seven days**, and **later** — where "later" also holds everything
    with no due date at all.

    ⚠️ **UNASSIGNED is a row, not an absence.** The spec says so and it is
    right: on a real board it is usually the largest bar and the actual
    finding. Reporting it as a gap in a list of people would hide the one
    number somebody can act on today.

    ⚠️ **Seven rolling days, NOT a calendar week, and the spec says "this
    week".** Two reasons for the divergence, both practical. A calendar week
    shrinks as the week runs, so the same board reads "overloaded" on Monday
    and "clear" on Friday without anybody finishing anything. And a calendar
    week needs a timezone, which here would be the PROJECT's — so a subtree
    spanning two timezones has two different weeks in one aggregate. The field
    is named `due_next_7d` rather than `this_week`, so the response cannot be
    read as claiming a calendar week it never computed.

    ⚠️ **A task with two assignees counts for BOTH.** It is genuinely on both
    people's plates, and halving it would make every bar a fraction nobody can
    act on. So the per-person numbers sum to more than `total_tasks`, which is
    why that total is reported separately rather than inferred by adding.
    """
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        await load_visible_project(db, vis, project_id)

        if include_subtree:
            scope_sql = (
                "t.project_id IN ("
                "  WITH RECURSIVE sub AS ("
                "    SELECT id FROM pm_projects WHERE id = CAST(:pid AS uuid)"
                "    UNION ALL"
                "    SELECT p.id FROM pm_projects p JOIN sub s"
                "      ON p.parent_project_id = s.id"
                "  ) SELECT id FROM sub)"
            )
        else:
            scope_sql = "t.project_id = CAST(:pid AS uuid)"

        # The same predicate `stuck` uses, for the same reason: two panels on
        # one dashboard must not disagree about what "open" means.
        open_where = (
            f"{scope_sql}"
            f" AND t.archived_at IS NULL"
            f" AND ({task_visibility_clause(vis, 't')})"
            f" AND ({triage_exclusion_clause('t')})"
            f" AND s.category <> ALL(CAST(:closed AS text[]))"
        )
        params: dict[str, Any] = {
            **vis.params,
            "pid": project_id,
            "closed": sorted(CLOSING_CATEGORIES),
        }

        # ── Per person, bucketed by when it is due — see `load_sql`. ──────
        rows = (
            await db.execute(text(load_sql(open_where)), params)
        ).fetchall()

        # ⚠️ Counted over TASKS, not summed from the rows above. A task with
        # two assignees appears twice up there, so adding `open_tasks` would
        # report more open work than exists.
        total_tasks = int(
            (
                await db.execute(text(total_open_sql(open_where)), params)
            ).scalar()
            or 0
        )

        people = [
            {
                # "" is the unassigned bucket. Named explicitly rather than
                # left as an empty string the client has to recognise.
                "assignee": row.who or None,
                "open_tasks": int(row.open_tasks),
                "overdue": int(row.overdue),
                "due_next_7d": int(row.due_next_7d),
                "later": int(row.later),
            }
            for row in rows
        ]
        return {
            "project_id": project_id,
            "include_subtree": include_subtree,
            "total_tasks": total_tasks,
            "people_total": len(people),
            "people": people[:MAX_PEOPLE],
        }
