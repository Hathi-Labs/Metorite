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
The agent state of each task comes from ``filters.agent_state_sql``, the SAME
text that fills the pill on a card. So the strip and the card cannot disagree.
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
    CLOSING_CATEGORIES,
    _tenant_session,
    actor,
    load_visible_project,
    resolve_visibility,
    router,
    task_visibility_clause,
    triage_exclusion_clause,
)
from gateway.routes.projects.filters import agent_runs_enabled, agent_state_sql
from sqlalchemy import text

#: The states that read as "working" on the strip: the agent has the task, and
#: the run is live, answered or idle (EA3-43).
WORKING_STATES: tuple[str, ...] = ("working", "answered", "stale")

_CLOSED_LIST = ", ".join(f"'{c}'" for c in sorted(CLOSING_CATEGORIES))

#: ``{scope}`` is the CTE of the visible tasks. The three state counts read
#: ONE state for each task, from ``agent_state_sql``. ``waiting_on_you`` is the
#: "Waiting on me" rule of §12.1: an `awaiting` run of the caller, or of a task
#: that the caller is assigned to.
_SUMMARY_SQL = f"""
WITH RECURSIVE sub AS (
    SELECT id FROM pm_projects WHERE id = CAST(:pid AS uuid)
    UNION ALL
    SELECT p.id FROM pm_projects p JOIN sub s ON p.parent_project_id = s.id
), scope AS (
    SELECT t.id, t.status_id
      FROM pm_tasks t
     WHERE {{visible}}
       AND t.project_id IN (SELECT id FROM sub)
       AND t.archived_at IS NULL
       AND EXISTS (SELECT 1 FROM pm_projects pp
                    WHERE pp.id = t.project_id AND pp.archived_at IS NULL)
       AND {{triage}}
), states AS (
    {{states}}
)
SELECT
  (SELECT count(*) FROM states WHERE state = 'queued') AS queued,
  (SELECT count(*) FROM states WHERE state = ANY(CAST(:working AS text[]))) AS working,
  (SELECT count(*) FROM states s
    WHERE s.state = 'awaiting'
      AND (lower(s.member_email) = :me
           OR EXISTS (SELECT 1 FROM pm_task_assignees a
                       WHERE a.task_id = s.task_id AND lower(a.assignee) = :me))
  ) AS waiting_on_you,
  (SELECT count(DISTINCT sc.id) FROM scope sc
     JOIN pm_task_refs r ON r.task_id = sc.id
     JOIN pm_task_statuses st ON st.id = sc.status_id
    WHERE r.kind = 'pull_request' AND r.provider = 'github'
      AND r.state IN ('open', 'draft')
      AND st.category NOT IN ({_CLOSED_LIST})
  ) AS in_review,
  (SELECT count(*) FROM scope sc
     JOIN pm_task_refs r ON r.task_id = sc.id
    WHERE r.kind = 'pull_request' AND r.provider = 'github'
      AND r.state = 'merged'
      AND r.merged_at >= now() - interval '7 days'
  ) AS merged_this_week
"""

#: With the flag off the states CTE is empty, and no pill query runs.
_NO_STATES = (
    "SELECT NULL::uuid AS task_id, NULL::text AS state, NULL::text AS member_email "
    "WHERE false"
)


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
        states = (
            agent_state_sql("r.task_id IN (SELECT id FROM scope)")
            if agent_runs_enabled() else _NO_STATES
        )
        sql = _SUMMARY_SQL.format(
            visible=task_visibility_clause(vis),
            triage=triage_exclusion_clause(),
            states=states,
        )
        row = (await db.execute(text(sql), {
            **vis.params,
            "pid": project_id,
            "me": actor(user).strip().lower(),
            "working": list(WORKING_STATES),
        })).fetchone()
    return _counts(row)


def _counts(row: Any) -> dict[str, int]:
    keys = ("queued", "working", "waiting_on_you", "in_review", "merged_this_week")
    return {key: int(getattr(row, key, 0) or 0) for key in keys}
