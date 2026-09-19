"""Projects · moving a task between projects, and carrying its meaning with it.

Spec: ``project-docs/specs/project_management_app.md`` §9.13 (WS-27bl) ·
**D-PM-29 / D-PM-30 / D-PM-31**, owner directive 2026-09-19.

    POST /projects/tasks/move/preview   → what the move WOULD do. Writes nothing.
    POST /projects/tasks/move           → do it, with the mapping the human agreed.

## Why a preview endpoint exists at all

A move crosses two vocabularies. Statuses, task types and custom fields are all
scoped to a status owner or a root, so a task that leaves its root arrives
somewhere its lane, its type and half its values may mean nothing. The member
has to be told what will be lost **before** they agree, not in a toast
afterwards (D-PM-29). That is a read the server must do, because only the
server can see both vocabularies at once.

## The rules, in one place

Three resolvers, all pure and all tested without a database:

* :func:`resolve_status_map` defers to the SQL rule that already exists
  (``_REMAP_TARGET_SQL``: name, then category, then any non-triage lane). This
  module does not re-implement it — a second answer to "where does this lane
  land" is the CLAUDE.md §5 defect this feature could most easily author.
* :func:`resolve_field_map` matches ``field_key`` first, then a case-folded
  name, and only ever to a **compatible type**. A `select` value landing in a
  `number` field is not a mapping, it is corruption with an audit trail.
* :func:`shared_source` enforces D-PM-30 — one source project per selection —
  and names the offenders, because a count does not tell anybody what to
  deselect.

## What this module refuses to do

It never auto-creates anything in the destination. A missing tag or a missing
field is reported, never minted: creating one is a write to a vocabulary the
whole root shares, taken as a side effect of one member's move. §9.13.5 records
that as its own decision.
"""

from __future__ import annotations

from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException
from gateway.routes.projects.bulk import MAX_BULK, dedupe_ids
from gateway.routes.projects.core import (
    _REMAP_TARGET_SQL,
    TRIAGE_CATEGORY,
    _tenant_session,
    actor,
    emit,
    from_jsonb,
    load_visible_project,
    load_visible_task,
    next_task_number,
    node_kind,
    record_activity,
    remap_one_type,
    resolve_visibility,
    root_project_id,
    router,
    status_owner_id,
    update_row,
)
from gateway.routes.projects.custom_fields import load_definitions
from pydantic import BaseModel
from sqlalchemy import text

#: Which destination field types may receive which source type.
#:
#: ⚠️ Deliberately NOT "anything to text". Widening everything into a text field
#: would make every map succeed and quietly turn a date into a string that no
#: filter can compare. A mapping that cannot round-trip is a drop wearing a
#: mapping's clothes, and D-PM-29 says a drop must be NAMED.
#:
#: `select` → `multi_select` is the one widening allowed: one chosen option is
#: a legal list of one, and nothing about the value changes.
COMPATIBLE_TYPES: dict[str, frozenset[str]] = {
    "text": frozenset({"text"}),
    "number": frozenset({"number"}),
    "date": frozenset({"date"}),
    "boolean": frozenset({"boolean"}),
    "url": frozenset({"url"}),
    "select": frozenset({"select", "multi_select"}),
    "multi_select": frozenset({"multi_select"}),
}


def compatible(source_type: str, dest_type: str) -> bool:
    """May a value of ``source_type`` be written into a ``dest_type`` field?"""
    return dest_type in COMPATIBLE_TYPES.get(source_type, frozenset())


def shared_source(tasks: list[Any]) -> str:
    """The one project every task in the selection belongs to (D-PM-30).

    ⚠️ **Raises naming the offenders, never just a count.** The member has to
    know which tasks to deselect, and "3 sources" tells them nothing. The
    majority project is kept as the presumed source so the message can say
    which rows are the odd ones out.
    """
    by_project: dict[str, list[str]] = {}
    for task in tasks:
        by_project.setdefault(str(task.project_id), []).append(
            f"#{task.task_number}" if task.task_number else str(task.id)
        )
    if not by_project:
        raise HTTPException(status_code=422, detail="No tasks selected.")
    if len(by_project) == 1:
        return next(iter(by_project))

    # The biggest group is the presumed source; everything else is the problem.
    presumed = max(by_project, key=lambda pid: len(by_project[pid]))
    strays = sorted(
        ref for pid, refs in by_project.items() if pid != presumed for ref in refs
    )
    raise HTTPException(
        status_code=422,
        detail=(
            "A bulk move needs one source project, so that one mapping applies "
            "to every task. Deselect " + ", ".join(strays[:10])
            + (f" and {len(strays) - 10} more" if len(strays) > 10 else "")
            + "."
        ),
    )


def resolve_field_map(
    source_defs: list[dict[str, Any]],
    dest_defs: list[dict[str, Any]],
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """``(field_key → destination field_key, unmappable source definitions)``.

    ``field_key`` first, because it is the stable identity the schema calls one
    (migration 155: *"Free to change; field_key is not"*). A case-folded NAME is
    the fallback, for the ordinary case of two spaces that each grew a
    "Severity" independently.

    A match on either is still refused when the types cannot carry the value,
    and that refusal makes the field unmappable rather than silently mapped.
    """
    by_key = {str(d["field_key"]): d for d in dest_defs}
    by_name = {str(d["name"]).strip().lower(): d for d in dest_defs}

    mapping: dict[str, str] = {}
    orphans: list[dict[str, Any]] = []
    for definition in source_defs:
        key = str(definition["field_key"])
        source_type = str(definition["field_type"])
        target = by_key.get(key) or by_name.get(str(definition["name"]).strip().lower())
        if target is not None and compatible(source_type, str(target["field_type"])):
            mapping[key] = str(target["field_key"])
        else:
            orphans.append(definition)
    return mapping, orphans


def apply_field_map(
    values: dict[str, Any],
    mapping: dict[str, str],
    dest_keys: frozenset[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """``(values as they land, values that are DROPPED)``.

    ⚠️ **A key already legal in the destination is carried through unmapped.**
    Two spaces that share a `field_key` need no map entry, and requiring one
    would make the common case the noisy one.

    A mapped key whose target is not in the destination's definitions is a
    DROP, not a write: the map can be stale by the time it is applied, and
    writing a value under a key nothing defines recreates the orphan this
    feature exists to remove.
    """
    landed: dict[str, Any] = {}
    dropped: dict[str, Any] = {}
    for key, value in values.items():
        target = mapping.get(key, key if key in dest_keys else None)
        if target is not None and target in dest_keys:
            landed[target] = value
        else:
            dropped[key] = value
    return landed, dropped


# ── The wire ────────────────────────────────────────────────────────────────

class MoveIn(BaseModel):
    task_ids: list[str]
    destination_project_id: str
    #: Old status id → new status id, the answer the member gave in the card.
    #: Applied FIRST; the automatic rule sweeps whatever it does not cover, so
    #: a card built from stale counts cannot leave a task behind. That ordering
    #: is `remap_task_statuses`' and it is deliberate.
    status_map: dict[str, str] | None = None
    #: Source `field_key` → destination `field_key`.
    field_map: dict[str, str] | None = None
    #: The member saw what would be dropped and agreed (D-PM-29). Without it a
    #: move that WOULD drop a value is refused, so a caller cannot lose data by
    #: omitting a flag it never knew about.
    accept_drops: bool = False


async def _selection(db: Any, vis: Any, raw_ids: list[str]) -> list[Any]:
    """The tasks, deduped, capped and visible. A task the caller cannot see is
    a 404 rather than a skip: a move names its rows, unlike a bulk edit where
    skipping one is the forgiving choice."""
    ids = dedupe_ids(raw_ids)
    if not ids:
        raise HTTPException(status_code=422, detail="No tasks selected.")
    if len(ids) > MAX_BULK:
        raise HTTPException(
            status_code=422,
            detail=f"At most {MAX_BULK} tasks per move; got {len(ids)}.",
        )
    return [await load_visible_task(db, vis, task_id) for task_id in ids]


async def _destination(db: Any, vis: Any, project_id: str) -> Any:
    dest = await load_visible_project(db, vis, project_id)
    # After the visibility load, so a caller who cannot see the folder still
    # gets 404 and never learns from a 422 that it exists (R5).
    if node_kind(getattr(dest, "kind", None)) == "folder":
        raise HTTPException(
            status_code=422,
            detail="A folder holds projects, not tasks. Move into a project "
                   "inside it.",
        )
    return dest


async def _status_proposal(
    db: Any, status_ids: list[str], owner_id: str,
) -> list[dict[str, Any]]:
    """Where each distinct source lane would land, by the EXISTING rule.

    ⚠️ Runs `_REMAP_TARGET_SQL` rather than re-deriving it. The rule is
    name → category → any non-triage lane, and a second copy here would be a
    second answer to the question the move itself asks.
    """
    if not status_ids:
        return []
    rows = (await db.execute(
        text(
            "SELECT old.id AS from_id, old.name AS from_name, "
            "       old.category AS from_category, "
            + _REMAP_TARGET_SQL + " AS to_id "
            "  FROM pm_task_statuses old "
            " WHERE old.id = ANY(CAST(:ids AS uuid[]))"
        ),
        {"ids": status_ids, "owner": owner_id, "triage": TRIAGE_CATEGORY},
    )).fetchall()
    targets = {str(r.to_id) for r in rows if r.to_id}
    names = {}
    if targets:
        names = {
            str(r.id): (r.name, r.category) for r in (await db.execute(
                text(
                    "SELECT id, name, category FROM pm_task_statuses "
                    " WHERE id = ANY(CAST(:ids AS uuid[]))"
                ),
                {"ids": sorted(targets)},
            )).fetchall()
        }
    out = []
    for row in rows:
        to_id = str(row.to_id) if row.to_id else None
        to_name, to_category = names.get(to_id or "", (None, None))
        out.append({
            "from": {
                "id": str(row.from_id),
                "name": row.from_name,
                "category": row.from_category,
            },
            "to": (
                {"id": to_id, "name": to_name, "category": to_category}
                if to_id else None
            ),
        })
    return out


async def _type_proposal(
    db: Any, type_ids: list[str], dest_root: str,
) -> list[dict[str, Any]]:
    """Where each distinct source TYPE would land in the destination root.

    Defers to :func:`remap_one_type`, which `/tasks/{id}/move` also calls. One
    rule, so the narrow path and this one cannot disagree about what a move
    does to a type -- which is exactly the CLAUDE.md section 5 defect a second
    move endpoint could most easily author.
    """
    if not type_ids:
        return []
    names = {
        str(r.id): r.name for r in (await db.execute(
            text("SELECT id, name FROM pm_task_types WHERE id = ANY(CAST(:ids AS uuid[]))"),
            {"ids": type_ids},
        )).fetchall()
    }
    out = []
    for type_id in type_ids:
        landing = await remap_one_type(db, type_id=type_id, root_id=dest_root)
        out.append({
            "from": {"id": type_id, "name": names.get(type_id)},
            "to": {"id": landing} if landing else None,
        })
    return out


async def _plan(db: Any, vis: Any, payload: MoveIn) -> dict[str, Any]:
    """Everything both endpoints need to agree about, computed once.

    The preview returns it. The move applies it. Sharing one function is what
    makes "what you were shown" and "what happened" the same computation rather
    than two that drift apart.
    """
    tasks = await _selection(db, vis, payload.task_ids)
    source_project_id = shared_source(tasks)          # D-PM-30
    dest = await _destination(db, vis, payload.destination_project_id)
    dest_id = str(dest.id)

    source_root = str(tasks[0].root_project_id)
    dest_root = await root_project_id(db, dest_id)
    source_home = await status_owner_id(db, source_project_id)
    dest_home = await status_owner_id(db, dest_id)

    statuses = (
        await _status_proposal(
            db, sorted({str(t.status_id) for t in tasks if t.status_id}), dest_home,
        )
        if dest_home != source_home else []
    )

    source_defs = await load_definitions(db, source_root)
    dest_defs = await load_definitions(db, dest_root)
    auto_fields, orphan_defs = resolve_field_map(source_defs, dest_defs)
    chosen = {**auto_fields, **(payload.field_map or {})}
    dest_keys = frozenset(str(d["field_key"]) for d in dest_defs)

    # What each task would actually lose, which is NOT the same as the orphan
    # DEFINITIONS: a field with no home costs nothing on a task that never
    # filled it in, and warning about that would be a warning about nothing.
    drops: dict[str, Any] = {}
    for task in tasks:
        _, dropped = apply_field_map(from_jsonb(task.custom_fields), chosen, dest_keys)
        for key, value in dropped.items():
            drops.setdefault(key, []).append({
                "task_id": str(task.id),
                "task_number": task.task_number,
                "value": value,
            })

    types = (
        await _type_proposal(
            db, sorted({str(t.type_id) for t in tasks if t.type_id}), dest_root,
        )
        if dest_root != source_root else []
    )

    # Tags ride the row as NAMES, so they always survive the move. What may not
    # survive is their registry entry, and with it the colour. Reported, never
    # auto-created (section 9.13.5).
    carried = sorted({tag for t in tasks for tag in (t.tags or [])})
    unregistered: list[str] = []
    if carried and dest_root != source_root:
        known = {
            str(r.name).lower() for r in (await db.execute(
                text("SELECT name FROM pm_tags WHERE project_id = CAST(:root AS uuid)"),
                {"root": dest_root},
            )).fetchall()
        }
        unregistered = [tag for tag in carried if tag.lower() not in known]

    return {
        "tasks": tasks,
        "source_project_id": source_project_id,
        "destination_project_id": dest_id,
        "source_root_id": source_root,
        "destination_root_id": dest_root,
        "crosses_status_set": dest_home != source_home,
        "crosses_root": dest_root != source_root,
        "statuses": statuses,
        "field_map": chosen,
        "orphan_fields": [
            {
                "field_key": d["field_key"],
                "name": d["name"],
                "field_type": d["field_type"],
            }
            for d in orphan_defs
        ],
        "drops": drops,
        "types": types,
        "tags": {"carried": carried, "unregistered": unregistered},
        "dest_keys": dest_keys,
        "dest_home": dest_home,
    }


def _public(plan: dict[str, Any]) -> dict[str, Any]:
    """The plan minus the rows and the internals the wire has no use for."""
    public = {
        key: value for key, value in plan.items()
        if key not in {"tasks", "dest_keys", "dest_home"}
    }
    public["task_count"] = len(plan["tasks"])
    return public


@router.post("/tasks/move/preview")
async def preview_move(
    payload: MoveIn, user: UserContext = Depends(get_current_user),
) -> dict:
    """What the move would do. **Writes nothing.**

    Every refusal the real move makes is made here too -- one source project, a
    visible destination, not a folder -- so the card cannot offer a move that
    the apply would then reject.
    """
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        return _public(await _plan(db, vis, payload))


@router.post("/tasks/move")
async def move_tasks(
    payload: MoveIn, user: UserContext = Depends(get_current_user),
) -> dict:
    """Move the selection, with the mapping the member agreed to.

    **One transaction.** A half-moved selection is worse than a half-applied
    bulk edit, because the tasks are then in two projects and nobody can tell
    which half landed. ``bulk.py`` makes the same argument for the same reason.
    """
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        plan = await _plan(db, vis, payload)

        if plan["source_project_id"] == plan["destination_project_id"]:
            raise HTTPException(
                status_code=422, detail="Those tasks are already there.",
            )
        # D-PM-29. A caller cannot lose a value by omitting a flag it never
        # knew about, so the refusal names the keys and the remedy.
        if plan["drops"] and not payload.accept_drops:
            raise HTTPException(
                status_code=422,
                detail=(
                    "This move drops values with no field in the destination: "
                    + ", ".join(sorted(plan["drops"]))
                    + ". Send accept_drops to confirm."
                ),
            )

        status_to = {
            str(row["from"]["id"]): str(row["to"]["id"])
            for row in plan["statuses"] if row["to"]
        }
        # The member's answer wins over the automatic one, and the automatic one
        # covers what the card did not -- `remap_task_statuses` orders it this
        # way so a card built from stale counts cannot leave a task behind.
        status_to.update(payload.status_map or {})
        type_to = {
            str(row["from"]["id"]): (row["to"]["id"] if row["to"] else None)
            for row in plan["types"]
        }

        moved: list[str] = []
        for task in plan["tasks"]:
            values: dict[str, Any] = {"project_id": plan["destination_project_id"]}

            if plan["crosses_status_set"]:
                landing = status_to.get(str(task.status_id))
                if not landing:
                    raise HTTPException(
                        status_code=422,
                        detail=(
                            f"The destination has no status for #{task.task_number}, "
                            "so there is nowhere for it to land."
                        ),
                    )
                values["status_id"] = landing

            dropped: dict[str, Any] = {}
            if plan["crosses_root"]:
                landed, dropped = apply_field_map(
                    from_jsonb(task.custom_fields),
                    plan["field_map"],
                    plan["dest_keys"],
                )
                values["custom_fields"] = landed
                values["root_project_id"] = plan["destination_root_id"]
                # The number belongs to the old root's sequence and would
                # collide in the new one. `tasks.py` takes the same decision,
                # and records the old number for the same reason: a human id
                # changing with no trace breaks every comment that cites it.
                values["task_number"] = await next_task_number(
                    db, plan["destination_root_id"],
                )
                if task.type_id:
                    values["type_id"] = type_to.get(str(task.type_id))

            old_number = task.task_number
            await update_row(db, "pm_tasks", str(task.id), values)

            if dropped:
                await record_activity(
                    db, activity_type="system", created_by=actor(user),
                    task_id=str(task.id),
                    body=(
                        "Dropped on move, no field in the destination: "
                        + "; ".join(sorted(dropped))
                    ),
                )
            await record_activity(
                db, activity_type="system", created_by=actor(user),
                task_id=str(task.id),
                body=(
                    f"Moved to another project; was #{old_number}"
                    if plan["crosses_root"] else "Moved to another project"
                ),
            )
            moved.append(str(task.id))

    for task_id in moved:
        await emit("pm.task.moved", {"task_id": task_id})
    return {
        "moved": len(moved),
        "task_ids": moved,
        "destination_project_id": plan["destination_project_id"],
        "dropped_fields": sorted(plan["drops"]),
    }
