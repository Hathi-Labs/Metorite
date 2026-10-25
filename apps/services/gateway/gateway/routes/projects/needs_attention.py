"""Projects · Needs attention — WS-55 SW-11a (Metorite task #342).

Spec: ``project-docs/specs/external_agent_access.md`` §12.7.

    GET /projects/needs-attention?project_id=&mine=&offset=&limit=
        → {rows, counts, total, listed, offset, limit, capped}

The work that stopped and that waits for a person, in three kinds. Each row
names one task and the one next action that ends it:

1. ``stalled``. An open recovery record of SW-10a that still fits its run
   (``filters.open_recovery_sql``). The action is Resume
   (``agent_runs.resume_agent_run``), and Close the run is the second one.
2. ``dangling``. An open run of a closed task (``filters.task_closed_sql``).
   The action is Close the run, which is the release of EA-14a.
3. ``blocked``. An open task that an open ``blocks`` link
   (``filters.open_block_sql``) blocks, where the blocker is parked: a
   ``backlog`` status and nobody holds it by RA-1's holder rule
   (``analytics.holder_label_sql``). The action opens the blocker.

⚠️ **Each rule here is a rule that already exists, joined.** The scope is
``analytics.scope_clause``, the open-work predicate is Load's
``analytics.load_open_where`` (as the handoffs of RA-3 read it), and who may
act is ``agent_runs.person_may_end_sql``, the rule of Release. A second copy
of any of them would let this list and the board disagree.

⚠️ **§7 rule 1.** Each query carries ``task_visibility_clause``, the blocker's
too, and the caller's tenant session runs each one. Nothing here reads a
tenant or a person from the request (R5, R11): ``mine`` and ``yours`` read
the session's member.

⚠️ **It only reads.** It sends no bell and writes nothing, so a deploy brings
no storm of notifications (spec §12.7, "Rollout: prospective only"). No flag
of its own. While ``agent_runs_enabled`` is off, the two run kinds read
nothing, as ``agent_summary.py`` does, because their actions answer 404 then.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from acb_auth import UserContext, agent_oauth, get_current_user
from fastapi import Depends, Query
from gateway.routes.projects.agent_runs import (
    RELEASE_ADMIN_PERMISSION,
    person_may_end_sql,
)
from gateway.routes.projects.analytics import (
    holder_label_sql,
    load_open_where,
    load_params,
    scope_clause,
)
from gateway.routes.projects.core import (
    _tenant_session,
    actor,
    resolve_visibility,
    router,
    task_visibility_clause,
)
from gateway.routes.projects.filters import (
    AGENT_RUN_OPEN_STATUSES,
    agent_runs_enabled,
    connection_gone_sql,
    open_block_sql,
    open_recovery_sql,
    task_closed_sql,
)
from sqlalchemy import text

#: The three kinds, in the order the list prints them.
KIND_STALLED = "stalled"
KIND_DANGLING = "dangling"
KIND_BLOCKED = "blocked"
KINDS: tuple[str, ...] = (KIND_STALLED, KIND_DANGLING, KIND_BLOCKED)

#: The actions. ``resume`` and ``close_run`` are member writes on the run.
#: ``open_blocker`` only opens a task.
ACTION_RESUME = "resume"
ACTION_CLOSE_RUN = "close_run"
ACTION_OPEN_BLOCKER = "open_blocker"

#: The one next action of each kind, then any second action.
#:
#: Resume asks the SAME agent client to continue, and the run keeps the task.
#: Close the run frees the task for anyone. A stalled run whose agent
#: connection is revoked or expired takes Close the run only
#: (:data:`STALLED_CONNECTION_GONE`): no session of that client can come back,
#: and the resume route refuses it.
ACTIONS: dict[str, tuple[str, ...]] = {
    KIND_STALLED: (ACTION_RESUME, ACTION_CLOSE_RUN),
    KIND_DANGLING: (ACTION_CLOSE_RUN,),
    KIND_BLOCKED: (ACTION_OPEN_BLOCKER,),
}
#: The acts of a stalled run with a dead connection (spec §12.7).
STALLED_CONNECTION_GONE: tuple[str, ...] = (ACTION_CLOSE_RUN,)

#: The status category of a parked blocker.
PARKED_CATEGORY = "backlog"

#: The most rows that one kind reads, the oldest first. Its count stays
#: exact, and ``capped`` says when a kind holds more.
MAX_PER_KIND = 200
#: The largest page, and the page when the caller names none.
MAX_LIMIT = 200
DEFAULT_LIMIT = 100


def _name(email: str) -> str:
    """The directory name of a member, as ``filters.agent_state_sql`` reads it."""
    return (
        f"(SELECT u.display_name FROM app_user u"
        f" WHERE lower(u.email) = lower({email}) LIMIT 1)"
    )


_OPEN_LIST = ", ".join(f"'{s}'" for s in AGENT_RUN_OPEN_STATUSES)

#: The task columns of every kind, over the task alias ``t``, the status
#: alias ``s`` and the project alias ``tp``.
_TASK_COLUMNS = (
    "t.id AS task_id, t.task_number, t.title, t.project_id, t.parent_task_id,"
    " t.archived_at, tp.name AS project_name, s.category"
)


def _run_where(scope_sql: str, vis: Any) -> str:
    """The tasks of the two run kinds: in the scope, and visible to the reader.

    Not Load's open-work predicate: a dangling run is a run of a CLOSED or an
    archived task, and that predicate keeps only open ones.
    """
    return f"({scope_sql}) AND ({task_visibility_clause(vis, 't')})"


def stalled_sql(scope_sql: str, vis: Any) -> str:
    """The ``stalled`` kind: an open recovery record that still fits its run,
    on a task that is not closed. One row for each run. Binds ``:me`` and
    ``:cap``."""
    return (
        f"SELECT DISTINCT ON (r.id) {_TASK_COLUMNS},"
        f"       r.id AS run_id, r.status AS run_status, r.client_via,"
        f"       r.member_email AS owner, {_name('r.member_email')} AS owner_name,"
        f"       s2.reason AS reason, r.last_activity_at AS since,"
        f"       {person_may_end_sql('r')} AS may_end,"
        f"       {connection_gone_sql('r')} AS connection_gone,"
        f"       (r.member_email = :me) AS yours"
        f"  FROM pm_agent_run_strands s2"
        f"  JOIN pm_agent_runs r ON r.id = s2.run_id"
        f"  JOIN pm_tasks t ON t.id = r.task_id"
        f"  JOIN pm_projects tp ON tp.id = t.project_id"
        f"  LEFT JOIN pm_task_statuses s ON s.id = t.status_id"
        f" WHERE {open_recovery_sql('s2', 'r')}"
        f"   AND NOT {task_closed_sql('r')}"
        f"   AND {_run_where(scope_sql, vis)}"
        f" ORDER BY r.id, s2.recovered_at DESC"
    )


def dangling_sql(scope_sql: str, vis: Any) -> str:
    """The ``dangling`` kind: an open run of a task that is archived, done or
    cancelled. ``reason`` says which, ``archived`` first, as the sweep's
    close reads it. Binds ``:me``."""
    return (
        f"SELECT {_TASK_COLUMNS},"
        f"       r.id AS run_id, r.status AS run_status, r.client_via,"
        f"       r.member_email AS owner, {_name('r.member_email')} AS owner_name,"
        f"       CASE WHEN t.archived_at IS NOT NULL THEN 'archived'"
        f"            ELSE s.category END AS reason,"
        f"       r.last_activity_at AS since,"
        f"       {person_may_end_sql('r')} AS may_end,"
        f"       {connection_gone_sql('r')} AS connection_gone,"
        f"       (r.member_email = :me) AS yours"
        f"  FROM pm_agent_runs r"
        f"  JOIN pm_tasks t ON t.id = r.task_id"
        f"  JOIN pm_projects tp ON tp.id = t.project_id"
        f"  LEFT JOIN pm_task_statuses s ON s.id = t.status_id"
        f" WHERE r.status IN ({_OPEN_LIST})"
        f"   AND {task_closed_sql('r')}"
        f"   AND {_run_where(scope_sql, vis)}"
    )


def blocked_sql(open_where: str, blocking_where: str) -> str:
    """The ``blocked`` kind: one row for each open ``blocks`` link whose
    blocker is parked.

    ``open_where`` is Load's open-work predicate over the scope, for the
    blocked task ``t``. ``blocking_where`` is the same predicate over every
    task the reader can see. The blocker test runs in a subquery whose own
    ``pm_tasks t`` and ``pm_task_statuses s`` hide the outer ones, so RA-1's
    ``holder_label_sql`` and ``load_open_where`` read the blocker with no
    copy. A NULL holder label is nobody: no assignee and no open run that
    keeps the task. Binds ``:closed``, ``:parked`` and ``:me``.
    """
    return (
        f"SELECT {_TASK_COLUMNS},"
        f"       b.id AS blocker_id, b.task_number AS blocker_number,"
        f"       b.title AS blocker_title, l.created_at AS since,"
        f"       ow.owner, {_name('ow.owner')} AS owner_name,"
        f"       EXISTS (SELECT 1 FROM pm_task_assignees y"
        f"                WHERE y.task_id = t.id AND lower(y.assignee) = :me) AS yours"
        f"  FROM pm_task_links l"
        f"  JOIN pm_tasks t ON t.id = l.target_task_id"
        f"  JOIN pm_task_statuses s ON s.id = t.status_id"
        f"  JOIN pm_projects tp ON tp.id = t.project_id"
        f"  JOIN pm_tasks b ON b.id = l.source_task_id"
        f"  JOIN pm_task_statuses bs ON bs.id = b.status_id"
        # The owner of a blocked row is the EARLIEST assignee of the blocked
        # task (`assigned_at`), with the address as the tie-break. A blank
        # assignee is no assignee, as RA-1's holder rule reads it.
        f"  LEFT JOIN LATERAL (SELECT x.assignee AS owner FROM pm_task_assignees x"
        f"     WHERE x.task_id = t.id AND btrim(coalesce(x.assignee, '')) <> ''"
        f"     ORDER BY x.assigned_at ASC, x.assignee ASC LIMIT 1) ow ON TRUE"
        f" WHERE {open_block_sql('l', 'bs')}"
        f"   AND bs.category = :parked"
        f"   AND ({open_where})"
        f"   AND EXISTS (SELECT 1 FROM pm_tasks t"
        f"                 JOIN pm_task_statuses s ON s.id = t.status_id"
        f"                WHERE t.id = b.id AND ({blocking_where})"
        f"                  AND ({holder_label_sql()}) IS NULL)"
    )


def _capped(inner: str) -> str:
    """At most :data:`MAX_PER_KIND` rows of a kind, the oldest first, each
    with the exact count of the kind. Binds ``:cap``."""
    return (
        f"SELECT k.*, count(*) OVER () AS kind_total FROM ({inner}) k"
        f" ORDER BY k.since ASC, k.task_id ASC LIMIT :cap"
    )


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _hours(clock: datetime, at: datetime | None) -> float | None:
    return None if at is None else round((clock - at).total_seconds() / 3600.0, 2)


def _row(kind: str, hit: Any, clock: datetime, admin: bool) -> dict[str, Any]:
    """The wire shape of one row. A field that does not apply is null."""
    run_id = getattr(hit, "run_id", None)
    via = getattr(hit, "client_via", None)
    gone = bool(getattr(hit, "connection_gone", False))
    actions = STALLED_CONNECTION_GONE if kind == KIND_STALLED and gone else ACTIONS[kind]
    can_act = True if kind == KIND_BLOCKED else bool(admin or hit.may_end)
    owner = getattr(hit, "owner", None)
    return {
        "kind": kind,
        "task_id": str(hit.task_id),
        "task_number": int(hit.task_number) if hit.task_number is not None else None,
        "title": hit.title,
        "project_id": str(hit.project_id),
        "project_name": hit.project_name,
        "parent_task_id": str(hit.parent_task_id) if hit.parent_task_id else None,
        "category": hit.category,
        "archived": hit.archived_at is not None,
        "owner": owner,
        "owner_name": getattr(hit, "owner_name", None) or owner,
        "since": _iso(hit.since),
        "age_hours": _hours(clock, hit.since),
        "run_id": str(run_id) if run_id else None,
        "run_status": getattr(hit, "run_status", None),
        "client_via": via,
        "client_name": agent_oauth.client_label(via) if via else None,
        "reason": getattr(hit, "reason", None),
        "blocker_id": str(hit.blocker_id) if getattr(hit, "blocker_id", None) else None,
        "blocker_number": (
            int(hit.blocker_number) if getattr(hit, "blocker_number", None) is not None
            else None
        ),
        "blocker_title": getattr(hit, "blocker_title", None),
        "action": actions[0],
        "actions": list(actions),
        # The rule of Release. A blocked row only opens a task, so any reader
        # who sees the row may take its action.
        "can_act": can_act,
        # True when the run's agent connection is revoked or expired. Then
        # no session of that client can come back, and Resume is refused.
        "connection_gone": gone,
        "can_resume": kind == KIND_STALLED and can_act and not gone,
        "yours": bool(hit.yours),
    }


@router.get("/needs-attention")
async def needs_attention(
    project_id: str | None = None,
    mine: bool = False,
    offset: int = Query(0, ge=0),
    limit: int = Query(DEFAULT_LIMIT, ge=0, le=MAX_LIMIT),
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """The stalled, dangling and blocked work of one project and its
    subprojects, or of every task the reader can see when ``project_id`` is
    absent (spec §12.7).

    A project the reader cannot see answers 404. ``mine`` keeps the rows the
    caller owns. The rows come stalled, dangling, blocked, and the oldest
    first inside each kind. ``counts`` and ``total`` are exact. ``listed``
    is the count of rows the page reads from, which is smaller than
    ``total`` only when a kind is ``capped``.
    """
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        scope_sql = await scope_clause(db, vis, project_id, True)
        params: dict[str, Any] = {
            **load_params(vis, project_id),
            "me": actor(user).strip().lower(),
            "parked": PARKED_CATEGORY,
            "cap": MAX_PER_KIND,
        }
        # One clock for every age, the database's, as the run states read it.
        clock = (await db.execute(text("SELECT now()"))).scalar()
        found: dict[str, list[Any]] = {kind: [] for kind in KINDS}
        if agent_runs_enabled():
            found[KIND_STALLED] = list((await db.execute(
                text(_capped(stalled_sql(scope_sql, vis))), params)).fetchall())
            found[KIND_DANGLING] = list((await db.execute(
                text(_capped(dangling_sql(scope_sql, vis))), params)).fetchall())
        open_where = load_open_where(scope_sql, vis)
        blocking_where = load_open_where("TRUE", vis)
        found[KIND_BLOCKED] = list((await db.execute(
            text(_capped(blocked_sql(open_where, blocking_where))), params)).fetchall())

    admin = user.has_permission(RELEASE_ADMIN_PERMISSION)
    counts: dict[str, int] = {}
    capped = False
    rows: list[dict[str, Any]] = []
    for kind in KINDS:
        hits = [h for h in found[kind] if h.yours] if mine else found[kind]
        # `kind_total` counts the whole kind. With `mine` the page counts
        # only the caller's rows of the capped read, so `capped` then says
        # that the count may be short.
        total = int(found[kind][0].kind_total) if found[kind] else 0
        counts[kind] = len(hits) if mine else total
        capped = capped or total > len(found[kind])
        rows.extend(_row(kind, hit, clock, admin) for hit in hits)
    page = rows[offset:offset + limit]
    return {
        "rows": page,
        "counts": counts,
        "total": sum(counts.values()),
        "listed": len(rows),
        "offset": offset,
        "limit": limit,
        "capped": capped,
    }


__all__ = [
    "ACTIONS",
    "STALLED_CONNECTION_GONE",
    "KINDS",
    "MAX_PER_KIND",
    "PARKED_CATEGORY",
    "blocked_sql",
    "dangling_sql",
    "needs_attention",
    "stalled_sql",
]
