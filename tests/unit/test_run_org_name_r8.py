"""R8: the run's organisation name, read from a real promoted catalog.

``executor._read_org_display_name`` reads ``organization.display_name`` by
id. This suite runs it on a dedicated database with the whole ladder and
the phase-4 policies applied, as a role with NOBYPASSRLS. So it proves the
SQL, and it proves the claim that ``organization`` has no row policy: an
app role reads each org's name with no tenant bound.

Mutations this file catches (R7): a wrong column or cast in the read, a row
policy on ``organization`` that hides the name, and a read that returns the
name of another org.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from tests.unit._tenant_ladder import apply_ladder  # noqa: E402

_ROOT = Path(__file__).resolve().parents[2]
_GENERATED = _ROOT / "infra" / "postgres" / "generated"

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
pytestmark = _DB_GATE

#: Suite-private role (a role is cluster-wide, so a shared name clobbers).
_APP_ROLE = "acb_app_orgname"
_APP_PW = "orgname_scratch_pw"


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


@pytest.fixture(scope="module")
def promoted():
    admin_url = make_url(_URL)
    dedicated = f"{admin_url.database}_orgname"
    _drop_database(admin_url, dedicated)
    maint = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with maint.connect() as c:
        c.execute(text(f'CREATE DATABASE "{dedicated}"'))
    maint.dispose()

    ded_admin_url = admin_url.set(database=dedicated)
    eng = create_engine(ded_admin_url, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    for phase in ("01_add_columns.sql", "02_backfill.sql",
                  "03_constraints.sql", "04_policies.sql"):
        with eng.begin() as conn:
            _exec_sql_file(conn, (_GENERATED / phase).read_text(encoding="utf-8"))
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
        c.execute(text(f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {_APP_ROLE}"))

    orgs = {str(uuid.uuid4()): "Acme Tools", str(uuid.uuid4()): "Bharat Labs"}
    with eng.begin() as conn:
        for i, (oid, name) in enumerate(orgs.items()):
            conn.execute(text(
                "INSERT INTO organization (id, slug, display_name) "
                "VALUES (:id, :slug, :name)"),
                {"id": oid, "slug": f"orgname-{i}", "name": name})

    app_engine = create_engine(
        admin_url.set(database=dedicated, username=_APP_ROLE, password=_APP_PW),
        future=True,
    )
    yield app_engine, orgs
    app_engine.dispose()
    eng.dispose()
    _drop_database(admin_url, dedicated)


def test_each_org_reads_its_own_name_as_the_app_role(promoted, monkeypatch) -> None:
    import orchestrator.executor as ex

    app_engine, orgs = promoted
    monkeypatch.setattr(ex, "_opener_for_org", lambda org: (lambda: Session(app_engine)))
    for oid, name in orgs.items():
        assert ex._read_org_display_name(oid) == name


def test_an_unknown_org_reads_no_name(promoted, monkeypatch) -> None:
    import orchestrator.executor as ex

    app_engine, _ = promoted
    monkeypatch.setattr(ex, "_opener_for_org", lambda org: (lambda: Session(app_engine)))
    assert ex._read_org_display_name(str(uuid.uuid4())) == ""
