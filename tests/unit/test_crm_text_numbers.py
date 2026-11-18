"""A text field that holds numbers (WS-53 CRM-Q34-11, task 3728).

Spec: ``project-docs/specs/crm_platform.md`` §18.17, the row CRM-Q34-11.

Hermetic. The behaviour on a real database, with the matrix of raw values,
is proved in ``tests/unit/test_crm_text_numbers_r8.py``. Here:

1. The number of every other field keeps its SQL text of before, byte for
   byte. The text number runs its regex once, inside the CASE.
2. The pattern uses ASCII digits only, and no ``\\d`` or ``\\s``.
3. ``admit_text_numbers`` takes a field at half of its filled values, and
   refuses one below half, or with no number, with the counts and shapes.
4. A call that names no text field runs no statement more.
5. A text equality filter stays the text filter of before.
6. The wire is the one of before when no text field was read.
7. The tool text: the answer line and the use of a text field.
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
    def __init__(self, rows: list[tuple]) -> None:
        self._rows = rows

    def one(self) -> tuple:
        return self._rows[0]

    def fetchall(self) -> list[tuple]:
        return self._rows


class _Db:
    """A session that answers each statement with the next list of rows,
    and keeps each statement that it got."""

    def __init__(self, *answers: list[tuple]) -> None:
        self.answers = list(answers)
        self.seen: list[tuple[str, dict]] = []

    async def execute(self, statement: Any, params: dict | None = None) -> _Result:
        self.seen.append((str(statement), dict(params or {})))
        return _Result(self.answers.pop(0))


# ── 1. The SQL text ───────────────────────────────────────────────────────────


def test_the_number_of_every_other_field_keeps_its_text_of_before() -> None:
    from gateway.routes.crm import field_sql as fs

    assert fs._number("c", "k") == _NUMBER_BEFORE
    assert fs._number("c", "k", False) == _NUMBER_BEFORE
    ref = fs.FieldRef(key="Amount", type="currency", label="Amount")
    (sql, bound), = fs._numbers("c", ref, "k")
    assert sql == _NUMBER_BEFORE.replace(":k", ":k_0") and bound == {"k_0": "Amount"}


def test_a_text_number_runs_its_regex_once_inside_the_case() -> None:
    from gateway.routes.crm import field_sql as fs

    ref = fs.FieldRef(key="Fiber", type="text", label="Fiber", text_number=True)
    (sql, bound), = fs._numbers("c", ref, "k")
    assert bound == {"k_0": "Fiber", fs.TEXT_NUMBER_PARAM: fs.TEXT_NUMBER_PATTERN}
    assert "Fiber" not in sql and sql.count("substring(") == 1
    # The cast reads only the captured number, in the THEN of the CASE, and
    # the length bound runs before it.
    assert sql.startswith("(CASE WHEN ") and sql.endswith(" END)")
    assert f"<= {fs.TEXT_NUMBER_MAX_CHARS} THEN CAST(replace(substring(" in sql
    assert sql.index("length(") < sql.index("substring(")
    assert " ~ " not in sql


def test_every_cast_of_the_two_reads_sits_in_a_then() -> None:
    """The fence of the brief: never ``regex AND cast`` in a WHERE. Each
    cast of a value is the THEN of a CASE. The other casts read a bound org,
    the bound words, or the empty jsonb of the one detoast."""
    from gateway.routes.crm import field_sql as fs

    for sql, params in (fs.text_numbers_sql(["Ka", "Kb"]), fs.text_shapes_sql(["Ka", "Kb"])):
        for found in re.finditer(r"CAST\(", sql):
            at = found.start()
            assert (sql[:at].endswith("THEN ")
                    or sql[at:].startswith(("CAST(:agf_org", "CAST(:agf_nv", "CAST('{}'"))), sql[at - 40:at + 40]
        assert "Ka" not in sql and "Kb" not in sql
        assert {params["agf_n0_0"], params["agf_n1_0"]} == {"Ka", "Kb"}


# ── 2. The pattern ────────────────────────────────────────────────────────────


def test_the_pattern_reads_ascii_digits_only() -> None:
    from gateway.routes.crm import field_sql as fs

    for banned in ("\\d", "\\s", "\\w", "\\D", "\\S"):
        assert banned not in fs.TEXT_NUMBER_PATTERN, banned
    assert "[0-9]" in fs.TEXT_NUMBER_PATTERN
    # One capture group: substring gives the number, never a currency mark.
    assert re.compile(fs.TEXT_NUMBER_PATTERN).groups == 1
    assert fs.TEXT_NUMBER_MAX_CHARS == 32


@pytest.mark.parametrize(("value", "number"), [
    ("120", "120"), ("0", "0"), ("-0.5", "-0.5"), ("1,234,567.89", "1,234,567.89"),
    ("12,34,567", "12,34,567"), ("₹ 1,200", "1,200"), ("Rs. 500", "500"),
    ("500 INR", "500"), ("12.5 %", "12.5"),
    ("1,5", None), ("12,34", None), ("1,2,3", None), ("007", None), ("NaN", None),
    ("Infinity", None), ("1e3", None), ("500 m", None), ("Rs 500 INR", None),
    ("٣", None), ("1" * 31, None),
])
def test_the_pattern_in_python_agrees_with_the_matrix(value: str, number: str | None) -> None:
    """A mirror of the R8 matrix in Python. Postgres is the proof: the R8
    file reads the same values back through the route."""
    from gateway.routes.crm import field_sql as fs

    found = re.search(fs.TEXT_NUMBER_PATTERN, value)
    assert (found.group(1) if found else None) == number


# ── 3. The gate ───────────────────────────────────────────────────────────────


async def test_a_text_field_at_half_of_its_filled_values_reads_as_numbers() -> None:
    from gateway.routes.crm import field_sql as fs

    scope = _scope(Fiber="text", Amount="currency", Invoice_Date="date")
    # 12 records: 5 numbers, 2 with no value, 5 not numbers.
    db = _Db([(12, 5, 2)], [(0, "9 a", 4), (0, "9,9", 1)])
    out, counts = await fs.admit_text_numbers(db, scope, ["Fiber", "Amount"])
    assert counts == {"Fiber": fs.TextNumbers(numbers=5, no_value=2, not_numbers=5,
                                              shapes=(("9 a", 4), ("9,9", 1)))}
    fiber = out.fields["Fiber"]
    assert (fiber.type, fiber.text_number) == ("text", True)
    assert out.fields["Amount"] == scope.fields["Amount"]
    assert not scope.fields["Fiber"].text_number
    assert fs.check_measure(out, "Fiber").text_number
    # Two statements: the counts, then the shapes. Each binds the key.
    assert len(db.seen) == 2
    for sql, params in db.seen:
        assert "Fiber" not in sql and "Invoices" not in sql
        assert params["agf_n0_0"] == "Fiber" and params["agf_module"] == "Invoices"
        assert params[fs.TEXT_NUMBER_PARAM] == fs.TEXT_NUMBER_PATTERN
        assert params["agf_nv"] == list(fs.NO_VALUE_WORDS) and "agf_org" in params
        assert fs.DETOAST_JOIN in sql
    assert f"LIMIT {fs.SHAPES_MAX}" in db.seen[1][0]
    assert counts["Fiber"].wire() == {
        "numbers": 5, "no_value": 2, "not_numbers": 5,
        "shapes": [{"shape": "9 a", "words": "a number with a unit word", "count": 4},
                   {"shape": "9,9", "words": "a comma that is no thousands group",
                    "count": 1}],
    }


async def test_a_field_with_every_value_a_number_reads_no_shapes() -> None:
    from gateway.routes.crm import field_sql as fs

    db = _Db([(10, 9, 1)])
    _out, counts = await fs.admit_text_numbers(db, _scope(Fiber="text"), ["Fiber"])
    assert len(db.seen) == 1 and counts["Fiber"].shapes == ()


async def test_a_field_below_half_is_a_422_with_its_counts_and_shapes() -> None:
    from fastapi import HTTPException
    from gateway.routes.crm import field_sql as fs

    scope = _scope(Notes="text", Amount="currency")
    db = _Db([(10, 4, 1)], [(0, "a", 3), (0, "a-a-9", 2)])
    with pytest.raises(HTTPException) as caught:
        await fs.admit_text_numbers(db, scope, ["Notes"])
    detail = caught.value.detail
    assert caught.value.status_code == 422 and detail["code"] == "unfit_field"
    assert detail["message"] == (
        "'Notes' is a text field, and fewer than half of its filled values are numbers, "
        "so it looks like a code or free text. In this module: 4 numbers, 1 with no value, "
        "5 not numbers. Most common of these: words only (3), a code (2). A measure takes "
        "a field of type number, decimal, currency, percent, or a text field that holds "
        "numbers. The number fields of module:Invoices are: Amount.")


async def test_a_field_with_no_number_is_a_422_with_its_counts() -> None:
    from fastapi import HTTPException
    from gateway.routes.crm import field_sql as fs

    with pytest.raises(HTTPException) as caught:
        await fs.admit_text_numbers(_Db([(3, 0, 3)]), _scope(Fiber="text"), ["Fiber"])
    assert caught.value.detail["message"].startswith(
        "'Fiber' is a text field, and no value of it is a number. In this module: "
        "0 numbers, 3 with no value, 0 not numbers. A measure takes")


@pytest.mark.parametrize(("numbers", "not_numbers", "fits"), [
    (5, 5, True), (50, 50, True), (49, 51, False), (1, 0, True), (0, 0, False),
    (0, 5, False), (7231, 52, True), (10549, 0, True), (1, 1, True), (1, 2, False),
])
def test_the_gate_is_half_of_the_filled_values(numbers: int, not_numbers: int,
                                               fits: bool) -> None:
    from gateway.routes.crm import field_sql as fs

    found = fs.TextNumbers(numbers=numbers, no_value=99, not_numbers=not_numbers)
    assert fs.text_number_fits(found) is fits


# ── 4. No statement more ─────────────────────────────────────────────────────


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


async def test_a_core_entity_never_reads_a_text_field_as_numbers() -> None:
    from gateway.routes.crm import field_sql as fs

    ref = fs.FieldRef(key="Fiber", type="text", label="Fiber")
    scope = fs.FieldScope(column="custom", owner="deals", fields={"Fiber": ref})
    db = _Db()
    out, counts = await fs.admit_text_numbers(db, scope, ["Fiber"])
    assert out is scope and counts == {} and db.seen == []


def test_the_keys_that_a_call_reads_as_numbers() -> None:
    from gateway.routes.crm import field_sql as fs

    pairs = [("Fiber.from", "100"), ("Fiber.to", "2000"), ("Notes", "x"),
             ("Invoice_Date.from", "2026-01-01")]
    assert fs.text_number_keys("sum", "Fee", None, pairs) == ["Fee", "Fiber", "Invoice_Date"]
    assert fs.text_number_keys("count", "Fee", None, []) == []
    assert fs.text_number_keys("avg", None, "Fiber, Amount", []) == ["Fiber", "Amount"]
    # An equality filter is never read as a number.
    assert fs.text_number_keys("count", None, None, [("Notes", "1")]) == []


def test_the_read_admits_before_any_check() -> None:
    import inspect

    from gateway.routes.crm import aggregate

    body = inspect.getsource(aggregate._read)
    admit = body.index("admit_text_numbers(")
    assert body.index("module_measure(") < admit < body.index("module_where(")
    assert admit < body.index("text_matches(")
    assert admit < body.index("check_split(") and admit < body.index("check_measure(")


def test_a_sum_of_fields_never_takes_a_text_part() -> None:
    """``@amount`` and a ``+`` sum take currency parts only, so a text
    field never reaches a sum of fields."""
    scope = _scope(Fiber="text", Rent="currency")
    assert scope._sum_of("Fiber+Rent") is None


# ── 5. Text equality stays text equality ─────────────────────────────────────


def test_a_text_equality_filter_is_the_filter_of_before() -> None:
    from dataclasses import replace

    from fastapi import HTTPException
    from gateway.routes.crm import field_sql as fs

    before = _scope(Fiber="text")
    after = replace(before, fields={"Fiber": replace(before.fields["Fiber"],
                                                     text_number=True)})
    pairs = [("Fiber", "1,200")]
    old = fs.module_where(before, "Invoices", pairs, zone="UTC")
    new = fs.module_where(after, "Invoices", pairs, zone="UTC")
    assert (new.sql, new.params, new.source) == (old.sql, old.params, old.source)
    # A range reads the number. Without the mark, a range is a 422, as before.
    ranged = fs.module_where(after, "Invoices", [("Fiber.from", "100")], zone="UTC")
    assert "substring(" in ranged.sql and ranged.params["agf_v0"] == "100"
    with pytest.raises(HTTPException):
        fs.module_where(before, "Invoices", [("Fiber.from", "100")], zone="UTC")
    with pytest.raises(HTTPException):
        fs.check_measure(before, "Fiber")


# ── 6. The shapes and the wire ───────────────────────────────────────────────


@pytest.mark.parametrize(("shape", "words"), [
    ("9 a", "a number with a unit word"), ("9a", "a number with a unit word"),
    ("9 a a", "a number with a unit word"), ("a", "words only"), ("a a", "words only"),
    ("9-9", "a range"), ("9 - 9", "a range"), ("9-9-9", "a date or a code"),
    ("9/9/9", "a date or a code"), ("a-a-9", "a code"), ("a/a/9-9/9", "a code"),
    ("a_a_9", "a code"), ("a9", "a code"), ("9 9", "two numbers"),
    ("9", "digits with a leading zero, or too many digits"),
    ("9,9", "a comma that is no thousands group"), ("(9,9)", "a number in brackets"),
    ("9a9", "an exponent or a code"), ("(array)", "a JSON list"),
    ("(object)", "a JSON object"), ("(boolean)", "true or false"), ("+9", "the shape '+9'"),
])
def test_each_shape_has_its_words(shape: str, words: str) -> None:
    from gateway.routes.crm import field_sql as fs

    assert fs.shape_words(shape) == words


def _response(**extra: Any) -> Any:
    from gateway.routes.crm.aggregate import AggregateResponse

    return AggregateResponse(entity="module:Invoices", measure="sum", field="Amount",
                             groups=[], total=5.0, values=1, records=1, **extra)


def test_the_wire_is_the_one_of_before_with_no_text_field() -> None:
    plain = _response().model_dump_json()
    assert "text_numbers" not in plain
    assert _response(text_numbers=None).model_dump_json() == plain
    assert _response(text_numbers={}).model_dump_json() == plain
    entry = {"numbers": 5, "no_value": 2, "not_numbers": 1,
             "shapes": [{"shape": "9 a", "words": "a number with a unit word", "count": 1}]}
    assert _response(text_numbers={"Fiber": entry}).model_dump()["text_numbers"] == {
        "Fiber": entry}


# ── 7. The tool text ──────────────────────────────────────────────────────────


def _answer(**extra: Any) -> dict[str, Any]:
    return {"entity": "module:Invoices", "measure": "sum", "field": "Fiber",
            "field_type": "text", "field_label": "Fiber Length", "groups": [],
            "total": 1395.5, "other": 0, "omitted": 0, "values": 7_231,
            "records": 7_283, **extra}


_COUNTS = {"numbers": 7_231, "no_value": 0, "not_numbers": 52, "shapes": [
    {"shape": "9 a", "words": "a number with a unit word", "count": 37},
    {"shape": "a", "words": "words only", "count": 15}]}
_LINE = ("Fiber Length is a text field read as numbers. In this module: 7,231 numbers, "
         "0 with no value, 52 not numbers. Left out, most common: a number with a unit "
         "word (37), words only (15). This total read 7,231 numbers of 7,283 matching "
         "records.")


async def test_a_total_names_the_three_counts_and_the_shapes(monkeypatch) -> None:
    import acb_skills.crm_tools as tools

    async def _get(path: str, params: dict | None = None, timeout: float = 30.0) -> dict:
        return _answer(text_numbers={"Fiber": _COUNTS})

    monkeypatch.setattr(tools, "_get", _get)
    out = await tools.crm_aggregate("module:Invoices", measure="sum", field="Fiber")
    assert _LINE in out.splitlines()


async def test_a_grouped_total_names_them_too(monkeypatch) -> None:
    import acb_skills.crm_tools as tools

    async def _get(path: str, params: dict | None = None, timeout: float = 30.0) -> dict:
        return _answer(group_field="Invoice_Date", bucket="month",
                       group_field_label="Invoice Date", group_field_type="date",
                       groups=[{"key": "2026-10", "label": "2026-10", "value": 1395.5}],
                       text_numbers={"Fiber": _COUNTS})

    monkeypatch.setattr(tools, "_get", _get)
    out = await tools.crm_aggregate("module:Invoices", measure="sum", field="Fiber",
                                    group_field="Invoice_Date", bucket="month")
    assert _LINE in out.splitlines()


async def test_an_answer_with_no_text_field_has_no_such_line(monkeypatch) -> None:
    import acb_skills.crm_tools as tools

    async def _get(path: str, params: dict | None = None, timeout: float = 30.0) -> dict:
        return _answer(field="Amount", field_label="Amount", field_type="currency")

    monkeypatch.setattr(tools, "_get", _get)
    out = await tools.crm_aggregate("module:Invoices", measure="sum", field="Amount")
    assert "text field" not in out


def test_a_split_and_a_range_name_each_text_field() -> None:
    import acb_skills.crm_tools as tools

    split = [{"field": "Fiber", "label": "Fiber", "values": 6},
             {"field": "Amount", "label": "Amount", "values": 9}]
    one = {"numbers": 6, "no_value": 1, "not_numbers": 1, "shapes": []}
    data = _answer(split=split, records=9, text_numbers={"Fiber": one})
    assert tools._text_number_lines(data, split) == [
        "Fiber is a text field read as numbers. In this module: 6 numbers, 1 with no "
        "value, 1 not a number. This total read 6 numbers of 9 matching records."]
    # A range alone reads no value of the field: the counts only.
    ranged = _answer(field=None, values=None, measure="count",
                     text_numbers={"Fiber": one})
    assert tools._text_number_lines(ranged, []) == [
        "Fiber is a text field read as numbers. In this module: 6 numbers, 1 with no "
        "value, 1 not a number."]


async def test_the_field_list_names_the_use_of_a_text_field_of_a_module(monkeypatch) -> None:
    import acb_skills.crm_tools as tools

    defs = [{"key": "Fiber", "label": "Fiber", "type": "text"},
            {"key": "Amount", "label": "Amount", "type": "currency"}]

    async def _get(path: str, params: dict | None = None, timeout: float = 30.0) -> Any:
        return defs

    monkeypatch.setattr(tools, "_get", _get)
    module = await tools.crm_list_fields("module:Invoices")
    assert ("- Fiber (key Fiber, text) [sums and ranges if it holds numbers, the answer "
            "counts the rest]") in module.splitlines()
    # A core entity is out of this slice: its text field names no use.
    core = await tools.crm_list_fields("deals")
    assert "(key Fiber, text)" in core and "(key Fiber, text) [" not in core
