"""WS-55 EA-6b and EA-6f — ``claim_task``, and an agent may move a task to done.

Spec: ``project-docs/specs/external_agent_access.md`` §6.1 (EA3-32 and
EA3-34), §9.2 (the rules of ``claim_task`` and the Done rule), §9.5 and
§15 EA-6b and EA-6f, the acceptance C1 to C10 and F1 to F5.

Two halves, as in ``test_mcp_edge_writes.py``, whose helpers this file reuses.

**Database-free (always runs).** The edge app runs in process with a fake
token store and a fake gateway. It proves the tool record, the POST that
``claim_task`` sends, the re-read, and the fixed text of each answer.

**R8 (a real database, as a NON-privileged role, on a phase-4-promoted
catalog of its own).** The edge calls the REAL gateway app through
``httpx.ASGITransport`` (EA3-20). It proves C1 to C9 and F1 to F5. C10 is the
four fences of the manifest row: ``test_mcp_edge_coverage.py``,
``test_projects_chat_coverage.py``, ``test_projects_field_parity.py`` and
``test_projects_ui_actions.py``.

Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_mcp_edge_claim.py -v -rs

**The mutations (R7).** Each one turned the named test red. ``E`` is
``apps/services/agent_edge/agent_edge`` and ``G`` is
``apps/services/gateway/gateway/routes/projects``.

* M1. ``G/tasks.py`` ``claim_task``: delete the other assignees before the insert. ``test_c1_the_member_is_added_and_every_other_assignee_stays``.
* M2. ``G/tasks.py`` ``claim_task``: read the lane from ``task.root_project_id``, not ``status_owner_id``. ``test_c2_a_subproject_task_takes_the_lowest_lane_of_its_own_set``.
* M3. ``G/tasks.py`` ``claim_task``: move a task that is already ``in_progress``. ``test_c3_an_in_progress_task_does_not_move``.
* M4. ``G/tasks.py`` ``claim_task``: drop the closed check. ``test_c4_a_task_that_cannot_be_claimed_is_refused_and_nothing_is_written[done]`` and ``[cancelled]``.
* M5. ``G/tasks.py`` ``claim_task``: drop the triage check. ``test_c4_...[triage]``.
* M6. ``G/tasks.py`` ``claim_task``: drop the archive check. ``test_c4_...[archived]``.
* M7. ``G/tasks.py`` ``claim_task``: swallow the 422 of a set with no ``in_progress`` lane and answer 200. ``test_c5_a_set_with_no_started_lane_refuses_and_rolls_back_the_assignee``.
* M8. ``G/tasks.py`` ``claim_task``: no ``require_precondition``. ``test_c6_a_stale_version_gives_the_stale_text``.
* M9. ``G/tasks.py`` ``claim_task``: write the ``assignment`` row when nothing was added. ``test_c7_a_second_claim_writes_no_activity_row``.
* M10. ``G/core.py`` ``record_activity``: no ``meta.via`` from ``ACTOR_VIA``. ``test_c8_both_rows_carry_the_via_stamp``.
* M11. ``G/tasks.py`` ``claim_task``: ``notify`` with ``actor_id="system"``. ``test_c9_the_member_gets_no_notification_and_no_agent_run_starts``.
* M12. ``G/tasks.py`` ``claim_task``: emit the added set as ``agent:<slug>``. The same test.
* M13. The claim pair leaves ``MCP_WRITE_ROUTES``. ``test_mcp_edge_coverage.py::test_the_write_pairs_are_those_of_ea_6a_ea_6d_and_ea_12a`` and every R8 claim test (branch 0 answers 401).
* M14. The manifest row leaves ``manifest.py``. ``test_projects_chat_coverage.py`` (the route has no row) and ``test_mcp_edge_coverage.py::test_rule_2_each_pair_is_class_a_or_a_named_exception``.
* M15. ``E/tools.py`` ``_claim_task``: ``current = written`` (no re-read). ``test_it_posts_the_claim_with_if_match_and_reads_the_task_again``.
* M16. ``E/tools.py`` ``_write``: no ``coded`` map. ``test_each_coded_refusal_has_its_own_text``.
* M17. ``G/tasks.py`` ``AGENT_MOVE_REFUSED = CLOSING_CATEGORIES`` (the EA-6d rule). ``test_f1_an_agent_moves_a_task_to_done`` and ``test_f3_an_agents_done_leaves_the_open_subtasks_open``.
* M18. ``E/tools.py`` ``AGENT_MOVE_REFUSED = CLOSING_CATEGORIES``. ``test_f1_an_agent_moves_a_task_to_done``.
* M19. ``G/tasks.py`` ``AGENT_MOVE_REFUSED = frozenset()``. ``test_f2_cancel_is_refused_at_the_edge_and_at_the_gateway``.
* M20. ``G/tasks.py``: the narrowing lets ``include_subtasks`` through. ``test_f3_an_agents_done_leaves_the_open_subtasks_open``.
* M21. ``G/recurrence.py`` ``spawn_successor``: no ``recurrence_spawned_at`` guard. ``test_f4_an_agents_done_on_a_recurring_task_makes_one_successor``.
* M22. ``E/tools.py`` ``_create``: the create checks ``AGENT_MOVE_REFUSED``. ``test_f5_create_task_with_a_done_status_is_still_refused``.
"""
from __future__ import annotations

import json
import os
import uuid
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

pytest.importorskip("sqlalchemy")

from acb_common import get_settings
from agent_edge import gateway_client, tools
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests.unit import test_mcp_edge_task_writes as tw
from tests.unit import test_mcp_edge_writes as w
from tests.unit._tenant_ladder import apply_ladder

_DB_GATE = w._DB_GATE
_uuid = w._uuid
GOOD, KEY, VIA = w.GOOD, w.KEY, w.VIA
TASK, PROJECT = w.TASK, w.PROJECT
S_TODO, S_DOING = w.S_TODO, w.S_DOING
V1, V2, V3 = w.V1, w.V2, tw.V3
MEMBER = w.MEMBER
LEAD = "lead@mcpwrites.test"
STALE = "The task changed. Read it again."
CANCEL_TEXT = "Cancelling a task stays a human step."
CREATE_TEXT = "A new task cannot start in a done or cancelled status."
CLAIM_PATH = f"/projects/tasks/{TASK}/claim"

_post, _rpc, _call, _tool, _text = w._post, w._rpc, w._call, w._tool, w._text


# ═══════════════════════════════════════════════════════════════════════════
# The edge, database-free
# ═══════════════════════════════════════════════════════════════════════════


class _Gateway(tw._Gateway):
    """The EA-6d fake gateway, with the claim route."""

    def _answer(self, r: SimpleNamespace) -> tuple[int, Any]:
        if (r.method, r.path) in self.answers:
            return self.answers[(r.method, r.path)]
        if r.method == "POST" and r.path == CLAIM_PATH:
            self.task.update({"status_id": S_DOING, "assignees": [LEAD, MEMBER]})
            answer = {**self.task, "updated_at": V2, "moved": True, "assigned": True,
                      "clickup_id": "CU-1"}
            self.task["updated_at"] = V3
            return 200, answer
        return super()._answer(r)


@pytest.fixture
def edge(monkeypatch, redis_fake) -> SimpleNamespace:
    """``test_mcp_edge_task_writes.edge`` with this file's fake gateway."""
    found: dict[str, Any] = {GOOD: w._lookup()}

    async def lookup(raw: str, *, kind: str = "access"):
        return found.get(raw)

    from acb_auth import agent_tokens

    monkeypatch.setattr(agent_tokens, "lookup_token", lookup)
    settings = get_settings()
    for name, value in (
            ("agent_access_enabled", True), ("agent_access_writes_enabled", True),
            ("agent_access_orgs", "*"), ("agent_edge_signing_key", KEY),
            ("agent_edge_signing_key_previous", ""), ("agent_issuer", w.ISSUER),
            ("agent_resource", w.RESOURCE), ("agent_edge_gateway_url", "http://gw.test")):
        monkeypatch.setattr(settings, name, value)
    fake = _Gateway()
    with gateway_client.use_transport(httpx.ASGITransport(app=fake)):
        yield SimpleNamespace(gateway=fake, tokens=found)


redis_fake = tw.redis_fake


def _claim_args(version: str = V1) -> dict[str, Any]:
    return {"task_id": TASK, "expected_version": version}


class TestClaimTaskAtTheEdge:
    """§9.2 and §9.5 for ``claim_task``, database-free."""

    async def test_the_tool_record(self, edge):
        listed = {t["name"]: t for t in
                  (await _post(_rpc("tools/list"))).json()["result"]["tools"]}
        claim = listed["claim_task"]
        assert claim["annotations"] == {
            "title": "Claim a task", "readOnlyHint": False, "destructiveHint": False,
            "idempotentHint": True, "openWorldHint": False}
        description = tools.CLAIM_TASK.description
        assert "Call it first, when you start work" in description
        assert "expected_version" in description and "untrusted_text" in description
        assert tools.CLAIM_TASK.required_scope == "projects:write"

    async def test_it_posts_the_claim_with_if_match_and_reads_the_task_again(self, edge):
        result = await _tool("claim_task", _claim_args())
        assert result["isError"] is False, result
        [post] = edge.gateway.writes()
        assert (post.method, post.path, post.query) == ("POST", CLAIM_PATH, "")
        assert json.loads(post.body) == {}
        assert post.headers["if-match"] == V1
        body = result["structuredContent"]
        assert set(body) == {"task", "moved"}
        assert body["moved"] is True
        task = body["task"]
        assert tuple(task) == tools.GET_TASK_KEYS
        # The version of the re-read, not the V2 of the POST answer.
        assert task["version"] == V3
        assert task["assignees"] == [LEAD, MEMBER]
        assert task["status"] == {"id": S_DOING, "name": {"untrusted_text": "Doing"}}
        assert "clickup_id" not in json.dumps(body)

    async def test_a_failed_re_read_gives_the_claim_answer(self, edge):
        edge.gateway.answers[("GET", f"/projects/tasks/{TASK}")] = (503, {})
        result = await _tool("claim_task", _claim_args())
        assert result["isError"] is False, result
        assert result["structuredContent"]["task"]["version"] == V2

    @pytest.mark.parametrize("code", sorted(tools.CLAIM_TEXTS))
    async def test_each_coded_refusal_has_its_own_text(self, edge, code):
        edge.gateway.answers[("POST", CLAIM_PATH)] = (
            422, {"detail": {"error": code, "message": "SECRET"}})
        result = await _tool("claim_task", _claim_args())
        assert _text(result) == tools.CLAIM_TEXTS[code]
        assert "SECRET" not in json.dumps(result)

    @pytest.mark.parametrize("status,detail,expected", [
        (412, {"detail": "SECRET"}, STALE),
        (422, {"detail": {"error": "something_else", "message": "SECRET"}},
         tools.TEXT_BAD_REQUEST),
        (422, {"detail": "SECRET"}, tools.TEXT_BAD_REQUEST),
        (404, {"detail": "SECRET"}, "Task not found."),
        (403, {"detail": "SECRET"}, "This member cannot change that."),
        (429, {"detail": "SECRET"}, "Too many changes. Wait a few minutes."),
        (503, {"detail": "SECRET"}, tools.TEXT_BUSY),
    ])
    async def test_the_answer_table_of_the_claim(self, edge, status, detail, expected):
        edge.gateway.answers[("POST", CLAIM_PATH)] = (status, detail)
        result = await _tool("claim_task", _claim_args())
        assert _text(result) == expected
        assert "SECRET" not in json.dumps(result)

    async def test_with_the_writes_flag_off_the_tool_is_hidden_and_sends_nothing(
            self, edge, monkeypatch):
        monkeypatch.setattr(get_settings(), "agent_access_writes_enabled", False)
        listed = (await _post(_rpc("tools/list"))).json()["result"]["tools"]
        assert "claim_task" not in {t["name"] for t in listed}
        assert _text(await _tool("claim_task", _claim_args())) == (
            "Changes are turned off for agents in this organization.")
        assert edge.gateway.requests == []

    async def test_a_read_connection_gets_the_step_up_403(self, edge):
        edge.tokens[GOOD] = w._lookup(scope="projects:read", scopes=("projects:read",))
        r = await _post(_call("claim_task", _claim_args()))
        assert r.status_code == 403
        assert r.headers["www-authenticate"] == w.CHALLENGE_403_WRITE
        assert edge.gateway.requests == []

    def test_the_claim_codes_mirror_the_gateway(self):
        from gateway.routes.projects import tasks as gateway_tasks

        assert set(tools.CLAIM_TEXTS) == gateway_tasks.CLAIM_REFUSALS


# ═══════════════════════════════════════════════════════════════════════════
# R8: the real gateway app on a promoted catalog, as a NON-privileged role
# ═══════════════════════════════════════════════════════════════════════════

_APP_ROLE = "acb_app_mcpclaim"
_APP_PW = "mcpclaim_scratch_pw"


@pytest.fixture(scope="module")
def world():
    """A private promoted catalog: one tenant, one member, three projects.

    ``team`` is a root with every category. ``sub`` is a subproject of
    ``team`` that owns its own set, with two ``in_progress`` lanes. ``bare``
    is a root with no ``in_progress`` lane.
    """
    if not w._URL:
        pytest.skip("TENANT_LADDER_DATABASE_URL unset")
    admin_url = make_url(w._URL)
    dedicated = f"{admin_url.database}_mcpclaim"
    w._drop_database(admin_url, dedicated)
    with create_engine(admin_url, isolation_level="AUTOCOMMIT").connect() as c:
        c.execute(text(f'CREATE DATABASE "{dedicated}"'))
    ded_admin_url = admin_url.set(database=dedicated)
    eng = create_engine(ded_admin_url, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    for phase in ("01_add_columns.sql", "02_backfill.sql", "03_constraints.sql",
                  "04_policies.sql"):
        with eng.begin() as conn, open(os.path.join(w._GENERATED, phase),
                                       encoding="utf-8") as fh:
            w._exec_sql_file(conn, fh.read())
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

    org = str(uuid.uuid4())
    with eng.begin() as c:
        c.execute(text("INSERT INTO organization (id, slug, display_name) "
                       "VALUES (:id, 'mcpclaim', 'mcpclaim')"), {"id": org})
    a = w._Seeder(eng, org)
    a.number = 200_000
    a.member(MEMBER, ["feature:projects"])
    team, bare = a.project("Team"), a.project("Bare")
    sub = str(a.one(
        "INSERT INTO pm_projects (name, status, source, created_by, organization_id,"
        " timezone, owns_statuses, parent_project_id) VALUES ('Sub', 'active', 'manual',"
        " :a, CAST(:o AS uuid), 'Asia/Kolkata', true, CAST(:p AS uuid)) RETURNING id",
        a=w.OWNER, o=org, p=team))
    s = SimpleNamespace(
        backlog=a.status(team, "Backlog", "backlog", 0),
        todo=a.status(team, "To do", "todo", 1),
        doing=a.status(team, "Doing", "in_progress", 2),
        review=a.status(team, "In review", "in_progress", 3),
        done=a.status(team, "Done", "done", 4),
        cancelled=a.status(team, "Dropped", "cancelled", 5),
        triage=a.status(team, "Triage", "triage", 6),
        # The subproject's own set. Its lowest in_progress lane is at 2, and
        # a later one sits at 5, so the lane comes from the position.
        sub_todo=a.status(sub, "Sub to do", "todo", 0),
        sub_late=a.status(sub, "Sub shipping", "in_progress", 5),
        sub_first=a.status(sub, "Sub building", "in_progress", 2),
        sub_done=a.status(sub, "Sub done", "done", 6),
        bare_todo=a.status(bare, "To do", "todo", 0),
        bare_done=a.status(bare, "Done", "done", 1),
    )
    world_ = SimpleNamespace(admin=eng, org=org, seeder=a, team=team, sub=sub, bare=bare,
                             s=s, c={}, tok={})
    for name, scopes in (("write", w.WRITE_SCOPES),):
        world_.c[name], world_.tok[name] = a.connection(scopes=scopes)
    world_.app_dsn = admin_url.set(
        database=dedicated, username=_APP_ROLE, password=_APP_PW,
    ).render_as_string(hide_password=False)
    yield world_

    eng.dispose()
    w._drop_database(admin_url, dedicated)


live_app = tw.live_app
live = tw.live
_send = tw._send
_row = tw._row


async def _mcp(world, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return await tw._mcp(world, "write", tool, arguments)


def _task(world, project: str, status: str, title: str, *, parent: str | None = None,
          root: str | None = None) -> str:
    world.seeder.number += 1
    return str(world.seeder.one(
        "INSERT INTO pm_tasks (title, project_id, root_project_id, status_id,"
        " created_by, organization_id, task_number, parent_task_id) VALUES (:t,"
        " CAST(:p AS uuid), CAST(:r AS uuid), CAST(:s AS uuid), :a, CAST(:o AS uuid),"
        " :n, CAST(:parent AS uuid)) RETURNING id",
        t=title, p=project, r=root or project, s=status, a=w.OWNER, o=world.org,
        n=world.seeder.number, parent=parent))


def _assign(world, task: str, who: str) -> None:
    world.seeder.one(
        "INSERT INTO pm_task_assignees (task_id, assignee, assigned_by)"
        " VALUES (CAST(:t AS uuid), :w, :a) RETURNING task_id",
        t=task, w=who, a=w.OWNER)


def _assignees(world, task: str) -> list[str]:
    with world.admin.begin() as c:
        return sorted(r[0] for r in c.execute(text(
            "SELECT assignee FROM pm_task_assignees WHERE task_id = CAST(:t AS uuid)"),
            {"t": task}))


def _activities(world, task: str, kind: str | None = None) -> list[Any]:
    sql = "SELECT * FROM pm_activities WHERE task_id = CAST(:t AS uuid)"
    if kind is not None:
        sql += " AND type = :k"
    with world.admin.begin() as c:
        return list(c.execute(text(sql + " ORDER BY created_at, id"),
                              {"t": task, "k": kind}))


async def _version(world, task: str) -> str:
    r = await _send(world, "GET", f"/projects/tasks/{task}")
    assert r.status_code == 200, r.text
    return r.json()["updated_at"]


async def _claim(world, task: str, version: str | None = None) -> dict[str, Any]:
    return await _mcp(world, "claim_task", {
        "task_id": task, "expected_version": version or await _version(world, task)})


class TestClaimOnARealDatabase:
    """C1 to C9 (§15 EA-6b), through the edge and the real gateway app."""

    pytestmark = _DB_GATE

    async def test_c1_the_member_is_added_and_every_other_assignee_stays(self, live):
        task = _task(live, live.team, live.s.todo, "c1")
        _assign(live, task, LEAD)
        result = await _claim(live, task)
        assert result["isError"] is False, result
        body = result["structuredContent"]
        assert body["moved"] is True
        assert sorted(body["task"]["assignees"]) == [LEAD, MEMBER]
        assert _assignees(live, task) == [LEAD, MEMBER]
        assert str(_row(live, task).status_id) == live.s.doing

    async def test_a_backlog_task_moves_too(self, live):
        task = _task(live, live.team, live.s.backlog, "backlog")
        result = await _claim(live, task)
        assert result["structuredContent"]["moved"] is True
        assert str(_row(live, task).status_id) == live.s.doing

    async def test_c2_a_subproject_task_takes_the_lowest_lane_of_its_own_set(self, live):
        task = _task(live, live.sub, live.s.sub_todo, "c2", root=live.team)
        result = await _claim(live, task)
        assert result["isError"] is False, result
        assert str(_row(live, task).status_id) == live.s.sub_first
        assert result["structuredContent"]["task"]["status"]["name"] == {
            "untrusted_text": "Sub building"}

    async def test_c3_an_in_progress_task_does_not_move(self, live):
        task = _task(live, live.team, live.s.review, "c3")
        result = await _claim(live, task)
        assert result["isError"] is False, result
        assert result["structuredContent"]["moved"] is False
        assert str(_row(live, task).status_id) == live.s.review
        assert _assignees(live, task) == [MEMBER]
        assert len(_activities(live, task, "assignment")) == 1
        assert _activities(live, task, "status_change") == []

    @pytest.mark.parametrize("case,code", [
        ("done", "claim_closed"), ("cancelled", "claim_closed"),
        ("triage", "claim_triage"), ("archived", "claim_archived")])
    async def test_c4_a_task_that_cannot_be_claimed_is_refused_and_nothing_is_written(
            self, live, case, code):
        status = live.s.todo if case == "archived" else getattr(live.s, case)
        task = _task(live, live.team, status, f"c4 {case}")
        if case == "archived":
            live.seeder.one("UPDATE pm_tasks SET archived_at = now() WHERE id ="
                            " CAST(:t AS uuid) RETURNING id", t=task)
        before = _row(live, task)
        result = await _claim(live, task)
        assert _text(result) == tools.CLAIM_TEXTS[code]
        after = _row(live, task)
        assert (str(after.status_id), after.updated_at) == (status, before.updated_at)
        assert _assignees(live, task) == []
        assert _activities(live, task) == []
        # The gateway's own answer, signed, as the edge sends it.
        path = f"/projects/tasks/{task}/claim"
        r = await _send(live, "POST", path, body={})
        assert r.status_code == 422, r.text
        assert r.json()["detail"]["error"] == code

    async def test_c5_a_set_with_no_started_lane_refuses_and_rolls_back_the_assignee(
            self, live):
        task = _task(live, live.bare, live.s.bare_todo, "c5")
        result = await _claim(live, task)
        assert _text(result) == tools.CLAIM_TEXTS["claim_no_started_status"]
        assert _assignees(live, task) == []
        assert _activities(live, task) == []
        assert str(_row(live, task).status_id) == live.s.bare_todo

    async def test_c6_a_stale_version_gives_the_stale_text(self, live):
        task = _task(live, live.team, live.s.todo, "c6")
        stale = await _version(live, task)
        live.seeder.one("UPDATE pm_tasks SET title = 'c6 moved on' WHERE id ="
                        " CAST(:t AS uuid) RETURNING id", t=task)
        result = await _claim(live, task, stale)
        assert result == {"content": [{"type": "text", "text": STALE}], "isError": True}
        assert _assignees(live, task) == []
        assert str(_row(live, task).status_id) == live.s.todo

    async def test_c7_a_second_claim_writes_no_activity_row(self, live):
        task = _task(live, live.team, live.s.todo, "c7")
        first = await _claim(live, task)
        rows = len(_activities(live, task))
        again = await _claim(live, task, first["structuredContent"]["task"]["version"])
        assert again["isError"] is False, again
        assert again["structuredContent"]["moved"] is False
        assert len(_activities(live, task)) == rows
        assert _assignees(live, task) == [MEMBER]

    async def test_c8_both_rows_carry_the_via_stamp(self, live):
        task = _task(live, live.team, live.s.todo, "c8")
        await _claim(live, task)
        [assignment] = _activities(live, task, "assignment")
        [move] = _activities(live, task, "status_change")
        assert assignment.meta["via"] == VIA and move.meta["via"] == VIA
        assert assignment.meta["added"] == [MEMBER]
        assert assignment.created_by == MEMBER and move.created_by == MEMBER

    async def test_c9_the_member_gets_no_notification_and_no_agent_run_starts(
            self, live, monkeypatch):
        from gateway.routes.projects import agent_dispatch
        from gateway.routes.projects import tasks as gateway_tasks

        emitted: list[tuple[str, dict[str, Any]]] = []
        real_emit = gateway_tasks.emit

        async def record(event: str, payload: dict[str, Any]) -> None:
            emitted.append((event, payload))
            await real_emit(event, payload)

        started: list[Any] = []
        monkeypatch.setattr(gateway_tasks, "emit", record)
        monkeypatch.setattr(agent_dispatch, "_start", lambda run: started.append(run))
        monkeypatch.setenv(agent_dispatch.DISPATCH_FLAG, "1")
        task = _task(live, live.team, live.s.todo, "c9")
        await _claim(live, task)
        with live.admin.begin() as c:
            notes = c.execute(text("SELECT count(*) FROM pm_notifications WHERE"
                                   " task_id = CAST(:t AS uuid)"), {"t": task}).scalar()
        assert notes == 0
        [assigned] = [p for e, p in emitted if e == "pm.task.assigned"]
        assert assigned["assignees"] == [MEMBER]
        # The real dispatch sink, on the event the claim sent.
        await tw._in_scope(live, lambda: agent_dispatch.on_event(
            "projects", "pm.task.assigned", assigned))
        assert started == []
        assert _activities(live, task, "agent_run") == []

    async def test_a_member_may_claim_too(self, live):
        """The route is not agent-only (§9.2). A member's claim has no via."""
        task = _task(live, live.team, live.s.todo, "member claim")
        r = await _send(live, "POST", f"/projects/tasks/{task}/claim", signed=None)
        assert r.status_code == 200, r.text
        assert r.json()["moved"] is True and r.json()["assigned"] is True
        assert _assignees(live, task) == [MEMBER]
        [move] = _activities(live, task, "status_change")
        assert "via" not in (move.meta or {})


class TestAnAgentMayMoveATaskToDone:
    """F1 to F5 (§15 EA-6f, EA3-32)."""

    pytestmark = _DB_GATE

    async def test_f1_an_agent_moves_a_task_to_done(self, live):
        task = _task(live, live.team, live.s.doing, "f1")
        result = await _mcp(live, "set_task_status", {
            "task_id": task, "status_id": live.s.done,
            "expected_version": await _version(live, task)})
        assert result["isError"] is False, result
        row = _row(live, task)
        assert str(row.status_id) == live.s.done and row.completed_at is not None
        [move] = _activities(live, task, "status_change")
        assert move.meta["via"] == VIA and move.meta["to_category"] == "done"

    async def test_f2_cancel_is_refused_at_the_edge_and_at_the_gateway(self, live):
        task = _task(live, live.team, live.s.doing, "f2")
        result = await _mcp(live, "set_task_status", {
            "task_id": task, "status_id": live.s.cancelled,
            "expected_version": await _version(live, task)})
        assert _text(result) == CANCEL_TEXT
        # A signed PATCH that skips the edge: 422, and the title rolls back.
        r = await _send(live, "PATCH", f"/projects/tasks/{task}", body={
            "title": "f2 changed", "status_id": live.s.cancelled})
        assert r.status_code == 422, r.text
        assert r.json()["detail"] == CANCEL_TEXT
        row = _row(live, task)
        assert (row.title, str(row.status_id), row.completed_at) == (
            "f2", live.s.doing, None)

    async def test_f3_an_agents_done_leaves_the_open_subtasks_open(self, live):
        parent = _task(live, live.team, live.s.doing, "f3 parent")
        kids = [_task(live, live.team, live.s.todo, f"f3 kid {n}", parent=parent)
                for n in (1, 2)]
        r = await _send(live, "PATCH", f"/projects/tasks/{parent}",
                        body={"status_id": live.s.done}, query="include_subtasks=true")
        assert r.status_code == 422, r.text
        assert str(_row(live, parent).status_id) == live.s.doing
        result = await _mcp(live, "set_task_status", {
            "task_id": parent, "status_id": live.s.done,
            "expected_version": await _version(live, parent)})
        assert result["isError"] is False, result
        assert str(_row(live, parent).status_id) == live.s.done
        for kid in kids:
            row = _row(live, kid)
            assert (str(row.status_id), row.completed_at) == (live.s.todo, None)

    async def test_f4_an_agents_done_on_a_recurring_task_makes_one_successor(self, live):
        task = _task(live, live.team, live.s.doing, "f4 weekly")
        r = await _send(live, "PUT", f"/projects/tasks/{task}/recurrence", signed=None,
                        body={"freq": "daily", "interval": 1, "anchor": "completed"})
        assert r.status_code == 200, r.text
        rule = str(_row(live, task).recurrence_id)

        async def move(status: str) -> None:
            result = await _mcp(live, "set_task_status", {
                "task_id": task, "status_id": status,
                "expected_version": await _version(live, task)})
            assert result["isError"] is False, result

        def series() -> int:
            with live.admin.begin() as c:
                return int(c.execute(text(
                    "SELECT count(*) FROM pm_tasks WHERE recurrence_id = CAST(:r AS uuid)"),
                    {"r": rule}).scalar() or 0)

        await move(live.s.done)
        assert series() == 2
        # Reopen and close again: still one successor.
        await move(live.s.doing)
        await move(live.s.done)
        assert series() == 2

    async def test_f5_create_task_with_a_done_status_is_still_refused(self, live):
        for status in (live.s.done, live.s.cancelled):
            result = await _mcp(live, "create_task", {
                "project_id": live.team, "title": "f5 closed", "status_id": status})
            assert _text(result) == CREATE_TEXT
        r = await _send(live, "POST", "/projects/tasks", body={
            "project_id": live.team, "title": "f5 closed", "status_id": live.s.done})
        assert r.status_code == 422, r.text
        assert r.json()["detail"] == CREATE_TEXT
        assert tw._count(live, "f5 closed") == 0

