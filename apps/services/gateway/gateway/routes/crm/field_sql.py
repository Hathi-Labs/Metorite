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
#: The prefix of the ``owner`` of the scope of a module (``module:<api_name>``).
MODULE_OWNER = "module:"
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

#: The most field keys that one 422 names. A model reads the list and picks
#: one, and a module can hold a few hundred fields.
CHOICES_MAX = 20

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
    of each picklist value.

    CRM-Q32-9: ``@amount`` can be the sum of 2 to 5 currency fields. Its
    ``parts`` hold each field, its key is the keys joined with ``+``, its
    label the labels joined with " + ", and its type ``currency``. Only
    :func:`_amount` reads the parts, and a sum takes no group
    (:func:`check_group`)."""

    key: str
    type: str
    label: str
    options: dict[str, str] = field(default_factory=dict)
    parts: tuple[FieldRef, ...] = ()


@dataclass(frozen=True)
class FieldScope:
    """The fields that one aggregate may name, and the jsonb column they
    live in. ``owner`` is what a 422 names: ``deals`` or ``module:Products``."""

    column: str
    owner: str
    fields: dict[str, FieldRef]
    #: True when the catalogue serves the org (``CRM_CATALOGUE_ORGS``). An
    #: org outside the list keeps the refusals of before the key words, and
    #: no message points it at key fields it cannot see (fix round, item 9).
    catalogue: bool = True
    #: CRM-Q32-6: the key parts of the module that are an open question
    #: (``questions.open_parts``). Empty outside ``CRM_QUESTIONS_ORGS``.
    questions: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if self.column not in _COLUMNS:
            raise ValueError(f"field_sql: {self.column!r} is not a field column")

    def choices(self, types: tuple[str, ...] | None = None) -> str:
        """The keys of the fields of *types* (every field when None), sorted,
        at most :data:`CHOICES_MAX` of them, as one phrase for a 422. A key
        word (``@amount``) is an alias of a real field, so it is left out."""
        keys = sorted(
            k for k, ref in self.fields.items()
            if k not in KEY_WORDS and (types is None or ref.type in types)
        )
        if not keys:
            return "none"
        more = len(keys) - CHOICES_MAX
        shown = ", ".join(keys[:CHOICES_MAX])
        return f"{shown}, and {more} more" if more > 0 else shown

    def filter_word(self, key: str) -> str:
        """The query word that filters on *key*: ``custom.<key>`` for a core
        entity (the list filters of ``records.ListParams``) and
        ``field.<key>`` for a module (:func:`module_where`)."""
        return f"custom.{key}" if self.column == CORE_COLUMN else f"{FIELD_PREFIX}{key}"

    def ref(self, key: str | None) -> FieldRef:
        """The field named *key*, or a 422 that names the legal keys."""
        found = self.fields.get(key or "")
        if found is None and key == AMOUNT_WORD and not self.catalogue:
            # The refusal of before CRM-Q31-2: `sum_amount` on a module.
            raise HTTPException(status_code=422,
                                detail="'sum_amount' needs deals. A module has no amount.")
        if found is None and (key or "") in KEY_WORDS and self.catalogue:
            raise HTTPException(status_code=422, detail=self._no_key_field(str(key)))
        if found is None:
            raise HTTPException(status_code=422, detail={
                "code": "unknown_field",
                "message": f"'{key}' is not a field of {self.owner}. "
                           f"Its fields are: {self.choices()}. "
                           "crm_list_fields gives the type of each.",
            })
        return found

    def _no_key_field(self, key: str) -> dict[str, Any]:
        """The detail of the 422 ``no_key_field``. When the part is an open
        question (CRM-Q32-6), it names where an admin answers it, and
        ``href`` links there (``sources.crm_settings_href``)."""
        detail: dict[str, Any] = {
            "code": "no_key_field",
            "message": f"{self.owner} has no key field for {key}. "
                       "crm_list_modules names the key fields of each module.",
        }
        part = KEY_WORDS.get(key)
        if part in self.questions:
            from gateway.crm_sync.questions import key_id
            from gateway.retrieval.sources import crm_settings_href

            detail["message"] += " A CRM admin can answer this in CRM settings, Open questions."
            detail["href"] = crm_settings_href("questions")
            # CRM-Q32-8: the id of the open question, so the chat can ask it.
            if self.owner.startswith(MODULE_OWNER):
                detail["question"] = key_id(self.owner[len(MODULE_OWNER):], str(part))
        return detail


@dataclass(frozen=True)
class MeasureSql:
    """The aggregate of a field measure. ``sum`` and ``count`` give the
    total of an ``avg`` across groups."""

    value: str
    sum: str
    count: str
    params: dict[str, Any]
    #: CRM-Q32-9a fix round: the FROM text that the statement adds after the
    #: table (:data:`DETOAST_JOIN`), or empty. Only a sum of fields has one.
    source: str = ""


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
    #: The FROM text of a filter on a sum of fields (:data:`DETOAST_JOIN`).
    source: str = ""


#: CRM-Q32-9a fix round: the detoasted jsonb of a module record. Each
#: ``fields -> :key`` on the stored row reads the whole TOASTed value again,
#: and a sum of 5 parts reads it 40 times a row (7.1 s over 30,000 rows of
#: 2.5 KB, measured). The LATERAL reads it ONCE for each row that passed
#: the WHERE clause, and ``OFFSET 0`` stops the planner from folding it back
#: in. The parts of a sum then read the copy in memory. A plain field keeps
#: its SQL text, so the statement of an org with no sum does not change.
DETOAST_ALIAS = "agf_d"
DETOAST_COLUMN = f"{DETOAST_ALIAS}.agf_fields"
DETOAST_JOIN = (
    f" CROSS JOIN LATERAL (SELECT {MODULE_COLUMN} || CAST('{{}}' AS jsonb) "
    f"AS agf_fields OFFSET 0) {DETOAST_ALIAS}"
)


def from_source(*sources: str) -> str:
    """The FROM text that a statement adds after its table: the one
    :data:`DETOAST_JOIN` when any part of the statement reads a sum, else
    empty. A statement never joins it twice."""
    return DETOAST_JOIN if any(sources) else ""


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
    from gateway.crm_sync.catalogue import MONEY_PURPOSES
    from gateway.routes.crm.catalogue import entry_of
    from gateway.routes.crm.core import current_tenant
    from gateway.routes.crm.flags import catalogue_allowed
    from gateway.routes.crm.questions import open_parts

    org = current_tenant()
    defs = await _field_defs(db, module)
    fields = {d.key: _ref(d) for d in defs}
    entry = await entry_of(db, org, module)
    fields.update(key_words(fields, entry))
    # CRM-Q32-6: the open questions of a money module, read only when a key
    # word has no field, and only for an org on CRM_QUESTIONS_ORGS. Only a
    # money entry has key parts, so no other module asks.
    missing = (getattr(entry, "purpose", None) in MONEY_PURPOSES
               and any(word not in fields for word in KEY_WORDS))
    questions = await open_parts(db, org, module) if missing else frozenset()
    return FieldScope(column=MODULE_COLUMN, owner=f"module:{module}", fields=fields,
                      catalogue=catalogue_allowed(org), questions=questions)


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
        if named is not None and getattr(named, "parts", ()):
            summed = _sum_ref(fields, named)
            if summed is not None and word == AMOUNT_WORD:
                out[word] = summed
            continue
        ref = fields.get(named.key) if named is not None else None
        if ref is not None and ref.type in PART_TYPES[part]:
            out[word] = ref
    return out


def _sum_ref(fields: dict[str, FieldRef], named: Any) -> FieldRef | None:
    """The one ``FieldRef`` of a sum (CRM-Q32-9), or ``None`` when a part is
    not a currency field of *fields*. The catalogue checked each key, and
    this checks them again against the fields of the scope."""
    from gateway.crm_sync.catalogue import SUM_LABEL_JOIN, SUM_TYPES

    refs = [fields.get(getattr(p, "key", "")) for p in named.parts]
    if not refs or any(r is None or r.type not in SUM_TYPES for r in refs):
        return None
    found = [r for r in refs if r is not None]
    return FieldRef(key=str(named.key), type=SUM_TYPES[0],
                    label=SUM_LABEL_JOIN.join(r.label for r in found), parts=tuple(found))


def module_measure(measure: str, field: str | None) -> tuple[str, str | None]:
    """The field measure and the field of a module aggregate (CRM-Q31-2
    item 4). ``sum_amount`` is the ``sum`` of ``@amount``. Each other measure
    stays as the call gives it."""
    return MODULE_MEASURES.get(measure, (measure, field))


def used_keys(scope: FieldScope, names: list[str | None]) -> dict[str, str | list[str]]:
    """Each key word that *names* use, with the real key it read (CRM-Q31-2
    item 5). A filter name may end in ``.from`` or ``.to``. A sum
    (CRM-Q32-9) gives the list of its keys."""
    out: dict[str, str | list[str]] = {}
    for name in names:
        word = name or ""
        for end in RANGE_ENDS:
            if word.endswith(end) and word[: -len(end)] in KEY_WORDS:
                word = word[: -len(end)]
        if word in KEY_WORDS and word in scope.fields:
            ref = scope.fields[word]
            out[word] = [p.key for p in ref.parts] if ref.parts else ref.key
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
            f"type {_type_words(MEASURE_TYPES)}. The number fields of "
            f"{scope.owner} are: {scope.choices(MEASURE_TYPES)}.",
        )
    return ref


def check_group(scope: FieldScope, key: str | None, bucket: str | None) -> FieldRef:
    """The field of ``group_field``, or a 422. A date needs a bucket, and a
    value group takes none."""
    ref = scope.ref(key)
    if ref.parts:
        # CRM-Q32-9: a sum has no single value to group by.
        raise HTTPException(status_code=422, detail={
            "code": "composite_group",
            "message": f"'{key}' is the sum of {len(ref.parts)} fields, so it has no "
                       "single value to group by. Group by another field, or filter on it.",
        })
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
            f"{_type_words(VALUE_GROUP_TYPES)}, or a date with a bucket. The "
            f"fields of {scope.owner} that can group are: "
            f"{scope.choices(VALUE_GROUP_TYPES + DATE_TYPES)}. To count the "
            f"records where '{ref.key}' has one value, give no group and the "
            f"filter {scope.filter_word(ref.key)}=<value>.",
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


def _numbers(column: str, ref: FieldRef, prefix: str) -> list[tuple[str, dict[str, Any]]]:
    """Each part of *ref* as a guarded number, with its bound key. A plain
    field is one part. Each bound name is ``{prefix}_{i}``: a separator, so
    ``agf_k1`` and part ``0`` never give ``agf_k10``, the key of filter 10
    (CRM-Q32-9). No key reaches the SQL text."""
    parts = ref.parts or (ref,)
    out = []
    for i, part in enumerate(parts):
        p = f"{prefix}_{i}"
        out.append((_number(column, p), {p: part.key}))
    return out


def _amount(column: str, ref: FieldRef, prefix: str) -> tuple[str, dict[str, Any], str]:
    """THE builder of the number of a measure field, ``@amount`` with it
    (CRM-Q32-9). :func:`measure_sql`, the number filter of
    :func:`module_where` and :func:`overlay_weighted` call it, so
    ``sum_amount``, the overlay and the digest read a sum with no change of
    their own.

    A plain field is its guarded number. A sum of fields is NULL when no
    part is set, so ``COUNT`` does not count the record. Else it is the sum
    of the parts that are set, each NULL part taken as 0.

    Returns the SQL, the bound keys and the FROM text. A sum over the
    module column reads :data:`DETOAST_COLUMN`, so its FROM text is
    :data:`DETOAST_JOIN`. A plain field has no FROM text."""
    summed = bool(ref.parts) and column == MODULE_COLUMN
    numbers = _numbers(DETOAST_COLUMN if summed else column, ref, prefix)
    params: dict[str, Any] = {}
    for _sql, bound in numbers:
        params.update(bound)
    if len(numbers) == 1:
        return numbers[0][0], params, ""
    unset = " AND ".join(f"{n} IS NULL" for n, _b in numbers)
    total = " + ".join(f"COALESCE({n}, 0)" for n, _b in numbers)
    return (f"(CASE WHEN {unset} THEN NULL ELSE {total} END)", params,
            DETOAST_JOIN if summed else "")


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
    """``measure`` of the field *ref*, with its sum and count of values. A
    sum of fields (CRM-Q32-9) reads through :func:`_amount`."""
    number, params, source = _amount(scope.column, ref, _MEASURE_KEY)
    return MeasureSql(
        value=FIELD_MEASURES[measure].format(n=number),
        sum=f"SUM({number})",
        count=f"COUNT({number})",
        params=params,
        source=source,
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


def _filter(scope: FieldScope, ref: FieldRef, end: str | None, k: str, v: str,
            value: str) -> tuple[str, Any, dict[str, Any], str]:
    """One filter clause, its bound value, its bound key and its FROM text."""
    clause, bound = _filter_value(scope, ref, end, k, v, value)
    if ref.type in MEASURE_TYPES and end is None:
        number, keys, source = _amount(scope.column, ref, k)
        return f"{number} = {clause}", bound, keys, source
    return clause, bound, {k: ref.key}, ""


def _filter_value(scope: FieldScope, ref: FieldRef, end: str | None, k: str, v: str,
                  value: str) -> tuple[str, Any]:
    """One filter clause and its bound value. A number gives only the right
    side of its clause: :func:`_filter` puts the number of :func:`_amount`
    on the left."""
    column = scope.column
    if end is not None:
        if ref.type not in DATE_TYPES:
            raise _refuse(
                f"'{ref.key}' is a {ref.type} field. A range takes a date field. "
                f"The date fields of {scope.owner} are: {scope.choices(DATE_TYPES)}. "
                f"To match one value, give {scope.filter_word(ref.key)}=<value>.",
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
        return f"CAST(:{v} AS numeric)", str(number)
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
    sources: list[str] = []
    for index, (name, value) in enumerate(pairs):
        ref, end = _split(scope, name)
        k, v = f"agf_k{index}", f"agf_v{index}"
        clause, bound, keys, source = _filter(scope, ref, end, k, v, value)
        clauses.append(clause)
        params.update(keys)
        params[v] = bound
        sources.append(source)
    return WhereSql(sql=" WHERE " + " AND ".join(clauses), params=params,
                    source=from_source(*sources))


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
    return WhereSql(sql=" AND ".join(clauses), params=params, source=where.source)


def overlay_weighted(
    scope: FieldScope, amount: FieldRef, probability: FieldRef, stage: FieldRef,
    open_values: list[str],
) -> MeasureSql:
    """``weighted`` on an overlay: the amount times the probability over
    100, for the records whose stage value maps to ``open``. Each key and
    each value is bound. The amount reads through :func:`_amount`, so a sum
    of fields (CRM-Q32-9) weighs too."""
    column = scope.column
    a, a_keys, a_source = _amount(column, amount, "agf_wa")
    w, w_keys, w_source = _amount(column, probability, "agf_wp")
    weight = (f"(CASE WHEN {column} ->> :agf_ws = ANY(CAST(:agf_open AS text[])) "
              f"THEN {a} * {w} / 100.0 END)")
    return MeasureSql(
        value=f"COALESCE(SUM({weight}), 0)", sum=f"SUM({weight})", count=f"COUNT({weight})",
        params={**a_keys, **w_keys, "agf_ws": stage.key, "agf_open": list(open_values)},
        source=from_source(a_source, w_source),
    )


# ── The values of a stage field (CRM-Q32-1, §18.14.3) ───────────────────────

#: The distinct values of one field of a mirrored module, each with its
#: count of records, the most first. The org, the module and the key are
#: bound, so no name reaches the SQL text.
#: Fix round: each value is trimmed as ``mapping.text_value`` trims it, the
#: rule of the executor, so " Won " counts as "Won" and never shows as a value
#: with no entry. An object or a list is no value, as there.
_TRIMMED = f"btrim({MODULE_COLUMN} ->> :{_GROUP_KEY}, E' \\t\\r\\n\\f\\x0b')"
_VALUE_COUNTS_SQL = (
    f"SELECT {_TRIMMED} AS v, count(*) AS n "
    "FROM crm_module_records WHERE organization_id = CAST(:agf_org AS uuid) "
    f"AND module = :{_MODULE} AND ext_source = 'zoho' "
    f"AND jsonb_typeof({MODULE_COLUMN} -> :{_GROUP_KEY}) IN ('string', 'number') "
    f"AND coalesce({_TRIMMED}, '') <> '' "
    "GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT :agf_limit"
)


async def value_counts(
    db: Any, org: str, module: str, key: str, limit: int,
) -> list[tuple[str, int]]:
    """The distinct values of the field *key* of *module*, each with its
    count of records, the most first, at most *limit* (CRM-Q32-1, the
    ``stage_value`` read). ONE grouped statement, in the caller's tenant
    session, with the org in the predicate and RLS under it."""
    rows = (await db.execute(text(_VALUE_COUNTS_SQL), {
        "agf_org": str(org), _MODULE: module, _GROUP_KEY: key,
        "agf_limit": max(1, int(limit)),
    })).fetchall()
    return [(str(r.v), int(r.n)) for r in rows if r.v is not None]


# ── The check of a sum (CRM-Q32-9, §18.14.10) ───────────────────────────────

#: The sum check reads at most this many records of a module, by id.
SUM_CHECK_ROWS = 10_000

#: The prefix of the bound keys of the sum check.
_SUM_KEY = "agf_s"


@dataclass(frozen=True)
class SumCheck:
    """What ONE statement of the sum check counted, over the first
    :data:`SUM_CHECK_ROWS` records of a module. ``covered`` holds the
    records with any part set, ``overlap`` the records with 2 or more parts
    set. ``pairs`` gives ``(i, j, both, equal)`` for each pair of parts:
    the records that set both, and the records where both are equal."""

    records: int
    covered: int
    overlap: int
    pairs: tuple[tuple[int, int, int, int], ...]


def sum_check_sql(refs: tuple[FieldRef, ...]) -> tuple[str, dict[str, Any]]:
    """The ONE statement of the sum check, and its bound keys. Each part is
    the guarded number of :func:`_numbers`, so a part is set when its value
    is a number or a numeric string. The org, the module and the limit are
    bound by the caller. Each alias comes from an index, never from text.

    The innermost select reads each row once, detoasted, under its LIMIT, so
    the parts read a copy in memory (the rule of :data:`DETOAST_JOIN`). A
    pair of parts that are both 0 is no "same value": a TDS and a bank
    charge of 0 hold no money twice (fix round)."""
    numbers = _numbers("agf_fields", FieldRef(key="", type="currency", label="",
                                              parts=tuple(refs)), _SUM_KEY)
    params: dict[str, Any] = {}
    inner = []
    for i, (sql, bound) in enumerate(numbers):
        inner.append(f"{sql} AS n_{i}")
        params.update(bound)
    count = len(numbers)
    set_flags = " + ".join(f"(CASE WHEN n_{i} IS NOT NULL THEN 1 ELSE 0 END)"
                           for i in range(count))
    outer = [
        "count(*) AS records",
        f"count(*) FILTER (WHERE {set_flags} >= 1) AS covered",
        f"count(*) FILTER (WHERE {set_flags} >= 2) AS overlap",
    ]
    for i in range(count):
        for j in range(i + 1, count):
            outer.append(f"count(*) FILTER (WHERE n_{i} IS NOT NULL AND n_{j} IS NOT NULL) "
                         f"AS both_{i}_{j}")
            outer.append(f"count(*) FILTER (WHERE n_{i} = n_{j} AND n_{i} <> 0) "
                         f"AS equal_{i}_{j}")
    sql = (
        f"SELECT {', '.join(outer)} FROM (SELECT {', '.join(inner)} "
        f"FROM (SELECT {MODULE_COLUMN} || CAST('{{}}' AS jsonb) AS agf_fields "
        "FROM crm_module_records WHERE organization_id = CAST(:agf_org AS uuid) "
        f"AND module = :{_MODULE} ORDER BY id LIMIT :agf_limit) d) r"
    )
    return sql, params


async def sum_check(db: Any, org: str, module: str, refs: tuple[FieldRef, ...]) -> SumCheck:
    """The counts of a ticked set of parts (CRM-Q32-9): ONE bounded
    statement in the caller's tenant session, with the org in its
    predicate and RLS under it. The caller checked each part first."""
    sql, params = sum_check_sql(refs)
    row = (await db.execute(text(sql), {
        **params, "agf_org": str(org), _MODULE: module, "agf_limit": SUM_CHECK_ROWS,
    })).first()
    count = len(refs)
    pairs = tuple(
        (i, j, int(getattr(row, f"both_{i}_{j}", 0) or 0),
         int(getattr(row, f"equal_{i}_{j}", 0) or 0))
        for i in range(count) for j in range(i + 1, count)
    )
    return SumCheck(records=int(getattr(row, "records", 0) or 0),
                    covered=int(getattr(row, "covered", 0) or 0),
                    overlap=int(getattr(row, "overlap", 0) or 0), pairs=pairs)


__all__ = [
    "AMOUNT_WORD",
    "BUCKETS",
    "CHOICES_MAX",
    "DATE_TYPES",
    "DEFAULT_ZONE",
    "DETOAST_COLUMN",
    "DETOAST_JOIN",
    "FIELD_MEASURES",
    "FIELD_PREFIX",
    "KEY_WORDS",
    "MEASURE_TYPES",
    "MODULE_MEASURES",
    "OVERLAY_REFUSED",
    "SUM_CHECK_ROWS",
    "VALUE_GROUP_TYPES",
    "ZONE_PARAM",
    "FieldRef",
    "FieldScope",
    "GroupSql",
    "MeasureSql",
    "SumCheck",
    "WhereSql",
    "check_group",
    "check_measure",
    "core_scope",
    "crm_zone",
    "day_start",
    "field_pairs",
    "from_source",
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
    "sum_check",
    "sum_check_sql",
    "used_keys",
    "value_counts",
    "zoned",
]
