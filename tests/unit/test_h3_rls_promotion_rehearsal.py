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
    _BOOTSTRAP_OWNER_SQL,
    _IDENTITY_LEG_SQL,
    mirror_identity_membership,
    resolve_identity,
)
from acb_auth.deps import _with_resolved_access
from acb_auth.roles import UserContext, UserRole
from acb_common.db import clear_tenant
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, ProgrammingError

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
