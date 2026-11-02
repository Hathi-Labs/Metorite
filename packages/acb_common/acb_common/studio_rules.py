"""The constraints of a Studio object: the grammar and the pure checks (WS-STU C1b).

Spec: ``project-docs/specs/ai_studio.md`` §3.1 and the §14 C1b row.

**Pure.** No gateway import, no SQL and no I/O. ``objects_store.py`` holds the
SQL, and it stays the one module with SQL of the object tables (the fence
``test_one_writer_of_the_four_tables``). C6 and C7 reuse
:class:`RuleViolation` and :func:`touched_keys`.

**The shape.** A rule is a JSON object with a ``name``, a ``kind`` and an
optional ``message``. An object holds at most :data:`MAX_RULES_PER_OBJECT`
rules. :data:`RULE_KINDS` is the closed list of kinds.

* ``range``: ``{field, min?, max?}``. One number field of one record, with at
  least one bound. Non-negative is ``min`` 0. A record with no value passes.
* ``unique``: ``{fields: [<one hot field>]}``. An empty value never clashes.
* ``group_sum_min``: ``{field, group_by: [<1 or 2 hot fields>], min}``. The
  sum of a hot number field over the records with the same group values
  stays at least ``min``. "Stock never below zero" is this kind over a ledger.
  A record with no value in a group field joins no group.

**A key.** A ``unique`` field and a group field have a type of
:data:`KEY_TYPES`. A checkbox has two states and a money value has a currency,
so neither is a key. :func:`touched_keys` answers the keys of one written
record, and the store checks each key once.

**A group.** A key with no rows passes, because a group that no longer exists
holds nothing to break. A key with rows and no amount sums to 0, and the floor
judges it. On a floor of 0 or less, a batch that does not lower a group never
reads the group: :func:`add_change` gives its exact net change on one key, and
:func:`floor_holds_at_zero` names the floors that may skip the read. A floor
above 0 always reads its group.

**Two checks of a declaration.** :func:`parse_rules` checks the shape.
:func:`check_rules_fit` checks the rules against the fields of the object.
The store runs the first before any statement, and the second after it locks
the object.

**A refusal never echoes a value**, because a record holds member data. It
names the rule, the object, the field keys and the declared bounds.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from decimal import Context, Decimal
from typing import Any

from acb_common.studio_fields import (
    NAME_PATTERN,
    FieldError,
    FieldSpec,
    check_value,
    is_blank,
    storable_text,
)

# --- the caps ---------------------------------------------------------------

#: The rules of one object.
MAX_RULES_PER_OBJECT = 16
#: The writes of one batch.
MAX_WRITES_PER_BATCH = 50
MAX_RULE_MESSAGE_CHARS = 200
#: A ``group_sum_min`` groups by one or two fields.
MAX_GROUP_FIELDS = 2

RANGE = "range"
UNIQUE = "unique"
GROUP_SUM_MIN = "group_sum_min"

#: The closed list of kinds.
RULE_KINDS: tuple[str, ...] = (RANGE, UNIQUE, GROUP_SUM_MIN)
#: The kind of a stored rule that the checks cannot read. It is not in
#: :data:`RULE_KINDS`, so :func:`check_kind_known` refuses each write.
MALFORMED = "malformed"

#: The field types of a ``unique`` field and of a group field.
KEY_TYPES: tuple[str, ...] = ("text", "email", "select", "number", "date", "datetime")

_COMMON_KEYS = frozenset({"name", "kind", "message"})
_KIND_KEYS: Mapping[str, frozenset[str]] = {
    RANGE: frozenset({"field", "min", "max"}),
    UNIQUE: frozenset({"fields"}),
    GROUP_SUM_MIN: frozenset({"field", "group_by", "min"}),
}
assert tuple(_KIND_KEYS) == RULE_KINDS, "each kind has its keys"

_NAME_RE = re.compile(NAME_PATTERN, re.ASCII)
#: A bound runs through the one check of a number value.
_BOUND = FieldSpec(key="bound", type="number")
#: The default context rounds to 28 digits. A value holds 15 digits before the
#: point and up to 20 after it, and a batch adds at most 100 of them.
_EXACT = Context(prec=64)


class ConstraintError(ValueError):
    """One refused declaration: the rule name and one sentence."""

    def __init__(self, rule: str, message: str) -> None:
        super().__init__(message)
        self.rule = rule
        self.message = message


class RuleViolation(ValueError):
    """A write that a rule refuses. C6 and C7 reuse this base.

    ``rule`` is the name of the rule. The message names the rule.
    """

    def __init__(self, rule: str, message: str) -> None:
        super().__init__(message)
        self.rule = rule
        self.message = message


class ConstraintViolation(RuleViolation):
    """A write that breaks a constraint of an object. ``object`` is its name."""

    def __init__(self, rule: str, object_name: str, message: str) -> None:
        super().__init__(rule, message)
        self.object = object_name


@dataclass(frozen=True)
class Rule:
    """One constraint of an object, after :func:`parse_rules`."""

    name: str
    kind: str
    message: str | None = None
    #: The number field of ``range`` and of ``group_sum_min``.
    field: str | None = None
    #: The key fields: the one field of ``unique``, the group of ``group_sum_min``.
    keys: tuple[str, ...] = ()
    min: int | float | None = None
    max: int | float | None = None

    def stored(self) -> dict[str, Any]:
        """The JSON of this rule, as the store keeps it."""
        out: dict[str, Any] = {"name": self.name, "kind": self.kind}
        if self.message is not None:
            out["message"] = self.message
        if self.kind == RANGE:
            out["field"] = self.field
        if self.kind == UNIQUE:
            out["fields"] = list(self.keys)
        if self.kind == GROUP_SUM_MIN:
            out["field"] = self.field
            out["group_by"] = list(self.keys)
        if self.min is not None:
            out["min"] = self.min
        if self.max is not None:
            out["max"] = self.max
        return out

    def hot_keys(self) -> tuple[str, ...]:
        """The fields that must stay hot while this rule stands."""
        if self.kind == RANGE:
            return ()
        return (*self.keys, self.field) if self.field is not None else self.keys


# --- the declaration --------------------------------------------------------


def _name(spec: Any) -> str:
    """The name to quote in a refusal: the name when it has the name shape."""
    raw = spec.get("name") if isinstance(spec, Mapping) else None
    return raw if isinstance(raw, str) and _NAME_RE.fullmatch(raw) else "?"


def _bound(rule: str, key: str, value: Any) -> int | float:
    try:
        checked: int | float = check_value(_BOUND, value)
    except FieldError:
        raise ConstraintError(
            rule, f"Rule `{rule}`: `{key}` is a number below 10^15, written with no exponent."
        ) from None
    return checked


def _key(rule: str, label: str, value: Any) -> str:
    if not isinstance(value, str) or not _NAME_RE.fullmatch(value):
        raise ConstraintError(rule, f"Rule `{rule}`: `{label}` names a field by its key.")
    return value


def _keys(rule: str, label: str, value: Any, most: int) -> tuple[str, ...]:
    if not isinstance(value, list) or not 1 <= len(value) <= most:
        count = "one field" if most == 1 else f"1 to {most} fields"
        raise ConstraintError(rule, f"Rule `{rule}`: `{label}` is a list of {count}.")
    keys = tuple(_key(rule, label, v) for v in value)
    if len(set(keys)) != len(keys):
        raise ConstraintError(rule, f"Rule `{rule}`: `{label}` names a field twice.")
    return keys


def _message(rule: str, value: Any) -> str | None:
    if value is None:
        return None
    if (not isinstance(value, str) or not value.strip() or len(value) > MAX_RULE_MESSAGE_CHARS
            or not storable_text(value)):
        raise ConstraintError(
            rule, f"Rule `{rule}`: `message` is text of 1 to {MAX_RULE_MESSAGE_CHARS} characters.")
    return value


def _parse_range(rule: str, spec: Mapping[str, Any], message: str | None) -> Rule:
    low, high = spec.get("min"), spec.get("max")
    if low is None and high is None:
        raise ConstraintError(rule, f"Rule `{rule}`: a range needs `min`, `max` or both.")
    low = None if low is None else _bound(rule, "min", low)
    high = None if high is None else _bound(rule, "max", high)
    if low is not None and high is not None and low > high:
        raise ConstraintError(rule, f"Rule `{rule}`: `min` is above `max`.")
    return Rule(rule, RANGE, message, field=_key(rule, "field", spec.get("field")), min=low, max=high)


def _parse_unique(rule: str, spec: Mapping[str, Any], message: str | None) -> Rule:
    return Rule(rule, UNIQUE, message, keys=_keys(rule, "fields", spec.get("fields"), 1))


def _parse_group(rule: str, spec: Mapping[str, Any], message: str | None) -> Rule:
    field = _key(rule, "field", spec.get("field"))
    keys = _keys(rule, "group_by", spec.get("group_by"), MAX_GROUP_FIELDS)
    if field in keys:
        raise ConstraintError(rule, f"Rule `{rule}`: `field` cannot also be in `group_by`.")
    if spec.get("min") is None:
        raise ConstraintError(rule, f"Rule `{rule}`: a group_sum_min needs `min`.")
    return Rule(rule, GROUP_SUM_MIN, message, field=field, keys=keys,
                min=_bound(rule, "min", spec.get("min")))


_PARSERS = {RANGE: _parse_range, UNIQUE: _parse_unique, GROUP_SUM_MIN: _parse_group}
assert tuple(_PARSERS) == RULE_KINDS, "each kind has one parser"


def parse_rule(spec: Any) -> Rule:
    """One rule as a :class:`Rule`. Raises :class:`ConstraintError`."""
    rule = _name(spec)
    if not isinstance(spec, Mapping):
        raise ConstraintError(rule, "A rule is an object with a name and a kind.")
    if rule == "?":
        raise ConstraintError(
            rule, "A rule name is lower-case letters, digits and _, a letter first, at most 48.")
    kind = spec.get("kind")
    if not isinstance(kind, str) or kind not in RULE_KINDS:
        raise ConstraintError(rule, f"Rule `{rule}`: the kind is {', '.join(RULE_KINDS)}.")
    extra = sorted(str(k) for k in spec if k not in _COMMON_KEYS | _KIND_KEYS[kind])
    if extra:
        safe = extra[0] if _NAME_RE.fullmatch(extra[0]) else "?"
        raise ConstraintError(rule, f"Rule `{rule}`: `{safe}` is not a key of a {kind} rule.")
    return _PARSERS[kind](rule, spec, _message(rule, spec.get("message")))


def parse_rules(specs: Any) -> tuple[Rule, ...]:
    """The rules of one object, with the checks of each one and of the list."""
    if not isinstance(specs, Sequence) or isinstance(specs, str | bytes):
        raise ConstraintError("?", "The rules of an object are a list.")
    if len(specs) > MAX_RULES_PER_OBJECT:
        raise ConstraintError("?", f"An object holds at most {MAX_RULES_PER_OBJECT} rules.")
    rules = tuple(parse_rule(s) for s in specs)
    seen: set[str] = set()
    for rule in rules:
        if rule.name in seen:
            raise ConstraintError(rule.name, f"Rule `{rule.name}`: each rule name must be unique.")
        seen.add(rule.name)
    return rules


def _number_field(rule: Rule, field: FieldSpec) -> None:
    if field.type != "number" or field.many:
        raise ConstraintError(
            rule.name, f"Rule `{rule.name}`: field `{field.key}` must be one number. "
            "A money field takes no rule yet.")


def _key_field(rule: Rule, field: FieldSpec) -> None:
    if field.type not in KEY_TYPES or field.many:
        raise ConstraintError(
            rule.name, f"Rule `{rule.name}`: field `{field.key}` cannot be a key. "
            f"Use one value of {', '.join(KEY_TYPES)}.")


def _hot(rule: Rule, field: FieldSpec) -> None:
    if not field.hot:
        raise ConstraintError(
            rule.name, f"Rule `{rule.name}`: field `{field.key}` must be hot. Turn it hot first.")


def check_rules_fit(rules: Sequence[Rule], fields: Sequence[FieldSpec]) -> None:
    """Refuse a rule that does not fit the *fields* of the object.

    Each named field must exist, and each has the type of its place. A field
    of ``unique`` and of ``group_sum_min`` must be hot. This check never turns
    a field hot. It raises :class:`ConstraintError`.
    """
    by_key = {f.key: f for f in fields}

    def named(rule: Rule, key: str) -> FieldSpec:
        field = by_key.get(key)
        if field is None:
            raise ConstraintError(rule.name, f"Rule `{rule.name}`: `{key}` is not a field of this object.")
        return field

    for rule in rules:
        if rule.field is not None:
            field = named(rule, rule.field)
            _number_field(rule, field)
            if rule.kind == GROUP_SUM_MIN:
                _hot(rule, field)
        for key in rule.keys:
            field = named(rule, key)
            _key_field(rule, field)
            _hot(rule, field)


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _is_key_list(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(k, str) for k in value)


def _can_run(item: Mapping[str, Any]) -> bool:
    """True when the checks of a write can read the stored rule *item*. This is
    the shape that they need, and no authoring check. A kind that this code
    does not know passes here, and it keeps its name."""
    kind = item.get("kind")
    field_named = isinstance(item.get("field"), str)
    if kind == RANGE:
        low, high = item.get("min"), item.get("max")
        return (field_named and (low is not None or high is not None)
                and all(v is None or _is_number(v) for v in (low, high)))
    if kind == UNIQUE:
        return _is_key_list(item.get("fields"))
    if kind == GROUP_SUM_MIN:
        return field_named and _is_key_list(item.get("group_by")) and _is_number(item.get("min"))
    return isinstance(kind, str)


def _stored_rule(item: Any) -> Rule:
    name = item.get("name") if isinstance(item, Mapping) else None
    if not isinstance(name, str) or not name:
        return Rule("?", MALFORMED)
    if not _can_run(item):
        return Rule(name, MALFORMED)
    keys = item.get("fields") if item.get("kind") == UNIQUE else item.get("group_by")
    return Rule(
        name=name, kind=item["kind"],
        message=item.get("message") if isinstance(item.get("message"), str) else None,
        field=item.get("field") if isinstance(item.get("field"), str) else None,
        keys=tuple(keys) if isinstance(keys, list) else (),
        min=item.get("min"), max=item.get("max"))


def stored_rules(raw: Any) -> tuple[Rule, ...]:
    """The :class:`Rule` list from the JSON that the store keeps.

    ⚠️ It runs no authoring check, as ``studio_fields.stored_field`` does. A
    rule that a later slice makes stricter binds a new declaration only, and a
    read of an object never fails on a stored rule. A kind that this code does
    not know stays in the list. An item that the checks cannot read becomes a
    rule of the kind :data:`MALFORMED`. Each of these fails closed:
    :func:`check_kind_known` refuses each write of the object until a new
    declaration replaces the list. A rule must never pass a write because the
    store could not read it.
    """
    if not isinstance(raw, list):
        return (Rule("?", MALFORMED),)
    return tuple(_stored_rule(item) for item in raw)


# --- the checks of a write --------------------------------------------------


def violation(rule: Rule, object_name: str, default: str) -> ConstraintViolation:
    """The refusal of *rule*. The message names the rule and never a value."""
    return ConstraintViolation(
        rule.name, object_name,
        f"Rule `{rule.name}` of object `{object_name}`: {rule.message or default}")


def check_kind_known(rule: Rule, object_name: str) -> None:
    """Fail closed. A stored rule of an unknown kind, or one that the checks
    cannot read, refuses the write."""
    if rule.kind not in RULE_KINDS:
        raise violation(
            rule, object_name, "The store cannot check this rule. Declare the rules of the object again.")


def check_rule_is_hot(rule: Rule, object_name: str, fields: Sequence[FieldSpec]) -> None:
    """Fail closed. A rule on a field with no hot rows would pass each write.
    The store keeps a field hot while a rule reads it, so only raw SQL gets here."""
    hot = {f.key for f in fields if f.hot}
    if not set(rule.hot_keys()) <= hot:
        raise violation(rule, object_name, "A field of this rule is not hot, so the rule cannot run.")


def check_range(rule: Rule, object_name: str, data: Mapping[str, Any]) -> None:
    """Refuse a value of the record *data* outside the bounds of a ``range``."""
    value = data.get(rule.field or "")
    if value is None:
        return
    if rule.min is not None and value < rule.min:
        raise violation(rule, object_name, f"`{rule.field}` must be at least {rule.min}.")
    if rule.max is not None and value > rule.max:
        raise violation(rule, object_name, f"`{rule.field}` must be at most {rule.max}.")


def check_unique_count(rule: Rule, object_name: str, count: int) -> None:
    """*count* records hold the key after the batch, and the record itself is one."""
    if count > 1:
        raise violation(rule, object_name, f"another record holds this value of `{rule.keys[0]}`.")


def check_group_sum(rule: Rule, object_name: str, total: Decimal | None) -> None:
    """Refuse a *total* below the ``min`` of a ``group_sum_min``. None means
    that the key has no rows. A group that no longer exists holds nothing to
    break, so it passes at any ``min``. A key with rows and no amount gives 0."""
    if total is None:
        return
    # repr gives the digits that json.dumps wrote, so the two compare exactly.
    floor = Decimal(repr(rule.min))
    if total < floor:
        group = ", ".join(rule.keys)
        raise violation(
            rule, object_name, f"the sum of `{rule.field}` for each `{group}` must stay at least {rule.min}.")


def _key_of(rule: Rule, data: Mapping[str, Any]) -> tuple[Any, ...] | None:
    """The key of the record *data*. None when a key field holds no value."""
    values = tuple(data.get(k) for k in rule.keys)
    return None if any(is_blank(v) for v in values) else values


def touched_keys(rule: Rule, before: Mapping[str, Any] | None,
                 after: Mapping[str, Any] | None) -> list[tuple[Any, ...]]:
    """The keys of *rule* that one written record touches, each one once.

    *before* is the record before the batch (None for a create), and *after*
    is the record after it (None for a delete). A ``group_sum_min`` key is
    touched by the old and the new key. A ``unique`` key is touched by the new
    key only: a delete or an old key cannot add a clash, so a check there
    would only stop the repair of old duplicates. C6 and C7 reuse this hook.
    """
    if rule.kind == RANGE:
        return []
    states: Collection[Mapping[str, Any] | None] = (
        (after,) if rule.kind == UNIQUE else (before, after))
    out: list[tuple[Any, ...]] = []
    for data in states:
        key = None if data is None else _key_of(rule, data)
        if key is not None and key not in out:
            out.append(key)
    return out


def floor_holds_at_zero(rule: Rule) -> bool:
    """True for a ``group_sum_min`` with a ``min`` of 0 or less.

    A group with no rows has a sum of 0, and this floor holds at 0. So a batch
    that does not lower a group cannot break it, and the store need not read
    the group. A floor above 0 has no such start: a new group of 0.5 breaks a
    ``min`` of 1. So a floor above 0 always reads its group.
    """
    return rule.kind == GROUP_SUM_MIN and rule.min is not None and rule.min <= 0


def _share(rule: Rule, key: tuple[Any, ...], data: Mapping[str, Any] | None) -> Decimal:
    """What the record *data* adds to the sum of *key*."""
    if data is None or _key_of(rule, data) != key:
        return Decimal(0)
    value = data.get(rule.field or "")
    # repr gives the digits that json.dumps wrote, as `check_group_sum` reads them.
    return Decimal(0) if value is None else Decimal(repr(value))


def add_change(rule: Rule, key: tuple[Any, ...], before: Mapping[str, Any] | None,
               after: Mapping[str, Any] | None, net: Decimal = Decimal(0)) -> Decimal:
    """*net*, plus the change that one written record makes to the sum of *key*.

    It reads only the values of the record, and it is exact. On a floor of
    :func:`floor_holds_at_zero`, a batch whose net change on a key is not
    negative cannot break the group, so the store does not read the group.
    """
    return _EXACT.add(net, _EXACT.subtract(_share(rule, key, after), _share(rule, key, before)))


def key_filter(rule: Rule, key: tuple[Any, ...]) -> list[dict[str, Any]]:
    """The filter of the query grammar that finds the records of *key*."""
    return [{"field": k, "op": "eq", "value": v} for k, v in zip(rule.keys, key, strict=True)]
