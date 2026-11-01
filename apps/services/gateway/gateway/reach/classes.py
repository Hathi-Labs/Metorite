"""The reach class of each card kind (WS-59 RE-1a, ``proactive_reach.md`` §4.2).

A class says how far a card may reach the member:

* ``none`` — never leaves My Day.
* ``brief`` — goes into the morning brief.
* ``interrupt`` — may interrupt, inside the budget, and is in the brief too.
* ``urgent`` — reaches like ``interrupt``, and may use the urgent allowance.
  A "Tell me on WhatsApp" request can be ``urgent`` (RE-5, §11.3), and so is
  a due reminder on web push (WS-66 RE-9b).

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
    # WS-53 CRM-Q28 item 14: the weekly CRM digest. It never leaves My Day.
    # Delivery outside the app is CRM-Q28b, an owner gate.
    "digest_ready": "none",
    # WS-53 CRM-Q32-4: the open questions of the CRM, for a CRM admin. The
    # admin answers them in CRM settings, so the card never leaves My Day.
    "crm_open_questions": "none",
    # WS-61 KB-9a (knowledge_base.md §9): a question that Knowledge could not
    # answer. A thing to know for a manager, so it goes in the brief.
    "kb_gap": "brief",
    # WS-61 KB-9b (knowledge_base.md §9): a page past its review date, and a
    # page that another author published. Each is a thing to know, so each
    # goes in the brief and interrupts nobody.
    "doc_needs_review": "brief",
    "doc_shared_with_me": "brief",
    # KB-9b slice 2: a source stopped syncing, for the member who connected it
    # (only that member gets the card). It may interrupt, inside the budget.
    "source_sync_failed": "interrupt",
    # WS-66 RE-9b (calendar_events_reminders.md §5.6, decision 3): the
    # member's own reminders reach the member by web push only
    # (`reach/push_channel.py`). `reminder_due` is `urgent`: it may break
    # quiet hours, with its own cap of 10 a day (D114.3). `reminder_check` is
    # `interrupt`, inside the interrupt cap and never in quiet hours.
    # `reminder_unconfirmed` is `brief`, so it sends no push in quiet hours.
    # ⚠️ A class does not keep a card off WhatsApp. `NOT_ON_WHATSAPP` below
    # does that, so the Reach loop never sends a reminder (D114.7).
    "reminder_due": "urgent",
    "reminder_check": "interrupt",
    "reminder_unconfirmed": "brief",
    # "Tell me on WhatsApp" (RE-5, §11.3). A rule or a workflow at ``normal``
    # interrupts. The member's own request, and a rule at ``urgent``, are
    # ``urgent`` (below).
    "notify_me": "interrupt",
}

#: The kinds that never go on WhatsApp: no brief, no template parameter, no
#: interrupt and no preview names them (D114.7). They are the member's own
#: reminders, which can hold private text, and RM-9 lifts this. The chat
#: brief (``routes/chat_brief.py``) still shows them: the chat is a channel
#: that D114.7 allows. ``plan.on_whatsapp`` applies it, and
#: ``tests/unit/test_reach_loop.py`` holds it.
NOT_ON_WHATSAPP = frozenset({"reminder_due", "reminder_check", "reminder_unconfirmed"})

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
