"""Automation · Reply Zero — per-chat reply status.

The WhatsApp port of the email vertical's Reply Zero: each conversation carries a
status (NEEDS_REPLY / AWAITING / FYI / DONE) so the triage queue shows
obligations, not unreads. WhatsApp has no threads, so we key on the chat.

Deterministic-first (an LLM refinement can layer on later, as email's did):

* we spoke last            → AWAITING  (waiting on them)
* they spoke last, DM      → NEEDS_REPLY
* they spoke last, group   → NEEDS_REPLY only if the founder was @mentioned,
                             else FYI (group chatter isn't a reply obligation)
* a reaction/system last   → FYI       (a 👍 isn't a message to answer)
* no messages              → FYI

The classifier is a pure function so the policy is unit-testable without a DB;
``recompute_chat_status`` loads the latest message and writes the row;
``classify_chats`` is the post-sync hook that refreshes every chat on an account.
"""

from __future__ import annotations

from typing import Any

from acb_common import get_logger
from sqlalchemy import text

_log = get_logger("gateway.whatsapp.replyzero")

# The disposition set, matching wa_chat_status.status + the email model.
NEEDS_REPLY = "NEEDS_REPLY"
AWAITING = "AWAITING"
FYI = "FYI"
DONE = "DONE"

# Message kinds that are not themselves a reply obligation when they arrive last.
_NON_OBLIGATION_KINDS = {"reaction", "system"}


def classify_chat_status(
    last_direction: str | None,
    *,
    is_group: bool,
    mentioned: bool,
    last_kind: str = "text",
) -> tuple[str, str]:
    """Return ``(status, reason)`` for a chat from its latest message. Pure."""
    if not last_direction:
        return FYI, "no messages"
    if last_direction == "out":
        return AWAITING, "we replied — waiting on them"
    # Inbound last.
    if last_kind in _NON_OBLIGATION_KINDS:
        return FYI, f"last inbound was a {last_kind}"
    if is_group:
        if mentioned:
            return NEEDS_REPLY, "you were @mentioned"
        return FYI, "group message, not addressed to you"
    return NEEDS_REPLY, "they messaged and await your reply"


def _account_wa_ids(phone_number: str | None) -> set[str]:
    """The identifiers that count as 'the founder' in a group mention list — the
    account's own number, with and without a leading '+'."""
    if not phone_number:
        return set()
    p = phone_number.strip()
    return {p, p.lstrip("+")}


async def recompute_chat_status(
    db: Any, account_id: str, chat_id: str, *,
    account_phone: str | None = None, chat_kind: str | None = None,
) -> str | None:
    """Recompute + persist one chat's status. Returns the status, or None if the
    chat has no messages. Caller owns the transaction. ``chat_kind`` may be passed
    when the caller already knows it (the sweep does), sparing a per-chat lookup."""
    last = (await db.execute(
        text("""SELECT direction, kind, mentions, sent_at, id
                FROM wa_messages WHERE chat_id = :cid
                ORDER BY sent_at DESC NULLS LAST LIMIT 1"""),
        {"cid": chat_id},
    )).fetchone()
    if last is None:
        return None

    if chat_kind is None:
        chat = (await db.execute(
            text("SELECT kind FROM wa_chats WHERE id = :cid"),
            {"cid": chat_id},
        )).fetchone()
        chat_kind = chat.kind if chat else None
    is_group = chat_kind == "group"

    mentions = set(last.mentions or [])
    mentioned = bool(mentions & _account_wa_ids(account_phone))

    status, reason = classify_chat_status(
        last.direction, is_group=is_group, mentioned=mentioned,
        last_kind=last.kind or "text",
    )
    await db.execute(
        text("""INSERT INTO wa_chat_status
                  (account_id, chat_id, status, last_message_id, last_message_at,
                   reason, classified_at)
                VALUES (:aid, :cid, :status, :lmid, :lmat, :reason, now())
                ON CONFLICT (account_id, chat_id) DO UPDATE SET
                  status = EXCLUDED.status,
                  last_message_id = EXCLUDED.last_message_id,
                  last_message_at = EXCLUDED.last_message_at,
                  reason = EXCLUDED.reason,
                  classified_at = now(),
                  -- A NEW inbound message wakes a snoozed chat: clear the snooze
                  -- only when THIS chat's last message actually changed and the
                  -- new one is inbound (classify_chats sweeps every chat, so an
                  -- unconditional clear would defeat snooze). W6.
                  snoozed_until = CASE
                    WHEN wa_chat_status.last_message_id
                         IS DISTINCT FROM EXCLUDED.last_message_id
                         AND :last_dir = 'in'
                    THEN NULL
                    ELSE wa_chat_status.snoozed_until
                  END"""),
        {"aid": account_id, "cid": chat_id, "status": status,
         "lmid": str(last.id), "lmat": last.sent_at, "reason": reason,
         "last_dir": last.direction or "in"},
    )
    return status


async def classify_chats(account_id: str) -> None:
    """Post-sync hook: refresh the reply status of every chat on an account.

    Cheap when there's nothing new (one indexed query per chat); like the email
    classify_threads hook it deliberately sweeps all chats so a quiet number
    still catches up rather than gating on new inbound.
    """
    from gateway.routes.whatsapp.core import _tenant_session
    # WS-20 WA-C1: the caller binds the account's tenant. The Meta webhook
    # binds the org of the account row before it fires this hook. With no
    # binding, `_tenant_session()` raises `TenantUnbound` and writes nothing.
    # The bridge routes bind nothing yet (§12.4, out of WA-C1), and
    # `fire_post_sync_hooks` logs that failure.
    async with _tenant_session() as db:
        acc = (await db.execute(
            text("SELECT phone_number FROM wa_accounts WHERE id = :aid"),
            {"aid": account_id},
        )).fetchone()
        phone = acc.phone_number if acc else None
        chats = (await db.execute(
            text("SELECT id, kind FROM wa_chats WHERE account_id = :aid"),
            {"aid": account_id},
        )).fetchall()
        for c in chats:
            await recompute_chat_status(
                db, account_id, str(c.id),
                account_phone=phone, chat_kind=c.kind)
    _log.info("whatsapp.classify_chats.done",
              account_id=account_id, chats=len(chats))
