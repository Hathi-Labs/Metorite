"""ARC-2b fence (R7): every task-write route says how it treats an archived project.

Spec: ``project-docs/specs/one_projects_app.md`` §9b.6 rule 2.

The test walks the real ``/projects`` router. Each mutating route that writes
a task (its path holds ``/tasks``, ``/intake``, ``/comments``, ``/activities``
or ``/views/{view_id}/positions``) must be in ``GUARDED`` or in ``OPEN``. A new
route in neither fails, so a writer cannot ship without a decision. A route in
``GUARDED`` names the function that holds the guard call, and that function
must call one of the guard helpers of ``core``. A route in ``OPEN`` carries its
reason.

Hermetic: it reads source and routes and opens no database. The behaviour is
proved by ``test_projects_archived_readonly_r8.py``.
"""

from __future__ import annotations

import importlib
import inspect
import re

from gateway.routes.projects import router

GUARD_CALLS = (
    "assert_task_project_open(",
    "assert_task_id_project_open(",
    "assert_project_open_for_writes(",
    "project_is_archived(",
    "task_write=True",
)

_WRITES_TASKS = re.compile(
    r"/tasks|/intake|/comments|/activities|/views/\{view_id\}/positions",
)

#: ``"METHOD /projects/path"`` -> ``(module, function)`` that holds the guard.
GUARDED: dict[str, tuple[str, str]] = {
    "POST /projects/tasks": ("tasks", "create_task_in"),
    "PATCH /projects/tasks/{task_id}": ("tasks", "patch_task"),
    "POST /projects/tasks/{task_id}/move": ("tasks", "move_task_in"),
    "DELETE /projects/tasks/{task_id}": ("tasks", "delete_task"),
    "POST /projects/tasks/{task_id}/agent-delete": ("tasks", "agent_delete_task"),
    "POST /projects/tasks/{task_id}/archive": ("tasks", "archive_task"),
    "POST /projects/tasks/{task_id}/unarchive": ("tasks", "unarchive_task"),
    "PUT /projects/tasks/{task_id}/assignees": ("tasks", "set_assignees"),
    "POST /projects/tasks/{task_id}/claim": ("tasks", "claim_task"),
    "POST /projects/tasks/{task_id}/links": ("tasks", "create_link"),
    "DELETE /projects/tasks/{task_id}/links/{link_id}": ("tasks", "delete_link"),
    "POST /projects/tasks/bulk": ("bulk", "bulk_edit"),
    "POST /projects/tasks/move": ("move", "_plan"),
    "POST /projects/tasks/move/preview": ("move", "_plan"),
    "POST /projects/tasks/{task_id}/attachments": ("attachments", "attach_file"),
    "DELETE /projects/tasks/{task_id}/attachments/{attachment_id}": (
        "attachments", "detach_file"),
    "POST /projects/tasks/{task_id}/comments": ("activities", "add_comment"),
    "PATCH /projects/comments/{activity_id}": ("activities", "edit_comment"),
    "DELETE /projects/comments/{activity_id}": ("activities", "delete_comment"),
    "POST /projects/activities/{activity_id}/revert": ("activities", "revert_change"),
    "POST /projects/tasks/{task_id}/refs": ("refs", "add_ref"),
    "DELETE /projects/tasks/{task_id}/refs/{ref_id}": ("refs", "delete_ref"),
    "PUT /projects/tasks/{task_id}/recurrence": ("recurrence", "set_recurrence"),
    "DELETE /projects/tasks/{task_id}/recurrence": ("recurrence", "clear_recurrence"),
    "POST /projects/tasks/{task_id}/merge": ("merge", "merge_tasks"),
    "PUT /projects/views/{view_id}/positions": ("views", "_load_visible_view"),
    "POST /projects/intake": ("intake", "capture_intake"),
    "POST /projects/intake/{task_id}/accept": ("intake", "accept_intake"),
    "POST /projects/intake/{task_id}/decline": ("intake", "decline_intake"),
    "POST /projects/intake/{task_id}/duplicate": ("intake", "duplicate_intake"),
    "POST /projects/intake/{task_id}/snooze": ("intake", "snooze_intake"),
    "POST /projects/my/tasks": ("personal", "create_personal_task"),
    "POST /projects/my/tasks/batch": ("personal", "create_personal_task"),
    "POST /projects/tasks/{task_id}/complete": ("personal", "complete_task"),
    "POST /projects/my/tasks/{task_id}/organize": ("personal", "_organize"),
    "POST /projects/my/tasks/{task_id}/subtasks": ("personal", "create_personal_task"),
}

#: Routes that stay open, with the reason.
OPEN: dict[str, str] = {
    "PUT /projects/tasks/{task_id}/watch": "A watch is the member's own subscription.",
    "DELETE /projects/tasks/{task_id}/watch": "A watch is the member's own subscription.",
    "PATCH /projects/tasks/{task_id}/personal": "The overlay (pm_task_personal) is not project work.",
    "POST /projects/tasks/{task_id}/defer": "The overlay: it hides the task from my list only.",
    "POST /projects/tasks/{task_id}/nudge": "An in-app notice to a person, with a stamp on my overlay.",
    "DELETE /projects/my/tasks/{task_id}": "The purge of a task in my own personal tree.",
    "POST /projects/tasks/{task_id}/asks": "A question to a member (decisions), not a task field.",
    "POST /projects/cycles/{cycle_id}/tasks": "Cycle membership is plan data; cycles refuse an archived node.",
    "DELETE /projects/cycles/{cycle_id}/tasks/{task_id}": "Cycle membership is plan data.",
    "POST /projects/tasks/{task_id}/agent-runs": "Agent-run route: a run keeps its own lifecycle.",
    "POST /projects/agent-runs/{run_id}/activities": "Agent-run route.",
    "POST /projects/tasks/{task_id}/archive-request": "Agent-run route (agent_runs.py).",
}


def _routes() -> dict[str, str]:
    found: dict[str, str] = {}
    for route in router.routes:
        methods = getattr(route, "methods", None) or set()
        for method in methods & {"POST", "PUT", "PATCH", "DELETE"}:
            if _WRITES_TASKS.search(route.path):
                found[f"{method} {route.path}"] = route.endpoint.__module__
    return found


def test_every_task_write_route_is_classified() -> None:
    routes = _routes()
    unknown = sorted(k for k in routes if k not in GUARDED and k not in OPEN)
    assert unknown == [], (
        "These routes write a task and are neither guarded against an archived "
        "project (core.assert_task_project_open and friends) nor listed as open "
        f"with a reason: {unknown}"
    )
    stale = sorted(k for k in (*GUARDED, *OPEN) if k not in routes)
    assert stale == [], f"These entries match no route any more: {stale}"
    assert not set(GUARDED) & set(OPEN)


def test_each_guarded_route_calls_a_guard() -> None:
    missing = []
    for key, (module, name) in sorted(GUARDED.items()):
        mod = importlib.import_module(f"gateway.routes.projects.{module}")
        source = inspect.getsource(getattr(mod, name))
        if not any(call in source for call in GUARD_CALLS):
            missing.append(f"{key} -> {module}.{name}")
    assert missing == [], f"These functions hold no archived-project guard: {missing}"


def test_agent_run_routes_never_call_the_guard() -> None:
    """A run keeps its own lifecycle, so an agent can finish or release it."""
    mod = importlib.import_module("gateway.routes.projects.agent_runs")
    source = inspect.getsource(mod)
    for call in GUARD_CALLS:
        assert call not in source, f"agent_runs.py must not call {call}"


def test_the_sentence_is_the_spec_sentence() -> None:
    from gateway.routes.projects.core import ARCHIVED_PROJECT_REFUSAL

    assert ARCHIVED_PROJECT_REFUSAL == (
        "This project is archived. Restore it to change its work."
    )
