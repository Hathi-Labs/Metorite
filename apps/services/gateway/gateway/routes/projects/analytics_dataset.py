"""Projects · the chat's dataset read — on-the-fly analysis (WS-27bm S7e).

Spec: ``project-docs/specs/projects_ai_chat.md`` §13.7, §10.7.

    GET /projects/analytics/dataset?project_id=&include_subtree=&state=
        &columns=&limit=&group_by=&measure=&<filters>

The chat tool ``task_dataset`` reads this, and nothing else does. It answers
an UNCOMMON question — cycle time by tag, the share of work in each stage —
in one of two shapes:

* **Rows.** A compact table of at most :data:`MAX_LIMIT` tasks, with the
  columns the caller names from :data:`COLUMNS`. ``total`` and ``truncated``
  travel beside the rows every time, and there is no second page (rule 6).
* **Groups.** With ``group_by`` set, the SERVER groups the full filtered set
  and computes one :data:`MEASURES` figure for each group (owner, O2). No row
  comes back. A median over the capped rows would be a median of the cap.

**The scope is not new** (rule 2). ``state=open`` is Load's predicate,
``analytics.load_open_where``, so its count equals Load's for one scope.
``closed`` and ``all`` are Throughput's, ``analytics.history_where``, with
the archived rows left out.

**The cycle time is not new** (rule 3). ``cycle_hours`` and ``completed_at``
come from ``analytics.cycle_cte_sql``: the first ``in_progress`` to the first
``done`` on the activity spine, over Throughput's widest window
(:data:`CYCLE_WEEKS`). The task row's own ``completed_at`` clears when a task
opens again, so this module never reads it and never subtracts one date from
another. A completion older than the window carries no cycle time, and the
response prints the window.

⚠️ **The HR tier is the CALLER's grant** (owner, O3; §13.2 rule 3). A member
may count tasks per person. ``estimate_sum`` and the two cycle measures per
person need ``admin:members:read``. Without it the group keeps ``key``,
``label`` and ``n``, the value keys are ABSENT, and ``measure_hidden`` says so.

Read-only, and no migration.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import Any
from uuid import UUID

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException, Request
from gateway.routes.projects.analytics import (
    MAX_WEEKS,
    _hours,
    _window,
    cycle_cte_sql,
    history_where,
    load_open_where,
    load_params,
    scope_clause,
    scope_params,
)
from gateway.routes.projects.analytics_capacity import _PEOPLE_SQL
from gateway.routes.projects.analytics_conflicts import _ASSIGNEES_SQL
from gateway.routes.projects.core import (
    CLOSING_CATEGORIES,
    COMPLETED_CATEGORY,
    STARTED_CATEGORY,
    _tenant_session,
    actor,
    resolve_visibility,
    router,
    task_visibility_clause,
    triage_exclusion_clause,
)
from gateway.routes.projects.filters import build_task_filters, parse_when
from gateway.routes.tasks.core import can_read_hr_fields
from sqlalchemy import text

#: The columns a row may carry (rule 4), in the order the table prints them.
COLUMNS: tuple[str, ...] = (
    "number", "full_id", "title", "project", "root_project", "status",
    "status_category", "type", "tags", "assignees", "estimate_mins", "start",
    "due", "completed_at", "created_at", "cycle_hours", "blockers",
)

#: ``open`` is Load's scope. ``closed`` and ``all`` are Throughput's.
STATES: tuple[str, ...] = ("open", "closed", "all")

#: What the server may group by (rule 7, owner O2).
GROUP_BY: tuple[str, ...] = (
    "tag", "status", "status_category", "project", "assignee", "type",
    "created_week", "completed_week",
)

#: What the server may compute for each group (rule 7, owner O2).
MEASURES: tuple[str, ...] = (
    "count", "estimate_sum", "cycle_hours_median", "cycle_hours_p90",
)

#: The measures that are a person's speed or load when grouped by assignee
#: (owner O3). Without the HR grant their value keys are absent.
HR_MEASURES: frozenset[str] = frozenset(
    {"estimate_sum", "cycle_hours_median", "cycle_hours_p90"}
)

#: The cap (rule 6). The default is the chat's; the maximum is the table's.
DEFAULT_LIMIT = 200
MAX_LIMIT = 500

#: The most groups one answer carries. ``groups_total`` counts them all.
MAX_GROUPS = 100

#: The completion window, in weeks: Throughput's widest (``weeks`` ≤ 26).
CYCLE_WEEKS = MAX_WEEKS

#: The filters, by the names ``build_task_filters`` takes (rule 4's
#: paragraph). ``include_archived`` and ``archived_only`` are not here: the
#: scope already decides the archive (rule 2). ``viewer`` is not here: the
#: route takes it from the caller, never from the query (R11).
BOOL_FILTERS: tuple[str, ...] = ("unassigned", "overdue", "watching")
UUID_FILTERS: tuple[str, ...] = ("parent_task_id", "status_id")
INT_FILTERS: tuple[str, ...] = ("importance_gte",)
TEXT_FILTERS: tuple[str, ...] = (
    "status_category", "assignee", "assignees", "q", "tags", "tags_all",
    "due_before",
)
#: The three filters S7e adds. The completion ones read the cycle CTE.
DATE_FILTERS: tuple[str, ...] = (
    "created_after", "completed_after", "completed_before",
)
FILTER_KEYS: tuple[str, ...] = (
    BOOL_FILTERS + UUID_FILTERS + INT_FILTERS + TEXT_FILTERS + DATE_FILTERS
)

#: Every query key the route reads. Any other key is a 422, never ignored:
#: a filter the server silently drops returns the unfiltered set, and the chat
#: then computes a figure over the wrong tasks.
QUERY_KEYS: tuple[str, ...] = (
    "project_id", "include_subtree", "state", "columns", "limit",
    "group_by", "measure", *FILTER_KEYS,
)

_TRUE = frozenset({"true", "1", "yes", "on"})
_FALSE = frozenset({"false", "0", "no", "off"})


def _refuse(detail: str) -> HTTPException:
    return HTTPException(status_code=422, detail=detail)


def _bool(key: str, raw: str) -> bool:
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise _refuse(f"{key} must be true or false.")


def _uuid(key: str, raw: str) -> str:
    try:
        return str(UUID(raw.strip()))
    except (ValueError, AttributeError):
        raise _refuse(f"{key} must be a UUID.") from None


def _int(key: str, raw: str) -> int:
    try:
        return int(raw.strip())
    except (ValueError, AttributeError):
        raise _refuse(f"{key} must be a whole number.") from None


def _csv(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


@dataclass(frozen=True)
class DatasetQuery:
    """One validated dataset request. :func:`parse_query` builds it."""

    project_id: str | None = None
    include_subtree: bool = True
    state: str = "open"
    columns: tuple[str, ...] = COLUMNS
    limit: int = DEFAULT_LIMIT
    group_by: str | None = None
    measure: str = "count"
    filters: dict[str, Any] = field(default_factory=dict)
    dates: dict[str, datetime] = field(default_factory=dict)


def parse_query(items: Iterable[tuple[str, str]]) -> DatasetQuery:
    """The query string as a :class:`DatasetQuery`, or a 422 that names why.

    Pure, so every refusal is tested without a session. The route calls it
    BEFORE a session opens, as ``check_horizon`` does for S7a.
    """
    seen: dict[str, str] = {}
    for key, value in items:
        if key in seen:
            raise _refuse(f"{key} is given twice. Give each key once.")
        seen[key] = value
    unknown = sorted(set(seen) - set(QUERY_KEYS))
    if unknown:
        raise _refuse(
            f"Unknown query key {unknown}. One of: {list(QUERY_KEYS)}."
        )

    project_id = seen.get("project_id", "").strip() or None
    if project_id is not None:
        project_id = _uuid("project_id", project_id)
    state = seen.get("state", "open").strip().lower() or "open"
    if state not in STATES:
        raise _refuse(f"Unknown state '{state}'. One of: {list(STATES)}.")
    group_by, measure = _parse_grouping(seen)
    filters, dates = _parse_filters(seen)
    return DatasetQuery(
        project_id=project_id,
        include_subtree=_bool("include_subtree", seen.get("include_subtree", "true")),
        state=state,
        columns=_parse_columns(seen.get("columns", "")),
        limit=_parse_limit(seen.get("limit", "")),
        group_by=group_by, measure=measure, filters=filters, dates=dates,
    )


def _parse_columns(raw: str) -> tuple[str, ...]:
    """The named columns in the table's own order, or every column."""
    if not raw.strip():
        return COLUMNS
    asked = _csv(raw)
    bad = [c for c in asked if c not in COLUMNS]
    if bad:
        raise _refuse(f"Unknown column {bad}. One of: {list(COLUMNS)}.")
    # The table's order, once each, so two requests for one set of columns
    # print one header.
    return tuple(c for c in COLUMNS if c in asked)


def _parse_limit(raw: str) -> int:
    limit = _int("limit", raw) if raw.strip() else DEFAULT_LIMIT
    if not 1 <= limit <= MAX_LIMIT:
        raise _refuse(
            f"limit must be between 1 and {MAX_LIMIT}. There is no second page."
        )
    return limit


def _parse_grouping(seen: dict[str, str]) -> tuple[str | None, str]:
    group_by = seen.get("group_by", "").strip().lower() or None
    if group_by is not None and group_by not in GROUP_BY:
        raise _refuse(f"Unknown group_by '{group_by}'. One of: {list(GROUP_BY)}.")
    measure_raw = seen.get("measure", "").strip().lower()
    measure = measure_raw or "count"
    if measure not in MEASURES:
        raise _refuse(f"Unknown measure '{measure}'. One of: {list(MEASURES)}.")
    if measure_raw and group_by is None:
        raise _refuse(
            "measure needs group_by. For one figure over the whole scope, use "
            "the analytics reads (throughput has the median and the p90)."
        )
    return group_by, measure


def _parse_filters(seen: dict[str, str]) -> tuple[dict[str, Any], dict[str, datetime]]:
    """Each filter as the type ``build_task_filters`` takes, or a 422."""
    given = {k: v for k, v in seen.items() if k in FILTER_KEYS and v.strip()}
    filters: dict[str, Any] = {}
    dates: dict[str, datetime] = {}
    for key, raw in given.items():
        if key in BOOL_FILTERS:
            filters[key] = _bool(key, raw)
        elif key in UUID_FILTERS:
            filters[key] = _uuid(key, raw)
        elif key in INT_FILTERS:
            filters[key] = _int(key, raw)
        elif key in DATE_FILTERS:
            dates[key] = parse_when(raw, field=key)
        else:
            filters[key] = raw.strip()
    return filters, dates


#: The row columns that are a person's speed or load once a row names its
#: assignees, or once the set is filtered to one person (owner O3). Without
#: the HR grant the table drops them. ``assignees`` stays: counts per person
#: are for every member.
HR_ROW_COLUMNS: tuple[str, ...] = ("estimate_mins", "cycle_hours")

#: The filters that narrow the set to named people.
ASSIGNEE_FILTERS: tuple[str, ...] = ("assignee", "assignees")


#: The fewest distinct people a group must hold before a caller without the
#: HR grant sees its estimate or cycle measure (fix round 2). A tag only Ana
#: uses, or a project only Ana works in, is Ana's speed under another name.
#: With three, no one member of the group reads another's figure by taking
#: their own out.
MIN_GROUP_PEOPLE = 3


def per_person(query: DatasetQuery) -> bool:
    """Is this GROUPED request tied to named people (owner O3)?

    True when the groups are people, or when a filter narrows the set to
    named people. The row table does not ask: its gate is unconditional
    (:func:`hr_gate`).
    """
    return (
        query.group_by == "assignee"
        or any(query.filters.get(k) for k in ASSIGNEE_FILTERS)
    )


def hr_gate(query: DatasetQuery, hr_visible: bool) -> tuple[DatasetQuery, list[str]]:
    """The query the caller may run, and the row columns it lost (O3).

    ⚠️ **Without the HR grant a row NEVER carries these columns, whatever
    else the request names** (fix round 2). A rule that dropped them only
    beside ``assignees`` was one join from useless: a call for
    ``full_id,cycle_hours`` and a call for ``full_id,assignees``, joined on
    the task, give each person's speed. Cycle time by tag or by stage stays
    as a server group, behind :data:`MIN_GROUP_PEOPLE`.
    """
    if hr_visible:
        return query, []
    hidden = [c for c in HR_ROW_COLUMNS if c in query.columns]
    if not hidden:
        return query, []
    kept = tuple(c for c in query.columns if c not in hidden)
    return replace(query, columns=kept), hidden



# ── The SQL ─────────────────────────────────────────────────────────────────


def state_where(state: str, scope_sql: str, vis: Any) -> str:
    """The task set for one state (rule 2). ``s`` is the task's status row.

    ``open`` is Load's predicate, unchanged. ``closed`` and ``all`` are
    Throughput's, with the archived rows left out: this table lists live
    tasks, and an archived task is on no board.
    """
    if state == "open":
        return load_open_where(scope_sql, vis)
    where = f"{history_where(scope_sql, vis)} AND t.archived_at IS NULL"
    if state == "closed":
        where += " AND s.category = ANY(CAST(:closed AS text[]))"
    return where


def state_params(state: str, vis: Any, project_id: str | None) -> dict[str, Any]:
    """The binds :func:`state_where` names, and the cycle CTE's."""
    if state == "open":
        base = load_params(vis, project_id)
    else:
        base = {**vis.params, **scope_params(project_id)}
        if state == "closed":
            base["closed"] = sorted(CLOSING_CATEGORIES)
    return {
        **base,
        "weeks": CYCLE_WEEKS,
        "closing": sorted(CLOSING_CATEGORIES),
        "done_cat": COMPLETED_CATEGORY,
        "started_cat": STARTED_CATEGORY,
    }


def filter_clauses(
    query: DatasetQuery, viewer: str,
) -> tuple[list[str], dict[str, Any]]:
    """The filter fragments: ``build_task_filters``, plus the three dates.

    ``include_archived=True`` tells the builder to add no archive clause of
    its own, because the scope already decides the archive. The completion
    filters read ``c``, the cycle CTE, so "completed" means one thing here.
    """
    f = query.filters
    clauses, params = build_task_filters(
        parent_task_id=f.get("parent_task_id"),
        status_id=f.get("status_id"),
        status_category=f.get("status_category"),
        assignee=f.get("assignee"),
        assignees=f.get("assignees"),
        unassigned=bool(f.get("unassigned")),
        overdue=bool(f.get("overdue")),
        due_before=f.get("due_before"),
        importance_gte=f.get("importance_gte"),
        q=f.get("q"),
        tags=f.get("tags"),
        tags_all=f.get("tags_all"),
        include_archived=True,
        watching=bool(f.get("watching")),
        viewer=viewer if f.get("watching") else None,
    )
    d = query.dates
    if "created_after" in d:
        clauses.append("t.created_at >= :ds_created_after")
        params["ds_created_after"] = d["created_after"]
    if "completed_after" in d:
        clauses.append("c.to_category = :done_cat AND c.finished_at >= :ds_completed_after")
        params["ds_completed_after"] = d["completed_after"]
    if "completed_before" in d:
        clauses.append("c.to_category = :done_cat AND c.finished_at < :ds_completed_before")
        params["ds_completed_before"] = d["completed_before"]
    return clauses, params


def dataset_cte_sql(history: str, where: str) -> str:
    """The cycle CTE, then ``ds``: one row for each task in the set.

    ``history`` is Throughput's scope, so the completions are the ones
    Throughput counts. A completion counts only when it reached ``done``: a
    cancellation is not a finish, the ruling ``_CYCLE_MEASURES`` makes.
    """
    return (
        f"{cycle_cte_sql(history)}"
        f", ds AS ("
        f"  SELECT t.id, t.task_number, t.title, t.project_id,"
        f"         t.root_project_id, t.type_id, t.tags, t.estimate_mins,"
        f"         t.start_date, t.due_at, t.created_at,"
        f"         s.name AS status_name, s.category AS status_category,"
        f"         CASE WHEN c.to_category = :done_cat"
        f"              THEN c.finished_at END AS completed_at,"
        f"         CASE WHEN c.to_category = :done_cat"
        f"              THEN c.hours END AS cycle_hours"
        f"    FROM pm_tasks t"
        f"    JOIN pm_task_statuses s ON s.id = t.status_id"
        f"    LEFT JOIN cyc c ON c.task_id = t.id"
        f"   WHERE {where}"
        f")"
    )


def rows_sql(history: str, where: str) -> str:
    """The capped rows, in a stable order, with the full count beside them."""
    return (
        f"{dataset_cte_sql(history, where)}"
        f" SELECT ds.*, count(*) OVER () AS total_rows"
        f"   FROM ds"
        f"  ORDER BY ds.created_at, ds.id"
        f"  LIMIT :ds_limit"
    )


#: ``group_by`` → (the key expression, the join it needs).
_GROUP_KEYS: dict[str, tuple[str, str]] = {
    "tag": ("g.tag", " LEFT JOIN LATERAL unnest(ds.tags) AS g(tag) ON TRUE"),
    "status": ("ds.status_name", ""),
    "status_category": ("ds.status_category", ""),
    "project": ("ds.project_id::text", ""),
    "assignee": (
        "lower(ga.assignee)", " LEFT JOIN pm_task_assignees ga ON ga.task_id = ds.id",
    ),
    "type": ("ds.type_id::text", ""),
    "created_week": (
        "to_char(date_trunc('week', ds.created_at AT TIME ZONE 'UTC'), 'YYYY-MM-DD')", "",
    ),
    "completed_week": (
        "to_char(date_trunc('week', ds.completed_at AT TIME ZONE 'UTC'), 'YYYY-MM-DD')", "",
    ),
}

#: ``measure`` → (the value, the count of tasks the value had a figure for).
#: ⚠️ ``percentile_cont`` ignores nulls, so a task with no cycle time stays
#: out of the median instead of pulling it to zero. Never a mean (rule 3).
_MEASURE_SQL: dict[str, tuple[str, str]] = {
    "count": ("count(DISTINCT ds.id)", "count(DISTINCT ds.id)"),
    "estimate_sum": ("coalesce(sum(ds.estimate_mins), 0)", "count(ds.estimate_mins)"),
    "cycle_hours_median": (
        "percentile_cont(0.5) WITHIN GROUP (ORDER BY ds.cycle_hours)",
        "count(ds.cycle_hours)",
    ),
    "cycle_hours_p90": (
        "percentile_cont(0.9) WITHIN GROUP (ORDER BY ds.cycle_hours)",
        "count(ds.cycle_hours)",
    ),
}


def groups_sql(history: str, where: str, group_by: str, measure: str | None) -> str:
    """One row for each group over the FULL set, never over the capped rows.

    ``measure`` None computes the count only: the caller may not see the
    value, so the server does not compute it.
    """
    key, join = _GROUP_KEYS[group_by]
    value, measured = _MEASURE_SQL[measure] if measure else ("NULL", "NULL")
    # A week is an axis, so it reads in date order. Every other group reads
    # largest first, because the top line is the one a member acts on.
    order = (
        "k NULLS LAST" if group_by.endswith("_week")
        else "v DESC NULLS LAST, n DESC, k NULLS LAST"
    )
    # ONE statement, so the cycle CTE runs once. `keyed` is one row for
    # each (task, group key). `owners` counts the distinct PEOPLE in each
    # group, agents left out, and the K rule reads it. The LIMIT is in SQL,
    # and `groups_total` is the window count taken before it. `tally` is
    # the task count over the same `ds`.
    return (
        f"{dataset_cte_sql(history, where)}"
        f", keyed AS (SELECT {key} AS k, ds.* FROM ds{join})"
        f", owners AS ("
        f"  SELECT kd.k, count(DISTINCT lower(pa.assignee)) AS people"
        f"    FROM keyed kd"
        f"    JOIN pm_task_assignees pa ON pa.task_id = kd.id"
        f"   WHERE lower(pa.assignee) NOT LIKE 'agent:%'"
        f"   GROUP BY kd.k"
        f"), tally AS (SELECT count(*) AS total FROM ds)"
        f" SELECT ds.k AS k, count(DISTINCT ds.id) AS n,"
        f"        {value} AS v, {measured} AS m,"
        f"        coalesce(max(o.people), 0) AS people,"
        f"        count(*) OVER () AS groups_total,"
        f"        (SELECT total FROM tally) AS total"
        f"   FROM keyed ds"
        f"   LEFT JOIN owners o ON o.k IS NOT DISTINCT FROM ds.k"
        f"  GROUP BY ds.k"
        f"  ORDER BY {order}"
        f"  LIMIT :ds_groups"
    )


def blockers_sql(vis: Any) -> str:
    """The open ``blocks`` blockers of the listed tasks (§13.5 rule 4).

    The blocker passes the caller's grants and is not archived, in any
    project. A blocker the caller cannot see gives nothing, and nothing says
    one was dropped. A parked triage task names itself nowhere, and a closed
    blocker blocks nothing, as ``analytics_conflicts.dependency_sql`` rules.
    """
    return (
        f"SELECT l.target_task_id AS blocked_id, k.id, k.task_number, k.title"
        f"  FROM pm_task_links l"
        f"  JOIN pm_tasks k ON k.id = l.source_task_id"
        f"  JOIN pm_task_statuses ks ON ks.id = k.status_id"
        f" WHERE l.link_type = 'blocks'"
        f"   AND l.target_task_id = ANY(CAST(:ids AS uuid[]))"
        f"   AND k.archived_at IS NULL"
        f"   AND ({task_visibility_clause(vis, 'k')})"
        f"   AND ({triage_exclusion_clause('k')})"
        f"   AND ks.category <> ALL(CAST(:closed AS text[]))"
        f" ORDER BY l.target_task_id, k.task_number, k.id"
    )


def project_names_sql(vis: Any) -> str:
    """Project names, for the projects the CALLER may see and no others.

    ⚠️ A task is visible through its assignee arm even when its project is
    not (``task_visibility_clause``). Its row must not print that project's
    name, so the lookup carries the project grant, and a hidden project
    gives no name (fix round 2).
    """
    return (
        "SELECT id, name FROM pm_projects"
        " WHERE id = ANY(CAST(:ids AS uuid[]))"
        f"   AND {vis.project_clause('id')}"
    )
_TYPE_NAMES_SQL = (
    "SELECT id, name FROM pm_task_types WHERE id = ANY(CAST(:ids AS uuid[]))"
)


# ── The route ───────────────────────────────────────────────────────────────


@router.get("/analytics/dataset")
async def dataset(
    request: Request,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """A compact table of tasks in one scope, or the server's groups over it.

    Omit ``project_id`` for the portfolio. ``state`` is open (default),
    closed or all. ``columns`` picks from the allowlist, and ``limit`` is 1
    to 500 (default 200). ``group_by`` with ``measure`` returns groups and no
    rows. Every other key is a filter from ``build_task_filters``, or
    ``created_after``, ``completed_after`` or ``completed_before``. An unknown
    key is a 422.
    """
    query = parse_query(request.query_params.multi_items())
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        return await dataset_body(
            db, vis, query,
            hr_visible=can_read_hr_fields(user),
            viewer=actor(user).lower(),
        )


def _iso(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


async def _names(
    db: Any, sql: str, ids: set[str], binds: dict[str, Any] | None = None,
) -> dict[str, str]:
    if not ids:
        return {}
    rows = (await db.execute(text(sql), {**(binds or {}), "ids": sorted(ids)})).fetchall()
    return {str(r.id): str(r.name or "") for r in rows}


async def dataset_body(
    db: Any,
    vis: Any,
    query: DatasetQuery,
    *,
    hr_visible: bool,
    viewer: str = "",
) -> dict[str, Any]:
    """The dataset answer, on a session and a visibility the caller resolved.

    ``hr_visible`` is the CALLER's grant, taken as an argument because this
    function has no request to read it from.
    """
    # 404 for a node the caller cannot see, before anything else is read.
    scope_sql = await scope_clause(db, vis, query.project_id, query.include_subtree)
    history = history_where(scope_sql, vis)
    where_parts = [state_where(query.state, scope_sql, vis)]
    params = state_params(query.state, vis, query.project_id)
    extra, extra_params = filter_clauses(query, viewer)
    where_parts.extend(extra)
    params.update(extra_params)
    where = " AND ".join(f"({part})" for part in where_parts)

    since = (await db.execute(
        text(f"SELECT (({_window(False)[0]}) AT TIME ZONE 'UTC')::date AS d"),
        {"weeks": CYCLE_WEEKS},
    )).scalar()
    head: dict[str, Any] = {
        "project_id": query.project_id,
        "scope": "portfolio" if query.project_id is None else "node",
        "include_subtree": query.include_subtree,
        "state": query.state,
        "hr_visible": hr_visible,
        # Said in the payload, so the chat can say it (rule 3).
        "cycle_window": {
            "weeks": CYCLE_WEEKS,
            "starts_on": _iso(since),
            "basis": "first in_progress to first done, on the activity spine",
        },
    }
    if query.group_by is not None:
        return {**head, **await _grouped(db, vis, history, where, params, query, hr_visible)}
    query, hidden_columns = hr_gate(query, hr_visible)
    body = await _tabled(db, vis, history, where, params, query)
    # Always present in the table shape, so a client never has to guess
    # whether a missing column was withheld or never asked for.
    return {**head, **body, "hidden_columns": hidden_columns}


async def _grouped(
    db: Any,
    vis: Any,
    history: str,
    where: str,
    params: dict[str, Any],
    query: DatasetQuery,
    hr_visible: bool,
) -> dict[str, Any]:
    """The groups (rule 7). The value keys are absent when O3 hides them."""
    assert query.group_by is not None
    gated = query.measure in HR_MEASURES and not hr_visible
    # Grouped by person, or filtered to named people: every value is a
    # person's estimate or speed (O3), so the server does not compute it.
    hidden = gated and per_person(query)
    rows = (await db.execute(
        text(groups_sql(history, where, query.group_by, None if hidden else query.measure)),
        {**params, "ds_groups": MAX_GROUPS + 1},
    )).fetchall()
    # No group means no task, because every group key join is a LEFT join.
    total = int(rows[0].total) if rows else 0
    groups_total = int(rows[0].groups_total) if rows else 0
    rows = rows[:MAX_GROUPS]

    keys = [None if r.k is None else str(r.k) for r in rows]
    labels: dict[str, str] = {}
    if query.group_by == "project":
        labels = await _names(
            db, project_names_sql(vis), {k for k in keys if k}, vis.params,
        )
    elif query.group_by == "type":
        labels = await _names(db, _TYPE_NAMES_SQL, {k for k in keys if k})
    elif query.group_by == "assignee":
        people = {k for k in keys if k and not k.startswith("agent:")}
        if people:
            found = (await db.execute(text(_PEOPLE_SQL), {"emails": sorted(people)})).fetchall()
            labels = {str(r.email): str(r.name) for r in found if r.name}

    groups: list[dict[str, Any]] = []
    for row, key in zip(rows, keys, strict=True):
        group: dict[str, Any] = {
            "key": key,
            "label": _label(query.group_by, key, labels),
            "n": int(row.n or 0),
        }
        if query.group_by == "assignee" and key and key.startswith("agent:"):
            group["agent"] = True
        if hidden:
            pass
        elif gated and int(row.people or 0) < MIN_GROUP_PEOPLE:
            # Fewer than K people: one person's figure under another name.
            # `n` stays, because a count is for every member.
            group["measure_hidden"] = True
        else:
            group["value"] = _value(query.measure, row.v)
            group["measured"] = int(row.m or 0)
        groups.append(group)

    out: dict[str, Any] = {
        "group_by": query.group_by,
        "measure": query.measure,
        "total": total,
        "groups_total": groups_total,
        "groups_truncated": groups_total > MAX_GROUPS,
        "groups": groups,
        # The server computed every figure here, over the full set (O2).
        "basis": "server",
    }
    if hidden:
        out["measure_hidden"] = True
    return out


def _value(measure: str, raw: Any) -> Any:
    if measure in ("cycle_hours_median", "cycle_hours_p90"):
        return _hours(raw)
    return int(raw or 0)


#: What a group with no key is, said in words.
_NO_KEY: dict[str, str] = {
    "tag": "no tag",
    "assignee": "unassigned",
    "type": "no type",
    "project": "no project",
    "completed_week": "not completed in the window",
}


def _label(group_by: str, key: str | None, labels: dict[str, str]) -> str:
    if key is None:
        return _NO_KEY.get(group_by, "none")
    return labels.get(key) or key


@dataclass
class _Lookups:
    """The names and lists a row's cells read, fetched once for the page."""

    projects: dict[str, str] = field(default_factory=dict)
    types: dict[str, str] = field(default_factory=dict)
    assignees: dict[str, list[str]] = field(default_factory=dict)
    blockers: dict[str, list[dict[str, Any]]] = field(default_factory=dict)


def _opt(names: dict[str, str], value: Any) -> str | None:
    return names.get(str(value)) if value else None


#: One reader for each column. ``r`` is a ``ds`` row, ``lk`` the lookups.
_CELLS: dict[str, Callable[[Any, _Lookups], Any]] = {
    "number": lambda r, lk: r.task_number,
    "full_id": lambda r, lk: str(r.id),
    "title": lambda r, lk: r.title,
    "project": lambda r, lk: _opt(lk.projects, r.project_id),
    "root_project": lambda r, lk: _opt(lk.projects, r.root_project_id),
    "status": lambda r, lk: r.status_name,
    "status_category": lambda r, lk: r.status_category,
    "type": lambda r, lk: _opt(lk.types, r.type_id),
    "tags": lambda r, lk: list(r.tags or []),
    "assignees": lambda r, lk: lk.assignees.get(str(r.id), []),
    "estimate_mins": lambda r, lk: r.estimate_mins,
    "start": lambda r, lk: _iso(r.start_date),
    "due": lambda r, lk: _iso(r.due_at),
    "completed_at": lambda r, lk: _iso(r.completed_at),
    "created_at": lambda r, lk: _iso(r.created_at),
    "cycle_hours": lambda r, lk: _hours(r.cycle_hours),
    "blockers": lambda r, lk: lk.blockers.get(str(r.id), []),
}


async def _lookups(db: Any, vis: Any, fetched: list[Any], cols: set[str]) -> _Lookups:
    """Only the reads the named columns need, each one query for the page."""
    lk = _Lookups()
    ids = [str(r.id) for r in fetched]
    if not ids:
        return lk
    if cols & {"project", "root_project"}:
        lk.projects = await _names(db, project_names_sql(vis), {
            str(v) for r in fetched for v in (r.project_id, r.root_project_id) if v
        }, vis.params)
    if "type" in cols:
        lk.types = await _names(
            db, _TYPE_NAMES_SQL, {str(r.type_id) for r in fetched if r.type_id},
        )
    if "assignees" in cols:
        for r in (await db.execute(text(_ASSIGNEES_SQL), {"ids": ids})).fetchall():
            lk.assignees.setdefault(str(r.task_id), []).append(str(r.who))
    if "blockers" in cols:
        for r in (await db.execute(
            text(blockers_sql(vis)),
            {**vis.params, "ids": ids, "closed": sorted(CLOSING_CATEGORIES)},
        )).fetchall():
            lk.blockers.setdefault(str(r.blocked_id), []).append(
                {"id": str(r.id), "number": r.task_number, "title": r.title}
            )
    return lk


async def _tabled(
    db: Any,
    vis: Any,
    history: str,
    where: str,
    params: dict[str, Any],
    query: DatasetQuery,
) -> dict[str, Any]:
    """The capped rows (rules 4, 5 and 6)."""
    fetched = (await db.execute(
        text(rows_sql(history, where)), {**params, "ds_limit": query.limit},
    )).fetchall()
    # The window count rides on every row. The limit is at least 1, so no row
    # means an empty set, and 0 is the count.
    total = int(fetched[0].total_rows) if fetched else 0
    lk = await _lookups(db, vis, fetched, set(query.columns))
    rows = [{col: _CELLS[col](r, lk) for col in query.columns} for r in fetched]
    return {
        "columns": list(query.columns),
        "limit": query.limit,
        "total": total,
        "truncated": total > len(rows),
        "rows": rows,
    }
