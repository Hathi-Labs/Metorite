"""R8 — a NUL byte in a CRM query string answers 422, never 500 (WS-53 CRM-Q25c).

Spec: ``project-docs/specs/crm_platform.md`` §18.11.2, CRM-Q25c done-when 1
and 2.

``GET /crm/deals?q=%00`` raised ``CharacterNotInRepertoireError`` from
asyncpg, and the route answered 500. Postgres text holds no NUL. One guard,
``records.refuse_nul``, now refuses a NUL in a query name or value before
any SQL runs, on each CRM read that takes a query string: the four lists,
the export, the aggregate, the board (``GET /crm/pipeline``, fix round of
2026-10-17), the module list and ``search_records``.

Two halves.

1. Database-free, so it never skips: the guard has one implementation, and
   each of those reads calls it.
2. R8: through the real ``/crm`` router on a real Postgres, each route
   answers 422 for ``q=%00``, for a NUL in a filter value and for a NUL in a
   name. The same request with no NUL answers 200, so the 422 comes from
   the guard. A hermetic fake agrees with any value it is handed, so the
   500 that this suite prevents is real only on the database.
"""

# ruff: noqa: F811 - the fixtures come in by import and are used as arguments
from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import pytest
from fastapi import HTTPException

pytest.importorskip("sqlalchemy")

from tests.unit.test_crm_aggregate_fields_r8 import (  # noqa: F401
    _admin,
    _call,
    _mirror_org,
    _module,
    ns,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

_CRM = (
    Path(__file__).resolve().parents[2]
    / "apps" / "services" / "gateway" / "gateway" / "routes" / "crm"
)

#: ``(file, class or None, function)`` of each read that must call the guard.
_GUARDED = (
    ("records.py", "ListParams", "__init__"),
    ("records.py", None, "search_records"),
    ("export.py", "ExportParams", "__init__"),
    ("pipeline.py", None, "get_pipeline"),
    ("modules.py", None, "list_module_records"),
)


# ── 1. Database-free ─────────────────────────────────────────────────────────


def _function(tree: ast.Module, owner: str | None, name: str) -> ast.AST:
    scope: list[ast.stmt] = tree.body
    if owner is not None:
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == owner)
        scope = cls.body
    return next(
        n for n in scope
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef) and n.name == name
    )


def _calls(node: ast.AST) -> set[str]:
    return {
        n.func.id for n in ast.walk(node)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }


@pytest.mark.parametrize(("file", "owner", "name"), _GUARDED)
def test_each_read_with_a_query_string_calls_the_guard(file, owner, name) -> None:
    tree = ast.parse((_CRM / file).read_text(encoding="utf-8"))
    assert "refuse_nul" in _calls(_function(tree, owner, name)), (file, owner, name)


def test_the_aggregate_takes_the_guard_from_its_list_params() -> None:
    """Fix round of 2026-10-17. The aggregate takes ``ListParams``, which
    calls the guard. So the route calls it no second time."""
    import typing

    from gateway.routes.crm.aggregate import aggregate
    from gateway.routes.crm.records import ListParams

    tree = ast.parse((_CRM / "aggregate.py").read_text(encoding="utf-8"))
    assert "refuse_nul" not in _calls(_function(tree, None, "aggregate"))
    # `Depends()` with no argument takes the class of the annotation.
    assert typing.get_type_hints(aggregate)["params"] is ListParams


def test_the_guard_has_one_implementation() -> None:
    """Done-when 1: one guard, not a second copy. Only ``records.py`` looks
    for a NUL, and no module of the package defines a second guard."""
    holders = sorted(
        path.name for path in _CRM.glob("*.py")
        if '"\\x00"' in path.read_text(encoding="utf-8")
    )
    assert holders == ["records.py"], holders
    for path in _CRM.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = {
            n.name for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
        }
        if path.name != "records.py":
            copies = {n for n in names if re.search(r"(^|_)nul($|_)", n.lower())}
            assert not copies, (path.name, copies)


def _request(query: str) -> Any:
    from starlette.requests import Request

    return Request({"type": "http", "method": "GET", "path": "/crm/deals",
                    "query_string": query.encode("latin-1"), "headers": []})


@pytest.mark.parametrize("query", ["q=%00", "owner=a%00b", "custom.tier=g%00", "q%00x=1"])
def test_the_param_classes_refuse_a_nul(query: str) -> None:
    from gateway.routes.crm.export import ExportParams
    from gateway.routes.crm.records import NUL_DETAIL, ListParams

    for build in (
        lambda: ListParams(direction="desc", page=1, page_size=50, request=_request(query)),
        lambda: ExportParams(direction="desc", request=_request(query)),
    ):
        with pytest.raises(HTTPException) as caught:
            build()
        assert (caught.value.status_code, caught.value.detail) == (422, NUL_DETAIL)
    # The same classes take a query with no NUL.
    ListParams(direction="desc", page=1, page_size=50, request=_request("q=widget"))


async def test_the_search_refuses_a_nul_before_any_session() -> None:
    """The command bar's search is no route. Its caller leaves an
    ``HTTPException`` out of the answer, so a 422 here leaves the CRM group
    out, where a database error did the same with a warning."""
    from gateway.routes.crm.records import search_records

    with pytest.raises(HTTPException) as caught:
        await search_records(_admin("00000000-0000-0000-0000-000000000001"), "ab\x00", 5)
    assert caught.value.status_code == 422


# ── 2. R8, through the real router ───────────────────────────────────────────


async def _status(p: Any, path: str, query: dict[str, str]) -> int:
    res = await _call(p, _admin(p.org_a), "GET", f"{path}?{urlencode(query)}")
    assert res.status_code != 500, (path, query, res.text)
    return res.status_code


#: ``(path, a query that answers 200)``. Each one also gets a NUL below.
_ROUTES = (
    ("/crm/leads", {"q": "widget"}),
    ("/crm/deals", {"q": "widget"}),
    ("/crm/contacts", {"q": "widget"}),
    ("/crm/organizations", {"q": "widget"}),
    ("/crm/export/leads.csv", {"q": "widget"}),
    ("/crm/export/deals.csv", {"q": "widget"}),
    ("/crm/export/contacts.csv", {"q": "widget"}),
    ("/crm/export/organizations.csv", {"q": "widget"}),
    ("/crm/aggregate", {"entity": "deals", "group_by": "owner", "q": "widget"}),
    ("/crm/modules/Products/records", {"q": "widget"}),
)


@pytest.fixture
def mirrored(ns):
    _mirror_org(ns, ns.org_a, [_module("Products")])
    return ns


@_DB_GATE
@pytest.mark.parametrize(("path", "good"), _ROUTES)
async def test_a_nul_in_a_query_is_a_422(mirrored, app_engine, path, good) -> None:
    p = mirrored
    assert await _status(p, path, good) == 200
    # q=%00, the shape the verifier of CRM-Q25 found.
    assert await _status(p, path, {**good, "q": "\x00"}) == 422
    # A NUL in another value, and a NUL in a name.
    assert await _status(p, path, {**good, "sort": "name\x00"}) == 422
    assert await _status(p, path, {**good, "q\x00x": "1"}) == 422


@_DB_GATE
@pytest.mark.parametrize("path", ["/crm/deals", "/crm/export/deals.csv"])
async def test_a_nul_in_a_list_filter_value_is_a_422(mirrored, app_engine, path) -> None:
    p = mirrored
    for name in ("owner", "source", "status_id", "created_from", "custom.tier"):
        assert await _status(p, path, {name: "a\x00b"}) == 422, name


@_DB_GATE
async def test_a_nul_on_the_board_is_a_422(mirrored, app_engine) -> None:
    """Fix round of 2026-10-17. ``GET /crm/pipeline`` takes ``owner``,
    ``source`` and ``custom.<key>``, and bound each one to SQL with no guard."""
    p = mirrored
    assert await _status(p, "/crm/pipeline", {"owner": "a@x.test"}) == 200
    for name in ("owner", "source", "custom.tier", "updated_from"):
        assert await _status(p, "/crm/pipeline", {name: "a\x00b"}) == 422, name
    assert await _status(p, "/crm/pipeline", {"q\x00x": "1"}) == 422
