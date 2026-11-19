"""SEM-1b-1 (task 3736): one query grammar on a CRM module total. Hermetic.

Spec: ``project-docs/specs/semantic_model.md`` §5.1 and the SEM-1b-1 row of
§10.2, "The rules of SEM-1b-1".

``GET /crm/aggregate`` on a module takes ``query``: a JSON list of nodes
joined by AND. A node is a leaf ``{"field", "op", "value"}`` or one group
``{"any": [leaf, ...]}`` of 2 to 8 leaves. The shared shape check lives in
``acb_common.studio_query.check_store_shape``. The CRM compiler lives in
``field_sql.py`` and reuses ``_filter`` for each leaf.

This file holds the rules that need no database: the shape, the caps, the
op refusals, the lowering table, the expansion of ``in``, the refusals of a
core entity and of an org off the flag, the AST fences, and the Studio
bytes. ``test_crm_query_grammar_r8.py`` runs the SQL on a real Postgres.

SEM-1b-2 (section 7): a range leaf on a text field that holds numbers
reads the ONE strict number of CRM-Q34-11. A range leaf picks the day field
of the findings. A refusal of a leaf value names the leaf. A native ``eq``
on a datetime is the day in the CRM zone (:data:`field_sql.DATETIME_EQ`).
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import pytest
from acb_common import studio_query
from acb_common.studio_query import (
    OP_NOT_ALLOWED,
    TOO_BIG,
    VALUE_INVALID,
    QueryError,
    check_store_shape,
)
from fastapi import HTTPException
from gateway.routes.crm import field_sql
from gateway.routes.crm.field_sql import (
    DEFERRED_OPS,
    LOWERED,
    QUERY_CHARS_MAX,
    QUERY_LEAVES_MAX,
    QUERY_OPS,
    RANGE_ENDS,
    FieldRef,
    FieldScope,
    QueryLeaf,
    day_field,
    lower_pair,
    module_where,
    parse_query,
    query_ranges,
    query_sql,
    text_number_keys,
)

from tests.unit.test_crm_aggregate import _field_sql_offenders

_ROOT = Path(__file__).resolve().parents[2]
_CRM = _ROOT / "apps" / "services" / "gateway" / "gateway" / "routes" / "crm"

#: A module scope with one field of each type that a filter takes, and two
#: key words. The keys are mixed case, as Zoho API names are.
_FIELDS = {
    "Grand_Total": FieldRef("Grand_Total", "currency", "Grand Total"),
    "Qty": FieldRef("Qty", "number", "Qty"),
    "Invoice_Date": FieldRef("Invoice_Date", "date", "Invoice Date"),
    "Paid_On": FieldRef("Paid_On", "datetime", "Paid On"),
    "Status": FieldRef("Status", "select", "Status"),
    "Subject": FieldRef("Subject", "text", "Subject"),
    "Taxable": FieldRef("Taxable", "boolean", "Taxable"),
    "Account": FieldRef("Account", "lookup", "Account", lookup_module="Accounts"),
}
_SCOPE = FieldScope(
    column=field_sql.MODULE_COLUMN, owner="module:Invoices",
    fields={**_FIELDS, "@amount": _FIELDS["Grand_Total"], "@parent": _FIELDS["Account"]},
)
_ZONE = "Asia/Kolkata"


def _leaf(key: str, op: str, value: Any) -> dict[str, Any]:
    return {"field": key, "op": op, "value": value}


def _q(*nodes: Any) -> str:
    return json.dumps(list(nodes))


def _refused(raw: str) -> dict[str, Any]:
    with pytest.raises(HTTPException) as caught:
        parse_query(raw)
    assert caught.value.status_code == 422
    detail = caught.value.detail
    assert isinstance(detail, dict), detail
    return detail


# ── 1. The shape, in the shared check ─────────────────────────────────────────


def test_a_query_is_a_list_of_leaves_and_any_groups() -> None:
    nodes = parse_query(_q(
        _leaf("Grand_Total", "gte", 100),
        {"any": [_leaf("Status", "eq", "Paid"), _leaf("Status", "eq", "Sent")]},
    ))
    assert nodes == (
        (QueryLeaf("Grand_Total", "gte", 100),),
        (QueryLeaf("Status", "eq", "Paid"), QueryLeaf("Status", "eq", "Sent")),
    )


@pytest.mark.parametrize("raw", [
    "{",
    "not json",
    '[{"field": "Qty", "op": "eq", "value": NaN}]',
    '[{"field": "Qty", "op": "eq", "value": Infinity}]',
    '{"field": "Qty", "op": "eq", "value": 1}',
    '"Qty"',
    "[1]",
    '[{"field": "Qty", "op": "eq", "value": 1, "extra": 2}]',
    '[{"field": "Qty", "value": 1}]',
    '[{"any": [{"field": "Qty", "op": "eq", "value": 1}]}]',
    '[{"any": [{"any": [{"field": "Qty", "op": "eq", "value": 1}]}, '
    '{"field": "Qty", "op": "eq", "value": 2}]}]',
    '[{"any": [{"field": "Qty", "op": "eq", "value": 1}], "field": "Qty"}]',
    '[{"field": "Qty", "op": "eq"}]',
    '[{"field": "Qty", "op": "eq", "value": null}]',
    '[{"field": "Qty", "op": "eq", "value": {"a": 1}}]',
    '[{"field": "Qty", "op": "eq", "value": [1, 2]}]',
    '[{"field": "Qty", "op": "in", "value": []}]',
    '[{"field": "Qty", "op": "in", "value": 5}]',
    '[{"field": "Subject", "op": "eq", "value": "a\\u0000b"}]',
])
def test_a_bad_shape_or_bad_json_is_query_value_invalid(raw: str) -> None:
    detail = _refused(raw)
    assert detail["code"] in (VALUE_INVALID, "query_field_unknown"), detail
    assert detail["message"]


def test_bad_json_names_the_shape() -> None:
    detail = _refused("{")
    assert detail["code"] == VALUE_INVALID
    assert "JSON" in detail["message"]


def test_a_key_that_is_no_text_is_refused_before_any_read() -> None:
    detail = _refused(_q({"field": 5, "op": "eq", "value": 1}))
    assert detail["code"] == "query_field_unknown"


def test_the_cap_is_eight_leaves() -> None:
    eight = [_leaf("Qty", "gte", i) for i in range(QUERY_LEAVES_MAX)]
    assert len(parse_query(json.dumps(eight))) == 8
    detail = _refused(json.dumps([*eight, _leaf("Qty", "lte", 9)]))
    assert detail["code"] == TOO_BIG
    assert "8" in detail["message"]


def test_the_leaves_of_a_group_count_toward_the_cap() -> None:
    group = {"any": [_leaf("Qty", "eq", i) for i in range(5)]}
    assert parse_query(_q(group, _leaf("Qty", "gte", 1), _leaf("Qty", "lte", 9)))
    assert _refused(_q(group, *[_leaf("Qty", "gte", i) for i in range(4)]))["code"] == TOO_BIG


@pytest.mark.parametrize(("size", "code"), [(1, VALUE_INVALID), (9, TOO_BIG)])
def test_an_any_group_holds_two_to_eight_leaves(size: int, code: str) -> None:
    group = {"any": [_leaf("Qty", "eq", i) for i in range(size)]}
    assert _refused(_q(group))["code"] == code
    for ok in (2, 8):
        assert parse_query(_q({"any": [_leaf("Qty", "eq", i) for i in range(ok)]}))


def test_the_query_text_holds_at_most_4000_characters() -> None:
    assert QUERY_CHARS_MAX == 4000
    padded = _q(_leaf("Subject", "eq", "x" * 100))
    padded += " " * (QUERY_CHARS_MAX - len(padded))
    assert len(padded) == QUERY_CHARS_MAX
    assert parse_query(padded)
    assert _refused(padded + " ")["code"] == TOO_BIG


@pytest.mark.parametrize("op", ["ne", "is_empty", "contains", "matches"])
def test_each_deferred_op_is_query_op_not_allowed_and_names_the_legal_ops(op: str) -> None:
    assert op in DEFERRED_OPS
    detail = _refused(_q(_leaf("Subject", op, "x")))
    assert detail["code"] == OP_NOT_ALLOWED
    assert "eq, in, gte, lte, gt, lt" in detail["message"], detail
    # The refusal names the key, and never the value.
    assert "Subject" in detail["message"]
    assert "'x'" not in detail["message"]


def test_an_unknown_op_is_refused_the_same_way() -> None:
    detail = _refused(_q(_leaf("Subject", "between", "x")))
    assert detail["code"] == OP_NOT_ALLOWED
    assert "between" not in detail["message"]


def test_the_ops_of_this_slice() -> None:
    assert QUERY_OPS == ("eq", "in", "gte", "lte", "gt", "lt")
    assert set(DEFERRED_OPS) == {"ne", "is_empty", "contains", "matches"}


def test_the_shared_check_takes_the_ops_of_each_store() -> None:
    """The check is pure: the op set comes from the store. It applies no key
    pattern, so a mixed-case CRM key and a key word pass."""
    nodes = check_store_shape([_leaf("Grand_Total", "eq", 1), _leaf("@amount", "gte", 2)],
                              ("eq", "gte"))
    assert [n[0]["field"] for n in nodes] == ["Grand_Total", "@amount"]
    with pytest.raises(QueryError) as caught:
        check_store_shape([_leaf("Grand_Total", "lt", 1)], ("eq", "gte"))
    assert caught.value.code == OP_NOT_ALLOWED
    assert "Use eq, gte." in caught.value.message


# ── 2. The expansion of `in` ──────────────────────────────────────────────────


def test_in_expands_to_an_any_group_of_eq_leaves() -> None:
    nodes = parse_query(_q(_leaf("Status", "in", ["Paid", "Sent"])))
    assert nodes == ((QueryLeaf("Status", "eq", "Paid"), QueryLeaf("Status", "eq", "Sent")),)


def test_in_inside_a_group_joins_the_group() -> None:
    nodes = parse_query(_q({"any": [_leaf("Status", "in", ["Paid", "Sent"]),
                                    _leaf("Qty", "eq", 3)]}))
    assert nodes == ((QueryLeaf("Status", "eq", "Paid"), QueryLeaf("Status", "eq", "Sent"),
                      QueryLeaf("Qty", "eq", 3)),)


def test_the_values_of_in_count_toward_the_cap() -> None:
    assert parse_query(_q(_leaf("Status", "in", [str(i) for i in range(8)])))
    detail = _refused(_q(_leaf("Status", "in", [str(i) for i in range(5)]),
                         _leaf("Qty", "in", [1, 2, 3, 4])))
    assert detail["code"] == TOO_BIG


def test_in_compiles_to_or_of_the_eq_clauses() -> None:
    nodes = parse_query(_q(_leaf("Status", "in", ["Paid", "Sent"])))
    where = query_sql(_SCOPE, nodes, zone=_ZONE)
    assert " OR " in where.sql
    assert where.sql.startswith("((") and where.sql.endswith("))")
    assert where.params["agf_qv0_0"] == ["paid"]
    assert where.params["agf_qv0_1"] == ["sent"]


# ── 3. The lowering table equals the old parameters ──────────────────────────


def _normal(sql: str, params: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """The SQL and the bound values of one leaf, with the bound names of the
    query (``agf_qk0_0``) renamed to the names of one old pair (``agf_k0``)."""
    rename = {"agf_qk0_0": "agf_k0", "agf_qv0_0": "agf_v0", "agf_qx0_0": "agf_x0"}
    out = re.sub(r"agf_q[kvx]0_0", lambda m: rename[m.group(0)], sql)
    return out, {re.sub(r"agf_q[kvx]0_0", lambda m: rename[m.group(0)], k): v
                 for k, v in params.items()}


@pytest.mark.parametrize(("name", "value"), [
    ("Grand_Total", "100"),
    ("Grand_Total.from", "100"),
    ("Grand_Total.to", "250.5"),
    ("@amount.from", "1"),
    ("Qty", "5"),
    ("Invoice_Date", "2026-11-01"),
    ("Invoice_Date.from", "2026-11-01"),
    ("Invoice_Date.to", "2026-11-30"),
    ("Paid_On", "2026-11-01"),
    ("Paid_On.from", "2026-11-01"),
    ("Paid_On.to", "2026-11-01"),
    ("Status", "Paid"),
    ("Subject", "hello"),
    ("Taxable", "true"),
    ("Account", "Acme"),
    ("@parent", "Acme"),
])
def test_a_lowered_pair_compiles_to_the_clause_of_the_old_parameter(name: str, value: str) -> None:
    """``field.<key>=v`` is ``eq``, ``.from`` is ``gte`` and ``.to`` is
    ``lte``. The query leaf builds the SAME clause through the same
    ``_filter``, so the lowering changes no total. SEM-1b-2: the leaf of an
    old pair is marked ``lowered`` (``lower_pair``), so a datetime ``=``
    keeps its old clause too."""
    end = next((e for e in RANGE_ENDS if name.endswith(e)), None)
    key = name[: -len(end)] if end else name
    leaf = lower_pair(name, value)
    assert leaf == QueryLeaf(key, LOWERED[end], value, lowered=True)
    old = module_where(_SCOPE, "Invoices", [(name, value)], zone=_ZONE)
    prefix = " WHERE module = :agf_module AND "
    assert old.sql.startswith(prefix)
    old_params = {k: v for k, v in old.params.items() if k not in ("agf_module",)}
    new = query_sql(_SCOPE, ((leaf,),), zone=_ZONE)
    sql, params = _normal(new.sql, new.params)
    assert sql == old.sql[len(prefix):]
    assert params == old_params
    assert new.source == old.source


def test_the_lowering_table() -> None:
    assert LOWERED == {None: "eq", ".from": "gte", ".to": "lte"}
    # The old parameters take no new end: `field.<key>.gt` stays unknown.
    assert RANGE_ENDS == {".from": ">=", ".to": "<="}


def test_a_json_number_and_a_numeric_string_bind_the_same_value() -> None:
    by_number = query_sql(_SCOPE, ((QueryLeaf("Grand_Total", "gte", 100),),), zone=_ZONE)
    by_text = query_sql(_SCOPE, ((QueryLeaf("Grand_Total", "gte", "100"),),), zone=_ZONE)
    assert by_number == by_text
    assert by_number.params["agf_qv0_0"] == "100"


def test_a_bool_is_refused_on_a_number_and_taken_on_a_boolean() -> None:
    with pytest.raises(HTTPException) as caught:
        query_sql(_SCOPE, ((QueryLeaf("Grand_Total", "eq", True),),), zone=_ZONE)
    assert caught.value.detail["code"] == VALUE_INVALID
    taxable = query_sql(_SCOPE, ((QueryLeaf("Taxable", "eq", True),),), zone=_ZONE)
    assert taxable.params["agf_qv0_0"] == "true"


@pytest.mark.parametrize(("op", "sign"), [("gt", ">"), ("lt", "<")])
def test_gt_and_lt_compare_strictly(op: str, sign: str) -> None:
    money = query_sql(_SCOPE, ((QueryLeaf("Grand_Total", op, 5),),), zone=_ZONE)
    assert f" {sign} CAST(:agf_qv0_0 AS numeric)" in money.sql
    day = query_sql(_SCOPE, ((QueryLeaf("Paid_On", op, "2026-11-01"),),), zone=_ZONE)
    assert f" {sign} CAST(:agf_qv0_0 AS date)" in day.sql
    # A datetime takes its day in the CRM zone, the bound zone parameter.
    assert ":agf_zone" in day.sql and day.params[field_sql.ZONE_PARAM] == _ZONE


def test_a_range_op_on_a_text_field_is_the_old_refusal() -> None:
    with pytest.raises(HTTPException) as caught:
        query_sql(_SCOPE, ((QueryLeaf("Subject", "gte", "a"),),), zone=_ZONE)
    assert "A range takes a date or a number field." in caught.value.detail["message"]


def test_an_unknown_key_names_the_legal_fields() -> None:
    with pytest.raises(HTTPException) as caught:
        query_sql(_SCOPE, ((QueryLeaf("Grand_Totl", "eq", 1),),), zone=_ZONE)
    detail = caught.value.detail
    assert detail["code"] == "unknown_field"
    assert "Its fields are:" in detail["message"]
    assert "Grand_Total" in detail["message"]


def test_no_key_and_no_value_reaches_the_sql_text() -> None:
    hostile = "x') OR 1=1; DROP TABLE crm_deals; --"
    scope = FieldScope(column=field_sql.MODULE_COLUMN, owner="module:Invoices",
                       fields={hostile: FieldRef(hostile, "text", "x")})
    where = query_sql(scope, ((QueryLeaf(hostile, "eq", "v' OR '1'='1"),),), zone=_ZONE)
    assert "DROP" not in where.sql and "'1'='1" not in where.sql
    assert hostile in where.params.values()


# ── 4. The route: a core entity, and an org off the flag ─────────────────────


@pytest.fixture
def bound(monkeypatch):
    """A bound tenant on the CRM switch. The test sets the flag list."""
    from acb_common import get_settings
    from acb_common.db import bind_tenant, clear_tenant, release_tenant

    from tests.unit._crm_fakes import crm_switch_on

    crm_switch_on(monkeypatch, "*")
    org = "00000000-0000-0000-0000-00000000a001"
    settings = get_settings()

    def flag(value: str) -> None:
        monkeypatch.setattr(settings, "semantic_model_orgs", value, raising=False)

    scope = clear_tenant()
    bind_tenant(org)
    try:
        yield flag
    finally:
        release_tenant(scope)


async def _compute_refusal(entity: str, query: str) -> HTTPException:
    from gateway.routes.crm.aggregate import compute
    from gateway.routes.crm.records import ListParams

    with pytest.raises(HTTPException) as caught:
        await compute(entity, params=ListParams(), query=query)
    return caught.value


async def test_a_query_from_an_org_off_the_flag_names_field_filters(bound) -> None:
    bound("")
    exc = await _compute_refusal("module:Invoices", _q(_leaf("Qty", "eq", 1)))
    assert exc.status_code == 422
    assert exc.detail["code"] == "query_off"
    assert "field_filters" in exc.detail["message"]


@pytest.mark.parametrize("entity", ["leads", "deals", "contacts", "organizations"])
async def test_a_query_on_a_core_entity_names_crm_list_records(bound, entity: str) -> None:
    bound("*")
    exc = await _compute_refusal(entity, _q(_leaf("Qty", "eq", 1)))
    assert exc.status_code == 422
    assert exc.detail["code"] == "query_needs_module"
    assert exc.detail["message"] == (
        "A query takes a module entity. For leads, deals, contacts or organizations, "
        "give the filters of crm_list_records.")


async def test_a_bad_query_is_refused_before_any_session(bound) -> None:
    bound("*")
    exc = await _compute_refusal("module:Invoices", _q(_leaf("Qty", "ne", 1)))
    assert exc.detail["code"] == OP_NOT_ALLOWED


def test_the_route_takes_query_and_the_digest_does_not_pass_it() -> None:
    import inspect

    from gateway.routes.crm import aggregate

    assert "query" in aggregate.MODULE_PARAMS
    assert inspect.signature(aggregate.compute).parameters["query"].default is None
    assert "query" in inspect.signature(aggregate.aggregate).parameters
    digest = ast.parse((_ROOT / "apps/services/gateway/gateway/crm_sync/digest.py")
                       .read_text(encoding="utf-8"))
    calls = [n for n in ast.walk(digest) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Name) and n.func.id == "compute"]
    assert calls, "the digest calls compute"
    assert all("query" not in {k.arg for k in call.keywords} for call in calls)


# ── 5. The fences: one builder, no SQL in the route ──────────────────────────


def _calls(tree: ast.AST, function: str) -> set[str]:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == function:
            return {
                n.func.id for n in ast.walk(node)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            }
    raise AssertionError(f"no function {function}")


def test_the_aggregate_holds_no_sql_of_the_query() -> None:
    source = (_CRM / "aggregate.py").read_text(encoding="utf-8")
    assert _field_sql_offenders(source) == []
    tree = ast.parse(source)
    strings = [n.value for n in ast.walk(tree)
               if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    assert not [s for s in strings if " OR " in s]
    # The aggregate reaches the query SQL through field_sql only.
    assert "_with_query" in _calls(tree, "_read")
    assert "query_where" in _calls(tree, "_with_query")
    assert "_query_nodes" in _calls(tree, "_compute")
    assert "parse_query" in _calls(tree, "_query_nodes")


def test_the_query_compiler_reuses_the_one_filter_builder() -> None:
    tree = ast.parse((_CRM / "field_sql.py").read_text(encoding="utf-8"))
    assert "_filter" in _calls(tree, "query_sql")
    assert "_filter" in _calls(tree, "module_where")
    # The leaf reads its stored variants and its lookup ids the old way.
    assert {"text_matches", "lookup_ids", "query_sql"} <= _calls(tree, "query_where")
    # No second clause builder: only `_filter` calls `_filter_value`.
    callers = [
        node.name for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and "_filter_value" in _calls(tree, node.name)
    ]
    assert callers == ["_filter"]


# ── 6. Studio stays byte for byte ─────────────────────────────────────────────

#: sha256 of ``json.dumps(schema, sort_keys=True)`` on main before SEM-1b-1.
_LIST_SCHEMA_SHA = "5e9047963da4d4c99d833c14a6e669bb1ee510321ba87b936a17aca4096ecfd6"
_AGGREGATE_SCHEMA_SHA = "48c32a4dc498b859d489d25f1d2e3b4b16fc5650acd192b650ba17670e6bef9a"


def _sha(schema: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(schema, sort_keys=True).encode()).hexdigest()


def test_studio_ops_and_schemas_do_not_change() -> None:
    assert studio_query.OPS == (
        "eq", "ne", "lt", "lte", "gt", "gte", "in", "contains", "is_empty")
    assert _sha(studio_query._LIST_INPUT_SCHEMA) == _LIST_SCHEMA_SHA
    assert _sha(studio_query.list_input_schema()) == _LIST_SCHEMA_SHA
    assert _sha(studio_query.aggregate_input_schema()) == _AGGREGATE_SCHEMA_SHA


def test_the_studio_parse_does_not_call_the_store_check() -> None:
    tree = ast.parse(Path(studio_query.__file__).read_text(encoding="utf-8"))
    for function in ("parse_query", "check_shape", "parse_aggregate", "check_aggregate_shape"):
        assert "check_store_shape" not in _calls(tree, function)


# ── 7. SEM-1b-2: text numbers, the day field, the leaf words, datetime eq ────

#: A module scope with a text field that holds numbers, marked as
#: ``field_sql.admit_text_numbers`` marks it (CRM-Q34-11).
_TEXT_NUMBER = FieldRef("Ref_Amount", "text", "Ref Amount", text_number=True)
_TN_SCOPE = FieldScope(
    column=field_sql.MODULE_COLUMN, owner="module:Bills",
    fields={**_FIELDS, "Ref_Amount": _TEXT_NUMBER},
)


@pytest.mark.parametrize(("op", "end"), [("gte", ".from"), ("lte", ".to")])
def test_a_range_leaf_on_a_text_number_builds_the_strict_clause_of_the_old_range(
        op: str, end: str) -> None:
    """B: the query leaf and ``field.<key>.from``/``.to`` build the SAME
    clause, with the ONE strict pattern of CRM-Q34-11 bound."""
    old = module_where(_TN_SCOPE, "Bills", [(f"Ref_Amount{end}", "10")], zone=_ZONE)
    prefix = " WHERE module = :agf_module AND "
    new = query_sql(_TN_SCOPE, ((QueryLeaf("Ref_Amount", op, 10),),), zone=_ZONE)
    sql, params = _normal(new.sql, new.params)
    assert sql == old.sql[len(prefix):]
    assert params == {k: v for k, v in old.params.items() if k != "agf_module"}
    assert params[field_sql.TEXT_NUMBER_PARAM] == field_sql.TEXT_NUMBER_PATTERN
    assert "regexp_match(" in new.sql


@pytest.mark.parametrize(("op", "sign"), [("gt", ">"), ("lt", "<")])
def test_a_strict_leaf_on_a_text_number_reads_the_strict_number(op: str, sign: str) -> None:
    where = query_sql(_TN_SCOPE, ((QueryLeaf("Ref_Amount", op, 5),),), zone=_ZONE)
    assert f" {sign} CAST(:agf_qv0_0 AS numeric)" in where.sql
    assert where.params[field_sql.TEXT_NUMBER_PARAM] == field_sql.TEXT_NUMBER_PATTERN


def test_an_eq_leaf_on_a_text_number_stays_the_text_filter_of_the_old_eq() -> None:
    """SPELLING (semantic_model.md §5.1): ``eq`` on text keeps the variant
    count, as ``field.<key>=`` does after CRM-Q34-11. So the lowering of
    ``=`` changes no total, and no ``eq`` admits a text field."""
    old = module_where(_TN_SCOPE, "Bills", [("Ref_Amount", "12")], zone=_ZONE)
    new = query_sql(_TN_SCOPE, ((QueryLeaf("Ref_Amount", "eq", 12),),), zone=_ZONE)
    assert _normal(new.sql, new.params)[0] == old.sql[len(" WHERE module = :agf_module AND "):]
    assert field_sql.TEXT_NUMBER_PARAM not in new.params


@pytest.mark.parametrize(("op", "keys"), [
    ("gte", ["Ref_Amount"]), ("lte", ["Ref_Amount"]), ("gt", ["Ref_Amount"]),
    ("lt", ["Ref_Amount"]), ("eq", []), ("in", []),
])
def test_a_range_leaf_admits_its_text_field_as_a_range_pair_does(op: str, keys: list) -> None:
    """B: ``query_ranges`` gives each range leaf in the pair form, so
    ``text_number_keys`` admits its field as it admits ``.from``/``.to``."""
    value: Any = [1, 2] if op == "in" else 1
    nodes = parse_query(_q(_leaf("Ref_Amount", op, value)))
    assert text_number_keys("count", None, None, query_ranges(_TN_SCOPE, nodes)) == keys
    if keys:
        old = [("Ref_Amount.from", "1")]
        assert text_number_keys("count", None, None, old) == keys


def _calls_any(tree: ast.AST, function: str) -> set[str]:
    """Each name that *function* calls, as a name or as an attribute."""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == function:
            return {
                n.func.id if isinstance(n.func, ast.Name) else n.func.attr
                for n in ast.walk(node)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name | ast.Attribute)
            }
    raise AssertionError(f"no function {function}")


def _functions_holding(tree: ast.AST, needle: str) -> list[str]:
    """The innermost functions whose string constants hold *needle*."""
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        inner = [n for n in ast.walk(node) if n is not node
                 and isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)]
        skip = {id(m) for f in inner for m in ast.walk(f)}
        if any(isinstance(n, ast.Constant) and isinstance(n.value, str) and needle in n.value
               and id(n) not in skip for n in ast.walk(node)):
            found.append(node.name)
    return found


def test_there_is_one_strict_number_expression() -> None:
    """B, the fence: the query path reads a text number through the ONE
    expression of CRM-Q34-11 (``_number`` with ``text_number``, reached by
    ``_amount``). A second number expression anywhere in the CRM routes
    fails here."""
    trees = {path.name: ast.parse(path.read_text(encoding="utf-8"))
             for path in sorted(_CRM.glob("*.py"))}
    holders = {name: _functions_holding(tree, "regexp_match(") for name, tree in trees.items()}
    assert {k: v for k, v in holders.items() if v} == {"field_sql.py": ["_number"]}
    # The pattern is bound in one place, and only the text-number builder
    # names it.
    binders = {name: _functions_holding(tree, "array_to_string(") for name, tree in trees.items()}
    assert {k: v for k, v in binders.items() if v} == {"field_sql.py": ["_number"]}
    fs = trees["field_sql.py"]
    assert "_filter" in _calls_any(fs, "query_sql")
    assert "_amount" in _calls_any(fs, "_filter")
    assert "_numbers" in _calls_any(fs, "_amount")
    assert "_number" in _calls_any(fs, "_numbers")
    # The total admits the text fields of the query ranges.
    agg = trees["aggregate.py"]
    assert {"query_ranges", "text_number_keys", "admit_text_numbers"} <= _calls_any(agg, "_read")
    # The module LIST builder of CRM-Q34-4 is NOT changed here: a list range on
    # a text number stays a follow-up row of CRM-Q34-4 (it needs its own R8).
    assert "admit_text_numbers" not in _calls_any(fs, "module_field_where")


def test_a_range_leaf_picks_the_day_field_as_a_range_pair_does() -> None:
    """C: the day of the findings is the field of the first date range of
    the old pairs or of the query, the same field both ways."""
    old = [("Invoice_Date.from", "2026-10-01"), ("Invoice_Date.to", "2026-10-31")]
    nodes = parse_query(_q(_leaf("Invoice_Date", "gte", "2026-10-01"),
                           _leaf("Invoice_Date", "lte", "2026-10-31")))
    by_query = day_field(_SCOPE, None, query_ranges(_SCOPE, nodes))
    assert by_query is not None and by_query == day_field(_SCOPE, None, old)
    assert by_query.key == "Invoice_Date"
    strict = parse_query(_q(_leaf("Paid_On", "gt", "2026-10-01")))
    assert day_field(_SCOPE, None, query_ranges(_SCOPE, strict)).key == "Paid_On"
    # An eq on a date is no range, as `field.<key>=<day>` is none.
    assert query_ranges(_SCOPE, parse_query(_q(_leaf("Invoice_Date", "eq", "2026-10-01")))) == []


def test_the_total_reads_the_query_ranges_for_the_day_and_the_aliases() -> None:
    """C: ``_read`` gives the day check the ranges of the pairs AND the
    query, and ``_with_query`` seeds the alias sets of the query leaves."""
    tree = ast.parse((_CRM / "aggregate.py").read_text(encoding="utf-8"))
    read = next(n for n in ast.walk(tree)
                if isinstance(n, ast.AsyncFunctionDef) and n.name == "_read")
    day_calls = [n for n in ast.walk(read) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Name) and n.func.id == "_day_of"]
    assert len(day_calls) == 1
    assert isinstance(day_calls[0].args[3], ast.Name) and day_calls[0].args[3].id == "ranges"
    assert "_seed_aliases" in _calls(tree, "_with_query")


@pytest.mark.parametrize(("op", "value", "words"), [
    ("gte", "abc", "The value of `Grand_Total` gte must be a number."),
    ("lte", "1" * 61, "The value of `Grand_Total` lte must be a number of at most 60 digits"),
    ("eq", "x", "The value of `Grand_Total` eq must be a number."),
])
def test_a_bad_number_in_a_query_leaf_names_the_leaf(op: str, value: str, words: str) -> None:
    """D: the 422 names the field and the op of the leaf, never an old
    parameter that the caller did not send."""
    with pytest.raises(HTTPException) as caught:
        query_sql(_SCOPE, ((QueryLeaf("Grand_Total", op, value),),), zone=_ZONE)
    message = caught.value.detail["message"]
    assert message.startswith(words), message
    assert "field." not in message


@pytest.mark.parametrize(("name", "words"), [
    ("Grand_Total.from", "'field.Grand_Total.from' must be a number."),
    ("Grand_Total", "'field.Grand_Total' must be a number."),
])
def test_a_bad_number_in_an_old_parameter_keeps_its_words(name: str, words: str) -> None:
    for build in (lambda: module_where(_SCOPE, "Invoices", [(name, "abc")], zone=_ZONE),
                  lambda: query_sql(_SCOPE, ((lower_pair(name, "abc"),),), zone=_ZONE)):
        with pytest.raises(HTTPException) as caught:
            build()
        assert caught.value.detail["message"] == words


def test_a_bad_day_or_bool_in_a_query_leaf_names_the_leaf() -> None:
    with pytest.raises(HTTPException) as caught:
        query_sql(_SCOPE, ((QueryLeaf("Invoice_Date", "gte", "soon"),),), zone=_ZONE)
    assert caught.value.detail["message"].startswith("The value of `Invoice_Date` gte must be")
    with pytest.raises(HTTPException) as caught:
        query_sql(_SCOPE, ((QueryLeaf("Taxable", "eq", "maybe"),),), zone=_ZONE)
    assert caught.value.detail["message"] == "The value of `Taxable` eq must be true or false."


def test_a_native_datetime_eq_is_the_day_in_the_crm_zone() -> None:
    """E, the rule ``native_zone``: a native ``eq <day>`` on a datetime is
    exactly ``gte <day>`` AND ``lte <day>``, the day in the CRM zone."""
    assert field_sql.DATETIME_EQ == "native_zone"
    assert field_sql.DATETIME_EQ in field_sql.DATETIME_EQ_RULES
    eq = query_sql(_SCOPE, ((QueryLeaf("Paid_On", "eq", "2026-11-01"),),), zone=_ZONE)
    both = query_sql(_SCOPE, ((QueryLeaf("Paid_On", "gte", "2026-11-01"),),
                              (QueryLeaf("Paid_On", "lte", "2026-11-01"),)), zone=_ZONE)
    assert eq.sql.startswith("(") and " AND " in eq.sql
    assert ">= CAST(:agf_qv0_0 AS date)" in eq.sql
    assert "<= CAST(:agf_qv0_0_1 AS date)" in eq.sql
    assert "left(" not in eq.sql
    # The same two clauses as the two range leaves, by bound names only.
    gte, lte = both.sql.split(" AND ", 1)
    assert eq.sql == "(" + gte + " AND " + lte.replace("agf_qk1_0", "agf_qk0_0").replace(
        "agf_qv1_0", "agf_qv0_0_1") + ")"
    assert eq.params["agf_qv0_0"] == eq.params["agf_qv0_0_1"] == both.params["agf_qv0_0"]
    # A date field keeps its stored day: a date has no time.
    date_eq = query_sql(_SCOPE, ((QueryLeaf("Invoice_Date", "eq", "2026-11-01"),),), zone=_ZONE)
    assert "left(" in date_eq.sql


def test_a_lowered_datetime_eq_keeps_its_old_clause() -> None:
    old = module_where(_SCOPE, "Invoices", [("Paid_On", "2026-11-01")], zone=_ZONE)
    new = query_sql(_SCOPE, ((lower_pair("Paid_On", "2026-11-01"),),), zone=_ZONE)
    assert _normal(new.sql, new.params)[0] == old.sql[len(" WHERE module = :agf_module AND "):]
    assert "left(" in new.sql


def test_the_datetime_eq_switch_is_one_place(monkeypatch) -> None:
    """E: the other rule changes in ONE constant, and the native leaf then
    builds the old clause."""
    monkeypatch.setattr(field_sql, "DATETIME_EQ", "stored_prefix")
    native = query_sql(_SCOPE, ((QueryLeaf("Paid_On", "eq", "2026-11-01"),),), zone=_ZONE)
    lowered = query_sql(_SCOPE, ((lower_pair("Paid_On", "2026-11-01"),),), zone=_ZONE)
    assert native == lowered
    assert query_ranges(_SCOPE, ((QueryLeaf("Paid_On", "eq", "2026-11-01"),),)) == []


def test_a_native_datetime_eq_is_a_range_for_the_day_field() -> None:
    nodes = ((QueryLeaf("Paid_On", "eq", "2026-11-01"),),)
    assert query_ranges(_SCOPE, nodes) == [("Paid_On.from", "2026-11-01"),
                                           ("Paid_On.to", "2026-11-01")]
    assert query_ranges(_SCOPE, ((lower_pair("Paid_On", "2026-11-01"),),)) == []


# ── 8. SEM-1b-2 review fixes ─────────────────────────────────────────────────


def test_a_native_range_on_a_text_field_names_the_eq_leaf() -> None:
    """P3: a native leaf's range 422 names the ``eq`` leaf. The old
    parameter keeps ``field.<key>=<value>``."""
    with pytest.raises(HTTPException) as caught:
        query_sql(_SCOPE, ((QueryLeaf("Subject", "gte", "a"),),), zone=_ZONE)
    message = caught.value.detail["message"]
    assert message.endswith(
        'To match one value, give {"field": "Subject", "op": "eq", "value": <value>}.')
    assert "field.Subject" not in message
    with pytest.raises(HTTPException) as caught:
        module_where(_SCOPE, "Invoices", [("Subject.from", "a")], zone=_ZONE)
    assert caught.value.detail["message"].endswith(
        "To match one value, give field.Subject=<value>.")


def test_the_admission_refusal_names_the_eq_leaf_of_a_query_key() -> None:
    found = field_sql.TextNumbers(numbers=0, no_value=1, not_numbers=2)
    native = field_sql._text_refusal(_SCOPE, "Subject", found, False, True)
    old = field_sql._text_refusal(_SCOPE, "Subject", found, False)
    assert native.detail["message"].endswith(
        'give {"field": "Subject", "op": "eq", "value": <value>}.')
    assert old.detail["message"].endswith("give field.Subject=<value>.")
    tree = ast.parse((_CRM / "aggregate.py").read_text(encoding="utf-8"))
    admit = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Name) and n.func.id == "admit_text_numbers"]
    assert [k.arg for k in admit[0].keywords] == ["measured", "queried"]


def test_query_ranges_resolve_a_key_as_the_sql_does() -> None:
    """The ranges resolve each key through ``FieldScope.ref``, the call of
    ``query_sql``: a summed key (``A+B``) that ``fields.get`` does not hold
    still gives its range, and an unknown key gives no 422 here."""
    money = {"Net": FieldRef("Net", "currency", "Net"),
             "Tax": FieldRef("Tax", "currency", "Tax")}
    scope = FieldScope(column=field_sql.MODULE_COLUMN, owner="module:Invoices",
                       fields={**_FIELDS, **money})
    assert "Net+Tax" not in scope.fields
    nodes = ((QueryLeaf("Net+Tax", "gte", 5),),)
    assert query_ranges(scope, nodes) == [("Net+Tax.from", "5")]
    assert " >= CAST(:agf_qv0_0 AS numeric)" in query_sql(scope, nodes, zone=_ZONE).sql
    assert query_ranges(scope, ((QueryLeaf("Nope", "gte", 5),),)) == [("Nope.from", "5")]
    tree = ast.parse((_CRM / "field_sql.py").read_text(encoding="utf-8"))
    assert "_leaf_ref" in _calls(tree, "query_ranges")
    assert "fields" not in {n.attr for n in ast.walk(next(
        f for f in ast.walk(tree) if isinstance(f, ast.FunctionDef) and f.name == "query_ranges"))
        if isinstance(n, ast.Attribute)}


def test_a_request_cannot_set_lowered() -> None:
    """``lowered`` is never read from the request: a leaf with that key is
    refused by the shape check, and a parsed leaf is never lowered."""
    detail = _refused(_q({"field": "Paid_On", "op": "eq", "value": "2026-11-01",
                          "lowered": True}))
    assert detail["code"] == VALUE_INVALID
    nodes = parse_query(_q(_leaf("Paid_On", "eq", "2026-11-01"),
                           {"any": [_leaf("Paid_On", "eq", "2026-11-01"),
                                    _leaf("Paid_On", "in", ["2026-11-02"])]}))
    assert all(not leaf.lowered for node in nodes for leaf in node)
