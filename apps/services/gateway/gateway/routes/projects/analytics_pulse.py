"""Projects · pulse — one card for each person, in a report (WS-27bn R3d).

Spec: ``project-docs/specs/projects_reports.md`` §8 R3d.

The ``pulse`` report section. Each card shows one person who holds open work
in the scope: a load bar, a status, the top focus tasks and a "Needs help"
mark. There is no route yet. The render awaits :func:`pulse_body`, as it
awaits ``hygiene_body``.

⚠️ **The rows ARE the capacity rows (edit E3).** :func:`pulse_body` calls
``capacity_body`` with the same scope, the same reader and the same ``today``,
and keeps its ``kind == "person"`` rows. So a card's ``pill`` is the capacity
row's ``pill``, and the two cannot disagree. An agent and the unassigned row
get no card.

⚠️ **Three rules come from elsewhere, and none is written again here.**
"Blocked" is ``analytics.blocked_clause()``, which the ``stuck`` route calls.
"Stale" is the ``stale_in_progress`` predicate of ``HYGIENE_KINDS``, with
``STALE_DAYS``. "Has room" is the ``idle`` flag of ``workload.classify``, which
applies ``IDLE_FRACTION``. A source test holds each of the three.

⚠️ **The HR half (edit E4).** Hours, the pill, the status and ``has_room``
need ``admin:members:read``, or the row must be the reader's own row. That is
the self door of ``people_center_app.md`` §4.2. Any other row carries the task
half only, and each HR key is ABSENT, never null.

⚠️ **Private notes stay on the reader's own row (owner Q6).** The SQL reads
``pm_task_personal`` for the READER's address only, and never for the address
of the row it builds. So an admin never sees the waiting items of another
member, or a task that member scheduled today. The member's own render shows
both.

⚠️ **Who sees which card (edit E5).** An admin sees every card, up to
``MAX_PEOPLE``. Any other reader sees their own card only. The filter runs
HERE, before the body leaves the server, and ``hidden_people`` counts the cards
the filter removed. Before R5 this is a strict subset of §7.1.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from gateway.routes.projects.analytics import (
    HYGIENE_KINDS,
    MAX_PEOPLE,
    STALE_DAYS,
    blocked_clause,
    load_open_where,
    load_params,
    scope_clause,
)
from gateway.routes.projects.analytics_capacity import capacity_body
from gateway.routes.projects.core import STARTED_CATEGORY
from gateway.routes.tasks.core import can_read_hr_fields
from gateway.work_schedule import absent_on
from sqlalchemy import text

#: The most focus tasks one card names. ``focus_total`` counts them all.
FOCUS_MAX = 5

#: The most waiting items the reader's own card names. ``waiting_count``
#: counts them all.
WAITING_MAX = 5

#: The three reasons a person needs help, in the order a card prints them.
HELP_REASONS: tuple[str, ...] = ("blocked", "stale", "waiting_overdue")

#: The keys of the HR half of a card. Each one is ABSENT from a row that the
#: reader may not see the HR half of. The first five are keys of
#: ``analytics_capacity.HR_KEYS``, and the last two are the card's own.
PULSE_HR_KEYS: tuple[str, ...] = (
    "pill",
    "pill_reason",
    "hours_basis",
    "working_hours_this_week",
    "committed_hours_this_week",
    "status",
    "has_room",
)

#: The keys that only the reader's own row carries (owner Q6).
PRIVATE_KEYS: tuple[str, ...] = ("waiting_count", "waiting")

#: What the section says about the reason it does not check yet.
HELP_NOTE = (
    'The report does not check "behind two runs in a row" yet. It needs the'
    " stored runs of slice R4."
)

#: "Stale", as the hygiene section says it. One predicate, two sections.
_STALE = dict(HYGIENE_KINDS)["stale_in_progress"]

#: A focus task: in progress, or due today or tomorrow, on the UTC calendar.
_FOCUS_WHEN = (
    "(s.category = :started_cat"
    " OR (t.due_at AT TIME ZONE 'UTC')::date"
    "    BETWEEN CAST(:today AS date) AND CAST(:today AS date) + 1)"
)

#: The READER scheduled task ``t`` for today. ⚠️ ``:me`` is the reader, and
#: only the reader. Never bind the address of the row here (owner Q6).
_MINE_TODAY = (
    "EXISTS (SELECT 1 FROM pm_task_personal tp"
    "         WHERE tp.task_id = t.id"
    "           AND lower(tp.member_email) = :me"
    "           AND (tp.scheduled_start AT TIME ZONE 'UTC')::date"
    "               = CAST(:today AS date))"
)


def pulse_counts_sql(open_where: str) -> str:
    """Each holder's blocked and stale open tasks, in one statement."""
    return (
        f"SELECT lower(a.assignee) AS who,"
        f"       count(*) FILTER (WHERE {blocked_clause()}) AS blocked,"
        f"       count(*) FILTER (WHERE {_STALE}) AS stale"
        f"  FROM pm_tasks t"
        f"  JOIN pm_task_statuses s ON s.id = t.status_id"
        f"  JOIN pm_task_assignees a ON a.task_id = t.id"
        f" WHERE {open_where}"
        f"   AND lower(a.assignee) = ANY(CAST(:holders AS text[]))"
        f" GROUP BY 1"
    )


def pulse_focus_sql(open_where: str) -> str:
    """Up to ``:focus_max`` focus tasks for each holder, and the count of all.

    The soonest due date comes first, and a task with no due date comes
    last. On the reader's own row, a task the reader scheduled for today
    joins the focus too.
    """
    mine = f"(lower(a.assignee) = :me AND {_MINE_TODAY})"
    return (
        "SELECT who, id, title, task_number, project_name, due_at,"
        "       in_progress, scheduled, n"
        "  FROM ("
        "  SELECT lower(a.assignee) AS who, t.id, t.title, t.task_number,"
        "         p.name AS project_name, t.due_at,"
        "         (s.category = :started_cat) AS in_progress,"
        f"        {mine} AS scheduled,"
        "         row_number() OVER ("
        "           PARTITION BY lower(a.assignee)"
        "           ORDER BY t.due_at ASC NULLS LAST, t.created_at, t.id"
        "         ) AS rn,"
        "         count(*) OVER (PARTITION BY lower(a.assignee)) AS n"
        "    FROM pm_tasks t"
        "    JOIN pm_task_statuses s ON s.id = t.status_id"
        "    JOIN pm_task_assignees a ON a.task_id = t.id"
        "    LEFT JOIN pm_projects p ON p.id = t.project_id"
        f"  WHERE {open_where}"
        "     AND lower(a.assignee) = ANY(CAST(:holders AS text[]))"
        f"    AND ({_FOCUS_WHEN} OR {mine})"
        "  ) f"
        " WHERE rn <= :focus_max"
        " ORDER BY who, rn"
    )


def pulse_waiting_sql(open_where: str) -> str:
    """The READER's overdue waiting items over open work in scope (Q6).

    A waiting item counts when ``waiting_on`` is set and ``expected_by`` is
    before today. ⚠️ ``:me`` is the reader. This statement never runs for
    the row of another person.
    """
    return (
        "SELECT t.id, t.title, t.task_number, p.name AS project_name,"
        "       tp.expected_by, count(*) OVER () AS n"
        "  FROM pm_task_personal tp"
        "  JOIN pm_tasks t ON t.id = tp.task_id"
        "  JOIN pm_task_statuses s ON s.id = t.status_id"
        "  LEFT JOIN pm_projects p ON p.id = t.project_id"
        f" WHERE {open_where}"
        "   AND lower(tp.member_email) = :me"
        "   AND tp.waiting_on IS NOT NULL"
        "   AND tp.expected_by IS NOT NULL"
        "   AND (tp.expected_by AT TIME ZONE 'UTC')::date < CAST(:today AS date)"
        " ORDER BY tp.expected_by, t.id"
        " LIMIT :waiting_max"
    )


def on_leave(absences: Any, today: date) -> bool:
    """True when a FULL absence covers ``today``. A partial one keeps the pill.

    ⚠️ ``capacity_body`` sends each absence with ISO date STRINGS
    (``absences_in_window``), and ``absent_on`` drops a span whose dates are
    not ``date`` objects. So the strings go back to dates here, or nobody is
    ever on leave.
    """
    spans: list[dict[str, Any]] = []
    for raw in absences or []:
        if not isinstance(raw, dict):
            continue
        try:
            starts = date.fromisoformat(str(raw.get("starts_on")))
            ends = date.fromisoformat(str(raw.get("ends_on")))
        except ValueError:
            continue
        spans.append({"starts_on": starts, "ends_on": ends, "kind": raw.get("kind")})
    hit = absent_on(today, spans)
    return hit is not None and hit.get("kind") != "partial"


def pulse_row(
    cap_row: dict[str, Any],
    *,
    hr: bool,
    today: date,
    blocked: int = 0,
    stale: int = 0,
    focus: list[dict[str, Any]] | None = None,
    focus_total: int = 0,
    waiting: list[dict[str, Any]] | None = None,
    waiting_count: int = 0,
) -> dict[str, Any]:
    """One card, from one capacity row.

    ``hr`` is True for a reader with ``admin:members:read``, and for the
    reader's own row (the self door). With ``hr`` False the row carries the
    task half only: no key of :data:`PULSE_HR_KEYS` is set.

    ``waiting`` is None for every row but the reader's own. Only then does the
    row carry :data:`PRIVATE_KEYS` and the ``waiting_overdue`` reason.
    """
    row: dict[str, Any] = {
        "assignee": cap_row.get("assignee"),
        "name": cap_row.get("name"),
        "open_tasks": int(cap_row.get("open_tasks") or 0),
        "overdue": int(cap_row.get("overdue") or 0),
        "blocked_count": int(blocked),
        "stale_count": int(stale),
        "focus": list(focus or []),
        "focus_total": int(focus_total),
    }
    if hr and "pill" in cap_row:
        away = on_leave(cap_row.get("absences"), today)
        status = "on_leave" if away else cap_row["pill"]
        row["pill"] = cap_row["pill"]
        row["pill_reason"] = cap_row.get("pill_reason")
        row["hours_basis"] = bool(cap_row.get("hours_basis"))
        row["working_hours_this_week"] = cap_row.get("working_hours_this_week")
        if "committed_hours_this_week" in cap_row:
            row["committed_hours_this_week"] = cap_row["committed_hours_this_week"]
        row["status"] = status
        # The `idle` flag applies IDLE_FRACTION. Nobody on leave has room.
        row["has_room"] = (
            "idle" in (cap_row.get("flags") or []) and status != "on_leave"
        )
    reasons: list[str] = []
    if row["blocked_count"] > 0:
        reasons.append("blocked")
    if row["stale_count"] > 0:
        reasons.append("stale")
    if waiting is not None:
        row["waiting_count"] = int(waiting_count)
        row["waiting"] = list(waiting)
        if waiting_count > 0:
            reasons.append("waiting_overdue")
    row["help_reasons"] = reasons
    row["needs_help"] = bool(reasons)
    return row


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


async def pulse_body(
    db: Any,
    vis: Any,
    user: Any,
    *,
    project_id: str | None,
    include_subtree: bool,
    today: date | None = None,
) -> dict[str, Any]:
    """The ``pulse`` report section, on a session and a visibility the caller
    resolved (`projects_reports.md` §8 R3d).

    ``today`` is ONE UTC day. It goes to ``capacity_body`` and to each query
    here, so the pill, the leave check, the focus and the waiting items read
    the same day. The section ignores the report period.
    """
    today = today or datetime.now(UTC).date()
    hr_grant = can_read_hr_fields(user)
    me = str(getattr(user, "email", "") or "").strip().lower()

    # ⚠️ `hr_visible=True` for every reader: the self door needs the HR half
    # of the reader's own row. The rows of other people leave this function
    # only for an admin (the E5 filter below), so a member never receives
    # the hours of anybody else.
    cap = await capacity_body(
        db, vis,
        hr_visible=True,
        project_id=project_id,
        include_subtree=include_subtree,
        today=today,
    )
    people = [r for r in cap["rows"] if r.get("kind") == "person"]
    people_total = len(people)
    if hr_grant:
        shown = people[:MAX_PEOPLE]
        hidden = 0
    else:
        shown = [r for r in people if (r.get("assignee") or "") == me]
        hidden = people_total - len(shown)

    holders = [str(r["assignee"]) for r in shown]
    counts: dict[str, Any] = {}
    focus: dict[str, list[dict[str, Any]]] = {}
    focus_total: dict[str, int] = {}
    waiting: list[dict[str, Any]] = []
    waiting_count = 0
    if holders:
        scope_sql = await scope_clause(db, vis, project_id, include_subtree)
        open_where = load_open_where(scope_sql, vis)
        base = load_params(vis, project_id)
        counts = {
            str(r.who): r
            for r in (await db.execute(
                text(pulse_counts_sql(open_where)),
                {**base, "holders": holders, "started_cat": STARTED_CATEGORY,
                 "stale_days": STALE_DAYS},
            )).fetchall()
        }
        for r in (await db.execute(
            text(pulse_focus_sql(open_where)),
            {**base, "holders": holders, "started_cat": STARTED_CATEGORY,
             "today": today, "me": me, "focus_max": FOCUS_MAX},
        )).fetchall():
            who = str(r.who)
            focus_total[who] = int(r.n or 0)
            focus.setdefault(who, []).append({
                "id": str(r.id),
                "title": r.title,
                "task_number": (
                    int(r.task_number) if r.task_number is not None else None
                ),
                "project_name": r.project_name,
                "due_at": _iso(r.due_at),
                "in_progress": bool(r.in_progress),
                # True only on the reader's own row: `:me` is the reader.
                **({"scheduled_today": True} if r.scheduled else {}),
            })
        if me in holders:
            for r in (await db.execute(
                text(pulse_waiting_sql(open_where)),
                {**base, "me": me, "today": today, "waiting_max": WAITING_MAX},
            )).fetchall():
                waiting_count = int(r.n or 0)
                waiting.append({
                    "id": str(r.id),
                    "title": r.title,
                    "task_number": (
                        int(r.task_number) if r.task_number is not None else None
                    ),
                    "project_name": r.project_name,
                    "expected_by": _iso(r.expected_by),
                })

    rows = []
    for cap_row in shown:
        who = str(cap_row["assignee"])
        own = who == me
        c = counts.get(who)
        rows.append(pulse_row(
            cap_row,
            hr=hr_grant or own,
            today=today,
            blocked=int(getattr(c, "blocked", 0) or 0) if c else 0,
            stale=int(getattr(c, "stale", 0) or 0) if c else 0,
            focus=focus.get(who, []),
            focus_total=focus_total.get(who, 0),
            waiting=waiting if own else None,
            waiting_count=waiting_count if own else 0,
        ))

    return {
        "today": today.isoformat(),
        "stale_days": STALE_DAYS,
        # The READER's grant. A panel decides the HR half per row, by the
        # keys the row carries, because the reader's own row has it anyway.
        "hr_visible": hr_grant,
        "people_total": people_total,
        "hidden_people": hidden,
        "help_note": HELP_NOTE,
        "rows": rows,
    }
