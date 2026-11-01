"""The filter, the sort and the paging of a Studio object query (WS-STU C2a).

Spec: ``project-docs/specs/ai_studio.md`` §3.1 and the §14 C2a row.

**One grammar.** The store (``gateway/studio/objects_store.query_records``)
parses its input here. B2b's ``object:<name>.list`` action and the chat tool
pass their input to the store, and :func:`list_input_schema` is the JSON
Schema of that input. No caller keeps a second grammar.

**Pure.** No gateway import, no SQL and no I/O. The store builds the SQL from
the :class:`Query` that :func:`parse_query` gives, and the store is the one
module that holds SQL of the object tables (the fence
``test_one_writer_of_the_four_tables``).

**The shape.** ``filter`` is a list of at most :data:`MAX_FILTERS`
conditions, and a record must meet each one (AND). A condition is
``{"field": <key>, "op": <op>, "value": <value>}``. ``sort`` is
``{"field": <key>, "dir": "asc" | "desc"}`` or None. :data:`OPS_BY_TYPE` is
the closed table of the ops of each field type.

**The values.** Each value runs through ``studio_fields.check_value``, the one
check of a value. So an email compares in lower case, a datetime in UTC, and
a select value must be one of the options. A money filter takes a whole
number of minor units and does not check the currency. A date value means
the whole day in UTC.

**The checkbox rule.** A checkbox has two states for a member, ticked and not
ticked. A form that does not send an unticked box writes no value, so
``eq false`` and ``ne true`` match a record with no value too. ``is_empty``
matches only a record with no value. This is the one exception to the rule
that ``ne`` matches only a record that holds a value.

**Errors.** Each refusal raises :class:`QueryError` with one of
:data:`QUERY_ERROR_CODES` and one sentence. The sentence names the field key
when the key has the key shape. It never echoes a value.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from acb_common.paging import PAGE_MAX, bounds
from acb_common.studio_fields import (
    MAX_ABS_NUMBER,
    MAX_TEXT_CHARS,
    NAME_PATTERN,
    FieldError,
    FieldSpec,
    check_value,
    storable_text,
)

# --- the caps ---------------------------------------------------------------

#: The conditions of one filter.
MAX_FILTERS = 8
#: The values of one ``in`` condition.
MAX_IN_VALUES = 100
#: The largest offset of a page.
MAX_OFFSET = 10_000
#: The count of a query stops here. Below it the total is exact. At it, the
#: page says "at least" this many.
TOTAL_CAP = MAX_OFFSET + 1
#: ``hot_or_small``: a filter on a field that is not hot runs only on at most
#: this many candidate records.
SMALL_OBJECT = 5_000
#: The limit of each statement of a query, in milliseconds.
TIMEOUT_MS = 5_000
#: The page size when the caller gives none. :data:`~acb_common.paging.PAGE_MAX`
#: is the largest.
DEFAULT_LIMIT = 25

# --- the ops ----------------------------------------------------------------

OPS: tuple[str, ...] = ("eq", "ne", "lt", "lte", "gt", "gte", "in", "contains", "is_empty")

_TEXT_OPS = frozenset({"eq", "ne", "in", "contains", "is_empty"})
_NUMBER_OPS = frozenset({"eq", "ne", "lt", "lte", "gt", "gte", "in", "is_empty"})
_TIME_OPS = frozenset({"eq", "lt", "lte", "gt", "gte", "is_empty"})

#: The closed table: the ops of each field type. A type that is not here,
#: and a field with ``many``, takes no filter.
OPS_BY_TYPE: Mapping[str, frozenset[str]] = {
    "text": _TEXT_OPS,
    "email": _TEXT_OPS,
    "select": frozenset({"eq", "ne", "in", "is_empty"}),
    "number": _NUMBER_OPS,
    "money": _NUMBER_OPS,
    "date": _TIME_OPS,
    "datetime": _TIME_OPS,
    "checkbox": frozenset({"eq", "ne", "is_empty"}),
}

#: The types that a sort takes. A sort needs a hot field, and ``v_bool`` has
#: no index, so a checkbox field takes no sort.
SORT_TYPES: frozenset[str] = frozenset(OPS_BY_TYPE) - {"checkbox"}

# --- the errors -------------------------------------------------------------

FIELD_UNKNOWN = "query_field_unknown"
OP_NOT_ALLOWED = "query_op_not_allowed"
VALUE_INVALID = "query_value_invalid"
NOT_INDEXED = "query_not_indexed"
TOO_BIG = "query_too_big"
TIMEOUT = "query_timeout"

QUERY_ERROR_CODES: tuple[str, ...] = (
    FIELD_UNKNOWN, OP_NOT_ALLOWED, VALUE_INVALID, NOT_INDEXED, TOO_BIG, TIMEOUT,
)


class QueryError(ValueError):
    """One refused query: a code of :data:`QUERY_ERROR_CODES` and one sentence.

    ``field`` is the field key when the refusal is about one field. A route
    answers 503 for ``query_timeout`` and 422 for each other code.
    """

    def __init__(self, code: str, message: str, field: str | None = None) -> None:
        if code not in QUERY_ERROR_CODES:
            raise ValueError(f"unknown query error code {code!r}")
        super().__init__(message)
        self.code = code
        self.message = message
        self.field = field


# --- the parsed query -------------------------------------------------------


@dataclass(frozen=True)
class Condition:
    """One checked condition.

    ``value`` is the checked value: a ``str``, a ``Decimal``, a ``bool``, an
    aware ``datetime``, or a tuple of these for ``in``. For ``contains`` it is
    the ``ILIKE`` pattern, with ``%``, ``_`` and ``\\`` escaped. For
    ``is_empty`` it is True (no value) or False (a value).

    A checkbox condition is ``eq`` True (ticked) or ``eq`` False (not ticked,
    which includes a record with no value). The parse turns ``ne`` into these.

    ``until`` is set only for ``eq`` on a day: the record matches from
    ``value`` up to ``until``, and not at ``until``.
    """

    field: FieldSpec
    op: str
    value: Any = None
    until: datetime | None = None


@dataclass(frozen=True)
class Sort:
    """The sort of a query: one hot field, then the record id."""

    field: FieldSpec
    desc: bool = False


@dataclass(frozen=True)
class Query:
    """A filter and a sort, checked against the fields of one object."""

    conditions: tuple[Condition, ...] = ()
    sort: Sort | None = None

    def hot_only(self) -> tuple[Condition, ...]:
        """The conditions on hot fields."""
        return tuple(c for c in self.conditions if c.field.hot)

    def cold(self) -> tuple[Condition, ...]:
        """The conditions on fields that are not hot (``hot_or_small``)."""
        return tuple(c for c in self.conditions if not c.field.hot)

    def hot_keys(self) -> tuple[str, ...]:
        """The keys of the fields that this query reads as hot."""
        keys = {c.field.key for c in self.hot_only()}
        if self.sort is not None:
            keys.add(self.sort.field.key)
        return tuple(sorted(keys))


# --- the shape --------------------------------------------------------------

_NAME_RE = re.compile(NAME_PATTERN, re.ASCII)
_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", re.ASCII)
_CONDITION_KEYS = frozenset({"field", "op", "value"})
_SORT_KEYS = frozenset({"field", "dir"})
_OP_LIST = ", ".join(OPS)


def _named(key: Any) -> str:
    """The key to name in a refusal: the key when it has the key shape, else ``?``."""
    return key if isinstance(key, str) and _NAME_RE.fullmatch(key) else "?"


def _is_list(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, str | bytes)


def check_shape(filter: Any, sort: Any = None) -> list[Mapping[str, Any]]:
    """The conditions of *filter*, after a check of the shape only.

    It needs no fields, so a route can run it before it opens a session.
    :func:`parse_query` runs it first too. Raises :class:`QueryError`.
    """
    if filter is None:
        items: list[Any] = []
    elif _is_list(filter):
        items = list(filter)
    else:
        raise QueryError(VALUE_INVALID, "The filter is a list of conditions: {field, op, value}.")
    if len(items) > MAX_FILTERS:
        raise QueryError(TOO_BIG, f"A filter holds at most {MAX_FILTERS} conditions.")
    for item in items:
        if not isinstance(item, Mapping) or not {"field", "op"} <= set(item) \
                or not set(item) <= _CONDITION_KEYS:
            raise QueryError(
                VALUE_INVALID, "Each condition is an object with field, op and value, and no other key.")
        key = _named(item["field"])
        if item["op"] not in OPS:
            raise QueryError(OP_NOT_ALLOWED, f"Field `{key}`: the op is unknown. Use {_OP_LIST}.", key)
        if item["op"] == "in":
            values = item.get("value")
            if not _is_list(values) or not values:
                raise QueryError(VALUE_INVALID, f"Field `{key}`: `in` takes a list of values.", key)
            if len(values) > MAX_IN_VALUES:
                raise QueryError(TOO_BIG, f"Field `{key}`: `in` takes at most {MAX_IN_VALUES} values.", key)
    _check_sort_shape(sort)
    return items


def _check_sort_shape(sort: Any) -> None:
    if sort is None:
        return
    if not isinstance(sort, Mapping) or "field" not in sort or not set(sort) <= _SORT_KEYS:
        raise QueryError(VALUE_INVALID, 'A sort is an object: {"field": <key>, "dir": "asc" or "desc"}.')
    if sort.get("dir", "asc") not in ("asc", "desc"):
        raise QueryError(VALUE_INVALID, 'The sort `dir` is "asc" or "desc".', _named(sort["field"]))


def page_bounds(offset: Any, limit: Any) -> tuple[int, int]:
    """``(offset, limit)`` of one page. ``paging.bounds`` cuts the limit to
    :data:`~acb_common.paging.PAGE_MAX`. An offset past :data:`MAX_OFFSET`
    raises ``query_too_big``."""
    start, size = bounds(offset, limit, default=DEFAULT_LIMIT)
    if start > MAX_OFFSET:
        raise QueryError(
            TOO_BIG, f"A page starts at offset {MAX_OFFSET} or before. Narrow the filter to see the rest.")
    return start, size


# --- the values -------------------------------------------------------------


def _field(by_key: Mapping[str, FieldSpec], raw: Any) -> FieldSpec:
    field = by_key.get(raw) if isinstance(raw, str) else None
    if field is None:
        key = _named(raw)
        raise QueryError(FIELD_UNKNOWN, f"Field `{key}` is not a field of this object.", key)
    return field


def _invalid(field: FieldSpec, why: str) -> QueryError:
    return QueryError(VALUE_INVALID, f"Field `{field.key}` {why}", field.key)


def _checked(field: FieldSpec, value: Any) -> Any:
    """One value through the one check of a value. A refusal names the key."""
    try:
        return check_value(field, value)
    except FieldError as exc:
        raise QueryError(VALUE_INVALID, exc.message, field.key) from None


def _number(field: FieldSpec, value: Any) -> Decimal:
    if field.type == "money":
        if isinstance(value, bool) or not isinstance(value, int) or abs(value) >= MAX_ABS_NUMBER:
            raise _invalid(field, "takes a whole number of minor units, as 125000 for INR 1,250.00.")
        return Decimal(value)
    checked = _checked(field, value)
    # repr gives the digits that json.dumps wrote, so the two compare equal.
    return Decimal(repr(checked)) if isinstance(checked, float) else Decimal(checked)


def _day(field: FieldSpec, value: str) -> tuple[datetime, datetime]:
    """The start of the day and the start of the next day, in UTC."""
    day = _checked(replace(field, type="date"), value)
    start = datetime.fromisoformat(f"{day}T00:00:00+00:00")
    try:
        return start, start + timedelta(days=1)
    except OverflowError:
        raise _invalid(field, "takes a day before 9999-12-31.") from None


def _time(field: FieldSpec, op: str, value: Any) -> Condition:
    """A date or datetime condition, as a test of the instant.

    A day value means the whole day in UTC: ``eq`` is the day, ``lt`` is
    before it, ``lte`` is up to its end, ``gt`` is after its end and ``gte``
    is from its start.
    """
    if field.type == "date" or (isinstance(value, str) and _DATE_RE.fullmatch(value)):
        if not isinstance(value, str):
            raise _invalid(field, "takes a real date as YYYY-MM-DD.")
        start, end = _day(field, value)
        if op == "eq":
            return Condition(field, "eq", start, until=end)
        moved = {"lt": ("lt", start), "lte": ("lt", end), "gt": ("gte", end), "gte": ("gte", start)}
        new_op, at = moved[op]
        return Condition(field, new_op, at)
    stamp = _checked(field, value)  # "...Z", in UTC
    return Condition(field, op, datetime.fromisoformat(stamp[:-1] + "+00:00"))


def _pattern(field: FieldSpec, value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > MAX_TEXT_CHARS \
            or not storable_text(value):
        raise _invalid(field, f"`contains` takes text of 1 to {MAX_TEXT_CHARS} characters.")
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _scalar(field: FieldSpec, value: Any) -> Any:
    if field.type in ("number", "money"):
        return _number(field, value)
    return _checked(field, value)


def _condition(field: FieldSpec, op: str, item: Mapping[str, Any]) -> Condition:
    value = item.get("value")
    if op == "is_empty":
        if value is not None and type(value) is not bool:
            raise _invalid(field, "`is_empty` takes true or false.")
        return Condition(field, op, True if value is None else value)
    if value is None:
        raise _invalid(field, f"has no value for `{op}`. Give a value.")
    if field.type == "checkbox":
        ticked = _checked(field, value)
        return Condition(field, "eq", ticked if op == "eq" else not ticked)
    if field.type in ("date", "datetime"):
        return _time(field, op, value)
    if op == "contains":
        return Condition(field, op, _pattern(field, value))
    if op == "in":
        return Condition(field, op, tuple(_scalar(field, v) for v in value))
    return Condition(field, op, _scalar(field, value))


def _sort(by_key: Mapping[str, FieldSpec], sort: Mapping[str, Any] | None) -> Sort | None:
    if sort is None:
        return None
    field = _field(by_key, sort["field"])
    if field.type not in SORT_TYPES or field.many:
        raise QueryError(OP_NOT_ALLOWED, f"Field `{field.key}` is a {field.type} field, and it takes no sort.",
                         field.key)
    if not field.hot:
        raise QueryError(
            NOT_INDEXED, f"Field `{field.key}` is not hot, so a query cannot sort by it. Turn it hot.",
            field.key)
    return Sort(field, desc=sort.get("dir") == "desc")


def parse_query(fields: Sequence[FieldSpec], filter: Any, sort: Any = None) -> Query:
    """The :class:`Query` of *filter* and *sort*, against the *fields* of one object.

    Raises :class:`QueryError` on the first problem. The ``hot_or_small``
    rule needs a count, so the store applies it.
    """
    items = check_shape(filter, sort)
    by_key = {f.key: f for f in fields}
    conditions = []
    for item in items:
        field = _field(by_key, item["field"])
        allowed = OPS_BY_TYPE.get(field.type, frozenset())
        if field.many or not allowed:
            raise QueryError(OP_NOT_ALLOWED, f"Field `{field.key}` is a {field.type} field, and it takes no filter.",
                             field.key)
        if item["op"] not in allowed:
            ops = ", ".join(o for o in OPS if o in allowed)
            raise QueryError(
                OP_NOT_ALLOWED, f"Field `{field.key}` is a {field.type} field. Use {ops}.", field.key)
        conditions.append(_condition(field, item["op"], item))
    return Query(tuple(conditions), _sort(by_key, sort))


# --- the input schema -------------------------------------------------------

_LIST_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "filter": {
            "type": "array",
            "maxItems": MAX_FILTERS,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["field", "op"],
                "properties": {
                    "field": {"type": "string", "pattern": f"^{NAME_PATTERN}$"},
                    "op": {"enum": list(OPS)},
                    "value": {},
                },
            },
        },
        "sort": {
            "anyOf": [
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["field"],
                    "properties": {
                        "field": {"type": "string", "pattern": f"^{NAME_PATTERN}$"},
                        "dir": {"enum": ["asc", "desc"]},
                    },
                },
                {"type": "null"},
            ],
        },
        "limit": {"type": "integer", "minimum": 1, "maximum": PAGE_MAX},
        "offset": {"type": "integer", "minimum": 0, "maximum": MAX_OFFSET},
    },
}


def list_input_schema() -> dict[str, Any]:
    """The JSON Schema of the input of a list: filter, sort, limit and offset.
    A new copy on each call, so a caller cannot change the shared one."""
    return copy.deepcopy(_LIST_INPUT_SCHEMA)
