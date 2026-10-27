"""Projects · due — a due DATE becomes noon in the member's own zone.

WS-64 chat wave 2 (2026-10-27). Spec:
``project-docs/specs/project_management_app.md`` §"Due dates".

``pm_tasks.due_at`` is an instant (``timestamptz``). The app writes a day
that a member picks as NOON in the browser's zone
(``workbench/control_plane/src/app/projects/lib/quickAdd.ts``,
``dueInstantForDay``). Noon keeps the task on that day for every viewer
within eleven hours of the writer.

The chat and the MCP edge sent the bare day (``2026-10-30``). The write
stored it as midnight UTC. In Los Angeles that is the evening before, so the
task showed one day early and went overdue one day early. In India it went
overdue at 05:30 on the day itself.

So each gateway path that writes ``due_at`` turns a bare day into noon in the
caller's stored zone (``user_settings.timezone``, UTC when there is none) at
ONE place, :func:`member_due_values`. Every caller gets the same rule, and a
client keeps no copy. A value with a time is left as it is. ``start_date`` is
a ``date`` column, so it has no zone and this module does not touch it.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

#: A due value that names a calendar day and no time.
DAY_ONLY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
#: The hour of the day that a bare due day stands for (``dueInstantForDay``).
DUE_HOUR = 12


def is_day_only(value: Any) -> bool:
    """True for ``"2026-10-30"`` and no other shape."""
    return isinstance(value, str) and DAY_ONLY.fullmatch(value.strip()) is not None


def noon_in(day: str, zone: str) -> str:
    """The bare *day* as noon in the IANA *zone*, an ISO instant with its
    offset. A day that is not real is returned as it is, so the write's own
    check (``core.coerce_write_values``) answers its 422."""
    try:
        y, m, d = (int(part) for part in day.strip().split("-"))
        at = datetime(y, m, d, DUE_HOUR, tzinfo=ZoneInfo(zone))
    except ValueError:
        return day
    return at.isoformat()


async def member_zone(db: Any, email: str) -> str:
    """The member's stored zone, or ``"UTC"``. The one read is
    ``personal.stored_zone``, and ``personal.zone_name`` judges it."""
    from gateway.routes.projects.personal import stored_zone, zone_name

    return zone_name(await stored_zone(db, email))


async def due_instant(db: Any, email: str, value: Any) -> Any:
    """*value* as the instant to store: a bare day becomes noon in the
    member's zone, and any other value is returned as it is. It reads the
    zone only when there is a bare day to turn."""
    if not is_day_only(value):
        return value
    return noon_in(str(value), await member_zone(db, email))


async def member_due_values(db: Any, email: str, values: dict[str, Any]) -> None:
    """Turn a bare ``due_at`` day in *values* into noon in the member's zone,
    in place (:func:`due_instant`)."""
    if "due_at" in values:
        values["due_at"] = await due_instant(db, email, values["due_at"])
