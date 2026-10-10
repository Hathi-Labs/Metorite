"""R8 — one settings row for each organization of a member (H-256, migration 239).

The fault this fences. ``user_settings`` was ``user_id TEXT PRIMARY KEY``
(``51_gtd_settings.sql``), so it held one row for each address. The generated
tenancy phases add ``organization_id`` and FORCE row-level security, and the key
stayed the same. So a member of two organizations could save settings in one of
them only: the insert in the second one failed on the key. Since WS-46 P7 the
Projects chat reads the member's zone from this table (``GET /projects/my/today``).

Migration 239 keys the table on ``(organization_id, user_id)``. The one writer,
``PUT /tasks/settings``, names the bound tenant and that arbiter. The rollover
sweep reads and writes the row of its own organization only.

The route halves run the REAL route functions as the NON-privileged role of the
phase-4 rehearsal (``acb_app_h3rls``: no superuser, no BYPASSRLS), on a database
that has the whole ladder (with 239) and the four generated phases, and two
organizations. A hermetic fake would agree with any SQL it is handed.

The replay half runs the body of 239 on a table shaped like production, as an
owner that FORCE RLS binds and with no tenant, the way the deploy runs it.
"""
from __future__ import annotations

import contextlib
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import psycopg
import pytest

pytest.importorskip("sqlalchemy")

from acb_common.db import bind_tenant, release_tenant, tenant_session
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

# The two-org phase-4 fixture and its DB gate. Used by name for injection.
from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

# ⚠️ Every test here needs the real database. Without this gate the suite
# ERRORS where no ladder URL is set (the deploy run), and the deploy stops.
pytestmark = _DB_GATE

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "infra" / "postgres" / "239_user_settings_per_org.sql"
)


def _address() -> str:
    return f"two.orgs.{uuid.uuid4().hex[:8]}@h256.test"


@pytest.fixture
def db(promoted):  # noqa: F811 — a fixture
    """The promoted catalog, with this module's settings rows removed after."""
    made: list[str] = []
    promoted.made = made
    yield promoted
    with promoted.admin_engine.begin() as c:
        for email in made:
            c.execute(text("DELETE FROM user_settings WHERE user_id = :u"), {"u": email})


@contextlib.contextmanager
def _tenant(org: str):
    token = bind_tenant(org)
    try:
        yield
    finally:
        release_tenant(token)


def _app_dsn(ns) -> str:
    return ns.app_url.render_as_string(hide_password=False)


async def _save(org: str, email: str, **fields: object) -> object:
    """``PUT /tasks/settings`` as the member, inside ``org``."""
    from gateway.routes.tasks.settings import UserSettingsPatch, put_user_settings

    with _tenant(org):
        return await put_user_settings(
            patch=UserSettingsPatch(**fields), user=SimpleNamespace(email=email))


async def _read(org: str, email: str) -> object:
    """``GET /tasks/settings`` as the member, inside ``org``."""
    from gateway.routes.tasks.settings import get_user_settings

    with _tenant(org):
        return await get_user_settings(user=SimpleNamespace(email=email))


def _rows(ns, email: str) -> list[tuple[str, str]]:
    with ns.admin_engine.connect() as c:
        return [tuple(r) for r in c.execute(text(
            "SELECT organization_id::text, timezone FROM user_settings "
            "WHERE user_id = :u ORDER BY 1"), {"u": email})]


# ── The route, for one address in two organizations ────────────────────────


async def test_one_address_saves_and_reads_its_own_settings_in_each_org(db) -> None:
    # The old key, `user_id` alone, refused the second save.
    email = _address()
    db.made.append(email)
    async with tenant_engine_scope(_app_dsn(db)):
        await _save(db.org_a, email, timezone="Asia/Kolkata", chat_model="tier-a")
        await _save(db.org_b, email, timezone="Europe/Berlin", chat_model="tier-b")
        a = await _read(db.org_a, email)
        b = await _read(db.org_b, email)
    assert (a.timezone, a.chat_model) == ("Asia/Kolkata", "tier-a")
    assert (b.timezone, b.chat_model) == ("Europe/Berlin", "tier-b")
    assert _rows(db, email) == sorted(
        [(db.org_a, "Asia/Kolkata"), (db.org_b, "Europe/Berlin")])


async def test_another_organization_never_reads_the_row(db) -> None:
    from gateway.routes.projects.personal import stored_zone

    email = _address()
    db.made.append(email)
    async with tenant_engine_scope(_app_dsn(db)):
        await _save(db.org_a, email, timezone="Pacific/Kiritimati")
        b = await _read(db.org_b, email)
        async with tenant_session(db.org_b) as s:
            zone_b = await stored_zone(s, email)
        async with tenant_session(db.org_a) as s:
            zone_a = await stored_zone(s, email)
    # Org B has no row, so its read is the default and no zone is stored.
    assert b.timezone == "UTC"
    assert zone_b is None
    assert zone_a == "Pacific/Kiritimati"


async def test_a_second_save_replaces_the_first_in_its_own_org_only(db) -> None:
    email = _address()
    db.made.append(email)
    async with tenant_engine_scope(_app_dsn(db)):
        await _save(db.org_a, email, timezone="Asia/Tokyo")
        await _save(db.org_b, email, timezone="America/Lima")
        await _save(db.org_a, email, timezone="Asia/Dubai")
    assert _rows(db, email) == sorted(
        [(db.org_a, "Asia/Dubai"), (db.org_b, "America/Lima")])


# ── The statement, and the key ──────────────────────────────────────────────


def _upsert(engine, org: str | None, email: str, zone: str) -> None:
    from gateway.routes.tasks.settings import upsert_settings_sql

    with engine.begin() as c:
        if org is not None:
            c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": org})
        c.execute(text(upsert_settings_sql(["timezone"])), {"uid": email, "timezone": zone})


def test_the_upsert_statement_runs_for_two_organizations(db, app_engine) -> None:  # noqa: F811
    email = _address()
    db.made.append(email)
    _upsert(app_engine, db.org_a, email, "Asia/Kolkata")
    _upsert(app_engine, db.org_a, email, "Asia/Kathmandu")
    _upsert(app_engine, db.org_b, email, "Africa/Cairo")
    assert _rows(db, email) == sorted(
        [(db.org_a, "Asia/Kathmandu"), (db.org_b, "Africa/Cairo")])


def test_an_unbound_save_writes_nothing(db, app_engine) -> None:  # noqa: F811
    email = _address()
    db.made.append(email)
    with pytest.raises(DBAPIError):
        _upsert(app_engine, None, email, "Asia/Kolkata")
    assert _rows(db, email) == []


def test_the_primary_key_is_per_organization(db) -> None:
    with db.admin_engine.connect() as c:
        defs = c.execute(text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid = 'user_settings'::regclass AND contype = 'p'"
        )).scalars().all()
    assert defs == ["PRIMARY KEY (organization_id, user_id)"]


# ── The rollover sweep, which reads every organization ─────────────────────


async def test_the_sweep_rolls_over_the_address_in_each_organization(db) -> None:
    # The sweep deduped on `user_id` alone (first tenant wins), so the second
    # organization's row never rolled over.
    from gateway.routes.tasks.calendar import _run_rollover_sweep

    email = _address()
    db.made.append(email)
    with db.admin_engine.begin() as c:
        for org in (db.org_a, db.org_b):
            c.execute(text(
                "INSERT INTO user_settings (user_id, organization_id, timezone, "
                "auto_rollover, last_rollover_date) "
                "VALUES (:u, CAST(:o AS uuid), 'UTC', true, NULL)"),
                {"u": email, "o": org})
    async with tenant_engine_scope(_app_dsn(db)):
        await _run_rollover_sweep()
    with db.admin_engine.connect() as c:
        rolled = c.execute(text(
            "SELECT organization_id::text, last_rollover_date FROM user_settings "
            "WHERE user_id = :u ORDER BY 1"), {"u": email}).all()
    today = datetime.now(UTC).date()
    assert [tuple(r) for r in rolled] == sorted(
        [(db.org_a, today), (db.org_b, today)])


# ── The migration, replayed on a table shaped like production ──────────────


def _body() -> str:
    """239 without its own BEGIN and COMMIT, so a test can roll it back."""
    lines = _MIGRATION.read_text(encoding="utf-8").splitlines()
    kept = [ln for ln in lines if ln.strip() not in ("BEGIN;", "COMMIT;")]
    assert len(kept) == len(lines) - 2, "239 lost its one transaction"
    return "\n".join(kept)


def _run(conn, sql: str) -> None:
    with conn.connection.dbapi_connection.cursor() as cur:
        cur.execute(sql)


_PROD_SHAPE = """
ALTER TABLE user_settings RENAME TO user_settings_h256_real;
ALTER TABLE user_settings_h256_real
    RENAME CONSTRAINT user_settings_org_user_pkey TO user_settings_h256_real_pkey;
CREATE ROLE h256_owner NOLOGIN NOSUPERUSER NOBYPASSRLS;
GRANT USAGE, CREATE ON SCHEMA public TO h256_owner;
GRANT SELECT, REFERENCES ON organization TO h256_owner;
CREATE TABLE user_settings (
    user_id TEXT,
    timezone TEXT,
    organization_id UUID NOT NULL
        DEFAULT current_setting('app.tenant_id', true)::uuid,
    CONSTRAINT gtd_settings_pkey PRIMARY KEY (user_id)
);
ALTER TABLE user_settings ADD CONSTRAINT gtd_settings_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
ALTER TABLE user_settings OWNER TO h256_owner;
ALTER TABLE user_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_settings FORCE ROW LEVEL SECURITY;
CREATE POLICY gtd_settings_tenant_isolation ON user_settings
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);
"""


def _pk(conn) -> list[str]:
    return conn.execute(text(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conrelid = 'user_settings'::regclass AND contype = 'p'"
    )).scalars().all()


@pytest.mark.parametrize("orgs", [1, 2], ids=["one-org", "two-orgs"])
def test_239_replays_on_the_production_shape(db, orgs: int) -> None:
    """Production: the column is there and NOT NULL, RLS is forced, and the
    deploy runs the file as an owner that RLS binds, with no tenant. Only the
    key changes, every row stays, and a second run changes nothing."""
    picked = [db.org_a, db.org_b][:orgs]
    conn = db.admin_engine.connect()
    trans = conn.begin()
    try:
        _run(conn, _PROD_SHAPE)
        for i, org in enumerate(picked):
            conn.execute(text(
                "INSERT INTO user_settings (user_id, timezone, organization_id) "
                "VALUES (:u, 'UTC', CAST(:o AS uuid))"),
                {"u": f"m{i}@h256.test", "o": org})
        assert _pk(conn) == ["PRIMARY KEY (user_id)"]

        conn.execute(text("SET LOCAL ROLE h256_owner"))
        _run(conn, _body())
        _run(conn, _body())
        conn.execute(text("RESET ROLE"))

        assert _pk(conn) == ["PRIMARY KEY (organization_id, user_id)"]
        assert conn.execute(text("SELECT count(*) FROM user_settings")).scalar() == orgs
        # The re-key is the point: one address now holds a row in each org.
        for org in (db.org_a, db.org_b):
            conn.execute(text(
                "INSERT INTO user_settings (user_id, timezone, organization_id) "
                "VALUES ('m0@h256.test', 'UTC', CAST(:o AS uuid)) "
                "ON CONFLICT (organization_id, user_id) DO NOTHING"), {"o": org})
        both = conn.execute(text(
            "SELECT count(*) FROM user_settings WHERE user_id = 'm0@h256.test'"
        )).scalar()
        assert both == 2
    finally:
        trans.rollback()
        conn.close()


def test_239_refuses_a_row_with_no_organization_when_two_exist(db) -> None:
    """A database from before tenancy, with rows and two organizations. No
    rule can say whose row it is, so 239 stops and changes nothing."""
    conn = db.admin_engine.connect()
    trans = conn.begin()
    try:
        _run(conn, """
            ALTER TABLE user_settings RENAME TO user_settings_h256_real;
            ALTER TABLE user_settings_h256_real
                RENAME CONSTRAINT user_settings_org_user_pkey
                TO user_settings_h256_real_pkey;
            CREATE TABLE user_settings (user_id TEXT PRIMARY KEY, timezone TEXT);
            INSERT INTO user_settings (user_id) VALUES ('old@h256.test');
        """)
        # The raw cursor raises the driver's own error, not SQLAlchemy's.
        with pytest.raises(psycopg.Error, match="refusing to re-key"):
            _run(conn, _body())
    finally:
        trans.rollback()
        conn.close()
