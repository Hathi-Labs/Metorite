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
    CLOSING_CATEGORIES,
    EPIC_TYPE_NAME,
    SETTINGS_WRITE,
    STATUS_CATEGORIES,
    StatusModel,
    TypeModel,
    _tenant_session,
    actor,
    assert_can_manage_settings,
    clean_payload,
    count_where,
    load_visible_project,
    record_activity,
    refuse_org_wide_write,
    remap_task_statuses,
    require_known_tenant,
    require_org_vocabulary_write,
    require_row,
    resolve_visibility,
    root_project_id,
    router,
    row_to_dict,
    shadowed,
    status_owner_id,
    status_scope_ids,
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
    #: ⚠️ ``is_default`` is GONE from a status (owner directive 2026-09-06).
    #: The first lane by position is where work starts — see
    #: ``core.load_default_status`` for why the flag was the wrong answer.
    #: Pydantic drops an unknown field, so a client still sending one is
    #: ignored rather than refused, which is what R6 asks of the release that
    #: removes a field. The column itself stays until a later migration.


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
    """The root whose TYPES a project inherits.

    Types are root-scoped and the subtree inherits, so a caller may pass any
    node in the tree and reach the same set — otherwise every subproject would
    need its own duplicated list.

    ⚠️ **Statuses left this function on 2026-09-06.** They now resolve to the
    nearest node that owns a set (:func:`core.status_owner_id`), which is the
    same answer as this one until somebody overrides a subproject and a
    different answer afterwards. Two names for two questions, because the bug
    where they diverge is silent.
    """
    await load_visible_project(db, vis, project_id)
    return await root_project_id(db, project_id)


async def _status_owner_for(db: Any, vis: Any, project_id: str) -> str:
    """The node whose status set ``project_id`` uses, checked for visibility."""
    await load_visible_project(db, vis, project_id)
    return await status_owner_id(db, project_id)


async def _clear_other_defaults(
    db: Any, table: str, root: str, keep: str,
) -> None:
    """Exactly one default TYPE per project.

    The migration cannot express this — a partial unique index would need the
    project in its predicate — so it is enforced here, by demoting the others
    rather than refusing the write. Refusing would make "make this the default"
    a two-step operation that is broken in between.

    ⚠️ **Statuses no longer come here at all** (owner directive 2026-09-06).
    A status has no default any more: the first lane by position is where work
    starts, and the drag that orders the lanes is the whole control. See
    ``core.load_default_status``, which records why the flag lost — the owner
    could not see it, and on the dev database it was set wrong on every root.

    A type keeps its flag, because a type list has no order to read an answer
    out of. Nothing here is a narrowing rule any more, so the ``category``
    parameter went with the status caller that needed it.
    """
    await db.execute(
        text(
            f"UPDATE {table} SET is_default = false "
            "WHERE project_id = CAST(:root AS uuid) AND id <> CAST(:keep AS uuid)"
        ),
        {"root": root, "keep": keep},
    )


# ── Statuses ────────────────────────────────────────────────────────────────

@router.get("/nodes/{project_id}/statuses")
async def list_statuses(
    project_id: str, user: UserContext = Depends(get_current_user),
) -> dict:
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        root = await _status_owner_for(db, vis, project_id)
        rows = (await db.execute(
            text(
                "SELECT * FROM pm_task_statuses WHERE project_id = CAST(:root AS uuid) "
                "ORDER BY position, name"
            ),
            {"root": root},
        )).fetchall()
        # How many tasks sit in each lane, within the scope this set governs.
        #
        # Returned WITH the lanes rather than from a second endpoint, because
        # every consumer needs both together: the editor prints the count on
        # the row, and a delete cannot be offered safely without it — the old
        # screen learned the number from a 409 AFTER the click. One call, so
        # the two halves cannot disagree about which lane holds what.
        scope = await status_scope_ids(db, project_id)
        counts = {
            str(r.status_id): int(r.tasks)
            for r in (await db.execute(
                text(
                    "SELECT status_id, count(*) AS tasks FROM pm_tasks "
                    " WHERE project_id = ANY(CAST(:scope AS uuid[])) "
                    " GROUP BY status_id"
                ),
                {"scope": scope},
            )).fetchall()
        }
        return {
            "rows": [row_to_dict(r, StatusModel) for r in rows],
            "total": len(rows),
            "counts": counts,
            "owner_id": root,
        }


@router.post("/nodes/{project_id}/statuses", status_code=201)
async def create_status(
    project_id: str, payload: StatusIn,
    user: UserContext = Depends(get_current_user),
) -> dict:
    assert_can_manage_settings(user)
    values = clean_payload(payload)
    name = str(values.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="A status needs a name.")
    category = values.get("category") or "todo"
    validate_choice(category, STATUS_CATEGORIES, "status category")

    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        root = await _status_owner_for(db, vis, project_id)
        row = (await db.execute(
            text(
                "INSERT INTO pm_task_statuses "
                "(project_id, name, color, position, category) "
                "VALUES (CAST(:root AS uuid), :name, :color, :position, "
                "        :category) RETURNING *"
            ),
            {
                "root": root, "name": name,
                "color": values.get("color") or "gray",
                "position": values.get("position") or 0,
                "category": category,
            },
        )).fetchone()
        return row_to_dict(row, StatusModel)


@router.patch("/statuses/{status_id}")
async def patch_status(
    status_id: str, payload: StatusIn,
    user: UserContext = Depends(get_current_user),
) -> dict:
    assert_can_manage_settings(user)
    values = clean_payload(payload)
    validate_choice(values.get("category"), STATUS_CATEGORIES, "status category")
    async with _tenant_session() as db:
        existing = await require_row(db, "pm_task_statuses", status_id, "Status")
        vis = await resolve_visibility(db, user)
        await load_visible_project(db, vis, str(existing.project_id))
        if not values:
            return row_to_dict(existing, StatusModel)
        row = await update_row(db, "pm_task_statuses", status_id, values)
        return row_to_dict(row, StatusModel)


@router.delete("/statuses/{status_id}")
async def delete_status(
    status_id: str,
    move_to: str | None = None,
    user: UserContext = Depends(get_current_user),
) -> dict:
    """Delete a lane, moving whatever is in it to ``move_to`` first.

    ``pm_tasks.status_id`` is ``ON DELETE RESTRICT``, so the database refuses a
    lane in use — as an opaque IntegrityError 500. This used to turn that into a
    409 naming the count and stop there, which told the caller to go and empty
    the lane by hand, one card at a time.

    ``move_to`` is the answer to the question the 409 was asking (owner
    directive 2026-09-06). Absent, the 409 stands and still names the count, so
    a client that has not adopted the parameter behaves exactly as before.

    Two lanes cannot be deleted at all, whatever ``move_to`` says:

    * **the last one** — every task needs a status and the column is NOT NULL;
    * **the last CLOSING one** — with no `done` and no `cancelled` lane left,
      nothing in this project could ever complete and every roll-up under it
      would read 0% forever. That failure is silent and permanent, which is
      exactly the kind this refuses rather than warns about.
    """
    assert_can_manage_settings(user)
    async with _tenant_session() as db:
        existing = await require_row(db, "pm_task_statuses", status_id, "Status")
        vis = await resolve_visibility(db, user)
        await load_visible_project(db, vis, str(existing.project_id))

        # The rows themselves, counted here rather than by the database. A
        # status set is a handful of lanes — the aggregate saved nothing and
        # cost a `FILTER` clause that only Postgres understands.
        survivors = (await db.execute(
            text(
                "SELECT id, category FROM pm_task_statuses "
                " WHERE project_id = CAST(:owner AS uuid) "
                "   AND id <> CAST(:sid AS uuid)"
            ),
            {"owner": str(existing.project_id), "sid": status_id},
        )).fetchall()
        if not survivors:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"'{existing.name}' is the only status here. A project "
                    "needs at least one, because every task must be in one."
                ),
            )
        if not any(str(r.category) in CLOSING_CATEGORIES for r in survivors):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"'{existing.name}' is the only lane that closes a task. "
                    "Without one, nothing here could ever be completed and "
                    "every roll-up would read 0%. Add another Done or "
                    "Cancelled lane first."
                ),
            )

        in_use = await count_where(db, "pm_tasks", "status_id", status_id)
        if in_use and not move_to:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"'{existing.name}' still holds {in_use} task(s). "
                    "Say which status they move to."
                ),
            )
        if in_use:
            target = await require_row(db, "pm_task_statuses", move_to, "Status")
            if str(target.project_id) != str(existing.project_id):
                raise HTTPException(
                    status_code=422,
                    detail="Tasks can only move to a status in the same set.",
                )
            if str(target.id) == status_id:
                raise HTTPException(
                    status_code=422,
                    detail="A status cannot hand its tasks to itself.",
                )
            await db.execute(
                text(
                    "UPDATE pm_tasks SET status_id = CAST(:new AS uuid) "
                    " WHERE status_id = CAST(:old AS uuid)"
                ),
                {"new": str(target.id), "old": status_id},
            )
            # The lane they land in may close a task when the one they left did
            # not, or the other way round. Same correction the switch makes, and
            # for the same reason: `completed_at` is derived from the category.
            await db.execute(
                text(
                    "UPDATE pm_tasks SET completed_at = CASE WHEN :closes "
                    "         THEN COALESCE(completed_at, now()) ELSE NULL END "
                    " WHERE status_id = CAST(:new AS uuid) "
                    "   AND (:closes) <> (completed_at IS NOT NULL)"
                ),
                {
                    "new": str(target.id),
                    "closes": str(target.category) in CLOSING_CATEGORIES,
                },
            )
        await db.execute(
            text("DELETE FROM pm_task_statuses WHERE id = CAST(:sid AS uuid)"),
            {"sid": status_id},
        )
        return {"deleted": status_id, "tasks_affected": in_use}


# ── Which set a project uses (WS-27 status sets, 2026-09-06) ────────────────
#
# One model, one sentence: a project uses the set of the NEAREST node at or
# above it that owns one. These three routes are the only way that changes.
#
# ⚠️ **A mapping travels by target NAME, not by target id**, and that is not
# sloppiness. When the switch is a COPY the destination lanes do not exist yet —
# the card is chosen before the rows are written — so an id is unavailable at
# exactly the moment the human decides. Names are unique per set
# (`UNIQUE (project_id, name)`), so a name is a stable handle in both cases, and
# one wire shape serves inherit, copy and dormant alike. `_apply_set` resolves
# names to ids after materialising the target, and refuses an unknown one.


class StatusSetIn(BaseModel):
    #: ``"inherit"`` gives the node no set of its own. ``"own"`` gives it one.
    mode: str
    #: ``own`` only — the node whose lanes to duplicate. Absent reuses this
    #: node's dormant set if it has one, else copies the set it uses today, so
    #: "use its own statuses" never starts from an empty board.
    copy_from: str | None = None
    #: ``{old status id: target lane NAME}``, from the mapping card. Applied
    #: before the automatic rule, which then sweeps anything left over.
    mapping: dict[str, str] | None = None


async def _resolve_set_choice(
    db: Any, project_id: str, payload: StatusSetIn,
) -> tuple[str | None, list[Any]]:
    """What the node would use, and the lanes it would have.

    Returns ``(source_project_id, lanes)``. ``source_project_id`` is ``None``
    for a dormant set, which is already this node's own rows.
    """
    mode = (payload.mode or "").strip().lower()
    if mode not in ("inherit", "own"):
        raise HTTPException(
            status_code=422, detail="mode must be 'inherit' or 'own'.",
        )

    if mode == "inherit":
        parent = (await db.execute(
            text(
                "SELECT parent_project_id FROM pm_projects "
                " WHERE id = CAST(:id AS uuid)"
            ),
            {"id": project_id},
        )).fetchone()
        if parent is None or parent.parent_project_id is None:
            raise HTTPException(
                status_code=422,
                detail=(
                    "A space has nothing above it to inherit from, so it "
                    "always carries its own statuses."
                ),
            )
        source = await status_owner_id(db, str(parent.parent_project_id))
        return source, await _lanes_of(db, source)

    if payload.copy_from:
        source = await status_owner_id(db, payload.copy_from)
        return source, await _lanes_of(db, source)

    dormant = await _lanes_of(db, project_id)
    if dormant:
        return None, dormant
    source = await status_owner_id(db, project_id)
    return source, await _lanes_of(db, source)


async def _lanes_of(db: Any, owner_id: str) -> list[Any]:
    return list((await db.execute(
        text(
            "SELECT * FROM pm_task_statuses "
            " WHERE project_id = CAST(:owner AS uuid) ORDER BY position, name"
        ),
        {"owner": owner_id},
    )).fetchall())


async def _moves_for(
    db: Any, project_id: str, lanes: list[Any],
) -> list[dict]:
    """Every lane the scope's tasks sit in now, and where each would land.

    The suggestion is the same rule :func:`core.remap_task_statuses` applies —
    an exact name, then the first lane of the same category — so the card
    pre-fills with the answer that would happen anyway, and a human touching
    nothing gets the automatic outcome rather than a different one.
    """
    scope = await status_scope_ids(db, project_id)
    if not scope:
        return []
    rows = (await db.execute(
        text(
            "SELECT s.id, s.name, s.category, count(*) AS tasks "
            "  FROM pm_tasks t JOIN pm_task_statuses s ON s.id = t.status_id "
            " WHERE t.project_id = ANY(CAST(:scope AS uuid[])) "
            " GROUP BY s.id, s.name, s.category ORDER BY s.name"
        ),
        {"scope": scope},
    )).fetchall()

    by_name = {str(lane.name).strip().lower(): lane for lane in lanes}
    moves: list[dict] = []
    for row in rows:
        keeps = by_name.get(str(row.name).strip().lower())
        # A lane that survives the switch by name AND category is not a move at
        # all. Listing it would ask the reader to confirm that nothing happens.
        same = keeps is not None and str(keeps.category) == str(row.category)
        suggestion = keeps or next(
            (l for l in lanes if str(l.category) == str(row.category)), None,
        )
        moves.append({
            "status_id": str(row.id),
            "name": row.name,
            "category": row.category,
            "tasks": int(row.tasks),
            "unchanged": bool(same),
            "suggested": None if suggestion is None else str(suggestion.name),
            "closes": (
                None if suggestion is None
                else str(suggestion.category) in CLOSING_CATEGORIES
            ),
            "closed_now": str(row.category) in CLOSING_CATEGORIES,
        })
    return moves


@router.get("/nodes/{project_id}/status-set")
async def describe_status_set(
    project_id: str, user: UserContext = Depends(get_current_user),
) -> dict:
    """Where this project's statuses come from, and whether it may change."""
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        node = await load_visible_project(db, vis, project_id)
        owner = await status_owner_id(db, project_id)
        owner_row = await require_row(db, "pm_projects", owner, "Project")
        dormant = [] if getattr(node, "owns_statuses", False) else await _lanes_of(
            db, project_id,
        )
        return {
            "project_id": project_id,
            "owns": bool(getattr(node, "owns_statuses", False)),
            "owner_id": owner,
            "owner_name": owner_row.name,
            # A space has nothing above it, so "inherit" is not a choice it can
            # make. The UI disables the control and says why rather than hiding
            # it — an absent option reads as a missing feature.
            "can_inherit": node.parent_project_id is not None,
            "has_dormant_set": bool(dormant),
            "may_edit": bool(user is not None and user.has_permission(SETTINGS_WRITE)),
        }


@router.post("/nodes/{project_id}/status-set/preview")
async def preview_status_set(
    project_id: str, payload: StatusSetIn,
    user: UserContext = Depends(get_current_user),
) -> dict:
    """What a switch would move, without moving it.

    Read-only on purpose: the card is opened far more often than it is
    confirmed, and a preview that wrote anything would make opening it a
    decision.
    """
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        await load_visible_project(db, vis, project_id)
        _source, lanes = await _resolve_set_choice(db, project_id, payload)
        moves = await _moves_for(db, project_id, lanes)
        return {
            "lanes": [row_to_dict(lane, StatusModel) for lane in lanes],
            "moves": moves,
            "moving": sum(m["tasks"] for m in moves if not m["unchanged"]),
            "completing": sum(
                m["tasks"] for m in moves if m["closes"] and not m["closed_now"]
            ),
            "reopening": sum(
                m["tasks"] for m in moves
                if m["closes"] is False and m["closed_now"]
            ),
        }


@router.post("/nodes/{project_id}/status-set")
async def set_status_set(
    project_id: str, payload: StatusSetIn,
    user: UserContext = Depends(get_current_user),
) -> dict:
    """Switch which set a project uses, and move its tasks in one transaction.

    ⚠️ **The flag and the task moves commit together.** That is what closes the
    window a task could be created in: anything written after this commit
    already resolves to the new set, so there is no sweep to run afterwards and
    no half-switched state to recover from.
    """
    assert_can_manage_settings(user)
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        node = await load_visible_project(db, vis, project_id)
        mode = (payload.mode or "").strip().lower()
        source, lanes = await _resolve_set_choice(db, project_id, payload)
        if not lanes:
            raise HTTPException(
                status_code=422,
                detail="That set has no statuses in it, so nothing could move.",
            )

        if mode == "inherit":
            await db.execute(
                text(
                    "UPDATE pm_projects SET owns_statuses = false "
                    " WHERE id = CAST(:id AS uuid)"
                ),
                {"id": project_id},
            )
            owner = await status_owner_id(db, project_id)
        else:
            owner = project_id
            stale: list[str] = []
            if source is not None:
                # A copy REPLACES a dormant set rather than merging into it
                # (design G3): names are unique per project, so a merge would
                # need its own mapping card for lanes nobody is looking at.
                #
                # ⚠️ DO UPDATE, not DO NOTHING. A dormant lane whose name the
                # source also uses must end up looking like the SOURCE's lane,
                # not keep an old colour and category under a matching name.
                # Updating in place also keeps the row id, so a task already
                # sitting there does not move at all.
                await db.execute(
                    text(
                        "INSERT INTO pm_task_statuses "
                        "  (project_id, name, color, position, category) "
                        "SELECT CAST(:me AS uuid), s.name, s.color, s.position, "
                        "       s.category "
                        "  FROM pm_task_statuses s "
                        " WHERE s.project_id = CAST(:src AS uuid) "
                        "ON CONFLICT (project_id, name) DO UPDATE SET "
                        "  color = EXCLUDED.color, "
                        "  position = EXCLUDED.position, "
                        "  category = EXCLUDED.category"
                    ),
                    {"me": project_id, "src": source},
                )
                # What the source does NOT have — read AFTER the insert, so a
                # lane the copy just re-used by name is correctly not in here.
                stale = [str(r.id) for r in (await db.execute(
                    text(
                        "SELECT me.id FROM pm_task_statuses me "
                        " WHERE me.project_id = CAST(:me AS uuid) "
                        "   AND NOT EXISTS ("
                        "     SELECT 1 FROM pm_task_statuses s "
                        "      WHERE s.project_id = CAST(:src AS uuid) "
                        "        AND lower(btrim(s.name)) = lower(btrim(me.name)))"
                    ),
                    {"me": project_id, "src": source},
                )).fetchall()]
            await db.execute(
                text(
                    "UPDATE pm_projects SET owns_statuses = true "
                    " WHERE id = CAST(:id AS uuid)"
                ),
                {"id": project_id},
            )

        # Names → ids, now that the target set certainly exists.
        target = {
            str(r.name).strip().lower(): str(r.id)
            for r in await _lanes_of(db, owner)
        }
        mapping: dict[str, str] = {}
        for old_id, wanted in (payload.mapping or {}).items():
            found = target.get(str(wanted).strip().lower())
            if found is None:
                raise HTTPException(
                    status_code=422,
                    detail=f"'{wanted}' is not a status in the set being moved to.",
                )
            mapping[str(old_id)] = found

        result = await remap_task_statuses(
            db, project_id=project_id, owner_id=owner, mapping=mapping or None,
        )

        if mode == "own" and stale:
            # Only the rows nothing landed on. `remap_task_statuses` has just
            # moved every task in scope onto the new lanes, so an id still
            # holding tasks here is one a task OUTSIDE the scope points at —
            # left alone rather than force-deleted through a RESTRICT.
            await db.execute(
                text(
                    "DELETE FROM pm_task_statuses s "
                    " WHERE s.id = ANY(CAST(:ids AS uuid[])) "
                    "   AND NOT EXISTS (SELECT 1 FROM pm_tasks t "
                    "                    WHERE t.status_id = s.id)"
                ),
                {"ids": stale},
            )

        await record_activity(
            db, activity_type="system", created_by=actor(user),
            project_id=project_id,
            body=(
                "Statuses now inherited from the parent"
                if mode == "inherit" else "Statuses are now this project's own"
            ),
        )
        return {
            "project_id": project_id, "owns": mode == "own",
            "owner_id": owner, **result,
        }


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
