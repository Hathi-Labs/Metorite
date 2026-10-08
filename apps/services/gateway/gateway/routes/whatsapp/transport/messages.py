"""Transport · messages — a conversation's thread + full-text search (read-only).

🔴 **Both routes are reads, and they change no state.** Neither one writes a
row, and neither one sends a read receipt: WhatsApp's "seen" goes out only
through a provider's ``mark_read``, and no route here calls it. The narrowing
READ of WS-48 N4 rests on that. ``test_whatsapp_read_no_mark.py`` holds it on a
real database (R8), and ``test_whatsapp_narrow_source.py`` holds it on the SQL
that each route sends.

WS-48 N4 (``data_narrowing_pipeline.md`` §9 N4) adds parameters to both
routes. Each one defaults to no change, so the app reads the same as before:

* ``GET /search`` gains the structured filters of the narrowing adapter, the
  ``websearch`` grammar (``or`` between words), and the chat's name and kind
  in each row. With no ``q``, it searches on the filters only.
* ``GET /chats/{chat_id}/messages`` gains ``around`` and ``window``: the
  message ``around`` and at most ``window`` messages on each side of it.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException, Query
from gateway.routes.whatsapp.core import (
    WhatsAppMessageModel,
    _tenant_session,
    assert_chat_owned,
    router,
)
from sqlalchemy import text

#: The chat kinds of ``wa_chats.kind`` (migration 102).
CHAT_KINDS = frozenset({"dm", "group", "broadcast"})
#: The message kinds that carry media (migration 102, ``wa_messages.kind``).
MEDIA_KINDS: tuple[str, ...] = ("image", "video", "audio", "voice", "document", "sticker")
#: The most messages on each side of ``around`` (WS-48 N4).
MAX_WINDOW = 10


def _message_model(row: Any) -> WhatsAppMessageModel:
    sender = row.sender or {}
    if isinstance(sender, str):
        import json
        try:
            sender = json.loads(sender)
        except ValueError:
            sender = {}
    return WhatsAppMessageModel(
        id=str(row.id),
        chat_id=str(row.chat_id),
        wa_message_id=row.wa_message_id,
        direction=row.direction or "in",
        kind=row.kind or "text",
        sender_name=(sender or {}).get("name", "") or "",
        body_text=row.body_text or "",
        transcript_text=getattr(row, "transcript_text", None),
        quoted_wa_message_id=row.quoted_wa_message_id,
        categories=list(row.categories or []),
        intent=row.intent,
        send_regime=row.send_regime,
        sent_at=row.sent_at.isoformat() if row.sent_at else None,
        chat_name=getattr(row, "chat_name", None),
        chat_kind=getattr(row, "chat_kind", None),
    )


def _uuid_or_none(value: Any) -> str | None:
    try:
        return str(uuid.UUID(str(value).strip()))
    except (ValueError, AttributeError, TypeError):
        return None


def _plain(value: Any, default: Any) -> Any:
    """A direct Python call gets the ``Query`` object as the value of a
    parameter it does not pass. That reads as the default."""
    return default if type(value).__name__ in {"Query", "QueryInfo", "FieldInfo"} else value


_COLUMNS = """m.id, m.chat_id, m.wa_message_id, m.direction, m.kind, m.sender,
              m.body_text, m.transcript_text, m.quoted_wa_message_id,
              m.categories, m.intent, m.send_regime, m.sent_at,
              c.name AS chat_name, c.kind AS chat_kind"""


async def _window(db: Any, chat_id: str, around: str, window: int) -> list[WhatsAppMessageModel]:
    """The message *around* and at most *window* messages on each side of it,
    oldest first. A message that is not in the chat is a 404. A read only."""
    rows = (await db.execute(
        text(f"""WITH anchor AS (
                    SELECT id, sent_at FROM wa_messages
                    WHERE id = CAST(:mid AS uuid) AND chat_id = CAST(:cid AS uuid)
                 )
                 (SELECT {_COLUMNS}, 0 AS side
                    FROM wa_messages m JOIN wa_chats c ON c.id = m.chat_id, anchor a
                   WHERE m.id = a.id)
                 UNION ALL
                 (SELECT {_COLUMNS}, -1 AS side
                    FROM wa_messages m JOIN wa_chats c ON c.id = m.chat_id, anchor a
                   WHERE m.chat_id = CAST(:cid AS uuid)
                     AND (m.sent_at, m.id) < (a.sent_at, a.id)
                   ORDER BY m.sent_at DESC, m.id DESC
                   LIMIT :w)
                 UNION ALL
                 (SELECT {_COLUMNS}, 1 AS side
                    FROM wa_messages m JOIN wa_chats c ON c.id = m.chat_id, anchor a
                   WHERE m.chat_id = CAST(:cid AS uuid)
                     AND (m.sent_at, m.id) > (a.sent_at, a.id)
                   ORDER BY m.sent_at ASC, m.id ASC
                   LIMIT :w)"""),
        {"mid": around, "cid": chat_id, "w": window},
    )).fetchall()
    if not any(r.side == 0 for r in rows):
        raise HTTPException(status_code=404, detail="Message not found")
    # UNION ALL keeps no order. A side row has a sent_at (the row compare
    # leaves out a NULL), so the sort needs no NULL rule.
    def side(n: int) -> list[Any]:
        return sorted((r for r in rows if r.side == n), key=lambda r: (r.sent_at, str(r.id)))

    return [_message_model(r) for r in (*side(-1), *side(0), *side(1))]


@router.get("/chats/{chat_id}/messages", response_model=list[WhatsAppMessageModel])
async def list_messages(
    chat_id: str,
    limit: int = Query(100, le=500),
    around: str | None = None,
    window: int = Query(2, ge=0, le=MAX_WINDOW),
    user: UserContext = Depends(get_current_user),
):
    """Return a conversation's messages oldest-first (thread reading order).

    With ``around`` (a message id of this chat), return that message and at
    most ``window`` messages on each side of it, oldest first (WS-48 N4).
    ``limit`` does not apply then. Both forms only read.
    """
    around = _plain(around, None)
    window = int(_plain(window, 2))
    async with _tenant_session() as db:
        await assert_chat_owned(db, chat_id, user.email or "anonymous")
        if around is not None:
            anchor = _uuid_or_none(around)
            canonical_chat = _uuid_or_none(chat_id)
            if anchor is None or canonical_chat is None:
                raise HTTPException(status_code=404, detail="Message not found")
            return await _window(db, canonical_chat, anchor, max(0, min(window, MAX_WINDOW)))
        rows = (await db.execute(
            text("""SELECT id, chat_id, wa_message_id, direction, kind, sender,
                           body_text, transcript_text, quoted_wa_message_id,
                           categories, intent, send_regime, sent_at
                    FROM wa_messages
                    WHERE chat_id = :cid
                    ORDER BY sent_at ASC NULLS FIRST
                    LIMIT :limit"""),
            {"cid": chat_id, "limit": limit},
        )).fetchall()
        return [_message_model(r) for r in rows]


# The tsvector expression — byte-for-byte identical to migration 102's
# ``idx_wa_messages_fts`` (simple config, body + transcript + sender name) so the
# GIN index is used, not a seq scan. Kept in one place: match + rank share it.
_WA_FTS = (
    "to_tsvector('simple', "
    "coalesce(m.body_text, '') || ' ' || "
    "coalesce(m.transcript_text, '') || ' ' || "
    "coalesce(m.sender->>'name', ''))"
)


def _like(value: str) -> str:
    """*value* as an ILIKE pattern of a substring, with its wildcards escaped."""
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


@router.get("/search", response_model=list[WhatsAppMessageModel])
async def search_messages(
    q: str | None = None,
    account_id: str | None = None,
    # 201, not 200: a caller that wants 200 rows asks for one more, to see
    # that more matched (WS-48 N4). The route returns no total.
    limit: int = Query(50, le=201),
    hybrid: bool = Query(
        False, description="Blend semantic (vector) similarity into the ranking"),
    websearch: bool = False,
    chat_id: str | None = None,
    contact: str | None = None,
    chat_kind: str | None = None,
    sent_after: datetime | None = None,
    sent_before: datetime | None = None,
    direction: str | None = None,
    has_media: bool | None = None,
    user: UserContext = Depends(get_current_user),
):
    """Full-text search across the user's WhatsApp history.

    Recall is always LEXICAL (every FTS match is returned). ``hybrid=true``
    re-ORDERS the matches by blending the lexical rank with cosine similarity to
    the query's embedding (W10) — so a keyword hit is never dropped, and semantic
    closeness only re-ranks. Requires ``whatsapp_semantic_search_enabled``; if the
    query can't be embedded (flag off / embed error), it falls through to lexical.

    WS-48 N4, each one optional and each one a narrower search:

    * ``websearch=true`` reads ``q`` in the ``websearch_to_tsquery`` grammar,
      so ``price or rate`` finds either word. The default ANDs the words.
    * ``chat_id``, ``contact`` (a part of the chat name or the sender name),
      ``chat_kind`` (``dm``, ``group`` or ``broadcast``), ``sent_after`` and
      ``sent_before`` (both inclusive), ``direction`` (``in`` or ``out``) and
      ``has_media``.
    * With no ``q``, the filters alone choose the rows, newest first. A call
      with no ``q`` and no filter is a 422.

    The scope never widens: every row is a message of the user's own accounts.
    """
    q = (_plain(q, None) or "").strip()
    hybrid = bool(_plain(hybrid, False))
    websearch = bool(_plain(websearch, False))
    limit = int(_plain(limit, 50))
    chat_id, contact, chat_kind = (_plain(v, None) for v in (chat_id, contact, chat_kind))
    sent_after, sent_before = _plain(sent_after, None), _plain(sent_before, None)
    direction, has_media = _plain(direction, None), _plain(has_media, None)

    filters: list[str] = []
    params: dict[str, Any] = {"uid": user.email or "anonymous", "limit": limit}
    if chat_id:
        canonical = _uuid_or_none(chat_id)
        if canonical is None:
            raise HTTPException(status_code=422, detail="chat_id must be a chat id.")
        filters.append("m.chat_id = CAST(:cid AS uuid)")
        params["cid"] = canonical
    if contact is not None and contact.strip():
        filters.append("(c.name ILIKE :contact ESCAPE '\\' "
                       "OR m.sender->>'name' ILIKE :contact ESCAPE '\\')")
        params["contact"] = _like(contact.strip()[:200])
    if chat_kind:
        if chat_kind not in CHAT_KINDS:
            raise HTTPException(status_code=422, detail="chat_kind must be dm, group or broadcast.")
        filters.append("c.kind = :kind")
        params["kind"] = chat_kind
    if sent_after is not None:
        filters.append("m.sent_at >= :after")
        params["after"] = sent_after
    if sent_before is not None:
        filters.append("m.sent_at <= :before")
        params["before"] = sent_before
    if direction:
        if direction not in {"in", "out"}:
            raise HTTPException(status_code=422, detail="direction must be in or out.")
        filters.append("m.direction = :dir")
        params["dir"] = direction
    if has_media is not None:
        media = ", ".join(f"'{k}'" for k in MEDIA_KINDS)  # constants, never input
        filters.append(f"m.kind {'IN' if has_media else 'NOT IN'} ({media})")
    if not q and not filters:
        raise HTTPException(status_code=422, detail="Give q or a filter.")

    async with _tenant_session() as db:
        scope = "m.account_id IN (SELECT id FROM wa_accounts WHERE user_id = :uid"
        if account_id:
            scope += " AND id = :aid"
            params["aid"] = account_id
        scope += ")"

        tsquery = (
            "websearch_to_tsquery('simple', :q)" if websearch
            else "plainto_tsquery('simple', :q)"
        )
        where = [scope, *filters]
        if q:
            where.append(f"{_WA_FTS} @@ {tsquery}")
            params["q"] = q

        join_sql = ""
        order_sql = "m.sent_at DESC NULLS LAST"
        if hybrid and q:
            qvec = None
            try:
                from whatsapp_ingestion.wa_embeddings import embed_query
                qvec = await embed_query(q)
            except Exception:
                qvec = None
            if qvec is not None:
                params["qvec"] = "[" + ",".join(f"{x:.7f}" for x in qvec) + "]"
                # 0.5·lexical (capped to 1) + 0.5·cosine. A message with no
                # embedding yet (sim NULL → 0) still ranks on its lexical score,
                # so unembedded history is never hidden.
                join_sql = ("LEFT JOIN wa_message_embeddings e "
                            "ON e.message_id = m.id")
                order_sql = (
                    f"(LEAST(ts_rank_cd({_WA_FTS}, {tsquery}), 1.0) * 0.5"
                    " + COALESCE(1 - (e.embedding <=> CAST(:qvec AS vector)), 0)"
                    " * 0.5) DESC, m.sent_at DESC NULLS LAST")

        rows = (await db.execute(
            text(f"""SELECT {_COLUMNS}
                     FROM wa_messages m
                     JOIN wa_chats c ON c.id = m.chat_id
                     {join_sql}
                     WHERE {' AND '.join(where)}
                     ORDER BY {order_sql}
                     LIMIT :limit"""),
            params,
        )).fetchall()
        return [_message_model(r) for r in rows]
