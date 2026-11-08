"""WS-ALN ALN-4 — a lead pins a published Knowledge page to a project (R8).

Spec: ``project-docs/specs/project_alignment.md`` §4.1, §7.2 and the §10 row
ALN-4. Code: ``gateway/routes/projects/authorities.py`` (the one writer) and
the three helpers in ``gateway/routes/knowledge/core.py``. Migration: the
file that creates ``pm_project_authorities`` (329 at build, 339 at merge).

Two halves.

**Database-free (always runs).** The leak check refuses on any error and on
an empty audience. The fast path needs no per-person read. The stale rule is
pure. The module has no copy of the Knowledge visibility SQL, and no other
module writes the table.

**R8 (a real database, as a NON-privileged role, on a promoted catalog of
its own).** The real gateway app. Members: a lead, the lead of an ancestor,
a Projects admin, a viewer, an outsider, a holder of ``data:org:read`` and a
Knowledge manager. Organization B has a member, a project, a page and a pin.

Run, on a PRIVATE database (one R8 suite at a time)::

    export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/<private db>
    uv run pytest tests/unit/test_project_authorities_r8.py -v -rs

Without the database the R8 half SKIPS, and the run reads green. A skip is
not a pass.

**The mutations (R7).** Commit before each, restore with ``git checkout``.
``G`` is ``apps/services/gateway/gateway/routes/projects``.

* M1. ``G/authorities.py`` ``pin_authority``: no ``assert_may_write_grant``.
  ``test_a_member_with_no_authority_gets_403_and_an_outsider_404`` goes red.
* M2. ``G/authorities.py`` ``pin_authority``: no ``in_personal_tree`` refusal.
  ``test_a_project_in_a_personal_tree_is_refused`` goes red.
* M3. ``G/authorities.py`` ``pin_authority``: no ``published`` check.
  ``test_a_draft_an_archived_page_and_a_me_page_are_refused`` goes red.
* M4. ``G/authorities.py`` ``pin_authority``: no ``_refuse_if_narrower``.
  ``test_a_page_narrower_than_the_project_is_refused_and_a_wide_one_is_not``
  goes red.
* M5. The migration: drop the trigger ``pm_project_authorities_same_org``.
  ``test_the_database_refuses_a_hand_written_cross_org_pin`` goes red.
* M8. ``G/authorities.py``: drop ``dependencies=_ENABLED`` from the routes.
  ``test_with_the_flag_off_all_three_routes_answer_404_and_write_nothing`` goes red.
* M7. ``G/authorities.py`` ``_view``: drop the ``page_hidden`` mask of ``stale_reason``.
  ``test_a_hidden_page_shows_no_reason_and_no_version_but_keeps_the_state`` goes red.
* M6. ``G/authorities.py`` ``_stale_reason``: always ``None``.
  ``test_edit_and_archive_make_a_pin_stale_and_a_restore_makes_it_active``
  goes red.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from tests.unit import test_mcp_edge_delete as d
from tests.unit import test_mcp_edge_task_writes as tw
from tests.unit import test_mcp_edge_writes as w
from tests.unit._tenant_ladder import apply_ladder

_DB_GATE = w._DB_GATE

_ROOT = Path(__file__).resolve().parents[2]
_GATEWAY = _ROOT / "apps" / "services" / "gateway" / "gateway"
_MIGRATION = next(
    p for p in (_ROOT / "infra" / "postgres").glob("*.sql")
    if "CREATE TABLE IF NOT EXISTS pm_project_authorities (" in p.read_text(encoding="utf-8"))
_AUTHORITIES = _GATEWAY / "routes" / "projects" / "authorities.py"

#: The members. The domain is the one ``live_app`` allows.
LEAD = "lead@mcpwrites.test"
ANC = "anc@mcpwrites.test"
ADMIN = "admin@mcpwrites.test"
VIEWER = "viewer@mcpwrites.test"
OUTSIDER = "outsider@mcpwrites.test"
ORGREAD = "orgread@mcpwrites.test"
KBA = "kba@mcpwrites.test"
BOB = "bob@mcpwrites.test"
ALL_A = (LEAD, ANC, ADMIN, VIEWER, OUTSIDER, ORGREAD, KBA)

_APP_ROLE = "acb_app_aln4pin"
_APP_PW = "aln4pin_scratch_pw"

#: A title that no refusal text may carry.
SECRET_TITLE = "Quarterly layoff plan"


@pytest.fixture(autouse=True)
def _knowledge_on(monkeypatch):
    from acb_common import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "knowledge_enabled", True, raising=False)
    monkeypatch.setattr(settings, "knowledge_orgs", "*", raising=False)
    monkeypatch.setattr(settings, "authority_pins_orgs", "*", raising=False)


def _authorities_module():
    from gateway.routes.projects import authorities

    return authorities


def _kb():
    from gateway.routes.knowledge import core

    return core


# ═══════════════════════════════════════════════════════════════════════════
# Database-free
# ═══════════════════════════════════════════════════════════════════════════


class _Cm:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Result:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


class _FakeDb:
    """A db that answers every read the same way, or raises."""

    def __init__(self, *, row=None, boom=False):
        self.row, self.boom, self.calls = row, boom, 0

    def begin_nested(self):
        return _Cm()

    async def execute(self, *_a, **_k):
        self.calls += 1
        if self.boom:
            raise RuntimeError("the database went away")
        return _Result(self.row)


async def test_the_leak_check_refuses_on_any_error() -> None:
    kb = _kb()
    db = _FakeDb(boom=True)
    assert await kb.audience_can_read_page(db, str(uuid.uuid4()), [LEAD], str(uuid.uuid4())) is False


async def test_the_leak_check_refuses_an_empty_audience_it_cannot_prove() -> None:
    kb = _kb()
    db = _FakeDb(row=None)
    assert await kb.audience_can_read_page(db, str(uuid.uuid4()), [], str(uuid.uuid4())) is False
    assert await kb.audience_can_read_page(db, str(uuid.uuid4()), [LEAD], str(uuid.uuid4())) is False


async def test_the_leak_check_takes_the_org_wide_fast_path_with_one_read() -> None:
    kb = _kb()
    db = _FakeDb(row=(1,))
    assert await kb.audience_can_read_page(
        db, str(uuid.uuid4()), [LEAD, VIEWER, ORGREAD], str(uuid.uuid4())) is True
    assert db.calls == 1


async def test_the_pin_refuses_when_the_holder_read_fails(monkeypatch) -> None:
    from fastapi import HTTPException

    a = _authorities_module()

    async def boom(*_a, **_k):
        raise RuntimeError("no")

    monkeypatch.setattr(a, "project_grant_holders", boom)
    project = SimpleNamespace(id=uuid.uuid4(), organization_id=str(uuid.uuid4()))
    user = SimpleNamespace(organization_id=str(uuid.uuid4()))
    with pytest.raises(HTTPException) as caught:
        await a._refuse_if_narrower(_FakeDb(row=(1,)), user, project, str(uuid.uuid4()))
    assert caught.value.status_code == 409
    assert caught.value.detail == a.NARROWER_REFUSED


def test_the_refusal_sentences_name_no_page() -> None:
    a = _authorities_module()
    for sentence in (a.NARROWER_REFUSED, a.NOT_PUBLISHED_REFUSED, a.PERSONAL_PIN_REFUSED):
        assert "{" not in sentence and "%" not in sentence


def test_the_stale_rule_is_pure() -> None:
    a = _authorities_module()
    ok = {"exists": True, "state": "published", "changed": False}
    assert a._stale_reason(None, ok) is None
    assert a._stale_reason(None, {**ok, "changed": True}) == "changed"
    assert a._stale_reason(None, {**ok, "state": "archived"}) == "not_published"
    assert a._stale_reason(None, {**ok, "state": "draft"}) == "not_published"
    assert a._stale_reason(None, {"exists": False, "state": None, "changed": True}) == "page_gone"


def test_the_pin_module_holds_no_copy_of_the_knowledge_visibility_sql() -> None:
    src = _AUTHORITIES.read_text(encoding="utf-8")
    for needle in ("FROM kb_", "JOIN kb_", "kb_space_grants", "_subject_match",
                   "visible_pages_clause"):
        assert needle not in src, needle


def test_only_the_pin_module_writes_the_table() -> None:
    write = re.compile(
        r"\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+pm_project_authorities\b", re.IGNORECASE)
    found = []
    for base in (_ROOT / "apps", _ROOT / "packages", _ROOT / "scripts"):
        for path in base.rglob("*.py"):
            if "node_modules" in path.parts or ".venv" in path.parts:
                continue
            if write.search(path.read_text(encoding="utf-8", errors="replace")):
                found.append(path.relative_to(_ROOT).as_posix())
    assert found == [
        "apps/services/gateway/gateway/routes/projects/authorities.py"], found


def test_the_migration_holds_the_checks_the_rls_and_the_trigger() -> None:
    sql = _MIGRATION.read_text(encoding="utf-8")
    assert "ENABLE ROW LEVEL SECURITY" in sql and "FORCE  ROW LEVEL SECURITY" in sql
    assert "REFERENCES organization (id) ON DELETE CASCADE" in sql
    assert "UNIQUE (organization_id, id)" in sql
    assert "WHERE state <> 'removed'" in sql
    assert "pm_project_authorities_same_org" in sql
    assert re.search(r"state\s+IN\s+\('active',\s*'stale',\s*'removed'\)", sql)
    assert "(state = 'removed') = (removed_at IS NOT NULL)" in sql
    # Expand only (R6).
    for banned in ("DROP TABLE", "DROP COLUMN", "RENAME", "ALTER TABLE pm_projects",
                   "ALTER TABLE kb_pages"):
        assert banned not in sql, banned


# ═══════════════════════════════════════════════════════════════════════════
# R8
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def world():
    """A private promoted catalog: seven members in org A, one in org B."""
    if not w._URL:
        pytest.skip("TENANT_LADDER_DATABASE_URL unset")
    admin_url = make_url(w._URL)
    dedicated = f"{admin_url.database}_aln4pin"
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

    org_a, org_b = str(uuid.uuid4()), str(uuid.uuid4())
    with eng.begin() as c:
        for oid, slug in ((org_a, "aln4a"), (org_b, "aln4b")):
            c.execute(text("INSERT INTO organization (id, slug, display_name) "
                           "VALUES (:id, :s, :s)"), {"id": oid, "s": slug})
    a = w._Seeder(eng, org_a)
    a.number = 900_000
    for who in (LEAD, ANC, VIEWER, OUTSIDER):
        a.member(who, ["feature:projects", "feature:knowledge"])
    a.member(ADMIN, ["feature:projects", "feature:knowledge", "projects:settings:write"])
    a.member(ORGREAD, ["feature:projects", "feature:knowledge", "data:org:read"])
    a.member(KBA, ["feature:projects", "feature:knowledge", "knowledge:manage"])
    b = w._Seeder(eng, org_b)
    b.number = 910_000
    b.member(BOB, ["feature:projects", "feature:knowledge", "knowledge:manage"])

    world_ = SimpleNamespace(
        admin=eng, org=org_a, org_b=org_b, seeder=a, seeder_b=b, c={}, tok={},
        app_dsn=admin_url.set(
            database=dedicated, username=_APP_ROLE, password=_APP_PW,
        ).render_as_string(hide_password=False))
    # The warm-up of ``tw.live`` signs one read as the "write" connection.
    world_.c["write"], world_.tok["write"] = d._connection_of(a, LEAD)
    # The projects. PARENT grants the org and TEAM inherits it. SMALL grants
    # two people. HIDDEN grants its lead only. PERSONAL is a personal tree.
    world_.parent = _project(world_, "Parent", lead=ANC, grants=("org",))
    world_.team = _project(world_, "Team", lead=LEAD, parent=world_.parent)
    world_.small = _project(world_, "Small", lead=LEAD, grants=(LEAD, VIEWER))
    world_.hidden = _project(world_, "Hidden", lead=LEAD, grants=(LEAD,))
    world_.personal = _project(world_, "Mine", lead=LEAD, grants=(LEAD,), personal_owner=LEAD)
    world_.space_org = _space(world_, "Everyone", [("org", "read")])
    world_.project_b = _project(world_, "Bob project", lead=BOB, grants=(BOB,), seeder=b)
    world_.space_b = _space(world_, "Bob space", [("org", "read")], seeder=b)
    world_.page_b = _page(world_, world_.space_b, "Bob page", seeder=b)
    world_.pin_b = b.one(
        "INSERT INTO pm_project_authorities (project_id, page_id, page_version, pinned_by)"
        " VALUES (CAST(:p AS uuid), CAST(:g AS uuid), 1, :who) RETURNING id",
        p=world_.project_b, g=world_.page_b, who=BOB)
    yield world_

    eng.dispose()
    w._drop_database(admin_url, dedicated)


redis_fake = tw.redis_fake
live_app = tw.live_app
live = tw.live


def _project(world, name: str, *, lead: str | None = None, grants: tuple[str, ...] = (),
             parent: str | None = None, personal_owner: str | None = None,
             seeder: Any = None) -> str:
    s = seeder or world.seeder
    pid = str(s.one(
        "INSERT INTO pm_projects (name, status, source, created_by, organization_id,"
        " timezone, parent_project_id, lead, personal_owner, owns_statuses) VALUES"
        " (:n, 'active', 'manual', :m, CAST(:o AS uuid), 'Asia/Kolkata',"
        " CAST(:p AS uuid), :l, :po, CAST(:own AS boolean))"
        " RETURNING id",
        n=name, m=lead or w.OWNER, o=s.org, p=parent, l=lead, po=personal_owner,
        own=parent is None))
    for subject in grants:
        s.one("INSERT INTO pm_project_grants (project_id, subject, created_by,"
              " organization_id) VALUES (CAST(:p AS uuid), :s, :a, CAST(:o AS uuid))"
              " RETURNING project_id", p=pid, s=subject, a=w.OWNER, o=s.org)
    return pid


def _space(world, name: str, grants: list[tuple[str, str]], *, kind: str = "team",
           owner: str | None = None, seeder: Any = None) -> str:
    s = seeder or world.seeder
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") + "-" + uuid.uuid4().hex[:6]
    sid = str(s.one(
        "INSERT INTO kb_spaces (slug, name, kind, owner_email, organization_id) VALUES"
        " (:s, :n, :k, :o, CAST(:org AS uuid)) RETURNING id",
        s=slug if kind != "me" else "me", n=name, k=kind, o=owner, org=s.org))
    for subject, level in grants:
        s.one("INSERT INTO kb_space_grants (space_id, subject, level, created_by,"
              " organization_id) VALUES (CAST(:sp AS uuid), :su, :l, :a, CAST(:o AS uuid))"
              " RETURNING space_id", sp=sid, su=subject, l=level, a=w.OWNER, o=s.org)
    return sid


def _page(world, space: str, title: str, *, state: str = "published", versions: int = 2,
          author: str = KBA, body: str = "The rules of the project.",
          seeder: Any = None) -> str:
    s = seeder or world.seeder
    pid = str(s.one(
        "INSERT INTO kb_pages (space_id, title, body_md, state, author_email, published_at,"
        " archived_from_state, archived_at, organization_id) VALUES (CAST(:sp AS uuid), :t,"
        " :b, CAST(:st AS text), :au, CASE WHEN CAST(:st AS text) = 'published' THEN now() END,"
        " CASE WHEN CAST(:st AS text) = 'archived' THEN 'published' END,"
        " CASE WHEN CAST(:st AS text) = 'archived' THEN now() END, CAST(:o AS uuid)) RETURNING id",
        sp=space, t=title, b=body, st=state, au=author, o=s.org))
    for n in range(1, versions + 1):
        s.one("INSERT INTO kb_page_versions (page_id, version, body_md, title, author_email,"
              " reason, organization_id) VALUES (CAST(:p AS uuid), :v, :b, :t, :a,"
              " 'create', CAST(:o AS uuid)) RETURNING version",
              p=pid, v=n, b=body, t=title, a=author, o=s.org)
    return pid


def _group(world, slug: str, members: list[str]) -> None:
    s = world.seeder
    gid = str(s.one("INSERT INTO org_group (organization_id, slug, display_name)"
                    " VALUES (CAST(:o AS uuid), :s, :s) RETURNING id", o=s.org, s=slug))
    for who in members:
        uid = s.one("SELECT id FROM app_user WHERE lower(email) = :e", e=who)
        s.one("INSERT INTO org_group_member (group_id, user_id, organization_id)"
              " VALUES (CAST(:g AS uuid), CAST(:u AS uuid), CAST(:o AS uuid))"
              " RETURNING group_id", g=gid, u=str(uid), o=s.org)


def _rows(world, sql: str, **params: Any) -> list[Any]:
    with world.admin.begin() as c:
        c.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": world.org})
        return list(c.execute(text(sql), params))


async def _as(world, who: str, method: str, path: str,
              body: dict[str, Any] | None = None) -> httpx.Response:
    return await d._person(world, who, method, path, body)


def _path(project: str) -> str:
    return f"/projects/nodes/{project}/authorities"


async def _pin(world, who: str, project: str, page: str, **extra: Any) -> httpx.Response:
    return await _as(world, who, "POST", _path(project), {"page_id": page, **extra})


async def _list(world, who: str, project: str, **query: str) -> list[dict]:
    qs = "&".join(f"{k}={v}" for k, v in query.items())
    r = await _as(world, who, "GET", _path(project) + (f"?{qs}" if qs else ""))
    assert r.status_code == 200, r.text
    return r.json()["rows"]


def _item(rows: list[dict], pin_id: str) -> dict:
    return next(r for r in rows if r["id"] == pin_id)


class TestThePin:
    pytestmark = _DB_GATE

    async def test_a_lead_pins_a_published_page_and_the_actor_is_the_session(self, live):
        page = _page(live, live.space_org, "Rules one", versions=3)
        r = await _pin(live, LEAD, live.team, page)
        assert r.status_code == 201, r.text
        got = r.json()
        assert got["pinned_by"] == LEAD and got["state"] == "active"
        assert got["page_version"] == 3, "the highest kb_page_versions.version"
        assert got["title"] == "Rules one" and got["page_hidden"] is False
        row = _rows(live, "SELECT organization_id, pinned_by, page_version"
                          " FROM pm_project_authorities WHERE id = CAST(:i AS uuid)",
                    i=got["id"])[0]
        assert str(row.organization_id) == live.org

    async def test_a_lead_of_an_ancestor_and_a_projects_admin_pin(self, live):
        p1 = _page(live, live.space_org, "Rules anc")
        p2 = _page(live, live.space_org, "Rules admin")
        assert (await _pin(live, ANC, live.team, p1)).status_code == 201
        assert (await _pin(live, ADMIN, live.team, p2)).status_code == 201

    async def test_a_member_with_no_authority_gets_403_and_an_outsider_404(self, live):
        page = _page(live, live.space_org, "Rules authority")
        r = await _pin(live, VIEWER, live.team, page)
        assert r.status_code == 403, r.text
        r = await _pin(live, VIEWER, live.hidden, page)
        assert r.status_code == 404, r.text
        r = await _pin(live, OUTSIDER, live.hidden, page)
        assert r.status_code == 404, r.text
        assert _rows(live, "SELECT 1 FROM pm_project_authorities WHERE page_id ="
                           " CAST(:g AS uuid)", g=page) == []

    async def test_org_b_reads_none_of_org_a_and_the_app_role_obeys_rls(self, live):
        page = _page(live, live.space_org, "Rules rls")
        pin = (await _pin(live, LEAD, live.team, page)).json()["id"]
        # The route: org B's member cannot see org A's project at all.
        r = await _as(live, BOB, "GET", _path(live.team))
        assert r.status_code == 404, r.text
        r = await _as(live, BOB, "DELETE", f"{_path(live.team)}/{pin}")
        assert r.status_code == 404, r.text
        # The database, as the non-privileged role, bound to org B and to nobody.
        eng = create_engine(live.app_dsn)
        try:
            with eng.begin() as c:
                bypass = c.execute(text(
                    "SELECT rolbypassrls, rolsuper FROM pg_roles"
                    " WHERE rolname = current_user")).first()
                assert not bypass.rolbypassrls and not bypass.rolsuper
                flags = c.execute(text(
                    "SELECT relrowsecurity, relforcerowsecurity FROM pg_class"
                    " WHERE relname = 'pm_project_authorities'")).first()
                assert tuple(flags) == (True, True)
                c.execute(text("SELECT set_config('app.tenant_id', :t, true)"),
                          {"t": live.org_b})
                mine = c.execute(text("SELECT id FROM pm_project_authorities")).fetchall()
                assert {str(m.id) for m in mine} == {str(live.pin_b)}
                direct = c.execute(text(
                    "SELECT 1 FROM pm_project_authorities WHERE id = CAST(:i AS uuid)"),
                    {"i": pin}).first()
                assert direct is None, "org B read a pin of org A by id"
        finally:
            eng.dispose()
        fresh = create_engine(live.app_dsn)
        try:
            with fresh.begin() as c:
                none = c.execute(text("SELECT count(*) FROM pm_project_authorities")).scalar()
                assert none == 0, "an unbound session reads nothing"
        finally:
            fresh.dispose()

    async def test_a_page_of_another_org_is_refused_with_the_same_404(self, live):
        r = await _pin(live, LEAD, live.team, live.page_b)
        foreign = r.status_code, r.json()
        hidden = _page(live, _space(live, "Closed", [(KBA, "manage")]), "Closed page")
        r2 = await _pin(live, LEAD, live.team, hidden)
        assert foreign[0] == 404 and r2.status_code == 404
        assert foreign[1] == r2.json(), "a foreign page and a hidden page read the same"
        r3 = await _pin(live, LEAD, live.team, str(uuid.uuid4()))
        assert r3.status_code == 404 and r3.json() == foreign[1]
        r4 = await _pin(live, LEAD, live.team, "not-a-uuid")
        assert r4.status_code == 404

    async def test_the_database_refuses_a_hand_written_cross_org_pin(self, live):
        page_a = _page(live, live.space_org, "Rules fk")
        sql = ("INSERT INTO pm_project_authorities"
               " (organization_id, project_id, page_id, page_version, pinned_by)"
               " VALUES (CAST(:o AS uuid), CAST(:p AS uuid), CAST(:g AS uuid), 1, 'x@y.z')")
        cases = [
            ("page of org B in org A", live.org, live.team, live.page_b),
            ("project of org B in org A", live.org, live.project_b, page_a),
            ("org B row on org A project and page", live.org_b, live.team, page_a),
        ]
        # The owner role first: the trigger holds for each role.
        for _label, org, project, page in cases:
            with pytest.raises(DBAPIError), live.admin.begin() as c:
                c.execute(text("SELECT set_config('app.tenant_id', :t, true)"),
                          {"t": org})
                c.execute(text(sql), {"o": org, "p": project, "g": page})
        eng = create_engine(live.app_dsn)
        try:
            for _label, org, project, page in cases:
                with pytest.raises(DBAPIError), eng.begin() as c:
                    c.execute(text("SELECT set_config('app.tenant_id', :t, true)"),
                              {"t": org})
                    c.execute(text(sql), {"o": org, "p": project, "g": page})
        finally:
            eng.dispose()
        # The same insert with rows of one org goes through.
        with live.admin.begin() as c:
            c.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": live.org})
            c.execute(text(sql), {"o": live.org, "p": live.team, "g": page_a})

    async def test_a_draft_an_archived_page_and_a_me_page_are_refused(self, live):
        draft = _page(live, live.space_org, "Draft", state="draft", author=LEAD)
        r = await _pin(live, LEAD, live.team, draft)
        assert r.status_code == 409, r.text
        assert r.json()["detail"] == _authorities_module().NOT_PUBLISHED_REFUSED
        # LEAD manages this space, so LEAD reads its archived pages.
        managed = _space(live, "Managed", [("org", "read"), (LEAD, "manage")])
        archived = _page(live, managed, "Archived", state="archived")
        r = await _pin(live, LEAD, live.team, archived)
        assert r.status_code == 409, r.text
        assert r.json()["detail"] == _authorities_module().NOT_PUBLISHED_REFUSED
        mine = _space(live, "My notes", [], kind="me", owner=LEAD)
        note = _page(live, mine, "My note", author=LEAD)
        r = await _pin(live, LEAD, live.team, note)
        assert r.status_code == 409, r.text
        assert _rows(live, "SELECT 1 FROM pm_project_authorities WHERE page_id = ANY("
                           "CAST(:ids AS uuid[]))", ids=[draft, archived, note]) == []

    async def test_a_page_narrower_than_the_project_is_refused_and_a_wide_one_is_not(self, live):
        _group(live, "aln4eng", [LEAD, KBA])
        nar = _authorities_module()
        # Project granted to org, space granted to one group: refused.
        group_space = _space(live, "Eng only", [("group:aln4eng", "read")])
        page = _page(live, group_space, SECRET_TITLE)
        r = await _pin(live, LEAD, live.team, page)
        assert r.status_code == 409, r.text
        assert r.json()["detail"] == nar.NARROWER_REFUSED
        assert SECRET_TITLE not in r.text, "the refusal names no page title"
        # A space with an org grant: accepted.
        wide = _page(live, live.space_org, "Wide page")
        assert (await _pin(live, LEAD, live.team, wide)).status_code == 201
        # SMALL has three holders: LEAD, VIEWER and ORGREAD (data:org:read).
        two = _space(live, "Two people", [(LEAD, "read"), (VIEWER, "read")])
        p_two = _page(live, two, "Two page")
        r = await _pin(live, LEAD, live.small, p_two)
        assert r.status_code == 409, "a data:org:read holder cannot read the page"
        three = _space(live, "Three people", [(LEAD, "read"), (VIEWER, "read"), (ORGREAD, "read")])
        p_three = _page(live, three, "Three page")
        assert (await _pin(live, LEAD, live.small, p_three)).status_code == 201
        # A space whose grants name a group that holds every holder.
        _group(live, "aln4small", [LEAD, VIEWER, ORGREAD])
        grouped = _space(live, "Grouped", [("group:aln4small", "read")])
        p_group = _page(live, grouped, "Grouped page")
        assert (await _pin(live, LEAD, live.small, p_group)).status_code == 201

    async def test_a_project_in_a_personal_tree_is_refused(self, live):
        page = _page(live, live.space_org, "Rules personal")
        r = await _pin(live, LEAD, live.personal, page)
        assert r.status_code == 403, r.text
        assert r.json()["detail"] == _authorities_module().PERSONAL_PIN_REFUSED
        assert _rows(live, "SELECT 1 FROM pm_project_authorities WHERE project_id ="
                           " CAST(:p AS uuid)", p=live.personal) == []

    async def test_the_body_cannot_name_another_actor_or_org(self, live):
        page = _page(live, live.space_org, "Rules body")
        r = await _pin(live, LEAD, live.team, page, pinned_by=VIEWER,
                       organization_id=live.org_b, state="removed", page_version=99,
                       removed_by=VIEWER)
        assert r.status_code == 201, r.text
        got = r.json()
        assert got["pinned_by"] == LEAD and got["state"] == "active"
        assert got["page_version"] == 2
        row = _rows(live, "SELECT organization_id, pinned_by, removed_by"
                          " FROM pm_project_authorities WHERE id = CAST(:i AS uuid)",
                    i=got["id"])[0]
        assert str(row.organization_id) == live.org
        assert row.pinned_by == LEAD and row.removed_by is None

    async def test_a_second_pin_of_the_same_page_is_the_same_pin(self, live):
        page = _page(live, live.space_org, "Rules twice")
        first = await _pin(live, LEAD, live.team, page)
        again = await _pin(live, LEAD, live.team, page)
        assert first.status_code == 201 and again.status_code == 200, again.text
        assert again.json()["id"] == first.json()["id"]
        rows = _rows(live, "SELECT 1 FROM pm_project_authorities WHERE page_id ="
                           " CAST(:g AS uuid) AND state <> 'removed'", g=page)
        assert len(rows) == 1

    async def test_two_pins_at_once_make_one_active_row(self, live):
        from gateway.main import app as gateway_app

        page = _page(live, live.space_org, "Rules race")
        raw = json.dumps({"page_id": page}).encode()
        headers = {"Authorization": f"Bearer {tw.INTERNAL}", "X-User-Email": LEAD,
                   "Content-Type": "application/json"}

        async def go():
            async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=gateway_app),
                    base_url="http://gateway.test") as client:
                return await asyncio.gather(*[
                    client.request("POST", _path(live.team), headers=headers, content=raw)
                    for _ in range(4)])

        results = await tw._in_scope(live, go)
        assert sorted(r.status_code for r in results) == [200, 200, 200, 201], [
            (r.status_code, r.text) for r in results]
        rows = _rows(live, "SELECT 1 FROM pm_project_authorities WHERE page_id ="
                           " CAST(:g AS uuid) AND state <> 'removed'", g=page)
        assert len(rows) == 1


class TestTheRemoval:
    pytestmark = _DB_GATE

    async def test_a_removal_keeps_the_row_and_the_page_can_be_pinned_again(self, live):
        page = _page(live, live.space_org, "Rules remove")
        pin = (await _pin(live, LEAD, live.team, page)).json()["id"]
        r = await _as(live, VIEWER, "DELETE", f"{_path(live.team)}/{pin}")
        assert r.status_code == 403, r.text
        r = await _as(live, LEAD, "DELETE", f"{_path(live.team)}/{pin}")
        assert r.status_code == 200, r.text
        assert r.json()["state"] == "removed"
        row = _rows(live, "SELECT state, removed_by, removed_at FROM pm_project_authorities"
                          " WHERE id = CAST(:i AS uuid)", i=pin)[0]
        assert row.state == "removed" and row.removed_by == LEAD and row.removed_at is not None
        assert pin not in {x["id"] for x in await _list(live, LEAD, live.team)}
        shown = await _list(live, LEAD, live.team, include_removed="true")
        assert _item(shown, pin)["state"] == "removed"
        # Removing twice is the same removal. An unknown pin is 404.
        r = await _as(live, LEAD, "DELETE", f"{_path(live.team)}/{pin}")
        assert r.status_code == 200 and r.json()["state"] == "removed"
        r = await _as(live, LEAD, "DELETE", f"{_path(live.team)}/{uuid.uuid4()}")
        assert r.status_code == 404
        r = await _as(live, LEAD, "DELETE", f"{_path(live.team)}/nope")
        assert r.status_code == 404
        again = await _pin(live, LEAD, live.team, page)
        assert again.status_code == 201 and again.json()["id"] != pin
        assert len(_rows(live, "SELECT 1 FROM pm_project_authorities WHERE page_id ="
                               " CAST(:g AS uuid)", g=page)) == 2

    async def test_a_pin_of_another_project_does_not_remove_through_this_one(self, live):
        page = _page(live, live.space_org, "Rules cross")
        pin = (await _pin(live, LEAD, live.team, page)).json()["id"]
        # ANC leads PARENT, so ANC may write on Team too. LEAD does not lead SMALL's tree
        # beyond Small. The pin of Team is not found through Small.
        r = await _as(live, LEAD, "DELETE", f"{_path(live.small)}/{pin}")
        assert r.status_code == 404, r.text
        assert _rows(live, "SELECT state FROM pm_project_authorities WHERE id ="
                           " CAST(:i AS uuid)", i=pin)[0].state == "active"


class TestTheRead:
    pytestmark = _DB_GATE

    async def test_edit_and_archive_make_a_pin_stale_and_a_restore_makes_it_active(self, live):
        # Archive, then restore: the words do not change.
        page = _page(live, live.space_org, "Rules archive", versions=1)
        pin = (await _pin(live, LEAD, live.team, page)).json()["id"]
        assert _item(await _list(live, LEAD, live.team), pin)["state"] == "active"
        r = await _as(live, KBA, "POST", f"/knowledge/pages/{page}/archive")
        assert r.status_code == 200, r.text
        got = _item(await _list(live, LEAD, live.team), pin)
        # LEAD cannot read an archived page. The pin shows `stale`, and says
        # no reason and no version, so LEAD learns nothing about the page.
        assert got["state"] == "stale" and got["stale_reason"] is None
        assert got["page_hidden"] is True and "title" not in got
        assert got["page_version"] is None
        r = await _as(live, KBA, "POST", f"/knowledge/pages/{page}/restore")
        assert r.status_code == 200, r.text
        got = _item(await _list(live, LEAD, live.team), pin)
        assert got["state"] == "active" and got["stale_reason"] is None, got
        assert got["page_version"] == 1, "the stored version does not move"
        # An edit changes the words.
        r = await _as(live, KBA, "PATCH", f"/knowledge/pages/{page}",
                      {"body_md": "The rules, changed."})
        assert r.status_code == 200, r.text
        got = _item(await _list(live, LEAD, live.team), pin)
        assert got["state"] == "stale" and got["stale_reason"] == "changed"
        assert got["title"] == "Rules archive", "a reader of the page sees its title"
        # The stored state is untouched: the read derives it.
        assert _rows(live, "SELECT state FROM pm_project_authorities WHERE id ="
                           " CAST(:i AS uuid)", i=pin)[0].state == "active"

    async def test_a_member_who_cannot_read_the_page_sees_no_title(self, live):
        two = _space(live, "Shrinks", [("org", "read")])
        page = _page(live, two, SECRET_TITLE)
        pin = (await _pin(live, LEAD, live.team, page)).json()["id"]
        # The page narrows after the pin. The check ran at the pin only.
        live.seeder.one("DELETE FROM kb_space_grants WHERE space_id = CAST(:s AS uuid)"
                        " RETURNING space_id", s=two)
        live.seeder.one("INSERT INTO kb_space_grants (space_id, subject, level, created_by,"
                        " organization_id) VALUES (CAST(:s AS uuid), :u, 'read', :a,"
                        " CAST(:o AS uuid)) RETURNING space_id",
                        s=two, u=LEAD, a=w.OWNER, o=live.org)
        seen = _item(await _list(live, VIEWER, live.team), pin)
        assert seen["page_hidden"] is True
        assert "title" not in seen and "space_id" not in seen
        assert SECRET_TITLE not in json.dumps(seen)
        mine = _item(await _list(live, LEAD, live.team), pin)
        assert mine["page_hidden"] is False and mine["title"] == SECRET_TITLE

    async def test_a_hidden_page_shows_no_reason_and_no_version_but_keeps_the_state(
        self, live,
    ):
        # P2 of the ALN-4 review. A member who cannot read the page must not learn
        # that it was edited or unpublished. `state` stays: the authority needs care.
        two = _space(live, "Hides change", [("org", "read")])
        page = _page(live, two, "Rules hidden change", versions=2)
        pin = (await _pin(live, LEAD, live.team, page)).json()["id"]
        r = await _as(live, KBA, "PATCH", f"/knowledge/pages/{page}",
                      {"body_md": "Changed after the pin."})
        assert r.status_code == 200, r.text
        live.seeder.one("DELETE FROM kb_space_grants WHERE space_id = CAST(:s AS uuid)"
                        " RETURNING space_id", s=two)
        live.seeder.one("INSERT INTO kb_space_grants (space_id, subject, level, created_by,"
                        " organization_id) VALUES (CAST(:s AS uuid), :u, 'read', :a,"
                        " CAST(:o AS uuid)) RETURNING space_id",
                        s=two, u=LEAD, a=w.OWNER, o=live.org)
        seen = _item(await _list(live, VIEWER, live.team), pin)
        assert seen["page_hidden"] is True
        assert seen["state"] == "stale", "the member still sees that care is due"
        assert seen["stale_reason"] is None and seen["page_version"] is None
        mine = _item(await _list(live, LEAD, live.team), pin)
        assert mine["page_hidden"] is False
        assert mine["stale_reason"] == "changed" and mine["page_version"] == 2

    async def test_with_the_flag_off_all_three_routes_answer_404_and_write_nothing(
        self, live, monkeypatch,
    ):
        from acb_common import get_settings

        page = _page(live, live.space_org, "Rules flag")
        pin = (await _pin(live, LEAD, live.team, page)).json()["id"]
        before = len(_rows(live, "SELECT 1 FROM pm_project_authorities"))
        for off in ("", live.org_b):
            monkeypatch.setattr(get_settings(), "authority_pins_orgs", off, raising=False)
            assert (await _as(live, LEAD, "GET", _path(live.team))).status_code == 404
            assert (await _pin(live, LEAD, live.team, page)).status_code == 404
            r = await _as(live, LEAD, "DELETE", f"{_path(live.team)}/{pin}")
            assert r.status_code == 404
        assert len(_rows(live, "SELECT 1 FROM pm_project_authorities")) == before
        assert _rows(live, "SELECT state FROM pm_project_authorities WHERE id ="
                           " CAST(:i AS uuid)", i=pin)[0].state == "active"
        monkeypatch.setattr(get_settings(), "authority_pins_orgs", live.org, raising=False)
        assert (await _as(live, LEAD, "GET", _path(live.team))).status_code == 200

    async def test_a_member_who_cannot_see_the_project_reads_404(self, live):
        r = await _as(live, OUTSIDER, "GET", _path(live.hidden))
        assert r.status_code == 404, r.text
        r = await _as(live, LEAD, "GET", _path(live.hidden))
        assert r.status_code == 200, r.text
