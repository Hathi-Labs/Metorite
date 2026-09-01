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
