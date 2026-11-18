"""R8 — a text field that holds numbers (WS-53 CRM-Q34-11, task 3728).

Spec: ``project-docs/specs/crm_platform.md`` §18.17, the row CRM-Q34-11.

Against a REAL Postgres, through the real ``/crm`` router, as the
non-privileged role of the phase-4 rehearsal. The seed goes in as the
superuser.

1. THE MATRIX: each raw value in a module of its own, read back through the
   route. A number gives its exact number. A value with no value and a value
   that is not a number give the 422 whose counts name their class.
2. A sum of a dirty text field by month equals an independent SQL sum, and
   the counts equal an independent SQL count.
3. avg, min, max, a number range, a split, and the shapes of the answer.
4. A text equality filter stays text equality, in a call that also sums.
5. The gate: half of the filled values, and a 422 with the counts below it.
6. Organization B never moves the counts or the gate of organization A.
7. A number field answers as before, to the byte.

A hermetic fake agrees with any SQL it is handed, so none of these is proved
without the database.
"""

# ruff: noqa: F811 - the fixtures come in by import and are used as arguments
from __future__ import annotations

import json
import re
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

#: A record with no key at all.
_MISSING = object()

NUMBER, NO_VALUE, NOT_NUMBER = "number", "no_value", "not_number"

#: THE MATRIX: each raw value, its class, and its number. The raw value is
#: what Zoho stored: a JSON string, or another JSON value.
MATRIX: list[tuple[Any, str, float | None]] = [
    ("120", NUMBER, 120.0), ("0", NUMBER, 0.0), ("0.5", NUMBER, 0.5),
    ("-5", NUMBER, -5.0), ("-0.5", NUMBER, -0.5), ("-0", NUMBER, 0.0),
    ("1,234", NUMBER, 1234.0), ("12,34,567", NUMBER, 1234567.0),
    ("1,00,000", NUMBER, 100000.0), ("123,456", NUMBER, 123456.0),
    ("1,234,567.89", NUMBER, 1234567.89),
    ("₹ 1,200", NUMBER, 1200.0), ("₹500", NUMBER, 500.0), ("Rs. 500", NUMBER, 500.0),
    ("rs 500", NUMBER, 500.0), ("RS500", NUMBER, 500.0), ("500 INR", NUMBER, 500.0),
    ("inr 500", NUMBER, 500.0), ("500rs", NUMBER, 500.0), ("12%", NUMBER, 12.0),
    ("12.5 %", NUMBER, 12.5), (" 75.5 ", NUMBER, 75.5), ("\t5\t", NUMBER, 5.0),
    ("\u00a042\u00a0", NUMBER, 42.0), ("9" * 30, NUMBER, float("9" * 30)),
    (42.5, NUMBER, 42.5), (0, NUMBER, 0.0),
    ("1,2,3", NOT_NUMBER, None), ("1,5", NOT_NUMBER, None), ("12,34", NOT_NUMBER, None),
    ("1,23", NOT_NUMBER, None), (",100", NOT_NUMBER, None), ("1,234,56", NOT_NUMBER, None),
    ("007", NOT_NUMBER, None), ("00", NOT_NUMBER, None), ("100-200", NOT_NUMBER, None),
    ("2026-11-01", NOT_NUMBER, None), ("01/02/2026", NOT_NUMBER, None),
    ("500 mtrs", NOT_NUMBER, None), ("500 m", NOT_NUMBER, None), ("1e3", NOT_NUMBER, None),
    ("1E3", NOT_NUMBER, None), ("NaN", NOT_NUMBER, None), ("Infinity", NOT_NUMBER, None),
    ("-Infinity", NOT_NUMBER, None), ("100 200", NOT_NUMBER, None),
    ("(1,200)", NOT_NUMBER, None), ("1,200-", NOT_NUMBER, None), ("+5", NOT_NUMBER, None),
    (".5", NOT_NUMBER, None), ("5.", NOT_NUMBER, None), ("\u0661\u0662\u0663", NOT_NUMBER, None),
    ("\uff11\uff12\uff13", NOT_NUMBER, None), ("1" * 40, NOT_NUMBER, None), ("1" * 31, NOT_NUMBER, None),
    ("Rs 500 INR", NOT_NUMBER, None), ("₹500%", NOT_NUMBER, None),
    ("ABC-DEF-12", NOT_NUMBER, None), ([1, 2], NOT_NUMBER, None),
    ({"a": 1}, NOT_NUMBER, None), (True, NOT_NUMBER, None),
    ("", NO_VALUE, None), ("  ", NO_VALUE, None), ("-", NO_VALUE, None),
    ("—", NO_VALUE, None), ("NA", NO_VALUE, None), ("N/A", NO_VALUE, None),
    ("nil", NO_VALUE, None), ("none", NO_VALUE, None), ("null", NO_VALUE, None),
    ("NULL", NO_VALUE, None), (None, NO_VALUE, None), (_MISSING, NO_VALUE, None),
]

#: The words of the 422 of a value of each class that is not a number.
_COUNTS_OF = {
    NO_VALUE: "In this module: 0 numbers, 1 with no value, 0 not numbers.",
    NOT_NUMBER: "In this module: 0 numbers, 0 with no value, 1 not a number.",
}


def _matrix_module(i: int) -> str:
    return f"Mx{i:02d}"


#: The dirty invoices of org A: ``(id, Fiber, Invoice_Date, more)``.
_INVOICES: list[tuple[str, Any, str, dict[str, Any]]] = [
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
    ("r15", "500 mtrs", "2026-10-15", {}),
    ("r16", "500 m", "2026-11-16", {}),
    ("r17", "12,34,567", "2026-12-17", {}),
]
#: 51 more clean values of 10 each, in December.
_CLEAN = 51
#: The number of each raw value of the invoices, written by hand. The
#: independent SQL sum joins it on the stored text.
_INVOICE_NUMBERS = {"120": 120, "1,200": 1200, " 75.5 ": 75.5, "Rs 500": 500, "₹500": 500,
                    "40": 40, "12,34,567": 1234567, "10": 10}
_NUMBERS, _NO_VALUE, _NOT_NUMBERS = 58, 7, 3
_RECORDS = _NUMBERS + _NO_VALUE + _NOT_NUMBERS
_FIBER = {"numbers": _NUMBERS, "no_value": _NO_VALUE, "not_numbers": _NOT_NUMBERS,
          "shapes": [{"shape": "9 a", "words": "a number with a unit word", "count": 2},
                     {"shape": "9,9", "words": "a comma that is no thousands group",
                      "count": 1}]}

_INVOICE_DEFS = [
    ("Fiber", "text", None, False, False),
    ("Notes", "text", None, False, False),
    ("Amount", "currency", None, False, False),
    ("Invoice_Date", "date", None, False, False),
]
_BILL_DEFS = [("Share50", "text", None, False, False),
              ("Share49", "text", None, False, False),
              ("Empty", "text", None, False, False)]


def _fields(raw: Any, key: str = "Raw", **more: Any) -> dict[str, Any]:
    out = dict(more)
    if raw is not _MISSING:
        out[key] = raw
    return out


def _seed(p: Any) -> None:
    a, b = p.org_a, p.org_b
    matrix = [_matrix_module(i) for i in range(len(MATRIX))]
    _mirror_org(p, a, [_module(m) for m in ("Invoices", "Bills", *matrix)])
    _mirror_org(p, b, [_module("Invoices")])
    _defs(p, a, "module:Invoices", _INVOICE_DEFS)
    _defs(p, b, "module:Invoices", _INVOICE_DEFS)
    _defs(p, a, "module:Bills", _BILL_DEFS)
    for module in matrix:
        _defs(p, a, f"module:{module}", [("Raw", "text", None, False, False)])
    with p.admin_engine.begin() as c:
        for i, (raw, _cls, _number) in enumerate(MATRIX):
            _record(c, a, _matrix_module(i), f"m{i:02d}", None, _fields(raw))
        for ext, fiber, day, more in _INVOICES:
            _record(c, a, "Invoices", ext, None,
                    _fields(fiber, "Fiber", Invoice_Date=day, **more))
        for i in range(_CLEAN):
            _record(c, a, "Invoices", f"c{i:02d}", None,
                    {"Fiber": "10", "Invoice_Date": "2026-12-01"})
        # Org B: numbers in October, and a Notes field of numbers. If they
        # leaked, A's October and A's Notes would both change.
        for i in range(100):
            _record(c, b, "Invoices", f"b{i:03d}", None,
                    {"Fiber": "99999", "Notes": "7", "Invoice_Date": "2026-10-05"})
        # Bills: exactly half, and just below half, of the filled values.
        for i in range(100):
            _record(c, a, "Bills", f"x{i:03d}", None, {
                "Share50": "1" if i < 50 else "y",
                "Share49": "1" if i < 49 else "y",
                "Empty": "NA",
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


# ── 1. The matrix ─────────────────────────────────────────────────────────────


async def test_the_matrix_of_raw_values(inv, app_engine) -> None:
    """Each raw value is the one record of its own module, so each answer
    reads that value alone. The table prints as ``value -> class -> number``."""
    table = []
    for i, (raw, cls, number) in enumerate(MATRIX):
        res = await _res(inv, inv.org_a, entity=f"module:{_matrix_module(i)}",
                         measure="sum", field="Raw")
        shown = "(missing key)" if raw is _MISSING else json.dumps(raw, ensure_ascii=False)
        if res.status_code == 200:
            body = res.json()
            got_cls, got = NUMBER, body["total"]
            assert body["text_numbers"]["Raw"] == {
                "numbers": 1, "no_value": 0, "not_numbers": 0, "shapes": []}, shown
        else:
            assert res.status_code == 422, (shown, res.text)
            message = res.json()["detail"]["message"]
            got_cls = next((c for c, words in _COUNTS_OF.items() if words in message), "?")
            got = None
            if got_cls == NOT_NUMBER:
                # The shape never carries the value: no digit but 9, and
                # never the stored text.
                shapes = message.split("Most common of these: ", 1)[1].split(". A measure")[0]
                shapes = re.sub(r" \([0-9,]+\)", "", shapes)  # the count of each shape
                assert not set(shapes) & set("012345678"), (shown, shapes)
                if isinstance(raw, str) and len(raw) >= 3:
                    assert raw not in shapes, (shown, shapes)
        table.append(f"{shown} -> {got_cls} -> {got}")
        assert (got_cls, got) == (cls, None if number is None else round(number, 2)), (
            shown, res.text)
    print("\n" + "\n".join(table))


# ── 2. A sum by month, against an independent SQL ────────────────────────────


def _independent(p: Any, org: str) -> tuple[dict[str, float], int, int]:
    """The month sums of org A's Fiber by a hand-written join on the table
    of numbers, its count of numbers, and its count of values with no
    value, in plain SQL that shares no code with the route."""
    values = ", ".join(f"(:r{i}, CAST(:n{i} AS numeric))"
                       for i in range(len(_INVOICE_NUMBERS)))
    params: dict[str, Any] = {"o": org}
    for i, (raw, number) in enumerate(_INVOICE_NUMBERS.items()):
        params[f"r{i}"], params[f"n{i}"] = raw, str(number)
    joined = (f"FROM crm_module_records r JOIN (VALUES {values}) x(raw, n) "
              "ON r.fields ->> 'Fiber' = x.raw WHERE r.organization_id = CAST(:o AS uuid) "
              "AND r.module = 'Invoices'")
    with p.admin_engine.begin() as c:
        sums = c.execute(text(
            f"SELECT left(r.fields ->> 'Invoice_Date', 7) AS m, sum(x.n) {joined} "
            "GROUP BY 1"), params).fetchall()
        numbers = c.execute(text(f"SELECT count(*) {joined}"), params).scalar_one()
        empty = c.execute(text(
            "SELECT count(*) FROM crm_module_records WHERE organization_id = "
            "CAST(:o AS uuid) AND module = 'Invoices' AND (NOT fields ? 'Fiber' "
            "OR fields -> 'Fiber' = CAST('null' AS jsonb) OR lower(trim(fields ->> 'Fiber')) "
            "IN ('', '-', 'na', 'n/a'))"), {"o": org}).scalar_one()
    return {m: float(s) for m, s in sums}, int(numbers), int(empty)


async def test_a_month_sum_equals_an_independent_sql_sum(inv, app_engine) -> None:
    body = await _agg(inv, inv.org_a, entity="module:Invoices", measure="sum",
                      field="Fiber", group_field="Invoice_Date", bucket="month")
    sums, numbers, empty = _independent(inv, inv.org_a)
    assert _months(body) == sums == {"2026-10": 1820.0, "2026-11": 615.5,
                                     "2026-12": 1235077.0}
    assert (numbers, empty) == (_NUMBERS, _NO_VALUE)
    assert body["text_numbers"] == {"Fiber": _FIBER}
    assert (body["values"], body["records"], body["total"]) == (_NUMBERS, _RECORDS, 1237512.5)
    assert (body["field"], body["field_type"]) == ("Fiber", "text")


# ── 3. avg, min, max, a range and a split ────────────────────────────────────


async def test_avg_min_and_max_of_a_text_field(inv, app_engine) -> None:
    got = {m: (await _agg(inv, inv.org_a, entity="module:Invoices", measure=m,
                          field="Fiber"))["total"] for m in ("avg", "min", "max")}
    assert got == {"avg": round(1237512.5 / _NUMBERS, 2), "min": 10.0, "max": 1234567.0}


async def test_a_range_on_a_text_field_counts_the_exact_records(inv, app_engine) -> None:
    body = await _agg(inv, inv.org_a, **{"entity": "module:Invoices", "measure": "count",
                                         "field.Fiber.from": "100", "field.Fiber.to": "2000"})
    # "120", "1,200", "Rs 500" and "₹500". "500 mtrs" and "500 m" are not numbers.
    assert body["total"] == 4
    assert body["text_numbers"] == {"Fiber": _FIBER}
    # "1,5" is never 1.5 and never 15.
    for low, high in (("1", "2"), ("14", "16"), ("1.4", "1.6")):
        none = await _agg(inv, inv.org_a, **{
            "entity": "module:Invoices", "measure": "count",
            "field.Fiber.from": low, "field.Fiber.to": high})
        assert none["total"] == 0, (low, high)


async def test_a_split_of_a_text_field_and_a_number_field(inv, app_engine) -> None:
    body = await _agg(inv, inv.org_a, entity="module:Invoices", measure="sum",
                      fields="Fiber,Amount")
    totals = {s["field"]: (s["total"], s["values"]) for s in body["split"]}
    assert totals == {"Fiber": (1237512.5, _NUMBERS), "Amount": (600.0, 3)}
    assert body["text_numbers"] == {"Fiber": _FIBER}


# ── 4. Text equality stays text equality ─────────────────────────────────────


async def test_a_text_equality_filter_stays_text_in_a_call_that_sums(inv, app_engine) -> None:
    one = await _agg(inv, inv.org_a, **{"entity": "module:Invoices", "measure": "sum",
                                        "field": "Fiber", "field.Fiber": "120"})
    assert (one["total"], one["records"]) == (120.0, 1)
    # "1,200" is no decimal, so a number filter would be a 422. Text matches it.
    comma = await _agg(inv, inv.org_a, **{"entity": "module:Invoices", "measure": "sum",
                                          "field": "Fiber", "field.Fiber": "1,200"})
    assert (comma["total"], comma["records"]) == (1200.0, 1)
    words = await _agg(inv, inv.org_a, **{"entity": "module:Invoices", "measure": "count",
                                          "field.Fiber": "500 MTRS"})
    assert words["total"] == 1 and "text_numbers" not in words


# ── 5. The gate ───────────────────────────────────────────────────────────────


async def test_a_text_field_of_words_is_a_422_with_its_counts(inv, app_engine) -> None:
    res = await _res(inv, inv.org_a, entity="module:Invoices", measure="sum", field="Notes")
    assert res.status_code == 422, res.text
    # "hello", "world" and "12": one of three. Org B's 100 numbers never count.
    assert res.json()["detail"]["message"].startswith(
        "'Notes' is a text field, and fewer than half of its filled values are numbers, "
        "so it looks like a code or free text. In this module: 1 number, 65 with no value, "
        "2 not numbers. Most common of these: words only (2).")


async def test_half_takes_a_field_and_below_half_is_a_422(inv, app_engine) -> None:
    body = await _agg(inv, inv.org_a, entity="module:Bills", measure="sum", field="Share50")
    assert (body["total"], body["values"], body["records"]) == (50.0, 50, 100)
    assert body["text_numbers"]["Share50"]["numbers"] == 50
    res = await _res(inv, inv.org_a, entity="module:Bills", measure="sum", field="Share49")
    assert res.status_code == 422, res.text
    assert ("In this module: 49 numbers, 0 with no value, 51 not numbers. Most common of "
            "these: words only (51).") in res.json()["detail"]["message"]
    empty = await _res(inv, inv.org_a, **{"entity": "module:Bills", "measure": "count",
                                          "field.Empty.from": "1"})
    assert empty.status_code == 422, empty.text
    assert empty.json()["detail"]["message"].startswith(
        "'Empty' is a text field, and no value of it is a number. In this module: "
        "0 numbers, 100 with no value, 0 not numbers.")


# ── 6. Organization B ─────────────────────────────────────────────────────────


async def test_org_b_counts_only_its_own_records(inv, app_engine) -> None:
    body = await _agg(inv, inv.org_b, entity="module:Invoices", measure="sum",
                      field="Fiber", group_field="Invoice_Date", bucket="month")
    assert _months(body) == {"2026-10": 9999900.0}
    assert body["text_numbers"] == {"Fiber": {"numbers": 100, "no_value": 0,
                                              "not_numbers": 0, "shapes": []}}
    # B's Notes are all numbers, so B totals them. A's stay a 422 above.
    notes = await _agg(inv, inv.org_b, entity="module:Invoices", measure="sum", field="Notes")
    assert notes["total"] == 700.0


# ── 7. A number field, as before ──────────────────────────────────────────────


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
