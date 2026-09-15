"""Provisioning an organization on a TENANCY-APPLIED database (H-104).

Spec: ``project-docs/specs/saas_multitenancy.md`` §11 MT-1j · migration 200.

⚠️ **This suite exists because the two databases DISAGREE, and every existing
test runs on the one production is not.**

The tenancy phase adds ``org_role_permission.organization_id`` and makes it NOT
NULL (``infra/postgres/generated/03_constraints.sql:1029``). Those files live in
a SUBDIRECTORY, and ``scripts/apply_migrations.sh`` globs
``[0-9][0-9]*_*.sql`` in ``infra/postgres`` only — so the ladder never replays
them. A fresh developer database and CI's replay have no such column. Production
does.

``provision_org_roles``'s grant INSERT named only ``(role_id, permission)``, so
on production it raised ``null value in column "organization_id" … violates
not-null constraint``. That function is what a NEW organization is built from,
so **every create failed there**: self-serve signup, the operator path, and
CP-2i's bootstrap sweep alike. M1 is *a second org can exist safely*, and a
second org could not be created at all.

``tests/unit/test_org_provisioning.py`` passes throughout, because its database
has no such column. **This suite applies the column itself**, so it tests the
shape production actually has — which is the whole point, and the reason a green
run elsewhere proved nothing.
"""
from __future__ import annotations

import os
import uuid

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import create_engine, text

from tests.unit._tenant_ladder import apply_ladder

_TENANT_URL = os.environ.get("TENANT_LADDER_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not _TENANT_URL,
    reason=(
        "TENANT_LADDER_DATABASE_URL unset — R8 requires a REAL Postgres with "
        "pgvector. A skip here is not a pass; CI must set it."
    ),
)


@pytest.fixture(scope="module")
def db():
    eng = create_engine(_TENANT_URL, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    yield eng
    eng.dispose()


#: The tables the PROVISIONING PATH writes that production carries
#: `organization_id NOT NULL` on, measured 2026-09-15.
#:
#: ⚠️ **This was `org_role_permission` ALONE until migration 201, and that is
#: exactly how H-104's second head reached production.** The fixture modelled
#: the one table migration 200 was about, so the suite passed while
#: `provision_org_owner` still raised on `user_role` one statement later. A test
#: that builds a PARTIAL copy of the failing environment is worth exactly as
#: much as the copy — which was nothing for the statement it did not model.
#:
#: The other five production carries the column on (`app_user`,
#: `org_membership`, `org_role`, `tenant_placement`,
#: `user_permission_override`) are deliberately NOT tightened here: the
#: provisioning functions already name the column on every one they write, and
#: `app_user`'s column is real ladder schema with dependent objects, so touching
#: it destroys rather than models.
_TENANCY_TABLES = ("org_role_permission", "user_role")


@pytest.fixture
def tenancy_applied(db):
    """Make the provisioning path see PRODUCTION's shape, and put it back after.

    ⚠️ **This fixture IS the test's subject.** Without it the suite runs on the
    ladder's own shape — the schema that hid this defect class for as long as it
    existed, because `infra/postgres/generated/` is not on the ladder.

    Adds only what is missing, BACKFILLS rather than deletes (the ladder's
    seeded rows are what the other suites read), and drops only what it added.
    """
    added: list[str] = []
    with db.begin() as c:
        for table in _TENANCY_TABLES:
            present = c.execute(
                text(
                    "SELECT 1 FROM information_schema.columns "
                    " WHERE table_name = :t AND column_name = 'organization_id'"
                ),
                {"t": table},
            ).first()
            if present:
                continue
            c.execute(text(
                f"ALTER TABLE {table} ADD COLUMN organization_id UUID"))
            added.append(table)

        # Own the pre-existing rows from whatever names their tenant, so the
        # constraint can be tightened around real data rather than over a hole.
        c.execute(text(
            "UPDATE org_role_permission p SET organization_id = r.organization_id "
            "  FROM org_role r WHERE r.id = p.role_id "
            " AND p.organization_id IS NULL"))
        c.execute(text(
            "UPDATE user_role ur SET organization_id = r.organization_id "
            "  FROM org_role r WHERE r.id = ur.role_id "
            " AND ur.organization_id IS NULL"))
        for table in added:
            c.execute(text(
                f"ALTER TABLE {table} "
                "  ALTER COLUMN organization_id SET NOT NULL"))
    try:
        yield
    finally:
        with db.begin() as c:
            for table in added:
                c.execute(text(
                    f"ALTER TABLE {table} DROP COLUMN organization_id"))


def _slug() -> str:
    return f"h104-{uuid.uuid4().hex[:8]}"


class TestProvisioningOnTheProductionShape:
    """The defect, and the fix, on the schema that has it."""

    def test_provisioning_an_organization_SUCCEEDS(self, db, tenancy_applied):
        """Red before migration 200 with:

            null value in column "organization_id" of relation
            "org_role_permission" violates not-null constraint
        """
        slug = _slug()
        with db.begin() as c:
            org_id = c.execute(
                text("SELECT provision_organization(CAST(:s AS TEXT), "
                     "  CAST(:n AS TEXT), NULL)"),
                {"s": slug, "n": "H104 Co"},
            ).scalar_one()
        assert org_id

    def test_every_grant_row_is_OWNED(self, db, tenancy_applied):
        """The column is not merely satisfied, it is CORRECT.

        A fix that wrote any non-null value would pass the constraint and leave
        one organization holding another's grants — which under FORCE ROW LEVEL
        SECURITY is a cross-tenant leak, not a tidiness problem.
        """
        slug = _slug()
        with db.begin() as c:
            org_id = c.execute(
                text("SELECT provision_organization(CAST(:s AS TEXT), "
                     "  CAST(:n AS TEXT), NULL)"),
                {"s": slug, "n": "H104 Co"},
            ).scalar_one()

        with db.connect() as c:
            mismatched = c.execute(
                text(
                    "SELECT count(*) FROM org_role_permission p "
                    "  JOIN org_role r ON r.id = p.role_id "
                    " WHERE r.organization_id = CAST(:o AS UUID) "
                    "   AND p.organization_id IS DISTINCT FROM r.organization_id"
                ),
                {"o": org_id},
            ).scalar_one()
            granted = c.execute(
                text(
                    "SELECT count(*) FROM org_role_permission p "
                    "  JOIN org_role r ON r.id = p.role_id "
                    " WHERE r.organization_id = CAST(:o AS UUID)"
                ),
                {"o": org_id},
            ).scalar_one()

        assert mismatched == 0
        # Non-vacuity: a function that seeded NOTHING would pass the check
        # above trivially.
        assert granted > 0

    def test_the_six_system_roles_are_all_there(self, db, tenancy_applied):
        """The seed's own contract, re-asserted on this schema.

        Migration 200 restates the whole function, so the role table is the
        thing most at risk of an edit nobody meant — and a hand-copied one was
        in fact written and thrown away during this build.
        """
        slug = _slug()
        with db.begin() as c:
            org_id = c.execute(
                text("SELECT provision_organization(CAST(:s AS TEXT), "
                     "  CAST(:n AS TEXT), NULL)"),
                {"s": slug, "n": "H104 Co"},
            ).scalar_one()

        with db.connect() as c:
            slugs = {
                r[0] for r in c.execute(
                    text("SELECT slug FROM org_role "
                         " WHERE organization_id = CAST(:o AS UUID)"),
                    {"o": org_id},
                )
            }
        assert slugs == {
            "owner", "admin", "manager", "member", "guest", "agent_service",
        }

    def test_the_owner_role_still_holds_the_WILDCARD(self, db, tenancy_applied):
        """The single most consequential grant in the table.

        If restating the function had altered the permission vocabulary — which
        a hand-copy did, before this was caught — the owner of every new
        organization would have silently lost `*`.
        """
        slug = _slug()
        with db.begin() as c:
            org_id = c.execute(
                text("SELECT provision_organization(CAST(:s AS TEXT), "
                     "  CAST(:n AS TEXT), NULL)"),
                {"s": slug, "n": "H104 Co"},
            ).scalar_one()

        with db.connect() as c:
            perms = {
                r[0] for r in c.execute(
                    text(
                        "SELECT p.permission FROM org_role_permission p "
                        "  JOIN org_role r ON r.id = p.role_id "
                        " WHERE r.organization_id = CAST(:o AS UUID) "
                        "   AND r.slug = 'owner'"
                    ),
                    {"o": org_id},
                )
            }
        assert perms == {"*"}

    def test_it_is_still_IDEMPOTENT(self, db, tenancy_applied):
        """179's property, unchanged. Twice called, one organization."""
        slug = _slug()
        with db.begin() as c:
            first = c.execute(
                text("SELECT provision_organization(CAST(:s AS TEXT), "
                     "  CAST(:n AS TEXT), NULL)"),
                {"s": slug, "n": "H104 Co"},
            ).scalar_one()
            second = c.execute(
                text("SELECT provision_organization(CAST(:s AS TEXT), "
                     "  CAST(:n AS TEXT), NULL)"),
                {"s": slug, "n": "H104 Co"},
            ).scalar_one()
        assert first == second

        with db.connect() as c:
            roles = c.execute(
                text("SELECT count(*) FROM org_role "
                     " WHERE organization_id = CAST(:o AS UUID)"),
                {"o": first},
            ).scalar_one()
        assert roles == 6


class TestTheLadderShapeIsUnCHANGED:
    """The other database still behaves exactly as it did.

    Migration 200 has to be a no-op for a schema without the column, or fixing
    production breaks every developer and CI at once.
    """

    def test_provisioning_still_works_without_the_column(self, db):
        # No `tenancy_applied` fixture here — this is the ladder's own shape.
        slug = _slug()
        with db.begin() as c:
            org_id = c.execute(
                text("SELECT provision_organization(CAST(:s AS TEXT), "
                     "  CAST(:n AS TEXT), NULL)"),
                {"s": slug, "n": "Ladder Co"},
            ).scalar_one()

        with db.connect() as c:
            roles = c.execute(
                text("SELECT count(*) FROM org_role "
                     " WHERE organization_id = CAST(:o AS UUID)"),
                {"o": org_id},
            ).scalar_one()
            granted = c.execute(
                text(
                    "SELECT count(*) FROM org_role_permission p "
                    "  JOIN org_role r ON r.id = p.role_id "
                    " WHERE r.organization_id = CAST(:o AS UUID)"
                ),
                {"o": org_id},
            ).scalar_one()
        assert roles == 6
        assert granted > 0
