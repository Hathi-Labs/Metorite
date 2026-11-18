"""A text field that holds numbers (WS-53 CRM-Q34-11, task 3728).

Spec: ``project-docs/specs/crm_platform.md`` §18.17, the row CRM-Q34-11.

Hermetic. The behaviour on a real database is proved in
``tests/unit/test_crm_text_numbers_r8.py``. Here:

1. The number of every other field keeps its SQL text of before, byte for
   byte.
2. The grouped number takes "1,200" and refuses "1,5", "Rs 500" and "NA".
3. ``admit_text_numbers`` reads a text field as a number at 90% and refuses
   it below, with the counts.
4. A call that names no text field runs no statement more.
5. The wire is the one of before when no text field was read.
6. The tool says how many matching records hold no plain number.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

#: The SQL text of ``_number`` before CRM-Q34-11, for column ``c`` and key
#: ``k``. Every field of every other type still reads this text.
_NUMBER_BEFORE = (
    "(CASE WHEN jsonb_typeof(c -> :k) = 'number' THEN CAST(c ->> :k AS numeric) "
    "WHEN jsonb_typeof(c -> :k) = 'string' AND c ->> :k ~ "
    r"'^\s*-?[0-9]{1,30}(\.[0-9]{1,30})?\s*$' "
    "THEN CAST(c ->> :k AS numeric) END)"
)


def _scope(**types: str) -> Any:
    from gateway.routes.crm import field_sql as fs

    refs = {k: fs.FieldRef(key=k, type=t, label=k.replace("_", " ")) for k, t in types.items()}
    return fs.FieldScope(column="fields", owner="module:Invoices", fields=refs)


class _Result:
    def __init__(self, row: tuple) -> None:
        self._row = row

    def one(self) -> tuple:
        return self._row


class _Db:
    """A session that answers the one count statement with *row*, and keeps
    each statement that it got."""

    def __init__(self, row: tuple = ()) -> None:
        self.row = row
        self.seen: list[tuple[str, dict]] = []

    async def execute(self, statement: Any, params: dict | None = None) -> _Result:
        self.seen.append((str(statement), dict(params or {})))
        return _Result(self.row)


# ── 1. The SQL text of every other field ─────────────────────────────────────


def test_the_number_of_every_other_field_keeps_its_text_of_before() -> None:
    from gateway.routes.crm import field_sql as fs

    assert fs._number("c", "k") == _NUMBER_BEFORE
    assert fs._number("c", "k", False) == _NUMBER_BEFORE
    ref = fs.FieldRef(key="Amount", type="currency", label="Amount")
    (sql, bound), = fs._numbers("c", ref, "k")
    assert sql == _NUMBER_BEFORE.replace(":k", ":k_0") and bound == {"k_0": "Amount"}


def test_only_a_text_number_reads_the_grouped_number() -> None:
    from gateway.routes.crm import field_sql as fs

    ref = fs.FieldRef(key="Fiber", type="number", label="Fiber", text_number=True)
    (sql, _bound), = fs._numbers("c", ref, "k")
    assert sql != _NUMBER_BEFORE.replace(":k", ":k_0")
    assert "replace(c ->> :k_0, ',', '')" in sql
    assert "Fiber" not in sql


# ── 2. The shape of a grouped number ─────────────────────────────────────────


def _grouped(value: str) -> bool:
    from gateway.routes.crm import field_sql as fs

    return re.search(fs._NUMBER_GROUPED[1:-1], value) is not None


@pytest.mark.parametrize("value", [
    "120", "1,200", "-1,200.50", "1,200,000", "1,200,000.50", " 75.5 ", "999,999",
])
def test_the_grouped_number_takes_a_strict_thousands_shape(value: str) -> None:
    assert _grouped(value)


@pytest.mark.parametrize("value", [
    "1,5", "12,34", "1,20", ",100", "1,2000", "1234,567", "Rs 500", "₹500", "NA", "N/A",
    "-", "", " ", "1e5", "1" * 31, "1,000,000,000,000,000,000,000,000,000,000",
])
def test_the_grouped_number_refuses_every_other_shape(value: str) -> None:
    assert not _grouped(value)


# ── 3. The share of 90% ───────────────────────────────────────────────────────


async def test_a_text_field_at_90_percent_reads_as_a_number() -> None:
    from gateway.routes.crm import field_sql as fs

    scope = _scope(Fiber="text", Amount="currency", Invoice_Date="date")
    db = _Db(row=(10, 9))
    out, counts = await fs.admit_text_numbers(db, scope, ["Fiber", "Amount"])
    assert counts == {"Fiber": fs.TextNumbers(filled=10, plain=9)}
    assert out.fields["Fiber"].type == "number" and out.fields["Fiber"].text_number
    assert out.fields["Amount"] == scope.fields["Amount"]
    # The input scope is frozen and stays as it was.
    assert scope.fields["Fiber"].type == "text"
    assert fs.check_measure(out, "Fiber").text_number
    # ONE statement, with each key, the org and the module bound.
    (sql, params), = db.seen
    assert "Fiber" not in sql and "Invoices" not in sql
    assert params["agf_n0_0"] == "Fiber" and params["agf_module"] == "Invoices"
    assert "agf_org" in params and fs.DETOAST_JOIN in sql


async def test_a_text_field_below_90_percent_is_a_422_with_its_counts() -> None:
    from fastapi import HTTPException
    from gateway.routes.crm import field_sql as fs

    scope = _scope(Notes="text", Amount="currency")
    with pytest.raises(HTTPException) as caught:
        await fs.admit_text_numbers(_Db(row=(100, 89)), scope, ["Notes"])
    detail = caught.value.detail
    assert caught.value.status_code == 422 and detail["code"] == "unfit_field"
    assert detail["message"].startswith(
        "'Notes' is a text field. Only 89 of its 100 filled values are plain numbers, "
        "too few to total.")
    assert "The number fields of module:Invoices are: Amount." in detail["message"]


@pytest.mark.parametrize(("filled", "plain", "fits"), [
    (10, 9, True), (100, 90, True), (100, 89, False), (1, 1, True), (1, 0, False),
    (7283, 7231, True), (10549, 10549, True), (0, 0, False),
])
def test_the_share_is_90_percent_of_the_filled_values(filled: int, plain: int,
                                                      fits: bool) -> None:
    from gateway.routes.crm import field_sql as fs

    assert fs.text_number_fits(fs.TextNumbers(filled=filled, plain=plain)) is fits


async def test_a_text_field_with_no_value_keeps_the_refusal_of_before() -> None:
    from gateway.routes.crm import field_sql as fs

    scope = _scope(Fiber="text")
    out, counts = await fs.admit_text_numbers(_Db(row=(0, 0)), scope, ["Fiber"])
    assert out is scope and counts == {}


async def test_two_text_fields_take_one_statement() -> None:
    from gateway.routes.crm import field_sql as fs

    scope = _scope(Fiber="text", Fee="text")
    db = _Db(row=(10, 10, 4, 4))
    _out, counts = await fs.admit_text_numbers(db, scope, ["Fiber", "Fee"])
    assert len(db.seen) == 1 and set(counts) == {"Fiber", "Fee"}
    assert counts["Fee"] == fs.TextNumbers(filled=4, plain=4)


# ── 4. No statement more without a text field ────────────────────────────────


@pytest.mark.parametrize("keys", [
    [], ["Amount"], ["Nope"], ["@amount"], ["Status"], ["Notes_Area"], ["Amount+Rent"],
])
async def test_a_call_that_names_no_text_field_runs_no_statement(keys: list[str]) -> None:
    from gateway.routes.crm import field_sql as fs

    scope = _scope(Amount="currency", Rent="currency", Status="select",
                   Notes_Area="textarea", Fiber="text")
    db = _Db()
    out, counts = await fs.admit_text_numbers(db, scope, keys)
    assert out is scope and counts == {} and db.seen == []


async def test_a_core_entity_never_reads_a_text_field_as_a_number() -> None:
    from gateway.routes.crm import field_sql as fs

    ref = fs.FieldRef(key="Fiber", type="text", label="Fiber")
    scope = fs.FieldScope(column="custom", owner="deals", fields={"Fiber": ref})
    db = _Db()
    out, counts = await fs.admit_text_numbers(db, scope, ["Fiber"])
    assert out is scope and counts == {} and db.seen == []


def test_the_keys_that_a_call_reads_as_a_number() -> None:
    from gateway.routes.crm import field_sql as fs

    pairs = [("Fiber.from", "100"), ("Fiber.to", "2000"), ("Notes", "x"),
             ("Invoice_Date.from", "2026-01-01")]
    assert fs.text_number_keys("sum", "Fee", None, pairs) == ["Fee", "Fiber", "Invoice_Date"]
    assert fs.text_number_keys("count", "Fee", None, []) == []
    assert fs.text_number_keys("avg", None, "Fiber, Amount", []) == ["Fiber", "Amount"]
    assert fs.text_number_keys("count", None, None, [("Notes", "1")]) == []


def test_the_read_admits_before_any_check() -> None:
    """``_read`` admits the text fields right after it reads the scope, and
    before the first check or filter reads it."""
    import inspect

    from gateway.routes.crm import aggregate

    body = inspect.getsource(aggregate._read)
    admit = body.index("admit_text_numbers(")
    assert body.index("module_measure(") < admit < body.index("module_where(")
    assert admit < body.index("check_split(") and admit < body.index("check_measure(")


# ── 5. The wire ───────────────────────────────────────────────────────────────


def _response(**extra: Any) -> Any:
    from gateway.routes.crm.aggregate import AggregateResponse

    return AggregateResponse(entity="module:Invoices", measure="sum", field="Amount",
                             groups=[], total=5.0, values=1, records=1, **extra)


def test_the_wire_is_the_one_of_before_with_no_text_field() -> None:
    plain = _response().model_dump_json()
    assert "text_numbers" not in plain
    assert _response(text_numbers=None).model_dump_json() == plain
    assert _response(text_numbers={}).model_dump_json() == plain
    shown = _response(text_numbers={"Fiber": {"filled": 10, "plain": 9}}).model_dump()
    assert shown["text_numbers"] == {"Fiber": {"filled": 10, "plain": 9}}


# ── 6. The tool line ──────────────────────────────────────────────────────────


def _answer(**extra: Any) -> dict[str, Any]:
    return {"entity": "module:Invoices", "measure": "sum", "field": "Fiber",
            "field_type": "number", "field_label": "Fiber Length", "groups": [],
            "total": 1395.5, "other": 0, "omitted": 0, "values": 4, "records": 9, **extra}


async def test_a_total_says_how_many_records_hold_no_plain_number(monkeypatch) -> None:
    import acb_skills.crm_tools as tools

    async def _get(path: str, params: dict | None = None, timeout: float = 30.0) -> dict:
        return _answer(text_numbers={"Fiber": {"filled": 9, "plain": 4}})

    monkeypatch.setattr(tools, "_get", _get)
    out = await tools.crm_aggregate("module:Invoices", measure="sum", field="Fiber")
    assert ("Fiber Length is a text field that holds numbers: 4 of 9 matching records "
            "hold a plain number. The other 5 count as no value.") in out.splitlines()


async def test_a_grouped_total_says_it_too(monkeypatch) -> None:
    import acb_skills.crm_tools as tools

    async def _get(path: str, params: dict | None = None, timeout: float = 30.0) -> dict:
        return _answer(
            values=7_231, records=7_283, group_field="Invoice_Date", bucket="month",
            group_field_label="Invoice Date", group_field_type="date",
            groups=[{"key": "2026-10", "label": "2026-10", "value": 1395.5}],
            text_numbers={"Fiber": {"filled": 7_283, "plain": 7_231}})

    monkeypatch.setattr(tools, "_get", _get)
    out = await tools.crm_aggregate("module:Invoices", measure="sum", field="Fiber",
                                    group_field="Invoice_Date", bucket="month")
    assert ("Fiber Length is a text field that holds numbers: 7,231 of 7,283 matching "
            "records hold a plain number. The other 52 count as no value.") in out.splitlines()


async def test_an_answer_with_no_text_field_has_no_such_line(monkeypatch) -> None:
    import acb_skills.crm_tools as tools

    async def _get(path: str, params: dict | None = None, timeout: float = 30.0) -> dict:
        return _answer(field="Amount", field_label="Amount")

    monkeypatch.setattr(tools, "_get", _get)
    out = await tools.crm_aggregate("module:Invoices", measure="sum", field="Amount")
    assert "text field" not in out


def test_a_split_names_each_text_field_with_its_own_count() -> None:
    import acb_skills.crm_tools as tools

    split = [{"field": "Fiber", "label": "Fiber", "values": 6},
             {"field": "Amount", "label": "Amount", "values": 9}]
    data = _answer(split=split, text_numbers={"Fiber": {"filled": 8, "plain": 6}})
    assert tools._text_number_lines(data, split) == [
        "Fiber is a text field that holds numbers: 6 of 9 matching records hold a "
        "plain number. The other 3 count as no value."]
    # A range alone reads no value, so it gives no line.
    assert tools._text_number_lines(_answer(field=None, values=None,
                                            text_numbers={"Fiber": {"filled": 8, "plain": 6}}),
                                    []) == []
