"""Projects · assignment IS dispatch — WS-27f / `workflows_app.md` §13 U7.

Spec: `project-docs/specs/project_management_app.md` §6.4.

Assigning a task to `agent:<name>` starts an agent run. Not a separate
"delegate to AI" button, not a parallel feature with its own field — the same
gesture that hands work to a colleague, because D-PM-4 put both species in one
assignee vocabulary and this is where that stops being a schema note.

**Event-driven, never called from the handler.** `PUT /tasks/{id}/assignees`
emits `pm.task.assigned` and returns; this module is a *sink* on that event,
registered beside the workflows dispatcher at startup. That is Paca's shape
(research §5): the HTTP handler never calls the agent runtime, so a slow or
broken agent cannot make assigning somebody a task fail.

**The activity lands first.** Before the agent produces a single token, an
`agent_run` row is on the task's timeline — Paca's `agent.session.started`
move. A handoff that is invisible until the agent finishes looks, for its whole
duration, exactly like a handoff that never happened.

**Only NEW assignees dispatch.** `set_assignees` emits the *added* set, not the
whole set, so re-asserting an existing assignee cannot start a second run. That
property lives in the emitter and is relied on here; both sides say so.

**The tenant rides the event, and nothing else will do (WS-27aa / H4).** This
module was the H2 ratchet's one Projects exemption, on the grounds that an
event consumer must not inherit the ambient request tenant. That was right, and
the way out was never "convert it" — it was to give it an EXPLICIT one.
`set_assignees` now reads the task's own `organization_id` inside the request's
bound session and puts it on the payload; every session opened here is
`_tenant_session(org)` with that value. A payload without one is **refused**:
no run is dispatched and nothing is written, because writing the refusal would
itself need the unbound session this rule exists to forbid — the refusal is
therefore a WARNING log line (`projects.agent_dispatch_refused`), not a
timeline row. Fenced by `tests/unit/test_projects_automation.py`'s
`test_an_event_without_a_tenant_refuses_*` and by
`tests/unit/test_db_engine_seam.py`, which no longer exempts this file.
"""

from __future__ import annotations

import asyncio
from typing import Any

from acb_common import get_logger

# ⚠️ H4 DONE (WS-27aa), which is why this is `tenant_session` and NOT the
# ambient no-argument form. This module is an EVENT CONSUMER, not a request
# handler — `on_event` fires from `emit_event`'s sink fan-out and
# `_run_and_record` outlives the request that scheduled it — so
# `tenant_session()` with no argument would inherit whatever tenant happened to
# be in context, which is precisely what the runbook forbids. Every call here
# passes the organization the EVENT carried, and there is no path through this
# module that opens a session without one.
from gateway.db import tenant_session as _tenant_session
from gateway.routes.projects.core import (
    is_runnable_with_ancestors,
    record_activity,
)
from sqlalchemy import text

_log = get_logger("projects.agent_dispatch")

#: The prefix that makes an assignee an agent rather than a person (D-PM-4).
AGENT_PREFIX = "agent:"

#: How long a dispatched run may take before the activity is marked failed.
#: Matches the workflows engine's agent-node budget — the same runtime, so a
#: different number here would only mean one of the two is lying.
AGENT_RUN_TIMEOUT_SECONDS = 600.0


def agent_targets(assignees: Any) -> list[str]:
    """The agent names in an assignee list, in order, deduplicated.

    Case-insensitive on the prefix because the API lowercases on write but an
    event payload is not a database row and should not be trusted to have been
    through it.
    """
    out: list[str] = []
    seen: set[str] = set()
    for raw in assignees if isinstance(assignees, list) else []:
        value = str(raw or "").strip().lower()
        if not value.startswith(AGENT_PREFIX):
            continue
        name = value[len(AGENT_PREFIX):].strip()
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out


def build_message(task: Any) -> str:
    """What the agent is told. The task, not a prompt template.

    Deliberately plain: the agent gets the human-readable identifier it can
    quote back, the title, and the description if there is one. Anything richer
    belongs in the agent's own instructions, which are code-authored in Git —
    inventing a prompt here would put agent behaviour in a route package.
    """
    number = getattr(task, "task_number", None)
    label = f"#{number} " if number else ""
    parts = [f"You have been assigned task {label}{task.title}."]
    description = str(getattr(task, "description", "") or "").strip()
    if description:
        parts.append(description)
    parts.append(f"The task id is {task.id}.")
    return "\n\n".join(parts)


def event_tenant(payload: dict[str, Any]) -> str:
    """The organization the event says it belongs to, or ``""``.

    Exported and tiny so the refusal has ONE definition that both `on_event`
    and its tests read. The value is a stored fact about the task, stamped by
    the emitter inside the request's bound session — never a field a caller
    chose, which is R11 restated for an event payload.
    """
    return str(payload.get("organization_id") or "").strip()


async def on_event(source: str, event_type: str, payload: dict[str, Any]) -> None:
    """Event sink: start a run for every agent newly assigned to a task.

    Registered alongside the workflows dispatcher, so `pm.task.assigned` fans
    out to both. Best-effort like every sink — `emit_event` swallows sink
    errors by default and that default is load-bearing (a webhook must never
    5xx because a sink failed), so this returns rather than raises.

    ⚠️ **Refuses without a tenant on the payload** (WS-27aa / H4). Not
    "falls back to the ambient one", not "looks it up unbound" — both are the
    unbounded-leak shape the runbook names. The refusal is a WARNING log line
    rather than a timeline row, and that asymmetry is deliberate: the timeline
    lives in `pm_activities`, which is tenant data, so recording the refusal
    there would need exactly the unbound session being refused. Under RLS
    phase 4 such a write lands nowhere anyway. `set_assignees` always stamps
    the field (`pm_tasks.organization_id` is NOT NULL since migration 161), so
    in practice this fires only for a foreign or replayed emitter — which is
    the case worth being loud about.
    """
    if source != "projects" or event_type != "pm.task.assigned":
        return
    agents = agent_targets(payload.get("assignees"))
    if not agents:
        return

    task_id = str(payload.get("task_id") or "").strip()
    if not task_id:
        return

    organization_id = event_tenant(payload)
    if not organization_id:
        _log.warning(
            "projects.agent_dispatch_refused", task_id=task_id,
            agents=agents,
            reason="event carries no organization_id — a sink must not "
                   "inherit an ambient tenant or resolve one unbound (H4)",
        )
        return

    async with _tenant_session(organization_id) as db:
        task = (await db.execute(
            text("SELECT * FROM pm_tasks WHERE id = CAST(:tid AS uuid)"),
            {"tid": task_id},
        )).fetchone()
        if task is None:
            return

        # WS-27bg. An agent is not put to work inside a project that is not
        # running. Assignment in a paused project is planning ("this is yours
        # when we restart"), and dispatching on it would have an agent burn
        # budget on work the org has explicitly suspended.
        #
        # A WARNING rather than a timeline row, for the same reason the tenant
        # refusal above is: this is a sink, and the decision belongs in the log
        # where an operator looks, not in the activity feed of a project nobody
        # is reading.
        # Ancestor-aware: an agent must not be put to work inside an active
        # subproject of a PAUSED department. Reading the immediate project
        # alone was the slice-1 defect (see `is_runnable_with_ancestors`).
        if not await is_runnable_with_ancestors(
            db, getattr(task, "project_id", None)
        ):
            _log.warning(
                "projects.agent_dispatch_skipped", task_id=task_id,
                agents=agents,
                reason="the task's project (or one above it) is not running "
                       "(WS-27bg)",
            )
            return

        message = build_message(task)
        for name in agents:
            # The timeline entry is written and COMMITTED before the run
            # starts — the `_tenant_session` block commits on exit, which is
            # BEFORE the dispatch loop below — so the handoff is visible
            # immediately rather than when the agent finishes.
            await record_activity(
                db, activity_type="agent_run", created_by=f"{AGENT_PREFIX}{name}",
                task_id=task_id, body=f"Assigned to {name}; starting a run.",
                meta={"agent": name, "state": "started"},
            )

    for name in agents:
        await _run_and_record(name, message, task_id, organization_id)


async def _run_and_record(
    agent: str, message: str, task_id: str, organization_id: str,
) -> None:
    """Run one agent and close its timeline entry either way.

    A dispatch that fails silently is worse than one that never started: the
    task shows a session that appears to still be running and nobody knows to
    pick the work back up. So the failure path writes too.

    ``organization_id`` is threaded down rather than re-read: this coroutine
    outlives the transaction that started it, so there is nothing left to read
    it from, and re-resolving it would be a second answer to a question the
    event already settled.
    """
    try:
        from orchestrator.executor import run_agent
    except Exception as exc:  # pragma: no cover — orchestrator is a hard dep
        await _record_outcome(
            task_id, agent, organization_id,
            ok=False, detail="orchestrator unavailable",
        )
        _log.warning("projects.agent_dispatch_unavailable", error=str(exc))
        return

    try:
        result = await asyncio.wait_for(
            # H-201 part 3: the run needs its tenant for its working dir. A
            # shared agent with no tenant is refused. The org is the one the
            # server-side event carries, and the task row was found in it.
            run_agent(agent, message, organization_id=organization_id),
            timeout=AGENT_RUN_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        await _record_outcome(
            task_id, agent, organization_id, ok=False, detail="timed out",
        )
        return
    except Exception as exc:
        await _record_outcome(
            task_id, agent, organization_id, ok=False, detail=str(exc)[:300],
        )
        return
    await _record_outcome(
        task_id, agent, organization_id,
        ok=True, detail=str(result or "")[:2000],
    )


async def _record_outcome(
    task_id: str, agent: str, organization_id: str, *, ok: bool, detail: str,
) -> None:
    try:
        async with _tenant_session(organization_id) as db:
            await record_activity(
                db, activity_type="agent_run",
                created_by=f"{AGENT_PREFIX}{agent}",
                task_id=task_id,
                body=detail if ok else f"Agent run failed: {detail}",
                meta={"agent": agent, "state": "finished" if ok else "failed"},
            )
    except Exception as exc:  # pragma: no cover — the outcome write is best-effort
        _log.warning("projects.agent_dispatch_record_failed", error=str(exc))
