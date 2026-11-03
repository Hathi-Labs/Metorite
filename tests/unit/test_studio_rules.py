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
    ({"name": "r", "kind": "average"}, "the kind is"),
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
        # C7b: the derived write of a roll-up parent is the one other writer. It is
        # a store write: formulas, validation and hot rows, and its checks run in
        # `_check_records`. It writes no create and no delete.
        want = ["_apply_write", "_derive_parent"] if statement == "_UPDATE_RECORD_SQL"             else ["_apply_write"]
        assert users == want, (statement, users)
    derive = _calls(funcs["_derive_parent"])
    assert {"run_formulas", "validate_record", "record_json", "_hot_rows"} <= derive
    assert {"_recompute_parents", "_find_rollups"} <= _calls(steps)


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


# ── C6: the state machine (WS-STU STU-C6) ──────────────────────────────────
#
# Mutations (R7) and the test that goes red for each. Commit before each.
#   M1. check_move: drop the from/to match (let any change pass).
#       test_a_change_needs_a_declared_move, test_a_move_that_is_not_declared_is_refused
#   M2. check_move: the guard reads `before`.
#       test_the_guard_reads_the_merged_record_after_the_change
#   M3. check_move: skip the create check.
#       test_a_create_must_hold_the_initial_state
#   M4. touched_keys: remove STATE_MACHINE from the early return.
#       test_a_state_machine_touches_no_key
#   M5. _parse_atom / _parse_move: a role key passes.
#       test_a_role_guard_waits_for_c8
#   M6. objects_store: judge each write, not the net (before = state of the last write).
#       test_r8_a_batch_judges_the_net_move (test_studio_state_machine_r8.py)

_PO_SPECS: list[dict[str, Any]] = [
    {"key": "status", "type": "select", "options": ["draft", "approved", "sent", "void"]},
    {"key": "total", "type": "number"},
    {"key": "note", "type": "text"},
    {"key": "urgent", "type": "checkbox"},
    {"key": "due", "type": "date"},
    {"key": "at", "type": "datetime"},
    {"key": "price", "type": "money"},
    {"key": "tags", "type": "multiSelect", "options": ["a", "b"]},
    {"key": "mode", "type": "select", "options": ["a", "b"]},
]
PO_FIELDS = [sf.parse_field_spec(s) for s in _PO_SPECS]

_MACHINE: dict[str, Any] = {
    "name": "po_flow", "kind": "state_machine", "field": "status", "initial": "draft",
    "moves": [
        {"from": "draft", "to": "approved"},
        {"from": "draft", "to": "sent"},
        {"from": "approved", "to": "sent",
         "guard": [{"field": "total", "op": "lte", "value": 1000}]},
        {"from": "approved", "to": "void"},
    ],
}


def _machine(**over: Any) -> sr.Rule:
    return sr.parse_rule({**_MACHINE, **over})


def _bad_machine(**over: Any) -> ConstraintError:
    with pytest.raises(ConstraintError) as info:
        sr.parse_rule({**_MACHINE, **over})
    return info.value


def test_a_machine_parses_and_round_trips() -> None:
    rule = _machine()
    assert rule.kind == sr.STATE_MACHINE and rule.field == "status" and rule.initial == "draft"
    assert len(rule.moves) == 4
    assert rule.stored() == _MACHINE
    assert sr.stored_rules([rule.stored()]) == (rule,)


def test_the_closed_list_of_kinds() -> None:
    kinds = ("range", "unique", "group_sum_min", "state_machine", "formula", "rollup")
    assert kinds == sr.RULE_KINDS
    assert sr.ROLLUP in sr._PARSERS and sr.ROLLUP in sr._KIND_KEYS
    assert sr.STATE_MACHINE in sr._PARSERS and sr.STATE_MACHINE in sr._KIND_KEYS
    assert sr.FORMULA in sr._PARSERS and sr.FORMULA in sr._KIND_KEYS


@pytest.mark.parametrize("over", [
    {"field": "Status"}, {"field": None}, {"initial": ""}, {"initial": 3},
    {"initial": "x" * 81}, {"moves": []}, {"moves": "draft"}, {"moves": [3]},
    {"moves": [{"from": "draft"}]}, {"moves": [{"from": "draft", "to": "draft"}]},
    {"moves": [{"from": "a", "to": "b"}, {"from": "a", "to": "b"}]},
    {"moves": [{"from": "a", "to": "b", "extra": 1}]},
    {"extra": 1},
])
def test_a_bad_machine_shape_is_refused(over: dict[str, Any]) -> None:
    exc = _bad_machine(**over)
    assert exc.rule == "po_flow"
    assert len(exc.message.split()) <= 30


def test_a_machine_holds_at_most_64_moves() -> None:
    moves = [{"from": f"s{i}", "to": f"s{i + 1}"} for i in range(sr.MAX_MOVES)]
    assert len(_machine(moves=moves).moves) == sr.MAX_MOVES
    moves.append({"from": "s64", "to": "s65"})
    assert "1 to 64 moves" in _bad_machine(moves=moves).message


def test_a_guard_holds_at_most_4_atoms() -> None:
    atom = {"field": "total", "op": "gte", "value": 1}
    ok = [{"from": "draft", "to": "sent", "guard": [atom] * 4}]
    assert len(_machine(moves=ok).moves[0].guard) == 4
    bad = [{"from": "draft", "to": "sent", "guard": [atom] * 5}]
    assert "1 to 4 atoms" in _bad_machine(moves=bad).message
    empty = [{"from": "draft", "to": "sent", "guard": []}]
    assert "1 to 4 atoms" in _bad_machine(moves=empty).message


@pytest.mark.parametrize("atom", [
    {"field": "total", "op": "contains", "value": 1},
    {"field": "total", "op": "lte"},
    {"field": "total", "op": "is_empty", "value": 1},
    {"field": "total", "op": "in", "value": 3},
    {"field": "total", "op": "in", "value": []},
    {"field": "total", "op": "eq", "value": {"a": 1}},
    {"field": "total", "op": "eq", "value": [1]},
    {"field": "total", "op": "eq", "value": None},
    {"field": "Total", "op": "eq", "value": 1},
    {"field": "total", "op": "eq", "value": 1, "or": []},
    "total > 1",
])
def test_a_bad_atom_is_refused(atom: Any) -> None:
    moves = [{"from": "draft", "to": "sent", "guard": [atom]}]
    assert _bad_machine(moves=moves).rule == "po_flow"


def test_an_in_atom_holds_at_most_20_values() -> None:
    atom = {"field": "total", "op": "in", "value": list(range(sr.MAX_GUARD_IN_VALUES))}
    moves = [{"from": "draft", "to": "sent", "guard": [atom]}]
    assert _machine(moves=moves).moves[0].guard[0].value == tuple(range(20))
    atom["value"] = list(range(21))
    assert "1 to 20 values" in _bad_machine(moves=moves).message


@pytest.mark.parametrize("key", ["role", "roles"])
def test_a_role_guard_waits_for_c8(key: str) -> None:
    on_move = [{"from": "draft", "to": "sent", key: "director"}]
    on_atom = [{"from": "draft", "to": "sent", "guard": [
        {"field": "total", "op": "gte", "value": 1, key: "director"}]}]
    for moves in (on_move, on_atom):
        msg = _bad_machine(moves=moves).message
        assert "C8" in msg and "role" in msg


def test_a_stored_machine_with_a_role_fails_closed() -> None:
    item = {**_MACHINE, "moves": [{"from": "draft", "to": "sent", "role": "director"}]}
    (rule,) = sr.stored_rules([item])
    assert rule.kind == sr.MALFORMED
    with pytest.raises(RuleViolation):
        sr.check_kind_known(rule, "po")


def test_a_stored_machine_of_a_bad_shape_fails_closed() -> None:
    (rule,) = sr.stored_rules([{"name": "m", "kind": "state_machine", "field": "status"}])
    assert rule.kind == sr.MALFORMED and rule.name == "m"


def test_a_field_has_one_machine() -> None:
    other = {**_MACHINE, "name": "second"}
    with pytest.raises(ConstraintError, match="one state machine"):
        sr.parse_rules([_MACHINE, other])
    assert len(sr.parse_rules([_MACHINE, {**other, "field": "mode", "initial": "a"}])) == 2


def test_the_machine_shares_the_16_rule_cap() -> None:
    rules = [{**_MACHINE, "name": f"m{i}", "field": f"f{i}"} for i in range(17)]
    with pytest.raises(ConstraintError, match="at most 16"):
        sr.parse_rules(rules)


def test_a_machine_fits_a_select() -> None:
    sr.check_rules_fit([_machine()], PO_FIELDS)


@pytest.mark.parametrize("over,needle", [
    ({"field": "total"}, "must be one select"),
    ({"field": "tags"}, "must be one select"),
    ({"field": "ghost"}, "not a field"),
    ({"initial": "nope"}, "initial"),
    ({"moves": [{"from": "draft", "to": "nope"}]}, "moves[0].to"),
    ({"moves": [{"from": "nope", "to": "sent"}]}, "moves[0].from"),
])
def test_a_machine_that_does_not_fit_is_refused(over: dict[str, Any], needle: str) -> None:
    with pytest.raises(ConstraintError, match=re.escape(needle)):
        sr.check_rules_fit([_machine(**over)], PO_FIELDS)


def test_a_machine_field_may_be_cold() -> None:
    assert not next(f for f in PO_FIELDS if f.key == "status").hot
    sr.check_rules_fit([_machine()], PO_FIELDS)
    assert _machine().hot_keys() == ()


@pytest.mark.parametrize("atom,needle", [
    ({"field": "ghost", "op": "eq", "value": 1}, "not a field"),
    ({"field": "price", "op": "eq", "value": 1}, "cannot test"),
    ({"field": "tags", "op": "is_empty"}, "cannot test"),
    ({"field": "note", "op": "lt", "value": "a"}, "cannot test"),
    ({"field": "at", "op": "gt", "value": "2026-01-01T00:00:00Z"}, "cannot test"),
    ({"field": "total", "op": "eq", "value": "ten"}, "value of that field"),
    ({"field": "mode", "op": "eq", "value": "zzz"}, "value of that field"),
    ({"field": "due", "op": "eq", "value": "2026-02-30"}, "value of that field"),
    ({"field": "urgent", "op": "eq", "value": 1}, "value of that field"),
])
def test_a_guard_that_does_not_fit_is_refused(atom: dict[str, Any], needle: str) -> None:
    moves = [{"from": "draft", "to": "sent", "guard": [atom]}]
    with pytest.raises(ConstraintError, match=needle):
        sr.check_rules_fit([_machine(moves=moves)], PO_FIELDS)


def test_the_guard_ops_are_a_subset_of_the_c2a_table() -> None:
    """A fence. A guard op is an op of the query grammar, bar `not_empty`, which
    is the negation of `is_empty`. No type gains an op that C2a lacks."""
    assert set(sr.GUARD_OPS) - {"not_empty"} <= set(sq.OPS)
    for kind, ops in sr.GUARD_OPS_BY_TYPE.items():
        assert ops - {"not_empty"} <= sq.OPS_BY_TYPE[kind], kind
        assert ("not_empty" in ops) == ("is_empty" in sq.OPS_BY_TYPE[kind]), kind
    assert "money" not in sr.GUARD_OPS_BY_TYPE
    assert not sr.GUARD_OPS_BY_TYPE["datetime"] & {"lt", "lte", "gt", "gte"}


def _check(before: dict[str, Any] | None, after: dict[str, Any] | None,
           rule: sr.Rule | None = None) -> None:
    sr.check_move(rule or _machine(), "po", before, after)


def _refused(before: dict[str, Any] | None, after: dict[str, Any] | None,
             rule: sr.Rule | None = None) -> sr.MoveViolation:
    with pytest.raises(sr.MoveViolation) as info:
        _check(before, after, rule)
    return info.value


def test_a_create_must_hold_the_initial_state() -> None:
    _check(None, {"status": "draft"})
    err = _refused(None, {"status": "sent"})
    assert err.rule == "po_flow" and err.object == "po"
    assert err.from_state is None and err.to_state == "sent"
    assert _refused(None, {"note": "x"}).to_state is None


def test_a_change_needs_a_declared_move() -> None:
    _check({"status": "draft"}, {"status": "approved"})
    err = _refused({"status": "sent"}, {"status": "draft"})
    assert (err.from_state, err.to_state) == ("sent", "draft")


def test_a_move_that_is_not_declared_is_refused() -> None:
    # draft -> void is a pair of declared states and not a declared move.
    assert "cannot make this move" in str(_refused({"status": "draft"}, {"status": "void"}))


def test_an_unchanged_state_passes() -> None:
    _check({"status": "approved", "total": 5}, {"status": "approved", "total": 9})
    _check({"status": "legacy"}, {"status": "legacy", "note": "x"})


def test_a_cleared_state_is_refused() -> None:
    assert "cannot be cleared" in str(_refused({"status": "draft"}, {"note": "x"}))
    assert "cannot be cleared" in str(_refused({"status": "draft"}, {"status": ""}))


def test_a_stored_state_that_is_not_declared_is_refused() -> None:
    err = _refused({"status": "legacy"}, {"status": "sent"})
    assert err.from_state is None and err.to_state == "sent"
    assert "not declared" in str(err)
    assert "legacy" not in str(err)


def test_a_delete_is_not_a_move() -> None:
    _check({"status": "sent"}, None)
    _check(None, None)


def test_the_guard_reads_the_merged_record_after_the_change() -> None:
    """approved -> sent needs total <= 1000. The total is in `after`."""
    _check({"status": "approved", "total": 5000}, {"status": "sent", "total": 900})
    err = _refused({"status": "approved", "total": 900}, {"status": "sent", "total": 5000})
    assert "guard" in str(err)


def test_a_failed_guard_refuses_and_a_blank_value_fails_a_numeric_atom() -> None:
    _refused({"status": "approved"}, {"status": "sent"})
    _check({"status": "approved"}, {"status": "sent", "total": 1000})


def test_a_refusal_never_echoes_a_guard_value() -> None:
    moves = [{"from": "draft", "to": "sent",
              "guard": [{"field": "note", "op": "eq", "value": "SECRET-GUARD-VALUE"}]}]
    err = _refused({"status": "draft"}, {"status": "sent", "note": "other"},
                   _machine(moves=moves))
    assert "SECRET-GUARD-VALUE" not in str(err) and "other" not in str(err)
    assert err.rule == "po_flow"


def test_the_message_of_the_rule_replaces_the_default() -> None:
    err = _refused({"status": "sent"}, {"status": "draft"}, _machine(message="No way back."))
    assert str(err).endswith("No way back.")


@pytest.mark.parametrize("op,value,data,holds", [
    ("eq", "a", {"mode": "a"}, True), ("eq", "a", {"mode": "b"}, False),
    ("eq", "a", {}, False), ("ne", "a", {"mode": "b"}, True), ("ne", "a", {}, True),
    ("ne", "a", {"mode": "a"}, False), ("in", ("a", "b"), {"mode": "b"}, True),
    ("in", ("a",), {"mode": "b"}, False), ("in", ("a",), {}, False),
    ("is_empty", None, {}, True), ("is_empty", None, {"mode": "a"}, False),
    ("not_empty", None, {"mode": "a"}, True), ("not_empty", None, {}, False),
    ("lt", 5, {"mode": 4}, True), ("lt", 5, {"mode": 5}, False),
    ("lte", 5, {"mode": 5}, True), ("gt", 5, {"mode": 6}, True),
    ("gt", 5, {"mode": 5}, False), ("gte", 5, {"mode": 5}, True),
    ("gte", 5, {}, False), ("gte", 5, {"mode": "x"}, False),
])
def test_each_guard_op(op: str, value: Any, data: dict[str, Any], holds: bool) -> None:
    assert sr.guard_holds([sr.Atom("mode", op, value)], data) is holds


def test_every_atom_of_a_guard_must_hold() -> None:
    both = [sr.Atom("total", "gte", 1), sr.Atom("note", "not_empty")]
    assert sr.guard_holds(both, {"total": 2, "note": "x"})
    assert not sr.guard_holds(both, {"total": 2})
    assert not sr.guard_holds(both, {"note": "x"})
    assert sr.guard_holds([], {})


def test_a_create_takes_the_initial_state_when_the_field_is_absent() -> None:
    rules = (_machine(),)
    assert sr.with_initial(rules, {"total": 1}) == {"total": 1, "status": "draft"}
    assert sr.with_initial(rules, {"status": "sent"}) == {"status": "sent"}
    assert sr.with_initial(rules, "not a record") == "not a record"
    assert sr.with_initial((), {"total": 1}) == {"total": 1}


def test_with_initial_never_changes_its_input() -> None:
    data = {"total": 1}
    sr.with_initial((_machine(),), data)
    assert data == {"total": 1}


def test_a_state_machine_touches_no_key() -> None:
    rule = _machine()
    assert sr.touched_keys(rule, None, {"status": "draft"}) == []
    assert sr.touched_keys(rule, {"status": "draft"}, {"status": "sent"}) == []
    assert sr.touched_keys(rule, {"status": "draft"}, None) == []


def test_the_store_runs_the_machine_in_the_record_check_and_makes_the_initial_state() -> None:
    src = _STORE.read_text(encoding="utf-8")
    check = src[src.index("def _check_records"):src.index("async def _check_key(")]
    assert "STATE_MACHINE" in check and "check_move(rule, held.name, mark.before, mark.after)" in check
    assert "with_initial(held.rules, step.data)" in src


def test_the_manifest_uses_the_one_parser() -> None:
    src = (_ROOT / "packages/acb_common/acb_common/studio_manifest.py").read_text(encoding="utf-8")
    assert "parse_rules" in src and "check_rules_fit" in src
    for second in ("def parse_rule", "def _parse_machine", "MAX_MOVES ="):
        assert second not in src, second


# ── C6 fix round: the checkbox rule and a blank initial state ──────────────
#
#   F1. studio_rules._atom_holds: drop the line that reads an absent checkbox
#       as false. test_a_checkbox_guard_follows_the_c2a_rule goes red.
#   F2. studio_rules.with_initial: test `not in data` again.
#       test_a_blank_state_on_a_create_takes_the_initial_state goes red.


@pytest.mark.parametrize("op,value,data,holds", [
    ("eq", False, {}, True), ("eq", False, {"urgent": False}, True),
    ("eq", False, {"urgent": True}, False),
    ("ne", True, {}, True), ("ne", True, {"urgent": False}, True),
    ("ne", True, {"urgent": True}, False),
    ("eq", True, {}, False), ("eq", True, {"urgent": True}, True),
    ("eq", True, {"urgent": False}, False),
    ("ne", False, {}, False), ("ne", False, {"urgent": True}, True),
    ("ne", False, {"urgent": False}, False),
])
def test_a_checkbox_guard_follows_the_c2a_rule(
        op: str, value: bool, data: dict[str, Any], holds: bool) -> None:
    assert sr.guard_holds([sr.Atom("urgent", op, value)], data) is holds


def test_is_empty_on_a_checkbox_still_means_no_value() -> None:
    assert sr.guard_holds([sr.Atom("urgent", "is_empty")], {})
    assert not sr.guard_holds([sr.Atom("urgent", "is_empty")], {"urgent": False})
    assert sr.guard_holds([sr.Atom("urgent", "not_empty")], {"urgent": False})


@pytest.mark.parametrize("data", [
    {"status": None}, {}, {"status": ""}, {"status": "  "}])
def test_a_blank_state_on_a_create_takes_the_initial_state(data: dict[str, Any]) -> None:
    assert sr.with_initial((_machine(),), data)["status"] == "draft"


def test_a_state_that_is_set_stays_on_a_create() -> None:
    assert sr.with_initial((_machine(),), {"status": "sent"}) == {"status": "sent"}


# ── C7a: the formula kind (the hermetic half) ───────────────────────────────
#
# The mutations (R7). Commit, turn each red, restore.
#
#   G1. studio_rules.touched_keys: take FORMULA out of the early return.
#       test_a_formula_touches_no_key goes red.
#   G2. studio_rules.refuse_computed_keys: answer None.
#       test_a_key_for_a_target_is_refused_also_with_the_same_value goes red.
#   G3. studio_rules.formula_order: answer the declared order.
#       test_the_formulas_run_in_order_of_dependency goes red.
#   G4. studio_rules.run_formulas: skip the null rule (keep the old value).
#       test_a_null_result_clears_the_target goes red.


def _fields_c7() -> list[sf.FieldSpec]:
    specs: list[dict[str, Any]] = [
        {"key": "qty", "type": "number"}, {"key": "price", "type": "number"},
        {"key": "total", "type": "number"}, {"key": "double", "type": "number"},
        {"key": "fee", "type": "money"}, {"key": "tax", "type": "money"},
        {"key": "label", "type": "text"}, {"key": "full", "type": "text"},
        {"key": "done", "type": "checkbox"}, {"key": "flag", "type": "checkbox"},
        {"key": "day", "type": "date"}, {"key": "due", "type": "date"},
        {"key": "status", "type": "select", "options": ["open", "done"]},
        {"key": "needed", "type": "number", "required": True},
        {"key": "sizes", "type": "number", "many": True},
    ]
    return [sf.parse_field_spec(s) for s in specs]


F7 = _fields_c7()

_TOTAL: dict[str, Any] = {
    "name": "line_total", "kind": "formula", "target": "total", "rounding": "half_up",
    "scale": 2,
    "expr": {"fn": "mul", "args": [{"ref": "qty"}, {"ref": "price"}]},
}


def _formula(**over: Any) -> dict[str, Any]:
    return {**_TOTAL, **over}


def _bad_formula(**over: Any) -> ConstraintError:
    with pytest.raises(ConstraintError) as info:
        sr.parse_rule(_formula(**over))
    return info.value


def _fits(*specs: dict[str, Any]) -> None:
    sr.check_rules_fit(sr.parse_rules(list(specs)), F7)


def _unfit(*specs: dict[str, Any]) -> str:
    with pytest.raises(ConstraintError) as info:
        _fits(*specs)
    return info.value.message


def test_a_formula_parses_and_round_trips() -> None:
    rule = sr.parse_rule(_TOTAL)
    assert rule.kind == sr.FORMULA and rule.target == "total" and rule.scale == 2
    assert rule.rounding == "half_up" and rule.currency is None
    assert rule.stored() == _TOTAL
    assert sr.stored_rules([rule.stored()]) == (rule,)
    assert hash(rule) == hash(sr.parse_rule(_TOTAL))


def test_a_money_formula_stores_its_currency() -> None:
    spec = {"name": "tax_of", "kind": "formula", "target": "tax", "currency": "INR",
            "expr": {"fn": "mul", "args": [{"ref": "fee"}, {"num": "0.18"}], "rounding": "half_up"},
            "message": "Tax is 18 percent."}
    assert sr.parse_rule(spec).stored() == spec
    _fits(spec)


@pytest.mark.parametrize("over,fragment", [
    ({"target": "Total"}, "names a field by its key"), ({"target": None}, "names a field"),
    ({"scale": 5}, "`scale`"), ({"scale": -1}, "`scale`"), ({"scale": True}, "`scale`"),
    ({"scale": 1.5}, "`scale`"), ({"rounding": "round"}, "`rounding`"),
    ({"rounding": 1}, "`rounding`"), ({"currency": "inr"}, "`currency`"),
    ({"currency": 5}, "`currency`"), ({"extra": 1}, "is not a key of a formula rule"),
    ({"expr": {"ref": "a.b"}}, "`expr.ref`"), ({"expr": 5}, "`expr`"),
    ({"expr": {"num": 1}}, "decimal text"), ({"message": ""}, "`message` is text"),
])
def test_a_bad_formula_is_refused(over: dict[str, Any], fragment: str) -> None:
    assert fragment in _bad_formula(**over).message


def test_a_formula_needs_its_expr() -> None:
    spec = {k: v for k, v in _TOTAL.items() if k != "expr"}
    with pytest.raises(ConstraintError) as info:
        sr.parse_rule(spec)
    assert "needs `expr`" in info.value.message


def test_a_tree_over_a_cap_is_refused_at_declaration_with_the_path() -> None:
    deep: dict[str, Any] = {"num": "1"}
    for _ in range(8):
        deep = {"fn": "neg", "args": [deep]}
    message = _bad_formula(expr=deep).message
    assert "at `expr.args[0]" in message and "deeper than 8" in message


def test_an_object_holds_at_most_8_formulas() -> None:
    specs = [_formula(name=f"f{i}", target="total") for i in range(9)]
    with pytest.raises(ConstraintError) as info:
        sr.parse_rules(specs)
    assert "at most 8 formula rules" in info.value.message
    assert len(sr.parse_rules(specs[:8])) == 8


# -- the fit ------------------------------------------------------------------


def test_a_formula_that_fits_passes() -> None:
    _fits(_TOTAL)


@pytest.mark.parametrize("over,fragment", [
    ({"target": "ghost"}, "is not a field"),
    ({"target": "status"}, "must be one number, money, text"),
    ({"target": "sizes"}, "must be one number, money, text"),
    ({"target": "needed"}, "cannot be required"),
    ({"rounding": None}, "needs `rounding`"),
    ({"target": "full"}, "only a number target takes `scale` and `rounding`"),
    ({"currency": "INR"}, "only a money target takes `currency`"),
    ({"expr": {"ref": "ghost"}}, "`ghost` is not a field"),
    ({"expr": {"ref": "total"}}, "own target"),
    ({"expr": {"ref": "label"}}, "gives a text value"),
    ({"expr": {"ref": "status"}}, "cannot be a field of this type"),
])
def test_a_formula_that_does_not_fit_is_refused(over: dict[str, Any], fragment: str) -> None:
    spec = {k: v for k, v in _formula(**over).items() if v is not None}
    assert fragment in _unfit(spec)


def test_a_money_target_needs_a_currency() -> None:
    spec = {"name": "t", "kind": "formula", "target": "tax",
            "expr": {"fn": "add", "args": [{"ref": "fee"}, {"ref": "fee"}]}}
    assert "needs one" in _unfit(spec)


def test_a_target_takes_the_type_of_its_tree() -> None:
    spec = {"name": "t", "kind": "formula", "target": "flag", "expr": {"ref": "done"}}
    _fits(spec)
    assert "gives a bool value" in _unfit({**spec, "target": "total", "rounding": "up"})
    assert "gives a number value" in _unfit(
        {"name": "d", "kind": "formula", "target": "due",
         "expr": {"ref": "qty"}})


def test_two_formulas_cannot_fill_one_field() -> None:
    assert "another formula fills `total`" in _unfit(_TOTAL, _formula(name="again"))


def test_a_cycle_is_refused() -> None:
    a = {"name": "a", "kind": "formula", "target": "total", "rounding": "up",
         "expr": {"fn": "add", "args": [{"ref": "double"}, {"num": "1"}]}}
    b = {"name": "b", "kind": "formula", "target": "double", "rounding": "up",
         "expr": {"fn": "add", "args": [{"ref": "total"}, {"num": "1"}]}}
    assert "read each other in a loop" in _unfit(a, b)


def test_a_chain_of_formulas_is_not_a_cycle() -> None:
    base = {"name": "a", "kind": "formula", "target": "total", "rounding": "up",
            "expr": {"fn": "add", "args": [{"ref": "qty"}, {"num": "1"}]}}
    top = {"name": "b", "kind": "formula", "target": "double", "rounding": "up",
           "expr": {"fn": "mul", "args": [{"ref": "total"}, {"num": "2"}]}}
    _fits(top, base)


def test_the_formulas_run_in_order_of_dependency() -> None:
    base = sr.parse_rule({"name": "a", "kind": "formula", "target": "total", "rounding": "up",
                          "expr": {"fn": "add", "args": [{"ref": "qty"}, {"num": "1"}]}})
    top = sr.parse_rule({"name": "b", "kind": "formula", "target": "double", "rounding": "up",
                         "expr": {"fn": "mul", "args": [{"ref": "total"}, {"num": "2"}]}})
    other = sr.parse_rule({"name": "c", "kind": "formula", "target": "full",
                           "expr": {"fn": "upper", "args": [{"ref": "label"}]}})
    order = sr.formula_order([top, other, base])
    assert order is not None
    assert [r.name for r in order] == ["c", "a", "b"]
    assert [r.name for r in sr.formula_order([base, top, other]) or []] == ["a", "b", "c"]


def test_a_loop_has_no_order() -> None:
    a = sr.parse_rule({"name": "a", "kind": "formula", "target": "total", "rounding": "up",
                       "expr": {"ref": "double"}})
    b = sr.parse_rule({"name": "b", "kind": "formula", "target": "double", "rounding": "up",
                       "expr": {"ref": "total"}})
    assert sr.formula_order([a, b]) is None


# -- the other kinds read a target --------------------------------------------


def test_a_formula_touches_no_key() -> None:
    rule = sr.parse_rule(_TOTAL)
    assert sr.touched_keys(rule, None, {"total": 5}) == []
    assert sr.touched_keys(rule, {"total": 5}, {"total": 6}) == []
    assert sr.touched_keys(rule, {"total": 5}, None) == []
    assert rule.hot_keys() == ()


def test_the_kind_is_known_and_a_forged_one_fails_closed() -> None:
    sr.check_kind_known(sr.parse_rule(_TOTAL), "o")
    deep: dict[str, Any] = {"num": "1"}
    for _ in range(9):
        deep = {"fn": "neg", "args": [deep]}
    (forged,) = sr.stored_rules([{**_TOTAL, "expr": deep}])
    assert forged.kind == sr.MALFORMED
    with pytest.raises(RuleViolation):
        sr.check_kind_known(forged, "o")
    (alien,) = sr.stored_rules([{**_TOTAL, "extra": 1}])
    assert alien.kind == sr.MALFORMED


def test_a_range_and_a_guard_read_the_stored_result() -> None:
    plan = sr.plan_formulas([sr.parse_rule(_TOTAL)], "o", F7)
    record = sr.run_formulas(plan, "o", {"qty": 3, "price": 2.5, "needed": 1})
    assert record["total"] == 7.5
    rng = sr.parse_rule({"name": "cap", "kind": "range", "field": "total", "max": 7})
    with pytest.raises(ConstraintViolation):
        sr.check_range(rng, "o", record)
    ok = sr.parse_rule({"name": "cap", "kind": "range", "field": "total", "max": 8})
    sr.check_range(ok, "o", record)
    machine = sr.parse_rule({
        "name": "flow", "kind": "state_machine", "field": "status", "initial": "open",
        "moves": [{"from": "open", "to": "done",
                   "guard": [{"field": "total", "op": "lte", "value": 8}]}]})
    sr.check_move(machine, "o", {"status": "open", **record}, {"status": "done", **record})
    big = sr.run_formulas(plan, "o", {"qty": 4, "price": 2.5, "needed": 1})
    with pytest.raises(sr.MoveViolation):
        sr.check_move(machine, "o", {"status": "open"}, {"status": "done", **big})


# -- the plan and the run --------------------------------------------------------


def test_an_object_with_no_formula_has_no_plan() -> None:
    assert sr.plan_formulas([sr.parse_rule(_RANGE)], "o", FIELDS) is None
    assert sr.plan_formulas([], "o", FIELDS) is None
    assert sr.run_formulas(None, "o", {"a": 1}) == {"a": 1}
    assert sr.without_targets(None, {"a": 1}) == {"a": 1}
    sr.refuse_computed_keys(None, "o", {"a": 1})


def test_the_plan_splits_the_fields() -> None:
    plan = sr.plan_formulas([sr.parse_rule(_TOTAL)], "o", F7)
    assert plan is not None and plan.targets == frozenset({"total"})
    assert "total" not in {f.key for f in plan.plain}
    assert len(plan.plain) == len(F7) - 1 and len(plan.fields) == len(F7)


def test_a_stored_formula_that_does_not_fit_refuses_the_write() -> None:
    (forged,) = sr.stored_rules([_formula(expr={"ref": "ghost"})])
    with pytest.raises(sr.FormulaError) as info:
        sr.plan_formulas([forged], "po", F7)
    assert info.value.code == "declaration" and info.value.object == "po"
    assert info.value.rule == "line_total" and "ghost" in info.value.message


def test_a_stored_list_over_the_count_cap_refuses_the_plan() -> None:
    forged = sr.stored_rules([_formula(name=f"r{n}") for n in range(sr.MAX_COMPUTED_RULES + 1)])
    with pytest.raises(sr.FormulaError) as info:
        sr.plan_formulas(list(forged), "po", F7)
    assert info.value.code == "declaration" and info.value.object == "po"
    assert "at most 8" in info.value.message


@pytest.mark.parametrize("data", [
    {"total": 5}, {"total": None}, {"qty": 1, "total": 7.5}, {"total": "x"}])
def test_a_key_for_a_target_is_refused_also_with_the_same_value(data: dict[str, Any]) -> None:
    plan = sr.plan_formulas([sr.parse_rule(_TOTAL)], "o", F7)
    with pytest.raises(sr.FormulaError) as info:
        sr.refuse_computed_keys(plan, "o", data)
    assert info.value.code == sr.COMPUTED_FIELD and info.value.rule == "line_total"
    assert "`total` is computed" in info.value.message


def test_a_record_with_no_target_key_passes_the_refusal() -> None:
    plan = sr.plan_formulas([sr.parse_rule(_TOTAL)], "o", F7)
    sr.refuse_computed_keys(plan, "o", {"qty": 1})
    sr.refuse_computed_keys(plan, "o", "not a mapping")
    assert sr.without_targets(plan, {"qty": 1, "total": 9}) == {"qty": 1}


def test_a_chain_of_targets_reads_the_stored_result() -> None:
    chain = [
        sr.parse_rule({"name": "twice", "kind": "formula", "target": "double",
                       "rounding": "half_even",
                       "expr": {"fn": "mul", "args": [{"ref": "total"}, {"num": "2"}]}}),
        sr.parse_rule(_TOTAL),
    ]
    plan = sr.plan_formulas(chain, "o", F7)
    out = sr.run_formulas(plan, "o", {"qty": 3, "price": 1.005, "needed": 1})
    # 3 * 1.005 = 3.015 -> 3.02 at scale 2 (half_up). The next formula reads 3.02, not 3.015.
    assert out["total"] == 3.02 and out["double"] == 6
    assert list(out) == [f.key for f in F7 if f.key in out]


def test_a_null_result_clears_the_target() -> None:
    plan = sr.plan_formulas([sr.parse_rule(_TOTAL)], "o", F7)
    out = sr.run_formulas(plan, "o", {"qty": 3, "needed": 1})
    assert "total" not in out and out == {"qty": 3, "needed": 1}


def test_a_formula_fills_each_target_type() -> None:
    rules = sr.parse_rules([
        {"name": "t", "kind": "formula", "target": "tax", "currency": "INR",
         "expr": {"fn": "mul", "args": [{"ref": "fee"}, {"num": "0.18"}], "rounding": "half_up"}},
        {"name": "n", "kind": "formula", "target": "full",
         "expr": {"fn": "concat", "args": [{"ref": "label"}, {"text": "!"}]}},
        {"name": "f", "kind": "formula", "target": "flag",
         "expr": {"fn": "gt", "args": [{"ref": "qty"}, {"num": "1"}]}},
        {"name": "d", "kind": "formula", "target": "due",
         "expr": {"fn": "add_days", "args": [{"ref": "day"}, {"num": "30"}]}},
    ])
    plan = sr.plan_formulas(rules, "o", F7)
    data = {"fee": {"minor": 1000, "currency": "INR"}, "label": "ab", "qty": 2,
            "day": "2026-01-31", "needed": 1}
    out = sr.run_formulas(plan, "o", data)
    assert out["tax"] == {"minor": 180, "currency": "INR"} and out["full"] == "ab!"
    assert out["flag"] is True and out["due"] == "2026-03-02"


def test_a_failed_run_names_the_rule_and_the_object_and_no_value() -> None:
    rule = sr.parse_rule({
        "name": "ratio", "kind": "formula", "target": "total", "rounding": "up",
        "expr": {"fn": "div", "args": [{"ref": "qty"}, {"ref": "price"}],
                 "rounding": "up", "places": 2}})
    plan = sr.plan_formulas([rule], "po", F7)
    with pytest.raises(sr.FormulaError) as info:
        sr.run_formulas(plan, "po", {"qty": 987654, "price": 0, "needed": 1})
    err = info.value
    assert isinstance(err, RuleViolation)
    assert (err.rule, err.object, err.code) == ("ratio", "po", "division_by_zero")
    assert "987654" not in err.message and "`ratio`" in err.message and "`po`" in err.message


def test_a_rule_message_replaces_the_default_sentence() -> None:
    rule = sr.parse_rule({
        "name": "ratio", "kind": "formula", "target": "total", "rounding": "up",
        "message": "Price cannot be zero.",
        "expr": {"fn": "div", "args": [{"ref": "qty"}, {"ref": "price"}],
                 "rounding": "up", "places": 2}})
    plan = sr.plan_formulas([rule], "po", F7)
    with pytest.raises(sr.FormulaError) as info:
        sr.run_formulas(plan, "po", {"qty": 1, "price": 0, "needed": 1})
    assert info.value.message.endswith("Price cannot be zero.")


def test_a_result_over_the_limit_refuses() -> None:
    rule = sr.parse_rule({
        "name": "big", "kind": "formula", "target": "total", "rounding": "up", "scale": 2,
        "expr": {"fn": "mul", "args": [{"ref": "qty"}, {"ref": "price"}]}})
    plan = sr.plan_formulas([rule], "po", F7)
    with pytest.raises(sr.FormulaError) as info:
        sr.run_formulas(plan, "po", {"qty": 10**7, "price": 10**7, "needed": 1})
    assert info.value.code == "result_too_large"


# -- rule_reads ---------------------------------------------------------------------


def test_rule_reads_names_every_field_a_rule_reads() -> None:
    assert sr.rule_reads(sr.parse_rule(_RANGE)) == frozenset({"weight"})
    assert sr.rule_reads(sr.parse_rule(_UNIQUE)) == frozenset({"sku"})
    assert sr.rule_reads(sr.parse_rule(_GROUP)) == frozenset({"qty", "sku", "state"})
    machine = sr.parse_rule({
        "name": "flow", "kind": "state_machine", "field": "status", "initial": "open",
        "moves": [{"from": "open", "to": "done",
                   "guard": [{"field": "total", "op": "lte", "value": 7},
                             {"field": "needed", "op": "not_empty"}]}]})
    assert sr.rule_reads(machine) == frozenset({"status", "total", "needed"})
    assert sr.rule_reads(sr.parse_rule(_TOTAL)) == frozenset({"qty", "price"})
    assert sr.rule_reads(sr.Rule("x", sr.MALFORMED)) == frozenset()
    assert isinstance(sr.rule_reads(sr.parse_rule(_TOTAL)), frozenset)


# -- the fence (R7): the rule module and the store ------------------------------------


def test_the_rules_module_has_no_eval_no_clock_no_float_call_no_sql() -> None:
    tree = ast.parse(_RULES.read_text(encoding="utf-8"))
    banned_calls = {"eval", "exec", "compile", "float", "__import__"}
    calls = {n.func.id for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert not calls & banned_calls, calls & banned_calls
    attrs = {a.attr for a in ast.walk(tree) if isinstance(a, ast.Attribute)}
    assert not attrs & {"now", "today", "utcnow", "time", "monotonic", "execute"}
    imports = {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    imports |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not {i for i in imports if i.startswith(("sqlalchemy", "gateway", "psycopg"))}


def test_the_store_runs_the_formulas_in_the_write_and_checks_the_plan_once() -> None:
    funcs = _functions()
    write = ast.unparse(funcs["_apply_write"])
    assert "refuse_computed_keys" in write and "run_formulas" in write
    assert write.count("run_formulas") == 2 and write.count("refuse_computed_keys") == 2
    assert write.index("validate_record") < write.index("_CREATE_RECORD_SQL")
    assert "plan_formulas" in _calls(funcs["_lock_objects"])
    assert "_check_formulas_first" in _calls(funcs["set_constraints"])


def test_the_object_names_its_computed_fields() -> None:
    obj = store.StudioObject(
        id="i", package_id="p", name="o", label=None, created_by="a", created_at=None,  # type: ignore[arg-type]
        updated_at=None, fields=tuple(F7),  # type: ignore[arg-type]
        constraints=sr.parse_rules([_TOTAL, _RANGE]))
    assert obj.computed == ("total",)


# ── The roll-ups (C7b) ──────────────────────────────────────────────────────
#
# The mutations of the pure half (R7). Commit first, and restore with git.
#   H1. studio_rules.touched_keys: take ROLLUP out of the early return.
#       test_a_rollup_touches_no_key goes red.
#   H2. studio_rules.check_rollups: skip the key test of the target.
#       test_a_target_cannot_be_a_key_of_another_rule goes red.
#   H3. studio_rules.rollup_parents: answer only the new parent.
#       test_a_move_touches_the_old_and_the_new_parent goes red.
#   H4. studio_rules.check_rollup_currency: return at once.
#       test_a_child_in_another_currency_is_refused goes red.

_PARENT_ID = "0b9f5c1e-6f3a-4a52-9d57-8d3d3b0c8a11"
_OTHER_ID = "7c1d2e3f-0a1b-4c5d-8e6f-123456789abc"


def _child_fields() -> list[sf.FieldSpec]:
    specs: list[dict[str, Any]] = [
        {"key": "account", "type": "relation", "to": "account", "hot": True},
        {"key": "days", "type": "number", "hot": True},
        {"key": "amount", "type": "money", "hot": True},
        {"key": "day", "type": "date", "hot": True},
        {"key": "kind", "type": "select", "options": ["approved", "pending"], "hot": True},
        {"key": "note", "type": "text"},
        {"key": "cold_days", "type": "number"},
    ]
    return [sf.parse_field_spec(s) for s in specs]


def _parent_fields() -> list[sf.FieldSpec]:
    specs: list[dict[str, Any]] = [
        {"key": "balance", "type": "number"}, {"key": "spent", "type": "money"},
        {"key": "first_day", "type": "date"}, {"key": "name", "type": "text"},
        {"key": "must", "type": "number", "required": True},
        {"key": "stage", "type": "select", "options": ["a", "b"]},
    ]
    return [sf.parse_field_spec(s) for s in specs]


_SUM: dict[str, Any] = {"name": "bal", "kind": "rollup", "target": "balance", "source": "entry",
                        "via": "account", "agg": "sum", "field": "days"}


def _rollup(**over: Any) -> dict[str, Any]:
    return {k: v for k, v in {**_SUM, **over}.items() if v is not None}


def _source(rules: list[sr.Rule] | None = None) -> dict[str, sr.RollupSource]:
    return {"entry": sr.RollupSource("entry", tuple(_child_fields()), tuple(rules or ()))}


def _fit_rollup(*specs: dict[str, Any], fields: list[sf.FieldSpec] | None = None,
                sources: dict[str, sr.RollupSource] | None = None) -> None:
    sr.check_rules_fit(
        sr.parse_rules(list(specs)), _parent_fields() if fields is None else fields,
        object_name="account", sources=_source() if sources is None else sources)


def _unfit_rollup(*specs: dict[str, Any], **kw: Any) -> str:
    with pytest.raises(ConstraintError) as info:
        _fit_rollup(*specs, **kw)
    return info.value.message


@pytest.mark.parametrize("spec", [
    _SUM,
    _rollup(name="n", target="balance", agg="count", field=None),
    _rollup(name="m", target="first_day", agg="min", field="day", message="Mind the day."),
    _rollup(name="s", target="spent", field="amount", currency="INR"),
    _rollup(where=[{"field": "kind", "op": "eq", "value": "approved"}]),
    _rollup(where=[{"field": "kind", "op": "in", "value": ["approved", "pending"]},
                   {"field": "note", "op": "is_empty"}]),
    _rollup(where=[{"field": "account", "op": "is_empty", "value": False}]),
])
def test_a_rollup_parses_stores_and_reads_back_the_same(spec: dict[str, Any]) -> None:
    (rule,) = sr.parse_rules([spec])
    assert rule.kind == sr.ROLLUP and rule.stored() == spec
    assert sr.stored_rules([spec]) == (rule,)
    assert hash(rule) == hash(sr.parse_rules([spec])[0])


@pytest.mark.parametrize(("over", "fragment"), [
    ({"agg": "avg"}, "`agg` is sum, count, min, max"),
    ({"agg": None}, "`agg` is sum"),
    ({"source": "crm.entry"}, "bare name"),
    ({"source": "Entry"}, "bare name"),
    ({"source": None}, "bare name"),
    ({"via": "Account"}, "names a field"),
    ({"via": None}, "names a field"),
    ({"target": None}, "names a field"),
    ({"field": None}, "names a field"),
    ({"agg": "count"}, "a count takes no `field`"),
    ({"currency": "inr"}, "three capital letters"),
    ({"where": []}, "1 to 2 conditions"),
    ({"where": "x"}, "1 to 2 conditions"),
    ({"where": [{"field": "kind", "op": "eq", "value": "a"}] * 3}, "1 to 2 conditions"),
    ({"where": [3]}, "an object with `field` and `op`"),
    ({"where": [{"field": "kind", "op": "like", "value": "a"}]}, "a `where` op is"),
    ({"where": [{"field": "kind", "op": "eq"}]}, "needs a `value`"),
    ({"where": [{"field": "kind", "op": "in", "value": []}]}, "1 to 100 values"),
    ({"where": [{"field": "kind", "op": "is_empty", "value": "x"}]}, "true or false"),
    ({"where": [{"field": "kind", "op": "eq", "value": "a", "extra": 1}]}, "not a key of a `where`"),
    ({"extra": 1}, "`extra` is not a key"),
    ({"expr": {"ref": "a"}}, "`expr` is not a key"),
])
def test_a_bad_rollup_is_refused(over: dict[str, Any], fragment: str) -> None:
    with pytest.raises(ConstraintError) as info:
        sr.parse_rule(_rollup(**over))
    assert fragment in info.value.message


def test_an_object_holds_at_most_8_formula_and_rollup_rules_in_all() -> None:
    rules = [_rollup(name=f"r{n}", target=f"t{n}") for n in range(8)]
    assert len(sr.parse_rules(rules)) == 8
    mixed = [*rules[:7], _formula(name="f1"), _formula(name="f2", target="double")]
    with pytest.raises(ConstraintError) as info:
        sr.parse_rules(mixed)
    assert "formula and rollup rules in all" in info.value.message


def test_a_rollup_fits_its_source_and_its_object() -> None:
    _fit_rollup(_SUM)
    _fit_rollup(_rollup(name="c", target="balance", agg="count", field=None))
    _fit_rollup(_rollup(name="f", target="first_day", agg="max", field="day"))
    _fit_rollup(_rollup(name="s", target="spent", field="amount", currency="INR"))
    _fit_rollup(_rollup(where=[{"field": "kind", "op": "eq", "value": "approved"},
                               {"field": "days", "op": "gte", "value": 2}]))


@pytest.mark.parametrize(("over", "fragment"), [
    ({"source": "ghost"}, "another object of this package"),
    ({"via": "ghost"}, "is not a field of `entry`"),
    ({"via": "note"}, "one relation field"),
    ({"field": "ghost"}, "is not a field of `entry`"),
    ({"field": "note"}, "reads a number or money field"),
    ({"field": "cold_days"}, "must be one hot field"),
    ({"field": "day"}, "reads a number or money field"),
    ({"target": "ghost"}, "is not a field of this object"),
    ({"target": "name"}, "must be one number or money field"),
    ({"target": "must"}, "cannot be required"),
    ({"target": "spent"}, "only a money target takes `currency`"),
    ({"target": "spent", "field": "amount"}, "only a money target takes `currency`"),
    ({"currency": "INR"}, "only a money target takes `currency`"),
    ({"agg": "count", "field": None, "target": "first_day"}, "one number field for `count`"),
    ({"where": [{"field": "ghost", "op": "eq", "value": 1}]}, "`where`"),
    ({"where": [{"field": "note", "op": "eq", "value": "x"}]}, "must be one hot field"),
    ({"where": [{"field": "days", "op": "contains", "value": "x"}]}, "`where`"),
    ({"where": [{"field": "kind", "op": "eq", "value": "nope"}]}, "`where`"),
])
def test_a_rollup_that_does_not_fit_is_refused(over: dict[str, Any], fragment: str) -> None:
    assert fragment in _unfit_rollup(_rollup(**over))


def test_a_relation_that_points_elsewhere_or_is_not_hot_or_has_many_is_refused() -> None:
    def child(**over: Any) -> dict[str, sr.RollupSource]:
        spec = {"key": "account", "type": "relation", "to": "account", "hot": True, **over}
        fields = [sf.parse_field_spec(spec), sf.parse_field_spec(
            {"key": "days", "type": "number", "hot": True})]
        return {"entry": sr.RollupSource("entry", tuple(fields))}

    assert "must point to `account`" in _unfit_rollup(_SUM, sources=child(to="crm.account"))
    assert "must point to `account`" in _unfit_rollup(_SUM, sources=child(to="region"))
    assert "must be one hot field" in _unfit_rollup(_SUM, sources=child(hot=False))
    assert "one relation field" in _unfit_rollup(_SUM, sources=child(many=True, hot=False))


def test_a_source_must_be_another_object_and_a_check_needs_its_package() -> None:
    own = {"entry": sr.RollupSource("account", tuple(_child_fields()))}
    assert "another object of this package" in _unfit_rollup(_SUM, sources=own)
    with pytest.raises(ConstraintError) as info:
        sr.check_rules_fit(sr.parse_rules([_SUM]), _parent_fields())
    assert "other objects of its package" in info.value.message


def test_two_rollups_cannot_fill_one_field_and_a_formula_cannot_either() -> None:
    assert "fills `balance` already" in _unfit_rollup(_SUM, _rollup(name="again"))
    formula = _formula(name="f", target="balance", expr={"num": "1"}, rounding="half_up")
    assert "fills `balance` already" in _unfit_rollup(formula, _SUM)


@pytest.mark.parametrize("other", [
    {"name": "u", "kind": "unique", "fields": ["balance"]},
    {"name": "g", "kind": "group_sum_min", "field": "name", "group_by": ["balance"], "min": 0},
    {"name": "g", "kind": "group_sum_min", "field": "balance", "group_by": ["name"], "min": 0},
    {"name": "m", "kind": "state_machine", "field": "balance", "initial": "a",
     "moves": [{"from": "a", "to": "b"}]},
])
def test_a_target_cannot_be_a_key_of_another_rule(other: dict[str, Any]) -> None:
    fields = [sf.parse_field_spec({**s, "hot": True}) if s["key"] in ("balance", "name") else
              sf.parse_field_spec(s) for s in [
                  {"key": "balance", "type": "number"}, {"key": "name", "type": "text"},
                  {"key": "stage", "type": "select", "options": ["a", "b"]}]]
    message = _unfit_rollup(_SUM, other, fields=fields)
    assert "is a key of rule" in message and "fills no key" in message


def test_a_range_and_a_guard_may_name_a_rollup_target() -> None:
    fields = [*_parent_fields(), sf.parse_field_spec({"key": "stage2", "type": "select",
                                                      "options": ["a", "b"]})]
    machine = {"name": "m", "kind": "state_machine", "field": "stage2", "initial": "a",
               "moves": [{"from": "a", "to": "b", "guard": [
                   {"field": "balance", "op": "gte", "value": 0}]}]}
    _fit_rollup(_SUM, {"name": "ok", "kind": "range", "field": "balance", "min": 0}, machine,
                fields=fields)


def test_a_rollup_of_a_rollup_is_refused_directly_and_through_a_formula() -> None:
    below = sr.parse_rules([{"name": "b", "kind": "rollup", "target": "days", "source": "line",
                             "via": "entry", "agg": "sum", "field": "n"}])
    assert "cannot read a roll-up" in _unfit_rollup(_SUM, sources=_source(list(below)))
    tainted = sr.rollup_tainted([*below, sr.parse_rule(
        {"name": "t", "kind": "formula", "target": "x", "rounding": "up",
         "expr": {"fn": "add", "args": [{"ref": "days"}, {"num": "1"}]}})])
    assert tainted == {"days", "x"}
    assert sr.rollup_tainted([]) == frozenset()


def test_the_inputs_of_a_reader_may_not_be_filled_by_the_new_rules() -> None:
    reader = sr.parse_rule({"name": "over", "kind": "rollup", "target": "total",
                            "source": "account", "via": "region", "agg": "sum",
                            "field": "balance"})
    mine = sr.parse_rules([_SUM])
    with pytest.raises(ConstraintError) as info:
        sr.check_rollup_inputs("account", mine, [("region", reader)])
    assert "`over` of object `region`" in info.value.message
    sr.check_rollup_inputs("account", (), [("region", reader)])
    sr.check_rollup_inputs("account", mine, [])


def test_a_rollup_reads_no_key_of_its_own_object_but_names_the_source_keys() -> None:
    (rule,) = sr.parse_rules([_rollup(
        where=[{"field": "kind", "op": "eq", "value": "approved"},
               {"field": "days", "op": "gt", "value": 1}])])
    assert rule.hot_keys() == ()
    assert rule.source_keys() == ("account", "days", "kind")
    assert sr.parse_rules([{"name": "c", "kind": "rollup", "target": "balance", "source": "entry",
                            "via": "account", "agg": "count"}])[0].source_keys() == ("account",)
    assert sr.rule_reads(rule) == frozenset()
    assert sr.parse_rules([_RANGE])[0].source_keys() == ()


def test_a_rollup_touches_no_key() -> None:
    (rule,) = sr.parse_rules([_SUM])
    assert sr.touched_keys(rule, None, {"account": {"id": "a"}, "days": 1}) == []
    assert sr.touched_keys(rule, {"days": 1}, None) == []
    assert sr.touched_keys(rule, {"days": 1}, {"days": 2}) == []


def test_a_rollup_never_passes_a_hot_check_it_does_not_need() -> None:
    (rule,) = sr.parse_rules([_SUM])
    sr.check_rule_is_hot(rule, "account", _parent_fields())


def test_a_stored_rollup_that_cannot_be_read_fails_closed() -> None:
    (rule,) = sr.stored_rules([{"name": "bad", "kind": "rollup", "target": "balance"}])
    assert rule.kind == sr.MALFORMED and rule.name == "bad"
    with pytest.raises(ConstraintViolation):
        sr.check_kind_known(rule, "account")


# -- which parents a child write makes stale ---------------------------------------

_APPROVED = [{"field": "kind", "op": "eq", "value": "approved"}]


def _child(parent: str | None = _PARENT_ID, **data: Any) -> dict[str, Any]:
    out = dict(data)
    if parent is not None:
        out["account"] = {"id": parent}
    return out


def test_a_create_and_a_delete_touch_their_parent() -> None:
    (rule,) = sr.parse_rules([_SUM])
    assert sr.rollup_parents(rule, None, _child(days=1)) == [_PARENT_ID]
    assert sr.rollup_parents(rule, _child(days=1), None) == [_PARENT_ID]
    assert sr.rollup_parents(rule, None, None) == []


def test_a_move_touches_the_old_and_the_new_parent() -> None:
    (rule,) = sr.parse_rules([_SUM])
    got = sr.rollup_parents(rule, _child(_PARENT_ID, days=1), _child(_OTHER_ID, days=1))
    assert got == [_PARENT_ID, _OTHER_ID]


def test_an_update_touches_its_parent_only_when_a_read_field_changed() -> None:
    (rule,) = sr.parse_rules([_rollup(where=_APPROVED)])
    same = _child(days=1, kind="approved", note="a")
    assert sr.rollup_parents(rule, same, {**same, "note": "b"}) == []
    assert sr.rollup_parents(rule, same, {**same, "days": 2}) == [_PARENT_ID]
    assert sr.rollup_parents(rule, same, {**same, "kind": "pending"}) == [_PARENT_ID]
    assert sr.rollup_parents(rule, same, {**same, "cold_days": 9}) == []
    (count,) = sr.parse_rules([_rollup(name="c", agg="count", field=None)])
    assert sr.rollup_parents(count, same, {**same, "days": 9}) == []


def test_a_parent_in_capitals_or_a_spelling_without_dashes_is_the_same_parent() -> None:
    (rule,) = sr.parse_rules([_SUM])
    assert sr.rollup_parents(rule, None, _child(_PARENT_ID.upper())) == [_PARENT_ID]
    assert sr.rollup_parents(rule, None, _child(_PARENT_ID.replace("-", ""))) == [_PARENT_ID]
    assert sr.rollup_parents(rule, _child(_PARENT_ID, days=1),
                             _child(_PARENT_ID.upper(), days=1)) == []


@pytest.mark.parametrize("child", [
    {"days": 1}, {"account": None}, {"account": "x"}, {"account": {"id": "not-a-uuid"}},
    {"account": {"id": 5}}, {"account": {}},
])
def test_a_child_with_no_parent_or_a_dangling_one_touches_none(child: dict[str, Any]) -> None:
    (rule,) = sr.parse_rules([_SUM])
    assert sr.rollup_parents(rule, None, child) == []
    assert sr.rollup_parents(rule, child, None) == []


# -- the values -----------------------------------------------------------------------


def test_a_child_in_another_currency_is_refused() -> None:
    (rule,) = sr.parse_rules([_rollup(target="spent", field="amount", currency="INR")])
    sr.check_rollup_currency(rule, "account", {"amount": {"minor": 5, "currency": "INR"}})
    sr.check_rollup_currency(rule, "account", {"days": 1})
    with pytest.raises(sr.RollupError) as info:
        sr.check_rollup_currency(rule, "account", {"amount": {"minor": 5, "currency": "USD"}})
    assert info.value.code == "currency_mismatch" and "USD" not in info.value.message
    (plain,) = sr.parse_rules([_SUM])
    sr.check_rollup_currency(plain, "account", {"amount": {"minor": 5, "currency": "USD"}})


def test_an_empty_rollup_is_zero_for_a_sum_and_a_count_and_nothing_for_min_and_max() -> None:
    sums, count, low, high, money = sr.parse_rules([
        _rollup(name="a"), _rollup(name="b", agg="count", field=None),
        _rollup(name="c", agg="min", target="first_day", field="day"),
        _rollup(name="d", agg="max", target="first_day", field="day"),
        _rollup(name="e", target="spent", field="amount", currency="INR")])
    assert [sr.rollup_empty(r) for r in (sums, count)] == [0, 0]
    assert sr.rollup_empty(low) is None and sr.rollup_empty(high) is None
    assert sr.rollup_empty(money) == {"minor": 0, "currency": "INR"}


def test_the_zero_of_a_new_parent_comes_from_the_plan() -> None:
    fields = _parent_fields()
    rules = sr.parse_rules([
        _SUM, _rollup(name="m", target="first_day", agg="min", field="day"),
        _rollup(name="s", target="spent", field="amount", currency="INR")])
    plan = sr.plan_formulas(rules, "account", fields)
    assert plan is not None and plan.rollups and not plan.ordered
    assert sr.with_rollup_zero(plan, {"name": "x"}) == {
        "name": "x", "balance": 0, "spent": {"minor": 0, "currency": "INR"}}
    assert sr.with_rollup_zero(None, {"name": "x"}) == {"name": "x"}
    assert sr.with_rollup_zero(plan, "not a mapping") == "not a mapping"


def test_a_key_for_a_rollup_target_is_refused_also_with_the_same_value() -> None:
    plan = sr.plan_formulas(sr.parse_rules([_SUM]), "account", _parent_fields())
    for data in ({"balance": 0}, {"balance": None}, {"balance": 5}):
        with pytest.raises(sr.FormulaError) as info:
            sr.refuse_computed_keys(plan, "account", data)
        assert info.value.code == sr.COMPUTED_FIELD and info.value.rule == "bal"
    sr.refuse_computed_keys(plan, "account", {"name": "x"})
    # A roll-up target stays a plain field of the record: it is validated and kept.
    assert "balance" in [f.key for f in plan.plain]
    assert sr.without_targets(plan, {"balance": 4, "name": "x"}) == {"balance": 4, "name": "x"}


def test_a_stored_rollup_whose_target_is_gone_refuses_the_plan() -> None:
    fields = [f for f in _parent_fields() if f.key != "balance"]
    with pytest.raises(sr.FormulaError) as info:
        sr.plan_formulas(sr.parse_rules([_SUM]), "account", fields)
    assert info.value.code == "declaration"


@pytest.mark.parametrize(("rule", "raw", "want"), [
    (_SUM, None, 0), (_SUM, 12, 12), (_SUM, Decimal("3.75"), 3.75), (_SUM, Decimal("-7"), -7),
    (_rollup(name="c", agg="count", field=None), 4, 4),
    (_rollup(name="s", target="spent", field="amount", currency="INR"), Decimal(1500),
     {"minor": 1500, "currency": "INR"}),
    (_rollup(name="s", target="spent", field="amount", currency="INR"), None,
     {"minor": 0, "currency": "INR"}),
    (_rollup(name="m", agg="min", field="days"), None, None),
    (_rollup(name="m", agg="max", field="days"), Decimal(9), 9),
])
def test_a_rollup_result_is_the_stored_value(rule: dict[str, Any], raw: Any, want: Any) -> None:
    (parsed,) = sr.parse_rules([rule])
    field = next(f for f in _parent_fields() if f.key == parsed.target)
    assert sr.rollup_result(parsed, "account", field, raw) == want


def test_a_date_result_is_a_day() -> None:
    from datetime import UTC, datetime

    (rule,) = sr.parse_rules([_rollup(name="m", target="first_day", agg="min", field="day")])
    field = next(f for f in _parent_fields() if f.key == "first_day")
    assert sr.rollup_result(rule, "account", field, datetime(2026, 1, 31, tzinfo=UTC)) == "2026-01-31"


@pytest.mark.parametrize("raw", [Decimal(10**15), Decimal("-1E+15")])
def test_a_result_that_does_not_fit_is_refused_with_a_code_and_no_value(raw: Decimal) -> None:
    (rule,) = sr.parse_rules([_SUM])
    field = next(f for f in _parent_fields() if f.key == "balance")
    with pytest.raises(sr.RollupError) as info:
        sr.rollup_result(rule, "account", field, raw)
    assert info.value.code == "result_too_large" and str(raw) not in info.value.message


@pytest.mark.parametrize("raw", [
    Decimal("0.1234567890123456789"), Decimal("1000.123456789012345"), Decimal("0.00005")])
def test_a_number_sum_that_a_number_cannot_hold_exactly_is_refused_as_precision(raw: Decimal) -> None:
    """A sum far below 10^15 is not too large. It needs more digits than a
    stored number holds (or an exponent), and the message names the next step."""
    (rule,) = sr.parse_rules([_SUM])
    field = next(f for f in _parent_fields() if f.key == "balance")
    with pytest.raises(sr.RollupError) as info:
        sr.rollup_result(rule, "account", field, raw)
    assert info.value.code == sr.ROLLUP_PRECISION == "rollup_precision"
    assert "fewer decimal places" in info.value.message and "`days`" in info.value.message
    assert str(raw) not in info.value.message


def test_a_fractional_money_sum_is_refused() -> None:
    (rule,) = sr.parse_rules([_rollup(name="s", target="spent", field="amount", currency="INR")])
    field = next(f for f in _parent_fields() if f.key == "spent")
    with pytest.raises(sr.RollupError):
        sr.rollup_result(rule, "account", field, Decimal("1.5"))


def test_the_store_runs_the_rollup_steps_in_order_and_uses_the_one_aggregate_builder() -> None:
    funcs = _functions()
    steps = ast.unparse(funcs["_run_steps"])
    assert (steps.index("_lock_org(") < steps.index("_find_rollups(") < steps.index("_lock_objects(")
            < steps.index("_lock_records(") < steps.index("_apply_write(")
            < steps.index("_recompute_parents(") < steps.index("_check_records("))
    assert "build_rollup" in _calls(funcs["_recompute_parents"])
    assert "rollup_result" in _calls(funcs["_recompute_parents"])
    assert "with_rollup_zero" in _calls(funcs["_apply_write"])
    assert "_check_no_rollup_reads" in _calls(funcs["set_field_hot"])
    assert "check_rollup_inputs" in _calls(funcs["set_constraints"])
    assert "_check_rollups_fit" in _calls(funcs["_recompute_parents"])


def test_the_derived_write_is_a_store_write_that_checks_and_keeps_the_actor() -> None:
    src = ast.unparse(_functions()["_derive_parent"])
    assert "run_formulas" in src and "validate_record" in src and "_hot_rows" in src
    assert "batch.who or mark.updated_by" in src
    assert "INSERT" not in src and "_CREATE_RECORD_SQL" not in src  # a derived write adds no row


def test_the_rollup_sql_binds_the_org_the_package_and_the_object() -> None:
    fields = _child_fields()
    for rule in sr.parse_rules([_SUM, _rollup(name="c", agg="count", field=None),
                                _rollup(name="m", agg="min", field="day", target="first_day"),
                                _rollup(where=_APPROVED, name="w")]):
        sql, params = store.build_rollup(rule, fields, object_id=OID, package_id=PID,
                                         parent_id=_PARENT_ID)
        assert "app.tenant_id" in sql and "CAST(:pid AS uuid)" in sql
        assert _PARENT_ID not in sql and "approved" not in sql
        assert params["oid"] == OID and params["pid"] == PID and _PARENT_ID in params.values()
    assert "v_ts" in store.build_rollup(
        sr.parse_rules([_rollup(name="m", agg="min", field="day", target="first_day")])[0],
        fields, object_id=OID, package_id=PID, parent_id=_PARENT_ID)[0]


def test_a_hostile_where_value_never_reaches_the_rollup_sql() -> None:
    hostile = "x'; DROP TABLE studio_records; --"
    (rule,) = sr.parse_rules([_rollup(where=[{"field": "kind", "op": "in",
                                              "value": ["approved", hostile]}])])
    with pytest.raises(sq.QueryError):  # not an option of the field
        store.build_rollup(rule, _child_fields(), object_id=OID, package_id=PID,
                           parent_id=_PARENT_ID)


def test_each_table_of_the_rollup_sql_names_the_org() -> None:
    """RLS hides a missing bind from every R8 test, so the text is the fence.
    Each alias of `studio_records` and `studio_record_hot` carries the org
    predicate, for each aggregate, with and without `where`."""
    fields = _child_fields()
    specs = [_SUM, _rollup(name="c", agg="count", field=None),
             _rollup(name="lo", agg="min", field="day", target="first_day"),
             _rollup(name="hi", agg="max", field="day", target="first_day"),
             _rollup(name="w", where=_APPROVED),
             _rollup(name="wc", agg="count", field=None, where=_APPROVED)]
    for rule in sr.parse_rules(specs):
        sql = store.build_rollup(rule, fields, object_id=OID, package_id=PID,
                                 parent_id=_PARENT_ID)[0]
        aliases = _aliases(sql)
        assert aliases, sql
        for alias in aliases:
            assert f"{alias}.{_OF_ORG}" in sql, (rule.name, alias, sql)
        # every table that holds a row of a tenant is read through an alias
        assert len(aliases) == len(re.findall(r"\bstudio_\w+\s+\w+", sql)), sql
        assert sql.count(_OF_ORG) >= len(aliases), sql
