"""Views — reads that also draw. A timeline, a board, a table, a report card.

Spec: ``project-docs/specs/projects_ai_chat.md`` §3.4, §4.2 (S4).

Every tool here is class A: it reads through the routes the manifest gives
its composite (``manifest.COMPOSITE``) and then emits ONE generative UI
template through ``emit_generative_ui`` (``generative_ui_2.md`` §3), so the
member sees the rows as a card and not as a wall of text. The text the tool
returns is the same facts in the row convention the cards read, so the agent
can reason over them and the transcript keeps them when a card is dismissed.

The template names and their ``data`` shapes live in
``genUITemplates.tsx::TEMPLATE_CATALOG``, mirrored into the
``emit_generative_ui`` docstring. ``tests/unit/test_genui_catalog_lockstep.py``
holds the two equal, and ``genUITemplates.test.ts`` holds the catalog equal
to the registry, so a name emitted here is a name the renderer draws.

Emitting is best-effort: with no active run stream (a test, a batch run) the
emit returns ``ok: False`` and the tool still returns its text.

Every card is INLINE. The Projects rail renders ``AgentChat`` and no side
panel host, so a ``surface: "panel"`` spec is a chip that opens nothing
there (S4 review). The templates scroll inside the card instead.
"""

from __future__ import annotations

import importlib
import json
from typing import Any

from skill_projects.client import data, get, uuid_of
from skill_projects.reads import (
    _day,
    _report_section,
    _status_names,
    _task_line,
    legend,
)

try:
    from acb_skills.tool_annotations import annotate as _annotate
except Exception:  # pragma: no cover — platform package absent in isolation

    def _annotate(**_hints):  # type: ignore[misc]
        def _wrap(fn):
            return fn

        return _wrap


#: How many timeline rows a drawn timeline carries. More than the detail
#: read's dozen, because the card scrolls and the text does not.
TIMELINE_CARD_ROWS = 40
#: How many tasks a board or a table draws. The route caps a page at 50.
BOARD_ROWS = 50
#: The board shows OPEN work. The list route hides nothing by default, so the
#: categories are named (a CSV the route splits); done and cancelled stay off.
OPEN_CATEGORIES = "backlog,todo,in_progress,triage"


async def _emit(spec: dict[str, Any]) -> dict[str, Any]:
    """One door to ``emit_generative_ui``, through the module attribute so a
    test can record what was drawn. The package re-exports the FUNCTION
    ``write_artifact`` at top level, which shadows the submodule, so the
    module is imported by name."""
    wa = importlib.import_module("acb_skills.write_artifact")
    try:
        return await wa.emit_generative_ui(json.dumps(spec))
    except Exception as exc:  # pragma: no cover — the emit never raises by contract
        return {"ok": False, "error": str(exc)}


def _template(name: str, payload: dict[str, Any], *, surface: str = "inline") -> dict[str, Any]:
    spec: dict[str, Any] = {"type": "template", "props": {"name": name, "data": payload}}
    if surface != "inline":
        spec["surface"] = surface
    return spec


def _plain(value: Any) -> str:
    """Member text for a card: newlines collapsed, no fence. A card cell is
    not a line the receipt parser reads, so the guillemets would only clutter."""
    return " ".join(str(value or "").split())


def _change_text(meta: dict[str, Any]) -> tuple[str, str, str]:
    """``(field, before, after)`` from a field-change row's meta."""
    changes = meta.get("changes") or []
    if isinstance(changes, list) and changes:
        first = changes[0] if isinstance(changes[0], dict) else {}
        return (
            str(first.get("field") or ""),
            _plain(first.get("old_label", first.get("old"))),
            _plain(first.get("new_label", first.get("new"))),
        )
    if meta.get("field"):
        return str(meta.get("field")), _plain(meta.get("before")), _plain(meta.get("after"))
    return "", "", ""


# ── The timeline ─────────────────────────────────────────────────────────────


@_annotate(read_only=True, idempotent=True)
async def render_timeline(task_id: str, kind: str = "all") -> str:
    """Draw a task's timeline as a card: every comment, field change,
    assignment and system note, newest first, with who and when. kind is
    all, comments or events. Use it when the member asks what happened on a
    task, or to review a change before revert_activity."""
    tid = uuid_of(task_id, "task_id")
    which = str(kind or "all").strip().lower()
    if which not in ("all", "comments", "events"):
        return "kind is all, comments or events."
    task = await get(f"/projects/tasks/{tid}")
    payload = await get(
        f"/projects/tasks/{tid}/timeline",
        {"kind": which, "page": 1, "page_size": TIMELINE_CARD_ROWS},
    )
    events = (payload or {}).get("rows") or []
    total = int((payload or {}).get("total") or len(events))
    rows: list[dict[str, Any]] = []
    lines = [legend(), *_task_line(task), f"Timeline ({len(events)} of {total}):"]
    for ev in events:
        meta = ev.get("meta") or {}
        field, before, after = _change_text(meta if isinstance(meta, dict) else {})
        row: dict[str, Any] = {
            "id": str(ev.get("id") or ""),
            "at": str(ev.get("created_at") or ""),
            "type": str(ev.get("type") or ""),
            "actor": _plain(ev.get("created_by")),
            "body": _plain(str(ev.get("body") or "")[:400]),
        }
        if field:
            row.update({"field": field, "before": before, "after": after})
        if isinstance(meta, dict) and meta.get("via"):
            # D-PM-36 stamps `chat:projects-assistant`. A member reads "the AI
            # chat", not the identifier (visual review, 2026-09-23).
            via = str(meta.get("via"))
            row["via"] = "the AI chat" if via.startswith("chat:") else via
        rows.append(row)
        line = f"- {_day(ev.get('created_at'))} {ev.get('type')} by {data(ev.get('created_by') or '?')}"
        if ev.get("body"):
            line += f": {data(str(ev.get('body'))[:200])}"
        elif field:
            line += f": {field} {data(before)} → {data(after)}"
        lines.append(f"{line} (activity id {ev.get('id')})")
    await _emit(
        _template(
            "timeline",
            {
                "title": f"#{task.get('task_number')} {_plain(task.get('title'))}",
                "taskId": tid,
                "total": total,
                "rows": rows,
            },
        )
    )
    return "\n".join(lines)


# ── The board and the table ──────────────────────────────────────────────────


def _task_cell(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(task.get("id") or ""),
        "number": task.get("task_number"),
        "title": _plain(task.get("title")),
        "assignees": [_plain(a) for a in task.get("assignees") or []],
        "due": _day(task.get("due_at")),
        "importance": task.get("importance"),
        "done": bool(task.get("completed_at")),
    }


def _list_params(project_id: str, **extra: Any) -> dict[str, Any]:
    params: dict[str, Any] = {"include_subtree": True, "page": 1, "page_size": BOARD_ROWS}
    if project_id:
        params["project_id"] = uuid_of(project_id, "project_id")
    for key, value in extra.items():
        if value not in ("", None, False):
            params[key] = value
    return params


@_annotate(read_only=True, idempotent=True)
async def render_board(project_id: str, assignee: str = "", tags: str = "") -> str:
    """Draw a project's board as a card: one column per status lane, the
    OPEN tasks in each (done and cancelled lanes stay empty), with assignee
    and due date. Up to 50 tasks; the text names the total. assignee and
    tags narrow it the way the app's filters do. Each row opens the task in
    the app."""
    pid = uuid_of(project_id, "project_id")
    node = await get(f"/projects/nodes/{pid}")
    lanes = ((await get(f"/projects/nodes/{pid}/statuses")) or {}).get("rows") or []
    listing = await get(
        "/projects/tasks",
        _list_params(pid, assignees=assignee, tags=tags, status_category=OPEN_CATEGORIES),
    )
    tasks = (listing or {}).get("rows") or []
    total = int((listing or {}).get("total") or len(tasks))
    lane_names = {str(lane.get("id")): str(lane.get("name") or "") for lane in lanes}
    by_status: dict[str, list[dict[str, Any]]] = {}
    for t in tasks:
        by_status.setdefault(str(t.get("status_id")), []).append(t)
    columns = [
        {
            "id": str(lane.get("id")),
            "name": _plain(lane.get("name")),
            "category": lane.get("category"),
            "tasks": [_task_cell(t) for t in by_status.get(str(lane.get("id")), [])],
        }
        for lane in lanes
    ]
    lines = [
        legend(),
        f"Board for {data(node.get('name'))}: {total} tasks, showing {len(tasks)}",
    ]
    for lane in lanes:
        held = by_status.get(str(lane.get("id")), [])
        lines.append(f"{data(lane.get('name'))} ({len(held)}):")
        for t in held:
            lines.extend(_task_line(t, lane_names.get(str(t.get("status_id")), "")))
    await _emit(
        _template(
            "taskBoard",
            {"title": _plain(node.get("name")), "total": total, "columns": columns},
        )
    )
    return "\n".join(lines)


@_annotate(read_only=True, idempotent=True)
async def render_tasks(
    project_id: str = "",
    status_category: str = "",
    assignee: str = "",
    overdue: bool = False,
    tags: str = "",
    query: str = "",
) -> str:
    """Draw a task list as a table card: number, title, status, assignees,
    due, importance. The same filters as list_tasks. Each row opens the
    task in the app. Use it when the member wants to SEE a list, not read
    one."""
    params = _list_params(
        project_id,
        status_category=status_category,
        assignees=assignee,
        overdue=overdue,
        tags=tags,
        q=query,
    )
    listing = await get("/projects/tasks", params)
    tasks = (listing or {}).get("rows") or []
    total = int((listing or {}).get("total") or len(tasks))
    # A list row carries `status_id` only (`SELECT t.*`); the name lives on
    # the lane. Resolved the way `list_tasks` does (S4 verifier: the Status
    # column was blank for every row).
    names = await _status_names(
        {str(t.get("root_project_id")) for t in tasks if t.get("root_project_id")}
    )
    rows = [
        {
            "id": str(t.get("id") or ""),
            "cells": [
                f"#{t.get('task_number')}",
                _plain(t.get("title")),
                _plain(names.get(str(t.get("status_id")), "")),
                ", ".join(_plain(a) for a in t.get("assignees") or []) or "unassigned",
                _day(t.get("due_at")),
                t.get("importance") or "",
            ],
        }
        for t in tasks
    ]
    lines = [legend(), f"Tasks ({total} total, showing {len(tasks)}):"]
    for t in tasks:
        lines.extend(_task_line(t, names.get(str(t.get("status_id")), "")))
    await _emit(
        _template(
            "dataGrid",
            {
                "title": f"Tasks ({total})",
                "columns": ["#", "Title", "Status", "Assignees", "Due", "Importance"],
                "rows": rows,
                "openBase": "/projects?task=",
            },
        )
    )
    return "\n".join(lines)


# ── The report card ──────────────────────────────────────────────────────────


@_annotate(read_only=True, idempotent=True)
async def render_report(report_id: str) -> str:
    """Draw a saved report as a card: its headline numbers as tiles and
    each section as a table. Same numbers as report_render, computed now by
    the Reports app's own SQL. Use it for "show me the weekly report"."""
    rid = uuid_of(report_id, "report_id")
    payload = await get(f"/projects/reports/{rid}/render")
    report = payload.get("report") or {}
    sections = payload.get("sections") or {}
    lines = [
        legend(),
        f"Report {data(report.get('name'))} · {payload.get('period_start')} to "
        f"{payload.get('period_end')}",
    ]
    stats: list[dict[str, Any]] = []
    tables: list[dict[str, Any]] = []
    for name, section in sections.items():
        if not isinstance(section, dict):
            continue
        lines.extend(_report_section(str(name), section))
        for key in ("total", "finished", "count", "open_tasks", "blocked_total"):
            if isinstance(section.get(key), int | float):
                stats.append({"label": f"{name} {key}".replace("_", " "), "value": section[key]})
        rows = section.get("rows") or section.get("people") or section.get("projects") or []
        if isinstance(rows, list) and rows and isinstance(rows[0], dict):
            columns = [k for k in rows[0] if not isinstance(rows[0][k], dict | list)][:6]
            tables.append(
                {
                    "title": str(name).replace("_", " "),
                    "columns": columns,
                    "rows": [{"cells": [_plain(r.get(c)) for c in columns]} for r in rows[:25]],
                }
            )
    await _emit(
        _template(
            "reportCard",
            {
                "title": _plain(report.get("name")),
                "period": f"{payload.get('period_start')} to {payload.get('period_end')}",
                "reportId": rid,
                "stats": stats[:8],
                "tables": tables,
            },
        )
    )
    return "\n".join(lines)


# ── W2 · the status report ───────────────────────────────────────────────────

FLAG_ORDER = ("blocked", "at risk", "on track")


def _flag(child: dict[str, Any], blocked_ids: set[str], overdue_ids: set[str]) -> str:
    """One of three flags per project (spec §3.4 W2).

    Blocked: a task in it has an open blocking link. At risk: it has
    overdue work, or the stuck read names it. On track: neither.
    """
    cid = str(child.get("id") or "")
    if cid in blocked_ids:
        return "blocked"
    if cid in overdue_ids or int(child.get("overdue") or 0) > 0:
        return "at risk"
    return "on track"


@_annotate(read_only=True, idempotent=True)
async def status_report(project_id: str = "") -> str:
    """W2: a status report for a node or the portfolio. Every child project
    gets one flag from the server's reads: blocked (a task with an open
    blocking link), at risk (overdue work, or named by the stuck read), on
    track. Draws a dashboard card and returns the report as Markdown, which
    you then save with write_artifact so the member can open it. The
    numbers are the analytics routes'; nothing here counts a list."""
    params: dict[str, Any] = {"include_subtree": True}
    scope_name = "the portfolio"
    pid = ""
    if project_id.strip():
        pid = uuid_of(project_id, "project_id")
        params["project_id"] = pid
        summary = await get(f"/projects/nodes/{pid}/summary")
        scope_name = _plain(summary.get("name"))
    else:
        summary = await get("/projects/summary")
    stuck = await get("/projects/analytics/stuck", params)
    load = await get("/projects/analytics/load", params)
    outlook = await get("/projects/analytics/outlook", params)

    blocked_rows = stuck.get("blocked") or []
    blocked_ids = {str(r.get("project_id")) for r in blocked_rows if r.get("project_id")}
    overdue_ids = {
        str(r.get("project_id")) for r in stuck.get("overdue") or [] if int(r.get("overdue") or 0)
    }
    children = list(summary.get("children") or [])
    if not children and pid:
        # A leaf project has no children row. Its own work (`own`, always
        # present on the summary) is the one row the report flags.
        own = summary.get("own") or {}
        children = [
            {
                "id": pid,
                "name": summary.get("name"),
                "tasks": own.get("tasks", summary.get("tasks", 0)),
                "overdue": own.get("overdue", summary.get("overdue", 0)),
            }
        ]
    flags = [(c, _flag(c, blocked_ids, overdue_ids)) for c in children]
    counts = {f: sum(1 for _, flag in flags if flag == f) for f in FLAG_ORDER}
    people = load.get("people") or []
    top = max(people, key=lambda p: int(p.get("open_tasks") or 0), default=None)

    md = [
        f"# Status report · {scope_name}",
        "",
        f"Open tasks: {summary.get('tasks', 0)} · overdue: {summary.get('overdue', 0)} · "
        f"blocked by open work: {stuck.get('blocked_total', 0)}",
        "",
        "| Project | Flag | Open | Overdue |",
        "|---|---|---|---|",
    ]
    for child, flag in sorted(flags, key=lambda cf: FLAG_ORDER.index(cf[1])):
        md.append(
            f"| {_plain(child.get('name'))} | {flag} | {child.get('tasks', 0)} | "
            f"{child.get('overdue', 0)} |"
        )
    if blocked_rows:
        md += ["", "## Blocked", ""]
        for r in blocked_rows[:20]:
            md.append(
                f"- #{r.get('task_number')} {_plain(r.get('title'))} (due {_day(r.get('due_at'))})"
            )
    if top and int(top.get("open_tasks") or 0):
        md += [
            "",
            "## Load",
            "",
            f"Most open work: {_plain(top.get('assignee') or 'unassigned')} with "
            f"{top.get('open_tasks')} open, {top.get('overdue', 0)} overdue.",
        ]
    plan = outlook.get("plan") or {}
    if plan.get("planned_finish"):
        md += [
            "",
            "## Outlook",
            "",
            f"Planned finish {_day(plan.get('planned_finish'))} over {plan.get('dated', 0)} dated "
            f"of {plan.get('tasks', 0)} tasks; slip {plan.get('slip_days', 0)} days.",
        ]
    await _emit(
        _template(
            "statDashboard",
            {
                "title": f"Status · {scope_name}",
                "stats": [
                    {"label": "Blocked", "value": counts["blocked"], "icon": "octagon-alert"},
                    {"label": "At risk", "value": counts["at risk"], "icon": "triangle-alert"},
                    {"label": "On track", "value": counts["on track"], "icon": "circle-check"},
                    {"label": "Overdue tasks", "value": summary.get("overdue", 0)},
                ],
            },
        )
    )
    text_lines = [legend(), *md]
    text_lines.append(
        "Save this as a Markdown artifact with write_artifact, then offer to comment on "
        "each at-risk task (one class B batch)."
    )
    return "\n".join(text_lines)


__all__ = [  # noqa: RUF022 — grouped by what they draw
    "render_timeline",
    "render_board",
    "render_tasks",
    "render_report",
    "status_report",
]
