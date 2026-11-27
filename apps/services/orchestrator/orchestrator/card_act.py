"""The member's answer to a chat card, in the act record (WS-PZ MM-1b, task 3614).

Spec: ``personalization.md`` §19.4 and ``proactive_loop.md`` §5.3. The seam is
the card answer in ``ask_tools``: ``request_confirmation`` calls
:func:`record_card_act` when a card closes. The outcome is a key of
``ask_tools.CARD_OUTCOMES`` (HRD-7), the one vocabulary of card outcomes. The
gateway route does not record a card answer: it sees the text of the answer,
never the outcome of the card.

**What it records.** ``app="chat"``, ``via="card"``, ``item_kind="card"``, the
card id as the item, and one act (:data:`ACT_OF_OUTCOME`):

* ``approved`` gives ``card_approve``. A plain Approve, a reply card that the
  member sent or saved, a rows card with a tick, an edit card and a held send
  card all close as ``approved``.
* ``declined`` gives ``card_reject``. A Reject, a Discard, a "Not now" and a
  rows card with no tick all close as ``declined``.

Each other outcome records nothing. A timeout, a parked card and a card that
did not show are no answer. "Another member answered" is not the member's own
answer. "Changed since the card" is set after the approve, which records once.
"Closed by your message" records no ``card_revise``: the spec names the act
but gives no trigger, and any chat message of the member closes the card, also
one about another subject.

**Only the member's own answer.** The responder is the gateway's session member
who pressed (``responder`` of the answer, never a field that the browser
sets). The run's own member comes from the run on the server
(``pending_ask._run_facts``). A row is written only when the two are the same
member, so a press of a colleague in a shared room records nothing. A late
approve (HRD-22) and the yes of a stored call (RESUME-2a) pass ``late=True``:
their grant binds the run's own member, and no card id of the late press
reaches the run, so the row takes a new id.

**The route rows.** A chat card that sends a mail calls ``POST /email/send``
(or ``/email/forward``, ``/email/drafts/send``) as the member, so that route
also writes its own row, with ``via="app"``. One yes on an email card then
gives two rows. The spec does not say how to join them (a card marker on the
route row, or one of the two rows only). That question is open, and the flag
must stay off until it is answered.

**Gates.** The flag ``MEMBER_ACTIONS_ORGS`` is read first, from the bound
tenant, before any Redis read or session. The write is bounded in time. It
never raises, so a fault never costs the card its answer. The row holds no
text of the card or of the answer.

Fences (R7): ``tests/unit/test_card_act.py`` and
``tests/unit/test_card_act_r8.py`` (the row on a real database).
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from acb_common import get_logger, member_actions
from acb_skills.ask_tools import CARD_APPROVED, CARD_DECLINED

_log = get_logger(__name__)

#: The act of each outcome of ``ask_tools.CARD_OUTCOMES`` that the act record
#: keeps. An outcome that is not here records nothing.
ACT_OF_OUTCOME: dict[str, str] = {
    CARD_APPROVED: "card_approve",
    CARD_DECLINED: "card_reject",
}

#: The bound of the whole write of one answer. A card never waits on it long.
_TIMEOUT_S = 3.0


def act_of(outcome: Any) -> str | None:
    """The act of a card *outcome*, or None when the record keeps none."""
    return ACT_OF_OUTCOME.get(str(outcome or ""))


async def _facts(thread_id: str) -> tuple[str | None, str]:
    """The org and the run's own member of the run on *thread_id*, server side."""
    from orchestrator.pending_ask import _run_facts

    facts = await _run_facts(thread_id)
    return facts[0], str(facts[3] or "").strip().lower()


async def record_card_act(thread_id: str, request_id: str | None, outcome: str,
                          responder: str = "", *, late: bool = False) -> bool:
    """Record the member's answer to a card. True when a row was written.

    *thread_id* is the run's own chat (``resolve_relay_thread_id``).
    *outcome* is a key of ``ask_tools.CARD_OUTCOMES``. *responder* is the
    gateway's session member who answered. *late* is True only for a late
    approve and for the yes of a stored call. Never raises."""
    try:
        act = act_of(outcome)
        if act is None or not thread_id:
            return False
        from acb_common.db import current_tenant

        bound = current_tenant()
        if bound is not None and not member_actions.enabled_for(bound):
            # Off for the bound tenant: no Redis read and no session.
            return False

        async def _write() -> bool:
            org, actor = await _facts(thread_id)
            who = actor if late else str(responder or "").strip().lower()
            if not org or not actor or who != actor or not member_actions.enabled_for(org):
                return False
            from acb_common.db import tenant_session

            async with tenant_session(org) as db:
                return await member_actions.record_act(
                    db, org=org, member=who, app="chat", act=act, via="card",
                    item_kind="card", item_id=str(request_id or uuid.uuid4().hex))

        return await asyncio.wait_for(_write(), _TIMEOUT_S)
    except BaseException as exc:
        if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
            raise
        _log.warning("card_act.failed", error=type(exc).__name__)
        return False
