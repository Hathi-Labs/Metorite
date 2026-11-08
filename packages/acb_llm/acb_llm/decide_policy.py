"""The cool-down of the ``decide`` tier, shared by the two callers (DEC-1).

Two seams ask ``acb_llm.decide`` for a pick: ``gateway/decide_features.py``
(modes, org lists, shadow) and ``acb_skills.decide_tools.ask_typed`` (the
typed choices inside a skill's tool). A skill may not import the gateway
(``packages/acb_skills/AGENTS.md``). So the state that BOTH must honour lives
here, one copy, in a package that both may import.

After a refused verdict (402 no credit, 403 forbidden), no call goes to the
Router for the organization for :data:`REFUSAL_COOLDOWN_S`. Each caller keeps
its own log line. This module logs nothing and reads no settings.

The clock is a parameter. A caller that tests a window passes ``at``.
"""
from __future__ import annotations

import time

__all__ = [
    "COOLDOWN_STATUSES",
    "COOLDOWN_UNTIL",
    "ONLY_402",
    "REFUSAL_COOLDOWN_S",
    "cooling",
    "now",
    "start_cooldown",
    "starts_cooldown",
]

#: After a 402 or a 403 verdict, an organization makes no Router call for this
#: long, in seconds. The gateway runs as ONE process, so an in-process map is
#: the whole state of that process.
REFUSAL_COOLDOWN_S: float = 15 * 60

#: The verdicts that start a cool-down for the email features.
COOLDOWN_STATUSES = frozenset({402, 403})

#: The verdicts that start it for an app that is not email. A 403 there can
#: name that app's grant, so it must not pause the other apps of the org.
ONLY_402 = frozenset({402})

#: Organization id → the monotonic time at which its cool-down ends.
COOLDOWN_UNTIL: dict[str, float] = {}


def now() -> float:
    """The clock of the cool-down."""
    return time.monotonic()


def cooling(org: object, *, at: float | None = None) -> bool:
    """Whether ``org`` is in its cool-down. No organization is never cooling."""
    when = now() if at is None else at
    return bool(org) and COOLDOWN_UNTIL.get(str(org), 0.0) > when


def start_cooldown(org: object, *, at: float | None = None) -> bool:
    """Start the cool-down of ``org``. False, and no effect, with no org."""
    if not org:
        return False
    when = now() if at is None else at
    COOLDOWN_UNTIL[str(org)] = when + REFUSAL_COOLDOWN_S
    return True


def starts_cooldown(status: int | None, *, only_402: bool = False) -> bool:
    """Whether a verdict with ``status`` starts a cool-down."""
    return status in (ONLY_402 if only_402 else COOLDOWN_STATUSES)
