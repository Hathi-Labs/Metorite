"""H-304 — an org-wide reader does NOT see a SEALED personal tree (D63).

Decision D63 (``work_plan.md`` §3): the company is entitled to the work, not
to the workspace. When a member leaves, their personal tree is sealed
(migration 215, ``pm_projects.sealed_at``). Two rules follow:

* a task in the tree that was assigned OUTWARD is handed over. Its assignee
  keeps it, through the assignee arm of ``task_visibility_clause``;
* everything else in the tree is invisible to EVERYBODY, the
  ``data:org:read`` admin included, until the owner-only logged door opens it.

Until H-304, the unrestricted branch of ``task_visibility_clause`` was the
tenant alone, so an admin read the whole sealed tree through the task list,
the task read, search and the ``#N`` refs. The project clause already filtered
the seal.

**R8.** Each test runs the REAL Projects routes as a NON-privileged role on a
phase-4-promoted catalog of its own, so FORCE RLS binds every read. Two
organizations. Org A holds:

* LEAVER — deactivated. Their personal tree (a root and an Area) is sealed. It
  holds a private task, a task assigned outward to ASSIGNEE, and a task
  assigned outward to ADMIN;
* ADMIN — ``data:org:read``;
* ASSIGNEE — a plain member;
* PEER — a live member with a live personal tree, and a team project.

Org B holds its own admin and a task.

The bypass callers that MUST see a sealed tree are named at the bottom, and
each one is shown to still see it: ``seal_counts`` (the off-boarding dialog)
and ``unseal_personal_tree`` (the reactivation door). Neither reads through
the visibility clause.

The gate is ``TENANT_LADDER_DATABASE_URL``. A skip is not a pass. Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_sealed_tree_visibility_r8.py -v -rs
"""
from __future__ import annotations

import asyncio
import os
import re
import uuid
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import access as access_module
from acb_auth import require_authenticated
from acb_auth.access import invalidate as invalidate_access
from fastapi import FastAPI
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

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
        "pgvector. A skip here is not a pass; CI must set it."
    ),
)

_APP_ROLE = "acb_app_sealedtree"
_APP_PW = "sealedtree_scratch_pw"
_GENERATED = os.path.join(_LADDER_DIR, "generated")

INTERNAL = "sealed-tree-internal-token"
DOMAIN = "sealedtree.test"
LEAVER = f"leaver@{DOMAIN}"
ADMIN = f"admin@{DOMAIN}"
ASSIGNEE = f"assignee@{DOMAIN}"
PEER = f"peer@{DOMAIN}"
B_ADMIN = f"b-admin@{DOMAIN}"
#: A word in every task title, so one search reaches every task.
WORD = "quokka"


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
    """Admin writes, each with the tenant GUC set, so every tenant column
    default stamps the right organization."""

    def __init__(self, engine, org: str, base: int) -> None:
        self.engine = engine
        self.org = org
        self.number = base

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
            "INSERT INTO pm_projects (name, status, source, created_by,"
            " organization_id, timezone, parent_project_id, owns_statuses,"
            " personal_owner) VALUES (:n, 'active', 'manual', :a,"
            " CAST(:o AS uuid), 'Asia/Kolkata', CAST(:parent AS uuid), true,"
            " :po) RETURNING id",
            n=name, a=personal_owner or PEER, o=self.org, parent=parent,
            po=personal_owner))
        if grant_to:
            self.one(
                "INSERT INTO pm_project_grants (project_id, subject, created_by,"
                " organization_id) VALUES (CAST(:p AS uuid), :s, :a,"
                " CAST(:o AS uuid)) RETURNING project_id",
                p=pid, s=grant_to, a=PEER, o=self.org)
        return pid

    def task(self, project: str, root: str, title: str, *,
             assignee: str | None = None) -> tuple[str, int]:
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
            t=f"{WORD} {title}", p=project, r=root, s=str(status), a=PEER,
            o=self.org, n=self.number))
        if assignee:
            self.one(
                "INSERT INTO pm_task_assignees (task_id, assignee, assigned_by,"
                " organization_id) VALUES (CAST(:t AS uuid), :e, :a,"
                " CAST(:o AS uuid)) RETURNING task_id",
                t=tid, e=assignee, a=PEER, o=self.org)
        return tid, self.number

    def member(self, email: str, permissions: list[str],
               status: str = "active") -> None:
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
            o=self.org, s="st-" + email.split("@")[0])
        for permission in permissions:
            self.one(
                "INSERT INTO org_role_permission (role_id, permission)"
                " VALUES (CAST(:r AS uuid), :p) RETURNING role_id",
                r=str(role), p=permission)
        self.one(
            "INSERT INTO user_role (user_id, role_id) VALUES (CAST(:u AS uuid),"
            " CAST(:r AS uuid)) RETURNING user_id", u=str(app_user), r=str(role))
        if status != "active":
            self.one(
                "UPDATE app_user SET status = :s WHERE id = CAST(:u AS uuid)"
                " RETURNING id", s=status, u=str(app_user))


@pytest.fixture(scope="module")
def world():
    """A private promoted catalog, two tenants, and one sealed tree."""
    admin_url = make_url(_URL)
    dedicated = f"{admin_url.database}_sealedtree"
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

    org_a = str(uuid.uuid4())
    org_b = str(uuid.uuid4())
    with eng.begin() as c:
        for oid, slug in ((org_a, "sealedtree-a"), (org_b, "sealedtree-b")):
            c.execute(text("INSERT INTO organization (id, slug, display_name) "
                           "VALUES (:id, :slug, :slug)"), {"id": oid, "slug": slug})

    a = _Seeder(eng, org_a, 0)
    b = _Seeder(eng, org_b, 0)
    reader = ["feature:projects", "data:org:read"]
    a.member(ADMIN, reader)
    a.member(ASSIGNEE, ["feature:projects"])
    a.member(PEER, ["feature:projects"])
    a.member(LEAVER, ["feature:projects"])
    b.member(B_ADMIN, reader)

    w = SimpleNamespace(admin=eng, org_a=org_a, org_b=org_b, p={}, t={}, n={})
    p = w.p

    def task(key: str, seeder: _Seeder, project: str, root: str, title: str,
             assignee: str | None = None) -> None:
        w.t[key], w.n[key] = seeder.task(project, root, title, assignee=assignee)

    # The live part of org A: a team project and a live personal tree.
    p["team"] = a.project("Team", grant_to="org")
    p["peer_root"] = a.project("Peer root", personal_owner=PEER, grant_to=PEER)
    task("team", a, p["team"], p["team"], "team task")
    task("peer_private", a, p["peer_root"], p["peer_root"], "peer private")

    # The leaver's tree: a root and an Area beneath it, both personal.
    p["sealed_root"] = a.project("Leaver root", personal_owner=LEAVER,
                                 grant_to=LEAVER)
    p["sealed_area"] = a.project("Leaver area", parent=p["sealed_root"],
                                 personal_owner=LEAVER)
    task("sealed_private", a, p["sealed_root"], p["sealed_root"],
         "leaver private")
    task("sealed_area", a, p["sealed_area"], p["sealed_root"], "leaver area")
    task("handover", a, p["sealed_root"], p["sealed_root"],
         "handed over to the assignee", assignee=ASSIGNEE)
    task("handover_admin", a, p["sealed_area"], p["sealed_root"],
         "handed over to the admin", assignee=ADMIN)

    # Org B: its own tree, numbered in the SAME range as org A.
    p["b_root"] = b.project("Org B", grant_to="org")
    task("b", b, p["b_root"], p["b_root"], "org B task")

    # The seal, as D63's door writes it: the leaver is no longer active, and
    # the tree is stamped by `seal_personal_tree` itself.
    from gateway.routes.projects.seal import seal_personal_tree

    async def _seal() -> int:
        from sqlalchemy.ext.asyncio import create_async_engine

        url = ded_admin_url.set(drivername="postgresql+asyncpg")
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                return await seal_personal_tree(conn, LEAVER, org_a)
        finally:
            await engine.dispose()

    assert asyncio.run(_seal()) == 2, "the root and the Area are both sealed"
    a.one("UPDATE app_user SET status = 'removed' WHERE lower(email) = :e"
          " RETURNING id", e=LEAVER)
    # The delta feed reads nothing newer than `now()` minus its horizon
    # (`delta.HORIZON_LAG_SECONDS`). A fast runner reaches the delta test
    # inside that window, and the feed is then empty. Put every task an hour
    # back, so the delta test does not depend on the speed of the runner.
    with eng.begin() as c:
        c.execute(text("UPDATE pm_tasks SET updated_at = now() - interval '1 hour'"
                       " WHERE organization_id = ANY(CAST(:o AS uuid[]))"),
                  {"o": [org_a, org_b]})

    w.app_dsn = admin_url.set(
        database=dedicated, username=_APP_ROLE, password=_APP_PW,
    ).render_as_string(hide_password=False)
    w.sealed = {w.t["sealed_private"], w.t["sealed_area"], w.t["handover"],
                w.t["handover_admin"]}
    yield w

    eng.dispose()
    _drop_database(admin_url, dedicated)


@pytest.fixture
def armed(monkeypatch, world):
    monkeypatch.setenv("GATEWAY_INTERNAL_TOKEN", INTERNAL)
    monkeypatch.setenv("IDENTITY_CUTOVER", "1")
    monkeypatch.setenv("ALLOWED_EMAIL_DOMAIN", DOMAIN)
    monkeypatch.delenv("GATEWAY_TRUST_UNVERIFIED_SSO_HEADERS", raising=False)
    # A process-wide latch an earlier suite can set. See the same lines in
    # `test_mcp_edge_authority.py`.
    monkeypatch.setattr(access_module, "_tables_missing", False)
    invalidate_access()
    try:
        yield world
    finally:
        invalidate_access()


def _gateway() -> FastAPI:
    from gateway.routes.projects import router as projects_router

    app = FastAPI(dependencies=[require_authenticated(public=frozenset())])
    app.include_router(projects_router)
    return app


async def _get(world, email: str, path: str) -> httpx.Response:
    headers = {"Authorization": f"Bearer {INTERNAL}", "X-User-Email": email}

    async def _go() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=_gateway()), base_url="http://gw",
        ) as client:
            return await client.get(path, headers=headers)

    async with tenant_engine_scope(world.app_dsn):
        return await asyncio.create_task(_go())


async def _ok(world, email: str, path: str) -> Any:
    r = await _get(world, email, path)
    assert r.status_code == 200, (email, path, r.status_code, r.text)
    return r


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


async def _listed(world, email: str) -> set[str]:
    body = (await _ok(world, email, "/projects/tasks?page_size=100")).json()
    rows = {row["id"] for row in body["rows"]}
    assert int(body["total"]) == len(rows), "the total counts what the rows show"
    return rows


async def _searched(world, email: str) -> set[str]:
    body = (await _ok(world, email, f"/projects/search?q={WORD}&limit=50")).json()
    return {row["id"] for row in body["rows"]}


async def _refs(world, email: str) -> set[str]:
    numbers = ",".join(str(n) for n in sorted(set(world.n.values())))
    body = (await _ok(world, email, f"/projects/search/refs?numbers={numbers}")).json()
    return {row["id"] for row in body["rows"]}


# ═══════════════════════════════════════════════════════════════════════════
# The admin with `data:org:read`
# ═══════════════════════════════════════════════════════════════════════════


@_DB_GATE
class TestTheAdminDoesNotReadTheSealedTree:
    """Every read that goes through the one clause hides the tree, except
    the task that was handed over to the admin."""

    @pytest.mark.parametrize("read", ["list", "search", "refs"])
    async def test_a_list_read_hides_every_sealed_task_but_the_admins_own(
            self, armed, read):
        rows = await {"list": _listed, "search": _searched, "refs": _refs}[read](
            armed, ADMIN)
        t = armed.t
        assert rows & armed.sealed == {t["handover_admin"]}, (
            "the admin reads the sealed tree, or lost its own hand-over")
        assert {t["team"], t["peer_private"]} <= rows, (
            "a live tree must stay readable to the admin")
        assert t["b"] not in rows, "never another tenant's task"

    @pytest.mark.parametrize("key", ["sealed_private", "sealed_area", "handover"])
    async def test_the_task_read_is_a_404(self, armed, key):
        r = await _get(armed, ADMIN, f"/projects/tasks/{armed.t[key]}")
        missing = await _get(
            armed, ADMIN, "/projects/tasks/00000000-0000-4000-8000-000000000000")
        assert r.status_code == 404, (key, r.text)
        assert r.json() == missing.json(), "one not-found text"

    async def test_the_admins_own_hand_over_task_reads(self, armed):
        await _ok(armed, ADMIN, f"/projects/tasks/{armed.t['handover_admin']}")

    async def test_the_csv_export_holds_no_sealed_title(self, armed):
        csv = (await _ok(armed, ADMIN, "/projects/export/tasks.csv")).text
        assert "leaver private" not in csv
        assert "leaver area" not in csv
        assert "handed over to the assignee" not in csv
        assert "handed over to the admin" in csv
        assert "team task" in csv

    async def test_the_delta_feed_hides_the_sealed_tree(self, armed):
        body = (await _ok(armed, ADMIN, "/projects/delta/tasks")).json()
        rows = {row["id"] for row in body["rows"]}
        assert rows & armed.sealed == {armed.t["handover_admin"]}
        assert armed.t["team"] in rows

    async def test_no_sealed_project_lists_or_reads(self, armed):
        p = armed.p
        sealed = {p["sealed_root"], p["sealed_area"]}
        for path in ("/projects/tree", "/projects/nodes", "/projects/summary"):
            body = (await _ok(armed, ADMIN, path)).json()
            assert not (_ids(body) & sealed), path
        for project in sealed:
            r = await _get(armed, ADMIN, f"/projects/nodes/{project}")
            assert r.status_code == 404, r.text
            r = await _get(armed, ADMIN, f"/projects/tasks?project_id={project}")
            assert r.status_code == 404, r.text
        r = await _get(armed, ADMIN, f"/projects/nodes/{p['team']}")
        assert r.status_code == 200, r.text


# ═══════════════════════════════════════════════════════════════════════════
# The hand-over, and the plain members
# ═══════════════════════════════════════════════════════════════════════════


@_DB_GATE
class TestTheHandOverSurvivesTheSeal:
    """D63: the company keeps the work. The assignee keeps the task."""

    async def test_the_assignee_lists_and_reads_the_outward_task(self, armed):
        t = armed.t
        for read in (_listed, _searched, _refs):
            rows = await read(armed, ASSIGNEE)
            assert rows & armed.sealed == {t["handover"]}, read.__name__
        await _ok(armed, ASSIGNEE, f"/projects/tasks/{t['handover']}")

    async def test_a_member_with_no_tie_sees_nothing_of_the_tree(self, armed):
        rows = await _listed(armed, PEER)
        assert not (rows & armed.sealed)
        assert {armed.t["team"], armed.t["peer_private"]} <= rows


@_DB_GATE
class TestTheTenantHolds:
    async def test_the_other_tenants_admin_sees_nothing_of_org_a(self, armed):
        a_tasks = set(armed.t.values()) - {armed.t["b"]}
        for read in (_listed, _searched, _refs):
            rows = await read(armed, B_ADMIN)
            assert rows == {armed.t["b"]}, (read.__name__, rows & a_tasks)
        r = await _get(armed, B_ADMIN, f"/projects/tasks/{armed.t['handover_admin']}")
        assert r.status_code == 404


@_DB_GATE
class TestTheSuiteIsHonest:
    async def test_the_suite_runs_as_a_role_that_cannot_bypass_rls(self, armed):
        engine = create_engine(make_url(armed.app_dsn), future=True)
        try:
            with engine.connect() as c:
                row = c.execute(text(
                    "SELECT rolbypassrls, rolsuper FROM pg_roles"
                    " WHERE rolname = current_user")).one()
        finally:
            engine.dispose()
        assert (row.rolbypassrls, row.rolsuper) == (False, False)

    async def test_the_tree_is_really_sealed_and_really_there(self, armed):
        with armed.admin.connect() as c:
            rows = c.execute(text(
                "SELECT count(*) FILTER (WHERE p.sealed_at IS NOT NULL) AS sealed,"
                " count(*) AS tasks FROM pm_tasks t"
                " JOIN pm_projects p ON p.id = t.project_id"
                " WHERE t.id = ANY(CAST(:ids AS uuid[]))"),
                {"ids": sorted(armed.sealed)}).one()
        assert (rows.sealed, rows.tasks) == (4, 4), "nothing was deleted"


# ═══════════════════════════════════════════════════════════════════════════
# The bypass callers: they MUST still see the sealed tree, and say so
# ═══════════════════════════════════════════════════════════════════════════


async def _as_app(world, org: str, fn):
    from sqlalchemy.ext.asyncio import create_async_engine

    url = make_url(world.app_dsn).set(drivername="postgresql+asyncpg")
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"),
                               {"t": org})
            return await fn(conn)
    finally:
        await engine.dispose()


@_DB_GATE
class TestTheBypassDoorsStillSeeTheTree:
    """``projects/seal.py`` is the off-boarding door. It reads the sealed tree
    by its own ``_TREE_SQL`` and never through the visibility clause, which is
    what lets the dialog state the split in numbers."""

    async def test_seal_counts_still_counts_the_sealed_tree(self, armed):
        from gateway.routes.projects.seal import seal_counts

        counts = await _as_app(
            armed, armed.org_a, lambda db: seal_counts(db, LEAVER, armed.org_a))
        assert counts == {"projects": 2, "tasks": 4, "handed_over": 2,
                          "sealed": 2}

    async def test_the_reactivation_door_still_reaches_the_tree(self, armed):
        from gateway.routes.projects.seal import (
            seal_personal_tree,
            unseal_personal_tree,
        )

        async def _round_trip(db) -> tuple[int, int]:
            opened = await unseal_personal_tree(db, LEAVER, armed.org_a)
            closed = await seal_personal_tree(db, LEAVER, armed.org_a)
            return opened, closed

        assert await _as_app(armed, armed.org_a, _round_trip) == (2, 2)


class TestTheBypassDoorsAreExplicit:
    """Database-free, so it runs in every CI job."""

    def test_the_doors_do_not_read_through_the_visibility_clause(self):
        """The explicit part. A door that read through the clause would see
        nothing of the tree after H-304, and its numbers would silently read 0."""
        from pathlib import Path

        source = (Path(__file__).resolve().parents[2]
                  / "apps/services/gateway/gateway/routes/projects/seal.py"
                  ).read_text(encoding="utf-8")
        code = re.sub(r'""".*?"""', "", source, flags=re.S)
        code = re.sub(r"#.*", "", code)
        for name in ("task_visibility_clause", "project_clause",
                     "resolve_visibility", "load_visible_"):
            assert name not in code, name
