"""Provisioning as a role that FORCE ROW LEVEL SECURITY actually applies to.

Spec: ``project-docs/specs/saas_multitenancy.md`` §11 MT-1j · WS-29 H6 ·
migration 202.

⚠️ **Every other provisioning suite runs as the OWNER of the tables, and an
owner bypasses FORCE RLS.** So they answer a question production never asks.

That gap let H-104's THIRD head reach production. Migrations 200 and 201 fixed
two NOT NULL omissions, and ``SELECT provision_organization(...)`` then
succeeded — run as ``postgres``. The gateway runs as ``acb_app``, and the same
call failed there with::

    new row violates row-level security policy for table "org_role_permission"

Migration **185** exists precisely to prevent that: it binds ``app.tenant_id``
to the new organization before the FORCE-RLS'd writes. 185 is in production's
ledger. **Its body was not in production's function** — the live definition was
179's, and the ledger cannot repair a ``CREATE OR REPLACE`` whose object drifted
afterwards. Migration 202 re-asserts it forward-only.

**This suite is the fence that had to exist for any of that to be visible**: it
provisions as a non-owner role, with the tenancy phase and the RLS policy
applied, which is the shape production has.

⚠️ **RED without 202, GREEN with it.** That is asserted below rather than
claimed here — ``test_the_bind_is_what_makes_it_pass`` strips the bind from the
function, proves provisioning then fails, and restores it.
"""
from __future__ import annotations

import os
import uuid

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from tests.unit._tenant_ladder import apply_ladder

_TENANT_URL = os.environ.get("TENANT_LADDER_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not _TENANT_URL,
    reason=(
        "TENANT_LADDER_DATABASE_URL unset — R8 requires a REAL Postgres. A skip "
        "here is not a pass; CI must set it."
    ),
)

#: The three tables 185's header names as the FORCE-RLS'd writes the create act
#: makes. `organization` and `tenant_placement` are deliberately absent: they are
#: RLS-EXEMPT (no policy in `generated/04_policies.sql`), which is what lets the
#: organization row exist before there is a tenant to bind to.
_FORCED = ("org_role_permission", "app_user", "user_role")

#: The non-owner role. FORCE RLS still exempts the table OWNER, so a suite that
#: stays on the ladder's own role tests nothing about this.
_APP_ROLE = "acb_rls_probe"


@pytest.fixture(scope="module")
def db():
    eng = create_engine(_TENANT_URL, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    yield eng
    eng.dispose()


@pytest.fixture
def forced_rls(db):
    """Give the provisioning path PRODUCTION's shape: tenancy column + policy.

    Adds the tenancy column where it is missing, backfills from the row's own
    role so the constraint closes over real data, enables and FORCES RLS, and
    creates the same policy ``generated/04_policies.sql`` writes. Everything it
    added, it removes.
    """
    added: list[str] = []
    with db.begin() as c:
        for table in _FORCED:
            present = c.execute(
                text(
                    "SELECT 1 FROM information_schema.columns "
                    " WHERE table_name = :t AND column_name = 'organization_id'"
                ),
                {"t": table},
            ).first()
            if not present:
                c.execute(
                    text(f"ALTER TABLE {table} ADD COLUMN organization_id UUID")
                )
                added.append(table)

        # Own the pre-existing rows before tightening anything around them.
        c.execute(text(
            "UPDATE org_role_permission p SET organization_id = r.organization_id"
            "  FROM org_role r WHERE r.id = p.role_id"
            " AND p.organization_id IS NULL"))
        c.execute(text(
            "UPDATE user_role ur SET organization_id = r.organization_id"
            "  FROM org_role r WHERE r.id = ur.role_id"
            " AND ur.organization_id IS NULL"))

        c.execute(text(
            f"DO $$ BEGIN IF NOT EXISTS ("
            f"  SELECT 1 FROM pg_roles WHERE rolname = '{_APP_ROLE}') THEN"
            f"  CREATE ROLE {_APP_ROLE} NOLOGIN; END IF; END $$;"))
        c.execute(text(
            f"GRANT USAGE ON SCHEMA public TO {_APP_ROLE}"))
        c.execute(text(
            "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA "
            f"public TO {_APP_ROLE}"))
        c.execute(text(
            f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {_APP_ROLE}"))

        for table in _FORCED:
            c.execute(text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
            c.execute(text(f"ALTER TABLE {table} FORCE  ROW LEVEL SECURITY"))
            c.execute(text(
                f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}"))
            c.execute(text(
                f"CREATE POLICY {table}_tenant_isolation ON {table}"
                "    USING      (organization_id ="
                "        current_setting('app.tenant_id', true)::uuid)"
                "    WITH CHECK (organization_id ="
                "        current_setting('app.tenant_id', true)::uuid)"))
    try:
        yield
    finally:
        with db.begin() as c:
            for table in _FORCED:
                c.execute(text(
                    f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}"))
                c.execute(text(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY"))
                c.execute(text(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY"))
            for table in added:
                c.execute(text(f"ALTER TABLE {table} DROP COLUMN organization_id"))


def _slug() -> str:
    return f"rls-{uuid.uuid4().hex[:8]}"


def _provision_as_app_role(conn, slug: str):
    """Provision while acting as the non-owner role, as the gateway does."""
    conn.execute(text(f"SET LOCAL ROLE {_APP_ROLE}"))
    return conn.execute(
        text("SELECT provision_organization(CAST(:s AS TEXT), "
             "  CAST(:n AS TEXT), CAST(:e AS TEXT))"),
        {"s": slug, "n": "RLS Co", "e": f"{slug}@example.invalid"},
    ).scalar_one()


class TestProvisioningUnderForcedRLS:
    """The shape production has, as the role production uses."""

    def test_provisioning_SUCCEEDS_as_a_non_owner_role(self, db, forced_rls):
        """Red before migration 202 with:

            new row violates row-level security policy for table
            "org_role_permission"
        """
        slug = _slug()
        with db.begin() as c:
            org_id = _provision_as_app_role(c, slug)
        assert org_id

    def test_the_owner_really_got_their_grant(self, db, forced_rls):
        """Non-vacuity: a create act that wrote nothing would also not be refused.

        `user_role` is the LAST forced write in the act, so a row here proves the
        bind survived through every one of them.
        """
        slug = _slug()
        with db.begin() as c:
            org_id = _provision_as_app_role(c, slug)

        with db.connect() as c:
            grants = c.execute(
                text("SELECT count(*) FROM user_role ur"
                     "  JOIN org_role r ON r.id = ur.role_id"
                     " WHERE r.organization_id = CAST(:o AS UUID)"
                     "   AND r.slug = 'owner'"),
                {"o": org_id},
            ).scalar_one()
            perms = c.execute(
                text("SELECT count(*) FROM org_role_permission p"
                     " WHERE p.organization_id = CAST(:o AS UUID)"),
                {"o": org_id},
            ).scalar_one()
        assert grants == 1
        assert perms > 0

    def test_the_bind_does_not_LEAK_to_the_next_transaction(self, db, forced_rls):
        """`set_config(..., is_local => true)` is transaction-scoped.

        A session-scoped bind would leave a pooled connection silently acting as
        the last organization provisioned on it — a cross-tenant read, handed out
        by the connection pool.
        """
        with db.begin() as c:
            _provision_as_app_role(c, _slug())
        with db.connect() as c:
            leaked = c.execute(
                text("SELECT current_setting('app.tenant_id', true)")
            ).scalar_one()
        assert leaked in (None, "")


class TestTheFenceIsNotVACUOUS:
    """Prove the suite can fail, by removing the fix and watching it fail."""

    def test_the_bind_is_what_makes_it_pass(self, db, forced_rls):
        """Strip the `set_config` line, prove the refusal returns, put it back.

        ⚠️ This is the assertion that makes every test above mean something. The
        200-era fence was green while production was broken, so "the suite
        passes" is not by itself evidence about a suite.
        """
        with db.connect() as c:
            original = c.execute(
                text("SELECT pg_get_functiondef(p.oid) FROM pg_proc p"
                     "  JOIN pg_namespace n ON n.oid = p.pronamespace"
                     " WHERE n.nspname = 'public'"
                     "   AND p.proname = 'provision_organization'")
            ).scalar_one()
        assert "set_config('app.tenant_id'" in original, (
            "migration 202 is not applied to this database — the ladder must "
            "carry it, or this fence is testing 179's body and proves nothing"
        )

        unbound = original.replace(
            "PERFORM set_config('app.tenant_id', v_org_id::text, true);", ""
        )
        assert unbound != original

        try:
            with db.begin() as c:
                c.execute(text(unbound))

            # ⚠️ A FRESH session, not a pooled one. Once `app.tenant_id` has been
            # set anywhere in a session, `current_setting(..., true)` returns ''
            # there forever after rather than NULL — so a reused connection fails
            # on `''::uuid` instead of on the policy, which is a different bug
            # from the one production has. A connection that never bound it is
            # what the gateway starts from.
            fresh = create_engine(_TENANT_URL, future=True, poolclass=NullPool)
            try:
                with pytest.raises(Exception) as caught:
                    with fresh.begin() as c:
                        _provision_as_app_role(c, _slug())
            finally:
                fresh.dispose()
            assert "row-level security" in str(caught.value).lower(), (
                "expected the RLS refusal production got; saw: "
                f"{caught.value}"
            )
        finally:
            with db.begin() as c:
                c.execute(text(original))

        # And it works again once the bind is back.
        with db.begin() as c:
            assert _provision_as_app_role(c, _slug())
