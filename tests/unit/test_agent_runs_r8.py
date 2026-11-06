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
#: EA-13a. The stamp of Claude Code's connection: its client id is
#: :data:`CLIENT_ID`, so ``agent_oauth.actor_via_for`` adds the slug.
CLIENT_ID = "https://claude.ai/oauth/claude-code-client-metadata"
VIA = "mcp:claude.ai:claude-code"

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
    # MCP-P1b-1 (§19.5): the two events of an app run. A spend closes an
    # approved request in `acb_auth.app_requests.SPEND_SQL`.
    "app_ask": {"created": "awaiting"},
    "app_close": {"awaiting": "completed", "in_progress": "completed",
                  "stale": "completed"},
    # WS-55 MCP-G15: the agent records a decision that its member made
    # outside Metorite. It ends the wait, and nothing else.
    "agent_outside_decision": {"awaiting": "in_progress"},
    # SW-43: the member takes back an answer that no agent has read.
    "member_withdraw": {"in_progress": "awaiting", "stale": "awaiting"},
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
        ("POST", "/projects/agent-runs/{run_id}/answer/withdraw"),
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
    # EA-13a: the label of the stamp, never "Claude.ai" (EA3-45).
    assert needs._sentence(row) == "Claude Code asked you about #4 Fix it"


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
            " expires_at) VALUES (CAST(:o AS uuid), :m, :client,"
            " 'Claude Code', :origin, ARRAY['projects:read', 'projects:write'],"
            " CAST('{}' AS uuid[]), false, now() + interval '1 day') RETURNING id"),
            {"o": org, "m": email, "origin": ORIGIN, "client": CLIENT_ID}).scalar())


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
    # EA-18a. These cases read the rule of D-EA-10 point (1), the rule with the
    # flag off. `test_agent_questions_r8.py` holds the rule with it on.
    monkeypatch.setattr(get_settings(), "agent_questions_orgs", "")
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
            # With AGENT_QUESTIONS_ORGS off (the `w` fixture), any member who
            # can see the task answers: in_progress (D-EA-10 point 1).
            answered = await _act(call, w.c, rid, type="prompt", selected=["pg"])
            assert answered.status_code == 201, answered.text
            assert answered.json()["run"]["status"] == "in_progress"
            # A member reply on in_progress keeps in_progress.
            steer = await _act(call, w.a, rid, type="prompt", body="And add an index")
            assert steer.json()["run"]["status"] == "in_progress"
            # RA-4b. A prompt to an OPEN run is no reopen: it has no marker.
            assert "reopens" not in steer.json()["activity"]["content"]
            # Finish: completed. A member prompt opens it again.
            done = await call(w.agent_a, "POST", f"/projects/agent-runs/{rid}/finish",
                              {"summary": "Done", "idempotency_key": _key()})
            assert done.status_code == 200 and done.json()["run"]["status"] == "completed"
            assert done.json()["run"]["ended_at"] is not None
            reopened = await _act(call, w.a, rid, type="prompt", body="One more thing")
            assert reopened.json()["run"]["status"] == "in_progress"
            assert reopened.json()["run"]["ended_at"] is None
            # RA-4b. The reopening prompt carries the end it reopened.
            stamp = reopened.json()["activity"]["content"]["reopens"]["ended_at"]
            from datetime import datetime

            assert datetime.fromisoformat(stamp) == datetime.fromisoformat(
                done.json()["run"]["ended_at"].replace("Z", "+00:00"))
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
            # SW-10a (spec §12.6): `answered` has an age limit, the stale band.
            # Six minutes after the answer, with no agent activity since, the
            # run reads `stale`. Before SW-10a it read `answered` for ever.
            # `needs_agent` keeps the run, and its reason still says
            # `answered`, so `my_work` still offers the answer.
            with w.p.admin_engine.begin() as c:
                c.execute(text(
                    "UPDATE pm_agent_runs SET last_activity_at = last_activity_at - interval "
                    "'6 minutes', last_agent_activity_at = last_agent_activity_at - interval "
                    "'7 minutes' WHERE id = CAST(:r AS uuid)"), {"r": rid})
            assert await _status(call, w.a, rid) == ("in_progress", "stale")
            needs = await call(w.agent_a, "GET", "/projects/agent-runs?needs_agent=true")
            assert [(r["id"], r["state"], r["needs_agent"]) for r in needs.json()["rows"]] == [
                (rid, "stale", "answered")]
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

    # ── CIV-14k: the "Waiting for you" list ─────────────────────────────

    async def test_civ14k_waiting_list_is_oldest_first_with_task_facts_and_hides_unseen(self, w):
        m_email = "ea12-m@runs-r8.test"
        conn = _seed_connection(w.p, w.p.org_a, m_email)
        agent_m = _principal(m_email, w.p.org_a, unrestricted=False, agent_connection=conn)
        task_x = _seed_task(w.p, w.p.org_a, w.a_email, grant=m_email)
        task_y = _seed_task(w.p, w.p.org_a, w.a_email, grant=m_email)
        task_h = _seed_task(w.p, w.p.org_a, w.a_email, grant=m_email)
        async with _session(w.p) as call:
            ids = {}
            for name, task in (("x", task_x), ("y", task_y), ("h", task_h)):
                run = await _start(w, call, user=agent_m, task=task)
                ids[name] = run["id"]
                asked = await _act(call, agent_m, run["id"], **_select(f"Question {name}?"))
                assert asked.status_code == 201, asked.text
            # The member loses sight of the third project.
            with w.p.admin_engine.begin() as c:
                c.execute(text(
                    "DELETE FROM pm_project_grants WHERE subject = :m AND project_id = "
                    "(SELECT project_id FROM pm_tasks WHERE id = CAST(:t AS uuid))"),
                    {"m": m_email, "t": task_h})
            me = _principal(m_email, w.p.org_a, unrestricted=False)
            plain = await call(me, "GET", "/projects/agent-runs?status=awaiting")
            assert plain.status_code == 200, plain.text
            assert "task" not in plain.json()["rows"][0], "the wire stays as it was"
            assert [r["id"] for r in plain.json()["rows"]] == [ids["y"], ids["x"]]
            got = await call(
                me, "GET", "/projects/agent-runs?status=awaiting&order=oldest&with_task=true")
            assert got.status_code == 200, got.text
            rows = got.json()["rows"]
            assert [r["id"] for r in rows] == [ids["x"], ids["y"]]
            assert ids["h"] not in [r["id"] for r in rows]
            for row in rows:
                assert row["open_elicitation"] is not None
                assert row["task"] == {
                    "number": 1, "title": "Fix the extruder", "project_name": "runs",
                }
            assert rows[0]["task_id"] == task_x and rows[1]["task_id"] == task_y
            bad = await call(me, "GET", "/projects/agent-runs?order=sideways")
            assert bad.status_code == 422

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

    async def test_r9_the_long_poll_returns_at_once_when_an_activity_exists(self, w, monkeypatch):
        # "At once" is an order, not a clock (#947): the route never sleeps.
        slept: list[float] = []

        async def counted(seconds: float) -> None:
            slept.append(seconds)

        monkeypatch.setattr(agent_runs, "_sleep", counted)
        async with _session(w.p) as call:
            run = await _start(w, call)
            first = await _act(call, w.agent_a, run["id"], type="thought", body="one")
            seq = first.json()["activity"]["seq"]
            got = await call(w.a, "GET", f"/projects/agent-runs/{run['id']}?after={seq - 1}&wait_s=10")
            assert slept == []
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

        first_sleep = asyncio.Event()

        async def watched(seconds: float) -> None:
            assert opened.now == 0, "the long poll slept with a session open"
            opened.sleeps += 1
            first_sleep.set()
            await real_sleep(seconds)

        monkeypatch.setattr(agent_runs, "_tenant_session", counted)
        monkeypatch.setattr(agent_runs, "_sleep", watched)
        async with _session(w.p) as call:
            run = await _start(w, call)
            # No activity arrives: the answer comes after the wait, empty.
            got = await call(w.a, "GET", f"/projects/agent-runs/{run['id']}?after=0&wait_s=3")
            assert got.status_code == 200 and got.json()["activities"] == []
            assert opened.sleeps >= 2  # 3 s in ticks of 2 s: it waited, and read again
            # An activity arrives during the wait: the answer comes at the
            # next tick, long before the 8 ticks of a full 15 s wait.
            opened.sleeps = 0
            first_sleep.clear()
            poll = asyncio.create_task(
                call(w.a, "GET", f"/projects/agent-runs/{run['id']}?after=0&wait_s=15"))
            await asyncio.wait_for(first_sleep.wait(), timeout=30)  # a hang guard
            await _act(call, w.agent_a, run["id"], type="thought", body="now")
            got = await asyncio.wait_for(poll, timeout=30)  # a hang guard
            assert opened.sleeps <= 3, opened.sleeps
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
        """Item 8, after EA-14a. A task has ONE open run, so a newer run of
        member C cannot hide member A's question: C's start is refused with
        ``task_held`` and the pill keeps the question. Mutation: drop the
        gate in ``_gate_task`` — C's run opens and the second read below
        finds two runs."""
        agent_c = _principal(w.c_email, w.p.org_a, agent_connection=w.conn_a)
        async with _session(w.p) as call:
            asking = await _start(w, call)
            await _act(call, w.agent_a, asking["id"], **_select())
            refused = await call(agent_c, "POST", f"/projects/tasks/{w.task_a}/agent-runs", {})
            assert refused.status_code == 409, refused.text
            assert refused.json()["detail"]["error"] == "task_held"
            task = await call(w.c, "GET", f"/projects/tasks/{w.task_a}")
            listed = await call(w.c, "GET", "/projects/tasks?q=extruder")
            run_panel = await call(w.c, "GET", f"/projects/tasks/{w.task_a}/agent-runs")
        assert task.json()["agent_state"] == "awaiting"
        assert task.json()["agent_member_email"] == w.a_email
        states = {r["id"]: r.get("agent_state") for r in listed.json()["rows"]}
        assert states[w.task_a] == "awaiting"
        assert [r["id"] for r in run_panel.json()["rows"]] == [asking["id"]]

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

    # ── EA-12d: a member starts a run ───────────────────────────────────

    async def test_ea12d_a_members_run_is_queued_found_and_picked_up(self, w):
        """EA-12d, the acceptance of §15. A member starts a run for one of
        their own connections: it reads ``queued``. The agent finds it with
        ``needs_agent=true``. Its first activity moves it to ``in_progress``,
        and then ``needs_agent`` no longer lists it.

        Mutations: ``_NEEDS_AGENT_SQL`` drops ``'created'`` and the find
        fails. ``member_start`` maps ``created`` to ``in_progress`` and the
        queued check fails."""
        async with _session(w.p) as call:
            made = await call(w.a, "POST", f"/projects/tasks/{w.task_a}/agent-runs",
                              {"connection_id": w.conn_a, "body": "Write the tests",
                               "idempotency_key": _key()})
            assert made.status_code == 201, made.text
            assert made.json()["created"] is True
            run = made.json()["run"]
            assert (run["status"], run["state"]) == ("created", "queued")
            assert run["connection_id"] == w.conn_a and run["client_via"] == VIA
            assert run["started_at"] is None and run["started_by"] == w.a_email
            task = await call(w.a, "GET", f"/projects/tasks/{w.task_a}")
            assert task.json()["agent_state"] == "queued"
            # The instruction is the first prompt, with no status move.
            read = await call(w.agent_a, "GET", f"/projects/agent-runs/{run['id']}")
            (prompt,) = read.json()["activities"]
            assert prompt["type"] == "prompt" and prompt["actor_via"] is None
            assert prompt["content"]["body"] == "Write the tests"

            needs = await call(w.agent_a, "GET", "/projects/agent-runs?needs_agent=true")
            assert needs.status_code == 200, needs.text
            assert [r["id"] for r in needs.json()["rows"]] == [run["id"]]
            assert needs.json()["rows"][0]["state"] == "queued"

            moved = await _act(call, w.agent_a, run["id"], type="thought", body="On it")
            assert moved.status_code == 201, moved.text
            assert moved.json()["run"]["status"] == "in_progress"
            assert moved.json()["run"]["state"] == "working"
            assert moved.json()["run"]["started_at"] is not None
            again = await call(w.agent_a, "GET", "/projects/agent-runs?needs_agent=true")
            assert again.json()["rows"] == []

    async def test_ea12d_only_the_members_own_live_connection_starts_a_run(self, w):
        """EA-12d. A member names only their own live connection. Another
        member's connection, a revoked one and an empty instruction are 422,
        and no run is made. Mutation: the connection read drops
        ``lower(member_email) = :me`` and the first check fails."""
        async with _session(w.p) as call:
            theirs = await call(w.c, "POST", f"/projects/tasks/{w.task_a}/agent-runs",
                                {"connection_id": w.conn_a, "body": "Go"})
            assert theirs.status_code == 422, theirs.text
            empty = await call(w.a, "POST", f"/projects/tasks/{w.task_a}/agent-runs",
                               {"connection_id": w.conn_a, "body": "   "})
            assert empty.status_code == 422, empty.text
            with w.p.admin_engine.begin() as c:
                c.execute(text("UPDATE agent_connection SET revoked_at = now() "
                               "WHERE id = CAST(:c AS uuid)"), {"c": w.conn_a})
            try:
                revoked = await call(w.a, "POST", f"/projects/tasks/{w.task_a}/agent-runs",
                                     {"connection_id": w.conn_a, "body": "Go"})
                assert revoked.status_code == 422, revoked.text
            finally:
                with w.p.admin_engine.begin() as c:
                    c.execute(text("UPDATE agent_connection SET revoked_at = NULL "
                                   "WHERE id = CAST(:c AS uuid)"), {"c": w.conn_a})
        assert _count(w.p, "SELECT count(*) FROM pm_agent_runs") == 0


# ── SW-43a: withdraw an answer (spec agent_coordination.md §3.9) ────────────


@pytest.fixture
def wd(w, monkeypatch):
    """``w`` with the flag of SW-43 on."""
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "agent_answer_change_enabled", True)
    return w


async def _answered(w: Any, call: Any) -> tuple[str, str, str]:
    """A run whose member answered the agent's question.
    Returns ``(run_id, question_id, answer_id)``."""
    run = await _start(w, call)
    rid = run["id"]
    asked = await _act(call, w.agent_a, rid, **_select())
    qid = asked.json()["activity"]["id"]
    got = await _act(call, w.a, rid, type="prompt", selected=["pg"])
    assert got.status_code == 201, got.text
    return rid, qid, got.json()["activity"]["id"]


def _withdraw(call: Any, user: Any, rid: str, answer: str, key: str | None = None) -> Any:
    return call(user, "POST", f"/projects/agent-runs/{rid}/answer/withdraw",
                {"answer_id": answer, "idempotency_key": key or _key()})


@_DB_GATE
class TestWithdrawAnAnswer:
    async def test_the_flag_off_gives_404_and_no_answer_field(self, w):
        async with _session(w.p) as call:
            rid, _q, aid = await _answered(w, call)
            assert (await _withdraw(call, w.a, rid, aid)).status_code == 404
            got = await call(w.a, "GET", f"/projects/agent-runs/{rid}")
            assert "answer" not in got.json()["run"]

    async def test_a_withdraw_opens_the_question_and_the_run_waits_for_a_person(self, wd):
        w = wd
        async with _session(w.p) as call:
            rid, qid, aid = await _answered(w, call)
            before = await call(w.a, "GET", f"/projects/agent-runs/{rid}")
            assert before.json()["run"]["answer"]["id"] == aid
            assert before.json()["run"]["answer"]["changeable"] is True
            assert before.json()["run"]["needs_agent"] == "answered"
            done = await _withdraw(call, w.a, rid, aid)
            assert done.status_code == 200, done.text
            run = done.json()["run"]
            assert (run["status"], run["state"]) == ("awaiting", "awaiting")
            assert run["needs_agent"] is None
            assert run["open_elicitation"]["id"] == qid
            assert run["answer"] is None
            needs = await call(w.a, "GET", "/projects/agent-runs?needs_agent=true")
            assert rid not in [r["id"] for r in needs.json()["rows"]]
            # The agent read hides the withdrawn answer and shows the marker.
            read = await call(w.agent_a, "GET", f"/projects/agent-runs/{rid}")
            rows = read.json()["activities"]
            assert aid not in [a["id"] for a in rows]
            marker = rows[-1]
            assert marker["type"] == "prompt" and marker["content"]["withdraws"] == aid
            assert marker["in_reply_to"] is None
            row = _rows(w.p, "SELECT in_reply_to, content, content_metadata "
                             "FROM pm_agent_run_activities WHERE id = CAST(:i AS uuid)", i=aid)[0]
            assert row.in_reply_to is None
            assert row.content["withdrawn"]["by"] == w.a_email
            assert row.content_metadata["question_id"] == qid
            # The member answers again with the normal card.
            again = await _act(call, w.a, rid, type="prompt", selected=["my"])
            assert again.status_code == 201, again.text
            assert again.json()["run"]["answer"]["changeable"] is True
            assert again.json()["run"]["answer"]["id"] != aid

    async def test_an_agent_activity_after_the_answer_gives_409(self, wd):
        w = wd
        async with _session(w.p) as call:
            rid, _q, aid = await _answered(w, call)
            seen = await _act(call, w.agent_a, rid, type="thought", body="Got it")
            assert seen.json()["run"]["answer"]["changeable"] is False
            late = await _withdraw(call, w.a, rid, aid)
            assert late.status_code == 409
            assert "already has your answer" in late.json()["detail"]
            assert _count(w.p, "SELECT count(*) FROM pm_agent_run_activities "
                               "WHERE run_id = CAST(:r AS uuid) AND content ? 'withdraws'",
                          r=rid) == 0

    async def test_only_the_runs_member_and_never_an_agent(self, wd):
        w = wd
        async with _session(w.p) as call:
            rid, _q, aid = await _answered(w, call)
            assert (await _withdraw(call, w.c, rid, aid)).status_code == 403
            assert (await _withdraw(call, w.agent_a, rid, aid)).status_code == 403
            assert (await _withdraw(call, w.b, rid, aid)).status_code == 404
            other = await _withdraw(call, w.a, rid, str(uuid.uuid4()))
            assert other.status_code == 409
            assert _count(w.p, "SELECT count(*) FROM pm_agent_run_activities "
                               "WHERE run_id = CAST(:r AS uuid) AND content ? 'withdrawn'",
                          r=rid) == 0

    async def test_a_replay_of_the_key_writes_once(self, wd):
        w = wd
        async with _session(w.p) as call:
            rid, _q, aid = await _answered(w, call)
            key = _key()
            first = await _withdraw(call, w.a, rid, aid, key)
            second = await _withdraw(call, w.a, rid, aid, key)
            assert (first.status_code, second.status_code) == (200, 200)
            assert second.json()["created"] is False
            assert first.json()["activity"]["id"] == second.json()["activity"]["id"]
            assert _count(w.p, "SELECT count(*) FROM pm_agent_run_activities "
                               "WHERE run_id = CAST(:r AS uuid) AND content ? 'withdraws'",
                          r=rid) == 1

    async def test_a_withdraw_and_an_agent_post_serialize_on_the_lock(self, wd):
        """Mutation: take no lock in the route and the withdraw does not wait,
        so the first assert fails. Mutation: drop the ``actor_via`` test from
        ``answer_changeable_sql`` and the withdraw wins over the late post."""
        w = wd
        async with _session(w.p) as call:
            rid, _q, aid = await _answered(w, call)
            holder = w.p.admin_engine.connect()
            tx = holder.begin()
            try:
                holder.execute(text("SELECT id FROM pm_agent_runs "
                                    "WHERE id = CAST(:r AS uuid) FOR UPDATE"), {"r": rid})
                racing = asyncio.create_task(_withdraw(call, w.a, rid, aid))
                await asyncio.sleep(1.0)
                assert not racing.done(), "the withdraw did not wait for the lock"
                # The agent's post commits first, as the holder of the lock.
                task_id = w.task_a
                holder.execute(text(
                    "INSERT INTO pm_agent_run_activities (organization_id, run_id, task_id,"
                    " type, content, actor, actor_via, ephemeral) VALUES (CAST(:o AS uuid),"
                    " CAST(:r AS uuid), CAST(:t AS uuid), 'thought',"
                    " CAST('{\"body\": \"read\"}' AS jsonb), :m, :v, true)"),
                    {"o": w.p.org_a, "r": rid, "t": task_id, "m": w.a_email, "v": VIA})
                tx.commit()
            except BaseException:
                tx.rollback()
                raise
            finally:
                holder.close()
            lost = await asyncio.wait_for(racing, timeout=30)
            assert lost.status_code == 409, lost.text
            live = _rows(w.p, "SELECT in_reply_to FROM pm_agent_run_activities "
                              "WHERE id = CAST(:i AS uuid)", i=aid)[0]
            assert live.in_reply_to is not None

    # ── SW-43a review round: the read closes the window ─────────────────

    async def test_an_agent_read_closes_the_window_and_a_member_read_does_not(self, wd):
        """Mutation: remove the stamp in ``_read_run`` and the 409 is a 200.
        Mutation: remove ``NOT (p.content ? 'read_at')`` from
        ``answer_changeable_sql`` and ``changeable`` stays true."""
        w = wd
        async with _session(w.p) as call:
            rid, _q, aid = await _answered(w, call)
            mine = await call(w.a, "GET", f"/projects/agent-runs/{rid}")
            assert mine.json()["run"]["answer"]["changeable"] is True
            assert _count(w.p, "SELECT count(*) FROM pm_agent_run_activities "
                               "WHERE id = CAST(:i AS uuid) AND content ? 'read_at'", i=aid) == 0
            read = await call(w.agent_a, "GET", f"/projects/agent-runs/{rid}")
            assert read.status_code == 200
            assert _count(w.p, "SELECT count(*) FROM pm_agent_run_activities "
                               "WHERE id = CAST(:i AS uuid) AND content ? 'read_at'", i=aid) == 1
            after = await call(w.a, "GET", f"/projects/agent-runs/{rid}")
            assert after.json()["run"]["answer"]["changeable"] is False
            late = await _withdraw(call, w.a, rid, aid)
            assert late.status_code == 409
            assert "already has your answer" in late.json()["detail"]

    async def test_a_claim_that_finds_the_open_run_closes_the_window(self, wd):
        w = wd
        async with _session(w.p) as call:
            rid, _q, aid = await _answered(w, call)
            with w.p.admin_engine.begin() as c:
                c.execute(text(
                    "INSERT INTO pm_task_statuses (id, organization_id, project_id, name,"
                    " category, position) SELECT gen_random_uuid(), t.organization_id,"
                    " t.project_id, 'Doing', 'in_progress', 20 FROM pm_tasks t"
                    " WHERE t.id = CAST(:t AS uuid) AND NOT EXISTS (SELECT 1 FROM"
                    " pm_task_statuses s WHERE s.project_id = t.project_id"
                    " AND s.category = 'in_progress')"), {"t": w.task_a})
            claimed = await call(w.agent_a, "POST", f"/projects/tasks/{w.task_a}/claim", None)
            assert claimed.status_code == 200, claimed.text
            assert _count(w.p, "SELECT count(*) FROM pm_agent_run_activities "
                               "WHERE id = CAST(:i AS uuid) AND content ? 'read_at'", i=aid) == 1
            assert (await _withdraw(call, w.a, rid, aid)).status_code == 409

    async def test_only_the_run_members_own_answer_is_live(self, wd):
        w = wd
        async with _session(w.p) as call:
            run = await _start(w, call)
            rid = run["id"]
            await _act(call, w.agent_a, rid, **_select())
            theirs = await _act(call, w.c, rid, type="prompt", selected=["pg"])
            assert theirs.status_code == 201, theirs.text
            aid = theirs.json()["activity"]["id"]
            got = await call(w.a, "GET", f"/projects/agent-runs/{rid}")
            assert got.json()["run"]["answer"] is None
            gone = await _withdraw(call, w.a, rid, aid)
            assert gone.status_code == 409
            assert "no longer the live answer" in gone.json()["detail"]

    # ── SW-43a: the read takes no lock unless it must ───────────────────

    @staticmethod
    @contextlib.contextmanager
    def _held(w: Any, rid: str):
        """Another connection holds ``FOR UPDATE`` on the run."""
        holder = w.p.admin_engine.connect()
        tx = holder.begin()
        try:
            holder.execute(text("SELECT id FROM pm_agent_runs "
                                "WHERE id = CAST(:r AS uuid) FOR UPDATE"), {"r": rid})
            yield
        finally:
            tx.rollback()
            holder.close()

    @staticmethod
    def _read_at(w: Any, aid: str) -> Any:
        return _rows(w.p, "SELECT content->>'read_at' AS r FROM pm_agent_run_activities "
                          "WHERE id = CAST(:i AS uuid)", i=aid)[0].r

    async def test_an_agent_read_of_a_run_with_no_answer_takes_no_lock(self, wd):
        """Mutation: take ``_lock_run`` before the probe and this hangs."""
        w = wd
        async with _session(w.p) as call:
            run = await _start(w, call)
            rid = run["id"]
            await _act(call, w.agent_a, rid, **_select())
            with self._held(w, rid):
                got = await asyncio.wait_for(
                    call(w.agent_a, "GET", f"/projects/agent-runs/{rid}"), timeout=10)
            assert got.status_code == 200
            assert got.json()["run"]["status"] == "awaiting"
            assert _count(w.p, "SELECT count(*) FROM pm_agent_run_activities "
                               "WHERE run_id = CAST(:r AS uuid) AND content ? 'read_at'",
                          r=rid) == 0

    async def test_a_member_read_of_a_live_answer_takes_no_lock(self, wd):
        w = wd
        async with _session(w.p) as call:
            rid, _q, aid = await _answered(w, call)
            with self._held(w, rid):
                got = await asyncio.wait_for(
                    call(w.a, "GET", f"/projects/agent-runs/{rid}"), timeout=10)
            assert got.status_code == 200
            assert self._read_at(w, aid) is None

    async def test_with_the_flag_off_an_agent_read_stamps_nothing_and_waits_for_nothing(
            self, w):
        """Mutation: remove the flag guard in ``_stamp_answer_read`` and the
        read hangs on the lock (or stamps)."""
        async with _session(w.p) as call:
            rid, _q, aid = await _answered(w, call)
            with self._held(w, rid):
                got = await asyncio.wait_for(
                    call(w.agent_a, "GET", f"/projects/agent-runs/{rid}"), timeout=10)
            assert got.status_code == 200
            assert "answer" not in got.json()["run"]
            assert self._read_at(w, aid) is None

    async def test_a_second_agent_read_stamps_once_and_does_not_block(self, wd):
        w = wd
        async with _session(w.p) as call:
            rid, _q, aid = await _answered(w, call)
            assert (await call(w.agent_a, "GET", f"/projects/agent-runs/{rid}")).status_code == 200
            first = self._read_at(w, aid)
            assert first is not None
            with self._held(w, rid):
                again = await asyncio.wait_for(
                    call(w.agent_a, "GET", f"/projects/agent-runs/{rid}"), timeout=10)
            assert again.status_code == 200
            assert self._read_at(w, aid) == first

    async def test_after_a_withdraw_an_agent_read_shows_awaiting_and_no_answer(self, wd):
        w = wd
        async with _session(w.p) as call:
            rid, _q, aid = await _answered(w, call)
            assert (await _withdraw(call, w.a, rid, aid)).status_code == 200
            got = await call(w.agent_a, "GET", f"/projects/agent-runs/{rid}")
            assert got.json()["run"]["status"] == "awaiting"
            assert got.json()["run"]["answer"] is None

    async def test_a_member_comment_after_the_answer_leaves_the_window_open(self, wd):
        """Mutation: remove only ``AND x.actor_via IS NOT NULL`` from
        ``answer_changeable_sql`` and the member comment closes the window."""
        w = wd
        async with _session(w.p) as call:
            rid, _q, aid = await _answered(w, call)
            note = await _act(call, w.a, rid, type="prompt", body="One more detail")
            assert note.status_code == 201, note.text
            assert note.json()["activity"]["in_reply_to"] is None
            assert note.json()["run"]["answer"]["id"] == aid
            assert note.json()["run"]["answer"]["changeable"] is True
            done = await _withdraw(call, w.a, rid, aid)
            assert done.status_code == 200, done.text

    # ── SW-43a second review ────────────────────────────────────────────

    async def test_an_answer_that_commits_after_the_probe_is_stamped_when_returned(
            self, wd, monkeypatch):
        """Mutation: drop the ``_stamp_returned`` call in ``_read_run`` and the
        agent receives an answer with no ``read_at``."""
        w = wd
        async with _session(w.p) as call:
            run = await _start(w, call)
            rid = run["id"]
            await _act(call, w.agent_a, rid, **_select())
            state = {"n": 0, "aid": None}
            orig = agent_runs._live_unread

            async def spy(db: Any, run_id: str) -> Any:
                found = await orig(db, run_id)
                if state["n"] == 0:
                    state["n"] = 1
                    got = await _act(call, w.a, rid, type="prompt", selected=["pg"])
                    state["aid"] = got.json()["activity"]["id"]
                return found

            monkeypatch.setattr(agent_runs, "_live_unread", spy)
            read = await call(w.agent_a, "GET", f"/projects/agent-runs/{rid}")
            assert read.status_code == 200
            assert state["aid"] in [a["id"] for a in read.json()["activities"]]
            assert self._read_at(w, state["aid"]) is not None
            late = await _withdraw(call, w.a, rid, state["aid"])
            assert late.status_code == 409

    async def test_the_agent_of_another_member_stamps_nothing(self, wd):
        """Mutation: drop the member test in ``_reads_for_member`` and C's
        agent closes the window of A's answer."""
        w = wd
        conn_c = _seed_connection(w.p, w.p.org_a, w.c_email)
        agent_c = _principal(w.c_email, w.p.org_a, agent_connection=conn_c)
        async with _session(w.p) as call:
            rid, _q, aid = await _answered(w, call)
            seen = await call(agent_c, "GET", f"/projects/agent-runs/{rid}")
            assert seen.status_code == 200, seen.text
            assert self._read_at(w, aid) is None
            assert seen.json()["run"]["answer"]["changeable"] is True
            assert (await _withdraw(call, w.a, rid, aid)).status_code == 200


# ── Task 1045a: the `waiting` state (spec agent_coordination.md §3.2) ───────


def _waiting_migration() -> Path:
    """The migration that adds ``pm_agent_runs.waiting_on``, found by content
    (R1)."""
    found = [
        p for p in sorted(_MIGRATIONS.glob("*.sql"))
        if "CONSTRAINT pm_agent_runs_waiting_on" in p.read_text(encoding="utf-8")
    ]
    assert len(found) == 1, found
    return found[0]


def test_the_waiting_migration_is_expand_only_and_checks_the_five_values() -> None:
    """1045a item 2. Two nullable columns with no default, and a CHECK of the
    same five values that the gateway and the edge hold."""
    from agent_edge import tools as edge_tools
    from gateway.routes.projects import filters

    sql = _waiting_migration().read_text(encoding="utf-8")
    assert "ADD COLUMN IF NOT EXISTS waiting_until TIMESTAMPTZ" in sql
    assert "DEFAULT" not in sql.split("$checks$")[0].split("SET lock_timeout")[1]
    for value in filters.AGENT_RUN_WAITING_ON:
        assert f"'{value}'" in sql, value
    assert filters.AGENT_RUN_WAITING_ON == edge_tools.WAITING_ON
    assert filters.AGENT_RUN_WAITING_MAX_MIN == edge_tools.MAX_WAITING_MINUTES == 120
    assert agent_runs.MAX_WAITING_NOTE == edge_tools.MAX_WAITING_NOTE
    for forbidden in ("DROP ", "RENAME", "UPDATE "):
        assert forbidden not in sql.upper().split("-- ====")[-1], forbidden


@pytest.mark.parametrize(("lease", "cap"), [
    ("", 120), ("7200", 120), ("9000", 120), ("1800", 30), ("90", 1), ("30", 1),
])
def test_the_cap_of_a_wait_is_120_minutes_or_one_lease(monkeypatch, lease, cap) -> None:
    """D140.8 and SW-4 item 2. A wait never passes one lease."""
    from gateway.routes.projects import filters

    monkeypatch.setenv(filters.AGENT_RUN_LEASE_ENV, lease)
    assert filters.agent_run_waiting_cap_min() == cap


def test_waiting_comes_first_in_the_in_progress_arm() -> None:
    """§3.2: the order is waiting, then stale, then answered, then working."""
    from gateway.routes.projects import filters

    sql = filters.AGENT_RUN_STATE_SQL
    arm = sql[sql.index("WHEN 'in_progress'"):]
    order = [arm.index(f"'{s}'") for s in ("waiting", "stale", "answered", "working")]
    assert order == sorted(order), order
    assert "waiting" in filters.WORKING_STATES


def _clock_back(w: Any, rid: str, minutes: int) -> None:
    """Move the clock of run *rid* on by *minutes*: every stamp of the run
    goes back by that much, so ``now()`` reads that many minutes later."""
    with w.p.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE pm_agent_runs SET"
            " last_activity_at = last_activity_at - make_interval(mins => :m),"
            " last_agent_activity_at = last_agent_activity_at - make_interval(mins => :m),"
            " waiting_until = waiting_until - make_interval(mins => :m)"
            " WHERE id = CAST(:r AS uuid)"), {"r": rid, "m": minutes})


def _waits(w: Any, rid: str) -> tuple[Any, Any]:
    found = _rows(w.p, "SELECT waiting_on, waiting_until FROM pm_agent_runs "
                       "WHERE id = CAST(:r AS uuid)", r=rid)
    return found[0][0], found[0][1]


_CI_30 = {"on": "ci", "until_minutes": 30, "note": "PR 12 runs its checks"}


@_DB_GATE
class TestTheWaitingState:
    async def test_a_wait_on_ci_reads_waiting_at_minute_20_and_stale_at_minute_31(self, w):
        """1045a done-when 1. Mutation: drop the `waiting` arm of the state
        SQL, and minute 20 reads `stale`."""
        async with _session(w.p) as call:
            rid = (await _start(w, call))["id"]
            got = await _act(call, w.agent_a, rid, type="thought",
                             body="Waiting on CI", waiting=_CI_30)
            assert got.status_code == 201, got.text
            run = got.json()["run"]
            assert run["state"] == "waiting"
            assert run["waiting"]["on"] == "ci"
            assert run["waiting"]["note"] == "PR 12 runs its checks"
            assert run["waiting"]["until"] is not None
            assert got.json()["activity"]["content"]["waiting"] == {
                "on": "ci", "until_minutes": 30, "note": "PR 12 runs its checks"}
            on, until = _waits(w, rid)
            assert on == "ci" and until is not None
            _clock_back(w, rid, 20)
            assert await _status(call, w.a, rid) == ("in_progress", "waiting")
            # The task row carries the wait for the card, and the strip
            # counts the run as working.
            task = await call(w.a, "GET", f"/projects/tasks/{w.task_a}")
            assert task.json()["agent_state"] == "waiting"
            assert task.json()["agent_waiting"]["on"] == "ci"
            _clock_back(w, rid, 11)
            assert await _status(call, w.a, rid) == ("in_progress", "stale")
            read = await call(w.a, "GET", f"/projects/agent-runs/{rid}")
            assert read.json()["run"]["waiting"] is None
            task = await call(w.a, "GET", f"/projects/tasks/{w.task_a}")
            assert task.json()["agent_state"] == "stale"
            assert task.json()["agent_waiting"] is None

    async def test_a_thought_at_minute_10_clears_the_wait(self, w):
        """1045a done-when 2. Mutation: keep the columns on a plain activity,
        and the run still reads `waiting`."""
        async with _session(w.p) as call:
            rid = (await _start(w, call))["id"]
            await _act(call, w.agent_a, rid, type="thought", body="Wait", waiting=_CI_30)
            _clock_back(w, rid, 10)
            assert await _status(call, w.a, rid) == ("in_progress", "waiting")
            got = await _act(call, w.agent_a, rid, type="thought", body="CI passed")
            assert got.status_code == 201, got.text
            assert got.json()["run"]["state"] == "working"
            assert got.json()["run"]["waiting"] is None
            assert _waits(w, rid) == (None, None)

    async def test_a_members_prompt_clears_the_wait(self, w):
        """§3.2: a member's answer clears the wait, so the card reads
        `answered`."""
        async with _session(w.p) as call:
            rid = (await _start(w, call))["id"]
            await _act(call, w.agent_a, rid, type="thought", body="Wait",
                       waiting={"on": "member", "until_minutes": 60})
            said = await _act(call, w.a, rid, type="prompt", body="Go on")
            assert said.status_code == 201, said.text
            assert await _status(call, w.a, rid) == ("in_progress", "answered")
            assert _waits(w, rid) == (None, None)

    async def test_a_wrong_wait_gets_422_and_writes_nothing(self, w):
        """1045a done-when 3: `until_minutes` of 121 gets 422. So do 0, an
        unknown `on`, a long note and a wait on a question or an error."""
        async with _session(w.p) as call:
            rid = (await _start(w, call))["id"]
            for waiting, kind in (
                ({"on": "ci", "until_minutes": 121}, "thought"),
                ({"on": "ci", "until_minutes": 0}, "thought"),
                ({"on": "lunch", "until_minutes": 5}, "thought"),
                ({"on": "ci", "until_minutes": 5, "note": "x" * 201}, "thought"),
                ({"on": "ci", "until_minutes": 5, "extra": 1}, "thought"),
                ({"on": "ci", "until_minutes": 5}, "elicitation"),
                ({"on": "ci", "until_minutes": 5}, "error"),
            ):
                got = await _act(call, w.agent_a, rid, type=kind, body="b", waiting=waiting)
                assert got.status_code == 422, (waiting, kind, got.text)
            said = await _act(call, w.a, rid, type="prompt", body="hi",
                              waiting={"on": "ci", "until_minutes": 5})
            assert said.status_code == 422, said.text
            assert _count(w.p, "SELECT count(*) FROM pm_agent_run_activities "
                               "WHERE run_id = CAST(:r AS uuid) AND content ? 'waiting'",
                          r=rid) == 0
            assert _waits(w, rid) == (None, None)

    async def test_a_lease_below_two_hours_caps_the_wait(self, w, monkeypatch):
        """SW-4 item 2 (D140.8). With a lease of 30 minutes, a wait of 31
        minutes gets the coded 422, and a wait of 30 is taken."""
        from gateway.routes.projects import filters

        monkeypatch.setenv(filters.AGENT_RUN_LEASE_ENV, "1800")
        async with _session(w.p) as call:
            rid = (await _start(w, call))["id"]
            long = await _act(call, w.agent_a, rid, type="thought", body="b",
                              waiting={"on": "ci", "until_minutes": 31})
            assert long.status_code == 422, long.text
            assert long.json()["detail"]["error"] == agent_runs.WAITING_TOO_LONG
            ok = await _act(call, w.agent_a, rid, type="thought", body="b",
                            waiting={"on": "ci", "until_minutes": 30})
            assert ok.status_code == 201, ok.text

    async def test_the_stranded_clause_never_matches_a_waiting_run(self, w):
        """1045a done-when 4. A waiting run whose lease ended still reads
        `waiting`, and the stranded clause does not match it. The control:
        the same run with no wait matches. Mutation: put the `stale` arm
        before the `waiting` arm, and the first count is 1."""
        from gateway.routes.projects import filters

        stranded = (f"SELECT count(*) FROM pm_agent_runs r WHERE r.id = CAST(:r AS uuid)"
                    f" AND {filters.stranded_clause()}")
        async with _session(w.p) as call:
            rid = (await _start(w, call))["id"]
            await _act(call, w.agent_a, rid, type="thought", body="Wait", waiting=_CI_30)
            # The lease (2 hours) ended, and the wait has not: an edge case
            # that the cap prevents, made here by hand.
            with w.p.admin_engine.begin() as c:
                c.execute(text(
                    "UPDATE pm_agent_runs SET last_activity_at = now() - interval '3 hours',"
                    " last_agent_activity_at = now() - interval '3 hours'"
                    " WHERE id = CAST(:r AS uuid)"), {"r": rid})
            assert await _status(call, w.a, rid) == ("in_progress", "waiting")
            assert _count(w.p, stranded, r=rid) == 0
            with w.p.admin_engine.begin() as c:
                c.execute(text("UPDATE pm_agent_runs SET waiting_until = now() - interval"
                               " '1 minute' WHERE id = CAST(:r AS uuid)"), {"r": rid})
            assert await _status(call, w.a, rid) == ("in_progress", "stale")
            assert _count(w.p, stranded, r=rid) == 1
