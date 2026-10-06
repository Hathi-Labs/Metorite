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

WS-46 P4 (D91) takes the same rule from the route to the FIELD. :data:`SENDS`,
:data:`FIELD_EXEMPT` and :data:`FIELD_PLANNED` hold every field of a mapped
route's request, and ``tests/unit/test_projects_field_parity.py`` (fence F2)
reads the fields from the real router.

WS-46 P5 takes it to the UI client METHOD. :data:`UI_ACTIONS`,
:data:`UI_EXEMPT` and :data:`UI_PLANNED` hold every method of the Projects
client, and ``tests/unit/test_projects_ui_actions.py`` (fence F3) reads the
methods and the routes they call from the TypeScript source.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "CLASSES",
    "COMPOSITE",
    "FIELD_EXEMPT",
    "FIELD_GAPS",
    "FIELD_PLANNED",
    "IDENTITY_HEADERS",
    "MANIFEST",
    "PLANNED",
    "READ_ONLY_POSTS",
    "SENDS",
    "UI_ACTIONS",
    "UI_EXEMPT",
    "UI_PLANNED",
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
#: Writing who can see a project is a membership-shaped act. D-PM-40, the
#: owner's answer of 2026-09-29 to spec §12 question 2.
_GRANT_REASON = (
    "D-PM-40 — the chat does not write grants. Propose the grant in words, "
    "and a person applies it in the app."
)
#: Per-client UI state and board order. The browser owns these.
_UI_STATE_REASON = "Client UI state. The browser writes it, and a chat has no view to keep."
#: The day planner belongs to the Calendar app and the Tasks assistant
#: (``calendar_ai_review.md`` §4). A second caller would be a second planner.
_PLANNER_REASON = (
    "The day planner is the Calendar app's and the Tasks assistant's (calendar_ai_review.md §4)."
)
#: A nudge tells a COLLEAGUE you are waiting on them. That is contacting a
#: third party on the member's behalf, which CLAUDE.md §3a rule 3 holds for the
#: owner. §9.12.9 also makes the act deliberately explicit — "a follow-up that
#: always pings somebody is a tool people stop using" — and a chat tool is the
#: shortest road to one that does. An assistant that chases people for you is a
#: different product decision from one that reads and edits your own work, so
#: this waits for an answer instead of assuming one. The member presses Nudge
#: on the Waiting-For row.
_NUDGE_REASON = (
    "A nudge contacts a colleague. §9.12.9 makes it an explicit human act, and "
    "whether the chat may chase somebody is an owner question."
)
#: A file import is an admin's upload through a wizard (project_import.md §7.7).
_IMPORT_REASON = (
    "A file import is an admin's upload and review through the import wizard "
    "(project_import.md §7.7). A chat holds no file and makes no bulk write."
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
    Route("GET", "/projects/nodes/{project_id}/views", "project_views", "A"),
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
    Route(
        "POST",
        "/projects/my/tasks/batch",
        "",
        "X",
        "The multi-line capture box (WS-39 S6a). The chat captures one thought "
        "per turn through create_personal_task; a paste of twelve lines is a "
        "browser gesture.",
    ),
    Route(
        "POST",
        "/projects/my/tasks/{task_id}/organize",
        "",
        "X",
        "The Clarify card's one-transaction decision (WS-39 S6a). The chat "
        "holds each half as its own tool: set_my_overlay, move_task, assign, "
        "complete, add_subtasks.",
    ),
    Route(
        "POST",
        "/projects/my/tasks/{task_id}/subtasks",
        "",
        "X",
        "My Tasks' checklist door for new steps (D-PM-38 S4): the organize "
        "helper, so a step lands in the parent's open lane and states NEXT "
        "under a NEXT parent. The chat adds steps through add_subtasks, on "
        "POST /tasks. Both doors take the lane from core.parent_lane_status "
        "(spec 11.42). Only the NEXT rule stays on this door.",
    ),
    Route("PATCH", "/projects/tasks/{task_id}/personal", "set_my_overlay", "B"),
    # WS-39 S6e — `?untriaged=true` is a query flag on this same route, so
    # it needs no row: the manifest keys on the verb and the path, and
    # `my_work` may pass the flag ("what landed on my plate").
    Route("GET", "/projects/my/inbox", "my_work", "A"),
    Route("GET", "/projects/my/tasks/{task_id}", "my_task", "A"),
    # WS-39 S6g — My Tasks' purge: the hard delete that finalises a soft
    # delete, and only for a task in the caller's personal tree. A delete is
    # never a chat act, for the reason `DELETE /projects/tasks/{id}` gives.
    Route("DELETE", "/projects/my/tasks/{task_id}", "", "X", _DELETE_REASON),
    Route(
        "GET",
        "/projects/my/tasks/{task_id}/lanes",
        "",
        "X",
        "The task panel's Status select (WS-39 S6e): the lanes one of my "
        "tasks can be in, behind the membership check rather than the project "
        "grant. The chat reads a lane through my_task's workflow_stage and "
        "the vocabulary of a project through `vocabulary`; a second door onto "
        "the same lane list is a browser need, not a chat one.",
    ),
    Route("GET", "/projects/my/calendar", "calendar", "A"),
    Route("GET", "/projects/my/contexts", "my_contexts", "A"),
    # WS-39 S6e — the projects I lead, with their open work and mine.
    Route("GET", "/projects/my/led", "my_led_projects", "A"),
    # WS-39 S6b — a member's own categories. The READ is on the surface,
    # because "file this under Home" needs to know Home exists. The three
    # writes are not, and that is a decision rather than an oversight: an
    # Area is the member's own filing scheme, the names are theirs and often
    # near-duplicates ("Home" / "House"), and an assistant that renames or
    # deletes one on a guess is the kind of help nobody asked for. S6c owns
    # the promote door; if the chat should ever shape a member's tree, that
    # is its own decision with its own card.
    Route("GET", "/projects/my/areas", "my_areas", "A"),
    Route(
        "POST",
        "/projects/my/areas",
        "",
        "X",
        "A member's categories are theirs to make. The chat can file a task "
        "into one (my_areas reads them), and minting one is a shaping act "
        "the member should perform where they can see the whole list.",
    ),
    Route(
        "PATCH",
        "/projects/my/areas/{area_id}",
        "",
        "X",
        "Renaming somebody's own category on a guess. Two of a member's "
        "areas are often near-synonyms, and the chat cannot tell which was "
        "meant from a sentence.",
    ),
    Route(
        "DELETE",
        "/projects/my/areas/{area_id}",
        "",
        "X",
        "An empty area is hard-deleted by this route. That is not a verb to "
        "reach through a guess at which area was meant.",
    ),
    Route("POST", "/projects/tasks/{task_id}/complete", "complete", "B"),
    Route("POST", "/projects/tasks/{task_id}/defer", "defer", "B"),
    Route("POST", "/projects/tasks/{task_id}/nudge", "", "X", _NUDGE_REASON),
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
    # ── imports.py (WS-41, D80) ──────────────────────────────────────────
    Route("POST", "/projects/import/runs", "", "X", _IMPORT_REASON),
    Route("GET", "/projects/import/runs", "", "X", _IMPORT_REASON),
    Route("GET", "/projects/import/runs/{run_id}", "", "X", _IMPORT_REASON),
    Route("PUT", "/projects/import/runs/{run_id}/mapping", "", "X", _IMPORT_REASON),
    Route("POST", "/projects/import/runs/{run_id}/apply", "", "X", _IMPORT_REASON),
    Route("POST", "/projects/import/runs/{run_id}/discard", "", "X", _IMPORT_REASON),
    # ── vocabulary.py (WS-42 PS-3) ───────────────────────────────────────
    Route(
        "GET",
        "/projects/vocabulary",
        "",
        "X",
        "A settings list for people. The chat reads a space's effective tags, "
        "fields and types through the per-space reads, which include these rows.",
    ),
    Route(
        "GET",
        "/projects/vocabulary/{kind}/{row_id}/impact",
        "",
        "X",
        "The count shown before an admin deletes or merges a shared entry in "
        "Projects settings. The chat neither deletes nor merges shared entries.",
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
    # S7a — capacity (analytics_capacity.py). The HR tier is the route's.
    Route("GET", "/projects/analytics/capacity", "team_capacity", "A"),
    # S7b — fit and rebalancing (candidates.py, analytics_rebalance.py).
    # `fit_for_task`, not `suggest_assignees`: that is the picker's route
    # function in assignees.py. The HR tier is the routes'.
    Route("GET", "/projects/tasks/{task_id}/candidates", "fit_for_task", "A"),
    Route("GET", "/projects/candidates", "fit_for_task", "A"),
    Route("GET", "/projects/analytics/rebalance", "rebalance", "A"),
    # S7c — conflicts (analytics_conflicts.py). The HR kinds are the route's.
    Route("GET", "/projects/analytics/conflicts", "find_conflicts", "A"),
    # S7e — on-the-fly analysis (analytics_dataset.py). The server groups;
    # the per-person values are the route's HR tier.
    Route("GET", "/projects/analytics/dataset", "task_dataset", "A"),
    # S7d — the plan preview (plan_preview.py). It writes nothing, so it is
    # in READ_ONLY_POSTS. Its class is the tool's: propose_plan is class B.
    Route("POST", "/projects/plan/preview", "propose_plan", "B"),
    Route("GET", "/projects/reports", "report_list", "A"),
    # WS-27bn R2 - the template gallery. It must sit ABOVE the
    # {report_id} row, because route_for takes the first match. R-final:
    # render_report resolves a template name through it.
    Route("GET", "/projects/reports/templates", "render_report", "A"),
    # WS-27bn R5a - the picker's subjects. Above {report_id} for the same
    # reason as the templates row. R-final: render_report resolves a person
    # or a team through it. It lists only what the reader may report on.
    Route("GET", "/projects/reports/subjects", "render_report", "A"),
    Route("POST", "/projects/reports", "report_save", "B"),
    Route("GET", "/projects/reports/{report_id}", "report_render", "A"),
    Route("PATCH", "/projects/reports/{report_id}", "report_save", "B"),
    Route("DELETE", "/projects/reports/{report_id}", "report_delete", "C"),
    Route("GET", "/projects/reports/{report_id}/render", "report_render", "A"),
    # WS-27bn R1 - the builder's unsaved preview. It writes nothing, so it
    # is in READ_ONLY_POSTS. R-final: render_report renders a template and a
    # subject through it, so the server runs the §7.1 check.
    Route("POST", "/projects/reports/preview", "render_report", "A"),
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
    # WS-39 S6b (2026-09-23) added the personal-areas read.
    "my_areas": "S6b",
    # S4 (2026-09-23) shipped the workflows, the views and the forms. S5
    # (2026-09-23) shipped the rest: views, calendar, contexts, watchers,
    # intake, notifications, grants (`inbox.py`). Every other tool the
    # manifest names is built.
}


#: A COMPOSITE tool writes through another tool's routes under ONE card, so
#: the member signs once for one act. ``create_task`` assigns through
#: ``assign``'s route after the create; ``add_subtasks`` creates through
#: ``create_task``'s. The class of a composite is the class of what it
#: reaches. ``test_projects_agent_writes.py`` holds every non-GET a tool
#: issues to its own routes or to these.
COMPOSITE: dict[str, frozenset[str]] = {
    # WS-46 P1 (D91): a repeating task is one act, so the rule is the second
    # write under the create's one card (`PUT …/recurrence`).
    "create_task": frozenset({"assign", "set_recurrence"}),
    # WS-46 P1: the detail prints "Repeats:" from the `recurrence` read.
    "task_detail": frozenset({"recurrence"}),
    "add_subtasks": frozenset({"create_task"}),
    # The create route's INSERT has no `required` column; the flag is a
    # PATCH under the same card (S2b verifier).
    "create_field": frozenset({"update_field"}),
    # S4 — the views read through the reads' routes and draw a template.
    "render_timeline": frozenset({"task_detail"}),
    "render_board": frozenset({"project_summary", "vocabulary", "list_tasks"}),
    "render_tasks": frozenset({"list_tasks", "vocabulary"}),
    "render_report": frozenset({"report_render"}),
    "status_report": frozenset(
        {"project_summary", "analytics_stuck", "analytics_load", "analytics_outlook"}
    ),
    # S4 — the forms draw an editable card, then write through the class B
    # tools' routes under those tools' own confirmation card.
    # S5 — three reads name the row through another read's route.
    "watchers": frozenset({"task_detail", "project_summary"}),
    "project_access": frozenset({"project_summary"}),
    "project_views": frozenset({"project_summary"}),
    "edit_task": frozenset({"update_task"}),
    "edit_project": frozenset({"update_project"}),
    # S7d — the plan also writes its `blocks` links under the same card.
    "propose_plan": frozenset({"create_project", "create_task", "link_tasks"}),
    # S6 — navigation reads the row it opens, then dispatches to the page.
    "open_in_app": frozenset({"task_detail", "project_summary"}),
}

#: POST routes that WRITE NOTHING. A preview computes what an act would do
#: and returns it. The fences treat these as reads, so a tool may call one
#: before its card — it is how the card gets its numbers (D-PM-29).
READ_ONLY_POSTS: frozenset[tuple[str, str]] = frozenset(
    {
        ("POST", "/projects/tasks/move/preview"),
        ("POST", "/projects/nodes/{project_id}/status-set/preview"),
        ("POST", "/projects/plan/preview"),
        ("POST", "/projects/reports/preview"),
    }
)


# ── WS-46 P4 (D91, fence F2) — every request FIELD, not only every route ─────
#
# Spec: ``project-docs/specs/projects_agent_parity.md`` §6.3. D-PM-37 holds
# each ROUTE to a decision. These three tables hold each FIELD of a mapped
# route's request to one. ``tests/unit/test_projects_field_parity.py`` reads
# the fields from FastAPI's own ``route.dependant`` (the body model's fields,
# the query parameters and the route's own headers), and fails on a field
# that is in none of the three, or in more than one. So a new field on a
# Projects route cannot merge until its author decides what the chat does
# with it. Class X routes have no tool, and their fields are not held here.
#
# The wire name is the key: an alias (``from``, ``If-Match``) where the route
# declares one. A dict body with no model is one field, the parameter name.

#: Headers a route's DEPENDENCY declares. They are the auth seam, and the
#: client sets each one from the run binding, never from a tool argument
#: (R11, user_management_contract.md). The fence ignores these four and
#: fails on any other header a dependency adds.
IDENTITY_HEADERS: frozenset[str] = frozenset(
    {"x-user-email", "x-user-role", "authorization", "x-actor-via"}
)

#: For each mapped route: request field -> the tool that sends it. The value is
#: ``"tool.argument"`` when an argument sets the field, or ``"tool"`` when the
#: tool sets it by itself (a fixed page size, ``include_subtree``). One witness
#: per field is enough. The fence calls each witness through the fake gateway
#: and asserts that the request carries the field, so a claim the tool does
#: not honour fails.
SENDS: dict[tuple[str, str], dict[str, str]] = {
    # ── tasks ────────────────────────────────────────────────────────────
    ("GET", "/projects/tasks"): {
        "project_id": "list_tasks.project_id",
        "include_subtree": "list_tasks.include_subtree",
        "q": "list_tasks.query",
        "include_archived": "list_tasks.include_archived",
        "status_category": "list_tasks.status_category",
        "assignees": "list_tasks.assignee",
        "unassigned": "list_tasks.unassigned",
        "overdue": "list_tasks.overdue",
        "due_before": "list_tasks.due_before",
        "tags": "list_tasks.tags",
        "watching": "list_tasks.watching",
        "page": "list_tasks.page",
        "page_size": "list_tasks.page_size",
    },
    ("POST", "/projects/tasks"): {
        "project_id": "create_task.project_id",
        "parent_task_id": "create_task.parent_task_id",
        "status_id": "create_task.status",
        "title": "create_task.title",
        "description": "create_task.description",
        "importance": "create_task.important",
        "leveraged": "create_task.leveraged",
        "estimate_mins": "create_task.estimate_mins",
        "due_at": "create_task.due",
        "tags": "create_task.tags",
    },
    ("PATCH", "/projects/tasks/{task_id}"): {
        "status_id": "update_task.status",
        "title": "update_task.title",
        "description": "update_task.description",
        "importance": "update_task.important",
        "leveraged": "update_task.leveraged",
        "estimate_mins": "update_task.estimate_mins",
        "start_date": "update_task.start",
        "due_at": "update_task.due",
        "tags": "update_task.tags",
    },
    ("POST", "/projects/tasks/{task_id}/move"): {
        "parent_task_id": "move_task.parent_task_id",
    },
    ("PUT", "/projects/tasks/{task_id}/assignees"): {"assignees": "assign.assignees"},
    ("POST", "/projects/tasks/{task_id}/links"): {
        "target_task_id": "link_tasks.other_task_id",
        "link_type": "link_tasks.link_type",
    },
    ("POST", "/projects/tasks/bulk"): {
        "task_ids": "bulk_update.task_ids",
        "patch": "bulk_update.status",
        "assignees_add": "bulk_update.assignees_add",
        "assignees_remove": "bulk_update.assignees_remove",
        "tags_add": "bulk_update.tags_add",
        "tags_remove": "bulk_update.tags_remove",
        "action": "bulk_update.action",
    },
    ("POST", "/projects/tasks/move/preview"): {
        "task_ids": "move_task.task_ids",
        "destination_project_id": "move_task.destination_project_id",
    },
    ("POST", "/projects/tasks/move"): {
        "task_ids": "move_task.task_ids",
        "destination_project_id": "move_task.destination_project_id",
        "accept_drops": "move_task",
        "accepted_drops": "move_task",
    },
    ("POST", "/projects/tasks/{task_id}/merge"): {"sources": "merge_tasks.source_task_ids"},
    ("GET", "/projects/search"): {"q": "find_tasks.query", "limit": "find_tasks.limit"},
    # ── activity ─────────────────────────────────────────────────────────
    ("GET", "/projects/tasks/{task_id}/timeline"): {
        "kind": "render_timeline.kind",
        "page": "task_detail",
        "page_size": "task_detail",
    },
    ("POST", "/projects/tasks/{task_id}/comments"): {
        "body": "comment.body",
        "parent_id": "comment.reply_to",
    },
    ("PATCH", "/projects/comments/{activity_id}"): {"body": "edit_comment.body"},
    # ── statuses, types, fields, tags ────────────────────────────────────
    ("POST", "/projects/nodes/{project_id}/statuses"): {
        "name": "create_status.name",
        "color": "create_status.color",
        "position": "create_status",
        "category": "create_status.category",
    },
    ("PATCH", "/projects/statuses/{status_id}"): {
        "name": "update_status.name",
        "color": "update_status.color",
        "position": "update_status.position",
        "category": "update_status.category",
    },
    ("DELETE", "/projects/statuses/{status_id}"): {"move_to": "delete_status.move_to"},
    ("POST", "/projects/nodes/{project_id}/status-set/preview"): {
        "mode": "set_status_set.mode",
        "copy_from": "set_status_set.copy_from",
    },
    ("POST", "/projects/nodes/{project_id}/status-set"): {
        "mode": "set_status_set.mode",
        "copy_from": "set_status_set.copy_from",
    },
    ("POST", "/projects/nodes/{project_id}/types"): {
        "name": "create_type.name",
        "icon": "create_type.icon",
        "color": "create_type.color",
        "is_default": "create_type.is_default",
        "scope": "create_type.org_wide",
        "is_epic": "create_type.is_epic",
    },
    ("PATCH", "/projects/types/{type_id}"): {
        "name": "update_type.name",
        "icon": "update_type.icon",
        "color": "update_type.color",
        "is_default": "update_type.make_default",
        "is_epic": "update_type.epic",
    },
    ("POST", "/projects/nodes/{project_id}/fields"): {
        "name": "create_field.name",
        "description": "create_field.description",
        "field_type": "create_field.field_type",
        "options": "create_field.options",
        "scope": "create_field.org_wide",
    },
    ("PATCH", "/projects/fields/{field_id}"): {
        "name": "update_field.name",
        "description": "update_field.description",
        "field_type": "update_field.field_type",
        "options": "update_field.options",
        "required": "update_field.required",
    },
    ("POST", "/projects/nodes/{project_id}/tags"): {
        "name": "create_tag.name",
        "color": "create_tag.color",
        "description": "create_tag.description",
        "scope": "create_tag.org_wide",
    },
    ("PATCH", "/projects/tags/{tag_id}"): {
        "name": "update_tag.name",
        "color": "update_tag.color",
        "description": "update_tag.description",
    },
    ("POST", "/projects/tags/{tag_id}/merge"): {"into_tag_id": "merge_tags.into"},
    # ── the tree and the views ───────────────────────────────────────────
    ("POST", "/projects/nodes"): {
        "name": "create_project.name",
        "description": "create_project.description",
        "parent_project_id": "create_project.parent_project_id",
        "kind": "create_project.kind",
        "lead": "create_project.lead",
    },
    ("PATCH", "/projects/nodes/{project_id}"): {
        "name": "update_project.name",
        "description": "update_project.description",
        "status": "update_project.status",
        "lead": "update_project.lead",
    },
    ("POST", "/projects/nodes/{project_id}/move"): {
        "parent_project_id": "move_project.parent_project_id",
    },
    ("POST", "/projects/nodes/{project_id}/views"): {
        "name": "save_view.name",
        "view_type": "save_view.view_type",
    },
    ("PATCH", "/projects/views/{view_id}"): {"name": "save_view.view_id"},
    # ── the member's own work ────────────────────────────────────────────
    ("GET", "/projects/assigned-to-me"): {
        "include_done": "my_work.include_done",
        "page": "my_work.page",
        "page_size": "my_work",
    },
    ("GET", "/projects/my/inbox"): {
        "include_done": "my_work.include_done",
        "page": "my_work.page",
        "page_size": "my_work",
    },
    ("POST", "/projects/my/tasks"): {
        "title": "create_personal_task.title",
        "next_action": "create_personal_task.next_action",
        "context": "create_personal_task.context",
        "due_at": "create_personal_task.due",
        "notes": "create_personal_task.notes",
    },
    ("PATCH", "/projects/tasks/{task_id}/personal"): {
        "disposition": "set_my_overlay.disposition",
        "next_action": "set_my_overlay.next_action",
        "context": "set_my_overlay.context",
        "energy": "set_my_overlay.energy",
        "is_two_minute": "set_my_overlay.two_minute",
    },
    ("POST", "/projects/tasks/{task_id}/defer"): {"until": "defer.until"},
    ("GET", "/projects/my/calendar"): {"start": "calendar.start", "end": "calendar.end"},
    ("GET", "/projects/calendar"): {
        "from": "calendar.start",
        "to": "calendar.end",
        "project_id": "calendar.project_id",
        # WS-46 P5: the app's calendar sends it, so the chat's does too.
        "include_subtree": "calendar.include_subtree",
    },
    # ── recurrence ───────────────────────────────────────────────────────
    ("PUT", "/projects/tasks/{task_id}/recurrence"): {
        "freq": "set_recurrence.freq",
        "interval": "set_recurrence.interval",
        "anchor": "set_recurrence.anchor",
        "weekdays": "set_recurrence.weekdays",
        "day_of_month": "set_recurrence.day_of_month",
        "month_of_year": "set_recurrence.month_of_year",
        "until_at": "set_recurrence.until",
        "max_occurrences": "set_recurrence.max_occurrences",
    },
    # ── intake and the bell ──────────────────────────────────────────────
    ("POST", "/projects/intake"): {
        "project_id": "capture_intake.project_id",
        "title": "capture_intake.title",
        "description": "capture_intake.description",
        "importance": "capture_intake.important",
        "leveraged": "capture_intake.leveraged",
        "due_at": "capture_intake.due",
    },
    ("GET", "/projects/intake"): {
        "project_id": "intake_queue.project_id",
        "page": "intake_queue",
        "page_size": "intake_queue",
    },
    ("POST", "/projects/intake/{task_id}/accept"): {"status_id": "triage_intake.status"},
    ("POST", "/projects/intake/{task_id}/duplicate"): {
        "duplicate_of_task_id": "triage_intake.duplicate_of",
    },
    ("POST", "/projects/intake/{task_id}/snooze"): {"until": "triage_intake.until"},
    ("GET", "/projects/notifications"): {
        "unread_only": "notifications.unread_only",
        "page": "notifications",
        "page_size": "notifications",
    },
    ("POST", "/projects/notifications/read"): {
        "ids": "mark_notifications_read.ids",
        "all": "mark_notifications_read.all_unread",
    },
    # ── people, fit and analytics ────────────────────────────────────────
    ("GET", "/projects/people/names"): {"emails": "people_for.emails"},
    ("GET", "/projects/assignees"): {"q": "people_for.query", "due": "people_for.due"},
    ("GET", "/projects/candidates"): {
        "title": "fit_for_task.title",
        "tags": "fit_for_task.tags",
        "due": "fit_for_task.due",
    },
    ("GET", "/projects/analytics/stuck"): {
        "project_id": "analytics_stuck.project_id",
        "include_subtree": "analytics_stuck",
    },
    ("GET", "/projects/analytics/load"): {
        "project_id": "analytics_load.project_id",
        "include_subtree": "analytics_load",
    },
    ("GET", "/projects/analytics/throughput"): {
        "project_id": "analytics_throughput.project_id",
        "include_subtree": "analytics_throughput",
        "weeks": "analytics_throughput.weeks",
    },
    ("GET", "/projects/analytics/finished"): {
        "project_id": "analytics_finished.project_id",
        "include_subtree": "analytics_finished",
        "weeks": "analytics_finished.weeks",
        "skip_current_week": "analytics_finished.skip_current_week",
    },
    ("GET", "/projects/analytics/outlook"): {
        "project_id": "analytics_outlook.project_id",
        "include_subtree": "analytics_outlook",
    },
    ("GET", "/projects/analytics/capacity"): {
        "project_id": "team_capacity.project_id",
        "include_subtree": "team_capacity",
        "horizon_days": "team_capacity.horizon_days",
    },
    ("GET", "/projects/analytics/conflicts"): {
        "project_id": "find_conflicts.project_id",
        "include_subtree": "find_conflicts",
        "horizon_days": "find_conflicts.horizon_days",
    },
    ("GET", "/projects/analytics/rebalance"): {
        "project_id": "rebalance.project_id",
        "include_subtree": "rebalance",
        "horizon_days": "rebalance.horizon_days",
    },
    # ── plans and reports ────────────────────────────────────────────────
    ("POST", "/projects/plan/preview"): {"rows": "propose_plan.tasks"},
    ("POST", "/projects/reports"): {"payload": "report_save.name"},
    ("PATCH", "/projects/reports/{report_id}"): {"payload": "report_save.report_id"},
    ("POST", "/projects/reports/preview"): {"payload": "render_report.template"},
}

_ORDER_REASON = "The order of rows on screen, which a person drags in the app."
_MAPPING_CARD_REASON = (
    "The answer on the app's mapping card. Without it the server's own rule maps "
    "each row, and the preview the chat card shows already counts that rule."
)
_READ_FILTER_REASON = (
    "A filter of the app's list. The chat narrows tasks through list_tasks, "
    "which prints the facts this filter reads."
)
_SCOPE_AT_CREATE_REASON = (
    "Where a NEW row lands. The PATCH route drops it, because a row keeps its scope."
)
_TRIAGE_READ_REASON = "Intake rows. The chat reads them through intake_queue."

#: A field the chat does not set, and why. A reason names the tool or the
#: rule that answers the need, so a reviewer can check the claim.
FIELD_EXEMPT: dict[tuple[str, str], dict[str, str]] = {
    ("GET", "/projects/tasks"): {
        "parent_task_id": "Subtasks are read through task_detail, which lists them.",
        "status_id": (
            "One lane by id. list_tasks filters by status_category, and render_board "
            "groups the tasks by lane."
        ),
        "assignee": "The older single form. list_tasks sends assignees.",
        "sort": "The order of the app's list. The chat orders its own answer.",
        "direction": "The order of the app's list. The chat orders its own answer.",
        "archived_only": "The archive view. include_archived reads archived rows with the rest.",
        "importance_gte": _READ_FILTER_REASON,
        "tags_all": "Every tag rather than any tag. task_dataset takes tags_all.",
        "include_triage": _TRIAGE_READ_REASON,
        "view_id": "The hand-arranged order of a saved view, which only the board reads.",
        "top_level": "The board's subtask toggle. Each row the chat reads names its parent.",
    },
    ("PATCH", "/projects/tasks/{task_id}"): {
        "If-Match": "The browser's edit guard (D-PM-20). A chat write reads the row first.",
        "project_id": "The route refuses it: a move goes through move_task.",
        "parent_task_id": "The route refuses it: a re-parent goes through move_task.",
        "source": "Where a task came from is a fact of its create. An edit keeps it.",
    },
    ("POST", "/projects/tasks/{task_id}/move"): {
        "project_id": (
            "move_task moves between projects through POST /projects/tasks/move, the "
            "route with the preview and the drop check."
        ),
        "assignees": (
            "Promote-and-assign is the Clarify card's one transaction. The chat "
            "assigns through assign."
        ),
    },
    ("POST", "/projects/tasks/move/preview"): {
        "status_map": _MAPPING_CARD_REASON,
        "field_map": _MAPPING_CARD_REASON,
        "accept_drops": "A preview writes nothing, so it has nothing to accept.",
        "accepted_drops": "A preview writes nothing, so it has nothing to accept.",
    },
    ("POST", "/projects/tasks/move"): {
        "status_map": _MAPPING_CARD_REASON,
        "field_map": _MAPPING_CARD_REASON,
    },
    ("GET", "/projects/search"): {
        "exclude_relatives_of": "The link picker's filter in the app. task_detail reads links.",
        "include_triage": _TRIAGE_READ_REASON,
    },
    ("PATCH", "/projects/comments/{activity_id}"): {
        "parent_id": "Only POST reads it. An edit cannot re-parent a comment (activities.py).",
    },
    ("POST", "/projects/nodes/{project_id}/status-set/preview"): {
        "mapping": _MAPPING_CARD_REASON,
    },
    ("POST", "/projects/nodes/{project_id}/status-set"): {"mapping": _MAPPING_CARD_REASON},
    ("PATCH", "/projects/types/{type_id}"): {"scope": _SCOPE_AT_CREATE_REASON},
    ("POST", "/projects/nodes/{project_id}/fields"): {
        "field_key": "The route makes the key from the name.",
        "position": _ORDER_REASON,
        "required": (
            "The create route stores no required flag. create_field sets it with a "
            "PATCH under the same card."
        ),
    },
    ("PATCH", "/projects/fields/{field_id}"): {
        "field_key": "The route refuses it: a key is the identity of every stored value.",
        "position": _ORDER_REASON,
        "scope": _SCOPE_AT_CREATE_REASON,
    },
    ("PATCH", "/projects/tags/{tag_id}"): {"scope": _SCOPE_AT_CREATE_REASON},
    ("POST", "/projects/nodes"): {
        "status": "A new node starts active. update_project sets another status.",
    },
    ("PATCH", "/projects/nodes/{project_id}"): {
        "parent_project_id": "The route refuses it: a re-parent goes through move_project.",
        "kind": "The route refuses it: a node's kind is set at its create.",
        "source": "Where a node came from is a fact of its create. An edit keeps it.",
    },
    ("POST", "/projects/nodes/{project_id}/views"): {"position": _ORDER_REASON},
    ("PATCH", "/projects/views/{view_id}"): {
        "view_type": "A view keeps its type. The app has no control that changes it.",
        "position": _ORDER_REASON,
    },
    ("GET", "/projects/my/inbox"): {
        "disposition": "A chip of the lens. my_work prints the disposition of each row.",
        "context": "A chip of the lens. my_work prints the context of each row.",
        "include_deferred": (
            "A deferred task leaves the lens until its date, as in the app. list_tasks reads it."
        ),
        "include_archived": "Archived work is read through list_tasks include_archived.",
    },
    ("PATCH", "/projects/tasks/{task_id}/personal"): {
        "time_estimate_mins": (
            "Retired by D77. The route refuses it by name. update_task sets the estimate."
        ),
        "defer_until": "defer writes it, through POST /projects/tasks/{task_id}/defer.",
        "important": "Retired by D78. The route refuses it by name. update_task sets it.",
        "leveraged": "Retired by D78. The route refuses it by name. update_task sets it.",
        "kept_mine": "The member's dismissal of the app's delegate hint. The chat shows no hint.",
        "sort_key": _ORDER_REASON,
        "last_nudged_at": "The nudge route writes it, and the nudge stays out of the chat (Q3).",
    },
    ("GET", "/projects/my/calendar"): {
        "include_done": "Done work is read through my_work include_done.",
    },
    ("GET", "/projects/calendar"): {
        "status_id": _READ_FILTER_REASON,
        "status_category": _READ_FILTER_REASON,
        "assignee": _READ_FILTER_REASON,
        "assignees": _READ_FILTER_REASON,
        "unassigned": _READ_FILTER_REASON,
        "overdue": _READ_FILTER_REASON,
        "importance_gte": _READ_FILTER_REASON,
        "q": _READ_FILTER_REASON,
        "tags": _READ_FILTER_REASON,
        "tags_all": _READ_FILTER_REASON,
        "include_archived": _READ_FILTER_REASON,
        "archived_only": _READ_FILTER_REASON,
        "include_links": "The dependency lines on the app's calendar. task_detail reads links.",
        "include_undated": "A task with no date is not on a calendar. list_tasks reads it.",
        "include_triage": _TRIAGE_READ_REASON,
        "watching": _READ_FILTER_REASON,
        "top_level": _READ_FILTER_REASON,
    },
    ("GET", "/projects/analytics/outlook"): {
        "weeks": "The app sends no horizon either. The tool reads the route's default.",
    },
}

#: The open gaps of ``projects_agent_parity.md`` §3.3 that a field waits on,
#: and the slice of §12 that closes each. The fence reads the spec: a gap id
#: must be a row of the gap table, and its slice must not be marked built.
FIELD_GAPS: dict[str, str] = {
    "G5": "P6",
    "G6": "P6",
    "G7": "P6",
    "G8": "P6",
    "G9": "P6",
    "G10": "P9",
    "G11": "P9",
    "G12": "P7",
    "G13": "P7",
    "G14": "P7",
    "G15": "P7",
    "G16": "P7",
    "G17": "P7",
    "G18": "P7",
}

#: A field that a later slice gives the chat: field -> its gap id. The slice
#: that closes a gap moves its rows to ``SENDS``, so this table only shrinks.
FIELD_PLANNED: dict[tuple[str, str], dict[str, str]] = {
    ("POST", "/projects/tasks"): {
        "start_date": "G5",
        "type_id": "G6",
        "custom_fields": "G7",
        "source": "G10",
    },
    ("PATCH", "/projects/tasks/{task_id}"): {
        "type_id": "G6",
        "custom_fields": "G7",
        "include_subtasks": "G9",
    },
    ("POST", "/projects/tasks/{task_id}/move"): {
        "custom_fields": "G8",
        "include_subtasks": "G9",
    },
    ("POST", "/projects/tasks/move/preview"): {"include_subtasks": "G9"},
    ("POST", "/projects/tasks/move"): {"include_subtasks": "G9"},
    ("POST", "/projects/tasks/{task_id}/archive"): {"include_subtasks": "G9"},
    ("POST", "/projects/tasks/{task_id}/complete"): {"include_subtasks": "G9"},
    ("POST", "/projects/tasks/bulk"): {"personal": "G16", "include_subtasks": "G9"},
    ("POST", "/projects/intake"): {"source": "G11", "source_ref": "G11"},
    # G12, the project settings. A create takes them as well as an edit.
    ("POST", "/projects/nodes"): {
        "icon": "G12",
        "icon_slot": "G12",
        "task_prefix": "G12",
        "archive_after_months": "G12",
        "close_after_months": "G12",
        "timezone": "G12",
        "position": "G13",
        # G10's rule, on a node: the route takes `agent` as a source.
        "source": "G10",
    },
    ("PATCH", "/projects/nodes/{project_id}"): {
        "icon": "G12",
        "icon_slot": "G12",
        "task_prefix": "G12",
        "archive_after_months": "G12",
        "close_after_months": "G12",
        "timezone": "G12",
        "position": "G13",
    },
    ("POST", "/projects/nodes/{project_id}/move"): {"position": "G13"},
    ("POST", "/projects/nodes/{project_id}/views"): {"config": "G14"},
    ("PATCH", "/projects/views/{view_id}"): {"config": "G14"},
    ("PATCH", "/projects/tasks/{task_id}/personal"): {
        "scheduled_start": "G15",
        "scheduled_end": "G15",
        "flexible": "G15",
        "is_hard_date": "G15",
        "actual_start": "G15",
        "actual_end": "G15",
        "deep_work": "G15",
        "waiting_on": "G15",
        "delegated_at": "G15",
        "expected_by": "G15",
    },
    ("GET", "/projects/my/areas"): {"include_archived": "G17"},
    ("GET", "/projects/my/inbox"): {"untriaged": "G18"},
}


# ── WS-46 P5 (D91, fence F3) — every UI client METHOD, not only every field ──
#
# Spec: ``project-docs/specs/projects_agent_parity.md`` §6.4. A UI action can
# be new while its route is old, so F1 and F2 cannot see it. The client method
# is where a UI action first exists in code. ``tests/unit/test_projects_ui_actions.py``
# reads each method of the Projects client files as TEXT, reads the verb and
# the path that the method calls, and holds the method to one of these three
# tables. A method that is in none of them fails by its name and its file.
#
# The key is ``object.method``, for example ``projectsApi.createTask``. A free
# function that builds a gateway path takes the stem of its file as the
# object, for example ``export.exportPath``.

#: A UI client method -> the tool that does the same act. The fence asserts
#: that the tool may reach the route that the method calls (``reaches``), and
#: that the route is not class X. So a false claim fails.
UI_ACTIONS: dict[str, str] = {
    # ── projectsApi: the tree and the roll-ups ───────────────────────────
    "projectsApi.tree": "projects_tree",
    "projectsApi.summary": "project_summary",
    "projectsApi.portfolio": "project_summary",
    "projectsApi.createProject": "create_project",
    "projectsApi.patchProject": "update_project",
    "projectsApi.moveNode": "move_project",
    "projectsApi.archiveProject": "archive_project",
    "projectsApi.unarchiveProject": "unarchive_project",
    "projectsApi.grants": "project_access",
    # ── projectsApi: analytics and reports ───────────────────────────────
    "projectsApi.stuck": "analytics_stuck",
    "projectsApi.load": "analytics_load",
    "projectsApi.throughput": "analytics_throughput",
    "projectsApi.finished": "analytics_finished",
    "projectsApi.outlook": "analytics_outlook",
    "projectsApi.capacity": "team_capacity",
    "projectsApi.conflicts": "find_conflicts",
    "projectsApi.reports": "report_list",
    "projectsApi.reportTemplates": "render_report",
    "projectsApi.reportSubjects": "render_report",
    "projectsApi.createReport": "report_save",
    "projectsApi.patchReport": "report_save",
    "projectsApi.renderReport": "report_render",
    "projectsApi.previewReport": "render_report",
    "projectsApi.deleteReport": "report_delete",
    # ── projectsApi: statuses and status sets ────────────────────────────
    "projectsApi.statuses": "vocabulary",
    "projectsApi.statusSet": "vocabulary",
    "projectsApi.previewStatusSet": "set_status_set",
    "projectsApi.setStatusSet": "set_status_set",
    "projectsApi.createStatus": "create_status",
    "projectsApi.patchStatus": "update_status",
    "projectsApi.deleteStatus": "delete_status",
    # ── projectsApi: tasks ───────────────────────────────────────────────
    "projectsApi.tasks": "list_tasks",
    "projectsApi.calendar": "calendar",
    "projectsApi.search": "find_tasks",
    "projectsApi.task": "task_detail",
    "projectsApi.timeline": "task_detail",
    "projectsApi.relations": "task_detail",
    "projectsApi.createTask": "create_task",
    "projectsApi.patchTask": "update_task",
    "projectsApi.archiveTask": "archive_task",
    "projectsApi.unarchiveTask": "unarchive_task",
    "projectsApi.previewMove": "move_task",
    "projectsApi.moveTasks": "move_task",
    "projectsApi.mergeTasks": "merge_tasks",
    "projectsApi.bulkEdit": "bulk_update",
    "projectsApi.setAssignees": "assign",
    "projectsApi.comment": "comment",
    "projectsApi.createLink": "link_tasks",
    "projectsApi.deleteLink": "unlink_tasks",
    "projectsApi.recurrence": "recurrence",
    "projectsApi.setRecurrence": "set_recurrence",
    "projectsApi.clearRecurrence": "set_recurrence",
    # ── projectsApi: people and fit ──────────────────────────────────────
    "projectsApi.suggestAssignees": "people_for",
    "projectsApi.personNames": "people_for",
    "projectsApi.taskCandidates": "fit_for_task",
    # ── projectsApi: tags, types and fields ──────────────────────────────
    "projectsApi.tags": "vocabulary",
    "projectsApi.createTag": "create_tag",
    "projectsApi.patchTag": "update_tag",
    "projectsApi.tagImpact": "delete_tag",
    "projectsApi.mergeTag": "merge_tags",
    "projectsApi.deleteTag": "delete_tag",
    "projectsApi.types": "vocabulary",
    "projectsApi.createType": "create_type",
    "projectsApi.patchType": "update_type",
    "projectsApi.deleteType": "delete_type",
    "projectsApi.fields": "vocabulary",
    "projectsApi.createField": "create_field",
    "projectsApi.patchField": "update_field",
    "projectsApi.deleteField": "delete_field",
    # ── projectsApi: saved views ─────────────────────────────────────────
    "projectsApi.views": "project_views",
    "projectsApi.createView": "save_view",
    "projectsApi.patchView": "save_view",
    "projectsApi.deleteView": "delete_view",
    # ── attachmentsApi ───────────────────────────────────────────────────
    "attachmentsApi.list": "task_detail",
    "attachmentsApi.detach": "delete_attachment",
    # ── notificationsApi ─────────────────────────────────────────────────
    "notificationsApi.list": "notifications",
    "notificationsApi.markRead": "mark_notifications_read",
    "notificationsApi.markAllRead": "mark_notifications_read",
    # ── watchersApi · projectWatchersApi ─────────────────────────────────
    "watchersApi.get": "watchers",
    "watchersApi.watch": "watch",
    "watchersApi.unwatch": "watch",
    "projectWatchersApi.get": "watchers",
    "projectWatchersApi.watch": "watch",
    "projectWatchersApi.unwatch": "watch",
    # ── intakeApi ────────────────────────────────────────────────────────
    "intakeApi.queue": "intake_queue",
    "intakeApi.capture": "capture_intake",
    "intakeApi.accept": "triage_intake",
    "intakeApi.decline": "triage_intake",
    "intakeApi.duplicate": "triage_intake",
    "intakeApi.snooze": "triage_intake",
}

#: A UI client method the chat does not mirror, and why. The fence allows an
#: exemption only where the route the method calls is class X. Where a tool
#: reaches the route, the method names the tool in ``UI_ACTIONS``.
UI_EXEMPT: dict[str, str] = {
    "projectsApi.deleteProject": _DELETE_REASON,
    "projectsApi.deleteTask": _DELETE_REASON,
    "projectsApi.setViewState": (
        "The member's own overlay on a saved view (WS-27ae). " + _UI_STATE_REASON
    ),
    "projectsApi.setPositions": (
        "The hand-dragged order of cards on a saved view. " + _UI_STATE_REASON
    ),
    "projectsApi.vocabulary": (
        "The shared vocabulary list of Projects settings. The chat reads a "
        "space's effective tags, fields and types through vocabulary."
    ),
    "projectsApi.vocabularyImpact": (
        "The count shown before an admin deletes or merges a shared entry. "
        "The chat neither deletes nor merges shared entries."
    ),
    "export.exportPath": (
        "The CSV download of the board's filter. A file download is the app's "
        "export button, and a chat answer carries text."
    ),
    "importApi.upload": _IMPORT_REASON,
    "importApi.get": _IMPORT_REASON,
    "importApi.saveMapping": _IMPORT_REASON,
    "importApi.list": _IMPORT_REASON,
    "importApi.discard": _IMPORT_REASON,
    "importApi.apply": _IMPORT_REASON,
}

#: A UI client method that a later slice gives the chat: method -> its gap id
#: in the gap table of ``projects_agent_parity.md`` §3.3. The fence reads the
#: spec, and fails when the slice of that gap is marked built. The slice that
#: closes the gap moves its rows to ``UI_ACTIONS``, so this table only shrinks.
UI_PLANNED: dict[str, str] = {
    # G21 (P10): the chat attaches a file the member gave it in this thread.
    "attachmentsApi.upload": "G21",
}


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
