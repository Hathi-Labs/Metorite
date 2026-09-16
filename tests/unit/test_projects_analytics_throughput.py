"""WS-27bk §9.12.7(c) — are we getting faster, against a real Postgres.

Spec: ``project-docs/specs/project_management_app.md`` §9.12.7.

⚠️ **R8, and the spec asks for it by name.** §9.12.7's Done-when says *"Each is
covered against a real database (R8)"*. This suite executes **the route
module's own SQL**, imported rather than copied, exactly as the slice (b) suite
does. ``weekly_sql`` and ``cycle_summary_sql`` are functions for that reason:
an edit to one is an edit to what runs here, which is the property a
transcribed query loses on its first divergence.

Slice (c) reads the **activity spine** instead of the task rows, and that moves
every hazard. A count over `pm_tasks` is wrong when the SET is wrong. A count
over `pm_activities` is wrong when the same task is counted twice, when a
relabel reads as work, or when a duration is measured from the wrong end —
and each of those produces a perfectly plausible number.

The claims, each one a place where an obvious query is wrong and still looks
right:

* **`done` outranks `cancelled` on one task.** A `done → cancelled` tidy-up
  must not read as a second finish, and a `cancelled → done` revival must not
  read as a cancellation. Both are the same tiebreak, pulling in opposite
  directions, and a filter that fixes one by itself breaks the other.
* **`cancelled` is not throughput.** Folding it in makes cancellation the
  cheapest way to raise the number the endpoint reports.
* **one row per TASK.** A task finished, reopened and finished again did one
  piece of work and one piece of rework. Two rows hide the rework inside the
  throughput figure.
* **the clock starts at the FIRST `in_progress`.** Measuring from the last one
  charges nothing for the fortnight a task sat started and untouched.
* **a task with no start is NOT a zero.** ⚠️ This rests on `percentile_cont`
  ignoring nulls, which is a property of Postgres and not of our code. It is
  asserted here rather than trusted.
* **a quiet week is a ZERO, not a missing row.** A chart drawn from grouped
  activity alone runs a straight line across the gap.
* **the window BOUNDS the read.** The spec requires it: without the period
  bound the query walks the whole spine.
"""
from __future__ import annotations

import os
import uuid

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import create_engine, text

from tests.unit._tenant_ladder import apply_ladder

_TENANT_URL = os.environ.get("TENANT_LADDER_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not _TENANT_URL,
    reason=(
        "TENANT_LADDER_DATABASE_URL unset — R8 requires a REAL Postgres. A "
        "skip here is not a pass; CI must set it."
    ),
)

#: The Monday that opens the current week, in UTC, as a `timestamptz`.
#:
#: Every fixture time below is written relative to this rather than as a
#: literal date. A suite that pins "2026-09-14" passes until that week leaves
#: the twelve-week window, and then fails for a reason that has nothing to do
#: with the code.
_MONDAY = "(date_trunc('week', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')"


def _at(*, weeks_ago: int = 0, hours: int = 1) -> str:
    """A SQL expression for a moment inside a chosen week."""
    return (
        f"({_MONDAY} - make_interval(weeks => {weeks_ago})"
        f" + make_interval(hours => {hours}))"
    )


@pytest.fixture(scope="module")
def db():
    eng = create_engine(_TENANT_URL, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    yield eng
    eng.dispose()


@pytest.fixture
def scope(db):
    """One project with a full lane set, torn down after each test.

    Function-scoped and cleaned by hand rather than shared: every test here
    counts rows, so one test's leftovers are another test's wrong answer.
    """
    made: dict[str, str] = {}
    with db.begin() as c:
        org = str(c.execute(
            text("SELECT id FROM organization ORDER BY created_at LIMIT 1")
        ).scalar_one())
        made["org"] = org
        made["project"] = str(c.execute(
            text(
                "INSERT INTO pm_projects (name, status, source, created_by,"
                " organization_id, timezone, parent_project_id, owns_statuses)"
                " VALUES (:n,'active','manual','cycle@example.test',"
                " CAST(:o AS uuid),'Asia/Kolkata',NULL,true) RETURNING id"
            ),
            {"n": f"cycle-{uuid.uuid4().hex[:6]}", "o": org},
        ).scalar_one())
        for name, cat, pos in (
            ("To do", "todo", 0),
            ("In progress", "in_progress", 1),
            ("Done", "done", 2),
            ("Cancelled", "cancelled", 3),
        ):
            made[cat] = str(c.execute(
                text(
                    "INSERT INTO pm_task_statuses (project_id,name,color,"
                    " position,category) VALUES (CAST(:p AS uuid),:n,'gray',"
                    " :pos,:cat) RETURNING id"
                ),
                {"p": made["project"], "n": name, "pos": pos, "cat": cat},
            ).scalar_one())
    yield made
    with db.begin() as c:
        c.execute(
            text(
                "DELETE FROM pm_activities WHERE task_id IN"
                " (SELECT id FROM pm_tasks WHERE project_id = CAST(:p AS uuid))"
            ),
            {"p": made["project"]},
        )
        c.execute(
            text("DELETE FROM pm_tasks WHERE project_id = CAST(:p AS uuid)"),
            {"p": made["project"]},
        )
        c.execute(
            text(
                "DELETE FROM pm_task_statuses WHERE project_id = CAST(:p AS uuid)"
            ),
            {"p": made["project"]},
        )
        c.execute(
            text("DELETE FROM pm_projects WHERE id = CAST(:p AS uuid)"),
            {"p": made["project"]},
        )


def _task(conn, scope, *, title, category="done", archived=False):
    return str(conn.execute(
        text(
            "INSERT INTO pm_tasks (title, project_id, root_project_id,"
            " status_id, created_by, organization_id, task_number, archived_at)"
            " SELECT :t, CAST(:p AS uuid), CAST(:p AS uuid), CAST(:s AS uuid),"
            " 'cycle@example.test', CAST(:o AS uuid),"
            " COALESCE(MAX(task_number),0)+1,"
            " CASE WHEN :arch THEN now() ELSE NULL END"
            " FROM pm_tasks WHERE root_project_id = CAST(:p AS uuid)"
            " RETURNING id"
        ),
        {
            "t": title, "p": scope["project"], "s": scope[category],
            "o": scope["org"], "arch": archived,
        },
    ).scalar_one())


def _move(conn, scope, task_id, *, frm, to, at):
    """Write one `status_change` activity, at a chosen instant.

    `at` is a SQL expression, not a value — see `_at`.
    """
    conn.execute(
        text(
            "INSERT INTO pm_activities (task_id, organization_id, type,"
            " created_by, body, meta, created_at)"
            f" VALUES (CAST(:t AS uuid), CAST(:o AS uuid), 'status_change',"
            f" 'cycle@example.test', :b, CAST(:m AS jsonb), {at})"
        ),
        {
            "t": task_id, "o": scope["org"], "b": f"{frm} -> {to}",
            "m": f'{{"from_category": "{frm}", "to_category": "{to}"}}',
        },
    )


def _run(db, scope, *, weeks=12):
    """Execute the ROUTE MODULE'S OWN queries. Imported, never transcribed."""
    from gateway.routes.projects.analytics import (
        cycle_summary_sql,
        weekly_sql,
    )

    # The route builds this from the caller's scope and grants. Here it is the
    # project alone, which is the part these claims are about.
    where = "t.project_id = CAST(:pid AS uuid)"
    params = {
        "pid": scope["project"],
        "weeks": weeks,
        "closing": ["cancelled", "done"],
        "done_cat": "done",
        "started_cat": "in_progress",
    }
    with db.connect() as c:
        series = c.execute(text(weekly_sql(where)), params).fetchall()
        summary = c.execute(text(cycle_summary_sql(where)), params).one()
    return series, summary


def _week(series, weeks_ago: int):
    """The bucket `weeks_ago` weeks back. The last row is the current week."""
    return series[-1 - weeks_ago]


class TestTheVocabularyIsTheSHAREDOne:
    """R7 — the fence for the two words this slice splits out.

    `COMPLETED_CATEGORY` and `STARTED_CATEGORY` live in `core` beside
    `CLOSING_CATEGORIES`, not in the endpoint. A literal in the route module is
    the second status vocabulary that `test_projects_analytics`'s shape fence
    refuses, and it is how a category rename leaves one read counting a word
    nothing writes any more.
    """

    def test_both_words_are_real_categories(self):
        from gateway.routes.projects.core import (
            CLOSING_CATEGORIES,
            COMPLETED_CATEGORY,
            STARTED_CATEGORY,
            STATUS_CATEGORIES,
        )

        assert COMPLETED_CATEGORY in STATUS_CATEGORIES
        assert STARTED_CATEGORY in STATUS_CATEGORIES
        # ⚠️ Completion is a SUBSET of closing, never the whole of it. If these
        # two ever became equal, cancellations would count as throughput and
        # every assertion below would still pass.
        assert COMPLETED_CATEGORY in CLOSING_CATEGORIES
        assert STARTED_CATEGORY not in CLOSING_CATEGORIES
        assert CLOSING_CATEGORIES - {COMPLETED_CATEGORY}

    def test_the_endpoint_writes_NEITHER_word_itself(self):
        import inspect

        from gateway.routes.projects import analytics

        source = inspect.getsource(analytics)
        assert "COMPLETED_CATEGORY" in source
        # The shape fence in `test_projects_analytics` bans `"done"` outright.
        # This is the other half: the module must not spell `in_progress`
        # either, which that fence does not cover.
        assert '"in_progress"' not in source


class TestACrossingIsACompletion:
    """A state is not an event, and the difference doubles a number."""

    async def test_a_move_into_done_counts_once(self, db, scope):
        with db.begin() as c:
            t = _task(c, scope, title="shipped")
            _move(c, scope, t, frm="in_progress", to="done", at=_at(hours=2))
        series, summary = _run(db, scope)
        assert _week(series, 0).completed == 1
        assert summary.completed == 1

    async def test_a_done_to_cancelled_RELABEL_is_not_a_second_finish(
        self, db, scope,
    ):
        """Tidying a finished backlog must not double a team's output.

        Two closing crossings land on one task. The `done` tiebreak in
        ``per_task`` is what keeps the completion and discards the relabel.
        """
        with db.begin() as c:
            t = _task(c, scope, title="finished then relabelled")
            _move(c, scope, t, frm="in_progress", to="done", at=_at(hours=2))
            _move(c, scope, t, frm="done", to="cancelled", at=_at(hours=3))
        _, summary = _run(db, scope)
        assert summary.completed == 1
        assert summary.cancelled == 0

    async def test_a_cancelled_task_revived_STRAIGHT_into_done_still_counts(
        self, db, scope,
    ):
        """⚠️ The case that killed an earlier `from_category` filter.

        Excluding crossings that leave a closing category removes this
        completion entirely, and the task then reports as a cancellation. The
        work happened. Nothing about the move out of `cancelled` makes the
        arrival at `done` less real.
        """
        with db.begin() as c:
            t = _task(c, scope, title="revived in place")
            _move(c, scope, t, frm="todo", to="cancelled",
                  at=_at(weeks_ago=3, hours=1))
            _move(c, scope, t, frm="cancelled", to="done", at=_at(hours=2))
        series, summary = _run(db, scope)
        assert summary.completed == 1
        assert summary.cancelled == 0
        assert _week(series, 0).completed == 1


class TestCancelledIsNotThroughput:
    """Otherwise cancelling everything is the fastest team in the company."""

    async def test_it_is_counted_BESIDE_the_completions(self, db, scope):
        with db.begin() as c:
            done = _task(c, scope, title="done")
            _move(c, scope, done, frm="in_progress", to="done", at=_at(hours=2))
            killed = _task(c, scope, title="dropped", category="cancelled")
            _move(c, scope, killed, frm="todo", to="cancelled", at=_at(hours=2))
        _, summary = _run(db, scope)
        assert summary.completed == 1
        assert summary.cancelled == 1

    async def test_a_cancellation_never_reaches_the_cycle_time(self, db, scope):
        """A cancelled task's duration is not how long work takes."""
        with db.begin() as c:
            killed = _task(c, scope, title="dropped", category="cancelled")
            _move(c, scope, killed, frm="todo", to="in_progress",
                  at=_at(weeks_ago=6, hours=1))
            _move(c, scope, killed, frm="in_progress", to="cancelled",
                  at=_at(hours=2))
        _, summary = _run(db, scope)
        assert summary.cancelled == 1
        assert summary.measured == 0
        assert summary.median_hours is None


class TestOneRowPerTask:
    """Rework is not throughput, and it must not hide inside it."""

    async def test_finished_reopened_and_finished_again_counts_ONCE(
        self, db, scope,
    ):
        with db.begin() as c:
            t = _task(c, scope, title="bounced")
            _move(c, scope, t, frm="in_progress", to="done",
                  at=_at(weeks_ago=2, hours=2))
            _move(c, scope, t, frm="done", to="in_progress",
                  at=_at(weeks_ago=1, hours=2))
            _move(c, scope, t, frm="in_progress", to="done", at=_at(hours=2))
        series, summary = _run(db, scope)
        assert summary.completed == 1
        # And it lands in the week it was FIRST finished, not the last.
        assert _week(series, 2).completed == 1
        assert _week(series, 0).completed == 0

    async def test_a_task_cancelled_then_genuinely_finished_is_a_completion(
        self, db, scope,
    ):
        """⚠️ The `done` tiebreak. Chronology alone would file this as a
        cancellation, and the work that followed would disappear."""
        with db.begin() as c:
            t = _task(c, scope, title="revived")
            _move(c, scope, t, frm="todo", to="cancelled",
                  at=_at(weeks_ago=3, hours=1))
            _move(c, scope, t, frm="cancelled", to="in_progress",
                  at=_at(weeks_ago=2, hours=1))
            _move(c, scope, t, frm="in_progress", to="done", at=_at(hours=2))
        _, summary = _run(db, scope)
        assert summary.completed == 1
        assert summary.cancelled == 0


class TestTheClockStartsAtTheFirstInProgress:
    """Measuring from the last start charges nothing for the idle fortnight."""

    async def test_an_earlier_start_is_the_one_that_counts(self, db, scope):
        with db.begin() as c:
            t = _task(c, scope, title="started twice")
            _move(c, scope, t, frm="todo", to="in_progress",
                  at=_at(weeks_ago=2, hours=0))
            _move(c, scope, t, frm="in_progress", to="todo",
                  at=_at(weeks_ago=1, hours=0))
            _move(c, scope, t, frm="todo", to="in_progress",
                  at=_at(hours=0))
            _move(c, scope, t, frm="in_progress", to="done", at=_at(hours=10))
        _, summary = _run(db, scope)
        assert summary.measured == 1
        # Two whole weeks plus ten hours. The LAST start would say ten.
        assert summary.median_hours == pytest.approx(2 * 7 * 24 + 10, abs=0.1)

    async def test_a_start_OLDER_than_the_window_still_counts(self, db, scope):
        """⚠️ The start walk is deliberately unbounded in time.

        Clipping it at the window edge would report every long-running task as
        fast, which is the exact opposite of what the question asks.
        """
        with db.begin() as c:
            t = _task(c, scope, title="long haul")
            _move(c, scope, t, frm="todo", to="in_progress",
                  at=_at(weeks_ago=40, hours=0))
            _move(c, scope, t, frm="in_progress", to="done", at=_at(hours=0))
        _, summary = _run(db, scope)
        assert summary.measured == 1
        assert summary.median_hours == pytest.approx(40 * 7 * 24, abs=0.1)


class TestNoStartIsNotAZero:
    """⚠️ Rests on `percentile_cont` ignoring nulls. Asserted, not assumed."""

    async def test_unstarted_work_is_counted_and_left_out_of_the_median(
        self, db, scope,
    ):
        with db.begin() as c:
            measured = _task(c, scope, title="measured")
            _move(c, scope, measured, frm="todo", to="in_progress",
                  at=_at(hours=0))
            _move(c, scope, measured, frm="in_progress", to="done",
                  at=_at(hours=10))
            straight = _task(c, scope, title="never started")
            _move(c, scope, straight, frm="todo", to="done", at=_at(hours=2))
        _, summary = _run(db, scope)
        assert summary.completed == 2
        assert summary.measured == 1
        assert summary.no_start == 1
        # ⚠️ 10, not 5. A zero for the unstarted task would halve it.
        assert summary.median_hours == pytest.approx(10, abs=0.1)


class TestAQuietWeekIsAZero:
    """A missing row draws a straight line across the gap."""

    async def test_every_week_in_the_window_is_on_the_axis(self, db, scope):
        with db.begin() as c:
            t = _task(c, scope, title="lonely")
            _move(c, scope, t, frm="in_progress", to="done",
                  at=_at(weeks_ago=3, hours=2))
        series, _ = _run(db, scope, weeks=8)
        assert len(series) == 8
        assert _week(series, 3).completed == 1
        assert [_week(series, n).completed for n in (0, 1, 2, 4)] == [0, 0, 0, 0]

    async def test_an_empty_week_has_no_median_rather_than_a_zero(
        self, db, scope,
    ):
        """A zero here is a point on the chart claiming instant delivery."""
        series, _ = _run(db, scope, weeks=4)
        assert len(series) == 4
        assert all(row.median_hours is None for row in series)
        assert all(row.completed == 0 for row in series)


class TestTheWindowBoundsTheRead:
    """The spec requires it, or the query walks the whole spine."""

    async def test_a_completion_older_than_the_window_is_excluded(
        self, db, scope,
    ):
        with db.begin() as c:
            old = _task(c, scope, title="ancient")
            _move(c, scope, old, frm="in_progress", to="done",
                  at=_at(weeks_ago=9, hours=2))
            recent = _task(c, scope, title="recent")
            _move(c, scope, recent, frm="in_progress", to="done",
                  at=_at(weeks_ago=1, hours=2))
        series, summary = _run(db, scope, weeks=4)
        assert len(series) == 4
        assert summary.completed == 1
        assert _week(series, 1).completed == 1


class TestTheSummaryIsNotAMedianOfMedians:
    """A quiet week must not weigh as much as a busy one."""

    async def test_it_is_the_median_over_every_task_in_the_window(
        self, db, scope,
    ):
        with db.begin() as c:
            # Three fast tasks one week, one very slow task the next.
            for n in range(3):
                fast = _task(c, scope, title=f"fast-{n}")
                _move(c, scope, fast, frm="todo", to="in_progress",
                      at=_at(weeks_ago=2, hours=0))
                _move(c, scope, fast, frm="in_progress", to="done",
                      at=_at(weeks_ago=2, hours=2))
            slow = _task(c, scope, title="slow")
            _move(c, scope, slow, frm="todo", to="in_progress",
                  at=_at(weeks_ago=1, hours=0))
            _move(c, scope, slow, frm="in_progress", to="done",
                  at=_at(weeks_ago=1, hours=100))
        series, summary = _run(db, scope)
        assert summary.measured == 4
        # Four values: 2, 2, 2, 100. The median is 2.
        # A median of the two weekly medians (2 and 100) would say 51.
        assert summary.median_hours == pytest.approx(2, abs=0.1)
        assert _week(series, 2).median_hours == pytest.approx(2, abs=0.1)
        assert _week(series, 1).median_hours == pytest.approx(100, abs=0.1)


class TestArchivedWorkStillCounts:
    """History is what happened, and tidying up in September is not July."""

    async def test_archiving_a_finished_task_does_not_lower_its_week(
        self, db, scope,
    ):
        """⚠️ A deliberate divergence from `stuck` and `load`.

        Those two exclude archived tasks because they describe OPEN work. This
        one describes the past, and letting an archive sweep rewrite a past
        week makes the trend a record of housekeeping.
        """
        with db.begin() as c:
            t = _task(c, scope, title="filed away", archived=True)
            _move(c, scope, t, frm="in_progress", to="done",
                  at=_at(weeks_ago=2, hours=2))
        series, summary = _run(db, scope)
        assert summary.completed == 1
        assert _week(series, 2).completed == 1
