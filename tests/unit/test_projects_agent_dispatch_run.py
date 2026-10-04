"""Assignment is dispatch, end to end: the REAL sink, the REAL executor, a
REAL Postgres (WS-27f, ``project_management_app.md`` §6.4).

A member assigns a task to ``agent:<name>``. The gateway emits
``pm.task.assigned``, and ``agent_dispatch.on_event`` starts the run. From
2026-08-06 (commit 370209044) until this suite, the sink handed
``run_agent`` the task text as a STRING. ``run_agent`` takes a dict payload,
so every dispatched run failed with ``'str' object has no attribute 'keys'``
before the agent saw a word. No test saw it: each one replaced
``_run_and_record`` or ``run_agent`` with a fake that took any message.

This suite replaces neither. Only the model's wire is a fake: the agent is
the one ``agent-projects`` builds, and its HTTP transport answers from a
script. The task, the two timeline rows and the tenant are real rows on the
H3 rehearsal's phase-4 catalog, read and written as its NOSUPERUSER
NOBYPASSRLS role (R8).

The organization is an ordinary one, outside ``maf_coding_scope``. A COVERED
run that assigns a task to an agent is refused by H-236 (PR #613), and that
stays a separate case.

Mutations this suite catches (R7):

* the sink passes the task text as a string again: the closing row says
  ``Agent run failed: 'str' object has no attribute 'keys'``;
* the sink records the whole result dict and not the reply: the closing row
  is ``{'answer': ...}``;
* the sink awaits the run inline again: the assignment waits for the agent,
  and the closing row exists before ``on_event`` returns;
* the run is started without the event's tenant: the run is refused
  (``RunWorkspaceRefused``) and the closing row says so;
* the ``PROJECTS_AGENT_DISPATCH`` check removed: with the flag OFF the run
  starts anyway, and the OFF test sees a model call and a "started" row.

The run is dark by default. The run fixture turns the flag ON, and the OFF
test turns it off on the same real sink and the same real executor.

Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_projects_agent_dispatch_run.py -v -rs
"""
from __future__ import annotations

import asyncio
import json
import shutil
import uuid
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("agent_framework", reason="MAF not installed")
pytest.importorskip("skill_projects", reason="skill-projects not installed")

import httpx
from sqlalchemy import text

from tests.unit._projects_agent_fakes import load_agent_module
from tests.unit._sandbox_tools_fakes import short_tmp  # noqa: F401 — a fixture
from tests.unit._tenant_ladder import tenant_engine_scope

# Resolved by name as fixtures, so ruff sees them as unused (F401) and the
# test signatures as redefinitions (F811). The imports are load-bearing.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

REPO = Path(__file__).resolve().parents[2]
AGENT = "projects-assistant"
AGENT_DIR = REPO / "apps" / "agents" / "agent-projects"
MEMBER = "member@dispatch.test"
TITLE = "Write the launch notes"
REPLY = "I drafted the launch notes and left them on the task."

_PA = load_agent_module("projects_assistant_dispatch_suite")


# ── The model: a script behind the agent's real HTTP client ─────────────────


class _Model:
    """Answers a plain (not streamed) chat completion, as the batch path asks.

    Records each request body, so a test can see what the agent was told.
    """

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.bodies: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        self.bodies.append(body)
        assert not body.get("stream"), "the batch path asked for a stream"
        return httpx.Response(200, json={
            "id": f"chatcmpl-{len(self.bodies)}", "object": "chat.completion",
            "created": 0, "model": "probe",
            "choices": [{
                "index": 0, "finish_reason": "stop",
                "message": {"role": "assistant", "content": self.reply},
            }],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })


@pytest.fixture
def model(monkeypatch, short_tmp) -> _Model:  # noqa: F811
    """The real executor, with the agent built by its real factory.

    Replaced: the clone (a scratch dir with the agent's real config), the
    model's transport, and the git and registry reads that need a checkout.
    The host's own HTTP sends raise, so the run reaches nothing else.
    """
    import acb_memory
    import gateway.routes.agent as routes_agent
    from acb_common import get_settings
    from orchestrator import _tool_injection as ti
    from orchestrator import executor

    settings = get_settings()
    clone = short_tmp / "agents"
    monkeypatch.setattr(settings, "agents_clone_dir", str(clone))
    monkeypatch.setattr(settings, "custom_apps_root", str(short_tmp / "apps"))
    # No organization is covered. A dev .env must not make this a covered run.
    monkeypatch.setattr(settings, "maf_coding_scope", "")
    # The run ships dark. These tests are of the run, so they turn it ON.
    monkeypatch.setenv("PROJECTS_AGENT_DISPATCH", "1")
    agent_dir = clone / "repos" / AGENT
    agent_dir.mkdir(parents=True)
    shutil.copy(AGENT_DIR / "config.json", agent_dir / "config.json")
    config = json.loads((agent_dir / "config.json").read_text(encoding="utf-8"))
    scripted = _Model(REPLY)

    def build() -> list[Any]:
        agents = _PA.build_agents()
        oc = agents[0].client.client  # the AsyncOpenAI under the MAF client
        oc._client = httpx.AsyncClient(
            transport=httpx.MockTransport(scripted), event_hooks=oc._client.event_hooks,
        )
        return agents

    @contextmanager
    def load(name: str, **_k: Any):
        assert name == AGENT, name
        yield SimpleNamespace(
            agent_dir=agent_dir, agent_name=name, config=config, build_agents=build,
        )

    async def nothing(*_a: Any, **_k: Any) -> str:
        return ""

    async def no_send(_self: Any, request: Any) -> Any:
        raise AssertionError(f"the host sent {request.method} {request.url}")

    monkeypatch.setattr(executor, "load_agent", load)
    monkeypatch.setattr(executor, "build_integrations", lambda *a, **k: ({}, {}))
    monkeypatch.setattr(executor, "_install_push_guard", nothing)
    monkeypatch.setattr(executor, "_get_current_head", nothing)
    monkeypatch.setattr(executor, "_detect_agent_commits", nothing)
    monkeypatch.setattr(acb_memory, "rehydrate_workspace", nothing)
    monkeypatch.setattr(routes_agent, "_load_dynamic_agents", lambda: [])
    monkeypatch.setattr(ti, "_load_disabled_skill_families", lambda name: frozenset())
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", no_send)
    ti._build_registry_block.cache_clear()
    ti._build_injected_tools_addendum.cache_clear()
    try:
        yield scripted
    finally:
        ti._build_registry_block.cache_clear()
        ti._build_injected_tools_addendum.cache_clear()
        from acb_skills.write_artifact import bind_artifact_context

        bind_artifact_context()


# ── The board: one task in an ordinary organization ─────────────────────────


@pytest.fixture
def board(promoted):  # noqa: F811
    """One project, one lane and one task in org B. Seeded as the superuser,
    so the sink's own reads and writes are the first the app role makes."""
    org = promoted.org_b
    project, status, task = (str(uuid.uuid4()) for _ in range(3))
    with promoted.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO pm_projects (id, organization_id, name, source, "
            "created_by, owns_statuses) VALUES (CAST(:p AS uuid), "
            "CAST(:o AS uuid), 'Launch', 'manual', :me, true)"),
            {"p": project, "o": org, "me": MEMBER})
        c.execute(text(
            "INSERT INTO pm_task_statuses (id, project_id, name, position, "
            "category, is_default) VALUES (CAST(:s AS uuid), CAST(:p AS uuid), "
            "'To do', 1, 'todo', true)"),
            {"s": status, "p": project})
        c.execute(text(
            "INSERT INTO pm_tasks (id, organization_id, project_id, "
            "root_project_id, status_id, title, source, created_by, "
            "task_number) VALUES (CAST(:t AS uuid), CAST(:o AS uuid), "
            "CAST(:p AS uuid), CAST(:p AS uuid), CAST(:s AS uuid), :ti, "
            "'manual', :me, 7)"),
            {"t": task, "o": org, "p": project, "s": status, "ti": TITLE, "me": MEMBER})
    return SimpleNamespace(
        promoted=promoted, org=org, task=task,
        url=promoted.app_url.render_as_string(hide_password=False),
    )


def _timeline(board: SimpleNamespace) -> list[Any]:
    """The task's ``agent_run`` rows, oldest first, read as the superuser."""
    with board.promoted.admin_engine.connect() as c:
        return list(c.execute(text(
            "SELECT body, meta, created_by, organization_id FROM pm_activities "
            "WHERE task_id = CAST(:t AS uuid) AND type = 'agent_run' "
            "ORDER BY created_at, id"), {"t": board.task}))


async def _settle() -> None:
    """Wait for every task the sink left running: the run and its audit rows.

    Generic on purpose, so this suite reads the same on a sink that awaits
    the run inline and on one that starts it in the background.
    """
    me = asyncio.current_task()
    async with asyncio.timeout(120):
        while rest := [t for t in asyncio.all_tasks() if t is not me and not t.done()]:
            await asyncio.gather(*rest, return_exceptions=True)


def _assign(board: SimpleNamespace, *, before_settle: list[Any] | None = None) -> None:
    """The event ``PUT /tasks/{id}/assignees`` emits, through the real sink."""
    from gateway.routes.projects import agent_dispatch

    async def go() -> None:
        async with tenant_engine_scope(board.url):
            await agent_dispatch.on_event("projects", "pm.task.assigned", {
                "task_id": board.task, "assignees": [f"agent:{AGENT}"],
                "organization_id": board.org,
            })
            if before_settle is not None:
                before_settle.extend(_timeline(board))
            await _settle()

    asyncio.run(go())


# ── The fence ───────────────────────────────────────────────────────────────


def test_assigning_a_task_to_an_agent_runs_it_and_records_its_reply(
    board, model, app_engine,  # noqa: F811
) -> None:
    """The run starts, the agent reads the task, and its reply closes the
    handoff on the task's timeline, in the task's own tenant."""
    _assign(board)
    rows = _timeline(board)
    states = [r.meta.get("state") for r in rows]
    assert states == ["started", "finished"], [(s, r.body) for s, r in zip(states, rows, strict=True)]
    assert rows[-1].body == REPLY
    assert {r.created_by for r in rows} == {f"agent:{AGENT}"}
    assert {str(r.organization_id) for r in rows} == {board.org}
    # The agent was told the task, not an empty or serialized payload.
    assert len(model.bodies) == 1, model.bodies
    told = json.dumps(model.bodies[0]["messages"])
    assert TITLE in told and board.task in told, told


def test_the_assignment_does_not_wait_for_the_agent(
    board, model, app_engine,  # noqa: F811
) -> None:
    """``PUT /tasks/{id}/assignees`` awaits the sink. A sink that awaited the
    run held the member's request open for the whole run, up to
    ``AGENT_RUN_TIMEOUT_SECONDS``. At the moment the sink returns, only the
    handoff row exists. The closing row comes when the run ends."""
    seen: list[Any] = []
    _assign(board, before_settle=seen)
    assert [r.meta.get("state") for r in seen] == ["started"], [r.body for r in seen]
    assert [r.meta.get("state") for r in _timeline(board)] == ["started", "finished"]


def test_with_the_flag_off_no_run_starts_and_the_member_is_told(
    board, model, app_engine, monkeypatch,  # noqa: F811
) -> None:
    """``PROJECTS_AGENT_DISPATCH`` OFF, through the same real sink and the
    same real executor: the model is never called, nothing is spent, and the
    task carries one row in its own tenant that says why."""
    from gateway.routes.projects import agent_dispatch
    from orchestrator import executor

    monkeypatch.delenv("PROJECTS_AGENT_DISPATCH", raising=False)
    started: list[str] = []
    real = executor.run_agent

    async def counted(agent: str, *args: Any, **kwargs: Any) -> Any:
        started.append(agent)
        return await real(agent, *args, **kwargs)

    monkeypatch.setattr(executor, "run_agent", counted)
    _assign(board)
    assert started == [], "run_agent was called with the flag OFF"
    assert model.bodies == [], "the model was called with the flag OFF"
    rows = _timeline(board)
    assert [(r.body, r.meta.get("state")) for r in rows] == [
        (agent_dispatch.DISABLED_BODY, "disabled"),
    ]
    assert {str(r.organization_id) for r in rows} == {board.org}
