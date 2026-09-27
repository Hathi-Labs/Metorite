"""Projects · what a lifecycle act on a parent does to its subtasks (S5).

Spec: ``project-docs/specs/project_management_app.md`` §12.9 (D-PM-38, owner
decisions 2 and 4) and §11.41 (the build record).

Three acts can carry a parent's subtree with them:

* **complete** — each OPEN descendant goes to the first Done status of its
  OWN status set (:func:`complete_subtree`);
* **archive** — each descendant goes on the shelf (:func:`archive_subtree`);
* **move** — each descendant goes to the parent's new project. The move seam
  is ``tasks.move_task_in``, so the move half lives in ``tasks.py`` and this
  module gives it the subtree and the refusal (:func:`hidden_refusal`).

**Every door takes ``include_subtasks`` and every door defaults it to FALSE.**
The owner's defaults are UI defaults: the complete prompt defaults to "Only
this task", and the move and archive dialogs send ``true`` because their box
is ticked. A server default of ``true`` would change what every old caller,
the chat tools and the API do today, with no dialog in front of them.

**Only what the actor can see.** Complete and archive skip a descendant the
actor cannot see, and say nothing about it: a closed or shelved parent with an
open child is a legal state (WS-27p, "shown, never enforced"). A move is
different, because it would split the tree. So a move REFUSES when any
descendant is hidden (:func:`hidden_refusal`).

This module imports only ``core``, so ``tasks``, ``personal`` and ``bulk``
can all import it without a cycle.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException
from gateway.routes.projects.core import (
    CLOSING_CATEGORIES,
    COMPLETED_CATEGORY,
    MAX_DEPTH,
    apply_status_transition,
    archive_note,
    assert_move_keeps_privacy,
    emit,
    load_default_status,
    now,
    record_activity,
    status_owner_id,
    task_visibility_clause,
    update_row,
)
from sqlalchemy import text

#: Every descendant of ``:root``, at any depth, ONE row each.
#:
#: ⚠️ The walk does NOT stop at a task the actor cannot see. A visible
#: grandchild under a hidden child is still a descendant, and the move must
#: know about the hidden one to refuse. ``visible`` is a column, and each
#: caller decides what a hidden row means.
#:
#: Bounded by ``:max_depth``, and the root is excluded in both arms, so a
#: corrupt cycle ends instead of spinning. ``min(depth)`` gives each task one
#: row and orders the walk from the top down.
_SUBTREE_SQL = """
WITH RECURSIVE subtree AS (
    SELECT c.id, 1 AS depth
      FROM pm_tasks c
     WHERE c.parent_task_id = CAST(:root AS uuid)
       AND c.id <> CAST(:root AS uuid)
    UNION ALL
    SELECT c.id, s.depth + 1
      FROM pm_tasks c JOIN subtree s ON c.parent_task_id = s.id
     WHERE s.depth < :max_depth
       AND c.id <> CAST(:root AS uuid)
)
SELECT t.*, d.depth AS subtree_depth,
       st.name AS status_name, st.category AS status_category,
       CASE WHEN {visible} THEN true ELSE false END AS visible
  FROM (SELECT id, min(depth) AS depth FROM subtree GROUP BY id) d
  JOIN pm_tasks t ON t.id = d.id
  LEFT JOIN pm_task_statuses st ON st.id = t.status_id
 ORDER BY d.depth, t.created_at, t.id
"""


async def load_subtree(db: Any, vis: Any, task_id: str) -> list[Any]:
    """Every descendant of ``task_id``, top down, each with ``visible``.

    ``vis`` is the caller's ``core.Visibility``, resolved from the session and
    never from request input (R5). Each row also carries ``status_name`` and
    ``status_category``, so no caller reads the lane again.
    """
    rows = (await db.execute(
        text(_SUBTREE_SQL.format(visible=task_visibility_clause(vis))),
        {"root": str(task_id), "max_depth": MAX_DEPTH, **vis.params},
    )).fetchall()
    return list(rows)


def task_ref(row: Any) -> str:
    """``#42``, or the id when the task has no number."""
    number = getattr(row, "task_number", None)
    return f"#{number}" if number is not None else str(row.id)


def hidden_refusal(count: int) -> str:
    """The 409 a move with its subtasks answers when some are hidden.

    ⚠️ **It names the COUNT, on purpose.** The member asked to move the
    subtasks, and "some subtasks" gives them nothing to act on. The count
    says that such tasks exist. It says nothing about what they are.
    """
    noun = "subtask of this task is" if count == 1 else "subtasks of this task are"
    return (
        f"{count} {noun} hidden from you. A move with its subtasks would "
        "leave them behind and split the tree, so nothing moved. Move the "
        "task without its subtasks, or ask somebody who can see them."
    )


@dataclass
class Cascade:
    """What one cascade did, for the response and for the events.

    ``changes`` is the Undo record for a complete: each task's status before
    and after, so a client can put back the EXACT prior status (D79) and
    only while the task still holds the one the cascade set.
    """

    ids: list[str] = field(default_factory=list)
    changes: list[dict[str, Any]] = field(default_factory=list)
    events: list[tuple[str, dict[str, Any]]] = field(default_factory=list)


async def complete_subtree(
    db: Any, vis: Any, task_id: str, *, by: str,
) -> Cascade:
    """Close every OPEN, visible, filed-in descendant of ``task_id``.

    Each one goes to the FIRST Done status of its OWN status set, through
    ``load_default_status`` (D79), the one resolver. A child in another
    project, or in a subproject that owns its lanes, lands in that set's
    Done status. No status from another set is ever written.

    Each move goes through ``apply_status_transition``, so each child gets
    its ``completed_at``, its timeline entry and, for a recurring task, its
    next instance, exactly as a single Mark done would.

    ⚠️ **A set with no Done status refuses the WHOLE cascade (409).** D79
    keeps a Done status in every set, so this fires only on a set older than
    that guard. A partial cascade would leave the member unsure which tasks
    closed. "Only this task" still works, because it touches no child.

    Skipped: a closed descendant (nothing to do), an archived one (it is off
    every board) and a hidden one (the actor cannot act on it).
    """
    out = Cascade()
    for row in await load_subtree(db, vis, task_id):
        if not row.visible or getattr(row, "archived_at", None) is not None:
            continue
        if getattr(row, "status_category", None) in CLOSING_CATEGORIES:
            continue
        owner = await status_owner_id(db, str(row.project_id))
        try:
            done = await load_default_status(db, owner, COMPLETED_CATEGORY)
        except HTTPException:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Subtask {task_ref(row)} is in a status set with no Done "
                    "status, so the subtasks were not completed. Add a Done "
                    "status to that set, or complete only this task."
                ),
            ) from None
        moved = await apply_status_transition(db, row, str(done.id), created_by=by)
        out.ids.append(str(row.id))
        out.changes.append({
            "task_id": str(row.id),
            "from_status_id": str(row.status_id),
            "to_status_id": str(done.id),
            "recurred_to": moved.get("recurred_to"),
        })
        out.events.append(("pm.task.status_changed", {
            "task_id": str(row.id),
            "from": moved["from"].name,
            "to": moved["to"].name,
            "to_category": moved["to"].category,
        }))
    return out


async def archive_subtree(
    db: Any, vis: Any, task_id: str, *, by: str,
) -> Cascade:
    """Shelve every visible descendant of ``task_id`` that is not shelved.

    The same write and the same timeline words as ``POST /tasks/{id}/archive``
    (``core.archive_note`` names the lane). ``meta.cascade_from`` says which
    parent took it along.

    ⚠️ **Unarchive does NOT cascade.** A child can be on the shelf for its
    own reason, and nothing on the row tells the two apart. The client's Undo
    restores exactly ``ids``, which are the tasks THIS cascade shelved.
    """
    out = Cascade()
    for row in await load_subtree(db, vis, task_id):
        if not row.visible or getattr(row, "archived_at", None) is not None:
            continue
        await update_row(db, "pm_tasks", str(row.id), {"archived_at": now()})
        await record_activity(
            db, activity_type="system", created_by=by, task_id=str(row.id),
            body=archive_note(
                getattr(row, "status_name", None),
                str(getattr(row, "status_category", "") or ""),
            ),
            meta={"cascade_from": str(task_id)},
        )
        out.ids.append(str(row.id))
        out.events.append(("pm.task.archived", {"task_id": str(row.id)}))
    return out


def movable_subtree(rows: list[Any]) -> list[Any]:
    """The descendants a move with its subtasks takes, or a 409.

    Refused when ANY descendant is hidden: moving only the visible ones would
    split the tree with nothing on screen to say so (decision 4, "no split
    tree"). Checked before any write, so the refusal leaves everything where
    it was.
    """
    hidden = sum(1 for row in rows if not row.visible)
    if hidden:
        raise HTTPException(status_code=409, detail=hidden_refusal(hidden))
    return rows


def privacy_refusal(row: Any) -> str:
    """What D62 says when a SUBTASK, not the task, cannot make the move.

    It names the subtask, because the parent is not the problem, and it names
    the remedy the dialog offers: untick the box.
    """
    return (
        f"Subtask {task_ref(row)} is in a project that is not personal, so it "
        "cannot move into a personal project. Untick \"Include subtasks\" to "
        "move the task without it."
    )


async def subtree_privacy_refusals(
    db: Any, rows: list[Any], dest_id: str,
) -> list[dict[str, str]]:
    """D62 for each descendant a move would carry (`assert_move_keeps_privacy`).

    One ``{task_id, ref, reason}`` per subtask the guard refuses. The preview
    shows them, so the card can say why Move is held. The apply refuses the
    first one, before any write. A row already in the destination moves
    nowhere, so the guard has nothing to judge there.
    """
    out: list[dict[str, str]] = []
    for row in rows:
        if str(row.project_id) == str(dest_id):
            continue
        try:
            await assert_move_keeps_privacy(db, row, dest_id)
        except HTTPException:
            out.append({
                "task_id": str(row.id), "ref": task_ref(row),
                "reason": privacy_refusal(row),
            })
    return out


async def current_category(db: Any, task_id: str) -> str | None:
    """The category of the lane ``task_id`` sits in NOW, after a write."""
    row = (await db.execute(
        text(
            "SELECT s.category FROM pm_tasks t "
            "  JOIN pm_task_statuses s ON s.id = t.status_id "
            " WHERE t.id = CAST(:tid AS uuid)"
        ),
        {"tid": str(task_id)},
    )).fetchone()
    return None if row is None else str(row.category)


async def emit_all(events: list[tuple[str, dict[str, Any]]]) -> None:
    """Emit a cascade's events, after the commit, the way every route does."""
    for event_type, payload in events:
        await emit(event_type, payload)


__all__ = [
    "Cascade",
    "archive_subtree",
    "complete_subtree",
    "current_category",
    "emit_all",
    "hidden_refusal",
    "load_subtree",
    "movable_subtree",
    "privacy_refusal",
    "subtree_privacy_refusals",
    "task_ref",
]
