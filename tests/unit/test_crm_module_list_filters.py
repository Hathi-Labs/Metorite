"""CRM-Q34-4: one filter builder for a module list and a module total.

Hermetic half (R7). The R8 half, against a real Postgres, is
``test_crm_module_list_filters_r8.py``. Spec: ``project-docs/specs/crm_platform.md``
§18.17, the row CRM-Q34-4.

1. The list route builds ``field.<key>`` through ``field_sql.module_field_where``,
   which is ``text_matches``, ``lookup_ids`` and ``module_where``: the same three
   calls as ``aggregate._read``. A second builder is a defect.
2. An org off ``CRM_MODULE_FIND_ORGS`` gets a 422 for ``field.<key>`` on a list,
   never a list that dropped the filter.
3. The tool sends ``field.<key>`` and shows at most 5 keys on each row. With no
   new argument its request is the request of before.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytest.importorskip("agent_framework", reason="MAF not installed")

import acb_skills.crm_tools as _T

from tests.unit.test_crm_agent import _fake_gateway

_ROOT = Path(__file__).resolve().parents[2]
_CRM = _ROOT / "apps/services/gateway/gateway/routes/crm"
_ID = "11111111-1111-4111-8111-111111111111"


def _calls_of(path: Path, function: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    node = next(n for n in ast.walk(tree)
                if isinstance(n, ast.AsyncFunctionDef | ast.FunctionDef) and n.name == function)
    return {c.func.id for c in ast.walk(node)
            if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}


def test_the_list_builds_its_field_filters_with_the_calls_of_the_total() -> None:
    """The fence of 'one builder': the list helper and the total read call
    the same three functions, and the list route reaches the helper."""
    helper = _calls_of(_CRM / "field_sql.py", "module_field_where")
    assert {"text_matches", "lookup_ids", "module_where"} <= helper
    total = _calls_of(_CRM / "aggregate.py", "_read")
    assert {"text_matches", "lookup_ids", "module_where"} <= total
    route = _calls_of(_CRM / "modules.py", "list_module_records")
    assert "module_field_where" in route and "field_filter_refusal" in route


def test_the_list_route_holds_no_second_filter_builder() -> None:
    source = (_CRM / "modules.py").read_text(encoding="utf-8")
    for second in ("def _field_clause", "def _field_sql", "_filter_value("):
        assert second not in source, second


def test_the_refusal_names_a_working_path() -> None:
    from gateway.routes.crm.field_sql import field_filter_refusal

    refusal = field_filter_refusal()
    assert refusal.status_code == 422
    text = str(refusal.detail)
    assert "crm_aggregate" in text and "field_filters" in text


async def test_no_new_argument_sends_the_request_of_before(monkeypatch) -> None:
    calls = _fake_gateway(monkeypatch, lambda _c: {"rows": [], "total": 0, "field_defs": []})
    await _T.crm_list_module_records("Products", q="widget", page=2, page_size=10)
    sent = [c for c in calls if c["path"] == "/crm/modules/Products/records"]
    assert sent[0]["params"] == {"q": "widget", "page": 2, "page_size": 10}


async def test_field_filters_go_out_as_field_keys(monkeypatch) -> None:
    calls = _fake_gateway(monkeypatch, lambda _c: {"rows": [], "total": 0, "field_defs": []})
    await _T.crm_list_module_records(
        "Products", field_filters={"Customer": "Acme Ltd", "Amount.from": "100"})
    params = next(c["params"] for c in calls if c["path"] == "/crm/modules/Products/records")
    assert params["field.Customer"] == "Acme Ltd" and params["field.Amount.from"] == "100"
    assert "custom.Customer" not in params


def _rows(_call: dict) -> dict:
    return {
        "rows": [{"id": _ID, "name": "INV-1", "fields": {
            "Customer": {"id": "z1", "name": "Acme Ltd"}, "Amount": 100, "Notes": "x" * 200,
            "Status": "paid"}}],
        "total": 1,
        "field_defs": [{"key": "Customer", "type": "lookup"}, {"key": "Amount", "type": "currency"},
                       {"key": "Notes", "type": "text"}, {"key": "Status", "type": "select"}],
    }


async def test_fields_show_each_value_under_its_key_cut_at_80(monkeypatch) -> None:
    _fake_gateway(monkeypatch, _rows)
    out = await _T.crm_list_module_records("Invoices", fields=["Customer", "Amount", "Notes"])
    line = next(ln for ln in out.splitlines() if ln.startswith("- INV-1"))
    assert "Customer: Acme Ltd" in line and "Amount: 100" in line
    assert "Notes: " + "x" * 80 in line and "x" * 81 not in line
    assert "Status" not in line


async def test_no_fields_argument_prints_the_row_of_before(monkeypatch) -> None:
    _fake_gateway(monkeypatch, _rows)
    out = await _T.crm_list_module_records("Invoices")
    assert f"- INV-1 (id={_ID})" in out


async def test_an_unknown_key_is_named_not_dropped(monkeypatch) -> None:
    _fake_gateway(monkeypatch, _rows)
    out = await _T.crm_list_module_records("Invoices", fields=["Nope"])
    assert "Unknown field key 'Nope'" in out


@pytest.mark.parametrize("fields", [[], ["a", "b", "c", "d", "e", "f"], ["a", "a"], ["a", " "]])
async def test_fields_takes_one_to_five_distinct_keys(monkeypatch, fields) -> None:
    calls = _fake_gateway(monkeypatch, _rows)
    out = await _T.crm_list_module_records("Invoices", fields=fields)
    assert "fields takes 1 to 5 distinct field keys" in out
    assert calls == []
