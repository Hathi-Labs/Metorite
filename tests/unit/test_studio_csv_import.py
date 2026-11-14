"""WS-STU C11: the CSV planner, the hermetic half (task 1419).

Code: ``gateway/studio/csv_import.py``. No database. They pin the parse caps,
the map checks, each cell rule, the error list (never a cell), the digest and
the approval line. The source fence says the planner opens no connection and
never writes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")

from acb_common.studio_fields import FieldSpec
from gateway.studio import csv_import as ci

_ROOT = Path(__file__).resolve().parents[2]
_SRC = (_ROOT / "apps/services/gateway/gateway/studio/csv_import.py").read_text(encoding="utf-8")
_OBJ = "11111111-1111-1111-1111-111111111111"
_PKG = "44444444-4444-4444-4444-444444444444"

FIELDS = [
    FieldSpec(key="name", type="text", required=True),
    FieldSpec(key="qty", type="number"),
    FieldSpec(key="price", type="money"),
    FieldSpec(key="due", type="date"),
    FieldSpec(key="paid", type="checkbox"),
    FieldSpec(key="stage", type="select", options=("new", "won")),
    FieldSpec(key="tags", type="multiSelect", options=("a", "b", "c")),
    FieldSpec(key="mail", type="email", label="Email address"),
    FieldSpec(key="disc", type="percent"),
    FieldSpec(key="owner", type="person"),
]
MAP = {
    "Name": "name",
    "Qty": "qty",
    "Price": "price",
    "Due": "due",
    "Paid": "paid",
    "Stage": "stage",
    "Tags": "tags",
    "Email": "mail",
    "Disc": "disc",
}
HEAD = "Name,Qty,Price,Due,Paid,Stage,Tags,Email,Disc\n"


def _plan(body: str, mapping=MAP, **kw):
    return ci.plan_import(
        ci.parse_csv(HEAD + body),
        FIELDS,
        mapping,
        object_id=_OBJ,
        currency=kw.pop("currency", "INR"),
        **kw,
    )


def test_parse_strips_bom_and_detects_separator() -> None:
    t = ci.parse_csv("﻿a;b\n1;2\n\n3;4\n")
    assert t.headers == ("a", "b") and t.rows == (("1", "2"), ("3", "4"))


@pytest.mark.parametrize("text", ["", "   \n", "a,a\n1,2", "a,\n1,2", 'a,b\n"x,y\n'])
def test_parse_refuses(text: str) -> None:
    with pytest.raises(ci.CsvImportError):
        ci.parse_csv(text)


def test_parse_caps() -> None:
    with pytest.raises(ci.CsvImportError):
        ci.parse_csv("a\n" + "1\n" * (ci.MAX_ROWS + 1))
    with pytest.raises(ci.CsvImportError):
        ci.parse_csv(",".join(f"c{i}" for i in range(ci.MAX_COLUMNS + 1)))
    with pytest.raises(ci.CsvImportError):
        ci.parse_csv("a\n" + "x" * ci.MAX_BYTES)


def test_one_valid_row_coerces_every_type() -> None:
    p = _plan("Bolt,12,1234.50,2026-11-14,yes,won,a;c,A@B.io,12.5%\n")
    assert not p.errors and p.valid_rows == 1
    assert p.preview[0] == {
        "name": "Bolt",
        "qty": 12,
        "price": {"minor": 123450, "currency": "INR"},
        "due": "2026-11-14",
        "paid": True,
        "stage": "won",
        "tags": ["a", "c"],
        "mail": "a@b.io",
        "disc": 1250,
    }
    w = p.items[0].writes[0]
    assert (p.items[0].key, w.op, w.object_id) == ("row-1", "create", _OBJ)


def test_empty_cell_is_no_value() -> None:
    p = _plan("Bolt,,,,,,,,\n")
    assert p.preview[0] == {"name": "Bolt"}


def test_errors_name_row_and_field_and_never_the_cell() -> None:
    p = _plan("Bolt,SECRET1,,,,,,,\n,1,,,,,,,\nNut,2,,2026-13-45,,,,,\n")
    assert p.valid_rows == 0 and p.total_rows == 3
    assert {(e.row, e.field) for e in p.errors} == {(1, "qty"), (2, "name"), (3, "due")}
    assert all("SECRET1" not in e.message for e in p.errors)


def test_money_needs_a_currency_and_two_decimals() -> None:
    p = _plan("Bolt,,1.005,,,,,,\n")
    assert p.errors[0].field == "price"
    p = _plan("Bolt,,5,,,,,,\n", currency=None)
    assert p.errors[0].field == "price"
    with pytest.raises(ci.CsvImportError):
        _plan("Bolt\n", currency="inr")


def test_map_refusals() -> None:
    t = ci.parse_csv(HEAD + "Bolt,,,,,,,,\n")
    for bad in (
        {"Nope": "name"},
        {"Name": "ghost"},
        {"Name": "owner"},
        {"Name": "name", "Qty": "name"},
        {"Qty": "qty"},
        {},
    ):
        with pytest.raises(ci.CsvImportError):
            ci.plan_import(t, FIELDS, bad, object_id=_OBJ)


def test_suggest_mapping_uses_key_and_label() -> None:
    got = ci.suggest_mapping(["NAME", "Email Address", "owner", "zzz"], FIELDS)
    assert got == {"NAME": "name", "Email Address": "mail"}


def test_error_list_is_capped_and_marked() -> None:
    p = _plan("Bolt,x,,,,,,,\n" * 300)
    assert len(p.errors) == ci.MAX_ERRORS and p.errors_truncated


def test_approval_line_and_digest_binds_the_list() -> None:
    small = _plan("Bolt,1,,,,,,,\n" * ci.APPROVAL_ROWS)
    big = _plan("Bolt,1,,,,,,,\n" * (ci.APPROVAL_ROWS + 1))
    assert not small.needs_approval and big.needs_approval
    other = _plan("Bolt,2,,,,,,,\n" * (ci.APPROVAL_ROWS + 1))
    assert big.digest(_PKG) == big.digest(_PKG) != other.digest(_PKG)
    assert set(big.writes_by_key()) == {i.key for i in big.items}


def test_source_opens_no_connection_and_never_writes() -> None:
    for banned in ("execute(", "commit(", "get_db", "text(", "run_chunk", "open_batch"):
        assert banned not in _SRC.split('"""', 2)[2], banned


@pytest.mark.parametrize("cell", ["1,5", "1 000", "12,34", "1,23,456", ",5"])
def test_a_comma_that_is_no_thousands_group_is_refused(cell: str) -> None:
    """A European file writes 1,5 for 1.5. A read as 15 is a ten-fold error."""
    p = _plan(f'Bolt,,"{cell}",,,,,,\n')
    assert p.valid_rows == 0 and p.errors[0].field == "price"


def test_a_thousands_group_is_read() -> None:
    p = _plan('Bolt,,"1,234.50",,,,,,\n')
    assert p.preview[0]["price"] == {"minor": 123450, "currency": "INR"}


def test_a_number_of_more_than_15_digits_and_a_long_row_are_refused() -> None:
    p = _plan("Bolt,0.1234567890123456,,,,,,,\nNut,1,,,,,,,,extra\n")
    assert {(e.row, e.field) for e in p.errors} == {(1, "qty"), (2, None)}


def test_money_uses_the_minor_digits_of_the_currency() -> None:
    jpy = _plan("Bolt,,500,,,,,,\n", currency="JPY")
    kwd = _plan("Bolt,,1.500,,,,,,\n", currency="KWD")
    assert jpy.preview[0]["price"] == {"minor": 500, "currency": "JPY"}
    assert kwd.preview[0]["price"] == {"minor": 1500, "currency": "KWD"}
    assert _plan("Bolt,,1.5,,,,,,\n", currency="JPY").errors[0].field == "price"


def test_a_required_field_that_takes_no_cell_gives_one_clear_sentence() -> None:
    fields = [
        FieldSpec(key="name", type="text"),
        FieldSpec(key="owner", type="person", required=True),
    ]
    with pytest.raises(ci.CsvImportError, match="cannot take a CSV import"):
        ci.plan_import(ci.parse_csv("Name\nx\n"), fields, {"Name": "name"}, object_id=_OBJ)


def test_a_tiny_number_is_refused_with_no_exponent_message() -> None:
    assert _plan("Bolt,0.00005,,,,,,,\n").errors[0].field == "qty"


def test_a_bad_map_never_crashes_or_echoes() -> None:
    t = ci.parse_csv(HEAD + "Bolt,,,,,,,,\n")
    for bad in ({"Name": ["x"]}, {"Name": "ghost\x00" * 50}, {1: "name"}):
        with pytest.raises(ci.CsvImportError) as e:
            ci.plan_import(t, FIELDS, bad, object_id=_OBJ)
        assert "\x00" not in str(e.value)


def test_the_row_cap_stops_before_the_whole_grid_is_built() -> None:
    with pytest.raises(ci.CsvImportError, match="more than"):
        ci.parse_csv("a\n" + "1\n" * (ci.MAX_ROWS * 3))


def test_a_quoted_header_with_a_semicolon_keeps_the_comma() -> None:
    t = ci.parse_csv('"Notes; a; b",Name\nx,y\n')
    assert t.headers == ("Notes; a; b", "Name")
