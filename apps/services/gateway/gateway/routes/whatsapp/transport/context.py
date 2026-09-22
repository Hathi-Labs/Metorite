"""Transport · context — the company standing behind a conversation (W1).

Backs the conversation's "Details" drawer: who this contact is, the CRM/ERP
entity they link to, the tasks/commitments already open with them, and basic
history stats. This is the moat — the phone app can never show that the person
you're chatting with owes ₹3.2 L.

W1 resolves what's queryable from our own store (contact identity + category,
tasks captured from this chat via ``origin.wa_chat_id``, message stats) and
exposes the CRM/ERP linkage through a stable ``entity_ref`` the frontend can
deep-link on. Fetching live deal/invoice fields from Zoho/Odoo is a later phase
that fills the ``crm`` block; until then it degrades to the parsed ref, honestly.
"""

from __future__ import annotations

import json
from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException
from gateway.routes.whatsapp.core import _tenant_session, router
from pydantic import BaseModel
from sqlalchemy import text

# Known entity-ref systems (``<system>:<kind>:<id>``) the linker writes onto
# wa_contacts.entity_ref. Kept small + explicit so an unknown system degrades to
# "unlinked" rather than a broken deep-link.
#
# ⚠️ ``"crm"`` is PARSE-ONLY as of WS-26d (spec ``crm_app.md`` §6/§9). Nothing
# in this repo writes ``entity_ref = "crm:<kind>:<uuid>"`` yet — the linker that
# would is a later slice — so adding it here means exactly one thing: a ref that
# somebody sets by hand parses into an ``EntityRef`` instead of being discarded
# as an unknown system. The ``crm`` block on ``ChatContextModel`` below stays
# ``None`` either way; filling it is that same later slice, and a test pins both
# halves so "we added the constant" is never mistaken for "the link works".
_KNOWN_SYSTEMS = {"zoho", "odoo", "clickup", "crm"}


class EntityRef(BaseModel):
    system: str            # 'zoho' | 'odoo' | 'clickup' | 'crm'
    kind: str              # e.g. 'contact' | 'partner' | 'lead' | 'deal'
    id: str


class ContextContact(BaseModel):
    phone_number: str
    display_name: str = ""
    category: str | None = None
    entity: EntityRef | None = None


class OpenLoop(BaseModel):
    id: str
    title: str
    disposition: str
    kind: str              # 'captured' (from this chat) | 'commitment'


class WaitingOnLoop(BaseModel):
    """A promise THEY made in this chat, still open — the drawer offers a
    one-tap AI nudge draft for each (W4.2). ``id`` is the wa_commitment id the
    nudge endpoint keys on."""
    id: str
    text: str
    due_hint: str | None = None


class ContextStats(BaseModel):
    message_count: int = 0
    first_seen: str | None = None
    last_seen: str | None = None


class ChatContextModel(BaseModel):
    chat_id: str
    contact: ContextContact | None = None
    open_loops: list[OpenLoop] = []
    # Promises they owe us in this chat — each nudgeable by id (W4.2).
    waiting_on: list[WaitingOnLoop] = []
    stats: ContextStats = ContextStats()
    # Live CRM/ERP fields (deal stage, overdue invoices) — filled in a later
    # phase. Null in W1; the entity ref above is what the UI deep-links on.
    crm: dict[str, Any] | None = None


def parse_entity_ref(ref: str | None) -> EntityRef | None:
    """Parse a ``<system>:<kind>:<id>`` link, or None when unset/unknown.

    Pure + testable. An unknown system or a malformed ref returns None so the UI
    treats the contact as unlinked rather than rendering a dead deep-link.
    """
    if not ref:
        return None
    parts = ref.split(":", 2)
    if len(parts) != 3:
        return None
    system, kind, ident = (p.strip() for p in parts)
    if system.lower() not in _KNOWN_SYSTEMS or not kind or not ident:
        return None
    return EntityRef(system=system.lower(), kind=kind, id=ident)


def _is_commitment(row: object) -> bool:
    """Whether a captured task's origin carries the commitment mark.

    The mark is written as JSON ``true`` by ``update_origin``; a row read
    through bare ``text()`` may hand the origin back as a string, so both
    shapes are read.
    """
    origin = getattr(row, "origin", None)
    if isinstance(origin, str):
        try:
            origin = json.loads(origin)
        except ValueError:
            return False
    if not isinstance(origin, dict):
        return False
    return origin.get("commitment") in (True, "true")


@router.get("/chats/{chat_id}/context", response_model=ChatContextModel)
async def chat_context(
    chat_id: str,
    user: UserContext = Depends(get_current_user),
):
    """Resolve the company context for a conversation."""
    uid = user.email or "anonymous"
    async with _tenant_session() as db:
        chat = (await db.execute(
            text("""SELECT c.id, c.wa_chat_id, c.name, c.category, c.kind
                    FROM wa_chats c
                    JOIN wa_accounts a ON a.id = c.account_id
                    WHERE c.id = :cid AND a.user_id = :uid"""),
            {"cid": chat_id, "uid": uid},
        )).fetchone()
        if chat is None:
            raise HTTPException(status_code=404, detail="Chat not found")

        # Contact identity (DMs map to a wa_contact by the chat's wa_id).
        contact_row = (await db.execute(
            text("""SELECT wc.phone_number, wc.display_name, wc.category,
                           wc.entity_ref
                    FROM wa_contacts wc
                    JOIN wa_chats c ON c.account_id = wc.account_id
                    WHERE c.id = :cid AND wc.wa_id = c.wa_chat_id
                    LIMIT 1"""),
            {"cid": chat_id},
        )).fetchone()
        contact = None
        if contact_row is not None:
            contact = ContextContact(
                phone_number=contact_row.phone_number,
                display_name=contact_row.display_name or "",
                category=contact_row.category,
                entity=parse_entity_ref(contact_row.entity_ref),
            )
        elif chat.kind == "dm":
            contact = ContextContact(
                phone_number=chat.wa_chat_id,
                display_name=chat.name or "",
                category=chat.category,
            )

        # Open loops: tasks captured from this chat (origin.wa_chat_id) that are
        # still open — the "you promised / this needs doing" the rail surfaces.
        from gateway.routes.tasks.item_source import item_source

        loop_rows = await item_source().items_by_origin(
            db, uid, "wa_chat_id", chat.wa_chat_id, limit=10)
        open_loops = [
            OpenLoop(
                id=str(r.id), title=r.title or "", disposition=r.disposition,
                kind="commitment" if _is_commitment(r) else "captured",
            )
            for r in loop_rows
        ]

        # Waiting on them: open promises they made in this chat, nudgeable (W4.2).
        waiting_rows = (await db.execute(
            text("""SELECT id, text, due_hint FROM wa_commitments
                    WHERE chat_id = :cid AND direction = 'theirs'
                      AND status = 'open'
                    ORDER BY created_at DESC LIMIT 5"""),
            {"cid": chat_id},
        )).fetchall()
        waiting_on = [
            WaitingOnLoop(id=str(r.id), text=r.text, due_hint=r.due_hint)
            for r in waiting_rows
        ]

        stat_row = (await db.execute(
            text("""SELECT COUNT(*) AS n, MIN(sent_at) AS first_at,
                           MAX(sent_at) AS last_at
                    FROM wa_messages WHERE chat_id = :cid"""),
            {"cid": chat_id},
        )).fetchone()
        stats = ContextStats(
            message_count=int(stat_row.n or 0) if stat_row else 0,
            first_seen=stat_row.first_at.isoformat()
            if stat_row and stat_row.first_at else None,
            last_seen=stat_row.last_at.isoformat()
            if stat_row and stat_row.last_at else None,
        )

        return ChatContextModel(
            chat_id=chat_id, contact=contact, open_loops=open_loops,
            waiting_on=waiting_on, stats=stats,
        )
