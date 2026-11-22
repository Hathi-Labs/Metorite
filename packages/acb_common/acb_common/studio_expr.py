"""The closed expression tree of a formula field (WS-STU C7a).

Spec: ``project-docs/specs/ai_studio.md`` section 3.7 (layer 2) and the section 14 C7a row.

**Pure.** This module imports the standard library, ``decimal`` and
``acb_common.studio_fields`` only. It holds no SQL, no gateway import, no clock,
no regex and no float. It never calls ``eval``, ``exec`` or ``compile``.
``tests/unit/test_studio_expr.py`` scans its syntax tree for each of these.
``studio_rules.py`` holds the rule kind ``formula`` and the store calls it.

**The tree.** A node is one JSON object. There is no text syntax.

* ``{"ref": "field_key"}``: a field of the same record, by its bare key. A dotted
  key does not parse.
* A literal: ``{"num": "0.12"}`` (decimal text, at most 6 places, never a JSON
  number), ``{"money": {"minor": 500, "currency": "INR"}}``, ``{"text": "..."}``
  (at most 200 characters), ``{"bool": true}`` and ``{"date": "2026-01-31"}``.
* A call: ``{"fn": "name", "args": [...], "rounding": "half_up", "places": 2}``.
  ``rounding`` and ``places`` are optional keys. A node takes them only where
  :func:`check_expr` says that it needs them.

**The caps.** Depth :data:`MAX_DEPTH`, :data:`MAX_NODES` nodes and
:data:`MAX_ARGS` args for each call. :func:`parse_expr` refuses a tree over a
cap, with the JSON path. :func:`evaluate` counts one step for each node that it
runs, and it stops at :data:`MAX_STEPS`. A parsed tree cannot pass 64 steps,
so the step cap is a second line, for a tree that skips the parser.

**Money and numbers.** A number is a ``Decimal``. Money is a whole count of
minor units and an ISO currency. Each operation is exact: a local
``decimal.Context`` of 64 digits that traps ``Inexact``, so a result that would
lose a digit is ``result_too_large``. The context is never the global one.
Rounding happens at a node that names it, and at the target. It runs on integers
in :func:`_round_ratio`, the one place of rounding, with the four modes of
:data:`ROUNDINGS`. A tie rounds away from zero under ``half_up``, to the even
digit under ``half_even``, toward zero under ``down`` and away from zero under
``up``. Every error of ``decimal`` maps to a named :class:`ExprError`.

**Null.** A ref to an empty field is null. A function over a null gives null,
except ``is_empty``, ``coalesce`` and ``if``. All args of a call run, so a
division by zero in a later arg still refuses. ``if`` is lazy: only the chosen
branch runs. A null condition counts as false. A null result clears the target.

**An error never echoes a value**, because a record holds member data. It
carries a code and one fixed sentence.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import (
    Context,
    Decimal,
    DivisionByZero,
    Inexact,
    InvalidOperation,
    Overflow,
)
from typing import Any

from acb_common.studio_fields import (
    MAX_ABS_NUMBER,
    MAX_LONG_TEXT_CHARS,
    MAX_TEXT_CHARS,
    FieldError,
    FieldSpec,
    check_value,
    is_blank,
    storable_text,
)

# --- the caps ---------------------------------------------------------------

MAX_DEPTH = 8
MAX_NODES = 64
MAX_ARGS = 4
MAX_STEPS = 256
#: A ``text`` literal.
MAX_LITERAL_TEXT = 200
#: The places of a ``num`` literal.
MAX_LITERAL_PLACES = 6
#: The ``places`` of a rounding node.
MAX_PLACES = 8
#: The scale of a number target.
MAX_SCALE = 4
#: A stored number holds at most this many significant digits at its scale, so
#: a JSON number holds it exactly. ``abs(result) < 10 ** (SIGNIFICANT_DIGITS - scale)``.
SIGNIFICANT_DIGITS = 15

ROUNDINGS: tuple[str, ...] = ("half_up", "half_even", "down", "up")

#: The closed list of functions, with the least and the most args of each.
ARITY: Mapping[str, tuple[int, int]] = {
    "add": (2, 4), "sub": (2, 2), "mul": (2, 4), "div": (2, 2), "neg": (1, 1),
    "abs": (1, 1), "min": (2, 4), "max": (2, 4), "round": (1, 1),
    "concat": (2, 4), "lower": (1, 1), "upper": (1, 1), "trim": (1, 1), "len": (1, 1),
    "add_days": (2, 2), "days_between": (2, 2),
    "eq": (2, 2), "ne": (2, 2), "lt": (2, 2), "lte": (2, 2), "gt": (2, 2), "gte": (2, 2),
    "and": (2, 4), "or": (2, 4), "not": (1, 1), "is_empty": (1, 1),
    "coalesce": (2, 4), "if": (3, 3),
}  # fmt: skip
FUNCTIONS: tuple[str, ...] = tuple(ARITY)

#: The node forms that hold one value.
_LITERALS = ("num", "money", "text", "bool", "date")
_KEYS_OF_FN = frozenset({"fn", "args", "rounding", "places"})
_SINGLE = ("ref", *_LITERALS)

# The codes of an error. A refusal at declaration has the code ``declaration``.
DECLARATION = "declaration"
DIVISION_BY_ZERO = "division_by_zero"
CURRENCY_MISMATCH = "currency_mismatch"
RESULT_TOO_LARGE = "result_too_large"
STEP_CAP = "step_cap"
TYPE_ERROR = "type_error"
BAD_ARGUMENT = "bad_argument"
BAD_RESULT = "bad_result"

#: One fixed sentence for each code of a run. It names no value.
RUN_MESSAGES: Mapping[str, str] = {
    DIVISION_BY_ZERO: "A divisor is zero. Guard the division with `if`.",
    CURRENCY_MISMATCH: "Two money values have different currencies.",
    RESULT_TOO_LARGE: "A result is too large, or holds too many digits.",
    STEP_CAP: f"The formula ran more than {MAX_STEPS} steps.",
    TYPE_ERROR: "A value has the wrong type for its function.",
    BAD_ARGUMENT: "An argument is not a value that its function takes.",
    BAD_RESULT: "The result is not a valid value of the target field.",
}

_KEY_START = "abcdefghijklmnopqrstuvwxyz"
_KEY_CHARS = _KEY_START + "0123456789_"
_DIGITS = "0123456789"

#: Every operation runs in this context, never in the global one. ``Inexact``
#: traps, so a result that loses a digit refuses.
_CTX = Context(
    prec=64, traps=[InvalidOperation, DivisionByZero, Overflow, Inexact])
_ONE = Decimal(1)


class ExprError(ValueError):
    """One refused tree or run: a ``code``, the JSON ``path`` (a declaration
    only) and one sentence. It never echoes a value."""

    def __init__(self, code: str, message: str, path: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.path = path


def _declare(path: str, message: str) -> ExprError:
    return ExprError(DECLARATION, message, path)


def _run_error(code: str) -> ExprError:
    return ExprError(code, RUN_MESSAGES[code])


@dataclass(frozen=True)
class Node:
    """One node of a tree, after :func:`parse_expr`. It is hashable.

    ``op`` is ``ref``, a literal kind or a function name. ``value`` is the key of
    a ref or the payload of a literal: decimal text, ``(minor, currency)``,
    text, a bool or a date text. ``args`` are the args of a call.
    """

    op: str
    value: Any = None
    args: tuple[Node, ...] = ()
    rounding: str | None = None
    places: int | None = None


@dataclass(frozen=True)
class Money:
    """A money value at run time: a whole ``minor`` count and a currency."""

    minor: Decimal
    currency: str


# --- the parser -------------------------------------------------------------


def _is_key(value: Any) -> bool:
    """The shape of a field key: ``studio_fields.NAME_PATTERN``, with no regex."""
    return (isinstance(value, str) and 1 <= len(value) <= 48 and value[0] in _KEY_START
            and all(c in _KEY_CHARS for c in value))


def _safe(key: Any) -> str:
    """A key to name in a refusal: the key when it has the key shape, else ``?``."""
    return key if _is_key(key) else "?"


def _digits(part: str) -> bool:
    return bool(part) and all(c in _DIGITS for c in part)


def _num_text(path: str, value: Any) -> str:
    """The decimal text of a ``num`` literal. A JSON number is refused."""
    message = (f"A `num` is decimal text, as \"0.12\", with at most {MAX_LITERAL_PLACES} "
               "places and at most 15 digits before the point.")
    if not isinstance(value, str) or not 1 <= len(value) <= 24:
        raise _declare(path, message)
    body = value[1:] if value[0] == "-" else value
    whole, point, frac = body.partition(".")
    if (not _digits(whole) or len(whole) > 15
            or (point and (not _digits(frac) or len(frac) > MAX_LITERAL_PLACES))):
        raise _declare(path, message)
    return value


def _money_literal(path: str, value: Any) -> tuple[int, str]:
    try:
        checked = check_value(FieldSpec(key="money", type="money"), value)
    except FieldError:
        raise _declare(
            path, 'A `money` is {"minor": <whole number below 10^15>, "currency": "INR"}.'
        ) from None
    return int(checked["minor"]), str(checked["currency"])


def _text_literal(path: str, value: Any) -> str:
    if not isinstance(value, str) or len(value) > MAX_LITERAL_TEXT or not storable_text(value):
        raise _declare(path, f"A `text` is text of at most {MAX_LITERAL_TEXT} characters, with no NUL.")
    return value


def _date_literal(path: str, value: Any) -> str:
    try:
        return str(check_value(FieldSpec(key="date", type="date"), value))
    except FieldError:
        raise _declare(path, "A `date` is a real date as YYYY-MM-DD.") from None


def _literal(kind: str, path: str, value: Any) -> Node:
    if kind == "ref":
        if not _is_key(value):
            raise _declare(path, "A `ref` names one field of this record by its bare key.")
        return Node("ref", value)
    if kind == "num":
        return Node("num", _num_text(path, value))
    if kind == "money":
        return Node("money", _money_literal(path, value))
    if kind == "text":
        return Node("text", _text_literal(path, value))
    if kind == "bool":
        if type(value) is not bool:
            raise _declare(path, "A `bool` is true or false.")
        return Node("bool", value)
    return Node("date", _date_literal(path, value))


def _round_keys(path: str, raw: Mapping[str, Any]) -> tuple[str | None, int | None]:
    rounding, places = raw.get("rounding"), raw.get("places")
    if "rounding" in raw and (not isinstance(rounding, str) or rounding not in ROUNDINGS):
        raise _declare(f"{path}.rounding", f"`rounding` is {', '.join(ROUNDINGS)}.")
    if "places" in raw and (type(places) is not int or not 0 <= places <= MAX_PLACES):
        raise _declare(f"{path}.places", f"`places` is a whole number from 0 to {MAX_PLACES}.")
    return rounding, places


def _call(raw: Mapping[str, Any], path: str, depth: int, count: list[int]) -> Node:
    extra = sorted(str(k) for k in raw if k not in _KEYS_OF_FN)
    if extra:
        raise _declare(path, f"`{_safe(extra[0])}` is not a key of a call.")
    name = raw["fn"]
    if not isinstance(name, str) or name not in ARITY:
        raise _declare(f"{path}.fn", f"`fn` is one of: {', '.join(FUNCTIONS)}.")
    least, most = ARITY[name]
    args = raw.get("args")
    if not isinstance(args, list) or not least <= len(args) <= most:
        count_text = f"{least}" if least == most else f"{least} to {most}"
        raise _declare(f"{path}.args", f"Function `{name}` takes {count_text} args, as a list.")
    rounding, places = _round_keys(path, raw)
    kids = tuple(_parse(a, f"{path}.args[{i}]", depth + 1, count) for i, a in enumerate(args))
    return Node(name, None, kids, rounding, places)


def _parse(raw: Any, path: str, depth: int, count: list[int]) -> Node:
    if depth > MAX_DEPTH:
        raise _declare(path, f"The tree is deeper than {MAX_DEPTH} levels. Use fewer nested calls.")
    count[0] += 1
    if count[0] > MAX_NODES:
        raise _declare(path, f"The tree holds more than {MAX_NODES} nodes. Use fewer nodes.")
    if not isinstance(raw, Mapping) or not raw:
        raise _declare(path, 'A node is an object, as {"ref": "field"} or {"fn": "add", "args": [...]}.')
    if "fn" in raw:
        return _call(raw, path, depth, count)
    keys = sorted(str(k) for k in raw)
    if len(keys) != 1 or keys[0] not in _SINGLE:
        raise _declare(
            path, f"`{_safe(keys[0])}` does not start a node. A node has `fn` or one of "
            f"{', '.join(_SINGLE)}.")
    return _literal(keys[0], f"{path}.{keys[0]}", raw[keys[0]])


def parse_expr(raw: Any) -> Node:
    """The tree *raw* as a :class:`Node`. Raises :class:`ExprError` with the
    JSON path of the first fault, relative to the rule (``expr``, ``expr.args[1]``).

    This checks the shape and the caps. :func:`check_expr` checks the types
    against the fields of an object.
    """
    return _parse(raw, "expr", 1, [0])


def node_json(node: Node) -> dict[str, Any]:
    """The JSON of *node*, as the store keeps it. It reads back equal."""
    if node.op == "ref":
        return {"ref": node.value}
    if node.op == "money":
        minor, currency = node.value
        return {"money": {"minor": minor, "currency": currency}}
    if node.op in _LITERALS:
        return {node.op: node.value}
    out: dict[str, Any] = {"fn": node.op, "args": [node_json(a) for a in node.args]}
    if node.rounding is not None:
        out["rounding"] = node.rounding
    if node.places is not None:
        out["places"] = node.places
    return out


def expr_reads(node: Node) -> frozenset[str]:
    """Every field key that the tree reads. Iterative, so depth cannot overflow."""
    out: set[str] = set()
    stack = [node]
    while stack:
        here = stack.pop()
        if here.op == "ref":
            out.add(str(here.value))
        stack.extend(here.args)
    return frozenset(out)


# --- the type check ---------------------------------------------------------

_VALUE_TYPE: Mapping[str, str] = {
    "number": "number", "money": "money", "text": "text", "longText": "text",
    "email": "text", "checkbox": "bool", "date": "date",
}
#: A field of these types compares with a text value under ``eq`` and ``ne``.
_STRING_LIKE = frozenset({"select", "person", "url", "phone", "datetime"})
_ORDERED = ("number", "money", "date")


def field_type(field: FieldSpec) -> str:
    """The static type of a ref to *field*. A type that a formula reads only
    under ``eq``, ``ne`` and ``is_empty`` is ``o:<type>``, and a ``many`` field
    is ``o:many``."""
    if field.many:
        return "o:many"
    return _VALUE_TYPE.get(field.type) or f"o:{field.type}"


def _opaque(kind: str) -> bool:
    return kind.startswith("o:")


def _keys_of(node: Node, path: str, *, rounding: bool, places: str) -> None:
    """The rule of the ``rounding`` and ``places`` keys of one node. *places* is
    ``required``, ``zero`` (absent or 0) or ``none``."""
    if rounding and node.rounding is None:
        raise _declare(path, f"Call `{node.op}` needs `rounding`: {', '.join(ROUNDINGS)}.")
    if not rounding and node.rounding is not None:
        raise _declare(f"{path}.rounding", f"Call `{node.op}` rounds nothing, so it takes no `rounding`.")
    if places == "required" and node.places is None:
        raise _declare(path, f"Call `{node.op}` needs `places`, from 0 to {MAX_PLACES}.")
    if places == "none" and node.places is not None:
        raise _declare(f"{path}.places", f"Call `{node.op}` takes no `places`.")
    if places == "zero" and node.places not in (None, 0):
        raise _declare(f"{path}.places", f"Call `{node.op}` has a money result, so `places` is 0 or absent.")


def _bad_args(node: Node, path: str, want: str) -> ExprError:
    return _declare(path, f"Call `{node.op}` takes {want}.")


def _same_of(node: Node, path: str, ts: Sequence[str], allowed: Sequence[str], want: str) -> str:
    first = ts[0]
    if first not in allowed or any(t != first for t in ts):
        raise _bad_args(node, path, want)
    return first


def _type_arith(node: Node, path: str, ts: list[str]) -> str:
    fn = node.op
    if fn in ("add", "sub", "min", "max"):
        _keys_of(node, path, rounding=False, places="none")
        return _same_of(node, path, ts, ("number", "money"), "all numbers or all money")
    if fn in ("neg", "abs"):
        _keys_of(node, path, rounding=False, places="none")
        return _same_of(node, path, ts, ("number", "money"), "one number or one money value")
    if fn == "round":
        _keys_of(node, path, rounding=True, places="required")
        return _same_of(node, path, ts, ("number",), "one number")
    if fn == "mul":
        return _type_mul(node, path, ts)
    return _type_div(node, path, ts)


def _type_mul(node: Node, path: str, ts: list[str]) -> str:
    if all(t == "number" for t in ts):
        _keys_of(node, path, rounding=False, places="none")
        return "number"
    if ts.count("money") == 1 and all(t in ("money", "number") for t in ts):
        _keys_of(node, path, rounding=True, places="zero")
        return "money"
    raise _bad_args(node, path, "numbers, or one money value and numbers")


def _type_div(node: Node, path: str, ts: list[str]) -> str:
    if ts == ["money", "number"]:
        _keys_of(node, path, rounding=True, places="zero")
        return "money"
    if ts in (["number", "number"], ["money", "money"]):
        _keys_of(node, path, rounding=True, places="required")
        return "number"
    raise _bad_args(node, path, "two numbers, two money values, or money and a number")


def _type_text_date(node: Node, path: str, ts: list[str]) -> str:
    fn = node.op
    _keys_of(node, path, rounding=False, places="none")
    if fn == "concat":
        return _same_of(node, path, ts, ("text",), "text args")
    if fn in ("lower", "upper", "trim"):
        return _same_of(node, path, ts, ("text",), "one text")
    if fn == "len":
        _same_of(node, path, ts, ("text",), "one text")
        return "number"
    if fn == "add_days":
        if ts != ["date", "number"]:
            raise _bad_args(node, path, "a date and a number of days")
        return "date"
    if ts != ["date", "date"]:
        raise _bad_args(node, path, "two dates")
    return "number"


def _comparable(a: str, b: str) -> bool:
    if a == "o:many" or b == "o:many":
        return False
    if a == b:
        return True
    pair = {a, b}
    return "text" in pair and any(_opaque(t) and t[2:] in _STRING_LIKE for t in pair)


def _type_logic(node: Node, path: str, ts: list[str]) -> str:
    fn = node.op
    _keys_of(node, path, rounding=False, places="none")
    if fn in ("eq", "ne"):
        if not _comparable(ts[0], ts[1]):
            raise _bad_args(node, path, "two values of one type")
        return "bool"
    if fn in ("lt", "lte", "gt", "gte"):
        _same_of(node, path, ts, _ORDERED, "two numbers, two money values or two dates")
        return "bool"
    if fn in ("and", "or", "not"):
        _same_of(node, path, ts, ("bool",), "true or false args")
        return "bool"
    if fn == "is_empty":
        return "bool"
    if fn == "coalesce":
        return _same_of(node, path, ts, ("number", "money", "text", "bool", "date"),
                        "args of one type")
    if ts[0] != "bool" or ts[1] != ts[2] or _opaque(ts[1]):
        raise _bad_args(node, path, "a condition and two results of one type")
    return ts[1]


_ARITH = frozenset({"add", "sub", "mul", "div", "neg", "abs", "min", "max", "round"})
_TEXT_DATE = frozenset({"concat", "lower", "upper", "trim", "len", "add_days", "days_between"})
_OPAQUE_OK = frozenset({"eq", "ne", "is_empty"})


def _type_call(node: Node, path: str, ts: list[str]) -> str:
    if node.op not in _OPAQUE_OK:
        for i, kind in enumerate(ts):
            if _opaque(kind):
                raise _declare(
                    f"{path}.args[{i}]",
                    "A field of this type is read only under `eq`, `ne` or `is_empty`.")
    if node.op in _ARITH:
        return _type_arith(node, path, ts)
    if node.op in _TEXT_DATE:
        return _type_text_date(node, path, ts)
    return _type_logic(node, path, ts)


def _infer(node: Node, path: str, by_key: Mapping[str, FieldSpec], target: str | None) -> str:
    if node.op == "ref":
        key = str(node.value)
        field = by_key.get(key)
        if field is None:
            raise _declare(path, f"`{key}` is not a field of this object.")
        if key == target:
            raise _declare(path, f"A formula cannot read its own target `{key}`.")
        return field_type(field)
    if node.op in _LITERALS:
        return "number" if node.op == "num" else node.op
    ts = [_infer(a, f"{path}.args[{i}]", by_key, target) for i, a in enumerate(node.args)]
    return _type_call(node, path, ts)


def check_expr(node: Node, fields: Sequence[FieldSpec], *, target: str | None = None) -> str:
    """The type of the tree: ``number``, ``money``, ``text``, ``bool`` or ``date``.

    Each ref must name a field of *fields*, and not *target*. Each call must
    have args of its types and the ``rounding`` and ``places`` that it needs.
    Raises :class:`ExprError` with a JSON path.
    """
    kind = _infer(node, "expr", {f.key: f for f in fields}, target)
    if _opaque(kind):
        raise _declare("expr", "The result of a formula cannot be a field of this type.")
    return kind


# --- the rounding -----------------------------------------------------------


def _round_ratio(num: int, den: int, mode: str) -> int:
    """The integer nearest ``num / den`` under *mode*. This is the one place of
    rounding. ``den`` is above 0."""
    sign = -1 if num < 0 else 1
    quotient, rest = divmod(abs(num), den)
    if rest == 0:
        return sign * quotient
    twice = 2 * rest
    if mode == "down":
        up = False
    elif mode == "up":
        up = True
    elif mode == "half_up":
        up = twice >= den
    elif mode == "half_even":
        up = twice > den or (twice == den and quotient % 2 == 1)
    else:
        raise _declare("expr", f"`rounding` is {', '.join(ROUNDINGS)}.")
    return sign * (quotient + 1 if up else quotient)


def _parts(value: Decimal) -> tuple[int, int]:
    """The integer and the exponent of a finite *value*: ``value = int * 10 ** exp``."""
    sign, digits, exp = value.as_tuple()
    if not isinstance(exp, int):
        raise _run_error(RESULT_TOO_LARGE)
    whole = int("".join(str(d) for d in digits))
    return (-whole if sign else whole), exp


def _scaled(whole: int, places: int) -> Decimal:
    """``whole * 10 ** -places``, built from its digits so no context rounds it."""
    digits = tuple(int(c) for c in str(abs(whole)))
    return Decimal((1 if whole < 0 else 0, digits, -places))


def _divide(top: Decimal, bottom: Decimal, places: int, mode: str) -> Decimal:
    """``top / bottom`` rounded to *places* under *mode*, on integers, with no
    loss before the one rounding."""
    t_int, t_exp = _parts(top)
    b_int, b_exp = _parts(bottom)
    if b_int == 0:
        raise _run_error(DIVISION_BY_ZERO)
    shift = t_exp - b_exp + places
    num, den = t_int, b_int
    if shift >= 0:
        num *= 10**shift
    else:
        den *= 10 ** (-shift)
    if den < 0:
        num, den = -num, -den
    return _scaled(_round_ratio(num, den, mode), places)


def _is_whole(value: Decimal) -> bool:
    whole, exp = _parts(value)
    return exp >= 0 or whole % (10 ** (-exp)) == 0


def number_json(value: Decimal) -> Any:
    """The JSON number of a rounded *value*: a whole number as an int, else a
    JSON float with at most 15 digits, which reads back exactly."""
    if _is_whole(value):
        return int(value)
    return json.loads(format(value, "f"))


# --- the run ----------------------------------------------------------------


def _ref(value: Any) -> Any:
    """A stored value as a run value. An empty value is null."""
    if is_blank(value):
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, Mapping) and set(value) == {"minor", "currency"}:
        return Money(Decimal(value["minor"]), str(value["currency"]))
    if isinstance(value, str | Mapping | list):
        return value
    return Decimal(repr(value))


def _numbers(values: Sequence[Any]) -> list[Decimal]:
    if not all(isinstance(v, Decimal) for v in values):
        raise _run_error(TYPE_ERROR)
    return list(values)


def _monies(values: Sequence[Any]) -> list[Money]:
    if not all(isinstance(v, Money) for v in values):
        raise _run_error(TYPE_ERROR)
    out = list(values)
    if any(m.currency != out[0].currency for m in out):
        raise _run_error(CURRENCY_MISMATCH)
    return out


def _texts(values: Sequence[Any]) -> list[str]:
    if not all(isinstance(v, str) for v in values):
        raise _run_error(TYPE_ERROR)
    return list(values)


def _bools(values: Sequence[Any]) -> list[bool]:
    if not all(type(v) is bool for v in values):
        raise _run_error(TYPE_ERROR)
    return list(values)


def _sum(values: Sequence[Decimal]) -> Decimal:
    total = Decimal(0)
    for v in values:
        total = _CTX.add(total, v)
    return total


def _product(values: Sequence[Decimal]) -> Decimal:
    total = _ONE
    for v in values:
        total = _CTX.multiply(total, v)
    return total


def _mode(node: Node) -> str:
    if node.rounding not in ROUNDINGS:
        raise _run_error(TYPE_ERROR)
    return str(node.rounding)


def _places(node: Node) -> int:
    return 0 if node.places is None else node.places


def _add(node: Node, a: list[Any]) -> Any:
    if isinstance(a[0], Money):
        ms = _monies(a)
        return Money(_sum([m.minor for m in ms]), ms[0].currency)
    return _sum(_numbers(a))


def _sub(node: Node, a: list[Any]) -> Any:
    if isinstance(a[0], Money):
        x, y = _monies(a)
        return Money(_CTX.subtract(x.minor, y.minor), x.currency)
    x_n, y_n = _numbers(a)
    return _CTX.subtract(x_n, y_n)


def _mul(node: Node, a: list[Any]) -> Any:
    monies = [v for v in a if isinstance(v, Money)]
    if not monies:
        return _product(_numbers(a))
    factor = _product(_numbers([v for v in a if not isinstance(v, Money)]))
    if len(monies) != 1:
        raise _run_error(TYPE_ERROR)
    exact = _CTX.multiply(monies[0].minor, factor)
    return Money(_divide(exact, _ONE, 0, _mode(node)), monies[0].currency)


def _div(node: Node, a: list[Any]) -> Any:
    mode = _mode(node)
    if isinstance(a[0], Money) and isinstance(a[1], Money):
        x, y = _monies(a)
        return _divide(x.minor, y.minor, _places(node), mode)
    if isinstance(a[0], Money):
        (divisor,) = _numbers([a[1]])
        return Money(_divide(a[0].minor, divisor, 0, mode), a[0].currency)
    x_n, y_n = _numbers(a)
    return _divide(x_n, y_n, _places(node), mode)


def _unary(node: Node, a: list[Any]) -> Any:
    negate = node.op == "neg"
    if isinstance(a[0], Money):
        m = a[0]
        return Money(_CTX.minus(m.minor) if negate else _CTX.abs(m.minor), m.currency)
    (n,) = _numbers(a)
    return _CTX.minus(n) if negate else _CTX.abs(n)


def _extreme(node: Node, a: list[Any]) -> Any:
    if isinstance(a[0], Money):
        ms = _monies(a)
        keyed = [(m.minor, m) for m in ms]
        return (min if node.op == "min" else max)(keyed, key=lambda p: p[0])[1]
    ns = _numbers(a)
    return min(ns) if node.op == "min" else max(ns)


def _round(node: Node, a: list[Any]) -> Any:
    (n,) = _numbers(a)
    return _divide(n, _ONE, _places(node), _mode(node))


def _text_fn(node: Node, a: list[Any]) -> Any:
    ts = _texts(a)
    fn = node.op
    if fn == "len":
        return Decimal(len(ts[0]))
    out = {"concat": lambda: "".join(ts), "lower": ts[0].lower, "upper": ts[0].upper,
           "trim": ts[0].strip}[fn]()
    if len(out) > MAX_LONG_TEXT_CHARS:
        raise _run_error(RESULT_TOO_LARGE)
    return out


def _day(value: Any) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise _run_error(TYPE_ERROR) from None


def _days(node: Node, a: list[Any]) -> Any:
    if node.op == "days_between":
        return Decimal((_day(a[1]) - _day(a[0])).days)
    (n,) = _numbers([a[1]])
    if not _is_whole(n):
        raise _run_error(BAD_ARGUMENT)
    return (_day(a[0]) + timedelta(days=int(n))).isoformat()


def _equal(a: Any, b: Any) -> bool:
    if isinstance(a, Money) and isinstance(b, Money) and a.currency != b.currency:
        raise _run_error(CURRENCY_MISMATCH)
    return bool(a == b)


def _order(node: Node, a: list[Any]) -> Any:
    x: Any
    y: Any
    if isinstance(a[0], Money):
        ms = _monies(a)
        x, y = ms[0].minor, ms[1].minor
    elif isinstance(a[0], Decimal):
        x, y = _numbers(a)
    else:
        x, y = _texts(a)
        _day(x)
        _day(y)
    return {"lt": x < y, "lte": x <= y, "gt": x > y, "gte": x >= y}[node.op]


def _compare(node: Node, a: list[Any]) -> Any:
    if node.op in ("eq", "ne"):
        same = _equal(a[0], a[1])
        return same if node.op == "eq" else not same
    return _order(node, a)


def _logic(node: Node, a: list[Any]) -> Any:
    bs = _bools(a)
    if node.op == "not":
        return not bs[0]
    return all(bs) if node.op == "and" else any(bs)


_FAMILIES: Mapping[str, Any] = {
    "add": _add, "sub": _sub, "mul": _mul, "div": _div, "neg": _unary, "abs": _unary,
    "min": _extreme, "max": _extreme, "round": _round,
    "concat": _text_fn, "lower": _text_fn, "upper": _text_fn, "trim": _text_fn, "len": _text_fn,
    "add_days": _days, "days_between": _days,
    "eq": _compare, "ne": _compare, "lt": _compare, "lte": _compare, "gt": _compare,
    "gte": _compare, "and": _logic, "or": _logic, "not": _logic,
}  # fmt: skip


def _apply(node: Node, a: list[Any]) -> Any:
    """One call over its evaluated args. A null in the args gives null."""
    if node.op == "is_empty":
        return is_blank(a[0])
    if node.op == "coalesce":
        return next((v for v in a if v is not None), None)
    if any(v is None for v in a):
        return None
    family = _FAMILIES.get(node.op)
    if family is None:
        raise _run_error(TYPE_ERROR)
    return family(node, a)


class _Run:
    """One run of a tree. ``steps`` counts the nodes that ran."""

    def __init__(self, record: Mapping[str, Any], limit: int) -> None:
        self.record = record
        self.limit = limit
        self.steps = 0

    def run(self, node: Node) -> Any:
        self.steps += 1
        if self.steps > self.limit:
            raise _run_error(STEP_CAP)
        op = node.op
        if op == "ref":
            return _ref(self.record.get(str(node.value)))
        if op == "num":
            return Decimal(str(node.value))
        if op == "money":
            minor, currency = node.value
            return Money(Decimal(minor), currency)
        if op in _LITERALS:
            return node.value
        if op == "if":
            return self._if(node)
        return _apply(node, [self.run(a) for a in node.args])

    def _if(self, node: Node) -> Any:
        if len(node.args) != 3:
            raise _run_error(TYPE_ERROR)
        condition = self.run(node.args[0])
        if condition is not None and type(condition) is not bool:
            raise _run_error(TYPE_ERROR)
        return self.run(node.args[1] if condition else node.args[2])


def evaluate(node: Node, record: Mapping[str, Any], *, max_steps: int = MAX_STEPS) -> Any:
    """Run the tree on *record*, the checked data of one record.

    The answer is a run value: None, a ``Decimal``, :class:`Money`, text or a bool.
    Only an :class:`ExprError` leaves this function: each error of ``decimal``
    and of the runtime maps to a named code.
    """
    try:
        return _Run(record, max_steps).run(node)
    except ExprError:
        raise
    except ZeroDivisionError:
        raise _run_error(DIVISION_BY_ZERO) from None
    except (ArithmeticError, ValueError):
        raise _run_error(RESULT_TOO_LARGE) from None
    except RecursionError:
        raise _run_error(STEP_CAP) from None
    except (TypeError, AttributeError, KeyError, IndexError):
        raise _run_error(TYPE_ERROR) from None


# --- the result -------------------------------------------------------------


def _number_result(value: Any, scale: int, rounding: str | None) -> Any:
    if not isinstance(value, Decimal) or rounding not in ROUNDINGS:
        raise _run_error(TYPE_ERROR)
    rounded = _divide(value, _ONE, scale, str(rounding))
    if abs(rounded) >= 10 ** (SIGNIFICANT_DIGITS - scale):
        raise _run_error(RESULT_TOO_LARGE)
    return number_json(rounded)


def _money_result(value: Any, currency: str | None) -> dict[str, Any]:
    if not isinstance(value, Money):
        raise _run_error(TYPE_ERROR)
    if value.currency != currency:
        raise _run_error(CURRENCY_MISMATCH)
    if abs(value.minor) >= MAX_ABS_NUMBER:
        raise _run_error(RESULT_TOO_LARGE)
    return {"minor": int(value.minor), "currency": value.currency}


def _text_result(value: Any) -> str | None:
    if not isinstance(value, str):
        raise _run_error(TYPE_ERROR)
    if len(value) > MAX_TEXT_CHARS:
        raise _run_error(RESULT_TOO_LARGE)
    return None if is_blank(value) else value


def store_result(value: Any, field: FieldSpec, *, scale: int = 0, rounding: str | None = None,
                 currency: str | None = None) -> Any:
    """The result *value* of a run, as the target *field* stores it, or None.

    None means no value: the target clears. A number rounds to *scale* places
    under *rounding*, and its size stays below ``10 ** (15 - scale)``, or the
    call raises ``result_too_large``. Money keeps whole minor units below 10^15
    in *currency*. The answer then runs through ``check_value`` of the field.
    """
    if value is None:
        return None
    try:
        if field.type == "number":
            stored: Any = _number_result(value, scale, rounding)
        elif field.type == "money":
            stored = _money_result(value, currency)
        elif field.type == "text":
            stored = _text_result(value)
        elif field.type in ("checkbox", "date"):
            stored = value
        else:
            raise _run_error(TYPE_ERROR)
        return None if stored is None else check_value(field, stored)
    except FieldError:
        raise _run_error(BAD_RESULT) from None
    except ExprError:
        raise
    except ZeroDivisionError:
        raise _run_error(DIVISION_BY_ZERO) from None
    except (ArithmeticError, ValueError):
        raise _run_error(RESULT_TOO_LARGE) from None
