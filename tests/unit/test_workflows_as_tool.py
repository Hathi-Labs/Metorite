"""F13 (workflows as agent tools) + the write-without-approval publish gate.

The orchestrator's workflow tool trio runs against monkeypatched service
helpers (no Postgres); the gate tests exercise the validator context that
publish/validate/copilot all pass.

🔧 **RE-CUT 2026-08-25 (D52 repair round 1, board WS-39 S1).**
`test_destructive_registry_names_clickup_write` asserted
``"clickup.create_task" in destructive_action_names()``. D52 deleted that
`WorkflowToolSpec` — it was the registry's ONLY `destructive=True` entry — so
the case had been RED on this branch since the excision. It is replaced by a
test of the helper's DERIVATION (does it read `spec.destructive`?), which is
what actually needs fencing and which outlives any particular tool. The claim
about the registry's current CONTENTS lives in
`tests/unit/test_no_task_provider_connectors.py::
test_the_workflow_registry_has_no_destructive_tool` — a tripwire, expected to
go red when the next destructive tool lands.

⚠️ The three gate tests below were never affected: `validate_graph` takes
`destructive_actions` as an ARGUMENT, so their action name is an arbitrary
string the validator never resolves. It has been renamed off the retired tool
so the file stops implying that tool exists.
"""

from __future__ import annotations

import asyncio
import json

from gateway.routes.workflows.engine.graph import validate_graph
from gateway.routes.workflows.tools import destructive_action_names
from orchestrator import workflow_tools

# ── The publish gate ────────────────────────────────────────────────────────


def _node(nid: str, ntype: str, config: dict | None = None) -> dict:
    return {
        "id": nid,
        "type": ntype,
        "position": {"x": 0, "y": 0},
        "data": {"label": nid, "config": config or {}},
    }


def _edge(src: str, tgt: str) -> dict:
    return {"id": f"{src}->{tgt}", "source": src, "target": tgt}


def _write_graph(with_approval: bool) -> dict:
    nodes = [
        _node("start", "trigger"),
        _node("write", "tool", {"action": "demo.write", "args": {}}),
    ]
    edges = [_edge("start", "write")]
    if with_approval:
        nodes.insert(1, _node("gate", "approval", {}))
        edges = [_edge("start", "gate"), _edge("gate", "write")]
    return {"nodes": nodes, "edges": edges}


def test_destructive_names_are_derived_from_the_specs_own_flag() -> None:
    """`destructive_action_names()` reads `spec.destructive`, not a hand-kept
    list — proven by REGISTERING one of each and watching the answer follow.

    That derivation is what the publish gate's refusal set depends on. Asserting
    a particular tool's presence (as this case did for `clickup.create_task`
    until D52 deleted it) fences the catalog, not the helper — and goes red the
    day the catalog legitimately changes.
    """
    from gateway.routes.workflows import tools as wf_tools

    before = destructive_action_names()
    added = []
    for action, destructive in (("demo.write", True), ("demo.read", False)):
        wf_tools.register_tool(wf_tools.WorkflowToolSpec(
            action=action, label=action, description="", integration=None,
            read_only=not destructive, destructive=destructive,
            open_world=False,
        ))
        added.append(action)
    try:
        names = destructive_action_names()
        assert "demo.write" in names
        assert "demo.read" not in names
    finally:
        for action in added:
            wf_tools._TOOL_REGISTRY.pop(action, None)
    assert destructive_action_names() == before


def test_write_without_approval_blocks() -> None:
    issues = validate_graph(
        _write_graph(with_approval=False),
        destructive_actions={"demo.write"},
    )
    assert any(i.code == "write_without_approval" and i.node_id == "write" for i in issues)


def test_write_with_approval_upstream_passes() -> None:
    issues = validate_graph(
        _write_graph(with_approval=True),
        destructive_actions={"demo.write"},
    )
    assert not any(i.code == "write_without_approval" for i in issues)


def test_gate_is_context_gated_not_structural() -> None:
    """Without the context (editor badges pre-catalog), the gate stays quiet."""
    issues = validate_graph(_write_graph(with_approval=False))
    assert not any(i.code == "write_without_approval" for i in issues)


# ── Workflow tools (orchestrator side) ──────────────────────────────────────


def _patch_service(monkeypatch, **overrides) -> dict:
    """Monkeypatch the gateway service helpers workflow_tools calls."""
    import gateway.routes.workflows.service as svc

    calls: dict = {"run": [], "summary": []}

    async def list_published_workflows():
        return [{"id": "w1", "name": "Lead intake", "description": "…", "version": 3}]

    async def run_published_workflow(ref, payload, *, started_by):
        calls["run"].append((ref, payload, started_by))
        if ref == "ghost":
            return {"error": "no published workflow named or with id 'ghost'"}
        return {"run_id": "r1", "workflow_id": "w1", "workflow_name": "Lead intake", "version": 3}

    summaries = overrides.get(
        "summaries",
        [
            {
                "run_id": "r1",
                "workflow_name": "Lead intake",
                "status": "succeeded",
                "error": None,
                "output": ["done"],
            }
        ],
    )

    async def run_summary(run_id):
        calls["summary"].append(run_id)
        return summaries[min(len(calls["summary"]) - 1, len(summaries) - 1)]

    monkeypatch.setattr(svc, "list_published_workflows", list_published_workflows)
    monkeypatch.setattr(svc, "run_published_workflow", run_published_workflow)
    monkeypatch.setattr(svc, "run_summary", run_summary)
    return calls


def test_list_workflows_tool(monkeypatch) -> None:
    _patch_service(monkeypatch)
    out = asyncio.run(workflow_tools.list_workflows())
    assert "Lead intake" in out and json.loads(out)[0]["id"] == "w1"


def test_run_workflow_fire_and_forget(monkeypatch) -> None:
    calls = _patch_service(monkeypatch)
    out = asyncio.run(workflow_tools.run_workflow("Lead intake", '{"body": "x"}'))
    assert "run_id=r1" in out
    assert calls["run"] == [("Lead intake", {"body": "x"}, "agent-tool")]
    assert calls["summary"] == []  # no wait → no polling


def test_run_workflow_waits_and_reports_output(monkeypatch) -> None:
    _patch_service(monkeypatch)
    out = asyncio.run(workflow_tools.run_workflow("Lead intake", "{}", wait_seconds=5))
    assert "succeeded" in out and "done" in out


def test_run_workflow_reports_paused_as_waiting_for_inbox(monkeypatch) -> None:
    _patch_service(
        monkeypatch,
        summaries=[
            {
                "run_id": "r1",
                "workflow_name": "Lead intake",
                "status": "paused",
                "error": None,
                "output": None,
            }
        ],
    )
    out = asyncio.run(workflow_tools.run_workflow("Lead intake", "{}", wait_seconds=5))
    assert "paused" in out and "approvals inbox" in out


def test_run_workflow_bad_payload_and_unknown_ref(monkeypatch) -> None:
    _patch_service(monkeypatch)
    assert "not valid JSON" in asyncio.run(workflow_tools.run_workflow("Lead intake", "{nope"))
    assert "Could not start" in asyncio.run(workflow_tools.run_workflow("ghost", "{}"))


def test_load_workflow_tools_returns_trio_with_annotations() -> None:
    from acb_skills.tool_annotations import TOOL_ANNOTATIONS

    tools = workflow_tools.load_workflow_tools("triage")
    names = {t.__name__ for t in tools}
    assert names == {"list_workflows", "run_workflow", "get_workflow_run"}
    assert TOOL_ANNOTATIONS["list_workflows"]["read_only"] is True
    assert TOOL_ANNOTATIONS["run_workflow"]["destructive"] is False
