"""Projects · views — saved views and per-view manual order.

Spec: ``project-docs/specs/project_management_app.md`` §4 (``views.py`` row).

    GET    /projects/nodes/{project_id}/views
    POST   /projects/nodes/{project_id}/views
    PATCH  /projects/views/{view_id}
    DELETE /projects/views/{view_id}
    GET    /projects/views/{view_id}/positions
    PUT    /projects/views/{view_id}/positions      → bulk upsert
    GET    /projects/views/{view_id}/state          → the CALLER's overlay
    PUT    /projects/views/{view_id}/state

**Ordering is per view** (D-PM-5). There is no rank column on ``pm_tasks``,
because the People Center's master board and a Center slice must be able to
order the same task differently — one column cannot serve two views without
them fighting over it. Positions are floats and a drag writes exactly one row:
between two neighbours is ``(prev + next) / 2``.
"""

from __future__ import annotations

import json
from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException
from gateway.routes.projects.core import (
    ViewModel,
    _tenant_session,
    actor,
    clean_payload,
    from_jsonb,
    insert_row,
    load_visible_project,
    require_row,
    resolve_visibility,
    router,
    row_to_dict,
    task_visibility_clause,
    update_row,
)
from gateway.routes.projects.filters import (
    normalise_view_config,
    normalise_view_user_state,
)
from pydantic import BaseModel
from sqlalchemy import text

VIEW_TYPES: tuple[str, ...] = ("list", "board")

#: The ceiling on one bulk position write. Generous — a board column
#: materialising its order sends every task in the group — but not unbounded: a
#: single request that can write the whole table is a denial-of-service surface
#: rather than a feature.
MAX_POSITIONS = 1000


class ViewIn(BaseModel):
    name: str | None = None
    view_type: str | None = None
    config: dict | None = None
    position: float | None = None


class PositionIn(BaseModel):
    task_id: str
    position: float
    group_key: str | None = None


class PositionsIn(BaseModel):
    positions: list[PositionIn]


class UserStateIn(BaseModel):
    config: dict | None = None


@router.get("/nodes/{project_id}/views")
async def list_views(
    project_id: str, user: UserContext = Depends(get_current_user),
) -> dict:
    """Every view on this project, each carrying the CALLER's own overlay.

    ``user_state`` is attached here rather than left to a second round trip
    because the board already makes this call and needs both halves before it
    can paint: fetching the overlay separately would mean one frame drawn with
    the shared collapse state and a second with the member's.

    ⚠️ **The overlay is filtered to the caller, in the SQL** — never fetched
    for the view and picked apart afterwards. `member` is
    :func:`core.actor`'s answer for the authenticated session (R11: never
    anything the request sent), so a member cannot read what a colleague has
    collapsed, and the endpoint cannot start leaking it by an oversight in a
    later projection step.
    """
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        await load_visible_project(db, vis, project_id)
        rows = (await db.execute(
            text(
                "SELECT * FROM pm_views WHERE project_id = CAST(:pid AS uuid) "
                "ORDER BY position NULLS LAST, name"
            ),
            {"pid": project_id},
        )).fetchall()
        states = {
            str(r.view_id): from_jsonb(r.config) or {}
            for r in (await db.execute(
                text(
                    "SELECT view_id, config FROM pm_view_user_state s "
                    "WHERE lower(s.member) = :member AND s.view_id IN ("
                    "  SELECT id FROM pm_views "
                    "  WHERE project_id = CAST(:pid AS uuid))"
                ),
                {"pid": project_id, "member": actor(user).lower()},
            )).fetchall()
        }
        out = []
        for row in rows:
            item = row_to_dict(row, ViewModel)
            item["user_state"] = states.get(str(row.id), {})
            out.append(item)
        return {"rows": out, "total": len(out)}


@router.post("/nodes/{project_id}/views", status_code=201)
async def create_view(
    project_id: str, payload: ViewIn,
    user: UserContext = Depends(get_current_user),
) -> dict:
    values = clean_payload(payload)
    name = str(values.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="A view needs a name.")
    view_type = values.get("view_type") or "list"
    if view_type not in VIEW_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown view type '{view_type}'. One of: {list(VIEW_TYPES)}.",
        )
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        await load_visible_project(db, vis, project_id)
        row = await insert_row(db, "pm_views", {
            "project_id": project_id, "name": name, "view_type": view_type,
            # Normalised on the way IN, so a view can never be stored carrying
            # a filter the list endpoint would refuse — a saved view that 422s
            # when opened is worse than one that quietly saved less.
            "config": normalise_view_config(values.get("config")),
            "position": values.get("position"),
            "created_by": actor(user),
        })
        return row_to_dict(row, ViewModel)


@router.patch("/views/{view_id}")
async def patch_view(
    view_id: str, payload: ViewIn,
    user: UserContext = Depends(get_current_user),
) -> dict:
    values = clean_payload(payload)
    if values.get("view_type") and values["view_type"] not in VIEW_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown view type '{values['view_type']}'. "
                   f"One of: {list(VIEW_TYPES)}.",
        )
    async with _tenant_session() as db:
        existing = await require_row(db, "pm_views", view_id, "View")
        vis = await resolve_visibility(db, user)
        await load_visible_project(db, vis, str(existing.project_id))
        if not values:
            return row_to_dict(existing, ViewModel)
        # Same normalisation as create — an edit must not be the way an
        # un-openable config gets in.
        if "config" in values:
            values["config"] = normalise_view_config(values["config"])
        row = await update_row(db, "pm_views", view_id, values)
        return row_to_dict(row, ViewModel)


@router.delete("/views/{view_id}")
async def delete_view(
    view_id: str, user: UserContext = Depends(get_current_user),
) -> dict:
    """Delete a view and the manual order that belonged to it.

    ``pm_view_task_positions`` CASCADEs, which is correct — the order was the
    view's, not the tasks' — but the count is reported anyway (R7/R8), because
    losing a hand-arranged board silently is exactly the surprise that makes
    people distrust a delete button.
    """
    async with _tenant_session() as db:
        existing = await require_row(db, "pm_views", view_id, "View")
        vis = await resolve_visibility(db, user)
        await load_visible_project(db, vis, str(existing.project_id))
        positions = (await db.execute(
            text(
                "SELECT count(*) FROM pm_view_task_positions "
                "WHERE view_id = CAST(:vid AS uuid)"
            ),
            {"vid": view_id},
        )).scalar() or 0
        # WS-27ae — `pm_view_user_state` CASCADEs too, and it is other people's
        # work: deleting a shared view discards every member's arrangement of
        # it, so the count is reported for the same reason `positions` is.
        states = (await db.execute(
            text(
                "SELECT count(*) FROM pm_view_user_state "
                "WHERE view_id = CAST(:vid AS uuid)"
            ),
            {"vid": view_id},
        )).scalar() or 0
        await db.execute(
            text("DELETE FROM pm_views WHERE id = CAST(:vid AS uuid)"),
            {"vid": view_id},
        )
        return {
            "deleted": view_id,
            "cascaded": {
                "positions": int(positions), "user_states": int(states),
            },
        }


# ── Per-user view state (WS-27ae / P-28) ────────────────────────────────────
#
# `pm_views` stays canonical: it is what the view IS, and everyone sees the same
# one. `pm_view_user_state` is what one MEMBER has done to their own screen —
# which lanes they collapsed, which axis they grouped by, which columns they
# want. WS-27y stored `collapsed_lanes` in the SHARED config, so collapsing a
# swimlane collapsed it for the whole company; this is that fix, server-side.
#
# ⚠️ Presentation only. `filters.VIEW_USER_STATE_KEYS` deliberately excludes
# filters: two people must never be looking at a view that means two different
# sets of tasks. The one named exception is `subtasks` (D-PM-38): `hidden`
# folds subtask rows for one member. `VIEW_USER_STATE_KEYS` says why.

@router.get("/views/{view_id}/state")
async def get_view_state(
    view_id: str, user: UserContext = Depends(get_current_user),
) -> dict:
    """The CALLER's overlay on this view. Never anybody else's.

    There is deliberately no way to ask for another member's state, not even
    for an admin: it is a window arrangement, not a record, and an endpoint
    that could serve it would be the endpoint that leaks it.
    """
    async with _tenant_session() as db:
        await _load_visible_view(db, user, view_id)
        row = (await db.execute(
            text(
                "SELECT config FROM pm_view_user_state "
                "WHERE view_id = CAST(:vid AS uuid) AND lower(member) = :member"
            ),
            {"vid": view_id, "member": actor(user).lower()},
        )).fetchone()
        return {
            "view_id": view_id,
            "member": actor(user).lower(),
            # An absent row is an empty overlay, not a 404: "I have not
            # arranged this view yet" is the normal state, and answering 404
            # would make every client special-case its first read.
            "config": (from_jsonb(getattr(row, "config", None)) or {}) if row else {},
        }


@router.put("/views/{view_id}/state")
async def set_view_state(
    view_id: str, payload: UserStateIn,
    user: UserContext = Depends(get_current_user),
) -> dict:
    """Replace the caller's overlay on this view.

    A PUT rather than a PATCH because the overlay is small and whole: the
    client holds the complete arrangement it is rendering, and a merge protocol
    would only add a way for two tabs to interleave into a state neither chose.

    ⚠️ ``member`` comes from the authenticated session (R11) and there is no
    parameter that could name somebody else — writing another member's screen
    arrangement is not a feature this app has.
    """
    config = normalise_view_user_state(payload.config)
    async with _tenant_session() as db:
        await _load_visible_view(db, user, view_id)
        await db.execute(
            text(
                "INSERT INTO pm_view_user_state (view_id, member, config) "
                "VALUES (CAST(:vid AS uuid), :member, CAST(:config AS jsonb)) "
                "ON CONFLICT (view_id, member) DO UPDATE "
                "SET config = EXCLUDED.config, updated_at = now()"
            ),
            {
                "vid": view_id, "member": actor(user).lower(),
                "config": json.dumps(config),
            },
        )
        return {
            "view_id": view_id, "member": actor(user).lower(), "config": config,
        }


# ── Manual order ────────────────────────────────────────────────────────────

async def _load_visible_view(db: Any, user: UserContext, view_id: str) -> Any:
    view = await require_row(db, "pm_views", view_id, "View")
    vis = await resolve_visibility(db, user)
    await load_visible_project(db, vis, str(view.project_id))
    return view


@router.get("/views/{view_id}/positions")
async def get_positions(
    view_id: str, user: UserContext = Depends(get_current_user),
) -> dict:
    async with _tenant_session() as db:
        await _load_visible_view(db, user, view_id)
        rows = (await db.execute(
            text(
                "SELECT task_id, position, group_key FROM pm_view_task_positions "
                "WHERE view_id = CAST(:vid AS uuid) ORDER BY group_key, position"
            ),
            {"vid": view_id},
        )).fetchall()
        return {
            "rows": [
                {
                    "task_id": str(r.task_id),
                    "position": float(r.position),
                    "group_key": r.group_key,
                }
                for r in rows
            ],
            "total": len(rows),
        }


@router.put("/views/{view_id}/positions")
async def set_positions(
    view_id: str, payload: PositionsIn,
    user: UserContext = Depends(get_current_user),
) -> dict:
    """Upsert manual positions for this view.

    Bulk by design, and it is the *preferred* shape rather than a convenience:
    a single drag writes one entry, but the first drag into an unordered group
    has to materialise positions for every task in it at once, and doing that as
    N requests would leave the order half-applied if one failed.
    """
    if len(payload.positions) > MAX_POSITIONS:
        raise HTTPException(
            status_code=422,
            detail=f"At most {MAX_POSITIONS} positions per request; "
                   f"got {len(payload.positions)}.",
        )
    async with _tenant_session() as db:
        await _load_visible_view(db, user, view_id)

        # ⚠️ The VIEW being visible does not make the TASKS visible.
        #
        # This endpoint used to validate the view and then write every
        # `task_id` the caller sent, unchecked — a join row authorised at one
        # end only. That is the shape of Plane's own GHSA-4w5x-wc9w-f47x
        # (found while reading them as a reference, 2026-08-10): they scoped a
        # cycle-issue write by issue id alone and a caller could re-point
        # another tenant's join rows.
        #
        # Ours was narrower than theirs — the PK is `(view_id, task_id)` and
        # `view_id` is checked, so no existing row of anyone else's view could
        # be re-pointed, and `organization_id` is stamped from the view by a
        # trigger. But it still let a caller write rows referencing tasks they
        # cannot read, including another organisation's, and every sibling
        # endpoint in this package resolves visibility per task. An
        # authorisation check that one endpoint skips is not a rule.
        #
        # Filtered as a SET rather than per id: `MAX_POSITIONS` is 1000, and
        # 1000 round trips to re-derive what one predicate answers is how a
        # correctness fix becomes a latency regression.
        vis = await resolve_visibility(db, user)
        wanted = [entry.task_id for entry in payload.positions]
        visible = {
            str(row.id)
            for row in (await db.execute(
                text(
                    "SELECT t.id FROM pm_tasks t "
                    f"WHERE t.id = ANY(CAST(:ids AS uuid[])) "
                    f"AND {task_visibility_clause(vis)}"
                ),
                {"ids": wanted, **vis.params},
            )).fetchall()
        }

        written = 0
        for entry in payload.positions:
            if entry.task_id not in visible:
                continue
            await db.execute(
                text(
                    "INSERT INTO pm_view_task_positions "
                    "(view_id, task_id, position, group_key) "
                    "VALUES (CAST(:vid AS uuid), CAST(:tid AS uuid), :pos, :grp) "
                    "ON CONFLICT (view_id, task_id) DO UPDATE "
                    "SET position = EXCLUDED.position, "
                    "    group_key = EXCLUDED.group_key, "
                    "    updated_at = now()"
                ),
                {
                    "vid": view_id, "tid": entry.task_id,
                    "pos": entry.position, "grp": entry.group_key,
                },
            )
            written += 1

        # `skipped` is reported rather than raised: a drag can carry a stale
        # selection, and failing the whole reorder because one row went out of
        # scope mid-gesture is worse for the user than ordering the rest. The
        # count is returned so a client can tell "ordered 40" from "ordered 39",
        # which is the difference between a no-op and a silent partial write.
        return {
            "view_id": view_id,
            "written": written,
            "skipped": len(payload.positions) - written,
        }
