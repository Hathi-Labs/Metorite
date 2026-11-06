"""One handler per node *type* (RFC §6.3) over injected NodeServices.

Handlers are pure orchestration: they resolve ``{{…}}`` templates against the
run state, call the injected service seam, and return the node's output dict.
The gateway wires real services (orchestrator ``run_agent``, the workflow tool
registry, the module store) in ``service.py``; tests wire stubs.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from gateway.module_runs import ModuleRunsOff
from gateway.routes.workflows.engine.modules import run_module_code
from gateway.routes.workflows.engine.templating import resolve_value

#: Waits at or below this run INLINE (the run holds its slot and sleeps);
#: anything longer pauses the run and resumes from a snapshot, so a long wait
#: survives a gateway restart instead of evaporating with the process.
WAIT_INLINE_MAX_SECONDS = 60.0
#: Upper bound on a single wait — a typo ("wait 90000000") should fail at
#: publish, not park a run past the heat death of the quarter.
MAX_WAIT_SECONDS = 30 * 86400

#: Per-node wall-clock budgets (seconds), by node type.
NODE_TIMEOUTS: dict[str, float] = {
    "agent": 600.0,
    "tool": 120.0,
    "module": 15.0,
    "condition": 5.0,
    "set": 5.0,
    "approval": 5.0,
    # An inline wait sleeps inside the node; the budget must clear it.
    "wait": WAIT_INLINE_MAX_SECONDS + 10.0,
    "output": 5.0,
    "trigger": 5.0,
    # An internal DB write — nothing here crosses the network.
    "pm_task": 30.0,
    # The lifecycle sweep walks every root project with a policy (WS-27z).
    # Internal DB work like pm_task, but N projects × M stale tasks of it.
    "pm_lifecycle": 300.0,
}
DEFAULT_NODE_TIMEOUT = 60.0


class NodeExecutionError(Exception):
    """A node failed; the message is safe to surface in the run console."""


@dataclass(slots=True)
class NodeServices:
    """The seams a running workflow may touch — injected, never imported.

    ``run_agent(agent, message, model)`` → final text.
    ``run_tool(action, args, actor)``    → tool result dict (the workflow tool
                                           registry decides broker routing).
    ``get_module_code(module_id)``       → validated module source or None.
    """

    run_agent: Callable[[str, str, str | None], Awaitable[str]]
    run_tool: Callable[[str, dict[str, Any], str], Awaitable[dict[str, Any]]]
    get_module_code: Callable[[str], Awaitable[str | None]]
    #: ``update_task(task_id, fields)`` → what changed. Injected like every
    #: other seam, so the engine never imports the Projects app and a test can
    #: drive a task node with no database at all.
    #:
    #: Deliberately takes NO actor: the engine says *what* to change, the
    #: wiring decides *who* is changing it — and for an automation that is
    #: always the workflow itself, never whoever tripped the trigger. Passing
    #: an actor the implementation would have to ignore is how the two answers
    #: start disagreeing.
    update_task: Callable[
        [str, dict[str, Any]], Awaitable[dict[str, Any]]
    ] | None = None
    #: ``run_lifecycle_sweep()`` → sweep counts (WS-27z). Takes NOTHING: the
    #: whole policy — which projects, which windows, which timezone — lives in
    #: the Projects app's own columns, so a workflow cannot be published that
    #: sweeps differently from what each project's settings say. The wiring
    #: binds the workflow's identity, same as ``update_task``.
    run_lifecycle_sweep: Callable[[], Awaitable[dict[str, Any]]] | None = None
    actor: str = "workflow"
    #: Extra context merged into agent messages' metadata (reserved).
    context: dict[str, Any] = field(default_factory=dict)
    #: The org of the RUN (``service.build_node_services``), never of node
    #: input. The module node passes it to ``run_module_code``, whose switch
    #: refuses an org off ``MODULE_RUN_ORGS``. ``None`` (a run with no org, or
    #: a stub) is always refused (STU-Q15a).
    organization_id: str | None = None


# ── Condition evaluation (no eval(), a closed operator set) ─────────────────

_NUMERIC_OPS = {"gt", "gte", "lt", "lte"}


def _as_number(value: Any) -> float:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        raise NodeExecutionError(f"'{value}' is not a number") from None


def evaluate_condition(left: Any, op: str, right: Any) -> bool:
    """The closed comparison vocabulary behind the condition node."""
    op = (op or "").strip()
    if op == "equals":
        return str(left) == str(right) if not isinstance(left, type(right)) else left == right
    if op == "not_equals":
        return not evaluate_condition(left, "equals", right)
    if op == "contains":
        if isinstance(left, (list, dict)):
            return right in left
        return str(right) in str(left or "")
    if op == "not_contains":
        return not evaluate_condition(left, "contains", right)
    if op in _NUMERIC_OPS:
        ln, rn = _as_number(left), _as_number(right)
        return {"gt": ln > rn, "gte": ln >= rn, "lt": ln < rn, "lte": ln <= rn}[op]
    if op == "is_empty":
        return left is None or left == "" or left == [] or left == {}
    if op == "not_empty":
        return not evaluate_condition(left, "is_empty", right)
    if op == "truthy":
        return bool(left)
    raise NodeExecutionError(f"unknown condition operator '{op}'")


def resolve_wait_seconds(config: dict[str, Any], state: dict[str, Any]) -> float:
    """A wait node's duration, with ``{{refs}}`` resolved against the state.

    Shared by the runner (which decides inline-vs-pause BEFORE calling the
    handler) and the handler itself, so both read one duration.
    """
    raw = resolve_value(config.get("seconds"), state)
    try:
        seconds = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise NodeExecutionError(f"wait duration '{raw}' is not a number") from None
    if seconds < 0:
        raise NodeExecutionError("wait duration must be positive")
    return min(seconds, float(MAX_WAIT_SECONDS))


# ── Node handlers ────────────────────────────────────────────────────────────


async def execute_node(
    block: dict[str, Any],
    state: dict[str, Any],
    services: NodeServices,
) -> dict[str, Any]:
    """Run one block against the shared *state*; return its output dict.

    The output is what downstream ``{{node_id.field}}`` refs see. Condition
    outputs carry ``branch`` (``"true"``/``"false"``) for edge routing.
    """
    ntype = str(block.get("type") or "")
    config = block.get("config") or {}
    if ntype == "trigger":
        # State was seeded with the trigger payload before the run started.
        return dict(state.get("trigger") or {})

    if ntype == "agent":
        agent = str(resolve_value(config.get("agent"), state) or "").strip()
        message = str(resolve_value(config.get("message"), state) or "").strip()
        model = str(config.get("model") or "").strip() or None
        if not agent or not message:
            raise NodeExecutionError("agent node needs an agent and a message")
        text = await services.run_agent(agent, message, model)
        return {"result": text}

    if ntype == "tool":
        action = str(config.get("action") or "").strip()
        raw_args = config.get("args") if isinstance(config.get("args"), dict) else {}
        args = resolve_value(raw_args, state)
        if not action:
            raise NodeExecutionError("tool node needs an action")
        return await services.run_tool(action, args, services.actor)

    if ntype == "module":
        return await _execute_module(config, state, services)

    if ntype == "condition":
        left = resolve_value(config.get("left"), state)
        right = resolve_value(config.get("right"), state)
        result = evaluate_condition(left, str(config.get("op") or ""), right)
        return {"result": result, "branch": "true" if result else "false"}

    if ntype == "set":
        return _execute_set(config, state)

    if ntype == "approval":
        # The runner pauses unresolved approvals before this handler runs;
        # reaching it means a human approved — pass through.
        return {"approved": True, "message": str(config.get("message") or "")}

    if ntype == "wait":
        # Only two ways to reach this handler, and only one of them sleeps:
        #   short wait  → the runner chose the INLINE path; serve the time here
        #   long wait   → it already served its time as a paused run, and the
        #                 scheduler resumed it; returning immediately is the
        #                 whole point (never wait twice).
        seconds = resolve_wait_seconds(config, state)
        if 0 < seconds <= WAIT_INLINE_MAX_SECONDS:
            await asyncio.sleep(seconds)
        return {"waited_seconds": seconds}

    if ntype == "pm_task":
        return await _execute_pm_task(config, state, services)

    if ntype == "pm_lifecycle":
        # Config-free by design (WS-27z): the node is an instruction to apply
        # whatever each root project's stored policy says, so there is nothing
        # to resolve and nothing a graph could override.
        if services.run_lifecycle_sweep is None:
            raise NodeExecutionError("the Projects app is not available")
        return await services.run_lifecycle_sweep()

    if ntype == "output":
        value = resolve_value(config.get("value"), state)
        return {"value": value}

    raise NodeExecutionError(f"unknown node type '{ntype}'")


async def _execute_module(
    config: dict[str, Any],
    state: dict[str, Any],
    services: NodeServices,
) -> dict[str, Any]:
    """The module node: read the module of the run's org, then run it.

    A function of its own, as ``_execute_set`` is: ``execute_node`` sits at
    the complexity ceiling. ``run_module_code`` takes the org of the RUN
    (``services.organization_id``) and refuses an org off
    ``MODULE_RUN_ORGS`` before it spawns anything (STU-Q15a). The refusal
    fails this node with the member's words, so the run ends as a failed run
    and never hangs.
    """
    module_id = str(config.get("module_id") or "").strip()
    raw_inputs = config.get("inputs") if isinstance(config.get("inputs"), dict) else {}
    inputs = resolve_value(raw_inputs, state)
    code = await services.get_module_code(module_id)
    if not code:
        raise NodeExecutionError("module not found or not ready")
    try:
        return await run_module_code(code, inputs, organization_id=services.organization_id)
    except ModuleRunsOff as exc:
        raise NodeExecutionError(exc.message) from None


def _execute_set(config: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    """Assign values into the run's `vars` bucket.

    Extracted unchanged when the task node landed: `execute_node` sat exactly
    at the complexity ceiling, so one more branch had to be paid for rather
    than the ceiling raised. Behaviour is identical — the golden trajectory
    eval covers it.
    """
    assignments = (
        config.get("assignments") if isinstance(config.get("assignments"), dict) else {}
    )
    resolved = {str(k): resolve_value(v, state) for k, v in assignments.items()}
    vars_bucket = state.setdefault("vars", {})
    if isinstance(vars_bucket, dict):
        vars_bucket.update(resolved)
    return resolved


async def _execute_pm_task(
    config: dict[str, Any],
    state: dict[str, Any],
    services: NodeServices,
) -> dict[str, Any]:
    """The Projects task node (WS-27f / §13 U1).

    A separate function rather than a fifth branch inline: `execute_node` is
    already at the complexity ceiling, and this node's run-time checks are the
    interesting part rather than boilerplate worth burying.
    """
    if services.update_task is None:
        raise NodeExecutionError("the Projects app is not available")
    task_id = str(resolve_value(config.get("task_id"), state) or "").strip()
    if not task_id:
        raise NodeExecutionError("task node needs a task id")
    if "{{" in task_id:
        # `templating.resolve_value` keeps an unresolvable ref AS-IS at run time
        # BY DESIGN — design-time validation is where refs are flagged — and
        # `{{trigger.missing}}` passes that gate because its ROOT is legal.
        # Left alone the literal reaches the database as a would-be uuid and
        # comes back "Task not found", sending the maker to look for a task
        # rather than at their reference.
        raise NodeExecutionError(f"task id did not resolve: '{task_id}'")
    raw_fields = config.get("fields") if isinstance(config.get("fields"), dict) else {}
    fields = resolve_value(raw_fields, state)
    if not isinstance(fields, dict) or not fields:
        raise NodeExecutionError("task node needs at least one field to set")
    return await services.update_task(task_id, fields)
