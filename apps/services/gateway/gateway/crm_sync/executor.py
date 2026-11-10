"""The executor of the mapping plan (WS-53 CRM-Q19, ``crm_platform.md`` §18.9.6).

**One path for a rule value.** An approved rule of the plan puts a value of a
provider field into one of our columns. Each value of a rule goes through
this module, and no other code turns a rule value into a column value. Three
callers use it:

* ``mapping.apply_field_rules``, when the sync maps one record
  (``map_record``);
* ``field_rules.backfill_batch``, when the sync fills the rows that are there;
* ``POST /crm/mapping/preview`` (``routes/crm/mapping_plans.py``), which
  applies a draft to sampled records in memory through
  :func:`preview_record`, and writes nothing.

So the preview shows what the sync will write, because the two run the same
functions. The fence ``tests/unit/test_crm_executor.py`` scans the gateway
for a second call of the coercion.

**The functions are pure.** They read no database, call no provider and call
no model. The fixed map (``mapping.map_record``) stays where it is. The
executor runs only the value of a RULE.

**The transforms.** A transform takes the target column and one field value,
and returns an :class:`Outcome`. CRM-Q19 builds two of them:

* :func:`copy` puts the text of the value into a ``TEXT`` column;
* :func:`cast` turns the value into the type of the column (a number, a
  date), inside the limits of the column.

Both call ``mapping.coerce_target_value``, so the coercion has one rule. A
value that does not coerce gives ``None``. A filled value that gives ``None``
is ``unmapped``. The sync counts it under the count name ``unmapped`` of the
entity, and the preview counts it by entity and target column. Neither one
names the value.

**The five transforms of CRM-Q20-1.** CRM-Q20-1 adds the kinds of rule
``role``, ``target`` and ``stage`` (``crm_platform.md`` §18.9.6, "The three
new kinds of rule"), and the five transforms that they call:

* :func:`value_map` maps a stage value to ``won``, ``lost`` or ``open``
  through a :class:`ValueMap`. A value with no entry gets no stage, and it is
  ``unmapped``;
* :func:`normalize` cleans an email or a phone number;
* :func:`coalesce` takes the first of two or more values that maps;
* :func:`link` takes the id of the parent that a lookup names;
* :func:`owner` takes the email of the owner that a Zoho owner names.

Each one is a pure function with the shape of :data:`Transform`, and each
one is in :data:`TRANSFORMS` under its word. ``value_map`` takes its map as
the keyword argument ``spec``. The kinds go through :func:`run_target` and
:func:`run_stage`, and no other code calls the five. A ``field`` rule names
no word, so :func:`transform_of` chooses ``copy`` or ``cast`` from the type
of the column.

**Rule 2 of the kinds has one home here.** :func:`slot_target` gives the
legal targets of a slot: the columns of ``mapping.FIELD_RULE_TARGETS`` for
the entity of the slot, and ``name``. :func:`target_problem` and
:func:`stage_problem` say why an item is not legal. ``mapping_plans.commit``
and the pipeline both call them.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Any

# The mapping module of the source holds the targets and the one coercion.
# It does not import this module at load time (it imports it inside
# ``apply_field_rules``), so this import makes no cycle.
from gateway.crm_sources.zoho import mapping
from gateway.crm_sources.zoho.mapping import RuleTarget

#: The words of the transforms that CRM-Q19 builds.
COPY = "copy"
CAST = "cast"
#: The words of the transforms that CRM-Q20-1 builds.
VALUE_MAP = "value_map"
NORMALIZE = "normalize"
COALESCE = "coalesce"
LINK = "link"
OWNER = "owner"

#: The column type that :func:`copy` fills.
_TEXT = "TEXT"

#: The empty core slots that a module can play (§18.9.6, the three kinds).
SLOTS: tuple[str, ...] = ("company", "contact", "lead", "deal")

#: The target ``name`` of a slot: the name of the record. It is not a column
#: of ``FIELD_RULE_TARGETS``, and rule 2 of the kinds names it.
NAME = RuleTarget("name", _TEXT)

#: The words of a stage, as rule 3 of the kinds names them.
STAGE_WORDS: tuple[str, ...] = ("won", "lost", "open")

#: The pseudo target of :func:`value_map`. A stage is no column of ours.
STAGE = RuleTarget("stage", _TEXT)

#: The columns that :func:`normalize` cleans, and the kind of each.
_NORMAL_KINDS: Mapping[str, str] = MappingProxyType({
    "email": "email", "phone": "phone", "mobile": "phone",
})

#: An email: one ``@``, no blank, and a dot in the domain.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

#: The digits of a phone number, from 6 to 15 (E.164 holds 15 at most).
_PHONE_DIGITS = (6, 15)


@dataclass(frozen=True)
class Outcome:
    """The result of one transform on one value.

    ``value`` is the column value, or ``None`` for NULL. ``unmapped`` is True
    when the source held a filled value and the transform could not turn it
    into a column value. A blank source value is never ``unmapped``.
    """

    value: Any
    unmapped: bool = False


#: A transform: the target column and one field value give an :class:`Outcome`.
Transform = Callable[[RuleTarget, Any], Outcome]


def blank(value: Any) -> bool:
    """True when *value* holds nothing to map.

    It agrees with the rule of "filled" of the fill rank (``fill.FILLED_SQL``):
    NULL, an empty or blank text, ``[]``, ``{}`` and ``false`` are blank. A
    Zoho checkbox sends ``false`` when nobody ticks it. The number 0 is filled.
    """
    if value is None or value is False:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, list | dict):
        return not value
    return False


def _outcome(raw: Any, value: Any) -> Outcome:
    return Outcome(value, unmapped=value is None and not blank(raw))


def copy(target: RuleTarget, value: Any) -> Outcome:
    """The text of *value*, for a ``TEXT`` column.

    The text rule is ``mapping.coerce_target_value``: a string loses its outer
    blanks, an empty one is NULL, and a list or an object is NULL. A target
    that is not ``TEXT`` raises ``ValueError``, because a copy changes no type.
    Use :func:`cast` for that target.
    """
    if target.column_type != _TEXT:
        raise ValueError(
            f"copy fills a TEXT column, and {target.column} is {target.column_type}",
        )
    return _outcome(value, mapping.coerce_target_value(target, value))


def cast(target: RuleTarget, value: Any) -> Outcome:
    """*value* as the type of the column, inside the limits of the target.

    The rule is ``mapping.coerce_target_value``. A value that does not coerce,
    or a number outside the limits, gives NULL and is ``unmapped``.
    """
    return _outcome(value, mapping.coerce_target_value(target, value))


@dataclass(frozen=True)
class ValueMap:
    """The spec of :func:`value_map`: each source value, as text, and its
    stage word (``won``, ``lost`` or ``open``)."""

    values: Mapping[str, str]


def value_map(target: RuleTarget, value: Any, *, spec: ValueMap) -> Outcome:
    """The stage word of *value*, from the map of a ``stage`` rule.

    The text rule of *value* is ``mapping.text_value``: a picklist value is a
    string, and its outer blanks go. A value with no entry gets no stage and
    is ``unmapped``, so the run can count it (CRM-Q20-1 item 9). An entry that
    is not a stage word gives no stage either. *target* is :data:`STAGE`.
    """
    text = mapping.text_value(value)
    if text is None:
        return Outcome(None, unmapped=not blank(value))
    word = spec.values.get(text)
    if word not in STAGE_WORDS:
        return Outcome(None, unmapped=True)
    return Outcome(word)


def _clean_phone(text: str) -> str | None:
    """The digits of a phone number, with its leading ``+``, or ``None``."""
    digits = "".join(ch for ch in text if ch.isdigit())
    low, high = _PHONE_DIGITS
    if not low <= len(digits) <= high:
        return None
    if any(ch.isalpha() for ch in text):
        return None
    return f"+{digits}" if text.lstrip().startswith("+") else digits


def normalize(target: RuleTarget, value: Any) -> Outcome:
    """An email in lower case, or a phone number as its digits.

    The target must be ``email``, ``phone`` or ``mobile``, else
    ``ValueError``. The text comes from ``mapping.coerce_target_value``, the
    one coercion. A filled value that is not an email or a phone number is
    ``unmapped``.
    """
    kind = _NORMAL_KINDS.get(target.column)
    if kind is None or target.column_type != _TEXT:
        raise ValueError(f"normalize cleans an email or a phone, not {target.column}")
    text = mapping.coerce_target_value(target, value)
    if text is None:
        return _outcome(value, None)
    if kind == "email":
        lowered = text.lower()
        return _outcome(value, lowered if _EMAIL_RE.match(lowered) else None)
    return _outcome(value, _clean_phone(text))


def coalesce(target: RuleTarget, value: Any) -> Outcome:
    """The first value of *value*, a sequence in the order of the rule, that
    :func:`cast` maps. A blank value is passed over. The result is
    ``unmapped`` when a filled value was there and none mapped."""
    values = value if isinstance(value, list | tuple) else (value,)
    missed = False
    for one in values:
        if blank(one):
            continue
        outcome = cast(target, one)
        if outcome.value is not None:
            return outcome
        missed = True
    return Outcome(None, unmapped=missed)


def link(target: RuleTarget, value: Any) -> Outcome:
    """The id of the parent that a Zoho lookup (``{"id", "name"}``) names, as
    text. A bare id is its own id. The target must be a ``TEXT`` column."""
    if target.column_type != _TEXT:
        raise ValueError(f"link fills a TEXT column, and {target.column} is {target.column_type}")
    return _outcome(value, mapping.lookup_id(value))


def owner(target: RuleTarget, value: Any) -> Outcome:
    """The email of the owner, in lower case, from a Zoho owner
    (``{"id", "name", "email"}``) or a bare address. The target must be a
    ``TEXT`` column. An owner with no valid email is ``unmapped``."""
    if target.column_type != _TEXT:
        raise ValueError(f"owner fills a TEXT column, and {target.column} is {target.column_type}")
    raw = value.get("email") if isinstance(value, Mapping) else value
    email = (mapping.text_value(raw) or "").lower()
    return _outcome(value, email if _EMAIL_RE.match(email) else None)


#: Each transform by its word: the two of CRM-Q19 and the five of CRM-Q20-1.
TRANSFORMS: Mapping[str, Callable[..., Outcome]] = MappingProxyType({
    COPY: copy,
    CAST: cast,
    VALUE_MAP: value_map,
    NORMALIZE: normalize,
    COALESCE: coalesce,
    LINK: link,
    OWNER: owner,
})

#: The words that a ``target`` item may name (§18.9.6, the kinds table).
TARGET_TRANSFORMS: tuple[str, ...] = (COPY, CAST, NORMALIZE, COALESCE, LINK, OWNER)


def transform_of(target: RuleTarget) -> str:
    """The transform of a ``field`` rule: ``copy`` for a ``TEXT`` column, and
    ``cast`` for each other type."""
    return COPY if target.column_type == _TEXT else CAST


def run(
    target: RuleTarget, value: Any, transform: str | None = None, *,
    spec: ValueMap | None = None,
) -> Outcome:
    """Run one transform on one value.

    *transform* is a word of :data:`TRANSFORMS`. With none, it is the word of
    :func:`transform_of`. An unknown word raises ``ValueError``.
    ``value_map`` needs its *spec*, and no other transform takes one.
    """
    word = transform or transform_of(target)
    function = TRANSFORMS.get(word)
    if function is None:
        raise ValueError(f"Unknown transform {word!r}")
    if word == VALUE_MAP:
        if spec is None:
            raise ValueError("value_map needs its spec")
        return function(target, value, spec=spec)
    return function(target, value)


# ── The kinds of CRM-Q20-1 ──────────────────────────────────────────────────


def slot_target(slot: str, column: str | None) -> RuleTarget | None:
    """The target *column* of *slot*, or ``None`` when rule 2 of the kinds
    does not allow it: a column of ``FIELD_RULE_TARGETS`` for the entity of
    the slot, or ``name``."""
    if slot not in SLOTS:
        return None
    if column == NAME.column:
        return NAME
    return mapping.rule_target(slot, column)


def slot_targets(slot: str) -> tuple[str, ...]:
    """Each legal target of *slot*: ``name``, then the columns of
    ``FIELD_RULE_TARGETS`` in their order."""
    if slot not in SLOTS:
        return ()
    return (NAME.column, *(mapping.FIELD_RULE_TARGETS.get(slot) or {}))


def target_problem(slot: Any, target: Any, fields: Any, transform: Any) -> str | None:
    """Why a ``target`` item is not legal (rule 2 of the kinds), or ``None``.

    The target is a legal target of the slot. ``fields`` is a list of field
    API names: two or more for ``coalesce``, one for each other word. The
    word is one of :data:`TARGET_TRANSFORMS`, and it fits the column: a
    ``copy``, a ``link`` and an ``owner`` fill a ``TEXT`` column, and a
    ``normalize`` fills ``email``, ``phone`` or ``mobile``.
    """
    column = slot_target(slot, target) if isinstance(slot, str) else None
    if column is None:
        return "unknown_target"
    if transform not in TARGET_TRANSFORMS:
        return "unknown_transform"
    if not isinstance(fields, list) or not all(isinstance(f, str) and f for f in fields):
        return "bad_fields"
    if len(fields) != len(set(fields)):
        return "bad_fields"
    if (len(fields) >= 2) != (transform == COALESCE) or not fields:
        return "bad_fields"
    if transform in (COPY, LINK, OWNER) and column.column_type != _TEXT:
        return "type_mismatch"
    if transform == NORMALIZE and column.column not in _NORMAL_KINDS:
        return "type_mismatch"
    return None


def stage_problem(field_api_name: Any, values: Any) -> str | None:
    """Why a ``stage`` item is not legal (rule 3 of the kinds), or ``None``.
    The field is named, and each value maps to ``won``, ``lost`` or
    ``open``."""
    if not isinstance(field_api_name, str) or not field_api_name:
        return "bad_field"
    if not isinstance(values, Mapping):
        return "bad_values"
    for source, word in values.items():
        if not isinstance(source, str) or word not in STAGE_WORDS:
            return "bad_values"
    return None


def run_target(slot: str, item: Mapping[str, Any], fields: Mapping[str, Any]) -> Outcome:
    """Run one ``target`` item on the fields of one module record.

    *fields* is ``crm_module_records.fields``. A ``coalesce`` reads each field
    of the item in order, and each other word reads its one field. An item
    that is not legal raises ``ValueError``.
    """
    problem = target_problem(slot, item.get("target"), item.get("fields"), item.get("transform"))
    if problem is not None:
        raise ValueError(f"The target item is not legal: {problem}")
    column = slot_target(slot, item["target"])
    assert column is not None  # target_problem checked it
    names: Sequence[str] = item["fields"]
    word = item["transform"]
    value = tuple(fields.get(n) for n in names) if word == COALESCE else fields.get(names[0])
    return run(column, value, word)


def run_stage(item: Mapping[str, Any], fields: Mapping[str, Any]) -> Outcome:
    """Run one ``stage`` item on the fields of one module record: the stage
    word of the value of its field. An item that is not legal raises
    ``ValueError``."""
    problem = stage_problem(item.get("field_api_name"), item.get("values"))
    if problem is not None:
        raise ValueError(f"The stage item is not legal: {problem}")
    return run(STAGE, fields.get(item["field_api_name"]), VALUE_MAP,
               spec=ValueMap(dict(item["values"])))


def apply_rules(
    entity: str,
    fields: Mapping[str, Any],
    values: dict[str, Any],
    rules: Mapping[str, str],
) -> tuple[str, ...]:
    """Apply the ``field`` rules of *entity* to one record, in place.

    *fields* holds the provider fields of the record, by API name. *values*
    holds its column values. *rules* is ``{target_column: field_api_name}``.

    A rule fills its column only where *values* gives NULL, so a value of the
    fixed map wins (CRM-Q7a, also for a column that an empty fill opened,
    CRM-Q18-3). A column that is not a legal target is skipped. Returns the
    columns whose value was ``unmapped``, in the order of *rules*.
    """
    unmapped: list[str] = []
    for column, field_api_name in rules.items():
        target = mapping.rule_target(entity, column)
        if target is None or values.get(column) is not None:
            continue
        outcome = run(target, fields.get(field_api_name))
        values[column] = outcome.value
        if outcome.unmapped:
            unmapped.append(column)
    return tuple(unmapped)


def preview_record(
    entity: str,
    values: dict[str, Any],
    custom: Mapping[str, Any],
    live: Mapping[str, str],
    draft: Mapping[str, str],
) -> tuple[str, ...]:
    """Apply the ``field`` rules of a draft to one STORED record, in place.

    The preview of a draft (``POST /crm/mapping/preview``) calls it. *values*
    holds the stored columns of the record, and *custom* its ``custom``
    object. *live* holds the approved rules that the sync applies now, and
    *draft* the rules of the draft, each as ``{target_column:
    field_api_name}``.

    First each column of a live rule or a draft rule goes back to the value
    of the fixed map, as far as the stored row shows it:

    * a free column (no standard field) is NULL, because the fixed map never
      writes it;
    * a fixed column that a live rule fills is NULL, because a live rule
      writes only where the fixed map gave NULL;
    * any other fixed column keeps its stored value, the value of the fixed
      map.

    Then :func:`apply_rules` applies *draft*, so the preview runs the one
    path of a rule value. Returns the columns whose value was ``unmapped``.

    A fixed column that a live rule fills can also hold a value that Zoho
    sent later, for a rule that an empty fill opened (CRM-Q18-3). The sync
    keeps that value, because the fixed map wins. So the column goes back to
    NULL only while its stored value is the value that the live rule gives
    from *custom*. A stored value that differs is the value of the fixed map,
    and it stays (Batch M review, P2).
    """
    for column in (*live, *draft):
        target = mapping.rule_target(entity, column)
        if target is None:
            continue
        free = target.standard_field is None
        if free or (column in live and _is_rule_value(
                target, values.get(column), custom, live[column])):
            values[column] = None
    return apply_rules(entity, custom, values, draft)


def _is_rule_value(
    target: RuleTarget, stored: Any, custom: Mapping[str, Any], field_api_name: str,
) -> bool:
    """True when *stored* is the value that a live rule gives from *custom*,
    or NULL. Then the live rule wrote it, and not the fixed map."""
    if stored is None:
        return True
    return same_value(run(target, custom.get(field_api_name)).value, stored)


def same_value(a: Any, b: Any) -> bool:
    """Equal as the column holds them. A number compares through its text as
    a ``Decimal``: the transform gives a ``float`` for a NUMERIC column, and
    the database gives a ``Decimal``, and ``Decimal('1234.56') == 1234.56``
    is False (round 2 review, P2). Each other value compares as it is."""
    numbers = (int, float, Decimal)
    if (isinstance(a, numbers) and isinstance(b, numbers)
            and not isinstance(a, bool) and not isinstance(b, bool)):
        try:
            return Decimal(str(a)) == Decimal(str(b))
        except InvalidOperation:
            return False
    return a == b


__all__ = [
    "CAST",
    "COALESCE",
    "COPY",
    "LINK",
    "NAME",
    "NORMALIZE",
    "OWNER",
    "SLOTS",
    "STAGE",
    "STAGE_WORDS",
    "TARGET_TRANSFORMS",
    "TRANSFORMS",
    "VALUE_MAP",
    "Outcome",
    "Transform",
    "ValueMap",
    "apply_rules",
    "blank",
    "cast",
    "coalesce",
    "copy",
    "link",
    "normalize",
    "owner",
    "preview_record",
    "run",
    "run_stage",
    "run_target",
    "slot_target",
    "slot_targets",
    "stage_problem",
    "target_problem",
    "transform_of",
    "value_map",
]
