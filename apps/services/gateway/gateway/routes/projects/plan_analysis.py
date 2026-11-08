"""Projects · the plan of one goal, epic or project, read (PJP-6a).

Spec: ``project-docs/specs/project_planning.md`` §3.4.

    GET /projects/plan/analysis?root_task_id=…&start=YYYY-MM-DD

This route fetches the tasks and the ``blocks`` links from the one task store,
and hands them to :func:`acb_common.planning.analyze_plan`. It holds no maths of
its own (D195 Q8: one engine). It writes nothing. It computes on each read and
keeps no cache and no trigger.

**Visibility.** The same helpers as every Projects read: ``resolve_visibility``,
``load_visible_task`` for the root and ``task_visibility_clause`` for the rest.
So a project pin narrows a signed agent here as it does elsewhere. A slice that
the caller cannot see is COUNTED in ``hidden`` and never named. It is left out
of the plan, and so is each link that touches it. The result carries ids and
numbers, plus ``labels``: the number and title of the visible slices that the
answer names (SP-12). A hidden task has no label, because its text is read as
NULL in SQL.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from acb_auth import UserContext, get_current_user
from acb_common.planning import CLOSED_CATEGORIES, PlanTask, analyze_plan
from acb_common.priority import important_from_importance, priority_rank
from acb_common.today import DEFAULT_ZONE
from fastapi import Depends, HTTPException, Query
from gateway.routes.projects.core import (
    MAX_DEPTH,
    _tenant_session,
    load_visible_task,
    resolve_visibility,
    router,
    task_visibility_clause,
)
from sqlalchemy import text

#: The most tasks one read plans. A bigger tree is a goal for a person to split.
MAX_PLAN_TASKS = 2000

#: The most labels one answer carries (SP-12). The ids come in the order of use.
MAX_LABELS = 100

#: Every non-archived task under the root, the root included. ``vis`` says if
#: the caller may see it. A task that the caller cannot see is read only to
#: count it, and its text never leaves this module.
_TREE_SQL = """
WITH RECURSIVE tree AS (
    SELECT id, 1 AS depth FROM pm_tasks WHERE id = CAST(:root AS uuid)
    UNION ALL
    SELECT c.id, tree.depth + 1 FROM pm_tasks c
      JOIN tree ON c.parent_task_id = tree.id
     WHERE c.archived_at IS NULL AND tree.depth < :max_depth
)
SELECT t.id, t.parent_task_id, s.category, t.estimate_mins,
       CASE WHEN ({visible}) THEN t.task_number END AS task_number,
       CASE WHEN ({visible}) THEN t.title END AS title,
       t.importance, t.leveraged, t.due_at,
       ty.name AS type_name,
       (SELECT min(a.assignee) FROM pm_task_assignees a
         WHERE a.task_id = t.id) AS owner,
       ({visible}) AS vis
  FROM pm_tasks t
  JOIN tree ON tree.id = t.id
  JOIN pm_task_statuses s ON s.id = t.status_id
  LEFT JOIN pm_task_types ty ON ty.id = t.type_id
 LIMIT :cap
"""

_LINKS_SQL = """
SELECT source_task_id, target_task_id FROM pm_task_links
 WHERE link_type = 'blocks'
   AND (source_task_id = ANY(CAST(:ids AS uuid[]))
        OR target_task_id = ANY(CAST(:ids AS uuid[])))
"""

_OUTSIDE_SQL = """
SELECT t.id, s.category, ({visible}) AS vis
  FROM pm_tasks t
  JOIN pm_task_statuses s ON s.id = t.status_id
 WHERE t.id = ANY(CAST(:ids AS uuid[]))
"""


def _rank(row: Any) -> int | None:
    return priority_rank(
        important=important_from_importance(row.importance),
        leveraged=bool(row.leveraged),
        due_at=row.due_at,
    )


def _today() -> date:
    return datetime.now(tz=ZoneInfo(DEFAULT_ZONE)).date()


async def compute_plan(db: Any, vis: Any, root: str, start: date | None = None) -> dict:
    """The plan of the tree under *root*, for the caller *vis*.

    The caller has loaded *root* with ``load_visible_task`` already. This is
    the one place that fetches the tree and calls the engine. The route below
    and ``plan_order`` (SW-68a, the order of ``my_work``) both call it, so
    neither keeps a second copy of the fetch or the visibility rule. It
    raises a 422 for a tree over :data:`MAX_PLAN_TASKS`.
    """
    rows = (await db.execute(
        text(_TREE_SQL.format(visible=task_visibility_clause(vis))),
        {"root": root, "max_depth": MAX_DEPTH, "cap": MAX_PLAN_TASKS + 1,
         **vis.params},
    )).fetchall()
    if len(rows) > MAX_PLAN_TASKS:
        raise HTTPException(
            status_code=422,
            detail=f"This tree has more than {MAX_PLAN_TASKS} tasks. Plan a smaller epic.",
        )
    ids = [str(r.id) for r in rows]
    link_rows = (await db.execute(
        text(_LINKS_SQL), {"ids": ids},
    )).fetchall()
    inside = set(ids)
    far = sorted({
        str(end) for r in link_rows
        for end in (r.source_task_id, r.target_task_id) if str(end) not in inside
    })
    outside_rows = (await db.execute(
        text(_OUTSIDE_SQL.format(visible=task_visibility_clause(vis))),
        {"ids": far, **vis.params},
    )).fetchall() if far else []

    parents = {str(r.parent_task_id) for r in rows if r.parent_task_id}
    visible_parents = {
        str(r.parent_task_id) for r in rows if r.vis and r.parent_task_id
    }
    # A hidden open leaf is a hidden slice. It is counted and never named.
    hidden = sum(
        1 for r in rows
        if not r.vis and str(r.id) not in parents and r.category not in CLOSED_CATEGORIES
    )
    # A visible parent whose children are all hidden has only hidden slices. It
    # must not read as a leaf, so it leaves the plan.
    tasks = [
        PlanTask(
            id=str(r.id),
            category=r.category,
            estimate_mins=r.estimate_mins,
            owner=r.owner,
            type=r.type_name,
            parent=str(r.parent_task_id) if r.parent_task_id and str(r.id) != root else None,
            priority_rank=_rank(r),
        )
        for r in rows
        if r.vis and (str(r.id) not in parents or str(r.id) in visible_parents)
    ]
    outside = {str(r.id): r.category for r in outside_rows if r.vis}
    hidden_outside = sum(1 for r in outside_rows if not r.vis)
    links = [(str(r.source_task_id), str(r.target_task_id)) for r in link_rows]
    result = analyze_plan(tasks, links, start=start or _today(), outside=outside)
    answer = result.to_dict()
    # SP-12: a number and a title for each visible task that the answer names,
    # so a reader can say it in words. Never a hidden task: its title is NULL.
    named = [answer["next_slice"], *answer["critical_path"], *answer["ready"],
             *answer["held"], *(o["task_id"] for o in answer["outside_blockers"])]
    seen: set[str] = set()
    labels: dict[str, dict[str, Any]] = {}
    by_id = {str(r.id): r for r in rows if r.vis}
    for tid in named:
        if tid and tid not in seen and tid in by_id and len(labels) < MAX_LABELS:
            seen.add(tid)
            labels[tid] = {"number": by_id[tid].task_number, "title": by_id[tid].title}
    return {
        "root_task_id": root,
        **answer,
        "labels": labels,
        "hidden": {"slices": hidden, "outside_blockers": hidden_outside},
    }


@router.get("/plan/analysis")
async def plan_analysis(
    root_task_id: str = Query(..., description="The goal, epic or project task to plan."),
    start: date | None = Query(None, description="The first working day. Default: today."),
    user: UserContext = Depends(get_current_user),
) -> dict:
    """The ready set, waves, critical path, slack and finish of one tree."""
    try:
        root = str(UUID(root_task_id))
    except ValueError:
        raise HTTPException(status_code=404, detail="Task not found") from None
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        await load_visible_task(db, vis, root)
        return await compute_plan(db, vis, root, start)
