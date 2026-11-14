"""WS-STU C2d-b: the cells of a matrix, the pure half.

Spec: ``project-docs/specs/ai_studio.md`` §14, the C2d-b row. The database half
is ``test_studio_aggregate_cells_r8.py``. The mutations (R7) are listed there.
"""

from __future__ import annotations

from typing import Any

import pytest
from acb_common import studio_fields as sf
from acb_common import studio_query as sq
from acb_common.studio_query import QueryError
from gateway.studio import objects_store as store

_SPECS: list[dict[str, Any]] = [
    {"key": "status", "type": "select", "options": ["open", "done"], "hot": True},
    {"key": "team", "type": "text", "hot": True},
    {"key": "day", "type": "date", "hot": True},
    {"key": "hours", "type": "number", "hot": True},
    {"key": "fee", "type": "money", "hot": True},
    {"key": "note", "type": "text"},
    {"key": "owner", "type": "person", "hot": True},
]
FIELDS = [sf.parse_field_spec(s) for s in _SPECS]
OID = PID = "0b9f5c1e-6f3a-4a52-9d57-8d3d3b0c8a11"
ACCESS = (["owner"], "ana@example.test")


def _agg(group_by: Any, filter: Any = None, measures: Any = None) -> sq.Aggregate:
    return sq.parse_aggregate(FIELDS, filter, group_by, measures)


def _refused(code: str, group_by: Any) -> QueryError:
    with pytest.raises(QueryError) as refused:
        _agg(group_by)
    assert refused.value.code == code, refused.value.message
    return refused.value


def test_the_cap_of_an_axis_is_20_and_a_matrix_has_400_cells_at_most() -> None:
    assert sq.MAX_AXIS_VALUES == 20 and sq.MAX_GROUP_KEYS == 2
    assert sq.MAX_AXIS_VALUES**2 == 400


def test_two_groups_make_a_row_axis_and_a_column_axis() -> None:
    agg = _agg([{"field": "status"}, {"field": "day", "bucket": "month"}])
    assert agg.group == sq.GroupBy(FIELDS[0])
    assert agg.group2 == sq.GroupBy(FIELDS[2], "month")
    assert agg.hot_keys() == ("day", "status")


def test_a_group_object_and_a_list_of_one_are_the_same_aggregate() -> None:
    one = _agg({"field": "status"})
    assert _agg([{"field": "status"}]) == one
    assert one.group2 is None
    assert sq.check_aggregate_shape(None, [{"field": "status"}])[1] == [{"field": "status"}]
    assert sq.check_aggregate_shape(None, None)[1] == []


@pytest.mark.parametrize("group_by", [
    [], [{"field": "status"}] * 3, "status", [["status"]], [{}], [{"field": "status", "dir": "x"}],
    [{"field": "status"}, {"field": "day", "bucket": "week"}],
])
def test_a_bad_group_list_is_a_shape_error(group_by: Any) -> None:
    _refused("query_value_invalid", group_by)
    with pytest.raises(QueryError):
        sq.check_aggregate_shape(None, group_by)


def test_the_same_field_twice_is_refused_even_with_two_buckets() -> None:
    err = _refused("query_value_invalid", [{"field": "status"}, {"field": "status"}])
    assert err.field == "status" and "twice" in err.message
    _refused("query_value_invalid", [{"field": "day", "bucket": "day"},
                                     {"field": "day", "bucket": "month"}])


def test_each_key_follows_the_rules_of_a_single_group() -> None:
    first = {"field": "status"}
    for bad, code in (({"field": "fee"}, "query_op_not_allowed"),
                      ({"field": "owner"}, "query_op_not_allowed"),
                      ({"field": "note"}, "query_not_indexed"),
                      ({"field": "nope"}, "query_field_unknown"),
                      ({"field": "team", "bucket": "day"}, "query_value_invalid")):
        _refused(code, [first, bad])
        _refused(code, [bad, first])


def test_a_bucket_on_the_second_key_defaults_to_day_and_takes_month() -> None:
    assert _agg([{"field": "status"}, {"field": "day"}]).group2 == sq.GroupBy(FIELDS[2], "day")
    sql, _ = store.build_aggregate_cells(
        _agg([{"field": "status"}, {"field": "day", "bucket": "month"}]),
        object_id=OID, package_id=PID)
    assert "date_trunc('month', g2k.v_ts" in sql and "date_trunc('day'" not in sql


def test_the_input_schema_takes_a_list_of_two_groups() -> None:
    group_by = sq.aggregate_input_schema()["properties"]["group_by"]["anyOf"]
    assert group_by[0]["required"] == ["field"], "one group stays the first form"
    assert {"type": "array", "minItems": 1, "maxItems": 2, "items": group_by[0]} in group_by


def test_one_key_builds_the_statement_it_built_before_and_no_cells_statement() -> None:
    plain = _agg({"field": "status"}, measures=[{"op": "count"}, {"op": "sum", "field": "hours"}])
    listed = _agg([{"field": "status"}], measures=[{"op": "count"}, {"op": "sum", "field": "hours"}])
    one = store.build_aggregate(plain, object_id=OID, package_id=PID)
    assert store.build_aggregate(listed, object_id=OID, package_id=PID) == one
    assert "g2k" not in one[0] and "g2k" not in one[1] and "axis" not in one[1]


def test_the_cells_statement_holds_the_row_rule_in_each_part_and_the_cap_bind() -> None:
    agg = _agg([{"field": "status"}, {"field": "team"}], filter=[
        {"field": "note", "op": "contains", "value": "x"}], measures=[
        {"op": "sum", "field": "hours"}])
    sql, params = store.build_aggregate_cells(agg, object_id=OID, package_id=PID, access=ACCESS)
    assert params["me"] == ACCESS[1] and params["akeys"] == ["owner"]
    assert params["axis"] == sq.MAX_AXIS_VALUES
    assert params["gk"] == "status" and params["g2k"] == "team"
    assert sql.count("ac.v_text = CAST(:me AS text)") == sql.count("EXISTS (SELECT 1 FROM studio_record_hot ac")
    assert sql.count(":me") >= 2, "the guard and the candidates both hold the row rule"
    for value in ("status", "team", "hours"):
        assert value not in sql, "a key is a bind, never SQL text"
    owner_sql, owner_params = store.build_aggregate_cells(agg, object_id=OID, package_id=PID)
    assert ":me" not in owner_sql and "me" not in owner_params


def test_the_cell_rows_become_cells_in_order_and_past_the_cap_they_raise() -> None:
    agg = _agg([{"field": "status"}, {"field": "team"}])
    head = (1, None, 2, 2)

    def row(k: Any, k2: Any, n: Any) -> tuple[Any, ...]:
        return (*head, k, k2, n)
    got = store._cell_rows(agg, [row("done", "b", 2), row("open", None, 1), row("open", "a", 3),
                                 row("done", "a", 4)])
    assert [(c.key, c.key2, c.count) for c in got] == [
        ("open", "a", 3), ("open", None, 1), ("done", "a", 4), ("done", "b", 2)]
    assert got[0].values == (3,)
    assert store._cell_rows(agg, [(*head, None, None, None, None, None)]) == ()
    with pytest.raises(QueryError) as big:
        store._cell_rows(agg, [(1, None, 21, 2, None, None, None)])
    assert big.value.code == "query_too_big" and "Filter first" in big.value.message
    with pytest.raises(QueryError):
        store._cell_rows(agg, [(1, None, 2, 21, None, None, None)])
    assert store._cell_rows(agg, [(1, None, 20, 20, None, None, None)]) == ()
