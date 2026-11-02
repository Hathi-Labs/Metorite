"""The web push message of a reminder (WS-66 RE-9a).

Spec: ``project-docs/specs/calendar_events_reminders.md`` §5.6 "Settled
2026-10-30 (RE-9)", decision 4.

``build`` is a pure function. It reads no database and no flag, and it sends
nothing. RE-9b calls it from the send step. The result is the one
``push.sender.PushMessage``, and the service worker
(``public/sw.js``) reads its ``title``, ``body``, ``url`` and ``tag``, and the
``reminder_id`` and ``kind`` that RX-4 adds. So the org name goes in ``body``.
The worker shows the Done and "In 1 hour" buttons for a ``reminder_due`` or
``reminder_check`` push, and the buttons call the reminder routes.

* **Shown:** the title is the reminder title and its time in the zone of the
  row. The body is the org name.
* **Hidden** (``hide_push_details`` on, for a shared device): the title is
  ``HIDDEN_TITLE`` and the body is the org name. No title or time text is in
  the payload. The url, ``reminder_id`` and ``kind`` still hold the reminder
  id, which is a UUID, and the card kind. Neither says anything.

**The morning summary** (RX-5, ``calendar_events_reminders.md`` §5.11).
:func:`build_due` makes the push of ``reach/due_push.py``. It is no reminder,
so it has no reminder id and stays out of :data:`KINDS`. The title gives the
counts and the body gives the first title. With the details hidden, the body
is the org name: a count says nothing about a task.

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
#: pins it to ``REMINDER_KINDS`` of the needs path (``routes/shell/needs.py``).
KINDS = frozenset(_KINDS)

#: The kind of the morning summary of due tasks (RX-5).
DUE_SUMMARY = "due_summary"
#: Seconds that a summary stays useful. It holds the morning, and a day start
#: missed by more than this plans nothing (``push_channel.too_late``).
DUE_TTL_S = 4 * 3600
#: A new summary replaces yesterday's on the device, so summaries do not stack.
DUE_TAG = "due-summary"
#: My Day is the home of the app.
MY_DAY_URL = "/"


def ttl_of(kind: str) -> int:
    """The time to live of *kind*, in seconds. The send step of RE-9b skips a
    planned row older than this (``reach/push_channel.py``), so a late retry
    never sends a push that the push service would already drop."""
    if kind == DUE_SUMMARY:
        return DUE_TTL_S
    if kind not in _KINDS:
        raise KeyError(kind)
    return _KINDS[kind][1]


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
                       urgency=urgency, ttl=ttl, reminder_id=rid, kind=kind)


def due_title(due_today: int, overdue: int, *, capped: bool = False) -> str:
    """``3 due today, 1 overdue``. A count of 0 stays out. A read that stopped
    at its cap saw a floor and does not know the split, so it says so."""
    if capped:
        return f"{due_today + overdue}+ due or overdue"
    parts = [f"{n} {words}" for n, words in ((due_today, "due today"), (overdue, "overdue"))
             if n > 0]
    return ", ".join(parts)


def build_due(*, due_today: int, overdue: int, first_title: str, org_name: str,
              hide_details: bool, capped: bool = False) -> PushMessage:
    """The morning summary: the counts, then the first title and ``and 2
    more``. A tap opens My Day. With the details hidden, the body is the org
    name and no task title is in the payload."""
    if hide_details:
        body = (org_name or "").strip() or "Metorite"
    else:
        body = " ".join(str(first_title or "").split()) or "A task"
        if len(body) > TITLE_CLIP:
            body = body[: TITLE_CLIP - 1].rstrip() + "…"
        more = due_today + overdue - 1
        if more > 0:
            body = f"{body} and {more}{'+' if capped else ''} more"
    return PushMessage(title=due_title(due_today, overdue, capped=capped), body=body,
                       url=MY_DAY_URL, tag=DUE_TAG, urgency="normal", ttl=DUE_TTL_S)
