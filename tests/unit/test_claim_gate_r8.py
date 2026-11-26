"""LBB-2 — the claim gate of "land before build" (R8).

Spec: ``project-docs/specs/engineering_practice.md`` §7b.2, task 4041.

Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_claim_gate_r8.py -v -rs

The gateway app runs over a promoted catalog of its own, as a
non-privileged role (no superuser, no owner, no BYPASSRLS). An agent of one
member calls the three doors, signed for one connection. The MCP tools run on
the real edge over the same app. Each test uses its own agent label, and a
fixture marks every older PR ``gone`` first, so one test never counts the
PRs of another.

The acceptance ("Done when" of §7b.2):

1. With both flags off, no claim, assignee or run start reads the query of
   the gate (:class:`TestDark`). The old tests of the three doors run as they
   are, in their own files.
2. With ``CLAIM_GATE_ORGS`` on, an agent claim of a new task is refused, 422
   ``land_your_pr``, for a label that holds a counted PR. The body and the
   edge text name each PR, its age, its task and the next step.
3. Each exemption of trap 2 passes with the flag on.
4. ``my_work`` lists the ``land_your_pr`` row (its place: the edge file).
5. ``hand_over.sh`` prints the call (``test_hand_over.py``).

Mutations (R7). ``G`` is ``apps/services/gateway/gateway/routes/projects``.

* C1. ``G/claim_gate.py`` ``_counted_sql`` drops the label test.
  ``test_another_label_is_not_counted``.
* C2. ``G/claim_gate.py`` ``_counted_sql`` reads the OLDEST run.
  ``test_the_newest_run_decides_the_label``.
* C3. ``G/claim_gate.py`` ``_counted_sql`` drops the ``awaiting`` test.
  ``test_a_task_that_waits_on_a_person_is_not_counted``.
* C4. ``G/claim_gate.py`` ``_counted_sql`` drops the queued expiry.
  ``test_the_landing_marks``.
* C5. ``G/claim_gate.py`` ``_EXEMPT_TASK_SQL`` compares the tag with case.
  ``test_a_task_with_the_tag_landing_passes``.
* C6. ``G/claim_gate.py`` ``check_claim_gate`` refuses in shadow.
  ``test_the_shadow_refuses_nothing_and_logs``.
* C7. ``G/tasks.py`` ``set_assignees`` skips ``check_claim_gate``.
  ``test_the_three_doors_refuse[set_assignees]``.
* C8. ``G/agent_runs.py`` ``_start_scope`` skips ``check_claim_gate``.
  ``test_the_three_doors_refuse[start]``.
* C9. ``G/claim_gate.py`` ``check_claim_gate`` reads the flags after its
  first statement. ``test_with_both_flags_off_no_door_reads_the_gate``.
* C10. ``G/refs.py`` ``insert_ref`` writes the mark with no ``landing``.
  ``test_a_repeat_with_no_landing_changes_nothing``.
"""
from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_common import get_settings
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests.unit import test_mcp_edge_task_writes as tw
from tests.unit import test_mcp_edge_writes as w

_REPO = Path(__file__).resolve().parents[2]
_DB_GATE = w._DB_GATE
MEMBER = w.MEMBER
OTHER = "other@mcpwrites.test"

_APP_ROLE = "acb_app_claimgate"
_APP_PW = "claimgate_scratch_pw"

redis_fake = tw.redis_fake
live_app = tw.live_app
live = tw.live
_send = tw._send


@pytest.fixture(scope="module")
def world():
    """A private promoted catalog: one tenant, one member, two projects."""
    if not w._URL:
        pytest.skip("TENANT_LADDER_DATABASE_URL unset")
    admin_url = make_url(w._URL)
    dedicated = f"{admin_url.database}_claimgate"
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

    org = str(uuid.uuid4())
    with eng.begin() as c:
        c.execute(text("INSERT INTO organization (id, slug, display_name) "
                       "VALUES (:id, 'claimgate', 'claimgate')"), {"id": org})
    a = w._Seeder(eng, org)
    a.number = 600_000
    a.member(MEMBER, ["feature:projects"])
    work = a.project("Work")
    lanes = SimpleNamespace(
        backlog=a.status(work, "Backlog", "backlog", 0),
        todo=a.status(work, "To do", "todo", 1),
        doing=a.status(work, "Doing", "in_progress", 2),
        done=a.status(work, "Done", "done", 3),
        cancelled=a.status(work, "Cancelled", "cancelled", 4))
    # A project with no grant for the member: its tasks are hidden.
    hidden = str(a.one(
        "INSERT INTO pm_projects (name, status, source, created_by, organization_id,"
        " timezone, owns_statuses) VALUES ('Hidden', 'active', 'manual', :a,"
        " CAST(:o AS uuid), 'Asia/Kolkata', true) RETURNING id", a=w.OWNER, o=org))
    hidden_todo = a.status(hidden, "To do", "todo", 0)
    world_ = SimpleNamespace(
        admin=eng, org=org, seeder=a, work=work, s=lanes, hidden=hidden,
        hidden_todo=hidden_todo, c={}, tok={})
    world_.c["write"], world_.tok["write"] = a.connection(scopes=w.WRITE_SCOPES)
    world_.app_dsn = admin_url.set(
        database=dedicated, username=_APP_ROLE, password=_APP_PW,
    ).render_as_string(hide_password=False)
    yield world_

    eng.dispose()
    w._drop_database(admin_url, dedicated)


@pytest.fixture
def flags(monkeypatch, world):
    """Set the two flags of the claim gate for the world's organization."""
    def set_(*, refuse: bool = False, shadow: bool = False) -> None:
        monkeypatch.setattr(get_settings(), "claim_gate_orgs", world.org if refuse else "")
        monkeypatch.setattr(get_settings(), "claim_gate_shadow_orgs",
                            world.org if shadow else "")
    set_()
    return set_


@pytest.fixture
def clean(live):
    """Mark every PR of the world ``gone``, so a test counts only its own."""
    with live.admin.begin() as c:
        c.execute(text("UPDATE pm_task_refs SET landing_state = 'gone', landing_at = now()"))
    return live


@pytest.fixture
def runs_on(monkeypatch):
    monkeypatch.setattr(get_settings(), "agent_runs_enabled", True)


def _label() -> str:
    return f"lane-{uuid.uuid4().hex[:8]}"


def _task(world, title: str, *, status: str = "todo", project: str | None = None,
          tags: tuple[str, ...] = ()) -> str:
    project = project or world.work
    lane = world.hidden_todo if project == world.hidden else getattr(world.s, status)
    world.seeder.number += 1
    return str(world.seeder.one(
        "INSERT INTO pm_tasks (title, project_id, root_project_id, status_id, created_by,"
        " organization_id, task_number, tags) VALUES (:t, CAST(:p AS uuid),"
        " CAST(:p AS uuid), CAST(:s AS uuid), :a, CAST(:o AS uuid), :n,"
        " CAST(:tags AS text[])) RETURNING id",
        t=title, p=project, s=lane, a=w.OWNER, o=world.org, n=world.seeder.number,
        tags=list(tags)))


def _number(world, task: str) -> int:
    with world.admin.begin() as c:
        return int(c.execute(text("SELECT task_number FROM pm_tasks WHERE id = CAST(:t AS uuid)"),
                             {"t": task}).scalar())


def _run(world, task: str, *, label: str | None, member: str = MEMBER,
         status: str = "completed", days_ago: float = 3.0, answered: bool = False,
         connection: str | None = None) -> str:
    """One run on *task*. ``answered``: a member's answer is its newest
    activity, and no agent activity follows it."""
    return str(world.seeder.one(
        "INSERT INTO pm_agent_runs (task_id, member_email, client_via, status, started_by,"
        " connection_id, agent_label, created_at, last_activity_at, last_agent_activity_at)"
        " VALUES (CAST(:t AS uuid), :m, :via, :st, :via, CAST(:cid AS uuid),"
        " CAST(:label AS text), now() - make_interval(secs => :ago),"
        " now() - make_interval(secs => :act), now() - make_interval(secs => :agent))"
        " RETURNING id",
        t=task, m=member, via=w.R8_VIA, st=status, cid=connection, label=label,
        ago=days_ago * 86400, act=60 if answered else 3600,
        agent=3600 if answered else 60))


def _pr(world, task: str, number: int, *, hours: float = 60, state: str | None = "open",
        kind: str = "pull_request", merged: bool = False, landing: str | None = None,
        landing_hours: float = 1) -> str:
    ref = f"Hathi-Labs/Metorite#{number}"
    url = f"https://github.com/Hathi-Labs/Metorite/pull/{number}"
    if kind == "issue":
        ref, url = f"Hathi-Labs/Metorite#{number}", f"https://github.com/Hathi-Labs/Metorite/issues/{number}"
    return str(world.seeder.one(
        "INSERT INTO pm_task_refs (task_id, kind, provider, ref, url, state, via, created_by,"
        " created_at, merged_at, landing_state, landing_at, landing_by) VALUES"
        " (CAST(:t AS uuid), :kind, 'github', :ref, :url, :state, 'ui', :by,"
        " now() - make_interval(secs => :age),"
        " CASE WHEN CAST(:merged AS boolean) THEN now() END, CAST(:landing AS text),"
        " CASE WHEN CAST(:landing AS text) IS NOT NULL"
        "      THEN now() - make_interval(secs => :lage) END,"
        " CASE WHEN CAST(:landing AS text) IS NOT NULL THEN :by END) RETURNING id",
        t=task, kind=kind, ref=ref, url=url, state=state, by=MEMBER, age=hours * 3600,
        merged=merged, landing=landing, lage=landing_hours * 3600))


def _old(world, label: str | None, number: int, **kw: Any) -> str:
    """A task with an open PR of *label* older than the gate's 48 hours."""
    task = _task(world, f"old {number}", status=kw.pop("status", "doing"))
    _run(world, task, label=label)
    _pr(world, task, number, **kw)
    return task


async def _version(world, task: str) -> str:
    r = await _send(world, "GET", f"/projects/tasks/{task}")
    assert r.status_code == 200, r.text
    return r.json()["updated_at"]


async def _claim(world, task: str, label: str | None, **kw: Any) -> Any:
    body = {"agent_label": label} if label is not None else {}
    return await _send(world, "POST", f"/projects/tasks/{task}/claim", body=body,
                       headers={"If-Match": await _version(world, task)}, **kw)


async def _self_add(world, task: str) -> Any:
    return await _send(world, "PUT", f"/projects/tasks/{task}/assignees",
                       body={"assignees": [MEMBER]},
                       headers={"If-Match": await _version(world, task)})


async def _start(world, task: str, label: str | None) -> Any:
    body = {"agent_label": label} if label is not None else {}
    return await _send(world, "POST", f"/projects/tasks/{task}/agent-runs", body=body)


def _refused(r: Any) -> list[dict[str, Any]]:
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert detail["error"] == "land_your_pr", detail
    return detail["prs"]


def _assignees(world, task: str) -> list[str]:
    with world.admin.begin() as c:
        return sorted(r[0] for r in c.execute(text(
            "SELECT assignee FROM pm_task_assignees WHERE task_id = CAST(:t AS uuid)"),
            {"t": task}))


def _runs_of(world, task: str) -> int:
    with world.admin.begin() as c:
        return int(c.execute(text(
            "SELECT count(*) FROM pm_agent_runs WHERE task_id = CAST(:t AS uuid)"),
            {"t": task}).scalar())


# ═══════════════════════════════════════════════════════════════════════════
# The migration
# ═══════════════════════════════════════════════════════════════════════════


class TestTheMigration:
    pytestmark = _DB_GATE

    def test_the_file_is_found_by_content_and_is_expand_only(self):
        found = [p for p in _REPO.joinpath("infra", "postgres").glob("[0-9]*_*.sql")
                 if "pm_task_refs_landing_state_known" in p.read_text(encoding="utf-8")]
        assert len(found) == 1, found
        sql = found[0].read_text(encoding="utf-8")
        assert sql.count("ADD COLUMN IF NOT EXISTS") == 3
        assert "DROP " not in sql and "RENAME" not in sql and "NOT NULL" not in sql.split(
            "BEGIN;", 1)[1]
        assert "SET lock_timeout" in sql

    def test_the_columns_and_the_check_are_live(self, world):
        with world.admin.begin() as c:
            cols = {r[0]: r[1] for r in c.execute(text(
                "SELECT column_name, is_nullable FROM information_schema.columns"
                " WHERE table_name = 'pm_task_refs' AND column_name LIKE 'landing_%'"))}
        assert cols == {"landing_state": "YES", "landing_at": "YES", "landing_by": "YES"}
        task = _task(world, "check")
        with pytest.raises(Exception, match="pm_task_refs_landing_state_known"):
            _pr(world, task, 1, landing="merged")


# ═══════════════════════════════════════════════════════════════════════════
# Done when 1: dark
# ═══════════════════════════════════════════════════════════════════════════


class TestDark:
    pytestmark = _DB_GATE

    async def test_with_both_flags_off_no_door_reads_the_gate(
            self, clean, flags, runs_on, monkeypatch):
        from gateway.routes.projects import claim_gate

        async def boom(*_a: Any, **_k: Any) -> Any:
            raise AssertionError("the gate read the database with both flags off")

        for name in ("counted_prs", "_task_exempt", "_own_open_run"):
            monkeypatch.setattr(claim_gate, name, boom)
        label = _label()
        _old(clean, label, 9001)
        _old(clean, None, 9002)
        claim = await _claim(clean, _task(clean, "dark claim"), label)
        assert claim.status_code == 200, claim.text
        add = await _self_add(clean, _task(clean, "dark add"))
        assert add.status_code == 200, add.text
        start = await _start(clean, _task(clean, "dark start"), label)
        assert start.status_code == 201, start.text
        r = await _send(clean, "GET", "/projects/agent-work/landing",
                        query=f"agent_label={label}")
        assert r.status_code == 200 and r.json() == {"rows": []}


# ═══════════════════════════════════════════════════════════════════════════
# Done when 2: the refusal and each rule of "A counted PR"
# ═══════════════════════════════════════════════════════════════════════════


class TestTheRefusal:
    pytestmark = _DB_GATE

    async def test_a_label_with_an_old_pr_is_refused_and_writes_nothing(self, clean, flags):
        flags(refuse=True)
        label = _label()
        old = _old(clean, label, 101, hours=52)
        new = _task(clean, "new work")
        prs = _refused(await _claim(clean, new, label))
        assert prs == [{
            "pr_number": 101, "age_hours": 52, "task_id": old,
            "task_number": _number(clean, old), "next": "land",
            "url": "https://github.com/Hathi-Labs/Metorite/pull/101"}]
        assert _assignees(clean, new) == []

    async def test_the_edge_text_names_the_pr_its_age_its_task_and_the_step(
            self, clean, flags):
        flags(refuse=True)
        label = _label()
        old = _old(clean, label, 102, hours=50)
        new = _task(clean, "new work over mcp")
        result = await tw._mcp(clean, "write", "claim_task", {
            "task_id": new, "expected_version": await _version(clean, new),
            "agent_label": label})
        assert result["isError"] is True, result
        number = _number(clean, old)
        text_ = result["content"][0]["text"]
        assert (f"PR 102 of task {number} (task_id {old}) is 50 hours old:"
                " https://github.com/Hathi-Labs/Metorite/pull/102.") in text_
        assert (f"Merge main into PR 102, verify it, and queue it. Then call"
                f" add_task_reference on task {number} with landing queued.") in text_
        assert f"If PR 102 is merged or closed, call add_task_reference on task {number}" in text_

    async def test_an_answered_task_says_answer(self, clean, flags):
        flags(refuse=True)
        label = _label()
        old = _task(clean, "answered", status="doing")
        _run(clean, old, label=label, status="in_progress", days_ago=0.5, answered=True)
        _pr(clean, old, 103)
        [pr] = _refused(await _claim(clean, _task(clean, "new"), label))
        assert pr["next"] == "answer"

    async def test_up_to_five_prs_oldest_first(self, clean, flags):
        flags(refuse=True)
        label = _label()
        for n in range(7):
            _old(clean, label, 200 + n, hours=100 - n)
        prs = _refused(await _claim(clean, _task(clean, "new"), label))
        assert [p["pr_number"] for p in prs] == [200, 201, 202, 203, 204]

    @pytest.mark.parametrize("door", ["claim", "set_assignees", "start"])
    async def test_the_three_doors_refuse(self, clean, flags, runs_on, door):
        flags(refuse=True)
        # The PUT carries no label, so each door here checks the bucket of no label.
        _old(clean, None, 300)
        new = _task(clean, f"door {door}")
        r = {"claim": lambda: _claim(clean, new, None),
             "set_assignees": lambda: _self_add(clean, new),
             "start": lambda: _start(clean, new, None)}[door]
        [pr] = _refused(await r())
        assert pr["pr_number"] == 300
        assert _assignees(clean, new) == [] and _runs_of(clean, new) == 0

    async def test_another_label_is_not_counted(self, clean, flags):
        flags(refuse=True)
        _old(clean, _label(), 401)
        r = await _claim(clean, _task(clean, "new"), _label())
        assert r.status_code == 200, r.text

    async def test_no_label_and_a_label_are_two_buckets(self, clean, flags):
        flags(refuse=True)
        _old(clean, None, 402)
        assert (await _claim(clean, _task(clean, "labelled"), _label())).status_code == 200
        [pr] = _refused(await _claim(clean, _task(clean, "bare"), None))
        assert pr["pr_number"] == 402

    async def test_another_member_is_not_counted(self, clean, flags):
        flags(refuse=True)
        label = _label()
        task = _task(clean, "theirs", status="doing")
        _run(clean, task, label=label, member=OTHER)
        _pr(clean, task, 403)
        assert (await _claim(clean, _task(clean, "new"), label)).status_code == 200

    async def test_the_newest_run_decides_the_label(self, clean, flags):
        flags(refuse=True)
        mine, adopter = _label(), _label()
        task = _task(clean, "adopted", status="doing")
        _run(clean, task, label=mine, days_ago=5)
        _run(clean, task, label=adopter, days_ago=1)
        _pr(clean, task, 404)
        assert (await _claim(clean, _task(clean, "new"), mine)).status_code == 200
        [pr] = _refused(await _claim(clean, _task(clean, "new 2"), adopter))
        assert pr["pr_number"] == 404

    async def test_a_young_pr_is_not_counted(self, clean, flags):
        flags(refuse=True)
        label = _label()
        _old(clean, label, 405, hours=30)
        assert (await _claim(clean, _task(clean, "new"), label)).status_code == 200

    @pytest.mark.parametrize(("kw", "counted"), [
        ({"state": None}, True), ({"state": "draft"}, True), ({"state": "open"}, True),
        ({"state": "merged"}, False), ({"state": "closed"}, False),
        ({"merged": True}, False), ({"kind": "issue"}, False)])
    async def test_the_state_of_the_pr(self, clean, flags, kw, counted):
        flags(refuse=True)
        label = _label()
        _old(clean, label, 410, **kw)
        r = await _claim(clean, _task(clean, "new"), label)
        assert (r.status_code == 422) is counted, r.text

    @pytest.mark.parametrize("where", ["done", "cancelled", "archived", "project", "hidden"])
    async def test_a_closed_or_hidden_task_is_not_counted(self, clean, flags, where):
        flags(refuse=True)
        label = _label()
        if where == "hidden":
            task = _task(clean, "hidden", project=clean.hidden)
            _run(clean, task, label=label)
            _pr(clean, task, 420)
        else:
            task = _old(clean, label, 420, status="doing" if where in (
                "archived", "project") else where)
        if where == "archived":
            clean.seeder.one("UPDATE pm_tasks SET archived_at = now()"
                             " WHERE id = CAST(:t AS uuid) RETURNING id", t=task)
        if where == "project":
            project = _project_of_its_own(clean, task)
            clean.seeder.one("UPDATE pm_projects SET archived_at = now()"
                             " WHERE id = CAST(:p AS uuid) RETURNING id", p=project)
        assert (await _claim(clean, _task(clean, "new"), label)).status_code == 200

    async def test_a_task_that_waits_on_a_person_is_not_counted(self, clean, flags):
        flags(refuse=True)
        label = _label()
        task = _old(clean, label, 430)
        _run(clean, task, label=label, status="awaiting", days_ago=4)
        assert (await _claim(clean, _task(clean, "new"), label)).status_code == 200

    @pytest.mark.parametrize(("landing", "hours", "status", "counted"), [
        ("queued", 2, "doing", False), ("queued", 30, "doing", True),
        ("parked", 2, "todo", False), ("parked", 2, "backlog", False),
        ("parked", 2, "doing", True), ("gone", 2, "doing", False)])
    async def test_the_landing_marks(self, clean, flags, landing, hours, status, counted):
        flags(refuse=True)
        label = _label()
        _old(clean, label, 440, status=status, landing=landing, landing_hours=hours)
        r = await _claim(clean, _task(clean, "new"), label)
        assert (r.status_code == 422) is counted, r.text


def _project_of_its_own(world, task: str) -> str:
    """Move *task* to a new project with the member's grant, and return it."""
    project = world.seeder.project(f"own {task[:8]}")
    lane = world.seeder.status(project, "Doing", "in_progress", 0)
    world.seeder.one(
        "UPDATE pm_tasks SET project_id = CAST(:p AS uuid), root_project_id = CAST(:p AS uuid),"
        " status_id = CAST(:s AS uuid) WHERE id = CAST(:t AS uuid) RETURNING id",
        p=project, s=lane, t=task)
    return project


# ═══════════════════════════════════════════════════════════════════════════
# Done when 3: each exemption of trap 2, with the flag on
# ═══════════════════════════════════════════════════════════════════════════


class TestNoDeadlock:
    pytestmark = _DB_GATE

    async def test_the_task_of_an_open_pr_passes(self, clean, flags):
        flags(refuse=True)
        label = _label()
        old = _old(clean, label, 501)
        _old(clean, label, 502)
        r = await _claim(clean, old, label)
        assert r.status_code == 200, r.text
        # A "Land PR N" task that holds the reference of the PR passes too.
        land = _task(clean, "Land PR 502")
        _pr(clean, land, 502, hours=1)
        assert (await _claim(clean, land, label)).status_code == 200

    @pytest.mark.parametrize("tag", ["landing", "Landing", " LANDING "])
    async def test_a_task_with_the_tag_landing_passes(self, clean, flags, tag):
        flags(refuse=True)
        label = _label()
        _old(clean, label, 503)
        r = await _claim(clean, _task(clean, "tagged", tags=(tag,)), label)
        assert r.status_code == 200, r.text

    async def test_a_task_where_the_caller_has_an_open_run_passes(self, clean, flags, runs_on):
        flags(refuse=True)
        label = _label()
        _old(clean, label, 504)
        held = _task(clean, "held by me", status="doing")
        _run(clean, held, label=None, status="in_progress", days_ago=0.01,
             connection=clean.c["write"])
        r = await _claim(clean, held, label)
        assert r.status_code == 200, r.text
        start = await _start(clean, held, label)
        assert start.status_code == 200, start.text

    async def test_a_person_claim_passes(self, clean, flags):
        flags(refuse=True)
        _old(clean, None, 505)
        new = _task(clean, "by hand")
        r = await _send(clean, "POST", f"/projects/tasks/{new}/claim", body={},
                        headers={"If-Match": await _version(clean, new)}, signed=None)
        assert r.status_code == 200, r.text


class TestTheShadow:
    pytestmark = _DB_GATE

    async def test_the_shadow_refuses_nothing_and_logs(self, clean, flags, monkeypatch):
        from gateway.routes.projects import claim_gate

        lines: list[tuple[str, dict[str, Any]]] = []
        monkeypatch.setattr(claim_gate, "_log", SimpleNamespace(
            info=lambda event, **kw: lines.append((event, kw))))
        flags(shadow=True)
        label = _label()
        _old(clean, label, 601)
        new = _task(clean, "shadow")
        r = await _claim(clean, new, label)
        assert r.status_code == 200, r.text
        assert _assignees(clean, new) == [MEMBER]
        assert lines == [("claim.gate would_refuse",
                          {"task_id": new, "prs": [601], "labelled": True})]
        flags(refuse=True)
        lines.clear()
        _refused(await _claim(clean, _task(clean, "refused"), label))
        assert [event for event, _ in lines] == ["claim.gate refused"]


# ═══════════════════════════════════════════════════════════════════════════
# Done when 4: the row of my_work
# ═══════════════════════════════════════════════════════════════════════════


class TestMyWork:
    pytestmark = _DB_GATE

    async def test_my_work_lists_the_pr_over_24_hours(self, clean, flags):
        flags(shadow=True)
        label = _label()
        old = _old(clean, label, 701, hours=30)
        _old(clean, label, 702, hours=10)
        result = await tw._mcp(clean, "write", "my_work", {"limit": 25, "agent_label": label})
        assert result["isError"] is False, result
        rows = [r for r in result["structuredContent"]["rows"] if r["reason"] == "land_your_pr"]
        assert [(r["task_id"], r["pr_number"], r["age_hours"]) for r in rows] == [
            (old, 701, 30)]
        assert rows[0]["pr_url"] == "https://github.com/Hathi-Labs/Metorite/pull/701"
        # Another label sees none of them.
        other = await tw._mcp(clean, "write", "my_work", {"limit": 25, "agent_label": _label()})
        assert not [r for r in other["structuredContent"]["rows"]
                    if r["reason"] == "land_your_pr"]

    async def test_a_person_reads_no_row(self, clean, flags):
        flags(refuse=True)
        _old(clean, None, 703)
        r = await _send(clean, "GET", "/projects/agent-work/landing", signed=None)
        assert r.status_code == 200 and r.json() == {"rows": []}


# ═══════════════════════════════════════════════════════════════════════════
# The landing write
# ═══════════════════════════════════════════════════════════════════════════


def _ref_row(world, task: str, number: int) -> Any:
    with world.admin.begin() as c:
        return c.execute(text(
            "SELECT * FROM pm_task_refs WHERE task_id = CAST(:t AS uuid) AND ref = :r"),
            {"t": task, "r": f"Hathi-Labs/Metorite#{number}"}).one()


def _link_rows(world, task: str) -> list[Any]:
    with world.admin.begin() as c:
        return list(c.execute(text(
            "SELECT meta FROM pm_activities WHERE task_id = CAST(:t AS uuid)"
            " AND type = 'link' ORDER BY created_at, seq"), {"t": task}))


async def _add(world, task: str, number: int, landing: str | None) -> Any:
    body: dict[str, Any] = {"url": f"https://github.com/Hathi-Labs/Metorite/pull/{number}"}
    if landing is not None:
        body["landing"] = landing
    return await _send(world, "POST", f"/projects/tasks/{task}/refs", body=body,
                       query="refresh=false")


class TestTheLandingWrite:
    pytestmark = _DB_GATE

    async def test_queued_on_a_known_pr_marks_it_and_writes_one_line(self, clean, flags):
        flags(refuse=True)
        label = _label()
        old = _old(clean, label, 801)
        before = len(_link_rows(clean, old))
        r = await _add(clean, old, 801, "queued")
        assert r.status_code == 200, r.text
        row = _ref_row(clean, old, 801)
        assert (row.landing_state, row.landing_by) == ("queued", MEMBER)
        assert row.landing_at is not None
        lines = _link_rows(clean, old)
        assert len(lines) == before + 1
        assert lines[-1].meta["ref_action"] == "landing"
        assert lines[-1].meta["landing"] == "queued"
        # The mark lifts the gate.
        assert (await _claim(clean, _task(clean, "new"), label)).status_code == 200

    async def test_a_repeat_with_no_landing_changes_nothing(self, clean, flags):
        label = _label()
        old = _old(clean, label, 802)
        before = _ref_row(clean, old, 802)
        lines = len(_link_rows(clean, old))
        r = await _add(clean, old, 802, None)
        assert r.status_code == 200, r.text
        after = _ref_row(clean, old, 802)
        assert (after.landing_state, after.landing_at, after.landing_by, after.updated_at) == (
            before.landing_state, before.landing_at, before.landing_by, before.updated_at)
        assert len(_link_rows(clean, old)) == lines

    async def test_a_new_pr_with_landing_is_made_with_its_mark(self, clean, flags):
        task = _task(clean, "fresh")
        r = await _add(clean, task, 803, "gone")
        assert r.status_code == 201, r.text
        row = _ref_row(clean, task, 803)
        assert row.landing_state == "gone"
        [line] = _link_rows(clean, task)
        assert line.meta["ref_action"] == "added" and line.meta["landing"] == "gone"

    async def test_queued_again_renews_the_24_hours(self, clean, flags):
        flags(refuse=True)
        label = _label()
        old = _old(clean, label, 804, landing="queued", landing_hours=30)
        _refused(await _claim(clean, _task(clean, "new"), label))
        assert (await _add(clean, old, 804, "queued")).status_code == 200
        assert (await _claim(clean, _task(clean, "new 2"), label)).status_code == 200

    async def test_a_bad_mark_is_422(self, clean, flags):
        task = _task(clean, "bad mark")
        r = await _add(clean, task, 805, "merged")
        assert r.status_code == 422, r.text
