"""WS-27bk §9.12.7(a) — where work is stuck.

Spec: ``project-docs/specs/project_management_app.md`` §9.12.7.

The claims worth pinning are the ones where a plausible implementation is wrong
in a way that reads as fine:

* **the bands are DISJOINT.** Cumulative bands double-count, so the numbers do
  not add up to the total and a chart drawn from them lies about its own
  proportions while every value is individually explicable.
* **a closed blocker is not a block.** Counting every ``blocks`` edge reports a
  project as blocked by work it already finished, which is how a blocked count
  becomes noise people mute.
* **a closed task is not stuck.** Ageing over everything colours finished work
  red forever, which is the same lesson ``overdue`` learned in WS-27k.
* **visibility is the caller's.** A roll-up that skipped the grant clause would
  be a disclosure channel wearing a dashboard's clothes.

⚠️ **R8 — the SQL in this module was run against the live tenant database**
before it merged, not only against these assertions. A hermetic test agrees
with whatever SQL it is handed, and the four joins here are exactly where a
wrong column name hides. What this file pins is the SHAPE the SQL must keep.
"""

from __future__ import annotations

import inspect
from pathlib import Path

from gateway.routes.projects import analytics
from gateway.routes.projects.analytics import MAX_NAMED, STALE_BANDS

REPO = Path(__file__).resolve().parents[2]
SOURCE = (
    REPO / "apps/services/gateway/gateway/routes/projects/analytics.py"
).read_text(encoding="utf-8")


class TestStaleBands:
    def test_bands_are_disjoint_and_ascending(self):
        # Each band starts where the one before it ended. Overlap would
        # double-count, and a gap would drop tasks out of every band.
        for (_, _, high), (_, next_low, _) in zip(STALE_BANDS, STALE_BANDS[1:]):
            assert high == next_low

    def test_the_first_band_starts_at_zero(self):
        assert STALE_BANDS[0][1] == 0

    def test_the_last_band_is_open_ended(self):
        # Where genuinely forgotten work collects. A closed top band would
        # silently drop everything older than it.
        assert STALE_BANDS[-1][2] is None

    def test_every_band_has_a_distinct_name(self):
        names = [name for name, _, _ in STALE_BANDS]
        assert len(names) == len(set(names))

    def test_names_are_safe_as_sql_identifiers(self):
        """⚠️ This assertion USED TO PASS on a name Postgres cannot parse.

        It asked `name.replace("_", "").isalnum()`, and `"7to14d".isalnum()`
        is True. So `7_to_14d` sailed through, went out as a bare column
        alias, and Postgres read it as a numeric literal with trailing junk.
        `/analytics/stuck` answered 500 at EVERY scope from the day it merged
        (2026-09-16) until 2026-09-17, and no test failed.

        A fence that admits the value it exists to reject is worse than no
        fence, because it is also a claim that somebody checked.
        """
        for name, _, _ in STALE_BANDS:
            assert name, "an empty alias is not an identifier"
            # The actual rule, and the half the old assertion missed: an
            # unquoted SQL identifier may not BEGIN with a digit.
            assert not name[0].isdigit(), (
                f"{name!r} starts with a digit — Postgres reads that as a"
                " numeric literal, not as a column alias"
            )
            assert name[0] == "_" or name[0].isalpha()
            assert all(c == "_" or c.isalnum() for c in name)
            assert name.islower(), "an unquoted identifier folds to lowercase"


class TestTheSqlKeepsItsShape:
    def test_open_work_only_through_the_shared_vocabulary(self):
        # CLOSING_CATEGORIES, never a second literal list. A category added to
        # the closed set must not leave this endpoint counting finished work.
        assert "CLOSING_CATEGORIES" in SOURCE
        assert '"done"' not in SOURCE
        assert "'done'" not in SOURCE

    def test_the_visibility_clause_is_applied(self):
        assert "task_visibility_clause(vis, 't')" in SOURCE

    def test_triage_is_excluded(self):
        # The intake queue must not leak into a dashboard either — the ONE
        # predicate is core.triage_exclusion_clause.
        assert "triage_exclusion_clause('t')" in SOURCE

    def test_archived_work_is_excluded(self):
        assert "t.archived_at IS NULL" in SOURCE

    def test_a_closed_BLOCKER_does_not_count_as_a_block(self):
        # The EXISTS joins the blocker's own status and excludes the closed
        # categories. Without that, a finished blocker still reads as a block.
        assert "bs.category <> ALL(CAST(:closed AS text[]))" in SOURCE
        assert "b.archived_at IS NULL" in SOURCE

    def test_the_blocker_is_the_SOURCE_of_the_link(self):
        # `filters._WINDOW_LINKS_SQL` is the canonical direction:
        # source_task_id AS blocker, target_task_id AS blocked. Reversed, this
        # endpoint names exactly the wrong tasks.
        assert "l.target_task_id = t.id" in SOURCE
        assert "b.id = l.source_task_id" in SOURCE

    def test_only_blocks_links_count(self):
        # `relates_to` and `duplicates` assert no sequence (WS-27p's
        # DIRECTED_TYPES), so counting them would invent one.
        assert "l.link_type = 'blocks'" in SOURCE

    def test_overdue_means_past_due_AND_open(self):
        # A finished task with a past due date is not overdue, it is done.
        # `open_where` carries the open half, so this only adds the date.
        assert "t.due_at IS NOT NULL AND t.due_at < now()" in SOURCE

    def test_the_named_list_is_capped_and_the_total_travels_with_it(self):
        assert MAX_NAMED > 0
        # The CONSTANT, not its value: a hardcoded 20 in the SQL would drift
        # from the number the response documents.
        assert "LIMIT {MAX_NAMED}" in SOURCE
        assert '"blocked_total"' in SOURCE

    def test_the_blocked_list_is_OLDEST_first(self):
        # The thing blocked longest is the thing to ask about. Newest-first
        # shows a different list every day while the real problem sits below.
        assert "ORDER BY t.created_at ASC" in SOURCE

    def test_no_metric_is_left_to_the_client(self):
        # Every number leaves as a number. Returning raw rows for the browser
        # to count would be a count of one page — the endpoint is paginated
        # everywhere else in this package.
        for key in ('"stale"', '"blocked_total"', '"overdue"'):
            assert key in SOURCE


class TestScope:
    def test_the_portfolio_is_a_scope_rather_than_a_missing_one(self):
        # ⚠️ This test asserted the OPPOSITE until 2026-09-16: that
        # `project_id` was required, "so the cost is always the caller's
        # choice". That fence outlived its reasoning and then contradicted
        # §9.12.7's own Done-when, which says each slice answers "for a
        # subtree AND for the portfolio". It also left the Analytics pane
        # unable to call any of these endpoints, because that pane reads the
        # portfolio roll-up and holds no node id.
        #
        # The cost concern was real and is unchanged: the bound was never the
        # node, it is the visibility clause and the tenant session. See
        # `test_projects_analytics_scope.py`, which proves that against a real
        # database with a second organization present.
        assert "project_id: str | None = None," in SOURCE
        assert "project_id: str," not in SOURCE

    def test_the_scope_is_resolved_in_ONE_place(self):
        # One helper per endpoint, whatever the endpoint count is today.
        #
        # ⚠️ This asserted the literal 3 until slice (d) added a fourth, and
        # the fence did its job — it failed, and it was RIGHT to. But a count
        # that must be hand-edited beside every new route is a fence that
        # teaches people to bump the number, which is the one edit that always
        # passes. Counted against the routes instead, so a fifth endpoint that
        # builds its own scope FAILS rather than needing a new literal.
        endpoints = SOURCE.count('@router.get("/analytics/')
        assert endpoints >= 4, "an analytics endpoint disappeared"
        # WS-27bn R3c. `hygiene_body` resolves its scope for the report
        # section, and its route is a later slice (projects_reports.md §8
        # R3c non-goals). It is the one body with no route. The day a route
        # awaits it, the second assert fails, and this tuple must go.
        bodies_with_no_route = ("hygiene_body",)
        for body in bodies_with_no_route:
            assert f"async def {body}(" in SOURCE, body
            assert f"await {body}(" not in SOURCE, f"{body} has a route now"
        # The +1 belongs to hygiene_body, so a stray call elsewhere cannot
        # borrow it.
        body = inspect.getsource(analytics.hygiene_body)
        assert "await scope_clause(" in body
        assert SOURCE.count("await scope_clause(db, vis, project_id") == (
            endpoints + len(bodies_with_no_route)
        )

    def test_an_unreadable_node_404s_rather_than_reporting_zeroes(self):
        # Zeroes would tell the caller the project exists and is empty.
        assert "load_visible_project(db, vis, project_id)" in SOURCE

    def test_the_subtree_walk_is_recursive(self):
        assert "WITH RECURSIVE sub AS (" in SOURCE
