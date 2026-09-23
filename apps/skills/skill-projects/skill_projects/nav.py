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
APPS = ("analytics", "reports")
TARGETS = ("task", "project", "app")


async def _dispatch(name: str, args: dict[str, Any]) -> bool:
    """One door to the dispatcher, through the module so a test can record it."""
    ft = importlib.import_module("acb_skills.frontend_tools")
    try:
        result = await ft.emit_frontend_tool(name, args)
    except Exception:  # pragma: no cover — the helper never raises by contract
        return False
    return bool(result.get("ok"))


@_annotate(read_only=True, idempotent=True)
async def open_in_app(target: str, target_id: str = "") -> str:
    """Open something in the member's Projects page: target is task (a
    task's full_id), project (a node's full_id) or app (analytics or
    reports, as target_id). It reads the row first, then asks the open page
    to show it. It changes nothing. The result always carries a link, for
    a member who is not on the Projects page."""
    which = str(target or "").strip().lower()
    # A run with nobody to act as opens nothing, even an app that needs no
    # read: the same refusal every gateway call makes (`client.headers`).
    headers()
    if which not in TARGETS:
        return f"target is one of {', '.join(TARGETS)}."
    if which == "app":
        app = str(target_id or "").strip().lower()
        if app not in APPS:
            return f"app is one of {', '.join(APPS)}."
        sent = await _dispatch("projects.open_app", {"app": app})
        link = f"/projects?app={app}"
        head = f"Opening {app} in the Projects app." if sent else f"Open {app} here:"
        return f"{head}\n  link: {link}"
    if which == "task":
        tid = uuid_of(target_id, "task_id")
        task = await get(f"/projects/tasks/{tid}")
        sent = await _dispatch("projects.open_task", {"task_id": tid})
        label = f"#{task.get('task_number')} {data(task.get('title'))}"
        head = f"Opening {label}." if sent else f"Open {label} here:"
        return f"{head}\n  link: /projects?task={tid}\n  full_id: {tid}"
    pid = uuid_of(target_id, "project_id")
    node = await get(f"/projects/nodes/{pid}")
    sent = await _dispatch("projects.open_project", {"project_id": pid})
    label = data(node.get("name"))
    head = f"Opening {label}." if sent else f"Open {label} from the Projects tree."
    return f"{head}\n  project_id: {pid}"


__all__ = ["open_in_app"]
