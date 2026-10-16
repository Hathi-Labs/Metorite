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

**How CRM-Q20-1 extends it.** CRM-Q20-1 adds ``value_map``, ``normalize``,
``coalesce``, ``link`` and ``owner``. Each one is a pure function with the
shape of :data:`Transform`, and each one joins :data:`TRANSFORMS` under its
word. A transform that needs more than one value (``coalesce``) or a
parameter (``value_map``) takes a small frozen spec as a keyword argument.
The kinds of rule that call them go through :func:`run` with their
``transform`` word. A ``field`` rule names no word, so :func:`transform_of`
chooses ``copy`` or ``cast`` from the type of the column.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
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

#: The column type that :func:`copy` fills.
_TEXT = "TEXT"


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


#: Each transform by its word. CRM-Q20-1 adds its five words here.
TRANSFORMS: Mapping[str, Transform] = MappingProxyType({COPY: copy, CAST: cast})


def transform_of(target: RuleTarget) -> str:
    """The transform of a ``field`` rule: ``copy`` for a ``TEXT`` column, and
    ``cast`` for each other type."""
    return COPY if target.column_type == _TEXT else CAST


def run(target: RuleTarget, value: Any, transform: str | None = None) -> Outcome:
    """Run one transform on one value.

    *transform* is a word of :data:`TRANSFORMS`. With none, it is the word of
    :func:`transform_of`. An unknown word raises ``ValueError``.
    """
    word = transform or transform_of(target)
    function = TRANSFORMS.get(word)
    if function is None:
        raise ValueError(f"Unknown transform {word!r}")
    return function(target, value)


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

    Limit: a live rule on a fixed column that an empty fill opened (CRM-Q18-3)
    can share the column with a value that Zoho sent later. The stored row
    cannot tell the two apart, so the preview shows the draft value there.
    """
    for column in (*live, *draft):
        target = mapping.rule_target(entity, column)
        if target is None:
            continue
        if target.standard_field is None or column in live:
            values[column] = None
    return apply_rules(entity, custom, values, draft)


__all__ = [
    "CAST",
    "COPY",
    "TRANSFORMS",
    "Outcome",
    "Transform",
    "apply_rules",
    "blank",
    "cast",
    "copy",
    "preview_record",
    "run",
    "transform_of",
]
