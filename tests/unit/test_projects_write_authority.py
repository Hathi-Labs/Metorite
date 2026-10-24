"""WS-64 OP-8 — who may delete, and who may write a grant (D-OP-11, D-OP-12).

Spec: ``project-docs/specs/one_projects_app.md`` D-OP-11, D-OP-12 and §9
OP-8, done-when 1 to 6.

The rule lives in ONE module, ``gateway/routes/projects/authority.py``. A
task, a project or a sub-project is deleted by the member who created it, by
a lead of its project or of an ancestor project, or by a member who may
manage Projects settings. A grant is written by a lead and by a Projects
admin only. Every door calls the module: the app routes, the agent route,
the bulk delete, and the ``can_delete`` flag that the app and the chat read.

Two halves.

**Database-free (always runs).** The three copies of the task sentence are
one sentence. Each door calls the module. The chat refuses before its card
when the row says ``can_delete`` false, and it quotes the server's words.

**R8 (a real database, as a NON-privileged role, on a promoted catalog of
its own).** Four members: a creator, a lead, an admin and a viewer. The real
gateway app, the real MCP edge and the real chat tool.

Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_projects_write_authority.py -v -rs

**The mutations (R7).** Each one turned the named test red. ``G`` is
``apps/services/gateway/gateway/routes/projects``, ``E`` is
``apps/services/agent_edge/agent_edge`` and ``S`` is
``apps/skills/skill-projects/skill_projects``.

* A1. ``G/authority.py`` ``may_delete_task``: returns True. ``test_1_the_creator_deletes_and_a_viewer_gets_403`` and ``test_6_one_answer_for_the_viewer_at_every_door``.
* A2. ``G/authority.py`` ``_made_by``: returns False. ``test_1_the_creator_deletes_and_a_viewer_gets_403`` and ``test_4_the_creator_of_a_sub_project_deletes_it_with_its_tasks``.
* A3. ``G/authority.py`` ``_leads``: returns False. ``test_2_the_lead_deletes_a_task_another_member_made`` and ``test_5_a_lead_and_an_admin_write_a_grant_and_a_viewer_gets_403``.
* A4. ``G/authority.py`` ``_LED_SUBTREE_SQL``: no recursive arm, so only the led project itself counts. ``test_2_the_lead_deletes_a_task_another_member_made``.
* A5. ``G/authority.py`` ``may_delete_task``: no ``can_manage_settings`` arm. ``test_3_an_admin_deletes_any_task_and_any_project``.
* A5b. ``G/authority.py`` ``may_delete_project``: no ``can_manage_settings`` arm. ``test_3_an_admin_deletes_any_task_and_any_project``.
* A6. ``G/tasks.py`` ``delete_task``: no ``assert_may_delete_task``. ``test_1_the_creator_deletes_and_a_viewer_gets_403``.
* A7. ``G/tasks.py`` ``agent_delete_task``: no ``assert_may_delete_task``. ``test_6_one_answer_for_the_viewer_at_every_door``.
* A8. ``G/tree.py`` ``delete_node``: no ``assert_may_delete_project``. ``test_4_the_creator_of_a_sub_project_deletes_it_with_its_tasks``.
* A9. ``G/tree.py`` ``create_grant``: no ``assert_may_write_grant``. ``test_5_a_lead_and_an_admin_write_a_grant_and_a_viewer_gets_403``.
* A10. ``G/tree.py`` ``delete_grant``: no ``assert_may_write_grant``. ``test_5_a_lead_and_an_admin_write_a_grant_and_a_viewer_gets_403``.
* A11. ``G/bulk.py`` ``bulk_edit``: no ``may_delete_task``. ``test_the_bulk_delete_fails_the_task_a_viewer_may_not_delete``.
* A12. ``E/tools.py`` ``_delete_task``: no ``(403, TEXT_DELETE_NOT_ALLOWED)`` entry. ``test_6_one_answer_for_the_viewer_at_every_door``.
* A13. ``S/guarded.py`` ``delete_refusal``: returns ``""``. ``test_6_one_answer_for_the_viewer_at_every_door`` and ``test_the_chat_refuses_before_the_card_and_quotes_the_server``.
* A14. ``G/authority.py`` ``may_write_grant``: the creator arm added. ``test_5_a_lead_and_an_admin_write_a_grant_and_a_viewer_gets_403``.

Fix round 1 (the lead of D-OP-11, the moves of D-OP-14, the successor):

* B1. ``G/tree.py`` ``patch_node``: no ``assert_may_change_lead``. ``test_7_a_viewer_who_makes_themself_lead_gets_403``.
* B2. ``G/tree.py`` ``create_node``: no ``assert_may_name_lead``. ``test_7_a_viewer_who_makes_themself_lead_gets_403`` and ``test_7_a_member_leads_the_space_they_create``.
* B3. ``G/authority.py`` ``assert_may_name_lead``: a new root passes any lead. ``test_7_a_member_leads_the_space_they_create``.
* B4. ``G/tree.py`` ``move_node``: no ``assert_may_move_project``. ``test_8_a_new_space_does_not_launder_a_project``.
* B5. ``G/tasks.py`` ``move_task_in``: no ``assert_may_move_task``. ``test_8_a_new_space_does_not_launder_a_task``.
* B6. ``G/move.py`` ``_plan``: no ``assert_may_move_task``. ``test_8_a_new_space_does_not_launder_a_task``.
* B7. ``G/recurrence.py`` ``spawn_successor``: ``created_by`` is the completer. ``test_9_the_completer_is_not_the_creator_of_the_successor``.
* B8. ``G/authority.py`` ``assert_may_move_project``: refuses every member. ``test_8_a_lead_re_parents_a_project``.
* B9. ``G/authority.py`` ``assert_may_change_lead``: refuses every member. ``test_7_a_lead_hands_the_lead_to_another_member``.
* B10. ``G/personal.py`` ``_organize``: passes ``user=None`` to the move. ``test_the_new_doors_call_the_one_module``.
* B11. ``G/authority.py`` ``assert_may_change_lead``: a ``null`` lead passes. ``test_7_a_viewer_who_makes_themself_lead_gets_403``.
"""

from __future__ import annotations

import inspect
import os
import types
import uuid
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("skill_projects", reason="skill-projects not installed")

from agent_edge import tools
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests.unit import test_mcp_edge_delete as d
from tests.unit import test_mcp_edge_task_writes as tw
from tests.unit import test_mcp_edge_writes as w
from tests.unit._projects_agent_fakes import FakeResponse, approve, fake_gateway
from tests.unit._tenant_ladder import apply_ladder

_DB_GATE = w._DB_GATE

#: The four people. The domain is the one ``live_app`` allows.
CREATOR = "creator@mcpwrites.test"
LEAD = "lead@mcpwrites.test"
ADMIN = "admin@mcpwrites.test"
VIEWER = "viewer@mcpwrites.test"
PEOPLE = (CREATOR, LEAD, ADMIN, VIEWER)


def _authority():
    from gateway.routes.projects import authority

    return authority


# ═══════════════════════════════════════════════════════════════════════════
# Database-free
# ═══════════════════════════════════════════════════════════════════════════


def test_the_three_copies_of_the_task_sentence_are_one_sentence() -> None:
    """The edge may not import the gateway, so it holds a copy."""
    a = _authority()
    assert tools.TEXT_DELETE_NOT_ALLOWED == a.TASK_DELETE_REFUSED
    assert a.TASK_DELETE_REFUSED == (
        "Only the person who created this task, the project lead or a Projects "
        "admin can delete it.")


def test_every_door_calls_the_one_module() -> None:
    """No door has its own copy of the rule. Each calls ``authority``."""
    from gateway.routes.projects import bulk, tasks, tree

    doors = {
        tasks.delete_task: "assert_may_delete_task(db, user, doomed)",
        tasks.agent_delete_task: "assert_may_delete_task(db, user, doomed)",
        tree.delete_node: "assert_may_delete_project(db, user, node)",
        tree.create_grant: "assert_may_write_grant(db, user, node)",
        tree.delete_grant: "assert_may_revoke_grant(db, user, node, found.subject)",
        bulk.bulk_edit: "may_delete_task(db, user, task, led=led)",
        tasks.get_task: 'attach_can_delete(db, user, [result], kind="task")',
        tasks.list_tasks: 'attach_can_delete(db, user, page_rows, kind="task")',
        tree.get_node: 'attach_can_delete(db, user, [result], kind="project")',
        tree.get_tree: 'kind="project"',
    }
    for fn, call in doors.items():
        assert call in inspect.getsource(fn), fn.__name__
    # The agent route checks the member's authority after the lock and before
    # the delete.
    src = inspect.getsource(tasks.agent_delete_task)
    assert src.index("lock_task(") < src.index("assert_may_delete_task(") < src.index(
        "delete_task_in(")


def test_the_my_tasks_purge_keeps_its_own_narrower_rule() -> None:
    """``DELETE /projects/my/tasks/{id}`` deletes only a task in the member's
    own personal tree (WS-39 S6g). That is narrower than D-OP-12, so it does
    not call the module, and this pins that it keeps its own check."""
    from gateway.routes.projects import personal

    src = inspect.getsource(personal.purge_my_task)
    assert "delete_task_in(" in src and "personal" in src


async def test_the_cheap_arms_cost_no_query() -> None:
    """A creator and an admin are answered without a read."""
    a = _authority()

    class NoDb:
        async def execute(self, *_a, **_k):
            raise AssertionError("no query expected")

    maker = SimpleNamespace(email="Maker@x.test", has_permission=lambda _p: False)
    admin = SimpleNamespace(email="boss@x.test", has_permission=lambda p: p == "projects:settings:write")
    row = SimpleNamespace(created_by="maker@x.test", project_id="p", id="p")
    assert await a.may_delete_task(NoDb(), maker, row)
    assert await a.may_delete_project(NoDb(), maker, row)
    assert await a.may_delete_task(NoDb(), admin, row)
    assert await a.may_write_grant(NoDb(), admin, row)


def _tool_gateway(monkeypatch, row: dict[str, Any]) -> list[dict]:
    """A fake gateway whose task and node reads answer *row*."""

    def answer(call: dict) -> Any:
        path, method = call["path"], call["method"]
        if method == "GET" and path.endswith("/subtree"):
            return {"subtasks": [], "hidden": 0}
        if method == "GET" and "/statuses" in path:
            return {"rows": [{"id": row.get("status_id"), "name": "To do", "category": "todo"}]}
        if method == "GET":
            return row
        if method == "DELETE":
            return {"deleted": row["id"], "cascaded": {}}
        return FakeResponse({"detail": "unexpected"}, 500)

    return fake_gateway(monkeypatch, answer)


async def test_the_chat_refuses_before_the_card_and_quotes_the_server(monkeypatch) -> None:
    from skill_projects import guarded

    a = _authority()
    tid = str(uuid.uuid4())
    calls = _tool_gateway(monkeypatch, {
        "id": tid, "title": "Not yours", "task_number": 3, "project_id": str(uuid.uuid4()),
        "can_delete": False, "delete_refusal": a.TASK_DELETE_REFUSED})
    asked = approve(monkeypatch)
    out = await guarded.delete_task(tid)
    assert asked == [], "no card for a delete the server refuses"
    assert out.startswith("Refused:"), out
    assert f"Gateway said: «{a.TASK_DELETE_REFUSED}»" in out
    assert not [c for c in calls if c["method"] == "DELETE"]

    pid = str(uuid.uuid4())
    calls = _tool_gateway(monkeypatch, {
        "id": pid, "name": "Theirs", "can_delete": False,
        "delete_refusal": a.PROJECT_DELETE_REFUSED})
    out = await guarded.delete_project(pid)
    assert asked == [] and f"Gateway said: «{a.PROJECT_DELETE_REFUSED}»" in out, out
    assert not [c for c in calls if c["method"] == "DELETE"]


async def test_a_row_without_the_flag_goes_on_to_the_card(monkeypatch) -> None:
    """Only an explicit False stops the tool. The route then decides."""
    from skill_projects import guarded

    assert guarded.delete_refusal({}, "task", "x") == ""
    assert guarded.delete_refusal({"can_delete": True}, "task", "x") == ""
    assert guarded.delete_refusal({"can_delete": False}, "task", "x").startswith("Refused:")


# ═══════════════════════════════════════════════════════════════════════════
# R8
# ═══════════════════════════════════════════════════════════════════════════

_APP_ROLE = "acb_app_op8auth"
_APP_PW = "op8auth_scratch_pw"


@pytest.fixture(scope="module")
def world():
    """A private promoted catalog with four members and this tree:

    ``Team`` (lead: LEAD) holds ``Sub`` (made by CREATOR, no lead).
    ``Elsewhere`` has no lead. Each project is granted to all four.
    """
    if not w._URL:
        pytest.skip("TENANT_LADDER_DATABASE_URL unset")
    admin_url = make_url(w._URL)
    dedicated = f"{admin_url.database}_op8auth"
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
                       "VALUES (:id, 'op8auth', 'op8auth')"), {"id": org})
    a = w._Seeder(eng, org)
    a.number = 800_000
    a.member(w.MEMBER, ["feature:projects"])
    for who in (CREATOR, LEAD, VIEWER):
        a.member(who, ["feature:projects"])
    a.member(ADMIN, ["feature:projects", "projects:settings:write"])
    team, elsewhere = a.project("Team"), a.project("Elsewhere")
    a.one("UPDATE pm_projects SET lead = :l WHERE id = CAST(:p AS uuid) RETURNING id",
          l=LEAD, p=team)
    for project in (team, elsewhere):
        for who in PEOPLE:
            d._grant(a, project, who)
    world_ = SimpleNamespace(
        admin=eng, org=org, seeder=a, team=team, elsewhere=elsewhere, c={}, tok={},
        todo={team: a.status(team, "To do", "todo", 0),
              elsewhere: a.status(elsewhere, "To do", "todo", 0)})
    world_.sub = _sub(world_, "Sub", CREATOR)
    for who in PEOPLE:
        world_.c[who], world_.tok[who] = d._connection_of(a, who)
    world_.c["write"], world_.tok["write"] = world_.c[CREATOR], world_.tok[CREATOR]
    world_.app_dsn = admin_url.set(
        database=dedicated, username=_APP_ROLE, password=_APP_PW,
    ).render_as_string(hide_password=False)
    yield world_

    eng.dispose()
    w._drop_database(admin_url, dedicated)


redis_fake = tw.redis_fake
live_app = tw.live_app
live = tw.live


def _sub(world, name: str, maker: str, parent: str | None = None) -> str:
    """A sub-project made by *maker*, which owns no status set."""
    return str(world.seeder.one(
        "INSERT INTO pm_projects (name, status, source, created_by, organization_id,"
        " timezone, parent_project_id) VALUES (:n, 'active', 'manual', :m,"
        " CAST(:o AS uuid), 'Asia/Kolkata', CAST(:p AS uuid)) RETURNING id",
        n=name, m=maker, o=world.org, p=parent or world.team))


def _task(world, project: str, maker: str, title: str) -> str:
    """A task made by *maker* in *project*, in the lane of ``Team`` or
    ``Elsewhere``, the status owner above it."""
    root = world.elsewhere if project == world.elsewhere else world.team
    world.seeder.number += 1
    return str(world.seeder.one(
        "INSERT INTO pm_tasks (title, project_id, root_project_id, status_id,"
        " created_by, organization_id, task_number) VALUES (:t, CAST(:p AS uuid),"
        " CAST(:r AS uuid), CAST(:s AS uuid), :m, CAST(:o AS uuid), :n) RETURNING id",
        t=title, p=project, r=root, s=world.todo[root], m=maker, o=world.org,
        n=world.seeder.number))


def _exists(world, table: str, row_id: str) -> bool:
    with world.admin.begin() as c:
        return c.execute(text(f"SELECT 1 FROM {table} WHERE id = CAST(:i AS uuid)"),
                         {"i": row_id}).first() is not None


def _grants(world, project: str) -> set[str]:
    with world.admin.begin() as c:
        return {r.subject for r in c.execute(text(
            "SELECT subject FROM pm_project_grants WHERE project_id = CAST(:p AS uuid)"),
            {"p": project})}


async def _as(world, who: str, method: str, path: str,
              body: dict[str, Any] | None = None) -> httpx.Response:
    return await d._person(world, who, method, path, body)


async def _signed_delete(world, who: str, task: str) -> httpx.Response:
    row = tw._row(world, task)
    return await tw._send(world, "POST", f"/projects/tasks/{task}/agent-delete",
                          body={"expected_version": row.updated_at.isoformat(),
                                "confirm_title": row.title, "subtasks_to_delete": "none"},
                          signed=who)


async def _edge_delete(world, who: str, task: str) -> dict[str, Any]:
    row = tw._row(world, task)
    return await tw._mcp(world, who, "delete_task", {
        "task_id": task, "expected_version": row.updated_at.isoformat(),
        "confirm_title": row.title, "subtasks_to_delete": "none"})


async def _chat(world, monkeypatch, who: str, act) -> Any:
    """Run a chat tool as *who*, against the real gateway app."""
    import skill_projects.client as client
    from gateway.main import app as gateway_app

    real = httpx.AsyncClient
    monkeypatch.setattr(client, "httpx", types.SimpleNamespace(
        AsyncClient=lambda **kw: real(transport=httpx.ASGITransport(app=gateway_app), **kw),
        TransportError=httpx.TransportError, Response=httpx.Response))
    monkeypatch.setattr(client, "gateway_url", lambda: "http://gateway.test")
    monkeypatch.setattr(client, "current_user_email", lambda: who)
    monkeypatch.setattr(client, "internal_token", lambda: tw.INTERNAL)
    return await tw._in_scope(world, act)


class TestTheDelete:
    """Done-when 1 to 4, through the real gateway app."""

    pytestmark = _DB_GATE

    async def test_1_the_creator_deletes_and_a_viewer_gets_403(self, live):
        task = _task(live, live.team, CREATOR, "op8 one")
        r = await _as(live, VIEWER, "DELETE", f"/projects/tasks/{task}")
        assert r.status_code == 403, r.text
        assert r.json()["detail"] == _authority().TASK_DELETE_REFUSED
        assert _exists(live, "pm_tasks", task)
        got = (await _as(live, VIEWER, "GET", f"/projects/tasks/{task}")).json()
        assert got["can_delete"] is False
        assert got["delete_refusal"] == _authority().TASK_DELETE_REFUSED
        got = (await _as(live, CREATOR, "GET", f"/projects/tasks/{task}")).json()
        assert got["can_delete"] is True and "delete_refusal" not in got
        r = await _as(live, CREATOR, "DELETE", f"/projects/tasks/{task}")
        assert r.status_code == 200, r.text
        assert not _exists(live, "pm_tasks", task)

    async def test_2_the_lead_deletes_a_task_another_member_made(self, live):
        """The lead of ``Team`` deletes in ``Team`` and in ``Sub`` below it."""
        here = _task(live, live.team, CREATOR, "op8 two here")
        below = _task(live, live.sub, CREATOR, "op8 two below")
        rows = (await _as(live, LEAD, "GET",
                          f"/projects/tasks?project_id={live.team}&include_subtree=true")).json()
        flags = {r["id"]: r["can_delete"] for r in rows["rows"]}
        assert flags[here] is True and flags[below] is True
        for task in (here, below):
            r = await _as(live, LEAD, "DELETE", f"/projects/tasks/{task}")
            assert r.status_code == 200, r.text
            assert not _exists(live, "pm_tasks", task)

    async def test_3_an_admin_deletes_any_task_and_any_project(self, live):
        task = _task(live, live.elsewhere, w.OWNER, "op8 three")
        r = await _as(live, ADMIN, "DELETE", f"/projects/tasks/{task}")
        assert r.status_code == 200, r.text
        assert not _exists(live, "pm_tasks", task)
        gone = _sub(live, "op8 three project", w.OWNER, live.elsewhere)
        r = await _as(live, ADMIN, "DELETE", f"/projects/nodes/{gone}")
        assert r.status_code == 200, r.text
        assert not _exists(live, "pm_projects", gone)

    async def test_4_the_creator_of_a_sub_project_deletes_it_with_its_tasks(self, live):
        mine = _sub(live, "op8 four mine", CREATOR)
        tasks_in = [_task(live, mine, w.OWNER, "op8 four a"), _task(live, mine, LEAD, "op8 four b")]
        r = await _as(live, VIEWER, "DELETE", f"/projects/nodes/{mine}")
        assert r.status_code == 403, r.text
        assert r.json()["detail"] == _authority().PROJECT_DELETE_REFUSED
        assert _exists(live, "pm_projects", mine)
        tree = (await _as(live, VIEWER, "GET", "/projects/tree")).json()
        flags = _flags(tree["rows"])
        assert flags[mine] is False and flags[live.team] is False
        tree = (await _as(live, CREATOR, "GET", "/projects/tree")).json()
        assert _flags(tree["rows"])[mine] is True
        r = await _as(live, CREATOR, "DELETE", f"/projects/nodes/{mine}")
        assert r.status_code == 200, r.text
        assert r.json()["cascaded"]["tasks"] == 2 and r.json()["cascaded"]["projects"] == 1
        assert not _exists(live, "pm_projects", mine)
        assert not any(_exists(live, "pm_tasks", t) for t in tasks_in)

    async def test_the_bulk_delete_fails_the_task_a_viewer_may_not_delete(self, live):
        theirs = _task(live, live.team, CREATOR, "op8 bulk theirs")
        mine = _task(live, live.team, VIEWER, "op8 bulk mine")
        r = await _as(live, VIEWER, "POST", "/projects/tasks/bulk",
                      {"task_ids": [theirs, mine], "action": "delete"})
        assert r.status_code == 200, r.text
        out = r.json()
        assert out["failed"] == [{"task_id": theirs,
                                  "reason": _authority().TASK_DELETE_REFUSED}]
        assert [a["task_id"] for a in out["results"]] == [mine]
        assert _exists(live, "pm_tasks", theirs) and not _exists(live, "pm_tasks", mine)
        r = await _as(live, LEAD, "POST", "/projects/tasks/bulk",
                      {"task_ids": [theirs], "action": "delete"})
        assert [a["task_id"] for a in r.json()["results"]] == [theirs], r.text


def _flags(rows: list[dict[str, Any]]) -> dict[str, bool]:
    out: dict[str, bool] = {}
    for row in rows:
        out[row["id"]] = row["can_delete"]
        out.update(_flags(row.get("children") or []))
    return out


class TestTheGrants:
    """Done-when 5."""

    pytestmark = _DB_GATE

    async def test_5_a_lead_and_an_admin_write_a_grant_and_a_viewer_gets_403(self, live):
        words = _authority().GRANT_WRITE_REFUSED
        path = f"/projects/nodes/{live.team}/grants"
        # OP-6b fix round 1: the route refuses a group that does not exist.
        for slug in ("viewers", "leads", "admins"):
            live.seeder.one(
                "INSERT INTO org_group (organization_id, slug, display_name) VALUES"
                " (CAST(:o AS uuid), :s, :s) ON CONFLICT (organization_id, slug)"
                " DO UPDATE SET slug = EXCLUDED.slug RETURNING id", o=live.org, s=slug)
        for who in (VIEWER, CREATOR):
            r = await _as(live, who, "POST", path, {"subject": "group:viewers"})
            assert r.status_code == 403, (who, r.text)
            assert r.json()["detail"] == words
        # The creator of a sub-project does not write its grants either.
        r = await _as(live, CREATOR, "POST", f"/projects/nodes/{live.sub}/grants",
                      {"subject": "group:viewers"})
        assert r.status_code == 403 and r.json()["detail"] == words, r.text
        assert "group:viewers" not in _grants(live, live.team) | _grants(live, live.sub)

        r = await _as(live, LEAD, "POST", path, {"subject": "group:leads"})
        assert r.status_code == 201, r.text
        by_lead = r.json()["id"]
        r = await _as(live, ADMIN, "POST", path, {"subject": "group:admins"})
        assert r.status_code == 201, r.text
        by_admin = r.json()["id"]
        assert {"group:leads", "group:admins"} <= _grants(live, live.team)

        r = await _as(live, VIEWER, "DELETE", f"{path}/{by_lead}")
        assert r.status_code == 403 and r.json()["detail"] == words, r.text
        assert "group:leads" in _grants(live, live.team)
        r = await _as(live, ADMIN, "DELETE", f"{path}/{by_lead}")
        assert r.status_code == 200, r.text
        r = await _as(live, LEAD, "DELETE", f"{path}/{by_admin}")
        assert r.status_code == 200, r.text
        assert not {"group:leads", "group:admins"} & _grants(live, live.team)


class TestOneAnswer:
    """Done-when 6: the agent route, the chat tool and the app route give the
    same answer for the same member."""

    pytestmark = _DB_GATE

    async def test_6_one_answer_for_the_viewer_at_every_door(self, live, monkeypatch):
        words = _authority().TASK_DELETE_REFUSED
        task = _task(live, live.team, CREATOR, "op8 six")
        await d._switch(live, True, VIEWER)
        # The app route.
        r = await _as(live, VIEWER, "DELETE", f"/projects/tasks/{task}")
        assert r.status_code == 403 and r.json()["detail"] == words, r.text
        # The agent route, signed for the viewer's own connection.
        r = await _signed_delete(live, VIEWER, task)
        assert r.status_code == 403 and r.json()["detail"] == words, r.text
        # The MCP tool says the same sentence.
        assert d._text(await _edge_delete(live, VIEWER, task)) == words
        # The chat refuses before its card, with the server's words.
        from skill_projects import guarded

        asked = approve(monkeypatch)
        out = await _chat(live, monkeypatch, VIEWER, lambda: guarded.delete_task(task))
        assert asked == [] and f"Gateway said: «{words}»" in out, out
        assert _exists(live, "pm_tasks", task)

    async def test_6_one_answer_for_the_creator_at_every_door(self, live, monkeypatch):
        await d._switch(live, True, CREATOR)
        by_app = _task(live, live.team, CREATOR, "op8 six app")
        r = await _as(live, CREATOR, "DELETE", f"/projects/tasks/{by_app}")
        assert r.status_code == 200, r.text
        by_agent = _task(live, live.team, CREATOR, "op8 six agent")
        body = d._structured(await _edge_delete(live, CREATOR, by_agent))
        assert body["deleted"] == by_agent
        by_chat = _task(live, live.team, CREATOR, "op8 six chat")
        from skill_projects import guarded

        asked = approve(monkeypatch)
        out = await _chat(live, monkeypatch, CREATOR, lambda: guarded.delete_task(by_chat))
        assert len(asked) == 1 and out.startswith("Deleted"), out
        assert not any(_exists(live, "pm_tasks", t) for t in (by_app, by_agent, by_chat))



# ═══════════════════════════════════════════════════════════════════════════
# Fix round 1: the lead, the moves and the recurrence successor
# ═══════════════════════════════════════════════════════════════════════════


def _col(world, table: str, row_id: str, column: str) -> Any:
    with world.admin.begin() as c:
        return c.execute(text(f"SELECT {column} FROM {table} WHERE id = CAST(:i AS uuid)"),
                         {"i": row_id}).scalar()


def _root(world, name: str, maker: str, lead: str | None = None) -> str:
    """A root project made by *maker*, granted to all four people."""
    pid = str(world.seeder.one(
        "INSERT INTO pm_projects (name, status, source, created_by, organization_id,"
        " timezone, lead, owns_statuses) VALUES (:n, 'active', 'manual', :m,"
        " CAST(:o AS uuid), 'Asia/Kolkata', :l, true) RETURNING id",
        n=name, m=maker, o=world.org, l=lead))
    for who in PEOPLE:
        d._grant(world.seeder, pid, who)
    return pid


async def _new_space(world, who: str, name: str, lead: str | None) -> httpx.Response:
    body: dict[str, Any] = {"name": name}
    if lead is not None:
        body["lead"] = lead
    return await _as(world, who, "POST", "/projects/nodes", body)


def test_the_new_doors_call_the_one_module() -> None:
    """Fix round 1. Each write that sets a lead or moves a row calls the module."""
    from gateway.routes.projects import move, personal, tasks, tree

    doors = {
        tree.create_node: "assert_may_name_lead(db, user, parent_row, values.get(\"lead\"))",
        tree.patch_node: "assert_may_change_lead(db, user, before, values)",
        tree.move_node: "assert_may_move_project(db, user, moved)",
        tasks.move_task_in: "assert_may_move_task(db, user, task)",
        move._plan: "assert_may_move_task(db, user, task)",
        personal._organize: "user=user",
    }
    for fn, call in doors.items():
        assert call in inspect.getsource(fn), fn.__name__
    a = _authority()
    assert a.may_move_task is a.may_delete_task


class TestTheLead:
    """P0-1. The lead IS delete and grant authority, so a write to it is a
    grant write (D-OP-11)."""

    pytestmark = _DB_GATE

    async def test_7_a_viewer_who_makes_themself_lead_gets_403(self, live, monkeypatch):
        a = _authority()
        for project in (live.elsewhere, live.team):
            before = _col(live, "pm_projects", project, "lead")
            r = await _as(live, VIEWER, "PATCH", f"/projects/nodes/{project}",
                          {"lead": VIEWER})
            assert r.status_code == 403, r.text
            assert r.json()["detail"] == a.LEAD_WRITE_REFUSED
            assert _col(live, "pm_projects", project, "lead") == before
        # A clear is a change too.
        r = await _as(live, VIEWER, "PATCH", f"/projects/nodes/{live.team}", {"lead": None})
        assert r.status_code == 403, r.text
        assert _col(live, "pm_projects", live.team, "lead") == LEAD
        # The same lead again changes nothing, so a rename that echoes it passes.
        r = await _as(live, VIEWER, "PATCH", f"/projects/nodes/{live.team}",
                      {"lead": LEAD.upper()})
        assert r.status_code == 200, r.text
        # A child under a project the viewer does not lead, with a lead.
        r = await _as(live, VIEWER, "POST", "/projects/nodes",
                      {"name": "op8 lead child", "parent_project_id": live.elsewhere,
                       "lead": VIEWER})
        assert r.status_code == 403 and r.json()["detail"] == a.LEAD_CREATE_REFUSED, r.text
        # The chat tool goes to the same route, and the route refuses.
        from skill_projects import writes
        from skill_projects.client import GatewayRefusal

        approve(monkeypatch)

        async def act():
            try:
                return await writes.update_project(live.elsewhere, lead=VIEWER)
            except GatewayRefusal as err:
                return str(err)

        out = await _chat(live, monkeypatch, VIEWER, act)
        assert "Not permitted" in out, out
        assert _col(live, "pm_projects", live.elsewhere, "lead") is None

    async def test_7_a_lead_hands_the_lead_to_another_member(self, live):
        handed = _root(live, "op8 handover", w.OWNER, lead=LEAD)
        r = await _as(live, LEAD, "PATCH", f"/projects/nodes/{handed}", {"lead": CREATOR})
        assert r.status_code == 200, r.text
        assert _col(live, "pm_projects", handed, "lead") == CREATOR
        # The old lead has handed the authority on, and cannot take it back.
        r = await _as(live, LEAD, "PATCH", f"/projects/nodes/{handed}", {"lead": LEAD})
        assert r.status_code == 403, r.text
        # The lead of an ancestor names a lead below, and an admin names any.
        r = await _as(live, LEAD, "POST", "/projects/nodes",
                      {"name": "op8 lead below", "parent_project_id": live.team,
                       "lead": CREATOR})
        assert r.status_code == 201, r.text
        assert r.json()["lead"] == CREATOR
        r = await _as(live, ADMIN, "PATCH", f"/projects/nodes/{handed}", {"lead": VIEWER})
        assert r.status_code == 200, r.text

    async def test_7_a_member_leads_the_space_they_create(self, live):
        a = _authority()
        r = await _new_space(live, VIEWER, "op8 my space", VIEWER)
        assert r.status_code == 201, r.text
        assert r.json()["lead"] == VIEWER
        r = await _new_space(live, VIEWER, "op8 their space", CREATOR)
        assert r.status_code == 403 and r.json()["detail"] == a.LEAD_ROOT_REFUSED, r.text
        r = await _new_space(live, ADMIN, "op8 admin space", CREATOR)
        assert r.status_code == 201 and r.json()["lead"] == CREATOR, r.text


class TestTheMoves:
    """P0-2 (D-OP-14). A move changes who sees a row, so a view is not enough."""

    pytestmark = _DB_GATE

    async def test_8_a_new_space_does_not_launder_a_project(self, live):
        a = _authority()
        r = await _new_space(live, VIEWER, "op8 launder space", VIEWER)
        assert r.status_code == 201, r.text
        space = r.json()["id"]
        theirs = _sub(live, "op8 their project", CREATOR, live.elsewhere)
        r = await _as(live, VIEWER, "POST", f"/projects/nodes/{theirs}/move",
                      {"parent_project_id": space})
        assert r.status_code == 403, r.text
        assert r.json()["detail"] == a.PROJECT_MOVE_REFUSED
        assert str(_col(live, "pm_projects", theirs, "parent_project_id")) == live.elsewhere
        # The member can delete it no more after the attempt than before.
        r = await _as(live, VIEWER, "DELETE", f"/projects/nodes/{theirs}")
        assert r.status_code == 403, r.text
        # A reorder under the same parent changes nobody's view.
        r = await _as(live, VIEWER, "POST", f"/projects/nodes/{theirs}/move",
                      {"parent_project_id": live.elsewhere, "position": 7.0})
        assert r.status_code == 200, r.text

    async def test_8_a_lead_re_parents_a_project(self, live):
        below = _sub(live, "op8 lead moves", CREATOR, live.team)
        r = await _as(live, LEAD, "POST", f"/projects/nodes/{below}/move",
                      {"parent_project_id": live.elsewhere})
        assert r.status_code == 200, r.text
        assert str(_col(live, "pm_projects", below, "parent_project_id")) == live.elsewhere

    async def test_8_a_new_space_does_not_launder_a_task(self, live):
        a = _authority()
        r = await _new_space(live, VIEWER, "op8 task launder space", VIEWER)
        assert r.status_code == 201, r.text
        space = r.json()["id"]
        theirs = _task(live, live.team, CREATOR, "op8 their task")
        r = await _as(live, VIEWER, "POST", f"/projects/tasks/{theirs}/move",
                      {"project_id": space})
        assert r.status_code == 403 and r.json()["detail"] == a.TASK_MOVE_REFUSED, r.text
        body = {"task_ids": [theirs], "destination_project_id": space,
                "accept_drops": True}
        for path in ("/projects/tasks/move/preview", "/projects/tasks/move"):
            r = await _as(live, VIEWER, "POST", path, body)
            assert r.status_code == 403, (path, r.text)
            assert r.json()["detail"] == a.TASK_MOVE_REFUSED
        assert str(_col(live, "pm_tasks", theirs, "project_id")) == live.team
        # A status move inside the same project needs only a view.
        doing = live.seeder.status(live.team, "op8 Doing", "in_progress", 5)
        r = await _as(live, VIEWER, "PATCH", f"/projects/tasks/{theirs}",
                      {"status_id": doing})
        assert r.status_code == 200, r.text
        # The creator moves it, and the lead of its project does too.
        r = await _as(live, CREATOR, "POST", f"/projects/tasks/{theirs}/move",
                      {"project_id": space})
        assert r.status_code == 200, r.text
        assert str(_col(live, "pm_tasks", theirs, "project_id")) == space
        led = _task(live, live.team, CREATOR, "op8 lead moves task")
        r = await _as(live, LEAD, "POST", "/projects/tasks/move",
                      {"task_ids": [led], "destination_project_id": live.elsewhere,
                       "accept_drops": True})
        assert r.status_code == 200, r.text
        assert str(_col(live, "pm_tasks", led, "project_id")) == live.elsewhere


class TestTheSuccessor:
    """P1. A recurring task's successor keeps the series' creator (D-OP-12)."""

    pytestmark = _DB_GATE

    async def test_9_the_completer_is_not_the_creator_of_the_successor(self, live):
        a = _authority()
        done = live.seeder.status(live.elsewhere, "op8 Done", "done", 9)
        task = _task(live, live.elsewhere, CREATOR, "op8 weekly report")
        r = await _as(live, CREATOR, "PUT", f"/projects/tasks/{task}/recurrence",
                      {"freq": "daily", "interval": 1, "anchor": "completed"})
        assert r.status_code == 200, r.text
        r = await _as(live, CREATOR, "PUT", f"/projects/tasks/{task}/assignees",
                      {"assignees": [VIEWER]})
        assert r.status_code == 200, r.text
        r = await _as(live, VIEWER, "PATCH", f"/projects/tasks/{task}",
                      {"status_id": done})
        assert r.status_code == 200, r.text
        with live.admin.begin() as c:
            successor = c.execute(text(
                "SELECT id, created_by FROM pm_tasks WHERE recurrence_id = "
                "(SELECT recurrence_id FROM pm_tasks WHERE id = CAST(:t AS uuid))"
                " AND id <> CAST(:t AS uuid)"), {"t": task}).one()
        assert successor.created_by == CREATOR
        r = await _as(live, VIEWER, "DELETE", f"/projects/tasks/{successor.id}")
        assert r.status_code == 403 and r.json()["detail"] == a.TASK_DELETE_REFUSED, r.text
        assert _exists(live, "pm_tasks", str(successor.id))
