"""Projects · fold one task into another.

Spec: ``project-docs/specs/project_management_app.md`` §11.18. Migration
``209_projects_task_merge.sql``.

    POST /projects/tasks/{task_id}/merge     ← fold others INTO this one

Owner request, 2026-09-21: *"enabling us to merge multiple tasks into one. So
if I right click on a task, I should be able to merge it into another task.
And the data of both the tasks are appropriately combined."*

## The shape

The path names the task that SURVIVES, and the body names the ones folded in.
It reads the way the gesture reads — "merge these into me" — and it means the
survivor is never ambiguous, which matters for a call that moves data one way
and cannot be undone by re-running it.

## What a merge is, in one line

**Everything a source task HOLDS moves to the target, and the source becomes
an archived stub that points at it.**

⚠️ **"Archived" is not a synonym here, it is the mechanism.** A merged task
is exactly an archived task with a pointer. The first design gave merged
tasks a hiding rule of their own and the owner found the hole at once: a row
that no view lists is a row nobody can delete or restore. So merging spends
the shelf that already exists — every `archived_at IS NULL` clause excludes
it for free, the Archived filter lists it, and Delete and Unarchive already
work on it. Migration 209's ``pm_tasks_merged_is_archived`` makes that
unbreakable rather than remembered.

## What combines, and why each way

Set-like things UNION, because dropping half of a union loses work somebody
did. Scalars keep the TARGET's value, because the target is the task being
kept and its owner did not ask for it to change under them. Three scalars
are exceptions, and each one is a judgement rather than a default:

* **estimate** SUMS. Two tasks' work is still two tasks' work.
* **start date** takes the EARLIEST. The combined task starts when the
  earlier of its halves did.
* **due date** takes the EARLIEST, not the latest. Merging must not relax a
  commitment somebody made; the tighter deadline still has to be met.
* **priority** takes the HIGHER. Folding an urgent task into a normal one
  does not make the urgent work less urgent.

``description`` is the one that APPENDS, under a rule naming where the text
came from, because a description is prose somebody wrote and neither losing
it nor silently interleaving it is honest.
"""

from __future__ import annotations

from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException
from gateway.routes.projects.bulk import MAX_BULK
from gateway.routes.projects.core import (
    TaskModel,
    _tenant_session,
    actor,
    emit,
    load_visible_task,
    now,
    record_activity,
    resolve_visibility,
    router,
    row_to_dict,
    touch_task,
    update_row,
)
from pydantic import BaseModel
from sqlalchemy import text

#: Tables whose rows simply MOVE: everything the source holds becomes the
#: target's, with no key that could collide.
#:
#: ⚠️ `pm_activities` is in here and that is the point of the feature. The
#: comments and the history are the content people merge tasks to keep
#: together; leaving them behind on a stub nobody opens would make the merge
#: a deletion with extra steps.
_MOVE_WHOLE: tuple[tuple[str, str], ...] = (
    ("pm_activities", "task_id"),
    ("pm_task_attachments", "task_id"),
    ("pm_notifications", "task_id"),
    ("pm_intake", "task_id"),
    ("pm_intake", "duplicate_of_task_id"),
)

#: Tables with a UNIQUE key that includes the task: the source's rows move
#: only where the target has none for the same key, and the rest are dropped.
#:
#: Dropping is right and not lossy: two rows for the same (task, person) say
#: the same thing, and the target's is the one already attached to the task
#: that survives. `(table, the other half of the key)`.
_MOVE_UNIQUE: tuple[tuple[str, str], ...] = (
    ("pm_task_assignees", "assignee"),
    ("pm_task_watchers", "watcher"),
    ("pm_task_personal", "member_email"),
    ("pm_view_task_positions", "view_id"),
)


class MergeIn(BaseModel):
    #: The tasks to fold in. Plural from the start: the owner asked for
    #: "merge multiple tasks into one", and the multi-select is one of the
    #: two doors onto this.
    sources: list[str]


async def _load_mergeable(db: Any, vis: Any, task_id: str, target: Any) -> Any:
    """One source task, or a 4xx that says exactly why not.

    Each refusal is a state the caller could otherwise create that nothing
    downstream knows how to draw.
    """
    source = await load_visible_task(db, vis, task_id)
    if str(source.id) == str(target.id):
        raise HTTPException(
            status_code=422, detail="A task cannot be merged into itself.",
        )
    if getattr(source, "merged_into_task_id", None) is not None:
        raise HTTPException(
            status_code=422,
            detail=f"#{source.task_number} has already been merged.",
        )
    # ⚠️ The owner's ruling, 2026-09-21: same project only. Projects own their
    # statuses, custom fields and task types, so a cross-project merge is a
    # Move plus a merge — and `/move` already handles the status remapping and
    # the destination's required fields. Doing it here would be a second,
    # thinner implementation of that.
    if str(source.project_id) != str(target.project_id):
        raise HTTPException(
            status_code=422,
            detail=(
                f"#{source.task_number} is in a different project. "
                "Move it across first, then merge."
            ),
        )
    return source


def _merged_description(target: Any, sources: list[Any]) -> str | None:
    """The target's prose, then each source's under a rule naming it.

    Returns ``None`` when nothing anywhere had a description, so the column
    is left alone rather than written as an empty string.
    """
    parts: list[str] = []
    own = (getattr(target, "description", None) or "").strip()
    if own:
        parts.append(own)
    for source in sources:
        text_ = (getattr(source, "description", None) or "").strip()
        if not text_:
            continue
        parts.append(
            f"--- merged from #{source.task_number} {source.title} ---\n{text_}"
        )
    if not parts:
        return None
    return "\n\n".join(parts)


def _union_tags(target: Any, sources: list[Any]) -> list[str]:
    """Every tag from every side, in first-seen order, folded on case.

    Order is kept rather than sorted: a member's tag order on the task they
    kept is a thing they chose, and re-sorting it is a change nobody asked
    for. `pm_tags` is case-insensitive elsewhere, so `Urgent` and `urgent`
    are one tag here too.
    """
    out: list[str] = []
    seen: set[str] = set()
    for task in [target, *sources]:
        for tag in getattr(task, "tags", None) or []:
            key = str(tag).strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(str(tag))
    return out


def _fold_scalars(target: Any, sources: list[Any]) -> dict[str, Any]:
    """The columns a merge changes on the surviving task.

    Only the ones that actually move are returned, so a merge that changes
    nothing writes nothing and posts no `field_change`.
    """
    values: dict[str, Any] = {}
    every = [target, *sources]

    description = _merged_description(target, sources)
    if description is not None and description != (
        getattr(target, "description", None) or ""
    ):
        values["description"] = description

    tags = _union_tags(target, sources)
    if tags != list(getattr(target, "tags", None) or []):
        values["tags"] = tags

    # Higher is more important: 3 Urgent, 0 Low (`table.ts::IMPORTANCE_OPTIONS`).
    # `None` is "no priority" and loses to any set value.
    priorities = [
        p for p in (getattr(t, "importance", None) for t in every) if p is not None
    ]
    if priorities and max(priorities) != getattr(target, "importance", None):
        values["importance"] = max(priorities)

    # Summed, because two tasks' work is still two tasks' work. A task with
    # no estimate contributes nothing rather than zeroing the total.
    estimates = [
        e for e in (getattr(t, "estimate_mins", None) for t in every) if e is not None
    ]
    if estimates and sum(estimates) != getattr(target, "estimate_mins", None):
        values["estimate_mins"] = sum(estimates)

    starts = [s for s in (getattr(t, "start_date", None) for t in every) if s]
    if starts and min(starts) != getattr(target, "start_date", None):
        values["start_date"] = min(starts)

    # ⚠️ EARLIEST, not latest. Merging must not relax a commitment: if either
    # half was due on Friday, the combined work is still due on Friday.
    dues = [d for d in (getattr(t, "due_at", None) for t in every) if d]
    if dues and min(dues) != getattr(target, "due_at", None):
        values["due_at"] = min(dues)

    # The target's answers stand; a source only fills a key the target has
    # not answered. Overwriting would let a merge silently change a field
    # somebody set on the task they chose to keep.
    custom = dict(getattr(target, "custom_fields", None) or {})
    filled = False
    for source in sources:
        for key, value in (getattr(source, "custom_fields", None) or {}).items():
            if key not in custom or custom[key] in (None, "", []):
                custom[key] = value
                filled = True
    if filled:
        values["custom_fields"] = custom

    return values


async def _move_satellites(db: Any, source_id: str, target_id: str) -> None:
    """Everything hanging off the source becomes the target's.

    ⚠️ Driven by the two tables at the top of this module rather than by
    twelve hand-written statements. `pm_tasks` has twelve foreign keys
    pointing at it, and the failure mode of writing them out is the
    thirteenth: a satellite added later that nobody remembers to move, whose
    rows then cascade away when the stub is deleted.
    """
    keys = {"src": source_id, "dst": target_id}
    for table, column in _MOVE_WHOLE:
        await db.execute(
            text(
                f"UPDATE {table} SET {column} = CAST(:dst AS uuid) "  # noqa: S608
                f"WHERE {column} = CAST(:src AS uuid)"
            ),
            keys,
        )
    for table, other in _MOVE_UNIQUE:
        # Drop the source's row where the target already has one for the same
        # key, then move what is left. Two statements rather than an upsert,
        # because the tables have different column sets and this needs none
        # of them.
        await db.execute(
            text(
                f"DELETE FROM {table} WHERE task_id = CAST(:src AS uuid) "  # noqa: S608
                f"AND {other} IN (SELECT {other} FROM {table} "
                f"WHERE task_id = CAST(:dst AS uuid))"
            ),
            keys,
        )
        await db.execute(
            text(
                f"UPDATE {table} SET task_id = CAST(:dst AS uuid) "  # noqa: S608
                f"WHERE task_id = CAST(:src AS uuid)"
            ),
            keys,
        )


async def _move_links(db: Any, source_id: str, target_id: str) -> None:
    """Re-point the source's dependencies at the target, without nonsense.

    Three things a naive re-point creates, all of which the UI would then
    have to render:

    * **a self-link** — the source blocked the target, or the reverse, and
      after the merge both ends are the same task;
    * **a duplicate** — both tasks blocked the same third task, and
      `UNIQUE (source, target, link_type)` would refuse the second;
    * nothing else: a cycle cannot appear, because re-pointing an edge onto
      a task that already has the other end is exactly the self-link case.

    So the two offenders are deleted FIRST and what survives is moved.
    """
    keys = {"src": source_id, "dst": target_id}
    # Edges between the two tasks themselves. After the merge they would
    # say "this task blocks itself".
    await db.execute(
        text(
            "DELETE FROM pm_task_links WHERE "
            "(source_task_id = CAST(:src AS uuid) AND target_task_id = CAST(:dst AS uuid)) "
            "OR (source_task_id = CAST(:dst AS uuid) AND target_task_id = CAST(:src AS uuid))"
        ),
        keys,
    )
    for mine, theirs in (
        ("source_task_id", "target_task_id"),
        ("target_task_id", "source_task_id"),
    ):
        await db.execute(
            text(
                f"DELETE FROM pm_task_links a WHERE a.{mine} = CAST(:src AS uuid) "  # noqa: S608
                f"AND EXISTS (SELECT 1 FROM pm_task_links b "
                f"WHERE b.{mine} = CAST(:dst AS uuid) AND b.{theirs} = a.{theirs} "
                f"AND b.link_type = a.link_type)"
            ),
            keys,
        )
        await db.execute(
            text(
                f"UPDATE pm_task_links SET {mine} = CAST(:dst AS uuid) "  # noqa: S608
                f"WHERE {mine} = CAST(:src AS uuid)"
            ),
            keys,
        )


async def _reparent_children(db: Any, source_id: str, target: Any) -> None:
    """The source's subtasks become the target's.

    ⚠️ Two orderings matter here and neither is obvious.

    **The target may BE a child of the source.** Merging a parent into its
    own subtask is a real gesture — "this turned out to be the whole of it".
    Re-pointing blindly would make the target its own parent, which the
    `parent_task_id` self-reference does not forbid. So the target is lifted
    to the source's own parent first, and only then do its new siblings
    arrive.

    **A subtask is not merged, it is moved.** It is a task in its own right
    with its own content; folding it in would destroy work nobody named.
    """
    if str(getattr(target, "parent_task_id", None) or "") == str(source_id):
        await db.execute(
            text(
                "UPDATE pm_tasks SET parent_task_id = "
                "(SELECT parent_task_id FROM pm_tasks WHERE id = CAST(:src AS uuid)) "
                "WHERE id = CAST(:dst AS uuid)"
            ),
            {"src": source_id, "dst": str(target.id)},
        )
    await db.execute(
        text(
            "UPDATE pm_tasks SET parent_task_id = CAST(:dst AS uuid) "
            "WHERE parent_task_id = CAST(:src AS uuid) AND id <> CAST(:dst AS uuid)"
        ),
        {"src": source_id, "dst": str(target.id)},
    )


@router.post("/tasks/{task_id}/merge")
async def merge_tasks(
    task_id: str, payload: MergeIn,
    user: UserContext = Depends(get_current_user),
) -> dict:
    """Fold `sources` into this task. The task in the path is the survivor.

    One transaction. A merge that fails half way would leave comments on one
    task, attachments on another and a stub pointing at neither.
    """
    wanted = [t for t in dict.fromkeys(payload.sources or []) if t and t != task_id]
    if not wanted:
        raise HTTPException(
            status_code=422, detail="Name at least one other task to merge in.",
        )
    if len(wanted) > MAX_BULK:
        raise HTTPException(
            status_code=422,
            detail=f"Merge at most {MAX_BULK} tasks at a time.",
        )

    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        target = await load_visible_task(db, vis, task_id)
        # ⚠️ Merging INTO a stub would chain two redirects, and every reader
        # would have to walk the chain. Refused with the end of it named, so
        # the answer is one click away rather than a puzzle.
        if getattr(target, "merged_into_task_id", None) is not None:
            raise HTTPException(
                status_code=422,
                detail=(
                    "That task has itself been merged away. "
                    "Merge into the task it went to instead."
                ),
            )
        sources = [await _load_mergeable(db, vis, t, target) for t in wanted]

        for source in sources:
            await _move_links(db, str(source.id), task_id)
            await _reparent_children(db, str(source.id), target)
            await _move_satellites(db, str(source.id), task_id)

        values = _fold_scalars(target, sources)
        row = await update_row(db, "pm_tasks", task_id, values) if values else target

        stamp = now()
        names = ", ".join(f"#{s.task_number}" for s in sources)
        for source in sources:
            # ⚠️ Archived in the SAME statement as the pointer. Migration
            # 209's CHECK refuses one without the other, which is what keeps
            # a merged task reachable from the Archived shelf.
            await update_row(db, "pm_tasks", str(source.id), {
                "merged_into_task_id": task_id,
                "merged_at": stamp,
                "merged_by": actor(user),
                "archived_at": getattr(source, "archived_at", None) or stamp,
            })
            # On the stub. Its own history has moved, so without this its
            # timeline would be empty and say nothing about where it went.
            await record_activity(
                db, activity_type="merge", created_by=actor(user),
                task_id=str(source.id),
                body=f"Merged into #{target.task_number} {target.title}",
                meta={"merged_into": task_id},
            )

        # And on the survivor, once, naming all of them. `record_activity`
        # bumps the task for the delta feed.
        await record_activity(
            db, activity_type="merge", created_by=actor(user), task_id=task_id,
            body=f"Merged {names} into this task",
            meta={"merged_from": [str(s.id) for s in sources]},
        )
        await touch_task(db, task_id)
        result = row_to_dict(row, TaskModel)
        result["merged"] = [str(s.id) for s in sources]

    for source in sources:
        await emit("pm.task.merged", {
            "task_id": str(source.id), "into": task_id,
        })
    return result
