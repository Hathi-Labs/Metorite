"""Conflicts — where planned work interferes with itself (WS-27bm S7c).

Spec: ``project-docs/specs/projects_ai_chat.md`` §13.5, §10.5.

The pure half of ``GET /projects/analytics/conflicts``. The route
(``routes/projects/analytics_conflicts.py``) fetches rows and calls these.
Nothing here opens a session or reads a clock, so every rule is testable
without a database (R5: this module adds no connection site).

**A leaf module, beside ``capacity.py``**, for the same reason: the People
package imports the Projects package, so a helper in either route package
would close an import cycle.

**The dependency rule is ONE rule, in two languages.**
:func:`dependency_conflict` is the rule of ``timeline.ts`` ``conflicts()``
(D-PM-12). The timeline keeps its copy for live feedback while a member drags
a bar. ``tests/fixtures/projects_dependency_conflicts.json`` pins the cases,
and both ``test_projects_analytics_conflicts.py`` and ``timeline.test.ts``
read it, so the two copies cannot drift (§13.5 rule 2).

Two known weak spots of the browser copy are NOT copied (§13.5):
``TimelineView.tsx`` checks only the first blocker of a row, and
``conflicts()`` reads the browser's local day. The route checks every
visible blocker, and :func:`utc_day` reads the UTC day.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from typing import Any

#: The seven kinds, in the order the response sorts them (§13.5 table).
KINDS: tuple[str, ...] = (
    "dependency_order",
    "blocker_late",
    "parallel_person",
    "overcommitted",
    "absent_on_due",
    "over_concurrency",
    "leaving",
)

#: The four kinds that need ``admin:members:read`` (§13.5 rule 9). Without
#: the grant they are ABSENT from the response, never empty.
HR_KINDS: frozenset[str] = frozenset(
    {"overcommitted", "absent_on_due", "over_concurrency", "leaving"}
)

#: §13.5 rule 11. ``high`` when a due date is at stake, ``medium`` for the
#: rest. No other value exists.
SEVERITY: dict[str, str] = {
    "dependency_order": "medium",
    "blocker_late": "high",
    "parallel_person": "medium",
    "overcommitted": "high",
    "absent_on_due": "high",
    "over_concurrency": "medium",
    "leaving": "high",
}

#: The most rows one response carries (§13.5 rule 11). ``total`` and
#: ``by_kind`` count every row, before the cap.
MAX_ROWS = 200

#: §13.5 rule 6. Three tasks on one day, in two or more top-level projects.
PARALLEL_MIN_TASKS = 3
PARALLEL_MIN_ROOTS = 2
#: The tasks one ``parallel_person`` row names. ``tasks_total`` says how
#: many the day holds.
PARALLEL_MAX_TASKS = 5


def utc_day(value: Any) -> date | None:
    """The UTC date of a ``due_at`` (§13.5 rule 3).

    ``workload.as_date`` reads a stored timestamp the same way. A naive
    datetime is taken as UTC already, which is how the driver hands one back
    only when the column had no zone.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, date):
        return value
    else:
        try:
            moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if moment.tzinfo is not None:
        moment = moment.astimezone(UTC)
    return moment.date()


def start_day(value: Any) -> date | None:
    """A ``start_date``. It is a DATE column, so it has no zone to convert."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def interval(task: dict[str, Any]) -> tuple[date, date] | None:
    """A task's scheduled span, or ``None`` when it has no dates.

    The rule of ``timeline.ts`` ``interval``: a task with one date uses it for
    both ends, and a due date before the start date is swapped, so bad data
    reads as the span it implies.
    """
    start = start_day(task.get("start_date"))
    due = utc_day(task.get("due_at"))
    if start is None and due is None:
        return None
    a = start if start is not None else due
    b = due if due is not None else start
    assert a is not None and b is not None
    return (a, b) if a <= b else (b, a)


def dependency_conflict(blocker: dict[str, Any], blocked: dict[str, Any]) -> bool:
    """Does a ``blocks`` link disagree with the schedule? (D-PM-12)

    A conflict is the blocker's END strictly after the blocked task's START.
    A shared day is the normal handover and is not a conflict. A missing date
    on either end is unknowable and is not a conflict. A completed blocker
    never conflicts, because its dates are history.
    """
    if blocker.get("completed_at"):
        return False
    before = interval(blocker)
    after = interval(blocked)
    if before is None or after is None:
        return False
    return before[1] > after[0]


def parallel_days(task: dict[str, Any]) -> tuple[date, date] | None:
    """The days a task occupies for ``parallel_person``, inclusive, or None.

    Only a task with BOTH a start date and a due date counts (§13.5 rule 6).
    The span runs from its start to the day BEFORE its due day, because the
    due day is the handover, as it is in :func:`dependency_conflict`. So a
    task that ends on the day another starts does not overlap it. A task that
    starts and is due on one day occupies that day.
    """
    if start_day(task.get("start_date")) is None or utc_day(task.get("due_at")) is None:
        return None
    span = interval(task)
    if span is None:
        return None
    first, last = span
    if last > first:
        last = last - timedelta(days=1)
    return first, last


def busiest_parallel_day(
    tasks: Iterable[dict[str, Any]], window_start: date, window_end: date,
) -> tuple[date, list[dict[str, Any]]] | None:
    """One person's busiest day of parallel work in the window, or None.

    Each task carries ``id``, ``root_project_id``, ``in_scope``,
    ``start_date`` and ``due_at``. A day qualifies when it holds
    :data:`PARALLEL_MIN_TASKS` or more tasks, in :data:`PARALLEL_MIN_ROOTS` or
    more top-level projects, and at least one of them is in scope. The
    busiest day wins, and the earlier of two equal days wins. The tasks come
    back in-scope first, then by due day, then by id.
    """
    spans: list[tuple[date, date, dict[str, Any]]] = []
    seen: set[str] = set()
    for task in tasks:
        key = str(task.get("id"))
        if key in seen:
            continue
        seen.add(key)
        days = parallel_days(task)
        if days is not None:
            spans.append((days[0], days[1], task))
    if len(spans) < PARALLEL_MIN_TASKS:
        return None

    best: tuple[date, list[dict[str, Any]]] | None = None
    day = window_start
    while day <= window_end:
        covering = [t for first, last, t in spans if first <= day <= last]
        if (
            len(covering) >= PARALLEL_MIN_TASKS
            and len({str(t.get("root_project_id")) for t in covering}) >= PARALLEL_MIN_ROOTS
            and any(t.get("in_scope") for t in covering)
            and (best is None or len(covering) > len(best[1]))
        ):
            best = (day, covering)
        day += timedelta(days=1)
    if best is None:
        return None
    chosen, covering = best
    covering = sorted(
        covering,
        key=lambda t: (
            not t.get("in_scope"),
            utc_day(t.get("due_at")) or date.max,
            str(t.get("id")),
        ),
    )
    return chosen, covering


def conflict_row(
    kind: str,
    *,
    task_ids: list[str],
    people: list[dict[str, Any]],
    sentence: str,
    due_on: date | None,
    **extra: Any,
) -> dict[str, Any]:
    """One row, in the shape of §13.5 rule 11, with its severity.

    ``due_on`` is the day the row is about, and the sort reads it. Extra keys
    carry what one kind names and the others do not, such as a shortfall.
    """
    if kind not in SEVERITY:
        raise ValueError(f"unknown conflict kind {kind!r}")
    return {
        "kind": kind,
        "severity": SEVERITY[kind],
        "task_ids": [str(t) for t in task_ids],
        "people": people,
        "sentence": sentence,
        "due_on": due_on.isoformat() if due_on else None,
        **extra,
    }


def sort_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """By kind in :data:`KINDS` order, then by due day. The sort is stable,
    so rows of one kind and one day keep the order they were built in. A row
    with no day sorts last in its kind."""
    order = {k: i for i, k in enumerate(KINDS)}
    return sorted(
        rows,
        key=lambda r: (order.get(r["kind"], len(order)), r.get("due_on") or "9999-12-31"),
    )


def summarise(rows: list[dict[str, Any]], kinds: Iterable[str]) -> dict[str, Any]:
    """``total``, ``by_kind`` and the capped, sorted rows.

    ``kinds`` is the vocabulary THIS caller may see, so a kind the caller may
    not see is not even a zero in ``by_kind``.
    """
    shown = tuple(kinds)
    ordered = sort_rows([r for r in rows if r["kind"] in shown])
    by_kind = {k: 0 for k in shown}
    for row in ordered:
        by_kind[row["kind"]] += 1
    return {
        "total": len(ordered),
        "by_kind": by_kind,
        "truncated": len(ordered) > MAX_ROWS,
        "rows": ordered[:MAX_ROWS],
    }


__all__ = [
    "HR_KINDS",
    "KINDS",
    "MAX_ROWS",
    "PARALLEL_MAX_TASKS",
    "PARALLEL_MIN_ROOTS",
    "PARALLEL_MIN_TASKS",
    "SEVERITY",
    "busiest_parallel_day",
    "conflict_row",
    "dependency_conflict",
    "interval",
    "parallel_days",
    "sort_rows",
    "start_day",
    "summarise",
    "utc_day",
]
