"""WS-STU C1b — the rules of a Studio object and the batch, with no database.

Spec: ``project-docs/specs/ai_studio.md`` §3.1 and the §14 C1b row. Code:
``acb_common/studio_rules.py`` (the grammar) and the batch section of
``gateway/studio/objects_store.py``. ``test_studio_objects_batch_r8.py`` runs
the SQL on a real Postgres, and it lists the mutations.
"""

from __future__ import annotations

import ast
import asyncio
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from acb_common import studio_fields as sf
from acb_common import studio_query as sq
from acb_common import studio_rules as sr
from acb_common.studio_rules import ConstraintError, ConstraintViolation, RuleViolation
from gateway.studio import objects_store as store

_ROOT = Path(__file__).resolve().parents[2]
_STORE = _ROOT / "apps/services/gateway/gateway/studio/objects_store.py"
_RULES = _ROOT / "packages/acb_common/acb_common/studio_rules.py"

_SPECS: list[dict[str, Any]] = [
    {"key": "sku", "type": "text", "hot": True},
    {"key": "mail", "type": "email", "hot": True},
    {"key": "state", "type": "select", "options": ["open", "done"], "hot": True},
    {"key": "qty", "type": "number", "hot": True},
    {"key": "day", "type": "date", "hot": True},
    {"key": "at", "type": "datetime", "hot": True},
    {"key": "fee", "type": "money", "hot": True},
    {"key": "paid", "type": "checkbox", "hot": True},
    {"key": "weight", "type": "number"},
    {"key": "note", "type": "text"},
    {"key": "sizes", "type": "number", "many": True},
]
FIELDS = [sf.parse_field_spec(s) for s in _SPECS]

_RANGE = {"name": "weight_ok", "kind": "range", "field": "weight", "min": 0, "max": 10}
_UNIQUE = {"name": "one_sku", "kind": "unique", "fields": ["sku"]}
_GROUP = {"name": "stock_not_negative", "kind": "group_sum_min", "field": "qty",
          "group_by": ["sku", "state"], "min": 0, "message": "Stock cannot go below zero."}


def _rule(spec: dict[str, Any]) -> sr.Rule:
    return sr.parse_rule(spec)


# ── The grammar ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("spec", [
    _RANGE,
    {"name": "low", "kind": "range", "field": "weight", "min": 0},
    {"name": "high", "kind": "range", "field": "weight", "max": 2.5},
    _UNIQUE,
    _GROUP,
    {"name": "by_one", "kind": "group_sum_min", "field": "qty", "group_by": ["sku"], "min": -3.5},
])
def test_a_rule_parses_stores_and_reads_back_the_same(spec: dict[str, Any]) -> None:
    rule = _rule(spec)
    assert rule.stored() == spec
    assert sr.stored_rules([rule.stored()]) == (rule,)


@pytest.mark.parametrize(("spec", "fragment"), [
    ("no object", "is an object"),
    ({"kind": "range"}, "A rule name"),
    ({"name": "Bad-Name", "kind": "range"}, "A rule name"),
    ({"name": "a" * 49, "kind": "range"}, "A rule name"),
    ({"name": "r"}, "the kind is"),
    ({"name": "r", "kind": "formula"}, "the kind is"),
    ({**_RANGE, "extra": 1}, "`extra` is not a key"),
    ({**_UNIQUE, "min": 1}, "`min` is not a key"),
    ({"name": "r", "kind": "range", "field": "weight"}, "needs `min`, `max` or both"),
    ({"name": "r", "kind": "range", "field": "weight", "min": 2, "max": 1}, "above `max`"),
    ({"name": "r", "kind": "range", "field": "weight", "min": True}, "is a number"),
    ({"name": "r", "kind": "range", "field": "weight", "min": 1e-05}, "is a number"),
    ({"name": "r", "kind": "range", "field": "weight", "min": 10**15}, "is a number"),
    ({"name": "r", "kind": "range", "field": 3, "min": 0}, "names a field"),
    ({"name": "r", "kind": "range", "field": "BAD", "min": 0}, "names a field"),
    ({"name": "r", "kind": "unique", "fields": []}, "a list of one field"),
    ({"name": "r", "kind": "unique", "fields": ["sku", "mail"]}, "a list of one field"),
    ({"name": "r", "kind": "unique", "fields": "sku"}, "a list of one field"),
    ({"name": "r", "kind": "group_sum_min", "field": "qty", "group_by": [], "min": 0},
     "1 to 2 fields"),
    ({"name": "r", "kind": "group_sum_min", "field": "qty",
      "group_by": ["sku", "mail", "state"], "min": 0}, "1 to 2 fields"),
    ({"name": "r", "kind": "group_sum_min", "field": "qty", "group_by": ["sku", "sku"], "min": 0},
     "names a field twice"),
    ({"name": "r", "kind": "group_sum_min", "field": "qty", "group_by": ["sku"]}, "needs `min`"),
    ({"name": "r", "kind": "group_sum_min", "field": "qty", "group_by": ["qty"], "min": 0},
     "cannot also be in `group_by`"),
    ({**_UNIQUE, "message": ""}, "`message` is text"),
    ({**_UNIQUE, "message": "x" * 201}, "`message` is text"),
    ({**_UNIQUE, "message": "nul\x00"}, "`message` is text"),
])
def test_a_bad_rule_is_refused(spec: Any, fragment: str) -> None:
    with pytest.raises(ConstraintError) as refused:
        sr.parse_rule(spec)
    assert fragment in refused.value.message


def test_a_bound_of_zero_is_a_bound() -> None:
    """`min` 0 is the common case, and `not value` would drop it."""
    assert _rule({"name": "r", "kind": "range", "field": "weight", "min": 0}).min == 0
    group = {"name": "r", "kind": "group_sum_min", "field": "qty", "group_by": ["sku"], "min": 0}
    assert _rule(group).min == 0


def test_sixteen_rules_pass_and_a_seventeenth_is_refused() -> None:
    rules = [{"name": f"r{i}", "kind": "unique", "fields": ["sku"]} for i in range(17)]
    assert len(sr.parse_rules(rules[:16])) == sr.MAX_RULES_PER_OBJECT == 16
    with pytest.raises(ConstraintError, match="at most 16 rules"):
        sr.parse_rules(rules)


def test_the_rules_are_a_list_with_unique_names() -> None:
    for bad in ("x", {"name": "r"}, 3, None):
        with pytest.raises(ConstraintError, match="are a list"):
            sr.parse_rules(bad)
    with pytest.raises(ConstraintError, match="must be unique"):
        sr.parse_rules([_UNIQUE, _UNIQUE])


@pytest.mark.parametrize("spec", [
    _RANGE, _UNIQUE, _GROUP,
    {"name": "r", "kind": "unique", "fields": ["mail"]},
    {"name": "r", "kind": "unique", "fields": ["state"]},
    {"name": "r", "kind": "unique", "fields": ["qty"]},
    {"name": "r", "kind": "unique", "fields": ["day"]},
    {"name": "r", "kind": "unique", "fields": ["at"]},
])
def test_a_rule_fits_its_fields(spec: dict[str, Any]) -> None:
    sr.check_rules_fit([_rule(spec)], FIELDS)


@pytest.mark.parametrize(("spec", "fragment"), [
    ({"name": "r", "kind": "range", "field": "gone", "min": 0}, "not a field of this object"),
    ({"name": "r", "kind": "range", "field": "fee", "min": 0}, "must be one number"),
    ({"name": "r", "kind": "range", "field": "sizes", "min": 0}, "must be one number"),
    ({"name": "r", "kind": "range", "field": "sku", "min": 0}, "must be one number"),
    ({"name": "r", "kind": "unique", "fields": ["gone"]}, "not a field of this object"),
    ({"name": "r", "kind": "unique", "fields": ["note"]}, "must be hot"),
    ({"name": "r", "kind": "unique", "fields": ["fee"]}, "cannot be a key"),
    ({"name": "r", "kind": "unique", "fields": ["paid"]}, "cannot be a key"),
    ({"name": "r", "kind": "unique", "fields": ["sizes"]}, "cannot be a key"),
    ({"name": "r", "kind": "group_sum_min", "field": "weight", "group_by": ["sku"], "min": 0},
     "must be hot"),
    ({"name": "r", "kind": "group_sum_min", "field": "fee", "group_by": ["sku"], "min": 0},
     "must be one number"),
    ({"name": "r", "kind": "group_sum_min", "field": "qty", "group_by": ["note"], "min": 0},
     "must be hot"),
    ({"name": "r", "kind": "group_sum_min", "field": "qty", "group_by": ["paid"], "min": 0},
     "cannot be a key"),
    ({"name": "r", "kind": "group_sum_min", "field": "qty", "group_by": ["fee"], "min": 0},
     "cannot be a key"),
    ({"name": "r", "kind": "group_sum_min", "field": "qty", "group_by": ["gone"], "min": 0},
     "not a field of this object"),
])
def test_a_rule_that_does_not_fit_is_refused(spec: dict[str, Any], fragment: str) -> None:
    with pytest.raises(ConstraintError) as refused:
        sr.check_rules_fit([_rule(spec)], FIELDS)
    assert fragment in refused.value.message
    assert "`r`" in refused.value.message


def test_a_range_needs_no_hot_field() -> None:
    """A range reads one record in Python, so it takes no place in the budget."""
    assert not next(f for f in FIELDS if f.key == "weight").hot
    assert _rule(_RANGE).hot_keys() == ()
    assert _rule(_UNIQUE).hot_keys() == ("sku",)
    assert set(_rule(_GROUP).hot_keys()) == {"sku", "state", "qty"}


def test_a_stored_rule_reads_with_no_authoring_check() -> None:
    """A stricter rule binds a new declaration only, so a read never fails.
    `1e-05` and a long name pass here, and `parse_rule` refuses each."""
    old = {"name": "Old-Name", "kind": "range", "field": "qty", "min": 1e-05}
    later = {"name": "later", "kind": "a_later_kind", "field": "qty"}
    rules = sr.stored_rules([old, later])
    assert [(r.name, r.kind) for r in rules] == [("Old-Name", "range"), ("later", "a_later_kind")]
    sr.check_kind_known(rules[0], "ledger")


@pytest.mark.parametrize("item", [
    "junk", 42, None, ["list"],
    {"kind": "group_sum_min", "group_by": ["sku"], "min": 0},
    {"name": "", "kind": "unique", "fields": ["sku"]},
    {"name": 3, "kind": "unique", "fields": ["sku"]},
    {"name": "x"},
    {"name": "x", "kind": 3},
    {"name": "x", "kind": "range"},
    {"name": "x", "kind": "range", "min": 0},
    {"name": "x", "kind": "range", "field": "qty"},
    {"name": "x", "kind": "range", "field": "qty", "min": "a"},
    {"name": "x", "kind": "range", "field": "qty", "max": True},
    {"name": "x", "kind": "range", "field": 3, "min": 0},
    {"name": "x", "kind": "unique"},
    {"name": "x", "kind": "unique", "fields": []},
    {"name": "x", "kind": "unique", "fields": "sku"},
    {"name": "x", "kind": "unique", "fields": [3]},
    {"name": "x", "kind": "group_sum_min", "group_by": ["sku"], "min": -5},
    {"name": "x", "kind": "group_sum_min", "field": "qty", "min": 0},
    {"name": "x", "kind": "group_sum_min", "field": "qty", "group_by": [], "min": 0},
    {"name": "x", "kind": "group_sum_min", "field": "qty", "group_by": ["sku"]},
    {"name": "x", "kind": "group_sum_min", "field": "qty", "group_by": ["sku"], "min": "0"},
])
def test_a_malformed_stored_rule_reads_and_fails_closed(item: Any) -> None:
    """A rule must never pass a write because the store could not read it."""
    rules = sr.stored_rules([item])
    assert [r.kind for r in rules] == [sr.MALFORMED]
    assert rules[0].hot_keys() == ()
    with pytest.raises(ConstraintViolation, match="cannot check this rule"):
        sr.check_kind_known(rules[0], "ledger")


@pytest.mark.parametrize("raw", ["not a list", None, {"name": "x"}, 3])
def test_a_stored_value_that_is_no_list_fails_closed(raw: Any) -> None:
    rules = sr.stored_rules(raw)
    assert [r.kind for r in rules] == [sr.MALFORMED]


def test_an_empty_list_holds_no_rule() -> None:
    assert sr.stored_rules([]) == ()


def test_a_stored_kind_that_this_code_cannot_check_refuses_the_write() -> None:
    rule = sr.stored_rules([{"name": "old", "kind": "a_later_kind"}])[0]
    with pytest.raises(ConstraintViolation, match="old"):
        sr.check_kind_known(rule, "ledger")
    for known in (_RANGE, _UNIQUE, _GROUP):
        sr.check_kind_known(_rule(known), "ledger")


def test_a_rule_on_a_cold_field_refuses_the_write() -> None:
    """No hot rows means no check, so the rule refuses and never passes."""
    rule = _rule({"name": "r", "kind": "unique", "fields": ["note"]})
    with pytest.raises(ConstraintViolation, match="not hot"):
        sr.check_rule_is_hot(rule, "ledger", FIELDS)
    sr.check_rule_is_hot(_rule(_RANGE), "ledger", FIELDS)
    sr.check_rule_is_hot(_rule(_GROUP), "ledger", FIELDS)


# ── The checks ──────────────────────────────────────────────────────────────


def test_a_range_holds_its_bounds_and_passes_no_value() -> None:
    rule = _rule(_RANGE)
    for ok in (0, 10, 5.5):
        sr.check_range(rule, "ledger", {"weight": ok})
    sr.check_range(rule, "ledger", {})
    for bad in (-1, 10.5):
        with pytest.raises(ConstraintViolation, match="weight_ok"):
            sr.check_range(rule, "ledger", {"weight": bad})


def test_unique_counts_the_record_itself() -> None:
    """The count runs after the write, so one record is the record itself."""
    rule = _rule(_UNIQUE)
    sr.check_unique_count(rule, "ledger", 1)
    with pytest.raises(ConstraintViolation, match="one_sku"):
        sr.check_unique_count(rule, "ledger", 2)


def test_a_group_sum_at_the_floor_passes() -> None:
    rule = _rule(_GROUP)
    sr.check_group_sum(rule, "ledger", Decimal("0"))
    sr.check_group_sum(rule, "ledger", Decimal("0.5"))
    with pytest.raises(ConstraintViolation, match="stock_not_negative"):
        sr.check_group_sum(rule, "ledger", Decimal("-1"))


def test_a_key_with_no_rows_passes_at_any_floor() -> None:
    """A group that no longer exists holds nothing to break."""
    rule = _rule({"name": "r", "kind": "group_sum_min", "field": "qty", "group_by": ["sku"],
                  "min": 1})
    sr.check_group_sum(rule, "ledger", None)
    with pytest.raises(ConstraintViolation):
        sr.check_group_sum(rule, "ledger", Decimal("0"))


def test_a_float_floor_compares_by_its_digits() -> None:
    """`Decimal("0.1") < 0.1` is True in Python, because the float is a shade
    above. The sum is exact, so the floor takes the digits that JSON holds."""
    rule = _rule({"name": "r", "kind": "group_sum_min", "field": "qty", "group_by": ["sku"],
                  "min": 0.1})
    sr.check_group_sum(rule, "ledger", Decimal("0.1"))
    with pytest.raises(ConstraintViolation):
        sr.check_group_sum(rule, "ledger", Decimal("0.09"))


def test_a_refusal_names_the_rule_and_the_object_and_never_a_value() -> None:
    secret = "sku-of-a-member"
    refused = sr.violation(_rule(_GROUP), "ledger", "the default sentence")
    assert isinstance(refused, RuleViolation) and isinstance(refused, ValueError)
    assert refused.rule == "stock_not_negative" and refused.object == "ledger"
    assert "stock_not_negative" in str(refused) and "ledger" in str(refused)
    assert "Stock cannot go below zero." in str(refused)
    assert "the default sentence" not in str(refused)
    with pytest.raises(ConstraintViolation) as caught:
        sr.check_unique_count(_rule(_UNIQUE), "ledger", 2)
    assert secret not in str(caught.value)
    assert "one_sku" in str(caught.value) and "another record" in str(caught.value)


# ── The touched keys ────────────────────────────────────────────────────────


GROUP = _rule({"name": "g", "kind": "group_sum_min", "field": "qty", "group_by": ["sku", "state"],
               "min": 0})
UNIQUE = _rule(_UNIQUE)


def test_a_create_touches_its_key() -> None:
    new = {"sku": "a", "state": "open", "qty": 1}
    assert sr.touched_keys(GROUP, None, new) == [("a", "open")]
    assert sr.touched_keys(UNIQUE, None, new) == [("a",)]


def test_a_group_update_touches_the_old_and_the_new_key_once_each() -> None:
    old = {"sku": "a", "state": "open", "qty": 1}
    moved = {"sku": "a", "state": "done", "qty": 1}
    assert sr.touched_keys(GROUP, old, moved) == [("a", "open"), ("a", "done")]
    assert sr.touched_keys(GROUP, old, dict(old)) == [("a", "open")]


def test_a_group_delete_touches_its_key() -> None:
    assert sr.touched_keys(GROUP, {"sku": "a", "state": "open"}, None) == [("a", "open")]


def test_unique_is_touched_by_the_new_key_only() -> None:
    """A delete or an old key cannot add a clash. A check there would only
    stop the repair of old duplicates."""
    assert sr.touched_keys(UNIQUE, {"sku": "old"}, {"sku": "new"}) == [("new",)]
    assert sr.touched_keys(UNIQUE, {"sku": "old"}, None) == []


def test_a_range_touches_no_key() -> None:
    assert sr.touched_keys(_rule(_RANGE), {"weight": 1}, {"weight": 2}) == []


@pytest.mark.parametrize("empty", [None, "", "   "])
def test_an_empty_value_touches_no_key(empty: Any) -> None:
    assert sr.touched_keys(UNIQUE, None, {"sku": empty}) == []
    assert sr.touched_keys(GROUP, None, {"sku": "a", "state": empty}) == []
    assert sr.touched_keys(GROUP, {"sku": empty, "state": "open"}, None) == []


def test_a_zero_is_a_value_of_a_key() -> None:
    rule = _rule({"name": "u", "kind": "unique", "fields": ["qty"]})
    assert sr.touched_keys(rule, None, {"qty": 0}) == [(0,)]


@pytest.mark.parametrize(("floor", "skips"), [(0, True), (-5, True), (-0.5, True), (0.5, False),
                                              (1, False)])
def test_only_a_floor_that_holds_at_zero_can_skip_the_read(floor: float, skips: bool) -> None:
    """A new group starts at a sum of 0. A floor above 0 does not hold there,
    so a rise alone proves nothing about it."""
    rule = _rule({"name": "g", "kind": "group_sum_min", "field": "qty", "group_by": ["sku"],
                  "min": floor})
    assert sr.floor_holds_at_zero(rule) is skips


def test_only_a_group_floor_can_skip_the_read() -> None:
    assert not sr.floor_holds_at_zero(_rule(_UNIQUE))
    assert not sr.floor_holds_at_zero(_rule(_RANGE))


def test_the_net_change_of_one_record() -> None:
    row = {"sku": "a", "state": "open", "qty": 5}
    key, other = ("a", "open"), ("a", "done")
    moved = {**row, "state": "done"}
    assert sr.add_change(GROUP, key, None, row) == 5
    assert sr.add_change(GROUP, key, row, None) == -5
    assert sr.add_change(GROUP, key, row, {**row, "qty": 2}) == -3
    assert sr.add_change(GROUP, key, row, {**row, "note": "edited"}) == 0
    assert sr.add_change(GROUP, key, row, {"sku": "a", "state": "open"}) == -5
    assert sr.add_change(GROUP, key, row, moved) == -5
    assert sr.add_change(GROUP, other, row, moved) == 5
    assert sr.add_change(GROUP, key, None, {"sku": "a", "state": "open"}) == 0


def test_the_net_change_of_a_batch_is_exact() -> None:
    """A float sum of 0.1 + 0.2 - 0.3 is above 0, and the default context
    keeps 28 digits. Either one misjudges a net of zero."""
    key = ("a", "open")

    def row(qty: float) -> dict[str, Any]:
        return {"sku": "a", "state": "open", "qty": qty}

    net = sr.add_change(GROUP, key, None, row(0.1))
    net = sr.add_change(GROUP, key, None, row(0.2), net)
    assert sr.add_change(GROUP, key, row(0.3), None, net) == 0
    big, tiny = 999_999_999_999_999.9, 0.00012345678901234567
    net = sr.add_change(GROUP, key, None, row(big))
    net = sr.add_change(GROUP, key, None, row(tiny), net)
    assert sr.add_change(GROUP, key, row(big), None, net) == Decimal(repr(tiny))


def test_the_last_day_of_the_calendar_is_a_key() -> None:
    """A legal date has a next day, but for one. `eq` then ends at the last
    instant that a date holds, and the day stays one day."""
    field = sf.parse_field_spec({"key": "day", "type": "date", "hot": True})
    condition = sq.parse_query([field], [{"field": "day", "op": "eq", "value": "9999-12-31"}]).conditions[0]
    assert condition.value.isoformat() == "9999-12-31T00:00:00+00:00"
    assert condition.until is not None and condition.until.date().isoformat() == "9999-12-31"
    assert condition.until > condition.value and condition.until.tzinfo is not None


def test_the_filter_of_a_key_runs_through_the_query_grammar() -> None:
    query = sq.parse_query(FIELDS, sr.key_filter(GROUP, ("a", "open")))
    assert [(c.field.key, c.op, c.value) for c in query.conditions] == [
        ("sku", "eq", "a"), ("state", "eq", "open")]


# ── The fences ──────────────────────────────────────────────────────────────


def test_rules_module_holds_no_sql() -> None:
    """The store is the one module with SQL of the object tables. The rules
    are pure, so C6, C7 and the manifest check can import them."""
    src = _RULES.read_text(encoding="utf-8")
    code = ast.parse(src)
    body = re.sub(r'""".*?"""', "", src, flags=re.S)
    for word in ("SELECT ", "INSERT ", "UPDATE ", "DELETE ", "FROM "):
        assert word not in body, word
    for table in ("studio_objects", "studio_object_fields", "studio_records", "studio_record_hot"):
        assert table not in src, table
    imported = {n.module for n in ast.walk(code) if isinstance(n, ast.ImportFrom)}
    imported |= {a.name for n in ast.walk(code) if isinstance(n, ast.Import) for a in n.names}
    assert not any(str(m).startswith(("gateway", "sqlalchemy")) for m in imported), imported


def _functions() -> dict[str, ast.AsyncFunctionDef | ast.FunctionDef]:
    tree = ast.parse(_STORE.read_text(encoding="utf-8"))
    return {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)}


def _calls(node: ast.AST) -> set[str]:
    return {n.func.id for n in ast.walk(node)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}


def _names(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def test_every_record_write_path_runs_the_checks() -> None:
    """No single call skips a rule. Each record function is a batch of one,
    only the batch core runs the three write statements, and the checks run
    in the steps of each batch."""
    funcs = _functions()
    for name in ("create_record", "update_record", "delete_record"):
        assert "_one_write" in _calls(funcs[name]), name
        assert not {"_CREATE_RECORD_SQL", "_UPDATE_RECORD_SQL", "_DELETE_RECORD_SQL"} & _names(
            funcs[name]), name
    assert "_apply_batch" in _calls(funcs["_one_write"])
    assert "_apply_batch" in _calls(funcs["apply_writes"])
    assert "_run_steps" in _calls(funcs["_apply_batch"])
    steps = funcs["_run_steps"]
    assert {"_lock_org", "_lock_objects", "_lock_records", "_apply_write", "_check_keys",
            "_check_records"} <= _calls(steps)
    for statement in ("_CREATE_RECORD_SQL", "_UPDATE_RECORD_SQL", "_DELETE_RECORD_SQL"):
        users = sorted(name for name, f in funcs.items() if statement in _names(f))
        assert users == ["_apply_write"], (statement, users)


def test_the_writes_run_before_the_checks() -> None:
    """A check before the writes judges each row, and not the net of a move."""
    src = ast.unparse(_functions()["_run_steps"])
    assert src.index("_apply_write(") < src.index("_check_records(")
    assert src.index("_apply_write(") < src.index("_check_keys(")


def test_store_never_commits_or_rolls_back() -> None:
    """The caller owns the transaction. A query, a batch and the wait for the
    org lock each hold one SAVEPOINT, and only these three functions open one."""
    tree = ast.parse(_STORE.read_text(encoding="utf-8"))
    ended = [n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)
             and n.attr in ("commit", "rollback", "begin", "close")]
    assert ended == [], ended
    funcs = _functions()
    sites = sorted(name for name, f in funcs.items() if any(
        isinstance(n, ast.Attribute) and n.attr == "begin_nested" for n in ast.walk(f)))
    assert sites == ["_apply_batch", "_lock_org", "_time_limit"], sites


def test_the_wait_for_the_org_lock_is_1_second_and_says_one_sentence() -> None:
    assert store.ORG_LOCK_WAIT_MS == 1_000
    assert store.StoreBusy.code == "store_busy"
    assert issubclass(store.StoreBusy, store.ObjectStoreError)
    assert store._BUSY_MESSAGE == "This app is busy. Try again in a moment."


def test_every_lock_of_the_org_goes_through_the_bound() -> None:
    """The bound lives in `_lock_org`. No function takes the lock with its own
    statement, so no caller can wait with no bound."""
    funcs = _functions()
    assert sorted(n for n, f in funcs.items() if "_ORG_LOCK_SQL" in _names(f)) == ["_lock_org"]
    for name in ("define_object", "add_field", "set_field_hot", "drop_object", "set_constraints",
                 "_run_steps"):
        assert "_lock_org" in _calls(funcs[name]), name


class _NoSQL:
    """A session that fails on any statement."""

    def __init__(self) -> None:
        self.statements = 0

    async def execute(self, *a: Any, **k: Any) -> Any:
        self.statements += 1
        raise AssertionError("a statement ran")

    def begin_nested(self) -> Any:
        self.statements += 1
        raise AssertionError("a SAVEPOINT opened")


OID = "00000000-0000-0000-0000-00000000000a"
PID = "00000000-0000-0000-0000-00000000000b"
RID = "00000000-0000-0000-0000-00000000000c"


def _apply(db: Any, writes: Any, actor: str = "ana@x.test", pid: str = PID) -> Any:
    return asyncio.run(store.apply_writes(db, actor, package_id=pid, writes=writes))


def test_batch_cap_is_50() -> None:
    assert sr.MAX_WRITES_PER_BATCH == 50
    writes = [store.Write("create", OID, data={}) for _ in range(50)]
    assert len(store._clean_writes(writes)) == 50
    db = _NoSQL()
    with pytest.raises(store.BudgetExceeded, match="at most 50 writes"):
        _apply(db, [*writes, writes[0]])
    assert db.statements == 0


def test_an_empty_batch_runs_nothing() -> None:
    db = _NoSQL()
    assert _apply(db, []) == []
    assert db.statements == 0


@pytest.mark.parametrize(("write", "fragment"), [
    (store.Write("upsert", OID), "the op create"),
    ("not a write", "the op create"),
    (store.Write("create", "nope", data={}), "object_id must be a UUID"),
    (store.Write("create", OID, RID, {}), "takes no record_id"),
    (store.Write("update", OID, "nope", {}), "record_id must be a UUID"),
    (store.Write("update", OID, None, {}), "record_id must be a UUID"),
    (store.Write("update", OID, RID, None), "changes must be an object"),
    (store.Write("update", OID, RID, "x"), "changes must be an object"),  # type: ignore[arg-type]
    (store.Write("delete", OID, RID, {"a": 1}), "takes no data"),
])
def test_a_bad_write_is_refused_with_its_place_before_any_statement(
        write: Any, fragment: str) -> None:
    db = _NoSQL()
    good = store.Write("create", OID, data={})
    with pytest.raises(store.BatchError) as refused:
        _apply(db, [good, write])
    assert refused.value.index == 1
    assert isinstance(refused.value.cause, store.ObjectStoreError)
    assert fragment in str(refused.value)
    assert db.statements == 0


@pytest.mark.parametrize("bad", ["x", b"x", {"a": 1}, 3, None])
def test_writes_must_be_a_list(bad: Any) -> None:
    db = _NoSQL()
    with pytest.raises(store.ObjectStoreError, match="must be a list"):
        _apply(db, bad)
    assert db.statements == 0


def test_a_bad_actor_or_package_runs_no_statement() -> None:
    db = _NoSQL()
    with pytest.raises(store.ObjectStoreError, match="actor"):
        _apply(db, [], actor=" ")
    with pytest.raises(store.ObjectStoreError, match="package_id"):
        _apply(db, [], pid="nope")
    assert db.statements == 0


def test_a_database_error_keeps_its_text_out_of_the_batch_error() -> None:
    """The text of a database error holds the statement and its binds, and so
    member data. A refusal gives its sentence."""
    class Loud(Exception):
        def __str__(self) -> str:
            return "INSERT ... secret value"

    assert "secret value" not in str(store.BatchError(1, Loud()))
    assert "Loud" in str(store.BatchError(1, Loud()))
    assert "Write 2" in str(store.BatchError(2, ValueError("the sentence")))
    assert "the sentence" in str(store.BatchError(2, ValueError("the sentence")))


# Each statement of C1b names the bound org. RLS is the backstop, and it hides
# a missing filter from each test on a real database, so a source fence holds it.
_OF_ORG = f"organization_id = {store._BOUND_ORG}"
_FOR_PACKAGE = "package_id = CAST(:pid AS uuid)"


@pytest.mark.parametrize("name", [
    "_SHARE_OBJECT_SQL", "_LOCK_PACKAGE_OBJECT_SQL", "_SET_RULES_SQL", "_LOCK_RECORD_SQL",
])
def test_each_statement_of_a_batch_names_the_org_and_the_package(name: str) -> None:
    """The org filter of the statement itself. `_IN_PACKAGE` names the org of
    the object row, and that one does not cover the record row."""
    sql = getattr(store, name)
    assert re.search(r"(?<![\w.])" + re.escape(_OF_ORG), sql), name
    assert _FOR_PACKAGE in sql, name


def _aliases(sql: str) -> list[str]:
    return re.findall(r"\b(?:FROM|JOIN)\s+studio_\w+\s+(\w+)", sql)


def _built_sums() -> list[str]:
    fields = [sf.parse_field_spec(s) for s in _SPECS]
    text_key = sq.parse_query(fields, sr.key_filter(GROUP, ("a", "open"))).conditions
    long_key = sq.parse_query(fields, sr.key_filter(UNIQUE, ("x" * 300,))).conditions
    number = _rule({"name": "n", "kind": "unique", "fields": ["qty"]})
    number_key = sq.parse_query(fields, sr.key_filter(number, (3,))).conditions
    out = []
    for conditions in (text_key, long_key, number_key):
        out.append(store.build_group_sum(conditions, "qty", object_id=OID, package_id=PID)[0])
        out.append(store.build_count(conditions, object_id=OID, package_id=PID, cap=2)[0])
    return out


def test_the_sum_is_null_only_for_a_key_with_no_rows() -> None:
    """The join is from the group rows to the amount rows, so a row with no
    amount stays in the count."""
    sql = _built_sums()[0]
    assert "LEFT JOIN LATERAL" in sql and "CROSS JOIN LATERAL" not in sql
    assert "CASE WHEN count(*) > 0 THEN coalesce(sum(s.v_num), 0) END" in sql


def test_each_table_of_a_check_names_the_org_and_each_check_the_package() -> None:
    for sql in _built_sums():
        aliases = _aliases(sql)
        assert aliases, sql
        for alias in aliases:
            assert f"{alias}.{_OF_ORG}" in sql or (
                alias == "o" and f"o.{_OF_ORG}" in sql), (alias, sql)
        assert store._IN_PACKAGE in sql, sql


def test_a_hostile_key_or_value_never_reaches_the_sql_of_a_check() -> None:
    """Each key and each value is a bind, so a quote stays in the binds."""
    hostile = "x'; DROP TABLE studio_records; --"
    field = sf.parse_field_spec({"key": "sku", "type": "text", "hot": True})
    conditions = sq.parse_query([field], [{"field": "sku", "op": "eq", "value": hostile}]).conditions
    sql, params = store.build_group_sum(conditions, hostile, object_id=OID, package_id=PID)
    assert "DROP TABLE" not in sql
    assert hostile in params.values()
    assert params["sk"] == hostile
