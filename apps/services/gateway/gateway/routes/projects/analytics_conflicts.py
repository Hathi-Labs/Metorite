"""Projects · conflicts — where planned work interferes (WS-27bm S7c).

Spec: ``project-docs/specs/projects_ai_chat.md`` §13.2, §13.5, §10.5.

    GET /projects/analytics/conflicts?project_id=&include_subtree=&horizon_days=

One list of rows, each with a ``kind``, the ``task_ids`` and ``people`` it
names, one ``sentence`` that says why, and a ``severity``. Seven kinds and no
eighth (:data:`gateway.conflicts.KINDS`). The Analytics app's Conflicts panel,
the report section ``conflicts`` and the chat tool ``find_conflicts`` all read
:func:`conflicts_body`, so the three cannot disagree.

**The rules are not here.** The dependency rule, the parallel-work day, the
severity, the sort and the cap are :mod:`gateway.conflicts`, a pure leaf
module. The HR kinds are the S7b warning predicates
(:func:`~gateway.routes.projects.candidates.warning_kinds`) and the S7a walk
(:func:`gateway.capacity.person_capacity`). This module fetches rows and
builds sentences.

Where each kind reads from:

* ``dependency_order`` and ``blocker_late`` — ``blocks`` links only, from
  ``source_task_id`` (the blocker) to ``target_task_id``. The blocked task is
  open and in scope (``analytics.load_open_where``). The blocker passes
  ``task_visibility_clause``, is not archived and is open, in ANY project. A
  blocker in a stopped project still counts (owner, 2026-09-24). A blocker the
  viewer cannot see gives no row, and no count says one was dropped (§13.5
  rule 4). A late blocker gives ``blocker_late`` and never also
  ``dependency_order`` (rule 5). Neither kind reads the horizon.
* ``parallel_person`` — every open task the caller can see that has both
  dates, per person, agents left out (rule 6).
* ``overcommitted`` — ``person_capacity`` over every open task the caller can
  see, then the at-risk tasks IN SCOPE (rule 8).
* ``absent_on_due``, ``leaving``, ``over_concurrency`` — the S7b predicates,
  over the open tasks in scope that fall due inside the horizon or are already
  overdue (rules 7 and 10).

⚠️ **No HR grant, no HR kinds** (rule 9). A caller without
``admin:members:read`` gets ``hr_visible: false``, and none of the four HR
kinds appears anywhere in the body, not even as a zero in ``by_kind``.

Read-only, and no migration.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends
from gateway.capacity import (
    absences_for,
    dated_until,
    horizon_window,
    person_capacity,
)
from gateway.conflicts import (
    HR_KINDS,
    KINDS,
    PARALLEL_MAX_TASKS,
    busiest_parallel_day,
    conflict_row,
    dependency_conflict,
    interval,
    summarise,
    utc_day,
)
from gateway.routes.projects.analytics import (
    load_open_where,
    load_params,
    scope_clause,
)
from gateway.routes.projects.analytics_capacity import (
    _PEOPLE_SQL,
    capacity_dated_sql,
    capacity_totals_sql,
    check_horizon,
)
from gateway.routes.projects.candidates import (
    away_warning,
    concurrency_warning,
    leaving_warning,
)
from gateway.routes.projects.core import (
    STARTED_CATEGORY,
    _tenant_session,
    resolve_visibility,
    router,
    task_visibility_clause,
)
from gateway.routes.tasks.core import can_read_hr_fields
from gateway.workload import HORIZON_DAYS
from sqlalchemy import text


def dependency_sql(open_where: str, blocker_visible: str) -> str:
    """Every open ``blocks`` pair whose blocked task is in scope.

    ``t`` and ``s`` are the BLOCKED task and its status, so Load's predicate
    applies to it unchanged. ``k`` is the blocker, bounded by the caller's
    grants and nothing about projects (§13.5 rule 4). ``blocker_overdue`` is
    Load's overdue predicate, ``due_at < now()`` (rule 5).
    """
    return (
        f"SELECT t.id AS blocked_id, t.title AS blocked_title,"
        f"       t.start_date AS blocked_start, t.due_at AS blocked_due,"
        f"       k.id AS blocker_id, k.title AS blocker_title,"
        f"       k.start_date AS blocker_start, k.due_at AS blocker_due,"
        f"       k.completed_at AS blocker_completed,"
        f"       (k.due_at IS NOT NULL AND k.due_at < now()) AS blocker_overdue"
        f"  FROM pm_task_links l"
        f"  JOIN pm_tasks t ON t.id = l.target_task_id"
        f"  JOIN pm_task_statuses s ON s.id = t.status_id"
        f"  JOIN pm_tasks k ON k.id = l.source_task_id"
        f"  JOIN pm_task_statuses ks ON ks.id = k.status_id"
        f" WHERE l.link_type = 'blocks'"
        f"   AND {open_where}"
        f"   AND k.archived_at IS NULL"
        f"   AND ({blocker_visible})"
        f"   AND ks.category <> ALL(CAST(:closed AS text[]))"
        f" ORDER BY t.id, k.id"
    )


def parallel_sql(open_where: str, scope_sql: str) -> str:
    """Each person's open, visible, fully dated tasks that touch the window.

    ``open_where`` is Load's predicate at the PORTFOLIO scope: rule 6 counts
    over all the work the caller can see, because a project scope holds one
    root and the kind could never fire there. ``in_scope`` marks the tasks of
    THIS scope. The day bounds are loose on purpose, and
    :func:`gateway.conflicts.parallel_days` applies the exact rule.
    """
    due_day = "(t.due_at AT TIME ZONE 'UTC')::date"
    return (
        f"SELECT lower(a.assignee) AS who, t.id, t.title, t.start_date,"
        f"       t.due_at, t.root_project_id, ({scope_sql}) AS in_scope"
        f"  FROM pm_tasks t"
        f"  JOIN pm_task_statuses s ON s.id = t.status_id"
        f"  JOIN pm_task_assignees a ON a.task_id = t.id"
        f" WHERE {open_where}"
        f"   AND t.start_date IS NOT NULL AND t.due_at IS NOT NULL"
        f"   AND lower(a.assignee) NOT LIKE 'agent:%'"
        f"   AND LEAST(t.start_date, {due_day}) <= CAST(:window_end AS date)"
        f"   AND GREATEST(t.start_date, {due_day}) >= CAST(:window_start AS date)"
    )


def scoped_tasks_sql(open_where: str) -> str:
    """The open tasks in scope, one row per assignee (the HR kinds' input)."""
    return (
        f"SELECT t.id, t.title, t.due_at, s.category, lower(a.assignee) AS who"
        f"  FROM pm_tasks t"
        f"  JOIN pm_task_statuses s ON s.id = t.status_id"
        f"  JOIN pm_task_assignees a ON a.task_id = t.id"
        f" WHERE {open_where}"
    )


#: The assignees of the tasks a dependency row names. Both ends already
#: passed the caller's grants, so their people are the caller's to read.
_ASSIGNEES_SQL = (
    "SELECT task_id, lower(assignee) AS who FROM pm_task_assignees"
    " WHERE task_id = ANY(CAST(:ids AS uuid[]))"
    " ORDER BY task_id, lower(assignee)"
)


@router.get("/analytics/conflicts")
async def conflicts(
    project_id: str | None = None,
    include_subtree: bool = True,
    horizon_days: int = HORIZON_DAYS,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Where the plan interferes with itself, in one scope.

    Omit ``project_id`` for the portfolio. ``horizon_days`` (1 to 90, default
    14) bounds the kinds that depend on a date, and the response prints the
    window. The dependency kinds ignore it.
    """
    days = check_horizon(horizon_days)
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        return await conflicts_body(
            db, vis,
            hr_visible=can_read_hr_fields(user),
            project_id=project_id,
            include_subtree=include_subtree,
            horizon_days=days,
        )


def _who(email: str, directory: dict[str, Any]) -> dict[str, Any]:
    person = directory.get(email)
    return {"email": email, "name": (getattr(person, "name", None) or None) if person else None}


def _label(person: dict[str, Any]) -> str:
    return person.get("name") or person.get("email") or "Somebody"


def dependency_rows(
    pairs: list[dict[str, Any]], people_of: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """The two dependency kinds, from fetched pairs (§13.5 rules 2, 4, 5).

    Each pair carries ``blocked`` and ``blocker`` (``id``, ``title``,
    ``start_date``, ``due_at``, and ``completed_at`` on the blocker) and
    ``blocker_overdue``. The SQL already dropped a closed or hidden blocker.
    """
    rows: list[dict[str, Any]] = []
    for pair in pairs:
        blocked, blocker = pair["blocked"], pair["blocker"]
        ids = [str(blocked["id"]), str(blocker["id"])]
        people = people_of.get(ids[0], []) + [
            p for p in people_of.get(ids[1], []) if p not in people_of.get(ids[0], [])
        ]
        if pair.get("blocker_overdue"):
            due = utc_day(blocker.get("due_at"))
            rows.append(conflict_row(
                "blocker_late", task_ids=ids, people=people, due_on=due,
                sentence=(
                    f'"{blocker["title"]}" was due on {due.isoformat() if due else "?"}'
                    f' and is still open, and it blocks "{blocked["title"]}".'
                ),
            ))
        elif dependency_conflict(blocker, blocked):
            before, after = interval(blocker), interval(blocked)
            assert before is not None and after is not None
            rows.append(conflict_row(
                "dependency_order", task_ids=ids, people=people, due_on=after[0],
                sentence=(
                    f'"{blocked["title"]}" is planned from {after[0].isoformat()},'
                    f' before "{blocker["title"]}", which blocks it, is due on'
                    f" {before[1].isoformat()}. Nothing has been rescheduled."
                ),
            ))
    return rows


def parallel_rows(
    by_person: dict[str, list[dict[str, Any]]],
    directory: dict[str, Any],
    window_start: date,
    window_end: date,
) -> list[dict[str, Any]]:
    """``parallel_person``: one row at most for each person (§13.5 rule 6)."""
    rows: list[dict[str, Any]] = []
    for email in sorted(by_person):
        found = busiest_parallel_day(by_person[email], window_start, window_end)
        if found is None:
            continue
        day, tasks = found
        roots = len({str(t.get("root_project_id")) for t in tasks})
        person = _who(email, directory)
        rows.append(conflict_row(
            "parallel_person",
            task_ids=[str(t["id"]) for t in tasks[:PARALLEL_MAX_TASKS]],
            people=[person], due_on=day,
            day=day.isoformat(), tasks_total=len(tasks),
            sentence=(
                f"{_label(person)} holds {len(tasks)} open tasks on"
                f" {day.isoformat()}, in {roots} top-level projects."
            ),
        ))
    return rows


def task_warning_rows(
    task: dict[str, Any],
    person: dict[str, Any],
    helper: dict[str, Any],
    *,
    today: date,
    horizon_end: date,
) -> list[dict[str, Any]]:
    """``absent_on_due`` and ``leaving`` for one task and one holder.

    The S7b predicates decide, so these kinds agree with the picker's
    warnings for the same inputs (§13.5 rule 7). A task due after the horizon
    gives no row. An overdue task always does, because rule 7 checks it
    today (rule 10).
    """
    due = utc_day(task.get("due_at"))
    if due is None or due > horizon_end:
        return []
    rows: list[dict[str, Any]] = []
    for warning in (
        away_warning(helper, due, today=today),
        leaving_warning(helper, due),
    ):
        if warning is None:
            continue
        kind, said = warning
        rows.append(conflict_row(
            kind, task_ids=[str(task["id"])], people=[person], due_on=due,
            sentence=f'{_label(person)} holds "{task["title"]}": {said}.',
        ))
    return rows


def concurrency_row(
    person: dict[str, Any], helper: dict[str, Any], task_ids: list[str],
) -> dict[str, Any] | None:
    """``over_concurrency`` for one person, over all the work the caller can
    see (the S7a ``in_progress`` count), or None."""
    warning = concurrency_warning(helper)
    if warning is None:
        return None
    kind, said = warning
    return conflict_row(
        kind, task_ids=task_ids, people=[person], due_on=None,
        sentence=f"{_label(person)}: {said}.",
    )


def overcommitted_rows(
    person: dict[str, Any], at_risk: list[dict[str, Any]], in_scope: set[str],
) -> list[dict[str, Any]]:
    """``overcommitted``: the at-risk walk's tasks that are in scope (rule 8).

    One row for each task, with its shortfall, in the walk's own order.
    """
    rows: list[dict[str, Any]] = []
    for task in at_risk:
        tid = str(task.get("task_id"))
        if tid not in in_scope:
            continue
        due = utc_day(task.get("due_on"))
        rows.append(conflict_row(
            "overcommitted", task_ids=[tid], people=[person], due_on=due,
            shortfall_hours=task.get("shortfall_hours"),
            needed_hours=task.get("needed_hours"),
            available_hours=task.get("available_hours"),
            sentence=(
                f'{_label(person)} needs {task.get("needed_hours")}h of estimated'
                f' work by {task.get("due_on")} for "{task.get("title")}", and has'
                f' {task.get("available_hours")}h, so {task.get("shortfall_hours")}h'
                " short. The hours include work in other projects."
            ),
        ))
    return rows


async def conflicts_body(
    db: Any,
    vis: Any,
    *,
    hr_visible: bool,
    project_id: str | None,
    include_subtree: bool,
    horizon_days: int = HORIZON_DAYS,
    today: date | None = None,
) -> dict[str, Any]:
    """The conflicts answer, on a session and a visibility the caller resolved.

    Shared by the route and by the report section ``conflicts``.
    ``hr_visible`` is the CALLER's grant, taken as an argument because this
    function has no request to read it from.
    """
    days = check_horizon(horizon_days)
    today = today or date.today()
    _, horizon_end = horizon_window(today, days)
    # 404 for a node the caller cannot see, before anything else is read.
    scope_sql = await scope_clause(db, vis, project_id, include_subtree)
    scope_where = load_open_where(scope_sql, vis)
    params = {**load_params(vis, project_id)}

    # ── The dependency kinds ───────────────────────────────────────────────
    pairs = [
        {
            "blocked": {
                "id": str(r.blocked_id), "title": r.blocked_title,
                "start_date": r.blocked_start, "due_at": r.blocked_due,
            },
            "blocker": {
                "id": str(r.blocker_id), "title": r.blocker_title,
                "start_date": r.blocker_start, "due_at": r.blocker_due,
                "completed_at": r.blocker_completed,
            },
            "blocker_overdue": bool(r.blocker_overdue),
        }
        for r in (await db.execute(
            text(dependency_sql(scope_where, task_visibility_clause(vis, "k"))),
            params,
        )).fetchall()
    ]
    pair_ids = sorted({p[end]["id"] for p in pairs for end in ("blocked", "blocker")})
    assignees: dict[str, list[str]] = {}
    if pair_ids:
        for r in (await db.execute(text(_ASSIGNEES_SQL), {"ids": pair_ids})).fetchall():
            assignees.setdefault(str(r.task_id), []).append(str(r.who))

    # ── parallel_person, over all the work the caller can see ──────────────
    all_where = load_open_where("TRUE", vis)
    by_person: dict[str, list[dict[str, Any]]] = {}
    for r in (await db.execute(
        text(parallel_sql(all_where, scope_sql)),
        {**params, "window_start": today, "window_end": horizon_end},
    )).fetchall():
        by_person.setdefault(str(r.who), []).append({
            "id": str(r.id), "title": r.title, "start_date": r.start_date,
            "due_at": r.due_at, "root_project_id": str(r.root_project_id),
            "in_scope": bool(r.in_scope),
        })

    # ── The HR kinds' input: open tasks in scope, per holder ───────────────
    scoped: list[Any] = []
    if hr_visible:
        scoped = (await db.execute(text(scoped_tasks_sql(scope_where)), params)).fetchall()
    holders = sorted({
        str(r.who) for r in scoped if r.who and not str(r.who).startswith("agent:")
    })

    # ── The directory: names for every caller, the rest for the HR tier ────
    emails = sorted(
        {e for ws in assignees.values() for e in ws if not e.startswith("agent:")}
        | set(by_person) | set(holders)
    )
    directory: dict[str, Any] = {}
    if emails:
        directory = {
            str(r.email): r
            for r in (await db.execute(text(_PEOPLE_SQL), {"emails": emails})).fetchall()
        }
    people_of = {
        tid: [_who(e, directory) for e in ws] for tid, ws in assignees.items()
    }

    rows = dependency_rows(pairs, people_of)
    rows.extend(parallel_rows(by_person, directory, today, horizon_end))
    if hr_visible and holders:
        rows.extend(await _hr_rows(
            db, vis, scoped=scoped, holders=holders, directory=directory,
            today=today, horizon_days=days, horizon_end=horizon_end,
        ))

    kinds = [k for k in KINDS if hr_visible or k not in HR_KINDS]
    return {
        "project_id": project_id,
        "scope": "portfolio" if project_id is None else "node",
        "include_subtree": include_subtree,
        "horizon_days": days,
        "hr_visible": hr_visible,
        "window": {
            "starts_on": today.isoformat(),
            "ends_on": horizon_end.isoformat(),
            "days": days,
            # Said in the payload, so no client has to know it (rule 10).
            "ignored_by": ["dependency_order", "blocker_late"],
        },
        "partial": not bool(getattr(vis, "unrestricted", False)),
        "kinds": kinds,
        **summarise(rows, kinds),
    }


async def _hr_rows(
    db: Any,
    vis: Any,
    *,
    scoped: list[Any],
    holders: list[str],
    directory: dict[str, Any],
    today: date,
    horizon_days: int,
    horizon_end: date,
) -> list[dict[str, Any]]:
    """The four HR kinds. Only called for a caller who holds the grant."""
    from gateway.work_schedule import load_policy, person_schedule

    known = [e for e in holders if e in directory]
    if not known:
        return []
    all_where = load_open_where("TRUE", vis)
    all_params = {
        **load_params(vis, None),
        "holders": known,
        "started_cat": STARTED_CATEGORY,
    }
    totals = {
        str(r.who): r
        for r in (
            await db.execute(text(capacity_totals_sql(all_where)), all_params)
        ).fetchall()
    }
    dated_params = {**all_params, "until": dated_until(today, horizon_days)}
    dated_params.pop("started_cat")
    dated: dict[str, list[dict[str, Any]]] = {}
    for row in (
        await db.execute(text(capacity_dated_sql(all_where)), dated_params)
    ).fetchall():
        dated.setdefault(str(row.who), []).append({
            "id": str(row.id),
            "title": row.title,
            "due_at": row.due_at,
            "estimate_mins": row.estimate_mins,
            "project_name": getattr(row, "project_name", None),
            "_due": utc_day(row.due_at),
        })
    policy = await load_policy(db)
    absences = await absences_for(db, [str(directory[e].id) for e in known])

    tasks_of: dict[str, list[Any]] = {}
    for r in scoped:
        tasks_of.setdefault(str(r.who), []).append(r)
    in_scope = {str(r.id) for r in scoped}

    rows: list[dict[str, Any]] = []
    for email in known:
        record = directory[email]
        person = _who(email, directory)
        spans = absences.get(str(record.id), [])
        work = totals.get(email)
        helper = {
            "spans": spans,
            "end_date": getattr(record, "end_date", None),
            "max_concurrent_tasks": getattr(record, "max_concurrent_tasks", None),
            "in_progress": int(getattr(work, "in_progress", 0) or 0) if work else 0,
        }
        mine = tasks_of.get(email, [])
        m = person_capacity(
            schedule=person_schedule(policy or {}, record), spans=spans,
            totals=work, dated=dated.get(email, []),
            today=today, horizon_days=horizon_days,
        )
        rows.extend(overcommitted_rows(person, m["at_risk"], in_scope))
        for task in mine:
            rows.extend(task_warning_rows(
                {"id": str(task.id), "title": task.title, "due_at": task.due_at},
                person, helper, today=today, horizon_end=horizon_end,
            ))
        over = concurrency_row(
            person, helper,
            [str(t.id) for t in mine if t.category == STARTED_CATEGORY],
        )
        if over is not None:
            rows.append(over)
    return rows
