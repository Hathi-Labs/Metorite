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
  A signal with no task level weighs 0. A card with ``promise_due`` on it
  adds 10 (MD-3d, §5.9 item 8).
* ``affinity`` is 0 until MD-9 builds it.
* ``fatigue`` is -8 for each visit of the day with no act, to -24 at most
  (MD-2a). Only the state knows the visits, so the state join subtracts it
  (``state.join``), after the fold and before the order.

The group comes from the old-path table of §4.3 until ``my_day.triage``
lands (MD-6a). A card takes the most urgent group of its main kind and of
every kind folded into it (``most_urgent``). Two of the four rules of §7 apply in MD-1a:

1. **The group comes first.** Inside a group, a card the member has not
   seen comes first (``state`` is ``new``, ``my_day_feed.md`` §6.8 item 6).
   Then the score orders the cards.
3. **Today's caps stay.** 15 cards for each app at most, and an approval
   takes only the room that the member's own work leaves (``YIELDING_KINDS``).

Rule 2 (a ``drop`` answer never hides a needs card) has no subject until
MD-6a, and rule 4 (the order holds while the member reads) is the client's
(MD-1b). Fence: ``tests/unit/test_feed_rank.py``.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, timedelta
from typing import Any

from acb_common.priority import CELL_META
from gateway.routes.shell.needs import (
    ASKING_NOTIFICATIONS,
    PER_APP,
    YIELDING_KINDS,
    _instant,
)

#: The groups, in the order the feed shows them (§4.3). MD-1 fills the first
#: four. MD-2a fills ``later`` (the state join), and MD-2c fills ``handled``.
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
    # The Calendar kinds of MD-1 (§5.1) and the MD-3 kinds (§5.9 item 2),
    # each below every needs kind. MD-3c adds the rows of all eleven MD-3
    # kinds at once, so MD-3d to MD-3f add only their providers. This table
    # is also the vocabulary of kinds that the prefs accept (``prefs.py``).
    "agent_asks": 43,
    "promise_due": 42,
    "crm_followup_due": 41,
    "block_overlap": 40,
    "absent_on_due": 39,
    "unblocked": 38,
    "waiting_quiet": 37,
    "block_soon": 36,
    "no_answer": 35,
    "deal_close_passed": 34,
    "deal_quiet": 33,
    "day_overbooked": 32,
    "stale_next": 30,
    "no_plan_yet": 28,
    "colleague_out": 26,
    # WS-53 CRM-Q28 item 14: the weekly CRM digest is ready. A thing to
    # know, so it sits below every MD-3 kind.
    "digest_ready": 24,
    # WS-53 CRM-Q32-4: the CRM holds a new question about the data of the
    # org, for a CRM admin. One card a day at most, below `digest_ready`.
    "crm_open_questions": 22,
}

#: The urgency terms (§7).
SOON = timedelta(hours=2)
URGENT_SOON = 40
URGENT_PASSED = 60
LATE_PER_DAY = 2
LATE_MAX = 20

#: The fatigue terms (§7): -8 for each visit with no act, to -24 at most.
FATIGUE_PER_VISIT = 8
FATIGUE_MAX = 24

#: The weight of one level (§7: "the level from 0 to 4, times 6").
WEIGHT_STEP = 6
#: The old path adds this for a promise on the card (§7, §5.9 item 8, MD-3d).
PROMISE_WEIGHT = 10
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


def weight(facts: dict[str, Any], kinds: Iterable[str] = ()) -> int:
    """The old path of ``my_day.weight``: the task's D78 level, times 6, and
    10 more when ``promise_due`` is one of ``kinds``, the main kind of the
    card or a folded one (§7, §5.9 item 8, MD-3d).

    ``kinds`` is empty by default, so a caller that asks only for the level
    (``feed_decide.old_level``) gets the level. A card with no promise gets
    the weight it had before MD-3d."""
    cell = facts.get("priority")
    level = LEVEL_BY_CELL[cell] * WEIGHT_STEP if cell in CELL_META else 0
    return level + (PROMISE_WEIGHT if "promise_due" in kinds else 0)


def affinity(_facts: dict[str, Any]) -> int:
    """0 until MD-9 learns from the member's acts."""
    return 0


def fatigue(visits: int) -> int:
    """8 for each visit of the day with no act, to 24 at most (§7)."""
    return min(FATIGUE_MAX, FATIGUE_PER_VISIT * max(0, int(visits)))


def score(kind: str, facts: dict[str, Any], urgent_at: Any, now: datetime,
          kinds: Iterable[str] = ()) -> int:
    """The score with no state: base + urgency + weight + affinity. The
    state join subtracts ``fatigue`` (``state.join``). ``kinds`` are every
    kind on the card, for the promise term of ``weight``."""
    return (base(kind) + urgency(kind, facts, urgent_at, now) + weight(facts, kinds)
            + affinity(facts))


def _is_day(value: Any, today: date) -> bool:
    return str(value or "") == today.isoformat()


def _due_within_soon(facts: dict[str, Any], now: datetime) -> bool:
    """The due time is 0 to 2 hours away: not passed, and not later."""
    due = _instant(facts.get("due_at"))
    return due is not None and timedelta(0) <= due - now <= SOON


def group(kind: str, facts: dict[str, Any], now: datetime, today: date) -> str:
    """The old-path group of a kind (§4.3, and §5.9 item 3 for MD-3).
    ``today`` is the member's own date. An unknown kind raises, as ``base``
    does."""
    if kind in ("overdue", "approval", "needs_reply", "no_plan_yet",
                "waiting_quiet", "absent_on_due", "no_answer", "deal_close_passed",
                "agent_asks"):
        return "needs_you"
    if kind == "unblocked":
        return "ready"
    if kind in ("stale_next", "deal_quiet", "colleague_out", "digest_ready"):
        return "fyi"
    if kind == "crm_open_questions":
        # CRM-Q32-4: needs you while a question about money is open.
        return "needs_you" if facts.get("money") is True else "fyi"
    if kind in ("promise_due", "crm_followup_due"):
        return "now" if _due_within_soon(facts, now) else "needs_you"
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


def _sort_key(card: dict[str, Any]) -> tuple[int, int, int, float, str]:
    # Rule 1: the group first. Then a new card before a seen one (§6.8 item
    # 6, §6.5 step 1). A card with no state yet reads as new. Then the higher
    # score, then the older time (a card with no time goes last), then the
    # id, so a tie has one order on every load.
    at = _instant(card.get("at"))
    unseen = 0 if card.get("state", "new") == "new" else 1
    return (GROUPS.index(card["group"]), unseen, -int(card["score"]),
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

