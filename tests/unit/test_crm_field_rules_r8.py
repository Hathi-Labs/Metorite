"""R8 — the field rules of the mirror, against a real Postgres (WS-53 CRM-Q7a,
``crm_platform.md`` §18.7).

Migration 253 adds ``crm_field_rules``. An admin decides a rule in CRM settings
→ Field map, through ``/crm/field-rules``. The routes write that table only.
The sync applies the decision: ``map_record`` applies an approved rule after
the fixed rules, the next run backfills the rows that are there, and a
rejected or stale rule is undone.

What this proves, with two organizations and the non-privileged role of the
phase-4 rehearsal (no superuser, no BYPASSRLS), one test for each "Done when"
item of §18.7:

1. an approved rule makes the next sync write the column;
2. the next run backfills the rows that are there, with no Zoho call, and a
   value that does not coerce gives NULL while the run still succeeds;
3. a rule on a fixed column whose standard field is on a layout is a 422, a
   type mismatch is a 422, and the same column with the standard field off
   every layout is legal;
4. a member who is not an admin gets 403;
5. org B sees none of org A's rules;
6. a change in the profile makes a rule ``stale``, and the sync stops
   applying it;
7. two syncs of the same data give the same rows;
8. a record whose standard field holds a value keeps that value;
9. a reject makes the next run read the entity in full once, each row then
   holds the fixed map's value again, and the value stays in ``custom``;
10. a reject of a rule on a free column sets that column to NULL;
11. a second approved rule for the same column is a 409.

``tests/unit/test_crm_field_rules.py`` holds the rules that need no database.
"""

# ruff: noqa: F811 - the fixtures come in by import and are used as arguments
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import httpx
import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.pool import NullPool

from tests.unit._tenant_ladder import _exec_file
from tests.unit.test_crm_pipelines_r8 import Q1Zoho, _req
from tests.unit.test_crm_pipelines_r8 import _admin as _admin_of
from tests.unit.test_crm_pipelines_r8 import _member as _member_of
from tests.unit.test_crm_sync_engine_r8 import (  # noqa: F401
    _assert_non_priv,
    _connect,
    _count,
    _q,
    _the_loop_is_not_stopping,
    ns,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

# ⚠️ Every test here needs the real database. Without this gate the suite
# ERRORS where no ladder URL is set (the deploy run), and the deploy stops.
pytestmark = _DB_GATE

_MIGRATIONS = Path(__file__).resolve().parents[2] / "infra" / "postgres"

#: The custom fields of each module, with a standard field or two that the
#: fixed map consumes (``map_field_def`` writes no row for those).
FIELDS: dict[str, list[dict[str, Any]]] = {
    "Leads": [
        {"api_name": "Custom_Industry", "field_label": "Sector", "data_type": "picklist",
         "pick_list_values": [{"actual_value": "Robotics"}, {"actual_value": "Drones"}]},
        {"api_name": "Headcount", "field_label": "Headcount", "data_type": "integer"},
        {"api_name": "Why_Lost", "field_label": "Why lost", "data_type": "textarea"},
        {"api_name": "Next_Call", "field_label": "Next call", "data_type": "date"},
        {"api_name": "Tags_X", "field_label": "Tags", "data_type": "multiselectpicklist"},
        {"api_name": "Industry", "field_label": "Industry", "data_type": "picklist"},
        {"api_name": "Email", "field_label": "Email", "data_type": "email"},
    ],
    "Contacts": [
        {"api_name": "LinkedIn", "field_label": "LinkedIn", "data_type": "website"},
    ],
    "Accounts": [
        {"api_name": "Region", "field_label": "Region", "data_type": "text"},
    ],
    "Deals": [
        {"api_name": "Loss_Story", "field_label": "Loss story", "data_type": "textarea"},
    ],
}

#: The fields on the one active layout of each module. Industry and
#: No_of_Employees are off every Leads layout, so their columns are free.
LAYOUT: dict[str, list[str]] = {
    "Leads": ["Last_Name", "Company", "Email", "Custom_Industry", "Headcount", "Why_Lost"],
    "Contacts": ["Last_Name", "Email", "LinkedIn"],
    "Accounts": ["Account_Name", "Website"],
    "Deals": ["Deal_Name", "Stage", "Closing_Date"],
}


class RuleZoho(Q1Zoho):
    """The fake Zoho of CRM-Q1, with layouts that list their fields and custom
    fields of each type."""

    def __init__(self, tag: str, api_host: str, accounts_host: str) -> None:
        super().__init__(tag, api_host, accounts_host)
        self.layout = {m: list(names) for m, names in LAYOUT.items()}
        self.fields = {m: [dict(f) for f in rows] for m, rows in FIELDS.items()}

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/crm/v8/settings/layouts":
            self.requests.append(request)
            module = request.url.params.get("module", "")
            return httpx.Response(200, json={"layouts": [{
                "id": f"LD-{module}", "name": "Standard", "status": "active",
                "sections": [{"fields": [
                    {"api_name": name} for name in self.layout.get(module, [])
                ]}],
            }]})
        return super().handle(request)

    def _fields(self, module: str) -> list[dict[str, Any]]:
        base = [f for f in super()._fields(module) if f["api_name"] != "Region"]
        return base + [dict(f) for f in self.fields.get(module, [])]

    def lead_reads(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path == "/crm/v2/Leads"]

    def contact_reads(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path == "/crm/v2/Contacts"]


@pytest.fixture
def fr(ns, monkeypatch):
    """Two fake Zoho tenants under every provider call, and the CRM-Q1 and
    CRM-Q7a tables cleaned after the test."""
    from gateway.crm_sync import engine

    a = RuleZoho("A", "www.zohoapis.in", "accounts.zoho.in")
    b = RuleZoho("B", "www.zohoapis.eu", "accounts.zoho.eu")

    def transport(request: httpx.Request) -> httpx.Response:
        for z in (a, b):
            if request.url.host in (z.api_host, z.accounts_host):
                return z.handle(request)
        raise AssertionError(f"a call to an unknown host {request.url.host}")

    monkeypatch.setattr(engine, "TRANSPORT", httpx.MockTransport(transport))
    yield ns, a, b
    with ns.admin_engine.begin() as c:
        for org in (ns.org_a, ns.org_b):
            for table in (
                "crm_field_rules", "crm_pipeline_stages", "crm_pipelines",
                "crm_source_profiles",
            ):
                c.execute(text(
                    f"DELETE FROM {table} WHERE organization_id = CAST(:o AS uuid)"),
                    {"o": org})


def _seed(z: RuleZoho) -> None:
    z.add("Accounts", "a1", 1, Account_Name=f"{z.tag}-Company")
    z.add("Contacts", "c1", 2, Last_Name=f"{z.tag}-Ann",
          LinkedIn=f"https://linkedin.example/{z.tag}-ann")
    z.add("Leads", "l1", 3, Last_Name="One", Company="Acme",
          Custom_Industry="Robotics", Headcount="lots", Why_Lost="Price")
    # l2 holds a value in the standard field too (done when 8).
    z.add("Leads", "l2", 4, Last_Name="Two", Company="Beta", Industry="Standard",
          Custom_Industry="Drones", Headcount=40)
    z.add("Deals", "d1", 5, Deal_Name=f"{z.tag}-Deal", Stage="Qualify",
          Loss_Story="Too dear")


async def _run(org: str, **kw: Any) -> Any:
    from gateway.crm_sync import engine

    return await engine.run_org(org, **kw)


async def _imported(p: Any, org: str, z: RuleZoho) -> None:
    """Seed, connect and run the first import."""
    _seed(z)
    await _connect(p, org, z)
    first = await _run(org)
    assert (first.status, first.error) == ("ok", None)


async def _post(org: str, field: str, column: str, entity: str = "leads") -> Any:
    return await _req(_admin_of(org), "POST", "/crm/field-rules", {
        "entity": entity, "field_api_name": field, "target_column": column,
    })


async def _patch(org: str, rule_id: str, status: str) -> Any:
    return await _req(_admin_of(org), "PATCH", f"/crm/field-rules/{rule_id}",
                      {"status": status})


def _leads(p: Any, org: str) -> dict[str, Any]:
    rows = _q(p, "SELECT ext_id, industry, no_of_employees, lost_note, custom, "
                 "organization_name, lead_name FROM crm_leads "
                 "WHERE organization_id = CAST(:o AS uuid) ORDER BY ext_id", o=org)
    return {r[0]: r for r in rows}


def _age_profile(p: Any, org: str) -> None:
    """Make the next run read the profile again."""
    with p.admin_engine.begin() as c:
        c.execute(text("UPDATE crm_source_profiles SET fetched_at = now() - "
                       "interval '25 hours' WHERE organization_id = CAST(:o AS uuid)"),
                  {"o": org})


def _rule_status(p: Any, rule_id: str) -> str:
    return _q(p, "SELECT status FROM crm_field_rules WHERE id = CAST(:i AS uuid)",
              i=rule_id)[0][0]


# ── 1. An approved rule makes the next sync write the column ─────────────────


async def test_an_approved_rule_makes_the_next_sync_write_the_column(fr) -> None:
    p, a, _b = fr
    await _imported(p, p.org_a, a)
    assert _leads(p, p.org_a)["l1"][1] is None, "no rule, no value"

    made = await _post(p.org_a, "Custom_Industry", "industry")
    assert made.status_code == 201, made.text
    body = made.json()
    assert (body["status"], body["proposed_by"], body["field_type"]) == (
        "approved", "admin", "select")
    assert body["decided_by"] == "admin@crm-sync.test" and body["decided_at"]
    assert body["profile_version"] >= 1
    # The route wrote no CRM record.
    assert _leads(p, p.org_a)["l1"][1] is None

    a.add("Leads", "l3", 30, Last_Name="Three", Custom_Industry="Space")
    second = await _run(p.org_a)
    assert second.status == "ok"
    leads = _leads(p, p.org_a)
    assert leads["l3"][1] == "Space", "map_record applied the rule"
    assert leads["l3"][4]["Custom_Industry"] == "Space", "the value stays in custom"
    assert leads["l1"][1] == "Robotics", "the backfill filled the row that was there"


# ── 2. The backfill: no Zoho call, and a value that does not coerce ──────────


async def test_the_backfill_makes_no_zoho_call_and_a_bad_value_gives_null(fr) -> None:
    p, a, _b = fr
    await _imported(p, p.org_a, a)
    for field, column in (("Custom_Industry", "industry"), ("Headcount", "no_of_employees")):
        made = await _post(p.org_a, field, column)
        assert made.status_code == 201, made.text

    before = len(a.requests)
    # A run with no credit stops at its first provider call. The backfill runs
    # before it, so the rows are filled with no call at all.
    starved = await _run(p.org_a, credit_budget=0)
    assert (starved.status, starved.error) == ("stopped", "budget")
    assert len(a.requests) == before, "the backfill made a Zoho call"
    leads = _leads(p, p.org_a)
    assert leads["l1"][1] == "Robotics"
    assert leads["l1"][2] is None, "'lots' does not coerce to INT"
    assert leads["l2"][2] == 40
    assert leads["l1"][4]["Headcount"] == "lots", "custom keeps the raw value"

    # The next full run succeeds, and keeps the NULL.
    again = await _run(p.org_a)
    assert (again.status, again.error) == ("ok", None)
    assert _leads(p, p.org_a)["l1"][2] is None
    state = _q(p, "SELECT cursor_state FROM crm_sync_cursors WHERE organization_id = "
                  "CAST(:o AS uuid) AND module = 'zoho:field_rules'", o=p.org_a)[0][0]
    assert {r["column"] for r in state["applied"]} == {"industry", "no_of_employees"}


# ── 3. What is legal ─────────────────────────────────────────────────────────


async def test_a_fixed_column_needs_its_standard_field_off_every_layout(fr) -> None:
    p, a, _b = fr
    await _imported(p, p.org_a, a)

    on_layout = await _post(p.org_a, "Custom_Industry", "email")
    assert on_layout.status_code == 422, on_layout.text
    assert on_layout.json()["detail"]["code"] == "standard_field_on_layout"

    mismatch = await _post(p.org_a, "Next_Call", "industry")
    assert mismatch.status_code == 422
    assert mismatch.json()["detail"]["code"] == "type_mismatch"
    multi = await _post(p.org_a, "Tags_X", "lost_note")
    assert multi.status_code == 422
    assert multi.json()["detail"]["code"] == "type_mismatch"
    unknown = await _post(p.org_a, "Custom_Industry", "lead_name")
    assert unknown.status_code == 422
    assert unknown.json()["detail"]["code"] == "unknown_target"
    no_field = await _post(p.org_a, "Not_A_Field", "industry")
    assert no_field.status_code == 422
    standard = await _post(p.org_a, "Industry", "industry")
    assert standard.status_code == 422, "a standard field does not go to custom"
    assert _count(p, "crm_field_rules", p.org_a) == 0, "a refused rule writes no row"

    legal = await _post(p.org_a, "Custom_Industry", "industry")
    assert legal.status_code == 201, legal.text

    view = await _req(_admin_of(p.org_a), "GET", "/crm/field-rules?entity=leads")
    assert view.status_code == 200, view.text
    got = view.json()
    assert got["entity"] == "lead"
    assert {f["key"] for f in got["fields"]} == {
        "Custom_Industry", "Headcount", "Why_Lost", "Next_Call", "Tags_X"}
    targets = {t["column"]: t for t in got["targets"]}
    assert targets["industry"]["open"] is True
    assert targets["email"]["open"] is False
    assert targets["email"]["reason"] == "standard_field_on_layout"
    assert targets["lost_note"]["kind"] == "free" and targets["lost_note"]["open"]
    # Each field names the columns it may fill now, so the client holds no
    # copy of the rules.
    by_key = {f["key"]: f for f in got["fields"]}
    assert set(by_key["Custom_Industry"]["targets"]) == {
        "lost_note", "website", "industry", "phone", "mobile", "lead_source",
        "description"}, "Company and Email are on the layout"
    assert set(by_key["Headcount"]["targets"]) == {"no_of_employees", "annual_revenue"}
    assert by_key["Next_Call"]["targets"] == [] and by_key["Tags_X"]["targets"] == []
    assert [r["field_api_name"] for r in got["rules"]] == ["Custom_Industry"]


# ── 4. Admin only, Mirror only, dark behind the switch ───────────────────────


async def test_a_member_who_is_not_an_admin_gets_403(fr, monkeypatch) -> None:
    p, a, _b = fr
    await _imported(p, p.org_a, a)
    made = await _post(p.org_a, "Custom_Industry", "industry")
    rule_id = made.json()["id"]
    member = _member_of(p.org_a)
    # Every member may open this CRM, so the 403 below comes from the admin
    # guard of the Field map and not from the access rule of the router.
    with p.admin_engine.begin() as c:
        c.execute(text("UPDATE crm_settings SET access = 'everyone' "
                       "WHERE organization_id = CAST(:o AS uuid)"), {"o": p.org_a})
    assert (await _req(member, "GET", "/crm/fields/leads")).status_code == 200
    assert (await _req(member, "GET", "/crm/field-rules?entity=leads")).status_code == 403
    assert (await _req(member, "POST", "/crm/field-rules", {
        "entity": "leads", "field_api_name": "Why_Lost", "target_column": "lost_note",
    })).status_code == 403
    assert (await _req(member, "PATCH", f"/crm/field-rules/{rule_id}",
                       {"status": "rejected"})).status_code == 403
    assert _rule_status(p, rule_id) == "approved"

    # Native mode: the Field map is for a mirror.
    with p.admin_engine.begin() as c:
        c.execute(text("UPDATE crm_settings SET mode = 'native' "
                       "WHERE organization_id = CAST(:o AS uuid)"), {"o": p.org_a})
    native = await _req(_admin_of(p.org_a), "GET", "/crm/field-rules?entity=leads")
    assert native.status_code == 409 and native.json()["detail"]["code"] == "not_mirror"

    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "crm_mirror", False, raising=False)
    dark = await _req(_admin_of(p.org_a), "GET", "/crm/field-rules?entity=leads")
    assert dark.status_code == 404


# ── 5. Org isolation ─────────────────────────────────────────────────────────


async def test_org_b_sees_none_of_org_a_rules(fr, app_engine) -> None:
    p, a, b = fr
    _assert_non_priv(app_engine)
    await _imported(p, p.org_a, a)
    await _imported(p, p.org_b, b)
    made = await _post(p.org_a, "Custom_Industry", "industry")
    rule_id = made.json()["id"]

    seen_b = await _req(_admin_of(p.org_b), "GET", "/crm/field-rules?entity=leads")
    assert seen_b.status_code == 200 and seen_b.json()["rules"] == []
    patch_b = await _req(_admin_of(p.org_b), "PATCH", f"/crm/field-rules/{rule_id}",
                         {"status": "rejected"})
    assert patch_b.status_code == 404
    assert _rule_status(p, rule_id) == "approved"

    def rules_as(org: str | None) -> list[str]:
        fresh = create_engine(p.app_url, poolclass=NullPool)
        try:
            with fresh.begin() as c:
                if org is not None:
                    c.execute(text("SELECT set_config('app.tenant_id', :o, true)"),
                              {"o": org})
                return [str(r[0]) for r in c.execute(text(
                    "SELECT id FROM crm_field_rules")).all()]
        finally:
            fresh.dispose()

    assert rules_as(p.org_a) == [rule_id]
    assert rules_as(p.org_b) == []
    assert rules_as(None) == [], "no tenant bound reads nothing"
    with app_engine.connect() as c, pytest.raises(DBAPIError):
        c.execute(text("SELECT set_config('app.tenant_id', :o, false)"), {"o": p.org_b})
        c.execute(text(
            "INSERT INTO crm_field_rules (organization_id, ext_source, entity, "
            "field_api_name, field_type, profile_version, target_column, status, "
            "proposed_by) VALUES (CAST(:o AS uuid), 'zoho', 'lead', 'X', 'text', 1, "
            "'industry', 'approved', 'admin')"), {"o": p.org_a})
    # B's sync applies none of A's rules.
    assert (await _run(p.org_b)).status == "ok"
    assert _leads(p, p.org_b)["l1"][1] is None


# ── 6. A change in the profile makes a rule stale ────────────────────────────


async def test_a_profile_change_makes_the_rule_stale_and_the_sync_stops_applying_it(
    fr,
) -> None:
    p, a, _b = fr
    await _imported(p, p.org_a, a)
    ids = {}
    for field, column in (
        ("Custom_Industry", "industry"), ("Headcount", "no_of_employees"),
        ("Why_Lost", "lost_note"),
    ):
        made = await _post(p.org_a, field, column)
        assert made.status_code == 201, made.text
        ids[column] = made.json()["id"]
    assert (await _run(p.org_a)).status == "ok"
    assert _leads(p, p.org_a)["l1"][1] == "Robotics"

    # The three cases: a type change, the standard field back on a layout, and
    # a field that is gone.
    sector = next(f for f in a.fields["Leads"] if f["api_name"] == "Custom_Industry")
    sector["data_type"] = "multiselectpicklist"
    a.layout["Leads"].append("No_of_Employees")
    a.fields["Leads"] = [f for f in a.fields["Leads"] if f["api_name"] != "Why_Lost"]
    _age_profile(p, p.org_a)
    a.add("Leads", "l3", 40, Last_Name="Three", Custom_Industry="Space", Headcount=9)
    third = await _run(p.org_a)
    assert third.status == "ok"
    assert {c: _rule_status(p, i) for c, i in ids.items()} == {
        "industry": "stale", "no_of_employees": "stale", "lost_note": "stale"}
    leads = _leads(p, p.org_a)
    assert leads["l3"][1] is None, "a stale rule is not applied"
    assert leads["l3"][2] is None
    view = await _req(_admin_of(p.org_a), "GET", "/crm/field-rules?entity=leads")
    assert {r["status"] for r in view.json()["rules"]} == {"stale"}

    # The next run undoes what the stale rules wrote: the free column goes to
    # NULL, and the fixed columns get the fixed map's value from a full read.
    fourth = await _run(p.org_a)
    assert fourth.status == "ok"
    leads = _leads(p, p.org_a)
    assert leads["l1"][1] is None and leads["l2"][1] == "Standard"
    assert leads["l2"][2] is None and leads["l1"][3] is None
    # An admin approves a stale rule again only when it is legal again.
    retry = await _patch(p.org_a, ids["industry"], "approved")
    assert retry.status_code == 422


# ── 7. Two syncs of the same data ────────────────────────────────────────────


async def test_two_syncs_of_the_same_data_give_the_same_rows(fr) -> None:
    p, a, _b = fr
    await _imported(p, p.org_a, a)
    for field, column in (("Custom_Industry", "industry"), ("Why_Lost", "lost_note")):
        assert (await _post(p.org_a, field, column)).status_code == 201
    assert (await _run(p.org_a)).status == "ok"
    first = _leads(p, p.org_a)

    # A full read of every lead again: the cursor forgets where it was.
    with p.admin_engine.begin() as c:
        c.execute(text("UPDATE crm_sync_cursors SET last_pulled_at = NULL, "
                       "cursor_state = cursor_state - 'watermark_ids' "
                       "WHERE organization_id = CAST(:o AS uuid) AND module = 'zoho:lead'"),
                  {"o": p.org_a})
    reads = len(a.lead_reads())
    assert (await _run(p.org_a)).status == "ok"
    assert len(a.lead_reads()) > reads
    assert _leads(p, p.org_a) == first
    assert first["l1"][1] == "Robotics" and first["l1"][3] == "Price"


# ── 8. A value from the standard field stays ─────────────────────────────────


async def test_a_value_from_the_standard_field_is_kept(fr) -> None:
    p, a, _b = fr
    await _imported(p, p.org_a, a)
    assert (await _post(p.org_a, "Custom_Industry", "industry")).status_code == 201
    assert (await _run(p.org_a)).status == "ok"
    leads = _leads(p, p.org_a)
    assert leads["l2"][1] == "Standard", "the rule never overwrites the fixed map"
    assert leads["l1"][1] == "Robotics", "the rule fills only the NULL row"
    # And a fresh read of l2 keeps it too.
    l2 = next(r for r in a.data["Leads"] if r["id"] == "l2")
    l2.update(Modified_Time="2026-01-01T02:00:00+00:00", Custom_Industry="Other")
    assert (await _run(p.org_a)).status == "ok"
    assert _leads(p, p.org_a)["l2"][1] == "Standard"


# ── 9. A reject reads the entity in full once ────────────────────────────────


async def test_a_reject_reads_the_entity_in_full_once_and_restores_the_fixed_value(
    fr,
) -> None:
    p, a, _b = fr
    await _imported(p, p.org_a, a)
    made = await _post(p.org_a, "Custom_Industry", "industry")
    rule_id = made.json()["id"]
    assert (await _run(p.org_a)).status == "ok"
    assert _leads(p, p.org_a)["l1"][1] == "Robotics"
    leads_before = _count(p, "crm_leads", p.org_a)

    rejected = await _patch(p.org_a, rule_id, "rejected")
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "rejected"
    assert _leads(p, p.org_a)["l1"][1] == "Robotics", "the route writes no record"

    reads = len(a.lead_reads())
    undo = await _run(p.org_a)
    assert undo.status == "ok"
    this_run = a.lead_reads()[reads:]
    full = [r for r in this_run if r.headers.get("If-Modified-Since") is None]
    assert len(full) == 1, "one full read of the leads"
    leads = _leads(p, p.org_a)
    assert leads["l1"][1] is None, "the fixed map's value again"
    assert leads["l2"][1] == "Standard"
    assert leads["l1"][4]["Custom_Industry"] == "Robotics", "the value stays in custom"
    assert _count(p, "crm_leads", p.org_a) == leads_before, "the read deletes nothing"
    cursor = _q(p, "SELECT cursor_state FROM crm_sync_cursors WHERE organization_id = "
                   "CAST(:o AS uuid) AND module = 'zoho:lead'", o=p.org_a)[0][0]
    assert "rule_reread" not in cursor

    # Once: the next run reads the changed leads only.
    reads = len(a.lead_reads())
    assert (await _run(p.org_a)).status == "ok"
    assert all(r.headers.get("If-Modified-Since") for r in a.lead_reads()[reads:])


# ── 10. A reject on a free column ────────────────────────────────────────────


async def test_a_reject_on_a_free_column_sets_it_to_null(fr) -> None:
    p, a, _b = fr
    await _imported(p, p.org_a, a)
    made = await _post(p.org_a, "LinkedIn", "linkedin_url", entity="contacts")
    assert made.status_code == 201, made.text
    assert (await _run(p.org_a)).status == "ok"

    def contact() -> Any:
        return _q(p, "SELECT linkedin_url, custom FROM crm_contacts WHERE "
                     "organization_id = CAST(:o AS uuid) AND ext_id = 'c1'", o=p.org_a)[0]

    assert contact()[0] == "https://linkedin.example/A-ann"
    assert (await _patch(p.org_a, made.json()["id"], "rejected")).status_code == 200
    reads = len(a.contact_reads())
    assert (await _run(p.org_a)).status == "ok"
    assert contact()[0] is None
    assert contact()[1]["LinkedIn"] == "https://linkedin.example/A-ann"
    assert all(r.headers.get("If-Modified-Since") for r in a.contact_reads()[reads:]), (
        "a free column needs no full read")


# ── 11. One approved rule for each column ────────────────────────────────────


async def test_a_second_approved_rule_for_the_same_column_gives_409(fr) -> None:
    p, a, _b = fr
    await _imported(p, p.org_a, a)
    assert (await _post(p.org_a, "Custom_Industry", "industry")).status_code == 201

    twice = await _post(p.org_a, "Why_Lost", "industry")
    assert twice.status_code == 409, twice.text
    assert twice.json()["detail"]["code"] == "rule_taken"

    # A proposal for the same column (the CRM-Q7b proposer writes these).
    with p.admin_engine.begin() as c:
        proposed = c.execute(text(
            "INSERT INTO crm_field_rules (organization_id, ext_source, entity, "
            "field_api_name, field_type, profile_version, target_column, status, "
            "proposed_by, confidence, reason) VALUES (CAST(:o AS uuid), 'zoho', 'lead', "
            "'Why_Lost', 'textarea', 1, 'industry', 'proposed', 'ai', 0.4, "
            "'A guess.') RETURNING id"), {"o": p.org_a}).scalar()
    approve = await _patch(p.org_a, str(proposed), "approved")
    assert approve.status_code == 409
    assert _rule_status(p, str(proposed)) == "proposed"
    # The partial unique index holds it under the routes too.
    with pytest.raises(DBAPIError), p.admin_engine.begin() as c:
        c.execute(text("UPDATE crm_field_rules SET status = 'approved' "
                       "WHERE id = CAST(:i AS uuid)"), {"i": str(proposed)})
    # A reject frees the column.
    first = _q(p, "SELECT id FROM crm_field_rules WHERE organization_id = CAST(:o AS uuid) "
                  "AND status = 'approved'", o=p.org_a)[0][0]
    assert (await _patch(p.org_a, str(first), "rejected")).status_code == 200
    now = await _patch(p.org_a, str(proposed), "approved")
    assert now.status_code == 200 and now.json()["status"] == "approved"
    assert now.json()["proposed_by"] == "ai", "an approve keeps who proposed it"


# ── The migration ────────────────────────────────────────────────────────────


def test_the_field_rules_migration_is_found_by_content_and_replays(fr) -> None:
    p, _a, _b = fr
    sql = re.compile(r"CREATE TABLE IF NOT EXISTS crm_field_rules")
    found = [f for f in _MIGRATIONS.glob("*.sql")
             if f.name != "schema.generated.sql"
             and sql.search(f.read_text(encoding="utf-8"))]
    assert len(found) == 1
    with p.admin_engine.begin() as c:
        _exec_file(c, str(found[0]))
    forced = _q(p, "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                   "WHERE oid = to_regclass('crm_field_rules')")
    assert forced == [(True, True)]
    policy = _q(p, "SELECT qual IS NOT NULL, with_check IS NOT NULL FROM pg_policies "
                   "WHERE tablename = 'crm_field_rules'")
    assert policy and all(row == (True, True) for row in policy)
    indexes = {r[0] for r in _q(p, "SELECT indexname FROM pg_indexes "
                                   "WHERE tablename = 'crm_field_rules'")}
    assert {"crm_field_rules_field_key", "crm_field_rules_one_approved"} <= indexes
