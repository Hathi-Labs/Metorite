"""STU-E4: the human step node, hermetic half.

Spec: ``project-docs/specs/ai_studio.md`` §3.3 and §14 row E4. Code:
``routes/workflows/human_step.py``, the runner, the graph check,
``service._hold_at_human_step`` and ``scheduler.scan_done_tasks``. The R8 half
is ``test_workflows_human_step_r8.py``.

**Mutations this half catches** (each was run, see the R8 file for the rest):

* H1. ``graph._validate_human_step_config``: allow a ``{{ref}}`` assignee (the
  template case red).
* H2. ``runner._gate_pause``: never pause a human step (the pause case red).
* H3. ``human_step.open_step``: ``announce=True`` (the announce scan red).
* H4. ``human_step.task_outcome``: resume on a cancelled stage (the outcome
  case red).
* H5. ``service._MEMBER_BLIND_WRITE_TYPES``: add ``human_step`` (the doors case
  red, because the step makes a new private task and names no other).
"""
from __future__ import annotations

import ast
import asyncio
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("gateway.routes.projects", reason="gateway not installed")

from gateway.routes.workflows import human_step, service
from gateway.routes.workflows.engine.graph import (
    NODE_TYPES,
    GraphValidationError,
    compile_graph,
    validate_graph,
)
from gateway.routes.workflows.engine.handlers import NodeServices
from gateway.routes.workflows.engine.runner import execute_workflow

_ROOT = Path(__file__).resolve().parents[2]
_WORKFLOWS = _ROOT / "apps" / "services" / "gateway" / "gateway" / "routes" / "workflows"


def _node(nid: str, ntype: str, config: dict | None = None, label: str | None = None) -> dict:
    return {"id": nid, "type": ntype, "position": {"x": 0, "y": 0},
            "data": {"label": label or nid, "config": config or {}}}


def _graph(step_config: dict | None) -> dict:
    return {
        "nodes": [
            _node("start", "trigger", {"kind": "manual"}),
            _node("step", "human_step", step_config),
            _node("done", "output", {"value": "end"}),
        ],
        "edges": [
            {"id": "a", "source": "start", "target": "step"},
            {"id": "b", "source": "step", "target": "done"},
        ],
    }


_GOOD = {"assignee": "Lena@Example.test", "title": "Check the contract"}


def _codes(config: dict | None) -> list[str]:
    return [i.code for i in validate_graph(_graph(config))]


# ── The graph check ─────────────────────────────────────────────────────────


def test_the_node_type_is_registered() -> None:
    assert "human_step" in NODE_TYPES


def test_a_valid_step_compiles_and_keeps_its_literal_config() -> None:
    serialized = compile_graph(_graph({**_GOOD, "description": "Read page 3."}))
    block = next(b for b in serialized["blocks"] if b["id"] == "step")
    assert block["type"] == "human_step"
    assert block["config"]["assignee"] == "Lena@Example.test"
    assert human_step.human_step_nodes(serialized) == ["step"]


@pytest.mark.parametrize(
    "config",
    [
        None,
        {},
        {"title": "x"},
        {"assignee": "not-an-email", "title": "x"},
        {"assignee": "a@b.test"},
        {"assignee": "a@b.test", "title": "   "},
        # A template never picks the person: a payload could name anyone.
        {"assignee": "{{trigger.who}}", "title": "x"},
        {"assignee": "{{trigger.who}}@example.test", "title": "x"},
        {"assignee": "a@b.test", "title": "Check {{trigger.name}}"},
        {"assignee": "a@b.test", "title": "x", "description": "{{trigger.body}}"},
        {"assignee": "a@b.test", "title": "x", "project_id": "p"},
    ],
)
def test_a_bad_step_is_refused_at_publish(config: dict | None) -> None:
    with pytest.raises(GraphValidationError):
        compile_graph(_graph(config))
    assert _codes(config)


# ── The runner ──────────────────────────────────────────────────────────────


def _services() -> NodeServices:
    return NodeServices(
        run_agent=None, run_tool=None, get_module_code=None, actor="t",
    )


def _run(done: dict | None) -> Any:
    serialized = compile_graph(_graph(_GOOD))
    return asyncio.run(execute_workflow(
        serialized, {}, _services(), done_human_steps=done,
    ))


def test_a_human_step_pauses_the_run() -> None:
    outcome = _run(None)
    assert outcome.status == "paused"
    assert outcome.paused_nodes == ["step"]
    assert outcome.node_results["step"]["status"] == "waiting"
    assert outcome.node_results["done"]["status"] == "pending"


def test_a_done_step_runs_through_and_carries_the_task_id() -> None:
    outcome = _run({"step": "task-1"})
    assert outcome.status == "succeeded", outcome.error
    assert outcome.node_results["step"]["output"] == {"completed": True, "task_id": "task-1"}
    assert outcome.outputs == ["end"]


def test_a_step_that_is_not_named_still_pauses() -> None:
    assert _run({"other": "task-1"}).status == "paused"


# ── The outcome rule ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("row", "want"),
    [
        (None, human_step.GONE),
        (SimpleNamespace(category="done"), human_step.RESUME),
        (SimpleNamespace(category="cancelled"), human_step.CANCELLED),
        (SimpleNamespace(category="backlog"), human_step.WAIT),
        (SimpleNamespace(category="todo"), human_step.WAIT),
        (SimpleNamespace(category="in_progress"), human_step.WAIT),
        (SimpleNamespace(category="triage"), human_step.WAIT),
        # The default personal tree has no cancelled lane, so these end the step too.
        (SimpleNamespace(category="todo", archived_at="2026-01-01"), human_step.CANCELLED),
        (SimpleNamespace(category="todo", trashed=True), human_step.CANCELLED),
        (SimpleNamespace(category="done", trashed=True), human_step.CANCELLED),
        (SimpleNamespace(category="todo", assignee_active=False), human_step.CANCELLED),
        # A done task that the lifecycle sweep archived still resumes the run.
        (SimpleNamespace(category="done", archived_at="2026-01-01"), human_step.RESUME),
        (SimpleNamespace(category="done", assignee_active=False), human_step.RESUME),
    ],
)
def test_the_outcome_comes_from_the_stage_of_the_task_row(row: Any, want: str) -> None:
    assert human_step.task_outcome(row) == want


def test_the_task_id_comes_from_the_snapshot_and_must_be_a_uuid() -> None:
    good = "0d4b8f5e-0000-4000-8000-0000000000a1"
    assert human_step.task_id_of({"task_id": good}) == good
    for bad in (None, {}, {"task_id": ""}, {"task_id": "x"}, "text", {"task_id": 5}):
        assert human_step.task_id_of(bad) is None


def test_the_task_carries_a_link_to_its_run_and_plain_text() -> None:
    values = human_step.task_values(
        config={"title": "  Check\n the contract ", "description": "Read page 3."},
        workflow_id="w-1", workflow_name="Onboarding", run_id="r-1", node_id="step",
    )
    assert values["title"] == "Check the contract"
    assert values["source"] == "automation"
    assert values["origin"] == {
        "kind": "workflow_run", "run_id": "r-1", "workflow_id": "w-1", "node_id": "step",
    }
    assert "Read page 3." in values["description"]
    with pytest.raises(human_step.StepRefused):
        human_step.task_values(
            config={"title": " "}, workflow_id="w", workflow_name="n", run_id="r", node_id="s",
        )


def test_the_error_texts_name_no_task_id() -> None:
    for text_ in (human_step.CANCELLED_ERROR, human_step.GONE_ERROR,
                  human_step.REFUSED_ERROR, human_step.NOT_READY_ERROR):
        assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}", text_)
        assert len(text_) < 200


# ── The source fences ───────────────────────────────────────────────────────


def _src(name: str) -> str:
    return (_WORKFLOWS / name).read_text(encoding="utf-8")


def _calls(tree: ast.AST, name: str) -> list[ast.Call]:
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call)
            and getattr(n.func, "id", getattr(n.func, "attr", None)) == name]


def test_the_task_is_made_with_no_created_event() -> None:
    """``announce=False``: an event could start a workflow that makes the next
    task, with no end. ``personal.create_personal_task`` has the one rule."""
    calls = _calls(ast.parse(_src("human_step.py")), "create_personal_task")
    assert len(calls) == 1
    kw = {k.arg: k.value for k in calls[0].keywords}
    assert isinstance(kw.get("announce"), ast.Constant) and kw["announce"].value is False


def test_the_step_is_not_a_member_blind_write() -> None:
    """The step names no existing task. It makes one NEW private task for an
    active member of the org, so the doors that refuse ``pm_task`` need not
    refuse it. The test names the choice, so a change is a decision."""
    assert "human_step" not in service._MEMBER_BLIND_WRITE_TYPES
    serialized = compile_graph(_graph(_GOOD))
    assert service._member_blind_write_nodes(serialized) == []


def test_the_task_names_the_workflow_as_its_actor() -> None:
    calls = _calls(ast.parse(_src("human_step.py")), "create_personal_task")
    kw = {k.arg for k in calls[0].keywords}
    assert "actor" in kw


def test_the_assignee_is_checked_before_any_write() -> None:
    func = next(n for n in ast.parse(_src("human_step.py")).body
                if isinstance(n, ast.AsyncFunctionDef) and n.name == "open_step")
    line = {}
    for c in ast.walk(func):
        if isinstance(c, ast.Call):
            name = getattr(c.func, "attr", getattr(c.func, "id", ""))
            line[name] = min(line.get(name, c.lineno), c.lineno)
    first_write = min(line["ensure_personal_project"], line["create_personal_task"])
    assert line["_owner_in_org"] < first_write


def test_an_approval_card_never_resumes_a_human_step() -> None:
    src = _src("service.py")
    assert "AND reason <> 'task'" in src
    resume = next(n for n in ast.parse(src).body
                  if isinstance(n, ast.AsyncFunctionDef) and n.name == "resume_run")
    assert "task_done" in {a.arg for a in resume.args.kwonlyargs}
    # The scan alone passes it.
    callers = [p.name for p in _WORKFLOWS.glob("*.py")
               if "task_done=True" in p.read_text(encoding="utf-8")]
    assert callers == ["scheduler.py"]


def test_the_scan_binds_the_org_and_lives_in_the_one_org_loop() -> None:
    src = _src("scheduler.py")
    tree = ast.parse(src)
    scan = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)
                and n.name == "scan_done_tasks")
    body = ast.get_source_segment(src, scan) or ""
    assert "_tenant_session(org)" in body
    assert "_tenant_session()" not in body
    assert "organization_id" in body
    wait = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)
                and n.name == "scan_due_waits")
    org_loop = ast.get_source_segment(src, wait) or ""
    assert "unattended.org_allowed(org)" in org_loop
    assert org_loop.index("unattended.org_allowed(org)") < org_loop.index("scan_done_tasks(org)")
    # One scheduler: no second loop over the orgs.
    assert src.count("await _organization_ids()") == 2


def test_the_task_id_never_comes_from_a_payload() -> None:
    body = _src("scheduler.py")
    scan = body[body.index("async def scan_done_tasks"):body.index("async def _scheduler_loop")]
    assert "trigger_payload" not in scan
    assert "human_step.task_id_of(snapshot)" in scan


def test_the_r8_suite_is_named_in_the_ci_skip_guard() -> None:
    workflow = (_ROOT / ".github" / "workflows" / "pr-check.yml").read_text(encoding="utf-8")
    assert re.search(r"^\s+tests/unit/test_workflows_human_step_r8\.py \\?$", workflow, re.M)


def test_the_migration_widens_the_reason_and_takes_the_next_number() -> None:
    sql = (_ROOT / "infra" / "postgres" / "427_workflow_pause_task.sql").read_text(
        encoding="utf-8")
    assert "DROP CONSTRAINT IF EXISTS workflow_run_pauses_reason_check" in sql
    assert "('approval', 'wait', 'oauth', 'task')" in sql
    assert not re.search(r"\b(DROP TABLE|DROP COLUMN|RENAME)\b", sql)
