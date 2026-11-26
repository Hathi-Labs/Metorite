"""The chat parity table: each UI action, and the chat tool that does it.

Spec: ``project-docs/specs/chat_parity.md`` (P-0 of the chat parity epic,
Metorite task 1238). The owner's rule, 2026-11-01: each action that a member
can do by a click in the app is also possible from the chat.

**An action** is one request that the control plane can send to its BFF: a
verb and a path under ``/api/``, with ``{}`` for each id. The fence
``tests/unit/test_chat_parity.py`` reads every request of the UI from the
TypeScript source. It holds each one to exactly one decision:

* :data:`COVERED` names the chat tool that sends the same request to the
  gateway. The fence reads the tool's own source and checks that it names
  that gateway route. :data:`ALSO` names the other tools of a covered
  request that more than one tool sends, and the fence checks each one too.
* :data:`EXEMPT` names a kind from :data:`EXEMPT_KINDS` and a reason. It is
  only for a request that is not a member's act in any sense.
* :data:`DEBT` names the wave that will give the chat a tool. The count of
  each app may only go down (:data:`DEBT_CEILING`).

Three apps keep their own tables, and this module does not copy them:

* **Projects.** Fence F3 holds each method of the Projects client
  (``skill_projects.manifest.UI_ACTIONS``, ``UI_EXEMPT``, ``UI_PLANNED``). A
  request to ``/api/projects/...`` from any other file takes its decision
  from the Projects manifest row of its route.
* **Knowledge.** A request to ``/api/knowledge/...`` takes its decision
  from the Knowledge manifest row (``acb_skills.knowledge_manifest``).
* **CRM.** A request to ``/api/crm/...`` takes its decision from the CRM
  manifest row (``acb_common.app_manifests.crm``, WS-CRM CRM-M2a). The fence
  of that table is ``tests/unit/test_crm_ui_actions.py``.

A row here for a route of those three apps is allowed only where the manifest
gives no built tool, for example a route of the day planner that a Tasks
tool reaches.

This module is data. It imports nothing, so any reader can load it.
"""

from __future__ import annotations

__all__ = [
    "ALSO",
    "APPS",
    "APP_OF",
    "APP_PAGE",
    "COVERED",
    "DEBT",
    "DEBT_CEILING",
    "EXEMPT",
    "EXEMPT_KINDS",
    "F3_FILES",
    "P0_CEILING",
    "TOOL_SOURCES",
    "UNREAD_CEILING",
    "WAVES",
    "app_of",
]

#: An app of the control plane -> its name for a person.
APPS: dict[str, str] = {
    "projects": "Projects",
    "tasks": "Tasks",
    "calendar": "Calendar",
    "email": "Email",
    "crm": "CRM",
    "notes": "Notes",
    "whatsapp": "WhatsApp",
    "people": "People",
    "knowledge": "Knowledge",
    "memory": "Memory",
    "workflows": "Workflows",
    "apps": "Custom Apps",
    "agents": "Agent Registry",
    "chat": "Chat",
    "shell": "Shell, Home and approvals",
    "settings": "Settings and the assistant profile",
    "admin": "Admin, members and billing",
    "integrations": "Integrations",
    "studio": "AI Studio",
}

#: An app -> the live page where its member does the acts that no chat tool does
#: yet (PAR-1, ``chat_parity.md`` §6). When a member asks for an act that has no
#: tool, the chat opens this page with ``show_in_app`` and says what to press.
#: ``None`` is an app with no live page (its pane is a preview pane in ``nav.ts``),
#: so the chat says that there is none. ``test_chat_parity.py`` holds each page
#: equal to ``orchestrator.in_app_path`` (an admin may open it) and holds the
#: ``None`` apps to the apps with no live pane: an app that goes live needs a page.
APP_PAGE: dict[str, str | None] = {
    "projects": "/projects",
    "tasks": "/projects",  # My work is a section of Projects; /tasks redirects there
    "calendar": "/calendar",
    "email": "/email",
    "crm": "/crm",
    "notes": None,  # preview in nav.ts
    "whatsapp": "/whatsapp",
    "people": "/people",
    "knowledge": "/knowledge",
    "memory": None,  # preview in nav.ts
    "workflows": "/studio",  # apps, workflows and skills are one library (D179)
    "apps": "/studio",
    "agents": None,  # the Agent Registry is an operator concern, preview in nav.ts
    "chat": "/chat",
    "shell": "/",
    "settings": "/settings/appearance",
    "admin": "/settings/organization",
    "integrations": "/integrations",
    "studio": "/studio",
}

#: A BFF path prefix -> its app. The longest prefix that matches wins.
APP_OF: tuple[tuple[str, str], ...] = (
    ("/api/projects/my/calendar", "calendar"),
    ("/api/projects/my/reminders", "calendar"),
    ("/api/tasks/calendar", "calendar"),
    ("/api/calendar", "calendar"),
    ("/api/projects/my", "tasks"),
    ("/api/tasks/people", "people"),
    ("/api/tasks", "tasks"),
    ("/api/projects", "projects"),
    ("/api/email", "email"),
    ("/api/crm", "crm"),
    ("/api/notes", "notes"),
    ("/api/whatsapp", "whatsapp"),
    ("/api/me/whatsapp-link", "whatsapp"),
    ("/api/people", "people"),
    ("/api/me/reach", "people"),
    ("/api/knowledge", "knowledge"),
    ("/api/memory", "memory"),
    ("/api/chat/memories", "memory"),
    ("/api/workflows", "workflows"),
    ("/api/apps", "apps"),
    ("/api/agent/chat", "chat"),
    ("/api/agent/cancel", "chat"),
    ("/api/agent/respond-input", "chat"),
    # projects_ai_chat.md §31 VOX-1: the mic of the composer.
    ("/api/agent/transcribe", "chat"),
    ("/api/agent/workspace", "chat"),
    ("/api/agent/artifacts", "chat"),
    ("/api/agent", "agents"),
    ("/api/observability", "agents"),
    ("/api/chat", "chat"),
    ("/api/models", "chat"),
    ("/api/feedback", "chat"),
    ("/api/artifacts", "chat"),
    ("/api/documents", "chat"),
    ("/api/shell", "shell"),
    ("/api/brief", "shell"),
    ("/api/push", "shell"),
    ("/api/health", "shell"),
    ("/api/actions", "shell"),
    ("/api/settings", "settings"),
    # SW-31b: Settings, Agents, Guidance. The gateway serves `/agent-guidance`.
    ("/api/agent-guidance", "settings"),
    ("/api/auth", "settings"),
    ("/api/accounts", "settings"),
    ("/api/admin", "admin"),
    ("/api/billing", "admin"),
    ("/api/org", "admin"),
    ("/api/signup", "admin"),
    ("/api/integrations", "integrations"),
    ("/api/agent-access", "integrations"),
    ("/api/agent-kit", "integrations"),
    # WS-STU. The gateway serves `/studio` (STU-B1). The skill creator sends to it (STU-F2b).
    ("/api/studio", "studio"),
)


def app_of(path: str) -> str | None:
    """The app of a BFF path, or ``None`` when no prefix holds it."""
    best = ""
    found: str | None = None
    for prefix, app in APP_OF:
        if (path == prefix or path.startswith(prefix + "/")) and len(prefix) > len(best):
            best, found = prefix, app
    return found


#: A wave of the epic -> what it gives the chat, the apps it covers, and the
#: Metorite tasks of the board that build it (PAR-1b, task 3100). A wave with no
#: task is a wave that nobody builds, so ``test_chat_parity.py`` refuses it. A
#: wave that splits into slices lists the task of each slice.
#: P-1 is the next slice: P-1a (task 3112), P-1b (task 3922) and P-1c (task 3923).
#: The epic sets the order of the waves after it.
WAVES: dict[str, tuple[str, tuple[str, ...], tuple[int, ...]]] = {
    "P-1": ("Calendar and Tasks", ("calendar", "tasks"), (3112, 3922, 3923)),
    "P-2": ("Email", ("email",), (3111, 3919)),
    # P-3 (task 3678): task 3951 built the add and change of the CRM set-up (CRM-M2e).
    "P-3": ("CRM", ("crm",), (3678, 3951)),
    "P-4": ("Notes and WhatsApp", ("notes", "whatsapp"), (3113,)),
    # P-5b (task 3114) splits into P-5b-1 (3114) and P-5b-2 to P-5b-6 (3943 to 3947).
    "P-5": ("People, Knowledge and Memory", ("people", "knowledge", "memory"),
            (3105, 3114, 3943, 3944, 3945, 3946, 3947)),
    "P-6": ("Workflows, Custom Apps and the Agent Registry", ("workflows", "apps", "agents"),
            (3115, 3903, 3950)),
    "P-7": ("Chat threads and the shell", ("chat", "shell"), (3115, 3903)),
    "P-8": ("Settings, Admin and Integrations", ("settings", "admin", "integrations"),
            (3102, 3925, 3107, 3935, 3936, 3108, 3109, 3932)),
    "P-9": ("AI Studio", ("studio",), (3115,)),
}

#: The kinds of a request that is not a member's act. An exemption names one.
EXEMPT_KINDS: dict[str, str] = {
    "oauth": "An OAuth redirect, consent or callback. The provider needs the browser.",
    "download": "A file stream for a download, a preview or a picture. The chat reads the record.",
    "stream": "A live stream or a poll that refreshes a view. The chat reads the same data.",
    "webhook": "A call that a provider or a machine makes, not a member.",
    "health": "A health ping or a probe of the session.",
    "session": "Sign-in, sign-up, sign-out or a switch of account. The browser owns its session.",
    "chat": "The chat's own transport: a turn, a stop, an answer to a card, the saved thread.",
    "secret": "The member types a password, a key or a token. A secret never goes through the chat.",
}

#: The Projects client files. Fence F3 holds their methods, so this fence
#: does not hold their requests a second time.
F3_FILES: tuple[str, ...] = (
    "app/projects/lib/api.ts",
    "app/projects/lib/importApi.ts",
    "app/projects/lib/export.ts",
)

#: The Python files that hold chat tools that call the gateway over HTTP.
#: The fence reads the paths that each tool function names, and the paths of
#: the helpers of its module that it calls.
TOOL_SOURCES: tuple[str, ...] = (
    "apps/agents/agent-email-assistant/agents.py",
    "apps/agents/agent-crm/agents.py",
    "apps/agents/agent-whatsapp-assistant/agents.py",
    "apps/skills/skill-my-tasks/skill_my_tasks/core.py",
    "packages/acb_skills/acb_skills/chat_pin_tools.py",
    "packages/acb_skills/acb_skills/crm_tools.py",
    # PAR-9 (task 3950): the studio-apps pack sends the routes of the Custom Apps.
    "packages/acb_skills/acb_skills/custom_app_tools.py",
    # PAR-8b: the Home tools send the shell routes, the brief and the test push.
    "packages/acb_skills/acb_skills/home_tools.py",
    # SEM-8: ``org_facts`` sends the verified-question routes of the CRM.
    "packages/acb_skills/acb_skills/org_facts_tools.py",
    # PAR-2a: the org-admin pack sends the admin routes of the Members tab.
    "packages/acb_skills/acb_skills/org_admin_tools.py",
    # PAR-4a: the groups, roles, overrides and name of the org, in the same pack.
    "packages/acb_skills/acb_skills/org_admin_more_tools.py",
    # PAR-6a: the org-admin pack sends the routes of the Integrations app.
    "packages/acb_skills/acb_skills/integrations_admin_tools.py",
    # P-5b-1: the people pack sends the People routes and the two Reach reads.
    "packages/acb_skills/acb_skills/people_tools.py",
    "packages/acb_skills/acb_skills/profile_tools.py",
    "packages/acb_skills/acb_skills/studio_tools.py",
)

#: An action -> the tool that sends its request to the gateway. A tuple
#: gives the gateway route too, as ``"VERB /path"``, where the BFF route does
#: not name it in a form the fence can read.
COVERED: dict[str, str | tuple[str, str]] = {
    # ── Projects ──
    "GET /api/projects/nodes": "my_tasks_list_projects",
    # ── Tasks ──
    "POST /api/projects/my/tasks/batch": "my_tasks_capture_many",
    "POST /api/tasks/ai/atomize": "my_tasks_capture_many",
    "POST /api/tasks/items/{}/clarify": "my_tasks_clarify",
    "POST /api/tasks/plan": "my_tasks_plan_project",
    "POST /api/tasks/plan/apply": "my_tasks_plan_project",
    # P-1a (task 3112): the two proposals of the task page. Each tool writes nothing.
    "POST /api/tasks/items/{}/enrich": "my_tasks_enrich",
    "POST /api/tasks/items/{}/suggest-title": "my_tasks_suggest_title",
    # ── Calendar ──
    "GET /api/projects/my/calendar/estimate-stats": "my_tasks_estimate_stats",
    # CAL-1a (task 1675): the `calendar-day` pack puts these planner tools in the main
    # chat. The `{}` key covers plan-today, replan-today and rollover-today
    # (my_tasks_plan_day, my_tasks_replan_day, my_tasks_rollover). Fence:
    # tests/unit/test_calendar_day_pack.py.
    "POST /api/tasks/calendar/{}": "my_tasks_plan_day",
    "PUT /api/tasks/calendar/day-state": "my_tasks_set_one_thing",
    # P-1a (task 3112): the read of the Connected calendars section (CE-10c). No token shows.
    "GET /api/calendar/connections": "my_tasks_list_calendar_connections",
    # CAL-2 (task 1676): the CALENDAR fields of the settings (the standing instruction,
    # the working hours, capacity, buffer, focus run, break, lunch, energy windows,
    # auto rollover, time zone). The model choices, the sync flags and capture_dedup
    # are not here: the tool has no parameter for them. The day rules
    # (day_templates) are written by the four day rule tools of CAL-3 (task 1677),
    # through the same route with an If-Match token. Fence:
    # tests/unit/test_calendar_settings_tools.py and tests/unit/test_day_rules_tools.py.
    "GET /api/tasks/settings": "my_tasks_get_calendar_settings",
    "PUT /api/tasks/settings": "my_tasks_update_calendar_settings",
    # ── Email ──
    "DELETE /api/email/accounts/{}": "update_account",
    "DELETE /api/email/knowledge/{}": "delete_knowledge",
    "DELETE /api/email/learned-patterns/{}": "forget_pattern",
    "DELETE /api/email/rules/guidance/{}": "rule_guidance",
    "DELETE /api/email/rules/patterns/{}": "forget_pattern",
    "DELETE /api/email/rules/{}": "delete_rule",
    "DELETE /api/email/voice-profile": "voice_profile",
    "GET /api/email/accounts": "list_accounts",
    "GET /api/email/accounts/{}/labels": "list_labels",
    "GET /api/email/analytics/overview": "get_account_overview",
    "GET /api/email/assistant/always-copy": "get_rules_and_settings",
    "GET /api/email/assistant/settings": "get_rules_and_settings",
    "GET /api/email/cleanup/status": "auto_categorize_inbox",
    "GET /api/email/cleanup/uncategorized": "auto_categorize_inbox",
    "GET /api/email/cold-senders": "list_senders",
    "GET /api/email/contacts/card": "find_contact",
    "GET /api/email/contacts/sent-from": "send_email",
    "GET /api/email/contacts/suggest": "find_contact",
    "GET /api/email/digest": "digest",
    "GET /api/email/knowledge": "list_knowledge",
    "GET /api/email/learned-patterns": "list_patterns",
    "GET /api/email/messages": "query_inbox",
    "GET /api/email/messages/{}": "read_email",
    "GET /api/email/messages/{}/full-body": "read_email",
    "GET /api/email/messages/{}/timeline": "read_email",
    "GET /api/email/rules": "get_rules_and_settings",
    "GET /api/email/rules/guidance": "rule_guidance",
    "GET /api/email/rules/history": "list_rule_history",
    "GET /api/email/rules/patterns": "list_patterns",
    "GET /api/email/rules/policies": "rule_guidance",
    "GET /api/email/rules/process-past/estimate": "run_rules",
    "GET /api/email/rules/process-past/status": "run_rules",
    "GET /api/email/senders": "list_senders",
    "GET /api/email/senders/categories": "list_senders",
    "GET /api/email/voice-profile": "voice_profile",
    "GET /api/email/voice-profile/preview": "voice_profile",
    "GET /api/email/voice-profile/status": "voice_profile",
    # P-2a (task 3111): five reads of the Email app. Each is a mode of an email tool
    # that the email packs already hold, so no pack line changes.
    "GET /api/email/accounts/{}/folders": "list_labels",
    "GET /api/email/accounts/{}/storage/older": "get_account_overview",
    "GET /api/email/admin/connections": "list_accounts",
    "GET /api/email/messages/facets": "query_inbox",
    "GET /api/email/search": "query_inbox",
    "PATCH /api/email/accounts/{}": "update_account",
    "PATCH /api/email/accounts/{}/labels": "create_label",
    "PATCH /api/email/knowledge/{}": "save_knowledge",
    "PATCH /api/email/messages/{}": "manage_inbox",
    "PATCH /api/email/rules/{}": "update_rule",
    "POST /api/email/accounts/{}/default": "update_account",
    "POST /api/email/accounts/{}/folders": "create_label",
    "POST /api/email/accounts/{}/resync": "sync_account",
    "POST /api/email/assistant/writing-style/generate": "generate_writing_style",
    "POST /api/email/cleanup/auto-categorize": "auto_categorize_inbox",
    "POST /api/email/cleanup/backfill": "auto_categorize_inbox",
    "POST /api/email/cleanup/restore-labels": "auto_categorize_inbox",
    "POST /api/email/cold-senders": "set_sender_status",
    "POST /api/email/compose-assist": "edit_draft",
    "POST /api/email/digest/send": "digest",
    "POST /api/email/draft-reply": "draft_reply",
    "POST /api/email/drafts/send": "send_draft",
    "POST /api/email/follow-ups/scan": "find_follow_ups",
    "POST /api/email/forward": "forward_email",
    "POST /api/email/knowledge": "save_knowledge",
    "POST /api/email/knowledge/{}/approve": "voice_profile",
    "POST /api/email/messages/bulk": "bulk_manage_inbox",
    "POST /api/email/messages/{}/snooze": "snooze_email",
    "POST /api/email/newsletters": "unsubscribe_sender",
    "POST /api/email/reply-zero/resolve": "mark_thread_done",
    "POST /api/email/rules": "create_rule",
    "POST /api/email/rules/copy": "copy_rules",
    "POST /api/email/rules/feedback": "learn_rule_pattern",
    "POST /api/email/rules/guidance": "rule_guidance",
    "POST /api/email/rules/history/retry-failed": "run_rules",
    "POST /api/email/rules/history/{}/approve": "resolve_execution",
    "POST /api/email/rules/history/{}/reject": "resolve_execution",
    "POST /api/email/rules/history/{}/undo": "resolve_execution",
    # P-2c (task 3919): forget_pattern(action="review") approves or rejects one pattern.
    "POST /api/email/rules/patterns/review": "forget_pattern",
    "POST /api/email/rules/install-presets": "install_default_rules",
    "POST /api/email/rules/process-past": "run_rules",
    "POST /api/email/rules/reset": "install_default_rules",
    "POST /api/email/rules/run": "run_rules",
    "POST /api/email/rules/run-message": "run_rules",
    "POST /api/email/rules/test": "test_rule_match",
    "POST /api/email/rules/test/recent": "test_rule_match",
    "POST /api/email/send": "send_email",
    "POST /api/email/senders/categorize": "categorize_senders",
    "POST /api/email/sync": "sync_account",
    "POST /api/email/unsubscribe": "unsubscribe_sender",
    "POST /api/email/voice-profile/build": "voice_profile",
    "POST /api/email/voice-profile/sample": "voice_profile",
    "PUT /api/email/assistant/always-copy": "update_assistant_settings",
    "PUT /api/email/assistant/settings": "update_assistant_settings",
    "PUT /api/email/drafts": "edit_draft",
    "PUT /api/email/voice-profile": "voice_profile",
    # ── WhatsApp ──
    "GET /api/whatsapp/accounts": "list_whatsapp_accounts",
    "GET /api/whatsapp/chats": "list_whatsapp_chats",
    "GET /api/whatsapp/chats/{}/context": "whatsapp_chat_context",
    "GET /api/whatsapp/chats/{}/messages": "read_whatsapp_chat",
    # P-4a (task 3960): the read tools of the WhatsApp settings, rules, calls and link.
    "GET /api/me/whatsapp-link": "whatsapp_link_status",
    "GET /api/whatsapp/calls": "list_whatsapp_calls",
    "GET /api/whatsapp/calls/diagnostics": "whatsapp_call_diagnostics",
    "GET /api/whatsapp/calls/{}/events": "whatsapp_call_events",
    "GET /api/whatsapp/categories": "list_whatsapp_categories",
    "GET /api/whatsapp/connection/info": "whatsapp_connection_info",
    "GET /api/whatsapp/labels": "list_whatsapp_labels",
    "GET /api/whatsapp/pulse": "whatsapp_pulse",
    "GET /api/whatsapp/rules/preview": "whatsapp_rules_preview",
    "GET /api/whatsapp/saved-replies": "list_whatsapp_saved_replies",
    "GET /api/whatsapp/streams": "whatsapp_streams",
    "GET /api/whatsapp/templates": "list_whatsapp_templates",
    "POST /api/whatsapp/chats/{}/draft": "draft_whatsapp_reply",
    "POST /api/whatsapp/commitments/{}/nudge": "draft_waiting_on_nudge",
    "POST /api/whatsapp/messages/{}/transcribe": "transcribe_whatsapp_voice_note",
    # ── People ──
    "GET /api/tasks/people": "my_tasks_people",
    # P-5b-1 (task 3114): four reads of the people pack. Each tool sends the route as
    # the member, and the route projects each field by the right of the member.
    "GET /api/people": "people_directory",
    "GET /api/people/me": "people_me",
    "GET /api/people/me/work": "people_work",
    "GET /api/people/{}/work": "people_work",
    "GET /api/me/reach/status": "reach_preview",
    "GET /api/me/reach/preview": "reach_preview",
    # ── Chat ──
    # CO-11b (task 3563): Pin and Unpin of THIS chat. The tool takes no chat
    # id: it acts on the chat of the run, as the member of the run.
    "DELETE /api/chat/sessions/{}/pin": "pin_chat",
    "GET /api/agent/artifacts": "list_artifacts",
    "PUT /api/chat/sessions/{}/pin": "pin_chat",
    # ── Shell, Home and approvals ──
    # PAR-8b (task 3903, chat_parity.md section 6.4 rung 5): the Home tools of the
    # orchestrator, dark behind HOME_TOOLS_ORGS. Each sends the route as the member of the run.
    "GET /api/shell/feed": "home_read",
    "GET /api/shell/needs": "home_read",
    "GET /api/shell/search": "home_read",
    "GET /api/shell/feed/prefs": "home_read",
    "GET /api/brief/today": "home_read",
    "POST /api/shell/feed/state": "home_card",
    "PUT /api/shell/feed/prefs": "home_prefs",
    "PUT /api/shell/layout": "home_layout",
    "POST /api/push/test": "home_test_push",
    # ── Settings and the assistant profile ──
    "DELETE /api/auth/me/assistant-profile/aliases/{}": (
        "profile_alias",
        "DELETE /auth/me/assistant-profile/aliases/{}",
    ),
    "DELETE /api/auth/me/assistant-profile/facts/{}": (
        "profile_forget",
        "DELETE /auth/me/assistant-profile/facts/{}",
    ),
    "PATCH /api/auth/me/assistant-profile/aliases/{}": (
        "profile_alias",
        "PATCH /auth/me/assistant-profile/aliases/{}",
    ),
    "PATCH /api/auth/me/assistant-profile/facts/{}": (
        "profile_remember",
        "PATCH /auth/me/assistant-profile/facts/{}",
    ),
    "POST /api/auth/me/assistant-profile/aliases": (
        "profile_alias",
        "POST /auth/me/assistant-profile/aliases",
    ),
    "POST /api/auth/me/assistant-profile/facts": (
        "profile_remember",
        "POST /auth/me/assistant-profile/facts",
    ),
    "POST /api/auth/me/assistant-profile/interview": (
        "profile_interview",
        "POST /auth/me/assistant-profile/interview",
    ),
    "PUT /api/auth/me/assistant-profile": ("profile_set", "PUT /auth/me/assistant-profile"),
    # ── AI Studio (STU-F2d) ──
    "GET /api/studio/actions": "studio_list_actions",
    # STU-D2b: the READ side of a package page. The door POST of a package page is a list,
    # a create, an update or a delete, by its action id. The list is the row here, and
    # ALSO names the three write tools of B2c-2b, which send the same route.
    "GET /api/studio/packages": "studio_package_get",
    "GET /api/studio/packages/{}": "studio_package_get",
    # STU-G3a: "Build it" in the Studio IDE makes a draft package first
    # (lib/studio/packageDraft.ts createDraftPackage). studio_save makes one with
    # the same POST when it gets no package_id.
    "POST /api/studio/packages": "studio_save",
    # STU-G3c: the IDE saves a draft with the PUT that studio_save sends (based_on in the query).
    "PUT /api/studio/packages/{}/draft": "studio_save",
    # STU-G5: the IDE publishes with the POST that studio_publish sends (notes, based_on and origin in the body).
    "POST /api/studio/packages/{}/versions": "studio_publish",
    # STU-A5c-2: the app page deletes a package (BETA-1) with the preview and the DELETE
    # that studio_delete_package sends, after its one card.
    "GET /api/studio/packages/{}/delete-preview": "studio_delete_package",
    "DELETE /api/studio/packages/{}": "studio_delete_package",
    # STU-K1d (task 2967): the "Start from a ready app" section lists the templates and makes one.
    # studio_template_list sends the GET. studio_create_from_template sends the POST after its one card.
    "GET /api/studio/templates": "studio_template_list",
    "POST /api/studio/packages/from-template": "studio_create_from_template",
    "POST /api/studio/apps/{}/actions/{}": "studio_record_list",
    "GET /api/studio/skills": "studio_skill_list",
    "GET /api/studio/skills/{}": "studio_skill_list",
    "PATCH /api/studio/skills/{}/state": "studio_skill_state",
    "POST /api/studio/skills": "studio_skill_save",
    "POST /api/studio/skills/{}/versions": "studio_skill_save",
    # ── Workflows (STB-6a, ai_studio.md E13a) ──
    # The two reads of the Workflows app, as the member, in ONE tool (no id: the list).
    # The read of one workflow drops the webhook token, and shows the steps as a count by type.
    "GET /api/workflows": "studio_workflow_read",
    "GET /api/workflows/{}": "studio_workflow_read",
    # STB-6b (ai_studio.md E13b): the four live routes of publish.py, as the member,
    # after the right check and ONE card. Pause and resume are disable and enable.
    "POST /api/workflows/{}/publish": "studio_workflow_publish",
    "POST /api/workflows/{}/versions/{}/rollback": "studio_workflow_publish",
    "POST /api/workflows/{}/disable": "studio_workflow_live",
    "POST /api/workflows/{}/enable": "studio_workflow_live",
    # ── Admin, members (PAR-2a, task 3102, chat_parity.md section 6.3) ──
    # The org-admin pack calls each admin route as the member, and the guard of
    # the route is the only check. A write shows ONE card first.
    "GET /api/admin/members": "list_members",
    "GET /api/admin/members/requests": "list_members",
    # set_member_roles reads the roles of the org before its card.
    "GET /api/admin/roles": "set_member_roles",
    "PATCH /api/admin/members/{}": "set_member_status",
    "PUT /api/admin/members/{}/roles": "set_member_roles",
    # The approve and the deny of a sign-in request: the last part is the verb.
    "POST /api/admin/members/requests/{}/{}": "answer_access_request",
    # ── Admin, groups, roles, access and the org name (PAR-4a, task 3107) ──
    # The same pack and the same rule: each write reads GET /auth/me first and
    # shows ONE card. A role create and an overrides write give only rights
    # that the caller holds (stricter than the page).
    "GET /api/admin/groups": "list_org_groups",
    "POST /api/admin/groups": "create_org_group",
    "PATCH /api/admin/groups/{}": "edit_org_group",
    "DELETE /api/admin/groups/{}": "delete_org_group",
    "POST /api/admin/groups/{}/members": "add_org_group_member",
    "DELETE /api/admin/groups/{}/members/{}": "remove_org_group_member",
    "GET /api/admin/features": "list_roles_and_rights",
    "POST /api/admin/roles": "create_org_role",
    "DELETE /api/admin/roles/{}": "delete_org_role",
    "GET /api/admin/members/{}/access": "read_member_access",
    "PUT /api/admin/members/{}/overrides": "set_member_overrides",
    # A read only. remove_member shows the same numbers on its card.
    "GET /api/admin/members/{}/seal-preview": "preview_member_removal",
    # PAR-4c (task 3935): the removal, and the purge for good. The purge acts
    # only on the member's own typed address of this turn.
    "DELETE /api/admin/members/{}": "remove_member",
    "DELETE /api/admin/members/{}/purge": "purge_member",
    "PATCH /api/admin/organization": "rename_org",
    # ── Admin, the seats (PAR-4d, task 3936) ──
    # The seat routes relay the Customer Console. The tools send no org and no
    # actor (R11). The Billing page reads the counts through a helper that the
    # fence cannot follow, so that row names its gateway route.
    "GET /api/billing/seats": ("read_seats", "GET /billing/seats"),
    "GET /api/org/seats": "read_seats",
    "POST /api/billing/seats/assign": "set_seat",
    "POST /api/billing/seats/release": "set_seat",
    # SeatsTab sends `/api/billing/seats/${kind}`, assign or release. One tool sends both.
    "POST /api/billing/seats/{}": "set_seat",
    # ── Integrations (PAR-6a, task 3109, chat_parity.md section 6.3 rung 3) ──
    # The org-admin pack calls each Integrations route as the member, and the
    # guard of the route is the only check. A write shows ONE card first. The
    # BFF of the agent-access rows names its gateway path in a relay, so those
    # rows take the tuple form.
    "GET /api/agent-access/connections": (
        "read_agent_access", "GET /agent-access/connections"),
    "GET /api/agent-access/requests": ("read_agent_access", "GET /agent-access/requests"),
    "GET /api/integrations/agent-connections": (
        "read_agent_access", "GET /integrations/agent-connections"),
    "GET /api/integrations/connections": "read_integrations",
    "GET /api/integrations/github/account": "read_integrations",
    "GET /api/integrations/plugins": "read_integrations",
    "GET /api/integrations/skills": "read_integrations",
    "GET /api/integrations/status": "read_integrations",
    # The two probes and the proposal leave the org (open_world=True).
    "GET /api/integrations/test": "probe_integration",
    "POST /api/integrations/mcp/{}/test": "probe_integration",
    "POST /api/integrations/discover": "discover_api_setup",
    "DELETE /api/agent-access/connections/{}": (
        "revoke_agent_connection", "DELETE /agent-access/connections/{}"),
    "DELETE /api/integrations/agent-connections/{}": (
        "revoke_agent_connection", "DELETE /integrations/agent-connections/{}"),
    "POST /api/integrations/connections/{}/disconnect": "disconnect_integration",
    "DELETE /api/integrations/github": "disconnect_integration",
    "DELETE /api/integrations/plugins/{}": "disconnect_integration",
    "PATCH /api/integrations/mcp/{}": "set_mcp_server",
    # PAR-8a (task 3903, chat_parity.md section 6.4 rung 5): the other 18 acts of the
    # Workflows app, as the member, in the dark pack studio-workflows. Each write that a
    # card guards sends after approved only.
    "GET /api/workflows/{}/runs": "studio_workflow_runs",
    "GET /api/workflows/runs/{}": "studio_workflow_runs",
    "POST /api/workflows/{}/validate": "studio_workflow_check",
    "GET /api/workflows/catalog": "studio_workflow_catalog",
    "GET /api/workflows/catalog/search": "studio_workflow_catalog",
    "GET /api/workflows/modules": "studio_workflow_catalog",
    "GET /api/workflows/modules/{}": "studio_workflow_catalog",
    "POST /api/workflows/{}/run": "studio_workflow_run",
    "POST /api/workflows": "studio_workflow_save",
    "PUT /api/workflows/{}": "studio_workflow_save",
    "POST /api/workflows/{}/duplicate": "studio_workflow_save",
    "DELETE /api/workflows/{}": "studio_workflow_save",
    # The Copilot of the editor: the helper gives new steps, and the tool saves them
    # to the draft with the PUT of the editor after one card.
    "POST /api/workflows/{}/copilot": "studio_workflow_build",
    "POST /api/workflows/modules/generate": "studio_workflow_module",
    "POST /api/workflows/modules/test": "studio_workflow_module",
    "POST /api/workflows/modules": "studio_workflow_module",
    "PUT /api/workflows/modules/{}": "studio_workflow_module",
    "DELETE /api/workflows/modules/{}": "studio_workflow_module",
    # PAR-9 (task 3950, chat_parity.md section 6.4 rung 8): 26 acts of the Custom Apps,
    # as the member, in the dark pack studio-apps. A publish, a roll back, a share act,
    # a file delete, a restore and a data delete each show ONE card first.
    "GET /api/apps": "custom_app_read",
    "GET /api/apps/pins": "custom_app_read",
    "GET /api/apps/{}": "custom_app_read",
    "GET /api/apps/{}/versions": "custom_app_read",
    "GET /api/apps/{}/usage": "custom_app_read",
    "GET /api/apps/{}/grants": "custom_app_read",
    "GET /api/apps/{}/checkpoints": "custom_app_read",
    "GET /api/apps/{}/me": "custom_app_read",
    "GET /api/apps/{}/files": "custom_app_read",
    "GET /api/apps/{}/files/content": "custom_app_read",
    "GET /api/apps/{}/data/{}": "custom_app_read",
    "GET /api/apps/{}/data/{}/{}": "custom_app_read",
    "PATCH /api/apps/{}": "custom_app_save",
    "POST /api/apps/{}/fork": "custom_app_save",
    "DELETE /api/apps/{}/pin": "custom_app_save",
    "PUT /api/apps/{}/files/content": "custom_app_files",
    "DELETE /api/apps/{}/files/content": "custom_app_files",
    "POST /api/apps/{}/build": "custom_app_files",
    "POST /api/apps/{}/sync": "custom_app_files",
    "POST /api/apps/{}/restore": "custom_app_files",
    "POST /api/apps/{}/publish": "custom_app_publish",
    "POST /api/apps/{}/rollback": "custom_app_publish",
    "POST /api/apps/{}/grants": "custom_app_share",
    "DELETE /api/apps/{}/grants/{}": "custom_app_share",
    "PUT /api/apps/{}/data/{}/{}": "custom_app_data",
    "DELETE /api/apps/{}/data/{}/{}": "custom_app_data",
}

#: A covered action that more than one chat tool sends -> the other tools.
#: The first tool is the row of :data:`COVERED`. The fence checks each tool
#: here as it checks that row: a registered tool whose source names the
#: gateway route of the action. A key must be a key of :data:`COVERED`.
ALSO: dict[str, tuple[str, ...]] = {
    # STU-B2c-2b: one door route, an action id for each verb. A write from chat asks the
    # member first (ask=1), and the resend after the approval runs the stored call.
    "POST /api/studio/apps/{}/actions/{}": (
        "studio_record_create",
        "studio_record_update",
        "studio_record_delete",
        # STU-B2b-2c: a read of a door op (<app>.<op>) through the same route.
        "studio_app_read",
    ),
    # PAR-8a: the change of the draft by the workflow helper saves its steps with the
    # PUT of the editor too.
    "PUT /api/workflows/{}": ("studio_workflow_build",),
}

#: An action -> (kind, reason). The kind is a key of :data:`EXEMPT_KINDS`.
EXEMPT: dict[str, tuple[str, str]] = {
    # ── Shell ──
    "GET /api/shell/manifest": (
        "stream",
        "The shell reads the installed packages to draw the sidebar and the command bar, and reads them "
        "again every few seconds (PKG-6). No member click sends it. The chat acts on the same records.",
    ),
    "GET /api/shell/layout": (
        "stream",
        "The shell reads the org default of the star set and the preset, to draw the sidebar for a member "
        "with no choice of their own (PKG-17b). No member click sends it. The chat sets no org default here.",
    ),
    # ── AI Studio ──
    "GET /api/studio/packages/{}/lifetime": (
        "stream",
        "The app page reads the end date of the private install of the owner, to draw the end-date bar "
        "(PKG-10e-2). No member click sends it. The chat keeps or restores an app with the Keep and Restore rows.",
    ),
    "GET /api/studio/packages/{}/connections": (
        "stream",
        "The IDE reads whether each connection of the draft is met, to draw the Connect lines (I8a). It reads "
        "again after each save and on focus. No member click sends it. In chat, a saved draft that names an unmet connection shows the same Connect card (I8d). The card reads this route, and Mira never asks for a key.",
    ),
    # ── Calendar ──
    "POST /api/calendar/connections/{}/connect": (
        "oauth",
        "Connect Google Calendar or Outlook (CE-10c). The consent happens in the browser, on the page of the "
        "provider. The chat points the member to Calendar settings.",
    ),
    # ── Email ──
    "GET /api/email/attachments/{}/download": (
        "download",
        "The bytes of a mail attachment for a download. The chat reads it with read_email_attachment.",
    ),
    "GET /api/email/messages/{}/html": (
        "download",
        "The HTML body for the reading pane's sandbox frame. The chat reads the text with read_email.",
    ),
    "GET /api/email/messages/{}/inline": (
        "download",
        "An inline picture of a mail for the reading pane. The chat reads the text of the mail.",
    ),
    # P-2c (task 3919): three buttons on the chat's own draft and reply cards.
    "POST /api/email/drafts/save": (
        "chat",
        "The Save to Drafts button of the chat's own draft card, which draft_reply raised. The "
        "member answers that card. The gateway route says that it powers the chat's draft card.",
    ),
    "POST /api/email/reply-card/bcc": (
        "chat",
        "The Bcc row of the chat's own reply card, which compose_reply raised. The member types "
        "an answer on that card. The gateway route says that no tool calls it.",
    ),
    "POST /api/email/reply-card/discard": (
        "chat",
        "The Discard button of the chat's own reply card, which compose_reply raised. The member "
        "answers that card. The gateway route says that the model never calls it.",
    ),
    "GET /api/email/reply-card/draft-state": (
        "stream",
        "A chat reply card polls whether its own draft is still in Drafts, to refresh its view "
        "(task 1526). No member click sends it. compose_reply reads the same state.",
    ),
    "GET /api/email/oauth/providers": (
        "oauth",
        "The providers that the Connect step offers before it hands the browser to Google or Microsoft.",
    ),
    "GET /api/email/oauth/{}/app": (
        "oauth",
        "The OAuth app of a provider, read by the Connect step before the browser leaves for the provider.",
    ),
    "GET /api/email/oauth/{}/authorize": (
        "oauth",
        "The browser goes to Google or Microsoft to sign in a mailbox. A chat cannot sign in for the member.",
    ),
    "POST /api/email/accounts": (
        "secret",
        "Connects an IMAP mailbox with its password. A secret never goes through the chat.",
    ),
    "POST /api/email/compose-assist/stream": (
        "stream",
        "The streamed form of compose assist in the composer. The chat writes a draft with edit_draft.",
    ),
    # ── Notes ──
    "GET /api/notes/meetings/{}/audio": (
        "download",
        "The audio file of a meeting for the player. The chat reads the transcript and the note.",
    ),
    "GET /api/notes/meetings/{}/bot/screenshot": (
        "download",
        "A screenshot of the meeting bot's screen for the diagnostics panel.",
    ),
    "GET /api/notes/meetings/{}/copilot/stream": (
        "stream",
        "The live stream of the meeting copilot during a call.",
    ),
    "GET /api/notes/meetings/{}/events": (
        "stream",
        "The live event stream of a meeting page while it records or transcribes.",
    ),
    "GET /api/notes/meetings/{}/live": (
        "stream",
        "The live transcript stream of the live console during a call.",
    ),
    "POST /api/notes/meetings/{}/live/browser-segment": (
        "stream",
        "A segment of live text that the browser posts while it records. The member starts the recording.",
    ),
    "POST /api/notes/meetings/{}/recordings/{}/chunk": (
        "stream",
        "A chunk of audio that the recorder uploads while it records. The member starts the recording.",
    ),
    "POST /api/notes/stt/live-token": (
        "stream",
        "A short token for the live speech stream of the recorder. It is not an act of the member.",
    ),
    # ── WhatsApp ──
    "GET /api/whatsapp/calls/{}/recording": (
        "download",
        "The audio file of a WhatsApp call for the player.",
    ),
    "POST /api/whatsapp/accounts": (
        "secret",
        "Connects a WhatsApp number with its Meta access token. A secret never goes through the chat.",
    ),
    "POST /api/whatsapp/accounts/verify": (
        "secret",
        "Checks a Meta access token before the number connects. A secret never goes through the chat.",
    ),
    "POST /api/whatsapp/calls/{}/audio-token": (
        "stream",
        "A short token for the audio socket of a live call. It is not an act of the member.",
    ),
    "POST /api/whatsapp/connect/embedded": (
        "oauth",
        "The return step of Meta's embedded sign-up, after the member signs in to Meta.",
    ),
    # ── Knowledge ──
    "POST /api/knowledge/sources/google_drive/connect": (
        "oauth",
        "Starts the Google Drive sign-in. The browser goes to Google, and the member consents there.",
    ),
    # ── Workflows ──
    "GET /api/workflows/runs/{}/stream": (
        "stream",
        "The live event stream of a workflow run in the editor. The run itself is a row of its own.",
    ),
    # ── Custom Apps ──
    "GET /api/apps/{}/bundle": (
        "download",
        "The built bundle of a Custom App that the sandbox frame loads. It is code, not an act.",
    ),
    "POST /api/apps/{}/consent": (
        "oauth",
        "The member's consent to the data that a Custom App asks for, given on the app's consent screen.",
    ),
    # ── Agent Registry ──
    "GET /api/observability/activity/stream": (
        "stream",
        "The live activity stream of the Agent Registry's office view.",
    ),
    # ── Memory ──
    # P-5b-1 (task 3114, owner answer OD5 A of 2026-11-25): an EXEMPT row of an
    # existing kind counts as paid. The P-2c precedent (task 3919) is the kind chat.
    "POST /api/chat/memories": (
        "chat",
        "The chat files its own conversation in the member's memory when its panel closes "
        "(lib/chatMemorySave.ts, H-236 follow-up). No member click sends it. It is the "
        "chat's own transport, and the gateway extracts the memory of each named-agent turn "
        "at the end of the run.",
    ),
    # ── Chat ──
    "GET /api/agent/artifacts/file": (
        "download",
        "The bytes of an agent's file for a preview or a download. The chat lists files with list_artifacts.",
    ),
    "GET /api/agent/workspace/{}/file": (
        "download",
        "The bytes of a chat file for the viewer and the download button. The chat writes and reads its files itself.",
    ),
    "GET /api/auth/me/chat-decisions": (
        "chat",
        "Reads the kept decisions of this chat to draw the chips under the answer (HRD-12b). "
        "It is the chat's own card. Mira holds the same list in her context.",
    ),
    "POST /api/auth/me/chat-decisions/undo": (
        "chat",
        "The Undo of a kept decision, on the chip in the chat (HRD-12b). It is the chat's own card. "
        "In the chat, the member says it and Mira calls drop_correction.",
    ),
    "GET /api/chat/active-sessions": (
        "stream",
        "A poll of the runs that are live, for the rail's spinners. The chat is the run.",
    ),
    "GET /api/chat/sessions/{}/messages": (
        "chat",
        "Reads the saved turns of a thread to draw it. It is the chat's own record.",
    ),
    "GET /api/chat/sessions/{}/room-stream": (
        "stream",
        "The live event stream of a shared chat room. The chat reads the room as it runs.",
    ),
    "POST /api/agent/cancel": (
        "chat",
        "Stops the running turn. It is the chat's own stop button.",
    ),
    "POST /api/agent/chat": (
        "chat",
        "Sends a turn to the assistant. The member does it by typing in the chat.",
    ),
    "GET /api/agent/transcribe": (
        "chat",
        "Reads whether the message box shows the mic (VOX-1). It is part of the chat's own composer.",
    ),
    "POST /api/agent/transcribe": (
        "chat",
        "Turns the member's spoken words into text in the message box (VOX-1). The member then sends it as a turn.",
    ),
    "POST /api/agent/respond-input": (
        "chat",
        "Answers a card that the chat asked. It is the chat's own transport.",
    ),
    "POST /api/chat/sessions": (
        "chat",
        "Saves a new thread when the member starts a chat. It is the chat's own record.",
    ),
    "POST /api/chat/sessions/{}/brief": (
        "chat",
        "Asks the assistant for the brief as a turn of the thread.",
    ),
    "POST /api/chat/sessions/{}/buddy-hello": (
        "chat",
        "Posts the hello once, on a member's first visit (BUD-1). The shell sends it, not the member.",
    ),
    "POST /api/chat/sessions/{}/continue": (
        "chat",
        "The pill of a held chat carries the member's last typed words to a fresh chat (WT-2). "
        "It is the chat's own move, and the member sends the text.",
    ),
    "POST /api/chat/sessions/{}/compact": (
        "chat",
        "Folds the old turns of a thread into a summary. It is the chat's own context step.",
    ),
    "POST /api/chat/sessions/{}/messages": (
        "chat",
        "Saves the turns of a thread. It is the chat's own record.",
    ),
    "POST /api/chat/sessions/{}/presence": (
        "stream",
        "The heartbeat that shows who is in a shared room. It is not an act of the member.",
    ),
    "POST /api/chat/sessions/{}/reminder-open": (
        "chat",
        "Opens a reminder as a turn of the thread. It is the chat's own transport.",
    ),
    # ── Shell, Home and approvals ──
    "GET /api/health": (
        "health",
        "The gateway health ping that the chat recovery and the service banner read.",
    ),
    # ── Settings and the assistant profile ──
    "GET /api/accounts": (
        "session",
        "The accounts of this browser for the account switcher. A chat runs inside one account.",
    ),
    "GET /api/auth/session": (
        "health",
        "The probe that confirms a sign-out. It reads the browser's own session.",
    ),
    "POST /api/accounts/{}": (
        "session",
        "Switches or signs out an account of this browser. The browser owns its session.",
    ),
    "POST /api/auth/callback/resend": (
        "session",
        "Sends the sign-in code again from the sign-in form. A chat runs only after sign-in.",
    ),
    # ── Admin, members and billing ──
    "POST /api/signup": (
        "session",
        "Creates an account and an organization. A person who signs up has no chat yet.",
    ),
    # ── Integrations ──
    "GET /api/agent-access/requests/{}": (
        "chat",
        "The approval card in the chat reads its own request to draw it, also after a reload "
        "(STU-B2c-2c). It is the chat's own card. The tool result gives Mira the request.",
    ),
    "GET /api/agent-kit": (
        "download",
        "Lists the clients of the agent kit for the download block (KIT-4b). The chat reads the playbook.",
    ),
    "GET /api/agent-kit/{}": (
        "download",
        "Streams the zip of the agent kit for one client (KIT-4b). A zip is a file for the browser.",
    ),
    "POST /api/agent-access/authorize/approve": (
        "oauth",
        "The member's approval on the consent screen of an outside agent (MCP OAuth).",
    ),
    "POST /api/agent-access/authorize/check": (
        "oauth",
        "The consent screen of an outside agent (MCP OAuth). The member answers it in the browser.",
    ),
    # EA-19d (D-EA-5, spec §10.9): the box token routes. The page shows the token once, so a
    # secret never goes through the chat. The routes are person-only: an agent cannot mint.
    "POST /api/agent-access/box-tokens": (
        "secret",
        "Makes a box token and shows it once. A secret never goes through the chat, and an agent may not mint.",
    ),
    "POST /api/agent-access/box-tokens/check": (
        "secret",
        "Asks for the one-time ticket of a box token. The token is a secret, and the route is person-only.",
    ),
    "POST /api/agent-access/box-tokens/revoke-all": (
        "secret",
        "Revokes every box token of the organization. An admin acts on a box token only in the page.",
    ),
    "POST /api/agent-access/box-tokens/{}/rotate": (
        "secret",
        "Gives a box a new token and shows it once. A secret never goes through the chat.",
    ),
    "GET /api/integrations/api/zoho_books": (
        "oauth",
        "API-5. Reads the state of the Zoho Books consent and the choices of the admin's own pending pick.",
    ),
    "POST /api/integrations/api/zoho_books/cancel": (
        "oauth",
        "API-5. The admin leaves the Zoho Books consent: the gateway revokes the pending sign-in.",
    ),
    "POST /api/integrations/api/zoho_books/pick": (
        "oauth",
        "API-5. The return step of the Zoho Books consent: the admin picks one Books organization.",
    ),
    "POST /api/integrations/api/zoho_books/start": (
        "oauth",
        "API-5. Starts the Zoho Books consent. The browser goes to Zoho, and the admin approves it there.",
    ),
    "POST /api/integrations/configure": (
        "secret",
        "Saves the keys of an integration. A secret never goes through the chat.",
    ),
    "POST /api/integrations/github/app/confirm": (
        "oauth",
        "The return step of the GitHub App install, after GitHub sends the browser back.",
    ),
    "POST /api/integrations/github/app/start": (
        "oauth",
        "Starts the GitHub App install. The browser goes to GitHub, and the member approves it there.",
    ),
    "POST /api/integrations/mcp/{}/oauth/start": (
        "oauth",
        "Starts the OAuth sign-in of an MCP server. The browser goes to the provider.",
    ),
    "PUT /api/integrations/github/token": (
        "secret",
        "Saves a GitHub token. A secret never goes through the chat.",
    ),
    # PAR-6a (task 3109): the Add form of an MCP server sends its headers, its env vars
    # and its command in the body. The owner decided that secrets stay in the UI (D-PAR-7).
    # An add by URL only, with no secret, is the open question of PAR-6b (task 3932).
    "POST /api/integrations/mcp": (
        "secret",
        "Adds an MCP server with its headers, env vars or command. A secret never goes "
        "through the chat. An add by URL only is the open question of PAR-6b (task 3932).",
    ),
}

#: An action -> the wave that gives the chat a tool for it.
DEBT: dict[str, str] = {
    # ── AI Studio ──
    # PKG-10e-2 (task 2971): the end-date bar of an app page sends Keep and Restore. A later chat tool pays both.
    "POST /api/studio/packages/{}/keep": "P-9",
    "POST /api/studio/packages/{}/restore": "P-9",
    # STU-G5: the Publish dialog reads the live version with its manifest. G1d (chat reads versions) pays it.
    "GET /api/studio/packages/{}/versions/{}": "P-9",
    # ── Tasks ──
    # The owner decides: retire the /tasks button, or build a tool with a card.
    "POST /api/tasks/ai/backfill-context": "P-1",
    # P-1b (task 3922): the six capture rows of mail. HIGH, with the owner's Approve.
    "POST /api/tasks/capture/from-email": "P-1",
    "POST /api/tasks/capture/from-email/create": "P-1",
    "POST /api/tasks/capture/from-email/enhance": "P-1",
    "POST /api/tasks/capture/from-email/preview": "P-1",
    "POST /api/tasks/capture/from-reply/create": "P-1",
    "POST /api/tasks/capture/from-reply/detect": "P-1",
    # ── Calendar ──
    # CE-10c (task 66): the Connected calendars section of Calendar settings. P-1a (task 3112)
    # paid the list read. P-1c (task 3923) pays Disconnect and Sync now.
    "DELETE /api/calendar/connections/{}": "P-1",
    # The read of the day state is task 1680 (PR 1998), not P-1a.
    "GET /api/tasks/calendar/day-state": "P-1",
    # The switch of CE-10c follow-up (task 66): Show my Metorite tasks in Google Calendar or Outlook.
    # P-1c (task 3923) pays it.
    "PATCH /api/calendar/connections/{}": "P-1",
    "POST /api/calendar/connections/{}/sync": "P-1",
    # ── Email ──
    "DELETE /api/email/messages/{}": "P-2",
    "POST /api/email/accounts/{}/backfill": "P-2",
    "POST /api/email/accounts/{}/storage/remove-older": "P-2",
    # P-2c (task 3919) left it here. create_rules_from_prompt does the same act
    # through generate/preview and rules/batch, behind its card. /rules/generate
    # runs the model again and saves what it makes, so the card would not show
    # the saved rules. The owner accepts the debt, or the gateway changes.
    "POST /api/email/rules/generate": "P-2",
    # Paid by SG-6 (task 2182): the chat sets a signature image from an image
    # the member attaches.
    "POST /api/email/signature/images": "P-2",
    # ── Notes ──
    "DELETE /api/notes/glossary/{}": "P-4",
    "DELETE /api/notes/meetings/{}": "P-4",
    "GET /api/notes/bot/identity": "P-4",
    "GET /api/notes/bots/active": "P-4",
    "GET /api/notes/bots/status": "P-4",
    "GET /api/notes/copilot/instructions": "P-4",
    "GET /api/notes/glossary": "P-4",
    "GET /api/notes/live/sessions": "P-4",
    "GET /api/notes/meetings": "P-4",
    "GET /api/notes/meetings/{}": "P-4",
    "GET /api/notes/meetings/{}/actions": "P-4",
    "GET /api/notes/meetings/{}/agenda": "P-4",
    "GET /api/notes/meetings/{}/agenda/progress": "P-4",
    "GET /api/notes/meetings/{}/bot": "P-4",
    "GET /api/notes/meetings/{}/bot/diagnostics": "P-4",
    "GET /api/notes/meetings/{}/context": "P-4",
    "GET /api/notes/meetings/{}/live/roster": "P-4",
    "GET /api/notes/meetings/{}/live/session": "P-4",
    "GET /api/notes/meetings/{}/note": "P-4",
    "GET /api/notes/settings": "P-4",
    "GET /api/notes/templates": "P-4",
    "PATCH /api/notes/meetings/{}": "P-4",
    "POST /api/notes/actions/{}/approve": "P-4",
    "POST /api/notes/actions/{}/dispatch": "P-4",
    "POST /api/notes/actions/{}/reject": "P-4",
    "POST /api/notes/bot/identity/interactive": "P-4",
    "POST /api/notes/bot/identity/sign-in": "P-4",
    "POST /api/notes/glossary": "P-4",
    "POST /api/notes/meetings": "P-4",
    "POST /api/notes/meetings/bot-join": "P-4",
    "POST /api/notes/meetings/{}/actions/approve-all": "P-4",
    "POST /api/notes/meetings/{}/agenda/chat": "P-4",
    "POST /api/notes/meetings/{}/ask": "P-4",
    "POST /api/notes/meetings/{}/bot/stop": "P-4",
    "POST /api/notes/meetings/{}/context/deep": "P-4",
    "POST /api/notes/meetings/{}/identify-speakers": "P-4",
    "POST /api/notes/meetings/{}/live/copilot": "P-4",
    "POST /api/notes/meetings/{}/recordings/start": "P-4",
    "POST /api/notes/meetings/{}/recordings/{}/complete": "P-4",
    "POST /api/notes/meetings/{}/retranscribe": "P-4",
    "POST /api/notes/meetings/{}/share/email/draft": "P-4",
    "POST /api/notes/meetings/{}/summarize": "P-4",
    "POST /api/notes/meetings/{}/upload": "P-4",
    "PUT /api/notes/copilot/instructions": "P-4",
    "PUT /api/notes/meetings/{}/agenda": "P-4",
    "PUT /api/notes/meetings/{}/attendees": "P-4",
    "PUT /api/notes/meetings/{}/brief": "P-4",
    "PUT /api/notes/meetings/{}/note": "P-4",
    "PUT /api/notes/meetings/{}/scratch": "P-4",
    "PUT /api/notes/meetings/{}/speakers": "P-4",
    "PUT /api/notes/settings": "P-4",
    # ── WhatsApp ──
    "DELETE /api/whatsapp/accounts/{}": "P-4",
    "DELETE /api/whatsapp/saved-replies/{}": "P-4",
    "PATCH /api/whatsapp/categories/{}": "P-4",
    "POST /api/me/whatsapp-link/code": "P-4",
    "POST /api/whatsapp/accounts/{}/categories/bootstrap": "P-4",
    "POST /api/whatsapp/accounts/{}/history-sync": "P-4",
    "POST /api/whatsapp/calls": "P-4",
    "POST /api/whatsapp/calls/{}": "P-4",
    "POST /api/whatsapp/capture-task": "P-4",
    "POST /api/whatsapp/chats/{}/send": "P-4",
    "POST /api/whatsapp/chats/{}/snooze": "P-4",
    "POST /api/whatsapp/chats/{}/unsnooze": "P-4",
    "POST /api/whatsapp/saved-replies": "P-4",
    # ── People ──
    "DELETE /api/people/{}/absences/{}": "P-5",
    "DELETE /api/people/{}/avatar": "P-5",
    "PATCH /api/people/{}": "P-5",
    "PATCH /api/tasks/people/{}": "P-5",
    "POST /api/me/reach/brief": "P-5",
    "POST /api/people/{}/absences": "P-5",
    "POST /api/people/{}/avatar": "P-5",
    "POST /api/people/{}/resume": "P-5",
    "POST /api/tasks/people/{}/resume": "P-5",
    "PUT /api/people/schedule": "P-5",
    "PUT /api/people/{}/credentials": "P-5",
    "PUT /api/people/{}/skills": "P-5",
    # ── Memory ──
    "DELETE /api/memory/{}/{}": "P-5",
    "GET /api/memory/{}": "P-5",
    "GET /api/memory/{}/status": "P-5",
    "POST /api/memory/{}/search": "P-5",
    # ── Workflows ──
    # PAR-8a (task 3903) paid each Workflows row. The Workflows app has no debt.
    # ── Custom Apps ──
    # PAR-9 (task 3950) paid 26 rows. The code of an app sends these two through the
    # cc bridge (ccBridge.ts), and no member click sends them. No kind of EXEMPT_KINDS
    # fits, so each waits for an owner ruling (chat_parity.md section 6.4, PAR-9).
    "POST /api/apps/{}/ai/complete": "P-6",
    "POST /api/apps/{}/tools/{}": "P-6",
    # ── Agent Registry ──
    "DELETE /api/agent/mutations/{}/delete": "P-6",
    "DELETE /api/agent/mutations/{}/dismiss": "P-6",
    "DELETE /api/agent/{}": "P-6",
    "GET /api/agent/config": "P-6",
    "GET /api/agent/list": "P-6",
    "GET /api/agent/mutations": "P-6",
    "GET /api/agent/mutations/{}/diff": "P-6",
    "GET /api/agent/{}/skills": "P-6",
    "GET /api/observability/activity/recent": "P-6",
    "GET /api/observability/avatars": "P-6",
    "GET /api/observability/cost": "P-6",
    "GET /api/observability/roster": "P-6",
    "GET /api/observability/runs": "P-6",
    "PATCH /api/agent/{}": "P-6",
    "POST /api/agent/mutations/{}/remutate": "P-6",
    "POST /api/agent/mutations/{}/{}": "P-6",
    "POST /api/agent/register": "P-6",
    "POST /api/agent/{}/pull": "P-6",
    "PUT /api/agent/{}/skills": "P-6",
    "PUT /api/observability/avatars/{}": "P-6",
    # ── Chat ──
    "DELETE /api/agent/workspace/{}/file": "P-7",
    "DELETE /api/chat/sessions/{}": "P-7",
    "DELETE /api/chat/sessions/{}/agents/{}": "P-7",
    "DELETE /api/chat/sessions/{}/participants/{}": "P-7",
    "GET /api/agent/workspace/{}": "P-7",
    "GET /api/agent/workspace/{}/history": "P-7",
    "GET /api/chat/directory": "P-7",
    "GET /api/chat/pending-asks": "P-7",
    "GET /api/chat/sessions": "P-7",
    "GET /api/chat/sessions/{}/room": "P-7",
    # CO-2b (task 3588): the topic chip reads the topics, makes a private
    # topic, and moves a chat. No chat tool lists or moves chats today, so P-7
    # pays the three with the chat tool of the chat list, as for the pin.
    "GET /api/chat/topics": "P-7",
    "GET /api/models/all": "P-7",
    "PATCH /api/agent/workspace/{}": "P-7",
    "PATCH /api/chat/sessions/{}": "P-7",
    "PATCH /api/chat/sessions/{}/participants/{}": "P-7",
    "PATCH /api/chat/sessions/{}/room": "P-7",
    "POST /api/agent/artifacts/upload": "P-7",
    "POST /api/agent/workspace/{}/promote": "P-7",
    "POST /api/agent/workspace/{}/upload": "P-7",
    "POST /api/artifacts/compile": "P-7",
    "POST /api/chat/sessions/{}/agents": "P-7",
    "POST /api/chat/sessions/{}/participants": "P-7",
    "POST /api/chat/suggestions": "P-7",
    "POST /api/chat/topics": "P-7",
    "POST /api/documents/pdf": "P-7",
    "POST /api/feedback": "P-7",
    "PUT /api/agent/workspace/{}/file": "P-7",
    "PUT /api/chat/sessions/{}/topic": "P-7",
    # ── Shell, Home and approvals ──
    # PAR-8b (task 3903) paid 9 of the 16 shell rows. The two rows of the
    # approval queue stay: the safety fence test_no_person_present.py refuses a tool that
    # names /actions/pending, a read too, and the decision waits for the owner
    # (chat_parity.md section 7, item 6). pending_actions has no requester column, so a
    # tool cannot tell an action that the chat itself asked for.
    "GET /api/actions/pending": "P-7",
    "POST /api/actions/pending/{}/{}": "P-7",
    # PAR-8b: five rows that no kind of EXEMPT_KINDS fits, so they stay debt until the owner
    # rules (chat_parity.md section 2: a new kind needs the owner).
    # The model step of the command bar: the member's own turn does that job in the chat. It
    # needs an owner ruling on the kind.
    "POST /api/shell/intent": "P-7",
    # A device act: only the browser holds the push address of its device and its secret. It
    # needs a `device` kind, an owner decision of section 2.
    "GET /api/push/public-key": "P-7",
    "POST /api/push/subscriptions": "P-7",
    "POST /api/push/subscriptions/status": "P-7",
    "DELETE /api/push/subscriptions": "P-7",
    # ── Settings and the assistant profile ──
    # SW-31b: Settings, Agents, Guidance reads the versions of a scope and
    # saves a new version. The chat tool is SW-31c (task 1531).
    "GET /api/agent-guidance/history": "P-8",
    "POST /api/agent-guidance": "P-8",
    "DELETE /api/auth/me/assistant-profile": "P-8",
    "DELETE /api/auth/me/assistant-profile/interview": "P-8",
    "DELETE /api/auth/me/assistant-profile/interview/{}": "P-8",
    # PZ-35: the chat summaries. A chat write to them is a 422 today, as for
    # the interview reset above.
    "DELETE /api/auth/me/assistant-profile/summaries": "P-8",
    "DELETE /api/auth/me/assistant-profile/summaries/{}": "P-8",
    "DELETE /api/settings/branding": "P-8",
    "GET /api/auth/me": "P-8",
    "GET /api/auth/me/agent-access": "P-8",
    "GET /api/auth/me/appearance": "P-8",
    "GET /api/auth/me/assistant-profile": "P-8",
    "GET /api/auth/me/assistant-profile/address": "P-8",
    "GET /api/auth/me/assistant-profile/block": "P-8",
    "GET /api/auth/me/assistant-profile/export": "P-8",
    "GET /api/auth/me/assistant-profile/summaries": "P-8",
    "GET /api/auth/me/shell": "P-8",
    "GET /api/auth/me/zone": "P-8",
    "GET /api/settings/appearance": "P-8",
    "GET /api/settings/branding": "P-8",
    "GET /api/settings/llm": "P-8",
    "GET /api/settings/number-system": "P-8",
    "PUT /api/auth/me/agent-access": "P-8",
    "PUT /api/auth/me/appearance": "P-8",
    "PUT /api/auth/me/shell": "P-8",
    "PUT /api/auth/me/zone": "P-8",
    "PUT /api/settings/appearance": "P-8",
    "PUT /api/settings/branding": "P-8",
    "PUT /api/settings/number-system": "P-8",
    "PUT /api/settings/number-system/me": "P-8",
    # ── Admin, members and billing ──
    "GET /api/billing/catalog": "P-8",
    "GET /api/billing/members": "P-8",
    "GET /api/billing/orders/{}": "P-8",
    "GET /api/billing/summary": "P-8",
    "GET /api/billing/usage/activity": "P-8",
    "GET /api/billing/usage/apps": "P-8",
    "GET /api/billing/usage/members": "P-8",
    "POST /api/admin/members/invite": "P-8",
    "POST /api/billing/orders": "P-8",
    "POST /api/billing/orders/{}/redeem": "P-8",
    # ── Integrations ──
    # PAR-6a (task 3109) paid 17 rows. The four left wait for an owner decision
    # (PAR-6b, task 3932).
    # EA-17c-2: Change access in the AI agents view. The chat tool is not built:
    # a change of a grant waits for the owner.
    "PATCH /api/agent-access/connections/{}": "P-8",
    # MCP-P1b-1: Approve or Decline on AppRequestCard. In a chat, the card of
    # STU-B2c-2c (task 1866) is the chat surface of the decision: the member
    # presses Approve or Decline on the card. No chat tool decides, because
    # Mira calls as the member and would approve her own change. So the
    # decision stays debt of P-8. Fence: test_integrations_admin_tools.py.
    "POST /api/agent-access/requests/{}/decision": "P-8",
    # A save of a custom API definition, and the install of a plugin from a URL.
    "POST /api/integrations/custom": "P-8",
    "POST /api/integrations/plugins": "P-8",
}

#: The debt of each app at P-0 (2026-11-01, ``0ffbd35d3``), the baseline. A ceiling of
#: ``DEBT_CEILING`` above its baseline went UP, and its comment must name the task
#: that pays it: "paid by task N", with N a task of the wave of that app
#: (:data:`WAVES`). An app that P-0 did not hold (AI Studio) has the baseline 0.
#: PAR-1b (task 3100). Fence: ``test_chat_parity.py``.
P0_CEILING: dict[str, int] = {
    "projects": 7, "tasks": 11, "calendar": 1, "email": 13, "crm": 74, "notes": 51,
    "whatsapp": 25, "people": 19, "knowledge": 17, "memory": 5, "workflows": 24,
    "apps": 29, "agents": 20, "chat": 25, "shell": 15, "settings": 21, "admin": 36,
    "integrations": 19, "studio": 0,
}

#: The most debt that each app may carry. A count may go down, never up.
#: Lower the number in the change that pays the debt.
DEBT_CEILING: dict[str, int] = {
    "projects": 7,  # F3: UI_PLANNED, and UI_ACTIONS rows whose tool is PLANNED. SW-43c (task 1802) built the chat tools of agentRunsApi.withdrawAnswer and agentRunsApi.replaceAnswer (9 to 7).
    "tasks": 7,  # P-1a (task 3112) paid enrich and suggest-title (9 to 7).
    "calendar": 4,  # CE-10c (task 66) added the list read, Sync now and Disconnect of the calendar connections, paid by P-1 (1 to 4). The switch of CE-10c follow-up (task 66) added the PATCH, paid by P-1 (5). P-1a (task 3112) paid the list read (5 to 4). Paid by task 3923. The day-state read is task 1680.
    "email": 5,  # SG-1 (task 2177, 2026-11-07) added the signature image upload, paid by SG-6 (task 2182). Paid by task 3111. P-2c (task 3919) exempted drafts/save, reply-card/bcc and reply-card/discard as the chat's own cards, and paid rules/patterns/review (14 to 10). P-2a (task 3111) paid the search, the facets, the folders, the older-mail read and the org counts (10 to 5).
    "crm": 67,  # CRM-M2a: the manifest decides. The count is UI actions with no built tool. Field Studio GET /fields reaches crm_list_fields, so it left the debt (73 to 72). Task 3951 (CRM-M2e) paid the add, change and order reset of a stage and the add and change of a lost reason with crm_edit_status and crm_edit_lost_reason (72 to 67). The two DELETE rows and the PUT of the order stay debt: the agent sends no DELETE and no PUT (apps/AGENTS.md).
    "notes": 51,
    "whatsapp": 13,  # P-4a (task 3960) paid the 12 read rows of WhatsApp with 12 read tools (25 to 13). Paid by task 3960.
    "people": 12,  # P-5b-1 (task 3114) paid the directory, my row, my work, the work of a person, and the Reach status and preview (19 to 13). P-5b-6 (task 3947, owner answer OD3 A) deleted the dead client code that created a person, so POST /api/tasks/people left the UI (13 to 12). The gateway route stays.
    "knowledge": 4,  # PAR-3 (task 3105) built create_page, edit_page, create_space and edit_space, which pay POST and PATCH of a page and of a space (19 to 15). PAR-3b (task 3901) built move_page, revert_page and eight reads (15 to 4). Publish, archive, restore (chat_parity.md §7 item 7) and the search (AD-11) stay.
    "memory": 4,  # P-5b-1 (task 3114, owner answer OD5 A) made the unmount save of the chat (POST /api/chat/memories) a chat exemption (5 to 4). The four Mem0 rows stay debt until KG-11 (OD2 A).
    "workflows": 0,  # STB-6a (task 3117) paid the list and the read of one workflow (24 to 22). STB-6b (task 3118) paid publish, rollback, disable and enable (22 to 18). PAR-8a (task 3903) paid the other 18 with seven tools of studio-workflows (18 to 0).
    "apps": 2,  # PAR-9: 26 rows paid by task 3950 with the six tools of studio-apps (28 to 2). The model call and the tool call of the cc bridge wait for an owner ruling on their kind.
    "agents": 20,
    "chat": 28,  # CO-11 (task 3510) added Pin and Unpin of a chat (25 to 27). CO-2b (task 3588) added the topic read, the topic create and the move of a chat (27 to 30). CO-11b (task 3563) paid both pin routes with pin_chat (30 to 28). P-7 pays the three topic routes with the chat tool of the chat list, a separate slice. Paid by task 3115.
    "shell": 7,  # PKG-17c-1 (task 2969) added the PUT of the org shell layout. PAR-8b (task 3903) paid 9 rows with the Home tools (16 to 7). The two rows of the approval queue wait for the owner (chat_parity.md section 7, item 6). The intent row and the four push rows wait for an owner ruling on their kind (section 2).
    "settings": 30,  # PZ-35 added the three chat summary actions, and RX-7 the zone read and write. SW-31b added the guidance read and save, paid by SW-31c (task 1531). PKG-20 added the appearance read and write. Paid by task 3108.
    "admin": 10,  # PAR-2a (task 3102) paid the member list, the requests, the role list, the status, the roles and the answer of a request (36 to 30). PAR-4a (task 3107) paid the six group rows, the features read, the role create and delete, the access read, the overrides write, the removal preview and the org rename (30 to 17). PAR-4c (task 3935) paid the remove and the purge, and PAR-4d (task 3936) paid the five seat rows with read_seats and set_seat (17 to 10).
    "integrations": 4,  # MCP-P1b-1 added the app-request list and decision (task 1703). EA-17c-2 (task 1814) added the PATCH of Change access. PAR-6a (task 3109) paid 17 rows with the org-admin pack and made the MCP add a secret exemption (22 to 4). The grant change, the decision, the plugin install and the custom API wait for the owner (task 3932).
    "studio": 3,  # PKG-10e-2 added Keep and Restore of the end-date bar (task 2971), paid by P-9 (1 to 3). G5 adds the version read. A5c-2 paid the two delete requests of BETA-1. B2c-2b adds no debt: its writes are ALSO rows of the door route. STU-K1a-ui (task 1203) added the template list and the one-step install, which STU-K1d (task 2967) paid with studio_template_list and studio_create_from_template (5 to 3). Paid by task 3115.
}

#: A UI file -> the most requests in it that the fence cannot read. A new
#: request that the reader cannot follow fails. Write its path as a literal.
UNREAD_CEILING: dict[str, int] = {
    # `call(request.path)`: the export path comes from the caller.
    "app/crm/lib/api.ts": 1,
    # `call(id)`: the path is a person id, from the caller.
    "app/people/lib/api.ts": 1,
    # `usePdfDownload(pdfUrl)`: a PDF of a chat card.
    "components/genUITemplates.tsx": 2,
    # The Undo of a profile tool: the verb comes with the card.
    "components/profile/ProfileToolCards.tsx": 1,
    # The reminder chips: `actRequest` returns the url, and the verb is set where it is sent.
    # RX-2 to RX-4 (epic task 1402): Done, Snooze, Later, Undo and Change time
    # of a reminder go through `postReminder` and `moveRequest`, so the fence
    # cannot read the verb. Each act has a chat tool: resolve_reminder,
    # reschedule_reminder and reopen_reminder. The receipt's Change time is the
    # one of `reminderReceipt.ts`.
    "lib/reminderReceipt.ts": 1,
    "lib/shell/reminderOpen.ts": 8,
}
