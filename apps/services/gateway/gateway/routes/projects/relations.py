"""Projects · dependencies and subtasks, made reachable (WS-27p).

Spec: ``project-docs/specs/project_management_app.md`` §11.2 item 8, §11.14.

    GET /projects/tasks/{task_id}/relations   → subtasks + links, both directions

*"`pm_task_links` and `parent_task_id` both exist, unreachable from the board.
Data with no surface is a promise the product does not keep."*

**Both halves were genuinely unreachable, and for different reasons.** Links
could be created and deleted since WS-27a but never LISTED — `get_task` returns
a `links` *count* and nothing else, so no client could draw one. Subtasks could
be created from the panel but never listed either: `?parent_task_id=` exists on
the list endpoint and nothing called it.

**No migration.** The tables have been right since 146; what was missing was a
way to read them and one rule nobody had written down.

**That rule: `blocks` may not form a cycle.** `assert_no_task_cycle` has guarded
`parent_task_id` since WS-27a, and the same hazard sat unguarded on links — A
blocks B blocks C blocks A is a deadlock no human can resolve by finishing
something, and any "is this blocked" walk over it does not terminate.

**Blocked-ness is DERIVED and SHOWN, never enforced.** A task is blocked when
something that blocks it is still open. Refusing to close a blocked task is the
obvious next step and is deliberately not taken: dependencies in a real
workspace are frequently approximate, and a tool that will not let somebody
finish work they have finished is a tool they route around — after which the
links stop being maintained and the feature is worse than absent.
"""

from __future__ import annotations

from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException
from gateway.routes.projects.core import (
    CLOSING_CATEGORIES,
    MAX_DEPTH,
    _tenant_session,
    load_visible_task,
    record_activity,
    resolve_visibility,
    router,
    task_visibility_clause,
    touch_task,
    wire,
)
from sqlalchemy import text

LINK_TYPES: tuple[str, ...] = ("blocks", "relates_to", "duplicates")

#: The only link type with a direction that means anything to scheduling, and so
#: the only one a cycle can deadlock. `relates_to` and `duplicates` are
#: associations — a cycle in them is redundant, not harmful, and refusing one
#: would be a rule with no failure to prevent.
DIRECTED_TYPES: tuple[str, ...] = ("blocks",)


def blocked_by_open(blockers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Of the tasks blocking this one, those that are still open.

    Pure, and separate from the query, because "blocked" is a derived word this
    app now shows in three places and it must mean the same thing in all of
    them: a blocker that is `done` or `cancelled` no longer blocks anything.
    """
    return [b for b in blockers if b.get("category") not in CLOSING_CATEGORIES]


def subtask_progress(children: list[dict[str, Any]]) -> dict[str, int]:
    """``{done, total}`` for a set of subtasks.

    Counted from the child's status CATEGORY rather than from `completed_at`,
    for the same reason everything else in this app keys off the category: a
    project can name its finished lane anything, and `cancelled` counts as
    resolved even though nothing was completed.
    """
    total = len(children)
    done = sum(1 for c in children if c.get("category") in CLOSING_CATEGORIES)
    return {"done": done, "total": total}


async def assert_no_block_cycle(db: Any, source_id: str, target_id: str) -> None:
    """Refuse a ``blocks`` link that would close a loop.

    Walks forward from the proposed target: if the chain of things *it* blocks
    ever reaches the source, the new link would complete a cycle.

    The same hazard `assert_no_task_cycle` guards on `parent_task_id`, and it
    was unguarded here — A blocks B blocks C blocks A is a deadlock no human can
    resolve by finishing something, and every walk over it runs forever.

    Bounded by `MAX_DEPTH` like its sibling: a chain longer than that is already
    a chain nobody is reading, and an unbounded walk over data somebody can
    create is a denial-of-service surface rather than a thorough check.
    """
    if str(source_id) == str(target_id):
        raise HTTPException(
            status_code=422, detail="A task cannot block itself.",
        )
    frontier = {str(target_id)}
    seen: set[str] = set()
    for _ in range(MAX_DEPTH):
        if not frontier:
            return
        if str(source_id) in frontier:
            raise HTTPException(
                status_code=422,
                detail="That link would make a loop: this task already depends "
                       "on the one you are blocking, so neither could ever "
                       "start.",
            )
        seen |= frontier
        rows = (await db.execute(
            text(
                "SELECT target_task_id FROM pm_task_links "
                " WHERE link_type = 'blocks' "
                "   AND source_task_id = ANY(CAST(:ids AS uuid[]))"
            ),
            {"ids": sorted(frontier)},
        )).fetchall()
        frontier = {str(r.target_task_id) for r in rows} - seen
    raise HTTPException(
        status_code=422,
        detail="This dependency chain is longer than the supported maximum.",
    )


async def insert_task_link(
    db: Any, source_id: str, target_id: str, link_type: str, *, by: str,
) -> str:
    """Write one task link inside the caller's transaction. Returns its id.

    The ONE insert of ``pm_task_links``. ``POST /tasks/{id}/links`` calls it
    after its checks, and so do the links from text (TR-5, ``text_links.py``).
    It writes the ``link`` activity on the source and bumps the target,
    because a link is readable from both ends. The caller has checked that
    both tasks are visible, and the cycle rule for ``blocks``.
    """
    row = (await db.execute(
        text(
            "INSERT INTO pm_task_links "
            "(source_task_id, target_task_id, link_type, created_by) "
            "VALUES (CAST(:src AS uuid), CAST(:tgt AS uuid), :kind, :who) "
            "ON CONFLICT (source_task_id, target_task_id, link_type) "
            "DO UPDATE SET link_type = EXCLUDED.link_type RETURNING id"
        ),
        {"src": source_id, "tgt": target_id, "kind": link_type, "who": by},
    )).fetchone()
    await record_activity(
        db, activity_type="link", created_by=by, task_id=source_id,
        meta={"target": target_id, "type": link_type},
    )
    # WS-27ae / P-27 — the activity above bumps the SOURCE. A link is
    # readable from both ends (`attach_relation_counts` draws "blocked" on
    # the target), so the target changed too and a delta client watching
    # only the blocked task would otherwise never learn it is blocked.
    await touch_task(db, target_id)
    return str(row.id)


#: Subtasks, with the one status field the panel needs to draw progress.
#:
#: Visibility is applied to the CHILDREN, not inherited from the parent. A
#: subtask can be moved into a project the reader cannot see, and listing it
#: because its parent is readable would disclose a title from behind a grant.
_SUBTASKS_SQL = """
SELECT t.id, t.title, t.task_number, t.status_id, t.completed_at,
       t.start_date, t.due_at,
       s.name AS status_name, s.category
  FROM pm_tasks t
  JOIN pm_task_statuses s ON s.id = t.status_id
 WHERE t.parent_task_id = CAST(:tid AS uuid)
   AND t.archived_at IS NULL
   AND {visible}
 ORDER BY t.task_number NULLS LAST, t.created_at
"""

#: Links in BOTH directions, in one query.
#:
#: `direction` says which end this task is on, because the two read completely
#: differently: `blocks` outgoing means "this holds those up", incoming means
#: "this is waiting". A client given only one side would have to ask twice and
#: would still not know which was which.
_LINKS_SQL = """
SELECT l.id, l.link_type, 'outgoing' AS direction,
       t.id AS other_id, t.title, t.task_number, t.completed_at,
       t.start_date, t.due_at,
       s.name AS status_name, s.category
  FROM pm_task_links l
  JOIN pm_tasks t ON t.id = l.target_task_id
  JOIN pm_task_statuses s ON s.id = t.status_id
 WHERE l.source_task_id = CAST(:tid AS uuid) AND {visible}
UNION ALL
SELECT l.id, l.link_type, 'incoming' AS direction,
       t.id AS other_id, t.title, t.task_number, t.completed_at,
       t.start_date, t.due_at,
       s.name AS status_name, s.category
  FROM pm_task_links l
  JOIN pm_tasks t ON t.id = l.source_task_id
  JOIN pm_task_statuses s ON s.id = t.status_id
 WHERE l.target_task_id = CAST(:tid AS uuid) AND {visible}
"""


def _row(row: Any) -> dict[str, Any]:
    return {
        "id": str(row.other_id),
        "link_id": str(row.id),
        "link_type": row.link_type,
        "direction": row.direction,
        "title": row.title,
        "task_number": row.task_number,
        "status_name": row.status_name,
        "category": row.category,
        "completed_at": wire(row.completed_at),
        # WS-27t — the dates the schedule-conflict warning is computed from.
        # Carried here so ONE pure rule serves the timeline's red arrow and the
        # panel's sentence; two implementations of "does this start before its
        # blocker finishes" would eventually disagree, and the surface that got
        # it wrong would be the one nobody was looking at.
        "start_date": wire(row.start_date),
        "due_at": wire(row.due_at),
    }


@router.get("/tasks/{task_id}/relations")
async def get_relations(
    task_id: str, user: UserContext = Depends(get_current_user),
) -> dict:
    """One task's subtasks and links, with enough of each to render.

    ONE endpoint rather than three, because the panel needs all of it at once
    and three round trips to fill one block is three chances to paint a
    half-drawn dependency section.
    """
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        await load_visible_task(db, vis, task_id)
        visible = task_visibility_clause(vis)

        children = [
            {
                "id": str(r.id), "title": r.title, "task_number": r.task_number,
                "status_id": str(r.status_id), "status_name": r.status_name,
                "category": r.category, "completed_at": wire(r.completed_at),
                "start_date": wire(r.start_date), "due_at": wire(r.due_at),
            }
            for r in (await db.execute(
                text(_SUBTASKS_SQL.format(visible=visible)),
                {"tid": task_id, **vis.params},
            )).fetchall()
        ]

        links = [
            _row(r) for r in (await db.execute(
                text(_LINKS_SQL.format(visible=visible)),
                {"tid": task_id, **vis.params},
            )).fetchall()
        ]

        # "Blocked by" is the INCOMING half of `blocks`: somebody else's task
        # names this one as the thing it holds up.
        blockers = [
            link for link in links
            if link["link_type"] == "blocks" and link["direction"] == "incoming"
        ]
        return {
            "subtasks": children,
            "progress": subtask_progress(children),
            "links": links,
            "blocked_by": blocked_by_open(blockers),
        }


__all__ = [
    "DIRECTED_TYPES",
    "LINK_TYPES",
    "assert_no_block_cycle",
    "blocked_by_open",
    "insert_task_link",
    "subtask_progress",
]
