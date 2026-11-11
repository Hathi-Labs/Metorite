"""WS-STU C8a: app roles and row rules, the pure half and the fences.

Spec: ``project-docs/specs/ai_studio.md`` §19.7 and the §14 C8a row. Code:
``acb_common/studio_access.py`` (the grammar and the scope of a viewer),
``studio_manifest.py`` (A2 calls it), ``studio_fields.py`` and
``studio_query.py`` (a hot ``person`` field and its ops), and
``gateway/studio/objects_store.py`` (the one reader and the one writer). The
R8 half is ``test_studio_row_rules_r8.py``.

**The fences (R7).**

* Each call of the six store functions in ``apps/`` and ``packages/`` passes
  ``viewer`` (``test_each_call_of_the_six_passes_a_viewer``), and the six take
  it as a required keyword. The test lists each reader path.
* ``aggregate_records`` takes no viewer, so no code may call it yet.
* The door builds the viewer from the session's member only.
* The second copy of ``HOT_TYPES`` in ``draftEdit.ts`` equals the Python list.

**The mutations (R7).** Each was run on 2026-11-11.

* R1. ``studio_access.Scope.hidden_for``: take the UNION of the hides.
  ``test_a_key_is_hidden_only_where_each_grant_that_reads_it_hides_it`` goes red.
* R2. ``studio_access.Scope.maybe_hidden``: drop the read-all rule.
  ``test_a_grant_that_reads_all_and_shows_a_key_lifts_the_refusal`` goes red.
* R3. ``invoke._do_object_write``: drop ``viewer=viewer`` of ``apply_writes``.
  ``test_each_call_of_the_six_passes_a_viewer`` goes red.
"""

from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path
from typing import Any

import pytest
from acb_common import studio_access as sa
from acb_common import studio_fields as sf
from acb_common import studio_query as sq
from acb_common.studio_manifest import validate_manifest
from acb_common.studio_rules import parse_rules

pytest.importorskip("sqlalchemy")

from gateway.routes.studio import invoke
from gateway.studio import objects_store as store

_ROOT = Path(__file__).resolve().parents[2]
_STORE = _ROOT / "apps/services/gateway/gateway/studio/objects_store.py"
_DRAFT_EDIT = _ROOT / "workbench/control_plane/src/lib/studio/draftEdit.ts"
_SIX = ("get_record", "query_records", "apply_writes", "create_record", "update_record",
        "delete_record")

ME = "ana@x.test"
SLIP_FIELDS = [
    sf.parse_field_spec({"key": "employee", "type": "person", "hot": True}),
    sf.parse_field_spec({"key": "manager", "type": "person", "hot": True}),
    sf.parse_field_spec({"key": "amount", "type": "number"}),
    sf.parse_field_spec({"key": "bank_account", "type": "text"}),
    sf.parse_field_spec({"key": "note", "type": "text"}),
]
HR = {"role": "hr", "read": "all", "write": "all"}
MANAGER = {"role": "manager", "read": {"where": "manager = $me"}}
EMPLOYEE = {"role": "employee", "read": {"where": "employee = $me"}, "hide": ["bank_account"]}
ROLES = ["hr", "manager", "employee"]


def _grants(*specs: dict[str, Any]) -> tuple[sa.Grant, ...]:
    out = sa.parse_access(list(specs), ROLES)
    assert out is not None
    return out


def _scope(*roles: str, specs: tuple[dict[str, Any], ...] = (HR, MANAGER, EMPLOYEE),
           owner: bool = False) -> sa.Scope:
    return sa.scope_for(_grants(*specs), sa.Viewer(ME, frozenset(roles)), owner=owner)


# ── The grammar ─────────────────────────────────────────────────────────────


def test_the_example_of_the_spec_parses_and_stores_the_same() -> None:
    grants = _grants(HR, MANAGER, EMPLOYEE)
    assert [g.stored() for g in grants] == [HR, MANAGER, EMPLOYEE]
    assert grants[1] == sa.Grant("manager", where="manager")
    assert grants[2].hide == ("bank_account",)
    sa.check_access_fit(grants, SLIP_FIELDS)


def test_a_where_takes_spaces_around_the_sign_and_is_stored_in_one_form() -> None:
    grant = _grants({"role": "employee", "read": {"where": "  employee=$me "}})[0]
    assert grant.stored()["read"] == {"where": "employee = $me"}


@pytest.mark.parametrize("read", [
    "some", {"where": "employee = $you"}, {"where": "employee = 'ana'"},
    {"where": "employee = $me", "and": 1}, {"where": "employee = $me OR 1 = 1"},
    {"where": "Employee = $me"}, None, 3,
])
def test_a_read_of_another_shape_is_refused(read: Any) -> None:
    with pytest.raises(sa.AccessError) as refused:
        sa.parse_access([{"role": "hr", "read": read}], ROLES)
    assert refused.value.path == "[0].read"


@pytest.mark.parametrize(("spec", "path"), [
    ({"role": "hr", "read": "all", "write": "some"}, "[0].write"),
    ({"role": "hr", "read": "all", "write": {"where": "employee = $me"}}, "[0].write"),
    ({"role": "boss", "read": "all"}, "[0].role"),
    ({"role": "hr"}, "[0].read"),
    ({"role": "hr", "read": "all", "grant": "x"}, "[0].grant"),
    ({"role": "hr", "read": "all", "hide": "bank_account"}, "[0].hide"),
    ({"role": "hr", "read": "all", "hide": ["note", "note"]}, "[0].hide"),
    ("hr", "[0]"),
])
def test_a_grant_of_another_shape_is_refused(spec: Any, path: str) -> None:
    with pytest.raises(sa.AccessError) as refused:
        sa.parse_access([spec], ROLES)
    assert refused.value.path == path


def test_an_empty_list_two_grants_of_one_role_and_too_many_grants_are_refused() -> None:
    for raw in ([], "all", [HR] * 17):
        with pytest.raises(sa.AccessError):
            sa.parse_access(raw, ROLES)
    with pytest.raises(sa.AccessError) as twice:
        sa.parse_access([HR, HR], ROLES)
    assert twice.value.path == "[1].role"
    assert sa.parse_access(None, ROLES) is None


def test_the_roles_of_a_manifest() -> None:
    assert sa.parse_roles(None) == () and sa.parse_roles(ROLES) == tuple(ROLES)
    for bad in (["hr", "hr"], ["HR"], "hr", ["x"] * 17, [3]):
        with pytest.raises(sa.AccessError):
            sa.parse_roles(bad)


@pytest.mark.parametrize(("where", "fields", "said"), [
    ("note", SLIP_FIELDS, "not one"),
    ("gone", SLIP_FIELDS, "not one"),
    ("employee", [sf.parse_field_spec({"key": "employee", "type": "person"})], "must be hot"),
    ("employee", [sf.parse_field_spec({"key": "employee", "type": "person", "many": True})],
     "not one"),
])
def test_a_where_on_a_cold_field_or_a_field_that_is_not_a_person_is_refused(
        where: str, fields: list[sf.FieldSpec], said: str) -> None:
    grants = sa.parse_access([{"role": "hr", "read": {"where": f"{where} = $me"}}], ROLES)
    with pytest.raises(sa.AccessError) as refused:
        sa.check_access_fit(grants, fields)
    assert said in refused.value.message and refused.value.path == "[0].read"


def test_a_hide_of_a_key_that_is_no_field_is_refused() -> None:
    grants = sa.parse_access([{"role": "hr", "read": "all", "hide": ["salary"]}], ROLES)
    with pytest.raises(sa.AccessError) as refused:
        sa.check_access_fit(grants, SLIP_FIELDS)
    assert refused.value.path == "[0].hide"


def test_a_stored_grant_that_the_code_cannot_read_grants_nothing() -> None:
    assert sa.stored_access(None) is None
    assert sa.stored_access("all") == ()
    kept = sa.stored_access([HR, {"role": "employee", "read": "some"}])
    assert kept == (sa.Grant("hr", write=True),)
    scope = sa.scope_for(kept, sa.Viewer(ME, frozenset({"employee"})), owner=False)
    assert not scope.reads and not scope.writes and not scope.open


def test_access_reads_names_the_person_fields() -> None:
    assert sa.access_reads(_grants(HR, MANAGER, EMPLOYEE)) == {"manager", "employee"}
    assert sa.access_reads(None) == frozenset()


# ── The viewer ──────────────────────────────────────────────────────────────


def test_a_viewer_is_a_lower_case_address_with_role_names() -> None:
    v = sa.Viewer("  Ana@X.Test ", frozenset({"hr"}))
    assert v.email == "ana@x.test" and v.roles == {"hr"}
    for bad in ("", "system:jobs", "no-at", 3):
        with pytest.raises(ValueError):
            sa.Viewer(bad)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        sa.Viewer(ME, frozenset({"HR"}))


# ── The scope of a viewer ───────────────────────────────────────────────────


def test_the_owner_and_an_object_with_no_access_are_open() -> None:
    assert _scope(owner=True).open
    assert sa.scope_for(None, sa.Viewer(ME), owner=False).open
    open_ = _scope(owner=True)
    assert open_.read_all and open_.writes and not open_.row_bound
    assert open_.hidden_for({"bank_account": "x"}) == frozenset()


def test_a_viewer_with_no_role_on_the_object_reads_and_writes_nothing() -> None:
    scope = _scope()
    assert not scope.reads and not scope.writes and scope.row_bound and scope.where_keys == ()


def test_a_viewer_with_many_roles_gets_the_union_of_the_reads() -> None:
    scope = _scope("manager", "employee")
    assert scope.where_keys == ("employee", "manager") and scope.row_bound
    assert scope.sees({"employee": ME}) and scope.sees({"manager": ME})
    assert not scope.sees({"employee": "ben@x.test", "manager": "ben@x.test"})
    assert _scope("hr", "employee").read_all


def test_a_key_is_hidden_only_where_each_grant_that_reads_it_hides_it() -> None:
    scope = _scope("manager", "employee")
    own = {"employee": ME, "manager": "ben@x.test", "bank_account": "1"}
    team = {"employee": "cy@x.test", "manager": ME, "bank_account": "2"}
    both = {"employee": ME, "manager": ME, "bank_account": "3"}
    assert scope.hidden_for(own) == {"bank_account"}
    assert scope.hidden_for(team) == frozenset()
    assert scope.hidden_for(both) == frozenset()
    # A record that no grant reads hides each key.
    assert scope.hidden_for({"note": "x"}) == {"note"}


def test_a_grant_that_reads_all_and_shows_a_key_lifts_the_refusal() -> None:
    assert _scope("employee").maybe_hidden() == {"bank_account"}
    assert _scope("employee", "manager").maybe_hidden() == {"bank_account"}
    assert _scope("employee", "hr").maybe_hidden() == frozenset()


def test_a_roll_up_shows_only_to_a_viewer_who_reads_its_whole_source() -> None:
    rules = parse_rules([{"name": "paid", "kind": "rollup", "target": "total", "source": "slip",
                          "via": "team", "agg": "sum", "field": "amount"}])
    sources = {"slip": _scope("hr")}
    assert sa.rollup_hidden(rules, sources) == frozenset()
    assert sa.rollup_hidden(rules, {"slip": _scope("employee")}) == {"total"}
    assert sa.rollup_hidden(rules, {}) == {"total"}
    hides_amount = ({"role": "hr", "read": "all", "hide": ["amount"]},)
    assert sa.rollup_hidden(rules, {"slip": _scope("hr", specs=hides_amount)}) == {"total"}


def test_a_formula_of_a_hidden_key_is_hidden_too() -> None:
    rules = parse_rules([
        {"name": "f1", "kind": "formula", "target": "net", "rounding": "half_up",
         "expr": {"fn": "add", "args": [{"ref": "amount"}, {"num": "1"}]}},
        {"name": "f2", "kind": "formula", "target": "twice", "rounding": "half_up",
         "expr": {"fn": "mul", "args": [{"ref": "net"}, {"num": "2"}]}},
        {"name": "f3", "kind": "formula", "target": "other", "rounding": "half_up",
         "expr": {"fn": "add", "args": [{"ref": "bonus"}, {"num": "2"}]}},
    ])
    assert sa.with_formulas(frozenset({"amount"}), rules) == {"amount", "net", "twice"}
    assert sa.with_formulas(frozenset(), rules) == frozenset()


# ── Fix round 1: the rules of a write ───────────────────────────────────────

AUDITOR = {"role": "auditor", "read": "all"}
WRITER = {"role": "employee", "read": {"where": "employee = $me"}, "write": "all",
          "hide": ["bank_account"]}
OWN = {"employee": ME, "manager": "ben@x.test"}
TEAM = {"employee": "cy@x.test", "manager": ME}
_ROLES_PLUS = [*ROLES, "auditor"]


def _scope_plus(*roles: str, specs: tuple[dict[str, Any], ...]) -> sa.Scope:
    grants = sa.parse_access(list(specs), _ROLES_PLUS)
    return sa.scope_for(grants, sa.Viewer(ME, frozenset(roles)), owner=False)


def test_the_write_right_is_decided_for_each_record() -> None:
    """P1-1. One grant must both read the record and write."""
    both = _scope_plus("manager", "employee", specs=(MANAGER, WRITER))
    assert both.writes and not both.write_all
    assert both.may_write(OWN) and not both.may_write(TEAM)
    assert both.write_problem(TEAM, None) == ("row", None)
    assert both.write_problem(TEAM, {**TEAM, "note": "x"}) == ("row", None)
    audit = _scope_plus("auditor", "employee", specs=(AUDITOR, WRITER))
    assert audit.read_all and not audit.row_bound and not audit.write_all
    assert not audit.may_write(TEAM) and audit.may_write(OWN)
    assert audit.write_problem(None, {"employee": "cy@x.test"}) == ("leaves", None)
    hr = _scope_plus("hr", specs=(HR,))
    assert hr.write_all and hr.write_problem(TEAM, {**TEAM, "manager": "cy@x.test"}) is None
    assert sa.scope_for(None, sa.Viewer(ME), owner=False).write_problem(TEAM, {}) is None


def test_a_write_cannot_set_a_person_field_that_another_grant_reads() -> None:
    """P1-2. Only a write grant that reads a person field lets a viewer set it."""
    both = _scope_plus("manager", "employee", specs=(MANAGER, WRITER))
    assert both.guarded == {"employee", "manager"}
    assert both.write_problem(OWN, {**OWN, "manager": ME}) == ("guarded", "manager")
    assert both.write_problem(None, {"employee": ME, "manager": ME}) == ("guarded", "manager")
    assert both.write_problem(None, {"employee": ME}) is None
    assert both.write_problem(OWN, {**OWN, "note": "x"}) is None


def test_an_update_cannot_make_more_grants_read_the_record() -> None:
    """P1-2. Two write grants on two person fields: a move into both widens."""
    lead = {"role": "manager", "read": {"where": "manager = $me"}, "write": "all"}
    two = _scope_plus("manager", "employee", specs=(lead, WRITER))
    assert two.write_problem(OWN, {**OWN, "manager": ME}) == ("widens", None)
    assert two.covering(OWN) == {"employee"}


def test_blocked_keys_follow_each_rule_that_reads_hidden_data() -> None:
    """P1-4. A key that a rule reads beside hidden data, or over rows that the
    viewer cannot read, cannot be written. `unique` blocks nothing."""
    rules = parse_rules([
        {"name": "pay", "kind": "formula", "target": "total", "rounding": "half_up",
         "expr": {"fn": "add", "args": [{"ref": "amount"}, {"ref": "bonus"}]}},
        {"name": "cap", "kind": "range", "field": "total", "max": 10},
        {"name": "once", "kind": "unique", "fields": ["month"]},
        {"name": "stock", "kind": "group_sum_min", "field": "qty", "group_by": ["sku"], "min": 0},
    ])
    employee = _scope_plus("employee", specs=(WRITER,))
    hidden = frozenset({"bonus", "total"})
    blocked = sa.blocked_keys(employee, rules, hidden)
    assert {"amount", "qty", "sku"} <= blocked and "month" not in blocked
    hr = _scope_plus("hr", specs=(HR,))
    assert sa.blocked_keys(hr, rules, frozenset()) == frozenset()
    roll = parse_rules([{"name": "heads", "kind": "rollup", "target": "n", "source": "slip",
                         "via": "team", "agg": "count"}])
    assert sa.blocked_keys(employee, (), frozenset(), roll) == {"team"}
    assert sa.blocked_keys(hr, (), frozenset(), roll) == frozenset()
    machine = parse_rules([{"name": "flow", "kind": "state_machine", "field": "state",
                            "initial": "a", "moves": [{"from": "a", "to": "b", "guard": [
                                {"field": "bonus", "op": "gt", "value": 1}]}]}])
    assert sa.blocked_keys(employee, machine, frozenset({"bonus"})) == {"state"}
    tree = parse_rules([{"name": "tree", "kind": "hierarchy", "field": "parent"}])
    assert sa.blocked_keys(employee, tree, frozenset()) == {"parent"}
    assert sa.blocked_keys(hr, tree, frozenset()) == frozenset()


def test_parse_access_refuses_more_json_than_the_check_holds() -> None:
    """The CHECK `studio_objects_access_shape` holds 16 KiB. A list over it is an
    AccessError, so a publish gets a sync problem and never a database fault."""
    sql = next(p for p in (_ROOT / "infra" / "postgres").glob("*.sql")
               if "studio_objects ADD COLUMN IF NOT EXISTS access" in p.read_text(encoding="utf-8"))
    assert f"octet_length(access::text) <= {sa.MAX_ACCESS_BYTES}" in sql.read_text(encoding="utf-8")
    keys = [f"k{i:02d}_" + "x" * 40 for i in range(60)]
    roles = [f"r{i}" for i in range(16)]
    big = [{"role": r, "read": "all", "hide": keys} for r in roles]
    with pytest.raises(sa.AccessError) as refused:
        sa.parse_access(big, roles)
    assert str(sa.MAX_ACCESS_BYTES) in refused.value.message
    assert sa.parse_access(big[:2], roles) is not None


@pytest.mark.parametrize(("filter_", "sort"), [
    ([{"field": ["bank_account"], "op": "eq", "value": "x"}], None),
    ([{"field": {"k": 1}, "op": "eq", "value": "x"}], None),
    (None, {"field": ["bank_account"]}),
])
def test_a_field_name_that_is_not_text_is_refused_by_the_shape(filter_: Any, sort: Any) -> None:
    """P2. The shape check refuses it with `query_field_unknown`, before a read."""
    with pytest.raises(sq.QueryError) as refused:
        sq.check_shape(filter_, sort)
    assert refused.value.code == sq.FIELD_UNKNOWN


def test_the_lines_of_a_table_with_access_need_their_own_access() -> None:
    doc = _manifest([HR])
    doc["objects"][0]["fields"].append({"key": "rows", "type": "relation", "to": "row",
                                        "lines": "payslip"})
    doc["objects"].append({"name": "row", "fields": [
        {"key": "payslip", "type": "relation", "to": "payslip"}]})
    assert [e.path for e in validate_manifest(doc)] == ["$.objects[1]"]
    doc["objects"][1]["access"] = [HR]
    assert validate_manifest(doc) == []


# ── The field type and the query grammar ────────────────────────────────────


def test_a_person_field_may_be_hot_and_its_hot_row_holds_the_whole_address() -> None:
    assert "person" in sf.HOT_TYPES
    field = sf.parse_field_spec({"key": "employee", "type": "person", "hot": True})
    long = "a" * 240 + "@x.test"
    assert sf.hot_value(field, long) == {"k": "employee", "t": long, "n": None, "ts": None, "b": None}
    with pytest.raises(sf.FieldError):
        sf.parse_field_spec({"key": "employee", "type": "person", "hot": True, "many": True})


def test_a_person_takes_eq_ne_in_and_is_empty_and_no_sort_or_group() -> None:
    fields = [sf.parse_field_spec({"key": "employee", "type": "person", "hot": True})]
    for op, value in (("eq", ME), ("ne", ME), ("in", [ME]), ("is_empty", None)):
        item: dict[str, Any] = {"field": "employee", "op": op}
        if value is not None:
            item["value"] = value
        assert sq.parse_query(fields, [item]).conditions[0].op == op
    for op in ("lt", "contains"):
        with pytest.raises(sq.QueryError) as refused:
            sq.parse_query(fields, [{"field": "employee", "op": op, "value": ME}])
        assert refused.value.code == sq.OP_NOT_ALLOWED
    with pytest.raises(sq.QueryError):
        sq.parse_query(fields, [{"field": "employee", "op": "eq", "value": "Ana@X.test"}])
    with pytest.raises(sq.QueryError) as sorted_by:
        sq.parse_query(fields, None, {"field": "employee"})
    assert sorted_by.value.code == sq.OP_NOT_ALLOWED
    assert "person" not in sq.SORT_TYPES and "person" not in sq.GROUP_TYPES


# ── A2 ──────────────────────────────────────────────────────────────────────


def _manifest(access: Any, roles: Any = ROLES, fields: Any = None) -> dict[str, Any]:
    return {
        "studio": 1, "id": "hr", "name": "HR", "purpose": "Payslips.", "roles": roles,
        "objects": [{"name": "payslip", "fields": fields or [
            {"key": "employee", "type": "person", "hot": True},
            {"key": "manager", "type": "person", "hot": True},
            {"key": "bank_account", "type": "text"},
        ], "access": access}],
        "actions": [{"id": "list", "uses": "object:payslip.list"}],
    }


def test_the_validator_takes_the_example_of_the_spec() -> None:
    assert validate_manifest(_manifest([HR, MANAGER, EMPLOYEE])) == []
    assert validate_manifest(_manifest(None, roles=[])) == []


@pytest.mark.parametrize(("doc", "path"), [
    (_manifest([HR, MANAGER], roles=["hr"]), "$.objects[0].access[1].role"),
    (_manifest([{"role": "hr", "read": {"where": "bank_account = $me"}}]),
     "$.objects[0].access[0].read"),
    (_manifest([EMPLOYEE], fields=[{"key": "employee", "type": "person"},
                                   {"key": "bank_account", "type": "text"}]),
     "$.objects[0].access[0].read"),
    (_manifest([HR], roles=["hr", "hr"]), "$.roles[1]"),
    (_manifest([]), "$.objects[0].access"),
])
def test_the_validator_refuses_a_bad_grant_with_its_path(doc: dict[str, Any], path: str) -> None:
    errors = validate_manifest(doc)
    assert [e.path for e in errors] == [path], errors
    assert all(len(e.message.split()) <= 25 for e in errors)


# ── The fences (R7) ─────────────────────────────────────────────────────────


def _store_aliases(tree: ast.Module) -> tuple[set[str], set[str]]:
    """The names that a module binds to the store, and the six it imports bare."""
    mods: set[str] = set()
    bare: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "gateway.studio":
            mods.update(a.asname or a.name for a in node.names if a.name == "objects_store")
        if isinstance(node, ast.ImportFrom) and node.module == "gateway.studio.objects_store":
            bare.update(a.asname or a.name for a in node.names if a.name in _SIX)
        if isinstance(node, ast.Import):
            mods.update(a.asname or a.name for a in node.names
                        if a.name == "gateway.studio.objects_store")
    return mods, bare


def _calls_of_the_six(path: Path) -> list[tuple[str, int, bool]]:
    source = path.read_text(encoding="utf-8-sig")
    if "objects_store" not in source:
        return []  # a module that does not name the store cannot call it
    tree = ast.parse(source)
    mods, bare = _store_aliases(tree)
    if path == _STORE:
        bare = set(_SIX)
    out: list[tuple[str, int, bool]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        name = None
        if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id in mods:
            name = f.attr if f.attr in _SIX else None
        elif isinstance(f, ast.Name) and f.id in bare:
            name = f.id
        if name is not None:
            out.append((name, node.lineno, any(k.arg == "viewer" for k in node.keywords)))
    return out


def _source_files() -> list[Path]:
    return [p for top in ("apps", "packages") for p in (_ROOT / top).rglob("*.py")
            if "node_modules" not in p.parts and ".venv" not in p.parts]


def test_each_call_of_the_six_passes_a_viewer() -> None:
    """The one-reader fence of C8a. A call with no ``viewer`` would read past
    the row rules. A new reader path fails here until its slice lists it, so a
    reviewer sees each path that reads a record."""
    missing: list[str] = []
    readers: set[str] = set()
    for path in _source_files():
        for name, line, has in _calls_of_the_six(path):
            rel = path.relative_to(_ROOT).as_posix()
            readers.add(rel)
            if not has:
                missing.append(f"{rel}:{line} {name}")
    assert missing == []
    # Each reader path of the records today. Chat tools, search (C5) and
    # workflows (B6) reach the records through the door only.
    assert readers == {"apps/services/gateway/gateway/routes/studio/invoke.py"}


def test_the_six_take_viewer_as_a_required_keyword() -> None:
    for name in _SIX:
        param = inspect.signature(getattr(store, name)).parameters["viewer"]
        assert param.kind is inspect.Parameter.KEYWORD_ONLY, name
        assert param.default is inspect.Parameter.empty, name


@pytest.mark.parametrize("name", ["aggregate_records", "read_tree"])
def test_no_code_calls_a_reader_that_takes_no_viewer(name: str) -> None:
    """``aggregate_records`` (C2b) and ``read_tree`` (C18) read every record of
    the object. The first door of each adds a viewer first, and then joins the six."""
    assert "viewer" not in inspect.signature(getattr(store, name)).parameters
    found = []
    for path in _source_files():
        if path == _STORE:
            continue
        text = path.read_text(encoding="utf-8")
        if re.search(rf"\b{name}\s*\(", text):
            found.append(path.relative_to(_ROOT).as_posix())
    assert found == []


def test_the_door_builds_the_viewer_from_the_session_member_only() -> None:
    tree = ast.parse(Path(inspect.getfile(invoke)).read_text(encoding="utf-8"))
    built = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Name) and n.func.id in ("Viewer", "_viewer")]
    assert built, "the door builds no viewer"
    for call in built:
        assert [ast.unparse(a) for a in call.args] == ["member"], ast.unparse(call)
        assert not call.keywords, ast.unparse(call)
    source = inspect.getsource(invoke._run_executor)
    assert "member = str(user.email)" in source


def test_the_second_list_of_hot_types_in_draft_edit_equals_the_first() -> None:
    ts = _DRAFT_EDIT.read_text(encoding="utf-8")
    block = re.search(r"export const HOT_TYPES: readonly string\[\] = \[(.*?)\];", ts, re.S)
    assert block, "HOT_TYPES is missing from draftEdit.ts"
    assert re.findall(r'"(\w+)"', block.group(1)) == list(sf.HOT_TYPES)


@pytest.mark.parametrize("name", [
    "_GET_RECORD_SCOPED_SQL", "_LOCK_RECORD_SCOPED_SQL", "_LINE_PARENTS_SCOPED_SQL",
    "_SHARE_OBJECT_SQL", "_VIEW_SQL", "_SOURCE_ACCESS_SQL", "_OBJECT_ACCESS_SQL",
    "_SET_ACCESS_SQL",
])
def test_each_statement_of_the_row_rules_names_the_org_and_the_package(name: str) -> None:
    sql = getattr(store, name)
    assert f"organization_id = {store._BOUND_ORG}" in sql, name
    assert "package_id = CAST(:pid AS uuid)" in sql or "a.id = o.package_id" in sql, name
    for alias in re.findall(r"\b(?:FROM|JOIN)\s+(?:studio_\w+|apps)\s+(\w+)", sql):
        if alias.upper() in ("WHERE", "ON"):
            continue
        assert f"{alias}.organization_id = {store._BOUND_ORG}" in sql, (name, alias)


def test_a_page_with_a_row_rule_holds_it_in_each_part() -> None:
    """The count, the guard, the head and the tail of one page read the same
    records, so each part that names the package names the row rule too."""
    fields = [sf.parse_field_spec({"key": "employee", "type": "person", "hot": True}),
              sf.parse_field_spec({"key": "note", "type": "text"}),
              sf.parse_field_spec({"key": "day", "type": "date", "hot": True})]
    oid, pid = "00000000-0000-0000-0000-00000000000a", "00000000-0000-0000-0000-00000000000b"
    shapes = [
        sq.parse_query(fields, None),
        sq.parse_query(fields, None, {"field": "day"}),
        sq.parse_query(fields, [{"field": "note", "op": "contains", "value": "x"}]),
        sq.parse_query(fields, [{"field": "employee", "op": "eq", "value": ME}]),
    ]
    for query in shapes:
        sql, params = store.build_query(query, object_id=oid, package_id=pid, limit=10, offset=0,
                                        access=(("employee",), ME))
        assert params["akeys"] == ["employee"] and params["me"] == ME
        assert sql.count(store._IN_PACKAGE) == sql.count("ac.v_text = CAST(:me AS text)") > 0
        plain, plain_params = store.build_query(query, object_id=oid, package_id=pid, limit=10,
                                                offset=0)
        assert "ac.v_text" not in plain and "me" not in plain_params


def test_a_check_of_a_rule_reads_every_record() -> None:
    """A ``unique`` and a ``group_sum_min`` judge the whole object, so their
    statements hold no row rule."""
    fields = [sf.parse_field_spec({"key": "employee", "type": "person", "hot": True})]
    conditions = sq.parse_query(fields, [{"field": "employee", "op": "eq", "value": ME}]).conditions
    sql, _ = store.build_count(conditions, object_id="o", package_id="p", cap=2)
    assert "ac.v_text" not in sql


def test_the_migration_is_expand_only_and_adds_the_column_and_the_hot_type() -> None:
    files = [p for p in (_ROOT / "infra" / "postgres").glob("*.sql")
             if "studio_objects ADD COLUMN IF NOT EXISTS access" in p.read_text(encoding="utf-8")]
    assert len(files) == 1, files
    sql = files[0].read_text(encoding="utf-8")
    assert "lock_timeout" in sql and "studio_objects_access_shape" in sql
    for word in ("CREATE TABLE", "DROP TABLE", "DROP COLUMN", "RENAME", "SET NOT NULL"):
        assert word not in sql, word
    check = sql.split("CONSTRAINT studio_object_fields_hot_type CHECK", 1)[1]
    assert re.findall(r"'(\w+)'", check.split("AND (spec", 1)[0]) == list(sf.HOT_TYPES)
    assert sa.MAX_ROLES == 16 and "BETWEEN 1 AND 16" in sql


def test_this_suite_and_the_r8_suite_are_on_the_must_run_lists() -> None:
    for workflow in ("pr-check.yml", "deploy.yml"):
        text = (_ROOT / ".github" / "workflows" / workflow).read_text(encoding="utf-8")
        assert re.search(r"^[ \t]+tests/unit/test_studio_row_rules_r8\.py(?: \\)?$", text, re.M), workflow
