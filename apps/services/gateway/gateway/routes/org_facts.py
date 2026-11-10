"""Org facts · the one read route of the facts of the org (WS-53 CRM-Q32-5).

    GET /org/facts?app=&q=    the facts of the caller's org

Spec: ``project-docs/specs/crm_platform.md`` §18.14.4. The seam is
``gateway/org_facts.py``: each app registers one provider, and this route
reads each of them in ONE tenant session. ``app`` keeps one app, and ``q``
matches the subject and the meaning, case-insensitive, in code.

**Who may read.** Any member of an org. The organization comes from the
server (``UserContext.organization_id``), never from the request (R5, R11).
Each provider applies the access rule of its app, so a member with no CRM
access gets no CRM fact. The agent tool ``org_facts`` reads this route as
the member.
"""

from __future__ import annotations

from acb_auth import UserContext, get_current_user
from fastapi import APIRouter, Depends, HTTPException, Query
from gateway import org_facts
from gateway.db import tenant_session as _tenant_session
from pydantic import BaseModel

router = APIRouter(prefix="/org", tags=["org-facts"])


class OrgFactModel(BaseModel):
    """One fact (§18.14.4, the row shape)."""

    app: str
    subject: str
    key: str
    value: str | None = None
    meaning: str
    #: ``code``, ``ai`` or ``admin``.
    source: str
    #: The time of the plan version that holds the part.
    set_at: str | None = None
    #: ``admin``, ``Metorite`` or ``Jev``. Never an email.
    set_by: str


class OrgFactsModel(BaseModel):
    facts: list[OrgFactModel]


@router.get("/facts", response_model=OrgFactsModel)
async def get_org_facts(
    app: str | None = Query(None, max_length=40),
    q: str | None = Query(None, max_length=200),
    user: UserContext = Depends(get_current_user),
) -> OrgFactsModel:
    """The facts of the caller's org, from each provider of the seam. It
    reads only."""
    if not user.organization_id:
        raise HTTPException(status_code=403, detail="Forbidden: you are in no organization.")
    async with _tenant_session() as db:
        found = await org_facts.read(db, user, app=app, q=q)
    return OrgFactsModel(facts=[OrgFactModel(**row.as_json()) for row in found])


__all__ = ["OrgFactModel", "OrgFactsModel", "get_org_facts", "router"]
