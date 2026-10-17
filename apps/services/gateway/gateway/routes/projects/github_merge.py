"""Projects · a merge can close the task that an agent worked (WS-54 GH-6, G-4).

Spec: ``project-docs/specs/github_integration.md`` §12.

**The trigger (§12.1).** A ``pull_request`` ``closed`` event, merged or not,
or a reconcile read that finds ``merged`` or ``closed``. The caller has
written the reference's new state first, in the same transaction, and calls
:func:`on_merged` in the bound tenant.

**The steps (§12.3)**, for each task with a ``pull_request`` reference to the
pull request:

1. skip a task that is archived, or whose status category is ``done``,
   ``cancelled`` or ``triage``;
2. skip a task that people run: ``agent_worked.reason`` is ``None``;
3. skip a task that a person took back: its newest ``status_change`` row has
   no ``mcp:`` or ``github`` via, was not written by a ``system:`` actor, and
   is newer than the newest agent row. A move through the in-product chat
   (``chat:projects-assistant``) is a person's move;
4. skip while another pull request of the task is ``open`` or ``draft``, and
   when none of its pull requests is ``merged``. The last merge or close of
   the set runs the rule again;
5. find the done lane with ``load_default_status`` (the lane rule of
   ``complete_for_member``). No done lane skips the task and logs;
6. move: ``ACTOR_VIA`` is ``github``, then ``apply_status_transition`` with
   ``created_by = "system:github"`` and ``automation = True``. The recurrence
   successor comes from that helper. Then one ``system`` row names the reason;
7. after the commit, ``pm.task.updated`` and ``pm.task.status_changed`` go out.
   This function RETURNS them, and the caller emits them after its commit.

The subtasks stay open, an open agent run stays open, and nothing moves a
task to ``cancelled``. ⚠️ ``core.py`` does not change for this rule (D11).

Fence: ``tests/unit/test_github_merge_r8.py`` (R8).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from acb_common import get_logger
from fastapi import HTTPException
from gateway.routes.projects import agent_worked
from gateway.routes.projects.core import (
    ACTOR_VIA,
    CLOSING_CATEGORIES,
    COMPLETED_CATEGORY,
    TRIAGE_CATEGORY,
    apply_status_transition,
    emit,
    load_default_status,
    record_activity,
    status_owner_id,
)
from sqlalchemy import text

_log = get_logger("gateway.projects.github_merge")

GITHUB_ACTOR = "system:github"
GITHUB_VIA = "github"

#: Every task of the org with a ``pull_request`` reference to this one.
_TASKS_SQL = (
    "SELECT t.*, s.category AS status_category FROM pm_tasks t "
    "JOIN pm_task_statuses s ON s.id = t.status_id "
    "WHERE t.organization_id = CAST(:org AS uuid) AND t.id IN ("
    "  SELECT r.task_id FROM pm_task_refs r "
    "  WHERE r.organization_id = CAST(:org AS uuid) AND r.provider = 'github' "
    "  AND r.kind = 'pull_request' AND r.ref = :ref) "
    "ORDER BY t.created_at, t.id"
)

#: The states of every pull request of one task (§12.3 step 4).
_PR_STATES_SQL = (
    "SELECT state FROM pm_task_refs WHERE task_id = CAST(:tid AS uuid) "
    "AND provider = 'github' AND kind = 'pull_request'"
)

#: The newest status move of the task (§12.3 step 3).
_LAST_MOVE_SQL = (
    "SELECT created_by, created_at, meta FROM pm_activities "
    "WHERE task_id = CAST(:tid AS uuid) AND type = 'status_change' "
    "ORDER BY created_at DESC, id DESC LIMIT 1"
)


@dataclass
class MergeOutcome:
    """What one merge did: the moved task ids and the events to emit after
    the commit (§12.3 step 7)."""

    moved: list[str] = field(default_factory=list)
    events: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    async def emit_all(self) -> None:
        for name, payload in self.events:
            await emit(name, payload)


def _via_of(meta: Any) -> str:
    if isinstance(meta, dict):
        return str(meta.get("via") or "")
    return ""


async def _person_took_it_back(db: Any, task_id: str, work: agent_worked.AgentWork) -> bool:
    """§12.3 step 3: the newest status move is a person's, after the agent."""
    row = (await db.execute(text(_LAST_MOVE_SQL), {"tid": task_id})).first()
    if row is None:
        return False
    via = _via_of(row.meta)
    by_machine_via = via.startswith(agent_worked.MCP_VIA_PREFIX) or via == GITHUB_VIA
    by_system = str(row.created_by or "").startswith("system:")
    return not by_machine_via and not by_system and row.created_at > work.latest_at


def _repo_and_number(ref: str) -> tuple[str, int | None]:
    repo, _, number = ref.partition("#")
    return repo, (int(number) if number.isdigit() else None)


async def on_merged(
    db: Any,
    org: str,
    ref: str,
    merged_at: datetime | None,
    merged_by: str | None,
    *,
    delivery: str | None = None,
) -> MergeOutcome:
    """Run the rule of §12.3 for the pull request *ref* (``owner/repo#N``).

    *merged_at* is ``None`` for a close without a merge. The function writes in
    the caller's transaction and returns the events to emit after its commit.
    """
    outcome = MergeOutcome()
    at = merged_at or datetime.now(UTC)
    repo, number = _repo_and_number(ref)
    tasks = (await db.execute(text(_TASKS_SQL), {"org": org, "ref": ref})).fetchall()
    for task in tasks:
        task_id = str(task.id)
        # 1. A closed or held task.
        if task.archived_at is not None or task.status_category in (
                CLOSING_CATEGORIES | {TRIAGE_CATEGORY}):
            continue
        # 2. A task that people run.
        work = await agent_worked.reason(db, task_id, at)
        if work is None:
            continue
        # 3. A task that a person took back.
        if await _person_took_it_back(db, task_id, work):
            _log.info("github.merge.taken_back", task_id=task_id)
            continue
        # 4. The other pull requests of the task.
        states = [r.state for r in (await db.execute(
            text(_PR_STATES_SQL), {"tid": task_id})).fetchall()]
        if any(s in ("open", "draft") for s in states) or "merged" not in states:
            continue
        # 5. The done lane of the task's own status set.
        try:
            lane = await load_default_status(
                db, await status_owner_id(db, str(task.project_id)), COMPLETED_CATEGORY,
            )
        except HTTPException:
            _log.warning("github.merge.no_done_lane", task_id=task_id)
            continue
        # 6. The move, as GitHub, in the one status helper.
        token = ACTOR_VIA.set(GITHUB_VIA)
        try:
            moved = await apply_status_transition(
                db, task, str(lane.id), created_by=GITHUB_ACTOR, automation=True,
            )
            word = "merged" if merged_at is not None else "closed"
            meta: dict[str, Any] = {
                "via": GITHUB_VIA, "automation": True,
                "github": {"repo": repo, "number": number, "merged_by": merged_by,
                           "delivery": delivery},
                "reason": work.kind,
            }
            meta["agent_run_id" if work.kind == "agent_run" else "activity_id"] = work.row_id
            await record_activity(
                db, activity_type="system", created_by=GITHUB_ACTOR, task_id=task_id,
                body=f"GitHub moved this to Done after {ref} {word}",
                meta=meta, automation=True,
            )
        finally:
            ACTOR_VIA.reset(token)
        outcome.moved.append(task_id)
        # 7. The events wait for the caller's commit.
        outcome.events.append(("pm.task.updated", {"task_id": task_id}))
        outcome.events.append(("pm.task.status_changed", {
            "task_id": task_id, "from": moved["from"].name, "to": moved["to"].name,
            "to_category": moved["to"].category,
        }))
        _log.info("github.merge.moved", task_id=task_id, reason=work.kind)
    return outcome


__all__ = ["GITHUB_ACTOR", "MergeOutcome", "on_merged"]
