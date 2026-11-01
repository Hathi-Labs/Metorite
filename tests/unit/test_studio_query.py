"""WS-STU C2a — the object query, with no database.

Spec: ``project-docs/specs/ai_studio.md`` §3.1 and the §14 C2a row. Code:
``acb_common/studio_query.py`` (the grammar) and the query section of
``gateway/studio/objects_store.py`` (the SQL). ``test_studio_query_r8.py``
runs the SQL on a real Postgres.

**The mutation Q3 (R7).** In ``objects_store._q_hot``, write the key into
the SQL: ``row = _q_hot_row(i, rid).replace(f"CAST(:fk{i} AS text)", f"'{cond.field.key}'")``.
``test_no_fstring_in_the_query_sql_takes_a_value`` and
``test_a_hostile_key_or_value_never_reaches_the_sql`` go red.
"""

from __future__ import annotations

import ast
import re
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from acb_common import studio_fields as sf
from acb_common import studio_query as sq
from acb_common.paging import page_line
from acb_common.studio_query import Condition, Query, QueryError
from gateway.studio import objects_store as store

_ROOT = Path(__file__).resolve().parents[2]
_STORE = _ROOT / "apps/services/gateway/gateway/studio/objects_store.py"
_GRAMMAR = _ROOT / "packages/acb_common/acb_common/studio_query.py"

_SPECS: list[dict[str, Any]] = [
    {"key": "name", "type": "text", "hot": True},
    {"key": "mail", "type": "email", "hot": True},
    {"key": "status", "type": "select", "options": ["open", "done"], "hot": True},
    {"key": "hours", "type": "number", "hot": True},
    {"key": "fee", "type": "money", "hot": True},
    {"key": "day", "type": "date", "hot": True},
    {"key": "at", "type": "datetime", "hot": True},
    {"key": "paid", "type": "checkbox", "hot": True},
    {"key": "note", "type": "text"},
    {"key": "body", "type": "longText"},
    {"key": "tags", "type": "text", "many": True},
    {"key": "owner", "type": "person"},
]
FIELDS = [sf.parse_field_spec(s) for s in _SPECS]
BY_KEY = {f.key: f for f in FIELDS}


def _parse(filter: Any = None, sort: Any = None) -> Query:
    return sq.parse_query(FIELDS, filter, sort)


def _one(field: str, op: str, value: Any = None) -> Condition:
    item: dict[str, Any] = {"field": field, "op": op}
    if value is not None:
        item["value"] = value
    return _parse([item]).conditions[0]


def _refused(code: str, filter: Any = None, sort: Any = None) -> QueryError:
    with pytest.raises(QueryError) as refused:
        _parse(filter, sort)
    assert refused.value.code == code, (refused.value.code, refused.value.message)
    return refused.value


# ── The op table ────────────────────────────────────────────────────────────


def test_the_op_table_of_each_type() -> None:
    assert dict(sq.OPS_BY_TYPE) == {
        "text": {"eq", "ne", "in", "contains", "is_empty"},
        "email": {"eq", "ne", "in", "contains", "is_empty"},
        "select": {"eq", "ne", "in", "is_empty"},
        "number": {"eq", "ne", "lt", "lte", "gt", "gte", "in", "is_empty"},
        "money": {"eq", "ne", "lt", "lte", "gt", "gte", "in", "is_empty"},
        "date": {"eq", "lt", "lte", "gt", "gte", "is_empty"},
        "datetime": {"eq", "lt", "lte", "gt", "gte", "is_empty"},
        "checkbox": {"eq", "ne"},
    }
    # The op table holds the hot types, so each op can use the side table.
    assert set(sq.OPS_BY_TYPE) == set(sf.HOT_TYPES)
    assert set().union(*sq.OPS_BY_TYPE.values()) == set(sq.OPS)


def test_the_caps() -> None:
    assert (sq.MAX_FILTERS, sq.MAX_IN_VALUES, sq.MAX_OFFSET, sq.TOTAL_CAP) == (8, 100, 10_000, 10_001)
    assert (sq.SMALL_OBJECT, sq.TIMEOUT_MS, sq.DEFAULT_LIMIT) == (5_000, 5_000, 25)


@pytest.mark.parametrize(("field", "op"), [
    ("name", "lt"), ("name", "gte"), ("mail", "gt"), ("status", "contains"),
    ("hours", "contains"), ("day", "ne"), ("day", "in"), ("paid", "is_empty"),
    ("paid", "lt"), ("body", "eq"), ("tags", "eq"), ("owner", "eq"),
])
def test_an_op_outside_the_table_is_refused(field: str, op: str) -> None:
    """A range op on text is refused, and so is any op on a type outside the
    table or on a field with `many`."""
    value = ["x"] if op == "in" else "x"
    err = _refused("query_op_not_allowed", [{"field": field, "op": op, "value": value}])
    assert err.field == field and f"`{field}`" in err.message


# ── The shape ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("filter", "code"), [
    ("name = 1", "query_value_invalid"),
    ({"field": "name", "op": "eq"}, "query_value_invalid"),
    ([["name", "eq", "x"]], "query_value_invalid"),
    ([{"field": "name"}], "query_value_invalid"),
    ([{"field": "name", "op": "eq", "value": "x", "extra": 1}], "query_value_invalid"),
    ([{"field": "name", "op": "like", "value": "x"}], "query_op_not_allowed"),
    ([{"field": "name", "op": "in", "value": "x"}], "query_value_invalid"),
    ([{"field": "name", "op": "in", "value": []}], "query_value_invalid"),
    ([{"field": "name", "op": "in", "value": ["x"] * 101}], "query_too_big"),
    ([{"field": "name", "op": "eq", "value": "x"}] * 9, "query_too_big"),
])
def test_a_bad_shape_is_refused(filter: Any, code: str) -> None:
    _refused(code, filter)
    with pytest.raises(QueryError):
        sq.check_shape(filter)


def test_the_caps_hold_exactly() -> None:
    assert len(_parse([{"field": "hours", "op": "gte", "value": 1}] * 8).conditions) == 8
    assert len(_one("name", "in", ["x"] * 100).value) == 100


@pytest.mark.parametrize("key", [
    "nope", "x'; DROP TABLE studio_records; --", "%", "\\", 'a"b', "Name", 7, None, ["name"],
])
def test_an_unknown_or_hostile_field_is_query_field_unknown(key: Any) -> None:
    err = _refused("query_field_unknown", [{"field": key, "op": "eq", "value": "x"}])
    if isinstance(key, str) and key != "nope":
        assert key not in err.message
    assert err.field in ("nope", "?")


# ── The values ──────────────────────────────────────────────────────────────


def test_a_value_runs_through_the_one_check_of_a_value() -> None:
    assert _one("mail", "eq", "Ana@X.Test").value == "ana@x.test"
    assert _one("status", "in", ["open", "done"]).value == ("open", "done")
    assert _one("hours", "lt", 2.5).value == Decimal("2.5")
    assert _one("hours", "eq", 7).value == Decimal(7)
    assert _one("fee", "gte", 125000).value == Decimal(125000)
    assert _one("paid", "ne", False).value is False
    for field, op, value in (("status", "eq", "Open"), ("hours", "eq", "7"),
                             ("hours", "eq", 1e20), ("fee", "eq", 1.5),
                             ("fee", "eq", {"minor": 1, "currency": "INR"}), ("fee", "eq", True),
                             ("paid", "eq", "true"), ("mail", "eq", "not an address"),
                             ("name", "eq", "x" * 2001), ("name", "eq", "a\x00b"),
                             ("day", "eq", "2026-02-30"), ("day", "eq", "2026-01-01T00:00:00Z"),
                             ("at", "eq", "yesterday"), ("name", "contains", ""),
                             ("name", "is_empty", "yes")):
        err = _refused("query_value_invalid", [{"field": field, "op": op, "value": value}])
        assert err.field == field and f"`{field}`" in err.message
        if value in ("not an address", "x" * 2001, "2026-02-30", "yesterday"):
            assert value not in err.message


def test_a_day_is_the_whole_day_in_utc() -> None:
    start = datetime(2026, 1, 5, tzinfo=UTC)
    end = datetime(2026, 1, 6, tzinfo=UTC)
    for field in ("day", "at"):
        eq = _one(field, "eq", "2026-01-05")
        assert (eq.op, eq.value, eq.until) == ("eq", start, end)
        assert [(c.op, c.value) for c in (_one(field, op, "2026-01-05")
                                         for op in ("lt", "lte", "gt", "gte"))] == [
            ("lt", start), ("lt", end), ("gte", end), ("gte", start)]


def test_a_datetime_is_an_instant_in_utc() -> None:
    cond = _one("at", "lte", "2026-01-05T05:30:00+05:30")
    assert (cond.op, cond.value, cond.until) == ("lte", datetime(2026, 1, 5, tzinfo=UTC), None)


def test_contains_escapes_the_like_signs() -> None:
    assert _one("name", "contains", "50%_a\\b").value == "%50\\%\\_a\\\\b%"


def test_is_empty_takes_true_false_or_nothing() -> None:
    assert _one("name", "is_empty").value is True
    assert _parse([{"field": "name", "op": "is_empty", "value": None}]).conditions[0].value is True
    assert _one("name", "is_empty", False).value is False


# ── The sort and the page ───────────────────────────────────────────────────


def test_the_sort_rules() -> None:
    assert _parse(sort={"field": "hours"}).sort == sq.Sort(BY_KEY["hours"], desc=False)
    assert _parse(sort={"field": "at", "dir": "desc"}).sort == sq.Sort(BY_KEY["at"], desc=True)
    err = _refused("query_not_indexed", sort={"field": "note"})
    assert "`note`" in err.message and "Turn it hot" in err.message
    _refused("query_op_not_allowed", sort={"field": "paid"})
    _refused("query_op_not_allowed", sort={"field": "body"})
    _refused("query_field_unknown", sort={"field": "created_at"})
    _refused("query_value_invalid", sort={"field": "hours", "dir": "up"})
    _refused("query_value_invalid", sort="hours")


def test_page_bounds() -> None:
    assert sq.page_bounds(None, None) == (0, 25)
    assert sq.page_bounds(10_000, 500) == (10_000, 100)
    with pytest.raises(QueryError) as past:
        sq.page_bounds(10_001, 10)
    assert past.value.code == "query_too_big"


def test_page_line_names_no_offset_past_the_reach() -> None:
    assert page_line(None, 9_950, 100, more=True, noun="records", reach=10_000) == (
        "Showing 9951-10050. More records exist. A page starts at offset=10000 or before. "
        "Narrow the filter to see the rest.")
    assert page_line(20_000, 9_900, 100, noun="records", reach=10_000) == (
        "Showing 9901-10000 of 20000. Call again with offset=10000 for the rest.")
    # With no reach, the line is as before.
    assert page_line(None, 9_950, 100, more=True, noun="records") == (
        "Showing 9951-10050. More records exist. Call again with offset=10050 for the rest.")


# ── The errors ──────────────────────────────────────────────────────────────


def test_the_six_error_codes() -> None:
    assert sq.QUERY_ERROR_CODES == (
        "query_field_unknown", "query_op_not_allowed", "query_value_invalid",
        "query_not_indexed", "query_too_big", "query_timeout")
    with pytest.raises(ValueError, match="unknown query error code"):
        QueryError("query_other", "x")


def test_the_list_input_schema_holds_the_caps() -> None:
    schema = sq.list_input_schema()
    props = schema["properties"]
    assert props["filter"]["maxItems"] == sq.MAX_FILTERS
    assert props["filter"]["items"]["properties"]["op"]["enum"] == list(sq.OPS)
    assert props["limit"]["maximum"] == 100 and props["offset"]["maximum"] == sq.MAX_OFFSET
    schema["properties"].clear()
    assert sq.list_input_schema()["properties"], "each call gives a new copy"


# ── The SQL ─────────────────────────────────────────────────────────────────

_ORG = "organization_id = CAST(NULLIF(current_setting('app.tenant_id', true), '') AS uuid)"


def _sql(filter: Any = None, sort: Any = None) -> tuple[str, dict[str, Any]]:
    return store.build_query(_parse(filter, sort), object_id="o", package_id="p", limit=25, offset=0)


_MIX = [
    {"field": "name", "op": "eq", "value": "Alpha"},
    {"field": "hours", "op": "gte", "value": 2},
    {"field": "day", "op": "eq", "value": "2026-01-05"},
    {"field": "paid", "op": "ne", "value": True},
    {"field": "note", "op": "contains", "value": "kit"},
    {"field": "mail", "op": "is_empty"},
]


_HOT_MIX = [c for c in _MIX if c["field"] != "note"]

#: Each alias of the two record tables in a statement.
_ALIAS = re.compile(r"(?:FROM|JOIN) studio_record(?:s|_hot) (\w+)")


@pytest.mark.parametrize(("filter", "sort", "parts"), [
    (None, None, 2), (_HOT_MIX, None, 2), (None, {"field": "hours"}, 4),
    (_HOT_MIX, {"field": "hours", "dir": "desc"}, 4), (_MIX, None, 1),
    (_MIX, {"field": "hours", "dir": "desc"}, 1),
])
def test_each_statement_holds_the_org_the_object_and_the_package(filter: Any, sort: Any,
                                                                 parts: int) -> None:
    """Each alias of `studio_records` and `studio_record_hot` names the bound
    org and the object, and each part that reads candidates checks the package."""
    page, _ = _sql(filter, sort)
    count, _ = store.build_count(_parse(filter).hot_only(), object_id="o", package_id="p", cap=9)
    for sql in (page, count):
        aliases = set(_ALIAS.findall(sql))
        assert aliases
        for alias in aliases:
            assert f"{alias}.{_ORG}" in sql, alias
            assert f"{alias}.object_id = CAST(:oid AS uuid)" in sql, alias
    assert page.count("o.package_id = CAST(:pid AS uuid)") == parts
    assert count.count("o.package_id = CAST(:pid AS uuid)") == 1


def test_one_statement_reads_the_page_and_its_total() -> None:
    for filter, sort in ((None, None), (_HOT_MIX, {"field": "hours"}), (_MIX, {"field": "at"})):
        sql, _ = _sql(filter, sort)
        assert sql.startswith("WITH ") and sql.count("SELECT t.n, ") == 1
        assert "t AS (SELECT count(*) AS n FROM " in sql


def test_a_hot_sort_reads_the_head_then_the_tail_in_one_statement() -> None:
    """The head reads the hot rows of the sort field in index order, then the
    id. `hc` counts the head, so the tail reads only what the page needs."""
    sql, params = _sql(sort={"field": "hours", "dir": "desc"})
    assert "FROM studio_record_hot s WHERE s." in sql
    assert "ORDER BY s.v_num DESC, s.record_id DESC LIMIT :lim OFFSET :off" in sql
    assert "WITH hc AS MATERIALIZED (SELECT count(*) AS n FROM (SELECT 1 FROM studio_record_hot s" in sql
    assert "ORDER BY c.id LIMIT (SELECT greatest(0, CAST(:lim AS bigint) - least(" in sql
    assert sql.endswith(
        "ORDER BY p.part, p.k DESC, (CASE WHEN p.part = 0 THEN p.id END) DESC, p.id")
    assert params["sk"] == "hours"
    sql, _ = _sql()
    assert "ORDER BY c.id LIMIT :lim OFFSET :off" in sql and "hc" not in sql


def test_a_cold_filter_reads_data_only_after_the_hot_filters() -> None:
    """The candidates come from the hot rows, then each record by its key, and
    only then the test of `data`. The CTE holds them once."""
    sql, _ = _sql([{"field": "hours", "op": "gte", "value": 2},
                   {"field": "note", "op": "contains", "value": "kit"}], {"field": "at"})
    m = sql[:sql.index("), t AS (")]
    assert m.startswith("WITH m AS MATERIALIZED (SELECT c.id AS id FROM (SELECT d.record_id AS id "
                        "FROM studio_record_hot d WHERE ")
    lookup = m.index("CROSS JOIN LATERAL (SELECT r.id")
    assert m.index("OFFSET 0) r WHERE") > lookup
    assert m.index("(r.data ->> CAST(:fk1 AS text)) ILIKE") > m.index("OFFSET 0) r WHERE")
    assert "LEFT JOIN LATERAL (SELECT s.v_ts AS k FROM studio_record_hot s" in sql


def test_the_candidates_come_from_an_index_condition_first() -> None:
    """A time or a text match drives the candidates. A number range has no
    index condition for the app role, so it comes later."""
    sql, _ = _sql([{"field": "hours", "op": "gte", "value": 2},
                   {"field": "day", "op": "eq", "value": "2026-01-05"}])
    assert "AND d.field_key = CAST(:fk1 AS text) AND d.v_ts >= CAST(:v1 AS timestamptz)" in sql
    assert "EXISTS (SELECT 1 FROM studio_record_hot h0 " in sql
    sql, _ = _sql([{"field": "note", "op": "eq", "value": "kit"}, {"field": "mail", "op": "is_empty"}])
    assert "(SELECT r0.id FROM studio_records r0 WHERE r0." in sql


def test_text_eq_reads_data_only_from_256_characters() -> None:
    short, params = _sql([{"field": "name", "op": "eq", "value": "x" * 255}])
    assert "r.data ->>" not in short and "p0" not in params
    long, params = _sql([{"field": "name", "op": "eq", "value": "x" * 256}])
    assert "h0.v_text = CAST(:p0 AS text) AND r.data ->> CAST(:fk0 AS text) = CAST(:v0 AS text)" in long
    assert params["p0"] == "x" * 256
    contains, _ = _sql([{"field": "name", "op": "contains", "value": "x"}])
    assert "char_length(h0.v_text) = 256 AND r.data ->> CAST(:fk0 AS text) ILIKE" in contains


def test_a_cold_condition_reads_data_with_a_bound_key() -> None:
    sql, params = _sql([{"field": "note", "op": "ne", "value": "kit"}])
    assert "(r.data ->> CAST(:fk0 AS text)) <> CAST(:v0 AS text)" in sql
    assert params["fk0"] == "note" and "studio_record_hot h0" not in sql


_HOSTILE = ["x'; DROP TABLE studio_records; --", "%", "\\", "'", '"', "a' OR '1'='1", ":oid",
            "$$; SELECT 1; $$", "*/ --", "' || pg_sleep(9) || '"]


def _key_sql(key: str, kind: str, hot: bool) -> tuple[str, str, dict[str, Any]]:
    field = sf.FieldSpec(key=key, type=kind, hot=hot)
    conds = (Condition(field, "is_empty", True), Condition(field, "is_empty", False))
    query = Query(conds, sq.Sort(field) if hot and kind != "checkbox" else None)
    page, params = store.build_query(query, object_id="o", package_id="p", limit=1, offset=0)
    count, _ = store.build_count(conds, object_id="o", package_id="p", cap=1)
    return page, count, params


def test_a_hostile_key_or_value_never_reaches_the_sql() -> None:
    """The SQL text of a hostile value or key equals the SQL text of a plain
    one, so the text never depends on input. A value goes through the parse.
    A key goes in by hand, as if a key of another shape got past the stored
    CHECK. Each is a bind."""
    for bad in _HOSTILE:
        for field in ("name", "note"):
            for op in ("eq", "ne", "contains", "in"):
                plain, _ = _sql([{"field": field, "op": op, "value": ["zz"] if op == "in" else "zz"}])
                value = [bad] if op == "in" else bad
                sql, params = _sql([{"field": field, "op": op, "value": value}])
                assert sql == plain, (field, op, bad)
                if op != "contains":  # the pattern holds the value with its signs escaped
                    assert any(bad in str(v) for v in params.values())
        for kind in ("text", "number", "datetime", "checkbox"):
            for hot in (True, False):
                plain_page, plain_count, _ = _key_sql("k", kind, hot)
                page, count, params = _key_sql(bad, kind, hot)
                assert (page, count) == (plain_page, plain_count), (kind, hot, bad)
                assert params["fk0"] == bad
                if hot and kind != "checkbox":
                    assert params["sk"] == bad


#: The names that an f-string of the query SQL may take: the index, a module
#: constant, or a local that holds SQL built in these functions.
_SQL_NAMES = frozenset({
    "i", "j", "_BOUND_ORG", "_IN_PACKAGE", "_OF_ORG", "_OF_OBJECT", "_QUERY_COLUMNS",
    "_RECORD_ROW", "HOT_TEXT_CHARS", "kref", "kind", "expr", "cmp", "col", "hcol", "h",
    "rid", "frag", "row", "where", "dirn", "what", "source", "lookup", "head", "tail",
    "found", "total", "ids", "page", "counted", "count_head", "with_sql",
})


def _query_functions() -> list[ast.FunctionDef]:
    tree = ast.parse(_STORE.read_text(encoding="utf-8"))
    return [n for n in tree.body if isinstance(n, ast.FunctionDef)
            and (n.name.startswith("_q_") or n.name.startswith("build_"))]


def test_no_fstring_in_the_query_sql_takes_a_value() -> None:
    """A field key or a value in the SQL text is an injection. So an f-string
    of a query function takes a name of `_SQL_NAMES` only, and no attribute,
    call or subscript. A local of `_SQL_NAMES` is set from a constant map or
    from SQL built here, and a review checks a new one."""
    funcs = _query_functions()
    assert {f.name for f in funcs} >= {"_q_hot_row", "_q_data", "_q_compare", "_q_text_eq",
                                      "_q_text_in", "_q_contains", "_q_frag", "_q_hot",
                                      "_q_cold", "_q_lookup", "_q_where", "_q_source",
                                      "_q_filtered", "_q_head", "_q_sorted_ids",
                                      "_q_small_ids", "build_query", "build_count"}
    bad: list[str] = []
    for func in funcs:
        for node in ast.walk(func):
            if isinstance(node, ast.FormattedValue):
                value = node.value
                if isinstance(value, ast.Call) and getattr(value.func, "id", "").startswith("_q_"):
                    continue
                if not (isinstance(value, ast.Name) and value.id in _SQL_NAMES):
                    bad.append(f"{func.name}:{node.lineno}: {ast.unparse(value)}")
            elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod | ast.Add):
                for side in (node.left, node.right):
                    if isinstance(side, ast.Name) and side.id not in _SQL_NAMES:
                        bad.append(f"{func.name}:{node.lineno}: {ast.unparse(node)}")
            elif isinstance(node, ast.Attribute) and node.attr == "format":
                bad.append(f"{func.name}:{node.lineno}: .format")
    assert bad == [], bad


def test_the_grammar_holds_no_sql_and_no_gateway_import() -> None:
    """The store is the one module with SQL of the four tables. The grammar is
    pure, so B2b and the chat tool can import it."""
    src = _GRAMMAR.read_text(encoding="utf-8")
    assert "studio_record" not in src and "SELECT" not in src
    tree = ast.parse(src)
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    imported |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not any(str(m).startswith(("gateway", "sqlalchemy")) for m in imported), imported
