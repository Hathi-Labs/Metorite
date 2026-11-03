"""The @mentions of a text: who it tells (WS-27j, RX-1).

THE parser. The gateway reads it to notify each person that a comment or a
description names (``gateway/routes/projects/notifications.py``). The Projects
tools read it before their card, so the card names the same people that the
write tells. A second parser would let a card and its write disagree.
Fence: ``tests/unit/test_held_card_writes.py``.

An address is the key. Names stopped being unique in migration 148, so ``@Priya``
has no answer, and picking one of two Priyas would notify the wrong person. The
UI's picker inserts the address, so nobody types this by hand.
"""
from __future__ import annotations

import re

__all__ = ["MENTION", "mention_targets", "new_mentions"]

MENTION = re.compile(r"@([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})")


def mention_targets(body: str | None) -> list[str]:
    """Addresses @mentioned in a comment, lowercased, in first-seen order.

    Order is preserved and duplicates dropped so a comment that names somebody
    twice produces one notification, and so the *skipped* list a caller gets
    back reads in the order they wrote it.
    """
    seen: dict[str, None] = {}
    for match in MENTION.finditer(body or ""):
        seen.setdefault(match.group(1).lower(), None)
    return list(seen)


def new_mentions(old_body: str | None, new_body: str | None) -> list[str]:
    """Only the addresses an edit ADDED, in first-seen order (WS-27v).

    Editing a comment or description must notify the *newly named* people and
    nobody twice: without this diff, fixing a typo in a comment that mentions
    three colleagues re-pings all three, and a bell that repeats itself is a
    bell people mute. Set-differenced on the folded address, the same identity
    every other mention read uses (R10).
    """
    before = set(mention_targets(old_body))
    return [who for who in mention_targets(new_body) if who not in before]
