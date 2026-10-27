"""Navigation — open a task, a project or an app in the member's Projects page.

Spec: ``project-docs/specs/projects_ai_chat.md`` §4.2 ("Frontend tools,
navigation only").

The page registers three browser handlers (``projects.open_task``,
``projects.open_project``, ``projects.open_app``) through ``useFrontendTool``.
:func:`open_in_app` reads the row first — so the route decides whether the
member may see it — then asks the page to open it through
``acb_skills.frontend_tools.emit_frontend_tool``. It writes nothing.

The result always carries the deep link. A member chatting from the main chat
app has no Projects page open, so no handler runs; the link is how they get
there.
"""

from __future__ import annotations

import importlib
from typing import Any

from skill_projects.client import data, get, headers, uuid_of

try:
    from acb_skills.tool_annotations import annotate as _annotate
except Exception:  # pragma: no cover — platform package absent in isolation

    def _annotate(**_hints):  # type: ignore[misc]
        def _wrap(fn):
            return fn

        return _wrap


#: The apps the page opens by id. Only LIVE entries open (the page checks
#: `launch === "live"` again, so a slug here that is not live does nothing).
APPS = ("reports",)
#: Old app ids that still open an app (WS-27bn R5f). The Analytics app
#: joined Reports, so a model that says ``analytics`` opens Reports. The
#: page maps the id again through ``resolveAppLink`` in ``projectApps.ts``.
APP_ALIASES = {"analytics": "reports"}
TARGETS = ("task", "project", "app")


async def _dispatch(name: str, args: dict[str, Any]) -> bool:
    """One door to the dispatcher, through the module so a test can record it."""
    ft = importlib.import_module("acb_skills.frontend_tools")
    try:
        result = await ft.emit_frontend_tool(name, args)
    except Exception:  # pragma: no cover — the helper never raises by contract
        return False
    return bool(result.get("ok"))


@_annotate(read_only=True, idempotent=True, open_world=False)
async def open_in_app(target: str, target_id: str = "") -> str:
    """Open something in the member's Projects page: target is task (a
    task's full_id), project (a node's full_id) or app (reports, as
    target_id). It reads the row first, then asks the open page to show
    it. It changes nothing. The result always carries a link, for a member
    who is not on the Projects page."""
    which = str(target or "").strip().lower()
    # A run with nobody to act as opens nothing, even an app that needs no
    # read: the same refusal every gateway call makes (`client.headers`).
    headers()
    if which not in TARGETS:
        return f"target is one of {', '.join(TARGETS)}."
    if which == "app":
        app = str(target_id or "").strip().lower()
        app = APP_ALIASES.get(app, app)
        if app not in APPS:
            return f"app is one of {', '.join(APPS)}."
        sent = await _dispatch("projects.open_app", {"app": app})
        return _result(f"the {app} app", f"/projects?app={app}", sent)
    if which == "task":
        tid = uuid_of(target_id, "task_id")
        task = await get(f"/projects/tasks/{tid}")
        sent = await _dispatch("projects.open_task", {"task_id": tid})
        label = f"#{task.get('task_number')} {data(task.get('title'))}"
        return _result(label, f"/projects?task={tid}", sent) + f"\n  full_id: {tid}"
    pid = uuid_of(target_id, "project_id")
    node = await get(f"/projects/nodes/{pid}")
    sent = await _dispatch("projects.open_project", {"project_id": pid})
    return _result(data(node.get("name")), f"/projects?project={pid}", sent) + (
        f"\n  project_id: {pid}"
    )


def _result(label: str, link: str, sent: bool) -> str:
    """What happened, honestly. A queued event is a REQUEST to an open page:
    the run cannot know whether a Projects page consumed it (a member on the
    main chat app has none). So the text says "asked", and the link comes on
    every path (S6 review)."""
    head = (
        f"Asked the Projects page to open {label}. If it is not open, use the link."
        if sent
        else f"Open {label} with the link."
    )
    return f"{head}\n  link: {link}"


# ── WS-46 P11 (G22) — what the member's board shows ──────────────────────────
#
# Owner, 2026-10-22: "We should be able to also set up different filters,
# views, etc. as needed from the chat interface." This reverses Q4 of
# 2026-10-06. The tool changes what the OPEN page draws, the way the filter
# bar and the canvas switch do. It writes nothing: a filter is browser state,
# so there is no card. A view that must last is `save_view`.

#: The page's canvases, `VIEW_MODES` in `app/projects/lib/commands.ts`.
#: `tests/unit/test_projects_show_view.py` holds the two lists equal.
CANVASES = ("overview", "board", "list", "table", "calendar", "timeline")


async def _saved_view(pid: str, name: str) -> dict[str, Any]:
    from skill_projects.writes import _one_named

    rows = ((await get(f"/projects/nodes/{pid}/views")) or {}).get("rows") or []
    return _one_named(rows, name, "view")


@_annotate(read_only=True, idempotent=True, open_world=False)
async def show_view(
    project_id: str,
    mode: str = "",
    view_name: str = "",
    filters: str = "",
    group_by: str = "",
    sub_group_by: str = "",
) -> str:
    """Change what the member's open Projects page shows, for one project.
    Nothing is saved. mode is overview, board, list, table, calendar or
    timeline. view_name opens a saved view of the project by name, with its
    filters and grouping. filters is a JSON object in save_view's words:
    query, status_category, assignee, unassigned, overdue, watching, archived
    and tags. It REPLACES the filters on screen, and {} clears them. group_by
    is status, category, assignee, project, importance, tag, agent or none.
    sub_group_by puts the board in lanes by a second axis, or none. Use
    save_view when the member wants the view kept. The result carries a
    link for a member who is not on the Projects page."""
    from skill_projects.inbox import GROUP_BY, _view_filters

    headers()
    pid = uuid_of(project_id, "project_id")
    canvas = str(mode or "").strip().lower()
    if canvas and canvas not in CANVASES:
        return f"mode is one of {', '.join(CANVASES)}."
    axis = str(group_by or "").strip().lower()
    if axis and axis not in GROUP_BY:
        return f"group_by is one of {', '.join(GROUP_BY)}."
    lane = str(sub_group_by or "").strip().lower()
    if lane and lane not in GROUP_BY:
        return f"sub_group_by is one of {', '.join(GROUP_BY)}."
    if lane and lane != "none" and lane == axis:
        return "sub_group_by must differ from group_by. A board cannot lane by its own columns."
    asked_filters = bool(str(filters or "").strip())
    if not (canvas or view_name.strip() or asked_filters or axis or lane):
        return "Say what to show: a mode, a view_name, filters, group_by or sub_group_by."
    # The read decides whether the member may see the project, as every
    # navigation does.
    node = await get(f"/projects/nodes/{pid}")
    args: dict[str, Any] = {"project_id": pid}
    said: list[str] = []
    if canvas:
        args["mode"] = canvas
        said.append(f"the {canvas}")
    if view_name.strip():
        view = await _saved_view(pid, view_name)
        args["view_id"] = str(view.get("id"))
        args["view_config"] = view.get("config") or {}
        said.append(f"saved view {data(view.get('name'))}")
    if asked_filters:
        found = await _view_filters(filters)
        if isinstance(found, str):
            return found
        args["filters"] = found
        said.append("filters " + (", ".join(f"{k}={v}" for k, v in found.items()) or "cleared"))
    if axis:
        args["group_by"] = axis
        said.append(f"grouped by {axis}")
    if lane:
        args["sub_group_by"] = lane
        said.append("no lanes" if lane == "none" else f"in lanes by {lane}")
    sent = await _dispatch("projects.show_view", args)
    label = f"{data(node.get('name'))}, {'; '.join(said)}"
    return _result(label, f"/projects?project={pid}", sent) + f"\n  project_id: {pid}"


__all__ = ["CANVASES", "open_in_app", "show_view"]
