"""Projects · was this task worked by an agent? (WS-54 GH-6, §12.2)

Spec: ``project-docs/specs/github_integration.md`` §12.2. A task is
**agent-worked** at a time ``T`` when either row exists, created before ``T``:

1. **A run**: a ``pm_agent_runs`` row of the task, in any status.
2. **An act through MCP**: any ``pm_activities`` row of the task whose
   ``meta->>'via'`` starts with ``mcp:``. A claim writes an ``assignment`` row
   and ``record_activity`` stamps the ``via``. So an agent's comment, status
   move or link counts too, even when the claim wrote no row.

⚠️ **This module is the ONE copy of the predicate.** The merge-to-Done rule
(``github_merge.py``) reads it, and EA-13e's summary strip and filter import
it. :data:`AGENT_WORKED_SQL` is a SQL boolean with two named parameters
(``:aw_task`` and ``:aw_before``), and :func:`reason` names which row made it
true, so a person can audit each move. Both come from ONE WHERE clause for
each half (``_RUN_WHERE`` and ``_MCP_WHERE``). Fence:
``tests/unit/test_github_merge_r8.py`` (``test_f3_*`` holds that the boolean
and the reason agree on every case).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text

#: The prefix of an act through MCP in ``meta.via`` (``acb_auth.agent_oauth``).
MCP_VIA_PREFIX = "mcp:"

#: ⚠️ **The ONE text of the rule** (fix round 1, F3). Each half of §12.2 is
#: written ONCE, as a WHERE clause. :data:`AGENT_WORKED_SQL` (the boolean that
#: EA-13e's filter imports) and ``_REASON_SQL`` (the row that
#: :func:`reason` names) are both built from these two strings, so the two
#: cannot drift. ``LIKE 'mcp:%'`` holds no ``_``, so no character of the prefix
#: is a wildcard. ``created_at < :aw_before`` keeps an act after the merge out.
_RUN_WHERE = (
    "aw_r.task_id = CAST(:aw_task AS uuid) "
    "AND aw_r.created_at < CAST(:aw_before AS timestamptz)"
)
_MCP_WHERE = (
    "aw_a.task_id = CAST(:aw_task AS uuid) "
    "AND aw_a.meta->>'via' LIKE 'mcp:%' "
    "AND aw_a.created_at < CAST(:aw_before AS timestamptz)"
)

_RUN_EXISTS = f"EXISTS (SELECT 1 FROM pm_agent_runs aw_r WHERE {_RUN_WHERE})"
_MCP_EXISTS = f"EXISTS (SELECT 1 FROM pm_activities aw_a WHERE {_MCP_WHERE})"

#: The predicate, as one SQL boolean. Bind ``aw_task`` and ``aw_before``.
AGENT_WORKED_SQL = f"({_RUN_EXISTS} OR {_MCP_EXISTS})"

#: How the predicate names its task. ``_BATCH_SQL`` swaps this one text for
#: the id column of a list, so the batched read below is the SAME boolean and
#: no second copy of it (EA-13e, EA3-49).
_TASK_PARAM = "CAST(:aw_task AS uuid)"

#: The predicate over a LIST of task ids, one row for each id. Bind ``ids``
#: (a ``uuid[]``) and ``aw_before``. ⚠️ Built from :data:`AGENT_WORKED_SQL`
#: by replacing the task parameter. A change to either half of the rule moves
#: this query with it.
_BATCH_SQL = (
    "SELECT t.id AS task_id, "
    + AGENT_WORKED_SQL.replace(_TASK_PARAM, "t.id")
    + " AS worked FROM unnest(CAST(:ids AS uuid[])) AS t(id)"
)
assert _TASK_PARAM in AGENT_WORKED_SQL and "aw_task" not in _BATCH_SQL

#: Which row makes the predicate true: the newest run first, then the newest
#: MCP act. ``kind`` is ``agent_run`` or ``mcp_act``.
_REASON_SQL = (
    "(SELECT 'agent_run' AS kind, aw_r.id AS row_id, aw_r.created_at "
    f" FROM pm_agent_runs aw_r WHERE {_RUN_WHERE} "
    " ORDER BY aw_r.created_at DESC LIMIT 1) "
    "UNION ALL "
    "(SELECT 'mcp_act' AS kind, aw_a.id AS row_id, aw_a.created_at "
    f" FROM pm_activities aw_a WHERE {_MCP_WHERE} "
    " ORDER BY aw_a.created_at DESC LIMIT 1)"
)


@dataclass(frozen=True)
class AgentWork:
    """Why a task is agent-worked: the kind of row, its id and its time."""

    kind: str  # "agent_run" or "mcp_act"
    row_id: str
    created_at: datetime
    #: The newest agent row of either kind, for the take-back rule (§12.3 step 3).
    latest_at: datetime


def params(task_id: str, before: datetime) -> dict[str, Any]:
    """The two parameters of :data:`AGENT_WORKED_SQL`."""
    return {"aw_task": str(task_id), "aw_before": before}


async def reason(db: Any, task_id: str, before: datetime) -> AgentWork | None:
    """Why *task_id* is agent-worked at *before*, or ``None`` (§12.2)."""
    rows = (await db.execute(text(_REASON_SQL), params(task_id, before))).fetchall()
    if not rows:
        return None
    by_kind = {r.kind: r for r in rows}
    chosen = by_kind.get("agent_run") or by_kind["mcp_act"]
    latest = max(r.created_at for r in rows)
    return AgentWork(kind=chosen.kind, row_id=str(chosen.row_id),
                     created_at=chosen.created_at, latest_at=latest)


async def is_agent_worked(db: Any, task_id: str, before: datetime) -> bool:
    """The predicate alone, through the one SQL copy."""
    row = (await db.execute(text(f"SELECT {AGENT_WORKED_SQL} AS worked"),
                            params(task_id, before))).first()
    return bool(row and row.worked)


async def worked_among(db: Any, task_ids: list[str], before: datetime) -> set[str]:
    """The ids of *task_ids* that are agent-worked at *before*: ONE query for
    the whole list, through the same boolean as :func:`is_agent_worked`."""
    ids = [str(i) for i in dict.fromkeys(task_ids) if i]
    if not ids:
        return set()
    found = (await db.execute(text(_BATCH_SQL), {"ids": ids, "aw_before": before})).fetchall()
    return {str(r.task_id) for r in found if r.worked}


__all__ = [
    "AGENT_WORKED_SQL",
    "MCP_VIA_PREFIX",
    "AgentWork",
    "is_agent_worked",
    "params",
    "reason",
    "worked_among",
]
