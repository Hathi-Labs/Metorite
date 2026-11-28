"""R8 — ``make_spreadsheet`` end to end against a real Postgres (DOC-6).

Spec: ``project-docs/specs/chat_parity.md`` §DOC row DOC-6. The tool calls the
real ``/crm`` and ``/projects`` routers, as the member that the run bound, on a
database with the whole ladder, two organizations and the NON-privileged role
of the phase-4 rehearsal. Only the HTTP wire is replaced, by an ASGI transport.

What it proves:

- a member who cannot see a record never gets it in the file: a member with no
  CRM access gets no file at all, a record of another organization is never in
  it, and a task of a project that the member cannot see is not in it;
- a custom (mirrored) module and Contacts export, grouped, with a total row;
- 2,000 records give one whole file, and more than 10,000 give none.

A hermetic fake agrees with any SQL it is handed, so these need the database.
"""

from __future__ import annotations

import io
import uuid
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

import httpx
from fastapi import Request
from openpyxl import load_workbook
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_crm_modules_routes_r8 import (
    _field_def,
    _module,
    _record,
    _seed_org,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

_TABLES = (
    "crm_contacts", "crm_companies", "crm_module_records", "crm_field_defs",
    "crm_source_profiles", "crm_settings", "integration_connections",
)


@pytest.fixture
def ns(promoted, monkeypatch, tmp_path):  # noqa: F811 — a fixture
    from tests.unit._crm_fakes import crm_switch_on

    crm_switch_on(monkeypatch, "*")
    promoted.users = []
    yield promoted
    with promoted.admin_engine.begin() as c:
        for org in (promoted.org_a, promoted.org_b):
            for table in _TABLES:
                c.execute(text(
                    f"DELETE FROM {table} WHERE organization_id = CAST(:o AS uuid)"),
                    {"o": org})
        for email in promoted.users:
            c.execute(text("DELETE FROM app_user WHERE lower(email) = :e"),
                      {"e": email.lower()})


def _person(email: str, org: str, *perms: str) -> Any:
    from acb_auth import UserContext, UserRole, build_access

    return UserContext(email=email, role=UserRole.EMPLOYEE,
                       access=build_access(list(perms)), organization_id=org)


class _Chat:
    """One chat of one member: the tool, its real routes and its files."""

    def __init__(self, ns: Any, tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        self.ns, self.root, self.patch = ns, tmp_path, monkeypatch
        self.members: dict[str, Any] = {}

    def member(self, org: str, *perms: str, tag: str = "m") -> Any:
        email = f"{tag}.{uuid.uuid4().hex[:8]}@doc6-r8.test"
        with self.ns.admin_engine.begin() as c:
            c.execute(text(
                "INSERT INTO app_user (email, display_name, role, status, organization_id) "
                "VALUES (:e, :e, 'employee', 'active', CAST(:o AS uuid))"),
                {"e": email, "o": org})
        self.ns.users.append(email)
        self.members[email] = _person(email, org, *perms)
        return self.members[email]

    def _app(self) -> Any:
        from acb_auth import get_current_user
        from acb_common.db import bind_tenant
        from fastapi import FastAPI
        from gateway.routes import crm, projects

        members = self.members

        async def _caller(request: Request) -> Any:
            # What `_with_resolved_access` does: the tool's header names the
            # member, and the tenant comes from the member.
            user = members[request.headers["X-User-Email"]]
            bind_tenant(user.organization_id)
            return user

        app = FastAPI()
        app.include_router(crm.router)
        app.include_router(projects.router)
        app.dependency_overrides[get_current_user] = _caller
        return app

    async def export(self, user: Any, spec: dict[str, Any]) -> tuple[dict, Any]:
        """The tool's answer, and the workbook that it wrote, or None."""
        import importlib

        from acb_common.db import clear_tenant, release_tenant
        from acb_skills import spreadsheets
        from acb_skills.memory_tools import _bind_memory_user_id, _unbind_memory_user_id

        wa = importlib.import_module("acb_skills.write_artifact")
        app = self._app()
        real = httpx.AsyncClient

        def _wire(*args: Any, **kwargs: Any) -> Any:
            return real(transport=httpx.ASGITransport(app=app), base_url="http://gw.test")

        self.patch.setattr(spreadsheets.httpx, "AsyncClient", _wire)

        async def _quiet(*_a: Any, **_k: Any) -> None:
            return None

        self.patch.setattr(wa, "_notify", _quiet)
        self.patch.setattr(wa, "mirror_to_blob_store", _quiet)
        folder = self.root / uuid.uuid4().hex
        folder.mkdir()
        identity = _bind_memory_user_id(user.email)
        scope = clear_tenant()
        try:
            async with tenant_engine_scope(
                    self.ns.app_url.render_as_string(hide_password=False)):
                with wa.artifact_context_scope():
                    wa.bind_artifact_context(workspace_root=str(folder), session_id="s",
                                             agent_name="orchestrator")
                    out = await spreadsheets.make_spreadsheet(spec)
        finally:
            release_tenant(scope)
            _unbind_memory_user_id(identity)
        files = list(folder.rglob("*.xlsx"))
        assert len(files) == (1 if out["ok"] else 0), out
        return out, load_workbook(io.BytesIO(files[0].read_bytes())) if files else None


@pytest.fixture
def chat(ns, tmp_path, monkeypatch) -> _Chat:
    return _Chat(ns, tmp_path, monkeypatch)


def _contacts(p: Any, org: str, city: str, names: list[str]) -> None:
    with p.admin_engine.begin() as c:
        company = c.execute(text(
            "INSERT INTO crm_companies (organization_id, name, address) VALUES "
            "(CAST(:o AS uuid), :n, CAST(:a AS jsonb)) RETURNING id"),
            {"o": org, "n": f"{city} Works", "a": f'{{"city": "{city}"}}'}).scalar()
        for name in names:
            c.execute(text(
                "INSERT INTO crm_contacts (organization_id, first_name, company_id) "
                "VALUES (CAST(:o AS uuid), :n, :c)"), {"o": org, "n": name, "c": company})


def _many_contacts(p: Any, org: str, count: int) -> None:
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO crm_contacts (organization_id, first_name) "
            "SELECT CAST(:o AS uuid), 'bulk ' || g FROM generate_series(1, :n) g"),
            {"o": org, "n": count})


def _crm(module: str, **shape: Any) -> dict[str, Any]:
    return {"title": "Customers",
            "sheets": [{"name": "Customers", "source": {"type": "crm", "module": module},
                        **shape}]}


def _column(book: Any, at: int = 0) -> list[Any]:
    return [row[at].value for row in list(book.worksheets[0].iter_rows())[1:]]


def _open_crm(p: Any, org: str, access: str = "everyone") -> None:
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO crm_settings (organization_id, mode, access) VALUES "
            "(CAST(:o AS uuid), 'native', :a) ON CONFLICT (organization_id) DO UPDATE "
            "SET access = EXCLUDED.access"), {"o": org, "a": access})


async def test_contacts_by_city_with_totals_hold_this_org_only(ns, chat) -> None:
    _open_crm(ns, ns.org_a)
    _open_crm(ns, ns.org_b)
    _contacts(ns, ns.org_a, "Pune", ["Asha", "Ravi"])
    _contacts(ns, ns.org_a, "Delhi", ["Meena"])
    _contacts(ns, ns.org_b, "Pune", ["Other Org Person"])
    user = chat.member(ns.org_a, "feature:crm")

    out, book = await chat.export(user, _crm(
        "contacts", columns=["name", "company_name", "company_address.city"],
        group_by="company_address.city", totals=True, sort=["name"]))

    assert out["ok"] is True, out
    assert out["rows"] == 3 and out["sheets"][0]["groups"] == 2
    assert out["path"].endswith("Customers.xlsx")
    names = _column(book)
    assert names == ["Meena", "Delhi: 1 records", "Asha", "Ravi", "Pune: 2 records",
                     "Total: 3 records"]
    assert "Other Org Person" not in names


async def test_a_member_without_crm_access_gets_no_file(ns, chat) -> None:
    _open_crm(ns, ns.org_a, "admins")
    _contacts(ns, ns.org_a, "Pune", ["Asha"])
    user = chat.member(ns.org_a, "feature:crm")
    out, book = await chat.export(user, _crm("contacts"))
    assert out["ok"] is False and "not open to you" in out["error"], out
    assert book is None


async def test_a_member_without_the_crm_feature_gets_no_file(ns, chat) -> None:
    _open_crm(ns, ns.org_a)
    _contacts(ns, ns.org_a, "Pune", ["Asha"])
    out, book = await chat.export(chat.member(ns.org_a), _crm("contacts"))
    assert out["ok"] is False and book is None, out


async def test_two_thousand_records_give_one_whole_file(ns, chat) -> None:
    _open_crm(ns, ns.org_a)
    _many_contacts(ns, ns.org_a, 2000)
    user = chat.member(ns.org_a, "feature:crm")
    out, book = await chat.export(user, _crm("contacts", columns=["name"]))
    assert out["rows"] == 2000, out
    assert len(set(_column(book))) == 2000


async def test_more_than_the_cap_gives_no_file(ns, chat) -> None:
    _open_crm(ns, ns.org_a)
    _many_contacts(ns, ns.org_a, 10_001)
    user = chat.member(ns.org_a, "feature:crm")
    out, book = await chat.export(user, _crm("contacts"))
    assert out["ok"] is False and "10001" in out["error"] and "10000" in out["error"], out
    assert book is None


async def test_a_mirrored_module_exports_every_page_grouped(ns, chat) -> None:
    _seed_org(ns, ns.org_a, mode="mirror",
              modules=[_module("Install_Base", custom=True, label="Installations")])
    _seed_org(ns, ns.org_b, mode="mirror",
              modules=[_module("Install_Base", custom=True)])
    _field_def(ns, ns.org_a, "Install_Base", "City", position=1)
    for i in range(205):
        _record(ns, ns.org_a, "Install_Base", f"a{i}", f"Pump {i:03d}",
                {"City": "Pune" if i % 2 else "Delhi"})
    _record(ns, ns.org_b, "Install_Base", "b1", "Other Org Pump", {"City": "Pune"})
    user = chat.member(ns.org_a, "feature:crm")

    out, book = await chat.export(user, _crm(
        "Install_Base", columns=["name", "City"], group_by="City", totals=True))

    assert out["ok"] is True, out
    assert out["rows"] == 205
    names = _column(book)
    assert "Other Org Pump" not in names
    assert "Total: 205 records" in names and len(names) == 205 + 2 + 1


async def test_a_module_filter_on_no_field_is_refused_and_not_dropped(ns, chat) -> None:
    _seed_org(ns, ns.org_a, mode="mirror",
              modules=[_module("Install_Base", custom=True)])
    _field_def(ns, ns.org_a, "Install_Base", "City")
    _record(ns, ns.org_a, "Install_Base", "a1", "Pump", {"City": "Pune"})
    user = chat.member(ns.org_a, "feature:crm")
    spec = _crm("Install_Base")
    spec["sheets"][0]["source"]["custom"] = {"Cty": "Pune"}
    out, book = await chat.export(user, spec)
    assert out["ok"] is False and "no field for the filter" in out["error"], out
    assert book is None


def _board(p: Any, org: str, owner: str, titles: list[str]) -> str:
    """A project that only *owner* holds a grant on, with its tasks."""
    project, status = str(uuid.uuid4()), str(uuid.uuid4())
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO pm_projects (id, organization_id, name, created_by, owns_statuses) "
            "VALUES (:id, :o, 'private', :e, true)"), {"id": project, "o": org, "e": owner})
        c.execute(text(
            "INSERT INTO pm_project_grants (organization_id, project_id, subject, "
            "created_by) VALUES (:o, :p, :e, :e)"), {"o": org, "p": project, "e": owner})
        c.execute(text(
            "INSERT INTO pm_task_statuses (id, organization_id, project_id, name, category, "
            "position) VALUES (:id, :o, :p, 'To do', 'todo', 10)"),
            {"id": status, "o": org, "p": project})
        for number, title in enumerate(titles, start=1):
            c.execute(text(
                "INSERT INTO pm_tasks (id, organization_id, project_id, root_project_id, "
                "task_number, status_id, title, created_by) VALUES "
                "(gen_random_uuid(), :o, :p, :p, :n, :s, :t, :e)"),
                {"o": org, "p": project, "n": number, "s": status, "t": title, "e": owner})
    return project


async def test_a_task_of_a_project_the_member_cannot_see_is_not_in_the_file(ns, chat) -> None:
    mine = chat.member(ns.org_a, "feature:projects", tag="mine")
    other = chat.member(ns.org_a, "feature:projects", tag="other")
    _board(ns, ns.org_a, mine.email, ["=Mine to see"])
    hidden = _board(ns, ns.org_a, other.email, ["Secret of another"])
    spec = {"sheets": [{"source": {"type": "tasks"}, "columns": ["Title", "Status"],
                        "totals": True}]}

    out, book = await chat.export(mine, spec)
    assert out["ok"] is True, out
    assert _column(book) == ["=Mine to see", "Total: 1 records"]
    cell = book.worksheets[0]["A2"]
    assert cell.data_type == "s" and cell.quotePrefix

    spec["sheets"][0]["source"]["project_id"] = hidden
    out, book = await chat.export(mine, spec)
    assert out["ok"] is False and book is None, out


async def test_a_custom_field_named_like_a_column_keeps_both(ns, chat) -> None:
    mine = chat.member(ns.org_a, "feature:projects", tag="mine")
    project = _board(ns, ns.org_a, mine.email, ["One"])
    with ns.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO pm_custom_fields (organization_id, project_id, name, field_key, "
            "field_type, created_by) VALUES (:o, :p, 'Status', 'my_status', 'text', :e)"),
            {"o": ns.org_a, "p": project, "e": mine.email})
        c.execute(text(
            "UPDATE pm_tasks SET custom_fields = '{\"my_status\": \"blue\"}'::jsonb "
            "WHERE project_id = CAST(:p AS uuid)"), {"p": project})
    out, book = await chat.export(mine, {"sheets": [{
        "source": {"type": "tasks", "project_id": project},
        "columns": ["status", "custom.my_status"]}]})
    assert out["ok"] is True, out
    assert [c.value for c in list(book.worksheets[0].iter_rows())[1]] == ["To do", "blue"]
