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
* M23. Fix round 1. ``G/core.py`` ``_LOCK_TASK_SQL`` without ``FOR NO KEY UPDATE``. ``test_two_claims_with_one_version_give_one_200_and_one_412`` and ``test_a_claim_racing_a_move_to_done_leaves_a_consistent_row``.
* M23b. ``G/tasks.py`` ``claim_task``: ``load_visible_task`` in place of ``lock_task`` (the code before fix round 1). The same two tests.
* M26. Fix round 2. ``G/tasks.py`` ``set_assignees``: no ``lock_task``, so the PUT takes the key before the row again. ``test_a_claim_and_a_member_write_of_the_same_assignee_never_deadlock[put]`` (a ``DeadlockDetectedError``).
* M27. Fix round 2. ``G/bulk.py`` ``_apply_to_one``: no ``lock_task``. ``test_a_claim_and_a_member_write_of_the_same_assignee_never_deadlock[bulk]`` (a ``DeadlockDetectedError``).
* M24. ``G/tasks.py`` ``claim_task``: no ``assert_assignable_here`` (the review's V4). ``test_a_task_in_another_persons_personal_project_is_refused``.
* M25. ``G/tasks.py`` ``claim_task``: no visibility load, so the lock reads by id. ``test_a_task_outside_the_reach_of_the_connection_is_not_found[outside_pins]`` and ``[personal_off]``. The ``[other_org]`` case stays green, because RLS refuses it too.
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import agent_signing, agent_tokens
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
OTHER = "other@mcpwrites.test"


def _connection(a, *, include_personal: bool) -> tuple[str, str]:
    """``w._Seeder.connection`` with ``include_personal`` named (fix round 1)."""
    cid = str(a.one(
        "INSERT INTO agent_connection (organization_id, member_email, client_id,"
        " client_name, client_origin, scopes, pinned_projects, include_personal,"
        " expires_at) VALUES (CAST(:o AS uuid), :m, 'https://client.example/meta.json',"
        " 'Client', :origin, CAST(:scopes AS text[]), CAST(:pins AS uuid[]), :ip,"
        " now() + interval '1 day') RETURNING id",
        o=a.org, m=MEMBER, origin=w.ORIGIN, scopes=list(w.WRITE_SCOPES), pins=[],
        ip=include_personal))
    raw = agent_tokens.new_token()
    a.one("INSERT INTO agent_token (organization_id, connection_id, kind, token_hash,"
          " expires_at, client_id, resource, scope) VALUES (CAST(:o AS uuid),"
          " CAST(:c AS uuid), 'access', :h, now() + interval '1 hour',"
          " 'https://client.example/meta.json', :r, :s) RETURNING id",
          o=a.org, c=cid, h=agent_tokens.hash_token(raw), r=w.RESOURCE,
          s=" ".join(w.WRITE_SCOPES))
    return cid, raw


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
    # Fix round 1. Two personal projects: one of another person, granted to
    # the member, and the member's own.
    personal = SimpleNamespace(**{
        name: str(a.one(
            "INSERT INTO pm_projects (name, status, source, created_by, organization_id,"
            " timezone, owns_statuses, personal_owner) VALUES (:n, 'active', 'manual',"
            " :o_, CAST(:o AS uuid), 'Asia/Kolkata', true, :o_) RETURNING id",
            n=f"Personal {name}", o_=owner, o=org))
        for name, owner in (("other", OTHER), ("mine", MEMBER))})
    for project, by in ((personal.other, OTHER), (personal.mine, MEMBER)):
        a.one("INSERT INTO pm_project_grants (project_id, subject, created_by,"
              " organization_id) VALUES (CAST(:p AS uuid), :s, :a, CAST(:o AS uuid))"
              " RETURNING project_id", p=project, s=MEMBER, a=by, o=org)
    s.other_todo = a.status(personal.other, "To do", "todo", 0)
    s.other_doing = a.status(personal.other, "Doing", "in_progress", 1)
    s.mine_todo = a.status(personal.mine, "To do", "todo", 0)
    s.mine_doing = a.status(personal.mine, "Doing", "in_progress", 1)
    # A second organization, with one task in it.
    org_b = str(uuid.uuid4())
    with eng.begin() as c:
        c.execute(text("INSERT INTO organization (id, slug, display_name) "
                       "VALUES (:id, 'mcpclaimb', 'mcpclaimb')"), {"id": org_b})
    b = w._Seeder(eng, org_b)
    b.number = 300_000
    b_project = b.project("B team")
    b_todo = b.status(b_project, "To do", "todo", 0)
    b.status(b_project, "Doing", "in_progress", 1)
    world_ = SimpleNamespace(admin=eng, org=org, seeder=a, team=team, sub=sub, bare=bare,
                             s=s, c={}, tok={}, personal=personal,
                             org_b=SimpleNamespace(seeder=b, project=b_project, todo=b_todo))
    for name, scopes in (("write", w.WRITE_SCOPES),):
        world_.c[name], world_.tok[name] = a.connection(scopes=scopes)
    world_.c["pinned"], world_.tok["pinned"] = a.connection(
        scopes=w.WRITE_SCOPES, pins=(team,))
    world_.c["no_personal"], world_.tok["no_personal"] = _connection(
        a, include_personal=False)
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
        assert sorted(body["task"]["assignees"]) == sorted([LEAD, MEMBER])
        assert _assignees(live, task) == sorted([LEAD, MEMBER])
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
        # A real change through the gateway, so `updated_at` moves on.
        moved_on = await _send(live, "PATCH", f"/projects/tasks/{task}",
                               body={"title": "c6 moved on"})
        assert moved_on.status_code == 200, moved_on.text
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


# ═══════════════════════════════════════════════════════════════════════════
# Fix round 1: the row lock, the personal-project guard and the reach
# ═══════════════════════════════════════════════════════════════════════════


async def _gateway_unscoped(world, method: str, path: str, *, body: dict[str, Any],
                            signed: str | None = "write",
                            if_match: str | None = None) -> httpx.Response:
    """One request to the gateway app, as ``tw._send`` makes it. The caller
    holds the engine scope, so two requests can run at the same time."""
    from gateway.main import app as gateway_app

    raw = json.dumps(body).encode()
    sent: dict[str, str] = {"Content-Type": "application/json"}
    if signed is not None:
        sent.update(agent_signing.signed_headers(
            KEY, organization_id=world.org, connection_id=world.c[signed], actor_via=VIA,
            method=method, path=path, query="", body=raw))
    else:
        sent.update({"Authorization": f"Bearer {tw.INTERNAL}", "X-User-Email": MEMBER})
    if if_match is not None:
        sent["If-Match"] = if_match
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway_app),
                                 base_url="http://gateway.test") as client:
        return await client.request(method, path, headers=sent, content=raw)


def _consistent(world, task: str) -> bool:
    """A done row carries ``completed_at``, and an open row carries none."""
    with world.admin.begin() as c:
        row = c.execute(text(
            "SELECT s.category, t.completed_at FROM pm_tasks t JOIN pm_task_statuses s"
            " ON s.id = t.status_id WHERE t.id = CAST(:t AS uuid)"), {"t": task}).one()
    closed = row.category in ("done", "cancelled")
    return closed == (row.completed_at is not None)


#: The runs of each race test (fix round 3). No barrier: the two requests
#: start together, and the database decides who goes first.
RACE_RUNS = 10


async def _together(world, *calls: tuple[str, str, dict[str, Any], str | None, str | None],
                    errors: bool = False) -> list[Any]:
    """Start each ``(method, path, body, signed, if_match)`` request at once,
    in one engine scope, and wait for all of them. With *errors*, an error
    that the app raised comes back in the list."""
    async def go() -> list[Any]:
        return list(await asyncio.gather(*(
            _gateway_unscoped(world, m, path, body=body, signed=signed, if_match=im)
            for m, path, body, signed, im in calls), return_exceptions=errors))
    return await tw._in_scope(world, go)


class TestTheClaimRace:
    """Fix rounds 1 to 3. Two requests run at the same time through the real
    gateway app, ``RACE_RUNS`` times, with no barrier. Each run must end with
    no deadlock and a consistent row (a deadlock is a 500, or an error that
    the ASGI transport raises)."""

    pytestmark = _DB_GATE

    async def test_two_claims_with_one_version_give_one_200_and_one_412(self, live):
        for n in range(RACE_RUNS):
            task = _task(live, live.team, live.s.todo, f"two claims {n}")
            version = await _version(live, task)
            claim = ("POST", f"/projects/tasks/{task}/claim", {}, "write", version)
            answers = await _together(live, claim, claim)
            assert sorted(r.status_code for r in answers) == [200, 412], (
                n, [r.text for r in answers])
            assert len(_activities(live, task, "assignment")) == 1, n
            assert len(_activities(live, task, "status_change")) == 1, n
            assert _assignees(live, task) == [MEMBER], n
            assert str(_row(live, task).status_id) == live.s.doing, n

    async def test_a_claim_racing_a_move_to_done_leaves_a_consistent_row(self, live):
        for n in range(RACE_RUNS):
            task = _task(live, live.team, live.s.todo, f"claim vs done {n}")
            claim, done = await _together(
                live,
                ("POST", f"/projects/tasks/{task}/claim", {}, "write", None),
                ("PATCH", f"/projects/tasks/{task}", {"status_id": live.s.done}, None, None))
            assert done.status_code == 200, (n, done.text)
            assert claim.status_code in (200, 422), (n, claim.text)
            assert _consistent(live, task), (n, claim.text)
            assert str(_row(live, task).status_id) == live.s.done, n
            if claim.status_code == 422:
                assert _assignees(live, task) == [], n

    @pytest.mark.parametrize("other", ["put", "bulk"])
    async def test_a_claim_and_a_member_write_of_the_same_assignee_never_deadlock(
            self, live, other):
        """A claim against a PUT of the assignees, or a bulk people edit, that
        adds the same member. The claim gets 200, or 412 when the member
        write landed first and moved the version on."""
        for n in range(RACE_RUNS):
            task = _task(live, live.team, live.s.todo, f"claim vs {other} {n}")
            version = await _version(live, task)
            if other == "put":
                member = ("PUT", f"/projects/tasks/{task}/assignees",
                          {"assignees": [MEMBER]}, None, None)
            else:
                member = ("POST", "/projects/tasks/bulk",
                          {"task_ids": [task], "assignees_add": [MEMBER]}, None, None)
            claim, written = await _together(
                live, ("POST", f"/projects/tasks/{task}/claim", {}, "write", version), member)
            assert written.status_code == 200, (n, written.text)
            assert claim.status_code in (200, 412), (n, claim.text)
            assert _assignees(live, task) == [MEMBER], n
            assert _consistent(live, task), n
            expected = live.s.doing if claim.status_code == 200 else live.s.todo
            assert str(_row(live, task).status_id) == expected, n

    async def test_a_claim_and_a_merge_that_brings_the_member_never_deadlock(self, live):
        """A merge into the claimed task, whose source holds the member as an
        assignee. The merge moves the assignee key and then writes the row.

        ⚠️ The merge may fail with a unique violation of
        ``pm_task_assignees_pkey``. That is the merge's own race: its
        ``NOT EXISTS`` cannot see a key that another transaction holds. A PUT
        of the same member gives it too, 10 times in 10, with no claim
        involved (fix round 3). Spec §17 holds it. A deadlock fails the test."""
        for n in range(RACE_RUNS):
            task = _task(live, live.team, live.s.todo, f"merge target {n}")
            source = _task(live, live.team, live.s.todo, f"merge source {n}")
            _assign(live, source, MEMBER)
            version = await _version(live, task)
            claim, merged = await _together(
                live, ("POST", f"/projects/tasks/{task}/claim", {}, "write", version),
                ("POST", f"/projects/tasks/{task}/merge", {"sources": [source]}, None, None),
                errors=True)
            for answer in (claim, merged):
                assert "deadlock" not in str(answer).lower(), (n, answer)
            assert isinstance(claim, httpx.Response), (n, claim)
            assert claim.status_code in (200, 412), (n, claim.text)
            if not isinstance(merged, httpx.Response):
                assert "pm_task_assignees_pkey" in str(merged), (n, merged)
            else:
                assert merged.status_code == 200, (n, merged.text)
            assert _assignees(live, task) == [MEMBER], n
            assert _consistent(live, task), n


class TestTheClaimGuardsAndReach:
    """Fix round 1: the personal-project guard (R7 gap V4) and the reach of
    a connection."""

    pytestmark = _DB_GATE

    async def test_a_task_in_another_persons_personal_project_is_refused(self, live):
        task = _task(live, live.personal.other, live.s.other_todo, "their own")
        result = await _claim(live, task)
        assert _text(result) == tools.TEXT_BAD_REQUEST
        assert _assignees(live, task) == []
        assert _activities(live, task) == []
        assert str(_row(live, task).status_id) == live.s.other_todo

    async def test_a_task_in_the_members_own_personal_project_is_claimed(self, live):
        """The control: the owner may hold a task of their own project."""
        task = _task(live, live.personal.mine, live.s.mine_todo, "my own")
        result = await _claim(live, task)
        assert result["isError"] is False, result
        assert str(_row(live, task).status_id) == live.s.mine_doing

    @pytest.mark.parametrize("case", ["outside_pins", "personal_off", "other_org"])
    async def test_a_task_outside_the_reach_of_the_connection_is_not_found(
            self, live, case):
        if case == "outside_pins":
            name, task = "pinned", _task(live, live.bare, live.s.bare_todo, "unpinned")
        elif case == "personal_off":
            name, task = "no_personal", _task(live, live.personal.mine,
                                              live.s.mine_todo, "hidden personal")
        else:
            b = live.org_b
            name, task = "write", b.seeder.task(b.project, b.todo, "other org")
        result = await tw._mcp(live, name, "claim_task", {
            "task_id": task, "expected_version": "2026-10-15T00:00:00+00:00"})
        assert _text(result) == "Task not found."
        assert _assignees(live, task) == []
        assert _activities(live, task) == []

