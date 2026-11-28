"""Projects · the Excel export of a task query, in the shape the caller asks (DOC-6).

Spec: ``project-docs/specs/chat_parity.md`` §DOC row DOC-6.

``GET /projects/export/workbook.xlsx?spec=<json>`` gives one ``.xlsx``.
``spec`` is ``{"sheets": [{"source": {"type": "tasks", <filters>}, <shape>}]}``.
The chat's ``make_spreadsheet`` calls it as the member, so the rows go from
the database to the file and never through the model.

**The read is the CSV export's read** (:func:`export.export_tasks_csv`): the
same filters, the same grant clause, the same names for a status, a type and a
priority, and the same refusal above ``export.MAX_EXPORT_ROWS``. This module
reads its file back, so no second task query exists. Each sheet is complete or
the whole file is refused. It writes nothing.
"""

from __future__ import annotations

import asyncio
import csv
import io
import re
from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException, Query, Response
from gateway import xlsx_export as xe
from gateway.csv_export import BOM, FORMULA_LEADERS, INJECTION_GUARD
from gateway.routes.projects.core import (
    _tenant_session,
    load_visible_project,
    resolve_visibility,
    root_project_id,
    router,
)
from gateway.routes.projects.custom_fields import load_definitions
from gateway.routes.projects.export import (
    UNCONDITIONAL_HEADERS,
    export_tasks_csv,
    resolve_columns,
)
from gateway.routes.projects.filters import SHOWN_FIELDS

#: The filters of a task source: the CSV export's own parameters.
_FILTERS = frozenset({
    "project_id", "include_subtree", "parent_task_id", "status_id", "status_category",
    "assignee", "assignees", "unassigned", "overdue", "due_before", "importance_gte",
    "q", "tags", "tags_all", "include_archived", "archived_only", "include_triage",
    "scope", "watching", "top_level", "has_parent", "is_epic", "assigned",
    "updated_before", "updated_after", "created_after", "title_prefix",
})
_BOOLS = frozenset({
    "include_subtree", "unassigned", "overdue", "include_archived", "archived_only",
    "include_triage", "watching", "top_level", "has_parent", "is_epic", "assigned",
})
_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                   r"[0-9a-fA-F]{12}")
_NUMBERS = frozenset({"estimate", "attachments", "blocked"})


def _unguard(cell: str) -> str:
    """The stored text of a CSV cell: the guard of ``csv_export.csv_cell`` is
    for a CSV file, and the Excel writer guards each cell itself."""
    if cell.startswith(INJECTION_GUARD) and cell[1:].startswith(FORMULA_LEADERS):
        return cell[1:]
    return cell


def _args(plan: xe.SheetPlan) -> dict[str, Any]:
    args = xe.source_args(plan, _FILTERS)
    for key, value in args.items():
        if key in _BOOLS:
            if not isinstance(value, bool):
                raise xe.SpreadsheetError(f'"{key}" is true or false')
        elif key == "importance_gte":
            if not isinstance(value, int) or isinstance(value, bool):
                raise xe.SpreadsheetError('"importance_gte" is a whole number')
        else:
            args[key] = ",".join(map(str, value)) if isinstance(value, list) else str(value)
    for key in ("project_id", "parent_task_id", "status_id"):
        if key in args and not _UUID.fullmatch(args[key]):
            raise xe.SpreadsheetError(f'"{key}" is not an id')
    return args


async def _definitions(project_id: str, user: UserContext) -> list[dict[str, Any]]:
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        # A project the member cannot see is a 404, before its fields are read.
        await load_visible_project(db, vis, project_id)
        return await load_definitions(db, await root_project_id(db, project_id))


async def _table(plan: xe.SheetPlan, user: UserContext) -> xe.Table:
    args = _args(plan)
    custom: list[dict[str, Any]] = []
    if args.get("project_id"):
        custom = await _definitions(args["project_id"], user)
    keys = [*SHOWN_FIELDS, *(f"custom.{d['field_key']}" for d in custom)]
    answer = await export_tasks_csv(user=user, shown_fields=",".join(keys), **args)
    text = bytes(answer.body).decode("utf-8").removeprefix(BOM)
    lines = csv.reader(io.StringIO(text, newline=""))
    next(lines, None)
    # The columns of the route, in its order: "#", "Title", then those it drew.
    drawn = [("number", UNCONDITIONAL_HEADERS[0]), ("title", UNCONDITIONAL_HEADERS[1]),
             *resolve_columns(",".join(keys), custom)]
    columns = [key for key, _ in drawn]
    fields = [xe.Field(key, label) for key, label in drawn]
    rows: list[dict[str, Any]] = []
    for line in lines:
        row: dict[str, Any] = {k: _unguard(v) for k, v in zip(columns, line, strict=True)}
        for key in ("number", *_NUMBERS):
            if str(row.get(key, "")).isdigit():
                row[key] = int(row[key])
        rows.append(row)
    return xe.Table(fields=fields, rows=rows)


@router.get("/export/workbook.xlsx")
async def export_tasks_workbook(
    spec: str = Query(..., max_length=xe.MAX_SPEC_CHARS),
    user: UserContext = Depends(get_current_user),
) -> Response:
    """The tasks that the sources of *spec* pick, as one Excel file."""
    try:
        plans = xe.parse_query_spec(spec, "tasks")
        tables: list[xe.Table] = []
        for plan in plans:
            tables.append(await _table(plan, user))
            read = sum(len(t.rows) for t in tables)
            if read > xe.MAX_EXPORT_ROWS:
                raise xe.too_many(read, at_least=True)
        built = await asyncio.to_thread(xe.build, plans, tables)
    except xe.SpreadsheetError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return xe.workbook_response(built)
