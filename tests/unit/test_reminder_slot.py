"""WS-66 RM-2 — the ladder's times and the smart slot, as pure code.

Spec: ``project-docs/specs/calendar_events_reminders.md`` §5.4 (the
defaults) and §5.5 (the smart slot) · D114.

``gateway/reminders/slot.py`` reads no database, no model and no clock, so
this file holds each rule with fixed instants:

* **The grace** of §5.4: the estimate plus 30 minutes, kept between 30
  minutes and 3 hours, and 60 minutes with no estimate.
* **Quiet hours** are the Reach rule (``reach.plan.in_quiet_hours``) with
  every day a working day: outside the member's day hours, in the member's
  zone. A step that would land there waits for the next day start.
* **The smart slot**, in the order of §5.5: a named time, the same time of
  day on a later day, then the next free gap that is long enough. It is
  always after now, never quiet and never inside a busy block, also over a
  sweep of many calendars.

**The mutations (R7).** Each turned the named test red. Commit before each.

* M1. ``slot.quiet``: answer False.
  ``test_quiet_hours_are_the_reach_rule_on_every_day``,
  ``test_a_check_time_in_quiet_hours_waits_for_the_day_start`` and
  ``test_the_slot_is_never_quiet_and_never_busy``.
* M2. ``slot.inside``: answer False.
  ``test_a_busy_day_moves_option_2_to_the_next_day`` and
  ``test_the_slot_is_never_quiet_and_never_busy``.
* M3. ``slot.grace``: drop the clamp.
  ``test_the_grace_of_section_5_4``.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from gateway.reach.plan import in_quiet_hours
from gateway.reminders import slot
from gateway.routes.shell.state import Day, settings_day

IST = ZoneInfo("Asia/Kolkata")
BERLIN = ZoneInfo("Europe/Berlin")
#: The member's day: 08:00 to 20:00 in Kolkata.
DAY = Day(IST, 8, 20)


def _ist(day: int, hour: int, minute: int = 0) -> datetime:
    """An instant in Kolkata, in March 2099 (Tuesday 10 is day 10)."""
    return datetime(2099, 3, day, hour, minute, tzinfo=IST)


def _reminder(fire_at: datetime, estimate: int | None = None) -> SimpleNamespace:
    return SimpleNamespace(fire_at=fire_at, estimate_minutes=estimate)


# ── The grace (§5.4) ────────────────────────────────────────────────────────


@pytest.mark.parametrize(("estimate", "minutes"), [
    (None, 60), (0, 60), (-5, 60),
    (1, 31), (10, 40), (30, 60), (120, 150),
    (150, 180), (151, 180), (10080, 180),
])
def test_the_grace_of_section_5_4(estimate, minutes):
    assert slot.grace(estimate) == timedelta(minutes=minutes)


def test_the_grace_never_leaves_its_bounds():
    for estimate in range(1, 400):
        assert slot.GRACE_MIN <= slot.grace(estimate) <= slot.GRACE_MAX


# ── Quiet hours: the Reach rule (decision of 2026-10-25) ────────────────────


def test_quiet_hours_are_the_reach_rule_on_every_day():
    # Every day of a week, so a weekend has no rule of its own (RM-2c).
    for day in range(9, 16):
        for minute in range(0, 24 * 60, 15):
            at = _ist(day, 0) + timedelta(minutes=minute)
            assert slot.quiet(DAY, at) is in_quiet_hours(DAY, at, True), at
    assert slot.quiet(DAY, _ist(10, 7, 59)) is True
    assert slot.quiet(DAY, _ist(10, 8, 0)) is False
    assert slot.quiet(DAY, _ist(10, 19, 59)) is False
    assert slot.quiet(DAY, _ist(10, 20, 0)) is True
    # Saturday and Sunday are awake days too.
    assert slot.quiet(DAY, _ist(14, 12)) is False
    assert slot.quiet(DAY, _ist(15, 12)) is False


def test_the_day_reads_the_settings_row_as_the_feed_does():
    row = SimpleNamespace(timezone="Asia/Kolkata", day_start_hour=8, day_end_hour=20)
    assert settings_day(row) == Day(IST, 8, 20)
    # A bad zone reads as UTC and an unset hour as the column default.
    loose = settings_day(SimpleNamespace(timezone="Mars/Base", day_start_hour=None,
                                         day_end_hour=99))
    assert loose == Day(ZoneInfo("UTC"), 7, 24)


@pytest.mark.parametrize(("at", "start"), [
    (_ist(10, 16), _ist(11, 8)),     # after the check-back: tomorrow
    (_ist(10, 3), _ist(10, 8)),      # before the day: today
    (_ist(10, 8), _ist(11, 8)),      # strictly after a day start
    (_ist(10, 23, 30), _ist(11, 8)),
])
def test_the_next_day_start(at, start):
    assert slot.next_day_start(DAY, at) == start


def test_a_check_time_in_quiet_hours_waits_for_the_day_start():
    # A reminder at 19:30 with an estimate of 60: the grace of 90 minutes
    # reaches 21:00, which is quiet. The check-back waits for 08:00.
    fire = _ist(10, 19, 30)
    assert slot.awake(DAY, fire + slot.grace(60)) == _ist(11, 8)
    # Inside the day, the time stands.
    assert slot.awake(DAY, _ist(10, 15)) == _ist(10, 15)


# ── The smart slot (§5.5) ───────────────────────────────────────────────────


def test_option_1_a_named_time_wins_when_it_fits():
    member = slot.MemberDay(DAY)
    now = _ist(10, 9)
    named = _ist(12, 17, 45)
    assert slot.next_slot(member, _reminder(_ist(10, 15)), now, named=named) == named
    # A named time in quiet hours, in the past or inside a block falls to 2.
    busy = slot.MemberDay(DAY, ((_ist(12, 17), _ist(18, 0)),))
    for bad, who in ((_ist(12, 22), member), (_ist(9, 10), member), (named, busy)):
        assert slot.next_slot(who, _reminder(_ist(10, 15)), now, named=bad) == _ist(11, 15)


def test_option_2_the_same_time_on_the_next_day():
    # Fired on Tuesday at 15:00, and moved at the brief on Wednesday 08:00.
    member = slot.MemberDay(DAY)
    got = slot.next_slot(member, _reminder(_ist(10, 15)), _ist(11, 8))
    assert got == _ist(11, 15)
    # "Not yet" at 15:30 on the day it fired: the same time tomorrow.
    assert slot.next_slot(member, _reminder(_ist(10, 15)), _ist(10, 15, 30)) == _ist(11, 15)


def test_a_busy_day_moves_option_2_to_the_next_day():
    busy = ((_ist(11, 14, 30), _ist(11, 16)),)
    member = slot.MemberDay(DAY, busy)
    assert slot.next_slot(member, _reminder(_ist(10, 15)), _ist(11, 8)) == _ist(12, 15)


def test_option_2_keeps_the_clock_across_a_change_of_summer_time():
    # Berlin moves its clock on Sunday 29 March 2099. 15:00 stays 15:00.
    day = Day(BERLIN, 8, 20)
    fire = datetime(2099, 3, 28, 15, 0, tzinfo=BERLIN)
    got = slot.next_slot(slot.MemberDay(day), _reminder(fire), fire + timedelta(hours=1))
    assert got.astimezone(BERLIN).hour == 15 and got.astimezone(BERLIN).day == 29
    # Python subtracts two times of one zone as wall clocks, so compare in UTC.
    assert got.astimezone(UTC) - fire.astimezone(UTC) == timedelta(hours=23)


def test_option_3_a_quiet_time_of_day_takes_the_next_free_gap():
    # The member set 23:00, which D114.3 lets fire. A move never lands in
    # quiet hours, so the slot is the first free gap that fits 45 minutes.
    busy = ((_ist(11, 8), _ist(11, 9, 30)), (_ist(11, 9, 45), _ist(11, 12)))
    member = slot.MemberDay(DAY, busy)
    got = slot.next_slot(member, _reminder(_ist(10, 23), estimate=45), _ist(11, 7, 30))
    # 09:30 to 09:45 is too short for 45 minutes. 12:00 is the first fit.
    assert got == _ist(11, 12)


def test_option_3_starts_at_the_next_quarter_after_now():
    member = slot.MemberDay(DAY)
    got = slot.next_slot(member, _reminder(_ist(10, 23)), _ist(11, 10, 7))
    assert got == _ist(11, 10, 15)


def test_with_no_gap_in_14_days_the_slot_is_the_day_start_after_them():
    now = _ist(11, 9)
    free = slot.next_slot(slot.MemberDay(DAY, ((now, now + timedelta(days=14)),)),
                          _reminder(_ist(10, 15)), now)
    assert free == _ist(25, 9)  # the block ends at 09:00 on the 25th
    # A block past the 14 days too: the last resort moves past its end.
    wall = ((now - timedelta(days=1), now + timedelta(days=20)),)
    got = slot.next_slot(slot.MemberDay(DAY, wall), _reminder(_ist(10, 15)), now)
    assert got == _ist(31, 9)
    assert not slot.quiet(DAY, got) and not slot.inside(got, wall)


def test_the_slot_is_never_quiet_and_never_busy():
    """A sweep of 400 random calendars: the rule of §5.5 holds for each."""
    rng = random.Random(20991025)
    for _ in range(400):
        start, end = sorted(rng.sample(range(0, 25), 2))
        day = Day(rng.choice((IST, BERLIN, ZoneInfo("UTC"))), start, max(end, start + 1))
        now = datetime(2099, 3, 10, tzinfo=UTC) + timedelta(minutes=rng.randrange(0, 7 * 1440))
        busy = []
        for _block in range(rng.randrange(0, 30)):
            begin = now + timedelta(minutes=rng.randrange(-600, 10 * 1440))
            busy.append((begin, begin + timedelta(minutes=rng.randrange(15, 600))))
        member = slot.MemberDay(day, tuple(sorted(busy)))
        fire = now - timedelta(minutes=rng.randrange(0, 3 * 1440))
        got = slot.next_slot(member, _reminder(fire, rng.choice((None, 15, 90, 240))), now)
        assert got > now
        assert not slot.quiet(day, got), (day, now, got)
        assert not slot.inside(got, member.busy), (now, got)


def test_busy_blocks_reads_only_rows_with_a_block():
    rows = [
        SimpleNamespace(scheduled_start=_ist(10, 9), scheduled_end=_ist(10, 10)),
        SimpleNamespace(scheduled_start=_ist(10, 11), scheduled_end=None),
        SimpleNamespace(scheduled_start=_ist(10, 12), scheduled_end=_ist(10, 12)),
        SimpleNamespace(scheduled_start=datetime(2099, 3, 10, 1), scheduled_end=datetime(
            2099, 3, 10, 2)),
    ]
    got = slot.busy_blocks(rows)
    assert got == (
        (datetime(2099, 3, 10, 1, tzinfo=UTC), datetime(2099, 3, 10, 2, tzinfo=UTC)),
        (_ist(10, 9), _ist(10, 10)),
    )
    assert slot.inside(_ist(10, 9), got) and not slot.inside(_ist(10, 10), got)
