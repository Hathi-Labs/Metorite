"""Projects · the teams of a member (WS-39 T-1, Metorite task 2532).

Spec: ``project-docs/specs/project_management_app.md`` §14.9.

    GET /projects/teams              each team of the caller, with its spaces
    GET /projects/teams?team=<slug>  one team of the caller
    GET /projects/teams?team=all     each team, as with no value

One read answers two questions: which groups is the caller in, and which
spaces and projects does each group reach through ``pm_project_grants``. A
grant of a root covers its subtree. ``team_scope.py`` holds the rules of the
value, the reach and the flag.

**The answer.** ``rows`` holds one row for each team of the caller, in the
order of the slugs, and a team with no space is a row too. A row has
``slug``, ``subject``, ``name``, ``tasks`` and ``spaces``.

* ``spaces`` are the tops of the team view: each node of the team whose parent
  is not in the team view. A grant of a space makes the space a top. A grant of
  a project below a space makes that project a top, and ``level`` then says
  ``project``. A top carries the fields of ``GET /projects/tree``, its
  ``level``, ``tasks``, and ``projects``: the other nodes of the team under it.
  A top whose parent the caller cannot read has ``parent_project_id`` null,
  so the hidden space is not named by its id either.
* ``tasks`` counts what ``GET /projects/tasks?team=<slug>`` lists. The count
  comes from ``tasks._task_scope``, the one scope that the list and
  ``/projects/tasks/stats`` read, so the three agree by construction.

**Rules 2 and 4.** Each node comes from ``tree._visible_projects`` and each
count from ``tasks._task_scope``, and both AND the team beside the caller's
visibility. So a space that the caller cannot read is not named and not
counted, and a space that no team of the caller holds is in no row.

The route is dark behind ``TEAMS_ORGS`` (:func:`team_scope.require_teams`).
It writes nothing. The tenant and the caller come from the session (R5).

Fence: ``tests/unit/test_teams_read_model_r8.py`` (R8).
"""

from __future__ import annotations

from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends
from gateway.routes.projects.core import (
    MAX_DEPTH,
    ProjectModel,
    Visibility,
    _tenant_session,
    resolve_visibility,
    router,
    row_to_dict,
    wire,
)
from gateway.routes.projects.tasks import _task_scope
from gateway.routes.projects.team_scope import (
    TeamScope,
    member_teams,
    require_teams,
    resolve_team_scope,
)
from gateway.routes.projects.tree import _visible_projects, attach_levels
from sqlalchemy import text


async def _team_task_counts(
    db: Any, user: UserContext, vis: Visibility, scope: TeamScope,
) -> dict[str, int]:
    """Tasks for each project of the team: the set of the default list read.

    The scope is ``_task_scope`` with the arguments of a bare
    ``GET /projects/tasks?team=<slug>``. So the sum is that read's ``total``.
    """
    walk_cte, walk_join, where, params = await _task_scope(
        db, user, vis,
        project_id=None, include_subtree=False,
        parent_task_id=None, include_descendants=False,
        mine=False, include_triage=False, unheld=False,
        agent_clauses=[], agent_params={},
        filters={},
        team=scope,
    )
    rows = (await db.execute(
        text(
            f"{walk_cte}SELECT t.project_id, count(*) AS n"
            f" FROM pm_tasks t{walk_join}{where}"
            f" GROUP BY t.project_id"
        ),
        params,
    )).fetchall()
    return {str(r.project_id): int(r.n) for r in rows}


async def _readable(db: Any, vis: Visibility, ids: set[str]) -> set[str]:
    """The ids of *ids* that the caller may read, in one statement."""
    if not ids:
        return set()
    rows = (await db.execute(
        text(
            "SELECT id FROM pm_projects"
            f" WHERE id = ANY(CAST(:parent_ids AS uuid[])) AND {vis.project_clause()}"
        ),
        {"parent_ids": sorted(ids), **vis.params},
    )).fetchall()
    return {str(r.id) for r in rows}


def _top_of(node_id: str, by_id: dict[str, Any]) -> str:
    """The top of *node_id* in the team view: climb while the parent is in it.

    :data:`core.MAX_DEPTH` bounds the climb, as it bounds each walk of a tree.
    """
    current = node_id
    for _ in range(MAX_DEPTH):
        parent = str(getattr(by_id[current], "parent_project_id", None) or "")
        if parent not in by_id:
            break
        current = parent
    return current


async def _team_row(
    db: Any, user: UserContext, vis: Visibility, scope: TeamScope,
) -> dict[str, Any]:
    """One team: its spaces, the projects under each, and the task counts."""
    nodes = await _visible_projects(db, user, vis, team=scope)
    counts = await _team_task_counts(db, user, vis, scope)
    by_id = {str(r.id): r for r in nodes}
    spaces: dict[str, dict[str, Any]] = {}
    # Two passes, so the tops keep the order of the node read (position,
    # name, id) and each project lands under its top in the same order.
    tops = {node_id: _top_of(node_id, by_id) for node_id in by_id}
    # A top below a space that the team does not hold names a node outside
    # the view: its parent, and the root of an archive that took it. When the
    # caller cannot read that node, its id is not given either (rule 4), as
    # `core.visible_project_paths` stops at a parent that is not there.
    above = {
        str(value) for node_id, row in by_id.items() if tops[node_id] == node_id
        for value in (row.parent_project_id, getattr(row, "archived_root_id", None))
        if value and str(value) not in by_id
    }
    readable = await _readable(db, vis, above)
    for node_id, row in by_id.items():
        if tops[node_id] == node_id:
            space = {
                **row_to_dict(row, ProjectModel),
                "tasks": counts.get(node_id, 0),
                "projects": [],
            }
            for key in ("parent_project_id", "archived_root_id"):
                value = space.get(key)
                if value and value not in by_id and value not in readable:
                    space[key] = None
            spaces[node_id] = space
    for node_id, row in by_id.items():
        top = tops[node_id]
        if top == node_id:
            continue
        space = spaces[top]
        space["tasks"] += counts.get(node_id, 0)
        space["projects"].append({
            "id": node_id,
            "name": row.name,
            "parent_project_id": (
                str(row.parent_project_id) if row.parent_project_id else None
            ),
            "kind": getattr(row, "kind", None),
            "archived_at": wire(getattr(row, "archived_at", None)),
        })
    return {
        "slug": scope.slug,
        "subject": scope.subject,
        "name": scope.name,
        "tasks": sum(counts.values()),
        "spaces": list(spaces.values()),
    }


@router.get("/teams", dependencies=[Depends(require_teams)])
async def list_teams(
    user: UserContext = Depends(get_current_user),
    # A team slug or `all` (`team_scope.py`). A slug gives one row.
    team: str | None = None,
) -> dict:
    """The teams of the caller, each with the spaces that it reaches.

    See the module docstring for the shape. A slug that names no team of the
    caller is 404, with the body of a slug that names no group.
    """
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        chosen = await resolve_team_scope(db, user, vis, team)
        scopes = [chosen] if chosen is not None else await member_teams(db, vis)
        rows = [await _team_row(db, user, vis, scope) for scope in scopes]
        # The level of each top, in one read for every team.
        await attach_levels(db, [space for row in rows for space in row["spaces"]])
    return {"rows": rows, "total": len(rows)}
