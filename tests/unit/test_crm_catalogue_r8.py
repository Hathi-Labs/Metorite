"""R8 — the CRM catalogue by code, through the real router (WS-53 CRM-Q31-1).

Spec: ``project-docs/specs/crm_platform.md`` §18.13.4 and the CRM-Q31-1
done-when list of §18.13.6.

Against a REAL Postgres, as the non-privileged role of the phase-4 rehearsal.
The seed goes in as the superuser, so each read below is the first thing the
app role does.

1. A Mirror org: one entry for each core entity and each mirrored module,
   the code rules on stored field definitions and fill rows, a role and a
   stored catalogue item of the approved plan (items 2 to 4, 9).
2. No value of ``crm_module_records.fields`` and no owner email reaches the
   body (item 5).
3. Native mode gives the four core entities from code. An org outside
   ``CRM_CATALOGUE_ORGS`` gets 404 ``catalogue_off`` in each mode (item 6).
4. Org B never reads the catalogue of org A, through the route or through
   ``entry_of``.
5. 25 modules and 1,300 field definitions answer 200 under 1 s (item 10).
"""

# ruff: noqa: F811 - the fixtures come in by import and are used as arguments
from __future__ import annotations

import json
import time
import uuid
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

# Without the gate the suite ERRORS where no ladder URL is set (deploy.yml).
pytestmark = _DB_GATE

_TABLES = (
    "crm_mapping_plans", "crm_field_fill", "crm_module_records", "crm_field_defs",
    "crm_source_profiles", "integration_connections", "crm_deals", "crm_leads",
    "crm_contacts", "crm_companies", "crm_pipeline_stages", "crm_pipelines",
    "crm_settings", "crm_deal_statuses", "crm_lead_statuses", "crm_lost_reasons",
)

#: Item 10: the bound of one answer, in seconds.
BOUND_S = 1.0

#: The marks of item 5. None of them may reach a body.
_MARK = "MARK-Q31-SECRET"
_OWNER_MARK = "mark-owner@q31-secret.test"


def _member(org: str) -> Any:
    from acb_auth import UserContext, UserRole, build_access

    return UserContext(
        email="member@crm-q31-r8.test", role=UserRole.EMPLOYEE,
        access=build_access(["feature:crm"]), organization_id=org,
    )


async def _call(p: Any, org: str, path: str = "/crm/catalogue") -> Any:
    """One request through the real router. The tenant comes from the user."""
    import httpx
    from acb_auth import get_current_user
    from acb_common.db import bind_tenant, clear_tenant, release_tenant
    from fastapi import FastAPI
    from gateway.routes import crm as crm_package

    user = _member(org)

    async def _caller() -> Any:
        bind_tenant(user.organization_id)
        return user

    app = FastAPI()
    app.include_router(crm_package.router)
    app.dependency_overrides[get_current_user] = _caller
    scope = clear_tenant()
    try:
        async with (
            tenant_engine_scope(p.app_url.render_as_string(hide_password=False)),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://crm.test",
            ) as client,
        ):
            return await client.get(path)
    finally:
        release_tenant(scope)


async def _entry_of(p: Any, org: str, module: str) -> Any:
    """``catalogue.entry_of`` as the app role, with *org* bound."""
    from acb_common.db import bind_tenant, clear_tenant, release_tenant
    from gateway.routes.crm.catalogue import entry_of
    from gateway.routes.crm.core import _tenant_session

    scope = clear_tenant()
    token = bind_tenant(org)
    try:
        async with (
            tenant_engine_scope(p.app_url.render_as_string(hide_password=False)),
            _tenant_session() as db,
        ):
            return await entry_of(db, org, module)
    finally:
        release_tenant(token)
        release_tenant(scope)


# ── The seed, as the superuser ────────────────────────────────────────────────


def _module(api: str, label: str | None = None, *, custom: bool = True,
            singular: str | None = None, display: str | None = "Name") -> dict:
    """One generic module row of a profile, in the shape ``_module_rows`` writes."""
    return {
        "api_name": api, "tab": api, "label": label or api,
        "singular_label": singular, "custom": custom,
        "generated_type": "custom" if custom else "default", "api_supported": True,
        "visible": True, "mirrored": True, "generic": True,
        "scope": "ZohoCRM.modules.custom.READ", "display_field": display,
    }


def _core(api: str, label: str) -> dict:
    return {"api_name": api, "tab": api, "label": label, "singular_label": label,
            "custom": False, "generated_type": "default", "api_supported": True,
            "visible": True, "mirrored": True}


def _mirror(p: Any, org: str, modules: list[dict]) -> None:
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO crm_settings (organization_id, mode, access) "
            "VALUES (CAST(:o AS uuid), 'mirror', 'everyone') "
            "ON CONFLICT (organization_id) DO UPDATE SET mode = 'mirror', "
            "access = 'everyone'"), {"o": org})
        conn = c.execute(text(
            "INSERT INTO integration_connections (organization_id, provider, "
            "owning_app) VALUES (CAST(:o AS uuid), 'zoho', 'crm') RETURNING id"),
            {"o": org}).scalar()
        c.execute(text(
            "INSERT INTO crm_source_profiles (organization_id, connection_id, "
            "version, profile, profile_hash) VALUES (CAST(:o AS uuid), :c, 1, "
            "CAST(:p AS jsonb), :h)"),
            {"o": org, "c": conn, "p": json.dumps({"modules": modules}),
             "h": uuid.uuid4().hex})


def _native(p: Any, org: str) -> None:
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO crm_settings (organization_id, mode, access) "
            "VALUES (CAST(:o AS uuid), 'native', 'everyone') "
            "ON CONFLICT (organization_id) DO UPDATE SET mode = 'native'"), {"o": org})


def _defs(p: Any, org: str, module: str, rows: list[tuple]) -> None:
    """``(key, type, lookup target)`` rows of ``crm_field_defs``."""
    with p.admin_engine.begin() as c:
        for position, (key, kind, target) in enumerate(rows):
            c.execute(text(
                "INSERT INTO crm_field_defs (organization_id, entity, key, label, "
                "type, options, source, provider_api_name, position) VALUES "
                "(CAST(:o AS uuid), :e, :k, :l, :t, CAST(:opt AS jsonb), 'zoho', "
                ":k, :pos)"),
                {"o": org, "e": f"module:{module}", "k": key,
                 "l": key.replace("_", " "), "t": kind, "pos": position,
                 "opt": json.dumps({"lookup_module": target}) if target else None})


def _fills(p: Any, org: str, module: str, shares: dict[str, float], total: int = 100) -> None:
    with p.admin_engine.begin() as c:
        for key, share in shares.items():
            c.execute(text(
                "INSERT INTO crm_field_fill (organization_id, entity, key, filled, "
                "total, distinct_count) VALUES (CAST(:o AS uuid), :e, :k, :f, :t, 1)"),
                {"o": org, "e": f"module:{module}", "k": key,
                 "f": int(share * total), "t": total})


def _records(p: Any, org: str, module: str, count: int) -> None:
    """*count* records, each holding the marks of item 5."""
    with p.admin_engine.begin() as c:
        for i in range(count):
            c.execute(text(
                "INSERT INTO crm_module_records (organization_id, module, ext_source, "
                "ext_id, name, owner_email, fields) VALUES (CAST(:o AS uuid), :m, "
                "'zoho', :e, :n, :w, CAST(:f AS jsonb))"),
                {"o": org, "m": module, "e": f"{module}-{i}", "n": f"{_MARK}-name",
                 "w": _OWNER_MARK,
                 "f": json.dumps({"Billed_Value": 4242, "Note": _MARK,
                                  "Buyer": {"id": "x", "name": _MARK}})})


def _plan(p: Any, org: str, items: list[dict]) -> None:
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO crm_mapping_plans (organization_id, version, state, rules, "
            "created_by, approved_by, approved_at) VALUES (CAST(:o AS uuid), 1, "
            "'approved', CAST(:r AS jsonb), 'r8', 'r8', now())"),
            {"o": org, "r": json.dumps(items)})


@pytest.fixture
def cat_env(promoted, monkeypatch):
    from acb_common import get_settings

    from tests.unit._crm_fakes import crm_switch_on

    crm_switch_on(monkeypatch, "*")
    settings = get_settings()
    monkeypatch.setattr(settings, "crm_catalogue_orgs", "*", raising=False)
    monkeypatch.setattr(settings, "crm_fill_rank_orgs", "*", raising=False)
    yield promoted
    with promoted.admin_engine.begin() as c:
        for org in (promoted.org_a, promoted.org_b):
            for table in _TABLES:
                c.execute(text(
                    f"DELETE FROM {table} WHERE organization_id = CAST(:o AS uuid)"),
                    {"o": org})


#: Org A: DEWiN's shape, with invented names.
_PROFILE_A = [
    _core("Contacts", "Field Sites"), _core("Accounts", "Accounts"),
    _core("Leads", "Leads"), _core("Deals", "Deals"),
    _module("Invoices", custom=False, singular="Invoice"),
    _module("Fee_Receipts", "Collection"),
    _module("Buyer_Clients", "Clients"),
    _module("Quotes", custom=False),
    _module("Towers"),
]

_INVOICE_DEFS = [
    ("Name", "text", None), ("Billed_Value", "currency", None),
    ("Net_Value", "currency", None), ("Invoice_Date", "date", None),
    ("Status", "select", None), ("Status3", "select", None),
    ("Buyer", "lookup", "Buyer_Clients"), ("Site_Contact", "lookup", "Contacts"),
    ("Owner", "lookup", None),
]


@pytest.fixture
def org_a(cat_env):
    p, a = cat_env, cat_env.org_a
    _mirror(p, a, _PROFILE_A)
    _defs(p, a, "Invoices", _INVOICE_DEFS)
    _fills(p, a, "Invoices", {"Billed_Value": 0.8, "Net_Value": 0.7,
                              "Invoice_Date": 1.0, "Status": 0.42, "Status3": 0.63})
    _defs(p, a, "Fee_Receipts", [("Paid_On", "date", None), ("Part_A", "currency", None),
                                 ("Part_B", "currency", None)])
    _defs(p, a, "Towers", [("Name", "text", None)])
    _records(p, a, "Invoices", 3)
    _records(p, a, "Fee_Receipts", 2)
    _records(p, a, "Buyer_Clients", 1)
    _records(p, a, "Towers", 4)
    _plan(p, a, [
        {"kind": "catalogue", "module": "Fee_Receipts", "amount": None,
         "date": "Paid_On", "by": {"amount": "ai", "date": "ai"}},
        {"kind": "catalogue", "module": "Invoices", "amount": "Net_Value",
         "by": {"amount": "admin"}},
    ])
    return p


def _by_module(body: dict) -> dict[str, dict]:
    return {e["module"]: e for e in body["entries"]}


async def test_a_mirror_org_gets_one_entry_for_each_entity_and_module(
    org_a, app_engine,
) -> None:
    res = await _call(org_a, org_a.org_a)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["mode"] == "mirror"
    entries = _by_module(body)
    # The core entities, then the modules in the profile order.
    assert list(entries)[:4] == ["Accounts", "Contacts", "Leads", "Deals"]
    assert sorted(list(entries)[4:]) == [
        "Buyer_Clients", "Fee_Receipts", "Invoices", "Quotes", "Towers",
    ]
    # Rule 1: a renamed core entity waits for its purpose question.
    contacts = entries["Contacts"]
    assert (contacts["label"], contacts["purpose"], contacts["open"]) == (
        "Field Sites", None, [])
    assert entries["Accounts"]["purpose"] == "customer_company"
    deals = entries["Deals"]
    assert deals["purpose"] == "deal" and deals["fields"]["amount"]["key"] == "amount"
    # Rules 2, 6 to 9, and a stored admin part: Net_Value wins over code.
    inv = entries["Invoices"]
    assert (inv["purpose"], inv["records"], inv["by"]["purpose"]) == ("invoice", 3, "code")
    assert (inv["fields"]["amount"]["key"], inv["by"]["amount"]) == ("Net_Value", "admin")
    assert inv["fields"]["date"]["key"] == "Invoice_Date"
    assert inv["fields"]["parent"] == {"key": "Buyer", "label": "Buyer",
                                       "type": "lookup", "target": "Buyer_Clients"}
    assert inv["open"] == ["status"]
    assert [link["key"] for link in inv["links"]] == ["Buyer", "Site_Contact"]
    assert inv["name"]["key"] == "Name" and inv["owner"]["key"] == "owner_email"
    # Rule 3, and a stored part that was asked and stayed unsure.
    fee = entries["Fee_Receipts"]
    assert (fee["label"], fee["purpose"]) == ("Collection", "payment_in")
    assert fee["fields"]["amount"] is None and fee["fields"]["date"]["key"] == "Paid_On"
    assert entries["Buyer_Clients"]["purpose"] == "customer_company"
    # Item 9 and rule 5: 0 records, the purpose of code, and no question.
    quotes = entries["Quotes"]
    assert (quotes["records"], quotes["purpose"], quotes["open"]) == (0, "deal", [])
    assert entries["Towers"]["open"] == ["purpose"]


async def test_the_role_of_the_approved_plan_gives_the_purpose(org_a, app_engine) -> None:
    with org_a.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE crm_mapping_plans SET rules = rules || CAST(:r AS jsonb) WHERE "
            "organization_id = CAST(:o AS uuid)"),
            {"o": org_a.org_a, "r": json.dumps([
                {"kind": "role", "slot": "company", "module": "Towers"}])})
    towers = _by_module((await _call(org_a, org_a.org_a)).json())["Towers"]
    assert (towers["purpose"], towers["by"]["purpose"], towers["open"]) == (
        "customer_company", "role", [])


async def test_no_record_value_and_no_owner_email_reaches_the_body(
    org_a, app_engine,
) -> None:
    """Item 5: the marks sit in names, fields and owners of every record."""
    res = await _call(org_a, org_a.org_a)
    assert res.status_code == 200, res.text
    assert _MARK not in res.text and _OWNER_MARK not in res.text
    assert "4242" not in res.text


async def test_native_mode_gives_the_four_core_entities_from_code(
    cat_env, app_engine,
) -> None:
    _native(cat_env, cat_env.org_b)
    body = (await _call(cat_env, cat_env.org_b)).json()
    assert body["mode"] == "native"
    assert [(e["entity"], e["label"], e["purpose"]) for e in body["entries"]] == [
        ("organizations", "Organizations", "customer_company"),
        ("contacts", "Contacts", "person"), ("leads", "Leads", "lead"),
        ("deals", "Deals", "deal"),
    ]
    assert {e["kind"] for e in body["entries"]} == {"entity"}


async def test_an_org_outside_the_list_gets_404_in_each_mode(
    org_a, app_engine, monkeypatch,
) -> None:
    from acb_common import get_settings

    _native(org_a, org_a.org_b)
    monkeypatch.setattr(get_settings(), "crm_catalogue_orgs", org_a.org_b, raising=False)
    off = await _call(org_a, org_a.org_a)
    assert off.status_code == 404, off.text
    assert off.json()["detail"]["code"] == "catalogue_off"
    assert await _entry_of(org_a, org_a.org_a, "Invoices") is None
    monkeypatch.setattr(get_settings(), "crm_catalogue_orgs", "", raising=False)
    native = await _call(org_a, org_a.org_b)
    assert native.status_code == 404 and native.json()["detail"]["code"] == "catalogue_off"


async def test_org_b_never_reads_the_catalogue_of_org_a(org_a, app_engine) -> None:
    """Narrowing 4. Org B mirrors one module of the same name, with no field,
    no record and no plan. Nothing of org A reaches it."""
    b = org_a.org_b
    _mirror(org_a, b, [_module("Invoices", custom=False)])
    body = (await _call(org_a, b)).json()
    entries = _by_module(body)
    assert list(entries) == ["Accounts", "Contacts", "Leads", "Deals", "Invoices"]
    inv = entries["Invoices"]
    assert (inv["records"], inv["fields"], inv["links"]) == (0, {}, [])
    assert entries["Contacts"]["label"] == "Contacts"
    for word in ("Field Sites", "Collection", "Net_Value", "Buyer", "Towers"):
        assert word not in json.dumps(body), word
    # The one reader of one entry, bound to org B, reads org B only.
    entry = await _entry_of(org_a, b, "Invoices")
    assert entry is not None and entry.records == 0 and entry.fields == {}
    assert await _entry_of(org_a, b, "Fee_Receipts") is None
    own = await _entry_of(org_a, org_a.org_a, "Invoices")
    assert own.fields["amount"].key == "Net_Value"


async def test_twenty_five_modules_and_1300_fields_answer_under_one_second(
    cat_env, app_engine,
) -> None:
    """Item 10: the route reads in a few statements, whatever the size."""
    p, a = cat_env, cat_env.org_a
    modules = [_module(f"Big_{i:02d}", f"Big {i}") for i in range(25)]
    _mirror(p, a, modules)
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO crm_field_defs (organization_id, entity, key, label, type, "
            "source, provider_api_name, position) "
            "SELECT CAST(:o AS uuid), 'module:Big_' || lpad(CAST(m AS text), 2, '0'), "
            "'F_' || f, 'Field ' || f, "
            "CASE f % 4 WHEN 0 THEN 'currency' WHEN 1 THEN 'date' "
            "WHEN 2 THEN 'select' ELSE 'text' END, 'zoho', 'F_' || f, f "
            "FROM generate_series(0, 24) m, generate_series(1, 52) f"), {"o": a})
        c.execute(text(
            "INSERT INTO crm_field_fill (organization_id, entity, key, filled, total, "
            "distinct_count) SELECT organization_id, entity, key, 60, 100, 3 "
            "FROM crm_field_defs WHERE organization_id = CAST(:o AS uuid)"), {"o": a})
        count = c.execute(text(
            "SELECT count(*) FROM crm_field_defs WHERE organization_id = "
            "CAST(:o AS uuid)"), {"o": a}).scalar()
    assert count == 1300
    await _call(p, a)  # warm the engine and the imports
    started = time.perf_counter()
    res = await _call(p, a)
    took = time.perf_counter() - started
    assert res.status_code == 200, res.text
    assert len(res.json()["entries"]) == 29
    assert took < BOUND_S, f"the catalogue took {took:.3f} s"
