"""Project sequences (SW-27a, agent_coordination.md "SW-27", task 861).

A project holds named number sequences, such as ``migration``, ``decision``
or ``handoff``. One call reserves the next number of one sequence for one
task, and no two callers ever get the same number.

    POST /projects/nodes/{project_id}/sequences/{name}/reserve
         {"task_id": "...", "run_id": "..." (optional)}
    -> 201 {"sequence": name, "value": 474, "reservation_id": "...", ...}

**One atomic statement.** The reserve is ``INSERT ... ON CONFLICT DO UPDATE
SET last_value = last_value + 1 RETURNING``. Postgres takes the row lock of
the conflicting row, so parallel callers queue on it and each gets the next
value. The first call of a name makes the sequence and returns 1. The
reservation row is written in the same transaction, and the unique key
``(sequence_id, value)`` refuses a second row for one number.

**Burned numbers** (the owner's answer on task 861, prompt e6f9f76c). No code
lowers ``last_value``, so a released number is never handed out again.

**Who may reserve.** A caller who can see the task (``load_visible_task``),
for a task in the project or in one of its subprojects (``scope_clause``). A
project the caller cannot see answers 404, as every Projects read does. An
archived project refuses a new reservation (``assert_not_archived``). An
outside agent reaches the route through the MCP tool ``reserve_number``
(SW-27b): the pair is in ``MCP_WRITE_ROUTES``, so ``projects:write``, the nonce
and the write cap apply, and ``resolve_visibility`` narrows the agent to its
pins. The organization comes from the bound session, never from the request
(R5). A caller may name a run, and the run must be a run of that task. The
route does not check that the run is the caller's own: that is SW-27c's to
decide, before a mark reads ``run_id`` (task 3894).

Not here: the Needs attention kind and the release (SW-27c), the seed of the
repo's sequences (SW-27d), and the ``/next-ticket`` and ``/handoff`` edits
(SW-19 and SW-19b).
"""

from __future__ import annotations

import re
import uuid
from typing import Any, Literal

from acb_auth import UserContext, agent_oauth, get_current_user
from fastapi import Depends, HTTPException
from gateway.routes.projects.analytics import scope_clause
from gateway.routes.projects.checks import own_run
from gateway.routes.projects.core import (
    _tenant_session,
    assert_not_archived,
    load_visible_project,
    load_visible_task,
    resolve_visibility,
    router,
    wire,
)
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

#: The name rule. The migration's CHECK holds the same pattern. Use it with
#: ``fullmatch``: ``$`` in Python also matches before a final newline, and the
#: CHECK does not, so ``match`` would let "name\n" reach a 500.
NAME_RE = re.compile(r"[a-z][a-z0-9_-]{0,39}")

#: The one reserve statement. The INSERT makes the sequence on its first use
#: and hands out 1. On a conflict it adds one under the row lock and hands out
#: the new value. ``organization_id`` takes the column default, the bound
#: tenant (R5).
RESERVE_SQL = """
WITH seq AS (
    INSERT INTO pm_project_sequences (project_id, name, last_value)
    VALUES (CAST(:pid AS uuid), :name, 1)
    ON CONFLICT (organization_id, project_id, name)
    DO UPDATE SET last_value = pm_project_sequences.last_value + 1,
                  updated_at = now()
    RETURNING id, last_value
)
INSERT INTO pm_sequence_reservations (sequence_id, value, task_id, run_id)
SELECT seq.id, seq.last_value, CAST(:task AS uuid), CAST(:run AS uuid)
  FROM seq
RETURNING id, value, reserved_at
"""

#: The run must belong to the task, so a reservation never names a stranger.
_RUN_OF_TASK_SQL = """
SELECT lower(member_email) AS member, client_via, connection_id, session_key
  FROM pm_agent_runs
 WHERE id = CAST(:run AS uuid) AND task_id = CAST(:task AS uuid)
   AND task_id IS NOT NULL
"""

#: SW-27c1. One reservation with its sequence, and the run that it names, for
#: the own-run rule (``checks.own_run``). ``FOR UPDATE OF r`` holds the row,
#: so two marks of one number queue, and the second sees the first.
_RESERVATION_SQL = """
SELECT r.id, r.state, r.value, r.task_id, r.run_id, r.reserved_at, r.used_at,
       r.released_at, s.name AS sequence, s.project_id,
       lower(ar.member_email) AS member, ar.client_via, ar.connection_id,
       ar.session_key
  FROM pm_sequence_reservations r
  JOIN pm_project_sequences s ON s.id = r.sequence_id
  LEFT JOIN pm_agent_runs ar ON ar.id = r.run_id
 WHERE r.id = CAST(:rid AS uuid)
   FOR UPDATE OF r
"""

#: SW-27c1. The mark itself. ``state = 'reserved'`` is in the WHERE, so a
#: number that is already used or released never moves, and the CHECKs of
#: migration 477 hold (one timestamp for each state).
_MARK_SQL = """
UPDATE pm_sequence_reservations
   SET state = :state,
       used_at = CASE WHEN :state = 'used' THEN now() ELSE NULL END,
       released_at = CASE WHEN :state = 'released' THEN now() ELSE NULL END
 WHERE id = CAST(:rid AS uuid) AND state = 'reserved'
RETURNING id, state, used_at, released_at
"""

#: The texts of the mark route. The edge maps each one to its own text.
NOT_YOUR_RUN = "This number is not a number of your run."
PERSON_MARK = "Only the agent of the run marks its number here."
RUN_NOT_YOURS = "The run is not your own run on this task."


class ReserveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    run_id: str | None = None


def _uuid(value: str | None, what: str, status: int = 422) -> str | None:
    if value is None:
        return None
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=status, detail=f"{what} is not an id.") from exc


@router.post("/nodes/{project_id}/sequences/{name}/reserve", status_code=201)
async def reserve_number(
    project_id: str,
    name: str,
    payload: ReserveIn,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Reserve the next number of the sequence *name* of a project for one
    task. 201 with the number. 404 for a project or task the caller cannot
    see, 422 for a bad name, a task outside the project, or a run of another
    task."""
    pid = _uuid(project_id, "Project", status=404)
    task_id = _uuid(payload.task_id, "task_id")
    run_id = _uuid(payload.run_id, "run_id")
    if not NAME_RE.fullmatch(name):
        raise HTTPException(
            status_code=422,
            detail="A sequence name is 1 to 40 lowercase letters, digits, _ or -.",
        )
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        await load_visible_task(db, vis, task_id)
        # FOR SHARE: a delete of the project waits for this reservation, so a
        # race answers 404 here and never a 500 from the foreign key.
        project = await load_visible_project(db, vis, pid, lock_share=True)
        assert_not_archived(project, then="Reserve a number in")
        in_project = await scope_clause(db, vis, pid, True)
        found = (await db.execute(
            text(f"SELECT 1 FROM pm_tasks t WHERE t.id = CAST(:task AS uuid) AND {in_project}"),
            {"task": task_id, "pid": pid},
        )).fetchone()
        if found is None:
            raise HTTPException(status_code=422, detail="The task is not in this project.")
        if run_id is not None:
            run = (await db.execute(
                text(_RUN_OF_TASK_SQL), {"run": run_id, "task": task_id},
            )).fetchone()
            if run is None:
                raise HTTPException(status_code=422, detail="The run is not a run of this task.")
            # SW-27c1, the owner's decision 1: a reservation names only the
            # caller's own run. A person owns no agent run.
            if not own_run(run, user):
                raise HTTPException(status_code=422, detail=RUN_NOT_YOURS)
        try:
            row = (await db.execute(
                text(RESERVE_SQL),
                {"pid": pid, "name": name, "task": task_id, "run": run_id},
            )).fetchone()
        except IntegrityError as exc:
            # A race: the task or the run went between the checks above and
            # this statement. The same-org guard then refuses with the SQLSTATE
            # of a foreign key. Answer 404, never a 500. Any other integrity
            # error is a defect, so it still raises.
            if getattr(exc.orig, "sqlstate", None) != "23503":
                raise
            raise HTTPException(
                status_code=404, detail="The task or the run is gone. Try again.",
            ) from exc
    return {
        "project_id": pid,
        "sequence": name,
        "value": int(row.value),
        "reservation_id": str(row.id),
        "task_id": task_id,
        "run_id": run_id,
        "reserved_at": wire(row.reserved_at),
    }


class MarkIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: Literal["used", "released"]


@router.post("/reservations/{reservation_id}/mark")
async def mark_number(
    reservation_id: str,
    payload: MarkIn,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Mark one reserved number ``used`` or ``released`` (SW-27c1).

    The owner's decisions 1, 2 and 5 on task 3894:

    * Only a signed agent marks here, and only a number of its OWN run
      (``checks.own_run``: the same member, client, connection and session).
      A person gets 403. A person's release is SW-27c3.
    * Dark: a signed agent of an organization off ``sequence_marks_orgs``
      gets 404 with ``agent_oauth.SEQUENCE_MARKS_OFF``.
    * A number that the caller cannot see answers 404, as a task read does.
      A number with no run, or of another run, answers 403.
    * The same mark twice answers 200 and changes nothing. A number that is
      already used or released cannot change: 409. A released number is
      burned, and no number comes back.
    """
    agent = getattr(user, "agent", None)
    if agent is None:
        raise HTTPException(status_code=403, detail=PERSON_MARK)
    if not agent_oauth.sequence_marks_allowed(getattr(user, "organization_id", None)):
        raise HTTPException(status_code=404, detail=agent_oauth.SEQUENCE_MARKS_OFF)
    rid = _uuid(reservation_id, "Reservation", status=404)
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        row = (await db.execute(text(_RESERVATION_SQL), {"rid": rid})).fetchone()
        if row is None or row.task_id is None:
            raise HTTPException(status_code=404, detail="Reservation not found")
        # The pins and the visibility of the task decide what the agent sees.
        await load_visible_task(db, vis, str(row.task_id))
        if row.run_id is None or not own_run(row, user):
            raise HTTPException(status_code=403, detail=NOT_YOUR_RUN)
        state = row.state
        used_at, released_at = row.used_at, row.released_at
        if state == "reserved":
            marked = (await db.execute(
                text(_MARK_SQL), {"rid": rid, "state": payload.state},
            )).fetchone()
            state, used_at, released_at = marked.state, marked.used_at, marked.released_at
        elif state != payload.state:
            raise HTTPException(
                status_code=409, detail=f"The number is already {state}. It cannot change.")
    return {
        "reservation_id": rid,
        "project_id": str(row.project_id),
        "sequence": row.sequence,
        "value": int(row.value),
        "task_id": str(row.task_id),
        "run_id": str(row.run_id),
        "state": state,
        "reserved_at": wire(row.reserved_at),
        "used_at": wire(used_at),
        "released_at": wire(released_at),
    }
