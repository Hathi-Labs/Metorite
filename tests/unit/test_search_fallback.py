"""HRD-4 (task 2983) - search that finds nothing, and a search that waits.

Spec: ``project-docs/specs/find_engine.md`` §3.2 and §9.2, D-FIND-6, and
``chat_failure_modes.md`` §4 B2. No database: every source is a fake.

The claims, each written so a wrong build fails it:

* **At once.** A slow source does not hold a fast one. The time of the pass is
  about the slowest source, not the sum.
* **Four at most.** Six sources never run more than ``FIND_SESSIONS`` together,
  and the fifth waits for a slot.
* **Its own time.** Each source has ``SOURCE_TIMEOUT_S`` from its own start,
  and only the slow source is named in ``skipped``, in registry order.
* **No shared options.** Each source gets its own ``budget_s``.
* **The words held.** ``find_tasks`` says "No task has all the words" and
  lists the tasks that hold most of them, when the route marks a fallback.

Mutations this file catches (R7): the loop goes back to one source at a time
-> ``test_a_fast_source_is_not_held_by_a_slow_one``; the semaphore drops ->
``test_at_most_four_sources_run_at_once``; one dict is shared ->
``test_each_source_gets_its_own_options``; the sentence changes ->
``test_find_tasks_says_it_is_a_fallback``.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException
from gateway.retrieval import company

pytest.importorskip("skill_projects", reason="skill-projects not installed")
import skill_projects

from tests.unit._projects_agent_fakes import fake_gateway


class _Source:
    """A fake source. ``delay`` is its time, ``error`` is what it raises."""

    dated = True

    def __init__(self, key: str, world: _World, delay: float = 0.0,
                 error: BaseException | None = None) -> None:
        self.key = key
        self.world = world
        self.delay = delay
        self.error = error

    def reaches(self, _user: object) -> bool:
        return True

    async def find(self, _user: object, _q: str, _k: int, *, hybrid: bool = False,
                   **options: Any) -> list[str]:
        w = self.world
        w.options[self.key] = options
        w.active += 1
        w.peak = max(w.peak, w.active)
        w.started.append(self.key)
        try:
            await asyncio.sleep(self.delay)
            if self.error is not None:
                raise self.error
            return [f"{self.key}-hit"]
        finally:
            w.active -= 1


class _World:
    def __init__(self) -> None:
        self.active = 0
        self.peak = 0
        self.started: list[str] = []
        self.options: dict[str, dict[str, Any]] = {}


def _ask(monkeypatch: pytest.MonkeyPatch, sources: list[_Source], *,
         timed: bool = False, budget: float = 4.0) -> tuple[dict, list[str], float]:
    registry = {s.key: s for s in sources}
    monkeypatch.setattr(company, "REGISTRY", registry)

    async def go() -> tuple[dict, list[str]]:
        loop = asyncio.get_running_loop()
        found, skipped, _k = await company._ask_each(
            SimpleNamespace(), "Sharath 3D printer", list(registry), 5, hybrid=False,
            options={"after": None}, since=None, deadline=loop.time() + budget,
            timed=timed)
        return found, skipped

    began = time.monotonic()
    found, skipped = asyncio.run(go())
    return found, skipped, time.monotonic() - began


def test_a_fast_source_is_not_held_by_a_slow_one(monkeypatch) -> None:
    world = _World()
    slow, fast = _Source("slow", world, delay=1.0), _Source("fast", world)
    found, skipped, took = _ask(monkeypatch, [slow, fast])
    assert found == {"slow": ["slow-hit"], "fast": ["fast-hit"]}
    assert skipped == []
    assert took < 1.5, f"about the slowest source, not the sum: {took}"
    # The fast source started before the slow one ended.
    assert world.started == ["slow", "fast"]


def test_at_most_four_sources_run_at_once(monkeypatch) -> None:
    world = _World()
    sources = [_Source(f"s{i}", world, delay=0.05) for i in range(6)]
    found, skipped, _took = _ask(monkeypatch, sources)
    assert company.FIND_SESSIONS == 4
    assert world.peak == company.FIND_SESSIONS
    assert skipped == [] and list(found) == [s.key for s in sources]


def test_a_fifth_source_waits_for_a_slot(monkeypatch) -> None:
    world = _World()
    sources = [_Source(f"s{i}", world, delay=0.2) for i in range(5)]
    _found, _skipped, took = _ask(monkeypatch, sources)
    assert world.peak == 4
    assert took >= 0.4, f"the fifth ran after a slot freed: {took}"


def test_a_slow_source_alone_is_named_skipped_in_registry_order(monkeypatch) -> None:
    monkeypatch.setattr(company, "SOURCE_TIMEOUT_S", 0.1)
    world = _World()
    sources = [
        _Source("a", world), _Source("b", world, delay=1.0), _Source("c", world),
        _Source("d", world, error=HTTPException(status_code=422, detail="short")),
        _Source("e", world, delay=1.0),
    ]
    found, skipped, took = _ask(monkeypatch, sources)
    assert list(found) == ["a", "c"]
    assert skipped == ["b", "d", "e"]
    assert took < 0.8


def test_the_time_of_a_source_counts_from_its_own_start(monkeypatch) -> None:
    # Five sources of 0.15 s, a limit of 0.2 s, and 4 slots. The fifth starts
    # at 0.15 s and ends at 0.30 s. A limit counted from the pass would cut it.
    monkeypatch.setattr(company, "SOURCE_TIMEOUT_S", 0.2)
    world = _World()
    sources = [_Source(f"s{i}", world, delay=0.15) for i in range(5)]
    found, skipped, _took = _ask(monkeypatch, sources)
    assert skipped == [] and len(found) == 5


def test_each_source_gets_its_own_options(monkeypatch) -> None:
    world = _World()
    sources = [_Source(f"s{i}", world, delay=0.02) for i in range(3)]
    _ask(monkeypatch, sources, timed=True, budget=3.0)
    budgets = {key: opts["budget_s"] for key, opts in world.options.items()}
    assert set(budgets) == {"s0", "s1", "s2"}
    assert all(0 < b <= company.SOURCE_TIMEOUT_S for b in budgets.values())
    # Distinct dict objects: a shared one would hold the last write for all.
    assert len({id(o) for o in world.options.values()}) == 3


def test_the_budget_still_ends_the_pass(monkeypatch) -> None:
    world = _World()
    sources = [_Source(f"s{i}", world, delay=0.3) for i in range(6)]
    found, skipped, _took = _ask(monkeypatch, sources, budget=0.34)
    # The first four take the slots, the other two find no time left.
    assert list(found) == ["s0", "s1", "s2", "s3"]
    assert skipped == ["s4", "s5"]


# ── find_tasks, a fallback on most of the words ───────────────────────────────

def _row(n: int, title: str, matched: int | None = None) -> dict[str, Any]:
    row = {"id": f"00000000-0000-4000-8000-00000000000{n}", "title": title,
           "task_number": n, "status_name": "Open", "category": "todo",
           "project_name": "Ops"}
    if matched is not None:
        row["matched"] = matched
    return row


async def test_find_tasks_says_it_is_a_fallback(monkeypatch) -> None:
    payload = {"rows": [_row(5, "Sharath printer", 2), _row(6, "3D plan", 1)],
               "total": 2, "truncated": False, "query": "Sharath 3D printer",
               "partial": True, "words": 3}
    calls = fake_gateway(monkeypatch, lambda _c: payload)
    out = await skill_projects.find_tasks("Sharath 3D printer")
    assert "No task has all the words. These have most of them" in out
    assert "holds 2 of 3 words" in out and "holds 1 of 3 words" in out
    assert out.index("#5") < out.index("#6")
    assert [c["path"] for c in calls] == ["/projects/search"]


async def test_find_tasks_says_nothing_of_a_fallback_for_an_all_words_hit(
        monkeypatch) -> None:
    payload = {"rows": [_row(5, "Sharath 3D printer")], "total": 1,
               "truncated": False, "query": "Sharath printer"}
    fake_gateway(monkeypatch, lambda _c: payload)
    out = await skill_projects.find_tasks("Sharath printer")
    assert "No task has all the words" not in out
    assert "Tasks matching" in out and "holds" not in out
