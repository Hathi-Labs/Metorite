"""Projects · analytics — WS-27bk §9.12.7.

Spec: ``project-docs/specs/project_management_app.md`` §9.12.7.

    GET /projects/analytics/stuck       → (a) where work is stuck
    GET /projects/analytics/load        → (b) who is overloaded
    GET /projects/analytics/throughput  → (c) are we getting faster
    GET /projects/analytics/finished    → (d) what did we finish

The owner asked four questions. (a) is ageing, blocked and overdue — the
operational view, the one that says what needs attention today. (b) splits
open work across the people carrying it. (c) reads the activity spine for
throughput and cycle time, and is the first read here about the PAST rather
than about now. (d) groups the SAME completions (c) counts by project instead
of by week, and its shape is also §9.12.8's weekly report.

⚠️ **EVERY NUMBER IS A SERVER AGGREGATE, and that is not a preference.** The
task list is paginated. A count taken in the browser over the rows on screen is
a count of one page — it looks plausible, it is wrong, and nothing on the way
says so. That is the same rule ``filters.py`` states for filters, one level up:
a metric applied after ``LIMIT`` is a metric about the limit.

⚠️ **Visibility is the CALLER's.** The subtree walk runs over ``pm_projects``
unrestricted, because a subtree is a structural fact. Every task count goes
through ``vis.task_clause()``, so a member who can see a space but only one
project inside it gets numbers that match what they could reach by clicking.
Without that a roll-up becomes a disclosure channel — `tree.get_node_summary`
carries the same note for the same reason.

**A closed task is not stuck.** Everything here counts OPEN work only, through
``CLOSING_CATEGORIES`` rather than a second literal list. A category added to
the closed set must not leave this endpoint reporting finished work as late.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends, Query
from gateway.routes.projects.core import (
    CLOSING_CATEGORIES,
    COMPLETED_CATEGORY,
    REPORTABLE_STATUSES,
    STARTED_CATEGORY,
    _tenant_session,
    load_visible_project,
    reportable_with_ancestors_clause,
    resolve_visibility,
    router,
    task_visibility_clause,
    triage_exclusion_clause,
)
from sqlalchemy import text

#: Days since a task last changed, banded. DISJOINT and ascending.
#:
#: Cumulative bands ("7 or more", "14 or more") double-count, so the four
#: numbers do not add up to the total and a chart drawn from them lies about
#: its own proportions. The last band is open-ended, which is where the
#: genuinely forgotten work collects.
#: ⚠️ **A NAME MUST NOT START WITH A DIGIT.** These are interpolated as bare
#: SQL column aliases, and Postgres reads `7_to_14d` as a numeric literal with
#: trailing junk — `PostgresSyntaxError`, which takes the WHOLE endpoint down.
#:
#: This shipped broken on 2026-09-16 and stayed broken until 2026-09-17.
#: `/analytics/stuck` answered 500 at every scope from the day it merged, and
#: nothing said so: the client treats a rejected panel as null and renders
#: nothing, which is correct behaviour that made a dead endpoint invisible.
#:
#: Two fences failed together. `test_names_are_safe_as_sql_identifiers` asked
#: `name.replace("_", "").isalnum()`, and `"7to14d".isalnum()` is True — the
#: test passed on a name Postgres cannot parse. And §9.12.7(a) shipped with a
#: STRUCTURAL suite and no R8 test, so nothing ever RAN this query. The
#: header of `test_projects_analytics_load.py` predicted it in writing: *"a
#: hand-run leaves nothing behind that fails later."*
STALE_BANDS: tuple[tuple[str, int, int | None], ...] = (
    ("under_7d", 0, 7),
    ("days_7_to_14", 7, 14),
    ("days_14_to_30", 14, 30),
    ("over_30d", 30, None),
)

#: The most blocked tasks named in one response.
#:
#: A list, not a page: this is a dashboard panel, and somebody who needs all of
#: them wants the board with a filter, not an endpoint that scrolls. The total
#: travels beside it so the panel can say "12 of 47" rather than imply 12.
MAX_NAMED = 20

#: The days after which a task in progress with no change is STALE
#: (`projects_reports.md` §5, WS-27bn R3c).
#:
#: One named constant, and it is the lower bound of the `days_14_to_30` band
#: in :data:`STALE_BANDS`, so "stale" in a report and the ageing chart agree.
#: `test_projects_report_sections_r3c.py` pins the two together. The
#: `hygiene` section reads it now, and the `pulse` section reads it later.
STALE_DAYS = 14

#: The four kinds of open task the `hygiene` section counts, in the order it
#: prints them, each with the predicate over `pm_tasks t` and
#: `pm_task_statuses s` that decides it (WS-27bn R3c).
#:
#: ⚠️ **One predicate for each kind, and the counts and the rows both read
#: it.** A count with one spelling and a row list with another is how a panel
#: says "23 undated" over a list of 19.
#:
#: ⚠️ **An `agent:<name>` assignee is an assignee.** The predicate asks for
#: ANY row in `pm_task_assignees`, with no filter on the value, so work that
#: an agent holds is not "nobody's".
#:
#: ⚠️ **Never `pm_task_personal`.** A private estimate or plan of one member
#: does not fill a shared field (D53, owner Q6). The source test in
#: `test_projects_report_sections_r3c.py` fails if a predicate names it.
HYGIENE_KINDS: tuple[tuple[str, str], ...] = (
    (
        "no_assignee",
        "NOT EXISTS (SELECT 1 FROM pm_task_assignees ha WHERE ha.task_id = t.id)",
    ),
    ("no_due_date", "t.due_at IS NULL"),
    ("no_estimate", "t.estimate_mins IS NULL"),
    (
        "stale_in_progress",
        # INTS through `make_interval`, never `CAST(:x AS interval)`: the
        # asyncpg trap `stale_bands_sql` records.
        "s.category = :started_cat"
        " AND t.updated_at <= now() - make_interval(days => :stale_days)",
    ),
)


# ── Scope: one node, one subtree, or the whole portfolio ────────────────────


async def scope_clause(
    db: Any, vis: Any, project_id: str | None, include_subtree: bool,
) -> str:
    """The task-scope fragment these three endpoints share.

    ⚠️ **`project_id=None` is the PORTFOLIO, and the spec requires it.**
    §9.12.7's Done-when says each slice "answers for a subtree and for the
    portfolio". All three shipped node-only, so the Analytics pane could not
    call any of them — that pane reads the portfolio roll-up and holds no node
    id at all.

    An earlier note here argued that a portfolio read would be "every task in
    the tenant". It is, and it is the same read `get_portfolio_summary`
    already serves to the strip above these panels. The bound was never the
    node: it is ``vis.task_clause()`` plus the tenant session, and dropping
    the node changes neither. A panel that refuses the scope its own page is
    showing only moves the count into the browser, which is the one thing the
    module header forbids.

    The node check lives here rather than in each caller, because it is part
    of resolving the scope. Seeing a node is required to report on it, so an
    unreadable id answers 404 instead of zeroes — zeroes would tell the caller
    the project exists and has no work. The portfolio has no node to check,
    and `vis` is what bounds it.
    """
    if project_id is None:
        # ⚠️ Not "no filter". Every caller below appends the visibility clause
        # and runs inside the tenant session, and those two are what make this
        # the caller's portfolio instead of the database's.
        return "TRUE"
    await load_visible_project(db, vis, project_id)
    if include_subtree:
        return (
            "t.project_id IN ("
            "  WITH RECURSIVE sub AS ("
            "    SELECT id FROM pm_projects WHERE id = CAST(:pid AS uuid)"
            "    UNION ALL"
            "    SELECT p.id FROM pm_projects p JOIN sub s"
            "      ON p.parent_project_id = s.id"
            "  ) SELECT id FROM sub)"
        )
    return "t.project_id = CAST(:pid AS uuid)"


def scope_params(project_id: str | None) -> dict[str, Any]:
    """`:pid`, and ONLY when the clause above names it.

    A bound parameter with no placeholder is an error on some drivers and a
    silent no-op on others. Neither is worth discovering in production.
    """
    return {} if project_id is None else {"pid": project_id}


@router.get("/analytics/stuck")
async def stuck(
    project_id: str | None = None,
    include_subtree: bool = True,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Where work is stuck, under one node or across the portfolio.

    Three answers in three grouped reads over the same scope: how long open
    work has sat untouched, what is blocked by something unfinished, and what
    is past due.

    Omit ``project_id`` for the portfolio. See :func:`scope_clause`, which
    also records why an earlier version of this docstring refused it.
    """
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)

        scope_sql = await scope_clause(db, vis, project_id, include_subtree)

        # The predicate every read below shares. Written once: three copies
        # would drift, and a dashboard whose panels disagree about what "open"
        # means is worse than one panel.
        open_where = (
            f"{scope_sql}"
            f" AND t.archived_at IS NULL"
            f" AND ({task_visibility_clause(vis, 't')})"
            f" AND ({triage_exclusion_clause('t')})"
            # D-PM-32(b). A STOPPED project's work is not happening, so
            # counting it as overload or as stuck is noise in every metric.
            # ⚠️ Paused and queued work STAYS — hiding a stalled project from
            # the one surface that would reveal the stall is how a quarter of
            # its work goes missing. `reportable_project_clause` documents why
            # this is not `runnable_project_clause`.
            f" AND ({reportable_with_ancestors_clause('t')})"
            f" AND s.category <> ALL(CAST(:closed AS text[]))"
        )
        params: dict[str, Any] = {
            **vis.params,
            **scope_params(project_id),
            "closed": sorted(CLOSING_CATEGORIES),
            "reportable_states": sorted(REPORTABLE_STATUSES),
        }

        # ── How long has open work sat untouched? ──────────────────────────
        #
        # `updated_at`, not "time in current status". The status history is in
        # `pm_activities` and answering from it means a walk of the whole
        # spine per task. This is the cheap question that is still worth
        # asking, and §9.12.7(c) is where the spine gets read.
        band_sql, band_params = stale_bands_sql(open_where)
        params.update(band_params)

        stale_row = (await db.execute(
            text(band_sql),
            params,
        )).fetchone()
        stale = [
            {"band": name, "n": int(getattr(stale_row, name, 0) or 0)}
            for name, _, _ in STALE_BANDS
        ]

        # ── What is blocked by something unfinished? ────────────────────────
        #
        # ⚠️ A link to a task that is DONE is not a block. Counting every
        # `blocks` edge would report a project as blocked by work it already
        # finished, which is how a blocked count becomes noise people mute.
        blocker_join = (
            "EXISTS ("
            "  SELECT 1 FROM pm_task_links l"
            "    JOIN pm_tasks b ON b.id = l.source_task_id"
            "    JOIN pm_task_statuses bs ON bs.id = b.status_id"
            "   WHERE l.target_task_id = t.id"
            "     AND l.link_type = 'blocks'"
            "     AND b.archived_at IS NULL"
            "     AND bs.category <> ALL(CAST(:closed AS text[]))"
            ")"
        )
        blocked_total = int((await db.execute(
            text(
                f"SELECT count(*) FROM pm_tasks t"
                f"  JOIN pm_task_statuses s ON s.id = t.status_id"
                f" WHERE {open_where} AND {blocker_join}"
            ),
            params,
        )).scalar() or 0)

        blocked_rows = (await db.execute(
            text(
                f"SELECT t.id, t.title, t.task_number, t.due_at, t.project_id"
                f"  FROM pm_tasks t"
                f"  JOIN pm_task_statuses s ON s.id = t.status_id"
                f" WHERE {open_where} AND {blocker_join}"
                # Oldest first: the thing blocked longest is the thing to ask
                # about, and a dashboard that led with the newest would show a
                # different list every day while the real problem sat below.
                f" ORDER BY t.created_at ASC, t.id ASC"
                f" LIMIT {MAX_NAMED}"
            ),
            params,
        )).fetchall()

        overdue_rows = (await db.execute(
            text(overdue_by_project_sql(open_where)), params,
        )).fetchall()

        return {
            "project_id": project_id,
            "scope": "portfolio" if project_id is None else "node",
            "include_subtree": include_subtree,
            "stale": stale,
            "blocked_total": blocked_total,
            "blocked": [
                {
                    "id": str(row.id),
                    "title": row.title,
                    "task_number": row.task_number,
                    "due_at": row.due_at.isoformat() if row.due_at else None,
                    # WS-27bm S4: the status report flags the PROJECT a
                    # blocked task lives in. Without this the flag was
                    # unreachable, and a fake that invented the key hid it.
                    "project_id": str(row.project_id),
                }
                for row in blocked_rows
            ],
            # ⚠️ BY PROJECT, which is what §9.12.7(a) asks for — "Overdue by
            # project". This answered a bare integer until 2026-09-17, and the
            # panel that consumed it declared a LIST. `number.length` is
            # undefined, `undefined > 0` is false, so the Overdue section
            # rendered nothing at all and threw nothing either. A total with
            # no breakdown also could not answer the question the section is
            # for, which is WHERE the late work is.
            "overdue": [
                {
                    "project_id": str(row.project_id),
                    "name": row.name,
                    "overdue": int(row.overdue),
                }
                for row in overdue_rows
            ],
            # The scalar keeps its meaning under a name that cannot be
            # mistaken for the list.
            "overdue_total": sum(int(r.overdue) for r in overdue_rows),
        }


def overdue_by_project_sql(open_where: str) -> str:
    """Open work past its due date, grouped by the project it lives in.

    §9.12.7(a) asks for "Overdue by project" in those words, and a single
    total cannot answer it: the useful finding is never "we have 14 late", it
    is "11 of the 14 are in Mobile App".

    Named and exported because `reports.py` needs the same number. A report
    that counted overdue work itself would be the second set of numbers
    §9.12.8 exists to prevent — so this is the one definition, and both
    callers run it.

    The project is the task's OWN, matching `finished_sql` for the same
    reason: rolling a subproject's late work into its parent hides which team
    is behind.
    """
    return (
        f"SELECT t.project_id AS project_id, pr.name AS name,"
        f"       count(*) AS overdue"
        f"  FROM pm_tasks t"
        f"  JOIN pm_task_statuses s ON s.id = t.status_id"
        f"  JOIN pm_projects pr ON pr.id = t.project_id"
        f" WHERE {open_where}"
        f"   AND t.due_at IS NOT NULL AND t.due_at < now()"
        f" GROUP BY 1, 2"
        # Worst first. A dashboard row the reader acts on is the top one.
        f" ORDER BY overdue DESC, name"
    )


#: The most people named in one load response.
#:
#: Same ruling as :data:`MAX_NAMED`: a dashboard panel, not a page. The total
#: travels beside the list so the panel can say "20 of 34" instead of implying
#: that thirty-four people are twenty.
MAX_PEOPLE = 20


def stale_bands_sql(open_where: str) -> tuple[str, dict[str, int]]:
    """The ageing histogram, as a query a test can RUN — and its parameters.

    ⚠️ **Extracted 2026-09-17, because nothing ever executed it.** §9.12.7(a)
    shipped with a structural suite: the band names were asserted, the query
    they build was not. It answered 500 at every scope for a day, and the
    only reason nobody noticed is that a rejected panel correctly renders as
    nothing rather than as zeroes.

    Returns the SQL and the interval parameters together, so a caller cannot
    build one without the other — the failure mode a second `for` loop over
    `STALE_BANDS` invites.
    """
    bands = " ".join(
        f"count(*) FILTER (WHERE "
        f"t.updated_at <= now() - make_interval(days => :d{low})"
        + (
            f" AND t.updated_at > now() - make_interval(days => :d{high})"
            if high is not None
            else ""
        )
        + f") AS {name},"
        for name, low, high in STALE_BANDS
    ).rstrip(",")
    # ⚠️ **INTS through `make_interval`, never a string through `CAST(:x AS
    # interval)`.** The second shape works under psycopg and FAILS under
    # asyncpg — *"invalid input for query argument: '0 days' (str object has
    # no attribute days)"* — and the gateway runs asyncpg (`acb_common.db`
    # rewrites every DSN onto it). This exact trap is recorded twice already,
    # in `delta.py` and in `filters.parse_when`, and slice (a) walked into it
    # anyway because no test ran the query on the driver production uses.
    #
    # `make_interval(weeks => :weeks)` is the idiom the rest of this module
    # already uses. There is now one shape here, not two.
    params: dict[str, int] = {}
    for _, low, high in STALE_BANDS:
        params[f"d{low}"] = int(low)
        if high is not None:
            params[f"d{high}"] = int(high)
    return (
        f"SELECT {bands}"
        f"  FROM pm_tasks t"
        f"  JOIN pm_task_statuses s ON s.id = t.status_id"
        f" WHERE {open_where}"
    ), params


def load_sql(open_where: str) -> str:
    """The per-assignee aggregate, as one string a test can RUN.

    ⚠️ A function rather than an f-string inline in the route, so
    ``test_projects_analytics_load.py`` executes THIS query against a real
    Postgres instead of a transcription of it. The spec's Done-when for
    §9.12.7 asks for exactly that, and a copied query agrees with itself
    forever while the route drifts.

    ``open_where`` is the caller's scope, visibility and open-only predicate.
    It is interpolated because it is built from trusted fragments — the same
    shape ``stuck`` uses — and every VALUE in it still travels as a bound
    parameter.

    `LEFT JOIN` and `coalesce` are what put unassigned work in the list instead
    of dropping it. An inner join would silently answer a different question —
    "who is overloaded, among tasks somebody already took" — and that question
    hides the backlog nobody owns.

    The buckets are DISJOINT, and the ``due_at IS NULL`` arm lands in ``later``
    on purpose. Undated work is real work, and a fourth "no date" column would
    split the one bar a reader wants to compare.
    """
    return (
        f"SELECT coalesce(lower(a.assignee), '') AS who,"
        f"       count(*) FILTER ("
        f"         WHERE t.due_at IS NOT NULL AND t.due_at < now()"
        f"       ) AS overdue,"
        f"       count(*) FILTER ("
        f"         WHERE t.due_at >= now()"
        f"           AND t.due_at < now() + interval '7 days'"
        f"       ) AS due_next_7d,"
        f"       count(*) FILTER ("
        f"         WHERE t.due_at IS NULL"
        f"            OR t.due_at >= now() + interval '7 days'"
        f"       ) AS later,"
        f"       count(*) AS open_tasks,"
        # ⚠️ ESTIMATED effort, never logged effort. Nothing in this product
        # records hours actually worked — `pm_tasks` has `estimate_mins` and
        # no actual. `pm_task_personal.actual_start/end` is the Calendar's
        # per-member timeboxing, which is private, sparse, and not a project
        # effort log. So every field below is a plan, and the client says so.
        f"       coalesce(sum(t.estimate_mins), 0) AS est_mins,"
        # The COVERAGE beside the sum, and it is not optional. `count(col)`
        # skips NULL, so this is "how many of these tasks carry an estimate".
        # A total of 40h over 3 estimated tasks out of 30 is not 40h of work,
        # and a sum printed without its coverage says it is.
        f"       count(t.estimate_mins) AS estimated"
        f"  FROM pm_tasks t"
        f"  JOIN pm_task_statuses s ON s.id = t.status_id"
        f"  LEFT JOIN pm_task_assignees a ON a.task_id = t.id"
        f" WHERE {open_where}"
        f" GROUP BY 1"
        # Most loaded first, then the overdue half as the tiebreak — two people
        # with nine open tasks are not equally in trouble.
        f" ORDER BY open_tasks DESC, overdue DESC, who"
    )


#: Open work in scope, counted over TASKS rather than summed per person.
#:
#: ⚠️ A task with two assignees appears twice in `load_sql`, so adding its
#: `open_tasks` would report more open work than exists.
def total_open_sql(open_where: str) -> str:
    return (
        f"SELECT count(DISTINCT t.id) FROM pm_tasks t"
        f"  JOIN pm_task_statuses s ON s.id = t.status_id"
        f" WHERE {open_where}"
    )


def effort_sql(where: str) -> str:
    """Estimated effort over TASKS in scope, with the coverage beside it.

    ⚠️ **"Spent" here is the ESTIMATE of finished work, not hours logged.**
    Owner decision 2026-09-17, taken with the constraint on the table: there
    is no time tracking anywhere in this product. `pm_tasks` carries
    `estimate_mins` and no actual. So "spent" answers *"how much work did we
    think the finished tasks were"*, and the client must never label it
    "logged" or "actual".

    ⚠️ **No assignee join, on purpose.** `load_sql` counts a two-assignee task
    for BOTH people, which is right for a plate and wrong for a total. This
    reads tasks, so the figures here are the node's and not a sum of the rows
    above them — the same split `total_open_sql` already makes for counts.

    `count(estimate_mins)` skips NULL, so `estimated` over `tasks` is the
    coverage. Every consumer prints it. A sum with no coverage is a number
    that looks complete and is not.
    """
    return (
        f"SELECT coalesce(sum(t.estimate_mins), 0) AS mins,"
        f"       count(t.estimate_mins) AS estimated,"
        f"       count(*) AS tasks"
        f"  FROM pm_tasks t"
        f"  JOIN pm_task_statuses s ON s.id = t.status_id"
        f" WHERE {where}"
    )


def load_open_where(scope_sql: str, vis: Any) -> str:
    """Load's open-work predicate: the scope, the caller's grants, open only.

    ⚠️ **Named since WS-27bm S7a, because a second panel reads it.** The
    Capacity panel sits beside Load, and its per-person `open_tasks` must equal
    Load's for the same scope (`projects_ai_chat.md` §10.3 item 1). A copy of
    this string in the capacity module would be a second spelling of "open",
    and the two panels would drift apart the first time one was edited.

    ``scope_sql`` is :func:`scope_clause`'s answer. ``"TRUE"`` is every open
    task the caller can see — the portfolio.
    """
    return (
        f"{scope_sql}"
        f" AND t.archived_at IS NULL"
        f" AND ({task_visibility_clause(vis, 't')})"
        f" AND ({triage_exclusion_clause('t')})"
        # D-PM-32(b), the same clause and the same reason as `stuck`.
        # Counting a stopped project's work as somebody's load is how a
        # person reads as overloaded by work nobody expects them to do.
        f" AND ({reportable_with_ancestors_clause('t')})"
        f" AND s.category <> ALL(CAST(:closed AS text[]))"
    )


def load_params(vis: Any, project_id: str | None) -> dict[str, Any]:
    """The binds :func:`load_open_where` names, and ONLY those."""
    return {
        **vis.params,
        **scope_params(project_id),
        "closed": sorted(CLOSING_CATEGORIES),
        "reportable_states": sorted(REPORTABLE_STATUSES),
    }


@router.get("/analytics/load")
async def load(
    project_id: str | None = None,
    include_subtree: bool = True,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Who is overloaded, under one node — §9.12.7(b).

    Open work per assignee, split by when it is due: **overdue**, due in the
    **next seven days**, and **later** — where "later" also holds everything
    with no due date at all.

    ⚠️ **UNASSIGNED is a row, not an absence.** The spec says so and it is
    right: on a real board it is usually the largest bar and the actual
    finding. Reporting it as a gap in a list of people would hide the one
    number somebody can act on today.

    ⚠️ **Seven rolling days, NOT a calendar week, and the spec says "this
    week".** Two reasons for the divergence, both practical. A calendar week
    shrinks as the week runs, so the same board reads "overloaded" on Monday
    and "clear" on Friday without anybody finishing anything. And a calendar
    week needs a timezone, which here would be the PROJECT's — so a subtree
    spanning two timezones has two different weeks in one aggregate. The field
    is named `due_next_7d` rather than `this_week`, so the response cannot be
    read as claiming a calendar week it never computed.

    ⚠️ **A task with two assignees counts for BOTH.** It is genuinely on both
    people's plates, and halving it would make every bar a fraction nobody can
    act on. So the per-person numbers sum to more than `total_tasks`, which is
    why that total is reported separately rather than inferred by adding.
    """
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)

        scope_sql = await scope_clause(db, vis, project_id, include_subtree)

        # The same predicate `stuck` uses, for the same reason: two panels on
        # one dashboard must not disagree about what "open" means. Named, so
        # the Capacity panel beside this one reads the SAME predicate.
        open_where = load_open_where(scope_sql, vis)
        params = load_params(vis, project_id)

        # ── Per person, bucketed by when it is due — see `load_sql`. ──────
        rows = (
            await db.execute(text(load_sql(open_where)), params)
        ).fetchall()

        # ⚠️ Counted over TASKS, not summed from the rows above. A task with
        # two assignees appears twice up there, so adding `open_tasks` would
        # report more open work than exists.
        total_tasks = int(
            (
                await db.execute(text(total_open_sql(open_where)), params)
            ).scalar()
            or 0
        )

        # ── Effort, in both directions — see `effort_sql`. ───────────────
        #
        # ⚠️ Two reads rather than one grouped by open/closed. The `left` half
        # must use the SAME `open_where` the rows above used, or the panel
        # would state a remaining figure for a different set of tasks than the
        # bars beside it. Sharing the fragment is what keeps them the same
        # question.
        left = (
            await db.execute(text(effort_sql(open_where)), params)
        ).one()

        # "Spent" is DONE work only. Cancelled work consumed effort too, and
        # counting it here would say the team delivered it — the same
        # exclusion the Progress card already footnotes.
        done_where = (
            f"{scope_sql}"
            f" AND t.archived_at IS NULL"
            f" AND ({task_visibility_clause(vis, 't')})"
            f" AND s.category = :done_cat"
        )
        spent = (
            await db.execute(
                text(effort_sql(done_where)),
                {**params, "done_cat": COMPLETED_CATEGORY},
            )
        ).one()

        people = [
            {
                # "" is the unassigned bucket. Named explicitly rather than
                # left as an empty string the client has to recognise.
                "assignee": row.who or None,
                "open_tasks": int(row.open_tasks),
                "overdue": int(row.overdue),
                "due_next_7d": int(row.due_next_7d),
                "later": int(row.later),
                # ⚠️ Estimated, never logged. `est_mins` is what somebody
                # GUESSED this plate weighs, and `estimated` of `open_tasks`
                # says how much of the plate was guessed at all.
                "est_mins": int(row.est_mins or 0),
                "estimated": int(row.estimated or 0),
            }
            for row in rows
        ]
        return {
            "project_id": project_id,
            "scope": "portfolio" if project_id is None else "node",
            "include_subtree": include_subtree,
            "total_tasks": total_tasks,
            "people_total": len(people),
            "people": people[:MAX_PEOPLE],
            # ⚠️ **Counted over TASKS, so it does not add up from `people`.**
            # A two-assignee task sits on two plates above and is one task
            # here. Reported as its own block for that reason.
            #
            # ⚠️ **`estimated` and `tasks` are not decoration.** They are the
            # coverage, and the sum means nothing without them. 40h over 3 of
            # 30 tasks is not 40h of remaining work.
            "effort": {
                "left_mins": int(left.mins or 0),
                "left_estimated": int(left.estimated or 0),
                "left_tasks": int(left.tasks or 0),
                "spent_mins": int(spent.mins or 0),
                "spent_estimated": int(spent.estimated or 0),
                "spent_tasks": int(spent.tasks or 0),
                # Said in the payload so no client has to know it, and no
                # client can label these "logged" without contradicting the
                # response it is drawing.
                "basis": "estimate",
            },
            # Headcount, and it excludes the unassigned bucket — "nobody" is
            # not a person, and counting it would report one extra worker on
            # every project that has a backlog.
            "people_named": sum(1 for p in people if p["assignee"]),
        }


# ── (c) Are we getting faster? — §9.12.7(c) ─────────────────────────────────
#
# ⚠️ **This slice reads HISTORY, and history has a different scoping rule from
# the two above.** `stuck` and `load` answer about open work NOW, so they read
# the task rows. Throughput and cycle time answer about what happened, so they
# read `pm_activities` — the timeline spine (§3.8), where a status move is a
# row nobody rewrites.
#
# The spec is explicit that this walk "MUST be bounded by period and by scope,
# or it reads the whole spine". Both bounds are below and neither is optional.

#: How far back the history walk may reach, in whole weeks.
#:
#: A cap rather than a preference: `weeks` comes from the caller, and an
#: unbounded value turns a dashboard panel into a full scan of every activity
#: the tenant has ever written. 26 is two quarters, which is longer than any
#: "are we getting faster" question needs and short enough to stay on the
#: `(task_id, created_at)` index.
MAX_WEEKS = 26
DEFAULT_WEEKS = 12

# ⚠️ **This slice splits `CLOSING_CATEGORIES`, and the split is DELIBERATE.**
#
# Both categories close a task, and both correctly count as closed in `stuck`
# and `load` — a cancelled task is not outstanding work. They stop being
# interchangeable here: a team that cancelled forty tasks did not get faster,
# and folding the two together makes cancellation the cheapest way to raise the
# number this endpoint reports.
#
# So the headline counts `COMPLETED_CATEGORY` only. Cancellations travel BESIDE
# it in the same weekly row rather than vanishing — "we cancelled twelve this
# week" is itself the finding on some boards.
#
# Both words are imported from `core`, never written here. Which word means
# "finished" is a fact about the status vocabulary, and a literal in a route
# module is the second vocabulary `test_projects_analytics`'s shape fence
# refuses by name.

#: The first instant inside the window, as a `timestamptz`.
#:
#: ⚠️ **Truncated in UTC, explicitly, and converted back explicitly.** Postgres
#: `date_trunc('week', ...)` on a `timestamptz` uses the SESSION timezone, so
#: the same request answers with different week boundaries depending on what
#: the connection happens to carry. The same hazard `load` met with calendar
#: weeks, met here in the database instead of in the reasoning.
#:
#: Week-aligned rather than `now() - N weeks` so every bucket is a whole week.
#: A ragged first bucket reads as a dip in throughput that nobody caused.
_WEEK_NOW = "date_trunc('week', now() AT TIME ZONE 'UTC')"


def _window(skip_current_week: bool) -> tuple[str, str]:
    """The half-open window `[start, end)`, as two `timestamptz` expressions.

    ⚠️ **`skip_current_week` is the shape decision §9.12.7(d) was told to
    make.** The dashboard wants the running week — it is this week's work and
    leaving it out loses it. A REPORT wants the opposite. A weekly report sent
    on Monday describes a week that ENDED, and a rolling window that still has
    days left in it would re-report the same tasks on the next send, each time
    with a different number beside them.

    One flag rather than two date parameters, because every window here is a
    whole number of weeks ending at a week boundary. Free-form dates would
    admit a half week, which is the ragged bucket `weekly_sql` already refuses.

    The end bound is inert for the dashboard: with the flag off it is next
    Monday, which no `created_at` can reach. It is written anyway, so the
    query states its own period instead of implying one.
    """
    back = 1 if skip_current_week else 0
    last = f"({_WEEK_NOW} - make_interval(weeks => {back}))"
    start = f"(({last} - make_interval(weeks => :weeks - 1)) AT TIME ZONE 'UTC')"
    end = f"(({last} + interval '1 week') AT TIME ZONE 'UTC')"
    return start, end


#: The dashboard's window start. Kept as a name because three docstrings and
#: the throughput tests refer to it.
_PERIOD_START = _window(False)[0]


def cycle_cte_sql(
    scope_where: str, *, skip_current_week: bool = False,
) -> str:
    """The shared definition of "a task finished", as a ``WITH`` prefix.

    Both reads below are built on this, so the weekly trend and the overall
    summary cannot disagree about which tasks they describe. Two panels headed
    "throughput" and "median cycle time" that count different sets is the
    dashboard defect this exists to prevent.

    ``scope_where`` is the caller's subtree, visibility and triage predicate
    over the alias ``t``. It is interpolated because it is built from trusted
    fragments — the shape `stuck` and `load` use — and every VALUE in it still
    travels as a bound parameter.

    Three rulings live in this query.

    **One row per TASK, at its FIRST completion in the window.** A task that was
    finished, reopened and finished again did one piece of work plus one piece
    of rework. Counting it twice puts the rework inside the throughput number
    where nobody can see it. It also makes throughput and cycle time share a
    key, so the two lines on one chart describe the same tasks.

    ⚠️ **`done` wins the tiebreak over `cancelled`, and it carries more weight
    than it looks.** It is what absorbs a `done → cancelled` relabel: tidying a
    finished backlog leaves two closing crossings on one task, and the ruling
    keeps the completion rather than letting the relabel overwrite it. An
    earlier draft filtered those crossings out by their `from_category`
    instead. That arm was redundant here, and it silently dropped the opposite
    case — a cancelled task revived straight into `done` — which is a real
    completion that then appeared as a cancellation.

    **The start is UNBOUNDED in time, and that is not a missing bound.** A task
    finished this week may have started five months ago, and clipping the start
    at the window edge would report every long task as fast. The read stays
    bounded because it is restricted to the tasks already selected — an index
    seek per task on ``(task_id, created_at)``, never a scan of the spine.

    **A task with no recorded start has NO cycle time, and is not a zero.**
    Work created directly in a closing lane, imported history, and anything
    that skipped `in_progress` land here. A zero would drag every median toward
    it and make a board look fastest exactly where it recorded least. They are
    counted as ``no_start`` instead, so the median's denominator sits beside it
    rather than being implied.
    """
    start, end = _window(skip_current_week)
    return (
        f"WITH closings AS ("
        f"  SELECT a.task_id, t.project_id, a.created_at AS finished_at,"
        f"         a.meta->>'to_category' AS to_category"
        f"    FROM pm_activities a"
        f"    JOIN pm_tasks t ON t.id = a.task_id"
        f"   WHERE a.type = 'status_change'"
        f"     AND a.deleted_at IS NULL"
        f"     AND a.created_at >= {start}"
        f"     AND a.created_at < {end}"
        f"     AND a.meta->>'to_category' = ANY(CAST(:closing AS text[]))"
        f"     AND ({scope_where})"
        f"), per_task AS ("
        f"  SELECT DISTINCT ON (task_id)"
        f"         task_id, project_id, finished_at, to_category"
        f"    FROM closings"
        f"   ORDER BY task_id, (to_category = :done_cat) DESC, finished_at"
        f"), started AS ("
        f"  SELECT a.task_id, min(a.created_at) AS started_at"
        f"    FROM pm_activities a"
        f"   WHERE a.type = 'status_change'"
        f"     AND a.deleted_at IS NULL"
        f"     AND a.meta->>'to_category' = :started_cat"
        f"     AND a.task_id IN (SELECT task_id FROM per_task)"
        f"   GROUP BY 1"
        f"), cyc AS ("
        f"  SELECT p.task_id, p.project_id, p.finished_at, p.to_category,"
        # A start recorded AFTER the finish is not a cycle. It means the history
        # is out of order, and subtracting would report a negative duration that
        # a median cheerfully takes at face value.
        f"         CASE WHEN st.started_at IS NOT NULL"
        f"               AND st.started_at <= p.finished_at"
        f"              THEN extract(epoch FROM (p.finished_at - st.started_at))"
        f"                   / 3600.0"
        f"         END AS hours"
        f"    FROM per_task p"
        f"    LEFT JOIN started st ON st.task_id = p.task_id"
        f")"
    )


#: The six numbers a completion bucket reports. One string, used twice, so the
#: weekly row and the overall summary cannot drift apart.
#:
#: ⚠️ `percentile_cont` IGNORES nulls, which is what keeps `no_start` tasks out
#: of the median instead of pinning it at zero. That is load-bearing, and it is
#: a property of Postgres rather than of this file. The throughput suite
#: asserts it against a real database instead of trusting the documentation.
_CYCLE_MEASURES = (
    "count(*) FILTER (WHERE c.to_category = :done_cat) AS completed,"
    " count(*) FILTER (WHERE c.to_category IS NOT NULL"
    "                    AND c.to_category <> :done_cat) AS cancelled,"
    " count(c.hours) FILTER (WHERE c.to_category = :done_cat) AS measured,"
    " count(*) FILTER (WHERE c.to_category = :done_cat"
    "                    AND c.hours IS NULL) AS no_start,"
    " percentile_cont(0.5) WITHIN GROUP (ORDER BY c.hours)"
    "   FILTER (WHERE c.to_category = :done_cat) AS median_hours,"
    " percentile_cont(0.9) WITHIN GROUP (ORDER BY c.hours)"
    "   FILTER (WHERE c.to_category = :done_cat) AS p90_hours"
)


def weekly_sql(scope_where: str) -> str:
    """Throughput and cycle time per week, over a GENERATED week spine.

    ⚠️ **A week with no completions must appear as a zero.** Grouping the
    activity rows alone emits no row for a quiet week, and a chart drawn from
    that runs a line straight across the gap — so a fortnight when nothing
    shipped renders as steady delivery. `generate_series` puts every week of
    the window on the axis, and the LEFT JOIN leaves the empty ones at zero.

    The last bucket is the CURRENT week, which is partial by construction. The
    route flags it rather than hiding it: dropping it loses this week's work,
    and leaving it unmarked makes every Monday look like a collapse.
    """
    return (
        f"{cycle_cte_sql(scope_where)}"
        f", weeks AS ("
        f"  SELECT generate_series("
        f"    {_WEEK_NOW} - make_interval(weeks => :weeks - 1),"
        f"    {_WEEK_NOW}, interval '1 week') AS week"
        f")"
        f" SELECT w.week AS week, {_CYCLE_MEASURES}"
        f"   FROM weeks w"
        f"   LEFT JOIN cyc c"
        f"     ON date_trunc('week', c.finished_at AT TIME ZONE 'UTC') = w.week"
        f"  GROUP BY w.week"
        f"  ORDER BY w.week"
    )


def cycle_summary_sql(
    scope_where: str, *, skip_current_week: bool = False,
) -> str:
    """The same six numbers over the whole window, in ONE bucket.

    ⚠️ Not derivable from :func:`weekly_sql`'s output. A median of weekly
    medians is not the median, and it is wrong in the direction that matters —
    a quiet week with one slow task would weigh the same as a busy week with
    forty fast ones.
    """
    return (
        f"{cycle_cte_sql(scope_where, skip_current_week=skip_current_week)}"
        f" SELECT {_CYCLE_MEASURES} FROM cyc c"
    )


def _hours(value: Any) -> float | None:
    """Round an hour figure for transport, or keep the null.

    ⚠️ **None is not zero here.** A window in which nothing measurable finished
    has no median, and sending 0.0 would draw a point on the chart claiming the
    team finished everything instantly.
    """
    return None if value is None else round(float(value), 2)


# ── Will this land, and when? — the executive read ──────────────────────────
#
# ⚠️ **NO TIME TRACKING EXISTS, and this is built knowing it.** Owner
# direction, 2026-09-17: *"we'll try to estimate, depending on the start and
# end dates, due dates, etc., and the estimated number of hours... we can
# estimate the temporal characteristics of each project without needing to
# use time tracking."* Everything below derives from data we already hold:
# `pm_activities` for what actually happened, `pm_tasks.estimate_mins` and
# `due_at` for the plan, and `people` for who can do the work.
#
# ⚠️ **TWO FORECASTS, DELIBERATELY, AND THEY ANSWER DIFFERENT QUESTIONS.**
# Velocity says *"at the rate this team actually goes"*. Capacity says *"if
# the team simply worked the plan"*. Where they disagree, the disagreement is
# the finding — a plan that needs 40h/week from somebody who has 12 is not a
# plan, and only the second number can say so.
#
# ⚠️ **SCOPE GROWTH IS PART OF THE ANSWER, NOT A FOOTNOTE.** Owner decision,
# 2026-09-17: when a project adds work faster than it finishes work, this
# refuses to print a date and says why. Remaining ÷ velocity always yields a
# date, and that date silently slips every week while nobody is told the
# reason. "Not converging" is the finding an executive needs.


def velocity_sql(scope_where: str) -> str:
    """Finished per week and CREATED per week, over the same week spine.

    ⚠️ **Both halves, over one spine, in one read.** Two queries over two
    windows would let the arrival rate and the completion rate describe
    different fortnights, and their difference is the whole point.

    A quiet week must be a ZERO, not an absent row — the lesson `weekly_sql`
    records. `generate_series` puts every week on the axis and the LEFT JOINs
    leave the empty ones at zero, so a fortnight where nothing shipped reads
    as a fortnight where nothing shipped.

    ⚠️ The CURRENT week is excluded by both arms. It is partial by
    construction, and a half-finished week dragged into an average makes
    every Monday look like a collapse — `weekly_sql` flags it instead because
    a chart can show a partial bar. An average cannot.
    """
    return (
        f"{cycle_cte_sql(scope_where, skip_current_week=True)}"
        f", weeks AS ("
        f"  SELECT generate_series("
        f"    {_WEEK_NOW} - make_interval(weeks => :weeks),"
        f"    {_WEEK_NOW} - interval '1 week', interval '1 week') AS week"
        f")"
        # Arrivals are read off `pm_tasks.created_at` rather than off the
        # activity spine: a task's birth is not a status_change, so the spine
        # has no row for it. Same scope and same visibility as the closings.
        f", born AS ("
        f"  SELECT date_trunc('week', t.created_at AT TIME ZONE 'UTC') AS week,"
        f"         count(*) AS n"
        f"    FROM pm_tasks t"
        f"   WHERE {scope_where}"
        f"     AND t.archived_at IS NULL"
        f"   GROUP BY 1"
        f")"
        f" SELECT w.week AS week,"
        f"        count(c.task_id) AS finished,"
        f"        coalesce(max(b.n), 0) AS created"
        f"   FROM weeks w"
        f"   LEFT JOIN cyc c"
        f"     ON date_trunc('week', c.finished_at AT TIME ZONE 'UTC') = w.week"
        f"    AND c.to_category = :done_cat"
        f"   LEFT JOIN born b ON b.week = w.week"
        f"  GROUP BY w.week"
        f"  ORDER BY w.week"
    )


def planned_finish_sql(open_where: str) -> str:
    """The last due date on open work, and how much of it carries one.

    ⚠️ **The coverage is not decoration.** "Planned to finish 12 Mar" over a
    backlog where 4 of 71 tasks have a due date is a claim about 4 tasks. The
    client prints the share or does not print the date.
    """
    return (
        f"SELECT max(t.due_at) AS planned_finish,"
        f"       count(t.due_at) AS dated,"
        f"       count(*) AS tasks"
        f"  FROM pm_tasks t"
        f"  JOIN pm_task_statuses s ON s.id = t.status_id"
        f" WHERE {open_where}"
    )


def team_capacity_sql(open_where: str) -> str:
    """Weekly hours available to the people who actually hold open work here.

    ⚠️ **Scoped to the assignees of THIS node's open work, never to the whole
    directory.** A company of forty has forty people's hours, and none of that
    is capacity for this project. The join is what makes the number mean
    something.

    ⚠️ **`capacity_hours_per_week` is the stated fact and wins.**
    `working_hours` is the fallback — a JSONB record of days and a start and
    end, from which a week's hours are arithmetic. Neither is required, so
    `known` travels beside the total: a sum over 2 of 7 people is not the
    team's capacity, and it looks identical to one that is.
    """
    return (
        f"WITH holders AS ("
        f"  SELECT DISTINCT lower(a.assignee) AS email"
        f"    FROM pm_tasks t"
        f"    JOIN pm_task_statuses s ON s.id = t.status_id"
        f"    JOIN pm_task_assignees a ON a.task_id = t.id"
        f"   WHERE {open_where}"
        # An agent is not a person with working hours (R10's vocabulary).
        f"     AND a.assignee NOT LIKE 'agent:%%'"
        f")"
        f" SELECT count(*) AS people,"
        f"        count(p.capacity_hours_per_week) AS with_stated,"
        f"        coalesce(sum(p.capacity_hours_per_week), 0) AS stated_hours,"
        # ⚠️ Counted, not summed — the fallback's arithmetic happens in
        # Python through `work_schedule.working_hours_between`, which is the
        # ONE place that knows what a `working_hours` record means. A second
        # interpretation of that JSONB in SQL is how the two start to differ.
        f"        count(p.working_hours) AS with_schedule,"
        f"        count(p.end_date) FILTER ("
        f"          WHERE p.end_date IS NOT NULL AND p.end_date <= "
        f"                (now() + make_interval(days => :horizon_days))::date"
        f"        ) AS leaving_soon"
        f"   FROM holders h"
        f"   LEFT JOIN people p ON lower(p.email) = h.email"
    )


def history_where(scope_sql: str, vis: Any) -> str:
    """Throughput's scope: the node, the caller's grants, no triage.

    ⚠️ **Named since WS-27bm S7e, because a second read uses it.** The chat's
    dataset read (`analytics_dataset.py`) answers `state=closed` and
    `state=all` over THIS predicate, so its cycle times describe the tasks
    Throughput describes (`projects_ai_chat.md` §13.7 rule 2). A copy of the
    string there would be a third spelling of the scope.

    No archive arm and no status arm: see :func:`throughput`. A caller that
    wants live rows only adds ``t.archived_at IS NULL`` itself.
    """
    return (
        f"{scope_sql}"
        f" AND ({task_visibility_clause(vis, 't')})"
        f" AND ({triage_exclusion_clause('t')})"
    )


@router.get("/analytics/throughput")
async def throughput(
    project_id: str | None = None,
    include_subtree: bool = True,
    weeks: int = Query(DEFAULT_WEEKS, ge=1, le=MAX_WEEKS),
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Are we getting faster, under one node — §9.12.7(c).

    Two numbers per week, plus a summary over the window: how many tasks
    finished, and how long they took from their first `in_progress` to `done`.

    ⚠️ **This reads the ACTIVITY SPINE, not `pm_tasks.completed_at`.** The
    column holds the CURRENT completion stamp and is cleared when a task
    reopens, so a read built on it reports a July that changes in September for
    reasons that have nothing to do with July. The spine is append-only, so a
    week's figure is settled once the week ends. The spec says the history
    already exists, and this is what it meant.

    ⚠️ **Archived tasks still count.** `stuck` and `load` exclude them because
    those endpoints describe open work, and an archived task is off the board.
    This one describes the past, and letting a tidy-up in September lower
    July's throughput would make the trend a record of housekeeping.

    ⚠️ **Cancellations are reported and never counted as throughput.** See
    `COMPLETED_CATEGORY` — folding them in makes cancelling the cheapest way
    to improve the number.

    ⚠️ **Median and p90, never a mean.** Cycle time is heavily right-skewed:
    one task forgotten for eight months moves a mean past every real
    experience on the team, and the reader cannot tell it happened.
    """
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)

        scope_sql = await scope_clause(db, vis, project_id, include_subtree)

        # ⚠️ Deliberately WITHOUT `archived_at IS NULL` and without the
        # open-only status arm — see the docstring. Visibility and triage stay,
        # because those two are about who may read a row, not about when.
        scope_where = history_where(scope_sql, vis)
        params: dict[str, Any] = {
            **vis.params,
            **scope_params(project_id),
            "weeks": weeks,
            "closing": sorted(CLOSING_CATEGORIES),
            "done_cat": COMPLETED_CATEGORY,
            "started_cat": STARTED_CATEGORY,
        }

        rows = (
            await db.execute(text(weekly_sql(scope_where)), params)
        ).fetchall()
        summary = (
            await db.execute(text(cycle_summary_sql(scope_where)), params)
        ).one()

        series = [
            {
                # ISO Monday, in UTC. Dated rather than stamped: a week is a
                # day on an axis, and a time of day here would only invite a
                # client to re-bucket it in its own timezone.
                "week_start": row.week.date().isoformat(),
                "completed": int(row.completed or 0),
                "cancelled": int(row.cancelled or 0),
                "measured": int(row.measured or 0),
                "no_start": int(row.no_start or 0),
                "median_hours": _hours(row.median_hours),
                "p90_hours": _hours(row.p90_hours),
            }
            for row in rows
        ]
        return {
            "project_id": project_id,
            "scope": "portfolio" if project_id is None else "node",
            "include_subtree": include_subtree,
            "weeks": weeks,
            "series": series,
            # The last bucket is this week, and this week is not over. Named so
            # a chart can dash it instead of drawing a cliff every Monday.
            "current_week_partial": bool(series),
            "summary": {
                "completed": int(summary.completed or 0),
                "cancelled": int(summary.cancelled or 0),
                "measured": int(summary.measured or 0),
                "no_start": int(summary.no_start or 0),
                "median_hours": _hours(summary.median_hours),
                "p90_hours": _hours(summary.p90_hours),
            },
        }


# ── (d) What did we finish? — §9.12.7(d) ────────────────────────────────────
#
# ⚠️ **Built on `cycle_cte_sql`, and that is the whole design.** (c) and (d)
# are the same question asked along two axes: (c) groups completions by week,
# (d) groups the SAME completions by project. A second definition of "we
# finished this" would let the throughput chart and the finished list print
# different totals for one period, and both would look right.
#
# The spec says this slice also decides the body of §9.12.8's weekly report.
# What that needed, and what it got, is `skip_current_week` — see `_window`.


def finished_sql(scope_where: str, *, skip_current_week: bool = False) -> str:
    """What finished in the window, by project.

    One row per project that finished or cancelled anything, plus the name so
    a report does not have to resolve ids afterwards.

    ⚠️ **`cancelled` travels in its own column and never in `completed`.** The
    same ruling as (c), and for the same reason: a team that cancelled forty
    tasks did not finish forty tasks. A report that added them would be a
    weekly email congratulating people for abandoning work.

    ⚠️ **The project is the task's OWN project, not the root.** A subtree
    read rolls up to the node the caller asked about, but "what did we finish"
    wants the place the work actually lives. Rolling a subproject's work into
    its parent hides which team did it, which is the one thing this list is
    for.

    ``INNER JOIN pm_projects`` rather than ``LEFT``: a completion whose
    project vanished cannot be attributed, and a nameless row in a report is
    worse than an absent one. The count that matters is ``total`` below, which
    is taken over ``cyc`` and therefore still includes it.
    """
    return (
        f"{cycle_cte_sql(scope_where, skip_current_week=skip_current_week)}"
        f" SELECT c.project_id AS project_id, pr.name AS name,"
        f"        count(*) FILTER (WHERE c.to_category = :done_cat)"
        f"          AS completed,"
        f"        count(*) FILTER (WHERE c.to_category <> :done_cat)"
        f"          AS cancelled,"
        f"        percentile_cont(0.5) WITHIN GROUP (ORDER BY c.hours)"
        f"          FILTER (WHERE c.to_category = :done_cat) AS median_hours"
        f"   FROM cyc c"
        f"   JOIN pm_projects pr ON pr.id = c.project_id"
        f"  GROUP BY 1, 2"
        # Most finished first. A report reads top-down, and the project that
        # shipped most is the one the first line should be about.
        f" HAVING count(*) FILTER (WHERE c.to_category = :done_cat) > 0"
        f"     OR count(*) FILTER (WHERE c.to_category <> :done_cat) > 0"
        f"  ORDER BY completed DESC, cancelled DESC, name"
    )


def finished_period_sql(*, skip_current_week: bool = False) -> str:
    """The window itself, as two dates.

    ⚠️ A report MUST be able to say which days it covered, and it must not
    compute them itself. A client that re-derives "the last four weeks" from
    its own clock will disagree with the server across a timezone or a
    midnight, and then two copies of one report name different weeks.

    The end is reported INCLUSIVE — the last day the window contains — because
    that is what a person reads. The query bound is half-open.
    """
    start, end = _window(skip_current_week)
    return (
        f"SELECT CAST({start} AS date) AS period_start,"
        f"       CAST({end} AS date) - 1 AS period_end"
    )


@router.get("/analytics/finished")
async def finished(
    project_id: str | None = None,
    include_subtree: bool = True,
    weeks: int = Query(DEFAULT_WEEKS, ge=1, le=MAX_WEEKS),
    skip_current_week: bool = False,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """What we finished, by project — §9.12.7(d).

    The same completions `throughput` counts, grouped by project instead of by
    week. Sharing `cycle_cte_sql` is deliberate: two definitions of "finished"
    would let one dashboard print two totals for one period.

    ⚠️ **`skip_current_week` is for the REPORT, and it is off here.** A
    dashboard wants the running week, because that is this week's work. A
    weekly report wants a week that ended — a rolling window with days left in
    it re-reports the same tasks on the next send, with a different number
    each time. §9.12.7(d) says this slice decides the report's shape, and this
    flag is that decision.

    The response carries `period_start` and `period_end` so a report can say
    which days it covered. It must not work them out from its own clock: a
    client that does will disagree with the server across a timezone or a
    midnight, and two copies of one report will then name different weeks.
    """
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)

        scope_sql = await scope_clause(db, vis, project_id, include_subtree)
        # The same predicate `throughput` uses, and deliberately without
        # `archived_at IS NULL` for the same reason: this describes the past,
        # and an archive sweep in September must not empty July.
        scope_where = (
            f"{scope_sql}"
            f" AND ({task_visibility_clause(vis, 't')})"
            f" AND ({triage_exclusion_clause('t')})"
        )
        params: dict[str, Any] = {
            **vis.params,
            **scope_params(project_id),
            "weeks": weeks,
            "closing": sorted(CLOSING_CATEGORIES),
            "done_cat": COMPLETED_CATEGORY,
            "started_cat": STARTED_CATEGORY,
        }

        rows = (
            await db.execute(
                text(finished_sql(scope_where, skip_current_week=skip_current_week)),
                params,
            )
        ).fetchall()
        window = (
            await db.execute(
                text(finished_period_sql(skip_current_week=skip_current_week)),
                {"weeks": weeks},
            )
        ).one()
        # ⚠️ Totalled from `cyc`, not by adding the rows above. The rows drop
        # a completion whose project row has gone, and a report whose parts do
        # not add to its own headline is a report nobody trusts twice.
        totals = (
            await db.execute(
                text(
                    cycle_summary_sql(
                        scope_where, skip_current_week=skip_current_week,
                    )
                ),
                params,
            )
        ).one()

        return {
            "project_id": project_id,
            "scope": "portfolio" if project_id is None else "node",
            "weeks": weeks,
            "skip_current_week": skip_current_week,
            "period_start": window.period_start.isoformat(),
            "period_end": window.period_end.isoformat(),
            "projects": [
                {
                    "project_id": str(row.project_id),
                    "name": row.name,
                    "completed": int(row.completed),
                    "cancelled": int(row.cancelled),
                    "median_hours": _hours(row.median_hours),
                }
                for row in rows
            ],
            "total_completed": int(totals.completed or 0),
            "total_cancelled": int(totals.cancelled or 0),
            "median_hours": _hours(totals.median_hours),
        }

#: How many whole weeks of history the forecast reads.
#:
#: Six is a compromise the numbers force. Fewer and one good fortnight reads
#: as a trend. More and a team that changed shape two months ago is forecast
#: from the team it used to be.
FORECAST_WEEKS = 6

#: How far ahead "leaving soon" looks, for an engagement that ends.
LEAVING_HORIZON_DAYS = 90

#: Below this, a forecast is arithmetic on a rumour.
#:
#: ⚠️ Two finished weeks is not a velocity, it is two numbers. The verdict
#: `no_history` is a better answer than a date with no support, because a date
#: gets quoted in a meeting and a refusal does not.
MIN_FINISHED_FOR_FORECAST = 3


def project_forecast(
    *,
    remaining_tasks: int,
    finished: list[int],
    created: list[int],
) -> dict[str, Any]:
    """Will this land, and when — from what the team ACTUALLY did.

    Pure, and separate from the route so it can be tested without a database.
    Every branch below is a refusal the caller must be able to reproduce.

    ⚠️ **SCOPE GROWTH IS PART OF THE ANSWER.** Owner decision, 2026-09-17.
    `remaining ÷ finished_per_week` always yields a date. That date is a lie
    whenever the backlog is growing, and it is a quiet one: it slips a little
    every week and nobody is told why. So the rate that matters is
    **net = finished minus created**, and when net is zero or negative this
    returns `not_converging` and NO date at all. A project adding 5.1 tasks a
    week while finishing 4.2 has no completion date, and saying so is the
    single most useful thing this endpoint does.

    ⚠️ **It refuses more often than it answers, on purpose.** `no_history`,
    `nothing_left` and `not_converging` are findings. A number invented to
    fill the space would be quoted in a meeting and believed.
    """
    weeks = max(len(finished), len(created))
    fin_total = sum(finished)
    made_total = sum(created)
    per_week_fin = round(fin_total / weeks, 2) if weeks else 0.0
    per_week_made = round(made_total / weeks, 2) if weeks else 0.0
    net = round(per_week_fin - per_week_made, 2)

    base: dict[str, Any] = {
        "weeks_sampled": weeks,
        "finished_per_week": per_week_fin,
        "created_per_week": per_week_made,
        "net_per_week": net,
        "remaining_tasks": remaining_tasks,
        "weeks_remaining": None,
        "finish_date": None,
    }

    if remaining_tasks <= 0:
        # Nothing open. Not a forecast at all, and a date here would be a
        # prediction about work that does not exist.
        return {**base, "verdict": "nothing_left"}

    if fin_total < MIN_FINISHED_FOR_FORECAST:
        return {**base, "verdict": "no_history"}

    if net <= 0:
        # ⚠️ The finding. No date is produced, and the two rates travel so a
        # reader can see WHY rather than being told a forecast failed.
        return {**base, "verdict": "not_converging"}

    weeks_left = math.ceil(remaining_tasks / net)
    return {
        **base,
        "verdict": "converging",
        "weeks_remaining": weeks_left,
        "finish_date": (
            date.today() + timedelta(weeks=weeks_left)
        ).isoformat(),
    }


def capacity_forecast(
    *, left_mins: int, left_estimated: int, left_tasks: int, hours_per_week: float,
) -> dict[str, Any]:
    """The other question: how long if the team simply WORKED THE PLAN.

    ⚠️ **This is not a second opinion on the same question.** Velocity says
    *"at the rate this team actually goes"*. This says *"if the estimates are
    right and everybody is free to work them"*. Where the two disagree, the
    disagreement is the finding — a plan needing 40 hours a week from
    somebody who has 12 is not a plan, and only this number can say so.

    ⚠️ **It refuses without coverage.** Remaining hours summed over a third of
    the backlog is a third of the answer, and it looks exactly like the whole
    one. `estimate_coverage` travels so the client can print the caveat or
    print nothing.
    """
    coverage = (
        round(left_estimated / left_tasks, 3) if left_tasks > 0 else 1.0
    )
    out: dict[str, Any] = {
        "hours_per_week": round(hours_per_week, 1),
        "hours_left": round(left_mins / 60, 1),
        "estimate_coverage": coverage,
        "weeks_remaining": None,
        "finish_date": None,
    }
    if left_tasks <= 0:
        return {**out, "verdict": "nothing_left"}
    if left_estimated <= 0:
        return {**out, "verdict": "no_estimates"}
    if hours_per_week <= 0:
        # Nobody's capacity is known. Dividing by an assumed 40 would put a
        # confident date on a number the product never asked anybody for.
        return {**out, "verdict": "no_capacity"}
    weeks_left = math.ceil((left_mins / 60) / hours_per_week)
    return {
        **out,
        "verdict": "ok",
        "weeks_remaining": weeks_left,
        "finish_date": (
            date.today() + timedelta(weeks=weeks_left)
        ).isoformat(),
    }


def slip_days(planned: Any, projected: str | None) -> int | None:
    """How late the forecast is against the plan. Negative means early.

    `None` when either side is missing — an unplanned project cannot slip,
    and neither can one with no forecast.
    """
    if planned is None or not projected:
        return None
    plan = planned.date() if hasattr(planned, "date") else planned
    try:
        proj = date.fromisoformat(projected)
    except (TypeError, ValueError):
        return None
    return (proj - plan).days


@router.get("/analytics/outlook")
async def outlook(
    project_id: str | None = None,
    include_subtree: bool = True,
    weeks: int = FORECAST_WEEKS,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Will this land, when, and what would have to be true — §9.12.7 wave 7.

    Owner ask, 2026-09-17: an estimated completion date, the number of people,
    the work left and the work spent, *"and other important information that
    might be needed by an executive team, CEO, or project/product manager"*.

    ⚠️ **Built with no time tracking, and the owner set that constraint
    deliberately** — *"we can estimate the temporal characteristics of each
    project without needing to use time tracking"*. Every number here comes
    from data the product already holds: `pm_activities` for what happened,
    `estimate_mins` and `due_at` for the plan, `people` for who can work.

    ⚠️ **Two forecasts, and they are not redundant.** `velocity` reads the
    team's actual rate and subtracts the rate work ARRIVES, so a growing
    backlog reports `not_converging` instead of a date that quietly slips.
    `capacity` reads remaining estimated hours against the hours the assigned
    people actually have. Velocity says what will happen. Capacity says what
    the plan would need. An executive wants both, and their gap most of all.

    ⚠️ **Every block carries its own coverage, and refuses rather than
    guesses.** Verdicts are `no_history`, `no_estimates`, `no_capacity`,
    `not_converging`, `nothing_left`. A missing figure is a finding; an
    invented one gets quoted in a meeting.

    The body is :func:`outlook_body`, which the report section ``outlook``
    also calls (WS-27bn R3a). The panel and the report are one computation.
    """
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        return await outlook_body(
            db, vis,
            project_id=project_id,
            include_subtree=include_subtree,
            weeks=weeks,
        )


async def outlook_body(
    db: Any,
    vis: Any,
    *,
    project_id: str | None,
    include_subtree: bool,
    weeks: int = FORECAST_WEEKS,
) -> dict[str, Any]:
    """The outlook answer, on a session and a visibility the caller resolved.

    WS-27bn R3a. Shared by the route and by the report section ``outlook``,
    so a report and the panel it came from are one computation.

    ⚠️ **``weeks`` is the history the forecast reads, not a report period.**
    A report passes nothing here and gets :data:`FORECAST_WEEKS`. Passing a
    report's ``config.weeks`` of 1 would clamp to 2 below and change the
    forecast, so the report and the panel would disagree.
    """
    weeks = max(2, min(26, int(weeks)))
    scope_sql = await scope_clause(db, vis, project_id, include_subtree)

    # The one `open` definition this whole module shares. Two panels on
    # one screen must not disagree about what is still to do.
    open_where = (
        f"{scope_sql}"
        f" AND t.archived_at IS NULL"
        f" AND ({task_visibility_clause(vis, 't')})"
        f" AND ({triage_exclusion_clause('t')})"
        # D-PM-32(b). An outlook is a forecast, and forecasting the
        # delivery of work that was stopped is the clearest case of all.
        f" AND ({reportable_with_ancestors_clause('t')})"
        f" AND s.category <> ALL(CAST(:closed AS text[]))"
    )
    params: dict[str, Any] = {
        **vis.params,
        **scope_params(project_id),
        "closed": sorted(CLOSING_CATEGORIES),
        "reportable_states": sorted(REPORTABLE_STATUSES),
    }
    period = {
        **vis.params,
        **scope_params(project_id),
        "weeks": weeks,
        "closing": sorted(CLOSING_CATEGORIES),
        "done_cat": COMPLETED_CATEGORY,
        "started_cat": STARTED_CATEGORY,
    }

    rows = (await db.execute(
        text(velocity_sql(f"{scope_sql} AND ({task_visibility_clause(vis, 't')})")),
        period,
    )).fetchall()

    remaining = int((await db.execute(
        text(total_open_sql(open_where)), params,
    )).scalar() or 0)

    left = (await db.execute(
        text(effort_sql(open_where)), params,
    )).one()

    planned = (await db.execute(
        text(planned_finish_sql(open_where)), params,
    )).one()

    cap = (await db.execute(
        text(team_capacity_sql(open_where)),
        {**params, "horizon_days": LEAVING_HORIZON_DAYS},
    )).one()

    velocity = project_forecast(
        remaining_tasks=remaining,
        finished=[int(r.finished or 0) for r in rows],
        created=[int(r.created or 0) for r in rows],
    )
    capacity = capacity_forecast(
        left_mins=int(left.mins or 0),
        left_estimated=int(left.estimated or 0),
        left_tasks=int(left.tasks or 0),
        hours_per_week=float(cap.stated_hours or 0),
    )
    return {
        "project_id": project_id,
        "scope": "portfolio" if project_id is None else "node",
        "include_subtree": include_subtree,
        "weeks": weeks,
        "velocity": velocity,
        "capacity": capacity,
        "plan": {
            "planned_finish": (
                planned.planned_finish.isoformat()
                if planned.planned_finish else None
            ),
            # ⚠️ The coverage. "Planned to finish 12 Mar" over a backlog where
            # 4 of 71 tasks carry a due date is a claim about 4 tasks.
            "dated": int(planned.dated or 0),
            "tasks": int(planned.tasks or 0),
            "slip_days": slip_days(
                planned.planned_finish, velocity.get("finish_date"),
            ),
        },
        "people": {
            "holding_open_work": int(cap.people or 0),
            # `stated_hours` over `with_stated` people. A sum over 2 of 7 is
            # not the team's capacity and looks identical to one that is.
            "with_stated_capacity": int(cap.with_stated or 0),
            "with_schedule_only": int(cap.with_schedule or 0),
            "hours_per_week": float(cap.stated_hours or 0),
            # ⚠️ An engagement that ends inside the forecast window is a risk
            # no velocity can see. `people.end_date` already exists for
            # "assignment past it is a mistake" (spec §6.1); this is the same
            # fact asked at project scale.
            "leaving_within_90d": int(cap.leaving_soon or 0),
        },
    }


# ── Hygiene: open tasks that make every other report wrong — WS-27bn R3c ────


def hygiene_counts_sql(open_where: str) -> str:
    """One count for each kind in :data:`HYGIENE_KINDS`, over open TASKS.

    A task counts in each kind that it breaks, so the four counts do not add
    up to the open total. No assignee join, so a task with two assignees is
    one task here, as in :func:`total_open_sql`.
    """
    counts = ", ".join(
        f"count(*) FILTER (WHERE {predicate}) AS {kind}"
        for kind, predicate in HYGIENE_KINDS
    )
    return (
        f"SELECT {counts}"
        f"  FROM pm_tasks t"
        f"  JOIN pm_task_statuses s ON s.id = t.status_id"
        f" WHERE {open_where}"
    )


def hygiene_rows_sql(open_where: str) -> str:
    """Up to ``:max_named`` tasks of each kind, in kind order.

    The stale kind lists the oldest change first, because that task has
    waited longest. The other kinds list the oldest task first. `id` breaks
    each tie, so two renders of one state list the same rows.

    ⚠️ A `UNION ALL` does not keep the order of its arms. So each arm carries
    its kind's `position` and a `rn` from its own order, and the outer query
    sorts on the two.
    """
    arms = []
    for position, (kind, predicate) in enumerate(HYGIENE_KINDS):
        order = (
            "t.updated_at ASC, t.id"
            if kind == "stale_in_progress"
            else "t.created_at ASC, t.id"
        )
        arms.append(
            f"(SELECT {position} AS position,"
            f"        row_number() OVER (ORDER BY {order}) AS rn,"
            f"        '{kind}' AS kind,"
            f"        t.id, t.title, t.task_number, t.project_id,"
            f"        p.name AS project_name, t.due_at, t.updated_at"
            f"   FROM pm_tasks t"
            f"   JOIN pm_task_statuses s ON s.id = t.status_id"
            f"   JOIN pm_projects p ON p.id = t.project_id"
            f"  WHERE {open_where} AND ({predicate})"
            f"  ORDER BY rn"
            f"  LIMIT :max_named)"
        )
    return (
        "SELECT kind, id, title, task_number, project_id, project_name,"
        "       due_at, updated_at"
        "  FROM (" + " UNION ALL ".join(arms) + ") h"
        " ORDER BY position, rn"
    )


async def hygiene_body(
    db: Any,
    vis: Any,
    *,
    project_id: str | None,
    include_subtree: bool,
) -> dict[str, Any]:
    """The `hygiene` report section, on a session and a visibility the caller
    resolved (`projects_reports.md` §8 R3c).

    Open tasks with no assignee, no due date or no estimate, and tasks in
    progress with no change for :data:`STALE_DAYS` days.

    ⚠️ **"Open" is Load's.** The predicate is :func:`load_open_where` with
    :func:`load_params`, so hygiene and `load` agree about which work is
    open: subtasks count, and triage, archived tasks, closed tasks and the
    work of a stopped project do not (D-PM-32).

    ⚠️ **It reads the state NOW.** No report period reaches it, so a report
    of last week shows today's gaps. That is the question the section asks.

    ``by_kind`` counts every task of each kind. ``rows`` names up to
    :data:`MAX_NAMED` of each, so a reader sees how many the cap cut.
    """
    scope_sql = await scope_clause(db, vis, project_id, include_subtree)
    open_where = load_open_where(scope_sql, vis)
    params: dict[str, Any] = {
        **load_params(vis, project_id),
        "started_cat": STARTED_CATEGORY,
        "stale_days": STALE_DAYS,
    }

    open_total = int((await db.execute(
        text(total_open_sql(open_where)), params,
    )).scalar() or 0)
    counts = (await db.execute(
        text(hygiene_counts_sql(open_where)), params,
    )).one()
    rows = (await db.execute(
        text(hygiene_rows_sql(open_where)),
        {**params, "max_named": MAX_NAMED},
    )).fetchall()

    return {
        "open_total": open_total,
        "stale_days": STALE_DAYS,
        "by_kind": {
            kind: int(getattr(counts, kind, 0) or 0) for kind, _ in HYGIENE_KINDS
        },
        "rows": [
            {
                "kind": r.kind,
                "id": str(r.id),
                "title": r.title,
                "task_number": (
                    int(r.task_number) if r.task_number is not None else None
                ),
                "project_id": str(r.project_id),
                "project_name": r.project_name,
                "due_at": r.due_at.isoformat() if r.due_at else None,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
            for r in rows
        ],
    }
