"""Projects · who may delete, and who may write a grant (WS-64 OP-8).

Spec: ``project-docs/specs/one_projects_app.md`` D-OP-11, D-OP-12 and §9 OP-8.

THE one rule of each act. Every door calls this module, and no door has a
copy of the rule: ``DELETE /tasks/{id}``, ``POST /tasks/{id}/agent-delete``,
the bulk delete, ``DELETE /nodes/{id}``, the two grant routes, and the
``can_delete`` flag that the task and project reads give the app and the chat.

**Delete (D-OP-12).** A task, a project or a sub-project is deleted by:

1. the member who created it (``created_by``, NOT NULL on ``pm_tasks`` and
   ``pm_projects`` since migration 146, compared without case);
2. a lead of its project. For a task that is the task's own project. For a
   project it is the project itself. In both cases the lead of ANY ancestor
   project counts too, because a lead is accountable for the subtree below
   (``pm_projects.lead``, an email, nullable);
3. a member who may manage Projects settings (``core.can_manage_settings``,
   the permission ``projects:settings:write``).

A member who only sees the row cannot delete it. The rule is checked on the
row the member names. The subtasks of a task delete, and the tasks and
sub-projects inside a project delete, follow that one check, because the
owner's rule is about what the member deletes.

**Grants (D-OP-11).** Only a lead of the project, or of an ancestor, and a
``can_manage_settings`` member write a grant.

**A personal tree (WS-64 OP-6b, fix round 1).** No grant on a node of a
member's personal tree changes by a grant route, for anyone. The owner, a
lead and a Projects admin all get :data:`PERSONAL_GRANT_REFUSED`. See
:func:`grant_write_refusal`.

**The lead (D-OP-11, fix round 1).** The ``lead`` column IS the authority
above, so a write to it is a grant write. A change of ``lead`` on a project
needs :func:`may_write_grant` on that project. A new project that names a
lead needs :func:`may_write_grant` on its parent. One exception: a member
who creates a new ROOT may name themself its lead, because a member may
lead the space they create. Doors: ``POST /nodes`` and ``PATCH /nodes/{id}``.
The chat tools ``create_project`` and ``update_project`` call those routes.

**Moves (D-OP-14).** A move changes who sees a row, so a move needs more
than a view. A re-parent of a project needs :func:`may_write_grant` on the
moved project. A move of a task to another project needs
:func:`may_delete_task` on the task where it is now. The two rules are the
same rule, so :func:`may_move_task` is that function, and a client that
hides Move reads ``can_delete``. A reorder under the same parent, and a
status move inside one project, need only a view. Doors:
``POST /nodes/{id}/move``, ``tasks.move_task_in`` (``POST /tasks/{id}/move``
and the My Tasks organize) and ``POST /tasks/move`` with its preview. The
chat tools ``move_project`` and ``move_task`` call those routes. A subtask
that a move carries follows the one check on the task the member names,
as a delete does.

Each read runs inside the caller's tenant session, so RLS bounds it.
Fence: ``tests/unit/test_projects_write_authority.py`` (R8).
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from gateway.routes.projects.core import actor, can_manage_settings
from sqlalchemy import text

#: The 403 of a task delete. The chat (``skill_projects.guarded``) and the
#: MCP edge (``agent_edge.tools``) hold copies, and a test pins them equal.
TASK_DELETE_REFUSED = (
    "Only the person who created this task, the project lead or a Projects "
    "admin can delete it."
)
#: The 403 of a project delete.
PROJECT_DELETE_REFUSED = (
    "Only the person who created this project, the project lead or a Projects "
    "admin can delete it."
)
#: The 403 of a grant write.
GRANT_WRITE_REFUSED = (
    "Only the project lead or a Projects admin can change who can see this project."
)

#: The 403 of a grant write on a personal tree (WS-64 OP-6b, fix round 1).
#: D-OP-2 and §8 Q4: a personal space is not shared, so its grants do not
#: change by the grant routes. The bootstraps of the tree write the owner's
#: grant directly (``personal._grant_owner``, ``item_lens``), not through a
#: route, so this rule does not touch them.
PERSONAL_GRANT_REFUSED = (
    "Access to a personal space cannot be changed. Move the work to a team "
    "project to share it."
)

#: The 403 of a change of ``lead``.
LEAD_WRITE_REFUSED = (
    "Only the project lead or a Projects admin can change who leads this project."
)
#: The 403 of a new project that names a lead under a parent.
LEAD_CREATE_REFUSED = (
    "Only the lead of the parent project or a Projects admin can name a lead "
    "here. Create the project without a lead, or ask them."
)
#: The 403 of a new root that names a lead other than its creator.
LEAD_ROOT_REFUSED = (
    "You can name only yourself as the lead of a new space. A Projects admin "
    "can name another member."
)
#: The 403 of a re-parent of a project.
PROJECT_MOVE_REFUSED = (
    "Only the project lead or a Projects admin can move this project to "
    "another place."
)
#: The 403 of a move of a task to another project.
TASK_MOVE_REFUSED = (
    "Only the person who created this task, the project lead or a Projects "
    "admin can move it to another project."
)

#: Each project that the member leads, and each project below one. ``UNION``
#: and not ``UNION ALL``, so a cycle in the tree ends the walk.
_LED_SUBTREE_SQL = (
    "WITH RECURSIVE led AS ("
    "  SELECT id FROM pm_projects WHERE lower(lead) = :me"
    "  UNION"
    "  SELECT p.id FROM pm_projects p JOIN led ON p.parent_project_id = led.id"
    ") SELECT id FROM led"
)


#: One row when the project, or any project above it, is in a personal tree.
#: ``personal_owner`` is stamped on every node that the personal seam mints,
#: and the walk up also finds a node that ``POST /nodes`` put under a personal
#: parent without the stamp. ``UNION``, so a cycle in the tree ends the walk.
_PERSONAL_TREE_SQL = (
    "WITH RECURSIVE up AS ("
    "  SELECT id, parent_project_id, personal_owner FROM pm_projects"
    "   WHERE id = CAST(:pid AS uuid)"
    "  UNION"
    "  SELECT p.id, p.parent_project_id, p.personal_owner FROM pm_projects p"
    "   JOIN up ON p.id = up.parent_project_id"
    ") SELECT 1 FROM up WHERE personal_owner IS NOT NULL LIMIT 1"
)

#: The owner of the personal tree that holds the project, nearest first.
_PERSONAL_OWNER_SQL = _PERSONAL_TREE_SQL.replace(
    "SELECT 1 FROM up", "SELECT personal_owner FROM up"
)


def _me(user: Any) -> str:
    """The caller's email in lower case, or ``""`` when the context has none."""
    who = actor(user).strip().lower()
    return "" if who == "anonymous" else who


def _made_by(row: Any, me: str) -> bool:
    made = getattr(row, "created_by", None)
    if made is None and isinstance(row, dict):
        made = row.get("created_by")
    return bool(me) and str(made or "").strip().lower() == me


def _field(row: Any, name: str) -> Any:
    return row.get(name) if isinstance(row, dict) else getattr(row, name, None)


async def led_projects(db: Any, user: Any) -> frozenset[str]:
    """The ids of the projects that *user* leads, with every project below them."""
    me = _me(user)
    if not me:
        return frozenset()
    rows = (await db.execute(text(_LED_SUBTREE_SQL), {"me": me})).fetchall()
    return frozenset(str(r.id) for r in rows)


async def _leads(db: Any, user: Any, project_id: Any, led: frozenset[str] | None) -> bool:
    if not project_id:
        return False
    if led is None:
        led = await led_projects(db, user)
    return str(project_id) in led


async def may_delete_task(
    db: Any, user: Any, task: Any, *, led: frozenset[str] | None = None,
) -> bool:
    """May *user* delete *task*, a row of ``pm_tasks``? (D-OP-12.)

    The two cheap arms go first, so a creator or an admin costs no query.
    *led* is :func:`led_projects`, when the caller already read it for a page.
    """
    if can_manage_settings(user) or _made_by(task, _me(user)):
        return True
    return await _leads(db, user, _field(task, "project_id"), led)


async def may_delete_project(
    db: Any, user: Any, project: Any, *, led: frozenset[str] | None = None,
) -> bool:
    """May *user* delete *project*, a row of ``pm_projects``? (D-OP-12.)"""
    if can_manage_settings(user) or _made_by(project, _me(user)):
        return True
    return await _leads(db, user, _field(project, "id"), led)


async def may_write_grant(
    db: Any, user: Any, project: Any, *, led: frozenset[str] | None = None,
) -> bool:
    """May *user* add or remove a grant on *project*? (D-OP-11.)

    The creator of a project does NOT get this by creating it. Only a lead
    and a Projects admin do.
    """
    if can_manage_settings(user):
        return True
    return await _leads(db, user, _field(project, "id"), led)


async def in_personal_tree(db: Any, project: Any) -> bool:
    """Is *project* a node of a member's personal tree? (WS-64 OP-6b.)

    The same line that ``core.assert_move_keeps_privacy`` holds for a move:
    a personal tree is private, whoever acts. A row that carries
    ``personal_owner`` answers without a query.
    """
    if str(_field(project, "personal_owner") or "").strip():
        return True
    pid = _field(project, "id")
    if not pid:
        return False
    found = await db.execute(text(_PERSONAL_TREE_SQL), {"pid": str(pid)})
    return found.first() is not None


async def grant_write_refusal(db: Any, user: Any, project: Any) -> str:
    """The 403 words of a grant write on *project*, or ``""`` when it may go.

    THE one answer for both grant routes and for the ``can_write_grants``
    flag. A personal tree is refused first and for everyone, the owner and a
    Projects admin too. Then :func:`may_write_grant` decides.
    """
    if await in_personal_tree(db, project):
        return PERSONAL_GRANT_REFUSED
    if not await may_write_grant(db, user, project):
        return GRANT_WRITE_REFUSED
    return ""


async def personal_tree_owner(db: Any, project: Any) -> str:
    """The lower-case owner of the personal tree that holds *project*, or ``""``."""
    own = str(_field(project, "personal_owner") or "").strip()
    if own:
        return own.lower()
    pid = _field(project, "id")
    if not pid:
        return ""
    found = (await db.execute(text(_PERSONAL_OWNER_SQL), {"pid": str(pid)})).first()
    return str(found[0]).strip().lower() if found else ""


async def assert_may_revoke_grant(db: Any, user: Any, project: Any, subject: str) -> None:
    """403 or return, for a revoke of the grant to *subject* on *project*.

    Outside a personal tree this is :func:`assert_may_write_grant`. Inside one,
    a revoke can only make the tree MORE private, so it is not refused like a
    grant (OP-6b review round 2): the owner's own grant never goes, and any
    other grant (a stray one written before OP-6b) may be removed by the
    owner, a lead or a Projects admin.
    """
    owner = await personal_tree_owner(db, project)
    if not owner:
        await assert_may_write_grant(db, user, project)
        return
    if str(subject or "").strip().lower() == owner:
        raise HTTPException(status_code=403, detail=PERSONAL_GRANT_REFUSED)
    if _me(user) == owner or await may_write_grant(db, user, project):
        return
    raise HTTPException(status_code=403, detail=GRANT_WRITE_REFUSED)


#: D-OP-14. The rule of a task move IS the rule of a task delete, so the
#: two are one function and the ``can_delete`` flag answers both.
may_move_task = may_delete_task


async def assert_may_move_task(db: Any, user: Any, task: Any) -> None:
    """403 with :data:`TASK_MOVE_REFUSED`, or return. (D-OP-14.)"""
    if not await may_move_task(db, user, task):
        raise HTTPException(status_code=403, detail=TASK_MOVE_REFUSED)


async def assert_may_move_project(db: Any, user: Any, project: Any) -> None:
    """403 with :data:`PROJECT_MOVE_REFUSED`, or return. (D-OP-14.)

    The caller calls this only for a re-parent. A reorder under the same
    parent changes nobody's view.
    """
    if not await may_write_grant(db, user, project):
        raise HTTPException(status_code=403, detail=PROJECT_MOVE_REFUSED)


def _same_person(a: Any, b: Any) -> bool:
    return str(a or "").strip().lower() == str(b or "").strip().lower()


async def assert_may_change_lead(
    db: Any, user: Any, project: Any, values: dict[str, Any],
) -> None:
    """A PATCH that changes ``lead`` needs :func:`may_write_grant`.

    A body that sends the lead the row already has changes nothing, so it
    passes. A ``null`` clears the lead, and that is a change.
    """
    if "lead" not in values or _same_person(values["lead"], _field(project, "lead")):
        return
    if not await may_write_grant(db, user, project):
        raise HTTPException(status_code=403, detail=LEAD_WRITE_REFUSED)


async def assert_may_name_lead(
    db: Any, user: Any, parent: Any | None, lead: Any,
) -> None:
    """A new project that names *lead*. *parent* is ``None`` for a new root.

    Under a parent, the caller needs :func:`may_write_grant` on the parent.
    A new root passes when the lead is the creator, because a member may
    lead the space they create. Any other lead of a new root needs a
    Projects admin, because no project above it has a lead to ask.
    """
    if not str(lead or "").strip():
        return
    if parent is None:
        if _same_person(lead, _me(user)) or can_manage_settings(user):
            return
        raise HTTPException(status_code=403, detail=LEAD_ROOT_REFUSED)
    if not await may_write_grant(db, user, parent):
        raise HTTPException(status_code=403, detail=LEAD_CREATE_REFUSED)


async def assert_may_delete_task(db: Any, user: Any, task: Any) -> None:
    """403 with :data:`TASK_DELETE_REFUSED`, or return."""
    if not await may_delete_task(db, user, task):
        raise HTTPException(status_code=403, detail=TASK_DELETE_REFUSED)


async def assert_may_delete_project(db: Any, user: Any, project: Any) -> None:
    """403 with :data:`PROJECT_DELETE_REFUSED`, or return."""
    if not await may_delete_project(db, user, project):
        raise HTTPException(status_code=403, detail=PROJECT_DELETE_REFUSED)


async def assert_may_write_grant(db: Any, user: Any, project: Any) -> None:
    """403 with :data:`PERSONAL_GRANT_REFUSED` on a personal tree, or with
    :data:`GRANT_WRITE_REFUSED`, or return. (:func:`grant_write_refusal`.)"""
    refused = await grant_write_refusal(db, user, project)
    if refused:
        raise HTTPException(status_code=403, detail=refused)


async def attach_can_write_grants(db: Any, user: Any, row: dict[str, Any]) -> dict[str, Any]:
    """Set ``can_write_grants`` on one project row, through :func:`may_write_grant`.

    WS-64 OP-6b. The chat reads it from ``GET /projects/nodes/{id}`` and
    refuses before its grant card where it is false. A row with the flag
    false also carries ``grant_refusal``, the words of the 403, which the chat
    quotes. The chat holds no copy of the rule, and the route still decides.
    """
    refused = await grant_write_refusal(db, user, row)
    row["can_write_grants"] = not refused
    if refused:
        row["grant_refusal"] = refused
    return row


async def attach_can_delete(
    db: Any, user: Any, rows: list[dict[str, Any]], *, kind: str,
) -> list[dict[str, Any]]:
    """Set ``can_delete`` on each row, through the rule above. One query.

    *kind* is ``"task"`` or ``"project"``. The app hides Delete where the
    flag is false, and the chat refuses before its card. Neither has a copy
    of the rule. A row with ``can_delete`` false also carries
    ``delete_refusal``, the words of the 403, which the chat quotes.
    """
    if kind not in ("task", "project"):
        raise ValueError(f"unknown kind {kind!r}")
    if not rows:
        return rows
    check = may_delete_task if kind == "task" else may_delete_project
    me = _me(user)
    # The lead set is read once for the page, and only when a row needs it.
    led: frozenset[str] | None = None
    if not can_manage_settings(user) and any(not _made_by(r, me) for r in rows):
        led = await led_projects(db, user)
    words = TASK_DELETE_REFUSED if kind == "task" else PROJECT_DELETE_REFUSED
    for row in rows:
        row["can_delete"] = await check(db, user, row, led=led or frozenset())
        if not row["can_delete"]:
            # The 403's own words, so the chat quotes the server and holds
            # no copy of who may act.
            row["delete_refusal"] = words
    return rows
