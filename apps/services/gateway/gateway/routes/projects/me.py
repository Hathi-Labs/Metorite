"""Projects · me — the caller's own work.

Spec: ``project-docs/specs/project_management_app.md`` §4 (``me.py`` row).

    GET /projects/assigned-to-me

This is the read WS-27e's personal mirror consumes (§6.1): the org store's
answer to "what is mine", which the Tasks app turned into rows of its own store so
the whole My Tasks overlay — clarify, timeboxing, Waiting-For — works on org tasks
with no changes to its code.

It is deliberately **not** ``/projects/tasks?assignee=<me>``. That route exists
and answers the same question, but this one cannot be pointed at anybody else:
the identity comes from the session and there is no parameter to change it, so
the mirror can never be made to sync one member's work into another's inbox.
"""

from __future__ import annotations

from acb_auth import UserContext, get_current_user
from fastapi import Depends
from gateway.routes.projects.core import (
    PRIORITY_RANK_SQL,
    ListResponse,
    Page,
    TaskModel,
    _tenant_session,
    actor,
    mine_clause,
    resolve_organization_id,
    router,
    row_to_dict,
)
from sqlalchemy import text


@router.get("/assigned-to-me")
async def assigned_to_me(
    user: UserContext = Depends(get_current_user),
    include_done: bool = False,
    page: Page = Depends(),
) -> ListResponse:
    """Tasks assigned to the caller, across every project they can reach.

    **No GRANT clause, on purpose.** Assignment is itself the strongest claim
    to a task — ``load_visible_task`` already treats it that way — so filtering
    this by project grants would hide work from the very person asked to do it
    whenever it was delegated across a Center boundary.

    ⚠️ **But there is a tenant clause, and it is not optional** (WS-29b). This
    route reaches tasks by MATCHING A STRING: ``pm_task_assignees.assignee`` is
    a bare email (D-PM-4) that nothing validates, so anyone in another
    organization can put this caller's address on their task and — without the
    line below — its title, description and dates appear here. Worse than a
    read: WS-27e's personal mirror SYNCED this endpoint into the retired task store, so
    the leak would be copied into a second app and outlive the request.

    Found by driving this endpoint against a real two-tenant database. The
    grant-based reads were all scoped by then; this one has no grant clause to
    have noticed was missing.

    Done tasks are excluded by default. The completion boundary is read from the
    status ``category`` rather than from ``completed_at``, so a project that
    renamed its terminal lane still behaves correctly: the category is the
    machine-readable half and the name is the owner's.
    """
    email = actor(user).lower()
    clauses = [
        "t.organization_id = CAST(:vis_org AS uuid)",
        # WS-64 OP-11: the assignee arm of the ONE "mine" (`core.mine_clause`).
        # This route's contract is "assigned to me" only: `my_work`
        # view="assigned" reads it, and its rows sync into no other list.
        # The arm, not a copy, so the two cannot drift.
        mine_clause("t", arm="assigned"),
        "t.archived_at IS NULL",
        # WS-64: a task of an archived project is history, not my work.
        "NOT EXISTS (SELECT 1 FROM pm_projects pr "
        "            WHERE pr.id = t.project_id AND pr.archived_at IS NOT NULL)",
    ]
    if not include_done:
        clauses.append(
            "NOT EXISTS (SELECT 1 FROM pm_task_statuses s "
            "            WHERE s.id = t.status_id "
            "              AND s.category IN ('done', 'cancelled'))"
        )
    where = " WHERE " + " AND ".join(clauses)

    async with _tenant_session() as db:
        # A caller the directory does not know binds NULL and matches nothing,
        # which is the same fail-closed shape every other read here has.
        scope = {"who": email, "vis_org": await resolve_organization_id(db, email)}
        total = (await db.execute(
            text(f"SELECT count(*) FROM pm_tasks t{where}"), scope,
        )).scalar() or 0
        rows = (await db.execute(
            text(
                f"SELECT t.* FROM pm_tasks t{where} "
                # D78: the tie on a due date breaks by the priority matrix,
                # most pressing cell first.
                f"ORDER BY t.due_at NULLS LAST, {PRIORITY_RANK_SQL} DESC, "
                f"t.created_at DESC LIMIT :limit OFFSET :offset"
            ),
            {**scope, "limit": page.limit, "offset": page.offset},
        )).fetchall()
        return ListResponse(
            rows=[row_to_dict(r, TaskModel) for r in rows], total=int(total),
        )
