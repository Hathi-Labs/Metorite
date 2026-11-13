"""WS-STU C2c — the float8 copy of ``v_num``, with no database.

Spec: ``project-docs/specs/ai_studio.md`` §14 row C2c. Rule:
``docs/rls_and_indexes.md``. ``test_studio_query_r8.py`` runs the same SQL on
a real Postgres (the plan and the records).

**Fences (R7).**

* The migration file is found by CONTENT, never by number (R1). It must add
  the column, the trigger and the CONCURRENTLY index, hold no BEGIN, and drop
  an invalid index first. Mutation: delete the DO block. The test goes red.
* With the flag off, the SQL of a number filter is the SQL of C2a.
  Mutation: make ``_f8_on`` return True. The test goes red.
* With the flag on, each number op adds a float8 test with the non-strict
  operator, beside the exact test. Mutation: write ``<`` for ``lt`` in
  ``_q_num_f8``. The test goes red.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from acb_common import studio_fields as sf
from acb_common import studio_query as sq
from acb_common.studio_flags import num_f8_allowed
from gateway.studio import objects_store as store

_ROOT = Path(__file__).resolve().parents[2]
_MIGRATIONS = _ROOT / "infra" / "postgres"
_MARK = "studio_record_hot_num_f8_fill"

_SPECS: list[dict[str, Any]] = [
    {"key": "hours", "type": "number", "hot": True},
    {"key": "fee", "type": "money", "hot": True},
    {"key": "name", "type": "text", "hot": True},
]


def _migration() -> str:
    found = [p for p in _MIGRATIONS.glob("*.sql") if _MARK in p.read_text(encoding="utf-8")]
    assert len(found) == 1, found
    return found[0].read_text(encoding="utf-8")


def _code(sql: str) -> str:
    """The SQL with the comment lines taken out."""
    return "\n".join(ln for ln in sql.splitlines() if not ln.lstrip().startswith("--"))


def test_the_migration_adds_the_column_the_trigger_and_the_concurrent_index() -> None:
    code = _code(_migration())
    assert re.search(r"ALTER TABLE studio_record_hot ADD COLUMN IF NOT EXISTS v_num_f8 "
                     r"double precision;", code)
    assert "NEW.v_num_f8 := CASE" in code and "ELSE NEW.v_num::float8" in code
    assert re.search(r"CREATE TRIGGER studio_record_hot_num_f8_fill\s+"
                     r"BEFORE INSERT OR UPDATE OF v_num\b", code)
    assert "DROP TRIGGER IF EXISTS studio_record_hot_num_f8_fill" in code
    assert re.search(r"CREATE INDEX CONCURRENTLY IF NOT EXISTS studio_record_hot_num_f8_idx\s+"
                     r"ON studio_record_hot \(organization_id, object_id, field_key, v_num_f8\);",
                     code)


def test_the_migration_holds_no_transaction_and_drops_an_invalid_index_first() -> None:
    """The runner has no single transaction, and CONCURRENTLY cannot run in
    one. A failed build leaves an INVALID index that IF NOT EXISTS skips."""
    code = _code(_migration())
    outside = re.sub(r"(?s)\$(\w*)\$.*?\$\1\$", "", code)  # a function body has its own BEGIN
    assert not re.search(r"(?im)^\s*(BEGIN|START TRANSACTION|COMMIT)\b", outside)
    drop = code.index("NOT i.indisvalid")
    assert "DROP INDEX %I" in code and "studio_record_hot_num_f8_todo_idx" in code
    assert drop < code.index("CREATE INDEX CONCURRENTLY")


def test_the_trigger_guards_the_float8_range_and_the_index_build_lifts_lock_timeout() -> None:
    code = _code(_migration())
    assert "WHEN NEW.v_num > 1e300 THEN 'Infinity'::float8" in code
    assert "WHEN NEW.v_num < -1e300 THEN '-Infinity'::float8" in code
    assert "WHEN abs(NEW.v_num) < 1e-300 THEN 0.0::float8" in code
    assert "lock_timeout = 0" not in code
    lift = code.index("SET lock_timeout = '10min';")  # finite, never 0
    assert lift < code.index("CREATE INDEX CONCURRENTLY") < code.index("RESET lock_timeout;")
    assert code.count("CREATE INDEX CONCURRENTLY") == 2
    assert "ON studio_record_hot (organization_id) WHERE v_num IS NOT NULL AND v_num_f8 IS NULL;" in code


def test_the_store_imports_no_routes_layer() -> None:
    src = (_ROOT / "apps/services/gateway/gateway/studio/objects_store.py").read_text(encoding="utf-8")
    assert "gateway.routes" not in src and "acb_common.studio_flags" in src


def test_the_migration_is_expand_only() -> None:
    code = _code(_migration()).upper()
    for word in ("DROP COLUMN", "DROP TABLE", "RENAME", "SET NOT NULL", "DEFAULT"):
        assert word not in code, word


def _compile(filter: Any, on: bool, monkeypatch: pytest.MonkeyPatch,
             sort: Any = None) -> tuple[str, dict[str, Any]]:
    monkeypatch.setattr(store, "_f8_on", lambda: on)
    query = sq.parse_query([sf.parse_field_spec(f) for f in _SPECS], filter, sort)
    return store.build_query(query, object_id="o", package_id="p", limit=25, offset=0)


_OPS: list[tuple[str, Any]] = [("eq", 5), ("ne", 5), ("lt", 5), ("lte", 5), ("gt", 5), ("gte", 5),
                               ("in", [1, 2]), ("is_empty", True)]


@pytest.mark.parametrize(("op", "value"), _OPS)
@pytest.mark.parametrize("field", ["hours", "fee"])
def test_with_the_flag_off_the_sql_never_names_the_float8_column(
        monkeypatch: pytest.MonkeyPatch, field: str, op: str, value: Any) -> None:
    if field == "fee" and op == "in":
        value = [100, 200]
    sql, _ = _compile([{"field": field, "op": op, "value": value}], False, monkeypatch)
    assert "v_num_f8" not in sql and "float8" not in sql


def test_with_the_flag_off_a_number_filter_is_the_sql_of_c2a(
        monkeypatch: pytest.MonkeyPatch) -> None:
    sql, _ = _compile([{"field": "hours", "op": "lte", "value": 5}], False, monkeypatch)
    assert "d.v_num <= CAST(:v0 AS numeric)" in sql
    assert " AND d.v_num <= CAST(:v0 AS numeric)) c" in sql


_F8_WANT = {
    "eq": "d.v_num_f8 = CAST(CAST(:v0 AS numeric) AS float8)",
    "lt": "d.v_num_f8 <= CAST(CAST(:v0 AS numeric) AS float8)",
    "lte": "d.v_num_f8 <= CAST(CAST(:v0 AS numeric) AS float8)",
    "gt": "d.v_num_f8 >= CAST(CAST(:v0 AS numeric) AS float8)",
    "gte": "d.v_num_f8 >= CAST(CAST(:v0 AS numeric) AS float8)",
}
_EXACT = {"eq": "=", "lt": "<", "lte": "<=", "gt": ">", "gte": ">="}


@pytest.mark.parametrize("op", sorted(_F8_WANT))
def test_with_the_flag_on_each_op_adds_the_non_strict_float8_test_beside_the_exact_one(
        monkeypatch: pytest.MonkeyPatch, op: str) -> None:
    sql, params = _compile([{"field": "hours", "op": op, "value": 5}], True, monkeypatch)
    exact = f"d.v_num {_EXACT[op]} CAST(:v0 AS numeric)"
    assert f"{_F8_WANT[op]} AND {exact}" in sql
    assert params["v0"] == 5
    # A second condition drives, so the number is tested on the hot row of the candidate.
    sql, _ = _compile([{"field": "name", "op": "eq", "value": "a"},
                       {"field": "hours", "op": op, "value": 5}], True, monkeypatch)
    want = _F8_WANT[op].replace("d.v_num_f8", "h1.v_num_f8").replace(":v0", ":v1")
    assert f"{want} AND h1.v_num {_EXACT[op]} CAST(:v1 AS numeric)" in sql


def test_with_the_flag_on_in_uses_an_any_of_float8_and_ne_and_is_empty_are_unchanged(
        monkeypatch: pytest.MonkeyPatch) -> None:
    sql, _ = _compile([{"field": "hours", "op": "in", "value": [1, 2]}], True, monkeypatch)
    assert ("d.v_num_f8 = ANY(CAST(CAST(:v0 AS numeric[]) AS float8[])) "
            "AND d.v_num = ANY(CAST(:v0 AS numeric[]))") in sql
    for op, value in (("ne", 5), ("is_empty", True)):
        on, _ = _compile([{"field": "hours", "op": op, "value": value}], True, monkeypatch)
        off, _ = _compile([{"field": "hours", "op": op, "value": value}], False, monkeypatch)
        assert on == off and "v_num_f8" not in on


def test_a_text_filter_and_a_sort_do_not_change_with_the_flag(
        monkeypatch: pytest.MonkeyPatch) -> None:
    for filter, sort in ([{"field": "name", "op": "eq", "value": "a"}], None), \
            (None, {"field": "hours", "dir": "desc"}):
        on, _ = _compile(filter, True, monkeypatch, sort)
        off, _ = _compile(filter, False, monkeypatch, sort)
        assert on == off


def test_the_float8_bound_has_no_column_in_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bound with a column is not a constant, so no index condition takes it."""
    sql, _ = _compile([{"field": "hours", "op": "lte", "value": 5}], True, monkeypatch)
    for bound in re.findall(r"CAST\(CAST\(:v0 AS numeric\) AS float8\)", sql):
        assert ".v_num" not in bound


def test_the_flag_reader_takes_the_org_list_and_defaults_off(monkeypatch: pytest.MonkeyPatch) -> None:
    from acb_common import get_settings

    monkeypatch.delenv("STUDIO_NUM_F8_ORGS", raising=False)
    get_settings.cache_clear()
    assert not num_f8_allowed("acme") and not num_f8_allowed(None)
    monkeypatch.setenv("STUDIO_NUM_F8_ORGS", "acme, Globex")
    get_settings.cache_clear()
    try:
        assert num_f8_allowed("ACME") and num_f8_allowed("globex") and not num_f8_allowed("zed")
        monkeypatch.setenv("STUDIO_NUM_F8_ORGS", "*")
        get_settings.cache_clear()
        assert num_f8_allowed("zed") and not num_f8_allowed("")
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()


def test_no_tenant_bound_means_the_flag_is_off() -> None:
    assert store._f8_on() is False


def test_a_cold_or_not_ready_cache_gives_the_sql_of_c2a(monkeypatch: pytest.MonkeyPatch) -> None:
    """The flag alone does not turn the float8 test on. The org must also be
    ready: no row with a `v_num` and a NULL copy."""
    import time

    monkeypatch.setattr(store, "num_f8_org", lambda: "acme")
    store._F8_READY.clear()
    assert store._f8_on() is False
    store._F8_READY["acme"] = (False, time.monotonic())
    assert store._f8_on() is False
    store._F8_READY["acme"] = (True, time.monotonic())
    assert store._f8_on() is True
    store._F8_READY["acme"] = (True, time.monotonic() - store._F8_TTL_READY - 1)
    assert store._f8_on() is False  # stale
    store._F8_READY.clear()
    monkeypatch.setattr(store, "num_f8_org", lambda: None)
    assert store._f8_on() is False


def test_the_backfill_sql_clamps_as_the_trigger_does() -> None:
    sql = store._NUM_F8_BATCH
    assert "WHEN h.v_num > 1e300 THEN 'Infinity'::float8" in sql
    assert "WHEN h.v_num < -1e300 THEN '-Infinity'::float8" in sql
    assert "WHEN abs(h.v_num) < 1e-300 THEN 0.0::float8" in sql


def test_the_guard_reads_the_org_it_counts_and_has_a_cap_inside_the_deadline() -> None:
    assert "current_setting('app.tenant_id', true)" in store._F8_READY_SQL
    assert 0 < store._F8_GUARD_CAP_S <= 1.0
    src = (_ROOT / "apps/services/gateway/gateway/studio/objects_store.py").read_text(encoding="utf-8")
    for fn in ("query_records", "aggregate_records"):
        body = src[src.index(f"async def {fn}("):]
        body = body[:body.index("\nasync def ", 10) if "\nasync def " in body[10:] else len(body)]
        assert body.index("deadline = _clock()") < body.index("await _warm_f8(db, deadline)"), fn
