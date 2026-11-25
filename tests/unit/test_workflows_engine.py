"""Workflow engine unit tests — templating, graph compile, MAF execution.

Covers the transport-free engine (gateway/routes/workflows/engine/): the
``{{…}}`` state bus, design-time validation, edit→run-model compilation, and
a full golden-trajectory run through the MAF-compiled workflow with stubbed
NodeServices (agents/tools/modules never leave the process).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest
from gateway.routes.workflows.engine.graph import (
    GraphValidationError,
    compile_graph,
    validate_graph,
)
from gateway.routes.workflows.engine.handlers import (
    NodeServices,
    evaluate_condition,
)
from gateway.routes.workflows.engine.runner import execute_workflow
from gateway.routes.workflows.engine.templating import (
    collect_refs,
    resolve_value,
)

# ── Templating ──────────────────────────────────────────────────────────────


def test_exact_ref_returns_native_value() -> None:
    state = {"clean": {"rows": [1, 2, 3], "count": 3}}
    assert resolve_value("{{clean.rows}}", state) == [1, 2, 3]
    assert resolve_value("{{clean.count}}", state) == 3


def test_embedded_ref_stringifies() -> None:
    state = {"trigger": {"who": "Ada"}, "n": {"count": 2}}
    assert resolve_value("Hi {{trigger.who}}, {{n.count}} new", state) == "Hi Ada, 2 new"


def test_unresolved_ref_kept_verbatim() -> None:
    assert resolve_value("{{missing.path}}", {}) == "{{missing.path}}"


def test_resolve_recurses_containers_and_list_index() -> None:
    state = {"t": {"items": ["a", "b"]}}
    resolved = resolve_value({"first": "{{t.items.0}}", "all": ["{{t.items}}"]}, state)
    assert resolved == {"first": "a", "all": [["a", "b"]]}


def test_collect_refs_finds_nested() -> None:
    config = {"msg": "x {{a.b}}", "args": {"v": "{{trigger.id}}"}}
    assert collect_refs(config) == {"a.b", "trigger.id"}


# ── Condition vocabulary ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("left", "op", "right", "expected"),
    [
        ("lead", "equals", "lead", True),
        ("lead", "not_equals", "spam", True),
        ("hello world", "contains", "world", True),
        ([1, 2], "contains", 2, True),
        ("5", "gt", 3, True),
        (2, "lte", "2", True),
        ("", "is_empty", None, True),
        ([1], "not_empty", None, True),
        (0, "truthy", None, False),
    ],
)
def test_evaluate_condition(left: Any, op: str, right: Any, expected: bool) -> None:
    assert evaluate_condition(left, op, right) is expected


# ── Graph validation + compile ──────────────────────────────────────────────


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


def _linear_graph() -> dict:
    return {
        "nodes": [
            _node("start", "trigger", {"kind": "manual"}),
            _node(
                "classify", "agent", {"agent": "triage", "message": "Classify: {{trigger.body}}"}
            ),
            _node("done", "output", {"value": "{{classify.result}}"}),
        ],
        "edges": [_edge("start", "classify"), _edge("classify", "done")],
    }


def test_valid_linear_graph_compiles() -> None:
    serialized = compile_graph(_linear_graph())
    assert serialized["entry"] == "start"
    assert {b["id"] for b in serialized["blocks"]} == {"start", "classify", "done"}
    assert len(serialized["connections"]) == 2


def test_missing_trigger_rejected() -> None:
    graph = _linear_graph()
    graph["nodes"] = [n for n in graph["nodes"] if n["id"] != "start"]
    codes = {i.code for i in validate_graph(graph)}
    assert "no_trigger" in codes


def test_unresolved_ref_flagged_with_node() -> None:
    graph = _linear_graph()
    graph["nodes"][2]["data"]["config"]["value"] = "{{nonexistent.field}}"
    issues = validate_graph(graph)
    refs = [i for i in issues if i.code == "unresolved_ref"]
    assert refs and refs[0].node_id == "done"


def test_downstream_ref_is_not_upstream() -> None:
    """A node cannot reference a node later in the flow."""
    graph = _linear_graph()
    graph["nodes"][1]["data"]["config"]["message"] = "{{done.value}}"
    assert any(i.code == "unresolved_ref" for i in validate_graph(graph))


def test_secret_shaped_string_blocks_publish() -> None:
    graph = _linear_graph()
    graph["nodes"][1]["data"]["config"]["message"] = "use sk-abcdefghijklmnop1234"
    assert any(i.code == "secret_in_config" for i in validate_graph(graph))


def test_cycle_rejected() -> None:
    graph = _linear_graph()
    graph["edges"].append(_edge("done", "classify"))
    codes = {i.code for i in validate_graph(graph)}
    assert "cycle" in codes


def test_fan_in_rejected_v1() -> None:
    graph = _linear_graph()
    graph["nodes"].append(_node("extra", "set", {"assignments": {"a": "1"}}))
    graph["edges"].append(_edge("start", "extra"))
    graph["edges"].append(_edge("extra", "done"))
    assert any(i.code == "fan_in" for i in validate_graph(graph))


def test_condition_edges_need_branch_handles() -> None:
    graph = {
        "nodes": [
            _node("start", "trigger"),
            _node("check", "condition", {"left": "{{trigger.x}}", "op": "truthy", "right": ""}),
            _node("yes", "output", {"value": "y"}),
        ],
        "edges": [_edge("start", "check"), _edge("check", "yes")],
    }
    assert any(i.code == "missing_branch_handle" for i in validate_graph(graph))


def test_unknown_agent_flagged_when_context_given() -> None:
    issues = validate_graph(_linear_graph(), known_agents={"task-manager"})
    assert any(i.code == "unknown_agent" for i in issues)
    assert not any(
        i.code == "unknown_agent" for i in validate_graph(_linear_graph(), known_agents={"triage"})
    )


def test_compile_raises_with_all_issues() -> None:
    graph = _linear_graph()
    graph["nodes"][2]["data"]["config"]["value"] = "{{nope.x}}"
    with pytest.raises(GraphValidationError) as excinfo:
        compile_graph(graph)
    assert any(i.code == "unresolved_ref" for i in excinfo.value.issues)


# ── Full engine runs (MAF-compiled, stub services) ──────────────────────────


def _stub_services(
    agent_replies: dict[str, str] | None = None,
    modules: dict[str, str] | None = None,
    tool_results: dict[str, dict] | None = None,
) -> NodeServices:
    async def run_agent(agent: str, message: str, model: str | None) -> str:
        return (agent_replies or {}).get(agent, f"[{agent}] {message}")

    async def run_tool(action: str, args: dict, actor: str) -> dict:
        return (tool_results or {}).get(action, {"ok": True, "args": args})

    async def get_module_code(module_id: str) -> str | None:
        return (modules or {}).get(module_id)

    return NodeServices(
        run_agent=run_agent,
        run_tool=run_tool,
        get_module_code=get_module_code,
        actor="workflow:test",
        organization_id="org-engine",
    )


def _run(serialized: dict, payload: dict, services: NodeServices, **kw: Any):
    return asyncio.run(execute_workflow(serialized, payload, services, **kw))


def test_linear_run_threads_state_through_nodes() -> None:
    serialized = compile_graph(_linear_graph())
    outcome = _run(
        serialized,
        {"body": "I want to buy a printer"},
        _stub_services(agent_replies={"triage": "lead"}),
    )
    assert outcome.status == "succeeded"
    assert outcome.node_results["classify"]["status"] == "ok"
    assert outcome.outputs == ["lead"]


def test_condition_routes_only_the_taken_branch() -> None:
    graph = {
        "nodes": [
            _node("start", "trigger"),
            _node(
                "check", "condition", {"left": "{{trigger.kind}}", "op": "equals", "right": "lead"}
            ),
            _node("yes", "tool", {"action": "notify.activity", "args": {"message": "lead!"}}),
            _node("no", "output", {"value": "ignored"}),
        ],
        "edges": [
            _edge("start", "check"),
            _edge("check", "yes", "true"),
            _edge("check", "no", "false"),
        ],
    }
    outcome = _run(compile_graph(graph), {"kind": "lead"}, _stub_services())
    assert outcome.status == "succeeded"
    assert outcome.node_results["yes"]["status"] == "ok"
    assert outcome.node_results["no"]["status"] == "skipped"

    outcome = _run(compile_graph(graph), {"kind": "spam"}, _stub_services())
    assert outcome.node_results["yes"]["status"] == "skipped"
    assert outcome.outputs == ["ignored"]


def test_module_node_runs_generated_code(monkeypatch: pytest.MonkeyPatch) -> None:
    from acb_common import get_settings

    # The runner is dark by org (STU-Q15a). `test_module_runs_off.py` fences it.
    monkeypatch.setattr(get_settings(), "module_run_orgs", "org-engine", raising=False)
    code = (
        "def run(inputs):\n"
        "    rows = [r for r in inputs.get('rows', []) if r.get('email')]\n"
        "    seen = {}\n"
        "    for r in rows:\n"
        "        seen[r['email'].lower()] = r\n"
        "    return {'rows': list(seen.values()), 'dropped': "
        "len(inputs.get('rows', [])) - len(seen)}\n"
    )
    graph = {
        "nodes": [
            _node("start", "trigger"),
            _node("clean", "module", {"module_id": "m1", "inputs": {"rows": "{{trigger.rows}}"}}),
            _node("done", "output", {"value": "{{clean.rows}}"}),
        ],
        "edges": [_edge("start", "clean"), _edge("clean", "done")],
    }
    rows = [
        {"email": "A@x.io"},
        {"email": "a@x.io"},
        {"name": "no-mail"},
    ]
    outcome = _run(
        compile_graph(graph, known_modules={"m1"}),
        {"rows": rows},
        _stub_services(modules={"m1": code}),
    )
    assert outcome.status == "succeeded"
    assert outcome.node_results["clean"]["output"]["dropped"] == 2
    assert len(outcome.outputs[0]) == 1


def test_node_failure_fails_the_run_and_marks_downstream_skipped() -> None:
    graph = {
        "nodes": [
            _node("start", "trigger"),
            _node("boom", "module", {"module_id": "missing", "inputs": {}}),
            _node("after", "output", {"value": "never"}),
        ],
        "edges": [_edge("start", "boom"), _edge("boom", "after")],
    }
    outcome = _run(
        compile_graph(graph, known_modules={"missing"}),
        {},
        _stub_services(),
    )
    assert outcome.status == "failed"
    assert "boom" in (outcome.error or "")
    assert outcome.node_results["boom"]["status"] == "error"
    assert outcome.node_results["after"]["status"] == "skipped"


def test_set_node_merges_vars_and_emit_sequence() -> None:
    events: list[tuple[str, str]] = []
    graph = {
        "nodes": [
            _node("start", "trigger"),
            _node("assign", "set", {"assignments": {"owner": "{{trigger.email}}"}}),
            _node("done", "output", {"value": "{{vars.owner}}"}),
        ],
        "edges": [_edge("start", "assign"), _edge("assign", "done")],
    }
    outcome = _run(
        compile_graph(graph),
        {"email": "ops@fracktal.works"},
        _stub_services(),
        emit=lambda nid, status, detail: events.append((nid, status)),
    )
    assert outcome.status == "succeeded"
    assert outcome.outputs == ["ops@fracktal.works"]
    assert ("assign", "running") in events and ("assign", "ok") in events


def test_fan_out_runs_both_branches() -> None:
    graph = {
        "nodes": [
            _node("start", "trigger"),
            _node("left", "output", {"value": "L"}),
            _node("right", "output", {"value": "R"}),
        ],
        "edges": [_edge("start", "left"), _edge("start", "right")],
    }
    outcome = _run(compile_graph(graph), {}, _stub_services())
    assert outcome.status == "succeeded"
    assert sorted(outcome.outputs) == ["L", "R"]


# ── Schedule due-ness math ──────────────────────────────────────────────────


def test_compute_due_fire_collapses_downtime_to_one_tick() -> None:
    from gateway.routes.workflows.scheduler import compute_due_fire

    now = datetime(2026, 7, 30, 12, 0, 30, tzinfo=UTC)
    last = datetime(2026, 7, 30, 9, 0, 0, tzinfo=UTC)
    due = compute_due_fire("0 * * * *", last, now)  # hourly; 3 ticks missed
    assert due == datetime(2026, 7, 30, 12, 0, 0, tzinfo=UTC)


def test_compute_due_fire_none_when_not_due() -> None:
    from gateway.routes.workflows.scheduler import compute_due_fire

    now = datetime(2026, 7, 30, 12, 0, 30, tzinfo=UTC)
    last = datetime(2026, 7, 30, 12, 0, 0, tzinfo=UTC)
    assert compute_due_fire("0 * * * *", last, now) is None


def test_compute_due_fire_never_fired_waits_for_next_tick() -> None:
    from gateway.routes.workflows.scheduler import compute_due_fire

    now = datetime(2026, 7, 30, 12, 30, 0, tzinfo=UTC)
    assert compute_due_fire("0 * * * *", None, now) is None


# ── Route registration order (load-bearing — see routes/workflows/__init__) ─


def test_static_routes_register_before_workflow_id_routes() -> None:
    """`GET /workflows/modules` must never be swallowed by `/{workflow_id}`.

    FastAPI matches in registration order, and the package registers routes
    as an import side effect — so an alphabetized import block in
    ``routes/workflows/__init__`` silently breaks routing. This pins it.
    """
    from gateway.routes.workflows import router

    paths = [r.path for r in router.routes]
    for static in ("/workflows/catalog", "/workflows/modules"):
        assert paths.index(static) < paths.index("/workflows/{workflow_id}"), (
            f"{static} registered after /workflows/{{workflow_id}} — "
            "restore the import order in routes/workflows/__init__.py"
        )
