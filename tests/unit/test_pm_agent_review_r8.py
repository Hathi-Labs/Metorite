"""WS-MCP SW-18a — a person accepts agent work before it is done (task 349).

Spec: ``project-docs/specs/agent_coordination.md`` SW-18a.

A project setting "Agent work needs review" (default off) and an optional
default reviewer. With the setting on, a SIGNED agent's move to done lands in
the review lane of the task's own status set: the first ``in_progress`` status
whose name holds "review". With no such lane the move goes to done, with a
note. A person's move is never redirected. The named reviewer, or a lead of
the project or of an ancestor, approves: the task moves to done and the open
agent run closes. Everybody else gets 403. Dark behind
``agent_review_enabled``.

Two halves.

**Database-free (always runs).** The records, the migration text, the CI skip
guard and the edge texts of the flag.

**R8 (a real database, as a NON-privileged NOBYPASSRLS role).** The edge and
the person both call the REAL gateway app.

Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_pm_agent_review_r8.py -v -rs

**The mutations (R7).** ``G`` is ``apps/services/gateway/gateway/routes/projects``.

* R1. ``G/agent_review.py``: ``redirect_for`` ignores ``user.agent``. ``test_a_person_done_is_never_redirected``.
* R2. ``G/agent_review.py``: ``redirect_for`` ignores the setting. ``test_an_agent_done_is_unchanged_with_the_setting_off``.
* R3. ``G/agent_review.py``: ``review_lane_of_set`` ignores the name. ``test_with_no_review_lane_the_done_stays_with_a_note``.
* R4. ``G/authority.py``: ``may_approve_agent_work`` returns True. ``test_a_member_who_is_not_the_reviewer_gets_403``.
* R5. ``G/agent_review.py``: ``_CLAIM_SQL`` drops the wait guard. ``test_a_second_approve_changes_nothing``.
* R6. ``G/agent_review.py``: the approve skips ``close_reviewed_run``. ``test_the_reviewer_approves_and_the_run_closes``.
* R7. ``G/tasks.py``: the PATCH skips ``redirect_for``. ``test_an_agent_done_lands_in_review``.
* R8. ``G/checks.py``: the pass skips ``redirect_for``. ``test_a_passed_check_of_an_agent_lands_in_review``.
* R9. ``G/move.py``: no ``refuse_agent_move``. ``test_an_agent_move_does_not_skip_the_review``.
* R10. ``G/agent_review.py``: ``settle_after_move`` keeps the wait. ``test_a_person_move_ends_the_wait``.
* R11. ``G/agent_review.py``: ``redirect_for`` reads no current category. ``test_a_done_task_never_opens_again_into_review``.
* R12. ``G/agent_review.py``: ``redirect_for`` skips a cancelled task. ``test_an_agent_done_of_a_cancelled_task_lands_in_review``.
* R13. ``G/move.py``: ``_plan`` skips ``refuse_agent_move_out``. ``test_an_agent_moves_no_work_into_a_new_child_project`` and ``test_a_carried_subtask_keeps_the_review``.
* R14. ``G/core.py``: ``apply_status_transition`` keeps the wait. ``test_mark_done_by_a_person_ends_the_wait``.
"""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("skill_projects", reason="skill-projects not installed")

from acb_auth import agent_oauth
from acb_common import get_settings
from agent_edge import tools
from skill_projects import manifest as m
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from tests.unit import test_mcp_edge_task_writes as t
from tests.unit import test_mcp_edge_writes as w

_DB_GATE = w._DB_GATE
live_app = t.live_app
live = t.live
redis_fake = t.redis_fake
_mcp = t._mcp
_send = t._send

_ROOT = Path(__file__).resolve().parents[2]
APPROVE = "/projects/tasks/{task_id}/agent-review"
SETTING = "/projects/nodes/{project_id}/agent-review"
MEMBER = w.MEMBER
REVIEWER = "reviewer@mcpwrites.test"
LEAD = "lead@mcpwrites.test"
OTHER = "other@mcpwrites.test"
_APP_ROLE = "acb_app_agentreview"
_APP_PW = "agentreview_scratch_pw"


@pytest.fixture
def review_on(monkeypatch):
    monkeypatch.setattr(get_settings(), "agent_review_enabled", True)


def _migration() -> str:
    """The migration of SW-18a, found by CONTENT, never by number (R1)."""
    for path in sorted((_ROOT / "infra" / "postgres").glob("*.sql")):
        body = path.read_text(encoding="utf-8")
        if "ADD COLUMN IF NOT EXISTS agent_review_requested_at" in body:
            return body
    raise AssertionError("no migration adds pm_tasks.agent_review_requested_at")


# ═══════════════════════════════════════════════════════════════════════════
# Database-free
# ═══════════════════════════════════════════════════════════════════════════


class TestTheRecords:
    def test_both_routes_are_a_persons_acts_off_the_edge(self):
        rows = {(r.method, r.path): r for r in m.MANIFEST}
        assert rows[("POST", APPROVE)].cls == "C"
        assert rows[("POST", APPROVE)].tool == "approve_agent_work"
        assert rows[("PUT", SETTING)].cls == "B"
        assert rows[("PUT", SETTING)].tool == "set_agent_review"
        assert {"approve_agent_work", "set_agent_review"} <= set(m.PLANNED)
        for pair in (("POST", APPROVE), ("PUT", SETTING)):
            assert pair not in agent_oauth.MCP_ROUTES, pair
            assert pair not in agent_oauth.MCP_WRITE_ROUTES, pair

    def test_the_review_word_and_codes(self):
        from gateway.routes.projects import agent_review, checks

        assert agent_review.REVIEW_WORD == "review"
        assert checks.WAITING_REVIEW in tools.CHECK_NOT_CLOSED
        assert agent_review.OUTCOME_REVIEW_REQUESTED == "review_requested"
        assert agent_review.OUTCOME_NO_REVIEW_LANE == "no_review_lane"

    def test_the_migration_is_expand_only(self):
        body = _migration()
        for column in ("agent_work_needs_review", "agent_review_default_reviewer",
                       "agent_reviewer", "agent_review_requested_at"):
            assert f"ADD COLUMN IF NOT EXISTS {column}" in body, column
        assert "SET lock_timeout" in body
        assert "BOOLEAN NOT NULL DEFAULT false" in body
        sql = body.split("-- ===")[-1]
        assert "DROP " not in sql.upper()
        assert sql.count("NOT VALID") == 2 and sql.count("VALIDATE CONSTRAINT") == 2

    def test_this_suite_is_named_in_the_ci_skip_guard(self):
        """An R8 suite that CI does not list skips in silence when the database
        gate is disarmed. Match the argument line, not a mention."""
        for name in ("pr-check.yml", "deploy.yml"):
            workflow = (_ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
            assert re.search(r"^\s+tests/unit/test_pm_agent_review_r8\.py \\?$",
                             workflow, re.M), name


class TestTheEdgeTexts:
    def test_the_flag_fills_the_review_sentences(self, monkeypatch):
        settings = get_settings()
        monkeypatch.setattr(settings, "agent_access_writes_enabled", True)
        monkeypatch.setattr(settings, "agent_task_checks_enabled", True)
        monkeypatch.setattr(settings, "agent_review_enabled", False)
        status_off = tools.TOOLS["set_task_status"].describe()["description"]
        check_off = tools.TOOLS["report_check"].describe()["description"]
        for described in (status_off, check_off):
            assert "review" not in described and "[[" not in described
        monkeypatch.setattr(settings, "agent_review_enabled", True)
        status_on = tools.TOOLS["set_task_status"].describe()["description"]
        check_on = tools.TOOLS["report_check"].describe()["description"]
        assert "lands in its in progress status whose name says review" in status_on
        assert "the move goes to done" in status_on
        assert "waiting_review" in check_on
        assert "[[" not in status_on and "[[" not in check_on


# ═══════════════════════════════════════════════════════════════════════════
# R8: the real gateway app
# ═══════════════════════════════════════════════════════════════════════════


def _grant(seeder: Any, project: str, who: str) -> None:
    seeder.one("INSERT INTO pm_project_grants (project_id, subject, created_by,"
               " organization_id) VALUES (CAST(:p AS uuid), :s, :a, CAST(:o AS uuid))"
               " RETURNING project_id", p=project, s=who, a=w.OWNER, o=seeder.org)


def _setting(seeder: Any, project: str, on: bool, reviewer: str | None = None) -> None:
    seeder.one("UPDATE pm_projects SET agent_work_needs_review = :on,"
               " agent_review_default_reviewer = :r WHERE id = CAST(:p AS uuid)"
               " RETURNING id", on=on, r=reviewer, p=project)


@pytest.fixture(scope="module")
def world():
    """A private promoted catalog: two tenants. The first has the agent's
    member, a reviewer, a lead and a member with no say, and four projects.
    The app role is NOSUPERUSER NOBYPASSRLS, so FORCE RLS binds each request."""
    if not w._URL:
        pytest.skip("TENANT_LADDER_DATABASE_URL unset")
    admin_url = make_url(w._URL)
    dedicated = f"{admin_url.database}_agentreview"
    w._clone_promoted_database(admin_url, dedicated)
    ded_admin_url = admin_url.set(database=dedicated)
    eng = create_engine(ded_admin_url, future=True)
    with create_engine(ded_admin_url, isolation_level="AUTOCOMMIT").connect() as c:
        c.execute(text(
            f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='{_APP_ROLE}')"
            f" THEN CREATE ROLE {_APP_ROLE} LOGIN; END IF; END $$;"))
        c.execute(text(f"ALTER ROLE {_APP_ROLE} WITH LOGIN PASSWORD '{_APP_PW}' "
                       "NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS INHERIT"))
        c.execute(text(f'GRANT CONNECT ON DATABASE "{dedicated}" TO {_APP_ROLE}'))
        c.execute(text(f"GRANT USAGE ON SCHEMA public TO {_APP_ROLE}"))
        c.execute(text("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES "
                       f"IN SCHEMA public TO {_APP_ROLE}"))
        c.execute(text(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {_APP_ROLE}"))
    org, other_org = str(uuid.uuid4()), str(uuid.uuid4())
    with eng.begin() as c:
        for oid, slug in ((org, "agentreview"), (other_org, "agentreview-b")):
            c.execute(text("INSERT INTO organization (id, slug, display_name) "
                           "VALUES (:id, :s, :s)"), {"id": oid, "s": slug})
    a = w._Seeder(eng, org)
    a.number = 100_000
    for who in (MEMBER, REVIEWER, LEAD, OTHER):
        a.member(who, ["feature:projects"])
    names = ("Team", "Plain", "Off", "Open")
    projects = {name: a.project(name, in_space=True) for name in names}
    for project in projects.values():
        space = a.root(project)
        for who in (REVIEWER, LEAD, OTHER):
            _grant(a, space, who)
        a.one("UPDATE pm_projects SET lead = :l WHERE id = CAST(:p AS uuid) RETURNING id",
              l=LEAD, p=space)
    team, plain, off, open_ = (projects[n] for n in names)
    s = SimpleNamespace(
        todo=a.status(team, "To do", "todo", 0),
        doing=a.status(team, "Doing", "in_progress", 1),
        review=a.status(team, "In Review", "in_progress", 2),
        done=a.status(team, "Done", "done", 3),
        cancelled=a.status(team, "Dropped", "cancelled", 4),
        p_todo=a.status(plain, "To do", "todo", 0),
        p_doing=a.status(plain, "Doing", "in_progress", 1),
        p_done=a.status(plain, "Done", "done", 3),
        o_doing=a.status(off, "Doing", "in_progress", 1),
        o_review=a.status(off, "Code review", "in_progress", 2),
        o_done=a.status(off, "Done", "done", 3),
        n_doing=a.status(open_, "Doing", "in_progress", 1),
        n_review=a.status(open_, "Needs review", "in_progress", 2),
        n_done=a.status(open_, "Done", "done", 3),
    )
    _setting(a, team, True, REVIEWER)
    _setting(a, plain, True, REVIEWER)
    _setting(a, off, False)
    _setting(a, open_, True)
    b = w._Seeder(eng, other_org)
    b.number = 200_000
    b_project = b.project("Elsewhere")
    b_status = b.status(b_project, "Doing", "in_progress", 1)
    b.status(b_project, "Done", "done", 3)
    b_task = b.task(b_project, b_status, "sw18a another tenant")
    b.one("UPDATE pm_tasks SET agent_review_requested_at = now(), agent_reviewer = :r"
          " WHERE id = CAST(:t AS uuid) RETURNING id", r=REVIEWER, t=b_task)
    world_ = SimpleNamespace(admin=eng, org=org, seeder=a, team=team, plain=plain, off=off,
                             open=open_, s=s, c={}, tok={}, task=a.task, b_task=b_task)
    for name, scopes in (("write", w.WRITE_SCOPES), ("read", ("projects:read",))):
        world_.c[name], world_.tok[name] = a.connection(scopes=scopes)
    world_.app_dsn = admin_url.set(
        database=dedicated, username=_APP_ROLE, password=_APP_PW,
    ).render_as_string(hide_password=False)
    yield world_

    eng.dispose()
    w._drop_database(admin_url, dedicated)


async def _person(world, email: str, method: str, path: str, *,
                  body: dict[str, Any] | None = None,
                  headers: dict[str, str] | None = None) -> httpx.Response:
    """One request to the gateway app as the member *email*, through the
    internal bearer: a person, never a signed agent."""
    from gateway.main import app as gateway_app

    raw = json.dumps(body).encode() if body is not None else b""
    sent = {**(headers or {}), "Authorization": f"Bearer {t.INTERNAL}", "X-User-Email": email}
    if body is not None:
        sent["Content-Type"] = "application/json"

    async def go() -> httpx.Response:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway_app),
                                     base_url="http://gateway.test") as client:
            return await client.request(method, path, headers=sent, content=raw)
    return await t._in_scope(world, go)


def _row(world, task: str) -> Any:
    return t._row(world, task)


def _version(world, task: str) -> str:
    return _row(world, task).updated_at.isoformat()


async def _agent_done(world, task: str, status: str) -> dict[str, Any]:
    result = await _mcp(world, "write", "set_task_status", {
        "task_id": task, "status_id": status, "expected_version": _version(world, task)})
    assert result["isError"] is False, result
    return result["structuredContent"]


async def _approve(world, email: str, task: str) -> httpx.Response:
    return await _person(world, email, "POST", APPROVE.format(task_id=task),
                         body={"decision": "approve"})


def _status_changes(world, task: str) -> int:
    with world.admin.begin() as c:
        return int(c.execute(text(
            "SELECT count(*) FROM pm_activities WHERE task_id = CAST(:t AS uuid)"
            " AND type = 'status_change'"), {"t": task}).scalar() or 0)


@pytest.mark.usefixtures("review_on")
class TestTheRedirectOnARealDatabase:
    pytestmark = _DB_GATE

    async def test_an_agent_done_lands_in_review(self, live):
        task = live.task(live.team, live.s.doing, "sw18a agent done")
        got = await _agent_done(live, task, live.s.done)
        assert got["status"]["id"] == live.s.review
        assert got["status"]["name"] == {"untrusted_text": "In Review"}
        row = _row(live, task)
        assert str(row.status_id) == live.s.review and row.completed_at is None
        assert row.agent_reviewer == REVIEWER and row.agent_review_requested_at is not None
        assert got["version"] == row.updated_at.isoformat()
        # The gateway answer of the same PATCH names the outcome.
        again = await _send(live, "PATCH", f"/projects/tasks/{task}",
                            body={"status_id": live.s.done},
                            headers={"If-Match": _version(live, task)})
        assert again.status_code == 200, again.text
        body = again.json()
        assert body["status_id"] == live.s.review
        assert body["agent_review"]["outcome"] == "review_requested"
        assert body["agent_review"]["reviewer"] == REVIEWER
        # A second done keeps the first wait.
        assert _row(live, task).agent_review_requested_at == row.agent_review_requested_at

    async def test_an_agent_done_of_a_cancelled_task_lands_in_review(self, live):
        """P1-a: a person's Cancel is not undone by an agent's done that skips
        the review."""
        task = live.task(live.team, live.s.cancelled, "sw18a cancelled")
        got = await _agent_done(live, task, live.s.done)
        assert got["status"]["id"] == live.s.review
        row = _row(live, task)
        assert str(row.status_id) == live.s.review and row.completed_at is None
        assert row.agent_reviewer == REVIEWER and row.agent_review_requested_at is not None

    async def test_a_done_task_never_opens_again_into_review(self, live):
        task = live.task(live.team, live.s.done, "sw18a done already")
        sent = await _send(live, "PATCH", f"/projects/tasks/{task}",
                           body={"status_id": live.s.done})
        assert sent.status_code == 200, sent.text
        assert sent.json()["status_id"] == live.s.done and "agent_review" not in sent.json()
        row = _row(live, task)
        assert str(row.status_id) == live.s.done and row.agent_review_requested_at is None

    async def test_an_agent_done_is_unchanged_with_the_setting_off(self, live):
        task = live.task(live.off, live.s.o_doing, "sw18a setting off")
        got = await _agent_done(live, task, live.s.o_done)
        assert got["status"]["id"] == live.s.o_done
        row = _row(live, task)
        assert str(row.status_id) == live.s.o_done and row.completed_at is not None
        assert row.agent_review_requested_at is None
        other = live.task(live.off, live.s.o_doing, "sw18a setting off again")
        sent = await _send(live, "PATCH", f"/projects/tasks/{other}",
                           body={"status_id": live.s.o_done})
        assert sent.status_code == 200 and "agent_review" not in sent.json()

    async def test_an_agent_done_is_unchanged_with_the_flag_off(self, live, monkeypatch):
        monkeypatch.setattr(get_settings(), "agent_review_enabled", False)
        task = live.task(live.team, live.s.doing, "sw18a flag off")
        got = await _agent_done(live, task, live.s.done)
        assert got["status"]["id"] == live.s.done
        assert _row(live, task).agent_review_requested_at is None
        approve = await _approve(live, REVIEWER, task)
        assert approve.status_code == 404, approve.text
        setting = await _person(live, LEAD, "PUT", SETTING.format(project_id=live.team),
                                body={"needs_review": False})
        assert setting.status_code == 404, setting.text

    async def test_with_no_review_lane_the_done_stays_with_a_note(self, live):
        task = live.task(live.plain, live.s.p_doing, "sw18a no lane")
        sent = await _send(live, "PATCH", f"/projects/tasks/{task}",
                           body={"status_id": live.s.p_done},
                           headers={"If-Match": _version(live, task)})
        assert sent.status_code == 200, sent.text
        body = sent.json()
        assert body["status_id"] == live.s.p_done
        assert body["agent_review"]["outcome"] == "no_review_lane"
        assert "no in progress status whose name says review" in body["agent_review"]["note"]
        row = _row(live, task)
        assert row.completed_at is not None and row.agent_review_requested_at is None

    async def test_a_person_done_is_never_redirected(self, live):
        task = live.task(live.team, live.s.doing, "sw18a person done")
        sent = await _person(live, MEMBER, "PATCH", f"/projects/tasks/{task}",
                             body={"status_id": live.s.done})
        assert sent.status_code == 200, sent.text
        assert sent.json()["status_id"] == live.s.done and "agent_review" not in sent.json()
        row = _row(live, task)
        assert str(row.status_id) == live.s.done and row.agent_review_requested_at is None

    async def test_a_passed_check_of_an_agent_lands_in_review(self, live, monkeypatch):
        monkeypatch.setattr(get_settings(), "agent_task_checks_enabled", True)
        task = live.task(live.team, live.s.doing, "sw18a check")
        live.seeder.one("UPDATE pm_tasks SET check_text = 'gh pr view 9'"
                        " WHERE id = CAST(:t AS uuid) RETURNING id", t=task)
        result = await _mcp(live, "write", "report_check", {
            "task_id": task, "result": "passed", "evidence": "state MERGED",
            "expected_version": _version(live, task)})
        assert result["isError"] is False, result
        got = result["structuredContent"]
        assert (got["closed"], got["not_closed"]) == (False, "waiting_review")
        row = _row(live, task)
        assert str(row.status_id) == live.s.review and row.agent_reviewer == REVIEWER
        assert row.check_last_result == "passed"


@pytest.mark.usefixtures("review_on")
class TestTheApproveOnARealDatabase:
    pytestmark = _DB_GATE

    async def test_a_member_who_is_not_the_reviewer_gets_403(self, live):
        task = live.task(live.team, live.s.doing, "sw18a not yours")
        await _agent_done(live, task, live.s.done)
        before = _row(live, task)
        refused = await _approve(live, OTHER, task)
        assert refused.status_code == 403, refused.text
        # The agent's own member is not the reviewer either.
        assert (await _approve(live, MEMBER, task)).status_code == 403
        # A signed agent never approves: the pair is off the edge.
        signed = await _send(live, "POST", APPROVE.format(task_id=task),
                             body={"decision": "approve"})
        assert signed.status_code in (401, 403), signed.text
        row = _row(live, task)
        assert str(row.status_id) == live.s.review
        assert row.agent_review_requested_at == before.agent_review_requested_at

    async def test_the_reviewer_approves_and_the_run_closes(self, live, monkeypatch):
        monkeypatch.setattr(get_settings(), "agent_runs_enabled", True)
        task = live.task(live.team, live.s.todo, "sw18a approve")
        claimed = await _mcp(live, "write", "claim_task", {
            "task_id": task, "expected_version": _version(live, task)})
        assert claimed["isError"] is False, claimed
        run_id = claimed["structuredContent"]["agent_run_id"]
        await _agent_done(live, task, live.s.done)
        approved = await _approve(live, REVIEWER, task)
        assert approved.status_code == 200, approved.text
        body = approved.json()
        assert (body["approved"], body["status_id"], body["closed_run_id"]) == (
            True, live.s.done, run_id)
        row = _row(live, task)
        assert str(row.status_id) == live.s.done and row.completed_at is not None
        assert row.agent_reviewer is None and row.agent_review_requested_at is None
        with live.admin.begin() as c:
            run = c.execute(text("SELECT status, ended_at FROM pm_agent_runs"
                                 " WHERE id = CAST(:r AS uuid)"), {"r": run_id}).one()
            line = c.execute(text(
                "SELECT content FROM pm_agent_run_activities WHERE run_id = CAST(:r AS uuid)"
                " ORDER BY seq DESC LIMIT 1"), {"r": run_id}).scalar()
        assert run.status == "completed" and run.ended_at is not None
        assert line["server_close"] == "review" and line["system"] is True

    async def test_a_lead_of_an_ancestor_approves_a_task_with_no_reviewer(self, live):
        task = live.task(live.open, live.s.n_doing, "sw18a lead")
        got = await _agent_done(live, task, live.s.n_done)
        assert got["status"]["id"] == live.s.n_review
        row = _row(live, task)
        assert row.agent_reviewer is None and row.agent_review_requested_at is not None
        # No reviewer is named, so the reviewer of another project has no say.
        assert (await _approve(live, REVIEWER, task)).status_code == 403
        approved = await _approve(live, LEAD, task)
        assert approved.status_code == 200, approved.text
        assert approved.json()["closed_run_id"] is None
        assert str(_row(live, task).status_id) == live.s.n_done

    async def test_a_second_approve_changes_nothing(self, live):
        task = live.task(live.team, live.s.doing, "sw18a twice")
        await _agent_done(live, task, live.s.done)
        first = await _approve(live, REVIEWER, task)
        assert first.status_code == 200, first.text
        moves = _status_changes(live, task)
        version = _version(live, task)
        second = await _approve(live, REVIEWER, task)
        assert second.status_code == 409 and second.json()["detail"] == "not_waiting"
        assert _status_changes(live, task) == moves and _version(live, task) == version

    async def test_a_task_of_another_tenant_reads_as_a_task_that_does_not_exist(self, live):
        refused = await _approve(live, REVIEWER, live.b_task)
        assert refused.status_code == 404, refused.text
        with live.admin.begin() as c:
            still = c.execute(text("SELECT agent_review_requested_at FROM pm_tasks"
                                   " WHERE id = CAST(:t AS uuid)"), {"t": live.b_task}).scalar()
        assert still is not None

    async def test_a_person_move_ends_the_wait(self, live):
        task = live.task(live.team, live.s.doing, "sw18a person move")
        await _agent_done(live, task, live.s.done)
        sent = await _person(live, MEMBER, "PATCH", f"/projects/tasks/{task}",
                             body={"status_id": live.s.doing})
        assert sent.status_code == 200, sent.text
        row = _row(live, task)
        assert row.agent_review_requested_at is None and row.agent_reviewer is None
        refused = await _approve(live, REVIEWER, task)
        assert refused.status_code == 409 and refused.json()["detail"] == "not_waiting"

    async def test_an_agent_move_does_not_skip_the_review(self, live):
        """The member made both tasks, so the move rule of D-OP-14 passes and
        the review rule is the one that refuses."""
        async def made(title: str) -> str:
            result = await _mcp(live, "write", "create_task",
                                {"project_id": live.team, "title": title})
            assert result["isError"] is False, result
            return result["structuredContent"]["task"]["id"]

        waiting = await made("sw18a move waiting")
        await _agent_done(live, waiting, live.s.done)
        moved = await _send(live, "POST", "/projects/tasks/move", body={
            "task_ids": [waiting], "destination_project_id": live.off, "accepted_drops": []})
        assert moved.status_code == 403, moved.text
        assert moved.json()["detail"]["error"] == "review_waiting"
        fresh = await made("sw18a move out")
        out = await _send(live, "POST", "/projects/tasks/move", body={
            "task_ids": [fresh], "destination_project_id": live.off, "accepted_drops": [],
            "status_map": {live.s.todo: live.s.o_done}})
        assert out.status_code == 403, out.text
        assert out.json()["detail"]["error"] == "review_project"
        assert str(_row(live, fresh).project_id) == live.team
        assert str(_row(live, waiting).project_id) == live.team
        # Into a reviewed project from one with the setting off: no landing in done.
        made_off = await _mcp(live, "write", "create_task",
                              {"project_id": live.off, "title": "sw18a move in"})
        assert made_off["isError"] is False, made_off
        incoming = made_off["structuredContent"]["task"]["id"]
        landed = await _send(live, "POST", "/projects/tasks/move", body={
            "task_ids": [incoming], "destination_project_id": live.team, "accepted_drops": [],
            "status_map": {live.s.o_doing: live.s.done}})
        assert landed.status_code == 403, landed.text
        assert landed.json()["detail"]["error"] == "review_needed"
        assert str(_row(live, incoming).project_id) == live.off

    async def test_an_agent_moves_no_work_into_a_new_child_project(self, live):
        """Gap (a): an agent makes a child project, whose setting starts off,
        and moves its own task there. The move out of the reviewed project is
        a person's act, in the preview and in the apply."""
        child = await _send(live, "POST", "/projects/nodes", body={
            "name": "sw18a child", "parent_project_id": live.team})
        assert child.status_code in (200, 201), child.text
        child_id = child.json()["id"]
        made = await _mcp(live, "write", "create_task",
                          {"project_id": live.team, "title": "sw18a to the child"})
        assert made["isError"] is False, made
        task = made["structuredContent"]["task"]["id"]
        body = {"task_ids": [task], "destination_project_id": child_id, "accepted_drops": []}
        preview = await _send(live, "POST", "/projects/tasks/move/preview", body=body)
        assert preview.status_code == 403, preview.text
        assert preview.json()["detail"]["error"] == "review_project"
        moved = await _send(live, "POST", "/projects/tasks/move", body=body)
        assert moved.status_code == 403, moved.text
        assert moved.json()["detail"]["error"] == "review_project"
        assert str(_row(live, task).project_id) == live.team

    async def test_a_carried_subtask_keeps_the_review(self, live):
        """Gap (b): the parent sits in a project with the setting off, and its
        subtask in a reviewed project. A move that carries the subtask out is
        refused, for a wait and for the project alike."""
        seeder = live.seeder
        off2 = seeder.project("sw18a off two", in_space=True)
        for who in (REVIEWER, LEAD, OTHER):
            _grant(seeder, seeder.root(off2), who)
        seeder.status(off2, "Doing", "in_progress", 1)
        seeder.status(off2, "Done", "done", 3)
        parent = live.task(live.off, live.s.o_doing, "sw18a parent")
        seeder.one("UPDATE pm_tasks SET created_by = :m WHERE id = CAST(:t AS uuid)"
                   " RETURNING id", m=MEMBER, t=parent)
        child = live.task(live.team, live.s.review, "sw18a carried child")
        seeder.one("UPDATE pm_tasks SET parent_task_id = CAST(:p AS uuid),"
                   " agent_review_requested_at = now(), agent_reviewer = :r"
                   " WHERE id = CAST(:t AS uuid) RETURNING id", p=parent, r=REVIEWER, t=child)
        body = {"task_ids": [parent], "destination_project_id": off2, "accepted_drops": [],
                "include_subtasks": True}
        waiting = await _send(live, "POST", "/projects/tasks/move", body=body)
        assert waiting.status_code == 403, waiting.text
        assert waiting.json()["detail"]["error"] == "review_waiting"
        preview = await _send(live, "POST", "/projects/tasks/move/preview", body=body)
        assert preview.status_code == 403 and preview.json()["detail"]["error"] == "review_waiting"
        seeder.one("UPDATE pm_tasks SET agent_review_requested_at = NULL, agent_reviewer = NULL"
                   " WHERE id = CAST(:t AS uuid) RETURNING id", t=child)
        out = await _send(live, "POST", "/projects/tasks/move", body=body)
        assert out.status_code == 403, out.text
        assert out.json()["detail"]["error"] == "review_project"
        assert str(_row(live, parent).project_id) == live.off
        assert str(_row(live, child).project_id) == live.team
        # Without the subtask, the parent leaves a project with the setting off.
        alone = await _send(live, "POST", "/projects/tasks/move", body={
            **body, "include_subtasks": False})
        assert alone.status_code == 200, alone.text
        assert str(_row(live, parent).project_id) == off2

    async def test_mark_done_by_a_person_ends_the_wait(self, live):
        """P2: every status move ends a wait, Mark done too
        (``core.apply_status_transition``)."""
        task = live.task(live.team, live.s.doing, "sw18a mark done")
        await _agent_done(live, task, live.s.done)
        assert _row(live, task).agent_review_requested_at is not None
        done = await _person(live, MEMBER, "POST", f"/projects/tasks/{task}/complete")
        assert done.status_code == 200, done.text
        row = _row(live, task)
        assert str(row.status_id) == live.s.done
        assert row.agent_review_requested_at is None and row.agent_reviewer is None
        refused = await _approve(live, REVIEWER, task)
        assert refused.status_code == 409 and refused.json()["detail"] == "not_waiting"


@pytest.mark.usefixtures("review_on")
class TestTheSettingOnARealDatabase:
    pytestmark = _DB_GATE

    async def test_a_lead_turns_it_on_and_the_answer_names_the_lane(self, live):
        project = live.seeder.project("sw18a setting", in_space=True)
        space = live.seeder.root(project)
        for who in (REVIEWER, LEAD, OTHER):
            _grant(live.seeder, space, who)
        live.seeder.one("UPDATE pm_projects SET lead = :l WHERE id = CAST(:p AS uuid)"
                        " RETURNING id", l=LEAD, p=space)
        path = SETTING.format(project_id=project)
        refused = await _person(live, OTHER, "PUT", path, body={"needs_review": True})
        assert refused.status_code == 403, refused.text
        signed = await _send(live, "PUT", path, body={"needs_review": True})
        assert signed.status_code in (401, 403), signed.text
        stranger = await _person(live, LEAD, "PUT", path, body={
            "needs_review": True, "default_reviewer": "nobody@mcpwrites.test"})
        assert stranger.status_code == 422, stranger.text
        extra = await _person(live, LEAD, "PUT", path, body={"needs_review": True, "x": 1})
        assert extra.status_code == 422, extra.text
        on = await _person(live, LEAD, "PUT", path, body={
            "needs_review": True, "default_reviewer": " Reviewer@MCPWrites.test "})
        assert on.status_code == 200, on.text
        body = on.json()
        assert (body["needs_review"], body["default_reviewer"], body["review_status"]) == (
            True, REVIEWER, None)
        assert "no in progress status whose name says review" in body["note"]
        lane = live.seeder.status(project, "Review", "in_progress", 2)
        off = await _person(live, LEAD, "PUT", path, body={"needs_review": True})
        assert off.status_code == 200, off.text
        assert off.json()["review_status"] == {"id": lane, "name": "Review"}
        assert off.json()["default_reviewer"] is None and off.json()["note"] is None
        with live.admin.begin() as c:
            row = c.execute(text("SELECT agent_work_needs_review, agent_review_default_reviewer"
                                 " FROM pm_projects WHERE id = CAST(:p AS uuid)"),
                            {"p": project}).one()
        assert (row.agent_work_needs_review, row.agent_review_default_reviewer) == (True, None)

    def test_the_database_refuses_a_reviewer_with_no_wait(self, world):
        task = world.task(world.team, world.s.doing, "sw18a schema")
        app = create_engine(world.app_dsn, future=True)
        try:
            with pytest.raises(IntegrityError), app.begin() as c:
                c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": world.org})
                c.execute(text("UPDATE pm_tasks SET agent_reviewer = 'x@y.z'"
                               " WHERE id = CAST(:t AS uuid)"), {"t": task})
            with pytest.raises(IntegrityError), app.begin() as c:
                c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": world.org})
                c.execute(text("UPDATE pm_projects SET agent_review_default_reviewer = '  '"
                               " WHERE id = CAST(:p AS uuid)"), {"p": world.team})
        finally:
            app.dispose()
