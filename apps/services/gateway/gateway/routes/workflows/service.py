"""Run lifecycle: create → execute (supervised asyncio task) → persist.

Bridges the transport-free engine to the gateway's real seams:

- agent nodes    → ``orchestrator.executor.run_agent`` (MAF batch path,
                   ``source="workflow"`` — constraint #9: event-driven
                   execution goes through MAF, never the Copilot runtime)
- tool nodes     → the workflow tool registry (broker-gated writes)
- module nodes   → ``workflow_modules`` rows (status ``ready``)

Live run events fan out through an in-process hub (per-run replay + tail) that
``runs.py`` serves over SSE. v1 honesty note (spec §3.3): runs execute as
supervised asyncio tasks inside the gateway process — durable queueing across
restarts is BO-20's scope; a run interrupted by a restart is marked failed by
the startup sweep (``reconcile_orphaned_runs``) and, for reads that race it,
lazily on the next status read — never silently lost. Paused runs are exempt:
resume rebuilds from the pause snapshot, so they survive restarts.

Every terminal run also passes through ``evaluate_automation_health`` — the
spec R2 mitigation that takes a published workflow off its triggers once its
unattended runs have failed ``AUTO_DISABLE_AFTER`` times in a row.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

# H4, DELIBERATELY NOT H2 (`saas_multitenancy_handover.md`): every session in
# this module belongs to the UNATTENDED run lifecycle — engine node loads
# (`_get_module_code`), run rows written by supervised background tasks that
# outlive the request that may have started them (`start_run`, `_finish_run`,
# `_hold_at_gate`), the startup sweep (`reconcile_orphaned_runs`), the health
# policy (`evaluate_automation_health`), and the programmatic/agent entry
# points (F13). Several are DUAL-USE — reached from a member's manual Run AND
# from the scheduler/webhook/event paths (`start_run`, `resume_run`) — and the
# H2 rule for that is LEAVE: a run must not behave differently depending on
# who happened to trigger it, and inheriting the ambient request tenant is
# exactly what H4 forbids. They stay on the unbound `get_db()` until H4
# threads an explicit tenant (`tenant_session(org_id)` from the workflow row)
# through the run lifecycle. `_pm_task_updater` is the Projects automation
# seam and is likewise H4 — do not change its session acquisition here.
#
# ⚠️ ONE EXCEPTION, and it is done: `_pm_lifecycle_sweeper` (WS-27aa). It
# resolves the workflow owner's organization on an unbound session and then
# opens `_tenant_session(org)` for the sweep — an EXPLICIT tenant from a stored
# fact, which is what H4 asks for, not the ambient inheritance it forbids. It
# is the shape the rest of this module's sites will take.
from gateway.routes.workflows.core import (
    _get_db,
    _log,
    _tenant_session,
    parse_jsonb,
    publish_workflow_activity,
)
from gateway.routes.workflows.engine.handlers import (
    NodeExecutionError,
    NodeServices,
)
from gateway.routes.workflows.engine.runner import execute_workflow
from gateway.routes.workflows.tools import execute_tool
from sqlalchemy import text

#: Global + per-workflow concurrency caps (spec R2 mitigation).
MAX_CONCURRENT_RUNS = 8
MAX_CONCURRENT_RUNS_PER_WORKFLOW = 2
#: Hub entries are dropped this long after a run finishes.
HUB_TTL_SECS = 600.0

#: Trigger kinds that fire with nobody watching — the only ones the
#: auto-disable policy counts (spec R2). ``manual`` is somebody sitting in the
#: editor pressing Run, and ``api`` is an agent or caller passing its own
#: payload: neither repeats on its own, and neither should be able to take a
#: workflow away from everyone else because one caller sent bad arguments.
UNATTENDED_TRIGGERS = frozenset({"schedule", "webhook", "event"})
#: Consecutive unattended failures that disable a published workflow.
AUTO_DISABLE_AFTER = 5

#: Rendered once from the constant above — a literal fragment, never input.
_UNATTENDED_SQL = ", ".join(f"'{kind}'" for kind in sorted(UNATTENDED_TRIGGERS))


# ── The in-process run event hub ─────────────────────────────────────────────


@dataclass(slots=True)
class RunHub:
    events: list[dict[str, Any]] = field(default_factory=list)
    queues: set[asyncio.Queue] = field(default_factory=set)
    done: bool = False


_HUBS: dict[str, RunHub] = {}
_ACTIVE_BY_WORKFLOW: dict[str, int] = {}
_ACTIVE_TOTAL = 0


def hub_for(run_id: str) -> RunHub | None:
    return _HUBS.get(run_id)


def _hub_emit(run_id: str, event: dict[str, Any]) -> None:
    hub = _HUBS.get(run_id)
    if hub is None:
        return
    event = {**event, "ts": time.time()}
    hub.events.append(event)
    for queue in list(hub.queues):
        with contextlib.suppress(asyncio.QueueFull):  # unbounded queues
            queue.put_nowait(event)


def _hub_close(run_id: str) -> None:
    hub = _HUBS.get(run_id)
    if hub is None:
        return
    hub.done = True
    for queue in list(hub.queues):
        with contextlib.suppress(asyncio.QueueFull):
            queue.put_nowait(None)  # sentinel: stream over

    def _expire() -> None:
        # Identity-guarded: a resumed run replaces the hub before the TTL
        # fires, and the new hub must not be popped by the old timer.
        if _HUBS.get(run_id) is hub:
            _HUBS.pop(run_id, None)

    asyncio.get_running_loop().call_later(HUB_TTL_SECS, _expire)


# ── Real NodeServices ────────────────────────────────────────────────────────


async def _run_agent_node(agent: str, message: str, model: str | None) -> str:
    try:
        from orchestrator.executor import AgentRunError, run_agent
    except Exception as exc:  # pragma: no cover - orchestrator is a dep
        raise NodeExecutionError("orchestrator unavailable") from exc
    payload: dict[str, Any] = {
        "message": message,
        "mode": "sub_task",
        "source": "workflow",
    }
    try:
        result = await run_agent(agent, payload, model=model)
    except AgentRunError as exc:
        raise NodeExecutionError(f"agent '{agent}' failed: {exc}") from exc
    text_out = str(result.get("result") or result.get("answer") or "")
    if not text_out:
        raise NodeExecutionError(f"agent '{agent}' returned no output")
    return text_out


async def _get_module_code(module_id: str) -> str | None:
    db = await _get_db()
    try:
        row = (
            await db.execute(
                text("SELECT code FROM workflow_modules WHERE id = :id AND status = 'ready'"),
                {"id": module_id},
            )
        ).fetchone()
    finally:
        await db.close()
    return row.code if row is not None else None


def _pm_task_updater(workflow_id: str) -> Any:
    """The Projects write seam, with this workflow's identity bound in.

    The import is inside the closure so the workflows package gains no
    import-time dependency on an app package, and so a deployment without
    Projects fails the one node that needs it rather than the whole engine.
    """

    async def _update(task_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        try:
            from gateway.routes.projects.automation import (
                TaskPatchError,
                apply_task_patch,
                workflow_actor,
            )
        except Exception as exc:  # pragma: no cover — Projects ships with the gateway
            raise NodeExecutionError("the Projects app is not available") from exc
        db = await _get_db()
        try:
            result = await apply_task_patch(
                db, task_id, fields, actor=workflow_actor(workflow_id),
            )
            await db.commit()
            return result
        except TaskPatchError as exc:
            raise NodeExecutionError(str(exc)) from exc
        finally:
            await db.close()

    return _update


async def _workflow_organization(db: Any, workflow_id: str) -> str:
    """The tenant a workflow's unattended writes belong to. **A stored fact.**

    ⚠️ **The `workflows` table has no `organization_id` column today** —
    checked against `infra/postgres/132_workflows.sql` and against a live
    catalog, not inferred: that column exists only in the unapplied
    `generated/01_add_columns.sql` (H3 phase 1). So the tenant is resolved the
    way `routes/crm/auto_lead._owner_organization` resolves the mailbox
    owner's: the workflow's ``owner_email`` through ``app_user``. One shape for
    "which tenant does this background unit act for", not two. When H3 phase 1
    lands, this becomes a one-column read on the workflow row and this
    function is where that change goes.

    Reads on the CALLER's (unbound) session because it is what DECIDES the
    tenant — the same ordering identity resolution has on the request path.
    **Raises rather than returning None**: a workflow whose owner has no
    organization must fail its run loudly, never sweep "the usual" tenant
    (`saas_multitenancy_handover.md` §5 rule 3 — fail closed, everywhere).
    """
    row = (
        await db.execute(
            text(
                "SELECT au.organization_id FROM workflows w "
                "JOIN app_user au ON lower(au.email) = lower(w.owner_email) "
                "WHERE w.id = CAST(:wid AS uuid)"
            ),
            {"wid": workflow_id},
        )
    ).fetchone()
    org = getattr(row, "organization_id", None) if row is not None else None
    if not org:
        raise NodeExecutionError(
            f"workflow {workflow_id} has no resolvable organization "
            f"(owner_email -> app_user.organization_id) — an unattended sweep "
            f"cannot choose a tenant"
        )
    return str(org)


def _pm_lifecycle_sweeper(workflow_id: str) -> Any:
    """The Projects lifecycle sweep (WS-27z), identity AND tenant bound in.

    Mirrors ``_pm_task_updater`` for the identity half — closure import so the
    workflows package gains no import-time dependency on an app package, the
    workflow's ``system:workflow:<id>`` actor bound in, one transaction around
    the whole sweep.

    ⚠️ **WS-27aa / H4 — this one is no longer an unbound background site.**
    ``run_lifecycle_sweep`` used to walk `pm_projects` with no tenant
    predicate, so one workflow's schedule archived and closed every customer's
    work. It now takes a required ``organization_id`` and refuses without one,
    and the tenant is resolved here from a stored fact
    (:func:`_workflow_organization`) and bound **explicitly** on the session
    the sweep writes through — never inherited from whoever happened to
    trigger the run, which is the inheritance H4 forbids. Two sessions, in
    this order and for the reason auto_lead has them:

    1. an unbound one to RESOLVE the tenant (it is the decision, so it cannot
       already be inside it), closed before anything is written;
    2. ``tenant_session(org)`` for the sweep itself, which issues the
       ``SET LOCAL app.tenant_id`` the RLS policies will read the moment H3's
       phase 4 lands, and commits on clean exit.
    """

    async def _sweep() -> dict[str, Any]:
        try:
            from gateway.routes.projects.automation import (
                run_lifecycle_sweep,
                workflow_actor,
            )
        except Exception as exc:  # pragma: no cover — Projects ships with the gateway
            raise NodeExecutionError("the Projects app is not available") from exc
        # H4: unbound ON PURPOSE — this session's whole job is to decide the
        # tenant, and it writes nothing.
        resolver = await _get_db()
        try:
            organization_id = await _workflow_organization(resolver, workflow_id)
        finally:
            await resolver.close()
        async with _tenant_session(organization_id) as db:
            return await run_lifecycle_sweep(
                db,
                organization_id=organization_id,
                actor=workflow_actor(workflow_id),
            )

    return _sweep


def build_node_services(actor: str, workflow_id: str = "") -> NodeServices:
    return NodeServices(
        run_agent=_run_agent_node,
        run_tool=execute_tool,
        get_module_code=_get_module_code,
        actor=actor,
        update_task=_pm_task_updater(workflow_id),
        run_lifecycle_sweep=_pm_lifecycle_sweeper(workflow_id),
    )


# ── Run lifecycle ────────────────────────────────────────────────────────────


async def load_version_serialized(
    db: Any,
    workflow_id: str,
    version: int,
) -> dict[str, Any] | None:
    row = (
        await db.execute(
            text(
                "SELECT serialized FROM workflow_versions WHERE workflow_id = :wid AND version = :v"
            ),
            {"wid": workflow_id, "v": version},
        )
    ).fetchone()
    return parse_jsonb(row.serialized, None) if row is not None else None


async def start_run(
    *,
    workflow_id: str,
    workflow_name: str,
    version: int,
    serialized: dict[str, Any],
    trigger_kind: str,
    trigger_payload: dict[str, Any],
    variables: dict[str, Any] | None = None,
    started_by: str = "",
) -> str:
    """Create the run row + hub entry and launch the supervised task."""
    global _ACTIVE_TOTAL
    if _ACTIVE_TOTAL >= MAX_CONCURRENT_RUNS:
        raise RunRejected("the platform run concurrency limit is reached — retry shortly")
    if _ACTIVE_BY_WORKFLOW.get(workflow_id, 0) >= MAX_CONCURRENT_RUNS_PER_WORKFLOW:
        raise RunRejected("this workflow already has the maximum concurrent runs")

    run_id = str(uuid.uuid4())
    db = await _get_db()
    try:
        await db.execute(
            text(
                """INSERT INTO workflow_runs
                   (id, workflow_id, workflow_name, version, trigger_kind,
                    trigger_payload, status, started_by)
                   VALUES (:id, :wid, :name, :v, :tk,
                           :tp ::jsonb, 'running', :by)"""
            ),
            {
                "id": run_id,
                "wid": workflow_id,
                "name": workflow_name,
                "v": version,
                "tk": trigger_kind,
                "tp": json.dumps(trigger_payload, default=str),
                "by": started_by,
            },
        )
        await db.commit()
    finally:
        await db.close()

    _HUBS[run_id] = RunHub()
    _ACTIVE_TOTAL += 1
    _ACTIVE_BY_WORKFLOW[workflow_id] = _ACTIVE_BY_WORKFLOW.get(workflow_id, 0) + 1
    asyncio.get_running_loop().create_task(
        _execute_run(
            run_id=run_id,
            workflow_id=workflow_id,
            workflow_name=workflow_name,
            serialized=serialized,
            trigger_payload=trigger_payload,
            variables=variables or {},
            started_by=started_by,
            trigger_kind=trigger_kind,
        ),
        name=f"workflow-run:{run_id}",
    )
    return run_id


class RunRejected(Exception):
    """The run was refused before starting (concurrency limits)."""


async def record_skipped_run(
    *,
    workflow_id: str,
    workflow_name: str,
    version: int,
    trigger_kind: str,
    trigger_payload: dict[str, Any],
    reason: str,
) -> str | None:
    """Write a terminal run row for a trigger that fired but could not run.

    The schedule scanner CAS-claims ``last_fired_at`` *before* it starts the
    run, because the claim is what stops two workers double-firing one tick.
    That ordering means a tick refused afterwards — the workflow is already at
    its concurrency cap, or its published version has gone missing — is
    consumed and never re-offered. Without this row the maker sees a gap in run
    history with no explanation, which is the single most corrosive thing an
    automation tool can do.

    Recorded as ``cancelled``, not ``failed``: the workflow did not break, the
    platform declined to start it. That distinction is load-bearing — the R2
    auto-disable policy counts consecutive *failures*, and being busy must
    never take a healthy automation offline.
    """
    run_id = str(uuid.uuid4())
    try:
        db = await _get_db()
        try:
            await db.execute(
                text(
                    """INSERT INTO workflow_runs
                       (id, workflow_id, workflow_name, version, trigger_kind,
                        trigger_payload, status, error, started_by, finished_at)
                       VALUES (:id, :wid, :name, :v, :tk, :tp ::jsonb,
                               'cancelled', :err, 'scheduler', now())"""
                ),
                {
                    "id": run_id,
                    "wid": workflow_id,
                    "name": workflow_name,
                    "v": version,
                    "tk": trigger_kind,
                    "tp": json.dumps(trigger_payload, default=str),
                    "err": reason[:500],
                },
            )
            await db.commit()
        finally:
            await db.close()
    except Exception as exc:  # pragma: no cover - best effort bookkeeping
        _log.warning(
            "workflows.skipped_run_persist_failed",
            workflow_id=workflow_id,
            error=str(exc)[:160],
        )
        return None
    _log.warning(
        "workflows.tick_skipped",
        workflow_id=workflow_id,
        workflow_name=workflow_name,
        reason=reason[:160],
    )
    publish_workflow_activity(
        workflow_id,
        workflow_name,
        phase="end",
        run_id=run_id,
        status="cancelled",
        reason=reason[:160],
    )
    return run_id


async def _execute_run(
    *,
    run_id: str,
    workflow_id: str,
    workflow_name: str,
    serialized: dict[str, Any],
    trigger_payload: dict[str, Any],
    variables: dict[str, Any],
    started_by: str,
    # Deliberately not defaulted: the auto-disable policy only counts
    # unattended kinds, so a call site that forgets to pass this would make the
    # policy silently never fire. Required means the compiler asks instead.
    trigger_kind: str,
    precomputed: dict[str, Any] | None = None,
    resolved_approvals: set[str] | None = None,
    elapsed_waits: set[str] | None = None,
) -> None:
    global _ACTIVE_TOTAL
    actor = f"workflow:{workflow_name or workflow_id}"
    publish_workflow_activity(
        workflow_id,
        workflow_name,
        user=started_by or None,
        phase="start",
        run_id=run_id,
    )
    _hub_emit(run_id, {"event": "run", "status": "running"})

    def emit(node_id: str, status: str, detail: dict[str, Any]) -> None:
        _hub_emit(
            run_id,
            {
                "event": "node",
                "node_id": node_id,
                "status": status,
                **_safe_detail(detail),
            },
        )

    status = "failed"
    error: str | None = None
    try:
        outcome = await execute_workflow(
            serialized,
            trigger_payload,
            build_node_services(actor, workflow_id),
            variables=variables,
            emit=emit,
            precomputed=precomputed,
            resolved_approvals=resolved_approvals,
            elapsed_waits=elapsed_waits,
        )
        status, error = outcome.status, outcome.error
        await _finish_run(
            run_id,
            status=status,
            error=error,
            variables=outcome.state,
            node_results=outcome.node_results,
            outputs=outcome.outputs,
        )
        if status == "paused" and outcome.paused_nodes:
            node_id = outcome.paused_nodes[0]
            await _hold_at_gate(
                run_id=run_id,
                workflow_id=workflow_id,
                workflow_name=workflow_name,
                node_id=node_id,
                serialized=serialized,
                trigger_payload=trigger_payload,
                variables=variables,
                started_by=started_by,
                resume_at=outcome.wait_until.get(node_id),
            )
    except Exception as exc:  # engine bugs must still close the run out
        error = f"{type(exc).__name__}: {exc}"[:500]
        _log.warning("workflows.run_crashed", run_id=run_id, error=error)
        await _finish_run(
            run_id,
            status="failed",
            error=error,
            variables={},
            node_results={},
            outputs=[],
        )
    finally:
        _ACTIVE_TOTAL = max(0, _ACTIVE_TOTAL - 1)
        remaining = _ACTIVE_BY_WORKFLOW.get(workflow_id, 1) - 1
        if remaining <= 0:
            _ACTIVE_BY_WORKFLOW.pop(workflow_id, None)
        else:
            _ACTIVE_BY_WORKFLOW[workflow_id] = remaining
        _hub_emit(run_id, {"event": "run", "status": status, "error": error})
        _hub_close(run_id)
        publish_workflow_activity(
            workflow_id,
            workflow_name,
            user=started_by or None,
            phase="end",
            run_id=run_id,
            status=status,
        )
        await evaluate_automation_health(
            workflow_id,
            workflow_name,
            trigger_kind=trigger_kind,
            status=status,
        )


def _safe_detail(detail: dict[str, Any]) -> dict[str, Any]:
    """Keep hub/SSE events small: clamp big node outputs to a preview."""
    out = dict(detail)
    output = out.get("output")
    if output is not None:
        try:
            encoded = json.dumps(output, default=str)
        except (TypeError, ValueError):
            encoded = str(output)
        if len(encoded) > 8000:
            out["output"] = {"_truncated": True, "preview": encoded[:8000]}
    return out


async def _finish_run(
    run_id: str,
    *,
    status: str,
    error: str | None,
    variables: dict[str, Any],
    node_results: dict[str, Any],
    outputs: list[Any],
) -> None:
    try:
        db = await _get_db()
        try:
            # A paused run has not finished — finished_at stays NULL until
            # the resumed execution reaches a terminal status.
            finished = "now()" if status != "paused" else "NULL"
            await db.execute(
                text(
                    f"""UPDATE workflow_runs SET
                         status = :status, error = :error,
                         variables = :variables ::jsonb,
                         node_results = :node_results ::jsonb,
                         output = :output ::jsonb,
                         finished_at = {finished}
                       WHERE id = :id"""
                ),
                {
                    "id": run_id,
                    "status": status,
                    "error": error,
                    "variables": json.dumps(variables, default=str),
                    "node_results": json.dumps(node_results, default=str),
                    "output": json.dumps(outputs, default=str),
                },
            )
            await db.commit()
        finally:
            await db.close()
    except Exception as exc:  # pragma: no cover - best effort persistence
        _log.warning("workflows.finish_persist_failed", run_id=run_id, error=str(exc)[:160])


async def reconcile_orphaned_runs() -> int:
    """Startup sweep (spec §3.3 honesty): mark previous-process runs failed.

    Runs execute as supervised asyncio tasks inside the gateway process, so a
    row still ``running`` when this process boots belonged to a process that
    died — it can never finish. ``runs.py`` already patches such rows lazily
    per read; this sweep persists the truth once at startup so lists, the
    F13 agent tools, and anything else reading the table agree immediately.
    ``paused`` rows are deliberately untouched: resume rebuilds everything
    from the pause snapshot, so they legitimately survive restarts.

    WS-29 launch-defang kill-switch (default ON), same flag as the cron scanner
    — WORKFLOW_SCHEDULER_ENABLED gates the whole workflow scheduling subsystem,
    this reconciler included. The RLS-cutover runbook sets it false so neither
    writes UNBOUND under FORCE ROW LEVEL SECURITY (the sweep is out of launch
    scope and not yet tenant-bound, H4 slice 6c). The gate lives HERE, inside
    the function — never as an ``if`` at the gateway call site. Returns 0 (no
    rows reconciled) when disabled.
    """
    if os.getenv("WORKFLOW_SCHEDULER_ENABLED", "").strip().lower() in {
        "0", "false", "no", "off",
    }:
        _log.info("workflows.reconcile_disabled")
        return 0
    try:
        db = await _get_db()
        try:
            result = await db.execute(
                text(
                    """UPDATE workflow_runs SET
                         status = 'failed',
                         error = COALESCE(error, 'interrupted by a platform restart'),
                         finished_at = now()
                       WHERE status = 'running'"""
                ),
            )
            await db.commit()
            count = int(result.rowcount or 0)
        finally:
            await db.close()
    except Exception as exc:  # best effort — reads still self-heal lazily
        _log.warning("workflows.reconcile_failed", error=str(exc)[:160])
        return 0
    if count:
        _log.info("workflows.orphaned_runs_reconciled", count=count)
    return count


# ── Automation health (spec R2 — silent drift) ───────────────────────────────


async def evaluate_automation_health(
    workflow_id: str,
    workflow_name: str,
    *,
    trigger_kind: str,
    status: str,
) -> bool:
    """Disable a published workflow whose unattended runs keep failing.

    Spec R2 asks for a "disabled-on-repeated-failure policy with notification"
    because a published workflow keeps firing on its schedule long after the
    business around it moved on — and nobody reads a run list that has been
    green for six months. ``AUTO_DISABLE_AFTER`` consecutive failures from
    unattended triggers is the platform admitting the automation is broken and
    stopping it, rather than failing loudly into an empty room forever.

    Three deliberate narrowings keep this from firing on a working system:

    - **Unattended triggers only** (``UNATTENDED_TRIGGERS``). A maker debugging
      with the Run button must never disable production.
    - **Consecutive**, derived from ``workflow_runs`` rather than a counter
      column: one success breaks the streak with no bookkeeping to get wrong,
      and the number always matches the history a human reads.
    - **Only runs after ``health_since``**, which publish/rollback/enable reset.
      Without that window a re-enabled workflow would re-disable on its next
      failure, since the old failures are still the newest rows.

    Notification is in-product: the reason is persisted on the workflow (the
    gallery and editor show it), a warning is logged, and the activity feed
    gets a ``disabled`` event so it surfaces in /observability. Outward
    notification (email the owner) is an outward write and belongs to the
    Action Broker path, not here.

    Returns True when this call is the one that disabled the workflow.
    """
    if status != "failed" or trigger_kind not in UNATTENDED_TRIGGERS:
        return False
    reason = (
        f"Auto-disabled after {AUTO_DISABLE_AFTER} consecutive failed runs "
        "from unattended triggers. Fix the workflow, then re-enable it."
    )
    try:
        db = await _get_db()
        try:
            rows = (
                await db.execute(
                    text(
                        f"""SELECT r.status FROM workflow_runs r
                              JOIN workflows w ON w.id = r.workflow_id
                             WHERE r.workflow_id = :wid
                               AND w.status = 'published'
                               AND r.trigger_kind IN ({_UNATTENDED_SQL})
                               AND r.status IN ('succeeded', 'failed', 'cancelled')
                               AND (w.health_since IS NULL
                                    OR r.started_at > w.health_since)
                             ORDER BY r.started_at DESC
                             LIMIT :limit"""
                    ),
                    {"wid": workflow_id, "limit": AUTO_DISABLE_AFTER},
                )
            ).fetchall()
            if len(rows) < AUTO_DISABLE_AFTER or any(r.status != "failed" for r in rows):
                return False
            # CAS on status: two runs failing at once, or a human disabling
            # concurrently, must produce exactly one disable.
            result = await db.execute(
                text(
                    """UPDATE workflows SET status = 'disabled',
                           disabled_reason = :reason, disabled_at = now(),
                           updated_at = now()
                       WHERE id = :id AND status = 'published'"""
                ),
                {"id": workflow_id, "reason": reason},
            )
            await db.commit()
            if not result.rowcount:
                return False
        finally:
            await db.close()
    except Exception as exc:  # a health check must never fail a run
        _log.warning(
            "workflows.health_check_failed",
            workflow_id=workflow_id,
            error=str(exc)[:160],
        )
        return False
    _log.warning(
        "workflows.auto_disabled",
        workflow_id=workflow_id,
        workflow_name=workflow_name,
        consecutive_failures=AUTO_DISABLE_AFTER,
    )
    publish_workflow_activity(
        workflow_id,
        workflow_name,
        phase="disabled",
        status="auto_disabled",
        reason=reason,
        consecutive_failures=AUTO_DISABLE_AFTER,
    )
    return True


# ── Approval pause / resume (spec F11 — rides the Action Broker inbox) ───────


async def _hold_at_gate(
    *,
    run_id: str,
    workflow_id: str,
    workflow_name: str,
    node_id: str,
    serialized: dict[str, Any],
    trigger_payload: dict[str, Any],
    variables: dict[str, Any],
    started_by: str,
    resume_at: float | None = None,
) -> None:
    """Persist the pause; for an APPROVAL gate, also file the inbox proposal.

    Approval (``resume_at`` is None): the proposal (action
    ``workflow.resume_run``) lands in ``pending_actions`` exactly like any
    outward write — the operator sees it at /approvals and approving it fires
    this package's registered broker handler, which resumes the run.

    Long wait (``resume_at`` set): nobody decides anything, so no proposal is
    filed — the snapshot simply carries a deadline and the schedule scanner
    resumes the run once it passes. Either way everything a resume needs
    travels in the snapshot, so even a draft test run (no published version to
    reload) resumes cleanly, and the pause outlives a gateway restart.
    """
    node_label = next(
        (
            str(b.get("label") or node_id)
            for b in serialized.get("blocks", [])
            if str(b.get("id")) == node_id
        ),
        node_id,
    )
    is_wait = resume_at is not None
    action_id: str | None = None
    if not is_wait:
        try:
            from action_broker.broker import AuthorityTier, propose, submit

            proposal = propose(
                actor=f"workflow:{workflow_name or workflow_id}",
                action="workflow.resume_run",
                target=f"workflow_run:{run_id}",
                payload={
                    "run_id": run_id,
                    "workflow_id": workflow_id,
                    "workflow_name": workflow_name,
                    "node_id": node_id,
                    "node_label": node_label,
                    "requested_by": started_by,
                },
                authority=AuthorityTier.SUGGEST,  # always NEEDS_APPROVAL
                destructive=False,
            )
            result = await submit(proposal)
            action_id = str(result.get("action_id") or "") or None
        except Exception as exc:
            _log.warning("workflows.approval_propose_failed", run_id=run_id, error=str(exc)[:160])
    try:
        db = await _get_db()
        try:
            await db.execute(
                text(
                    """INSERT INTO workflow_run_pauses
                       (run_id, node_id, snapshot, reason, status)
                       VALUES (:run_id, :node_id, :snapshot ::jsonb,
                               :reason, 'pending')"""
                ),
                {
                    "run_id": run_id,
                    "node_id": node_id,
                    "reason": "wait" if is_wait else "approval",
                    "snapshot": json.dumps(
                        {
                            "serialized": serialized,
                            "trigger_payload": trigger_payload,
                            "variables": variables,
                            "workflow_id": workflow_id,
                            "workflow_name": workflow_name,
                            "started_by": started_by,
                            "action_id": action_id,
                            "resume_at": resume_at,
                        },
                        default=str,
                    ),
                },
            )
            await db.commit()
        finally:
            await db.close()
    except Exception as exc:  # pragma: no cover - best effort persistence
        _log.warning("workflows.pause_persist_failed", run_id=run_id, error=str(exc)[:160])


async def resume_run(run_id: str, *, resumed_by: str = "approver") -> dict[str, Any]:
    """Resume a paused run: replay completed nodes from stored outputs and
    let the gate node through.

    Two callers, one path: the broker handler when a human approves, and the
    schedule scanner when a long wait's deadline passes. The pause row's
    ``reason`` decides which gate is cleared — an elapsed wait must not also
    clear an approval further down the graph."""
    global _ACTIVE_TOTAL
    db = await _get_db()
    try:
        run = (
            await db.execute(
                text("SELECT * FROM workflow_runs WHERE id = :id"),
                {"id": run_id},
            )
        ).fetchone()
        if run is None or run.status != "paused":
            return {
                "ok": False,
                "error": f"run {run_id} is not paused" if run is not None else f"no run {run_id}",
            }
        pause = (
            await db.execute(
                text(
                    "SELECT * FROM workflow_run_pauses "
                    "WHERE run_id = :id AND status = 'pending' "
                    "ORDER BY created_at DESC LIMIT 1"
                ),
                {"id": run_id},
            )
        ).fetchone()
        if pause is None:
            return {"ok": False, "error": f"run {run_id} has no pending pause"}
        snapshot = parse_jsonb(pause.snapshot, {}) or {}
        await db.execute(
            text(
                "UPDATE workflow_run_pauses SET status = 'resolved', "
                "resolved_at = now() WHERE id = :id"
            ),
            {"id": str(pause.id)},
        )
        await db.execute(
            text("UPDATE workflow_runs SET status = 'running' WHERE id = :id"),
            {"id": run_id},
        )
        await db.commit()
        node_results = parse_jsonb(run.node_results, {}) or {}
    finally:
        await db.close()

    gate_node = str(pause.node_id)
    is_wait = str(getattr(pause, "reason", "approval") or "approval") == "wait"
    serialized = snapshot.get("serialized") or {}
    precomputed = {
        nid: res.get("output")
        for nid, res in node_results.items()
        if isinstance(res, dict) and res.get("status") == "ok"
    }
    workflow_id = str(snapshot.get("workflow_id") or run.workflow_id)
    _HUBS[run_id] = RunHub()
    _ACTIVE_TOTAL += 1
    _ACTIVE_BY_WORKFLOW[workflow_id] = _ACTIVE_BY_WORKFLOW.get(workflow_id, 0) + 1
    asyncio.get_running_loop().create_task(
        _execute_run(
            run_id=run_id,
            workflow_id=workflow_id,
            workflow_name=str(snapshot.get("workflow_name") or run.workflow_name),
            serialized=serialized,
            trigger_payload=snapshot.get("trigger_payload") or {},
            variables=snapshot.get("variables") or {},
            started_by=resumed_by,
            # The gate was crossed, but the run still belongs to whatever
            # triggered it — a scheduled run that fails after its approval
            # counts toward the health streak like any other.
            trigger_kind=str(getattr(run, "trigger_kind", "manual") or "manual"),
            precomputed=precomputed,
            resolved_approvals=set() if is_wait else {gate_node},
            elapsed_waits={gate_node} if is_wait else set(),
        ),
        name=f"workflow-resume:{run_id}",
    )
    return {"ok": True, "resumed": True, "run_id": run_id, "node_id": gate_node}

# ── Programmatic entry points (F13: workflows as agent tools) ────────────────


async def list_published_workflows() -> list[dict[str, Any]]:
    """Published workflows an agent (or API caller) may trigger."""
    db = await _get_db()
    try:
        rows = (
            await db.execute(
                text(
                    "SELECT id, name, description, latest_version FROM workflows "
                    "WHERE status = 'published' AND latest_version IS NOT NULL "
                    "ORDER BY name"
                ),
            )
        ).fetchall()
    finally:
        await db.close()
    return [
        {
            "id": str(r.id),
            "name": r.name,
            "description": r.description or "",
            "version": int(r.latest_version),
        }
        for r in rows
    ]


async def run_published_workflow(
    ref: str, payload: dict[str, Any], *, started_by: str
) -> dict[str, Any]:
    """Start a published workflow by name (case-insensitive) or id.

    The programmatic twin of the manual Run button — same entrypoint, same
    concurrency caps, trigger kind ``api``. Returns ``{run_id, …}`` or
    ``{error}`` (ambiguous/unknown refs are reported, never guessed).
    """
    db = await _get_db()
    try:
        rows = (
            await db.execute(
                text(
                    """SELECT id, name, latest_version, variables FROM workflows
                       WHERE status = 'published' AND latest_version IS NOT NULL
                         AND (lower(name) = lower(:ref) OR id::text = :ref)"""
                ),
                {"ref": ref.strip()},
            )
        ).fetchall()
        if not rows:
            return {"error": f"no published workflow named or with id '{ref}'"}
        if len(rows) > 1:
            return {
                "error": f"'{ref}' is ambiguous — matching workflows: "
                + ", ".join(f"{r.name} ({r.id})" for r in rows)
            }
        row = rows[0]
        serialized = await load_version_serialized(db, str(row.id), int(row.latest_version))
    finally:
        await db.close()
    if serialized is None:
        return {"error": f"published version missing for workflow '{row.name}'"}
    try:
        run_id = await start_run(
            workflow_id=str(row.id),
            workflow_name=row.name,
            version=int(row.latest_version),
            serialized=serialized,
            trigger_kind="api",
            trigger_payload=payload,
            variables=parse_jsonb(row.variables, {}),
            started_by=started_by,
        )
    except RunRejected as exc:
        return {"error": str(exc)}
    return {
        "run_id": run_id,
        "workflow_id": str(row.id),
        "workflow_name": row.name,
        "version": int(row.latest_version),
    }


async def run_summary(run_id: str) -> dict[str, Any]:
    """Status + outputs for one run (the polling half of workflow-as-tool)."""
    db = await _get_db()
    try:
        row = (
            await db.execute(
                text(
                    "SELECT status, error, output, workflow_name "
                    "FROM workflow_runs WHERE id = :id"
                ),
                {"id": run_id},
            )
        ).fetchone()
    finally:
        await db.close()
    if row is None:
        return {"error": f"no run {run_id}"}
    status = row.status
    if status == "running" and hub_for(run_id) is None:
        status = "failed"
    return {
        "run_id": run_id,
        "workflow_name": row.workflow_name,
        "status": status,
        "error": row.error,
        "output": parse_jsonb(row.output, None),
    }
