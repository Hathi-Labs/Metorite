"""SBX-F18 (SBX-1a, task 3800): the compose check of the no-match path.

Spec: ``project-docs/specs/agent_sandbox.md`` §6.1 and §12.1 decision 7
(done-when 6 and 9). Code: ``gateway/studio/package_search.callable_catalog``
and ``orchestrator/tool_gap_path.py`` steps c and f.

What this proves:

1. ``callable_catalog`` keeps a ``manifest`` row only when the member holds
   the feature of its app, and a ``logic`` row only with ``studio``. It
   drops each ``workflow``, ``integration``, ``mcp``, ``tool`` and
   ``package`` row, and a row of an unknown source (fail closed).
2. A catalog row that the member cannot call never reaches the decide
   context, the row or the answer.
3. A need that two callable rows cover, with the pick ``compose``, gives a
   ``compose`` row, never ``no_match``. With no callable row, the question
   never offers ``compose``.
4. No session of the hook is open while ``catalog_rows`` runs, and none is
   open during the decide call.
5. The check keeps 20 rows at most, and never calls ``package_rows``.

Mutations (R7), each red here: map ``compose`` to ``no_match``; keep a
``workflow`` row in ``callable_catalog``; open the library session before
``catalog_rows``.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

pytest.importorskip("agent_framework", reason="MAF not installed")

from gateway.studio import package_search as ps
from orchestrator import tool_gap_path as tgp

from tests.unit import _tool_gap_fakes as fk

KNOW = fk.knowledge_row("search", "Search the knowledge notes")
KNOW_READ = fk.knowledge_row("read", "Read the knowledge notes of a supplier")
LOGIC = {"id": "logic:price_list", "title": "Price list of a supplier", "source": "logic",
         "class": "B", "app": None}
OTHERS = [
    {"id": "workflow:00000000-0000-4000-8000-000000000001", "title": "Supplier price list",
     "source": "workflow", "app": None},
    {"id": "integration:zoho.price_list", "title": "Supplier price list",
     "source": "integration", "app": None},
    {"id": "mcp:notion.search_notes", "title": "Search the knowledge notes", "source": "mcp",
     "app": None},
    {"id": "tool:search_company", "title": "Search the knowledge notes", "source": "tool",
     "app": None},
    {"id": "object:notes.list", "title": "List notes", "source": "package",
     "app": "00000000-0000-4000-8000-000000000002"},
    {"id": "odd.row", "title": "Supplier price list", "source": "odd", "app": None},
]


# ── 1. The filter ──────────────────────────────────────────────────────────


def test_a_manifest_row_needs_the_feature_of_its_app() -> None:
    assert ps.callable_catalog({"knowledge"}, [KNOW]) == [KNOW]
    assert ps.callable_catalog({"studio", "projects"}, [KNOW]) == []
    unknown = {**KNOW, "id": "nope.read", "app": "nope"}
    assert ps.callable_catalog(fk.holder_features(), [unknown]) == []


def test_a_logic_row_needs_studio() -> None:
    assert ps.callable_catalog({"studio"}, [LOGIC]) == [LOGIC]
    assert ps.callable_catalog({"knowledge"}, [LOGIC]) == []


def test_every_other_source_is_dropped_for_every_member() -> None:
    """Fail closed. Mutation: keep a ``workflow`` row -> red."""
    assert ps.callable_catalog(fk.holder_features(), OTHERS) == []
    assert frozenset({"manifest", "logic"}) == ps.CALLABLE_SOURCES


@pytest.mark.parametrize("features", [None, frozenset(), []])
def test_no_known_member_keeps_no_row(features: Any) -> None:
    assert ps.callable_catalog(features, [KNOW, LOGIC]) == []


# ── 2. A row that the member cannot call never reaches the model ──────────


async def test_a_row_of_a_feature_the_member_lacks_never_reaches_the_context(
        monkeypatch) -> None:
    """Done-when 9. The member holds ``studio`` but not ``knowledge``."""
    world = fk.install(monkeypatch, features=frozenset({"studio", "chat"}),
                       rows=[KNOW, KNOW_READ, LOGIC, *OTHERS],
                       picks={"answer": ("compose", 0.95), "category": ("documents", 0.9)})
    with fk.in_run():
        told = await tgp.gap_hook(fk.NEED, [])
    [(context, items, _purpose)] = world.asks
    seen = json.dumps({"context": context, "options": [list(i.options) for i in items],
                       "row": world.records, "told": told})
    for row in (KNOW, KNOW_READ, *OTHERS):
        assert row["id"] not in seen, row["id"]
    assert json.loads(context)["catalog"] == ["logic:price_list"]
    assert world.records[0]["outcome"] == "compose"


# ── 3. compose is never no_match ──────────────────────────────────────────


async def test_two_callable_rows_and_compose_give_a_compose_row(monkeypatch) -> None:
    """Done-when 6. Mutation: map ``compose`` to ``no_match`` -> red."""
    world = fk.install(monkeypatch, rows=[KNOW, KNOW_READ],
                       picks={"answer": ("compose", 0.95), "category": ("documents", 0.9)})
    with fk.in_run():
        told = await tgp.gap_hook(fk.NEED, [])
    [row] = world.records
    assert (row["answer"], row["outcome"]) == ("compose", "compose")
    assert row["outcome"] != "no_match"
    assert row["candidates"][:2] == ["knowledge.read", "knowledge.search"]
    assert told == tgp.SAY_COMPOSE.format(ids="knowledge.read, knowledge.search")


async def test_with_no_callable_row_compose_is_not_offered(monkeypatch) -> None:
    world = fk.install(monkeypatch, features=frozenset({"chat"}), rows=[KNOW, *OTHERS],
                       picks={"answer": ("compose", 0.95)})
    with fk.in_run():
        await tgp.gap_hook(fk.NEED, [])
    [(_context, items, _purpose)] = world.asks
    assert "compose" not in items[0].options
    assert world.records[0]["outcome"] == "unsure"


# ── 4. No session while the catalog and the engine run ────────────────────


async def test_no_session_is_open_while_catalog_rows_or_the_engine_runs(monkeypatch) -> None:
    """Done-when 9. Mutation: open the library session before
    ``catalog_rows`` -> the catalog sees one session -> red."""
    world = fk.install(monkeypatch, rows=[KNOW], picks={"answer": ("none", 0.9)})
    with fk.in_run():
        await tgp.gap_hook(fk.NEED, [])
    assert world.catalog_calls == 1
    assert world.catalog_saw_open == [0]
    assert world.ask_saw_open == [0]
    assert world.opened == 2, "one session for the library, one for the record"
    assert world.sessions_open == 0


# ── 5. The cap, and no package_rows ───────────────────────────────────────


def test_the_check_keeps_twenty_rows_at_most() -> None:
    rows = [fk.knowledge_row(f"r{i:02d}", "Search the knowledge notes") for i in range(30)]
    picked = tgp.nearest_rows(fk.NEED, rows)
    assert len(picked) == tgp.CATALOG_LIMIT == 20
    assert picked == sorted(picked)
    assert tgp.nearest_rows("weather in paris", rows) == []


async def test_the_hook_never_calls_package_rows(monkeypatch) -> None:
    from gateway.routes.studio import actions

    def _trip(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("package_rows ran")

    world = fk.install(monkeypatch, rows=[KNOW], picks={"answer": ("none", 0.9)})
    monkeypatch.setattr(actions, "package_rows", _trip)
    with fk.in_run():
        await tgp.gap_hook(fk.NEED, [])
    assert len(world.records) == 1
