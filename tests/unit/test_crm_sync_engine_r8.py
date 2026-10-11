"""R8 — the CRM mirror sync engine, against a real Postgres (WS-53 CRM-Z4 and CRM-Z5).

Migration 246 adds ``crm_sync_runs`` and three cursor columns. The engine
(``gateway/crm_sync/``) reads a fake Zoho (``httpx.MockTransport``, no network)
and writes the five mirror tables through ``core.upsert_from_source``.

What this proves against a REAL Postgres, as the NON-privileged role of the
phase-4 rehearsal (no superuser, no BYPASSRLS), with two organizations:

- one loop tick syncs two organizations, each from its OWN Zoho tenant, with no
  crossover in the rows and in the tokens that Zoho sees;
- an import that fails after page 1 resumes with no duplicate, and the counts
  equal the source counts;
- links (contact to company, deal to company and contact, note to parent)
  resolve, and an unresolved link stays NULL and is counted;
- an owner who is a member maps to the member email, and any other owner
  keeps the Zoho name in ``custom`` with a NULL owner column;
- a record deleted upstream is deleted here, and a reconcile removes an id
  that an offset-paged module no longer lists;
- the run lock refuses a second run, and a stale lock is taken over;
- a dead token stops the run, marks the connection, and the loop skips the org;
- the routes: the status shape, Sync now (202, 409, 409), and 404 with
  ``CRM_MIRROR`` off;
- no token and no record content reach a log line.

``tests/unit/test_crm_sync_engine.py`` holds the rules that need no database.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

# ⚠️ Every test here needs the real database. Without this gate the suite
# ERRORS where no ladder URL is set (the deploy run), and the deploy stops.
pytestmark = _DB_GATE

T0 = datetime(2026, 1, 1, tzinfo=UTC)
MEMBER = "member@crm-sync.test"
OUTSIDER = "outsider@zoho.test"
SENTINEL = "SENTINEL-CUSTOMER-DATA"

MODULES = ("Leads", "Deals", "Contacts", "Accounts", "Notes", "Calls", "Events")


def stamp(minutes: int) -> str:
    return (T0 + timedelta(minutes=minutes)).isoformat()


class ZohoTenant:
    """One Zoho tenant: its records, its tombstones, its users, its faults."""

    def __init__(self, tag: str, api_host: str, accounts_host: str) -> None:
        self.tag, self.api_host, self.accounts_host = tag, api_host, accounts_host
        self.data: dict[str, list[dict[str, Any]]] = {m: [] for m in MODULES}
        self.tombstones: dict[str, list[dict[str, Any]]] = {m: [] for m in MODULES}
        self.users: list[dict[str, Any]] = []
        self.hide: set[str] = set()
        self.token_error: str | None = None
        self.fail_after: dict[str, int] = {}
        self.reads: dict[str, int] = {}
        self.truncate: dict[str, int] = {}  # module -> first page that comes back empty
        self.no_count = False
        self.low_credits: set[str] = set()  # modules whose reply reports few credits
        self.on_read: dict[str, Any] = {}
        self.requests: list[httpx.Request] = []

    def add(self, module: str, ident: str, minute: int, **fields: Any) -> None:
        self.data[module].append(
            {"id": ident, "Modified_Time": stamp(minute), "Created_Time": stamp(0), **fields})

    def drop(self, module: str, ident: str, *, tombstone: bool) -> None:
        self.data[module] = [r for r in self.data[module] if r["id"] != ident]
        if tombstone:
            self.tombstones[module].append(
                {"id": ident, "deleted_time": datetime.now(UTC).isoformat()})

    # ── The wire ────────────────────────────────────────────────────────────

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if request.method == "POST" and path.endswith("/oauth/v2/token"):
            if self.token_error:
                return httpx.Response(200, json={"error": self.token_error})
            return httpx.Response(200, json={
                "access_token": f"ACCESS2-{self.tag}", "expires_in": 3600,
                "api_domain": f"https://{self.api_host}"})
        if path.endswith("/users"):
            return httpx.Response(200, json={
                "users": self.users, "info": {"more_records": False}})
        if path.endswith("/settings/fields"):
            return httpx.Response(200, json={"fields": self._fields(
                request.url.params.get("module", ""))})
        count = re.match(r"/crm/v8/(\w+)/actions/count$", path)
        if count:
            if self.no_count:
                return httpx.Response(200, json={})
            return httpx.Response(200, json={"count": len(self.data[count.group(1)])})
        deleted = re.match(r"/crm/v2/(\w+)/deleted$", path)
        if deleted:
            return self._page(request, self.tombstones[deleted.group(1)], deleted=True,
                              module=deleted.group(1))
        records = re.match(r"/crm/v2/(\w+)$", path)
        if records:
            module = records.group(1)
            self.reads[module] = self.reads.get(module, 0) + 1
            limit = self.fail_after.get(module)
            if limit is not None and self.reads[module] > limit:
                raise httpx.ConnectError("injected fault")
            if module in self.on_read:
                self.on_read[module](self.reads[module])
            response = self._page(request, self.data[module], deleted=False, module=module)
            if module in self.low_credits:
                response.headers["X-API-CREDITS-REMAINING"] = "500"
            return response
        return httpx.Response(404, json={"code": "INVALID_URL_PATTERN"})

    def _fields(self, module: str) -> list[dict[str, Any]]:
        fields = [{"api_name": "Region", "field_label": "Region", "data_type": "text",
                   "sequence_number": 90, "visible": True}]
        if module == "Deals":
            fields.append({
                "api_name": "Stage", "data_type": "picklist", "pick_list_values": [
                    {"actual_value": "Won It", "sequence_number": 2, "probability": 100,
                     "forecast_category": {"name": "Closed Won"}},
                    {"actual_value": "Qualify", "sequence_number": 1, "probability": 10,
                     "forecast_category": {"name": "Pipeline"}},
                    {"actual_value": "Gone", "sequence_number": 3,
                     "forecast_category": {"name": "Closed Lost"}}]})
        return fields

    def _page(self, request: httpx.Request, rows: list[dict[str, Any]], *,
              deleted: bool, module: str) -> httpx.Response:
        header = request.headers.get("If-Modified-Since")
        since = parsedate_to_datetime(header) if header else None
        key = "deleted_time" if deleted else "Modified_Time"
        hidden = module in self.hide and not deleted
        if hidden:
            rows = sorted(({k: v for k, v in r.items() if k != "Modified_Time"}
                           for r in rows), key=lambda r: r["id"])
        else:
            rows = sorted(rows, key=lambda r: r[key])
            if since is not None:
                rows = [r for r in rows if datetime.fromisoformat(r[key]) >= since]
        page = int(request.url.params.get("page", "1"))
        per = int(request.url.params.get("per_page", "200"))
        if module in self.truncate and not deleted and page >= self.truncate[module]:
            return httpx.Response(204)
        chunk = rows[(page - 1) * per: page * per]
        if not chunk:
            return httpx.Response(204)
        return httpx.Response(200, json={
            "data": chunk, "info": {"more_records": page * per < len(rows)}})


def seed_basic(z: ZohoTenant, prefix: str) -> None:
    """Five companies, two contacts, a lead, a deal and a note."""
    for i in range(1, 6):
        z.add("Accounts", f"{prefix}a{i}", i, Account_Name=f"{prefix}-Company-{i}",
              Region="south")
    z.add("Contacts", f"{prefix}c1", 10, First_Name="Ann", Last_Name=f"{prefix}-Ann",
          Account_Name={"id": f"{prefix}a1", "name": "x"})
    z.add("Contacts", f"{prefix}c2", 11, Last_Name=f"{prefix}-Orphan",
          Account_Name={"id": f"{prefix}missing", "name": "x"})
    z.add("Leads", f"{prefix}l1", 12, Last_Name=f"{prefix}-Lead", Company="Acme",
          Lead_Status="Fresh")
    z.add("Deals", f"{prefix}d1", 13, Deal_Name=f"{prefix}-Deal", Stage="Qualify",
          Amount=100, Account_Name={"id": f"{prefix}a1", "name": "x"},
          Contact_Name={"id": f"{prefix}c1", "name": "x"})
    z.add("Notes", f"{prefix}n1", 14, Note_Title="Hello", Note_Content="Body",
          Parent_Id={"id": f"{prefix}d1", "name": "x"}, **{"$se_module": "Deals"})


ZOHO_A = ZohoTenant("A", "www.zohoapis.in", "accounts.zoho.in")
ZOHO_B = ZohoTenant("B", "www.zohoapis.eu", "accounts.zoho.eu")


def _transport(request: httpx.Request) -> httpx.Response:
    for z in (ZOHO_A, ZOHO_B):
        if request.url.host in (z.api_host, z.accounts_host):
            return z.handle(request)
    raise AssertionError(f"a call to an unknown host {request.url.host}")


def _fresh_tenants() -> None:
    for tag, host, accounts in (("A", "www.zohoapis.in", "accounts.zoho.in"),
                                ("B", "www.zohoapis.eu", "accounts.zoho.eu")):
        fresh = ZohoTenant(tag, host, accounts)
        target = ZOHO_A if tag == "A" else ZOHO_B
        target.__dict__.update(fresh.__dict__)


@pytest.fixture
async def ns(promoted, monkeypatch):  # noqa: F811 — a fixture
    """The promoted catalog, the switches on, the fake Zoho wired, cleaned after."""
    import acb_llm.key_store as key_store
    from acb_common import get_settings
    from gateway.crm_sources.zoho import client
    from gateway.crm_sync import engine

    from tests.unit._crm_fakes import crm_switch_on

    crm_switch_on(monkeypatch, "*")
    settings = get_settings()
    for name, value in {
        "crm_mirror": True,
        "crm_zoho_client_id": "1000.R8CLIENT",
        "crm_zoho_client_secret": "r8-client-secret",
        "gateway_session_secret": "r8-session-secret-not-a-default-0123456789",
        "acb_master_key": "r8-master-key-for-the-blob",
    }.items():
        monkeypatch.setattr(settings, name, value, raising=False)
    monkeypatch.setenv("WORKBENCH_PUBLIC_URL", "https://app.metorite.com")
    monkeypatch.setattr(key_store, "_store", None)
    monkeypatch.setattr(client, "PER_PAGE", 2)
    monkeypatch.setattr(engine, "TRANSPORT", httpx.MockTransport(_transport))
    _fresh_tenants()
    dsn = promoted.app_url.render_as_string(hide_password=False)
    async with tenant_engine_scope(dsn):
        yield promoted
    with promoted.admin_engine.begin() as c:
        for org in (promoted.org_a, promoted.org_b):
            for table in (
                "crm_activities", "crm_deal_contacts", "crm_deals", "crm_leads",
                "crm_contacts", "crm_companies", "crm_lead_statuses",
                "crm_deal_statuses", "crm_field_defs", "crm_sync_runs",
                "crm_sync_cursors", "crm_settings", "integration_connections",
            ):
                c.execute(text(
                    f"DELETE FROM {table} WHERE organization_id = CAST(:o AS uuid)"),
                    {"o": org})
        c.execute(text("DELETE FROM app_user WHERE email LIKE '%@crm-sync.test'"))


def _admin(email: str, org: str) -> Any:
    from acb_auth import UserContext, UserRole, build_access

    return UserContext(
        email=email, role=UserRole.EMPLOYEE,
        access=build_access(["feature:crm", "admin:access:manage"]),
        organization_id=org,
    )


async def _connect(
    p: Any, org: str, z: ZohoTenant, *, expired: bool = False, mode: str = "mirror",
) -> str:
    """Store a connection of ``org`` to ``z`` and set the org to Mirror mode."""
    from acb_common.db import tenant_session
    from gateway.connections import store
    from gateway.crm_sources.base import SourceCredential

    credential = SourceCredential(
        access_token=f"ACCESS-{z.tag}", refresh_token=f"REFRESH-{z.tag}",
        expires_at=datetime.now(UTC) + (timedelta(hours=-1) if expired else timedelta(hours=1)),
        scopes=(),
        provider_meta={
            "api_domain": f"https://{z.api_host}",
            "accounts_server": f"https://{z.accounts_host}",
            "location": "in" if z.tag == "A" else "eu",
        },
    )
    async with tenant_session(org) as db:
        row = await store.save_credential(
            db, org, "zoho", "crm", credential, connected_by="admin@crm-sync.test",
            scopes=("ZohoCRM.modules.leads.READ",))
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO crm_settings (organization_id, mode, connection_id) "
            "VALUES (CAST(:o AS uuid), :m, CAST(:c AS uuid)) "
            "ON CONFLICT (organization_id) DO UPDATE SET mode = EXCLUDED.mode, "
            "connection_id = EXCLUDED.connection_id"),
            {"o": org, "m": mode, "c": row.id})
    return row.id


def _q(p: Any, sql: str, **params: Any) -> list[Any]:
    with p.admin_engine.connect() as c:
        return list(c.execute(text(sql), params).all())


def _count(p: Any, table: str, org: str, where: str = "TRUE") -> int:
    return _q(p, f"SELECT count(*) FROM {table} WHERE organization_id = "
                 f"CAST(:o AS uuid) AND {where}", o=org)[0][0]


def _assert_non_priv(app_eng: Any) -> None:
    with app_eng.connect() as c:
        role = c.execute(text(
            "SELECT rolsuper, rolbypassrls FROM pg_roles "
            "WHERE rolname = current_user")).first()
    assert role is not None and not role[0] and not role[1], (
        "this suite connects as a SUPERUSER/BYPASSRLS role, so RLS is bypassed")


async def _call(user: Any, method: str, path: str) -> Any:
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
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://crm.test",
        ) as client:
            return await client.request(method, path)
    finally:
        release_tenant(scope)


# ── Two orgs, one loop ───────────────────────────────────────────────────────

async def test_one_tick_syncs_two_orgs_each_from_its_own_zoho(ns, app_engine) -> None:  # noqa: F811
    from gateway.crm_sync import scheduler

    _assert_non_priv(app_engine)
    seed_basic(ZOHO_A, "A")
    seed_basic(ZOHO_B, "B")
    await _connect(ns, ns.org_a, ZOHO_A)
    await _connect(ns, ns.org_b, ZOHO_B)

    assert await scheduler.run_tick() == 2

    for org, tag in ((ns.org_a, "A"), (ns.org_b, "B")):
        names = {r[0] for r in _q(
            ns, "SELECT name FROM crm_companies WHERE organization_id = CAST(:o AS uuid)",
            o=org)}
        assert names == {f"{tag}-Company-{i}" for i in range(1, 6)}
        runs = _q(ns, "SELECT status, kind, error FROM crm_sync_runs WHERE "
                      "organization_id = CAST(:o AS uuid)", o=org)
        assert runs == [("ok", "import", None)], runs
    # Each Zoho saw only its own token.
    for z in (ZOHO_A, ZOHO_B):
        tokens = {r.headers.get("Authorization") for r in z.requests
                  if "Authorization" in r.headers}
        assert tokens == {f"Zoho-oauthtoken ACCESS-{z.tag}"}, tokens
    # The loop does not run an org again inside the interval.
    assert await scheduler.run_tick() == 0


# ── Import, resume, counts ───────────────────────────────────────────────────

async def test_an_import_that_fails_after_page_one_resumes_with_no_duplicate(ns) -> None:
    from gateway.crm_sync import engine

    seed_basic(ZOHO_A, "A")
    await _connect(ns, ns.org_a, ZOHO_A)
    ZOHO_A.fail_after["Accounts"] = 1  # page 1 of 3 reads, the next page faults

    first = await engine.run_org(ns.org_a)
    assert (first.status, first.kind, first.error) == ("failed", "import", "source_error")
    assert _count(ns, "crm_companies", ns.org_a) == 2
    cursor = _q(ns, "SELECT cursor_state, initial_done_at FROM crm_sync_cursors "
                    "WHERE organization_id = CAST(:o AS uuid) AND module = 'zoho:company'",
                o=ns.org_a)
    assert cursor and cursor[0][1] is None and cursor[0][0]["in_progress"] is True

    del ZOHO_A.fail_after["Accounts"]
    second = await engine.run_org(ns.org_a)
    assert (second.status, second.kind) == ("ok", "import")
    assert _count(ns, "crm_companies", ns.org_a) == 5
    ids = [r[0] for r in _q(ns, "SELECT ext_id FROM crm_companies WHERE "
                                "organization_id = CAST(:o AS uuid)", o=ns.org_a)]
    assert len(ids) == len(set(ids)) == 5
    counts = second.counts
    assert counts["organizations"]["seen"] == 5
    assert counts["organizations"]["upserted"] == 5
    assert counts["contacts"]["seen"] == 2
    assert counts["leads"]["seen"] == 1 and counts["deals"]["seen"] == 1
    assert counts["activities"]["seen"] == 1
    # The estimate came from the count endpoint.
    est = _q(ns, "SELECT estimate FROM crm_sync_runs WHERE id = CAST(:i AS uuid)",
             i=second.run_id)[0][0]
    assert est["organizations"] == 5
    # The next run is incremental.
    third = await engine.run_org(ns.org_a)
    assert (third.status, third.kind) == ("ok", "incremental")
    assert _count(ns, "crm_companies", ns.org_a) == 5


async def test_a_run_stops_at_its_credit_budget_and_resumes(ns) -> None:
    from gateway.crm_sync import engine

    seed_basic(ZOHO_A, "A")
    await _connect(ns, ns.org_a, ZOHO_A)

    stopped = await engine.run_org(ns.org_a, credit_budget=5)
    assert (stopped.status, stopped.error) == ("stopped", "budget")
    assert stopped.credits_used <= 5
    done = await engine.run_org(ns.org_a)
    assert done.status == "ok"
    assert _count(ns, "crm_companies", ns.org_a) == 5


async def test_field_defs_and_stages_are_mirrored_before_the_records(ns) -> None:
    from gateway.crm_sync import engine

    seed_basic(ZOHO_A, "A")
    await _connect(ns, ns.org_a, ZOHO_A)
    assert (await engine.run_org(ns.org_a)).status == "ok"

    defs = _q(ns, "SELECT entity, key, type, source FROM crm_field_defs WHERE "
                  "organization_id = CAST(:o AS uuid) ORDER BY entity", o=ns.org_a)
    assert ("deal", "Region", "text", "zoho") in [tuple(d) for d in defs]
    stages = _q(ns, "SELECT name, type, position, probability FROM crm_deal_statuses "
                    "WHERE organization_id = CAST(:o AS uuid) ORDER BY position",
                o=ns.org_a)
    assert [tuple(s) for s in stages][:3] == [
        ("Qualify", "open", 0, 10), ("Won It", "won", 1, 100), ("Gone", "lost", 2, 0)]
    deal = _q(ns, "SELECT s.name FROM crm_deals d JOIN crm_deal_statuses s "
                  "ON s.id = d.status_id WHERE d.organization_id = CAST(:o AS uuid)",
              o=ns.org_a)
    assert [d[0] for d in deal] == ["Qualify"]
    lead = _q(ns, "SELECT s.name FROM crm_leads l JOIN crm_lead_statuses s "
                  "ON s.id = l.status_id WHERE l.organization_id = CAST(:o AS uuid)",
              o=ns.org_a)
    assert [r[0] for r in lead] == ["Fresh"]


# ── Links and owners ─────────────────────────────────────────────────────────

async def test_links_resolve_and_an_unresolved_link_stays_null_and_is_counted(ns) -> None:
    from gateway.crm_sync import engine

    seed_basic(ZOHO_A, "A")
    await _connect(ns, ns.org_a, ZOHO_A)
    result = await engine.run_org(ns.org_a)
    assert result.status == "ok"

    contact = _q(ns, "SELECT c.last_name, co.ext_id FROM crm_contacts c "
                     "LEFT JOIN crm_companies co ON co.id = c.company_id "
                     "WHERE c.organization_id = CAST(:o AS uuid) ORDER BY c.ext_id",
                 o=ns.org_a)
    assert [tuple(r) for r in contact] == [("A-Ann", "Aa1"), (None, None)]
    deal = _q(ns, "SELECT co.ext_id, ct.ext_id FROM crm_deals d "
                  "JOIN crm_companies co ON co.id = d.company_id "
                  "JOIN crm_deal_contacts dc ON dc.deal_id = d.id "
                  "JOIN crm_contacts ct ON ct.id = dc.contact_id "
                  "WHERE d.organization_id = CAST(:o AS uuid)", o=ns.org_a)
    assert [tuple(r) for r in deal] == [("Aa1", "Ac1")]
    note = _q(ns, "SELECT d.ext_id, a.created_by FROM crm_activities a "
                  "JOIN crm_deals d ON d.id = a.deal_id "
                  "WHERE a.organization_id = CAST(:o AS uuid)", o=ns.org_a)
    assert [tuple(r) for r in note] == [("Ad1", "crm:zoho-sync")]
    assert result.counts["contacts"]["unlinked"] == 1


async def test_owner_maps_to_a_member_else_keeps_the_name(ns) -> None:
    from gateway.crm_sync import engine

    with ns.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO app_user (email, display_name, role, status, organization_id) "
            "VALUES (:e, :e, 'executive', 'active', CAST(:o AS uuid))"),
            {"e": MEMBER, "o": ns.org_a})
    ZOHO_A.users = [{"id": "u1", "email": MEMBER}, {"id": "u2", "email": OUTSIDER}]
    ZOHO_A.add("Accounts", "m1", 1, Account_Name="Mine",
               Owner={"id": "u1", "name": "Member Name"})
    ZOHO_A.add("Accounts", "o1", 2, Account_Name="Theirs",
               Owner={"id": "u2", "name": "Outside Person"})
    await _connect(ns, ns.org_a, ZOHO_A)
    assert (await engine.run_org(ns.org_a)).status == "ok"

    rows = {r[0]: r for r in _q(
        ns, "SELECT name, owner_email, custom FROM crm_companies WHERE "
            "organization_id = CAST(:o AS uuid)", o=ns.org_a)}
    assert rows["Mine"][1] == MEMBER and "_owner_name" not in rows["Mine"][2]
    assert rows["Theirs"][1] is None
    assert rows["Theirs"][2]["_owner_name"] == "Outside Person"


# ── Deletes and the reconcile ────────────────────────────────────────────────

async def test_a_record_deleted_upstream_is_deleted_here_on_the_next_run(ns) -> None:
    from gateway.crm_sync import engine

    seed_basic(ZOHO_A, "A")
    await _connect(ns, ns.org_a, ZOHO_A)
    assert (await engine.run_org(ns.org_a)).status == "ok"
    ZOHO_A.drop("Contacts", "Ac1", tombstone=True)

    result = await engine.run_org(ns.org_a)
    assert (result.status, result.kind) == ("ok", "incremental")
    assert _count(ns, "crm_contacts", ns.org_a, "ext_id = 'Ac1'") == 0
    assert _count(ns, "crm_contacts", ns.org_a) == 1
    assert result.counts["contacts"]["deleted"] == 1
    # The deal that named the contact stays, and its link row went with it.
    assert _count(ns, "crm_deals", ns.org_a) == 1


async def test_a_reconcile_removes_an_id_an_offset_paged_module_no_longer_lists(ns) -> None:
    from gateway.crm_sync import engine

    seed_basic(ZOHO_A, "A")
    ZOHO_A.hide.add("Accounts")
    await _connect(ns, ns.org_a, ZOHO_A)
    assert (await engine.run_org(ns.org_a)).status == "ok"
    state = _q(ns, "SELECT cursor_state FROM crm_sync_cursors WHERE organization_id = "
                   "CAST(:o AS uuid) AND module = 'zoho:company'", o=ns.org_a)[0][0]
    assert state["offset_paged"] is True

    ZOHO_A.drop("Accounts", "Aa5", tombstone=False)  # no tombstone: only a compare sees it
    quiet = await engine.run_org(ns.org_a)
    assert (quiet.kind, quiet.status) == ("incremental", "ok")
    assert _count(ns, "crm_companies", ns.org_a) == 5

    with ns.admin_engine.begin() as c:
        c.execute(text("UPDATE crm_sync_cursors SET last_reconciled_at = now() - "
                       "interval '2 days' WHERE organization_id = CAST(:o AS uuid) "
                       "AND module = 'zoho:company'"), {"o": ns.org_a})
    result = await engine.run_org(ns.org_a)
    assert (result.kind, result.status) == ("reconcile", "ok")
    assert _count(ns, "crm_companies", ns.org_a) == 4
    assert _count(ns, "crm_companies", ns.org_a, "ext_id = 'Aa5'") == 0
    # A second run inside 24 hours is not a reconcile.
    assert (await engine.run_org(ns.org_a)).kind == "incremental"


async def test_a_reconcile_does_not_trust_an_empty_listing(ns) -> None:
    from gateway.crm_sync import engine

    seed_basic(ZOHO_A, "A")
    ZOHO_A.hide.add("Accounts")
    await _connect(ns, ns.org_a, ZOHO_A)
    assert (await engine.run_org(ns.org_a)).status == "ok"
    ZOHO_A.data["Accounts"] = []
    with ns.admin_engine.begin() as c:
        c.execute(text("UPDATE crm_sync_cursors SET last_reconciled_at = now() - "
                       "interval '2 days' WHERE organization_id = CAST(:o AS uuid) "
                       "AND module = 'zoho:company'"), {"o": ns.org_a})
    assert (await engine.run_org(ns.org_a)).kind == "reconcile"
    assert _count(ns, "crm_companies", ns.org_a) == 5


# ── The lock ─────────────────────────────────────────────────────────────────

async def test_a_second_run_gets_sync_running_and_a_stale_lock_is_taken_over(ns) -> None:
    from gateway.crm_sync import engine

    seed_basic(ZOHO_A, "A")
    await _connect(ns, ns.org_a, ZOHO_A)
    first = await engine.acquire(ns.org_a)
    with pytest.raises(engine.SyncRunning):
        await engine.acquire(ns.org_a)
    both = await asyncio.gather(
        engine.acquire(ns.org_a), engine.acquire(ns.org_a), return_exceptions=True)
    assert all(isinstance(e, engine.SyncRunning) for e in both), both

    with ns.admin_engine.begin() as c:
        c.execute(text("UPDATE crm_sync_runs SET heartbeat_at = now() - interval "
                       "'20 minutes' WHERE id = CAST(:i AS uuid)"), {"i": first.run_id})
    taken = await engine.acquire(ns.org_a)
    assert taken.run_id != first.run_id
    old = _q(ns, "SELECT status, error FROM crm_sync_runs WHERE id = CAST(:i AS uuid)",
             i=first.run_id)
    assert [tuple(r) for r in old] == [("failed", "stale")]
    assert (await engine.execute(taken)).status == "ok"


# ── A dead token ─────────────────────────────────────────────────────────────

async def test_a_dead_token_stops_the_run_marks_the_connection_and_the_loop_skips(ns) -> None:
    from gateway.crm_sync import engine, scheduler

    seed_basic(ZOHO_A, "A")
    ZOHO_A.token_error = "invalid_code"
    await _connect(ns, ns.org_a, ZOHO_A, expired=True)

    result = await engine.run_org(ns.org_a)
    assert (result.status, result.error) == ("stopped", "needs_reconnect")
    status = _q(ns, "SELECT status, last_error FROM integration_connections WHERE "
                    "organization_id = CAST(:o AS uuid)", o=ns.org_a)
    assert [tuple(r) for r in status] == [("needs_reconnect", "invalid_code")]
    assert _count(ns, "crm_companies", ns.org_a) == 0
    assert not any("/crm/v2/" in str(r.url) for r in ZOHO_A.requests)
    assert await scheduler.is_due(ns.org_a) is False
    with pytest.raises(engine.NotConnected):
        await engine.acquire(ns.org_a)


# ── The routes ───────────────────────────────────────────────────────────────

async def test_the_status_route_shape_and_sync_now(ns) -> None:
    from gateway.crm_sync import scheduler

    seed_basic(ZOHO_A, "A")
    admin = _admin("admin@crm-sync.test", ns.org_a)

    # No connection: status is "never", Sync now is 409 not_connected.
    never = await _call(admin, "GET", "/crm/sync/status")
    assert never.status_code == 200
    assert never.json() == {
        "state": "never", "kind": None, "started_at": None, "finished_at": None,
        "last_success_at": None, "counts": {}, "estimate": None, "error": None,
        "next_run_at": None, "failed": 0}
    refused = await _call(admin, "POST", "/crm/sync/zoho")
    assert (refused.status_code, refused.json()) == (409, {"detail": "not_connected"})

    await _connect(ns, ns.org_a, ZOHO_A)
    started = await _call(admin, "POST", "/crm/sync/zoho")
    assert (started.status_code, started.json()) == (202, {"started": True})
    again = await _call(admin, "POST", "/crm/sync/zoho")
    assert (again.status_code, again.json()) == (409, {"detail": "sync_running"})
    await asyncio.gather(*list(scheduler._background))

    body = (await _call(admin, "GET", "/crm/sync/status")).json()
    assert body["state"] == "idle" and body["kind"] == "import" and body["error"] is None
    assert body["counts"] == {"organizations": 5, "contacts": 2, "leads": 1,
                              "deals": 1, "activities": 1}
    assert body["estimate"] == {"organizations": 5, "contacts": 2, "leads": 1,
                                "deals": 1, "activities": 1}
    assert body["started_at"] and body["finished_at"] and body["last_success_at"]
    assert body["next_run_at"]
    text_body = json.dumps(body)
    assert "ACCESS" not in text_body and "REFRESH" not in text_body
    # A member without the admin permission cannot start a run.
    from acb_auth import UserContext, UserRole, build_access

    member = UserContext(email=MEMBER, role=UserRole.EMPLOYEE,
                         access=build_access(["feature:crm"]), organization_id=ns.org_a)
    assert (await _call(member, "POST", "/crm/sync/zoho")).status_code == 403


async def test_with_crm_mirror_off_both_routes_404_and_the_loop_starts_no_task(
    ns, monkeypatch,
) -> None:
    from acb_common import get_settings
    from gateway.crm_sync import scheduler

    monkeypatch.setattr(get_settings(), "crm_mirror", False, raising=False)
    admin = _admin("admin@crm-sync.test", ns.org_a)
    assert (await _call(admin, "GET", "/crm/sync/status")).status_code == 404
    assert (await _call(admin, "POST", "/crm/sync/zoho")).status_code == 404
    assert await scheduler.start_crm_mirror_sync() is False
    assert scheduler._task is None

    monkeypatch.setattr(get_settings(), "crm_mirror", True, raising=False)
    try:
        assert await scheduler.start_crm_mirror_sync() is True
        assert scheduler.loop_running()
    finally:
        await scheduler.stop_crm_mirror_sync()
    assert not scheduler.loop_running()


async def test_the_loop_skips_an_org_that_is_not_in_mirror_mode(ns) -> None:
    from gateway.crm_sync import scheduler

    await _connect(ns, ns.org_a, ZOHO_A, mode="native")
    assert await scheduler.is_due(ns.org_a) is False


# ── The logs ─────────────────────────────────────────────────────────────────

async def test_no_token_and_no_record_content_reach_a_log_line(
    ns, monkeypatch, caplog, capfd,
) -> None:
    from gateway.crm_sync import engine

    caplog.set_level("DEBUG")
    ZOHO_A.add("Accounts", "s1", 1, Account_Name=SENTINEL, Region=SENTINEL)
    ZOHO_A.add("Accounts", "s2", 2, Account_Name=f"{SENTINEL}-2")
    await _connect(ns, ns.org_a, ZOHO_A)
    real = engine._write_record

    async def flaky(ctx, db, entity, record, caches):
        if record.ext_id == "s1":
            raise RuntimeError(f"boom {record.fields['Account_Name']}")
        return await real(ctx, db, entity, record, caches)

    monkeypatch.setattr(engine, "_write_record", flaky)
    result = await engine.run_org(ns.org_a)
    assert result.status == "ok" and result.counts["organizations"]["failed"] == 1
    assert _count(ns, "crm_companies", ns.org_a) == 1  # the other record of the page landed
    out = capfd.readouterr()
    everything = caplog.text + out.out + out.err
    for secret in (SENTINEL, "ACCESS-A", "REFRESH-A", "r8-client-secret"):
        assert secret not in everything, secret
    assert "s1" in everything  # the id is logged, the content is not


# ── Review round 1 ───────────────────────────────────────────────────────────

async def _import_hidden(ns, count: int = 5) -> None:
    from gateway.crm_sync import engine

    for i in range(1, count + 1):
        ZOHO_A.add("Accounts", f"Aa{i}", i, Account_Name=f"Co-{i}")
    ZOHO_A.hide.add("Accounts")
    await _connect(ns, ns.org_a, ZOHO_A)
    assert (await engine.run_org(ns.org_a)).status == "ok"


def _make_due_for_reconcile(ns) -> None:
    with ns.admin_engine.begin() as c:
        c.execute(text("UPDATE crm_sync_cursors SET last_reconciled_at = now() - "
                       "interval '2 days' WHERE organization_id = CAST(:o AS uuid) "
                       "AND module = 'zoho:company'"), {"o": ns.org_a})


async def test_p1_1_an_offset_paged_entity_is_not_listed_by_an_incremental_run(ns) -> None:
    from gateway.crm_sync import engine

    await _import_hidden(ns)
    before = ZOHO_A.reads["Accounts"]
    for _ in range(2):
        assert (await engine.run_org(ns.org_a)).kind == "incremental"
    assert ZOHO_A.reads["Accounts"] == before, "an incremental run listed the whole module"


def test_p1_1_the_default_budget_assumes_the_free_edition_and_idle_spend_spaces_runs() -> None:
    from gateway.crm_sync import engine

    assert engine.DAILY_CREDIT_BUDGET == 500
    assert engine.next_interval(0) == engine.INTERVAL_SECONDS
    assert engine.next_interval(8) > engine.INTERVAL_SECONDS  # 8 credits a run, 500 a day


async def test_p1_2_a_reconcile_that_stops_resumes_where_it_stopped(ns) -> None:
    from gateway.crm_sync import engine

    await _import_hidden(ns, 6)
    ZOHO_A.drop("Accounts", "Aa6", tombstone=False)
    _make_due_for_reconcile(ns)
    ZOHO_A.fail_after["Accounts"] = ZOHO_A.reads["Accounts"] + 1  # page 2 faults
    first = await engine.run_org(ns.org_a)
    assert (first.kind, first.status) == ("reconcile", "failed")
    del ZOHO_A.fail_after["Accounts"]
    mark = ZOHO_A.reads["Accounts"]
    second = await engine.run_org(ns.org_a)
    assert (second.kind, second.status) == ("reconcile", "ok"), second
    assert ZOHO_A.reads["Accounts"] - mark == 2, "the walk restarted at page 1"
    assert _count(ns, "crm_companies", ns.org_a, "ext_id = 'Aa6'") == 0
    assert _count(ns, "crm_companies", ns.org_a) == 5


async def test_p1_3_a_truncated_walk_deletes_nothing(ns) -> None:
    from gateway.crm_sync import engine

    await _import_hidden(ns)
    ZOHO_A.truncate["Accounts"] = 2  # page 2 comes back empty, as a 204
    _make_due_for_reconcile(ns)
    result = await engine.run_org(ns.org_a)
    assert result.status == "ok"
    assert _count(ns, "crm_companies", ns.org_a) == 5
    assert result.counts["organizations"]["refused"] == 1


async def test_p1_3_a_delete_of_more_than_a_fifth_of_the_rows_is_refused(ns) -> None:
    from gateway.crm_sync import engine

    await _import_hidden(ns, 12)
    for i in (1, 2, 3, 4):
        ZOHO_A.drop("Accounts", f"Aa{i}", tombstone=False)
    ZOHO_A.no_count = True  # no count to compare: the 20% rule must hold alone
    _make_due_for_reconcile(ns)
    result = await engine.run_org(ns.org_a)
    assert _count(ns, "crm_companies", ns.org_a) == 12
    assert result.counts["organizations"]["refused"] == 1


async def test_p2_1_a_failed_record_is_counted_in_the_status_and_retried(ns, monkeypatch) -> None:
    from gateway.crm_sync import engine

    seed_basic(ZOHO_A, "A")
    await _connect(ns, ns.org_a, ZOHO_A)
    real = engine._write_record
    failed_once: set[str] = set()

    async def flaky(ctx, db, entity, record, caches):
        if record.ext_id == "Aa3" and "Aa3" not in failed_once:
            failed_once.add("Aa3")
            raise RuntimeError("boom")
        return await real(ctx, db, entity, record, caches)

    monkeypatch.setattr(engine, "_write_record", flaky)
    assert (await engine.run_org(ns.org_a)).status == "ok"
    assert _count(ns, "crm_companies", ns.org_a, "ext_id = 'Aa3'") == 0
    body = (await _call(_admin("a@crm-sync.test", ns.org_a), "GET", "/crm/sync/status")).json()
    assert body["failed"] == 1 and body["counts"]["organizations"] == 5
    assert (await engine.run_org(ns.org_a)).status == "ok"
    assert _count(ns, "crm_companies", ns.org_a, "ext_id = 'Aa3'") == 1


async def test_p2_2_a_page_already_paid_for_is_kept_when_credits_run_low(ns) -> None:
    from gateway.crm_sync import engine

    seed_basic(ZOHO_A, "A")
    ZOHO_A.low_credits.add("Accounts")
    await _connect(ns, ns.org_a, ZOHO_A)
    result = await engine.run_org(ns.org_a)
    assert (result.status, result.error) == ("stopped", "budget")
    assert _count(ns, "crm_companies", ns.org_a) == 2  # the first page of two rows


async def test_p2_3_due_orgs_run_at_the_same_time(ns, monkeypatch) -> None:
    from gateway.crm_sync import engine, scheduler

    await _connect(ns, ns.org_a, ZOHO_A)
    await _connect(ns, ns.org_b, ZOHO_B)
    entered: list[str] = []
    both = asyncio.Event()

    async def fake_run(org, **kw):
        entered.append(org)
        if len(entered) == 2:
            both.set()
        await asyncio.wait_for(both.wait(), 3)

    monkeypatch.setattr(engine, "run_org", fake_run)
    assert await asyncio.wait_for(scheduler.run_tick(), 10) == 2


async def test_p2_4_acquire_refuses_an_org_that_is_not_in_mirror_mode(ns, monkeypatch) -> None:
    from acb_common import get_settings
    from gateway.crm_sync import engine

    await _connect(ns, ns.org_a, ZOHO_A, mode="native")
    with pytest.raises(engine.NotConnected):
        await engine.acquire(ns.org_a)
    refused = await _call(_admin("a@crm-sync.test", ns.org_a), "POST", "/crm/sync/zoho")
    assert (refused.status_code, refused.json()) == (409, {"detail": "not_connected"})
    await _connect(ns, ns.org_a, ZOHO_A)
    monkeypatch.setattr(get_settings(), "crm_orgs", ns.org_b, raising=False)
    with pytest.raises(engine.NotConnected):
        await engine.acquire(ns.org_a)


async def test_p2_4_a_run_stops_when_the_mode_changes_between_pages(ns) -> None:
    from gateway.crm_sync import engine

    seed_basic(ZOHO_A, "A")
    await _connect(ns, ns.org_a, ZOHO_A)

    def flip(read: int) -> None:
        if read == 2:
            with ns.admin_engine.begin() as c:
                c.execute(text("UPDATE crm_settings SET mode = 'native' WHERE "
                               "organization_id = CAST(:o AS uuid)"), {"o": ns.org_a})

    ZOHO_A.on_read["Accounts"] = flip
    result = await engine.run_org(ns.org_a)
    assert (result.status, result.error) == ("stopped", "mode_changed")
    assert _count(ns, "crm_companies", ns.org_a) < 5
