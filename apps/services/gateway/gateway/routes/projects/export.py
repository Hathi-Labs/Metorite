"""Projects · the filtered-list CSV export (WS-27ae, P-26).

Spec: ``project-docs/specs/project_management_app.md`` §9.2 (WS-27ae, the export
third) and §11.26 for the as-built record.

    GET /projects/export/tasks.csv?…the list endpoint's filters…&shown_fields=…

⚠️ **The ticket named a pattern that does not exist.** §9.2 says "filtered-list
CSV export on the export-job pattern". There is no export-job pattern in this
repo — no ``export_job`` table, model, queue or endpoint anywhere under
``apps/`` — so this is deliberately **synchronous**: one bounded query, one
``text/csv`` response, no job row, no polling, no worker. Building a job queue
to satisfy a phrase would have been infrastructure nobody asked for, with a
whole new failure surface (orphaned jobs, expiring artefacts, a second place a
tenant boundary has to hold) in exchange for nothing this app can measure.

**It exports what you were looking at, or it exports nothing.**

Three rules, and each one exists because the obvious implementation is wrong:

1. **The filters are the caller's, applied by the SAME pure builder the list
   endpoint uses** (``filters.build_task_filters``). A second filter parser
   would drift, and then the export and the screen would disagree about what
   "my open bugs in Ops" means — the exact bug class §11.8 wrote the one-builder
   rule for. An export that quietly means "everything in the project" is worse
   than no export: it is a spreadsheet that looks authoritative and is not.

2. **The columns are the view's ``shown_fields``**, plus the task number and
   title unconditionally (a row you cannot identify is not a row). Column ORDER
   is the vocabulary's own declaration order, never the stored list's, because
   that is what ``lib/table.tableColumns`` draws on screen — following the
   stored list would make the file's columns differ from the table's, which is
   the same disagreement rule 1 forbids one level down. ``shown_fields`` is
   normalised by ``filters.normalise_view_config``, the one normaliser saved
   views already go through, so this endpoint cannot accept a key a view could
   not store.

3. **It is never truncated. It is complete, or it is refused.** The cap is
   :data:`MAX_EXPORT_ROWS`, checked with a ``count(*)`` over the same WHERE
   *before* a single row is rendered, and a filter matching more than that
   answers **422 naming the real count and the cap** rather than a file holding
   the first N rows. A short CSV does not announce itself the way a short page
   does — nobody scrolls to the bottom of a spreadsheet to check whether it
   ended early — so silence here is indistinguishable from a smaller workspace.
   The calendar (WS-27q) took the other branch of the same trade, and could:
   its client draws a "truncated" banner beside the month. A downloaded file
   has no banner.

**The path is ``/projects/export/tasks.csv``, not ``/projects/tasks/export.csv``.**
The obvious spelling would be *shadowed*: ``/projects/tasks/{task_id}`` is a
template, so which handler answered would depend on router registration order —
the one trap ``__init__.py`` says this package does not have. A literal segment
of its own keeps that true.

**Tenancy (R5/R11).** This is a read of tenant data, so it goes through
``_tenant_session()`` like every other read in this package — no new
DB-connection site, no ``get_db()``, and ``routes/projects`` stays at zero
unbound sites with no H2 exemption. The tenant comes from the ambient binding
the request already carries; the caller's identity comes from the authenticated
``UserContext``; neither is ever read from a header, query parameter or body.
Visibility is the same grant clause every other read binds, so the export can
never contain a task the caller could not open.

**CSV injection is neutralised, not documented away.** See
:func:`gateway.csv_export.csv_cell`, the shared writer this file re-exports.
"""

from __future__ import annotations

import re
from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException, Response

# The gateway's ONE CSV writer (WS-26i-export promoted it out of this file when
# the CRM became the second export). `csv_cell` is a security control, and a
# security control with two copies is one that does not get the next fix —
# CLAUDE.md §4. Re-exported below so this module's public surface is unchanged.
from gateway.csv_export import (
    BOM,
    FORMULA_LEADERS,
    INJECTION_GUARD,
    csv_cell,
    csv_document,
)
from gateway.routes.projects.core import (
    DIRECTIONS,
    TASK_SORTS,
    TaskModel,
    _tenant_session,
    actor,
    load_visible_project,
    resolve_visibility,
    root_project_id,
    router,
    row_to_dict,
    task_visibility_clause,
    triage_exclusion_clause,
)
from gateway.routes.projects.custom_fields import load_definitions
from gateway.routes.projects.filters import (
    SHOWN_FIELDS,
    attach_assignees,
    attach_relation_counts,
    build_task_filters,
    normalise_view_config,
    split_csv,
)
from sqlalchemy import text

#: The most rows one export may contain.
#:
#: Reached, the request is **refused** (see the module docstring): this endpoint
#: never hands back a partial file. Five thousand is a spreadsheet a person
#: opens, and it is also the point past which "export it and look" stops being
#: what anybody actually wants — beyond it the honest answer is to narrow the
#: filter, which is the thing the refusal says.
MAX_EXPORT_ROWS = 5000

#: Column header per ``shown_fields`` key.
#:
#: Mirrors ``lib/shownFields.ts``'s ``FIELD_LABELS`` — the client vocabulary the
#: table's own headers are drawn from, so the file's header row reads exactly
#: like the screen's. A hand-kept mirror needs a test that reads the original,
#: or it is a comment claiming to be an invariant (the `attachment` activity-type
#: lesson, §4 of core.py): ``test_projects_export`` parses the TypeScript.
FIELD_LABELS: dict[str, str] = {
    "status": "Status",
    "assignees": "Assignees",
    "start_date": "Start",
    "due_at": "Due",
    "importance": "Priority",
    "subtasks": "Subtasks",
    "blocked": "Blocked",
    "tags": "Tags",
    "attachments": "Files",
    "estimate": "Estimate",
    "created_at": "Created",
}

#: The two columns every export carries, whatever the view shows.
#:
#: A row you cannot identify is not a row: without the number a line in the file
#: cannot be matched back to a task, and without the title nobody can read it.
UNCONDITIONAL_HEADERS: tuple[str, str] = ("#", "Title")


def resolve_columns(
    raw_shown_fields: str | None, definitions: list[dict[str, Any]],
) -> list[tuple[str, str]]:
    """The export's columns as ``(key, header)``, in the order the table draws.

    Core keys in :data:`filters.SHOWN_FIELDS` declaration order, then the
    project's custom fields in their registry order — ``lib/table.tableColumns``
    exactly, because the stored list is a SET and its order is not a preference
    anybody expressed.

    A ``custom.<key>`` with no surviving definition produces **no column**, the
    same rule the table applies: the field was deleted after the view was saved,
    and a header with nothing under it is a column of nothing.

    **An absent or empty ``shown_fields`` yields only the unconditional pair.**
    Not a default set: the column set belongs to the caller's VIEW, the server
    holds no view, and mirroring the client's ``DEFAULT_SHOWN`` here would be a
    second copy of a preference that drifts. A caller who wants columns names
    them, and the UI always does.
    """
    wanted = set(
        normalise_view_config(
            {"shown_fields": split_csv(raw_shown_fields)},
        )["shown_fields"]
    )
    columns: list[tuple[str, str]] = []
    for key in SHOWN_FIELDS:
        if key in wanted:
            columns.append((key, FIELD_LABELS[key]))
    for definition in definitions:
        key = f"custom.{definition['field_key']}"
        if key in wanted:
            columns.append((key, str(definition["name"])))
    return columns


def _render(key: str, task: dict[str, Any], statuses: dict[str, str]) -> Any:
    """One task's value for one column key, before :func:`csv_cell`.

    Two columns deliberately carry the STORED value rather than the label the
    table draws, and both are the same judgement: ``importance`` and
    ``estimate``'s formatting vocabularies (``lib/table.IMPORTANCE_OPTIONS``,
    ``durationLabel``) live in the browser, and copying either here would be a
    second vocabulary that drifts — the one thing this package refuses to grow.
    A spreadsheet wants the number anyway: ``2`` sorts and sums, ``High`` does
    not.
    """
    if key == "status":
        return statuses.get(str(task.get("status_id") or ""), "")
    if key == "assignees":
        return ", ".join(task.get("assignees") or [])
    if key == "tags":
        return ", ".join(task.get("tags") or [])
    if key == "subtasks":
        counts = task.get("subtasks") or {}
        total = int(counts.get("total") or 0)
        # "0/0" would claim a task HAS sub-tasks, all of them unfinished.
        return f"{int(counts.get('done') or 0)}/{total}" if total else ""
    if key == "blocked":
        return task.get("blocked_by_count") or ""
    if key == "attachments":
        # The one column the file knows more than the screen: the table renders
        # "—" here because the LIST endpoint does not count attachments
        # (`lib/card.ts` says so, honestly). An export that carried the screen's
        # ignorance would be a column of nothing, so it is counted below.
        return task.get("attachment_count") or ""
    if key == "estimate":
        return task.get("estimate_mins")
    if key.startswith("custom."):
        value = (task.get("custom_fields") or {}).get(key[len("custom."):])
        if isinstance(value, list):
            return ", ".join(str(item) for item in value)
        if isinstance(value, bool):
            return "true" if value else "false"
        return value
    return task.get(key)


#: How many attachments each of these tasks carries, in ONE query.
#:
#: The same page-aggregate shape as ``filters._ASSIGNEES_SQL`` and for the same
#: reason. It lives here rather than in ``filters.py`` because nothing else asks
#: for it: putting it beside the board's aggregates would imply the board draws
#: an attachment count, which it does not.
_ATTACHMENT_COUNTS_SQL = """
SELECT task_id, count(*) AS files
  FROM pm_task_attachments
 WHERE task_id = ANY(CAST(:ids AS uuid[]))
 GROUP BY task_id
"""

_STATUS_NAMES_SQL = """
SELECT id, name FROM pm_task_statuses WHERE id = ANY(CAST(:ids AS uuid[]))
"""


@router.get("/export/tasks.csv")
async def export_tasks_csv(
    user: UserContext = Depends(get_current_user),
    project_id: str | None = None,
    include_subtree: bool = False,
    parent_task_id: str | None = None,
    status_id: str | None = None,
    assignee: str | None = None,
    q: str | None = None,
    sort: str | None = None,
    direction: str = "desc",
    include_archived: bool = False,
    status_category: str | None = None,
    assignees: str | None = None,
    unassigned: bool = False,
    overdue: bool = False,
    due_before: str | None = None,
    importance_gte: int | None = None,
    tags: str | None = None,
    tags_all: str | None = None,
    include_triage: bool = False,
    # WS-27bk §9.12.2. Carried because the docstring below promises this
    # endpoint takes `list_tasks`' parameters verbatim. A filter the board
    # applies and the export ignores hands somebody a file of the wrong rows,
    # and nothing on the way says so.
    watching: bool = False,
    #: The view's field set, CSV, in the wire vocabulary `shown_fields` is
    #: stored under. Absent = the two unconditional columns only.
    shown_fields: str | None = None,
) -> Response:
    """The caller's current filter, as a CSV file.

    The parameter list is ``list_tasks``' verbatim minus pagination — a page is
    a screen's unit, and half a filter's tasks in a file is exactly the silent
    truncation this endpoint refuses. ``sort``/``direction`` are kept because
    the order on screen is part of what somebody is exporting.
    """
    column = TASK_SORTS.get(sort or "created_at")
    if column is None:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown sort key '{sort}'. One of: {sorted(TASK_SORTS)}.",
        )
    order = DIRECTIONS.get((direction or "").lower())
    if order is None:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown sort direction '{direction}'. One of: asc, desc.",
        )

    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        clauses: list[str] = [task_visibility_clause(vis)]
        params: dict[str, Any] = dict(vis.params)
        definitions: list[dict[str, Any]] = []

        if project_id:
            # Seeing the project is required to export it: an unreadable id is a
            # 404, never an empty file, which would confirm the project exists
            # and is simply quiet (R5, the whole package's rule).
            await load_visible_project(db, vis, project_id)
            if include_subtree:
                clauses.append(
                    "t.project_id IN ("
                    "  WITH RECURSIVE sub AS ("
                    "    SELECT id FROM pm_projects WHERE id = CAST(:pid AS uuid)"
                    "    UNION ALL"
                    "    SELECT p.id FROM pm_projects p JOIN sub s"
                    "      ON p.parent_project_id = s.id"
                    "  ) SELECT id FROM sub)"
                )
            else:
                clauses.append("t.project_id = CAST(:pid AS uuid)")
            params["pid"] = project_id
            # Custom-field COLUMNS need their definitions, which are per root
            # project. Without a project there is no registry to read, so a
            # `custom.*` key simply produces no column — the same honest
            # rendering the table gives a deleted field.
            definitions = await load_definitions(
                db, await root_project_id(db, project_id),
            )

        extra_clauses, extra_params = build_task_filters(
            parent_task_id=parent_task_id, status_id=status_id,
            status_category=status_category, assignee=assignee,
            assignees=assignees, unassigned=unassigned, overdue=overdue,
            due_before=due_before, importance_gte=importance_gte, q=q,
            tags=tags, tags_all=tags_all, include_archived=include_archived,
            watching=watching, viewer=actor(user) if watching else None,
        )
        clauses.extend(extra_clauses)
        params.update(extra_params)
        if not include_triage:
            clauses.append(triage_exclusion_clause())

        where = " WHERE " + " AND ".join(clauses)
        total = int((await db.execute(
            text(f"SELECT count(*) FROM pm_tasks t{where}"), params,
        )).scalar() or 0)
        if total > MAX_EXPORT_ROWS:
            # The refusal, and the reason it is a refusal — see the module
            # docstring. It names the real number so the caller can judge how
            # much narrowing is needed rather than guessing.
            raise HTTPException(
                status_code=422,
                detail=(
                    f"{total} tasks match these filters and the export cap is "
                    f"{MAX_EXPORT_ROWS}. Narrow the filter (a status, an "
                    f"assignee, a single project) and export again — this "
                    f"endpoint will not hand back a partial file."
                ),
            )

        rows = (await db.execute(
            text(
                f"SELECT t.* FROM pm_tasks t{where} "
                f"ORDER BY {column.format(dir=order)} "
                f"LIMIT :cap"
            ),
            {**params, "cap": MAX_EXPORT_ROWS},
        )).fetchall()
        exported = [row_to_dict(r, TaskModel) for r in rows]
        await attach_assignees(db, exported)
        await attach_relation_counts(db, exported)

        ids = [str(r["id"]) for r in exported if r.get("id")]
        statuses: dict[str, str] = {}
        if ids:
            status_ids = sorted({
                str(r["status_id"]) for r in exported if r.get("status_id")
            })
            statuses = {
                str(r.id): r.name for r in (await db.execute(
                    text(_STATUS_NAMES_SQL), {"ids": status_ids},
                )).fetchall()
            }
            files = {
                str(r.task_id): int(r.files or 0) for r in (await db.execute(
                    text(_ATTACHMENT_COUNTS_SQL), {"ids": ids},
                )).fetchall()
            }
            for row in exported:
                row["attachment_count"] = files.get(str(row["id"]), 0)

    columns = resolve_columns(shown_fields, definitions)
    return Response(
        # RFC 4180, the BOM and the formula guard all live in the shared writer
        # — see the import note at the top of this file.
        content=csv_document(
            [*UNCONDITIONAL_HEADERS, *(header for _, header in columns)],
            (
                [
                    row.get("task_number"),
                    row.get("title"),
                    *(_render(key, row, statuses) for key, _ in columns),
                ]
                for row in exported
            ),
        ),
        media_type="text/csv; charset=utf-8",
        headers={
            # The server owns the filename so there is ONE of it: the browser
            # reads it back off this header rather than composing a second one
            # that would drift (`lib/export.filenameFromDisposition`).
            "Content-Disposition": (
                f'attachment; filename="{_filename(project_id)}"'
            ),
            # An honest count beside the file, for anything reading this
            # programmatically. The file itself is complete by construction.
            "X-Export-Rows": str(len(exported)),
        },
    )


def _filename(project_id: str | None) -> str:
    """``projects-tasks.csv``, or the project's id when one was named.

    The id rather than the name: a project name is free text (slashes, quotes,
    newlines, non-ASCII) and a filename built from one is a header-injection
    surface in a response header. The browser shows the file it downloaded next
    to the page it downloaded it from, so the name buys little and costs a
    validated escape.
    """
    if project_id and re.fullmatch(r"[0-9a-fA-F-]{36}", project_id):
        return f"projects-tasks-{project_id}.csv"
    return "projects-tasks.csv"


__all__ = [
    "BOM",
    "FIELD_LABELS",
    "FORMULA_LEADERS",
    "INJECTION_GUARD",
    "MAX_EXPORT_ROWS",
    "UNCONDITIONAL_HEADERS",
    "csv_cell",
    "resolve_columns",
]
