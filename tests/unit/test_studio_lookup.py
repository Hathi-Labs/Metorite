"""WS-STU STU-C19: lookup tables, the pure half and the source fences.

Spec: ``project-docs/specs/ai_studio.md`` section 14, row C19. Code: the
section "the lookup tables (C19)" at the end of ``acb_common/studio_rules.py``
and of ``gateway/studio/objects_store.py``. The R8 half is
``test_studio_lookup_r8.py``.

A lookup table is a plain object of the same package. A rule of kind
``lookup`` reads ONE row of it by its keys and fills a field. No row is a
refusal, never a zero.
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from acb_common import studio_fields as sf
from acb_common import studio_query as sq
from acb_common import studio_rules as sr
from acb_common.studio_rules import ConstraintError, FormulaError, LookupRefused, RollupSource
from gateway.studio import objects_store as store

_ROOT = Path(__file__).resolve().parents[2]
_STORE = _ROOT / "apps/services/gateway/gateway/studio/objects_store.py"

_CARD = (
    sf.parse_field_spec({"key": "lane", "type": "text", "hot": True}),
    sf.parse_field_spec({"key": "slab", "type": "number", "hot": True}),
    sf.parse_field_spec({"key": "starts", "type": "date", "hot": True}),
    sf.parse_field_spec({"key": "rate", "type": "money"}),
    sf.parse_field_spec({"key": "note", "type": "text"}),
)
_QUOTE = [
    sf.parse_field_spec({"key": "lane", "type": "text"}),
    sf.parse_field_spec({"key": "slab", "type": "number"}),
    sf.parse_field_spec({"key": "on", "type": "date"}),
    sf.parse_field_spec({"key": "rate", "type": "money"}),
    sf.parse_field_spec({"key": "kg", "type": "number"}),
    sf.parse_field_spec({"key": "freight", "type": "money"}),
]
_RATE: dict[str, Any] = {
    "name": "rate_of_lane", "kind": "lookup", "target": "rate", "source": "card",
    "field": "rate", "match": [{"field": "lane", "ref": "lane"}, {"field": "slab", "ref": "slab"}],
    "as_of": "on", "valid_from": "starts",
}
_SOURCES = {"card": RollupSource("card", _CARD)}


def _rule(spec: dict[str, Any]) -> sr.Rule:
    return sr.parse_rule(spec)


def _fit(*specs: dict[str, Any], fields: Any = None, sources: Any = _SOURCES) -> None:
    sr.check_rules_fit(sr.parse_rules(list(specs)), _QUOTE if fields is None else fields,
                       object_name="quote", sources=sources)


# -- the grammar -------------------------------------------------------------


def test_a_lookup_parses_and_round_trips() -> None:
    rule = _rule(_RATE)
    assert rule.kind == sr.LOOKUP
    assert rule.match == (("lane", "lane"), ("slab", "slab"))
    assert rule.stored() == {**_RATE}
    assert sr.parse_rule(rule.stored()) == rule
    assert sr.stored_rules([rule.stored()]) == (rule,)


@pytest.mark.parametrize(("change", "fragment"), [
    ({"match": []}, "1 to 3 keys"),
    ({"match": [{"field": "lane"}]}, "a `match` item"),
    ({"match": [{"field": "lane", "ref": "lane", "op": "lte"}]}, "a `match` item"),
    ({"match": [{"field": "lane", "ref": "lane"}, {"field": "lane", "ref": "slab"}]}, "twice"),
    ({"as_of": None}, "go together"),
    ({"valid_from": "lane"}, "cannot also be a key"),
    ({"source": "Card!"}, "bare name"),
    ({"zero": 0}, "is not a key of a lookup rule"),
])
def test_a_bad_lookup_is_refused_at_the_shape(change: dict[str, Any], fragment: str) -> None:
    spec = {k: v for k, v in {**_RATE, **change}.items() if v is not None}
    with pytest.raises(ConstraintError, match=fragment):
        _rule(spec)


def test_an_object_holds_at_most_four_lookups() -> None:
    specs = [{**_RATE, "name": f"l{i}"} for i in range(sr.MAX_LOOKUP_RULES + 1)]
    with pytest.raises(ConstraintError, match="at most 4 lookup rules"):
        sr.parse_rules(specs)


# -- the fit -----------------------------------------------------------------


def test_a_lookup_fits_its_source() -> None:
    _fit(_RATE)


@pytest.mark.parametrize(("change", "fragment"), [
    ({"source": "quote"}, "another object of this package"),
    ({"source": "nothing"}, "another object of this package"),
    ({"field": "note"}, "must be a text field"),
    ({"match": [{"field": "rate", "ref": "rate"}]}, "cannot be a key"),
    ({"match": [{"field": "lane", "ref": "kg"}]}, "must be a text field"),
    ({"match": [{"field": "lane", "ref": "freight"}]}, "is computed"),
    ({"as_of": "kg"}, "`as_of` must be one date field"),
    ({"valid_from": "note"}, "`valid_from` must be one date field"),
])
def test_a_lookup_that_does_not_fit_is_refused(change: dict[str, Any], fragment: str) -> None:
    formula = {"name": "freight", "kind": "formula", "target": "freight", "currency": "INR",
               "expr": {"fn": "mul", "args": [{"ref": "rate"}, {"ref": "kg"}],
                        "rounding": "half_up"}}
    with pytest.raises(ConstraintError, match=fragment):
        _fit({**_RATE, **change}, formula)


def test_a_key_of_the_source_must_be_hot() -> None:
    cold = tuple(sf.parse_field_spec({"key": f.key, "type": f.type}) if f.key == "lane" else f
                 for f in _CARD)
    with pytest.raises(ConstraintError, match="must be one hot field"):
        _fit(_RATE, sources={"card": RollupSource("card", cold)})


def test_a_lookup_needs_the_other_objects_of_the_package() -> None:
    with pytest.raises(ConstraintError, match="checked with the other objects"):
        sr.check_rules_fit((_rule(_RATE),), _QUOTE, object_name="quote")


def test_a_formula_reads_the_target_and_no_other_rule_fills_it() -> None:
    formula = {"name": "freight", "kind": "formula", "target": "freight", "currency": "INR",
               "expr": {"fn": "mul", "args": [{"ref": "rate"}, {"ref": "kg"}],
                        "rounding": "half_up"}}
    _fit(_RATE, formula)
    twice = {**formula, "target": "rate", "expr": {"money": {"minor": 1, "currency": "INR"}}}
    with pytest.raises(ConstraintError, match="fills `rate` already"):
        _fit(_RATE, {**twice, "name": "other"})


def test_a_required_target_is_refused() -> None:
    fields = [sf.parse_field_spec({"key": "rate", "type": "money", "required": True})
              if f.key == "rate" else f for f in _QUOTE]
    with pytest.raises(ConstraintError, match="cannot be required"):
        _fit(_RATE, fields=fields)


# -- the steps of a write ----------------------------------------------------


def test_a_caller_cannot_write_the_target_also_with_no_value() -> None:
    rules = (_rule(_RATE),)
    for data in ({"rate": {"minor": 1, "currency": "INR"}}, {"rate": None}):
        with pytest.raises(FormulaError) as refused:
            sr.refuse_lookup_keys(rules, "quote", data)
        assert refused.value.code == sr.COMPUTED_FIELD and refused.value.field == "rate"
    sr.refuse_lookup_keys(rules, "quote", {"lane": "x"})


def test_a_create_reads_the_row_and_an_update_reads_it_only_when_an_input_changed() -> None:
    rule = _rule(_RATE)
    now = {"lane": "a", "slab": 500, "on": "2026-03-01", "kg": 4}
    assert sr.lookup_stale(rule, None, now)
    assert not sr.lookup_stale(rule, now, {**now, "kg": 9})
    assert sr.lookup_stale(rule, now, {**now, "on": "2026-07-01"})
    assert sr.lookup_stale(rule, now, {**now, "slab": 1000})


def test_an_empty_input_reads_no_row() -> None:
    rule = _rule(_RATE)
    assert sr.lookup_inputs(rule, {"lane": "a", "slab": 500}) is None
    assert sr.lookup_inputs(rule, {"lane": " ", "slab": 500, "on": "2026-03-01"}) is None
    inputs = sr.lookup_inputs(rule, {"lane": "a", "slab": 500, "on": "2026-03-01"})
    assert sr.lookup_filter(rule, inputs or {}) == [
        {"field": "lane", "op": "eq", "value": "a"},
        {"field": "slab", "op": "eq", "value": 500},
        {"field": "starts", "op": "lte", "value": "2026-03-01"},
    ]


_TARGET = next(f for f in _QUOTE if f.key == "rate")
_DAY = datetime(2026, 1, 1, tzinfo=UTC)


def test_no_row_is_a_refusal_never_a_zero() -> None:
    rule = _rule(_RATE)
    with pytest.raises(LookupRefused) as refused:
        sr.lookup_result(rule, "quote", _TARGET, [])
    assert refused.value.code == sr.LOOKUP_MISSING and refused.value.field == "rate"
    assert "no row of `card` matches" in refused.value.message
    with pytest.raises(LookupRefused) as empty:
        sr.lookup_result(rule, "quote", _TARGET, [({"lane": "a"}, _DAY)])
    assert empty.value.code == sr.LOOKUP_MISSING and "holds no `rate`" in empty.value.message


def test_two_rows_on_one_date_are_ambiguous_and_a_later_date_wins() -> None:
    rule = _rule(_RATE)
    one = ({"rate": {"minor": 100, "currency": "INR"}}, _DAY)
    two = ({"rate": {"minor": 200, "currency": "INR"}}, _DAY)
    with pytest.raises(LookupRefused) as refused:
        sr.lookup_result(rule, "quote", _TARGET, [one, two])
    assert refused.value.code == sr.LOOKUP_AMBIGUOUS
    older = ({"rate": {"minor": 50, "currency": "INR"}}, datetime(2025, 1, 1, tzinfo=UTC))
    assert sr.lookup_result(rule, "quote", _TARGET, [one, older]) == {"minor": 100, "currency": "INR"}
    plain = _rule({k: v for k, v in _RATE.items() if k not in ("as_of", "valid_from")})
    with pytest.raises(LookupRefused, match="two rows"):
        sr.lookup_result(plain, "quote", _TARGET, [(one[0], None), (two[0], None)])


def test_a_refusal_never_echoes_a_value() -> None:
    rule = _rule(_RATE)
    secret = "BLR-DEL-SECRET"
    with pytest.raises(LookupRefused) as refused:
        sr.lookup_result(rule, "quote", _TARGET, [])
    assert secret not in refused.value.message
    bad = ({"rate": "not money " + secret}, _DAY)
    with pytest.raises(LookupRefused) as wrong:
        sr.lookup_result(rule, "quote", _TARGET, [bad])
    assert wrong.value.code == "bad_result" and secret not in wrong.value.message


def test_a_lookup_reads_its_own_keys_for_c9_and_queues_no_key_check() -> None:
    rule = _rule(_RATE)
    assert sr.rule_reads(rule) == frozenset({"lane", "slab", "on"})
    assert sr.lookup_source_keys(rule) == ("lane", "slab", "starts")
    assert rule.hot_keys() == ()
    assert sr.touched_keys(rule, None, {"lane": "a"}) == []


# -- the SQL -----------------------------------------------------------------


def test_the_read_binds_each_key_and_value_and_names_the_org_and_the_package() -> None:
    rule = _rule(_RATE)
    hostile = "x'); DROP TABLE studio_records; --"
    inputs = {"lane": hostile, "slab": 500, "on": "2026-03-01"}
    conditions = sq.parse_query(list(_CARD), sr.lookup_filter(rule, inputs)).conditions
    sql, params = store.build_lookup(rule, conditions, object_id="o", package_id="p")
    assert hostile not in sql and hostile in params.values()
    assert "starts" not in sql and params["vk"] == "starts"
    assert store._IN_PACKAGE in sql and f"organization_id = {store._BOUND_ORG}" in sql
    assert "ORDER BY s.v_ts DESC, r.id LIMIT 2" in sql
    plain = _rule({k: v for k, v in _RATE.items() if k not in ("as_of", "valid_from")})
    sql2, _ = store.build_lookup(plain, conditions[:2], object_id="o", package_id="p")
    assert "LIMIT 2" in sql2 and "v_ts DESC" not in sql2


def _functions() -> dict[str, ast.AsyncFunctionDef | ast.FunctionDef]:
    tree = ast.parse(_STORE.read_text(encoding="utf-8"))
    return {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)}


def _calls(node: ast.AST) -> set[str]:
    return {n.func.id for n in ast.walk(node)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}


def test_each_write_fills_the_lookups_before_the_formulas_and_refuses_a_target_key() -> None:
    """The write runs `_fill_lookups` on the checked record, and its answer is
    the input of `run_formulas`. A caller key for a target is refused first."""
    src = ast.unparse(_functions()["_apply_write"])
    assert src.count("_fill_lookups(") == 2
    assert src.count("refuse_lookup_keys(") == 2
    assert src.count("run_formulas(held.plan, held.name, await _fill_lookups(") == 2
    assert "_read_lookup" in _calls(_functions()["_fill_lookups"])
    read = _calls(_functions()["_read_lookup"])
    assert {"_lookup_source", "build_lookup", "_time_limit", "lookup_result"} <= read


def test_a_cold_flip_of_a_lookup_key_is_refused_by_the_store() -> None:
    assert "_check_no_lookup_reads" in _calls(_functions()["set_field_hot"])


def test_a_new_lookup_waits_for_an_object_with_no_record() -> None:
    src = ast.unparse(_functions()["_check_formulas_first"])
    assert "LOOKUP" in src
    assert store._computed_key(_rule(_RATE)) != store._computed_key(
        _rule({**_RATE, "valid_from": "lane2"}))
    assert store._computed_key(_rule(_RATE)) == store._computed_key(
        _rule({**_RATE, "name": "renamed", "message": "A new message."}))


def test_a_lookup_target_is_computed_in_the_description() -> None:
    obj = store.StudioObject(id="o", package_id="p", name="quote", label=None, created_by="a",
                             created_at=_DAY, updated_at=_DAY, fields=tuple(_QUOTE),
                             constraints=(_rule(_RATE),))
    assert obj.computed == ("rate",)


# -- the read budget of one batch --------------------------------------------


class _Reads:
    """A fake of `_time_limit` that counts the lookup reads and answers one row."""

    def __init__(self) -> None:
        self.count = 0

    async def execute(self, stmt: Any, params: dict[str, Any]) -> Any:
        self.count += 1
        rows = [({"rate": {"minor": 1000, "currency": "INR"}}, _DAY)]
        return type("Rows", (), {"all": lambda _self: rows})()


def _counted(monkeypatch: pytest.MonkeyPatch) -> _Reads:
    import contextlib

    reads = _Reads()

    @contextlib.asynccontextmanager
    async def limit(db: Any, deadline: float, message: str = "") -> Any:
        yield reads

    monkeypatch.setattr(store, "_time_limit", limit)
    return reads


def _batch() -> Any:
    batch = store._Batch("ana@x.test", "p", [])
    batch.lookups.sources["card"] = ("o", list(_CARD))
    return batch


def _read(batch: Any, rule: sr.Rule, **inputs: Any) -> Any:
    import asyncio

    held = store._Held(name="quote", fields=list(_QUOTE), rules=(rule,))
    keys = {"lane": "BLR-DEL", "slab": 500, "on": "2026-03-01", **inputs}
    return asyncio.run(store._read_lookup(None, batch, rule, held, keys, _TARGET))


def test_the_lookup_cap_comes_from_the_rule_cap_and_the_batch_cap() -> None:
    assert sr.MAX_LOOKUP_STATEMENTS == sr.MAX_LOOKUP_RULES * sr.MAX_WRITES_PER_BATCH
    assert store.MAX_LOOKUP_STATEMENTS is sr.MAX_LOOKUP_STATEMENTS
    assert issubclass(store.LookupBudgetExceeded, store.BudgetExceeded)


def test_a_second_read_with_the_same_keys_runs_no_statement(monkeypatch) -> None:
    reads = _counted(monkeypatch)
    batch, rule = _batch(), _rule(_RATE)
    assert _read(batch, rule) == _read(batch, rule) == {"minor": 1000, "currency": "INR"}
    assert reads.count == 1 and batch.lookups.statements == 1
    _read(batch, rule, slab=1000)
    assert reads.count == 2 and batch.lookups.statements == 2


def test_a_write_to_the_source_drops_its_kept_rows(monkeypatch) -> None:
    reads = _counted(monkeypatch)
    batch, rule = _batch(), _rule(_RATE)
    _read(batch, rule)
    batch.lookups.forget("other")
    _read(batch, rule)
    assert reads.count == 1
    batch.lookups.forget("o")
    _read(batch, rule)
    assert reads.count == 2


def test_49_lines_with_4_lookups_each_stay_under_the_cap(monkeypatch) -> None:
    """The largest legal save: a parent and 49 lines, each with 4 lookups that
    read 4 other rows. The 200 reads fit, and one more read is refused."""
    reads = _counted(monkeypatch)
    batch = _batch()
    rules = [_rule({**_RATE, "name": f"rate_{i}"}) for i in range(sr.MAX_LOOKUP_RULES)]
    for write in range(sr.MAX_WRITES_PER_BATCH):
        for i, rule in enumerate(rules):
            _read(batch, rule, lane=f"lane-{i}", slab=write)
    assert reads.count == sr.MAX_LOOKUP_STATEMENTS
    with pytest.raises(store.LookupBudgetExceeded):
        _read(batch, rules[0], lane="one-more")
    assert reads.count == sr.MAX_LOOKUP_STATEMENTS


def test_the_lookup_cap_has_its_own_sentence() -> None:
    from gateway.routes.studio import invoke

    out = invoke._Outcome(action="create")
    assert invoke._set_write_error(out, store.LookupBudgetExceeded("cap"))
    assert out.fail is not None and out.fail.reason == invoke.RECORD_INVALID
    detail = out.fail.error.detail
    assert detail["code"] == invoke.RECORD_INVALID
    assert detail["message"] == invoke.LOOKUP_BUDGET != invoke.STORE_REFUSED
    assert "lookup" in invoke.LOOKUP_BUDGET and len(invoke.LOOKUP_BUDGET.split()) <= 25
