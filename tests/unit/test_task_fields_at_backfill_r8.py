"""WS-55 MCP-G7 — the backfill of migration ``*_pm_tasks_fields_at.sql`` under
FORCE ROW LEVEL SECURITY, in the shape of production.

Spec: ``project-docs/specs/project_management_app.md`` §9.10.3.

**The trap.** Production's migration runner connects as the OWNER of
``pm_tasks``. It is not a superuser and has no BYPASSRLS, and FORCE binds the
owner too. With no tenant bound, the policy shows that role no row. So a bare
``UPDATE pm_tasks SET fields_at = updated_at`` backfilled NOTHING, and every
task that existed before the migration kept a NULL ``fields_at``. 221 and 297
record the same trap, and the verifier of fix round 2 measured it for this file.

**The shape here.** A private database gets the whole ladder EXCEPT this
migration, then the four ``generated/`` phases, so ``pm_tasks`` carries FORCE
RLS. Two tenants each get a task. A ``NOSUPERUSER NOBYPASSRLS`` role then owns
``pm_tasks`` and runs the migration, as the production runner does. The tests
check that each tenant's task gets ``fields_at``, that ``updated_at`` does not
move, and that a comment then a PATCH with the token from before the migration
passes. The app connects as the same role, so FORCE binds every read too.

Run on a PRIVATE database (``scripts/dev_db.sh``)::

    TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/acb_g7 \\
        uv run pytest tests/unit/test_task_fields_at_backfill_r8.py -v -rs

Mutation F1: replace the per-tenant ``DO`` block of the migration with the bare
``UPDATE pm_tasks SET fields_at = updated_at WHERE fields_at IS NULL``. Then
``test_the_backfill_reaches_each_tenant_under_force`` and
``test_a_comment_then_a_patch_with_the_old_token_passes`` go red.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("gateway.routes.projects", reason="gateway not installed")

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests.unit._tenant_ladder import INIT_SCHEMA, _exec_file, ladder
from tests.unit.test_agent_runs_r8 import _principal, _seed_task, _session
from tests.unit.test_h3_rls_promotion_rehearsal import (
    _DB_GATE,
    _URL,
    _apply_generated_phase,
)

pytestmark = _DB_GATE

_MIGRATION = next(
    (Path(__file__).resolve().parents[2] / "infra" / "postgres").glob(
        "*_pm_tasks_fields_at.sql"))

#: Suite-private, as ``test_h3_rls_promotion_rehearsal`` explains: a role is
#: cluster-wide, so a shared name would clobber another suite's password.
_OWNER_ROLE = "acb_g7_fill_owner"
_OWNER_PW = "g7_fill_scratch_pw"

ME = "g7-fill@fields-at.test"
OTHER = "g7-fill-other@fields-at.test"


def _drop(admin_url: Any, name: str) -> None:
    with create_engine(admin_url, isolation_level="AUTOCOMMIT").connect() as c:
        c.execute(text(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = :d AND pid <> pg_backend_pid()"), {"d": name})
        c.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))


@pytest.fixture(scope="module")
def before():
    """A promoted catalog WITHOUT this migration, a task in each of two
    tenants, and a NOSUPERUSER NOBYPASSRLS owner of ``pm_tasks``."""
    admin_url = make_url(_URL)
    dedicated = f"{admin_url.database}_g7fill"
    _drop(admin_url, dedicated)
    with create_engine(admin_url, isolation_level="AUTOCOMMIT").connect() as c:
        c.execute(text(f'CREATE DATABASE "{dedicated}"'))
    ded_url = admin_url.set(database=dedicated)
    eng = create_engine(ded_url, future=True)
    with eng.begin() as conn:
        _exec_file(conn, INIT_SCHEMA)
        for path in ladder():
            if Path(path).name != _MIGRATION.name:
                _exec_file(conn, path)
    for phase in ("01_add_columns.sql", "02_backfill.sql",
                  "03_constraints.sql", "04_policies.sql"):
        with eng.begin() as conn:
            _apply_generated_phase(conn, phase)

    with create_engine(ded_url, isolation_level="AUTOCOMMIT").connect() as c:
        c.execute(text(
            f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE "
            f"rolname='{_OWNER_ROLE}') THEN CREATE ROLE {_OWNER_ROLE} LOGIN; "
            f"END IF; END $$;"))
        c.execute(text(
            f"ALTER ROLE {_OWNER_ROLE} WITH LOGIN PASSWORD '{_OWNER_PW}' "
            f"NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS INHERIT"))
        c.execute(text(f'GRANT CONNECT ON DATABASE "{dedicated}" TO {_OWNER_ROLE}'))
        # CREATE, so the owner can write the function of the migration.
        c.execute(text(f"GRANT USAGE, CREATE ON SCHEMA public TO {_OWNER_ROLE}"))
        c.execute(text("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES "
                       f"IN SCHEMA public TO {_OWNER_ROLE}"))
        c.execute(text("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public "
                       f"TO {_OWNER_ROLE}"))
        c.execute(text(f"ALTER TABLE pm_tasks OWNER TO {_OWNER_ROLE}"))

    org_a, org_b = str(uuid.uuid4()), str(uuid.uuid4())
    with eng.begin() as c:
        for oid, slug in ((org_a, "g7fill-a"), (org_b, "g7fill-b")):
            c.execute(text("INSERT INTO organization (id, slug, display_name) "
                           "VALUES (:id, :slug, :slug)"), {"id": oid, "slug": slug})
    ns = SimpleNamespace(
        admin_engine=eng, org_a=org_a, org_b=org_b,
        app_url=admin_url.set(database=dedicated, username=_OWNER_ROLE,
                              password=_OWNER_PW),
    )
    ns.task_a = _seed_task(ns, org_a, ME, grant=ME)
    ns.task_b = _seed_task(ns, org_b, ME, grant=ME)
    with eng.begin() as c:
        c.execute(text(
            "INSERT INTO app_user (email, display_name, role, status, organization_id) "
            "VALUES (:e, :e, 'employee', 'active', CAST(:o AS uuid)) "
            "ON CONFLICT DO NOTHING"), {"e": OTHER, "o": org_a})
        ns.stamps = {str(r.id): r.updated_at for r in c.execute(text(
            "SELECT id, updated_at FROM pm_tasks"))}
    yield ns

    eng.dispose()
    _drop(admin_url, dedicated)


@pytest.fixture(scope="module")
def migrated(before):
    """The migration, run ONCE by the owner role, as production runs it."""
    owner = create_engine(before.app_url, future=True)
    try:
        with owner.begin() as conn:
            _exec_file(conn, str(_MIGRATION))
    finally:
        owner.dispose()
    return before


def _row(p: Any, task: str) -> Any:
    with p.admin_engine.connect() as c:
        return c.execute(text(
            "SELECT updated_at, fields_at FROM pm_tasks WHERE id = CAST(:t AS uuid)"),
            {"t": task}).one()


def test_the_shape_is_production(before):
    """The precondition, checked rather than assumed. FORCE binds the owner,
    the owner is no superuser and has no BYPASSRLS, and with no tenant bound
    it sees no task. A bare UPDATE by this role could backfill nothing."""
    with before.admin_engine.connect() as c:
        forced = c.execute(text(
            "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class "
            "WHERE oid = 'pm_tasks'::regclass")).scalar()
        role = c.execute(text(
            "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = :r"),
            {"r": _OWNER_ROLE}).one()
        owner = c.execute(text(
            "SELECT pg_get_userbyid(relowner) FROM pg_class "
            "WHERE oid = 'pm_tasks'::regclass")).scalar()
        column = c.execute(text(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name = 'pm_tasks' AND column_name = 'fields_at'")).scalar()
    assert forced is True
    assert tuple(role) == (False, False)
    assert owner == _OWNER_ROLE
    assert column == 0, "the migration ran before this test"
    eng = create_engine(before.app_url, future=True)
    try:
        with eng.connect() as c:
            assert c.execute(text("SELECT count(*) FROM pm_tasks")).scalar() == 0
    finally:
        eng.dispose()


def test_the_backfill_reaches_each_tenant_under_force(migrated):
    for task in (migrated.task_a, migrated.task_b):
        row = _row(migrated, task)
        assert row.fields_at is not None, task
        assert row.fields_at == row.updated_at, task
        # The backfill does not SET updated_at, so no token moved.
        assert row.updated_at == migrated.stamps[task], task


def test_a_second_run_changes_nothing(migrated):
    first = {t: tuple(_row(migrated, t)) for t in (migrated.task_a, migrated.task_b)}
    owner = create_engine(migrated.app_url, future=True)
    try:
        with owner.begin() as conn:
            _exec_file(conn, str(_MIGRATION))
    finally:
        owner.dispose()
    assert {t: tuple(_row(migrated, t)) for t in first} == first


async def test_a_comment_then_a_patch_with_the_old_token_passes(migrated):
    """The token a client held BEFORE the migration ran. A comment moves
    ``updated_at``, and the backfilled ``fields_at`` keeps the token good."""
    p = migrated
    token = p.stamps[p.task_a].isoformat()
    me, other = _principal(ME, p.org_a), _principal(OTHER, p.org_a)
    async with _session(p) as call:
        said = await call(other, "POST", f"/projects/tasks/{p.task_a}/comments",
                          {"body": "Checked the extruder."})
        assert said.status_code == 201, said.text
        key = str(id(me))
        call.users[key] = me
        late = await call.client.request(
            "PATCH", f"/projects/tasks/{p.task_a}", json={"title": "Fixed"},
            headers={"x-test-as": key, "If-Match": token})
    assert late.status_code == 200, late.text
    assert late.json()["title"] == "Fixed"
