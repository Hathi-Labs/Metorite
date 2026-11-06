"""Projects · the one Today rule and the today's-focus mark (WS-MDC CC-2).

Spec: ``project-docs/specs/my_day_command_centre.md`` §9.2 (D-MD-2, D-MD-3),
fences §10. Metorite task 1969.

    GET   /projects/my/today?with_tasks=true   → the Today set (``personal.my_today``)
    PATCH /projects/tasks/{task_id}/personal   → ``focus_on``, under the cap
                                                 (``personal.mark_focus``)

**ONE predicate decides "Today"**, for one member and one local day. A task is
in the set when it is open, visible to the member, and one of four arms holds:

1. ``one_thing``: it is the day's One Thing (``calendar_day_state.one_thing_id``).
2. ``focus``: the member's ``pm_task_personal.focus_on`` equals today.
3. ``scheduled``: the member's block starts today (``scheduled_start``).
4. ``due``: the old rule of ``mine.in_today``. It is assigned to me (the
   ``assigned`` arm of ``core.mine_clause``), its effective disposition is
   NEXT, it is not deferred past today, it has started, and it is due or
   started on or before today. :func:`due_arm_sql` is its SQL form, and
   ``mine.in_today`` stays its Python form for the count with the flag off.

The day is the member's local day: midnight to midnight in the zone of
``personal.stored_zone``, the zone that ``in_today`` reads. A statement binds
``:today``, ``:day_start`` and ``:day_end`` from :class:`TodayWindow`. No
statement here compares a column with the clock.

The Calendar's "To fit" tray is the set minus the tasks with a block today
(:func:`to_fit`). The client twin of the four arms is
``src/lib/shell/todayRule.ts``. ``tests/fixtures/today_rule_cases.json`` holds
the cases, and the pytest and the vitest both read it.

**The cap of the mark** (D-MD-3). The count is the member's marks of the day,
plus the One Thing when it has no mark (:func:`focus_count_sql`). A mark past
the cap answers 409 ``focus_cap_reached``. :func:`focus_room_sql` is the guard
of the one statement that counts and writes the mark, and
``personal.mark_focus`` runs it under :data:`FOCUS_LOCK_SQL`. The One Thing
never refuses, but it counts. A lower cap keeps the marks that exist.

⚠️ **Identity and tenant come from the session (R5, R11).** ``:who`` is the
member of the session and ``:vis_org`` the tenant of the directory. Each arm
reads only the member's own overlay row and day state. Each statement anchors
the tenant on ``pm_tasks.organization_id`` beside FORCE RLS, because the
overlay and the day-state tables take their tenant column from the generated
phases only.

``TODAY_RULE_ORGS`` (default empty) moves the count of the Today chip and its
list to this rule (:func:`today_rule_allowed`). With the flag off, the chip
and its count keep the old rule. The read and the mark exist with the flag
off, and no surface calls them until CC-3 and CC-7.

Fences: ``tests/unit/test_today_rule_r8.py``,
``tests/unit/test_today_focus_cap_r8.py`` and ``src/lib/shell/todayRule.test.ts``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from acb_common import get_settings
from gateway.routes.projects import core
from gateway.routes.projects.core import (
    CLOSING_CATEGORIES,
    Visibility,
    mine_clause,
    task_visibility_clause,
    triage_exclusion_clause,
)
from gateway.routes.projects.due_day import zone_name
from gateway.routes.projects.filters import build_task_filters
from sqlalchemy import text

#: The cap when the member set none (D-MD-3).
DEFAULT_FOCUS_CAP = 3
#: The range of the cap. A write clamps to it, and a read clamps again.
FOCUS_CAP_MIN = 1
FOCUS_CAP_MAX = 10

#: The ``why`` of a row, in the order a row lists it.
WHY_ORDER: tuple[str, ...] = ("one_thing", "focus", "scheduled", "due")

#: The most rows one read returns. ``total`` counts them all.
MAX_ROWS = 200

#: The code of the 409 of a mark past the cap.
FOCUS_CAP_REACHED = "focus_cap_reached"

#: No task. The read counts every mark, so it excludes this id.
NO_TASK = "00000000-0000-0000-0000-000000000000"

#: The transaction lock of one member's marks of one day. Every writer of a
#: mark takes it before the statement that counts and writes.
FOCUS_LOCK_SQL = "SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"


def today_rule_allowed(organization_id: str | None) -> bool:
    """True when ``TODAY_RULE_ORGS`` holds the org, or holds ``*``. Default OFF.

    The one reader of the flag. ``mine.my_summary`` asks here for the count of
    the Today chip and the ids of its list. The org comes from the session,
    never from request input (R5). It reads ``get_settings()`` on each call,
    so a flag reload applies a change with no restart. A fault reads as off.
    """
    org = str(organization_id or "").strip().lower()
    if not org:
        return False
    try:
        raw = str(getattr(get_settings(), "today_rule_orgs", "") or "")
    except Exception:
        return False
    listed = {p.strip().lower() for p in raw.split(",") if p.strip()}
    return "*" in listed or org in listed


def clamp_focus_cap(value: Any) -> int:
    """The cap as a number from 1 to 10. ``None`` and junk read as 3."""
    if value is None or isinstance(value, bool):
        return DEFAULT_FOCUS_CAP
    try:
        number = int(value)
    except (TypeError, ValueError):
        return DEFAULT_FOCUS_CAP
    return max(FOCUS_CAP_MIN, min(FOCUS_CAP_MAX, number))


def clock() -> datetime:
    """The one clock of the Today read and of the mark, so a test can freeze
    it in one place. It is ``core.now``."""
    return core.now()


@dataclass(frozen=True)
class TodayWindow:
    """One member's local day: the date, the zone, and its two ends."""

    today: date
    zone: str
    day_start: datetime
    day_end: datetime

    @property
    def binds(self) -> dict[str, Any]:
        return {"today": self.today, "day_start": self.day_start, "day_end": self.day_end}


def window_of(zone: Any, at: datetime) -> TodayWindow:
    """The local day of *at* in *zone*, midnight to midnight. An unknown zone
    reads as UTC (``due_day.zone_name``), as ``in_today`` reads it."""
    name = zone_name(zone)
    tz = ZoneInfo(name)
    today = at.astimezone(tz).date()
    return TodayWindow(
        today=today,
        zone=name,
        day_start=datetime.combine(today, time.min, tzinfo=tz),
        day_end=datetime.combine(today + timedelta(days=1), time.min, tzinfo=tz),
    )


# ── The four arms ───────────────────────────────────────────────────────────
#
# Aliases: ``t`` is the task, ``s`` its status, ``p`` the member's own overlay
# row (a LEFT JOIN on ``:who``) and ``proj`` the task's project.

_CLOSED = ", ".join(f"'{c}'" for c in sorted(CLOSING_CATEGORIES))

#: The task is open: not completed, not in a closing lane, and not in my
#: trash. A trashed task is in no list of mine (D77).
OPEN_SQL = (
    "t.completed_at IS NULL"
    f" AND COALESCE(s.category, '') NOT IN ({_CLOSED})"
    " AND p.disposition IS DISTINCT FROM 'TRASH'"
)

#: Arm 1. The member's One Thing of the day. The day state keys on the
#: session's spelling of the address, so the match is in any case.
ONE_THING_SQL = (
    "t.id IN (SELECT d.one_thing_id FROM calendar_day_state d"
    " WHERE lower(d.user_id) = :who AND d.day = CAST(:today AS date)"
    " AND d.one_thing_id IS NOT NULL)"
)

#: Arm 2. My mark, while it equals my today.
FOCUS_SQL = "(p.focus_on IS NOT NULL AND p.focus_on = CAST(:today AS date))"

#: Arm 3. My block starts in my local day.
SCHEDULED_SQL = (
    "(p.scheduled_start IS NOT NULL"
    " AND p.scheduled_start >= CAST(:day_start AS timestamptz)"
    " AND p.scheduled_start < CAST(:day_end AS timestamptz))"
)

#: The effective disposition is NEXT, for a task assigned to me. It is
#: ``personal.effective_disposition`` in SQL for that one answer: a closing
#: lane is DONE, a stated DONE on an open lane is NEXT, any other stated value
#: stays, and with nothing stated a ``backlog`` lane is SOMEDAY.
_EFFECTIVE_NEXT_SQL = (
    f"(COALESCE(s.category, '') NOT IN ({_CLOSED})"
    " AND (p.disposition IN ('NEXT', 'DONE')"
    "      OR (p.disposition IS NULL AND COALESCE(s.category, '') <> 'backlog')))"
)


def due_arm_sql(alias: str = "t") -> str:
    """Arm 4: the rule of ``mine.in_today``, in SQL.

    Assigned to me (the ``assigned`` arm of ``core.mine_clause``), the
    effective disposition is NEXT, not completed, not deferred past today, not
    started after today, and started or due on or before today. "On or before
    today" is "before the start of tomorrow" (``:day_end``), so an overdue task
    is in it. The fixture holds this text and ``in_today`` to one answer.
    """
    return (
        f"({mine_clause(alias, arm='assigned')}"
        f" AND {alias}.completed_at IS NULL"
        f" AND {_EFFECTIVE_NEXT_SQL}"
        " AND (p.defer_until IS NULL OR p.defer_until < CAST(:day_end AS timestamptz))"
        f" AND ({alias}.start_date IS NULL OR {alias}.start_date <= CAST(:today AS date))"
        f" AND ({alias}.start_date IS NOT NULL"
        f"      OR ({alias}.due_at IS NOT NULL"
        f"          AND {alias}.due_at < CAST(:day_end AS timestamptz))))"
    )


DUE_SQL = due_arm_sql("t")

#: The four arms, joined. A task is in the set when one holds.
ARMS_SQL = f"({ONE_THING_SQL} OR {FOCUS_SQL} OR {SCHEDULED_SQL} OR {DUE_SQL})"


def today_where(vis: Visibility) -> str:
    """The WHERE of the set: visible, live, not parked in triage, open, and
    one arm. The visibility is ``core.task_visibility_clause``, which carries
    the tenant. The archive rules are the defaults of ``build_task_filters``,
    the task and its project, as ``mine.mine_where`` composes them."""
    live, _ = build_task_filters()
    return " AND ".join([
        task_visibility_clause(vis), *live, triage_exclusion_clause(), OPEN_SQL, ARMS_SQL,
    ])


_FROM_SQL = (
    " FROM pm_tasks t"
    " JOIN pm_task_statuses s ON s.id = t.status_id"
    " LEFT JOIN pm_task_personal p"
    "        ON p.task_id = t.id AND lower(p.member_email) = :who"
    " LEFT JOIN pm_projects proj ON proj.id = t.project_id"
)

#: The order of the set: the One Thing, then the marks, then the blocks by
#: their start, then the due work by its date, and the id for a tie.
_ORDER_SQL = (
    f" ORDER BY ({ONE_THING_SQL}) DESC, ({FOCUS_SQL}) DESC,"
    " p.scheduled_start ASC NULLS LAST, t.due_at ASC NULLS LAST, t.id"
)


def today_set_sql(vis: Visibility) -> str:
    """One statement: each row of the set, with its block and its arms."""
    return (
        "SELECT t.*, s.category AS status_category, s.name AS workflow_stage,"
        " proj.name AS project_name,"
        " p.disposition AS p_disposition, p.focus_on AS p_focus_on,"
        " p.scheduled_start AS p_scheduled_start, p.scheduled_end AS p_scheduled_end,"
        " EXISTS (SELECT 1 FROM pm_task_assignees ma"
        "          WHERE ma.task_id = t.id AND lower(ma.assignee) = :who) AS is_mine,"
        " (SELECT count(*) FROM pm_task_assignees mc WHERE mc.task_id = t.id)"
        "    AS assignee_count,"
        f" COALESCE({ONE_THING_SQL}, false) AS w_one_thing,"
        f" COALESCE({FOCUS_SQL}, false) AS w_focus,"
        f" COALESCE({SCHEDULED_SQL}, false) AS w_scheduled,"
        f" COALESCE({DUE_SQL}, false) AS w_due,"
        " count(*) OVER () AS total"
        + _FROM_SQL
        + f" WHERE {today_where(vis)}"
        + _ORDER_SQL
        + " LIMIT :limit"
    )


def today_ids_sql(vis: Visibility) -> str:
    """The ids of the set, for the count of the Today chip and its list."""
    return "SELECT t.id" + _FROM_SQL + f" WHERE {today_where(vis)} ORDER BY t.id"


def today_binds(vis: Visibility, window: TodayWindow) -> dict[str, Any]:
    """Every bind of the set. ``:who`` is the member of the session."""
    return {**vis.params, "who": vis.email, **window.binds}


# ── The cap ─────────────────────────────────────────────────────────────────


def focus_count_sql() -> str:
    """The count of the cap, as one SQL value: my marks of the day, plus the
    One Thing when it has no mark of mine.

    ``:focus_task`` is left out of both terms, so the guard of a mark counts
    the OTHER tasks. The read binds :data:`NO_TASK`. Binds ``:focus_who``,
    ``:focus_org`` and ``:focus_on`` (the day). The tenant rides on the task
    row of each term, beside FORCE RLS.
    """
    return (
        "((SELECT count(*) FROM pm_task_personal f"
        "   JOIN pm_tasks ft ON ft.id = f.task_id"
        "                   AND ft.organization_id = CAST(:focus_org AS uuid)"
        "  WHERE lower(f.member_email) = :focus_who"
        "    AND f.focus_on = CAST(:focus_on AS date)"
        "    AND f.task_id <> CAST(:focus_task AS uuid))"
        " + (SELECT count(DISTINCT d.one_thing_id) FROM calendar_day_state d"
        "   JOIN pm_tasks dt ON dt.id = d.one_thing_id"
        "                   AND dt.organization_id = CAST(:focus_org AS uuid)"
        "  WHERE lower(d.user_id) = :focus_who"
        "    AND d.day = CAST(:focus_on AS date)"
        "    AND d.one_thing_id <> CAST(:focus_task AS uuid)"
        "    AND NOT EXISTS (SELECT 1 FROM pm_task_personal g"
        "                     WHERE g.task_id = d.one_thing_id"
        "                       AND lower(g.member_email) = :focus_who"
        "                       AND g.focus_on = d.day)))"
    )


def focus_room_sql() -> str:
    """The value of ``focus_on`` in the one statement that counts and writes.

    The day when the mark adds nothing to the count, or when the count of the
    OTHER tasks is under the cap. A mark adds nothing when the task holds
    today's mark already, or when it is today's One Thing, which counts with
    no mark. NULL when the day is full, and then ``personal.mark_focus``
    answers 409 and the transaction rolls back. So a lower cap keeps a mark
    that exists, and refuses a new one.
    """
    return (
        "(SELECT CAST(:focus_on AS date) WHERE"
        " EXISTS (SELECT 1 FROM pm_task_personal k"
        "          WHERE k.task_id = CAST(:focus_task AS uuid)"
        "            AND lower(k.member_email) = :focus_who"
        "            AND k.focus_on = CAST(:focus_on AS date))"
        " OR EXISTS (SELECT 1 FROM calendar_day_state o"
        "             WHERE lower(o.user_id) = :focus_who"
        "               AND o.day = CAST(:focus_on AS date)"
        "               AND o.one_thing_id = CAST(:focus_task AS uuid))"
        f" OR {focus_count_sql()} < :focus_cap)"
    )


@dataclass(frozen=True)
class FocusGuard:
    """The binds of :func:`focus_room_sql` for one mark. ``personal.mark_focus``
    builds it from the session: the member, the tenant, the task and the cap."""

    who: str
    organization_id: str
    task_id: str
    cap: int

    @property
    def binds(self) -> dict[str, Any]:
        return {
            "focus_who": self.who, "focus_org": self.organization_id,
            "focus_task": self.task_id, "focus_cap": self.cap,
        }


def focus_lock_key(organization_id: str, who: str, day: date) -> str:
    """The key of :data:`FOCUS_LOCK_SQL`: the org, the member and the day."""
    return f"today_focus:{organization_id}:{who}:{day.isoformat()}"


async def focus_cap_of(db: Any, email: str) -> int:
    """``user_settings.today_focus_cap`` of the member in the BOUND tenant,
    clamped. No row and NULL read as 3. The address matches in any case, as
    ``personal.stored_zone`` reads the zone."""
    from gateway.routes.tasks.settings import _BOUND_ORG, ANY_CASE_MATCH

    row = (await db.execute(
        text(f"SELECT today_focus_cap FROM user_settings WHERE organization_id = {_BOUND_ORG}"
             + ANY_CASE_MATCH),
        {"uid": email},
    )).fetchone()
    return clamp_focus_cap(None if row is None else getattr(row, "today_focus_cap", None))


async def focus_count(db: Any, vis: Visibility, day: date) -> int:
    """The count of the cap for the member's *day*: every mark, plus the One
    Thing when it has no mark."""
    found = (await db.execute(
        text(f"SELECT {focus_count_sql()} AS n"),
        {"focus_who": vis.email, "focus_org": vis.organization_id,
         "focus_on": day, "focus_task": NO_TASK},
    )).scalar()
    return int(found or 0)


# ── The read ────────────────────────────────────────────────────────────────


def why_of(row: Any) -> list[str]:
    """The arms that hold for one row, in :data:`WHY_ORDER`."""
    return [name for name in WHY_ORDER if bool(getattr(row, f"w_{name}", False))]


def to_fit(rows: list[dict[str, Any]]) -> list[str]:
    """The Calendar's "To fit" tray: the set minus the tasks with a block
    today. The ids keep the order of the set."""
    return [str(r["task"]["id"]) for r in rows if "scheduled" not in r["why"]]


async def today_ids(db: Any, vis: Visibility, window: TodayWindow) -> list[str]:
    """The ids of the member's set, for the count of the Today chip."""
    found = (await db.execute(text(today_ids_sql(vis)), today_binds(vis, window))).fetchall()
    return [str(r.id) for r in found]


def _row(row: Any) -> dict[str, Any]:
    """One row of the set: the task, its block, and why it is in Today."""
    # Imported here: `personal` imports this module.
    from gateway.routes.projects.core import TaskModel, row_to_dict
    from gateway.routes.projects.personal import effective_disposition

    task = row_to_dict(row, TaskModel)
    task["disposition"] = effective_disposition(
        getattr(row, "p_disposition", None),
        status_category=getattr(row, "status_category", None),
        is_mine=bool(getattr(row, "is_mine", False)),
        has_assignee=int(getattr(row, "assignee_count", 0) or 0) > 0,
    )
    task["is_mine"] = bool(getattr(row, "is_mine", False))
    task["status_category"] = getattr(row, "status_category", None)
    task["workflow_stage"] = getattr(row, "workflow_stage", None)
    task["project_name"] = getattr(row, "project_name", None)
    focus = getattr(row, "p_focus_on", None)
    task["focus_on"] = focus.isoformat() if focus is not None else None
    start = getattr(row, "p_scheduled_start", None)
    end = getattr(row, "p_scheduled_end", None)
    block = None
    if start is not None:
        block = {"start": start.isoformat(), "end": end.isoformat() if end is not None else None}
    return {"task": task, "block": block, "why": why_of(row)}


async def today_answer(db: Any, vis: Visibility, window: TodayWindow) -> dict[str, Any]:
    """The Today set of ``GET /projects/my/today?with_tasks=true``.

    ``rows`` holds each task with its block and ``why``, at most
    :data:`MAX_ROWS`, and ``total`` counts the whole set. ``to_fit`` is the
    tray. ``focus_cap`` and ``focus_count`` are the cap and the count of the
    day. It writes nothing.
    """
    from gateway.routes.projects.filters import attach_agent_state

    found = (await db.execute(
        text(today_set_sql(vis)), {**today_binds(vis, window), "limit": MAX_ROWS},
    )).fetchall()
    rows = [_row(r) for r in found]
    await attach_agent_state(db, [r["task"] for r in rows])
    total = int(getattr(found[0], "total", 0) or 0) if found else 0
    return {
        "rows": rows,
        "total": total,
        "to_fit": to_fit(rows),
        "focus_cap": await focus_cap_of(db, vis.email),
        "focus_count": await focus_count(db, vis, window.today),
    }


__all__ = [
    "DEFAULT_FOCUS_CAP",
    "FOCUS_CAP_MAX",
    "FOCUS_CAP_MIN",
    "FOCUS_CAP_REACHED",
    "FOCUS_LOCK_SQL",
    "MAX_ROWS",
    "WHY_ORDER",
    "FocusGuard",
    "TodayWindow",
    "clamp_focus_cap",
    "clock",
    "due_arm_sql",
    "focus_cap_of",
    "focus_count",
    "focus_count_sql",
    "focus_lock_key",
    "focus_room_sql",
    "to_fit",
    "today_answer",
    "today_ids",
    "today_rule_allowed",
    "today_set_sql",
    "today_where",
    "why_of",
    "window_of",
]
