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

from pathlib import Path

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
        # They are interpolated as column aliases, so anything but a plain
        # identifier is an injection site rather than a typo.
        for name, _, _ in STALE_BANDS:
            assert name.replace("_", "").isalnum()


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
    def test_project_id_is_required(self):
        # A dashboard with no scope is a read of every task in the tenant.
        # Required, not defaulted, so the cost is always the caller's choice.
        assert "project_id: str," in SOURCE
        assert "project_id: str | None" not in SOURCE

    def test_an_unreadable_node_404s_rather_than_reporting_zeroes(self):
        # Zeroes would tell the caller the project exists and is empty.
        assert "load_visible_project(db, vis, project_id)" in SOURCE

    def test_the_subtree_walk_is_recursive(self):
        assert "WITH RECURSIVE sub AS (" in SOURCE
