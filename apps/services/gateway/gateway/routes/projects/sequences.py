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
archived project refuses a new reservation (``assert_not_archived``). An agent
token cannot reach the route until SW-27b adds it to ``MCP_WRITE_ROUTES``, with
``projects:write`` and the pin check. The
organization comes from the bound session, never from the request (R5). An
agent may name its run, and the run must be a run of that task.

Not in this slice: the MCP edge tool (SW-27b), the Needs attention kind and
the release (SW-27c), the seed of the repo's sequences (SW-27d), and the
``/next-ticket`` and ``/handoff`` edits (SW-19 and SW-19b).
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException
from gateway.routes.projects.analytics import scope_clause
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
SELECT 1 FROM pm_agent_runs
 WHERE id = CAST(:run AS uuid) AND task_id = CAST(:task AS uuid)
   AND task_id IS NOT NULL
"""


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
        if run_id is not None and (await db.execute(
            text(_RUN_OF_TASK_SQL), {"run": run_id, "task": task_id},
        )).fetchone() is None:
            raise HTTPException(status_code=422, detail="The run is not a run of this task.")
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
