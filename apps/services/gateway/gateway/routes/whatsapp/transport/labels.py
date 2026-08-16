"""Transport · labels — the native WhatsApp labels/lists the founder created,
mirrored read-only (W16).

WhatsApp labels live in the account's app-state; the whatsmeow bridge syncs them
into ``wa_labels`` + ``wa_chat_labels`` (see transport/bridge.py). This route
serves them to the inbox: a small list with per-label chat counts, so the app can
show label chips on chats and a "filter by label" affordance — the same tags the
user set up inside WhatsApp, surfaced in Metorite.

Distinct from ``/whatsapp/categories`` (W2), which are OUR policy carriers.
"""

from __future__ import annotations

from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends
from gateway.routes.whatsapp.core import _tenant_session, router
from pydantic import BaseModel
from sqlalchemy import text


class WhatsAppLabelModel(BaseModel):
    wa_label_id: str
    name: str
    color: str | None = None
    color_index: int | None = None
    list_type: str | None = None
    sort_order: int = 100
    chat_count: int = 0


@router.get("/labels", response_model=list[WhatsAppLabelModel])
async def list_labels(
    account_id: str | None = None,
    user: UserContext = Depends(get_current_user),
):
    """Return the active, non-deleted labels for the account(s) the user owns,
    each with the number of chats currently carrying it."""
    async with _tenant_session() as db:
        params: dict[str, Any] = {"uid": user.email or "anonymous"}
        scope = "l.account_id IN (SELECT id FROM wa_accounts WHERE user_id = :uid"
        if account_id:
            scope += " AND id = :aid"
            params["aid"] = account_id
        scope += ")"

        rows = (await db.execute(text(f"""
            SELECT l.wa_label_id, l.name, l.color, l.color_index,
                   l.list_type, l.sort_order,
                   COUNT(cl.wa_chat_id) AS chat_count
            FROM wa_labels l
            LEFT JOIN wa_chat_labels cl
              ON cl.account_id = l.account_id AND cl.wa_label_id = l.wa_label_id
            WHERE {scope}
              AND l.wa_label_id IS NOT NULL AND NOT l.deleted
            GROUP BY l.account_id, l.wa_label_id, l.name, l.color,
                     l.color_index, l.list_type, l.sort_order
            ORDER BY l.sort_order, l.name"""), params)).fetchall()
        return [
            WhatsAppLabelModel(
                wa_label_id=r.wa_label_id,
                name=r.name or "",
                color=r.color,
                color_index=r.color_index,
                list_type=r.list_type,
                sort_order=int(r.sort_order or 100),
                chat_count=int(r.chat_count or 0),
            )
            for r in rows
        ]
