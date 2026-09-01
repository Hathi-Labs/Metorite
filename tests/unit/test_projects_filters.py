"""WS-27k — filters and saved views.

Spec: `project-docs/specs/project_management_app.md` §11.2 item 3.

*"My open bugs in Ops, grouped by assignee"* is a daily question the board could
not answer. This is the filter half.

The claims worth pinning are the ones where a wrong answer is *plausible* rather
than obviously broken:

* **every filter is a WHERE clause.** Pagination is done in SQL, so a filter
  applied in Python after `LIMIT` returns short pages — "page 2 is empty but
  there are 40 more" is a bug people work around for months instead of
  reporting.
* **`overdue` means past due AND still open.** A finished task with a past due
  date is not overdue, and colouring it red forever is how a board teaches
  people to ignore red.
* **an unknown status category is a 422, not an empty board.** A client
  filtering on `in-progress` (hyphen) must not conclude the project is empty.
* **a saved view and the same filters typed by hand produce one query.** Two
  implementations would drift, and a *saved* view that shows a different set is
  the one thing it may not do.

Pure functions, tested directly. No Postgres, no fake.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import HTTPException
from gateway.routes.projects.filters import (
    CLOSED_CATEGORIES,
    GROUP_BY,
    MAX_MULTI,
    MIN_QUERY,
    STATUS_CATEGORIES,
    VIEW_FILTER_KEYS,
    build_task_filters,
    normalise_view_config,
    split_csv,
)

REPO = Path(__file__).resolve().parents[2]


def sql(**kwargs) -> str:
    return " ".join(build_task_filters(**kwargs)[0])


def bound(**kwargs) -> dict:
    return build_task_filters(**kwargs)[1]


# ── The vocabulary matches the schema ───────────────────────────────────────

def migrated_status_categories() -> set[str]:
    """The category vocabulary as the DATABASE will enforce it.

    Assembled from every migration that constrains `pm_task_statuses.category`,
    taking the LAST one in file order — 146 creates the CHECK inline and 164
    (WS-27u) replaces it wholesale to admit `triage`, so the newest definition
    is the one that survives a full replay. The same aggregation
    `test_projects_migration._activity_check_values` does for the activity
    vocabulary, and for the same reason: a mirror pinned to the file that
    CREATED the check goes quietly stale the day a later file widens it.

    Scoped to files that name `pm_task_statuses`, because `feature_catalog`
    (130/140) constrains a `category` column of its own.
    """
    latest: str | None = None
    for path in sorted((REPO / "infra/postgres").glob("*.sql")):
        if path.name == "schema.generated.sql":
            continue
        text = "\n".join(
            re.sub(r"--.*$", "", line)
            for line in path.read_text(encoding="utf-8").splitlines()
        )
        if "pm_task_statuses" not in text:
            continue
        for match in re.finditer(
            r"CHECK\s*\(\s*category\s+IN\s*\((.*?)\)\s*\)", text, re.S | re.I,
        ):
            latest = match.group(1)
    assert latest is not None, "no migration constrains pm_task_statuses.category"
    return set(re.findall(r"'([a-z_]+)'", latest))


def test_the_categories_are_the_ones_the_database_has():
    """Read from the migrations, not restated. A filter vocabulary that drifts
    from the CHECK is a filter that silently matches nothing."""
    assert migrated_status_categories() == set(STATUS_CATEGORIES)


def test_closed_means_done_or_cancelled():
    assert set(CLOSED_CATEGORIES) == {"done", "cancelled"}


# ── Nothing matches everything by accident ─────────────────────────────────

def test_the_default_query_still_excludes_archived_tasks():
    """The one clause that is present when nothing is asked for."""
    assert "t.archived_at IS NULL" in sql()


def test_asking_for_archived_drops_that_clause_and_adds_nothing_else():
    assert build_task_filters(include_archived=True) == ([], {})


def test_no_filter_leaks_a_parameter_it_did_not_use():
    """A stray bound parameter is how a query starts failing with "parameter
    not used" once a clause is refactored away."""
    clauses, params = build_task_filters(overdue=True)
    joined = " ".join(clauses)
    for name in params:
        assert f":{name}" in joined, f"{name} is bound but never referenced"


# ── overdue ────────────────────────────────────────────────────────────────

def test_overdue_excludes_finished_work():
    """Past due AND still open. Without the second half a done task stays red
    forever, and a board with permanent red is a board nobody reads."""
    clause = sql(overdue=True)
    assert "t.due_at < now()" in clause
    assert "NOT EXISTS" in clause
    assert bound(overdue=True)["closed"] == list(CLOSED_CATEGORIES)


def test_overdue_is_absent_unless_asked_for():
    assert "due_at < now()" not in sql()


# ── assignees ──────────────────────────────────────────────────────────────

def test_assignee_matching_is_case_insensitive_on_both_sides():
    """R10 — `pm_task_assignees` stores lowercased addresses, and a filter that
    compared raw text would match nothing for anyone who typed a capital."""
    assert "lower(a.assignee)" in sql(assignee="Priya@Fracktal.IN")
    assert bound(assignee="Priya@Fracktal.IN")["assignees"] == ["priya@fracktal.in"]


def test_the_single_and_multi_assignee_filters_combine():
    """Both spellings exist (one for a URL a human types, one for a saved
    view), and they must not silently drop each other."""
    params = bound(assignee="a@x.co", assignees="b@x.co,c@x.co")
    assert params["assignees"] == ["a@x.co", "b@x.co", "c@x.co"]


def test_a_duplicate_assignee_is_bound_once():
    assert bound(assignee="a@x.co", assignees="A@X.co")["assignees"] == ["a@x.co"]


def test_unassigned_is_a_NOT_EXISTS_not_an_empty_string_match():
    """"Nobody" is the absence of a row, not an assignee whose address is ''."""
    clause = sql(unassigned=True)
    assert "NOT EXISTS" in clause
    assert "pm_task_assignees" in clause


def test_a_multi_filter_cannot_be_unbounded():
    """An `IN` list a client controls is a way to hand the database a megabyte
    of literals."""
    huge = ",".join(f"p{i}@x.co" for i in range(MAX_MULTI + 50))
    assert len(bound(assignees=huge)["assignees"]) == MAX_MULTI


# ── categories ─────────────────────────────────────────────────────────────

def test_an_unknown_category_is_refused_and_lists_the_real_ones():
    with pytest.raises(HTTPException) as exc:
        build_task_filters(status_category="in-progress")
    assert exc.value.status_code == 422
    for known in STATUS_CATEGORIES:
        assert known in str(exc.value.detail)


def test_categories_are_matched_through_the_status_row():
    """The category belongs to the STATUS. Denormalising it onto the task would
    need re-stamping every task whenever a lane is recategorised."""
    clause = sql(status_category="todo,in_progress")
    assert "pm_task_statuses" in clause
    assert bound(status_category="todo,in_progress")["categories"] == [
        "todo", "in_progress",
    ]


def test_a_trailing_comma_is_a_typo_not_a_request_for_nothing():
    assert split_csv("todo, ,in_progress,") == ["todo", "in_progress"]
    assert split_csv(None) == []


# ── search ─────────────────────────────────────────────────────────────────

def test_search_covers_the_description_as_well_as_the_title():
    assert "t.description ILIKE" in sql(q="extruder")


def test_a_whitespace_only_search_is_not_a_filter():
    """`%   %` matches every task with a space in it — i.e. everything, slowly."""
    assert "ILIKE" not in sql(q="   ")


# ── the minimum, and the two ways it could be got wrong (WS-27be) ──────────
#
# `search.py` has enforced `MIN_QUERY` since it was written; the list endpoint
# never did, so `GET /projects/tasks?q=a` was an unbounded substring scan at the
# one surface with nothing capping it. These four pin the fix and both of the
# plausible wrong fixes.

@pytest.mark.parametrize("term", ["a", "#", " x "])
def test_a_query_below_the_minimum_runs_no_substring_scan(term):
    assert "ILIKE" not in sql(q=term)


@pytest.mark.parametrize("term", ["a", "#", " x "])
def test_a_query_below_the_minimum_matches_nothing_rather_than_everything(term):
    """The wrong fix, and the reason this test exists.

    Dropping the clause is the obvious implementation and it is worse than the
    bug: a one-character `q` would return the WHOLE board, so a client that
    sent it renders an unfiltered list while believing it is filtered. The
    contract is the one `/projects/search` already keeps — you asked for a
    substring search and got the tasks that match; there are none, because we
    will not run a scan that short.
    """
    clauses, params = build_task_filters(q=term)
    assert "FALSE" in clauses, (
        f"{term!r} is below MIN_QUERY and must narrow the result to nothing; "
        f"omitting the clause returns every task instead"
    )
    assert "q" not in params, "nothing should be bound for a query that never runs"


def test_the_minimum_is_the_one_search_already_enforces():
    """One rule, two surfaces — and only one place it is written down.

    `search.py` re-exports this constant rather than declaring its own. Two
    copies of a threshold is how a palette that refuses `a` and a list endpoint
    that accepts it come back — which is the bug WS-27be fixed.

    ⚠️ The value comparison alone would NOT catch a second declaration: CPython
    caches small integers, so `search.MIN_QUERY is filters.MIN_QUERY` is true
    even when `search.py` writes its own `MIN_QUERY = 2`. The structural half is
    what makes this a fence.
    """
    from gateway.routes.projects import search as search_mod

    assert search_mod.MIN_QUERY == MIN_QUERY
    source = (
        REPO / "apps" / "services" / "gateway" / "gateway" / "routes"
        / "projects" / "search.py"
    ).read_text(encoding="utf-8")
    assert not re.search(r"^MIN_QUERY\s*=", source, re.M), (
        "search.py declares its own MIN_QUERY — import it from filters instead, "
        "or the two surfaces will disagree the first time one is edited"
    )


@pytest.mark.parametrize("term", ["ab", "abc", "extruder"])
def test_a_query_at_or_above_the_minimum_still_searches(term):
    clauses, params = build_task_filters(q=term)
    assert "(t.title ILIKE :q OR t.description ILIKE :q)" in clauses
    assert params["q"] == f"%{term}%"
    assert len(term) >= MIN_QUERY


# ── due_before ─────────────────────────────────────────────────────────────

def test_a_timestamp_filter_binds_a_datetime_not_the_string_it_arrived_as():
    """Found against a real Postgres, invisible to a fake.

    ``CAST(:x AS timestamptz)`` reads like it would take the string, but asyncpg
    infers the parameter's type from that cast and then refuses to *encode* a
    `str` — the query never reaches the database and the endpoint answers 500.
    So the type of the bound value is the claim, not the shape of the SQL.
    """
    params = bound(due_before="2026-08-31")
    assert isinstance(params["due_before"], datetime)


def test_no_clause_casts_a_bound_parameter_to_a_timestamp():
    """The general form, and the reason this is worth a second test.

    Every timestamp cast over a bound parameter is this bug: asyncpg reads the
    cast, decides the parameter is a timestamp, and refuses the string. A future
    `after=` or `created_since=` filter written the obvious way fails here
    rather than the first time somebody clicks it.
    """
    clauses, _ = build_task_filters(
        due_before="2026-08-31", overdue=True, q="x", assignee="a@x.co",
        status_category="todo", importance_gte=1, parent_task_id=None,
    )
    for clause in clauses:
        assert not re.search(r"CAST\s*\(\s*:\w+\s+AS\s+timestamp", clause, re.I), (
            f"{clause!r} casts a bound parameter to a timestamp — parse it into "
            f"a datetime with `parse_when` instead, or asyncpg will 500"
        )


def test_a_bare_date_and_a_full_timestamp_both_work():
    """A human types a date; a saved view stores what the picker produced."""
    assert bound(due_before="2026-08-31")["due_before"] == datetime(
        2026, 8, 31, tzinfo=UTC,
    )
    assert bound(due_before="2026-08-31T17:00:00Z")["due_before"] == datetime(
        2026, 8, 31, 17, 0, tzinfo=UTC,
    )


def test_a_naive_timestamp_is_read_as_UTC_rather_than_left_ambiguous():
    """`due_at` is a timestamptz and `overdue` compares against `now()`, so a
    value with no zone has to be given one somewhere. Doing it here beats
    inheriting whatever the connection's TimeZone happens to be."""
    assert bound(due_before="2026-08-31T17:00:00")["due_before"].tzinfo is not None


def test_an_unparseable_timestamp_is_a_422_and_says_what_was_expected():
    """`due_before=tomorrow` is the client's mistake. Before this it was a 500,
    which reads as a server fault and gets reported as an outage."""
    with pytest.raises(HTTPException) as exc:
        build_task_filters(due_before="tomorrow")
    assert exc.value.status_code == 422
    assert "due_before" in str(exc.value.detail)
    assert "ISO 8601" in str(exc.value.detail)


def test_the_clause_no_longer_carries_a_cast_that_would_reintroduce_it():
    """The fix is the bind, but the cast is what caused it — leaving it in place
    would invite the string back the next time someone edits this."""
    assert "timestamptz" not in sql(due_before="2026-08-31")


# ── saved views ────────────────────────────────────────────────────────────

def test_a_view_can_only_pin_filters_the_list_endpoint_accepts():
    """The set is derived from `build_task_filters`'s own signature, so a
    filter added there without being allowed here fails loudly rather than
    being unsavable in a view for a release."""
    import inspect

    accepted = set(inspect.signature(build_task_filters).parameters)
    assert accepted >= VIEW_FILTER_KEYS
    # `parent_task_id` is navigation, not a saved preference.
    assert "parent_task_id" not in VIEW_FILTER_KEYS


def test_an_unknown_config_key_is_dropped_rather_than_refused():
    """A view is a saved preference written by an older client. Refusing to
    open one because it carries a key this version has never heard of turns
    every deploy into a migration of everybody's saved views."""
    got = normalise_view_config({
        "filters": {"overdue": True, "colour": "purple"},
        "group_by": "assignee",
    })
    assert got == {"filters": {"overdue": True}, "group_by": "assignee"}


def test_an_unknown_group_by_falls_back_to_the_boards_own_default():
    """Rendering must produce something; `status` is the board's axis, not a
    guess."""
    assert normalise_view_config({"group_by": "phase"})["group_by"] == "status"


def test_a_config_that_is_not_an_object_still_yields_a_usable_view():
    for junk in (None, [], "board", 7):
        assert normalise_view_config(junk) == {"filters": {}, "group_by": "status"}


def test_filters_that_are_not_an_object_are_ignored():
    assert normalise_view_config({"filters": "overdue"})["filters"] == {}


@pytest.mark.parametrize("group_by", GROUP_BY)
def test_every_advertised_grouping_survives_normalisation(group_by):
    assert normalise_view_config({"group_by": group_by})["group_by"] == group_by


def test_lane_state_survives_normalisation():
    """WS-27y — the sub-axis and its lane state ride the view config. The
    server must not strip them, or every save round-trip silently flattens
    the board back to a lane-less one."""
    got = normalise_view_config({
        "group_by": "status",
        "sub_group_by": "assignee",
        "collapsed_lanes": ["a@x.io", 7, "b@x.io"],
        "show_empty_lanes": True,
    })
    assert got["sub_group_by"] == "assignee"
    assert got["collapsed_lanes"] == ["a@x.io", "b@x.io"]
    assert got["show_empty_lanes"] is True


def test_a_sub_axis_equal_to_the_main_axis_is_dropped_with_its_lane_state():
    """Mirrors grouping.ts fromConfig: laning a board by its own columns is
    nonsense a hand-edited config could still say."""
    got = normalise_view_config({
        "group_by": "status",
        "sub_group_by": "status",
        "collapsed_lanes": ["todo"],
        "show_empty_lanes": True,
    })
    assert "sub_group_by" not in got
    assert "collapsed_lanes" not in got
    assert "show_empty_lanes" not in got


def test_a_lane_less_view_stores_no_lane_keys_at_all():
    """A view saved before lanes existed and one saved after with no lanes
    must stay byte-identical, so nothing bumps updated_at on a no-op save."""
    assert normalise_view_config({"group_by": "status"}) == {
        "filters": {}, "group_by": "status",
    }


def test_shown_fields_survive_normalisation_with_junk_dropped():
    """WS-27x — the shown-fields set rides the view config. Unknown keys and
    non-strings are hand-edits (or a newer client's vocabulary) and are
    dropped; the rest must come back intact or every save round-trip would
    strip somebody's column choices."""
    got = normalise_view_config({
        "group_by": "status",
        "shown_fields": ["status", "phase", 7, None, "due_at", "assignees"],
    })
    assert got["shown_fields"] == ["status", "due_at", "assignees"]


def test_every_advertised_shown_field_survives_normalisation():
    from gateway.routes.projects.filters import SHOWN_FIELDS

    got = normalise_view_config({"shown_fields": list(SHOWN_FIELDS)})
    assert got["shown_fields"] == list(SHOWN_FIELDS)


def test_custom_field_keys_pass_by_shape_and_a_bare_prefix_does_not():
    """`custom.<key>` names a project field the pure normaliser cannot look
    up, so it is checked by shape — and `custom.` alone names nothing."""
    got = normalise_view_config({
        "shown_fields": ["custom.budget", "custom.", "customer"],
    })
    assert got["shown_fields"] == ["custom.budget"]


def test_a_duplicate_shown_field_is_kept_once():
    got = normalise_view_config({"shown_fields": ["status", "status", "tags"]})
    assert got["shown_fields"] == ["status", "tags"]


def test_an_absent_or_junk_shown_fields_stays_absent():
    """Absent means "the client's default set". Writing a default HERE would
    freeze today's default into every stored view, so the key is simply not
    emitted — mirroring how a lane-less view stores no lane keys."""
    assert "shown_fields" not in normalise_view_config({"group_by": "status"})
    for junk in ("status,tags", {"status": True}, 7, None, True):
        assert "shown_fields" not in normalise_view_config({"shown_fields": junk})


def test_an_explicitly_empty_shown_fields_list_is_kept():
    """Hiding every column is a choice, not the default — collapsing `[]`
    into "absent" would un-hide a deliberate choice on the next apply."""
    assert normalise_view_config({"shown_fields": []})["shown_fields"] == []


def test_a_saved_view_and_the_same_filters_typed_by_hand_are_one_query():
    """The whole reason the builder is shared. If these ever diverge, a saved
    view shows a different set of tasks than the filters it claims to hold."""
    config = normalise_view_config({
        "filters": {"status_category": "todo", "overdue": True,
                    "assignee": "priya@fracktal.in"},
    })
    from_view = build_task_filters(**config["filters"])
    typed = build_task_filters(
        status_category="todo", overdue=True, assignee="priya@fracktal.in",
    )
    assert from_view == typed


class TestWatchingFilter:
    """WS-27bk 9.12.2 - "what am I watching", as a filter.

    A FILTER and not a fourth lens. One task store, three lenses
    (D52/D53/D54), and a watched-tasks view would fork that. As a filter it
    composes: "things I watch, in Ops, that are overdue" is one query.
    """

    def test_absent_by_default(self):
        clauses, params = build_task_filters()
        assert not any("pm_task_watchers" in c for c in clauses)
        assert "viewer" not in params

    def test_is_a_where_clause_on_the_watchers_table(self):
        clauses, params = build_task_filters(watching=True, viewer="Priya@Example.com")
        watch = [c for c in clauses if "pm_task_watchers" in c]
        assert len(watch) == 1
        assert "w.task_id = t.id" in watch[0]
        assert params["viewer"] == "priya@example.com"

    def test_folds_the_address_because_the_stored_row_is_folded(self):
        # `watchers.watchable` lowercases before it inserts, so an unfolded
        # parameter matches nothing and the board silently reads empty.
        _, params = build_task_filters(watching=True, viewer="  MIXED@Case.COM ")
        assert params["viewer"] == "mixed@case.com"

    def test_watching_without_a_viewer_is_REFUSED_not_ignored(self):
        # Asked to narrow to a person and given none. Dropping the clause would
        # silently return the WHOLE board as though it were one person's
        # subscriptions, which is the dangerous direction.
        for blank in ("", "   ", None):
            with pytest.raises(HTTPException) as caught:
                build_task_filters(watching=True, viewer=blank)
            assert caught.value.status_code == 422

    def test_a_viewer_without_watching_filters_nothing(self):
        # The identity alone is not a request to narrow. Every endpoint has a
        # caller, so treating one as an implicit filter would silently hide
        # every task nobody watches.
        clauses, params = build_task_filters(viewer="a@b.com")
        assert not any("pm_task_watchers" in c for c in clauses)
        assert "viewer" not in params

    def test_is_ADDITIVE_and_never_widens(self):
        # A watch is an intent to hear, never a right to see (WS-27v). The
        # clause may only narrow: it is an EXISTS, never an OR, and it carries
        # no visibility of its own. The endpoint's own visibility clause is
        # what keeps a watcher who lost the grant from reading the row.
        clauses, _ = build_task_filters(watching=True, viewer="a@b.com")
        watch = next(c for c in clauses if "pm_task_watchers" in c)
        assert watch.strip().startswith("EXISTS")
        assert " OR " not in watch.upper()

    def test_composes_with_the_other_filters(self):
        clauses, params = build_task_filters(
            watching=True, viewer="a@b.com", overdue=True, status_category="in_progress",
        )
        assert any("pm_task_watchers" in c for c in clauses)
        assert any("pm_task_statuses" in c for c in clauses)
        assert params["viewer"] == "a@b.com"

    def test_a_view_may_pin_watching_but_never_an_address(self):
        # The view stores the INTENT and the endpoint resolves the person.
        # Storing an address would pin one member's subscriptions into a view
        # other people open, and show them a colleague's list as their own.
        assert "watching" in VIEW_FILTER_KEYS
        assert "viewer" not in VIEW_FILTER_KEYS

    def test_a_saved_view_keeps_watching_through_a_round_trip(self):
        out = normalise_view_config({"filters": {"watching": True}})
        assert out["filters"] == {"watching": True}

    def test_the_endpoint_never_takes_an_address_from_the_caller(self):
        # Reading `watching` as a string would turn this filter into a way of
        # asking what a COLLEAGUE follows. The query parameter is a bool and
        # the identity is resolved server-side from the session.
        source = Path(
            REPO / "apps/services/gateway/gateway/routes/projects/tasks.py"
        ).read_text(encoding="utf-8")
        assert "watching: bool = False" in source
        assert "viewer=actor(user) if watching else None" in source

    def test_export_carries_the_same_filter(self):
        # The export promises `list_tasks`' parameters verbatim. A filter the
        # board applies and the export ignores hands somebody a file of the
        # wrong rows, and nothing on the way says so.
        source = Path(
            REPO / "apps/services/gateway/gateway/routes/projects/export.py"
        ).read_text(encoding="utf-8")
        assert "watching: bool = False" in source
        assert "viewer=actor(user) if watching else None" in source
