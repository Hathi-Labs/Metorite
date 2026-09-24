"""Projects · bulk edit (WS-27n).

Spec: ``project-docs/specs/project_management_app.md`` §11.2 item 6, §11.11.

    POST /projects/tasks/bulk    → per-task outcomes

**This one gates WS-27g.** §11.3: the cutover imports a real workspace, and an
import that cannot be re-triaged in bulk is an import somebody abandons halfway,
leaving two live systems — the exact state the retirement exists to end.
Re-triaging fifty imported tasks one at a time is the failure mode.

**It reuses `automation.apply_task_patch` rather than growing a second writer.**
That service already exists because WS-27f needed a task edit that was
*indistinguishable in validation* from a human PATCH; a bulk endpoint with its
own field handling would be a third opinion about what a task edit is, and the
three would drift. Assignees and tags are not part of it — they are separate
write paths on the task — so those are handled here, once, in the same shape.

**Status is named, never keyed — and here that is load-bearing rather than
stylistic.** A bulk selection can span projects, and a status id belongs to
exactly one root. Sending `status_id` for fifty tasks across three projects
would put two thirds of them in a lane that is not theirs, or fail on the
foreign key. `status` resolves per task, against its OWN root.

**Assignees and tags are ADD/REMOVE, never SET.** "Assign these to Priya" means
*also* Priya; a replace across a selection would wipe every individual
assignment the fifty tasks already carried. The destructive spelling is
deliberately absent rather than merely discouraged.
"""

from __future__ import annotations

from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException
from gateway.routes.projects.automation import (
    PATCHABLE_FIELDS,
    TaskPatchError,
    apply_task_patch,
)
from gateway.routes.projects.core import (
    _tenant_session,
    actor,
    assert_assignable_here,
    archive_note,
    clean_payload,
    emit,
    load_visible_task,
    now,
    record_activity,
    resolve_visibility,
    router,
    touch_task,
    update_row,
)
from gateway.routes.projects.notifications import notifiable, notify
from gateway.routes.projects.personal import (
    PersonalIn,
    _reject_impossible_block,
    _reject_waiting_without_since,
    _upsert_personal,
    reopen_if_closed,
    validate_overlay,
)
from gateway.routes.projects.tags import apply_task_tags, normalise_tag
from pydantic import BaseModel
from sqlalchemy import text

#: How many tasks one request may touch.
#:
#: Generous enough for the case this ticket exists for — re-triaging an imported
#: ClickUp list is hundreds of tasks — and bounded, because a single request
#: that can rewrite the whole table is a denial-of-service surface rather than a
#: feature. The same reasoning, and the same shape, as `views.MAX_POSITIONS`.
MAX_BULK = 500


#: The lifecycle verbs a selection can be put through.
#:
#: Separate from `patch` on purpose. A patch SETS FIELDS and composes — you can
#: change status and add a tag in one request. These three do not compose with
#: anything, with each other, or with a patch: there is no coherent reading of
#: "archive it and also rename it", and `delete` makes every other half of such
#: a request meaningless. Modelling them as one exclusive verb is what lets the
#: endpoint refuse the incoherent combinations by name instead of guessing an
#: order.
BULK_ACTIONS = ("archive", "unarchive", "delete", "personal")


class BulkIn(BaseModel):
    task_ids: list[str]
    #: Plain fields plus `status` (by NAME), handed to `apply_task_patch`.
    patch: dict[str, Any] | None = None
    assignees_add: list[str] | None = None
    assignees_remove: list[str] | None = None
    tags_add: list[str] | None = None
    tags_remove: list[str] | None = None
    #: One of :data:`BULK_ACTIONS`, and then nothing else in this payload.
    action: str | None = None
    #: WS-39 S6a. MY overlay, written to every task in the selection, with
    #: ``action: "personal"``. A separate field rather than keys in `patch`
    #: because `patch` is the TASK's shared fields and this is nobody's but
    #: mine — the same split `PATCH /tasks/{id}` and `/tasks/{id}/personal`
    #: keep, and conflating them here would let one bulk request move the
    #: team's board and my triage in a single unreadable body.
    personal: PersonalIn | None = None


def validate_personal(
    action: str | None, personal: PersonalIn | None,
) -> dict[str, Any]:
    """The overlay half of a bulk request, or a 422 before any task is touched.

    ``disposition: "DONE"`` is refused by name. Under one store a task is
    completed through its project's done lane (``POST /tasks/{id}/complete``),
    and writing DONE onto fifty overlays would mark them done in MY list while
    the board still shows them open — the exact drift §13.5a decision 1 names.
    The client sends completion separately, one call per task.
    """
    if personal is None:
        if action == "personal":
            raise HTTPException(
                status_code=422,
                detail="'personal' needs a `personal` object with the overlay to set.",
            )
        return {}
    if action != "personal":
        raise HTTPException(
            status_code=422,
            detail="A `personal` overlay is applied with action 'personal'.",
        )
    values = validate_overlay(clean_payload(personal))
    if not values:
        raise HTTPException(
            status_code=422, detail="Nothing to change in `personal`.",
        )
    if values.get("disposition") == "DONE":
        raise HTTPException(
            status_code=422,
            detail=(
                "DONE is not an overlay write. Complete each task through "
                "POST /projects/tasks/{id}/complete, which moves it into the "
                "project's done lane and sets your disposition with it."
            ),
        )
    return values


def validate_patch(patch: dict[str, Any] | None) -> dict[str, Any]:
    """Refuse a patch shape before ANY task is touched.

    A field nobody can set is the sender's mistake and is the same mistake for
    every task in the selection, so it is a 422 up front rather than five
    hundred identical per-task failures.

    ``status_id`` gets its own message. It is the mistake somebody makes by
    copying a single-task PATCH body, and "unknown field" would not explain why
    the thing that works on one task is refused on fifty.
    """
    if not patch:
        return {}
    if not isinstance(patch, dict):
        raise HTTPException(status_code=422, detail="patch must be an object.")
    if "status_id" in patch:
        raise HTTPException(
            status_code=422,
            detail="Use 'status' with a lane NAME in a bulk edit. A status id "
                   "belongs to one project, and a selection can span several — "
                   "keying by id would put tasks in a lane that is not theirs.",
        )
    allowed = {*PATCHABLE_FIELDS, "status"}
    unknown = sorted(k for k in patch if k not in allowed)
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"Cannot bulk-set {unknown}. One of: {sorted(allowed)}.",
        )
    return dict(patch)


def clean_people(raw: Any) -> list[str]:
    """Assignee addresses, lowercased and deduplicated.

    R10 — `pm_task_assignees` stores lowercased addresses, so a caller who typed
    a capital must not create a second row for the same person.
    """
    out: list[str] = []
    for entry in raw or ():
        cleaned = (entry or "").strip().lower() if isinstance(entry, str) else ""
        if cleaned and cleaned not in out:
            out.append(cleaned)
    return out


def clean_tag_list(raw: Any) -> list[str]:
    """Tag names, normalised the way `tags.normalise_tag` normalises them."""
    out: list[str] = []
    for entry in raw or ():
        cleaned = normalise_tag(entry)
        if cleaned and cleaned.lower() not in {o.lower() for o in out}:
            out.append(cleaned)
    return out


def is_noop(
    patch: dict[str, Any],
    add_people: list[str],
    drop_people: list[str],
    add_tags: list[str],
    drop_tags: list[str],
) -> bool:
    """Whether this request asks for nothing at all.

    A 422 rather than a cheerful "0 changed": a selection of fifty tasks and an
    empty patch is a client bug, and answering 200 lets it ship.
    """
    return not (patch or add_people or drop_people or add_tags or drop_tags)


def dedupe_ids(raw: list[str]) -> list[str]:
    """The requested ids, in order, without repeats.

    A selection built by shift-clicking can name a task twice; applying the
    patch twice is harmless but reporting it twice makes the counts wrong.
    """
    seen: set[str] = set()
    out: list[str] = []
    for value in raw:
        cleaned = (value or "").strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            out.append(cleaned)
    return out


def moved_people(
    current: set[str], add: list[str], drop: list[str],
) -> tuple[list[str], list[str]]:
    """Of what was asked for, what actually changes on this task.

    Pure, and separated from the write for exactly one reason: **re-asserting
    an assignee somebody already has must not write an `assignment` activity**.
    Fifty tasks that were already Priya's would otherwise each gain a timeline
    entry saying nothing, and a bulk edit that reports "50 changed" when it
    changed nothing is a bulk edit nobody can trust the count of.
    """
    added = [w for w in add if w not in current]
    removed = [w for w in drop if w in current]
    return added, removed


async def _apply_people(
    db: Any, task_id: str, add: list[str], drop: list[str], *, by: str,
) -> tuple[list[str], list[str]]:
    """Add and remove assignees on one task. Returns what actually moved."""
    current = {
        r.assignee for r in (await db.execute(
            text(
                "SELECT assignee FROM pm_task_assignees "
                "WHERE task_id = CAST(:tid AS uuid)"
            ),
            {"tid": task_id},
        )).fetchall()
    }
    added, removed = moved_people(current, add, drop)

    for who in removed:
        await db.execute(
            text(
                "DELETE FROM pm_task_assignees "
                "WHERE task_id = CAST(:tid AS uuid) AND assignee = :who"
            ),
            {"tid": task_id, "who": who},
        )
    for who in added:
        await db.execute(
            text(
                "INSERT INTO pm_task_assignees (task_id, assignee, assigned_by) "
                "VALUES (CAST(:tid AS uuid), :who, :by) "
                "ON CONFLICT (task_id, assignee) DO NOTHING"
            ),
            {"tid": task_id, "who": who, "by": by},
        )
    if added or removed:
        await record_activity(
            db, activity_type="assignment", created_by=by, task_id=task_id,
            # `bulk: true` so a timeline can say "changed with 49 others"
            # rather than presenting a sweep as fifty separate decisions.
            meta={"added": added, "removed": removed, "bulk": True},
        )
    return added, removed


async def _apply_tags(
    db: Any, task: Any, add: list[str], drop: list[str], *, by: str,
) -> list[str] | None:
    """Add and remove tags on one task. Returns the new list, or None if same.

    Goes through `apply_task_tags`, so a tag added in bulk is registered and
    canonicalised exactly as one typed into the panel — the registry cannot be
    true if there is a second door into the array.
    """
    current = list(task.tags or [])
    dropped = {d.lower() for d in drop}
    kept = [t for t in current if t.lower() not in dropped]
    have = {t.lower() for t in kept}
    wanted = kept + [t for t in add if t.lower() not in have]
    if [t.lower() for t in wanted] == [t.lower() for t in current]:
        return None
    return await apply_task_tags(db, str(task.root_project_id), wanted, by=by)


async def _apply_to_one(
    db: Any,
    task: Any,
    *,
    patch: dict[str, Any],
    add_people: list[str],
    drop_people: list[str],
    add_tags: list[str],
    drop_tags: list[str],
    by: str,
) -> tuple[dict[str, Any], list[str]]:
    """Everything one task gets, and who was newly put on it.

    Split out of `bulk_edit` so the endpoint reads as *selection handling* and
    this reads as *one task's edit* — and so the three write paths sit together
    where their order is visible: fields and status first, then assignees, then
    tags.
    """
    task_id = str(task.id)
    outcome: dict[str, Any] = {"task_id": task_id, "changed": []}
    notify_these: list[str] = []

    if patch:
        result = await apply_task_patch(db, task_id, patch, actor=by)
        outcome["changed"] = list(result["changed"])
        outcome["status"] = result.get("status")

    if add_people or drop_people:
        added, removed = await _apply_people(
            db, task_id, add_people, drop_people, by=by,
        )
        if added or removed:
            outcome["changed"].append("assignees")
        notify_these = notifiable(added, exclude=by)

    if add_tags or drop_tags:
        # `task` is the row loaded for the visibility check, and it is still
        # accurate here: `apply_task_patch` writes columns but never `tags`, so
        # re-reading would be a query per task for a value that cannot have
        # moved.
        new_tags = await _apply_tags(db, task, add_tags, drop_tags, by=by)
        if new_tags is not None:
            await db.execute(
                text(
                    "UPDATE pm_tasks SET tags = :tags, updated_at = now() "
                    "WHERE id = CAST(:tid AS uuid)"
                ),
                {"tags": new_tags, "tid": task_id},
            )
            outcome["changed"].append("tags")

    return outcome, notify_these


async def _act_on_one(
    db: Any, task: Any, action: str, *, by: str,
    personal: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """Put ONE task through a lifecycle verb. Returns ``(outcome, detail)``.

    ``outcome`` is ``"applied"``, ``"skipped"`` or ``"failed"`` — the same
    three buckets the edit path reports, so a caller reads one response shape
    whichever verb it sent.

    ⚠️ **Idempotent where the single-task routes are idempotent.** Archiving an
    already-archived task is ``skipped``, not an error: a selection of fifty
    that contains three already-filed tasks is a normal thing to do twice, and
    failing it would teach people to avoid the button.
    """
    task_id = str(task.id)

    if action == "personal":
        # MY overlay on this task, through the one upsert `set_personal` uses,
        # with the same two merged-row checks — a partial write is judged
        # against what is already stored, per task, because the fifty rows
        # can each hold a different block or a different chase.
        email = by.lower()
        values = dict(personal or {})
        await _reject_impossible_block(db, task_id, email, values)
        await _reject_waiting_without_since(db, task_id, email, values)
        values["clarified_at"] = now()
        # D77 — un-checking a closed task (the card, Focus mode, Undo all
        # arrive here) reopens it for the board, through the one reopen.
        await reopen_if_closed(db, task, email, values.get("disposition"))
        await _upsert_personal(db, task_id, email, values)
        return "applied", "personal"

    if action == "delete":
        # The tombstone is migration 168's AFTER DELETE trigger, not a
        # statement here — see `tasks.delete_task`, which explains why. The
        # subtasks are PROMOTED by the FK's SET NULL, never destroyed.
        children = [
            str(r.id) for r in (await db.execute(
                text("SELECT id FROM pm_tasks WHERE parent_task_id = CAST(:t AS uuid)"),
                {"t": task_id},
            )).fetchall()
        ]
        await db.execute(
            text("DELETE FROM pm_tasks WHERE id = CAST(:t AS uuid)"), {"t": task_id},
        )
        await touch_task(db, getattr(task, "parent_task_id", None), *children)
        return "applied", "deleted"

    if action == "archive":
        if getattr(task, "archived_at", None) is not None:
            return "skipped", "unchanged"
        status = (await db.execute(
            text(
                "SELECT name, category FROM pm_task_statuses "
                " WHERE id = CAST(:s AS uuid)"
            ),
            {"s": str(task.status_id)},
        )).fetchone()
        # ⚠️ No category guard, in EITHER door. It was removed on 2026-09-21
        # and `core.archive_note` says why. What both doors still do is name
        # the lane in the activity, which is what keeps the history readable
        # once the status stops implying the outcome.
        await update_row(db, "pm_tasks", task_id, {"archived_at": now()})
        await record_activity(
            db, activity_type="system", created_by=by, task_id=task_id,
            body=archive_note(
                getattr(status, "name", None),
                str(getattr(status, "category", "") or ""),
            ),
        )
        return "applied", "archived"

    if getattr(task, "archived_at", None) is None:
        return "skipped", "unchanged"
    # No category guard in this direction: restoring puts work back where
    # people can see it, which is never the trap the archive guard prevents.
    await update_row(db, "pm_tasks", task_id, {"archived_at": None})
    await record_activity(
        db, activity_type="system", created_by=by,
        task_id=task_id, body="Task restored from the archive",
    )
    return "applied", "unarchived"


@router.post("/tasks/bulk")
async def bulk_edit(
    payload: BulkIn, user: UserContext = Depends(get_current_user),
) -> dict:
    """Apply one edit to many tasks, and report what happened to each.

    **Shape is validated once, up front; outcomes are per task.** Those are
    genuinely different failures. A field nobody can set is the same mistake for
    every task in the selection and earns a 422 before anything is written. A
    status name that exists in one project and not another is a fact about *that
    task*, and failing the whole batch for it would make a mixed selection
    unusable — which is precisely the selection somebody makes after an import.

    **A task the caller cannot see is skipped, not an error** (R5). Reporting it
    per id says exactly what a per-id 404 would say and nothing more, and
    aborting instead would let a caller probe for existence by watching whether
    the batch failed.

    **One transaction.** Partial application is the worst outcome available: a
    re-triage that half-happened is harder to recover from than one that did
    not, because nobody can tell which half.
    """
    ids = dedupe_ids(payload.task_ids)
    if not ids:
        raise HTTPException(status_code=422, detail="No tasks selected.")
    if len(ids) > MAX_BULK:
        raise HTTPException(
            status_code=422,
            detail=f"At most {MAX_BULK} tasks per request; got {len(ids)}.",
        )

    patch = validate_patch(payload.patch)
    add_people = clean_people(payload.assignees_add)
    drop_people = clean_people(payload.assignees_remove)
    add_tags = clean_tag_list(payload.tags_add)
    drop_tags = clean_tag_list(payload.tags_remove)

    action = (payload.action or "").strip().lower() or None
    personal = validate_personal(action, payload.personal)
    if action is not None:
        if action not in BULK_ACTIONS:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown action '{action}'. One of: {list(BULK_ACTIONS)}.",
            )
        if not is_noop(patch, add_people, drop_people, add_tags, drop_tags):
            # Refused rather than ordered. "Archive these and also tag them"
            # has two readings — tag then archive, or archive then tag — and a
            # `delete` makes the other half meaningless whichever way it runs.
            raise HTTPException(
                status_code=422,
                detail=(
                    f"'{action}' is an action, not an edit. Send it on its own, "
                    "without a patch, assignees or tags."
                ),
            )
    elif is_noop(patch, add_people, drop_people, add_tags, drop_tags):
        raise HTTPException(
            status_code=422,
            detail="Nothing to change. Send a patch, assignees, tags or an action.",
        )

    who = actor(user)
    applied: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    failed: list[dict[str, str]] = []
    #: address → the tasks they were newly put on, for one notification each.
    newly_assigned: dict[str, list[str]] = {}
    changed_ids: list[str] = []

    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        for task_id in ids:
            try:
                task = await load_visible_task(db, vis, task_id)
            except HTTPException:
                skipped.append({"task_id": task_id, "reason": "not_found"})
                continue

            if action is not None:
                verdict, detail = await _act_on_one(
                    db, task, action, by=who, personal=personal,
                )
                if verdict == "applied":
                    applied.append({"task_id": task_id, "changed": [detail]})
                    changed_ids.append(task_id)
                elif verdict == "skipped":
                    skipped.append({"task_id": task_id, "reason": detail})
                else:
                    failed.append({"task_id": task_id, "reason": detail})
                continue

            if add_people:
                # S6g repair (P2-a). The single-task routes refuse a colleague
                # on a task in somebody's personal tree (`assert_assignable_here`,
                # owner directive 2026-08-26). The bulk path skipped the guard.
                # Per task, BEFORE any write: one private task in a selection
                # fails alone, and the rest of the selection still applies.
                try:
                    await assert_assignable_here(
                        db, str(task.project_id), {p.lower() for p in add_people},
                    )
                except HTTPException as exc:
                    failed.append({"task_id": task_id, "reason": str(exc.detail)})
                    continue

            try:
                outcome, newly = await _apply_to_one(
                    db, task,
                    patch=patch, add_people=add_people, drop_people=drop_people,
                    add_tags=add_tags, drop_tags=drop_tags, by=who,
                )
            except TaskPatchError as exc:
                # Per task, because a status name can exist in one project and
                # not another and the selection may span both.
                failed.append({"task_id": task_id, "reason": str(exc)})
                continue
            for person in newly:
                newly_assigned.setdefault(person, []).append(task_id)

            if outcome["changed"]:
                applied.append(outcome)
                changed_ids.append(task_id)
            else:
                skipped.append({"task_id": task_id, "reason": "unchanged"})

        # ONE notification per person, not one per task. Being handed fifty
        # tasks should ring once and say fifty — fifty bells is a bell people
        # turn off, and a bell nobody reads notifies about nothing (WS-27j's
        # own argument, applied to the case that would break it).
        for person, tasks in sorted(newly_assigned.items()):
            await notify(
                db, recipients=[person], kind="assigned",
                task_id=tasks[0], actor_id=who,
                excerpt=(
                    f"and {len(tasks) - 1} other task(s)" if len(tasks) > 1 else None
                ),
            )

    # Emitted after the commit, and per task: `emit` is best-effort by
    # construction, so an automation that fails cannot roll back a re-triage.
    for task_id in changed_ids:
        await emit("pm.task.updated", {"task_id": task_id})

    return {
        "requested": len(ids),
        "applied": len(applied),
        "results": applied,
        "skipped": skipped,
        "failed": failed,
    }


__all__ = [
    "MAX_BULK",
    "BulkIn",
    "clean_people",
    "clean_tag_list",
    "dedupe_ids",
    "is_noop",
    "moved_people",
    "validate_patch",
    "validate_personal",
]
