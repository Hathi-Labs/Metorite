"""The web push message of a reminder (WS-66 RE-9a).

Spec: ``project-docs/specs/calendar_events_reminders.md`` §5.6 "Settled
2026-10-30 (RE-9)", decision 4.

``build`` is a pure function. It reads no database and no flag, and it sends
nothing. RE-9b calls it from the send step. The result is the one
``push.sender.PushMessage``, and the service worker
(``public/sw.js``) reads only its ``title``, ``body``, ``url`` and ``tag``. So
the org name goes in ``body``.

* **Shown:** the title is the reminder title and its time in the zone of the
  row. The body is the org name.
* **Hidden** (``hide_push_details`` on, for a shared device): the title is
  ``HIDDEN_TITLE`` and the body is the org name. No title or time text is in
  the payload. The url still holds the reminder id, which is a UUID and says
  nothing.

Fence: ``tests/unit/test_reach_push_message.py``.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

from gateway.push.sender import PushMessage, Urgency

#: The title when the member hides the details on the lock screen.
HIDDEN_TITLE = "Metorite: a reminder"

#: The longest reminder title in a push title. A long title would fill the
#: lock screen, and the device cuts it anyway.
TITLE_CLIP = 80

#: The urgency and the time to live (seconds) of each kind. The push service
#: holds an undelivered message for the TTL. A due reminder is time-bound, so
#: it expires in one hour. A check-back lives longer, and the unconfirmed
#: notice is the least urgent.
_KINDS: dict[str, tuple[Urgency, int]] = {
    "reminder_due": ("high", 3600),
    "reminder_check": ("normal", 4 * 3600),
    "reminder_unconfirmed": ("low", 12 * 3600),
}

#: The kinds this builder knows. ``tests/unit/test_reach_push_message.py``
#: pins it to ``REMINDER_KINDS`` of the feed.
KINDS = frozenset(_KINDS)


def reminder_url(reminder_id: str) -> str:
    """The tap target: a path on this app (``PushMessage.payload`` checks it)."""
    return f"/chat?reminder={quote(str(reminder_id), safe='')}"


def _zone(name: Any) -> ZoneInfo | None:
    try:
        return ZoneInfo(str(name)) if name else None
    except Exception:  # an unknown zone falls back to UTC
        return None


def _local(fire_at: Any, tz: Any) -> datetime | None:
    if isinstance(fire_at, str):
        try:
            fire_at = datetime.fromisoformat(fire_at)
        except ValueError:
            return None
    if not isinstance(fire_at, datetime):
        return None
    if fire_at.tzinfo is None:
        fire_at = fire_at.replace(tzinfo=UTC)
    return fire_at.astimezone(_zone(tz) or UTC)


def _clock(at: datetime) -> str:
    """``9:30 AM``: no leading zero, and no strftime flag that differs by OS."""
    hour = at.hour % 12 or 12
    return f"{hour}:{at.minute:02d} {'AM' if at.hour < 12 else 'PM'}"


def build(kind: str, reminder: Mapping[str, Any], org_name: str,
          hide_details: bool) -> PushMessage:
    """The push of one reminder.

    ``reminder`` holds ``id``, ``title``, ``fire_at`` and ``tz``. ``org_name``
    names the org, so a device with several accounts shows whose reminder it is.
    """
    if kind not in _KINDS:
        raise KeyError(kind)
    urgency, ttl = _KINDS[kind]
    rid = str(reminder["id"])
    org = (org_name or "").strip() or "Metorite"
    if hide_details:
        title = HIDDEN_TITLE
    else:
        text = " ".join(str(reminder.get("title") or "").split()) or "Reminder"
        if len(text) > TITLE_CLIP:
            text = text[: TITLE_CLIP - 1].rstrip() + "…"
        at = _local(reminder.get("fire_at"), reminder.get("tz"))
        title = f"{text} at {_clock(at)}" if at is not None else text
    return PushMessage(title=title, body=org, url=reminder_url(rid), tag=rid,
                       urgency=urgency, ttl=ttl)
