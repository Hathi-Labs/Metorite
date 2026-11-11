"""SW-45 — own apps in ``my_work`` and the scope of an agent claim (R8).

Spec: ``project-docs/specs/agent_coordination.md`` §3.10.1 and SW-45.

Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_agent_claim_scope_r8.py -v -rs

The MCP tools run on the real edge, over the real gateway app, as a
non-privileged role, on a promoted catalog of their own. The member leads one
app, ``Mine``, with a subproject below it. ``Theirs`` has another lead, and
``Loose`` has no lead. The member holds a view grant on each of the three. A
grant gives a view, not a write, so only ``Mine`` is an own app. A second
organization has an app that the member's address leads.

The acceptance (SW-45, Done when):

* A member with one own app gets that app's free tasks in ``own_app_free``.
* A claim outside it warns in ``warn``, gets 403 in ``refuse`` and passes in
  ``off``. An assigned task passes in every mode.
* A task of another organization never appears.

Mutations (R7). ``G`` is ``apps/services/gateway/gateway/routes/projects``.

* M1. ``G/claim_scope.py`` ``check_claim_scope`` returns ``None`` at once.
  ``test_warn_claims_names_the_lead_and_writes_one_line``,
  ``test_refuse_answers_403_and_writes_nothing``.
* M2. ``G/claim_scope.py`` ``check_claim_scope`` drops the ``current`` test.
  ``test_an_assigned_task_passes_in_every_mode[refuse]``.
* M3. ``G/authority.py`` ``app_owner`` reads the project's own lead only, with
  no walk. ``test_a_subproject_of_an_own_app_passes_in_refuse``.
* M4. ``G/claim_scope.py`` ``_free_sql`` drops the assignee test.
  ``test_the_free_tasks_of_the_own_app``.
* M5. ``G/claim_scope.py`` ``own_app_free`` skips ``scope_mode``.
  ``test_with_the_mode_off_my_work_has_no_free_row``.
* M6. ``G/tasks.py`` ``claim_task`` writes the timeline line when nothing was
  added. ``test_warn_claims_names_the_lead_and_writes_one_line`` (two lines).
* M7. ``G/claim_scope.py`` ``check_claim_scope`` drops the agent test.
  ``test_a_member_claim_is_never_checked``.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_common import get_settings
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests.unit import test_mcp_edge_task_writes as tw
from tests.unit import test_mcp_edge_writes as w

_DB_GATE = w._DB_GATE
MEMBER = w.MEMBER
LEAD = "lead@mcpwrites.test"
OTHER = "other@mcpwrites.test"
FREE = "/projects/my/own-app-free"

_APP_ROLE = "acb_app_claimscope"
_APP_PW = "claimscope_scratch_pw"

redis_fake = tw.redis_fake
live_app = tw.live_app
live = tw.live
_send = tw._send
_row = tw._row


def _sub(a, name: str, parent: str) -> str:
    """A subproject that keeps the lanes of its parent."""
    return str(a.one(
        "INSERT INTO pm_projects (name, status, source, created_by, organization_id,"
        " timezone, owns_statuses, parent_project_id) VALUES (:n, 'active', 'manual',"
        " :a, CAST(:o AS uuid), 'Asia/Kolkata', false, CAST(:p AS uuid)) RETURNING id",
        n=name, a=w.OWNER, o=a.org, p=parent))


def _lead(a, project: str, who: str | None) -> None:
    a.one("UPDATE pm_projects SET lead = :w WHERE id = CAST(:p AS uuid) RETURNING id",
          w=who, p=project)


def _lanes(a, project: str) -> SimpleNamespace:
    return SimpleNamespace(
        todo=a.status(project, "To do", "todo", 0),
        doing=a.status(project, "Doing", "in_progress", 1),
        done=a.status(project, "Done", "done", 2))


@pytest.fixture(scope="module")
def world():
    """A private promoted catalog: two tenants, one member, four apps."""
    if not w._URL:
        pytest.skip("TENANT_LADDER_DATABASE_URL unset")
    admin_url = make_url(w._URL)
    dedicated = f"{admin_url.database}_claimscope"
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

    org, org_b = str(uuid.uuid4()), str(uuid.uuid4())
    with eng.begin() as c:
        for oid, slug in ((org, "claimscope"), (org_b, "claimscopeb")):
            c.execute(text("INSERT INTO organization (id, slug, display_name) "
                           "VALUES (:id, :s, :s)"), {"id": oid, "s": slug})
    a = w._Seeder(eng, org)
    a.number = 400_000
    a.member(MEMBER, ["feature:projects"])
    mine, theirs, loose = a.project("Mine"), a.project("Theirs"), a.project("Loose")
    mine_sub = _sub(a, "Mine sub", mine)
    _lead(a, mine, MEMBER.upper())
    _lead(a, theirs, LEAD)
    # The other org: an app that the member's address leads, with a free task.
    b = w._Seeder(eng, org_b)
    b.number = 500_000
    b_app = b.project("B app")
    _lead(b, b_app, MEMBER)
    b_lanes = _lanes(b, b_app)
    b_task = b.task(b_app, b_lanes.todo, "B free")
    world_ = SimpleNamespace(
        admin=eng, org=org, seeder=a, mine=mine, mine_sub=mine_sub, theirs=theirs,
        loose=loose, s={p: _lanes(a, p) for p in (mine, theirs, loose)},
        b=SimpleNamespace(seeder=b, app=b_app, task=b_task), c={}, tok={})
    world_.c["write"], world_.tok["write"] = a.connection(scopes=w.WRITE_SCOPES)
    world_.app_dsn = admin_url.set(
        database=dedicated, username=_APP_ROLE, password=_APP_PW,
    ).render_as_string(hide_password=False)
    yield world_

    eng.dispose()
    w._drop_database(admin_url, dedicated)


@pytest.fixture
def mode(monkeypatch, world):
    """Set the claim scope of the world's organization."""
    def set_(value: str) -> None:
        monkeypatch.setattr(get_settings(), "agent_claim_scope_modes",
                            f"{world.org}={value}" if value else "")
    return set_


def _task(world, project: str, title: str, *, status: str = "todo",
          importance: int | None = None, leveraged: bool = False) -> str:
    root = world.mine if project == world.mine_sub else project
    lane = getattr(world.s[root], status)
    world.seeder.number += 1
    return str(world.seeder.one(
        "INSERT INTO pm_tasks (title, project_id, root_project_id, status_id,"
        " created_by, organization_id, task_number, importance, leveraged) VALUES"
        " (:t, CAST(:p AS uuid), CAST(:r AS uuid), CAST(:s AS uuid), :a,"
        " CAST(:o AS uuid), :n, :i, :l) RETURNING id",
        t=title, p=project, r=root, s=lane, a=w.OWNER, o=world.org,
        n=world.seeder.number, i=importance, l=leveraged))


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


def _scope_lines(world, task: str) -> list[Any]:
    with world.admin.begin() as c:
        return list(c.execute(text(
            "SELECT * FROM pm_activities WHERE task_id = CAST(:t AS uuid)"
            " AND type = 'system' AND meta->>'kind' = 'claim_outside_apps'"),
            {"t": task}))


async def _mcp(world, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return await tw._mcp(world, "write", tool, arguments)


async def _version(world, task: str) -> str:
    r = await _send(world, "GET", f"/projects/tasks/{task}")
    assert r.status_code == 200, r.text
    return r.json()["updated_at"]


async def _claim(world, task: str) -> dict[str, Any]:
    return await _mcp(world, "claim_task", {
        "task_id": task, "expected_version": await _version(world, task)})


async def _free_rows(world) -> list[dict[str, Any]]:
    result = await _mcp(world, "my_work", {"limit": 25})
    assert result["isError"] is False, result
    return [r for r in result["structuredContent"]["rows"] if r["reason"] == "own_app_free"]


class TestOwnAppFree:
    pytestmark = _DB_GATE

    async def test_the_free_tasks_of_the_own_app(self, live, mode):
        mode("warn")
        best = _task(live, live.mine, "best", importance=3, leveraged=True)
        sub = _task(live, live.mine_sub, "sub", importance=3)
        taken = _task(live, live.mine, "taken", importance=3, leveraged=True)
        _assign(live, taken, OTHER)
        _task(live, live.mine, "closed", status="done", importance=3, leveraged=True)
        outside = _task(live, live.theirs, "outside", importance=3, leveraged=True)
        rows = await _free_rows(live)
        ids = [r["task_id"] for r in rows]
        assert ids[:2] == [best, sub], rows
        assert taken not in ids and outside not in ids
        assert len(rows) <= 5
        # The nearest own app of a subproject task is the app above it.
        app = {r["task_id"]: r["app_project"] for r in rows}
        assert app[best] == {"id": live.mine, "name": {"untrusted_text": "Mine"}}
        assert app[sub]["id"] == live.mine

    async def test_another_orgs_task_never_appears(self, live, mode):
        mode("warn")
        rows = await _free_rows(live)
        assert live.b.task not in [r["task_id"] for r in rows]
        r = await _send(live, "GET", FREE)
        assert r.status_code == 200, r.text
        assert all(row["project_id"] != live.b.app for row in r.json()["rows"])
        assert live.b.task not in [row["id"] for row in r.json()["rows"]]

    async def test_with_the_mode_off_my_work_has_no_free_row(self, live, mode):
        mode("")
        _task(live, live.mine, "dark", importance=3, leveraged=True)
        assert await _free_rows(live) == []
        r = await _send(live, "GET", FREE)
        assert r.status_code == 200 and r.json() == {"rows": []}


class TestTheClaimScope:
    pytestmark = _DB_GATE

    async def test_warn_claims_names_the_lead_and_writes_one_line(self, live, mode):
        mode("warn")
        task = _task(live, live.theirs, "warn me")
        result = await _claim(live, task)
        assert result["isError"] is False, result
        assert result["structuredContent"]["warning"] == (
            f"This task is outside your apps. Its app lead is {LEAD}."
            " The task timeline records this claim.")
        assert _assignees(live, task) == [MEMBER]
        [line] = _scope_lines(live, task)
        assert line.meta["lead"] == LEAD
        # A second claim adds no second line.
        again = await _claim(live, task)
        assert again["isError"] is False, again
        assert len(_scope_lines(live, task)) == 1

    async def test_warn_on_an_app_with_no_lead(self, live, mode):
        mode("warn")
        task = _task(live, live.loose, "loose")
        body = (await _claim(live, task))["structuredContent"]
        assert body["warning"].startswith("This task is outside your apps, and its app has no lead.")

    async def test_refuse_answers_403_and_writes_nothing(self, live, mode):
        mode("refuse")
        task = _task(live, live.theirs, "refuse me")
        before = _row(live, task)
        result = await _claim(live, task)
        assert result["isError"] is True, result
        assert result["content"][0]["text"] == (
            f"This task is outside your apps. Ask its app lead, {LEAD},"
            " to assign it to you, or take a task from my_work.")
        assert _assignees(live, task) == []
        assert _scope_lines(live, task) == []
        assert str(_row(live, task).status_id) == str(before.status_id)
        r = await _send(live, "POST", f"/projects/tasks/{task}/claim", body={},
                        headers={"If-Match": await _version(live, task)})
        assert r.status_code == 403
        assert r.json()["detail"]["error"] == "outside_apps"

    async def test_off_passes(self, live, mode):
        mode("off")
        task = _task(live, live.theirs, "off")
        result = await _claim(live, task)
        assert result["isError"] is False, result
        assert "warning" not in result["structuredContent"]
        assert _scope_lines(live, task) == []

    @pytest.mark.parametrize("value", ["off", "warn", "refuse"])
    async def test_an_assigned_task_passes_in_every_mode(self, live, mode, value):
        mode(value)
        task = _task(live, live.theirs, f"assigned {value}")
        _assign(live, task, MEMBER)
        result = await _claim(live, task)
        assert result["isError"] is False, result
        assert "warning" not in result["structuredContent"]
        assert _scope_lines(live, task) == []

    async def test_a_subproject_of_an_own_app_passes_in_refuse(self, live, mode):
        mode("refuse")
        task = _task(live, live.mine_sub, "inside")
        result = await _claim(live, task)
        assert result["isError"] is False, result
        assert "warning" not in result["structuredContent"]
        assert _assignees(live, task) == [MEMBER]

    async def test_a_member_claim_is_never_checked(self, live, mode):
        mode("refuse")
        task = _task(live, live.theirs, "by hand")
        r = await _send(live, "POST", f"/projects/tasks/{task}/claim", body={},
                        headers={"If-Match": await _version(live, task)}, signed=None)
        assert r.status_code == 200, r.text
        assert "warning" not in r.json()

    async def test_another_orgs_task_is_not_found(self, live, mode):
        mode("refuse")
        result = await _mcp(live, "claim_task", {
            "task_id": live.b.task, "expected_version": "2026-01-01T00:00:00+00:00"})
        assert result["isError"] is True
        assert result["content"][0]["text"] == "Task not found."
