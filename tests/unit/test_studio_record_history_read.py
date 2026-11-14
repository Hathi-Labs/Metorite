"""WS-STU C4a-2 — the read of the change history of a Studio record, with no database.

Spec: ``project-docs/specs/ai_studio.md`` §14 C4a-2. Code:
``gateway/studio/objects_store.list_record_changes`` (the one reader, through
``_read_record``, the gate of a get) and ``routes/studio/invoke.py``
(``object:<name>.history``, ``_run_object_history``). The decision of each
field type is ``studio_fields.HISTORY_DECISION``.
``test_studio_record_history_read_r8.py`` runs the SQL on a real Postgres.

Here the real reader runs on a fake session that answers its four
statements. The door runs on the fake session of ``test_studio_action_door.py``,
with the reader patched.

**The mutations (R7).** Commit before each, and restore with git after.

* HR1. ``list_record_changes``: drop ``and not view.owner``.
  ``test_only_the_owner_reads_the_history_of_a_deleted_record`` goes red.
* HR2. ``list_record_changes``: build ``shown`` with no ``hidden``.
  ``test_a_hidden_key_leaves_changes_and_the_store_keeps_it`` goes red.
* HR3. ``_CHANGES_PAGE_SQL``: drop the ``jsonb_exists_any`` filter. The fake
  session reads the same filter, so the R8 twin
  ``test_r8_a_walk_never_shows_a_hidden_only_update`` is its fence.
* HR6. ``list_record_changes``: drop the scope start (``boundary``).
  ``test_a_viewer_who_got_a_record_by_a_move_reads_from_the_move_on`` goes red.
* HR4. ``_history_input``: accept a bool. The bool cases of
  ``test_a_bad_page_value_is_422_and_reads_nothing`` go red.
* HR5. ``OPEN_EXECUTORS``: drop the history. ``test_a_history_is_class_a_and_it_runs``
  goes red.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_common import studio_fields
from acb_common.paging import PAGE_MAX
from acb_common.studio_access import Viewer
from acb_common.studio_manifest import OBJECT_OP_CLASSES, OBJECT_OPS, parse_uses
from gateway.routes.studio import invoke
from gateway.studio import objects_store as store

from tests.unit.test_studio_action_door import (  # noqa: F401
    ANA,
    BEN,
    MANIFEST,
    _call,
    _client,
    _object,
    _package,
    _record,
    world,
)

_ROOT = Path(__file__).resolve().parents[2]
_STORE = _ROOT / "apps/services/gateway/gateway/studio/objects_store.py"

OID = "00000000-0000-0000-0000-00000000000a"
PID = "00000000-0000-0000-0000-00000000000b"
RID = "00000000-0000-0000-0000-00000000000c"
OWNER = "owner@x.test"
NOW = datetime(2026, 11, 13, tzinfo=UTC)

#: A slip: ``employee`` reads its own rows and does not see ``bonus``, and ``hr``
#: reads all. The formula ``total`` reads ``bonus``, so it is hidden too.
_FIELDS = [("employee", "person"), ("amount", "number"), ("bonus", "number"),
           ("total", "number"), ("note", "text")]
_ACCESS = [{"role": "hr", "read": "all", "write": "all"},
           {"role": "employee", "read": {"where": "employee = $me"}, "write": "all",
            "hide": ["bonus"]}]
_RULES = [{"name": "pay", "kind": "formula", "target": "total", "rounding": "half_up",
           "expr": {"fn": "add", "args": [{"ref": "amount"}, {"ref": "bonus"}]}}]

AS_OWNER = Viewer(OWNER)
AS_E1 = Viewer("e1@x.test", frozenset({"employee"}))
AS_HR = Viewer("hr@x.test", frozenset({"hr"}))


def _row(i: int, op: str, changes: dict[str, Any], actor: str = OWNER,
         via: tuple[str, str] | None = None) -> tuple[Any, ...]:
    kind, ident = via or (None, None)
    return (i, op, actor, kind, ident, json.dumps(changes), NOW)


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def first(self) -> Any:
        return self._rows[0] if self._rows else None

    def all(self) -> list[Any]:
        return list(self._rows)


class _DB:
    """The four statements of the reader. ``record`` is the stored record, or
    None for a deleted one. ``reads`` says whether the row rule of the viewer
    reads it. ``history`` holds the rows in id order."""

    def __init__(self, *, record: dict[str, Any] | None, history: list[tuple[Any, ...]],
                 reads: bool = True) -> None:
        self.record, self.history, self.reads = record, history, reads
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _Result:
        sql, p = str(stmt), dict(params or {})
        self.calls.append((sql, p))
        if sql == store._VIEW_SQL:
            return _Result([(json.dumps(_RULES), json.dumps(_ACCESS), OWNER)])
        if sql in (store._GET_RECORD_SQL, store._GET_RECORD_SCOPED_SQL):
            if self.record is None or (sql == store._GET_RECORD_SCOPED_SQL and not self.reads):
                return _Result([])
            return _Result([(RID, OID, json.dumps(self.record), OWNER, OWNER, NOW, NOW)])
        if sql == store._FIELDS_SQL:
            return _Result([(k, t, {}, k == "employee") for k, t in _FIELDS])
        if sql == store._CHANGES_PAGE_SQL:
            # The filter of the statement: an update must name a shown key.
            rows = [r for r in self.history if r[0] > p["after"] and (
                r[1] != "update" or set(json.loads(r[5])) & set(p["keys"]))]
            return _Result(rows[: p["n"]])
        if sql == store._CHANGES_SCOPE_START_SQL:
            ids = [r[0] for r in self.history if set(json.loads(r[5])) & set(p["wkeys"])]
            return _Result([(max(ids) if ids else None,)])
        raise AssertionError(f"an unexpected statement: {sql[:80]}")

    def page_reads(self) -> list[dict[str, Any]]:
        return [p for s, p in self.calls if s == store._CHANGES_PAGE_SQL]


def _read(db: _DB, viewer: Viewer, **kw: Any) -> store.ChangePage | None:
    return asyncio.run(store.list_record_changes(db, OID, RID, package_id=PID, viewer=viewer,
                                                 **kw))


_HISTORY = [
    _row(1, "create", {"employee": {"old": None, "new": "e1@x.test"},
                       "amount": {"old": None, "new": 10}, "bonus": {"old": None, "new": 5},
                       "total": {"old": None, "new": 15}}),
    _row(2, "update", {"bonus": {"old": 5, "new": 7}, "total": {"old": 15, "new": 17}}),
    _row(3, "update", {"note": {"old": None, "new": "ok"}}, via=("workflow_run", "run-1")),
]
_STORED = {"employee": "e1@x.test", "amount": 10, "bonus": 7, "total": 17, "note": "ok"}


# ── The decision of each field type (R7) ────────────────────────────────────


def test_each_field_type_has_a_history_decision() -> None:
    """A new type in ``FIELD_TYPES`` fails here until ``HISTORY_DECISION``
    names what the reader shows of it."""
    assert set(studio_fields.HISTORY_DECISION) == set(studio_fields.FIELD_TYPES)
    allowed = {studio_fields.HISTORY_VALUES, studio_fields.HISTORY_DROP}
    assert set(studio_fields.HISTORY_DECISION.values()) <= allowed


def test_a_type_with_no_decision_or_a_drop_is_not_shown(monkeypatch: Any) -> None:
    specs = [studio_fields.parse_field_spec({"key": k, "type": t}) for k, t in _FIELDS]
    assert store._history_shown(specs, frozenset()) == {k for k, _ in _FIELDS}
    monkeypatch.setitem(studio_fields.HISTORY_DECISION, "text", studio_fields.HISTORY_DROP)
    monkeypatch.setattr(store, "HISTORY_DECISION", studio_fields.HISTORY_DECISION)
    assert "note" not in store._history_shown(specs, frozenset())
    monkeypatch.delitem(studio_fields.HISTORY_DECISION, "number")
    assert store._history_shown(specs, frozenset()) == {"employee"}


# ── The gate and the hide ───────────────────────────────────────────────────


def test_the_owner_reads_each_change_oldest_first_with_the_actor_and_via() -> None:
    page = _read(_DB(record=_STORED, history=_HISTORY), AS_OWNER)
    assert page is not None and page.next is None
    assert [c.id for c in page.changes] == [1, 2, 3]
    assert [c.op for c in page.changes] == ["create", "update", "update"]
    assert page.changes[0].changes["bonus"] == {"old": None, "new": 5}
    assert page.changes[2].actor == OWNER
    assert (page.changes[2].via_kind, page.changes[2].via_id) == ("workflow_run", "run-1")


def test_a_record_that_the_row_rule_does_not_read_answers_none() -> None:
    db = _DB(record=_STORED, history=_HISTORY, reads=False)
    assert _read(db, AS_E1) is None
    # The gate runs before the history, so no history row is read.
    assert db.page_reads() == []


def test_a_viewer_with_no_grant_answers_none() -> None:
    db = _DB(record=_STORED, history=_HISTORY)
    assert _read(db, Viewer("x@x.test", frozenset({"intern"}))) is None
    assert db.page_reads() == []


def test_a_hidden_key_leaves_changes_and_the_store_keeps_it() -> None:
    """``bonus`` is hidden from an employee, and so is the formula ``total``
    that reads it. The owner and ``hr`` read both."""
    page = _read(_DB(record=_STORED, history=_HISTORY), AS_E1)
    assert page is not None
    assert page.changes[0].changes == {"employee": {"old": None, "new": "e1@x.test"},
                                       "amount": {"old": None, "new": 10}}
    for change in page.changes:
        assert "bonus" not in change.changes and "total" not in change.changes
    hr = _read(_DB(record=_STORED, history=_HISTORY), AS_HR)
    assert hr is not None and "bonus" in hr.changes[1].changes


def test_an_update_of_only_hidden_keys_leaves_the_page() -> None:
    """Row 2 changed only ``bonus`` and ``total``. The statement leaves it
    out, so an employee does not see that it was there, as a get does not
    show a hidden key. The keys of the statement are the shown keys."""
    db = _DB(record=_STORED, history=_HISTORY)
    page = _read(db, AS_E1)
    assert page is not None
    assert [c.id for c in page.changes] == [1, 3]
    [read] = db.page_reads()
    assert read["keys"] == ["amount", "employee", "note"]


def test_only_the_owner_reads_the_history_of_a_deleted_record() -> None:
    gone = [*_HISTORY, _row(4, "delete", {k: {"old": v, "new": None}
                                          for k, v in _STORED.items()})]
    owner = _read(_DB(record=None, history=gone), AS_OWNER)
    assert owner is not None and [c.op for c in owner.changes][-1] == "delete"
    for viewer in (AS_E1, AS_HR):
        db = _DB(record=None, history=gone)
        assert _read(db, viewer) is None
        assert db.page_reads() == []


def test_the_owner_gets_none_for_a_record_with_no_row_and_no_history() -> None:
    assert _read(_DB(record=None, history=[]), AS_OWNER) is None


# ── The page ────────────────────────────────────────────────────────────────


def test_a_page_by_id_names_the_next_after() -> None:
    db = _DB(record=_STORED, history=_HISTORY)
    first = _read(db, AS_OWNER, limit=2)
    assert first is not None and [c.id for c in first.changes] == [1, 2] and first.next == 2
    second = _read(db, AS_OWNER, limit=2, after=first.next)
    assert second is not None and [c.id for c in second.changes] == [3]
    assert second.next is None
    # One past the page, so the reader knows that a next page exists.
    assert [p["n"] for p in db.page_reads()] == [3, 3]
    assert [p["after"] for p in db.page_reads()] == [0, 2]


def _walk(history: list[tuple[Any, ...]], viewer: Viewer) -> list[tuple[list[int], Any]]:
    """Each page of *history* for *viewer*, with a limit of 1."""
    out: list[tuple[list[int], Any]] = []
    after = None
    while True:
        page = _read(_DB(record=_STORED, history=history), viewer, limit=1, after=after)
        assert page is not None
        out.append(([c.id for c in page.changes], page.next))
        if page.next is None:
            return out
        after = page.next


def test_a_hidden_only_update_never_moves_next_or_shortens_a_page() -> None:
    """Fix round 1. A walk with a limit of 1 gives one visible change on each
    page, and ``next`` is never the id of a row that the viewer cannot see. The
    walk is the same with and without the hidden update in the history."""
    seen = _walk(_HISTORY, AS_E1)
    assert seen == [([1], 1), ([3], None)]
    without = [r for r in _HISTORY if r[0] != 2]
    assert _walk(without, AS_E1) == seen


@pytest.mark.parametrize("kw", [{"limit": 0}, {"limit": PAGE_MAX + 1}, {"limit": True},
                                {"limit": 2.0}, {"after": -1}, {"after": True},
                                {"after": "3"}])
def test_the_store_refuses_a_bad_page_before_any_statement(kw: dict[str, Any]) -> None:
    db = _DB(record=_STORED, history=_HISTORY)
    with pytest.raises(store.ObjectStoreError):
        _read(db, AS_OWNER, **kw)
    assert db.calls == []


def test_the_page_sql_reads_one_record_by_key_in_the_bound_org() -> None:
    sql = store._CHANGES_PAGE_SQL
    assert f"organization_id = {store._BOUND_ORG}" in sql
    assert "object_id = CAST(:oid AS uuid)" in sql and "record_id = CAST(:rid AS uuid)" in sql
    assert "id > :after AND " in sql and sql.endswith("ORDER BY id LIMIT :n")
    assert "(op <> 'update' OR jsonb_exists_any(changes, CAST(:keys AS text[])))" in sql
    assert "DESC" not in sql and "?" not in sql
    start = store._CHANGES_SCOPE_START_SQL
    assert f"organization_id = {store._BOUND_ORG}" in start and "?" not in start
    assert "record_id = CAST(:rid AS uuid)" in start
    assert "jsonb_exists_any(changes, CAST(:wkeys AS text[]))" in start


def test_the_reader_takes_viewer_as_a_required_keyword() -> None:
    param = inspect.signature(store.list_record_changes).parameters["viewer"]
    assert param.kind is inspect.Parameter.KEYWORD_ONLY
    assert param.default is inspect.Parameter.empty


def test_the_get_and_the_history_share_one_gate() -> None:
    """``_read_record`` runs the scoped statement. ``get_record`` and
    ``list_record_changes`` call it, and no other function names the scoped get."""
    tree = ast.parse(_STORE.read_text(encoding="utf-8"))
    funcs = {n.name: n for n in ast.walk(tree)
             if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)}
    users = sorted(n for n, f in funcs.items() if any(
        isinstance(x, ast.Name) and x.id == "_GET_RECORD_SCOPED_SQL" for x in ast.walk(f)))
    assert users == ["_read_record"]
    callers = sorted(n for n, f in funcs.items() if any(
        isinstance(x, ast.Call) and getattr(x.func, "id", "") == "_read_record"
        for x in ast.walk(f)))
    assert callers == ["get_record", "list_record_changes"]


def test_one_function_reads_the_history() -> None:
    tree = ast.parse(_STORE.read_text(encoding="utf-8"))
    users = sorted(n.name for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef) and any(
                       isinstance(x, ast.Name) and x.id == "_CHANGES_PAGE_SQL"
                       for x in ast.walk(n)))
    assert users == ["list_record_changes"]


def test_only_narrows_the_keys_in_the_statement() -> None:
    """J2a: the installed keys narrow the shown keys in the statement, so an
    update of a key that the version does not hold is not in the page."""
    db = _DB(record=_STORED, history=_HISTORY)
    page = _read(db, AS_OWNER, only={"amount", "bonus"})
    assert page is not None and [c.id for c in page.changes] == [1, 2]
    assert db.page_reads()[0]["keys"] == ["amount", "bonus"]
    assert set(page.changes[0].changes) == {"amount", "bonus"}


def test_a_viewer_of_an_install_reads_no_writer() -> None:
    page = store.ChangePage((store.RecordChange(
        2, "update", OWNER, "workflow_run", "run-1", {"late": {"old": 2, "new": 3}}, NOW),), 2)
    out = page.without_writer()
    assert (out.changes[0].actor, out.changes[0].via_kind, out.changes[0].via_id) == (
        None, None, None)
    assert out.changes[0].changes == {"late": {"old": 2, "new": 3}} and out.next == 2


# ── The scope start (fix round 1) ───────────────────────────────────────────

#: A slip of E2 that HR moved to E1 at row 3.
_MOVED = [
    _row(1, "create", {"employee": {"old": None, "new": "e2@x.test"},
                       "amount": {"old": None, "new": 10}}),
    _row(2, "update", {"amount": {"old": 10, "new": 20}}),
    _row(3, "update", {"employee": {"old": "e2@x.test", "new": "e1@x.test"},
                       "amount": {"old": 20, "new": 25}}),
    _row(4, "update", {"note": {"old": None, "new": "moved"}}),
]
_MOVED_NOW = {"employee": "e1@x.test", "amount": 25, "note": "moved"}


def test_a_viewer_who_got_a_record_by_a_move_reads_from_the_move_on() -> None:
    page = _read(_DB(record=_MOVED_NOW, history=_MOVED), AS_E1)
    assert page is not None and [c.id for c in page.changes] == [3, 4]
    # The row of the move shows only the new values.
    assert page.changes[0].changes == {"employee": {"new": "e1@x.test"}, "amount": {"new": 25}}
    for change in page.changes:
        assert "e2@x.test" not in json.dumps(change.changes)
        assert 10 not in [v.get("old") for v in change.changes.values()]
    # An after before the move does not reach the rows before it.
    early = _read(_DB(record=_MOVED_NOW, history=_MOVED), AS_E1, after=0, limit=1)
    assert early is not None and [c.id for c in early.changes] == [3]


def test_the_owner_and_a_reader_of_all_read_the_whole_history() -> None:
    for viewer in (AS_OWNER, AS_HR):
        db = _DB(record=_MOVED_NOW, history=_MOVED)
        page = _read(db, viewer)
        assert page is not None and [c.id for c in page.changes] == [1, 2, 3, 4]
        assert page.changes[2].changes["employee"] == {"old": "e2@x.test", "new": "e1@x.test"}
        assert not [s for s, _ in db.calls if s == store._CHANGES_SCOPE_START_SQL]


def test_a_viewer_who_held_the_record_from_its_create_reads_all() -> None:
    page = _read(_DB(record=_STORED, history=_HISTORY), AS_E1)
    assert page is not None and page.changes[0].id == 1
    assert page.changes[0].changes["amount"] == {"old": None, "new": 10}


# ── The door ────────────────────────────────────────────────────────────────

H_MANIFEST: dict[str, Any] = {**MANIFEST, "actions": [
    *MANIFEST["actions"], {"id": "trail", "uses": "object:visit.history"}]}

_H_OUTCOME = {"action": "trail", "uses": "object:visit.history", "class": "A"}


def test_the_history_is_a_verb_of_the_grammar_and_class_a() -> None:
    assert "history" in OBJECT_OPS and OBJECT_OP_CLASSES["history"] == "A"
    ref = parse_uses("object:visit.history")
    assert ref is not None and (ref.source, ref.object, ref.op) == ("object", "visit", "history")
    assert ("object", "history") in invoke.OPEN_EXECUTORS
    assert invoke._READS["history"] is invoke._run_object_history
    assert ("object", "history") not in invoke.DIRECT_EXECUTORS


@pytest.fixture
def hw(world: Any, monkeypatch: Any) -> Any:  # noqa: F811
    """The door world, with the reader patched. It keeps each call and
    answers ``world.history_page``."""
    world.history_calls = []
    world.history_page = store.ChangePage((store.RecordChange(
        7, "update", ANA, None, None, {"site": {"old": "a", "new": "b"}}, NOW),), None)

    async def _reader(db: Any, object_id: str, record_id: str, *, package_id: str,
                      viewer: Any, limit: Any = None, after: Any = None,
                      only: Any = None) -> Any:
        world.history_calls.append({"object_id": object_id, "record_id": record_id,
                                    "package_id": package_id, "viewer": viewer,
                                    "limit": limit, "after": after, "only": only})
        store._uuid(record_id, "record_id")
        return world.history_page

    monkeypatch.setattr(store, "list_record_changes", _reader)
    return world


def _hsetup(w: Any) -> tuple[str, str, str]:
    pid = _package(w, manifest=H_MANIFEST)
    oid = _object(w, pid)
    return pid, oid, _record(w, oid)


def test_a_history_is_class_a_and_it_runs(hw: Any) -> None:
    pid, oid, rid = _hsetup(hw)
    r = _call(_client(hw), pid, "trail", {"id": rid, "after": 3, "limit": 2})
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["next"] is None
    [row] = got["history"]
    assert str(row.pop("at")).startswith("2026-11-13T00:00:00")
    assert row == {"id": 7, "op": "update", "actor": ANA, "via_kind": None, "via_id": None,
                   "changes": {"site": {"old": "a", "new": "b"}}}
    [call] = hw.history_calls
    assert call == {"object_id": oid, "record_id": rid, "package_id": pid,
                    "viewer": Viewer(ANA), "limit": 2, "after": 3, "only": None}
    assert hw.audits[0]["detail"] == {**_H_OUTCOME, "outcome": "ran"}


def test_a_history_that_the_reader_does_not_give_is_404(hw: Any) -> None:
    pid, _, rid = _hsetup(hw)
    hw.history_page = None
    r = _call(_client(hw), pid, "trail", {"id": rid})
    assert r.status_code == 404
    assert r.json() == {"detail": "Not found"}
    assert hw.audits[0]["detail"] == {**_H_OUTCOME, "outcome": "not_found"}


@pytest.mark.parametrize("body", [{"id": None}, {"after": 1}, [], "x", None],
                         ids=["null-id", "no-id", "list", "text", "empty"])
def test_a_body_with_no_record_id_is_422(hw: Any, body: Any) -> None:
    pid, _, _ = _hsetup(hw)
    r = _call(_client(hw), pid, "trail", body)
    assert r.status_code == 422
    assert hw.history_calls == []


@pytest.mark.parametrize("extra", [{"limit": True}, {"after": False}, {"limit": 2.0},
                                   {"after": "3"}, {"limit": None}, {"offset": 0},
                                   {"organization_id": "x"}],
                         ids=lambda e: "-".join(f"{k}={v!r}" for k, v in e.items()))
def test_a_bad_page_value_is_422_and_reads_nothing(hw: Any, extra: dict[str, Any]) -> None:
    pid, _, rid = _hsetup(hw)
    r = _call(_client(hw), pid, "trail", {"id": rid, **extra})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "query_value_invalid"
    assert r.json()["detail"]["message"] == invoke.BAD_HISTORY_INPUT
    assert hw.history_calls == []
    assert hw.audits[0]["detail"]["reason"] == "query_value_invalid"


def test_a_package_of_another_member_is_404_and_reads_no_history(hw: Any) -> None:
    pid, _, rid = _hsetup(hw)
    r = _call(_client(hw, email=BEN), pid, "trail", {"id": rid})
    assert r.status_code == 404
    assert hw.history_calls == []


def test_the_history_audit_row_holds_no_value(hw: Any) -> None:
    pid, _, rid = _hsetup(hw)
    _call(_client(hw), pid, "trail", {"id": rid})
    text = json.dumps(hw.audits[0]["detail"])
    for word in ("site", "changes", '"a"', '"b"', rid):
        assert word not in text, word
    assert re.search(r"\bhistory\b", hw.audits[0]["detail"]["uses"])


def test_each_record_id_of_the_body_is_bound_to_a_uuid(hw: Any) -> None:
    pid, _, _ = _hsetup(hw)
    r = _call(_client(hw), pid, "trail", {"id": "not-a-uuid"})
    assert r.status_code == 422
    assert r.json()["detail"] == invoke.BAD_RECORD_ID


def test_the_reader_of_the_door_is_new_and_named_once() -> None:
    src = inspect.getsource(invoke._run_object_history)
    assert "list_record_changes" in src and "viewer=viewer" in src
    assert "only=package.field_keys(obj.name) if installed else None" in src
    assert "page.without_writer() if installed else page" in src
