"""WhatsApp digest — the projection that joins the morning brief.

Same shape as the email digest-dashboard: ONE computation over the classified
state (chat statuses + intents + commitments), rendered as the "needs you first"
list plus the calm counts. Read-only; it never classifies (that's the post-sync
hooks' job) — it projects what they already wrote.
"""

from __future__ import annotations

from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends
from gateway.routes.whatsapp.core import _tenant_session, router
from pydantic import BaseModel
from sqlalchemy import text

# Intents that mean a message is routine noise, not an obligation — the "muted"
# tally in the trust ledger.
_MUTED_INTENTS = ("social", "spam")


class DigestItem(BaseModel):
    chat_id: str
    name: str
    snippet: str = ""
    intent: str | None = None


class DigestCounts(BaseModel):
    needs_reply: int = 0
    waiting: int = 0
    groups: int = 0
    muted: int = 0


class CommitmentWatchItem(BaseModel):
    chat_id: str
    text: str
    due_hint: str | None = None
    has_task: bool = False               # True once captured to a task


class WaitingOnItem(BaseModel):
    id: str                              # commitment id — the nudge target (W4.2)
    chat_id: str
    text: str
    due_hint: str | None = None


class WhatsAppDigestModel(BaseModel):
    needs_you: list[DigestItem] = []      # the ≤N that need the founder first
    counts: DigestCounts = DigestCounts()
    # Promises WE made that are still open — the ones the digest nudges you to
    # turn into tasks (has_task=False is the "never became work" flag).
    commitment_watch: list[CommitmentWatchItem] = []
    # Promises THEY made, still open — the waiting-on strip. Each carries its
    # commitment id so the founder can one-tap a nudge draft (W4.2).
    waiting_on: list[WaitingOnItem] = []
    waiting_on_count: int = 0            # total open (may exceed the shown list)


def status_counts(rows: list[Any]) -> DigestCounts:
    """Fold ``(status, n)`` rows into the calm counts. Pure/testable."""
    by = {r.status: int(r.n or 0) for r in rows}
    return DigestCounts(
        needs_reply=by.get("NEEDS_REPLY", 0),
        waiting=by.get("AWAITING", 0),
    )


def top_needs_you(items: list[DigestItem], limit: int = 3) -> list[DigestItem]:
    """The ≤``limit`` items the digest leads with. Pure (the query already orders
    by recency, so this just bounds it — kept separate so the bound is testable)."""
    return items[:limit]


@router.get("/digest", response_model=WhatsAppDigestModel)
async def digest(
    account_id: str | None = None,
    user: UserContext = Depends(get_current_user),
):
    """The WhatsApp section of the morning brief for the user's number(s)."""
    async with _tenant_session() as db:
        params: dict[str, Any] = {"uid": user.email or "anonymous"}
        scope = "IN (SELECT id FROM wa_accounts WHERE user_id = :uid"
        if account_id:
            scope += " AND id = :aid"
            params["aid"] = account_id
        scope += ")"

        status_rows = (await db.execute(
            text(f"""SELECT s.status, COUNT(*) AS n
                     FROM wa_chat_status s
                     WHERE s.account_id {scope}
                     GROUP BY s.status"""),
            params,
        )).fetchall()
        counts = status_counts(status_rows)

        counts.groups = int((await db.execute(
            text(f"SELECT COUNT(*) FROM wa_chats WHERE kind = 'group' "
                 f"AND account_id {scope}"),
            params,
        )).scalar() or 0)

        counts.muted = int((await db.execute(
            text(f"""SELECT COUNT(*) FROM wa_messages
                     WHERE intent = ANY(:muted) AND account_id {scope}"""),
            {**params, "muted": list(_MUTED_INTENTS)},
        )).scalar() or 0)

        need_rows = (await db.execute(
            text(f"""SELECT c.id, c.name, c.wa_chat_id,
                            lm.body_text AS snippet, lm.intent AS intent
                     FROM wa_chat_status s
                     JOIN wa_chats c ON c.id = s.chat_id
                     LEFT JOIN LATERAL (
                         SELECT body_text, intent FROM wa_messages m
                         WHERE m.chat_id = c.id
                         ORDER BY m.sent_at DESC NULLS LAST LIMIT 1
                     ) lm ON TRUE
                     WHERE s.status = 'NEEDS_REPLY' AND s.account_id {scope}
                       AND (s.snoozed_until IS NULL OR s.snoozed_until <= now())
                     ORDER BY s.last_message_at DESC NULLS LAST
                     LIMIT 3"""),
            params,
        )).fetchall()
        needs_you = top_needs_you([
            DigestItem(
                chat_id=str(r.id),
                name=r.name or r.wa_chat_id,
                snippet=(r.snippet or "")[:120],
                intent=r.intent,
            )
            for r in need_rows
        ])

        # Commitment watch: our open promises, task-linked or not.
        watch_rows = (await db.execute(
            text(f"""SELECT k.chat_id, k.text, k.due_hint,
                            (k.task_id IS NOT NULL)
                                AS has_task
                     FROM wa_commitments k
                     WHERE k.direction = 'ours' AND k.status = 'open'
                       AND k.account_id {scope}
                     ORDER BY k.created_at DESC
                     LIMIT 5"""),
            params,
        )).fetchall()
        commitment_watch = [
            CommitmentWatchItem(
                chat_id=str(r.chat_id), text=r.text, due_hint=r.due_hint,
                has_task=bool(r.has_task),
            )
            for r in watch_rows
        ]

        # Waiting-on strip: what THEY owe us, each nudgeable by id (W4.2).
        waiting_rows = (await db.execute(
            text(f"""SELECT k.id, k.chat_id, k.text, k.due_hint
                     FROM wa_commitments k
                     WHERE k.direction = 'theirs' AND k.status = 'open'
                       AND k.account_id {scope}
                     ORDER BY k.created_at DESC
                     LIMIT 5"""),
            params,
        )).fetchall()
        waiting_on = [
            WaitingOnItem(
                id=str(r.id), chat_id=str(r.chat_id), text=r.text,
                due_hint=r.due_hint,
            )
            for r in waiting_rows
        ]
        waiting_on_count = int((await db.execute(
            text(f"""SELECT COUNT(*) FROM wa_commitments
                     WHERE direction = 'theirs' AND status = 'open'
                       AND account_id {scope}"""),
            params,
        )).scalar() or 0)

        return WhatsAppDigestModel(
            needs_you=needs_you, counts=counts,
            commitment_watch=commitment_watch,
            waiting_on=waiting_on,
            waiting_on_count=waiting_on_count,
        )
