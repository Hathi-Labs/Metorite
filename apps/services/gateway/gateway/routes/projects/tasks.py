"""Projects · tasks — task CRUD, the list contract, assignees and links.

Spec: ``project-docs/specs/project_management_app.md`` §4 (``tasks.py`` row).

    GET    /projects/tasks                       → {rows, total}
    POST   /projects/tasks
    GET    /projects/tasks/{id}
    PATCH  /projects/tasks/{id}
    DELETE /projects/tasks/{id}                  → what cascaded
    POST   /projects/tasks/{id}/move
    PUT    /projects/tasks/{id}/assignees        → replaces the set
    POST   /projects/tasks/{id}/links
    DELETE /projects/tasks/{id}/links/{link_id}

Subtasks are tasks with a ``parent_task_id`` — there is no second endpoint and
no second table (§3.5). ``?parent_task_id=`` lists one task's children;
``?include_subtree=`` widens a project filter to its descendants.
"""

from __future__ import annotations

from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends, Header, HTTPException
from gateway.routes.projects.core import (
    CLOSING_CATEGORIES,
    DIRECTIONS,
    TASK_SORTS,
    TASK_SOURCES,
    ListResponse,
    Page,
    TaskIn,
    TaskModel,
    _tenant_session,
    actor,
    apply_status_transition,
    assert_assignable_here,
    assert_epic_has_no_parent,
    assert_move_keeps_privacy,
    assert_no_task_cycle,
    clean_payload,
    count_where,
    diff_changes,
    emit,
    from_jsonb,
    insert_row,
    load_default_status,
    load_visible_project,
    load_visible_task,
    next_task_number,
    node_kind,
    now,
    record_activity,
    record_field_change,
    remap_one_status,
    require_precondition,
    require_row,
    require_status_in_project,
    resolve_visibility,
    root_project_id,
    router,
    row_to_dict,
    status_owner_id,
    task_visibility_clause,
    touch_task,
    triage_exclusion_clause,
    update_row,
    validate_choice,
)
from gateway.routes.projects.custom_fields import (
    apply_values,
    assert_required_fields_present,
    load_definitions,
)
from gateway.routes.projects.filters import (
    attach_assignees,
    attach_relation_counts,
    build_task_filters,
)
from gateway.routes.projects.notifications import (
    excerpt_of,
    new_mentions,
    notify,
)
from gateway.routes.projects.relations import (
    DIRECTED_TYPES,
    assert_no_block_cycle,
)
from gateway.routes.projects.tags import apply_task_tags
from gateway.routes.projects.watchers import ensure_watchers
from pydantic import BaseModel
from sqlalchemy import text

#: Fields whose change earns a timeline entry. `status_id` is absent on purpose:
#: a status move is a TRANSITION and gets its own richer activity, so listing it
#: here too would write the same fact twice under two types.
_TRACKED_TASK_FIELDS: tuple[str, ...] = (
    "title", "description", "importance", "due_at", "start_date",
    "estimate_mins", "type_id", "parent_task_id", "project_id",
)

_LINK_TYPES: tuple[str, ...] = ("blocks", "relates_to", "duplicates")


class MoveTask(BaseModel):
    project_id: str | None = None
    parent_task_id: str | None = None
    #: WS-39. Values for the destination's REQUIRED custom fields, supplied in
    #: the same call that moves the task — the move dialog collects them, so the
    #: server should not need a second round trip to store them.
    custom_fields: dict | None = None
    #: WS-39, owner directive 2026-08-26. Promote-and-assign in ONE transaction.
    #: Two calls (move, then assign) can fail between them, and the wreckage is
    #: worse than either failure alone: a task promoted to a team project,
    #: visible to everyone, owned by nobody. `None` leaves assignees untouched.
    assignees: list[str] | None = None


class AssigneesIn(BaseModel):
    assignees: list[str]


class LinkIn(BaseModel):
    target_task_id: str
    link_type: str = "relates_to"


class DeleteResponse(BaseModel):
    deleted: str
    cascaded: dict[str, int]


# ── List ────────────────────────────────────────────────────────────────────

@router.get("/tasks")
async def list_tasks(
    user: UserContext = Depends(get_current_user),
    project_id: str | None = None,
    include_subtree: bool = False,
    parent_task_id: str | None = None,
    status_id: str | None = None,
    assignee: str | None = None,
    q: str | None = None,
    sort: str | None = None,
    direction: str = "desc",
    page: Page = Depends(),
    include_archived: bool = False,
    # WS-27k. CSV rather than repeated params so a saved view's stored config
    # round-trips through a query string unchanged.
    status_category: str | None = None,
    assignees: str | None = None,
    unassigned: bool = False,
    overdue: bool = False,
    due_before: str | None = None,
    importance_gte: int | None = None,
    # WS-27m. `tags` is ANY, `tags_all` is ALL — see `build_task_filters`.
    tags: str | None = None,
    tags_all: str | None = None,
    # WS-27u. Triage-parked tasks are invisible to every list surface unless
    # asked for — the ONE predicate lives in `core.triage_exclusion_clause`.
    include_triage: bool = False,
    # WS-27bk §9.12.2. A BOOLEAN, resolved here to the caller — never an
    # address from the query string. Letting a caller name whose watches to
    # read would turn a filter into a way of asking what a colleague follows,
    # which is not a question this endpoint should answer.
    watching: bool = False,
) -> ListResponse:
    """The one task-list endpoint every surface reads through.

    Paca's lesson: list, board and any saved view are the same query with
    different filters, so growing a second endpoint per surface is how the
    filters start disagreeing about what a member may see.

    An unknown sort key is a **422**, deliberately not a silent fall back to the
    default — a client sorting by a column it believes exists and quietly
    getting ``created_at`` is a bug that survives review.
    """
    column = TASK_SORTS.get(sort or "created_at")
    if column is None:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown sort key '{sort}'. One of: {sorted(TASK_SORTS)}.",
        )
    order = DIRECTIONS.get((direction or "").lower())
    if order is None:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown sort direction '{direction}'. One of: asc, desc.",
        )

    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        clauses: list[str] = [task_visibility_clause(vis)]
        params: dict[str, Any] = dict(vis.params)

        if project_id:
            # Seeing the project is required to filter by it, so an unreadable
            # id answers 404 rather than an empty list — an empty list would
            # tell the caller the project exists and is simply empty.
            await load_visible_project(db, vis, project_id)
            if include_subtree:
                clauses.append(
                    "t.project_id IN ("
                    "  WITH RECURSIVE sub AS ("
                    "    SELECT id FROM pm_projects WHERE id = CAST(:pid AS uuid)"
                    "    UNION ALL"
                    "    SELECT p.id FROM pm_projects p JOIN sub s"
                    "      ON p.parent_project_id = s.id"
                    "  ) SELECT id FROM sub)"
                )
            else:
                clauses.append("t.project_id = CAST(:pid AS uuid)")
            params["pid"] = project_id
        # Every filter but the project scoping above comes from ONE pure
        # builder, shared with saved views — two implementations would let a
        # saved view show a different set of tasks than the same filters typed
        # by hand, which is the one thing a saved view may not do.
        extra_clauses, extra_params = build_task_filters(
            parent_task_id=parent_task_id, status_id=status_id,
            status_category=status_category, assignee=assignee,
            assignees=assignees, unassigned=unassigned, overdue=overdue,
            due_before=due_before, importance_gte=importance_gte, q=q,
            tags=tags, tags_all=tags_all, include_archived=include_archived,
            watching=watching, viewer=actor(user) if watching else None,
        )
        clauses.extend(extra_clauses)
        params.update(extra_params)
        if not include_triage:
            clauses.append(triage_exclusion_clause())

        where = " WHERE " + " AND ".join(clauses)
        total = (await db.execute(
            text(f"SELECT count(*) FROM pm_tasks t{where}"), params,
        )).scalar() or 0
        # `column` is an allowlisted template with `{dir}` slots; the direction
        # is one of OUR two words, never caller text. Every entry ends with the
        # `(created_at, id)` tiebreaker (core.SORT_TIEBREAK), so the order is
        # total and a tie cannot straddle a page boundary.
        rows = (await db.execute(
            text(
                f"SELECT t.* FROM pm_tasks t{where} "
                f"ORDER BY {column.format(dir=order)} "
                f"LIMIT :limit OFFSET :offset"
            ),
            {**params, "limit": page.limit, "offset": page.offset},
        )).fetchall()
        # Assignees, subtask progress and blocked-ness on the LIST, not only on
        # the single-task read. Without them a card cannot draw an owner, a
        # progress count or a blocked flag — and fetching any of the three per
        # card is N+1 across an imported workspace of hundreds.
        page_rows = [row_to_dict(r, TaskModel) for r in rows]
        await attach_assignees(db, page_rows)
        await attach_relation_counts(db, page_rows)
        return ListResponse(rows=page_rows, total=int(total))


@router.get("/tasks/{task_id}")
async def get_task(
    task_id: str, user: UserContext = Depends(get_current_user),
) -> dict:
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        row = await load_visible_task(db, vis, task_id)
        result = row_to_dict(row, TaskModel)
        assignees = (await db.execute(
            text(
                "SELECT assignee FROM pm_task_assignees "
                "WHERE task_id = CAST(:tid AS uuid) ORDER BY assignee"
            ),
            {"tid": task_id},
        )).fetchall()
        result["assignees"] = [r.assignee for r in assignees]
        return result


# ── Writes ──────────────────────────────────────────────────────────────────

@router.post("/tasks", status_code=201)
async def create_task(
    payload: TaskIn, user: UserContext = Depends(get_current_user),
) -> dict:
    values = clean_payload(payload)
    title = str(values.get("title") or "").strip()
    if not title:
        raise HTTPException(status_code=422, detail="A task needs a title.")
    project_id = values.get("project_id")
    if not project_id:
        raise HTTPException(status_code=422, detail="A task needs a project_id.")
    validate_choice(values.get("source"), TASK_SOURCES, "source")
    values["title"] = title
    values["created_by"] = actor(user)

    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        target = await load_visible_project(db, vis, str(project_id))
        # Migration 193 — a folder is a grouping node. Tasks live on
        # projects; a task filed on a folder would sit outside every board.
        if node_kind(getattr(target, "kind", None)) == "folder":
            raise HTTPException(
                status_code=422,
                detail="A folder holds projects, not tasks. Create the "
                       "task in a project inside it.",
            )
        root = await root_project_id(db, str(project_id))
        values["root_project_id"] = root
        # The ROOT scopes the counter, the types and the tenant. A STATUS scopes
        # to the nearest node that owns a set, which is the root until a
        # subproject overrides it (migration 196). Two names for two questions,
        # because the bug where they diverge is silent — everything works until
        # somebody uses the feature.
        status_home = await status_owner_id(db, str(project_id))

        parent_id = values.get("parent_task_id")
        if parent_id:
            await load_visible_task(db, vis, str(parent_id))
        await assert_epic_has_no_parent(db, values.get("type_id"), parent_id)

        status = (
            await require_status_in_project(
                db, status_home, str(values["status_id"]),
            )
            if values.get("status_id")
            else await load_default_status(db, status_home)
        )
        values["status_id"] = str(status.id)
        values["task_number"] = await next_task_number(db, root)
        # WS-27m — through the registry, never straight into the array. Create
        # and patch both go this way, so there is no route by which a tag enters
        # the system unregistered; that is the only thing that keeps the
        # registry's "one spelling per tag" true rather than aspirational.
        if "tags" in values:
            values["tags"] = await apply_task_tags(
                db, root, values["tags"], by=actor(user),
            )

        row = await insert_row(db, "pm_tasks", values)
        task_id = str(row.id)
        await record_activity(
            db, activity_type="system", created_by=actor(user),
            task_id=task_id, body="Task created",
        )
        # WS-27v — the creator watches their own task, which is what keeps the
        # WS-27j author-hears-about-comments behaviour once the audience is
        # watchers ∪ assignees (migration 165 seeds the same for older tasks).
        await ensure_watchers(db, task_id, [actor(user)], by=actor(user))
        result = row_to_dict(row, TaskModel)

    await emit("pm.task.created", {
        "task_id": task_id, "project_id": str(project_id), "title": title,
    })
    return result


@router.patch("/tasks/{task_id}")
async def patch_task(
    task_id: str, payload: TaskIn,
    user: UserContext = Depends(get_current_user),
    if_match: str | None = Header(None, alias="If-Match"),
) -> dict:
    """Update a task. A body that moves ``status_id`` is a status TRANSITION.

    Which is why the status is split out and delegated to
    ``core.apply_status_transition`` rather than written with the other columns:
    the transition owes ``completed_at`` and a timeline entry, and a PATCH that
    writes only the column would look correct in the UI while emptying the
    timeline (the CRM's finding, restated here because the shape is identical).
    """
    values = clean_payload(payload)
    validate_choice(values.get("source"), TASK_SOURCES, "source")
    for guarded, endpoint in (
        ("project_id", "move"), ("parent_task_id", "move"),
    ):
        if guarded in values:
            raise HTTPException(
                status_code=422,
                detail=f"Use POST /projects/tasks/{{id}}/{endpoint} to change "
                       f"'{guarded}'.",
            )
    new_status = values.pop("status_id", None)
    custom = values.pop("custom_fields", None)

    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        before = await load_visible_task(db, vis, task_id)

        # WS-27bi / D-PM-20. Checked HERE — after the visibility load, before any
        # write — so a caller who cannot see the task still gets 404 and never
        # learns from a 412 that it exists (R5: 404, never 403).
        require_precondition(before, if_match, row_to_dict(before, TaskModel))

        # WS-27l. Merged against the definitions on the task's OWN root, not the
        # caller's current project: a task opened from My work can belong to a
        # project the board is not showing, and validating against the wrong
        # project's fields would refuse a perfectly good value.
        if "tags" in values:
            values["tags"] = await apply_task_tags(
                db, str(before.root_project_id), values["tags"], by=actor(user),
            )

        custom_changes: dict[str, Any] = {}
        if custom is not None:
            definitions = await load_definitions(db, str(before.root_project_id))
            merged, custom_changes = apply_values(
                from_jsonb(before.custom_fields), custom, definitions,
            )
            if custom_changes:
                values["custom_fields"] = merged

        after = before
        if values:
            await assert_epic_has_no_parent(
                db, values.get("type_id"), getattr(before, "parent_task_id", None),
            )
            after = await update_row(db, "pm_tasks", task_id, values)
            changes = diff_changes(before, after, _TRACKED_TASK_FIELDS)
            # Folded into the SAME `field_change` entry, under namespaced keys,
            # rather than given an activity type of its own. `record_activity`
            # refuses a type the migration's CHECK does not list — the trap that
            # made every attachment upload answer 422 — and a custom field
            # changing IS a field change, so a new vocabulary word would buy a
            # migration and a second timeline shape for nothing.
            changes.extend(
                {"field": f"custom.{key}", "old": moved["from"], "new": moved["to"]}
                for key, moved in sorted(custom_changes.items())
            )
            if changes:
                # Through the ONE field_change door (WS-27w): FK ids gain their
                # labels at write time, and a same-actor consecutive
                # description edit coalesces into the prior row.
                await record_field_change(
                    db, created_by=actor(user), task_id=task_id, changes=changes,
                )
        moved = None
        if new_status is not None and str(new_status) != str(before.status_id):
            moved = await apply_status_transition(
                db, after, str(new_status), created_by=actor(user),
            )
            after = moved["row"]

        if values or moved is not None:
            # WS-27v — editing a task subscribes the editor (idempotent). Only
            # when something actually changed: a no-op PATCH is not a touch.
            await ensure_watchers(db, task_id, [actor(user)], by=actor(user))
        if "description" in values:
            # WS-27v — mention DIFFING on the description, same rule as a
            # comment edit: only the addresses this edit ADDED are notified, so
            # rewording a description that already names two colleagues pings
            # neither, and the newly delivered mentions become watchers.
            # Watchers at large deliberately hear nothing about a description
            # edit — the diffed mentions are the whole fan-out.
            added = new_mentions(
                getattr(before, "description", None), values["description"],
            )
            mentioned = await notify(
                db, recipients=added, kind="mention", task_id=task_id,
                actor_id=actor(user), excerpt=excerpt_of(values["description"]),
            )
            await ensure_watchers(
                db, task_id, mentioned["notified"], by=actor(user),
            )

        result = row_to_dict(after, TaskModel)

    await emit("pm.task.updated", {"task_id": task_id})
    if moved is not None:
        await emit("pm.task.status_changed", {
            "task_id": task_id,
            "from": moved["from"].name, "to": moved["to"].name,
            "to_category": moved["to"].category,
        })
    return result


@router.post("/tasks/{task_id}/move")
async def move_task(
    task_id: str, payload: MoveTask,
    user: UserContext = Depends(get_current_user),
) -> dict:
    """Re-parent a task, move it to another project, or both.

    Crossing into another project re-stamps ``root_project_id`` and re-points
    the status, because statuses are per-root: carrying the old status across
    would leave the task in a lane the destination board does not render.
    """
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        task = await load_visible_task(db, vis, task_id)
        values: dict[str, Any] = {}

        if payload.parent_task_id is not None or "parent_task_id" in payload.model_fields_set:
            new_parent = payload.parent_task_id
            if new_parent:
                await load_visible_task(db, vis, str(new_parent))
            await assert_no_task_cycle(db, task_id, new_parent)
            await assert_epic_has_no_parent(
                db, getattr(task, "type_id", None), new_parent,
            )
            values["parent_task_id"] = new_parent

        if payload.project_id and str(payload.project_id) != str(task.project_id):
            dest = await load_visible_project(db, vis, str(payload.project_id))
            # Migration 193 — after the visibility load, same as the privacy
            # guard below: a caller who cannot see the folder still gets 404.
            if node_kind(getattr(dest, "kind", None)) == "folder":
                raise HTTPException(
                    status_code=422,
                    detail="A folder holds projects, not tasks. Move the "
                           "task into a project inside it.",
                )
            # Before anything is computed: a task may only move INTO a personal
            # project it already lives in. Checked after the visibility load so
            # a caller who cannot see the destination still gets 404 and never
            # learns from a 422 that it exists (R5: 404, never 403).
            await assert_move_keeps_privacy(db, task, str(payload.project_id))
            new_root = await root_project_id(db, str(payload.project_id))
            # ⚠️ Tracked SEPARATELY from the root, because since migration 196
            # the set can change while the root does not: moving a task from a
            # subproject that overrides into a sibling that inherits stays
            # inside one space and still crosses two status sets.
            old_status_home = await status_owner_id(db, str(task.project_id))
            new_status_home = await status_owner_id(db, str(payload.project_id))
            values["project_id"] = str(payload.project_id)
            if new_status_home != old_status_home:
                values["status_id"] = await remap_one_status(
                    db, status_id=str(task.status_id), owner_id=new_status_home,
                )
            if new_root != str(task.root_project_id):
                # The destination's REQUIRED fields (migration 192), merged from
                # what the task already carries plus what this call supplies.
                # Checked BEFORE any value is written, so a refusal leaves the
                # task exactly where it was rather than half-moved.
                #
                # Only when the ROOT changes: definitions are per-root, so a
                # move between two projects of the same tree cannot introduce a
                # requirement the task has not already satisfied, and asking
                # again would be a prompt with nothing behind it.
                merged, custom_changes = apply_values(
                    from_jsonb(task.custom_fields),
                    payload.custom_fields or {},
                    await load_definitions(db, new_root),
                )
                await assert_required_fields_present(db, new_root, merged)
                if custom_changes:
                    values["custom_fields"] = merged

                values["root_project_id"] = new_root
                # The number belongs to the old root's sequence and would
                # collide in the new one, so it is reallocated rather than
                # carried. The old number is recorded on the timeline below —
                # a task's human id changing without a trace is how a reference
                # in a comment stops resolving.
                values["task_number"] = await next_task_number(db, new_root)

        if payload.assignees is not None:
            # Promote-and-assign, in ONE transaction. `assert_assignable_here`
            # is re-run against the DESTINATION rather than the task's current
            # project, which is the whole point of allowing it here: assigning a
            # colleague is refused in a personal project, and the fix offered is
            # this very call — so the check has to see where the task is GOING.
            await assert_assignable_here(
                db,
                str(values.get("project_id", task.project_id)),
                {a.strip().lower() for a in payload.assignees if (a or "").strip()},
            )

        if not values and payload.assignees is None:
            return row_to_dict(task, TaskModel)

        row = await update_row(db, "pm_tasks", task_id, values) if values else task
        # WS-27ae / P-27 — SUBTASK MEMBERSHIP is a satellite of the PARENT, and
        # it is the one satellite that lives on the child's own row. Re-parenting
        # changes what both parents contain (`attach_relation_counts` draws
        # `{done, total}` from exactly this), and neither parent row is touched
        # by the UPDATE above.
        if "parent_task_id" in values:
            await touch_task(
                db, getattr(task, "parent_task_id", None),
                values.get("parent_task_id"),
            )
        if values:
            await record_activity(
                db, activity_type="system", created_by=actor(user), task_id=task_id,
                body="Task moved",
                meta={"from_project": str(task.project_id),
                      "from_number": getattr(task, "task_number", None)},
            )

        # ── Promote-and-assign, in the SAME transaction ─────────────────────
        #
        # The whole reason this lives here rather than in a follow-up call to
        # `set_assignees`: between two calls, the move can commit and the
        # assignment fail, leaving a task promoted onto a team board and owned
        # by nobody. That is a worse state than either failure alone, and it is
        # the state a client cannot repair without knowing what it was trying to
        # do. One transaction or neither.
        notified: dict[str, list[str]] = {"notified": [], "skipped": []}
        if payload.assignees is not None:
            wanted = {
                a.strip().lower() for a in payload.assignees if (a or "").strip()
            }
            current = {
                r.assignee for r in (await db.execute(
                    text(
                        "SELECT assignee FROM pm_task_assignees "
                        "WHERE task_id = CAST(:tid AS uuid)"
                    ),
                    {"tid": task_id},
                )).fetchall()
            }
            added = wanted - current
            for who in sorted(current - wanted):
                await db.execute(
                    text(
                        "DELETE FROM pm_task_assignees "
                        "WHERE task_id = CAST(:tid AS uuid) AND assignee = :who"
                    ),
                    {"tid": task_id, "who": who},
                )
            for who in sorted(added):
                await db.execute(
                    text(
                        "INSERT INTO pm_task_assignees "
                        "(task_id, assignee, assigned_by) "
                        "VALUES (CAST(:tid AS uuid), :who, :by) "
                        "ON CONFLICT (task_id, assignee) DO NOTHING"
                    ),
                    {"tid": task_id, "who": who, "by": actor(user)},
                )
            if added or (current - wanted):
                await record_activity(
                    db, activity_type="assignment", created_by=actor(user),
                    task_id=task_id,
                    meta={"added": sorted(added),
                          "removed": sorted(current - wanted)},
                )
            if added:
                # Inside the transaction, for `notify`'s own stated reason: an
                # assignment that committed while its notification did not is
                # the silent assignment WS-27j exists to end.
                notified = await notify(
                    db, recipients=sorted(added), kind="assigned",
                    task_id=task_id, actor_id=actor(user),
                )

        result = row_to_dict(row, TaskModel)
        result["notified"] = notified["notified"]
        result["skipped"] = notified["skipped"]

    await emit("pm.task.moved", {"task_id": task_id})
    return result


@router.delete("/tasks/{task_id}")
async def delete_task(
    task_id: str, user: UserContext = Depends(get_current_user),
) -> DeleteResponse:
    """Delete a task, and say what went with it.

    Subtasks are **promoted, not destroyed** — ``parent_task_id`` SET NULLs
    (§3.5) — so the reported ``subtasks_promoted`` count is not a cascade but
    its opposite, and is named accordingly. Reporting it as "deleted" would be
    the exact class of lie the N8 purge shipped: a count that reassures in the
    wrong direction.
    """
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        doomed = await load_visible_task(db, vis, task_id)
        promoted = await count_where(db, "pm_tasks", "parent_task_id", task_id)
        # WS-27ae / P-27 — read BEFORE the delete, because afterwards the FK has
        # already SET NULL and nothing connects these rows to the task that used
        # to own them. Their `parent_task_id` changed without any statement in
        # this module writing them, so without the bump a promoted subtask is an
        # edit no delta client can ever see.
        children = [
            str(r.id) for r in (await db.execute(
                text(
                    "SELECT id FROM pm_tasks "
                    "WHERE parent_task_id = CAST(:tid AS uuid)"
                ),
                {"tid": task_id},
            )).fetchall()
        ]
        activities = await count_where(db, "pm_activities", "task_id", task_id)
        assignees = (await db.execute(
            text(
                "SELECT count(*) FROM pm_task_assignees "
                "WHERE task_id = CAST(:tid AS uuid)"
            ),
            {"tid": task_id},
        )).scalar() or 0
        links = (await db.execute(
            text(
                "SELECT count(*) FROM pm_task_links "
                "WHERE source_task_id = CAST(:tid AS uuid) "
                "   OR target_task_id = CAST(:tid AS uuid)"
            ),
            {"tid": task_id},
        )).scalar() or 0

        await db.execute(
            text("DELETE FROM pm_tasks WHERE id = CAST(:tid AS uuid)"),
            {"tid": task_id},
        )
        # The tombstone itself is written by migration 168's AFTER DELETE
        # trigger, NOT here — `pm_projects` CASCADEs to `pm_tasks`, so a
        # statement in this function would have recorded the one deletion path
        # that has an endpoint and silently missed the one that takes hundreds
        # of tasks at once. What this function still owes is the bump on the
        # rows the delete CHANGED but did not remove.
        await touch_task(db, getattr(doomed, "parent_task_id", None), *children)

    await emit("pm.task.deleted", {"task_id": task_id})
    return DeleteResponse(
        deleted=task_id,
        cascaded={
            "activities": int(activities),
            "assignees": int(assignees),
            "links": int(links),
            "subtasks_promoted": int(promoted),
        },
    )


# ── Archive (WS-27w item 1) ─────────────────────────────────────────────────

@router.post("/tasks/{task_id}/archive")
async def archive_task(
    task_id: str, user: UserContext = Depends(get_current_user),
) -> dict:
    """Archive one task — allowed only once it is CLOSED.

    An archived task exits every default list, board, calendar and search
    surface at once, so archiving an open task is a trap, not a feature (P-3):
    the work disappears while still owed, and nobody gardening a board can see
    where it went. The guard is written on the status CATEGORY, and as "not in
    (done, cancelled)" rather than as a list of open categories — a category
    added later (WS-27u's `triage`) is refused by default instead of becoming
    silently archivable. The refusal names the actual category, because "cannot
    archive" without the why sends people hunting through lanes.

    WS-27z's sweeper depends on this guard shipping first.
    """
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        task = await load_visible_task(db, vis, task_id)
        status = await require_row(
            db, "pm_task_statuses", str(task.status_id), "Status",
        )
        category = str(getattr(status, "category", "") or "")
        if category not in CLOSING_CATEGORIES:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Cannot archive an open task: its status category is "
                    f"'{category}'. Move it to a done or cancelled status "
                    f"first."
                ),
            )
        if getattr(task, "archived_at", None) is not None:
            # Already archived — idempotent, the double-click answer.
            return row_to_dict(task, TaskModel)
        row = await update_row(db, "pm_tasks", task_id, {"archived_at": now()})
        await record_activity(
            db, activity_type="system", created_by=actor(user),
            task_id=task_id, body="Task archived",
        )
        result = row_to_dict(row, TaskModel)

    await emit("pm.task.archived", {"task_id": task_id})
    return result


@router.post("/tasks/{task_id}/unarchive")
async def unarchive_task(
    task_id: str, user: UserContext = Depends(get_current_user),
) -> dict:
    """Bring an archived task back onto its board.

    No category guard in this direction — restoring puts work back where
    people can see it, which is never the trap the archive guard exists to
    prevent. Without this endpoint an archive would be one-way: nothing else
    writes ``archived_at``, and the PATCH surface deliberately does not accept
    it.
    """
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        task = await load_visible_task(db, vis, task_id)
        if getattr(task, "archived_at", None) is None:
            return row_to_dict(task, TaskModel)
        row = await update_row(db, "pm_tasks", task_id, {"archived_at": None})
        await record_activity(
            db, activity_type="system", created_by=actor(user),
            task_id=task_id, body="Task restored from the archive",
        )
        result = row_to_dict(row, TaskModel)

    await emit("pm.task.unarchived", {"task_id": task_id})
    return result


# ── Assignees ───────────────────────────────────────────────────────────────

@router.put("/tasks/{task_id}/assignees")
async def set_assignees(
    task_id: str, payload: AssigneesIn,
    user: UserContext = Depends(get_current_user),
) -> dict:
    """Replace a task's assignee set.

    An assignee is an email or ``agent:<name>`` — one vocabulary for both
    species (D-PM-4), which is what makes handing work to an agent the *same*
    action as handing it to a colleague rather than a parallel feature.

    The emitted ``pm.task.assigned`` event is what WS-27f keys agent dispatch
    off, so it carries the added assignees rather than the whole set: a
    re-assert of an existing assignee must not re-dispatch a run. It also
    carries the task's ``organization_id`` (WS-27aa): the sink runs with no
    request behind it, so this is the one place its tenant can come from a
    stored fact instead of an ambient binding.
    """
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        task = await load_visible_task(db, vis, task_id)
        # WS-27aa / H4 — the tenant the dispatch sink will bind, read HERE:
        # inside the request's already-bound session, off the task's own
        # `organization_id` (NOT NULL since migration 161). It is a stored
        # fact about the row, never anything the caller sent (R11), and it is
        # read here because this is the last place a tenant legitimately
        # exists: `agent_dispatch.on_event` fires with no request behind it,
        # and a sink that looked the tenant up itself would have to do so on
        # an unbound session — the exact thing H4 forbids.
        task_org = str(getattr(task, "organization_id", "") or "")

        wanted = {
            a.strip().lower() for a in payload.assignees if (a or "").strip()
        }
        current = {
            r.assignee for r in (await db.execute(
                text(
                    "SELECT assignee FROM pm_task_assignees "
                    "WHERE task_id = CAST(:tid AS uuid)"
                ),
                {"tid": task_id},
            )).fetchall()
        }
        added, removed = wanted - current, current - wanted

        # Requirement 5 (owner, 2026-08-26). Checked before the first write, so
        # a refused assignment leaves the set exactly as it was rather than
        # half-applied. REMOVALS are deliberately not guarded: taking somebody
        # off a task can never be the thing that strands them.
        await assert_assignable_here(db, str(task.project_id), added)

        for who in sorted(removed):
            await db.execute(
                text(
                    "DELETE FROM pm_task_assignees "
                    "WHERE task_id = CAST(:tid AS uuid) AND assignee = :who"
                ),
                {"tid": task_id, "who": who},
            )
        for who in sorted(added):
            await db.execute(
                text(
                    "INSERT INTO pm_task_assignees "
                    "(task_id, assignee, assigned_by) "
                    "VALUES (CAST(:tid AS uuid), :who, :by) "
                    "ON CONFLICT (task_id, assignee) DO NOTHING"
                ),
                {"tid": task_id, "who": who, "by": actor(user)},
            )
        if added or removed:
            await record_activity(
                db, activity_type="assignment", created_by=actor(user),
                task_id=task_id,
                meta={"added": sorted(added), "removed": sorted(removed)},
            )
        # WS-27j — the notification is written INSIDE this transaction, not
        # emitted on the bus afterwards. `emit` is best-effort by construction
        # so a broken workflow can never fail a task edit; that is right for
        # agent dispatch, where a missed run is recoverable, and wrong here.
        # An assignment that committed while its notification did not is
        # exactly the silent assignment §11.2 filed.
        #
        # Only `added`: a re-assert of somebody already on the task must not
        # ping them again, the same reason the event carries added rather than
        # the whole set.
        notified = await notify(
            db, recipients=sorted(added), kind="assigned",
            task_id=task_id, actor_id=actor(user),
        )
        # WS-27v — a newly added assignee becomes a watcher. Idempotent, and
        # only `added` for the same reason the event carries added: a re-assert
        # is not a touch. Agents are dropped inside the helper (they are
        # dispatched, never subscribed), and the rows survive a later
        # unassignment — having held the work is a reason to keep hearing.
        await ensure_watchers(db, task_id, sorted(added), by=actor(user))

    if added:
        await emit("pm.task.assigned", {
            "task_id": task_id, "assignees": sorted(added),
            # The sink's ONLY tenant source. `agent_dispatch.on_event` refuses
            # a payload without it rather than running unbound.
            "organization_id": task_org,
        })
    return {
        "task_id": task_id,
        "assignees": sorted(wanted),
        # Named rather than swallowed: assigning somebody outside the project's
        # grant closure is a real thing to do by accident, and it leaves them
        # holding work they cannot open. Saying so is what lets the assigner
        # fix it.
        "not_notified": notified["skipped"],
    }


# ── Links ───────────────────────────────────────────────────────────────────

@router.post("/tasks/{task_id}/links", status_code=201)
async def create_link(
    task_id: str, payload: LinkIn,
    user: UserContext = Depends(get_current_user),
) -> dict:
    if payload.link_type not in _LINK_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown link type '{payload.link_type}'. "
                   f"One of: {list(_LINK_TYPES)}.",
        )
    if str(payload.target_task_id) == str(task_id):
        raise HTTPException(
            status_code=422, detail="A task cannot be linked to itself.",
        )
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        await load_visible_task(db, vis, task_id)
        # Both ends must be visible: a link is readable from either side, so
        # accepting an unreadable target would disclose that it exists.
        await load_visible_task(db, vis, str(payload.target_task_id))
        # WS-27p — the same guard `assert_no_task_cycle` has always put on
        # `parent_task_id`, finally on the edge that can actually deadlock:
        # A blocks B blocks C blocks A is a loop no human can resolve by
        # finishing something, and every walk over it runs forever.
        if payload.link_type in DIRECTED_TYPES:
            await assert_no_block_cycle(db, task_id, str(payload.target_task_id))
        row = (await db.execute(
            text(
                "INSERT INTO pm_task_links "
                "(source_task_id, target_task_id, link_type, created_by) "
                "VALUES (CAST(:src AS uuid), CAST(:tgt AS uuid), :kind, :who) "
                "ON CONFLICT (source_task_id, target_task_id, link_type) "
                "DO UPDATE SET link_type = EXCLUDED.link_type RETURNING id"
            ),
            {
                "src": task_id, "tgt": str(payload.target_task_id),
                "kind": payload.link_type, "who": actor(user),
            },
        )).fetchone()
        await record_activity(
            db, activity_type="link", created_by=actor(user), task_id=task_id,
            meta={"target": str(payload.target_task_id), "type": payload.link_type},
        )
        # WS-27ae / P-27 — the activity above bumps the SOURCE. A link is
        # readable from both ends (`attach_relation_counts` draws "blocked" on
        # the target), so the target changed too and a delta client watching
        # only the blocked task would otherwise never learn it is blocked.
        await touch_task(db, str(payload.target_task_id))
        return {
            "id": str(row.id), "source_task_id": task_id,
            "target_task_id": str(payload.target_task_id),
            "link_type": payload.link_type,
        }


@router.delete("/tasks/{task_id}/links/{link_id}")
async def delete_link(
    task_id: str, link_id: str,
    user: UserContext = Depends(get_current_user),
) -> dict:
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        await load_visible_task(db, vis, task_id)
        row = (await db.execute(
            text(
                "DELETE FROM pm_task_links WHERE id = CAST(:lid AS uuid) "
                "AND (source_task_id = CAST(:tid AS uuid) "
                "     OR target_task_id = CAST(:tid AS uuid)) "
                "RETURNING id, source_task_id, target_task_id"
            ),
            {"lid": link_id, "tid": task_id},
        )).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Link not found")
        # WS-27ae / P-27 — unlinking records NO activity (deliberately: WS-27p
        # kept the timeline for the link, not the unlink), so this is the one
        # place the bump cannot ride the spine. BOTH ends, read off the deleted
        # row rather than assumed from the path, because the caller may be
        # either end of it.
        await touch_task(
            db, getattr(row, "source_task_id", None),
            getattr(row, "target_task_id", None),
        )
        return {"deleted": link_id}
