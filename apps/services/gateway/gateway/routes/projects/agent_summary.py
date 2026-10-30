"""Projects · the agent summary strip of a project (WS-55 EA-13e, EA3-43).

Spec: ``project-docs/specs/external_agent_access.md`` §12.2 item 4.

    GET /projects/{project_id}/agent-summary

Five counts over the tasks of the project and its subprojects that the CALLER
may see: ``queued``, ``working``, ``waiting_on_you``, ``in_review`` and
``merged_this_week``. The Projects board draws them above the board, and each
count is a button that sets the matching filter.

⚠️ **One scope, three reads.** The visible tasks come from the same two clauses
as ``GET /projects/tasks`` (``task_visibility_clause`` and the default archive
and triage exclusions), so a count never names a task that the board hides.
Each count is the number of tasks that ``filters.agent_focus_clause`` keeps.
``GET /projects/tasks?agent_focus=`` filters by the SAME text, which reads
``filters.agent_state_sql`` (the pill on a card). So the strip, the card and
the list behind a click cannot disagree.
The route reads the tenant from the session only (R5, R11), and the session
binds RLS.

⚠️ **The runs flag.** While ``agent_runs_enabled`` is off, the cards show no
pill, so the three state counts read 0 here too. The two pull request counts
do not depend on the flag.
"""

from __future__ import annotations

from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends
from gateway.routes.projects.core import (
    _tenant_session,
    actor,
    load_visible_project,
    resolve_visibility,
    router,
    task_visibility_clause,
    triage_exclusion_clause,
)
from gateway.routes.projects.filters import (
    AGENT_FOCI,
    AGENT_FOCUS_ME,
    agent_focus_clause,
)
from sqlalchemy import text

#: ``scope`` is the CTE of the visible tasks. Each count is the number of
#: those tasks that ``filters.agent_focus_clause`` keeps, so a count is the
#: ``total`` of the list under the same focus (WS-55 EA-13e, task #986).
_SUMMARY_SQL = """
WITH RECURSIVE sub AS (
    SELECT id FROM pm_projects WHERE id = CAST(:pid AS uuid)
    UNION ALL
    SELECT p.id FROM pm_projects p JOIN sub s ON p.parent_project_id = s.id
), scope AS (
    SELECT t.id
      FROM pm_tasks t
     WHERE {visible}
       AND t.project_id IN (SELECT id FROM sub)
       AND t.archived_at IS NULL
       AND EXISTS (SELECT 1 FROM pm_projects pp
                    WHERE pp.id = t.project_id AND pp.archived_at IS NULL)
       AND {triage}
)
SELECT
  (SELECT count(*) FROM pm_tasks t JOIN scope sc ON sc.id = t.id WHERE {queued}) AS queued,
  (SELECT count(*) FROM pm_tasks t JOIN scope sc ON sc.id = t.id WHERE {working}) AS working,
  (SELECT count(*) FROM pm_tasks t JOIN scope sc ON sc.id = t.id WHERE {waiting}) AS waiting_on_you,
  (SELECT count(*) FROM pm_tasks t JOIN scope sc ON sc.id = t.id WHERE {review}) AS in_review,
  (SELECT count(*) FROM pm_tasks t JOIN scope sc ON sc.id = t.id WHERE {merged}) AS merged_this_week
"""


@router.get("/{project_id}/agent-summary")
async def agent_summary(
    project_id: str,
    user: UserContext = Depends(get_current_user),
) -> dict[str, int]:
    """The five counts of one project, over the tasks the caller can see.

    A project the caller cannot see answers 404, as the list does. The caller
    is the session's member, never a name from the request.
    """
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        await load_visible_project(db, vis, project_id)
        sql = _SUMMARY_SQL.format(
            visible=task_visibility_clause(vis),
            triage=triage_exclusion_clause(),
            **{focus: agent_focus_clause(focus) for focus in AGENT_FOCI},
        )
        row = (await db.execute(text(sql), {
            **vis.params,
            "pid": project_id,
            AGENT_FOCUS_ME: actor(user).strip().lower(),
        })).fetchone()
    return _counts(row)


def _counts(row: Any) -> dict[str, int]:
    keys = ("queued", "working", "waiting_on_you", "in_review", "merged_this_week")
    return {key: int(getattr(row, key, 0) or 0) for key in keys}
