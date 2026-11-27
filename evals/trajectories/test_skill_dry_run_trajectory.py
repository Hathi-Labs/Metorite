"""Golden trajectory: a skill test holds its write on a real turn (WS-STU F4b).

Spec: ``project-docs/specs/ai_studio_rows/F4.md``. The real
``skill_tests.run_suite`` drives the real ``agent_runner``, so the real
``executor.run_agent_stream`` of the orchestrator runs with the tool packs
on. Only three things are fakes: the HTTP transport of the model (a scripted
model, ``tests/unit/_native_maf_harness.py``), the HTTP transport of the
Projects client (a fake gateway), and the meter. No live model call and no
credit read happen. A call to the Router or to the direct arm fails the test.

The turn:

1. The model calls ``create_task``. The hold records it, and no write
   reaches the gateway.
2. The model calls ``list_tasks``. It is on the allowlist, so it runs for
   real, and its GET reaches the fake gateway. This proves that the tool
   object of the call is the object that the attach captured.
3. The model answers. Three model requests pass ``CapGuard``.

**The mutations (R7).**

* Make ``_tool_injection._apply_dry_run`` return at once. The write runs,
  a POST reaches the gateway, and the door reads unarmed → red.
* In ``_turn_with_skills``, drop the dry-run branch. The words name no
  skill, so the skill does not load → ``skill_unloaded`` → red.
"""
from __future__ import annotations

import asyncio
import json
import types
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest

pytest.importorskip("agent_framework", reason="MAF not installed")

from acb_common import get_settings
from gateway.studio import skill_tests as st
from gateway.studio import skills_store as store
from orchestrator import _tool_injection as ti
from orchestrator import executor as ex

from tests.unit._egress_mark_store import install_memory_mark_store
from tests.unit._native_maf_harness import ScriptedModel, text_turn, tool_turn
from tests.unit.test_skill_tests import install_slot_redis

REPO = Path(__file__).resolve().parents[2]
ORG = "0a0a0a0a-0000-4000-8000-0000000f4b01"
MEMBER = "jane@acme.test"
SID = "00000000-0000-4000-8000-0000000f4b02"
PROJECT = "11111111-1111-4111-8111-111111111111"
CONFIG = json.loads((REPO / "apps/agents/agent-orchestrator/config.json")
                    .read_text(encoding="utf-8"))
BODY = {
    "description": "Use it to add a task.",
    "instructions": "Add the task the member names, then list the tasks.",
    "actions": ["tool:create_task", "tool:list_tasks"],
    "tests": [{"id": "t1", "input": "Add a task: Call Ravi",
               "expect": [{"action": "tool:create_task", "fields": {"title": "Call Ravi"}}]}],
}
_NOW = datetime(2026, 11, 19, tzinfo=UTC)


def _turns() -> list[list[dict[str, Any]]]:
    return [
        tool_turn("create_task", json.dumps({"project_id": PROJECT, "title": "Call Ravi"}), "c1"),
        tool_turn("list_tasks", "{}", "c2"),
        text_turn("Done."),
    ]


@pytest.fixture
def world(monkeypatch, tmp_path):
    """The packs on, a cap set, the fakes in place, and the guards on every
    other model path."""
    install_memory_mark_store(monkeypatch)
    install_slot_redis(monkeypatch)  # the slot of a suite, in memory
    settings = get_settings()
    for key, value in (("tool_packs", "*"), ("tool_packs_orgs", "*"),
                       ("studio_chat_tools_orgs", "*"), ("studio_skill_tests_cap_credits", "5"),
                       ("agents_clone_dir", str(tmp_path / "agents"))):
        monkeypatch.setattr(settings, key, value, raising=False)
    w = types.SimpleNamespace(model=ScriptedModel(_turns()), built=[], gateway=[], side=[])

    # The other model paths: each one records and refuses.
    from acb_llm import routed

    async def _no_router(*_a: Any, **_k: Any) -> Any:
        w.side.append("router")
        raise AssertionError("a model call outside the scripted model")

    monkeypatch.setattr(routed, "completion_on_router", _no_router)
    monkeypatch.setattr(routed, "stream_on_router", _no_router)
    monkeypatch.setattr(routed, "direct_arm_allowed", lambda: False)

    # The agent: the real orchestrator, with the scripted model on its wire.
    routes_agent = pytest.importorskip("gateway.routes.agent")
    monkeypatch.setattr(routes_agent, "_load_dynamic_agents", lambda: [])
    monkeypatch.setattr(ti, "_load_disabled_skill_families", lambda _name: frozenset())
    from orchestrator.agents import build_orchestrator_agent

    def _build() -> list[Any]:
        agent = build_orchestrator_agent(with_history=False)
        oc = agent.client.client
        oc._client = httpx.AsyncClient(transport=httpx.MockTransport(w.model),
                                       event_hooks=oc._client.event_hooks)
        w.built.append(agent)
        return [agent]

    class _Loaded:
        agent_dir = REPO / "apps/agents/agent-orchestrator"
        agent_name = "orchestrator"
        config = CONFIG

        def build_agents(self) -> list[Any]:
            return _build()

        def __enter__(self) -> _Loaded:
            return self

        def __exit__(self, *_a: Any) -> bool:
            return False

    monkeypatch.setattr(ex, "load_agent", lambda *_a, **_k: _Loaded())
    monkeypatch.setattr(ex, "build_integrations", lambda *a, **k: ({}, {}))

    # The gateway of the Projects tools: a fake that records each request.
    from skill_projects import client as projects_client

    def _gateway(request: httpx.Request) -> httpx.Response:
        w.gateway.append((request.method, request.url.path))
        return httpx.Response(200, json={"rows": [], "total": 0})

    real = httpx.AsyncClient

    def _client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(_gateway)
        return real(*args, **kwargs)

    proxy = types.SimpleNamespace(**{k: getattr(httpx, k) for k in dir(httpx)
                                     if not k.startswith("_")})
    proxy.AsyncClient = _client
    monkeypatch.setattr(projects_client, "httpx", proxy)

    # The skill under test.
    async def _load(org: str, member: str, skill_id: str, version: int | None) -> Any:
        skill = store.Skill(id=SID, name="Add a task", slug="add-a-task", description="",
                            owner_email=MEMBER, visibility="private", state="draft",
                            current_version=1, created_at=_NOW, updated_at=_NOW)
        return skill, store.Version(skill_id=SID, version=1, body=BODY, content_hash="h",
                                    created_by=MEMBER, created_at=_NOW)

    monkeypatch.setattr(st, "_load_skill", _load)
    return w


class _Meter:
    """A fake meter. It answers *values* in order, the last one again, and
    records the run context of each read: ``console_meter`` reads only for
    the proven member."""

    def __init__(self, values: list[Decimal]) -> None:
        self.values = values
        self.seen: list[tuple[Any, Any]] = []

    async def __call__(self, org: str, member: str, run_id: str) -> Decimal:
        from acb_common._log import get_run_context

        ctx = get_run_context()
        self.seen.append((ctx.get("user"), ctx.get("member_verified")))
        return self.values[min(len(self.seen) - 1, len(self.values) - 1)]


def _suite(meter: _Meter) -> st.SuiteResult:
    return asyncio.run(st.run_suite(org=ORG, member=MEMBER, skill_id=SID,
                                    runner=st.agent_runner, meter=meter))


def _tool_names(body: dict[str, Any]) -> set[str]:
    return {t.get("function", {}).get("name") for t in body.get("tools") or []}


def test_a_held_write_is_recorded_and_no_write_reaches_the_gateway(world) -> None:
    meter = _Meter([Decimal(0)])
    result = _suite(meter)
    only = result.results[0]
    assert world.side == []
    assert (only.reason, only.passed) == ("passed", True), (only, world.gateway)
    assert only.writes == ("tool:create_task",)
    assert only.calls == ("tool:create_task", "tool:list_tasks")
    assert [m for m, _p in world.gateway if m != "GET"] == [], world.gateway
    assert any(p.endswith("/projects/tasks") for m, p in world.gateway if m == "GET")
    assert len(world.model.bodies) == 3 == only.model_calls
    assert {"create_task", "list_tasks"} <= _tool_names(world.model.bodies[0])
    # Three reads before the three requests, and one after the run. Each one
    # reads as the proven member.
    assert len(meter.seen) == 4 and set(meter.seen) == {(MEMBER, "1")}, meter.seen


def test_the_run_stops_at_its_credit_cap_on_the_real_loop(world) -> None:
    """The meter reads the cap after request 1. ``CapGuard`` refuses request
    2, and the stop holds through each fallback of the executor."""
    meter = _Meter([Decimal(0), Decimal(5)])
    result = _suite(meter)
    only = result.results[0]
    assert (only.reason, result.stopped) == ("cap_reached", "cap_reached")
    assert len(world.model.bodies) == 1 == only.model_calls
    assert [m for m, _p in world.gateway if m != "GET"] == []


def test_an_org_with_no_packs_starts_no_model_call(world, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "tool_packs_orgs", "", raising=False)
    result = _suite(_Meter([Decimal(0)]))
    assert result.results[0].reason == "skill_unloaded"
    assert world.model.bodies == [] and world.gateway == [] and world.side == []


def test_a_turn_that_errors_never_passes_on_the_real_loop(world, monkeypatch) -> None:
    """The model answers 503, so the stream ends with a ``RUN_ERROR`` line and
    raises nothing. A test that expects no write must read ``run_failed``.
    Mutation: ignore the ``RUN_ERROR`` line in ``agent_runner`` → ``passed`` → red."""
    world.model = _Down()
    nothing = {**BODY, "tests": [{"id": "t0", "input": "Say hello", "expect": []}]}

    async def _load(org: str, member: str, skill_id: str, version: int | None) -> Any:
        skill = store.Skill(id=SID, name="Add a task", slug="add-a-task", description="",
                            owner_email=MEMBER, visibility="private", state="draft",
                            current_version=1, created_at=_NOW, updated_at=_NOW)
        return skill, store.Version(skill_id=SID, version=1, body=nothing, content_hash="h",
                                    created_by=MEMBER, created_at=_NOW)

    monkeypatch.setattr(st, "_load_skill", _load)
    result = _suite(_Meter([Decimal(0)]))
    only = result.results[0]
    assert (only.reason, only.passed, result.passed) == ("run_failed", False, False), only
    assert only.detail.startswith("The run failed: run_error:")
    assert "Router is down" not in repr(result)
    assert world.model.bodies, "non-vacuity: the model was asked"
    assert [m for m, _p in world.gateway if m != "GET"] == []


class _Down:
    """A model transport that answers 503 to each request."""

    def __init__(self) -> None:
        self.bodies: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.bodies.append(json.loads(request.content or b"{}"))
        return httpx.Response(503, json={"error": {"message": "the Router is down"}})
