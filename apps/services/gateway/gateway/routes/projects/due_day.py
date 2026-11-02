"""A due date is a day, and a reminder is a moment (RX-5).

Owner directive, 2026-11-01. It replaces D-PM-22 of
``project_management_app.md``. Spec: ``calendar_events_reminders.md`` §5.11.

``pm_tasks.due_at`` holds one instant. The client stores local noon for a day
that the member picks, and the seam below stores local noon for a date-only
value from any other caller. The instant only carries the day. So:

* **The one rule.** A task is overdue when its LOCAL due date is before the
  member's LOCAL today. A task due today stays due today until that day ends
  in the member's zone, whatever hour the row holds.
* **The SQL form of the rule.** ``local date of due_at < today`` is the same
  test as ``due_at < the instant that today started``. So a statement binds
  ``:day_start`` (:func:`day_start`, or ``personal.member_day_start`` for a
  reader) and uses :func:`overdue_sql` and :func:`not_overdue_sql`. No other
  statement compares ``due_at`` with ``now()`` to find late work.
* **Whose today.** The reader's. A shared board counts a colleague's task late
  by the zone of the member who reads it, because that member's own cards and
  filters must agree with the count.
* **The client half** is ``isOverdue`` in ``src/lib/taskCard.ts``. It compares
  the same two local dates, in the zone that the browser stores in
  ``user_settings.timezone``.

**Pure.** This module reads no database and no clock, and it imports nothing
from the Projects package, so ``core``, ``filters`` and ``personal`` can all
import it. The read of the member's zone is ``personal.stored_zone``.

Fences: ``tests/unit/test_due_day.py`` holds the rule and refuses a new
``due_at < now()`` in the gateway. ``test_due_push_r8.py`` runs the SQL form on a
real Postgres. ``src/lib/taskCard.test.ts`` holds the client half.
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

#: The hour that stands for a date with no time, the one that the client sends
#: (``dueInstantForDay`` in ``app/projects/lib/quickAdd.ts``). Noon keeps the
#: day for every viewer within 11 hours of the member who set it.
NOON = time(12)


def zone_name(tz_name: Any) -> str:
    """``tz_name`` when it is an IANA zone, else ``"UTC"``, the default
    `user_settings.timezone` carries. One rule for the date and for the name,
    so a route never reports a zone that its date was not computed in."""
    try:
        ZoneInfo(str(tz_name or "UTC"))
    except (ZoneInfoNotFoundError, ValueError, OSError):
        # OSError: "America" is a directory of the zone database, not a zone.
        return "UTC"
    return str(tz_name or "UTC")


def local_date(tz_name: Any, at: datetime) -> date:
    """``at`` as a calendar date in the IANA zone ``tz_name``. An unknown or
    empty zone reads as UTC, the default `user_settings.timezone` carries."""
    return at.astimezone(ZoneInfo(zone_name(tz_name))).date()


def is_overdue(due: datetime, tz_name: Any, today: date) -> bool:
    """True when the local date of *due* is before *today*, the member's own
    date in the same zone. The one rule of this module."""
    return local_date(tz_name, due) < today


def day_start(tz_name: Any, at: datetime) -> datetime:
    """The instant that the local date of *at* started in the zone. A row with
    ``due_at`` before it is overdue at *at*."""
    return datetime.combine(local_date(tz_name, at), time.min,
                            tzinfo=ZoneInfo(zone_name(tz_name)))


def noon_of(day: date, tz_name: Any) -> datetime:
    """Noon of *day* in the zone: the instant that stands for a date."""
    return datetime.combine(day, NOON, tzinfo=ZoneInfo(zone_name(tz_name)))


def date_only(value: Any) -> date | None:
    """The date of a value that holds a date and no time, or None. A value
    with a time, a value that is not a string and a value that does not parse
    answer None, so the caller leaves them as they are."""
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def overdue_sql(alias: str = "t") -> str:
    """The rule as a predicate on ``<alias>.due_at``. It needs ``:day_start``
    bound to an aware datetime. A NULL ``due_at`` matches nothing."""
    return f"{alias}.due_at < :day_start"


def not_overdue_sql(alias: str = "t") -> str:
    """The other side of :func:`overdue_sql`: due today or later. The two are
    disjoint, so a bucket read that uses both counts each task once."""
    return f"{alias}.due_at >= :day_start"
