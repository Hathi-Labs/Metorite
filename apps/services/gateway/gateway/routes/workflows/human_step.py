"""The human step: a task for a person, and a pause until it is done (STU-E4).

Spec: ``project-docs/specs/ai_studio.md`` §3.3 and §14 row E4. A **playbook** is
a workflow of mostly human steps. It needs no second engine and no second task
store, because the step is a node of the one engine and its task is a row of
``pm_tasks``.

**The step.** A ``human_step`` node has a literal ``assignee`` (a member email),
a ``title`` and an optional ``description``. The runner pauses the run at the
node, as it does at a long wait. ``service._hold_at_gate`` then calls
:func:`open_step`, which makes the task and writes the pause row in ONE
transaction. A failure rolls both back, and the run ends ``failed`` with a
plain message. The pause row has the reason ``task`` (migration 427).

**Least privilege.** The task goes into the PERSONAL project of the assignee,
through ``create_personal_task(..., announce=False)``, as ``park.py`` does for
its subject task. So the step writes one NEW private task for one member of the
org. It names no existing task, so it has none of the gap that
``service._member_blind_write_nodes`` closes for ``pm_task``, and it does not
widen that gap. The assignee must be an ACTIVE member of the org of the run,
checked before any write. The node needs no project grant of the workflow
owner, because it writes nowhere that the assignee cannot already write. An
``announce=False`` task sends no ``pm.task.created`` event, so no workflow can
start from it and loop.

**The link.** The task carries ``origin = {kind: workflow_run, run_id,
workflow_id, node_id}``. The pause row carries the task id in its snapshot.
:func:`read_task` reads the task only when BOTH agree, so a task that is not
the one of this run never settles the run.

**The actor.** The assignment and the "Captured" line name
``system:workflow:<id>`` (D-PM-4), through the ``actor`` argument of
``create_personal_task``, so the timeline shows an automation. The task keeps
the assignee as its creator, so the assignee may move or delete it
(``authority.may_delete_task``).

**The settle.** ``scheduler.scan_done_tasks`` reads each pending ``task`` pause
of ONE org in a session bound to that org. The task id comes from the stored
pause row and never from a payload or a trigger. A done status resumes the run
through ``service.resume_run``, whose compare-and-set lets exactly one caller
start it. **Cancelled means more than a cancelled lane**, because the default
personal tree seeds no cancelled lane. The step also ends when the assignee
TRASHED the task (the TRASH overlay, for longer than :data:`TRASH_SETTLE_SECONDS`), when the task is ARCHIVED (by hand, or
as the source of a merge), when the assignee is no longer an active member, and
when a lane of the cancelled category holds it. A deleted task ends it too. All
of them end the run as ``failed`` through :func:`end_step`, with the same
compare-and-set. A done task wins over an archive, because the lifecycle sweep
archives done tasks, and a TRASH wins over a done lane, as
``personal.effective_disposition`` reads it.

**Open: no expiry.** ``park.PARK_LIFE`` is the life of a decision, not of a
task, and no task seam holds a deadline. So a task that stays open keeps the run
parked. Nothing here answers for a
person, and nothing counts or ranks one.

The scan runs only for an org on ``WORKFLOW_UNATTENDED_ORGS``, so the node is
dark until the owner lists an org. :func:`org_ready` is the check of the hold.

**The steps of a run (STU-E4b).** ``runs.get_run_steps`` lists the human steps
of one run with :func:`step_rows`, :func:`step_state` and the task reading of
:func:`read_task` and :func:`task_outcome`. It keeps no second reading of a
task. The task id goes only to the person of the task.

Fence: ``tests/unit/test_workflows_human_step.py``,
``tests/unit/test_workflows_human_step_r8.py``,
``tests/unit/test_workflows_run_steps.py`` and
``tests/unit/test_workflows_run_steps_r8.py``.
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from typing import Any

from gateway.routes.workflows import park, unattended
from sqlalchemy import text

#: The ``workflow_run_pauses.reason`` of a human step (migration 427).
REASON = "task"
#: The ``origin.kind`` of the task, the link from the task back to its run.
ORIGIN_KIND = "workflow_run"

#: What :func:`task_outcome` can name.
WAIT, RESUME, CANCELLED, GONE = "wait", "resume", "cancelled", "gone"

#: The error of a run that a human step ended. Plain words, no task id.
CANCELLED_ERROR = (
    "The task of a human step was cancelled, removed or archived, or its person "
    "left, so the run stopped."
)
GONE_ERROR = "The task of a human step no longer exists, so the run stopped."
#: The error of a hold that could not make the task.
REFUSED_ERROR = (
    "The human step could not make its task: the person is not an active member "
    "of this organization, or the step has no person or title. Nothing was made."
)
NOT_READY_ERROR = (
    "Human steps are not turned on for this organization yet, so the run stopped. "
    "Nothing was made."
)

_TITLE_MAX = 200
#: How long a TRASH overlay must stand before it ends the step. My Tasks writes
#: TRASH as the reversible half of Delete, with an Undo window of 7 s
#: (``UNDO_WINDOW_SECONDS`` in ``tasks/lib/removal.ts``), and the scan runs every 30 s. A trash
#: that the person undoes must not fail the run, so the overlay counts after 60 s.
TRASH_SETTLE_SECONDS = 60
_TEXT_MAX = 2000
_EMAIL_MAX = 254


class StepRefused(Exception):
    """A fact that the step needs is missing. The hold fails the run."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def org_ready(organization_id: str | None) -> bool:
    """True when the scan can resume a pause of this org. Default OFF.

    The scan acts only for an org on ``WORKFLOW_UNATTENDED_ORGS`` (H-328 part
    (b)), so a pause of any other org would stay for good.
    """
    return unattended.org_allowed(organization_id)


def human_step_nodes(serialized: dict[str, Any]) -> list[str]:
    """Ids of the ``human_step`` nodes in *serialized*."""
    return [
        str(block.get("id"))
        for block in serialized.get("blocks") or []
        if str(block.get("type") or "") == "human_step"
    ]


def is_human_step(serialized: Any, node_id: str) -> bool:
    """True when the block *node_id* of the pinned graph is a human step."""
    if not isinstance(serialized, dict):
        return False
    return any(
        str(block.get("id")) == node_id and str(block.get("type") or "") == "human_step"
        for block in serialized.get("blocks") or []
    )


def step_config(serialized: Any, node_id: str) -> dict[str, Any]:
    """The config of the node in the run's PINNED graph, never the draft."""
    if not isinstance(serialized, dict):
        return {}
    for block in serialized.get("blocks") or []:
        if str(block.get("id")) == node_id and isinstance(block.get("config"), dict):
            return dict(block["config"])
    return {}


def _clip(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def step_person(value: Any) -> str | None:
    """The member email of a step, in lower case, or None.

    The one rule of a usable person: an ``@``, at most 254 characters and no
    ``{{ref}}``. :func:`open_step` refuses a step with None, and
    :func:`step_rows` shows None.
    """
    who = str(value or "").strip().lower()
    if "@" not in who or len(who) > _EMAIL_MAX or "{{" in who:
        return None
    return who


def task_values(
    *, config: dict[str, Any], workflow_id: str, workflow_name: str, run_id: str,
    node_id: str,
) -> dict[str, Any]:
    """The task columns of a step. The title and text come from the pinned
    config only. The origin links the task back to the run."""
    title = _clip(config.get("title"), _TITLE_MAX)
    if not title:
        raise StepRefused("no_title")
    body = str(config.get("description") or "").strip()[:_TEXT_MAX]
    name = _clip(workflow_name, 60) or "a workflow"
    footer = f'This task is a step of the workflow "{name}". The run goes on when you finish it.'
    return {
        "title": title,
        "description": f"{body}\n\n{footer}" if body else footer,
        "source": "automation",
        "origin": {
            "kind": ORIGIN_KIND,
            "run_id": run_id,
            "workflow_id": workflow_id,
            "node_id": node_id,
        },
    }


_INSERT_PAUSE = (
    "INSERT INTO workflow_run_pauses (id, run_id, node_id, snapshot, reason, status) "
    "VALUES (CAST(:id AS uuid), :run_id, :node_id, :snapshot ::jsonb, 'task', "
    "'pending')"
)


async def open_step(
    db: Any,
    *,
    organization_id: str,
    workflow_id: str,
    workflow_name: str,
    run_id: str,
    node_id: str,
    snapshot: dict[str, Any],
) -> dict[str, str]:
    """Make the task and write the pause, on *db*. Nothing here commits.

    *db* is the caller's ``_run_session(org)``. The assignee and the text come
    from the pinned graph in *snapshot*. Raises :class:`StepRefused` when a fact
    is missing, and the caller's rollback then removes each row.
    """
    from gateway.routes.projects.core import load_default_status
    from gateway.routes.projects.personal import (
        create_personal_task,
        ensure_personal_project,
    )

    org = str(organization_id or "").strip().lower()
    config = step_config(snapshot.get("serialized"), node_id)
    who = step_person(config.get("assignee"))
    if not org or who is None:
        raise StepRefused("no_assignee")
    # An ACTIVE member of THIS org, before any write. A member of another org
    # resolves to that org, and the compare refuses it.
    if not await park._owner_in_org(db, who, org):
        raise StepRefused("assignee_not_member")
    values = task_values(
        config=config, workflow_id=workflow_id, workflow_name=workflow_name,
        run_id=run_id, node_id=node_id,
    )
    project = await ensure_personal_project(db, who)
    project_id = str(project.id)
    status = await load_default_status(db, project_id)
    task = await create_personal_task(
        db, who, project_id, project_id, str(status.id), values, announce=False,
        actor=park.asker(workflow_id),
    )
    task_id = str(task.id)
    pause_id = str(uuid.uuid4())
    await db.execute(
        text(_INSERT_PAUSE),
        {
            "id": pause_id,
            "run_id": run_id,
            "node_id": node_id,
            "snapshot": json.dumps(
                {**snapshot, "task_id": task_id, "assignee": who}, default=str,
            ),
        },
    )
    return {"task_id": task_id, "pause_id": pause_id, "assignee": who}


#: The pending ``task`` pauses of one org. The session binds the org, and the
#: outer predicate names it again.
PENDING_SQL = (
    "SELECT p.id, p.run_id, p.node_id, p.snapshot, r.organization_id, "
    "       r.workflow_id, r.workflow_name "
    "  FROM workflow_run_pauses p "
    "  JOIN workflow_runs r ON r.id = p.run_id "
    " WHERE p.reason = 'task' AND p.status = 'pending' "
    "   AND r.status = 'paused' "
    "   AND r.organization_id = CAST(:org AS uuid)"
)

#: The task of ONE pause, with its status stage. It must carry the origin of
#: THIS run and THIS node, so a task of another run never settles this one.
_TASK_SQL = (
    "SELECT t.id, s.category, t.archived_at, "
    "       EXISTS (SELECT 1 FROM pm_task_personal pp WHERE pp.task_id = t.id "
    "                  AND lower(pp.member_email) = :who "
    "                  AND pp.disposition = 'TRASH' "
    "                  AND pp.updated_at < now() - make_interval(secs => :settle)) "
    "       AS trashed "
    "  FROM pm_tasks t JOIN pm_task_statuses s ON s.id = t.status_id "
    " WHERE t.id = CAST(:id AS uuid) "
    "   AND t.origin ->> 'kind' = 'workflow_run' "
    "   AND t.origin ->> 'run_id' = :run_id "
    "   AND t.origin ->> 'node_id' = :node_id"
)


def task_id_of(snapshot: Any) -> str | None:
    """The task id that a pause snapshot holds, or None."""
    if not isinstance(snapshot, dict):
        return None
    raw = snapshot.get("task_id")
    if not raw:
        return None
    try:
        return str(uuid.UUID(str(raw)))
    except (TypeError, ValueError):
        return None


async def read_task(
    db: Any, task_id: str, run_id: str, node_id: str, assignee: str = "",
    org: str = "",
) -> Any | None:
    """The task row of one pause, in the org that *db* binds, or None.

    The row carries ``category``, ``archived_at``, the ``trashed`` mark of the
    *assignee* and ``assignee_active``. *assignee* is the person of the stored
    pause row, and the directory read uses *org*. Both come from the pause row
    and the scan, never from a payload."""
    who = str(assignee or "").strip().lower()
    row = (
        await db.execute(
            text(_TASK_SQL),
            {"id": task_id, "run_id": str(run_id), "node_id": str(node_id), "who": who,
             "settle": TRASH_SETTLE_SECONDS},
        )
    ).fetchone()
    if row is None:
        return None
    active = bool(who and org and await park._owner_in_org(db, who, str(org).strip().lower()))
    return SimpleNamespace(
        category=row.category, archived_at=row.archived_at, trashed=bool(row.trashed),
        assignee_active=active,
    )


def task_outcome(task: Any | None) -> str:
    """What the stored task row says. Nothing else decides.

    ``gone`` for a missing row. ``cancelled`` when the assignee trashed the
    task. ``resume`` for a done status. ``cancelled`` for an archived task, a
    cancelled status or an assignee who is no longer active. Any other row is
    ``wait``. A row with no ``trashed``, ``archived_at`` or ``assignee_active``
    reads as a live task of an active person.
    """
    from gateway.routes.projects.core import COMPLETED_CATEGORY

    if task is None:
        return GONE
    if getattr(task, "trashed", False):
        return CANCELLED
    if task.category == COMPLETED_CATEGORY:
        return RESUME
    if (
        task.category == "cancelled"
        or getattr(task, "archived_at", None) is not None
        or not getattr(task, "assignee_active", True)
    ):
        return CANCELLED
    return WAIT


async def end_step(
    db: Any, *, pause_id: str, run_id: str, outcome: str,
) -> bool:
    """End one run at its human step: the pause ``rejected``, the run ``failed``.

    The pause UPDATE is the compare-and-set. A loser returns False and writes
    nothing. The run UPDATE holds its own guard, and a loss there raises
    :class:`park.ParkLost`, so the caller rolls the pause back too.
    """
    if outcome not in (CANCELLED, GONE):
        raise ValueError(f"end_step takes {CANCELLED} or {GONE}, not {outcome}")
    won = (
        await db.execute(
            text(
                "UPDATE workflow_run_pauses SET status = 'rejected', resolved_at = now() "
                "WHERE id = CAST(:id AS uuid) AND status = 'pending' RETURNING id"
            ),
            {"id": pause_id},
        )
    ).fetchone()
    if won is None:
        return False
    ran = (
        await db.execute(
            text(
                "UPDATE workflow_runs SET status = 'failed', error = :error, "
                "finished_at = now() "
                "WHERE id = CAST(:id AS uuid) AND status = 'paused' RETURNING id"
            ),
            {"id": run_id, "error": CANCELLED_ERROR if outcome == CANCELLED else GONE_ERROR},
        )
    ).fetchone()
    if ran is None:
        raise park.ParkLost(run_id)
    return True


# ── The steps of one run (STU-E4b) ──────────────────────────────────────────
#
# ``GET /workflows/runs/{run_id}/steps`` (``runs.get_run_steps``) lists the
# human steps of one run, for the Checklist block of a playbook (STU-D9). The
# pure parts are here, beside the rules that they read. The route reads the
# rows in ONE tenant session and calls them.

#: The state words of a step. Nothing else names a state of a step.
STEP_PENDING = "pending"
STEP_DONE = "done"
STEP_CANCELLED = "cancelled"
STEP_NOT_REACHED = "not_reached"

#: A run in one of these states can still make the task of a step.
_LIVE_RUN = frozenset({"queued", "running", "paused"})

#: The task pauses of one run, the newest first. The read takes the task id and
#: the person from the snapshot, and never the pinned graph, so it stays small.
RUN_PAUSES_SQL = (
    "SELECT node_id, status, snapshot ->> 'task_id' AS task_id, "
    "       snapshot ->> 'assignee' AS assignee "
    "  FROM workflow_run_pauses "
    " WHERE run_id = CAST(:run_id AS uuid) AND reason = 'task' "
    " ORDER BY created_at DESC, id DESC"
)

#: The graph that the run pinned: the newest pause snapshot that holds one.
#: ``resume_run`` replays this graph, so a test run of a draft reads right too.
PINNED_GRAPH_SQL = (
    "SELECT snapshot -> 'serialized' AS serialized FROM workflow_run_pauses "
    " WHERE run_id = CAST(:run_id AS uuid) "
    "   AND jsonb_typeof(snapshot -> 'serialized') = 'object' "
    " ORDER BY created_at DESC, id DESC LIMIT 1"
)


def _list_of(serialized: dict[str, Any], key: str) -> list[dict[str, Any]]:
    """The dict entries of ``serialized[key]``. Any other shape gives none."""
    found = serialized.get(key)
    return [item for item in found if isinstance(item, dict)] if isinstance(found, list) else []


def walk_order(serialized: Any) -> list[str]:
    """The block ids of *serialized* in the order that a run walks them.

    It is a stable topological order of the connections. A block comes after
    each block that leads to it, and the block list breaks a tie. A cycle or a
    connection to an unknown block does not raise. Each block that the walk
    cannot place comes last, in the block list order.
    """
    import heapq

    if not isinstance(serialized, dict):
        return []
    index: dict[str, int] = {}
    for block in _list_of(serialized, "blocks"):
        index.setdefault(str(block.get("id")), len(index))
    after: dict[str, list[str]] = {nid: [] for nid in index}
    waits = dict.fromkeys(index, 0)
    for conn in _list_of(serialized, "connections"):
        src, tgt = str(conn.get("source")), str(conn.get("target"))
        if src in index and tgt in index and src != tgt:
            after[src].append(tgt)
            waits[tgt] += 1
    ready = [(index[nid], nid) for nid in index if waits[nid] == 0]
    heapq.heapify(ready)
    order: list[str] = []
    while ready:
        _, nid = heapq.heappop(ready)
        order.append(nid)
        for tgt in after[nid]:
            waits[tgt] -= 1
            if waits[tgt] == 0:
                heapq.heappush(ready, (index[tgt], tgt))
    placed = set(order)
    return order + [nid for nid in index if nid not in placed]


def step_rows(serialized: Any) -> list[dict[str, Any]]:
    """The human steps of *serialized* in walk order.

    Each row is ``node_id``, ``title`` and ``assignee``, from the literal config
    of the pinned graph, never from a payload. A step with no usable person
    gives ``assignee`` None, and a step with no title gives an empty title.
    Neither raises.
    """
    if not isinstance(serialized, dict):
        return []
    steps: dict[str, dict[str, Any]] = {}
    for block in _list_of(serialized, "blocks"):
        if str(block.get("type") or "") == "human_step":
            steps.setdefault(str(block.get("id")), block)
    rows: list[dict[str, Any]] = []
    for nid in walk_order(serialized):
        block = steps.get(nid)
        if block is None:
            continue
        config = block.get("config") if isinstance(block.get("config"), dict) else {}
        rows.append({
            "node_id": nid,
            "title": _clip(config.get("title"), _TITLE_MAX),
            "assignee": step_person(config.get("assignee")),
        })
    return rows


def step_state(
    *, result: Any, pause_status: str | None, outcome: str | None, run_status: str,
) -> str:
    """The state word of one step. Nothing else decides it.

    *result* is the node result of the run, *pause_status* the status of the
    newest ``task`` pause of the step (None with no pause), *outcome* the
    :func:`task_outcome` of its task (None when nothing read the task), and
    *run_status* the status of the run.

    * A node result ``ok`` is ``done``: the run went past the step.
    * With a pause, the pause and the task decide. ``resolved`` is ``done``,
      because only a done task resolves a task pause. A done task is ``done``.
      A pending pause whose task waits, or whose snapshot holds no task, is
      ``pending`` while the run is live. Each other case is ``cancelled``.
    * With no pause, a step that waits is ``pending`` while the run is live,
      because the hold has not made the task yet. A refused hold ended the
      run, so the step is ``cancelled``.
    * Any other step is ``not_reached``.
    """
    status = result.get("status") if isinstance(result, dict) else None
    live = run_status in _LIVE_RUN
    if status == "ok":
        return STEP_DONE
    if pause_status is not None:
        if pause_status == "resolved" or outcome == RESUME:
            return STEP_DONE
        if pause_status == "pending" and outcome in (WAIT, None) and live:
            return STEP_PENDING
        return STEP_CANCELLED
    if status == "waiting":
        return STEP_PENDING if live else STEP_CANCELLED
    if status == "error":
        return STEP_CANCELLED
    return STEP_NOT_REACHED
