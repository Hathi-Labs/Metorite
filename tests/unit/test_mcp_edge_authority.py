"""WS-55 EA-3a — the agent scope narrows the Projects visibility seam (§7.3).

Spec: ``project-docs/specs/external_agent_access.md`` §7.1, §7.3, §10.1 and
§15.2 D3, D4 and D14 (the gateway half) · decision D97 · EA3-4.

Two halves.

**Database-free (always runs).**

* **D3, the golden fence.** With ``agent`` None, ``Visibility.project_clause``,
  ``task_visibility_clause`` and ``Visibility.params`` give the exact strings
  they gave before EA-3a. The literals below were recorded from the tree at
  ``12fc3c6d5``, before the change.
* ``resolve_visibility`` copies ``user.agent``. ``resolve_visibility_for``
  resolves another person and cannot.

**R8 (a real database, as a NON-privileged role, on a phase-4-promoted
catalog of its own).** A signed request goes through the REAL branch 0, the
REAL default-deny guard and the REAL Projects routes, with
``IDENTITY_CUTOVER`` on so the member resolves through the RLS-exempt identity
tables. It proves:

* **D1.** A signed request of a live connection resolves to its member.
* **D4.** A pin, the personal opt-out and the seal each hide their target on
  BOTH arms of ``task_visibility_clause``: a task the member sees through a
  grant, and a task the member sees only as its assignee. A get answers 404
  with the text of a missing row, and the ``total`` of a list leaves the
  target out. The seal also hides a D63 hand-over task in the unrestricted
  (``data:org:read``) form.
* **D14 (gateway half).** A revoked connection and a suspended member each get
  401 on the next call.
* The connection read itself: live, revoked, expired and another tenant.

The gate is ``TENANT_LADDER_DATABASE_URL``. A skip is not a pass. Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_mcp_edge_authority.py -v -rs
"""
from __future__ import annotations

import asyncio
import inspect
import os
import re
import uuid
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import (
    AgentScope,
    UserContext,
    UserRole,
    agent_signing,
    agent_tokens,
    deps,
    get_current_user,
    require_authenticated,
)
from acb_auth import access as access_module
from acb_auth.access import invalidate as invalidate_access
from acb_auth.permissions import build_access
from acb_common import get_settings
from fastapi import FastAPI
from gateway.routes.projects import core
from gateway.routes.projects.core import Visibility, task_visibility_clause
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests.unit import _branch_zero_tripwire as tripwire
from tests.unit._tenant_ladder import _LADDER_DIR, apply_ladder, tenant_engine_scope

_SNAPSHOT = os.environ.get("_ACB_TENANT_LADDER_URL_AT_LAUNCH")
_URL = (
    _SNAPSHOT
    if _SNAPSHOT is not None
    else os.environ.get("TENANT_LADDER_DATABASE_URL", "")
).strip()

_DB_GATE = pytest.mark.skipif(
    not _URL,
    reason=(
        "TENANT_LADDER_DATABASE_URL unset — R8 requires a REAL Postgres with "
        "pgvector. A skip here is not a pass; CI must set it. ⚠️ Do NOT export "
        "it as DATABASE_URL — see test_org_provisioning.py's docstring."
    ),
)


# ═══════════════════════════════════════════════════════════════════════════
# Database-free: the golden fence (D3) and the resolver
# ═══════════════════════════════════════════════════════════════════════════

GOLDEN_ORG = "11111111-1111-1111-1111-111111111111"

_GRANTED = (
    "\nWITH RECURSIVE granted AS (\n    SELECT DISTINCT g.project_id AS id\n"
    "    FROM pm_project_grants g\n    JOIN pm_projects gp ON gp.id = g.project_id\n"
    "    WHERE g.organization_id = CAST(:vis_org AS uuid)\n"
    "      AND gp.sealed_at IS NULL\n      AND (g.subject = 'org'\n"
    "           OR lower(g.subject) = :vis_email\n"
    "           OR g.subject = ANY(:vis_groups))\n    UNION\n    SELECT p.id\n"
    "    FROM pm_projects p\n    JOIN granted a ON p.parent_project_id = a.id\n"
    "    WHERE p.organization_id = CAST(:vis_org AS uuid)\n"
    "      AND p.sealed_at IS NULL\n)\nSELECT id FROM granted\n"
)
_TENANT_ONLY = (
    "\nSELECT id FROM pm_projects\nWHERE organization_id = CAST(:vis_org AS uuid)\n"
    "  AND sealed_at IS NULL\n"
)


def _assignee(alias: str) -> str:
    return (
        f"      OR EXISTS (SELECT 1 FROM pm_task_assignees a"
        f"                 WHERE a.task_id = {alias}.id"
        f"                   AND lower(a.assignee) = :vis_email)))"
    )


#: The strings of today, recorded from the tree BEFORE EA-3a (sha256 below).
GOLDEN: dict[str, tuple[str, str]] = {
    "restricted.project_clause()": (
        f"id IN ({_GRANTED})",
        "dba65e0b590787f64898e72d22bee7038c18e66ea2ca7687141ba65c83995c39"),
    "restricted.project_clause('p.id')": (
        f"p.id IN ({_GRANTED})",
        "ecd969e75b9fd54e88baec240b7c430877d723fa7f41baa6ae3f312493364859"),
    "unrestricted.project_clause()": (
        f"id IN ({_TENANT_ONLY})",
        "34f2ac865083391bda3ac3d3136083b710ad4fdabda6e79c72f5f4f789a99497"),
    "unrestricted.project_clause('p.id')": (
        f"p.id IN ({_TENANT_ONLY})",
        "a9bbbdea0760ab66d3143bb3420a659132cf8abc8b1348639b93d8a714b3bd78"),
    "task_visibility_clause(restricted)": (
        f"(t.organization_id = CAST(:vis_org AS uuid) AND (t.project_id IN ({_GRANTED})"
        + _assignee("t"),
        "3c4159407bc824edc0b27b6b9a70a20ba7236657d0c865e3a0570bd738b5eb12"),
    "task_visibility_clause(restricted, 'x')": (
        f"(x.organization_id = CAST(:vis_org AS uuid) AND (x.project_id IN ({_GRANTED})"
        + _assignee("x"),
        "cc2295becad3c04cbac338584b95bf7c27c47cc363aa012f6918000471e84480"),
    # ⚠️ H-304 changed these two ON PURPOSE (D63). The unrestricted task
    # clause was the tenant alone, so `data:org:read` read a closed tree. It
    # now has the same two arms as the restricted clause, over the tenant set.
    "task_visibility_clause(unrestricted)": (
        f"(t.organization_id = CAST(:vis_org AS uuid) AND (t.project_id IN ({_TENANT_ONLY})"
        + _assignee("t"),
        "bbf499b5a640c6de7e3ec2c8f344620f29223a9e000b7962646653b703561ec1"),
    "task_visibility_clause(unrestricted, 'x')": (
        f"(x.organization_id = CAST(:vis_org AS uuid) AND (x.project_id IN ({_TENANT_ONLY})"
        + _assignee("x"),
        "ef6e9fc8a6643692f405b9a218bc030cfa2f2c54e303c1d7b8b2a74591c7f60f"),
}


def _golden_visibilities() -> tuple[Visibility, Visibility]:
    restricted = Visibility(unrestricted=False, email="dev@golden.test",
                            groups=("group:ops",), organization_id=GOLDEN_ORG)
    unrestricted = Visibility(unrestricted=True, email="admin@golden.test",
                              groups=(), organization_id=GOLDEN_ORG)
    return restricted, unrestricted


def _today() -> dict[str, str]:
    restricted, unrestricted = _golden_visibilities()
    return {
        "restricted.project_clause()": restricted.project_clause(),
        "restricted.project_clause('p.id')": restricted.project_clause("p.id"),
        "unrestricted.project_clause()": unrestricted.project_clause(),
        "unrestricted.project_clause('p.id')": unrestricted.project_clause("p.id"),
        "task_visibility_clause(restricted)": task_visibility_clause(restricted),
        "task_visibility_clause(restricted, 'x')": task_visibility_clause(restricted, "x"),
        "task_visibility_clause(unrestricted)": task_visibility_clause(unrestricted),
        "task_visibility_clause(unrestricted, 'x')": task_visibility_clause(unrestricted, "x"),
    }


class TestTheHumanPathIsByteIdentical:
    """D3: with ``agent`` None, every clause and every param is unchanged."""

    def test_the_golden_literals_match_their_recorded_hashes(self):
        import hashlib

        for name, (literal, digest) in GOLDEN.items():
            assert hashlib.sha256(literal.encode()).hexdigest() == digest, name

    @pytest.mark.parametrize("name", sorted(GOLDEN))
    def test_each_clause_string_is_byte_identical_to_before(self, name):
        assert _today()[name] == GOLDEN[name][0]

    def test_the_params_are_unchanged(self):
        restricted, unrestricted = _golden_visibilities()
        assert restricted.params == {
            "vis_email": "dev@golden.test", "vis_groups": ["group:ops"],
            "vis_org": GOLDEN_ORG}
        # H-304: the unrestricted caller binds its email for the assignee arm.
        assert unrestricted.params == {
            "vis_email": "admin@golden.test", "vis_org": GOLDEN_ORG}

    def test_agent_none_is_the_default(self):
        assert Visibility(unrestricted=False, email="", groups=()).agent is None

    @pytest.mark.parametrize("unrestricted", [False, True])
    def test_a_stand_in_with_no_agent_attribute_fails_closed(self, unrestricted):
        """A Visibility-like object with no ``agent`` field RAISES. It never
        reads as "no narrowing", so a future duck-typed scope cannot drop the
        agent narrowing by leaving the field out. The two older stand-ins
        (``test_projects_analytics_throughput.py``, ``test_subtasks_s5.py``)
        carry ``agent=None`` for that reason."""
        stand_in = SimpleNamespace(
            unrestricted=unrestricted, email="dev@golden.test",
            groups=("group:ops",), organization_id=GOLDEN_ORG)
        with pytest.raises(AttributeError):
            task_visibility_clause(stand_in)
        with pytest.raises(AttributeError):
            Visibility.project_clause(stand_in)

    @pytest.mark.parametrize("unrestricted", [False, True])
    def test_a_stand_in_with_agent_none_gives_the_old_sql(self, unrestricted):
        stand_in = SimpleNamespace(
            unrestricted=unrestricted, email="dev@golden.test",
            groups=("group:ops",), organization_id=GOLDEN_ORG, agent=None)
        kind = "unrestricted" if unrestricted else "restricted"
        assert task_visibility_clause(stand_in) == GOLDEN[
            f"task_visibility_clause({kind})"][0]
        assert Visibility.project_clause(stand_in) == GOLDEN[
            f"{kind}.project_clause()"][0]


def _scope(**over: Any) -> AgentScope:
    values: dict[str, Any] = {
        "connection_id": "c", "pinned_projects": (), "include_personal": True}
    values.update(over)
    return AgentScope(**values)


class TestTheNarrowingShape:
    """The shape of §7.3, read as text. The R8 class below proves the rows."""

    @pytest.mark.parametrize("unrestricted", [False, True])
    def test_one_and_wraps_both_arms_of_the_task_clause(self, unrestricted):
        vis = Visibility(unrestricted=unrestricted, email="m@x.test", groups=(),
                         organization_id=GOLDEN_ORG, agent=_scope())
        plain = task_visibility_clause(
            Visibility(unrestricted=unrestricted, email="m@x.test", groups=(),
                       organization_id=GOLDEN_ORG))
        narrowed = task_visibility_clause(vis)
        assert narrowed.startswith(f"({plain} AND t.project_id IN (")
        assert "sealed_at IS NULL" in narrowed[len(plain):]

    def test_the_personal_opt_out_and_the_pins_add_their_lines(self):
        base = Visibility(unrestricted=False, email="m@x.test", groups=(),
                          organization_id=GOLDEN_ORG)
        open_ = core._narrow_for_agent("X", "c", _scope())
        assert "personal_owner" not in open_ and "vis_pins" not in open_
        closed = core._narrow_for_agent("X", "c", _scope(include_personal=False))
        assert "AND personal_owner IS NULL" in closed
        pinned = core._narrow_for_agent("X", "c", _scope(pinned_projects=("p1",)))
        assert "ANY(CAST(:vis_pins AS uuid[]))" in pinned
        assert not re.search(r"NOT\s+IN", pinned), "a pin is a POSITIVE IN"
        assert "vis_pins" not in base.params
        with_pins = Visibility(unrestricted=False, email="m@x.test", groups=(),
                               organization_id=GOLDEN_ORG,
                               agent=_scope(pinned_projects=("p1", "p2")))
        assert with_pins.params["vis_pins"] == ["p1", "p2"]

    def test_the_project_clause_is_narrowed_too(self):
        vis = Visibility(unrestricted=True, email="", groups=(),
                         organization_id=GOLDEN_ORG,
                         agent=_scope(include_personal=False))
        clause = vis.project_clause("p.id")
        assert clause.startswith(f"(p.id IN ({_TENANT_ONLY}) AND p.id IN (")
        assert "personal_owner IS NULL" in clause


class _FakeDb:
    """Answers the two reads of ``resolve_visibility``."""

    def __init__(self, org: str) -> None:
        self.org = org

    async def execute(self, statement, params=None):
        sql = str(statement)
        if "organization_id" in sql and "app_user" in sql:
            row = SimpleNamespace(organization_id=self.org)
            return SimpleNamespace(fetchone=lambda: row, fetchall=lambda: [row])
        return SimpleNamespace(fetchone=lambda: None, fetchall=lambda: [])


class TestTheResolverCopiesTheScope:

    @pytest.mark.parametrize("perms", [["feature:projects"], ["data:org:read"]])
    async def test_resolve_visibility_copies_the_callers_agent(self, perms):
        scope = _scope(pinned_projects=("p1",))
        user = UserContext(email="m@x.test", role=UserRole.EMPLOYEE,
                           access=build_access(perms), agent=scope)
        vis = await core.resolve_visibility(_FakeDb(GOLDEN_ORG), user)
        assert vis.agent is scope

    async def test_a_human_caller_gets_no_agent(self):
        user = UserContext(email="m@x.test", role=UserRole.EMPLOYEE,
                           access=build_access(["feature:projects"]))
        vis = await core.resolve_visibility(_FakeDb(GOLDEN_ORG), user)
        assert vis.agent is None

    async def test_resolve_visibility_for_cannot_take_an_agent(self):
        assert "user" not in inspect.signature(core.resolve_visibility_for).parameters
        vis = await core.resolve_visibility_for(_FakeDb(GOLDEN_ORG), "m@x.test")
        assert vis.agent is None


# ═══════════════════════════════════════════════════════════════════════════
# R8: the whole chain on a phase-4-promoted catalog, as a non-privileged role
# ═══════════════════════════════════════════════════════════════════════════

_APP_ROLE = "acb_app_mcpauth"
_APP_PW = "mcpauth_scratch_pw"
_GENERATED = os.path.join(_LADDER_DIR, "generated")

KEY = "mcp-authority-signing-key-" + "k" * 20
INTERNAL = "mcp-authority-internal-token"
MEMBER = "agent-member@mcpauth.test"
READER = "org-reader@mcpauth.test"
OTHER = "someone-else@mcpauth.test"
B_MEMBER = "b-member@mcpauth.test"
MOVER = "mover@mcpauth.test"
OWNER = "owner@mcpauth.test"
ORIGIN = "https://client.example"
VIA = "mcp:client.example"


def _exec_sql_file(conn, sql: str) -> None:
    with conn.connection.dbapi_connection.cursor() as cur:
        cur.execute(sql)


def _drop_database(admin_url, name: str) -> None:
    maint = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with maint.connect() as c:
        c.execute(text(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = :d AND pid <> pg_backend_pid()"), {"d": name})
        c.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
    maint.dispose()


class _Seeder:
    """Admin writes, each in a transaction with the tenant GUC set, so every
    tenant column default stamps the right organization."""

    def __init__(self, engine, org: str) -> None:
        self.engine = engine
        self.org = org
        self.number = 0

    def one(self, sql: str, **params) -> Any:
        with self.engine.begin() as c:
            c.execute(text("SELECT set_config('app.tenant_id', :t, true)"),
                      {"t": self.org})
            row = c.execute(text(sql), params).first()
        return row[0] if row is not None else None

    def project(self, name: str, *, parent: str | None = None,
                personal_owner: str | None = None,
                grant_to: str | None = None) -> str:
        pid = str(self.one(
            "INSERT INTO pm_projects (name, status, source, created_by, "
            " organization_id, timezone, parent_project_id, owns_statuses, "
            " personal_owner) VALUES (:n, 'active', 'manual', :a, CAST(:o AS uuid),"
            " 'Asia/Kolkata', CAST(:parent AS uuid), true, :po) RETURNING id",
            n=name, a=OWNER, o=self.org, parent=parent, po=personal_owner))
        if grant_to:
            self.one(
                "INSERT INTO pm_project_grants (project_id, subject, created_by,"
                " organization_id) VALUES (CAST(:p AS uuid), :s, :a,"
                " CAST(:o AS uuid)) RETURNING project_id",
                p=pid, s=grant_to, a=OWNER, o=self.org)
        return pid

    def task(self, project: str, root: str, title: str, *,
             assignee: str | None = None) -> str:
        status = self.one(
            "SELECT id FROM pm_task_statuses WHERE project_id = CAST(:p AS uuid)"
            " LIMIT 1", p=root)
        if status is None:
            status = self.one(
                "INSERT INTO pm_task_statuses (project_id, name, color, position,"
                " category, organization_id) VALUES (CAST(:p AS uuid), 'To do',"
                " 'gray', 0, 'todo', CAST(:o AS uuid)) RETURNING id",
                p=root, o=self.org)
        self.number += 1
        tid = str(self.one(
            "INSERT INTO pm_tasks (title, project_id, root_project_id, status_id,"
            " created_by, organization_id, task_number) VALUES (:t,"
            " CAST(:p AS uuid), CAST(:r AS uuid), CAST(:s AS uuid), :a,"
            " CAST(:o AS uuid), :n) RETURNING id",
            t=title, p=project, r=root, s=str(status), a=OWNER, o=self.org,
            n=self.number + (0 if self.org.endswith("a") else 1000)))
        if assignee:
            self.one(
                "INSERT INTO pm_task_assignees (task_id, assignee, assigned_by,"
                " organization_id) VALUES (CAST(:t AS uuid), :e, :a,"
                " CAST(:o AS uuid)) RETURNING task_id",
                t=tid, e=assignee, a=OWNER, o=self.org)
        return tid

    def member(self, email: str, permissions: list[str]) -> None:
        identity = self.one(
            "INSERT INTO user_identity (email, display_name) VALUES (:e, :e)"
            " ON CONFLICT DO NOTHING RETURNING id", e=email)
        if identity is None:
            identity = self.one("SELECT id FROM user_identity WHERE email = :e",
                                e=email)
        self.one(
            "INSERT INTO org_membership (organization_id, user_id, status)"
            " VALUES (CAST(:o AS uuid), CAST(:u AS uuid), 'active')"
            " RETURNING user_id", o=self.org, u=str(identity))
        app_user = self.one(
            "INSERT INTO app_user (email, display_name, organization_id, status)"
            " VALUES (:e, :e, CAST(:o AS uuid), 'active') RETURNING id",
            e=email, o=self.org)
        role = self.one(
            "INSERT INTO org_role (organization_id, slug, display_name)"
            " VALUES (CAST(:o AS uuid), :s, :s) RETURNING id",
            o=self.org, s="mcp-" + email.split("@")[0])
        for permission in permissions:
            self.one(
                "INSERT INTO org_role_permission (role_id, permission)"
                " VALUES (CAST(:r AS uuid), :p) RETURNING role_id",
                r=str(role), p=permission)
        self.one(
            "INSERT INTO user_role (user_id, role_id) VALUES (CAST(:u AS uuid),"
            " CAST(:r AS uuid)) RETURNING user_id", u=str(app_user), r=str(role))

    def connection(self, member: str, *, pins: tuple[str, ...] = (),
                   include_personal: bool = True,
                   expires: str = "now() + interval '1 day'") -> str:
        return str(self.one(
            "INSERT INTO agent_connection (organization_id, member_email,"
            " client_id, client_name, client_origin, scopes, pinned_projects,"
            " include_personal, expires_at) VALUES (CAST(:o AS uuid), :m,"
            " 'https://client.example/meta.json', 'Client', :origin,"
            " ARRAY['projects:read'], CAST(:pins AS uuid[]), :personal, "
            + expires + ") RETURNING id",
            o=self.org, m=member, origin=ORIGIN, pins=list(pins),
            personal=include_personal))


@pytest.fixture(scope="module")
def world():
    """A private promoted catalog, two tenants, two members, and the trees."""
    admin_url = make_url(_URL)
    dedicated = f"{admin_url.database}_mcpauthority"
    _drop_database(admin_url, dedicated)
    with create_engine(admin_url, isolation_level="AUTOCOMMIT").connect() as c:
        c.execute(text(f'CREATE DATABASE "{dedicated}"'))
    ded_admin_url = admin_url.set(database=dedicated)
    eng = create_engine(ded_admin_url, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    for phase in ("01_add_columns.sql", "02_backfill.sql",
                  "03_constraints.sql", "04_policies.sql"):
        with eng.begin() as conn, open(os.path.join(_GENERATED, phase),
                                       encoding="utf-8") as fh:
            _exec_sql_file(conn, fh.read())
    with create_engine(ded_admin_url, isolation_level="AUTOCOMMIT").connect() as c:
        c.execute(text(
            f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE "
            f"rolname='{_APP_ROLE}') THEN CREATE ROLE {_APP_ROLE} LOGIN; "
            f"END IF; END $$;"))
        c.execute(text(
            f"ALTER ROLE {_APP_ROLE} WITH LOGIN PASSWORD '{_APP_PW}' "
            f"NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS INHERIT"))
        c.execute(text(f'GRANT CONNECT ON DATABASE "{dedicated}" TO {_APP_ROLE}'))
        c.execute(text(f"GRANT USAGE ON SCHEMA public TO {_APP_ROLE}"))
        c.execute(text("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES "
                       f"IN SCHEMA public TO {_APP_ROLE}"))
        c.execute(text("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public "
                       f"TO {_APP_ROLE}"))

    org_a = str(uuid.uuid4())[:-1] + "a"
    org_b = str(uuid.uuid4())[:-1] + "b"
    with eng.begin() as c:
        for oid, slug in ((org_a, "mcpauth-a"), (org_b, "mcpauth-b")):
            c.execute(text("INSERT INTO organization (id, slug, display_name) "
                           "VALUES (:id, :slug, :slug)"), {"id": oid, "slug": slug})

    a = _Seeder(eng, org_a)
    b = _Seeder(eng, org_b)
    a.member(MEMBER, ["feature:projects"])
    a.member(READER, ["feature:projects", "data:org:read"])

    w = SimpleNamespace(admin=eng, org_a=org_a, org_b=org_b, p={}, t={}, c={})
    p, t = w.p, w.t
    p["team"] = a.project("Team", grant_to=MEMBER)
    p["team_child"] = a.project("Team child", parent=p["team"])
    p["other"] = a.project("Other team", grant_to=MEMBER)
    p["foreign"] = a.project("Foreign", grant_to=None)
    p["personal"] = a.project("Mine", personal_owner=MEMBER, grant_to=MEMBER)
    p["personal_other"] = a.project("Theirs", personal_owner=OTHER)
    p["sealed"] = a.project("Departed", grant_to=MEMBER)
    p["b_root"] = b.project("Org B")

    t["team"] = a.task(p["team"], p["team"], "team task")
    t["child"] = a.task(p["team_child"], p["team"], "child task")
    t["other"] = a.task(p["other"], p["other"], "other task (grant arm)")
    t["foreign"] = a.task(p["foreign"], p["foreign"], "foreign (assignee arm)",
                          assignee=MEMBER)
    t["personal"] = a.task(p["personal"], p["personal"], "personal (grant arm)")
    t["personal_other"] = a.task(p["personal_other"], p["personal_other"],
                                 "their personal (assignee arm)", assignee=MEMBER)
    t["handover"] = a.task(p["sealed"], p["sealed"], "hand-over (assignee arm)",
                           assignee=MEMBER)
    t["b"] = b.task(p["b_root"], p["b_root"], "org B task", assignee=MEMBER)
    a.one("UPDATE pm_projects SET sealed_at = now() WHERE id = CAST(:p AS uuid)"
          " RETURNING id", p=p["sealed"])

    w.c["open"] = a.connection(MEMBER)
    w.c["pinned"] = a.connection(MEMBER, pins=(p["team"],))
    w.c["no_personal"] = a.connection(MEMBER, include_personal=False)
    w.c["reader"] = a.connection(READER)
    w.c["revoke"] = a.connection(MEMBER)
    w.c["suspend"] = a.connection(MEMBER)
    w.c["expired"] = a.connection(MEMBER, expires="now() - interval '1 second'")
    w.c["b"] = b.connection(MEMBER)
    b.member(B_MEMBER, ["feature:projects"])
    w.c["b_member"] = b.connection(B_MEMBER)
    # A member of org A whose connection row still says org B (S4).
    a.member(MOVER, ["feature:projects"])
    w.c["mover_row_b"] = b.connection(MOVER)
    w.app_dsn = admin_url.set(
        database=dedicated, username=_APP_ROLE, password=_APP_PW,
    ).render_as_string(hide_password=False)
    #: The superuser of the catalog. It BYPASSES RLS, which is the point of
    #: the one test that uses it.
    w.admin_dsn = admin_url.set(database=dedicated).render_as_string(
        hide_password=False)
    w.seeder_a = a
    yield w

    eng.dispose()
    _drop_database(admin_url, dedicated)


@pytest.fixture
def armed(monkeypatch, world):
    settings = get_settings()
    monkeypatch.setattr(settings, "agent_access_enabled", True)
    # EA-10p: each organization, unless a test narrows the list.
    monkeypatch.setattr(settings, "agent_access_orgs", "*")
    monkeypatch.setattr(settings, "agent_edge_signing_key", KEY)
    monkeypatch.setattr(settings, "agent_edge_signing_key_previous", "")
    monkeypatch.setenv("GATEWAY_INTERNAL_TOKEN", INTERNAL)
    monkeypatch.setenv("IDENTITY_CUTOVER", "1")
    monkeypatch.setenv("ALLOWED_EMAIL_DOMAIN", "mcpauth.test")
    monkeypatch.delenv("GATEWAY_TRUST_UNVERIFIED_SSO_HEADERS", raising=False)
    # `access._tables_missing` is a process-wide latch. An earlier suite that
    # reached a database with no access tables sets it, and every member then
    # resolves to the legacy mapping. Clear it here and restore it after, so
    # this suite does not depend on the order of the run.
    monkeypatch.setattr(access_module, "_tables_missing", False)
    invalidate_access()
    # Every request of the suite runs branch 0 under the runtime tripwire
    # (`_branch_zero_tripwire.py`), and the teardown asserts no violation.
    _ARMED.append(tripwire.arm(monkeypatch, deps))
    try:
        yield world
    finally:
        _ARMED.clear()
        invalidate_access()
    tripwire.assert_clean()


#: The armed tripwire of the running test. `_gateway` installs its override.
_ARMED: list = []


def _gateway() -> FastAPI:
    """The Projects router and ``/auth/me`` behind the real default-deny guard,
    with branch 0 under the tripwire while a test has armed it."""
    from gateway.routes.admin.me import me_router
    from gateway.routes.projects import router as projects_router

    app = FastAPI(dependencies=[require_authenticated(public=frozenset())])
    app.include_router(projects_router)
    app.include_router(me_router)
    if _ARMED:
        app.dependency_overrides[get_current_user] = (
            tripwire.current_user_override(_ARMED[0]))
    return app


async def _send(world, url: str, headers: dict[str, str],
                dsn: str | None = None) -> httpx.Response:
    async def _go() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=_gateway()), base_url="http://gw",
        ) as client:
            return await client.get(url, headers=headers)

    async with tenant_engine_scope(dsn or world.app_dsn):
        return await asyncio.create_task(_go())


async def _agent(world, connection: str, path: str, query: str = "",
                 org: str | None = None, dsn: str | None = None) -> httpx.Response:
    headers = agent_signing.signed_headers(
        KEY, organization_id=org or world.org_a, connection_id=connection,
        actor_via=VIA, method="GET", path=path, query=query)
    return await _send(world, f"{path}?{query}" if query else path, headers, dsn)


async def _human(world, email: str, path: str, query: str = "") -> httpx.Response:
    headers = {"Authorization": f"Bearer {INTERNAL}", "X-User-Email": email}
    return await _send(world, f"{path}?{query}" if query else path, headers)


def _ids(body: Any) -> set[str]:
    """Every UUID-shaped string anywhere in a JSON body."""
    out: set[str] = set()
    if isinstance(body, dict):
        for value in body.values():
            out |= _ids(value)
    elif isinstance(body, list):
        for value in body:
            out |= _ids(value)
    elif isinstance(body, str) and re.fullmatch(r"[0-9a-f-]{36}", body):
        out.add(body)
    return out


async def _list(world, who: str, *, connection: str | None = None,
                email: str = MEMBER) -> tuple[set[str], int]:
    query = "page_size=100"
    if connection:
        r = await _agent(world, connection, "/projects/tasks", query)
    else:
        r = await _human(world, email, "/projects/tasks", query)
    assert r.status_code == 200, (who, r.status_code, r.text)
    body = r.json()
    return {row["id"] for row in body["rows"]}, int(body["total"])


_MISSING_TASK = "/projects/tasks/00000000-0000-4000-8000-000000000000"


@_DB_GATE
class TestTheChainOnARealDatabase:
    """D1, and the human baseline every narrowing below is measured against."""

    async def test_a_signed_request_resolves_to_the_connections_member(self, armed):
        r = await _agent(armed, armed.c["open"], "/auth/me")
        assert r.status_code == 200, r.text
        assert r.json()["email"] == MEMBER

    async def test_the_human_baseline_sees_every_target(self, armed):
        rows, total = await _list(armed, "human")
        t = armed.t
        expected = {t["team"], t["child"], t["other"], t["foreign"], t["personal"],
                    t["personal_other"], t["handover"]}
        assert expected <= rows, "the human sees the grant AND the assignee arms"
        assert t["b"] not in rows, "never another tenant's task"
        assert total == len(rows)

    async def test_an_open_connection_hides_only_the_sealed_hand_over(self, armed):
        rows, total = await _list(armed, "open", connection=armed.c["open"])
        human, human_total = await _list(armed, "human")
        assert rows == human - {armed.t["handover"]}
        assert total == human_total - 1


@_DB_GATE
class TestEachNarrowingHidesItsTargetOnBothArms:
    """D4. Each target is hidden from the list, from the total and from a get,
    and the get says exactly what a missing row says."""

    async def _hidden(self, world, connection: str, targets: list[str],
                      kept: list[str]) -> None:
        rows, total = await _list(world, "agent", connection=connection)
        for target in targets:
            assert target not in rows, target
        for task in kept:
            assert task in rows, task
        assert total == len(rows)
        missing = await _agent(world, connection, _MISSING_TASK)
        assert missing.status_code == 404
        for target in targets:
            r = await _agent(world, connection, f"/projects/tasks/{target}")
            assert r.status_code == 404, (target, r.text)
            assert r.json() == missing.json(), "one not-found text"
        for task in kept:
            r = await _agent(world, connection, f"/projects/tasks/{task}")
            assert r.status_code == 200, (task, r.text)

    async def test_a_pin_hides_a_grant_arm_task_and_an_assignee_arm_task(self, armed):
        t = armed.t
        await self._hidden(
            armed, armed.c["pinned"],
            targets=[t["other"], t["foreign"], t["personal"], t["personal_other"],
                     t["handover"]],
            kept=[t["team"], t["child"]],
        )

    async def test_the_personal_opt_out_hides_both_arms(self, armed):
        t = armed.t
        await self._hidden(
            armed, armed.c["no_personal"],
            targets=[t["personal"], t["personal_other"], t["handover"]],
            kept=[t["team"], t["child"], t["other"], t["foreign"]],
        )

    async def test_the_seal_hides_a_hand_over_task_from_an_agent(self, armed):
        """The human sees the D63 hand-over task (the assignee arm). The agent
        of the same member does not."""
        human = await _human(armed, MEMBER, f"/projects/tasks/{armed.t['handover']}")
        assert human.status_code == 200, human.text
        await self._hidden(armed, armed.c["open"], targets=[armed.t["handover"]],
                           kept=[armed.t["team"], armed.t["foreign"]])

    async def test_the_seal_binds_the_unrestricted_form_too(self, armed):
        """H-304: the human reader no longer lists the sealed task either. The
        reader is not its assignee, so the hand-over arm does not reach it."""
        rows, _ = await _list(armed, "reader human", email=READER)
        assert armed.t["handover"] not in rows, "data:org:read lists the sealed task"
        agent_rows, agent_total = await _list(armed, "reader agent",
                                              connection=armed.c["reader"])
        assert armed.t["handover"] not in agent_rows
        assert agent_rows == rows
        assert agent_total == len(agent_rows)

    async def test_a_pin_hides_a_project_from_the_tree_and_from_a_get(self, armed):
        p = armed.p
        tree = await _agent(armed, armed.c["pinned"], "/projects/tree")
        assert tree.status_code == 200, tree.text
        seen = _ids(tree.json())
        assert p["team"] in seen and p["team_child"] in seen
        assert p["other"] not in seen
        human = _ids((await _human(armed, MEMBER, "/projects/tree")).json())
        assert p["other"] in human
        missing = await _agent(armed, armed.c["pinned"],
                               "/projects/nodes/00000000-0000-4000-8000-000000000000")
        hidden = await _agent(armed, armed.c["pinned"], f"/projects/nodes/{p['other']}")
        assert missing.status_code == hidden.status_code == 404
        assert hidden.json() == missing.json()
        shown = await _agent(armed, armed.c["pinned"], f"/projects/nodes/{p['team_child']}")
        assert shown.status_code == 200, shown.text


@_DB_GATE
class TestTheNextCallAfterARevokeOrASuspension:
    """D14, the gateway half."""

    async def test_a_revoked_connection_gets_401_on_the_next_call(self, armed):
        assert (await _agent(armed, armed.c["revoke"], "/auth/me")).status_code == 200
        armed.seeder_a.one(
            "UPDATE agent_connection SET revoked_at = now() WHERE id = CAST(:c AS uuid)"
            " RETURNING id", c=armed.c["revoke"])
        assert (await _agent(armed, armed.c["revoke"], "/auth/me")).status_code == 401

    async def test_an_expired_connection_gets_401(self, armed):
        assert (await _agent(armed, armed.c["expired"], "/auth/me")).status_code == 401

    async def test_a_connection_of_another_organization_gets_401(self, armed):
        """Org B's connection, signed as org A's: the read binds org A."""
        r = await _agent(armed, armed.c["b"], "/auth/me")
        assert r.status_code == 401
        r = await _agent(armed, armed.c["open"], "/auth/me", org=armed.org_b)
        assert r.status_code == 401

    async def test_a_suspended_member_gets_401_on_the_next_call(self, armed):
        """The admin member routes clear the access cache. The test does what
        they do (``invalidate_for``), then calls again."""
        email = MEMBER
        assert (await _agent(armed, armed.c["suspend"], "/auth/me")).status_code == 200
        try:
            armed.seeder_a.one(
                "UPDATE org_membership SET status = 'suspended' WHERE user_id ="
                " (SELECT id FROM user_identity WHERE email = :e) RETURNING user_id",
                e=email)
            armed.seeder_a.one(
                "UPDATE app_user SET status = 'suspended' WHERE email = :e"
                " RETURNING id", e=email)
            invalidate_access(email)
            r = await _agent(armed, armed.c["suspend"], "/auth/me")
            assert r.status_code == 401
        finally:
            armed.seeder_a.one(
                "UPDATE org_membership SET status = 'active' WHERE user_id ="
                " (SELECT id FROM user_identity WHERE email = :e) RETURNING user_id",
                e=email)
            armed.seeder_a.one(
                "UPDATE app_user SET status = 'active' WHERE email = :e"
                " RETURNING id", e=email)
            invalidate_access(email)


@_DB_GATE
class TestBranchZeroReadsTheOrgAllowlist:
    """EA-10p door 4 (spec §10.6). ``read_live_connection`` finds no
    connection for an organization off the list, so branch 0 refuses the
    signed request as it refuses a dead connection. Branch 0 itself does not
    change, so its digest stays the same."""

    async def test_a_listed_organization_passes(self, armed, monkeypatch):
        monkeypatch.setattr(get_settings(), "agent_access_orgs", armed.org_a)
        r = await _agent(armed, armed.c["open"], "/auth/me")
        assert r.status_code == 200, r.text
        assert r.json()["email"] == MEMBER

    @pytest.mark.parametrize("listed", ["", "ORG_B", "not-a-uuid"])
    async def test_an_organization_off_the_list_gets_401(self, armed, monkeypatch, listed):
        value = armed.org_b if listed == "ORG_B" else listed
        monkeypatch.setattr(get_settings(), "agent_access_orgs", value)
        r = await _agent(armed, armed.c["open"], "/auth/me")
        assert r.status_code == 401, r.text
        async with tenant_engine_scope(armed.app_dsn):
            found = await agent_tokens.read_live_connection(armed.org_a, armed.c["open"])
        assert found is None

    async def test_a_star_passes_each_organization(self, armed, monkeypatch):
        monkeypatch.setattr(get_settings(), "agent_access_orgs", "*")
        a = await _agent(armed, armed.c["open"], "/auth/me")
        b = await _agent(armed, armed.c["b_member"], "/auth/me", org=armed.org_b)
        assert (a.status_code, b.status_code) == (200, 200), (a.text, b.text)

    async def test_the_flag_off_is_401_even_for_a_listed_organization(
            self, armed, monkeypatch):
        monkeypatch.setattr(get_settings(), "agent_access_orgs", armed.org_a)
        monkeypatch.setattr(get_settings(), "agent_access_enabled", False)
        r = await _agent(armed, armed.c["open"], "/auth/me")
        assert r.status_code == 401

    async def test_the_list_reads_the_rows_organization(self, armed, monkeypatch):
        """The read checks the organization of the ROW, never the argument.
        A lenient read (no org predicate, run as the superuser that bypasses
        RLS) finds org B's row for org A. With only org A on the list, the
        read still answers None."""
        predicate = TestTheRowOrganizationIsTheOneThatCounts._PREDICATE
        assert agent_tokens._CONNECTION_LIVE_SQL.count(predicate) == 1
        monkeypatch.setattr(
            agent_tokens, "_CONNECTION_LIVE_SQL",
            agent_tokens._CONNECTION_LIVE_SQL.replace(predicate, ""))
        monkeypatch.setattr(get_settings(), "agent_access_orgs", armed.org_a)
        async with tenant_engine_scope(armed.admin_dsn):
            found = await agent_tokens.read_live_connection(
                armed.org_a, armed.c["b_member"])
        assert found is None


@_DB_GATE
class TestTheConnectionReads:
    """``read_live_connection`` and the wider ``lookup_token``, as the
    non-privileged role, on the promoted catalog."""

    async def test_a_live_connection_reads_with_its_scope(self, armed):
        async with tenant_engine_scope(armed.app_dsn):
            found = await agent_tokens.read_live_connection(
                armed.org_a, armed.c["pinned"])
        assert found is not None
        assert found.member_email == MEMBER
        assert found.client_origin == ORIGIN
        assert found.scopes == ("projects:read",)
        assert found.pinned_projects == (armed.p["team"],)
        assert found.include_personal is True

    async def test_a_dead_or_foreign_connection_reads_as_none(self, armed):
        async with tenant_engine_scope(armed.app_dsn):
            assert await agent_tokens.read_live_connection(
                armed.org_a, armed.c["expired"]) is None
            assert await agent_tokens.read_live_connection(
                armed.org_b, armed.c["open"]) is None
            assert await agent_tokens.read_live_connection(
                armed.org_a, armed.c["b"]) is None
            assert await agent_tokens.read_live_connection(
                "not-a-uuid", armed.c["open"]) is None

    async def test_the_token_lookup_returns_the_connection_scope(self, armed):
        raw = agent_tokens.new_token()
        armed.seeder_a.one(
            "INSERT INTO agent_token (organization_id, connection_id, kind,"
            " token_hash, expires_at, client_id, resource, scope) VALUES"
            " (CAST(:o AS uuid), CAST(:c AS uuid), 'access', :h,"
            " now() + interval '1 hour', 'https://client.example/meta.json',"
            " 'https://mcp.metorite.com/mcp', 'projects:read') RETURNING id",
            o=armed.org_a, c=armed.c["no_personal"], h=agent_tokens.hash_token(raw))
        async with tenant_engine_scope(armed.app_dsn):
            found = await agent_tokens.lookup_token(raw)
        assert found is not None
        assert found.connection_id == armed.c["no_personal"]
        assert found.scopes == ("projects:read",)
        assert found.pinned_projects == ()
        assert found.include_personal is False
        assert found.client_origin == ORIGIN

    async def test_the_suite_runs_as_a_role_that_cannot_bypass_rls(self, armed):
        """Every R8 class here runs as this role, so FORCE RLS binds it."""
        engine = create_engine(make_url(armed.app_dsn), future=True)
        try:
            with engine.connect() as c:
                row = c.execute(text(
                    "SELECT rolbypassrls, rolsuper FROM pg_roles"
                    " WHERE rolname = current_user")).one()
        finally:
            engine.dispose()
        assert (row.rolbypassrls, row.rolsuper) == (False, False)


@_DB_GATE
class TestTheRowOrganizationIsTheOneThatCounts:
    """Fix round 1, P2-2. Branch 0 compares the SIGNED organization with the
    one the connection ROW holds. Normally the read's own predicate and RLS
    make the two agree. This class removes both: the read loses its
    ``organization_id = :org`` line (a mutated copy of the SQL string), and it
    runs as the catalog's superuser, which bypasses RLS. Only the comparison
    in branch 0 is then left to refuse."""

    _PREDICATE = "   AND organization_id = CAST(:org AS UUID)\n"

    def _lenient(self, monkeypatch) -> None:
        assert agent_tokens._CONNECTION_LIVE_SQL.count(self._PREDICATE) == 1
        monkeypatch.setattr(
            agent_tokens, "_CONNECTION_LIVE_SQL",
            agent_tokens._CONNECTION_LIVE_SQL.replace(self._PREDICATE, ""))

    async def test_the_lenient_read_reports_the_rows_organization(self, armed, monkeypatch):
        self._lenient(monkeypatch)
        async with tenant_engine_scope(armed.admin_dsn):
            found = await agent_tokens.read_live_connection(
                armed.org_a, armed.c["b_member"])
        assert found is not None, "the lenient read found org B's row"
        assert found.organization_id == armed.org_b, "the ROW's org, not the argument"

    async def test_a_signed_org_that_is_not_the_rows_is_refused(self, armed, monkeypatch):
        self._lenient(monkeypatch)
        # Control: signed as its own org, the connection works on this path.
        ok = await _agent(armed, armed.c["b_member"], "/auth/me",
                          org=armed.org_b, dsn=armed.admin_dsn)
        assert ok.status_code == 200, ok.text
        assert ok.json()["email"] == B_MEMBER
        # Signed as org A: the row and the member both say org B.
        r = await _agent(armed, armed.c["b_member"], "/auth/me",
                         org=armed.org_a, dsn=armed.admin_dsn)
        assert r.status_code == 401, r.text

    async def test_only_the_row_org_compare_refuses_a_member_who_moved(self, armed, monkeypatch):
        """Fix round 2, S4. The member HAS moved into org A, the signed org.
        The connection ROW still says org B. Under the lenient read as the
        superuser, the seat check would pass (member org A == signed org A),
        so the ONE check left to refuse is the row's organization against the
        signed one. Reverting the read to report its argument turns this red."""
        self._lenient(monkeypatch)
        async with tenant_engine_scope(armed.admin_dsn):
            found = await agent_tokens.read_live_connection(
                armed.org_a, armed.c["mover_row_b"])
        assert found is not None and found.organization_id == armed.org_b
        assert found.member_email == MOVER
        r = await _agent(armed, armed.c["mover_row_b"], "/auth/me",
                         org=armed.org_a, dsn=armed.admin_dsn)
        assert r.status_code == 401, r.text
