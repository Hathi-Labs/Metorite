"""The read tools say when Zoho holds more (WS-53 CRM-Q30b).

Spec: ``project-docs/specs/crm_platform.md`` §18.11.2, CRM-Q30b items 3 to 6.

Hermetic. ``crm_tools._get`` is the one GET of the tools, so a fake of it
answers each route. ``test_crm_short_reads_r8.py`` proves the route itself
on a real database.
"""
from __future__ import annotations

import inspect
from typing import Any

import pytest
from acb_skills import crm_tools as T

_ID = "11111111-1111-1111-1111-111111111111"

SHORT = [
    {"key": "module:Payment_Made", "zoho_count": 4947, "readable": 3003, "short_by": 1944},
    {"key": "contacts", "zoho_count": 120, "readable": 100, "short_by": 20},
]

LINE = ("Note: Zoho holds 4,947 Payment_Made records, and the connected Zoho user can "
        "read 3,003. Totals may be short. An admin can widen that user's access in Zoho.")


def _routes(short: Any = SHORT) -> dict[str, Any]:
    return {
        T.SHORT_READS_PATH: short,
        "/crm/aggregate": {"groups": [{"key": "a", "label": "a", "value": 3}], "total": 3,
                           "zone": "UTC"},
        "/crm/modules": [{"api_name": "Payment_Made", "label": "Payment Made", "count": 3003}],
        # CRM-Q31-1: the catalogue is off, so crm_list_modules lists /crm/modules.
        "/crm/catalogue": T._GatewayError("off", "catalogue_off", status=404),
        "/crm/modules/Payment_Made/records": {
            "rows": [{"id": _ID, "name": "PM-1"}], "total": 1, "field_defs": []},
        "/crm/contacts": {"rows": [{"id": _ID, "first_name": "Asha"}], "total": 1},
        f"/crm/contacts/{_ID}": {"id": _ID, "first_name": "Asha"},
        "/crm/fields/contacts": [],
        f"/crm/modules/Payment_Made/records/{_ID}": {"record": {"id": _ID, "name": "PM-1"},
                                                     "field_defs": []},
    }


def _fake(monkeypatch, routes: dict[str, Any]) -> list[str]:
    calls: list[str] = []

    async def get(path: str, params: dict[str, Any] | None = None) -> Any:
        calls.append(path)
        answer = routes[path]
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(T, "_get", get)
    return calls


TOOLS = {
    "crm_aggregate": lambda: T.crm_aggregate("module:Payment_Made", "owner"),
    "crm_list_module_records": lambda: T.crm_list_module_records("Payment_Made"),
    "crm_list_modules": lambda: T.crm_list_modules(),
}


@pytest.mark.parametrize("tool", sorted(TOOLS))
async def test_a_module_tool_adds_the_line(tool: str, monkeypatch) -> None:
    calls = _fake(monkeypatch, _routes())
    out = await TOOLS[tool]()
    assert LINE in out
    assert calls.count(T.SHORT_READS_PATH) == 1, "one read of the route for each call"


async def test_a_list_of_a_core_entity_adds_its_own_line_only(monkeypatch) -> None:
    _fake(monkeypatch, _routes())
    out = await T.crm_list_records("contacts")
    assert "Zoho holds 120 Contacts records, and the connected Zoho user can read 100." in out
    assert "Payment_Made" not in out


async def test_an_aggregate_of_another_entity_has_no_line(monkeypatch) -> None:
    _fake(monkeypatch, _routes())
    out = await T.crm_aggregate("module:Invoices", "owner")
    assert "Note: Zoho holds" not in out


async def test_an_empty_field_still_carries_the_line(monkeypatch) -> None:
    routes = _routes()
    routes["/crm/aggregate"] = {"groups": [], "total": None, "values": 0}
    _fake(monkeypatch, routes)
    out = await T.crm_aggregate("module:Payment_Made", "owner", measure="sum", field="Amount")
    assert "no values recorded for Amount" in out and LINE in out


@pytest.mark.parametrize("failure", [
    RuntimeError("CRM GET /crm/sync/short-reads failed (500)"),
    T._Refused(T.CRM_CLOSED),
])
async def test_a_failed_read_adds_nothing(failure: Exception, monkeypatch) -> None:
    _fake(monkeypatch, _routes(failure))
    out = await T.crm_aggregate("module:Payment_Made", "owner")
    assert out.startswith("Payment_Made, count by owner:")
    assert "Note:" not in out and T.CRM_CLOSED not in out


async def test_a_row_of_a_wrong_shape_is_left_out(monkeypatch) -> None:
    _fake(monkeypatch, _routes([{"key": "module:Payment_Made", "zoho_count": "many"}, "x"]))
    assert "Note:" not in await T.crm_list_modules()


async def test_a_tool_of_one_record_reads_no_short_read(monkeypatch) -> None:
    calls = _fake(monkeypatch, _routes())
    await T.crm_get_record("contacts", _ID)
    await T.crm_get_module_record("Payment_Made", _ID)
    assert T.SHORT_READS_PATH not in calls


def test_one_helper_writes_the_line() -> None:
    """The sentence lives in ``_short_line`` only, and each tool calls
    ``_short_note``. A second copy would drift."""
    source = inspect.getsource(T)
    assert source.count("Note: Zoho holds") == 1
    for name in ("crm_aggregate", "crm_list_records", "crm_list_module_records",
                 "crm_list_modules"):
        fn = getattr(T, name).__wrapped__
        assert "_short_note(" in inspect.getsource(fn), name
