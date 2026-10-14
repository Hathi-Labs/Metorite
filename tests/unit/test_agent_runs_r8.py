"""WS-55 EA-12a — agent runs on a task: the tables and the six routes.

Spec: ``project-docs/specs/external_agent_access.md`` §12.1 (D-EA-10, EA12-1
to EA12-13) and row EA-12a of §15, acceptance R1 to R12.

Two halves.

**Hermetic** (no database). The status machine is a pure table
(``agent_runs.transition``), so every pair of a status and an event is
checked here against the table of §12.1, and every pair the table does not
list must be a 409. The six routes are mounted, and the migration is found by
CONTENT (R1).

**R8** (a real Postgres, through the real ``/projects`` router, as the
non-privileged role of the phase-4 rehearsal, on a database with the whole
ladder and two organizations). A hermetic fake agrees with any SQL it is
handed, so the acceptance items run here:

* R1 the status machine, through the routes; R2 who writes which type;
  R3 the ``selected`` checks; R4 ``answered`` and ``stale``; R5 the
  idempotency key; R6 the tenant and the task's visibility; R7 the cap of 20
  ephemeral rows; R8 the comment and the notification of a question; R9 the
  long poll; R10 the flag; R11 no session across the sleep; R12 the run's
  member gets the ``agent_question`` row.

The R8 class carries ``_DB_GATE``, so it skips where no ladder URL is set (the
deploy run, which has no ladder database) and its fixtures never start there.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("gateway.routes.projects", reason="gateway not installed")

from fastapi import HTTPException, Request
from gateway.routes.projects import agent_runs
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

_REPO = Path(__file__).resolve().parents[2]
_MIGRATIONS = _REPO / "infra" / "postgres"

ORIGIN = "https://claude.ai"
VIA = "mcp:claude.ai"

# ── Hermetic: the status machine of §12.1 ───────────────────────────────────

#: The table of §12.1 with EA12-5, written out once more BY HAND. A change to
#: ``agent_runs._MOVES`` that this table does not make fails here.
EXPECTED: dict[str, dict[str, str]] = {
    "agent_activity": {
        "created": "in_progress", "in_progress": "in_progress", "stale": "in_progress",
        "awaiting": "awaiting", "stopping": "stopped",
    },
    "agent_elicitation": {
        "created": "awaiting", "in_progress": "awaiting", "stale": "awaiting",
        "stopping": "stopped",
    },
    "agent_error": {
        "created": "failed", "in_progress": "failed", "stale": "failed",
        "awaiting": "failed", "stopping": "stopped",
    },
    "agent_finish": {
        "in_progress": "completed", "stale": "completed", "awaiting": "completed",
        "stopping": "stopped",
    },
    "member_prompt": {
        "awaiting": "in_progress", "in_progress": "in_progress", "stale": "in_progress",
        "completed": "in_progress",
    },
    "member_stop": {
        "created": "stopping", "in_progress": "stopping", "stale": "stopping",
        "awaiting": "stopping",
    },
    "member_start": {"created": "created"},
}


@pytest.mark.parametrize("event", sorted(EXPECTED))
@pytest.mark.parametrize("status", agent_runs.RUN_STATUSES)
def test_r1_the_status_machine_is_the_table_and_nothing_else(status: str, event: str) -> None:
    """R1, hermetic. Each listed move gives its status. Each other move is 409."""
    want = EXPECTED[event].get(status)
    if want is None:
        with pytest.raises(HTTPException) as refused:
            agent_runs.transition(status, event)
        assert refused.value.status_code == 409
    else:
        assert agent_runs.transition(status, event) == want


def test_the_routes_are_mounted() -> None:
    from gateway.routes.projects import router

    mounted = {(m, r.path) for r in router.routes for m in getattr(r, "methods", set())}
    for pair in (
        ("GET", "/projects/tasks/{task_id}/agent-runs"),
        ("POST", "/projects/tasks/{task_id}/agent-runs"),
        ("GET", "/projects/agent-runs"),
        ("GET", "/projects/agent-runs/{run_id}"),
        ("POST", "/projects/agent-runs/{run_id}/activities"),
        ("POST", "/projects/agent-runs/{run_id}/finish"),
    ):
        assert pair in mounted, pair


def _migration() -> Path:
    """The migration that creates ``pm_agent_runs``, found by content (R1)."""
    found = [
        p for p in sorted(_MIGRATIONS.glob("*.sql"))
        if "CREATE TABLE IF NOT EXISTS pm_agent_runs" in p.read_text(encoding="utf-8")
    ]
    assert len(found) == 1, [p.name for p in found]
    return found[0]


def test_the_migration_widens_the_notification_kinds() -> None:
    sql = _migration().read_text(encoding="utf-8")
    assert "'agent_question'" in sql and "pm_notifications_kind_check" in sql
    assert "seq               BIGINT GENERATED ALWAYS AS IDENTITY" in sql


def test_the_migration_indexes_the_three_activity_lookups() -> None:
    """Fix round 1, item 4. Mutation: delete one index — this fails."""
    sql = _migration().read_text(encoding="utf-8")
    for index, column in (
        ("pm_agent_run_activities_in_reply_to", "(in_reply_to)"),
        ("pm_agent_run_activities_pm_activity", "(pm_activity_id)"),
        ("pm_agent_run_activities_task", "(task_id)"),
    ):
        head = f"CREATE INDEX IF NOT EXISTS {index}\n    ON pm_agent_run_activities {column}"
        assert head in sql, index


def test_the_bell_reads_an_agent_question_as_a_question() -> None:
    """§12.1, "For EA-12c, the bell": every notification kind has a verb, and
    ``agent_question`` stays in the asking rows."""
    from gateway.routes.projects.notifications import NOTIFICATION_KINDS
    from gateway.routes.shell import needs

    assert set(NOTIFICATION_KINDS) <= set(needs._VERB)
    assert "agent_question" in needs.ASKING_NOTIFICATIONS
    row = {"kind": "agent_question", "actor": VIA, "task_title": "Fix it", "task_number": 4}
    assert needs._sentence(row) == "Claude.ai asked you about #4 Fix it"


# ── R8: the helpers ─────────────────────────────────────────────────────────


def _principal(
    email: str, org: str, *, unrestricted: bool = True, agent_connection: str | None = None,
    scopes: tuple[str, ...] = ("projects:read", "projects:write"),
) -> Any:
    from acb_auth import AgentScope, UserContext, UserRole, build_access

    perms = ["feature:projects", "data:org:read"] if unrestricted else ["feature:projects"]
    agent = None
    if agent_connection is not None:
        agent = AgentScope(
            connection_id=agent_connection, scopes=scopes, include_personal=False,
            client_origin=ORIGIN, actor_via=VIA,
        )
    return UserContext(
        email=email, role=UserRole.EMPLOYEE, access=build_access(perms),
        organization_id=org, agent=agent,
    )


def _app(users: dict[str, Any]) -> Any:
    """The real ``/projects`` router. The caller is the principal that the
    test header names, and the override binds the tenant from it, as
    ``_with_resolved_access`` does. The tenant never comes from the request."""
    from acb_auth import get_current_user
    from acb_common.db import bind_tenant
    from fastapi import FastAPI
    from gateway.routes import projects as projects_package

    async def _caller(request: Request) -> Any:
        user = users[request.headers["x-test-as"]]
        bind_tenant(user.organization_id)
        return user

    app = FastAPI()
    app.include_router(projects_package.router)
    app.dependency_overrides[get_current_user] = _caller
    return app


class _Client:
    """One engine scope and one ASGI client for a whole test, so two
    requests may run at once (R9)."""

    def __init__(self, client: httpx.AsyncClient, users: dict[str, Any]) -> None:
        self.client = client
        self.users = users

    async def __call__(
        self, user: Any, method: str, path: str, body: dict | None = None,
    ) -> httpx.Response:
        key = str(id(user))
        self.users[key] = user
        headers = {"x-test-as": key}
        if getattr(user, "agent", None) is not None:
            headers["X-Actor-Via"] = VIA
        return await self.client.request(method, path, json=body, headers=headers)


@contextlib.asynccontextmanager
async def _session(p: Any):
    from acb_common.db import clear_tenant, release_tenant

    users: dict[str, Any] = {}
    scope = clear_tenant()
    try:
        async with (
            tenant_engine_scope(p.app_url.render_as_string(hide_password=False)),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=_app(users)), base_url="http://projects.test",
            ) as client,
        ):
            yield _Client(client, users)
    finally:
        release_tenant(scope)


async def _call(p: Any, user: Any, method: str, path: str, body: dict | None = None) -> httpx.Response:
    async with _session(p) as call:
        return await call(user, method, path, body)


def _seed_task(p: Any, org: str, email: str, *, grant: str | None = None,
               assignee: str | None = None) -> str:
    """A member, a project, a lane and a task in *org*, as the RLS-bypassing
    admin. Every INSERT stamps ``organization_id`` itself."""
    project, status, task = (str(uuid.uuid4()) for _ in range(3))
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO app_user (email, display_name, role, status, organization_id) "
            "VALUES (:e, :e, 'employee', 'active', CAST(:o AS uuid)) "
            "ON CONFLICT DO NOTHING"), {"e": email, "o": org})
        c.execute(text(
            "INSERT INTO pm_projects (id, organization_id, name, created_by, owns_statuses) "
            "VALUES (:id, :o, 'runs', :e, true)"), {"id": project, "o": org, "e": email})
        if grant is not None:
            c.execute(text(
                "INSERT INTO pm_project_grants (organization_id, project_id, subject, "
                "created_by) VALUES (:o, :p, :s, :e)"),
                {"o": org, "p": project, "s": grant, "e": email})
        c.execute(text(
            "INSERT INTO pm_task_statuses (id, organization_id, project_id, name, category, "
            "position) VALUES (:id, :o, :p, 'To do', 'todo', 10)"),
            {"id": status, "o": org, "p": project})
        c.execute(text(
            "INSERT INTO pm_tasks (id, organization_id, project_id, root_project_id, "
            "task_number, status_id, title, created_by) "
            "VALUES (:id, :o, :p, :p, 1, :s, 'Fix the extruder', :e)"),
            {"id": task, "o": org, "p": project, "s": status, "e": email})
        if assignee is not None:
            c.execute(text(
                "INSERT INTO app_user (email, display_name, role, status, organization_id) "
                "VALUES (:e, :e, 'employee', 'active', CAST(:o AS uuid)) "
                "ON CONFLICT DO NOTHING"), {"e": assignee, "o": org})
            c.execute(text(
                "INSERT INTO pm_task_assignees (organization_id, task_id, assignee, "
                "assigned_by) VALUES (:o, :t, :a, :e)"),
                {"o": org, "t": task, "a": assignee, "e": email})
    return task


def _seed_connection(p: Any, org: str, email: str) -> str:
    with p.admin_engine.begin() as c:
        return str(c.execute(text(
            "INSERT INTO agent_connection (organization_id, member_email, client_id,"
            " client_name, client_origin, scopes, pinned_projects, include_personal,"
            " expires_at) VALUES (CAST(:o AS uuid), :m, 'https://claude.ai/client.json',"
            " 'Claude', :origin, ARRAY['projects:read', 'projects:write'],"
            " CAST('{}' AS uuid[]), false, now() + interval '1 day') RETURNING id"),
            {"o": org, "m": email, "origin": ORIGIN}).scalar())


def _count(p: Any, sql: str, **params: Any) -> int:
    with p.admin_engine.connect() as c:
        return int(c.execute(text(sql), params).scalar())


def _rows(p: Any, sql: str, **params: Any) -> list[Any]:
    with p.admin_engine.connect() as c:
        return list(c.execute(text(sql), params))


def _key() -> str:
    return uuid.uuid4().hex


@pytest.fixture(scope="module")
def world(promoted):  # noqa: F811 — a fixture
    """Members, connections and tasks in the two organizations."""
    a_email, b_email = "ea12-a@runs-r8.test", "ea12-b@runs-r8.test"
    m_email, c_email = "ea12-m@runs-r8.test", "ea12-c@runs-r8.test"
    conn_a = None
    ns = SimpleNamespace(p=promoted)
    # A grant to the member, so the bell's rule 3 (`deliverable`) finds that
    # the run's member can open the task, as it can in production.
    ns.task_a = _seed_task(promoted, promoted.org_a, a_email, grant=a_email,
                           assignee=c_email)
    ns.task_b = _seed_task(promoted, promoted.org_b, b_email)
    ns.task_private = _seed_task(promoted, promoted.org_a, a_email, grant=a_email)
    conn_a = _seed_connection(promoted, promoted.org_a, a_email)
    ns.conn_b = _seed_connection(promoted, promoted.org_b, b_email)
    ns.conn_a = conn_a
    ns.a = _principal(a_email, promoted.org_a)
    ns.agent_a = _principal(a_email, promoted.org_a, agent_connection=conn_a)
    ns.c = _principal(c_email, promoted.org_a)
    ns.m = _principal(m_email, promoted.org_a, unrestricted=False)
    ns.b = _principal(b_email, promoted.org_b)
    ns.agent_b = _principal(b_email, promoted.org_b, agent_connection=ns.conn_b)
    ns.a_email, ns.c_email = a_email, c_email
    with promoted.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO app_user (email, display_name, role, status, organization_id) "
            "VALUES (:e, :e, 'employee', 'active', CAST(:o AS uuid)) "
            "ON CONFLICT DO NOTHING"), {"e": m_email, "o": promoted.org_a})
    return ns


@pytest.fixture
def w(world, monkeypatch):
    """The world with the flag on, and with no run, no agent comment and no
    notification left from the last test."""
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "agent_runs_enabled", True)
    with world.p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM pm_agent_runs"))
        c.execute(text("DELETE FROM pm_notifications"))
        c.execute(text("DELETE FROM pm_activities WHERE type = 'comment'"))
    return world


async def _start(w: Any, call: Any, user: Any = None, task: str | None = None, **body: Any) -> dict:
    answer = await call(user or w.agent_a, "POST",
                        f"/projects/tasks/{task or w.task_a}/agent-runs", body or {})
    assert answer.status_code in (200, 201), answer.text
    return answer.json()["run"]


async def _act(call: Any, user: Any, run: str, **body: Any) -> httpx.Response:
    body.setdefault("idempotency_key", _key())
    return await call(user, "POST", f"/projects/agent-runs/{run}/activities", body)


async def _status(call: Any, user: Any, run: str) -> tuple[str, str]:
    got = await call(user, "GET", f"/projects/agent-runs/{run}")
    assert got.status_code == 200, got.text
    return got.json()["run"]["status"], got.json()["run"]["state"]


def _select(question: str = "Which database?") -> dict:
    return {
        "type": "elicitation", "body": question,
        "options": [
            {"id": "pg", "label": "Postgres", "recommended": True},
            {"id": "my", "label": "MySQL", "description": "Older"},
            {"id": "sq", "label": "SQLite"},
        ],
    }


@_DB_GATE
class TestAgentRunsOnARealDatabase:
    async def test_the_route_runs_as_a_role_that_cannot_bypass_rls(self, w, app_engine):  # noqa: F811
        with app_engine.connect() as c:
            role = c.execute(text(
                "SELECT rolsuper, rolbypassrls FROM pg_roles "
                "WHERE rolname = current_user")).first()
            forced = c.execute(text(
                "SELECT count(*) FROM pg_class WHERE relname IN "
                "('pm_agent_runs', 'pm_agent_run_activities') "
                "AND relrowsecurity AND relforcerowsecurity")).scalar()
        assert role is not None and not role[0] and not role[1]
        assert forced == 2

    # ── R1 ──────────────────────────────────────────────────────────────

    async def test_r1_every_row_of_the_table_holds_through_the_routes(self, w):
        async with _session(w.p) as call:
            # The agent starts: in_progress, working.
            run = await _start(w, call)
            assert (run["status"], run["state"]) == ("in_progress", "working")
            rid = run["id"]
            # A thought keeps in_progress. A question moves it to awaiting.
            assert (await _act(call, w.agent_a, rid, type="thought", body="Reading")).status_code == 201
            assert await _status(call, w.a, rid) == ("in_progress", "working")
            asked = await _act(call, w.agent_a, rid, **_select())
            assert asked.status_code == 201 and asked.json()["run"]["status"] == "awaiting"
            # A thought on awaiting keeps awaiting, and is not a 409.
            assert (await _act(call, w.agent_a, rid, type="action", action="read_file",
                               parameters={"path": "a.py"})).json()["run"]["status"] == "awaiting"
            # Any member who can see the task answers: in_progress.
            answered = await _act(call, w.c, rid, type="prompt", selected=["pg"])
            assert answered.status_code == 201, answered.text
            assert answered.json()["run"]["status"] == "in_progress"
            # A member reply on in_progress keeps in_progress.
            assert (await _act(call, w.a, rid, type="prompt", body="And add an index"
                               )).json()["run"]["status"] == "in_progress"
            # Finish: completed. A member prompt opens it again.
            done = await call(w.agent_a, "POST", f"/projects/agent-runs/{rid}/finish",
                              {"summary": "Done", "idempotency_key": _key()})
            assert done.status_code == 200 and done.json()["run"]["status"] == "completed"
            assert done.json()["run"]["ended_at"] is not None
            reopened = await _act(call, w.a, rid, type="prompt", body="One more thing")
            assert reopened.json()["run"]["status"] == "in_progress"
            assert reopened.json()["run"]["ended_at"] is None
            # Finish from awaiting.
            await _act(call, w.agent_a, rid, type="elicitation", body="Free question?")
            assert await _status(call, w.a, rid) == ("awaiting", "awaiting")
            done = await call(w.agent_a, "POST", f"/projects/agent-runs/{rid}/finish", {})
            assert done.json()["run"]["status"] == "completed"

            # An error moves an open run to failed.
            run2 = await _start(w, call)
            failed = await _act(call, w.agent_a, run2["id"], type="error", body="Tests broke")
            assert failed.json()["run"]["status"] == "failed"
            assert failed.json()["run"]["error_metadata"]["body"] == "Tests broke"

            # A member starts a run for the agent: created, queued. The
            # agent's first activity moves it to in_progress.
            run3 = await _start(w, call, user=w.a, connection_id=w.conn_a, body="Fix it")
            assert (run3["status"], run3["state"]) == ("created", "queued")
            assert run3["client_via"] == VIA and run3["started_by"] == w.a_email
            moved = await _act(call, w.agent_a, run3["id"], type="thought", body="On it")
            assert moved.json()["run"]["status"] == "in_progress"
            assert moved.json()["run"]["state"] == "working"

            # Stop: stopping. The next agent activity: stopped.
            stop = await _act(call, w.a, run3["id"], type="prompt", signal="stop")
            assert stop.json()["run"]["status"] == "stopping"
            assert stop.json()["run"]["stopped_by"] == w.a_email
            late = await _act(call, w.agent_a, run3["id"], type="thought", body="Still here")
            assert late.json()["run"]["status"] == "stopped"

            # A Stop wins over an error and over a finish.
            for final in ("error", "finish"):
                run4 = await _start(w, call)
                await _act(call, w.a, run4["id"], type="prompt", signal="stop")
                if final == "error":
                    got = await _act(call, w.agent_a, run4["id"], type="error", body="x")
                else:
                    got = await call(w.agent_a, "POST", f"/projects/agent-runs/{run4['id']}/finish", {})
                assert got.json()["run"]["status"] == "stopped", final

            # A question on a created run: awaiting. Stop on created: stopping.
            run5 = await _start(w, call, user=w.a, connection_id=w.conn_a, body="Plan it")
            assert (await _act(call, w.agent_a, run5["id"], type="elicitation", body="Which?"
                               )).json()["run"]["status"] == "awaiting"
            await _act(call, w.agent_a, run5["id"], type="error", body="gone")
            run6 = await _start(w, call, user=w.a, connection_id=w.conn_a, body="Later")
            assert (await _act(call, w.a, run6["id"], type="prompt", signal="stop"
                               )).json()["run"]["status"] == "stopping"

    async def test_r1_a_move_the_table_does_not_list_is_409_and_writes_nothing(self, w):
        async with _session(w.p) as call:
            run = await _start(w, call)
            rid = run["id"]
            await _act(call, w.agent_a, rid, type="elicitation", body="First?")
            before = _count(w.p, "SELECT count(*) FROM pm_agent_run_activities")
            # A second question while one waits.
            again = await _act(call, w.agent_a, rid, type="elicitation", body="Second?")
            assert again.status_code == 409
            # A Stop on a stopped run, a prompt on stopping, failed and stopped.
            await _act(call, w.a, rid, type="prompt", signal="stop")
            assert (await _act(call, w.a, rid, type="prompt", body="hi")).status_code == 409
            await _act(call, w.agent_a, rid, type="thought", body="ack")  # stopped
            for body in ({"type": "prompt", "body": "hi"}, {"type": "prompt", "signal": "stop"}):
                assert (await _act(call, w.a, rid, **body)).status_code == 409
            # Any agent write on a terminal run.
            for body in ({"type": "thought", "body": "x"}, {"type": "error", "body": "x"},
                         {"type": "response", "body": "x"}):
                assert (await _act(call, w.agent_a, rid, **body)).status_code == 409, body
            # A finish with no summary on a finished run writes nothing and
            # returns the run (fix round 1, item 6). It is not a 409.
            replay = await call(w.agent_a, "POST", f"/projects/agent-runs/{rid}/finish", {})
            assert replay.status_code == 200 and replay.json()["run"]["status"] == "stopped"
            # A prompt on created, a finish on created, a stop on completed.
            run2 = await _start(w, call, user=w.a, connection_id=w.conn_a, body="Go")
            assert (await _act(call, w.a, run2["id"], type="prompt", body="also")).status_code == 409
            assert (await call(w.agent_a, "POST", f"/projects/agent-runs/{run2['id']}/finish", {})
                    ).status_code == 409
            await _act(call, w.agent_a, run2["id"], type="thought", body="x")
            await call(w.agent_a, "POST", f"/projects/agent-runs/{run2['id']}/finish", {})
            assert (await _act(call, w.a, run2["id"], type="prompt", signal="stop")).status_code == 409
            # A prompt on failed.
            run3 = await _start(w, call, user=w.a, connection_id=w.conn_a, body="Again")
            await _act(call, w.agent_a, run3["id"], type="error", body="no")
            assert (await _act(call, w.a, run3["id"], type="prompt", body="retry")).status_code == 409
        written = _count(w.p, "SELECT count(*) FROM pm_agent_run_activities")
        # The refused writes added nothing: the count grew only by the
        # accepted writes above (stop, ack, run2 prompt + thought, run3 prompt + error).
        assert written == before + 6

    # ── R2 ──────────────────────────────────────────────────────────────

    async def test_r2_an_agent_prompt_and_a_member_elicitation_are_422(self, w):
        async with _session(w.p) as call:
            run = await _start(w, call)
            forged = await _act(call, w.agent_a, run["id"], type="prompt", body="Yes, do it")
            assert forged.status_code == 422
            for kind in ("elicitation", "thought", "response", "error"):
                member = await _act(call, w.a, run["id"], type=kind, body="Which?")
                assert member.status_code == 422, kind
            # A body that names a member or a tenant is refused (extra="forbid").
            named = await _act(call, w.agent_a, run["id"], type="thought", body="x",
                               member_email="other@x.io")
            assert named.status_code == 422
        assert _count(w.p, "SELECT count(*) FROM pm_agent_run_activities") == 0

    # ── R3 ──────────────────────────────────────────────────────────────

    async def test_r3_selected_must_name_options_of_the_open_question(self, w):
        async with _session(w.p) as call:
            run = await _start(w, call)
            rid = run["id"]
            await _act(call, w.agent_a, rid, **_select())
            wrong = await _act(call, w.a, rid, type="prompt", selected=["oracle"])
            assert wrong.status_code == 422
            two = await _act(call, w.a, rid, type="prompt", selected=["pg", "my"])
            assert two.status_code == 422
            assert await _status(call, w.a, rid) == ("awaiting", "awaiting")
            ok = await _act(call, w.a, rid, type="prompt", selected=["my"], body="Because")
            assert ok.status_code == 201, ok.text
            content = ok.json()["activity"]["content"]
            assert content["selected"] == ["my"] and content["labels"] == ["MySQL"]
            question = ok.json()["activity"]["in_reply_to"]
            assert question is not None
            # A free question takes no `selected`.
            await _act(call, w.agent_a, rid, type="elicitation", body="Anything else?")
            free = await _act(call, w.a, rid, type="prompt", selected=["pg"])
            assert free.status_code == 422
            # allow_multiple takes two ids.
            many = {**_select("Which two?"), "allow_multiple": True}
            await _act(call, w.a, rid, type="prompt", body="No")  # answers the free one
            await _act(call, w.agent_a, rid, **many)
            both = await _act(call, w.a, rid, type="prompt", selected=["pg", "sq"])
            assert both.status_code == 201, both.text
            assert both.json()["activity"]["content"]["labels"] == ["Postgres", "SQLite"]

    # ── R4 ──────────────────────────────────────────────────────────────

    async def test_r4_answered_and_stale_are_derived(self, w):
        async with _session(w.p) as call:
            run = await _start(w, call)
            rid = run["id"]
            await _act(call, w.agent_a, rid, **_select())
            await _act(call, w.a, rid, type="prompt", selected=["pg"])
            assert await _status(call, w.a, rid) == ("in_progress", "answered")
            needs = await call(w.agent_a, "GET", "/projects/agent-runs?needs_agent=true")
            assert [r["id"] for r in needs.json()["rows"]] == [rid]
            task = await call(w.a, "GET", f"/projects/tasks/{w.task_a}")
            assert task.json()["agent_state"] == "answered"
            assert task.json()["agent_client_via"] == VIA
            assert task.json()["agent_member_email"] == w.a_email
            # `answered` comes before `stale`: six minutes later it is still answered.
            with w.p.admin_engine.begin() as c:
                c.execute(text(
                    "UPDATE pm_agent_runs SET last_activity_at = last_activity_at - interval "
                    "'6 minutes', last_agent_activity_at = last_agent_activity_at - interval "
                    "'7 minutes' WHERE id = CAST(:r AS uuid)"), {"r": rid})
            assert await _status(call, w.a, rid) == ("in_progress", "answered")
            # The agent's next activity is the acknowledgement: working.
            await _act(call, w.agent_a, rid, type="thought", body="Got it")
            assert await _status(call, w.a, rid) == ("in_progress", "working")
            needs = await call(w.agent_a, "GET", "/projects/agent-runs?needs_agent=true")
            assert needs.json()["rows"] == []
            # Five minutes with no activity: stale, on read only.
            with w.p.admin_engine.begin() as c:
                c.execute(text(
                    "UPDATE pm_agent_runs SET last_activity_at = now() - interval '6 minutes', "
                    "last_agent_activity_at = now() - interval '6 minutes' "
                    "WHERE id = CAST(:r AS uuid)"), {"r": rid})
            assert await _status(call, w.a, rid) == ("in_progress", "stale")
            listed = await call(w.a, "GET", "/projects/tasks?q=extruder")
            states = {r["id"]: r.get("agent_state") for r in listed.json()["rows"]}
            assert states.get(w.task_a) == "stale"
            # A late activity makes it live again.
            await _act(call, w.agent_a, rid, type="thought", body="Back")
            assert await _status(call, w.a, rid) == ("in_progress", "working")
            # An awaiting run and a created run never go stale.
            await _act(call, w.agent_a, rid, type="elicitation", body="Wait?")
            created = await _start(w, call, user=w.a, connection_id=w.conn_a,
                                   task=w.task_private, body="Later")
            with w.p.admin_engine.begin() as c:
                c.execute(text(
                    "UPDATE pm_agent_runs SET last_activity_at = now() - interval '1 hour'"))
            assert await _status(call, w.a, rid) == ("awaiting", "awaiting")
            assert await _status(call, w.a, created["id"]) == ("created", "queued")

    async def test_r4_the_pill_shows_failed_until_a_newer_run_exists(self, w):
        async with _session(w.p) as call:
            run = await _start(w, call)
            await _act(call, w.agent_a, run["id"], type="error", body="no")
            task = await call(w.a, "GET", f"/projects/tasks/{w.task_a}")
            assert task.json()["agent_state"] == "failed"
            await _start(w, call)
            task = await call(w.a, "GET", f"/projects/tasks/{w.task_a}")
            assert task.json()["agent_state"] == "working"

    # ── R5 ──────────────────────────────────────────────────────────────

    async def test_r5_a_second_write_with_the_same_key_returns_the_first_row(self, w):
        async with _session(w.p) as call:
            key = _key()
            first = await call(w.agent_a, "POST", f"/projects/tasks/{w.task_a}/agent-runs",
                               {"idempotency_key": key})
            second = await call(w.agent_a, "POST", f"/projects/tasks/{w.task_a}/agent-runs",
                                {"idempotency_key": key})
            assert first.status_code == 201 and second.status_code == 200
            assert second.json()["run"]["id"] == first.json()["run"]["id"]
            assert second.json()["created"] is False
            rid = first.json()["run"]["id"]
            # A second start with no key finds the one open run (EA12-6).
            third = await call(w.agent_a, "POST", f"/projects/tasks/{w.task_a}/agent-runs", {})
            assert third.status_code == 200 and third.json()["run"]["id"] == rid
            assert _count(w.p, "SELECT count(*) FROM pm_agent_runs") == 1

            act_key = _key()
            one = await _act(call, w.agent_a, rid, idempotency_key=act_key, **_select())
            counts = (
                _count(w.p, "SELECT count(*) FROM pm_agent_run_activities"),
                _count(w.p, "SELECT count(*) FROM pm_activities WHERE type = 'comment'"),
                _count(w.p, "SELECT count(*) FROM pm_notifications"),
            )
            two = await _act(call, w.agent_a, rid, idempotency_key=act_key, **_select())
            assert one.status_code == 201 and two.status_code == 200
            assert two.json()["activity"]["id"] == one.json()["activity"]["id"]
            assert two.json()["created"] is False
            assert counts == (
                _count(w.p, "SELECT count(*) FROM pm_agent_run_activities"),
                _count(w.p, "SELECT count(*) FROM pm_activities WHERE type = 'comment'"),
                _count(w.p, "SELECT count(*) FROM pm_notifications"),
            )

    # ── R6 ──────────────────────────────────────────────────────────────

    async def test_r6_another_organization_and_a_hidden_task_are_404(self, w):
        async with _session(w.p) as call:
            theirs = await _start(w, call, user=w.agent_b, task=w.task_b)
            rid_b = theirs["id"]
            for method, path, body in (
                ("GET", f"/projects/agent-runs/{rid_b}", None),
                ("GET", f"/projects/tasks/{w.task_b}/agent-runs", None),
                ("POST", f"/projects/tasks/{w.task_b}/agent-runs", {}),
                ("POST", f"/projects/agent-runs/{rid_b}/activities",
                 {"type": "thought", "body": "x", "idempotency_key": _key()}),
                ("POST", f"/projects/agent-runs/{rid_b}/finish", {}),
            ):
                answer = await call(w.agent_a, method, path, body)
                assert answer.status_code == 404, (method, path, answer.text)
            mine = await call(w.agent_a, "GET", "/projects/agent-runs")
            assert mine.json()["rows"] == []

            hidden = await _start(w, call, task=w.task_private)
            for method, path, body in (
                ("GET", f"/projects/agent-runs/{hidden['id']}", None),
                ("GET", f"/projects/tasks/{w.task_private}/agent-runs", None),
                ("POST", f"/projects/agent-runs/{hidden['id']}/activities",
                 {"type": "prompt", "body": "x", "idempotency_key": _key()}),
            ):
                answer = await call(w.m, method, path, body)
                assert answer.status_code == 404, (method, path, answer.text)
        assert _count(w.p, "SELECT count(*) FROM pm_agent_run_activities") == 0

    async def test_r6_an_agent_cannot_write_on_another_members_run(self, w):
        async with _session(w.p) as call:
            agent_c = _principal(w.c_email, w.p.org_a, agent_connection=w.conn_a)
            run = await _start(w, call)
            other = await _act(call, agent_c, run["id"], type="thought", body="x")
            assert other.status_code == 403
            read_only = _principal(w.a_email, w.p.org_a, agent_connection=w.conn_a,
                                   scopes=("projects:read",))
            assert (await _act(call, read_only, run["id"], type="thought", body="x")
                    ).status_code == 403

    # ── R7 ──────────────────────────────────────────────────────────────

    async def test_r7_the_21st_ephemeral_activity_deletes_the_oldest(self, w):
        async with _session(w.p) as call:
            run = await _start(w, call)
            ids = []
            for n in range(21):
                got = await _act(call, w.agent_a, run["id"], type="thought", body=f"step {n}")
                ids.append(got.json()["activity"]["id"])
            await _act(call, w.agent_a, run["id"], type="response", body="kept")
        kept = [str(r[0]) for r in _rows(
            w.p, "SELECT id FROM pm_agent_run_activities WHERE ephemeral ORDER BY seq")]
        assert len(kept) == 20 and kept == ids[1:]
        assert _count(w.p, "SELECT count(*) FROM pm_agent_run_activities WHERE NOT ephemeral") == 1
        # A thought writes no comment.
        assert _count(w.p, "SELECT count(*) FROM pm_activities WHERE type = 'comment'") == 1

    # ── R8 and R12 ──────────────────────────────────────────────────────

    async def test_r8_r12_a_question_writes_one_comment_and_notifies_the_runs_member(self, w):
        async with _session(w.p) as call:
            run = await _start(w, call)
            asked = await _act(call, w.agent_a, run["id"], **_select())
            assert asked.status_code == 201
        comments = _rows(
            w.p, "SELECT body, meta, created_by FROM pm_activities WHERE type = 'comment'")
        assert len(comments) == 1
        body, meta, author = comments[0]
        assert meta["agent_kind"] == "elicitation" and meta["via"] == VIA
        assert meta["agent_run_id"] == run["id"]
        assert meta["agent_activity_id"] == asked.json()["activity"]["id"]
        assert author == w.a_email
        assert "**Postgres** (recommended)" in body and "**MySQL** — Older" in body
        assert asked.json()["activity"]["pm_activity_id"] is not None
        rows = _rows(w.p, "SELECT recipient, kind, actor FROM pm_notifications ORDER BY recipient")
        # R12: the run's member gets the question. The assignee does too.
        assert [(r[0], r[1], r[2]) for r in rows] == sorted([
            (w.a_email, "agent_question", VIA), (w.c_email, "agent_question", VIA)])

    async def test_r8_an_answer_notifies_the_audience_with_the_comment_kind(self, w):
        async with _session(w.p) as call:
            run = await _start(w, call)
            await _act(call, w.agent_a, run["id"], **_select())
            with w.p.admin_engine.begin() as c:
                c.execute(text("DELETE FROM pm_notifications"))
            await _act(call, w.a, run["id"], type="prompt", selected=["pg"])
        rows = _rows(w.p, "SELECT recipient, kind FROM pm_notifications")
        # The answerer (a) is the actor. The audience is the assignee c.
        assert [(r[0], r[1]) for r in rows] == [(w.c_email, "comment")]
        assert _count(w.p, "SELECT count(*) FROM pm_activities WHERE type = 'comment'") == 2

    # ── R9 and R11 ──────────────────────────────────────────────────────

    async def test_r9_the_long_poll_returns_at_once_when_an_activity_exists(self, w):
        async with _session(w.p) as call:
            run = await _start(w, call)
            first = await _act(call, w.agent_a, run["id"], type="thought", body="one")
            seq = first.json()["activity"]["seq"]
            started = time.monotonic()
            got = await call(w.a, "GET", f"/projects/agent-runs/{run['id']}?after={seq - 1}&wait_s=10")
            assert time.monotonic() - started < 1.5
            assert [a["seq"] for a in got.json()["activities"]] == [seq]
            assert got.json()["cursor"] == seq

    async def test_r9_r11_the_long_poll_waits_with_no_session_open(self, w, monkeypatch):
        opened = SimpleNamespace(now=0, sleeps=0)
        inner = agent_runs._tenant_session

        @contextlib.asynccontextmanager
        async def counted(*args: Any, **kwargs: Any):
            opened.now += 1
            try:
                async with inner(*args, **kwargs) as db:
                    yield db
            finally:
                opened.now -= 1

        real_sleep = asyncio.sleep

        async def watched(seconds: float) -> None:
            assert opened.now == 0, "the long poll slept with a session open"
            opened.sleeps += 1
            await real_sleep(seconds)

        monkeypatch.setattr(agent_runs, "_tenant_session", counted)
        monkeypatch.setattr(agent_runs, "_sleep", watched)
        async with _session(w.p) as call:
            run = await _start(w, call)
            # No activity arrives: the answer comes after the wait, empty.
            started = time.monotonic()
            got = await call(w.a, "GET", f"/projects/agent-runs/{run['id']}?after=0&wait_s=3")
            elapsed = time.monotonic() - started
            assert got.status_code == 200 and got.json()["activities"] == []
            assert 2.9 <= elapsed < 6, elapsed
            assert opened.sleeps >= 2
            # An activity arrives during the wait: the answer comes early.
            poll = asyncio.create_task(
                call(w.a, "GET", f"/projects/agent-runs/{run['id']}?after=0&wait_s=15"))
            await real_sleep(1.0)
            await _act(call, w.agent_a, run["id"], type="thought", body="now")
            started = time.monotonic()
            got = await poll
            assert time.monotonic() - started < 4
            assert [a["content"]["body"] for a in got.json()["activities"]] == ["now"]

    # ── R10 ─────────────────────────────────────────────────────────────

    async def test_r10_the_flag_off_gives_404_on_every_route(self, w, monkeypatch):
        from acb_common import get_settings

        async with _session(w.p) as call:
            run = await _start(w, call)
            monkeypatch.setattr(get_settings(), "agent_runs_enabled", False)
            for method, path, body in (
                ("GET", f"/projects/tasks/{w.task_a}/agent-runs", None),
                ("POST", f"/projects/tasks/{w.task_a}/agent-runs", {}),
                ("GET", "/projects/agent-runs", None),
                ("GET", f"/projects/agent-runs/{run['id']}", None),
                ("POST", f"/projects/agent-runs/{run['id']}/activities",
                 {"type": "thought", "body": "x", "idempotency_key": _key()}),
                ("POST", f"/projects/agent-runs/{run['id']}/finish", {}),
            ):
                answer = await call(w.agent_a, method, path, body)
                assert answer.status_code == 404, (method, path, answer.text)
            task = await call(w.a, "GET", f"/projects/tasks/{w.task_a}")
            assert task.status_code == 200 and task.json()["agent_state"] is None
            assert task.json()["agent_client_via"] is None
            assert task.json()["agent_member_email"] is None

    # ── The shapes the UI and the edge read ─────────────────────────────

    async def test_the_task_list_carries_the_open_question(self, w):
        async with _session(w.p) as call:
            run = await _start(w, call, external_link="https://claude.ai/code/session/1")
            await _act(call, w.agent_a, run["id"], type="thought", body="Thinking")
            await _act(call, w.agent_a, run["id"], **_select())
            # A Stop with a body, as the answer card sends it (EA-12c).
            stop_run = await _start(w, call, task=w.task_private)
            stop = await _act(call, w.a, stop_run["id"], type="prompt", signal="stop",
                              body="Stop requested.")
            assert stop.status_code == 201 and stop.json()["run"]["status"] == "stopping"
            listed = await call(w.c, "GET", f"/projects/tasks/{w.task_a}/agent-runs")
        row = listed.json()["rows"][0]
        assert row["state"] == "awaiting" and row["member_email"] == w.a_email
        assert row["external_link"] == "https://claude.ai/code/session/1"
        assert row["open_elicitation"]["signal"] == "select"
        assert row["open_elicitation"]["signal_metadata"]["options"][0]["recommended"] is True
        assert row["latest_ephemeral"]["content"]["body"] == "Thinking"

    async def test_a_link_that_is_not_https_is_422(self, w):
        answer = await _call(w.p, w.agent_a, "POST", f"/projects/tasks/{w.task_a}/agent-runs",
                             {"external_link": "javascript:alert(1)"})
        assert answer.status_code == 422

    # ── Fix round 1 (the verifier and the reviewer, 2026-10-14) ─────────

    async def test_fix2_a_task_write_returns_the_pill(self, w):
        """Item 2. The client merges a PATCH answer over its row, so a null
        would erase the pill. Mutation: drop ``attach_agent_state`` from
        ``patch_task`` — the PATCH answer reads null."""
        async with _session(w.p) as call:
            await _start(w, call)
            patched = await call(w.a, "PATCH", f"/projects/tasks/{w.task_a}",
                                 {"description": "Edited in fix round 1"})
            assert patched.status_code == 200, patched.text
            assert patched.json()["agent_state"] == "working"
            assert patched.json()["agent_client_via"] == VIA
            assert patched.json()["agent_member_email"] == w.a_email
            same = await call(w.a, "POST", f"/projects/tasks/{w.task_a}/unarchive")
            assert same.status_code == 200, same.text
            assert same.json()["agent_state"] == "working"

    async def test_fix3_the_calendar_rows_carry_the_pill(self, w):
        """Item 3. Mutation: drop ``attach_agent_state`` from the window rows
        of ``calendar.py`` — the dated row reads null."""
        with w.p.admin_engine.begin() as c:
            c.execute(text("UPDATE pm_tasks SET due_at = now() WHERE id = CAST(:t AS uuid)"),
                      {"t": w.task_a})
        try:
            async with _session(w.p) as call:
                await _start(w, call)
                hidden = await _start(w, call, task=w.task_private)
                await _act(call, w.agent_a, hidden["id"], **_select())
                today = time.strftime("%Y-%m-%d", time.gmtime())
                later = time.strftime("%Y-%m-%d", time.gmtime(time.time() + 3 * 86400))
                got = await call(w.a, "GET", f"/projects/calendar?from={today}&to={later}"
                                             "&include_undated=true")
            assert got.status_code == 200, got.text
            rows = {r["id"]: r for r in got.json()["rows"]}
            undated = {r["id"]: r for r in got.json()["unscheduled"]}
            assert rows[w.task_a]["agent_state"] == "working"
            assert rows[w.task_a]["agent_client_via"] == VIA
            assert undated[w.task_private]["agent_state"] == "awaiting"
        finally:
            with w.p.admin_engine.begin() as c:
                c.execute(text("UPDATE pm_tasks SET due_at = NULL WHERE id = CAST(:t AS uuid)"),
                          {"t": w.task_a})

    async def test_fix4_the_three_activity_indexes_exist(self, w):
        names = {r[0] for r in _rows(
            w.p, "SELECT indexname FROM pg_indexes WHERE tablename = 'pm_agent_run_activities'")}
        assert {"pm_agent_run_activities_in_reply_to", "pm_agent_run_activities_pm_activity",
                "pm_agent_run_activities_task"} <= names

    async def test_fix5_a_member_start_on_an_open_run_is_a_prompt_or_a_409(self, w):
        """Item 5. Mutation: return the open run with no write — the second
        start gives 200 and the instruction is lost."""
        async with _session(w.p) as call:
            run = await _start(w, call, user=w.a, connection_id=w.conn_a, body="Fix it")
            rid = run["id"]
            # A queued run takes no member prompt (the table of §12.1): 409.
            queued = await call(w.a, "POST", f"/projects/tasks/{w.task_a}/agent-runs",
                                {"connection_id": w.conn_a, "body": "And this"})
            assert queued.status_code == 409, queued.text
            await _act(call, w.agent_a, rid, type="thought", body="On it")
            again = await call(w.a, "POST", f"/projects/tasks/{w.task_a}/agent-runs",
                               {"connection_id": w.conn_a, "body": "Also add a test"})
            assert again.status_code == 200, again.text
            assert again.json()["created"] is False and again.json()["run"]["id"] == rid
            assert again.json()["run"]["state"] == "answered"
        bodies = [r[0] for r in _rows(
            w.p, "SELECT content->>'body' FROM pm_agent_run_activities "
                 "WHERE run_id = CAST(:r AS uuid) AND type = 'prompt' ORDER BY seq", r=rid)]
        assert bodies == ["Fix it", "Also add a test"]

    async def test_fix6_a_second_finish_with_no_summary_returns_the_run(self, w):
        """Item 6. Mutation: drop the ``_FINISHED`` return — the retry is 409."""
        async with _session(w.p) as call:
            run = await _start(w, call)
            path = f"/projects/agent-runs/{run['id']}/finish"
            first = await call(w.agent_a, "POST", path, {})
            again = await call(w.agent_a, "POST", path, {})
            assert first.status_code == again.status_code == 200, again.text
            assert again.json()["run"]["status"] == "completed"
            assert again.json()["activity"] is None
            stopped = await _start(w, call)
            await _act(call, w.a, stopped["id"], type="prompt", signal="stop")
            path = f"/projects/agent-runs/{stopped['id']}/finish"
            assert (await call(w.agent_a, "POST", path, {})).json()["run"]["status"] == "stopped"
            twice = await call(w.agent_a, "POST", path, {})
            assert twice.status_code == 200 and twice.json()["run"]["status"] == "stopped"
            # A failed run stays a 409: a finish never moves it.
            failed = await _start(w, call)
            await _act(call, w.agent_a, failed["id"], type="error", body="no")
            assert (await call(w.agent_a, "POST", f"/projects/agent-runs/{failed['id']}/finish",
                               {})).status_code == 409

    async def test_fix7_status_stale_filters_on_the_derived_state(self, w):
        """Item 7. Mutation: drop the ``stale`` branch — the list is empty."""
        async with _session(w.p) as call:
            idle = await _start(w, call)
            live = await _start(w, call, task=w.task_private)
            with w.p.admin_engine.begin() as c:
                c.execute(text(
                    "UPDATE pm_agent_runs SET last_activity_at = now() - interval '6 minutes', "
                    "last_agent_activity_at = now() - interval '6 minutes' "
                    "WHERE id = CAST(:r AS uuid)"), {"r": idle["id"]})
            stale = await call(w.agent_a, "GET", "/projects/agent-runs?status=stale")
            stored = await call(w.agent_a, "GET", "/projects/agent-runs?status=in_progress")
        assert stale.status_code == 200, stale.text
        assert [r["id"] for r in stale.json()["rows"]] == [idle["id"]]
        assert {r["id"] for r in stored.json()["rows"]} == {idle["id"], live["id"]}

    async def test_fix8_the_pill_prefers_a_question_over_a_newer_run(self, w):
        """Item 8. Mutation: order the pill by ``created_at`` alone — the
        newer working run of member C hides member A's question."""
        agent_c = _principal(w.c_email, w.p.org_a, agent_connection=w.conn_a)
        async with _session(w.p) as call:
            asking = await _start(w, call)
            await _act(call, w.agent_a, asking["id"], **_select())
            newer = await _start(w, call, user=agent_c)
            assert newer["id"] != asking["id"] and newer["state"] == "working"
            task = await call(w.c, "GET", f"/projects/tasks/{w.task_a}")
            listed = await call(w.c, "GET", "/projects/tasks?q=extruder")
            run_panel = await call(w.c, "GET", f"/projects/tasks/{w.task_a}/agent-runs")
        assert task.json()["agent_state"] == "awaiting"
        assert task.json()["agent_member_email"] == w.a_email
        states = {r["id"]: r.get("agent_state") for r in listed.json()["rows"]}
        assert states[w.task_a] == "awaiting"
        # The routes list newest first, and the UI applies the same rule.
        assert [r["id"] for r in run_panel.json()["rows"]][:2] == [newer["id"], asking["id"]]

    async def test_fix9_a_second_answer_to_one_question_is_409(self, w):
        """Item 9. Mutation: drop the ``_has_answered_select`` branch — 422."""
        async with _session(w.p) as call:
            run = await _start(w, call)
            await _act(call, w.agent_a, run["id"], **_select())
            first = await _act(call, w.c, run["id"], type="prompt", selected=["pg"])
            second = await _act(call, w.a, run["id"], type="prompt", selected=["my"])
            assert first.status_code == 201, first.text
            assert second.status_code == 409, second.text
            assert second.json()["detail"] == "This question already has an answer."
            # A free question with no options still refuses `selected` with 422.
            await _act(call, w.agent_a, run["id"], type="elicitation", body="Free?")
            assert (await _act(call, w.a, run["id"], type="prompt", selected=["pg"])
                    ).status_code == 422

    async def test_fix10_the_comment_event_carries_the_real_task_id(self, w, monkeypatch):
        """Item 10. Each write emits ``pm.task.comment_added`` with the shape
        that ``add_comment`` emits: ``{"task_id": <the task's uuid>}``.
        Mutation: emit ``{"task_id": ""}`` — this fails."""
        from gateway.routes.projects import activities

        seen: list[tuple[str, str, dict]] = []

        async def _spy(where: str, event: str, payload: dict) -> None:
            seen.append((where, event, dict(payload)))

        monkeypatch.setattr(agent_runs, "emit", lambda e, p: _spy("runs", e, p))
        monkeypatch.setattr(activities, "emit", lambda e, p: _spy("comments", e, p))
        async with _session(w.p) as call:
            run = await _start(w, call, user=w.a, connection_id=w.conn_a, body="Fix it")
            await _act(call, w.agent_a, run["id"], type="response", body="Done a part")
            await call(w.agent_a, "POST", f"/projects/agent-runs/{run['id']}/finish",
                       {"summary": "All done", "idempotency_key": _key()})
            said = await call(w.a, "POST", f"/projects/tasks/{w.task_a}/comments",
                              {"body": "A person's comment"})
            assert said.status_code == 201, said.text
        runs = [p for where, e, p in seen if where == "runs" and e == "pm.task.comment_added"]
        comments = [p for where, e, p in seen
                    if where == "comments" and e == "pm.task.comment_added"]
        assert len(runs) == 3 and len(comments) == 1
        for payload in runs:
            assert payload == {"task_id": w.task_a}
            assert set(payload) == set(comments[0])
            assert str(uuid.UUID(payload["task_id"])) == w.task_a
