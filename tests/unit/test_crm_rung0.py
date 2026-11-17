"""CRM Rung 0 (task 3690): the module list and the module aggregate share
ONE filter, and ONE name matcher. Database-free, so it never skips.

Spec: ``project-docs/specs/crm_platform.md`` §18, "CRM Rung 0 (task 3690)".
The behaviour is proved in ``test_crm_rung0_r8.py``.

The fence: ``aggregate._read`` and ``modules.list_module_records`` both call
``field_sql.module_filter``. ``module_filter`` calls ``module_where``, and
``module_where`` calls ``name_clause``. ``overlay_where`` calls
``module_where`` and holds no matcher of its own. ``name_clause`` calls
``core.search_where``, the one search predicate of ``?q=``.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from fastapi import HTTPException

_CRM = (
    Path(__file__).resolve().parents[2]
    / "apps" / "services" / "gateway" / "gateway" / "routes" / "crm"
)

#: function → the calls it must make, by file.
_PINS: dict[str, dict[str, set[str]]] = {
    "aggregate.py": {"_read": {"module_filter"}},
    "modules.py": {"list_module_records": {"module_filter"}},
    "field_sql.py": {
        "module_filter": {"module_where"},
        "module_where": {"name_clause"},
        "overlay_where": {"module_where"},
        "name_clause": {"search_where"},
    },
}


def _calls(source: str, name: str) -> set[str] | None:
    """The plain-name calls of the function *name* of *source*, or None."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == name:
            return {
                n.func.id for n in ast.walk(node)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            }
    return None


def _strings(source: str) -> list[str]:
    """Each string constant of *source* that is no docstring."""
    tree = ast.parse(source)
    docs = {
        id(n.body[0].value) for n in ast.walk(tree)
        if isinstance(n, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
        and n.body and isinstance(n.body[0], ast.Expr)
        and isinstance(n.body[0].value, ast.Constant)
    }
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs]


def _offenders(sources: dict[str, str]) -> list[str]:
    found = []
    for file, pins in _PINS.items():
        for function, wanted in pins.items():
            calls = _calls(sources[file], function)
            if calls is None:
                found.append(f"{file}: no function {function}")
                continue
            found.extend(f"{file}: {function} does not call {w}" for w in wanted - calls)
    # A second matcher: an ILIKE in the field SQL, or in the module list.
    for file in ("field_sql.py", "modules.py"):
        found.extend(f"{file}: ILIKE in {s[:40]!r}"
                     for s in _strings(sources[file]) if "ILIKE" in s.upper())
    return found


def _sources() -> dict[str, str]:
    return {f: (_CRM / f).read_text(encoding="utf-8") for f in _PINS}


def test_the_list_and_the_aggregate_share_one_filter() -> None:
    assert _offenders(_sources()) == []


def test_the_fence_sees_a_second_path() -> None:
    """The fence on the fence: each kind of drift turns it red."""
    sources = _sources()
    good = _offenders(sources)
    assert good == []
    drift = dict(sources)
    drift["modules.py"] = sources["modules.py"].replace("module_filter(", "other_filter(")
    assert "modules.py: list_module_records does not call module_filter" in _offenders(drift)
    drift = dict(sources)
    drift["field_sql.py"] = sources["field_sql.py"] + "\nX = 'name ILIKE :agf_q'\n"
    assert any("ILIKE" in o for o in _offenders(drift))
    drift = dict(sources)
    drift["field_sql.py"] = sources["field_sql.py"].replace(
        "named, named_params = name_clause(q)", "named, named_params = None, {}")
    assert "field_sql.py: module_where does not call name_clause" in _offenders(drift)


# ── The pure parts ───────────────────────────────────────────────────────────


def _scope():
    from gateway.routes.crm import field_sql as fs

    def ref(key: str, kind: str, label: str | None = None) -> fs.FieldRef:
        return fs.FieldRef(key=key, type=kind, label=label or key)

    return fs.FieldScope(column="fields", owner="module:Clients", fields={
        "Fee": ref("Fee", "currency", "Annual Fee"), "Qty": ref("Qty", "number", "Quantity"),
        "Note": ref("Note", "text"), "Start": ref("Start", "date"),
        "Flag": ref("Flag", "boolean"),
        "Long": ref("Long", "text", "L'" + "x" * 200),
    })


def test_a_number_range_reads_the_guarded_number() -> None:
    from gateway.routes.crm import field_sql as fs

    where = fs.module_where(_scope(), "Clients", [("Fee.from", "1"), ("Qty.to", " 2.50 ")],
                            zone="UTC")
    assert ">= CAST(:agf_v0 AS numeric)" in where.sql
    assert "<= CAST(:agf_v1 AS numeric)" in where.sql
    # The number is the guarded one of the measure: a plain decimal or a JSON number.
    assert where.sql.count("jsonb_typeof") == 4
    assert (where.params["agf_v0"], where.params["agf_v1"]) == ("1", "2.50")
    assert (where.params["agf_k0_0"], where.params["agf_k1_0"]) == ("Fee", "Qty")
    assert where.source == fs.DETOAST_JOIN


@pytest.mark.parametrize(("name", "value", "words"), [
    ("Note.from", "1", "A range takes a date or a number field."),
    ("Flag.to", "1", "The date and number fields of module:Clients are: Fee, Qty, Start."),
    ("Fee.from", "abc", "'field.Fee.from' must be a number."),
    ("Fee.to", "1e1000000", "'field.Fee.to' must be a number of at most 60 digits"),
    ("Fee.from", "NaN", "'field.Fee.from' must be a number of at most"),
])
def test_an_unfit_range_is_a_422(name: str, value: str, words: str) -> None:
    from gateway.routes.crm import field_sql as fs

    with pytest.raises(HTTPException) as exc:
        fs.module_where(_scope(), "Clients", [(name, value)], zone="UTC")
    assert words in exc.value.detail["message"]


def test_the_unknown_field_names_the_close_fields() -> None:
    scope = _scope()
    for asked, close in (("Fees", "'Fee' (label 'Annual Fee')"),
                         ("quantity", "'Qty' (label 'Quantity')")):
        with pytest.raises(HTTPException) as exc:
            scope.ref(asked)
        detail = exc.value.detail
        assert detail["key"] == asked
        assert f"The closest fields of module:Clients are: {close}." in detail["message"]
    with pytest.raises(HTTPException) as exc:
        scope.ref("Xyzzy_Plugh")
    assert "closest" not in exc.value.detail["message"]
    # A label is cut to 60 characters, and its own quote is dropped.
    assert scope.closest("Long").startswith("'Long' (label 'L" + "x" * 59 + "')")
    # At most three, and a long name reads no match at all.
    assert len(scope.closest("F").split(", ")) <= 3
    assert scope.closest("x" * 101) == ""


def test_the_quote_of_org_text_is_cut_and_closed() -> None:
    from gateway.routes.crm import field_sql as fs

    quoted = fs._quoted("It's\n" + "y" * 100)
    assert quoted.startswith("'Its y") and quoted.endswith("'")
    assert len(quoted) == fs.HINT_CHARS + 2


@pytest.mark.parametrize("q", ["x" * 10_000, "a\x00b"])
def test_name_clause_refuses_a_long_q_and_a_nul(q: str) -> None:
    from gateway.routes.crm import field_sql as fs

    with pytest.raises(HTTPException) as exc:
        fs.name_clause(q)
    assert exc.value.status_code == 422


def test_name_clause_is_the_list_search() -> None:
    from gateway.routes.crm import field_sql as fs
    from gateway.routes.crm.core import search_where
    from gateway.routes.crm.modules import MODULE_RECORDS

    for q in ("acme", "ACME corp", "50%", "O'Brien", "_"):
        assert fs.name_clause(q) == search_where(MODULE_RECORDS, q)
    assert fs.name_clause("") == (None, {}) and fs.name_clause("  ") == (None, {})
    assert fs.name_clause(None) == (None, {})


async def test_the_hint_passes_each_other_refusal_through() -> None:
    from gateway.routes.crm import field_sql as fs

    class _NoDb:
        async def execute(self, *_a, **_k):
            raise AssertionError("no read for another refusal")

    for exc in (HTTPException(status_code=422, detail="plain"),
                HTTPException(status_code=404, detail={"code": "unknown_module"}),
                HTTPException(status_code=422, detail={"code": "unfit_field", "message": "m"}),
                HTTPException(status_code=422, detail={"code": "unknown_field", "key": "",
                                                       "message": "m"})):
        assert await fs.field_hint(_NoDb(), "Clients", exc) is exc


def test_a_module_aggregate_takes_q() -> None:
    from gateway.routes.crm import aggregate as agg

    assert "q" in agg.MODULE_PARAMS
