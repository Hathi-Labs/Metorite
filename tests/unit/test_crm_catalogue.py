"""The CRM catalogue by code (WS-53 CRM-Q31-1, ``crm_platform.md`` §18.13.4).

Database-free, so it never skips. The behaviour against a real Postgres is in
``test_crm_catalogue_r8.py``.

1. The vocabulary, and the type table of the key parts.
2. Code rules 1 to 10 on synthetic data with DEWiN's shape. Each name of a
   module and a field is invented here, as ``evals/crm_mapping`` does.
3. The stored ``catalogue`` items: the order of the answers, and ``stale``.
4. ``crm_list_modules``: the catalogue lines and the money block, and the
   answer of today, byte for byte, for an org outside ``CRM_CATALOGUE_ORGS``.
5. The flag, and the line of the addendum.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from gateway.crm_sync import catalogue as cat

_REPO = Path(__file__).resolve().parents[2]
_CATALOGUE = _REPO / "apps" / "services" / "gateway" / "gateway" / "crm_sync" / "catalogue.py"


def _f(key: str, kind: str, target: str | None = None) -> cat.Field:
    return cat.Field(key=key, label=key.replace("_", " "), type=kind, target=target)


def _module(api: str, *, label: str | None = None, records: int = 10,
            fields: tuple[cat.Field, ...] = (), fills: dict | None = None,
            custom: bool = True, singular: str | None = None) -> cat.Source:
    return cat.Source(
        module=api, entity=f"module:{api}", kind="module", label=label or api,
        records=records, singular=singular, custom=custom, fields=fields,
        fills=fills,
    )


def _entity(module: str, slug: str, *, label: str | None = None, records: int = 5,
            standard: bool = True, fields: tuple[cat.Field, ...] = ()) -> cat.Source:
    return cat.Source(
        module=module, entity=slug, kind="entity", label=label or module,
        records=records, standard_label=standard, fields=fields,
    )


def _one(sources: list[cat.Source], module: str, **kw: Any) -> cat.Entry:
    found = cat.build(sources, kw.get("roles"), kw.get("items", ()))
    return next(e for e in found if e.module == module)


# ── 1. The vocabulary ────────────────────────────────────────────────────────


def test_the_purposes_are_the_fixed_list_in_order() -> None:
    assert cat.PURPOSES == (
        "customer_company", "customer_site", "person", "lead", "deal", "contract",
        "invoice", "payment_in", "receivable", "payment_out", "product", "asset",
        "vendor", "partner", "activity", "reference", "other",
    )
    assert {
        "deal", "contract", "invoice", "payment_in", "receivable", "payment_out",
    } == cat.MONEY_PURPOSES
    for words in (cat.STANDARD_PURPOSES.values(), cat.PURPOSE_WORDS,
                  cat.CORE_PURPOSES.values(), cat.SLOT_PURPOSES.values()):
        assert set(words) <= set(cat.PURPOSES)


def test_the_part_types_are_the_types_of_the_aggregate() -> None:
    """One type rule for the catalogue and the key words of the aggregate."""
    from gateway.routes.crm import field_sql as fs

    assert cat.PART_TYPES["amount"] == fs.MEASURE_TYPES
    assert cat.PART_TYPES["date"] == fs.DATE_TYPES
    assert cat.PART_TYPES["status"] == ("select",)
    assert cat.PART_TYPES["parent"] == ("lookup",)


def test_the_rules_read_no_database_and_call_no_model() -> None:
    """Done-when 1: pure functions. No session, no SQL, no ``await``."""
    source = _CATALOGUE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef | ast.Await)]
    imported = {
        n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module
    } | {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not {m for m in imported if "sqlalchemy" in m or "decide" in m or "db" in m}
    assert "text(" not in source


# ── 2. Code rules 1 to 5: the purpose ────────────────────────────────────────


def test_rule_1_a_core_entity_keeps_its_purpose_while_its_label_is_standard() -> None:
    sources = [
        _entity("Leads", "leads"), _entity("Accounts", "organizations"),
        _entity("Contacts", "contacts", label="Field Sites", standard=False),
    ]
    entries = {e.module: e for e in cat.build(sources)}
    assert entries["Leads"].purpose == "lead"
    assert entries["Accounts"].purpose == "customer_company"
    # A renamed core entity gets one purpose question.
    assert entries["Contacts"].purpose is None
    assert entries["Contacts"].open == ["purpose"]


def test_rule_1_the_deal_takes_its_fixed_columns() -> None:
    deal_fields = (
        _f("amount", "currency"), _f("expected_close_date", "date"),
        _f("status_id", "select"), _f("company_id", "lookup", "Accounts"),
        _f("name", "text"),
    )
    entry = _one([_entity("Deals", "deals", fields=deal_fields)], "Deals")
    assert entry.purpose == "deal"
    assert {p: f.key for p, f in entry.fields.items()} == {
        "amount": "amount", "date": "expected_close_date",
        "status": "status_id", "parent": "company_id",
    }
    assert set(entry.by.values()) == {"code"} and entry.open == []


def test_rule_2_a_standard_module_takes_the_purpose_of_its_api_name() -> None:
    for api, purpose in (("Invoices", "invoice"), ("Quotes", "deal"),
                         ("Sales_Orders", "contract"), ("Vendors", "vendor"),
                         ("Price_Books", "reference")):
        assert _one([_module(api, custom=False)], api).purpose == purpose
    # A custom module is never read as a standard one by its API name.
    assert _one([_module("Products", custom=True, label="Spares")], "Products").purpose is None


@pytest.mark.parametrize(("api", "label", "purpose"), [
    ("Buyer_Clients", "Clients", "customer_company"),
    ("Clients", "Clients", "customer_company"),
    ("Fee_Receipts", "Collection", "payment_in"),
    ("Payment_Received", "Receipts", "payment_in"),
    ("Payment_Made", "Payouts", "payment_out"),
    ("Spend_Log", "Expenses", "payment_out"),
    ("Dues", "Outstanding", "receivable"),
    ("Channel_Partners", "Partners", "partner"),
    ("Client_Visits", "Client Visits", None),
    ("Towers", "Towers", None),
])
def test_rule_3_a_custom_module_takes_a_purpose_from_its_whole_name(
    api: str, label: str, purpose: str | None,
) -> None:
    assert _one([_module(api, label=label)], api).purpose == purpose


def test_rule_3_two_purposes_in_one_name_give_none() -> None:
    entry = _one([_module("Clients", label="Collection")], "Clients")
    assert entry.purpose is None and entry.open == ["purpose"]


def test_rule_4_the_role_wins_over_each_other_answer() -> None:
    items = [{"kind": "catalogue", "module": "Clients", "purpose": "vendor",
              "by": {"purpose": "admin"}}]
    entry = _one([_module("Clients")], "Clients", roles={"deal": "Clients"}, items=items)
    assert (entry.purpose, entry.by["purpose"]) == ("deal", "role")


def test_rule_5_a_module_with_no_records_gets_no_question() -> None:
    empty = _one([_module("Towers", records=0)], "Towers")
    assert empty.purpose is None and empty.open == []
    quotes = _one([_module("Quotes", custom=False, records=0)], "Quotes")
    assert quotes.purpose == "deal" and quotes.open == [] and quotes.fields == {}


# ── Code rules 6 to 10: the key parts ────────────────────────────────────────


def _invoices(fields: tuple[cat.Field, ...], fills: dict | None) -> cat.Entry:
    return _one([_module("Invoices", custom=False, fields=fields, fills=fills,
                         singular="Invoice")], "Invoices")


def test_rule_6_a_standard_amount_name_wins_and_needs_half_the_records() -> None:
    fields = (_f("Grand_Total", "currency"), _f("Net_Value", "currency"),
              _f("Total", "currency"))
    entry = _invoices(fields, {"Grand_Total": 0.6, "Net_Value": 0.9, "Total": 0.95})
    assert entry.fields["amount"].key == "Grand_Total"
    # Below half, the standard name gives way to the next rule.
    entry = _invoices(fields, {"Grand_Total": 0.4, "Net_Value": 0.9, "Total": 0.3})
    assert entry.fields["amount"].key == "Net_Value"


def test_rule_6_the_singular_amount_and_the_one_currency_field() -> None:
    named = _invoices((_f("Invoice_Amount", "currency"), _f("Tax", "currency")),
                      {"Invoice_Amount": 0.7, "Tax": 0.8})
    assert named.fields["amount"].key == "Invoice_Amount"
    one = _invoices((_f("Billed_Value", "currency"), _f("Rebate", "currency")),
                    {"Billed_Value": 0.8, "Rebate": 0.1})
    assert one.fields["amount"].key == "Billed_Value"


def test_rule_6_ten_filled_currency_fields_leave_the_amount_open() -> None:
    """DEWiN's shape: many currency fields over half, and no standard name."""
    fields = tuple(_f(f"Charge_{i}", "currency") for i in range(10))
    entry = _invoices(fields, {f.key: 0.7 for f in fields})
    assert "amount" not in entry.fields and "amount" in entry.open


def test_rule_7_the_date_named_like_the_module_then_the_one_full_date() -> None:
    fields = (_f("Invoice_Date", "date"), _f("Due_On", "date"),
              _f("Created_Time", "datetime"))
    entry = _invoices(fields, {"Invoice_Date": 1.0, "Due_On": 0.95, "Created_Time": 1.0})
    assert entry.fields["date"].key == "Invoice_Date"
    # With no named date, the one date over 90% wins. A system time never counts.
    entry = _invoices(fields[1:], {"Due_On": 0.95, "Created_Time": 1.0})
    assert entry.fields["date"].key == "Due_On"
    entry = _invoices((_f("Due_On", "date"), _f("Paid_On", "datetime")),
                      {"Due_On": 0.95, "Paid_On": 0.99})
    assert "date" in entry.open


def test_rule_8_the_one_status_over_half() -> None:
    """DEWiN's shape: ``Status`` at 42% and ``Status3`` at 63% give no status."""
    entry = _invoices((_f("Status", "select"), _f("Status3", "select")),
                      {"Status": 0.42, "Status3": 0.63})
    assert "status" in entry.open
    entry = _invoices((_f("Invoice_Stage", "select"), _f("Stage", "text")),
                      {"Invoice_Stage": 0.8, "Stage": 1.0})
    assert entry.fields["status"].key == "Invoice_Stage"


def test_rule_9_the_parent_is_the_one_lookup_to_a_customer() -> None:
    fields = (_f("Buyer", "lookup", "Clients"), _f("Site_Contact", "lookup", "Contacts"),
              _f("Crew", "lookup", "Crews"))
    sources = [
        _module("Invoices", custom=False, fields=fields, fills={}),
        _module("Clients"), _module("Crews"),
        _entity("Contacts", "contacts"),
    ]
    entry = _one(sources, "Invoices")
    assert entry.fields["parent"].key == "Buyer"
    assert [link.key for link in entry.links] == ["Buyer", "Site_Contact", "Crew"]
    # With no company lookup, the one lookup to a person or a site.
    sources[0] = _module("Invoices", custom=False, fields=fields[1:], fills={})
    assert _one(sources, "Invoices").fields["parent"].key == "Site_Contact"
    # Two lookups to a company: no sure parent.
    two = (_f("Buyer", "lookup", "Clients"), _f("Payer", "lookup", "Clients"))
    sources[0] = _module("Invoices", custom=False, fields=two, fills={})
    assert "parent" in _one(sources, "Invoices").open


def test_rule_10_no_fill_row_leaves_each_fill_rule_open() -> None:
    """A module with no fill row: rules 6 to 8 skip, and the parent stays."""
    fields = (_f("Grand_Total", "currency"), _f("Invoice_Date", "date"),
              _f("Status", "select"), _f("Buyer", "lookup", "Clients"))
    entry = _one([_module("Invoices", custom=False, fields=fields, fills=None),
                  _module("Clients")], "Invoices")
    assert entry.open == ["amount", "date", "status"]
    assert entry.fields == {"parent": fields[3]}


def test_a_purpose_that_holds_no_money_gets_no_key_part() -> None:
    entry = _one([_module("Products", custom=False,
                          fields=(_f("Unit_Price", "currency"),),
                          fills={"Unit_Price": 1.0})], "Products")
    assert entry.purpose == "product" and entry.fields == {} and entry.open == []


# ── 3. A stored catalogue item ───────────────────────────────────────────────


def test_a_stored_part_wins_and_a_stale_one_gives_way_to_code() -> None:
    fields = (_f("Billed_Value", "currency"), _f("Invoice_Date", "date"),
              _f("Status", "select"))
    fills = {"Billed_Value": 0.8, "Invoice_Date": 1.0, "Status": 0.9}
    items = [{"kind": "catalogue", "module": "Invoices", "amount": "Net_Value",
              "date": "Status", "status": None,
              "by": {"amount": "ai", "date": "admin", "status": "ai"}},
             {"kind": "role", "slot": "deal", "module": "Other"}]
    entry = _one([_module("Invoices", custom=False, fields=fields, fills=fills)],
                 "Invoices", items=items)
    # The amount field is gone and the date has another type: both stale, and
    # code answers them. The status was asked, and no answer was sure.
    assert entry.stale == ["amount", "date"]
    assert entry.fields["amount"].key == "Billed_Value" and entry.by["amount"] == "code"
    assert entry.fields["date"].key == "Invoice_Date"
    assert entry.fields["status"] is None and entry.by["status"] == "ai"


def test_a_stored_admin_part_and_a_stored_purpose() -> None:
    fields = (_f("Billed_Value", "currency"), _f("Rebate", "currency"))
    items = [{"kind": "catalogue", "module": "Towers", "purpose": "invoice",
              "amount": "Rebate", "by": {"purpose": "ai", "amount": "admin"}}]
    entry = _one([_module("Towers", fields=fields, fills={"Billed_Value": 0.9})],
                 "Towers", items=items)
    assert (entry.purpose, entry.by["purpose"]) == ("invoice", "ai")
    assert (entry.fields["amount"].key, entry.by["amount"]) == ("Rebate", "admin")
    bad = [{"kind": "catalogue", "module": "Clients", "purpose": "galaxy"}]
    entry = _one([_module("Clients")], "Clients", items=bad)
    assert entry.purpose == "customer_company" and entry.stale == ["purpose"]


# ── 4. The tool ──────────────────────────────────────────────────────────────

import acb_skills.crm_tools as tools  # noqa: E402


class _Resp:
    def __init__(self, status: int, payload: Any) -> None:
        self.status_code, self._payload, self.text = status, payload, ""

    def json(self) -> Any:
        return self._payload


def _gateway(monkeypatch, answer) -> list[str]:
    """The tool's transport, faked at the client. *answer* maps a path to
    ``(status, payload)``."""
    seen: list[str] = []

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        async def request(self, method, url, **_kw):
            path = "/" + url.split("/", 3)[-1]
            seen.append(path)
            return _Resp(*answer(path))

    monkeypatch.setattr(tools, "httpx", SimpleNamespace(AsyncClient=lambda **_kw: _Client()))
    monkeypatch.setattr(tools, "_current_user_email", lambda: "sales@q31.test")
    return seen


_OFF = (404, {"detail": {"code": "catalogue_off",
                         "message": "The catalogue is off for this organization."}})

#: The answer of ``crm_list_modules`` before CRM-Q31-1 for these modules,
#: captured from the code at ``origin/main`` 4a0a41c24.
_MODULES = [{"label": "Products", "api_name": "Products", "count": 3},
            {"label": "Collection", "api_name": "Payment_Received", "count": 8681}]
_TODAY = (
    "Modules:\n- Products (api_name Products): 3 records\n"
    "- Collection (api_name Payment_Received): 8681 records\n"
    "To total a module, call crm_aggregate with entity 'module:<api_name>'."
)


async def test_an_org_outside_the_list_gets_the_answer_of_today_byte_for_byte(
    monkeypatch,
) -> None:
    """Narrowing 1: the catalogue is dark in each mode. Off, the tool reads
    ``/crm/modules`` and answers exactly as before."""
    seen = _gateway(monkeypatch, lambda p: _OFF if p == "/crm/catalogue" else (200, _MODULES))
    assert await tools.crm_list_modules() == _TODAY
    assert seen == ["/crm/catalogue", "/crm/modules", "/crm/sync/short-reads"]
    _gateway(monkeypatch, lambda p: _OFF if p == "/crm/catalogue" else (200, []))
    assert await tools.crm_list_modules() == "This CRM mirrors no other module."


async def test_another_failure_of_the_catalogue_is_no_fallback(monkeypatch) -> None:
    _gateway(monkeypatch, lambda p: (500, {"detail": {"code": "boom", "message": "x"}}))
    # PR #1126: the 500 is a refusal the model reads, never the fallback.
    out = await tools.crm_list_modules()
    assert out.startswith("Refused:") and "(500)" in out
    assert "Gateway said" not in out
    # The kill switch is still the answer, not a fallback.
    _gateway(monkeypatch, lambda p: (404, {"detail": "Not Found"}))
    assert await tools.crm_list_modules() == tools.CRM_OFF


def _kf(key: str, label: str, kind: str, target: str | None = None) -> dict:
    return {"key": key, "label": label, "type": kind, "target": target}


_ENTRIES = [
    {"entity": "contacts", "module": "Contacts", "kind": "entity",
     "label": "Field Sites", "records": 805, "purpose": "customer_site",
     "fields": {}, "links": [], "open": []},
    {"entity": "deals", "module": "Deals", "kind": "entity", "label": "Deals",
     "records": 0, "purpose": "deal", "fields": {}, "links": [], "open": []},
    {"entity": "module:Invoices", "module": "Invoices", "kind": "module",
     "label": "Invoices", "records": 30177, "purpose": "invoice",
     "fields": {
         "amount": _kf("Billed_Value", "Billed Value", "currency"),
         "date": _kf("Invoice_Date", "Invoice Date", "date"),
         "parent": _kf("Buyer", "Buyer", "lookup", "Clients"),
     },
     "links": [_kf("Buyer", "Buyer", "lookup", "Clients"),
               _kf("Site_Contact", "Site Contact", "lookup", "Contacts")],
     "open": ["status"]},
    {"entity": "module:Fee_Receipts", "module": "Fee_Receipts", "kind": "module",
     "label": "Collection", "records": 8681, "purpose": "payment_in",
     "fields": {"amount": None, "date": _kf("Paid_On", "Paid On", "date")},
     "links": [], "open": []},
    {"entity": "module:Spend_Log", "module": "Spend_Log", "kind": "module",
     "label": "Expenses", "records": 7, "purpose": "payment_out", "fields": {},
     "links": [], "open": []},
    {"entity": "module:Bills_Out", "module": "Bills_Out", "kind": "module",
     "label": "Bills", "records": 1, "purpose": "payment_out", "fields": {},
     "links": [], "open": []},
    {"entity": "module:Quotes", "module": "Quotes", "kind": "module",
     "label": "Quotes", "records": 0, "purpose": "deal", "fields": {},
     "links": [], "open": []},
    {"entity": "module:Towers", "module": "Towers", "kind": "module",
     "label": "Towers", "records": 100, "purpose": None, "fields": {},
     "links": [], "open": ["purpose"]},
]


async def test_the_catalogue_renders_one_line_for_each_entry_and_the_money(
    monkeypatch,
) -> None:
    seen = _gateway(monkeypatch, lambda p: (200, {"mode": "mirror", "entries": _ENTRIES}))
    out = await tools.crm_list_modules()
    assert seen == ["/crm/catalogue", "/crm/sync/short-reads"]
    lines = out.splitlines()
    assert lines[1] == "- Field Sites (entity contacts): customer_site, 805 records."
    assert lines[2] == "- Deals (entity deals): deal, no records."
    assert lines[3] == (
        "- Invoices (module:Invoices): invoice, 30,177 records. Amount Billed Value "
        "(Billed_Value). Date Invoice Date (Invoice_Date). Status: not known. "
        "Customer Buyer (Buyer -> Clients). Links: Site_Contact -> Contacts."
    )
    assert lines[4] == (
        "- Collection (module:Fee_Receipts): payment_in, 8,681 records. Amount: no "
        "single field. Date Paid On (Paid_On)."
    )
    assert "- Bills (module:Bills_Out): payment_out, 1 record." in lines
    # Done-when 9: a module with 0 records shows "no records", and no question.
    assert "- Quotes (module:Quotes): deal, no records." in lines
    assert "- Towers (module:Towers): 100 records." in lines
    # The money block names only an entry that holds records.
    assert lines[-2] == (
        "Money: billed in Invoices. Received in Collection. Paid out in Expenses "
        "and Bills."
    )
    assert lines[-1] == tools.CATALOGUE_TOTAL_HINT


async def test_the_catalogue_pages_fifty_entries(monkeypatch) -> None:
    many = [{"entity": f"module:M{i}", "module": f"M{i}", "kind": "module",
             "label": f"M{i}", "records": 1, "purpose": None} for i in range(60)]
    _gateway(monkeypatch, lambda p: (200, {"mode": "mirror", "entries": many}))
    first = await tools.crm_list_modules()
    assert "(module:M49)" in first and "(module:M50)" not in first
    assert "Call again with page=2" in first
    last = await tools.crm_list_modules(page=2)
    assert "(module:M59)" in last and "(module:M0)" not in last


# ── 5. The flag, and the addendum ────────────────────────────────────────────


@pytest.mark.parametrize(("value", "org", "allowed"), [
    ("", "a1", False), ("*", "a1", True), ("a1, b2", "B2", True), ("b2", "a1", False),
])
def test_the_catalogue_flag(monkeypatch, value: str, org: str, allowed: bool) -> None:
    from acb_common import Settings, get_settings
    from gateway.routes.crm import flags

    from tests.unit._crm_fakes import crm_switch_on

    assert Settings.model_fields["crm_catalogue_orgs"].default == ""
    crm_switch_on(monkeypatch, "*")
    monkeypatch.setattr(get_settings(), "crm_catalogue_orgs", value, raising=False)
    assert flags.catalogue_allowed(org) is allowed
    # The CRM switch comes first, also with "*".
    monkeypatch.setattr(get_settings(), "crm_enabled", False, raising=False)
    assert flags.catalogue_allowed(org) is False
    assert flags.catalogue_allowed(None) is False


def test_the_addendum_tells_the_model_to_read_the_catalogue_first() -> None:
    from acb_skills.addendum import render_injected_tools_addendum

    text = render_injected_tools_addendum(
        is_sub_agent=False, effective_scope=frozenset({"crm_list_modules"}),
        registry_block="", risk_block="",
    )
    assert ("Before a total over a module, call crm_list_modules. Name the module "
            "and the fields that each number comes from.") in text


# ── 6. The kind `catalogue` in the plan (CRM-Q31-3) ─────────────────────────


def test_rule_4_of_the_kinds_checks_a_catalogue_item_and_needs_no_role() -> None:
    """Item 9: kind_key and check_kinds know the kind together. A catalogue
    item never falls into the role_missing check."""
    from fastapi import HTTPException
    from gateway.routes.crm import mapping_plans as mpl

    good = {"kind": "catalogue", "module": "Fee_Receipts", "purpose": "payment_in",
            "amount": None, "date": "Paid_On", "by": {"amount": "ai", "date": "code"}}
    assert mpl.kind_key(good) == ("catalogue", "Fee_Receipts")
    mpl.check_kinds([good])
    for bad, code in (
        ({**good, "purpose": "galaxy"}, "bad_catalogue"),
        ({**good, "amount": 7}, "bad_catalogue"),
        ({**good, "by": {"amount": "robot"}}, "bad_catalogue"),
        ({**good, "module": ""}, "bad_catalogue"),
    ):
        with pytest.raises(HTTPException) as exc:
            mpl.check_kinds([bad])
        assert exc.value.detail["code"] == code
    with pytest.raises(HTTPException) as exc:
        mpl.check_kinds([good, {**good, "purpose": "invoice"}])
    assert exc.value.detail["code"] == "duplicate"
    merged = mpl.merge_kinds([good], [{**good, "purpose": "invoice"}])
    assert [i["purpose"] for i in merged] == ["invoice"]


def test_a_merge_keeps_each_part_of_the_admin() -> None:
    """Item 14: the pipeline never replaces a part with by: admin."""
    old = {"kind": "catalogue", "module": "Dues", "amount": "Due_Value", "purpose": "receivable",
           "by": {"amount": "admin", "purpose": "ai"}}
    new = {"kind": "catalogue", "module": "Dues", "amount": "Late_Fee", "purpose": "invoice",
           "date": "Due_On", "by": {"amount": "ai", "purpose": "ai", "date": "ai"}}
    got = cat.merge_item(old, new)
    assert (got["amount"], got["by"]["amount"]) == ("Due_Value", "admin")
    assert (got["purpose"], got["date"]) == ("invoice", "Due_On")
    admin = cat.merge_item(old, {**new, "by": {"amount": "admin"}})
    assert admin["amount"] == "Late_Fee"
    assert cat.without_catalogue([old, {"kind": "role"}]) == [{"kind": "role"}]


# ── 7. Fix round (CRM-Q31 review) ────────────────────────────────────────────


async def test_a_bare_404_of_the_catalogue_gives_the_answer_of_today(monkeypatch) -> None:
    """Item 7: during a deploy a gateway has no /crm/catalogue yet. Its bare
    404 is no kill switch: the tool answers from /crm/modules, as before."""
    seen = _gateway(monkeypatch, lambda p: (404, {"detail": "Not Found"})
                    if p == "/crm/catalogue" else (200, _MODULES))
    assert await tools.crm_list_modules() == _TODAY
    assert seen == ["/crm/catalogue", "/crm/modules", "/crm/sync/short-reads"]


async def test_the_empty_core_line_survives_a_bare_404(monkeypatch) -> None:
    """Item 7: a total stands when the catalogue read gives a bare 404."""
    def answer(path):
        if path == "/crm/catalogue":
            return 404, {"detail": "Not Found"}
        return 200, {"entity": "deals", "measure": "count", "groups": [], "total": 0,
                     "other": 0, "omitted": 0}

    _gateway(monkeypatch, answer)
    assert await tools.crm_aggregate("deals", group_by="owner") == (
        "Deals, count by owner:\nTotal: 0.")


def test_a_dark_org_keeps_the_refusal_of_before() -> None:
    """Item 9: outside CRM_CATALOGUE_ORGS no message points at key fields."""
    from fastapi import HTTPException
    from gateway.routes.crm import field_sql as fs

    dark = fs.FieldScope(column="fields", owner="module:X", fields={}, catalogue=False)
    with pytest.raises(HTTPException) as exc:
        dark.ref("@amount")
    assert exc.value.detail == "'sum_amount' needs deals. A module has no amount."
    with pytest.raises(HTTPException) as exc:
        dark.ref("@date")
    assert exc.value.detail["code"] == "unknown_field"
    lit = fs.FieldScope(column="fields", owner="module:X", fields={})
    with pytest.raises(HTTPException) as exc:
        lit.ref("@amount")
    assert exc.value.detail["code"] == "no_key_field"


def test_the_overlay_reads_the_catalogue_once() -> None:
    """Item 5: overlay_of loads no catalogue entry. module_scope is the one
    read of it in a total."""
    import dataclasses

    from gateway.routes.crm import overlay

    assert "entry" not in {f.name for f in dataclasses.fields(overlay.Overlay)}
    source = Path(overlay.__file__).read_text(encoding="utf-8")
    assert "entry_of(" not in source


# ── 8. An amount can be the sum of several fields (CRM-Q32-9a) ─────────────
#
# crm_platform.md §18.14.10. DEWiN's Collection splits each payment into
# parts. The keys below are the keys of the spec. The labels and the fills
# are invented.

_SUM_KEYS = ["Billing_Amount", "IP", "EB_Net_Amount"]


def _collection(**more: Any) -> cat.Source:
    fields = (
        cat.Field("Billing_Amount", "Rent Net Amount", "currency"),
        cat.Field("IP", "IP Basic Amount", "currency"),
        cat.Field("EB_Net_Amount", "EB Net Amount", "currency"),
        cat.Field("Rate", "Rate", "percent"),
        cat.Field("Units", "Units", "number"),
        cat.Field("Paid_On", "Paid On", "date"),
        *more.get("extra", ()),
    )
    return _module("Payment_Received", label="Collection", records=8681, fields=fields,
                   fills={"Billing_Amount": 0.52, "IP": 0.52, "EB_Net_Amount": 0.46,
                          "Rate": 0.4, "Units": 0.3, "Paid_On": 0.98})


def _summed(value: Any, *, by: Any = "admin", part: str = "amount") -> dict[str, Any]:
    words = {"purpose": "admin"}
    if by is not None:
        words[part] = by
    return {"kind": "catalogue", "module": "Payment_Received", "purpose": "payment_in",
            part: value, "by": words}


def test_item_problem_takes_a_sum_of_amount_by_the_admin_only() -> None:
    """Item 1: 2 to 5 distinct keys, for amount only, with by admin."""
    assert cat.item_problem(_summed(_SUM_KEYS)) is None
    assert cat.item_problem(_summed(["A", "B"])) is None
    assert cat.item_problem(_summed(["A", "B", "C", "D", "E"])) is None
    for bad in (
        _summed(["A"]),                                # 1 key
        _summed(["A", "B", "C", "D", "E", "F"]),       # 6 keys
        _summed(["A", "B", "A"]),                      # a duplicate key
        _summed(["A", "B"], by="ai"),                  # by: ai
        _summed(["A", "B"], by="code"),
        _summed(["A", "B"], by=None),                  # no by at all
        _summed([]),
        _summed(["A", 7]),
        _summed(["A", "@amount"]),
        _summed(["A", "B+C"]),
        _summed(["A", "x" * 300]),
    ):
        assert cat.item_problem(bad) == "amount", bad
    assert cat.item_problem(_summed(["Paid_On", "Paid_On_2"], part="date")) == "date"
    assert cat.item_problem(_summed(["A", "B"], part="status")) == "status"


def test_the_plan_check_takes_a_sum_by_the_admin_and_refuses_one_by_ai() -> None:
    """Item 1 through check_kinds, the check of each plan commit."""
    from fastapi import HTTPException
    from gateway.routes.crm import mapping_plans as mpl

    mpl.check_kinds([_summed(_SUM_KEYS)])
    with pytest.raises(HTTPException) as exc:
        mpl.check_kinds([_summed(_SUM_KEYS, by="ai")])
    assert exc.value.detail["code"] == "bad_catalogue"


def test_build_reads_a_sum_into_one_composite_field() -> None:
    """Item 2: the key, the label, the type and each part."""
    entry = _one([_collection()], "Payment_Received", items=[_summed(_SUM_KEYS)])
    amount = entry.fields["amount"]
    assert amount is not None
    assert amount.key == "Billing_Amount+IP+EB_Net_Amount"
    assert amount.label == "Rent Net Amount + IP Basic Amount + EB Net Amount"
    assert amount.type == "currency"
    assert [p.key for p in amount.parts] == _SUM_KEYS
    assert [p.label for p in amount.parts] == [
        "Rent Net Amount", "IP Basic Amount", "EB Net Amount"]
    assert (entry.by["amount"], entry.stale) == ("admin", [])
    assert "amount" not in entry.open


@pytest.mark.parametrize("keys", [
    ["Billing_Amount", "Gone"],          # a key that is gone
    ["Billing_Amount", "Rate"],          # a percent, not a currency
    ["Billing_Amount", "Units"],         # a number, not a currency
    ["Billing_Amount", "Paid_On"],       # a date
])
def test_a_gone_or_unfit_key_makes_the_whole_sum_stale(keys: list[str]) -> None:
    """Item 2: one bad key, and the part is stale. The read never holds a
    composite with a missing part."""
    entry = _one([_collection()], "Payment_Received", items=[_summed(keys)])
    assert "amount" in entry.stale
    found = entry.fields.get("amount")
    assert found is None or not found.parts


def test_problem_and_resolve_of_a_list() -> None:
    src = _collection()
    assert cat.problem(src, "amount", _SUM_KEYS) is None
    assert cat.problem(src, "amount", ["Billing_Amount", "Gone"]) == "gone"
    assert cat.problem(src, "amount", ["Billing_Amount", "Rate"]) == "type"
    assert cat.problem(src, "date", ["Paid_On", "Paid_On"]) == "type"
    assert cat.resolve(src, None) is None
    assert cat.resolve(src, "IP") == src.field("IP")
    assert cat.resolve(src, _SUM_KEYS).parts == tuple(src.field(k) for k in _SUM_KEYS)


def test_merge_keeps_a_sum_of_the_admin_and_never_takes_one_from_the_pipeline() -> None:
    """Code and the pipeline never write a list. A merge of the admin keeps
    its own copy, so a later change of the input does not move the plan."""
    keys = list(_SUM_KEYS)
    got = cat.merge_item(None, _summed(keys))
    keys.append("Changed")
    assert got["amount"] == _SUM_KEYS and got["amount"] is not keys
    ai = {"kind": "catalogue", "module": "Payment_Received", "amount": "IP",
          "by": {"amount": "ai"}}
    assert cat.merge_item(got, ai)["amount"] == _SUM_KEYS
    pipeline_list = {**ai, "amount": ["IP", "EB_Net_Amount"]}
    assert "amount" not in cat.merge_item(None, pipeline_list)
    old = {"kind": "catalogue", "module": "Payment_Received", "amount": "IP",
           "by": {"amount": "ai"}}
    assert cat.merge_item(old, pipeline_list)["amount"] == "IP"
    admin = cat.merge_item(old, _summed(["IP", "EB_Net_Amount"]))
    assert (admin["amount"], admin["by"]["amount"]) == (["IP", "EB_Net_Amount"], "admin")


def test_auto_takes_a_sum_back_to_the_answer_of_the_pipeline() -> None:
    """The value "@auto" on a sum: the cached item of the pipeline comes
    back, and a list in the cache is no answer of the pipeline."""
    stored = cat.merge_item(None, _summed(_SUM_KEYS))
    cached = {"kind": "catalogue", "module": "Payment_Received", "amount": "IP",
              "by": {"amount": "ai"}}
    back = cat.clear_parts(stored, ["amount"], cached)
    assert (back["amount"], back["by"]["amount"]) == ("IP", "ai")
    gone = cat.clear_parts(stored, ["amount"], {**cached, "amount": _SUM_KEYS})
    assert "amount" not in gone and "amount" not in gone["by"]


def test_the_keys_of_an_answer_and_a_put_take_a_list() -> None:
    """Item 8: check_parts and _bad_field take a list for amount only. Each
    key must be a currency field of the module. Each bad list is a 422
    bad_answer, and the shape is refused before any field is read."""
    from fastapi import HTTPException
    from gateway.routes.crm.catalogue import _bad_field, check_parts

    src = _collection()
    check_parts("Payment_Received", {"amount": _SUM_KEYS}, src)
    check_parts("Payment_Received", {"amount": ["IP", "EB_Net_Amount"]}, src)
    assert _bad_field(src, "amount", ["IP", "EB_Net_Amount"]) is False
    for parts in (
        {"amount": ["IP"]},
        {"amount": ["IP", "EB_Net_Amount", "Billing_Amount", "A", "B", "C"]},
        {"amount": ["IP", "IP"]},
        {"amount": ["IP", "Rate"]},
        {"amount": ["IP", "Gone"]},
        {"amount": ["IP", "a'--"]},
        {"amount": ["IP", "@amount"]},
        {"amount": ["IP", "x" * 300]},
        {"date": ["Paid_On", "IP"]},
        {"status": ["IP", "EB_Net_Amount"]},
    ):
        with pytest.raises(HTTPException) as exc:
            check_parts("Payment_Received", parts, src)
        assert exc.value.status_code == 422, parts
        assert exc.value.detail["code"] == "bad_answer", parts

    class _NoRead:
        def field(self, key: str) -> Any:
            raise AssertionError("a bad shape must not reach a read of the fields")

    assert _bad_field(_NoRead(), "amount", ["IP", "x" * 300]) is True
    assert _bad_field(_NoRead(), "amount", ["IP"]) is True


def test_the_answer_the_put_and_undo_take_a_list_on_the_wire() -> None:
    """Item 8: AnswerIn, UndoModel and CatalogueChangeIn take a list."""
    from gateway.routes.crm.catalogue import CatalogueChangeIn
    from gateway.routes.crm.questions import AnswerIn, UndoModel
    from pydantic import ValidationError

    assert AnswerIn(value=_SUM_KEYS).value == _SUM_KEYS
    assert AnswerIn(value="IP").value == "IP" and AnswerIn(value=None).value is None
    undo = UndoModel(module="Payment_Received", part="amount", value=_SUM_KEYS)
    assert undo.value == _SUM_KEYS
    assert UndoModel(value="@auto").value == "@auto"
    assert CatalogueChangeIn(amount=_SUM_KEYS).amount == _SUM_KEYS
    assert CatalogueChangeIn(amount="IP").amount == "IP"
    with pytest.raises(ValidationError):
        AnswerIn(value=[f"K{i}" for i in range(9)])
    with pytest.raises(ValidationError):
        CatalogueChangeIn(purpose=["deal", "invoice"])


def test_the_wire_of_a_sum_carries_its_parts() -> None:
    """KeyField of the wire gains parts. A plain field has none."""
    from gateway.routes.crm.catalogue import _wire

    entry = _one([_collection()], "Payment_Received", items=[_summed(_SUM_KEYS)])
    wire = _wire(entry).model_dump()
    amount = wire["fields"]["amount"]
    assert (amount["key"], amount["type"]) == ("Billing_Amount+IP+EB_Net_Amount", "currency")
    assert [p["key"] for p in amount["parts"]] == _SUM_KEYS
    # A plain field has no parts key, so its wire is the wire of before.
    assert "parts" not in wire["fields"]["date"]


async def test_the_tool_line_names_each_part_of_a_sum(monkeypatch) -> None:
    """Item 9: crm_list_modules says "sum of" and names each part."""
    from gateway.routes.crm.catalogue import _wire

    entry = _one([_collection()], "Payment_Received", items=[_summed(_SUM_KEYS)])
    body = _wire(entry).model_dump()
    _gateway(monkeypatch, lambda p: (200, {"mode": "mirror", "entries": [body]}))
    out = await tools.crm_list_modules()
    assert ("Amount: sum of Rent Net Amount, IP Basic Amount and EB Net Amount "
            "(Billing_Amount + IP + EB_Net_Amount).") in out
