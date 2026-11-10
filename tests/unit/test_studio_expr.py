"""WS-STU C7a: the closed expression tree of a formula field, with no database.

Spec: ``project-docs/specs/ai_studio.md`` section 3.7 (layer 2) and the section 14
C7a row. Code: ``packages/acb_common/acb_common/studio_expr.py``. The rule kind is
in ``studio_rules.py`` (``test_studio_rules.py``), and the store runs it
(``test_studio_formula_r8.py``).

The mutations of this file (R7). Commit first, turn each one red, then restore.

* M1. ``_round_ratio``: ``half_up`` becomes ``twice > den``.
  ``test_a_tie_rounds_as_the_named_mode`` goes red.
* M2. ``_divide``: skip the rounding (``num // den``).
  ``test_div_rounds_at_its_node`` goes red.
* M3. ``_div``: answer ``Decimal(x) / Decimal(y)`` through a ``float``.
  ``test_div_gives_an_exact_decimal_never_a_float`` goes red.
* M4. ``_parse``: remove the node cap.
  ``test_the_node_cap_is_64`` goes red.
* M5. ``_Run._if``: run both branches.
  ``test_if_is_lazy`` goes red.
* M6. ``_apply``: drop the null rule.
  ``test_a_null_gives_null`` goes red.
* M7. ``_is_key``: accept a dot. ``test_a_dotted_or_foreign_ref_is_refused`` goes red.
"""

from __future__ import annotations

import ast
import decimal
import json
from pathlib import Path
from typing import Any

import pytest
from acb_common import studio_expr as x
from acb_common import studio_fields as sf
from acb_common.studio_expr import ExprError, Money, Node

_FILE = Path(__file__).resolve().parents[2] / "packages/acb_common/acb_common/studio_expr.py"

D = decimal.Decimal


def n(text: str) -> dict[str, Any]:
    return {"num": text}


def ref(key: str) -> dict[str, Any]:
    return {"ref": key}


def fn(name: str, *args: Any, **keys: Any) -> dict[str, Any]:
    return {"fn": name, "args": list(args), **keys}


def money(minor: int, currency: str = "INR") -> dict[str, Any]:
    return {"money": {"minor": minor, "currency": currency}}


def ev(expr: Any, record: dict[str, Any] | None = None, **kw: Any) -> Any:
    return x.evaluate(x.parse_expr(expr), record or {}, **kw)


def refused(expr: Any) -> ExprError:
    with pytest.raises(ExprError) as info:
        x.parse_expr(expr)
    return info.value


def run_code(expr: Any, record: dict[str, Any] | None = None) -> str:
    with pytest.raises(ExprError) as info:
        ev(expr, record)
    return info.value.code


def fields(**types: str) -> list[sf.FieldSpec]:
    return [sf.FieldSpec(key=k, type=t) for k, t in types.items()]


FIELDS = fields(
    qty="number", rate="money", label="text", ok="checkbox", day="date", state="select",
    when="datetime", sizes="number")
FIELDS[-1] = sf.FieldSpec(key="sizes", type="number", many=True)


def typed(expr: Any, target: str | None = None) -> str:
    return x.check_expr(x.parse_expr(expr), FIELDS, target=target)


def type_refused(expr: Any, target: str | None = None) -> ExprError:
    with pytest.raises(ExprError) as info:
        typed(expr, target)
    return info.value


# -- the parser and its caps ---------------------------------------------------

_ROUND_TRIP = [
    ref("qty"), n("0.12"), n("-5"), money(500), {"text": "hi"}, {"bool": True},
    {"date": "2026-01-31"},
    fn("add", ref("qty"), n("1")),
    fn("div", ref("qty"), n("3"), rounding="half_even", places=4),
    fn("if", fn("gt", ref("qty"), n("0")), {"text": "a"}, {"text": "b"}),
]


@pytest.mark.parametrize("expr", _ROUND_TRIP)
def test_a_tree_reads_back_equal(expr: Any) -> None:
    node = x.parse_expr(expr)
    assert x.node_json(node) == expr
    assert hash(node) == hash(x.parse_expr(json.loads(json.dumps(expr))))


def _chain(depth: int) -> dict[str, Any]:
    out: dict[str, Any] = n("1")
    for _ in range(depth - 1):
        out = fn("neg", out)
    return out


def test_the_depth_cap_is_8() -> None:
    x.parse_expr(_chain(8))
    err = refused(_chain(9))
    assert err.code == x.DECLARATION and "deeper than 8" in err.message
    assert err.path is not None and err.path.startswith("expr.args[0]")


def test_the_node_cap_is_64() -> None:
    root = fn("add", *[fn("add", *[fn("add", n("1"), n("1"), n("1"), n("1"))
                                     for _ in range(4)]) for _ in range(4)])
    leaf4 = fn("add", n("1"), n("1"), n("1"), n("1"))  # 5 nodes
    mid = fn("add", leaf4, leaf4, leaf4, leaf4)  # 21 nodes
    exact = fn("add", mid, mid, fn("add", n("1"), n("1"), n("1")), n("1"))  # 1+21+21+4+1 = 48
    assert _count(exact) == 48
    x.parse_expr(exact)
    pad = fn("add", exact, leaf4, leaf4, n("1"))  # 1 + 48 + 5 + 5 + 1 = 60
    pad = fn("add", pad, n("1"), n("1"), n("1"))  # 64
    assert _count(pad) == 64
    x.parse_expr(pad)
    over = fn("add", pad, n("1"))  # 66 nodes, but depth fine
    assert _count(over) == 66
    err = refused(over)
    assert "more than 64 nodes" in err.message and err.path is not None
    assert _count(root) > 64
    assert "more than 64 nodes" in refused(root).message


def _count(expr: Any) -> int:
    if isinstance(expr, dict) and "fn" in expr:
        return 1 + sum(_count(a) for a in expr["args"])
    return 1


def test_a_call_takes_at_most_4_args() -> None:
    err = refused(fn("add", *[n("1")] * 5))
    assert err.path == "expr.args" and "2 to 4" in err.message


@pytest.mark.parametrize("name,count", [("sub", 3), ("div", 1), ("neg", 2), ("if", 2), ("not", 0)])
def test_the_arity_of_a_function_is_closed(name: str, count: int) -> None:
    assert refused(fn(name, *[n("1")] * count)).path == "expr.args"


@pytest.mark.parametrize("value", [1, 1.5, True, None, "", "-", "1.", ".5", "+1", "1e3", " 1",
                                   "1.1234567", "1" * 16, "0x10", "1,5", "--1", "1.2.3"])
def test_a_num_is_decimal_text_with_at_most_6_places(value: Any) -> None:
    err = refused({"num": value})
    assert err.path == "expr.num" and "decimal text" in err.message


@pytest.mark.parametrize("text", ["0", "-0", "0.123456", "-999999999999999.999999", "7"])
def test_a_good_num(text: str) -> None:
    assert x.node_json(x.parse_expr({"num": text})) == {"num": text}


def test_a_text_literal_holds_200_characters_and_no_nul() -> None:
    x.parse_expr({"text": "a" * 200})
    assert refused({"text": "a" * 201}).path == "expr.text"
    assert refused({"text": "a\x00b"}).path == "expr.text"
    assert refused({"text": 5}).path == "expr.text"
    assert refused({"text": "\ud800"}).path == "expr.text"


@pytest.mark.parametrize("key", ["a.b", "app.object.field", "Qty", "1a", "", "a-b", "x" * 49,
                                 ".", "a b", 3, None, ["a"]])
def test_a_dotted_or_foreign_ref_is_refused(key: Any) -> None:
    err = refused({"ref": key})
    assert err.path == "expr.ref" and "bare key" in err.message


def test_every_key_shape_agrees_with_the_field_module() -> None:
    import re

    pattern = re.compile(sf.NAME_PATTERN, re.ASCII)
    for sample in ["a", "a1", "a_b", "z" * 48, "z" * 49, "A", "_a", "1a", "a.b", "", "é", "a\n"]:
        assert x._is_key(sample) == (pattern.fullmatch(sample) is not None), sample


@pytest.mark.parametrize("bad", [
    5, "x", [], {}, {"ref": "a", "num": "1"}, {"nope": 1}, {"fn": "nope", "args": []},
    {"fn": "add"}, {"fn": "add", "args": "x"}, {"fn": "add", "args": [n("1"), n("1")], "x": 1},
    {"fn": 3, "args": []}, {"bool": "true"}, {"bool": 1}, {"date": "2026-02-30"},
    {"date": "2026-1-5"}, {"date": 5}, {"money": {"minor": 1}},
    {"money": {"minor": 1.5, "currency": "INR"}}, {"money": {"minor": 1, "currency": "inr"}},
    {"money": {"minor": 10**15, "currency": "INR"}}, {"money": 5},
])
def test_a_bad_node_is_refused_with_a_path(bad: Any) -> None:
    err = refused(bad)
    assert err.code == x.DECLARATION and err.path and err.path.startswith("expr")


def test_a_refusal_names_no_hostile_key() -> None:
    err = refused({"fn": "add", "args": [n("1"), n("1")], "SECRET-KEY!": 1})
    assert "SECRET" not in err.message and "`?`" in err.message


@pytest.mark.parametrize("keys,field", [
    ({"rounding": "round"}, "rounding"), ({"rounding": 5}, "rounding"),
    ({"places": 9}, "places"), ({"places": -1}, "places"), ({"places": True}, "places"),
    ({"places": 1.0}, "places"),
])
def test_a_bad_rounding_key(keys: dict[str, Any], field: str) -> None:
    err = refused(fn("round", n("1"), **keys))
    assert err.path == f"expr.{field}"


# -- the type check -------------------------------------------------------------


@pytest.mark.parametrize("expr,kind", [
    (fn("add", ref("qty"), n("1")), "number"),
    (fn("add", ref("rate"), money(5)), "money"),
    (fn("mul", ref("rate"), ref("qty"), rounding="half_up"), "money"),
    (fn("mul", ref("qty"), n("2")), "number"),
    (fn("div", ref("rate"), ref("rate"), rounding="down", places=2), "number"),
    (fn("div", ref("rate"), n("3"), rounding="down"), "money"),
    (fn("round", ref("qty"), rounding="up", places=1), "number"),
    (fn("concat", ref("label"), {"text": "x"}), "text"),
    (fn("len", ref("label")), "number"),
    (fn("add_days", ref("day"), n("5")), "date"),
    (fn("days_between", ref("day"), {"date": "2026-01-01"}), "number"),
    (fn("eq", ref("state"), {"text": "open"}), "bool"),
    (fn("is_empty", ref("when")), "bool"),
    (fn("is_empty", ref("sizes")), "bool"),
    (fn("coalesce", ref("qty"), n("0")), "number"),
    (fn("if", ref("ok"), ref("label"), {"text": "none"}), "text"),
    (fn("lt", ref("day"), {"date": "2026-01-01"}), "bool"),
])
def test_the_type_of_a_tree(expr: Any, kind: str) -> None:
    assert typed(expr) == kind


@pytest.mark.parametrize("expr,where", [
    (ref("missing"), "expr"),
    (fn("add", ref("qty"), ref("rate")), "expr"),
    (fn("add", ref("qty"), ref("label")), "expr"),
    (fn("mul", ref("rate"), ref("rate"), rounding="half_up"), "expr"),
    (fn("div", n("1"), ref("rate"), rounding="half_up", places=0), "expr"),
    (fn("div", ref("qty"), n("2")), "expr"),
    (fn("div", ref("qty"), n("2"), rounding="up"), "expr"),
    (fn("add", ref("qty"), n("1"), rounding="up"), "expr.rounding"),
    (fn("mul", ref("rate"), n("2"), rounding="up", places=2), "expr.places"),
    (fn("div", ref("rate"), n("2"), rounding="up", places=2), "expr.places"),
    (fn("concat", ref("qty"), n("1")), "expr"),
    (fn("lt", ref("label"), {"text": "a"}), "expr"),
    (fn("lt", ref("qty"), ref("rate")), "expr"),
    (fn("eq", ref("qty"), ref("label")), "expr"),
    (fn("and", ref("ok"), ref("qty")), "expr"),
    (fn("if", ref("qty"), n("1"), n("2")), "expr"),
    (fn("if", ref("ok"), n("1"), {"text": "a"}), "expr"),
    (fn("coalesce", ref("qty"), ref("label")), "expr"),
    (fn("add_days", ref("qty"), n("1")), "expr"),
    (fn("days_between", ref("day"), ref("qty")), "expr"),
    (fn("concat", ref("state"), {"text": "x"}), "expr.args[0]"),
    (fn("add", ref("when"), n("1")), "expr.args[0]"),
    (fn("eq", ref("sizes"), ref("sizes")), "expr"),
    (fn("len", fn("neg", ref("label"))), "expr"),
    (ref("state"), "expr"),
])
def test_a_type_error_gets_a_path_and_a_sentence(expr: Any, where: str) -> None:
    err = type_refused(expr)
    assert err.code == x.DECLARATION and err.path is not None and err.path.startswith(where)
    assert len(err.message.split()) <= 25


def test_an_own_target_ref_is_refused() -> None:
    err = type_refused(fn("add", ref("qty"), n("1")), target="qty")
    assert err.path == "expr.args[0]" and "own target" in err.message


def test_an_opaque_field_is_read_only_under_eq_ne_and_is_empty() -> None:
    assert typed(fn("ne", ref("state"), {"text": "x"})) == "bool"
    assert "read only under" in type_refused(fn("not", ref("state"))).message


# -- the run: rounding ------------------------------------------------------------


@pytest.mark.parametrize("mode,value,expected", [
    ("half_up", "2.5", 3), ("half_up", "-2.5", -3), ("half_up", "3.5", 4),
    ("half_even", "2.5", 2), ("half_even", "-2.5", -2), ("half_even", "3.5", 4),
    ("half_even", "-3.5", -4),
    ("down", "2.5", 2), ("down", "-2.5", -2), ("down", "2.9", 2), ("down", "-2.9", -2),
    ("up", "2.5", 3), ("up", "-2.5", -3), ("up", "2.1", 3), ("up", "-2.1", -3),
    ("half_up", "2.4", 2), ("half_up", "2.6", 3), ("half_even", "2.51", 3),
    ("half_up", "-2.4", -2), ("up", "2", 2), ("down", "-2", -2),
])
def test_a_tie_rounds_as_the_named_mode(mode: str, value: str, expected: int) -> None:
    out = ev(fn("round", n(value), rounding=mode, places=0))
    assert out == D(expected) and isinstance(out, D)


def test_round_to_places() -> None:
    assert ev(fn("round", n("1.005"), rounding="half_up", places=2)) == D("1.01")
    assert ev(fn("round", n("1.005"), rounding="half_even", places=2)) == D("1.00")
    assert ev(fn("round", n("-1.005"), rounding="up", places=2)) == D("-1.01")
    assert ev(fn("round", n("1.239"), rounding="down", places=2)) == D("1.23")


@pytest.mark.parametrize("a,b,places,mode,expected", [
    ("1", "3", 4, "half_up", "0.3333"), ("2", "3", 4, "half_up", "0.6667"),
    ("2", "3", 4, "down", "0.6666"), ("2", "3", 4, "up", "0.6667"),
    ("10", "4", 0, "half_up", "3"), ("10", "4", 0, "half_even", "2"), ("10", "4", 0, "down", "2"),
    ("-10", "4", 0, "half_up", "-3"), ("10", "-4", 0, "up", "-3"), ("-10", "-4", 0, "down", "2"),
    ("1", "8", 2, "half_even", "0.12"), ("3", "8", 2, "half_even", "0.38"),
    ("0", "7", 3, "up", "0.000"),
])
def test_div_rounds_at_its_node(a: str, b: str, places: int, mode: str, expected: str) -> None:
    out = ev(fn("div", n(a), n(b), rounding=mode, places=places))
    assert out == D(expected) and out.as_tuple().exponent == -places


def test_div_gives_an_exact_decimal_never_a_float() -> None:
    out = ev(fn("div", n("0.1"), n("0.3"), rounding="half_up", places=8))
    assert isinstance(out, D) and out == D("0.33333333")
    # A tie that a binary float would miss: 0.285 / 1 at 2 places.
    assert ev(fn("round", n("0.285"), rounding="half_up", places=2)) == D("0.29")


def test_rounding_happens_only_at_the_named_node() -> None:
    # (1/3) * 3 at places 6 would give 0.999999. Exact intermediates give 1 at the end.
    third = fn("div", n("1"), n("3"), rounding="down", places=6)
    assert ev(fn("mul", third, n("3"))) == D("0.999999")
    exact = fn("mul", fn("mul", n("0.333333"), n("0.333333")), n("3"))
    assert ev(exact) == D("0.333332666667")


# -- the run: division by zero ------------------------------------------------------


def test_division_by_zero_is_a_named_error() -> None:
    assert run_code(fn("div", n("1"), n("0"), rounding="up", places=0)) == x.DIVISION_BY_ZERO
    assert run_code(fn("div", n("1"), ref("qty"), rounding="up", places=0),
                    {"qty": 0}) == x.DIVISION_BY_ZERO
    assert run_code(fn("div", money(5), n("0.000"), rounding="up")) == x.DIVISION_BY_ZERO
    assert run_code(fn("div", money(5), money(0), rounding="up", places=2)) == x.DIVISION_BY_ZERO


def test_if_is_lazy() -> None:
    guarded = fn("if", fn("eq", ref("qty"), n("0")), n("0"),
                 fn("div", n("10"), ref("qty"), rounding="half_up", places=2))
    assert ev(guarded, {"qty": 0}) == D(0)
    assert ev(guarded, {"qty": 4}) == D("2.50")


def test_if_runs_the_chosen_branch_only_and_counts_it() -> None:
    expr = x.parse_expr(fn("if", {"bool": True}, n("1"), fn("add", n("1"), n("1"))))
    # condition + chosen branch + the if node = 3 steps. The other branch (3 nodes) never runs.
    assert x.evaluate(expr, {}, max_steps=3) == D(1)
    with pytest.raises(ExprError) as info:
        x.evaluate(expr, {}, max_steps=2)
    assert info.value.code == x.STEP_CAP


def test_an_eager_call_still_refuses_a_division_by_zero_in_a_later_arg() -> None:
    expr = fn("coalesce", n("1"), fn("div", n("1"), n("0"), rounding="up", places=0))
    assert run_code(expr) == x.DIVISION_BY_ZERO


# -- the run: money -----------------------------------------------------------------


def test_money_add_sub_neg_abs_min_max() -> None:
    assert ev(fn("add", money(100), money(250))) == Money(D(350), "INR")
    assert ev(fn("sub", money(100), money(250))) == Money(D(-150), "INR")
    assert ev(fn("neg", money(100))) == Money(D(-100), "INR")
    assert ev(fn("abs", money(-100))) == Money(D(100), "INR")
    assert ev(fn("min", money(100), money(-5), money(7))) == Money(D(-5), "INR")
    assert ev(fn("max", money(100), money(-5), money(7))) == Money(D(100), "INR")


@pytest.mark.parametrize("name", ["add", "sub", "min", "max", "lt", "eq", "ne"])
def test_two_currencies_never_meet(name: str) -> None:
    assert run_code(fn(name, money(1, "INR"), money(1, "USD"))) == x.CURRENCY_MISMATCH


def test_money_mul_rounds_once_at_its_node() -> None:
    # 101 minor units * 2.5 = 252.5 -> 253 (half_up), 252 (half_even), 252 (down), 253 (up).
    for mode, expected in (("half_up", 253), ("half_even", 252), ("down", 252), ("up", 253)):
        out = ev(fn("mul", money(101), n("2.5"), rounding=mode))
        assert out == Money(D(expected), "INR")
    # Two factors, one rounding: 1000 * 0.155 * 0.5 = 77.5, not (155 -> 78) * 0.5.
    assert ev(fn("mul", money(1000), n("0.155"), n("0.5"), rounding="half_even")) == Money(
        D(78), "INR")
    assert ev(fn("mul", n("2"), money(5), rounding="up")) == Money(D(10), "INR")


def test_money_div() -> None:
    assert ev(fn("div", money(1000), n("3"), rounding="half_up")) == Money(D(333), "INR")
    assert ev(fn("div", money(-1000), n("3"), rounding="up")) == Money(D(-334), "INR")
    assert ev(fn("div", money(1000), money(300), rounding="half_up", places=3)) == D("3.333")


def test_money_from_a_record() -> None:
    record = {"rate": {"minor": 1999, "currency": "INR"}, "qty": 3}
    expr = fn("mul", ref("rate"), ref("qty"), rounding="half_up")
    assert ev(expr, record) == Money(D(5997), "INR")


# -- the run: null, text, date, logic -----------------------------------------------


@pytest.mark.parametrize("expr", [
    fn("add", ref("qty"), n("1")), fn("mul", ref("qty"), n("2")),
    fn("div", ref("qty"), n("2"), rounding="up", places=0), fn("neg", ref("qty")),
    fn("round", ref("qty"), rounding="up", places=0), fn("concat", ref("label"), {"text": "a"}),
    fn("len", ref("label")), fn("upper", ref("label")), fn("eq", ref("qty"), n("1")),
    fn("lt", ref("qty"), n("1")), fn("and", ref("ok"), {"bool": True}), fn("not", ref("ok")),
    fn("add_days", ref("day"), n("1")), fn("days_between", ref("day"), {"date": "2026-01-01"}),
])
def test_a_null_gives_null(expr: Any) -> None:
    assert ev(expr, {}) is None


def test_a_blank_value_is_null() -> None:
    assert ev(ref("label"), {"label": "   "}) is None
    assert ev(ref("qty"), {"qty": None}) is None


def test_is_empty_coalesce_and_the_if_condition_take_null() -> None:
    assert ev(fn("is_empty", ref("qty")), {}) is True
    assert ev(fn("is_empty", ref("qty")), {"qty": 0}) is False
    assert ev(fn("is_empty", {"text": " "})) is True
    assert ev(fn("coalesce", ref("qty"), ref("qty"), n("7")), {}) == D(7)
    assert ev(fn("coalesce", ref("qty"), n("7")), {"qty": 3}) == D(3)
    assert ev(fn("coalesce", ref("qty"), ref("rate")), {}) is None
    assert ev(fn("if", ref("ok"), n("1"), n("2")), {}) == D(2)
    assert ev(fn("if", fn("eq", ref("qty"), n("1")), n("1"), n("2")), {}) == D(2)


def test_text_functions() -> None:
    assert ev(fn("concat", {"text": "ab"}, {"text": "cd"}, {"text": "e"})) == "abcde"
    assert ev(fn("lower", {"text": "AbC"})) == "abc"
    assert ev(fn("upper", {"text": "AbC"})) == "ABC"
    assert ev(fn("trim", {"text": "  a b  "})) == "a b"
    assert ev(fn("len", {"text": "héllo"})) == D(5)


def test_a_long_text_result_is_too_large() -> None:
    big = {"label": "x" * 20_000}
    assert ev(fn("lower", ref("label")), big) == "x" * 20_000
    assert run_code(fn("concat", ref("label"), {"text": "y"}), big) == x.RESULT_TOO_LARGE


def test_date_functions() -> None:
    assert ev(fn("add_days", {"date": "2026-01-31"}, n("1"))) == "2026-02-01"
    assert ev(fn("add_days", {"date": "2026-03-01"}, n("-1"))) == "2026-02-28"
    assert ev(fn("days_between", {"date": "2026-01-01"}, {"date": "2026-01-31"})) == D(30)
    assert ev(fn("days_between", {"date": "2026-01-31"}, {"date": "2026-01-01"})) == D(-30)
    assert run_code(fn("add_days", {"date": "2026-01-31"}, n("1.5"))) == x.BAD_ARGUMENT
    assert run_code(fn("add_days", {"date": "9999-12-31"}, n("1"))) == x.RESULT_TOO_LARGE
    assert run_code(fn("add_days", {"date": "2026-01-31"}, n("99999999999"))) == (
        x.RESULT_TOO_LARGE)


def test_logic_and_comparison() -> None:
    t, f = {"bool": True}, {"bool": False}
    assert ev(fn("and", t, f)) is False and ev(fn("or", t, f)) is True
    assert ev(fn("not", f)) is True
    assert ev(fn("lt", n("1"), n("2"))) is True and ev(fn("gte", n("1"), n("2"))) is False
    assert ev(fn("lte", {"date": "2026-01-01"}, {"date": "2026-01-01"})) is True
    assert ev(fn("gt", money(5), money(4))) is True
    assert ev(fn("eq", n("1.0"), n("1.00"))) is True and ev(fn("ne", n("1"), n("2"))) is True
    assert ev(fn("eq", {"text": "a"}, {"text": "a"})) is True
    assert ev(fn("eq", ref("state"), {"text": "open"}), {"state": "open"}) is True
    assert ev(fn("ne", ref("state"), {"text": "open"}), {"state": "done"}) is True


# -- the caps of a run ----------------------------------------------------------------


def test_a_parsed_tree_never_passes_64_steps() -> None:
    leaf4 = fn("add", n("1"), n("1"), n("1"), n("1"))
    mid = fn("add", leaf4, leaf4, leaf4, leaf4)
    top = fn("add", mid, mid, fn("add", n("1"), n("1"), n("1")), n("1"))
    assert x.evaluate(x.parse_expr(top), {}, max_steps=64) == D(36)


def test_the_step_cap_stops_a_forged_tree() -> None:
    """A tree built in code can share a subtree, so it runs more nodes than it holds."""
    node = Node("num", "1")
    for _ in range(4):
        node = Node("add", None, (node, node, node, node))
    # 4 levels of 4 shared args = 1 + 4 + 16 + 64 + 256 = 341 steps.
    assert run_forged(node).code == x.STEP_CAP
    assert x.evaluate(node, {}, max_steps=341) == D(256)


def run_forged(node: Node, record: dict[str, Any] | None = None) -> ExprError:
    with pytest.raises(ExprError) as info:
        x.evaluate(node, record or {})
    return info.value


@pytest.mark.parametrize("node,code", [
    (Node("add", None, (Node("text", "a"), Node("num", "1"))), x.TYPE_ERROR),
    (Node("nope", None, (Node("num", "1"),)), x.TYPE_ERROR),
    (Node("div", None, (Node("num", "1"), Node("num", "2"))), x.TYPE_ERROR),
    (Node("if", None, (Node("num", "1"), Node("num", "1"), Node("num", "2"))), x.TYPE_ERROR),
    (Node("if", None, (Node("bool", True),)), x.TYPE_ERROR),
    (Node("and", None, (Node("num", "1"), Node("num", "1"))), x.TYPE_ERROR),
    (Node("add_days", None, (Node("text", "nope"), Node("num", "1"))), x.TYPE_ERROR),
    (Node("mul", None, (Node("num", "1e999999"), Node("num", "1e999999"))), x.RESULT_TOO_LARGE),
    (Node("add", None, (Node("money", (1, "INR")), Node("num", "1"))), x.TYPE_ERROR),
])
def test_a_forged_tree_ends_in_a_named_error(node: Node, code: str) -> None:
    assert run_forged(node).code == code


def test_a_product_that_loses_a_digit_is_too_large() -> None:
    big = "999999999999999.999999"
    assert run_code(fn("mul", n(big), n(big), n(big), n(big))) == x.RESULT_TOO_LARGE
    assert ev(fn("mul", n(big), n(big))) == decimal.Context(prec=100).multiply(D(big), D(big))


def test_the_run_never_reads_the_global_decimal_context() -> None:
    with decimal.localcontext(decimal.Context(prec=3, traps=[])):
        out = ev(fn("add", n("123456789.123456"), n("1")))
        assert out == D("123456790.123456")
        assert x.number_json(D("12345678.1234")) == 12345678.1234
    assert decimal.getcontext().prec == 28


def test_an_error_never_echoes_a_value() -> None:
    secret = "s3cret-value-77"
    for expr, record in (
        (fn("add", ref("label"), n("1")), {"label": secret}),
        (fn("div", n("1"), ref("qty"), rounding="up", places=0), {"qty": 0, "label": secret}),
        (fn("add_days", ref("label"), n("1")), {"label": secret}),
    ):
        with pytest.raises(ExprError) as info:
            ev(expr, record)
        assert secret not in str(info.value) and secret not in info.value.message


# -- the result of a formula -----------------------------------------------------------

NUM = sf.FieldSpec(key="t", type="number")
MON = sf.FieldSpec(key="t", type="money")
TXT = sf.FieldSpec(key="t", type="text")
CHK = sf.FieldSpec(key="t", type="checkbox")
DAY = sf.FieldSpec(key="t", type="date")


@pytest.mark.parametrize("value,scale,mode,expected", [
    ("2.5", 0, "half_up", 3), ("-2.5", 0, "half_up", -3), ("2.5", 0, "half_even", 2),
    ("2.5", 0, "down", 2), ("2.5", 0, "up", 3), ("1.005", 2, "half_up", 1.01),
    ("1.005", 2, "half_even", 1.0), ("0.00005", 4, "half_up", 0.0001),
    ("-0.00004", 4, "half_up", 0), ("12.34567", 3, "down", 12.345), ("7", 4, "up", 7),
])
def test_a_number_target_rounds_to_its_scale(value: str, scale: int, mode: str,
                                            expected: Any) -> None:
    out = x.store_result(D(value), NUM, scale=scale, rounding=mode)
    assert out == expected and (isinstance(out, int) == (expected == int(expected)))


@pytest.mark.parametrize("scale", [0, 1, 2, 3, 4])
def test_a_number_target_holds_15_significant_digits(scale: int) -> None:
    limit = 10 ** (15 - scale)
    top = D(limit - 1)
    if scale:
        top = top + D(10**scale - 1) / D(10**scale)
    stored = x.store_result(top, NUM, scale=scale, rounding="down")
    assert abs(stored) < limit and sf.check_value(NUM, stored) == stored
    assert json.loads(json.dumps(stored)) == stored
    with pytest.raises(ExprError) as info:
        x.store_result(D(limit), NUM, scale=scale, rounding="down")
    assert info.value.code == x.RESULT_TOO_LARGE
    with pytest.raises(ExprError) as neg:
        x.store_result(D(-limit), NUM, scale=scale, rounding="down")
    assert neg.value.code == x.RESULT_TOO_LARGE


def test_rounding_up_to_the_limit_is_too_large() -> None:
    with pytest.raises(ExprError) as info:
        x.store_result(D("999999999999999.5"), NUM, scale=0, rounding="half_up")
    assert info.value.code == x.RESULT_TOO_LARGE
    assert x.store_result(D("999999999999999"), NUM, scale=0, rounding="half_up") == (
        999_999_999_999_999)


def test_a_stored_number_has_no_exponent() -> None:
    assert x.store_result(D("0.0001"), NUM, scale=4, rounding="down") == 0.0001
    assert "e" not in repr(x.store_result(D("0.0001"), NUM, scale=4, rounding="down"))
    assert sf._plain_number(x.store_result(D("123.4567"), NUM, scale=4, rounding="down"))


def test_a_number_target_needs_a_named_rounding() -> None:
    for mode in (None, "round"):
        with pytest.raises(ExprError) as info:
            x.store_result(D(1), NUM, scale=0, rounding=mode)
        assert info.value.code == x.TYPE_ERROR


def test_money_target() -> None:
    assert x.store_result(Money(D(500), "INR"), MON, currency="INR") == {
        "minor": 500, "currency": "INR"}
    with pytest.raises(ExprError) as info:
        x.store_result(Money(D(500), "USD"), MON, currency="INR")
    assert info.value.code == x.CURRENCY_MISMATCH
    for minor in (10**15, -(10**15)):
        with pytest.raises(ExprError) as big:
            x.store_result(Money(D(minor), "INR"), MON, currency="INR")
        assert big.value.code == x.RESULT_TOO_LARGE
    assert x.store_result(Money(D(10**15 - 1), "INR"), MON, currency="INR")["minor"] == 10**15 - 1
    with pytest.raises(ExprError) as kind:
        x.store_result(D(5), MON, currency="INR")
    assert kind.value.code == x.TYPE_ERROR


def test_text_checkbox_and_date_targets() -> None:
    assert x.store_result("abc", TXT) == "abc"
    assert x.store_result("   ", TXT) is None
    assert x.store_result(None, TXT) is None
    assert x.store_result(True, CHK) is True
    assert x.store_result("2026-05-01", DAY) == "2026-05-01"
    with pytest.raises(ExprError) as info:
        x.store_result("x" * 2001, TXT)
    assert info.value.code == x.RESULT_TOO_LARGE
    with pytest.raises(ExprError) as bad:
        x.store_result("not a date", DAY)
    assert bad.value.code == x.BAD_RESULT
    with pytest.raises(ExprError) as kind:
        x.store_result(1, TXT)
    assert kind.value.code == x.TYPE_ERROR


def test_number_json() -> None:
    assert x.number_json(D("5.00")) == 5 and isinstance(x.number_json(D("5.00")), int)
    assert x.number_json(D("-0")) == 0
    assert x.number_json(D("12.34")) == 12.34
    assert x.number_json(D("1E+3")) == 1000


# -- the reads -----------------------------------------------------------------------


def test_expr_reads_every_ref_once() -> None:
    node = x.parse_expr(fn("add", ref("a"), fn("mul", ref("b"), ref("a")), n("1")))
    assert x.expr_reads(node) == frozenset({"a", "b"})
    assert x.expr_reads(x.parse_expr(n("1"))) == frozenset()


def test_expr_reads_is_iterative() -> None:
    node = Node("ref", "k")
    for _ in range(5000):
        node = Node("neg", None, (node,))
    assert x.expr_reads(node) == frozenset({"k"})


# -- the fence (R7): the syntax tree of the module --------------------------------------

_SOURCE = _FILE.read_text(encoding="utf-8")
_TREE = ast.parse(_SOURCE)
_ALLOWED_IMPORTS = {
    "__future__", "json", "collections.abc", "dataclasses", "datetime", "decimal", "typing",
    "acb_common.studio_fields",
}


def _imports() -> set[str]:
    out: set[str] = set()
    for node in ast.walk(_TREE):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            out.add(node.module or "")
    return out


def test_expr_module_imports_only_stdlib_and_fields() -> None:
    assert _imports() <= _ALLOWED_IMPORTS, _imports() - _ALLOWED_IMPORTS


_BANNED_NAMES = {
    "eval", "exec", "compile", "float", "__import__", "open", "input", "globals", "locals",
    "getattr", "setattr", "vars", "getcontext", "setcontext", "localcontext", "re", "random",
    "time", "sqlalchemy", "text", "now", "today", "utcnow",
}
_BANNED_ATTRS = {
    "now", "today", "utcnow", "time", "monotonic", "perf_counter", "fromtimestamp", "utcfromtimestamp",
    "getcontext", "setcontext", "localcontext", "execute", "compile", "match", "fullmatch",
    "search", "sub", "findall", "__float__",
}


def test_expr_module_has_no_eval_no_clock_no_float_no_sql() -> None:
    names = {n.id for n in ast.walk(_TREE) if isinstance(n, ast.Name)}
    args = {a.arg for a in ast.walk(_TREE) if isinstance(a, ast.arg)}
    attrs = {a.attr for a in ast.walk(_TREE) if isinstance(a, ast.Attribute)}
    assert not (names | args) & (_BANNED_NAMES - {"text"}), (names | args) & _BANNED_NAMES
    assert not attrs & _BANNED_ATTRS, attrs & _BANNED_ATTRS
    assert "execute(" not in _SOURCE and "sqlalchemy" not in _imports()


def test_every_context_is_local() -> None:
    contexts = [n for n in ast.walk(_TREE)
                if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "Context"]
    assert len(contexts) == 1
    assert {kw.arg for kw in contexts[0].keywords} == {"prec", "traps"}


def test_the_caps_are_the_documented_numbers() -> None:
    assert (x.MAX_DEPTH, x.MAX_NODES, x.MAX_ARGS, x.MAX_STEPS) == (8, 64, 4, 256)
    assert x.ROUNDINGS == ("half_up", "half_even", "down", "up")
    assert x.MAX_SCALE == 4 and x.MAX_PLACES == 8 and x.SIGNIFICANT_DIGITS == 15
    assert x.MAX_LITERAL_TEXT == 200 and x.MAX_LITERAL_PLACES == 6


def test_the_function_list_is_closed_and_each_has_a_family_or_a_special_case() -> None:
    special = {"is_empty", "coalesce", "if"}
    assert set(x.FUNCTIONS) - special == set(x._FAMILIES)
    assert len(x.FUNCTIONS) == len(set(x.FUNCTIONS)) == 28
