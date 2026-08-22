"""WS-29 H3 — RLS phase-4 promotion rehearsal, two-org isolation + sign-in brick.

Spec: ``project-docs/specs/saas_multitenancy_handover.md`` §H3 (RLS phases) ·
``saas_multitenancy.md`` §1.3 / MT-1b · MT-1i (``work_plan.md`` §2 WS-14a row:
"the two-org DB-backed fixture run ``passed``, never ``skipped``") · D15.

**What this file is.** The OWED two-org isolation fixture and the H3 sign-in-brick
characterization, both run against a REAL, fully phase-4-promoted tenant catalog
on scratch Postgres. It is the R8 half of H3: a hermetic fake agrees with any SQL
it is handed, so an isolation "proof" against a fake proves nothing — the whole
point is that ``USING``/``WITH CHECK`` and ``FORCE ROW LEVEL SECURITY`` behave as
the generated policies claim, which only a real server can answer.

**It builds its own DEDICATED database and NEVER promotes the shared ladder DB.**
Phase 3 adds ``organization_id NOT NULL`` to ~137 tables and phase 4 forces RLS;
applying either to the shared ``TENANT_LADDER_DATABASE_URL`` database would break
every other tenant-ladder suite sharing it in the same CI job. So the module
fixture DROP/CREATEs a sibling database (``<db>_h3rls``), applies the full ladder
+ ``infra/postgres/generated/{01,02,03,04}.sql`` by hand in phase order (the
ladder deliberately never replays ``generated/``), creates the NON-privileged
``acb_app`` role per handover §H3, and connects as that role for every assertion
— because a SUPERUSER bypasses RLS entirely and the DB owner escapes it without
``FORCE``, so a test that connected as either would prove nothing.

⚠️ **The gate is ``TENANT_LADDER_DATABASE_URL``, never ``DATABASE_URL``** — the
latter arms ``test_tenant_coverage.py``'s two DB-gated H3 tests, which answer to a
different (fully-promoted, acb_app) catalog. It reads ``tests/conftest.py``'s
launch snapshot for the reason recorded there (``import litellm`` calls
``load_dotenv()`` mid-collection and a dev ``.env`` must not aim an R8 gate at a
local database). A skip is not a pass (R8).

⚠️ **Rehearsal finding, recorded so it is not lost.** A faithful phase-4
promotion leaves the three CRM homonym tables (``crm_contacts``/``crm_deals``/
``crm_activities``) with an ``organization_id`` column but NO policy, on purpose —
``gen_tenant_migration`` scopes ``discover_tables() - EXEMPT - HOMONYM_BLOCKED``.
This suite therefore isolates on ``apps`` (a properly scoped table), never a
homonym. The interaction of that gap with ``test_tenant_coverage.py``'s live
catalog gate is written up in ``saas_multitenancy_handover.md`` §H3 as an owner
decision.

Run::

    TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5443/acb_tenant \
        uv run pytest tests/unit/test_h3_rls_promotion_rehearsal.py -v -rs
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")

# ``acb_auth.access`` supplies the REAL identity statements (_ACCESS_SQL /
# _BOOTSTRAP_OWNER_SQL) — imported, never transcribed — so this suite
# characterizes THE code's own SQL under phase-4 policies; a copy would keep
# passing after the brick is fixed (H6 / an app_user carve-out), the one moment
# the characterization must change.
import acb_auth.access as access_mod
from acb_auth.access import (
    _ACCESS_SQL,
    _BOOTSTRAP_ORG_SLUG,
    _BOOTSTRAP_OWNER_SQL,
    _HAS_OWNER_SQL,
    _IDENTITY_LEG_SQL,
    ensure_owner_bootstrap,
    mirror_identity_membership,
    resolve_identity,
    resolve_session_access,
)
from acb_auth.deps import _with_resolved_access
from acb_auth.roles import UserContext, UserRole
from acb_common.db import bind_tenant, clear_tenant
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.pool import NullPool

from tests.unit._tenant_ladder import apply_ladder, tenant_engine_scope

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
        "pgvector (infra/postgres/01_schema.sql needs uuid-ossp AND vector). "
        "A skip here is not a pass; CI must set it. ⚠️ Do NOT export it as "
        "DATABASE_URL — see this module's docstring."
    ),
)

#: The non-privileged application role and its scratch password. NOT superuser,
#: NOT owner, NOT BYPASSRLS — the whole point of connecting as it (handover §H3).
#:
#: ⚠️ **Suite-private NAME, canonical PRIVILEGES.** The handover's production role
#: is ``acb_app``; this rehearsal deliberately does NOT reuse that name, because a
#: role is cluster-wide and ``ALTER ROLE ... PASSWORD`` here would clobber a real
#: ``acb_app``'s password on any shared cluster. The grant set below mirrors
#: §H3's exactly — what the test proves (a non-super, non-owner, non-BYPASSRLS
#: role is subject to RLS) is about the privileges, never the name.
_APP_ROLE = "acb_app_h3rls"
_APP_PW = "h3rls_scratch_pw"


def _apply_generated_phase(conn, name: str) -> None:
    """Run one ``generated/NN_*.sql`` phase file verbatim, in order.

    Uses the DBAPI cursor directly for the reason ``_tenant_ladder._exec_file``
    documents at length: SQLAlchemy's ``exec_driver_sql`` hands psycopg an empty
    parameter set, triggering its client-side ``%`` placeholder pass, and one
    file carrying many statements needs the simple query protocol. The generated
    phases carry no ``%`` today, but this keeps the two mechanisms identical.
    """
    with conn.connection.dbapi_connection.cursor() as cur:
        cur.execute((_GENERATED / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def promoted():
    """A dedicated, fully phase-4-promoted scratch catalog + the acb_app role.

    Yields a namespace carrying an admin (superuser) engine to the dedicated
    database and the ``acb_app`` (non-privileged) DSN the assertions connect
    through. Torn down by dropping the dedicated database, so nothing this suite
    promotes ever touches the shared ladder DB.
    """
    admin_url = make_url(_URL)
    dedicated = f"{admin_url.database}_h3rls"

    # ── DROP/CREATE the dedicated database (autocommit; DDL cannot be in a txn) ─
    maint = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with maint.connect() as c:
        c.execute(text(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = :d AND pid <> pg_backend_pid()"), {"d": dedicated})
        c.execute(text(f'DROP DATABASE IF EXISTS "{dedicated}"'))
        c.execute(text(f'CREATE DATABASE "{dedicated}"'))
    maint.dispose()

    ded_admin_url = admin_url.set(database=dedicated)
    eng = create_engine(ded_admin_url, future=True)

    # ── Full ladder, then the four generated phases IN ORDER, by hand ──────────
    with eng.begin() as conn:
        apply_ladder(conn)
    for phase in ("01_add_columns.sql", "02_backfill.sql",
                  "03_constraints.sql", "04_policies.sql"):
        with eng.begin() as conn:
            _apply_generated_phase(conn, phase)

    # ── The non-privileged app role (handover §H3 pre-phase-1 SQL) ─────────────
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

    # ── Seed two organizations + rows, as the SUPERUSER admin (bypasses RLS) ────
    #    Seeding here rather than through acb_app is deliberate: the admin escapes
    #    the very policies under test, so the seed can stamp org B directly and
    #    the WITH-CHECK assertion below is then the FIRST thing acb_app tries.
    org_a = str(uuid.uuid4())
    org_b = str(uuid.uuid4())
    owner_email = "owner@h3rls.test"
    with eng.begin() as conn:
        for oid, slug in ((org_a, "h3rls-a"), (org_b, "h3rls-b")):
            conn.execute(text(
                "INSERT INTO organization (id, slug, display_name) "
                "VALUES (:id, :slug, :name)"),
                {"id": oid, "slug": slug, "name": slug})
        # apps: 2 rows for org A, 3 for org B, all marked 'h3rls'.
        for i, oid in enumerate([org_a, org_a, org_b, org_b, org_b]):
            conn.execute(text(
                "INSERT INTO apps (slug, name, owner_email, workspace_path, "
                "organization_id) VALUES (:slug, 'h3rls', :oe, :wp, :org)"),
                {"slug": f"h3rls-app-{i}", "oe": owner_email,
                 "wp": f"/tmp/h3rls-{i}", "org": oid})
        # one app_user for the brick: the owner, in org A.
        conn.execute(text(
            "INSERT INTO app_user (email, display_name, role, status, "
            "organization_id) VALUES (:e, :e, 'executive', 'active', :org)"),
            {"e": owner_email, "org": org_a})

    app_url = admin_url.set(
        database=dedicated, username=_APP_ROLE, password=_APP_PW)

    class _NS:
        pass

    ns = _NS()
    ns.admin_engine = eng
    ns.app_url = app_url
    ns.org_a = org_a
    ns.org_b = org_b
    ns.owner_email = owner_email

    yield ns

    eng.dispose()
    maint = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with maint.connect() as c:
        c.execute(text(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = :d AND pid <> pg_backend_pid()"), {"d": dedicated})
        c.execute(text(f'DROP DATABASE IF EXISTS "{dedicated}"'))
    maint.dispose()


@pytest.fixture
def app_engine(promoted):
    """A fresh engine connected as the NON-privileged ``acb_app`` role.

    Disposed per test so no pooled connection outlives it — and, more to the
    point, so every assertion runs as a role that cannot bypass RLS.
    """
    eng = create_engine(promoted.app_url, future=True)
    try:
        yield eng
    finally:
        eng.dispose()


# ==========================================================================
# The OWED two-org isolation fixture (MT-1i / work_plan.md §2 WS-14a).
# ==========================================================================
@_DB_GATE
class TestTwoOrgIsolation:
    """A session bound to org A reads ONLY org A rows and cannot WRITE org B's.

    Runs against a real phase-4-promoted ``apps`` table, as ``acb_app`` — the
    exact posture the product runs in. Proves the three load-bearing halves of
    every generated policy: ``FORCE`` (acb_app is not the owner, but RLS applies
    regardless), ``USING`` (reads are filtered) and ``WITH CHECK`` (a write
    stamped with another tenant's id is refused).
    """

    def test_the_app_role_really_cannot_bypass_rls(self, app_engine):
        """Precondition: the role these assertions use is genuinely constrained.

        A superuser or a BYPASSRLS role would make every assertion below vacuous.
        """
        with app_engine.connect() as c:
            row = c.execute(text(
                "SELECT rolsuper, rolbypassrls FROM pg_roles "
                "WHERE rolname = current_user")).first()
        assert row is not None
        assert not row[0], "the isolation test connects as a SUPERUSER — RLS bypassed"
        assert not row[1], "the isolation test role has BYPASSRLS — RLS bypassed"

    def test_a_bound_session_sees_only_its_own_tenants_rows(self, app_engine, promoted):
        with app_engine.connect() as c, c.begin():
            c.execute(text("SELECT set_config('app.tenant_id', :o, true)"),
                      {"o": promoted.org_a})
            total = c.execute(text(
                "SELECT count(*) FROM apps WHERE name='h3rls'")).scalar_one()
            foreign = c.execute(text(
                "SELECT count(*) FROM apps WHERE name='h3rls' "
                "AND organization_id = :b"), {"b": promoted.org_b}).scalar_one()
            distinct_orgs = [
                str(r[0]) for r in c.execute(text(
                    "SELECT DISTINCT organization_id FROM apps "
                    "WHERE name='h3rls'"))]
        assert total == 2, f"org A must see its 2 rows, saw {total}"
        assert foreign == 0, "org B's rows leaked into an org-A-bound read"
        assert distinct_orgs == [promoted.org_a], (
            f"org-A-bound read exposed foreign tenants: {distinct_orgs}")

    def test_the_other_tenant_sees_its_own_disjoint_rows(self, app_engine, promoted):
        with app_engine.connect() as c, c.begin():
            c.execute(text("SELECT set_config('app.tenant_id', :o, true)"),
                      {"o": promoted.org_b})
            total = c.execute(text(
                "SELECT count(*) FROM apps WHERE name='h3rls'")).scalar_one()
        assert total == 3, f"org B must see its 3 rows, saw {total}"

    def test_an_unbound_session_sees_zero_rows(self, app_engine):
        """Fail-closed: no ``app.tenant_id`` bound → the GUC is NULL → no rows.

        This is §0.1's property and the whole reason H2 must precede H3.
        """
        with app_engine.connect() as c, c.begin():
            total = c.execute(text(
                "SELECT count(*) FROM apps WHERE name='h3rls'")).scalar_one()
        assert total == 0, (
            f"an UNBOUND session read {total} rows — RLS is not fail-closed")

    def test_a_write_stamped_with_another_tenant_is_rejected(self, app_engine, promoted):
        """WITH CHECK: an org-A-bound session cannot INSERT a row owned by org B."""
        with app_engine.connect() as c, c.begin():
            c.execute(text("SELECT set_config('app.tenant_id', :o, true)"),
                      {"o": promoted.org_a})
            with pytest.raises((DBAPIError, ProgrammingError)) as exc:
                c.execute(text(
                    "INSERT INTO apps (slug, name, owner_email, "
                    "workspace_path, organization_id) VALUES "
                    "('h3rls-evil', 'h3rls', 'x@y', '/tmp/e', :b)"),
                    {"b": promoted.org_b})
        assert "row-level security" in str(exc.value).lower(), (
            f"expected a WITH CHECK violation, got: {exc.value}")

    def test_a_default_stamped_write_lands_in_the_bound_tenant(self, app_engine, promoted):
        """The phase-1 DEFAULT stamps the bound tenant when the writer omits it.

        This is what makes §1.3 true — no INSERT statement in the gateway had to
        learn about ``organization_id``. Rolled back so it does not perturb the
        row counts the other tests assert.
        """
        with app_engine.connect() as c:
            trans = c.begin()
            try:
                c.execute(text("SELECT set_config('app.tenant_id', :o, true)"),
                          {"o": promoted.org_a})
                stamped = c.execute(text(
                    "INSERT INTO apps (slug, name, owner_email, workspace_path) "
                    "VALUES ('h3rls-default', 'h3rls-default', 'x@y', '/tmp/d') "
                    "RETURNING organization_id")).scalar_one()
                assert str(stamped) == promoted.org_a, (
                    "the DEFAULT did not stamp the bound tenant")
            finally:
                trans.rollback()


# ==========================================================================
# H3 sign-in brick characterization (the confirmed finding).
# ==========================================================================
@_DB_GATE
class TestSignInBrickCharacterization:
    """Under phase-4 policies, identity resolution reads ``app_user`` on an
    UNBOUND session — so it reads ZERO rows and sign-in bricks.

    This runs THE code's own SQL (``_ACCESS_SQL`` / ``_BOOTSTRAP_OWNER_SQL``,
    imported from ``acb_auth.access``) as ``acb_app`` with no ``app.tenant_id``
    bound, which is exactly how ``resolve_access`` / ``resolve_identity`` /
    ``ensure_owner_bootstrap`` reach the database (all via the plain, un-bound
    ``get_session_factory()``). It characterizes the H3-before-H6 state; when the
    owner ratifies a fix (an app_user carve-out, or sequencing H6 first) these
    assertions are updated in the same change — a characterization test is
    supposed to move when the behaviour does.
    """

    def test_the_owner_row_exists_and_is_visible_when_bound(self, app_engine, promoted):
        """The data IS there: only the missing bind hides it. Bind → the read
        that resolves sign-in returns the owner row."""
        with app_engine.connect() as c, c.begin():
            c.execute(text("SELECT set_config('app.tenant_id', :o, true)"),
                      {"o": promoted.org_a})
            row = c.execute(text(_ACCESS_SQL),
                            {"email": promoted.owner_email}).mappings().first()
        assert row is not None, "bound, the identity read must find the owner"
        assert str(row["organization_id"]) == promoted.org_a

    def test_the_unbound_identity_read_returns_nothing(self, app_engine, promoted):
        """THE BRICK. The exact ``_ACCESS_SQL`` ``resolve_access`` runs, on an
        unbound session, returns no row — so ``resolve_access`` resolves to
        ``is_active=False`` and the authenticated user is refused: the
        2026-07-30 lockout shape, now by construction for every user."""
        with app_engine.connect() as c, c.begin():
            row = c.execute(text(_ACCESS_SQL),
                            {"email": promoted.owner_email}).mappings().first()
        assert row is None, (
            "the unbound identity read returned a row — the brick would NOT "
            "reproduce, which would mean app_user is not actually FORCE-RLS'd")

    def test_the_unbound_tenant_discovery_read_returns_nothing(self, app_engine, promoted):
        """The chicken-and-egg: ``resolve_identity`` reads ``app_user`` to
        DISCOVER which tenant to bind — but that read is itself unbound, so it
        returns nothing and there is no tenant to bind. This is why the brick
        cannot be fixed by "just bind earlier"."""
        with app_engine.connect() as c, c.begin():
            row = c.execute(text(
                "SELECT id::text AS id, organization_id::text AS org "
                "FROM app_user WHERE lower(email) = :email LIMIT 1"),
                {"email": promoted.owner_email}).mappings().first()
        assert row is None, "unbound tenant-discovery read must return nothing"

    def test_the_unbound_owner_bootstrap_write_is_rejected(self, app_engine):
        """The WRITE half: ``ensure_owner_bootstrap`` runs ``_BOOTSTRAP_OWNER_SQL``
        on an unbound session. Its ``app_user`` INSERT's WITH CHECK compares
        ``organization_id`` against the unset GUC (NULL) → the row is refused, so
        an ownerless box can never bootstrap its first owner after promotion."""
        # pytest.raises stays nested, NOT folded into the connect/begin contexts
        # (SIM117): it must scope to the execute alone, or it would also swallow
        # the transaction rollback that fires as the failed statement unwinds.
        with app_engine.connect() as c, c.begin():  # noqa: SIM117
            with pytest.raises((DBAPIError, ProgrammingError)) as exc:
                c.execute(text(_BOOTSTRAP_OWNER_SQL),
                          {"email": "newowner@h3rls.test", "org_slug": "h3rls-a"})
        assert "row-level security" in str(exc.value).lower(), (
            f"expected a WITH CHECK violation on the app_user insert, got: {exc.value}")


# ==========================================================================
# H6 slice 3b — THE READ CUTOVER (the sign-in brick fix), DARK behind
# IDENTITY_CUTOVER. The same red/green the brick class above lands, now driven
# through `resolve_identity`'s own call path against the promoted phase-4
# catalog as the non-privileged acb_app role, UNBOUND: flag ON, an ACTIVE
# member resolves GREEN off the RLS-EXEMPT `user_identity ⋈ org_membership`;
# flag OFF, the `app_user` read still bricks RED; a suspended member is refused
# by the `status='active'` filter. Seeded through `mirror_identity_membership`
# (the shadow's own writer) so the whole path is the product's, not the test's.
# ==========================================================================
@_DB_GATE
class TestTheReadCutoverIdentityLeg:
    """The slice-3b done-when, R8 against the phase-4-promoted two-org catalog.

    Runs `resolve_identity` through the shared seam (`tenant_engine_scope` points
    it at the dedicated DB as the non-privileged `acb_app` role) on an UNBOUND
    session — exactly how `deps._with_resolved_access` reaches it before a tenant
    is bound. `app_user` is FORCE-RLS'd here, so the flag-OFF path returns zero
    rows (the brick) while the flag-ON path resolves off the exempt shadow.
    """

    @staticmethod
    def _app_dsn(promoted) -> str:
        return promoted.app_url.render_as_string(hide_password=False)

    @staticmethod
    async def _purge_shadow(factory, email: str) -> None:
        async with factory() as s:
            await s.execute(
                text(
                    "DELETE FROM org_membership m USING user_identity ui "
                    " WHERE m.user_id = ui.id AND lower(ui.email) = lower(:e)"
                ),
                {"e": email},
            )
            await s.execute(
                text("DELETE FROM user_identity WHERE lower(email) = lower(:e)"),
                {"e": email},
            )
            await s.commit()

    async def test_an_active_member_resolves_green_with_the_flag_on(
        self, promoted, monkeypatch
    ):
        """GREEN: `IDENTITY_CUTOVER` ON, the identity leg resolves the tenant of
        a clean ACTIVE member on an UNBOUND session — the exact place the
        pre-cutover `app_user` read returned nothing and sign-in bricked."""
        from acb_common.db import get_session_factory

        monkeypatch.setenv("IDENTITY_CUTOVER", "1")
        async with tenant_engine_scope(self._app_dsn(promoted)):
            factory = get_session_factory()
            try:
                await mirror_identity_membership(
                    email=promoted.owner_email,
                    display_name=promoted.owner_email,
                    org_id=promoted.org_a,
                    status="active",
                )
                user_id, org = await resolve_identity(promoted.owner_email)
                assert org == promoted.org_a, (
                    "the identity leg did not resolve the tenant UNBOUND — the "
                    "read cutover is not GREEN")
                assert user_id is not None, "no user_identity id was resolved"
            finally:
                await self._purge_shadow(factory, promoted.owner_email)

    async def test_the_app_user_read_still_bricks_red_with_the_flag_off(
        self, promoted, monkeypatch
    ):
        """RED: with the flag OFF, `resolve_identity` reads `app_user` (unbound,
        FORCE-RLS'd → zero rows) — the brick, unchanged — EVEN WITH the shadow
        seeded, proving the shadow is never consulted while the flag is unset
        (the flag-OFF-byte-identical fence, in its live form)."""
        from acb_common.db import get_session_factory

        monkeypatch.delenv("IDENTITY_CUTOVER", raising=False)
        async with tenant_engine_scope(self._app_dsn(promoted)):
            factory = get_session_factory()
            try:
                # Seed the shadow so the ONLY reason flag-OFF returns nothing is
                # that it reads app_user, not the (present) shadow.
                await mirror_identity_membership(
                    email=promoted.owner_email,
                    org_id=promoted.org_a,
                    status="active",
                )
                user_id, org = await resolve_identity(promoted.owner_email)
                assert (user_id, org) == (None, None), (
                    "flag OFF must read app_user unbound and brick RED — the "
                    "shadow must not be consulted when IDENTITY_CUTOVER is unset")
            finally:
                await self._purge_shadow(factory, promoted.owner_email)

    async def test_a_suspended_member_is_not_admitted_by_the_identity_leg(
        self, promoted, monkeypatch
    ):
        """The `status='active'` filter is SELECTIVE, not blanket: with the flag
        ON, an ACTIVE member resolves while a SUSPENDED one (same org) does not
        — so a suspended member's stale-but-present shadow row can never bind a
        tenant (and the bound role leg is the second gate anyway)."""
        from acb_common.db import get_session_factory

        monkeypatch.setenv("IDENTITY_CUTOVER", "1")
        active = "active.member@h3rls.test"
        suspended = "suspended.member@h3rls.test"
        async with tenant_engine_scope(self._app_dsn(promoted)):
            factory = get_session_factory()
            try:
                await mirror_identity_membership(
                    email=active, org_id=promoted.org_a, status="active")
                await mirror_identity_membership(
                    email=suspended, org_id=promoted.org_a, status="suspended")

                _, active_org = await resolve_identity(active)
                assert active_org == promoted.org_a, (
                    "the active member was not admitted — the filter is not "
                    "selective")

                sus_id, sus_org = await resolve_identity(suspended)
                assert (sus_id, sus_org) == (None, None), (
                    "a SUSPENDED member was admitted by the identity leg — the "
                    "status='active' filter is not applied")
            finally:
                await self._purge_shadow(factory, active)
                await self._purge_shadow(factory, suspended)

    def test_the_identity_leg_sql_reads_only_the_rls_exempt_tables(self):
        """Structural companion (no seam): the imported `_IDENTITY_LEG_SQL` reads
        the exempt shadow join, filters `status='active'`, and NEVER names
        `app_user` — which is FORCE-RLS'd and would return zero rows unbound."""
        assert "user_identity" in _IDENTITY_LEG_SQL
        assert "org_membership" in _IDENTITY_LEG_SQL
        assert "organization" in _IDENTITY_LEG_SQL
        assert "status = 'active'" in _IDENTITY_LEG_SQL
        assert "app_user" not in _IDENTITY_LEG_SQL, (
            "the identity leg names app_user — it must read only the RLS-exempt "
            "shadow tables, or it bricks unbound post-phase-4")


# ==========================================================================
# H6 slice 3b — THE END-TO-END TWO-PHASE FENCE (repair round, the P0 the
# identity-leg-in-isolation fence above missed). The role leg is NOT
# RLS-bound by `bind_tenant` alone — `bind_tenant` sets only the ContextVar,
# never the GUC — so under phase-4 RLS + IDENTITY_CUTOVER=ON a live member's
# `resolve_access` read of the FORCE-RLS'd `app_user` returned 0 rows and
# every member was locked out (the cutover did not fix the brick it exists
# for). This drives the FULL `deps._with_resolved_access` composition (identity
# leg → bind → role leg) against the promoted two-org catalog as `acb_app`,
# UNBOUND: GREEN a clean ACTIVE member resolves is_active=True WITH their real
# role; RED the mutation (role leg blind to the bound tenant) → is_active=False.
# ==========================================================================
async def _purge_shadow_rows(factory, email: str) -> None:
    """Delete an identity's shadow (org_membership + user_identity) — the
    module-level twin of ``TestTheReadCutoverIdentityLeg._purge_shadow``, so the
    end-to-end fence cleans up without reaching into that class."""
    async with factory() as s:
        await s.execute(
            text(
                "DELETE FROM org_membership m USING user_identity ui "
                " WHERE m.user_id = ui.id AND lower(ui.email) = lower(:e)"
            ),
            {"e": email},
        )
        await s.execute(
            text("DELETE FROM user_identity WHERE lower(email) = lower(:e)"),
            {"e": email},
        )
        await s.commit()


def _seed_active_member(admin_engine, *, org_id: str, email: str,
                        role_slug: str) -> None:
    """Seed (as the RLS-BYPASSING superuser admin) an ACTIVE ``app_user`` in
    ``org_id`` holding a real ``org_role`` — so the BOUND role leg resolves
    is_active=True WITH a role. Every INSERT stamps ``organization_id``
    explicitly: the phase-1 DEFAULT reads the unset GUC as NULL and these role
    tables are NOT NULL / FORCE-RLS'd after phases 3+4."""
    uid = str(uuid.uuid4())
    role_id = str(uuid.uuid4())
    with admin_engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO org_role (id, organization_id, slug, display_name, "
            "is_system, rank) VALUES (:rid, :org, :slug, :slug, false, 100)"),
            {"rid": role_id, "org": org_id, "slug": role_slug})
        conn.execute(text(
            "INSERT INTO org_role_permission (role_id, permission, "
            "organization_id) VALUES (:rid, 'feature:chat', :org)"),
            {"rid": role_id, "org": org_id})
        conn.execute(text(
            "INSERT INTO app_user (id, email, display_name, role, status, "
            "organization_id) VALUES (:uid, :e, :e, 'employee', 'active', :org)"),
            {"uid": uid, "e": email, "org": org_id})
        conn.execute(text(
            "INSERT INTO user_role (user_id, role_id, assigned_by, "
            "organization_id) VALUES (:uid, :rid, 'h3rls-test', :org)"),
            {"uid": uid, "rid": role_id, "org": org_id})


def _unseed_member(admin_engine, *, email: str, role_slug: str,
                   org_id: str) -> None:
    with admin_engine.begin() as conn:
        # ``app_user`` delete cascades ``user_role``; ``org_role`` delete
        # cascades ``org_role_permission``.
        conn.execute(text("DELETE FROM app_user WHERE lower(email) = lower(:e)"),
                     {"e": email})
        conn.execute(text(
            "DELETE FROM org_role WHERE organization_id = :org AND slug = :slug"),
            {"org": org_id, "slug": role_slug})


def _seed_shared_room(admin_engine, *, org_id: str, actor_email: str,
                      member_email: str, actor_role: str, member_role: str,
                      group_slug: str, session_id: str) -> None:
    """Seed (as the RLS-BYPASSING superuser admin) a TWO-member shared session in
    ``org_id``: an actor (owner participant) and a second member reached through a
    ``group:<slug>`` subject — so ``resolve_session_access``'s expansion touches
    ``chat_session_participant`` (the participant read), ``org_group_member`` and
    ``app_user`` (the ``_GROUP_MEMBER_SQL`` group leg), ALL FORCE-RLS'd after
    phases 3+4. ``org_group`` itself is RLS-EXEMPT (``gen_tenant_migration``:
    "carries organization_id since 138"), so the group ROW is readable unbound —
    the members are not. Every INSERT stamps ``organization_id`` explicitly: the
    phase-1 DEFAULT reads the unset GUC (NULL) as the admin, and these tables are
    NOT NULL / FORCE-RLS'd after promotion."""
    _seed_active_member(admin_engine, org_id=org_id, email=actor_email,
                        role_slug=actor_role)
    _seed_active_member(admin_engine, org_id=org_id, email=member_email,
                        role_slug=member_role)
    gid = str(uuid.uuid4())
    with admin_engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO org_group (id, organization_id, slug, display_name) "
            "VALUES (:gid, :org, :slug, :slug)"),
            {"gid": gid, "org": org_id, "slug": group_slug})
        conn.execute(text(
            "INSERT INTO org_group_member (group_id, user_id, organization_id) "
            "SELECT :gid, au.id, :org FROM app_user au "
            "WHERE lower(au.email) = lower(:e)"),
            {"gid": gid, "org": org_id, "e": member_email})
        conn.execute(text(
            "INSERT INTO chat_session (id, user_id, organization_id) "
            "VALUES (:sid, :owner, :org)"),
            {"sid": session_id, "owner": actor_email, "org": org_id})
        conn.execute(text(
            "INSERT INTO chat_session_participant "
            "(session_id, subject, role, organization_id) "
            "VALUES (:sid, :subj, 'owner', :org)"),
            {"sid": session_id, "subj": actor_email, "org": org_id})
        conn.execute(text(
            "INSERT INTO chat_session_participant "
            "(session_id, subject, role, organization_id) "
            "VALUES (:sid, :subj, 'member', :org)"),
            {"sid": session_id, "subj": f"group:{group_slug}", "org": org_id})


def _unseed_shared_room(admin_engine, *, org_id: str, actor_email: str,
                        member_email: str, actor_role: str, member_role: str,
                        group_slug: str, session_id: str) -> None:
    with admin_engine.begin() as conn:
        # ``chat_session`` delete cascades its participants; ``org_group`` delete
        # cascades ``org_group_member``.
        conn.execute(text("DELETE FROM chat_session WHERE id = :sid"),
                     {"sid": session_id})
        conn.execute(text(
            "DELETE FROM org_group WHERE organization_id = :org AND slug = :slug"),
            {"org": org_id, "slug": group_slug})
    _unseed_member(admin_engine, email=actor_email, role_slug=actor_role,
                   org_id=org_id)
    _unseed_member(admin_engine, email=member_email, role_slug=member_role,
                   org_id=org_id)


@_DB_GATE
class TestTheReadCutoverEndToEnd:
    """The end-to-end two-phase fence (R7 name: ``end-to-end-two-phase``; R8).

    Drives ``deps._with_resolved_access`` — the real production composition — on
    the phase-4-promoted catalog as ``acb_app``, session UNBOUND, flag ON. The
    identity-leg-only fence above could not catch the P0 because it never drove
    the ROLE leg under the bind.
    """

    @staticmethod
    def _app_dsn(promoted) -> str:
        return promoted.app_url.render_as_string(hide_password=False)

    async def test_a_clean_active_member_resolves_active_with_roles_flag_on(
        self, promoted, monkeypatch
    ):
        """GREEN: UNBOUND session, ``IDENTITY_CUTOVER`` ON, the full two-phase
        path admits a clean ACTIVE member — is_active=True WITH their real role —
        because the role leg now reads ``app_user`` on a GUC-bound session."""
        from acb_common.db import get_session_factory

        monkeypatch.setenv("IDENTITY_CUTOVER", "1")
        email = "e2e.active@h3rls.test"
        role_slug = "e2e-green-role"
        _seed_active_member(
            promoted.admin_engine, org_id=promoted.org_a,
            email=email, role_slug=role_slug)
        async with tenant_engine_scope(self._app_dsn(promoted)):
            factory = get_session_factory()
            try:
                await mirror_identity_membership(
                    email=email, org_id=promoted.org_a, status="active")
                access_mod.invalidate()
                clear_tenant()  # the precondition: the session starts UNBOUND

                enriched = await _with_resolved_access(
                    UserContext(email=email, role=UserRole.EMPLOYEE))

                assert enriched.access.is_active is True, (
                    "a clean ACTIVE member resolved is_active=False under the "
                    "cutover — the role leg is not GUC-bound (the P0)")
                assert role_slug in enriched.access.roles, (
                    f"the member's real role {role_slug!r} did not resolve: "
                    f"{sorted(enriched.access.roles)} — the bound role leg did "
                    "not read app_user")
                assert str(enriched.organization_id) == promoted.org_a
            finally:
                clear_tenant()
                await _purge_shadow_rows(factory, email)
                _unseed_member(
                    promoted.admin_engine, email=email,
                    role_slug=role_slug, org_id=promoted.org_a)

    async def test_the_role_leg_blind_to_the_bind_locks_the_member_out_red(
        self, promoted, monkeypatch
    ):
        """RED (the mutation): with the role leg blind to the bound tenant
        (``current_tenant()`` → None inside ``resolve_access``, i.e. the pre-fix
        raw-session behaviour ``bind_tenant`` never actually cured), the SAME
        clean ACTIVE member resolves is_active=False — total lockout. This is the
        P0; the GREEN above proves the GUC bind, not something else, admits them.
        """
        from acb_common.db import get_session_factory

        monkeypatch.setenv("IDENTITY_CUTOVER", "1")
        email = "e2e.red@h3rls.test"
        role_slug = "e2e-red-role"
        _seed_active_member(
            promoted.admin_engine, org_id=promoted.org_a,
            email=email, role_slug=role_slug)
        async with tenant_engine_scope(self._app_dsn(promoted)):
            factory = get_session_factory()
            try:
                await mirror_identity_membership(
                    email=email, org_id=promoted.org_a, status="active")
                access_mod.invalidate()
                clear_tenant()
                # The mutation: the role leg does not see the bound tenant, so
                # resolve_access falls back to the raw UNBOUND session — exactly
                # what shipped before this repair. bind_tenant still fires in
                # _with_resolved_access; it just sets the ContextVar, never the
                # GUC, which is the whole point.
                monkeypatch.setattr(access_mod, "current_tenant", lambda: None)

                enriched = await _with_resolved_access(
                    UserContext(email=email, role=UserRole.EMPLOYEE))

                assert enriched.access.is_active is False, (
                    "the member resolved is_active=True with the role leg blind "
                    "to the bind — the fence does NOT catch the P0, so a "
                    "regression to the raw unbound role leg would ship green")
            finally:
                clear_tenant()
                await _purge_shadow_rows(factory, email)
                _unseed_member(
                    promoted.admin_engine, email=email,
                    role_slug=role_slug, org_id=promoted.org_a)

    async def test_a_multi_org_human_is_denied_not_arbitrarily_bound(
        self, promoted, monkeypatch
    ):
        """P2a (R7 name: ``multi-org-safe``): a human with TWO active memberships
        and no disambiguating host resolves to ``(None, None)`` — the deny /
        WorkspaceChooserRequired case — NEVER an arbitrary org bind."""
        from acb_common.db import get_session_factory

        monkeypatch.setenv("IDENTITY_CUTOVER", "1")
        email = "e2e.multiorg@h3rls.test"
        async with tenant_engine_scope(self._app_dsn(promoted)):
            factory = get_session_factory()
            try:
                # Two ACTIVE memberships for one identity, across the two orgs —
                # seeded through the shadow's own writer (the identity leg reads
                # only the exempt shadow, so no app_user row is needed).
                await mirror_identity_membership(
                    email=email, org_id=promoted.org_a, status="active")
                await mirror_identity_membership(
                    email=email, org_id=promoted.org_b, status="active")

                user_id, org = await resolve_identity(email)

                assert (user_id, org) == (None, None), (
                    "a human with two active memberships was bound to "
                    f"{org!r} — the identity leg silently picked one instead of "
                    "denying (P2a)")
            finally:
                await _purge_shadow_rows(factory, email)


# ==========================================================================
# H6 RLS-BIND HARDENING (WS-29) — the remaining unbound-RLS access sites.
# The systematic audit found two more sites that read/write RLS-FORCED tables on
# an UNBOUND session (no `app.tenant_id` GUC) and so collapse under phase-4 RLS,
# the SAME class as slice 3b's role leg and slice 4's RBAC bridge. Each is driven
# HERE as the non-privileged `acb_app` role on the phase-4-promoted catalog: GREEN
# bound (via the fix), RED on the unbound mutation. A bypass-role run is blind to
# both (scratch `acb` has `rolbypassrls`), which is the whole reason these use the
# `promoted()` fixture's dedicated non-priv role.
# ==========================================================================
@_DB_GATE
class TestResolveSessionAccessBindUnderForceRls:
    """``resolve_session_access``'s participant/member expansion under FORCE RLS.

    R7 name: ``session-authority-bound-under-rls``. Drives the REAL
    ``resolve_session_access`` on the promoted two-org catalog as ``acb_app``.
    Its fold reads ``chat_session_participant`` + ``org_group_member`` +
    ``app_user`` (all FORCE-RLS'd) — so on an UNBOUND session under phase-4 RLS
    they return ZERO rows and the shared-run authority silently collapses to the
    actor alone. GREEN: with the request tenant bound (as ``deps`` binds it) and
    ``IDENTITY_CUTOVER`` ON, the fix reads through ``tenant_session()`` and the
    second member resolves via the ``group:<slug>`` subject. RED (the mutation):
    with the fold blind to the bound tenant it falls back to the raw unbound
    session and the member drops — the pre-fix behaviour.
    """

    @staticmethod
    def _app_dsn(promoted) -> str:
        return promoted.app_url.render_as_string(hide_password=False)

    async def test_a_bound_fold_resolves_the_shared_member_green(
        self, promoted, monkeypatch
    ):
        """GREEN: bound + flag ON, the fold reads the FORCE-RLS'd participant /
        group / app_user rows and resolves the second member."""
        monkeypatch.setenv("IDENTITY_CUTOVER", "1")
        actor = "rsa.actor@h3rls.test"
        member = "rsa.member@h3rls.test"
        group = "rsa-green-group"
        sid = f"rsa-green-{uuid.uuid4().hex[:8]}"
        _seed_shared_room(
            promoted.admin_engine, org_id=promoted.org_a, actor_email=actor,
            member_email=member, actor_role="rsa-green-actor",
            member_role="rsa-green-member", group_slug=group, session_id=sid)
        async with tenant_engine_scope(self._app_dsn(promoted)):
            try:
                access_mod.invalidate()
                monkeypatch.setattr(access_mod, "_tables_missing", False)
                clear_tenant()
                bind_tenant(promoted.org_a)  # deps binds the resolved org

                _folded, members = await resolve_session_access(sid, actor)

                assert member in members, (
                    "the shared member did not resolve — the fold read the "
                    "FORCE-RLS'd participant/group tables on a session with no "
                    "GUC bound and got 0 rows (the authority collapse the fix "
                    "removes)")
                assert set(members) == {actor, member}, (
                    f"expected exactly the two members, got {members}")
            finally:
                clear_tenant()
                _unseed_shared_room(
                    promoted.admin_engine, org_id=promoted.org_a,
                    actor_email=actor, member_email=member,
                    actor_role="rsa-green-actor", member_role="rsa-green-member",
                    group_slug=group, session_id=sid)

    async def test_an_unbound_fold_collapses_to_the_actor_red(
        self, promoted, monkeypatch
    ):
        """RED (the mutation): the fold blind to the bound tenant (the pre-fix
        raw-session behaviour ``bind_tenant`` never cured) resolves the SAME room
        to the actor alone — the second member silently drops. The ContextVar is
        bound exactly as GREEN; only the GUC seam differs, so this pins the GUC
        bind — not something else — as what admits the member."""
        monkeypatch.setenv("IDENTITY_CUTOVER", "1")
        actor = "rsa.actor.red@h3rls.test"
        member = "rsa.member.red@h3rls.test"
        group = "rsa-red-group"
        sid = f"rsa-red-{uuid.uuid4().hex[:8]}"
        _seed_shared_room(
            promoted.admin_engine, org_id=promoted.org_a, actor_email=actor,
            member_email=member, actor_role="rsa-red-actor",
            member_role="rsa-red-member", group_slug=group, session_id=sid)
        async with tenant_engine_scope(self._app_dsn(promoted)):
            try:
                access_mod.invalidate()
                monkeypatch.setattr(access_mod, "_tables_missing", False)
                clear_tenant()
                bind_tenant(promoted.org_a)
                # The mutation: the fold cannot see the bound tenant, so it opens
                # the raw UNBOUND session (no GUC) — exactly what shipped before
                # this hardening. `current_tenant` is the name the fix branches on.
                monkeypatch.setattr(access_mod, "current_tenant", lambda: None)

                _folded, members = await resolve_session_access(sid, actor)

                assert member not in members, (
                    "the shared member resolved with the fold blind to the bind "
                    "— the FORCE-RLS'd participant read must return 0 rows "
                    "unbound, so this passing means the tables are not actually "
                    "FORCE-RLS'd or the fence does not exercise them")
                assert members == [actor], (
                    f"the unbound fold must collapse to the actor, got {members}")
            finally:
                clear_tenant()
                _unseed_shared_room(
                    promoted.admin_engine, org_id=promoted.org_a,
                    actor_email=actor, member_email=member,
                    actor_role="rsa-red-actor", member_role="rsa-red-member",
                    group_slug=group, session_id=sid)


@_DB_GATE
class TestEnsureOwnerBootstrapBindUnderForceRls:
    """``ensure_owner_bootstrap`` un-bricked under FORCE RLS.

    R7 name: ``owner-bootstrap-bound-under-rls``. At gateway STARTUP the function
    ran UNBOUND, so under phase-4 RLS its ``_HAS_OWNER_SQL`` read (via the
    FORCE-RLS'd ``user_role``) saw no owner and its ``_BOOTSTRAP_OWNER_SQL``
    INSERT into the FORCE-RLS'd ``app_user``/``user_role`` was WITH-CHECK-refused
    — an ownerless box could never bootstrap its first owner
    (``test_the_unbound_owner_bootstrap_write_is_rejected`` characterizes the raw
    write). The fix resolves the ``default`` org id from the RLS-EXEMPT
    ``organization`` table, then binds ``tenant_session(default_org_id)`` around
    both statements. The ``default`` org + its ``owner`` role are seeded by
    migration 130 and left ownerless, so the real function can be driven
    end-to-end here.
    """

    @staticmethod
    def _app_dsn(promoted) -> str:
        return promoted.app_url.render_as_string(hide_password=False)

    @staticmethod
    def _default_org_id(admin_engine) -> str:
        with admin_engine.connect() as c:
            return str(c.execute(text(
                "SELECT id FROM organization WHERE slug = :s"),
                {"s": _BOOTSTRAP_ORG_SLUG}).scalar_one())

    @staticmethod
    def _clean_default_owner(admin_engine, email: str) -> None:
        with admin_engine.begin() as conn:
            # app_user delete cascades its user_role; also drop any shadow rows
            # ensure_owner_bootstrap's post-commit mirror may have written.
            conn.execute(text(
                "DELETE FROM app_user WHERE lower(email) = lower(:e)"), {"e": email})
            conn.execute(text(
                "DELETE FROM org_membership m USING user_identity ui "
                " WHERE m.user_id = ui.id AND lower(ui.email) = lower(:e)"),
                {"e": email})
            conn.execute(text(
                "DELETE FROM user_identity WHERE lower(email) = lower(:e)"),
                {"e": email})

    async def test_the_real_bootstrap_provisions_the_owner_under_force_rls(
        self, promoted, monkeypatch
    ):
        """GREEN, end-to-end: the REAL ``ensure_owner_bootstrap`` — called as
        ``acb_app`` with the session UNBOUND, the exact startup posture — resolves
        the default org from the exempt table, binds it, and lands the owner. The
        re-run returns ``None`` because the now-bound ``_HAS_OWNER_SQL`` read sees
        the owner it just wrote: the has-owner read AND the INSERT, both bound."""
        candidate = "bootstrap.green@h3rls.test"
        self._clean_default_owner(promoted.admin_engine, candidate)
        monkeypatch.setenv("EXECUTIVE_EMAILS", f"{candidate}, second@h3rls.test")
        async with tenant_engine_scope(self._app_dsn(promoted)):
            try:
                access_mod.invalidate()
                monkeypatch.setattr(access_mod, "_tables_missing", False)
                clear_tenant()  # the precondition: startup runs UNBOUND

                first = await ensure_owner_bootstrap()
                assert first == candidate, (
                    "the bootstrap INSERT did not land under FORCE RLS — the "
                    "has-owner read/INSERT ran on a session with no GUC bound "
                    "and RLS refused/hid them (the write-brick the fix removes)")

                # The bound has-owner read now sees the owner → a no-op re-run.
                second = await ensure_owner_bootstrap()
                assert second is None, (
                    "the re-run did not see the owner it just wrote — the bound "
                    "_HAS_OWNER_SQL read is not actually seeing the FORCE-RLS'd "
                    "user_role row")
            finally:
                clear_tenant()
                self._clean_default_owner(promoted.admin_engine, candidate)

    def test_the_bootstrap_write_needs_the_guc_bind_under_force_rls(
        self, promoted
    ):
        """RED/GREEN at the SQL level: the exact ``_BOOTSTRAP_OWNER_SQL`` the
        function runs lands the owner on a GUC-bound session and is WITH-CHECK
        refused on an unbound one — so the fix's bind is load-bearing, not
        cosmetic. Paired with the has-owner read: bound sees the owner, unbound
        sees nothing.

        ⚠️ **NullPool, one connection per op.** A custom GUC set with ``SET
        LOCAL`` (``set_config(..., true)``) does not reset to NULL when the
        transaction ends — it resets to ``''``, which then makes the policy's
        ``current_setting('app.tenant_id', true)::uuid`` cast raise
        ``invalid input syntax for type uuid: ""`` on a REUSED pooled
        connection. Real startup is a FRESH connection that never bound a tenant,
        where the GUC is genuinely NULL and the read returns 0 rows cleanly — so
        each op here opens its own backend connection to reproduce that, not a
        pool artefact."""
        org_id = self._default_org_id(promoted.admin_engine)
        bound_ok = "bootstrap.sqlbound@h3rls.test"
        unbound_evil = "bootstrap.sqlunbound@h3rls.test"
        self._clean_default_owner(promoted.admin_engine, bound_ok)
        self._clean_default_owner(promoted.admin_engine, unbound_evil)
        eng = create_engine(promoted.app_url, future=True, poolclass=NullPool)
        try:
            # ── the WRITE, unbound → WITH CHECK refuses (the brick) ──
            with eng.connect() as c, c.begin():  # noqa: SIM117
                with pytest.raises((DBAPIError, ProgrammingError)) as exc:
                    c.execute(text(_BOOTSTRAP_OWNER_SQL),
                              {"email": unbound_evil,
                               "org_slug": _BOOTSTRAP_ORG_SLUG})
            assert "row-level security" in str(exc.value).lower(), (
                f"expected a WITH CHECK violation unbound, got: {exc.value}")

            # ── the WRITE, bound → the owner lands ──
            with eng.connect() as c, c.begin():
                c.execute(text("SELECT set_config('app.tenant_id', :o, true)"),
                          {"o": org_id})
                c.execute(text(_BOOTSTRAP_OWNER_SQL),
                          {"email": bound_ok, "org_slug": _BOOTSTRAP_ORG_SLUG})

            # ── the has-owner READ: bound sees the owner, unbound sees nothing ──
            with eng.connect() as c, c.begin():
                c.execute(text("SELECT set_config('app.tenant_id', :o, true)"),
                          {"o": org_id})
                bound_owner = c.execute(
                    text(_HAS_OWNER_SQL),
                    {"org_slug": _BOOTSTRAP_ORG_SLUG}).first()
            with eng.connect() as c, c.begin():
                unbound_owner = c.execute(
                    text(_HAS_OWNER_SQL),
                    {"org_slug": _BOOTSTRAP_ORG_SLUG}).first()
            assert bound_owner is not None, (
                "the bound has-owner read did not see the owner just written")
            assert unbound_owner is None, (
                "the unbound has-owner read saw an owner — user_role is not "
                "actually FORCE-RLS'd, so the brick would not reproduce")
        finally:
            eng.dispose()
            self._clean_default_owner(promoted.admin_engine, bound_ok)
            self._clean_default_owner(promoted.admin_engine, unbound_evil)


# ==========================================================================
# WS-29 H6 · NEW-ORG PROVISIONING under FORCE RLS (migration 185).
# `provision_organization` creates the org row + placement (RLS-EXEMPT, land
# unbound) then PERFORMs `provision_org_roles` + `provision_org_owner`, which
# write org_role_permission + app_user + user_role (all FORCE-RLS'd). On an
# UNBOUND session those writes are refused under phase-4 RLS → provisioning a
# customer post-flip fails. Migration 185 SETs `app.tenant_id` LOCAL to the org
# it just created (before the forced writes), so the create act's own writes
# satisfy the policy. Driven HERE as the non-privileged `acb_app` role on the
# phase-4-promoted catalog: GREEN the real 185 function provisions unbound; RED
# the no-set_config mutation is refused; the create-only guard (180) still holds.
# A bypass-role run is blind to all of it (scratch `acb` has `rolbypassrls`),
# which is the whole reason this reuses `promoted()`'s dedicated non-priv role.
# ==========================================================================
_PROVISION_MIGRATION = _ROOT / "infra" / "postgres" / "185_provision_org_rls_bind.sql"

#: The one line migration 185 adds to 179's provision_organization body. The RED
#: mutation removes exactly this and shows the create act then refuses under
#: FORCE RLS — so the SET LOCAL is load-bearing, not cosmetic.
_SET_LOCAL_LINE = "    PERFORM set_config('app.tenant_id', v_org_id::text, true);\n"


def _exec_admin_sql(admin_engine, sql: str) -> None:
    """Run *sql* verbatim as the superuser admin (installs/restores a function).

    Via the DBAPI cursor for the same reason ``_apply_generated_phase`` and
    ``_tenant_ladder._exec_file`` document: the simple query protocol carries a
    dollar-quoted CREATE OR REPLACE without SQLAlchemy handing psycopg an empty
    parameter set.
    """
    with admin_engine.begin() as conn, \
            conn.connection.dbapi_connection.cursor() as cur:
        cur.execute(sql)


def _purge_provisioned_org(admin_engine, slug: str) -> None:
    """Delete everything a provision left for *slug* (as the RLS-bypassing admin).

    ``app_user`` cascades ``user_role``; ``org_role`` cascades
    ``org_role_permission``. Keeps the shared module-scoped ``promoted`` catalog
    clean between provision tests."""
    with admin_engine.begin() as conn:
        oid = conn.execute(
            text("SELECT id FROM organization WHERE slug = :s"), {"s": slug}
        ).scalar()
        if oid is None:
            return
        conn.execute(text("DELETE FROM app_user WHERE organization_id = :o"),
                     {"o": oid})
        conn.execute(text("DELETE FROM org_role WHERE organization_id = :o"),
                     {"o": oid})
        conn.execute(text("DELETE FROM tenant_placement WHERE organization_id = :o"),
                     {"o": oid})
        conn.execute(text("DELETE FROM organization WHERE id = :o"), {"o": oid})


@_DB_GATE
class TestProvisionOrgBindUnderForceRls:
    """``provision_organization`` provisions under FORCE RLS via its own GUC bind.

    R7 name: ``provision-under-force-rls``. Drives the REAL migration-185
    ``provision_organization`` on the promoted two-org catalog as the
    non-privileged ``acb_app`` role, session UNBOUND — the exact posture the
    owner provisions a customer in post-flip.
    """

    @staticmethod
    def _app_engine(promoted):
        # NullPool + a fresh backend per op: a SET LOCAL GUC resets to '' (not
        # NULL) on a reused pooled connection, which is a pool artefact, not the
        # unbound-startup reality this fence reproduces (the bootstrap fence
        # above documents the same reason at length).
        return create_engine(promoted.app_url, future=True, poolclass=NullPool)

    def test_provisioning_succeeds_unbound_under_force_rls_green(self, promoted):
        """GREEN: as ``acb_app`` with NO ``app.tenant_id`` bound, the real 185
        function provisions a FULL org — org + placement (exempt) AND the
        FORCE-RLS'd org_role_permission + owner app_user + owner user_role — all
        land, because it binds ``app.tenant_id`` to the org it just created before
        the forced writes."""
        slug = f"prov-rls-green-{uuid.uuid4().hex[:8]}"
        owner = f"owner-{uuid.uuid4().hex[:8]}@prov.example"
        eng = self._app_engine(promoted)
        try:
            with eng.connect() as c, c.begin():
                org_id = c.execute(
                    text("SELECT provision_organization(:slug, :name, :owner)"),
                    {"slug": slug, "name": "Prov RLS", "owner": owner},
                ).scalar_one()
            assert org_id is not None, "provision_organization returned no id"

            # Verify what landed as the RLS-bypassing admin (sees every row).
            with promoted.admin_engine.connect() as a:
                placement = a.execute(text(
                    "SELECT count(*) FROM tenant_placement "
                    "WHERE organization_id = :o"), {"o": org_id}).scalar_one()
                perms = a.execute(text(
                    "SELECT count(*) FROM org_role_permission "
                    "WHERE organization_id = :o"), {"o": org_id}).scalar_one()
                owner_users = a.execute(text(
                    "SELECT count(*) FROM app_user "
                    "WHERE organization_id = :o AND lower(email) = lower(:e)"),
                    {"o": org_id, "e": owner}).scalar_one()
                owner_roles = a.execute(text(
                    "SELECT count(*) FROM user_role ur "
                    "  JOIN org_role r ON r.id = ur.role_id AND r.slug = 'owner' "
                    " WHERE r.organization_id = :o"), {"o": org_id}).scalar_one()
            assert placement == 1, "the exempt tenant_placement row did not land"
            assert perms > 0, (
                "org_role_permission (FORCE-RLS'd) got 0 rows — the roles seed "
                "was refused, so the internal bind did not reach it")
            assert owner_users == 1, "the owner app_user (FORCE-RLS'd) did not land"
            assert owner_roles == 1, "the owner user_role (FORCE-RLS'd) did not land"
        finally:
            eng.dispose()
            _purge_provisioned_org(promoted.admin_engine, slug)

    def test_reverting_the_set_local_refuses_the_provision_red(self, promoted):
        """RED (the mutation): install 185's body with the SET LOCAL line removed;
        as ``acb_app`` UNBOUND the create act's first FORCE-RLS'd write is refused
        and the whole atomic act writes NOTHING — proving the bind is load-bearing.

        The real 185 body is restored in ``finally`` so the mutation cannot leak
        into any other test on the shared ``promoted`` catalog."""
        original = _PROVISION_MIGRATION.read_text(encoding="utf-8")
        mutated = original.replace(_SET_LOCAL_LINE, "", 1)
        assert mutated != original, (
            "the SET LOCAL line was not found to remove — this mutation would be "
            "vacuous; check _SET_LOCAL_LINE still matches migration 185")

        slug = f"prov-rls-red-{uuid.uuid4().hex[:8]}"
        owner = f"owner-{uuid.uuid4().hex[:8]}@prov.example"
        eng = self._app_engine(promoted)
        try:
            _exec_admin_sql(promoted.admin_engine, mutated)  # install the mutant
            with eng.connect() as c, c.begin():  # noqa: SIM117
                with pytest.raises((DBAPIError, ProgrammingError)) as exc:
                    c.execute(
                        text("SELECT provision_organization(:slug, :name, :owner)"),
                        {"slug": slug, "name": "Prov RLS Red", "owner": owner})
            msg = str(exc.value).lower()
            assert ("row-level security" in msg
                    or "violates not-null constraint" in msg), (
                "the unbound mutant was refused for an unexpected reason — expected "
                f"an RLS WITH CHECK or a NULL-org (unset-GUC DEFAULT) refusal: {exc.value}")
            # Atomic: the whole act rolled back, so not even the exempt org row
            # survives.
            with promoted.admin_engine.connect() as a:
                left = a.execute(text(
                    "SELECT count(*) FROM organization WHERE slug = :s"),
                    {"s": slug}).scalar_one()
            assert left == 0, "the refused provision left an organization row behind"
        finally:
            eng.dispose()
            _exec_admin_sql(promoted.admin_engine, original)  # restore the real 185
            _purge_provisioned_org(promoted.admin_engine, slug)

    def test_the_owner_forced_writes_need_the_guc_bind_red_green(self, promoted):
        """The app_user/user_role write, isolated: with the org + roles seeded,
        ``provision_org_owner``'s ``app_user`` INSERT (explicit ``organization_id``)
        is WITH-CHECK refused on an UNBOUND session (RED) and lands on a GUC-bound
        one (GREEN) — the exact ``app_user``/``user_role`` refusal migration 185's
        internal bind removes, pinned at the SQL level (the bootstrap fence's
        shape, on the provisioning owner path)."""
        slug = f"prov-rls-owner-{uuid.uuid4().hex[:8]}"
        unbound_evil = f"evil-{uuid.uuid4().hex[:8]}@prov.example"
        bound_ok = f"ok-{uuid.uuid4().hex[:8]}@prov.example"
        eng = self._app_engine(promoted)
        try:
            # Seed the org + its roles as the admin (bypasses RLS), leaving it
            # OWNERLESS — so provision_org_owner is the FIRST forced owner write.
            with promoted.admin_engine.begin() as a:
                org_id = a.execute(text(
                    "INSERT INTO organization (slug, display_name) "
                    "VALUES (:s, :s) RETURNING id"), {"s": slug}).scalar_one()
                a.execute(text("SELECT set_config('app.tenant_id', :o, true)"),
                          {"o": str(org_id)})
                a.execute(text("SELECT provision_org_roles(:o)"), {"o": org_id})

            # ── UNBOUND → the app_user WITH CHECK refuses (the brick) ──
            with eng.connect() as c, c.begin():  # noqa: SIM117
                with pytest.raises((DBAPIError, ProgrammingError)) as exc:
                    c.execute(text("SELECT provision_org_owner(:o, :e)"),
                              {"o": org_id, "e": unbound_evil})
            assert "row-level security" in str(exc.value).lower(), (
                "expected a WITH CHECK violation on the unbound owner app_user "
                f"insert, got: {exc.value}")

            # ── BOUND → the owner app_user + user_role land ──
            with eng.connect() as c, c.begin():
                c.execute(text("SELECT set_config('app.tenant_id', :o, true)"),
                          {"o": str(org_id)})
                c.execute(text("SELECT provision_org_owner(:o, :e)"),
                          {"o": org_id, "e": bound_ok})
            with promoted.admin_engine.connect() as a:
                landed = a.execute(text(
                    "SELECT count(*) FROM user_role ur "
                    "  JOIN app_user au ON au.id = ur.user_id "
                    "  JOIN org_role r ON r.id = ur.role_id AND r.slug = 'owner' "
                    " WHERE r.organization_id = :o "
                    "   AND lower(au.email) = lower(:e)"),
                    {"o": org_id, "e": bound_ok}).scalar_one()
                evil = a.execute(text(
                    "SELECT count(*) FROM app_user WHERE lower(email) = lower(:e)"),
                    {"e": unbound_evil}).scalar_one()
            assert landed == 1, "the bound owner app_user + user_role did not land"
            assert evil == 0, "the refused unbound owner insert left a row behind"
        finally:
            eng.dispose()
            _purge_provisioned_org(promoted.admin_engine, slug)

    def test_the_create_only_guard_still_holds_under_force_rls(self, promoted):
        """180's create-only guard survives the bind (and the bind is what lets it
        WORK): as ``acb_app`` unbound the real 185 function provisions ``alice`` as
        owner, then a FRESH ``carol`` claiming the same slug is refused with the
        dedicated SQLSTATE ``P1002``, and re-provisioning as ``alice`` grants the
        owner role exactly once."""
        from acb_common.provisioning import SQLSTATE_SLUG_OWNED_BY_ANOTHER

        slug = f"prov-rls-guard-{uuid.uuid4().hex[:8]}"
        alice = f"alice-{uuid.uuid4().hex[:8]}@prov.example"
        carol = f"carol-{uuid.uuid4().hex[:8]}@prov.example"
        eng = self._app_engine(promoted)
        try:
            with eng.connect() as c, c.begin():
                c.execute(text("SELECT provision_organization(:s, :s, :e)"),
                          {"s": slug, "e": alice})
            # A fresh carol claiming alice's slug → P1002 (create-only), refused.
            with eng.connect() as c, c.begin():  # noqa: SIM117
                with pytest.raises((DBAPIError, ProgrammingError)) as exc:
                    c.execute(text("SELECT provision_organization(:s, :s, :e)"),
                              {"s": slug, "e": carol})
            assert getattr(exc.value.orig, "sqlstate", None) == \
                SQLSTATE_SLUG_OWNED_BY_ANOTHER, (
                    f"expected the create-only SQLSTATE {SQLSTATE_SLUG_OWNED_BY_ANOTHER}, "
                    f"got {getattr(exc.value.orig, 'sqlstate', None)}")
            # Idempotent: re-provisioning as alice grants the owner role once.
            with eng.connect() as c, c.begin():
                c.execute(text("SELECT provision_organization(:s, :s, :e)"),
                          {"s": slug, "e": alice})
            with promoted.admin_engine.connect() as a:
                owners = a.execute(text(
                    "SELECT count(*) FROM user_role ur "
                    "  JOIN org_role r ON r.id = ur.role_id AND r.slug = 'owner' "
                    "  JOIN organization o ON o.id = r.organization_id "
                    " WHERE o.slug = :s"), {"s": slug}).scalar_one()
                carol_rows = a.execute(text(
                    "SELECT count(*) FROM app_user WHERE lower(email) = lower(:e)"),
                    {"e": carol}).scalar_one()
            assert owners == 1, f"the create-only slug has {owners} owners, not 1"
            assert carol_rows == 0, "the refused carol got an app_user row anyway"
        finally:
            eng.dispose()
            _purge_provisioned_org(promoted.admin_engine, slug)


# ==========================================================================
# Structural — no database, and these must NEVER skip (they arm the R8 gate).
# ==========================================================================
class TestThisSuiteIsArmed:
    """The hand-maintained CI skip guard discovers nothing (pr-check.yml's own
    warning), so an R8 suite absent from it skips silently and leaves the job
    green — the exact failure that step exists to catch."""

    def test_this_suite_is_named_in_the_ci_skip_guard(self):
        workflow = (_ROOT / ".github/workflows/pr-check.yml").read_text(
            encoding="utf-8")
        assert "tests/unit/test_h3_rls_promotion_rehearsal.py" in workflow

    def test_the_owning_spec_names_this_suite(self):
        spec = (_ROOT / "project-docs/specs/saas_multitenancy_handover.md").read_text(
            encoding="utf-8")
        assert "test_h3_rls_promotion_rehearsal.py" in spec
