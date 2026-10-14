"""The reach class of each card kind (WS-59 RE-1a, ``proactive_reach.md`` §4.2).

A class says how far a card may reach the member:

* ``none`` — never leaves My Day.
* ``brief`` — goes into the morning brief.
* ``interrupt`` — may interrupt, inside the budget, and is in the brief too.
* ``urgent`` — reserved for a later slice. It reaches like ``interrupt``.

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
}

#: The notification reasons that interrupt. A comment or a change does not.
INTERRUPTING_NOTIFICATIONS = frozenset({"assigned", "mention"})

REACHING = frozenset({"interrupt", "urgent"})


def reach_class(card: dict[str, Any]) -> str:
    """The class of one card, from its main kind and its facts."""
    kind = str(card.get("kind") or "")
    cls = REACH_CLASS.get(kind, "none")
    if kind == "notification":
        facts = card.get("facts") or {}
        if str(facts.get("notification") or "") in INTERRUPTING_NOTIFICATIONS:
            return "interrupt"
    return cls
