"""CP-2g's TENANT half — ``purge_tenant_organization`` destroys one org, only.

The Console owns the lifecycle and its own registry purge
(``test_org_purge_console.py``); this suite fences the tenant-plane
destruction: one ``DELETE FROM organization`` whose FK cascade removes every
tenant-scoped table's rows, the OTHER organization's rows surviving whole,
the global ``user_identity`` surviving (it becomes D51's org-less sign-in),
and the ``already_absent`` retry arm.

**R8** — real Postgres via the tenant ladder (``TENANT_LADDER_DATABASE_URL``):
the whole feature IS FK topology, which a hermetic fake cannot disagree with.
The suite's sharpest lesson is pinned in ``TestTheExclusionCannotGoStale``:
the ``crm_*`` tables' ``organization_id`` references ``crm_organizations``
(a CRM company record), NOT the tenant — discovered when this suite's first
draft seeded them as tenant rows and the FK refused.

⚠️ Writes are COMMITTED (the function opens its own sessions); every test
seeds unique rows and cleans up in ``finally``.
"""
from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import create_engine, text

from tests.unit._tenant_ladder import apply_ladder, tenant_engine_scope

_URL = os.environ.get("TENANT_LADDER_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not _URL,
    reason=(
        "TENANT_LADDER_DATABASE_URL unset — R8 requires a REAL Postgres. "
        "A skip here is not a pass; CI must set it."
    ),
)


@pytest.fixture(scope="module")
def eng():
    engine = create_engine(_URL, future=True)
    with engine.begin() as conn:
        apply_ladder(conn)
    yield engine
    engine.dispose()


def _seed_org(conn, *, slug: str) -> str:
    """One org with rows on three different cascade tables."""
    org = str(
        conn.execute(
            text(
                "INSERT INTO organization (slug, display_name) "
                "VALUES (:s, :s) RETURNING id"
            ),
            {"s": slug},
        ).scalar_one()
    )
    email = f"owner@{slug}.example"
    uid = str(
        conn.execute(
            text(
                "INSERT INTO user_identity (email, display_name) "
                "VALUES (:e, '') RETURNING id"
            ),
            {"e": email},
        ).scalar_one()
    )
    conn.execute(
        text(
            "INSERT INTO org_membership (organization_id, user_id, status) "
            "VALUES (CAST(:o AS uuid), CAST(:u AS uuid), 'active')"
        ),
        {"o": org, "u": uid},
    )
    conn.execute(
        text(
            "INSERT INTO app_user (email, status, organization_id) "
            "VALUES (:e, 'active', CAST(:o AS uuid))"
        ),
        {"e": email, "o": org},
    )
    return org


def _counts(conn, org: str) -> dict[str, int]:
    out = {}
    for table in ("organization", "org_membership", "app_user"):
        col = "id" if table == "organization" else "organization_id"
        out[table] = conn.execute(
            text(f"SELECT count(*) FROM {table} WHERE {col} = CAST(:o AS uuid)"),
            {"o": org},
        ).scalar_one()
    return out


def _cleanup(conn, *, slug: str, org: str):
    conn.execute(
        text("DELETE FROM organization WHERE id = CAST(:o AS uuid)"), {"o": org}
    )
    conn.execute(
        text("DELETE FROM user_identity WHERE email = :e"),
        {"e": f"owner@{slug}.example"},
    )
    conn.execute(
        text("DELETE FROM app_user WHERE email = :e"),
        {"e": f"owner@{slug}.example"},
    )


class TestThePurge:
    async def test_one_org_dies_whole_and_its_neighbour_survives_whole(self, eng):
        """The headline: the victim's rows are gone from every seeded table via
        the cascade; the second org — same tables, same shapes — keeps all."""
        from acb_auth.offboard import purge_tenant_organization

        victim = f"purge-{uuid.uuid4().hex[:8]}"
        bystander = f"stays-{uuid.uuid4().hex[:8]}"
        with eng.begin() as conn:
            v_org = _seed_org(conn, slug=victim)
            b_org = _seed_org(conn, slug=bystander)
        try:
            async with tenant_engine_scope(_URL):
                receipt = await purge_tenant_organization(slug=victim)

            assert receipt["already_absent"] is False
            assert receipt["deleted"] == {"organization": 1}

            with eng.begin() as conn:
                gone = _counts(conn, v_org)
                kept = _counts(conn, b_org)
            assert gone == {k: 0 for k in gone}, gone
            assert kept == {
                "organization": 1, "org_membership": 1, "app_user": 1,
            }, kept
        finally:
            with eng.begin() as conn:
                _cleanup(conn, slug=victim, org=v_org)
                _cleanup(conn, slug=bystander, org=b_org)

    async def test_the_identity_survives_the_purge(self, eng):
        """``user_identity`` is global and email-keyed — after the purge the
        person is exactly the org-less sign-in D51's chooser exists for."""
        from acb_auth.offboard import purge_tenant_organization

        slug = f"purge-{uuid.uuid4().hex[:8]}"
        with eng.begin() as conn:
            org = _seed_org(conn, slug=slug)
        try:
            async with tenant_engine_scope(_URL):
                await purge_tenant_organization(slug=slug)
            with eng.begin() as conn:
                left = conn.execute(
                    text("SELECT count(*) FROM user_identity WHERE email = :e"),
                    {"e": f"owner@{slug}.example"},
                ).scalar_one()
            assert left == 1
        finally:
            with eng.begin() as conn:
                _cleanup(conn, slug=slug, org=org)

    async def test_a_mixed_case_slug_is_matched_byte_exactly(self, eng):
        """R7 for offboard's "never case-fold" rule (repair round 2): the
        tenant lookup must take the slug byte-identically. Round 1's `.lower()`
        made a mixed-case org an `already_absent` no-op that answered 200 —
        re-adding any normalisation turns this red."""
        from acb_auth.offboard import purge_tenant_organization

        slug = f"Purge-Mixed-{uuid.uuid4().hex[:8]}"
        with eng.begin() as conn:
            org = _seed_org(conn, slug=slug)
        try:
            async with tenant_engine_scope(_URL):
                receipt = await purge_tenant_organization(slug=slug)
            assert receipt["already_absent"] is False, receipt
            assert receipt["deleted"] == {"organization": 1}
        finally:
            with eng.begin() as conn:
                _cleanup(conn, slug=slug, org=org)

    async def test_an_absent_org_answers_already_absent(self, eng):
        """The retry arm: a half-failed two-plane purge is just run again."""
        from acb_auth.offboard import purge_tenant_organization

        async with tenant_engine_scope(_URL):
            receipt = await purge_tenant_organization(
                slug=f"never-{uuid.uuid4().hex[:8]}"
            )
        assert receipt == {
            "slug": receipt["slug"], "already_absent": True, "deleted": {},
        }

    async def test_a_blank_slug_is_refused_not_a_full_table_scan(self, eng):
        from acb_auth.offboard import purge_tenant_organization

        with pytest.raises(ValueError):
            await purge_tenant_organization(slug="   ")


class TestTheExclusionCannotGoStale:
    def test_every_organization_id_cascades_to_the_tenant_or_is_named_crm(
        self, eng
    ):
        """Every column NAMED ``organization_id`` must either (a) cascade to
        the tenant ``organization`` table — so the purge's single DELETE
        reaches it — or (b) be one of the named ``crm_*`` tables whose
        ``organization_id`` is a DIFFERENT fact (an FK to
        ``crm_organizations``, the CRM company record — measured 2026-08-24
        when seeding them as tenant rows was refused by that very FK).

        The red arms, both deliberate: a NEW tenant table without the cascade
        FK would orphan rows the purge claims to destroy; and the day MT-1j
        threads the CRM family onto real tenancy, the exclusion stops being
        true and the purge must grow their deletes."""
        from acb_auth.offboard import _NOT_TENANT_SCOPED

        with eng.begin() as conn:
            not_cascading = {
                r[0]
                for r in conn.execute(text(
                    "SELECT col.table_name "
                    "FROM information_schema.columns col "
                    "WHERE col.column_name = 'organization_id' "
                    "  AND col.table_schema = 'public' "
                    "  AND col.table_name NOT IN ("
                    "    SELECT rel.relname FROM pg_constraint c "
                    "    JOIN pg_class rel ON rel.oid = c.conrelid "
                    "    JOIN pg_class ref ON ref.oid = c.confrelid "
                    "    JOIN pg_attribute a ON a.attrelid = c.conrelid "
                    "      AND a.attnum = ANY(c.conkey) "
                    "    WHERE c.contype = 'f' "
                    "      AND a.attname = 'organization_id' "
                    "      AND ref.relname = 'organization' "
                    "      AND c.confdeltype = 'c')"
                ))
            }
        assert not_cascading == set(_NOT_TENANT_SCOPED), (
            f"organization_id columns outside the cascade: "
            f"{sorted(not_cascading)} — a new tenant table must gain "
            "ON DELETE CASCADE to organization; a newly-threaded CRM table "
            "must be removed from _NOT_TENANT_SCOPED and purged explicitly"
        )

        # And the exclusion means what the docstring says: each named table's
        # organization_id points at crm_organizations, not the tenant.
        with eng.begin() as conn:
            for table in sorted(_NOT_TENANT_SCOPED):
                target = conn.execute(text(
                    "SELECT ref.relname FROM pg_constraint c "
                    "JOIN pg_class rel ON rel.oid = c.conrelid "
                    "JOIN pg_class ref ON ref.oid = c.confrelid "
                    "JOIN pg_attribute a ON a.attrelid = c.conrelid "
                    "  AND a.attnum = ANY(c.conkey) "
                    "WHERE c.contype = 'f' AND a.attname = 'organization_id' "
                    f"  AND rel.relname = '{table}'"
                )).scalar_one_or_none()
                assert target == "crm_organizations", (
                    f"{table}.organization_id references {target!r} — the "
                    "exclusion's premise changed; re-derive it"
                )
