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

from gateway.reach.classes import REACHING, reach_class, reach_kind
from gateway.routes.shell import feed_rank
from gateway.routes.shell.needs import _instant

#: A needs-reply card interrupts only while the thread is this fresh.
REPLY_FRESH = timedelta(hours=12)
#: A brief goes out as the template only in the first hours of the member's
#: day. As free text it may go at any hour of the day (RE-2a).
BRIEF_WINDOW_HOURS = 3
#: Lines of the brief, before the count of the rest.
BRIEF_LINES = 7
#: The most characters of one message.
MAX_CHARS = 1000
#: The most characters of a title inside a line.
TITLE_CHARS = 80
#: The most characters of a request's title inside a batched line (RE-5).
REQUEST_TITLE_CHARS = 200
#: The most characters of a request's body inside a batched message (RE-5).
REQUEST_BODY_CHARS = 300
#: The most characters of a message that is one request of the member's own:
#: the title of 300 and the body of 1,000 fit, with the link (RE-5). WhatsApp
#: takes 4,096.
SELF_REQUEST_CHARS = 1600
#: The most characters of one template parameter.
PARAM_CHARS = 160
#: A template parameter is never empty. Meta refuses an empty one.
EMPTY_PARAM = "\u2013"  # an en dash

#: The card states that may reach. The state join already drops a card that
#: is snoozed or dismissed. ``later`` is the member's own "not now".
OPEN_STATES = frozenset({"new", "seen"})
#: The groups of the brief.
BRIEF_GROUPS = ("now", "needs_you")

Pair = tuple[str, str]


# ── Which cards reach ───────────────────────────────────────────────────────


def reach_print(card: dict[str, Any]) -> str:
    """What makes this card a NEW reason to reach the member.

    Not the feed's fingerprint: that one moves on a fold or a later comment,
    so the same mention would interrupt again (review of RE-1a, P2). Only a
    new ask moves this one: a new asking note, a new message in the thread,
    a new block time.
    """
    kind = reach_kind(card)
    facts = card.get("facts") or {}
    if kind == "notification":
        parts = [kind, str(facts.get("notification") or ""), str(facts.get("at") or "")]
    elif kind == "needs_reply":
        parts = [kind, str(facts.get("message_id") or card.get("at") or "")]
    else:
        parts = [kind, str(card.get("at") or card.get("urgent_at") or "")]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


def pair(card: dict[str, Any]) -> Pair:
    """The identity of one card at one reason to reach (:func:`reach_print`)."""
    return str(card["signal_id"]), reach_print(card)


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

    The brief is due on a working day, from the day start until the day end
    (RE-2a). So a member who first writes to the bot at 14:00 gets the brief
    then. The loop sends it once for each date.
    """
    if not working_day:
        return None
    start, end, local_day = _day_span(day, now)
    return local_day if start <= now < end else None


def brief_morning(day: Any, now: datetime) -> bool:
    """True in the first :data:`BRIEF_WINDOW_HOURS` hours of the member's day.
    Only then may a brief go as the template."""
    start, end, _ = _day_span(day, now)
    return start <= now < min(end, start + timedelta(hours=BRIEF_WINDOW_HOURS))


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


def line(card: dict[str, Any], now: datetime, *, kind: str | None = None) -> str:
    """One line for one card: what the member must do, and one short fact.
    *kind* names the kind to speak of, when it is not the main one."""
    kind = kind or str(card.get("kind") or "")
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
    if kind == "notify_me":
        return _clean(card.get("title"), REQUEST_TITLE_CHARS)
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


def is_self_request(card: dict[str, Any]) -> bool:
    """True for a "Tell me on WhatsApp" card that the member asked for now."""
    return card.get("kind") == "notify_me" and (card.get("facts") or {}).get("self") is True


def compose_self_request(card: dict[str, Any], app_url: str) -> str:
    """A message of one request of the member's own: the title, the body and
    the link. The member asked for this text, so it goes whole (RE-5)."""
    title = _clean(card.get("title"), 300)
    parts = [f"*{title}*"]
    body = str((card.get("facts") or {}).get("body") or "").strip()
    if body:
        parts.append(body)
    href = str(card.get("href") or "")
    if href.startswith("/"):
        parts.append(f"{app_url.rstrip('/')}{href}")
    text = "\n\n".join(parts)
    return text if len(text) <= SELF_REQUEST_CHARS else text[:SELF_REQUEST_CHARS - 1] + "…"


def compose_interrupt(cards: list[dict[str, Any]], app_url: str, *,
                      now: datetime) -> str:
    """One batched interrupt for *cards*, which must not be empty.

    A message of exactly one request of the member's own is that request:
    its title and body (RE-5). In a batch, the one request of the member's
    own carries its body after the title, cut to a short line."""
    selfs = [c for c in cards if is_self_request(c)]
    if len(cards) == 1 and selfs:
        return compose_self_request(cards[0], app_url)
    lines = []
    for c in cards:
        text = line(c, now, kind=reach_kind(c))
        body = (c.get("facts") or {}).get("body")
        if len(selfs) == 1 and c is selfs[0] and body:
            text = f"{text}: {_clean(body, REQUEST_BODY_CHARS)}"
        lines.append(text)
    tail = f"Reply here to act on any of these, or open My Day: {app_url.rstrip('/')}/"
    return _fit("*Needs you now*", lines, tail)


def _date_words(day: date) -> str:
    return f"{day:%a} {day.day} {day:%b}"


def brief_items(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The open cards of Now and Needs you, in the feed's order."""
    top = [c for c in cards if c.get("group") in BRIEF_GROUPS and is_open(c)]
    return feed_rank.order(top, len(top)) if top else []


def compose_brief(cards: list[dict[str, Any]], app_url: str, local_date: date, *,
                  now: datetime) -> str | None:
    """The morning brief, or None when no open card is in Now or Needs you."""
    ordered = brief_items(cards)
    if not ordered:
        return None
    shown = ordered[:BRIEF_LINES]
    rest = len(ordered) - len(shown)
    tail = f"My Day: {app_url.rstrip('/')}/"
    if rest > 0:
        tail = f"+{rest} more. {tail}"
    return _fit(f"*Your day, {_date_words(local_date)}*",
                [line(c, now) for c in shown], tail)


# ── The template parameters (RE-2a) ─────────────────────────────────────────


def template_param(value: Any, limit: int = PARAM_CHARS) -> str:
    """One body parameter of a Meta template.

    Meta refuses a newline, a tab, more than four spaces in a row, and an
    empty value. So the text becomes one line with single spaces, at most
    *limit* characters with an ellipsis, and never empty.
    """
    return _clean(value, limit) or EMPTY_PARAM


def first_name(display_name: str | None, email: str) -> str:
    """The name the templates greet: the first word of the display name, or
    the first part of the email's local part, with a capital. Never empty."""
    words = str(display_name or "").split()
    if words:
        return template_param(words[0], 40)
    local = str(email or "").split("@", 1)[0]
    for part in local.replace("_", ".").replace("-", ".").split("."):
        if part.strip():
            return template_param(part.strip().capitalize(), 40)
    return "there"


def needs_you_params(name: str, cards: list[dict[str, Any]], now: datetime) -> list[str]:
    """The parameters of the needs-you template: the name, then the first
    card's line and the count of the rest. *cards* must not be empty."""
    rest = len(cards) - 1
    tail = f" (and {rest} more)" if rest > 0 else ""
    head = template_param(line(cards[0], now, kind=reach_kind(cards[0])),
                          PARAM_CHARS - len(tail))
    return [template_param(name, 40), head + tail]


def brief_params(name: str, cards: list[dict[str, Any]], local_date: date,
                 now: datetime) -> list[str]:
    """The parameters of the brief template: the name, the date, the count of
    brief items and the first line. *cards* must hold a brief item."""
    items = brief_items(cards)
    return [template_param(name, 40), _date_words(local_date), str(len(items)),
            template_param(line(items[0], now))]


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


def full_brief_key(member: str, local_date: date) -> str:
    """The key of the full brief that follows a template brief (RE-2a)."""
    return brief_key(member, local_date) + ":full"
