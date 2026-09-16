"""Projects · watchers — who follows a task (WS-27v).

Spec: ``ai-company-brain/specs/project_management_app.md`` §9.1, WS-27v.

    PUT    /projects/tasks/{id}/watch     → subscribe the caller
    DELETE /projects/tasks/{id}/watch     → unsubscribe the caller
    GET    /projects/tasks/{id}/watchers  → {watchers, watching}

A watcher row is an **intent to hear, never a right to see**. The fan-out in
``notifications.py`` still filters every recipient through
``resolve_visibility_for`` — Plane's membership-only check is the
counterexample, not the model — so a watcher who has lost the project's grant
keeps their row and stops hearing, exactly as they stop seeing.

Auto-subscribe has ONE implementation: :func:`ensure_watchers`, called from
every write site that means "this person now cares about this task" —
commenting, editing, being assigned, being @mentioned, and the explicit watch
endpoint below. One helper rather than an INSERT per site, because the
casefolding (R10) and the agent fence are rules, and a rule copied five times
is a rule that drifts in one of them.
"""

from __future__ import annotations

from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends
from gateway.routes.projects.core import (
    MAX_DEPTH,
    _tenant_session,
    actor,
    load_visible_project,
    load_visible_task,
    resolve_visibility,
    router,
)
from sqlalchemy import text


def watchable(recipients: object) -> list[str]:
    """The addresses a watcher row may carry: folded, deduped, human.

    Pure, and deliberately the same three fences as migration 165's CHECKs —
    blanks, ``agent:<name>`` and non-addresses are dropped here so a bad value
    is silently skipped rather than surfacing as an IntegrityError 500 from a
    comment that otherwise succeeded. Agents are excluded for WS-27j's rule 2:
    they are handed work by the dispatch sink, and a subscription would feed an
    inbox nobody ever opens.
    """
    seen: dict[str, None] = {}
    for raw in recipients or ():
        clean = (raw or "").strip().lower()
        if clean and "@" in clean and not clean.startswith("agent:"):
            seen.setdefault(clean, None)
    return list(seen)


async def ensure_watchers(
    db: Any, task_id: str, recipients: object, *, by: str,
) -> list[str]:
    """Idempotently subscribe people to a task; returns who was newly added.

    Idempotent by the UNIQUE(task_id, watcher) pair, not by a read-then-write:
    two comments landing together must not race their way into a duplicate.
    Does **not** commit — it runs inside the write that caused it, so an edit
    and the subscription it implies land together or not at all.

    Deliberately no visibility check here: the write sites that call this have
    already loaded the task through the caller's own visibility, and delivery
    to the *watcher* is gated per event by ``resolve_visibility_for`` at
    fan-out time — the one place that answer is allowed to live.
    """
    added: list[str] = []
    for who in watchable(recipients):
        row = (await db.execute(
            text(
                "INSERT INTO pm_task_watchers (task_id, watcher, created_by) "
                "VALUES (CAST(:tid AS uuid), :who, :by) "
                "ON CONFLICT (task_id, watcher) DO NOTHING RETURNING id"
            ),
            {"tid": task_id, "who": who, "by": by},
        )).fetchone()
        if row is not None:
            added.append(who)
    return added


async def watchers_of(db: Any, task_id: str) -> list[str]:
    """Everyone subscribed to one task, sorted."""
    rows = (await db.execute(
        text(
            "SELECT watcher FROM pm_task_watchers "
            "WHERE task_id = CAST(:tid AS uuid) ORDER BY watcher"
        ),
        {"tid": task_id},
    )).fetchall()
    return [r.watcher for r in rows if getattr(r, "watcher", None)]


@router.put("/tasks/{task_id}/watch")
async def watch_task(
    task_id: str, user: UserContext = Depends(get_current_user),
) -> dict:
    """Subscribe the caller. Idempotent — watching twice is watching.

    The identity is the session's (R3), never a body field: an endpoint that
    accepted a ``watcher`` parameter would let anyone subscribe anyone else to
    a stream of that task's titles.
    """
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        # R5: an invisible task is 404, never 403 — "not yours" and "no such
        # task" must be one answer, or this endpoint becomes an oracle for
        # which ids exist.
        await load_visible_task(db, vis, task_id)
        await ensure_watchers(db, task_id, [actor(user)], by=actor(user))
    return {"task_id": task_id, "watching": True}


@router.delete("/tasks/{task_id}/watch")
async def unwatch_task(
    task_id: str, user: UserContext = Depends(get_current_user),
) -> dict:
    """Unsubscribe the caller. Idempotent — a row that is not there stays gone.

    Unwatching does not silence assignment: the audience is watchers ∪
    assignees, so somebody who holds the work keeps hearing about it. That is
    the contract's own shape, not an oversight.
    """
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        await load_visible_task(db, vis, task_id)
        await db.execute(
            text(
                "DELETE FROM pm_task_watchers "
                "WHERE task_id = CAST(:tid AS uuid) AND watcher = :who"
            ),
            {"tid": task_id, "who": actor(user).lower()},
        )
    return {"task_id": task_id, "watching": False}


@router.get("/tasks/{task_id}/watchers")
async def list_watchers(
    task_id: str, user: UserContext = Depends(get_current_user),
) -> dict:
    """Who watches this task, and whether the caller does — one read, so the
    panel's toggle can render without a second round trip."""
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        await load_visible_task(db, vis, task_id)
        watchers = await watchers_of(db, task_id)
    return {
        "watchers": watchers,
        "watching": actor(user).lower() in set(watchers),
    }


# ── Watching a PROJECT (WS-27bk §9.12.2(b)) ─────────────────────────────────
#
# ⚠️ **A project watch is its OWN row, and it never expands into task rows.**
# The expansion would be wrong twice: it goes stale the moment somebody adds a
# task, and it writes thousands of rows for one click. So the subscription is
# one row against the node, and the CHAIN is resolved at fan-out time — which
# is what makes a task created today reach a subscription taken a year ago.
#
# The three endpoints mirror the task verbs exactly, because a member who has
# learned one has learned the other, and migration 203 mirrors 165's shape for
# the same reason.

#: Everyone watching any node on the path from ``:pid`` up to its root.
#:
#: ⚠️ **Walks UP, never DOWN.** Watching a parent hears about its children,
#: which is the ask ("tell me about this project" covers the work inside it).
#: Watching a child must NOT subscribe you to a sibling's traffic, and a
#: downward walk from the root would do exactly that.
#:
#: Bounded by ``MAX_DEPTH`` like every other walk here: this runs on the
#: notification path, and a corrupted parent chain must stop rather than spin.
_CHAIN_WATCHERS_SQL = """
WITH RECURSIVE chain AS (
    SELECT id, parent_project_id, 1 AS depth
      FROM pm_projects
     WHERE id = CAST(:pid AS uuid)
    UNION ALL
    SELECT p.id, p.parent_project_id, c.depth + 1
      FROM pm_projects p
      JOIN chain c ON p.id = c.parent_project_id
     WHERE c.depth < :max_depth
)
SELECT DISTINCT w.watcher
  FROM pm_project_watchers w
  JOIN chain c ON c.id = w.project_id
"""


async def project_chain_watchers(db: Any, project_id: object) -> list[str]:
    """Watchers of ``project_id`` and of every ancestor above it, sorted.

    One round trip rather than a Python loop per ancestor: this is on the
    notification fan-out path, and ``root_project_id``'s loop shape is a WRITE
    path helper where the extra reads are already paid for.

    An absent or NULL ``project_id`` answers empty rather than raising — a
    personal task has no project, and a notification for one must still be
    delivered rather than 500.
    """
    if not project_id:
        return []
    rows = (await db.execute(
        text(_CHAIN_WATCHERS_SQL),
        {"pid": str(project_id), "max_depth": MAX_DEPTH},
    )).fetchall()
    return sorted(
        r.watcher for r in rows if getattr(r, "watcher", None)
    )


async def project_watchers_of(db: Any, project_id: str) -> list[str]:
    """Everyone subscribed to ONE node, sorted.

    Deliberately not the chain: this answers "who watches this project", which
    is what the header toggle renders. The chain is an audience question, and
    it belongs to the fan-out.
    """
    rows = (await db.execute(
        text(
            "SELECT watcher FROM pm_project_watchers "
            "WHERE project_id = CAST(:pid AS uuid) ORDER BY watcher"
        ),
        {"pid": project_id},
    )).fetchall()
    return [r.watcher for r in rows if getattr(r, "watcher", None)]


@router.put("/nodes/{project_id}/watch")
async def watch_project(
    project_id: str, user: UserContext = Depends(get_current_user),
) -> dict:
    """Subscribe the caller to a project. Idempotent — watching twice is watching.

    The identity is the session's (R3), never a body field, for the same reason
    the task verb refuses one: an endpoint that accepted a ``watcher`` would let
    anyone subscribe anyone else to a subtree's whole event stream.
    """
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        # R5: an invisible project is 404, never 403 — see load_visible_project.
        await load_visible_project(db, vis, project_id)
        who = actor(user).lower()
        if watchable([who]):
            await db.execute(
                text(
                    "INSERT INTO pm_project_watchers "
                    "  (project_id, watcher, created_by) "
                    "VALUES (CAST(:pid AS uuid), :who, :by) "
                    "ON CONFLICT (project_id, watcher) DO NOTHING"
                ),
                {"pid": project_id, "who": who, "by": who},
            )
    return {"project_id": project_id, "watching": True}


@router.delete("/nodes/{project_id}/watch")
async def unwatch_project(
    project_id: str, user: UserContext = Depends(get_current_user),
) -> dict:
    """Unsubscribe the caller. Idempotent — a row that is not there stays gone.

    Unwatching a project does not silence the tasks inside it that the caller
    watches or is assigned in. The audience is a union, and each membership
    stands on its own — the same contract the task verb keeps.
    """
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        await load_visible_project(db, vis, project_id)
        await db.execute(
            text(
                "DELETE FROM pm_project_watchers "
                "WHERE project_id = CAST(:pid AS uuid) AND watcher = :who"
            ),
            {"pid": project_id, "who": actor(user).lower()},
        )
    return {"project_id": project_id, "watching": False}


@router.get("/nodes/{project_id}/watchers")
async def list_project_watchers(
    project_id: str, user: UserContext = Depends(get_current_user),
) -> dict:
    """Who watches this node, whether the caller does, and whether an ancestor
    already covers them — one read, so the header toggle renders without a
    second round trip.

    ``inherited`` is what stops the toggle from lying. A member who watches the
    parent already hears about this project, and a control that reads "not
    watching" would invite a second subscription that changes nothing.
    """
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        await load_visible_project(db, vis, project_id)
        watchers = await project_watchers_of(db, project_id)
        chain = await project_chain_watchers(db, project_id)
    me = actor(user).lower()
    return {
        "project_id": project_id,
        "watchers": watchers,
        "watching": me in set(watchers),
        "inherited": me in set(chain) and me not in set(watchers),
    }
