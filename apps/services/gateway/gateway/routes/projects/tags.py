"""Projects · tags — the registry the research notes said to build (WS-27m).

Spec: ``project-docs/specs/project_management_app.md`` §11.2 item 5, §11.10.

    GET    /projects/nodes/{project_id}/tags        → with a usage count each
    POST   /projects/nodes/{project_id}/tags
    PATCH  /projects/tags/{tag_id}                  → rename or recolour
    POST   /projects/tags/{tag_id}/merge            → into another tag
    DELETE /projects/tags/{tag_id}                  → strips it from every task

``paca_pm_research_2026-08.md`` row 13 refused Paca's bare array — "no registry,
no colors, no rename/merge" — and §5 shipped ``pm_tasks.tags TEXT[]`` in its
place with the registry named as additive later. This is it. Migration 156 says
why the array stays.

**Applying an unregistered tag REGISTERS it.** Refusing would make tagging a
two-step errand — leave the task, create the tag, come back — which is how
tagging gets abandoned, and an abandoned tag set is worse than a messy one. The
registry's value is rename, merge and colour, not gatekeeping.

**The cost of that, stated: every typo becomes a tag.** Which is exactly why
`merge` is here and is not optional. Free tagging without merge is how a tag
list rots into forty near-duplicates nobody can fix.

**Identity is case-insensitive; display is case-preserving.** Somebody typing
"Bug" when "bug" exists means the tag they can already see. The task's array
stores the REGISTRY's spelling, which is what makes "one spelling per tag" true
rather than aspirational — and what lets a rename be one statement.
"""

from __future__ import annotations

import re
from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException
from gateway.routes.projects.core import (
    _tenant_session,
    actor,
    clean_payload,
    load_visible_project,
    org_wide_exists,
    governed_tasks_scope,
    is_org_wide,
    refuse_org_wide_rescope,
    refuse_org_wide_write,
    require_known_tenant,
    require_org_vocabulary_edit,
    require_known_tenant,
    require_org_vocabulary_write,
    require_row,
    resolve_visibility,
    root_project_id,
    router,
    shadowed,
    update_row,
    vocabulary_scope,
)
from pydantic import BaseModel
from sqlalchemy import text

#: Longest tag. Mirrors migration 156's CHECK and pinned by a test that reads it.
MAX_TAG = 64

#: Most tags one task may carry. A task wearing thirty labels has none — the cap
#: is the difference between a tag and a second description.
MAX_TAGS_PER_TASK = 25

#: Most tags one project may register, so auto-registration cannot be a way to
#: fill a table from a text box.
MAX_TAGS_PER_PROJECT = 500

_WHITESPACE = re.compile(r"\s+")


def normalise_tag(raw: Any) -> str | None:
    """One incoming tag → its storable form, or ``None`` if it is not a tag.

    Trimmed, and internal whitespace collapsed: ``"needs  review"`` and
    ``"needs review"`` are the same label typed twice, and letting both in
    creates two tags that render almost identically and filter separately.

    ``None`` rather than an exception for a blank, because a blank arrives from
    a trailing comma in a text box — a typo, not a request.
    """
    if not isinstance(raw, str):
        return None
    cleaned = _WHITESPACE.sub(" ", raw).strip()
    if not cleaned:
        return None
    if len(cleaned) > MAX_TAG:
        raise HTTPException(
            status_code=422,
            detail=f"Tag '{cleaned[:20]}…' is longer than {MAX_TAG} characters. "
                   f"A tag is a label, not a note.",
        )
    return cleaned


def canonicalise(raw: Any, registry: dict[str, str]) -> list[str]:
    """A task's incoming tag list → what to store.

    ``registry`` maps ``lower(name)`` → the registered display form.

    Three things happen, and each is one of the failures a bare array has:

    * every tag is replaced by its REGISTERED spelling, so "Bug" written on a
      project that knows "bug" is stored as "bug" and one filter finds both;
    * duplicates are collapsed **case-insensitively**, since ``["bug", "Bug"]``
      is one tag typed twice;
    * order is the order they were given, because a tag list somebody arranged
      should not come back alphabetised.
    """
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise HTTPException(status_code=422, detail="tags must be a list of strings.")
    out: list[str] = []
    seen: set[str] = set()
    for entry in raw:
        cleaned = normalise_tag(entry)
        if cleaned is None:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(registry.get(key, cleaned))
    if len(out) > MAX_TAGS_PER_TASK:
        raise HTTPException(
            status_code=422,
            detail=f"At most {MAX_TAGS_PER_TASK} tags per task; got {len(out)}.",
        )
    return out


def unregistered(names: list[str], registry: dict[str, str]) -> list[str]:
    """The tags in ``names`` the registry has never seen, in first-seen order.

    These are what auto-registration creates. Deduplicated case-insensitively
    so one request that says ``["Bug", "bug"]`` does not try to insert twice and
    trip the unique index on its own payload.
    """
    fresh: list[str] = []
    seen: set[str] = set()
    for name in names:
        key = name.lower()
        if key in registry or key in seen:
            continue
        seen.add(key)
        fresh.append(name)
    return fresh


def merged_tags(current: list[str], source: str, target: str) -> list[str]:
    """One task's tags after ``source`` is merged into ``target``.

    Pure, because the interesting case is the one that is easy to get wrong: a
    task carrying **both** tags must end with ``target`` **once**. Appending
    blindly leaves a duplicate that then renders twice and survives the next
    merge as well.
    """
    out: list[str] = []
    seen: set[str] = set()
    for name in current:
        replaced = target if name.lower() == source.lower() else name
        key = replaced.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(replaced)
    return out


def renamed_tags(current: list[str], before: str, after: str) -> list[str]:
    """One task's tags after ``before`` is renamed to ``after``.

    Distinct from a merge on purpose: a rename asserts the destination did not
    already exist (`patch_tag` refuses otherwise with a 409 that names merge),
    so this preserves position rather than collapsing. A tag keeping its place
    in the list is the difference between a rename and a reshuffle.
    """
    return [after if name.lower() == before.lower() else name for name in current]


# ── Routes ──────────────────────────────────────────────────────────────────

class TagIn(BaseModel):
    name: str | None = None
    color: str | None = None
    description: str | None = None
    #: WS-27bj. ``"org"`` mints an org-wide tag; anything else, including the
    #: default, keeps today's per-project behaviour verbatim. A field rather than
    #: a second endpoint so there stays ONE create path — a parallel one is how
    #: the two drift on the next validation somebody adds.
    scope: str | None = None


class MergeIn(BaseModel):
    into_tag_id: str


async def load_registry_rows(db: Any, root: str) -> list[Any]:
    """This project's EFFECTIVE tag rows: org-wide ∪ root-local (WS-27bj).

    ⚠️ **The shadowing here is a correctness rule, not a preference.**
    ``pm_tasks.tags`` stores display TEXT rather than a foreign key (migration
    156), so an org-wide "bug" and a root-local "bug" are the *same tag on every
    task* while being two registry rows with two colours. Returning both would
    make "what colour is this tag" depend on which row a renderer reached first.
    """
    rows = (await db.execute(
        text(f"SELECT * FROM pm_tags WHERE {vocabulary_scope()}"),
        {"root": root},
    )).fetchall()
    return shadowed(rows, lambda r: r.name.lower())


async def load_registry(db: Any, root: str) -> dict[str, str]:
    """``lower(name)`` → the display form a task's array should store.

    Org-wide tags are IN here, and that is the point of putting the union on the
    write path too: without it, the first task in every project to type "bug"
    would auto-register a root-local duplicate and shadow the organization's own
    tag — the registry would fill up with exactly the near-duplicates it exists
    to prevent, one project at a time.
    """
    return {r.name.lower(): r.name for r in await load_registry_rows(db, root)}


async def register(
    db: Any, root: str, names: list[str], *, by: str,
    organization_id: str | None = None,
) -> int:
    """Insert tags this project has not seen. Returns how many were created.

    ``organization_id`` set means register these **org-wide** (WS-27bj) rather
    than on ``root``: migration 161's trigger fills the tenant from the parent
    project, so a row with no project must carry it explicitly.

    ``ON CONFLICT DO NOTHING`` against the case-insensitive unique index, so two
    requests racing to first-use the same tag both succeed and neither 500s —
    which matters because this runs on an ordinary task edit, not on an admin
    screen somebody is paying attention to.
    """
    if not names:
        return 0
    # The cap counts the EFFECTIVE set. A picker showing 500 tags is unusable
    # whether the organization or the project contributed them, and counting only
    # the local half would let the real number reach 1000.
    existing = int((await db.execute(
        text(f"SELECT count(*) FROM pm_tags WHERE {vocabulary_scope()}"),
        {"root": root},
    )).scalar() or 0)
    if existing + len(names) > MAX_TAGS_PER_PROJECT:
        raise HTTPException(
            status_code=409,
            detail=f"This project would exceed {MAX_TAGS_PER_PROJECT} tags. "
                   f"Merge some before adding more.",
        )
    created = 0
    for name in names:
        created += (await db.execute(
            text(
                "INSERT INTO pm_tags (project_id, organization_id, name, "
                "                     created_by) "
                "VALUES (CAST(:root AS uuid), CAST(:org AS uuid), :name, :by) "
                "ON CONFLICT DO NOTHING"
            ),
            {
                "root": None if organization_id else root,
                "org": organization_id,
                "name": name, "by": by,
            },
        )).rowcount or 0
    return created


async def apply_task_tags(
    db: Any, root: str, raw: Any, *, by: str,
) -> list[str]:
    """The full write path for one task's tags: canonicalise, then register.

    Called from `tasks.py` for both create and patch, so a tag can never enter
    the system by a route that skipped the registry — which is the only way the
    registry stays true.
    """
    registry = await load_registry(db, root)
    names = canonicalise(raw, registry)
    await register(db, root, unregistered(names, registry), by=by)
    return names


async def _root_for(db: Any, vis: Any, project_id: str) -> str:
    await load_visible_project(db, vis, project_id)
    return await root_project_id(db, project_id)


def _row(row: Any, count: int | None = None) -> dict[str, Any]:
    out = {
        "id": str(row.id),
        # ``None``, never the string ``"None"``: this is org-wide (WS-27bj), and
        # a client reads it to know whether the row is editable here.
        "project_id": None if row.project_id is None else str(row.project_id),
        "name": row.name,
        "color": row.color,
        "description": row.description,
    }
    if count is not None:
        out["task_count"] = count
    return out


@router.get("/nodes/{project_id}/tags")
async def list_tags(
    project_id: str, user: UserContext = Depends(get_current_user),
) -> dict:
    """Every tag on this tree, with how many tasks wear it.

    The count is the whole reason anybody opens this screen: it is what tells
    you which of two near-duplicate tags to merge into the other, and computing
    it per row in the browser would mean shipping every task to get it.

    Org-wide ∪ root-local, root-local shadowing (WS-27bj).
    """
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        root = await _root_for(db, vis, project_id)
        rows = (await db.execute(
            text(
                # ⚠️ The count is scoped to `:root`, NOT to `g.project_id`. For a
                # root-local row the two are the same value — the WHERE pins it —
                # but an org-wide row has no `project_id`, and correlating on it
                # would silently report every org-wide tag as used by 0 tasks.
                # A wrong number here is worse than a missing one: it is the
                # number people merge on.
                "SELECT g.*, ("
                "  SELECT count(*) FROM pm_tasks t "
                "   WHERE t.root_project_id = CAST(:root AS uuid) "
                "     AND t.archived_at IS NULL "
                "     AND g.name = ANY(t.tags)"
                ") AS task_count "
                "  FROM pm_tags g "
                f" WHERE {vocabulary_scope('g')} "
                " ORDER BY g.name"
            ),
            {"root": root},
        )).fetchall()
        rows = shadowed(rows, lambda r: r.name.lower())
        return {
            "rows": [_row(r, int(r.task_count or 0)) for r in rows],
            "total": len(rows),
        }


@router.post("/nodes/{project_id}/tags", status_code=201)
async def create_tag(
    project_id: str, payload: TagIn,
    user: UserContext = Depends(get_current_user),
) -> dict:
    values = clean_payload(payload)
    name = normalise_tag(values.get("name"))
    if name is None:
        raise HTTPException(status_code=422, detail="A tag needs a name.")
    org_wide = values.get("scope") == "org"
    if org_wide:
        require_org_vocabulary_write(user)
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        root = await _root_for(db, vis, project_id)
        if org_wide:
            require_known_tenant(vis, "tag")
            # A shadowed org-wide row is absent from the effective registry by
            # design, so the org-wide arm has to ask the table directly or its
            # INSERT would collide with a row it could not see.
            if await org_wide_exists(db, "pm_tags", root, name):
                raise HTTPException(
                    status_code=409,
                    detail=f"An organization-wide tag '{name}' already exists.",
                )
        else:
            # ⚠️ Only a ROOT-LOCAL clash is refused. A project registering its
            # own "bug" while the organization also has one is the shadowing
            # D-PM-16 permits, not a duplicate — and `shadowed` has already made
            # the root-local row the winner, so a non-NULL `project_id` is what
            # separates the two cases.
            clash = next(
                (r for r in await load_registry_rows(db, root)
                 if r.name.lower() == name.lower() and r.project_id is not None),
                None,
            )
            if clash is not None:
                # 409 naming the spelling that already exists, rather than
                # letting the unique index fire: "already exists" is useless
                # without saying what it looks like, since the difference is only
                # capitalisation.
                raise HTTPException(
                    status_code=409,
                    detail=f"'{clash.name}' already exists here.",
                )
        await register(
            db, root, [name], by=actor(user),
            organization_id=vis.organization_id if org_wide else None,
        )
        row = (await db.execute(
            text(
                "SELECT * FROM pm_tags "
                " WHERE lower(name) = :key "
                + ("   AND project_id IS NULL AND organization_id = ("
                   "         SELECT p.organization_id FROM pm_projects p"
                   "          WHERE p.id = CAST(:root AS uuid))"
                   if org_wide else
                   "   AND project_id = CAST(:root AS uuid)")
            ),
            {"root": root, "key": name.lower()},
        )).fetchone()
        if values.get("color") or values.get("description"):
            row = await update_row(db, "pm_tags", str(row.id), {
                k: v for k, v in values.items() if k in ("color", "description")
            })
        return _row(row, 0)


@router.patch("/tags/{tag_id}")
async def patch_tag(
    tag_id: str, payload: TagIn,
    user: UserContext = Depends(get_current_user),
) -> dict:
    """Rename or recolour. A rename rewrites every task that wears the tag.

    **Renaming onto a name that already exists is a 409, not a silent merge.**
    They are different operations with different outcomes — a merge destroys one
    tag — and quietly doing the destructive one because the names collided is
    the kind of surprise that makes people stop using a rename button.
    """
    values = clean_payload(payload)
    values.pop("scope", None)     # WS-27bj: selects where a NEW row lands only
    new_name = normalise_tag(values.get("name")) if "name" in values else None
    if "name" in values and new_name is None:
        raise HTTPException(status_code=422, detail="A tag needs a name.")

    async with _tenant_session() as db:
        existing = await require_row(db, "pm_tags", tag_id, "Tag")
        org_wide = is_org_wide(existing)
        if org_wide:
            # Owner decision, 2026-09-20: an org-wide tag MAY be renamed, by
            # somebody who can change organization settings, after being shown
            # how many tasks it touches. Merge and delete stay refused — those
            # are the halves that cannot be walked back.
            require_org_vocabulary_edit(user, existing.name)
            refuse_org_wide_rescope(
                values, frozenset({"name", "color", "description"}), "tag",
            )
            vis = await resolve_visibility(db, user)
            require_known_tenant(vis, "tag")
        else:
            refuse_org_wide_write(existing, "tag")
            vis = await resolve_visibility(db, user)
            await load_visible_project(db, vis, str(existing.project_id))
        root = str(existing.project_id)

        renamed = 0
        if new_name and new_name.lower() != existing.name.lower():
            if org_wide:
                # ⚠️ The collision has to be checked across the ORGANIZATION,
                # and the reason is the storage: `pm_tasks.tags` holds display
                # TEXT. Rewriting the organization's "bug" to "defect" inside a
                # project that already has its own "defect" would MERGE the two
                # on every task there — a destructive act nobody asked for,
                # reported as a rename. The per-project rule above already
                # refuses a rename onto an existing name; this is that rule at
                # the scope the row actually has.
                clash = (await db.execute(
                    text(
                        "SELECT id, name, project_id FROM pm_tags "
                        " WHERE lower(name) = :name "
                        "   AND id <> CAST(:tid AS uuid) "
                        "   AND organization_id = CAST(:org AS uuid) LIMIT 1"
                    ),
                    {
                        "name": new_name.lower(), "tid": tag_id,
                        "org": str(existing.organization_id),
                    },
                )).fetchone()
                if clash is not None:
                    raise HTTPException(
                        status_code=409,
                        detail=f"'{clash.name}' already exists "
                               f"{await _where_tag_lives(db, clash)}. Renaming "
                               f"'{existing.name}' onto it would merge the two on "
                               f"every task that wears either.",
                    )
            else:
                registry = await load_registry(db, root)
                if new_name.lower() in registry:
                    raise HTTPException(
                        status_code=409,
                        detail=f"'{registry[new_name.lower()]}' already exists. "
                               f"Merge '{existing.name}' into it instead — a rename "
                               f"would leave two tags with one name.",
                    )
            renamed = await _rewrite(db, existing, existing.name, new_name)

        write = {k: v for k, v in values.items() if k in ("color", "description")}
        if new_name:
            write["name"] = new_name
        row = await update_row(db, "pm_tags", tag_id, write) if write else existing
        return {**_row(row), "retagged": renamed}


@router.get("/tags/{tag_id}/impact")
async def tag_impact(
    tag_id: str, user: UserContext = Depends(get_current_user),
) -> dict:
    """How much a rename of this tag would rewrite, BEFORE it is asked for.

    Owner decision, 2026-09-20: an organization-wide tag may be renamed, and
    the person renaming it is shown the size of the act first. This is the
    read that makes that possible, and it is the same shape as
    ``tasks/move/preview`` — a preview endpoint the dialog reads, rather than a
    count smuggled into the write's response where it arrives too late to
    inform a decision.

    ⚠️ **``projects`` is the count of distinct TREES, not of nodes.**
    ``pm_tasks.root_project_id`` is what the tag is scoped by, so a tag on
    forty tasks spread over two trees reads "40 tasks in 2 projects", and a
    subproject is not counted separately from its root. Saying which it means
    costs one word and prevents the number being read as "two places to go and
    look".
    """
    async with _tenant_session() as db:
        existing = await require_row(db, "pm_tags", tag_id, "Tag")
        vis = await resolve_visibility(db, user)
        if is_org_wide(existing):
            require_org_vocabulary_edit(user, existing.name)
            require_known_tenant(vis, "tag")
        else:
            await load_visible_project(db, vis, str(existing.project_id))
        where, params = governed_tasks_scope(existing)
        row = (await db.execute(
            text(
                f"SELECT count(*) AS tasks, "
                f"       count(DISTINCT root_project_id) AS projects "
                f"  FROM pm_tasks WHERE {where} AND :name = ANY(tags)"
            ),
            {**params, "name": existing.name},
        )).fetchone()
        return {
            "tag": existing.name,
            "scope": "organization" if is_org_wide(existing) else "project",
            "tasks": int(row.tasks or 0),
            "projects": int(row.projects or 0),
        }


@router.post("/tags/{tag_id}/merge")
async def merge_tag(
    tag_id: str, payload: MergeIn,
    user: UserContext = Depends(get_current_user),
) -> dict:
    """Fold this tag into another, then delete it.

    The answer to the real failure mode of free tagging: the set rots into
    near-duplicates and, without this, nobody can fix it. A task carrying both
    ends with the target **once** — `merged_tags` is where that is decided, and
    it is pure so the case can be asserted directly.
    """
    async with _tenant_session() as db:
        source = await require_row(db, "pm_tags", tag_id, "Tag")
        target = await require_row(db, "pm_tags", payload.into_tag_id, "Tag")
        # Both ends: merging INTO an org-wide tag would rewrite tasks across one
        # project only while claiming an organization-wide result, and merging
        # one AWAY would delete a row every other project is still using.
        refuse_org_wide_write(source, "tag")
        refuse_org_wide_write(target, "tag")
        vis = await resolve_visibility(db, user)
        await load_visible_project(db, vis, str(source.project_id))
        if str(source.project_id) != str(target.project_id):
            # 404-shaped rather than 403 (R5), but the reason is worth saying:
            # tags are root-scoped, so a cross-project merge would move a label
            # into a tree that never agreed to it.
            raise HTTPException(
                status_code=404,
                detail="Both tags must belong to the same project.",
            )
        if str(source.id) == str(target.id):
            raise HTTPException(
                status_code=422, detail="A tag cannot be merged into itself.",
            )

        moved = await _rewrite(db, source, source.name, target.name)
        await db.execute(
            text("DELETE FROM pm_tags WHERE id = CAST(:tid AS uuid)"),
            {"tid": tag_id},
        )
        return {
            "merged": source.name, "into": target.name, "retagged": moved,
        }


@router.delete("/tags/{tag_id}")
async def delete_tag(
    tag_id: str, user: UserContext = Depends(get_current_user),
) -> dict:
    """Delete a tag and strip it from every task.

    Same judgement as `delete_field` (WS-27l), and the opposite of Paca's: a
    label left on tasks with nothing defining it is invisible until somebody
    recreates the name. The count is reported (R7/R8), because losing data
    silently is what makes people distrust a delete.
    """
    async with _tenant_session() as db:
        existing = await require_row(db, "pm_tags", tag_id, "Tag")
        refuse_org_wide_write(existing, "tag")
        vis = await resolve_visibility(db, user)
        await load_visible_project(db, vis, str(existing.project_id))
        where, params = governed_tasks_scope(existing)
        stripped = (await db.execute(
            text(
                f"UPDATE pm_tasks SET tags = array_remove(tags, :name) "
                f" WHERE {where} AND :name = ANY(tags)"
            ),
            {**params, "name": existing.name},
        )).rowcount or 0
        await db.execute(
            text("DELETE FROM pm_tags WHERE id = CAST(:tid AS uuid)"),
            {"tid": tag_id},
        )
        return {
            "deleted": tag_id, "name": existing.name,
            "cascaded": {"tasks_untagged": int(stripped)},
        }


async def _where_tag_lives(db: Any, row: Any) -> str:
    """"in 'Bootloader'", or "organization-wide". For one refusal message.

    A second point read rather than a ``LEFT JOIN`` on the clash query, and the
    join is the one this replaced. The collision path is rare, the name is only
    wanted when it fires, and a two-table join whose second table contributes
    one string to an error message is a harder query for every reader of the
    first one.
    """
    if row.project_id is None:
        return "organization-wide"
    found = (await db.execute(
        text("SELECT name FROM pm_projects WHERE id = CAST(:pid AS uuid)"),
        {"pid": str(row.project_id)},
    )).fetchone()
    return f"in '{found.name}'" if found is not None else "in another project"


async def _rewrite(db: Any, owner: Any, before: str, after: str) -> int:
    """Replace one tag with another across the tasks ``owner`` governs.

    Done in Python over the affected rows rather than as a clever array
    expression in SQL, because the merge case — a task holding BOTH tags must
    end with the target once — is where this is easy to get wrong, and
    `merged_tags` is a pure function a test can pin. An `array_replace` would
    leave the duplicate.

    ⚠️ **Takes the ROW, not a root id.** An org-wide tag governs every task in
    the organization, and passing ``str(row.project_id)`` for one of those
    sends the literal string ``"None"`` into a uuid cast. See
    :func:`governed_tasks_scope`.
    """
    where, params = governed_tasks_scope(owner)
    rows = (await db.execute(
        text(
            f"SELECT id, tags FROM pm_tasks WHERE {where} AND :before = ANY(tags)"
        ),
        {**params, "before": before},
    )).fetchall()
    for row in rows:
        await db.execute(
            text("UPDATE pm_tasks SET tags = :tags WHERE id = :tid"),
            {"tags": merged_tags(list(row.tags or []), before, after), "tid": row.id},
        )
    return len(rows)


__all__ = [
    "MAX_TAG",
    "MAX_TAGS_PER_PROJECT",
    "MAX_TAGS_PER_TASK",
    "apply_task_tags",
    "canonicalise",
    "load_registry",
    "load_registry_rows",
    "merged_tags",
    "normalise_tag",
    "register",
    "renamed_tags",
    "unregistered",
]
