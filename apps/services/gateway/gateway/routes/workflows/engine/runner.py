"""Compile the serialized run-model to a MAF workflow and execute it (RFC §6).

The engine is a compile target, not a scheduler: each block becomes a MAF
``FunctionExecutor`` closing over one shared run-state dict (the ``{{…}}``
state bus — RFC §6.4's shared-state bridge), each connection becomes an edge,
and condition branches become conditional edges keyed on the token's branch.
MAF's superstep scheduler does the routing, fan-out, and completion detection.

Per-node lifecycle events go to an injected ``emit`` callback so the transport
layer (SSE, persistence) stays out of the engine.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from agent_framework import WorkflowBuilder, WorkflowContext, executor
from gateway.routes.workflows.engine.handlers import (
    DEFAULT_NODE_TIMEOUT,
    NODE_TIMEOUTS,
    WAIT_INLINE_MAX_SECONDS,
    NodeExecutionError,
    NodeServices,
    execute_node,
    resolve_wait_seconds,
)

#: Overall wall-clock budget for one run.
RUN_TIMEOUT_SECS = 15 * 60.0

EmitFn = Callable[[str, str, dict[str, Any]], None]
"""emit(node_id, status, detail) — status: running | ok | error | waiting |
skipped | pending."""


@dataclass(slots=True)
class RunOutcome:
    status: str  # succeeded | failed | paused
    state: dict[str, Any]  # final merged run state (node outputs)
    node_results: dict[str, Any]  # node_id → {status, output|error, duration_ms}
    outputs: list[Any]  # values yielded by output nodes
    error: str | None = None
    #: Gate nodes (approval or long wait) that halted their branch this run
    #: (status == "paused").
    paused_nodes: list[str] = field(default_factory=list)
    #: Long-wait pauses only: node_id → unix epoch seconds to resume at.
    #: Empty when the pause is an approval (a human resumes that one).
    wait_until: dict[str, float] = field(default_factory=dict)


@dataclass(slots=True)
class _Token:
    """The message routed along edges; data travels via the shared state."""

    branch: str | None = None


class _NodeFailure(Exception):
    def __init__(self, node_id: str, message: str):
        self.node_id = node_id
        self.message = message
        super().__init__(f"{node_id}: {message}")


def _noop_emit(node_id: str, status: str, detail: dict[str, Any]) -> None:
    return None


async def execute_workflow(
    serialized: dict[str, Any],
    trigger_payload: dict[str, Any],
    services: NodeServices,
    *,
    variables: dict[str, Any] | None = None,
    emit: EmitFn | None = None,
    run_timeout: float = RUN_TIMEOUT_SECS,
    precomputed: dict[str, Any] | None = None,
    resolved_approvals: set[str] | None = None,
    elapsed_waits: set[str] | None = None,
    done_human_steps: dict[str, str] | None = None,
) -> RunOutcome:
    """Execute one compiled workflow version against a trigger payload.

    *precomputed* replays a paused run: node ids mapped to their prior
    outputs pass through instantly (emitted as cached ``ok``) so resume
    re-walks the graph without re-running side effects.
    *resolved_approvals* names approval nodes a human has approved — any
    other approval node PAUSES its branch (outcome status ``paused``).
    *elapsed_waits* is the same idea for wait nodes whose deadline has
    passed: named waits run through, unnamed long waits pause the branch.
    *done_human_steps* maps a human step node (STU-E4) to the id of its done
    task: a named step runs through with that task id in its output, and any
    other human step pauses its branch.
    """
    emit = emit or _noop_emit
    precomputed = precomputed or {}
    resolved_approvals = resolved_approvals or set()
    elapsed_waits = elapsed_waits or set()
    done_human_steps = done_human_steps or {}
    paused_nodes: list[str] = []
    wait_until: dict[str, float] = {}
    blocks: list[dict[str, Any]] = list(serialized.get("blocks") or [])
    connections: list[dict[str, Any]] = list(serialized.get("connections") or [])
    entry_id = str(serialized.get("entry") or "")
    by_id = {str(b["id"]): b for b in blocks}
    if entry_id not in by_id:
        return RunOutcome(
            status="failed",
            state={},
            node_results={},
            outputs=[],
            error="compiled workflow has no entry block",
        )

    # The shared state bus: node outputs keyed by node id + reserved roots.
    state: dict[str, Any] = {
        "trigger": dict(trigger_payload or {}),
        "vars": dict(variables or {}),
    }
    node_results: dict[str, Any] = {}
    outputs: list[Any] = []
    has_outgoing = {str(c.get("source")) for c in connections}

    def _make_executor(block: dict[str, Any]) -> Any:
        node_id = str(block["id"])
        ntype = str(block.get("type") or "")
        timeout = NODE_TIMEOUTS.get(ntype, DEFAULT_NODE_TIMEOUT)
        terminal = node_id not in has_outgoing

        @executor(id=node_id)
        async def _run(token: _Token, ctx: WorkflowContext[_Token, Any]) -> None:
            # Resume replay: a node that already ran passes its stored output
            # through without re-executing (no repeated side effects).
            if node_id in precomputed:
                output = precomputed[node_id]
                state[node_id] = output
                node_results[node_id] = {"status": "ok", "output": output, "cached": True}
                emit(node_id, "ok", {"output": output, "cached": True})
                await _forward_cached(ctx, ntype, output, outputs)
                return

            # Gate nodes halt their branch: an approval waits for a human, a
            # long wait waits for its deadline. Nothing downstream runs.
            gate = _gate_pause(
                ntype, node_id, block, state, resolved_approvals, elapsed_waits,
                set(done_human_steps),
            )
            if gate is not None:
                paused_nodes.append(node_id)
                if gate.get("resume_at") is not None:
                    wait_until[node_id] = gate["resume_at"]
                node_results[node_id] = {"status": "waiting", **gate}
                emit(node_id, "waiting", {"type": ntype, **gate})
                return

            emit(node_id, "running", {"type": ntype})
            started = time.monotonic()
            try:
                output = await asyncio.wait_for(
                    execute_node(block, state, services),
                    timeout=timeout,
                )
            except TimeoutError:
                message = f"timed out after {timeout:.0f}s"
                node_results[node_id] = {"status": "error", "error": message}
                emit(node_id, "error", {"error": message})
                raise _NodeFailure(node_id, message) from None
            except NodeExecutionError as exc:
                node_results[node_id] = {"status": "error", "error": str(exc)}
                emit(node_id, "error", {"error": str(exc)})
                raise _NodeFailure(node_id, str(exc)) from exc
            except _NodeFailure:
                raise
            except Exception as exc:  # unexpected — still a clean node failure
                message = f"{type(exc).__name__}: {exc}"
                node_results[node_id] = {"status": "error", "error": message[:500]}
                emit(node_id, "error", {"error": message[:500]})
                raise _NodeFailure(node_id, message[:500]) from exc

            duration_ms = int((time.monotonic() - started) * 1000)
            if ntype == "human_step" and node_id in done_human_steps:
                output = {**output, "task_id": done_human_steps[node_id]}
            state[node_id] = output
            node_results[node_id] = {
                "status": "ok",
                "output": output,
                "duration_ms": duration_ms,
            }
            emit(node_id, "ok", {"output": output, "duration_ms": duration_ms})
            if ntype == "output":
                outputs.append(output.get("value"))
                await ctx.yield_output(output.get("value"))
            await ctx.send_message(_Token(branch=_branch_of(output)))
            if terminal and ntype != "output":
                await ctx.yield_output(output)

        return _run

    executors = {str(b["id"]): _make_executor(b) for b in blocks}
    workflow = _build_maf_workflow(executors, blocks, connections, entry_id, has_outgoing)

    error = await _run_compiled(workflow, run_timeout)
    paused = bool(paused_nodes) and not error
    # Nodes that never ran: "pending" while paused (they run on resume),
    # "skipped" on a finished run (untaken branch / after a failure).
    _mark_unrun(by_id, node_results, emit, "pending" if paused else "skipped")
    return RunOutcome(
        status="failed" if error else ("paused" if paused else "succeeded"),
        state=state,
        node_results=node_results,
        outputs=outputs,
        error=error,
        paused_nodes=paused_nodes,
        wait_until=wait_until,
    )


def _build_maf_workflow(
    executors: dict[str, Any],
    blocks: list[dict[str, Any]],
    connections: list[dict[str, Any]],
    entry_id: str,
    has_outgoing: set[str],
) -> Any:
    """Wire the per-node executors into a MAF workflow: edges from the
    connections, conditional edges for condition branches.

    Only output-type and terminal blocks ever ``yield_output``, so they are
    designated explicitly (MAF's implicit mode is deprecated).
    """
    yielding = [
        executors[str(b["id"])]
        for b in blocks
        if str(b.get("type")) == "output" or str(b["id"]) not in has_outgoing
    ]
    builder = WorkflowBuilder(
        start_executor=executors[entry_id],
        output_from=yielding or [executors[entry_id]],
    )
    for conn in connections:
        src, tgt = str(conn.get("source")), str(conn.get("target"))
        if src not in executors or tgt not in executors:
            continue
        handle = conn.get("handle")
        if handle in ("true", "false"):
            builder.add_edge(
                executors[src], executors[tgt], condition=_branch_condition(handle)
            )
        else:
            builder.add_edge(executors[src], executors[tgt])
    return builder.build()


def _branch_of(output: Any) -> str | None:
    """The edge handle a node's output routes to ("true"/"false"), if any.
    Shared by the live and replay paths so both route identically."""
    branch = output.get("branch") if isinstance(output, dict) else None
    return branch if isinstance(branch, str) else None


def _gate_pause(
    ntype: str,
    node_id: str,
    block: dict[str, Any],
    state: dict[str, Any],
    resolved_approvals: set[str],
    elapsed_waits: set[str],
    done_human_steps: set[str] | None = None,
) -> dict[str, Any] | None:
    """Extra node-result fields when this gate node must PAUSE, else ``None``.

    Approval (spec F11) → ``{}``: a human resumes it from the approvals inbox.
    Long wait (spec F3) → ``{"resume_at": epoch}``: the schedule scanner does.
    Human step (STU-E4) → ``{"human_step": True}``: the task scan resumes it
    when its task is done.
    A gate already cleared for this pass — and every non-gate node type —
    returns ``None`` and runs normally.
    """
    if ntype == "approval":
        return None if node_id in resolved_approvals else {}
    if ntype == "human_step":
        return None if node_id in (done_human_steps or set()) else {"human_step": True}
    if ntype == "wait" and node_id not in elapsed_waits:
        deadline = _long_wait_deadline(block.get("config") or {}, state)
        if deadline is not None:
            return {"resume_at": deadline}
    return None


def _long_wait_deadline(config: dict[str, Any], state: dict[str, Any]) -> float | None:
    """Unix deadline for a wait too long to run inline, else ``None``.

    ``None`` also covers an unresolvable duration ("{{trigger.delay}}" with no
    such field): that is a node ERROR, and letting it fall through to the
    handler produces exactly the same clean failure every other node type
    gets — no duplicate error plumbing here.
    """
    try:
        seconds = resolve_wait_seconds(config, state)
    except NodeExecutionError:
        return None
    return time.time() + seconds if seconds > WAIT_INLINE_MAX_SECONDS else None


async def _forward_cached(
    ctx: Any, ntype: str, output: Any, outputs: list[Any]
) -> None:
    """Route a replayed node's stored output onward (yield + branch token)."""
    if ntype == "output":
        value = output.get("value") if isinstance(output, dict) else output
        outputs.append(value)
        await ctx.yield_output(value)
    await ctx.send_message(_Token(branch=_branch_of(output)))


async def _run_compiled(workflow: Any, run_timeout: float) -> str | None:
    """Run the built MAF workflow; classify any failure into a message."""
    try:
        await asyncio.wait_for(workflow.run(_Token()), timeout=run_timeout)
        return None
    except TimeoutError:
        return f"run exceeded the {run_timeout:.0f}s budget"
    except _NodeFailure as failure:
        return f"node '{failure.node_id}' failed: {failure.message}"
    except Exception as exc:
        # MAF wraps executor exceptions; surface the innermost _NodeFailure.
        failure = _find_node_failure(exc)
        if failure is not None:
            return f"node '{failure.node_id}' failed: {failure.message}"
        return f"{type(exc).__name__}: {exc}"[:500]


def _branch_condition(handle: str) -> Callable[[Any], bool]:
    def _check(message: Any) -> bool:
        return getattr(message, "branch", None) == handle

    return _check


def _find_node_failure(exc: BaseException) -> _NodeFailure | None:
    seen: set[int] = set()
    stack: list[BaseException] = [exc]
    while stack:
        current = stack.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, _NodeFailure):
            return current
        for nxt in (current.__cause__, current.__context__):
            if nxt is not None:
                stack.append(nxt)
        if isinstance(current, BaseExceptionGroup):
            stack.extend(current.exceptions)
    return None


def _mark_unrun(
    by_id: dict[str, dict[str, Any]],
    node_results: dict[str, Any],
    emit: EmitFn,
    status: str,
) -> None:
    """Blocks that never ran — "skipped" on a finished run, "pending" while
    the run is paused for approval (they execute on resume)."""
    for node_id in by_id:
        if node_id not in node_results:
            node_results[node_id] = {"status": status}
            emit(node_id, status, {})
