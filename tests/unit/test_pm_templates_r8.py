"""WS-39 TPL-1a: the template store of Projects, on a real Postgres (R8).

Spec: ``project-docs/specs/project_management_app.md`` §14.2 (row TPL-1a) and
§14.8. Migration: the file that creates ``pm_templates``, found by CONTENT
(R1). Routes: ``gateway/routes/projects/templates.py``.

**Hermetic** (no database). The migration text, the flag reader, the three
places of a flag, the flag on each route, the manifest rows, and the payload
check: the version, an extra key, each cap, a payload nested past any parser.

**R8** (the class carries ``_DB_GATE``). The real ``/projects`` router runs
as the non-privileged role of the phase-4 rehearsal, on a database with the
whole ladder and two organizations. It proves:

* a round trip: a project with its statuses, types, custom fields, tags and a
  task tree of three levels with dates is saved and applied, and the new
  project holds each, with each date at the start plus its offset, and with
  no id of the source, no assignee and no comment;
* the same key twice makes one project, a race of two makes one project, and
  the same key with another body is 409;
* org B cannot list, read or apply a template of org A, through the routes
  and through SQL under RLS;
* the private-source rule: a project that the whole org cannot see is no
  source, and an agent with pins sees only a template whose source it sees;
* the caps refuse an oversized template on the save, on the apply and in the
  database;
* each route answers 404 with the flag off, and writes nothing;
* each new table has FORCE row level security.

**Mutations this suite catches.** Commit first, and restore from HEAD.

* M1. ``save_template``: drop the check of ``_org_reader`` ->
  ``test_a_project_that_the_whole_org_cannot_see_is_no_source``.
* M2. ``apply_template``: answer a claimed key as a new apply (skip
  ``_replay``) -> ``test_the_same_key_twice_makes_one_project``.
* M3. ``require_templates``: do nothing ->
  ``test_with_the_flag_off_every_route_answers_404``.
* M4. ``_reach_clause``: answer nothing for an agent ->
  ``test_an_agent_with_pins_sees_only_a_template_whose_source_it_sees``.
* M5. ``parse_payload``: drop the depth check ->
  ``test_the_caps_refuse_an_oversized_template``.

Run::

    TENANT_LADDER_DATABASE_URL=... uv run pytest tests/unit/test_pm_templates_r8.py -v -rs
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import typing
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("gateway.routes.projects", reason="gateway not installed")

from fastapi import HTTPException
from gateway.routes.projects import templates
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
_TABLES = ("pm_templates", "pm_template_applies")

#: Every bound on a wait. Broken code goes red and never hangs.
_WAIT = 120

#: The three routes of TPL-1a, as the router mounts them.
ROUTES = {
    ("GET", "/projects/templates"),
    ("POST", "/projects/templates"),
    ("POST", "/projects/templates/{template_id}/apply"),
}


def _migration() -> Path:
    """The migration that creates ``pm_templates``, found by content (R1)."""
    found = [
        p for p in sorted(_MIGRATIONS.glob("*.sql"))
        if "CREATE TABLE IF NOT EXISTS pm_templates (" in p.read_text(encoding="utf-8")
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
        body = sql.split(f"CREATE TABLE IF NOT EXISTS {table} (", 1)[1].split("\n);", 1)[0]
        # REFERENCES before DEFAULT, so the tenancy ratchet matches the pair.
        assert re.search(
            r"organization_id\s+UUID NOT NULL\s+REFERENCES organization \(id\) ON DELETE "
            r"CASCADE\s+DEFAULT current_setting\('app.tenant_id', true\)::uuid", body), table
    assert re.search(
        r"CREATE UNIQUE INDEX IF NOT EXISTS uq_pm_templates_name\s+"
        r"ON pm_templates \(organization_id, lower\(name\), kind\);", sql)
    assert re.search(
        r"CREATE UNIQUE INDEX IF NOT EXISTS uq_pm_template_applies_key\s+"
        r"ON pm_template_applies \(organization_id, applied_by, idempotency_key\);", sql)
    assert "CHECK (kind IN ('project', 'task'))" in sql
    assert "IS NOT DISTINCT FROM '1'::jsonb" in sql
    assert f"octet_length(payload::text) <= {templates.MAX_PAYLOAD_BYTES}" in sql
    assert "source_project_id  UUID NULL REFERENCES pm_projects (id) ON DELETE SET NULL" in sql


def test_the_flag_is_off_by_default_and_reads_a_list_or_a_star(monkeypatch) -> None:
    from acb_common import get_settings

    org, other = str(uuid.uuid4()), str(uuid.uuid4())
    settings = get_settings()
    assert type(settings).model_fields["projects_templates_orgs"].default == ""
    for raw, org_on, other_on in (
        ("", False, False),
        (f" {org.upper()} , x", True, False),
        ("*", True, True),
        (other, False, True),
    ):
        monkeypatch.setattr(settings, "projects_templates_orgs", raw)
        assert templates.templates_allowed(org) is org_on, raw
        assert templates.templates_allowed(other) is other_on, raw
    monkeypatch.setattr(settings, "projects_templates_orgs", "*")
    for nobody in (None, "", "  "):
        assert templates.templates_allowed(nobody) is False


def test_the_flag_is_named_in_the_three_places_of_a_flag() -> None:
    from acb_common.env_guard import is_platform_env
    from gateway.flag_reload import reloadable

    env = (_REPO / ".env.example").read_text(encoding="utf-8")
    assert re.search(r"^PROJECTS_TEMPLATES_ORGS=$", env, re.MULTILINE)
    assert is_platform_env("PROJECTS_TEMPLATES_ORGS")
    assert reloadable("PROJECTS_TEMPLATES_ORGS")


def test_each_route_is_mounted_and_reads_the_flag() -> None:
    from gateway.routes.projects import router

    mounted = {
        (m, r.path): r for r in router.routes for m in getattr(r, "methods", set())
        if r.path.startswith("/projects/templates")
    }
    assert set(mounted) == ROUTES, set(mounted) ^ ROUTES
    for key, route in mounted.items():
        calls = {d.call for d in route.dependant.dependencies}
        assert templates.require_templates in calls, key


def test_each_route_has_its_manifest_row_and_class() -> None:
    """A read is class A. A save and an apply are class C: hard to undo, one
    card each (§14.3)."""
    from skill_projects import manifest as m

    rows = {(r.method, r.path): r for r in m.MANIFEST}
    assert (rows[("GET", "/projects/templates")].tool,
            rows[("GET", "/projects/templates")].cls) == ("list_templates", "A")
    assert (rows[("POST", "/projects/templates")].tool,
            rows[("POST", "/projects/templates")].cls) == ("save_as_template", "C")
    apply = rows[("POST", "/projects/templates/{template_id}/apply")]
    assert (apply.tool, apply.cls) == ("create_project_from_template", "C")


def test_the_payload_literals_mirror_the_vocabularies() -> None:
    from gateway.routes.projects.core import STATUS_CATEGORIES
    from gateway.routes.projects.custom_fields import FIELD_TYPES

    category = templates.StatusSpec.model_fields["category"].annotation
    field_type = templates.FieldSpec.model_fields["field_type"].annotation
    assert set(typing.get_args(category)) == set(STATUS_CATEGORIES)
    assert set(typing.get_args(field_type)) == set(FIELD_TYPES)


def test_a_date_that_is_not_real_is_a_422_not_a_500() -> None:
    """The pattern of the body passes 2026-02-30. The parse refuses it."""
    assert templates._day(None) is None
    assert str(templates._day("2026-12-01")) == "2026-12-01"
    with pytest.raises(HTTPException) as caught:
        templates._day("2026-02-30")
    assert caught.value.status_code == 422


def _task(title: str, children: list[dict] | None = None, **extra: Any) -> dict[str, Any]:
    return {"title": title, "children": children or [], **extra}


def _payload(tasks: list[dict] | None = None, **extra: Any) -> dict[str, Any]:
    tasks = tasks or []

    def count(nodes: list[dict]) -> int:
        return sum(1 + count(n.get("children") or []) for n in nodes)

    body: dict[str, Any] = {
        "schema_version": 1,
        "work_style": None,
        "statuses": [{"name": "To do", "category": "todo", "color": "gray", "position": 10},
                     {"name": "Done", "category": "done", "color": "green", "position": 20}],
        "types": [], "fields": [], "tags": [], "tasks": tasks,
    }
    body.update(extra)
    body["counts"] = {"statuses": len(body["statuses"]), "types": len(body["types"]),
                      "fields": len(body["fields"]), "tags": len(body["tags"]),
                      "tasks": count(body["tasks"])}
    return body


def _refused(raw: Any) -> dict[str, Any]:
    with pytest.raises(HTTPException) as caught:
        templates.parse_payload(raw)
    assert caught.value.status_code == 422
    return caught.value.detail


def test_the_payload_check_takes_a_valid_payload_and_refuses_each_bad_one() -> None:
    good = _payload([_task("A", [_task("A1", [_task("A1a")])], status="To do",
                           start_offset_days=0, due_offset_days=3)])
    parsed = templates.parse_payload(good)
    assert parsed.counts.tasks == 3
    assert templates.parse_payload(json.dumps(good)).counts.tasks == 3
    assert _refused({**good, "schema_version": 2})["error"] == templates.INVALID
    assert _refused({**good, "script": "rm -rf /"})["error"] == templates.INVALID
    assert _refused(_payload([_task("A", status="Nowhere")]))["error"] == templates.INVALID
    no_done = _payload(statuses=[{"name": "To do", "category": "todo", "position": 1}])
    assert _refused(no_done)["error"] == templates.INVALID
    wrong_count = {**good, "counts": {**good["counts"], "tasks": 9}}
    assert _refused(wrong_count)["error"] == templates.INVALID
    # strict: a string is not a number, and a number is not a flag
    assert _refused(_payload([_task("A", estimate_mins="30")]))["error"] == templates.INVALID


def test_each_cap_refuses_a_payload_one_past_it() -> None:
    many = _payload([_task(f"T{i}") for i in range(templates.MAX_TASKS + 1)])
    assert _refused(many)["error"] == templates.TOO_LARGE
    deep: dict[str, Any] = _task("leaf")
    for i in range(templates.MAX_TASK_DEPTH):
        deep = _task(f"L{i}", [deep])
    assert _refused(_payload([deep]))["error"] == templates.TOO_LARGE
    wide = _payload([_task("A", description="x" * 20_000) for _ in range(60)])
    assert _refused(wide)["error"] == templates.TOO_LARGE
    statuses = [{"name": f"S{i}", "category": "done", "position": i}
                for i in range(templates.MAX_STATUSES + 1)]
    assert _refused(_payload(statuses=statuses))["error"] == templates.TOO_LARGE


def test_a_payload_nested_past_any_parser_is_refused_by_its_depth() -> None:
    """A stored payload a million levels deep never reaches a recursive
    parser: the stack walk counts its depth first."""
    deep: dict[str, Any] = {"title": "leaf", "children": []}
    for _ in range(100_000):
        deep = {"title": "n", "children": [deep]}
    detail = _refused({**_payload(), "tasks": [deep]})
    assert detail["error"] == templates.TOO_LARGE


# ── R8: the helpers ─────────────────────────────────────────────────────────


class _Client:
    def __init__(self, client: httpx.AsyncClient, users: dict[str, Any]) -> None:
        self.client = client
        self.users = users

    async def __call__(
        self, user: Any, method: str, path: str, body: dict | None = None,
    ) -> httpx.Response:
        key = str(id(user))
        self.users[key] = user
        return await asyncio.wait_for(
            self.client.request(method, path, json=body, headers={"x-test-as": key}), _WAIT,
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
                transport=httpx.ASGITransport(app=_app(users)), base_url="http://templates.test",
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


def _one(p: Any, sql: str, **params: Any) -> Any:
    with p.admin_engine.connect() as c:
        return c.execute(text(sql), params).fetchone()


def _all(p: Any, sql: str, **params: Any) -> list[Any]:
    with p.admin_engine.connect() as c:
        return list(c.execute(text(sql), params))


def _count(p: Any, sql: str, **params: Any) -> int:
    with p.admin_engine.connect() as c:
        return int(c.execute(text(sql), params).scalar())


def _exec(p: Any, sql: str, **params: Any) -> None:
    with p.admin_engine.begin() as c:
        c.execute(text(sql), params)


def _tag() -> str:
    return uuid.uuid4().hex[:8]


async def _space(call: Any, user: Any, name: str) -> str:
    made = await call(user, "POST", "/projects/nodes", {"name": name})
    assert made.status_code == 201, made.text
    return made.json()["id"]


async def _project(call: Any, user: Any, parent: str, name: str) -> str:
    made = await call(user, "POST", "/projects/nodes",
                      {"name": name, "parent_project_id": parent})
    assert made.status_code == 201, made.text
    return made.json()["id"]


def _lanes(p: Any, owner: str) -> dict[str, str]:
    return {r.name: str(r.id) for r in _all(
        p, "SELECT id, name FROM pm_task_statuses WHERE project_id = CAST(:o AS uuid)", o=owner)}


def _types(p: Any, root: str) -> dict[str, str]:
    return {r.name: str(r.id) for r in _all(
        p, "SELECT id, name FROM pm_task_types WHERE project_id = CAST(:r AS uuid)", r=root)}


async def _make_task(call: Any, user: Any, body: dict[str, Any]) -> str:
    made = await call(user, "POST", "/projects/tasks", body)
    assert made.status_code == 201, made.text
    return made.json()["id"]


async def _source(call: Any, w: Any, *, org_wide: bool = True) -> SimpleNamespace:
    """A space S with its own lane, type, two fields and two tags, a project
    P under it, and a task tree of three levels in P, through the routes."""
    ns = SimpleNamespace(tag=_tag())
    ns.space = await _space(call, w.u_alice, f"Space {ns.tag}")
    org = w.p.org_a
    _exec(w.p, "INSERT INTO pm_task_statuses (organization_id, project_id, name, color, position,"
          " category) VALUES (:o, :s, 'Review', 'purple', 35, 'in_progress')", o=org, s=ns.space)
    _exec(w.p, "INSERT INTO pm_task_types (organization_id, project_id, name, icon, is_default,"
          " is_system, is_epic) VALUES (:o, :s, 'Feature', 'star', false, false, false)",
          o=org, s=ns.space)
    _exec(w.p, "INSERT INTO pm_custom_fields (organization_id, project_id, field_key, name,"
          " field_type, options, position, created_by) VALUES"
          " (:o, :s, 'customer_po', 'Customer PO', 'text', '[]'::jsonb, 0, :e),"
          " (:o, :s, 'size', 'Size', 'select', '[\"S\", \"M\", \"L\"]'::jsonb, 1, :e)",
          o=org, s=ns.space, e=w.alice)
    _exec(w.p, "INSERT INTO pm_tags (organization_id, project_id, name, color, created_by) VALUES"
          " (:o, :s, 'urgent', 'red', :e), (:o, :s, 'client', 'blue', :e)",
          o=org, s=ns.space, e=w.alice)
    if not org_wide:
        _exec(w.p, "DELETE FROM pm_project_grants WHERE project_id = CAST(:s AS uuid)", s=ns.space)
        _exec(w.p, "INSERT INTO pm_project_grants (organization_id, project_id, subject, created_by)"
              " VALUES (:o, :s, :e, :e)", o=org, s=ns.space, e=w.alice)
    ns.project = await _project(call, w.u_alice, ns.space, f"Install {ns.tag}")
    ns.lanes = _lanes(w.p, ns.space)
    ns.types = _types(w.p, ns.space)
    ns.a = await _make_task(call, w.u_alice, {
        "project_id": ns.project, "title": "Survey the site", "description": "Walk the floor.",
        "status_id": ns.lanes["Review"], "type_id": ns.types["Feature"],
        "tags": ["urgent"], "custom_fields": {"customer_po": "PO-77", "size": "M"},
        "importance": 2, "estimate_mins": 90,
        "start_date": "2026-11-10", "due_at": "2026-11-15", "assignees": [w.alice],
    })
    ns.a1 = await _make_task(call, w.u_alice, {
        "project_id": ns.project, "title": "Measure the room", "parent_task_id": ns.a,
        "status_id": ns.lanes["To do"], "due_at": "2026-11-12",
    })
    ns.a1a = await _make_task(call, w.u_alice, {
        "project_id": ns.project, "title": "Bring the laser", "parent_task_id": ns.a1,
        "start_date": "2026-11-11", "tags": ["client"],
    })
    ns.b = await _make_task(call, w.u_alice, {
        "project_id": ns.project, "title": "Ship the kit", "status_id": ns.lanes["Done"],
    })
    gone = await _make_task(call, w.u_alice, {"project_id": ns.project, "title": "Old work"})
    filed = await call(w.u_alice, "POST", f"/projects/tasks/{gone}/archive", {})
    assert filed.status_code == 200, filed.text
    said = await call(w.u_alice, "POST", f"/projects/tasks/{ns.a}/comments",
                      {"body": "A comment that must not travel."})
    assert said.status_code == 201, said.text
    return ns


async def _save(call: Any, user: Any, project: str, name: str, **extra: Any) -> httpx.Response:
    return await call(user, "POST", "/projects/templates",
                      {"project_id": project, "name": name, **extra})


async def _apply(call: Any, user: Any, template: str, parent: str, key: str,
                 **extra: Any) -> httpx.Response:
    return await call(user, "POST", f"/projects/templates/{template}/apply",
                      {"idempotency_key": key, "parent_project_id": parent, **extra})


def _error(response: httpx.Response) -> str:
    detail = response.json()["detail"]
    return detail["error"] if isinstance(detail, dict) else str(detail)


@pytest.fixture(scope="module")
def world(promoted):  # noqa: F811 — a fixture
    ns = SimpleNamespace(p=promoted)
    ns.alice = "alice@templates-r8.test"
    ns.bob = "bob@templates-r8.test"
    ns.zed = "zed@templates-r8-b.test"
    for email in (ns.alice, ns.bob):
        _member(promoted, promoted.org_a, email)
    _member(promoted, promoted.org_b, ns.zed)
    ns.u_alice = _principal(ns.alice, promoted.org_a)
    ns.u_bob = _principal(ns.bob, promoted.org_a)
    ns.u_zed = _principal(ns.zed, promoted.org_b)
    return ns


@pytest.fixture
def w(world, monkeypatch):
    """The flag on for both orgs."""
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "projects_templates_orgs",
                        f"{world.p.org_a},{world.p.org_b}")
    return world


@_DB_GATE
class TestTheTemplateStoreOnARealDatabase:
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

    # ── The flag ────────────────────────────────────────────────────────

    async def test_with_the_flag_off_every_route_answers_404(self, w, monkeypatch):
        from acb_common import get_settings

        async with _session(w.p) as call:
            src = await _source(call, w)
            saved = await _save(call, w.u_alice, src.project, f"Flag {src.tag}")
            assert saved.status_code == 201, saved.text
            tid = saved.json()["id"]
            monkeypatch.setattr(get_settings(), "projects_templates_orgs", w.p.org_b)
            before = [_count(w.p, f"SELECT count(*) FROM {t}") for t in _TABLES]
            projects = _count(w.p, "SELECT count(*) FROM pm_projects")
            calls = [
                ("GET", "/projects/templates", None),
                ("POST", "/projects/templates", {"project_id": src.project, "name": "Off"}),
                ("POST", f"/projects/templates/{tid}/apply",
                 {"idempotency_key": "off-1", "parent_project_id": src.space}),
            ]
            assert {(m, re.sub(r"/[0-9a-f-]{36}", "/{}", path)) for m, path, _ in calls} == {
                (m, re.sub(r"\{[a-z_]+\}", "{}", path)) for m, path in ROUTES}
            for method, path, body in calls:
                refused = await call(w.u_alice, method, path, body)
                assert refused.status_code == 404, (method, path, refused.text)
                assert refused.json() == {"detail": "Not Found"}, (method, path)
            assert [_count(w.p, f"SELECT count(*) FROM {t}") for t in _TABLES] == before
            assert _count(w.p, "SELECT count(*) FROM pm_projects") == projects
            ok = await call(w.u_zed, "GET", "/projects/templates")
            assert ok.status_code == 200 and ok.json()["rows"] == []

    # ── The round trip ──────────────────────────────────────────────────

    async def test_a_round_trip_recreates_the_project_with_its_dates(self, w):
        async with _session(w.p) as call:
            src = await _source(call, w)
            saved = await _save(call, w.u_alice, src.project, f"Install kit {src.tag}",
                                description="The steps of one install.")
            assert saved.status_code == 201, saved.text
            out = saved.json()
            assert out["kind"] == "project" and out["name"] == f"Install kit {src.tag}"
            assert out["counts"] == {"statuses": 5, "types": 4, "fields": 2, "tags": 2, "tasks": 4}
            assert "payload" not in out and out["created_by"] == w.alice

            # The payload holds no id of the source, no person and no comment.
            stored = _one(w.p, "SELECT payload::text AS body, organization_id, source_project_id"
                          " FROM pm_templates WHERE id = CAST(:t AS uuid)", t=out["id"])
            assert str(stored.organization_id) == w.p.org_a
            assert str(stored.source_project_id) == src.project
            for secret in (src.space, src.project, src.a, src.a1, src.a1a, src.b,
                           *src.lanes.values(), *src.types.values(), w.alice,
                           "A comment that must not travel.", "Old work"):
                assert secret not in stored.body, secret
            body = json.loads(stored.body)
            assert body["schema_version"] == 1 and body["work_style"] is None
            [root_a, root_b] = body["tasks"]
            assert root_a["title"] == "Survey the site" and root_a["status"] == "Review"
            assert (root_a["start_offset_days"], root_a["due_offset_days"]) == (0, 5)
            assert root_b["title"] == "Ship the kit" and root_b["status"] is None  # done -> open

            listed = await call(w.u_bob, "GET", "/projects/templates")
            assert listed.status_code == 200
            assert out["id"] in {r["id"] for r in listed.json()["rows"]}

            # Apply in another space, whose lanes differ: the project owns a copy.
            other = await _space(call, w.u_alice, f"Other {src.tag}")
            made = await _apply(call, w.u_alice, out["id"], other, f"k-{src.tag}",
                                start_date="2026-12-01")
            assert made.status_code == 201, made.text
            result = made.json()
            new = result["project"]["id"]
            assert result["replayed"] is False and result["own_statuses"] is True
            assert result["project"]["parent_project_id"] == other
            assert result["project"]["name"] == f"Install kit {src.tag}"
            assert result["created"] == {"statuses": 5, "types": 1, "fields": 2, "tags": 2,
                                         "tasks": 4}
            node = _one(w.p, "SELECT owns_statuses, organization_id FROM pm_projects"
                        " WHERE id = CAST(:n AS uuid)", n=new)
            assert node.owns_statuses is True and str(node.organization_id) == w.p.org_a
            assert set(_lanes(w.p, new)) == {"Backlog", "To do", "In progress", "Review", "Done"}
            assert {"Feature", "Bug", "Task", "Epic"} <= set(_types(w.p, other))
            fields = {r.field_key: (r.field_type, r.required, json.loads(r.options)
                                    if isinstance(r.options, str) else r.options)
                      for r in _all(w.p, "SELECT field_key, field_type, required, options"
                                    " FROM pm_custom_fields WHERE project_id = CAST(:r AS uuid)",
                                    r=other)}
            assert fields == {"customer_po": ("text", False, []),
                              "size": ("select", False, ["S", "M", "L"])}
            tags = {r.name: r.color for r in _all(
                w.p, "SELECT name, color FROM pm_tags WHERE project_id = CAST(:r AS uuid)", r=other)}
            assert tags == {"urgent": "red", "client": "blue"}

            rows = {r.title: r for r in _all(
                w.p,
                "SELECT t.id, t.title, t.description, t.parent_task_id, t.root_project_id,"
                "       t.start_date, (t.due_at AT TIME ZONE 'UTC')::date AS due_day,"
                "       t.tags, t.custom_fields, t.importance, t.estimate_mins, t.created_by,"
                "       t.source, s.name AS lane, ty.name AS type_name"
                "  FROM pm_tasks t JOIN pm_task_statuses s ON s.id = t.status_id"
                "  LEFT JOIN pm_task_types ty ON ty.id = t.type_id"
                " WHERE t.project_id = CAST(:n AS uuid)", n=new)}
            assert set(rows) == {"Survey the site", "Measure the room", "Bring the laser",
                                 "Ship the kit"}
            a, a1, a1a, b = (rows["Survey the site"], rows["Measure the room"],
                             rows["Bring the laser"], rows["Ship the kit"])
            assert a.parent_task_id is None and b.parent_task_id is None
            assert str(a1.parent_task_id) == str(a.id) and str(a1a.parent_task_id) == str(a1.id)
            assert str(a.start_date) == "2026-12-01" and str(a.due_day) == "2026-12-06"
            assert str(a1.due_day) == "2026-12-03" and a1.start_date is None
            assert str(a1a.start_date) == "2026-12-02" and a1a.due_day is None
            assert (a.lane, a1.lane, a1a.lane) == ("Review", "To do", "To do")
            assert b.lane == "Backlog"  # a done task of the source starts open
            assert a.type_name == "Feature" and a.description == "Walk the floor."
            assert list(a.tags) == ["urgent"] and list(a1a.tags) == ["client"]
            values = a.custom_fields if isinstance(a.custom_fields, dict) else json.loads(a.custom_fields)
            assert values == {"customer_po": "PO-77", "size": "M"}
            assert (a.importance, a.estimate_mins) == (2, 90)
            for row in rows.values():
                assert row.created_by == w.alice and row.source == "manual"
                assert str(row.root_project_id) == other
            ids = [str(r.id) for r in rows.values()]
            assert _count(w.p, "SELECT count(*) FROM pm_task_assignees"
                          " WHERE task_id = ANY(CAST(:ids AS uuid[]))", ids=ids) == 0
            assert _count(w.p, "SELECT count(*) FROM pm_activities WHERE type = 'comment'"
                          " AND task_id = ANY(CAST(:ids AS uuid[]))", ids=ids) == 0
            note = _one(w.p, "SELECT body, meta FROM pm_activities WHERE project_id ="
                        " CAST(:n AS uuid) AND body LIKE 'Made from the template%'", n=new)
            assert note is not None

            # Apply in the source's own space: the lanes match, so it inherits.
            again = await _apply(call, w.u_alice, out["id"], src.space, f"k2-{src.tag}",
                                 name="Install two", start_date="2026-12-01")
            assert again.status_code == 201, again.text
            assert again.json()["own_statuses"] is False
            assert again.json()["created"]["statuses"] == 0
            assert again.json()["created"]["types"] == 0
            twin = again.json()["project"]["id"]
            assert _lanes(w.p, twin) == {}
            assert _count(w.p, "SELECT count(*) FROM pm_tasks WHERE project_id = CAST(:n AS uuid)",
                          n=twin) == 4
            # The space gained no second copy of anything.
            assert len(_types(w.p, src.space)) == 4
            assert _count(w.p, "SELECT count(*) FROM pm_custom_fields WHERE project_id ="
                          " CAST(:s AS uuid)", s=src.space) == 2

    async def test_a_name_is_taken_once_in_each_org(self, w):
        async with _session(w.p) as call:
            src = await _source(call, w)
            first = await _save(call, w.u_alice, src.project, f"Same {src.tag}")
            assert first.status_code == 201, first.text
            second = await _save(call, w.u_bob, src.project, f"SAME {src.tag}")
            assert second.status_code == 409 and _error(second) == templates.NAME_TAKEN
            extra = await call(w.u_alice, "POST", "/projects/templates",
                               {"project_id": src.project, "name": "x",
                                "organization_id": w.p.org_b})
            assert extra.status_code == 422  # the org never comes from the body

    # ── Idempotency ─────────────────────────────────────────────────────

    async def test_the_same_key_twice_makes_one_project(self, w):
        async with _session(w.p) as call:
            src = await _source(call, w)
            saved = await _save(call, w.u_alice, src.project, f"Once {src.tag}")
            tid = saved.json()["id"]
            target = await _space(call, w.u_alice, f"Target {src.tag}")
            count = "SELECT count(*) FROM pm_projects WHERE parent_project_id = CAST(:t AS uuid)"
            key = f"once-{src.tag}"
            first = await _apply(call, w.u_alice, tid, target, key)
            assert first.status_code == 201, first.text
            second = await _apply(call, w.u_alice, tid, target, key)
            assert second.status_code == 200, second.text
            assert second.json()["replayed"] is True
            assert second.json()["project"]["id"] == first.json()["project"]["id"]
            assert _count(w.p, count, t=target) == 1
            other_body = await _apply(call, w.u_alice, tid, target, key, name="Another")
            assert other_body.status_code == 409 and _error(other_body) == templates.KEY_USED
            assert _count(w.p, count, t=target) == 1
            # Another member may use the same key: a key is the member's own.
            bob = await _apply(call, w.u_bob, tid, target, key)
            assert bob.status_code == 201, bob.text
            assert _count(w.p, count, t=target) == 2

    async def test_a_race_of_two_applies_with_one_key_makes_one_project(self, w):
        async with _session(w.p) as call:
            src = await _source(call, w)
            tid = (await _save(call, w.u_alice, src.project, f"Race {src.tag}")).json()["id"]
            target = await _space(call, w.u_alice, f"Race target {src.tag}")
            key = f"race-{src.tag}"
            one, two = await asyncio.gather(
                _apply(call, w.u_alice, tid, target, key),
                _apply(call, w.u_alice, tid, target, key),
            )
            assert sorted([one.status_code, two.status_code]) == [200, 201], (one.text, two.text)
            assert one.json()["project"]["id"] == two.json()["project"]["id"]
            assert _count(w.p, "SELECT count(*) FROM pm_projects WHERE parent_project_id ="
                          " CAST(:t AS uuid)", t=target) == 1
            # The index holds under a direct second row too.
            with pytest.raises(IntegrityError):
                _exec(w.p, "INSERT INTO pm_template_applies (organization_id, applied_by,"
                      " idempotency_key, request_hash) VALUES (:o, :e, :k, :h)",
                      o=w.p.org_a, e=w.alice, k=key, h="0" * 64)

    # ── Tenancy ─────────────────────────────────────────────────────────

    async def test_org_b_cannot_list_read_or_apply_a_template_of_org_a(self, w, app_engine):  # noqa: F811
        async with _session(w.p) as call:
            src = await _source(call, w)
            tid = (await _save(call, w.u_alice, src.project, f"Tenant {src.tag}")).json()["id"]
            space_b = await _space(call, w.u_zed, f"B space {src.tag}")
            listed = await call(w.u_zed, "GET", "/projects/templates")
            assert listed.status_code == 200
            assert tid not in {r["id"] for r in listed.json()["rows"]}
            applied = await _apply(call, w.u_zed, tid, space_b, f"b-{src.tag}")
            assert applied.status_code == 404, applied.text
            saved = await _save(call, w.u_zed, src.project, f"Steal {src.tag}")
            assert saved.status_code == 404, saved.text
            assert _count(w.p, "SELECT count(*) FROM pm_projects WHERE parent_project_id ="
                          " CAST(:s AS uuid)", s=space_b) == 0
        # Under RLS, the app role bound to org B reads no row of org A, and
        # cannot write a row stamped with org A.
        with app_engine.begin() as c:
            c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": w.p.org_b})
            seen = c.execute(text("SELECT count(*) FROM pm_templates WHERE id = CAST(:t AS uuid)"),
                             {"t": tid}).scalar()
            assert seen == 0
            assert c.execute(text("SELECT count(*) FROM pm_template_applies")).scalar() == 0
        with app_engine.connect() as c, pytest.raises(DBAPIError):
            c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": w.p.org_b})
            c.execute(text(
                "INSERT INTO pm_templates (organization_id, name, payload, created_by)"
                " VALUES (:o, 'forged', '{\"schema_version\": 1}'::jsonb, 'x@y.z')"),
                {"o": w.p.org_a})

    # ── The private-source rule ─────────────────────────────────────────

    async def test_a_project_that_the_whole_org_cannot_see_is_no_source(self, w):
        async with _session(w.p) as call:
            src = await _source(call, w, org_wide=False)
            before = _count(w.p, "SELECT count(*) FROM pm_templates")
            refused = await _save(call, w.u_alice, src.project, f"Private {src.tag}")
            assert refused.status_code == 403, refused.text
            assert _error(refused) == templates.SOURCE_NOT_ORG_WIDE
            # Bob cannot see the project at all, so he gets 404 and learns nothing.
            hidden = await _save(call, w.u_bob, src.project, f"Hidden {src.tag}")
            assert hidden.status_code == 404
            # A space holds no tasks, so it is no source either.
            space = await _save(call, w.u_alice, src.space, f"Space {src.tag}")
            assert space.status_code == 422 and _error(space) == templates.SOURCE_NOT_A_PROJECT
            assert _count(w.p, "SELECT count(*) FROM pm_templates") == before

    async def test_an_agent_with_pins_sees_only_a_template_whose_source_it_sees(self, w):
        from acb_auth import AgentScope

        async with _session(w.p) as call:
            src = await _source(call, w)
            tid = (await _save(call, w.u_alice, src.project, f"Agent {src.tag}")).json()["id"]
            elsewhere = await _space(call, w.u_alice, f"Elsewhere {src.tag}")
            outside = _principal(w.alice, w.p.org_a, agent=AgentScope(
                connection_id=str(uuid.uuid4()), pinned_projects=(elsewhere,),
                actor_via="mcp:test"))
            listed = await call(outside, "GET", "/projects/templates")
            assert listed.status_code == 200
            assert tid not in {r["id"] for r in listed.json()["rows"]}
            refused = await _apply(call, outside, tid, elsewhere, f"pin-{src.tag}")
            assert refused.status_code == 404, refused.text
            # Pinned to the source's space: it sees the template and applies it
            # under the space, and each task carries the agent's source.
            inside = _principal(w.alice, w.p.org_a, agent=AgentScope(
                connection_id=str(uuid.uuid4()), pinned_projects=(src.space,),
                actor_via="mcp:test"))
            listed = await call(inside, "GET", "/projects/templates")
            assert tid in {r["id"] for r in listed.json()["rows"]}
            made = await _apply(call, inside, tid, src.space, f"pin-in-{src.tag}")
            assert made.status_code == 201, made.text
            sources = {r.source for r in _all(
                w.p, "SELECT source FROM pm_tasks WHERE project_id = CAST(:n AS uuid)",
                n=made.json()["project"]["id"])}
            assert sources == {"mcp"}

    # ── The caps ────────────────────────────────────────────────────────

    async def test_the_caps_refuse_an_oversized_template(self, w):
        async with _session(w.p) as call:
            src = await _source(call, w)
            # The save refuses a source with more tasks than the cap.
            big = await _project(call, w.u_alice, src.space, f"Big {src.tag}")
            _exec(w.p,
                  "INSERT INTO pm_tasks (organization_id, project_id, root_project_id, task_number,"
                  " status_id, title, created_by) SELECT CAST(:o AS uuid), CAST(:pid AS uuid),"
                  " CAST(:r AS uuid), 100000 + g, CAST(:s AS uuid), 'T' || g, :e"
                  " FROM generate_series(1, :n) g",
                  o=w.p.org_a, pid=big, r=src.space, s=src.lanes["To do"], e=w.alice,
                  n=templates.MAX_TASKS + 1)
            refused = await _save(call, w.u_alice, big, f"Big {src.tag}")
            assert refused.status_code == 422 and _error(refused) == templates.TOO_LARGE
            # The apply refuses a stored payload past a cap, and makes nothing.
            deep: dict[str, Any] = _task("leaf")
            for i in range(templates.MAX_TASK_DEPTH):
                deep = _task(f"L{i}", [deep])
            tid = str(uuid.uuid4())
            _exec(w.p, "INSERT INTO pm_templates (id, organization_id, name, payload, created_by)"
                  " VALUES (:id, :o, :n, CAST(:b AS jsonb), :e)",
                  id=tid, o=w.p.org_a, n=f"Deep {src.tag}", b=json.dumps(_payload([deep])),
                  e=w.alice)
            projects = _count(w.p, "SELECT count(*) FROM pm_projects")
            applied = await _apply(call, w.u_alice, tid, src.space, f"deep-{src.tag}")
            assert applied.status_code == 422 and _error(applied) == templates.TOO_LARGE
            assert _count(w.p, "SELECT count(*) FROM pm_projects") == projects
            assert _count(w.p, "SELECT count(*) FROM pm_template_applies WHERE idempotency_key"
                          " = :k", k=f"deep-{src.tag}") == 0
            # The database refuses a payload past the byte cap, and a wrong version.
            with pytest.raises(IntegrityError):
                _exec(w.p, "INSERT INTO pm_templates (organization_id, name, payload, created_by)"
                      " VALUES (:o, :n, CAST(:b AS jsonb), :e)", o=w.p.org_a, n=f"Huge {src.tag}",
                      b=json.dumps({"schema_version": 1, "pad": "x" * templates.MAX_PAYLOAD_BYTES}),
                      e=w.alice)
            with pytest.raises(IntegrityError):
                _exec(w.p, "INSERT INTO pm_templates (organization_id, name, payload, created_by)"
                      " VALUES (:o, :n, '{}'::jsonb, :e)", o=w.p.org_a, n=f"Bare {src.tag}",
                      e=w.alice)
