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
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(DEFAULT_ZONE)


def today_line(now: datetime | None = None, zone: str | None = None) -> str:
    """"Today is Sunday, 19 October 2026 (Asia/Kolkata)." One short line.

    *now* is for a test. A naive *now* reads as UTC."""
    tz = _zone(zone)
    moment = now or datetime.now(tz)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=ZoneInfo("UTC"))
    local = moment.astimezone(tz)
    return f"Today is {local:%A}, {local.day} {local:%B %Y} ({tz.key})."


__all__ = ["DEFAULT_ZONE", "today_line"]
