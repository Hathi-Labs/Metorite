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
# this module belongs to the UNATTENDED run lifecycle — run rows written by
# supervised background tasks that outlive the request that may have started
# them (`start_run`, `_finish_run`, `_hold_at_gate`), the startup sweep
# (`reconcile_orphaned_runs`), the health policy
# (`evaluate_automation_health`), and the programmatic/agent entry points
# (F13). Several are DUAL-USE — reached from a member's manual Run AND from
# the scheduler/webhook/event paths (`start_run`, `resume_run`) — and the H2
# rule for that is LEAVE: a run must not behave differently depending on who
# happened to trigger it, and inheriting the ambient request tenant is exactly
# what H4 forbids. They stay on the unbound `get_db()` until H4 threads an
# explicit tenant (`tenant_session(org_id)` from the workflow row) through the
# run lifecycle.
#
# ⚠️ ONE EXCEPTION, and it is done: `_pm_lifecycle_sweeper` (WS-27aa). It
# opens `_tenant_session(org)` for the sweep — an EXPLICIT tenant from a stored
# fact, which is what H4 asks for, not the ambient inheritance it forbids.
#
# ⚠️ A SECOND EXCEPTION (2026-10-15): the F13 agent entry points. The agent
# tools pass the run's organization, which the orchestrator takes from the run
# binding. `list_published_workflows`, `run_published_workflow` and
# `run_summary` read inside `_tenant_session(org)`, and refuse with no org.
# A run they start carries that org, so `start_run`, `_finish_run` and
# `_hold_at_gate` write it through `_run_session(org)`. An approval resumes
# through `resume_run(org)`, with the approver's org. A long wait refuses at
# the start, because the wait scan is unbound. A run with no org (manual,
# schedule, webhook, event, the wait scan) keeps the unbound path above.
# Fence: `tests/unit/test_workflows_tool_rls_r8.py`.
#
# ⚠️ A THIRD EXCEPTION (H-328 part (a), 2026-11-02): the node services. A run
# that carries an organization binds it for the module read, the `pm_task`
# write, the lifecycle sweep and the `notify.whatsapp` owner read
# (`build_node_services`). The org is the run's own stored fact, never the
# ambient tenant and never input. ONE EXCEPTION: the approval door opens its
# read with the approver's `current_tenant()` (`resume_run`). RLS makes that
# org equal the stored org, and the resumed nodes take the org from the run
# row. A run with no org keeps the old path until H4 slice 6c. That path is
# `_run_session(None)` for the module read and the task write, and the unbound
# owner resolver for the other two.
#
# ⚠️ A task write has no member check (`_member_blind_write_nodes`). So
# `run_published_workflow` and `resume_run` refuse a `pm_task` or `pm_lifecycle`
# node, and each stays refused until a run checks a verified member.
# Fence: `tests/unit/test_workflows_lifecycle_rls_r8.py`.
from acb_common.db import TenantUnbound
from gateway.routes.workflows.core import (
    _get_db,
    _log,
    _tenant_session,
    parse_jsonb,
    publish_workflow_activity,
)
from gateway.routes.workflows.engine.handlers import (
    WAIT_INLINE_MAX_SECONDS,
    NodeExecutionError,
    NodeServices,
    resolve_wait_seconds,
)
from gateway.routes.workflows.engine.runner import execute_workflow
from gateway.routes.workflows.tools import (
    NOTIFY_WHATSAPP,
    execute_tool,
    notify_owner_whatsapp,
)
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


#: Who pays for a workflow's model calls, from OUR tables only (H-152).
#:
#: One read answers both halves. ``owner_email`` is the workflow's owner, an
#: ACTIVE member with an organization. ``ambient_email`` is the member already
#: bound on this run (the person who pressed Run), and it comes back only when
#: that person is an active member of the OWNER's organization. So a run can
#: never bill a person outside the organization the workflow belongs to.
_SQL_WORKFLOW_BILLING_MEMBER = (
    "SELECT o.email AS owner_email, "
    "       (SELECT m.email FROM app_user m "
    "         WHERE lower(m.email) = lower(:ambient) "
    "           AND m.organization_id = o.organization_id "
    "           AND m.status = 'active') AS ambient_email "
    "  FROM workflows w "
    "  JOIN app_user o ON lower(o.email) = lower(w.owner_email) "
    " WHERE w.id = CAST(:wid AS uuid) "
    "   AND o.organization_id IS NOT NULL "
    "   AND o.status = 'active'"
)


async def _workflow_billing_member(workflow_id: str) -> str | None:
    """The member a workflow's agent node runs for. **A stored fact** (H-152).

    🔴 **A scheduled run had no member, and the Router will refuse it.** The
    agent node called ``run_agent`` with no ``session_user``. A manual run
    inherited the clicker from the request, but a schedule, a webhook or an
    event ran with nobody bound. Under the per-box deployment key the Console
    derives the organization from ``X-CC-Member``, and a call without one is a
    400.

    Two answers, in this order:

    1. **The member already bound on this run, if the session VERIFIED them**
       and they belong to the workflow's organization. That is the person who
       pressed Run or approved the gate, so the bill names them.
    2. **The workflow's owner.** A schedule has no person, and the owner is
       the one who set it running. A run with no organization takes its
       tenant from the same owner (``_workflow_organization``), so the bill
       and the writes name one organization.

    ⚠️ **Never a request body.** A webhook payload, an event body and a
    ``user_email`` claim all stay out, because the member decides which
    organization pays. An unverified ambient member is replaced by the owner.

    ⚠️ **``None`` when neither resolves, never a guess.** The node then runs
    as it did before this change. On the org-key arm that still bills the one
    organization, and on the deployment arm the Console refuses it loudly.

    ⚠️ **A TENANT-DISCOVERY read, so it is unbound on purpose.** It decides
    which organization pays, so it cannot run inside that organization's
    session. ``test_db_engine_seam.H2_TENANT_DISCOVERY_SITES`` names it. Once
    FORCE RLS covers ``app_user`` it returns zero rows, like every H4
    resolver, and this function then fails CLOSED to ``None`` (H-152).
    """
    ambient = ""
    try:
        from acb_common import get_run_context

        ctx = get_run_context()
        if ctx.get("member_verified") == "1":
            ambient = str(ctx.get("user") or "").strip()
    except Exception:
        ambient = ""
    if not workflow_id:
        return ambient or None

    try:
        db = await _get_db()
        try:
            row = (
                await db.execute(
                    text(_SQL_WORKFLOW_BILLING_MEMBER),
                    {"wid": workflow_id, "ambient": ambient},
                )
            ).fetchone()
        finally:
            await db.close()
    except Exception as exc:
        _log.warning(
            "workflows.billing_member_unresolved",
            workflow_id=workflow_id,
            error=str(exc)[:160],
        )
        return None
    if row is None:
        _log.warning("workflows.billing_member_unresolved", workflow_id=workflow_id)
        return None
    return str(row.ambient_email or row.owner_email)


def _agent_runner(workflow_id: str) -> Any:
    """The agent node's seam, with this workflow's billing member bound in."""

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
        # H-152: a server-derived member goes in as ``session_user``, the
        # keyword no request body can set. The executor then binds it
        # VERIFIED, so the Router names it and a cap may key on it.
        member = await _workflow_billing_member(workflow_id)
        try:
            # WS-63 PZ-2 (personalization.md §5.1): a workflow's output is
            # not one member's solo chat, so the node is never solo. The run
            # copies the context of what started it, and an explicit False
            # stops a solo chat's bit from reaching the node.
            result = await run_agent(
                agent, payload, model=model, session_user=member, solo_room=False,
            )
        except AgentRunError as exc:
            raise NodeExecutionError(f"agent '{agent}' failed: {exc}") from exc
        text_out = str(result.get("result") or result.get("answer") or "")
        if not text_out:
            raise NodeExecutionError(f"agent '{agent}' returned no output")
        return text_out

    return _run_agent_node


def _module_code_reader(organization_id: str | None) -> Any:
    """The module node's read, with the run's organization bound in.

    The read opens :func:`_run_session`, so a run sees only the modules of its
    own organization. A module of another organization reads as no row.
    """

    async def _get_module_code(module_id: str) -> str | None:
        async with _run_session(organization_id) as db:
            row = (
                await db.execute(
                    text("SELECT code FROM workflow_modules WHERE id = :id AND status = 'ready'"),
                    {"id": module_id},
                )
            ).fetchone()
        return row.code if row is not None else None

    return _get_module_code


def _pm_task_updater(workflow_id: str, organization_id: str | None) -> Any:
    """The Projects write seam, with the workflow's identity and the run's
    organization bound in.

    The import is inside the closure so the workflows package gains no
    import-time dependency on an app package, and so a deployment without
    Projects fails the one node that needs it rather than the whole engine.

    :func:`_run_session` commits on a clean exit. A task of another
    organization is not visible, so the patch reads it as ``Task not found``.
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
        try:
            async with _run_session(organization_id) as db:
                return await apply_task_patch(
                    db, task_id, fields, actor=workflow_actor(workflow_id),
                )
        except TaskPatchError as exc:
            raise NodeExecutionError(str(exc)) from exc

    return _update


async def _workflow_organization(db: Any, workflow_id: str) -> str:
    """The tenant of a run with no organization, from the workflow's owner.

    ⚠️ **This is the old path (H-328).** A run that carries an organization
    never comes here. Under FORCE RLS an unbound session cannot read this join.
    A fresh connection reads zero rows, so this function raises. A used
    connection holds an empty ``app.tenant_id``, so the policy cast raises
    ``DBAPIError`` (``invalid input syntax for type uuid: ""``). Either way,
    that run fails its sweep or notify node. H4 slice 6c deletes this function
    with its last caller.

    The owner goes through ``app_user``, as
    `routes/crm/auto_lead._owner_organization` resolves the mailbox owner's.

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


def _pm_lifecycle_sweeper(workflow_id: str, organization_id: str | None) -> Any:
    """The Projects lifecycle sweep (WS-27z), identity AND tenant bound in.

    Mirrors ``_pm_task_updater`` for the identity half — closure import so the
    workflows package gains no import-time dependency on an app package, the
    workflow's ``system:workflow:<id>`` actor bound in, one transaction around
    the whole sweep.

    ⚠️ **WS-27aa / H4 — this one is no longer an unbound background site.**
    ``run_lifecycle_sweep`` used to walk `pm_projects` with no tenant
    predicate, so one workflow's schedule archived and closed every customer's
    work. It now takes a required ``organization_id`` and refuses without one.
    The tenant is bound **explicitly** on the session the sweep writes
    through — never inherited from whoever happened to trigger the run, which
    is the inheritance H4 forbids.

    The tenant is the run's own organization (H-328 part (a)). A run with no
    organization keeps the old path, :func:`_resolve_workflow_organization`.
    ``tenant_session(org)`` issues ``SET LOCAL app.tenant_id`` for the sweep,
    and commits on clean exit.
    """

    async def _sweep() -> dict[str, Any]:
        try:
            from gateway.routes.projects.automation import (
                run_lifecycle_sweep,
                workflow_actor,
            )
        except Exception as exc:  # pragma: no cover — Projects ships with the gateway
            raise NodeExecutionError("the Projects app is not available") from exc
        org = organization_id or await _resolve_workflow_organization(workflow_id)
        async with _tenant_session(org) as db:
            return await run_lifecycle_sweep(
                db,
                organization_id=org,
                actor=workflow_actor(workflow_id),
            )

    return _sweep


async def _resolve_workflow_organization(workflow_id: str) -> str:
    """:func:`_workflow_organization` on its own unbound resolver session.

    H4: unbound ON PURPOSE — this session's whole job is to decide the
    tenant, and it writes nothing. Only a run with no organization comes
    here, from the lifecycle sweep or ``notify.whatsapp``."""
    resolver = await _get_db()
    try:
        return await _workflow_organization(resolver, workflow_id)
    finally:
        await resolver.close()


async def _workflow_owner(
    workflow_id: str, organization_id: str | None,
) -> tuple[str, str]:
    """The tenant and the owner's email of a workflow. **Stored facts.**

    The tenant is the run's own organization. A run with no organization
    keeps the old path, :func:`_resolve_workflow_organization`. The owner is
    then read in that tenant's session, so a workflow of another organization
    has no owner to read. Raises when either is missing."""
    org = organization_id or await _resolve_workflow_organization(workflow_id)
    async with _tenant_session(org) as db:
        owner = (
            await db.execute(
                text("SELECT owner_email FROM workflows WHERE id = CAST(:wid AS uuid)"),
                {"wid": workflow_id},
            )
        ).scalar()
    if not owner:
        raise NodeExecutionError(f"workflow {workflow_id} has no owner to tell")
    return org, str(owner).strip().lower()


def _tool_runner(workflow_id: str, organization_id: str | None) -> Any:
    """``execute_tool``, with the workflow's owner bound into the one step
    that tells a member: ``notify.whatsapp`` (WS-59 RE-5). The step's
    arguments never name the member."""

    async def _run(action: str, args: dict[str, Any], actor: str) -> dict[str, Any]:
        if action != NOTIFY_WHATSAPP:
            return await execute_tool(action, args, actor)
        if not workflow_id:
            raise NodeExecutionError("notify.whatsapp needs a saved workflow")
        org, owner = await _workflow_owner(workflow_id, organization_id)
        return await notify_owner_whatsapp(org, owner, workflow_id, args)

    return _run


def build_node_services(
    actor: str, workflow_id: str, organization_id: str | None,
) -> NodeServices:
    """The node seams of one run, bound to the run's organization.

    *organization_id* has no default, so a call site that forgets it fails at
    once. The value comes from the run (:func:`_execute_run`), never from the
    ambient tenant or from node input. ``None`` means that the run carries no
    organization, so it keeps the old path until H4 slice 6c.
    """
    return NodeServices(
        run_agent=_agent_runner(workflow_id),
        run_tool=_tool_runner(workflow_id, organization_id),
        get_module_code=_module_code_reader(organization_id),
        actor=actor,
        update_task=_pm_task_updater(workflow_id, organization_id),
        run_lifecycle_sweep=_pm_lifecycle_sweeper(workflow_id, organization_id),
    )


# ── Run lifecycle ────────────────────────────────────────────────────────────


@contextlib.asynccontextmanager
async def _run_session(organization_id: str | None) -> Any:
    """One session for one run-lifecycle step. It commits on a clean exit.

    A run that carries an organization uses
    ``_tenant_session(organization_id)``, for its run rows and for the module
    read and the task write of its nodes. Only the F13 agent entry points
    start such a run, with the org of the agent run. The org is never
    inherited from the ambient context, which is what H4 forbids. The one
    exception is the read at the approval door (:func:`resume_run`).

    A run with no organization keeps the unbound ``_get_db()`` (the H4 note at
    the top of this module). Under FORCE RLS that read finds no row on a fresh
    connection, and raises ``DBAPIError`` on a used one (``invalid input syntax
    for type uuid: ""``). That write fails on both. H4 slice 6c owns the
    conversion.
    """
    if organization_id:
        async with _tenant_session(organization_id) as db:
            yield db
        return
    db = await _get_db()
    try:
        yield db
        await db.commit()
    finally:
        await db.close()


def _long_wait_nodes(serialized: dict[str, Any]) -> list[str]:
    """Ids of the wait nodes in *serialized* that can pause the run.

    A wait of up to ``WAIT_INLINE_MAX_SECONDS`` runs inline and never pauses.
    A longer wait, or a duration that only the trigger data decides, can
    pause. Only the schedule scanner resumes such a pause, and its scan is
    not tenant-bound yet (H4 slice 6c). So the F13 entry point refuses these
    workflows rather than start a run that stays paused for good.
    """
    out: list[str] = []
    for block in serialized.get("blocks") or []:
        if str(block.get("type") or "") != "wait":
            continue
        try:
            seconds = resolve_wait_seconds(block.get("config") or {}, {})
        except NodeExecutionError:
            out.append(str(block.get("id")))
            continue
        if seconds > WAIT_INLINE_MAX_SECONDS:
            out.append(str(block.get("id")))
    return out


#: The node types that write ``pm_tasks`` with no member check.
_MEMBER_BLIND_WRITE_TYPES = frozenset({"pm_task", "pm_lifecycle"})

#: Why such a node does not run. It names no task, so it tells no one that a
#: private task exists.
_MEMBER_BLIND_REASON = "A run does not check which tasks a member can see."
#: What a run row and the approver read when a resume stops at such a node.
_RESUME_REFUSED = (
    "This run stopped at its gate, because a later node can change Projects "
    f"tasks. {_MEMBER_BLIND_REASON}"
)


def _member_blind_write_nodes(serialized: dict[str, Any]) -> list[str]:
    """Ids of the nodes in *serialized* that write tasks with no member check.

    A ``pm_task`` or ``pm_lifecycle`` node writes ``pm_tasks`` by primary key
    in the run's org. No step asks what the run's starter can see, and RLS
    binds only the tenant. So a member's agent could name another member's
    private task in the trigger payload, and the run would change that task.
    A fixed task id has the same gap, because the agent still picks the
    workflow. The sweep has it too, and it archives and closes tasks.

    :func:`run_published_workflow` and :func:`resume_run` refuse these nodes,
    whether the task id is a template or fixed. The refusal stays until a run
    checks a verified member. Parts (b) and (c) of H-328 must decide which
    member that is (``workflows_app.md``).
    """
    return [
        str(block.get("id"))
        for block in serialized.get("blocks") or []
        if str(block.get("type") or "") in _MEMBER_BLIND_WRITE_TYPES
    ]


def _require_org(organization_id: str | None) -> str:
    """The explicit org of an F13 entry point, or :class:`TenantUnbound`.

    ``_tenant_session`` falls back to the ambient tenant when it gets no
    argument. These entry points must name the org, so an empty value
    refuses here and never reaches that fallback.
    """
    org = str(organization_id or "").strip()
    if not org:
        raise TenantUnbound(
            "a workflow agent tool needs the run's organization — none was "
            "passed, so nothing is read and nothing runs"
        )
    return org


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
    organization_id: str | None = None,
) -> str:
    """Create the run row + hub entry and launch the supervised task.

    *organization_id* binds the run's writes (:func:`_run_session`). Only the
    F13 agent entry point passes it, from the agent run's binding.
    """
    global _ACTIVE_TOTAL
    if _ACTIVE_TOTAL >= MAX_CONCURRENT_RUNS:
        raise RunRejected("the platform run concurrency limit is reached — retry shortly")
    if _ACTIVE_BY_WORKFLOW.get(workflow_id, 0) >= MAX_CONCURRENT_RUNS_PER_WORKFLOW:
        raise RunRejected("this workflow already has the maximum concurrent runs")

    run_id = str(uuid.uuid4())
    async with _run_session(organization_id) as db:
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
            organization_id=organization_id,
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
    organization_id: str | None = None,
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

    # H-152: the WHOLE run acts for its billing member, not only the agent
    # node. A tool node can emit an event whose sink starts an agent run
    # (`projects/agent_dispatch.py`), and that run inherits this task's
    # member. Closed as the last act of the `finally` below. An unresolved
    # member binds nothing, so a manual run keeps its clicker as before.
    from acb_common import job_member_scope

    member_scope = contextlib.ExitStack()
    billing_member = await _workflow_billing_member(workflow_id)
    if billing_member:
        member_scope.enter_context(
            job_member_scope(billing_member, app="workflows", agent="workflows.automation")
        )

    status = "failed"
    error: str | None = None
    try:
        outcome = await execute_workflow(
            serialized,
            trigger_payload,
            build_node_services(actor, workflow_id, organization_id),
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
            organization_id=organization_id,
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
                organization_id=organization_id,
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
            organization_id=organization_id,
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
        try:
            await evaluate_automation_health(
                workflow_id,
                workflow_name,
                trigger_kind=trigger_kind,
                status=status,
            )
        finally:
            member_scope.close()


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
    organization_id: str | None = None,
) -> None:
    try:
        async with _run_session(organization_id) as db:
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
    organization_id: str | None = None,
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
        async with _run_session(organization_id) as db:
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
    except Exception as exc:  # pragma: no cover - best effort persistence
        _log.warning("workflows.pause_persist_failed", run_id=run_id, error=str(exc)[:160])


async def resume_run(
    run_id: str, *, resumed_by: str = "approver", organization_id: str | None = None,
) -> dict[str, Any]:
    """Resume a paused run: replay completed nodes from stored outputs and
    let the gate node through.

    Two callers, one path: the broker handler when a human approves, and the
    schedule scanner when a long wait's deadline passes. The pause row's
    ``reason`` decides which gate is cleared — an elapsed wait must not also
    clear an approval further down the graph.

    *organization_id* binds the read and the claim (:func:`_run_session`).
    The broker handler passes the approver's org. Under FORCE RLS a run of
    another org is not visible, so it reads as ``no run`` and nothing resumes.
    The schedule scanner passes no org yet (H4 slice 6c), so the agent entry
    point refuses a long wait.

    ⚠️ **The approval door is the one exception to "never the ambient tenant"
    and to "part (a) wakes nothing".** The handler takes the approver's
    ``current_tenant()`` for any paused run, whatever started it. RLS makes
    that org equal the stored org, so no run of another org leaks. The resumed
    run takes its org from the run row. So an approver who clears a run that
    paused before RLS now runs its module and notify nodes. A person approved
    that run, and nothing wakes without one.

    ⚠️ **A resume never reaches a task write.** A node of
    :func:`_member_blind_write_nodes` that has not run closes the run as
    ``cancelled`` and the pause as ``rejected``, as a rejected approval does.
    That is not ``failed``: the platform stopped the run, so the auto-disable
    policy does not count it. A node that already ran stays replayed, so a
    resume past it goes on.
    """
    global _ACTIVE_TOTAL
    async with _run_session(organization_id) as db:
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
        serialized = snapshot.get("serialized") or {}
        node_results = parse_jsonb(run.node_results, {}) or {}
        precomputed = {
            nid: res.get("output")
            for nid, res in node_results.items()
            if isinstance(res, dict) and res.get("status") == "ok"
        }
        blind = [n for n in _member_blind_write_nodes(serialized) if n not in precomputed]
        if blind:
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
                {"id": run_id, "error": _RESUME_REFUSED},
            )
        else:
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

    gate_node = str(pause.node_id)
    is_wait = str(getattr(pause, "reason", "approval") or "approval") == "wait"
    workflow_id = str(snapshot.get("workflow_id") or run.workflow_id)
    workflow_name = str(snapshot.get("workflow_name") or run.workflow_name)
    if blind:
        _log.warning("workflows.resume_refused", run_id=run_id, nodes=blind)
        publish_workflow_activity(
            workflow_id,
            workflow_name,
            user=resumed_by,
            phase="end",
            run_id=run_id,
            status="cancelled",
            reason=_RESUME_REFUSED,
        )
        return {"ok": False, "error": _RESUME_REFUSED, "run_id": run_id, "node_id": gate_node}
    run_org = getattr(run, "organization_id", None)
    _HUBS[run_id] = RunHub()
    _ACTIVE_TOTAL += 1
    _ACTIVE_BY_WORKFLOW[workflow_id] = _ACTIVE_BY_WORKFLOW.get(workflow_id, 0) + 1
    asyncio.get_running_loop().create_task(
        _execute_run(
            run_id=run_id,
            workflow_id=workflow_id,
            workflow_name=workflow_name,
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
            organization_id=str(run_org) if run_org else None,
        ),
        name=f"workflow-resume:{run_id}",
    )
    return {"ok": True, "resumed": True, "run_id": run_id, "node_id": gate_node}

# ── Programmatic entry points (F13: workflows as agent tools) ────────────────
#
# ⚠️ Each one takes the org EXPLICITLY and reads inside `_tenant_session(org)`
# (2026-10-15). `workflows` and `workflow_runs` have FORCE RLS, so the unbound
# `_get_db()` these used to open read ZERO rows in production, and an agent
# saw no workflow at all. The caller passes the org of the agent run, which
# the orchestrator takes from the run binding, never from tool input (R5,
# R11). An empty org raises `TenantUnbound` and reads nothing.


async def list_published_workflows(*, organization_id: str) -> list[dict[str, Any]]:
    """Published workflows of *organization_id* that an agent may trigger."""
    org = _require_org(organization_id)
    async with _tenant_session(org) as db:
        rows = (
            await db.execute(
                text(
                    "SELECT id, name, description, latest_version FROM workflows "
                    "WHERE status = 'published' AND latest_version IS NOT NULL "
                    # The id breaks a tie of the name: the chat pages this list.
                    "ORDER BY name, id"
                ),
            )
        ).fetchall()
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
    ref: str, payload: dict[str, Any], *, started_by: str, organization_id: str
) -> dict[str, Any]:
    """Start a published workflow of *organization_id* by name or id.

    The programmatic twin of the manual Run button — same entrypoint, same
    concurrency caps, trigger kind ``api``. Returns ``{run_id, …}`` or
    ``{error}`` (ambiguous/unknown refs are reported, never guessed). The
    name match is case-insensitive. The run carries the org, so its writes
    bind it too (:func:`_run_session`). A workflow that holds a node of
    :func:`_member_blind_write_nodes` is refused, so an agent writes no task.
    """
    org = _require_org(organization_id)
    async with _tenant_session(org) as db:
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
    if serialized is None:
        return {"error": f"published version missing for workflow '{row.name}'"}
    if _member_blind_write_nodes(serialized):
        return {
            "error": f"workflow '{row.name}' can change Projects tasks, so an "
            f"agent cannot start it yet. {_MEMBER_BLIND_REASON} Nothing ran."
        }
    long_waits = _long_wait_nodes(serialized)
    if long_waits:
        return {
            "error": f"workflow '{row.name}' has a wait node that can pause the "
            f"run ({', '.join(long_waits)}). An agent cannot start it yet, "
            "because nothing can resume that pause for this organization."
        }
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
            organization_id=org,
        )
    except RunRejected as exc:
        return {"error": str(exc)}
    return {
        "run_id": run_id,
        "workflow_id": str(row.id),
        "workflow_name": row.name,
        "version": int(row.latest_version),
    }


async def run_summary(run_id: str, *, organization_id: str) -> dict[str, Any]:
    """Status + outputs for one run of *organization_id* (the polling half).

    A run of another org reads as ``no run``: RLS hides the row.
    """
    org = _require_org(organization_id)
    async with _tenant_session(org) as db:
        row = (
            await db.execute(
                text(
                    "SELECT status, error, output, workflow_name "
                    "FROM workflow_runs WHERE id = :id"
                ),
                {"id": run_id},
            )
        ).fetchone()
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
