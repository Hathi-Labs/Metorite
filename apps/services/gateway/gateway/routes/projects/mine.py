"""Projects · My tasks — the board reads over "mine" (WS-64 OP-11).

Spec: ``project-docs/specs/one_projects_app.md`` §9 OP-11.

    GET /projects/tasks?scope=mine[&with_overlay=1]    → the list, over mine
    GET /projects/calendar?scope=mine[&with_overlay=1] → the window, over mine
    GET /projects/my/summary                           → the Overview roll-up

My tasks is a virtual roll-up space. It is not a project and it is not
stored. It reads the one task store through ONE definition of "mine",
:func:`core.mine_clause`, which ``personal.MY_TASKS_FROM`` and
``/projects/assigned-to-me`` compose too.

⚠️ **A board read over mine keeps the board's rules.** ``scope=mine`` ANDs
the clause INTO the read, beside ``task_visibility_clause``. So a member never
sees a task here that they cannot open, and every filter, sort and page of
the board still applies. The personal lens (``/my/inbox``) has no visibility
clause, by design. Each arm of mine is inside the member's reach, so the two
answer the same set (``test_projects_mine_r8.py`` holds them equal).

⚠️ **Identity comes from the session (R5, R11).** ``scope`` is a word, never
an address. :func:`core.mine_binds` reads the member, the tenant and the
groups from the directory.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException
from gateway.routes.projects.core import (
    CLOSING_CATEGORIES,
    Visibility,
    _tenant_session,
    mine_binds,
    mine_clause,
    node_kind,
    reader_day_start,
    resolve_visibility,
    router,
    task_visibility_clause,
    triage_exclusion_clause,
)
from gateway.routes.projects.due_day import not_overdue_sql, overdue_sql
from gateway.routes.projects.filters import build_task_filters
from sqlalchemy import text

#: The one value ``scope`` takes. An absent scope is the board as before.
SCOPE_MINE = "mine"

#: "Due soon" on the Overview: open work due in the next seven days, the
#: window of Load's ``due_next_7d`` (`analytics.load`).
DUE_SOON_DAYS = 7

#: The disposition blocks of the Overview, in the order of the chips.
DISPOSITION_KEYS: tuple[str, ...] = (
    "inbox", "today", "next", "waiting", "someday", "done", "new",
)
# "new" (D170, INB-1, 2026-11-03): work assigned to me that I have not planned. It is
# a marker, a count and a chip filter on My tasks.


def check_scope(scope: str | None) -> bool:
    """True for ``scope=mine``, False for no scope. Any other value is a 422.

    A word and not an address: no request names whose tasks it reads.
    """
    if scope is None or scope == "":
        return False
    if scope == SCOPE_MINE:
        return True
    raise HTTPException(
        status_code=422,
        detail=f"Unknown scope '{scope}'. The one scope is '{SCOPE_MINE}'.",
    )


async def mine_scope(db: Any, vis: Visibility) -> tuple[str, dict[str, Any]]:
    """The clause and the binds that narrow a board read to the caller's own.

    The binds are the MEMBER's (:func:`core.mine_binds`). ``vis_groups``
    joins a ``data:org:read`` holder's binds too, where no other clause
    reads it, so the WAITING arm keeps the member's own closure.
    """
    return mine_clause("t"), await mine_binds(db, vis.email)


#: The task source of an agent (``pm_tasks.source``, migration 265).
AGENT_SOURCE = "mcp"

#: The two facts of a ready agent task that live on the task row. Aliases
#: ``t`` and ``s`` are the task and its status.
_HAS_DESCRIPTION_SQL = "(t.description IS NOT NULL AND btrim(t.description) <> '')"
_HAS_TYPE_SQL = "(t.type_id IS NOT NULL)"

#: What :func:`is_ready_agent_task` reads from a row, as select items.
AGENT_FACTS_SQL = (
    f"t.source AS t_source, {_HAS_DESCRIPTION_SQL} AS has_description,"
    f" {_HAS_TYPE_SQL} AS has_type"
)

#: The SQL twin of :func:`is_ready_agent_task` (D170 amendment, PJP-14). ONE
#: spelling: ``personal.UNTRIAGED_CLAUSE`` composes it, and a test holds the
#: Python and SQL answers equal. Aliases ``t`` and ``s``.
READY_AGENT_SQL = (
    f"(COALESCE(t.source = '{AGENT_SOURCE}', false)"
    f" AND {_HAS_DESCRIPTION_SQL} AND {_HAS_TYPE_SQL}"
    " AND s.category <> 'triage'"
    " AND EXISTS (SELECT 1 FROM pm_task_assignees ra WHERE ra.task_id = t.id)"
    " AND NOT EXISTS (SELECT 1 FROM pm_projects rpj"
    " WHERE rpj.id = t.project_id AND rpj.personal_owner IS NOT NULL))"
)

#: The caller's overlay and the facts the derived disposition reads, for the
#: rows of one page. LEFT, so a task with no overlay row still answers. The
#: tenant is on the read beside RLS (R5), as on ``MY_TASKS_FROM``.
_OVERLAY_SQL = (
    "SELECT t.id AS task_id, s.category AS status_category,"
    "       p.disposition, p.context, p.energy, p.defer_until,"
    "       EXISTS (SELECT 1 FROM pm_task_assignees a"
    "                WHERE a.task_id = t.id AND lower(a.assignee) = :who) AS is_mine,"
    "       EXISTS (SELECT 1 FROM pm_task_assignees a2"
    "                WHERE a2.task_id = t.id) AS has_assignee,"
    "       NOT EXISTS (SELECT 1 FROM pm_projects pj"
    "                    WHERE pj.id = t.project_id"
    "                      AND pj.personal_owner IS NOT NULL) AS on_board,"
    f"       {AGENT_FACTS_SQL}"
    "  FROM pm_tasks t"
    "  JOIN pm_task_statuses s ON s.id = t.status_id"
    "  LEFT JOIN pm_task_personal p"
    "         ON p.task_id = t.id AND lower(p.member_email) = :who"
    " WHERE t.id = ANY(CAST(:ids AS uuid[]))"
    "   AND t.organization_id = CAST(:vis_org AS uuid)"
)


async def attach_my_overlay(
    db: Any, vis: Visibility, rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Put the CALLER's overlay on each row, in one query for the page.

    Each row takes ``disposition`` (the effective one), ``is_triaged``,
    ``is_mine``, ``context``, ``energy`` and ``defer_until``. It also takes
    ``status_category``, the stage of its status, because the rows of My
    tasks come from many projects and the client groups them by stage (WS-64
    OP-11, the Board tab). A task with no
    overlay row takes the derived disposition of ``effective_disposition``,
    the one rule every reader calls (D77). The member is ``vis.email``, from
    the session.
    """
    # Imported here, not at the top: `personal` imports `tasks`, and `tasks`
    # imports this module, so a top-level import is a cycle.
    from gateway.routes.projects.personal import effective_disposition

    for row in rows:
        row.update({
            "disposition": None, "is_triaged": False, "is_mine": False,
            "context": None, "energy": None, "defer_until": None,
            "status_category": None, "is_new": False,
        })
    ids = [str(r["id"]) for r in rows if r.get("id")]
    if not ids:
        return rows
    found = (await db.execute(
        text(_OVERLAY_SQL),
        {"ids": ids, "who": vis.email, "vis_org": vis.organization_id},
    )).fetchall()
    by_id = {str(r.task_id): r for r in found}
    for row in rows:
        hit = by_id.get(str(row.get("id")))
        if hit is None:
            continue
        defer = getattr(hit, "defer_until", None)
        effective = effective_disposition(
            hit.disposition,
            status_category=hit.status_category,
            is_mine=bool(hit.is_mine),
            has_assignee=bool(hit.has_assignee),
        )
        row.update({
            "disposition": effective,
            "is_triaged": hit.disposition is not None,
            "is_mine": bool(hit.is_mine),
            "context": hit.context,
            "energy": hit.energy,
            "defer_until": defer.isoformat() if defer is not None else None,
            "status_category": hit.status_category,
            # D170: "New" is "no disposition of mine yet" on a company board.
            # A disposition write clears it, so it needs no write of its own.
            "is_new": new_work(
                hit.disposition, on_board=bool(getattr(hit, "on_board", True)),
                is_mine=bool(hit.is_mine), effective=effective,
                ready_agent=_ready_from_row(hit),
            ),
        })
    return rows


# ── GET /projects/my/summary ────────────────────────────────────────────────

def mine_where(vis: Visibility) -> tuple[str, dict[str, Any]]:
    """The WHERE of the Overview: the default board read with ``scope=mine``.

    The SAME clauses ``GET /projects/tasks?scope=mine`` composes with no
    other filter: the visibility clause, the one "mine", the archive rules
    of ``build_task_filters`` (the task and its project), and the triage
    exclusion. So the Overview counts the rows the List shows.
    """
    clauses = [task_visibility_clause(vis), mine_clause("t")]
    extra, params = build_task_filters()
    clauses.extend(extra)
    clauses.append(triage_exclusion_clause())
    return " AND ".join(clauses), params


def _summary_sql(where: str) -> str:
    """One row for each of my tasks, with what the fold reads."""
    return (
        "SELECT t.id, t.project_id, t.root_project_id, s.category,"
        "       t.start_date, t.due_at, t.completed_at,"
        "       po.disposition AS p_disposition, po.defer_until AS p_defer_until,"
        "       EXISTS (SELECT 1 FROM pm_task_assignees a3"
        "                WHERE a3.task_id = t.id AND lower(a3.assignee) = :who)"
        "                                                   AS is_mine,"
        "       EXISTS (SELECT 1 FROM pm_task_assignees a4"
        "                WHERE a4.task_id = t.id)           AS has_assignee,"
        "       NOT EXISTS (SELECT 1 FROM pm_projects pj"
        "                    WHERE pj.id = t.project_id"
        "                      AND pj.personal_owner IS NOT NULL)  AS on_board,"
        f"       {AGENT_FACTS_SQL},"
        f"       (t.due_at IS NOT NULL AND {overdue_sql()}"
        "        AND s.category <> ALL(CAST(:closed AS text[])))  AS is_overdue,"
        f"       (t.due_at IS NOT NULL AND {not_overdue_sql()}"
        "        AND t.due_at < CAST(:day_start AS timestamptz)"
        "                       + make_interval(days => :soon_days)"
        "        AND s.category <> ALL(CAST(:closed AS text[])))  AS is_due_soon"
        "  FROM pm_tasks t"
        "  JOIN pm_task_statuses s ON s.id = t.status_id"
        "  LEFT JOIN pm_task_personal po"
        "         ON po.task_id = t.id AND lower(po.member_email) = :who"
        f" WHERE {where}"
    )


def _projects_sql(vis: Visibility) -> str:
    """The projects of my tasks that the member may NAME (audit P1-8)."""
    return (
        "SELECT id, name, kind, status, icon, icon_slot, personal_owner,"
        "       archived_at, parent_project_id"
        "  FROM pm_projects"
        " WHERE id = ANY(CAST(:node_ids AS uuid[]))"
        f"   AND {vis.project_clause('id')}"
    )


def _empty_block() -> dict[str, Any]:
    return {"tasks": 0, "overdue": 0, "due_soon": 0, "by_category": {}}


def _add(block: dict[str, Any], category: str, overdue: bool, soon: bool) -> None:
    block["tasks"] += 1
    block["overdue"] += int(overdue)
    block["due_soon"] += int(soon)
    block["by_category"][category] = block["by_category"].get(category, 0) + 1


def _day(value: Any, zone: str) -> date | None:
    """The calendar day of a date or an instant, in the member's zone."""
    from gateway.routes.projects.personal import local_date

    if value is None:
        return None
    if isinstance(value, datetime):
        return local_date(zone, value if value.tzinfo else value.replace(tzinfo=UTC))
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def in_today(
    *, effective: str, is_mine: bool, completed_at: Any, defer_until: Any,
    start_date: Any, due_at: Any, today: date, zone: str,
) -> bool:
    """The Today rule of ``tasks/lib/today.ts`` ``isInToday``, on the server.

    Open, NEXT and mine, not deferred past today, started, and dated on or
    before today. An overdue task is in Today. The archive is out already.
    """
    if completed_at is not None or effective != "NEXT" or not is_mine:
        return False
    deferred = _day(defer_until, zone)
    if deferred is not None and deferred > today:
        return False
    start = _day(start_date, zone)
    if start is not None and start > today:
        return False
    due = _day(due_at, zone)
    return bool((start is not None and start <= today) or (due is not None and due <= today))


def is_ready_agent_task(
    *, on_board: bool, source: Any, has_assignee: bool, has_description: bool,
    has_type: bool, status_category: Any,
) -> bool:
    """An agent-made task that is set up in full (D170 amendment, PJP-14).

    On a company board, ``source`` is ``mcp``, at least one assignee, a
    description, a type, and a status that is not in the ``triage`` stage.
    A date or an estimate is not required. The SQL twin is
    :data:`READY_AGENT_SQL`, and a test holds the two to one answer. The
    rule reads the row and writes nothing, so an edit that breaks a
    condition makes the task New again.
    """
    return bool(
        on_board and source == AGENT_SOURCE and has_assignee and has_description
        and has_type and status_category != "triage"
    )


def _ready_from_row(row: Any) -> bool:
    """:func:`is_ready_agent_task` on a row that carries ``AGENT_FACTS_SQL``."""
    return is_ready_agent_task(
        on_board=bool(getattr(row, "on_board", True)),
        source=getattr(row, "t_source", None),
        has_assignee=bool(getattr(row, "has_assignee", False)),
        has_description=bool(getattr(row, "has_description", False)),
        has_type=bool(getattr(row, "has_type", False)),
        status_category=getattr(row, "status_category", None)
        or getattr(row, "category", None),
    )


def new_work(
    stated: Any, *, on_board: bool, is_mine: bool, effective: str,
    ready_agent: bool = False,
) -> bool:
    """"New" on My tasks: ``personal.UNTRIAGED_CLAUSE`` on one row (D170).

    No stated disposition of mine, a company board (not a personal project),
    assigned to me, and open (``/my/inbox?untriaged=true`` drops DONE and
    TRASH). Any disposition write clears it. A ready agent task is never
    New (:func:`is_ready_agent_task`). It keeps its derived disposition.
    """
    return (
        stated is None and on_board and is_mine
        and effective not in ("DONE", "TRASH") and not ready_agent
    )


def fold_dispositions(
    rows: list[Any], *, at: datetime, today: date, zone: str,
) -> dict[str, int]:
    """The chip counts, with the rules of ``taskStore.viewCounts`` and ``inboxCount``.

    ``inbox`` is what the "My task inbox" badge counts (``inbox.ts``
    ``inboxCount``): the INBOX rows that do not wait for their date, only.
    Changed 2026-11-03 by D170 (INB-1): the inbox holds only what I captured,
    so an assigned, untriaged board row is no longer in ``inbox``. It counts
    in ``new`` (:func:`new_work`) and in its own chip by its derived
    disposition. ``next`` is
    NEXT and assigned to me. ``waiting``, ``someday`` and ``done`` are the
    effective disposition. ``today`` is :func:`in_today`. TRASH is in no chip.
    The disposition is ``effective_disposition``, the one rule (D77).
    """
    from gateway.routes.projects.personal import effective_disposition, not_yet

    counts = dict.fromkeys(DISPOSITION_KEYS, 0)
    for row in rows:
        mine = bool(row.is_mine)
        effective = effective_disposition(
            row.p_disposition, status_category=row.category,
            is_mine=mine, has_assignee=bool(row.has_assignee),
        )
        if effective == "INBOX" and not not_yet(row.p_defer_until, row.start_date, at, today):
            counts["inbox"] += 1
        elif effective == "NEXT" and mine:
            counts["next"] += 1
        elif effective == "WAITING":
            counts["waiting"] += 1
        elif effective == "SOMEDAY":
            counts["someday"] += 1
        elif effective == "DONE":
            counts["done"] += 1
        if new_work(
            row.p_disposition, on_board=bool(getattr(row, "on_board", True)),
            is_mine=mine, effective=effective,
            ready_agent=_ready_from_row(row),
        ):
            counts["new"] += 1
        if in_today(
            effective=effective, is_mine=mine, completed_at=row.completed_at,
            defer_until=row.p_defer_until, start_date=row.start_date,
            due_at=row.due_at, today=today, zone=zone,
        ):
            counts["today"] += 1
    return counts


def fold_mine_summary(rows: list[Any], nodes: dict[str, Any]) -> dict[str, Any]:
    """Totals, the child rows and ``own``, from the rows and the NAMEABLE nodes.

    A child is the SPACE a task sits in: its root, when the member may see
    the root. When the member may not, the child is the task's own project,
    when the member may see that. A task in neither sits in ``own``, which
    names no project. So no row names a project the member cannot open
    (audit P1-8), and ``own.tasks + sum(children.tasks) == tasks`` holds, the
    invariant of ``tree.fold_node_summary``.
    """
    totals = _empty_block()
    own = _empty_block()
    per_child: dict[str, dict[str, Any]] = {}
    for row in rows:
        category = str(row.category or "todo")
        overdue, soon = bool(row.is_overdue), bool(row.is_due_soon)
        _add(totals, category, overdue, soon)
        root, pid = str(row.root_project_id or ""), str(row.project_id or "")
        key = root if root in nodes else pid if pid in nodes else None
        _add(per_child.setdefault(key, _empty_block()) if key else own, category, overdue, soon)
    children = []
    for key, block in per_child.items():
        node = nodes[key]
        children.append({
            "id": key,
            "name": node.name,
            "kind": node_kind(getattr(node, "kind", None)),
            "status": node.status,
            "archived": node.archived_at is not None,
            "icon": getattr(node, "icon", None),
            "icon_slot": getattr(node, "icon_slot", None),
            "personal": bool(getattr(node, "personal_owner", None)),
            **block,
        })
    children.sort(key=lambda c: (-c["tasks"], str(c["name"] or "").lower(), c["id"]))
    projects = {
        str(row.project_id) for row in rows
        if str(row.project_id) in nodes
        and node_kind(getattr(nodes[str(row.project_id)], "kind", None)) == "project"
    }
    return {
        **{k: totals[k] for k in ("tasks", "overdue", "due_soon", "by_category")},
        "projects": len(projects),
        "own": own,
        "children": children,
    }


@router.get("/my/summary")
async def my_summary(user: UserContext = Depends(get_current_user)) -> dict[str, Any]:
    """The Overview of My tasks: the ``NodeSummary`` shape, over mine.

    ``tasks``, ``overdue``, ``due_soon`` and ``by_category`` count the rows
    that ``GET /projects/tasks?scope=mine`` lists with no other filter.
    ``children`` are the spaces my tasks sit in, one row each with its own
    counts. ``by_disposition`` holds the chip counts, so the client does not
    count a page. Archived projects are out. No row names a project the
    member cannot see. The member is the session's, and the route takes no
    parameter.
    """
    from gateway.routes.projects.personal import local_date, stored_zone, zone_name

    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        where, params = mine_where(vis)
        params = {
            **vis.params,
            **params,
            **await mine_binds(db, vis.email),
            "closed": sorted(CLOSING_CATEGORIES),
            "soon_days": DUE_SOON_DAYS,
            "day_start": await reader_day_start(db, vis.email),
        }
        rows = list((await db.execute(text(_summary_sql(where)), params)).fetchall())
        node_ids = sorted({
            str(v) for r in rows for v in (r.root_project_id, r.project_id) if v is not None
        })
        nodes: dict[str, Any] = {}
        if node_ids:
            found = (await db.execute(
                text(_projects_sql(vis)), {**vis.params, "node_ids": node_ids},
            )).fetchall()
            nodes = {str(n.id): n for n in found}
        clock = (await db.execute(text("SELECT now()"))).scalar() or datetime.now(UTC)
        zone = zone_name(await stored_zone(db, vis.email))

    today = local_date(zone, clock)
    return {
        "id": "mine",
        "name": "My tasks",
        "level": "mine",
        **fold_mine_summary(rows, nodes),
        "due_soon_days": DUE_SOON_DAYS,
        "by_disposition": fold_dispositions(rows, at=clock, today=today, zone=zone),
        "today": today.isoformat(),
        # The zone the Today count used (`stored_zone`, else UTC). The client
        # reads Today in this zone too, so a chip and its count agree when the
        # browser sits in another zone (WS-64 OP-11 fix round, item 8).
        "time_zone": zone,
    }


__all__ = [
    "DISPOSITION_KEYS",
    "DUE_SOON_DAYS",
    "SCOPE_MINE",
    "attach_my_overlay",
    "check_scope",
    "fold_dispositions",
    "fold_mine_summary",
    "in_today",
    "mine_scope",
    "mine_where",
    "my_summary",
]
