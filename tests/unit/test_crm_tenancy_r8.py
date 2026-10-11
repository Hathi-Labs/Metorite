"""R8 — the CRM is per organization, under phase-4 RLS (WS-53 CRM-T4).

The 14 hermetic CRM suites run on ``_crm_fakes.py``. A fake agrees with any SQL
it is handed, and it has no row level security, so none of them can say whether
org B sees org A's deals. This suite runs the REAL route code against a real
database as the NON-privileged role of the phase-4 rehearsal
(``acb_app_h3rls``: no superuser, no BYPASSRLS), with two organizations that
each hold one company, contact, lead, deal and activity in seeded stages.

Three cases, as the spec asks (``crm_platform.md`` §13.3, CRM-T4):

* (a) org B reads none of org A's rows on the 4 entity lists, and on
  activities, statuses and lost reasons, through the route functions.
* (b) ``WITH CHECK`` refuses an insert that carries another org's
  ``organization_id``.
* (c) a session with no tenant bound reads 0 rows.

Each read case also asserts that org A reads its own row. Without that control,
"0 rows" would also pass on an empty table.

**The recorded FK gap.** ``TestTheForeignKeyGap`` is an ``xfail(strict=True)``.
A foreign key check runs below row level security, so a member of org A can
point a contact at org B's company. Spec ``crm_platform.md`` §13.3 says "A
composite ``(organization_id, id)`` key closes it". That work is not in T4. The
strict xfail keeps the gap visible: when the key lands, the test XPASSes, the
run goes red, and the person who closed the gap removes the marker.
"""
# ruff: noqa: F811  (fixtures are imported by name, the repo idiom for R8 suites)
from __future__ import annotations

import contextlib
import uuid

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth.roles import UserContext, UserRole
from acb_common.db import TenantUnbound, bind_tenant, release_tenant
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

# The two-org phase-4 fixture and its DB gate. Used by name for injection.
from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)
from tests.unit.test_org_purge_tenant import _seed_crm

# ⚠️ Every test here needs the real database. Without this gate the suite
# ERRORS where no ladder URL is set (the deploy run), and the deploy stops.
pytestmark = _DB_GATE

_ENTITIES = ("ORGANIZATIONS", "CONTACTS", "LEADS", "DEALS")
_CRM_READ_TABLES = (
    "crm_companies", "crm_contacts", "crm_leads", "crm_deals",
    "crm_activities", "crm_lead_statuses", "crm_deal_statuses",
    "crm_lost_reasons",
)


@pytest.fixture(scope="module")
def crm(promoted):
    """Seed each org's CRM rows as the superuser, which escapes the policies."""
    with promoted.admin_engine.begin() as conn:
        for org in (promoted.org_a, promoted.org_b):
            _seed_crm(conn, org)
            conn.execute(text(
                "INSERT INTO crm_lost_reasons (label, position, organization_id) "
                "VALUES ('Price', 1, CAST(:o AS uuid))"), {"o": org})
    return promoted


@contextlib.contextmanager
def tenant(org: str):
    token = bind_tenant(org)
    try:
        yield
    finally:
        release_tenant(token)


@contextlib.asynccontextmanager
async def route_scope(db):
    """Point the shared engine at the NON-privileged role for one test."""
    dsn = db.app_url.render_as_string(hide_password=False)
    async with tenant_engine_scope(dsn):
        yield


def _list_params():
    from gateway.routes.crm.records import ListParams

    # Passed explicitly: called outside FastAPI, the ``Query(...)`` defaults
    # would arrive as Query objects.
    return ListParams(direction="desc", page=1, page_size=50)


def _user() -> UserContext:
    return UserContext(email="a@x.test", role=UserRole.EXECUTIVE)


def _entity(name: str):
    from gateway.routes.crm import core

    return getattr(core, name)


class TestReadsAreFilteredByOrganization:
    @pytest.mark.parametrize("name", _ENTITIES)
    async def test_org_b_reads_none_of_org_a_on_each_entity_list(self, crm, name):
        from gateway.routes.crm.records import _list

        entity = _entity(name)
        async with route_scope(crm):
            with tenant(crm.org_a):
                own = await _list(entity, _list_params())
            with tenant(crm.org_b):
                other = await _list(entity, _list_params())
            with tenant(str(uuid.uuid4())):
                stranger = await _list(entity, _list_params())
        assert own.total == 1, "control: org A must read its own row"
        # Org B has its OWN row. It must see exactly that one, never A's.
        assert other.total == 1 and len(other.rows) == 1
        assert other.rows[0]["id"] != own.rows[0]["id"]
        assert stranger.total == 0 and stranger.rows == []

    async def test_a_stranger_reads_none_on_statuses_lost_reasons_and_activities(
        self, crm,
    ):
        from gateway.routes.crm.activities import _activity_entries
        from gateway.routes.crm.admin import list_lost_reasons, list_statuses
        from gateway.routes.crm.core import DEALS, _tenant_session

        async with route_scope(crm):
            with tenant(crm.org_a):
                a_lanes = await list_statuses("deal", user=_user())
                a_leads = await list_statuses("lead", user=_user())
                a_lost = await list_lost_reasons(user=_user())
                async with _tenant_session() as db:
                    deal_id = str((await db.execute(
                        text("SELECT id FROM crm_deals"))).scalar_one())
                    a_acts = await _activity_entries(db, DEALS, deal_id, "own", 100)
            with tenant(str(uuid.uuid4())):
                s_lanes = await list_statuses("deal", user=_user())
                s_leads = await list_statuses("lead", user=_user())
                s_lost = await list_lost_reasons(user=_user())
                async with _tenant_session() as db:
                    # Org A's deal id, asked by a stranger: it reads nothing.
                    s_acts = await _activity_entries(db, DEALS, deal_id, "own", 100)
                    s_raw = (await db.execute(
                        text("SELECT count(*) FROM crm_activities"))).scalar_one()
            with tenant(crm.org_b):
                b_lanes = await list_statuses("deal", user=_user())
                b_lost = await list_lost_reasons(user=_user())
                async with _tenant_session() as db:
                    b_acts = await _activity_entries(db, DEALS, deal_id, "own", 100)
        assert len(a_lanes) == len(a_leads) == len(a_lost) == len(a_acts) == 1
        assert s_lanes == [] and s_leads == [] and s_lost == []
        assert s_acts == [] and s_raw == 0
        # Org B has its own stage and reason, never org A's.
        assert len(b_lanes) == 1 and b_lanes[0].id != a_lanes[0].id
        assert len(b_lost) == 1 and b_lost[0].id != a_lost[0].id
        assert b_acts == []

    async def test_org_b_gets_404_for_org_a_record_by_id(self, crm):
        from gateway.routes.crm.core import DEALS
        from gateway.routes.crm.records import _get, _list

        async with route_scope(crm):
            with tenant(crm.org_a):
                deal_id = (await _list(DEALS, _list_params())).rows[0]["id"]
            with tenant(crm.org_b), pytest.raises(HTTPException) as err:
                await _get(DEALS, deal_id)
        assert err.value.status_code == 404


class TestWithCheckRefusesACrossOrganizationInsert:
    @pytest.mark.parametrize(
        "table, columns, values",
        [
            ("crm_companies", "name", "'X'"),
            ("crm_contacts", "first_name", "'X'"),
            ("crm_lost_reasons", "label", "'X'"),
            ("crm_lead_statuses", "name, position, type", "'X', 9, 'open'"),
            ("crm_deal_statuses", "name, position, type", "'X', 9, 'open'"),
        ],
    )
    def test_an_insert_stamped_with_another_org_is_refused(
        self, crm, app_engine, table, columns, values,
    ):
        with app_engine.connect() as c:
            c.execute(text("SELECT set_config('app.tenant_id', :o, false)"),
                      {"o": crm.org_a})
            with pytest.raises(DBAPIError) as err:
                c.execute(text(
                    f"INSERT INTO {table} ({columns}, organization_id) "
                    f"VALUES ({values}, CAST(:b AS uuid))"), {"b": crm.org_b})
        assert "row-level security" in str(err.value)

    def test_an_insert_stamped_with_the_bound_org_is_accepted(self, crm, app_engine):
        """Control: the refusal above is the policy, not a broken statement."""
        with app_engine.begin() as c:
            c.execute(text("SELECT set_config('app.tenant_id', :o, true)"),
                      {"o": crm.org_a})
            c.execute(text(
                "INSERT INTO crm_companies (name, organization_id) "
                "VALUES ('control', CAST(:a AS uuid))"), {"a": crm.org_a})
            c.execute(text("DELETE FROM crm_companies WHERE name = 'control'"))

    @pytest.mark.parametrize(
        "table, status_table, cols, vals",
        [
            ("crm_leads", "crm_lead_statuses", "lead_name", "'X'"),
            ("crm_deals", "crm_deal_statuses", "name", "'X'"),
        ],
    )
    def test_the_lead_and_deal_inserts_are_refused_too(
        self, crm, app_engine, table, status_table, cols, vals,
    ):
        """The two tables whose ``status_id`` is RESTRICT: a stage of org A and
        a stamp of org B."""
        with app_engine.connect() as c:
            c.execute(text("SELECT set_config('app.tenant_id', :o, false)"),
                      {"o": crm.org_a})
            stage = c.execute(text(f"SELECT id FROM {status_table}")).scalar_one()
            with pytest.raises(DBAPIError) as err:
                c.execute(text(
                    f"INSERT INTO {table} ({cols}, status_id, organization_id) "
                    f"VALUES ({vals}, CAST(:s AS uuid), CAST(:b AS uuid))"),
                    {"s": str(stage), "b": crm.org_b})
        assert "row-level security" in str(err.value)


class TestAnUnboundSessionReadsNothing:
    @pytest.mark.parametrize("table", _CRM_READ_TABLES)
    def test_no_tenant_bound_reads_zero_rows(self, crm, app_engine, table):
        with app_engine.connect() as c:
            seen = c.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
            c.execute(text("SELECT set_config('app.tenant_id', :o, false)"),
                      {"o": crm.org_a})
            control = c.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
        assert control == 1, "control: a bound session reads its own row"
        assert seen == 0

    async def test_the_route_seam_refuses_an_unbound_request(self, crm):
        from gateway.routes.crm.records import _list

        async with route_scope(crm):
            with pytest.raises(TenantUnbound):
                await _list(_entity("DEALS"), _list_params())


class TestTheForeignKeyGap:
    @pytest.mark.xfail(
        strict=True,
        # Only "DID NOT RAISE" counts as the expected failure. A broken setup
        # (no company row, an import error) must ERROR, not hide as an xfail.
        raises=pytest.fail.Exception,
        reason=(
            "Recorded FK gap (crm_platform.md §13.3, CRM-T4): a foreign key "
            "check runs below row level security, so a contact in org A can "
            "point company_id at org B's company. A composite "
            "(organization_id, id) key closes it. Remove this marker when "
            "that key lands."
        ),
    )
    async def test_a_member_cannot_point_a_contact_at_another_orgs_company(self, crm):
        """POST /crm/contacts, as a member of org A, with org B's company id.

        The assertion is the CORRECT behaviour: the write is refused. Today the
        create succeeds, so ``pytest.raises`` fails and the test is an xfail.
        """
        from gateway.routes.crm.core import CONTACTS, ContactIn
        from gateway.routes.crm.records import create_record

        with crm.admin_engine.begin() as c:
            b_company = str(c.execute(text(
                "SELECT id FROM crm_companies WHERE organization_id = "
                "CAST(:b AS uuid)"), {"b": crm.org_b}).scalar_one())
        try:
            async with route_scope(crm):
                with tenant(crm.org_a), pytest.raises(Exception):  # noqa: B017
                    await create_record(
                        CONTACTS,
                        ContactIn(first_name="Leak", company_id=b_company),
                        _user(),
                    )
        finally:
            with crm.admin_engine.begin() as c:
                c.execute(text("DELETE FROM crm_contacts WHERE first_name = 'Leak'"))
