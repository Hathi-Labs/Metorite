"""Run endpoints: start, history, detail, live SSE (spec F5, F8, F9).

``GET /runs/{run_id}/steps`` lists the human steps of one run (STU-E4b,
``ai_studio.md`` row E4). It has the access of ``GET /runs/{run_id}``. This
route gives the task id only to the person of the step. ``GET /runs/{run_id}``
and the stream still show the task id of a done step in ``node_results``. That
is a known gap, and this route does not change it.

Test runs of the unpublished draft compile on the fly (``draft: true``) so the
editor's "Test ▸" loop needs no publish; real triggers always execute the
published version.

A test run of the draft is authoring, so it also needs ``feature:studio``
where ``WORKFLOW_STUDIO_GATE_ORGS`` holds the org (STU-J1-3,
``core.check_author``). A run of the published version is use, and the router
gate alone decides it (owner rule 1, task 1818).
"""

from __future__ import annotations

import asyncio
import itertools
import json
import uuid
from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException
from fastapi.responses import StreamingResponse
from gateway.routes.workflows import human_step, unattended
from gateway.routes.workflows.core import (
    MAX_RUN_STEPS,
    MAX_RUNS_PAGE,
    _tenant_session,
    _uid,
    check_author,
    iso,
    load_workflow_or_404,
    parse_jsonb,
    router,
)
from gateway.routes.workflows.engine.graph import (
    GraphValidationError,
    compile_graph,
)
from gateway.routes.workflows.service import (
    _MEMBER_BLIND_REASON,
    RunRejected,
    _long_wait_nodes,
    _member_blind_write_nodes,
    hub_for,
    load_version_serialized,
    start_run,
)
from pydantic import BaseModel, Field
from sqlalchemy import text


class RunRequest(BaseModel):
    payload: dict[str, Any] = Field(default_factory=dict)
    draft: bool = False  # compile + run the current draft (Test ▸)


@router.post("/{workflow_id}/run", status_code=202)
async def run_workflow(
    workflow_id: str,
    body: RunRequest,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    if body.draft:
        # Before the session: a refused caller learns nothing of the workflow.
        await check_author(user)
    async with _tenant_session() as db:
        row = await load_workflow_or_404(db, workflow_id)
        if body.draft:
            ready_modules = (
                await db.execute(
                    text("SELECT id FROM workflow_modules WHERE status = 'ready'"),
                )
            ).fetchall()
            from gateway.routes.workflows.catalog import known_agent_names

            try:
                serialized = compile_graph(
                    parse_jsonb(row.graph, {"nodes": [], "edges": []}),
                    known_modules={str(m.id) for m in ready_modules},
                    known_agents=known_agent_names(),
                )
            except GraphValidationError as exc:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "error": "graph_invalid",
                        "issues": [i.as_dict() for i in exc.issues],
                    },
                ) from exc
            version = int(row.latest_version or 0)
        else:
            if row.status != "published" or not row.latest_version:
                raise HTTPException(
                    status_code=409,
                    detail="Workflow is not published — run as draft or publish first",
                )
            version = int(row.latest_version)
            serialized = await load_version_serialized(db, workflow_id, version)
            if serialized is None:
                raise HTTPException(status_code=500, detail="Published version missing")
        variables = parse_jsonb(row.variables, {})
        name = row.name
        # H-328 part (c): the org of the ROW that the member read in the
        # member's own tenant. Never the payload, and never a second read of
        # the ambient tenant. A person pressed Run, so no list gates it.
        org = str(row.organization_id)
    if _member_blind_write_nodes(serialized):
        # A task node writes `pm_tasks` with no member check, and the spec
        # does not say yet which member a run checks. So the door refuses, as
        # the agent door does (`service.run_published_workflow`).
        raise HTTPException(
            status_code=409,
            detail=f"This workflow can change Projects tasks, so it cannot run "
            f"yet. {_MEMBER_BLIND_REASON} Nothing ran.",
        )
    if human_step.human_step_nodes(serialized) and not human_step.org_ready(org):
        # Only the task scan resumes a human step, and it acts only for a
        # listed org, so the run could stay paused for good (STU-E4).
        raise HTTPException(
            status_code=409,
            detail="This workflow has a human step, and human steps are not "
            "turned on for this organization yet, so it cannot run. Nothing ran.",
        )
    long_waits = _long_wait_nodes(serialized)
    if long_waits and not unattended.org_allowed(org):
        # Only the wait scan resumes such a pause, and it acts only for a
        # listed org. So the run could stay paused for good.
        raise HTTPException(
            status_code=409,
            detail=f"This workflow has a wait node that can pause the run "
            f"({', '.join(long_waits)}). Nothing can resume that pause for this "
            "organization yet, so it cannot run. Nothing ran.",
        )
    # The session is closed before `start_run`: the run opens its own
    # sessions in `org` and outlives this request's transaction.
    try:
        run_id = await start_run(
            workflow_id=workflow_id,
            workflow_name=name,
            version=version,
            serialized=serialized,
            trigger_kind="manual",
            trigger_payload=body.payload,
            variables=variables,
            started_by=_uid(user),
            organization_id=org,
        )
    except RunRejected as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    return {"run_id": run_id, "status": "running", "version": version}


@router.get("/{workflow_id}/runs")
async def list_runs(
    workflow_id: str,
    limit: int = 25,
    user: UserContext = Depends(get_current_user),
) -> list[dict[str, Any]]:
    limit = max(1, min(limit, MAX_RUNS_PAGE))
    async with _tenant_session() as db:
        rows = (
            await db.execute(
                text(
                    """SELECT id, version, trigger_kind, status, error,
                          started_by, started_at, finished_at
                     FROM workflow_runs WHERE workflow_id = :wid
                    ORDER BY started_at DESC LIMIT :limit"""
                ),
                {"wid": workflow_id, "limit": limit},
            )
        ).fetchall()
    return [
        {
            "id": str(r.id),
            "version": r.version,
            "trigger_kind": r.trigger_kind,
            "status": r.status,
            "error": r.error,
            "started_by": r.started_by,
            "started_at": iso(r.started_at),
            "finished_at": iso(r.finished_at),
        }
        for r in rows
    ]


@router.get("/runs/{run_id}")
async def get_run(
    run_id: str,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    async with _tenant_session() as db:
        row = (
            await db.execute(
                text("SELECT * FROM workflow_runs WHERE id = :id"),
                {"id": run_id},
            )
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Run not found")
    status = row.status
    error = row.error
    # Honesty about restarts (spec §3.3): a "running" run with no live hub
    # entry died with the process — report it failed rather than forever-live.
    if status == "running" and hub_for(str(row.id)) is None:
        status = "failed"
        error = error or "interrupted by a platform restart"
    # Lazy reject reconciliation (spec F11): the broker only marks a rejected
    # proposal — nothing calls back into the run. Reads reconcile it.
    if status == "paused":
        status, error = await _reconcile_rejected_pause(str(row.id), status, error)
    return {
        "id": str(row.id),
        "workflow_id": str(row.workflow_id),
        "workflow_name": row.workflow_name,
        "version": row.version,
        "trigger_kind": row.trigger_kind,
        "trigger_payload": parse_jsonb(row.trigger_payload, {}),
        "status": status,
        "error": error,
        "node_results": parse_jsonb(row.node_results, {}),
        "output": parse_jsonb(row.output, None),
        "started_by": row.started_by,
        "started_at": iso(row.started_at),
        "finished_at": iso(row.finished_at),
    }


@router.get("/runs/{run_id}/steps")
async def get_run_steps(
    run_id: str,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """The human steps of one run, in walk order (STU-E4b, ``ai_studio.md`` E4).

    Each step is ``node_id``, ``title``, ``assignee``, ``state`` and
    ``task_id``. The title and the person come from the literal config of the
    graph that the run pinned. The state comes from the node result, the
    ``task`` pause and the task, through :func:`human_step.task_outcome`.

    **Access is the access of** :func:`get_run`: the router gate and the
    tenant session, with no other check. A run of another org, an unknown id
    and an id that is not a UUID each get the same 404.

    **This route gives the task id only to the person of the step.** The task
    is in the personal project of that person. ``task_id`` is null unless the
    caller IS that person and the task still exists. Each other caller gets
    the state word and nothing else of the task. :func:`get_run` and the stream
    still show the task id of a done step in ``node_results``. That is a known
    gap, and this route does not change it.

    **A cap.** The answer holds at most :data:`MAX_RUN_STEPS` steps, the first
    ones in walk order, and ``truncated`` says when the run has more.
    """
    try:
        rid = str(uuid.UUID(str(run_id).strip()))
    except ValueError:
        # Before the session: a bad id is a 404, never a database error.
        raise HTTPException(status_code=404, detail="Run not found") from None
    viewer = str(getattr(user, "email", "") or "").strip().lower()
    alive = hub_for(rid) is not None
    # ONE session: the run, its pauses and each task are read in one
    # transaction, so a resume between two reads cannot split the answer.
    async with _tenant_session() as db:
        read = await _read_steps(db, rid, viewer, alive)
    if read is None:
        raise HTTPException(status_code=404, detail="Run not found")
    steps, truncated = read
    return {"run_id": rid, "steps": steps, "truncated": truncated}


async def _read_steps(
    db: Any, run_id: str, viewer: str, alive: bool,
) -> tuple[list[dict[str, Any]], bool] | None:
    """The steps of *run_id* on *db* and the ``truncated`` mark, or None when
    the session sees no run. Only the first :data:`MAX_RUN_STEPS` steps read
    a task."""
    run = (
        await db.execute(
            text(
                "SELECT id, workflow_id, version, status, node_results, organization_id "
                "FROM workflow_runs WHERE id = CAST(:id AS uuid)"
            ),
            {"id": run_id},
        )
    ).fetchone()
    if run is None:
        return None
    status = str(run.status or "")
    if status == "running" and not alive:
        status = "failed"  # the honesty rule of `get_run`
    results = parse_jsonb(run.node_results, {})
    results = results if isinstance(results, dict) else {}
    pauses = (
        await db.execute(text(human_step.RUN_PAUSES_SQL), {"run_id": run_id})
    ).fetchall()
    newest: dict[str, Any] = {}
    for pause in pauses:
        newest.setdefault(str(pause.node_id), pause)
    org = str(getattr(run, "organization_id", "") or "").strip().lower()
    serialized = await _pinned_graph(db, run)
    rows = human_step.step_rows(serialized)
    shown = list(itertools.islice(rows, MAX_RUN_STEPS))
    steps = [
        await _one_step(db, run_id, row, results.get(row["node_id"]),
                        newest.get(row["node_id"]), status, viewer, org)
        for row in shown
    ]
    return steps, len(rows) > len(shown)


async def _pinned_graph(db: Any, run: Any) -> dict[str, Any]:
    """The graph that the run pinned. The newest pause snapshot holds it, and
    a run with no pause reads its published version."""
    row = (
        await db.execute(text(human_step.PINNED_GRAPH_SQL), {"run_id": str(run.id)})
    ).fetchone()
    found = parse_jsonb(row.serialized, None) if row is not None else None
    if not isinstance(found, dict):
        found = await load_version_serialized(db, str(run.workflow_id), int(run.version or 0))
    return found if isinstance(found, dict) else {}


async def _one_step(
    db: Any, run_id: str, row: dict[str, Any], result: Any, pause: Any,
    run_status: str, viewer: str, org: str,
) -> dict[str, Any]:
    """One step of the answer. The task is read only when the state needs it
    (a pending pause), or when the caller is its person and may get its id. A
    rejected or resolved pause decides the state with no read, so a reader who
    is not the person never learns the live state of the private task after
    the run ended."""
    node_id = row["node_id"]
    task_id = human_step.task_id_of({"task_id": pause.task_id}) if pause is not None else None
    output = result.get("output") if isinstance(result, dict) else None
    if task_id is None and isinstance(output, dict):
        task_id = human_step.task_id_of(output)  # the runner's copy (`runner.py`)
    person = human_step.step_person(pause.assignee) if pause is not None else None
    person = person or row["assignee"]
    mine = bool(viewer) and viewer == person
    pause_status = str(pause.status) if pause is not None else None
    task: Any = None
    outcome: str | None = None
    if task_id is not None and (mine or pause_status == "pending"):
        task = await human_step.read_task(db, task_id, run_id, node_id, person or "", org)
        if pause_status == "pending":
            outcome = human_step.task_outcome(task)
    return {
        "node_id": node_id,
        "title": row["title"],
        "assignee": row["assignee"],
        "state": human_step.step_state(
            result=result, pause_status=pause_status, outcome=outcome, run_status=run_status,
        ),
        # This route gives the task id only to the person of the step, and only
        # while the task exists.
        "task_id": task_id if mine and task is not None else None,
    }


@router.get("/runs/{run_id}/stream")
async def stream_run(
    run_id: str,
    user: UserContext = Depends(get_current_user),
) -> StreamingResponse:
    """SSE: replay this run's node events, then tail live until it finishes."""

    async def _generate():
        hub = hub_for(run_id)
        if hub is None:
            # Finished (or lost) run: serve the stored summary once and close.
            # The tenant scope is still bound here: `TenantScopeMiddleware` is
            # pure ASGI, so the streamed body is produced inside the request's
            # scope, not after it.
            async with _tenant_session() as db:
                row = (
                    await db.execute(
                        text(
                            "SELECT status, error, node_results FROM workflow_runs WHERE id = :id"
                        ),
                        {"id": run_id},
                    )
                ).fetchone()
            if row is None:
                yield _sse({"event": "error", "error": "run not found"})
                return
            for node_id, result in (parse_jsonb(row.node_results, {}) or {}).items():
                yield _sse(
                    {
                        "event": "node",
                        "node_id": node_id,
                        "status": result.get("status", "ok"),
                        "output": result.get("output"),
                        "error": result.get("error"),
                    }
                )
            yield _sse({"event": "run", "status": row.status, "error": row.error})
            return

        queue: asyncio.Queue = asyncio.Queue()
        hub.queues.add(queue)
        try:
            for event in list(hub.events):
                yield _sse(event)
            if hub.done:
                return
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                except TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                if event is None:
                    return
                yield _sse(event)
        finally:
            hub.queues.discard(queue)

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


def _sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, default=str)}\n\n"


async def _reconcile_rejected_pause(
    run_id: str, status: str, error: str | None
) -> tuple[str, str | None]:
    """If the pause's approvals-inbox proposal was rejected, the run is over:
    mark it cancelled (and the pause rejected). Best-effort on read.

    The best-effort `except` wraps the `async with`: an exception mid-write
    rolls the transaction back and the caller keeps the stored status, same
    as before the H2 conversion."""
    try:
        async with _tenant_session() as db:
            pause = (
                await db.execute(
                    text(
                        "SELECT id, snapshot FROM workflow_run_pauses "
                        "WHERE run_id = :id AND status = 'pending' "
                        "ORDER BY created_at DESC LIMIT 1"
                    ),
                    {"id": run_id},
                )
            ).fetchone()
            if pause is None:
                return status, error
            action_id = str((parse_jsonb(pause.snapshot, {}) or {}).get("action_id") or "")
            if not action_id:
                return status, error
            action = (
                await db.execute(
                    text("SELECT status FROM pending_actions WHERE id = :id"),
                    {"id": action_id},
                )
            ).fetchone()
            if action is None or action.status != "rejected":
                return status, error
            await db.execute(
                text(
                    "UPDATE workflow_run_pauses SET status = 'rejected', "
                    "resolved_at = now() WHERE id = :id"
                ),
                {"id": str(pause.id)},
            )
            await db.execute(
                text(
                    "UPDATE workflow_runs SET status = 'cancelled', "
                    "error = :error, finished_at = now() WHERE id = :id"
                ),
                {"id": run_id, "error": "approval rejected"},
            )
            return "cancelled", "approval rejected"
    except Exception:
        return status, error
