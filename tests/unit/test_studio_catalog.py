"""``GET /studio/actions`` — the hermetic half (STU-B1).

Spec: ``project-docs/specs/ai_studio.md`` §3.2 and §14 B1. The R8 half, on a
real Postgres, is ``test_studio_catalog_r8.py``. It proves the tenant wall.
This file proves:

- the flag: a not-listed org gets 404, a listed org and ``*`` get 200;
- the count: door ops, plus integration tools, plus published workflows,
  with no duplicate id;
- the class mapping, row by row, and no row of class X;
- the read takes no org from the request;
- B1c: the rows of the caller's own packages (``package_rows``), their class,
  the ``?app=`` filter, and one statement however many packages exist.
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
from gateway.studio import packages_store

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
    # B1c: the package read is faked here, so these tests see no package row.
    fake.package_calls = []
    fake.packages = []

    async def _packages(db, member, *, package_id=None):
        fake.package_calls.append((member, package_id))
        return [p for p in fake.packages if package_id in (None, p.id)]

    monkeypatch.setattr(packages_store, "list_package_actions", _packages)
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

    def test_a_workflow_is_class_b_with_no_input_schema(self, monkeypatch, db):
        _open(monkeypatch, "*")
        one, two = _wf("One"), _wf("Two", {"topic": "x"})
        db.rows = [one, two]
        rows = {r["id"]: r for r in _client().get(URL).json()["actions"]
                if r["source"] == "workflow"}
        assert set(rows) == {f"workflow:{one.id}", f"workflow:{two.id}"}
        assert all(r["class"] == "B" for r in rows.values())
        # variables are start values, not an input schema (runner.py state["vars"]).
        assert all(r["input_schema"] is None for r in rows.values())

    def test_a_manifest_schema_is_the_door_args_schema(self, monkeypatch, db):
        from gateway.routes.agent_apps import args_schema

        _open(monkeypatch, "*")
        rows = {r["id"]: r for r in _client().get(URL).json()["actions"]
                if r["source"] == "manifest"}
        for app in every_app():
            if app.door != "ops":
                continue
            for name in door_ops(app):
                assert rows[f"{app.id}.{name}"]["input_schema"] == args_schema(app.id, name)
        propose = args_schema("knowledge", "propose")
        assert "kind" in propose["required"]
        assert "kind" in rows["knowledge.propose"]["input_schema"]["required"]
        listed = args_schema("knowledge", "list_pages")
        assert "q" in listed["properties"]
        assert "q" in rows["knowledge.list_pages"]["input_schema"]["properties"]

    def test_tripwire_the_real_registry_has_no_integration_tool(self):
        # No monkeypatch. Today list_tools() holds http.request, notify.activity
        # and notify.whatsapp, all with integration=None, so source 2 gives 0 rows.
        # The first tool that registers with an integration must update this test.
        assert actions._integration_rows() == []

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


# --- B1c: the rows of the caller's own packages ------------------------------

PKG_A = str(uuid.uuid4())
PKG_B = str(uuid.uuid4())
VERBS = {"list": "A", "get": "A", "create": "B", "update": "B", "delete": "C"}


def _pkg(pid, objects=(), actions=()):
    return packages_store.PackageActions(id=pid, objects=tuple(objects), actions=tuple(actions))


def _rows(db, monkeypatch, **params):
    _open(monkeypatch, "*")
    res = _client().get(URL, params=params)
    assert res.status_code == 200
    return [r for r in res.json()["actions"] if r["source"] == "package"]


class TestThePackageRows:
    def test_an_object_gives_five_rows_with_the_class_of_its_verb(self, monkeypatch, db):
        db.packages = [_pkg(PKG_A, ["items"])]
        rows = _rows(db, monkeypatch)
        assert {r["id"]: r["class"] for r in rows} == {
            f"object:items.{v}": c for v, c in VERBS.items()}
        assert {r["app"] for r in rows} == {PKG_A}
        assert all("uses" not in r for r in rows)

    def test_a_stored_object_name_that_a2_refuses_gives_no_row(self, monkeypatch, db):
        db.packages = [_pkg(PKG_A, ["items", "Items", "x" * 3000, "a.b", "ok_2"])]
        names = {r["id"].split(":", 1)[1].rsplit(".", 1)[0] for r in _rows(db, monkeypatch)}
        assert names == {"items", "ok_2"}

    def test_every_row_has_the_shape_of_the_catalog_and_a_named_one_adds_uses(
            self, monkeypatch, db):
        db.packages = [_pkg(PKG_A, ["items"], [("peek", "object:items.get", "Peek")])]
        for r in _rows(db, monkeypatch):
            assert set(r) - {"uses"} == {"id", "title", "source", "class", "input_schema",
                                         "output_schema", "connection", "app"}

    def test_a_named_action_is_one_row_with_uses_and_the_class_of_its_target(
            self, monkeypatch, db):
        db.packages = [_pkg(PKG_A, [], [("peek", "object:items.get", "Peek it"),
                                        ("drop", "object:items.delete", None)])]
        rows = {r["id"]: r for r in _rows(db, monkeypatch)}
        assert set(rows) == {"peek", "drop"}
        assert (rows["peek"]["uses"], rows["peek"]["class"], rows["peek"]["title"]) == (
            "object:items.get", "A", "Peek it")
        assert (rows["drop"]["class"], rows["drop"]["title"]) == ("C", "drop")

    def test_a_named_action_on_a_catalog_target_takes_the_catalog_class(
            self, monkeypatch, db):
        monkeypatch.setattr(actions, "list_tools", lambda: [_tool("zoho.x", read_only=True)])
        db.packages = [_pkg(PKG_A, [], [("sync", "integration:zoho.x", None)])]
        rows = _rows(db, monkeypatch)
        assert [(r["id"], r["uses"], r["class"]) for r in rows] == [
            ("sync", "integration:zoho.x", "A")]

    def test_a_class_x_target_and_an_action_outside_the_catalog_are_left_out(
            self, monkeypatch, db):
        async def _cat():
            return [{"id": "knowledge.secret", "class": "X", "source": "manifest"},
                    {"id": "knowledge.list_pages", "class": "A", "source": "manifest"}]

        monkeypatch.setattr(actions, "catalog_rows", _cat)
        db.packages = [_pkg(PKG_A, [], [
            ("x", "knowledge.secret", None), ("gone", "knowledge.nothing", None),
            ("bad", "not an id", None), ("ok", "knowledge.list_pages", None)])]
        assert [r["id"] for r in _rows(db, monkeypatch)] == ["ok"]

    def test_two_packages_with_one_object_name_give_two_rows_with_different_app(
            self, monkeypatch, db):
        db.packages = [_pkg(PKG_A, ["items"]), _pkg(PKG_B, ["items"])]
        rows = [r for r in _rows(db, monkeypatch) if r["id"] == "object:items.list"]
        assert {r["app"] for r in rows} == {PKG_A, PKG_B}

    def test_the_app_filter_narrows_to_one_package(self, monkeypatch, db):
        db.packages = [_pkg(PKG_A, ["items"]), _pkg(PKG_B, ["items"])]
        _open(monkeypatch, "*")
        body = _client().get(URL, params={"app": PKG_B.upper()}).json()["actions"]
        assert {r["app"] for r in body} == {PKG_B}
        assert len(body) == 5
        assert db.package_calls[-1][1] == PKG_B

    def test_a_bad_app_filter_is_422_and_reads_nothing(self, monkeypatch, db):
        _open(monkeypatch, "*")
        assert _client().get(URL, params={"app": "nope"}).status_code == 422
        assert db.package_calls == []

    def test_the_member_is_the_caller_and_never_a_query_value(self, monkeypatch, db):
        db.packages = [_pkg(PKG_A, ["items"])]
        _rows(db, monkeypatch, member="evil@example.test", owner_email="evil@example.test")
        assert {m for m, _ in db.package_calls} == {"m@example.test"}

    def test_the_switch_off_reads_no_package(self, monkeypatch, db):
        _open(monkeypatch, OTHER)
        assert _client().get(URL, params={"app": "nope"}).status_code == 404
        assert db.package_calls == []

    def test_no_package_gives_the_old_catalog_unchanged(self, monkeypatch, db):
        assert _rows(db, monkeypatch) == []

    def test_a_fence_catalog_rows_holds_no_package_row(self, monkeypatch, db):
        import asyncio
        import inspect

        db.packages = [_pkg(PKG_A, ["items"], [("peek", "object:items.get", None)])]
        _open(monkeypatch, "*")
        rows = asyncio.run(actions.catalog_rows())
        assert all(r["source"] != "package" for r in rows)
        assert db.package_calls == []
        body = inspect.getsource(actions.catalog_rows).split('"""')[2]
        assert "package_rows" not in body and "packages_store" not in body


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _StatementDb:
    def __init__(self, n):
        self.sql: list[str] = []
        self.params: list[dict] = []
        self.n = n

    async def execute(self, stmt, params=None):
        self.sql.append(str(stmt))
        self.params.append(params)
        return _Rows([
            (str(uuid.uuid4()), ["items"],
             [{"id": "peek", "uses": "object:items.get", "title": None}])
            for _ in range(self.n)])


class TestThePackageStatement:
    @pytest.mark.parametrize("n", [1, 5, 60])
    def test_one_statement_however_many_packages(self, n):
        import asyncio

        db = _StatementDb(n)
        out = asyncio.run(packages_store.list_package_actions(db, "Ana@Example.test"))
        assert len(out) == n
        assert len(db.sql) == 1
        assert db.params[0] == {"m": "ana@example.test", "app": None}

    def test_the_statement_binds_the_org_and_the_member_and_loads_no_manifest(self):
        sql = packages_store._ACTIONS_SQL
        assert "app.tenant_id" in sql
        assert "owner_email = :m" in sql
        assert "format = 'manifest'" in sql
        assert "SELECT manifest" not in sql
        assert ", manifest," not in sql

    def test_a_package_id_that_is_not_a_uuid_is_refused_before_a_statement(self):
        import asyncio

        db = _StatementDb(1)
        with pytest.raises(packages_store.PackageStoreError):
            asyncio.run(packages_store.list_package_actions(db, "a@b.test", package_id="x"))
        assert db.sql == []
