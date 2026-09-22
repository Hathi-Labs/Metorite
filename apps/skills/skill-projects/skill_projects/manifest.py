"""The route manifest — every ``/projects`` route, and what the chat does with it.

Spec: ``project-docs/specs/projects_ai_chat.md`` §5.2 (the classes) and §7.1
(D-PM-37, the fence).

**This table is the chat's allowlist and its coverage record at once.** The
client (:mod:`skill_projects.client`) refuses a verb-plus-path the table does
not carry, and ``tests/unit/test_projects_chat_coverage.py`` walks the real
router and fails on a route the table does not carry. So a new Projects route
cannot ship until somebody decides what the chat does with it, and a tool
cannot reach a route nobody decided about. One table, two fences.

Classes:

    A   a read — no card
    B   a write the app can undo — one card, may list many rows
    C   a write that is hard to undo — one card per act, with counts
    X   not on the chat surface — ``reason`` says why

A row may name a tool that is not built yet. Every such tool is in
:data:`PLANNED` with the slice that builds it, and the fence asserts that.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "CLASSES",
    "COMPOSITE",
    "MANIFEST",
    "PLANNED",
    "READ_ONLY_POSTS",
    "Route",
    "allowed",
    "is_read",
    "reaches",
    "route_for",
    "tool_class",
    "tools_by_class",
]

CLASSES = ("A", "B", "C", "X")


@dataclass(frozen=True)
class Route:
    method: str
    #: The path template as FastAPI reports it, prefix included.
    path: str
    #: The tool that reaches this route, or ``""`` for class X.
    tool: str
    cls: str
    #: Required for class X. Why the chat does not reach this route.
    reason: str = ""

    def __post_init__(self) -> None:
        if self.cls not in CLASSES:
            raise ValueError(
                f"{self.method} {self.path}: class {self.cls!r} is not one of {CLASSES}"
            )
        if self.cls == "X" and not self.reason:
            raise ValueError(f"{self.method} {self.path}: an excluded route needs a reason")
        if self.cls == "X" and self.tool:
            raise ValueError(f"{self.method} {self.path}: an excluded route names no tool")
        if self.cls != "X" and not self.tool:
            raise ValueError(f"{self.method} {self.path}: class {self.cls} needs a tool")


#: Why the two hard deletes are not here. D-PM-35: the routes carry no
#: authority rule (H-121, WS-40), so a chat tool over them would let any
#: reader destroy a subtree with a card as the only brake.
_DELETE_REASON = (
    "D-PM-35 — hard delete waits for WS-40's authority rule. Archive is the chat's remove verb."
)
#: Delivery is the Reports app's, and arming the schedule is owner-gated
#: (§9.12.8). The chat renders a report. It never sends one.
_DELIVERY_REASON = (
    "Report delivery stays in the Reports app. Arming the schedule is owner-gated (§9.12.8)."
)
#: Writing who can see a project is a membership-shaped act. Owner question
#: 2 in the spec §12.
_GRANT_REASON = "A grant write is membership-shaped. Spec §12 question 2 holds it for the owner."
#: Per-client UI state and board order. The browser owns these.
_UI_STATE_REASON = "Client UI state. The browser writes it, and a chat has no view to keep."
#: The day planner belongs to the Calendar app and the Tasks assistant
#: (``calendar_ai_review.md`` §4). A second caller would be a second planner.
_PLANNER_REASON = (
    "The day planner is the Calendar app's and the Tasks assistant's (calendar_ai_review.md §4)."
)

MANIFEST: tuple[Route, ...] = (
    # ── tree.py ──────────────────────────────────────────────────────────
    Route("GET", "/projects/tree", "projects_tree", "A"),
    Route(
        "GET",
        "/projects/nodes",
        "",
        "X",
        "The flat form of /projects/tree. The chat reads the tree.",
    ),
    Route("GET", "/projects/nodes/{project_id}", "project_summary", "A"),
    Route("GET", "/projects/summary", "project_summary", "A"),
    Route("GET", "/projects/nodes/{project_id}/summary", "project_summary", "A"),
    Route("POST", "/projects/nodes", "create_project", "B"),
    Route("PATCH", "/projects/nodes/{project_id}", "update_project", "B"),
    Route("POST", "/projects/nodes/{project_id}/move", "move_project", "C"),
    Route("DELETE", "/projects/nodes/{project_id}", "", "X", _DELETE_REASON),
    Route("POST", "/projects/nodes/{project_id}/archive", "archive_project", "C"),
    Route("POST", "/projects/nodes/{project_id}/unarchive", "unarchive_project", "C"),
    Route("GET", "/projects/nodes/{project_id}/grants", "project_access", "A"),
    Route("POST", "/projects/nodes/{project_id}/grants", "", "X", _GRANT_REASON),
    Route("DELETE", "/projects/nodes/{project_id}/grants/{grant_id}", "", "X", _GRANT_REASON),
    # ── tasks.py ─────────────────────────────────────────────────────────
    Route("GET", "/projects/tasks", "list_tasks", "A"),
    Route("GET", "/projects/tasks/{task_id}", "task_detail", "A"),
    Route("POST", "/projects/tasks", "create_task", "B"),
    Route("PATCH", "/projects/tasks/{task_id}", "update_task", "B"),
    Route("POST", "/projects/tasks/{task_id}/move", "move_task", "B"),
    Route("DELETE", "/projects/tasks/{task_id}", "", "X", _DELETE_REASON),
    Route("POST", "/projects/tasks/{task_id}/archive", "archive_task", "C"),
    Route("POST", "/projects/tasks/{task_id}/unarchive", "unarchive_task", "B"),
    Route("PUT", "/projects/tasks/{task_id}/assignees", "assign", "B"),
    Route("POST", "/projects/tasks/{task_id}/links", "link_tasks", "B"),
    Route("DELETE", "/projects/tasks/{task_id}/links/{link_id}", "unlink_tasks", "B"),
    # ── bulk.py · move.py · merge.py ─────────────────────────────────────
    Route("POST", "/projects/tasks/bulk", "bulk_update", "C"),
    Route("POST", "/projects/tasks/move/preview", "move_task", "B"),
    Route("POST", "/projects/tasks/move", "move_task", "B"),
    Route("POST", "/projects/tasks/{task_id}/merge", "merge_tasks", "C"),
    # ── activities.py ────────────────────────────────────────────────────
    Route("GET", "/projects/tasks/{task_id}/timeline", "task_detail", "A"),
    Route("POST", "/projects/tasks/{task_id}/comments", "comment", "B"),
    Route("PATCH", "/projects/comments/{activity_id}", "edit_comment", "B"),
    Route("DELETE", "/projects/comments/{activity_id}", "delete_comment", "C"),
    Route("POST", "/projects/activities/{activity_id}/revert", "revert_activity", "C"),
    # ── admin.py — statuses, status sets, types ──────────────────────────
    Route("GET", "/projects/nodes/{project_id}/statuses", "vocabulary", "A"),
    Route("POST", "/projects/nodes/{project_id}/statuses", "create_status", "B"),
    Route("PATCH", "/projects/statuses/{status_id}", "update_status", "B"),
    Route("DELETE", "/projects/statuses/{status_id}", "delete_status", "C"),
    Route("GET", "/projects/nodes/{project_id}/status-set", "vocabulary", "A"),
    Route("POST", "/projects/nodes/{project_id}/status-set/preview", "set_status_set", "C"),
    Route("POST", "/projects/nodes/{project_id}/status-set", "set_status_set", "C"),
    Route("GET", "/projects/nodes/{project_id}/types", "vocabulary", "A"),
    Route("POST", "/projects/nodes/{project_id}/types", "create_type", "B"),
    Route("PATCH", "/projects/types/{type_id}", "update_type", "B"),
    Route("DELETE", "/projects/types/{type_id}", "delete_type", "C"),
    # ── custom_fields.py ─────────────────────────────────────────────────
    Route("GET", "/projects/nodes/{project_id}/fields", "vocabulary", "A"),
    Route("POST", "/projects/nodes/{project_id}/fields", "create_field", "B"),
    Route("PATCH", "/projects/fields/{field_id}", "update_field", "B"),
    Route("DELETE", "/projects/fields/{field_id}", "delete_field", "C"),
    # ── tags.py ──────────────────────────────────────────────────────────
    Route("GET", "/projects/nodes/{project_id}/tags", "vocabulary", "A"),
    Route("POST", "/projects/nodes/{project_id}/tags", "create_tag", "B"),
    Route("PATCH", "/projects/tags/{tag_id}", "update_tag", "B"),
    Route("GET", "/projects/tags/{tag_id}/impact", "delete_tag", "C"),
    Route("POST", "/projects/tags/{tag_id}/merge", "merge_tags", "C"),
    Route("DELETE", "/projects/tags/{tag_id}", "delete_tag", "C"),
    # ── views.py ─────────────────────────────────────────────────────────
    Route("GET", "/projects/nodes/{project_id}/views", "views", "A"),
    Route("POST", "/projects/nodes/{project_id}/views", "save_view", "B"),
    Route("PATCH", "/projects/views/{view_id}", "save_view", "B"),
    Route("DELETE", "/projects/views/{view_id}", "delete_view", "C"),
    Route("GET", "/projects/views/{view_id}/state", "", "X", _UI_STATE_REASON),
    Route("PUT", "/projects/views/{view_id}/state", "", "X", _UI_STATE_REASON),
    Route("GET", "/projects/views/{view_id}/positions", "", "X", _UI_STATE_REASON),
    Route("PUT", "/projects/views/{view_id}/positions", "", "X", _UI_STATE_REASON),
    # ── me.py · personal.py ──────────────────────────────────────────────
    Route("GET", "/projects/assigned-to-me", "my_work", "A"),
    Route("GET", "/projects/my/project", "my_work", "A"),
    Route(
        "POST",
        "/projects/my/project",
        "",
        "X",
        "The first capture creates the personal project itself "
        "(personal.py ensure_personal_project). A second door is redundant.",
    ),
    Route("POST", "/projects/my/tasks", "create_personal_task", "B"),
    Route("PATCH", "/projects/tasks/{task_id}/personal", "set_my_overlay", "B"),
    Route("GET", "/projects/my/inbox", "my_work", "A"),
    Route("GET", "/projects/my/tasks/{task_id}", "my_task", "A"),
    Route("GET", "/projects/my/calendar", "calendar", "A"),
    Route("GET", "/projects/my/contexts", "my_contexts", "A"),
    Route("POST", "/projects/tasks/{task_id}/complete", "complete", "B"),
    Route("POST", "/projects/tasks/{task_id}/defer", "defer", "B"),
    # ── planning.py ──────────────────────────────────────────────────────
    Route("POST", "/projects/my/calendar/plan", "", "X", _PLANNER_REASON),
    Route("POST", "/projects/my/calendar/replan", "", "X", _PLANNER_REASON),
    Route("POST", "/projects/my/calendar/rollover", "", "X", _PLANNER_REASON),
    Route("GET", "/projects/my/calendar/estimate-stats", "", "X", _PLANNER_REASON),
    # ── calendar.py · search.py · export.py · delta.py ───────────────────
    Route("GET", "/projects/calendar", "calendar", "A"),
    Route("GET", "/projects/search", "find_tasks", "A"),
    Route(
        "GET",
        "/projects/export/tasks.csv",
        "",
        "X",
        "A file download. The app's export button owns it.",
    ),
    Route(
        "GET",
        "/projects/delta/tasks",
        "",
        "X",
        "The sync feed for a client cache. A chat holds no cache.",
    ),
    # ── assignees.py ─────────────────────────────────────────────────────
    Route("GET", "/projects/people/names", "people_for", "A"),
    Route("GET", "/projects/assignees", "people_for", "A"),
    # ── analytics.py · reports.py ────────────────────────────────────────
    Route("GET", "/projects/analytics/stuck", "analytics_stuck", "A"),
    Route("GET", "/projects/analytics/load", "analytics_load", "A"),
    Route("GET", "/projects/analytics/throughput", "analytics_throughput", "A"),
    Route("GET", "/projects/analytics/finished", "analytics_finished", "A"),
    Route("GET", "/projects/analytics/outlook", "analytics_outlook", "A"),
    Route("GET", "/projects/reports", "report_list", "A"),
    Route("POST", "/projects/reports", "report_save", "B"),
    Route("GET", "/projects/reports/{report_id}", "report_render", "A"),
    Route("PATCH", "/projects/reports/{report_id}", "report_save", "B"),
    Route("DELETE", "/projects/reports/{report_id}", "report_delete", "C"),
    Route("GET", "/projects/reports/{report_id}/render", "report_render", "A"),
    Route("GET", "/projects/reports/{report_id}/recipients", "", "X", _DELIVERY_REASON),
    Route("POST", "/projects/reports/{report_id}/recipients", "", "X", _DELIVERY_REASON),
    Route("DELETE", "/projects/reports/{report_id}/recipients/{email}", "", "X", _DELIVERY_REASON),
    Route("PATCH", "/projects/reports/{report_id}/schedule", "", "X", _DELIVERY_REASON),
    # ── attachments.py ───────────────────────────────────────────────────
    Route(
        "POST",
        "/projects/tasks/{task_id}/attachments",
        "",
        "X",
        "A file upload. The chat has no file input, so the browser owns it.",
    ),
    Route("GET", "/projects/tasks/{task_id}/attachments", "task_detail", "A"),
    Route(
        "GET",
        "/projects/attachments/{attachment_id}/{filename}",
        "",
        "X",
        "Raw file bytes. A chat answer carries text.",
    ),
    Route(
        "DELETE", "/projects/tasks/{task_id}/attachments/{attachment_id}", "delete_attachment", "C"
    ),
    # ── recurrence.py · relations.py · watchers.py ───────────────────────
    Route("GET", "/projects/tasks/{task_id}/recurrence", "recurrence", "A"),
    Route("PUT", "/projects/tasks/{task_id}/recurrence", "set_recurrence", "B"),
    Route("DELETE", "/projects/tasks/{task_id}/recurrence", "set_recurrence", "B"),
    Route("GET", "/projects/tasks/{task_id}/relations", "task_detail", "A"),
    Route("PUT", "/projects/tasks/{task_id}/watch", "watch", "B"),
    Route("DELETE", "/projects/tasks/{task_id}/watch", "watch", "B"),
    Route("GET", "/projects/tasks/{task_id}/watchers", "watchers", "A"),
    Route("PUT", "/projects/nodes/{project_id}/watch", "watch", "B"),
    Route("DELETE", "/projects/nodes/{project_id}/watch", "watch", "B"),
    Route("GET", "/projects/nodes/{project_id}/watchers", "watchers", "A"),
    # ── intake.py · notifications.py ─────────────────────────────────────
    Route("POST", "/projects/intake", "capture_intake", "B"),
    Route("GET", "/projects/intake", "intake_queue", "A"),
    Route("POST", "/projects/intake/{task_id}/accept", "triage_intake", "B"),
    Route("POST", "/projects/intake/{task_id}/decline", "triage_intake", "B"),
    Route("POST", "/projects/intake/{task_id}/duplicate", "triage_intake", "B"),
    Route("POST", "/projects/intake/{task_id}/snooze", "triage_intake", "B"),
    Route("GET", "/projects/notifications", "notifications", "A"),
    Route("POST", "/projects/notifications/read", "mark_notifications_read", "B"),
)

#: Tools the manifest names that no slice has built yet, with the slice that
#: builds each. The coverage fence holds this against ``__all__``: a tool is
#: exported OR it is here, never both and never neither.
PLANNED: dict[str, str] = {
    # S2 (2026-09-22) shipped the fifteen daily class B verbs. S2b
    # (2026-09-23) shipped the rest of class B and the two reads it needed
    # (`recurrence`, `my_task`).
    # S3 (2026-09-23) shipped the seventeen class C acts (`guarded.py`).
    # S4 — workflows and the reads they need
    "project_access": "S4",
    "views": "S4",
    "save_view": "S4",
    "calendar": "S4",
    "my_contexts": "S4",
    "watchers": "S4",
    "capture_intake": "S4",
    "intake_queue": "S4",
    "triage_intake": "S4",
    "notifications": "S4",
    "mark_notifications_read": "S4",
}


#: A COMPOSITE tool writes through another tool's routes under ONE card, so
#: the member signs once for one act. ``create_task`` assigns through
#: ``assign``'s route after the create; ``add_subtasks`` creates through
#: ``create_task``'s. The class of a composite is the class of what it
#: reaches. ``test_projects_agent_writes.py`` holds every non-GET a tool
#: issues to its own routes or to these.
COMPOSITE: dict[str, frozenset[str]] = {
    "create_task": frozenset({"assign"}),
    "add_subtasks": frozenset({"create_task"}),
    # The create route's INSERT has no `required` column; the flag is a
    # PATCH under the same card (S2b verifier).
    "create_field": frozenset({"update_field"}),
}

#: POST routes that WRITE NOTHING. A preview computes what an act would do
#: and returns it. The fences treat these as reads, so a tool may call one
#: before its card — it is how the card gets its numbers (D-PM-29).
READ_ONLY_POSTS: frozenset[tuple[str, str]] = frozenset(
    {
        ("POST", "/projects/tasks/move/preview"),
        ("POST", "/projects/nodes/{project_id}/status-set/preview"),
    }
)


def tool_class(name: str) -> str | None:
    """The class a tool acts at: its own rows, or its composite's."""
    own = {r.cls for r in MANIFEST if r.tool == name}
    if own:
        return sorted(own)[0]
    reached = COMPOSITE.get(name)
    if not reached:
        return None
    classes = {c for t in reached for c in [tool_class(t)] if c}
    return sorted(classes)[-1] if classes else None


def tools_by_class(cls: str) -> set[str]:
    """Every tool name the manifest puts in ``cls``, composites included."""
    direct = {r.tool for r in MANIFEST if r.cls == cls and r.tool}
    return direct | {t for t in COMPOSITE if tool_class(t) == cls}


def reaches(tool: str, route_tool: str) -> bool:
    """May ``tool`` issue a call on a route the manifest gives ``route_tool``?"""
    if tool == route_tool:
        return True
    return any(reaches(t, route_tool) for t in COMPOSITE.get(tool, ()))


def is_read(method: str, path: str) -> bool:
    """A GET, or a POST the manifest records as writing nothing."""
    if method.upper() == "GET":
        return True
    row = route_for(method, path)
    return row is not None and (row.method, row.path) in READ_ONLY_POSTS


def _template_regex(template: str) -> re.Pattern[str]:
    parts = re.split(r"(\{[^}]+\})", template)
    out = "".join("[^/]+" if p.startswith("{") else re.escape(p) for p in parts)
    return re.compile(f"^{out}$")


_COMPILED: tuple[tuple[Route, re.Pattern[str]], ...] = tuple(
    (r, _template_regex(r.path)) for r in MANIFEST
)


def route_for(method: str, path: str) -> Route | None:
    """The manifest row a concrete ``method path`` falls under, or ``None``.

    ``path`` is the concrete path with ids filled in and no query string.
    """
    bare = path.split("?", 1)[0]
    verb = method.upper()
    for route, pattern in _COMPILED:
        if route.method == verb and pattern.match(bare):
            return route
    return None


def allowed(method: str, path: str) -> tuple[bool, str]:
    """May a tool issue ``method path``? ``(ok, why_not)``.

    The client asks this before it builds a request. A route the manifest does
    not carry is refused, and so is a class X route: both are decisions
    nobody took, or took the other way.
    """
    route = route_for(method, path)
    if route is None:
        return False, f"{method.upper()} {path} is not in the Projects route manifest."
    if route.cls == "X":
        return False, f"{method.upper()} {path} is not on the chat surface: {route.reason}"
    return True, ""
