"""The reach class of each card kind (WS-59 RE-1a, ``proactive_reach.md`` §4.2).

A class says how far a card may reach the member:

* ``none`` — never leaves My Day.
* ``brief`` — goes into the morning brief.
* ``interrupt`` — may interrupt, inside the budget, and is in the brief too.
* ``urgent`` — reaches like ``interrupt``, and may use the urgent allowance.
  Only a "Tell me on WhatsApp" request is ``urgent`` today (RE-5, §11.3).

An unknown kind is ``none``, so a new provider reaches nobody until it adds
its row here. ``tests/unit/test_reach_plan.py`` fails when a kind that a
provider can give has no row.
"""

from __future__ import annotations

from typing import Any

#: The class of each kind. The CRM and sales kinds of MD-3c to MD-3f are
#: here before their providers land, so they reach WhatsApp on that day.
REACH_CLASS: dict[str, str] = {
    # The needs kinds.
    "needs_reply": "interrupt",
    "approval": "interrupt",
    "notification": "brief",  # interrupt when it assigns or mentions (below)
    "overdue": "brief",
    "due_today": "brief",
    # The Calendar kinds.
    "block_soon": "interrupt",
    "block_overlap": "brief",
    "day_overbooked": "brief",
    "no_plan_yet": "brief",
    # The planned CRM and sales kinds.
    "crm_followup_due": "interrupt",
    "deal_quiet": "interrupt",
    "lead_replied": "interrupt",
    "deal_stage_changed": "brief",
    "deal_close_passed": "brief",
    # The MD-3 kinds, as the table of proactive_reach.md §4.2 gives them.
    "no_answer": "brief",
    "promise_due": "brief",
    "unblocked": "brief",
    "colleague_out": "brief",
    "agent_asks": "interrupt",
    # §4.2 names no class for these three. Each one is a thing to know, not a
    # thing to act on now, so each one goes in the brief and interrupts nobody.
    "waiting_quiet": "brief",
    "stale_next": "brief",
    "absent_on_due": "brief",
    # "Tell me on WhatsApp" (RE-5, §11.3). A rule or a workflow at ``normal``
    # interrupts. The member's own request, and a rule at ``urgent``, are
    # ``urgent`` (below).
    "notify_me": "interrupt",
}

#: The notification reasons that interrupt. A comment or a change does not.
INTERRUPTING_NOTIFICATIONS = frozenset({"assigned", "mention"})

REACHING = frozenset({"interrupt", "urgent"})


#: The order of the classes, weakest first.
RANK = ("none", "brief", "interrupt", "urgent")


def _kind_class(kind: str, facts: dict[str, Any]) -> str:
    if kind == "notify_me" and (facts.get("self") is True
                                or facts.get("urgency") == "urgent"):
        return "urgent"
    if (kind == "notification"
            and str(facts.get("notification") or "") in INTERRUPTING_NOTIFICATIONS):
        return "interrupt"
    return REACH_CLASS.get(kind, "none")


def reach_kind(card: dict[str, Any]) -> str:
    """The kind on the card that reaches furthest: the main kind, or a kind
    folded into it. A mention folded under an overdue task still interrupts.
    A tie keeps the main kind."""
    facts = card.get("facts") or {}
    kinds = [str(card.get("kind") or ""), *(str(k) for k in card.get("folded") or ())]
    return max(kinds, key=lambda k: RANK.index(_kind_class(k, facts)))


def reach_class(card: dict[str, Any]) -> str:
    """The class of one card: the strongest of its main and folded kinds."""
    return _kind_class(reach_kind(card), card.get("facts") or {})
