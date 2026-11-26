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
* M2. ``G/claim_scope.py`` ``check_claim_scope`` drops the ``holds_task``
  test. ``test_an_assigned_task_passes_in_every_mode[refuse]``.
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
* M8. ``G/tasks.py`` ``set_assignees`` skips ``check_claim_scope``.
  ``test_a_self_add_outside_is_refused``, ``test_a_self_add_outside_warns``.
* M9. ``G/agent_runs.py`` ``start_agent_run`` skips ``check_claim_scope``.
  ``test_an_agent_start_outside_is_refused``,
  ``test_an_agent_start_outside_warns``.
* M10. ``G/claim_scope.py`` ``_free_sql`` drops the project archive test.
  ``test_a_task_of_a_filed_project_is_not_free``.
* M11. ``G/claim_scope.py`` ``_app_names`` drops the visibility clause.
  ``test_a_pinned_connection_reads_no_app_outside_its_pins``.
* M12. ``G/claim_scope.py`` ``check_claim_scope`` calls ``app_owner`` with no
  ``vis``. ``test_a_pinned_connection_reads_no_lead_outside_its_pins``.

* M13. ``G/claim_scope.py`` ``check_claim_scope`` passes a task with ANY
  assignee, as it did before review P1 of 2026-11-17.
  ``test_assign_an_agent_then_claim_is_refused``,
  ``test_assign_an_agent_then_start_is_refused``,
  ``test_assign_an_agent_then_self_add_is_refused``,
  ``test_assign_a_colleague_with_a_grant_then_claim_is_refused``,
  ``test_a_self_add_beside_another_member_is_refused``.
* M14. ``G/tasks.py`` ``set_assignees`` checks only a task with no assignee.
  ``test_assign_an_agent_then_self_add_is_refused``.

A pinned connection reaches its pins and the projects below them, and no
parent (review P2). ``pin_mine`` is pinned to ``Mine sub``, and
``pin_theirs`` to ``Theirs sub``, a child of ``Theirs`` with no lead.
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
    world_.theirs_sub = _sub(a, "Theirs sub", theirs)
    world_.c["write"], world_.tok["write"] = a.connection(scopes=w.WRITE_SCOPES)
    world_.c["pin_mine"], world_.tok["pin_mine"] = a.connection(
        scopes=w.WRITE_SCOPES, pins=(mine_sub,))
    world_.c["pin_theirs"], world_.tok["pin_theirs"] = a.connection(
        scopes=w.WRITE_SCOPES, pins=(world_.theirs_sub,))
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
          importance: int | None = None, leveraged: bool = False,
          root: str | None = None) -> str:
    if root is None:
        root = world.mine if project == world.mine_sub else project
    if project == world.theirs_sub:
        root = world.theirs
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


def _runs(world, task: str) -> list[Any]:
    with world.admin.begin() as c:
        return list(c.execute(text(
            "SELECT id FROM pm_agent_runs WHERE task_id = CAST(:t AS uuid)"), {"t": task}))


async def _self_add(world, task: str) -> Any:
    return await _send(world, "PUT", f"/projects/tasks/{task}/assignees",
                       body={"assignees": [MEMBER]},
                       headers={"If-Match": await _version(world, task)})


async def _start(world, task: str) -> Any:
    return await _send(world, "POST", f"/projects/tasks/{task}/agent-runs", body={})


@pytest.fixture
def runs_on(monkeypatch):
    monkeypatch.setattr(get_settings(), "agent_runs_enabled", True)


class TestTheSecondDoors:
    """Review P1: ``refuse`` has no second door through ``set_assignees`` or
    ``start_agent_run``. ONE check, ``claim_scope.check_claim_scope``."""

    pytestmark = _DB_GATE

    async def test_a_self_add_outside_is_refused(self, live, mode):
        mode("refuse")
        task = _task(live, live.theirs, "self add refuse")
        r = await _self_add(live, task)
        assert r.status_code == 403, r.text
        assert r.json()["detail"]["error"] == "outside_apps"
        assert r.json()["detail"]["lead"] == LEAD
        assert _assignees(live, task) == []
        assert _scope_lines(live, task) == []
        # So the claim after it is still checked.
        claim = await _send(live, "POST", f"/projects/tasks/{task}/claim", body={},
                            headers={"If-Match": await _version(live, task)})
        assert claim.status_code == 403, claim.text

    async def test_a_self_add_outside_warns(self, live, mode):
        mode("warn")
        task = _task(live, live.theirs, "self add warn")
        r = await _self_add(live, task)
        assert r.status_code == 200, r.text
        assert r.json()["warning"] == (
            f"This task is outside your apps. Its app lead is {LEAD}."
            " The task timeline records this claim.")
        assert r.json()["outside_apps"] == {"lead": LEAD}
        assert _assignees(live, task) == [MEMBER]
        assert len(_scope_lines(live, task)) == 1
        again = await _self_add(live, task)
        assert again.status_code == 200, again.text
        assert "warning" not in again.json()
        assert len(_scope_lines(live, task)) == 1

    async def test_a_self_add_beside_another_member_is_refused(self, live, mode):
        """Review P1 of 2026-11-17: another member on the task is not a pass.
        Only the session member is (§3.10.1)."""
        mode("refuse")
        task = _task(live, live.theirs, "self add assigned")
        _assign(live, task, OTHER)
        r = await _send(live, "PUT", f"/projects/tasks/{task}/assignees",
                        body={"assignees": [OTHER, MEMBER]},
                        headers={"If-Match": await _version(live, task)})
        assert r.status_code == 403, r.text
        assert r.json()["detail"]["error"] == "outside_apps"
        assert _assignees(live, task) == [OTHER]

    async def test_a_task_that_holds_the_member_passes_in_refuse(self, live, mode):
        mode("refuse")
        task = _task(live, live.theirs, "holds the member")
        _assign(live, task, MEMBER)
        r = await _send(live, "PUT", f"/projects/tasks/{task}/assignees",
                        body={"assignees": [MEMBER, "agent:helper"]},
                        headers={"If-Match": await _version(live, task)})
        assert r.status_code == 200, r.text
        assert "warning" not in r.json()
        assert _assignees(live, task) == sorted([MEMBER, "agent:helper"])
        claim = await _claim(live, task)
        assert claim["isError"] is False, claim
        assert _scope_lines(live, task) == []

    async def test_an_agent_start_outside_is_refused(self, live, mode, runs_on):
        mode("refuse")
        task = _task(live, live.theirs, "start refuse")
        r = await _start(live, task)
        assert r.status_code == 403, r.text
        assert r.json()["detail"]["error"] == "outside_apps"
        assert _runs(live, task) == []
        assert _scope_lines(live, task) == []

    async def test_an_agent_start_outside_warns(self, live, mode, runs_on):
        mode("warn")
        task = _task(live, live.theirs, "start warn")
        r = await _start(live, task)
        assert r.status_code == 201, r.text
        assert r.json()["outside_apps"] == {"lead": LEAD}
        assert r.json()["warning"].startswith("This task is outside your apps.")
        assert len(_runs(live, task)) == 1
        assert len(_scope_lines(live, task)) == 1

    async def test_an_agent_start_inside_passes_in_refuse(self, live, mode, runs_on):
        mode("refuse")
        task = _task(live, live.mine_sub, "start inside")
        r = await _start(live, task)
        assert r.status_code == 201, r.text
        assert "warning" not in r.json()


async def _put(world, task: str, people: list[str]) -> Any:
    return await _send(world, "PUT", f"/projects/tasks/{task}/assignees",
                       body={"assignees": people},
                       headers={"If-Match": await _version(world, task)})


def _colleague(world) -> str:
    """OTHER as an active member with a grant on ``Theirs``, once."""
    if not getattr(world, "colleague", False):
        world.seeder.member(OTHER, ["feature:projects"])
        world.seeder.one(
            "INSERT INTO pm_project_grants (project_id, subject, created_by,"
            " organization_id) VALUES (CAST(:p AS uuid), :s, :a, CAST(:o AS uuid))"
            " RETURNING project_id", p=world.theirs, s=OTHER, a=w.OWNER, o=world.org)
        world.colleague = True
    return OTHER


class TestTheTwoStepDoors:
    """Review P1 of 2026-11-17. An agent assigns somebody else first, and
    then holds the task. Another assignee is never a pass. Only the session
    member on the task is (§3.10.1 and SW-45)."""

    pytestmark = _DB_GATE

    async def test_assign_an_agent_then_claim_is_refused(self, live, mode):
        mode("refuse")
        task = _task(live, live.theirs, "two step claim")
        first = await _put(live, task, ["agent:x"])
        assert first.status_code == 200, first.text
        claim = await _claim(live, task)
        assert claim["isError"] is True, claim
        assert claim["content"][0]["text"].startswith("This task is outside your apps.")
        assert _assignees(live, task) == ["agent:x"]
        assert _scope_lines(live, task) == []

    async def test_assign_an_agent_then_claim_warns_and_writes_one_line(self, live, mode):
        mode("warn")
        task = _task(live, live.theirs, "two step warn")
        assert (await _put(live, task, ["agent:x"])).status_code == 200
        claim = await _claim(live, task)
        assert claim["isError"] is False, claim
        assert claim["structuredContent"]["warning"] == (
            f"This task is outside your apps. Its app lead is {LEAD}."
            " The task timeline records this claim.")
        assert _assignees(live, task) == sorted(["agent:x", MEMBER])
        assert len(_scope_lines(live, task)) == 1

    async def test_assign_an_agent_then_start_is_refused(self, live, mode, runs_on):
        mode("refuse")
        task = _task(live, live.theirs, "two step start")
        assert (await _put(live, task, ["agent:x"])).status_code == 200
        r = await _start(live, task)
        assert r.status_code == 403, r.text
        assert r.json()["detail"]["error"] == "outside_apps"
        assert _runs(live, task) == []
        assert _scope_lines(live, task) == []

    async def test_assign_an_agent_then_self_add_is_refused(self, live, mode):
        mode("refuse")
        task = _task(live, live.theirs, "two step self add")
        assert (await _put(live, task, ["agent:x"])).status_code == 200
        r = await _put(live, task, [MEMBER])
        assert r.status_code == 403, r.text
        assert r.json()["detail"]["error"] == "outside_apps"
        assert _assignees(live, task) == ["agent:x"]
        assert _scope_lines(live, task) == []

    async def test_assign_a_colleague_with_a_grant_then_claim_is_refused(self, live, mode):
        mode("refuse")
        task = _task(live, live.theirs, "two step colleague")
        first = await _put(live, task, [_colleague(live)])
        assert first.status_code == 200, first.text
        claim = await _claim(live, task)
        assert claim["isError"] is True, claim
        assert _assignees(live, task) == [OTHER]
        r = await _put(live, task, [OTHER, MEMBER])
        assert r.status_code == 403, r.text
        assert _assignees(live, task) == [OTHER]


class TestTheReviewReads:
    pytestmark = _DB_GATE

    async def test_a_task_of_a_filed_project_is_not_free(self, live, mode):
        """Review P1: ``archive_node`` stamps the project, not its tasks."""
        mode("warn")
        filed = _sub(live.seeder, "Mine filed", live.mine)
        task = _task(live, filed, "filed", importance=3, leveraged=True, root=live.mine)
        live.seeder.one("UPDATE pm_projects SET archived_at = now()"
                        " WHERE id = CAST(:p AS uuid) RETURNING id", p=filed)
        r = await _send(live, "GET", FREE)
        assert r.status_code == 200, r.text
        assert task not in [row["id"] for row in r.json()["rows"]]

    async def test_a_pinned_connection_reads_no_app_outside_its_pins(self, live, mode):
        """Review P2: the pin is ``Mine sub``, so ``Mine`` is out of reach."""
        mode("warn")
        task = _task(live, live.mine_sub, "pinned free", importance=3, leveraged=True)
        r = await _send(live, "GET", FREE, signed="pin_mine")
        assert r.status_code == 200, r.text
        rows = {row["id"]: row for row in r.json()["rows"]}
        assert task in rows, rows
        assert rows[task]["app_project"] == {"id": None, "name": None}
        # The task row's own `root_project_id` is the task model's, outside
        # SW-45. The app is what this test pins.
        # The unpinned connection still names the app.
        full = await _send(live, "GET", FREE)
        named = {row["id"]: row for row in full.json()["rows"]}
        assert named[task]["app_project"] == {"id": live.mine, "name": "Mine"}

    @pytest.mark.parametrize("value", ["warn", "refuse"])
    async def test_a_pinned_connection_reads_no_lead_outside_its_pins(
            self, live, mode, value):
        """Review P2: the lead of ``Theirs`` is above the pin ``Theirs sub``."""
        mode(value)
        task = _task(live, live.theirs_sub, f"pinned lead {value}")
        r = await _send(live, "POST", f"/projects/tasks/{task}/claim", body={},
                        headers={"If-Match": await _version(live, task)},
                        signed="pin_theirs")
        assert LEAD not in r.text
        if value == "refuse":
            assert r.status_code == 403, r.text
            assert r.json()["detail"]["lead"] is None
        else:
            assert r.status_code == 200, r.text
            assert r.json()["outside_apps"] == {"lead": None}
            [line] = _scope_lines(live, task)
            assert line.meta["lead"] is None


# ── Fix round of 2026-11-26: three more passes for the session member ──────


def _queue(world, task: str, *, signed_as: str = "write") -> Any:
    """The member start of ``start_agent_run`` (``via is None``) on the
    connection *signed_as*: a run that a person queued for the member."""
    return _send(world, "POST", f"/projects/tasks/{task}/agent-runs",
                 body={"connection_id": world.c[signed_as], "body": "Do this task."},
                 signed=None)


def _run_row(world, task: str, **cols: Any) -> None:
    """A run row, written by the admin with the tenant GUC set."""
    names = ", ".join(cols)
    marks = ", ".join(f":{k}" for k in cols)
    world.seeder.one(
        f"INSERT INTO pm_agent_runs (task_id, {names}) VALUES (CAST(:t AS uuid), {marks})"
        " RETURNING id", t=task, **cols)


def _personal(world) -> SimpleNamespace:
    """The member's personal root and an Area below it, and the personal root
    of OTHER with a view grant to the member. Made once."""
    if getattr(world, "personal", None) is None:
        a = world.seeder

        def root(owner: str, name: str) -> str:
            pid = str(a.one(
                "INSERT INTO pm_projects (name, status, source, created_by, organization_id,"
                " timezone, owns_statuses, personal_owner) VALUES (:n, 'active', 'manual',"
                " :w, CAST(:o AS uuid), 'Asia/Kolkata', true, :w) RETURNING id",
                n=name, w=owner, o=world.org))
            a.one("INSERT INTO pm_project_grants (project_id, subject, created_by,"
                  " organization_id) VALUES (CAST(:p AS uuid), :s, :w, CAST(:o AS uuid))"
                  " RETURNING project_id", p=pid, s=MEMBER, w=owner, o=world.org)
            world.s[pid] = _lanes(a, pid)
            return pid

        mine = root(MEMBER, "Inbox")
        # The Area carries no stamp, so the check walks up to the root.
        area = _sub(a, "Area", mine)
        a.one("INSERT INTO pm_project_grants (project_id, subject, created_by,"
              " organization_id) VALUES (CAST(:p AS uuid), :s, :s, CAST(:o AS uuid))"
              " RETURNING project_id", p=area, s=MEMBER, o=world.org)
        # A team node below the tree: no stamp, and a grant to OTHER. The one
        # walk of a personal tree stops there (migration 297), so it is team work.
        team = _sub(a, "Shared", mine)
        a.one("INSERT INTO pm_project_grants (project_id, subject, created_by,"
              " organization_id) VALUES (CAST(:p AS uuid), :s, :m, CAST(:o AS uuid))"
              " RETURNING project_id", p=team, s=OTHER, m=MEMBER, o=world.org)
        world.personal = SimpleNamespace(root=mine, area=area, team=team,
                                         other=root(OTHER, "Other inbox"))
    return world.personal


async def _hold(world, door: str, task: str) -> tuple[bool, str]:
    """``(passed, text)`` of an agent hold through *door*: the MCP
    ``claim_task`` or the agent's own ``start_agent_run``."""
    if door == "claim":
        result = await _claim(world, task)
        return (not result["isError"]), result["content"][0]["text"]
    r = await _start(world, task)
    if r.status_code in (200, 201):
        return True, r.text
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["error"] == "outside_apps", r.text
    return False, r.text


async def _refused(world, door: str, task: str) -> None:
    """The hold through *door* gets the claim scope refusal, and no other one
    (a 409 ``task_held`` of the gate is not this refusal)."""
    passed, text_ = await _hold(world, door, task)
    assert not passed, text_
    assert "This task is outside your apps." in text_ or "outside_apps" in text_, text_


DOORS = ("claim", "start")


class TestTheMemberPasses:
    """Each pass in mode ``refuse``, on both doors, with a negative twin.

    Mutations (R7). ``G`` as above.

    * M15. ``G/claim_scope.py`` ``check_claim_scope`` drops
      ``queued_by_member``. ``test_a_run_the_member_queued_passes``.
    * M16. ``G/claim_scope.py`` ``_QUEUED_RUN_SQL`` drops the
      ``started_by`` arm. ``test_a_run_an_agent_started_does_not_pass``.
    * M17. ``G/claim_scope.py`` ``_QUEUED_RUN_SQL`` drops the member arm and
      the ``started_by`` arm. ``test_a_colleagues_queued_run_does_not_pass``.
      Each arm alone refuses the colleague's run, because only the member
      start writes a run, and it writes the same email in both columns.
    * M18. ``G/claim_scope.py`` ``QUEUED_RUN_STATUSES`` is
      ``AGENT_RUN_OPEN_STATUSES`` or every status.
      ``test_an_ended_run_does_not_pass``.
    * M19. ``G/claim_scope.py`` ``_QUEUED_RUN_SQL`` drops the connection arm.
      ``test_a_run_queued_on_another_connection_does_not_pass``.
    * M20. ``G/claim_scope.py`` ``check_claim_scope`` drops
      ``in_own_personal_tree``. ``test_the_own_personal_tree_passes``.
    * M21. ``G/authority.py`` ``in_own_personal_tree`` answers True for
      every node. ``test_a_team_node_below_the_tree_does_not_pass``.
    * M22. ``G/claim_scope.py`` ``check_claim_scope`` drops
      ``made_by_member``. ``test_a_task_the_member_created_passes``.
    * M23. ``G/authority.py`` ``made_by_member`` answers True for any
      creator. ``test_a_task_another_member_created_does_not_pass``.
    """

    pytestmark = _DB_GATE

    @pytest.mark.parametrize("door", DOORS)
    async def test_a_run_the_member_queued_passes(self, live, mode, runs_on, door):
        mode("refuse")
        task = _task(live, live.theirs, f"queued {door}")
        queued = await _queue(live, task)
        assert queued.status_code == 201, queued.text
        passed, text_ = await _hold(live, door, task)
        assert passed, text_
        assert _scope_lines(live, task) == []
        assert len(_runs(live, task)) == 1

    @pytest.mark.parametrize("door", DOORS)
    async def test_a_colleagues_queued_run_does_not_pass(self, live, mode, runs_on, door):
        """OTHER's run, even on the member's own connection."""
        mode("refuse")
        task = _task(live, live.theirs, f"colleague run {door}")
        _run_row(live, task, member_email=OTHER, connection_id=live.c["write"],
                 client_via=tw.R8_VIA, status="created", started_by=OTHER)
        await _refused(live, door, task)
        assert _assignees(live, task) == []

    @pytest.mark.parametrize("door", DOORS)
    async def test_a_run_an_agent_started_does_not_pass(self, live, mode, runs_on, door):
        """A start in ``warn`` leaves the agent's own run. In ``refuse`` it
        opens no way in, so ``ask_on_task`` cannot either."""
        mode("warn")
        task = _task(live, live.theirs, f"agent run {door}")
        first = await _start(live, task)
        assert first.status_code == 201, first.text
        mode("refuse")
        await _refused(live, door, task)
        assert _assignees(live, task) == []

    async def test_ask_on_task_outside_is_refused(self, live, mode, runs_on):
        mode("refuse")
        task = _task(live, live.theirs, "ask outside")
        result = await _mcp(live, "ask_on_task", {
            "task_id": task, "question": "Which one?", "idempotency_key": "ask-outside-1"})
        assert result["isError"] is True, result
        assert result["content"][0]["text"].startswith("This task is outside your apps.")
        assert _runs(live, task) == []
        # The refused ask left no run, so the claim is refused too.
        claim = await _claim(live, task)
        assert claim["isError"] is True, claim

    @pytest.mark.parametrize("status", ["completed", "released", "stopping"])
    async def test_an_ended_run_does_not_pass(self, live, mode, runs_on, status):
        mode("refuse")
        task = _task(live, live.theirs, f"ended {status}")
        queued = await _queue(live, task)
        assert queued.status_code == 201, queued.text
        live.seeder.one("UPDATE pm_agent_runs SET status = :s,"
                        " ended_at = CASE WHEN :s = 'stopping' THEN NULL ELSE now() END"
                        " WHERE task_id = CAST(:t AS uuid) RETURNING id", s=status, t=task)
        await _refused(live, "claim", task)
        assert _assignees(live, task) == []

    async def test_a_run_queued_on_another_connection_does_not_pass(self, live, mode,
                                                                    runs_on):
        mode("refuse")
        task = _task(live, live.theirs, "queued elsewhere")
        queued = await _queue(live, task, signed_as="pin_mine")
        assert queued.status_code == 201, queued.text
        await _refused(live, "claim", task)

    @pytest.mark.parametrize("door", DOORS)
    @pytest.mark.parametrize("where", ["root", "area"])
    async def test_the_own_personal_tree_passes(self, live, mode, runs_on, door, where):
        mode("refuse")
        tree = _personal(live)
        project = getattr(tree, where)
        task = _task(live, project, f"personal {where} {door}", root=tree.root)
        passed, text_ = await _hold(live, door, task)
        assert passed, text_
        assert _scope_lines(live, task) == []

    @pytest.mark.parametrize("door", DOORS)
    async def test_a_team_node_below_the_tree_does_not_pass(self, live, mode,
                                                            runs_on, door):
        """The pass reads the one walk of a personal tree, so a team node
        below the member's tree is outside it."""
        mode("refuse")
        tree = _personal(live)
        task = _task(live, tree.team, f"team node {door}", root=tree.root)
        await _refused(live, door, task)
        assert _assignees(live, task) == []

    async def test_another_members_personal_project_does_not_pass(self, live, mode,
                                                                  runs_on):
        """OTHER's personal tree is private, also with a grant to the member.
        The visibility load answers 404 before the scope check, so neither
        door holds the task."""
        mode("refuse")
        tree = _personal(live)
        task = _task(live, tree.other, "other personal", root=tree.other)
        claim = await _send(live, "POST", f"/projects/tasks/{task}/claim", body={})
        assert claim.status_code == 404, claim.text
        start = await _start(live, task)
        assert start.status_code == 404, start.text
        assert _assignees(live, task) == [] and _runs(live, task) == []

    @pytest.mark.parametrize("door", DOORS)
    async def test_a_task_the_member_created_passes(self, live, mode, runs_on, door):
        mode("refuse")
        task = _task(live, live.theirs, f"mine made {door}")
        live.seeder.one("UPDATE pm_tasks SET created_by = :w WHERE id = CAST(:t AS uuid)"
                        " RETURNING id", w=MEMBER.upper(), t=task)
        passed, text_ = await _hold(live, door, task)
        assert passed, text_
        assert _scope_lines(live, task) == []

    @pytest.mark.parametrize("door", DOORS)
    async def test_a_task_another_member_created_does_not_pass(self, live, mode,
                                                               runs_on, door):
        mode("refuse")
        task = _task(live, live.theirs, f"other made {door}")
        live.seeder.one("UPDATE pm_tasks SET created_by = :w WHERE id = CAST(:t AS uuid)"
                        " RETURNING id", w=OTHER, t=task)
        await _refused(live, door, task)
