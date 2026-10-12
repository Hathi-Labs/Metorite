"""The rules of the generic record store that need no database (WS-53 CRM-Q6a).

Spec: ``project-docs/specs/crm_platform.md`` §18.7. The R8 suite
``test_crm_module_records_r8.py`` proves the sync and the table. This file
holds the pure rules:

- which modules the profile takes, in which order, and the cap of 25;
- the scope name of each module, and that the consent asks for each one;
- the field definitions, the display field and the lookup target;
- one record as the columns of ``crm_module_records``;
- the adapter reads a generic module by its API name, and refuses junk.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from gateway.crm_sources.zoho import client, links
from gateway.crm_sources.zoho import mapping as zm


def _module(api_name: str, kind: str = "default", supported: bool = True) -> dict:
    return {
        "api_name": api_name, "module_name": api_name, "plural_label": api_name,
        "generated_type": kind, "api_supported": supported,
    }


ALL_SCOPES = frozenset(
    zm.module_read_scope(m, custom=False) for m in zm.GENERIC_STANDARD_MODULES
) | {"ZohoCRM.modules.custom.READ"}


def _rows(modules: list[dict], scopes: frozenset[str] = ALL_SCOPES) -> dict[str, dict]:
    profile = zm.build_profile({"modules": modules}, None, scopes)
    return {m["api_name"]: m for m in profile["modules"]}


# ── Which modules ────────────────────────────────────────────────────────────


def test_the_five_rules_pick_the_generic_modules() -> None:
    rows = _rows([
        _module("Deals"), _module("Tasks"), _module("Products"),
        _module("Install_Base", "custom"), _module("Deals_X_Products", "linking"),
        _module("Line_Items", "subform"), _module("Hidden", "custom", supported=False),
        _module("Forecasts"), _module("Visits", "web"),
    ])
    generic = {name for name, row in rows.items() if row.get("generic")}
    assert generic == {"Products", "Install_Base"}
    assert rows["Products"]["mirrored"] is True
    assert rows["Install_Base"]["mirrored"] is True
    for name in ("Deals_X_Products", "Line_Items", "Hidden", "Forecasts", "Visits"):
        assert rows[name]["mirrored"] is False, name
    # A known module stays mirrored, Tasks included (CRM-Q5), and is never generic.
    assert rows["Deals"]["mirrored"] is True and "generic" not in rows["Deals"]
    assert rows["Tasks"]["mirrored"] is True


def test_a_module_whose_scope_is_missing_waits_for_a_reconnect() -> None:
    rows = _rows([_module("Products"), _module("Install_Base", "custom")],
                 scopes=frozenset({"ZohoCRM.modules.products.READ"}))
    assert rows["Products"]["mirrored"] is True
    install = rows["Install_Base"]
    assert install["generic"] is True and install["mirrored"] is False
    assert install["scope"] == "ZohoCRM.modules.custom.READ"
    # With no scopes at all (the profile of CRM-Q1), no generic module mirrors.
    assert _rows([_module("Products")], scopes=frozenset())["Products"]["mirrored"] is False


def test_the_order_is_the_standard_list_then_custom_by_name_and_the_cap_is_25() -> None:
    customs = [_module(f"Custom_{i:02d}", "custom") for i in range(30)]
    modules = [*customs, *(_module(m) for m in reversed(zm.GENERIC_STANDARD_MODULES))]
    profile = zm.build_profile({"modules": modules}, None, ALL_SCOPES)
    taken = [m["api_name"] for m in zm.generic_modules(profile)]
    assert len(taken) == zm.GENERIC_MODULE_CAP == 25
    assert taken[:10] == list(zm.GENERIC_STANDARD_MODULES)
    assert taken[10:] == [f"Custom_{i:02d}" for i in range(15)]
    left = {m["api_name"] for m in profile["modules"] if not m.get("generic")}
    assert left == {f"Custom_{i:02d}" for i in range(15, 30)}


# ── The scopes ───────────────────────────────────────────────────────────────

#: Zoho's published scope list (API v2 and v8, read on 2026-10-12) names each of
#: these. A name that is not on that list makes Zoho refuse the whole consent.
PUBLISHED = {
    "Products": "ZohoCRM.modules.products.READ",
    "Quotes": "ZohoCRM.modules.quotes.READ",
    "Sales_Orders": "ZohoCRM.modules.salesorders.READ",
    "Purchase_Orders": "ZohoCRM.modules.purchaseorders.READ",
    "Invoices": "ZohoCRM.modules.invoices.READ",
    "Vendors": "ZohoCRM.modules.vendors.READ",
    "Price_Books": "ZohoCRM.modules.pricebooks.READ",
    "Cases": "ZohoCRM.modules.cases.READ",
    "Solutions": "ZohoCRM.modules.solutions.READ",
    "Campaigns": "ZohoCRM.modules.campaigns.READ",
}


def test_each_scope_name_is_the_published_one_and_the_consent_asks_for_it() -> None:
    from gateway.routes.crm.connection import ZOHO_READ_SCOPES

    assert set(PUBLISHED) == set(zm.GENERIC_STANDARD_MODULES)
    for module, scope in PUBLISHED.items():
        assert zm.module_read_scope(module, custom=False) == scope
        assert scope in ZOHO_READ_SCOPES, scope
    assert zm.module_read_scope("Install_Base", custom=True) == "ZohoCRM.modules.custom.READ"
    assert "ZohoCRM.modules.custom.READ" in ZOHO_READ_SCOPES
    assert len(ZOHO_READ_SCOPES) == len(set(ZOHO_READ_SCOPES)), "no scope twice"
    assert all(s.endswith(".READ") for s in ZOHO_READ_SCOPES), "the mirror reads only"


# ── Fields ───────────────────────────────────────────────────────────────────


def test_a_module_field_def_keeps_every_field_and_the_lookup_target() -> None:
    v8 = {"api_name": "Vendor_Name", "field_label": "Vendor", "data_type": "lookup",
          "lookup": {"module": {"api_name": "Vendors"}}}
    v2 = {"api_name": "Account", "data_type": "lookup", "lookup": {"module": "Accounts"}}
    owner = {"api_name": "Owner", "data_type": "ownerlookup"}
    row = zm.map_module_field_def("Products", v8, 3)
    assert row is not None
    assert row["entity"] == "module:Products" and row["key"] == "Vendor_Name"
    assert row["options"] == {"lookup_module": "Vendors"}
    v2_row = zm.map_module_field_def("Install_Base", v2, 0)
    assert v2_row is not None and v2_row["options"] == {"lookup_module": "Accounts"}
    # A field a known entity consumes is still a field of a generic module.
    owner_row = zm.map_module_field_def("Products", owner, 1)
    assert owner_row is not None and owner_row["options"] is None
    assert zm.map_module_field_def("Products", {"api_name": "$state"}, 0) is None


def test_the_display_field_comes_from_the_fields_or_the_previous_profile() -> None:
    profile = zm.build_profile(
        {"modules": [_module("Products"), _module("Quotes")]}, None, ALL_SCOPES)
    previous = {"modules": [{"api_name": "Quotes", "display_field": "Subject"}]}
    fields = {"Products": [{"api_name": "Code"},
                           {"api_name": "Product_Name", "display_field": True}]}
    out = {m["api_name"]: m for m in zm.with_module_fields(profile, fields, previous)["modules"]}
    assert out["Products"]["display_field"] == "Product_Name"
    assert out["Quotes"]["display_field"] == "Subject", "a failed read keeps the old one"


# ── One record ───────────────────────────────────────────────────────────────


def test_one_record_maps_to_the_columns_of_the_generic_store() -> None:
    fields = {
        "id": "p1", "Product_Name": "Widget", "Vendor_Name": {"id": "v1", "name": "V"},
        "Owner": {"id": "u1", "name": "Ann", "email": "Ann@Example.com"},
        "Modified_Time": "2026-10-01T10:00:00+05:30", "Created_Time": "2026-01-01T00:00:00Z",
        "$approved": True,
    }
    mapped = zm.map_module_record(
        "Products", fields, modified_at=None, owners={}, display_field="Product_Name")
    assert mapped["name"] == "Widget"
    assert mapped["owner_email"] == "ann@example.com"
    assert mapped["fields"]["Vendor_Name"] == {"id": "v1", "name": "V"}
    assert "id" not in mapped["fields"] and "$approved" not in mapped["fields"]
    assert mapped["ext_modified_at"] == datetime(2026, 10, 1, 4, 30, tzinfo=UTC)
    assert mapped["ext_created_at"] == datetime(2026, 1, 1, tzinfo=UTC)
    # A lookup as the display field gives its name, and no display field gives NULL.
    looked = zm.map_module_record("Quotes", {"Deal": {"id": "d", "name": "Big"}},
                                  modified_at=None, owners={}, display_field="Deal")
    assert looked["name"] == "Big"
    assert zm.map_module_record("X", {"A": "b"}, modified_at=None, owners={},
                                display_field=None)["name"] is None


def test_the_deep_link_of_a_generic_record() -> None:
    meta = {"accounts_server": "https://accounts.zoho.in", "domain_name": "org9"}
    assert zm.module_record_link(meta, "CustomModule3", "77") == (
        "https://crm.zoho.in/crm/org9/tab/CustomModule3/77")
    assert zm.module_record_link({}, "Products", "1") is None
    assert links.module_link("https://evil.example", "org9", "Products", "1") is None
    assert links.module_link("https://accounts.zoho.in", "org9", "a/b", "1") is None


# ── The adapter ──────────────────────────────────────────────────────────────


def test_the_adapter_reads_a_generic_module_by_its_api_name() -> None:
    assert client._module("module:Products") == "Products"
    assert client._module("module:Install_Base") == "Install_Base"
    assert client._module("deal") == "Deals"
    for bad in ("module:", "module:../x", "module:a b", "module:1abc"):
        with pytest.raises(ValueError):
            client._module(bad)


@pytest.mark.parametrize("entity,scope", [
    ("module:Sales_Orders", "ZohoCRM.modules.salesorders.READ"),
    ("module:Price_Books", "ZohoCRM.modules.pricebooks.READ"),
    ("module:Install_Base", "ZohoCRM.modules.custom.READ"),
    ("deal", "ZohoCRM.modules.deals.READ"),
])
async def test_a_scope_refusal_names_the_published_scope(entity: str, scope: str) -> None:
    """The error of a refused read names the scope an admin must grant."""
    from tests.unit.test_crm_sources_zoho import Fake, ok

    refused = ok({"code": "OAUTH_SCOPE_MISMATCH", "message": "invalid oauth scope"}, 401)
    with pytest.raises(client.ZohoScopeError) as caught:
        await Fake([refused]).source().list_changed(entity)
    assert caught.value.scope == scope
