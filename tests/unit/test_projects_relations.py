"""WS-27p — dependencies and subtasks, made reachable.

Spec: `project-docs/specs/project_management_app.md` §11.2 item 8, §11.14.

*"`pm_task_links` and `parent_task_id` both exist, unreachable from the board.
Data with no surface is a promise the product does not keep."*

Both halves were genuinely unreachable, and for different reasons: links could
be created and deleted since WS-27a but never LISTED (`get_task` returns a
*count*), and subtasks could be created from the panel but never listed either.

The claims worth pinning:

* **`blocks` may not form a cycle.** `assert_no_task_cycle` has guarded
  `parent_task_id` since WS-27a and the same hazard sat unguarded on links. A
  blocks B blocks C blocks A is a deadlock no human can resolve by finishing
  something, and every walk over it runs forever.
* **only `blocks` is guarded.** A cycle in `relates_to` is redundant, not
  harmful, and refusing one would be a rule with no failure to prevent.
* **"blocked" means a blocker that is still OPEN.** A done blocker blocks
  nothing, and a task that stays red after its dependency shipped is a task
  people learn to ignore.
* **progress counts the CATEGORY**, not `completed_at` — a project can name its
  finished lane anything, and `cancelled` is resolved even though nothing was
  completed.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException
from gateway.routes.projects.core import CLOSING_CATEGORIES, MAX_DEPTH
from gateway.routes.projects.relations import (
    DIRECTED_TYPES,
    LINK_TYPES,
    assert_no_block_cycle,
    blocked_by_open,
    subtask_progress,
)


def run(coro):
    import asyncio

    return asyncio.run(coro)


class FakeLinks:
    """Just enough of a db for the cycle walk: a `blocks` adjacency list."""

    def __init__(self, edges: dict[str, list[str]]):
        self.edges = edges
        self.queries = 0
        #: The advisory locks taken, and how many walk queries came before each.
        self.locks: list[int] = []

    async def execute(self, sql: Any, params: dict | None = None):
        if "pg_advisory_xact_lock" in str(sql):
            self.locks.append(self.queries)
            return SimpleNamespace(fetchall=lambda: [])
        self.queries += 1
        ids = (params or {}).get("ids") or []
        out: list[Any] = []
        for source in ids:
            for target in self.edges.get(str(source), []):
                out.append(SimpleNamespace(target_task_id=target))
        return SimpleNamespace(fetchall=lambda: out)


# ── The vocabulary ──────────────────────────────────────────────────────────

def test_only_blocks_is_treated_as_directed():
    """A cycle in `relates_to` or `duplicates` is redundant, not harmful.
    Refusing one would be a rule with no failure to prevent."""
    assert DIRECTED_TYPES == ("blocks",)
    for kind in DIRECTED_TYPES:
        assert kind in LINK_TYPES


def test_the_link_types_match_the_ones_tasks_py_accepts():
    """Two lists of link types would drift, and the pair that drifted would let
    a link be created that this module refuses to classify."""
    from gateway.routes.projects.tasks import _LINK_TYPES

    assert set(LINK_TYPES) == set(_LINK_TYPES)


# ── The cycle guard ─────────────────────────────────────────────────────────

def test_a_task_cannot_block_itself_and_is_TOLD_that():
    """Without its own branch this still 422s — the walk's first step finds the
    source in the frontier — but it says "this task already depends on the one
    you are blocking", which is a baffling thing to read about one task."""
    with pytest.raises(HTTPException) as exc:
        run(assert_no_block_cycle(FakeLinks({}), "a", "a"))
    assert exc.value.status_code == 422
    assert "itself" in str(exc.value.detail), (
        "a self-link deserves its own message, not the loop explanation"
    )


def test_a_direct_reciprocal_block_is_refused():
    """B already blocks A, so A blocking B closes the loop."""
    with pytest.raises(HTTPException) as exc:
        run(assert_no_block_cycle(FakeLinks({"b": ["a"]}), "a", "b"))
    assert exc.value.status_code == 422
    assert "loop" in str(exc.value.detail)


def test_a_LONG_cycle_is_refused_too():
    """The case a naive one-hop check misses: A → B → C → A."""
    with pytest.raises(HTTPException):
        run(assert_no_block_cycle(FakeLinks({"b": ["c"], "c": ["a"]}), "a", "b"))


def test_an_honest_chain_is_allowed():
    """A → B → C is a dependency chain, not a cycle, and refusing it would make
    the feature useless for the thing it is for."""
    run(assert_no_block_cycle(FakeLinks({"b": ["c"]}), "a", "b"))


def test_a_diamond_is_allowed():
    """A blocks B and C, both of which block D. Not a cycle — and a walk that
    did not track what it had seen would visit D twice."""
    run(assert_no_block_cycle(FakeLinks({"b": ["d"], "c": ["d"]}), "a", "b"))


def test_an_existing_cycle_elsewhere_does_not_hang_the_walk():
    """Data can already contain a loop — 146 has no constraint against one, and
    every link created before this guard existed went in unchecked. The walk
    must terminate over it rather than spin."""
    graph = FakeLinks({"b": ["c"], "c": ["b"]})
    run(assert_no_block_cycle(graph, "a", "b"))
    assert graph.queries < MAX_DEPTH, "the walk revisited nodes instead of tracking them"


def test_the_lock_comes_before_the_first_read_of_the_graph():
    """SW-16a. Two adds in opposite directions must not both pass the walk."""
    graph = FakeLinks({"b": ["c"]})
    run(assert_no_block_cycle(graph, "a", "b"))
    assert graph.locks == [0]


def test_the_walk_is_bounded():
    """An unbounded walk over data somebody can create is a denial-of-service
    surface rather than a thorough check."""
    chain = {str(i): [str(i + 1)] for i in range(MAX_DEPTH + 50)}
    with pytest.raises(HTTPException) as exc:
        run(assert_no_block_cycle(FakeLinks(chain), "target", "0"))
    assert "longer than the supported maximum" in str(exc.value.detail)


def test_the_walk_asks_in_BATCHES_not_one_query_per_node():
    """A dependency graph is walked on every link create. Per-node queries make
    that N round trips for a graph somebody else's project owns."""
    graph = FakeLinks({"b": ["c", "d"], "c": ["e"], "d": ["e"]})
    run(assert_no_block_cycle(graph, "a", "b"))
    # Three levels (b → {c,d} → {e} → {}), so at most three queries.
    assert graph.queries <= 3


# ── Blocked-ness ────────────────────────────────────────────────────────────

def blocker(category: str) -> dict:
    return {"id": "x", "title": "t", "category": category}


def test_a_finished_blocker_no_longer_blocks():
    """A task that stays red after its dependency shipped is a task people
    learn to ignore."""
    assert blocked_by_open([blocker("done")]) == []
    assert blocked_by_open([blocker("cancelled")]) == []


def test_an_open_blocker_blocks():
    for category in ("backlog", "todo", "in_progress"):
        assert len(blocked_by_open([blocker(category)])) == 1


def test_the_closing_categories_are_the_ones_the_rest_of_the_app_uses():
    """"Blocked" is derived, and it must mean the same thing here as everywhere
    else — the status transition, the overdue filter and this all key off one
    definition of finished."""
    assert set(CLOSING_CATEGORIES) == {"done", "cancelled"}


def test_a_blocker_with_no_category_is_treated_as_open():
    """Fail towards showing the dependency. Silently dropping one because a row
    came back thin is how a blocked task looks ready to start."""
    assert len(blocked_by_open([{"id": "x"}])) == 1


def test_nothing_blocking_is_not_blocked():
    assert blocked_by_open([]) == []


# ── Subtask progress ────────────────────────────────────────────────────────

def child(category: str) -> dict:
    return {"id": "c", "title": "t", "category": category}


def test_progress_counts_done_over_total():
    got = subtask_progress([child("done"), child("todo"), child("todo")])
    assert got == {"done": 1, "total": 3}


def test_a_cancelled_subtask_counts_as_resolved():
    """Nothing was completed, but nobody is waiting on it either — and "2 of 3"
    beside a list where the third was cancelled reads as work outstanding."""
    assert subtask_progress([child("cancelled"), child("done")]) == {
        "done": 2, "total": 2,
    }


def test_progress_reads_the_CATEGORY_not_a_status_name():
    """A project can name its finished lane "Shipped", "Live" or "Signed off".
    Matching on the name would work for exactly the seeded projects."""
    assert subtask_progress([{"category": "done", "status_name": "Shipped"}]) == {
        "done": 1, "total": 1,
    }
    assert subtask_progress([{"category": "todo", "status_name": "Done-ish"}]) == {
        "done": 0, "total": 1,
    }


def test_no_subtasks_is_zero_of_zero_rather_than_a_crash():
    """The panel divides by this to draw a bar."""
    assert subtask_progress([]) == {"done": 0, "total": 0}


# ── Wiring ──────────────────────────────────────────────────────────────────

def test_relations_is_mounted():
    from gateway.routes.projects import router

    paths = {r.path for r in router.routes}
    assert "/projects/tasks/{task_id}/relations" in paths


def test_creating_a_link_goes_through_the_cycle_guard():
    """The guard is only worth having if the write path calls it. Asserted
    against the source because a behavioural test would need a real graph in a
    fake that would then be agreeing with itself."""
    from pathlib import Path

    import gateway.routes.projects.tasks as tasks_mod

    source = Path(tasks_mod.__file__).read_text(encoding="utf-8")
    body = source.split("async def create_link", 1)[1].split("\n@router", 1)[0]
    assert "assert_no_block_cycle" in body, (
        "create_link no longer checks for a dependency loop"
    )
    assert "DIRECTED_TYPES" in body, (
        "the guard should apply to directed links only, not to relates_to"
    )
