"""Golden trajectories: the Workflows engine end-to-end (workflows_app.md §6).

Unlike the unit tests (which pin validate/compile/handlers individually),
these drive the full journey a published workflow takes: edit-model graph →
``compile_graph`` → ``execute_workflow`` over stub NodeServices — including
the {{…}} state bus, condition branching, the approval pause, and the
precomputed-replay resume that must never repeat a side effect.

The fixture graph is the spec's canonical shape:

    trigger → agent → condition ──true──> module → approval → tool → output
                          └─────false──────────────────────────────> output

Offline: agent/tool are stubs; the module node runs the real subprocess
sandbox (no network).
"""

from __future__ import annotations

from typing import Any

import pytest
from gateway.routes.workflows.engine.graph import compile_graph
from gateway.routes.workflows.engine.handlers import NodeExecutionError, NodeServices
from gateway.routes.workflows.engine.runner import execute_workflow
from gateway.routes.workflows.tools import tool_arg_schemas

KNOWN_AGENTS = {"summarizer"}
KNOWN_MODULES = {"mod-clean"}
# ⚠️ Re-cut 2026-08-24 by D52 (board WS-39 S1). This suite's destructive node
# was `clickup.create_task`, whose WorkflowToolSpec is deleted with the
# connector — and after that removal the registry holds NO destructive tool at
# all. The approval-gate behaviour under test is the engine's, not any one
# tool's, and `DESTRUCTIVE` is supplied by the CALLER rather than read from the
# registry, so `http.request` (a real, registered tool) is declared destructive
# here to keep the gate exercised. Losing the coverage would have been the
# expensive way to notice D52.
DESTRUCTIVE = {"http.request"}

MODULE_CODE = 'def run(inputs):\n    return {"cleaned": inputs["text"].strip().lower()}\n'


def _node(nid: str, ntype: str, config: dict | None = None) -> dict:
    return {
        "id": nid,
        "type": ntype,
        "position": {"x": 0, "y": 0},
        "data": {"label": nid, "config": config or {}},
    }


def _edge(src: str, tgt: str, handle: str | None = None) -> dict:
    edge = {"id": f"{src}->{tgt}", "source": src, "target": tgt}
    if handle:
        edge["sourceHandle"] = handle
    return edge


def _graph() -> dict:
    return {
        "nodes": [
            _node("start", "trigger"),
            _node("summarize", "agent", {"agent": "summarizer", "message": "Summarize: {{trigger.subject}}"}),
            _node("check", "condition", {"left": "{{trigger.priority}}", "op": "equals", "right": "high"}),
            _node("clean", "module", {"module_id": "mod-clean", "inputs": {"text": "{{summarize.result}}"}}),
            _node("gate", "approval", {"message": "Approve task creation"}),
            _node("write", "tool", {"action": "http.request", "args": {"url": "https://example.invalid/tasks", "method": "POST", "body": {"name": "{{clean.cleaned}}"}}}),
            _node("out_hi", "output", {"value": "{{write.task_id}}"}),
            _node("out_lo", "output", {"value": "low priority — no task"}),
        ],
        "edges": [
            _edge("start", "summarize"),
            _edge("summarize", "check"),
            _edge("check", "clean", "true"),
            _edge("clean", "gate"),
            _edge("gate", "write"),
            _edge("write", "out_hi"),
            _edge("check", "out_lo", "false"),
        ],
    }


class _Trace:
    """Stub NodeServices that records every seam crossing + emit event."""

    def __init__(self, *, tool_error: str | None = None) -> None:
        self.agent_calls: list[tuple[str, str, str | None]] = []
        self.tool_calls: list[tuple[str, dict, str]] = []
        self.events: list[tuple[str, str]] = []
        self._tool_error = tool_error

    def services(self) -> NodeServices:
        async def run_agent(agent: str, message: str, model: str | None) -> str:
            self.agent_calls.append((agent, message, model))
            return "  Pump order stuck at packing  "

        async def run_tool(action: str, args: dict, actor: str) -> dict[str, Any]:
            self.tool_calls.append((action, args, actor))
            if self._tool_error:
                raise NodeExecutionError(self._tool_error)
            return {"task_id": "T-1"}

        async def get_module_code(module_id: str) -> str | None:
            return MODULE_CODE if module_id == "mod-clean" else None

        return NodeServices(
            run_agent=run_agent, run_tool=run_tool, get_module_code=get_module_code
        )

    def emit(self, node_id: str, status: str, detail: dict) -> None:
        self.events.append((node_id, status))


@pytest.fixture(scope="module")
def serialized() -> dict:
    """The compile step is part of the trajectory: the publish-time gate
    (write node behind an approval) must accept this graph."""
    return compile_graph(
        _graph(),
        known_agents=KNOWN_AGENTS,
        known_modules=KNOWN_MODULES,
        destructive_actions=DESTRUCTIVE,
        # Against the REAL declarations: this fixture graph must stay
        # publishable as the tool catalog evolves, not merely runnable.
        tool_schemas=tool_arg_schemas(),
    )


async def test_high_priority_run_pauses_at_the_gate(serialized: dict) -> None:
    """First leg: trigger → agent → condition(true) → module all run, then the
    approval node halts the branch — the destructive tool NEVER fires."""
    trace = _Trace()
    outcome = await execute_workflow(
        serialized,
        {"subject": "Pump order stuck", "priority": "high"},
        trace.services(),
        emit=trace.emit,
    )

    assert outcome.status == "paused"
    assert outcome.paused_nodes == ["gate"]
    assert outcome.error is None
    assert trace.tool_calls == []  # the whole point of the gate

    # Templating bus: the agent saw the RESOLVED message, the module saw the
    # agent's output, and the module's sandbox result landed on the state bus.
    assert trace.agent_calls == [("summarizer", "Summarize: Pump order stuck", None)]
    assert outcome.state["clean"] == {"cleaned": "pump order stuck at packing"}

    assert {nid: r["status"] for nid, r in outcome.node_results.items()} == {
        "start": "ok",
        "summarize": "ok",
        "check": "ok",
        "clean": "ok",
        "gate": "waiting",
        "write": "pending",
        "out_hi": "pending",
        "out_lo": "pending",
    }
    # The emitted lifecycle stream is the run console's contract.
    assert trace.events == [
        ("start", "running"), ("start", "ok"),
        ("summarize", "running"), ("summarize", "ok"),
        ("check", "running"), ("check", "ok"),
        ("clean", "running"), ("clean", "ok"),
        ("gate", "waiting"),
        ("write", "pending"), ("out_hi", "pending"), ("out_lo", "pending"),
    ]


async def test_resume_replays_without_repeating_side_effects(serialized: dict) -> None:
    """Second leg: approve the gate and re-execute from the pause snapshot.
    Every already-ran node passes through cached; only gate → write → out_hi
    execute live. The agent is NOT called again."""
    first = _Trace()
    paused = await execute_workflow(
        serialized,
        {"subject": "Pump order stuck", "priority": "high"},
        first.services(),
        emit=first.emit,
    )
    precomputed = {
        nid: r["output"]
        for nid, r in paused.node_results.items()
        if r["status"] == "ok"
    }

    resumed = _Trace()
    outcome = await execute_workflow(
        serialized,
        {"subject": "Pump order stuck", "priority": "high"},
        resumed.services(),
        emit=resumed.emit,
        precomputed=precomputed,
        resolved_approvals={"gate"},
    )

    assert outcome.status == "succeeded"
    assert resumed.agent_calls == []  # replayed, not re-run
    # ⚠️ These args must match the `write` node's config in `serialized`, with
    # `{{clean.cleaned}}` resolved. The D52 re-cut moved that node from
    # `clickup.create_task` to `http.request` and updated the fixture but not
    # this assertion, which kept the old connector's `{list_id, name}` shape —
    # so the branch's evals job was RED from the day of the excision.
    assert resumed.tool_calls == [
        (
            "http.request",
            {
                "url": "https://example.invalid/tasks",
                "method": "POST",
                "body": {"name": "pump order stuck at packing"},
            },
            "workflow",
        )
    ]
    assert outcome.outputs == ["T-1"]
    assert outcome.node_results["gate"]["output"]["approved"] is True
    assert outcome.node_results["summarize"]["cached"] is True
    assert outcome.node_results["out_lo"] == {"status": "skipped"}


async def test_low_priority_takes_the_false_branch(serialized: dict) -> None:
    """The condition routes low-priority triggers straight to out_lo; the
    entire write branch (module, gate, tool) is skipped untouched."""
    trace = _Trace()
    outcome = await execute_workflow(
        serialized,
        {"subject": "FYI only", "priority": "low"},
        trace.services(),
        emit=trace.emit,
    )

    assert outcome.status == "succeeded"
    assert outcome.outputs == ["low priority — no task"]
    assert trace.tool_calls == []
    statuses = {nid: r["status"] for nid, r in outcome.node_results.items()}
    assert statuses["out_lo"] == "ok"
    assert {statuses[n] for n in ("clean", "gate", "write", "out_hi")} == {"skipped"}


async def test_tool_failure_surfaces_the_node_and_skips_downstream(serialized: dict) -> None:
    """A failing tool ends the run 'failed' with the node named in the error
    (the run console's contract) — downstream output never yields."""
    trace = _Trace(tool_error="the endpoint rejected the request")
    outcome = await execute_workflow(
        serialized,
        {"subject": "Pump order stuck", "priority": "high"},
        trace.services(),
        emit=trace.emit,
        resolved_approvals={"gate"},
    )

    assert outcome.status == "failed"
    assert outcome.error is not None
    assert "write" in outcome.error and "the endpoint rejected the request" in outcome.error
    assert outcome.outputs == []
    assert outcome.node_results["write"]["status"] == "error"
    assert outcome.node_results["out_hi"] == {"status": "skipped"}


# ── The wait gate (spec F3): a second gate kind on the same rails ───────────

WAIT_GRAPH = {
    "nodes": [
        _node("start", "trigger"),
        _node("cool_off", "wait", {"seconds": 2 * 86400}),
        _node("nudge", "agent", {"agent": "summarizer", "message": "Follow up on {{trigger.subject}}"}),
        _node("out", "output", {"value": "{{nudge.result}}"}),
    ],
    "edges": [
        _edge("start", "cool_off"),
        _edge("cool_off", "nudge"),
        _edge("nudge", "out"),
    ],
}


async def test_long_wait_pauses_then_resumes_on_deadline() -> None:
    """A "wait two days, then follow up" workflow: the wait parks the run with
    a deadline (nothing downstream runs, no agent call), and the scheduler's
    resume — modelled here by naming the elapsed wait — finishes it WITHOUT
    sleeping again or re-running the replayed nodes."""
    compiled = compile_graph(WAIT_GRAPH, known_agents=KNOWN_AGENTS)

    parked = _Trace()
    paused = await execute_workflow(
        compiled, {"subject": "Pump order"}, parked.services(), emit=parked.emit
    )

    assert paused.status == "paused"
    assert paused.paused_nodes == ["cool_off"]
    assert parked.agent_calls == []  # the follow-up has NOT been sent
    assert paused.wait_until["cool_off"] > 0
    assert paused.node_results["nudge"]["status"] == "pending"
    assert parked.events[-2:] == [("nudge", "pending"), ("out", "pending")]

    resumed = _Trace()
    outcome = await execute_workflow(
        compiled,
        {"subject": "Pump order"},
        resumed.services(),
        emit=resumed.emit,
        precomputed={
            nid: r["output"]
            for nid, r in paused.node_results.items()
            if r["status"] == "ok"
        },
        elapsed_waits={"cool_off"},
    )

    assert outcome.status == "succeeded"
    assert outcome.node_results["cool_off"]["output"] == {"waited_seconds": 2 * 86400}
    assert len(resumed.agent_calls) == 1  # the follow-up fires exactly once
    assert outcome.outputs == ["  Pump order stuck at packing  "]


async def test_short_wait_never_pauses_the_run() -> None:
    """Under the inline threshold the run just sleeps and carries on — no
    pause row, no scheduler involvement."""
    graph = {**WAIT_GRAPH, "nodes": [
        _node("start", "trigger"),
        _node("cool_off", "wait", {"seconds": 0.01}),
        _node("nudge", "agent", {"agent": "summarizer", "message": "go"}),
        _node("out", "output", {"value": "{{nudge.result}}"}),
    ]}
    compiled = compile_graph(graph, known_agents=KNOWN_AGENTS)
    trace = _Trace()
    outcome = await execute_workflow(compiled, {}, trace.services(), emit=trace.emit)

    assert outcome.status == "succeeded"
    assert outcome.paused_nodes == [] and outcome.wait_until == {}
    assert len(trace.agent_calls) == 1
