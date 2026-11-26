"""Golden workflow fixtures — one JSON file per scenario, one generic runner.

The pattern is n8n's (`packages/cli/test/integration/workflows/`): a corpus of
whole workflows paired with their expected outcome, executed by a single
runner. Adding coverage is adding a file, not writing code — which is the only
version of "we should have more tests" that survives contact with a deadline.

Each fixture in ``workflows/*.json``:

    {
      "name":        "lead-triage",
      "description": "why this workflow exists / what it pins",
      "catalog":     {"agents": [...], "modules": {"id": "<python>"}},
      "stubs":       {"agents": {...}, "tools": {...}},
      "graph":       {"nodes": [...], "edges": [...]},   # edit-model
      "trigger":     {...}, "variables": {...},
      "expect":      {...}
    }

``expect`` takes one of two shapes:

  publishable: false → compiling must FAIL, and ``issues`` lists the codes it
                       must report. These fixtures are the gates: a graph that
                       should never reach production.
  otherwise          → the run's status, per-node statuses and outputs, the
                       yielded outputs, and which seams were crossed. The
                       last part is what makes them trustworthy: asserting a
                       workflow "succeeded" while silently never calling the
                       integration is the failure mode this corpus exists to
                       stop.

Tool ARG SCHEMAS and DESTRUCTIVE actions come from the real registry, not the
fixture — so a fixture is validated against the catalog the product actually
ships, and renaming a required argument breaks the corpus rather than
production.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from gateway.routes.workflows.engine.graph import (
    GraphValidationError,
    compile_graph,
)
from gateway.routes.workflows.engine.handlers import NodeExecutionError, NodeServices
from gateway.routes.workflows.engine.runner import execute_workflow
from gateway.routes.workflows.tools import destructive_action_names, tool_arg_schemas

FIXTURE_DIR = Path(__file__).parent / "workflows"
FIXTURES = sorted(FIXTURE_DIR.glob("*.json"))


#: The org of each run here. ``MODULE_RUN_ORGS`` lists it, because the module
#: runner is dark by org (STU-Q15a). ``tests/unit/test_module_runs_off.py``
#: fences the switch, and this eval keeps its subject: the engine.
EVAL_ORG = "org-eval"


@pytest.fixture(autouse=True)
def _module_runs_on(monkeypatch: pytest.MonkeyPatch) -> None:
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "module_run_orgs", EVAL_ORG, raising=False)


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


class _Stubs:
    """Data-driven NodeServices: the fixture says what each seam returns.

    Every crossing is recorded, so a fixture can assert that a run reached the
    integration it claims to — and, on the resume path, that it did not reach
    it twice.
    """

    def __init__(self, spec: dict[str, Any]) -> None:
        self._agents: dict[str, str] = spec.get("agents") or {}
        self._tools: dict[str, Any] = spec.get("tools") or {}
        self._modules: dict[str, str] = spec.get("modules") or {}
        self.agent_calls: list[dict[str, Any]] = []
        self.tool_calls: list[dict[str, Any]] = []
        self.events: list[tuple[str, str]] = []

    async def run_agent(self, agent: str, message: str, model: str | None) -> str:
        self.agent_calls.append({"agent": agent, "message": message})
        if agent not in self._agents:
            raise NodeExecutionError(f"no stub reply for agent '{agent}'")
        return self._agents[agent]

    async def run_tool(self, action: str, args: dict[str, Any], actor: str) -> dict[str, Any]:
        self.tool_calls.append({"action": action, "args": args})
        if action not in self._tools:
            raise NodeExecutionError(f"no stub result for tool '{action}'")
        return self._tools[action]

    async def get_module_code(self, module_id: str) -> str | None:
        return self._modules.get(module_id)

    def services(self) -> NodeServices:
        return NodeServices(
            run_agent=self.run_agent,
            run_tool=self.run_tool,
            get_module_code=self.get_module_code,
            actor="eval:fixtures",
            organization_id=EVAL_ORG,
        )

    def emit(self, node_id: str, status: str, detail: dict[str, Any]) -> None:
        self.events.append((node_id, status))


def _compile(fixture: dict[str, Any]) -> dict[str, Any]:
    catalog = fixture.get("catalog") or {}
    return compile_graph(
        fixture["graph"],
        known_agents=set(catalog.get("agents") or []),
        known_modules=set((catalog.get("modules") or {}).keys()),
        destructive_actions=destructive_action_names(),
        tool_schemas=tool_arg_schemas(),
    )


def test_the_corpus_is_not_empty() -> None:
    """A globbing runner that finds nothing passes silently — the one way this
    whole file could stop testing anything without anyone noticing."""
    assert FIXTURES, f"no fixtures found in {FIXTURE_DIR}"


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
def test_fixture(path: Path) -> None:
    fixture = _load(path)
    expect = fixture["expect"]

    if expect.get("publishable") is False:
        with pytest.raises(GraphValidationError) as exc:
            _compile(fixture)
        codes = {issue.code for issue in exc.value.issues}
        expected = set(expect.get("issues") or [])
        assert expected <= codes, f"expected {sorted(expected)}, got {sorted(codes)}"
        return

    compiled = _compile(fixture)
    stubs = _Stubs(fixture.get("stubs") or {})
    stubs._modules = (fixture.get("catalog") or {}).get("modules") or {}
    outcome = _run(compiled, fixture, stubs)

    assert outcome.status == expect.get("status", "succeeded"), outcome.error
    if "error_contains" in expect:
        assert expect["error_contains"] in (outcome.error or "")
    if "outputs" in expect:
        assert outcome.outputs == expect["outputs"]

    for node_id, want in (expect.get("nodes") or {}).items():
        got = outcome.node_results.get(node_id)
        assert got is not None, f"node '{node_id}' produced no result"
        assert got["status"] == want["status"], f"{node_id}: {got}"
        if "output" in want:
            assert got.get("output") == want["output"], f"{node_id}: {got.get('output')}"

    # Seams: what the run actually touched, not just what it reported.
    if "agent_calls" in expect:
        assert [c["agent"] for c in stubs.agent_calls] == expect["agent_calls"]
    if "tool_calls" in expect:
        assert [c["action"] for c in stubs.tool_calls] == expect["tool_calls"]
    if "tool_args" in expect:
        assert [c["args"] for c in stubs.tool_calls] == expect["tool_args"]


def _run(compiled: dict[str, Any], fixture: dict[str, Any], stubs: _Stubs) -> Any:
    import asyncio

    return asyncio.run(
        execute_workflow(
            compiled,
            fixture.get("trigger") or {},
            stubs.services(),
            variables=fixture.get("variables") or {},
            emit=stubs.emit,
            resolved_approvals=set(fixture.get("approve") or []),
            elapsed_waits=set(fixture.get("elapsed_waits") or []),
        )
    )
