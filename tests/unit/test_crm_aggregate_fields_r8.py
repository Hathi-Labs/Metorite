"""R8 — the typed aggregate over every module and field (WS-53 CRM-Q25).

Spec: ``project-docs/specs/crm_platform.md`` §18.11, CRM-Q25 done-when 1 to 8.

Against a REAL Postgres, through the real ``/crm`` router, as the
non-privileged role of the phase-4 rehearsal. The seed goes in as the
superuser, so each read below is the first thing the app role does.

1. A module entity: a sum, an avg, a min and a max of a field, and a 404 for
   a module that the profile does not mirror.
2. A field group by value (picklist, boolean, lookup) and by a date bucket
   (month, quarter, year), and the fixed module groups.
3. The field filters of a module, one value and a date range.
4. A core entity measures and groups a custom field, never a system or a
   retired one, and takes no field filter.
5. Each unfit name or type is a 422, and so is each name that tries to
   inject SQL.
6. The 50 largest groups, ``other`` and ``omitted``, in the order of today.
7. No row of organization B reaches organization A.
8. 30,000 module rows: a sum and a bucket group answer 200 inside the
   10 s timeout and inside the measured bound, and a statement past the
   timeout answers 503.

A hermetic fake agrees with any SQL it is handed, so none of these is proved
without the database.
"""

# ruff: noqa: F811 - the fixtures come in by import and are used as arguments
from __future__ import annotations

import json
import time
import uuid
from typing import Any
from urllib.parse import urlencode

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
    "crm_module_records", "crm_field_defs", "crm_source_profiles",
    "integration_connections", "crm_status_changes", "crm_deal_contacts",
    "crm_activities", "crm_deals", "crm_leads", "crm_contacts", "crm_companies",
    "crm_pipeline_stages", "crm_pipelines", "crm_settings", "crm_deal_statuses",
    "crm_lead_statuses", "crm_lost_reasons",
)

#: The measured bound of item 8, in seconds, for one request on a warm engine:
#: the tenant session, the statements and the answer. Measured 0.10 to 0.18 s
#: on the scratch database (2026-10-16, three runs), so 1 s leaves room for a
#: slower box. See the "Built as" note of CRM-Q25.
PRACTICAL_BOUND_S = 1.0

#: The LOCAL statement_timeout of the route (done-when 6).
TIMEOUT_S = 10.0


def _admin(org: str) -> Any:
    from acb_auth import UserContext, UserRole, build_access

    return UserContext(
        email="admin@crm-q25-r8.test", role=UserRole.EMPLOYEE,
        access=build_access(["feature:crm", "admin:access:manage"]),
        organization_id=org,
    )


async def _call(p: Any, user: Any, method: str, path: str, body: dict | None = None) -> Any:
    """One request through the real router. The tenant comes from the user."""
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
        async with (
            tenant_engine_scope(p.app_url.render_as_string(hide_password=False)),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://crm.test",
            ) as client,
        ):
            return await client.request(method, path, json=body)
    finally:
        release_tenant(scope)


def _path(**query: Any) -> str:
    return "/crm/aggregate?" + urlencode(query)


async def _agg(p: Any, org: str, **query: Any) -> dict:
    res = await _call(p, _admin(org), "GET", _path(**query))
    assert res.status_code == 200, (query, res.text)
    return res.json()


async def _status(p: Any, org: str, **query: Any) -> int:
    return (await _call(p, _admin(org), "GET", _path(**query))).status_code


def _values(body: dict) -> dict[str, Any]:
    return {g["label"]: g["value"] for g in body["groups"]}


def _keys(body: dict) -> list[Any]:
    return [g["key"] for g in body["groups"]]


# ── The seed, as the superuser ────────────────────────────────────────────────


def _module(api_name: str, *, mirrored: bool = True) -> dict[str, Any]:
    """One generic module row of a profile, in the shape ``_module_rows`` writes."""
    return {
        "api_name": api_name, "tab": api_name, "label": api_name,
        "singular_label": api_name.rstrip("s"), "custom": False,
        "generated_type": "default", "api_supported": True, "visible": True,
        "mirrored": mirrored, "generic": True,
        "scope": f"ZohoCRM.modules.{api_name.lower()}.READ",
        "display_field": "Name",
    }


_PROFILE = [_module("Products"), _module("Quotes"), _module("Vendors", mirrored=False)]


def _mirror_org(p: Any, org: str, modules: list[dict[str, Any]]) -> None:
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


def _defs(p: Any, org: str, entity: str, rows: list[tuple]) -> None:
    """``(key, type, options, is_system, retired)`` rows of ``crm_field_defs``."""
    with p.admin_engine.begin() as c:
        for position, (key, ftype, options, is_system, retired) in enumerate(rows):
            c.execute(text(
                "INSERT INTO crm_field_defs (organization_id, entity, key, label, "
                "type, options, source, provider_api_name, position, is_system, "
                "retired_at) VALUES (CAST(:o AS uuid), :e, :k, :l, :t, "
                "CAST(:opt AS jsonb), 'zoho', :k, :pos, :sys, "
                "CASE WHEN :ret THEN now() END)"),
                {"o": org, "e": entity, "k": key, "l": key.replace("_", " "),
                 "t": ftype, "opt": json.dumps(options) if options else None,
                 "pos": position, "sys": is_system, "ret": retired})


_PRODUCT_DEFS = [
    ("Unit_Price", "currency", None, False, False),
    ("Qty", "number", None, False, False),
    ("Category", "select", [{"label": "Hardware", "value": "HW"},
                            {"label": "Software", "value": "SW"}], False, False),
    ("Taxable", "boolean", None, False, False),
    ("Vendor_Name", "lookup", {"lookup_module": "Vendors"}, False, False),
    ("Sales_Start", "date", None, False, False),
    ("Updated_On", "datetime", None, False, False),
    ("Description", "text", None, False, False),
    ("Tags", "multiselect", None, False, False),
    ("Margin", "formula", None, False, False),
]


def _record(c: Any, org: str, module: str, ext_id: str, owner: str | None,
            fields: dict[str, Any], created: str | None = None) -> None:
    c.execute(text(
        "INSERT INTO crm_module_records (organization_id, module, ext_source, "
        "ext_id, name, owner_email, fields, ext_created_at) VALUES "
        "(CAST(:o AS uuid), :m, 'zoho', :e, :e, :w, CAST(:f AS jsonb), "
        "CAST(:t AS timestamptz))"),
        {"o": org, "m": module, "e": ext_id, "w": owner, "f": json.dumps(fields),
         "t": created})


def _products(p: Any, org: str) -> None:
    """Four products of org A, with Zoho's value shapes."""
    with p.admin_engine.begin() as c:
        _record(c, org, "Products", "p1", "a@q25.test", {
            "Unit_Price": 100, "Qty": "5", "Category": "HW", "Taxable": True,
            "Vendor_Name": {"id": "v1", "name": "Acme"},
            "Sales_Start": "2026-01-15", "Updated_On": "2026-04-02T10:00:00+05:30",
            "Tags": ["Red", "Blue"],
        }, created="2026-01-10T00:00:00Z")
        _record(c, org, "Products", "p2", "b@q25.test", {
            "Unit_Price": 250.5, "Qty": 2, "Category": "SW", "Taxable": False,
            "Vendor_Name": {"id": "v1", "name": "Acme"}, "Sales_Start": "2026-02-20",
            "Tags": ["Red"],
        }, created="2026-02-11T00:00:00Z")
        # A price that is not a number is left out of each measure.
        _record(c, org, "Products", "p3", "A@q25.test", {
            "Unit_Price": "abc", "Qty": None, "Category": "HW",
            "Vendor_Name": {"id": "v2", "name": "Beta"}, "Sales_Start": "2026-04-01",
        }, created="2026-02-12T00:00:00Z")
        _record(c, org, "Products", "p4", None, {})
        _record(c, org, "Quotes", "q1", "a@q25.test", {"Grand_Total": 7})


@pytest.fixture
def ns(promoted, monkeypatch):
    from tests.unit._crm_fakes import crm_switch_on

    crm_switch_on(monkeypatch, "*")
    yield promoted
    with promoted.admin_engine.begin() as c:
        for org in (promoted.org_a, promoted.org_b):
            for table in _TABLES:
                c.execute(text(
                    f"DELETE FROM {table} WHERE organization_id = CAST(:o AS uuid)"),
                    {"o": org})


@pytest.fixture
def mods(ns):
    """Org A mirrors Products and Quotes. Org B mirrors Products only."""
    a, b = ns.org_a, ns.org_b
    _mirror_org(ns, a, _PROFILE)
    _mirror_org(ns, b, [_module("Products")])
    _defs(ns, a, "module:Products", _PRODUCT_DEFS)
    _defs(ns, b, "module:Products", _PRODUCT_DEFS)
    _products(ns, a)
    with ns.admin_engine.begin() as c:
        _record(c, b, "Products", "p1", "b@q25.test", {"Unit_Price": 99999})
    return ns


# ── 1. A module entity, and its measures ─────────────────────────────────────


async def test_a_sum_on_a_module_by_owner(mods, app_engine) -> None:
    body = await _agg(mods, mods.org_a, entity="module:Products", measure="sum",
                      field="Unit_Price", group_by="owner")
    # The owner folds its case. "abc" is no number, and p4 has no owner.
    assert _values(body) == {"a@q25.test": 100.0, "b@q25.test": 250.5, "(none)": 0.0}
    assert body["entity"] == "module:Products"
    assert (body["field"], body["field_type"]) == ("Unit_Price", "currency")
    assert body["total"] == 350.5
    assert (body["other"], body["omitted"]) == (0, 0)


async def test_avg_min_and_max_on_a_module(mods, app_engine) -> None:
    avg = await _agg(mods, mods.org_a, entity="module:Products", measure="avg",
                     field="Unit_Price", group_by="owner")
    assert _values(avg) == {"a@q25.test": 100.0, "b@q25.test": 250.5, "(none)": None}
    # The mean of every value, not the mean of the group means.
    assert avg["total"] == 175.25
    low = await _agg(mods, mods.org_a, entity="module:Products", measure="min",
                     field="Unit_Price", group_by="owner")
    high = await _agg(mods, mods.org_a, entity="module:Products", measure="max",
                      field="Unit_Price", group_by="owner")
    assert (low["total"], high["total"]) == (100.0, 250.5)
    # A number that Zoho sends as a string is a number.
    qty = await _agg(mods, mods.org_a, entity="module:Products", measure="sum",
                     field="Qty", group_by="owner")
    assert qty["total"] == 7.0


async def test_a_module_that_the_profile_does_not_mirror_is_404(mods, app_engine) -> None:
    for module in ("Vendors", "Nope", "Leads", "Products' OR '1'='1"):
        res = await _call(mods, _admin(mods.org_a), "GET",
                          _path(entity=f"module:{module}", group_by="owner"))
        assert res.status_code == 404, (module, res.text)
        assert res.json()["detail"]["code"] == "unknown_module"


async def test_a_native_org_has_no_module(ns, app_engine) -> None:
    res = await _call(ns, _admin(ns.org_a), "PATCH", "/crm/settings", {"mode": "native"})
    assert res.status_code == 200, res.text
    assert await _status(ns, ns.org_a, entity="module:Products", group_by="owner") == 404


# ── 2. The groups of a module ────────────────────────────────────────────────


async def test_a_value_group_on_a_module(mods, app_engine) -> None:
    a = mods.org_a
    pick = await _agg(mods, a, entity="module:Products", group_field="Category")
    # A picklist value shows its option label. Largest first, a tie by label.
    assert [(g["key"], g["label"], g["value"]) for g in pick["groups"]] == [
        ("HW", "Hardware", 2), (None, "(none)", 1), ("SW", "Software", 1),
    ]
    flag = await _agg(mods, a, entity="module:Products", group_field="Taxable")
    assert _values(flag) == {"true": 1, "false": 1, "(none)": 2}
    look = await _agg(mods, a, entity="module:Products", group_field="Vendor_Name",
                      measure="sum", field="Unit_Price")
    # A lookup groups by its id, and its name labels the group.
    assert [(g["key"], g["label"], g["value"]) for g in look["groups"]] == [
        ("v1", "Acme", 350.5), (None, "(none)", 0.0), ("v2", "Beta", 0.0),
    ]
    assert look["group_by"] is None and look["group_field"] == "Vendor_Name"


async def test_a_date_bucket_on_a_module(mods, app_engine) -> None:
    a = mods.org_a
    month = await _agg(mods, a, entity="module:Products", group_field="Sales_Start",
                       bucket="month")
    # Time order, and the record with no date last.
    assert [(g["key"], g["value"]) for g in month["groups"]] == [
        ("2026-01", 1), ("2026-02", 1), ("2026-04", 1), (None, 1),
    ]
    assert month["bucket"] == "month"
    quarter = await _agg(mods, a, entity="module:Products", group_field="Sales_Start",
                         bucket="quarter", measure="sum", field="Unit_Price")
    assert [(g["key"], g["value"]) for g in quarter["groups"]] == [
        ("2026-Q1", 350.5), ("2026-Q2", 0.0), (None, 0.0),
    ]
    year = await _agg(mods, a, entity="module:Products", group_field="Sales_Start",
                      bucket="year")
    assert _values(year) == {"2026": 3, "(none)": 1}
    stamp = await _agg(mods, a, entity="module:Products", group_field="Updated_On",
                       bucket="month")
    assert _values(stamp) == {"2026-04": 1, "(none)": 3}


async def test_the_fixed_groups_of_a_module(mods, app_engine) -> None:
    """A module keeps Zoho's created time, so its created month is real."""
    created = await _agg(mods, mods.org_a, entity="module:Products",
                         group_by="created_month")
    assert [(g["key"], g["value"]) for g in created["groups"]] == [
        ("2026-01", 1), ("2026-02", 2), (None, 1),
    ]
    updated = await _agg(mods, mods.org_a, entity="module:Products",
                         group_by="updated_month")
    assert updated["total"] == 4


# ── 3. The field filters of a module ─────────────────────────────────────────


async def test_the_field_filters_of_a_module(mods, app_engine) -> None:
    a = mods.org_a

    async def owners(**filters: str) -> dict[str, Any]:
        query = {f"field.{k}": v for k, v in filters.items()}
        return _values(await _agg(mods, a, entity="module:Products",
                                  group_by="owner", **query))

    assert await owners(Category="HW") == {"a@q25.test": 2}
    assert await owners(**{"Sales_Start.from": "2026-02-01"}) == {
        "b@q25.test": 1, "a@q25.test": 1,
    }
    assert await owners(**{"Sales_Start.from": "2026-02-01",
                           "Sales_Start.to": "2026-02-28"}) == {"b@q25.test": 1}
    assert await owners(Vendor_Name="Acme") == {"a@q25.test": 1, "b@q25.test": 1}
    assert await owners(Vendor_Name="v2") == {"a@q25.test": 1}
    assert await owners(Taxable="true") == {"a@q25.test": 1}
    assert await owners(Qty="5") == {"a@q25.test": 1}
    assert await owners(Tags="Blue") == {"a@q25.test": 1}
    assert await owners(Updated_On="2026-04-02") == {"a@q25.test": 1}


# ── 4. A core entity and its custom fields ───────────────────────────────────


async def test_a_core_entity_measures_and_groups_a_custom_field(ns, app_engine) -> None:
    a = ns.org_a
    res = await _call(ns, _admin(a), "PATCH", "/crm/settings", {"mode": "native"})
    assert res.status_code == 200, res.text
    _defs(ns, a, "deal", [
        ("units", "number", None, False, False),
        ("tier", "select", [{"label": "Gold", "value": "g"}], False, False),
        ("old_units", "number", None, False, True),
        ("amount", "currency", None, True, False),
    ])
    with ns.admin_engine.begin() as c:
        for name, custom in (("D1", {"units": 3, "tier": "g", "old_units": 1}),
                             ("D2", {"units": "4", "tier": "s"}),
                             ("D3", {})):
            c.execute(text(
                "INSERT INTO crm_deals (organization_id, name, status_id, amount, "
                "owner_email, custom) SELECT CAST(:o AS uuid), :n, id, 100, "
                "'r@q25.test', CAST(:c AS jsonb) FROM crm_deal_statuses "
                "WHERE organization_id = CAST(:o AS uuid) AND name = 'Proposal'"),
                {"o": a, "n": name, "c": json.dumps(custom)})
    units = await _agg(ns, a, entity="deals", measure="sum", field="units",
                       group_by="status")
    assert _values(units) == {"Proposal": 7.0}
    tier = await _agg(ns, a, entity="deals", group_field="tier")
    assert _values(tier) == {"Gold": 1, "s": 1, "(none)": 1}
    weighted = await _agg(ns, a, entity="deals", measure="weighted", group_field="tier")
    assert weighted["total"] == 150.0  # 3 deals of 100 at Proposal's 50
    # The list filters still bind the core aggregate.
    owned = await _agg(ns, a, entity="deals", group_field="tier", **{"custom.tier": "g"})
    assert _values(owned) == {"Gold": 1}
    # A retired field and a system overlay are no custom keys (CRM-Q25 done-when 5).
    assert await _status(ns, a, entity="deals", measure="sum", field="old_units",
                         group_by="status") == 422
    assert await _status(ns, a, entity="deals", measure="sum", field="amount",
                         group_by="status") == 422
    # A core entity takes no field filter (CRM-Q25 done-when 4).
    assert await _status(ns, a, entity="deals", group_by="status",
                         **{"field.units": "3"}) == 422


# ── 5. Each unfit value is a 422, and so is each hostile name ────────────────


@pytest.mark.parametrize("query", [
    {"measure": "sum", "field": "Category", "group_by": "owner"},     # a picklist
    {"group_field": "Description"},                                    # text
    {"group_field": "Tags"},                                           # multiselect
    {"group_field": "Margin"},                                         # formula
    {"measure": "avg", "field": "Margin", "group_by": "owner"},
    {"group_field": "Sales_Start"},                                    # no bucket
    {"group_field": "Sales_Start", "bucket": "week"},
    {"group_field": "Category", "bucket": "month"},
    {"group_by": "owner", "bucket": "month"},
    {"group_by": "owner", "group_field": "Category"},
    {"measure": "count"},
    {"measure": "sum", "group_by": "owner"},                           # no field
    {"measure": "count", "field": "Unit_Price", "group_by": "owner"},
    {"measure": "sum_amount", "group_by": "owner"},
    {"measure": "weighted", "group_by": "owner"},
    {"group_by": "status"},
    {"group_by": "close_month"},
    {"group_by": "owner", "q": "widget"},                              # a list filter
    {"group_by": "owner", "owner": "me"},
    {"group_by": "owner", "field.Nope": "1"},
    {"group_by": "owner", "field.Description.from": "2026-01-01"},
    {"group_by": "owner", "field.Qty": "abc"},
    {"group_by": "owner", "field.Taxable": "maybe"},
    {"group_by": "owner", "field.Sales_Start": "soon"},
    {"group_by": "owner", "field.Sales_Start.to": "2026-13-45"},
])
async def test_an_unfit_value_is_a_422(mods, app_engine, query: dict) -> None:
    res = await _call(mods, _admin(mods.org_a), "GET",
                      _path(entity="module:Products", **query))
    assert res.status_code == 422, (query, res.text)


_HOSTILE = (
    "Unit_Price') OR 1=1 --",
    "x'; DROP TABLE crm_module_records; --",
    "Category\" OR \"1\"=\"1",
    "fields",
    "Unit_Price -> 'a'",
)


@pytest.mark.parametrize("name", _HOSTILE)
async def test_a_name_that_tries_to_inject_sql_is_a_422(mods, app_engine, name: str) -> None:
    a = mods.org_a
    for query in (
        {"measure": "sum", "field": name, "group_by": "owner"},
        {"group_field": name},
        {"group_by": "owner", f"field.{name}": "1"},
        {"group_by": "owner", f"field.{name}.from": "2026-01-01"},
    ):
        res = await _call(mods, _admin(a), "GET", _path(entity="module:Products", **query))
        assert res.status_code == 422, (query, res.text)
    for query in (
        {"measure": "sum", "field": name, "group_by": "status"},
        {"group_field": name},
    ):
        assert await _status(mods, a, entity="deals", **query) == 422, query
    # Nothing ran: the rows are all still there.
    count = await _agg(mods, a, entity="module:Products", group_by="owner")
    assert count["total"] == 4


# ── 5b. The fix round: inputs that once answered 500 ──────────────────────────


async def test_an_empty_value_is_an_absent_one(mods, app_engine) -> None:
    """An empty group_by, group_field, field or bucket counts as absent, for
    each of the four. It once raised KeyError('') as a 500."""
    a = mods.org_a
    for entity in ("module:Products", "deals"):
        # No group at all is a 422, never a 500.
        assert await _status(mods, a, entity=entity, group_field="") == 422
        assert await _status(mods, a, entity=entity, group_by="", group_field="") == 422
        # A sum with an empty field names no field: a 422.
        assert await _status(mods, a, entity=entity, measure="sum", field="",
                             group_by="owner") == 422
    body = await _agg(mods, a, entity="module:Products", group_by="owner",
                      group_field="", field="", bucket="")
    assert body["total"] == 4 and body["group_field"] is None


async def test_a_value_that_is_no_real_date_is_null(mods, app_engine) -> None:
    """One bad date must not break the read of every row. Each value that is
    no calendar date groups as "(none)" and passes no range."""
    a = mods.org_a
    with mods.admin_engine.begin() as c:
        for ext_id, value in (("bad1", "2026-02-30"), ("bad2", "2026-02-29"),
                              ("bad3", "0000-01-01"), ("bad4", "2026-04-31"),
                              ("leap", "2024-02-29T08:00:00+05:30")):
            _record(c, a, "Products", ext_id, None,
                    {"Sales_Start": value, "Updated_On": value})
    month = await _agg(mods, a, entity="module:Products", group_field="Sales_Start",
                       bucket="month")
    assert [(g["key"], g["value"]) for g in month["groups"]] == [
        ("2024-02", 1), ("2026-01", 1), ("2026-02", 1), ("2026-04", 1), (None, 5),
    ]
    stamp = await _agg(mods, a, entity="module:Products", group_field="Updated_On",
                       bucket="year")
    assert _values(stamp) == {"2024": 1, "2026": 1, "(none)": 7}
    ranged = await _agg(mods, a, entity="module:Products", group_by="owner", **{
        "field.Sales_Start.from": "2024-01-01", "field.Sales_Start.to": "2026-02-28",
    })
    # p1 (2026-01-15), p2 (2026-02-20) and the leap day. No bad date passes.
    assert ranged["total"] == 3


@pytest.mark.parametrize("value", [
    "1e1000000", "-1E+999", "1e-1000000", "1" * 61, "0." + "1" * 61,
])
async def test_a_number_past_the_bounds_is_a_422(mods, app_engine, value: str) -> None:
    """``1e1000000`` is finite, and Postgres still refuses it, once as a 500."""
    res = await _call(mods, _admin(mods.org_a), "GET", _path(
        entity="module:Products", group_by="owner", **{"field.Qty": value}))
    assert res.status_code == 422, res.text


async def test_a_number_inside_the_bounds_answers(mods, app_engine) -> None:
    body = await _agg(mods, mods.org_a, entity="module:Products", group_by="owner",
                      **{"field.Qty": "5.0e0"})
    assert _values(body) == {"a@q25.test": 1}
    body = await _agg(mods, mods.org_a, entity="module:Products", group_by="owner",
                      **{"field.Qty": "1e99"})
    assert body["total"] == 0


@pytest.mark.parametrize("query", [
    {"entity": "module:Products", "group_by": "owner", "field.Category": "HW\x00"},
    {"entity": "module:Products", "group_by": "owner", "field.Cat\x00egory": "HW"},
    {"entity": "module:Products", "group_field": "Cate\x00gory"},
    {"entity": "module:Products", "group_by": "owner", "measure": "sum",
     "field": "Unit_Price\x00"},
    {"entity": "deals", "group_by": "status", "custom.tier": "g\x00"},
    {"entity": "deals", "group_by": "status", "q": "\x00"},
])
async def test_a_nul_in_a_name_or_a_value_is_a_422(mods, app_engine, query: dict) -> None:
    res = await _call(mods, _admin(mods.org_a), "GET", _path(**query))
    assert res.status_code == 422, (query, res.text)


# ── 6. The 50 largest groups ─────────────────────────────────────────────────


async def test_the_cap_keeps_the_fifty_largest_and_folds_the_rest(mods, app_engine) -> None:
    a = mods.org_a
    with mods.admin_engine.begin() as c:
        c.execute(text("DELETE FROM crm_module_records WHERE organization_id = "
                       "CAST(:o AS uuid)"), {"o": a})
        for i in range(1, 61):
            _record(c, a, "Products", f"cap{i}", None,
                    {"Category": f"C{i:02d}", "Unit_Price": i})
    body = await _agg(mods, a, entity="module:Products", measure="sum",
                      field="Unit_Price", group_field="Category")
    assert len(body["groups"]) == 50
    assert [g["value"] for g in body["groups"]] == [float(i) for i in range(60, 10, -1)]
    assert (body["omitted"], body["other"], body["total"]) == (10, 55.0, 1830.0)
    avg = await _agg(mods, a, entity="module:Products", measure="avg",
                     field="Unit_Price", group_field="Category")
    assert (avg["other"], avg["total"]) == (5.5, 30.5)


async def test_the_cap_keeps_time_order_for_a_bucket(mods, app_engine) -> None:
    """60 months, and the ten LATEST hold two records each. The ten and the
    40 earliest single months stay, in time order. Ten single months fold.
    A cap in time order would keep the 50 earliest months instead, and fold
    the ten biggest groups (CRM-Q25 done-when 6)."""
    a = mods.org_a
    with mods.admin_engine.begin() as c:
        c.execute(text("DELETE FROM crm_module_records WHERE organization_id = "
                       "CAST(:o AS uuid)"), {"o": a})
        months = [f"{2020 + m // 12}-{m % 12 + 1:02d}" for m in range(60)]
        for i, month in enumerate(months):
            _record(c, a, "Products", f"m{i}", None, {"Sales_Start": f"{month}-03"})
        for i, month in enumerate(months[50:]):
            _record(c, a, "Products", f"d{i}", None, {"Sales_Start": f"{month}-09"})
    body = await _agg(mods, a, entity="module:Products", group_field="Sales_Start",
                      bucket="month")
    keys = _keys(body)
    assert len(keys) == 50 and keys == sorted(keys)
    assert keys == months[:40] + months[50:]
    assert (body["omitted"], body["other"], body["total"]) == (10, 10.0, 70.0)


# ── 7. Tenant isolation ──────────────────────────────────────────────────────


async def test_no_row_of_org_b_reaches_org_a(mods, app_engine) -> None:
    a = await _agg(mods, mods.org_a, entity="module:Products", measure="max",
                   field="Unit_Price", group_by="owner")
    assert a["total"] == 250.5
    b = await _agg(mods, mods.org_b, entity="module:Products", measure="sum",
                   field="Unit_Price", group_by="owner")
    assert _values(b) == {"b@q25.test": 99999.0}
    # A module that only org A mirrors is unknown to org B.
    assert await _status(mods, mods.org_b, entity="module:Quotes", group_by="owner") == 404
    fields_b = await _call(mods, _admin(mods.org_b), "GET", "/crm/fields/module:Quotes")
    assert fields_b.status_code == 404
    fields_a = await _call(mods, _admin(mods.org_a), "GET", "/crm/fields/module:Products")
    assert fields_a.status_code == 200, fields_a.text
    assert [f["key"] for f in fields_a.json()][:2] == ["Unit_Price", "Qty"]


# ── 7a. An empty field has no total (CRM-Q25c item 3) ────────────────────────


async def test_a_field_that_every_row_leaves_empty_has_no_total(mods, app_engine) -> None:
    """Found on DEWiN (2026-10-16): a sum over a field with no value gave a
    total of 0, and the assistant told a member "the total is 0". With no
    value, ``total`` and each group value are None, and ``values`` is 0. A
    count stays a number."""
    a = mods.org_a
    with mods.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO crm_field_defs (organization_id, entity, key, label, type, "
            "source, provider_api_name, position) VALUES (CAST(:o AS uuid), "
            "'module:Products', 'Discount', 'Discount', 'currency', 'zoho', "
            "'Discount', 99)"), {"o": a})
    for measure in ("sum", "avg", "min", "max"):
        for group in ({"group_by": "owner"}, {"group_field": "Category"}):
            body = await _agg(mods, a, entity="module:Products", measure=measure,
                              field="Discount", **group)
            assert (body["total"], body["values"]) == (None, 0), (measure, group)
            assert {g["value"] for g in body["groups"]} == {None}, (measure, body)
    # A field with values still sums them, and says how many it read.
    full = await _agg(mods, a, entity="module:Products", measure="sum",
                      field="Unit_Price", group_by="owner")
    assert (full["total"], full["values"]) == (350.5, 2)
    count = await _agg(mods, a, entity="module:Products", group_by="owner")
    assert count["total"] == 4 and count["values"] is None


# ── 7b. One zone for each month bucket (CRM-Q25b) ────────────────────────────

#: One record made at 00:30 IST on 1 November 2026. Its UTC month is
#: October. Zoho writes its datetime fields on the clock of the Zoho org.
_INSTANT_UTC = "2026-10-31T19:00:00Z"
_INSTANT_ZOHO = "2026-11-01T00:30:00+05:30"


def _set_zone(p: Any, org: str, zone: str | None) -> None:
    """Put ``org.time_zone`` in the profile, as ``mapping._org_summary`` does."""
    with p.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE crm_source_profiles SET profile = profile || "
            "jsonb_build_object('org', jsonb_build_object('time_zone', "
            "CAST(:z AS text))) WHERE organization_id = CAST(:o AS uuid)"),
            {"o": org, "z": zone})


@pytest.fixture
def zoned(ns):
    """Org A and org B each mirror Products, with one record at the same
    instant. The test names the zone of each profile."""
    for org in (ns.org_a, ns.org_b):
        _mirror_org(ns, org, [_module("Products")])
        _defs(ns, org, "module:Products", [
            ("Created_Time", "datetime", None, False, False),
            ("Sales_Start", "date", None, False, False),
        ])
        with ns.admin_engine.begin() as c:
            _record(c, org, "Products", "z1", "a@q25.test", {
                "Created_Time": _INSTANT_ZOHO, "Sales_Start": "2026-11-01",
            }, created=_INSTANT_UTC)
    return ns


async def _months(p: Any, org: str, **query: Any) -> tuple[list[Any], str]:
    body = await _agg(p, org, entity="module:Products", **query)
    return [(g["key"], g["value"]) for g in body["groups"]], body["zone"]


async def test_the_crm_zone_moves_the_record_into_november(zoned, app_engine) -> None:
    """Done-when 6, case 1: for a profile that names Asia/Kolkata, the
    record falls in 2026-11 by created_month and by its datetime field."""
    _set_zone(zoned, zoned.org_a, "Asia/Kolkata")
    created = await _months(zoned, zoned.org_a, group_by="created_month")
    assert created == ([("2026-11", 1)], "Asia/Kolkata")
    field = await _months(zoned, zoned.org_a, group_field="Created_Time", bucket="month")
    assert field == ([("2026-11", 1)], "Asia/Kolkata")
    updated = await _months(zoned, zoned.org_a, group_by="updated_month")
    assert updated[1] == "Asia/Kolkata"


async def test_an_org_with_no_zone_counts_in_utc(zoned, app_engine) -> None:
    """Done-when 6, case 2: the same record falls in 2026-10 for an org
    with no zone, by both rules. Before CRM-Q25b the field said 2026-11."""
    created = await _months(zoned, zoned.org_b, group_by="created_month")
    assert created == ([("2026-10", 1)], "UTC")
    field = await _months(zoned, zoned.org_b, group_field="Created_Time", bucket="month")
    assert field == ([("2026-10", 1)], "UTC")


@pytest.mark.parametrize("zone", ["Mars/Olympus", "America", "", "UTC'; DROP TABLE x; --"])
async def test_a_zone_that_is_not_real_gives_utc(zoned, app_engine, zone: str) -> None:
    """Done-when 6, case 3: a zone that is not a real one gives UTC, not an
    error. A directory of the zone database is no zone."""
    _set_zone(zoned, zoned.org_a, zone)
    field = await _months(zoned, zoned.org_a, group_field="Created_Time", bucket="month")
    assert field == ([("2026-10", 1)], "UTC")


async def test_a_datetime_range_agrees_with_the_bucket(zoned, app_engine) -> None:
    """Done-when 4: a range on a datetime field takes its day in the CRM
    zone, so the record that the bucket puts in November passes a November
    range, and fails an October one. With no zone, the reverse."""
    _set_zone(zoned, zoned.org_a, "Asia/Kolkata")

    async def total(org: str, **ends: str) -> float:
        query = {f"field.Created_Time.{k}": v for k, v in ends.items()}
        return (await _agg(zoned, org, entity="module:Products", group_by="owner",
                           **query))["total"]

    assert await total(zoned.org_a, **{"from": "2026-11-01"}) == 1
    assert await total(zoned.org_a, to="2026-10-31") == 0
    assert await total(zoned.org_b, **{"from": "2026-11-01"}) == 0
    assert await total(zoned.org_b, to="2026-10-31") == 1


async def test_a_date_field_keeps_the_day_that_zoho_wrote(zoned, app_engine) -> None:
    """Done-when 3 and 7: a date has no time, so no zone moves it."""
    _set_zone(zoned, zoned.org_a, "America/Los_Angeles")
    for org in (zoned.org_a, zoned.org_b):
        day = await _months(zoned, org, group_field="Sales_Start", bucket="month")
        assert day[0] == [("2026-11", 1)], (org, day)
        ranged = await _agg(zoned, org, entity="module:Products", group_by="owner",
                            **{"field.Sales_Start.from": "2026-11-01"})
        assert ranged["total"] == 1


async def test_the_core_updated_month_takes_the_zone(zoned, app_engine) -> None:
    """Done-when 2 and 7: a mirrored deal changed at 00:30 IST on 1 November
    falls in 2026-11 by updated_month for an org in Asia/Kolkata, and in
    2026-10 for an org with no zone. Its close month is a date, and no zone
    moves it."""
    _set_zone(zoned, zoned.org_a, "Asia/Kolkata")
    with zoned.admin_engine.begin() as c:
        for org in (zoned.org_a, zoned.org_b):
            c.execute(text(
                "INSERT INTO crm_deal_statuses (organization_id, name, position, "
                "type) VALUES (CAST(:o AS uuid), 'Q25b open', 1, 'open')"), {"o": org})
            c.execute(text(
                "INSERT INTO crm_deals (organization_id, name, status_id, "
                "ext_source, ext_id, ext_modified_at, expected_close_date) "
                "SELECT CAST(:o AS uuid), 'Z deal', id, 'zoho', 'zd1', "
                "CAST(:t AS timestamptz), DATE '2026-11-01' FROM crm_deal_statuses "
                "WHERE organization_id = CAST(:o AS uuid) AND name = 'Q25b open'"),
                {"o": org, "t": _INSTANT_UTC})
    for org, month, zone in ((zoned.org_a, "2026-11", "Asia/Kolkata"),
                             (zoned.org_b, "2026-10", "UTC")):
        body = await _agg(zoned, org, entity="deals", group_by="updated_month")
        assert ([(g["key"], g["value"]) for g in body["groups"]], body["zone"]) == (
            [(month, 1)], zone)
        close = await _agg(zoned, org, entity="deals", group_by="close_month")
        assert [(g["key"], g["value"]) for g in close["groups"]] == [("2026-11", 1)]


async def test_a_native_crm_counts_in_utc(zoned, app_engine) -> None:
    """A Native CRM has no Zoho org, so a stale profile names no zone. The
    route refuses a move from Mirror to Native, so the seed sets the mode."""
    _set_zone(zoned, zoned.org_a, "Asia/Kolkata")
    with zoned.admin_engine.begin() as c:
        c.execute(text("UPDATE crm_settings SET mode = 'native' "
                       "WHERE organization_id = CAST(:o AS uuid)"), {"o": zoned.org_a})
    body = await _agg(zoned, zoned.org_a, entity="deals", group_by="updated_month")
    assert body["zone"] == "UTC"


# ── 8. 30,000 module rows ────────────────────────────────────────────────────

_BULK = 30_000

_BULK_SQL = (
    "INSERT INTO crm_module_records (organization_id, module, ext_source, "
    "ext_id, name, owner_email, fields) "
    "SELECT CAST(:o AS uuid), 'Products', 'zoho', 'bulk' || g, 'Bulk ' || g, "
    "'rep' || (g % 7) || '@q25.test', jsonb_build_object("
    "'Unit_Price', CASE WHEN g % 10 = 0 THEN to_jsonb(CAST(g % 1000 AS text)) "
    "ELSE to_jsonb(g % 1000) END, "
    "'Category', 'C' || (g % 20), "
    "'Sales_Start', to_char(DATE '2024-01-01' + (g % 1095), 'YYYY-MM-DD')) "
    "FROM generate_series(1, :n) AS g"
)


async def _timed(p: Any, org: str, queries: dict[str, dict]) -> dict[str, tuple]:
    """Each query through ONE app, engine and client, after one warm-up call,
    so the time is the route's own: the session, the statements, the answer.
    Name → (status, body, seconds)."""
    import httpx
    from acb_auth import get_current_user
    from acb_common.db import bind_tenant, clear_tenant, release_tenant
    from fastapi import FastAPI
    from gateway.routes import crm as crm_package

    user = _admin(org)

    async def _caller() -> Any:
        bind_tenant(user.organization_id)
        return user

    app = FastAPI()
    app.include_router(crm_package.router)
    app.dependency_overrides[get_current_user] = _caller
    out: dict[str, tuple] = {}
    scope = clear_tenant()
    try:
        async with (
            tenant_engine_scope(p.app_url.render_as_string(hide_password=False)),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://crm.test",
            ) as client,
        ):
            warm = await client.get(_path(entity="module:Products", group_by="owner"))
            assert warm.status_code == 200, warm.text
            for name, query in queries.items():
                start = time.perf_counter()
                res = await client.get(_path(entity="module:Products", **query))
                out[name] = (res.status_code, res.json(), time.perf_counter() - start)
    finally:
        release_tenant(scope)
    return out


async def test_thirty_thousand_module_rows(mods, app_engine, monkeypatch) -> None:
    from gateway.routes.crm import aggregate as aggregate_route

    a = mods.org_a
    with mods.admin_engine.begin() as c:
        c.execute(text("DELETE FROM crm_module_records WHERE organization_id = "
                       "CAST(:o AS uuid)"), {"o": a})
        c.execute(text(_BULK_SQL), {"o": a, "n": _BULK})
        c.execute(text("ANALYZE crm_module_records"))
    timed = await _timed(mods, a, {
        "sum": {"measure": "sum", "field": "Unit_Price", "group_field": "Category"},
        "bucket": {"group_field": "Sales_Start", "bucket": "month"},
    })
    print("\nCRM-Q25 30,000 rows, seconds:",
          {name: round(seconds, 3) for name, (_s, _b, seconds) in timed.items()})
    status, body, _seconds = timed["sum"]
    assert status == 200, body
    # 30 x (0 + 1 + ... + 999). Every tenth price is a JSON string.
    assert body["total"] == 30 * 499_500 and len(body["groups"]) == 20
    status, body, _seconds = timed["bucket"]
    assert status == 200, body
    assert body["total"] == _BULK and len(body["groups"]) == 36
    for name, (_status, _body, seconds) in timed.items():
        assert seconds < TIMEOUT_S, (name, seconds)
        assert seconds < PRACTICAL_BOUND_S, (name, seconds)
    # Past the LOCAL statement_timeout, the route answers 503 and no 500.
    monkeypatch.setattr(aggregate_route, "STATEMENT_TIMEOUT_MS", 1)
    res = await _call(mods, _admin(a), "GET", _path(
        entity="module:Products", measure="sum", field="Unit_Price",
        group_field="Category",
    ))
    assert res.status_code == 503, res.text
    assert res.json()["detail"]["code"] == "aggregate_timeout"
