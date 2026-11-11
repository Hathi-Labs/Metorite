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
  (``RunWorkspaceRefused``) and the closing row is a failure;
* the ``PROJECTS_AGENT_DISPATCH`` check removed: with the flag OFF the run
  starts anyway, and the OFF test sees a model call and a "started" row;
* H-240: the sink passes no ``session_user``: the run's member is not
  verified, and ``test_the_run_acts_as_the_assigner_*`` fails;
* H-240: the sink leaves the member out of ``run_payload``: the tools have
  no acting user, the comment never reaches the route, and the same test
  fails.

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

# Module level: this file uses postponed annotations, and FastAPI resolves a
# dependency's annotation from the module's globals.
from fastapi import Request
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
    ``steps`` (H-240) holds tool calls to answer with first, one per request.
    Each request also records who the run acts for at that moment: the
    run context's member, whether the session verified it, and the acting
    user of the gateway-calling tools. The handler runs inside the run's own
    task, so these are the run's bindings.
    """

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.bodies: list[dict[str, Any]] = []
        self.steps: list[dict[str, Any]] = []
        self.who: list[tuple[str, str, str]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        from acb_common import get_run_context
        from acb_skills.memory_tools import _get_memory_user_id

        body = json.loads(request.content or b"{}")
        self.bodies.append(body)
        ctx = get_run_context()
        self.who.append((
            str(ctx.get("user") or ""), str(ctx.get("member_verified") or ""),
            _get_memory_user_id() or "",
        ))
        assert not body.get("stream"), "the batch path asked for a stream"
        if self.steps:
            call = self.steps.pop(0)
            message = {"role": "assistant", "content": None, "tool_calls": [{
                "id": f"call_{len(self.bodies)}", "type": "function",
                "function": {"name": call["name"],
                             "arguments": json.dumps(call["arguments"])},
            }]}
            finish = "tool_calls"
        else:
            message = {"role": "assistant", "content": self.reply}
            finish = "stop"
        return httpx.Response(200, json={
            "id": f"chatcmpl-{len(self.bodies)}", "object": "chat.completion",
            "created": 0, "model": "probe",
            "choices": [{"index": 0, "finish_reason": finish, "message": message}],
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
        promoted=promoted, org=org, task=task, project=project,
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


def _assign(
    board: SimpleNamespace, *, before_settle: list[Any] | None = None,
    member: str | None = None,
) -> None:
    """The event ``PUT /tasks/{id}/assignees`` emits, through the real sink.

    *member* (H-240) is the assigner the gateway's emitter writes from the
    session. ``None`` sends the event with no member key at all.
    """
    from gateway.routes.projects import agent_dispatch

    event: dict[str, Any] = {
        "task_id": board.task, "assignees": [f"agent:{AGENT}"],
        "organization_id": board.org,
    }
    if member is not None:
        event[agent_dispatch.MEMBER_KEY] = member

    async def go() -> None:
        async with tenant_engine_scope(board.url):
            await agent_dispatch.on_event("projects", "pm.task.assigned", event)
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


# ── H-240: the run acts for the member who assigned the task ────────────────
#
# The model asks for the `comment` tool. The tool's gateway client talks to
# the REAL Projects router in-process, and the router reads and writes the
# phase-4 catalog as the NOSUPERUSER NOBYPASSRLS role. Only the auth seam is
# a stand-in: it maps the `X-User-Email` the run sent to that person's
# access, as `deps.get_current_user` branch 1a does. The acting user is NOT
# stubbed. It is what the executor bound from the sink's member.

EDITOR = "editor@dispatch.test"
OUTSIDER = "outsider@dispatch.test"
COMMENT = "The launch notes are drafted."


@pytest.fixture
def people(board):
    """Two members of org B. The editor holds a grant on the task's project.
    The outsider holds none, so the task is not theirs to open or change."""
    with board.promoted.admin_engine.begin() as c:
        for email in (EDITOR, OUTSIDER):
            c.execute(text(
                "INSERT INTO app_user (email, display_name, role, status, "
                "organization_id) SELECT :e, :e, 'employee', 'active', "
                "CAST(:o AS uuid) WHERE NOT EXISTS "
                "(SELECT 1 FROM app_user WHERE lower(email) = :e)"),
                {"e": email, "o": board.org})
        c.execute(text(
            "INSERT INTO pm_project_grants (id, project_id, organization_id, "
            "subject, created_by) VALUES (CAST(:g AS uuid), CAST(:p AS uuid), "
            "CAST(:o AS uuid), :e, :me)"),
            {"g": str(uuid.uuid4()), "p": board.project, "o": board.org,
             "e": EDITOR, "me": MEMBER})
    return board


@pytest.fixture
def gateway(monkeypatch):
    """The skill's client reaches the real Projects router in-process.

    Returns the list of `X-User-Email` values each call carried. The member
    approves each card: the card is the member's consent, and in a real
    dispatched run no card can reach the member yet (DEFERRED in H-240).
    """
    import types

    import skill_projects.client as client
    from acb_auth import get_current_user
    from fastapi import FastAPI
    from gateway.routes.projects import router

    from tests.unit._projects_agent_fakes import approve
    from tests.unit._projects_fakes import member_user

    sent: list[str] = []

    async def who(request: Request) -> Any:
        email = request.headers.get("x-user-email", "")
        sent.append(email)
        return member_user(email)

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = who
    real = httpx.AsyncClient
    monkeypatch.setattr(client, "httpx", types.SimpleNamespace(
        AsyncClient=lambda **kw: real(transport=httpx.ASGITransport(app=app), **kw),
    ))
    monkeypatch.setattr(client, "gateway_url", lambda: "http://gateway.test")
    approve(monkeypatch)
    return sent


def _comments(board: SimpleNamespace) -> list[tuple[str, str]]:
    with board.promoted.admin_engine.connect() as c:
        return [(r.created_by, r.body) for r in c.execute(text(
            "SELECT created_by, body FROM pm_activities WHERE task_id = "
            "CAST(:t AS uuid) AND type = 'comment' ORDER BY created_at"),
            {"t": board.task})]


def _comment_step(board: SimpleNamespace) -> dict[str, Any]:
    return {"name": "comment",
            "arguments": {"task_id": board.task, "body": COMMENT}}


def test_the_run_acts_as_the_assigner_and_writes_what_they_may(
    people, model, gateway, app_engine,  # noqa: F811
) -> None:
    """H-240. The editor assigns the task. The run binds the editor as its
    VERIFIED member and as the acting user of the tools. The agent's comment
    reaches the real route as the editor, and lands in the task's tenant
    under the editor's name."""
    model.steps.append(_comment_step(people))
    _assign(people, member=EDITOR)
    assert model.who and set(model.who) == {(EDITOR, "1", EDITOR)}, model.who
    assert gateway and set(gateway) == {EDITOR}, gateway
    assert _comments(people) == [(EDITOR, COMMENT)]
    assert [r.meta.get("state") for r in _timeline(people)] == ["started", "finished"]


def test_the_run_of_a_member_who_may_not_open_the_task_changes_nothing(
    people, model, gateway, app_engine,  # noqa: F811
) -> None:
    """H-240. The outsider is the assigner. The run acts as the outsider, so
    the route answers as it answers the outsider: the task is not found, and
    no comment is written. The agent reads the refusal as the tool's
    result."""
    model.steps.append(_comment_step(people))
    _assign(people, member=OUTSIDER)
    assert set(model.who) == {(OUTSIDER, "1", OUTSIDER)}, model.who
    assert gateway and set(gateway) == {OUTSIDER}, gateway
    assert _comments(people) == []
    told = json.dumps(model.bodies[-1]["messages"])
    assert "not visible to you" in told, told


def test_a_run_with_no_member_has_no_acting_user_and_writes_nothing(
    people, model, gateway, app_engine,  # noqa: F811
) -> None:
    """An event with no member (an outside agent, a service caller, a
    suspended member) runs as before H-240: no member, and each gateway call
    refuses before it leaves the run."""
    model.steps.append(_comment_step(people))
    _assign(people)
    assert set(model.who) == {("", "", "")}, model.who
    assert gateway == []
    assert _comments(people) == []
    told = json.dumps(model.bodies[-1]["messages"])
    assert "No acting user" in told, told
