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
    CLOSING_CATEGORIES,
    TRIAGE_CATEGORY,
    _tenant_session,
    actor,
    assert_move_keeps_privacy,
    emit,
    from_jsonb,
    load_visible_project,
    load_visible_task,
    next_task_number,
    node_kind,
    now,
    record_activity,
    remap_one_type,
    require_status_in_project,
    resolve_visibility,
    root_project_id,
    router,
    status_owner_id,
    update_row,
    vocabulary_scope,
)
from gateway.routes.projects.custom_fields import (
    _is_blank,
    assert_required_fields_present,
    load_definitions,
)
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
    # ⚠️ `select` does NOT widen into `multi_select`. The earlier comment here
    # claimed "one chosen option is a legal list of one, and nothing about the
    # value changes" — and those two clauses contradict each other. A list of
    # one is `["High"]`, and `_coerce_multi_select` refuses a bare string. The
    # widening would have written a value the destination's own coercer
    # rejects, which is the failure this table exists to prevent.
    "select": frozenset({"select"}),
    "multi_select": frozenset({"multi_select"}),
}


#: The choice types, whose OPTIONS have to agree as well as their type.
CHOICE_TYPES = frozenset({"select", "multi_select"})


def compatible(
    source_type: str,
    dest_type: str,
    source_options: Any = None,
    dest_options: Any = None,
) -> bool:
    """May a value of ``source_type`` be written into a ``dest_type`` field?

    ⚠️ **For a choice field the TYPE is not enough, and assuming it was is a
    real defect this module shipped once.** Two spaces can each hold a
    `select` named "Severity" with `[Low, High]` and `[S1, S2, S3]`. The types
    match, so the value `"High"` was copied straight across — and
    `_coerce_select` refuses exactly that value on every later write. The
    task then carried a value its own field rejects: unfilterable,
    uneditable except by hand, and reported as landed rather than dropped.

    So a choice field carries only when the destination's options are a
    SUPERSET of the source's. Anything else is an orphan, which means the
    member is told.
    """
    if dest_type not in COMPATIBLE_TYPES.get(source_type, frozenset()):
        return False
    if source_type not in CHOICE_TYPES:
        return True
    have = {str(o) for o in (source_options or [])}
    allowed = {str(o) for o in (dest_options or [])}
    return have <= allowed


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
    # 🔴 Which destination keys are already spoken for.
    #
    # Two source fields can legitimately resolve to ONE destination field, and
    # it is the ORDINARY case rather than a curiosity: `pm_custom_fields` is
    # UNIQUE on (project_id, field_key) and on nothing else, so two rows may
    # share a NAME — and WS-27bj's org-wide plus root-local union produces exactly
    # that, an org-wide `priority` beside a root-local `prio`, both called
    # "Priority". One matches by key, the other by name, and both aimed at the
    # same target.
    #
    # Without this, the second value overwrote the first in `landed` and the
    # loser never entered `drops` — so no warning, no `accept_drops` gate and
    # no timeline row. Which value survived followed JSONB key order, so it
    # differed task by task inside ONE bulk move. Found by review, 2026-09-19.
    claimed: dict[str, str] = {}
    # Exact-key matches first, so the order `load_definitions` happens to
    # return cannot decide which of two contenders wins a shared target.
    ordered = sorted(
        source_defs, key=lambda d: 0 if str(d["field_key"]) in by_key else 1
    )
    for definition in ordered:
        key = str(definition["field_key"])
        source_type = str(definition["field_type"])
        # An exact key match is the stronger claim and is resolved first, so a
        # name match can never displace it — see the second pass below.
        target = by_key.get(key) or by_name.get(str(definition["name"]).strip().lower())
        if target is None or not compatible(
            source_type,
            str(target["field_type"]),
            definition.get("options"),
            target.get("options"),
        ):
            orphans.append(definition)
            continue
        target_key = str(target["field_key"])
        if target_key in claimed:
            # Contested. The loser is an ORPHAN, which means the member is told
            # its value will be dropped instead of losing it silently.
            orphans.append(definition)
            continue
        claimed[target_key] = key
        mapping[key] = target_key
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
        # 🔴 `target in landed` is the second half of the collision guard.
        # `resolve_field_map` stops two DEFINITIONS claiming one target, and
        # this stops a hand-supplied `field_map` doing the same. A caller
        # posts the map, so the rule cannot live only where we build it.
        if target is not None and target in dest_keys and target not in landed:
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
    #: ⚠️ The field keys the member was SHOWN as dropping, from the preview.
    #:
    #: `accept_drops` alone is a bare yes to a question asked earlier. If an
    #: administrator deletes a destination field between the preview and the
    #: apply, the drop set grows and a stale yes would accept the extra loss
    #: too. Sending the shown set lets the server refuse a move that would
    #: drop MORE than was agreed to. Omitted means "no list", and the bare
    #: flag then behaves as before.
    accepted_drops: list[str] | None = None


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

    # 🔴 The guard `tasks.py` has always called on the single-task path, and
    # this one did not until review found it (2026-09-19).
    #
    # `tree.py` filters `personal_owner IS NULL`, so a team task moved into a
    # personal project leaves the company board and every grant holder loses
    # it, with nothing on screen to say why. `load_visible_task` admits a task
    # to its ASSIGNEE, so a caller with no grant on the source project could
    # have done this to MAX_BULK tasks in one call.
    #
    # In `_plan`, so the PREVIEW refuses it too — a card must not offer a move
    # the apply would reject.
    for task in tasks:
        await assert_move_keeps_privacy(db, task, dest_id)

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
    # ⚠️ A caller-supplied entry clears the SAME bar the resolved ones do.
    #
    # `chosen` used to merge `payload.field_map` straight over the resolved
    # map, and `apply_field_map` only ever checks `target in dest_keys`. So a
    # client could post `{"<a number key>": "<a boolean key>"}` and write a
    # value the destination's own coercer refuses — the exact hole closed for
    # `status_map` in the same commit that left this one open.
    src_by_key = {str(d["field_key"]): d for d in source_defs}
    dst_by_key = {str(d["field_key"]): d for d in dest_defs}
    chosen = dict(auto_fields)
    for raw_from, raw_to in (payload.field_map or {}).items():
        src = src_by_key.get(str(raw_from))
        dst = dst_by_key.get(str(raw_to))
        if src is None or dst is None:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Cannot map {raw_from!r} to {raw_to!r}: one of them is "
                    "not a field of its project."
                ),
            )
        if not compatible(
            str(src["field_type"]), str(dst["field_type"]),
            src.get("options"), dst.get("options"),
        ):
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Cannot map {src['name']!r} to {dst['name']!r}: a "
                    f"{src['field_type']} value does not fit a "
                    f"{dst['field_type']} field."
                ),
            )
        chosen[str(raw_from)] = str(raw_to)
    dest_keys = frozenset(str(d["field_key"]) for d in dest_defs)

    # What each task would actually lose, which is NOT the same as the orphan
    # DEFINITIONS: a field with no home costs nothing on a task that never
    # filled it in, and warning about that would be a warning about nothing.
    drops: dict[str, Any] = {}
    #: Each task's values AS THEY WOULD LAND, computed once and read twice —
    #: by the drop report and by the required-field check.
    landed_by_task: list[dict[str, Any]] = []
    # ⚠️ Only when the ROOT changes. `custom_fields` is untouched otherwise, so
    # computing drops for a same-root move made the endpoint demand
    # `accept_drops`, label the button "Move and drop", and then report a loss
    # that never happened — for a stale JSONB key from a deleted field.
    for task in tasks if dest_root != source_root else []:
        landed, dropped = apply_field_map(
            from_jsonb(task.custom_fields), chosen, dest_keys,
        )
        landed_by_task.append(landed)
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
        # ⚠️ `vocabulary_scope()`, not `project_id = :root`. A tag may be
        # ORG-WIDE (`project_id IS NULL`, migration 175), and the narrow read
        # reported every one of them as unregistered — telling the member a
        # tag they can see in the destination "is not a tag" there.
        known = {
            str(r.name).lower() for r in (await db.execute(
                text(f"SELECT name FROM pm_tags WHERE {vocabulary_scope()}"),
                {"root": dest_root},
            )).fetchall()
        }
        unregistered = [tag for tag in carried if tag.lower() not in known]

    # P1 — the destination's REQUIRED fields the selection does not satisfy.
    # `tasks.py` refuses a single move without them (migration 192), so
    # omitting the check here let the bulk path land tasks the narrow path
    # rejects. Reported by the preview AND enforced by the apply.
    # ⚠️ `_is_blank`, not a hand-rolled emptiness test.
    #
    # The first version used `in (None, "")`, which calls `"   "` and `[]`
    # PRESENT while `assert_required_fields_present` calls them blank. So the
    # bulk path would have landed a task the single-task path refuses — which
    # is the very disagreement this check was added to close. One predicate,
    # imported from the module that owns it.
    #
    # Computed from `landed_by_task`, which the drop loop already built: the
    # first version re-ran `apply_field_map` once per (required field x task),
    # i.e. 20000 times for a 500-task move into a root with 40 required fields.
    required_missing: list[str] = []
    if dest_root != source_root:
        for definition in (d for d in dest_defs if d.get("required")):
            key = str(definition["field_key"])
            if any(_is_blank(landed.get(key)) for landed in landed_by_task):
                required_missing.append(str(definition.get("name") or key))

    # 🔴 The destination's OWN lanes, returned with the plan.
    #
    # Without them the card's per-row override was a shipped no-op: the page
    # could only supply the SELECTED project's statuses, and the destination
    # is never the selected project (moving there is refused as "already
    # there"). So every dropdown rendered exactly one option — the automatic
    # landing — and D-PM-31's "adjustable" was unreachable. Found by review,
    # 2026-09-19.
    dest_lanes = [
        {"id": str(r.id), "name": r.name, "category": r.category}
        for r in (await db.execute(
            text(
                "SELECT id, name, category FROM pm_task_statuses "
                " WHERE project_id = CAST(:owner AS uuid) "
                " ORDER BY position, name"
            ),
            {"owner": dest_home},
        )).fetchall()
    ]

    return {
        "tasks": tasks,
        "required_missing": required_missing,
        "destination_statuses": dest_lanes,
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


def _refuse_unless_agreed(plan: dict[str, Any], payload: MoveIn) -> None:
    """Every refusal that depends on what the member was SHOWN.

    Lifted out of :func:`move_tasks` because the endpoint's complexity is
    almost entirely these three, and a reader looking for what the move DOES
    should not wade through what it refuses first.
    """
    if plan["source_project_id"] == plan["destination_project_id"]:
        raise HTTPException(
            status_code=422, detail="Those tasks are already there.",
        )
    if plan["required_missing"]:
        raise HTTPException(
            status_code=422,
            detail=(
                "The destination requires "
                + ", ".join(plan["required_missing"])
                + ", which these tasks do not carry. Fill them in first."
            ),
        )
    # D-PM-29. A caller cannot lose a value by omitting a flag it never knew
    # about, so the refusal names the keys and the remedy.
    if plan["drops"] and not payload.accept_drops:
        raise HTTPException(
            status_code=422,
            detail=(
                "This move drops values with no field in the destination: "
                + ", ".join(sorted(plan["drops"]))
                + ". Send accept_drops to confirm."
            ),
        )
    # ⚠️ Bound to the plan the member saw. A bare `accept_drops` is a yes to a
    # question asked earlier; if a field was deleted in between, the drop set
    # grows and a stale yes would accept the extra loss too.
    if payload.accepted_drops is not None:
        unexpected = sorted(set(plan["drops"]) - set(payload.accepted_drops))
        if unexpected:
            raise HTTPException(
                status_code=409,
                detail=(
                    "The destination changed since you looked. This would now "
                    "also drop " + ", ".join(unexpected)
                    + ". Open the move again to see what it costs."
                ),
            )


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

        _refuse_unless_agreed(plan, payload)

        # Every destination lane's category, so the completion correction
        # above has the one fact it needs without a second read.
        category_of: dict[str, str] = {
            str(lane["id"]): str(lane["category"])
            for lane in plan["destination_statuses"]
        }
        status_to = {
            str(row["from"]["id"]): str(row["to"]["id"])
            for row in plan["statuses"] if row["to"]
        }
        # The member's answer wins over the automatic one, and the automatic one
        # covers what the card did not -- `remap_task_statuses` orders it this
        # way so a card built from stale counts cannot leave a task behind.
        # 🔴 A caller-supplied landing is validated against the destination's
        # tree before it is trusted. `pm_tasks.status_id` has an FK, so any
        # real uuid passes it — including another department's lane, and
        # (because RI runs with RLS bypassed) another TENANT's. `admin.py`
        # validates its caller-supplied mapping the same way; this endpoint
        # did not until review found it.
        for source_id, wanted in (payload.status_map or {}).items():
            # ⚠️ The status HOME, not the destination ROOT. Since migration 196
            # a subproject may own its lanes, so the two differ — and the root
            # is wrong in both directions: it refuses every lane the card just
            # offered (those come from `dest_home`), and it accepts a root lane
            # the destination board does not render. `core.py` states this rule
            # for this exact call. My first fix read the root and disagreed
            # with the lane list four functions above it.
            await require_status_in_project(db, plan["dest_home"], str(wanted))
            status_to[str(source_id)] = str(wanted)
        type_to = {
            str(row["from"]["id"]): (row["to"]["id"] if row["to"] else None)
            for row in plan["types"]
        }

        moved: list[str] = []
        for task in plan["tasks"]:
            values: dict[str, Any] = {"project_id": plan["destination_project_id"]}

            landing_category: str | None = None
            if plan["crosses_status_set"]:
                landing = status_to.get(str(task.status_id))
                landing_category = category_of.get(str(landing or ""))
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
                # The guard `tasks.py` calls, on the values as they LAND.
                # `required_missing` above is the preview's answer; this is
                # the one that refuses, so the two cannot drift.
                await assert_required_fields_present(
                    db, plan["destination_root_id"], landed,
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
            lost_type = (
                plan["crosses_root"]
                and task.type_id
                and not type_to.get(str(task.type_id))
            )
            # ⚠️ **NOT `apply_status_transition`, and the reason is exact.**
            # That helper resolves the lane's owner from `task.project_id` —
            # the task's OWN project, which inside this loop is still the
            # SOURCE, because nothing has been written yet. Every lane here
            # belongs to the DESTINATION's set, so it refused each one with
            # "That status does not belong to this project" and rolled the
            # whole move back. It was the only case that sets a landing, so
            # the fix broke the entire feature. Found by review, 2026-09-19.
            #
            # It also spawns a recurrence successor on a close, which would
            # mint fifty tasks nobody asked for out of one bulk move.
            #
            # `remap_task_statuses` is the precedent for a BULK remap and it
            # does neither: it writes the lane and corrects completion from
            # the lane's CATEGORY. Same rule here, one task at a time.
            if landing_category is not None:
                closing = landing_category in CLOSING_CATEGORIES
                was_closed = task.completed_at is not None
                if closing != was_closed:
                    values["completed_at"] = now() if closing else None

            await update_row(db, "pm_tasks", str(task.id), values)

            if dropped:
                await record_activity(
                    db, activity_type="system", created_by=actor(user),
                    task_id=str(task.id),
                    # 🔴 The VALUES, not just the keys. D-PM-29 promises the
                    # old value is readable in history and the card repeats
                    # that promise to the member; `"; ".join(sorted(dropped))`
                    # yielded the KEYS and kept none of it.
                    body=(
                        "Dropped on move, no field in the destination: "
                        + "; ".join(f"{k}={dropped[k]!r}" for k in sorted(dropped))
                    ),
                    meta={"dropped_custom_fields": dropped},
                )
            if lost_type:
                await record_activity(
                    db, activity_type="system", created_by=actor(user),
                    task_id=str(task.id),
                    body=(
                        "Task type cleared on move: the destination has no "
                        "type of that name"
                    ),
                    meta={"cleared_type_id": str(task.type_id)},
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
