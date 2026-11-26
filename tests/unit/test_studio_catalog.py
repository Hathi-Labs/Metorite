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
  the ``?app=`` filter, and one statement however many packages exist;
- F6a: the ``tool`` of each row is ``skill_packs.action_tool`` of its id.
  Mutation (2026-11-14): give a manifest row ``op.route.tool or None`` with
  ``also`` added, or drop ``tool`` from one row builder, and
  ``TestTheChatTool`` turns red.
- TOF-1a: the source ``tool``, one ``tool:<name>`` row for each chat tool of
  the CRM, Email, Projects and Calendar packs, behind
  ``STUDIO_CHAT_TOOLS_ORGS`` (``TestTheChatToolSource``). Each case names the
  mutation that turns it red.
"""
from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager, contextmanager
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
        access=build_access(["feature:studio"]), organization_id=org)
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
        """TOF-1a: ``STUDIO_CHAT_TOOLS_ORGS`` is empty by default, so the GET
        adds no ``tool`` row. ``TestTheChatToolSource`` counts them with the
        flag on."""
        _open(monkeypatch, "*")
        db.rows = [_wf("One"), _wf("Two", {"a": 1})]
        body = _client().get(URL).json()["actions"]
        doors = sum(len(door_ops(a)) for a in every_app() if a.door == "ops")
        tools = sum(1 for t in list_tools() if t.integration)
        assert doors > 0
        assert len(body) == doors + tools + 2
        assert all(r["source"] != "tool" for r in body)
        ids = [r["id"] for r in body]
        assert len(ids) == len(set(ids))

    def test_every_row_has_the_shape(self, monkeypatch, db):
        _open(monkeypatch, "*")
        db.rows = [_wf("One")]
        for row in _client().get(URL).json()["actions"]:
            assert set(row) == {"id", "title", "source", "class", "input_schema",
                                "output_schema", "connection", "app", "tool"}
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


# --- F6a: the chat tool of each row -------------------------------------------


class TestTheChatTool:
    """``tool`` comes from ``skill_packs.action_tool``, the one map (STU-F6a)."""

    def test_each_tool_equals_action_tool_of_the_id(self, monkeypatch, db):
        from acb_skills.skill_packs import action_tool

        _open(monkeypatch, "*")
        monkeypatch.setattr(actions, "list_tools", lambda: [_tool("zoho.x", read_only=True)])
        db.rows = [_wf("One")]
        db.packages = [_pkg(PKG_A, ["items"], [("peek", "object:items.get", "Peek")])]
        body = _client().get(URL).json()["actions"]
        assert {r["source"] for r in body} == {"manifest", "integration", "workflow", "package"}
        for row in body:
            assert row["tool"] == action_tool(row["id"]), row["id"]

    def test_the_three_knowledge_steps_of_the_demo_map_to_their_chat_tools(self, monkeypatch, db):
        _open(monkeypatch, "*")
        rows = {r["id"]: r["tool"] for r in _client().get(URL).json()["actions"]}
        assert rows["knowledge.list_spaces"] == "list_spaces"
        assert rows["knowledge.get_page"] == "read_page"
        assert rows["knowledge.propose"] == "propose_page"

    def test_a_workflow_maps_to_run_workflow_and_an_integration_to_none(self, monkeypatch, db):
        _open(monkeypatch, "*")
        monkeypatch.setattr(actions, "list_tools", lambda: [_tool("zoho.x")])
        one = _wf("One")
        db.rows = [one]
        rows = {r["id"]: r["tool"] for r in _client().get(URL).json()["actions"]}
        assert rows[f"workflow:{one.id}"] == "run_workflow"
        assert rows["integration:zoho.x"] is None

    def test_a_door_op_with_no_chat_tool_gives_none(self, monkeypatch, db):
        _open(monkeypatch, "*")
        rows = {r["id"]: r for r in _client().get(URL).json()["actions"]
                if r["source"] == "manifest"}
        for app in every_app():
            if app.door != "ops":
                continue
            for name, op in door_ops(app).items():
                assert rows[f"{app.id}.{name}"]["tool"] == (op.route.tool or None)

    def test_the_route_keeps_no_second_map(self):
        # The one map is ``action_tool``. A second map in the route would
        # drift from the skill loader. Mutation: write ``op.route.tool`` or a
        # literal ``"run_workflow"`` in actions.py, and this turns red.
        import inspect

        src = inspect.getsource(actions)
        assert "route.tool" not in src
        assert '"run_workflow"' not in src
        assert src.count("action_tool(") == 5


# --- B1c: the rows of the caller's own packages ------------------------------

PKG_A = str(uuid.uuid4())
PKG_B = str(uuid.uuid4())
#: The class of each object op. STU-D4b adds the aggregate, STU-D18-2a the tree
#: and C4a-2 the history, each a read.
VERBS = {"list": "A", "get": "A", "create": "B", "update": "B", "delete": "C", "aggregate": "A",
         "tree": "A", "history": "A"}


def _pkg(pid, objects=(), actions=()):
    return packages_store.PackageActions(id=pid, objects=tuple(objects), actions=tuple(actions))


def _rows(db, monkeypatch, **params):
    _open(monkeypatch, "*")
    res = _client().get(URL, params=params)
    assert res.status_code == 200
    return [r for r in res.json()["actions"] if r["source"] == "package"]


class TestThePackageRows:
    def test_an_object_gives_six_rows_with_the_class_of_its_verb(self, monkeypatch, db):
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
                                         "output_schema", "connection", "app", "tool"}

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
        # The five record verbs, the aggregate (STU-D4b), the tree (STU-D18-2a) and the
        # history (C4a-2): one row for each object op.
        assert len(body) == 8
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


# --- TOF-1a: the chat tools of the packs -------------------------------------

#: The owners of the packs in scope, and the pack left out (TOF-1a, decision 4).
#: The test reads the tools from the registry at run time: no count is golden.
_TOF_OWNERS = {"crm-assistant", "email-assistant", "projects-assistant"}
_TOF_LEFT_OUT = {"email-setup"}
_ROW_KEYS = {"id", "title", "source", "class", "input_schema", "output_schema",
             "connection", "app", "tool"}


def _door_tools() -> set[str]:
    return {op.route.tool for a in every_app() if a.door == "ops"
            for op in door_ops(a).values() if op.route.tool}


def _in_scope() -> list[str]:
    """Each tool of a pack in scope, in registry order, read now."""
    from acb_skills import skill_families as sf

    doors = _door_tools()
    return [n for slug in sf.pack_slugs()
            if sf.SKILL_FAMILIES[slug]["owner"] in _TOF_OWNERS and slug not in _TOF_LEFT_OUT
            for n in sf.SKILL_FAMILIES[slug]["tools"]
            if not n.startswith("studio_") and n not in doors]


def _risky(name: str, **hints):
    """A fake tool function with the annotation *hints*."""
    def fn() -> None:
        return None

    fn.__name__ = name
    fn.__tool_risk__ = dict(hints)
    return fn


_READ = dict(read_only=True, destructive=False, idempotent=True, open_world=False)
_WRITE = dict(read_only=False, destructive=False, idempotent=False, open_world=False)


@pytest.fixture
def loaded(monkeypatch):
    """The owner load as a dict that a test fills. Empty: every row is C."""
    from gateway.studio import chat_tool_actions

    tools: dict = {}
    monkeypatch.setattr(chat_tool_actions, "_owner_tools", lambda packs: dict(tools))
    return tools


@contextmanager
def _bound(org: str = ORG):
    from acb_common.db import bind_tenant, release_tenant

    token = bind_tenant(org)
    try:
        yield
    finally:
        release_tenant(token)


def _chat_tools_on(monkeypatch, value: str) -> None:
    monkeypatch.setattr(get_settings(), "studio_chat_tools_orgs", value)


@pytest.fixture(scope="module")
def real_rows():
    """The rows from the real owners, through THE loader of a pack owner.
    One load for the module: the Projects owner builds about 140 tools."""
    pytest.importorskip("agent_framework", reason="MAF not installed")
    from gateway.studio import chat_tool_actions

    return chat_tool_actions.chat_tool_rows()


_RANK = {"A": 0, "B": 1, "C": 2, "X": 3}


class TestTheChatToolSource:
    """TOF-1a. ``gateway.studio.chat_tool_actions`` and ``actions._chat_tool_rows``."""

    def test_the_flag_is_off_by_default_and_reloads(self):
        """Mutation: give the setting a default, or rename it so that the
        reload list misses it → red."""
        from acb_common.settings import Settings
        from gateway.flag_reload import reloadable

        assert Settings.model_fields["studio_chat_tools_orgs"].default == ""
        assert reloadable("STUDIO_CHAT_TOOLS_ORGS")

    def test_the_flag_off_gives_no_row_and_loads_no_tool(self, monkeypatch):
        """Mutation: drop the flag check of ``_chat_tool_rows``, or read the
        org from anything but the bound tenant → red."""
        from gateway.studio import chat_tool_actions

        def boom():
            raise AssertionError("a dark org must not build the rows")

        monkeypatch.setattr(chat_tool_actions, "chat_tool_rows", boom)
        _chat_tools_on(monkeypatch, "")
        with _bound():
            assert actions._chat_tool_rows() == []
        _chat_tools_on(monkeypatch, OTHER)
        with _bound():
            assert actions._chat_tool_rows() == []
        _chat_tools_on(monkeypatch, "*")
        assert actions._chat_tool_rows() == []  # no bound tenant
        assert flags.chat_tools_allowed(None) is False
        _chat_tools_on(monkeypatch, f" {ORG.upper()} ")
        assert flags.chat_tools_allowed(ORG) and not flags.chat_tools_allowed(OTHER)

    def test_the_count_adds_one_row_for_each_chat_tool(self, monkeypatch, db, loaded):
        """With the flag on, ``catalog_rows`` holds each source, plus one row
        for each tool in scope, with no duplicate id. Mutation: leave
        ``_chat_tool_rows`` out of ``catalog_rows`` → red."""
        async def _no_mcp():
            return [], False

        monkeypatch.setattr(actions, "_mcp_rows", _no_mcp)
        _chat_tools_on(monkeypatch, ORG)
        db.rows = [_wf("One")]
        with _bound():
            rows = asyncio.run(actions.catalog_rows())
        doors = sum(len(door_ops(a)) for a in every_app() if a.door == "ops")
        tools = sum(1 for t in list_tools() if t.integration)
        scope = _in_scope()
        assert len(scope) > 0
        assert len(rows) == doors + tools + 1 + len(scope)
        assert [r["id"] for r in rows if r["source"] == "tool"] == [f"tool:{n}" for n in scope]
        ids = [r["id"] for r in rows]
        assert len(ids) == len(set(ids))

    def test_one_row_for_each_tool_of_the_packs_in_scope(self, loaded):
        """The unfiltered builder gives each tool in scope exactly one row, in
        registry order. CRM, Email, Projects and Calendar each give rows. No
        ``email-setup`` tool and no Studio builder tool gets a row. Mutation:
        drop the owner allow list, drop ``email-setup`` from the packs left
        out, or let a name through twice → red."""
        from acb_skills import skill_families as sf
        from gateway.studio import chat_tool_actions

        rows = chat_tool_actions.chat_tool_rows()
        names = [r["id"].removeprefix("tool:") for r in rows]
        assert names == _in_scope()
        assert len(names) == len(set(names))
        assert {"create_lead", "send_email", "update_task", "my_tasks_plan_day"} <= set(names)
        assert not set(names) & set(sf.SKILL_FAMILIES["email-setup"]["tools"])
        builder = {n for slug in sf.pack_slugs() if sf.SKILL_FAMILIES[slug]["owner"]
                   == "studio-builder" for n in sf.SKILL_FAMILIES[slug]["tools"]}
        assert builder and not set(names) & builder
        assert not any(n.startswith("studio_") for n in names)

    def test_a_tool_that_a_door_op_maps_gets_no_tool_row(self, monkeypatch, loaded):
        """A pack tool that a door op maps already keeps its door row only.
        Mutation: drop the door check of ``skill_packs._scope`` → red."""
        from acb_skills import skill_families as sf
        from gateway.studio import chat_tool_actions

        fam = sf.SKILL_FAMILIES["crm-records"]
        monkeypatch.setitem(sf.SKILL_FAMILIES, "crm-records",
                            {**fam, "tools": (*fam["tools"], "read_page")})
        ids = [r["id"] for r in chat_tool_actions.chat_tool_rows()]
        assert "tool:read_page" not in ids and "tool:create_lead" in ids
        rows = actions._manifest_rows()
        assert [r["id"] for r in rows if r["tool"] == "read_page"] == ["knowledge.get_page"]

    def test_each_row_has_the_shape_and_maps_its_tool(self, loaded):
        """Mutation: give a row a second map for ``tool``, or another source
        name → red."""
        from acb_skills.skill_packs import action_tool
        from gateway.studio import chat_tool_actions

        rows = chat_tool_actions.chat_tool_rows()
        assert rows
        for row in rows:
            assert set(row) == _ROW_KEYS
            assert row["source"] == "tool"
            assert row["class"] in {"A", "B", "C"}
            assert row["tool"] == action_tool(row["id"]) == row["id"].removeprefix("tool:")
            assert row["input_schema"] is None and row["output_schema"] is None
            assert row["connection"] is None and row["app"] is None
            assert row["title"]

    @pytest.mark.parametrize(("name", "hints", "expected"), [
        ("x_read", _READ, "A"),
        ("x_write", _WRITE, "B"),
        ("x_unknown", None, "C"),
        ("x_destroy", {**_WRITE, "destructive": True}, "C"),
        ("x_world", {**_READ, "open_world": True}, "C"),
        ("x_no_world", {k: v for k, v in _READ.items() if k != "open_world"}, "C"),
        ("x_no_destructive", {k: v for k, v in _READ.items() if k != "destructive"}, "C"),
        ("x_truthy", {**_READ, "read_only": "yes"}, "B"),
        ("compose_reply", _READ, "C"),
        ("send_draft", _READ, "C"),
        ("send_email", _READ, "C"),
        ("forward_email", _READ, "C"),
        ("unsubscribe_sender", _READ, "C"),
    ])
    def test_the_class_rule_fails_closed(self, name, hints, expected):
        """A only for an explicit read. C for a send of the two egress lists
        whatever it says, for an unknown annotation and for each missing
        key. Mutation: read ``read_only`` as truthy, treat a missing key as
        False, or drop a send list → red."""
        from gateway.studio.chat_tool_actions import chat_tool_class

        assert chat_tool_class(name, hints) == expected

    def test_a_fake_owner_gives_the_class_of_each_annotation(self, loaded):
        """The annotation comes from the tool or from its ``func`` (a MAF
        tool). A send that says it only reads is C. A tool that the owner did
        not build is C. Mutation: read the annotation of the name from
        ``TOOL_ANNOTATIONS``, or skip ``func`` → red."""
        from types import SimpleNamespace

        from gateway.studio import chat_tool_actions

        loaded.update({
            "search_crm": _risky("search_crm", **_READ),
            "get_pipeline": SimpleNamespace(func=_risky("get_pipeline", **_READ)),
            "update_task": _risky("update_task", **_WRITE),
            "compose_reply": _risky("compose_reply", **_READ),
        })
        rows = {r["id"]: r["class"] for r in chat_tool_actions.chat_tool_rows()}
        assert rows["tool:search_crm"] == "A"
        assert rows["tool:get_pipeline"] == "A"
        assert rows["tool:update_task"] == "B"
        assert rows["tool:compose_reply"] == "C"
        assert rows["tool:get_record"] == "C"  # not built: unknown

    def test_an_owner_that_does_not_load_gives_class_c_rows(self, monkeypatch):
        """THE loader gives no module, or the build raises: the rows stay, and
        each is class C. Mutation: drop the rows of an owner that fails, or
        give them class A or B → red."""
        pytest.importorskip("agent_framework", reason="MAF not installed")
        from gateway.studio import chat_tool_actions
        from orchestrator import _tool_injection as ti

        monkeypatch.setattr(ti, "_pack_owner_module", lambda owner: None)
        rows = chat_tool_actions.chat_tool_rows()
        assert [r["id"] for r in rows] == [f"tool:{n}" for n in _in_scope()]
        assert {r["class"] for r in rows} == {"C"}

        def broken(slug, owner, source):
            raise RuntimeError("down")

        monkeypatch.setattr(ti, "_pack_owner_module", lambda owner: object())
        monkeypatch.setattr(ti, "_owner_pack_tools", broken)
        assert chat_tool_actions._owner_tools({"crm-records": "crm-assistant"}) == {}

    def test_the_real_owners_give_each_class_and_never_below_a_route(self, real_rows):
        """The real tools, through THE loader. No Projects tool shows a class
        below the highest class of its routes in ``skill_projects.manifest``.
        An email read says ``read_only=False``, so it is B. Mutation: give
        the Projects tools ``manifest.tool_class`` (the lowest class) → red."""
        from skill_projects.manifest import MANIFEST

        rows = {r["id"].removeprefix("tool:"): r["class"] for r in real_rows}
        assert set(rows) == set(_in_scope())
        assert rows["search_crm"] == "A"
        assert rows["create_lead"] == "C"
        assert rows["send_email"] == "C"
        assert rows["compose_reply"] == "C"
        assert rows["read_email"] == "B"
        assert set(rows.values()) == {"A", "B", "C"}
        highest: dict[str, str] = {}
        for route in MANIFEST:
            if route.tool:
                old = highest.get(route.tool, "A")
                highest[route.tool] = max(old, route.cls, key=_RANK.__getitem__)
        checked = [n for n in rows if n in highest]
        assert len(checked) > 50
        for name in checked:
            assert _RANK[rows[name]] >= _RANK[highest[name]], (name, rows[name], highest[name])

    def test_a_tool_id_stays_outside_the_uses_grammar(self, loaded):
        """No package can name a ``tool:`` row, so the door never runs one,
        also when the catalog serves it. Mutation: add a ``tool:`` form to
        ``studio_manifest._USES_RE`` → red."""
        from acb_common.studio_manifest import _USES_RE, parse_uses
        from gateway.studio import chat_tool_actions

        rows = chat_tool_actions.chat_tool_rows()
        assert rows
        for row in rows:
            assert _USES_RE.fullmatch(row["id"]) is None
            assert parse_uses(row["id"]) is None
        served = {"tool:create_lead": "A"}
        assert actions._named_row(PKG_A, ("x", "tool:create_lead", None), served) is None

    def test_the_source_reads_no_pack_switch_and_keeps_no_second_map(self):
        """The load refusal of the org's pack switch stays in the run
        (``_skills_present``). Mutation: read the switch in the catalog, or
        set ``tool`` without ``action_tool`` → red."""
        import inspect

        from gateway.studio import chat_tool_actions

        src = inspect.getsource(chat_tool_actions)
        assert "packs_on" not in src and "tool_packs_orgs" not in src
        assert "route.tool" not in src
        assert src.count("action_tool(") == 1
