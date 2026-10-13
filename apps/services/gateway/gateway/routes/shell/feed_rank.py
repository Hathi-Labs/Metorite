"""The order of the My Day feed (``my_day_feed.md`` §7, WS-58 MD-1a).

The order is a score. Code owns time, and ``decide`` owns judgment::

    score = base(kind) + urgency + weight + affinity - fatigue

* ``base(kind)`` is the one table below. The five needs kinds start above
  every other kind, and among them the order is the needs feed's own
  (``needs.KIND_ORDER``). The fold of §4.5 reads it too: when one entity
  gives several kinds, the kind with the highest base is the card's kind.
* ``urgency`` grows as ``urgent_at`` comes near: +40 inside 2 hours, +60 when
  it passed. An overdue task adds 2 for each day late, to 20 at most. A
  model never does date arithmetic.
* ``weight`` is the OLD PATH until MD-6a: the task's D78 level, from the one
  server matrix (``acb_common.priority``), as a level from 0 to 4, times 6.
  A signal with no task level weighs 0.
* ``affinity`` and ``fatigue`` are 0 until MD-9 and MD-2 build them.

The group comes from the old-path table of §4.3 until ``my_day.triage``
lands (MD-6a). A card takes the most urgent group of its main kind and of
every kind folded into it (``most_urgent``). Two of the four rules of §7 apply in MD-1a:

1. **The group comes first.** The score orders the cards inside a group.
3. **Today's caps stay.** 15 cards for each app at most, and an approval
   takes only the room that the member's own work leaves (``YIELDING_KINDS``).

Rule 2 (a ``drop`` answer never hides a needs card) has no subject until
MD-6a, and rule 4 (the order holds while the member reads) is the client's
(MD-1b). Fence: ``tests/unit/test_feed_rank.py``.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from acb_common.priority import CELL_META
from gateway.routes.shell.needs import (
    ASKING_NOTIFICATIONS,
    PER_APP,
    YIELDING_KINDS,
    _instant,
)

#: The groups, in the order the feed shows them (§4.3). MD-1 fills only the
#: first four. ``later`` waits on MD-2, and ``handled`` on MD-7.
GROUPS: tuple[str, ...] = ("now", "needs_you", "ready", "fyi", "later", "handled")

#: base(kind). ⚠️ The fixed seed of ``test_feed_rank.py`` holds this table: a
#: change here changes the order, and the seed must change with it.
BASE: dict[str, int] = {
    # The needs kinds, in the needs feed's own order (KIND_ORDER).
    "overdue": 60,
    "approval": 56,
    "due_today": 52,
    "notification": 48,
    "needs_reply": 44,
    # The Calendar kinds of MD-1 (§5.1), below every needs kind.
    "block_overlap": 40,
    "block_soon": 36,
    "day_overbooked": 32,
    "no_plan_yet": 28,
}

#: The urgency terms (§7).
SOON = timedelta(hours=2)
URGENT_SOON = 40
URGENT_PASSED = 60
LATE_PER_DAY = 2
LATE_MAX = 20

#: The weight of one level (§7: "the level from 0 to 4, times 6").
WEIGHT_STEP = 6
#: The D78 level of each cell, mapped onto 0 to 4 by its rank:
#: ``round((7 - rank) * 4 / 6)``. Rank 1 (Critical) is 4, and rank 7 (Low
#: Priority) is 0. Written out so the table reads at a glance.
LEVEL_BY_CELL: dict[str, int] = {
    "critical": 4,
    "urgent": 3,
    "high-leverage": 3,
    "important": 2,
    "quick-leverage": 1,
    "speculative-bet": 1,
    "low-priority": 0,
}



def base(kind: str) -> int:
    """The fixed number of a kind. An unknown kind raises, so a provider
    that adds a kind must add its row here in the same change."""
    return BASE[kind]


def urgency(kind: str, facts: dict[str, Any], urgent_at: Any, now: datetime) -> int:
    """+40 inside 2 hours of ``urgent_at``, +60 once it passed, plus 2 for
    each day an overdue task is late, to 20 at most."""
    points = 0
    at = _instant(urgent_at)
    if at is not None:
        if at <= now:
            points = URGENT_PASSED
        elif at - now <= SOON:
            points = URGENT_SOON
    if kind == "overdue":
        late = facts.get("days_late")
        if isinstance(late, int) and not isinstance(late, bool) and late > 0:
            points += min(LATE_MAX, LATE_PER_DAY * late)
    return points


def weight(facts: dict[str, Any]) -> int:
    """The old path of ``my_day.weight``: the task's D78 level, times 6."""
    cell = facts.get("priority")
    if cell not in CELL_META:
        return 0
    return LEVEL_BY_CELL[cell] * WEIGHT_STEP


def affinity(_facts: dict[str, Any]) -> int:
    """0 until MD-9 learns from the member's acts."""
    return 0


def fatigue(_facts: dict[str, Any]) -> int:
    """0 until MD-2 records the visits with no act."""
    return 0


def score(kind: str, facts: dict[str, Any], urgent_at: Any, now: datetime) -> int:
    return (base(kind) + urgency(kind, facts, urgent_at, now) + weight(facts)
            + affinity(facts) - fatigue(facts))


def _is_day(value: Any, today: date) -> bool:
    return str(value or "") == today.isoformat()


def group(kind: str, facts: dict[str, Any], now: datetime, today: date) -> str:
    """The old-path group of an MD-1 kind (§4.3). ``today`` is the member's
    own date. An unknown kind raises, as ``base`` does."""
    if kind in ("overdue", "approval", "needs_reply", "no_plan_yet"):
        return "needs_you"
    if kind == "due_today":
        due = _instant(facts.get("due_at"))
        return "now" if due is not None and due - now <= SOON else "needs_you"
    if kind == "notification":
        return "needs_you" if facts.get("notification") in ASKING_NOTIFICATIONS else "fyi"
    if kind == "block_soon":
        return "now"
    if kind == "block_overlap":
        return "now" if _is_day(facts.get("overlap_day"), today) else "needs_you"
    if kind == "day_overbooked":
        return "needs_you" if _is_day(facts.get("day"), today) else "fyi"
    raise KeyError(kind)


def most_urgent(groups: list[str]) -> str:
    """The most urgent of some groups, in the order now > needs_you > ready >
    fyi (§4.5). A card takes the most urgent group of all its kinds, so a
    folded block that starts soon keeps the card in Now."""
    return min(groups, key=GROUPS.index)


def _sort_key(card: dict[str, Any]) -> tuple[int, int, float, str]:
    # Rule 1: the group first. Then the higher score, then the older time
    # (a card with no time goes last), then the id, so a tie has one order
    # on every load.
    at = _instant(card.get("at"))
    return (GROUPS.index(card["group"]), -int(card["score"]),
            at.timestamp() if at is not None else float("inf"), str(card["signal_id"]))


def order(cards: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """The feed's order and cut (§7 rules 1 and 3).

    Each app gives ``PER_APP`` cards at most, its best first. An approval
    takes only the room the member's own cards leave inside ``limit``, the
    rule of ``needs._fit``.
    """
    per_app: dict[str, int] = {}
    kept: list[dict[str, Any]] = []
    for card in sorted(cards, key=lambda c: (-int(c["score"]), _sort_key(c))):
        seen = per_app.get(card["app"], 0)
        if seen >= PER_APP:
            continue
        per_app[card["app"]] = seen + 1
        kept.append(card)
    own = sorted((c for c in kept if c["kind"] not in YIELDING_KINDS), key=_sort_key)[:limit]
    rest = sorted((c for c in kept if c["kind"] in YIELDING_KINDS),
                  key=_sort_key)[:max(0, limit - len(own))]
    return sorted(own + rest, key=_sort_key)

