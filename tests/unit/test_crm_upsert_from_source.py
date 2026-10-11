"""``core.upsert_from_source`` on a real Postgres (WS-53 CRM-Z3, R8).

Two organizations, one ``ext_id``. Hermetic fakes agree with whatever SQL they
are handed, so every row assertion here runs against the migration ladder.

The connection is a sync one behind a one-method async adapter, as in
``test_people_email_per_tenant.py``: psycopg refuses the Windows default event
loop. Isolation is read as a role that cannot bypass row level security,
because the ladder owner is a superuser and sees every tenant.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from gateway.routes.crm import core as crm_core
from sqlalchemy import create_engine, text

from tests.unit._tenant_ladder import apply_ladder

_URL = os.environ.get("TENANT_LADDER_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not _URL,
    reason=(
        "TENANT_LADDER_DATABASE_URL unset — R8 requires a REAL Postgres. "
        "A skip here is not a pass; CI must set it."
    ),
)

ROLE = "crm_z3_probe"


def run(coro):
    return asyncio.run(coro)


class _AsAsync:
    def __init__(self, conn):
        self._conn = conn

    async def execute(self, sql, params=None):
        return self._conn.execute(sql, params or {})


@pytest.fixture(scope="module")
def eng():
    engine = create_engine(_URL, future=True)
    with engine.begin() as conn:
        apply_ladder(conn)
    yield engine
    engine.dispose()


@pytest.fixture
def two_orgs(eng):
    ids = []
    with eng.begin() as conn:
        for tag in ("z3a", "z3b"):
            oid = uuid.uuid4()
            conn.execute(text(
                "INSERT INTO organization (id, slug, display_name) "
                "VALUES (:id, :s, :n)"),
                {"id": oid, "s": f"{tag}-{oid.hex[:8]}", "n": tag})
            ids.append(oid)
    yield ids
    with eng.begin() as conn:
        for oid in ids:
            conn.execute(text("DELETE FROM organization WHERE id = :i"), {"i": oid})


def _upsert(eng, org, table, values):
    """One upsert in *org*'s tenant. Returns the RETURNING row as a dict."""
    with eng.begin() as conn:
        conn.execute(text("SELECT set_config('app.tenant_id', :o, true)"),
                     {"o": str(org)})
        row = run(crm_core.upsert_from_source(_AsAsync(conn), table, values))
        return dict(row._mapping)


def _count(eng, org, table, ext_id):
    with eng.begin() as conn:
        conn.execute(text("SELECT set_config('app.tenant_id', :o, true)"),
                     {"o": str(org)})
        return conn.execute(text(
            f"SELECT count(*) FROM {table} WHERE ext_id = :e"), {"e": ext_id},
        ).scalar_one()


def test_two_upserts_of_one_key_give_one_row_and_the_second_updates(eng, two_orgs):
    a, _ = two_orgs
    first = _upsert(eng, a, "crm_companies", {
        "ext_source": "zoho", "ext_id": "123", "name": "Old Co",
        "custom": {"Region": "South"},
    })
    second = _upsert(eng, a, "crm_companies", {
        "ext_source": "zoho", "ext_id": "123", "name": "New Co",
        "custom": {"Region": "North"},
    })
    assert first["id"] == second["id"]
    assert _count(eng, a, "crm_companies", "123") == 1
    assert second["name"] == "New Co"
    assert second["custom"] == {"Region": "North"}
    assert second["zoho_dirty"] in (False, None)
    assert second["organization_id"] == a


def test_two_orgs_each_hold_ext_id_123_and_read_only_their_own(eng, two_orgs):
    a, b = two_orgs
    ra = _upsert(eng, a, "crm_companies", {
        "ext_source": "zoho", "ext_id": "123", "name": "Alpha"})
    rb = _upsert(eng, b, "crm_companies", {
        "ext_source": "zoho", "ext_id": "123", "name": "Beta"})
    assert ra["id"] != rb["id"]
    assert (ra["organization_id"], rb["organization_id"]) == (a, b)

    with eng.begin() as conn:
        conn.execute(text(f"DROP ROLE IF EXISTS {ROLE}"))
        conn.execute(text(f"CREATE ROLE {ROLE} NOLOGIN NOSUPERUSER NOBYPASSRLS"))
        conn.execute(text(f"GRANT SELECT ON crm_companies TO {ROLE}"))
        try:
            for org, name in ((a, "Alpha"), (b, "Beta")):
                conn.execute(text(f"SET LOCAL ROLE {ROLE}"))
                conn.execute(text("SELECT set_config('app.tenant_id', :o, true)"),
                             {"o": str(org)})
                names = [r[0] for r in conn.execute(text(
                    "SELECT name FROM crm_companies WHERE ext_id = '123'"))]
                conn.execute(text("RESET ROLE"))
                assert names == [name]
        finally:
            conn.execute(text("RESET ROLE"))
            conn.execute(text(f"REVOKE ALL ON crm_companies FROM {ROLE}"))
            conn.execute(text(f"DROP ROLE {ROLE}"))


def test_a_native_row_with_null_ext_does_not_collide(eng, two_orgs):
    a, _ = two_orgs
    with eng.begin() as conn:
        conn.execute(text("SELECT set_config('app.tenant_id', :o, true)"),
                     {"o": str(a)})
        for name in ("one", "two"):
            conn.execute(text("INSERT INTO crm_companies (name) VALUES (:n)"),
                         {"n": name})


def test_the_conflict_arm_keeps_source_and_never_sets_dirty(eng, two_orgs):
    a, _ = two_orgs
    _upsert(eng, a, "crm_contacts", {
        "ext_source": "zoho", "ext_id": "9", "first_name": "A", "last_name": "X",
        "source": "manual"})
    again = _upsert(eng, a, "crm_contacts", {
        "ext_source": "zoho", "ext_id": "9", "first_name": "A", "last_name": "Y",
        "source": "import"})
    assert again["source"] == "manual"
    assert again["last_name"] == "Y"
    assert again["zoho_dirty"] in (False, None)


def test_a_table_outside_the_five_is_refused(eng, two_orgs):
    a, _ = two_orgs
    with pytest.raises(ValueError, match="not a mirror table"):
        _upsert(eng, a, "crm_lost_reasons", {"ext_source": "zoho", "ext_id": "1"})


@pytest.mark.parametrize("bad", [
    {"ext_id": "1"}, {"ext_source": "zoho"},
    {"ext_source": "zoho", "ext_id": "1", "zoho_dirty": True},
    {"ext_source": "zoho", "ext_id": "1", "organization_id": "x"},
])
def test_bad_values_are_refused_before_any_sql(bad) -> None:
    class _Boom:
        async def execute(self, *a, **k):  # pragma: no cover
            raise AssertionError("SQL ran")

    with pytest.raises(ValueError):
        run(crm_core.upsert_from_source(_Boom(), "crm_leads", bad))


def test_migration_244_shape(eng) -> None:
    """The columns, the five unique indexes, and the one forced policy."""
    five = ("crm_companies", "crm_contacts", "crm_leads", "crm_deals",
            "crm_activities")
    with eng.begin() as conn:
        for table in five:
            cols = {r[0] for r in conn.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = :t"), {"t": table})}
            assert {"ext_source", "ext_id", "ext_modified_at", "custom",
                    "zoho_id"} <= cols, table
            assert conn.execute(text(
                "SELECT count(*) FROM pg_indexes WHERE tablename = :t "
                "AND indexdef LIKE '%UNIQUE%(organization_id, ext_source, ext_id)%'"),
                {"t": table}).scalar_one() == 1, table
        deals = {r[0] for r in conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'crm_deals'"))}
        assert {"currency", "exchange_rate"} <= deals
        assert conn.execute(text(
            "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class "
            "WHERE relname = 'crm_field_defs'")).scalar_one()
        assert conn.execute(text(
            "SELECT count(*) FROM pg_policies WHERE tablename = 'crm_field_defs'"
        )).scalar_one() == 1
