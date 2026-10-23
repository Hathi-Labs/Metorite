"""Projects · tasks — task CRUD, the list contract, assignees and links.

Spec: ``project-docs/specs/project_management_app.md`` §4 (``tasks.py`` row).

    GET    /projects/tasks                       → {rows, total}
    POST   /projects/tasks
    GET    /projects/tasks/{id}
    PATCH  /projects/tasks/{id}
    DELETE /projects/tasks/{id}                  → what cascaded
    POST   /projects/tasks/{id}/move
    PUT    /projects/tasks/{id}/assignees        → replaces the set
    POST   /projects/tasks/{id}/claim            → add the caller, and start it
    POST   /projects/tasks/{id}/agent-delete     → an MCP agent deletes (EA-6h)
    POST   /projects/tasks/{id}/links
    DELETE /projects/tasks/{id}/links/{link_id}

Subtasks are tasks with a ``parent_task_id`` — there is no second endpoint and
no second table (§3.5). ``?parent_task_id=`` lists one task's children;
``?include_subtree=`` widens a project filter to its descendants.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Annotated, Any, Literal, NoReturn

from acb_auth import UserContext, get_current_user
from acb_auth.agent_oauth import ACTOR_VIA_PATTERN, PROJECTS_WRITE
from acb_common import get_logger
from fastapi import Depends, Header, HTTPException, Query
from gateway.routes.projects.authority import (
    assert_may_delete_task,
    assert_may_move_task,
    attach_can_delete,
)
from gateway.routes.projects.cascade import (
    archive_subtree,
    complete_subtree,
    emit_all,
    load_subtree,
    movable_subtree,
    subtree_privacy_refusals,
)
from gateway.routes.projects.core import (
    CLIENT_TASK_SOURCES,
    CLOSING_CATEGORIES,
    COMPLETED_CATEGORY,
    DIRECTIONS,
    STARTED_CATEGORY,
    TASK_SORTS,
    TRIAGE_CATEGORY,
    ListResponse,
    Page,
    TaskIn,
    TaskModel,
    _tenant_session,
    actor,
    apply_status_transition,
    archive_note,
    assert_assignable_here,
    assert_epic_has_no_parent,
    assert_move_keeps_privacy,
    assert_no_task_cycle,
    clean_payload,
    count_where,
    diff_changes,
    emit,
    from_jsonb,
    insert_assignees,
    insert_row,
    lift_subtasks_to_grandparent,
    load_default_status,
    load_visible_project,
    load_visible_task,
    lock_task,
    next_task_number,
    node_kind,
    now,
    parent_lane_status,
    parse_precondition,
    record_activity,
    record_field_change,
    remap_one_status,
    remap_one_type,
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
    vocabulary_scope,
)
from gateway.routes.projects.custom_fields import (
    apply_values,
    load_definitions,
)
from gateway.routes.projects.filters import (
    attach_agent_state,
    attach_assignees,
    attach_parent_context,
    attach_relation_counts,
    build_task_filters,
)
from gateway.routes.projects.landing import (
    completion_correction,
    land_custom_fields,
    lane_category,
    record_drops,
    resolve_field_map,
)
from gateway.routes.projects.notifications import (
    excerpt_of,
    new_mentions,
    notify,
)
from gateway.routes.projects.relations import (
    DIRECTED_TYPES,
    assert_no_block_cycle,
    insert_task_link,
)
from gateway.routes.projects.tags import apply_task_tags
from gateway.routes.projects.text_links import link_from_text, saved_texts
from gateway.routes.projects.watchers import ensure_watchers
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr
from sqlalchemy import text

_log = get_logger("gateway.projects.tasks")

#: Fields whose change earns a timeline entry. `status_id` is absent on purpose:
#: a status move is a TRANSITION and gets its own richer activity, so listing it
#: here too would write the same fact twice under two types.
_TRACKED_TASK_FIELDS: tuple[str, ...] = (
    "title", "description", "importance", "leveraged", "due_at", "start_date",
    "estimate_mins", "type_id", "parent_task_id", "project_id",
)

_LINK_TYPES: tuple[str, ...] = ("blocks", "relates_to", "duplicates")

#: WS-55 EA-6d (spec `external_agent_access.md` §9.2). The body keys that a
#: signed agent write may send, by route. The MCP edge sends no other key, and
#: the route refuses any other key with 422 when `UserContext.agent` is set.
#: So a holder of the signing key cannot skip the edge's rules with a body
#: that the edge would never build. A human request is not narrowed.
#: The create set is the input of `create_task`.
AGENT_CREATE_FIELDS: frozenset[str] = frozenset({
    "project_id", "title", "description", "parent_task_id", "type_id",
    "status_id", "tags", "due_at",
})
#: The PATCH set is the union of `set_task_status` (`status_id`) and
#: `update_task` (the other five).
AGENT_PATCH_FIELDS: frozenset[str] = frozenset({
    "status_id", "title", "description", "tags", "due_at", "custom_fields",
})
#: The `pm_tasks.source` that the server stamps on a task an agent makes.
AGENT_TASK_SOURCE = "mcp"


def _narrow_agent_body(user: UserContext, values: dict[str, Any],
                       allowed: frozenset[str], *,
                       include_subtasks: bool = False) -> None:
    """Refuse a key outside *allowed* in the body of a signed agent write,
    and the PATCH cascade ``include_subtasks``.

    A no-op for a human request (``user.agent`` is ``None``).
    """
    if getattr(user, "agent", None) is None:
        return
    extra = sorted(set(values) - allowed)
    if include_subtasks:
        extra.append("include_subtasks")
    if extra:
        raise HTTPException(
            status_code=422,
            detail=f"An agent write may not send {extra}.",
        )


def _refuse_agent_parent(user: UserContext, parent: Any, project_id: str) -> None:
    """An agent's subtask lives in its parent's project (§9.2, EA-6d).

    The route itself does not check it for a member. For an agent the edge
    refuses first, and this is the gateway's own copy of the rule.
    """
    if getattr(user, "agent", None) is None or parent is None:
        return
    if str(getattr(parent, "project_id", "")).lower() != project_id.lower():
        raise HTTPException(
            status_code=422,
            detail="The parent task is in another project.",
        )


async def _refuse_agent_type(db: Any, user: UserContext, type_id: Any,
                             root: str) -> None:
    """An agent's ``type_id`` is a type of the project's vocabulary (§9.2).

    The vocabulary is the one ``GET /nodes/{id}/types`` reads: the root's own
    types and the org-wide ones (``core.vocabulary_scope``). The route itself
    does not check it for a member, and a made-up id there hits the foreign
    key. For an agent the edge refuses first, and this is the gateway's own
    copy of the rule.
    """
    if getattr(user, "agent", None) is None or not type_id:
        return
    found = (await db.execute(
        text(
            "SELECT 1 FROM pm_task_types "
            f"WHERE id = CAST(:type_id AS uuid) AND {vocabulary_scope()}"
        ),
        {"type_id": str(type_id), "root": root},
    )).first()
    if found is None:
        raise HTTPException(
            status_code=422,
            detail="That task type does not belong to this project.",
        )


#: WS-55 EA-6f (EA3-32, spec `external_agent_access.md` §9.2). An agent may
#: move a task into ``done``. Cancel stays a human step, so a signed agent
#: MOVE refuses only the abandoned half of the closing categories. The shape
#: is `tree.ABANDONED_CATEGORIES`, and no new literal names a category.
AGENT_MOVE_REFUSED: frozenset[str] = CLOSING_CATEGORIES - {COMPLETED_CATEGORY}
#: A signed agent CREATE still refuses both closing categories: a new task
#: that starts closed is not work that an agent did.
AGENT_CREATE_REFUSED: frozenset[str] = CLOSING_CATEGORIES

#: The two texts of the refusal, one for each door.
AGENT_MOVE_REFUSAL = "Cancelling a task stays a human step."
AGENT_CREATE_REFUSAL = "A new task cannot start in a done or cancelled status."


def _refuse_agent_close(user: UserContext, category: str | None, *,
                        refused: frozenset[str], detail: str) -> None:
    """An agent never moves a task into a category of *refused* (§9.2).

    The create passes :data:`AGENT_CREATE_REFUSED` and the PATCH passes
    :data:`AGENT_MOVE_REFUSED` (EA-6f). The edge refuses first. This is the
    gateway's own copy of the rule, for a request that did not come through
    the edge's tools.
    """
    if getattr(user, "agent", None) is not None and category in refused:
        raise HTTPException(status_code=422, detail=detail)


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
    #: D-PM-38 decision 4 (S5). Carry every descendant to the new project too,
    #: each remapped into the destination's set by this same seam. FALSE by
    #: default, so an old caller and the organize door move one task. The
    #: move dialogs send true, because their box is ticked, and the Projects
    #: chat's `move_task` sends it for a member's "yes" (WS-46 P6). It
    #: means something only when ``project_id`` changes.
    include_subtasks: bool = False


class AssigneesIn(BaseModel):
    assignees: list[str]


class LinkIn(BaseModel):
    target_task_id: str
    link_type: str = "relates_to"


class DeleteResponse(BaseModel):
    deleted: str
    cascaded: dict[str, int]
    #: The ids of the subtasks that :func:`delete_task_in` deleted, for the
    #: caller's ``pm.task.deleted`` events (D-OP-10). Not in the response.
    _subtask_ids: list[str] = PrivateAttr(default_factory=list)

    @property
    def subtask_ids(self) -> list[str]:
        """The deleted subtasks, sorted. The task itself is not in the list."""
        return list(self._subtask_ids)


# ── List ────────────────────────────────────────────────────────────────────

#: The read half of H-64, named so a test can run the real fragment.
#:
#: ⚠️ **`pm_view_task_positions` was written for weeks and read by nothing.**
#: `GET /projects/tasks` selected `t.*` and joined no positions, so every row
#: arrived with `view_position` undefined, `board.sortForView` sent them all
#: down its `created_at` branch, and a drag inside a column was a silent
#: no-op — the card animated back to where it started (H-64, filed
#: 2026-08-26).
#:
#: LEFT, never inner: a task nobody has dragged has no row here, and an inner
#: join would drop every such card — on a fresh board, all of them.
VIEW_POSITION_JOIN = (
    " LEFT JOIN pm_view_task_positions vp"
    "   ON vp.task_id = t.id AND vp.view_id = CAST(:view_id AS uuid)"
)


async def _with_agent_state(db: Any, row: dict) -> dict:
    """*row* with its agent pill (WS-55 EA-12c, EA12-10). Every route that
    hands a task row back to the client fills it, because the client merges
    the row over its own copy."""
    await attach_agent_state(db, [row])
    return row


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
    #: The archive AS A PLACE. `include_archived` shows everything; this shows
    #: only what somebody filed, which is the view you open to get one back.
    archived_only: bool = False,
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
    # H-64. The saved view whose hand-arranged order to read back.
    #
    # ⚠️ **The board has WRITTEN `pm_view_task_positions` since WS-27 and
    # nothing ever read it.** This endpoint selected `t.*` and joined nothing,
    # so `view_position` arrived `undefined` on every row, `sortForView` sent
    # every task down its `created_at` branch, and a drag inside a column was
    # a silent no-op: the card animated back to where it started. `planDrop`
    # then saw no positioned neighbour and materialised the WHOLE group on
    # every drop — up to `MAX_POSITIONS` rows written, none of them read.
    view_id: str | None = None,
    # D-PM-38. A view whose `subtasks` mode is `hidden` sends this, so the
    # rows AND the total leave subtasks out. Applied in SQL for the reason
    # every filter is: paging happens there.
    top_level: bool = False,
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
            archived_only=archived_only,
            watching=watching, viewer=actor(user) if watching else None,
            top_level=top_level,
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
        # ⚠️ **LEFT JOIN, and the ORDER BY is untouched.** A task with no row
        # in this view is unordered, not missing — an inner join would drop
        # every card nobody has dragged yet, which on a fresh board is all of
        # them. And the server keeps its own total order (`SORT_TIEBREAK`)
        # because paging happens in SQL: ordering by a nullable position here
        # would make page boundaries depend on who dragged what. The client
        # sorts WITHIN a column, which is the only place the order means
        # anything — `board.sortForView` holds that rule and is tested.
        join = VIEW_POSITION_JOIN if view_id else ""
        if view_id:
            params["view_id"] = view_id
        rows = (await db.execute(
            text(
                f"SELECT t.*{', vp.position AS view_position' if view_id else ''}"
                f"  FROM pm_tasks t{join}{where} "
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
        # WS-55 EA-12a — the agent pill, in one query for the page.
        await attach_agent_state(db, page_rows)
        await attach_relation_counts(db, page_rows, vis)
        # D-PM-38 — "↳ Parent" on every flat row, under the reader's grants.
        await attach_parent_context(db, vis, page_rows)
        # WS-64 OP-8 — the app hides Delete where this is false.
        await attach_can_delete(db, user, page_rows, kind="task")
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
        result["time_spent_mins"] = await time_spent_mins(db, task_id)
        # WS-55 EA-12a — the agent pill on the task detail (EA12-10).
        await attach_agent_state(db, [result])
        # WS-64 OP-8 — the chat refuses before its card where this is false.
        await attach_can_delete(db, user, [result], kind="task")
        return result


#: D77 — the work actually done on a task, summed over EVERY member's timed
#: block (`pm_task_personal.actual_start` → `actual_end`). One member's
#: actuals are theirs (the overlay), but the SUM is a fact about the work,
#: which is why Projects shows it beside the shared estimate. Bound to the
#: task the caller already loaded through `load_visible_task`: the overlay
#: rows hang off that task by foreign key, and the tenant clause is on the
#: read as well as on RLS (R5), the way `MY_TASKS_FROM` carries one.
_TIME_SPENT_SQL = (
    "SELECT COALESCE(round(sum(EXTRACT(EPOCH FROM (p.actual_end - p.actual_start)))"
    " / 60.0), 0) AS mins"
    "  FROM pm_task_personal p"
    "  JOIN pm_tasks t ON t.id = p.task_id"
    "   AND t.organization_id = p.organization_id"
    " WHERE p.task_id = CAST(:tid AS uuid)"
    "   AND p.actual_start IS NOT NULL AND p.actual_end IS NOT NULL"
    "   AND p.actual_end > p.actual_start"
)


async def time_spent_mins(db: Any, task_id: str) -> int:
    """Minutes every member has timed on one task. ``0`` when nobody has."""
    row = (await db.execute(text(_TIME_SPENT_SQL), {"tid": task_id})).first()
    return int(getattr(row, "mins", 0) or 0) if row is not None else 0


# ── Writes ──────────────────────────────────────────────────────────────────

@router.post("/tasks", status_code=201)
async def create_task(
    payload: TaskIn, user: UserContext = Depends(get_current_user),
) -> dict:
    values = clean_payload(payload)
    # WS-55 EA-6d. A signed agent write sends only the keys of `create_task`.
    _narrow_agent_body(user, values, AGENT_CREATE_FIELDS)
    title = str(values.get("title") or "").strip()
    if not title:
        raise HTTPException(status_code=422, detail="A task needs a title.")
    project_id = values.get("project_id")
    if not project_id:
        raise HTTPException(status_code=422, detail="A task needs a project_id.")
    validate_choice(values.get("source"), CLIENT_TASK_SOURCES, "source")
    if getattr(user, "agent", None) is not None:
        # The task source of an agent comes from branch 0's signed context,
        # never from the body (the narrowing above refuses a body `source`).
        values["source"] = AGENT_TASK_SOURCE
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
        # The values pass the gate a PATCH puts them through
        # (`custom_fields.apply_values`), against the definitions of the root
        # the task is created in, in this transaction and before any write.
        # Before this the create stored the body as it came: a wrong type, an
        # option nobody defined, or a key from another project landed in the
        # row and read back as a saved value. A stated null means "no value",
        # so it leaves the column default rather than writing NULL into a
        # NOT NULL column.
        if "custom_fields" in values:
            custom = values.pop("custom_fields")
            if custom is not None:
                shaped, _ = apply_values(
                    {}, custom, await load_definitions(db, root),
                )
                if shaped:
                    values["custom_fields"] = shaped
        # The ROOT scopes the counter, the types and the tenant. A STATUS scopes
        # to the nearest node that owns a set, which is the root until a
        # subproject overrides it (migration 196). Two names for two questions,
        # because the bug where they diverge is silent — everything works until
        # somebody uses the feature.
        status_home = await status_owner_id(db, str(project_id))

        parent_id = values.get("parent_task_id")
        parent = (
            await load_visible_task(db, vis, str(parent_id)) if parent_id else None
        )
        _refuse_agent_parent(user, parent, str(project_id))
        await _refuse_agent_type(db, user, values.get("type_id"), str(root))
        await assert_epic_has_no_parent(db, values.get("type_id"), parent_id)

        # A stated status always wins. A new step with no stated status takes
        # its parent's lane through the ONE seam (D-PM-38, D79), so the
        # Projects panel, the board, both chat tools and My Tasks agree.
        if values.get("status_id"):
            status = await require_status_in_project(
                db, status_home, str(values["status_id"]),
            )
            _refuse_agent_close(user, getattr(status, "category", None),
                                refused=AGENT_CREATE_REFUSED,
                                detail=AGENT_CREATE_REFUSAL)
        elif parent is not None:
            status = await parent_lane_status(db, parent, status_home)
        else:
            status = await load_default_status(db, status_home)
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
    # WS-57 TR-5 — `#N` and GitHub or document URLs in the new text, after
    # the commit and in a transaction of its own (`text_links.py`).
    await link_from_text(user, [(task_id, [(title, False, None), (values.get("description"), True, None)])])
    return result


@router.patch("/tasks/{task_id}")
async def patch_task(
    task_id: str, payload: TaskIn,
    user: UserContext = Depends(get_current_user),
    if_match: str | None = Header(None, alias="If-Match"),
    include_subtasks: bool = False,
) -> dict:
    """Update a task. A body that moves ``status_id`` is a status TRANSITION.

    Which is why the status is split out and delegated to
    ``core.apply_status_transition`` rather than written with the other columns:
    the transition owes ``completed_at`` and a timeline entry, and a PATCH that
    writes only the column would look correct in the UI while emptying the
    timeline (the CRM's finding, restated here because the shape is identical).

    D-PM-38 decision 2 (S5). ``?include_subtasks=true`` on a move INTO a
    ``done`` lane also completes every open descendant, each in its own set
    (``cascade.complete_subtree``). The default is false, so an old caller
    closes this task only. A move into ``cancelled`` never cascades: nobody
    asked to cancel the children. The reply then carries
    ``subtasks_completed`` and ``subtask_changes``, the Undo record.
    """
    values = clean_payload(payload)
    # WS-55 EA-6d. A signed agent PATCH sends only the keys of
    # `set_task_status` and `update_task`, and never the cascade.
    _narrow_agent_body(user, values, AGENT_PATCH_FIELDS,
                       include_subtasks=include_subtasks)
    validate_choice(values.get("source"), CLIENT_TASK_SOURCES, "source")
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
        cascade = None
        if new_status is not None and str(new_status) != str(before.status_id):
            moved = await apply_status_transition(
                db, after, str(new_status), created_by=actor(user),
            )
            # WS-55 EA-6d. Inside the transaction, so the refusal rolls the
            # whole PATCH back, the field changes too. EA-6f: an agent may
            # move a task into done, and only a cancel is refused. The
            # narrowing above refuses `include_subtasks`, so an agent's done
            # never closes a subtask.
            _refuse_agent_close(user, getattr(moved["to"], "category", None),
                                refused=AGENT_MOVE_REFUSED,
                                detail=AGENT_MOVE_REFUSAL)
            after = moved["row"]
            # In the SAME transaction as the parent's own move, so a refused
            # cascade (a set with no Done status) leaves the parent open too.
            if include_subtasks and moved["to"].category == COMPLETED_CATEGORY:
                cascade = await complete_subtree(
                    db, vis, task_id, by=actor(user),
                )

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
        # WS-55 EA-12c — the pill survives an edit. The client merges this
        # row over its copy, so a null here would erase the pill (EA12-10).
        await attach_agent_state(db, [result])
        if include_subtasks:
            result["subtasks_completed"] = len(cascade.ids) if cascade else 0
            result["subtask_changes"] = cascade.changes if cascade else []

    await emit("pm.task.updated", {"task_id": task_id})
    if moved is not None:
        await emit("pm.task.status_changed", {
            "task_id": task_id,
            "from": moved["from"].name, "to": moved["to"].name,
            "to_category": moved["to"].category,
        })
    if cascade is not None:
        await emit_all(cascade.events)
    # WS-57 TR-5 — the numbers and URLs this PATCH ADDS to the title or the
    # description, against `before` (`text_links.py`, rule 4). A PATCH that
    # writes neither reads nothing.
    await link_from_text(user, [(task_id, saved_texts(values, before))])
    return result


async def _cross_root_values(
    db: Any, task: Any, new_root: str, answers: dict[str, Any] | None,
    field_map: dict[str, str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """What a task's row owes when its ROOT changes: ``(values, dropped)``.

    Only when the root changes: definitions, types and numbers are per-root,
    so a move between two projects of one tree cannot introduce a
    requirement the task has not already satisfied, and asking again would
    be a prompt with nothing behind it.

    The custom fields go through the ONE landing seam the bulk route uses
    (`landing.land_custom_fields`): the field map is resolved here by key
    and then by name, so the client need not send one, and the caller's
    answers are merged AFTER the map, under destination keys, so a required
    field renamed between roots is answerable. Before this the answers were
    merged onto the RAW values, and `po` → `customer_po` (same name) was
    refused on this path and accepted on the bulk one. Checked BEFORE any
    value is written, so a refusal leaves the task exactly where it was.
    """
    dest_defs = await load_definitions(db, new_root)
    if field_map is None:
        source_defs = await load_definitions(db, str(task.root_project_id))
        field_map, _orphans = resolve_field_map(source_defs, dest_defs)
    landed, dropped = await land_custom_fields(
        db, task, dest_root=new_root, field_map=field_map,
        dest_defs=dest_defs, answers=answers,
    )
    values: dict[str, Any] = {
        "custom_fields": landed,
        "root_project_id": new_root,
    }
    # WS-27bl. Types are root-scoped too, and this path carried `type_id`
    # across untouched until 2026-09-19 — the task then pointed at a row in
    # the OLD root's registry and its type silently stopped resolving. Same
    # helper as `/tasks/move`, so the narrow path and the mapping-aware one
    # cannot disagree about what a move does to a type.
    if getattr(task, "type_id", None):
        values["type_id"] = await remap_one_type(
            db, type_id=str(task.type_id), root_id=new_root,
        )
    # The number belongs to the old root's sequence and would collide in the
    # new one, so it is reallocated rather than carried. The old number is
    # recorded on the timeline by the caller — a task's human id changing
    # with no trace is how a reference in a comment stops resolving.
    values["task_number"] = await next_task_number(db, new_root)
    return values, dropped


async def move_task_in(
    db: Any, vis: Any, task: Any, payload: MoveTask, *, by: str, user: Any,
    field_map: dict[str, str] | None = None,
) -> dict:
    """The body of ``POST /tasks/{id}/move``, inside a caller's transaction.

    Split out of the route on 2026-09-23 (WS-39 S6a) so the personal
    ``organize`` decision can move and assign a task through THIS seam rather
    than growing a second reading of what a move does. Every guard the route
    applies — privacy (D62), required custom fields (192), the assign guard —
    runs here, because this IS the route minus the session and the event.

    ``task`` is the row ``load_visible_task`` returned. ``by`` is the actor.
    Returns the wire task with ``notified`` and ``skipped`` attached.

    ``field_map`` is the map the bulk move's card showed for this task's root
    (D-PM-38, S5). Absent, the automatic map for the task's own root applies,
    as it always did.

    ``user`` is the member whose authority a move to another project needs
    (D-OP-14, :func:`authority.assert_may_move_task`). It has no default, so
    each caller says it. ``None`` means "a subtask that a checked move
    carries", and only this module and ``move.py`` pass it.
    """
    task_id = str(task.id)
    values: dict[str, Any] = {}
    #: Values that had no field in the destination (D-PM-29). Written to
    #: the timeline after the move, with their values, by `record_drops`.
    dropped: dict[str, Any] = {}
    #: D-PM-38 decision 4 — the descendants this move takes along, read and
    #: checked BEFORE any write. `None` when the caller did not ask.
    subtree: list[Any] | None = None

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
        # WS-64 OP-8 (D-OP-14). A move to another project changes who sees
        # the task, so it needs the delete authority on the task as it is.
        # After the 404 and the shape refusals, before any write.
        if user is not None:
            await assert_may_move_task(db, user, task)
        if payload.include_subtasks:
            # A 409 when any descendant is hidden, before anything is written:
            # moving only the visible ones would split the tree unseen.
            subtree = movable_subtree(await load_subtree(db, vis, task_id))
            # D62 for each subtask, BEFORE any write, and naming the subtask:
            # the recursive call below would refuse it too, but its message
            # reads as if the parent were the problem (review of #493).
            refused = await subtree_privacy_refusals(
                db, subtree, str(payload.project_id),
            )
            if refused:
                raise HTTPException(status_code=422, detail=refused[0]["reason"])
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
            # The landing lane's CATEGORY decides `completed_at`, the same
            # rule the bulk path applies (`landing.completion_correction`).
            # Without it a DONE task promoted to a board with no closing
            # lane arrived in "Backlog" with a completion date.
            values.update(completion_correction(
                task, await lane_category(db, values["status_id"]),
            ))
        if new_root != str(task.root_project_id):
            crossed, dropped = await _cross_root_values(
                db, task, new_root, payload.custom_fields, field_map,
            )
            values.update(crossed)

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
        unchanged = row_to_dict(task, TaskModel)
        if payload.include_subtasks:
            unchanged["subtasks_moved"] = 0
            unchanged["subtask_ids"] = []
        return unchanged

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
            db, activity_type="system", created_by=by, task_id=task_id,
            body="Task moved",
            meta={"from_project": str(task.project_id),
                  "from_number": getattr(task, "task_number", None)},
        )
        await record_drops(db, task_id=task_id, dropped=dropped, by=by)

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
                {"tid": task_id, "who": who, "by": by},
            )
        if added or (current - wanted):
            await record_activity(
                db, activity_type="assignment", created_by=by,
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
                task_id=task_id, actor_id=by,
            )

    # ── The subtree, in the SAME transaction (D-PM-38 decision 4) ───────
    #
    # Each descendant goes through THIS seam, one at a time, so each one gets
    # the D62 refusal, its own status remap (its set may differ from the
    # parent's), the required-field check and the drop record. A refusal on
    # any of them rolls the whole move back, parent included: no split tree.
    # The member's answers for the destination's required fields apply to
    # each, because the dialog asked once for the move. Assignees do not:
    # "who owns it" was asked about the parent.
    moved_ids: list[str] = []
    if subtree is not None:
        for child in subtree:
            if str(child.project_id) == str(payload.project_id):
                continue
            await move_task_in(
                db, vis, child,
                MoveTask(
                    project_id=str(payload.project_id),
                    custom_fields=payload.custom_fields,
                ),
                by=by,
                # Carried by the checked move above (D-OP-14).
                user=None,
            )
            moved_ids.append(str(child.id))

    result = row_to_dict(row, TaskModel)
    result["notified"] = notified["notified"]
    result["skipped"] = notified["skipped"]
    if payload.include_subtasks:
        result["subtasks_moved"] = len(moved_ids)
        result["subtask_ids"] = moved_ids
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

    The work is :func:`move_task_in`. This route only opens the session and
    emits the event.
    """
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        task = await load_visible_task(db, vis, task_id)
        result = await move_task_in(
            db, vis, task, payload, by=actor(user), user=user,
        )
        await attach_agent_state(db, [result])

    await emit("pm.task.moved", {"task_id": task_id})
    # D-PM-38 — one event per task the move took along, like the bulk move.
    for child_id in result.get("subtask_ids", []):
        await emit("pm.task.moved", {"task_id": child_id})
    return result


#: The most subtask ids that one delete names (D-OP-10). ``all`` has no such
#: cap, because the server finds that set itself.
MAX_DELETE_IDS = 1_000
#: The most ids ``DELETE /tasks/{id}`` takes in its query string. A longer
#: list overruns the header limit, so the caller sends ``delete_all_subtasks``.
MAX_QUERY_DELETE_IDS = 200
#: 422: an id of the set is not a descendant that the caller can see.
#: Nothing changes. A hidden id gets the same code as a wrong id, so the code
#: says nothing about a hidden task.
SUBTASK_NOT_BELOW = "subtask_not_below"


def clean_task_ids(ids: Any) -> frozenset[str]:
    """The ids as canonical UUID strings, or 422 for a value that is not one."""
    out: set[str] = set()
    for raw in ids:
        try:
            out.add(str(uuid.UUID(str(raw))))
        except ValueError:
            raise HTTPException(
                status_code=422, detail="A subtask id is not a task id.",
            ) from None
    if len(out) > MAX_DELETE_IDS:
        raise HTTPException(
            status_code=422, detail=f"At most {MAX_DELETE_IDS} subtask ids in one delete.",
        )
    return frozenset(out)


def kept_parents(
    tree: dict[str, str], task_id: str, grandparent: str | None, gone: frozenset[str],
) -> dict[str, str | None]:
    """The new parent of each KEPT descendant whose parent is deleted.

    It is the nearest ancestor that survives. When no ancestor below the
    task survives, it is the task's own parent, *grandparent* (D-PM-38,
    generalised by D-OP-10). A kept descendant whose parent survives does
    not move.
    """
    dead = gone | {task_id}
    moves: dict[str, str | None] = {}
    for kid, parent in tree.items():
        if kid in dead or parent not in dead:
            continue
        up = parent
        # Each step goes one level up inside the tree, so the walk ends after
        # at most `len(tree)` steps. The cap holds even on a bad row.
        for _ in range(len(tree) + 1):
            if up not in dead or up == task_id:
                break
            up = tree.get(up, task_id)
        moves[kid] = grandparent if up in dead else up
    return moves


async def delete_task_in(
    db: Any, doomed: Any, *, vis: Any = None,
    delete_ids: frozenset[str] = frozenset(), delete_all: bool = False,
) -> DeleteResponse:
    """The body of ``DELETE /tasks/{id}``, inside a caller's transaction.

    Split out on 2026-09-24 (WS-39 S6g) so My Tasks' purge
    (``DELETE /projects/my/tasks/{id}``) can refuse a task outside the
    member's personal tree and delete in the SAME transaction as that check.
    ``doomed`` is the row ``load_visible_task`` returned. The caller emits
    one ``pm.task.deleted`` for the task and one for each id of
    ``subtask_ids`` on the result.

    *delete_ids* and *delete_all* (D-OP-10, owner 2026-10-23). The
    descendants, at any depth, that go with the task. *delete_all* takes
    every descendant that *vis* can see. Each id of *delete_ids* must be a
    descendant that *vis* can see, or the call is 422 and nothing changes.
    A hidden descendant is never deleted. It is kept, as each descendant
    not in the set is. A kept one whose parent goes moves up to its nearest
    surviving ancestor, or to the task's own parent when none below the task
    survives (:func:`kept_parents`). ``subtasks_kept_hidden`` counts the
    hidden ones, with no title. Each row goes through a plain ``DELETE``, so
    the trigger of migration 168 writes one tombstone for each. With neither
    argument the rule is D-PM-38, the rule before D-OP-10.
    """
    task_id = str(doomed.id)
    removed: list[str] = []
    hidden = 0
    if delete_ids or delete_all:
        if vis is None:
            # A caller that names subtasks must say who can see them. Fail
            # closed, and never read "no visibility" as "sees everything".
            raise ValueError("delete_task_in needs vis to delete subtasks")
        # One seam (cascade.load_subtree): the whole subtree, each row with
        # `visible`, through the caller's visibility and the agent's pins.
        rows = await load_subtree(db, vis, task_id)
        tree = {str(r.id): str(r.parent_task_id) for r in rows}
        seen = frozenset(str(r.id) for r in rows if r.visible)
        hidden = len(tree) - len(seen)
        if delete_all:
            ids = seen
        else:
            ids = clean_task_ids(delete_ids)
            if ids - seen:
                raise HTTPException(status_code=422, detail=SUBTASK_NOT_BELOW)
        # The grandparent is read from the database, for the reason that
        # `lift_subtasks_to_grandparent` gives.
        row = (await db.execute(
            text("SELECT parent_task_id FROM pm_tasks WHERE id = CAST(:tid AS uuid)"),
            {"tid": task_id},
        )).fetchone()
        grand = getattr(row, "parent_task_id", None) if row is not None else None
        moves = kept_parents(tree, task_id, str(grand) if grand else None, ids)
        # The moves go BEFORE the deletes. The FK's SET NULL would otherwise
        # make each kept row top-level first.
        for kid, parent in moves.items():
            await db.execute(
                text("UPDATE pm_tasks SET parent_task_id = CAST(:p AS uuid)"
                     " WHERE id = CAST(:k AS uuid)"),
                {"p": parent, "k": kid},
            )
        children = list(moves)
        removed = sorted(ids)
        if removed:
            await db.execute(
                text("DELETE FROM pm_tasks WHERE id = ANY(CAST(:ids AS uuid[]))"),
                {"ids": removed},
            )
    else:
        # D-PM-38 (B10) — the subtasks move UP ONE LEVEL, to this task's own
        # parent, before the row goes. The FK's SET NULL would make them
        # top-level, which is two levels for a subtask of a subtask. In the
        # same transaction as the delete, so a failure leaves both as they were.
        #
        # WS-27ae / P-27 — the moved ids come back from the UPDATE, because
        # their `parent_task_id` changed and `updated_at` did not. Without the
        # bump below, a promoted subtask is an edit no delta client can see.
        children = await lift_subtasks_to_grandparent(db, task_id)
    promoted = len(children)
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
    out = DeleteResponse(
        deleted=task_id,
        cascaded={
            "activities": int(activities),
            "assignees": int(assignees),
            "links": int(links),
            "subtasks_promoted": int(promoted),
            "subtasks_deleted": len(removed),
            "subtasks_kept_hidden": int(hidden),
        },
    )
    out._subtask_ids.extend(removed)
    return out


@router.get("/tasks/{task_id}/subtree")
async def task_subtree(
    task_id: str, user: UserContext = Depends(get_current_user),
) -> dict:
    """The subtasks that the delete dialog and the chat card offer (D-OP-10).

    Every descendant the caller can see, at any depth, top down, archived
    ones included. ``hidden`` counts the ones the caller cannot see, with no
    title, because a delete keeps them (``delete_task_in``). One read through
    ``cascade.load_subtree``, so it shows the set that ``delete_all`` takes.
    """
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        await load_visible_task(db, vis, task_id)
        rows = await load_subtree(db, vis, task_id)
    shown = [r for r in rows if r.visible]
    return {
        "task_id": task_id,
        "subtasks": [
            {
                "id": str(r.id),
                "parent_task_id": str(r.parent_task_id) if r.parent_task_id else None,
                "title": r.title,
                "status_name": r.status_name,
                "status_category": r.status_category,
                "archived": getattr(r, "archived_at", None) is not None,
            }
            for r in shown
        ],
        "hidden": len(rows) - len(shown),
    }


@router.delete("/tasks/{task_id}")
async def delete_task(
    task_id: str, user: UserContext = Depends(get_current_user),
    delete_subtask_ids: list[str] = Query(default=[]),
    delete_all_subtasks: bool = Query(default=False),
) -> DeleteResponse:
    """Delete a task, and say what went with it.

    Subtasks are **promoted, not destroyed**. D-PM-38 moves them up ONE
    level, to this task's own parent, before the delete. The FK's SET NULL
    alone would make a subtask of a subtask top-level. So the reported
    ``subtasks_promoted`` count is not a cascade but its opposite, and is
    named accordingly. Reporting it as "deleted" would be
    the exact class of lie the N8 purge shipped: a count that reassures in the
    wrong direction.

    ``?delete_subtask_ids=<id>``, once for each id (D-OP-10), names the
    descendants that go with the task. A query and not a body, because a
    DELETE in this package takes no body. The default is none, so D-PM-38
    holds. ``subtasks_deleted`` counts the ids that went. Each id must be a
    descendant the member can see, or the call is 422 ``subtask_not_below``.
    ``?delete_all_subtasks=true`` takes every subtask the member can see,
    with no id list, because a long list overruns the URL. A list is at most
    ``MAX_QUERY_DELETE_IDS`` ids. One ``pm.task.deleted`` goes out for the
    task and one for each subtask.

    The work is :func:`delete_task_in`. This route opens the session, loads
    the task through the caller's visibility and emits the event.
    """
    # A direct call, as most tests of this package make, gets the `Query`
    # object itself, so only a list counts (the reason `require_precondition`
    # gives for `If-Match`).
    ids = clean_task_ids(delete_subtask_ids if isinstance(delete_subtask_ids, list) else [])
    if len(ids) > MAX_QUERY_DELETE_IDS:
        raise HTTPException(
            status_code=422,
            detail=f"Pick up to {MAX_QUERY_DELETE_IDS} subtasks, or choose Delete all.",
        )
    take_all = delete_all_subtasks is True
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        doomed = await load_visible_task(db, vis, task_id)
        # WS-64 OP-8 (D-OP-12). Checked on the task the member names. The
        # subtasks that go with it follow that one check.
        await assert_may_delete_task(db, user, doomed)
        result = await delete_task_in(
            db, doomed, vis=vis, delete_ids=frozenset() if take_all else ids,
            delete_all=take_all,
        )

    for gone in (task_id, *result.subtask_ids):
        await emit("pm.task.deleted", {"task_id": gone})
    return result


# ── The delete of an agent, with the member's yes (WS-55 EA-6h, EA3-51) ─────

#: The coded refusals of ``agent-delete``. The MCP edge maps each one to a
#: fixed text, and never shows the detail itself.
#: 403: the member's switch "Let my AI agents delete tasks" is off.
AGENT_DELETE_OFF = "agent_delete_off"
#: 409: the task changed after the agent read it.
AGENT_DELETE_STALE = "agent_delete_stale"
#: 409: ``confirm_title`` is not the exact current title.
AGENT_DELETE_TITLE = "agent_delete_title"

#: The longest ``confirm_title``. The edge takes the same cap.
AGENT_DELETE_TITLE_MAX = 2_000


class AgentDeleteIn(BaseModel):
    """``POST /projects/tasks/{id}/agent-delete`` (EA-6h). The agent sends
    the version and the title that it read, so it must read the task and
    name it (EA3-51 layer 3)."""

    model_config = ConfigDict(extra="forbid")

    expected_version: str = Field(min_length=1, max_length=64)
    confirm_title: str = Field(min_length=1, max_length=AGENT_DELETE_TITLE_MAX)
    #: The member's answer (D-OP-10). Required, with no default: the agent
    #: lists the subtasks to the person and passes what they chose. ``none``
    #: keeps every one, ``all`` deletes the whole subtree, and a list names
    #: the ids that go.
    subtasks_to_delete: Literal["none", "all"] | Annotated[
        list[str], Field(max_length=MAX_DELETE_IDS)]


def _agent_delete_via(user: UserContext) -> str:
    """The ``mcp:<slug>`` stamp of the signed agent caller, or 403.

    A member deletes with ``DELETE /projects/tasks/{id}``. This route is the
    agent's only, and it needs ``projects:write`` (branch 0 checks it too).
    """
    agent = getattr(user, "agent", None)
    if agent is None:
        raise HTTPException(
            status_code=403,
            detail="Only an agent uses this route. A member deletes a task in Metorite.",
        )
    via = str(getattr(agent, "actor_via", "") or "")
    if not (via.startswith("mcp:") and ACTOR_VIA_PATTERN.fullmatch(via)):
        raise HTTPException(status_code=403, detail="This agent connection has no client stamp.")
    if PROJECTS_WRITE not in tuple(getattr(agent, "scopes", ()) or ()):
        raise HTTPException(status_code=403, detail="This connection may not write.")
    return via


@router.post("/tasks/{task_id}/agent-delete")
async def agent_delete_task(
    task_id: str, payload: AgentDeleteIn,
    user: UserContext = Depends(get_current_user),
) -> DeleteResponse:
    """An MCP agent deletes one task, after the member said yes (EA3-51).

    The four layers of EA3-51. (1) The member's switch, read here in the
    transaction of the delete: off answers 403 ``agent_delete_off``. (2) The
    tool's ``destructiveHint`` and its text, at the edge. (3) The task row,
    locked: a version other than ``updated_at`` or a title other than the
    exact current title answers 409, and nothing changes. (4) The delete goes
    through :func:`delete_task_in`, the one core, and the trigger of
    migration 168 writes each tombstone. ``subtasks_to_delete`` is the
    member's answer (D-OP-10). ``all`` takes each descendant that the member
    can see through the connection's pins, and keeps each hidden one. An id
    that is not such a descendant is 422 ``subtask_not_below``, and nothing
    changes.

    A task outside the member's visibility, or the connection's pins, is 404.
    One ``pm.task.deleted`` goes out for the task and one for each deleted
    subtask, after the commit. Each carries the client stamp as ``via``.
    """
    via = _agent_delete_via(user)
    try:
        task_id = str(uuid.UUID(task_id))
    except ValueError:
        raise HTTPException(status_code=404, detail="Task not found") from None
    expected = parse_precondition(payload.expected_version)
    from gateway.routes.admin.agent_access_switch import delete_enabled

    async with _tenant_session() as db:
        if not await delete_enabled(db, getattr(user, "email", None) or ""):
            raise HTTPException(status_code=403, detail=AGENT_DELETE_OFF)
        vis = await resolve_visibility(db, user)
        await load_visible_task(db, vis, task_id)
        # The lock holds the row until the delete commits, so a member's edit
        # cannot land between the two checks and the delete.
        doomed = await lock_task(db, task_id)
        # WS-64 OP-8 (D-OP-12). The member's own authority, the rule of the
        # app route. The edge maps this 403 to the same sentence.
        await assert_may_delete_task(db, user, doomed)
        if doomed.updated_at != expected:
            raise HTTPException(status_code=409, detail=AGENT_DELETE_STALE)
        if doomed.title != payload.confirm_title:
            raise HTTPException(status_code=409, detail=AGENT_DELETE_TITLE)
        # `vis` carries the agent's pins (`core._narrow_for_agent`), so a
        # descendant outside them reads as hidden and stays.
        choice = payload.subtasks_to_delete
        result = await delete_task_in(
            db, doomed, vis=vis, delete_all=choice == "all",
            delete_ids=frozenset() if isinstance(choice, str) else clean_task_ids(choice),
        )

    _log.info("projects.task.agent_deleted", task_id=task_id, via=via,
              subtasks_deleted=len(result.subtask_ids),
              subtasks_kept_hidden=result.cascaded.get("subtasks_kept_hidden", 0),
              connection_id=str(getattr(user.agent, "connection_id", "")))
    for gone in (task_id, *result.subtask_ids):
        await emit("pm.task.deleted", {"task_id": gone, "via": via})
    return result


# ── Archive (WS-27w item 1) ─────────────────────────────────────────────────


@dataclass(frozen=True)
class ArchiveOutcome:
    """What :func:`archive_task_in` did, for the reply and the events that
    the caller emits after its commit."""

    #: The task row, with its agent pill.
    row: dict
    #: True when the task was on the shelf before, so nothing was written.
    was_archived: bool
    #: The subtree that ``include_subtasks`` shelved, or ``None``.
    cascade: Any | None


async def archive_task_in(
    db: Any, vis: Any, task: Any, user: UserContext, *, include_subtasks: bool,
) -> ArchiveOutcome:
    """The ONE core of an archive, inside the caller's transaction.

    Two callers: :func:`archive_task`, and the archive approval of WS-55
    EA-6g (``agent_runs.approve_task_archive``, EA3-40), which writes the
    member's answer in the same transaction. *task* is a row that the caller
    loaded through ``load_visible_task``. ``include_subtasks`` has no
    default, so each caller says whether it shelves the subtree. The caller
    emits ``pm.task.archived`` after its commit when ``was_archived`` is
    false, and the events of ``cascade``.
    """
    task_id = str(task.id)
    status = await require_row(
        db, "pm_task_statuses", str(task.status_id), "Status",
    )
    was_archived = getattr(task, "archived_at", None) is not None
    row = task
    # Already archived — idempotent, the double-click answer. The
    # subtasks are still asked about, so a second click with the box
    # ticked shelves children the first click left out.
    if not was_archived:
        row = await update_row(
            db, "pm_tasks", task_id, {"archived_at": now()},
        )
        await record_activity(
            db, activity_type="system", created_by=actor(user),
            task_id=task_id,
            # Names the lane, because the status no longer implies the
            # outcome. See `archive_note`.
            body=archive_note(
                getattr(status, "name", None),
                str(getattr(status, "category", "") or ""),
            ),
        )
    cascade = (
        await archive_subtree(db, vis, task_id, by=actor(user))
        if include_subtasks else None
    )
    result = row_to_dict(row, TaskModel)
    await attach_agent_state(db, [result])
    return ArchiveOutcome(result, was_archived, cascade)


@router.post("/tasks/{task_id}/archive")
async def archive_task(
    task_id: str, user: UserContext = Depends(get_current_user),
    include_subtasks: bool = False,
) -> dict:
    """Archive one task, from ANY status.

    Archive is a shelf: it hides a task from the boards, lists, calendars and
    searches where it is not relevant right now, and it is reversible by
    `unarchive_task`. It is not an outcome, and it does not claim one.

    ⚠️ **This refused an open task until 2026-09-21 (P-3).** Owner ruling
    removed the guard: parking and abandoning are different acts, many
    projects have no `cancelled` lane to satisfy it with, and "never again"
    is `DELETE` now. `core.archive_note` carries the full reasoning and,
    importantly, the thing the guard was right about.

    The activity row names the lane the task sat in, because the status no
    longer implies the outcome.

    D-PM-38 decision 4 (S5). ``?include_subtasks=true`` shelves every visible
    descendant too (``cascade.archive_subtree``), and the reply carries
    ``subtasks_archived`` and ``subtask_ids``. The default is false. Unarchive
    does NOT cascade: the client's Undo restores ``subtask_ids``, which are
    exactly the tasks this call shelved.

    The work is :func:`archive_task_in`, the one core that the archive
    approval of WS-55 EA-6g calls too.
    """
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        task = await load_visible_task(db, vis, task_id)
        outcome = await archive_task_in(
            db, vis, task, user, include_subtasks=include_subtasks,
        )
        result = outcome.row
        cascade = outcome.cascade
        if cascade is not None:
            result["subtasks_archived"] = len(cascade.ids)
            result["subtask_ids"] = cascade.ids

    if not outcome.was_archived:
        await emit("pm.task.archived", {"task_id": task_id})
    if cascade is not None:
        await emit_all(cascade.events)
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

    ⚠️ **No subtask cascade in this direction** (D-PM-38, S5). A child can be
    on the shelf for its own reason, and nothing on the row says whether its
    parent took it there. The client restores the exact ids an archive
    cascade reported, and nothing else.
    """
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        task = await load_visible_task(db, vis, task_id)
        if getattr(task, "archived_at", None) is None:
            return await _with_agent_state(db, row_to_dict(task, TaskModel))
        # ⚠️ A MERGED task loses its pointer here, and it must: migration
        # 210's `pm_tasks_merged_is_archived` refuses a row that claims to be
        # merged while off the shelf, so clearing one without the other is a
        # 500 from the driver.
        #
        # It is also the honest reading. Restoring a merged task makes it a
        # task again, so it stops redirecting. What does NOT come back is its
        # content — the comments, history and attachments moved to the task
        # it went to, and they stay there. The two `merge` activities on
        # either side are what still say where.
        merged_away = getattr(task, "merged_into_task_id", None) is not None
        row = await update_row(db, "pm_tasks", task_id, {
            "archived_at": None,
            **({"merged_into_task_id": None} if merged_away else {}),
        })
        await record_activity(
            db, activity_type="system", created_by=actor(user),
            task_id=task_id,
            body=(
                "Task restored from the archive and is no longer merged"
                if merged_away
                else "Task restored from the archive"
            ),
        )
        result = await _with_agent_state(db, row_to_dict(row, TaskModel))

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
        await insert_assignees(db, task_id, added, by=actor(user))
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
    elif removed:
        # WS-58 MD-5a — a removal alone emits no event, and the task leaves
        # the My Day of each member removed. So the org version moves, after
        # the commit, with no new `pm.*` event: workflow triggers stay as
        # they were (my_day_feed.md §8.4 item 5).
        from gateway.routes.shell import cache as feed_cache

        await feed_cache.bump_org(task_org)
    return {
        "task_id": task_id,
        "assignees": sorted(wanted),
        # Named rather than swallowed: assigning somebody outside the project's
        # grant closure is a real thing to do by accident, and it leaves them
        # holding work they cannot open. Saying so is what lets the assigner
        # fix it.
        "not_notified": notified["skipped"],
    }


# ── Claim (WS-55 EA-6b) ─────────────────────────────────────────────────────

#: The codes of a refused claim (spec `external_agent_access.md` §9.2,
#: EA3-34). Each refusal is a 422 whose detail is ``{"error": <code>,
#: "message": <text>}``. The MCP edge maps each code to a fixed text of its
#: own (``agent_edge.tools.CLAIM_TEXTS``) and never shows the message. A test
#: pins the two sets equal.
CLAIM_CLOSED = "claim_closed"
CLAIM_TRIAGE = "claim_triage"
CLAIM_ARCHIVED = "claim_archived"
CLAIM_NO_STARTED_STATUS = "claim_no_started_status"
CLAIM_REFUSALS: frozenset[str] = frozenset({
    CLAIM_CLOSED, CLAIM_TRIAGE, CLAIM_ARCHIVED, CLAIM_NO_STARTED_STATUS,
})


#: The claim's own insert of its assignee key. It is ``core.insert_assignees``
#: with ``RETURNING``, so the claim knows which rows it wrote. The PUT keeps
#: the shared helper, which returns nothing.
_CLAIM_INSERT_SQL = (
    "INSERT INTO pm_task_assignees (task_id, assignee, assigned_by) "
    "VALUES (CAST(:tid AS uuid), :who, :by) "
    "ON CONFLICT (task_id, assignee) DO NOTHING RETURNING assignee"
)


async def _insert_claim_assignees(db: Any, task_id: str, wanted: list[str], *,
                                  by: str) -> list[str]:
    """Insert each assignee of *wanted* (lower case), and return the ones that
    this insert wrote. A key that another transaction committed first is not
    in the answer."""
    written: list[str] = []
    for who in sorted({str(w).strip().lower() for w in wanted if str(w or "").strip()}):
        row = (await db.execute(
            text(_CLAIM_INSERT_SQL), {"tid": task_id, "who": who, "by": by},
        )).fetchone()
        if row is not None:
            written.append(str(row.assignee))
    return written


def _refuse_claim(code: str, message: str) -> NoReturn:
    """Raise the 422 of a refused claim. Inside the transaction, so a
    refusal after a write rolls that write back."""
    raise HTTPException(status_code=422,
                        detail={"error": code, "message": message})


@router.post("/tasks/{task_id}/claim")
async def claim_task(
    task_id: str, user: UserContext = Depends(get_current_user),
    if_match: str | None = Header(None, alias="If-Match"),
) -> dict:
    """The caller takes the task: add them as an assignee, and start it.

    WS-55 EA-6b (spec `external_agent_access.md` §9.2, EA3-34). ONE
    transaction, with ``If-Match`` and no body. The name says that the call
    can move the status, which ``assignees/self`` hid. A member may call it
    too, and nothing here reads ``UserContext.agent``.

    * ``backlog`` or ``todo``: add the caller, keep every other assignee, and
      move the task to the first ``in_progress`` lane of its OWN status set
      (``status_owner_id``, never the root: a subproject can own its set).
    * ``in_progress``: add the caller. The status does not move.
    * ``done``, ``cancelled``, ``triage``, or an archived task: 422, and
      nothing is written. ``load_visible_task`` does not filter these, so
      this route checks.
    * No ``in_progress`` lane: 422, and the assignee write rolls back.
    * Already assigned and already started: 200, and no activity row.

    The assignee half reuses the path of ``set_assignees``: the personal
    project guard, the key insert (``_insert_claim_assignees``, the SQL of
    ``insert_assignees`` with ``RETURNING``), an ``assignment`` activity,
    ``ensure_watchers``, ``notify(kind="assigned")`` and the
    ``pm.task.assigned`` event. ``notify`` drops the actor, so a self claim
    pings nobody. ``record_activity`` stamps ``meta.via`` on both rows.

    **The lock order (fix round 3, option A).** The visibility load, then
    the assignee key, then the task row lock (``core.lock_task``), then the
    version check and the refusals, then the row writes, then the watcher
    key. That is the order of ``set_assignees``: the key before the row,
    and the watcher key after it. So the claim adds no lock order that a
    member path does not already have, and it changes no member path.

    **EA-13c (EA3-42).** Before the row lock, an agent's claim opens its
    run through ``agent_runs.open_run_for_claim`` while the runs flag is on.
    The one proven rule is the run BEFORE the task row: a run writer (a
    member's start, the archive request) takes the run and then the row for
    its comment, and O-M9 of ``test_mcp_edge_claim.py`` deadlocks when the
    claim takes the row first. The run's place against the assignee key does
    not matter, because no run writer takes an assignee key, so no cycle can
    pass through the key. The answer carries ``agent_run_id``, which is None
    for a member and with the flag off.
    """
    me = actor(user).strip().lower()
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        # 1. The visibility load answers 404 for a task the caller cannot see.
        seen = await load_visible_task(db, vis, task_id)
        # The tenant of the dispatch sink, read as `set_assignees` reads it.
        task_org = str(getattr(seen, "organization_id", "") or "")

        # 2. The assignee key FIRST, through the helpers and in the order of
        # `set_assignees` (fix round 3, option A): the key, then the row,
        # then the watcher key. The PUT, the merge and the bulk add take the
        # key before the row too, so the claim adds no lock order that a
        # member path does not already have. A second claim of the same
        # member waits here on the first claim's key.
        current = {
            r.assignee for r in (await db.execute(
                text(
                    "SELECT assignee FROM pm_task_assignees "
                    "WHERE task_id = CAST(:tid AS uuid)"
                ),
                {"tid": task_id},
            )).fetchall()
        }
        wanted = sorted({me} - current)
        await assert_assignable_here(db, str(seen.project_id), set(wanted))
        # Final fix (review P2): `added` is the set that THIS insert wrote,
        # not the set that the read above missed. Two claims of one member
        # with no If-Match both read an empty set, and the second insert
        # writes nothing. Without RETURNING the second claim also wrote an
        # `assignment` row, answered `assigned: true` and emitted
        # `pm.task.assigned` for a row it did not write.
        added = await _insert_claim_assignees(db, task_id, wanted, by=actor(user))

        # 2b. EA-13c (EA3-42): an agent's claim opens its run while the runs
        # flag is on, through the one run writer. BEFORE the row: a run writer
        # takes the run, then the task row (its comment bumps the row), and
        # the claim keeps that order (O-M9). Its place after the key does not
        # matter: no run writer takes an assignee key. A refusal below rolls
        # the run back with the claim. None for a member.
        from gateway.routes.projects import agent_runs

        run_id = await agent_runs.open_run_for_claim(db, task_id, user)

        # 3. The row lock, and the row as the lock read it (fix round 1).
        # Two claims serialise here, and the second one sees the new
        # `updated_at`. A PATCH to done that holds the row makes the claim
        # wait, and the claim then reads the done.
        task = await lock_task(db, task_id)
        # 4. The version, on the locked row and before any row write.
        require_precondition(task, if_match, row_to_dict(task, TaskModel))
        # 5. The refusals. Each one raises inside the transaction, so the
        # keys of step 2 roll back and a refused claim writes nothing.
        if getattr(task, "archived_at", None) is not None:
            _refuse_claim(CLAIM_ARCHIVED, "An archived task cannot be claimed.")
        status = await require_row(
            db, "pm_task_statuses", str(task.status_id), "Status",
        )
        category = str(getattr(status, "category", "") or "")
        if category in CLOSING_CATEGORIES:
            _refuse_claim(CLAIM_CLOSED,
                          "A done or cancelled task cannot be claimed.")
        if category == TRIAGE_CATEGORY:
            _refuse_claim(CLAIM_TRIAGE,
                          "A task in triage cannot be claimed. Accept it first.")
        lane = None
        if category != STARTED_CATEGORY:
            home = await status_owner_id(db, str(task.project_id))
            try:
                lane = await load_default_status(db, home, STARTED_CATEGORY)
            except HTTPException:
                _refuse_claim(
                    CLAIM_NO_STARTED_STATUS,
                    "This project has no in progress status. Add one first.",
                )

        # 6. The writes of the row: the activity (its `touch_task` bumps the
        # locked row), then the move from the locked row.
        if added:
            await record_activity(
                db, activity_type="assignment", created_by=actor(user),
                task_id=task_id, meta={"added": added, "removed": []},
            )
        moved = None
        if lane is not None:
            moved = await apply_status_transition(
                db, task, str(lane.id), created_by=actor(user),
            )

        notified = await notify(
            db, recipients=added, kind="assigned",
            task_id=task_id, actor_id=actor(user),
        )
        # The watcher key after the row, as `set_assignees` writes it. Before
        # the row it crossed a member's PATCH, which writes the row and then
        # the editor's watcher key (fix round 3).
        await ensure_watchers(db, task_id, added, by=actor(user))

        row = await load_visible_task(db, vis, task_id)
        result = row_to_dict(row, TaskModel)
        result["assignees"] = sorted(current | set(wanted))
        await attach_agent_state(db, [result])
        result["moved"] = moved is not None
        result["assigned"] = bool(added)
        result["not_notified"] = notified["skipped"]
        # EA-13c: the run that this claim opened or found, else None.
        result["agent_run_id"] = run_id

    if added:
        await emit("pm.task.assigned", {
            "task_id": task_id, "assignees": added,
            "organization_id": task_org,
        })
    if moved is not None:
        await emit("pm.task.updated", {"task_id": task_id})
        await emit("pm.task.status_changed", {
            "task_id": task_id,
            "from": moved["from"].name, "to": moved["to"].name,
            "to_category": moved["to"].category,
        })
    return result


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
        # The ONE insert, shared with the links from text (TR-5): the row,
        # the `link` activity on the source and the bump of the target.
        link_id = await insert_task_link(
            db, task_id, str(payload.target_task_id), payload.link_type,
            by=actor(user),
        )
        return {
            "id": link_id, "source_task_id": task_id,
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
