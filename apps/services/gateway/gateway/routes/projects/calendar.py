"""Projects · the calendar window (WS-27q).

Spec: ``project-docs/specs/project_management_app.md`` §11.2 item 9, §11.16.

    GET /projects/calendar?from=2026-08-01&to=2026-09-01   → every task in view

*"The third view ClickUp users actually use, after list and board."*

**A window, not a page — and that is the whole reason this is a new endpoint.**
`/projects/tasks` is paginated, which is right for a list and catastrophic for a
calendar: a month with 90 tasks read at `page_size=50` draws forty of them and
leaves the rest of the days looking EMPTY. A short page announces itself ("page
2 of 3"); a short month does not. So the window is the unit, everything in it
comes back, and when the cap is hit the response SAYS so rather than quietly
handing back a plausible-looking month.

**`start_date` has existed since migration 146 and no surface has ever shown
it.** The same complaint §11.14 makes about links: a column that cannot be seen
is a promise the product does not keep. A calendar is the view that needs it,
because a task is a BAR from its start to its due date, not a dot on one day.

**Overlap, not equality.** A task that starts on Monday and is due on Friday
belongs on Wednesday's cell too. Filtering on `due_at BETWEEN` — the obvious
implementation — puts it on Friday alone, which is exactly the week where
somebody looks at Wednesday and concludes they are free.

**A task with NEITHER date is not on the calendar, and the count says so.**
Dropping them silently is how a calendar comes to look like the whole workspace
when it is showing a third of it; `undated` is what lets the view admit it.

**The window is read in UTC and the client is expected to ask for slack.** A
`start_date` is a floating calendar date and `due_at` is an instant, so no
single frame makes both exact — a `due_at` of 23:00Z sits on the next day in
IST and the previous one in PST. Rather than pretend, the server OVER-selects
against a UTC reading of the window and the browser, which is the only party
that knows the viewer's timezone, does the placement. `calendarWindow()` on the
client adds the day of slack that makes that safe.

**No new write path.** Dragging a task to another day is a `PATCH /tasks/{id}`
of `start_date` and `due_at` — the same validation, the same `field_change`
activity, the same revert. A `POST /calendar/move` would be a second way to
edit a task, which is how the two start disagreeing about what is allowed.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException, Query
from gateway.routes.projects.core import (
    TaskModel,
    _tenant_session,
    actor,
    load_visible_project,
    resolve_visibility,
    router,
    row_to_dict,
    task_visibility_clause,
    triage_exclusion_clause,
)
from gateway.routes.projects.filters import (
    attach_assignees,
    attach_relation_counts,
    build_task_filters,
    window_links,
)
from sqlalchemy import text

#: The widest window that may be asked for, in days.
#:
#: A year plus slack, so a year view is possible and an unbounded scan of an
#: imported workspace is not. Refused with a 422 rather than clamped: a client
#: that asked for five years and silently got one would draw four empty ones.
MAX_WINDOW_DAYS = 400

#: The most tasks one window returns.
#:
#: Reached, the response sets `truncated` and the view says so. **Silence is the
#: only unacceptable behaviour here** — a calendar missing a third of its tasks
#: looks exactly like a calendar with fewer tasks, and nobody investigates a
#: quiet week.
MAX_WINDOW_ROWS = 1000


def parse_day(raw: str, *, field: str) -> date:
    """A ``YYYY-MM-DD`` query parameter → a real ``date``.

    A **date**, not a timestamp: the window's unit is the day, and accepting
    `2026-08-01T13:45:00+05:30` would invite the caller to believe the edge is
    honoured to the minute when the whole contract is that the server
    over-selects and the browser places (see the module docstring).

    A bad value is a 422 naming the format, for the reason `parse_when` gives:
    `from=august` is the client's mistake and deserves to be told so.
    """
    try:
        return date.fromisoformat(raw.strip())
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"'{raw}' is not a valid {field}. "
                   f"Expected a calendar date, e.g. 2026-08-01.",
        ) from None


def window_bounds(raw_from: str, raw_to: str) -> tuple[datetime, datetime]:
    """The window's half-open instant bounds, ``[from, to)``, read in UTC.

    **Half-open on purpose.** A month runs `2026-08-01` to `2026-09-01`, so two
    consecutive windows tile without a task landing in both — an inclusive end
    would double-count every task due on the last day, and a calendar that
    disagrees with itself across a page turn is worse than one that is slightly
    conservative at the edge.
    """
    start = parse_day(raw_from, field="from")
    end = parse_day(raw_to, field="to")
    if end <= start:
        raise HTTPException(
            status_code=422,
            detail=f"'to' ({end}) must be after 'from' ({start}).",
        )
    if (end - start).days > MAX_WINDOW_DAYS:
        raise HTTPException(
            status_code=422,
            detail=f"That window is {(end - start).days} days. "
                   f"The maximum is {MAX_WINDOW_DAYS}.",
        )
    return (
        datetime.combine(start, datetime.min.time(), tzinfo=UTC),
        datetime.combine(end, datetime.min.time(), tzinfo=UTC),
    )


#: A task's scheduled interval overlaps the window.
#:
#: `start` is `coalesce(start_date, due_at)` and `end` is `coalesce(due_at,
#: start_date)`, so a task with one date is a POINT and a task with both is a
#: bar. Overlap is then the standard `start < :to AND end >= :from`.
#:
#: **A task with neither date drops out on its own**, because both coalesces are
#: NULL and every comparison against NULL is NULL rather than TRUE. That is
#: correct and it is also invisible, so `_UNDATED_SQL` counts them separately
#: and a test pins the behaviour rather than trusting the reading.
#:
#: `start_date` is anchored to UTC explicitly rather than through `CAST(… AS
#: timestamptz)`, which would silently use the connection's `TimeZone` — a
#: session setting no caller controls and no test would notice changing.
OVERLAPS = (
    "coalesce(CAST(t.start_date AS timestamp) AT TIME ZONE 'UTC', t.due_at)"
    " < :window_to"
    " AND coalesce(t.due_at, CAST(t.start_date AS timestamp) AT TIME ZONE 'UTC')"
    " >= :window_from"
)

#: Neither date set — the tasks a calendar structurally cannot show.
UNDATED = "t.start_date IS NULL AND t.due_at IS NULL"


def _subtree_clause() -> str:
    return (
        "t.project_id IN ("
        "  WITH RECURSIVE sub AS ("
        "    SELECT id FROM pm_projects WHERE id = CAST(:pid AS uuid)"
        "    UNION ALL"
        "    SELECT p.id FROM pm_projects p JOIN sub s"
        "      ON p.parent_project_id = s.id"
        "  ) SELECT id FROM sub)"
    )


@router.get("/calendar")
async def get_calendar(
    user: UserContext = Depends(get_current_user),
    # `from` is a Python keyword, so the wire name is set by alias rather than
    # by renaming the query parameter to something a caller would have to guess.
    date_from: str = Query("", alias="from"),
    date_to: str = Query("", alias="to"),
    project_id: str | None = None,
    include_subtree: bool = False,
    # The board's filters, verbatim. A calendar that ignored them would show
    # everything the moment somebody switched view, which reads as a bug in the
    # filter rather than an absence in the calendar.
    status_id: str | None = None,
    status_category: str | None = None,
    assignee: str | None = None,
    assignees: str | None = None,
    unassigned: bool = False,
    overdue: bool = False,
    importance_gte: int | None = None,
    q: str | None = None,
    tags: str | None = None,
    tags_all: str | None = None,
    include_archived: bool = False,
    # WS-27t. Off by default: the calendar draws no arrows and would pay for a
    # query it never reads. A flag rather than a second endpoint because the
    # WINDOW is the resource — calendar and timeline are two renderings of the
    # same question, and the app's standing rule (§11.8) is that a second
    # endpoint per surface is how the filters start disagreeing.
    include_links: bool = False,
    # WS-27u. The intake queue must not leak onto the month either — the ONE
    # predicate is `core.triage_exclusion_clause`, applied below.
    include_triage: bool = False,
    # WS-27bk §9.12.2. ⚠️ Declared HERE as well as on the list, or FastAPI
    # drops it without a word — and the calendar AND the timeline both read
    # this endpoint, so the chip would silently stop applying in two views at
    # once. That reads as the FILTER being broken rather than the view.
    # `test_every_list_filter_is_accepted_by_the_calendar_or_named_as_excluded`
    # is the fence, and it caught exactly this.
    watching: bool = False,
) -> dict:
    """Every visible task whose schedule overlaps ``[from, to)``.

    The filters are the board's, applied by the same pure builder, so switching
    from board to calendar changes the SHAPE of what is on screen and never the
    SET — the rule §11.8 states for list and board, extended to the third view.

    **`due_before` is the one filter deliberately not accepted**, because it
    duplicates the window: two ways to bound the same column that can
    contradict, where the losing one vanishes without a word. `overdue` looks
    like the same objection and is not — "already late" is a fact about the
    status as much as the date, it composes with any window, and dropping it
    would make a board filtered to overdue work show everything the moment
    somebody switched to the calendar.
    """
    window_from, window_to = window_bounds(date_from, date_to)

    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        clauses: list[str] = [task_visibility_clause(vis)]
        params: dict[str, Any] = dict(vis.params)

        if project_id:
            # Seeing the project is required to filter by it (R5): an
            # unreadable id is a 404, never an empty calendar, which would
            # confirm the project exists and is simply quiet.
            await load_visible_project(db, vis, project_id)
            clauses.append(
                _subtree_clause() if include_subtree
                else "t.project_id = CAST(:pid AS uuid)"
            )
            params["pid"] = project_id

        extra_clauses, extra_params = build_task_filters(
            status_id=status_id, status_category=status_category,
            assignee=assignee, assignees=assignees, unassigned=unassigned,
            overdue=overdue, importance_gte=importance_gte, q=q, tags=tags,
            tags_all=tags_all, include_archived=include_archived,
            watching=watching, viewer=actor(user) if watching else None,
        )
        clauses.extend(extra_clauses)
        params.update(extra_params)
        if not include_triage:
            clauses.append(triage_exclusion_clause())

        scoped = " AND ".join(clauses)
        params["window_from"] = window_from
        params["window_to"] = window_to

        rows = (await db.execute(
            text(
                f"SELECT t.* FROM pm_tasks t WHERE {scoped} AND {OVERLAPS} "
                # Sorted so a day's cell is stable between loads: the interval's
                # start, then the task number. An unordered calendar reshuffles
                # every cell on refresh, which reads as the data having changed.
                f"ORDER BY coalesce(t.start_date, CAST(t.due_at AS date)), "
                f"         t.task_number NULLS LAST, t.id "
                f"LIMIT :cap"
            ),
            {**params, "cap": MAX_WINDOW_ROWS + 1},
        )).fetchall()

        truncated = len(rows) > MAX_WINDOW_ROWS
        window_rows = [
            row_to_dict(r, TaskModel) for r in rows[:MAX_WINDOW_ROWS]
        ]
        await attach_assignees(db, window_rows)
        await attach_relation_counts(db, window_rows)

        # Counted with the SAME filters, so "12 unscheduled" means twelve of the
        # tasks you are looking at — not twelve somewhere in the workspace.
        undated = (await db.execute(
            text(f"SELECT count(*) FROM pm_tasks t WHERE {scoped} AND {UNDATED}"),
            params,
        )).scalar() or 0

        return {
            "from": window_from.date().isoformat(),
            "to": window_to.date().isoformat(),
            "rows": window_rows,
            "truncated": truncated,
            "cap": MAX_WINDOW_ROWS,
            "undated": int(undated),
            # Always present, empty when not asked for: a missing key and an
            # empty list read the same to a careless client, and "this window
            # has no dependencies" must not be confused with "nobody asked".
            "links": await window_links(db, window_rows) if include_links else [],
        }


__all__ = [
    "MAX_WINDOW_DAYS",
    "MAX_WINDOW_ROWS",
    "OVERLAPS",
    "UNDATED",
    "parse_day",
    "window_bounds",
]
