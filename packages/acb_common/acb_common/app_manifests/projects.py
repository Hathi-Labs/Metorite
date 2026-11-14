"""The Projects door table: every ``/projects`` route, and what the MCP door does.

Spec: ``project-docs/specs/external_agent_access.md`` §19.16 (MCP-G24), §19.3
(the manifest contract), §19.11 and AD-1 to AD-19 of §19.12. Decision D208.
Metorite task 2520.

**What this table is.** One row for each route of the Projects chat
manifest (``skill_projects.manifest.MANIFEST``), with the same method, path,
class and chat tool. The chat manifest stays the allowlist of Mira's
Projects client, in ``skill_projects``. This table adds the door half only:
a door ``op``, or an ``off_door`` reason (AD-2). This module cannot import
``skill_projects``, so it repeats the 215 pairs. That is an exception to
AD-1 and D136.1 for Projects (D208.2), until MCP-G24c moves the chat
manifest into ``acb_common``. The fence holds the pairs, the class and the
tool equal, row for row.

**The typed tools stay (§9, §19.3).** A row whose route is in
``agent_oauth.MCP_ROUTES`` names no op. Its typed tool serves it, and
``MCP_ROUTES`` stays its allowlist. So no pair is a door pair and a typed
pair at once (AD-3).

**The door ops (§19.16).** 11 ops: eight for cycles and three for saved
views. Three are class A, six are class B and two are class C. Each class B
or C op asks the member for each call (§19.5). No op is a proposal, and no
op sends.

* Status editing stays off the door. The chat client holds guards that the
  routes lack: the PST-4 guard of a shared status set, the counts of the
  preview, and the visibility check of ``copy_from``. The read
  ``GET .../status-set`` names projects with no visibility check. The
  registration slice (MCP-G24b) moves each guard to the route first.
* Template apply stays off the door (EA3-53). An MCP write of a template
  needs a preview and a confirm step, and the apply adds vocabulary to the
  whole space.
* Import stays class X. Its routes need ``admin:access:manage``, the right
  of an admin (§19.11), and its upload is a file, not JSON.
* Goals and teams have no route in the Projects router. A goal object is
  later work (task 977), and a team is a filter (D87).

**What never gets an op (§19.11).** A grant or a pin that acts as one, the
answer of a person, a bulk export, and an act that reaches another person.
No op deletes a record for good: a project, a task, a cycle, a view, a
status, a tag, a report or a file. Two ops delete the link row of a task in
an open cycle: ``remove_cycle_task``, and ``close_cycle`` with the choice
``backlog``. The task stays in each case.

**Dark.** :data:`PROJECTS_DOOR` is not in ``app_manifests.APPS``. So the
edge, branch 0, ``describe_app`` and the Studio catalog serve no op of it.
MCP-G24b, the registration slice, registers it after MCP-P1b-2 builds the
spend. It also moves the guards of status editing to the routes and builds
the preview of a template apply. MCP-G24c makes one Projects manifest
(§19.16).

**The fence.** ``tests/unit/test_projects_door_coverage.py`` walks the real
Projects router. It fails on a served route with no row, on a row that
names a route the router does not serve, on a row that differs from the
chat manifest, and on an op where §19.11 or AD-3 forbids one.
"""

from __future__ import annotations

from acb_common.app_manifests.base import App, Route

# ── The off-door reasons ────────────────────────────────────────────────────

#: AD-3: the typed tools of §9 serve this route through ``MCP_ROUTES``.
TYPED = (
    "A typed tool of §9 serves this route, and MCP_ROUTES stays its "
    "allowlist. So no door op names it (AD-3, §19.3)."
)

#: MCP-G24 sets the ops of the seven acts of §19.16 first.
LATER = (
    "MCP-G24 opens the seven acts of §19.16 first. A later slice decides the "
    "door op of this row."
)

#: §19.11 and D-PM-40: grants, and the pins that act as grants.
GRANT = (
    "§19.11 and D-PM-40: an agent writes no grant. The grants of a project, "
    "and the authority pins that act as grants, stay in Projects."
)

#: §19.11: the answer of a person.
DECISION = (
    "§19.11: the person whom a decision asks answers it in Projects. An agent "
    "never answers for that person."
)

#: The door never deletes a record for good.
HARD_DELETE = (
    "This route deletes a record for good. The door never does that. A member "
    "deletes it in Projects."
)

#: The count that a delete card shows.
DELETE_COUNT = (
    "The chat reads this count for the card of a delete or a merge. The door "
    "deletes no record for good, so it needs no count."
)

#: PST-4: the chat client guards a change to a shared status set.
STATUS_GUARD = (
    "The chat client holds the PST-4 guard of a shared status set "
    "(skill_projects/writes.py, status_edit_refusal), and this route does not. "
    "The registration slice (MCP-G24b) moves the guard to the route before the "
    "door opens it."
)

#: The chat client shows the preview counts and checks the copy_from project.
STATUS_SET_GUARDS = (
    "The chat client shows the counts of the preview on its card, and it checks "
    "that the member can see the copy_from project (skill_projects/guarded.py). "
    "This route does neither. The registration slice (MCP-G24b) adds both before "
    "the door opens it."
)

#: The status-set read names projects with no visibility check.
STATUS_SET_NAMES = (
    "The answer names the owner project and the project to inherit from, and "
    "the route reads them with no visibility check (admin.py, _inherit_target). "
    "A pinned connection could learn a name outside its pin (D-EA-12). The "
    "registration slice (MCP-G24b) fixes the read before the door opens it."
)

#: EA3-53: a template write needs a preview and a confirm step.
TEMPLATE_APPLY = (
    "EA3-53: an MCP write of a template needs a preview and a confirm step, as "
    "move_tasks has. The apply also adds task types, fields and tags to the "
    "whole space. The registration slice (MCP-G24b) builds the preview first."
)

#: The typed tool ``delete_task`` deletes through the agent-delete route.
TASK_DELETE = (
    "The typed tool delete_task of §9 deletes a task through the agent-delete "
    "route, after the yes of the member (EA3-51). This route stays off the MCP."
)

#: An act that reaches another person: a mail, a nudge or a question.
REACHES_PERSON = (
    "This act reaches another person: a mail, a nudge or a question. A later "
    "slice decides it with the sends rule of AD-19."
)

#: §19.11: a bulk export.
EXPORT = (
    "§19.11: a bulk export is a file of the app. The typed tool list_tasks "
    "reads the tasks."
)

#: §19.3: a door read is a GET.
PREVIEW_POST = (
    "A door read is a GET (§19.3), and this preview is a POST that writes "
    "nothing. The card of a status-set change shows its counts, so the preview "
    "joins the door with that change."
)

# ── The class X reasons ─────────────────────────────────────────────────────

_FLAT_TREE = (
    "The flat form of GET /projects/tree, which the typed tool list_projects "
    "reads (§9)."
)
_SCREEN = (
    "The state of the screen of one member. The browser writes it, and an "
    "agent has no screen."
)
_MY_PROJECT = (
    "Projects makes the personal project of a member at the first capture. No "
    "person and no agent makes it."
)
_MY_TASKS_STEP = (
    "A step of the My Tasks screen, not an act of its own. The typed tools of "
    "§9 make and change a task."
)
_PURGE = (
    "The purge of a soft-deleted personal task is the last step of the member "
    "in My Tasks. The door never deletes a row for good."
)
_PLANNER = (
    "The day planner belongs to the Calendar app and the Tasks assistant "
    "(calendar_ai_review.md §4)."
)
_REFS = (
    "The chat reads it to link a task number in an answer. The typed tool "
    "search_tasks finds a task."
)
_DELTA = "The sync feed of a client cache. An agent holds no cache."
_IMPORT = (
    "§19.11: an import needs admin:access:manage, the right of an admin "
    "(project_import.md §7.4). The upload is a file, and the door sends JSON "
    "only."
)
_SETTINGS_LIST = (
    "A list of Projects settings for a person. An agent reads the statuses, "
    "types, fields and tags of a project through the typed tools (§9)."
)
_SETTINGS_IMPACT = (
    "The count that Projects settings shows before an admin deletes or merges "
    "a shared entry. The door never deletes a row for good."
)
_BYTES = (
    "The bytes of a file. The typed tool get_attachment reads them through "
    "MCP_ROUTES (IMG-3)."
)
_ARCHIVE_ANSWER = (
    "§19.11: only the member of the run answers the request of an agent, on "
    "the task card (EA3-40, D-OP-13)."
)
_AGENT_DELETE = (
    "The typed tool delete_task of §9 serves this route, after the yes of the "
    "member (EA3-51). MCP_ROUTES stays its allowlist."
)

#: AD-10: the keys of a Projects answer that may leave as plain strings. The
#: edge sends each one plain only in a plain shape: an id, a time or a short
#: token. Each other string leaves as untrusted text. Only the keys that the
#: server makes: ids, times, the task number and the cycle state. No key of
#: a saved view's ``config`` is here, because a member writes its values
#: (``filters.status_id`` holds any text). The fence holds that.
PLAIN: tuple[str, ...] = (
    "id", "project_id", "cycle_id", "task_id", "view_id", "short_id", "state",
    "created_at", "updated_at", "starts_on", "ends_on", "started_at", "closed_at",
)

PROJECTS_DOOR = App(
    id="projects",
    name="Projects",
    reach="The projects and tasks that the member can see.",
    pane="/projects",
    feature="projects",
    router="gateway.routes.projects",
    routes=(
        # ── tree.py ──
        Route("GET", "/projects/tree", "projects_tree", "A", off_door=TYPED),
        Route("GET", "/projects/nodes", "", "X", reason=_FLAT_TREE),
        Route("GET", "/projects/nodes/{project_id}", "project_summary", "A", off_door=TYPED),
        Route("GET", "/projects/summary", "project_summary", "A", off_door=LATER),
        Route("GET", "/projects/nodes/{project_id}/summary", "project_summary", "A",
              off_door=LATER),
        Route("POST", "/projects/nodes", "create_project", "B", off_door=TYPED),
        Route("PATCH", "/projects/nodes/{project_id}", "update_project", "B", off_door=TYPED),
        Route("POST", "/projects/nodes/{project_id}/move", "move_project", "C", off_door=LATER),
        Route("POST", "/projects/nodes/{project_id}/convert", "convert_folder_to_project", "C",
              off_door=LATER),
        Route("DELETE", "/projects/nodes/{project_id}", "delete_project", "C",
              off_door=HARD_DELETE),
        Route("POST", "/projects/nodes/{project_id}/archive", "archive_project", "C",
              off_door=LATER),
        Route("POST", "/projects/nodes/{project_id}/unarchive", "unarchive_project", "C",
              off_door=LATER),
        Route("GET", "/projects/nodes/{project_id}/grants", "project_access", "A",
              off_door=GRANT),
        Route("GET", "/projects/nodes/{project_id}/audience", "project_access", "A",
              off_door=GRANT),
        Route("POST", "/projects/nodes/{project_id}/grants", "grant_access", "C",
              off_door=GRANT),
        Route("DELETE", "/projects/nodes/{project_id}/grants/{grant_id}", "revoke_access", "C",
              off_door=GRANT),
        Route("GET", "/projects/nodes/{project_id}/authorities", "project_authorities", "A",
              off_door=GRANT),
        Route("POST", "/projects/nodes/{project_id}/authorities", "pin_authority", "C",
              off_door=GRANT),
        Route("DELETE", "/projects/nodes/{project_id}/authorities/{pin_id}", "unpin_authority",
              "C", off_door=GRANT),
        Route("GET", "/projects/nodes/{project_id}/updates", "list_project_updates", "A",
              off_door=TYPED),
        Route("POST", "/projects/nodes/{project_id}/updates", "post_project_update", "B",
              off_door=TYPED),
        Route("DELETE", "/projects/nodes/{project_id}/updates/{update_id}",
              "delete_project_update", "C", off_door=LATER),
        Route("GET", "/projects/decisions", "my_decisions", "A", off_door=LATER),
        Route("POST", "/projects/decisions/{decision_id}/answer", "answer_decision", "C",
              off_door=DECISION),
        Route("POST", "/projects/tasks/{task_id}/asks", "ask_person", "C",
              off_door=REACHES_PERSON),
        Route("POST", "/projects/asks/{ask_id}/withdraw", "withdraw_ask", "C", off_door=LATER),
        # ── cycles.py (§19.16, act 1). PROJECTS_SPRINTS_ORGS keeps each route at 404.
        Route("GET", "/projects/nodes/{project_id}/cycles", "list_cycles", "A",
              op="list_cycles", title="List the cycles of a project, a folder or a space.",
              plain=PLAIN),
        Route("POST", "/projects/nodes/{project_id}/cycles", "create_cycle", "B",
              op="create_cycle",
              title="Plan a new cycle on a project, a folder or a space.", plain=PLAIN),
        Route("GET", "/projects/cycles/{cycle_id}", "cycle_detail", "A",
              op="get_cycle",
              title="Read one cycle, with the tasks in it that the member can see.",
              plain=PLAIN),
        Route("PATCH", "/projects/cycles/{cycle_id}", "update_cycle", "B",
              op="update_cycle",
              title="Change the name, the goal, the dates or the budget of a cycle.",
              plain=PLAIN),
        Route("DELETE", "/projects/cycles/{cycle_id}", "delete_cycle", "C",
              off_door=HARD_DELETE),
        Route("POST", "/projects/cycles/{cycle_id}/tasks", "plan_cycle", "B",
              op="add_cycle_task", title="Add one task to a planned or active cycle.",
              plain=PLAIN),
        Route("DELETE", "/projects/cycles/{cycle_id}/tasks/{task_id}", "plan_cycle", "B",
              op="remove_cycle_task",
              title="Take one task out of a cycle. The task stays, in the backlog.",
              plain=PLAIN),
        Route("POST", "/projects/cycles/{cycle_id}/start", "start_cycle", "C",
              op="start_cycle",
              title="Start a planned cycle. Metorite keeps the tasks and the estimates "
                    "of the start.",
              plain=PLAIN),
        Route("POST", "/projects/cycles/{cycle_id}/close", "close_cycle", "C",
              op="close_cycle",
              title="Close an active cycle, with a choice for each task that is not done.",
              plain=PLAIN),
        # ── tasks.py ──
        Route("GET", "/projects/tasks", "list_tasks", "A", off_door=TYPED),
        Route("GET", "/projects/tasks/stats", "board_stats", "A", off_door=TYPED),
        Route("GET", "/projects/assignable-members", "assignable_members", "A", off_door=TYPED),
        Route("GET", "/projects/tasks/{task_id}", "task_detail", "A", off_door=TYPED),
        Route("GET", "/projects/tasks/{task_id}/subtree", "delete_task", "C",
              off_door=TASK_DELETE),
        Route("POST", "/projects/tasks", "create_task", "B", off_door=TYPED),
        Route("PATCH", "/projects/tasks/{task_id}", "update_task", "B", off_door=TYPED),
        Route("POST", "/projects/tasks/{task_id}/move", "move_task", "B", off_door=TYPED),
        Route("DELETE", "/projects/tasks/{task_id}", "delete_task", "C", off_door=TASK_DELETE),
        Route("POST", "/projects/tasks/{task_id}/archive", "archive_task", "C", off_door=LATER),
        Route("POST", "/projects/tasks/{task_id}/unarchive", "unarchive_task", "B",
              off_door=LATER),
        Route("PUT", "/projects/tasks/{task_id}/assignees", "assign", "B", off_door=TYPED),
        Route("POST", "/projects/tasks/{task_id}/claim", "claim_task", "B", off_door=TYPED),
        Route("POST", "/projects/tasks/{task_id}/check-result", "report_check", "B",
              off_door=TYPED),
        Route("POST", "/projects/tasks/{task_id}/links", "link_tasks", "B", off_door=TYPED),
        Route("DELETE", "/projects/tasks/{task_id}/links/{link_id}", "unlink_tasks", "B",
              off_door=TYPED),
        Route("GET", "/projects/tasks/{task_id}/epic-contract", "set_epic_contract", "B",
              off_door=LATER),
        Route("PUT", "/projects/tasks/{task_id}/epic-contract", "set_epic_contract", "B",
              off_door=TYPED),
        # ── bulk.py · move.py · merge.py ──
        Route("POST", "/projects/tasks/bulk", "bulk_update", "C", off_door=LATER),
        Route("POST", "/projects/tasks/move/preview", "move_task", "B", off_door=TYPED),
        Route("POST", "/projects/tasks/move", "move_task", "B", off_door=TYPED),
        Route("POST", "/projects/tasks/{task_id}/merge", "merge_tasks", "C", off_door=LATER),
        # ── activities.py ──
        Route("GET", "/projects/tasks/{task_id}/timeline", "task_detail", "A", off_door=TYPED),
        Route("POST", "/projects/tasks/{task_id}/comments", "comment", "B", off_door=TYPED),
        Route("PATCH", "/projects/comments/{activity_id}", "edit_comment", "B", off_door=TYPED),
        Route("DELETE", "/projects/comments/{activity_id}", "delete_comment", "C",
              off_door=TYPED),
        Route("POST", "/projects/activities/{activity_id}/revert", "revert_activity", "C",
              off_door=LATER),
        # ── admin.py: statuses and status sets (§19.16, act 4), and types. The
        # registration slice (MCP-G24b) moves the chat guards to the routes first.
        Route("GET", "/projects/nodes/{project_id}/statuses", "vocabulary", "A",
              off_door=TYPED),
        Route("POST", "/projects/nodes/{project_id}/statuses", "create_status", "B",
              off_door=STATUS_GUARD),
        Route("PATCH", "/projects/statuses/{status_id}", "update_status", "B",
              off_door=STATUS_GUARD),
        Route("DELETE", "/projects/statuses/{status_id}", "delete_status", "C",
              off_door=HARD_DELETE),
        Route("GET", "/projects/nodes/{project_id}/status-set", "vocabulary", "A",
              off_door=STATUS_SET_NAMES),
        Route("POST", "/projects/nodes/{project_id}/status-set/preview", "set_status_set", "C",
              off_door=PREVIEW_POST),
        Route("POST", "/projects/nodes/{project_id}/status-set", "set_status_set", "C",
              off_door=STATUS_SET_GUARDS),
        Route("GET", "/projects/nodes/{project_id}/types", "vocabulary", "A", off_door=TYPED),
        Route("POST", "/projects/nodes/{project_id}/types", "create_type", "B", off_door=LATER),
        Route("PATCH", "/projects/types/{type_id}", "update_type", "B", off_door=LATER),
        Route("DELETE", "/projects/types/{type_id}", "delete_type", "C", off_door=HARD_DELETE),
        # ── custom_fields.py ──
        Route("GET", "/projects/nodes/{project_id}/fields", "vocabulary", "A", off_door=TYPED),
        Route("POST", "/projects/nodes/{project_id}/fields", "create_field", "B",
              off_door=LATER),
        Route("PATCH", "/projects/fields/{field_id}", "update_field", "B", off_door=LATER),
        Route("DELETE", "/projects/fields/{field_id}", "delete_field", "C",
              off_door=HARD_DELETE),
        # ── tags.py ──
        Route("GET", "/projects/nodes/{project_id}/tags", "vocabulary", "A", off_door=TYPED),
        Route("POST", "/projects/nodes/{project_id}/tags", "create_tag", "B", off_door=LATER),
        Route("PATCH", "/projects/tags/{tag_id}", "update_tag", "B", off_door=LATER),
        Route("GET", "/projects/tags/{tag_id}/impact", "delete_tag", "C", off_door=DELETE_COUNT),
        Route("POST", "/projects/tags/{tag_id}/merge", "merge_tags", "C", off_door=LATER),
        Route("DELETE", "/projects/tags/{tag_id}", "delete_tag", "C", off_door=HARD_DELETE),
        # ── views.py: saved views (§19.16, act 2) ──
        Route("GET", "/projects/nodes/{project_id}/views", "project_views", "A",
              op="list_views", title="List the saved views of a project.", plain=PLAIN),
        Route("POST", "/projects/nodes/{project_id}/views", "save_view", "B",
              op="create_view", title="Save a new view on a project.", plain=PLAIN),
        Route("PATCH", "/projects/views/{view_id}", "save_view", "B",
              op="update_view", title="Change one saved view of a project.", plain=PLAIN),
        Route("DELETE", "/projects/views/{view_id}", "delete_view", "C", off_door=HARD_DELETE),
        Route("GET", "/projects/views/{view_id}/state", "", "X", reason=_SCREEN),
        Route("PUT", "/projects/views/{view_id}/state", "", "X", reason=_SCREEN),
        Route("GET", "/projects/views/{view_id}/positions", "", "X", reason=_SCREEN),
        Route("PUT", "/projects/views/{view_id}/positions", "", "X", reason=_SCREEN),
        # ── me.py · personal.py ──
        Route("GET", "/projects/assigned-to-me", "my_work", "A", off_door=LATER),
        Route("GET", "/projects/plan/order", "my_work", "A", off_door=TYPED),
        Route("GET", "/projects/my/project", "my_work", "A", off_door=LATER),
        Route("POST", "/projects/my/project", "", "X", reason=_MY_PROJECT),
        Route("POST", "/projects/my/tasks", "create_personal_task", "B", off_door=LATER),
        Route("POST", "/projects/my/tasks/batch", "", "X", reason=_MY_TASKS_STEP),
        Route("POST", "/projects/my/tasks/{task_id}/organize", "", "X", reason=_MY_TASKS_STEP),
        Route("POST", "/projects/my/tasks/{task_id}/subtasks", "", "X", reason=_MY_TASKS_STEP),
        Route("PATCH", "/projects/tasks/{task_id}/personal", "set_my_overlay", "B",
              off_door=LATER),
        Route("GET", "/projects/my/inbox", "my_work", "A", off_door=LATER),
        Route("GET", "/projects/my/tasks/{task_id}", "my_task", "A", off_door=LATER),
        Route("GET", "/projects/my/summary", "", "X", reason=_MY_TASKS_STEP),
        Route("DELETE", "/projects/my/tasks/{task_id}", "", "X", reason=_PURGE),
        Route("GET", "/projects/my/tasks/{task_id}/lanes", "", "X", reason=_MY_TASKS_STEP),
        Route("GET", "/projects/my/calendar", "calendar", "A", off_door=LATER),
        Route("GET", "/projects/my/contexts", "my_contexts", "A", off_door=LATER),
        Route("GET", "/projects/my/today", "my_work", "A", off_door=LATER),
        Route("GET", "/projects/my/led", "my_led_projects", "A", off_door=LATER),
        Route("GET", "/projects/my/areas", "my_areas", "A", off_door=LATER),
        Route("POST", "/projects/my/areas", "create_area", "B", off_door=LATER),
        Route("PATCH", "/projects/my/areas/{area_id}", "rename_area", "C", off_door=LATER),
        Route("DELETE", "/projects/my/areas/{area_id}", "delete_area", "C",
              off_door=HARD_DELETE),
        Route("POST", "/projects/tasks/{task_id}/complete", "complete", "B", off_door=LATER),
        Route("POST", "/projects/tasks/{task_id}/defer", "defer", "B", off_door=LATER),
        Route("POST", "/projects/tasks/{task_id}/nudge", "nudge_task", "C",
              off_door=REACHES_PERSON),
        # ── reminders.py ──
        Route("GET", "/projects/my/reminders", "list_reminders", "A", off_door=LATER),
        Route("POST", "/projects/my/reminders", "set_reminder", "B", off_door=LATER),
        Route("PATCH", "/projects/my/reminders/{reminder_id}", "update_reminder", "B",
              off_door=LATER),
        Route("POST", "/projects/my/reminders/{reminder_id}/resolve", "resolve_reminder", "B",
              off_door=LATER),
        Route("POST", "/projects/my/reminders/{reminder_id}/reopen", "reopen_reminder", "B",
              off_door=LATER),
        Route("POST", "/projects/my/reminders/{reminder_id}/promote", "promote_reminder", "C",
              off_door=LATER),
        Route("POST", "/projects/my/reminders/{reminder_id}/snooze", "reschedule_reminder", "B",
              off_door=LATER),
        Route("POST", "/projects/my/reminders/{reminder_id}/reschedule", "reschedule_reminder",
              "B", off_door=LATER),
        # ── planning.py ──
        Route("POST", "/projects/my/calendar/plan", "", "X", reason=_PLANNER),
        Route("POST", "/projects/my/calendar/replan", "", "X", reason=_PLANNER),
        Route("POST", "/projects/my/calendar/rollover", "", "X", reason=_PLANNER),
        Route("GET", "/projects/my/calendar/estimate-stats", "", "X", reason=_PLANNER),
        # ── calendar.py · search.py · export.py · delta.py ──
        Route("GET", "/projects/calendar", "calendar", "A", off_door=LATER),
        Route("GET", "/projects/search", "find_tasks", "A", off_door=TYPED),
        Route("GET", "/projects/search/refs", "", "X", reason=_REFS),
        Route("GET", "/projects/export/tasks.csv", "export_tasks", "A", off_door=EXPORT),
        Route("GET", "/projects/delta/tasks", "", "X", reason=_DELTA),
        # ── imports.py (§19.16, act 3): class X by §19.11 ──
        Route("POST", "/projects/import/runs", "", "X", reason=_IMPORT),
        Route("GET", "/projects/import/runs", "", "X", reason=_IMPORT),
        Route("GET", "/projects/import/runs/{run_id}", "", "X", reason=_IMPORT),
        Route("PUT", "/projects/import/runs/{run_id}/mapping", "", "X", reason=_IMPORT),
        Route("POST", "/projects/import/runs/{run_id}/apply", "", "X", reason=_IMPORT),
        Route("POST", "/projects/import/runs/{run_id}/discard", "", "X", reason=_IMPORT),
        # ── vocabulary.py ──
        Route("GET", "/projects/vocabulary", "", "X", reason=_SETTINGS_LIST),
        Route("GET", "/projects/vocabulary/{kind}/{row_id}/impact", "", "X",
              reason=_SETTINGS_IMPACT),
        # ── assignees.py ──
        Route("GET", "/projects/people/names", "people_for", "A", off_door=LATER),
        Route("GET", "/projects/assignees", "people_for", "A", off_door=LATER),
        # ── analytics.py · reports.py ──
        Route("GET", "/projects/analytics/stuck", "analytics_stuck", "A", off_door=LATER),
        Route("GET", "/projects/analytics/load", "analytics_load", "A", off_door=LATER),
        Route("GET", "/projects/analytics/throughput", "analytics_throughput", "A",
              off_door=LATER),
        Route("GET", "/projects/analytics/finished", "analytics_finished", "A", off_door=LATER),
        Route("GET", "/projects/analytics/outlook", "analytics_outlook", "A", off_door=LATER),
        Route("GET", "/projects/analytics/capacity", "team_capacity", "A", off_door=LATER),
        Route("GET", "/projects/tasks/{task_id}/candidates", "fit_for_task", "A",
              off_door=LATER),
        Route("GET", "/projects/candidates", "fit_for_task", "A", off_door=LATER),
        Route("GET", "/projects/analytics/rebalance", "rebalance", "A", off_door=LATER),
        Route("GET", "/projects/analytics/conflicts", "find_conflicts", "A", off_door=LATER),
        Route("GET", "/projects/analytics/dataset", "task_dataset", "A", off_door=LATER),
        Route("POST", "/projects/plan/preview", "propose_plan", "B", off_door=LATER),
        Route("GET", "/projects/plan/analysis", "get_plan", "A", off_door=TYPED),
        Route("GET", "/projects/reports", "report_list", "A", off_door=LATER),
        Route("GET", "/projects/reports/templates", "render_report", "A", off_door=LATER),
        Route("GET", "/projects/reports/subjects", "render_report", "A", off_door=LATER),
        Route("POST", "/projects/reports", "report_save", "B", off_door=LATER),
        Route("GET", "/projects/reports/{report_id}", "report_render", "A", off_door=LATER),
        Route("PATCH", "/projects/reports/{report_id}", "report_save", "B", off_door=LATER),
        Route("DELETE", "/projects/reports/{report_id}", "report_delete", "C",
              off_door=HARD_DELETE),
        Route("GET", "/projects/reports/{report_id}/render", "report_render", "A",
              off_door=LATER),
        Route("POST", "/projects/reports/preview", "render_report", "A", off_door=LATER),
        Route("GET", "/projects/reports/{report_id}/recipients", "report_recipients", "A",
              off_door=LATER),
        Route("POST", "/projects/reports/{report_id}/recipients", "add_report_recipient", "C",
              off_door=REACHES_PERSON),
        Route("DELETE", "/projects/reports/{report_id}/recipients/{email}",
              "remove_report_recipient", "C", off_door=LATER),
        Route("PATCH", "/projects/reports/{report_id}/schedule", "set_report_schedule", "C",
              off_door=REACHES_PERSON),
        # ── attachments.py ──
        Route("POST", "/projects/tasks/{task_id}/attachments", "attach_to_task", "C",
              off_door=LATER),
        Route("GET", "/projects/tasks/{task_id}/attachments", "task_detail", "A",
              off_door=TYPED),
        Route("GET", "/projects/attachments/{attachment_id}/{filename}", "", "X",
              reason=_BYTES),
        Route("DELETE", "/projects/tasks/{task_id}/attachments/{attachment_id}",
              "delete_attachment", "C", off_door=HARD_DELETE),
        # ── recurrence.py · relations.py · watchers.py ──
        Route("GET", "/projects/tasks/{task_id}/recurrence", "recurrence", "A", off_door=LATER),
        Route("PUT", "/projects/tasks/{task_id}/recurrence", "set_recurrence", "B",
              off_door=LATER),
        Route("DELETE", "/projects/tasks/{task_id}/recurrence", "set_recurrence", "B",
              off_door=LATER),
        Route("GET", "/projects/tasks/{task_id}/relations", "task_detail", "A", off_door=TYPED),
        Route("GET", "/projects/affects-notes", "my_work", "A", off_door=TYPED),
        # ── refs.py ──
        Route("GET", "/projects/tasks/{task_id}/refs", "task_detail", "A", off_door=TYPED),
        Route("POST", "/projects/tasks/{task_id}/refs", "add_task_ref", "B", off_door=TYPED),
        Route("DELETE", "/projects/tasks/{task_id}/refs/{ref_id}", "remove_task_ref", "B",
              off_door=LATER),
        # ── agent_runs.py ──
        Route("GET", "/projects/tasks/{task_id}/agent-runs", "agent_runs", "A", off_door=TYPED),
        Route("GET", "/projects/agent-runs", "waiting_agent_requests", "A", off_door=TYPED),
        Route("GET", "/projects/agent-runs/{run_id}", "agent_runs", "A", off_door=TYPED),
        Route("POST", "/projects/tasks/{task_id}/agent-runs", "start_agent_run", "B",
              off_door=TYPED),
        Route("POST", "/projects/agent-runs/{run_id}/activities", "post_agent_activity", "B",
              off_door=TYPED),
        Route("POST", "/projects/agent-runs/{run_id}/finish", "finish_agent_run", "B",
              off_door=TYPED),
        Route("POST", "/projects/agent-runs/{run_id}/release", "release_agent_run", "B",
              off_door=TYPED),
        Route("POST", "/projects/agent-runs/{run_id}/resume", "resume_agent_run", "B",
              off_door=LATER),
        Route("POST", "/projects/agent-runs/{run_id}/answer/withdraw", "change_agent_answer",
              "C", off_door=LATER),
        Route("POST", "/projects/agent-runs/{run_id}/answer/replace", "change_agent_answer",
              "C", off_door=LATER),
        Route("POST", "/projects/agent-runs/{run_id}/outside-decision",
              "record_outside_decision", "B", off_door=TYPED),
        Route("POST", "/projects/tasks/{task_id}/archive-request", "request_task_archive", "B",
              off_door=TYPED),
        Route("POST", "/projects/agent-runs/{run_id}/archive-approval", "", "X",
              reason=_ARCHIVE_ANSWER),
        Route("POST", "/projects/tasks/{task_id}/agent-delete", "", "X", reason=_AGENT_DELETE),
        # ── agent_summary.py · needs_attention.py · agent_questions.py ──
        Route("GET", "/projects/{project_id}/agent-summary", "agent_summary", "A",
              off_door=LATER),
        Route("GET", "/projects/agents/overview", "agents_list", "A", off_door=LATER),
        Route("GET", "/projects/agents/{agent_key}/runs", "agent_recent_runs", "A",
              off_door=LATER),
        Route("GET", "/projects/needs-attention", "needs_attention", "A", off_door=LATER),
        Route("GET", "/projects/agent-questions", "agent_questions", "A", off_door=LATER),
        # ── watchers.py ──
        Route("PUT", "/projects/tasks/{task_id}/watch", "watch", "B", off_door=LATER),
        Route("DELETE", "/projects/tasks/{task_id}/watch", "watch", "B", off_door=LATER),
        Route("GET", "/projects/tasks/{task_id}/watchers", "watchers", "A", off_door=LATER),
        Route("PUT", "/projects/nodes/{project_id}/watch", "watch", "B", off_door=LATER),
        Route("DELETE", "/projects/nodes/{project_id}/watch", "watch", "B", off_door=LATER),
        Route("GET", "/projects/nodes/{project_id}/watchers", "watchers", "A", off_door=LATER),
        # ── intake.py · notifications.py ──
        Route("POST", "/projects/intake", "capture_intake", "B", off_door=LATER),
        Route("GET", "/projects/intake", "intake_queue", "A", off_door=LATER),
        Route("POST", "/projects/intake/{task_id}/accept", "triage_intake", "B", off_door=LATER),
        Route("POST", "/projects/intake/{task_id}/decline", "triage_intake", "B",
              off_door=LATER),
        Route("POST", "/projects/intake/{task_id}/duplicate", "triage_intake", "B",
              off_door=LATER),
        Route("POST", "/projects/intake/{task_id}/snooze", "triage_intake", "B", off_door=LATER),
        Route("GET", "/projects/notifications", "notifications", "A", off_door=LATER),
        Route("POST", "/projects/notifications/read", "mark_notifications_read", "B",
              off_door=LATER),
        # ── templates.py (§19.16, act 5). PROJECTS_TEMPLATES_ORGS keeps each route at 404.
        Route("GET", "/projects/templates", "list_templates", "A", off_door=TYPED),
        Route("POST", "/projects/templates", "save_as_template", "C", off_door=LATER),
        Route("POST", "/projects/templates/{template_id}/apply", "create_project_from_template",
              "C", off_door=TEMPLATE_APPLY),
    ),
)

#: The seven acts of §19.16, and the door ops that MCP-G24 gives each one.
#: Status editing, template apply, import, Goals and teams have none: see the
#: docstring of this module.
FIRST_ACTS: dict[str, tuple[str, ...]] = {
    "cycles": ("list_cycles", "create_cycle", "get_cycle", "update_cycle",
               "add_cycle_task", "remove_cycle_task", "start_cycle", "close_cycle"),
    "saved views": ("list_views", "create_view", "update_view"),
    "import": (),
    "status editing": (),
    "template apply": (),
    "goals": (),
    "teams": (),
}

#: The ops that delete a link row: a task leaves an open cycle, and the task
#: stays. No other op deletes a row (the fence reads each route's source).
LINK_DELETE_OPS: frozenset[str] = frozenset({"remove_cycle_task", "close_cycle"})

__all__ = [
    "DECISION",
    "DELETE_COUNT",
    "EXPORT",
    "FIRST_ACTS",
    "GRANT",
    "HARD_DELETE",
    "LATER",
    "LINK_DELETE_OPS",
    "PLAIN",
    "PREVIEW_POST",
    "PROJECTS_DOOR",
    "REACHES_PERSON",
    "STATUS_GUARD",
    "STATUS_SET_GUARDS",
    "STATUS_SET_NAMES",
    "TASK_DELETE",
    "TEMPLATE_APPLY",
    "TYPED",
]
