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

**Each module record is read ONCE** (task 3127). Postgres decompresses a
TOASTed jsonb value again for each ``fields -> :key`` on the stored row, and
an aggregate of a module reads up to 17 of them a row. So every expression
over a module record reads the one detoasted copy of :data:`DETOAST_JOIN`
(:func:`_reads`), and each builder gives that join as its FROM text. A core
entity keeps its ``custom`` column, which is small, and its SQL of before.

**One zone for each month** (CRM-Q25b). :func:`crm_zone` gives the CRM zone,
the zone of the Zoho org, or UTC. A ``datetime`` field and the fixed month
groups of ``aggregate.py`` take their month on that clock, through
:func:`zoned` and the bound :data:`ZONE_PARAM`. A ``date`` keeps its day.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import HTTPException
from gateway.routes.crm.fields import custom_defs
from gateway.routes.crm.modules import KNOWN_TARGETS, _field_defs
from sqlalchemy import text

#: The query-string prefix of a module field filter: ``field.<key>=<value>``,
#: and ``field.<key>.from`` / ``field.<key>.to`` for a date or a number range.
FIELD_PREFIX = "field."

#: The two ends of a range filter. Each end is inclusive.
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

#: CRM-Q34-11: the strict shape of a number in a TEXT field. Strict, never a
#: guess: a value is a number only when its whole shape is clearly one.
#:
#: * ASCII digits only, ``[0-9]``. Never ``\d``: it can take a digit of
#:   another script, and then the cast would fail the whole read.
#: * An optional minus, only as the first character, before any currency
#:   mark: ``-₹500`` and ``-Rs 500`` are -500. A minus after a mark
#:   (``Rs-5``, ``RS-485``, ``₹-500``) or before a space (``- 5``) is no
#:   number, because a code such as ``RS-232`` has that shape.
#: * No leading zero: ``0`` and ``0.5`` are numbers, and ``007`` is a code.
#: * Commas only in a Western (``1,234,567``) or an Indian (``12,34,567``)
#:   grouping. ``1,5``, ``12,34`` and ``1,2,3`` are not numbers.
#: * An optional ``.`` and 1 to 30 digits. At most 30 digits before it.
#: * One currency mark (``₹``, ``Rs``, ``Rs.``, ``INR``, in any case) before
#:   OR after the number, never both. Or a ``%`` after it.
#: * Spaces and tabs only between a mark and the number, never ``\s``.
#:
#: ``NaN`` and ``Infinity`` never match: the cast of ``numeric`` takes them.
#: The two capture groups are the minus and the number with its commas.
#: ``regexp_match`` gives both, and ``array_to_string`` joins them, so the
#: cast gets the sign and the digits, or NULL. The pattern is a BOUND value
#: (:data:`TEXT_NUMBER_PARAM`): its ``(?:`` would read as a bind name in the
#: SQL text.
_MONEY_MARK = r"(?:₹|rs\.?|inr)"
_TEXT_NUMBER_DIGITS = (
    r"(?:0|[1-9][0-9]{0,2}(?:,[0-9]{3}){1,9}"
    r"|[1-9][0-9]?(?:,[0-9]{2}){0,12},[0-9]{3}"
    r"|[1-9][0-9]{0,29})(?:\.[0-9]{1,30})?"
)
TEXT_NUMBER_PATTERN = (
    r"(?i)^(-)?(?!" + _MONEY_MARK + r".*[^0-9]$)"
    r"(?:" + _MONEY_MARK + r"[ \t]*)?"
    r"(" + _TEXT_NUMBER_DIGITS + r")"
    r"(?:[ \t]*(?:" + _MONEY_MARK + r"|%))?$"
)
#: The bound name of :data:`TEXT_NUMBER_PATTERN`. One value in each statement.
TEXT_NUMBER_PARAM = "agf_tn_re"
#: The longest trimmed value that the pattern reads. A longer one is no
#: number, and the regex never runs on it.
TEXT_NUMBER_MAX_CHARS = 32
#: The characters that a text value trims: white space, and the no-break
#: space U+00A0 (``chr(160)``).
_TEXT_TRIM = "E' \\t\\r\\n\\f\\x0b' || chr(160)"

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

#: Task 3690: the most close fields, and the most other modules, that one
#: ``unknown_field`` 422 names. The cutoff is the ``difflib`` ratio.
CLOSE_MAX = 3
CLOSE_CUTOFF = 0.5
#: The longest asked name that the close match reads. A longer one gets no
#: close fields, so a long name costs no ``difflib`` work.
CLOSE_PROBE = 100
#: The longest field key, label or module name that a hint quotes. Each is
#: org text, so the hint cuts it and puts it between quotes.
HINT_CHARS = 60

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
#: CRM-Q34-10: the bound key of the second field of a two-field group.
GROUP2_KEY = "agf_g2"
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
    #: Task 3128: the module that a ``lookup`` field points at, from
    #: ``crm_field_defs.options.lookup_module``. None for every other type.
    lookup_module: str | None = None
    #: CRM-Q34-11: True for a text field of a module that holds numbers
    #: (:func:`admit_text_numbers`). Its type stays ``text``, so a text
    #: filter on it stays a text filter. Only a measure and a range read it
    #: as a number, through :data:`TEXT_NUMBER_PATTERN`.
    text_number: bool = False


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
        """The field named *key*, or a 422 that names the legal keys.

        CRM-Q33: a module also takes the keys of 2 to 5 currency fields
        joined with ``+`` (``IP+Rent+EB_Total_Amount``), as ONE sum of the
        parts, the shape of a summed ``@amount`` (CRM-Q32-9). So the chat
        reads the total of an assumption that is a sum in one aggregate."""
        found = self.fields.get(key or "")
        if found is None and key and self.column == MODULE_COLUMN:
            found = self._sum_of(key)
        if found is None and key == AMOUNT_WORD and not self.catalogue:
            # The refusal of before CRM-Q31-2: `sum_amount` on a module.
            raise HTTPException(status_code=422,
                                detail="'sum_amount' needs deals. A module has no amount.")
        if found is None and (key or "") in KEY_WORDS and self.catalogue:
            raise HTTPException(status_code=422, detail=self._no_key_field(str(key)))
        if found is None:
            close = self.closest(key or "")
            near = f"The closest fields of {self.owner} are: {close}. " if close else ""
            raise HTTPException(status_code=422, detail={
                "code": "unknown_field",
                "message": f"'{key}' is not a field of {self.owner}. {near}"
                           f"Its fields are: {self.choices()}. "
                           "crm_list_fields gives the type of each.",
                # Task 3690: the asked name, so a caller with a session can
                # name the other modules that hold it (:func:`field_hint`).
                "key": key or "",
            })
        return found

    def closest(self, key: str) -> str:
        """At most :data:`CLOSE_MAX` keys of this scope whose key or label is
        near *key*, as one phrase for a 422, or ``""`` (task 3690). The match
        is ``difflib`` over the case-folded keys and labels, with the cutoff
        :data:`CLOSE_CUTOFF`. A key word is left out, and so is a name longer
        than :data:`CLOSE_PROBE`, so the work stays small."""
        probe = key.strip().casefold()
        if not probe or len(probe) > CLOSE_PROBE:
            return ""
        names: dict[str, str] = {}
        for k, ref in sorted(self.fields.items()):
            if k in KEY_WORDS or ref.parts:
                continue
            names.setdefault(k.casefold(), k)
            names.setdefault(ref.label.casefold(), k)
        keys: list[str] = []
        for name in difflib.get_close_matches(probe, list(names), n=len(names) or 1,
                                              cutoff=CLOSE_CUTOFF):
            if names[name] not in keys:
                keys.append(names[name])
            if len(keys) == CLOSE_MAX:
                break
        return ", ".join(_named(k, self.fields[k].label) for k in keys)

    def _sum_of(self, key: str) -> FieldRef | None:
        """The one ``FieldRef`` of a sum named by its joined keys, or None.
        Each part must be a real currency field of the scope, never a key
        word, and the parts are 2 to 5 distinct keys. A key never reaches the
        SQL text: :func:`_numbers` binds each part."""
        from gateway.crm_sync.catalogue import (
            SUM_KEY_JOIN,
            SUM_LABEL_JOIN,
            SUM_TYPES,
            sum_shape_problem,
        )

        if SUM_KEY_JOIN not in key:
            return None
        keys = key.split(SUM_KEY_JOIN)
        if sum_shape_problem(keys) or any(k in KEY_WORDS for k in keys):
            return None
        refs = [self.fields.get(k) for k in keys]
        if any(r is None or r.type not in SUM_TYPES or r.parts for r in refs):
            return None
        found = [r for r in refs if r is not None]
        return FieldRef(key=key, type=SUM_TYPES[0],
                        label=SUM_LABEL_JOIN.join(r.label for r in found), parts=tuple(found))

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
    #: Task 3127: the FROM text of a module group (:data:`DETOAST_JOIN`).
    source: str = ""


@dataclass(frozen=True)
class WhereSql:
    sql: str
    params: dict[str, Any]
    #: The FROM text of a filter on a sum of fields (:data:`DETOAST_JOIN`).
    source: str = ""


#: CRM-Q32-9a fix round: the detoasted jsonb of a module record. Each
#: ``fields -> :key`` on the stored row reads the whole TOASTed value again,
#: and a sum of 5 parts reads it 40 times a row (7.1 s over 30,000 rows of
#: 2.5 KB, measured). The LATERAL reads it ONCE for each row of the module,
#: and ``OFFSET 0`` stops the planner from folding it back in. Task 3127:
#: every field expression of a module statement (a measure, a group key, a
#: date bucket and each filter) reads this copy, through :func:`_reads`.
#: Production, 2026-11-13: a six-month total over 31,590 invoices of 3 KB
#: took 7.7 s as built and 3.7 s with one detoast.
DETOAST_ALIAS = "agf_d"
DETOAST_COLUMN = f"{DETOAST_ALIAS}.agf_fields"
DETOAST_JOIN = (
    f" CROSS JOIN LATERAL (SELECT {MODULE_COLUMN} || CAST('{{}}' AS jsonb) "
    f"AS agf_fields OFFSET 0) {DETOAST_ALIAS}"
)


def from_source(*sources: str) -> str:
    """The FROM text that a statement adds after its table: the one
    :data:`DETOAST_JOIN` when any part of the statement reads a field of a
    module, else empty. A statement never joins it twice."""
    return DETOAST_JOIN if any(sources) else ""


def _reads(column: str) -> str:
    """The jsonb that an expression over *column* reads (task 3127). A module
    record reads its one detoasted copy, :data:`DETOAST_COLUMN`. A core
    record reads its ``custom`` column, as before."""
    return DETOAST_COLUMN if column == MODULE_COLUMN else column


def _source_of(column: str) -> str:
    """The FROM text of an expression over *column*: :data:`DETOAST_JOIN`
    for a module record, else empty."""
    return DETOAST_JOIN if column == MODULE_COLUMN else ""


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
    target = getattr(defn, "lookup_module", None)
    return FieldRef(
        key=str(defn.key), type=str(defn.type), label=str(defn.label or defn.key),
        options=_options(getattr(defn, "options", None)),
        lookup_module=target if isinstance(target, str) and target else None,
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


def _quoted(name: Any) -> str:
    """Org text for a 422 (task 3690): one line, at most :data:`HINT_CHARS`
    characters, between quotes. A quote inside it is dropped, so the text
    cannot close the quotes early."""
    flat = " ".join(str(name).split()).replace("'", "")
    return f"'{flat[:HINT_CHARS]}'"


def _named(key: str, label: str) -> str:
    """A field key for a 422, with its label when the label differs."""
    if not label or label == key:
        return _quoted(key)
    return f"{_quoted(key)} (label {_quoted(label)})"


#: Task 3690: the other mirrored modules of the bound org that hold a field
#: with the asked key or label. The org, the name and the modules are bound,
#: and RLS holds the session too.
_ELSEWHERE_SQL = (
    "SELECT DISTINCT entity FROM crm_field_defs "
    "WHERE organization_id = CAST(:agf_org AS uuid) "
    "AND entity = ANY(CAST(:agf_entities AS text[])) "
    "AND (lower(key) = :agf_name OR lower(label) = :agf_name) "
    f"ORDER BY entity LIMIT {CLOSE_MAX}"
)


async def field_hint(db: Any, module: str, exc: HTTPException) -> HTTPException:
    """*exc* with one more sentence when it is the 422 ``unknown_field``:
    the other mirrored modules that hold a field with the asked key or
    label (task 3690). Each other 422 comes back as it was. One bounded read
    in the caller's tenant session, for the bound org, after the read of the
    mirrored set. A module name is an API name, never record data. With no
    such module, the 422 names none."""
    detail = exc.detail
    asked = detail.get("key") if isinstance(detail, dict) else None
    if (exc.status_code != 422 or not isinstance(detail, dict)
            or detail.get("code") != "unknown_field" or not asked
            or len(str(asked)) > CLOSE_PROBE):
        return exc
    from gateway.routes.crm.core import current_tenant
    from gateway.routes.crm.modules import mirrored_modules

    mirrored = await mirrored_modules(db)
    others = [f"{MODULE_OWNER}{m}" for m in sorted(mirrored) if m != module]
    org = current_tenant()
    if not others or not org:
        return exc
    rows = (await db.execute(text(_ELSEWHERE_SQL), {
        "agf_org": str(org), "agf_entities": others,
        "agf_name": str(asked).strip().lower(),
    })).fetchall()
    if not rows:
        return exc
    names = ", ".join(_quoted(r[0]) for r in rows)
    # The hint goes FIRST: the tool cuts the message at 800 characters, and the
    # list of keys that follows can be long (verifier F2 of task 3693).
    message = (f"A field {_quoted(asked)} is on: {names}. Give that module as the "
               f"entity. {detail.get('message', '')}")
    return HTTPException(status_code=422, detail={**detail, "message": message})


def _type_words(types: tuple[str, ...]) -> str:
    return ", ".join(types)


#: CRM-Q34-2: the stored types of a text field, for the 422 of ``q``.
TEXT_TYPES: tuple[str, ...] = ("text", "textarea", "email", "phone", "url")
#: The most lookup fields that the 422 of ``q`` names, each with its target.
Q_LOOKUPS_MAX = 10
STATUS_WORD = "@status"


def q_refusal(scope: FieldScope) -> HTTPException:
    """The 422 of ``q`` on a module total (CRM-Q34-2). It names the step
    that works first, then the lookup fields of the module, each with its
    target module, then its text fields. The tool cuts a refusal at 800
    characters, so the step comes first."""
    lookups = sorted(
        (k, ref) for k, ref in scope.fields.items()
        if k not in KEY_WORDS and ref.type == "lookup"
    )
    named = ", ".join(
        f"{_quoted(k)} (to {_quoted(ref.lookup_module)})" if ref.lookup_module else _quoted(k)
        for k, ref in lookups[:Q_LOOKUPS_MAX]
    ) or "none"
    more = len(lookups) - Q_LOOKUPS_MAX
    if more > 0:
        named += f", and {more} more"
    return _refuse(
        "A module total takes no q. To count the records of one name, give "
        "field.<lookup>=<the exact name>. A lookup matches the exact stored name "
        "or the record id. A text field takes field.<key>=<value> in any case. "
        f"The lookup fields of {scope.owner} are: {named}. "
        f"The text fields of {scope.owner} are: {scope.choices(TEXT_TYPES)}.",
    )


def status_field(scope: FieldScope) -> str:
    """``group_by=status`` on a module (CRM-Q34-2): the key word
    :data:`STATUS_WORD` when the catalogue names a status field of the
    module, else a 422 that names the select fields of the module. With an
    open question for the status, the 422 keeps its ``no_key_field`` detail,
    so the chat can ask it (CRM-Q32-8)."""
    if STATUS_WORD in scope.fields:
        return STATUS_WORD
    detail: dict[str, Any] = (
        scope._no_key_field(STATUS_WORD) if scope.catalogue
        else {"code": "unfit_field", "message": ""}
    )
    detail["message"] = (
        f"{scope.owner} names no status field, so group_by=status cannot group it. "
        "Give group_field=<key> with one of its select fields: "
        f"{scope.choices(('select',))}. {detail['message']}"
    ).strip()
    raise HTTPException(status_code=422, detail=detail)


def check_measure(scope: FieldScope, key: str | None) -> FieldRef:
    """The field of a ``sum``, ``avg``, ``min`` or ``max``, or a 422."""
    ref = scope.ref(key)
    # CRM-Q34-11: a text field that holds numbers (`admit_text_numbers`).
    if ref.type not in MEASURE_TYPES and not ref.text_number:
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


def check_groups(scope: FieldScope, key: str | None, key2: str | None,
                 bucket: str | None) -> tuple[FieldRef, FieldRef]:
    """CRM-Q34-10: the two fields of a two-field group, or a 422. The two
    are different fields, and at most one is a date. *bucket* goes to the
    date field. With no date, it goes to the first field, which refuses it."""
    first, second = scope.ref(key), scope.ref(key2)
    if first.key == second.key:
        raise _refuse("Give two different fields as group_field and group_field2.")
    dated = (first.type in DATE_TYPES, second.type in DATE_TYPES)
    if all(dated):
        raise _refuse(
            f"'{first.key}' and '{second.key}' are both dates. Give one date field, "
            "and one picklist, boolean or lookup field.",
        )
    to_first = dated[0] or not dated[1]
    return (check_group(scope, key, bucket if to_first else None),
            check_group(scope, key2, None if to_first else bucket))


# ── The expressions. Every key and value is a bound parameter. ──────────────


def _trimmed(column: str, p: str) -> str:
    """The text of the field, with its outer white space and no-break
    spaces trimmed (CRM-Q34-11)."""
    return f"btrim({column} ->> :{p}, {_TEXT_TRIM})"


def _number(column: str, p: str, text_number: bool = False) -> str:
    """The field as a number, or NULL. A JSON number casts. A string casts
    only when it is a plain decimal, so no cast error can reach the route.

    CRM-Q34-11: with *text_number*, a string is a number only in the strict
    shape of :data:`TEXT_NUMBER_PATTERN`. ``regexp_match`` gives the minus
    and the number, or NULL, and ``array_to_string`` joins them. So the
    regex runs once and the cast only ever gets a captured number. The
    length bound runs first, and the cast stays inside the CASE. Without
    *text_number* the SQL text is the text of before."""
    if text_number:
        t = _trimmed(column, p)
        return (
            f"(CASE WHEN jsonb_typeof({column} -> :{p}) = 'number' "
            f"THEN CAST({column} ->> :{p} AS numeric) "
            f"WHEN jsonb_typeof({column} -> :{p}) = 'string' "
            f"AND length({t}) <= {TEXT_NUMBER_MAX_CHARS} "
            f"THEN CAST(replace(array_to_string(regexp_match({t}, "
            f":{TEXT_NUMBER_PARAM}), ''), ',', '') AS numeric) END)"
        )
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
    (CRM-Q32-9). No key reaches the SQL text. A text number (CRM-Q34-11)
    binds its pattern too, under :data:`TEXT_NUMBER_PARAM`."""
    parts = ref.parts or (ref,)
    out = []
    for i, part in enumerate(parts):
        p = f"{prefix}_{i}"
        bound: dict[str, Any] = {p: part.key}
        if part.text_number:
            bound[TEXT_NUMBER_PARAM] = TEXT_NUMBER_PATTERN
        out.append((_number(column, p, part.text_number), bound))
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

    Returns the SQL, the bound keys and the FROM text. A field of a module
    reads :data:`DETOAST_COLUMN`, so its FROM text is :data:`DETOAST_JOIN`
    (task 3127). A field of a core record has no FROM text."""
    numbers = _numbers(_reads(column), ref, prefix)
    source = _source_of(column)
    params: dict[str, Any] = {}
    for _sql, bound in numbers:
        params.update(bound)
    if len(numbers) == 1:
        return numbers[0][0], params, source
    unset = " AND ".join(f"{n} IS NULL" for n, _b in numbers)
    total = " + ".join(f"COALESCE({n}, 0)" for n, _b in numbers)
    return f"(CASE WHEN {unset} THEN NULL ELSE {total} END)", params, source


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


def measure_sql(scope: FieldScope, ref: FieldRef, measure: str, *,
                prefix: str = _MEASURE_KEY) -> MeasureSql:
    """``measure`` of the field *ref*, with its sum and count of values. A
    sum of fields (CRM-Q32-9) reads through :func:`_amount`. *prefix* names
    the bound keys. Only :func:`split_sql` gives another one."""
    number, params, source = _amount(scope.column, ref, prefix)
    return MeasureSql(
        value=FIELD_MEASURES[measure].format(n=number),
        sum=f"SUM({number})",
        count=f"COUNT({number})",
        params=params,
        source=source,
    )


# ── A split: several fields in one statement (CRM-Q25e part 2) ─────────────

#: The fewest and the most fields of one split total.
SPLIT_MIN = 2
SPLIT_MAX = 5
#: The separator of the keys of a split in ``fields=``. A Zoho API name
#: holds no comma.
SPLIT_JOIN = ","


def check_split(scope: FieldScope, names: str | None) -> list[FieldRef]:
    """The fields of ``fields=a,b,c``, in order, or a 422. Each is a field
    of a measure (:func:`check_measure`), and the keys are 2 to 5 distinct
    ones. A key may be a sum of fields (CRM-Q33), as ``field`` takes."""
    keys = [k.strip() for k in str(names or "").split(SPLIT_JOIN)]
    if (any(not k for k in keys) or not SPLIT_MIN <= len(keys) <= SPLIT_MAX
            or len(set(keys)) != len(keys)):
        raise _refuse(
            f"'fields' names {SPLIT_MIN} to {SPLIT_MAX} distinct number fields, with a "
            "comma between them. For one field, give field=<key>.",
        )
    return [check_measure(scope, key) for key in keys]


def split_sql(scope: FieldScope, refs: Sequence[FieldRef], measure: str) -> list[MeasureSql]:
    """The measure of each field of a split AFTER the first one, which the
    statement reads as its ``field`` (:func:`measure_sql`). Each field binds
    its keys under its own prefix (``agf_m1``, ``agf_m2`` ...), so ONE
    statement reads every field of each row once."""
    return [measure_sql(scope, ref, measure, prefix=f"{_MEASURE_KEY}{i}")
            for i, ref in enumerate(refs, start=1)]


# ── A text field that holds numbers (CRM-Q34-11) ────────────────────────────

#: The stored type that may hold numbers. Only this one, and only on a module.
TEXT_NUMBER_TYPE = "text"
#: A measure or a range reads a text field as numbers only when at least
#: this percent of its filled values are numbers. A value with no value
#: (:data:`NO_VALUE_WORDS`) is not filled. Below it, the field is a code or
#: free text, and the read is a 422.
TEXT_NUMBER_MIN_SHARE_PCT = 50
#: The trimmed values, in lower case, that hold no value. A blank value
#: and a JSON null or a missing key hold no value too. Zero is a number.
NO_VALUE_WORDS: tuple[str, ...] = ("", "-", "—", "na", "n/a", "nil", "none", "null")
#: The most shapes of the values that are not numbers that one answer names.
SHAPES_MAX = 2
#: The longest shape. A shape is the structure of a value, never its text.
SHAPE_CHARS = 24
#: The prefix of the bound keys of the one read, and the bound names.
_TEXT_KEY = "agf_n"
_NO_VALUE = "agf_nv"
#: The longest value that a shape reads, so a long value costs little.
_SHAPE_READ = 64


@dataclass(frozen=True)
class TextNumbers:
    """The values of one text field over the whole module (CRM-Q34-11):
    the count of numbers, of values with no value, and of the values that
    are not numbers. ``shapes`` holds the most common shapes of the values
    that are not numbers, each with its count."""

    numbers: int
    no_value: int
    not_numbers: int
    shapes: tuple[tuple[str, int], ...] = ()

    def wire(self) -> dict[str, Any]:
        """The entry of ``text_numbers`` in the answer of the route."""
        return {
            "numbers": self.numbers, "no_value": self.no_value,
            "not_numbers": self.not_numbers,
            "shapes": [{"shape": shape, "words": shape_words(shape), "count": count}
                       for shape, count in self.shapes],
        }


def text_number_keys(measure: str, field_key: str | None, fields: str | None,
                     pairs: Sequence[tuple[str, str]]) -> list[str]:
    """The keys that a module aggregate reads as a number: the field of a
    ``sum``, ``avg``, ``min`` or ``max``, each key of a split, and the key of
    each range filter (``.from`` or ``.to``). Each key comes once, in order.
    An equality filter is never here: it stays a text filter. Pure, so the
    hermetic tests call it."""
    keys: list[str] = []
    if field_key and measure in FIELD_MEASURES:
        keys.append(field_key)
    if fields:
        keys.extend(k.strip() for k in str(fields).split(SPLIT_JOIN) if k.strip())
    for name, _value in pairs:
        for end in RANGE_ENDS:
            if name.endswith(end) and name[: -len(end)]:
                keys.append(name[: -len(end)])
    return list(dict.fromkeys(keys))


def _no_value(column: str, p: str) -> str:
    """True when the field holds no value: a missing key, a JSON null, or a
    string that trims to one of :data:`NO_VALUE_WORDS` in any case."""
    return (
        f"(jsonb_typeof({column} -> :{p}) IS NULL "
        f"OR jsonb_typeof({column} -> :{p}) = 'null' "
        f"OR (jsonb_typeof({column} -> :{p}) = 'string' "
        f"AND lower({_trimmed(column, p)}) = ANY(CAST(:{_NO_VALUE} AS text[]))))"
    )


def _shape(column: str, p: str) -> str:
    """The shape of a value that is not a number: each run of digits is
    ``9``, each run of letters is ``a``, and each other character that is
    not plain punctuation is ``?``. So the shape never carries the text or
    the digits of the value. A JSON list, object or boolean gives its type
    in brackets. At most :data:`SHAPE_CHARS` characters. Each colon of the
    class ``[:alpha:]`` has a backslash, or ``text()`` reads ``:alph`` as a
    bind name. ``text()`` takes the backslash off."""
    digits = f"regexp_replace(left({_trimmed(column, p)}, {_SHAPE_READ}), '[0-9]+', '9', 'g')"
    letters = f"regexp_replace({digits}, '[[\\:alpha\\:]]+', 'a', 'g')"
    other = f"regexp_replace({letters}, '[^9a ./,%()_+#&₹-]+', '?', 'g')"
    return (
        f"(CASE WHEN jsonb_typeof({column} -> :{p}) = 'string' "
        f"THEN left({other}, {SHAPE_CHARS}) "
        f"ELSE '(' || jsonb_typeof({column} -> :{p}) || ')' END)"
    )


def _text_part(key: str, prefix: str) -> tuple[str, str, dict[str, Any]]:
    """The guarded number of the text field *key* (through :func:`_amount`,
    the one builder), its bound key name and its bound values."""
    ref = FieldRef(key=key, type=TEXT_NUMBER_TYPE, label=key, text_number=True)
    number, bound, _source = _amount(MODULE_COLUMN, ref, prefix)
    (p,) = (name for name in bound if name != TEXT_NUMBER_PARAM)
    return number, p, bound


def text_numbers_sql(keys: Sequence[str]) -> tuple[str, dict[str, Any]]:
    """ONE statement and ONE scan of the whole module for every key: the
    count of numbers, of values with no value and of values that are not
    numbers, and the :data:`SHAPES_MAX` most common shapes of the last.

    The scan reads each record once (:data:`DETOAST_JOIN`). A LATERAL
    VALUES list gives one row for each record and key, with the guarded
    number test and the no-value test. The shape runs only on a value that
    is not a number. The rows are MATERIALIZED, so the counts and the
    shapes read them and not the table. Each key, the pattern, the words,
    the org and the module are bound. Only the shape leaves the database.

    A row for each key and shape, in the order of the key and the rank:
    ``(k, numbers, no_value, not_numbers, shape, count)``. A key with no
    shape has one row with a NULL shape. An empty module gives no row."""
    params: dict[str, Any] = {_NO_VALUE: list(NO_VALUE_WORDS)}
    rows, shapes = [], []
    for i, key in enumerate(keys):
        number, p, bound = _text_part(key, f"{_TEXT_KEY}{i}")
        params.update(bound)
        rows.append(f"({i}, {number} IS NOT NULL, "
                    f"coalesce({_no_value(DETOAST_COLUMN, p)}, false))")
        shapes.append(f"WHEN {i} THEN {_shape(DETOAST_COLUMN, p)}")
    sql = (
        "WITH agf_tv AS MATERIALIZED (SELECT agf_x.k, agf_x.num, agf_x.nv, "
        f"CASE WHEN NOT agf_x.num AND NOT agf_x.nv THEN CASE agf_x.k {' '.join(shapes)} "
        "END END AS shape "
        f"FROM crm_module_records{DETOAST_JOIN} "
        f"CROSS JOIN LATERAL (VALUES {', '.join(rows)}) AS agf_x(k, num, nv) "
        f"WHERE organization_id = CAST(:agf_org AS uuid) AND module = :{_MODULE}), "
        "agf_tc AS (SELECT k, count(*) FILTER (WHERE num) AS numbers, "
        "count(*) FILTER (WHERE nv) AS no_value, "
        "count(*) FILTER (WHERE NOT num AND NOT nv) AS not_numbers "
        "FROM agf_tv GROUP BY k), "
        "agf_ts AS (SELECT k, shape, count(*) AS n, row_number() OVER "
        "(PARTITION BY k ORDER BY count(*) DESC, shape) AS agf_r "
        "FROM agf_tv WHERE shape IS NOT NULL GROUP BY k, shape) "
        "SELECT agf_tc.k, agf_tc.numbers, agf_tc.no_value, agf_tc.not_numbers, "
        "agf_ts.shape, agf_ts.n FROM agf_tc LEFT JOIN agf_ts "
        f"ON agf_ts.k = agf_tc.k AND agf_ts.agf_r <= {SHAPES_MAX} "
        "ORDER BY agf_tc.k, agf_ts.agf_r"
    )
    return sql, params


def text_number_fits(found: TextNumbers) -> bool:
    """True when at least one value is a number, and the numbers are at
    least :data:`TEXT_NUMBER_MIN_SHARE_PCT` percent of the filled values (the
    numbers and the values that are not numbers). Integers only."""
    filled = found.numbers + found.not_numbers
    return found.numbers >= 1 and found.numbers * 100 >= filled * TEXT_NUMBER_MIN_SHARE_PCT


#: Each shape of a value that is not a number → its words, for the answer
#: and the 422. The first match wins. Any other shape is quoted as it is.
_SHAPE_WORDS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern), words) for pattern, words in (
        (r"^\(array\)$", "a JSON list"),
        (r"^\(object\)$", "a JSON object"),
        (r"^\(boolean\)$", "true or false"),
        (r"^-?9([.,]9)*( ?a)+\.?$", "a number with a unit word"),
        (r"^a( a)*\.?$", "words only"),
        (r"^9 ?- ?9$", "a range"),
        (r"^9[-/.]9[-/.]9$", "a date or a code"),
        (r"^-?9 9$", "two numbers"),
        (r"^-?9(\.9)?$", "digits with a leading zero, or too many digits"),
        (r"^-?9(,9)+(\.9)?$", "a comma that is no thousands group"),
        (r"^\(.*9.*\)$", "a number in brackets"),
        (r"^-?9a9$", "an exponent or a code"),
        (r"^a.*9", "a code"),
    )
)


def shape_words(shape: str) -> str:
    """The words of one shape (:data:`_SHAPE_WORDS`), or the shape in
    quotes. A shape holds no text of the value, so it can be quoted."""
    for pattern, words in _SHAPE_WORDS:
        if pattern.search(shape):
            return words
    return f"the shape '{shape}'"


def text_counts_words(found: TextNumbers) -> str:
    """The three counts of one field, and its most common shapes, in words.
    The tool writes the same words from ``text_numbers`` of the answer."""
    out = (f"In this module: {found.numbers:,} "
           f"{'number' if found.numbers == 1 else 'numbers'}, "
           f"{found.no_value:,} with no value, {found.not_numbers:,} not "
           f"{'a number' if found.not_numbers == 1 else 'numbers'}.")
    if found.shapes:
        shown = ", ".join(f"{shape_words(shape)} ({count:,})" for shape, count in found.shapes)
        out += f" Most common of these: {shown}."
    return out


def _text_refusal(scope: FieldScope, key: str, found: TextNumbers,
                  measured: bool) -> HTTPException:
    """The 422 of a text field that does not hold numbers, with its counts.
    A measure names the number fields. A range keeps the words of the range
    422 of before (CRM-Q34-2 item 4): the date and number fields, and the
    equality filter."""
    why = ("no value of it is a number" if found.numbers == 0 else
           "fewer than half of its filled values are numbers, so it looks like a "
           "code or free text")
    head = f"'{key}' is a text field, and {why}. {text_counts_words(found)}"
    if measured:
        return _refuse(
            f"{head} A measure takes a field of type {_type_words(MEASURE_TYPES)}, or a "
            "text field that holds numbers. The number fields of "
            f"{scope.owner} are: {scope.choices(MEASURE_TYPES)}.",
        )
    return _refuse(
        f"{head} A range takes a date or a number field. The date and number fields of "
        f"{scope.owner} are: {scope.choices(DATE_TYPES + MEASURE_TYPES)}. "
        f"To match one value, give {scope.filter_word(key)}=<value>.",
    )


async def admit_text_numbers(
    db: Any, scope: FieldScope, keys: Sequence[str],
    measured: Collection[str] | None = None,
) -> tuple[FieldScope, dict[str, TextNumbers]]:
    """*scope* with each text field of *keys* that holds numbers marked
    ``text_number``, and the counts of each such field (CRM-Q34-11).

    Only a module scope, and only a field of type :data:`TEXT_NUMBER_TYPE`,
    never a key word or a sum. An unknown key and every other type come back
    unchanged, so the checks after this give their 422 of before. With no
    text key, no statement runs.

    ONE statement and ONE scan (:func:`text_numbers_sql`) read the counts of
    every key over the whole module and the most common shapes of its
    values that are not numbers. It runs in the caller's tenant session,
    with the org in the predicate and RLS under it. An empty module counts
    0 for each key. A field that fits (:func:`text_number_fits`) keeps
    its type ``text`` and gets ``text_number``, so a measure and a range
    read it as a number, and a text filter stays a text filter. A field that
    does not fit is a 422 with its counts and its shapes. *measured* holds
    the keys of a measure or a split, and None means every key. The 422 of
    any other key, a range, keeps the words of a range."""
    if scope.column != MODULE_COLUMN:
        return scope, {}
    texts = [
        k for k in keys
        if k not in KEY_WORDS and (ref := scope.fields.get(k)) is not None
        and ref.type == TEXT_NUMBER_TYPE and not ref.parts
    ]
    if not texts:
        return scope, {}
    from gateway.routes.crm.core import current_tenant

    where = {"agf_org": str(current_tenant()), _MODULE: scope.owner[len(MODULE_OWNER):]}
    sql, params = text_numbers_sql(texts)
    rows = (await db.execute(text(sql), {**params, **where})).fetchall()
    counts: dict[str, TextNumbers] = {}
    for i, key in enumerate(texts):
        mine = [r for r in rows if int(r[0]) == i]
        head = mine[0] if mine else (i, 0, 0, 0, None, None)
        counts[key] = TextNumbers(
            numbers=int(head[1] or 0), no_value=int(head[2] or 0),
            not_numbers=int(head[3] or 0),
            shapes=tuple((str(r[4]), int(r[5])) for r in mine if r[4] is not None))
    for key in texts:
        if not text_number_fits(counts[key]):
            raise _text_refusal(scope, key, counts[key],
                                measured is None or key in measured)
    fields = {**scope.fields, **{
        k: replace(scope.fields[k], text_number=True) for k in texts}}
    return replace(scope, fields=fields), counts


def group_sql(
    scope: FieldScope, ref: FieldRef, bucket: str | None, *, zone: str,
    prefix: str = _GROUP_KEY,
) -> GroupSql:
    """The group key of the field *ref*. A date groups by its bucket.

    A ``date`` keeps the day that Zoho wrote. A ``datetime`` reads the whole
    value with its offset, and takes its bucket on the clock of *zone*, the
    CRM zone (CRM-Q25b). So it agrees with ``created_month``. A module
    group reads the detoasted copy (task 3127). *prefix* names the bound
    key. Only the second field of a two-field group (CRM-Q34-10) gives
    another one, :data:`GROUP2_KEY`.
    """
    column, p = _reads(scope.column), prefix
    source = _source_of(scope.column)
    if ref.type in DATE_TYPES:
        unit, fmt = BUCKETS[bucket or ""]
        key = f"to_char(date_trunc('{unit}', {_when(column, ref, p)}), '{fmt}')"
        return GroupSql(key=key, label="NULL", params={p: ref.key, ZONE_PARAM: zone},
                        source=source)
    if ref.type == "lookup":
        return GroupSql(
            key=_lookup_id(column, p),
            label=(
                f"max(CASE WHEN jsonb_typeof({column} -> :{p}) = 'object' "
                f"THEN {column} -> :{p} ->> 'name' END)"
            ),
            params={p: ref.key},
            source=source,
        )
    return GroupSql(key=f"{column} ->> :{p}", label="NULL", params={p: ref.key},
                    source=source)


# ── The day of each record, for the check of one day (CRM-Q25e part 4) ─────

#: The bound key of the day field of :func:`day_sql`.
_DAY_KEY = "agf_dd"
#: The key word of the catalogue date of a module (CRM-Q31-2).
DATE_WORD = "@date"


def day_field(scope: FieldScope | None, grouped: FieldRef | None,
              pairs: Sequence[tuple[str, str]]) -> FieldRef | None:
    """The date field whose days the check of one day reads (task 3129):
    the date field of the group, else the field of the first date range
    filter, else the catalogue date (``@date``). None for a core entity, and
    when the answer names no date."""
    if scope is None or scope.column != MODULE_COLUMN:
        return None
    if grouped is not None and grouped.type in DATE_TYPES and not grouped.parts:
        return grouped
    for name, _value in pairs:
        for end in RANGE_ENDS:
            ref = scope.fields.get(name[: -len(end)]) if name.endswith(end) else None
            if ref is not None and ref.type in DATE_TYPES:
                return ref
    ref = scope.fields.get(DATE_WORD)
    return ref if ref is not None and ref.type in DATE_TYPES else None


def day_sql(scope: FieldScope, ref: FieldRef, *, zone: str) -> GroupSql:
    """The calendar day of *ref* as a group key (task 3129): the day that
    Zoho wrote for a ``date``, and the day in the CRM zone for a
    ``datetime`` (:func:`_calendar_day`). It reads the detoasted copy."""
    return GroupSql(key=_calendar_day(_reads(scope.column), ref, _DAY_KEY), label="NULL",
                    params={_DAY_KEY: ref.key, ZONE_PARAM: zone},
                    source=_source_of(scope.column))


def _when(column: str, ref: FieldRef, p: str) -> str:
    """The value of a date or a datetime field as a ``timestamp``. A
    ``date`` keeps the day that Zoho wrote. A ``datetime`` takes its time on
    the clock of the CRM zone (CRM-Q25b)."""
    if ref.type == "datetime":
        return zoned(_instant(column, p))
    return f"CAST({_date(column, p)} AS timestamp)"


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
            if name.endswith(end):
                # A key word with no key field is the 422 `no_key_field`.
                # Task 3690: an unknown base is the 422 `unknown_field` of
                # the base, so its hint names the field, not `<key>.from`.
                return scope.ref(name[: -len(end)]), end
    return scope.ref(name), None


def _filter(scope: FieldScope, ref: FieldRef, end: str | None, k: str, v: str,
            value: str, also: Sequence[str] = (),
            ext: tuple[str, str] | None = None) -> tuple[str, Any, dict[str, Any], str]:
    """One filter clause, its bound value, its bound keys and its FROM text.
    *also* holds the stored values that a text filter counts too
    (§18.16 part 6, :func:`text_matches`). *ext* is ``(bound name, Zoho
    id)`` of a lookup filter whose value is a Metorite id (task 3128,
    :func:`lookup_ids`)."""
    x = ext[0] if ext is not None and ref.type == "lookup" and end is None else None
    clause, bound = _filter_value(scope, ref, end, k, v, value, also, x)
    # CRM-Q34-11: a range on a text field that holds numbers reads the same
    # guarded number. Its `=` stays a text filter, as before.
    if ref.type in MEASURE_TYPES or (ref.text_number and end is not None):
        # Task 3690: a range compares the same guarded number as `=`. A value
        # that is no number is NULL, so no end of a range takes it.
        number, keys, source = _amount(scope.column, ref, k)
        operator = "=" if end is None else RANGE_ENDS[end]
        return f"{number} {operator} {clause}", bound, keys, source
    keys = {k: ref.key}
    if x is not None and ext is not None:
        keys[x] = ext[1]
    return clause, bound, keys, _source_of(scope.column)


def _decimal(name: str, value: str) -> str:
    """The bound text of a number filter value, or a 422 (``field.<name>``).
    The one guard of the value of ``=``, ``.from`` and ``.to``: a finite
    number of at most :data:`MAX_DIGITS` digits, below 10^:data:`MAX_EXPONENT`."""
    try:
        number = Decimal(value.strip())
    except InvalidOperation:
        raise _refuse(f"'field.{name}' must be a number.") from None
    if (not number.is_finite()
            or len(number.as_tuple().digits) > MAX_DIGITS
            or abs(number.adjusted()) > MAX_EXPONENT):
        raise _refuse(
            f"'field.{name}' must be a number of at most {MAX_DIGITS} "
            f"digits, below 10^{MAX_EXPONENT}.",
        )
    return str(number)


def _filter_value(scope: FieldScope, ref: FieldRef, end: str | None, k: str, v: str,
                  value: str, also: Sequence[str] = (),
                  x: str | None = None) -> tuple[str, Any]:
    """One filter clause and its bound value. A number gives only the right
    side of its clause: :func:`_filter` puts the number of :func:`_amount`
    on the left. A module filter reads the detoasted copy (task 3127)."""
    column = _reads(scope.column)
    if end is not None and (ref.type in MEASURE_TYPES or ref.text_number):
        # Task 3690: a number range. :func:`_filter` puts the number on the left.
        return f"CAST(:{v} AS numeric)", _decimal(ref.key + end, value)
    if end is not None:
        if ref.type not in DATE_TYPES:
            raise _refuse(
                f"'{ref.key}' is a {ref.type} field. A range takes a date or a number "
                f"field. The date and number fields of {scope.owner} are: "
                f"{scope.choices(DATE_TYPES + MEASURE_TYPES)}. "
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
        return f"CAST(:{v} AS numeric)", _decimal(ref.key, value)
    if ref.type == "boolean":
        word = value.strip().lower()
        if word not in ("true", "false"):
            raise _refuse(f"'field.{ref.key}' must be true or false.")
        return f"{column} ->> :{k} = :{v}", word
    if ref.type == "lookup":
        # Task 3128: a Metorite id of the target record also matches the
        # Zoho id that the lookup stores, bound as :x.
        by_id = f" OR {_lookup_id(column, k)} = :{x}" if x else ""
        return (
            f"({_lookup_id(column, k)} = :{v} OR {column} -> :{k} ->> 'name' = :{v}{by_id})",
            value,
        )
    # A picklist, a text and each other type match the text. A multiselect
    # holds a JSON array, and the array arm matches one of its options.
    # WS-53 §18.16 part 6: in any case, and each stored variant in *also*.
    # The CASE keeps the array read off a value that is no array.
    wanted = sorted({_folded(x) for x in (value, *also) if _folded(x)})
    return (
        f"(lower(btrim({column} ->> :{k})) = ANY(CAST(:{v} AS text[])) OR "
        f"CASE WHEN jsonb_typeof({column} -> :{k}) = 'array' THEN EXISTS (SELECT 1 FROM "
        f"jsonb_array_elements_text({column} -> :{k}) agf_e WHERE lower(btrim(agf_e)) = "
        f"ANY(CAST(:{v} AS text[]))) ELSE false END)",
        wanted or [""],
    )


def _folded(text_value: Any) -> str:
    """A filter value as the text clause compares it: trimmed, lower case."""
    return str(text_value or "").strip().lower()


#: §18.16 part 6: the types whose filter value counts its stored variants.
ALIAS_TYPES: tuple[str, ...] = ("text", "select")
#: The most distinct values that one alias read takes.
ALIAS_VALUES = 500


async def text_matches(db: Any, org: str, scope: FieldScope, module: str,
                       pairs: Sequence[tuple[str, str]]) -> dict[int, list[str]]:
    """The stored values that each text filter of *pairs* counts, by the
    index of the pair (WS-53 §18.16 part 6): the value in each case, and its
    short forms, from the distinct values of the field
    (``crm_sync.terms.value_matches``). One grouped statement for each text
    filter, in the caller's tenant session. A range and each other type
    take none."""
    from gateway.crm_sync.terms import value_matches

    out: dict[int, list[str]] = {}
    for index, (name, value) in enumerate(pairs):
        ref = scope.fields.get(name)
        if ref is None or ref.type not in ALIAS_TYPES or ref.parts or not str(value).strip():
            continue
        distinct = await value_counts(db, org, module, ref.key, ALIAS_VALUES)
        out[index] = value_matches(str(value), distinct)
    return out


# ── A lookup filter by a Metorite id (task 3128) ────────────────────────────

#: The shape of a Metorite record id. A Zoho id is digits, so it never has it.
_UUID_TEXT = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

#: The Zoho id of ONE record of a mirrored module, by its Metorite id. The
#: org, the module and the id are bound, and RLS holds the session too.
_LOOKUP_ID_SQL = (
    "SELECT ext_id FROM crm_module_records WHERE organization_id = CAST(:agf_org AS uuid) "
    f"AND module = :{_MODULE} AND id = CAST(:agf_id AS uuid)"
)

#: CRM-Q34-1: the Zoho id of ONE core record, by its Metorite id, for a
#: lookup whose target is a core entity (``Contacts``, ``Accounts``,
#: ``Leads``, ``Deals``). Each table name comes from
#: ``modules.KNOWN_TARGETS``, the one map that ``resolve_links`` reads, and
#: never from the caller. The org and the id are bound, and RLS holds the
#: session too. A Native record has no Zoho id, so it finds nothing.
_CORE_LOOKUP_ID_SQL: dict[str, str] = {
    module: (
        f"SELECT ext_id FROM {entity.table} WHERE organization_id = "
        "CAST(:agf_org AS uuid) AND id = CAST(:agf_id AS uuid)"
    )
    for module, entity in KNOWN_TARGETS.items()
}


def lookup_targets(scope: FieldScope, pairs: Sequence[tuple[str, str]],
                   ) -> dict[int, tuple[str, str]]:
    """``index → (lookup module, Metorite id)`` for each filter of *pairs* on
    a ``lookup`` field whose value has the shape of a Metorite id. The
    module comes from the field definition (``lookup_module``), never from
    the caller. Pure, so the hermetic tests call it."""
    out: dict[int, tuple[str, str]] = {}
    for index, (name, value) in enumerate(pairs):
        ref = scope.fields.get(name)
        ident = str(value or "").strip()
        if (ref is None or ref.type != "lookup" or not ref.lookup_module
                or not _UUID_TEXT.match(ident)):
            continue
        out[index] = (ref.lookup_module, ident.lower())
    return out


async def lookup_ids(db: Any, org: str, scope: FieldScope,
                     pairs: Sequence[tuple[str, str]]) -> dict[int, str]:
    """The Zoho id of each lookup filter whose value is a Metorite id of a
    record of the lookup's module (task 3128). The chat finds a record with
    ``crm_list_module_records`` and sends its Metorite id, while the stored
    lookup holds the Zoho id. One read by primary key for each such filter,
    in the caller's tenant session. An id of another module or another org
    finds nothing, and the filter then matches as before.

    CRM-Q34-1: a lookup whose target is a core entity reads the core table
    of :data:`_CORE_LOOKUP_ID_SQL`. Before, it read the module records, found
    nothing, and the total was a silent zero."""
    out: dict[int, str] = {}
    for index, (module, ident) in lookup_targets(scope, pairs).items():
        core = _CORE_LOOKUP_ID_SQL.get(module)
        ext = (await db.execute(text(core or _LOOKUP_ID_SQL), {
            "agf_org": str(org), "agf_id": ident,
            **({} if core else {_MODULE: module}),
        })).scalar()
        if ext:
            out[index] = str(ext)
    return out


def module_where(
    scope: FieldScope, module: str, pairs: list[tuple[str, str]], *, zone: str,
    matches: Mapping[int, Sequence[str]] | None = None,
    ids: Mapping[int, str] | None = None,
) -> WhereSql:
    """The WHERE clause of one module: the module, and each field filter.

    Each name is checked against *scope* first, so an unknown name is a 422
    before any statement runs. A range on a datetime field reads *zone*,
    the CRM zone (CRM-Q25b). *ids* holds the Zoho id of each lookup filter
    whose value is a Metorite id (task 3128, :func:`lookup_ids`).
    """
    clauses = [f"module = :{_MODULE}"]
    params: dict[str, Any] = {_MODULE: module, ZONE_PARAM: zone}
    sources: list[str] = []
    for index, (name, value) in enumerate(pairs):
        ref, end = _split(scope, name)
        k, v = f"agf_k{index}", f"agf_v{index}"
        ext = (f"agf_x{index}", ids[index]) if ids and index in ids else None
        clause, bound, keys, source = _filter(scope, ref, end, k, v, value,
                                              (matches or {}).get(index, ()), ext)
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
    owner: str | None, params: Any, ids: Mapping[int, str] | None = None,
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
    where = module_where(scope, str(overlay.module), pairs, zone=zone, ids=ids)
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
    weight = (f"(CASE WHEN {_reads(column)} ->> :agf_ws = ANY(CAST(:agf_open AS text[])) "
              f"THEN {a} * {w} / 100.0 END)")
    return MeasureSql(
        value=f"COALESCE(SUM({weight}), 0)", sum=f"SUM({weight})", count=f"COUNT({weight})",
        params={**a_keys, **w_keys, "agf_ws": stage.key, "agf_open": list(open_values)},
        source=from_source(a_source, w_source, _source_of(column)),
    )


# ── The values of a stage field (CRM-Q32-1, §18.14.3) ───────────────────────

#: The distinct values of one field of a mirrored module, each with its
#: count of records, the most first. The org, the module and the key are
#: bound, so no name reaches the SQL text.
#: Fix round: each value is trimmed as ``mapping.text_value`` trims it, the
#: rule of the executor, so " Won " counts as "Won" and never shows as a value
#: with no entry. An object or a list is no value, as there. Task 3127: the
#: three reads of the field read the detoasted copy, so a row is read once.
_TRIMMED = f"btrim({DETOAST_COLUMN} ->> :{_GROUP_KEY}, E' \\t\\r\\n\\f\\x0b')"
_VALUE_COUNTS_SQL = (
    f"SELECT {_TRIMMED} AS v, count(*) AS n "
    f"FROM crm_module_records{DETOAST_JOIN} "
    "WHERE organization_id = CAST(:agf_org AS uuid) "
    f"AND module = :{_MODULE} AND ext_source = 'zoho' "
    f"AND jsonb_typeof({DETOAST_COLUMN} -> :{_GROUP_KEY}) IN ('string', 'number') "
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


# ── The evidence of GST and TDS (CRM-Q33 part 5, §18.15) ────────────────────

#: The prefix of the bound keys of the check of GST.
_GST_KEY = "agf_t"


def gst_check_sql(keys: list[str], rels: list[Any]) -> tuple[str, dict[str, Any]]:
    """The ONE statement of the check of GST, and its bound keys. *keys* are
    the distinct fields that the relations name, and each relation of *rels*
    (``crm_sync.questions.Relation``) names them by key. Each field is the
    guarded number of :func:`_numbers`, and each alias comes from an index,
    never from text. The org, the module and the limit are bound by the
    caller, so one statement reads the first records of the module once.

    A ``ratio`` holds where the field is its basic times 1.18, within 1% and
    1 rupee. A ``sum`` holds where the field is its basic plus the GST field,
    less the TDS field when it is set, within 1 rupee."""
    from gateway.crm_sync.questions import GST_RATE

    numbers = _numbers("agf_fields", FieldRef(key="", type="currency", label="",
                                              parts=tuple(FieldRef(key=k, type="currency",
                                                                   label=k) for k in keys)),
                       _GST_KEY)
    index = {k: i for i, k in enumerate(keys)}
    params: dict[str, Any] = {"agf_rate": GST_RATE}
    inner = []
    for i, (sql, bound) in enumerate(numbers):
        inner.append(f"{sql} AS n_{i}")
        params.update(bound)
    outer = ["count(*) AS records"]
    for r, rel in enumerate(rels):
        f, b = f"n_{index[rel.field]}", f"n_{index[rel.basic]}"
        if rel.kind == "ratio":
            can = f"{f} IS NOT NULL AND {b} IS NOT NULL AND {b} > 0"
            held = f"abs({f} - {b} * CAST(:agf_rate AS numeric)) <= {b} * 0.01 + 1"
            outer.append(f"count(*) FILTER (WHERE {can}) AS den_{r}")
            outer.append(f"count(*) FILTER (WHERE {can} AND {held}) AS ok_{r}")
            continue
        g = f"n_{index[rel.gst]}"
        can = f"{f} IS NOT NULL AND {b} IS NOT NULL AND {g} IS NOT NULL AND {g} <> 0"
        if rel.tds is None:
            held = f"abs({f} - ({b} + {g})) <= 1"
            outer.append(f"count(*) FILTER (WHERE {can}) AS den_{r}")
            outer.append(f"count(*) FILTER (WHERE {can} AND {held}) AS ok_{r}")
            continue
        t = f"n_{index[rel.tds]}"
        held = f"abs({f} - ({b} + {g} - COALESCE({t}, 0))) <= 1"
        outer.append(f"count(*) FILTER (WHERE {can}) AS den_{r}")
        outer.append(f"count(*) FILTER (WHERE {can} AND {held}) AS ok_{r}")
        outer.append(f"count(*) FILTER (WHERE {can} AND {held} AND {t} IS NOT NULL "
                     f"AND {t} <> 0) AS tds_{r}")
    sql = (
        f"SELECT {', '.join(outer)} FROM (SELECT {', '.join(inner)} "
        f"FROM (SELECT {MODULE_COLUMN} || CAST('{{}}' AS jsonb) AS agf_fields "
        "FROM crm_module_records WHERE organization_id = CAST(:agf_org AS uuid) "
        f"AND module = :{_MODULE} ORDER BY id LIMIT :agf_limit) d) r"
    )
    return sql, params


async def gst_check(db: Any, org: str, module: str, keys: list[str],
                    rels: list[Any]) -> list[tuple[int, int, int]]:
    """``(rows, held, tds_rows)`` of each relation of *rels*: ONE bounded
    statement over the first :data:`SUM_CHECK_ROWS` records of *module*, in
    the caller's tenant session, with the org in its predicate and RLS under
    it. The caller took each key from the field definitions of the module."""
    if not rels:
        return []
    sql, params = gst_check_sql(keys, rels)
    row = (await db.execute(text(sql), {
        **params, "agf_org": str(org), _MODULE: module, "agf_limit": SUM_CHECK_ROWS,
    })).first()
    return [(int(getattr(row, f"den_{r}", 0) or 0), int(getattr(row, f"ok_{r}", 0) or 0),
             int(getattr(row, f"tds_{r}", 0) or 0)) for r in range(len(rels))]


__all__ = [
    "AMOUNT_WORD",
    "BUCKETS",
    "CHOICES_MAX",
    "DATE_TYPES",
    "DATE_WORD",
    "DEFAULT_ZONE",
    "DETOAST_COLUMN",
    "DETOAST_JOIN",
    "FIELD_MEASURES",
    "FIELD_PREFIX",
    "KEY_WORDS",
    "MEASURE_TYPES",
    "MODULE_MEASURES",
    "NO_VALUE_WORDS",
    "OVERLAY_REFUSED",
    "SPLIT_JOIN",
    "SPLIT_MAX",
    "SPLIT_MIN",
    "SUM_CHECK_ROWS",
    "TEXT_NUMBER_MAX_CHARS",
    "TEXT_NUMBER_MIN_SHARE_PCT",
    "TEXT_NUMBER_PARAM",
    "TEXT_NUMBER_PATTERN",
    "VALUE_GROUP_TYPES",
    "ZONE_PARAM",
    "FieldRef",
    "FieldScope",
    "GroupSql",
    "MeasureSql",
    "SumCheck",
    "TextNumbers",
    "WhereSql",
    "admit_text_numbers",
    "check_group",
    "check_measure",
    "check_split",
    "core_scope",
    "crm_zone",
    "day_field",
    "day_sql",
    "day_start",
    "field_pairs",
    "from_source",
    "group_sql",
    "gst_check",
    "gst_check_sql",
    "key_words",
    "lookup_ids",
    "lookup_targets",
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
    "shape_words",
    "split_sql",
    "sum_check",
    "sum_check_sql",
    "text_counts_words",
    "text_matches",
    "text_number_fits",
    "text_number_keys",
    "text_numbers_sql",
    "used_keys",
    "value_counts",
    "zoned",
]
