"""The gateway's one XLSX writer (DOC-6, ``chat_parity.md`` §DOC).

``gateway.xlsx_export`` guards each cell and shapes each sheet. Two fences:

* **The cell rule has one answer.** The sandbox data engine
  (``orchestrator/sandbox/data_engine.py::_xlsx_value``) cannot be imported by
  the gateway, so the gateway holds a copy. :func:`test_the_two_cell_rules_agree`
  feeds the same values to both and fails when they differ (R7).
* **No cell is a formula.** A string that starts as a formula is a string cell
  with Excel's quote prefix, read back from the real file.
"""

from __future__ import annotations

import importlib.util
import io
import sys
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from gateway import xlsx_export as xe
from gateway.csv_export import FORMULA_LEADERS
from openpyxl import load_workbook

_ENGINE = (Path(__file__).resolve().parents[2] / "apps" / "services" / "orchestrator"
           / "sandbox" / "data_engine.py")


def _engine() -> Any:
    spec = importlib.util.spec_from_file_location("data_engine_for_xlsx", _ENGINE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


VALUES: tuple[Any, ...] = (
    None, "", "plain", "=1+1", "+91 98765 43210", "@x", "-30", "a\x00b\x07c\tz\n",
    "x" * 40_000, True, False, 0, -7, 1.5, float("nan"), float("inf"), -float("inf"),
    Decimal("12.30"), date(2026, 11, 28), datetime(2026, 11, 28, 9, 30),
    datetime(2026, 11, 28, 9, 30, tzinfo=UTC), b"\x01\x02", ["a", 1], {"k": "v"},
)


@pytest.mark.parametrize("value", VALUES, ids=range(len(VALUES)))
def test_the_two_cell_rules_agree(value: Any) -> None:
    engine = _engine()
    ours, theirs = xe.cell_value(value), engine._xlsx_value(value)
    assert type(ours) is type(theirs)
    assert ours == theirs
    assert xe.MAX_CELL_CHARS == engine.XLSX_MAX_CELL_CHARS


def _fields(*keys: str) -> list[xe.Field]:
    return [xe.Field(k, k.replace("_", " ").capitalize()) for k in keys]


def _make(spec: dict[str, Any], *tables: xe.Table) -> Any:
    built = xe.build(xe.parse_spec(spec), list(tables))
    return built, load_workbook(io.BytesIO(built.data))


def _sheet(**shape: Any) -> dict[str, Any]:
    return {"sheets": [{"source": {"type": "rows"}, **shape}]}


def _grid(sheet: Any) -> list[list[Any]]:
    return [[c.value for c in row] for row in sheet.iter_rows()]


CUSTOMERS = xe.Table(
    fields=_fields("name", "city", "revenue", "since", "phone"),
    rows=[
        {"name": "Acme", "city": "Pune", "revenue": 100.5, "since": "2024-01-05",
         "phone": "0091 1"},
        {"name": "Beta", "city": "Delhi", "revenue": 200, "since": "2023-03-01",
         "phone": "002"},
        {"name": "Core", "city": "Pune", "revenue": 50, "since": "2025-07-09", "phone": ""},
        {"name": "Dune", "city": None, "revenue": None, "since": None, "phone": None},
    ],
)


def test_a_group_gives_subtotals_and_a_total_row() -> None:
    built, book = _make(_sheet(
        name="Customers", group_by="city", totals=True,
        columns=["name", "city", "revenue"], sort=["name"]), CUSTOMERS)
    grid = _grid(book["Customers"])
    assert grid[0] == ["Name", "City", "Revenue"]
    assert [r[0] for r in grid[1:]] == [
        "Beta", "Delhi: 1 records", "Acme", "Core", "Pune: 2 records",
        "Dune", "(blank): 1 records", "Total: 4 records"]
    assert grid[5][2] == 150.5
    assert grid[-1][2] == 350.5
    assert built.rows == 4
    assert built.sheets[0]["groups"] == 3
    assert book["Customers"].freeze_panes == "A2"
    assert book["Customers"]["A9"].font.bold


def test_columns_renames_sorts_filters_and_types() -> None:
    _, book = _make(_sheet(
        columns=[{"field": "Name", "header": "Customer"},
                 {"field": "revenue", "type": "currency", "currency": "INR"}, "since", "phone"],
        where=[{"field": "city", "equals": "pune"}], sort=["-revenue"]), CUSTOMERS)
    sheet = book.worksheets[0]
    grid = _grid(sheet)
    assert grid[0] == ["Customer", "Revenue", "Since", "Phone"]
    assert [r[0] for r in grid[1:]] == ["Acme", "Core"]
    assert sheet["B2"].number_format == '"INR" #,##0.00'
    assert sheet["C2"].value.date() == date(2024, 1, 5)
    assert sheet["C2"].number_format == "yyyy-mm-dd"
    # A number stored as text stays text, so a leading zero survives.
    assert sheet["D2"].value == "0091 1"
    assert sheet.auto_filter.ref == "A1:D3"
    assert sheet.column_dimensions["A"].width >= len("Customer")


def test_with_no_columns_every_field_that_holds_a_value_shows() -> None:
    table = xe.Table(fields=_fields("name", "fax"), rows=[{"name": "A", "fax": None}])
    _, book = _make(_sheet(), table)
    assert _grid(book.worksheets[0])[0] == ["Name"]


def test_several_sheets_get_legal_distinct_names() -> None:
    spec = {"sheets": [{"name": "A/B: [x]", "source": {"type": "rows"}},
                       {"name": "A B x", "source": {"type": "rows"}},
                       {"name": "y" * 50, "source": {"type": "rows"}}]}
    table = xe.Table(fields=_fields("name"), rows=[{"name": "n"}])
    _, book = _make(spec, table, table, table)
    assert book.sheetnames == ["A B x", "A B x 2", "y" * 31]


@pytest.mark.parametrize("leader", FORMULA_LEADERS)
def test_a_formula_leader_is_a_string_with_a_quote_prefix(leader: str) -> None:
    attack = f"{leader}cmd|'/c calc'!A0"
    table = xe.Table(fields=[xe.Field("name", f"{leader}Header")], rows=[{"name": attack}])
    _, book = _make(_sheet(), table)
    sheet = book.worksheets[0]
    for cell in (sheet["A1"], sheet["A2"]):
        assert cell.data_type == "s", cell.value
        assert cell.quotePrefix is True, cell.value


def test_a_bare_number_is_not_guarded_and_numbers_stay_numbers() -> None:
    table = xe.Table(fields=_fields("n", "s"), rows=[{"n": -30, "s": "-30"}])
    _, book = _make(_sheet(), table)
    sheet = book.worksheets[0]
    assert sheet["A2"].value == -30 and sheet["A2"].data_type == "n"
    assert sheet["B2"].value == "-30" and not sheet["B2"].quotePrefix


def test_an_error_code_string_stays_a_string() -> None:
    table = xe.Table(fields=_fields("s"), rows=[{"s": "#N/A"}])
    _, book = _make(_sheet(), table)
    assert book.worksheets[0]["A2"].data_type == "s"


def test_currencies_that_differ_are_never_added() -> None:
    table = xe.Table(
        fields=_fields("name", "amount", "currency"),
        rows=[{"name": "a", "amount": 1, "currency": "INR"},
              {"name": "b", "amount": 2, "currency": "USD"}])
    _, book = _make(_sheet(columns=["name", {"field": "amount", "type": "currency"}],
                           totals=True), table)
    assert _grid(book.worksheets[0])[-1][1] == "mixed currencies"


def test_the_cap_refuses_the_whole_file() -> None:
    table = xe.Table(fields=_fields("n"), rows=[{"n": i} for i in range(xe.MAX_EXPORT_ROWS + 1)])
    with pytest.raises(xe.SpreadsheetError, match="10001 rows match"):
        _make(_sheet(), table)


def test_two_thousand_rows_work() -> None:
    table = xe.Table(fields=_fields("n"), rows=[{"n": i} for i in range(2000)])
    built, book = _make(_sheet(totals=True), table)
    assert built.rows == 2000
    assert book.worksheets[0].max_row == 2002


def test_no_rows_gives_a_header_and_a_zero_count() -> None:
    built, book = _make(_sheet(columns=["name"]), xe.Table(fields=_fields("name"), rows=[]))
    assert built.rows == 0 and _grid(book.worksheets[0]) == [["Name"]]


@pytest.mark.parametrize(("shape", "says"), [
    ({"columns": ["nope"]}, 'no field "nope". The fields of this source are: name, city'),
    ({"group_by": "nope"}, 'no field "nope"'),
    ({"totals": ["name"]}, "is not a number"),
    ({"pivot": True}, "unknown keys: pivot"),
    ({"columns": [{"field": "name", "type": "money"}]}, "type must be one of"),
    ({"where": [{"field": "name", "like": "a"}]}, "exactly one of"),
])
def test_a_bad_shape_says_what_to_fix(shape: dict[str, Any], says: str) -> None:
    with pytest.raises(xe.SpreadsheetError) as caught:
        _make(_sheet(**shape), CUSTOMERS)
    assert says in str(caught.value)


def test_a_route_refuses_a_source_of_another_type() -> None:
    with pytest.raises(xe.SpreadsheetError, match='type "crm" only'):
        xe.parse_query_spec('{"sheets":[{"source":{"type":"tasks"}}]}', "crm")


def test_a_filter_the_source_does_not_know_is_refused() -> None:
    (plan,) = xe.parse_spec({"sheets": [{"source": {"type": "crm", "city": "Pune"}}]})
    with pytest.raises(xe.SpreadsheetError, match="has no filter city"):
        xe.source_args(plan, frozenset({"q"}))


def test_the_summary_header_is_short_ascii() -> None:
    built = xe.Built(b"", [{"sheet": "क", "rows": 1, "columns": ["क" * 200] * 100,
                            "groups": 0}] * 10, 10)
    header = xe.summary_header(built)
    assert header.isascii() and len(header) <= 8000


def test_totals_never_add_a_task_number() -> None:
    table = xe.Table(fields=_fields("number", "estimate"),
                     rows=[{"number": 7, "estimate": 30}, {"number": 9, "estimate": 60}])
    _, book = _make(_sheet(totals=True), table)
    assert _grid(book.worksheets[0])[-1] == ["Total: 2 records", 90]


def test_a_huge_number_text_and_an_old_date_stay_text() -> None:
    table = xe.Table(fields=_fields("n", "d"),
                     rows=[{"n": "1e999999999", "d": "1850-01-02"}])
    _, book = _make(_sheet(columns=[{"field": "n", "type": "number"},
                                    {"field": "d", "type": "date"}]), table)
    assert _grid(book.worksheets[0])[1] == ["1e999999999", "1850-01-02"]
