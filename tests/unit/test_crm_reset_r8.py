"""R8 — setup, connection and start over (WS-53 CRM-X0, ``crm_platform.md`` §19.11).

What this proves against a REAL Postgres, through the real ``/crm`` router, as
the NON-privileged role of the phase-4 rehearsal:

- ``POST /crm/reset`` removes every listed row of org A in one transaction,
  leaves org B as it was, and keeps ``crm_settings``, ``crm_views`` and the run
  history (done when 1);
- a member who is not an admin gets 403, and a wrong ``confirm`` gets 422
  (done when 2);
- a reconnect to the SAME Zoho org keeps the 547 deals and starts the sync
  again, and a reconnect to a DIFFERENT Zoho org stores ``needs_reset`` and
  writes no record (done when 3);
- ``GET /crm/export/all.zip`` holds one CSV for each entity with every row,
  and never a row of org B (done when 4);
- the reset waits for a sync page that holds the mode, so a page in flight
  cannot leave a record behind;
- migration 252 is found by content and replays with no change.

Hermetic fakes agree with any SQL they are handed (``FOR UPDATE``, ``FOR
SHARE``, the RLS policy, a CHECK constraint), so none of this is proved
without the database.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_crm_access_r8 import (
    _address,
    _admin,
    _app_dsn,
    _assert_non_priv,
    _call,
    _member,
)
from tests.unit.test_crm_connection import FakeZoho
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

# ⚠️ Every test here needs the real database. Without this gate the suite
# ERRORS where no ladder URL is set (the deploy run), and the deploy stops.
pytestmark = _DB_GATE

_MIGRATIONS = Path(__file__).resolve().parents[2] / "infra" / "postgres"

DEALS = 547

#: Every table the start over empties. The R8 suite holds its own copy, so a
#: table that leaves the door's list fails here.
EMPTIED = (
    "crm_activities", "crm_deal_contacts", "crm_deals", "crm_leads",
    "crm_contacts", "crm_companies", "crm_module_records",
    "crm_status_changes", "crm_field_defs", "crm_pipeline_stages",
    "crm_pipelines", "crm_deal_statuses", "crm_lead_statuses",
    "crm_lost_reasons", "crm_sync_cursors", "crm_source_profiles",
    "crm_zoho_tombstones",
    # CRM-Q7a review P2: the rules name the fields of the old Zoho account.
    "crm_field_rules",
    # CRM-C4a: the studio sections go with the fields they group.
    "crm_field_sections",
    # CRM-C6a: the conversion mapping names the fields of the old setup.
    "crm_convert_mappings",
    # CRM-Q26: the labels of the old account's notes.
    "crm_note_labels",
    # CRM-Q27: the vectors of the old account's notes.
    "crm_note_vectors",
    # CRM-Q17a-1: the fill rank counts the records of the old setup.
    "crm_field_fill",
    # CRM-Q18-1: the versions of the plan name the old field rules.
    "crm_mapping_plans",
)
#: What the start over keeps. The same set as ``core.RESET_KEEPS``.
KEPT = ("crm_settings", "crm_views", "crm_sync_runs", "crm_auto_lead_cursors")
ALL = EMPTIED + KEPT


#: The deal ids of each seeded account start with these digits. Zoho record
#: ids are digits, and the callback reads one by id (CRM-X0 review P1-2).
DEAL_BASE = {"A": 910_000_000, "B": 920_000_000}


class OrgZoho(FakeZoho):
    """Zoho for one consent: a token, a ``GET org`` that names an org id, and
    ``GET Deals/{id}`` that finds the deals whose id starts with *holds*.

    ``record_status`` forces the status of the record read (404, 204, 500).
    """

    def __init__(self, tag: str, org_id: str, *, holds: str = "",
                 record_status: int | None = None) -> None:
        super().__init__(token_body={
            "access_token": f"ACCESS-{tag}", "refresh_token": f"REFRESH-{tag}",
            "expires_in": 3600, "api_domain": "https://www.zohoapis.in",
        })
        self.org_id = org_id
        self.holds = holds
        self.record_status = record_status

    def _handle(self, request: Any) -> Any:
        import httpx

        if "/crm/v2/Deals/" in request.url.path:
            self.requests.append(request)
            rid = request.url.path.rsplit("/", 1)[-1]
            if self.record_status is not None:
                return httpx.Response(self.record_status, json={"code": "X"})
            if self.holds and rid.startswith(self.holds):
                return httpx.Response(200, json={"data": [{"id": rid, "Deal_Name": "d"}]})
            return httpx.Response(204)
        if request.url.path.endswith("/org"):
            self.requests.append(request)
            return httpx.Response(200, json={"org": [{
                "id": self.org_id, "domain_name": f"dom-{self.org_id}",
                "license_details": {"paid": False},
            }]})
        return super()._handle(request)


@pytest.fixture
def x0(promoted, monkeypatch):  # noqa: F811 — a fixture
    """The switches on, a key for the blob, one fake Zoho, cleaned after."""
    import acb_llm.key_store as key_store
    from acb_common import get_settings

    from tests.unit._crm_fakes import crm_switch_on

    crm_switch_on(monkeypatch, "*")
    settings = get_settings()
    for name, value in {
        "crm_mirror": True,
        "crm_mirror_orgs": "*",
        "crm_zoho_client_id": "1000.X0CLIENT",
        "crm_zoho_client_secret": "x0-client-secret",
        "gateway_session_secret": "x0-session-secret-not-a-default-0123456789",
        "acb_master_key": "x0-master-key-for-the-blob",
    }.items():
        monkeypatch.setattr(settings, name, value, raising=False)
    monkeypatch.setenv("WORKBENCH_PUBLIC_URL", "https://app.metorite.com")
    monkeypatch.setattr(key_store, "_store", None)

    from gateway.crm_sources.zoho import ZohoSource
    from gateway.crm_sync import scheduler
    from gateway.routes.crm import connection as conn

    active: dict[str, Any] = {"zoho": OrgZoho("A", "Z-A")}
    promoted.active = active
    monkeypatch.setattr(
        conn, "_source",
        lambda credential=None, transport=None: ZohoSource(
            conn.zoho_oauth_config(), credential,
            transport=active["zoho"].transport,
        ),
    )
    started: list[str] = []

    async def _record_start(org: str) -> bool:
        started.append(org)
        return True

    monkeypatch.setattr(scheduler, "start_first_import", _record_start)
    promoted.started = started
    audits: list[Any] = []
    import acb_audit

    monkeypatch.setattr(acb_audit, "record", audits.append)
    promoted.audits = audits
    yield promoted
    with promoted.admin_engine.begin() as c:
        for org in (promoted.org_a, promoted.org_b):
            for table in (*ALL, "integration_connections", "email_accounts"):
                c.execute(text(
                    f"DELETE FROM {table} WHERE organization_id = CAST(:o AS uuid)"),
                    {"o": org})


def _q(p: Any, sql: str, **params: Any) -> list[Any]:
    with p.admin_engine.connect() as c:
        return c.execute(text(sql), params).all()


def _counts(p: Any, org: str) -> dict[str, int]:
    with p.admin_engine.connect() as c:
        return {
            t: int(c.execute(text(
                f"SELECT count(*) FROM {t} WHERE organization_id = CAST(:o AS uuid)"),
                {"o": org}).scalar())
            for t in ALL
        }


def _seed(p: Any, org: str, *, tag: str, deals: int, status: str = "disconnected",
          zoho_org: str = "Z-A", profile: bool = True) -> str:
    """A Mirror org with records in every listed table. Returns the connection id."""
    o = {"o": org}
    with p.admin_engine.begin() as c:
        conn = str(c.execute(text(
            "INSERT INTO integration_connections (organization_id, provider, "
            "owning_app, status) VALUES (CAST(:o AS uuid), 'zoho', 'crm', :s) "
            "RETURNING id"), {**o, "s": status}).scalar())
        c.execute(text(
            "INSERT INTO crm_settings (organization_id, mode, connection_id, access) "
            "VALUES (CAST(:o AS uuid), 'mirror', CAST(:c AS uuid), 'everyone')"),
            {**o, "c": conn})
        ls = c.execute(text(
            "INSERT INTO crm_lead_statuses (organization_id, name, position, type) "
            "VALUES (CAST(:o AS uuid), 'New', 0, 'open') RETURNING id"), o).scalar()
        ds = c.execute(text(
            "INSERT INTO crm_deal_statuses (organization_id, name, position, type) "
            "VALUES (CAST(:o AS uuid), 'Qualify', 0, 'open') RETURNING id"), o).scalar()
        c.execute(text(
            "INSERT INTO crm_lost_reasons (organization_id, label) "
            "VALUES (CAST(:o AS uuid), 'Price')"), o)
        pipe = c.execute(text(
            "INSERT INTO crm_pipelines (organization_id, name, ext_source, ext_id) "
            "VALUES (CAST(:o AS uuid), 'Sales', 'zoho', :e) RETURNING id"),
            {**o, "e": f"{tag}-pipe"}).scalar()
        c.execute(text(
            "INSERT INTO crm_pipeline_stages (organization_id, pipeline_id, status_id) "
            "VALUES (CAST(:o AS uuid), :p, :s)"), {**o, "p": pipe, "s": ds})
        company = c.execute(text(
            "INSERT INTO crm_companies (organization_id, name, ext_source, ext_id) "
            "VALUES (CAST(:o AS uuid), :n, 'zoho', :e) RETURNING id"),
            {**o, "n": f"{tag} Co", "e": f"{tag}-co"}).scalar()
        contact = c.execute(text(
            "INSERT INTO crm_contacts (organization_id, first_name, company_id, "
            "ext_source, ext_id) VALUES (CAST(:o AS uuid), :n, :co, 'zoho', :e) "
            "RETURNING id"), {**o, "n": f"{tag} Ann", "co": company,
                              "e": f"{tag}-contact"}).scalar()
        c.execute(text(
            "INSERT INTO crm_leads (organization_id, lead_name, status_id, "
            "ext_source, ext_id) VALUES (CAST(:o AS uuid), :n, :s, 'zoho', :e)"),
            {**o, "n": f"{tag} Lead", "s": ls, "e": f"{tag}-lead"})
        c.execute(text(
            "INSERT INTO crm_deals (organization_id, name, status_id, pipeline_id, "
            "company_id, ext_source, ext_id, amount) "
            "SELECT CAST(:o AS uuid), :tag || ' deal ' || g, :s, :p, :co, 'zoho', "
            "(:base + g)::text, g * 100 FROM generate_series(1, :n) g"),
            {**o, "tag": tag, "s": ds, "p": pipe, "co": company, "n": deals,
             "base": DEAL_BASE[tag]})
        first = c.execute(text(
            "SELECT id FROM crm_deals WHERE organization_id = CAST(:o AS uuid) "
            "ORDER BY ext_id LIMIT 1"), o).scalar()
        c.execute(text(
            "INSERT INTO crm_deal_contacts (organization_id, deal_id, contact_id) "
            "VALUES (CAST(:o AS uuid), :d, :c)"), {**o, "d": first, "c": contact})
        note = c.execute(text(
            "INSERT INTO crm_activities (organization_id, type, subject, deal_id, "
            "created_by, ext_source, ext_id) VALUES (CAST(:o AS uuid), 'note', "
            ":s, :d, 'sync', 'zoho', :e) RETURNING id"),
            {**o, "s": f"{tag} note", "d": first, "e": f"{tag}-note"}).scalar()
        # CRM-Q26: the label of that note. The start over takes it.
        c.execute(text(
            "INSERT INTO crm_note_labels (organization_id, activity_id, kind, "
            "mode, body_md5) VALUES (CAST(:o AS uuid), :a, 'payment', 'shadow', "
            "'h')"), {**o, "a": note})
        # CRM-Q27: the vector of that note. The start over takes it.
        c.execute(text(
            "INSERT INTO crm_note_vectors (organization_id, activity_id, "
            "body_md5, embedding, embedded_at) VALUES (CAST(:o AS uuid), :a, 'h', "
            "CAST(:v AS vector), now())"),
            {**o, "a": note, "v": "[" + ",".join(["0.5"] * 1536) + "]"})
        c.execute(text(
            "INSERT INTO crm_module_records (organization_id, module, ext_source, "
            "ext_id, name, fields) VALUES (CAST(:o AS uuid), 'Products', 'zoho', "
            ":e, :n, CAST(:f AS jsonb))"),
            {**o, "e": f"{tag}-prod", "n": f"{tag} Widget",
             "f": json.dumps({"Unit_Price": 10})})
        c.execute(text(
            "INSERT INTO crm_status_changes (organization_id, entity_type, "
            "entity_id, to_status, changed_by) VALUES (CAST(:o AS uuid), 'deal', "
            ":d, 'Qualify', 'sync')"), {**o, "d": first})
        c.execute(text(
            "INSERT INTO crm_field_defs (organization_id, entity, key, label, "
            "source) VALUES (CAST(:o AS uuid), 'deal', 'zk', 'ZK', 'zoho'), "
            "(CAST(:o AS uuid), 'deal', 'mk', 'MK', 'manual')"), o)
        # CRM-C4a: a studio section, and a studio field in it. The start
        # over takes both.
        section = c.execute(text(
            "INSERT INTO crm_field_sections (organization_id, entity, label) "
            "VALUES (CAST(:o AS uuid), 'deal', 'Main') RETURNING id"), o).scalar()
        c.execute(text(
            "INSERT INTO crm_field_defs (organization_id, entity, key, label, "
            "source, section_id) VALUES (CAST(:o AS uuid), 'deal', 'studio', "
            "'Studio', 'metorite', CAST(:s AS uuid))"), {**o, "s": str(section)})
        # CRM-C6a: a conversion mapping row. The start over takes it.
        c.execute(text(
            "INSERT INTO crm_convert_mappings (organization_id, source_key, "
            "target_entity, skip) VALUES (CAST(:o AS uuid), 'studio', 'deal', "
            "true)"), o)
        # CRM-Q17a-1: a fill row. The start over takes it.
        c.execute(text(
            "INSERT INTO crm_field_fill (organization_id, entity, key, filled, "
            "total) VALUES (CAST(:o AS uuid), 'deal', 'name', 1, 1)"), o)
        c.execute(text(
            "INSERT INTO crm_sync_cursors (organization_id, module, "
            "initial_done_at) VALUES (CAST(:o AS uuid), 'zoho:deal', now())"), o)
        c.execute(text(
            "INSERT INTO crm_source_profiles (organization_id, connection_id, "
            "version, profile, profile_hash) VALUES (CAST(:o AS uuid), "
            "CAST(:c AS uuid), 1, CAST(:p AS jsonb), 'h')"),
            {**o, "c": conn, "p": json.dumps({"org": {
                "id": zoho_org, "domain_name": f"dom-{zoho_org}"}})})
        if not profile:
            # The state of prod on 2026-10-12: an earlier disconnect dropped it.
            c.execute(text("DELETE FROM crm_source_profiles WHERE "
                           "organization_id = CAST(:o AS uuid)"), o)
        c.execute(text(
            "INSERT INTO crm_zoho_tombstones (organization_id, module, zoho_id, "
            "entity_type, deleted_by) VALUES (CAST(:o AS uuid), 'Deals', :z, "
            "'deal', 'a@x.test')"), {**o, "z": f"{tag}-gone"})
        c.execute(text(
            "INSERT INTO crm_sync_runs (organization_id, connection_id, kind, "
            "status, started_at, finished_at) VALUES (CAST(:o AS uuid), "
            "CAST(:c AS uuid), 'import', 'ok', now() - interval '2 days', "
            "now() - interval '2 days')"), {**o, "c": conn})
        c.execute(text(
            "INSERT INTO crm_views (organization_id, entity, name, owner_email) "
            "VALUES (CAST(:o AS uuid), 'deals', 'Mine', 'admin@crm-t5-r8.test')"), o)
        c.execute(text(
            "INSERT INTO crm_field_rules (organization_id, ext_source, entity, "
            "field_api_name, field_type, profile_version, target_column, status, "
            "proposed_by) VALUES (CAST(:o AS uuid), 'zoho', 'lead', 'Sector', "
            "'select', 1, 'industry', 'approved', 'admin')"), o)
        # CRM-Q18-1: a version of the plan. The start over takes it.
        c.execute(text(
            "INSERT INTO crm_mapping_plans (organization_id, version, state, "
            "rules, created_by) VALUES (CAST(:o AS uuid), 1, 'approved', "
            "'[]'::jsonb, 'admin@crm-t5-r8.test')"), o)
        box = c.execute(text(
            "INSERT INTO email_accounts (user_id, provider, email_address, "
            "credentials_encrypted, sync_enabled, organization_id) VALUES "
            "(:u, 'microsoft', :u, 'x', true, CAST(:o AS uuid)) RETURNING id"),
            {**o, "u": f"box-{tag.lower()}@crm-x0.test"}).scalar()
        c.execute(text(
            "INSERT INTO crm_auto_lead_cursors (account_id, activated_at, "
            "processed_watermark, last_run_at, organization_id) VALUES "
            "(:a, now(), now(), now(), CAST(:o AS uuid))"), {**o, "a": box})
    return conn


def _seed_both(p: Any) -> None:
    _seed(p, p.org_a, tag="A", deals=DEALS)
    _seed(p, p.org_b, tag="B", deals=3, zoho_org="Z-B")


async def _reset(p: Any, body: dict[str, Any], user: Any = None) -> Any:
    return await _call(p, user or _admin(p.org_a), "POST", "/crm/reset", body)


def _settings(p: Any, org: str) -> Any:
    rows = _q(p, "SELECT mode, access, connection_id FROM crm_settings "
                 "WHERE organization_id = CAST(:o AS uuid)", o=org)
    return rows[0] if rows else None


def _connection(p: Any, org: str) -> Any:
    rows = _q(p, "SELECT status, credentials_encrypted, last_error FROM "
                 "integration_connections WHERE organization_id = CAST(:o AS uuid)",
              o=org)
    return rows[0] if rows else None


# ── Every CRM table is emptied or kept on purpose (review P2) ────────────────


def test_every_crm_table_is_emptied_or_kept_by_the_start_over(x0) -> None:
    """A new ``crm_*`` table must join ``RESET_TABLES`` or ``RESET_KEEPS``.

    Without this, a table that names the old Zoho account survives a start
    over, and its rows apply to the next account. ``crm_field_rules`` was
    that table (CRM-Q7a review P2).
    """
    from gateway.routes.crm.core import RESET_KEEPS, RESET_TABLES

    tables = {r[0] for r in _q(x0, (
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' "
        "AND table_name LIKE 'crm\\_%'"))}
    assert "crm_field_rules" in tables and "crm_leads" in tables
    assert tables - set(RESET_TABLES) - set(RESET_KEEPS) == set(), (
        "a crm_ table that the start over neither empties nor keeps")
    assert not set(RESET_TABLES) & set(RESET_KEEPS)
    # The copies of this suite follow the door's lists.
    assert set(EMPTIED) == set(RESET_TABLES)
    assert set(KEPT) == set(RESET_KEEPS)


def test_both_start_overs_delete_the_plans_before_the_rules() -> None:
    """CRM-Q18-1b item 5. A plan approve locks the plan rows first and the
    rule rows second. Each start over deletes in that order too, or it waits
    on an approve in a cycle (40P01). ``test_crm_map_plan_r8.py`` makes that
    deadlock on a real database."""
    from gateway.routes.crm.core import RESET_TABLES
    from gateway.routes.crm.settings import _RESET_DELETES

    for name, tables in (("RESET_TABLES", RESET_TABLES), ("_RESET_DELETES", _RESET_DELETES)):
        assert tables.index("crm_mapping_plans") < tables.index("crm_field_rules"), (
            f"{name} deletes the rules before the plans")


# ── Done when 1: the reset ───────────────────────────────────────────────────


async def test_the_reset_removes_every_listed_row_and_leaves_org_b(x0, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    _seed_both(x0)
    before_b = _counts(x0, x0.org_b)
    before_a = _counts(x0, x0.org_a)
    assert all(before_a[t] >= 1 for t in ALL), before_a

    r = await _reset(x0, {"confirm": "DELETE", "then": "none"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["removed"] == {
        "leads": 1, "contacts": 1, "organizations": 1, "deals": DEALS,
        "activities": 1, "module_records": 1,
    }
    assert body["mode"] is None and body["consent_url"] is None

    after_a = _counts(x0, x0.org_a)
    assert {t: after_a[t] for t in EMPTIED} == dict.fromkeys(EMPTIED, 0)
    assert {t: after_a[t] for t in KEPT} == {t: before_a[t] for t in KEPT}
    assert _counts(x0, x0.org_b) == before_b
    mode, access, pointer = _settings(x0, x0.org_a)
    assert (mode, access, pointer) == (None, "everyone", None)
    status, blob, _err = _connection(x0, x0.org_a)
    assert (status, blob) == ("disconnected", None)
    # One audit line: who, when (the event's own time), what was removed.
    (event,) = [e for e in x0.audits if e.action == "crm.reset"]
    assert event.actor == "user:admin@crm-t5-r8.test"
    assert event.organization_id == x0.org_a
    assert event.payload["removed"]["deals"] == DEALS
    assert event.payload["then"] == "none"


async def test_then_native_seeds_the_native_stages(x0, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    _seed(x0, x0.org_a, tag="A", deals=4)
    r = await _reset(x0, {"confirm": "DELETE", "then": "native"})
    assert r.status_code == 200, r.text
    assert r.json()["mode"] == "native"
    assert _settings(x0, x0.org_a)[0] == "native"
    after = _counts(x0, x0.org_a)
    assert after["crm_deals"] == 0
    # The 17 seed rows of the first-open choice (seed.py).
    assert (after["crm_deal_statuses"], after["crm_lead_statuses"],
            after["crm_lost_reasons"]) == (6, 5, 6)


async def test_then_mirror_leaves_no_mode_and_returns_the_consent(x0, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    _seed(x0, x0.org_a, tag="A", deals=4)
    r = await _reset(x0, {"confirm": "DELETE", "then": "mirror"})
    assert r.status_code == 200, r.text
    url = r.json()["consent_url"]
    assert url and urlsplit(url).netloc.startswith("accounts.zoho")
    assert parse_qs(urlsplit(url).query)["state"][0]
    assert _settings(x0, x0.org_a)[0] is None
    assert _counts(x0, x0.org_a)["crm_deals"] == 0


async def test_a_reset_of_an_org_with_nothing_is_a_no_op_200(x0, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    r = await _reset(x0, {"confirm": "DELETE", "then": "none"})
    assert r.status_code == 200, r.text
    assert sum(r.json()["removed"].values()) == 0


# ── Done when 2: who may, and the typed confirm ──────────────────────────────


async def test_a_member_who_is_not_admin_gets_403(x0, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    _seed(x0, x0.org_a, tag="A", deals=4)
    before = _counts(x0, x0.org_a)
    r = await _reset(x0, {"confirm": "DELETE", "then": "none"},
                     _member(_address("m"), x0.org_a))
    assert r.status_code == 403, r.text
    assert _counts(x0, x0.org_a) == before


@pytest.mark.parametrize("body", [
    {"confirm": "delete", "then": "none"},
    {"confirm": "DELETE ", "then": "none"},
    {"then": "none"},
    {"confirm": "DELETE", "then": "zoho"},
    {"confirm": "DELETE", "then": "none", "organization_id": "x"},
])
async def test_a_wrong_body_is_422_and_removes_nothing(x0, app_engine, body) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    _seed(x0, x0.org_a, tag="A", deals=4)
    before = _counts(x0, x0.org_a)
    r = await _reset(x0, body)
    assert r.status_code == 422, r.text
    assert _counts(x0, x0.org_a) == before
    assert _connection(x0, x0.org_a)[0] == "disconnected"
    assert _settings(x0, x0.org_a)[0] == "mirror"


async def test_the_counts_read_is_admin_only_and_counts_this_org(x0, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    _seed_both(x0)
    r = await _call(x0, _admin(x0.org_a), "GET", "/crm/settings/counts")
    assert r.status_code == 200, r.text
    assert r.json()["counts"]["deals"] == DEALS
    assert r.json()["total"] == DEALS + 5
    denied = await _call(x0, _member(_address("m"), x0.org_a), "GET",
                         "/crm/settings/counts")
    assert denied.status_code == 403


# ── Done when 3: reconnect ───────────────────────────────────────────────────


async def _connect(p: Any, zoho: OrgZoho) -> Any:
    p.active["zoho"] = zoho
    admin = _admin(p.org_a)
    started = await _call(p, admin, "POST", "/crm/connection/zoho/start")
    assert started.status_code == 200, started.text
    state = parse_qs(urlsplit(started.json()["url"]).query)["state"][0]
    return await _callback(p, admin, {
        "code": "CODE", "state": state, "location": "in",
        "accounts-server": "https://accounts.zoho.in",
    })


async def _callback(p: Any, user: Any, params: dict[str, str]) -> Any:
    import httpx
    from acb_auth import get_current_user
    from acb_common.db import bind_tenant, clear_tenant, release_tenant
    from fastapi import FastAPI
    from gateway.routes import crm as crm_package

    async def _caller() -> Any:
        bind_tenant(user.organization_id)
        return user

    app = FastAPI()
    app.include_router(crm_package.router)
    app.dependency_overrides[get_current_user] = _caller
    scope = clear_tenant()
    try:
        async with tenant_engine_scope(_app_dsn(p)), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://crm.test",
        ) as client:
            return await client.get("/crm/oauth/zoho/callback", params=params)
    finally:
        release_tenant(scope)


async def _is_due(p: Any, org: str) -> bool:
    from gateway.crm_sync import scheduler

    async with tenant_engine_scope(_app_dsn(p)):
        return await scheduler.is_due(org)


async def test_a_disconnect_keeps_the_profile_that_the_reconnect_compares(x0, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    _seed(x0, x0.org_a, tag="A", deals=4, status="connected")
    r = await _call(x0, _admin(x0.org_a), "POST", "/crm/connection/disconnect")
    assert r.status_code == 200, r.text
    assert _counts(x0, x0.org_a)["crm_source_profiles"] == 1


async def test_a_reconnect_to_the_same_zoho_org_keeps_the_records_and_resumes(x0, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    _seed(x0, x0.org_a, tag="A", deals=DEALS)
    assert not await _is_due(x0, x0.org_a)
    r = await _connect(x0, OrgZoho("A2", "Z-A"))
    assert r.status_code == 302 and r.headers["location"] == "/crm?connected=zoho", r.text
    assert _connection(x0, x0.org_a)[0] == "connected"
    assert _settings(x0, x0.org_a)[0] == "mirror"
    assert _counts(x0, x0.org_a)["crm_deals"] == DEALS
    assert x0.started == [x0.org_a]
    assert await _is_due(x0, x0.org_a)


async def test_a_reconnect_to_another_zoho_org_needs_a_reset_and_writes_nothing(x0, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    _seed(x0, x0.org_a, tag="A", deals=DEALS)
    before = _counts(x0, x0.org_a)
    other = OrgZoho("B", "Z-OTHER")
    r = await _connect(x0, other)
    assert r.status_code == 302, r.text
    assert r.headers["location"] == "/crm?connect_error=different_org"
    status, blob, err = _connection(x0, x0.org_a)
    assert (status, err) == ("needs_reset", "different_org")
    # Nothing of the other account is kept, and no record moved.
    assert blob is None
    assert _counts(x0, x0.org_a) == before
    assert _settings(x0, x0.org_a)[0] == "mirror"
    assert x0.started == []
    assert not await _is_due(x0, x0.org_a)
    revokes = [q for q in other.requests if q.url.path.endswith("/token/revoke")]
    assert len(revokes) == 1 and b"REFRESH-B" in revokes[0].content


async def test_needs_reset_gives_the_sync_no_credential(x0, app_engine) -> None:  # noqa: F811
    """A connected org that consents to another Zoho org stops: no token, no run."""
    _assert_non_priv(app_engine)
    _seed(x0, x0.org_a, tag="A", deals=4)
    first = await _connect(x0, OrgZoho("A1", "Z-A"))
    assert first.status_code == 302, first.text
    assert _connection(x0, x0.org_a)[0] == "connected"
    # No discovery runs between the two consents. The first consent is the
    # same account, so it keeps the profile, stale (CRM-Q10). The account id
    # it stored is what catches the second.
    assert _counts(x0, x0.org_a)["crm_source_profiles"] == 1
    second = await _connect(x0, OrgZoho("B", "Z-OTHER"))
    assert second.status_code == 302, second.text
    assert _connection(x0, x0.org_a)[0] == "needs_reset"

    from gateway.crm_sync import engine
    from gateway.routes.crm import connection as conn

    async with tenant_engine_scope(_app_dsn(x0)):
        assert await conn.fresh_zoho_credential(x0.org_a) is None
        with pytest.raises(engine.NotConnected):
            await engine.acquire(x0.org_a)
    # The reset clears it, through the one disconnect logic.
    r = await _reset(x0, {"confirm": "DELETE", "then": "none"})
    assert r.status_code == 200, r.text
    assert _connection(x0, x0.org_a)[0] == "disconnected"


def _account_id(p: Any, org: str) -> Any:
    rows = _q(p, "SELECT provider_meta ->> 'account_id' FROM integration_connections "
                 "WHERE organization_id = CAST(:o AS uuid)", o=org)
    return rows[0][0] if rows else None


# ── Review P1-2: the account id outlives a consent ──────────────────────────


async def test_a_consent_stores_the_account_id_that_survives_the_next_consent(x0, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    _seed(x0, x0.org_a, tag="A", deals=DEALS)
    r = await _connect(x0, OrgZoho("A2", "Z-A"))
    assert r.headers["location"] == "/crm?connected=zoho", r.text
    assert _account_id(x0, x0.org_a) == "Z-A"
    assert _counts(x0, x0.org_a)["crm_deals"] == DEALS
    # A disconnect keeps it too.
    assert (await _call(x0, _admin(x0.org_a), "POST",
                        "/crm/connection/disconnect")).status_code == 200
    assert _account_id(x0, x0.org_a) == "Z-A"
    again = await _connect(x0, OrgZoho("A3", "Z-A"))
    assert again.headers["location"] == "/crm?connected=zoho"
    other = await _connect(x0, OrgZoho("B", "Z-OTHER"))
    assert other.headers["location"] == "/crm?connect_error=different_org"
    assert _connection(x0, x0.org_a)[0] == "needs_reset"
    assert _account_id(x0, x0.org_a) == "Z-A"
    assert _counts(x0, x0.org_a)["crm_deals"] == DEALS


async def test_no_stored_account_and_a_deal_zoho_holds_connects(x0, app_engine) -> None:  # noqa: F811
    """Fracktal's case: records, no stored id, no profile. One deal is read."""
    _assert_non_priv(app_engine)
    _seed(x0, x0.org_a, tag="A", deals=DEALS, profile=False)
    zoho = OrgZoho("A2", "Z-A", holds="91")
    r = await _connect(x0, zoho)
    assert r.headers["location"] == "/crm?connected=zoho", r.text
    reads = [q for q in zoho.requests if "/crm/v2/Deals/" in q.url.path]
    assert len(reads) == 1 and reads[0].url.path.rsplit("/", 1)[-1].startswith("91")
    assert _account_id(x0, x0.org_a) == "Z-A"
    assert _counts(x0, x0.org_a)["crm_deals"] == DEALS
    assert x0.started == [x0.org_a]


@pytest.mark.parametrize("status", [None, 404, 204])
async def test_no_stored_account_and_a_deal_zoho_lacks_needs_a_reset(x0, app_engine, status) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    _seed(x0, x0.org_a, tag="A", deals=4, profile=False)
    before = _counts(x0, x0.org_a)
    zoho = OrgZoho("B", "Z-OTHER", holds="92", record_status=status)
    r = await _connect(x0, zoho)
    assert r.headers["location"] == "/crm?connect_error=different_org", r.text
    assert _connection(x0, x0.org_a)[0] == "needs_reset"
    assert _counts(x0, x0.org_a) == before
    assert _account_id(x0, x0.org_a) is None
    assert x0.started == []


async def test_no_stored_account_and_a_failed_read_changes_nothing(x0, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    _seed(x0, x0.org_a, tag="A", deals=4, profile=False)
    r = await _connect(x0, OrgZoho("B", "Z-OTHER", record_status=500))
    assert r.headers["location"] == "/crm?connect_error=org_unreadable", r.text
    assert _connection(x0, x0.org_a)[0] == "disconnected"
    assert _account_id(x0, x0.org_a) is None


async def test_no_records_and_no_stored_account_connects_and_stores_it(x0, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    zoho = OrgZoho("A", "Z-NEW")
    r = await _connect(x0, zoho)
    assert r.headers["location"] == "/crm?connected=zoho", r.text
    assert _account_id(x0, x0.org_a) == "Z-NEW"
    assert not [q for q in zoho.requests if "/crm/v2/Deals/" in q.url.path]


async def test_the_start_over_forgets_the_account_id(x0, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    _seed(x0, x0.org_a, tag="A", deals=4)
    assert (await _connect(x0, OrgZoho("A2", "Z-A"))).status_code == 302
    assert _account_id(x0, x0.org_a) == "Z-A"
    assert (await _reset(x0, {"confirm": "DELETE", "then": "none"})).status_code == 200
    assert _account_id(x0, x0.org_a) is None
    r = await _connect(x0, OrgZoho("B", "Z-OTHER"))
    assert r.headers["location"] == "/crm?connected=zoho", r.text
    assert _account_id(x0, x0.org_a) == "Z-OTHER"


# ── Review P2: the run history after a start over ───────────────────────────


async def test_the_next_import_ignores_the_runs_before_a_start_over(x0, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    conn = _seed(x0, x0.org_a, tag="A", deals=2)
    with x0.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO crm_sync_runs (organization_id, connection_id, kind, "
            "status, started_at, finished_at, counts, estimate, credits_used) "
            "VALUES (CAST(:o AS uuid), CAST(:c AS uuid), 'import', 'stopped', "
            "now() - interval '1 hour', now() - interval '50 minutes', "
            "CAST(:k AS jsonb), CAST(:e AS jsonb), 400)"),
            {"o": x0.org_a, "c": conn, "k": json.dumps({"deals": {"created": 5}}),
             "e": json.dumps({"deals": 9})})
    from acb_common.db import tenant_session
    from gateway.crm_sync import runs

    async def read() -> tuple[Any, int]:
        async with tenant_engine_scope(_app_dsn(x0)), tenant_session(x0.org_a) as db:
            return (await runs.resumable_import(db, x0.org_a),
                    await runs.credits_used_24h(db, x0.org_a))

    (counts, estimate), used = await read()
    assert counts == {"deals": {"created": 5}} and estimate == {"deals": 9} and used == 400
    assert (await _reset(x0, {"confirm": "DELETE", "then": "none"})).status_code == 200
    (counts, estimate), used = await read()
    assert (counts, estimate) == ({}, None)
    # Re-review P2: the credits are the Zoho org's, and a start over does not
    # give them back, so the same account that returns has spent them.
    assert used == 400
    # The history itself stays.
    assert _counts(x0, x0.org_a)["crm_sync_runs"] == 2


# ── Review P2: a write during the start over ────────────────────────────────


async def test_a_record_written_during_the_start_over_is_409_not_500(x0, app_engine) -> None:  # noqa: F811
    """Native mode: a lead lands while the reset runs. Its status cannot go
    (RESTRICT), so the reset stops with 409 and removes nothing."""
    _assert_non_priv(app_engine)
    r = await _call(x0, _admin(x0.org_a), "PATCH", "/crm/settings", {"mode": "native"})
    assert r.status_code == 200, r.text
    before = _counts(x0, x0.org_a)
    page = app_engine.connect()
    tx = page.begin()
    page.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": x0.org_a})
    page.execute(text(
        "INSERT INTO crm_leads (organization_id, lead_name, status_id) "
        "SELECT CAST(:o AS uuid), 'Late', id FROM crm_lead_statuses "
        "WHERE organization_id = CAST(:o AS uuid) LIMIT 1"), {"o": x0.org_a})
    task = asyncio.create_task(_reset(x0, {"confirm": "DELETE", "then": "none"}))
    # Commit only once the reset waits on the lead's lock. A fixed sleep raced
    # the reset under load: the lead committed first, and the reset removed it.
    for _ in range(300):
        await asyncio.sleep(0.05)
        with x0.admin_engine.connect() as c:
            waiting = c.execute(text(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE datname = current_database() AND wait_event_type = 'Lock'"
            )).scalar()
        if waiting:
            break
    else:
        tx.rollback()
        page.close()
        pytest.fail("the reset never waited on the lock of the late lead")
    tx.commit()
    page.close()
    r = await task
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "crm_changed"
    after = _counts(x0, x0.org_a)
    assert after["crm_leads"] == 1
    assert {t: after[t] for t in ("crm_lead_statuses", "crm_deal_statuses")} == {
        t: before[t] for t in ("crm_lead_statuses", "crm_deal_statuses")}
    assert _settings(x0, x0.org_a)[0] == "native"


# ── Done when 4: the download ────────────────────────────────────────────────


async def test_the_zip_holds_every_row_of_each_entity_and_none_of_org_b(x0, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    _seed_both(x0)
    r = await _call(x0, _admin(x0.org_a), "GET", "/crm/export/all.zip")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/zip"
    assert 'filename="crm-export.zip"' in r.headers["content-disposition"]
    archive = zipfile.ZipFile(io.BytesIO(r.content))
    names = sorted(archive.namelist())
    assert names == sorted([
        "leads.csv", "contacts.csv", "companies.csv", "deals.csv",
        "activities.csv", "module_records.csv",
    ])
    rows = {}
    for name in names:
        body = archive.read(name).decode("utf-8-sig")
        assert "B-" not in body and "B deal" not in body, f"{name} holds a row of org B"
        table = list(csv.reader(io.StringIO(body)))
        assert "organization_id" not in table[0]
        rows[name] = table[1:]
    assert len(rows["deals.csv"]) == DEALS
    for name in names:
        if name != "deals.csv":
            assert len(rows[name]) == 1, name
    # Every column of the row, JSON as JSON.
    header = archive.read("module_records.csv").decode("utf-8-sig").splitlines()[0]
    assert "fields" in header.split(",")
    assert '""Unit_Price"": 10' in archive.read("module_records.csv").decode("utf-8-sig")


async def test_the_zip_is_admin_only(x0, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    r = await _call(x0, _member(_address("m"), x0.org_a), "GET", "/crm/export/all.zip")
    assert r.status_code == 403


# ── The sync stops before the delete ─────────────────────────────────────────


async def test_the_reset_waits_for_a_sync_page_in_flight(x0, app_engine) -> None:  # noqa: F811
    """A page holds the mode ``FOR SHARE`` (the engine's own statement) and
    has written a record it has not committed. The reset takes the row ``FOR
    UPDATE``, so it waits for the commit, and then it removes that record too.

    Without the share lock the reset does not wait. Its DELETE cannot see the
    page's row, so the row stays after the commit. Mutation-checked."""
    _assert_non_priv(app_engine)
    from gateway.crm_sync import engine

    _seed(x0, x0.org_a, tag="A", deals=2, status="connected")
    page = app_engine.connect()
    tx = page.begin()
    page.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": x0.org_a})
    mode = page.execute(text(engine.MODE_SHARE_SQL), {"org": x0.org_a}).scalar()
    assert mode == "mirror"
    page.execute(text(
        "INSERT INTO crm_companies (organization_id, name, ext_source, ext_id) "
        "VALUES (CAST(:o AS uuid), 'Late', 'zoho', 'late')"), {"o": x0.org_a})
    task = asyncio.create_task(_reset(x0, {"confirm": "DELETE", "then": "none"}))
    for _ in range(40):  # up to 8 s: the reset must still be waiting
        await asyncio.sleep(0.2)
        if task.done():
            break
    waited = not task.done()
    tx.commit()
    page.close()
    r = await task
    assert waited, "the reset did not wait for the page"
    assert r.status_code == 200, r.text
    assert _counts(x0, x0.org_a)["crm_companies"] == 0
    # The next page reads the new mode and stops.
    with app_engine.begin() as c:
        c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": x0.org_a})
        assert c.execute(text(engine.MODE_SHARE_SQL), {"org": x0.org_a}).scalar() is None


# ── The migration ────────────────────────────────────────────────────────────


def _needs_reset_migration() -> Path:
    found = [
        p for p in sorted(_MIGRATIONS.glob("*.sql"))
        if p.name != "schema.generated.sql"
        and "integration_connections_status_known" in p.read_text(encoding="utf-8")
        and "needs_reset" in p.read_text(encoding="utf-8")
    ]
    assert len(found) == 1, [p.name for p in found]
    return found[0]


def test_the_status_check_takes_needs_reset_and_refuses_a_typo(x0) -> None:
    from sqlalchemy.exc import IntegrityError

    conn = _seed(x0, x0.org_a, tag="A", deals=1)
    with x0.admin_engine.begin() as c:
        c.execute(text("UPDATE integration_connections SET status = 'needs_reset' "
                       "WHERE id = CAST(:i AS uuid)"), {"i": conn})
    with pytest.raises(IntegrityError), x0.admin_engine.begin() as c:
        c.execute(text("UPDATE integration_connections SET status = 'need_reset' "
                       "WHERE id = CAST(:i AS uuid)"), {"i": conn})


def test_the_migration_replays_with_no_change(x0) -> None:
    sql = _needs_reset_migration().read_text(encoding="utf-8")
    check = ("SELECT pg_get_constraintdef(oid) FROM pg_constraint "
             "WHERE conname = 'integration_connections_status_known'")
    before = _q(x0, check)
    with x0.admin_engine.begin() as c, c.connection.dbapi_connection.cursor() as cur:
        cur.execute(sql)
    assert _q(x0, check) == before
    assert "needs_reset" in before[0][0]


async def test_the_save_checks_the_stored_account_again_under_the_lock(
    x0, app_engine, monkeypatch,  # noqa: F811
) -> None:
    """Two consents at one time: the first stores its account after the second
    read the org. The save, under the connect lock, still sees the first."""
    _assert_non_priv(app_engine)
    _seed(x0, x0.org_a, tag="A", deals=4)
    assert (await _connect(x0, OrgZoho("A2", "Z-A"))).status_code == 302
    assert _account_id(x0, x0.org_a) == "Z-A"
    from gateway.routes.crm import connection as conn

    async def stale(db: Any, org: str) -> Any:  # the read of the second consent
        return conn.KnownAccount()

    monkeypatch.setattr(conn, "_known_account", stale)
    r = await _connect(x0, OrgZoho("B", "Z-OTHER"))
    assert r.headers["location"] == "/crm?connect_error=different_org", r.text
    assert _connection(x0, x0.org_a)[0] == "needs_reset"
    assert _account_id(x0, x0.org_a) == "Z-A"



# ── Re-review P2: the lock order of the start over ──────────────────────────


async def test_the_reset_takes_the_connect_lock_first(x0, app_engine) -> None:  # noqa: F811
    """A consent holds the advisory connect lock and then writes the
    connection. The reset takes the same lock before ``crm_settings``, so the
    two never wait on each other in opposite orders (40P01)."""
    _assert_non_priv(app_engine)
    from gateway.routes.crm import connection as conn

    _seed(x0, x0.org_a, tag="A", deals=2)
    consent = app_engine.connect()
    tx = consent.begin()
    consent.execute(text(conn._CONNECT_LOCK_SQL),
                    {"key": f"crm.connect:{x0.org_a}:{conn.PROVIDER}"})
    task = asyncio.create_task(_reset(x0, {"confirm": "DELETE", "then": "none"}))
    for _ in range(20):
        await asyncio.sleep(0.2)
        if task.done():
            break
    waited = not task.done()
    tx.commit()
    consent.close()
    r = await task
    assert waited, "the reset did not take the connect lock"
    assert r.status_code == 200, r.text


async def test_a_deadlock_in_the_reset_is_409(x0, app_engine, monkeypatch) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    from gateway.routes.crm import settings as mod
    from sqlalchemy.exc import DBAPIError

    class _Orig(Exception):
        sqlstate = "40P01"

    async def deadlocked(db: Any, org: str) -> Any:
        raise DBAPIError("DELETE", {}, _Orig())

    _seed(x0, x0.org_a, tag="A", deals=2)
    before = _counts(x0, x0.org_a)
    monkeypatch.setattr(mod, "reset_crm_from_admin", deadlocked)
    r = await _reset(x0, {"confirm": "DELETE", "then": "none"})
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "crm_changed"
    assert _counts(x0, x0.org_a) == before
