"""CRM · deal contacts — who on the customer's side this deal runs through.

Spec: ``project-docs/specs/crm_app.md`` §3.5, §9 WS-26c done-when 2.

    GET    /crm/deals/{id}/contacts
    POST   /crm/deals/{id}/contacts               {contact_id, role?, is_primary?}
    DELETE /crm/deals/{id}/contacts/{contact_id}

WS-26a created ``crm_deal_contacts`` and wrote exactly one row into it, from
the convert path. Nothing could read the table, nothing could add a second
contact, and "at most one primary per deal" was true only because there was
never more than one row. This module is the read/write surface the record
sheet needs, and the primary rule now lives on the shared
``core.link_deal_contact`` seam that convert also goes through — so the rule
belongs to the table, not to whichever endpoint happened to write it.

There is deliberately **no** partial unique index behind it (§3.5): the WS-26b
importer sets primaries in a pass that would fight one. That makes the code
path the only enforcement, which is exactly why it is one path.
"""

from __future__ import annotations

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException
from gateway.routes.crm.core import (
    CONTACTS,
    DEALS,
    ContactModel,
    _tenant_session,
    assert_writable,
    current_tenant,
    link_deal_contact,
    require_row,
    router,
    row_to_dict,
)
from pydantic import BaseModel
from sqlalchemy import text


class DealContact(BaseModel):
    """One contact on a deal — the person's card plus the link's own fields."""

    contact: dict
    role: str | None = None
    is_primary: bool = False


class DealContactsResponse(BaseModel):
    rows: list[DealContact]


class DealContactIn(BaseModel):
    contact_id: str
    #: Both of these are tri-state — ``None`` is "leave it as it is", which is
    #: what an absent field means. ``is_primary`` defaulting to ``False`` made
    #: "set this person's role" demote the deal's primary as a side effect.
    role: str | None = None
    is_primary: bool | None = None


@router.get("/deals/{deal_id}/contacts", response_model=DealContactsResponse)
async def list_deal_contacts(
    deal_id: str, user: UserContext = Depends(get_current_user),
) -> DealContactsResponse:
    """The deal's people, primary first.

    Returns the whole contact row rather than an id: the record sheet's right
    panel prints a name, a title and a phone number, and a payload of ids would
    make it issue one request per person to render a card.
    """
    async with _tenant_session() as db:
        await require_row(db, DEALS.table, deal_id, DEALS.label)
        rows = (await db.execute(
            text(
                "SELECT c.*, dc.role AS link_role, dc.is_primary AS link_is_primary "
                "FROM crm_deal_contacts dc "
                "JOIN crm_contacts c ON c.id = dc.contact_id "
                "WHERE dc.deal_id = CAST(:deal_id AS uuid) "
                # The id breaks a tie of the name: the chat pages this list.
                "ORDER BY dc.is_primary DESC, c.first_name, c.id"
            ),
            {"deal_id": deal_id},
        )).fetchall()
        return DealContactsResponse(rows=[
            DealContact(
                contact=row_to_dict(row, ContactModel),
                role=getattr(row, "link_role", None),
                is_primary=bool(getattr(row, "link_is_primary", False)),
            )
            for row in rows
        ])


@router.post("/deals/{deal_id}/contacts", status_code=201)
async def add_deal_contact(
    deal_id: str, payload: DealContactIn,
    user: UserContext = Depends(get_current_user),
) -> DealContact:
    """Attach a contact, or change the role/primary flag of one already on it.

    Promoting a contact to primary demotes the incumbent in the same
    transaction (``core.link_deal_contact``) — the whole point of the endpoint
    beyond the row it writes.
    """
    async with _tenant_session() as db:
        deal = await require_row(db, DEALS.table, deal_id, DEALS.label)
        await assert_writable(db, current_tenant(), DEALS, None, row=deal)
        contact = await require_row(
            db, CONTACTS.table, payload.contact_id, CONTACTS.label,
        )
        link = await link_deal_contact(
            db, deal_id, str(contact.id),
            role=payload.role, is_primary=payload.is_primary,
        )
        return DealContact(
            contact=row_to_dict(contact, ContactModel),
            role=getattr(link, "role", None),
            is_primary=bool(getattr(link, "is_primary", False)),
        )


@router.delete("/deals/{deal_id}/contacts/{contact_id}")
async def remove_deal_contact(
    deal_id: str, contact_id: str,
    user: UserContext = Depends(get_current_user),
) -> dict:
    """Detach a contact from a deal. The contact record itself is untouched.

    404 when the link does not exist, rather than a cheerful 200: "remove Priya
    from this deal" answered OK while Priya is still on it is the response that
    makes the UI re-render her and look broken.
    """
    async with _tenant_session() as db:
        deal = await require_row(db, DEALS.table, deal_id, DEALS.label)
        await assert_writable(db, current_tenant(), DEALS, None, row=deal)
        existing = (await db.execute(
            text(
                "SELECT * FROM crm_deal_contacts "
                "WHERE deal_id = CAST(:deal_id AS uuid) "
                "AND contact_id = CAST(:contact_id AS uuid)"
            ),
            {"deal_id": deal_id, "contact_id": contact_id},
        )).fetchone()
        if existing is None:
            raise HTTPException(
                status_code=404, detail="That contact is not on this deal.",
            )
        await db.execute(
            text(
                "DELETE FROM crm_deal_contacts "
                "WHERE deal_id = CAST(:deal_id AS uuid) "
                "AND contact_id = CAST(:contact_id AS uuid)"
            ),
            {"deal_id": deal_id, "contact_id": contact_id},
        )
        # A deal whose primary contact was just removed has none. Saying so
        # lets the sheet prompt for a new one instead of silently showing a
        # deal that nobody is the contact for.
        return {
            "deleted": contact_id,
            "deal_id": deal_id,
            "was_primary": bool(getattr(existing, "is_primary", False)),
        }


__all__ = ["DealContact", "DealContactIn", "DealContactsResponse"]
