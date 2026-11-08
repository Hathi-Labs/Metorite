"""Projects · the cycle store (WS-39 SP-1, Metorite task 960, D195).

Spec: ``project-docs/specs/project_management_app.md`` §13.2 (the calls) and
§13.2a (the data model). Migration: the file that creates ``pm_cycles``.

THE one writer of ``pm_cycles``, ``pm_cycle_tasks``, ``pm_cycle_committed``
and ``pm_cycle_capacity``. A cycle (a sprint) is a time box over one project
node: a space, a folder or a project. A task sits in one cycle at most.

* ``GET    /nodes/{project_id}/cycles``          the cycles of one node
* ``POST   /nodes/{project_id}/cycles``          plan a cycle
* ``GET    /cycles/{cycle_id}``                  one cycle with its tasks
* ``PATCH  /cycles/{cycle_id}``                  edit (``If-Match``)
* ``DELETE /cycles/{cycle_id}``                  delete a planned cycle
* ``POST   /cycles/{cycle_id}/tasks``            add a task
* ``DELETE /cycles/{cycle_id}/tasks/{task_id}``  remove a task
* ``POST   /cycles/{cycle_id}/start``            start: the snapshots
* ``POST   /cycles/{cycle_id}/close``            close: a choice for each task

**The flag.** ``PROJECTS_SPRINTS_ORGS`` (default empty) is the flag
``PROJECTS_SPRINTS`` of the board, as a per-org list. While it does not hold
the org, each route answers 404 and nothing is written. :func:`sprints_allowed`
is its one reader.

**The write right.** A cycle act needs the right that edits tasks on the node:
:func:`core.load_visible_project`, the check of ``POST /tasks``. A member who
sees the node may plan, edit, start, close and delete its cycles. To add or
remove a task needs the right that edits the task:
:func:`core.load_visible_task`, the check of ``PATCH /tasks/{id}``. An agent
follows the same right, because ``resolve_visibility`` narrows it. No new
grant word. A miss is 404, never 403 (R5).

**Hidden tasks (§13.7, task 849).** The store keeps the full total. A reader
sees the tasks that it can see, and ``hidden``: how many it cannot see. A
close that would move a task the caller cannot see is refused (409), as
``cascade.hidden_refusal`` refuses a move.

**The snapshot.** The start copies each member task and its estimate into
``pm_cycle_committed``, and stores the two totals on the cycle. A carry-over
moves membership rows, never the snapshot, so the start total stays true. The
trigger ``pm_cycles_guard`` refuses a change of the start columns.

**The carry-over.** The close takes a choice for each unfinished task:
``next`` with a cycle id, or ``backlog``. It moves the membership and writes
one ``cycle_transfer`` activity on the task, with the meta
``{from_cycle, to_cycle, choice}``. An archived task (a merged task too) needs
no choice. It stays in the closing cycle as part of its record.

**A task that moves away.** A move of a task, or of a project above it, can
take a task out of the node of its open cycle. :func:`detach_moved_tasks`
then takes the task out of that cycle, in the transaction of the move, and
writes one ``cycle_transfer`` activity with the meta
``{from_cycle, to_cycle: null, cause: "task_moved"}``. It runs while the flag
is off too. The start snapshot does not change, and a closed cycle keeps its
rows.

The tenant and the actor come from the session. ``organization_id`` is the
column DEFAULT, the bound tenant, and each ``*_by`` is the caller's email. The
wire models forbid an extra key.

Fence: ``tests/unit/test_sprint_store_r8.py`` (R8).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from acb_auth import UserContext, get_current_user
from acb_common import get_settings
from fastapi import Depends, Header, HTTPException
from gateway.routes.projects.core import (
    CLOSING_CATEGORIES,
    _ancestor_chain_sql,
    _tenant_session,
    actor,
    assert_not_archived,
    clean_payload,
    load_visible_project,
    load_visible_task,
    record_activity,
    require_precondition,
    resolve_visibility,
    router,
    task_visibility_clause,
)
from gateway.routes.projects.filters import attach_agent_state
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

#: The cycle states (the CHECK ``pm_cycles_state``).
CYCLE_STATES: tuple[str, ...] = ("planned", "active", "closed")
#: The choices of a close (§13.2): the next cycle, or the backlog.
CLOSE_CHOICES: tuple[str, ...] = ("next", "backlog")
#: The activity type of one logged move (the CHECK of ``pm_activities.type``).
TRANSFER_TYPE = "cycle_transfer"
#: The meta ``cause`` of a task that left its cycle because the task moved.
MOVED_CAUSE = "task_moved"
#: The bounds of the text columns (the CHECKs hold them too).
NAME_MAX = 120
GOAL_MAX = 500
#: The longest cycle, in days. A sanity bound of the route only.
SPAN_MAX_DAYS = 366
#: The most review minutes of one person in one snapshot: a year of minutes.
REVIEW_MINUTES_MAX = SPAN_MAX_DAYS * 24 * 60
#: The most people in one capacity snapshot, and choices in one close.
CAPACITY_MAX = 100
CHOICES_MAX = 500
#: The most cycles that one list returns, newest first.
LIST_LIMIT = 200
#: The unique index of one active cycle for each node.
ONE_ACTIVE_INDEX = "uq_pm_cycles_one_active"


# ── The flag ────────────────────────────────────────────────────────────────


def sprints_allowed(organization_id: str | None) -> bool:
    """True when ``PROJECTS_SPRINTS_ORGS`` holds the org, or holds ``*``.
    Default OFF.

    The org comes from the session, never from request input (R5). It reads
    ``get_settings()`` on each call, so a flag reload applies a change with
    no restart.
    """
    org = str(organization_id or "").strip().lower()
    if not org:
        return False
    raw = str(getattr(get_settings(), "projects_sprints_orgs", "") or "")
    listed = {p.strip().lower() for p in raw.split(",") if p.strip()}
    return "*" in listed or org in listed


async def require_sprints(user: UserContext = Depends(get_current_user)) -> None:
    """404 while ``PROJECTS_SPRINTS_ORGS`` does not hold the caller's org."""
    if not sprints_allowed(getattr(user, "organization_id", None)):
        raise HTTPException(status_code=404, detail="Not Found")


_ENABLED = [Depends(require_sprints)]


# ── Small helpers ───────────────────────────────────────────────────────────


def _refusal(status: int, error: str, message: str) -> HTTPException:
    """A refusal that says what happened and what to do next."""
    return HTTPException(status_code=status, detail={"error": error, "message": message})


def _uuid_or_404(value: Any, what: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=f"{what} not found") from exc


@asynccontextmanager
async def _savepoint(db: Any) -> AsyncIterator[None]:
    """A ``SAVEPOINT`` around one statement, so its error leaves the
    transaction usable (the idiom of ``agent_runs._savepoint``)."""
    begin_nested = getattr(db, "begin_nested", None)
    if begin_nested is None:  # pragma: no cover — only the fakes lack it
        yield
        return
    async with begin_nested():
        yield


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _wire(row: Any) -> dict[str, Any]:
    """One cycle as the routes show it.

    The two stored totals of the start are not here: they count the tasks
    that the reader cannot see too (§13.7). The read of one cycle gives the
    reader's own count and ``hidden``.
    """
    budget = row.credit_budget
    return {
        "id": str(row.id),
        "project_id": str(row.project_id),
        "name": row.name,
        "goal": row.goal,
        "starts_on": _iso(row.starts_on),
        "ends_on": _iso(row.ends_on),
        "cooldown_days": row.cooldown_days,
        "state": row.state,
        "credit_budget": float(budget) if budget is not None else None,
        "started_at": _iso(row.started_at),
        "started_by": row.started_by,
        "closed_at": _iso(row.closed_at),
        "closed_by": row.closed_by,
        "created_by": row.created_by,
        "created_at": _iso(row.created_at),
        # The If-Match token, as for a task (D-PM-20).
        "updated_at": _iso(row.updated_at),
    }


def _check_span(starts_on: date, ends_on: date) -> None:
    if ends_on < starts_on:
        raise _refusal(422, "bad_dates", "The cycle ends before it starts. Pick an end date "
                       "on or after the start date.")
    if (ends_on - starts_on).days > SPAN_MAX_DAYS:
        raise _refusal(422, "bad_dates", f"A cycle lasts at most {SPAN_MAX_DAYS} days. "
                       "Pick a nearer end date.")


def _clean_name(raw: Any) -> str:
    name = str(raw or "").strip()
    if not name:
        raise _refusal(422, "bad_name", "A cycle needs a name. Write a short name.")
    return name


def _clean_goal(raw: Any) -> str | None:
    if raw is None:
        return None
    goal = str(raw).strip()
    return goal or None


# ── The loads ───────────────────────────────────────────────────────────────


async def _load_cycle(db: Any, vis: Any, cycle_id: Any, *, lock: str = "") -> Any:
    """One cycle on a node that the caller can see, or 404.

    ``lock`` is ``""``, ``"FOR SHARE"`` or ``"FOR UPDATE"``. The visibility
    clause and the lock are one statement, so the state that the caller
    reads is the state that the lock holds.
    """
    cid = _uuid_or_404(cycle_id, "Cycle")
    if lock not in ("", "FOR SHARE", "FOR UPDATE"):
        raise ValueError(f"unknown lock {lock!r}")
    row = (await db.execute(
        text(
            "SELECT c.* FROM pm_cycles c WHERE c.id = CAST(:cid AS uuid)"
            f" AND {vis.project_clause('c.project_id')}"
            + (f" {lock} OF c" if lock else "")
        ),
        {"cid": cid, **vis.params},
    )).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Cycle not found")
    return row


#: Is the project ``:pid`` the node ``:node``, or below it? The walk is the
#: one ancestor chain of ``core`` (no third walk).
_IN_NODE_SQL = _ancestor_chain_sql("id = CAST(:pid AS uuid)") + (
    "SELECT EXISTS (SELECT 1 FROM chain WHERE id = CAST(:node AS uuid))"
)


async def _task_in_node(db: Any, task: Any, node_id: str) -> bool:
    return bool((await db.execute(
        text(_IN_NODE_SQL), {"pid": str(task.project_id), "node": node_id},
    )).scalar())


async def _node_visible(db: Any, vis: Any, project_id: Any) -> bool:
    found = (await db.execute(
        text(
            "SELECT 1 FROM pm_projects WHERE id = CAST(:p AS uuid)"
            f" AND {vis.project_clause()}"
        ),
        {"p": str(project_id), **vis.params},
    )).fetchone()
    return found is not None


# ── The bodies ──────────────────────────────────────────────────────────────


class CycleIn(BaseModel):
    """Plan a cycle. The node comes from the path, the author from the session."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=NAME_MAX)
    goal: str | None = Field(default=None, max_length=GOAL_MAX)
    starts_on: date
    ends_on: date
    cooldown_days: int | None = Field(default=None, ge=0, le=30)
    credit_budget: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=4)


class CyclePatchIn(BaseModel):
    """Edit a cycle. Only the keys sent change. ``null`` clears an optional key."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=NAME_MAX)
    goal: str | None = Field(default=None, max_length=GOAL_MAX)
    starts_on: date | None = None
    ends_on: date | None = None
    cooldown_days: int | None = Field(default=None, ge=0, le=30)
    credit_budget: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=4)


class CycleTaskIn(BaseModel):
    """Add one task to a cycle."""

    model_config = ConfigDict(extra="forbid")

    task_id: uuid.UUID


class CapacityIn(BaseModel):
    """The review minutes of one named member for the cycle."""

    model_config = ConfigDict(extra="forbid")

    member_email: str = Field(min_length=3, max_length=320)
    review_minutes: int = Field(ge=0, le=REVIEW_MINUTES_MAX)


class StartIn(BaseModel):
    """Start a cycle. ``goal`` replaces the stored goal. ``capacity`` is the
    snapshot of the start. Send ``{}`` to start with the stored goal and no
    capacity."""

    model_config = ConfigDict(extra="forbid")

    goal: str | None = Field(default=None, max_length=GOAL_MAX)
    capacity: list[CapacityIn] = Field(default_factory=list, max_length=CAPACITY_MAX)


class CloseChoiceIn(BaseModel):
    """The choice for one unfinished task: ``next`` with ``cycle_id``, or
    ``backlog`` with no cycle."""

    model_config = ConfigDict(extra="forbid")

    task_id: uuid.UUID
    choice: Literal["next", "backlog"]
    cycle_id: uuid.UUID | None = None


class CloseIn(BaseModel):
    """Close a cycle: one choice for each unfinished task that it holds."""

    model_config = ConfigDict(extra="forbid")

    choices: list[CloseChoiceIn] = Field(default_factory=list, max_length=CHOICES_MAX)


# ── Reads ───────────────────────────────────────────────────────────────────


def _member_counts_sql(vis: Any) -> str:
    """For each cycle of ``:ids``: its member tasks, and those the reader sees."""
    return (
        "SELECT ct.cycle_id, count(*) AS total,"
        f"       count(*) FILTER (WHERE {task_visibility_clause(vis)}) AS visible"
        "  FROM pm_cycle_tasks ct JOIN pm_tasks t ON t.id = ct.task_id"
        " WHERE ct.cycle_id = ANY(CAST(:ids AS uuid[]))"
        " GROUP BY ct.cycle_id"
    )


@router.get("/nodes/{project_id}/cycles", dependencies=_ENABLED)
async def list_cycles(
    project_id: str, user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """The cycles of one node, newest first. Each carries ``tasks`` (the
    member tasks that the reader sees) and ``hidden`` (the rest)."""
    pid = _uuid_or_404(project_id, "Project")
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        await load_visible_project(db, vis, pid)
        rows = (await db.execute(
            text(
                "SELECT * FROM pm_cycles WHERE project_id = CAST(:p AS uuid)"
                " ORDER BY starts_on DESC, created_at DESC, id LIMIT :lim"
            ),
            {"p": pid, "lim": LIST_LIMIT},
        )).fetchall()
        counts: dict[str, Any] = {}
        if rows:
            found = (await db.execute(
                text(_member_counts_sql(vis)),
                {"ids": [str(r.id) for r in rows], **vis.params},
            )).fetchall()
            counts = {str(c.cycle_id): c for c in found}
    out = []
    for row in rows:
        hit = counts.get(str(row.id))
        visible = int(hit.visible) if hit is not None else 0
        total = int(hit.total) if hit is not None else 0
        out.append({**_wire(row), "tasks": visible, "hidden": total - visible})
    return {"project_id": pid, "cycles": out}


@router.get("/cycles/{cycle_id}", dependencies=_ENABLED)
async def get_cycle(
    cycle_id: str, user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """One cycle: its member tasks that the reader sees, ``hidden``, the
    reader's view of the start snapshot, and the capacity snapshot."""
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        cycle = await _load_cycle(db, vis, cycle_id)
        cid = str(cycle.id)
        tasks = (await db.execute(
            text(
                "SELECT t.id, t.task_number, t.title, t.status_id, t.estimate_mins,"
                "       t.archived_at, s.name AS status_name, s.category AS status_category,"
                "       ct.added_at, ct.added_by"
                "  FROM pm_cycle_tasks ct"
                "  JOIN pm_tasks t ON t.id = ct.task_id"
                "  LEFT JOIN pm_task_statuses s ON s.id = t.status_id"
                " WHERE ct.cycle_id = CAST(:cid AS uuid)"
                f"  AND {task_visibility_clause(vis)}"
                " ORDER BY t.task_number, t.id"
            ),
            {"cid": cid, **vis.params},
        )).fetchall()
        total = int((await db.execute(
            text("SELECT count(*) FROM pm_cycle_tasks WHERE cycle_id = CAST(:cid AS uuid)"),
            {"cid": cid},
        )).scalar() or 0)
        rows = [
            {
                "id": str(t.id),
                "task_number": t.task_number,
                "title": t.title,
                "status_id": str(t.status_id) if t.status_id else None,
                "status_name": t.status_name,
                "status_category": t.status_category,
                "estimate_mins": t.estimate_mins,
                "archived": t.archived_at is not None,
                "added_at": _iso(t.added_at),
                "added_by": t.added_by,
            }
            for t in tasks
        ]
        await attach_agent_state(db, rows)
        committed = None
        if cycle.state != "planned":
            seen = (await db.execute(
                text(
                    "SELECT count(*) AS n, COALESCE(sum(c.estimate_mins), 0) AS mins"
                    "  FROM pm_cycle_committed c JOIN pm_tasks t ON t.id = c.task_id"
                    " WHERE c.cycle_id = CAST(:cid AS uuid)"
                    f"  AND {task_visibility_clause(vis)}"
                ),
                {"cid": cid, **vis.params},
            )).fetchone()
            n = int(seen.n or 0)
            committed = {
                "tasks": n,
                # A committed task that the reader cannot see, or that is gone.
                "hidden": int(cycle.committed_task_count or 0) - n,
                "estimate_mins": int(seen.mins or 0),
            }
        capacity = (await db.execute(
            text(
                "SELECT member_email, review_minutes FROM pm_cycle_capacity"
                " WHERE cycle_id = CAST(:cid AS uuid) ORDER BY member_email"
            ),
            {"cid": cid},
        )).fetchall()
    return {
        "cycle": _wire(cycle),
        "tasks": rows,
        "hidden": total - len(rows),
        "committed": committed,
        "capacity": [
            {"member_email": c.member_email, "review_minutes": c.review_minutes}
            for c in capacity
        ],
    }


# ── Plan, edit, delete ──────────────────────────────────────────────────────


@router.post("/nodes/{project_id}/cycles", status_code=201, dependencies=_ENABLED)
async def create_cycle(
    project_id: str, payload: CycleIn, user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Plan a cycle on a node that the caller can see. It starts ``planned``."""
    pid = _uuid_or_404(project_id, "Project")
    name = _clean_name(payload.name)
    _check_span(payload.starts_on, payload.ends_on)
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        node = await load_visible_project(db, vis, pid, lock_share=True)
        assert_not_archived(node, then="Plan the cycle in")
        row = (await db.execute(
            text(
                "INSERT INTO pm_cycles (project_id, name, goal, starts_on, ends_on,"
                " cooldown_days, credit_budget, created_by) "
                "VALUES (CAST(:p AS uuid), :name, :goal, :starts, :ends, :cool,"
                " :budget, :by) RETURNING *"
            ),
            {
                "p": pid, "name": name, "goal": _clean_goal(payload.goal),
                "starts": payload.starts_on, "ends": payload.ends_on,
                "cool": payload.cooldown_days, "budget": payload.credit_budget,
                "by": actor(user),
            },
        )).fetchone()
    return {"cycle": _wire(row)}


#: The columns that ``PATCH /cycles/{id}`` may write.
_EDITABLE = ("name", "goal", "starts_on", "ends_on", "cooldown_days", "credit_budget")


@router.patch("/cycles/{cycle_id}", dependencies=_ENABLED)
async def update_cycle(
    cycle_id: str, payload: CyclePatchIn,
    user: UserContext = Depends(get_current_user),
    if_match: str | None = Header(None, alias="If-Match"),
) -> dict[str, Any]:
    """Edit a planned or an active cycle. ``If-Match`` is ``updated_at``, the
    rule of a task (``core.require_precondition``). A closed cycle is 409."""
    values = clean_payload(payload)
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        cycle = await _load_cycle(db, vis, cycle_id, lock="FOR UPDATE")
        # After the visibility load, so a caller who cannot see the cycle
        # never learns from a 412 that it exists.
        require_precondition(cycle, if_match, _wire(cycle))
        if cycle.state == "closed":
            raise _refusal(409, "cycle_closed",
                           "This cycle is closed, so it does not change. Plan a new cycle.")
        if "name" in values:
            values["name"] = _clean_name(values["name"])
        if "goal" in values:
            values["goal"] = _clean_goal(values["goal"])
            if values["goal"] is None and cycle.state != "planned":
                raise _refusal(422, "goal_required",
                               "A started cycle keeps its goal. Write a new goal instead.")
        for key in ("starts_on", "ends_on"):
            if key in values and values[key] is None:
                raise _refusal(422, "bad_dates", "A cycle needs a start date and an end date.")
        _check_span(values.get("starts_on", cycle.starts_on),
                    values.get("ends_on", cycle.ends_on))
        sets = [f"{k} = :{k}" for k in _EDITABLE if k in values]
        if not sets:
            return {"cycle": _wire(cycle)}
        row = (await db.execute(
            text(
                f"UPDATE pm_cycles SET {', '.join(sets)}, updated_at = now() "
                "WHERE id = CAST(:cid AS uuid) RETURNING *"
            ),
            {"cid": str(cycle.id), **{k: values[k] for k in _EDITABLE if k in values}},
        )).fetchone()
    return {"cycle": _wire(row)}


@router.delete("/cycles/{cycle_id}", dependencies=_ENABLED)
async def delete_cycle(
    cycle_id: str, user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Delete a planned cycle. Its tasks go back to the backlog. A started
    cycle is 409: close it instead, so its record stays. A cycle that holds a
    task that the caller cannot see is 409 too, as for a close: the delete
    would move that task."""
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        cycle = await _load_cycle(db, vis, cycle_id, lock="FOR UPDATE")
        if cycle.state != "planned":
            raise _refusal(409, "cycle_started",
                           "Only a planned cycle can be deleted. Close this cycle instead, "
                           "so its record stays.")
        # FOR UPDATE above: an add waits for this delete, so the count stays true.
        counts = (await db.execute(
            text(_member_counts_sql(vis)), {"ids": [str(cycle.id)], **vis.params},
        )).fetchone()
        freed = int(counts.total) if counts is not None else 0
        hidden = freed - (int(counts.visible) if counts is not None else 0)
        if hidden:
            raise _refusal(409, "hidden_tasks",
                           "This cycle holds tasks that are hidden from you "
                           f"(hidden: {hidden}). A member who can see them must delete the "
                           "cycle.")
        await db.execute(
            text("DELETE FROM pm_cycles WHERE id = CAST(:cid AS uuid)"),
            {"cid": str(cycle.id)},
        )
    return {"deleted": str(cycle.id), "tasks_freed": freed}


# ── Membership ──────────────────────────────────────────────────────────────


async def _other_cycle_refusal(db: Any, vis: Any, task_id: str) -> HTTPException:
    """The 409 of a task that sits in another cycle. It names that cycle only
    when the caller can see its node."""
    other = (await db.execute(
        text(
            "SELECT c.id, c.name, c.project_id FROM pm_cycle_tasks ct"
            "  JOIN pm_cycles c ON c.id = ct.cycle_id"
            " WHERE ct.task_id = CAST(:t AS uuid)"
        ),
        {"t": task_id},
    )).fetchone()
    if other is not None and await _node_visible(db, vis, other.project_id):
        return _refusal(409, "task_in_cycle",
                        f'This task is in the cycle "{other.name}". Remove it from that '
                        "cycle first.")
    return _refusal(409, "task_in_cycle",
                    "This task is in another cycle. Remove it from that cycle first.")


@router.post("/cycles/{cycle_id}/tasks", dependencies=_ENABLED)
async def add_cycle_task(
    cycle_id: str, payload: CycleTaskIn, user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Add one task to a planned or an active cycle. The task must sit on the
    cycle's node or below it. A task in another cycle is 409. A task that is
    in this cycle already answers ``added: false``."""
    tid = str(payload.task_id)
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        # FOR SHARE: a start or a close of this cycle waits for this add, and
        # this add waits for them, so the state below stays true.
        cycle = await _load_cycle(db, vis, cycle_id, lock="FOR SHARE")
        if cycle.state == "closed":
            raise _refusal(409, "cycle_closed",
                           "This cycle is closed. Add the task to a planned or an active "
                           "cycle.")
        task = await load_visible_task(db, vis, tid)
        if task.archived_at is not None:
            raise _refusal(422, "task_archived",
                           "This task is archived. Unarchive it first.")
        if not await _task_in_node(db, task, str(cycle.project_id)):
            raise _refusal(422, "task_outside_node",
                           "This task is not in the project of this cycle. Move the task "
                           "there first, or plan the cycle on a node above both.")
        made = (await db.execute(
            text(
                "INSERT INTO pm_cycle_tasks (cycle_id, task_id, added_by) "
                "VALUES (CAST(:cid AS uuid), CAST(:t AS uuid), :by) "
                "ON CONFLICT (task_id) DO NOTHING RETURNING id"
            ),
            {"cid": str(cycle.id), "t": tid, "by": actor(user)},
        )).fetchone()
        if made is None:
            here = (await db.execute(
                text(
                    "SELECT 1 FROM pm_cycle_tasks WHERE task_id = CAST(:t AS uuid)"
                    " AND cycle_id = CAST(:cid AS uuid)"
                ),
                {"t": tid, "cid": str(cycle.id)},
            )).fetchone()
            if here is None:
                raise await _other_cycle_refusal(db, vis, tid)
    return {"cycle_id": str(cycle.id), "task_id": tid, "added": made is not None}


@router.delete("/cycles/{cycle_id}/tasks/{task_id}", dependencies=_ENABLED)
async def remove_cycle_task(
    cycle_id: str, task_id: str, user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Take one task out of a cycle. The task goes back to the backlog.

    A closed cycle keeps each finished task as its record (409). A task that
    is open again may leave a closed cycle, so it can join a new one.
    """
    tid = _uuid_or_404(task_id, "Task")
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        cycle = await _load_cycle(db, vis, cycle_id, lock="FOR SHARE")
        await load_visible_task(db, vis, tid)
        if cycle.state == "closed":
            category = (await db.execute(
                text(
                    "SELECT s.category FROM pm_tasks t"
                    "  LEFT JOIN pm_task_statuses s ON s.id = t.status_id"
                    " WHERE t.id = CAST(:t AS uuid)"
                ),
                {"t": tid},
            )).scalar()
            if category in CLOSING_CATEGORIES:
                raise _refusal(409, "cycle_closed",
                               "This task is finished in a closed cycle, so it stays there "
                               "as the record of that cycle.")
        gone = (await db.execute(
            text(
                "DELETE FROM pm_cycle_tasks WHERE cycle_id = CAST(:cid AS uuid)"
                " AND task_id = CAST(:t AS uuid) RETURNING id"
            ),
            {"cid": str(cycle.id), "t": tid},
        )).fetchone()
        if gone is None:
            raise _refusal(404, "not_in_cycle", "This task is not in this cycle.")
    return {"cycle_id": str(cycle.id), "task_id": tid, "removed": True}


# ── A task that moves away ──────────────────────────────────────────────────

#: The body of a task that left its cycle because it moved. It names no
#: cycle, for the reason of ``_TRANSFER_BODY``.
MOVED_BODY = "Left its cycle, because the task moved out of the project of the cycle."

#: The rows of open cycles that hold one of the tasks ``:ids``. The caller
#: checks each row with the one ancestor walk (``_IN_NODE_SQL``).
_OPEN_ROWS_OF_TASKS_SQL = (
    "SELECT ct.cycle_id, ct.task_id, t.project_id, c.project_id AS cycle_node"
    "  FROM pm_cycle_tasks ct"
    "  JOIN pm_cycles c ON c.id = ct.cycle_id"
    "  JOIN pm_tasks t ON t.id = ct.task_id"
    " WHERE ct.task_id = ANY(CAST(:ids AS uuid[])) AND c.state <> 'closed'"
    " ORDER BY ct.task_id"
)

#: The rows of open cycles that a move of the node ``:node`` takes out of
#: their node. Each task of the subtree is below the moved node. A cycle on a
#: node outside the subtree holds the task only when that node is in
#: ``chain``: the moved node and each node above its new place. The walk up
#: is the one ancestor chain of ``core``.
_OPEN_ROWS_LEFT_BY_NODE_SQL = _ancestor_chain_sql("id = CAST(:node AS uuid)") + (
    ", sub AS ("
    "    SELECT id FROM pm_projects WHERE id = CAST(:node AS uuid)"
    "    UNION ALL"
    "    SELECT p.id FROM pm_projects p JOIN sub s ON p.parent_project_id = s.id"
    ")"
    " SELECT ct.cycle_id, ct.task_id, t.project_id, c.project_id AS cycle_node"
    "  FROM pm_cycle_tasks ct"
    "  JOIN pm_cycles c ON c.id = ct.cycle_id"
    "  JOIN pm_tasks t ON t.id = ct.task_id"
    " WHERE c.state <> 'closed'"
    "   AND t.project_id IN (SELECT id FROM sub)"
    "   AND c.project_id NOT IN (SELECT id FROM sub)"
    "   AND c.project_id NOT IN (SELECT id FROM chain)"
    " ORDER BY ct.task_id"
)


async def detach_moved_tasks(
    db: Any, task_ids: Iterable[Any] = (), *, by: str, node_id: str | None = None,
) -> int:
    """Take each moved task out of an open cycle whose node no longer holds it.

    The callers are the moves of a task (``POST /tasks/move``,
    ``tasks.move_task_in``) with ``task_ids``, and the move of a project
    (``POST /nodes/{id}/move``) with ``node_id``. Each calls it after its own
    write, inside its own transaction. The move has a lock on each task row,
    so a close of the cycle waits for the move, or the move waits for it.

    It writes one ``cycle_transfer`` activity for each row that it removes.
    A closed cycle keeps its rows, and the start snapshot does not change. It
    reads no flag: a row that stays after a move blocks the task for good
    (§13.2a). Returns the count of rows that it removed.
    """
    if node_id is not None:
        rows = (await db.execute(
            text(_OPEN_ROWS_LEFT_BY_NODE_SQL), {"node": str(node_id)},
        )).fetchall()
    else:
        ids = sorted({str(t) for t in task_ids})
        if not ids:
            return 0
        found = (await db.execute(text(_OPEN_ROWS_OF_TASKS_SQL), {"ids": ids})).fetchall()
        rows = [r for r in found if not await _task_in_node(db, r, str(r.cycle_node))]
    removed = 0
    for row in rows:
        cid, tid = str(row.cycle_id), str(row.task_id)
        gone = (await db.execute(
            text(
                "DELETE FROM pm_cycle_tasks WHERE cycle_id = CAST(:cid AS uuid)"
                " AND task_id = CAST(:t AS uuid) RETURNING id"
            ),
            {"cid": cid, "t": tid},
        )).fetchone()
        if gone is None:
            continue
        await record_activity(
            db, activity_type=TRANSFER_TYPE, created_by=by, task_id=tid, body=MOVED_BODY,
            meta={"from_cycle": cid, "to_cycle": None, "cause": MOVED_CAUSE},
        )
        removed += 1
    return removed


# ── Start ───────────────────────────────────────────────────────────────────


def _active_refusal(name: str | None) -> HTTPException:
    named = f' "{name}"' if name else ""
    return _refusal(409, "cycle_active",
                    f"Another cycle{named} is active on this node. Close it before you "
                    "start this one.")


async def _active_on_node(db: Any, node_id: str, cycle_id: str) -> Any:
    return (await db.execute(
        text(
            "SELECT id, name FROM pm_cycles WHERE project_id = CAST(:p AS uuid)"
            " AND state = 'active' AND id <> CAST(:cid AS uuid) LIMIT 1"
        ),
        {"p": node_id, "cid": cycle_id},
    )).fetchone()


async def _check_capacity(
    db: Any, organization_id: str, capacity: list[CapacityIn],
) -> list[tuple[str, int]]:
    """The capacity rows, each an active member of the cycle's org, each once.

    ``organization_id`` is the cycle row's own org, never request input. RLS
    binds the same org. The filter keeps the answer right where RLS is off.
    """
    rows: dict[str, int] = {}
    for item in capacity:
        email = item.member_email.strip().lower()
        if not email or "@" not in email or email.startswith("agent:"):
            raise _refusal(422, "bad_capacity",
                           "Name each person of the capacity by the email address of a "
                           "member.")
        if email in rows:
            raise _refusal(422, "bad_capacity",
                           f"{email} is in the capacity twice. Name each person once.")
        rows[email] = item.review_minutes
    if rows:
        known = {
            str(r.email) for r in (await db.execute(
                text(
                    "SELECT lower(email) AS email FROM app_user"
                    " WHERE lower(email) = ANY(:emails) AND status = 'active'"
                    " AND organization_id = CAST(:org AS uuid)"
                ),
                {"emails": sorted(rows), "org": organization_id},
            )).fetchall()
        }
        unknown = sorted(set(rows) - known)
        if unknown:
            raise _refusal(422, "bad_capacity",
                           "These people are not members of this organization: "
                           f"{', '.join(unknown)}. Name members only.")
    return sorted(rows.items())


@router.post("/cycles/{cycle_id}/start", dependencies=_ENABLED)
async def start_cycle(
    cycle_id: str, payload: StartIn, user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Start a planned cycle.

    It needs a goal. It stores the committed snapshot (each member task and
    its estimate, and the two totals) and the capacity snapshot. A second
    active cycle on the node is 409. The unique index holds the same rule
    under a race.
    """
    me = actor(user)
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        cycle = await _load_cycle(db, vis, cycle_id, lock="FOR UPDATE")
        if cycle.state == "active":
            raise _refusal(409, "cycle_started", "This cycle has already started.")
        if cycle.state == "closed":
            raise _refusal(409, "cycle_closed", "This cycle is closed. Plan a new cycle.")
        node = await load_visible_project(db, vis, str(cycle.project_id))
        assert_not_archived(node, then="Start the cycle in")
        goal = _clean_goal(payload.goal if payload.goal is not None else cycle.goal)
        if goal is None:
            raise _refusal(422, "goal_required",
                           "A cycle needs a goal before it starts. Write one sentence that "
                           "says what the cycle is for.")
        capacity = await _check_capacity(db, str(cycle.organization_id), payload.capacity)
        cid, node_id = str(cycle.id), str(cycle.project_id)
        other = await _active_on_node(db, node_id, cid)
        if other is not None:
            raise _active_refusal(other.name)
        # The committed snapshot: every member task, the hidden ones too, so
        # the stored total is the full total (§13.7). `pm_tasks.estimate_mins`
        # has no CHECK, and a member can save a negative estimate. The snapshot
        # refuses one, so a negative estimate counts as no estimate.
        await db.execute(
            text(
                "INSERT INTO pm_cycle_committed (cycle_id, task_id, estimate_mins) "
                "SELECT ct.cycle_id, ct.task_id,"
                "       CASE WHEN t.estimate_mins >= 0 THEN t.estimate_mins END"
                "  FROM pm_cycle_tasks ct JOIN pm_tasks t ON t.id = ct.task_id"
                " WHERE ct.cycle_id = CAST(:cid AS uuid)"
            ),
            {"cid": cid},
        )
        totals = (await db.execute(
            text(
                "SELECT count(*) AS n, COALESCE(sum(estimate_mins), 0) AS mins"
                "  FROM pm_cycle_committed WHERE cycle_id = CAST(:cid AS uuid)"
            ),
            {"cid": cid},
        )).fetchone()
        try:
            async with _savepoint(db):
                row = (await db.execute(
                    text(
                        "UPDATE pm_cycles SET state = 'active', goal = :goal,"
                        " started_at = now(), started_by = :me,"
                        " committed_task_count = :n, committed_estimate_mins = :mins,"
                        " updated_at = now() "
                        "WHERE id = CAST(:cid AS uuid) RETURNING *"
                    ),
                    {"goal": goal, "me": me, "n": int(totals.n), "mins": int(totals.mins),
                     "cid": cid},
                )).fetchone()
        except IntegrityError as exc:
            if ONE_ACTIVE_INDEX not in str(exc.orig):
                raise
            # A start on the same node won the race. Name it if it is there.
            winner = await _active_on_node(db, node_id, cid)
            raise _active_refusal(winner.name if winner is not None else None) from None
        for email, minutes in capacity:
            await db.execute(
                text(
                    "INSERT INTO pm_cycle_capacity (cycle_id, member_email, review_minutes) "
                    "VALUES (CAST(:cid AS uuid), :email, :minutes)"
                ),
                {"cid": cid, "email": email, "minutes": minutes},
            )
        # The caller's own count, as `get_cycle` gives it (§13.7). The stored
        # total counts the tasks that the caller cannot see too.
        seen = int((await db.execute(
            text(
                "SELECT count(*) FROM pm_cycle_committed c JOIN pm_tasks t ON t.id = c.task_id"
                " WHERE c.cycle_id = CAST(:cid AS uuid)"
                f"  AND {task_visibility_clause(vis)}"
            ),
            {"cid": cid, **vis.params},
        )).scalar() or 0)
    return {"cycle": _wire(row), "committed_tasks": seen, "hidden": int(totals.n) - seen}


# ── Close ───────────────────────────────────────────────────────────────────

#: The body of a logged move. The meta holds the ids. The body names no
#: cycle, because a reader of the task may not see the node of the cycle.
_TRANSFER_BODY = {
    "next": "Carried over to the next cycle when the cycle closed.",
    "backlog": "Moved to the backlog when the cycle closed.",
}


async def _lock_cycles(db: Any, vis: Any, ids: list[str]) -> dict[str, Any]:
    """Lock the cycles ``ids`` that the caller can see, FOR UPDATE, in ONE
    statement ordered by id. Returns each locked row by its id.

    The close locks the closing cycle and each next cycle here, before any
    other work. So two closes take their cycle locks in one order: a close of
    A that carries to B and a close of B that carries to A wait for each
    other, and never deadlock. The rows that come back are the rows that the
    lock holds, so their state is current.
    """
    rows = (await db.execute(
        text(
            "SELECT c.* FROM pm_cycles c WHERE c.id = ANY(CAST(:ids AS uuid[]))"
            f" AND {vis.project_clause('c.project_id')}"
            " ORDER BY c.id FOR UPDATE OF c"
        ),
        {"ids": sorted(set(ids)), **vis.params},
    )).fetchall()
    return {str(r.id): r for r in rows}


async def _check_targets(
    db: Any, closing: Any, choices: list[CloseChoiceIn], tasks: dict[str, Any],
    locked: dict[str, Any],
) -> None:
    """Each ``next`` cycle: not this one, on a node that the caller sees, not
    closed, and on a node that holds the task. ``locked`` holds the rows that
    :func:`_lock_cycles` locked, so this takes no lock of its own."""
    targets = sorted({str(c.cycle_id) for c in choices if c.choice == "next"})
    for target in targets:
        if target == str(closing.id):
            raise _refusal(422, "bad_choice",
                           "A task cannot carry over to the cycle that closes. Pick the next "
                           "cycle, or the backlog.")
        row = locked.get(target)
        if row is None:
            raise _refusal(422, "bad_choice",
                           "The next cycle is not found. Pick a planned or an active cycle "
                           "that you can see.")
        if row.state == "closed":
            raise _refusal(422, "bad_choice",
                           f'The cycle "{row.name}" is closed. Pick a planned or an active '
                           "cycle.")
    for choice in choices:
        if choice.choice != "next":
            continue
        target = locked[str(choice.cycle_id)]
        if not await _task_in_node(db, tasks[str(choice.task_id)], str(target.project_id)):
            raise _refusal(422, "bad_choice",
                           f'A task is not in the project of the cycle "{target.name}". '
                           "Pick another cycle for it, or the backlog.")


@router.post("/cycles/{cycle_id}/close", dependencies=_ENABLED)
async def close_cycle(
    cycle_id: str, payload: CloseIn, user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Close an active cycle.

    Each unfinished task (its status is not done or cancelled) needs one
    choice: ``next`` with a cycle id, or ``backlog``. The close moves each
    membership row and writes one ``cycle_transfer`` activity on the task. A
    finished task stays in the closed cycle as its record. An archived task
    (a merged task too) stays there as well, needs no choice, and refuses
    one (422 ``task_archived``, the rule of the add). The snapshot of the
    start does not change. An unfinished task that the caller cannot see
    refuses the whole close (409): nothing moves.
    """
    me = actor(user)
    cid = _uuid_or_404(cycle_id, "Cycle")
    targets = [str(c.cycle_id) for c in payload.choices
               if c.choice == "next" and c.cycle_id is not None]
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        # Before any other work: the closing cycle and each next cycle, in one
        # lock order (`_lock_cycles`).
        locked = await _lock_cycles(db, vis, [cid, *targets])
        cycle = locked.get(cid)
        if cycle is None:
            raise HTTPException(status_code=404, detail="Cycle not found")
        if cycle.state == "planned":
            raise _refusal(409, "cycle_not_started",
                           "This cycle has not started. Start it, or delete it.")
        if cycle.state == "closed":
            raise _refusal(409, "cycle_closed", "This cycle is already closed.")
        # FOR SHARE OF t, in id order: a delete or a move of a member task
        # waits for this close, or this close waits for it and then reads
        # what it left. A task deleted first is not here.
        members = (await db.execute(
            text(
                "SELECT t.id, t.project_id, t.archived_at, s.category,"
                f"       ({task_visibility_clause(vis)}) AS visible"
                "  FROM pm_cycle_tasks ct"
                "  JOIN pm_tasks t ON t.id = ct.task_id"
                "  LEFT JOIN pm_task_statuses s ON s.id = t.status_id"
                " WHERE ct.cycle_id = CAST(:cid AS uuid)"
                " ORDER BY t.id FOR SHARE OF t"
            ),
            {"cid": cid, **vis.params},
        )).fetchall()
        # An archived task stays as part of the record, so it needs no choice.
        archived = {str(m.id) for m in members if m.archived_at is not None}
        unfinished = {
            str(m.id): m for m in members
            if m.category not in CLOSING_CATEGORIES and m.archived_at is None
        }
        hidden = sum(1 for m in unfinished.values() if not m.visible)
        if hidden:
            raise _refusal(409, "hidden_tasks",
                           "This cycle has unfinished tasks that are hidden from you "
                           f"(hidden: {hidden}). A member who can see them must close the "
                           "cycle.")
        chosen: dict[str, CloseChoiceIn] = {}
        for choice in payload.choices:
            tid = str(choice.task_id)
            if tid in chosen:
                raise _refusal(422, "bad_choice", "A task has two choices. Give each task one.")
            if tid in archived:
                raise _refusal(422, "task_archived",
                               "This task is archived. Unarchive it first, or send no choice "
                               "for it: it stays in this cycle as part of its record.")
            if tid not in unfinished:
                raise _refusal(422, "bad_choice",
                               "A choice names a task that is not an unfinished task of this "
                               "cycle. Send choices for its unfinished tasks only.")
            if choice.choice == "next" and choice.cycle_id is None:
                raise _refusal(422, "bad_choice",
                               "A choice of next needs the id of the next cycle.")
            if choice.choice == "backlog" and choice.cycle_id is not None:
                raise _refusal(422, "bad_choice",
                               "A choice of backlog takes no cycle id.")
            chosen[tid] = choice
        missing = len(set(unfinished) - set(chosen))
        if missing:
            raise _refusal(422, "choices_missing",
                           "Choose the next cycle or the backlog for each unfinished task. "
                           f"Tasks with no choice: {missing}.")
        await _check_targets(db, cycle, list(chosen.values()), unfinished, locked)
        moved = {"next": 0, "backlog": 0}
        for tid in sorted(chosen):
            choice = chosen[tid]
            to = str(choice.cycle_id) if choice.choice == "next" else None
            if to is not None:
                hit = (await db.execute(
                    text(
                        "UPDATE pm_cycle_tasks SET cycle_id = CAST(:to AS uuid),"
                        " added_at = now(), added_by = :me"
                        " WHERE cycle_id = CAST(:cid AS uuid) AND task_id = CAST(:t AS uuid)"
                        " RETURNING id"
                    ),
                    {"to": to, "me": me, "cid": cid, "t": tid},
                )).fetchone()
            else:
                hit = (await db.execute(
                    text(
                        "DELETE FROM pm_cycle_tasks WHERE cycle_id = CAST(:cid AS uuid)"
                        " AND task_id = CAST(:t AS uuid) RETURNING id"
                    ),
                    {"cid": cid, "t": tid},
                )).fetchone()
            if hit is None:
                # The row left this cycle while the close ran. No move, so no
                # activity, which would name a task that may be gone.
                continue
            await record_activity(
                db, activity_type=TRANSFER_TYPE, created_by=me, task_id=tid,
                body=_TRANSFER_BODY[choice.choice],
                meta={"from_cycle": cid, "to_cycle": to, "choice": choice.choice},
            )
            moved[choice.choice] += 1
        row = (await db.execute(
            text(
                "UPDATE pm_cycles SET state = 'closed', closed_at = now(), closed_by = :me,"
                " updated_at = now() WHERE id = CAST(:cid AS uuid) RETURNING *"
            ),
            {"me": me, "cid": cid},
        )).fetchone()
    return {"cycle": _wire(row), "moved": moved}


__all__ = [
    "CLOSE_CHOICES",
    "CYCLE_STATES",
    "MOVED_CAUSE",
    "TRANSFER_TYPE",
    "detach_moved_tasks",
    "require_sprints",
    "sprints_allowed",
]
