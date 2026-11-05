"""The exception engine v1 (WS-BRIEF BR-5, ``core_brief.md`` §6).

Between the brief and the close-out, a card reaches the member only when a
rule says so. Every other card waits in My Day for the next brief. The rules
are rows of code, beside ``classes.REACH_CLASS``. A member cannot add a rule.

**The four rules of v1** (DECIDED, ``core_brief.md`` §9 Q4):

1. ``approval_waiting``: an ``approval`` card that waits 2 hours or more.
2. ``agent_asks``: an agent asks the member a question.
3. ``block_soon_asked``: a ``block_soon`` card when the member asked to be
   told (``facts.notify_me``). ⚠️ No provider sets ``notify_me`` yet, so this
   rule never fires in v1. It is silent, which is the safe side. The setting
   is an open item of BR-5 slice 2.
4. ``source_sync_failed``: a source stopped syncing.

**What a rule tests.** Only the facts of the card, with ``==``, ``>=``,
``<=`` and ``in``. One derived fact is added before the test: ``waited_hours``
is the time from the card's ``at`` to *now*, because the approvals provider
gives ``at`` and no hours.

**The exception that is no rule.** A ``notify_me`` card is the member's own
"tell me on WhatsApp" request (RE-5). It is a command, so it always
qualifies (:data:`EXPLICIT`). Without this, the engine would refuse the
member's own request.

**The limits stay with Reach.** This module decides WHAT may interrupt. The
budget (3 a day, the batch window and quiet hours, ``proactive_reach.md``
§6.1) is unchanged, and it runs after this. A held card is never dropped: the
feed shows it, and the brief carries it.

**The switch** is ``BRIEF_EXCEPTIONS`` (``brief.flags.exceptions``), default
OFF. Off, ``plan.interrupt_candidates`` decides by the class table.

**The mute** (BR-5 slice 2a, built). A member mutes a rule with the
``my_day_prefs`` key ``rule_mutes`` (``routes/shell/prefs.py``). The loop and
the preview read it through ``loop.muted_rules`` and pass it as *muted*.

**Not in this slice.** The useful or not-useful rating of an alert, and the
``notify_me`` setting that rule 3 needs. Both stay open.

Fence: ``tests/unit/test_reach_exceptions.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

#: The test operators of ``when``.
OPERATORS: tuple[str, ...] = ("==", ">=", "<=", "in")
#: The actions of a rule. No match is ``hold``.
ACTIONS: tuple[str, ...] = ("interrupt", "hold")
#: The kinds that always qualify: the member's own request, not an exception.
EXPLICIT: frozenset[str] = frozenset({"notify_me"})


@dataclass(frozen=True)
class Rule:
    """One rule: a card kind, a test on its facts, and an action."""

    id: str
    kind: str
    when: dict[str, tuple[str, Any]] = field(default_factory=dict)
    action: str = "interrupt"
    reason: str = ""

    def __post_init__(self) -> None:
        if self.action not in ACTIONS:
            raise ValueError(f"action must be one of {ACTIONS}")
        for op, _ in self.when.values():
            if op not in OPERATORS:
                raise ValueError(f"operator must be one of {OPERATORS}")


@dataclass(frozen=True)
class Decision:
    """The answer for one card: ``interrupt`` or ``hold``, the rule that
    matched (None when no rule did), and its reason."""

    action: str
    rule_id: str | None = None
    reason: str = ""


RULES: tuple[Rule, ...] = (
    Rule(id="approval_waiting", kind="approval", when={"waited_hours": (">=", 2)},
         reason="An approval waits on you"),
    Rule(id="agent_asks", kind="agent_asks", reason="An agent asks you a question"),
    Rule(id="block_soon_asked", kind="block_soon", when={"notify_me": ("==", True)},
         reason="A block starts soon, and you asked to be told"),
    Rule(id="source_sync_failed", kind="source_sync_failed",
         reason="A source stopped syncing"),
)

HOLD = Decision("hold")


def _instant(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def facts_of(card: dict[str, Any], now: datetime) -> dict[str, Any]:
    """The facts a rule tests: the card's own, and ``waited_hours`` from its
    ``at``. A card with no readable ``at`` has no ``waited_hours``, so a
    test on it fails and the card holds. Pure."""
    facts = dict(card.get("facts") or {})
    at = _instant(card.get("at"))
    if at is not None and at.tzinfo is not None and now.tzinfo is not None:
        facts["waited_hours"] = max(0.0, (now - at).total_seconds() / 3600)
    return facts


def _holds(test: tuple[str, Any], value: Any) -> bool:
    op, want = test
    if value is None:
        return False
    try:
        if op == "==":
            return bool(value == want)
        if op == ">=":
            return bool(value >= want)
        if op == "<=":
            return bool(value <= want)
        return value in want
    except TypeError:
        return False


def _kinds(card: dict[str, Any]) -> list[str]:
    return [str(card.get("kind") or ""), *(str(k) for k in card.get("folded") or ())]


def decide(card: dict[str, Any], now: datetime, *,
           muted: frozenset[str] = frozenset(),
           rules: tuple[Rule, ...] = RULES) -> Decision:
    """The decision for *card*. The first rule that matches the main kind or
    a folded kind and is not in *muted* wins. A rule with action ``hold``
    holds. No match holds. Pure."""
    facts = facts_of(card, now)
    kinds = _kinds(card)
    for rule in rules:
        if rule.id in muted or rule.kind not in kinds:
            continue
        if all(_holds(test, facts.get(name)) for name, test in rule.when.items()):
            return Decision(rule.action, rule.id, rule.reason)
    return HOLD


def may_interrupt(card: dict[str, Any], now: datetime, *,
                  muted: frozenset[str] = frozenset()) -> bool:
    """True when *card* may enter the interrupt budget: an explicit request,
    or a rule that says ``interrupt``."""
    if str(card.get("kind") or "") in EXPLICIT:
        return True
    return decide(card, now, muted=muted).action == "interrupt"
