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
