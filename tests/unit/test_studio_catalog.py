"""``GET /studio/actions`` — the hermetic half (STU-B1).

Spec: ``project-docs/specs/ai_studio.md`` §3.2 and §14 B1. The R8 half, on a
real Postgres, is ``test_studio_catalog_r8.py``. It proves the tenant wall.
This file proves:

- the flag: a not-listed org gets 404, a listed org and ``*`` get 200;
- the count: door ops, plus integration tools, plus published workflows,
  with no duplicate id;
- the class mapping, row by row, and no row of class X;
- the read takes no org from the request.
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from acb_auth import UserContext, UserRole, build_access, get_current_user
from acb_common import get_settings
from acb_common.app_manifests import door_ops, every_app
from fastapi import FastAPI
from fastapi.testclient import TestClient
from gateway.routes.studio import actions, flags
from gateway.routes.workflows.tools import WorkflowToolSpec, list_tools

ORG = str(uuid.uuid4())
OTHER = str(uuid.uuid4())
URL = "/studio/actions"


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeDB:
    def __init__(self, rows):
        self.rows = rows
        self.sql: list[str] = []

    async def execute(self, stmt, params=None):
        self.sql.append(str(stmt))
        return _Result(self.rows)


def _wf(name, variables=None):
    return SimpleNamespace(id=uuid.uuid4(), name=name, variables=variables or {})


@pytest.fixture
def db(monkeypatch):
    fake = _FakeDB([])

    @asynccontextmanager
    async def _session():
        yield fake

    monkeypatch.setattr(actions, "_tenant_session", _session)
    return fake


def _client(org: str | None = ORG) -> TestClient:
    app = FastAPI()
    app.include_router(actions.router)
    app.dependency_overrides[get_current_user] = lambda: UserContext(
        email="m@example.test", role=UserRole.EMPLOYEE,
        access=build_access(["feature:workflows"]), organization_id=org)
    return TestClient(app)


def _open(monkeypatch, value: str) -> None:
    monkeypatch.setattr(get_settings(), "studio_orgs", value)


def _tool(action, *, integration="zoho", read_only=False, destructive=False,
          open_world=False, args=None):
    return WorkflowToolSpec(
        action=action, label=f"Label {action}", description="d",
        integration=integration, read_only=read_only, destructive=destructive,
        open_world=open_world, args_schema=args or {})


class TestTheFlag:
    def test_it_is_off_by_default(self):
        from acb_common.settings import Settings

        assert Settings.model_fields["studio_orgs"].default == ""

    def test_empty_opens_no_org(self, monkeypatch, db):
        _open(monkeypatch, "")
        assert _client().get(URL).status_code == 404

    def test_a_not_listed_org_gets_404(self, monkeypatch, db):
        _open(monkeypatch, OTHER)
        assert _client().get(URL).status_code == 404
        assert db.sql == []

    def test_a_listed_org_gets_200(self, monkeypatch, db):
        _open(monkeypatch, f"{OTHER}, {ORG.upper()}")
        assert _client().get(URL).status_code == 200

    def test_star_opens_every_org(self, monkeypatch, db):
        _open(monkeypatch, "*")
        assert _client().get(URL).status_code == 200

    def test_no_org_gets_404_even_with_star(self, monkeypatch, db):
        _open(monkeypatch, "*")
        assert _client(org=None).get(URL).status_code == 404

    def test_a_query_cannot_name_the_org(self, monkeypatch, db):
        _open(monkeypatch, OTHER)
        assert _client().get(URL, params={"organization_id": OTHER}).status_code == 404


class TestTheRows:
    def test_the_count_is_the_sum_of_the_sources(self, monkeypatch, db):
        _open(monkeypatch, "*")
        db.rows = [_wf("One"), _wf("Two", {"a": 1})]
        body = _client().get(URL).json()["actions"]
        doors = sum(len(door_ops(a)) for a in every_app() if a.door == "ops")
        tools = sum(1 for t in list_tools() if t.integration)
        assert doors > 0
        assert len(body) == doors + tools + 2
        ids = [r["id"] for r in body]
        assert len(ids) == len(set(ids))

    def test_every_row_has_the_shape(self, monkeypatch, db):
        _open(monkeypatch, "*")
        db.rows = [_wf("One")]
        for row in _client().get(URL).json()["actions"]:
            assert set(row) == {"id", "title", "source", "class", "input_schema",
                                "output_schema", "connection", "app"}
            assert row["source"] in {"manifest", "integration", "workflow"}
            assert row["class"] in {"A", "B", "C"}
            assert row["output_schema"] is None

    def test_a_manifest_row_keeps_the_route_class(self, monkeypatch, db):
        _open(monkeypatch, "*")
        rows = {r["id"]: r for r in _client().get(URL).json()["actions"]
                if r["source"] == "manifest"}
        for app in every_app():
            if app.door != "ops":
                continue
            for name, op in door_ops(app).items():
                row = rows[f"{app.id}.{name}"]
                assert row["class"] == op.route.cls
                assert row["app"] == app.id
                assert row["connection"] is None

    def test_no_row_is_class_x(self, monkeypatch, db):
        _open(monkeypatch, "*")
        assert all(r["class"] != "X" for r in _client().get(URL).json()["actions"])

    def test_a_row_with_no_op_never_appears(self, monkeypatch, db):
        _open(monkeypatch, "*")
        ops = sum(1 for a in every_app() if a.door == "ops" for r in a.routes if r.op)
        rows = [r for r in _client().get(URL).json()["actions"]
                if r["source"] == "manifest"]
        assert len(rows) == ops

    @pytest.mark.parametrize("flags_,expected", [
        (dict(read_only=True), "A"),
        (dict(), "B"),
        (dict(destructive=True), "C"),
        (dict(open_world=True), "C"),
        (dict(read_only=True, open_world=True), "C"),
        (dict(read_only=True, destructive=True), "C"),
    ])
    def test_the_integration_class_mapping(self, monkeypatch, db, flags_, expected):
        _open(monkeypatch, "*")
        monkeypatch.setattr(actions, "list_tools", lambda: [_tool("zoho.x", **flags_)])
        rows = [r for r in _client().get(URL).json()["actions"]
                if r["source"] == "integration"]
        assert [(r["id"], r["class"], r["connection"]) for r in rows] == [
            ("integration:zoho.x", expected, "zoho")]

    def test_a_tool_with_no_integration_never_appears(self, monkeypatch, db):
        _open(monkeypatch, "*")
        monkeypatch.setattr(actions, "list_tools", lambda: [
            _tool("http.request", integration=None), _tool("zoho.x")])
        rows = [r["id"] for r in _client().get(URL).json()["actions"]
                if r["source"] == "integration"]
        assert rows == ["integration:zoho.x"]

    def test_the_tool_schema_reads_the_args(self, monkeypatch, db):
        _open(monkeypatch, "*")
        monkeypatch.setattr(actions, "list_tools", lambda: [
            _tool("zoho.x", args={"name": "string|Who", "note": "string?|Extra"})])
        row = next(r for r in _client().get(URL).json()["actions"]
                   if r["source"] == "integration")
        assert row["input_schema"]["required"] == ["name"]
        assert set(row["input_schema"]["properties"]) == {"name", "note"}

    def test_a_workflow_is_class_b_with_its_variables(self, monkeypatch, db):
        _open(monkeypatch, "*")
        one, two = _wf("One"), _wf("Two", {"topic": "x"})
        db.rows = [one, two]
        rows = {r["id"]: r for r in _client().get(URL).json()["actions"]
                if r["source"] == "workflow"}
        assert set(rows) == {f"workflow:{one.id}", f"workflow:{two.id}"}
        assert all(r["class"] == "B" for r in rows.values())
        assert rows[f"workflow:{one.id}"]["input_schema"] == {}
        assert rows[f"workflow:{two.id}"]["input_schema"] == {"topic": "x"}

    def test_the_query_reads_published_only(self, monkeypatch, db):
        _open(monkeypatch, "*")
        _client().get(URL)
        assert len(db.sql) == 1
        assert "status = 'published'" in db.sql[0]
        assert "organization_id" not in db.sql[0]

    def test_the_probe_is_never_called(self, monkeypatch, db):
        _open(monkeypatch, "*")
        import acb_skills.integrations as integrations

        def boom(*a, **kw):
            raise AssertionError("the catalog must not probe the integrations")

        monkeypatch.setattr(integrations, "build_integrations", boom)
        assert _client().get(URL).status_code == 200


def test_the_flag_reader_is_per_call(monkeypatch):
    _open(monkeypatch, "")
    assert flags.studio_allowed(ORG) is False
    _open(monkeypatch, ORG)
    assert flags.studio_allowed(ORG) is True
