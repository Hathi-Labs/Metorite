"""WS-PKG PKG-4 — the package registry tables on a real database (R8).

Spec: ``project-docs/specs/package_host.md`` §5.1 and the PKG-4 row of §10.
Migration: the file that creates ``package_install`` (321 at build). This
slice adds the tables only. No store writes them yet (PKG-5), so each write
here is raw SQL as the app role, through ``acb_common.db.tenant_session``.

The rows live on a phase-4 promoted scratch database: the full ladder, then
the four generated phases. The fixture builds it from an empty database, so
it also proves that the migration applies on a fresh ladder, where ``apps``
has no ``organization_id`` until this file adds it. Two organizations, two
members, two apps in each org.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_package_registry_r8.py -v -rs -p no:xdist

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.

**Acceptance (PKG-4).**

a. The migration applies. Both tables have ENABLE and FORCE RLS and one
   policy with USING and WITH CHECK. ``apps`` holds UNIQUE (organization_id,
   id). A second run of the file changes nothing in the catalog.
b. Org B reads, changes and deletes none of org A's rows, in each table, as
   the app role and as a table owner that is not a superuser.
c. A session of the app role with no tenant bound reads no row, and writes
   none.
d. WITH CHECK refuses a row stamped with another org, in each table.
e. A contribution cannot point at an install of another org, and an install
   cannot point at an app of another org (the composite foreign keys).
f. A delete of an install deletes its contributions. A delete of an app
   deletes its installs and their contributions.
g. Each CHECK of §5.1 refuses a bad row, and a good row at each edge passes:
   the scope kinds, the shape of each scope subject, a lower-case
   ``installed_by``, the owner as the installer of a private install, a NULL
   version only for ``private``, ``archived_at`` with the state, the 13
   points of §4 and the payload size. The private address takes each value
   that ``normalize_grant_subject`` gives, as the CHECK of 272 does.
h. One app has one install at each scope.
i. The member purge deletes the member's private installs of this org that
   nobody else sees, and their contributions. It keeps and counts a private
   install of an app that the member shared with another person (decision
   3), and the group and org installs that the member made. The three
   clauses split the member's rows with no overlap and no gap. The rows of
   another member and of another org stay, also as the superuser.

**R7: red first, then mutate.** Each case below was red before its object
existed. Commit before each mutation, and restore with git after.

* M1. The migration and ``generated/04_policies.sql``: drop the line
  ``ALTER TABLE package_contribution FORCE  ROW LEVEL SECURITY``.
  ``test_the_migration_holds_the_keys_the_checks_and_the_rls``,
  ``test_r8_both_tables_have_enable_force_and_one_policy`` and the
  ``owner`` case of ``test_r8_org_b_reads_and_writes_none_of_org_a`` go red.
  The ``app`` case stays green: the app role does not own the table, and
  ENABLE alone binds a role that is not the owner. FORCE binds the owner.
* M1b. Drop the ENABLE and the FORCE lines of ``package_contribution``, in
  both files. Both cases of the org-B test go red, and so do the unbound
  test and the WITH CHECK test.
* M2. The migration: make ``package_contribution_install_fk`` a key on
  ``install_id`` alone, to ``package_install (id)``.
  ``test_r8_a_contribution_cannot_point_at_an_install_of_another_org`` goes
  red, because a foreign key check skips RLS.
* M3. ``routes/admin/members.py``: drop ``scope_kind <> 'private' AND`` from
  the ``shared_app_installs`` row. Both cases of
  ``test_r8_the_member_purge_takes_private_installs_and_keeps_shared_ones``
  go red (3 kept, not 2), and so do three tests of
  ``test_admin_member_purge.py``. The same drop on the delete row stays
  green here: the CHECK on ``scope_subject`` lets only a private install
  hold an address. The exact-text test of ``test_admin_member_purge.py``
  is its fence.
* M4. ``routes/admin/members.py``: drop the org predicate of the
  ``private_app_installs`` row. The ``superuser`` case of the purge test
  goes red, because org B's private install of the same address goes too.
  The ``app`` case stays green, because RLS still binds the app role.
* M5. ``routes/admin/members.py``: drop ``AND NOT {_OTHERS_SEE_THE_APP}``
  from the ``private_app_installs`` row. Both cases of
  ``test_r8_a_private_install_that_she_shared_stays_and_is_counted`` go
  red: the delete counts 2, and the shared install is gone.

Each mutation ran on 2026-11-01 and gave the result above.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_common.db import get_session_factory, tenant_session
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION = next(
    p for p in (_ROOT / "infra" / "postgres").glob("*.sql")
    if "CREATE TABLE IF NOT EXISTS package_install (" in p.read_text(encoding="utf-8"))
_POLICIES = _ROOT / "infra" / "postgres" / "generated" / "04_policies.sql"
_SPEC = _ROOT / "project-docs" / "specs" / "package_host.md"

TABLES = ("package_install", "package_contribution")

TAG = uuid.uuid4().hex[:8]
#: The two members of this suite. The tag lets teardown find their apps.
ANA = f"ana.{TAG}@pkg4.test"
BEN = f"ben.{TAG}@pkg4.test"
_SLUG_LIKE = f"pkg4-{TAG}-%"

#: A table owner that is not a superuser. Suite-private name, as the app role
#: of the promoted fixture is. Only FORCE binds the owner of a table.
_OWNER_ROLE = "pkg4_owner_r8"
_OWNER_PW = "pkg4_owner_scratch_pw"

#: A time for `archived_at`. The seam's driver (asyncpg) takes a datetime,
#: never a string, for a timestamptz parameter.
_WHEN = datetime(2026, 11, 1, tzinfo=UTC)


def _dsn(url: Any) -> str:
    return url.render_as_string(hide_password=False)


@pytest.fixture
def world(promoted):  # noqa: F811
    """Two apps in each org, seeded as the superuser. Teardown deletes the
    apps, and the installs and their contributions go by cascade."""
    p = promoted
    apps: dict[str, str] = {}
    with p.admin_engine.begin() as c:
        for tag, org in (("a", p.org_a), ("b", p.org_b)):
            for n in (1, 2):
                apps[f"{tag}{n}"] = str(c.execute(text(
                    "INSERT INTO apps (slug, name, owner_email, workspace_path, "
                    "organization_id) VALUES (:s, 'pkg4', :o, :w, :org) RETURNING id"),
                    {"s": f"pkg4-{TAG}-{tag}{n}", "o": ANA, "w": f"/tmp/pkg4-{tag}{n}",
                     "org": org}).scalar_one())
    yield SimpleNamespace(p=p, apps=apps)
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM apps WHERE slug LIKE :s"), {"s": _SLUG_LIKE})


def _go(url: Any, fn: Any) -> Any:
    """Run ``fn()`` in a new event loop, on the engine of *url*."""
    async def go() -> Any:
        async with tenant_engine_scope(_dsn(url)):
            return await fn()
    return asyncio.run(go())


_INSTALL_SQL = (
    "INSERT INTO package_install (app_id, version, scope_kind, scope_subject, "
    "installed_by, state, archived_at) VALUES (CAST(:app AS uuid), :v, :kind, "
    ":subject, :by, :state, CAST(:archived AS timestamptz)) RETURNING id")

_CONTRIB_SQL = (
    "INSERT INTO package_contribution (install_id, point, key, payload) "
    "VALUES (CAST(:install AS uuid), :point, :key, CAST(:payload AS jsonb))")


def _install_params(app: str, **kw: Any) -> dict[str, Any]:
    return {"app": app, "v": 1, "kind": "org", "subject": "org", "by": ANA,
            "state": "active", "archived": None, **kw}


def _contrib_params(install: str, **kw: Any) -> dict[str, Any]:
    return {"install": install, "point": "jobs", "key": f"job-{uuid.uuid4().hex[:6]}",
            "payload": "{}", **kw}


async def _install(org: str, app: str, **kw: Any) -> str:
    """One install as the app role, bound to *org*. The org comes from the
    column DEFAULT, never from the statement."""
    async with tenant_session(org) as db:
        return str((await db.execute(text(_INSTALL_SQL), _install_params(app, **kw))).scalar_one())


async def _contrib(org: str, install: str, **kw: Any) -> None:
    async with tenant_session(org) as db:
        await db.execute(text(_CONTRIB_SQL), _contrib_params(install, **kw))


def _seed(world: Any, org_tag: str, contributions: int = 2, **kw: Any) -> str:
    """One install on the first app of the org, with its contributions."""
    org = world.p.org_a if org_tag == "a" else world.p.org_b
    app = world.apps[f"{org_tag}1"]

    async def go() -> str:
        install = await _install(org, app, **kw)
        for _ in range(contributions):
            await _contrib(org, install)
        return install

    return _go(world.p.app_url, go)


def _admin_scalar(world: Any, sql: str, params: dict[str, Any] | None = None) -> Any:
    """A read as the superuser, which RLS does not bind."""
    with world.p.admin_engine.connect() as c:
        return c.execute(text(sql), params or {}).scalar_one()


def _contributions_of(world: Any, install: str) -> int:
    return int(_admin_scalar(
        world, "SELECT count(*) FROM package_contribution "
               "WHERE install_id = CAST(:i AS uuid)", {"i": install}))


def _installs_of_suite(world: Any) -> int:
    return int(_admin_scalar(
        world, "SELECT count(*) FROM package_install i JOIN apps a ON a.id = i.app_id "
               "WHERE a.slug LIKE :s", {"s": _SLUG_LIKE}))


def _refused(world: Any, org: str, sql: str, params: dict[str, Any]) -> str:
    """Run *sql* as the app role bound to *org*. Answers the error text."""
    async def go() -> None:
        async with tenant_session(org) as db:
            await db.execute(text(sql), params)

    with pytest.raises(DBAPIError) as exc:
        _go(world.p.app_url, go)
    return str(exc.value)


def _code(sql: str) -> str:
    """The SQL with its ``--`` comments removed."""
    return re.sub(r"--[^\n]*", "", sql)


def _spec_points() -> list[str]:
    """The points of the table in ``package_host.md`` §4, in order."""
    section = _SPEC.read_text(encoding="utf-8").split(
        "## 4. Contribution points", 1)[1].split("\n## 5.", 1)[0]
    return re.findall(r"^\| `([a-z]+)` \|", section, re.M)


# ── a. The schema and the catalog ──────────────────────────────────────────


def test_the_migration_holds_the_keys_the_checks_and_the_rls() -> None:
    sql = _MIGRATION.read_text(encoding="utf-8")
    code = _code(sql)
    policies = _POLICIES.read_text(encoding="utf-8")
    for table in TABLES:
        assert f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" in code
        assert f"ALTER TABLE {table} FORCE  ROW LEVEL SECURITY" in code
        assert f"ALTER TABLE {table} FORCE  ROW LEVEL SECURITY" in policies
        assert f"CREATE POLICY {table}_tenant_isolation ON {table}" in code
    assert code.count(
        "WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid)") == 2
    # The key on apps, and the two composite foreign keys that it serves.
    assert "ADD CONSTRAINT apps_org_id_key UNIQUE (organization_id, id)" in code
    assert "FOREIGN KEY (organization_id, app_id)" in code
    assert "REFERENCES apps (organization_id, id) ON DELETE CASCADE" in code
    assert "FOREIGN KEY (organization_id, install_id)" in code
    assert "REFERENCES package_install (organization_id, id) ON DELETE CASCADE" in code
    assert "PRIMARY KEY (organization_id, install_id, point, key)" in code
    assert "UNIQUE (organization_id, app_id, scope_kind, scope_subject)" in code
    assert "WHERE ends_at IS NOT NULL" in code
    assert "SET LOCAL lock_timeout" in code
    assert code.lstrip().startswith("BEGIN;") and code.rstrip().endswith("COMMIT;")
    # Expand only (R6): no drop, no rename, and an existing table changes
    # only on `apps`, by one new column (where it is missing) and one key.
    assert not re.search(r"\b(DROP\s+(TABLE|COLUMN|CONSTRAINT)|RENAME)\b", code, re.I)
    altered = set(re.findall(r"ALTER TABLE\s+([a-z_]+)", code))
    assert altered == {"apps", *TABLES}, altered
    apps_changes = [" ".join(m.split())
                    for m in re.findall(r"ALTER TABLE apps\s+(ADD [^(]+)\(", code)]
    assert apps_changes == [
        "ADD COLUMN IF NOT EXISTS organization_id UUID REFERENCES organization",
        "ADD CONSTRAINT apps_org_id_key UNIQUE",
    ]
    # The point CHECK holds exactly the 13 points of §4, in the spec's order.
    check = code.split("CONSTRAINT package_contribution_point CHECK (point IN (", 1)[1]
    points = re.findall(r"'([a-z]+)'", check.split("))", 1)[0])
    assert points == _spec_points()
    assert len(points) == 13
    assert "octet_length(payload::text) <= 65536" in code
    # The owner of a private install is its installer, so the purge finds
    # each row of a member (finding 2 of the PKG-4 review).
    assert "scope_kind <> 'private' OR installed_by = scope_subject" in code
    # The address shape of 272, which normalize_grant_subject matches.
    assert "scope_subject ~ '^[^@[:space:]]+@[^@[:space:]]+$'" in code
    assert "scope_subject !~ '^group:'" in code
    assert "gtd_" not in code


def test_r8_both_tables_have_enable_force_and_one_policy(world) -> None:
    with world.p.admin_engine.connect() as c:
        rows = c.execute(text(
            "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE relname IN ('package_install', 'package_contribution') "
            "ORDER BY relname")).all()
        policies = c.execute(text(
            "SELECT tablename, policyname, qual IS NOT NULL, with_check IS NOT NULL "
            "FROM pg_policies WHERE tablename IN ('package_install', "
            "'package_contribution') ORDER BY tablename")).all()
    assert [tuple(r) for r in rows] == [
        ("package_contribution", True, True), ("package_install", True, True)]
    assert [tuple(r) for r in policies] == [
        ("package_contribution", "package_contribution_tenant_isolation", True, True),
        ("package_install", "package_install_tenant_isolation", True, True)]


def test_r8_apps_holds_the_key_of_the_install_foreign_key(world) -> None:
    with world.p.admin_engine.connect() as c:
        key = c.execute(text(
            "SELECT contype, pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid = 'apps'::regclass AND conname = 'apps_org_id_key'")).one()
        fks = c.execute(text(
            "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE contype = 'f' AND conrelid IN ('package_install'::regclass, "
            "'package_contribution'::regclass) AND conname LIKE '%\\_fk' "
            "AND conname NOT LIKE '%org\\_fk' ORDER BY conname")).all()
    assert tuple(key) == ("u", "UNIQUE (organization_id, id)")
    assert [tuple(r) for r in fks] == [
        ("package_contribution_install_fk",
         "FOREIGN KEY (organization_id, install_id) REFERENCES "
         "package_install(organization_id, id) ON DELETE CASCADE"),
        ("package_install_app_fk",
         "FOREIGN KEY (organization_id, app_id) REFERENCES "
         "apps(organization_id, id) ON DELETE CASCADE"),
    ]


_CATALOG_SQL = """
    SELECT 'con' AS kind, conrelid::regclass::text AS rel, conname AS name,
           pg_get_constraintdef(oid) AS def
      FROM pg_constraint
     WHERE conrelid IN ('apps'::regclass, 'package_install'::regclass,
                        'package_contribution'::regclass)
    UNION ALL
    SELECT 'idx', tablename, indexname, indexdef FROM pg_indexes
     WHERE tablename IN ('apps', 'package_install', 'package_contribution')
    UNION ALL
    SELECT 'pol', tablename, policyname, coalesce(qual, '') || '|' || coalesce(with_check, '')
      FROM pg_policies
     WHERE tablename IN ('apps', 'package_install', 'package_contribution')
    UNION ALL
    SELECT 'rls', relname, relrowsecurity::text, relforcerowsecurity::text FROM pg_class
     WHERE relname IN ('apps', 'package_install', 'package_contribution')
    UNION ALL
    SELECT 'col', table_name, column_name,
           data_type || '|' || is_nullable || '|' || coalesce(column_default, '')
      FROM information_schema.columns
     WHERE table_name IN ('apps', 'package_install', 'package_contribution')
     ORDER BY 1, 2, 3
"""


def test_r8_a_second_run_of_the_migration_changes_nothing(world) -> None:
    """The deploy replays the ladder, and CI replays it three times. A second
    run of the file must find each object and change none of them."""
    seeded = _seed(world, "a")
    with world.p.admin_engine.connect() as c:
        before = [tuple(r) for r in c.execute(text(_CATALOG_SQL)).all()]
    auto = world.p.admin_engine.execution_options(isolation_level="AUTOCOMMIT")
    with auto.connect() as c, c.connection.dbapi_connection.cursor() as cur:
        cur.execute(_MIGRATION.read_text(encoding="utf-8"))
    with world.p.admin_engine.connect() as c:
        after = [tuple(r) for r in c.execute(text(_CATALOG_SQL)).all()]
    assert after == before
    assert len([r for r in before if r[0] == "con" and r[1] == "apps"
                and r[3].startswith("FOREIGN KEY (organization_id)")]) == 2, (
        "apps holds the inline key of the ladder and apps_org_fk of phase 3")
    assert _contributions_of(world, seeded) == 2


# ── b, c, d. Two orgs and an unbound session ───────────────────────────────


@pytest.fixture
def owner_url(world):
    """A role that is not a superuser and owns both tables. FORCE is the only
    thing that binds it. Teardown gives the tables back to their owner."""
    with world.p.admin_engine.connect() as c:
        db_name = c.execute(text("SELECT current_database()")).scalar_one()
        old_owner = c.execute(text(
            "SELECT tableowner FROM pg_tables WHERE tablename = 'package_install'")).scalar_one()
    auto = create_engine(world.p.admin_engine.url, isolation_level="AUTOCOMMIT")
    with auto.connect() as c:
        c.execute(text(
            f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE "
            f"rolname = '{_OWNER_ROLE}') THEN CREATE ROLE {_OWNER_ROLE} LOGIN; "
            f"END IF; END $$;"))
        c.execute(text(
            f"ALTER ROLE {_OWNER_ROLE} WITH LOGIN PASSWORD '{_OWNER_PW}' "
            f"NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS"))
        c.execute(text(f'GRANT CONNECT ON DATABASE "{db_name}" TO {_OWNER_ROLE}'))
        c.execute(text(f"GRANT USAGE ON SCHEMA public TO {_OWNER_ROLE}"))
        for table in TABLES:
            c.execute(text(f"ALTER TABLE {table} OWNER TO {_OWNER_ROLE}"))
    try:
        yield world.p.app_url.set(username=_OWNER_ROLE, password=_OWNER_PW)
    finally:
        with auto.connect() as c:
            for table in TABLES:
                c.execute(text(f'ALTER TABLE {table} OWNER TO "{old_owner}"'))
            c.execute(text(f"REVOKE USAGE ON SCHEMA public FROM {_OWNER_ROLE}"))
            c.execute(text(f'REVOKE CONNECT ON DATABASE "{db_name}" FROM {_OWNER_ROLE}'))
        auto.dispose()


@pytest.mark.parametrize("role", ["app", "owner"])
def test_r8_org_b_reads_and_writes_none_of_org_a(world, request, role: str) -> None:
    install = _seed(world, "a")
    url = world.p.app_url if role == "app" else request.getfixturevalue("owner_url")
    b = world.p.org_b

    async def go() -> dict[str, int]:
        out: dict[str, int] = {}
        async with tenant_session(b) as db:
            p = {"i": install}
            out["installs"] = (await db.execute(text(
                "SELECT count(*) FROM package_install WHERE id = CAST(:i AS uuid)"),
                p)).scalar_one()
            out["claims"] = (await db.execute(text(
                "SELECT count(*) FROM package_contribution "
                "WHERE install_id = CAST(:i AS uuid)"), p)).scalar_one()
            out["paused"] = (await db.execute(text(
                "UPDATE package_install SET state = 'paused' "
                "WHERE id = CAST(:i AS uuid)"), p)).rowcount
            out["moved"] = (await db.execute(text(
                "UPDATE package_contribution SET position = 9 "
                "WHERE install_id = CAST(:i AS uuid)"), p)).rowcount
            out["claims_gone"] = (await db.execute(text(
                "DELETE FROM package_contribution "
                "WHERE install_id = CAST(:i AS uuid)"), p)).rowcount
            out["install_gone"] = (await db.execute(text(
                "DELETE FROM package_install WHERE id = CAST(:i AS uuid)"), p)).rowcount
        return out

    assert _go(url, go) == {"installs": 0, "claims": 0, "paused": 0, "moved": 0,
                            "claims_gone": 0, "install_gone": 0}
    assert _admin_scalar(world, "SELECT state FROM package_install "
                                "WHERE id = CAST(:i AS uuid)", {"i": install}) == "active"
    assert _contributions_of(world, install) == 2
    assert _admin_scalar(world, "SELECT max(position) FROM package_contribution "
                                "WHERE install_id = CAST(:i AS uuid)", {"i": install}) == 0


def test_r8_an_unbound_session_reads_and_writes_none(world) -> None:
    install = _seed(world, "a")

    async def go() -> dict[str, Any]:
        out: dict[str, Any] = {}
        session = get_session_factory()()
        try:
            for table in TABLES:
                out[table] = (await session.execute(text(
                    f"SELECT count(*) FROM {table}"))).scalar_one()
            await session.execute(text(_INSTALL_SQL), _install_params(world.apps["a2"]))
        except DBAPIError as exc:
            out["write"] = str(exc)
        finally:
            await session.rollback()
            await session.close()
        return out

    out = _go(world.p.app_url, go)
    assert out["package_install"] == 0 and out["package_contribution"] == 0
    # The DEFAULT reads an unset tenant as NULL, so the row has no org, and
    # the policy refuses it before the NOT NULL check runs.
    assert 'row-level security policy for table "package_install"' in out["write"]
    # The positive control: the superuser sees the row that the app role missed.
    assert _contributions_of(world, install) == 2


def test_r8_with_check_refuses_a_row_stamped_with_another_org(world) -> None:
    """Bound to org A, an insert that names org B is refused by the policy,
    in each table. The foreign keys would pass: each target is a real row of
    org B."""
    b_install = _seed(world, "b", contributions=0)
    a, b = world.p.org_a, world.p.org_b
    install_err = _refused(world, a, (
        "INSERT INTO package_install (organization_id, app_id, version, scope_kind, "
        "scope_subject, installed_by) VALUES (CAST(:org AS uuid), CAST(:app AS uuid), "
        "1, 'org', 'org', :by)"), {"org": b, "app": world.apps["b2"], "by": ANA})
    claim_err = _refused(world, a, (
        "INSERT INTO package_contribution (organization_id, install_id, point, key) "
        "VALUES (CAST(:org AS uuid), CAST(:i AS uuid), 'jobs', 'stamped')"),
        {"org": b, "i": b_install})
    assert 'row-level security policy for table "package_install"' in install_err
    assert 'row-level security policy for table "package_contribution"' in claim_err
    assert _contributions_of(world, b_install) == 0
    assert _installs_of_suite(world) == 1


# ── e. The composite foreign keys ──────────────────────────────────────────


def test_r8_a_contribution_cannot_point_at_an_install_of_another_org(world) -> None:
    """A foreign key check skips RLS, so the pair (org, install) is the fence."""
    b_install = _seed(world, "b", contributions=0)
    err = _refused(world, world.p.org_a, _CONTRIB_SQL, _contrib_params(b_install))
    assert "package_contribution_install_fk" in err
    assert _contributions_of(world, b_install) == 0


def test_r8_an_install_cannot_point_at_an_app_of_another_org(world) -> None:
    err = _refused(world, world.p.org_a, _INSTALL_SQL, _install_params(world.apps["b1"]))
    assert "package_install_app_fk" in err
    assert _installs_of_suite(world) == 0


# ── f. The cascades ────────────────────────────────────────────────────────


def test_r8_deleting_an_install_deletes_its_contributions(world) -> None:
    install = _seed(world, "a", contributions=3)
    keep = _seed(world, "a", contributions=2, kind="group", subject="group:ops")
    assert _contributions_of(world, install) == 3

    async def go() -> int:
        async with tenant_session(world.p.org_a) as db:
            return (await db.execute(text(
                "DELETE FROM package_install WHERE id = CAST(:i AS uuid)"),
                {"i": install})).rowcount

    assert _go(world.p.app_url, go) == 1
    assert _contributions_of(world, install) == 0
    assert _contributions_of(world, keep) == 2


def test_r8_deleting_an_app_deletes_its_installs_and_their_contributions(world) -> None:
    one = _seed(world, "a")
    two = _seed(world, "a", kind="private", subject=ANA, v=None)
    other_app = world.apps["a2"]
    other = _go(world.p.app_url, lambda: _install(world.p.org_a, other_app))
    assert _installs_of_suite(world) == 3

    async def go() -> int:
        async with tenant_session(world.p.org_a) as db:
            return (await db.execute(text(
                "DELETE FROM apps WHERE id = CAST(:i AS uuid)"),
                {"i": world.apps["a1"]})).rowcount

    assert _go(world.p.app_url, go) == 1
    assert _installs_of_suite(world) == 1
    assert _contributions_of(world, one) == 0 and _contributions_of(world, two) == 0
    assert _admin_scalar(world, "SELECT count(*) FROM package_install "
                                "WHERE id = CAST(:i AS uuid)", {"i": other}) == 1


# ── g. The CHECKs ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(("params", "constraint"), [
    ({"kind": "team", "subject": "org"}, "package_install_scope_kind"),
    ({"kind": "org", "subject": "group:ops"}, "package_install_scope_subject"),
    ({"kind": "org", "subject": "ORG"}, "package_install_scope_subject"),
    ({"kind": "group", "subject": "org"}, "package_install_scope_subject"),
    ({"kind": "group", "subject": "ops"}, "package_install_scope_subject"),
    ({"kind": "group", "subject": "group:"}, "package_install_scope_subject"),
    ({"kind": "group", "subject": "group: ops"}, "package_install_scope_subject"),
    ({"kind": "group", "subject": "group:Ops"}, "package_install_scope_subject"),
    # A private row names its owner as its installer here, so only the
    # subject CHECK can refuse it. An upper-case subject breaks a second
    # CHECK too, so `test_r8_the_subject_check_alone_holds_each_shape` holds it.
    *(({"kind": "private", "subject": s, "by": s}, "package_install_scope_subject")
      for s in ("group:ana@pkg4.test", "group:x@y", "org", "not-an-address",
                "ana @pkg4.test", "ana\t@pkg4.test", "a@b@pkg4.test", "@pkg4.test",
                "ana@")),
    ({"by": "Ana@PKG4.test"}, "package_install_installed_by"),
    # Finding 2 of the PKG-4 review: a private install of Ben that Ana made.
    ({"kind": "private", "subject": BEN, "by": ANA}, "package_install_private_installer"),
    ({"kind": "private", "subject": BEN, "by": ANA, "v": None},
     "package_install_private_installer"),
    ({"v": None}, "package_install_draft_private"),
    ({"v": None, "kind": "group", "subject": "group:ops"}, "package_install_draft_private"),
    ({"state": "deleted"}, "package_install_state"),
    ({"state": "archived"}, "package_install_archived_at"),
    ({"state": "active", "archived": _WHEN}, "package_install_archived_at"),
    ({"state": "paused", "archived": _WHEN}, "package_install_archived_at"),
])
def test_r8_the_install_checks_refuse_a_bad_row(world, params, constraint) -> None:
    err = _refused(world, world.p.org_a, _INSTALL_SQL,
                   _install_params(world.apps["a1"], **params))
    assert constraint in err
    assert _installs_of_suite(world) == 0


@pytest.mark.parametrize("params", [
    {"kind": "private", "subject": "ana@pkg4.test", "by": "ana@pkg4.test", "v": None},
    {"kind": "private", "subject": "ana@pkg4.test", "by": "ana@pkg4.test", "v": 3},
    # Finding 3 of the PKG-4 review: normalize_grant_subject and the CHECK
    # of 272 take a colon in the local part, so this CHECK takes it too.
    {"kind": "private", "subject": "a:b@x.test", "by": "a:b@x.test"},
    # A group or an org install names its installer, who is any member.
    {"kind": "group", "subject": "group:ops", "by": BEN},
    {"kind": "group", "subject": "group:ops"},
    {"kind": "group", "subject": "group:field-visits"},
    {"kind": "org", "subject": "org"},
    {"state": "paused"},
    {"state": "archived", "archived": _WHEN},
])
def test_r8_a_good_install_at_each_edge_passes(world, params) -> None:
    install = _go(world.p.app_url, lambda: _install(world.p.org_a, world.apps["a1"], **params))
    assert _admin_scalar(world, "SELECT organization_id::text FROM package_install "
                                "WHERE id = CAST(:i AS uuid)", {"i": install}) == world.p.org_a


def _subject_check_passes(world: Any, kind: str, subject: str) -> bool:
    """The stored expression of ``package_install_scope_subject``, run on one
    pair of values, with no other CHECK in the way.

    The expression comes from ``pg_get_constraintdef``, so this tests what
    Postgres holds, never a copy. It runs on the DBAPI cursor: SQLAlchemy's
    ``text()`` reads ``[:space:]`` as a bind parameter."""
    with world.p.admin_engine.connect() as c, c.connection.dbapi_connection.cursor() as cur:
        cur.execute(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid = 'package_install'::regclass "
            "AND conname = 'package_install_scope_subject'")
        definition = cur.fetchone()[0]
        assert definition.startswith("CHECK (") and definition.endswith(")"), definition
        cur.execute(
            f"SELECT {definition[len('CHECK '):]} FROM (VALUES (%s::text, %s::text)) "
            "AS t(scope_kind, scope_subject)", (kind, subject))
        return bool(cur.fetchone()[0])


@pytest.mark.parametrize(("kind", "subject", "passes"), [
    # Finding 3 of the PKG-4 review: one `@`, no whitespace, lower case.
    ("private", "a:b@x.test", True),
    ("private", "ana@pkg4.test", True),
    ("private", "group:x@y", False),
    ("private", "org", False),
    ("private", "Ana@PKG4.test", False),
    ("private", "ana@PKG4.test", False),
    ("private", "ana @pkg4.test", False),
    ("private", "a@b@pkg4.test", False),
    ("group", "group:ops", True),
    ("group", "group:Ops", False),
    ("org", "org", True),
    ("org", "ana@pkg4.test", False),
])
def test_r8_the_subject_check_alone_holds_each_shape(world, kind, subject, passes) -> None:
    assert _subject_check_passes(world, kind, subject) is passes


#: Real shapes that a member types, and that a writer (PKG-5) hands to
#: ``normalize_grant_subject`` first (§5.1 decision 2).
_REAL_SUBJECTS = (
    "Ana+Sales@Example.COM",          # a plus sign, and upper case
    "user42@42.example",              # digits
    "Ünal@Bücher.Example",            # an IDN domain, in Unicode
    "mary-jane@my-firm.example",      # a dash on each side
    "o'neil@pkg4.test",               # an apostrophe
    "a:b@x.test",                     # a colon in the local part
    "  Ravi@Fracktal.IN  ",           # spaces around the value
    "group:Field-Visits",             # a dash in a group slug
    "group: Ops",                     # a space after the prefix
    "org",
)


def test_r8_each_value_of_normalize_grant_subject_passes_the_check(world) -> None:
    """Finding 3 of the PKG-4 review. A value that ``normalize_grant_subject``
    gives must pass the CHECK, or PKG-5 answers 500 where it owes 422. Each
    value goes in as a real row, as the app role, with its owner as its
    installer for a private one."""
    from acb_auth.permissions import normalize_grant_subject

    org, app = world.p.org_a, world.apps["a1"]
    rows: dict[str, str] = {}
    for raw in _REAL_SUBJECTS:
        value = normalize_grant_subject(raw)
        kind = ("org" if value == "org"
                else "group" if value.startswith("group:") else "private")
        rows[value] = kind
        assert _subject_check_passes(world, kind, value), (raw, value)

    async def go() -> None:
        for value, kind in rows.items():
            await _install(org, app, kind=kind, subject=value,
                           by=value if kind == "private" else ANA)

    _go(world.p.app_url, go)
    with world.p.admin_engine.connect() as c:
        stored = dict(c.execute(text(
            "SELECT scope_subject, scope_kind FROM package_install "
            "WHERE app_id = CAST(:a AS uuid)"), {"a": app}).all())
    assert stored == rows
    assert "ünal@bücher.example" in stored and "a:b@x.test" in stored


def _payload_of_text_size(size: int) -> str:
    """A JSON object whose jsonb text is exactly *size* bytes."""
    return '{"x": "' + "y" * (size - 9) + '"}'


@pytest.mark.parametrize(("params", "constraint"), [
    ({"point": "menu"}, "package_contribution_point"),
    ({"point": "Jobs"}, "package_contribution_point"),
    ({"payload": _payload_of_text_size(65537)}, "package_contribution_payload_size"),
])
def test_r8_the_contribution_checks_refuse_a_bad_row(world, params, constraint) -> None:
    install = _seed(world, "a", contributions=0)
    err = _refused(world, world.p.org_a, _CONTRIB_SQL, _contrib_params(install, **params))
    assert constraint in err
    assert _contributions_of(world, install) == 0


def test_r8_each_point_of_the_spec_and_a_payload_at_the_limit_pass(world) -> None:
    install = _seed(world, "a", contributions=0)
    org = world.p.org_a

    async def go() -> None:
        for point in _spec_points():
            await _contrib(org, install, point=point, key="one")
        await _contrib(org, install, point="cards", key="big",
                       payload=_payload_of_text_size(65536))

    _go(world.p.app_url, go)
    assert _contributions_of(world, install) == 14
    assert _admin_scalar(world, "SELECT octet_length(payload::text) FROM "
                                "package_contribution WHERE install_id = CAST(:i AS uuid) "
                                "AND key = 'big'", {"i": install}) == 65536


# ── h. One install at each scope ───────────────────────────────────────────


def test_r8_one_app_has_one_install_at_each_scope(world) -> None:
    _seed(world, "a", contributions=0)
    err = _refused(world, world.p.org_a, _INSTALL_SQL, _install_params(world.apps["a1"]))
    assert "package_install_scope_key" in err
    # Another scope of the same app, and the same scope of another app, pass.
    a = world.p.org_a

    async def go() -> None:
        await _install(a, world.apps["a1"], kind="private", subject=ANA, v=None)
        await _install(a, world.apps["a1"], kind="private", subject=BEN, by=BEN, v=None)
        await _install(a, world.apps["a2"])

    _go(world.p.app_url, go)
    assert _installs_of_suite(world) == 4
    install = _seed(world, "a", contributions=0, kind="group", subject="group:ops")
    _go(world.p.app_url, lambda: _contrib(a, install, key="same"))
    err = _refused(world, a, _CONTRIB_SQL, _contrib_params(install, key="same"))
    assert "package_contribution_pkey" in err


# ── i. The member purge ────────────────────────────────────────────────────


@pytest.mark.parametrize("role", ["app", "superuser"])
def test_r8_the_member_purge_takes_private_installs_and_keeps_shared_ones(
    world, role: str,
) -> None:
    """The clauses of ``package_install`` in ``members.py`` on a real
    database. The superuser run skips RLS, so only the org predicate of each
    clause keeps org B's rows out there. No app here has a grant, so each
    private install of Ana goes."""
    ids = {
        # Org A: Ana's private install, the org and group installs she made,
        # and Ben's private install and his org install.
        "ana_private": _seed(world, "a", kind="private", subject=ANA, v=None),
        "ana_org": _seed(world, "a", contributions=1),
        "ana_group": _go(world.p.app_url, lambda: _install(
            world.p.org_a, world.apps["a2"], kind="group", subject="group:ops")),
        "ben_private": _go(world.p.app_url, lambda: _install(
            world.p.org_a, world.apps["a2"], kind="private", subject=BEN, by=BEN)),
        "ben_org": _go(world.p.app_url, lambda: _install(
            world.p.org_a, world.apps["a2"], by=BEN)),
        # Org B: the same address, a private install and an org install.
        "b_private": _seed(world, "b", kind="private", subject=ANA, v=None),
        "b_org": _go(world.p.app_url, lambda: _install(world.p.org_b, world.apps["b2"])),
    }
    url = world.p.app_url if role == "app" else world.p.admin_engine.url

    out = _go(url, lambda: _purge_installs(world.p.org_a))
    assert out.counts == {"private_app_installs": 1, "shared_app_installs": 2,
                          "shared_private_app_installs": 0}
    assert out.deleted == 1
    assert _left(world, ids) == set(ids.values()) - {ids["ana_private"]}
    assert _contributions_of(world, ids["ana_private"]) == 0
    assert _contributions_of(world, ids["ana_org"]) == 1
    assert _contributions_of(world, ids["b_private"]) == 2


@pytest.mark.parametrize("role", ["app", "superuser"])
def test_r8_a_private_install_that_she_shared_stays_and_is_counted(
    world, role: str,
) -> None:
    """Finding 1 of the PKG-4 review (decisions 3 and 5). Ana shared app a2
    with Ben, so Ben holds an ``app_grants`` row on it, and the resolve shows
    him her private install. The purge of Ana keeps that install and counts
    it. Her private install of a1 has only her own grant, so it goes.

    The purge runs in its own order: each count first, then the delete of
    her grants, then the delete of her installs. The three clauses split
    her rows of org A with no overlap and no gap."""
    a, b = world.p.org_a, world.p.org_b
    ids = {
        "ana_alone": _seed(world, "a", kind="private", subject=ANA, v=None),
        "ana_shared": _go(world.p.app_url, lambda: _install(
            a, world.apps["a2"], kind="private", subject=ANA)),
        "ana_org": _go(world.p.app_url, lambda: _install(a, world.apps["a2"])),
        # Ben's private install of a1, where Ana holds a grant. It is his.
        "ben_private": _go(world.p.app_url, lambda: _install(
            a, world.apps["a1"], kind="private", subject=BEN, by=BEN)),
        # Org B: the same address, on an app that Ben holds a grant on.
        "b_shared": _seed(world, "b", kind="private", subject=ANA, v=None),
    }
    _go(world.p.app_url, lambda: _contrib(a, ids["ana_shared"]))

    async def grants() -> None:
        await _grant(a, world.apps["a1"], ANA)
        await _grant(a, world.apps["a2"], BEN)
        await _grant(b, world.apps["b1"], BEN)

    _go(world.p.app_url, grants)
    url = world.p.app_url if role == "app" else world.p.admin_engine.url
    # Each row of org A that names Ana, read as the superuser before the purge.
    with world.p.admin_engine.connect() as c:
        hers = set(c.execute(text(
            "SELECT id::text FROM package_install WHERE organization_id = CAST(:o AS uuid) "
            "AND (lower(scope_subject) = :e OR lower(installed_by) = :e)"),
            {"o": a, "e": ANA}).scalars().all())
    assert hers == {ids["ana_alone"], ids["ana_shared"], ids["ana_org"]}

    out = _go(url, lambda: _purge_installs(a))

    assert out.counts == {"private_app_installs": 1, "shared_app_installs": 1,
                          "shared_private_app_installs": 1}
    assert out.deleted == 1
    # No overlap and no gap: each of her rows is on exactly one side.
    assert out.rows["private_app_installs"] == {ids["ana_alone"]}
    assert out.rows["shared_private_app_installs"] == {ids["ana_shared"]}
    assert out.rows["shared_app_installs"] == {ids["ana_org"]}
    assert set().union(*out.rows.values()) == hers
    assert sum(len(v) for v in out.rows.values()) == len(hers)
    assert _left(world, ids) == set(ids.values()) - {ids["ana_alone"]}
    assert _contributions_of(world, ids["ana_alone"]) == 0
    assert _contributions_of(world, ids["ana_shared"]) == 1
    # Her own grant went. Ben's grants stay.
    assert _grants_of(world, ANA) == 0
    assert _grants_of(world, BEN) == 2


async def _grant(org: str, app: str, subject: str) -> None:
    """One ``app_grants`` row as the app role, bound to *org*."""
    async with tenant_session(org) as db:
        await db.execute(text(
            "INSERT INTO app_grants (app_id, subject, granted_by) "
            "VALUES (CAST(:app AS uuid), :s, :by)"), {"app": app, "s": subject, "by": ANA})


def _grants_of(world: Any, subject: str) -> int:
    return int(_admin_scalar(
        world, "SELECT count(*) FROM app_grants g JOIN apps a ON a.id = g.app_id "
               "WHERE a.slug LIKE :s AND g.subject = :e", {"s": _SLUG_LIKE, "e": subject}))


def _left(world: Any, ids: dict[str, str]) -> set[str]:
    with world.p.admin_engine.connect() as c:
        return set(c.execute(text(
            "SELECT id::text FROM package_install WHERE id = ANY(CAST(:i AS uuid[]))"),
            {"i": list(ids.values())}).scalars().all())


async def _purge_installs(org: str) -> SimpleNamespace:
    """The ``package_install`` half of ``purge_member_rows``, in its order.

    It counts each row and reads its ids first, then deletes Ana's grants
    through the ``app_grants`` row, then her installs. It uses the rows of
    ``members.py``, never a copy."""
    from gateway.routes.admin.members import _PURGE_DELETES, _PURGE_KEEPS, _purge_params

    rows = {r.key: r for r in (*_PURGE_DELETES, *_PURGE_KEEPS)
            if r.table in ("package_install", "app_grants")}
    uid = str(uuid.uuid4())
    out = SimpleNamespace(counts={}, rows={}, deleted=0)
    async with tenant_session(org) as db:
        for key, row in rows.items():
            if row.table != "package_install":
                continue
            params = _purge_params(row, uid=uid, email=ANA)
            out.counts[key] = int((await db.execute(text(row.count_sql), params)).scalar_one())
            out.rows[key] = {str(i) for i in (await db.execute(text(
                f"SELECT id FROM package_install WHERE {row.where}"), params)).scalars()}
        grants = rows["app_grants"]
        await db.execute(text(grants.delete_sql), _purge_params(grants, uid=uid, email=ANA))
        gone = rows["private_app_installs"]
        out.deleted = (await db.execute(text(gone.delete_sql), _purge_params(
            gone, uid=uid, email=ANA))).rowcount
    return out
