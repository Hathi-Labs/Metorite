"""Today's date for a chat run (WS-53, ``crm_platform.md`` §18.16 part 1).

The incident of 2026-10-19 (production, web chat): a member asked for a
total "for the month of September" twice. One run answered for the current
year and the next for the year before. Neither run knew the date, because
no channel gave the model one (#1166 parity audit).

:func:`today_line` is the ONE line that says the date. The per-turn context
builder (``orchestrator.executor._context_preamble_parts``) adds it, and that
builder serves the web stream and the batch path that WhatsApp runs, so both
channels get the same line.

**The zone.** Metorite keeps no zone for an organization
(``gateway.routes.crm.field_sql.crm_zone`` says so too), so each org reads
:data:`DEFAULT_ZONE` until an org zone exists. A name that ``zoneinfo`` does
not know falls back to the default, never to UTC in silence.

Fence: ``tests/unit/test_crm_answer_consistency.py``.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

#: The zone of a run whose organization names none (owner, 2026-10-19).
DEFAULT_ZONE = "Asia/Kolkata"


def _zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or DEFAULT_ZONE)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return ZoneInfo(DEFAULT_ZONE)


def _valid(name: str | None) -> str | None:
    try:
        return ZoneInfo(name).key if name and name.strip() else None
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return None


def resolve_zone(*names: str | None) -> str:
    """The member's zone from candidates in order of trust (task 1649).

    The caller passes ``user_settings.timezone`` first, then
    ``people.timezone``. The first valid IANA name wins. ``UTC`` is the column
    default of ``user_settings.timezone``, so it only wins when no later
    candidate names a real zone. With no valid name the result is
    :data:`DEFAULT_ZONE`. Fence: ``tests/unit/test_chat_now_line.py``."""
    found = [v for v in (_valid(n) for n in names) if v]
    for name in found:
        if name != "UTC":
            return name
    return found[0] if found else DEFAULT_ZONE


def today_line(
    now: datetime | None = None, zone: str | None = None, *, with_time: bool = False,
) -> str:
    """"Today is Sunday, 19 October 2026 (Asia/Kolkata)." One short line.

    *now* is for a test. A naive *now* reads as UTC. With *with_time* the line
    is the "now" line of every chat turn (task 1649): date, time and zone,
    and the rule that the chat never asks the member for the time."""
    tz = _zone(zone)
    moment = now or datetime.now(tz)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=ZoneInfo("UTC"))
    local = moment.astimezone(tz)
    if with_time:
        return (
            f"Now: {local:%A %Y-%m-%d %H:%M} {local.tzname()} ({tz.key}). "
            "This is the member's own clock. Use it for \"now\", \"the next hour\" "
            "and \"this evening\". Never ask the member what time it is."
        )
    return f"Today is {local:%A}, {local.day} {local:%B %Y} ({tz.key})."


def part_of_day(zone: str | None, now: datetime | None = None) -> str:
    """``morning`` (5 to 11), ``afternoon`` (12 to 16), ``evening`` (17 to
    20) or ``night``, at the hour of *zone*. A bad or missing zone reads as
    :data:`DEFAULT_ZONE`. A naive *now* reads as UTC."""
    tz = _zone(zone)
    moment = now or datetime.now(tz)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=ZoneInfo("UTC"))
    hour = moment.astimezone(tz).hour
    if 5 <= hour <= 11:
        return "morning"
    if 12 <= hour <= 16:
        return "afternoon"
    if 17 <= hour <= 20:
        return "evening"
    return "night"


def part_of_day_line(zone: str | None, now: datetime | None = None) -> str:
    """"Part of the day for the member: morning." One short line.

    WS-63 PZ-4 (``personalization.md`` §5.2): the greeting rule says "for the
    time of day", and :func:`today_line` gives no hour. *zone* is the
    member's ``people.timezone``. Fence:
    ``tests/unit/test_executor_profile_block.py``."""
    return f"Part of the day for the member: {part_of_day(zone, now)}."


__all__ = ["DEFAULT_ZONE", "part_of_day", "part_of_day_line", "resolve_zone", "today_line"]
