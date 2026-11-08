"""The cool-down of the ``decide`` tier, shared by the two callers (DEC-1).

Two seams ask ``acb_llm.decide`` for a pick: ``gateway/decide_features.py``
(modes, org lists, shadow) and ``acb_skills.decide_tools.ask_typed`` (the
typed choices inside a skill's tool). A skill may not import the gateway
(``packages/acb_skills/AGENTS.md``). So the logic that BOTH use lives here,
one copy, in a package that both may import.

After a refused verdict, no call goes to the Router for the organization for
:data:`REFUSAL_COOLDOWN_S`. Each caller keeps its own log line. This module
logs nothing and reads no settings.

**Areas.** The email, My Day and CRM features share ONE table, as before
DEC-1: a 402 there means no credit, and those apps wait together. An area in
:data:`ISOLATED_AREAS` has its own table. ``projects.`` is the first: a
verdict on a Projects call pauses only Projects calls, and a verdict on an
email, My Day or CRM call never pauses them. So the typed choices of a skill
(area ``projects.``) read and write only their own table. Only a 402 starts
the cool-down of an isolated area.

The clock is a parameter. A caller that tests a window passes ``at``.
"""
from __future__ import annotations

import time

__all__ = [
    "COOLDOWN_STATUSES",
    "COOLDOWN_UNTIL",
    "ISOLATED_AREAS",
    "ISOLATED_UNTIL",
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

#: Organization id → the monotonic time at which its cool-down ends, for the
#: email, My Day and CRM features (they share it, as before DEC-1).
COOLDOWN_UNTIL: dict[str, float] = {}

#: The areas (feature prefixes) that keep a table of their own.
ISOLATED_AREAS: tuple[str, ...] = ("projects.",)

#: Area → organization id → the monotonic time at which its cool-down ends.
ISOLATED_UNTIL: dict[str, dict[str, float]] = {a: {} for a in ISOLATED_AREAS}


def _table(area: str) -> dict[str, float]:
    return ISOLATED_UNTIL[area] if area in ISOLATED_UNTIL else COOLDOWN_UNTIL


def now() -> float:
    """The clock of the cool-down."""
    return time.monotonic()


def cooling(org: object, *, at: float | None = None, area: str = "") -> bool:
    """Whether ``org`` is in its cool-down for ``area``. No org is never cooling.

    ``area`` is a feature prefix such as ``projects.``. Any area that is not
    in :data:`ISOLATED_AREAS` reads the shared table.
    """
    when = now() if at is None else at
    return bool(org) and _table(area).get(str(org), 0.0) > when


def start_cooldown(org: object, *, at: float | None = None, area: str = "") -> bool:
    """Start the cool-down of ``org`` for ``area``. False, and no effect, with no org."""
    if not org:
        return False
    when = now() if at is None else at
    _table(area)[str(org)] = when + REFUSAL_COOLDOWN_S
    return True


def starts_cooldown(status: int | None, *, only_402: bool = False) -> bool:
    """Whether a verdict with ``status`` starts a cool-down."""
    return status in (ONLY_402 if only_402 else COOLDOWN_STATUSES)
