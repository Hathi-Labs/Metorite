"""Projects · the order of the member's own tasks, by the plan engine (SW-68a).

Spec: ``project-docs/specs/agent_coordination.md`` SW-68 and
``project-docs/specs/project_planning.md`` §3.4 (readers: ``my_work``).

    GET /projects/plan/order?task_ids=<id>,<id>,…

The member's task list (``my_work`` on the MCP edge, ``assigned-to-me`` in the
chat) is a flat list. A task that sits under an epic belongs to a plan. This
module ranks those tasks by the one engine, ``acb_common.planning``, through
``plan_analysis.compute_plan``. It holds no graph maths of its own (D195 Q8).
It writes nothing and keeps no cache.

**The rule.** The tasks that sit under an epic fill the slots that they held in
the input, in plan order: a ready slice before a blocked one, then the least
slack, then the best priority rank, then the least id. That is the order of the
engine's ``next_slice``. A task outside any epic keeps its slot. A slice that
the plan names as blocked goes after every other task, so it is never the top
row. The order of a member with no epic is unchanged.

**Visibility.** The engine runs over the slices that the caller can see. A
hidden slice leaves the plan, and so does each link that touches it. An open
blocker that the caller cannot see holds its task and is COUNTED in
``blocked_by_hidden``. It is never named.

**The cap.** At most :data:`MAX_TASKS` tasks and :data:`MAX_EPICS` epics for
each read. An epic over that cap, a tree over the engine's task limit, an epic
that the caller cannot open, and an epic with a dependency cycle keep the
input order for their tasks.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from acb_auth import UserContext, get_current_user
from acb_common.planning import CLOSED_CATEGORIES
from fastapi import Depends, HTTPException, Query
from gateway.routes.projects.core import (
    MAX_DEPTH,
    _tenant_session,
    load_visible_task,
    resolve_visibility,
    router,
    task_visibility_clause,
)
from gateway.routes.projects.plan_analysis import _rank, compute_plan
from sqlalchemy import text

#: The most tasks one read orders (the page of ``my_work``).
MAX_TASKS = 100
#: The most epics one read plans. The rest keep the input order.
MAX_EPICS = 10

_NO_RANK = 99

#: Each task in the input with its path to the root, nearest first. ``visible``
#: limits the leaves to tasks the caller can read.
_WALK_SQL = """
WITH RECURSIVE up AS (
    SELECT t.id AS leaf, t.id AS cur, t.parent_task_id AS par, 1 AS depth
      FROM pm_tasks t
     WHERE t.id = ANY(CAST(:ids AS uuid[])) AND ({visible})
    UNION ALL
    SELECT up.leaf, p.id, p.parent_task_id, up.depth + 1
      FROM up JOIN pm_tasks p ON p.id = up.par
     WHERE up.depth < :max_depth
)
SELECT up.leaf, up.cur, up.par, up.depth,
       (coalesce(ty.is_epic, false)
        OR (coalesce(ty.is_system, false) AND ty.name = 'Epic')) AS is_epic
  FROM up
  JOIN pm_tasks p ON p.id = up.cur
  LEFT JOIN pm_task_types ty ON ty.id = p.type_id
"""

_RANK_SQL = """
SELECT t.id, t.importance, t.leveraged, t.due_at
  FROM pm_tasks t WHERE t.id = ANY(CAST(:ids AS uuid[]))
"""

#: The ``blocks`` links that point at a task or at one of its ancestors.
_BLOCKERS_SQL = """
SELECT l.source_task_id AS src, l.target_task_id AS dst, s.category,
       ({visible}) AS vis
  FROM pm_task_links l
  JOIN pm_tasks t ON t.id = l.source_task_id
  JOIN pm_task_statuses s ON s.id = t.status_id
 WHERE l.link_type = 'blocks'
   AND l.target_task_id = ANY(CAST(:ids AS uuid[]))
"""


@dataclass(frozen=True)
class TaskPlan:
    """What the plan says about one task. ``rank`` is for sorting only."""

    epic_id: str
    blocked: bool
    slack_mins: int | None
    blocked_by: tuple[str, ...]
    blocked_by_hidden: int
    rank: int | None = None

    def key(self, task_id: str) -> tuple[Any, ...]:
        return (self.blocked, self.slack_mins or 0, self.rank or _NO_RANK, task_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "epic_id": self.epic_id,
            "ready": not self.blocked,
            "slack_mins": self.slack_mins,
            "blocked_by": list(self.blocked_by),
            "blocked_by_hidden": self.blocked_by_hidden,
        }


def reorder(ids: Sequence[str], plans: Mapping[str, TaskPlan]) -> list[str]:
    """*ids* in the order of the plan. The pure half of the rule above."""
    planned = sorted((i for i in ids if i in plans), key=lambda i: plans[i].key(i))
    fill = iter(planned)
    seated = [next(fill) if i in plans else i for i in ids]
    return ([i for i in seated if not (i in plans and plans[i].blocked)]
            + [i for i in seated if i in plans and plans[i].blocked])


async def plan_tasks(db: Any, vis: Any, task_ids: Sequence[str]) -> dict[str, TaskPlan]:
    """The plan of each task in *task_ids* that sits under an epic, by id.

    A task that is not in the answer has no plan: no epic above it, a parent
    task, a task the caller cannot read, or an epic that is over the cap.
    """
    ids = list(dict.fromkeys(task_ids))[:MAX_TASKS]
    if not ids:
        return {}
    walk = (await db.execute(
        text(_WALK_SQL.format(visible=task_visibility_clause(vis))),
        {"ids": ids, "max_depth": MAX_DEPTH, **vis.params},
    )).fetchall()
    chain: dict[str, list[str]] = {}
    root_of: dict[str, tuple[str, bool]] = {}
    for r in sorted(walk, key=lambda r: r.depth):
        chain.setdefault(str(r.leaf), []).append(str(r.cur))
        if r.par is None:
            root_of[str(r.leaf)] = (str(r.cur), bool(r.is_epic))
    by_epic: dict[str, list[str]] = {}
    for leaf in ids:
        root = root_of.get(leaf)
        if root is not None and root[1] and root[0] != leaf:
            by_epic.setdefault(root[0], []).append(leaf)

    rank_rows = (await db.execute(text(_RANK_SQL), {"ids": ids})).fetchall()
    ranks = {str(r.id): _rank(r) for r in rank_rows}

    plans: dict[str, TaskPlan] = {}
    for epic in list(by_epic)[:MAX_EPICS]:
        try:
            await load_visible_task(db, vis, epic)
            result = await compute_plan(db, vis, epic)
        except HTTPException:
            continue
        if result.get("cycle"):
            continue
        slices = {s for wave in result["waves"] for s in wave} | set(result["held"])
        ready = set(result["ready"])
        for leaf in by_epic[epic]:
            if leaf not in slices:
                continue
            plans[leaf] = TaskPlan(
                epic_id=epic, blocked=leaf not in ready,
                slack_mins=result["slack"].get(leaf), blocked_by=(),
                blocked_by_hidden=0, rank=ranks.get(leaf))
    if plans:
        plans = await _with_blockers(db, vis, plans, chain)
    return plans


async def _with_blockers(db: Any, vis: Any, plans: dict[str, TaskPlan],
                         chain: Mapping[str, list[str]]) -> dict[str, TaskPlan]:
    """Add what blocks each task. A visible open blocker is named by id, and
    only for a task that the engine holds back. An open blocker that the caller
    cannot see is counted, and it also marks the task as blocked."""
    targets = sorted({a for leaf in plans for a in chain[leaf]})
    rows = (await db.execute(
        text(_BLOCKERS_SQL.format(visible=task_visibility_clause(vis))),
        {"ids": targets, **vis.params},
    )).fetchall()
    out: dict[str, TaskPlan] = {}
    for leaf, plan in plans.items():
        mine = set(chain[leaf])
        named: set[str] = set()
        hidden = 0
        for r in rows:
            src, dst = str(r.src), str(r.dst)
            if dst not in mine or src in mine or r.category in CLOSED_CATEGORIES:
                continue
            if r.vis:
                named.add(src)
            else:
                hidden += 1
        out[leaf] = TaskPlan(
            epic_id=plan.epic_id, blocked=plan.blocked or hidden > 0,
            slack_mins=plan.slack_mins,
            blocked_by=tuple(sorted(named)) if plan.blocked else (),
            blocked_by_hidden=hidden, rank=plan.rank)
    return out


async def order_for(db: Any, user: UserContext, task_ids: Sequence[str]) -> tuple[
        list[str], dict[str, TaskPlan]]:
    """``(ids in plan order, plan by id)``. The chat route calls it in its own
    session, and the read below calls it for the MCP edge."""
    vis = await resolve_visibility(db, user)
    plans = await plan_tasks(db, vis, task_ids)
    return reorder(list(dict.fromkeys(task_ids)), plans), plans


@router.get("/plan/order")
async def plan_order(
    task_ids: str = Query(..., description="Task ids, comma-separated, in the order to rank."),
    user: UserContext = Depends(get_current_user),
) -> dict:
    """The ids in plan order, and the plan of each task that sits under an epic."""
    ids: list[str] = []
    for part in task_ids.split(","):
        try:
            ids.append(str(UUID(part.strip())))
        except ValueError:
            continue
    ids = list(dict.fromkeys(ids))[:MAX_TASKS]
    async with _tenant_session() as db:
        order, plans = await order_for(db, user, ids)
    return {"order": order, "plan": {i: p.to_dict() for i, p in plans.items()}}
