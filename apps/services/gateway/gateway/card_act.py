"""The member's answer to a chat card, in the act record (WS-PZ MM-1b, task 3614).

Spec: ``personalization.md`` §19.4 and ``proactive_loop.md`` §5.3. The seam is
``POST /agent/respond-input`` (``routes/agent.py``): the one place where the
member's OWN press on a card reaches the gateway, with the session member and
the session org. The run that asked the card has no database session and no
say in who answered, so the act is not recorded there.

**What it records.** ``app="chat"``, ``via="card"``, ``item_kind="card"``, the
card's ``request_id`` as the item, and one act:

* ``card_approve`` for the answer ``APPROVE``.
* ``card_reject`` for the answer ``REJECT``, and for a decline ("Not now").

An answer of another shape (a reply form, ticked rows, an edit form) records
nothing here. Each of those is an approve with content, and a later slice reads
it. A card steer ("make it shorter", ``card_revise``) does not come through this
route, so this slice records no revise either. The row holds no text of the
card or of the answer.

**It is called only after the thread that owns the card took the answer**, as
the reply-card record is (H-201), so a request id seen from another room records
nothing. It never raises, and the session is opened only when
``MEMBER_ACTIONS_ORGS`` holds the org.

**The route rows and this row.** An approved email card makes the email route
write its own row (``via=app``, with the mailbox and the sender domain). The
card row has no features, so ``member_habits`` never counts it with the mail row.
"""

from __future__ import annotations

from typing import Any

from acb_common import get_logger, member_actions

_log = get_logger(__name__)


def act_of(answer: str, declined: bool) -> str | None:
    """The act of a card answer, or None when it is not a plain yes or no."""
    if declined:
        return "card_reject"
    said = str(answer or "").strip().upper()
    if said == "APPROVE":
        return "card_approve"
    if said == "REJECT":
        return "card_reject"
    return None


async def record_card_answer(user: Any, request_id: str, answer: str,
                             declined: bool = False) -> bool:
    """Record the member's answer to a card. True when a row was written."""
    try:
        org = str(getattr(user, "organization_id", None) or "")
        email = str(getattr(user, "email", None) or "")
        act = act_of(answer, declined)
        if act is None or not member_actions.enabled_for(org):
            return False
        from acb_common.db import tenant_session

        async with tenant_session(org) as db:
            return await member_actions.record_act(
                db, org=org, member=email, app="chat", act=act, via="card",
                item_kind="card", item_id=str(request_id or ""))
    except Exception as exc:  # the class only
        _log.warning("card_act.failed", error=type(exc).__name__)
        return False
