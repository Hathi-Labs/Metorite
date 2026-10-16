"""CRM · field SQL — the one builder of SQL over a jsonb field (WS-53 CRM-Q25).

Spec: ``project-docs/specs/crm_platform.md`` §18.11, CRM-Q25 done-when 2 to 5.

The typed aggregate (``aggregate.py``) measures, groups and filters by one
field of a record. A core record keeps its custom fields in ``custom``, and a
generic module record keeps each Zoho field in ``fields``. Both are jsonb, and
the values are Zoho's, unchanged: a currency is a JSON number, a date is
``"YYYY-MM-DD"``, a datetime is ISO with an offset, a boolean is a JSON bool,
a lookup is ``{id, name}``, and some numbers arrive as strings.

**The field SQL lives here and nowhere else.** ``aggregate.py`` holds no jsonb
operator, and ``tests/unit/test_crm_aggregate.py`` fails when it does. The
core list filters stay in ``records.list_query``.

**A name never reaches the SQL text.** :class:`FieldScope` holds the field
definitions of the org (core) or of the module. Each name from the caller is
looked up there first, and an unknown name is a 422. The builders then bind
the key, every value and the module name as parameters. The jsonb column is a
literal of this module, and a bucket picks its unit and format from
:data:`BUCKETS`. The same test file sends a hostile name through each builder.

**The types** (``crm_field_defs.type``). A measure takes ``number``,
``decimal``, ``currency`` or ``percent``. A group takes ``select`` (a picklist),
``boolean`` and ``lookup`` by value, and ``date`` and ``datetime`` by a bucket.
Any other type is a 422.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import HTTPException
from gateway.routes.crm.fields import custom_defs
from gateway.routes.crm.modules import _field_defs

#: The query-string prefix of a module field filter: ``field.<key>=<value>``,
#: and ``field.<key>.from`` / ``field.<key>.to`` for a date range.
FIELD_PREFIX = "field."

#: The two ends of a date range filter.
RANGE_ENDS: dict[str, str] = {".from": ">=", ".to": "<="}

#: The types a field measure takes.
MEASURE_TYPES: tuple[str, ...] = ("number", "decimal", "currency", "percent")

#: The types that group by value. ``select`` is the stored word of a picklist.
VALUE_GROUP_TYPES: tuple[str, ...] = ("select", "boolean", "lookup")

#: The types that group by a bucket.
DATE_TYPES: tuple[str, ...] = ("date", "datetime")

#: ``measure`` → its aggregate over the guarded number ``{n}``. ``sum`` gives 0
#: for a group with no value. The others give NULL there.
FIELD_MEASURES: dict[str, str] = {
    "sum": "COALESCE(SUM({n}), 0)",
    "avg": "AVG({n})",
    "min": "MIN({n})",
    "max": "MAX({n})",
}

#: ``bucket`` → the unit of ``date_trunc`` and the format of the group key.
BUCKETS: dict[str, tuple[str, str]] = {
    "month": ("month", "YYYY-MM"),
    "quarter": ("quarter", 'YYYY-"Q"Q'),
    "year": ("year", "YYYY"),
}

#: The jsonb column of each kind of record. The only two columns a scope takes.
CORE_COLUMN = "custom"
MODULE_COLUMN = "fields"
_COLUMNS = (CORE_COLUMN, MODULE_COLUMN)

#: A number that arrives as a string. Bound into no statement: it is a literal.
#: The digits are bounded, so no string can overflow ``numeric``.
_NUMBER_TEXT = r"'^\s*-?[0-9]{1,30}(\.[0-9]{1,30})?\s*$'"

#: A value that starts with the shape of a calendar date. A datetime starts
#: with one too. The year starts at 1000, so ``make_date`` takes every year
#: that passes. The day is checked against its month in :func:`_date`.
_DATE_TEXT = r"'^[1-9][0-9]{3}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])'"

#: The bounds of a number in a filter. ``1e1000000`` is finite, and Postgres
#: still refuses it, so a larger number is a 422 before any statement.
MAX_DIGITS = 60
MAX_EXPONENT = 100

#: The parameter names of this module. A list filter never uses the prefix.
_MEASURE_KEY = "agf_m"
_GROUP_KEY = "agf_g"
_MODULE = "agf_module"


@dataclass(frozen=True)
class FieldRef:
    """One checked field: its key, its stored type, its label, and the label
    of each picklist value."""

    key: str
    type: str
    label: str
    options: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class FieldScope:
    """The fields that one aggregate may name, and the jsonb column they
    live in. ``owner`` is what a 422 names: ``deals`` or ``module:Products``."""

    column: str
    owner: str
    fields: dict[str, FieldRef]

    def __post_init__(self) -> None:
        if self.column not in _COLUMNS:
            raise ValueError(f"field_sql: {self.column!r} is not a field column")

    def ref(self, key: str | None) -> FieldRef:
        """The field named *key*, or a 422 that names the legal keys."""
        found = self.fields.get(key or "")
        if found is None:
            raise HTTPException(status_code=422, detail={
                "code": "unknown_field",
                "message": f"'{key}' is not a field of {self.owner}. "
                           "Read the field keys with crm_list_fields.",
            })
        return found


@dataclass(frozen=True)
class MeasureSql:
    """The aggregate of a field measure. ``sum`` and ``count`` give the
    total of an ``avg`` across groups."""

    value: str
    sum: str
    count: str
    params: dict[str, Any]


@dataclass(frozen=True)
class GroupSql:
    """The group key, and an aggregate that names it (a lookup's name)."""

    key: str
    label: str
    params: dict[str, Any]


@dataclass(frozen=True)
class WhereSql:
    sql: str
    params: dict[str, Any]


def _options(raw: Any) -> dict[str, str]:
    """Each picklist value → its label, from a ``crm_field_defs.options`` list."""
    if not isinstance(raw, list):
        return {}
    return {
        str(o.get("value")): str(o.get("label"))
        for o in raw
        if isinstance(o, dict) and o.get("value") is not None and o.get("label")
    }


def _ref(defn: Any) -> FieldRef:
    return FieldRef(
        key=str(defn.key), type=str(defn.type), label=str(defn.label or defn.key),
        options=_options(getattr(defn, "options", None)),
    )


async def core_scope(db: Any, slug: str) -> FieldScope:
    """The custom fields of a core entity that an aggregate may name.

    A system overlay is not a custom key (``fields.custom_defs`` leaves it
    out), and a retired field is left out here, as ``core.extra_filters``
    refuses it.
    """
    defs = await custom_defs(db, slug)
    return FieldScope(
        column=CORE_COLUMN, owner=slug,
        fields={d.key: _ref(d) for d in defs if not d.retired and not d.is_system},
    )


async def module_scope(db: Any, module: str) -> FieldScope:
    """The fields of a mirrored module. The caller has checked the module
    against the mirrored set of the profile (``modules._require_module``)."""
    defs = await _field_defs(db, module)
    return FieldScope(
        column=MODULE_COLUMN, owner=f"module:{module}",
        fields={d.key: _ref(d) for d in defs},
    )


def _refuse(message: str) -> HTTPException:
    return HTTPException(status_code=422, detail={
        "code": "unfit_field", "message": message,
    })


def _type_words(types: tuple[str, ...]) -> str:
    return ", ".join(types)


def check_measure(scope: FieldScope, key: str | None) -> FieldRef:
    """The field of a ``sum``, ``avg``, ``min`` or ``max``, or a 422."""
    ref = scope.ref(key)
    if ref.type not in MEASURE_TYPES:
        raise _refuse(
            f"'{ref.key}' is a {ref.type} field. A measure takes a field of "
            f"type {_type_words(MEASURE_TYPES)}.",
        )
    return ref


def check_group(scope: FieldScope, key: str | None, bucket: str | None) -> FieldRef:
    """The field of ``group_field``, or a 422. A date needs a bucket, and a
    value group takes none."""
    ref = scope.ref(key)
    if ref.type in DATE_TYPES:
        if bucket not in BUCKETS:
            raise _refuse(
                f"'{ref.key}' is a {ref.type} field. Give bucket as one of: "
                f"{', '.join(BUCKETS)}.",
            )
        return ref
    if ref.type not in VALUE_GROUP_TYPES:
        raise _refuse(
            f"'{ref.key}' is a {ref.type} field. A group takes a field of type "
            f"{_type_words(VALUE_GROUP_TYPES)}, or a date with a bucket.",
        )
    if bucket is not None:
        raise _refuse(f"A bucket goes with a date field, not with '{ref.key}'.")
    return ref


# ── The expressions. Every key and value is a bound parameter. ──────────────


def _number(column: str, p: str) -> str:
    """The field as a number, or NULL. A JSON number casts. A string casts
    only when it is a plain decimal, so no cast error can reach the route."""
    return (
        f"(CASE WHEN jsonb_typeof({column} -> :{p}) = 'number' "
        f"THEN CAST({column} ->> :{p} AS numeric) "
        f"WHEN jsonb_typeof({column} -> :{p}) = 'string' "
        f"AND {column} ->> :{p} ~ " + _NUMBER_TEXT + " "
        f"THEN CAST({column} ->> :{p} AS numeric) END)"
    )


def _date(column: str, p: str) -> str:
    """The calendar date at the start of the field value, or NULL.

    A value that is no real date (``2026-02-30``) is NULL, never an error,
    so one bad row cannot break the read of every row. The check runs on
    every Postgres version: ``pg_input_is_valid`` needs 16. Nested CASEs keep
    the order, because Postgres may evaluate the arms of an AND in any order.
    The inner CASE compares the day with the last day of its month.
    """
    v = f"({column} ->> :{p})"
    year = f"CAST(substr({v}, 1, 4) AS int)"
    month = f"CAST(substr({v}, 6, 2) AS int)"
    day = f"CAST(substr({v}, 9, 2) AS int)"
    last = (
        f"CAST(extract(day FROM make_date({year}, {month}, 1) "
        "+ interval '1 month' - interval '1 day') AS int)"
    )
    return (
        f"(CASE WHEN {v} ~ " + _DATE_TEXT + " "
        f"THEN (CASE WHEN {day} <= {last} "
        f"THEN make_date({year}, {month}, {day}) END) END)"
    )


def _lookup_id(column: str, p: str) -> str:
    """A lookup's id when the value is ``{id, name}``, else the text."""
    return (
        f"(CASE WHEN jsonb_typeof({column} -> :{p}) = 'object' "
        f"THEN {column} -> :{p} ->> 'id' ELSE {column} ->> :{p} END)"
    )


def measure_sql(scope: FieldScope, ref: FieldRef, measure: str) -> MeasureSql:
    """``measure`` of the field *ref*, with its sum and count of values."""
    number = _number(scope.column, _MEASURE_KEY)
    return MeasureSql(
        value=FIELD_MEASURES[measure].format(n=number),
        sum=f"SUM({number})",
        count=f"COUNT({number})",
        params={_MEASURE_KEY: ref.key},
    )


def group_sql(scope: FieldScope, ref: FieldRef, bucket: str | None) -> GroupSql:
    """The group key of the field *ref*. A date groups by its bucket."""
    column, p = scope.column, _GROUP_KEY
    if ref.type in DATE_TYPES:
        unit, fmt = BUCKETS[bucket or ""]
        key = (
            f"to_char(date_trunc('{unit}', CAST({_date(column, p)} AS timestamp)), "
            f"'{fmt}')"
        )
        return GroupSql(key=key, label="NULL", params={p: ref.key})
    if ref.type == "lookup":
        return GroupSql(
            key=_lookup_id(column, p),
            label=(
                f"max(CASE WHEN jsonb_typeof({column} -> :{p}) = 'object' "
                f"THEN {column} -> :{p} ->> 'name' END)"
            ),
            params={p: ref.key},
        )
    return GroupSql(key=f"{column} ->> :{p}", label="NULL", params={p: ref.key})


def field_pairs(query_params: Any) -> list[tuple[str, str]]:
    """Every ``field.<name>=<value>`` pair of a query string, in order."""
    if query_params is None:
        return []
    return [
        (key[len(FIELD_PREFIX):], value)
        for key, value in query_params.multi_items()
        if key.startswith(FIELD_PREFIX) and key != FIELD_PREFIX
    ]


def _iso_date(name: str, value: str) -> date:
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        raise _refuse(f"'field.{name}' must be an ISO date (YYYY-MM-DD).") from None


def _split(scope: FieldScope, name: str) -> tuple[FieldRef, str | None]:
    """The field and the range end of one filter name."""
    if name not in scope.fields:
        for end in RANGE_ENDS:
            if name.endswith(end) and name[: -len(end)] in scope.fields:
                return scope.fields[name[: -len(end)]], end
    return scope.ref(name), None


def _filter(column: str, ref: FieldRef, end: str | None, k: str, v: str,
            value: str) -> tuple[str, Any]:
    """One filter clause and its bound value."""
    if end is not None:
        if ref.type not in DATE_TYPES:
            raise _refuse(
                f"'{ref.key}' is a {ref.type} field. A range takes a date field.",
            )
        return (
            f"{_date(column, k)} {RANGE_ENDS[end]} CAST(:{v} AS date)",
            _iso_date(ref.key + end, value),
        )
    if ref.type in DATE_TYPES:
        return f"left({column} ->> :{k}, 10) = :{v}", _iso_date(ref.key, value).isoformat()
    if ref.type in MEASURE_TYPES:
        try:
            number = Decimal(value.strip())
        except InvalidOperation:
            raise _refuse(f"'field.{ref.key}' must be a number.") from None
        if (not number.is_finite()
                or len(number.as_tuple().digits) > MAX_DIGITS
                or abs(number.adjusted()) > MAX_EXPONENT):
            raise _refuse(
                f"'field.{ref.key}' must be a number of at most {MAX_DIGITS} "
                f"digits, below 10^{MAX_EXPONENT}.",
            )
        return f"{_number(column, k)} = CAST(:{v} AS numeric)", str(number)
    if ref.type == "boolean":
        word = value.strip().lower()
        if word not in ("true", "false"):
            raise _refuse(f"'field.{ref.key}' must be true or false.")
        return f"{column} ->> :{k} = :{v}", word
    if ref.type == "lookup":
        return (
            f"({_lookup_id(column, k)} = :{v} OR {column} -> :{k} ->> 'name' = :{v})",
            value,
        )
    # A picklist, a text and each other type match the text. A multiselect
    # holds a JSON array, and the array arm matches one of its options.
    return (
        f"({column} ->> :{k} = :{v} OR (jsonb_typeof({column} -> :{k}) = 'array' "
        f"AND jsonb_exists({column} -> :{k}, :{v})))",
        value,
    )


def module_where(
    scope: FieldScope, module: str, pairs: list[tuple[str, str]],
) -> WhereSql:
    """The WHERE clause of one module: the module, and each field filter.

    Each name is checked against *scope* first, so an unknown name is a 422
    before any statement runs.
    """
    clauses = [f"module = :{_MODULE}"]
    params: dict[str, Any] = {_MODULE: module}
    for index, (name, value) in enumerate(pairs):
        ref, end = _split(scope, name)
        k, v = f"agf_k{index}", f"agf_v{index}"
        clause, bound = _filter(scope.column, ref, end, k, v, value)
        clauses.append(clause)
        params[k] = ref.key
        params[v] = bound
    return WhereSql(sql=" WHERE " + " AND ".join(clauses), params=params)


__all__ = [
    "BUCKETS", "DATE_TYPES", "FIELD_MEASURES", "FIELD_PREFIX", "MEASURE_TYPES",
    "VALUE_GROUP_TYPES", "FieldRef", "FieldScope", "GroupSql", "MeasureSql",
    "WhereSql", "check_group", "check_measure", "core_scope", "field_pairs",
    "group_sql", "measure_sql", "module_scope", "module_where",
]
