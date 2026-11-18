"""CRM-Q34-12: ONE resolver for every ``module:<X>`` path of the CRM routes.

A chat asks for a module by the label that people use, and the store keys a
module by its API name. ``modules.resolve_module`` is the one place that maps
the first to the second, and it names close modules when none fits.
"""
from __future__ import annotations

import contextlib
import re
from pathlib import Path

import pytest
from fastapi import HTTPException
from gateway.routes.crm import aggregate, fields, modules
from gateway.routes.crm.modules import REFUSAL_MAX, resolve_module

ROOT = Path(__file__).resolve().parents[2]
CRM = ROOT / "apps" / "services" / "gateway" / "gateway" / "routes" / "crm"

ORG_A = {
    "Sales_Orders": {"api_name": "Sales_Orders", "label": "Sales Orders",
                     "singular_label": "Sales Order"},
    "Products": {"api_name": "Products", "label": "Products", "singular_label": "Product"},
    "Vendors": {"api_name": "Vendors", "label": "Vendors", "singular_label": "Vendor"},
}
ORG_B = {"Secret_Deals": {"api_name": "Secret_Deals", "label": "Secret Deals"}}


def refusal(mirrored: dict, text: str) -> str:
    with pytest.raises(HTTPException) as exc:
        resolve_module(mirrored, text)
    assert exc.value.status_code == 404
    assert exc.value.detail["code"] == "unknown_module"
    message = exc.value.detail["message"]
    assert len(message) <= REFUSAL_MAX
    return message


@pytest.mark.parametrize("given", [
    "Sales_Orders",      # the API name
    "sales_orders", "SALES_ORDERS", "sales orders", "Sales-Orders", "SalesOrders",
    "Sales Orders",      # the plural label
    "Sales Order",       # the singular label
    "sales order", "sales_order",
])
def test_each_form_of_a_module_resolves_to_its_api_name(given: str) -> None:
    assert resolve_module(ORG_A, given) == "Sales_Orders"


def test_a_plain_label_resolves() -> None:
    assert resolve_module(ORG_A, "product") == "Products"
    assert resolve_module(ORG_A, "VENDORS") == "Vendors"


def test_an_unknown_module_names_up_to_three_close_ones() -> None:
    many = {n: {"label": n} for n in ("Quotes", "Quote_Items", "Quoted", "Quotas", "Zebra")}
    head = refusal(many, "Quot").split(" The mirrored modules")[0]
    named = [n for n in ("Quotes", "Quote_Items", "Quoted", "Quotas") if n in head]
    assert 1 <= len(named) <= 3
    assert "Zebra" not in head


def test_a_near_spelling_names_the_close_module_with_its_label() -> None:
    message = refusal(ORG_A, "Sale Ordrs")
    assert "Did you mean: Sales_Orders (Sales Orders)" in message


def test_no_close_module_keeps_the_list_of_mirrored_ones() -> None:
    message = refusal({"Invoices": {}, "Quotes": {}}, "Outstanding")
    assert "The mirrored modules are: Invoices, Quotes." in message
    assert "Did you mean" not in message


def test_an_ambiguous_name_resolves_none_and_names_both() -> None:
    two = {
        "Deal_Items": {"label": "Deal Items"},
        "DealItems": {"label": "Deal items 2"},
        "Other": {"label": "Other"},
    }
    head = refusal(two, "deal items").split(" The mirrored modules")[0]
    assert "fits more than one module" in head
    assert "Deal_Items" in head and "DealItems" in head and "Other" not in head


def test_an_ambiguous_label_is_not_guessed() -> None:
    two = {"A_One": {"label": "Items"}, "B_Two": {"label": "Items"}}
    head = refusal(two, "items").split(" The mirrored modules")[0]
    assert "A_One" in head and "B_Two" in head


def test_the_exact_api_name_beats_a_label_of_another_module() -> None:
    both = {"Leads": {"label": "Leads"}, "Other": {"label": "Leads"}}
    assert resolve_module(both, "Leads") == "Leads"


def test_the_refusal_is_bounded_with_a_long_module_list() -> None:
    big = {f"Module_Number_{i:03d}": {"label": f"Module {i}"} for i in range(60)}
    refusal(big, "Module_Number_00")
    refusal(big, "x" * 500)


def test_a_module_of_another_org_never_resolves() -> None:
    # ``mirrored_modules`` gives the caller's own set. A name of org B is
    # unknown to org A, in every form, and the refusal does not name it.
    for given in ("Secret_Deals", "secret deals", "Secret Deals"):
        assert "Secret" not in refusal(ORG_A, given)
    assert resolve_module(ORG_B, "secret deals") == "Secret_Deals"


def test_an_empty_name_is_a_refusal() -> None:
    refusal(ORG_A, "")
    refusal(ORG_A, "  __ ")


def test_the_strict_check_refuses_a_label_and_names_the_api_name() -> None:
    # `_require_module` serves the callers that keep the name as the key
    # (the aggregate). It never returns a row for a label.
    with pytest.raises(HTTPException) as exc:
        modules._require_module(ORG_A, "Sales Orders")
    assert "Sales_Orders (Sales Orders)" in exc.value.detail["message"]


# -- The paths go through the one resolver ------------------------------------


@contextlib.asynccontextmanager
async def _session():
    yield object()


async def test_the_fields_path_resolves_a_label(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    async def mirrored(db):
        return ORG_A

    async def defs(db, module, org=None):
        seen.append(module)
        return []

    monkeypatch.setattr(fields, "_tenant_session", _session)
    monkeypatch.setattr(modules, "mirrored_modules", mirrored)
    monkeypatch.setattr(modules, "_field_defs", defs)
    assert await fields.module_field_defs("Sales Orders", "org") == []
    assert seen == ["Sales_Orders"]
    with pytest.raises(HTTPException) as exc:
        await fields.module_field_defs("Sale Ordrs", "org")
    assert exc.value.status_code == 404 and "Sales_Orders" in exc.value.detail["message"]


class _Result:
    def scalar(self):
        return 0

    def fetchall(self):
        return []


class _Db:
    def __init__(self):
        self.params: list[dict] = []

    async def execute(self, statement, params=None):
        self.params.append(dict(params or {}))
        return _Result()


async def test_the_list_path_reads_the_api_name(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _Db()

    @contextlib.asynccontextmanager
    async def session():
        yield db

    async def mirrored(_db):
        return ORG_A

    async def defs(_db, module, org=None):
        return []

    async def no_filters(*_a, **_k):
        return ((), {})

    monkeypatch.setattr(modules, "_tenant_session", session)
    monkeypatch.setattr(modules, "mirrored_modules", mirrored)
    monkeypatch.setattr(modules, "_field_defs", defs)
    monkeypatch.setattr(modules, "_field_filters", no_filters)
    user = type("U", (), {"organization_id": "org", "email": "a@b.c"})()
    out = await modules.list_module_records(
        "sales order", user=user, request=None, page=1, page_size=10,
        sort=None, direction="desc", q=None, owner=None,
        updated_from=None, updated_to=None,
    )
    assert out.module == "Sales_Orders"
    assert any(p.get("module") == "Sales_Orders" for p in db.params)


# -- The fence: no path parses a module name with its own code ----------------

#: The routes that take ``module:<X>`` or ``/modules/{module}``, and the file
#: of each. Each must call ``resolve_module``. ``aggregate.py`` and
#: ``field_sql.py`` are not here: the lane that owns them calls it (see the
#: CRM-Q34-12 note in crm_platform.md).
_CALLERS = {
    "modules.py": ("list_module_records", "get_module_record",
                   "get_module_record_timeline", "patch_module_record"),
    "fields.py": ("module_field_defs", "search_fields"),
    "terms.py": ("resolve_term",),
    "aggregate.py": ("_compute",),
}


def _body(source: str, name: str) -> str:
    start = re.search(rf"^async def {name}\(", source, re.M)
    assert start, name
    nxt = re.search(r"^(?:async def|def|@router)", source[start.end():], re.M)
    return source[start.start(): start.end() + (nxt.start() if nxt else len(source))]


@pytest.mark.parametrize("file", sorted(_CALLERS))
def test_each_module_path_calls_the_one_resolver(file: str) -> None:
    source = (CRM / file).read_text(encoding="utf-8")
    for name in _CALLERS[file]:
        assert "resolve_module(" in _body(source, name), f"{file}:{name}"


def test_no_second_label_matcher_in_the_crm_routes() -> None:
    for path in CRM.glob("*.py"):
        if path.name == "modules.py":
            continue
        source = path.read_text(encoding="utf-8")
        assert "def fold_module" not in source, path.name


async def test_a_tool_takes_a_label_and_names_the_api_name(monkeypatch: pytest.MonkeyPatch) -> None:
    from acb_skills import crm_tools as tools

    paths: list[str] = []

    async def get(path, params=None, timeout=30.0):
        paths.append(path)
        return {"rows": [], "total": 0, "field_defs": [], "module": "Sales_Orders"}

    async def note(_keys):
        return ""

    monkeypatch.setattr(tools, "_get", get)
    monkeypatch.setattr(tools, "_short_note", note)
    out = await tools.crm_list_module_records.__wrapped__("module:Sales Orders") \
        if hasattr(tools.crm_list_module_records, "__wrapped__") \
        else await tools.crm_list_module_records("module:Sales Orders")
    assert paths == ["/crm/modules/Sales Orders/records"]
    assert "Sales_Orders" in out
    assert tools._entity_slug("module:Sales Orders", allow_module=True) == "module:Sales Orders"
    with pytest.raises(Exception, match="Invalid module name"):
        tools._module_name("Sales/../Orders")


class _Stop(Exception):
    pass


@pytest.mark.parametrize("given", ["module:Sales Orders", "module:sales order",
                                   "module:sales_orders", "module:Sales_Orders"])
async def test_the_aggregate_path_reads_the_api_name(
    given: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str | None] = []

    async def mirrored(_db):
        return ORG_A

    async def read(_db, found, module, **_kw):
        seen.append(module)
        raise _Stop

    monkeypatch.setattr(aggregate, "_tenant_session", _session)
    monkeypatch.setattr(aggregate, "mirrored_modules", mirrored)
    monkeypatch.setattr(aggregate, "_read", read)
    with pytest.raises(_Stop):
        await aggregate.compute(given, params=None)
    assert seen == ["Sales_Orders"]


async def test_the_aggregate_path_refuses_an_unknown_module_with_close_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def mirrored(_db):
        return ORG_A

    monkeypatch.setattr(aggregate, "_tenant_session", _session)
    monkeypatch.setattr(aggregate, "mirrored_modules", mirrored)
    with pytest.raises(HTTPException) as exc:
        await aggregate.compute("module:Sale Ordrs", params=None)
    assert exc.value.status_code == 404
    assert "Sales_Orders (Sales Orders)" in exc.value.detail["message"]
