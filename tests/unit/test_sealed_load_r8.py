"""H-305 — a departed member's load does not count their SEALED tree (D63).

Owner answer, 2026-10-13: *"Stop counting them."* ``people/core.py::
compute_load`` counts the open tasks and hours one person holds. It feeds
three surfaces, and all three call it: the person record (``person_payload``),
people search and the Projects assignee picker. So one predicate fixes all
three.

The rule, in D63's own terms:

* a task in a sealed tree that is assigned to the tree's OWNER stops counting.
  The workspace is closed, and nobody is holding that work;
* a task in a sealed tree that was handed over to a colleague still counts for
  that colleague. The work is theirs now;
* nothing else changes.

**R8.** A private phase-4-promoted catalog, read as a role that cannot bypass
RLS, with the tenant GUC bound the way a request binds it. Two organizations
hold the SAME addresses, so a fence that leaks across tenants shows up as a
wrong number.

The gate is ``TENANT_LADDER_DATABASE_URL``. A skip is not a pass. Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_sealed_load_r8.py -v -rs
"""
from __future__ import annotations

import asyncio
import os
import re
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests.unit._tenant_ladder import _LADDER_DIR, apply_ladder

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

_APP_ROLE = "acb_app_sealedload"
_APP_PW = "sealedload_scratch_pw"
_GENERATED = os.path.join(_LADDER_DIR, "generated")

LEAVER = "leaver@sealedload.test"
ASSIGNEE = "assignee@sealedload.test"
PEER = "peer@sealedload.test"


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
    """Admin writes, each with the tenant GUC set."""

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
                personal_owner: str | None = None) -> str:
        return str(self.one(
            "INSERT INTO pm_projects (name, status, source, created_by,"
            " organization_id, timezone, parent_project_id, owns_statuses,"
            " personal_owner) VALUES (:n, 'active', 'manual', 'fixture',"
            " CAST(:o AS uuid), 'Asia/Kolkata', CAST(:parent AS uuid), true,"
            " :po) RETURNING id",
            n=name, o=self.org, parent=parent, po=personal_owner))

    def task(self, project: str, root: str, *, assignees: tuple[str, ...],
             minutes: int | None, category: str = "in_progress") -> str:
        status = self.one(
            "SELECT id FROM pm_task_statuses WHERE project_id = CAST(:p AS uuid)"
            " AND category = :c LIMIT 1", p=root, c=category)
        if status is None:
            status = self.one(
                "INSERT INTO pm_task_statuses (project_id, name, color, position,"
                " category, organization_id) VALUES (CAST(:p AS uuid), :c,"
                " 'gray', 0, :c, CAST(:o AS uuid)) RETURNING id",
                p=root, c=category, o=self.org)
        self.number += 1
        tid = str(self.one(
            "INSERT INTO pm_tasks (title, project_id, root_project_id, status_id,"
            " created_by, organization_id, task_number, estimate_mins)"
            " VALUES ('work', CAST(:p AS uuid), CAST(:r AS uuid),"
            " CAST(:s AS uuid), 'fixture', CAST(:o AS uuid), :n, :m)"
            " RETURNING id",
            p=project, r=root, s=str(status), o=self.org, n=self.number,
            m=minutes))
        for who in assignees:
            self.one(
                "INSERT INTO pm_task_assignees (task_id, assignee, assigned_by,"
                " organization_id) VALUES (CAST(:t AS uuid), :e, 'fixture',"
                " CAST(:o AS uuid)) RETURNING task_id",
                t=tid, e=who, o=self.org)
        return tid


def _seed_org(a: _Seeder, *, seal: bool) -> None:
    """The same shape in each org, so a cross-tenant leak changes a number.

    LEAVER's personal tree (a root and an Area):
      * 2 private tasks: 60 min, and one with no estimate;
      * 1 task in the Area: 30 min;
      * 1 task handed over to ASSIGNEE, 45 min;
      * 1 task held by BOTH LEAVER and ASSIGNEE, 15 min;
      * 1 closed task, 999 min, which never counts.
    LEAVER also holds 1 team task (120 min), which is the company's work and
    keeps counting. ASSIGNEE holds 1 team task (10 min). PEER holds 1 task in
    their own live personal tree (20 min).
    """
    # A personal root is unique per ADDRESS across the deployment
    # (`uq_pm_projects_personal_root`), so org B holds the same tasks in team
    # projects. Its numbers are still the full, unsealed count.
    leaver = LEAVER if seal else None
    team = a.project("Team")
    root = a.project("Leaver root", personal_owner=leaver)
    area = a.project("Leaver area", parent=root, personal_owner=leaver)
    peer = a.project("Peer root", personal_owner=PEER if seal else None)
    a.task(root, root, assignees=(LEAVER,), minutes=60)
    a.task(root, root, assignees=(LEAVER,), minutes=None)
    a.task(area, root, assignees=(LEAVER,), minutes=30)
    a.task(root, root, assignees=(ASSIGNEE,), minutes=45)
    a.task(area, root, assignees=(LEAVER, ASSIGNEE), minutes=15)
    a.task(root, root, assignees=(LEAVER,), minutes=999, category="done")
    a.task(team, team, assignees=(LEAVER,), minutes=120)
    a.task(team, team, assignees=(ASSIGNEE,), minutes=10)
    a.task(peer, peer, assignees=(PEER,), minutes=20)
    if seal:
        a.one(
            "UPDATE pm_projects SET sealed_at = now()"
            " WHERE lower(personal_owner) = :e RETURNING id", e=LEAVER)


@pytest.fixture(scope="module")
def world():
    """Org A has the leaver's tree SEALED. Org B holds the same tasks, LIVE."""
    admin_url = make_url(_URL)
    dedicated = f"{admin_url.database}_sealedload"
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

    org_a = str(uuid.uuid4())
    org_b = str(uuid.uuid4())
    with eng.begin() as c:
        for oid, slug in ((org_a, "sealedload-a"), (org_b, "sealedload-b")):
            c.execute(text("INSERT INTO organization (id, slug, display_name) "
                           "VALUES (:id, :slug, :slug)"), {"id": oid, "slug": slug})
    _seed_org(_Seeder(eng, org_a), seal=True)
    _seed_org(_Seeder(eng, org_b), seal=False)

    w = SimpleNamespace(
        admin=eng, org_a=org_a, org_b=org_b,
        app_url=admin_url.set(database=dedicated, username=_APP_ROLE,
                              password=_APP_PW,
                              drivername="postgresql+asyncpg"),
    )
    yield w
    eng.dispose()
    _drop_database(admin_url, dedicated)


def _load(world, org: str, email: str) -> dict[str, Any]:
    """``compute_load`` as a request runs it: the app role, the GUC bound."""
    from gateway.routes.people.core import compute_load
    from sqlalchemy.ext.asyncio import create_async_engine

    async def _go() -> dict[str, Any]:
        engine = create_async_engine(world.app_url)
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text("SELECT set_config('app.tenant_id', :t, true)"),
                    {"t": org})
                return await compute_load(conn, email)
        finally:
            await engine.dispose()

    return asyncio.run(_go())


#: Org B's tree is LIVE, so its numbers are the "before" of the seal.
LIVE = {
    LEAVER: {"open_tasks": 5, "estimated_hours": 3.8, "unestimated": 1},
    ASSIGNEE: {"open_tasks": 3, "estimated_hours": 1.2, "unestimated": 0},
    PEER: {"open_tasks": 1, "estimated_hours": 0.3, "unestimated": 0},
}


@_DB_GATE
class TestTheSealedLoad:
    def test_the_leaver_stops_counting_their_sealed_tree(self, world):
        """Only the team task is left: 120 min, no unestimated task."""
        assert _load(world, world.org_a, LEAVER) == {
            "open_tasks": 1, "estimated_hours": 2.0, "unestimated": 0}

    def test_the_hand_over_still_counts_for_the_new_assignee(self, world):
        """The handed-over task (45), the shared one (15), and the team task."""
        assert _load(world, world.org_a, ASSIGNEE) == LIVE[ASSIGNEE]

    def test_a_member_with_no_tie_is_unchanged(self, world):
        assert _load(world, world.org_a, PEER) == LIVE[PEER]

    @pytest.mark.parametrize("email", [LEAVER, ASSIGNEE, PEER])
    def test_the_other_tenant_holds_its_own_live_numbers(self, world, email):
        """Org B holds the same tasks for the same addresses, not sealed. Its
        numbers are the full count, and org A's rows never reach them."""
        assert _load(world, world.org_b, email) == LIVE[email]

    def test_the_suite_runs_as_a_role_that_cannot_bypass_rls(self, world):
        engine = create_engine(
            world.app_url.set(drivername="postgresql+psycopg"), future=True)
        try:
            with engine.connect() as c:
                row = c.execute(text(
                    "SELECT rolbypassrls, rolsuper FROM pg_roles"
                    " WHERE rolname = current_user")).one()
        finally:
            engine.dispose()
        assert (row.rolbypassrls, row.rolsuper) == (False, False)


def test_every_surface_reads_the_one_load_function():
    """Database-free. The person record, people search and the assignee
    picker each call ``compute_load``, so its one predicate binds all three.
    A surface that wrote its own count would skip the seal."""
    root = Path(__file__).resolve().parents[2] / "apps/services/gateway/gateway/routes"
    for path in ("people/core.py", "people/search.py", "projects/assignees.py"):
        source = (root / path).read_text(encoding="utf-8")
        assert re.search(r"await compute_load\(", source), path
