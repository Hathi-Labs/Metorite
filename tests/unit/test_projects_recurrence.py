"""WS-27o — recurring tasks.

Spec: `project-docs/specs/project_management_app.md` §11.2 item 7, §11.13.

Recurrence looks trivial and is not. Each of these is a different way to be
quietly wrong for a year:

* **January 31st, monthly.** Clamping to February and *storing* the clamped day
  permanently demotes the rule to the 28th. It has to clamp at computation time.
* **February 29th, yearly.** Same shape, once every four years.
* **A weekly rule crossing Sunday.** "Mon and Thu, every 2 weeks" must land on
  both days of the right weeks, not alternate between them.
* **A task closed six weeks late.** An anchor of `due` that does not catch up
  produces a successor already overdue the moment it appears, which teaches
  people the date means nothing.
* **A task closed twice.** Reopening and re-closing must not spawn a second
  successor.

Pure functions, tested directly. No Postgres, no fake.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import HTTPException
from gateway.routes.projects.recurrence import (
    ANCHORS,
    CARRIED_FIELDS,
    FREQS,
    MAX_CATCHUP,
    next_occurrence,
    series_exhausted,
    validate_rule,
)

REPO = Path(__file__).resolve().parents[2]
MIGRATION = REPO / "infra/postgres/160_projects_recurrence.sql"


def sql_without_comments() -> str:
    text = MIGRATION.read_text(encoding="utf-8")
    return "\n".join(re.sub(r"--.*$", "", line) for line in text.splitlines())


def at(spec: str) -> datetime:
    return datetime.fromisoformat(spec).replace(tzinfo=UTC)


def rule(**over) -> dict:
    return {"freq": "daily", "interval": 1, "anchor": "due", "weekdays": [], **over}


def nxt(r: dict, *, due=None, completed=None, now="2026-01-01T00:00:00") -> datetime | None:
    return next_occurrence(
        r, due_at=due, completed_at=completed, now=at(now),
    )


# ── The vocabulary matches the schema ───────────────────────────────────────

def test_the_frequencies_are_the_ones_the_database_allows():
    match = re.search(r"freq\s+TEXT\s+NOT\s+NULL\s*CHECK\s*\(\s*freq\s+IN\s*\((.*?)\)\)",
                      sql_without_comments(), re.S | re.I)
    assert match, "160 no longer constrains pm_recurrences.freq"
    assert set(re.findall(r"'(\w+)'", match.group(1))) == set(FREQS)


def test_the_anchors_are_the_ones_the_database_allows():
    match = re.search(r"anchor\s+TEXT[^,]*?CHECK\s*\(\s*anchor\s+IN\s*\((.*?)\)\)",
                      sql_without_comments(), re.S | re.I)
    assert match, "160 no longer constrains pm_recurrences.anchor"
    assert set(re.findall(r"'(\w+)'", match.group(1))) == set(ANCHORS)


def test_the_database_refuses_a_rule_that_cannot_pick_a_date():
    """A weekly rule with no weekdays, or a monthly one with no day, is a series
    that silently stops. Refused on both sides."""
    sql = sql_without_comments()
    assert "pm_recurrences_weekly_needs_days" in sql
    assert "pm_recurrences_monthly_needs_a_day" in sql


def test_the_weekly_check_survives_an_EMPTY_array_not_just_a_missing_one():
    """A bug the live run caught and reading could not.

    `array_length('{}', 1)` returns **NULL**, not 0. `NULL >= 1` is NULL, and a
    CHECK constraint only fails on FALSE — so the un-coalesced form evaluated to
    NULL for exactly the row it existed to reject, and a weekly rule with no
    weekdays inserted happily past a constraint that looked correct.

    Asserted against the SQL because the claim is about the expression, and the
    hermetic suite has no database to try it on.
    """
    match = re.search(
        r"CHECK \(freq <> 'weekly' OR ([^)]+\)[^)]*)\)", sql_without_comments(),
    )
    assert match, "160 no longer guards a weekly rule's weekdays"
    assert "coalesce(" in match.group(1), (
        "array_length of an empty array is NULL, so this CHECK passes the very "
        "row it exists to reject unless the NULL is coalesced"
    )


def test_the_idempotency_stamp_exists_in_the_schema():
    """The whole reason one weekly report does not become three."""
    assert re.search(
        r"ADD COLUMN IF NOT EXISTS recurrence_spawned_at\s+TIMESTAMPTZ",
        sql_without_comments(), re.I,
    )


def test_no_scheduler_table_was_added():
    """§5: `/workflows` is the only engine. A queue or a due-runs table here
    would be the second one this spec forbids."""
    sql = sql_without_comments().lower()
    for forbidden in ("pm_recurrence_queue", "pm_scheduled", "next_run_at"):
        assert forbidden not in sql, f"160 grew a scheduler ({forbidden})"


# ── Validation ──────────────────────────────────────────────────────────────

def test_an_unknown_frequency_is_refused_and_lists_the_real_ones():
    with pytest.raises(HTTPException) as exc:
        validate_rule({"freq": "fortnightly"})
    assert exc.value.status_code == 422
    for known in FREQS:
        assert known in str(exc.value.detail)


def test_a_weekly_rule_without_weekdays_is_refused():
    with pytest.raises(HTTPException) as exc:
        validate_rule({"freq": "weekly"})
    assert "weekday" in str(exc.value.detail)


def test_a_monthly_rule_without_a_day_is_refused():
    with pytest.raises(HTTPException) as exc:
        validate_rule({"freq": "monthly"})
    assert "day of the month" in str(exc.value.detail)


def test_an_unknown_anchor_is_refused_and_explains_the_difference():
    """The two anchors mean genuinely different things, so the error says so
    rather than only listing them."""
    with pytest.raises(HTTPException) as exc:
        validate_rule({"freq": "daily", "anchor": "whenever"})
    detail = str(exc.value.detail)
    assert "schedule" in detail and "finished" in detail


def test_an_out_of_range_interval_is_refused():
    for bad in (0, -1, 10_000):
        with pytest.raises(HTTPException):
            validate_rule({"freq": "daily", "interval": bad})


def test_an_impossible_weekday_is_refused():
    with pytest.raises(HTTPException):
        validate_rule({"freq": "weekly", "weekdays": [0]})
    with pytest.raises(HTTPException):
        validate_rule({"freq": "weekly", "weekdays": [8]})


def test_weekdays_are_deduplicated_and_ordered():
    assert validate_rule({"freq": "weekly", "weekdays": [4, 1, 4]})["weekdays"] == [1, 4]


def test_the_default_anchor_is_the_schedule():
    """`due` keeps a cadence on its dates. Defaulting to `completed` would make
    every series drift the first time somebody was a day late."""
    assert validate_rule({"freq": "daily"})["anchor"] == "due"


# ── Daily ───────────────────────────────────────────────────────────────────

def test_daily_advances_by_a_day():
    assert nxt(rule(), due="2026-01-05T09:00:00", now="2026-01-05T10:00:00") == at(
        "2026-01-06T09:00:00"
    )


def test_every_n_days_advances_by_n():
    assert nxt(
        rule(interval=3), due="2026-01-05T09:00:00", now="2026-01-05T10:00:00"
    ) == at("2026-01-08T09:00:00")


def test_the_time_of_day_is_kept():
    """A stand-up at 09:00 must not become a stand-up at midnight."""
    assert nxt(
        rule(), due="2026-01-05T09:30:00", now="2026-01-05T10:00:00"
    ).time() == at("2026-01-05T09:30:00").time()


# ── Monthly — the January 31st case ─────────────────────────────────────────

def test_the_31st_becomes_the_28th_in_february_but_stays_the_31st_after():
    """The single most important case here. Clamping is what February needs;
    clamping *permanently* is the bug — a rule stored as the 28th can never
    return to the 31st, so a monthly report quietly moves three days earlier
    forever after its first February."""
    r = rule(freq="monthly", day_of_month=31)
    feb = nxt(r, due="2026-01-31T09:00:00", now="2026-01-31T10:00:00")
    assert feb == at("2026-02-28T09:00:00")

    # And the NEXT step, computed from the rule rather than from the clamp,
    # goes back to the 31st.
    march = nxt(r, due=feb, now="2026-02-28T10:00:00")
    assert march == at("2026-03-31T09:00:00")


def test_the_31st_lands_on_the_30th_in_a_thirty_day_month():
    r = rule(freq="monthly", day_of_month=31)
    assert nxt(r, due="2026-03-31T09:00:00", now="2026-03-31T10:00:00") == at(
        "2026-04-30T09:00:00"
    )


def test_a_leap_february_gets_the_29th():
    r = rule(freq="monthly", day_of_month=31)
    assert nxt(r, due="2028-01-31T09:00:00", now="2028-01-31T10:00:00") == at(
        "2028-02-29T09:00:00"
    )


def test_monthly_crosses_the_year_boundary():
    r = rule(freq="monthly", day_of_month=15)
    assert nxt(r, due="2026-12-15T09:00:00", now="2026-12-15T10:00:00") == at(
        "2027-01-15T09:00:00"
    )


def test_every_other_month():
    r = rule(freq="monthly", interval=2, day_of_month=1)
    assert nxt(r, due="2026-01-01T09:00:00", now="2026-01-01T10:00:00") == at(
        "2026-03-01T09:00:00"
    )


# ── Yearly — February 29th ──────────────────────────────────────────────────

def test_a_february_29th_rule_clamps_in_a_common_year_and_returns_in_a_leap_one():
    """Once every four years, and permanently wrong if the clamp is stored."""
    r = rule(freq="yearly", day_of_month=29, month_of_year=2)
    common = nxt(r, due="2028-02-29T09:00:00", now="2028-03-01T00:00:00")
    assert common == at("2029-02-28T09:00:00")

    r_leap = rule(freq="yearly", interval=4, day_of_month=29, month_of_year=2)
    assert nxt(r_leap, due="2028-02-29T09:00:00", now="2028-03-01T00:00:00") == at(
        "2032-02-29T09:00:00"
    )


def test_a_yearly_rule_moves_the_date_to_ITS_month_not_the_one_it_came_from():
    """The fixtures above all have a due date already in the rule's month, so
    reading `when.month` instead of the rule would give the same answer. Here
    the two differ: a rule that says April, from a task due in November."""
    r = rule(freq="yearly", day_of_month=6, month_of_year=4)
    assert nxt(r, due="2026-11-20T09:00:00", now="2026-11-20T10:00:00") == at(
        "2027-04-06T09:00:00"
    )


# ── Weekly ──────────────────────────────────────────────────────────────────

def test_weekly_takes_the_next_allowed_day_in_the_same_week():
    # 2026-01-05 is a Monday. Mon(1) and Thu(4).
    r = rule(freq="weekly", weekdays=[1, 4])
    assert nxt(r, due="2026-01-05T09:00:00", now="2026-01-05T10:00:00") == at(
        "2026-01-08T09:00:00"
    )


def test_weekly_wraps_to_the_next_week_when_the_days_run_out():
    r = rule(freq="weekly", weekdays=[1, 4])
    # From Thursday the next allowed day is the following Monday.
    assert nxt(r, due="2026-01-08T09:00:00", now="2026-01-08T10:00:00") == at(
        "2026-01-12T09:00:00"
    )


def test_every_other_week_lands_on_BOTH_days_of_the_right_weeks():
    """The case a naive "+14 days" gets wrong: it would alternate between Monday
    and Thursday instead of giving both days of every second week."""
    r = rule(freq="weekly", interval=2, weekdays=[1, 4])
    # Mon 5th → Thu 8th, same week: the interval does not apply within a week.
    assert nxt(r, due="2026-01-05T09:00:00", now="2026-01-05T10:00:00") == at(
        "2026-01-08T09:00:00"
    )
    # Thu 8th → skips a week → Mon 19th, not Mon 12th.
    assert nxt(r, due="2026-01-08T09:00:00", now="2026-01-08T10:00:00") == at(
        "2026-01-19T09:00:00"
    )


def test_a_sunday_only_rule_advances_by_a_week():
    r = rule(freq="weekly", weekdays=[7])
    # 2026-01-04 is a Sunday.
    assert nxt(r, due="2026-01-04T09:00:00", now="2026-01-04T10:00:00") == at(
        "2026-01-11T09:00:00"
    )


# ── Anchors ─────────────────────────────────────────────────────────────────

def test_an_anchor_of_due_keeps_the_schedule_when_the_task_was_closed_late():
    """"Stock count on the 1st" stays on the 1st. Measuring from completion
    would drag the whole series later every time somebody was busy."""
    r = rule(freq="monthly", day_of_month=1, anchor="due")
    assert nxt(
        r, due="2026-02-01T09:00:00", completed="2026-02-09T17:00:00",
        now="2026-02-09T17:00:00",
    ) == at("2026-03-01T09:00:00")


def test_an_anchor_of_completed_measures_from_when_it_was_actually_done():
    """"Water the plants every 3 days" restarts when you water them."""
    r = rule(freq="daily", interval=3, anchor="completed")
    assert nxt(
        r, due="2026-02-01T09:00:00", completed="2026-02-09T17:00:00",
        now="2026-02-09T17:00:00",
    ) == at("2026-02-12T17:00:00")


def test_a_due_anchored_rule_catches_up_rather_than_landing_in_the_past():
    """A monthly task closed six weeks late would otherwise produce a successor
    already overdue the moment it appears — which teaches people the date is
    meaningless."""
    r = rule(freq="monthly", day_of_month=1, anchor="due")
    got = nxt(r, due="2026-01-01T09:00:00", now="2026-04-15T00:00:00")
    assert got == at("2026-05-01T09:00:00")


def test_catching_up_SKIPS_the_missed_ones_rather_than_backfilling():
    """Nobody wants four copies of a stand-up they did not attend. The proof is
    that one call returns one date, and it is the next FUTURE one."""
    r = rule(freq="daily", anchor="due")
    got = nxt(r, due="2026-01-01T09:00:00", now="2026-01-10T12:00:00")
    assert got == at("2026-01-11T09:00:00")


def _daily_at(now: str, day_start: str) -> datetime | None:
    return next_occurrence(rule(freq="daily", anchor="due"), due_at="2026-01-09T06:30:00+00:00",
                           completed_at=None, now=at(now), day_start=at(day_start))


def test_a_task_closed_after_noon_keeps_the_occurrence_of_that_day():
    """RX-5, the day rule. An occurrence due at noon today is still due today at
    15:00, so the series does not skip it. Mutation: read `now` in `passed`."""
    assert _daily_at("2026-01-10T09:30:00", "2026-01-09T18:30:00") == at("2026-01-10T06:30:00")


def test_an_occurrence_of_yesterday_still_moves_on():
    """The day rule keeps today and nothing before it."""
    assert _daily_at("2026-01-11T09:30:00", "2026-01-10T18:30:00") == at("2026-01-11T06:30:00")


def test_the_edge_of_the_day_is_today():
    assert _daily_at("2026-01-10T07:00:00", "2026-01-10T06:30:00") == at("2026-01-10T06:30:00")


def test_a_completed_anchor_ignores_the_day_rule():
    got = next_occurrence(rule(freq="daily", anchor="completed"), due_at=None,
                          completed_at=at("2026-01-10T09:30:00"), now=at("2026-01-10T09:30:00"),
                          day_start=at("2026-01-09T18:30:00"))
    assert got == at("2026-01-11T09:30:00")


def test_a_series_further_behind_than_the_catch_up_cap_is_dead_not_late():
    r = rule(freq="daily", anchor="due")
    assert nxt(r, due="1990-01-01T09:00:00", now="2026-01-01T00:00:00") is None


def test_the_catch_up_cap_is_generous_enough_for_a_real_lapse():
    """Bounded because an unbounded loop over data somebody can create is a
    denial of service — but a daily task must survive years of neglect."""
    assert MAX_CATCHUP >= 365 * 3


def test_a_completed_anchor_takes_exactly_ONE_step_even_from_an_old_completion():
    """"Every 3 days after you did it" means three days after you did it.

    The fixture deliberately puts the completion months behind `now`, because
    with a completion of "just now" a catch-up loop and no catch-up loop give
    the same answer, and the assertion would prove nothing. Here they differ:
    catching up would say June, and the honest answer is January."""
    r = rule(freq="daily", interval=3, anchor="completed")
    assert nxt(
        r, due="2020-01-01T09:00:00", completed="2026-01-01T08:00:00",
        now="2026-06-01T00:00:00",
    ) == at("2026-01-04T08:00:00")


def test_a_task_with_no_due_date_falls_back_to_when_it_was_completed():
    r = rule(freq="daily", anchor="due")
    assert nxt(r, completed="2026-01-05T09:00:00", now="2026-01-05T10:00:00") == at(
        "2026-01-06T09:00:00"
    )


# ── Ending a series ─────────────────────────────────────────────────────────

def test_a_series_stops_at_its_until_date():
    r = rule(freq="daily", until_at="2026-01-05T00:00:00")
    assert nxt(r, due="2026-01-04T09:00:00", now="2026-01-04T10:00:00") is None


def test_a_series_stops_after_its_occurrence_cap():
    r = rule(freq="daily", max_occurrences=3, occurrences_made=3)
    assert nxt(r, due="2026-01-04T09:00:00", now="2026-01-04T10:00:00") is None


def test_a_series_below_its_cap_keeps_going():
    r = rule(freq="daily", max_occurrences=3, occurrences_made=2)
    assert nxt(r, due="2026-01-04T09:00:00", now="2026-01-04T10:00:00") is not None


def test_whichever_limit_ends_it_first_wins():
    """A rule with `max_occurrences: 6` and an `until_at` next year stops at
    six, and one with two left but a date yesterday stops on the date."""
    soon = rule(freq="daily", max_occurrences=6, occurrences_made=6,
                until_at="2099-01-01T00:00:00")
    assert series_exhausted(soon, at("2026-06-01T00:00:00")) is True

    dated = rule(freq="daily", max_occurrences=6, occurrences_made=1,
                 until_at="2026-01-01T00:00:00")
    assert series_exhausted(dated, at("2026-06-01T00:00:00")) is True


def test_no_limits_means_the_series_runs_until_somebody_stops_it():
    """Which is what an operations cadence actually is."""
    assert series_exhausted(rule(), at("2099-01-01T00:00:00")) is False


# ── What carries to the successor ───────────────────────────────────────────

def test_the_successor_does_NOT_inherit_the_finished_state():
    """"This month's report" has not been started, and a successor that arrives
    already `done` is a series that only ever runs once."""
    for absent in ("status_id", "completed_at", "task_number"):
        assert absent not in CARRIED_FIELDS


def test_the_successor_does_not_inherit_last_month_s_conversation():
    """Comments and attachments belong to the occurrence they were made on.
    Copying them is how a recurring task becomes unreadable by March."""
    for absent in ("recurrence_spawned_at", "clickup_id"):
        assert absent not in CARRIED_FIELDS


def test_the_successor_DOES_inherit_what_makes_it_the_same_work():
    for carried in ("title", "description", "importance", "tags", "custom_fields"):
        assert carried in CARRIED_FIELDS


def test_it_stays_in_the_same_project():
    """A series that wandered into another project would escape the grant that
    scoped it."""
    assert "project_id" in CARRIED_FIELDS
    assert "root_project_id" in CARRIED_FIELDS
