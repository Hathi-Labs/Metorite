"""Slice 2 unit tests — approval pause/resume + event triggers.

The approval node rides the Action Broker approvals inbox (spec F11); event
triggers ride the existing webhook plumbing (spec F10). These tests cover the
engine's pause/replay semantics, the pure event matcher, and the registered
broker handler — no Postgres, no LLM.
"""

from __future__ import annotations

import asyncio
from typing import Any, ClassVar

from gateway.routes.workflows import broker_handlers, service
from gateway.routes.workflows.engine.graph import compile_graph, validate_graph
from gateway.routes.workflows.engine.handlers import NodeServices
from gateway.routes.workflows.engine.runner import execute_workflow
from gateway.routes.workflows.triggers import event_trigger_matches

# ── Helpers (mirror test_workflows_engine.py) ───────────────────────────────


def _node(nid: str, ntype: str, config: dict | None = None) -> dict:
    return {
        "id": nid,
        "type": ntype,
        "position": {"x": 0, "y": 0},
        "data": {"label": nid, "config": config or {}},
    }


def _edge(src: str, tgt: str, handle: str | None = None) -> dict:
    e = {"id": f"{src}->{tgt}", "source": src, "target": tgt}
    if handle:
        e["sourceHandle"] = handle
    return e


def _services() -> NodeServices:
    async def run_agent(agent: str, message: str, model: str | None) -> str:
        return f"[{agent}] ok"

    async def run_tool(action: str, args: dict, actor: str) -> dict:
        return {"ok": True, "action": action}

    async def get_module_code(module_id: str) -> str | None:
        return None

    return NodeServices(
        run_agent=run_agent,
        run_tool=run_tool,
        get_module_code=get_module_code,
        actor="workflow:test",
    )


def _approval_graph() -> dict:
    return {
        "nodes": [
            _node("start", "trigger", {"kind": "manual"}),
            _node("classify", "agent", {"agent": "triage", "message": "x {{trigger.body}}"}),
            _node("gate", "approval", {"message": "OK to write?"}),
            _node("write", "tool", {"action": "notify.activity", "args": {"message": "hi"}}),
        ],
        "edges": [
            _edge("start", "classify"),
            _edge("classify", "gate"),
            _edge("gate", "write"),
        ],
    }


def _run(serialized: dict, **kw: Any):
    return asyncio.run(execute_workflow(serialized, {"body": "b"}, _services(), **kw))


# ── Approval node: graph + engine semantics ─────────────────────────────────


def test_approval_node_validates() -> None:
    assert validate_graph(_approval_graph(), known_agents={"triage"}) == []


def test_unresolved_approval_pauses_the_run() -> None:
    outcome = _run(compile_graph(_approval_graph()))
    assert outcome.status == "paused"
    assert outcome.paused_nodes == ["gate"]
    assert outcome.node_results["classify"]["status"] == "ok"
    assert outcome.node_results["gate"]["status"] == "waiting"
    # Downstream of the gate has not run — pending, not skipped.
    assert outcome.node_results["write"]["status"] == "pending"


def test_resume_replays_cached_nodes_and_passes_the_gate() -> None:
    serialized = compile_graph(_approval_graph())
    first = _run(serialized)
    precomputed = {
        nid: res["output"] for nid, res in first.node_results.items() if res.get("status") == "ok"
    }
    agent_calls: list[str] = []

    async def counting_agent(agent: str, message: str, model: str | None) -> str:
        agent_calls.append(agent)
        return "again"

    services = _services()
    services.run_agent = counting_agent
    outcome = asyncio.run(
        execute_workflow(
            serialized,
            {"body": "b"},
            services,
            precomputed=precomputed,
            resolved_approvals={"gate"},
        )
    )
    assert outcome.status == "succeeded"
    assert agent_calls == []  # cached — the agent did NOT run again
    assert outcome.node_results["classify"].get("cached") is True
    assert outcome.node_results["gate"]["status"] == "ok"
    assert outcome.node_results["gate"]["output"]["approved"] is True
    assert outcome.node_results["write"]["status"] == "ok"


def test_condition_branch_replays_correctly_on_resume() -> None:
    """A cached condition's stored branch must route the same way again."""
    graph = {
        "nodes": [
            _node("start", "trigger"),
            _node("check", "condition", {"left": "{{trigger.kind}}", "op": "equals", "right": "a"}),
            _node("gate", "approval", {}),
            _node("no", "output", {"value": "n"}),
            _node("yes_out", "output", {"value": "y"}),
        ],
        "edges": [
            _edge("start", "check"),
            _edge("check", "gate", "true"),
            _edge("check", "no", "false"),
            _edge("gate", "yes_out"),
        ],
    }
    serialized = compile_graph(graph)
    first = asyncio.run(execute_workflow(serialized, {"kind": "a"}, _services()))
    assert first.status == "paused"
    precomputed = {
        nid: res["output"] for nid, res in first.node_results.items() if res.get("status") == "ok"
    }
    resumed = asyncio.run(
        execute_workflow(
            serialized,
            {"kind": "a"},
            _services(),
            precomputed=precomputed,
            resolved_approvals={"gate"},
        )
    )
    assert resumed.status == "succeeded"
    assert resumed.outputs == ["y"]
    assert resumed.node_results["no"]["status"] == "skipped"


# ── Event trigger matching ──────────────────────────────────────────────────


def test_event_trigger_matches_source_and_type() -> None:
    assert event_trigger_matches(
        {"source": "clickup", "event_type": "taskUpdated"}, "clickup", "taskUpdated"
    )
    assert not event_trigger_matches(
        {"source": "clickup", "event_type": "taskUpdated"}, "clickup", "taskCreated"
    )
    assert not event_trigger_matches({"source": "zoho"}, "clickup", "taskUpdated")


def test_event_trigger_empty_type_matches_all() -> None:
    assert event_trigger_matches({"source": "clickup"}, "clickup", "anything")
    assert event_trigger_matches({"source": "clickup", "event_type": ""}, "clickup", "x")


# ── Broker handler ──────────────────────────────────────────────────────────


def test_resume_handler_registered_and_calls_service(monkeypatch) -> None:
    from action_broker.broker import _HANDLERS, clear_action_handlers

    clear_action_handlers()
    try:
        broker_handlers.register_handlers()
        assert "workflow.resume_run" in _HANDLERS

        calls: list[tuple[str, str | None]] = []

        async def fake_resume(
            run_id: str, *, resumed_by: str = "", organization_id: str | None = None,
        ) -> dict:
            calls.append((run_id, organization_id))
            return {"ok": True, "run_id": run_id}

        monkeypatch.setattr(service, "resume_run", fake_resume)

        class _Proposal:
            payload: ClassVar[dict] = {"run_id": "run-123", "organization_id": "spoofed"}

        from acb_common.db import bind_tenant, release_tenant

        token = bind_tenant("org-approver")
        try:
            result = asyncio.run(_HANDLERS["workflow.resume_run"](_Proposal()))
        finally:
            release_tenant(token)
        # The approver's bound org, never a payload field (R11).
        assert result["ok"] is True and calls == [("run-123", "org-approver")]
    finally:
        clear_action_handlers()


def test_resume_handler_with_no_bound_org_refuses(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_resume(run_id: str, **_kw) -> dict:
        calls.append(run_id)
        return {"ok": True}

    monkeypatch.setattr(service, "resume_run", fake_resume)

    class _Proposal:
        payload: ClassVar[dict] = {"run_id": "run-123"}

    result = asyncio.run(broker_handlers._resume_run_handler(_Proposal()))
    assert result["ok"] is False and calls == []


def test_resume_handler_without_run_id_fails_closed() -> None:
    class _Proposal:
        payload: ClassVar[dict] = {}

    result = asyncio.run(broker_handlers._resume_run_handler(_Proposal()))
    assert result["ok"] is False


# ── Startup reconciliation ──────────────────────────────────────────────────


class _FakeReconcileDb:
    """Captures the UPDATE the startup sweep issues (no Postgres)."""

    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount
        self.statements: list[str] = []
        self.committed = False

    async def execute(self, clause: Any, params: Any = None) -> Any:
        self.statements.append(str(clause))
        db = self

        class _Result:
            rowcount = db.rowcount

        return _Result()

    async def commit(self) -> None:
        self.committed = True

    async def close(self) -> None:
        return None


def test_reconcile_orphaned_runs_fails_running_only(monkeypatch) -> None:
    """The sweep flips 'running' rows to failed — and ONLY 'running' rows:
    paused runs must survive restarts (resume rebuilds from the snapshot)."""
    fake = _FakeReconcileDb(rowcount=3)

    async def fake_get_db() -> Any:
        return fake

    monkeypatch.setattr(service, "_get_db", fake_get_db)
    assert asyncio.run(service.reconcile_orphaned_runs()) == 3
    assert fake.committed
    sql = " ".join(fake.statements)
    assert "status = 'running'" in sql and "'failed'" in sql
    assert "interrupted by a platform restart" in sql
    assert "'paused'" not in sql


def test_reconcile_orphaned_runs_survives_db_outage(monkeypatch) -> None:
    """Best-effort at boot: a dead DB must not crash the gateway lifespan."""

    async def fake_get_db() -> Any:
        raise RuntimeError("postgres not up yet")

    monkeypatch.setattr(service, "_get_db", fake_get_db)
    assert asyncio.run(service.reconcile_orphaned_runs()) == 0
