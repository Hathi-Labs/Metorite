"""Projects · fold one task into another.

Spec: ``project-docs/specs/project_management_app.md`` §11.18. Migration
``210_projects_task_merge.sql``.

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
work on it. Migration 210's ``pm_tasks_merged_is_archived`` makes that
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
    MAX_DEPTH,
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
from gateway.routes.projects.relations import assert_no_block_cycle
from pydantic import BaseModel
from sqlalchemy import text

#: Every satellite the merge moves, as `(table, the rest of its unique key)`.
#:
#: ``None`` means the table's key is the task alone, so at most one row can
#: exist per task — `pm_intake` is the case.
#:
#: 🔴 **NOTHING IS DELETED, and the first draft deleted plenty.** It moved
#: four of these with a blind UPDATE under the claim that they had "no key
#: that could collide", and de-duplicated three others by DELETING the
#: source's row. Adversarial review found both halves wrong against a real
#: database:
#:
#:   * `pm_intake.task_id` is UNIQUE, so merging two captured tasks answered
#:     500 — and two emails about one thing is the canonical use of this
#:     feature;
#:   * `pm_task_attachments` is keyed `(task_id, attachment_id)`, the same
#:     hazard one upload path away;
#:   * `pm_task_personal` holds a member's Calendar block and their tracked
#:     actuals. "Two rows say the same thing" is true of an assignee and
#:     false of that table, so the de-dupe destroyed real work.
#:
#: The repair is one rule for all of them, and it is simpler than what it
#: replaced: **move a row only where the target has none for the same key,
#: and otherwise leave it where it is.** Nothing collides and nothing is
#: lost, because the source survives as a stub and its own rows stay
#: readable on it. The target still ends up with the union, which is what
#: the merge owed.
_MOVE: tuple[tuple[str, str | None], ...] = (
    # History and comments: the content people merge tasks to keep together.
    ("pm_activities", "id"),
    ("pm_task_attachments", "attachment_id"),
    ("pm_notifications", "id"),
    ("pm_task_assignees", "assignee"),
    ("pm_task_watchers", "watcher"),
    ("pm_task_personal", "member_email"),
    ("pm_view_task_positions", "view_id"),
    # ⚠️ One intake row per task, ever. It records how the task was CREATED,
    # which stays true of the stub — so it moves only when the target has no
    # origin of its own, and otherwise stays put rather than claiming the
    # request created a task it did not.
    ("pm_intake", None),
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
    """Everything hanging off the source becomes the target's, where it can.

    ⚠️ Driven by :data:`_MOVE` rather than by thirteen hand-written
    statements. `pm_tasks` has thirteen foreign keys pointing at it, and the
    failure mode of writing them out is the fourteenth: a satellite added
    later that nobody remembers, whose rows then vanish when the stub is
    deleted.

    One statement per table, and it never deletes. See :data:`_MOVE`.
    """
    keys = {"src": source_id, "dst": target_id}
    for table, other in _MOVE:
        clash = (
            f"SELECT 1 FROM {table} b WHERE b.task_id = CAST(:dst AS uuid)"  # noqa: S608
            + (f" AND b.{other} = a.{other}" if other else "")
        )
        await db.execute(
            text(
                f"UPDATE {table} a SET task_id = CAST(:dst AS uuid) "  # noqa: S608
                f"WHERE a.task_id = CAST(:src AS uuid) "
                f"AND NOT EXISTS ({clash})"
            ),
            keys,
        )


async def _move_links(db: Any, source_id: str, target_id: str) -> None:
    """Re-point the source's dependencies at the target, without nonsense.

    Row by row rather than in one UPDATE, because three of the four outcomes
    are not a move:

    * **a self-link** — the source blocked the target, or the reverse, and
      after the merge both ends would be the same task;
    * **a duplicate** — both tasks blocked the same third task, and
      `UNIQUE (source, target, link_type)` would refuse the second;
    * **a CYCLE** — S blocks X and X blocks T, so re-pointing S's edge onto
      T closes a loop.

    🔴 **The first draft's docstring said a cycle could not appear here.**
    It reasoned that re-pointing onto a task holding the other end is the
    self-link case, which is true only when that task IS the target. With a
    third task in the middle the loop closes, and adversarial review built
    one against a real database. `assert_no_block_cycle` calls that state "a
    deadlock no human can resolve by finishing something" and the link
    endpoint refuses it with 422 — so a merge must not quietly write it.

    ⚠️ The cycle test is the EXISTING guard, not a second implementation.
    An edge that would close a loop is dropped rather than moved: the
    target's own dependency graph is the one being kept, exactly as its
    scalars are.
    """
    rows = (await db.execute(
        text(
            "SELECT id, source_task_id, target_task_id, link_type "
            "FROM pm_task_links WHERE source_task_id = CAST(:src AS uuid) "
            "OR target_task_id = CAST(:src AS uuid)"
        ),
        {"src": source_id},
    )).fetchall()

    for row in rows:
        src = target_id if str(row.source_task_id) == source_id else str(row.source_task_id)
        dst = target_id if str(row.target_task_id) == source_id else str(row.target_task_id)
        drop = src == dst
        if not drop:
            twin = (await db.execute(
                text(
                    "SELECT 1 FROM pm_task_links WHERE id <> CAST(:id AS uuid) "
                    "AND source_task_id = CAST(:s AS uuid) "
                    "AND target_task_id = CAST(:d AS uuid) AND link_type = :k"
                ),
                {"id": str(row.id), "s": src, "d": dst, "k": row.link_type},
            )).fetchone()
            drop = twin is not None
        if not drop and row.link_type == "blocks":
            try:
                await assert_no_block_cycle(db, src, dst)
            except HTTPException:
                drop = True
        if drop:
            await db.execute(
                text("DELETE FROM pm_task_links WHERE id = CAST(:id AS uuid)"),
                {"id": str(row.id)},
            )
            continue
        await db.execute(
            text(
                "UPDATE pm_task_links SET source_task_id = CAST(:s AS uuid), "
                "target_task_id = CAST(:d AS uuid) WHERE id = CAST(:id AS uuid)"
            ),
            {"s": src, "d": dst, "id": str(row.id)},
        )


async def _descends_from(db: Any, task_id: str, ancestor_id: str) -> bool:
    """Is `task_id` under `ancestor_id`, at any depth?

    🔴 **The first draft asked only "is its parent the source".** That misses
    the grandchild, and merging a task into its own grandchild made the two
    tasks each other's parent — a shape `assert_no_task_cycle` refuses on
    every other write path in the product. Adversarial review built it
    against a real database.

    Bounded by `MAX_DEPTH`, for `assert_no_task_cycle`'s reason: an
    unbounded walk over data somebody can create is a denial-of-service
    surface rather than a thorough check.
    """
    at: str | None = task_id
    for _ in range(MAX_DEPTH):
        if at is None:
            return False
        if str(at) == str(ancestor_id):
            return True
        row = (await db.execute(
            text("SELECT parent_task_id FROM pm_tasks WHERE id = CAST(:i AS uuid)"),
            {"i": str(at)},
        )).fetchone()
        at = str(row.parent_task_id) if row and row.parent_task_id else None
    return False


async def _reparent_children(db: Any, source_id: str, target: Any) -> list[str]:
    """The source's subtasks become the target's. Returns the ones that moved.

    ⚠️ Two orderings matter here and neither is obvious.

    **The target may be UNDER the source**, at any depth. Merging a parent
    into one of its own descendants is a real gesture — "this turned out to
    be the whole of it". Re-pointing blindly would put the target inside its
    own subtree, so it is lifted to the source's own parent first.

    **A subtask is not merged, it is moved.** It is a task in its own right
    with its own content, and folding it in would destroy work nobody named.

    The ids come back because a moved subtask needs a `touch_task` of its
    own: `parent_task_id` changes and `updated_at` does not, so no delta
    client would ever see it — the failure `tasks.py` already names on its
    own promote path.
    """
    if await _descends_from(db, str(target.id), source_id):
        await db.execute(
            text(
                "UPDATE pm_tasks SET parent_task_id = "
                "(SELECT parent_task_id FROM pm_tasks WHERE id = CAST(:src AS uuid)) "
                "WHERE id = CAST(:dst AS uuid)"
            ),
            {"src": source_id, "dst": str(target.id)},
        )
    moved = (await db.execute(
        text(
            "UPDATE pm_tasks SET parent_task_id = CAST(:dst AS uuid) "
            "WHERE parent_task_id = CAST(:src AS uuid) "
            "AND id <> CAST(:dst AS uuid) RETURNING id"
        ),
        {"src": source_id, "dst": str(target.id)},
    )).fetchall()
    return [str(r.id) for r in moved]


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

        bumped: list[str] = []
        for source in sources:
            await _move_links(db, str(source.id), task_id)
            bumped += await _reparent_children(db, str(source.id), target)
            await _move_satellites(db, str(source.id), task_id)
            # 🔴 Everything that already pointed at this source now points at
            # the survivor instead.
            #
            # Without it a CHAIN forms: merge A into B today, then B into C
            # tomorrow — B is an ordinary task at the second merge, so nothing
            # refuses it — and A is left aiming at a stub. Opening A by its
            # old link then lands on an empty task, which is the one promise
            # merging makes and the one it would have broken. Found in
            # adversarial review, and the client comment that followed one hop
            # said this could not happen.
            #
            # It also keeps the invariant the client relies on: a stub always
            # points at a live task, so one hop is always enough.
            await db.execute(
                text(
                    "UPDATE pm_tasks SET merged_into_task_id = CAST(:dst AS uuid) "
                    "WHERE merged_into_task_id = CAST(:src AS uuid)"
                ),
                {"src": str(source.id), "dst": task_id},
            )

        values = _fold_scalars(target, sources)
        row = await update_row(db, "pm_tasks", task_id, values) if values else target

        stamp = now()
        names = ", ".join(f"#{s.task_number}" for s in sources)
        for source in sources:
            # ⚠️ Archived in the SAME statement as the pointer. Migration
            # 210's CHECK refuses one without the other, which is what keeps
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
        # A subtask that changed parent is an edit no delta client can see
        # otherwise — `parent_task_id` moves and `updated_at` does not.
        # `tasks.py` does exactly this on its own promote path and names the
        # failure there.
        for child in dict.fromkeys(bumped):
            await touch_task(db, child)
        result = row_to_dict(row, TaskModel)
        result["merged"] = [str(s.id) for s in sources]

    for source in sources:
        await emit("pm.task.merged", {
            "task_id": str(source.id), "into": task_id,
        })
    return result
