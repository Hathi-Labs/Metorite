"""WS-STU E8a: the batch ledger, the hermetic half (task 3095).

Spec: ``project-docs/specs/ai_studio.md`` §14 E8a. Code:
``gateway/studio/batch_store.py`` and migration 436 (found by content, never
by number, R1). ``test_studio_batch_r8.py`` holds the real-database half.

These tests need no database. They pin the digests, the refusals at open, the
chunk rule, and the source fences that keep the invariant: the store never
commits, each statement names the org, and no delete is in a batch.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")

from acb_common.studio_rules import MAX_WRITES_PER_BATCH
from gateway.studio import batch_store as bs
from gateway.studio.objects_store import Write

_ROOT = Path(__file__).resolve().parents[2]
_SRC = (_ROOT / "apps/services/gateway/gateway/studio/batch_store.py").read_text(encoding="utf-8")
_MIGRATION = next(
    p for p in (_ROOT / "infra" / "postgres").glob("*.sql")
    if "CREATE TABLE IF NOT EXISTS studio_batches" in p.read_text(encoding="utf-8"))
_OBJ = "11111111-1111-1111-1111-111111111111"
_LINE = "22222222-2222-2222-2222-222222222222"
_REC = "33333333-3333-3333-3333-333333333333"


def _w(**over: object) -> Write:
    base: dict = {"op": "create", "object_id": _OBJ, "data": {"a": 1, "b": "x"}}
    base.update(over)
    return Write(**base)


def _item(key: str = "k1", n: int = 0) -> bs.BatchItem:
    return bs.BatchItem(key, (_w(), *[Write("create", _LINE, data={"q": i}, parent=0)
                                      for i in range(n)]))


# -- the digests -------------------------------------------------------------


def test_the_item_digest_is_stable_and_ignores_the_order_of_data_keys() -> None:
    one = bs.item_digest([_w(data={"a": 1, "b": "x"})])
    assert one == bs.item_digest([_w(data={"b": "x", "a": 1})])
    assert re.fullmatch(r"[0-9a-f]{64}", one)


@pytest.mark.parametrize("change", [
    {"op": "update", "record_id": _REC},
    {"object_id": _LINE},
    {"data": {"a": 2, "b": "x"}},
    {"record_id": _REC},
    {"parent": 0},
])
def test_the_item_digest_binds_each_part_of_a_write(change: dict) -> None:
    assert bs.item_digest([_w()]) != bs.item_digest([_w(**change)])


def test_the_item_digest_binds_the_number_and_order_of_writes() -> None:
    a, b = _w(data={"n": 1}), _w(data={"n": 2})
    assert bs.item_digest([a, b]) != bs.item_digest([b, a])
    assert bs.item_digest([a]) != bs.item_digest([a, b])


def test_the_list_digest_binds_the_package_the_action_the_keys_and_the_order() -> None:
    one, two = _item("a"), _item("b")
    base = bs.list_digest(_OBJ, "load", [one, two])
    assert base == bs.list_digest(_OBJ, "load", [one, two])
    assert base != bs.list_digest(_OBJ, "load", [two, one])
    assert base != bs.list_digest(_LINE, "load", [one, two])
    assert base != bs.list_digest(_OBJ, "other", [one, two])
    assert base != bs.list_digest(_OBJ, "load", [bs.BatchItem("c", one.writes), two])


# -- the refusals at open ----------------------------------------------------


def test_a_duplicate_key_is_refused() -> None:
    with pytest.raises(bs.BatchStoreError, match="same key"):
        bs._clean_items([_item("a"), _item("a")])


@pytest.mark.parametrize("op", ["delete", "upsert", ""])
def test_an_op_other_than_create_or_update_is_refused(op: str) -> None:
    with pytest.raises(bs.BatchStoreError, match="no delete"):
        bs._clean_items([bs.BatchItem("a", (Write(op, _OBJ, record_id=_LINE),))])


def test_a_delete_among_the_lines_of_an_item_is_refused() -> None:
    bad = bs.BatchItem("a", (_w(), Write("delete", _LINE, record_id=_OBJ)))
    with pytest.raises(bs.BatchStoreError, match="no delete"):
        bs._clean_items([bad])


def test_the_cap_of_items_is_a_literal_of_1000() -> None:
    assert bs.MAX_BATCH_ITEMS == 1000
    assert len(bs._clean_items([_item(f"k{i}") for i in range(1000)])) == 1000
    with pytest.raises(bs.BatchStoreError, match="at most 1000"):
        bs._clean_items([_item(f"k{i}") for i in range(1001)])


@pytest.mark.parametrize("items", [[], "x", {"a": 1}, [object()]])
def test_a_list_that_is_no_list_of_items_is_refused(items: object) -> None:
    with pytest.raises(bs.BatchStoreError):
        bs._clean_items(items)


@pytest.mark.parametrize("key", ["", " a", "a ", "k" * 121])
def test_a_bad_key_is_refused(key: str) -> None:
    with pytest.raises(bs.BatchStoreError, match="key"):
        bs._clean_items([bs.BatchItem(key, (_w(),))])


def test_an_item_of_more_than_the_chunk_cap_is_refused() -> None:
    assert bs.CHUNK_WRITES == MAX_WRITES_PER_BATCH == 50
    assert len(bs._clean_items([_item("a", n=49)])) == 1
    with pytest.raises(bs.BatchStoreError, match="1 to 50 writes"):
        bs._clean_items([_item("a", n=50)])
    with pytest.raises(bs.BatchStoreError, match="1 to 50 writes"):
        bs._clean_items([bs.BatchItem("a", ())])


@pytest.mark.parametrize("parent", [-1, 1, 2, True, "0", 1.0])
def test_a_bad_parent_of_a_line_is_refused(parent: object) -> None:
    bad = bs.BatchItem("a", (_w(), Write("create", _LINE, data={"q": 1}, parent=parent)))
    with pytest.raises(bs.BatchStoreError, match="parent"):
        bs._clean_items([bad])


def test_the_record_write_has_no_parent_and_a_line_names_an_earlier_write() -> None:
    with pytest.raises(bs.BatchStoreError, match="no parent"):
        bs._clean_items([bs.BatchItem("a", (_w(parent=0),))])
    ok = bs.BatchItem("a", (_w(), Write("create", _LINE, parent=0),
                            Write("create", _LINE, parent=1)))
    assert len(bs._clean_items([ok])) == 1


def test_an_item_error_is_a_value_error_and_a_fault_is_not() -> None:
    assert bs._is_item_refusal(ValueError("x")) and not bs._is_item_refusal(RuntimeError("x"))


# -- the chunk rule ----------------------------------------------------------


def _rows(*counts: int) -> list[tuple[str, int, str]]:
    return [(f"k{i}", n, "d") for i, n in enumerate(counts)]


def test_a_chunk_takes_fifty_items_of_one_write() -> None:
    assert len(bs._take(_rows(*([1] * 80)))) == 50


def test_an_item_with_lines_counts_one_plus_n_toward_the_fifty() -> None:
    # 10 items of 1 + 4 lines are 50 writes. The 11th does not fit.
    assert len(bs._take(_rows(*([5] * 12)))) == 10
    assert [r[0] for r in bs._take(_rows(49, 1, 1))] == ["k0", "k1"]
    assert [r[0] for r in bs._take(_rows(49, 2, 1))] == ["k0"]


def test_an_item_is_never_split_and_the_order_is_kept() -> None:
    assert [r[0] for r in bs._take(_rows(30, 30, 1))] == ["k0"]


def test_a_line_parent_moves_by_the_place_of_its_record_in_the_chunk() -> None:
    item = _item("a", n=2)
    moved = bs._shift(item.writes, 7)
    assert [w.parent for w in moved] == [None, 7, 7]
    assert bs._shift(item.writes, 0) == list(item.writes)


def test_a_failure_code_is_short_and_never_a_message() -> None:
    from acb_common.studio_rules import ConstraintViolation
    code = bs._code(ConstraintViolation("qty_ok", "ledger", "Qty 500 is above 100 for jane"))
    assert code == "rule_refused"
    assert "secret" not in bs._code(ValueError("secret pay 123456"))


# -- the fences of the source (R7) -------------------------------------------


def test_the_store_never_commits_rolls_back_or_opens_a_connection() -> None:
    """The invariant: the status and the write are ONE transaction of the
    caller. A commit inside would split them."""
    for word in ("commit(", "rollback(", "create_engine", "get_db(", "get_engine", "redis",
                 "begin()", "AsyncSession("):
        assert word not in _SRC, word


def test_each_select_and_update_names_the_org() -> None:
    statements = re.findall(r"^_[A-Z_]+_SQL = \(\n(.*?)\n\)\n", _SRC, re.S | re.M)
    assert len(statements) >= 9
    for sql in statements:
        if sql.lstrip().startswith('"INSERT'):
            continue
        assert "_BOUND_ORG" in sql, sql[:60]


def test_the_batch_row_is_locked_for_update_and_no_statement_deletes() -> None:
    assert "FOR UPDATE" in _SRC
    assert not re.search(r'"\s*DELETE FROM', _SRC)


def test_the_store_keeps_no_write_data_at_rest() -> None:
    """Only the digest is stored. The insert names no data column."""
    insert = re.search(r"_INSERT_ITEM_SQL = \(\n(.*?)\n\)", _SRC, re.S).group(1)
    assert "item_digest" in insert and "data" not in insert and "payload" not in insert
    sql = _MIGRATION.read_text(encoding="utf-8")
    items = sql.split("CREATE TABLE IF NOT EXISTS studio_batch_items")[1].split(");")[0]
    assert "JSONB" not in items


def test_the_migration_is_expand_only_tenant_scoped_and_idempotent() -> None:
    sql = _MIGRATION.read_text(encoding="utf-8")
    assert "DROP TABLE" not in sql and "RENAME" not in sql
    assert len(re.findall(r"^CREATE TABLE IF NOT EXISTS", sql, re.M)) == 2
    assert "FORCE  ROW LEVEL SECURITY" in sql and "WITH CHECK" in sql
    # The tenancy ratchet reads REFERENCES before DEFAULT with no comma between.
    found = re.findall(r"organization_id\s+UUID NOT NULL\s+REFERENCES organization \(id\) "
                       r"ON DELETE CASCADE\s+DEFAULT current_setting", sql)
    assert len(found) == 2
    assert "CHECK (op IN ('create', 'update'))" in sql
    assert "status IN ('pending', 'done', 'failed')" in sql
    assert "PRIMARY KEY (organization_id, batch_id, item_key)" in sql


def test_the_r8_suite_is_named_in_both_ci_guards() -> None:
    for name in ("pr-check.yml", "deploy.yml"):
        flow = (_ROOT / ".github/workflows" / name).read_text(encoding="utf-8")
        assert re.search(r"^[ \t]+tests/unit/test_studio_batch_r8\.py(?: \\)?\s*$", flow, re.M), name
