"""WS-53 CRM-T1 — the CRM's company leaves the word "organization", and every
``crm_*`` table belongs to one tenant.

Spec: ``project-docs/specs/crm_platform.md`` §4 and §13.3 (CRM-T1) · D95.2.

==========================================  =======================
old                                         new
==========================================  =======================
table ``crm_organizations``                 ``crm_companies``
``organization_id`` on contacts, deals,     ``company_id``
activities (the customer company)
no tenant key on those three                ``organization_id`` on all 13
                                            ``crm_*`` tables (migration 241)
==========================================  =======================

**The rename lives in 144_crm.sql, the file that creates the table.** That is
the shape the gtd rename proved (``test_gtd_rename_upgrade.py``). One file then
answers a fresh install, an upgrade and a replay.

⚠️ **The bug this suite exists for** is the gtd one. The prologue names the
OLD table, so a sweep that rewrites ``crm_organizations`` everywhere rewrites
the prologue into a rename of the new name onto itself. That is a silent no-op.
An upgraded database keeps the old table and gets an empty new one beside it,
and every test that only builds a fresh database still passes. The upgrade arm
below rebuilds the old shape and upgrades it.

⚠️ **The column guard checks the foreign key, not only the names.** After 241,
each of the three tables holds BOTH ``company_id`` and the tenant
``organization_id``. The block renames only an ``organization_id`` whose foreign
key points at ``crm_companies``, so a replay can never rename the tenant key.

R8 arms run against a real Postgres behind ``TENANT_LADDER_DATABASE_URL``. The
gate is ``test_h3_rls_promotion_rehearsal._DB_GATE``, on each R8 test rather
than on the module, so the text arms still run where no database exists
(``deploy.yml`` has none, and a suite that builds ``promoted`` without the gate
fails there).

⚠️ **The upgrade and replay arms run inside one transaction that is rolled
back.** ``241_crm_tenancy.sql`` carries its own ``BEGIN``/``COMMIT``, so the
arms strip those two lines before they run the file. Otherwise the file's
``COMMIT`` would commit the arm's rebuilt old shape into the ladder database.

Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_crm_rename_upgrade.py -v -rs
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import create_engine, text

from tests.unit._tenant_ladder import apply_ladder, tenant_engine_scope

# ``promoted`` and ``app_engine`` are used by name for fixture injection, so
# the import is load-bearing even though it reads as unused.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    _URL,
    app_engine,
    promoted,
)

MIGRATIONS = Path(__file__).resolve().parents[2] / "infra" / "postgres"

OLD_TABLE = "crm_organizations"
NEW_TABLE = "crm_companies"

#: The three tables whose company column was called ``organization_id``.
COMPANY_TABLES: tuple[str, ...] = ("crm_contacts", "crm_deals", "crm_activities")

#: Every ``crm_*`` table. All 13 carry the tenant key after migration 241.
CRM_TABLES: tuple[str, ...] = (
    "crm_companies", "crm_contacts", "crm_leads", "crm_deals",
    "crm_activities", "crm_lead_statuses", "crm_deal_statuses",
    "crm_lost_reasons", "crm_deal_contacts", "crm_status_changes",
    "crm_zoho_tombstones", "crm_sync_cursors", "crm_auto_lead_cursors",
)

#: The global keys of the old shape, with the names a database that predates
#: CRM-T1 gave them. 241 must find them through ``pg_constraint``, never by
#: name, so the upgrade arm restores them under these OLD names.
OLD_GLOBAL_KEYS: tuple[tuple[str, str, str], ...] = (
    (NEW_TABLE, "crm_organizations_zoho_id_key", "zoho_id"),
    ("crm_contacts", "crm_contacts_zoho_id_key", "zoho_id"),
    ("crm_leads", "crm_leads_zoho_id_key", "zoho_id"),
    ("crm_deals", "crm_deals_zoho_id_key", "zoho_id"),
    ("crm_activities", "crm_activities_zoho_id_key", "zoho_id"),
    ("crm_lead_statuses", "crm_lead_statuses_name_key", "name"),
    ("crm_deal_statuses", "crm_deal_statuses_name_key", "name"),
    ("crm_lost_reasons", "crm_lost_reasons_label_key", "label"),
)

#: The per-tenant unique indexes 241 creates, as ``index -> (table, columns)``.
NEW_KEYS: dict[str, tuple[str, list[str]]] = {
    "uq_crm_companies_org_zoho_id": (NEW_TABLE, ["organization_id", "zoho_id"]),
    "uq_crm_contacts_org_zoho_id": ("crm_contacts", ["organization_id", "zoho_id"]),
    "uq_crm_leads_org_zoho_id": ("crm_leads", ["organization_id", "zoho_id"]),
    "uq_crm_deals_org_zoho_id": ("crm_deals", ["organization_id", "zoho_id"]),
    "uq_crm_activities_org_zoho_id": ("crm_activities", ["organization_id", "zoho_id"]),
    "uq_crm_lead_statuses_org_name": ("crm_lead_statuses", ["organization_id", "name"]),
    "uq_crm_deal_statuses_org_name": ("crm_deal_statuses", ["organization_id", "name"]),
    "uq_crm_lost_reasons_org_label": ("crm_lost_reasons", ["organization_id", "label"]),
}


# -- Finding the files, by CONTENT (R1) ------------------------------------


def _find(marker: str) -> Path:
    """The one numbered migration whose text holds *marker*. Never a number:
    R1 can renumber a migration at merge."""
    hits = [
        p for p in sorted(MIGRATIONS.glob("[0-9]*_*.sql"))
        if marker in p.read_text(encoding="utf-8")
    ]
    assert len(hits) == 1, f"expected one migration holding {marker!r}, got {hits}"
    return hits[0]


def _spine() -> Path:
    return _find(f"CREATE TABLE IF NOT EXISTS {NEW_TABLE}")


def _sync() -> Path:
    return _find("CREATE TABLE IF NOT EXISTS crm_zoho_tombstones")


def _discipline() -> Path:
    return _find("ADD COLUMN IF NOT EXISTS required_fields")


def _tenancy() -> Path:
    return _find("ALTER TABLE crm_contacts\n    ADD COLUMN IF NOT EXISTS organization_id")


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _code(sql: str) -> str:
    """The statements with ``--`` comments stripped. The prologue quotes the
    broken statement in prose, so an assertion over the raw file would read
    the warning as the defect."""
    return re.sub(r"--[^\n]*", "", sql)


def _block(sql: str, tag: str) -> str:
    match = re.search(rf"DO \${tag}\$(.*?)\${tag}\$;", sql, re.DOTALL)
    assert match, f"no DO ${tag}$ block"
    return match.group(1)


def _no_txn(sql: str) -> str:
    """The file without its own ``BEGIN;``/``COMMIT;`` lines (module docstring)."""
    return re.sub(r"^\s*(BEGIN|COMMIT);\s*$", "", sql, flags=re.MULTILINE)


# -- (a) The table prologue ------------------------------------------------


def test_the_spine_carries_a_guarded_table_rename():
    body = _block(_text(_spine()), "rename_crm_companies")
    # The old name, ONCE, as a quoted literal.
    assert f"old_name CONSTANT text := '{OLD_TABLE}';" in body
    # Renamed, never recreated.
    assert f"format('ALTER TABLE %I RENAME TO %I', old_name, '{NEW_TABLE}')" in body
    # A view that wears the old name is left alone.
    assert "relkind = 'r'" in body
    # Never renames ONTO a name that is taken, so a replay does nothing.
    assert f"to_regclass('public.{NEW_TABLE}') IS NULL" in body


def test_the_old_table_name_occurs_once_in_the_executable_sql():
    """One quoted literal, and nowhere else. A second spelling is a second
    place a sweep can break, or a statement that still uses the old name."""
    code = _code(_text(_spine()))
    assert len(re.findall(rf"\b{OLD_TABLE}\b", code)) == 1
    assert code.count(f"'{OLD_TABLE}'") == 1


def test_the_table_rename_is_not_the_swept_no_op():
    body = _code(_block(_text(_spine()), "rename_crm_companies"))
    assert f"ALTER TABLE {NEW_TABLE} RENAME TO {NEW_TABLE}" not in body
    assert f"RENAME TO %I', '{NEW_TABLE}', '{NEW_TABLE}'" not in body
    assert f"old_name CONSTANT text := '{NEW_TABLE}'" not in body


def test_the_table_prologue_runs_before_the_create():
    """A rename after the CREATE finds the new name taken and does nothing."""
    sql = _text(_spine())
    assert sql.index("$rename_crm_companies$") < sql.index(
        f"CREATE TABLE IF NOT EXISTS {NEW_TABLE}"
    )


# -- (b) The three column blocks -------------------------------------------


@pytest.mark.parametrize("table", COMPANY_TABLES)
def test_each_company_column_carries_a_guarded_rename(table):
    body = _block(_text(_spine()), f"rename_{table}_company_id")
    assert "old_col CONSTANT text := 'organization_id';" in body
    assert (
        f"format('ALTER TABLE %I RENAME COLUMN %I TO %I',\n"
        f"                       '{table}', old_col, 'company_id')"
    ) in body
    # All three guards: the old column exists, the new one does not, and the
    # foreign key on the old one points at the company table.
    assert "attname = old_col" in body
    assert "attname = 'company_id'" in body
    assert f"c.confrelid = to_regclass('public.{NEW_TABLE}')" in body


@pytest.mark.parametrize("table", COMPANY_TABLES)
def test_the_column_rename_is_not_the_swept_no_op(table):
    body = _code(_block(_text(_spine()), f"rename_{table}_company_id"))
    assert "RENAME COLUMN company_id TO company_id" not in body
    assert "old_col CONSTANT text := 'company_id'" not in body
    assert "'company_id', 'company_id'" not in body


@pytest.mark.parametrize("table", COMPANY_TABLES)
def test_each_column_rename_runs_before_its_create(table):
    sql = _text(_spine())
    assert sql.index(f"$rename_{table}_company_id$") < sql.index(
        f"CREATE TABLE IF NOT EXISTS {table} ("
    )


# -- (c) No migration creates the old table --------------------------------


def test_no_migration_creates_the_old_table():
    for path in sorted(MIGRATIONS.glob("[0-9]*_*.sql")):
        code = _code(_text(path))
        assert not re.search(
            rf"CREATE\s+TABLE\s+(IF\s+NOT\s+EXISTS\s+)?{OLD_TABLE}\b", code, re.I,
        ), f"{path.name} still creates {OLD_TABLE}. It must create {NEW_TABLE}."


# -- The seeds and the tenancy file, as text -------------------------------


@pytest.mark.parametrize(
    "table", ["crm_lead_statuses", "crm_deal_statuses", "crm_lost_reasons"],
)
def test_each_seed_runs_only_on_a_table_without_a_tenant_column(table):
    """A changed migration runs again on the next deploy. After 241 the seed
    tables are NOT NULL under FORCE row level security, and a migration binds
    no tenant, so an unguarded seed would stop the deploy."""
    body = _block(_text(_spine()), f"seed_{table}")
    assert f"to_regclass('public.{table}')" in body
    assert "attname = 'organization_id'" in body
    assert "IF NOT EXISTS" in body
    assert f"INSERT INTO {table}" in body


def test_241_declares_the_tenant_key_inline_and_never_as_org_fk():
    """Generated phase 3 adds ``<t>_org_fk`` by name. A migration that adds
    the same name collides with it the day the phases are applied."""
    code = _code(_text(_tenancy()))
    assert "_org_fk" not in code
    for table in CRM_TABLES:
        assert re.search(
            rf"ALTER TABLE {table}\n    ADD COLUMN IF NOT EXISTS organization_id UUID\n"
            r"    REFERENCES organization \(id\) ON DELETE CASCADE\n"
            r"    DEFAULT current_setting\('app\.tenant_id', true\)::uuid;",
            code,
        ), table


# == R8 — a real Postgres ===================================================


@pytest.fixture(scope="module")
def eng():
    engine = create_engine(_URL, future=True)
    with engine.begin() as conn:
        apply_ladder(conn)
    yield engine
    engine.dispose()


def _exists(conn, table: str) -> bool:
    return bool(conn.execute(
        text("SELECT to_regclass(:t) IS NOT NULL"), {"t": f"public.{table}"},
    ).scalar())


def _fk_target(conn, table: str, column: str) -> list[tuple[str, str]]:
    """``(referenced table, delete action)`` of each foreign key on *column*."""
    return [tuple(r) for r in conn.execute(text(
        "SELECT ref.relname, c.confdeltype::text FROM pg_constraint c "
        "JOIN pg_class ref ON ref.oid = c.confrelid "
        "JOIN pg_attribute a ON a.attrelid = c.conrelid "
        "  AND a.attnum = ANY (c.conkey) "
        "WHERE c.contype = 'f' AND c.conrelid = to_regclass(:t) "
        "  AND a.attname = :col ORDER BY 1"
    ), {"t": f"public.{table}", "col": column}).all()]


def _forced(conn, table: str) -> bool:
    return bool(conn.execute(text(
        "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class "
        "WHERE oid = to_regclass(:t)"), {"t": f"public.{table}"}).scalar())


def _policies(conn, table: str) -> list[str]:
    return list(conn.execute(text(
        "SELECT policyname FROM pg_policies WHERE tablename = :t ORDER BY 1"),
        {"t": table}).scalars())


def _shape(conn) -> dict[str, list]:
    """Columns, constraints, indexes, policies and RLS flags of the 13 tables."""
    tables = list(CRM_TABLES)
    return {
        "columns": sorted(tuple(r) for r in conn.execute(text(
            "SELECT table_name, column_name, data_type, is_nullable, "
            "       coalesce(column_default, '') "
            "  FROM information_schema.columns "
            " WHERE table_schema = 'public' AND table_name = ANY (:t)"),
            {"t": tables}).all()),
        "constraints": sorted(tuple(r) for r in conn.execute(text(
            "SELECT rel.relname, c.conname, pg_get_constraintdef(c.oid) "
            "  FROM pg_constraint c JOIN pg_class rel ON rel.oid = c.conrelid "
            " WHERE rel.relname = ANY (:t)"), {"t": tables}).all()),
        "indexes": sorted(tuple(r) for r in conn.execute(text(
            "SELECT tablename, indexname, indexdef FROM pg_indexes "
            " WHERE tablename = ANY (:t)"), {"t": tables}).all()),
        "policies": sorted(tuple(r) for r in conn.execute(text(
            "SELECT tablename, policyname, coalesce(qual, ''), "
            "       coalesce(with_check, '') "
            "  FROM pg_policies WHERE tablename = ANY (:t)"),
            {"t": tables}).all()),
        "rls": sorted(tuple(r) for r in conn.execute(text(
            "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
            " WHERE relname = ANY (:t) AND relkind = 'r'"), {"t": tables}).all()),
    }


def _unique_keys(conn, table: str) -> list[list[str]]:
    """The key columns of each unique index on *table* but the primary key."""
    rows = conn.execute(text(
        "SELECT array_agg(a.attname::text ORDER BY k.ord) "
        "  FROM pg_index i "
        "  CROSS JOIN LATERAL unnest(i.indkey) WITH ORDINALITY AS k(attnum, ord) "
        "  JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = k.attnum "
        " WHERE i.indrelid = to_regclass(:t) AND i.indisunique "
        "   AND NOT i.indisprimary "
        " GROUP BY i.indexrelid"), {"t": f"public.{table}"}).scalars().all()
    return sorted(list(r) for r in rows)


def _primary_key(conn, table: str) -> list[str]:
    return list(conn.execute(text(
        "SELECT array_agg(a.attname::text ORDER BY k.ord) "
        "  FROM pg_constraint c "
        "  CROSS JOIN LATERAL unnest(c.conkey) WITH ORDINALITY AS k(attnum, ord) "
        "  JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum "
        " WHERE c.conrelid = to_regclass(:t) AND c.contype = 'p'"),
        {"t": f"public.{table}"}).scalar() or [])


def _run(conn, sql: str) -> None:
    with conn.connection.dbapi_connection.cursor() as cur:
        cur.execute(sql)


# -- (d) A fresh install ---------------------------------------------------


@_DB_GATE
def test_the_ladder_builds_crm_companies_and_the_two_keys(eng):
    with eng.connect() as conn:
        assert _exists(conn, NEW_TABLE), f"the ladder did not build {NEW_TABLE}"
        assert not _exists(conn, OLD_TABLE), f"the ladder still builds {OLD_TABLE}"
        for table in COMPANY_TABLES:
            assert _fk_target(conn, table, "company_id") == [
                (NEW_TABLE, "c" if table == "crm_activities" else "n"),
            ], f"{table}.company_id must point at {NEW_TABLE}"
        for table in CRM_TABLES:
            assert _fk_target(conn, table, "organization_id") == [
                ("organization", "c"),
            ], f"{table}.organization_id must cascade from the tenant"
            nullable = conn.execute(text(
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_name = :t AND column_name = 'organization_id'"),
                {"t": table}).scalar_one()
            assert nullable == "NO", f"{table}.organization_id is nullable"
            assert _forced(conn, table), f"{table} does not FORCE row level security"
            assert len(_policies(conn, table)) == 1, (
                f"{table} has policies {_policies(conn, table)}, not one"
            )
        for index, (table, columns) in NEW_KEYS.items():
            assert columns in _unique_keys(conn, table), index
            for single in (["zoho_id"], ["name"], ["label"]):
                assert single not in _unique_keys(conn, table), (
                    f"{table} still has the global key {single}"
                )
        assert _primary_key(conn, "crm_sync_cursors") == ["organization_id", "module"]


# -- (e) An upgrade, from both old shapes ----------------------------------


def _restore_old_shape(conn, *, production: bool) -> None:
    """Put the schema back the way CRM-T1 found it.

    ``ladder``: no ``crm_*`` table has a tenant key, which is a database the
    ladder built before 241. ``production``: the generated phases scoped ten
    tables on 2026-08-23 and left the three homonym tables alone. In both, the
    company table and column carry their OLD names and the keys are global.
    """
    scoped_by_phases = set(CRM_TABLES) - set(COMPANY_TABLES) if production else set()
    for table in CRM_TABLES:
        if table in scoped_by_phases:
            continue
        for policy in _policies(conn, table):
            conn.execute(text(f'DROP POLICY "{policy}" ON {table}'))
        conn.execute(text(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY"))
        conn.execute(text(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY"))
        # CASCADE takes the per-tenant indexes and the foreign key with it.
        conn.execute(text(f"ALTER TABLE {table} DROP COLUMN organization_id CASCADE"))
    for index in NEW_KEYS:
        conn.execute(text(f"DROP INDEX IF EXISTS {index}"))
    for table, name, column in OLD_GLOBAL_KEYS:
        conn.execute(text(f"ALTER TABLE {table} ADD CONSTRAINT {name} UNIQUE ({column})"))
    pk = conn.execute(text(
        "SELECT conname FROM pg_constraint WHERE contype = 'p' "
        "AND conrelid = 'crm_sync_cursors'::regclass")).scalar()
    if pk:
        conn.execute(text(f'ALTER TABLE crm_sync_cursors DROP CONSTRAINT "{pk}"'))
    conn.execute(text(
        "ALTER TABLE crm_sync_cursors ADD CONSTRAINT crm_sync_cursors_pkey "
        "PRIMARY KEY (module)"))
    for table in COMPANY_TABLES:
        conn.execute(text(
            f"ALTER TABLE {table} RENAME COLUMN company_id TO organization_id"))
    conn.execute(text(f"ALTER TABLE {NEW_TABLE} RENAME TO {OLD_TABLE}"))


@_DB_GATE
@pytest.mark.parametrize("shape", ["ladder", "production"])
def test_an_existing_crm_upgrades_with_its_rows(eng, shape):
    """The upgrade. Rebuild the old shape, seed a company with a contact, a
    deal and an activity that point at it, then apply 144 and 241.

    This is the arm the swept no-op fails. Everything runs in one transaction
    that is rolled back, so the ladder database does not change.
    """
    production = shape == "production"
    conn = eng.connect()
    trans = conn.begin()
    try:
        conn.execute(text("TRUNCATE " + ", ".join(CRM_TABLES) + " CASCADE"))
        _restore_old_shape(conn, production=production)
        assert _exists(conn, OLD_TABLE) and not _exists(conn, NEW_TABLE)

        conn.execute(text(
            "INSERT INTO organization (id, slug, display_name) "
            "VALUES (gen_random_uuid(), 'fracktalworks', 'Fracktal Works') "
            "ON CONFLICT (slug) DO NOTHING"))
        owner = str(conn.execute(text(
            "SELECT id FROM organization WHERE slug = 'fracktalworks'")).scalar_one())
        # In the production shape the ten scoped tables already need a tenant.
        tenant = {"o": owner} if production else {}
        tcol = ", organization_id" if production else ""
        tval = ", CAST(:o AS uuid)" if production else ""

        company = str(conn.execute(text(
            f"INSERT INTO {OLD_TABLE} (name, zoho_id{tcol}) "
            f"VALUES ('Keep Ltd', 'z-keep-co'{tval}) RETURNING id"), tenant).scalar_one())
        stage = str(conn.execute(text(
            f"INSERT INTO crm_deal_statuses (name, position, type, "
            f"required_fields{tcol}) VALUES ('T1 stage', 10, 'open', "
            f"ARRAY['amount', 'organization_id']{tval}) RETURNING id"),
            tenant).scalar_one())
        contact = str(conn.execute(text(
            "INSERT INTO crm_contacts (first_name, organization_id, zoho_id) "
            "VALUES ('Kay', CAST(:c AS uuid), 'z-keep-ct') RETURNING id"),
            {"c": company}).scalar_one())
        deal = str(conn.execute(text(
            "INSERT INTO crm_deals (name, organization_id, status_id) "
            "VALUES ('Keep deal', CAST(:c AS uuid), CAST(:s AS uuid)) RETURNING id"),
            {"c": company, "s": stage}).scalar_one())
        activity = str(conn.execute(text(
            "INSERT INTO crm_activities (type, organization_id, created_by) "
            "VALUES ('note', CAST(:c AS uuid), 'keep@crm-t1.test') RETURNING id"),
            {"c": company}).scalar_one())

        _run(conn, _no_txn(_text(_spine())))
        _run(conn, _no_txn(_text(_tenancy())))

        assert _exists(conn, NEW_TABLE), f"144 did not rename {OLD_TABLE}"
        assert not _exists(conn, OLD_TABLE), (
            f"144 left {OLD_TABLE} in place and made a second table. This is the "
            "swept-no-op failure the module docstring records."
        )
        assert conn.execute(text(
            f"SELECT name FROM {NEW_TABLE} WHERE id = CAST(:c AS uuid)"),
            {"c": company}).scalar_one() == "Keep Ltd"
        for table, row in (("crm_contacts", contact), ("crm_deals", deal),
                           ("crm_activities", activity)):
            got = conn.execute(text(
                f"SELECT company_id::text, organization_id::text FROM {table} "
                "WHERE id = CAST(:r AS uuid)"), {"r": row}).one()
            assert got[0] == company, f"{table}.company_id lost the company"
            assert got[1] == owner, f"{table}.organization_id is not fracktalworks"
        for table, row in ((NEW_TABLE, company), ("crm_deal_statuses", stage)):
            assert str(conn.execute(text(
                f"SELECT organization_id FROM {table} WHERE id = CAST(:r AS uuid)"),
                {"r": row}).scalar_one()) == owner, table
        assert conn.execute(text(
            "SELECT required_fields FROM crm_deal_statuses "
            "WHERE id = CAST(:s AS uuid)"), {"s": stage}).scalar_one() == [
            "amount", "company_id",
        ], "241 did not rename the company in the stage requirements"

        for table in CRM_TABLES:
            assert _fk_target(conn, table, "organization_id") == [
                ("organization", "c"),
            ], f"{table}: the tenant key must cascade, and only once"
            assert _forced(conn, table), f"{table} does not FORCE row level security"
            assert len(_policies(conn, table)) == 1, (
                f"{table} has policies {_policies(conn, table)}, not one"
            )
        for table in COMPANY_TABLES:
            assert [t for t, _ in _fk_target(conn, table, "company_id")] == [NEW_TABLE]
        for index, (table, columns) in NEW_KEYS.items():
            assert columns in _unique_keys(conn, table), index
        old_names = {name for _, name, _ in OLD_GLOBAL_KEYS}
        left = conn.execute(text(
            "SELECT conname FROM pg_constraint WHERE conname = ANY (:n)"),
            {"n": sorted(old_names)}).scalars().all()
        assert not left, f"241 left the global keys {left}"
        assert _primary_key(conn, "crm_sync_cursors") == ["organization_id", "module"]
    finally:
        trans.rollback()
        conn.close()


# -- (e2) Who owns a row with no tenant ------------------------------------
#
# The owner of a row with no tenant is `fracktalworks`, else the only
# organization. With many organizations and no `fracktalworks` there is no
# owner, and 241 must not stop: the shared dev database is that shape, and a
# stop there breaks `scripts/dev_db.sh` for every session. So that table keeps
# a NULLABLE tenant column, FORCE row level security hides the orphan rows,
# and a NOTICE names the table. A table with no orphan rows is NOT NULL.
#
# ⚠️ No `default` lookup: the D43-A ratchet in test_org_provisioning.py
# (`TestTheDefaultSlugRatchet`) refuses a new ladder line that names it.


def _hide_slug(conn, slug: str) -> None:
    """Rename an organization out of the way, inside the arm's transaction."""
    conn.execute(text(
        "UPDATE organization SET slug = slug || '-hidden-' || "
        "substr(md5(random()::text), 1, 6) WHERE slug = :s"), {"s": slug})


def _old_shape_with_orphan(conn, *, extra_orgs: int) -> str:
    """The ladder's old shape, *extra_orgs* more organizations, and one company
    row with no tenant. Returns the company id."""
    conn.execute(text("TRUNCATE " + ", ".join(CRM_TABLES) + " CASCADE"))
    _restore_old_shape(conn, production=False)
    for _ in range(extra_orgs):
        conn.execute(text(
            "INSERT INTO organization (id, slug, display_name) VALUES "
            "(gen_random_uuid(), :s, 'crm-t1 extra')"),
            {"s": f"crm-t1-{uuid.uuid4().hex[:8]}"})
    return str(conn.execute(text(
        f"INSERT INTO {OLD_TABLE} (name) VALUES ('Orphan Ltd') RETURNING id"),
    ).scalar_one())


def _nullable(conn, table: str) -> bool:
    return conn.execute(text(
        "SELECT is_nullable FROM information_schema.columns "
        "WHERE table_name = :t AND column_name = 'organization_id'"),
        {"t": table}).scalar_one() == "YES"


def _apply_with_notices(conn) -> list[str]:
    notices: list[str] = []
    raw = conn.connection.dbapi_connection

    def _keep(diag) -> None:
        notices.append(diag.message_primary or "")

    raw.add_notice_handler(_keep)
    try:
        _run(conn, _no_txn(_text(_spine())))
        _run(conn, _no_txn(_text(_tenancy())))
    finally:
        raw.remove_notice_handler(_keep)
    return notices


@_DB_GATE
def test_with_no_clear_owner_the_table_stays_nullable_and_hidden(eng):
    """(a) Many organizations, no `fracktalworks`, rows with no tenant."""
    conn = eng.connect()
    trans = conn.begin()
    try:
        company = _old_shape_with_orphan(conn, extra_orgs=2)
        _hide_slug(conn, "fracktalworks")
        assert conn.execute(text("SELECT count(*) FROM organization")).scalar() > 1

        notices = _apply_with_notices(conn)

        assert _nullable(conn, NEW_TABLE), (
            f"{NEW_TABLE} has orphan rows and no owner, so it must stay nullable"
        )
        assert any(NEW_TABLE in n and "no tenant" in n for n in notices), notices
        # A table with no orphan row is NOT NULL all the same.
        assert not _nullable(conn, "crm_contacts")
        assert _forced(conn, NEW_TABLE) and len(_policies(conn, NEW_TABLE)) == 1
        assert conn.execute(text(
            f"SELECT organization_id FROM {NEW_TABLE} WHERE id = CAST(:c AS uuid)"),
            {"c": company}).scalar_one() is None

        # A tenant session, as a role that cannot bypass RLS, sees none of it.
        some_org = str(conn.execute(text(
            "SELECT id FROM organization LIMIT 1")).scalar_one())
        conn.execute(text("CREATE ROLE crm_t1_probe NOLOGIN NOSUPERUSER NOBYPASSRLS"))
        conn.execute(text(f"GRANT SELECT ON {NEW_TABLE} TO crm_t1_probe"))
        conn.execute(text("SET LOCAL ROLE crm_t1_probe"))
        conn.execute(text("SELECT set_config('app.tenant_id', :o, true)"),
                     {"o": some_org})
        seen = conn.execute(text(
            f"SELECT count(*) FROM {NEW_TABLE} WHERE id = CAST(:c AS uuid)"),
            {"c": company}).scalar_one()
        conn.execute(text("RESET ROLE"))
        assert seen == 0, "a tenant session can see a row with no tenant"
    finally:
        trans.rollback()
        # After the bound transaction ends, `app.tenant_id` reads '' on this
        # session, and the column DEFAULT then fails to cast it. So the
        # pooled connection must not serve the next arm.
        conn.invalidate()
        conn.close()


@_DB_GATE
def test_with_fracktalworks_the_orphans_are_its_rows_and_not_null(eng):
    """(b) `fracktalworks` exists, among many organizations."""
    conn = eng.connect()
    trans = conn.begin()
    try:
        company = _old_shape_with_orphan(conn, extra_orgs=2)
        conn.execute(text(
            "INSERT INTO organization (id, slug, display_name) "
            "VALUES (gen_random_uuid(), 'fracktalworks', 'Fracktal Works') "
            "ON CONFLICT (slug) DO NOTHING"))
        owner = str(conn.execute(text(
            "SELECT id FROM organization WHERE slug = 'fracktalworks'")).scalar_one())

        _apply_with_notices(conn)

        for table in CRM_TABLES:
            assert not _nullable(conn, table), f"{table}.organization_id is nullable"
        assert str(conn.execute(text(
            f"SELECT organization_id FROM {NEW_TABLE} WHERE id = CAST(:c AS uuid)"),
            {"c": company}).scalar_one()) == owner
    finally:
        trans.rollback()
        conn.close()


@_DB_GATE
def test_with_exactly_one_organization_it_owns_the_orphans(eng):
    """(c) A fresh install: one organization, which migration 130 seeds."""
    conn = eng.connect()
    trans = conn.begin()
    try:
        company = _old_shape_with_orphan(conn, extra_orgs=0)
        _hide_slug(conn, "fracktalworks")
        keep = str(conn.execute(text(
            "SELECT id FROM organization ORDER BY created_at LIMIT 1")).scalar_one())
        # Every foreign key to `organization` cascades, and the arm rolls back.
        conn.execute(text("DELETE FROM organization WHERE id <> CAST(:k AS uuid)"),
                     {"k": keep})
        assert conn.execute(text("SELECT count(*) FROM organization")).scalar() == 1

        _apply_with_notices(conn)

        for table in CRM_TABLES:
            assert not _nullable(conn, table), f"{table}.organization_id is nullable"
        assert str(conn.execute(text(
            f"SELECT organization_id FROM {NEW_TABLE} WHERE id = CAST(:c AS uuid)"),
            {"c": company}).scalar_one()) == keep
    finally:
        trans.rollback()
        conn.close()


# -- (f) A replay ------------------------------------------------------------


@_DB_GATE
def test_applying_the_crm_migrations_again_changes_nothing(eng):
    files = [_spine(), _sync(), _discipline(), _tenancy()]
    conn = eng.connect()
    trans = conn.begin()
    try:
        before = _shape(conn)
        for _ in range(2):
            for path in files:
                _run(conn, _no_txn(_text(path)))
        after = _shape(conn)
        assert after == before, {
            key: (sorted(set(before[key]) ^ set(after[key])))
            for key in before if before[key] != after[key]
        }
        assert _exists(conn, NEW_TABLE) and not _exists(conn, OLD_TABLE)
    finally:
        trans.rollback()
        conn.close()


# -- (g) The production shape: the promoted catalog --------------------------


@_DB_GATE
def test_144_and_241_apply_again_on_the_promoted_catalog(promoted):  # noqa: F811
    """The ladder AND all four generated phases, as on production. A second
    run of 144 and 241 must neither fail nor add a key, an index or a
    policy."""
    conn = promoted.admin_engine.connect()
    trans = conn.begin()
    try:
        before = _shape(conn)
        _run(conn, _no_txn(_text(_spine())))
        _run(conn, _no_txn(_text(_tenancy())))
        after = _shape(conn)
        assert after == before, {
            key: (sorted(set(before[key]) ^ set(after[key])))
            for key in before if before[key] != after[key]
        }
        for table in CRM_TABLES:
            assert _forced(conn, table), table
            assert len(_policies(conn, table)) == 1, (
                f"{table} has policies {_policies(conn, table)}: phase 4 and "
                "241 disagree about the policy name"
            )
    finally:
        trans.rollback()
        conn.close()


# == R8 — two tenants, the real code, a role that cannot bypass RLS ==========


def _assert_non_priv(app_eng) -> None:
    with app_eng.connect() as c:
        role = c.execute(text(
            "SELECT rolsuper, rolbypassrls FROM pg_roles "
            "WHERE rolname = current_user")).first()
    assert role is not None and not role[0] and not role[1], (
        "this suite connects as a SUPERUSER/BYPASSRLS role, so RLS is bypassed"
    )


def _app_dsn(ns) -> str:
    return ns.app_url.render_as_string(hide_password=False)


def _purge(admin_engine, table: str, column: str, value: str) -> None:
    with admin_engine.begin() as c:
        c.execute(text(f"DELETE FROM {table} WHERE {column} = :v"), {"v": value})


@_DB_GATE
class TestPerTenantKeys:
    """Acceptance 4. One Zoho id, one stage name, one cursor module in two
    organizations gives two rows. A second write in one organization updates
    its own row."""

    async def test_one_zoho_id_is_a_row_in_each_organization(
        self, promoted, app_engine,  # noqa: F811
    ):
        from acb_common.db import tenant_session
        from gateway.routes.crm import core

        _assert_non_priv(app_engine)
        p = promoted
        zoho = f"z-{uuid.uuid4().hex[:10]}"
        try:
            async with tenant_engine_scope(_app_dsn(p)):
                async with tenant_session(p.org_a) as db:
                    a = await core.upsert_by_zoho_id(
                        db, NEW_TABLE, {"name": "Acme", "zoho_id": zoho, "source": "import"})
                async with tenant_session(p.org_b) as db:
                    b = await core.upsert_by_zoho_id(
                        db, NEW_TABLE, {"name": "Acme B", "zoho_id": zoho, "source": "import"})
                async with tenant_session(p.org_a) as db:
                    a2 = await core.upsert_by_zoho_id(
                        db, NEW_TABLE, {"name": "Acme 2", "zoho_id": zoho, "source": "import"})
            assert str(a.organization_id) == p.org_a
            assert str(b.organization_id) == p.org_b
            assert a.id != b.id, "org B overwrote the row of org A"
            assert a2.id == a.id and a2.name == "Acme 2", (
                "a second upsert in org A made a new row rather than updating"
            )
            with p.admin_engine.connect() as c:
                rows = c.execute(text(
                    f"SELECT organization_id::text, name FROM {NEW_TABLE} "
                    "WHERE zoho_id = :z ORDER BY name"), {"z": zoho}).all()
            assert sorted(rows) == sorted([(p.org_a, "Acme 2"), (p.org_b, "Acme B")])
        finally:
            _purge(p.admin_engine, NEW_TABLE, "zoho_id", zoho)

    async def test_one_stage_name_is_a_lane_in_each_organization(
        self, promoted, app_engine,  # noqa: F811
    ):
        from acb_common.db import tenant_session
        from gateway.routes.crm import import_zoho

        _assert_non_priv(app_engine)
        p = promoted
        name = f"Stage {uuid.uuid4().hex[:8]}"
        try:
            async with tenant_engine_scope(_app_dsn(p)):
                made = {}
                for org in (p.org_a, p.org_b, p.org_a):
                    async with tenant_session(org) as db:
                        made.setdefault(org, []).append(await import_zoho.ensure_status(
                            db, "crm_deal_statuses", name, with_probability=True))
            (a_id, a_new), (a_again, a_again_new) = made[p.org_a]
            [(b_id, b_new)] = made[p.org_b]
            assert a_new and b_new and a_id != b_id
            assert a_again == a_id and not a_again_new
            with p.admin_engine.connect() as c:
                orgs = c.execute(text(
                    "SELECT organization_id::text FROM crm_deal_statuses "
                    "WHERE name = :n"), {"n": name}).scalars().all()
            assert sorted(orgs) == sorted([p.org_a, p.org_b])
        finally:
            _purge(p.admin_engine, "crm_deal_statuses", "name", name)

    async def test_one_module_is_a_cursor_in_each_organization(
        self, promoted, app_engine,  # noqa: F811
    ):
        from acb_common.db import tenant_session
        from gateway.routes.crm import sync_zoho

        _assert_non_priv(app_engine)
        p = promoted
        module = f"Mod{uuid.uuid4().hex[:8]}"
        try:
            async with tenant_engine_scope(_app_dsn(p)):
                for org, status in ((p.org_a, "a1"), (p.org_b, "b1"), (p.org_a, "a2")):
                    async with tenant_session(org) as db:
                        await sync_zoho.write_cursor(
                            db, module, pulled_at=None, status=status)
            with p.admin_engine.connect() as c:
                rows = c.execute(text(
                    "SELECT organization_id::text, last_status FROM crm_sync_cursors "
                    "WHERE module = :m"), {"m": module}).all()
            assert sorted(rows) == sorted([(p.org_a, "a2"), (p.org_b, "b1")])
        finally:
            _purge(p.admin_engine, "crm_sync_cursors", "module", module)


@_DB_GATE
async def test_a_body_organization_id_never_picks_the_tenant(
    promoted, app_engine, monkeypatch,  # noqa: F811
):
    """Acceptance 5, R5 and ``user_management_contract.md`` R11. A member of
    org A posts a contact with ``organization_id`` = org B in the body. The row
    belongs to org A, and the response carries no ``organization_id``."""
    import httpx
    from acb_auth import get_current_user
    from acb_common.db import bind_tenant, clear_tenant, release_tenant
    from fastapi import FastAPI
    from gateway.routes import crm as crm_package

    from tests.unit._crm_fakes import crm_switch_on, crm_user

    _assert_non_priv(app_engine)
    p = promoted
    crm_switch_on(monkeypatch, p.org_a)
    user = crm_user(email="r5@crm-t1.test", organization_id=p.org_a)

    async def _caller():
        # What `_with_resolved_access` does for a real request: the tenant
        # comes from the authenticated session and from nothing else.
        bind_tenant(p.org_a)
        return user

    app = FastAPI()
    app.include_router(crm_package.router)
    app.dependency_overrides[get_current_user] = _caller
    marker = f"r5-{uuid.uuid4().hex[:8]}@crm-t1.test"
    # The transport runs the app in this task, so the dependency's binding
    # would outlive the request. This scope ends it with the test.
    scope = clear_tenant()
    try:
        async with tenant_engine_scope(_app_dsn(p)):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://crm.test",
            ) as client:
                resp = await client.post("/crm/contacts", json={
                    "first_name": "Rae", "email": marker,
                    "organization_id": p.org_b,
                })
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert "organization_id" not in body, (
            "the contact response names the tenant. The company is company_id."
        )
        assert "company_id" in body
        with p.admin_engine.connect() as c:
            stored = c.execute(text(
                "SELECT organization_id::text FROM crm_contacts WHERE email = :e"),
                {"e": marker}).scalars().all()
        assert stored == [p.org_a], (
            f"the body chose the tenant: stored {stored}, caller is org A"
        )
    finally:
        release_tenant(scope)
        _purge(p.admin_engine, "crm_contacts", "email", marker)
