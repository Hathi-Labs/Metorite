"""D-PM-32(b) — which project states still COUNT in a report.

Owner ruling, 2026-09-19: *"the project that is stopped should not appear in
any reports"*, with the rest left to judgement. The line drawn is **abandoned,
not idle** — queued and paused work stays, because hiding a stalled project
from the one surface that would reveal the stall is how a quarter of its work
goes missing.

These cases are the decision, not the plumbing. The SQL half runs on a real
Postgres in `test_projects_sql_asyncpg.py`; a fake agrees with whatever SQL it
is handed (R8).
"""

from __future__ import annotations

from gateway.routes.projects.core import (
    REPORTABLE_STATUSES,
    is_reportable,
    is_runnable,
)


def project(status: str, archived_at=None) -> dict:
    return {"status": status, "archived_at": archived_at}


class TestWhatCounts:
    def test_active_work_counts(self):
        assert is_reportable(project("active"))

    def test_QUEUED_work_counts(self):
        # Planned but not started. A forecast that cannot see the queue is
        # not a forecast.
        assert is_reportable(project("queued"))

    def test_PAUSED_work_counts(self):
        """⚠️ The decision that took the most thought.

        A paused project is stalled, not abandoned. The report is the only
        place anybody would notice it had stopped moving, so hiding it is how
        the stall becomes permanent.
        """
        assert is_reportable(project("on_hold"))

    def test_STOPPED_work_does_NOT_count(self):
        assert not is_reportable(project("stopped"))

    def test_done_work_does_not_count(self):
        assert not is_reportable(project("done"))

    def test_archived_work_does_not_count_whatever_its_state(self):
        for state in sorted(REPORTABLE_STATUSES):
            assert not is_reportable(project(state, archived_at="2026-09-19"))

    def test_nothing_counts(self):
        assert not is_reportable(None)


class TestItIsNotTheRunnablePredicate:
    """🔴 The two must never be collapsed into one.

    `is_runnable` answers "may automation ACT here". `is_reportable` answers
    "does this work COUNT". They agree on active and on stopped, and they
    DISAGREE on paused — which is the entire reason both exist. A future
    reader who merges them either dispatches agents into paused work or hides
    it from every report.
    """

    def test_they_disagree_on_paused(self):
        paused = project("on_hold")
        assert is_reportable(paused)
        assert not is_runnable(paused)

    def test_they_disagree_on_queued(self):
        queued = project("queued")
        assert is_reportable(queued)
        assert not is_runnable(queued)

    def test_they_agree_on_active_and_on_stopped(self):
        assert is_reportable(project("active")) and is_runnable(project("active"))
        assert not is_reportable(project("stopped"))
        assert not is_runnable(project("stopped"))
