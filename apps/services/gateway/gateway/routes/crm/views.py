"""CRM · views — saved views of the CRM lists and boards (WS-53 CRM-Q8, CRM-S2).

Spec: ``project-docs/specs/crm_platform.md`` §5.3, §6 and §18.5.

    GET    /crm/views?entity=       the caller's own views and the shared ones
    POST   /crm/views               create a view (private, unless an admin shares it)
    PATCH  /crm/views/{id}          rename, change or share (the owner, or an admin)
    DELETE /crm/views/{id}          the owner, or an admin

A view is a saved list: the filters, the sort, the chosen custom columns, the
layout and the deal pipeline. It follows the shape of ``pm_views``.

**Who sees what.** A private view belongs to the member who made it. Row level
security binds the organization, and the SQL here binds the owner, so another
member never reads a private view and never learns that it exists (a PATCH or a
DELETE of it answers 404). A shared view shows to the whole organization. The
members that did not make it may read it and may not change it (403), unless
they are admins.

**Who may share.** Only a holder of ``admin:access:manage`` sets ``shared``.

**Mirror mode.** A view is not a CRM record. The routes write ``crm_views`` and
never a record table, so they work in Mirror mode as well. The write guard
(``assert_writable``) has no part in them, and ``test_crm_mirror_readonly.py``
names this file as exempt.

The owner and the organization come from the session (R5). A request body names
neither.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException, Query
from gateway.routes.crm.access import ADMIN_PERMISSION
from gateway.routes.crm.core import (
    _tenant_session,
    actor,
    checked_uuid,
    router,
)
from pydantic import BaseModel
from sqlalchemy import text

#: The longest name a view may have.
MAX_NAME = 120
#: The largest JSON a view may hold in ``filters``, ``sort`` or ``columns``.
MAX_JSON_BYTES = 8000
#: The filter keys that a view may carry. They are the URL names of the list.
FILTER_KEYS = frozenset({
    "q", "status", "owner", "source", "converted",
    "created_from", "created_to", "updated_from", "updated_to",
    "close_from", "close_to", "custom",
})

EntityName = Literal["leads", "deals", "contacts", "organizations"]

_COLUMNS = (
    "id, entity, name, owner_email, shared, layout, filters, sort, columns, "
    "pipeline_id, position, created_at, updated_at"
)


class ViewIn(BaseModel):
    entity: EntityName
    name: str
    layout: Literal["list", "board"] = "list"
    filters: dict[str, Any] = {}
    sort: dict[str, Any] | None = None
    columns: list[str] | None = None
    pipeline_id: str | None = None
    shared: bool = False


class ViewPatch(BaseModel):
    name: str | None = None
    layout: Literal["list", "board"] | None = None
    filters: dict[str, Any] | None = None
    sort: dict[str, Any] | None = None
    columns: list[str] | None = None
    pipeline_id: str | None = None
    shared: bool | None = None
    position: int | None = None


class ViewOut(BaseModel):
    id: str
    entity: str
    name: str
    owner_email: str
    shared: bool
    layout: str
    filters: dict[str, Any]
    sort: dict[str, Any] | None = None
    columns: list[str] | None = None
    pipeline_id: str | None = None
    position: int = 0
    #: True when the caller made the view. The UI shows edit controls on it.
    mine: bool = False
    created_at: str | None = None
    updated_at: str | None = None


def _wire(row: Any, me: str) -> ViewOut:
    def iso(value: Any) -> str | None:
        return value.isoformat() if value is not None else None

    return ViewOut(
        id=str(row.id), entity=row.entity, name=row.name,
        owner_email=row.owner_email, shared=bool(row.shared), layout=row.layout,
        filters=row.filters or {}, sort=row.sort, columns=row.columns,
        pipeline_id=str(row.pipeline_id) if row.pipeline_id else None,
        position=int(row.position or 0),
        mine=(row.owner_email or "").lower() == me,
        created_at=iso(row.created_at), updated_at=iso(row.updated_at),
    )


def _clean_name(name: str) -> str:
    cleaned = " ".join((name or "").split())
    if not cleaned:
        raise HTTPException(status_code=422, detail="A view needs a name.")
    if len(cleaned) > MAX_NAME:
        raise HTTPException(
            status_code=422, detail=f"A view name has at most {MAX_NAME} characters.",
        )
    return cleaned


def _check_json(label: str, value: Any) -> None:
    if len(json.dumps(value)) > MAX_JSON_BYTES:
        raise HTTPException(status_code=422, detail=f"The view {label} is too large.")


def _check_filters(filters: dict[str, Any]) -> None:
    unknown = sorted(set(filters) - FILTER_KEYS)
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown filter {unknown}. One of: {sorted(FILTER_KEYS)}.",
        )
    custom = filters.get("custom")
    if custom is not None and not isinstance(custom, dict):
        raise HTTPException(
            status_code=422, detail="'custom' must map a field key to a value.",
        )
    _check_json("filters", filters)


def _check_shape(
    entity: str, layout: str | None, sort: dict[str, Any] | None,
    columns: list[str] | None, pipeline_id: str | None,
) -> str | None:
    """Check the parts of a view that the entity limits. Returns the pipeline id."""
    if layout == "board" and entity not in ("deals", "leads"):
        raise HTTPException(
            status_code=422, detail=f"{entity} have no board. Use the list layout.",
        )
    if sort is not None:
        _check_json("sort", sort)
    if columns is not None:
        if len(columns) > 50:
            raise HTTPException(status_code=422, detail="A view has at most 50 columns.")
        _check_json("columns", columns)
    if pipeline_id:
        if entity != "deals":
            raise HTTPException(
                status_code=422, detail="Only a deals view names a pipeline.",
            )
        return checked_uuid(pipeline_id, "pipeline_id")
    return None


def _is_admin(user: UserContext) -> bool:
    return bool(user.has_permission(ADMIN_PERMISSION))


async def _load(db: Any, view_id: str, me: str) -> Any:
    """The view, or a 404. Another member's private view is a 404 as well, so
    its id does not leak."""
    row = (await db.execute(
        text(f"SELECT {_COLUMNS} FROM crm_views WHERE id = CAST(:id AS uuid)"),
        {"id": checked_uuid(view_id, "id")},
    )).first()
    if row is None or not (row.shared or (row.owner_email or "").lower() == me):
        raise HTTPException(status_code=404, detail="View not found.")
    return row


@router.get("/views", response_model=list[ViewOut])
async def list_views(
    entity: EntityName = Query(...),
    user: UserContext = Depends(get_current_user),
) -> list[ViewOut]:
    """The caller's own views and the shared ones for one entity."""
    me = actor(user).lower()
    async with _tenant_session() as db:
        rows = (await db.execute(
            text(
                f"SELECT {_COLUMNS} FROM crm_views WHERE entity = :entity "
                "AND (shared OR lower(owner_email) = :me) "
                "ORDER BY position, created_at, id"
            ),
            {"entity": entity, "me": me},
        )).fetchall()
    return [_wire(r, me) for r in rows]


@router.post("/views", response_model=ViewOut, status_code=201)
async def create_view(
    payload: ViewIn, user: UserContext = Depends(get_current_user),
) -> ViewOut:
    """Create a view. It is private unless an admin asks for ``shared``."""
    me = actor(user).lower()
    if payload.shared and not _is_admin(user):
        raise HTTPException(status_code=403, detail="Only an admin shares a view.")
    name = _clean_name(payload.name)
    _check_filters(payload.filters)
    pipeline = _check_shape(
        payload.entity, payload.layout, payload.sort, payload.columns,
        payload.pipeline_id,
    )
    async with _tenant_session() as db:
        nxt = (await db.execute(
            text("SELECT coalesce(max(position), -1) + 1 FROM crm_views "
                 "WHERE entity = :entity"),
            {"entity": payload.entity},
        )).scalar()
        row = (await db.execute(
            text(
                "INSERT INTO crm_views (entity, name, owner_email, shared, layout, "
                "filters, sort, columns, pipeline_id, position) VALUES "
                "(:entity, :name, :me, :shared, :layout, "
                "CAST(:filters AS jsonb), CAST(:sort AS jsonb), "
                "CAST(:columns AS jsonb), CAST(:pipeline AS uuid), :position) "
                f"RETURNING {_COLUMNS}"
            ),
            {
                "entity": payload.entity, "name": name, "me": me,
                "shared": payload.shared, "layout": payload.layout,
                "filters": json.dumps(payload.filters),
                "sort": json.dumps(payload.sort) if payload.sort is not None else None,
                "columns": (
                    json.dumps(payload.columns) if payload.columns is not None else None
                ),
                "pipeline": pipeline, "position": int(nxt or 0),
            },
        )).first()
    return _wire(row, me)


@router.patch("/views/{view_id}", response_model=ViewOut)
async def patch_view(
    view_id: str, payload: ViewPatch, user: UserContext = Depends(get_current_user),
) -> ViewOut:
    """Change a view. The owner may, and so may an admin (on a view they can see)."""
    me = actor(user).lower()
    admin = _is_admin(user)
    given = payload.model_fields_set
    if payload.shared and not admin:
        raise HTTPException(status_code=403, detail="Only an admin shares a view.")
    async with _tenant_session() as db:
        row = await _load(db, view_id, me)
        if not admin and (row.owner_email or "").lower() != me:
            raise HTTPException(
                status_code=403, detail="Only the owner or an admin changes a view.",
            )
        sets: list[str] = []
        bound: dict[str, Any] = {"id": str(row.id)}
        layout = payload.layout if payload.layout is not None else row.layout
        if "name" in given:
            sets.append("name = :name")
            bound["name"] = _clean_name(payload.name or "")
        if "filters" in given and payload.filters is not None:
            _check_filters(payload.filters)
            sets.append("filters = CAST(:filters AS jsonb)")
            bound["filters"] = json.dumps(payload.filters)
        pipeline = _check_shape(
            row.entity, layout,
            payload.sort if "sort" in given else None,
            payload.columns if "columns" in given else None,
            payload.pipeline_id if "pipeline_id" in given else None,
        )
        if "layout" in given and payload.layout is not None:
            sets.append("layout = :layout")
            bound["layout"] = payload.layout
        if "sort" in given:
            sets.append("sort = CAST(:sort AS jsonb)")
            bound["sort"] = (
                json.dumps(payload.sort) if payload.sort is not None else None
            )
        if "columns" in given:
            sets.append("columns = CAST(:columns AS jsonb)")
            bound["columns"] = (
                json.dumps(payload.columns) if payload.columns is not None else None
            )
        if "pipeline_id" in given:
            sets.append("pipeline_id = CAST(:pipeline AS uuid)")
            bound["pipeline"] = pipeline
        if "shared" in given and payload.shared is not None:
            sets.append("shared = :shared")
            bound["shared"] = payload.shared
        if "position" in given and payload.position is not None:
            sets.append("position = :position")
            bound["position"] = payload.position
        if sets:
            row = (await db.execute(
                text(
                    f"UPDATE crm_views SET {', '.join(sets)}, updated_at = now() "
                    f"WHERE id = CAST(:id AS uuid) RETURNING {_COLUMNS}"
                ),
                bound,
            )).first()
    return _wire(row, me)


@router.delete("/views/{view_id}")
async def delete_view(
    view_id: str, user: UserContext = Depends(get_current_user),
) -> dict[str, str]:
    """Delete a view. The owner may, and so may an admin."""
    me = actor(user).lower()
    async with _tenant_session() as db:
        row = await _load(db, view_id, me)
        if not _is_admin(user) and (row.owner_email or "").lower() != me:
            raise HTTPException(
                status_code=403, detail="Only the owner or an admin deletes a view.",
            )
        await db.execute(
            text("DELETE FROM crm_views WHERE id = CAST(:id AS uuid)"),
            {"id": str(row.id)},
        )
    return {"deleted": str(row.id)}
