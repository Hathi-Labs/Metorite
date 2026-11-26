"""The sentences of "Why am I seeing this" (WS-58 MD-9, LOOP-4b, task 4006).

Spec: ``project-docs/specs/proactive_loop.md`` §7.2, the pins W1 to W5, and
the sentence table of W3.

**Code writes each sentence.** No model runs and no query runs. A sentence
holds no title, no name and no other text from a record. The one value it
takes from a card is a count or the label of a priority cell. Each sentence
shows only when its rule is true.

**One caller.** ``feed.feed_answer`` adds ``why_seen`` to each card of its
answer, after the order and the cut, and only for an org in
``MY_DAY_LEARN_ORGS`` whose state read and learned read both succeeded (W1).
``feed.feed_cards`` adds no key, so the reach loop, the briefs and the chat
brief see none.

The order of the list is the order of the table: the group, the time, the
priority, the promise, what the feed learned, then the visits of the day.

Fence: ``tests/unit/test_feed_why.py`` holds one pass case and one fail case
for each row of W3.
"""

from __future__ import annotations

from collections.abc import Mapping, Set
from datetime import datetime
from typing import Any

from acb_common.priority import CELL_META
from gateway.routes.shell import feed_rank, state
from gateway.routes.shell.needs import _instant

#: Rows 1a to 1d: the groups that the join of the state does not set.
GROUP_SENTENCES: dict[str, str] = {
    "now": "It is in Now, because a time on it is close or has passed, or it overlaps today.",
    "needs_you": "It is in Needs you, because it asks you to act.",
    "ready": "It is in Ready for you, because a task that blocked it closed.",
    "fyi": "It is in For your information, which comes after the cards that need you.",
}
#: Row 1e: a muted needs card (P3).
LATER_MUTED = ("It is in Later because you muted this kind. It needs you, so it stays "
               "in the feed.")
#: Row 1f: any other card of Later.
LATER_SEEN = "It is in Later, because you saw it 3 times in one day."
#: Rows 2b and 2c.
TIME_PASSED = "Its start or due time has passed."
TIME_SOON = "Its start or due time is 2 hours away or less."
#: Row 4.
PROMISE = "It holds a promise that you made."
#: Rows 5a to 5c.
RANKS_HIGHER = ('In the last 30 days you acted on cards of this kind more than you marked '
                'them "Not useful", so it ranks higher.')
RANKS_LOWER = ('In the last 30 days you marked cards of this kind "Not useful" more than '
               'you acted on them, so it ranks lower.')
NOT_LOWERED = ('You marked cards of this kind "Not useful". This card needs you, so those '
               'marks do not lower it.')


def _kinds(card: Mapping[str, Any]) -> set[Any]:
    """The main kind of the card, and each kind folded into it."""
    return {card.get("kind"), *(card.get("folded") or ())}


def _group_line(card: Mapping[str, Any], taught: state.Learned) -> str | None:
    """Row 1. Only two rules put a card in Later: a mute of a needs card, and
    three visits of one day (``state.join``)."""
    from gateway.routes.shell import feed_decide  # feed_decide imports feed

    group = card.get("group")
    if group != "later":
        return GROUP_SENTENCES.get(str(group))
    muted = card.get("kind") in taught.mutes and feed_decide.is_needs_card(card)
    return LATER_MUTED if muted else LATER_SEEN


def _time_line(card: Mapping[str, Any], now: datetime) -> str | None:
    """Rows 2a to 2c. One of them at most: the days late of an overdue card
    first, else the state of ``urgent_at``."""
    late = (card.get("facts") or {}).get("days_late")
    if ("overdue" in _kinds(card) and isinstance(late, int) and not isinstance(late, bool)
            and late >= 1):
        return f"It is {late} {'day' if late == 1 else 'days'} late."
    urgent = _instant(card.get("urgent_at"))
    if urgent is None:
        return None
    if urgent <= now:
        return TIME_PASSED
    return TIME_SOON if urgent - now <= feed_rank.SOON else None


def _priority_line(card: Mapping[str, Any]) -> str | None:
    """Row 3: a priority cell whose level adds to the score."""
    cell = (card.get("facts") or {}).get("priority")
    if not isinstance(cell, str) or feed_rank.LEVEL_BY_CELL.get(cell, 0) <= 0:
        return None
    return f"Its priority is {CELL_META[cell][2]}."


def _learned_line(card: Mapping[str, Any], taught: state.Learned) -> str | None:
    """Rows 5a to 5c: the affinity of the main kind. The join holds the term
    of a needs card at 0 or more (P3), so its negative term lowers nothing."""
    from gateway.routes.shell import feed_decide  # feed_decide imports feed

    term = taught.affinity.get(str(card.get("kind")), 0)
    if term > 0:
        return RANKS_HIGHER
    if term < 0:
        return NOT_LOWERED if feed_decide.is_needs_card(card) else RANKS_LOWER
    return None


def _visits_line(card: Mapping[str, Any], acted: Set[str]) -> str | None:
    """Row 6: the fatigue of the day. An acted card takes none
    (``state.join``), so it says nothing."""
    visits = int(card.get("visits") or 0)
    if visits < 1 or card.get("signal_id") in acted:
        return None
    return (f"You saw it {visits} {'time' if visits == 1 else 'times'} today, so it ranks "
            "a little lower.")


def explain(card: Mapping[str, Any], taught: state.Learned, acted: Set[str],
            now: datetime) -> list[str]:
    """Why one card of the answer is where it is, as the sentences of W3.

    *card* is a card after the state join. *taught* is what the join learned,
    and *acted* holds the signal ids whose state row is ``acted``
    (``state.Applied``). *now* is the clock of the run. The function changes
    nothing.
    """
    lines = (
        _group_line(card, taught),
        _time_line(card, now),
        _priority_line(card),
        PROMISE if "promise_due" in _kinds(card) else None,
        _learned_line(card, taught),
        _visits_line(card, acted),
    )
    return [line for line in lines if line]
