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

**The key words of a module** (CRM-Q31-2, ``crm_platform.md`` §18.13.4). A
module scope also takes ``@amount``, ``@date``, ``@status`` and ``@parent``.
Each is an alias of the real field that the catalogue names for the module
(``routes/crm/catalogue.entry_of``), and only when its type fits. A Zoho API
name cannot start with ``@``, so a key word never hides a real field. The
SQL binds the real key, so a key word never reaches the SQL text. A key word
with no key field is a 422 ``no_key_field``. On a module, ``sum_amount`` is
the ``sum`` of ``@amount`` (:func:`module_measure`). This module and the
catalogue hold every alias rule, and ``aggregate.py`` holds none.

**The overlay of an empty core slot** (CRM-Q20-3a, §18.13.5). With an
approved ``role``, a total over a core entity reads the records of the
module. :func:`overlay_where` builds its WHERE clause, and the ``overlay_*``
functions pick the field of each word of the core entity, or give the 422
that the projection table of §18.13.5 names. The SQL stays here, so
``aggregate.py`` still holds no jsonb operator.

**One zone for each month** (CRM-Q25b). :func:`crm_zone` gives the CRM zone,
the zone of the Zoho org, or UTC. A ``datetime`` field and the fixed month
groups of ``aggregate.py`` take their month on that clock, through
:func:`zoned` and the bound :data:`ZONE_PARAM`. A ``date`` keeps its day.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import HTTPException
from gateway.routes.crm.fields import custom_defs
from gateway.routes.crm.modules import _field_defs
from sqlalchemy import text

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

#: A whole datetime with its offset, as Zoho writes it:
#: ``2026-11-01T00:30:00+05:30``. Each part is in range, so the cast in
#: :func:`_instant` cannot fail on a value that passes. The day is checked
#: against its month by :func:`_date`. Offsets stop at 14 hours.
_DATETIME_TEXT = (
    r"'^[1-9][0-9]{3}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])"
    r"[T ]([01][0-9]|2[0-3]):[0-5][0-9](:[0-5][0-9](\.[0-9]{1,9})?)?"
    r"(Z|[+-](0[0-9]|1[0-4])(:?[0-5][0-9])?)$'"
)

#: The CRM zone when no profile names a real one (CRM-Q25b).
DEFAULT_ZONE = "UTC"

#: The bound name of the CRM zone. Every month of one answer reads it, and
#: so does each date range of ``core.extra_filters``. It is the one name of
#: the zone in a statement, so a list filter and a bucket bind one value.
ZONE_PARAM = "agf_zone"

#: The bounds of a number in a filter. ``1e1000000`` is finite, and Postgres
#: still refuses it, so a larger number is a 422 before any statement.
MAX_DIGITS = 60
MAX_EXPONENT = 100

#: CRM-Q31-2: each key word of a module scope → its key part of the
#: catalogue (``crm_sync.catalogue.KEY_PARTS``).
KEY_WORDS: dict[str, str] = {
    "@amount": "amount", "@date": "date", "@status": "status", "@parent": "parent",
}
AMOUNT_WORD = "@amount"

#: CRM-Q31-2 item 4: a measure of a module that reads a key word, and the
#: field measure that it is.
MODULE_MEASURES: dict[str, tuple[str, str]] = {"sum_amount": ("sum", AMOUNT_WORD)}

#: The parameter names of this module. A list filter uses no name with the
#: prefix, except :data:`ZONE_PARAM`, which holds the same zone.
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
        if found is None and (key or "") in KEY_WORDS:
            raise HTTPException(status_code=422, detail={
                "code": "no_key_field",
                "message": f"{self.owner} has no key field for {key}. "
                           "crm_list_modules names the key fields of each module.",
            })
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
    """The fields of a mirrored module, and its key words (CRM-Q31-2). The
    caller has checked the module against the mirrored set of the profile
    (``modules._require_module``).

    The catalogue entry comes from ``catalogue.entry_of``, the ONE reader of
    one entry, for the bound tenant. Outside ``CRM_CATALOGUE_ORGS`` it gives
    no entry, and the scope has no key word.
    """
    from gateway.routes.crm.catalogue import entry_of
    from gateway.routes.crm.core import current_tenant

    defs = await _field_defs(db, module)
    fields = {d.key: _ref(d) for d in defs}
    fields.update(key_words(fields, await entry_of(db, current_tenant(), module)))
    return FieldScope(column=MODULE_COLUMN, owner=f"module:{module}", fields=fields)


def key_words(fields: dict[str, FieldRef], entry: Any) -> dict[str, FieldRef]:
    """Each key word of *entry* (a ``crm_sync.catalogue.Entry``, or ``None``)
    → the real field it names. A key word joins only when its field is in
    *fields* and its type fits the part (``catalogue.PART_TYPES``): a measure
    type for ``@amount``, a date type for ``@date``, a select for
    ``@status`` and a lookup for ``@parent``."""
    from gateway.crm_sync.catalogue import PART_TYPES

    parts = getattr(entry, "fields", None) or {}
    out: dict[str, FieldRef] = {}
    for word, part in KEY_WORDS.items():
        named = parts.get(part)
        ref = fields.get(named.key) if named is not None else None
        if ref is not None and ref.type in PART_TYPES[part]:
            out[word] = ref
    return out


def module_measure(measure: str, field: str | None) -> tuple[str, str | None]:
    """The field measure and the field of a module aggregate (CRM-Q31-2
    item 4). ``sum_amount`` is the ``sum`` of ``@amount``. Each other measure
    stays as the call gives it."""
    return MODULE_MEASURES.get(measure, (measure, field))


def used_keys(scope: FieldScope, names: list[str | None]) -> dict[str, str]:
    """Each key word that *names* use, with the real key it read (CRM-Q31-2
    item 5). A filter name may end in ``.from`` or ``.to``."""
    out: dict[str, str] = {}
    for name in names:
        word = name or ""
        for end in RANGE_ENDS:
            if word.endswith(end) and word[: -len(end)] in KEY_WORDS:
                word = word[: -len(end)]
        if word in KEY_WORDS and word in scope.fields:
            out[word] = scope.fields[word].key
    return out


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


def _instant(column: str, p: str) -> str:
    """The field value as a ``timestamptz`` with its own offset, or NULL
    (CRM-Q25b). A value that is not a whole datetime with an offset, or not
    a real day, is NULL, never an error. Nested CASEs keep the order."""
    v = f"({column} ->> :{p})"
    return (
        f"(CASE WHEN {v} ~ " + _DATETIME_TEXT + " "
        f"THEN (CASE WHEN {_date(column, p)} IS NOT NULL "
        f"THEN CAST({v} AS timestamptz) END) END)"
    )


def zoned(instant_sql: str) -> str:
    """*instant_sql*, a ``timestamptz``, as the time on the clock of the CRM
    zone (CRM-Q25b). The zone is the bound :data:`ZONE_PARAM`, never text."""
    return f"({instant_sql} AT TIME ZONE CAST(:{ZONE_PARAM} AS text))"


def day_start(day_sql: str) -> str:
    """The first instant of the day *day_sql* on the clock of the CRM zone,
    as a ``timestamptz``. The zone is the bound :data:`ZONE_PARAM`.

    ``core.extra_filters`` compares a ``timestamptz`` column with it, so a
    day that a member picks in the CRM is a day in the CRM zone, as each
    month bucket is (CRM-Q25b fix round). The column stays bare, so an
    index on it still serves.
    """
    return f"(CAST({day_sql} AS timestamp) AT TIME ZONE CAST(:{ZONE_PARAM} AS text))"


def _calendar_day(column: str, ref: FieldRef, p: str) -> str:
    """The calendar day of a date or a datetime field. A ``date`` keeps the
    day that Zoho wrote, because a date has no time. A ``datetime`` takes
    its day in the CRM zone (CRM-Q25b)."""
    if ref.type == "datetime":
        return f"CAST({zoned(_instant(column, p))} AS date)"
    return _date(column, p)


async def crm_zone(db: Any, org: Any) -> str:
    """The CRM zone of *org*: the IANA zone of its Zoho org (WS-53 CRM-Q25b).

    Metorite keeps no zone for an org. The newest source profile keeps the
    zone of the Zoho org at ``profile -> 'org' ->> 'time_zone'``
    (``mapping._org_summary``). A Native CRM, an org with no profile, a
    profile with no zone and a name that is not an IANA zone give
    :data:`DEFAULT_ZONE`. ``personal.zone_name`` is the one rule of a zone
    name. Each bucket and each datetime range of one answer uses this zone.
    """
    from gateway.crm_sync import profiles
    from gateway.routes.crm.core import is_mirror_mode
    from gateway.routes.projects.personal import zone_name

    if not org or not await is_mirror_mode(db, str(org)):
        return DEFAULT_ZONE
    latest = await profiles.latest(db, str(org))
    summary = latest.profile.get("org") if latest is not None else None
    zone = zone_name(summary.get("time_zone") if isinstance(summary, dict) else None)
    # Python's zone database and the one of Postgres can differ. A name that
    # only Python knows made `AT TIME ZONE` fail, and the route answered 500.
    return zone if await _postgres_knows(db, zone) else DEFAULT_ZONE


#: Each zone name that this process has asked Postgres about, and the
#: answer. The names come from ``personal.zone_name``, so only real IANA
#: names reach it, and the map stays at a few hundred entries at most. It
#: holds no tenant data.
_PG_ZONES: dict[str, bool] = {}

_PG_ZONE_SQL = "SELECT EXISTS (SELECT 1 FROM pg_timezone_names WHERE name = :zone)"


async def _postgres_knows(db: Any, zone: str) -> bool:
    """True when Postgres takes *zone* in ``AT TIME ZONE`` (CRM-Q25b fix
    round). The answer is cached, because ``pg_timezone_names`` reads the
    zone files of the server on each call."""
    if zone == DEFAULT_ZONE:
        return True
    known = _PG_ZONES.get(zone)
    if known is None:
        known = bool((await db.execute(text(_PG_ZONE_SQL), {"zone": zone})).scalar())
        _PG_ZONES[zone] = known
    return known


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


def group_sql(
    scope: FieldScope, ref: FieldRef, bucket: str | None, *, zone: str,
) -> GroupSql:
    """The group key of the field *ref*. A date groups by its bucket.

    A ``date`` keeps the day that Zoho wrote. A ``datetime`` reads the whole
    value with its offset, and takes its bucket on the clock of *zone*, the
    CRM zone (CRM-Q25b). So it agrees with ``created_month``.
    """
    column, p = scope.column, _GROUP_KEY
    if ref.type in DATE_TYPES:
        unit, fmt = BUCKETS[bucket or ""]
        when = (
            zoned(_instant(column, p)) if ref.type == "datetime"
            else f"CAST({_date(column, p)} AS timestamp)"
        )
        key = f"to_char(date_trunc('{unit}', {when}), '{fmt}')"
        return GroupSql(key=key, label="NULL", params={p: ref.key, ZONE_PARAM: zone})
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
            base = name[: -len(end)]
            if name.endswith(end) and (base in scope.fields or base in KEY_WORDS):
                # A key word with no key field is the 422 `no_key_field`.
                return scope.ref(base), end
    return scope.ref(name), None


def _filter(column: str, ref: FieldRef, end: str | None, k: str, v: str,
            value: str) -> tuple[str, Any]:
    """One filter clause and its bound value."""
    if end is not None:
        if ref.type not in DATE_TYPES:
            raise _refuse(
                f"'{ref.key}' is a {ref.type} field. A range takes a date field.",
            )
        # CRM-Q25b: a datetime takes its day in the CRM zone, as its bucket.
        return (
            f"{_calendar_day(column, ref, k)} {RANGE_ENDS[end]} CAST(:{v} AS date)",
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
    scope: FieldScope, module: str, pairs: list[tuple[str, str]], *, zone: str,
) -> WhereSql:
    """The WHERE clause of one module: the module, and each field filter.

    Each name is checked against *scope* first, so an unknown name is a 422
    before any statement runs. A range on a datetime field reads *zone*,
    the CRM zone (CRM-Q25b).
    """
    clauses = [f"module = :{_MODULE}"]
    params: dict[str, Any] = {_MODULE: module, ZONE_PARAM: zone}
    for index, (name, value) in enumerate(pairs):
        ref, end = _split(scope, name)
        k, v = f"agf_k{index}", f"agf_v{index}"
        clause, bound = _filter(scope.column, ref, end, k, v, value)
        clauses.append(clause)
        params[k] = ref.key
        params[v] = bound
    return WhereSql(sql=" WHERE " + " AND ".join(clauses), params=params)


# ── The overlay of an empty core slot (CRM-Q20-3a, §18.13.5) ────────────────

#: The list filters of a core entity that an overlay does not take. Each
#: gives 422 ``overlay_filter``, never a silent answer of the whole module.
OVERLAY_REFUSED: tuple[str, ...] = (
    "status_id", "custom", "pipeline_id", "company_id", "contact_id", "source",
    "created_from", "created_to", "include_converted",
)

#: The bound names of the overlay clauses.
_OWNER = "agf_owner"
_Q = "agf_q"
_UPDATED_FROM = "agf_uf"
_UPDATED_TO = "agf_ut"


def overlay_refusal(code: str, message: str) -> HTTPException:
    """A 422 of the projection table: ``overlay_no_amount``,
    ``overlay_no_probability``, ``overlay_no_stage``, ``overlay_no_date``,
    ``overlay_unfit`` or ``overlay_filter``."""
    return HTTPException(status_code=422, detail={"code": code, "message": message})


def _target_ref(scope: FieldScope, overlay: Any, target: str,
                types: tuple[str, ...]) -> FieldRef | None:
    """The field of the ``target`` item *target*, when the module has it with
    a type of *types*."""
    keys = (getattr(overlay, "targets", None) or {}).get(target) or []
    ref = scope.fields.get(keys[0]) if keys else None
    return ref if ref is not None and ref.type in types else None


def _from(overlay: Any) -> str:
    return f"This slot comes from the module {getattr(overlay, 'module', '')}."


def overlay_amount(scope: FieldScope, overlay: Any) -> FieldRef:
    """``sum_amount``: the ``amount`` target, else the catalogue amount."""
    ref = _target_ref(scope, overlay, "amount", MEASURE_TYPES) or scope.fields.get(AMOUNT_WORD)
    if ref is None:
        raise overlay_refusal("overlay_no_amount", f"{_from(overlay)} It has no amount. "
                              "crm_list_modules names its key fields.")
    return ref


def overlay_probability(scope: FieldScope, overlay: Any) -> FieldRef:
    """``weighted``: the ``probability`` target."""
    ref = _target_ref(scope, overlay, "probability", MEASURE_TYPES)
    if ref is None:
        raise overlay_refusal("overlay_no_probability",
                              f"{_from(overlay)} It has no probability, so no weighted total.")
    return ref


def overlay_stage(scope: FieldScope, overlay: Any) -> FieldRef:
    """``group_by=status`` and ``weighted``: the field of the ``stage`` item."""
    ref = scope.fields.get(getattr(overlay, "stage", None) or "")
    if ref is None:
        raise overlay_refusal("overlay_no_stage",
                              f"{_from(overlay)} No stage field is approved for it.")
    return ref


def overlay_close(scope: FieldScope, overlay: Any) -> FieldRef:
    """``close_month`` and the ``close_*`` filters: the
    ``expected_close_date`` target, else the catalogue date."""
    ref = (_target_ref(scope, overlay, "expected_close_date", DATE_TYPES)
           or scope.fields.get("@date"))
    if ref is None or ref.type not in DATE_TYPES:
        raise overlay_refusal("overlay_no_date", f"{_from(overlay)} It has no close date.")
    return ref


def overlay_filters(label: str, overlay: Any, params: Any) -> None:
    """A 422 ``overlay_filter`` for each list filter of *params* (a
    ``records.ListParams``) that the overlay does not take: one sentence that
    names the module and the call to use."""
    given = [name for name in OVERLAY_REFUSED if getattr(params, name, None)]
    if given:
        module = getattr(overlay, "module", "")
        raise overlay_refusal(
            "overlay_filter",
            f"{label}s come from the module {module}, and a total of them takes no "
            f"{', '.join(given)} filter. Call crm_aggregate with entity "
            f"'module:{module}' and field_filters, or group by status.",
        )


def overlay_where(
    scope: FieldScope, overlay: Any, pairs: list[tuple[str, str]], *, zone: str,
    owner: str | None, params: Any,
) -> WhereSql:
    """The WHERE clause of an overlaid core entity (§18.13.5): the module,
    each ``field.<key>`` filter, the owner (``owner_email``), ``q`` on the
    name, the updated range on ``coalesce(ext_modified_at, updated_at)`` in
    the CRM zone, and the close range on the close date of
    :func:`overlay_close`. *params* is the ``records.ListParams`` of the
    call, and *owner* its resolved owner. Each value is bound."""
    q = getattr(params, "q", None)
    updated_from = getattr(params, "updated_from", None)
    updated_to = getattr(params, "updated_to", None)
    close_from = getattr(params, "close_from", None)
    close_to = getattr(params, "close_to", None)
    pairs = list(pairs)
    if close_from or close_to:
        close = overlay_close(scope, overlay)
        if close_from:
            pairs.append((f"{close.key}.from", close_from))
        if close_to:
            pairs.append((f"{close.key}.to", close_to))
    where = module_where(scope, str(overlay.module), pairs, zone=zone)
    clauses = [where.sql]
    params = dict(where.params)
    if owner:
        clauses.append(f"lower(owner_email) = :{_OWNER}")
        params[_OWNER] = owner.strip().lower()
    if q and q.strip():
        clauses.append(f"name ILIKE :{_Q}")
        params[_Q] = f"%{q.strip()}%"
    if updated_from:
        clauses.append("coalesce(ext_modified_at, updated_at) >= "
                       + day_start(f"CAST(:{_UPDATED_FROM} AS date)"))
        params[_UPDATED_FROM] = _iso_date("updated_from", updated_from)
    if updated_to:
        clauses.append("coalesce(ext_modified_at, updated_at) < "
                       + day_start(f"CAST(:{_UPDATED_TO} AS date) + 1"))
        params[_UPDATED_TO] = _iso_date("updated_to", updated_to)
    return WhereSql(sql=" AND ".join(clauses), params=params)


def overlay_weighted(
    scope: FieldScope, amount: FieldRef, probability: FieldRef, stage: FieldRef,
    open_values: list[str],
) -> MeasureSql:
    """``weighted`` on an overlay: the amount times the probability over
    100, for the records whose stage value maps to ``open``. Each key and
    each value is bound."""
    column = scope.column
    a, w = _number(column, "agf_wa"), _number(column, "agf_wp")
    weight = (f"(CASE WHEN {column} ->> :agf_ws = ANY(CAST(:agf_open AS text[])) "
              f"THEN {a} * {w} / 100.0 END)")
    return MeasureSql(
        value=f"COALESCE(SUM({weight}), 0)", sum=f"SUM({weight})", count=f"COUNT({weight})",
        params={"agf_wa": amount.key, "agf_wp": probability.key, "agf_ws": stage.key,
                "agf_open": list(open_values)},
    )


__all__ = [
    "AMOUNT_WORD",
    "BUCKETS",
    "DATE_TYPES",
    "DEFAULT_ZONE",
    "FIELD_MEASURES",
    "FIELD_PREFIX",
    "KEY_WORDS",
    "MEASURE_TYPES",
    "MODULE_MEASURES",
    "OVERLAY_REFUSED",
    "VALUE_GROUP_TYPES",
    "ZONE_PARAM",
    "FieldRef",
    "FieldScope",
    "GroupSql",
    "MeasureSql",
    "WhereSql",
    "check_group",
    "check_measure",
    "core_scope",
    "crm_zone",
    "day_start",
    "field_pairs",
    "group_sql",
    "key_words",
    "measure_sql",
    "module_measure",
    "module_scope",
    "module_where",
    "overlay_amount",
    "overlay_close",
    "overlay_filters",
    "overlay_probability",
    "overlay_refusal",
    "overlay_stage",
    "overlay_weighted",
    "overlay_where",
    "used_keys",
    "zoned",
]
