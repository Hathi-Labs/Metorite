"""Projects · tree — the project hierarchy and the grants that scope it.

Spec: ``project-docs/specs/project_management_app.md`` §4 (``tree.py`` row).

    GET    /projects/tree                        → the granted forest, nested
    GET    /projects/nodes                       → the same, flat
    POST   /projects/nodes
    GET    /projects/nodes/{id}
    PATCH  /projects/nodes/{id}
    DELETE /projects/nodes/{id}                  → what cascaded
    POST   /projects/nodes/{id}/move
    GET    /projects/nodes/{id}/grants
    POST   /projects/nodes/{id}/grants
    DELETE /projects/nodes/{id}/grants/{grant_id}

Departments, projects and subprojects are all rows in ``pm_projects`` — a
department is simply a root project whose grant names a Center's group. The
paths say "nodes" rather than "projects" for the plain reason that
``/projects/projects/{id}`` reads like a typo; the resource is a node in one
tree.
"""

from __future__ import annotations

from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException
from gateway.routes.projects.core import (
    CLOSING_CATEGORIES,
    LIFECYCLE_FIELDS,
    NODE_KINDS,
    PROJECT_SOURCES,
    RUN_STATES,
    assert_node_grammar,
    assert_run_state_allowed,
    node_kind,
    node_level,
    GrantModel,
    ProjectIn,
    ProjectModel,
    _tenant_session,
    actor,
    assert_no_project_cycle,
    clean_payload,
    count_where,
    diff_changes,
    emit,
    insert_row,
    load_visible_project,
    record_activity,
    record_field_change,
    require_organization,
    resolve_visibility,
    root_project_id,
    router,
    row_to_dict,
    task_visibility_clause,
    update_row,
    validate_icon_slot,
    validate_choice,
    validate_grant_subject,
    validate_lifecycle_settings,
)
from pydantic import BaseModel
from sqlalchemy import text

#: Fields whose change is worth a timeline entry. Deliberately not every column:
#: `updated_at` moves on every write, and a diff that always has an entry is a
#: diff nobody reads.
_TRACKED_PROJECT_FIELDS: tuple[str, ...] = (
    "name", "description", "status", "lead", "parent_project_id",
    # WS-27z — a lifecycle-policy change is exactly the edit somebody asks
    # "who turned this on, and when" about, six months later.
    "archive_after_months", "close_after_months", "timezone",
    # Migration 194 — Space Settings. A space that changed colour overnight
    # is the same "who did that" question, and the icon is how people find
    # the space in a long sidebar.
    "icon", "icon_slot",
)


#: Migration 194 — the two Space Settings fields. Grouped because they are
#: written together by one dialog and refused together everywhere else.
IDENTITY_FIELDS: tuple[str, ...] = ("icon", "icon_slot")


def _refuse_identity_off_a_space(values: dict, level: str) -> None:
    """The icon and its hue belong to a SPACE (owner directive 2026-08-31).

    A project draws its run state in that slot and a folder draws a folder,
    so a value here would be stored and never rendered. Refused rather than
    ignored, for `assert_run_state_allowed`'s reason: a silently dropped
    field answers 200 and changes nothing.
    """
    if level == "space":
        return
    offered = [f for f in IDENTITY_FIELDS if f in values]
    if offered:
        raise HTTPException(
            status_code=422,
            detail=(
                f"{offered} belong to a space. A {level} draws its own "
                f"marker, so an icon set here would never be shown."
            ),
        )


def _refuse_lifecycle_on_child(values: dict, parent_project_id: object) -> None:
    """WS-27z — the policy is a ROOT-project setting; the subtree inherits.

    Statuses, types, custom fields and tags already work this way (root-keyed,
    subtree-wide), and the sweep acts on ``pm_tasks.root_project_id`` — so a
    value on a child row would be inert. Refusing the write keeps the inert
    case unreachable rather than merely documented.
    """
    if parent_project_id is None:
        return
    offered = [f for f in LIFECYCLE_FIELDS if f in values]
    if offered:
        raise HTTPException(
            status_code=422,
            detail=(
                f"{offered} are root-project settings — the root's lifecycle "
                f"policy governs its whole subtree. Set them on the root."
            ),
        )

async def _project_generation(db, node_id: str) -> int:
    """How many PROJECT generations sit at *node_id*, itself included.

    Folders are transparent: a folder reports its nearest project
    ancestor's count. A space is 1, a project 2, a subproject 3 — the
    numbers `assert_node_grammar` caps (migration 193).
    """
    return int((await db.execute(
        text(
            "WITH RECURSIVE anc AS ("
            "  SELECT id, parent_project_id, kind FROM pm_projects"
            "    WHERE id = CAST(:pid AS uuid)"
            "  UNION ALL"
            "  SELECT p.id, p.parent_project_id, p.kind FROM pm_projects p"
            "    JOIN anc a ON p.id = a.parent_project_id"
            ") SELECT count(*) FROM anc"
            "   WHERE coalesce(kind, 'project') = 'project'"
        ),
        {"pid": node_id},
    )).scalar() or 0)


async def _subtree_project_depth(db, node_id: str) -> int:
    """The longest PROJECT chain inside *node_id*'s subtree, itself included.

    Folders contribute nothing to any chain. A lone project is 1, a lone
    folder 0, a project with subprojects 2. A move re-checks the grammar
    with this number, because a subtree keeps its internal shape wherever
    it lands.
    """
    return int((await db.execute(
        text(
            "WITH RECURSIVE sub AS ("
            "  SELECT id, CASE WHEN coalesce(kind, 'project') = 'project'"
            "    THEN 1 ELSE 0 END AS d"
            "  FROM pm_projects WHERE id = CAST(:pid AS uuid)"
            "  UNION ALL"
            "  SELECT p.id, s.d + CASE WHEN coalesce(p.kind, 'project')"
            "    = 'project' THEN 1 ELSE 0 END"
            "  FROM pm_projects p JOIN sub s ON p.parent_project_id = s.id"
            ") SELECT coalesce(max(d), 0) FROM sub"
        ),
        {"pid": node_id},
    )).scalar() or 0)


#: Seeded on every ROOT project. The owner reshapes these in the app; they exist
#: so a new project has a working board on its first render rather than an empty
#: status picker.
#:
#: ⚠️ **The colours here must equal what `CATEGORY_HUES` derives from the
#: category** (`workbench/control_plane/src/lib/statusAccent.ts`), because a
#: stored colour OUTRANKS the category — that is what lets an owner choose. So a
#: seed that disagrees is a seed that silently overrides the shared vocabulary on
#: every project nobody has customised, and /projects goes back to looking
#: different from /tasks. It did: this tuple used to seed `To do` blue and
#: `In progress` amber against a category map of gray and blue, and two of the
#: four default lanes rendered differently in the two apps.
#:
#: These are defaults, not decisions. If the shared vocabulary changes, change
#: them here too; `test_seed_status_colours_match_the_shared_vocabulary` fails
#: until you do.
_SEED_STATUSES: tuple[tuple[str, str, int, str, bool], ...] = (
    ("Backlog",     "gray",   10, "backlog",     True),
    ("To do",       "gray",   20, "todo",        False),
    ("In progress", "blue",   30, "in_progress", False),
    ("Done",        "green",  40, "done",        False),
)

#: 'Epic' is the one system type (§3.4) and carries the only structural rule in
#: the task hierarchy. There is deliberately no 'Subtask' type.
_SEED_TYPES: tuple[tuple[str, str | None, bool, bool], ...] = (
    ("Task", "circle-check", True, False),
    ("Bug",  "bug",          False, False),
    ("Epic", "layers",       False, True),
)


class MoveProject(BaseModel):
    parent_project_id: str | None = None
    position: float | None = None


class GrantIn(BaseModel):
    subject: str


class DeleteResponse(BaseModel):
    """R7/R8 — a destructive route says what it destroyed, per table."""

    deleted: str
    cascaded: dict[str, int]


# ── Reads ───────────────────────────────────────────────────────────────────

async def _visible_projects(db: Any, user: UserContext) -> list[Any]:
    vis = await resolve_visibility(db, user)
    # Personal projects are excluded from every TEAM read (147/§3.11). They are
    # ordinary projects the grant model already scopes to one person, so this is
    # not a security filter — it is that "My tasks" does not belong in a
    # department tree beside Sales and Operations. The personal surface reads
    # them through `/projects/my/*`.
    return (await db.execute(
        text(
            f"SELECT * FROM pm_projects "
            f"WHERE {vis.project_clause()} AND personal_owner IS NULL "
            f"ORDER BY position NULLS LAST, name"
        ),
        vis.params,
    )).fetchall()


@router.get("/tree")
async def get_tree(user: UserContext = Depends(get_current_user)) -> dict:
    """The caller's forest, nested.

    Built in Python from the flat visible set rather than by a recursive read:
    the visibility subquery already walks the grant closure once, and nesting
    what came back costs nothing. A node whose parent is *not* visible surfaces
    as a root here — that is not a bug to fix by hiding it, it is the shape of a
    subtree granted to a Center without its parent department.
    """
    async with _tenant_session() as db:
        rows = await _visible_projects(db, user)

    nodes = {str(r.id): {**row_to_dict(r, ProjectModel), "children": []} for r in rows}
    roots: list[dict] = []
    for node in nodes.values():
        parent = nodes.get(str(node.get("parent_project_id") or ""))
        (parent["children"] if parent else roots).append(node)
    return {"rows": roots, "total": len(nodes)}


@router.get("/nodes")
async def list_nodes(user: UserContext = Depends(get_current_user)) -> dict:
    async with _tenant_session() as db:
        rows = await _visible_projects(db, user)
    return {"rows": [row_to_dict(r, ProjectModel) for r in rows], "total": len(rows)}


@router.get("/nodes/{project_id}")
async def get_node(
    project_id: str, user: UserContext = Depends(get_current_user),
) -> dict:
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        row = await load_visible_project(db, vis, project_id)
        return row_to_dict(row, ProjectModel)


@router.get("/summary")
async def get_portfolio_summary(
    user: UserContext = Depends(get_current_user),
) -> dict:
    """The same roll-up, one level up: every space the caller can see.

    Analytics inside the Projects app (owner directive 2026-08-31). It is
    deliberately the SAME shape as a node's summary, so one dashboard
    component draws both — a second response shape would be a second
    component, and then two places for a counting rule to be wrong.

    The scope here is the visible forest rather than one subtree, so
    `children` are the SPACES and `projects` counts every project under
    them.
    """
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        rows = await _visible_projects(db, user)

        by_parent: dict[str, list] = {}
        for row in rows:
            by_parent.setdefault(str(row.parent_project_id or ""), []).append(row)
        roots = by_parent.get("", [])

        # Which space each node belongs to, so a task counts toward exactly
        # one line. Walked down from the roots rather than up from each
        # node: one pass over rows already in memory, no query per task.
        space_of: dict[str, str] = {}

        def _claim(node_id: str, space_id: str) -> None:
            space_of[node_id] = space_id
            for child in by_parent.get(node_id, []):
                _claim(str(child.id), space_id)

        for root in roots:
            _claim(str(root.id), str(root.id))

        counts = (await db.execute(
            text(
                f"SELECT t.project_id, s.category, count(*) AS n,"
                f"       count(*) FILTER ("
                f"         WHERE t.due_at IS NOT NULL"
                f"           AND t.due_at < now()"
                f"           AND s.category <> ALL(CAST(:closed AS text[]))"
                f"       ) AS overdue"
                f"  FROM pm_tasks t"
                f"  JOIN pm_task_statuses s ON s.id = t.status_id"
                f" WHERE t.archived_at IS NULL"
                f"   AND ({task_visibility_clause(vis, 't')})"
                f" GROUP BY t.project_id, s.category"
            ),
            {**vis.params, "closed": sorted(CLOSING_CATEGORIES)},
        )).fetchall()

    totals: dict[str, int] = {}
    overdue_total = 0
    per_space: dict[str, dict] = {}
    for row in counts:
        space_id = space_of.get(str(row.project_id))
        # A task in a project the forest read did not return (a personal
        # project, say) belongs to no space here and is not counted — the
        # alternative is a total no line adds up to.
        if space_id is None:
            continue
        category = str(row.category or "todo")
        totals[category] = totals.get(category, 0) + int(row.n)
        overdue_total += int(row.overdue or 0)
        entry = per_space.setdefault(
            space_id, {"tasks": 0, "overdue": 0, "by_category": {}},
        )
        entry["tasks"] += int(row.n)
        entry["overdue"] += int(row.overdue or 0)
        entry["by_category"][category] = (
            entry["by_category"].get(category, 0) + int(row.n)
        )

    return {
        "id": "portfolio",
        "name": "All spaces",
        "level": "portfolio",
        "tasks": sum(totals.values()),
        "overdue": overdue_total,
        "by_category": totals,
        "projects": sum(
            1 for r in rows
            if node_kind(getattr(r, "kind", None)) == "project"
            and r.parent_project_id is not None
        ),
        "children": [
            {
                "id": str(root.id),
                "name": root.name,
                "kind": node_kind(getattr(root, "kind", None)),
                "status": root.status,
                "archived": root.archived_at is not None,
                **{
                    k: per_space.get(str(root.id), {}).get(k, v)
                    for k, v in (
                        ("tasks", 0), ("overdue", 0), ("by_category", {}),
                    )
                },
            }
            for root in roots
        ],
    }


@router.get("/nodes/{project_id}/summary")
async def get_node_summary(
    project_id: str, user: UserContext = Depends(get_current_user),
) -> dict:
    """The roll-up a space, a folder or a parent project shows instead of a board.

    Owner directive 2026-08-31: *a space is not a project*. It shows a
    dashboard of everything beneath it, and it has none of the views a
    project has. A folder does the same, and a project with subprojects
    aggregates them into its own view.

    ⚠️ **The whole subtree, counted ONCE, in two queries.** A dashboard that
    fetched each descendant's tasks separately would be N+1 across a real
    workspace, and the numbers would drift while it walked. So the totals
    and the per-child breakdown are each one grouped read over the same
    subtree.

    ⚠️ **Visibility is the caller's, not the node's.** The subtree walk runs
    over `pm_projects` unrestricted — a subtree is a structural fact — but
    every task count goes through `vis.task_clause()`. A member who can see
    a space but only one project inside it therefore gets a dashboard whose
    totals match what they could reach by clicking, which is the property
    that keeps a roll-up from becoming a disclosure channel.
    """
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        node = await load_visible_project(db, vis, project_id)
        level = node_level(
            node_kind(getattr(node, "kind", None)),
            await _project_generation(db, project_id),
        )

        # Every visible PROJECT under this node (folders excluded — they
        # hold no work), each with the id of the child of `project_id` it
        # sits under, so the dashboard can group by the row a user clicks.
        descendants = (await db.execute(
            text(
                "WITH RECURSIVE sub AS ("
                "  SELECT id, parent_project_id, name, kind, status,"
                "         archived_at, id AS branch_id"
                "    FROM pm_projects WHERE parent_project_id ="
                "         CAST(:pid AS uuid)"
                "  UNION ALL"
                "  SELECT p.id, p.parent_project_id, p.name, p.kind,"
                "         p.status, p.archived_at, s.branch_id"
                "    FROM pm_projects p JOIN sub s"
                "      ON p.parent_project_id = s.id"
                ") SELECT * FROM sub"
            ),
            {"pid": project_id},
        )).fetchall()

        # The node itself counts too: a project that carries subprojects
        # aggregates ITS OWN tasks with theirs, which is what "selecting the
        # project aggregates the subproject data" means.
        scope_ids = [project_id] + [str(r.id) for r in descendants]
        by_id = {str(r.id): r for r in descendants}

        rows = (await db.execute(
            text(
                f"SELECT t.project_id, s.category, count(*) AS n,"
                # CLOSING_CATEGORIES, not a second literal list: a category
                # added to the closed set must not leave this one counting
                # finished work as late.
                f"       count(*) FILTER ("
                f"         WHERE t.due_at IS NOT NULL"
                f"           AND t.due_at < now()"
                f"           AND s.category <> ALL(CAST(:closed AS text[]))"
                f"       ) AS overdue"
                f"  FROM pm_tasks t"
                f"  JOIN pm_task_statuses s ON s.id = t.status_id"
                f" WHERE t.project_id = ANY(CAST(:ids AS uuid[]))"
                f"   AND t.archived_at IS NULL"
                f"   AND ({task_visibility_clause(vis, 't')})"
                f" GROUP BY t.project_id, s.category"
            ),
            {
                **vis.params,
                "ids": scope_ids,
                "closed": sorted(CLOSING_CATEGORIES),
            },
        )).fetchall()

    # Fold the grouped rows up two ways: the node's own totals, and one
    # line per direct child. Done in Python because the second fold keys on
    # `branch_id`, which the SQL above already carried down the walk.
    totals: dict[str, int] = {}
    overdue_total = 0
    per_branch: dict[str, dict] = {}
    for row in rows:
        category = str(row.category or "todo")
        totals[category] = totals.get(category, 0) + int(row.n)
        overdue_total += int(row.overdue or 0)
        pid = str(row.project_id)
        branch = pid if pid == project_id else str(
            getattr(by_id.get(pid), "branch_id", pid),
        )
        entry = per_branch.setdefault(
            branch, {"tasks": 0, "overdue": 0, "by_category": {}},
        )
        entry["tasks"] += int(row.n)
        entry["overdue"] += int(row.overdue or 0)
        entry["by_category"][category] = (
            entry["by_category"].get(category, 0) + int(row.n)
        )

    children = []
    for row in descendants:
        if str(row.parent_project_id) != project_id:
            continue
        stats = per_branch.get(str(row.id), {})
        children.append({
            "id": str(row.id),
            "name": row.name,
            "kind": node_kind(getattr(row, "kind", None)),
            "status": row.status,
            "archived": row.archived_at is not None,
            "tasks": stats.get("tasks", 0),
            "overdue": stats.get("overdue", 0),
            "by_category": stats.get("by_category", {}),
        })

    return {
        "id": project_id,
        "name": node.name,
        "level": level,
        "tasks": sum(totals.values()),
        "overdue": overdue_total,
        "by_category": totals,
        # Projects only — a folder holds no work of its own, so counting it
        # as one would inflate every space's project count.
        "projects": sum(
            1 for r in descendants
            if node_kind(getattr(r, "kind", None)) == "project"
        ),
        "children": children,
    }


# ── Writes ──────────────────────────────────────────────────────────────────

async def _seed_root(db: Any, project_id: str, created_by: str) -> None:
    """Give a new root project a working board.

    Statuses, types and one list view. The counter row is NOT seeded here — it
    is created by ``next_task_number``'s upsert on the first task, so there is
    one place that can allocate a number and no way to have a counter without
    having gone through it.
    """
    for name, color, position, category, is_default in _SEED_STATUSES:
        await insert_row(db, "pm_task_statuses", {
            "project_id": project_id, "name": name, "color": color,
            "position": position, "category": category, "is_default": is_default,
        })
    for name, icon, is_default, is_system in _SEED_TYPES:
        await insert_row(db, "pm_task_types", {
            "project_id": project_id, "name": name, "icon": icon,
            "is_default": is_default, "is_system": is_system,
            # WS-27ae / P-28 — the seed stamps the FLAG rather than relying on
            # a later reader recognising the name. Migration 168 backfilled the
            # roots that already existed; this is what keeps new ones true.
            # Derived from `is_system`, not written as a fourth tuple field:
            # `Epic` is the only system type §3.4 has, so a second source of
            # truth in the same literal is a way for them to disagree.
            "is_epic": is_system,
        })
    await insert_row(db, "pm_views", {
        "project_id": project_id, "name": "All tasks", "view_type": "list",
        "config": {"sort_by": "created_at", "filters": {"include_subtree": True}},
        "position": 100.0, "created_by": created_by,
    })
    await insert_row(db, "pm_views", {
        "project_id": project_id, "name": "Board", "view_type": "board",
        "config": {
            "column_by": "status", "sort_by": "manual",
            "filters": {"include_subtree": True},
        },
        "position": 200.0, "created_by": created_by,
    })


@router.post("/nodes", status_code=201)
async def create_node(
    payload: ProjectIn, user: UserContext = Depends(get_current_user),
) -> dict:
    values = clean_payload(payload)
    name = str(values.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="A project needs a name.")
    # RUN_STATES, not PROJECT_STATUSES: the latter still carries the retained
    # `archived` so the pre-restart gateway keeps validating through the deploy
    # window (R6), but accepting it HERE would let a caller set the run state to
    # 'archived' without stamping `archived_at` — reintroducing the two-facts-one-
    # column defect D-PM-25 exists to remove. Filing a project is
    # POST /nodes/{id}/archive.
    validate_choice(values.get("status"), RUN_STATES, "project status")
    validate_choice(values.get("source"), PROJECT_SOURCES, "source")
    validate_choice(values.get("kind"), NODE_KINDS, "node kind")
    validate_icon_slot(values.get("icon_slot"))
    validate_lifecycle_settings(values)
    _refuse_lifecycle_on_child(values, values.get("parent_project_id"))
    values["name"] = name
    values["created_by"] = actor(user)
    kind = node_kind(values.get("kind"))

    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        parent_id = values.get("parent_project_id")
        parent_row = None
        if parent_id:
            # Creating INSIDE a project requires seeing it — otherwise a caller
            # could graft a subtree onto another department by guessing an id,
            # and inherit that department's grants for it.
            parent_row = await load_visible_project(db, vis, str(parent_id))

        # The tree grammar (migration 193): space → [folder] → project →
        # [folder] → subproject, and stop. Checked before the org resolve so
        # a bad shape is refused as a shape, not as a permission.
        parent_gen = (
            await _project_generation(db, str(parent_id)) if parent_id else 0
        )
        assert_node_grammar(
            kind=kind,
            parent_kind=(
                node_kind(getattr(parent_row, "kind", None))
                if parent_row is not None else None
            ),
            parent_generation=parent_gen,
            subtree_depth=1 if kind == "project" else 0,
        )

        # Level-gated fields, checked once the level is knowable.
        level = node_level(
            kind, parent_gen + (1 if kind == "project" else 0),
        )
        if values.get("status") is not None:
            assert_run_state_allowed(level)
        _refuse_identity_off_a_space(values, level)

        # WS-29a. This is the ONE place in the package that decides a tenant:
        # `pm_projects` is the root of every other `pm_*` row, and migration
        # 158's trigger derives the key for all of them from here. Written for
        # a child project too, not just a root — the trigger then REFUSES it if
        # it disagrees with the parent's, which turns "the caller's org and the
        # parent's org differ" into a refused write rather than a silent graft.
        #
        # AFTER the parent check, deliberately: a caller with no organization
        # asking to create inside a project they cannot see must still get R5's
        # 404. Answering 403 first would confirm the project exists.
        values["organization_id"] = require_organization(vis)

        row = await insert_row(db, "pm_projects", values)
        project_id = str(row.id)

        if parent_id is None:
            await _seed_root(db, project_id, actor(user))
            # A root project starts org-visible so a solo org is not immediately
            # locked out of the thing it just created. It is an ordinary grant
            # row: the importer writes `group:<slug>` instead (§7.1), and
            # removing this one is how scoping starts.
            await insert_row(db, "pm_project_grants", {
                "project_id": project_id, "subject": "org",
                "created_by": actor(user),
            })

        await record_activity(
            db, activity_type="system", created_by=actor(user),
            project_id=project_id, body=f"Project '{name}' created",
        )
        result = row_to_dict(row, ProjectModel)

    await emit("pm.project.created", {"project_id": project_id, "name": name})
    return result


@router.patch("/nodes/{project_id}")
async def patch_node(
    project_id: str, payload: ProjectIn,
    user: UserContext = Depends(get_current_user),
) -> dict:
    values = clean_payload(payload)
    # RUN_STATES, not PROJECT_STATUSES: the latter still carries the retained
    # `archived` so the pre-restart gateway keeps validating through the deploy
    # window (R6), but accepting it HERE would let a caller set the run state to
    # 'archived' without stamping `archived_at` — reintroducing the two-facts-one-
    # column defect D-PM-25 exists to remove. Filing a project is
    # POST /nodes/{id}/archive.
    validate_choice(values.get("status"), RUN_STATES, "project status")
    validate_choice(values.get("source"), PROJECT_SOURCES, "source")
    validate_icon_slot(values.get("icon_slot"))
    validate_lifecycle_settings(values)
    # Re-parenting is a MOVE, with its own cycle check and root re-stamping.
    # Accepting it here as an ordinary field would skip both.
    if "parent_project_id" in values:
        raise HTTPException(
            status_code=422,
            detail="Use POST /projects/nodes/{id}/move to re-parent a project.",
        )
    # A kind is set at creation and never changes: a folder becoming a
    # project (or back) would re-shape the tree without passing the grammar,
    # and every rule in `assert_node_grammar` could be dodged that way.
    if "kind" in values:
        raise HTTPException(
            status_code=422,
            detail="A node's kind is set at creation and cannot change.",
        )

    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        before = await load_visible_project(db, vis, project_id)
        _refuse_lifecycle_on_child(
            values, getattr(before, "parent_project_id", None),
        )
        # Level-gated fields (migrations 193/194): a run state belongs to a
        # project or a subproject, an icon to a space. The generation walk
        # is skipped unless one of those fields is actually offered — a
        # rename must not pay for a recursive query.
        if values.get("status") is not None or any(
            f in values for f in IDENTITY_FIELDS
        ):
            level = node_level(
                node_kind(getattr(before, "kind", None)),
                await _project_generation(db, project_id),
            )
            if values.get("status") is not None:
                assert_run_state_allowed(level)
            _refuse_identity_off_a_space(values, level)
        if not values:
            return row_to_dict(before, ProjectModel)
        after = await update_row(db, "pm_projects", project_id, values)
        changes = diff_changes(before, after, _TRACKED_PROJECT_FIELDS)
        if changes:
            # The ONE field_change door (WS-27w) — none of the tracked project
            # fields is FK-valued today, but the door is what keeps that claim
            # checked rather than remembered, and a project-description edit
            # session coalesces like a task's.
            await record_field_change(
                db, created_by=actor(user), project_id=project_id,
                changes=changes,
            )
        result = row_to_dict(after, ProjectModel)

    await emit("pm.project.updated", {"project_id": project_id})
    return result


@router.post("/nodes/{project_id}/move")
async def move_node(
    project_id: str, payload: MoveProject,
    user: UserContext = Depends(get_current_user),
) -> dict:
    """Re-parent or reorder a project.

    Moving a subtree re-stamps ``root_project_id`` on **every task beneath it**,
    because that column is what scopes statuses, types and the task counter. A
    move that left it stale would leave tasks pointing at another project's
    status rows — visible immediately as lanes that do not exist on the board.
    """
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        moved = await load_visible_project(db, vis, project_id)
        new_parent = payload.parent_project_id
        parent_row = None
        if new_parent:
            parent_row = await load_visible_project(db, vis, str(new_parent))
        await assert_no_project_cycle(db, project_id, new_parent)

        # The grammar holds through a move, with the subtree's own shape in
        # the sum: a project that carries subprojects cannot land under a
        # project, and a folder cannot land under a folder (migration 193).
        assert_node_grammar(
            kind=node_kind(getattr(moved, "kind", None)),
            parent_kind=(
                node_kind(getattr(parent_row, "kind", None))
                if parent_row is not None else None
            ),
            parent_generation=(
                await _project_generation(db, str(new_parent))
                if new_parent else 0
            ),
            subtree_depth=await _subtree_project_depth(db, project_id),
        )

        values: dict[str, Any] = {"parent_project_id": new_parent}
        if payload.position is not None:
            values["position"] = payload.position
        row = await update_row(db, "pm_projects", project_id, values)

        new_root = await root_project_id(db, project_id)
        await db.execute(
            text(
                "UPDATE pm_tasks SET root_project_id = CAST(:root AS uuid) "
                "WHERE project_id IN ("
                "  WITH RECURSIVE sub AS ("
                "    SELECT id FROM pm_projects WHERE id = CAST(:pid AS uuid)"
                "    UNION ALL"
                "    SELECT p.id FROM pm_projects p JOIN sub s"
                "      ON p.parent_project_id = s.id"
                "  ) SELECT id FROM sub)"
            ),
            {"root": new_root, "pid": project_id},
        )
        await record_activity(
            db, activity_type="system", created_by=actor(user),
            project_id=project_id, body="Project moved",
        )
        result = row_to_dict(row, ProjectModel)

    await emit("pm.project.moved", {"project_id": project_id})
    return result


@router.delete("/nodes/{project_id}")
async def delete_node(
    project_id: str, user: UserContext = Depends(get_current_user),
) -> DeleteResponse:
    """Delete a project and everything under it, and say what that was.

    R7/R8. The counts are read BEFORE the delete: afterwards the rows are gone
    and the honest number is unobtainable, which is how a destructive route ends
    up reporting zero.
    """
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        await load_visible_project(db, vis, project_id)

        subtree = (await db.execute(
            text(
                "WITH RECURSIVE sub AS ("
                "  SELECT id FROM pm_projects WHERE id = CAST(:pid AS uuid)"
                "  UNION ALL"
                "  SELECT p.id FROM pm_projects p JOIN sub s"
                "    ON p.parent_project_id = s.id"
                ") SELECT count(*) FROM sub"
            ),
            {"pid": project_id},
        )).scalar() or 0
        tasks = (await db.execute(
            text(
                "SELECT count(*) FROM pm_tasks WHERE project_id IN ("
                "  WITH RECURSIVE sub AS ("
                "    SELECT id FROM pm_projects WHERE id = CAST(:pid AS uuid)"
                "    UNION ALL"
                "    SELECT p.id FROM pm_projects p JOIN sub s"
                "      ON p.parent_project_id = s.id"
                "  ) SELECT id FROM sub)"
            ),
            {"pid": project_id},
        )).scalar() or 0
        grants = await count_where(db, "pm_project_grants", "project_id", project_id)

        await db.execute(
            text("DELETE FROM pm_projects WHERE id = CAST(:pid AS uuid)"),
            {"pid": project_id},
        )

    await emit("pm.project.deleted", {"project_id": project_id})
    return DeleteResponse(
        deleted=project_id,
        cascaded={
            # Subprojects include this project itself, so the caller sees the
            # true number of rows that disappeared rather than a count that
            # excludes the one they named.
            "projects": int(subtree),
            "tasks": int(tasks),
            "grants": int(grants),
        },
    )


# ── Archive (WS-27bg) ───────────────────────────────────────────────────────
#
# The other axis (D-PM-25). `status` says whether work is flowing; this says
# whether you want to see it. A project may be archived in any run state, and
# archiving does not change the run state — a paused project that gets filed is
# still paused when it comes back.

#: One project's subtree, itself included. A named constant because this file
#: had two inline copies of it inside `delete_node` and a third would be the
#: point at which they start disagreeing. (Those two are left alone: they are
#: existing code and CLAUDE.md §5 binds new work, not a conformance refactor.)
_SUBTREE_SQL = """
WITH RECURSIVE sub AS (
    SELECT id FROM pm_projects WHERE id = CAST(:pid AS uuid)
    UNION ALL
    SELECT p.id FROM pm_projects p JOIN sub s ON p.parent_project_id = s.id
) SELECT id FROM sub
"""


class ArchiveResponse(BaseModel):
    """What the archive touched, per table — the same honesty `DeleteResponse`
    owes, for the same reason: a route that changes what a caller can see says
    how much.

    ``open_tasks`` is a WARNING, never a refusal. Archiving is a filing
    decision, not a claim that the work finished, so unfinished work is
    something the caller should be told about and then allowed to do.
    """

    project_id: str
    archived: bool
    #: Projects whose `archived_at` this call actually stamped or cleared —
    #: excludes any already in the target state, so a double-click reports 0.
    projects: int
    #: Open tasks in the subtree at the moment of archiving. Reported, not acted
    #: on: **no `pm_tasks` row is written by this endpoint** (D-PM-26).
    open_tasks: int


async def _subtree_open_tasks(db: Any, project_id: str) -> int:
    """Open (not closed-category) tasks anywhere under one project."""
    return (await db.execute(
        text(
            f"SELECT count(*) FROM pm_tasks t "
            f"WHERE t.project_id IN ({_SUBTREE_SQL}) "
            f"  AND t.archived_at IS NULL "
            f"  AND NOT EXISTS ("
            f"    SELECT 1 FROM pm_task_statuses s "
            f"    WHERE s.id = t.status_id AND s.category = ANY(:closed))"
        ),
        {"pid": project_id, "closed": list(CLOSING_CATEGORIES)},
    )).scalar() or 0


@router.post("/nodes/{project_id}/archive")
async def archive_node(
    project_id: str, user: UserContext = Depends(get_current_user),
) -> ArchiveResponse:
    """File a project and its subtree out of the default surfaces.

    **The subtree is stamped at write time, and no task row is touched.** Both
    halves are deliberate (migration 171's header carries the full argument):
    stamping tens of project rows keeps every task read a plain indexed join
    instead of a recursive ancestor walk, and D-PM-26 forbids the cascade onto
    `pm_tasks` — thousands of rows, a timeline entry and a delta-sync bump each,
    and a restore problem afterwards.

    Idempotent. A project already archived reports ``projects: 0`` rather than
    422, because a double-clicked Archive button is not an error to teach the
    user about.
    """
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        row = await load_visible_project(db, vis, project_id)

        # Counted BEFORE the write, for `delete_node`'s reason: afterwards the
        # honest number is unobtainable and a destructive-ish route ends up
        # reporting zero.
        open_tasks = await _subtree_open_tasks(db, project_id)

        stamped = (await db.execute(
            text(
                f"UPDATE pm_projects SET archived_at = now(), "
                f"    archived_root_id = CAST(:pid AS uuid) "
                f"WHERE id IN ({_SUBTREE_SQL}) AND archived_at IS NULL"
            ),
            {"pid": project_id},
        )).rowcount or 0

        if stamped:
            await record_activity(
                db, activity_type="system", created_by=actor(user),
                project_id=project_id,
                body=f"Project '{row.name}' archived",
            )
        result = ArchiveResponse(
            project_id=project_id, archived=True,
            projects=int(stamped), open_tasks=int(open_tasks),
        )

    if stamped:
        await emit("pm.project.archived", {"project_id": project_id})
    return result


@router.post("/nodes/{project_id}/unarchive")
async def unarchive_node(
    project_id: str, user: UserContext = Depends(get_current_user),
) -> ArchiveResponse:
    """Bring a project and everything its archive filed back.

    Clears exactly the rows this project's archive stamped
    (``archived_root_id = :pid``), which is what the column exists for: a
    subproject somebody had ALREADY archived on its own carries its own id
    there and survives, instead of being silently un-filed by an ancestor's
    restore.

    🔴 **A project swept in by an ancestor's archive is REFUSED, not
    half-restored.** Lifting one child out of its parent's archive leaves a
    visible subtree inside a filed one — an incoherent state that would then
    need explaining forever. Refusing it and naming the ancestor is the same
    move ``_refuse_lifecycle_on_child`` makes: keep the incoherent case
    unreachable rather than merely documented.
    """
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        row = await load_visible_project(db, vis, project_id)

        if getattr(row, "archived_at", None) is None:
            return ArchiveResponse(
                project_id=project_id, archived=False, projects=0,
                open_tasks=await _subtree_open_tasks(db, project_id),
            )

        origin = getattr(row, "archived_root_id", None)
        if origin is not None and str(origin) != project_id:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"This project was filed as part of project {origin}'s "
                    f"archive. Unarchive that project instead — restoring this "
                    f"one alone would leave it visible inside an archived tree."
                ),
            )

        cleared = (await db.execute(
            text(
                "UPDATE pm_projects SET archived_at = NULL, "
                "    archived_root_id = NULL "
                "WHERE archived_root_id = CAST(:pid AS uuid)"
            ),
            {"pid": project_id},
        )).rowcount or 0

        if cleared:
            await record_activity(
                db, activity_type="system", created_by=actor(user),
                project_id=project_id,
                body=f"Project '{row.name}' unarchived",
            )
        result = ArchiveResponse(
            project_id=project_id, archived=False, projects=int(cleared),
            open_tasks=await _subtree_open_tasks(db, project_id),
        )

    if cleared:
        await emit("pm.project.unarchived", {"project_id": project_id})
    return result


# ── Grants ──────────────────────────────────────────────────────────────────

@router.get("/nodes/{project_id}/grants")
async def list_grants(
    project_id: str, user: UserContext = Depends(get_current_user),
) -> dict:
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        await load_visible_project(db, vis, project_id)
        rows = (await db.execute(
            text(
                "SELECT * FROM pm_project_grants "
                "WHERE project_id = CAST(:pid AS uuid) ORDER BY subject"
            ),
            {"pid": project_id},
        )).fetchall()
        return {
            "rows": [row_to_dict(r, GrantModel) for r in rows], "total": len(rows),
        }


@router.post("/nodes/{project_id}/grants", status_code=201)
async def create_grant(
    project_id: str, payload: GrantIn,
    user: UserContext = Depends(get_current_user),
) -> dict:
    """Grant a subject access to this project's subtree.

    A caller-reachable creation path is deliberate, not implied: WS-14 C1's
    review found an acceptance that could go green with no way to create a
    grant at all, and the same hole here would make every scoping test a
    fixture INSERT proving nothing about the API.
    """
    subject = validate_grant_subject(payload.subject)
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        await load_visible_project(db, vis, project_id)
        row = (await db.execute(
            text(
                "INSERT INTO pm_project_grants (project_id, subject, created_by) "
                "VALUES (CAST(:pid AS uuid), :subject, :who) "
                "ON CONFLICT (project_id, subject) DO UPDATE SET subject = "
                "EXCLUDED.subject RETURNING *"
            ),
            {"pid": project_id, "subject": subject, "who": actor(user)},
        )).fetchone()
        await record_activity(
            db, activity_type="system", created_by=actor(user),
            project_id=project_id, body=f"Granted to {subject}",
        )
        return row_to_dict(row, GrantModel)


@router.delete("/nodes/{project_id}/grants/{grant_id}")
async def delete_grant(
    project_id: str, grant_id: str,
    user: UserContext = Depends(get_current_user),
) -> dict:
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        await load_visible_project(db, vis, project_id)
        row = (await db.execute(
            text(
                "DELETE FROM pm_project_grants "
                "WHERE id = CAST(:gid AS uuid) AND project_id = CAST(:pid AS uuid) "
                "RETURNING subject"
            ),
            {"gid": grant_id, "pid": project_id},
        )).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Grant not found")
        await record_activity(
            db, activity_type="system", created_by=actor(user),
            project_id=project_id, body=f"Revoked {row.subject}",
        )
        return {"deleted": grant_id, "subject": row.subject}
