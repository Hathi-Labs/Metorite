"""Transport · capture — turn a WhatsApp message into a task.

The WhatsApp inbox as a capture channel, mirroring the email → task hand-off.
W1 keeps it deterministic (no LLM): a message becomes a plain INBOX item with an
``origin`` linking back to the chat + message, idempotent per message so a
double-tap returns the existing task rather than duplicating it. AI-routed
capture (disposition, delegate, due date) is a later phase, reusing the email
capture's LLM machinery.
"""

from __future__ import annotations

import json
import re

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException
from gateway.routes.tasks.item_source import item_source
from gateway.routes.whatsapp.core import _tenant_session, router
from pydantic import BaseModel
from sqlalchemy import text


class CaptureTaskRequest(BaseModel):
    message_id: str                 # wa_messages.id (a specific message)
    title: str | None = None        # optional override; else derived from the body


class CaptureTaskResponse(BaseModel):
    item_id: str
    title: str
    created: bool                   # False = this message was already captured


def derive_title(body: str, sender_name: str, chat_name: str) -> str:
    """A clean, bounded task title from a message. Never interprets the content
    (untrusted, other-authored) — just a readable handle. Pure/testable."""
    clean = re.sub(r"\s+", " ", (body or "")).strip()
    who = (sender_name or chat_name or "someone").strip()
    if clean:
        snippet = clean[:80] + ("…" if len(clean) > 80 else "")
        return f"WhatsApp · {who}: {snippet}"[:200]
    return f"WhatsApp · reply to {who}"[:200]


@router.post("/capture-task", response_model=CaptureTaskResponse, status_code=201)
async def capture_task(
    req: CaptureTaskRequest,
    user: UserContext = Depends(get_current_user),
):
    """Capture a WhatsApp message as a My Tasks inbox item (idempotent per message)."""
    uid = user.email or "anonymous"
    async with _tenant_session() as db:
        # Owner check THROUGH the account, and pull the fields we tag the origin
        # with in one query.
        msg = (await db.execute(
            text("""SELECT m.id, m.chat_id, m.wa_message_id, m.body_text,
                           m.sender, c.name AS chat_name, c.wa_chat_id
                    FROM wa_messages m
                    JOIN wa_chats c ON c.id = m.chat_id
                    JOIN wa_accounts a ON a.id = m.account_id
                    WHERE m.id = :mid AND a.user_id = :uid"""),
            {"mid": req.message_id, "uid": uid},
        )).fetchone()
        if msg is None:
            raise HTTPException(status_code=404, detail="Message not found")

        # Idempotent: an OPEN item already captured from this message wins.
        src = item_source()
        existing = await src.find_by_origin(
            db, uid, "wa_message_id", msg.wa_message_id)
        if existing is not None:
            return CaptureTaskResponse(
                item_id=str(existing.id), title=existing.title, created=False)

        sender = msg.sender or {}
        if isinstance(sender, str):
            try:
                sender = json.loads(sender)
            except ValueError:
                sender = {}
        sender_name = (sender or {}).get("name", "") or ""
        title = (req.title or "").strip() or derive_title(
            msg.body_text or "", sender_name, msg.chat_name or "")

        origin = {
            "kind": "whatsapp",
            "chat_id": str(msg.chat_id),
            "wa_chat_id": msg.wa_chat_id,
            "wa_message_id": msg.wa_message_id,
            "sender_name": sender_name[:120],
        }
        item_id = await src.insert_capture(db, uid, {
            "title": title,
            "description": (msg.body_text or "")[:500] or None,
            "disposition": "INBOX",
            "source": "LOCAL", "sync_state": "local", "is_mine": True,
        }, origin)
        return CaptureTaskResponse(item_id=item_id, title=title, created=True)
