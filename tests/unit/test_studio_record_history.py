"""WS-STU C4a-1 — the change history of Studio records, with no database.

Spec: ``project-docs/specs/ai_studio.md`` §14 C4a-1. Code:
``gateway/studio/objects_store._apply_write``, ``_record_change``, ``_changes``,
``_via`` and ``purge_record_changes``, and ``routes/studio/invoke.py``
(``run_workflow_write``). Migration: the file that creates
``studio_record_changes`` (431 at build), found by its content (R1).
``test_studio_record_history_r8.py`` runs the SQL on a real Postgres.

**The mutations (R7).** Commit before each, and restore with git after.

* H1. ``objects_store._apply_write``: drop the ``if diff`` guard of an update.
  ``test_an_update_that_changes_nothing_writes_no_row`` goes red.
* H2. ``objects_store._changes``: keep each key. ``test_the_diff_holds_only_the_changed_keys``
  and ``test_an_update_writes_one_row_with_the_diff_and_the_actor`` go red.
* H3. ``objects_store._via``: accept any kind. ``test_a_bad_via_is_refused_before_any_statement``
  goes red.
* H4. ``objects_store._apply_write``: write the history row before the record
  statement. ``test_the_history_row_follows_the_write_statement`` goes red.
* H5. ``routes/studio/invoke.py``: pass ``via`` from ``_check`` or from a
  direct write. ``test_only_the_workflow_write_sets_via`` goes red.
"""

from __future__ import annotations

import ast
import asyncio
import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from acb_common.studio_access import Viewer

pytest.importorskip("sqlalchemy")

from gateway.studio import objects_store as store

_ROOT = Path(__file__).resolve().parents[2]
_STORE = _ROOT / "apps/services/gateway/gateway/studio/objects_store.py"
_INVOKE = _ROOT / "apps/services/gateway/gateway/routes/studio/invoke.py"
_MIGRATION = next(
    p for p in sorted((_ROOT / "infra" / "postgres").glob("*.sql"))
    if "CREATE TABLE IF NOT EXISTS studio_record_changes" in p.read_text(encoding="utf-8"))

OID = "00000000-0000-0000-0000-00000000000a"
PID = "00000000-0000-0000-0000-00000000000b"
RID = "00000000-0000-0000-0000-00000000000c"
ANA = "ana@x.test"

_FIELDS = [{"key": "title", "type": "text"}, {"key": "qty", "type": "number"},
           {"key": "note", "type": "text"}]


class _Result:
    def __init__(self, row: Any) -> None:
        self._row = row

    def one(self) -> Any:
        return self._row


class _DB:
    """A session that keeps each statement and answers the row of a record."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _Result:
        sql = str(stmt)
        self.calls.append((sql, dict(params or {})))
        rid = (params or {}).get("rid") or str(uuid.uuid4())
        data = (params or {}).get("data") or "{}"
        now = datetime.now(UTC)
        return _Result((rid, OID, json.loads(data), ANA, ANA, now, now))

    def history(self) -> list[dict[str, Any]]:
        return [p for s, p in self.calls if "studio_record_changes" in s]


def _batch(steps: list[store.Write], via: tuple[str, str] | None = None,
           data: dict[str, Any] | None = None) -> Any:
    batch = store._Batch(ANA, PID, steps, Viewer(ANA), via)
    batch.objects[OID] = store._Held("thing", store._check_fields(_FIELDS), ())
    if data is not None:
        batch.marks[(OID, RID)] = store._Mark(OID, 0, dict(data), dict(data))
    return batch


def _run(batch: Any, db: _DB) -> list[Any]:
    async def go() -> list[Any]:
        out = []
        for i, step in enumerate(batch.steps):
            batch.index = i
            out.append(await store._apply_write(db, batch, step))
        return out
    return asyncio.run(go())


def test_the_diff_holds_only_the_changed_keys() -> None:
    old = {"title": "a", "qty": 1, "note": "same"}
    new = {"title": "b", "note": "same", "extra": True}
    assert store._changes(old, new) == {
        "extra": {"old": None, "new": True},
        "qty": {"old": 1, "new": None},
        "title": {"old": "a", "new": "b"}}
    assert store._changes(old, dict(old)) == {}


def test_an_update_writes_one_row_with_the_diff_and_the_actor() -> None:
    db = _DB()
    batch = _batch([store.Write("update", OID, RID, {"qty": 5})],
                   data={"title": "a", "qty": 1})
    _run(batch, db)
    [row] = db.history()
    assert row["op"] == "update"
    assert row["actor"] == ANA
    assert row["oid"] == OID and row["rid"] == RID
    assert json.loads(row["changes"]) == {"qty": {"old": 1, "new": 5}}
    assert row["via_kind"] is None and row["via_id"] is None


def test_an_update_that_changes_nothing_writes_no_row() -> None:
    """The record statement still runs, so ``updated_at`` moves as before."""
    db = _DB()
    batch = _batch([store.Write("update", OID, RID, {"qty": 1})],
                   data={"title": "a", "qty": 1})
    _run(batch, db)
    assert db.history() == []
    assert any("UPDATE studio_records" in s for s, _ in db.calls)


def test_a_create_holds_each_value_with_old_null() -> None:
    db = _DB()
    batch = _batch([store.Write("create", OID, data={"title": "a", "qty": 2})])
    [made] = _run(batch, db)
    [row] = db.history()
    assert row["op"] == "create" and row["rid"] == made.id
    assert json.loads(row["changes"]) == {"qty": {"old": None, "new": 2},
                                         "title": {"old": None, "new": "a"}}


def test_a_delete_keeps_each_old_value_with_new_null() -> None:
    db = _DB()
    batch = _batch([store.Write("delete", OID, RID)], data={"title": "a", "qty": 1})
    _run(batch, db)
    [row] = db.history()
    assert row["op"] == "delete"
    assert json.loads(row["changes"]) == {"qty": {"old": 1, "new": None},
                                         "title": {"old": "a", "new": None}}


def test_two_writes_to_one_record_give_two_rows_in_order() -> None:
    db = _DB()
    batch = _batch([store.Write("update", OID, RID, {"qty": 2}),
                    store.Write("update", OID, RID, {"qty": 3})],
                   data={"title": "a", "qty": 1})
    _run(batch, db)
    assert [json.loads(r["changes"]) for r in db.history()] == [
        {"qty": {"old": 1, "new": 2}}, {"qty": {"old": 2, "new": 3}}]


def test_the_history_row_follows_the_write_statement() -> None:
    db = _DB()
    batch = _batch([store.Write("update", OID, RID, {"qty": 2}),
                    store.Write("delete", OID, RID)], data={"title": "a", "qty": 1})
    _run(batch, db)
    kinds = ["history" if "studio_record_changes" in s else
             "update" if "UPDATE studio_records" in s else
             "delete" if "DELETE FROM studio_records" in s else s for s, _ in db.calls]
    assert kinds == ["update", "history", "delete", "history"]


def test_the_via_of_the_batch_names_each_row() -> None:
    db = _DB()
    batch = _batch([store.Write("update", OID, RID, {"qty": 2})],
                   via=("workflow_run", "run-1"), data={"qty": 1})
    _run(batch, db)
    [row] = db.history()
    assert (row["via_kind"], row["via_id"]) == ("workflow_run", "run-1")


@pytest.mark.parametrize("bad", [
    ("person", "x"), ("workflow_run", ""), ("workflow_run", "  "), ("agent", 7),
    ("agent", "x" * 201), ["workflow_run", "x"], ("workflow_run",), "workflow_run"])
def test_a_bad_via_is_refused_before_any_statement(bad: Any) -> None:
    class _NoSQL:
        async def execute(self, *a: Any, **k: Any) -> Any:
            raise AssertionError("a statement ran")

        def begin_nested(self) -> Any:
            raise AssertionError("a SAVEPOINT opened")

    with pytest.raises(store.ObjectStoreError, match="via"):
        asyncio.run(store.apply_writes(_NoSQL(), ANA, package_id=PID,
                                       writes=[store.Write("create", OID, data={})],
                                       viewer=Viewer(ANA), via=bad))


def test_via_is_absent_by_default_and_takes_the_two_kinds() -> None:
    assert store.VIA_KINDS == ("workflow_run", "agent")
    assert store._via(None) is None
    assert store._via(("agent", " a1 ")) == ("agent", "a1")
    assert store._via(("workflow_run", "x" * 200)) == ("workflow_run", "x" * 200)
    for fn in (store.create_record, store.update_record, store.delete_record):
        assert "via" not in fn.__code__.co_varnames, fn.__name__


def _functions(path: Path) -> dict[str, ast.AST]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {n.name: n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)}


def test_one_function_writes_the_history() -> None:
    """``_CHANGE_SQL`` runs in ``_record_change``, and ``_apply_write`` is its
    one caller. So the row is in the SAVEPOINT of ``_apply_batch``, and a
    refused batch writes none. A derived write of a roll-up parent writes no row."""
    funcs = _functions(_STORE)
    names = {n: {x.id for x in ast.walk(f) if isinstance(x, ast.Name)}
             for n, f in funcs.items()}
    assert sorted(n for n, used in names.items() if "_CHANGE_SQL" in used) == ["_record_change"]
    callers = sorted(n for n, f in funcs.items() if any(
        isinstance(x, ast.Call) and getattr(x.func, "id", "") == "_record_change"
        for x in ast.walk(f)))
    assert callers == ["_apply_write"]


def test_only_the_workflow_write_sets_via() -> None:
    """The kind and the id come from the arguments of ``run_workflow_write``,
    which its caller binds from the run row. No other call names ``via``."""
    funcs = _functions(_INVOKE)

    def _sets_via(f: ast.AST, callee: str) -> bool:
        return any(isinstance(x, ast.Call) and callee in ast.unparse(x.func)
                   and any(k.arg == "via" for k in x.keywords) for x in ast.walk(f))

    assert sorted(n for n, f in funcs.items() if _sets_via(f, "_do_object_write")) == [
        "run_workflow_write"]
    do_write = ast.unparse(funcs["_do_object_write"])
    assert "{} if via is None else {'via': via}" in do_write
    assert "viewer=viewer, **how)" in do_write
    src = ast.unparse(funcs["run_workflow_write"])
    assert "via=(WORKFLOW_RUN_VIA, str(run_id))" in src
    # Inside _do_object_write, `via` is only its keyword parameter: no line
    # assigns the name, and the one string "via" is the key of the dict that
    # passes it on. So no `body["via"]` and no `payload.get("via")`.
    fn = funcs["_do_object_write"]
    assert isinstance(fn, ast.AsyncFunctionDef)
    assert [a.arg for a in fn.args.kwonlyargs] == ["via"]
    stores = [n for n in ast.walk(fn) if isinstance(n, ast.Name) and n.id == "via"
              and isinstance(n.ctx, ast.Store)]
    assert stores == []
    keys = [n for n in ast.walk(fn) if isinstance(n, ast.Constant) and n.value == "via"]
    dicts = [n for n in ast.walk(fn) if isinstance(n, ast.Dict)
             and any(isinstance(k, ast.Constant) and k.value == "via" for k in n.keys)]
    assert len(keys) == 1 and len(dicts) == 1, ast.unparse(fn)
    [value] = dicts[0].values
    assert isinstance(value, ast.Name) and value.id == "via"


def test_the_audit_row_stays_value_free() -> None:
    """``_audit_detail`` names no data of the record and no ``changes``."""
    fn = _functions(_INVOKE)["_audit_detail"]
    assert isinstance(fn, ast.AsyncFunctionDef | ast.FunctionDef)
    code = ast.Module(body=fn.body[1:], type_ignores=[])  # no docstring
    src = ast.unparse(code)
    for word in ("data", "changes", "record", "body"):
        assert re.search(rf"\b{re.escape(word)}\b", src) is None, word


def test_the_migration_is_tenant_scoped_and_expand_only() -> None:
    sql = _MIGRATION.read_text(encoding="utf-8")
    body = "\n".join(re.sub(r"--.*$", "", line) for line in sql.splitlines())
    assert re.search(r"REFERENCES organization \(id\) ON DELETE CASCADE\s+"
                     r"DEFAULT current_setting\('app.tenant_id', true\)::uuid", body)
    assert "ALTER TABLE studio_record_changes ENABLE ROW LEVEL SECURITY" in body
    assert "ALTER TABLE studio_record_changes FORCE  ROW LEVEL SECURITY" in body
    assert "USING      (organization_id = current_setting('app.tenant_id', true)::uuid)" in body
    assert "WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid)" in body
    assert "via_kind IN ('workflow_run', 'agent')" in body
    assert "op IN ('create', 'update', 'delete')" in body
    # No foreign key to the records, so the history stays after a delete.
    assert "REFERENCES studio_records" not in body
    assert "REFERENCES studio_objects (organization_id, id) ON DELETE CASCADE" in body
    for word in ("DROP TABLE", "DROP COLUMN", "RENAME", "ALTER COLUMN"):
        assert word not in body.upper(), word
    assert "CREATE TABLE IF NOT EXISTS" in body and "CREATE INDEX IF NOT EXISTS" in body


def test_the_purge_of_history_names_the_bound_org_twice() -> None:
    """The DELETE and its subselect each filter on the bound org, beside RLS,
    as ``_PURGE_SQL`` does."""
    sql = store._PURGE_CHANGES_SQL
    outer, inner = sql.split("ARRAY(SELECT", 1)
    want = f"organization_id = {store._BOUND_ORG}"
    assert want in outer and want in inner
    assert "object_id = CAST(:oid AS uuid)" in outer and "object_id = CAST(:oid AS uuid)" in inner
    assert outer.startswith("DELETE FROM studio_record_changes ")
    assert "FROM studio_record_changes " in inner and "LIMIT :n" in inner
