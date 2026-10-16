"""The CRM aggregate routes have ONE filter builder (WS-53 CRM-C7, D10).

Spec: ``project-docs/specs/crm_platform.md`` §20.10 item 2. The aggregate
takes the filters of the list routes. A second copy of them would drift, and
the totals of an agent would then disagree with the list on the screen. So
``aggregate.py`` must build its WHERE clause through ``records.list_query``
and nothing else. Database-free, so it never skips. The behaviour is proved
in ``test_crm_aggregate_r8.py``.
"""

from __future__ import annotations

import ast
from pathlib import Path

_CRM = (
    Path(__file__).resolve().parents[2]
    / "apps" / "services" / "gateway" / "gateway" / "routes" / "crm"
)

#: Text that only a copy of the list filters would hold.
_FILTER_TEXT = (
    "created_from", "updated_from", "close_from", "custom ->>",
    "lower(owner_email) = :owner", "OPEN_LEAD_SQL", "company_id = CAST",
)

#: The builders a second filter path would call.
_BUILDERS = frozenset({
    "extra_filters", "list_contract", "related_filters", "ExtraFilterInput",
})


def _offenders(source: str) -> list[str]:
    found = [f"text {t!r}" for t in _FILTER_TEXT if t in source]
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in _BUILDERS:
            found.append(f"name {node.id}")
        if isinstance(node, ast.alias) and node.name in _BUILDERS:
            found.append(f"import {node.name}")
    calls = {
        node.func.id for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    if "list_query" not in calls:
        found.append("no call of list_query")
    return found


def test_the_aggregate_reuses_the_list_filter_builder() -> None:
    source = (_CRM / "aggregate.py").read_text(encoding="utf-8")
    assert _offenders(source) == []


def test_the_fence_sees_a_second_copy() -> None:
    """The fence on the fence: each kind of copy turns it red."""
    good = "from x import list_query\nasync def f():\n    q = await list_query(1)\n"
    assert _offenders(good) == []
    assert _offenders(good + "W = 'created_at >= CAST(:created_from AS date)'\n")
    assert _offenders(good + "from gateway.routes.crm.core import extra_filters\n")
    assert _offenders("def f():\n    return 1\n") == ["no call of list_query"]


# ── The field SQL has one home (WS-53 CRM-Q25, crm_platform.md §18.11) ──────

#: Text that only field SQL holds: a jsonb operator, a jsonb function, or the
#: guarded cast of a field value.
_FIELD_SQL_TEXT = ("->", "jsonb", "AS numeric")


def _field_sql_offenders(source: str) -> list[str]:
    """Each string constant of *source*, docstrings left out, that holds field
    SQL. Field SQL belongs in ``field_sql.py``, never in ``aggregate.py``."""
    tree = ast.parse(source)
    docstrings = {
        id(node.body[0].value) for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef
                      | ast.ClassDef)
        and node.body and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    found = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in docstrings):
            found.extend(
                f"text {t!r} in {node.value[:40]!r}"
                for t in _FIELD_SQL_TEXT if t in node.value
            )
    imports = {
        node.module for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    if "gateway.routes.crm.field_sql" not in imports:
        found.append("no import of gateway.routes.crm.field_sql")
    return found


def test_the_aggregate_holds_no_field_sql() -> None:
    """Done-when 5: the field SQL lives in ``field_sql.py``, and the
    aggregate reaches it through that module only."""
    source = (_CRM / "aggregate.py").read_text(encoding="utf-8")
    assert _field_sql_offenders(source) == []
    assert (_CRM / "field_sql.py").is_file()


def test_the_field_sql_fence_sees_a_copy() -> None:
    """The fence on the fence: each kind of field SQL turns it red."""
    good = '"""Doc -> it."""\nfrom gateway.routes.crm.field_sql import x\n'
    assert _field_sql_offenders(good) == []
    assert _field_sql_offenders(good + "S = 'fields ->> :k'\n")
    assert _field_sql_offenders(good + "S = f\"jsonb_typeof({c})\"\n")
    assert _field_sql_offenders(good + "S = 'CAST(v AS numeric)'\n")
    assert _field_sql_offenders("X = 1\n") == [
        "no import of gateway.routes.crm.field_sql",
    ]


#: A name and a value that try to inject SQL. Each one must reach the
#: statement as a bound parameter and never as text.
_HOSTILE_KEY = "x') OR 1=1; DROP TABLE crm_deals; --"
_HOSTILE_VALUE = "v' OR '1'='1"


def test_the_field_sql_binds_every_name_and_value() -> None:
    """Done-when 5: even a key that passed the check never enters the SQL
    text. The builders bind it, for the measure, the group and each filter."""
    from gateway.routes.crm import field_sql as fs

    def ref(key: str, kind: str) -> fs.FieldRef:
        return fs.FieldRef(key=key, type=kind, label=key, options={})

    pieces: list[tuple[str, dict]] = []
    for kind in ("number", "currency"):
        scope = fs.FieldScope(column="fields", owner="module:X",
                              fields={_HOSTILE_KEY: ref(_HOSTILE_KEY, kind)})
        for measure in fs.FIELD_MEASURES:
            sql = fs.measure_sql(scope, scope.fields[_HOSTILE_KEY], measure)
            pieces.append((f"{sql.value} {sql.sum} {sql.count}", sql.params))
    for kind, bucket in (("select", None), ("boolean", None), ("lookup", None),
                         ("date", "month"), ("datetime", "quarter"),
                         ("date", "year")):
        scope = fs.FieldScope(column="custom", owner="deals",
                              fields={_HOSTILE_KEY: ref(_HOSTILE_KEY, kind)})
        sql = fs.group_sql(scope, scope.fields[_HOSTILE_KEY], bucket)
        pieces.append((f"{sql.key} {sql.label}", sql.params))
    for kind, pairs in (
        ("select", [(_HOSTILE_KEY, _HOSTILE_VALUE)]),
        ("lookup", [(_HOSTILE_KEY, _HOSTILE_VALUE)]),
        ("multiselect", [(_HOSTILE_KEY, _HOSTILE_VALUE)]),
        ("number", [(_HOSTILE_KEY, "12.5")]),
        ("date", [(_HOSTILE_KEY + ".from", "2026-01-01"),
                  (_HOSTILE_KEY + ".to", "2026-12-31")]),
    ):
        scope = fs.FieldScope(column="fields", owner="module:X",
                              fields={_HOSTILE_KEY: ref(_HOSTILE_KEY, kind)})
        where = fs.module_where(scope, _HOSTILE_VALUE, pairs)
        pieces.append((where.sql, where.params))
    for sql, params in pieces:
        assert _HOSTILE_KEY not in sql and _HOSTILE_VALUE not in sql, sql
        assert "x')" not in sql and "--" not in sql, sql
        assert _HOSTILE_KEY in params.values(), params


def test_the_field_scope_takes_only_its_two_columns() -> None:
    """The jsonb column is a literal of ``field_sql``, never caller text."""
    import pytest
    from gateway.routes.crm import field_sql as fs

    for column in ("custom", "fields"):
        fs.FieldScope(column=column, owner="deals", fields={})
    with pytest.raises(ValueError):
        fs.FieldScope(column="name; DROP TABLE x", owner="deals", fields={})


def test_the_list_routes_use_the_same_builder() -> None:
    """``records._list`` builds its query through ``list_query`` too."""
    tree = ast.parse((_CRM / "records.py").read_text(encoding="utf-8"))
    body = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "_list"
    )
    calls = {
        n.func.id for n in ast.walk(body)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert "list_query" in calls
    assert "extra_filters" not in calls and "list_contract" not in calls
