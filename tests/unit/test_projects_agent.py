"""The Projects assistant agent (WS-27bm S1) — registration, identity, reads.

Spec: ``project-docs/specs/projects_ai_chat.md`` §10.1.

Three whole-surface properties, each of which has failed in this repo before
(``test_crm_agent.py`` carries the history):

1. **An agent that is not in BOTH registries silently never loads.** The
   gateway's ``_AGENT_REGISTRY`` is what ``local_path`` comes from and what
   the orchestrator imports for delegation. The catalog ``agent_registry.json``
   is what a person reads. Both are asserted, and they must agree.
2. **Every gateway call names the member it is for, or does not happen.** A
   bearer with no ``X-User-Email`` is the platform acting as itself. Asserted
   at the transport, with a fake httpx client, for every exported tool.
3. **A read tool issues GET and nothing else.** Asserted by observation over
   every invocation in the table below.

The agent module is loaded by file path (the Dynamic Agent Loader's own
path). No gateway and no MAF runtime are started.
"""

from __future__ import annotations

import inspect
import json
from typing import Any

import pytest

pytest.importorskip("skill_projects", reason="skill-projects not installed")
routes_agent = pytest.importorskip(
    "gateway.routes.agent",
    reason="gateway not installed",
)

import skill_projects  # noqa: E402
import skill_projects.client as client  # noqa: E402

from tests.unit._projects_agent_fakes import (  # noqa: E402
    AGENT_DIR,
    REPO_ROOT,
    empty_list,
    fake_gateway,
    load_agent_module,
    writes,
)

_M = load_agent_module()
AGENT = "projects-assistant"
UUID = "0f8fad5b-d9cb-469f-a165-70867728950e"


def _registry_entry(name: str) -> dict | None:
    return next(
        (e for e in routes_agent._AGENT_REGISTRY if e.get("name") == name),
        None,
    )


# ── Registration ─────────────────────────────────────────────────────────────


def test_the_agent_is_in_the_run_allowlist() -> None:
    assert AGENT in routes_agent._KNOWN_AGENTS


def test_the_agent_is_in_the_registry_the_orchestrator_imports() -> None:
    entry = _registry_entry(AGENT)
    assert entry is not None, "missing from _AGENT_REGISTRY — the agent silently never loads"
    assert entry["description"]


def test_registry_entry_points_at_the_real_agent_directory() -> None:
    entry = _registry_entry(AGENT)
    assert entry is not None
    assert entry["local_path"] == "apps/agents/agent-projects"
    agent_dir = REPO_ROOT / entry["local_path"]
    assert agent_dir == AGENT_DIR
    for name in ("agents.py", "config.json", "instructions.md", "pyproject.toml"):
        assert (agent_dir / name).is_file(), name


def test_the_agent_is_declared_maf_not_copilot() -> None:
    entry = _registry_entry(AGENT)
    assert entry is not None
    assert entry["agent_runtime"] == "maf"
    config = json.loads((AGENT_DIR / "config.json").read_text(encoding="utf-8"))
    assert config["runtime"] == "maf"


def test_the_catalog_registry_agrees_with_the_gateway_one() -> None:
    catalog = json.loads((REPO_ROOT / "agent_registry.json").read_text(encoding="utf-8"))
    entry = next((e for e in catalog if e.get("name") == AGENT), None)
    assert entry is not None
    assert entry["local_path"] == "apps/agents/agent-projects"
    assert entry["agent_runtime"] == "maf"


def test_config_scope_matches_the_exported_tools() -> None:
    config = json.loads((AGENT_DIR / "config.json").read_text(encoding="utf-8"))
    assert set(config["own_tool_scope"]) == set(skill_projects.__all__)
    assert config["skill_repos"] == ["skill-projects"]


def test_the_agent_carries_every_exported_tool() -> None:
    assert {fn.__name__ for fn in _M._TOOLS} == set(skill_projects.__all__)
    assert _M._register_agent_tools().keys() == set(skill_projects.__all__)


def test_all_tools_are_async_and_documented() -> None:
    for name in skill_projects.__all__:
        fn = getattr(skill_projects, name)
        assert inspect.iscoroutinefunction(fn), name
        assert (fn.__doc__ or "").strip(), f"{name} has no docstring — the model reads it"


def test_build_agents_constructs_one_native_maf_agent() -> None:
    pytest.importorskip("agent_framework", reason="MAF not installed")
    agents = _M.build_agents()
    assert len(agents) == 1
    assert agents[0].name == AGENT


# ── Identity at the transport ────────────────────────────────────────────────

#: One invocation per exported tool, with the arguments a real call carries.
_INVOCATIONS: dict[str, dict[str, Any]] = {
    "projects_tree": {},
    "project_summary": {"project_id": UUID},
    "find_tasks": {"query": "extruder"},
    "list_tasks": {"project_id": UUID, "status_category": "todo"},
    "task_detail": {"task_id": UUID},
    "my_work": {"view": "inbox"},
    "people_for": {"query": "pri"},
    "vocabulary": {"project_id": UUID},
    "analytics_stuck": {"project_id": UUID},
    "analytics_load": {},
    "analytics_throughput": {"project_id": UUID, "weeks": 4},
    "analytics_finished": {"weeks": 2, "skip_current_week": True},
    "analytics_outlook": {"project_id": UUID},
    "report_list": {},
    "report_render": {"report_id": UUID},
}


def test_the_invocation_table_covers_every_exported_tool() -> None:
    assert set(_INVOCATIONS) == set(skill_projects.__all__)


def _detail_responder(call: dict) -> Any:
    path = call["path"]
    if path.endswith("/relations"):
        return {"subtasks": [], "links": [], "blocked_by": [], "progress": {}}
    if path.startswith("/projects/tasks/") and path.count("/") == 3:
        return {
            "id": UUID,
            "title": "Fix the extruder",
            "task_number": 7,
            "status_id": "s1",
            "root_project_id": UUID,
            "project_id": UUID,
            "assignees": ["a@x.io"],
        }
    if path.startswith("/projects/reports/") and not path.endswith("/render"):
        return {"id": UUID, "name": "Weekly"}
    if path.startswith("/projects/nodes/") and path.endswith("/summary"):
        return {
            "id": UUID,
            "name": "Ops",
            "level": "space",
            "tasks": 3,
            "overdue": 1,
            "by_category": {"todo": 3},
            "own": {"tasks": 0, "overdue": 0},
            "children": [],
        }
    return empty_list(call)


@pytest.mark.parametrize("tool", sorted(_INVOCATIONS))
async def test_every_tool_sends_the_acting_user(tool: str, monkeypatch) -> None:
    calls = fake_gateway(monkeypatch, _detail_responder, user="pm@fracktal.in")
    await getattr(skill_projects, tool)(**_INVOCATIONS[tool])
    assert calls, f"{tool} made no gateway call"
    for call in calls:
        assert call["headers"]["X-User-Email"] == "pm@fracktal.in", tool
        assert call["headers"]["Authorization"].startswith("Bearer "), tool


@pytest.mark.parametrize("tool", sorted(_INVOCATIONS))
async def test_a_run_with_nobody_to_act_as_makes_no_call(tool: str, monkeypatch) -> None:
    calls = fake_gateway(monkeypatch, _detail_responder, user=None)
    with pytest.raises(client.GatewayRefusal):
        await getattr(skill_projects, tool)(**_INVOCATIONS[tool])
    assert calls == [], f"{tool} reached the gateway with no acting user"


@pytest.mark.parametrize("tool", sorted(_INVOCATIONS))
async def test_a_read_tool_issues_only_get(tool: str, monkeypatch) -> None:
    calls = fake_gateway(monkeypatch, _detail_responder)
    await getattr(skill_projects, tool)(**_INVOCATIONS[tool])
    assert writes(calls) == [], f"{tool} issued a non-GET: {writes(calls)}"


@pytest.mark.parametrize("tool", sorted(_INVOCATIONS))
async def test_every_call_a_tool_makes_is_a_manifest_route_for_that_tool(
    tool: str,
    monkeypatch,
) -> None:
    """The manifest says which routes a tool reaches. Hold the tool to it."""
    from skill_projects import manifest as m

    calls = fake_gateway(monkeypatch, _detail_responder)
    await getattr(skill_projects, tool)(**_INVOCATIONS[tool])
    for call in calls:
        row = m.route_for(call["method"], call["path"])
        assert row is not None, (
            f"{tool} called {call['method']} {call['path']}, not in the manifest"
        )
        # A list read may resolve status names through `vocabulary`'s route.
        assert row.tool in (tool, "vocabulary"), (
            f"{tool} called {call['method']} {call['path']}, which the manifest gives to {row.tool}"
        )


# ── Ids and paths ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("bad", ["../../admin/members", "not-a-uuid", "", "  "])
async def test_a_non_uuid_id_is_refused_before_any_request(bad: str, monkeypatch) -> None:
    calls = fake_gateway(monkeypatch, _detail_responder)
    with pytest.raises(client.GatewayRefusal):
        await skill_projects.task_detail(bad)
    assert calls == []


def test_uuid_of_canonicalises() -> None:
    assert client.uuid_of(UUID.upper()) == UUID
    assert client.uuid_of(UUID.replace("-", "")) == UUID


# ── Output conventions the cards read ────────────────────────────────────────


async def test_a_task_row_carries_full_id_on_the_next_line(monkeypatch) -> None:
    def responder(call: dict) -> Any:
        if call["path"] == "/projects/tasks":
            return {
                "rows": [
                    {
                        "id": UUID,
                        "title": "Call «the» vendor",
                        "task_number": 3,
                        "status_id": "s1",
                        "root_project_id": UUID,
                        "assignees": [],
                        "due_at": "2026-09-30T00:00:00+00:00",
                    }
                ],
                "total": 1,
            }
        if call["path"].endswith("/statuses"):
            return {"rows": [{"id": "s1", "name": "To do", "category": "todo"}]}
        return empty_list(call)

    fake_gateway(monkeypatch, responder)
    text = await skill_projects.list_tasks(project_id=UUID)
    lines = text.splitlines()
    head = next(i for i, ln in enumerate(lines) if ln.startswith("- #3 "))
    assert lines[head + 1].strip() == f"full_id: {UUID}"
    # The title is fenced, and an embedded guillemet cannot break the fence.
    assert "«Call the vendor»" in lines[head]
    assert "status To do" in lines[head]
    assert "due 2026-09-30" in lines[head]
    assert "unassigned" in lines[head]


async def test_find_tasks_refuses_a_short_query_without_a_call(monkeypatch) -> None:
    calls = fake_gateway(monkeypatch, _detail_responder)
    out = await skill_projects.find_tasks("ab")
    assert "3 characters" in out
    assert calls == []


async def test_a_gateway_404_is_relayed_as_not_visible(monkeypatch) -> None:
    import skill_projects.client as c

    from tests.unit._projects_agent_fakes import FakeClient, FakeResponse

    class NotFoundClient(FakeClient):
        async def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
            await super().request(method, url, **kwargs)
            return FakeResponse({"detail": "Task not found"}, status_code=404)

    calls: list[dict] = []
    from types import SimpleNamespace

    monkeypatch.setattr(
        c,
        "httpx",
        SimpleNamespace(AsyncClient=lambda **_kw: NotFoundClient(calls, {})),
    )
    monkeypatch.setattr(c, "current_user_email", lambda: "pm@fracktal.in")
    with pytest.raises(client.GatewayRefusal, match="not visible"):
        await skill_projects.task_detail(UUID)
