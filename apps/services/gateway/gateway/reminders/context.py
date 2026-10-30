"""Reminders — the words of a tap that opens the chat (WS-66 RM-4, D114).

Spec: ``project-docs/specs/calendar_events_reminders.md`` §5.7.

Pure functions, with no database, no clock and no model. The caller,
``routes/projects/reminders.open_reminder``, reads the member's own row, the
linked object as the member may see it, and the ledger tail through that row.
Then it calls these functions:

* :func:`first_message` — the text of the assistant's first row in the chat.
  Code builds it, so it is instant and free (§5.7 item 2).
* :func:`context_facts` — the ``reminder`` context block. The dock publishes
  it as the page facts while the chat is about this reminder, so a typed
  answer reaches the model with the row and its ledger (§5.7 item 4).
* :func:`card_data` — the data of the ``reminderCard`` template, which draws
  the chips of §5.7 item 3.

**Text that a person wrote is DATA.** The title, the notes, and the title and
the status of a linked object go into the facts inside «marks»
(:func:`data`), with each mark of their own taken out first, so a title
cannot end the fence early. The block tells the model that names in it may
hold instructions (``assistant.ts`` ``surfaceContext``).

**A linked object that the member cannot open shows nothing of itself**: no
title, no status and no id. Only its kind shows.

Fence: ``tests/unit/test_reminder_context.py``.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

#: How many ledger rows the facts carry: the newest ones, oldest first.
LEDGER_TAIL = 8
#: The longest notes text that reaches the facts, in characters.
NOTES_MAX = 500
#: The longest title in the label of the context chip.
LABEL_TITLE_MAX = 60

#: The noun of each kind of link (§5.1), for a line that names no content.
SUBJECT_NOUNS: dict[str, str] = {
    "task": "a task",
    "project": "a project",
    "email_thread": "an email thread",
    "crm_contact": "a CRM contact",
    "wa_chat": "a WhatsApp chat",
}

#: The kinds whose title and status RM-4 reads, through the member's own
#: visibility. The other kinds show their kind only (deferred in RM-4).
READ_SUBJECTS = frozenset({"task", "project"})


@dataclass(frozen=True)
class Subject:
    """The linked object of a reminder, as the member may see it.

    ``hidden`` is True when the member cannot open the object. Then nothing
    of it shows. ``title`` is set only when the read found the object and the
    member may see it. With neither, the object is of a kind that RM-4 does
    not read.
    """

    kind: str
    subject_id: str | None = None
    title: str | None = None
    status: str | None = None
    hidden: bool = False


# ── Text ────────────────────────────────────────────────────────────────────


def plain(value: Any, cap: int | None = None) -> str:
    """*value* as one line of plain text.

    NFKC first. Then each control character becomes a space, and each format
    character (zero width, bidi) and each « and » goes. All whitespace becomes
    one space. A text longer than *cap* stops at *cap* characters, with an
    ellipsis."""
    folded = unicodedata.normalize("NFKC", str(value or ""))
    folded = "".join(
        " " if unicodedata.category(ch) == "Cc"
        else "" if unicodedata.category(ch) == "Cf" or ch in "«»"
        else ch
        for ch in folded
    )
    out = " ".join(folded.split())
    if cap is not None and len(out) > cap:
        out = out[:cap].rstrip() + "…"
    return out


def data(value: Any, cap: int | None = None) -> str:
    """*value* fenced as data: ``«text»``, with no mark of its own inside."""
    return f"«{plain(value, cap)}»"


# ── Time ────────────────────────────────────────────────────────────────────


def day_words(day: date, today: date) -> str:
    """A local date in words: today, yesterday, tomorrow, on Monday, or on 12
    Oct. The same words as the My Day feed (``feed._day_words``)."""
    delta = (day - today).days
    if delta == 0:
        return "today"
    if delta == -1:
        return "yesterday"
    if delta == 1:
        return "tomorrow"
    if abs(delta) <= 6:
        return f"on {day.strftime('%A')}"
    return f"on {day.day} {day.strftime('%b')}"


def when_words(at: datetime | None, zone: ZoneInfo, now: datetime) -> str:
    """``15:00 today``: the clock and the day of *at* in *zone*, or ``""``."""
    if at is None:
        return ""
    local = at.astimezone(zone)
    today = now.astimezone(zone).date()
    return f"{local.hour:02d}:{local.minute:02d} {day_words(local.date(), today)}"


def _stamp(at: datetime, zone: ZoneInfo) -> str:
    """``29 Oct 15:05``: one ledger time in *zone*."""
    local = at.astimezone(zone)
    return f"{local.day} {local.strftime('%b')} {local.hour:02d}:{local.minute:02d}"


# ── The parts ───────────────────────────────────────────────────────────────


def answer_words(row: Any) -> str:
    """What the first message asks or says after the time, by the state.

    An ``action`` reminder that fired asks "Did you do it?". A ``notice``
    says nothing more, because it closes when the member opens it (§5.3). A
    closed reminder says how it closed. A ``scheduled`` one says nothing
    more: its time is in the line."""
    if row.kind == "notice":
        return ""
    if row.state in ("due", "unconfirmed"):
        return "Did you do it?"
    if row.state == "done":
        return {
            "promoted": "It is a task now.",
            "evidence": "It closed on its own.",
        }.get(row.resolution or "", "You marked it done.")
    if row.state == "dropped":
        return "You dropped it."
    if row.state == "cancelled":
        return "It was cancelled."
    return ""


def subject_line(subject: Subject | None) -> str:
    """The one line of the linked object, for the member (§5.7 item 2).

    A task shows its title and its status, and a project its name. An object
    that the member cannot open shows its kind only. RM-4 reads no other
    kind, so an email thread, a CRM contact or a WhatsApp chat shows its kind
    and "linked"."""
    if subject is None:
        return ""
    noun = SUBJECT_NOUNS.get(subject.kind, "an item")
    if subject.hidden:
        return f"Linked: {noun} that you cannot open."
    if subject.title:
        name = plain(subject.title)
        if subject.kind == "task":
            status = plain(subject.status) if subject.status else ""
            return f"Task: {name}" + (f" · {status}" if status else "")
        return f"Project: {name}"
    return f"Linked: {noun}."


def first_message(
    row: Any, *, zone: ZoneInfo, now: datetime, subject: Subject | None,
) -> str:
    """The assistant's first row: "Reminder · Get a haircut · 15:00 today.
    Did you do it?", then the line of the linked object (§5.7 item 2)."""
    head = f"Reminder · {plain(row.title)}"
    when = when_words(row.fire_at, zone, now)
    if when:
        head += f" · {when}"
    text = f"{head}. {answer_words(row)}".strip()
    link = subject_line(subject)
    return f"{text}\n\n{link}" if link else text


def label(row: Any) -> str:
    """The words of the context chip: ``Reminder · <title>``. The chip shows
    them, and no model reads them."""
    return f"Reminder · {plain(row.title, LABEL_TITLE_MAX)}"


def _subject_fact(subject: Subject | None) -> str:
    """The linked object for the model. Its words are fenced as data."""
    if subject is None:
        return ""
    noun = SUBJECT_NOUNS.get(subject.kind, "an item")
    if subject.hidden:
        return f"Linked: {noun} that the member cannot open. Nothing of it is shown here."
    if subject.title:
        line = f"Linked {subject.kind}: {data(subject.title)}"
        if subject.kind == "task" and subject.status:
            line += f", status {data(subject.status)}"
        return line + f" (id {subject.subject_id})."
    ident = f" (id {data(subject.subject_id)})" if subject.subject_id else ""
    return f"Linked: {noun}{ident}. This chat did not read it."


def history(ledger: list[Any], zone: ZoneInfo) -> str:
    """The ledger tail as one line, oldest first: ``created 28 Oct 10:00,
    fired 29 Oct 15:00``. *ledger* comes newest first, as the read orders it,
    and only the newest :data:`LEDGER_TAIL` rows count. Event names and times
    only: a detail can hold a field name, and never a value."""
    rows = list(ledger)[:LEDGER_TAIL]
    return ", ".join(f"{r.event} {_stamp(r.at, zone)}" for r in reversed(rows))


def context_facts(
    row: Any, *, zone: ZoneInfo, now: datetime, subject: Subject | None,
    ledger: list[Any],
) -> list[str]:
    """The ``reminder`` context block of §5.7 item 4, one fact a line.

    It names the reminder id, so a tool of §5.8 can act on it. The state is
    the state when the chat opened, and the line says so."""
    facts = [
        f"This chat is about the member's own reminder. reminder_id: {row.id}",
        f"Title: {data(row.title)}",
    ]
    if plain(row.notes):
        facts.append(f"Notes: {data(row.notes, NOTES_MAX)}")
    state = row.state + (f" ({row.resolution})" if row.resolution else "")
    facts.append(f"Kind: {row.kind}. State when the chat opened: {state}.")
    when = when_words(row.fire_at, zone, now)
    if when:
        facts.append(
            f"Time: {when}, in the zone {zone.key} ({row.fire_at.isoformat()})."
        )
    facts.append(
        f"Moves: the member moved it {int(row.snoozes or 0)} times, "
        f"and Metorite moved it {int(row.cycles or 0)} times."
    )
    link = _subject_fact(subject)
    if link:
        facts.append(link)
    past = history(ledger, zone)
    if past:
        facts.append(f"History, oldest first: {past}.")
    return facts


def card_data(
    row: Any, *, zone: ZoneInfo, now: datetime, subject: Subject | None,
) -> dict[str, Any]:
    """The data of the ``reminderCard`` template (``genUITemplates.tsx``).

    ``zone`` is the zone that the card writes a new time in, after "Not yet".
    The card acts on ``reminderId`` through the member's own routes."""
    out: dict[str, Any] = {
        "reminderId": str(row.id),
        "title": plain(row.title),
        "when": when_words(row.fire_at, zone, now),
        "zone": zone.key,
        "state": row.state,
        "resolution": row.resolution,
        "kind": row.kind,
        "link": subject_line(subject),
    }
    return {k: v for k, v in out.items() if v not in (None, "")}
