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
   the set runs the rule again. A skip names the blocking references in the
   log line ``github.merge.blocked_by``. ⚠️ **An unreadable reference does
   not block for ever** (fix round 1, item 6). A reference that the App has
   failed to read (404, 410, 403 or 301) for :data:`UNREADABLE_AFTER` or
   longer stops blocking. That is a pull request of the token era, or one in
   a repository outside the installation. When a refused read crosses the
   bound, :func:`on_unblocked` runs the rule again for its tasks;
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
from datetime import UTC, datetime, timedelta
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

#: §12.3 step 4: a reference that the App has failed to read for this long
#: stops blocking. Two refused reads of the poll (30 minutes apart) cross it.
UNREADABLE_AFTER = timedelta(hours=1)

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

#: Every pull request of one task (§12.3 step 4), with its refusal marker.
_PR_STATES_SQL = (
    "SELECT ref, state, merged_at, unreadable_since FROM pm_task_refs "
    "WHERE task_id = CAST(:tid AS uuid) "
    "AND provider = 'github' AND kind = 'pull_request' "
    "ORDER BY merged_at DESC NULLS LAST, ref"
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
    #: The org of the merge. The events go out after the caller's commit, when
    #: the request has released its tenant, so :meth:`emit_all` binds it again,
    #: as ``claim_task`` emits inside a bound request.
    org: str | None = None

    async def emit_all(self) -> None:
        from gateway.db import bind_tenant, release_tenant

        token = bind_tenant(self.org) if self.org else None
        try:
            for name, payload in self.events:
                await emit(name, payload)
        finally:
            release_tenant(token)


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


def past_unreadable_bound(since: datetime | None, now: datetime | None = None) -> bool:
    """True when a reference first refused at *since* no longer blocks."""
    if since is None:
        return False
    stamp = since if since.tzinfo else since.replace(tzinfo=UTC)
    return (now or datetime.now(UTC)) - stamp >= UNREADABLE_AFTER


def _blocking(rows: list[Any], now: datetime) -> list[str]:
    """The pull requests that hold the task open (§12.3 step 4)."""
    return [
        str(r.ref) for r in rows
        if r.state in ("open", "draft") and not past_unreadable_bound(r.unreadable_since, now)
    ]


async def on_merged(
    db: Any,
    org: str,
    ref: str,
    merged_at: datetime | None,
    merged_by: str | None,
    *,
    delivery: str | None = None,
    only_tasks: set[str] | None = None,
) -> MergeOutcome:
    """Run the rule of §12.3 for the pull request *ref* (``owner/repo#N``).

    *merged_at* is ``None`` for a close without a merge. The function writes in
    the caller's transaction and returns the events to emit after its commit.
    *only_tasks* holds the tasks whose reference just moved INTO a terminal
    state (``github_sync.apply_pull_request``). Every other task is left alone.
    """
    outcome = MergeOutcome(org=org)
    tasks = (await db.execute(text(_TASKS_SQL), {"org": org, "ref": ref})).fetchall()
    for task in tasks:
        if only_tasks is not None and str(task.id) not in only_tasks:
            continue
        await _rule_for_task(db, task, ref, merged_at, merged_by, delivery, outcome)
    return outcome


async def on_unblocked(
    db: Any, org: str, ref: str, *, only_tasks: set[str] | None = None,
) -> MergeOutcome:
    """Run the rule again for each task that holds *ref*, which has stopped
    blocking (§12.3 step 4). The rule runs for the task's newest MERGED pull
    request, so the timeline names the merge that finished the task.
    *only_tasks* holds the tasks whose row crossed the bound on THIS read."""
    outcome = MergeOutcome(org=org)
    tasks = (await db.execute(text(_TASKS_SQL), {"org": org, "ref": ref})).fetchall()
    for task in tasks:
        if only_tasks is not None and str(task.id) not in only_tasks:
            continue
        rows = (await db.execute(text(_PR_STATES_SQL), {"tid": str(task.id)})).fetchall()
        merged = next((r for r in rows if r.state == "merged"), None)
        if merged is None:
            continue
        await _rule_for_task(
            db, task, str(merged.ref), merged.merged_at or datetime.now(UTC), None, None,
            outcome,
        )
    return outcome


async def _rule_for_task(
    db: Any,
    task: Any,
    ref: str,
    merged_at: datetime | None,
    merged_by: str | None,
    delivery: str | None,
    outcome: MergeOutcome,
) -> None:
    """The seven steps of §12.3 for one task."""
    task_id = str(task.id)
    at = merged_at or datetime.now(UTC)
    # 1. A closed or held task.
    if task.archived_at is not None or task.status_category in (
            CLOSING_CATEGORIES | {TRIAGE_CATEGORY}):
        return
    # 2. A task that people run.
    work = await agent_worked.reason(db, task_id, at)
    if work is None:
        return
    # 3. A task that a person took back.
    if await _person_took_it_back(db, task_id, work):
        _log.info("github.merge.taken_back", task_id=task_id)
        return
    # 4. The other pull requests of the task.
    rows = (await db.execute(text(_PR_STATES_SQL), {"tid": task_id})).fetchall()
    blocking = _blocking(rows, datetime.now(UTC))
    if blocking:
        _log.info("github.merge.blocked_by", task_id=task_id, refs=blocking[:10])
        return
    if not any(r.state == "merged" for r in rows):
        return
    # 5. The done lane of the task's own status set.
    try:
        lane = await load_default_status(
            db, await status_owner_id(db, str(task.project_id)), COMPLETED_CATEGORY,
        )
    except HTTPException:
        _log.warning("github.merge.no_done_lane", task_id=task_id)
        return
    # 6. The move, as GitHub, in the one status helper.
    repo, number = _repo_and_number(ref)
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


__all__ = [
    "GITHUB_ACTOR",
    "UNREADABLE_AFTER",
    "MergeOutcome",
    "on_merged",
    "on_unblocked",
    "past_unreadable_bound",
]
