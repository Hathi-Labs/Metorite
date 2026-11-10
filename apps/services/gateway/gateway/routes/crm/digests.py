"""CRM · digests — the weekly digests of the org (WS-53 CRM-Q28).

Spec: ``project-docs/specs/crm_platform.md`` §18.11.2, CRM-Q28 items 13
and 14.

    GET /crm/digests?limit=4    the newest digests that hold a text

**Read only.** The route sits on the shared ``core.router``, so the kill
switch, ``feature:crm`` and ``require_crm_access`` apply before this body
runs. A digest counts the whole org, so the access rule of the CRM is the
whole gate. The org comes from the signed-in member, never from the
request (R5), and each statement names it too.

**Only a stored text.** ``crm_sync/digest.py`` stores ``body`` only after
the text passed the number check of item 9. A row with no body is a try
that failed, and no read here returns it.

**My Day** (item 14). :func:`ready_digest` gives the newest digest that was
stored inside the last :data:`READY_FOR`. The feed's ``crm`` provider reads
it through ``aggregate.my_day_rows``, in the same session as its other
reads. The card shows a code template and never the text.

Fences: ``tests/unit/test_crm_digest.py`` and
``tests/unit/test_crm_digest_r8.py`` (R8).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends, Query
from gateway.routes.crm.connection import _organization
from gateway.routes.crm.core import _tenant_session, router
from pydantic import BaseModel
from sqlalchemy import text

#: The most digests one read gives.
MAX_LIMIT = 12

#: How long the My Day card shows after a digest is stored (item 14).
READY_FOR = timedelta(days=1)

#: The newest digests with a text. The org is in the predicate as well as in
#: the policy, so a session with RLS off still reads one org.
_LIST_SQL = (
    "SELECT week_start, numbers, body, created_at, stored_at FROM crm_digests "
    "WHERE organization_id = CAST(:o AS uuid) AND body IS NOT NULL "
    "ORDER BY week_start DESC LIMIT :n"
)

#: The newest digest stored after ``:since``, for the My Day card.
READY_SQL = (
    "SELECT week_start, stored_at FROM crm_digests "
    "WHERE organization_id = CAST(:o AS uuid) AND body IS NOT NULL "
    "AND stored_at >= :since ORDER BY week_start DESC LIMIT 1"
)


class Digest(BaseModel):
    #: The Monday of the counted week, in the CRM zone.
    week_start: date
    #: The Sunday of the counted week.
    week_end: date
    #: The numbers of the CRM-Q25 aggregates, as the prompt held them.
    numbers: dict[str, Any]
    #: The text. Each number in it is in ``numbers``.
    text: str
    created_at: datetime
    stored_at: datetime | None = None


class DigestsResponse(BaseModel):
    digests: list[Digest]


def _numbers(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        import json

        try:
            value = json.loads(raw)
        except ValueError:
            return {}
        return value if isinstance(value, dict) else {}
    return {}


@router.get("/digests", response_model=DigestsResponse)
async def list_digests(
    limit: int = Query(4, ge=1, le=MAX_LIMIT),
    user: UserContext = Depends(get_current_user),
) -> DigestsResponse:
    """The newest weekly digests of the org, newest first.

    The org comes from the signed-in member (R5), as in ``get_crm_counts``.
    """
    org = _organization(user)
    async with _tenant_session() as db:
        rows = (await db.execute(text(_LIST_SQL), {"o": str(org), "n": limit})).fetchall()
    return DigestsResponse(digests=[
        Digest(
            week_start=r.week_start, week_end=r.week_start + timedelta(days=6),
            numbers=_numbers(r.numbers), text=str(r.body), created_at=r.created_at,
            stored_at=r.stored_at,
        )
        for r in rows
    ])


async def ready_digest(db: Any, org: str, now: datetime) -> dict[str, Any] | None:
    """The newest digest stored inside :data:`READY_FOR` before ``now``, as
    ``{week_start, stored_at}``, or None. The caller gives the session."""
    row = (await db.execute(text(READY_SQL), {
        "o": str(org), "since": now - READY_FOR,
    })).first()
    if row is None:
        return None
    return {"week_start": row.week_start, "stored_at": row.stored_at}


__all__ = ["MAX_LIMIT", "READY_FOR", "READY_SQL", "Digest", "DigestsResponse",
           "list_digests", "ready_digest"]
