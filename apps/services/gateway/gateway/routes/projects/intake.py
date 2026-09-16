"""Projects · intake — the front door (WS-27u).

Spec: ``ai-company-brain/specs/project_management_app.md`` §9.1 (WS-27u);
rationale in ``plane_pm_research_2026-08.md`` §3 (P-1), behind the AGPL wall —
the shape here is this package's own idiom, never a translation.

    POST /projects/intake                       → capture: task + wrapper, one transaction
    GET  /projects/intake                       → the queue (pending, and snoozes past due)
    POST /projects/intake/{task_id}/accept      → flip the task's status IN PLACE
    POST /projects/intake/{task_id}/decline     → archive; the wrapper stays as provenance
    POST /projects/intake/{task_id}/duplicate   → point at the original; archive
    POST /projects/intake/{task_id}/snooze      → hide from the queue until an instant

**A captured task is real from birth.** Capture writes an ordinary ``pm_tasks``
row — commentable, assignable, linkable from the first second — parked in a
status whose category is ``triage``, which is what keeps it out of every
board/list/calendar/timeline/search read (``core.triage_exclusion_clause``,
the ONE copy of that predicate) until somebody rules on it.

**Accept flips the status in place, never copies.** The task keeps its id, its
number, its timeline and anything anyone attached while it was parked. A
copy-on-accept design forks the history at exactly the moment it starts to
matter.

**The wrapper is permanent provenance.** All four rulings UPDATE the
``pm_intake`` row; nothing here deletes it — "where did this come from and who
ruled on it" must stay a point read forever. Decline and duplicate archive the
TASK (the standing soft-delete this app already has) and leave the wrapper
naming why.

**Snooze needs no scheduler.** The queue read is
``snoozed_until <= now()`` — the row reappears by being read, not by being
woken, so the feature costs no worker and no cron (§5's non-goals: /workflows
is the only engine).

**Visibility is the tasks' own** (R5): the queue and every action go through
``task_visibility_clause`` / ``load_visible_task``, so an item outside the
caller's grants is a 404, never a 403, and the queue can never show a capture
its reader could not open.

**Not in scope** (per the ticket): routing rules — auto-accept, agent
screening. Those are ``/workflows`` nodes (D6), added when email capture
(§6.5) lands.
"""

from __future__ import annotations

from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException
from gateway.routes.projects.core import (
    TASK_SOURCES,
    TRIAGE_CATEGORY,
    Page,
    TaskModel,
    _tenant_session,
    actor,
    apply_status_transition,
    emit,
    insert_row,
    load_default_status,
    load_visible_project,
    load_visible_task,
    next_task_number,
    now,
    record_activity,
    require_status_in_project,
    resolve_visibility,
    root_project_id,
    status_owner_id,
    router,
    row_to_dict,
    task_visibility_clause,
    update_row,
    wire,
)
from gateway.routes.projects.filters import parse_when
from pydantic import BaseModel
from sqlalchemy import text


class IntakeIn(BaseModel):
    """The capture payload. Title and project only — the front door asks the
    question a capture can answer, not the six a create form would."""

    project_id: str | None = None
    title: str | None = None
    description: str | None = None
    importance: int | None = None
    due_at: str | None = None
    #: Where this came from — free text ('email', 'slack', 'api', …) stored on
    #: the WRAPPER. When it happens to be a legal `pm_tasks.source` value it is
    #: stamped on the task too, so "captured from email" reads the same as an
    #: email-created task everywhere else.
    source: str | None = None
    source_ref: str | None = None


class AcceptIn(BaseModel):
    #: The destination lane. Omitted, the project's default status is used —
    #: the same answer `POST /tasks` gives a task created with no status.
    status_id: str | None = None


class DuplicateIn(BaseModel):
    duplicate_of_task_id: str


class SnoozeIn(BaseModel):
    until: str


class IntakeModel(BaseModel):
    """The wrapper, 1:1 with `pm_intake`'s columns so `row_to_dict` maps it."""

    id: str
    task_id: str
    status: str
    snoozed_until: str | None = None
    duplicate_of_task_id: str | None = None
    source: str | None = None
    source_ref: str | None = None
    created_by: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


#: The queue states — the two from which a ruling is legal. The other three
#: are terminal: ruling twice would write two contradictory provenance trails.
QUEUE_STATES: frozenset[str] = frozenset({"pending", "snoozed"})

#: The queue read: everything visible, unarchived, and either pending or
#: snoozed past its `snoozed_until`. The reappearance is the comparison —
#: nothing flips the row back, so a snooze needs no worker.
_QUEUE_SQL = """
SELECT t.*,
       i.id AS intake_id,
       i.status AS intake_status,
       i.snoozed_until AS intake_snoozed_until,
       i.source AS intake_source,
       i.source_ref AS intake_source_ref,
       i.duplicate_of_task_id AS intake_duplicate_of_task_id,
       i.created_at AS intake_created_at
  FROM pm_tasks t
  JOIN pm_intake i ON i.task_id = t.id
 WHERE {visible}
   AND t.archived_at IS NULL
   AND (i.status = 'pending'
        OR (i.status = 'snoozed' AND i.snoozed_until <= now()))
   {project_scope}
 ORDER BY i.created_at, t.id
 LIMIT :limit OFFSET :offset
"""

_QUEUE_COUNT_SQL = """
SELECT count(*)
  FROM pm_tasks t
  JOIN pm_intake i ON i.task_id = t.id
 WHERE {visible}
   AND t.archived_at IS NULL
   AND (i.status = 'pending'
        OR (i.status = 'snoozed' AND i.snoozed_until <= now()))
   {project_scope}
"""

#: The queue's project filter covers the SUBTREE — a project's front door
#: receives captures aimed at any of its subprojects, the same closure the
#: task list's `include_subtree` walks.
#:
#: ⚠️ **The recursive step repeats the tenant predicate**, matching
#: `core._VISIBLE_PROJECTS_SQL`. It is redundant today three times over — the
#: seed `:pid` is validated by `load_visible_project`, migration 161's
#: `pm_organization_from_parent` trigger refuses a child whose org disagrees
#: with its parent, and phase-4 RLS will filter the table anyway. It is here
#: for the reason `core.py` states about its own copy: a closure must not be
#: the thing that would leak if the trigger were ever dropped. A descent that
#: only checks its seed trusts every edge it walks.
_SUBTREE_SCOPE = (
    "AND t.project_id IN ("
    "  WITH RECURSIVE sub AS ("
    "    SELECT id FROM pm_projects WHERE id = CAST(:pid AS uuid)"
    "      AND organization_id = CAST(:vis_org AS uuid)"
    "    UNION ALL"
    "    SELECT p.id FROM pm_projects p JOIN sub s"
    "      ON p.parent_project_id = s.id"
    "     WHERE p.organization_id = CAST(:vis_org AS uuid)"
    "  ) SELECT id FROM sub)"
)


async def load_triage_status(db: Any, root_id: str) -> Any:
    """The root's triage-category status, provisioned on first use.

    Provisioned HERE rather than seeded on every root (`tree._seed_root`):
    most projects never use intake, and a "Triage" lane on every fresh board
    would advertise a feature nobody asked that project to have. The insert
    follows the seed's own shape. Position 5 puts it above Backlog (10) on the
    admin list without ever being the `load_default_status` fallback winner in
    a seeded project — and it is never `is_default`, so a normally-created
    task can never land in it by omission.
    """
    row = (await db.execute(
        text(
            "SELECT * FROM pm_task_statuses "
            "WHERE project_id = CAST(:root AS uuid) "
            f"AND category = '{TRIAGE_CATEGORY}' "
            "ORDER BY position, name LIMIT 1"
        ),
        {"root": root_id},
    )).fetchone()
    if row is not None:
        return row
    return await insert_row(db, "pm_task_statuses", {
        "project_id": root_id, "name": "Triage", "color": "gray",
        "position": 5, "category": TRIAGE_CATEGORY, "is_default": False,
    })


async def _load_wrapper(db: Any, task_id: str) -> Any:
    """The task's wrapper, or 404.

    Reached only AFTER `load_visible_task` has said yes, so this 404 means
    "that task never came through the front door" — R5 already collapsed
    "not yours" into the task load above it.
    """
    row = (await db.execute(
        text("SELECT * FROM pm_intake WHERE task_id = CAST(:tid AS uuid)"),
        {"tid": task_id},
    )).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Intake item not found")
    return row


def _require_queue_state(wrapper: Any) -> None:
    """A ruling is legal only from the queue states — a second ruling on a
    terminal wrapper would overwrite the provenance the first one wrote."""
    state = getattr(wrapper, "status", None)
    if state not in QUEUE_STATES:
        raise HTTPException(
            status_code=422,
            detail=f"This intake item was already resolved ('{state}').",
        )


def _shape(task_row: Any, wrapper_row: Any) -> dict[str, Any]:
    return {
        "task": row_to_dict(task_row, TaskModel),
        "intake": row_to_dict(wrapper_row, IntakeModel),
    }


# ── Capture ─────────────────────────────────────────────────────────────────

@router.post("/intake", status_code=201)
async def capture_intake(
    payload: IntakeIn, user: UserContext = Depends(get_current_user),
) -> dict:
    """Create the task and its wrapper in ONE transaction.

    One transaction is the contract, not an optimisation: a task without a
    wrapper is a capture with no provenance, and a wrapper without a task
    points at nothing — neither half may ever exist alone, so there is exactly
    one commit and it covers both writes plus the timeline entry.
    """
    title = (payload.title or "").strip()
    if not title:
        raise HTTPException(status_code=422, detail="A capture needs a title.")
    if not payload.project_id:
        raise HTTPException(status_code=422, detail="A capture needs a project_id.")

    source = (payload.source or "").strip() or None
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        # R5 — capturing INTO a project requires seeing it; an unreadable id
        # answers 404, never 403.
        await load_visible_project(db, vis, str(payload.project_id))
        root = await root_project_id(db, str(payload.project_id))
        # The triage lane belongs to the set this project USES, which is
        # the nearest owner and not necessarily the root (migration 196).
        # `root` still stamps `root_project_id`, which is a different job.
        status = await load_triage_status(
            db, await status_owner_id(db, str(payload.project_id)),
        )

        task = await insert_row(db, "pm_tasks", {
            "project_id": str(payload.project_id),
            "root_project_id": root,
            "status_id": str(status.id),
            "title": title,
            "description": payload.description,
            "importance": payload.importance,
            "due_at": payload.due_at,
            "task_number": await next_task_number(db, root),
            "created_by": actor(user),
            # The task's own source speaks the CHECK'd vocabulary; the
            # wrapper's is free text. When they coincide, both say it.
            **({"source": source} if source in TASK_SOURCES else {}),
        })
        wrapper = await insert_row(db, "pm_intake", {
            "task_id": str(task.id),
            "status": "pending",
            "source": source,
            "source_ref": (payload.source_ref or "").strip() or None,
            "created_by": actor(user),
        })
        await record_activity(
            db, activity_type="system", created_by=actor(user),
            task_id=str(task.id), body="Captured to intake",
            meta={"intake": "captured", "source": source,
                  "source_ref": getattr(wrapper, "source_ref", None)},
        )
        result = _shape(task, wrapper)

    await emit("pm.intake.captured", {
        "task_id": result["task"]["id"],
        "project_id": str(payload.project_id),
        "title": title,
        "source": source,
    })
    return result


# ── The queue ───────────────────────────────────────────────────────────────

@router.get("/intake")
async def list_intake(
    user: UserContext = Depends(get_current_user),
    project_id: str | None = None,
    page: Page = Depends(),
) -> dict:
    """The front-door queue: pending items, plus snoozes whose time has come.

    Scoped by the SAME grants as the tasks it wraps — `task_visibility_clause`
    is the whole gate, so the queue can never show a capture its reader could
    not open, and a `project_id` the caller cannot see is a 404 (R5) rather
    than an empty queue that confirms the project exists.
    """
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        params: dict[str, Any] = dict(vis.params)
        scope = ""
        if project_id:
            await load_visible_project(db, vis, project_id)
            scope = _SUBTREE_SCOPE
            params["pid"] = project_id

        visible = task_visibility_clause(vis)
        total = (await db.execute(
            text(_QUEUE_COUNT_SQL.format(visible=visible, project_scope=scope)),
            params,
        )).scalar() or 0
        rows = (await db.execute(
            text(_QUEUE_SQL.format(visible=visible, project_scope=scope)),
            {**params, "limit": page.limit, "offset": page.offset},
        )).fetchall()

        out = []
        for row in rows:
            item = row_to_dict(row, TaskModel)
            item["intake"] = {
                "id": str(row.intake_id),
                "task_id": item["id"],
                "status": row.intake_status,
                "snoozed_until": wire(row.intake_snoozed_until),
                "source": row.intake_source,
                "source_ref": row.intake_source_ref,
                "duplicate_of_task_id": (
                    str(row.intake_duplicate_of_task_id)
                    if row.intake_duplicate_of_task_id is not None else None
                ),
                "created_at": wire(row.intake_created_at),
            }
            out.append(item)
        return {"rows": out, "total": int(total)}


# ── The four rulings ────────────────────────────────────────────────────────

@router.post("/intake/{task_id}/accept")
async def accept_intake(
    task_id: str, payload: AcceptIn,
    user: UserContext = Depends(get_current_user),
) -> dict:
    """Accept: flip the task's status IN PLACE — never copy.

    The move goes through `apply_status_transition`, the same three-effect
    helper every other status write uses, so accepting writes the same
    `status_change` activity a board drag would. The wrapper flips to
    `accepted` and stays forever.
    """
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        task = await load_visible_task(db, vis, task_id)
        wrapper = await _load_wrapper(db, task_id)
        _require_queue_state(wrapper)

        # The lane it is accepted INTO comes from the set the task's own
        # project uses — the root only agrees until something overrides.
        root = await status_owner_id(db, str(task.project_id))
        destination = (
            await require_status_in_project(db, root, str(payload.status_id))
            if payload.status_id
            else await load_default_status(db, root)
        )
        # Accepting INTO triage is a ruling that rules nothing — refused
        # BEFORE anything is written, so the guard also catches a project
        # whose fallback default happens to be the triage lane itself.
        if getattr(destination, "category", None) == TRIAGE_CATEGORY:
            raise HTTPException(
                status_code=422,
                detail="Accepting must move the task out of triage; "
                       "pick a non-triage status.",
            )
        moved = await apply_status_transition(
            db, task, str(destination.id), created_by=actor(user),
        )
        wrapper = await update_row(db, "pm_intake", str(wrapper.id), {
            "status": "accepted", "snoozed_until": None,
        })
        await record_activity(
            db, activity_type="system", created_by=actor(user),
            task_id=task_id, body="Accepted from intake",
            meta={"intake": "accepted", "status_id": str(destination.id)},
        )
        result = _shape(moved["row"], wrapper)

    await emit("pm.intake.accepted", {"task_id": task_id})
    return result


@router.post("/intake/{task_id}/decline")
async def decline_intake(
    task_id: str, user: UserContext = Depends(get_current_user),
) -> dict:
    """Decline: archive the task; the wrapper stays as the reason it exists.

    Archived, not deleted — the standing soft-delete, so a decline is
    revertible the way any archive is, and the capture's timeline survives.
    """
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        await load_visible_task(db, vis, task_id)
        wrapper = await _load_wrapper(db, task_id)
        _require_queue_state(wrapper)

        task = await update_row(db, "pm_tasks", task_id, {"archived_at": now()})
        wrapper = await update_row(db, "pm_intake", str(wrapper.id), {
            "status": "declined", "snoozed_until": None,
        })
        await record_activity(
            db, activity_type="system", created_by=actor(user),
            task_id=task_id, body="Declined at intake",
            meta={"intake": "declined"},
        )
        result = _shape(task, wrapper)

    await emit("pm.intake.declined", {"task_id": task_id})
    return result


@router.post("/intake/{task_id}/duplicate")
async def duplicate_intake(
    task_id: str, payload: DuplicateIn,
    user: UserContext = Depends(get_current_user),
) -> dict:
    """Duplicate: record WHICH task this repeats, then archive it.

    The original must be visible to the caller — an unreadable target is a
    404 (R5), which is also what keeps a wrapper from pointing across a grant
    boundary the caller cannot see over.
    """
    original = str(payload.duplicate_of_task_id)
    if original == str(task_id):
        raise HTTPException(
            status_code=422, detail="A task cannot be a duplicate of itself.",
        )
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        await load_visible_task(db, vis, task_id)
        await load_visible_task(db, vis, original)
        wrapper = await _load_wrapper(db, task_id)
        _require_queue_state(wrapper)

        task = await update_row(db, "pm_tasks", task_id, {"archived_at": now()})
        wrapper = await update_row(db, "pm_intake", str(wrapper.id), {
            "status": "duplicate", "duplicate_of_task_id": original,
            "snoozed_until": None,
        })
        await record_activity(
            db, activity_type="system", created_by=actor(user),
            task_id=task_id, body="Marked duplicate at intake",
            meta={"intake": "duplicate", "duplicate_of_task_id": original},
        )
        result = _shape(task, wrapper)

    await emit("pm.intake.duplicate", {
        "task_id": task_id, "duplicate_of_task_id": original,
    })
    return result


@router.post("/intake/{task_id}/snooze")
async def snooze_intake(
    task_id: str, payload: SnoozeIn,
    user: UserContext = Depends(get_current_user),
) -> dict:
    """Snooze: hide from the queue until `until`, then reappear on its own.

    Reappearance is the queue's `snoozed_until <= now()` comparison — no
    worker, no cron, nothing to wake. An instant in the past is refused: it
    would be a snooze that never hid anything, which is a no-op wearing a
    ruling's clothes.
    """
    until = parse_when(payload.until, field="until")
    if until <= now():
        raise HTTPException(
            status_code=422, detail="'until' must be in the future.",
        )
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        task = await load_visible_task(db, vis, task_id)
        wrapper = await _load_wrapper(db, task_id)
        _require_queue_state(wrapper)

        wrapper = await update_row(db, "pm_intake", str(wrapper.id), {
            "status": "snoozed", "snoozed_until": until,
        })
        await record_activity(
            db, activity_type="system", created_by=actor(user),
            task_id=task_id, body="Snoozed at intake",
            meta={"intake": "snoozed", "until": until.isoformat()},
        )
        result = _shape(task, wrapper)

    await emit("pm.intake.snoozed", {
        "task_id": task_id, "until": until.isoformat(),
    })
    return result
