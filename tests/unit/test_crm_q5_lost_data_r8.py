"""R8 — the data the mirror lost (WS-53 CRM-Q5), against a real Postgres.

Three things that a plain Zoho read drops, and that the sync now keeps:

- **Converted leads.** Zoho hides a converted lead unless the list asks for
  ``converted=both``. The lead lands with its conversion links. A link whose
  record is not here yet stays NULL, and a later run fills it in.
- **Zoho Tasks.** A task is a ``crm_activities`` row of type ``task``. A
  connection that lacks the Tasks scope is skipped and reported as unavailable,
  and the run still succeeds and the connection stays connected.
- **Deal contact roles.** One call for each changed deal fills
  ``crm_deal_contacts``. A role that Zoho removes is removed here. A low budget
  leaves the deals queued and counts them as skipped.

Each new step has a key of its own in ``crm_sync_runs.counts``. Every path is
checked with two organizations that use the SAME Zoho ids.

``tests/unit/test_crm_sync_engine_r8.py`` owns the fake Zoho. This file adds
the three behaviours to it and nothing else.
"""

# ruff: noqa: F811 - the fixtures come in by import and are used as arguments
from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

from tests.unit.test_crm_sync_engine_r8 import (  # noqa: F401
    _admin,
    _call,
    ZohoTenant,
    _connect,
    _count,
    _q,
    _the_loop_is_not_stopping,
    ns,
    stamp,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

# ⚠️ Every test here needs the real database (see the engine suite).
pytestmark = _DB_GATE


class Q5Zoho(ZohoTenant):
    """The fake Zoho of the engine suite, plus the three CRM-Q5 behaviours."""

    def __init__(self, tag: str, api_host: str, accounts_host: str) -> None:
        super().__init__(tag, api_host, accounts_host)
        self.roles: dict[str, list[dict[str, Any]]] = {}
        self.role_calls: list[str] = []
        self.scope_denied: set[str] = set()
        #: module -> (status, body) for a fault on its record and deleted reads.
        self.faults: dict[str, tuple[int, dict[str, Any]]] = {}
        self.roles_fault: tuple[int, dict[str, str]] | None = None
        self.lead_params: list[str | None] = []
        self.lead_since: list[str | None] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        roles = re.match(r"/crm/v2/Deals/(\w+)/Contact_Roles$", path)
        if roles:
            self.requests.append(request)
            self.role_calls.append(roles.group(1))
            if self.roles_fault is not None:
                return httpx.Response(
                    self.roles_fault[0], json={"code": "X"},
                    headers=self.roles_fault[1])
            rows = self.roles.get(roles.group(1), [])
            if not rows:
                return httpx.Response(204)
            return httpx.Response(200, json={"data": rows})
        module = re.match(r"/crm/v2/(\w+)(/deleted)?$", path)
        if module and module.group(1) in self.faults:
            self.requests.append(request)
            status, body = self.faults[module.group(1)]
            return httpx.Response(status, json=body)
        if module and module.group(1) in self.scope_denied:
            self.requests.append(request)
            return httpx.Response(401, json={
                "code": "OAUTH_SCOPE_MISMATCH", "message": "invalid oauth scope"})
        if module and module.group(1) == "Leads" and not module.group(2):
            both = request.url.params.get("converted")
            self.lead_params.append(both)
            self.lead_since.append(request.headers.get("If-Modified-Since"))
            if both != "both":
                held = self.data["Leads"]
                self.data["Leads"] = [r for r in held if not r.get("Converted__s")]
                try:
                    return super().handle(request)
                finally:
                    self.data["Leads"] = held
        return super().handle(request)


@pytest.fixture
def zoho(ns, monkeypatch):
    """Two fresh fake tenants, wired under every provider call."""
    from gateway.crm_sync import engine

    tenants = (
        Q5Zoho("A", "www.zohoapis.in", "accounts.zoho.in"),
        Q5Zoho("B", "www.zohoapis.eu", "accounts.zoho.eu"),
    )

    def transport(request: httpx.Request) -> httpx.Response:
        for z in tenants:
            if request.url.host in (z.api_host, z.accounts_host):
                return z.handle(request)
        raise AssertionError(f"a call to an unknown host {request.url.host}")

    monkeypatch.setattr(engine, "TRANSPORT", httpx.MockTransport(transport))
    return tenants


def _seed(z: Q5Zoho) -> None:
    """The SAME Zoho ids in both tenants, with different names."""
    z.add("Accounts", "a1", 1, Account_Name=f"{z.tag}-Company")
    z.add("Contacts", "c1", 2, Last_Name=f"{z.tag}-One", Account_Name={"id": "a1"})
    z.add("Contacts", "c2", 3, Last_Name=f"{z.tag}-Two", Account_Name={"id": "a1"})


def _converted(z: Q5Zoho, ident: str = "l2") -> None:
    z.add(
        "Leads", ident, 5, Last_Name=f"{z.tag}-Converted", Company="Acme",
        Lead_Status="Qualified", Converted__s=True, Converted_Date_Time=stamp(6),
        Converted_Contact={"id": "c1", "name": "x"},
        Converted_Account={"id": "a1", "name": "x"},
        Converted_Deal={"id": "d1", "name": "x"},
    )


def _lead_links(p: Any, org: str) -> Any:
    return _q(
        p,
        "SELECT l.ext_id, l.converted_at, ct.ext_id, co.ext_id, d.ext_id "
        "FROM crm_leads l "
        "LEFT JOIN crm_contacts ct ON ct.id = l.converted_contact_id "
        "LEFT JOIN crm_companies co ON co.id = l.converted_organization_id "
        "LEFT JOIN crm_deals d ON d.id = l.converted_deal_id "
        "WHERE l.organization_id = CAST(:o AS uuid) AND l.converted_at IS NOT NULL",
        o=org,
    )


# ── Converted leads ──────────────────────────────────────────────────────────


async def test_converted_leads_land_and_a_later_run_resolves_a_missing_link(ns, zoho) -> None:
    from gateway.crm_sync import engine

    a, _b = zoho
    _seed(a)
    a.add("Leads", "l1", 4, Last_Name="Open", Company="Acme", Lead_Status="Fresh")
    _converted(a)  # its deal d1 is not in Zoho yet
    await _connect(ns, ns.org_a, a)

    first = await engine.run_org(ns.org_a)
    assert first.status == "ok"
    assert set(a.lead_params) == {"both"}, "the Leads read must ask for converted=both"
    assert _count(ns, "crm_leads", ns.org_a) == 2
    rows = _lead_links(ns, ns.org_a)
    assert len(rows) == 1
    ext, converted_at, contact, company, deal = rows[0]
    assert (ext, contact, company, deal) == ("l2", "c1", "a1", None)
    assert converted_at == datetime.fromisoformat(stamp(6)).astimezone(UTC)
    step = first.counts["converted_leads"]
    assert (step["seen"], step["upserted"], step["unlinked"]) == (1, 1, 1)

    a.add("Deals", "d1", 9, Deal_Name="Won", Stage="Qualify", Amount=5)
    second = await engine.run_org(ns.org_a)
    assert (second.status, second.kind) == ("ok", "incremental")
    assert _lead_links(ns, ns.org_a)[0][4] == "d1"
    assert second.counts["converted_leads"]["unlinked"] == 0


async def test_a_lead_cursor_from_before_q5_is_read_in_full_once(ns, zoho) -> None:
    from gateway.crm_sync import engine

    a, _b = zoho
    _seed(a)
    a.add("Leads", "l1", 4, Last_Name="Open", Company="Acme", Lead_Status="Fresh")
    await _connect(ns, ns.org_a, a)
    assert (await engine.run_org(ns.org_a)).status == "ok"
    # Make the cursor look like one that an older build wrote, with a lead that
    # was converted long ago and so is older than the watermark.
    with ns.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE crm_sync_cursors SET cursor_state = cursor_state - 'converted_both' "
            "WHERE organization_id = CAST(:o AS uuid) AND module = 'zoho:lead'"),
            {"o": ns.org_a})
    _converted(a)
    a.data["Leads"][-1]["Modified_Time"] = stamp(-60)

    result = await engine.run_org(ns.org_a)
    assert result.status == "ok"
    assert _count(ns, "crm_leads", ns.org_a, "converted_at IS NOT NULL") == 1


# ── Tasks ────────────────────────────────────────────────────────────────────


async def test_tasks_land_as_linked_activities_and_a_deleted_task_is_removed(ns, zoho) -> None:
    from gateway.crm_sync import engine

    a, _b = zoho
    _seed(a)
    a.add("Deals", "d1", 4, Deal_Name="Deal", Stage="Qualify")
    a.add("Tasks", "t1", 10, Subject="Call back", Due_Date="2026-10-20",
          Status="Not Started", Priority="High", Description="Ring them",
          Who_Id={"id": "c1", "name": "x"}, What_Id={"id": "d1", "name": "x"},
          **{"$se_module": "Deals"})
    a.add("Tasks", "t2", 11, Subject="Done thing", Due_Date="2026-10-01",
          Status="Completed", Closed_Time=stamp(12), What_Id={"id": "d1"},
          **{"$se_module": "Deals"})
    a.add("Tasks", "t3", 12, Subject="Orphan", Status="Not Started")
    await _connect(ns, ns.org_a, a)

    first = await engine.run_org(ns.org_a)
    assert first.status == "ok"
    rows = {r[0]: r for r in _q(
        ns,
        "SELECT a.ext_id, a.type, a.subject, a.due_at, a.completed_at, a.custom, "
        "d.ext_id, c.ext_id FROM crm_activities a "
        "LEFT JOIN crm_deals d ON d.id = a.deal_id "
        "LEFT JOIN crm_contacts c ON c.id = a.contact_id "
        "WHERE a.organization_id = CAST(:o AS uuid) AND a.type = 'task'",
        o=ns.org_a)}
    assert set(rows) == {"t1", "t2"}, "a task with no parent record is skipped"
    t1 = rows["t1"]
    assert t1[1:3] == ("task", "Call back")
    assert t1[3] == datetime(2026, 10, 20, tzinfo=UTC) and t1[4] is None
    assert t1[5]["Priority"] == "High" and t1[5]["Status"] == "Not Started"
    assert (t1[6], t1[7]) == ("d1", "c1")
    assert rows["t2"][4] is not None, "a completed task has completed_at"
    step = first.counts["tasks"]
    assert (step["seen"], step["upserted"], step["skipped"]) == (3, 2, 1)
    assert step["unavailable"] == 0

    a.drop("Tasks", "t1", tombstone=True)
    second = await engine.run_org(ns.org_a)
    assert (second.status, second.kind) == ("ok", "incremental")
    assert _count(ns, "crm_activities", ns.org_a, "ext_id = 't1'") == 0
    assert second.counts["tasks"]["deleted"] == 1
    assert _count(ns, "crm_activities", ns.org_a, "type = 'task'") == 1


async def test_a_missing_tasks_scope_skips_the_module_and_the_run_still_succeeds(
    ns, zoho,
) -> None:
    from gateway.crm_sync import engine

    a, _b = zoho
    _seed(a)
    a.add("Deals", "d1", 4, Deal_Name="Deal", Stage="Qualify")
    a.add("Tasks", "t1", 10, Subject="Call back", What_Id={"id": "d1"},
          **{"$se_module": "Deals"})
    a.scope_denied.add("Tasks")
    await _connect(ns, ns.org_a, a)

    first = await engine.run_org(ns.org_a)
    assert (first.status, first.error) == ("ok", None)
    assert first.counts["tasks"]["unavailable"] == 1
    assert _count(ns, "crm_companies", ns.org_a) == 1
    assert _count(ns, "crm_activities", ns.org_a, "type = 'task'") == 0
    status = _q(ns, "SELECT status FROM integration_connections WHERE "
                    "organization_id = CAST(:o AS uuid)", o=ns.org_a)
    assert [r[0] for r in status] == ["connected"], "a missing scope is not a dead token"

    # The plan is not stuck at import, and the module is not asked again at once.
    calls_before = sum(1 for r in a.requests if r.url.path.endswith("/Tasks"))
    second = await engine.run_org(ns.org_a)
    assert (second.status, second.kind) == ("ok", "incremental")
    assert second.counts["tasks"]["unavailable"] == 1
    assert sum(1 for r in a.requests if r.url.path.endswith("/Tasks")) == calls_before

    # The admin reconnects and grants the scope. The next ask, after the hour, reads.
    a.scope_denied.clear()
    with ns.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE crm_sync_cursors SET cursor_state = jsonb_set(cursor_state, "
            "'{checked_at}', to_jsonb((now() - interval '2 hours')::text)) "
            "WHERE organization_id = CAST(:o AS uuid) AND module = 'zoho:task'"),
            {"o": ns.org_a})
    third = await engine.run_org(ns.org_a)
    assert third.status == "ok"
    assert third.counts["tasks"]["unavailable"] == 0
    assert _count(ns, "crm_activities", ns.org_a, "type = 'task'") == 1


async def test_a_scope_error_on_a_core_module_still_fails_the_run(ns, zoho) -> None:
    from gateway.crm_sync import engine

    a, _b = zoho
    _seed(a)
    a.scope_denied.add("Contacts")
    await _connect(ns, ns.org_a, a)
    result = await engine.run_org(ns.org_a)
    assert result.status == "failed", "only an optional module is skipped"


# ── Deal contact roles ───────────────────────────────────────────────────────


def _roles(p: Any, org: str) -> dict[str, str | None]:
    return {
        r[0]: r[1] for r in _q(
            p,
            "SELECT c.ext_id, dc.role FROM crm_deal_contacts dc "
            "JOIN crm_contacts c ON c.id = dc.contact_id "
            "WHERE dc.organization_id = CAST(:o AS uuid)", o=org)
    }


async def test_contact_roles_fill_and_a_role_removed_upstream_is_removed(ns, zoho) -> None:
    from gateway.crm_sync import engine

    a, _b = zoho
    _seed(a)
    a.add("Deals", "d1", 4, Deal_Name="Deal", Stage="Qualify",
          Contact_Name={"id": "c1", "name": "x"})
    a.roles["d1"] = [
        {"id": "c1", "Contact_Role": "Decision Maker"},
        {"id": "c2", "Contact_Role": {"name": "Influencer"}},
        {"id": "missing", "Contact_Role": "Ghost"},
    ]
    await _connect(ns, ns.org_a, a)

    first = await engine.run_org(ns.org_a)
    assert first.status == "ok"
    assert a.role_calls == ["d1"]
    assert _roles(ns, ns.org_a) == {"c1": "Decision Maker", "c2": "Influencer"}
    step = first.counts["deal_contacts"]
    assert (step["seen"], step["upserted"], step["unlinked"], step["skipped"]) == (1, 2, 1, 0)

    # An unchanged deal costs no call. A changed deal reads its roles again.
    quiet = await engine.run_org(ns.org_a)
    assert quiet.status == "ok" and a.role_calls == ["d1"]
    a.roles["d1"] = [{"id": "c1", "Contact_Role": "Champion"}]
    a.data["Deals"][0]["Modified_Time"] = stamp(30)
    third = await engine.run_org(ns.org_a)
    assert third.status == "ok" and a.role_calls == ["d1", "d1"]
    assert _roles(ns, ns.org_a) == {"c1": "Champion"}
    assert third.counts["deal_contacts"]["deleted"] == 1


async def test_a_low_budget_leaves_the_deals_queued_and_counts_them_skipped(
    ns, zoho, monkeypatch,
) -> None:
    from gateway.crm_sync import engine

    a, _b = zoho
    _seed(a)
    for i in (1, 2, 3):
        a.add("Deals", f"d{i}", 3 + i, Deal_Name=f"Deal {i}", Stage="Qualify")
        a.roles[f"d{i}"] = [{"id": "c1", "Contact_Role": f"Role {i}"}]
    await _connect(ns, ns.org_a, a)

    monkeypatch.setattr(engine, "ROLES_RESERVE", 10**9)  # the budget is always low
    low = await engine.run_org(ns.org_a)
    assert low.status == "ok"
    assert a.role_calls == [] and _roles(ns, ns.org_a) == {}
    assert low.counts["deal_contacts"]["skipped"] == 3

    monkeypatch.setattr(engine, "ROLES_RESERVE", 20)
    later = await engine.run_org(ns.org_a)
    assert later.status == "ok"
    assert sorted(a.role_calls) == ["d1", "d2", "d3"]
    assert later.counts["deal_contacts"]["seen"] == 3
    assert later.counts["deal_contacts"]["skipped"] == 0
    assert _count(ns, "crm_deal_contacts", ns.org_a) == 3


# ── Two organizations, the same Zoho ids ─────────────────────────────────────


async def test_two_orgs_with_the_same_zoho_ids_never_cross(ns, zoho) -> None:
    from gateway.crm_sync import engine

    a, b = zoho
    for z in (a, b):
        _seed(z)
        z.add("Deals", "d1", 4, Deal_Name=f"{z.tag}-Deal", Stage="Qualify")
        _converted(z)
        z.add("Tasks", "t1", 10, Subject=f"{z.tag}-Task", What_Id={"id": "d1"},
              **{"$se_module": "Deals"})
        z.roles["d1"] = [{"id": "c1", "Contact_Role": f"{z.tag}-Role"}]
    await _connect(ns, ns.org_a, a)
    await _connect(ns, ns.org_b, b)

    for org in (ns.org_a, ns.org_b):
        assert (await engine.run_org(org)).status == "ok"

    for org, z in ((ns.org_a, a), (ns.org_b, b)):
        assert _roles(ns, org) == {"c1": f"{z.tag}-Role"}
        subjects = [r[0] for r in _q(
            ns, "SELECT subject FROM crm_activities WHERE organization_id = "
                "CAST(:o AS uuid) AND type = 'task'", o=org)]
        assert subjects == [f"{z.tag}-Task"]
        links = _lead_links(ns, org)
        assert [(r[0], r[2], r[3], r[4]) for r in links] == [("l2", "c1", "a1", "d1")]
        names = [r[0] for r in _q(
            ns, "SELECT c.first_name FROM crm_leads l JOIN crm_contacts c "
                "ON c.id = l.converted_contact_id WHERE l.organization_id = "
                "CAST(:o AS uuid)", o=org)]
        assert names == [f"{z.tag}-One"]
    # Each Zoho was asked only for its own deal.
    assert a.role_calls == ["d1"] and b.role_calls == ["d1"]

    # A task deleted in A leaves B alone.
    a.drop("Tasks", "t1", tombstone=True)
    assert (await engine.run_org(ns.org_a)).status == "ok"
    assert _count(ns, "crm_activities", ns.org_a, "type = 'task'") == 0
    assert _count(ns, "crm_activities", ns.org_b, "type = 'task'") == 1


# ── Review fixes ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("status,body", [
    (403, {"code": "NO_PERMISSION", "message": "no permission to access this module"}),
    (400, {"code": "INVALID_MODULE", "message": "the module name given seems to be invalid"}),
    (500, {"code": "INTERNAL_ERROR", "message": "boom"}),
])
async def test_any_failure_to_read_tasks_skips_it_and_the_plan_leaves_import(
    ns, zoho, status, body,
) -> None:
    from gateway.crm_sync import engine

    a, _b = zoho
    _seed(a)
    a.faults["Tasks"] = (status, body)
    await _connect(ns, ns.org_a, a)

    first = await engine.run_org(ns.org_a)
    assert (first.status, first.error) == ("ok", None)
    assert first.counts["tasks"]["unavailable"] == 1
    a.drop("Contacts", "c2", tombstone=True)
    second = await engine.run_org(ns.org_a)
    assert (second.status, second.kind) == ("ok", "incremental")
    assert _count(ns, "crm_contacts", ns.org_a, "ext_id = 'c2'") == 0, "deletes still run"


async def test_a_lead_with_no_deal_is_hidden_by_default_and_shown_on_request(ns, zoho) -> None:
    from gateway.crm_sync import engine

    a, _b = zoho
    _seed(a)
    a.add("Leads", "l1", 4, Last_Name="Open", Company="Acme", Lead_Status="Fresh")
    a.add("Leads", "l2", 5, Last_Name="Gone", Company="Acme", Lead_Status="Qualified",
          Converted__s=True, Converted_Date_Time=stamp(6),
          Converted_Contact={"id": "c1"})  # converted, and no deal at all
    await _connect(ns, ns.org_a, a)
    assert (await engine.run_org(ns.org_a)).status == "ok"

    admin = _admin("admin@crm-sync.test", ns.org_a)
    shown = await _call(admin, "GET", "/crm/leads")
    assert shown.status_code == 200
    assert [r["ext_id"] for r in shown.json()["rows"]] == ["l1"]
    both = await _call(admin, "GET", "/crm/leads?include_converted=true")
    assert sorted(r["ext_id"] for r in both.json()["rows"]) == ["l1", "l2"]


async def test_the_roles_queue_is_seeded_once_from_the_mirrored_deals(ns, zoho) -> None:
    from gateway.crm_sync import engine

    a, _b = zoho
    _seed(a)
    for i in (1, 2):
        a.add("Deals", f"d{i}", 3 + i, Deal_Name=f"Deal {i}", Stage="Qualify")
        a.roles[f"d{i}"] = [{"id": "c1", "Contact_Role": "R"}]
    await _connect(ns, ns.org_a, a)
    assert (await engine.run_org(ns.org_a)).status == "ok"
    assert sorted(a.role_calls) == ["d1", "d2"]
    # A mirror that was built before the roles step: no queue row at all.
    with ns.admin_engine.begin() as c:
        c.execute(text("DELETE FROM crm_sync_cursors WHERE organization_id = "
                       "CAST(:o AS uuid) AND module = 'zoho:deal_roles'"), {"o": ns.org_a})
    assert (await engine.run_org(ns.org_a)).status == "ok"
    assert sorted(a.role_calls) == ["d1", "d1", "d2", "d2"], "seeded and drained"
    assert (await engine.run_org(ns.org_a)).status == "ok"
    assert len(a.role_calls) == 4, "the flag stops a second seeding"


async def test_a_native_link_on_a_mirrored_deal_survives_a_roles_sync(ns, zoho) -> None:
    from gateway.crm_sync import engine

    a, _b = zoho
    _seed(a)
    a.add("Deals", "d1", 4, Deal_Name="Deal", Stage="Qualify")
    a.roles["d1"] = [{"id": "c1", "Contact_Role": "Boss"}]
    await _connect(ns, ns.org_a, a)
    assert (await engine.run_org(ns.org_a)).status == "ok"
    with ns.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO crm_deal_contacts (deal_id, contact_id, role, organization_id) "
            "SELECT d.id, ct.id, 'Mine', d.organization_id FROM crm_deals d, crm_contacts ct "
            "WHERE d.ext_id = 'd1' AND ct.ext_id = 'c2' AND d.organization_id = "
            "CAST(:o AS uuid) AND ct.organization_id = d.organization_id"), {"o": ns.org_a})
    a.roles["d1"] = [{"id": "c1", "Contact_Role": "Boss 2"}]
    a.data["Deals"][0]["Modified_Time"] = stamp(30)
    assert (await engine.run_org(ns.org_a)).status == "ok"
    assert _roles(ns, ns.org_a) == {"c1": "Boss 2", "c2": "Mine"}


async def test_a_failed_roles_read_requeues_the_deal_and_drops_it_after_three(ns, zoho) -> None:
    from gateway.crm_sync import engine

    a, _b = zoho
    _seed(a)
    a.add("Deals", "d1", 4, Deal_Name="Deal", Stage="Qualify")
    a.roles["d1"] = [{"id": "c1", "Contact_Role": "R"}]
    a.roles_fault = (503, {"Retry-After": "0"})
    await _connect(ns, ns.org_a, a)

    def queue() -> dict[str, Any]:
        return _q(ns, "SELECT cursor_state FROM crm_sync_cursors WHERE organization_id = "
                      "CAST(:o AS uuid) AND module = 'zoho:deal_roles'", o=ns.org_a)[0][0]

    for tries in (1, 2):
        assert (await engine.run_org(ns.org_a)).status == "ok"
        assert queue()["pending"] == ["d1"] and queue()["attempts"] == {"d1": tries}
    assert (await engine.run_org(ns.org_a)).status == "ok"
    assert queue()["pending"] == [], "the third failure drops the deal"
    a.roles_fault = None
    assert (await engine.run_org(ns.org_a)).status == "ok"
    assert _roles(ns, ns.org_a) == {}, "a dropped deal is read again only when it changes"


async def test_the_one_time_lead_re_read_does_not_repeat(ns, zoho) -> None:
    from gateway.crm_sync import engine

    a, _b = zoho
    _seed(a)
    a.add("Leads", "l1", 4, Last_Name="Open", Company="Acme", Lead_Status="Fresh")
    await _connect(ns, ns.org_a, a)
    assert (await engine.run_org(ns.org_a)).status == "ok"
    with ns.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE crm_sync_cursors SET cursor_state = cursor_state - 'converted_both' "
            "WHERE organization_id = CAST(:o AS uuid) AND module = 'zoho:lead'"),
            {"o": ns.org_a})
    a.lead_since.clear()
    assert (await engine.run_org(ns.org_a)).status == "ok"
    assert a.lead_since[0] is None, "the first run after a build older than Q5 reads in full"
    a.lead_since.clear()
    assert (await engine.run_org(ns.org_a)).status == "ok"
    assert a.lead_since and a.lead_since[0] is not None, "the second run is incremental"
