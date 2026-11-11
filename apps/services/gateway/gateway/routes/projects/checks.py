"""Projects · the Check of a task (WS-MCP SW-23, Metorite task 857).

Spec: ``project-docs/specs/agent_coordination.md`` SW-23 and §3.5.

    POST /projects/tasks/{id}/check-result   → records one run of the Check

A Check is a read-only command that proves the work of a task is still open.
It is the field that lets a ``HANDOFF.md`` entry retire into a task (D135.1).
An agent reads it from ``my_work``, runs it, and reports the result here.

* ``open``: the work is still open. The task does not move.
* ``cannot_run``: the agent could not run the command. The task does not
  move. A ``cannot_run`` NEVER closes a task.
* ``passed``: the Check passed. The task moves to the first ``done`` lane of
  its status set, and the evidence becomes a comment, only when the caller
  may move it (:func:`_closing_refusal`). Otherwise the run is still
  recorded, and the answer says why the task stayed open.

The four columns are on ``pm_tasks`` (migration ``*_pm_tasks_check.sql``).
``tasks.py`` writes ``check_text`` through the create and the PATCH. This
module is the ONE writer of the three result columns.

Dark: while ``agent_task_checks_enabled`` is off (``agent_oauth.task_checks_enabled``,
the one reader), the route answers 404, a task write that sends
``check_text`` gets 422, and :func:`attach_check` adds no key to a row.
Fence: ``tests/unit/test_task_check_r8.py``.
"""

from __future__ import annotations

from typing import Any, Literal

from acb_auth import UserContext, get_current_user
from acb_auth.agent_oauth import task_checks_enabled
from fastapi import Depends, Header, HTTPException
from gateway.routes.projects.core import (
    CLOSING_CATEGORIES,
    COMPLETED_CATEGORY,
    TaskModel,
    _tenant_session,
    actor,
    apply_status_transition,
    emit,
    load_default_status,
    load_visible_task,
    record_activity,
    require_precondition,
    require_row,
    resolve_visibility,
    router,
    row_to_dict,
    status_owner_id,
    wire,
)
from gateway.routes.projects.filters import (
    AGENT_RUN_OPEN_STATUSES,
    connection_gone_sql,
    lease_held_sql,
)
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

#: The three results of a run, the values of the CHECK of the migration.
CHECK_RESULTS: tuple[str, ...] = ("open", "passed", "cannot_run")
#: The result that may close the task. The other two never do.
CHECK_PASSED = "passed"
#: The longest Check text and the longest evidence. The edge holds copies
#: (``agent_edge.tools.MAX_CHECK_CHARS`` and ``MAX_CHECK_EVIDENCE_CHARS``),
#: and ``test_task_check_r8.py`` pins them equal.
MAX_CHECK_CHARS = 2000
MAX_CHECK_EVIDENCE_CHARS = 4000
#: A Check that has not run for this many days is due (``my_work`` reason
#: ``check_due``). The gateway decides it in SQL, so the edge reads ``due``.
CHECK_DUE_DAYS = 7

#: The detail of the 422 when a write sends ``check_text`` while the flag is
#: off. The edge refuses first, with its own text.
CHECKS_OFF = "task_checks_off"
#: The detail codes of the 409 of the report route.
NO_CHECK = "no_check"
CHECK_CHANGED = "check_changed"

#: Why a pass did not close the task (``not_closed`` of the answer).
ALREADY_CLOSED = "already_closed"
NO_DONE_STATUS = "no_done_status"
HELD_BY_OTHER = "held_by_other"

#: The prefix of the comment that a pass writes, before the evidence.
PASSED_COMMENT_PREFIX = "Check passed."


def checks_enabled() -> bool:
    """The flag ``agent_task_checks_enabled`` (default OFF, SW-23), through its
    one reader ``agent_oauth.task_checks_enabled``."""
    return task_checks_enabled()


async def require_checks() -> None:
    """404 while the Check flag is off. A route dependency, so it answers
    before the body is read, as ``agent_runs.require_answer_change`` does."""
    if not checks_enabled():
        raise HTTPException(status_code=404, detail="Not Found")


def clean_check_text(value: Any) -> str | None:
    """The ``check_text`` of a task write, cleaned, or a 422.

    The flag must be on. ``None`` and a blank string clear the Check. A text
    longer than :data:`MAX_CHECK_CHARS`, or one with a NUL, is refused,
    because Postgres refuses a NUL and the edge holds the same cap.
    """
    if not checks_enabled():
        raise HTTPException(status_code=422, detail=CHECKS_OFF)
    if value is None:
        return None
    if not isinstance(value, str):
        raise HTTPException(status_code=422, detail="check_text is text.")
    cleaned = value.strip()
    if "\x00" in cleaned:
        raise HTTPException(status_code=422, detail="check_text holds a NUL character.")
    if len(cleaned) > MAX_CHECK_CHARS:
        raise HTTPException(
            status_code=422,
            detail=f"A Check holds at most {MAX_CHECK_CHARS} characters.",
        )
    return cleaned or None


def check_patch_values(before: Any, values: dict[str, Any]) -> None:
    """Clean ``check_text`` of a PATCH in place. A new text also clears the
    three result columns: the old result was about another command."""
    if "check_text" not in values:
        return
    values["check_text"] = clean_check_text(values["check_text"])
    if values["check_text"] != getattr(before, "check_text", None):
        values.update({
            "check_last_run_at": None,
            "check_last_result": None,
            "check_last_evidence": None,
        })


#: The Check of each task of a page, in one read. ``due`` is the rule of
#: ``my_work``: never run, or not run for :data:`CHECK_DUE_DAYS` days. The
#: database clock decides it.
_CHECK_SQL = (
    "SELECT id, check_text, check_last_run_at, check_last_result,"
    " check_last_evidence,"
    " (check_last_run_at IS NULL OR check_last_run_at < now() - make_interval("
    f"days => {CHECK_DUE_DAYS})) AS due"
    "  FROM pm_tasks WHERE id = ANY(CAST(:ids AS uuid[])) AND check_text IS NOT NULL"
)


def _check_of(row: Any) -> dict[str, Any]:
    return {
        "text": row.check_text,
        "last_run_at": wire(row.check_last_run_at),
        "last_result": row.check_last_result,
        "last_evidence": row.check_last_evidence,
        "due": bool(row.due),
    }


async def attach_check(db: Any, rows: list[dict[str, Any]]) -> None:
    """Put ``check`` on each task row: ``{text, last_run_at, last_result,
    last_evidence, due}``, or ``None`` for a task with no Check.

    A no-op while the flag is off, so a row then has no ``check`` key at
    all, as the run wire has no ``answer`` key while SW-43 is off.
    """
    if not rows or not checks_enabled():
        return
    ids = [str(r.get("id")) for r in rows if r.get("id")]
    found = {
        str(r.id): _check_of(r)
        for r in (await db.execute(text(_CHECK_SQL), {"ids": ids})).fetchall()
    } if ids else {}
    for row in rows:
        row["check"] = found.get(str(row.get("id")))


class CheckResultIn(BaseModel):
    """The body of ``POST /tasks/{id}/check-result``. No other key."""

    model_config = ConfigDict(extra="forbid")

    result: Literal["open", "passed", "cannot_run"]
    evidence: str = Field(min_length=1, max_length=MAX_CHECK_EVIDENCE_CHARS)


#: The open run that keeps the task now: the lease holds and the connection
#: is live. The rules of ``filters.unheld_clause``, so a task that the
#: ``unheld`` filter calls free has no row here.
def _holder_sql() -> str:
    open_list = ", ".join(f"'{s}'" for s in AGENT_RUN_OPEN_STATUSES)
    return (
        "SELECT lower(r.member_email) AS member, r.client_via, r.connection_id,"
        " r.session_key FROM pm_agent_runs r"
        " WHERE r.task_id IS NOT NULL AND r.task_id = CAST(:tid AS uuid)"
        f"   AND r.status IN ({open_list})"
        f"   AND {lease_held_sql('r')}"
        f"   AND NOT {connection_gone_sql('r')}"
        " ORDER BY r.last_activity_at DESC"
    )


def _own_run(run: Any, user: UserContext) -> bool:
    """True when *run* is the caller's own: the same member, and for an agent
    the same client stamp, the same connection and, with SW-7, the same
    session or no session key. A person owns no agent run here."""
    agent = getattr(user, "agent", None)
    if agent is None:
        return False
    if str(run.member or "") != actor(user).strip().lower():
        return False
    if str(run.client_via or "") != str(getattr(agent, "actor_via", "") or ""):
        return False
    if str(run.connection_id or "").lower() != str(agent.connection_id or "").lower():
        return False
    mine = getattr(agent, "session_key", None)
    return run.session_key is None or mine is None or run.session_key == mine


async def _closing_refusal(db: Any, user: UserContext, task: Any) -> str | None:
    """Why a pass may NOT close *task*, or ``None`` when it may.

    The rule of a status move (``authority.py``: a status move inside one
    project needs only a view, and the caller already passed
    ``load_visible_task`` and ``If-Match``). An agent may move a task into
    ``done`` (EA-6f). On top of that, one run executes a task at a time
    (D140.1): a live run of another session or another member keeps the
    task, so a pass leaves the closing to that holder.
    """
    status = await require_row(db, "pm_task_statuses", str(task.status_id), "Status")
    if status.category in CLOSING_CATEGORIES:
        return ALREADY_CLOSED
    for run in (await db.execute(text(_holder_sql()), {"tid": str(task.id)})).fetchall():
        if not _own_run(run, user):
            return HELD_BY_OTHER
    return None


async def _done_status(db: Any, task: Any) -> Any | None:
    """The first ``done`` lane of the task's own status set, or ``None``."""
    try:
        return await load_default_status(
            db, await status_owner_id(db, str(task.project_id)), COMPLETED_CATEGORY,
        )
    except HTTPException as exc:
        if exc.status_code == 422:
            return None
        raise


_RECORD_SQL = (
    "UPDATE pm_tasks SET check_last_run_at = now(), check_last_result = :result,"
    " check_last_evidence = :evidence, updated_at = now()"
    " WHERE id = CAST(:tid AS uuid) AND check_text IS NOT DISTINCT FROM :expected"
    " RETURNING *"
)


#: The detail of the 428 when a signed agent sends no ``If-Match``. A pass
#: can close the task, so an agent always names the version that it read.
VERSION_REQUIRED = "An agent sends If-Match with the version that it read."


@router.post("/tasks/{task_id}/check-result", dependencies=[Depends(require_checks)])
async def report_check(
    task_id: str, payload: CheckResultIn,
    user: UserContext = Depends(get_current_user),
    if_match: str | None = Header(None, alias="If-Match"),
) -> dict:
    """Record one run of the task's Check (SW-23).

    The order: the flag (404), the visibility (404, so a hidden task is a task
    that does not exist), ``If-Match`` (412), a Check on the task (409
    :data:`NO_CHECK`), then the write. The write holds the Check text that
    the caller read in its WHERE, so a person's change of the Check in the
    same instant wins, and the report gets 409 :data:`CHECK_CHANGED`. Only a
    ``passed`` result may move the task, and only by :func:`_closing_refusal`.
    A signed agent must send ``If-Match`` (428). The edge always sends it, so
    this is the gateway's own copy of the rule.
    """
    if getattr(user, "agent", None) is not None and not isinstance(if_match, str):
        raise HTTPException(status_code=428, detail=VERSION_REQUIRED)
    evidence = payload.evidence.strip()
    if not evidence or "\x00" in evidence:
        raise HTTPException(
            status_code=422,
            detail="The evidence needs text, with no NUL character.",
        )
    moved = None
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        before = await load_visible_task(db, vis, task_id)
        require_precondition(before, if_match, row_to_dict(before, TaskModel))
        expected = getattr(before, "check_text", None)
        if expected is None:
            raise HTTPException(status_code=409, detail=NO_CHECK)
        after = (await db.execute(text(_RECORD_SQL), {
            "tid": task_id, "result": payload.result, "evidence": evidence,
            "expected": expected,
        })).fetchone()
        if after is None:
            raise HTTPException(status_code=409, detail=CHECK_CHANGED)
        not_closed: str | None = None
        if payload.result == CHECK_PASSED:
            not_closed = await _closing_refusal(db, user, after)
            done = None if not_closed else await _done_status(db, after)
            if not_closed is None and done is None:
                not_closed = NO_DONE_STATUS
            if done is not None:
                # The one transition of every status move: the new status,
                # `completed_at` and the `status_change` row.
                moved = await apply_status_transition(
                    db, after, str(done.id), created_by=actor(user),
                )
                after = moved["row"]
                await record_activity(
                    db, activity_type="comment", created_by=actor(user),
                    task_id=task_id, body=f"{PASSED_COMMENT_PREFIX}\n\n{evidence}",
                )
                # The comment bumped the task. Read the stamp that counts.
                after = await require_row(db, "pm_tasks", task_id, "Task")
        answer = {
            "task_id": str(after.id),
            "result": after.check_last_result,
            "last_run_at": wire(after.check_last_run_at),
            "closed": moved is not None,
            "not_closed": not_closed,
            "status_id": str(after.status_id),
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
