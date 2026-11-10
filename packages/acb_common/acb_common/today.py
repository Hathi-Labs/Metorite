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
    :data:`DEFAULT_ZONE`. The last fallback is Asia/Kolkata and not UTC on
    purpose: the owner directive of 2026-10-19 (see :data:`DEFAULT_ZONE`)
    sets it, and task 1649 keeps it. Fence: ``tests/unit/test_chat_now_line.py``."""
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


#: The ONE table of greetings (PZ-39). Each row is ``(first hour, part of the
#: day, greeting)``. A row runs until the next row starts. The last row runs
#: past midnight until the first row starts again at 05:00. The late hours
#: read "Hello" and never "Good night", because "Good night" is a goodbye.
#: My Day holds the same table in ``myDay.ts`` as ``GREETING_TABLE``.
#: Fence: ``tests/unit/test_greeting_table.py``.
GREETING_TABLE: tuple[tuple[int, str, str], ...] = (
    (5, "morning", "Good morning"),
    (12, "afternoon", "Good afternoon"),
    (17, "evening", "Good evening"),
    (21, "night", "Hello"),
)


def _hour_at(zone: str | None, now: datetime | None) -> int:
    tz = _zone(zone)
    moment = now or datetime.now(tz)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=ZoneInfo("UTC"))
    return moment.astimezone(tz).hour


def _row_at(zone: str | None, now: datetime | None) -> tuple[int, str, str]:
    hour = _hour_at(zone, now)
    row = GREETING_TABLE[-1]
    for entry in GREETING_TABLE:
        if entry[0] <= hour:
            row = entry
    return row


def part_of_day(zone: str | None, now: datetime | None = None) -> str:
    """``morning`` (5 to 11), ``afternoon`` (12 to 16), ``evening`` (17 to
    20) or ``night``, at the hour of *zone*. The rows come from
    :data:`GREETING_TABLE`. A bad or missing zone reads as
    :data:`DEFAULT_ZONE`. A naive *now* reads as UTC."""
    return _row_at(zone, now)[1]


def greeting_for(zone: str | None, now: datetime | None = None) -> str:
    """The greeting word for the hour of *zone*, from :data:`GREETING_TABLE`:
    "Good morning", "Good afternoon", "Good evening" or "Hello"."""
    return _row_at(zone, now)[2]


#: The gap that ends a conversation (PZ-39, owner 2026-11-06). The chat greets
#: in the first reply of a chat, and in the first reply after this many hours
#: with no message in that chat. It never greets in the middle of one.
GREET_GAP_HOURS = 4


def greet_due(last_message_ms: int | None, now_ms: int, *, first_reply: bool) -> bool:
    """True when this turn opens a conversation. Pure.

    *first_reply* means the chat holds no earlier message. Otherwise
    *last_message_ms* is the newest stored message BEFORE this turn, and the
    gap must reach :data:`GREET_GAP_HOURS`. A missing time gives False."""
    if first_reply:
        return True
    if last_message_ms is None:
        return False
    return now_ms - last_message_ms >= GREET_GAP_HOURS * 3600 * 1000


def part_of_day_line(
    zone: str | None, now: datetime | None = None, *, greet: bool | None = None,
    language: str | None = None,
) -> str:
    """One short line: the part of the day, and what to do about a greeting.

    WS-63 PZ-4 (``personalization.md`` §5.2): the greeting rule says "for the
    time of day", and :func:`today_line` gives no hour. *zone* is the
    member's resolved zone. *greet* has three states:

    * ``True``: the run decided that this turn opens a conversation. The line
      says "Greet now." and names the greeting.
    * ``False``: the run decided that it does not. The line says not to greet.
    * ``None``: no decision exists (a WhatsApp, delegated, batch or group
      run). The line keeps the old rule: greet only in the first reply of the
      conversation, and never again in it.

    The late hours never say "Good night", because it is a goodbye. From 21:00
    to 23:59 the line offers "Hello" or "Good evening". From 00:00 to 04:59 it
    offers "Hello" only. When *language* is a tag whose primary subtag is not
    ``en``, the line asks for the common hello of that language, and it bars a
    good-night phrase in any language. The line never guesses a language from
    a name or a region. Fence: ``tests/unit/test_executor_profile_block.py``."""
    part = part_of_day(zone, now)
    base = f"Part of the day for the member: {part}."
    if greet is False:
        return f"{base} Do not greet in this reply."
    lead = "Greet now." if greet else (
        "Greet only in the first reply of the conversation, and never again in it.")
    tag = (language or "").strip()
    if tag and tag.replace("_", "-").split("-")[0].lower() != "en":
        return (
            f"{base} {lead} The member's language is \"{tag}\". Greet with "
            "the common hello of that language, for example Namaste, Namaskaram "
            "or Vanakkam. Do not copy a time-of-day phrase from forwarded messages. "
            "Never use a good-night phrase in any language, such as \"Shubha "
            "ratri\", because it is a farewell."
        )
    word = greeting_for(zone, now)
    if part != "night":
        return f"{base} {lead} Greet with \"{word}\"."
    offer = f'\"{word}\"'
    if _hour_at(zone, now) >= 21:
        offer += ' or \"Good evening\"'
    return (
        f"{base} {lead} Greet with {offer}. "
        "Never say \"Good night\", because it is a goodbye."
    )


__all__ = [
    "DEFAULT_ZONE",
    "GREETING_TABLE",
    "GREET_GAP_HOURS",
    "greet_due",
    "greeting_for",
    "part_of_day",
    "part_of_day_line",
    "resolve_zone",
    "today_line",
]
