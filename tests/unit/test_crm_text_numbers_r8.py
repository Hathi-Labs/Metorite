"""R8 — a text field that holds numbers (WS-53 CRM-Q34-11, task 3728).

Spec: ``project-docs/specs/crm_platform.md`` §18.17, the row CRM-Q34-11.

Against a REAL Postgres, through the real ``/crm`` router, as the
non-privileged role of the phase-4 rehearsal. The seed goes in as the
superuser.

1. A sum of a text field by month: each dirty value is no value, and
   ``values`` and ``records`` give the count of the records with no number.
2. avg, min and max.
3. A number range on the text field counts the exact records.
4. A split of the text field and a number field.
5. A text field of words is a 422 that gives the counts.
6. A text field at exactly 90% reads as a number, and at 89% it is a 422.
7. Organization B never counts, and its field never moves the counts of A.
8. A number field answers as before, to the byte.
9. "1,5" is no number.

A hermetic fake agrees with any SQL it is handed, so none of these is proved
without the database.
"""

# ruff: noqa: F811 - the fixtures come in by import and are used as arguments
from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

from tests.unit.test_crm_aggregate_fields_r8 import (
    _TABLES,
    _admin,
    _call,
    _defs,
    _mirror_org,
    _module,
    _record,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

_INVOICE_DEFS = [
    ("Fiber", "text", None, False, False),
    ("Notes", "text", None, False, False),
    ("Amount", "currency", None, False, False),
    ("Invoice_Date", "date", None, False, False),
]
_BILL_DEFS = [
    ("Share90", "text", None, False, False),
    ("Share89", "text", None, False, False),
]

#: Fourteen dirty and clean values of org A, each ``(id, Fiber, date, extra)``.
#: ``_MISSING`` leaves the key out of the record.
_MISSING = object()
_DIRTY: list[tuple[str, Any, str, dict[str, Any]]] = [
    ("r01", "120", "2026-10-05", {"Amount": 100, "Notes": "hello"}),
    ("r02", "1,200", "2026-10-20", {"Amount": 200, "Notes": "world"}),
    ("r03", " 75.5 ", "2026-11-02", {"Amount": 300, "Notes": "12"}),
    ("r04", "", "2026-11-03", {}),
    ("r05", " ", "2026-11-04", {}),
    ("r06", "-", "2026-10-06", {}),
    ("r07", "NA", "2026-10-07", {}),
    ("r08", "N/A", "2026-11-08", {}),
    ("r09", "Rs 500", "2026-11-09", {}),
    ("r10", "₹500", "2026-10-10", {}),
    ("r11", 40, "2026-11-11", {}),
    ("r12", None, "2026-10-12", {}),
    ("r13", _MISSING, "2026-11-13", {}),
    ("r14", "1,5", "2026-10-14", {}),
]
#: 51 more clean values of 10 each, in December. So 55 of the 61 filled
#: values are plain numbers (90.2%), and 65 records match in all.
_CLEAN = 51
_PLAIN, _FILLED, _RECORDS = 55, 61, 65


def _seed(p: Any) -> None:
    a, b = p.org_a, p.org_b
    _mirror_org(p, a, [_module("Invoices"), _module("Bills")])
    _mirror_org(p, b, [_module("Invoices")])
    _defs(p, a, "module:Invoices", _INVOICE_DEFS)
    _defs(p, b, "module:Invoices", _INVOICE_DEFS)
    _defs(p, a, "module:Bills", _BILL_DEFS)
    with p.admin_engine.begin() as c:
        for ext, fiber, day, extra in _DIRTY:
            fields: dict[str, Any] = {"Invoice_Date": day, **extra}
            if fiber is not _MISSING:
                fields["Fiber"] = fiber
            _record(c, a, "Invoices", ext, None, fields)
        for i in range(_CLEAN):
            _record(c, a, "Invoices", f"c{i:02d}", None,
                    {"Fiber": "10", "Invoice_Date": "2026-12-01"})
        # Org B: numbers in October, and a Notes field of plain numbers. If
        # they leaked, A's October and A's Notes would both change.
        for i in range(100):
            _record(c, b, "Invoices", f"b{i:03d}", None,
                    {"Fiber": "99999", "Notes": "7", "Invoice_Date": "2026-10-05"})
        # Bills: exactly 90% and exactly 89% plain numbers.
        for i in range(100):
            _record(c, a, "Bills", f"x{i:03d}", None, {
                "Share90": "1" if i < 90 else "y",
                "Share89": "1" if i < 89 else "y",
            })


@pytest.fixture
def inv(promoted, monkeypatch):
    from tests.unit._crm_fakes import crm_switch_on

    crm_switch_on(monkeypatch, "*")
    _seed(promoted)
    yield promoted
    with promoted.admin_engine.begin() as c:
        for org in (promoted.org_a, promoted.org_b):
            for table in _TABLES:
                c.execute(text(
                    f"DELETE FROM {table} WHERE organization_id = CAST(:o AS uuid)"),
                    {"o": org})


async def _res(p: Any, org: str, **query: Any) -> Any:
    return await _call(p, _admin(org), "GET", "/crm/aggregate?" + urlencode(query))


async def _agg(p: Any, org: str, **query: Any) -> dict:
    res = await _res(p, org, **query)
    assert res.status_code == 200, (query, res.text)
    return res.json()


def _months(body: dict) -> dict[str, Any]:
    return {g["key"]: g["value"] for g in body["groups"]}


# ── 1. A sum by month ─────────────────────────────────────────────────────────


async def test_a_text_field_sums_by_month_and_each_dirty_value_is_no_value(
        inv, app_engine) -> None:
    body = await _agg(inv, inv.org_a, entity="module:Invoices", measure="sum",
                      field="Fiber", group_field="Invoice_Date", bucket="month")
    # October: 120 + 1,200. November: 75.5 + 40. December: 51 × 10.
    assert _months(body) == {"2026-10": 1320.0, "2026-11": 115.5, "2026-12": 510.0}
    assert body["total"] == 1945.5
    assert (body["values"], body["records"]) == (_PLAIN, _RECORDS)
    assert body["text_numbers"] == {"Fiber": {"filled": _FILLED, "plain": _PLAIN}}
    assert body["field"] == "Fiber"


# ── 2. avg, min and max ───────────────────────────────────────────────────────


async def test_avg_min_and_max_of_a_text_field(inv, app_engine) -> None:
    got = {m: (await _agg(inv, inv.org_a, entity="module:Invoices", measure=m,
                          field="Fiber"))["total"] for m in ("avg", "min", "max")}
    assert got == {"avg": round(1945.5 / _PLAIN, 2), "min": 10.0, "max": 1200.0}


# ── 3. A number range ─────────────────────────────────────────────────────────


async def test_a_range_on_a_text_field_counts_the_exact_records(inv, app_engine) -> None:
    body = await _agg(inv, inv.org_a, **{"entity": "module:Invoices", "measure": "count",
                                         "field.Fiber.from": "100", "field.Fiber.to": "2000"})
    # "120" and "1,200". "Rs 500" and "₹500" are no number.
    assert body["total"] == 2
    assert body["text_numbers"] == {"Fiber": {"filled": _FILLED, "plain": _PLAIN}}


# ── 4. A split ────────────────────────────────────────────────────────────────


async def test_a_split_of_a_text_field_and_a_number_field(inv, app_engine) -> None:
    body = await _agg(inv, inv.org_a, entity="module:Invoices", measure="sum",
                      fields="Fiber,Amount")
    totals = {s["field"]: (s["total"], s["values"]) for s in body["split"]}
    assert totals == {"Fiber": (1945.5, _PLAIN), "Amount": (600.0, 3)}
    assert body["text_numbers"] == {"Fiber": {"filled": _FILLED, "plain": _PLAIN}}


# ── 5. A text field of words ──────────────────────────────────────────────────


async def test_a_text_field_of_words_is_a_422_with_its_counts(inv, app_engine) -> None:
    res = await _res(inv, inv.org_a, entity="module:Invoices", measure="sum", field="Notes")
    assert res.status_code == 422, res.text
    message = res.json()["detail"]["message"]
    # "hello", "world" and "12": one of three. Org B's 100 numbers never count.
    assert message.startswith("'Notes' is a text field. Only 1 of its 3 filled values "
                              "are plain numbers, too few to total.")
    assert "The number fields of module:Invoices are: Amount." in message


# ── 6. The share of 90% ───────────────────────────────────────────────────────


async def test_a_text_field_at_90_percent_totals_and_at_89_it_is_a_422(
        inv, app_engine) -> None:
    body = await _agg(inv, inv.org_a, entity="module:Bills", measure="sum", field="Share90")
    assert (body["total"], body["values"], body["records"]) == (90.0, 90, 100)
    assert body["text_numbers"] == {"Share90": {"filled": 100, "plain": 90}}
    res = await _res(inv, inv.org_a, entity="module:Bills", measure="sum", field="Share89")
    assert res.status_code == 422, res.text
    assert "Only 89 of its 100 filled values" in res.json()["detail"]["message"]


# ── 7. Organization B ─────────────────────────────────────────────────────────


async def test_org_b_counts_only_its_own_records(inv, app_engine) -> None:
    body = await _agg(inv, inv.org_b, entity="module:Invoices", measure="sum",
                      field="Fiber", group_field="Invoice_Date", bucket="month")
    assert _months(body) == {"2026-10": 9999900.0}
    assert body["text_numbers"] == {"Fiber": {"filled": 100, "plain": 100}}
    # B's Notes are all numbers, so B totals them, while A's stay a 422 above.
    notes = await _agg(inv, inv.org_b, entity="module:Invoices", measure="sum", field="Notes")
    assert notes["total"] == 700.0


# ── 8. A number field, as before ──────────────────────────────────────────────


async def test_a_number_field_answers_as_before(inv, app_engine) -> None:
    res = await _res(inv, inv.org_a, entity="module:Invoices", measure="sum",
                     field="Amount", group_field="Invoice_Date", bucket="month")
    assert res.status_code == 200, res.text
    assert res.json() == {
        "entity": "module:Invoices", "measure": "sum", "group_by": None,
        "group_field": "Invoice_Date", "field": "Amount", "field_type": "currency",
        "field_label": "Amount", "group_field_label": "Invoice Date", "keys": {},
        "group_field_type": "date", "bucket": "month",
        "groups": [
            {"key": "2026-10", "label": "2026-10", "value": 300.0},
            {"key": "2026-11", "label": "2026-11", "value": 300.0},
            {"key": "2026-12", "label": "2026-12", "value": 0.0},
        ],
        "total": 600.0, "other": 0.0, "omitted": 0, "zone": "UTC",
        "values": 3, "records": _RECORDS,
    }
    assert "text_numbers" not in res.text


# ── 9. "1,5" is no number ─────────────────────────────────────────────────────


async def test_a_comma_that_is_no_thousands_group_is_no_number(inv, app_engine) -> None:
    for low, high in (("1", "2"), ("14", "16"), ("1.4", "1.6")):
        body = await _agg(inv, inv.org_a, **{
            "entity": "module:Invoices", "measure": "count",
            "field.Fiber.from": low, "field.Fiber.to": high})
        assert body["total"] == 0, (low, high, body)
