"""Capacity — one person's hours against their open work (WS-27bm S7a).

Spec: ``project-docs/specs/projects_ai_chat.md`` §13.3 ·
``project-docs/specs/people_center_app.md`` §5.7.2 · **D-PC-14**.

**The row arithmetic, in one place, for two callers.** The People dashboard
(``routes/people/dashboard.py``) and the Projects capacity read
(``routes/projects/analytics_capacity.py``) both turn "these open tasks, this
schedule, these absences" into hours, spare hours, at-risk tasks and a pill.
Before S7a the arithmetic lived inside the dashboard's ``_row``. A second copy
in the Projects package would be a second answer to "does this person have
the hours", and the two would disagree the first time either changed.

**A leaf module, outside both route packages**, for the reason
:mod:`gateway.work_schedule` and :mod:`gateway.workload` give. The People
package imports from the Projects package, so a helper that lived in either
one would close an import cycle.

**It computes. It decides nothing about who may see the answer.** Every
figure comes back, and each caller projects what its audience may read. The
dashboard is HR-gated as a whole. The capacity route drops the HR keys for a
caller without ``admin:members:read``. ``hours_basis`` travels so each caller
can say when the hours mean nothing.

⚠️ **Two windows, and they are not the same window** (§13.3 rule 3). The pill
compares the Monday-to-Sunday week with the contracted week, because that is
what the People dashboard's pill means. Spare hours and the at-risk walk use
the ``horizon_days`` window that starts today. :func:`week_window` and
:func:`horizon_window` name both, so a caller can print what it measured.

Everything here except the two readers at the end is pure. The readers take
the session they are handed and never acquire one, so this module adds no
connection site (R5).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy import text

from gateway.work_schedule import (
    _absence_spans,
    contracted_hours_per_week,
    working_hours_between,
)
from gateway.workload import (
    HORIZON_DAYS,
    as_date,
    at_risk_tasks,
    classify,
    hours_of,
)

#: The narrowest and the widest horizon a caller may ask for. One day is the
#: smallest window that still holds a deadline. Ninety days is a quarter, and
#: past a quarter the estimates and the schedule stop being worth trusting —
#: the same ceiling `analytics.LEAVING_HORIZON_DAYS` uses.
MIN_HORIZON_DAYS = 1
MAX_HORIZON_DAYS = 90

#: How many skills travel on a capacity row. Enough to say what a person can
#: do, few enough to read in one line of a chat answer.
TOP_SKILLS = 5

#: Skill levels, strongest first, for the order the skills travel in. The
#: values are migration 176's CHECK. An unassessed skill (NULL) comes last,
#: because "not assessed" is honest and says less than any level does.
_LEVEL_ORDER: dict[str | None, int] = {
    "expert": 0,
    "proficient": 1,
    "working": 2,
    "learning": 3,
    None: 4,
}


def week_window(today: date) -> tuple[date, date]:
    """This Monday and this Sunday — the pill's window.

    Monday to Sunday, so "this week" means the same thing to everybody who
    looks at the same row. It is the window ``person_availability`` and the
    People dashboard already use.
    """
    monday = today - timedelta(days=today.isoweekday() - 1)
    return monday, monday + timedelta(days=6)


def horizon_window(today: date, horizon_days: int = HORIZON_DAYS) -> tuple[date, date]:
    """Today and ``horizon_days`` later — the spare-hours and at-risk window."""
    return today, today + timedelta(days=int(horizon_days))


def dated_until(today: date, horizon_days: int = HORIZON_DAYS) -> date:
    """The EXCLUSIVE upper bound for a dated-task fetch: ``due_at < this``.

    ⚠️ **One bound for both callers** (the dashboard and the capacity route),
    and it must cover two windows at once:

    * **this Sunday**, because the pill (:func:`gateway.workload.classify`)
      reads every estimate due in the Monday-to-Sunday week. A fetch bounded
      at a one-day horizon dropped Friday's work, and a full week read idle.
    * **the horizon's LAST day**, because :func:`person_capacity` and
      :func:`gateway.workload.at_risk_tasks` treat ``due <= horizon_end`` as
      inside. A bound of ``today + horizon_days`` dropped a task due on
      exactly that day, which the dashboard counted.

    So it is the later of the two ends, plus one day.
    """
    _, sunday = week_window(today)
    _, horizon_end = horizon_window(today, horizon_days)
    return max(sunday, horizon_end) + timedelta(days=1)


def _field(source: Any, name: str) -> Any:
    """One aggregate, read from a result row or from a plain dict."""
    if source is None:
        return None
    if isinstance(source, dict):
        return source.get(name)
    return getattr(source, name, None)


def _due_of(task: dict[str, Any]) -> date | None:
    """The day a dated task falls on.

    ``_due`` first, because the dashboard stores it beside the timestamp so
    the week sum and the at-risk walk agree about the day. Two truncations of
    one timestamp is how a task lands in one figure and not in the other.
    """
    due = task.get("_due")
    return due if due is not None else as_date(task.get("due_at"))


def person_capacity(
    *,
    schedule: dict[str, Any] | None,
    spans: list[dict[str, Any]] | None,
    totals: Any,
    dated: list[dict[str, Any]],
    today: date,
    horizon_days: int = HORIZON_DAYS,
) -> dict[str, Any]:
    """One person's figures, from aggregates the caller already fetched.

    ``totals`` carries ``open_tasks``, ``mins``, ``unestimated``, ``overdue``
    and ``next_due`` for every open task the person holds that the caller
    counts. ``dated`` is the same person's open tasks with a due date inside
    the horizon or already past it. ``schedule`` is ``None`` for somebody the
    directory has no row for, and every hours figure is then ``None`` too.

    No query and no per-row round trip. That is what keeps each caller's read
    a fixed number of statements, whatever the headcount.
    """
    open_tasks = int(_field(totals, "open_tasks") or 0)
    mins = int(_field(totals, "mins") or 0)
    unestimated = int(_field(totals, "unestimated") or 0)
    overdue = int(_field(totals, "overdue") or 0)
    next_due = _field(totals, "next_due")

    _, sunday = week_window(today)
    _, horizon_end = horizon_window(today, horizon_days)

    # This week's commitment: what is overdue, plus what falls due on or
    # before Sunday. §5.7.2 says "for the week". Every open task ever,
    # against one week of hours, compares a backlog to a week and calls
    # everybody overloaded.
    week_hours = sum(
        hours_of(t.get("estimate_mins")) for t in dated
        if (d := _due_of(t)) is not None and d <= sunday
    )
    horizon_hours = sum(
        hours_of(t.get("estimate_mins")) for t in dated
        if (d := _due_of(t)) is not None and d <= horizon_end
    )

    contracted = contracted_hours_per_week(schedule) if schedule else 0.0
    available_week = (
        working_hours_between(schedule, today, sunday, spans) if schedule else None
    )
    available_horizon = (
        working_hours_between(schedule, today, horizon_end, spans)
        if schedule else None
    )
    risky = (
        at_risk_tasks(schedule, dated, spans, today, horizon_days=horizon_days)
        if schedule else []
    )

    verdict = classify({
        "open_tasks": open_tasks, "unestimated": unestimated,
        "overdue": overdue, "contracted_hours": contracted,
        "committed_this_week": week_hours, "at_risk": risky,
    })

    return {
        "open_tasks": open_tasks,
        "overdue": overdue,
        "unestimated": unestimated,
        "mins": mins,
        "next_due": next_due,
        "committed_hours": round(mins / 60.0, 1),
        "committed_this_week": round(week_hours, 1),
        "committed_horizon": round(horizon_hours, 1),
        "contracted_hours": contracted,
        "available_this_week": available_week,
        "available_horizon": available_horizon,
        # Spare is never negative here. A shortfall is a real finding, and
        # `at_risk` states it per task with its own number. A negative spare
        # figure beside that list would say the same thing a second way.
        "spare_this_week": (
            None if available_week is None
            else round(max(0.0, available_week - week_hours), 1)
        ),
        "spare_horizon": (
            None if available_horizon is None
            else round(max(0.0, available_horizon - horizon_hours), 1)
        ),
        "at_risk": risky,
        "pill": verdict["pill"],
        "reason": verdict["reason"],
        "flags": verdict["flags"],
        # ⚠️ The one the callers must read. False means no open task carries
        # an estimate, or there is no contracted week to compare against, and
        # then an hours figure is a zero that reads as "free".
        "hours_basis": verdict["hours_basis"],
        "note": verdict["note"],
    }


def absences_in_window(
    spans: list[dict[str, Any]] | None, start: date, end: date,
) -> list[dict[str, Any]]:
    """The absences that touch ``start``..``end``, in the arithmetic's shape.

    Filtered through :func:`gateway.work_schedule._absence_spans`, the one
    place that knows which stored rows the hours arithmetic can use, so the
    list a caller prints is the list the hours were reduced by.
    """
    out: list[dict[str, Any]] = []
    for span in _absence_spans(spans):
        if span["starts_on"] <= end and span["ends_on"] >= start:
            out.append({
                "kind": span.get("kind"),
                "starts_on": span["starts_on"].isoformat(),
                "ends_on": span["ends_on"].isoformat(),
            })
    return out


# ── Readers — they take a session, and never open one ──────────────────────


async def absences_for(db: Any, person_ids: list[str]) -> dict[str, list[dict]]:
    """Every current and future absence span for these people, by person.

    One statement, not one per person. **Best-effort**, for the reason
    :func:`gateway.routes.people.absences.away_today` gives: a database one
    deploy behind migration 174 should answer "nobody is away" rather than
    fail the whole read.
    """
    if not person_ids:
        return {}
    try:
        rows = (await db.execute(text(
            "SELECT person_id, starts_on, ends_on, kind, hours_per_day "
            "  FROM people_absences "
            " WHERE person_id = ANY(CAST(:ids AS uuid[])) "
            "   AND ends_on >= CURRENT_DATE"), {"ids": person_ids})).fetchall()
    except Exception:
        return {}
    out: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        out.setdefault(str(row.person_id), []).append({
            "starts_on": row.starts_on, "ends_on": row.ends_on,
            "kind": row.kind, "hours_per_day": row.hours_per_day})
    return out


async def skills_for(
    db: Any, person_ids: list[str], *, limit: int = TOP_SKILLS,
) -> dict[str, list[dict[str, Any]]]:
    """Each person's strongest skills, from ``people_skills`` (D-PC-6).

    The child table, not the ``people.skills[]`` cache, because only the table
    carries the level — and "knows Python" and "is expert in Python" are the
    difference between who owns a task and who reviews it.

    Strongest level first, then the most recently used, then by name. That is
    an order of one person's OWN skills, never a ranking of people against each
    other (D-PC-14). Best-effort for the same reason as :func:`absences_for`.
    """
    if not person_ids:
        return {}
    try:
        rows = (await db.execute(text(
            "SELECT person_id, skill, level, last_used_year "
            "  FROM people_skills "
            " WHERE person_id = ANY(CAST(:ids AS uuid[]))"),
            {"ids": person_ids})).fetchall()
    except Exception:
        return {}
    grouped: dict[str, list[Any]] = {}
    for row in rows:
        grouped.setdefault(str(row.person_id), []).append(row)
    out: dict[str, list[dict[str, Any]]] = {}
    for pid, found in grouped.items():
        found.sort(key=lambda r: (
            _LEVEL_ORDER.get(r.level, len(_LEVEL_ORDER)),
            -(int(r.last_used_year) if r.last_used_year else 0),
            str(r.skill or "").lower(),
        ))
        out[pid] = [
            {"skill": r.skill, "level": r.level}
            for r in found[:limit] if (r.skill or "").strip()
        ]
    return out
