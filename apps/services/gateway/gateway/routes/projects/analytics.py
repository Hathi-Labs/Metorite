"""Projects · analytics — WS-27bk §9.12.7.

Spec: ``project-docs/specs/project_management_app.md`` §9.12.7.

    GET /projects/analytics/stuck       → (a) where work is stuck
    GET /projects/analytics/load        → (b) who is overloaded
    GET /projects/analytics/throughput  → (c) are we getting faster

The owner asked four questions. (a) is ageing, blocked and overdue — the
operational view, the one that says what needs attention today. (b) splits
open work across the people carrying it. (c) reads the activity spine for
throughput and cycle time, and is the first read here about the PAST rather
than about now.

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

from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends, Query
from gateway.routes.projects.core import (
    CLOSING_CATEGORIES,
    COMPLETED_CATEGORY,
    STARTED_CATEGORY,
    _tenant_session,
    load_visible_project,
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
STALE_BANDS: tuple[tuple[str, int, int | None], ...] = (
    ("under_7d", 0, 7),
    ("7_to_14d", 7, 14),
    ("14_to_30d", 14, 30),
    ("over_30d", 30, None),
)

#: The most blocked tasks named in one response.
#:
#: A list, not a page: this is a dashboard panel, and somebody who needs all of
#: them wants the board with a filter, not an endpoint that scrolls. The total
#: travels beside it so the panel can say "12 of 47" rather than imply 12.
MAX_NAMED = 20


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
            f" AND s.category <> ALL(CAST(:closed AS text[]))"
        )
        params: dict[str, Any] = {
            **vis.params,
            **scope_params(project_id),
            "closed": sorted(CLOSING_CATEGORIES),
        }

        # ── How long has open work sat untouched? ──────────────────────────
        #
        # `updated_at`, not "time in current status". The status history is in
        # `pm_activities` and answering from it means a walk of the whole
        # spine per task. This is the cheap question that is still worth
        # asking, and §9.12.7(c) is where the spine gets read.
        band_sql = " ".join(
            f"count(*) FILTER (WHERE "
            f"t.updated_at <= now() - CAST(:d{low} AS interval)"
            + (
                f" AND t.updated_at > now() - CAST(:d{high} AS interval)"
                if high is not None
                else ""
            )
            + f") AS {name},"
            for name, low, high in STALE_BANDS
        ).rstrip(",")
        for _, low, high in STALE_BANDS:
            params[f"d{low}"] = f"{low} days"
            if high is not None:
                params[f"d{high}"] = f"{high} days"

        stale_row = (await db.execute(
            text(
                f"SELECT {band_sql}"
                f"  FROM pm_tasks t"
                f"  JOIN pm_task_statuses s ON s.id = t.status_id"
                f" WHERE {open_where}"
            ),
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
                f"SELECT t.id, t.title, t.task_number, t.due_at"
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

        overdue = int((await db.execute(
            text(
                f"SELECT count(*) FROM pm_tasks t"
                f"  JOIN pm_task_statuses s ON s.id = t.status_id"
                f" WHERE {open_where}"
                f"   AND t.due_at IS NOT NULL AND t.due_at < now()"
            ),
            params,
        )).scalar() or 0)

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
                }
                for row in blocked_rows
            ],
            "overdue": overdue,
        }


#: The most people named in one load response.
#:
#: Same ruling as :data:`MAX_NAMED`: a dashboard panel, not a page. The total
#: travels beside the list so the panel can say "20 of 34" instead of implying
#: that thirty-four people are twenty.
MAX_PEOPLE = 20


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
        f"       count(*) AS open_tasks"
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
        # one dashboard must not disagree about what "open" means.
        open_where = (
            f"{scope_sql}"
            f" AND t.archived_at IS NULL"
            f" AND ({task_visibility_clause(vis, 't')})"
            f" AND ({triage_exclusion_clause('t')})"
            f" AND s.category <> ALL(CAST(:closed AS text[]))"
        )
        params: dict[str, Any] = {
            **vis.params,
            **scope_params(project_id),
            "closed": sorted(CLOSING_CATEGORIES),
        }

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

        people = [
            {
                # "" is the unassigned bucket. Named explicitly rather than
                # left as an empty string the client has to recognise.
                "assignee": row.who or None,
                "open_tasks": int(row.open_tasks),
                "overdue": int(row.overdue),
                "due_next_7d": int(row.due_next_7d),
                "later": int(row.later),
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
_PERIOD_START = (
    f"(({_WEEK_NOW} - make_interval(weeks => :weeks - 1)) AT TIME ZONE 'UTC')"
)


def cycle_cte_sql(scope_where: str) -> str:
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
    return (
        f"WITH closings AS ("
        f"  SELECT a.task_id, a.created_at AS finished_at,"
        f"         a.meta->>'to_category' AS to_category"
        f"    FROM pm_activities a"
        f"    JOIN pm_tasks t ON t.id = a.task_id"
        f"   WHERE a.type = 'status_change'"
        f"     AND a.deleted_at IS NULL"
        f"     AND a.created_at >= {_PERIOD_START}"
        f"     AND a.meta->>'to_category' = ANY(CAST(:closing AS text[]))"
        f"     AND ({scope_where})"
        f"), per_task AS ("
        f"  SELECT DISTINCT ON (task_id) task_id, finished_at, to_category"
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
        f"  SELECT p.task_id, p.finished_at, p.to_category,"
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


def cycle_summary_sql(scope_where: str) -> str:
    """The same six numbers over the whole window, in ONE bucket.

    ⚠️ Not derivable from :func:`weekly_sql`'s output. A median of weekly
    medians is not the median, and it is wrong in the direction that matters —
    a quiet week with one slow task would weigh the same as a busy week with
    forty fast ones.
    """
    return f"{cycle_cte_sql(scope_where)} SELECT {_CYCLE_MEASURES} FROM cyc c"


def _hours(value: Any) -> float | None:
    """Round an hour figure for transport, or keep the null.

    ⚠️ **None is not zero here.** A window in which nothing measurable finished
    has no median, and sending 0.0 would draw a point on the chart claiming the
    team finished everything instantly.
    """
    return None if value is None else round(float(value), 2)


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
