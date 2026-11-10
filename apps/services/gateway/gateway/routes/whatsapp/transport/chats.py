"""Transport · chats — the triage streams + conversation list (read-only).

Powers the calm home screen: ``/whatsapp/streams`` returns the nav counts (the
single spine), ``/whatsapp/chats`` returns the quiet, filterable conversation
list. No unread count is exposed — obligations, not unreads, by design.
"""

from __future__ import annotations

from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends, Query
from gateway.routes.whatsapp.core import WhatsAppChatModel, _tenant_session, router
from sqlalchemy import text

# The triage streams shown in the nav, in order. Status streams map to
# wa_chat_status.status; 'groups' is a kind filter. Counts are cheap indexed
# queries; a stream with no classifier data yet simply reads 0 (W0 is pre-triage).
_STATUS_STREAMS = {
    "needs_reply": "NEEDS_REPLY",
    "waiting": "AWAITING",
}

# Snooze overlay (W6): a chat is hidden from every triage stream while its wake
# time is still in the future, and shown only in the dedicated 'snoozed' stream.
_NOT_SNOOZED = "(s.snoozed_until IS NULL OR s.snoozed_until <= now())"
_SNOOZED = "(s.snoozed_until > now())"


def _chat_model(
    row: Any, labels: list[dict[str, Any]] | None = None
) -> WhatsAppChatModel:
    window_open = bool(getattr(row, "window_open", False))
    snoozed_until = getattr(row, "snoozed_until", None)
    return WhatsAppChatModel(
        id=str(row.id),
        account_id=str(row.account_id),
        wa_chat_id=row.wa_chat_id,
        kind=row.kind or "dm",
        name=row.name or "",
        category=row.category,
        status=getattr(row, "status", None),
        last_message_at=row.last_message_at.isoformat() if row.last_message_at else None,
        last_snippet=(getattr(row, "last_snippet", "") or "")[:140],
        window_open=window_open,
        window_expires_at=(
            row.service_window_expires_at.isoformat()
            if row.service_window_expires_at else None
        ),
        snoozed_until=snoozed_until.isoformat() if snoozed_until else None,
        labels=labels or [],
        avatar_url=getattr(row, "avatar_url", None),
    )


async def _labels_by_chat(
    db: Any, rows: list[Any]
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """Fetch the native WhatsApp labels for a page of chats in one query, keyed by
    (account_id, wa_chat_id). Associations key on the JID (see 113 migration), so
    we match rows on that pair rather than the chat UUID."""
    if not rows:
        return {}
    aids = list({str(r.account_id) for r in rows})
    jids = list({r.wa_chat_id for r in rows})
    recs = (await db.execute(text("""
        SELECT cl.account_id, cl.wa_chat_id,
               l.wa_label_id, l.name, l.color, l.sort_order
        FROM wa_chat_labels cl
        JOIN wa_labels l
          ON l.account_id = cl.account_id AND l.wa_label_id = cl.wa_label_id
        WHERE cl.account_id = ANY(:aids) AND cl.wa_chat_id = ANY(:jids)
          AND NOT l.deleted
        ORDER BY l.sort_order, l.name"""),
        {"aids": aids, "jids": jids})).fetchall()
    out: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for r in recs:
        out.setdefault((str(r.account_id), r.wa_chat_id), []).append({
            "wa_label_id": r.wa_label_id, "name": r.name or "", "color": r.color,
        })
    return out


@router.get("/streams")
async def list_streams(
    account_id: str | None = None,
    user: UserContext = Depends(get_current_user),
):
    """Return the nav stream counts for the account(s) the user owns."""
    async with _tenant_session() as db:
        params: dict[str, Any] = {"uid": user.email or "anonymous"}
        scope = "c.account_id IN (SELECT id FROM wa_accounts WHERE user_id = :uid"
        if account_id:
            scope += " AND id = :aid"
            params["aid"] = account_id
        scope += ")"

        # One pass with conditional aggregates instead of five COUNT round-trips.
        row = (await db.execute(text(f"""
            SELECT
              COUNT(*) FILTER (WHERE s.status = 'NEEDS_REPLY' AND {_NOT_SNOOZED}) AS needs_reply,
              COUNT(*) FILTER (WHERE s.status = 'AWAITING' AND {_NOT_SNOOZED})     AS waiting,
              COUNT(*) FILTER (WHERE c.kind = 'group' AND {_NOT_SNOOZED})          AS groups,
              COUNT(*) FILTER (WHERE {_NOT_SNOOZED})                               AS all,
              COUNT(*) FILTER (WHERE {_SNOOZED})                                   AS snoozed
            FROM wa_chats c
            LEFT JOIN wa_chat_status s
              ON s.account_id = c.account_id AND s.chat_id = c.id
            WHERE {scope}"""), params)).fetchone()
        return {
            "needs_reply": int(row.needs_reply or 0),
            "waiting": int(row.waiting or 0),
            "groups": int(row.groups or 0),
            "all": int(row.all or 0),
            "snoozed": int(row.snoozed or 0),
        }


@router.get("/chats", response_model=list[WhatsAppChatModel])
async def list_chats(
    account_id: str | None = None,
    stream: str | None = Query(None, description="needs_reply|waiting|groups|all"),
    category: str | None = None,
    label: str | None = Query(None, description="filter to chats carrying this WhatsApp label id"),
    limit: int = Query(50, le=200),
    user: UserContext = Depends(get_current_user),
):
    """List conversations, newest first, optionally scoped to a stream/category/label."""
    async with _tenant_session() as db:
        params: dict[str, Any] = {"uid": user.email or "anonymous", "limit": limit}
        where = ["c.account_id IN (SELECT id FROM wa_accounts WHERE user_id = :uid"]
        if account_id:
            where[0] += " AND id = :aid"
            params["aid"] = account_id
        where[0] += ")"

        if stream == "snoozed":
            where.append(_SNOOZED)
        else:
            # Every non-snoozed stream hides currently-snoozed chats.
            where.append(_NOT_SNOOZED)
            if stream in _STATUS_STREAMS:
                where.append("s.status = :status")
                params["status"] = _STATUS_STREAMS[stream]
            elif stream == "groups":
                where.append("c.kind = 'group'")
        if category:
            where.append("c.category = :category")
            params["category"] = category
        if label:
            where.append("""EXISTS (SELECT 1 FROM wa_chat_labels cl
                WHERE cl.account_id = c.account_id
                  AND cl.wa_chat_id = c.wa_chat_id AND cl.wa_label_id = :label)""")
            params["label"] = label

        # Last message snippet via a lateral pull of the most recent body. Avatar
        # is a plain LEFT JOIN (one row per chat, unlike labels' one-to-many).
        q = f"""
            SELECT c.id, c.account_id, c.wa_chat_id, c.kind, c.name, c.category,
                   c.last_message_at, c.service_window_expires_at,
                   (c.service_window_expires_at > now()) AS window_open,
                   s.status AS status, s.snoozed_until AS snoozed_until,
                   lm.body_text AS last_snippet, av.avatar_url AS avatar_url
            FROM wa_chats c
            LEFT JOIN wa_chat_status s
              ON s.account_id = c.account_id AND s.chat_id = c.id
            LEFT JOIN wa_chat_avatars av
              ON av.account_id = c.account_id AND av.wa_chat_id = c.wa_chat_id
            LEFT JOIN LATERAL (
                SELECT body_text FROM wa_messages m
                WHERE m.chat_id = c.id
                ORDER BY m.sent_at DESC NULLS LAST LIMIT 1
            ) lm ON TRUE
            WHERE {' AND '.join(where)}
            ORDER BY c.last_message_at DESC NULLS LAST, c.id DESC
            LIMIT :limit
        """
        rows = (await db.execute(text(q), params)).fetchall()
        labels_by_chat = await _labels_by_chat(db, rows)
        return [
            _chat_model(r, labels_by_chat.get((str(r.account_id), r.wa_chat_id)))
            for r in rows
        ]
