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
three ``crm_*`` tables had an ``organization_id`` that referenced the CRM
company record, NOT the tenant — discovered when this suite's first draft
seeded them as tenant rows and the FK refused. WS-53 CRM-T1 renamed that
column to ``company_id``, and all 13 ``crm_*`` tables now cascade from the
tenant (migration 241).

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


_CRM_TABLES = (
    "crm_companies", "crm_contacts", "crm_lead_statuses", "crm_deal_statuses",
    "crm_leads", "crm_deals", "crm_activities",
)


def _seed_crm(conn, org: str) -> None:
    """One company, contact, lead, deal and activity, each lead and deal in a
    seeded stage of its own org (WS-53 CRM-T4).

    The stages are what make the purge hazard reachable: ``crm_leads.status_id``
    and ``crm_deals.status_id`` are ``ON DELETE RESTRICT``.
    """
    p = {"o": org}
    ids = {}
    for key, sql in (
        ("company", "INSERT INTO crm_companies (name, organization_id) "
                    "VALUES ('Acme', CAST(:o AS uuid)) RETURNING id"),
        ("lstage", "INSERT INTO crm_lead_statuses (name, position, type, "
                   "organization_id) VALUES ('New', 1, 'open', "
                   "CAST(:o AS uuid)) RETURNING id"),
        ("dstage", "INSERT INTO crm_deal_statuses (name, position, type, "
                   "organization_id) VALUES ('Proposal', 1, 'open', "
                   "CAST(:o AS uuid)) RETURNING id"),
    ):
        ids[key] = str(conn.execute(text(sql), p).scalar_one())
    ids["contact"] = str(conn.execute(text(
        "INSERT INTO crm_contacts (first_name, company_id, organization_id) "
        "VALUES ('Ada', CAST(:c AS uuid), CAST(:o AS uuid)) RETURNING id"),
        {**p, "c": ids["company"]}).scalar_one())
    conn.execute(text(
        "INSERT INTO crm_leads (lead_name, status_id, organization_id) "
        "VALUES ('Lead', CAST(:s AS uuid), CAST(:o AS uuid))"),
        {**p, "s": ids["lstage"]})
    ids["deal"] = str(conn.execute(text(
        "INSERT INTO crm_deals (name, company_id, status_id, organization_id) "
        "VALUES ('Deal', CAST(:c AS uuid), CAST(:s AS uuid), CAST(:o AS uuid)) "
        "RETURNING id"), {**p, "c": ids["company"], "s": ids["dstage"]}
    ).scalar_one())
    conn.execute(text(
        "INSERT INTO crm_activities (type, deal_id, created_by, "
        "organization_id) VALUES ('note', CAST(:d AS uuid), 'a@x.test', "
        "CAST(:o AS uuid))"), {**p, "d": ids["deal"]})


def _crm_counts(conn, org: str) -> dict[str, int]:
    return {
        t: conn.execute(
            text(f"SELECT count(*) FROM {t} WHERE organization_id = "
                 "CAST(:o AS uuid)"), {"o": org},
        ).scalar_one()
        for t in _CRM_TABLES
    }


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


class TestTheCrmPurge:
    async def test_an_org_holding_leads_and_deals_in_stages_purges_whole(self, eng):
        """WS-53 CRM-T4. ``crm_leads.status_id`` and ``crm_deals.status_id`` are
        ``ON DELETE RESTRICT``, so the one cascading DELETE can reach a stage
        before the lead or deal that sits in it.

        **Measured 2026-10-11 on Postgres 16: the hazard did NOT reproduce
        with the bare cascade.** Without the explicit deletes in
        ``purge_tenant_organization`` the org still purged whole, because the
        referential triggers fire after the statement and by then the
        cascade has removed the leads and deals too. The test stays, and
        the explicit deletes stay, so the purge does not depend on that
        trigger order.
        """
        from acb_auth.offboard import purge_tenant_organization

        victim = f"purge-{uuid.uuid4().hex[:8]}"
        bystander = f"stays-{uuid.uuid4().hex[:8]}"
        with eng.begin() as conn:
            v_org = _seed_org(conn, slug=victim)
            b_org = _seed_org(conn, slug=bystander)
            _seed_crm(conn, v_org)
            _seed_crm(conn, b_org)
            assert set(_crm_counts(conn, v_org).values()) == {1}
        try:
            async with tenant_engine_scope(_URL):
                receipt = await purge_tenant_organization(slug=victim)
            assert receipt["already_absent"] is False
            assert receipt["deleted"] == {
                "organization": 1, "crm_activities": 1, "crm_deals": 1,
                "crm_leads": 1,
            }, receipt["deleted"]
            with eng.begin() as conn:
                gone = _crm_counts(conn, v_org)
                kept = _crm_counts(conn, b_org)
            assert gone == {t: 0 for t in _CRM_TABLES}, gone
            assert kept == {t: 1 for t in _CRM_TABLES}, kept
        finally:
            with eng.begin() as conn:
                _cleanup(conn, slug=victim, org=v_org)
                _cleanup(conn, slug=bystander, org=b_org)


class TestTheExclusionCannotGoStale:
    def test_every_organization_id_cascades_to_the_tenant(self, eng):
        """Every column NAMED ``organization_id`` must cascade to the tenant
        ``organization`` table, so the purge's single DELETE reaches it, or be
        named in ``_NOT_TENANT_SCOPED``.

        That set is EMPTY since WS-53 CRM-T1 (2026-10-11). It held the three
        ``crm_*`` tables whose ``organization_id`` was the CRM company record.
        144_crm.sql renamed that column to ``company_id``, and migration 241
        gave all 13 ``crm_*`` tables a cascading tenant key.

        The red arm is deliberate: a NEW tenant table without the cascade FK
        would orphan rows the purge claims to destroy."""
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
        assert not _NOT_TENANT_SCOPED
        assert not_cascading == set(_NOT_TENANT_SCOPED), (
            f"organization_id columns outside the cascade: "
            f"{sorted(not_cascading)} — a new tenant table must gain "
            "ON DELETE CASCADE to organization"
        )

    def test_every_crm_table_cascades_from_the_tenant(self, eng):
        """WS-53 CRM-T1: the positive half, so the empty set above is not
        vacuous for the CRM. Each of the 13 ``crm_*`` tables carries an
        ``organization_id`` that cascades from ``organization``. WS-53 CRM-T5
        adds the 14th, ``crm_settings`` (migration 243), whose primary key is
        that column."""
        with eng.begin() as conn:
            cascading = {
                r[0]
                for r in conn.execute(text(
                    "SELECT rel.relname FROM pg_constraint c "
                    "JOIN pg_class rel ON rel.oid = c.conrelid "
                    "JOIN pg_class ref ON ref.oid = c.confrelid "
                    "JOIN pg_attribute a ON a.attrelid = c.conrelid "
                    "  AND a.attnum = ANY(c.conkey) "
                    "WHERE c.contype = 'f' AND a.attname = 'organization_id' "
                    "  AND ref.relname = 'organization' "
                    "  AND c.confdeltype = 'c' "
                    "  AND starts_with(rel.relname, 'crm_')"
                ))
            }
        assert cascading == {
            "crm_activities", "crm_auto_lead_cursors", "crm_companies",
            "crm_contacts", "crm_deal_contacts", "crm_deal_statuses",
            "crm_deals", "crm_field_defs", "crm_lead_statuses", "crm_leads",
            "crm_lost_reasons", "crm_settings", "crm_status_changes",
            "crm_sync_cursors", "crm_sync_runs", "crm_zoho_tombstones",
        }
