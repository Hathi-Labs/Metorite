"""The times of the reminder ladder, and the smart slot (WS-66 RM-2).

Spec: ``project-docs/specs/calendar_events_reminders.md`` §5.4 (the
defaults) and §5.5 (the smart slot) · D114.

Pure code. Nothing here reads a database, a model or a clock: the caller
passes the member's day, the busy blocks and ``now``. So
``tests/unit/test_reminder_slot.py`` holds every rule.

**Quiet hours are ONE rule with Reach.** :func:`quiet` is
``reach.plan.in_quiet_hours`` on the member's ``state.Day``: outside the
member's ``day_start_hour`` to ``day_end_hour``, in the member's
``user_settings.timezone``. RM-2 has no weekend rule, so every day is a
working day here. Steps 2 to 5 of the ladder never land in quiet hours.
Step 1 fires at the member's own time, also in quiet hours (D114.3).

**The smart slot** (:func:`next_slot`) picks, in order:

1. A time that the member named.
2. The same time of day on a later day: the first day after the reminder's
   day that is not quiet and not busy at that time.
3. The next free gap of the member's awake hours that is long enough for
   ``estimate_minutes``. The gaps come from ``tasks.calendar._free_intervals``,
   the planner's own rule.

A slot is always after ``now``, never in quiet hours, and never inside a busy
block. The work, personal, weekend and pinned rules of §5.5 are RM-2c, so
RM-2 treats every reminder the same.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

#: The grace before a check-back (§5.4): the estimate plus this, kept
#: between :data:`GRACE_MIN` and :data:`GRACE_MAX`.
GRACE_PAD = timedelta(minutes=30)
GRACE_MIN = timedelta(minutes=30)
GRACE_MAX = timedelta(hours=3)
#: The grace when the reminder has no estimate.
GRACE_NO_ESTIMATE = timedelta(minutes=60)

#: The days that the smart slot looks at, for options 2 and 3.
SLOT_DAYS = 14
#: The span of busy blocks that a caller reads for :func:`next_slot`: one
#: day before ``now`` (a block that already started) to past the last day.
BUSY_BEFORE = timedelta(days=1)
BUSY_AFTER = timedelta(days=SLOT_DAYS + 2)
#: The gap that a reminder with no estimate needs: the planner's smallest
#: slot (``tasks.calendar._MIN_SLOT``).
DEFAULT_GAP = timedelta(minutes=15)

Busy = tuple[tuple[datetime, datetime], ...]


@dataclass(frozen=True)
class MemberDay:
    """What :func:`next_slot` knows of the member: their ``state.Day`` (the
    zone and the day hours) and their busy blocks, as aware instants."""

    day: Any
    busy: Busy = ()


def grace(estimate_minutes: int | None) -> timedelta:
    """The wait before the check-back of an ``action`` reminder (§5.4).

    The estimate plus 30 minutes, kept between 30 minutes and 3 hours. With no
    estimate, 60 minutes."""
    if not estimate_minutes or estimate_minutes <= 0:
        return GRACE_NO_ESTIMATE
    wait = timedelta(minutes=int(estimate_minutes)) + GRACE_PAD
    return min(GRACE_MAX, max(GRACE_MIN, wait))


def quiet(day: Any, at: datetime) -> bool:
    """True when *at* is in the member's quiet hours. The Reach rule
    (``reach.plan.in_quiet_hours``), with every day a working day."""
    from gateway.reach.plan import in_quiet_hours

    return in_quiet_hours(day, at, True)


def next_day_start(day: Any, at: datetime) -> datetime:
    """The first start of the member's day strictly after *at*. Step 3 (the
    brief) waits for it, and a step in quiet hours waits for it too."""
    return day.at(day.feed_day(at) + timedelta(days=1), day.start_hour)


def awake(day: Any, at: datetime) -> datetime:
    """*at*, or the next start of the member's day when *at* is quiet."""
    return next_day_start(day, at) if quiet(day, at) else at


def _aware(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def busy_blocks(rows: Any) -> Busy:
    """The ``(start, end)`` of each row with a block, as aware instants. A row
    with no end, or an end that is not after its start, is no block."""
    out: list[tuple[datetime, datetime]] = []
    for row in rows or ():
        start = _aware(getattr(row, "scheduled_start", None))
        end = _aware(getattr(row, "scheduled_end", None))
        if start is not None and end is not None and end > start:
            out.append((start, end))
    return tuple(sorted(out))


def inside(at: datetime, busy: Busy) -> bool:
    """True when *at* falls inside a busy block: start <= at < end."""
    return any(start <= at < end for start, end in busy)


def _fits(member: MemberDay, at: datetime, now: datetime) -> bool:
    return at > now and not quiet(member.day, at) and not inside(at, member.busy)


def _same_time_later(member: MemberDay, base: datetime, now: datetime) -> datetime | None:
    """Option 2: the time of day of *base*, on the first later day that fits."""
    local = base.astimezone(member.day.tz)
    clock = local.time().replace(tzinfo=None)
    for step in range(1, SLOT_DAYS + 1):
        candidate = datetime.combine(local.date() + timedelta(days=step), clock,
                                     tzinfo=member.day.tz)
        if candidate <= now:
            continue
        if quiet(member.day, candidate):
            # The same clock time is quiet on every day, so no day fits.
            return None
        if not inside(candidate, member.busy):
            return candidate
    return None


def _day_window(day: Any, local_day: Any) -> tuple[datetime, datetime]:
    """The awake hours of one local day, as instants. An end at or before the
    start reads as the next day, as ``reach.plan`` reads it."""
    end_hour = day.end_hour if day.end_hour > day.start_hour else day.end_hour + 24
    return day.at(local_day, day.start_hour), day.at(local_day, end_hour)


def _first_gap(member: MemberDay, need: timedelta, now: datetime) -> datetime | None:
    """Option 3: the start of the first free gap of the member's awake hours
    that is at least *need* long, from *now*, over :data:`SLOT_DAYS` days."""
    from gateway.routes.tasks.calendar import _free_intervals

    first = member.day.feed_day(now)
    soonest = _next_quarter(now)
    for step in range(SLOT_DAYS):
        start, end = _day_window(member.day, first + timedelta(days=step))
        if end <= now:
            continue
        for gap_start, gap_end in _free_intervals(start, end, list(member.busy), now):
            # A gap that opens at now exactly (a day start) starts a quarter later.
            at = gap_start if gap_start > now else soonest
            if gap_end - at >= need and _fits(member, at, now):
                return at
    return None


def _next_quarter(now: datetime) -> datetime:
    """The first quarter of an hour strictly after *now*."""
    base = now.replace(second=0, microsecond=0)
    return base + timedelta(minutes=15 - base.minute % 15)


def next_slot(member: MemberDay, reminder: Any, now: datetime, *,
              named: datetime | None = None) -> datetime:
    """The smart slot of §5.5 for *reminder*: an aware instant after *now*.

    *reminder* gives ``fire_at`` (the time it last fired, or was set for) and
    ``estimate_minutes``. *named* is a time that the member named. It wins
    when it is after *now*, not quiet and not inside a busy block.

    With no gap in :data:`SLOT_DAYS` days, the slot is the day start that
    follows them, moved past any busy block (:func:`_clear`)."""
    if named is not None:
        named = _aware(named)
        if named is not None and _fits(member, named, now):
            return named
    base = _aware(getattr(reminder, "fire_at", None)) or now
    same = _same_time_later(member, base, now)
    if same is not None:
        return same
    minutes = getattr(reminder, "estimate_minutes", None)
    need = timedelta(minutes=int(minutes)) if minutes and minutes > 0 else DEFAULT_GAP
    gap = _first_gap(member, need, now)
    if gap is not None:
        return gap
    last = member.day.feed_day(now) + timedelta(days=SLOT_DAYS)
    return _clear(member, member.day.at(last, member.day.start_hour))


#: The moves of :func:`_clear`, at most: past a block, or to a day start.
_CLEAR_MOVES = 200


def _clear(member: MemberDay, at: datetime) -> datetime:
    """*at*, moved past each busy block it falls in and out of quiet hours,
    so the last resort is not quiet and not busy either."""
    for _ in range(_CLEAR_MOVES):
        ends = [end for start, end in member.busy if start <= at < end]
        if ends:
            at = max(ends)
        elif quiet(member.day, at):
            at = next_day_start(member.day, at)
        else:
            break
    return at
