"""CRM · fields — what each custom field means (WS-53 CRM-Q4).

Spec: ``project-docs/specs/crm_platform.md`` §18.3 step 2 item 3 and §18.5.

    GET  /crm/fields/{entity}

``entity`` is one of ``leads``, ``contacts``, ``organizations`` and ``deals``.
The route reads ``crm_field_defs``, which the sync writes, and it returns the
rows in order of ``position``. It lets the UI show a custom field with its label,
its order and its type. Without it, the UI has only the key.

Read only. Any member that the CRM access rule admits may read it. The
organization comes from the server and the session binds it, so the route
never takes a tenant from the request (R5).
"""

from __future__ import annotations

from typing import Any, Literal

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException
from gateway.routes.crm.core import _tenant_session, router
from pydantic import BaseModel
from sqlalchemy import text

#: The API slug of each entity → the value that ``crm_field_defs.entity`` holds.
#: The sync writes the singular form (``crm_sync.engine.FIELD_DEF_ENTITIES``).
ENTITY_BY_SLUG: dict[str, str] = {
    "leads": "lead",
    "contacts": "contact",
    "organizations": "company",
    "deals": "deal",
}

_FIELDS_SQL = (
    "SELECT key, label, type, options, source, provider_api_name, position, "
    "visible FROM crm_field_defs WHERE entity = :entity "
    "ORDER BY position, key"
)


class FieldDef(BaseModel):
    key: str
    label: str
    type: str
    options: list[Any] | None = None
    position: int
    visible: bool
    source: Literal["zoho", "native"]
    api_name: str | None = None


def _source(value: str | None) -> Literal["zoho", "native"]:
    return "zoho" if value == "zoho" else "native"


@router.get("/fields/{entity}", response_model=list[FieldDef])
async def list_fields(
    entity: str, user: UserContext = Depends(get_current_user),
) -> list[FieldDef]:
    """The field definitions of one entity, ordered by position."""
    stored = ENTITY_BY_SLUG.get(entity)
    if stored is None:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown entity '{entity}'. One of: {list(ENTITY_BY_SLUG)}.",
        )
    async with _tenant_session() as db:
        rows = (await db.execute(text(_FIELDS_SQL), {"entity": stored})).fetchall()
    return [
        FieldDef(
            key=r.key, label=r.label, type=r.type,
            options=r.options if isinstance(r.options, list) else None,
            position=int(r.position or 0), visible=bool(r.visible),
            source=_source(r.source), api_name=r.provider_api_name,
        )
        for r in rows
    ]
