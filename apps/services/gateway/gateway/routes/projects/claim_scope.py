"""Projects · own apps in ``my_work`` and in an agent's claim (WS-MCP SW-45).

Spec: ``project-docs/specs/agent_coordination.md`` §3.10.1 and SW-45.

    GET /projects/my/own-app-free

**Own apps.** The projects where the member is a lead, of the project or of
an ancestor, by the rule of ``authority.py``. A role does not count. This
module asks ``authority.own_apps`` and ``authority.app_owner``, and it names
no lead column of its own (``tests/unit/test_app_ownership_seam.py``).

**The flag.** ``AGENT_CLAIM_SCOPE_MODES`` maps an organization to ``off``,
``warn`` or ``refuse``. The one reader is
``acb_auth.agent_oauth.claim_scope_mode``. It reads the settings at each call,
so a SIGHUP reload of the gateway applies a change. Off, the default, the
route answers no rows and the claim does not change.

**The route.** Up to :data:`OWN_APP_FREE_MAX` tasks of the own apps that the
member can see, that have no assignee, that no open run holds and that no
open task blocks. The status category is ``backlog``, ``todo`` or
``in_progress``, the categories of ``my_work``. The best priority comes
first (``core.PRIORITY_RANK_SQL``), then the oldest. Each row is a task row
with ``app_project``: the id and the name of the nearest own app above it.
The MCP ``my_work`` lists them after the assigned rows, with the reason
``own_app_free``.

**The claim.** Only an agent claim (``UserContext.agent``) of a task with no
assignee is checked. A task that is assigned to anyone keeps the old claim,
and a task assigned to the member always passes. Outside the own apps:

* ``warn``: the claim works. The answer has ``warning`` and
  ``outside_apps`` (``{"lead": <email or null>}``), and the timeline of the
  task gets ONE ``system`` row (:func:`record_outside_claim`).
* ``refuse``: 403, detail ``{"error": "outside_apps", "message": ...,
  "lead": ...}``, and nothing is written.

The tenant and the member come from the session, never from request input
(R5). Fence: ``tests/unit/test_agent_claim_scope_r8.py`` (R8).
"""

from __future__ import annotations

from typing import Any

from acb_auth import UserContext, get_current_user
from acb_auth.agent_oauth import claim_scope_mode
from fastapi import Depends, HTTPException
from gateway.routes.projects.authority import app_owner, own_apps
from gateway.routes.projects.core import (
    PRIORITY_RANK_SQL,
    TaskModel,
    _tenant_session,
    actor,
    record_activity,
    resolve_visibility,
    router,
    row_to_dict,
    task_visibility_clause,
)
from gateway.routes.projects.filters import attach_relation_counts, unheld_clause
from sqlalchemy import text

#: The most ``own_app_free`` rows of one ``my_work`` (§3.10.1).
OWN_APP_FREE_MAX = 5
#: The candidates that the route reads before it drops the blocked ones.
_CANDIDATES = 25
#: The status categories of a free task: the categories of ``my_work``.
FREE_CATEGORIES: tuple[str, ...] = ("backlog", "todo", "in_progress")

#: The code of the 403 in mode ``refuse``. The MCP edge maps it to a fixed
#: text of its own (``agent_edge.tools.outside_apps_refusal``), and its copy
#: ``agent_edge.tools.CLAIM_OUTSIDE_APPS`` has a pin test.
OUTSIDE_APPS = "outside_apps"
#: ``meta.kind`` of the timeline row of a claim outside the own apps.
OUTSIDE_APPS_KIND = "claim_outside_apps"


def _free_sql(vis: Any) -> str:
    """The candidate tasks of the own apps. ``:own`` is the list of the
    project ids, and the app of each row is read in Python."""
    categories = ", ".join(f"'{c}'" for c in FREE_CATEGORIES)
    return (
        "SELECT t.* FROM pm_tasks t"
        " JOIN pm_task_statuses s ON s.id = t.status_id"
        " WHERE t.project_id = ANY(CAST(:own AS uuid[]))"
        "   AND t.archived_at IS NULL"
        f"  AND s.category IN ({categories})"
        "   AND NOT EXISTS (SELECT 1 FROM pm_task_assignees a WHERE a.task_id = t.id)"
        f"  AND {unheld_clause()}"
        f"  AND {task_visibility_clause(vis)}"
        f" ORDER BY {PRIORITY_RANK_SQL} DESC, t.created_at ASC, t.id ASC"
        " LIMIT :n"
    )


def scope_mode(user: Any) -> str:
    """The claim scope of the caller's organization, from the session."""
    return claim_scope_mode(getattr(user, "organization_id", None))


async def free_rows(db: Any, user: Any, vis: Any, *,
                    limit: int = OWN_APP_FREE_MAX) -> list[dict[str, Any]]:
    """The free tasks of the member's own apps, best priority first."""
    apps = await own_apps(db, user)
    if not apps:
        return []
    found = (await db.execute(
        text(_free_sql(vis)),
        {"own": list(apps), "n": _CANDIDATES, **vis.params},
    )).fetchall()
    rows = [row_to_dict(r, TaskModel) for r in found]
    await attach_relation_counts(db, rows, vis)
    rows = [r for r in rows if not r.get("blocked_by_count")][:limit]
    names = await _app_names(db, {apps[str(r["project_id"])] for r in rows})
    for row in rows:
        app_id = apps[str(row["project_id"])]
        row["app_project"] = {"id": app_id, "name": names.get(app_id)}
    return rows


async def _app_names(db: Any, ids: set[str]) -> dict[str, str]:
    if not ids:
        return {}
    found = (await db.execute(
        text("SELECT id, name FROM pm_projects WHERE id = ANY(CAST(:ids AS uuid[]))"),
        {"ids": sorted(ids)},
    )).fetchall()
    return {str(r.id): str(r.name or "") for r in found}


@router.get("/my/own-app-free")
async def own_app_free(user: UserContext = Depends(get_current_user)) -> dict:
    """The free tasks of the caller's own apps (SW-45). See the module."""
    if scope_mode(user) == "off":
        return {"rows": []}
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        rows = await free_rows(db, user, vis)
    return {"rows": rows}


def warning_text(lead: str | None) -> str:
    """The ``warning`` of a claim outside the own apps. The lead is an email
    from ``pm_projects``, and the rest of the text is fixed."""
    if lead:
        return (f"This task is outside your apps. Its app lead is {lead}."
                " The task timeline records this claim.")
    return ("This task is outside your apps, and its app has no lead."
            " The task timeline records this claim.")


#: The message of the 403 in mode ``refuse``. The edge shows its own text.
REFUSED_TEXT = (
    "This task is outside your apps. Ask its app lead to assign it to you,"
    " or take a task from my_work.")


async def check_claim_scope(db: Any, user: Any, project_id: Any,
                            current: set[str]) -> dict[str, Any] | None:
    """The scope check of a claim, before any write. (SW-45.)

    ``None`` lets the claim go with no note. A dict is a claim in mode
    ``warn``: the claim goes, and the caller adds :func:`apply_warning`. In
    mode ``refuse`` it raises the 403. *current* is the set of assignees
    that the claim read before its insert.
    """
    if getattr(user, "agent", None) is None or current:
        return None
    mode = scope_mode(user)
    if mode == "off":
        return None
    lead, own = await app_owner(db, user, project_id)
    if own:
        return None
    if mode == "refuse":
        raise HTTPException(status_code=403, detail={
            "error": OUTSIDE_APPS, "message": REFUSED_TEXT, "lead": lead})
    return {"lead": lead}


async def record_outside_claim(db: Any, user: Any, task_id: str,
                               note: dict[str, Any]) -> None:
    """Write the ONE timeline row of a claim outside the own apps."""
    await record_activity(
        db, activity_type="system", created_by=actor(user), task_id=task_id,
        body=warning_text(note.get("lead")),
        meta={"kind": OUTSIDE_APPS_KIND, "lead": note.get("lead"), "mode": "warn"},
    )


def apply_warning(result: dict[str, Any], note: dict[str, Any]) -> None:
    """Add the warning of *note* to the claim *result*. A warning that the
    claim already holds (SW-63) stays first."""
    text_ = warning_text(note.get("lead"))
    prior = result.get("warning")
    result["warning"] = f"{prior} {text_}" if isinstance(prior, str) and prior else text_
    result["outside_apps"] = {"lead": note.get("lead")}
