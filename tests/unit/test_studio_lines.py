"""WS-STU C13: line items, the hermetic half (no database).

Spec: ``project-docs/specs/ai_studio.md`` §14 C13. A lines relation is a
relation field of a parent object with ``lines``: it names a line object of the
same package and the field of it (``via``) that points back. The parent holds
no value for it. The door saves the parent and its lines in ONE ``apply_writes``
batch, so a save holds at most 49 lines.

Code: ``studio_fields`` (the spec rule and ``lines_problem``), ``studio_manifest``
(A2), ``gateway/routes/studio/invoke.py`` (``_split_lines``) and
``gateway/studio/objects_store.py`` (the link of a write to its parent, and the
check of a new parent). ``test_studio_lines_r8.py`` runs the store and the door
on a real Postgres.

**The mutations (R7).** Commit before each, and restore with git after. Each
result below was run on 2026-11-09.

* H1. ``studio_fields._check_lines``: return at once. The seven cases of
  ``test_a_lines_relation_refuses_a_bad_spec`` go red.
* H2. ``invoke._split_lines``: compare the total with ``MAX_LINES_PER_SAVE + 1``.
  ``test_fifty_lines_are_refused_before_any_write`` goes red.
* The mutations of the store and the door on a real database are in
  ``test_studio_lines_r8.py``.
"""

from __future__ import annotations

import ast
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_common import studio_fields as sf
from acb_common.studio_fields import FieldError
from acb_common.studio_manifest import validate_manifest
from acb_common.studio_rules import MAX_LINES_PER_SAVE, MAX_WRITES_PER_BATCH
from gateway.routes.studio import invoke
from gateway.studio import objects_store as store
from gateway.studio.objects_store import BatchError, Write

_ROOT = Path(__file__).resolve().parents[2]
_STORE = _ROOT / "apps/services/gateway/gateway/studio/objects_store.py"
_DOOR = _ROOT / "apps/services/gateway/gateway/routes/studio/invoke.py"

LINES = {"key": "lines", "type": "relation", "to": "order_line", "lines": "order"}
ORDER: list[dict[str, Any]] = [
    {"key": "title", "type": "text"}, LINES, {"key": "total", "type": "number"}]
LINE: list[dict[str, Any]] = [
    {"key": "order", "type": "relation", "to": "order", "hot": True},
    {"key": "qty", "type": "number", "hot": True},
]
ROLLUP = {"name": "qty_total", "kind": "rollup", "target": "total", "source": "order_line",
          "via": "order", "agg": "sum", "field": "qty"}


def _words(text: str) -> int:
    return len(text.split())


def _manifest(order: list[dict[str, Any]] | None = None,
              line: list[dict[str, Any]] | None = None,
              more: tuple[dict[str, Any], ...] = ()) -> dict[str, Any]:
    return {"studio": 1, "id": "orders", "name": "Orders", "purpose": "Take orders.",
            "objects": [{"name": "order", "fields": ORDER if order is None else order,
                         "rules": [ROLLUP]},
                        {"name": "order_line", "fields": LINE if line is None else line},
                        *more]}


# -- the field spec (studio_fields) ---------------------------------------------


def test_a_lines_relation_parses_and_keeps_its_lines_in_the_stored_spec() -> None:
    field = sf.parse_field_spec(LINES)
    assert (field.type, field.to, field.lines) == ("relation", "order_line", "order")
    assert field.stored_spec() == {"required": False, "many": False, "to": "order_line",
                                   "lines": "order"}
    again = sf.stored_field("lines", "relation", field.stored_spec(), False)
    assert again == field
    # A plain relation keeps no `lines` key, so its stored spec does not change.
    plain = sf.parse_field_spec({"key": "order", "type": "relation", "to": "order"})
    assert plain.lines is None and "lines" not in plain.stored_spec()


@pytest.mark.parametrize("spec,path", [
    ({"key": "lines", "type": "text", "lines": "order"}, "lines"),
    ({**LINES, "lines": "Order"}, "lines"),
    ({**LINES, "lines": 5}, "lines"),
    ({**LINES, "to": "crm.account"}, "to"),
    ({**LINES, "many": True}, "lines"),
    ({**LINES, "hot": True}, "lines"),
    ({**LINES, "required": True}, "lines"),
])
def test_a_lines_relation_refuses_a_bad_spec(spec: dict[str, Any], path: str) -> None:
    problems = sf.validate_field_spec(spec)
    assert path in [p.path for p in problems], problems
    for p in problems:
        assert _words(p.message) <= 25, p.message
    with pytest.raises(FieldError):
        sf.parse_field_spec(spec)


def test_a_stored_lines_value_of_another_shape_reads_as_no_lines() -> None:
    """A stored field runs no authoring check, so a broken stored `lines`
    reads as a plain relation, and the store checks the fit at each batch."""
    assert sf.stored_field("lines", "relation", {"to": "x", "lines": 5}, False).lines is None


def test_a_lines_field_holds_no_value() -> None:
    order = [sf.parse_field_spec(f) for f in ORDER]
    assert sf.validate_record(order, {"title": "A"}) == {"title": "A"}
    for value in ([{"data": {"qty": 1}}], {"id": "x"}, None, []):
        with pytest.raises(FieldError) as refused:
            sf.validate_record(order, {"title": "A", "lines": value})
        assert refused.value.field == "lines" and "holds no value" in refused.value.message
    with pytest.raises(FieldError):
        sf.check_value(order[1], {"id": "x"})


def test_lines_problem_is_the_one_fit_rule() -> None:
    field = sf.parse_field_spec(LINES)
    line = [sf.parse_field_spec(f) for f in LINE]
    assert sf.lines_problem("order", field, "order_line", line) is None
    assert "not an object" in str(sf.lines_problem("order", field, None, None))
    assert "another object" in str(sf.lines_problem("order", field, "order", line))
    no_via = [f for f in line if f.key != "order"]
    assert "not a field" in str(sf.lines_problem("order", field, "order_line", no_via))
    for bad in ({"key": "order", "type": "text"},
                {"key": "order", "type": "relation", "to": "quote"},
                {"key": "order", "type": "relation", "to": "order", "many": True},
                {"key": "order", "type": "relation", "to": "x.order"}):
        fields = [sf.parse_field_spec(bad), line[1]]
        problem = sf.lines_problem("order", field, "order_line", fields)
        assert problem is not None and "must be one relation" in problem, bad
        assert _words(problem) <= 25


# -- A2 (studio_manifest) ---------------------------------------------------------


def test_a2_takes_an_order_with_its_lines_and_a_rollup_over_them() -> None:
    assert validate_manifest(_manifest()) == []


@pytest.mark.parametrize("order,line,more,needle", [
    (None, [LINE[1]], (), "not a field of `order_line`"),
    (None, [{**LINE[0], "to": "quote"}, LINE[1]], ({"name": "quote", "fields": []},),
     "must be one relation to `order`"),
    ([ORDER[0], {**LINES, "to": "order"}, ORDER[2]], None, (), "another object"),
    ([ORDER[0], LINES, {**LINES, "key": "more"}, ORDER[2]], None, (),
     "is the lines of `order` already"),
])
def test_a2_refuses_a_lines_relation_that_does_not_fit(order, line, more, needle) -> None:
    errors = validate_manifest(_manifest(order, line, more))
    found = [e for e in errors if e.path.endswith(".lines")]
    assert found and needle in found[0].message, errors
    assert all(_words(e.message) <= 25 for e in found)


def test_a2_refuses_a_lines_relation_to_an_object_of_no_app() -> None:
    errors = validate_manifest(_manifest([ORDER[0], {**LINES, "to": "nothing"}, ORDER[2]]))
    assert any(e.path == "$.objects[0].fields[1].to" for e in errors), errors


# -- the door: the split of a save (invoke._split_lines) ---------------------------


def _order() -> store.StudioObject:
    now = datetime.now(UTC)
    return store.StudioObject(
        id="00000000-0000-0000-0000-000000000001", package_id="00000000-0000-0000-0000-000000000002",
        name="order", label=None, created_by="ana@x.test", created_at=now, updated_at=now,
        fields=tuple(sf.parse_field_spec(f) for f in ORDER))


def _new(n: int) -> list[dict[str, Any]]:
    return [{"data": {"qty": i}} for i in range(n)]


def test_the_cap_is_one_batch_less_the_parent() -> None:
    assert MAX_LINES_PER_SAVE == MAX_WRITES_PER_BATCH - 1 == 49


def test_forty_nine_lines_split_from_the_record_data() -> None:
    got = invoke._split_lines("create", _order(), {"data": {"title": "A", "lines": _new(49)}})
    assert not isinstance(got, invoke._Fail)
    data, lines = got
    assert data == {"title": "A"} and len(lines) == 49
    assert [(x.key, x.to, x.place, x.id) for x in lines[:2]] == [
        ("lines", "order_line", 1, None), ("lines", "order_line", 2, None)]
    assert lines[-1].data == {"qty": 48}


def test_fifty_lines_are_refused_before_any_write() -> None:
    got = invoke._split_lines("create", _order(), {"data": {"title": "A", "lines": _new(50)}})
    assert isinstance(got, invoke._Fail)
    detail = got.error.detail
    assert got.error.status_code == 422 and detail["code"] == invoke.TOO_MANY_LINES
    assert "at most 49 lines" in detail["message"] and "holds 50" in detail["message"]
    assert "second save" in detail["message"]


@pytest.mark.parametrize("op,lines,code", [
    ("create", {"qty": 1}, invoke.BAD_LINES),
    ("create", [{"qty": 1}], invoke.BAD_LINES),
    ("create", [{"data": {"qty": 1}, "extra": 1}], invoke.BAD_LINES),
    ("create", [{"data": [1]}], invoke.BAD_LINES),
    ("update", [{"id": 5, "data": {}}], invoke.BAD_LINES),
    ("update", [{"id": "x", "delete": True}], invoke.BAD_LINES),
    ("create", [{"id": "00000000-0000-0000-0000-000000000003", "data": {}}], invoke.BAD_LINES),
])
def test_a_list_of_another_shape_is_refused_with_its_field(op, lines, code) -> None:
    body = {"data": {"lines": lines}, **({"id": "x"} if op == "update" else {})}
    got = invoke._split_lines(op, _order(), body)
    assert isinstance(got, invoke._Fail), got
    assert got.error.detail["code"] == code
    assert got.error.detail["fieldErrors"][0]["field"] == "lines"


def test_an_update_takes_new_and_changed_lines_and_a_delete_takes_none() -> None:
    rid = "00000000-0000-0000-0000-000000000004"
    got = invoke._split_lines("update", _order(), {"id": "x", "data": {
        "lines": [{"data": {"qty": 1}}, {"id": rid, "data": {"qty": 2}}]}})
    assert not isinstance(got, invoke._Fail)
    assert [(x.id, x.data) for x in got[1]] == [(None, {"qty": 1}), (rid, {"qty": 2})]
    assert invoke._split_lines("delete", _order(), {"id": "x"}) == (None, [])


def test_each_sentence_of_the_lines_is_short() -> None:
    for text in (invoke.LINES_OVER_CAP, invoke.BAD_LINES_INPUT, invoke.NEW_RECORD_LINES,
                 invoke.NO_SUCH_LINE):
        for sentence in re.split(r"(?<=\.)\s+", text):
            assert _words(sentence) <= 25, sentence


# -- the store: the link of a write to its parent ----------------------------------


class _NoSQL:
    """A session that fails on any statement."""

    statements = 0

    async def execute(self, *_a: Any, **_k: Any) -> Any:
        self.statements += 1
        raise AssertionError("no statement may run")


OID = "00000000-0000-0000-0000-00000000000a"
PID = "00000000-0000-0000-0000-00000000000b"


@pytest.mark.parametrize("writes,index", [
    ([Write("create", OID, data={}, parent=0)], 0),
    ([Write("create", OID, data={}), Write("create", OID, data={}, parent=1)], 1),
    ([Write("create", OID, data={}), Write("create", OID, data={}, parent=True)], 1),
    ([Write("create", OID, data={}), Write("create", OID, data={}, parent=-1)], 1),
    ([Write("delete", OID, record_id=OID), Write("create", OID, data={}, parent=0)], 1),
    ([Write("create", OID, data={}), Write("delete", OID, record_id=OID, parent=0)], 1),
])
def test_a_bad_link_is_refused_before_any_statement(writes, index) -> None:
    import asyncio

    db = _NoSQL()
    with pytest.raises(BatchError) as refused:
        asyncio.run(store.apply_writes(db, "ana@x.test", package_id=PID, writes=writes))
    assert refused.value.index == index and db.statements == 0


# -- the fences (R7) -----------------------------------------------------------------


def _functions(path: Path) -> dict[str, ast.AST]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)}


def _calls(node: ast.AST) -> set[str]:
    return {n.func.id for n in ast.walk(node)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}


def test_each_batch_runs_the_lines_steps_in_their_order() -> None:
    """The relations come after the org lock, the fit after the object locks,
    and the parent check after the writes, under the one deadline."""
    src = ast.unparse(_functions(_STORE)["_run_steps"])
    order = ["_lock_org(", "_find_lines(", "_lock_objects(", "_check_lines_fit(",
             "_apply_write(", "_check_line_parents(", "_recompute_parents("]
    places = [src.index(s) for s in order]
    assert places == sorted(places), order
    assert "_check_line_parents(db, batch, deadline)" in src
    assert "_link" in _calls(_functions(_STORE)["_apply_write"])


def test_each_lines_statement_names_the_org_and_the_package() -> None:
    """RLS hides a missing filter from each R8 test, so a source fence holds
    it. Each alias of `_LINES_SQL` names the bound org, and the parent read
    names the org and the package of the batch."""
    bound = store._BOUND_ORG
    lines = store._LINES_SQL
    for alias in ("f", "p", "c"):
        assert f"{alias}.organization_id = {bound}" in lines, alias
    assert lines.count("package_id = CAST(:pid AS uuid)") == 2
    parents = store._LINE_PARENTS_SQL
    assert re.search(r"(?<![\w.])" + re.escape(f"organization_id = {bound}"), parents)
    assert "package_id = CAST(:pid AS uuid)" in parents and store._IN_PACKAGE in parents


def test_the_package_read_names_the_lines_relation() -> None:
    """A reader of the package (the chat tools, the IDE) sees which field holds
    lines, and the line object that it names."""
    from gateway.routes.studio import packages as routes

    fields = {f["key"]: f for f in routes._object_out(_order())["fields"]}
    assert (fields["lines"]["to"], fields["lines"]["lines"]) == ("order_line", "order")
    assert fields["title"]["lines"] is None


def test_the_door_saves_a_record_and_its_lines_in_one_batch() -> None:
    """One `apply_writes` call in the door, and the lines follow the record."""
    funcs = _functions(_DOOR)
    callers = sorted(name for name, f in funcs.items() if any(
        isinstance(n, ast.Attribute) and n.attr == "apply_writes" for n in ast.walk(f)))
    assert callers == ["_do_object_write"], callers
    src = ast.unparse(funcs["_do_object_write"])
    assert "writes=[write, *line_writes]" in src
    assert "parent=0" in ast.unparse(funcs["_line_writes"])
    assert "_split_lines" in _calls(funcs["_run_ask"])


_RID = "0f1e2d3c-4b5a-4968-8778-a6b5c4d3e2f1"


@pytest.mark.parametrize("value,ok", [
    (_RID, True),
    (f"urn:uuid:{_RID}", False),
    (f"uuid:{_RID}", False),
    ("{" + _RID + "}", False),
    (_RID.upper(), False),
    (_RID.replace("-", ""), False),
    ("not-a-record", False),
    ("", False),
])
def test_a_parent_id_takes_one_form_only(value: str, ok: bool) -> None:
    """Fix round 1, P2. `uuid.UUID` reads each of these forms, and Postgres
    refuses some of them, so the store takes the one form only."""
    assert store._plain_id(value) is ok


def test_the_refusal_of_a_parent_form_is_short_and_names_the_field() -> None:
    link = store._Lines("00000000-0000-0000-0000-000000000001", "order", "lines", "order")
    refused = store._bad_parent(link)
    assert refused.field == "order"
    for sentence in re.split(r"(?<=\.)\s+", refused.message):
        assert _words(sentence) <= 25, sentence


def test_a_published_lines_value_cannot_change() -> None:
    assert "lines" in store._FIELD_ATTRS


def test_the_r8_suite_is_named_in_both_ci_skip_guards() -> None:
    for name in ("pr-check.yml", "deploy.yml"):
        workflow = (_ROOT / ".github/workflows" / name).read_text(encoding="utf-8")
        block = re.search(r"--must-run \\\n((?:[ \t]+tests/unit/\S+(?: \\)?\n)+)", workflow)
        assert block, name
        assert re.search(r"^[ \t]+tests/unit/test_studio_lines_r8\.py(?: \\)?$",
                         block.group(1), re.M), name
