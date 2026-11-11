"""Projects · the roll-up of an epic — SP-9 (Metorite task 2292).

Spec: ``project-docs/specs/project_management_app.md`` §13.3 "Roll-ups".

One read, ``epic_rollup``. ``GET /projects/tasks/{id}`` adds its answer under
``rollup`` for a task whose type is an epic type. The web app, the chat and the
MCP ``get_task`` all read it from that one place. There is no route of its own.

* **Progress.** A slice is a descendant with no child of its own. The read
  counts slices done and total, and ``estimate_mins`` done and total, over
  EVERY level below the epic. ``task.subtasks`` still counts the direct
  children only, and nothing here changes it.
* **Waits.** An agent run in ``awaiting`` on a descendant. The rule is the one
  of the Questions list (``agent_questions.questions_sql``), so the two agree.
* **Hidden.** A descendant that the reader cannot see
  (``core.task_visibility_clause``) is left out of every number and counted in
  ``hidden``. A project pin of a signed agent narrows it the same way.
* **Room.** ``spend``, ``forecast``, ``health`` and ``hill`` are ``None`` for
  now. See :data:`_NOT_YET`.

⚠️ **Tenancy (R5).** The caller's tenant session runs each query. The recursion
carries ``organization_id`` as well as RLS. No parameter names a person or an
organization: they come from ``vis.params``.
"""

from __future__ import annotations

from typing import Any

from gateway.routes.projects.core import (
    is_epic_type,
    load_row,
    task_visibility_clause,
)
from gateway.routes.projects.filters import (
    AGENT_RUN_STATE_SQL,
    CLOSED_CATEGORIES,
)
from sqlalchemy import text

#: The deepest level that the recursion follows. The hierarchy rule
#: (``core``, "deeper than the supported maximum") is far below this. The cap
#: only keeps a bad cycle in data from running for ever.
MAX_DEPTH = 50

#: The most waiting questions the read lists. ``waits.count`` stays exact.
MAX_WAITS = 50

#: Keys that the shape holds and that carry ``None`` until their source lands.
#:
#: * ``spend``: the appetite (the epic contract) arrives with SP-8 (task 2291).
#:   No per-task source of credits used or review hours used exists yet.
#:   Nothing here invents one.
#: * ``forecast``, ``health``, ``hill``: the engine of §13.4 fills them (PJP-6).
_NOT_YET = ("spend", "forecast", "health", "hill")

#: Every non-archived descendant of ``:root``, with its depth. ``UNION ALL``
#: plus the depth cap is the cycle guard.
_DESCENDANTS_CTE = f"""
WITH RECURSIVE d AS (
    SELECT c.id, 1 AS depth
      FROM pm_tasks c
     WHERE c.parent_task_id = CAST(:root AS uuid)
       AND c.organization_id = CAST(:vis_org AS uuid)
       AND c.archived_at IS NULL
    UNION ALL
    SELECT c.id, d.depth + 1
      FROM pm_tasks c
      JOIN d ON c.parent_task_id = d.id
     WHERE c.organization_id = CAST(:vis_org AS uuid)
       AND c.archived_at IS NULL
       AND d.depth < {MAX_DEPTH}
)
"""

#: The numbers, in one statement. A hidden task has ``seen`` false. A task
#: with a hidden child is not a slice either, because it has a child.
_PROGRESS_SQL = (
    _DESCENDANTS_CTE
    + """
SELECT count(*) FILTER (WHERE NOT x.seen) AS hidden,
       count(*) FILTER (WHERE x.seen AND NOT x.has_child) AS slices_total,
       count(*) FILTER (WHERE x.seen AND NOT x.has_child AND x.closed) AS slices_done,
       coalesce(sum(x.estimate_mins) FILTER (WHERE x.seen AND NOT x.has_child), 0)
           AS est_total,
       coalesce(sum(x.estimate_mins) FILTER (
           WHERE x.seen AND NOT x.has_child AND x.closed), 0) AS est_done
  FROM (
    SELECT t.estimate_mins,
           ({visible}) AS seen,
           coalesce(s.category = ANY(:closed), false) AS closed,
           EXISTS (SELECT 1 FROM pm_tasks k
                    WHERE k.parent_task_id = t.id AND k.archived_at IS NULL) AS has_child
      FROM d
      JOIN pm_tasks t ON t.id = d.id
      LEFT JOIN pm_task_statuses s ON s.id = t.status_id
  ) x
"""
)


def _waits_sql(vis: Any) -> str:
    """The ``awaiting`` runs on visible descendants. ``listed_question_sql``
    leaves out a closed task, and it shows a typed archive request to the run's
    member only, as the Questions list does. Binds ``:vis_email`` (from
    ``vis.params``) for the reader."""
    from gateway.routes.projects.agent_runs import listed_question_sql

    return (
        _DESCENDANTS_CTE
        + f"""
SELECT r.id AS run_id, t.id AS task_id, t.task_number, t.title,
       t.created_by, r.member_email,
       (SELECT a.assignee FROM pm_task_assignees a
         WHERE a.task_id = t.id AND btrim(coalesce(a.assignee, '')) <> ''
         ORDER BY a.assigned_at ASC, a.assignee ASC LIMIT 1) AS assignee,
       r.last_activity_at
  FROM d
  JOIN pm_tasks t ON t.id = d.id
  JOIN pm_agent_runs r ON r.task_id = t.id
 WHERE r.task_id IS NOT NULL
   AND r.status = 'awaiting' AND ({AGENT_RUN_STATE_SQL}) = 'awaiting'
   AND {listed_question_sql('r', me='vis_email')}
   AND ({task_visibility_clause(vis, 't')})
"""
    )


def _wire(value: Any) -> Any:
    return value.isoformat() if hasattr(value, "isoformat") else value


async def _waits(db: Any, vis: Any, root_id: str) -> dict[str, Any]:
    from gateway.routes.projects.agent_runs import _OPEN_ELICITATIONS_SQL

    runs = (await db.execute(
        text(_waits_sql(vis)), {"root": root_id, **vis.params},
    )).fetchall()
    if not runs:
        return {"count": 0, "items": []}
    heads = {
        str(q.run_id): q
        for q in (await db.execute(
            text(f"SELECT q.run_id, q.created_at FROM ({_OPEN_ELICITATIONS_SQL}) q"),
            {"ids": [str(r.run_id) for r in runs]},
        )).fetchall()
    }
    items = []
    for r in runs:
        head = heads.get(str(r.run_id))
        # Who may answer, in the order of `agent_runs.answer_reason_sql`: the
        # creator of the task, an assignee, then the member of the run.
        person = r.created_by or r.assignee or r.member_email
        items.append({
            "run_id": str(r.run_id),
            "task_id": str(r.task_id),
            "task_number": r.task_number,
            "title": r.title,
            "person": person,
            "since": _wire(head.created_at if head is not None else r.last_activity_at),
        })
    items.sort(key=lambda i: (str(i["since"]), i["run_id"]))
    return {"count": len(items), "items": items[:MAX_WAITS]}


async def epic_rollup(db: Any, vis: Any, task: Any) -> dict[str, Any] | None:
    """The roll-up of *task*, or ``None`` when its type is not an epic type.

    *task* is a row that the caller already loaded through
    ``core.load_visible_task``.
    """
    type_id = getattr(task, "type_id", None)
    if type_id is None:
        return None
    type_row = await load_row(db, "pm_task_types", str(type_id))
    if type_row is None or not is_epic_type(type_row):
        return None
    root = str(task.id)
    row = (await db.execute(
        text(_PROGRESS_SQL.format(visible=task_visibility_clause(vis, "t"))),
        {"root": root, "closed": list(CLOSED_CATEGORIES), **vis.params},
    )).first()
    out: dict[str, Any] = {
        "slices": {"done": int(row.slices_done or 0), "total": int(row.slices_total or 0)},
        "estimate_mins": {"done": int(row.est_done or 0), "total": int(row.est_total or 0)},
        "waits": await _waits(db, vis, root),
        "hidden": int(row.hidden or 0),
    }
    for key in _NOT_YET:
        out[key] = None
    return out
