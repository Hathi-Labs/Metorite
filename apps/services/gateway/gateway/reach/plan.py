"""What Reach sends, decided in pure functions (WS-59 RE-1a).

Spec: ``project-docs/specs/proactive_reach.md`` §5.1 (the gate in code), §6
(the budget) and §9 (the message shapes).

Nothing here reads a database, Redis or a clock. The loop passes the cards of
``feed.feed_cards``, the pairs it already logged, the member's ``state.Day``
and ``now``. So ``tests/unit/test_reach_plan.py`` holds every rule.

The voice is a chief of staff: lead with what the member must do, one line
for each item, no filler. Each line comes from the card's own title and
facts. Nothing is invented.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from typing import Any

from gateway.reach.classes import REACHING, reach_class
from gateway.routes.shell import feed_rank
from gateway.routes.shell.needs import _instant

#: A needs-reply card interrupts only while the thread is this fresh.
REPLY_FRESH = timedelta(hours=12)
#: The brief goes out in the first hours of the member's day.
BRIEF_WINDOW_HOURS = 3
#: Lines of the brief, before the count of the rest.
BRIEF_LINES = 7
#: The most characters of one message.
MAX_CHARS = 1000
#: The most characters of a title inside a line.
TITLE_CHARS = 80

#: The card states that may reach. The state join already drops a card that
#: is snoozed or dismissed. ``later`` is the member's own "not now".
OPEN_STATES = frozenset({"new", "seen"})
#: The groups of the brief.
BRIEF_GROUPS = ("now", "needs_you")

Pair = tuple[str, str]


# ── Which cards reach ───────────────────────────────────────────────────────


def pair(card: dict[str, Any]) -> Pair:
    """The identity of one card at one fingerprint."""
    return str(card["signal_id"]), str(card.get("fingerprint") or "")


def is_open(card: dict[str, Any]) -> bool:
    """True when the member has not put the card aside."""
    if card.get("group") in ("later", "handled"):
        return False
    return str(card.get("state") or "new") in OPEN_STATES


def _reply_fresh(card: dict[str, Any], now: datetime) -> bool:
    since = _instant((card.get("facts") or {}).get("waiting_since") or card.get("at"))
    return since is not None and now - since <= REPLY_FRESH


def interrupt_candidates(cards: list[dict[str, Any]], logged: set[Pair],
                         now: datetime) -> list[dict[str, Any]]:
    """The cards that may interrupt now, in the feed's order.

    A card qualifies when its class is ``interrupt`` or ``urgent``, its pair
    is not in *logged*, it is open, and, for ``needs_reply``, the thread
    waits for 12 hours or less.
    """
    out = []
    for card in cards:
        if reach_class(card) not in REACHING or pair(card) in logged:
            continue
        if not is_open(card):
            continue
        if card.get("kind") == "needs_reply" and not _reply_fresh(card, now):
            continue
        out.append(card)
    return feed_rank.order(out, len(out)) if out else []


# ── When ────────────────────────────────────────────────────────────────────


def _day_span(day: Any, now: datetime) -> tuple[datetime, datetime, date]:
    """The start and the end of the member's current day, and its date."""
    local_day = day.feed_day(now)
    end_hour = day.end_hour if day.end_hour > day.start_hour else day.end_hour + 24
    return day.at(local_day, day.start_hour), day.at(local_day, end_hour), local_day


def in_quiet_hours(day: Any, now: datetime, working_day: bool) -> bool:
    """True outside the member's day hours, or on a day off."""
    if not working_day:
        return True
    start, end, _ = _day_span(day, now)
    return not (start <= now < end)


def brief_date(day: Any, now: datetime, working_day: bool) -> date | None:
    """The local date whose brief is due now, or None.

    The brief is due on a working day, from the day start for
    :data:`BRIEF_WINDOW_HOURS` hours.
    """
    if not working_day:
        return None
    start, end, local_day = _day_span(day, now)
    close = min(end, start + timedelta(hours=BRIEF_WINDOW_HOURS))
    return local_day if start <= now < close else None


# ── The words ───────────────────────────────────────────────────────────────


def _clean(value: Any, limit: int = TITLE_CHARS) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _age(since: datetime | None, now: datetime) -> str | None:
    if since is None:
        return None
    mins = max(0, int((now - since).total_seconds() // 60))
    if mins < 60:
        return f"{mins} min"
    hours = mins // 60
    if hours < 24:
        return f"{hours} h"
    return f"{hours // 24} d"


_NOTE_VERBS = {"assigned": "gave you", "mention": "mentioned you on",
               "nudge": "nudged you on"}


def line(card: dict[str, Any], now: datetime) -> str:
    """One line for one card: what the member must do, and one short fact."""
    kind = str(card.get("kind") or "")
    facts = card.get("facts") or {}
    title = _clean(card.get("title"))
    if kind == "needs_reply":
        who = _clean(facts.get("who"), 40)
        age = _age(_instant(facts.get("waiting_since") or card.get("at")), now)
        head = f"Reply to {who}: \"{title}\"" if who else f"Reply: \"{title}\""
        return f"{head} (waiting {age})" if age else head
    if kind == "approval":
        return f"Approve: {title}"
    if kind == "notification":
        who = _clean(facts.get("who"), 40) or "Somebody"
        verb = _NOTE_VERBS.get(str(facts.get("notification") or ""))
        return f"{who} {verb}: {title}" if verb else f"Update on: {title}"
    if kind == "overdue":
        late = facts.get("days_late")
        if isinstance(late, int) and not isinstance(late, bool) and late > 0:
            return f"Overdue: {title} ({late} day{'' if late == 1 else 's'} late)"
        return f"Overdue: {title}"
    if kind == "due_today":
        return f"Due today: {title}"
    if kind == "block_soon":
        mins = facts.get("minutes")
        return f"Starts in {mins} min: {title}" if mins is not None else f"Starts soon: {title}"
    if kind == "block_overlap":
        return f"Clash on your calendar: {title}"
    if kind == "day_overbooked":
        return f"Overbooked: {_clean(card.get('why'), 100)}"
    if kind == "no_plan_yet":
        return "Nothing is planned for today yet"
    return title


def _fit(head: str, lines: list[str], tail: str) -> str:
    """The message, with as many lines as fit under :data:`MAX_CHARS`."""
    kept: list[str] = []
    for item in lines:
        body = "\n".join([head, *kept, f"• {item}", "", tail])
        if len(body) > MAX_CHARS:
            break
        kept.append(f"• {item}")
    return "\n".join([head, *kept, "", tail])


def compose_interrupt(cards: list[dict[str, Any]], app_url: str, *,
                      now: datetime) -> str:
    """One batched interrupt for *cards*, which must not be empty."""
    tail = f"Reply here to act on any of these, or open My Day: {app_url.rstrip('/')}/"
    return _fit("*Needs you now*", [line(c, now) for c in cards], tail)


def _date_words(day: date) -> str:
    return f"{day:%a} {day.day} {day:%b}"


def compose_brief(cards: list[dict[str, Any]], app_url: str, local_date: date, *,
                  now: datetime) -> str | None:
    """The morning brief, or None when no open card is in Now or Needs you."""
    top = [c for c in cards if c.get("group") in BRIEF_GROUPS and is_open(c)]
    if not top:
        return None
    ordered = feed_rank.order(top, len(top))
    shown = ordered[:BRIEF_LINES]
    rest = len(ordered) - len(shown)
    tail = f"My Day: {app_url.rstrip('/')}/"
    if rest > 0:
        tail = f"+{rest} more. {tail}"
    return _fit(f"*Your day, {_date_words(local_date)}*",
                [line(c, now) for c in shown], tail)


# ── The dedupe key ──────────────────────────────────────────────────────────


def dedupe_key(kind: str, member: str, parts: Iterable[str]) -> str:
    """The outbox key of one plan. The same plan always gives the same key.

    A brief passes its local date. An interrupt passes its sorted
    ``signal_id:fingerprint`` pairs. The key is a hash, so it holds no title.
    """
    blob = "|".join([kind, member.strip().lower(), *sorted(parts)])
    return f"{kind}:{hashlib.sha256(blob.encode('utf-8')).hexdigest()[:40]}"


def interrupt_key(member: str, cards: list[dict[str, Any]]) -> str:
    return dedupe_key("interrupt", member, (f"{s}:{f}" for s, f in map(pair, cards)))


def brief_key(member: str, local_date: date) -> str:
    return dedupe_key("brief", member, [local_date.isoformat()])
