"""The constraints of a Studio object: the grammar and the pure checks (WS-STU C1b).

Spec: ``project-docs/specs/ai_studio.md`` §3.1 and the §14 C1b row.

**Pure.** No gateway import, no SQL and no I/O. ``objects_store.py`` holds the
SQL, and it stays the one module with SQL of the object tables (the fence
``test_one_writer_of_the_four_tables``). C6 and C7a reuse
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
* ``state_machine`` (C6): ``{field, initial, moves: [{from, to, guard?}]}``.
  One select field moves only along the declared moves. A guard is a closed
  list of at most 4 atoms ``{field, op, value?}``, and all must hold. It reads
  the merged record only. A role guard waits for C8, a history row for C4, and
  an expression for C7. A machine reads no key, so :func:`touched_keys` answers
  none for it, and :func:`check_move` judges the net move of one record.
* ``formula`` (C7a): ``{target, expr, scale?, rounding?, currency?}``. The store
  computes the field *target* on each write, from the other fields of the same
  record, with the closed tree of ``studio_expr.py``. The result is stored, so a
  read never computes it, and a rule of C1b or C6 reads the stored result. A
  caller cannot write a target, and any key for it is refused, also with the same
  value. A number target names ``rounding`` and may name a ``scale`` of 0 to 4.
  A money target names ``currency``. :func:`plan_formulas` checks the formulas of
  an object as a set, and :func:`run_formulas` runs them in order of dependency.
  A formula reads no key, so :func:`touched_keys` answers none for it.
* ``rollup`` (C7b): ``{target, source, via, agg, field?, where?, currency?}``. The
  store fills the field *target* of a parent record with the sum, count, min or
  max of a hot field of the child records of the object *source* of the same
  package, one hop. A child names its parent in the hot relation field *via*,
  whose ``to`` is the bare name of this object. ``where`` is at most 2 C2a
  conditions on hot fields of the source. The store recomputes each touched
  parent after the writes of a batch, so a read is exact. A caller cannot write a
  target. A roll-up reads no key of its own object, so :func:`touched_keys`
  answers none for it. The fields it reads are fields of the *source*
  (:meth:`Rule.source_keys`), and the store keeps them hot.
  :func:`check_rollups` checks a declaration against the source, and
  :func:`rollup_parents` and :func:`rollup_result` serve the recompute.

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

import math
import re
import uuid
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Context, Decimal
from typing import Any

from acb_common import studio_expr
from acb_common.studio_expr import ExprError, Node
from acb_common.studio_fields import (
    MAX_OPTION_CHARS,
    MAX_TEXT_CHARS,
    NAME_PATTERN,
    FieldError,
    FieldSpec,
    check_value,
    is_blank,
    relation_key,
    storable_text,
)
from acb_common.studio_query import MAX_IN_VALUES, OPS, OPS_BY_TYPE, QueryError, parse_query

# --- the caps ---------------------------------------------------------------

#: The rules of one object.
MAX_RULES_PER_OBJECT = 16
#: The writes of one batch.
MAX_WRITES_PER_BATCH = 50
MAX_RULE_MESSAGE_CHARS = 200
#: A ``group_sum_min`` groups by one or two fields.
MAX_GROUP_FIELDS = 2
#: The moves of one state machine.
MAX_MOVES = 64
#: The atoms of one guard. All of them must hold.
MAX_GUARD_ATOMS = 4
#: The values of an ``in`` atom.
MAX_GUARD_IN_VALUES = 20
#: The computed rules of one object (formulas and roll-ups), inside :data:`MAX_RULES_PER_OBJECT`.
MAX_COMPUTED_RULES = 8
#: The conditions of the ``where`` of one roll-up.
MAX_ROLLUP_WHERE = 2
#: The parents that one batch recomputes, and the aggregate statements it runs.
MAX_ROLLUP_PARENTS = 100
MAX_ROLLUP_STATEMENTS = 100

#: The closed ops of a guard atom. All but ``not_empty`` are ops of C2a.
GUARD_OPS: tuple[str, ...] = (
    "eq", "ne", "lt", "lte", "gt", "gte", "in", "is_empty", "not_empty")
_ORDER_OPS = frozenset({"lt", "lte", "gt", "gte"})
_NO_VALUE_OPS = frozenset({"is_empty", "not_empty"})
#: A role guard waits for C8, which gives the store the roles of the actor.
_ROLE_KEYS = frozenset({"role", "roles"})

RANGE = "range"
UNIQUE = "unique"
GROUP_SUM_MIN = "group_sum_min"
STATE_MACHINE = "state_machine"
FORMULA = "formula"
ROLLUP = "rollup"

#: The closed list of kinds.
RULE_KINDS: tuple[str, ...] = (RANGE, UNIQUE, GROUP_SUM_MIN, STATE_MACHINE, FORMULA, ROLLUP)
#: The aggregates of a roll-up.
ROLLUP_AGGS: tuple[str, ...] = ("sum", "count", "min", "max")
#: The field types that a roll-up reads, for each aggregate. ``count`` reads none.
ROLLUP_FIELD_TYPES: Mapping[str, tuple[str, ...]] = {
    "sum": ("number", "money"), "min": ("number", "money", "date"),
    "max": ("number", "money", "date")}
#: The code of a roll-up that reads a roll-up (also through a formula).
ROLLUP_OF_ROLLUP = "rollup_of_rollup"
#: The kind of a stored rule that the checks cannot read. It is not in
#: :data:`RULE_KINDS`, so :func:`check_kind_known` refuses each write.
MALFORMED = "malformed"

#: The field types that a formula can fill, and the static type of a tree for each.
FORMULA_TARGET_TYPES: Mapping[str, str] = {
    "number": "number", "money": "money", "text": "text", "checkbox": "bool", "date": "date"}
#: The code of a refusal of a caller that writes a computed field.
COMPUTED_FIELD = "computed_field"

#: The field types of a ``unique`` field and of a group field.
KEY_TYPES: tuple[str, ...] = ("text", "email", "select", "number", "date", "datetime")

_COMMON_KEYS = frozenset({"name", "kind", "message"})
_KIND_KEYS: Mapping[str, frozenset[str]] = {
    RANGE: frozenset({"field", "min", "max"}),
    UNIQUE: frozenset({"fields"}),
    GROUP_SUM_MIN: frozenset({"field", "group_by", "min"}),
    STATE_MACHINE: frozenset({"field", "initial", "moves"}),
    FORMULA: frozenset({"target", "expr", "scale", "rounding", "currency"}),
    ROLLUP: frozenset({"target", "source", "via", "agg", "field", "where", "currency"}),
}
assert tuple(_KIND_KEYS) == RULE_KINDS, "each kind has its keys"

_NAME_RE = re.compile(NAME_PATTERN, re.ASCII)
_CURRENCY_RE = re.compile(r"[A-Z]{3}", re.ASCII)
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


class FormulaError(RuleViolation):
    """A write that a formula refuses (C7a). ``object`` is the object name and
    ``code`` is a code of ``studio_expr``, or ``computed_field`` for a caller that
    writes a target. The message never echoes a value."""

    def __init__(self, rule: str, object_name: str, code: str, message: str) -> None:
        super().__init__(rule, message)
        self.object = object_name
        self.code = code


class RollupError(RuleViolation):
    """A write that a roll-up refuses (C7b). ``object`` is the name of the object
    that holds the roll-up and ``code`` is ``currency_mismatch``,
    ``result_too_large``, ``declaration`` or ``rollup_of_rollup``. The message
    never echoes a value."""

    def __init__(self, rule: str, object_name: str, code: str, message: str) -> None:
        super().__init__(rule, message)
        self.object = object_name
        self.code = code


class MoveViolation(RuleViolation):
    """A write that a state machine refuses (C6). ``from_state`` and
    ``to_state`` are declared states, or None when the state is not one. The
    message never echoes a guard value."""

    def __init__(self, rule: str, object_name: str, from_state: str | None,
                 to_state: str | None, message: str) -> None:
        super().__init__(rule, message)
        self.object = object_name
        self.from_state = from_state
        self.to_state = to_state


@dataclass(frozen=True)
class Atom:
    """One test of a guard on the merged record. ``value`` is a tuple for ``in``."""

    field: str
    op: str
    value: Any = None


@dataclass(frozen=True)
class Move:
    """One allowed move of a state machine, with the guard that must hold."""

    from_state: str
    to_state: str
    guard: tuple[Atom, ...] = ()


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
    #: A ``state_machine`` starts here, and it allows only these moves.
    initial: str | None = None
    moves: tuple[Move, ...] = ()
    #: A ``formula`` fills ``target`` from ``expr``. ``scale`` and ``rounding``
    #: are of a number target, and ``currency`` is of a money target.
    target: str | None = None
    expr: Node | None = None
    scale: int | None = None
    rounding: str | None = None
    currency: str | None = None
    #: A ``rollup`` fills ``target`` from the records of the object ``source``,
    #: found through the hot relation field ``via``. ``agg`` is one of
    #: :data:`ROLLUP_AGGS`, ``field`` is the field it reads (none for ``count``),
    #: and ``where`` keeps the conditions of the source.
    source: str | None = None
    via: str | None = None
    agg: str | None = None
    where: tuple[Atom, ...] = ()

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
        if self.kind == STATE_MACHINE:
            out["field"] = self.field
            out["initial"] = self.initial
            out["moves"] = [_stored_move(m) for m in self.moves]
        if self.kind == FORMULA and self.expr is not None:
            out["target"] = self.target
            out["expr"] = studio_expr.node_json(self.expr)
            for key in ("scale", "rounding", "currency"):
                if getattr(self, key) is not None:
                    out[key] = getattr(self, key)
        if self.kind == ROLLUP:
            out.update(target=self.target, source=self.source, via=self.via, agg=self.agg)
            if self.field is not None:
                out["field"] = self.field
            if self.where:
                out["where"] = where_filter(self)
            if self.currency is not None:
                out["currency"] = self.currency
        if self.min is not None:
            out["min"] = self.min
        if self.max is not None:
            out["max"] = self.max
        return out

    def hot_keys(self) -> tuple[str, ...]:
        """The fields of the object of this rule that must stay hot while the
        rule stands. A roll-up reads fields of its source, so it names none here:
        :meth:`source_keys` names them."""
        if self.kind in (RANGE, STATE_MACHINE, FORMULA, ROLLUP):
            return ()
        return (*self.keys, self.field) if self.field is not None else self.keys

    def source_keys(self) -> tuple[str, ...]:
        """The fields of the SOURCE object that a roll-up reads, each once: the
        relation, the summed field and the fields of ``where``. They must stay
        hot while the rule stands. Any other kind reads none."""
        if self.kind != ROLLUP:
            return ()
        keys = [str(self.via)]
        if self.field is not None:
            keys.append(self.field)
        keys.extend(a.field for a in self.where)
        return tuple(dict.fromkeys(keys))


def _stored_atom(atom: Atom) -> dict[str, Any]:
    out: dict[str, Any] = {"field": atom.field, "op": atom.op}
    if atom.op not in _NO_VALUE_OPS:
        out["value"] = list(atom.value) if atom.op == "in" else atom.value
    return out


def _stored_move(move: Move) -> dict[str, Any]:
    out: dict[str, Any] = {"from": move.from_state, "to": move.to_state}
    if move.guard:
        out["guard"] = [_stored_atom(a) for a in move.guard]
    return out


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


def _role_refused(rule: str, spec: Mapping[str, Any]) -> ConstraintError | None:
    if _ROLE_KEYS & {str(k) for k in spec}:
        return ConstraintError(
            rule, f"Rule `{rule}`: a role guard waits for slice C8, so a move takes none yet.")
    return None


def _state(rule: str, label: str, value: Any) -> str:
    if (not isinstance(value, str) or not value.strip() or len(value) > MAX_OPTION_CHARS
            or not storable_text(value)):
        raise ConstraintError(
            rule, f"Rule `{rule}`: `{label}` is one option of the field, as text of 1 to "
            f"{MAX_OPTION_CHARS} characters.")
    return value


def _atom_scalar(rule: str, value: Any) -> Any:
    if isinstance(value, bool) or (isinstance(value, str) and len(value) <= MAX_TEXT_CHARS):
        return value
    if isinstance(value, int | float) and math.isfinite(value):
        return value
    raise ConstraintError(
        rule, f"Rule `{rule}`: a guard `value` is one text, number or true or false.")


def _parse_atom(rule: str, spec: Any) -> Atom:
    if not isinstance(spec, Mapping):
        raise ConstraintError(rule, f"Rule `{rule}`: a guard atom is an object with `field` and `op`.")
    refusal = _role_refused(rule, spec)
    if refusal is not None:
        raise refusal
    extra = sorted(str(k) for k in spec if k not in ("field", "op", "value"))
    if extra:
        safe = extra[0] if _NAME_RE.fullmatch(extra[0]) else "?"
        raise ConstraintError(rule, f"Rule `{rule}`: `{safe}` is not a key of a guard atom.")
    field = _key(rule, "field", spec.get("field"))
    op = spec.get("op")
    if not isinstance(op, str) or op not in GUARD_OPS:
        raise ConstraintError(rule, f"Rule `{rule}`: a guard `op` is {', '.join(GUARD_OPS)}.")
    if op in _NO_VALUE_OPS:
        if "value" in spec:
            raise ConstraintError(rule, f"Rule `{rule}`: op `{op}` takes no `value`.")
        return Atom(field, op)
    if "value" not in spec:
        raise ConstraintError(rule, f"Rule `{rule}`: op `{op}` needs a `value`.")
    value = spec["value"]
    if op == "in":
        if not isinstance(value, list) or not 1 <= len(value) <= MAX_GUARD_IN_VALUES:
            raise ConstraintError(
                rule, f"Rule `{rule}`: op `in` takes a list of 1 to {MAX_GUARD_IN_VALUES} values.")
        return Atom(field, op, tuple(_atom_scalar(rule, v) for v in value))
    return Atom(field, op, _atom_scalar(rule, value))


def _parse_guard(rule: str, value: Any) -> tuple[Atom, ...]:
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_GUARD_ATOMS:
        raise ConstraintError(
            rule, f"Rule `{rule}`: a `guard` is a list of 1 to {MAX_GUARD_ATOMS} atoms.")
    return tuple(_parse_atom(rule, a) for a in value)


def _parse_move(rule: str, spec: Any) -> Move:
    if not isinstance(spec, Mapping):
        raise ConstraintError(rule, f"Rule `{rule}`: a move is an object with `from` and `to`.")
    refusal = _role_refused(rule, spec)
    if refusal is not None:
        raise refusal
    extra = sorted(str(k) for k in spec if k not in ("from", "to", "guard"))
    if extra:
        safe = extra[0] if _NAME_RE.fullmatch(extra[0]) else "?"
        raise ConstraintError(rule, f"Rule `{rule}`: `{safe}` is not a key of a move.")
    origin, target = _state(rule, "from", spec.get("from")), _state(rule, "to", spec.get("to"))
    if origin == target:
        raise ConstraintError(rule, f"Rule `{rule}`: a move must change the state.")
    guard = _parse_guard(rule, spec["guard"]) if "guard" in spec else ()
    return Move(origin, target, guard)


def _parse_machine(rule: str, spec: Mapping[str, Any], message: str | None) -> Rule:
    field = _key(rule, "field", spec.get("field"))
    initial = _state(rule, "initial", spec.get("initial"))
    raw = spec.get("moves")
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_MOVES:
        raise ConstraintError(rule, f"Rule `{rule}`: `moves` is a list of 1 to {MAX_MOVES} moves.")
    moves = tuple(_parse_move(rule, m) for m in raw)
    pairs = [(m.from_state, m.to_state) for m in moves]
    if len(set(pairs)) != len(pairs):
        raise ConstraintError(rule, f"Rule `{rule}`: a move is declared twice.")
    return Rule(rule, STATE_MACHINE, message, field=field, initial=initial, moves=moves)


def _expr_message(rule: str, exc: ExprError) -> str:
    where = f" at `{exc.path}`" if exc.path else ""
    return f"Rule `{rule}`{where}: {exc.message}"


def _parse_formula(rule: str, spec: Mapping[str, Any], message: str | None) -> Rule:
    target = _key(rule, "target", spec.get("target"))
    if "expr" not in spec:
        raise ConstraintError(rule, f"Rule `{rule}`: a formula needs `expr`.")
    try:
        expr = studio_expr.parse_expr(spec["expr"])
    except ExprError as exc:
        raise ConstraintError(rule, _expr_message(rule, exc)) from None
    scale, rounding, currency = spec.get("scale"), spec.get("rounding"), spec.get("currency")
    if "scale" in spec and (type(scale) is not int or not 0 <= scale <= studio_expr.MAX_SCALE):
        raise ConstraintError(
            rule, f"Rule `{rule}`: `scale` is a whole number from 0 to {studio_expr.MAX_SCALE}.")
    if "rounding" in spec and (not isinstance(rounding, str)
                               or rounding not in studio_expr.ROUNDINGS):
        raise ConstraintError(
            rule, f"Rule `{rule}`: `rounding` is {', '.join(studio_expr.ROUNDINGS)}.")
    if "currency" in spec and (not isinstance(currency, str)
                               or not _CURRENCY_RE.fullmatch(currency)):
        raise ConstraintError(rule, f"Rule `{rule}`: `currency` is three capital letters, as INR.")
    return Rule(rule, FORMULA, message, target=target, expr=expr, scale=scale,
                rounding=rounding, currency=currency)


def _object_name(rule: str, label: str, value: Any) -> str:
    if not isinstance(value, str) or not _NAME_RE.fullmatch(value):
        raise ConstraintError(
            rule, f"Rule `{rule}`: `{label}` names an object of this package by its bare name.")
    return value


def _parse_where_item(rule: str, spec: Any) -> Atom:
    if not isinstance(spec, Mapping):
        raise ConstraintError(rule, f"Rule `{rule}`: a `where` condition is an object with `field` and `op`.")
    extra = sorted(str(k) for k in spec if k not in ("field", "op", "value"))
    if extra:
        safe = extra[0] if _NAME_RE.fullmatch(extra[0]) else "?"
        raise ConstraintError(rule, f"Rule `{rule}`: `{safe}` is not a key of a `where` condition.")
    field = _key(rule, "field", spec.get("field"))
    op = spec.get("op")
    if not isinstance(op, str) or op not in OPS:
        raise ConstraintError(rule, f"Rule `{rule}`: a `where` op is {', '.join(OPS)}.")
    value = spec.get("value")
    if op == "is_empty":
        if value is not None and type(value) is not bool:
            raise ConstraintError(rule, f"Rule `{rule}`: `is_empty` takes true or false.")
        return Atom(field, op, value)
    if op == "in":
        if not isinstance(value, list) or not 1 <= len(value) <= MAX_IN_VALUES:
            raise ConstraintError(
                rule, f"Rule `{rule}`: op `in` takes a list of 1 to {MAX_IN_VALUES} values.")
        return Atom(field, op, tuple(_atom_scalar(rule, v) for v in value))
    if "value" not in spec:
        raise ConstraintError(rule, f"Rule `{rule}`: op `{op}` needs a `value`.")
    return Atom(field, op, _atom_scalar(rule, value))


def _parse_where(rule: str, value: Any) -> tuple[Atom, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_ROLLUP_WHERE:
        raise ConstraintError(
            rule, f"Rule `{rule}`: `where` is a list of 1 to {MAX_ROLLUP_WHERE} conditions.")
    return tuple(_parse_where_item(rule, item) for item in value)


def where_filter(rule: Rule) -> list[dict[str, Any]]:
    """The ``where`` of a roll-up as the filter of the query grammar (C2a)."""
    out: list[dict[str, Any]] = []
    for atom in rule.where:
        item: dict[str, Any] = {"field": atom.field, "op": atom.op}
        if atom.value is not None:
            item["value"] = list(atom.value) if atom.op == "in" else atom.value
        out.append(item)
    return out


def _parse_rollup(rule: str, spec: Mapping[str, Any], message: str | None) -> Rule:
    target = _key(rule, "target", spec.get("target"))
    source = _object_name(rule, "source", spec.get("source"))
    via = _key(rule, "via", spec.get("via"))
    agg = spec.get("agg")
    if not isinstance(agg, str) or agg not in ROLLUP_AGGS:
        raise ConstraintError(rule, f"Rule `{rule}`: `agg` is {', '.join(ROLLUP_AGGS)}.")
    field: str | None = None
    if agg == "count":
        if spec.get("field") is not None:
            raise ConstraintError(rule, f"Rule `{rule}`: a count takes no `field`.")
    else:
        field = _key(rule, "field", spec.get("field"))
    currency = spec.get("currency")
    if "currency" in spec and (not isinstance(currency, str) or not _CURRENCY_RE.fullmatch(currency)):
        raise ConstraintError(rule, f"Rule `{rule}`: `currency` is three capital letters, as INR.")
    return Rule(rule, ROLLUP, message, field=field, target=target, currency=currency,
                source=source, via=via, agg=agg, where=_parse_where(rule, spec.get("where")))


_PARSERS = {RANGE: _parse_range, UNIQUE: _parse_unique, GROUP_SUM_MIN: _parse_group,
            STATE_MACHINE: _parse_machine, FORMULA: _parse_formula, ROLLUP: _parse_rollup}
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
    if sum(r.kind == FORMULA for r in rules) > MAX_COMPUTED_RULES:
        raise ConstraintError("?", f"An object holds at most {MAX_COMPUTED_RULES} formula rules.")
    if sum(r.kind in (FORMULA, ROLLUP) for r in rules) > MAX_COMPUTED_RULES:
        raise ConstraintError(
            "?", f"An object holds at most {MAX_COMPUTED_RULES} formula and rollup rules in all.")
    machines: set[str] = set()
    for rule in rules:
        if rule.kind == STATE_MACHINE:
            if rule.field in machines:
                raise ConstraintError(
                    rule.name, f"Rule `{rule.name}`: a field has one state machine.")
            machines.add(str(rule.field))
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


def _guard_ops() -> Mapping[str, frozenset[str]]:
    """The ops of a guard atom for each field type: the table of C2a, with
    ``not_empty`` beside ``is_empty``. A money field takes no guard yet. A
    datetime orders as text would, which is wrong with a fraction of a second,
    so it takes no order op."""
    out: dict[str, frozenset[str]] = {}
    for kind, ops in OPS_BY_TYPE.items():
        if kind == "money":
            continue
        allowed = {op for op in ops if op in GUARD_OPS}
        if kind == "datetime":
            allowed -= _ORDER_OPS
        if "is_empty" in allowed:
            allowed.add("not_empty")
        out[kind] = frozenset(allowed)
    return out


#: The closed table: the guard ops of each field type.
GUARD_OPS_BY_TYPE: Mapping[str, frozenset[str]] = _guard_ops()


def _fit_atom(rule: Rule, atom: Atom, by_key: Mapping[str, FieldSpec]) -> None:
    field = by_key.get(atom.field)
    if field is None:
        raise ConstraintError(
            rule.name, f"Rule `{rule.name}`: `{atom.field}` is not a field of this object.")
    allowed = GUARD_OPS_BY_TYPE.get(field.type, frozenset())
    if field.many or atom.op not in allowed:
        raise ConstraintError(
            rule.name, f"Rule `{rule.name}`: a guard cannot test field `{field.key}` with op `{atom.op}`.")
    if atom.op in _NO_VALUE_OPS:
        return
    for value in atom.value if atom.op == "in" else (atom.value,):
        try:
            same = check_value(field, value) == value
        except FieldError:
            same = False
        if not same:
            raise ConstraintError(
                rule.name, f"Rule `{rule.name}`: a guard value for field `{field.key}` must be a "
                "value of that field, written as the store keeps it.")


def _fit_machine(rule: Rule, by_key: Mapping[str, FieldSpec]) -> None:
    field = by_key.get(str(rule.field))
    if field is None:
        raise ConstraintError(
            rule.name, f"Rule `{rule.name}`: `{rule.field}` is not a field of this object.")
    if field.type != "select" or field.many:
        raise ConstraintError(
            rule.name, f"Rule `{rule.name}`: field `{field.key}` must be one select.")
    options = set(field.options or ())
    states = [("initial", rule.initial)]
    for i, move in enumerate(rule.moves):
        states += [(f"moves[{i}].from", move.from_state), (f"moves[{i}].to", move.to_state)]
    for label, state in states:
        if state not in options:
            raise ConstraintError(
                rule.name, f"Rule `{rule.name}`: `{label}` is not an option of field `{field.key}`.")
    for move in rule.moves:
        for atom in move.guard:
            _fit_atom(rule, atom, by_key)


def check_rules_fit(rules: Sequence[Rule], fields: Sequence[FieldSpec], *,
                    object_name: str | None = None,
                    sources: Mapping[str, RollupSource] | None = None) -> None:
    """Refuse a rule that does not fit the *fields* of the object.

    Each named field must exist, and each has the type of its place. A field
    of ``unique`` and of ``group_sum_min`` must be hot. This check never turns
    a field hot. A ``rollup`` also needs the name of this object and the
    *sources* (the other objects of the package, by name), and without them it
    is refused. It raises :class:`ConstraintError`.
    """
    by_key = {f.key: f for f in fields}
    check_formulas([r for r in rules if r.kind == FORMULA], fields)
    check_rollups(rules, fields, object_name, sources)

    def named(rule: Rule, key: str) -> FieldSpec:
        field = by_key.get(key)
        if field is None:
            raise ConstraintError(rule.name, f"Rule `{rule.name}`: `{key}` is not a field of this object.")
        return field

    for rule in rules:
        if rule.kind == STATE_MACHINE:
            _fit_machine(rule, by_key)
            continue
        if rule.kind in (FORMULA, ROLLUP):
            continue
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
    if item.get("kind") in (STATE_MACHINE, FORMULA, ROLLUP):
        # The one parser reads it. A role atom or a bad shape fails closed.
        try:
            return parse_rule(item)
        except ConstraintError:
            return Rule(name, MALFORMED)
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
    would only stop the repair of old duplicates. C6 and C7a reuse this hook.
    """
    if rule.kind in (RANGE, STATE_MACHINE, FORMULA, ROLLUP):
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


# --- the state machine (C6) --------------------------------------------------


def with_initial(rules: Sequence[Rule], data: Any) -> Any:
    """The data of a create, with ``initial`` in each machine field that it
    leaves out or leaves blank (None, empty text). Any other data comes back
    as it is."""
    if not isinstance(data, Mapping):
        return data
    out: dict[str, Any] | None = None
    for rule in rules:
        if (rule.kind == STATE_MACHINE and rule.field is not None
                and is_blank(data.get(rule.field))):
            out = dict(data) if out is None else out
            out[rule.field] = rule.initial
    return data if out is None else out


def _atom_holds(atom: Atom, data: Mapping[str, Any]) -> bool:
    """One atom against the merged record. An error of type fails closed."""
    value = data.get(atom.field)
    blank = is_blank(value)
    # The checkbox rule of C2a. Only a checkbox takes a bool value (`_fit_atom`
    # holds it), so a bool value marks a checkbox atom. `eq false` and `ne true`
    # match a record with no value. `is_empty` still means no value.
    if blank and isinstance(atom.value, bool) and atom.op in ("eq", "ne"):
        value, blank = False, False
    if atom.op == "is_empty":
        return blank
    if atom.op == "not_empty":
        return not blank
    if blank:
        return atom.op == "ne"
    try:
        if atom.op == "eq":
            return bool(value == atom.value)
        if atom.op == "ne":
            return bool(value != atom.value)
        if atom.op == "in":
            return value in atom.value
        if atom.op == "lt":
            return bool(value < atom.value)
        if atom.op == "lte":
            return bool(value <= atom.value)
        if atom.op == "gt":
            return bool(value > atom.value)
        if atom.op == "gte":
            return bool(value >= atom.value)
    except TypeError:
        return False
    return False


def guard_holds(guard: Sequence[Atom], data: Mapping[str, Any]) -> bool:
    """True when every atom holds on the merged record *data*."""
    return all(_atom_holds(a, data) for a in guard)


def _states(rule: Rule) -> set[str]:
    """The states that the machine declares."""
    return {str(rule.initial)} | {s for m in rule.moves for s in (m.from_state, m.to_state)}


def _declared(rule: Rule, state: Any) -> str | None:
    """*state* when the machine declares it, else None. A refusal names no other."""
    return state if isinstance(state, str) and state in _states(rule) else None


def _refuse_move(rule: Rule, object_name: str, old: Any, new: Any, why: str) -> MoveViolation:
    return MoveViolation(
        rule.name, object_name, _declared(rule, old), _declared(rule, new),
        f"Rule `{rule.name}` of object `{object_name}`: {rule.message or why}")


def check_move(rule: Rule, object_name: str, before: Mapping[str, Any] | None,
               after: Mapping[str, Any] | None) -> None:
    """Judge the net move of one record against the state machine *rule*.

    *before* is the record before the batch (None for a create), and *after*
    is the record after it (None for a delete). A delete is not a move. A
    create must hold ``initial``. An unchanged state passes. A change needs a
    declared move whose guard holds on *after*, the merged record. A cleared
    state, and a state that is not a declared one, are refused.
    """
    if after is None:
        return
    key = str(rule.field)
    new = after.get(key)
    if before is None:
        if new != rule.initial:
            raise _refuse_move(
                rule, object_name, None, new, f"a new record starts in `{rule.initial}`.")
        return
    old = before.get(key)
    if old == new:
        return
    if is_blank(new):
        raise _refuse_move(rule, object_name, old, None, f"`{key}` cannot be cleared.")
    if _declared(rule, old) is None:
        raise _refuse_move(rule, object_name, old, new, f"`{key}` holds a state that is not declared.")
    for move in rule.moves:
        if move.from_state == old and move.to_state == new:
            if guard_holds(move.guard, after):
                return
            raise _refuse_move(rule, object_name, old, new, "the guard of this move does not hold.")
    raise _refuse_move(rule, object_name, old, new, f"`{key}` cannot make this move.")


# --- the formulas (C7a) ------------------------------------------------------


def _fit_target(rule: Rule, by_key: Mapping[str, FieldSpec]) -> FieldSpec:
    """The target field of one formula, with the keys that its type takes."""
    key = str(rule.target)
    field = by_key.get(key)
    if field is None:
        raise ConstraintError(rule.name, f"Rule `{rule.name}`: `{key}` is not a field of this object.")
    if field.type not in FORMULA_TARGET_TYPES or field.many:
        raise ConstraintError(
            rule.name, f"Rule `{rule.name}`: target `{key}` must be one number, money, text, "
            "checkbox or date field.")
    if field.required:
        raise ConstraintError(
            rule.name, f"Rule `{rule.name}`: target `{key}` cannot be required, because a "
            "formula can give no value.")
    is_number, is_money = field.type == "number", field.type == "money"
    if is_number and rule.rounding is None:
        raise ConstraintError(
            rule.name, f"Rule `{rule.name}`: a number target needs `rounding`: "
            f"{', '.join(studio_expr.ROUNDINGS)}.")
    if not is_number and (rule.rounding is not None or rule.scale is not None):
        raise ConstraintError(
            rule.name, f"Rule `{rule.name}`: only a number target takes `scale` and `rounding`.")
    if is_money != (rule.currency is not None):
        raise ConstraintError(
            rule.name, f"Rule `{rule.name}`: only a money target takes `currency`, and it needs one.")
    return field


def _fit_result(rule: Rule, field: FieldSpec, fields: Sequence[FieldSpec]) -> None:
    try:
        kind = studio_expr.check_expr(rule.expr or Node("bool"), fields, target=rule.target)
    except ExprError as exc:
        raise ConstraintError(rule.name, _expr_message(rule.name, exc)) from None
    if kind != FORMULA_TARGET_TYPES[field.type]:
        raise ConstraintError(
            rule.name, f"Rule `{rule.name}`: the formula gives a {kind} value, and target "
            f"`{field.key}` takes a {FORMULA_TARGET_TYPES[field.type]} value.")


def formula_order(formulas: Sequence[Rule]) -> list[Rule] | None:
    """The formulas in the order that they run: a formula runs after each
    formula whose target it reads, and the rest keep their declared order.
    None when the formulas read each other in a loop."""
    targets = {str(r.target) for r in formulas}
    waiting = list(formulas)
    done: set[str] = set()
    out: list[Rule] = []
    while waiting:
        ready = next(
            (r for r in waiting
             if all(k in done for k in studio_expr.expr_reads(r.expr or Node("bool")) & targets)),
            None)
        if ready is None:
            return None
        waiting.remove(ready)
        done.add(str(ready.target))
        out.append(ready)
    return out


def check_formulas(formulas: Sequence[Rule], fields: Sequence[FieldSpec]) -> None:
    """Refuse the formulas of one object that do not fit its *fields*, as a set.

    Each target is one plain field that no other formula fills. Each ref names
    a field, and never its own target. A tree gives the type of its target. The
    formulas read each other in no loop. Raises :class:`ConstraintError`.
    """
    by_key = {f.key: f for f in fields}
    seen: set[str] = set()
    for rule in formulas:
        field = _fit_target(rule, by_key)
        if field.key in seen:
            raise ConstraintError(
                rule.name, f"Rule `{rule.name}`: another formula fills `{field.key}` already.")
        seen.add(field.key)
        _fit_result(rule, field, fields)
    if formula_order(formulas) is None:
        raise ConstraintError(
            formulas[0].name, "The formulas of this object read each other in a loop.")


@dataclass(frozen=True)
class FormulaPlan:
    """The formulas of one object, checked once for a batch.

    ``ordered`` holds each formula with its target field, in the order of run.
    ``plain`` holds the fields that a caller may write: no target.
    """

    ordered: tuple[tuple[Rule, FieldSpec], ...]
    fields: tuple[FieldSpec, ...]
    plain: tuple[FieldSpec, ...]
    targets: frozenset[str]
    #: The roll-ups of the object (C7b), each with its target field. A roll-up
    #: target is computed too, but the record keeps its stored value, so it stays
    #: in ``plain`` and ``without_targets`` keeps it.
    rollups: tuple[tuple[Rule, FieldSpec], ...] = ()


def plan_formulas(rules: Sequence[Rule], object_name: str,
                  fields: Sequence[FieldSpec]) -> FormulaPlan | None:
    """The plan of the formulas of an object, or None when it has none.

    The store runs this on each write, so a stored formula that does not fit
    refuses the write (it fails closed). Raises :class:`FormulaError` with the
    code ``declaration``.
    """
    formulas = [r for r in rules if r.kind == FORMULA]
    rollups = [r for r in rules if r.kind == ROLLUP]
    if not formulas and not rollups:
        return None
    if len(formulas) > MAX_COMPUTED_RULES or len(formulas) + len(rollups) > MAX_COMPUTED_RULES:
        raise FormulaError(
            (formulas or rollups)[0].name, object_name, studio_expr.DECLARATION,
            f"Object `{object_name}`: at most {MAX_COMPUTED_RULES} formula and rollup rules are allowed.")
    by_key = {f.key: f for f in fields}
    try:
        if formulas:
            check_formulas(formulas, fields)
        for rule in rollups:
            _fit_rollup_target(rule, by_key)
    except ConstraintError as exc:
        raise FormulaError(
            exc.rule, object_name, studio_expr.DECLARATION,
            f"Object `{object_name}`: {exc.message}") from None
    ordered = formula_order(formulas) or []
    targets = frozenset(str(r.target) for r in formulas)
    return FormulaPlan(
        tuple((r, by_key[str(r.target)]) for r in ordered), tuple(fields),
        tuple(f for f in fields if f.key not in targets), targets,
        tuple((r, by_key[str(r.target)]) for r in rollups))


def refuse_computed_keys(plan: FormulaPlan | None, object_name: str, data: Any) -> None:
    """Refuse a write that holds a key of a target, also with the same value or
    with no value. A caller never writes a computed field."""
    if plan is None or not isinstance(data, Mapping):
        return
    for key in data:
        if key in plan.targets or any(f.key == key for _, f in plan.rollups):
            rule = next(r for r, f in (*plan.ordered, *plan.rollups) if f.key == key)
            raise FormulaError(
                rule.name, object_name, COMPUTED_FIELD,
                f"Rule `{rule.name}` of object `{object_name}`: field `{key}` is computed. "
                "Write no value for it.")


def without_targets(plan: FormulaPlan | None, data: Mapping[str, Any]) -> dict[str, Any]:
    """The record *data* with no target: the input of ``validate_record``."""
    if plan is None:
        return dict(data)
    return {k: v for k, v in data.items() if k not in plan.targets}


def run_formulas(plan: FormulaPlan | None, object_name: str,
                 clean: Mapping[str, Any]) -> dict[str, Any]:
    """The record *clean* with each target computed, in the order of the plan.

    *clean* is the checked record with no target. A formula that reads an
    earlier target reads its stored result. A null result leaves the target out.
    The answer holds its keys in the order of the fields. Raises
    :class:`FormulaError`, with the rule, the object and a code, and no value.
    """
    if plan is None:
        return dict(clean)
    record = dict(clean)
    for rule, field in plan.ordered:
        try:
            value = studio_expr.evaluate(rule.expr or Node("bool"), record)
            stored = studio_expr.store_result(
                value, field, scale=rule.scale or 0, rounding=rule.rounding,
                currency=rule.currency)
        except ExprError as exc:
            raise FormulaError(
                rule.name, object_name, exc.code,
                f"Rule `{rule.name}` of object `{object_name}`: {rule.message or exc.message}"
            ) from None
        if stored is None:
            record.pop(field.key, None)
        else:
            record[field.key] = stored
    return {f.key: record[f.key] for f in plan.fields if f.key in record}


def rule_reads(rule: Rule) -> frozenset[str]:
    """Every field key that *rule* reads. C9 reads it to decide that a formula
    or a guard that reads a personal or restricted field makes its target the
    same level. A target is written, so a formula does not read it. A rule of
    an unknown kind reads nothing. A roll-up reads fields of another object, so
    it reads nothing here (:meth:`Rule.source_keys` names them)."""
    if rule.kind == FORMULA:
        return studio_expr.expr_reads(rule.expr) if rule.expr is not None else frozenset()
    if rule.kind == ROLLUP:
        return frozenset()  # it reads fields of its source, never of its own record
    reads = set(rule.keys)
    if rule.field is not None:
        reads.add(rule.field)
    if rule.kind == STATE_MACHINE:
        reads.update(a.field for m in rule.moves for a in m.guard)
    return frozenset(reads) if rule.kind in RULE_KINDS else frozenset()


# --- the roll-ups (C7b) -------------------------------------------------------


@dataclass(frozen=True)
class RollupSource:
    """An object that a roll-up reads: its bare name, its fields and its rules."""

    name: str
    fields: tuple[FieldSpec, ...]
    rules: tuple[Rule, ...] = ()


def rollup_tainted(rules: Sequence[Rule]) -> frozenset[str]:
    """The fields of an object that a roll-up fills, directly or through a
    formula that reads one. A roll-up of another object may not read them:
    a roll-up of a roll-up needs a second recompute, and v1 has none."""
    tainted = {str(r.target) for r in rules if r.kind == ROLLUP}
    formulas = [r for r in rules if r.kind == FORMULA and r.expr is not None]
    grown = True
    while grown:
        grown = False
        for rule in formulas:
            key = str(rule.target)
            if key not in tainted and studio_expr.expr_reads(rule.expr or Node("bool")) & tainted:
                tainted.add(key)
                grown = True
    return frozenset(tainted)


def _refuse(rule: Rule, message: str) -> ConstraintError:
    return ConstraintError(rule.name, f"Rule `{rule.name}`: {message}")


def _fit_rollup_target(rule: Rule, by_key: Mapping[str, FieldSpec]) -> FieldSpec:
    """The target field of one roll-up, with the checks that need no source."""
    key = str(rule.target)
    field = by_key.get(key)
    if field is None:
        raise _refuse(rule, f"`{key}` is not a field of this object.")
    allowed = ("number",) if rule.agg == "count" else (
        ("number", "money") if rule.agg == "sum" else ("number", "money", "date"))
    if field.type not in allowed or field.many:
        raise _refuse(rule, f"target `{key}` must be one {' or '.join(allowed)} field for `{rule.agg}`.")
    if field.required:
        raise _refuse(rule, f"target `{key}` cannot be required, because a roll-up can give no value.")
    if (field.type == "money") != (rule.currency is not None):
        raise _refuse(rule, "only a money target takes `currency`, and it needs one.")
    return field


def _source_hot(rule: Rule, source: RollupSource, field: FieldSpec) -> None:
    if not field.hot or field.many:
        raise _refuse(
            rule, f"field `{field.key}` of `{source.name}` must be one hot field. Turn it hot first.")


def _fit_rollup(rule: Rule, own_name: str, by_key: Mapping[str, FieldSpec],
                source: RollupSource | None) -> None:
    target = _fit_rollup_target(rule, by_key)
    if source is None or source.name == own_name:
        raise _refuse(rule, f"`{rule.source}` must be another object of this package.")
    src = {f.key: f for f in source.fields}
    via = src.get(str(rule.via))
    if via is None:
        raise _refuse(rule, f"`{rule.via}` is not a field of `{source.name}`.")
    if via.type != "relation" or via.many:
        raise _refuse(rule, f"`via` must be one relation field of `{source.name}`.")
    if via.to != own_name:
        raise _refuse(
            rule, f"field `{via.key}` of `{source.name}` must point to `{own_name}`, by its bare name.")
    _source_hot(rule, source, via)
    if rule.agg != "count":
        read = src.get(str(rule.field))
        if read is None:
            raise _refuse(rule, f"`{rule.field}` is not a field of `{source.name}`.")
        kinds = ROLLUP_FIELD_TYPES[str(rule.agg)]
        if read.type not in kinds or read.many:
            raise _refuse(
                rule, f"`{rule.agg}` reads a {' or '.join(kinds)} field, and `{read.key}` is not one.")
        _source_hot(rule, source, read)
        if target.type != read.type:
            raise _refuse(rule, f"target `{target.key}` must be a {read.type} field, as `{read.key}` is.")
    try:
        conditions = parse_query(list(source.fields), where_filter(rule)).conditions
    except QueryError as exc:
        raise _refuse(rule, f"`where`: {exc.message}") from None
    for cond in conditions:
        _source_hot(rule, source, cond.field)
    bad = sorted(set(rule.source_keys()) & rollup_tainted(source.rules))
    if bad:
        raise _refuse(
            rule, f"field `{bad[0]}` of `{source.name}` is filled by a roll-up, "
            "and a roll-up cannot read a roll-up.")


def check_rollups(rules: Sequence[Rule], fields: Sequence[FieldSpec], object_name: str | None,
                  sources: Mapping[str, RollupSource] | None) -> None:
    """Refuse the roll-ups of one object that do not fit it and their *sources*.

    A source is a bare name of an object of the same package, and it is not
    this object. The relation is a hot, single relation field of the source
    that points to this object. The summed field and each ``where`` field are
    hot. The target takes the type of the summed field, and it is no key of
    another rule and no target of another computed rule. Raises
    :class:`ConstraintError`.
    """
    rollups = [r for r in rules if r.kind == ROLLUP]
    if not rollups:
        return
    if object_name is None or sources is None:
        raise _refuse(rollups[0], "a roll-up is checked with the other objects of its package.")
    by_key = {f.key: f for f in fields}
    taken = {str(r.target) for r in rules if r.kind == FORMULA}
    for rule in rollups:
        _fit_rollup(rule, object_name, by_key, sources.get(str(rule.source)))
        key = str(rule.target)
        if key in taken:
            raise _refuse(rule, f"another formula or roll-up fills `{key}` already.")
        taken.add(key)
        for other in rules:
            keyed = (other.kind == UNIQUE and key in other.keys) or (
                other.kind == GROUP_SUM_MIN and (key == other.field or key in other.keys)) or (
                other.kind == STATE_MACHINE and key == other.field)
            if keyed:
                raise _refuse(
                    rule, f"target `{key}` is a key of rule `{other.name}`. A roll-up fills no key.")


def check_rollup_inputs(object_name: str, rules: Sequence[Rule],
                        readers: Sequence[tuple[str, Rule]]) -> None:
    """The other direction of :func:`check_rollups`. *rules* are the new rules of
    the object *object_name*, and *readers* are the roll-ups of other objects
    of the package that read it, each with the name of its object. A roll-up
    that reads a field which these rules fill is refused."""
    tainted = rollup_tainted(rules)
    for owner, rule in readers:
        bad = sorted(set(rule.source_keys()) & tainted)
        if bad:
            raise ConstraintError(
                rule.name, f"Rule `{rule.name}` of object `{owner}` reads field `{bad[0]}` of "
                f"`{object_name}`, and a roll-up or a formula fills it. A roll-up cannot read a roll-up.")


def _parent_id(rule: Rule, data: Mapping[str, Any] | None) -> str | None:
    """The id of the parent that the child record *data* names, as a record id.
    None when the field is empty or the id is no UUID: the parent is dangling."""
    raw = None if data is None else data.get(str(rule.via))
    ident = raw.get("id") if isinstance(raw, Mapping) else None
    if not isinstance(ident, str):
        return None
    try:
        return str(uuid.UUID(relation_key(ident)))
    except ValueError:
        return None


def rollup_parents(rule: Rule, before: Mapping[str, Any] | None,
                   after: Mapping[str, Any] | None) -> list[str]:
    """The parents that one written child record makes stale, each once.

    *before* is the child before the batch (None for a create) and *after* is
    the child after it (None for a delete). A create and a delete touch their
    parent. An update touches its parent only when the relation field, the read
    field or a ``where`` field changed, and a move touches the old and the new
    parent. A parent id that is no UUID is dangling, and it is left out."""
    old, new = _parent_id(rule, before), _parent_id(rule, after)
    if before is not None and after is not None:
        watch = [k for k in rule.source_keys() if k != rule.via]  # the parents compare by id
        if old == new and all(before.get(k) == after.get(k) for k in watch):
            return []
    out: list[str] = []
    for ident in (old, new):
        if ident is not None and ident not in out:
            out.append(ident)
    return out


def check_rollup_currency(rule: Rule, object_name: str, data: Mapping[str, Any]) -> None:
    """Refuse a child record whose money is not in the currency of the rule.
    The hot row holds the minor amount only, so the sum would mix currencies."""
    if rule.currency is None or rule.field is None:
        return
    value = data.get(rule.field)
    if isinstance(value, Mapping) and value.get("currency") != rule.currency:
        raise RollupError(
            rule.name, object_name, studio_expr.CURRENCY_MISMATCH,
            f"Rule `{rule.name}` of object `{object_name}`: field `{rule.field}` of "
            f"`{rule.source}` must be in {rule.currency}.")


def rollup_empty(rule: Rule) -> Any:
    """What a roll-up gives for no child: 0 for a sum and a count (a money sum
    is 0 minor units in the currency of the rule), and None for a min and a max."""
    if rule.agg == "count":
        return 0
    if rule.agg == "sum":
        return {"minor": 0, "currency": rule.currency} if rule.currency is not None else 0
    return None


def with_rollup_zero(plan: FormulaPlan | None, data: Any) -> Any:
    """The data of a create, with the empty value of each roll-up that has one.
    A new parent has no child. Any other data comes back as it is."""
    if plan is None or not plan.rollups or not isinstance(data, Mapping):
        return data
    out = dict(data)
    for rule, field in plan.rollups:
        empty = rollup_empty(rule)
        if empty is not None:
            out[field.key] = empty
    return out


def _too_large(rule: Rule, object_name: str) -> RollupError:
    return RollupError(
        rule.name, object_name, studio_expr.RESULT_TOO_LARGE,
        f"Rule `{rule.name}` of object `{object_name}`: the result is too large to store exactly.")


def rollup_result(rule: Rule, object_name: str, field: FieldSpec, raw: Any) -> Any:
    """The stored value of a roll-up from the *raw* answer of the aggregate, or
    None when the target clears. A sum and a count of nothing give 0 and a min
    and a max of nothing give None. The answer runs through ``check_value`` of
    the target, and a number must read back exactly, so a result that does not
    fit raises :class:`RollupError` with ``result_too_large``."""
    if raw is None:
        return rollup_empty(rule)
    try:
        if field.type == "date":
            stored: Any = raw.astimezone(UTC).date().isoformat() if isinstance(raw, datetime) else raw
        else:
            exact = Decimal(raw)
            if abs(exact) >= 10**15:
                raise _too_large(rule, object_name)
            if field.type == "money":
                if exact != exact.to_integral_value():
                    raise _too_large(rule, object_name)
                stored = {"minor": int(exact), "currency": rule.currency}
            else:
                stored = studio_expr.number_json(exact)
                if Decimal(repr(stored)) != exact:
                    raise _too_large(rule, object_name)
        return check_value(field, stored)
    except (FieldError, ArithmeticError):
        raise _too_large(rule, object_name) from None
