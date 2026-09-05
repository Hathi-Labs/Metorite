"""Projects · admin — task statuses and task types, per root project.

Spec: ``project-docs/specs/project_management_app.md`` §4 (``admin.py`` row).

    GET    /projects/nodes/{project_id}/statuses
    POST   /projects/nodes/{project_id}/statuses
    PATCH  /projects/statuses/{status_id}
    DELETE /projects/statuses/{status_id}          → 409 while in use
    GET    /projects/nodes/{project_id}/types
    POST   /projects/nodes/{project_id}/types
    PATCH  /projects/types/{type_id}
    DELETE /projects/types/{type_id}

Statuses are DATA, not an enum (§3.3): the importer has to represent ClickUp's
real per-list status names and the owner has to reshape a workflow without a
deploy. ``category`` is the machine-readable half and is the only part other
code may key off.
"""

from __future__ import annotations

from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException
from gateway.routes.projects.core import (
    EPIC_TYPE_NAME,
    STATUS_CATEGORIES,
    StatusModel,
    TypeModel,
    _tenant_session,
    clean_payload,
    count_where,
    load_visible_project,
    refuse_org_wide_write,
    require_known_tenant,
    require_org_vocabulary_write,
    require_row,
    resolve_visibility,
    root_project_id,
    router,
    row_to_dict,
    shadowed,
    update_row,
    validate_choice,
    vocabulary_scope,
)
from pydantic import BaseModel
from sqlalchemy import text


class StatusIn(BaseModel):
    name: str | None = None
    color: str | None = None
    position: int | None = None
    category: str | None = None
    is_default: bool | None = None


class TypeIn(BaseModel):
    name: str | None = None
    icon: str | None = None
    color: str | None = None
    is_default: bool | None = None
    #: WS-27bj. ``"org"`` mints an org-wide type; anything else, including the
    #: default, keeps today's per-project behaviour verbatim. A field rather than
    #: a second endpoint so there stays ONE create path — a parallel one is how
    #: the two drift on the next validation somebody adds.
    scope: str | None = None
    #: WS-27ae / P-28. Unlike `is_system`, this IS the caller's to set: it says
    #: "this type is a top level", which is a workflow decision a project makes
    #: about its own vocabulary. `is_system` stays a hard-coded literal below,
    #: because that one grants an exemption rather than accepting a rule.
    is_epic: bool | None = None


async def _root_for(db: Any, vis: Any, project_id: str) -> str:
    """The root whose statuses and types a project inherits.

    Configuration is root-scoped and the subtree inherits, so a caller may pass
    any node in the tree and reach the same set — otherwise every subproject
    would need its own duplicated workflow.
    """
    await load_visible_project(db, vis, project_id)
    return await root_project_id(db, project_id)


async def _clear_other_defaults(
    db: Any, table: str, root: str, keep: str, category: str | None = None,
) -> None:
    """Exactly one default per project — per CATEGORY, when the table has one.

    The migration cannot express this — a partial unique index would need the
    project in its predicate — so it is enforced here, by demoting the others
    rather than refusing the write. Refusing would make "make this the default"
    a two-step operation that is broken in between.

    ⚠️ **`category` narrows the demotion, and it has to.** This function used to
    demote every other row in the tree, so a root held exactly ONE default
    status. Measured on the dev database 2026-09-03: five roots, four statuses
    each, and all five defaults sat on `backlog` — no root had a default in
    `todo`, `in_progress` or `done`.

    That makes the rule in `project_management_app.md` §9.12.3 — "dropping a
    card into a category column sets the task's status to that project's
    DEFAULT status in that category" — unanswerable for three of four columns.
    A default is the answer to "which lane in this STAGE", and a stage is what
    a category is, so one per root was the wrong grain.

    Types have no category, so they pass none and keep the root-wide rule
    verbatim. That is why this is a narrowing parameter rather than a second
    function: one demotion rule, read one way, whichever table calls it.
    """
    where = "project_id = CAST(:root AS uuid) AND id <> CAST(:keep AS uuid)"
    params: dict[str, Any] = {"root": root, "keep": keep}
    if category is not None:
        where += " AND category = :category"
        params["category"] = category
    await db.execute(
        text(f"UPDATE {table} SET is_default = false WHERE {where}"), params,
    )


# ── Statuses ────────────────────────────────────────────────────────────────

@router.get("/nodes/{project_id}/statuses")
async def list_statuses(
    project_id: str, user: UserContext = Depends(get_current_user),
) -> dict:
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        root = await _root_for(db, vis, project_id)
        rows = (await db.execute(
            text(
                "SELECT * FROM pm_task_statuses WHERE project_id = CAST(:root AS uuid) "
                "ORDER BY position, name"
            ),
            {"root": root},
        )).fetchall()
        return {
            "rows": [row_to_dict(r, StatusModel) for r in rows], "total": len(rows),
        }


@router.post("/nodes/{project_id}/statuses", status_code=201)
async def create_status(
    project_id: str, payload: StatusIn,
    user: UserContext = Depends(get_current_user),
) -> dict:
    values = clean_payload(payload)
    name = str(values.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="A status needs a name.")
    category = values.get("category") or "todo"
    validate_choice(category, STATUS_CATEGORIES, "status category")

    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        root = await _root_for(db, vis, project_id)
        row = (await db.execute(
            text(
                "INSERT INTO pm_task_statuses "
                "(project_id, name, color, position, category, is_default) "
                "VALUES (CAST(:root AS uuid), :name, :color, :position, "
                "        :category, :is_default) RETURNING *"
            ),
            {
                "root": root, "name": name,
                "color": values.get("color") or "gray",
                "position": values.get("position") or 0,
                "category": category,
                "is_default": bool(values.get("is_default")),
            },
        )).fetchone()
        if row.is_default:
            await _clear_other_defaults(
                db, "pm_task_statuses", root, str(row.id), str(row.category),
            )
        return row_to_dict(row, StatusModel)


@router.patch("/statuses/{status_id}")
async def patch_status(
    status_id: str, payload: StatusIn,
    user: UserContext = Depends(get_current_user),
) -> dict:
    values = clean_payload(payload)
    validate_choice(values.get("category"), STATUS_CATEGORIES, "status category")
    async with _tenant_session() as db:
        existing = await require_row(db, "pm_task_statuses", status_id, "Status")
        vis = await resolve_visibility(db, user)
        await load_visible_project(db, vis, str(existing.project_id))
        if not values:
            return row_to_dict(existing, StatusModel)
        row = await update_row(db, "pm_task_statuses", status_id, values)
        if values.get("is_default"):
            # `row.category`, not `values` — a PATCH that sets `is_default`
            # without naming a category must demote inside the category the row
            # ALREADY has, and a PATCH that moves the row to a new category must
            # demote inside the destination. The stored row after the update is
            # the only value that is right in both cases.
            await _clear_other_defaults(
                db, "pm_task_statuses", str(row.project_id), status_id,
                str(row.category),
            )
        return row_to_dict(row, StatusModel)


@router.delete("/statuses/{status_id}")
async def delete_status(
    status_id: str, user: UserContext = Depends(get_current_user),
) -> dict:
    """Delete a status, unless tasks are still in it.

    ``pm_tasks.status_id`` is ``ON DELETE RESTRICT``, so the database would
    refuse this anyway — as an opaque IntegrityError 500. Counting first turns
    that into a 409 naming how many tasks are in the way, which is the number
    the caller needs to decide what to do next.
    """
    async with _tenant_session() as db:
        existing = await require_row(db, "pm_task_statuses", status_id, "Status")
        vis = await resolve_visibility(db, user)
        await load_visible_project(db, vis, str(existing.project_id))
        in_use = await count_where(db, "pm_tasks", "status_id", status_id)
        if in_use:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"'{existing.name}' still holds {in_use} task(s). "
                    "Move them to another status first."
                ),
            )
        await db.execute(
            text("DELETE FROM pm_task_statuses WHERE id = CAST(:sid AS uuid)"),
            {"sid": status_id},
        )
        return {"deleted": status_id, "tasks_affected": 0}


# ── Types ───────────────────────────────────────────────────────────────────

@router.get("/nodes/{project_id}/types")
async def list_types(
    project_id: str, user: UserContext = Depends(get_current_user),
) -> dict:
    """This project's EFFECTIVE task types: org-wide ∪ root-local (WS-27bj).

    A root-local type shadows an org-wide one of the same name, so a project that
    has always had its own "Bug" keeps its own icon and colour after the
    organization gains one.
    """
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        root = await _root_for(db, vis, project_id)
        rows = (await db.execute(
            text(
                f"SELECT * FROM pm_task_types WHERE {vocabulary_scope()} "
                "ORDER BY name"
            ),
            {"root": root},
        )).fetchall()
        rows = shadowed(rows, lambda r: r.name)
        return {
            "rows": [row_to_dict(r, TypeModel) for r in rows], "total": len(rows),
        }


@router.post("/nodes/{project_id}/types", status_code=201)
async def create_type(
    project_id: str, payload: TypeIn,
    user: UserContext = Depends(get_current_user),
) -> dict:
    values = clean_payload(payload)
    name = str(values.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="A task type needs a name.")
    org_wide = values.get("scope") == "org"
    if org_wide:
        require_org_vocabulary_write(user)
        if values.get("is_default"):
            # "Exactly one default per project" is a per-project invariant
            # (`_clear_other_defaults`), and an org-wide default would be a
            # second answer to the same question for every project at once.
            # Which type a project starts tasks in is that project's call.
            raise HTTPException(
                status_code=422,
                detail="An organization-wide task type cannot be the default; "
                       "the default is each project's own choice.",
            )

    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        root = await _root_for(db, vis, project_id)
        if org_wide:
            require_known_tenant(vis, "task type")
        # ⚠️ The tenant is passed EXPLICITLY for an org-wide row, and it has to
        # be. Migration 161's trigger derives `organization_id` from the parent
        # project, so with no `project_id` nothing fills it and `NOT NULL`
        # refuses the insert. That is the migration's stated design — "the
        # application must decide the tenant exactly once", as it already does
        # for a root project — not a gap. It comes from the resolved visibility,
        # never from the payload (R5: never trust a tenant from request input).
        #
        # The per-project arm passes it too, so there is one statement rather
        # than two. That is not a no-op: 161's trigger switches from FILLING the
        # column to VERIFYING it against the parent project, which turns a
        # cross-tenant insert into a refusal instead of a stored row.
        row = (await db.execute(
            text(
                "INSERT INTO pm_task_types "
                "(project_id, organization_id, name, icon, color, is_default, "
                " is_epic, is_system) "
                "VALUES (CAST(:root AS uuid), CAST(:org AS uuid), :name, :icon, "
                "        :color, :is_default, :is_epic, false) RETURNING *"
            ),
            {
                "root": None if org_wide else root,
                "org": vis.organization_id,
                "name": name,
                "icon": values.get("icon"), "color": values.get("color"),
                "is_default": bool(values.get("is_default")),
                "is_epic": bool(values.get("is_epic")),
            },
        )).fetchone()
        # is_system is written as a literal false, never from the payload: the
        # Epic rule keys off it (§3.4), so a caller able to set it could mint a
        # second root-only type — or, worse, a type that claims Epic's exemption.
        if row.is_default:
            await _clear_other_defaults(db, "pm_task_types", root, str(row.id))
        return row_to_dict(row, TypeModel)


@router.patch("/types/{type_id}")
async def patch_type(
    type_id: str, payload: TypeIn,
    user: UserContext = Depends(get_current_user),
) -> dict:
    values = clean_payload(payload)
    # `scope` selects where a NEW row lands; it is not a column and re-scoping an
    # existing row is not this ticket's (see `refuse_org_wide_write`). Dropped
    # rather than refused: it arrives from a client reusing one form model for
    # both verbs, which is a shape, not a mistake.
    values.pop("scope", None)
    async with _tenant_session() as db:
        existing = await require_row(db, "pm_task_types", type_id, "Task type")
        refuse_org_wide_write(existing, "task type")
        vis = await resolve_visibility(db, user)
        await load_visible_project(db, vis, str(existing.project_id))
        if getattr(existing, "is_system", False) and "name" in values:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"'{EPIC_TYPE_NAME}' is a system type and cannot be renamed; "
                    "the hierarchy rule keys off it."
                ),
            )
        # WS-27ae — and it cannot be un-flagged either, for exactly the same
        # reason the rename is refused. `core.is_epic_type` still recognises the
        # seeded system type by name, so clearing the flag would answer 200 and
        # change nothing: a write that reports success while the rule stays on
        # is worse than one that says no.
        if getattr(existing, "is_system", False) and values.get("is_epic") is False:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"'{EPIC_TYPE_NAME}' is a system type and stays the top "
                    "level; the hierarchy rule keys off it."
                ),
            )
        if not values:
            return row_to_dict(existing, TypeModel)
        row = await update_row(db, "pm_task_types", type_id, values)
        if values.get("is_default"):
            await _clear_other_defaults(
                db, "pm_task_types", str(row.project_id), type_id,
            )
        return row_to_dict(row, TypeModel)


@router.delete("/types/{type_id}")
async def delete_type(
    type_id: str, user: UserContext = Depends(get_current_user),
) -> dict:
    """Delete a task type. Tasks carrying it keep existing, untyped.

    ``pm_tasks.type_id`` is ``ON DELETE SET NULL`` — unlike a status, a type
    carries no workflow semantics, so losing it costs a label rather than a
    lane. The count is reported (R7/R8) because "12 tasks became untyped" is
    not something to discover from a board.
    """
    async with _tenant_session() as db:
        existing = await require_row(db, "pm_task_types", type_id, "Task type")
        refuse_org_wide_write(existing, "task type")
        vis = await resolve_visibility(db, user)
        await load_visible_project(db, vis, str(existing.project_id))
        if getattr(existing, "is_system", False):
            raise HTTPException(
                status_code=409,
                detail=f"'{EPIC_TYPE_NAME}' is a system type and cannot be deleted.",
            )
        affected = await count_where(db, "pm_tasks", "type_id", type_id)
        await db.execute(
            text("DELETE FROM pm_task_types WHERE id = CAST(:tid AS uuid)"),
            {"tid": type_id},
        )
        return {"deleted": type_id, "tasks_untyped": affected}
