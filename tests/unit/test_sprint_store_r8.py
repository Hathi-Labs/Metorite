"""WS-39 SP-1: the cycle store of Projects, on a real Postgres (R8).

Spec: ``project-docs/specs/project_management_app.md`` §13.2, §13.2a and the
row SP-1 of §13.6. Migration: the file that creates ``pm_cycles``, found by
CONTENT (R1). Routes: ``gateway/routes/projects/cycles.py``.

**Hermetic** (no database). The migration text, the flag reader, the three
places of a flag, the flag on each route, the activity vocabulary, and the
timeline's reading of an unknown type.

**R8** (the class carries ``_DB_GATE``). The real ``/projects`` router runs
as the non-privileged role of the phase-4 rehearsal, on a database with the
whole ladder and two organizations. It proves:

* a task sits in one cycle at most: a second add gets 409, and a direct
  double INSERT breaks the UNIQUE;
* the 409 names the other cycle only when the caller can see its node;
* a member of another organization reads no cycle, through the routes and
  through SQL under ``tenant_engine_scope``;
* one active cycle for each node: a second start gets 409, a race ends as one
  start, and the partial index holds under a direct write;
* a start needs a goal, and stores the two snapshots;
* a close moves each unfinished task as chosen, and writes one
  ``cycle_transfer`` row for each, with the meta;
* the total of the start does not change after a carry-over;
* a reader who cannot see a task gets the visible count and ``hidden``;
* each new table has FORCE row level security;
* with the flag off, every route answers 404 and writes nothing.

**Mutations this suite catches.** Commit first, and restore from HEAD.

* M1. The migration: drop ``CONSTRAINT pm_cycle_tasks_one_cycle UNIQUE
  (task_id)`` -> ``test_a_task_sits_in_one_cycle_at_most``.
* M2. The migration: drop the index ``uq_pm_cycles_one_active`` ->
  ``test_one_active_cycle_for_each_node``.
* M3. ``require_sprints``: do nothing -> ``test_with_the_flag_off_every_route_answers_404``.
* M4. ``get_cycle``: read the committed count from ``pm_cycle_tasks`` in
  place of ``pm_cycle_committed`` ->
  ``test_the_start_total_does_not_change_after_a_carry_over``.

Run::

    TENANT_LADDER_DATABASE_URL=... uv run pytest tests/unit/test_sprint_store_r8.py -v -rs
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("gateway.routes.projects", reason="gateway not installed")

from gateway.routes.projects import cycles
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_decisions import _app, _principal
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

_REPO = Path(__file__).resolve().parents[2]
_MIGRATIONS = _REPO / "infra" / "postgres"
_TABLES = ("pm_cycles", "pm_cycle_tasks", "pm_cycle_committed", "pm_cycle_capacity")

#: Every bound on a wait. Broken code goes red and never hangs.
_WAIT = 60


def _migration() -> Path:
    """The migration that creates ``pm_cycles``, found by content (R1)."""
    found = [
        p for p in sorted(_MIGRATIONS.glob("*.sql"))
        if "CREATE TABLE IF NOT EXISTS pm_cycles (" in p.read_text(encoding="utf-8")
    ]
    assert len(found) == 1, [p.name for p in found]
    return found[0]


def _sql() -> str:
    return re.sub(r"--.*", "", _migration().read_text(encoding="utf-8"))


# ── Hermetic ────────────────────────────────────────────────────────────────


def test_the_migration_is_expand_only_and_forces_rls_on_each_table() -> None:
    sql = _sql()
    assert "SET LOCAL lock_timeout" in sql
    assert not re.search(r"\bDROP\s+(TABLE|COLUMN)\b|\bRENAME\b", sql, re.IGNORECASE)
    assert not re.search(r"\bGRANT\b", sql)
    for table in _TABLES:
        assert f"CREATE TABLE IF NOT EXISTS {table} (" in sql, table
        assert f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;" in sql, table
        assert f"ALTER TABLE {table} FORCE  ROW LEVEL SECURITY;" in sql, table
        assert f"CREATE POLICY {table}_tenant_isolation ON {table}" in sql, table
        # REFERENCES before DEFAULT, so the tenancy ratchet matches the pair.
        body = sql.split(f"CREATE TABLE IF NOT EXISTS {table} (", 1)[1].split("\n);", 1)[0]
        assert re.search(
            r"organization_id\s+UUID NOT NULL\s+REFERENCES organization \(id\) ON DELETE "
            r"CASCADE\s+DEFAULT current_setting\('app.tenant_id', true\)::uuid", body), table
    assert re.search(r"CONSTRAINT pm_cycle_tasks_one_cycle UNIQUE \(task_id\)", sql)
    assert re.search(
        r"CREATE UNIQUE INDEX IF NOT EXISTS uq_pm_cycles_one_active\s+ON pm_cycles "
        r"\(project_id\)\s+WHERE state = 'active';", sql)


def test_the_migration_widens_the_activity_check_with_cycle_transfer() -> None:
    sql = _sql()
    added = re.search(
        r"ADD CONSTRAINT pm_activities_type_check\s+CHECK \(type IN \(([^)]*)\)\) NOT VALID;",
        sql, re.S)
    assert added is not None
    assert "'cycle_transfer'" in added.group(1)
    assert "VALIDATE CONSTRAINT pm_activities_type_check;" in sql
    from gateway.routes.projects.core import ACTIVITY_TYPES

    assert cycles.TRANSFER_TYPE in ACTIVITY_TYPES
    assert set(re.findall(r"'([a-z_]+)'", added.group(1))) == set(ACTIVITY_TYPES)


def test_the_flag_is_off_by_default_and_reads_a_list_or_a_star(monkeypatch) -> None:
    from acb_common import get_settings

    org, other = str(uuid.uuid4()), str(uuid.uuid4())
    settings = get_settings()
    assert type(settings).model_fields["projects_sprints_orgs"].default == ""
    for raw, org_on, other_on in (
        ("", False, False),
        (f" {org.upper()} , x", True, False),
        ("*", True, True),
        (other, False, True),
    ):
        monkeypatch.setattr(settings, "projects_sprints_orgs", raw)
        assert cycles.sprints_allowed(org) is org_on, raw
        assert cycles.sprints_allowed(other) is other_on, raw
    monkeypatch.setattr(settings, "projects_sprints_orgs", "*")
    for nobody in (None, "", "  "):
        assert cycles.sprints_allowed(nobody) is False


def test_the_flag_is_named_in_the_three_places_of_a_flag() -> None:
    from acb_common.env_guard import is_platform_env
    from gateway.flag_reload import reloadable

    env = (_REPO / ".env.example").read_text(encoding="utf-8")
    assert re.search(r"^PROJECTS_SPRINTS_ORGS=$", env, re.MULTILINE)
    assert is_platform_env("PROJECTS_SPRINTS_ORGS")
    assert reloadable("PROJECTS_SPRINTS_ORGS")


#: The nine routes of SP-1, as the router mounts them.
ROUTES = {
    ("GET", "/projects/nodes/{project_id}/cycles"),
    ("POST", "/projects/nodes/{project_id}/cycles"),
    ("GET", "/projects/cycles/{cycle_id}"),
    ("PATCH", "/projects/cycles/{cycle_id}"),
    ("DELETE", "/projects/cycles/{cycle_id}"),
    ("POST", "/projects/cycles/{cycle_id}/tasks"),
    ("DELETE", "/projects/cycles/{cycle_id}/tasks/{task_id}"),
    ("POST", "/projects/cycles/{cycle_id}/start"),
    ("POST", "/projects/cycles/{cycle_id}/close"),
}


def test_each_route_is_mounted_and_reads_the_flag() -> None:
    from gateway.routes.projects import router

    mounted = {
        (m, r.path): r for r in router.routes for m in getattr(r, "methods", set())
        if "/cycles" in r.path
    }
    assert set(mounted) == ROUTES, set(mounted) ^ ROUTES
    for key, route in mounted.items():
        calls = {d.call for d in route.dependant.dependencies}
        assert cycles.require_sprints in calls, key


def test_each_route_has_a_manifest_row_and_a_planned_tool() -> None:
    from skill_projects import manifest as m

    rows = {(r.method, r.path): r for r in m.MANIFEST}
    for key in ROUTES:
        assert key in rows, key
        assert rows[key].tool in m.PLANNED, key
        assert "964" in m.PLANNED[rows[key].tool], key


def test_the_timeline_prints_the_body_of_an_unknown_type() -> None:
    """The web timeline has no arm for ``cycle_transfer``. Its default arm
    prints the row's body, so the move reads as the sentence the close wrote.
    """
    src = (_REPO / "workbench/control_plane/src/app/projects/lib/activityStream.ts").read_text(
        encoding="utf-8")
    default = src.split("default:", 1)[1].split("}", 1)[0]
    assert "activity.body ??" in default
    for body in cycles._TRANSFER_BODY.values():
        assert body.endswith(".") and "cycle" in body


# ── R8: the helpers ─────────────────────────────────────────────────────────


class _Client:
    def __init__(self, client: httpx.AsyncClient, users: dict[str, Any]) -> None:
        self.client = client
        self.users = users

    async def __call__(
        self, user: Any, method: str, path: str, body: dict | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        key = str(id(user))
        self.users[key] = user
        return await asyncio.wait_for(
            self.client.request(
                method, path, json=body, headers={"x-test-as": key, **(headers or {})},
            ),
            _WAIT,
        )


@contextlib.asynccontextmanager
async def _session(p: Any):
    from acb_common.db import clear_tenant, release_tenant

    users: dict[str, Any] = {}
    scope = clear_tenant()
    try:
        async with (
            tenant_engine_scope(p.app_url.render_as_string(hide_password=False)),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=_app(users)), base_url="http://cycles.test",
            ) as client,
        ):
            yield _Client(client, users)
    finally:
        release_tenant(scope)


def _member(p: Any, org: str, email: str) -> None:
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO app_user (email, display_name, role, status, organization_id) "
            "VALUES (:e, :e, 'employee', 'active', CAST(:o AS uuid)) ON CONFLICT DO NOTHING"),
            {"e": email, "o": org})


def _node(p: Any, org: str, creator: str, *, parent: dict | None = None,
          grants: tuple[str, ...] = (), sealed: bool = False) -> dict[str, Any]:
    """A root node with a `todo` and a `done` lane, or a child of *parent*
    that shares its lanes. Returns ``{id, root, lanes}``."""
    pid = str(uuid.uuid4())
    _member(p, org, creator)
    with p.admin_engine.begin() as c:
        if parent is None:
            c.execute(text(
                "INSERT INTO pm_projects (id, organization_id, name, created_by, owns_statuses) "
                "VALUES (:id, :o, 'space', :e, true)"), {"id": pid, "o": org, "e": creator})
            lanes = {}
            for position, category in enumerate(("todo", "done")):
                sid = str(uuid.uuid4())
                c.execute(text(
                    "INSERT INTO pm_task_statuses (id, organization_id, project_id, name, "
                    "category, position) VALUES (:id, :o, :p, :n, :c, :pos)"),
                    {"id": sid, "o": org, "p": pid, "n": category, "c": category,
                     "pos": (position + 1) * 10})
                lanes[category] = sid
            root = pid
        else:
            c.execute(text(
                "INSERT INTO pm_projects (id, organization_id, name, created_by, "
                "parent_project_id, owns_statuses, sealed_at) VALUES (:id, :o, 'project', "
                ":e, CAST(:par AS uuid), false, CASE WHEN :sealed THEN now() END)"),
                {"id": pid, "o": org, "e": creator, "par": parent["id"], "sealed": sealed})
            lanes, root = parent["lanes"], parent["root"]
        for who in grants:
            c.execute(text(
                "INSERT INTO pm_project_grants (organization_id, project_id, subject, "
                "created_by) VALUES (:o, :p, :s, :e)"),
                {"o": org, "p": pid, "s": who, "e": creator})
    return {"id": pid, "root": root, "lanes": lanes}


_NUMBER = [0]


def _task(p: Any, org: str, creator: str, node: dict, *, category: str = "todo",
          estimate: int | None = None, archived: bool = False) -> str:
    tid = str(uuid.uuid4())
    _NUMBER[0] += 1
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO pm_tasks (id, organization_id, project_id, root_project_id, "
            "task_number, status_id, title, created_by, estimate_mins, archived_at) "
            "VALUES (:id, :o, :p, :r, :n, :s, :t, :e, :est,"
            " CASE WHEN :arch THEN now() END)"),
            {"id": tid, "o": org, "p": node["id"], "r": node["root"], "n": _NUMBER[0],
             "s": node["lanes"][category], "t": f"Cycle task {_NUMBER[0]}", "e": creator,
             "est": estimate, "arch": archived})
    return tid


def _set_status(p: Any, task: str, node: dict, category: str) -> None:
    with p.admin_engine.begin() as c:
        c.execute(text("UPDATE pm_tasks SET status_id = CAST(:s AS uuid) WHERE id = CAST(:t AS uuid)"),
                  {"s": node["lanes"][category], "t": task})


def _one(p: Any, sql: str, **params: Any) -> Any:
    with p.admin_engine.connect() as c:
        return c.execute(text(sql), params).fetchone()


def _all(p: Any, sql: str, **params: Any) -> list[Any]:
    with p.admin_engine.connect() as c:
        return list(c.execute(text(sql), params))


def _count(p: Any, sql: str, **params: Any) -> int:
    with p.admin_engine.connect() as c:
        return int(c.execute(text(sql), params).scalar())


def _members_of(p: Any, cycle: str) -> set[str]:
    return {str(r.task_id) for r in _all(
        p, "SELECT task_id FROM pm_cycle_tasks WHERE cycle_id = CAST(:c AS uuid)", c=cycle)}


def _plan_body(name: str = "Week 46", goal: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"name": name, "starts_on": "2026-11-09", "ends_on": "2026-11-15"}
    if goal is not None:
        body["goal"] = goal
    return body


@pytest.fixture(scope="module")
def world(promoted):  # noqa: F811 — a fixture
    ns = SimpleNamespace(p=promoted)
    ns.alice = "alice@cycles-r8.test"     # sees the space S and everything under it
    ns.bob = "bob@cycles-r8.test"         # sees the project P only
    ns.carol = "carol@cycles-r8.test"     # a member with a capacity, no grant
    ns.zed = "zed@cycles-r8-b.test"       # a member of org B
    for email in (ns.alice, ns.bob, ns.carol):
        _member(promoted, promoted.org_a, email)
    _member(promoted, promoted.org_b, ns.zed)
    ns.space = _node(promoted, promoted.org_a, ns.alice, grants=(ns.alice,))
    ns.proj = _node(promoted, promoted.org_a, ns.alice, parent=ns.space, grants=(ns.bob,))
    # A sealed project under S: its tasks are hidden from each reader here.
    ns.sealed = _node(promoted, promoted.org_a, ns.alice, parent=ns.space, sealed=True)
    # Another root of org A, outside S.
    ns.other = _node(promoted, promoted.org_a, ns.alice, grants=(ns.alice,))
    ns.space_b = _node(promoted, promoted.org_b, ns.zed, grants=(ns.zed,))
    ns.u_alice = _principal(ns.alice, promoted.org_a)
    ns.u_bob = _principal(ns.bob, promoted.org_a)
    ns.u_zed = _principal(ns.zed, promoted.org_b)
    return ns


@pytest.fixture
def w(world, monkeypatch):
    """The flag on for both orgs, and no cycle and no carry-over row left."""
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "projects_sprints_orgs",
                        f"{world.p.org_a},{world.p.org_b}")
    with world.p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM pm_cycles"))
        c.execute(text("DELETE FROM pm_activities WHERE type = 'cycle_transfer'"))
    return world


async def _plan(call: Any, user: Any, node: str, **kw: Any) -> str:
    made = await call(user, "POST", f"/projects/nodes/{node}/cycles", _plan_body(**kw))
    assert made.status_code == 201, made.text
    return made.json()["cycle"]["id"]


async def _add(call: Any, user: Any, cycle: str, task: str) -> httpx.Response:
    return await call(user, "POST", f"/projects/cycles/{cycle}/tasks", {"task_id": task})


def _error(response: httpx.Response) -> str:
    detail = response.json()["detail"]
    return detail["error"] if isinstance(detail, dict) else str(detail)


@_DB_GATE
class TestTheCycleStoreOnARealDatabase:
    async def test_the_role_cannot_bypass_rls_and_each_table_forces_it(self, w, app_engine):  # noqa: F811
        with app_engine.connect() as c:
            role = c.execute(text(
                "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
            )).first()
            assert role is not None and not role[0] and not role[1]
            for table in _TABLES:
                forced = c.execute(text(
                    "SELECT relrowsecurity, relforcerowsecurity FROM pg_class"
                    " WHERE oid = to_regclass(:t)"), {"t": table}).first()
                assert forced is not None and forced[0] and forced[1], table
                policy = c.execute(text(
                    "SELECT qual, with_check FROM pg_policies WHERE tablename = :t"),
                    {"t": table}).first()
                assert policy is not None and "app.tenant_id" in policy[0], table
                assert "app.tenant_id" in policy[1], table
            # The migration ran: the partial index and the wider CHECK are there.
            index = c.execute(text(
                "SELECT indexdef FROM pg_indexes WHERE indexname = 'uq_pm_cycles_one_active'"
            )).scalar()
            assert index is not None and "UNIQUE" in index and "'active'" in index
            check = c.execute(text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
                " WHERE conname = 'pm_activities_type_check'")).scalar()
            assert "cycle_transfer" in check

    # ── The flag ────────────────────────────────────────────────────────

    async def test_with_the_flag_off_every_route_answers_404(self, w, monkeypatch):
        from acb_common import get_settings

        task = _task(w.p, w.p.org_a, w.alice, w.proj)
        async with _session(w.p) as call:
            cycle = await _plan(call, w.u_alice, w.space["id"], goal="Ship it")
            assert (await _add(call, w.u_alice, cycle, task)).status_code == 200
            # The flag holds org B only: org A is off.
            monkeypatch.setattr(get_settings(), "projects_sprints_orgs", w.p.org_b)
            before = [_count(w.p, f"SELECT count(*) FROM {t}") for t in _TABLES]
            calls = [
                ("GET", f"/projects/nodes/{w.space['id']}/cycles", None),
                ("POST", f"/projects/nodes/{w.space['id']}/cycles", _plan_body()),
                ("GET", f"/projects/cycles/{cycle}", None),
                ("PATCH", f"/projects/cycles/{cycle}", {"name": "Renamed"}),
                ("DELETE", f"/projects/cycles/{cycle}", None),
                ("POST", f"/projects/cycles/{cycle}/tasks", {"task_id": task}),
                ("DELETE", f"/projects/cycles/{cycle}/tasks/{task}", None),
                ("POST", f"/projects/cycles/{cycle}/start", {}),
                ("POST", f"/projects/cycles/{cycle}/close", {"choices": []}),
            ]
            assert {(m, re.sub(r"/[0-9a-f-]{36}", "/{}", path)) for m, path, _ in calls} == {
                (m, re.sub(r"\{[a-z_]+\}", "{}", path)) for m, path in ROUTES}
            for method, path, body in calls:
                refused = await call(w.u_alice, method, path, body)
                assert refused.status_code == 404, (method, path, refused.text)
                assert refused.json() == {"detail": "Not Found"}, (method, path)
            after = [_count(w.p, f"SELECT count(*) FROM {t}") for t in _TABLES]
            assert after == before
            row = _one(w.p, "SELECT name, state FROM pm_cycles WHERE id = CAST(:c AS uuid)",
                       c=cycle)
            assert (row.name, row.state) == ("Week 46", "planned")
            # Org B, on the list, still reaches its own routes.
            ok = await call(w.u_zed, "GET", f"/projects/nodes/{w.space_b['id']}/cycles")
            assert ok.status_code == 200 and ok.json()["cycles"] == []

    # ── Plan, read, edit, delete ────────────────────────────────────────

    async def test_plan_read_edit_and_delete_a_cycle(self, w):
        task = _task(w.p, w.p.org_a, w.alice, w.proj, estimate=45)
        async with _session(w.p) as call:
            made = await call(w.u_alice, "POST", f"/projects/nodes/{w.space['id']}/cycles",
                              {**_plan_body(), "cooldown_days": 1, "credit_budget": "12.5",
                               "organization_id": w.p.org_b})
            assert made.status_code == 422  # extra keys are refused, the org above all
            cycle = await _plan(call, w.u_alice, w.space["id"])
            row = _one(w.p, "SELECT * FROM pm_cycles WHERE id = CAST(:c AS uuid)", c=cycle)
            assert str(row.organization_id) == w.p.org_a
            assert row.state == "planned" and row.created_by == w.alice
            for bad in ({"ends_on": "2026-11-01"}, {"ends_on": "2028-01-01"},
                        {"name": "   "}):
                refused = await call(w.u_alice, "POST",
                                     f"/projects/nodes/{w.space['id']}/cycles",
                                     {**_plan_body(), **bad})
                assert refused.status_code == 422, (bad, refused.text)
            assert (await _add(call, w.u_alice, cycle, task)).json()["added"] is True
            read = await call(w.u_alice, "GET", f"/projects/cycles/{cycle}")
            assert read.status_code == 200, read.text
            out = read.json()
            assert [t["id"] for t in out["tasks"]] == [task]
            assert out["tasks"][0]["estimate_mins"] == 45 and out["hidden"] == 0
            assert out["committed"] is None and out["capacity"] == []
            assert "committed_task_count" not in out["cycle"]
            listed = await call(w.u_alice, "GET", f"/projects/nodes/{w.space['id']}/cycles")
            assert [(c["id"], c["tasks"], c["hidden"]) for c in listed.json()["cycles"]] == [
                (cycle, 1, 0)]
            # The edit takes If-Match: a stale token is 412 and changes nothing.
            token = out["cycle"]["updated_at"]
            edited = await call(w.u_alice, "PATCH", f"/projects/cycles/{cycle}",
                                {"name": "Week 46 (fixes)", "goal": "Close the bugs"},
                                headers={"If-Match": token})
            assert edited.status_code == 200, edited.text
            assert edited.json()["cycle"]["goal"] == "Close the bugs"
            stale = await call(w.u_alice, "PATCH", f"/projects/cycles/{cycle}",
                               {"name": "Lost"}, headers={"If-Match": token})
            assert stale.status_code == 412
            assert _one(w.p, "SELECT name FROM pm_cycles WHERE id = CAST(:c AS uuid)",
                        c=cycle).name == "Week 46 (fixes)"
            # A planned cycle deletes, and its task goes back to the backlog.
            gone = await call(w.u_alice, "DELETE", f"/projects/cycles/{cycle}")
            assert gone.status_code == 200 and gone.json()["tasks_freed"] == 1
            assert _count(w.p, "SELECT count(*) FROM pm_cycle_tasks") == 0
            again = await call(w.u_alice, "GET", f"/projects/cycles/{cycle}")
            assert again.status_code == 404

    async def test_a_caller_who_cannot_see_the_node_gets_404(self, w):
        async with _session(w.p) as call:
            cycle = await _plan(call, w.u_alice, w.space["id"])
            # Bob sees P, not S.
            for method, path, body in (
                ("GET", f"/projects/nodes/{w.space['id']}/cycles", None),
                ("POST", f"/projects/nodes/{w.space['id']}/cycles", _plan_body()),
                ("GET", f"/projects/cycles/{cycle}", None),
                ("PATCH", f"/projects/cycles/{cycle}", {"name": "Mine"}),
                ("DELETE", f"/projects/cycles/{cycle}", None),
                ("POST", f"/projects/cycles/{cycle}/start", {"goal": "Mine"}),
            ):
                refused = await call(w.u_bob, method, path, body)
                assert refused.status_code == 404, (method, path, refused.text)
            for bad in ("not-a-uuid", str(uuid.uuid4())):
                assert (await call(w.u_alice, "GET", f"/projects/cycles/{bad}")).status_code == 404
        assert _count(w.p, "SELECT count(*) FROM pm_cycles") == 1

    # ── One cycle for each task ─────────────────────────────────────────

    async def test_a_task_sits_in_one_cycle_at_most(self, w):
        task = _task(w.p, w.p.org_a, w.alice, w.proj)
        async with _session(w.p) as call:
            first = await _plan(call, w.u_alice, w.space["id"], name="First")
            second = await _plan(call, w.u_alice, w.space["id"], name="Second")
            added = await _add(call, w.u_alice, first, task)
            assert added.status_code == 200 and added.json()["added"] is True
            again = await _add(call, w.u_alice, first, task)
            assert again.status_code == 200 and again.json()["added"] is False
            refused = await _add(call, w.u_alice, second, task)
            assert refused.status_code == 409, refused.text
            detail = refused.json()["detail"]
            assert detail["error"] == "task_in_cycle"
            assert '"First"' in detail["message"] and "Remove it" in detail["message"]
        assert _members_of(w.p, first) == {task} and _members_of(w.p, second) == set()
        # The UNIQUE holds under a direct write.
        with pytest.raises(IntegrityError, match="pm_cycle_tasks_one_cycle"), w.p.admin_engine.begin() as c:
            c.execute(text(
                "INSERT INTO pm_cycle_tasks (organization_id, cycle_id, task_id, added_by) "
                "VALUES (CAST(:o AS uuid), CAST(:c AS uuid), CAST(:t AS uuid), 'x@y.test')"),
                {"o": w.p.org_a, "c": second, "t": task})
        assert _count(w.p, "SELECT count(*) FROM pm_cycle_tasks WHERE task_id = CAST(:t AS uuid)",
                      t=task) == 1

    async def test_the_409_names_the_other_cycle_only_when_the_caller_sees_it(self, w):
        task = _task(w.p, w.p.org_a, w.alice, w.proj)
        async with _session(w.p) as call:
            on_space = await _plan(call, w.u_alice, w.space["id"], name="Secret plan")
            on_proj = await _plan(call, w.u_bob, w.proj["id"], name="Bob's week")
            assert (await _add(call, w.u_alice, on_space, task)).status_code == 200
            # Bob sees the task and P, not S.
            hidden = await _add(call, w.u_bob, on_proj, task)
            assert hidden.status_code == 409
            assert "Secret plan" not in hidden.text
            assert "another cycle" in hidden.json()["detail"]["message"]
            # Alice sees S, so she reads the name.
            named = await _add(call, w.u_alice, on_proj, task)
            assert named.status_code == 409
            assert '"Secret plan"' in named.json()["detail"]["message"]

    async def test_a_task_outside_the_node_or_archived_is_refused(self, w):
        outside = _task(w.p, w.p.org_a, w.alice, w.other)
        shelved = _task(w.p, w.p.org_a, w.alice, w.proj, archived=True)
        async with _session(w.p) as call:
            cycle = await _plan(call, w.u_bob, w.proj["id"])
            refused = await _add(call, w.u_alice, cycle, outside)
            assert refused.status_code == 422 and _error(refused) == "task_outside_node"
            refused = await _add(call, w.u_alice, cycle, shelved)
            assert refused.status_code == 422 and _error(refused) == "task_archived"
            # A task that the caller cannot see is 404, as a task that is not there.
            sealed = _task(w.p, w.p.org_a, w.alice, w.sealed)
            on_space = await _plan(call, w.u_alice, w.space["id"])
            for task in (sealed, str(uuid.uuid4())):
                assert (await _add(call, w.u_alice, on_space, task)).status_code == 404
        assert _count(w.p, "SELECT count(*) FROM pm_cycle_tasks") == 0

    # ── Another organization ────────────────────────────────────────────

    async def test_a_member_of_another_org_reads_no_cycle(self, w, app_engine):  # noqa: F811
        task = _task(w.p, w.p.org_a, w.alice, w.proj, estimate=30)
        async with _session(w.p) as call:
            cycle = await _plan(call, w.u_alice, w.space["id"], goal="Org A goal")
            assert (await _add(call, w.u_alice, cycle, task)).status_code == 200
            started = await call(w.u_alice, "POST", f"/projects/cycles/{cycle}/start", {})
            assert started.status_code == 200, started.text
            for method, path, body in (
                ("GET", f"/projects/nodes/{w.space['id']}/cycles", None),
                ("POST", f"/projects/nodes/{w.space['id']}/cycles", _plan_body()),
                ("GET", f"/projects/cycles/{cycle}", None),
                ("PATCH", f"/projects/cycles/{cycle}", {"name": "Taken"}),
                ("DELETE", f"/projects/cycles/{cycle}", None),
                ("POST", f"/projects/cycles/{cycle}/tasks", {"task_id": task}),
                ("DELETE", f"/projects/cycles/{cycle}/tasks/{task}", None),
                ("POST", f"/projects/cycles/{cycle}/close", {"choices": []}),
            ):
                refused = await call(w.u_zed, method, path, body)
                assert refused.status_code == 404, (method, path, refused.text)
            # Org B plans on its own node, and sees only its own cycle.
            own = await _plan(call, w.u_zed, w.space_b["id"], name="B week")
            listed = await call(w.u_zed, "GET", f"/projects/nodes/{w.space_b['id']}/cycles")
            assert [c["id"] for c in listed.json()["cycles"]] == [own]
        # SQL as the app role: the tenant GUC decides what each org reads.
        with app_engine.connect() as c:
            for org, cycles_seen in ((w.p.org_b, 1), (w.p.org_a, 1)):
                with c.begin():
                    c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": org})
                    assert c.execute(text("SELECT count(*) FROM pm_cycles")).scalar() == cycles_seen
                    seen = {str(r[0]) for r in c.execute(text("SELECT id FROM pm_cycles"))}
                    assert seen == ({own} if org == w.p.org_b else {cycle})
                    tasks = c.execute(text("SELECT count(*) FROM pm_cycle_tasks")).scalar()
                    committed = c.execute(text("SELECT count(*) FROM pm_cycle_committed")).scalar()
                    assert (tasks, committed) == ((0, 0) if org == w.p.org_b else (1, 1))
            # Bound to org B, a write that names org A's node or cycle is refused.
            for sql, params in (
                ("INSERT INTO pm_cycles (project_id, name, starts_on, ends_on, created_by) "
                 "VALUES (CAST(:p AS uuid), 'x', '2026-11-09', '2026-11-15', 'z@b.test')",
                 {"p": w.space["id"]}),
                ("INSERT INTO pm_cycle_tasks (cycle_id, task_id, added_by) "
                 "VALUES (CAST(:c AS uuid), CAST(:t AS uuid), 'z@b.test')",
                 {"c": cycle, "t": task}),
                ("INSERT INTO pm_cycle_tasks (cycle_id, task_id, added_by) "
                 "VALUES (CAST(:c AS uuid), CAST(:t AS uuid), 'z@b.test')",
                 {"c": own, "t": task}),
            ):
                with pytest.raises(DBAPIError), c.begin():
                    c.execute(text("SELECT set_config('app.tenant_id', :o, true)"),
                              {"o": w.p.org_b})
                    c.execute(text(sql), params)
        assert _count(w.p, "SELECT count(*) FROM pm_cycle_tasks") == 1

    async def test_the_same_org_triggers_hold_for_the_table_owner(self, w):
        task_a = _task(w.p, w.p.org_a, w.alice, w.proj)
        with pytest.raises(DBAPIError, match="not a project of this organization"), w.p.admin_engine.begin() as c:
            c.execute(text(
                "INSERT INTO pm_cycles (organization_id, project_id, name, starts_on, "
                "ends_on, created_by) VALUES (CAST(:o AS uuid), CAST(:p AS uuid), 'x', "
                "'2026-11-09', '2026-11-15', 'z@b.test')"),
                {"o": w.p.org_b, "p": w.space["id"]})
        with w.p.admin_engine.begin() as c:
            cycle_b = str(c.execute(text(
                "INSERT INTO pm_cycles (organization_id, project_id, name, starts_on, "
                "ends_on, created_by) VALUES (CAST(:o AS uuid), CAST(:p AS uuid), 'b', "
                "'2026-11-09', '2026-11-15', 'z@b.test') RETURNING id"),
                {"o": w.p.org_b, "p": w.space_b["id"]}).scalar())
        with pytest.raises(DBAPIError, match="not a task of this organization"), w.p.admin_engine.begin() as c:
            c.execute(text(
                "INSERT INTO pm_cycle_tasks (organization_id, cycle_id, task_id, added_by) "
                "VALUES (CAST(:o AS uuid), CAST(:c AS uuid), CAST(:t AS uuid), 'z@b.test')"),
                {"o": w.p.org_b, "c": cycle_b, "t": task_a})

    # ── One active cycle for each node ──────────────────────────────────

    async def test_one_active_cycle_for_each_node(self, w):
        async with _session(w.p) as call:
            first = await _plan(call, w.u_alice, w.space["id"], name="First", goal="One")
            second = await _plan(call, w.u_alice, w.space["id"], name="Second", goal="Two")
            elsewhere = await _plan(call, w.u_bob, w.proj["id"], name="On P", goal="P")
            ok = await call(w.u_alice, "POST", f"/projects/cycles/{first}/start", {})
            assert ok.status_code == 200, ok.text
            refused = await call(w.u_alice, "POST", f"/projects/cycles/{second}/start", {})
            assert refused.status_code == 409, refused.text
            detail = refused.json()["detail"]
            assert detail["error"] == "cycle_active" and '"First"' in detail["message"]
            assert "Close it" in detail["message"]
            again = await call(w.u_alice, "POST", f"/projects/cycles/{first}/start", {})
            assert again.status_code == 409 and _error(again) == "cycle_started"
            # Another node has its own active cycle.
            other = await call(w.u_bob, "POST", f"/projects/cycles/{elsewhere}/start", {})
            assert other.status_code == 200, other.text
        assert _one(w.p, "SELECT state FROM pm_cycles WHERE id = CAST(:c AS uuid)",
                    c=second).state == "planned"
        # The partial index holds under a direct write.
        with pytest.raises(IntegrityError, match="uq_pm_cycles_one_active"), w.p.admin_engine.begin() as c:
            c.execute(text(
                "UPDATE pm_cycles SET state = 'active', started_at = now(),"
                " started_by = 'x@y.test', committed_task_count = 0,"
                " committed_estimate_mins = 0 WHERE id = CAST(:c AS uuid)"), {"c": second})
        with pytest.raises(IntegrityError, match="uq_pm_cycles_one_active"), w.p.admin_engine.begin() as c:
            c.execute(text(
                "INSERT INTO pm_cycles (organization_id, project_id, name, goal, starts_on,"
                " ends_on, state, started_at, started_by, committed_task_count,"
                " committed_estimate_mins, created_by) VALUES (CAST(:o AS uuid),"
                " CAST(:p AS uuid), 'Direct', 'g', '2026-11-09', '2026-11-15', 'active',"
                " now(), 'x@y.test', 0, 0, 'x@y.test')"),
                {"o": w.p.org_a, "p": w.space["id"]})
        assert _count(w.p, "SELECT count(*) FROM pm_cycles WHERE state = 'active'"
                           " AND project_id = CAST(:node AS uuid)", node=w.space["id"]) == 1

    async def test_two_concurrent_starts_on_one_node_end_as_one_start(self, w):
        async with _session(w.p) as call:
            ids = [await _plan(call, w.u_alice, w.space["id"], name=f"Race {i}", goal="Race")
                   for i in range(3)]
            results = await asyncio.wait_for(asyncio.gather(*(
                call(w.u_alice, "POST", f"/projects/cycles/{c}/start", {}) for c in ids
            )), _WAIT)
        codes = sorted(r.status_code for r in results)
        assert codes == [200, 409, 409], [r.text for r in results]
        assert all(_error(r) == "cycle_active" for r in results if r.status_code == 409)
        assert _count(w.p, "SELECT count(*) FROM pm_cycles WHERE state = 'active'") == 1

    # ── Start ───────────────────────────────────────────────────────────

    async def test_a_start_needs_a_goal_and_stores_both_snapshots(self, w):
        task = _task(w.p, w.p.org_a, w.alice, w.proj, estimate=20)
        async with _session(w.p) as call:
            cycle = await _plan(call, w.u_alice, w.space["id"])
            assert (await _add(call, w.u_alice, cycle, task)).status_code == 200
            for body in ({}, {"goal": "   "}):
                refused = await call(w.u_alice, "POST", f"/projects/cycles/{cycle}/start", body)
                assert refused.status_code == 422 and _error(refused) == "goal_required"
                assert "goal" in refused.json()["detail"]["message"]
            for capacity in ([{"member_email": "nobody@cycles-r8.test", "review_minutes": 60}],
                             [{"member_email": w.zed, "review_minutes": 60}],
                             [{"member_email": w.carol, "review_minutes": 60},
                              {"member_email": w.carol.upper(), "review_minutes": 30}]):
                refused = await call(w.u_alice, "POST", f"/projects/cycles/{cycle}/start",
                                     {"goal": "Ship", "capacity": capacity})
                assert refused.status_code == 422 and _error(refused) == "bad_capacity"
            assert _one(w.p, "SELECT state FROM pm_cycles WHERE id = CAST(:c AS uuid)",
                        c=cycle).state == "planned"
            started = await call(w.u_alice, "POST", f"/projects/cycles/{cycle}/start",
                                 {"goal": "Ship the extruder fix",
                                  "capacity": [{"member_email": w.carol.upper(),
                                                "review_minutes": 300}]})
            assert started.status_code == 200, started.text
            assert started.json()["cycle"]["state"] == "active"
            read = (await call(w.u_alice, "GET", f"/projects/cycles/{cycle}")).json()
            assert read["committed"] == {"tasks": 1, "hidden": 0, "estimate_mins": 20}
            assert read["capacity"] == [{"member_email": w.carol, "review_minutes": 300}]
        row = _one(w.p, "SELECT * FROM pm_cycles WHERE id = CAST(:c AS uuid)", c=cycle)
        assert row.goal == "Ship the extruder fix" and row.started_by == w.alice
        assert (row.committed_task_count, row.committed_estimate_mins) == (1, 20)
        assert row.started_at is not None
        # The snapshots do not change after the start, for the table owner too.
        for sql in ("UPDATE pm_cycle_committed SET estimate_mins = 1",
                    "UPDATE pm_cycle_capacity SET review_minutes = 1",
                    "UPDATE pm_cycles SET committed_task_count = 9 WHERE state = 'active'"):
            with pytest.raises(DBAPIError, match="does not change"), w.p.admin_engine.begin() as c:
                c.execute(text(sql))

    # ── Close ───────────────────────────────────────────────────────────

    async def test_a_close_moves_each_unfinished_task_as_chosen_and_logs_it(self, w):
        done = _task(w.p, w.p.org_a, w.alice, w.proj, category="done")
        carried = _task(w.p, w.p.org_a, w.alice, w.proj)
        dropped = _task(w.p, w.p.org_a, w.alice, w.proj)
        also = _task(w.p, w.p.org_a, w.alice, w.proj)
        async with _session(w.p) as call:
            cycle = await _plan(call, w.u_alice, w.space["id"], name="Now", goal="Now")
            nxt = await _plan(call, w.u_alice, w.space["id"], name="Next")
            for task in (done, carried, dropped, also):
                assert (await _add(call, w.u_alice, cycle, task)).status_code == 200
            assert (await call(w.u_alice, "POST", f"/projects/cycles/{cycle}/start",
                               {})).status_code == 200
            good = [
                {"task_id": carried, "choice": "next", "cycle_id": nxt},
                {"task_id": dropped, "choice": "backlog"},
                {"task_id": also, "choice": "next", "cycle_id": nxt},
            ]
            for bad, error in (
                (good[:2], "choices_missing"),
                ([*good, {"task_id": done, "choice": "backlog"}], "bad_choice"),
                ([*good[:2], {"task_id": also, "choice": "next"}], "bad_choice"),
                ([*good[:2], {"task_id": also, "choice": "backlog", "cycle_id": nxt}],
                 "bad_choice"),
                ([*good[:2], {"task_id": also, "choice": "next", "cycle_id": cycle}],
                 "bad_choice"),
                ([*good, good[1]], "bad_choice"),
            ):
                refused = await call(w.u_alice, "POST", f"/projects/cycles/{cycle}/close",
                                     {"choices": bad})
                assert refused.status_code == 422, (bad, refused.text)
                assert _error(refused) == error, (bad, refused.text)
            assert _members_of(w.p, cycle) == {done, carried, dropped, also}
            assert _count(w.p, "SELECT count(*) FROM pm_activities WHERE type = 'cycle_transfer'") == 0
            closed = await call(w.u_alice, "POST", f"/projects/cycles/{cycle}/close",
                                {"choices": good})
            assert closed.status_code == 200, closed.text
            assert closed.json()["moved"] == {"next": 2, "backlog": 1}
            assert closed.json()["cycle"]["state"] == "closed"
            again = await call(w.u_alice, "POST", f"/projects/cycles/{cycle}/close",
                               {"choices": []})
            assert again.status_code == 409 and _error(again) == "cycle_closed"
        # The finished task stays as the record. The others moved as chosen.
        assert _members_of(w.p, cycle) == {done}
        assert _members_of(w.p, nxt) == {carried, also}
        row = _one(w.p, "SELECT * FROM pm_cycles WHERE id = CAST(:c AS uuid)", c=cycle)
        assert row.state == "closed" and row.closed_at is not None and row.closed_by == w.alice
        logged = _all(w.p, "SELECT task_id, meta, body, created_by FROM pm_activities"
                           " WHERE type = 'cycle_transfer' ORDER BY task_id")
        assert len(logged) == 3
        by_task = {str(r.task_id): r for r in logged}
        assert set(by_task) == {carried, dropped, also}
        for task, to, choice in ((carried, nxt, "next"), (also, nxt, "next"),
                                 (dropped, None, "backlog")):
            meta = by_task[task].meta
            assert meta["from_cycle"] == cycle and meta["to_cycle"] == to, meta
            assert meta["choice"] == choice and by_task[task].created_by == w.alice
            assert by_task[task].body == cycles._TRANSFER_BODY[choice]

    async def test_the_start_total_does_not_change_after_a_carry_over(self, w):
        a = _task(w.p, w.p.org_a, w.alice, w.proj, estimate=30)
        b = _task(w.p, w.p.org_a, w.alice, w.proj, estimate=60)
        c_ = _task(w.p, w.p.org_a, w.alice, w.proj)
        async with _session(w.p) as call:
            cycle = await _plan(call, w.u_alice, w.space["id"], name="Now", goal="Now")
            nxt = await _plan(call, w.u_alice, w.space["id"], name="Next", goal="Next")
            for task in (a, b, c_):
                assert (await _add(call, w.u_alice, cycle, task)).status_code == 200
            assert (await call(w.u_alice, "POST", f"/projects/cycles/{cycle}/start",
                               {})).status_code == 200
            before = _all(w.p, "SELECT task_id, estimate_mins FROM pm_cycle_committed"
                               " WHERE cycle_id = CAST(:c AS uuid) ORDER BY task_id", c=cycle)
            # A change of an estimate after the start is not in the snapshot.
            with w.p.admin_engine.begin() as conn:
                conn.execute(text("UPDATE pm_tasks SET estimate_mins = 500"
                                  " WHERE id = CAST(:t AS uuid)"), {"t": a})
            _set_status(w.p, b, w.proj, "done")
            closed = await call(w.u_alice, "POST", f"/projects/cycles/{cycle}/close", {
                "choices": [{"task_id": a, "choice": "next", "cycle_id": nxt},
                            {"task_id": c_, "choice": "backlog"}]})
            assert closed.status_code == 200, closed.text
            read = (await call(w.u_alice, "GET", f"/projects/cycles/{cycle}")).json()
            assert read["committed"] == {"tasks": 3, "hidden": 0, "estimate_mins": 90}
            assert [t["id"] for t in read["tasks"]] == [b]
            # The next cycle starts with its own snapshot: what it holds now.
            assert (await call(w.u_alice, "POST", f"/projects/cycles/{nxt}/start",
                               {})).status_code == 200
            nxt_read = (await call(w.u_alice, "GET", f"/projects/cycles/{nxt}")).json()
            assert nxt_read["committed"] == {"tasks": 1, "hidden": 0, "estimate_mins": 500}
        row = _one(w.p, "SELECT committed_task_count, committed_estimate_mins FROM pm_cycles"
                        " WHERE id = CAST(:c AS uuid)", c=cycle)
        assert (row.committed_task_count, row.committed_estimate_mins) == (3, 90)
        after = _all(w.p, "SELECT task_id, estimate_mins FROM pm_cycle_committed"
                          " WHERE cycle_id = CAST(:c AS uuid) ORDER BY task_id", c=cycle)
        assert after == before and len(after) == 3

    async def test_a_closed_cycle_keeps_its_finished_tasks_and_lets_a_reopened_one_go(self, w):
        finished = _task(w.p, w.p.org_a, w.alice, w.proj, category="done")
        async with _session(w.p) as call:
            cycle = await _plan(call, w.u_alice, w.space["id"], goal="Now")
            assert (await _add(call, w.u_alice, cycle, finished)).status_code == 200
            assert (await call(w.u_alice, "POST", f"/projects/cycles/{cycle}/start",
                               {})).status_code == 200
            assert (await call(w.u_alice, "POST", f"/projects/cycles/{cycle}/close",
                               {"choices": []})).status_code == 200
            for method, path, body in (
                ("PATCH", f"/projects/cycles/{cycle}", {"name": "Late"}),
                ("POST", f"/projects/cycles/{cycle}/tasks",
                 {"task_id": _task(w.p, w.p.org_a, w.alice, w.proj)}),
                ("DELETE", f"/projects/cycles/{cycle}", None),
                ("DELETE", f"/projects/cycles/{cycle}/tasks/{finished}", None),
            ):
                refused = await call(w.u_alice, method, path, body)
                assert refused.status_code == 409, (method, path, refused.text)
            # The task opens again: it may leave the closed cycle and join a new one.
            _set_status(w.p, finished, w.proj, "todo")
            nxt = await _plan(call, w.u_alice, w.space["id"], name="Again")
            refused = await _add(call, w.u_alice, nxt, finished)
            assert refused.status_code == 409 and "Remove it" in refused.text
            out = await call(w.u_alice, "DELETE", f"/projects/cycles/{cycle}/tasks/{finished}")
            assert out.status_code == 200, out.text
            assert (await _add(call, w.u_alice, nxt, finished)).json()["added"] is True
        with pytest.raises(DBAPIError, match="closed cycle does not change"), w.p.admin_engine.begin() as c:
            c.execute(text("UPDATE pm_cycles SET name = 'x' WHERE id = CAST(:c AS uuid)"),
                      {"c": cycle})

    # ── Hidden tasks (§13.7) ────────────────────────────────────────────

    async def test_a_reader_who_cannot_see_a_task_gets_the_visible_count_and_hidden(self, w):
        seen = _task(w.p, w.p.org_a, w.alice, w.proj, estimate=15)
        unseen = _task(w.p, w.p.org_a, w.alice, w.sealed, estimate=600)
        async with _session(w.p) as call:
            cycle = await _plan(call, w.u_alice, w.space["id"], goal="Mixed")
            assert (await _add(call, w.u_alice, cycle, seen)).status_code == 200
            # The sealed task is in the cycle, but no reader here can see it.
            with w.p.admin_engine.begin() as c:
                c.execute(text(
                    "INSERT INTO pm_cycle_tasks (organization_id, cycle_id, task_id, added_by)"
                    " VALUES (CAST(:o AS uuid), CAST(:c AS uuid), CAST(:t AS uuid),"
                    " 'x@y.test')"), {"o": w.p.org_a, "c": cycle, "t": unseen})
            read = (await call(w.u_alice, "GET", f"/projects/cycles/{cycle}")).json()
            assert [t["id"] for t in read["tasks"]] == [seen] and read["hidden"] == 1
            assert unseen not in str(read)
            listed = (await call(w.u_alice, "GET",
                                 f"/projects/nodes/{w.space['id']}/cycles")).json()
            assert [(c["tasks"], c["hidden"]) for c in listed["cycles"]] == [(1, 1)]
            assert (await call(w.u_alice, "POST", f"/projects/cycles/{cycle}/start",
                               {})).status_code == 200
            read = (await call(w.u_alice, "GET", f"/projects/cycles/{cycle}")).json()
            # The visible count, the hidden count, and the visible estimate only.
            assert read["committed"] == {"tasks": 1, "hidden": 1, "estimate_mins": 15}
            # A close would move a task that the caller cannot see: refused whole.
            refused = await call(w.u_alice, "POST", f"/projects/cycles/{cycle}/close",
                                 {"choices": [{"task_id": seen, "choice": "backlog"}]})
            assert refused.status_code == 409 and _error(refused) == "hidden_tasks"
            assert "(hidden: 1)" in refused.json()["detail"]["message"]
        # The store keeps the full total.
        row = _one(w.p, "SELECT committed_task_count, committed_estimate_mins, state"
                        " FROM pm_cycles WHERE id = CAST(:c AS uuid)", c=cycle)
        assert (row.committed_task_count, row.committed_estimate_mins, row.state) == (
            2, 615, "active")
        assert _members_of(w.p, cycle) == {seen, unseen}
        assert _count(w.p, "SELECT count(*) FROM pm_activities WHERE type = 'cycle_transfer'") == 0
