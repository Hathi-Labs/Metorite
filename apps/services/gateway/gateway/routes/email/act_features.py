"""The derived features of a mail for the act record (WS-PZ MM-1c, task 3615).

``acb_common.member_actions`` takes a closed list of features and never any
text. Habits (MM-2) match a kind of item by ``sender_domain``, so each email
seam that records an act adds the domain of the sender. This module is the ONE
place that reads it. It selects the ``from_address`` and nothing else of the
mail, and it returns a domain, never an address, a subject or a body. A failed
read gives no feature, because the act row is worth more with no feature than
no row.
"""

from __future__ import annotations

from typing import Any

from acb_common import member_actions
from sqlalchemy import text

_MESSAGE_SQL = (
    "SELECT lower(from_address->>'email') FROM email_messages "
    "WHERE id = CAST(:id AS uuid) LIMIT 1"
)
#: The newest mail of the thread that the member did not send.
_THREAD_SQL = (
    "SELECT lower(from_address->>'email') FROM email_messages "
    "WHERE account_id = CAST(:aid AS uuid) AND thread_id = :tid "
    "AND lower(coalesce(folder, '')) <> 'sent' "
    "ORDER BY received_at DESC NULLS LAST LIMIT 1"
)


def of_address(address: Any) -> dict[str, Any]:
    """The features of a sender address: its domain, or none."""
    if isinstance(address, dict):  # a ``from_address`` jsonb: {"email": ..., "name": ...}
        address = address.get("email")
    domain = member_actions.domain_of(address if isinstance(address, str) else None)
    return {"sender_domain": domain} if domain else {}


async def of_message(db: Any, org: str | None, message_id: str) -> dict[str, Any]:
    """The features of one mail, read on the caller's tenant session. With
    ``MEMBER_ACTIONS_ORGS`` off for *org* it reads nothing."""
    if not member_actions.enabled_for(org):
        return {}
    try:
        async with db.begin_nested():
            row = (await db.execute(text(_MESSAGE_SQL), {"id": str(message_id)})).first()
        return of_address(row[0] if row else None)
    except Exception:
        return {}


async def of_thread(db: Any, org: str | None, account_id: str,
                    thread_id: str) -> dict[str, Any]:
    """The features of a thread: the sender of its newest inbound mail. With
    ``MEMBER_ACTIONS_ORGS`` off for *org* it reads nothing."""
    if not member_actions.enabled_for(org):
        return {}
    try:
        async with db.begin_nested():
            row = (await db.execute(text(_THREAD_SQL), {
                "aid": str(account_id), "tid": str(thread_id)})).first()
        return of_address(row[0] if row else None)
    except Exception:
        return {}
