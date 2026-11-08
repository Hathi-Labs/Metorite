"""The planning engine — the ONE place that computes the order of work (PJP-6a).

Spec: ``project-docs/specs/project_planning.md`` §3.4 (D195 Q8, SP-11, task 2294).

The module is pure. It does no I/O and imports no I/O library. The caller
fetches the tasks and the ``blocks`` links, and this module returns plain data.
The gateway read ``GET /projects/plan/analysis`` is the first caller. SW-90,
WM-3 and the chat tool of PJP-6b read the same function, and keep no maths of
their own. ``tests/unit/test_planning_module.py`` holds that rule (R7).

It lives in ``acb_common`` because the gateway and the chat skill both need it
and neither may import the other (as ``priority.py`` and ``paging.py`` do).

Rules, and why:

* **A slice is an open leaf.** A task with no children in the input is a leaf.
  Work is done at the leaves, so only a leaf has a duration. A parent has none.
* **A link on a parent expands to its slices.** An epic-to-epic ``blocks``
  link means that no slice of the blocked epic starts before the blocker epic is
  done. So every slice of the blocker epic blocks every slice of the blocked
  epic. This is the strict reading, and it needs no extra data.
* **Duration = ``estimate_mins`` + ``REVIEW_WAIT_MINS``.** A missing estimate
  counts as 0 minutes. It is listed in ``missing`` and never stops the read.
* **A working day is 360 minutes** and Saturday and Sunday do not count.
* **A blocker outside the input** is open or done. A done one is ignored. An
  open one holds its task, and the result names it in ``outside_blockers``.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Any

#: A working day, in minutes (6 focus hours). Spec §3.4 "Units".
WORKING_DAY_MINS = 360

#: The expected wait for the review of one slice, in minutes. A named constant
#: until SW-93 measures the real answer time of the reviewer and replaces it.
REVIEW_WAIT_MINS = 60

#: The statuses that end a task. The twin of ``CLOSING_CATEGORIES`` in the
#: gateway (``routes/projects/core.py``). A closed blocker no longer blocks.
CLOSED_CATEGORIES: frozenset[str] = frozenset({"done", "cancelled"})

#: The least number of linked open slices for a critical path.
MIN_LINKED = 2
NOT_ENOUGH_TEXT = "need 2 linked tasks"

#: A rank for a task with no priority. It sorts after every real rank (1 to 7).
_NO_RANK = 99


@dataclass(frozen=True)
class PlanTask:
    """One task as the planner reads it. ``priority_rank`` is 1 (first) to 7."""

    id: str
    category: str
    estimate_mins: int | None = None
    owner: str | None = None
    type: str | None = None
    parent: str | None = None
    priority_rank: int | None = None


@dataclass
class PlanResult:
    """The result. Every field is plain data, so ``to_dict`` is JSON."""

    ready: list[str] = field(default_factory=list)
    waves: list[list[str]] = field(default_factory=list)
    critical_path: list[str] = field(default_factory=list)
    slack: dict[str, int] = field(default_factory=dict)
    next_slice: str | None = None
    finish: dict[str, Any] | None = None
    cycle: dict[str, list[str]] | None = None
    missing: dict[str, Any] = field(default_factory=dict)
    not_enough: str | None = None
    outside_blockers: list[dict[str, Any]] = field(default_factory=list)
    held: list[str] = field(default_factory=list)
    slices: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def add_working_days(start: date, days: int) -> date:
    """The date that is ``days`` working days after ``start``. Weekends skip.

    ``days`` 0 gives ``start``, moved to Monday when it is a weekend.
    """
    day = start
    while day.weekday() >= 5:
        day += timedelta(days=1)
    left = days
    while left > 0:
        day += timedelta(days=1)
        if day.weekday() < 5:
            left -= 1
    return day


def _finish(mins: int, start: date | None) -> dict[str, Any]:
    days = -(-mins // WORKING_DAY_MINS) if mins > 0 else 0
    when = None
    if start is not None:
        # Work starts on day 1, so the last day is `days - 1` days later.
        when = add_working_days(start, max(days - 1, 0)).isoformat()
    return {"mins": mins, "working_days": days, "date": when}


def _plain(count: int, noun_phrase: str) -> str:
    if count == 1:
        return f"1 task {noun_phrase.replace('have', 'has', 1)}"
    return f"{count} tasks {noun_phrase}"


@dataclass
class _Graph:
    """The input, read once: the structure, the slices and the expanded edges."""

    by_id: dict[str, PlanTask]
    children: dict[str, list[str]]
    slices: list[str]
    #: `(from, to)` slice pairs, each with the endpoints as the caller wrote them.
    edges: dict[tuple[str, str], set[str]]
    #: Open outside blockers, by the slice that they hold.
    held_by: dict[str, set[str]]
    preds: dict[str, set[str]]
    succs: dict[str, set[str]]


def _leaves(children: Mapping[str, list[str]], task_id: str) -> list[str]:
    """Every leaf under a task, closed ones too. A leaf gives itself."""
    if task_id not in children:
        return [task_id]
    out: list[str] = []
    stack = list(children[task_id])
    seen: set[str] = set()
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        if cur in children:
            stack.extend(children[cur])
        else:
            out.append(cur)
    return out


def _build(
    tasks: Iterable[PlanTask],
    links: Iterable[tuple[str, str]],
    outside: Mapping[str, str],
) -> _Graph:
    by_id = {t.id: t for t in tasks}
    children: dict[str, list[str]] = defaultdict(list)
    for t in by_id.values():
        if t.parent in by_id:
            children[t.parent].append(t.id)
    slices = sorted(
        t.id for t in by_id.values()
        if t.id not in children and t.category not in CLOSED_CATEGORIES
    )
    slice_set = set(slices)
    edges: dict[tuple[str, str], set[str]] = defaultdict(set)
    held_by: dict[str, set[str]] = defaultdict(set)
    for blocker, blocked in links:
        if blocked not in by_id:
            continue
        for to_id in (x for x in _leaves(children, blocked) if x in slice_set):
            if blocker in by_id:
                for from_id in _leaves(children, blocker):
                    # A closed blocker is not a slice, so it blocks nothing.
                    if from_id != to_id and from_id in slice_set:
                        edges[(from_id, to_id)].update((blocker, blocked))
            elif blocker in outside and outside[blocker] not in CLOSED_CATEGORIES:
                held_by[to_id].add(blocker)
    preds: dict[str, set[str]] = {s: set() for s in slices}
    succs: dict[str, set[str]] = {s: set() for s in slices}
    for a, b in edges:
        succs[a].add(b)
        preds[b].add(a)
    return _Graph(by_id, children, slices, edges, held_by, preds, succs)


def _missing(g: _Graph) -> dict[str, Any]:
    no_estimate = [s for s in g.slices if g.by_id[s].estimate_mins is None]
    no_owner = [s for s in g.slices if not g.by_id[s].owner]
    words = []
    if no_estimate:
        words.append(_plain(len(no_estimate), "have no estimate"))
    if no_owner:
        words.append(_plain(len(no_owner), "have no owner"))
    return {
        "no_estimate": len(no_estimate),
        "no_owner": len(no_owner),
        "no_estimate_ids": no_estimate,
        "no_owner_ids": no_owner,
        "words": words,
    }


def _order(g: _Graph) -> list[str]:
    """Kahn's method. A slice that is left out is on a cycle or after one."""
    indeg = {s: len(g.preds[s]) for s in g.slices}
    queue = [s for s in g.slices if indeg[s] == 0]
    order: list[str] = []
    while queue:
        cur = queue.pop(0)
        order.append(cur)
        for nxt in sorted(g.succs[cur]):
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                queue.append(nxt)
    return order


def _cycle(g: _Graph, order: list[str]) -> dict[str, list[str]]:
    placed = set(order)
    left = {s for s in g.slices if s not in placed}
    # Strip the tail too: a slice after the loop is not part of it.
    changed = True
    while changed:
        changed = False
        for s in sorted(left):
            if not (g.succs[s] & left):
                left.discard(s)
                changed = True
    ends: set[str] = set()
    for (a, b), origin in g.edges.items():
        if a in left and b in left:
            ends |= origin
    return {
        "task_ids": sorted(left),
        "epic_ids": sorted(e for e in ends if e in g.children),
    }


def _waves(g: _Graph, order: list[str]) -> tuple[list[list[str]], list[str]]:
    """Wave 1 is the ready set. Wave n+1 is ready when wave n is done.

    A slice held by an outside blocker, and each slice after it, has no wave.
    """
    held: set[str] = {s for s in g.slices if g.held_by[s]}
    for s in order:
        if g.preds[s] & held:
            held.add(s)
    wave_of: dict[str, int] = {}
    for s in order:
        if s not in held:
            wave_of[s] = 1 + max((wave_of[p] for p in g.preds[s]), default=0)
    waves: list[list[str]] = [[] for _ in range(max(wave_of.values(), default=0))]
    for s in g.slices:
        if s in wave_of:
            waves[wave_of[s] - 1].append(s)
    return waves, sorted(held)


def _critical(g: _Graph, order: list[str]) -> tuple[dict[str, int], list[str], int]:
    """The critical path method: slack per slice, one longest chain, its length."""

    def dur(s: str) -> int:
        return (g.by_id[s].estimate_mins or 0) + REVIEW_WAIT_MINS

    es: dict[str, int] = {}
    ef: dict[str, int] = {}
    for s in order:
        es[s] = max((ef[p] for p in g.preds[s]), default=0)
        ef[s] = es[s] + dur(s)
    length = max(ef.values())
    lf: dict[str, int] = {}
    for s in reversed(order):
        lf[s] = min((lf[n] - dur(n) for n in g.succs[s]), default=length)
    slack = {s: lf[s] - ef[s] for s in g.slices}
    # The path walks back from the last slice to finish, by least id.
    cur = min(s for s in g.slices if ef[s] == length and slack[s] == 0)
    path = [cur]
    while g.preds[cur]:
        tight = [p for p in g.preds[cur] if ef[p] == es[cur] and slack[p] == 0]
        if not tight:
            break
        cur = min(tight)
        path.append(cur)
    path.reverse()
    return slack, path, length


def _next_slice(g: _Graph, ready: list[str], slack: Mapping[str, int]) -> str | None:
    """The ready slice with the least slack, then priority, then id.

    A ready slice with zero slack is on a critical chain. Two chains of the same
    length are both critical, and `critical_path` shows only one of them. So
    the pool is the ready slices with zero slack, and not the ones on that chain.
    """
    pool = [s for s in ready if slack[s] == 0] or ready
    if not pool:
        return None
    return min(pool, key=lambda s: (slack[s], g.by_id[s].priority_rank or _NO_RANK, s))


def analyze_plan(
    tasks: Iterable[PlanTask],
    links: Iterable[tuple[str, str]],
    *,
    start: date | None = None,
    outside: Mapping[str, str] | None = None,
) -> PlanResult:
    """Analyse the order of work.

    ``links`` are ``(blocker, blocked)`` pairs of ``blocks`` links. ``outside``
    maps the id of a task that is not in ``tasks`` to its status category, for
    a blocker the caller can see. A link to an id that is in neither is ignored.
    """
    g = _build(tasks, links, dict(outside or {}))
    result = PlanResult(slices=len(g.slices))
    result.ready = [s for s in g.slices if not g.preds[s] and not g.held_by[s]]
    result.outside_blockers = [
        {"task_id": s, "blocked_by": sorted(g.held_by[s])} for s in g.slices if g.held_by[s]
    ]
    result.missing = _missing(g)

    order = _order(g)
    if len(order) < len(g.slices):
        result.cycle = _cycle(g, order)
        return result
    result.waves, result.held = _waves(g, order)
    if sum(1 for s in g.slices if g.preds[s] or g.succs[s]) < MIN_LINKED:
        result.not_enough = NOT_ENOUGH_TEXT
        return result
    result.slack, result.critical_path, length = _critical(g, order)
    result.next_slice = _next_slice(g, result.ready, result.slack)
    result.finish = _finish(length, start)
    return result


def tasks_from_rows(rows: Sequence[Mapping[str, Any]]) -> list[PlanTask]:
    """``PlanTask`` records from plain dicts. Keys not named here are ignored."""
    return [
        PlanTask(
            id=str(r["id"]),
            category=str(r["category"]),
            estimate_mins=r.get("estimate_mins"),
            owner=r.get("owner"),
            type=r.get("type"),
            parent=str(r["parent"]) if r.get("parent") else None,
            priority_rank=r.get("priority_rank"),
        )
        for r in rows
    ]
