"""Projects · a person accepts agent work before it is done (WS-MCP SW-18a,
Metorite task 349).

Spec: ``project-docs/specs/agent_coordination.md`` SW-18a.

    PUT  /projects/nodes/{id}/agent-review   → the setting of one project
    POST /projects/tasks/{id}/agent-review   → a person approves the work

**The setting.** ``pm_projects.agent_work_needs_review`` (default false) and
``pm_projects.agent_review_default_reviewer`` (migration
``*_pm_agent_review.sql``). Only the PUT writes them, and only a lead of the
project or of an ancestor, or a Projects admin, may
(``authority.assert_may_set_agent_review``). The setting of the task's OWN
project counts. A parent's setting does not reach a child project.

**The redirect.** When a SIGNED agent (``UserContext.agent``) moves a task of
such a project into a ``done`` lane, the move lands in the review lane of the
task's own status set instead (:func:`review_lane`), and the task records the
reviewer and the time (``pm_tasks.agent_reviewer``,
``pm_tasks.agent_review_requested_at``). A person's move is never redirected.
Two doors call :func:`redirect_for`: the PATCH of a task (``tasks.py``, which
serves ``set_task_status``, ``update_task`` and ``update_tasks``) and the report
of a Check (``checks.py``). Every other door that moves a status is off the MCP
edge, so a signed agent cannot reach it (``agent_oauth.MCP_WRITE_ROUTES``).

**The review lane.** The first status of category ``in_progress`` in the
task's own status set whose name holds ``review`` in any case, in lane order
(``core.status_order_sql``). This is the convention that the MCP routine
already teaches (``agent_edge/prompts.py``). With no such lane the setting does
nothing: the agent's done goes to done as before, and the answer says so.

**The approve.** The named reviewer, or a lead of the project or of an
ancestor, approves (``authority.assert_may_approve_agent_work``). Everybody
else gets 403. A signed agent and a run with no person present get 403 before
any read. The approve clears the wait in ONE conditional UPDATE, so a second
approve finds nothing to approve (409). Then it moves the task to the first
``done`` lane of its set and closes the open agent run of the task
(``agent_runs.close_reviewed_run``), in the same transaction.

**A wait ends on any other move.** A status move through the PATCH that is not
the redirect clears the wait. So a person who moves the task by hand, to done
or back to work, decides the review too.

Dark: while ``agent_review_enabled`` is off (``agent_oauth.agent_review_enabled``,
the one reader), both routes answer 404 and :func:`redirect_for` returns None.
Fence: ``tests/unit/test_pm_agent_review_r8.py``.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any, Literal

from acb_auth import UserContext, get_current_user
from acb_auth.agent_oauth import agent_review_enabled
from acb_common.no_person import NO_PERSON_PRESENT, NO_PERSON_TEXT, no_person_present
from fastapi import Depends, Header, HTTPException
from gateway.routes.projects.authority import (
    assert_may_approve_agent_work,
    assert_may_set_agent_review,
)
from gateway.routes.projects.core import (
    CLOSING_CATEGORIES,
    COMPLETED_CATEGORY,
    STARTED_CATEGORY,
    ProjectModel,
    TaskModel,
    _tenant_session,
    actor,
    apply_status_transition,
    emit,
    load_default_status,
    load_visible_project,
    load_visible_task,
    record_activity,
    record_field_change,
    require_precondition,
    require_row,
    resolve_visibility,
    resolve_visibility_for,
    router,
    row_to_dict,
    status_order_sql,
    status_owner_id,
    transient_as_409,
    wire,
)
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

#: The word that names a review lane, matched in any case.
REVIEW_WORD = "review"

#: The outcome of an agent's done while the setting is on: the move landed in
#: the review lane, or the set has no review lane and the move went to done.
OUTCOME_REVIEW_REQUESTED = "review_requested"
OUTCOME_NO_REVIEW_LANE = "no_review_lane"

#: The words of each outcome, for the answer of the PATCH.
REVIEW_REQUESTED_NOTE = (
    "This project needs a person to accept agent work. The task waits in its "
    "review status until the reviewer or a project lead approves it."
)
NO_REVIEW_LANE_NOTE = (
    "This project needs a person to accept agent work, but its statuses have no "
    "in progress status whose name says review. So the task moved to done. Add "
    "such a status to make the review work."
)

#: The detail codes of the approve.
NOT_WAITING = "not_waiting"
NO_DONE_STATUS = "no_done_status"
#: The 403 of a signed agent on either route.
AGENT_REFUSED = "A person decides this. An agent connection cannot."
#: The 422 of a default reviewer who cannot open the project.
REVIEWER_CANNOT_SEE = (
    "The reviewer must be a member of this organization who can open this project."
)
#: The longest address that a reviewer may have.
MAX_REVIEWER_CHARS = 320
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+$")

#: The body of the system line of a request, and of an approve.
REQUESTED_BODY = "The agent's work waits for review."
APPROVED_BODY = "A person approved the agent's work."


def review_on() -> bool:
    """The flag ``agent_review_enabled`` (default OFF, SW-18a), through its one
    reader ``agent_oauth.agent_review_enabled``."""
    return agent_review_enabled()


async def require_review() -> None:
    """404 while the review flag is off. A route dependency, so it answers
    before the body is read, as ``checks.require_checks`` does."""
    if not review_on():
        raise HTTPException(status_code=404, detail="Not Found")


def _refuse_agent_or_absent_person(user: UserContext) -> None:
    """A person decides both acts. A signed agent gets 403, and so does a run
    with no person present (AUTO-7a), before any read."""
    if getattr(user, "agent", None) is not None:
        raise HTTPException(status_code=403, detail=AGENT_REFUSED)
    if no_person_present():
        raise HTTPException(status_code=403, detail={
            "error": NO_PERSON_PRESENT, "message": NO_PERSON_TEXT,
        })


def _uuid_or_none(value: Any) -> str | None:
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError):
        return None


# ── The review lane ─────────────────────────────────────────────────────────

_REVIEW_LANE_SQL = (
    "SELECT * FROM pm_task_statuses"
    " WHERE project_id = CAST(:owner AS uuid) AND category = :started"
    "   AND strpos(lower(name), :word) > 0"
    f" ORDER BY {status_order_sql()} LIMIT 1"
)


async def review_lane_of_set(db: Any, owner_id: str) -> Any | None:
    """The review lane of the status set that *owner_id* owns, or ``None``."""
    return (await db.execute(text(_REVIEW_LANE_SQL), {
        "owner": owner_id, "started": STARTED_CATEGORY, "word": REVIEW_WORD,
    })).fetchone()


async def review_lane(db: Any, task: Any) -> Any | None:
    """The review lane of the task's own status set, or ``None``."""
    return await review_lane_of_set(db, await status_owner_id(db, str(task.project_id)))


# ── The redirect of an agent's done ─────────────────────────────────────────


@dataclass(frozen=True)
class Redirect:
    """An agent's done under the setting. ``lane`` is the review lane, or
    ``None`` when the set has none and the move goes to done."""

    lane: Any | None
    reviewer: str | None

    @property
    def outcome(self) -> str:
        return OUTCOME_REVIEW_REQUESTED if self.lane is not None else OUTCOME_NO_REVIEW_LANE

    def wire(self) -> dict[str, Any]:
        """The ``agent_review`` key of the answer of the PATCH."""
        return {
            "outcome": self.outcome,
            "status_id": str(self.lane.id) if self.lane is not None else None,
            "reviewer": self.reviewer,
            "note": (REVIEW_REQUESTED_NOTE if self.lane is not None
                     else NO_REVIEW_LANE_NOTE),
        }


_TARGET_SQL = (
    "SELECT * FROM pm_task_statuses"
    " WHERE id = CAST(:sid AS uuid) AND project_id = CAST(:owner AS uuid)"
)
_CATEGORY_SQL = "SELECT category FROM pm_task_statuses WHERE id = CAST(:sid AS uuid)"
_SETTING_SQL = (
    "SELECT agent_work_needs_review, agent_review_default_reviewer"
    " FROM pm_projects WHERE id = CAST(:pid AS uuid)"
)


async def redirect_for(db: Any, user: UserContext, task: Any, target_status_id: Any
                       ) -> Redirect | None:
    """The redirect of a move of *task* to *target_status_id*, or ``None``.

    ``None`` when the rule does not apply: the flag is off, the caller is a
    person, the target is not a ``done`` lane of the task's own set, the task
    is closed already, or the task's project does not need review. A target that is not in the set is
    left to ``apply_status_transition``, which refuses it as before.
    """
    if not review_on() or getattr(user, "agent", None) is None:
        return None
    sid = _uuid_or_none(target_status_id)
    if sid is None:
        return None
    owner = await status_owner_id(db, str(task.project_id))
    target = (await db.execute(text(_TARGET_SQL), {"sid": sid, "owner": owner})).fetchone()
    if target is None or target.category != COMPLETED_CATEGORY:
        return None
    # A task that is closed already stays where it is: a repeated done never
    # opens it again into review.
    current = (await db.execute(text(_CATEGORY_SQL), {"sid": str(task.status_id)})).fetchone()
    if current is not None and current.category in CLOSING_CATEGORIES:
        return None
    setting = (await db.execute(text(_SETTING_SQL), {"pid": str(task.project_id)})).fetchone()
    if setting is None or not setting.agent_work_needs_review:
        return None
    reviewer = str(setting.agent_review_default_reviewer or "").strip().lower() or None
    return Redirect(lane=await review_lane_of_set(db, owner), reviewer=reviewer)


async def review_target(db: Any, user: UserContext, task: Any, new_status: Any
                        ) -> tuple[Redirect | None, Any]:
    """``(redirect, status)`` for a PATCH that may move *task* to *new_status*.

    The status is the review lane when the redirect has one, else
    *new_status* as it came. ``(None, new_status)`` when the rule does not
    apply, so a person's PATCH and a PATCH with no status are unchanged.
    """
    if new_status is None:
        return None, None
    redirect = await redirect_for(db, user, task, new_status)
    if redirect is not None and redirect.lane is not None:
        return redirect, str(redirect.lane.id)
    return redirect, new_status


def attach_review_outcome(row: dict[str, Any], redirect: Redirect | None) -> None:
    """Put ``agent_review`` on the answer of a PATCH, only when the rule ran."""
    if redirect is not None:
        row["agent_review"] = redirect.wire()


_REQUEST_SQL = (
    "UPDATE pm_tasks SET agent_reviewer = :reviewer, agent_review_requested_at = now(),"
    " updated_at = now()"
    " WHERE id = CAST(:tid AS uuid) AND agent_review_requested_at IS NULL"
    " RETURNING id"
)
_CLEAR_SQL = (
    "UPDATE pm_tasks SET agent_reviewer = NULL, agent_review_requested_at = NULL,"
    " updated_at = now()"
    " WHERE id = CAST(:tid AS uuid) AND agent_review_requested_at IS NOT NULL"
    " RETURNING id"
)


async def record_request(db: Any, user: UserContext, task_id: str, redirect: Redirect
                         ) -> bool:
    """Start the wait on *task_id*, once, with one system line. A wait that
    already holds keeps its reviewer and its time. True when it wrote."""
    if redirect.lane is None:
        return False
    started = (await db.execute(text(_REQUEST_SQL), {
        "tid": task_id, "reviewer": redirect.reviewer,
    })).fetchone()
    if started is None:
        return False
    await record_activity(
        db, activity_type="system", created_by=actor(user), task_id=task_id,
        body=REQUESTED_BODY,
        meta={"agent_review": OUTCOME_REVIEW_REQUESTED, "reviewer": redirect.reviewer},
    )
    return True


async def settle_after_move(db: Any, user: UserContext, task_id: str, before: Any,
                            redirect: Redirect | None, moved: Any) -> bool:
    """The wait after a status move of the PATCH or of a Check report.

    A redirect into the review lane starts the wait. Any other move ends a
    wait that holds, whoever moved the task. True when it wrote, so the
    caller reads the row again for its version.
    """
    if redirect is not None and redirect.lane is not None:
        return await record_request(db, user, task_id, redirect)
    if moved is None or getattr(before, "agent_review_requested_at", None) is None:
        return False
    return (await db.execute(text(_CLEAR_SQL), {"tid": task_id})).fetchone() is not None


#: The detail codes of a signed agent's move that would leave the review.
MOVE_WAITING = "review_waiting"
MOVE_NEEDS_REVIEW = "review_needed"


async def refuse_agent_move(db: Any, user: UserContext, task: Any, dest_project_id: Any,
                            landing_category: str | None) -> None:
    """A signed agent's bulk move keeps the review (SW-18a, ``move.move_tasks``).

    A move with a ``status_map`` is a status move too, so an agent could land
    its task in a ``done`` lane with no review. While the flag is on, a
    signed agent gets 403 for a task that waits for review, and for a landing
    in ``done`` when the source or the destination project needs review. A
    person's move is unchanged.
    """
    if not review_on() or getattr(user, "agent", None) is None:
        return
    if getattr(task, "agent_review_requested_at", None) is not None:
        raise HTTPException(status_code=403, detail={
            "error": MOVE_WAITING,
            "message": "This task waits for a person to review the agent's work. "
                       "A person moves it.",
        })
    if landing_category != COMPLETED_CATEGORY:
        return
    for pid in sorted({str(task.project_id), str(dest_project_id)}):
        setting = (await db.execute(text(_SETTING_SQL), {"pid": pid})).fetchone()
        if setting is not None and setting.agent_work_needs_review:
            raise HTTPException(status_code=403, detail={
                "error": MOVE_NEEDS_REVIEW,
                "message": "A person accepts agent work in this project. Move the "
                           "task to an open status, then set its status to done.",
            })


async def settle_review(db: Any, user: UserContext, task_id: str, before: Any,
                        redirect: Redirect | None, moved: Any, row: Any) -> Any:
    """:func:`settle_after_move`, then the task row for the answer: read again
    when the wait changed, so the answer carries the version that counts,
    else *row* as it came."""
    if await settle_after_move(db, user, task_id, before, redirect, moved):
        return await require_row(db, "pm_tasks", task_id, "Task")
    return row


# ── PUT /nodes/{id}/agent-review ────────────────────────────────────────────


class AgentReviewSettingIn(BaseModel):
    """The body of ``PUT /nodes/{id}/agent-review``. No other key."""

    model_config = ConfigDict(extra="forbid")

    needs_review: bool
    default_reviewer: str | None = Field(default=None, max_length=MAX_REVIEWER_CHARS)


def _clean_reviewer(value: str | None) -> str | None:
    cleaned = str(value or "").strip().lower()
    if not cleaned:
        return None
    if "\x00" in cleaned or not _EMAIL.fullmatch(cleaned):
        raise HTTPException(status_code=422, detail="default_reviewer is an email address.")
    return cleaned


async def _reviewer_sees(db: Any, reviewer: str, project_id: str) -> bool:
    """True when *reviewer* is an active member of this tenant who opens the
    project. The visibility of ``core.resolve_visibility_for``, the seam that
    the bell reads for a person who is not the caller."""
    vis = await resolve_visibility_for(db, reviewer)
    if not vis.organization_id:
        return False
    row = (await db.execute(
        text(
            "SELECT 1 FROM pm_projects p WHERE p.id = CAST(:pid AS uuid)"
            " AND p.organization_id = CAST(:vis_org AS uuid)"
            f" AND {vis.project_clause('p.id')} LIMIT 1"
        ),
        {"pid": project_id, **vis.params},
    )).fetchone()
    return row is not None


def _setting_out(row: Any, lane: Any | None) -> dict[str, Any]:
    on = bool(getattr(row, "agent_work_needs_review", False))
    return {
        "project_id": str(row.id),
        "needs_review": on,
        "default_reviewer": getattr(row, "agent_review_default_reviewer", None),
        "review_status": ({"id": str(lane.id), "name": lane.name} if lane is not None
                          else None),
        "note": NO_REVIEW_LANE_NOTE if on and lane is None else None,
        "version": wire(row.updated_at),
    }


@router.put("/nodes/{project_id}/agent-review", dependencies=[Depends(require_review)])
@transient_as_409
async def set_agent_review(
    project_id: str, payload: AgentReviewSettingIn,
    user: UserContext = Depends(get_current_user),
) -> dict:
    """Turn "Agent work needs review" on or off for one project (SW-18a).

    The order: the flag (404), a person (403), the visibility (404), the
    authority of a grant write (403), the reviewer (422), then the write. The
    answer names the review lane that the redirect uses, or says that the
    project has none.
    """
    _refuse_agent_or_absent_person(user)
    reviewer = _clean_reviewer(payload.default_reviewer)
    if _uuid_or_none(project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        before = await load_visible_project(db, vis, project_id)
        await assert_may_set_agent_review(db, user, before)
        if reviewer is not None and not await _reviewer_sees(db, reviewer, project_id):
            raise HTTPException(status_code=422, detail=REVIEWER_CANNOT_SEE)
        after = (await db.execute(text(
            "UPDATE pm_projects SET agent_work_needs_review = :on,"
            " agent_review_default_reviewer = :reviewer, updated_at = now()"
            " WHERE id = CAST(:pid AS uuid) RETURNING *"),
            {"on": payload.needs_review, "reviewer": reviewer, "pid": project_id},
        )).fetchone()
        changes = [
            {"field": field, "old": getattr(before, field, None), "new": getattr(after, field)}
            for field in ("agent_work_needs_review", "agent_review_default_reviewer")
            if getattr(before, field, None) != getattr(after, field)
        ]
        if changes:
            await record_field_change(
                db, created_by=actor(user), project_id=project_id, changes=changes)
        answer = _setting_out(after, await review_lane_of_set(
            db, await status_owner_id(db, project_id)))
        answer["project"] = row_to_dict(after, ProjectModel)
    await emit("pm.project.updated", {"project_id": project_id})
    return answer


# ── POST /tasks/{id}/agent-review ───────────────────────────────────────────


class AgentReviewIn(BaseModel):
    """The body of ``POST /tasks/{id}/agent-review``. No other key. SW-18b adds
    the decision ``request_changes``."""

    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve"]


#: The one write that a second approve cannot pass: the wait that the caller
#: read must still hold. The row lock queues a second approve, and Postgres
#: then reads the cleared row, so it matches nothing.
_CLAIM_SQL = (
    "UPDATE pm_tasks SET agent_reviewer = NULL, agent_review_requested_at = NULL,"
    " updated_at = now()"
    " WHERE id = CAST(:tid AS uuid)"
    "   AND agent_review_requested_at IS NOT NULL"
    "   AND agent_review_requested_at = :seen"
    " RETURNING *"
)


async def _done_lane(db: Any, task: Any) -> Any | None:
    """The first ``done`` lane of the task's own status set, or ``None``."""
    try:
        return await load_default_status(
            db, await status_owner_id(db, str(task.project_id)), COMPLETED_CATEGORY,
        )
    except HTTPException as exc:
        if exc.status_code == 422:
            return None
        raise


@router.post("/tasks/{task_id}/agent-review", dependencies=[Depends(require_review)])
@transient_as_409
async def decide_agent_review(
    task_id: str, payload: AgentReviewIn,
    user: UserContext = Depends(get_current_user),
    if_match: str | None = Header(None, alias="If-Match"),
) -> dict:
    """A person approves the agent work that waits on a task (SW-18a).

    The order: the flag (404), a person (403), the visibility (404, so a task
    of another tenant or a hidden task is a task that does not exist),
    ``If-Match`` when sent (412), a wait (409 :data:`NOT_WAITING`), the
    authority (403), then the writes. The approve clears the wait,
    moves the task to done and closes the open agent run, in one
    transaction. A second approve finds no wait: 409, and nothing changes.

    Lock order: the run row first, then the task row. An agent write locks
    its run and then bumps the task, so this order adds no deadlock. A
    transient SQLSTATE still answers 409 (``core.transient_as_409``).
    """
    _refuse_agent_or_absent_person(user)
    if _uuid_or_none(task_id) is None:
        raise HTTPException(status_code=404, detail="Task not found")
    # Imported here: `agent_runs` imports `tasks`, and `tasks` imports this
    # module, so a module import would make a cycle.
    from gateway.routes.projects.agent_runs import close_reviewed_run, lock_open_task_run

    moved = None
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        before = await load_visible_task(db, vis, task_id)
        if isinstance(if_match, str):
            require_precondition(before, if_match, row_to_dict(before, TaskModel))
        # The wait before the authority: an approve clears the reviewer, so a
        # replay of the reviewer reads 409, never 403. The caller sees the
        # task, so the state is no secret.
        seen = getattr(before, "agent_review_requested_at", None)
        if seen is None:
            raise HTTPException(status_code=409, detail=NOT_WAITING)
        await assert_may_approve_agent_work(db, user, before)
        run = await lock_open_task_run(db, task_id)
        claimed = (await db.execute(text(_CLAIM_SQL), {"tid": task_id, "seen": seen})
                   ).fetchone()
        if claimed is None:
            raise HTTPException(status_code=409, detail=NOT_WAITING)
        status = await require_row(db, "pm_task_statuses", str(claimed.status_id), "Status")
        if status.category not in CLOSING_CATEGORIES:
            done = await _done_lane(db, claimed)
            if done is None:
                # Raised inside the transaction, so the wait stays.
                raise HTTPException(status_code=409, detail=NO_DONE_STATUS)
            moved = await apply_status_transition(
                db, claimed, str(done.id), created_by=actor(user),
            )
        run_id = await close_reviewed_run(db, run) if run is not None else None
        await record_activity(
            db, activity_type="system", created_by=actor(user), task_id=task_id,
            body=APPROVED_BODY,
            meta={"agent_review": "approved", "agent_run_id": run_id},
        )
        after = await require_row(db, "pm_tasks", task_id, "Task")
        answer = {
            "task_id": task_id,
            "approved": True,
            "status_id": str(after.status_id),
            "closed_run_id": run_id,
            "version": wire(after.updated_at),
        }

    await emit("pm.task.updated", {"task_id": task_id})
    if moved is not None:
        await emit("pm.task.status_changed", {
            "task_id": task_id,
            "from": moved["from"].name, "to": moved["to"].name,
            "to_category": moved["to"].category,
        })
    return answer
