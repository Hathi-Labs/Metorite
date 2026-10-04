"""Published workflows → orchestrator tools (spec workflows_app.md F13).

The sibling of ``app_tools.py``: agents get three plain async tools —
``list_workflows`` / ``run_workflow`` / ``get_workflow_run`` — so a
conversational request ("run the lead-intake flow on this") reuses a
GOVERNED automation instead of the agent improvising the steps. Three
generic tools rather than one tool per workflow: the catalog can grow to
dozens of workflows without bloating every agent's tool schema, and the
list→run pair is exactly how the LLM discovers what exists.

Calls go in-process to ``gateway.routes.workflows.service`` — the SAME
entrypoints the manual Run button and the API use, so concurrency caps,
run history, activity events, and the approval gates inside the workflow
behave identically no matter who triggered it. A run that pauses at a
Human-approval node reports itself as ``paused`` and tells the agent it
is waiting in the approvals inbox — the agent cannot bypass the gate.

Injected through ``_tool_injection``'s SAFE pipeline (same block as app
tools) so every call passes the risk-aware permission gate.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from acb_common import get_logger

_log = get_logger("orchestrator.workflow_tools")

#: run_workflow's optional wait is capped so a tool call can never hang an
#: agent turn on a long-running workflow — poll get_workflow_run instead.
MAX_WAIT_SECONDS = 120
_POLL_INTERVAL_SECONDS = 2.0
_TERMINAL = {"succeeded", "failed", "cancelled", "paused"}


async def list_workflows() -> str:
    """List the published workflows you can trigger, with descriptions.

    Call this first to find the right workflow name for run_workflow.
    """
    from gateway.routes.workflows.service import list_published_workflows

    rows = await list_published_workflows()
    if not rows:
        return "No published workflows exist yet."
    return json.dumps(rows, default=str)


async def run_workflow(workflow: str, payload_json: str = "{}", wait_seconds: int = 0) -> str:
    """Trigger a published workflow by name (or id) with a JSON payload.

    The payload becomes the workflow's {{trigger.*}} data. With
    wait_seconds > 0 (max 120) this waits for the run to finish and returns
    its output; otherwise it returns the run_id immediately — check progress
    later with get_workflow_run. A run that pauses for human approval cannot
    be approved by you; it waits in the operators' approvals inbox.
    """
    from gateway.routes.workflows.service import run_published_workflow

    try:
        payload = json.loads(payload_json or "{}")
        if not isinstance(payload, dict):
            payload = {"value": payload}
    except ValueError:
        return 'payload_json is not valid JSON — pass an object like {"body": "…"}'

    started = await run_published_workflow(workflow, payload, started_by="agent-tool")
    if started.get("error"):
        return f"Could not start workflow: {started['error']}"
    run_id = str(started["run_id"])
    if wait_seconds <= 0:
        return (
            f"Started workflow '{started['workflow_name']}' — run_id={run_id}. "
            "Check progress with get_workflow_run."
        )
    return await _wait_and_report(run_id, min(int(wait_seconds), MAX_WAIT_SECONDS))


async def get_workflow_run(run_id: str) -> str:
    """Status and output of a workflow run started with run_workflow."""
    from gateway.routes.workflows.service import run_summary

    return _describe(await run_summary(run_id))


async def _wait_and_report(run_id: str, wait_seconds: int) -> str:
    from gateway.routes.workflows.service import run_summary

    deadline = asyncio.get_running_loop().time() + wait_seconds
    summary = await run_summary(run_id)
    while (
        summary.get("status") not in _TERMINAL
        and not summary.get("error")
        and asyncio.get_running_loop().time() < deadline
    ):
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)
        summary = await run_summary(run_id)
    return _describe(summary)


def _describe(summary: dict[str, Any]) -> str:
    if summary.get("error") and not summary.get("status"):
        return f"Workflow run lookup failed: {summary['error']}"
    status = summary.get("status")
    parts = [f"Run {summary.get('run_id')} ('{summary.get('workflow_name')}'): {status}."]
    if status == "paused":
        parts.append(
            "It is waiting for human approval in the approvals inbox; "
            "it will resume when an operator approves."
        )
    if summary.get("error"):
        parts.append(f"Error: {summary['error']}")
    if summary.get("output") is not None:
        parts.append(f"Output: {json.dumps(summary['output'], default=str)[:2000]}")
    return " ".join(parts)


def load_workflow_tools(agent_name: str) -> list[Any]:
    """The workflow tool trio for *agent_name*. Best-effort: if the gateway's
    workflows package is unavailable in this process, inject nothing rather
    than break the rest of tool injection."""
    try:
        import gateway.routes.workflows.service  # noqa: F401
    except Exception:
        _log.warning("workflow_tools.unavailable", agent=agent_name)
        return []
    try:
        from acb_skills.tool_annotations import TOOL_ANNOTATIONS

        TOOL_ANNOTATIONS.setdefault(
            "list_workflows",
            {"read_only": True, "destructive": False, "idempotent": True, "open_world": False},
        )
        TOOL_ANNOTATIONS.setdefault(
            "get_workflow_run",
            {"read_only": True, "destructive": False, "idempotent": True, "open_world": False},
        )
        # Not destructive itself: any outward write INSIDE the workflow is
        # already broker-gated / approval-noded — that is the whole point.
        # H-236: open_world, because a workflow node can call an outside URL
        # (``http.request``) with the payload the agent passes. A run that a
        # covered Projects run delegates to does not get it.
        TOOL_ANNOTATIONS.setdefault(
            "run_workflow",
            {"read_only": False, "destructive": False, "idempotent": False, "open_world": True},
        )
    except ImportError:
        pass
    trio = [list_workflows, run_workflow, get_workflow_run]
    try:  # H-236: the platform's own tools, trusted by identity
        from acb_skills.egress import register_platform_callable

        for fn in trio:
            register_platform_callable(fn)
    except ImportError:
        pass
    return trio
