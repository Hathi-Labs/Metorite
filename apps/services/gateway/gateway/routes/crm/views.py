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

**Change history** (WS-CRM CRM-M1, ``crm_conversational.md`` §8). Each of the
three writes logs one row through ``change_log.log_change``, in its own
transaction, at the ``own`` level of the ladder. The row of a private view is
private to its owner (``private_to``), so the Change history never shows it to
another member, an admin too. The Undo goes through the helper of the route,
with its owner checks: a create goes, a change goes back, and a deleted view
comes back with its id and its owner.
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException, Query
from gateway.routes.crm import change_log
from gateway.routes.crm.access import ADMIN_PERMISSION
from gateway.routes.crm.change_log import Change, Logged, log_change, quoted
from gateway.routes.crm.core import (
    FIELD_ENTITY_BY_SLUG,
    _tenant_session,
    actor,
    checked_uuid,
    from_jsonb,
    router,
)
from pydantic import BaseModel, field_validator
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

_RANGE_KEYS = (
    "created_from", "created_to", "updated_from", "updated_to",
    "close_from", "close_to",
)
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
#: The most custom filters, and options for each, that a view holds.
MAX_CUSTOM_FILTERS = 20
MAX_CUSTOM_OPTIONS = 50

CORE_ENTITIES = ("leads", "deals", "contacts", "organizations")
#: A generic Zoho module list is a view scope too: ``module:<api_name>``
#: (CRM-U8). The name rule is the one of the URL grammar (``MODULE_NAME``).
MODULE_ENTITY = re.compile(r"module:[A-Za-z][A-Za-z0-9_]{0,99}")


def check_entity(value: str) -> str:
    """*value* when it names a core entity or ``module:<api_name>``, else 422-able."""
    if value in CORE_ENTITIES or MODULE_ENTITY.fullmatch(value or ""):
        return value
    raise ValueError(
        f"Unknown entity. One of {list(CORE_ENTITIES)} or module:<api_name>."
    )


def _entity_query(entity: str = Query(...)) -> str:
    try:
        return check_entity(entity)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

_COLUMNS = (
    "id, entity, name, owner_email, shared, layout, filters, sort, columns, "
    "pipeline_id, position, created_at, updated_at"
)


class ViewIn(BaseModel):
    entity: str
    name: str
    layout: Literal["list", "board"] = "list"
    filters: dict[str, Any] = {}
    sort: dict[str, Any] | None = None
    columns: list[str] | None = None
    pipeline_id: str | None = None
    shared: bool = False

    @field_validator("entity")
    @classmethod
    def _entity_known(cls, value: str) -> str:
        return check_entity(value)


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
    #: WS-53 CRM-C4a: the custom filter keys that this read left out, because
    #: their field is retired. The stored view keeps them, and they apply
    #: again after a restore. Without this, each load of the view gives 422.
    skipped_filters: list[str] = []
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
    for key in _RANGE_KEYS:
        value = filters.get(key)
        if value is not None and not (
            isinstance(value, str) and _ISO_DATE.fullmatch(value)
        ):
            raise HTTPException(
                status_code=422, detail=f"'{key}' must be an ISO date (YYYY-MM-DD).",
            )
    for key in ("q", "status", "owner", "source"):
        if filters.get(key) is not None and not isinstance(filters[key], str):
            raise HTTPException(status_code=422, detail=f"'{key}' must be text.")
    if filters.get("converted") is not None and not isinstance(
        filters["converted"], bool,
    ):
        raise HTTPException(status_code=422, detail="'converted' must be true or false.")
    custom = filters.get("custom")
    if custom is not None:
        # The UI reads each value with `.join`, so the shape is a list of text.
        if not isinstance(custom, dict) or len(custom) > MAX_CUSTOM_FILTERS:
            raise HTTPException(
                status_code=422, detail="'custom' must map a field key to a list of options.",
            )
        for key, items in custom.items():
            if not (
                isinstance(items, list)
                and len(items) <= MAX_CUSTOM_OPTIONS
                and all(isinstance(i, str) and 0 < len(i) <= 200 for i in items)
            ):
                raise HTTPException(
                    status_code=422,
                    detail=f"'custom.{key}' must be a list of up to "
                           f"{MAX_CUSTOM_OPTIONS} options, each text.",
                )
    _check_json("filters", filters)


def _check_shape(
    entity: str, layout: str | None, sort: dict[str, Any] | None,
    columns: list[str] | None, pipeline_id: str | None,
) -> str | None:
    """Check the parts of a view that the entity limits. Returns the pipeline id."""
    if layout == "board" and entity not in ("deals", "leads"):  # a module has none
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
    entity: str = Depends(_entity_query),
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
        retired = await _retired_keys(db, entity)
    return [_without_retired(_wire(r, me), retired) for r in rows]


async def _retired_keys(db: Any, entity: str) -> frozenset[str]:
    """The custom keys of *entity* whose field is retired (CRM-C4a)."""
    rows = (await db.execute(
        text("SELECT key, retired_at FROM crm_field_defs WHERE entity = :entity"),
        {"entity": FIELD_ENTITY_BY_SLUG.get(entity, entity)},
    )).fetchall()
    return frozenset(r.key for r in rows if getattr(r, "retired_at", None) is not None)


def _without_retired(view: ViewOut, retired: frozenset[str]) -> ViewOut:
    """*view* with each custom filter on a retired key left out, and named.

    The custom filter of the list refuses a retired key with 422 (§20.7 item
    11). The client sends a view's filters as they are, so the view read
    drops the key first. A list request that names a retired key is still 422.
    """
    custom = view.filters.get("custom")
    if not retired or not isinstance(custom, dict):
        return view
    skipped = sorted(k for k in custom if k in retired)
    if not skipped:
        return view
    kept = {k: v for k, v in custom.items() if k not in retired}
    return view.model_copy(update={
        "filters": {**view.filters, "custom": kept},
        "skipped_filters": skipped,
    })


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
        await log_change(db, user, Change(
            area="view", action=change_log.CREATE,
            summary=f"Saved the view {quoted(row.name)}",
            ladder=change_log.OWN, entity_id=str(row.id),
            after=_snapshot(row), private_to=_private_to(row),
        ))
    return _wire(row, me)


# ── Change history (CRM-M1) ─────────────────────────────────────────────────

#: The columns of a view that a Change history row keeps.
VIEW_LOG_KEYS: tuple[str, ...] = (
    "entity", "name", "layout", "filters", "sort", "columns", "pipeline_id",
    "shared", "position",
)
_JSON_KEYS = frozenset({"filters", "sort", "columns"})

#: Each field of a view, in the words of a summary.
_VIEW_WORDS: dict[str, str] = {
    "name": "name", "layout": "layout", "filters": "filters", "sort": "sort",
    "columns": "columns", "pipeline_id": "pipeline", "shared": "sharing",
    "position": "place",
}


def _snapshot(row: Any, *, owner: bool = False) -> dict[str, Any]:
    """The logged values of a view. A deleted view keeps its owner too, so
    its Undo can bring it back to that owner."""
    out = {
        k: from_jsonb(getattr(row, k, None)) if k in _JSON_KEYS else getattr(row, k, None)
        for k in VIEW_LOG_KEYS
    }
    if owner:
        out["owner_email"] = (row.owner_email or "").lower()
    return out


def _private_to(*rows: Any) -> str | None:
    """The owner, when a state of the view is private. Else None."""
    if all(bool(getattr(r, "shared", False)) for r in rows):
        return None
    return (rows[0].owner_email or "").lower() or None


def _view_summary(row: Any, before: dict[str, Any], after: dict[str, Any]) -> str:
    if set(after) == {"name"}:
        return f"Renamed the view {quoted(before['name'])} to {quoted(after['name'])}"
    words = sorted({_VIEW_WORDS.get(k, k) for k in after})
    return f"Changed the view {quoted(row.name)}: {', '.join(words)}"


@router.patch("/views/{view_id}", response_model=ViewOut)
async def patch_view(
    view_id: str, payload: ViewPatch, user: UserContext = Depends(get_current_user),
) -> ViewOut:
    """Change a view. The owner may, and so may an admin (on a view they can see)."""
    me = actor(user).lower()
    async with _tenant_session() as db:
        row = await patch_view_in(db, user, view_id, payload)
    return _wire(row, me)


async def patch_view_in(
    db: Any, user: Any, view_id: str, payload: ViewPatch,
    *, undoes: str | None = None,
) -> Any:
    """The body of ``PATCH /crm/views/{id}``, in the caller's transaction.
    Returns the row. The route and the Undo of a change both call it, with
    the same owner checks. It logs one row when a value changes (CRM-M1)."""
    me = actor(user).lower()
    admin = _is_admin(user)
    given = payload.model_fields_set
    if payload.shared and not admin:
        raise HTTPException(status_code=403, detail="Only an admin shares a view.")
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
    if not sets:
        return row
    updated = (await db.execute(
        text(
            f"UPDATE crm_views SET {', '.join(sets)}, updated_at = now() "
            f"WHERE id = CAST(:id AS uuid) RETURNING {_COLUMNS}"
        ),
        bound,
    )).first()
    keys = [k for k in VIEW_LOG_KEYS if k in given]
    old, new = _snapshot(row), _snapshot(updated)
    before, after = change_log.diff(
        {k: old[k] for k in keys}, {k: new[k] for k in keys},
    )
    if after:
        await log_change(db, user, Change(
            area="view", action=change_log.CHANGE,
            summary=_view_summary(row, before, after),
            ladder=change_log.OWN, entity_id=str(row.id),
            before=before, after=after, private_to=_private_to(row, updated),
        ), undoes=undoes)
    return updated


@router.delete("/views/{view_id}")
async def delete_view(
    view_id: str, user: UserContext = Depends(get_current_user),
) -> dict[str, str]:
    """Delete a view. The owner may, and so may an admin."""
    async with _tenant_session() as db:
        row = await delete_view_in(db, user, view_id)
    return {"deleted": str(row.id)}


async def delete_view_in(
    db: Any, user: Any, view_id: str, *, undoes: str | None = None,
) -> Any:
    """The body of ``DELETE /crm/views/{id}``, in the caller's transaction.
    Returns the row that went. The route and the Undo of a create both call
    it, with the same owner check. It logs one row (CRM-M1)."""
    me = actor(user).lower()
    row = await _load(db, view_id, me)
    if not _is_admin(user) and (row.owner_email or "").lower() != me:
        raise HTTPException(
            status_code=403, detail="Only the owner or an admin deletes a view.",
        )
    await db.execute(
        text("DELETE FROM crm_views WHERE id = CAST(:id AS uuid)"),
        {"id": str(row.id)},
    )
    await log_change(db, user, Change(
        area="view", action=change_log.DELETE,
        summary=f"Deleted the view {quoted(row.name)}",
        ladder=change_log.OWN, entity_id=str(row.id),
        before=_snapshot(row, owner=True), private_to=_private_to(row),
    ), undoes=undoes)
    return row


#: A deleted view comes back with its own id and its own owner.
_RESTORE_SQL = (
    "INSERT INTO crm_views (id, entity, name, owner_email, shared, layout, "
    "filters, sort, columns, pipeline_id, position) VALUES "
    "(CAST(:id AS uuid), :entity, :name, :owner, :shared, :layout, "
    "CAST(:filters AS jsonb), CAST(:sort AS jsonb), "
    "CAST(:columns AS jsonb), CAST(:pipeline AS uuid), :position) "
    f"RETURNING {_COLUMNS}"
)


@change_log.undoes("view")
async def _undo_view(db: Any, user: Any, logged: Logged) -> None:
    """A create goes, a change goes back, and a deleted view comes back.

    Each path keeps the owner rule of the route. A deleted view comes back
    only for its owner, or for an admin when the view was shared.
    """
    view_id = str(logged.entity_id or "")
    if logged.action == change_log.CREATE:
        await delete_view_in(db, user, view_id, undoes=logged.id)
        return
    if logged.action == change_log.CHANGE:
        current = (await db.execute(
            text(f"SELECT {_COLUMNS} FROM crm_views WHERE id = CAST(:id AS uuid)"),
            {"id": view_id},
        )).first()
        if current is None:
            raise change_log.gone(logged.area)
        if not change_log.same_values(_snapshot(current), logged.after):
            raise change_log.changed_since()
        await patch_view_in(
            db, user, view_id, ViewPatch(**(logged.before or {})), undoes=logged.id,
        )
        return
    before = dict(logged.before or {})
    owner = str(before.get("owner_email") or "").lower()
    me = actor(user).lower()
    if owner != me and not (bool(before.get("shared")) and _is_admin(user)):
        raise HTTPException(
            status_code=403, detail="Only the owner or an admin brings a view back.",
        )
    if (await db.execute(
        text("SELECT id FROM crm_views WHERE id = CAST(:id AS uuid)"), {"id": view_id},
    )).first() is not None:
        raise change_log.changed_since()
    entity = check_entity(str(before.get("entity") or ""))
    name = _clean_name(str(before.get("name") or ""))
    filters = before.get("filters") or {}
    _check_filters(filters)
    pipeline = _check_shape(
        entity, before.get("layout"), before.get("sort"), before.get("columns"),
        before.get("pipeline_id"),
    )
    row = (await db.execute(text(_RESTORE_SQL), {
        "id": view_id, "entity": entity, "name": name, "owner": owner,
        "shared": bool(before.get("shared")), "layout": before.get("layout") or "list",
        "filters": json.dumps(filters),
        "sort": json.dumps(before["sort"]) if before.get("sort") is not None else None,
        "columns": (
            json.dumps(before["columns"]) if before.get("columns") is not None else None
        ),
        "pipeline": pipeline, "position": int(before.get("position") or 0),
    })).first()
    await log_change(db, user, Change(
        area="view", action=change_log.CREATE,
        summary=f"Brought back the view {quoted(row.name)}",
        ladder=change_log.OWN, entity_id=str(row.id),
        after=_snapshot(row), private_to=_private_to(row),
    ), undoes=logged.id)
