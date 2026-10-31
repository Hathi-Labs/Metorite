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
  that gateway route.
* :data:`EXEMPT` names a kind from :data:`EXEMPT_KINDS` and a reason. It is
  only for a request that is not a member's act in any sense.
* :data:`DEBT` names the wave that will give the chat a tool. The count of
  each app may only go down (:data:`DEBT_CEILING`).

Two apps keep their own tables, and this module does not copy them:

* **Projects.** Fence F3 holds each method of the Projects client
  (``skill_projects.manifest.UI_ACTIONS``, ``UI_EXEMPT``, ``UI_PLANNED``). A
  request to ``/api/projects/...`` from any other file takes its decision
  from the Projects manifest row of its route.
* **Knowledge.** A request to ``/api/knowledge/...`` takes its decision
  from the Knowledge manifest row (``acb_skills.knowledge_manifest``).

A row here for a route of those two apps is allowed only where the manifest
gives no built tool, for example a route of the day planner that a Tasks
tool reaches.

This module is data. It imports nothing, so any reader can load it.
"""

from __future__ import annotations

__all__ = [
    "APPS",
    "APP_OF",
    "COVERED",
    "DEBT",
    "DEBT_CEILING",
    "EXEMPT",
    "EXEMPT_KINDS",
    "F3_FILES",
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
}

#: A BFF path prefix -> its app. The longest prefix that matches wins.
APP_OF: tuple[tuple[str, str], ...] = (
    ("/api/projects/my/calendar", "calendar"),
    ("/api/projects/my/reminders", "calendar"),
    ("/api/tasks/calendar", "calendar"),
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
    ("/api/auth", "settings"),
    ("/api/accounts", "settings"),
    ("/api/admin", "admin"),
    ("/api/billing", "admin"),
    ("/api/org", "admin"),
    ("/api/signup", "admin"),
    ("/api/integrations", "integrations"),
    ("/api/agent-access", "integrations"),
)


def app_of(path: str) -> str | None:
    """The app of a BFF path, or ``None`` when no prefix holds it."""
    best = ""
    found: str | None = None
    for prefix, app in APP_OF:
        if (path == prefix or path.startswith(prefix + "/")) and len(prefix) > len(best):
            best, found = prefix, app
    return found


#: A wave of the epic -> what it gives the chat, and the apps it covers.
#: P-1 is the next slice. The epic sets the order of the waves after it.
WAVES: dict[str, tuple[str, tuple[str, ...]]] = {
    "P-1": ("Calendar and Tasks", ("calendar", "tasks")),
    "P-2": ("Email", ("email",)),
    "P-3": ("CRM", ("crm",)),
    "P-4": ("Notes and WhatsApp", ("notes", "whatsapp")),
    "P-5": ("People, Knowledge and Memory", ("people", "knowledge", "memory")),
    "P-6": ("Workflows, Custom Apps and the Agent Registry", ("workflows", "apps", "agents")),
    "P-7": ("Chat threads and the shell", ("chat", "shell")),
    "P-8": ("Settings, Admin and Integrations", ("settings", "admin", "integrations")),
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
    "packages/acb_skills/acb_skills/crm_tools.py",
    "packages/acb_skills/acb_skills/profile_tools.py",
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
    # ── Calendar ──
    "GET /api/projects/my/calendar/estimate-stats": "my_tasks_estimate_stats",
    "POST /api/tasks/calendar/{}": "my_tasks_plan_day",
    "PUT /api/tasks/calendar/day-state": "my_tasks_set_one_thing",
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
    # ── CRM ──
    "GET /api/crm/deals/{}/contacts": "crm_deal_contacts",
    "GET /api/crm/fields/{}": "crm_list_fields",
    "GET /api/crm/lost-reasons": "update_deal_status",
    "GET /api/crm/mode": "crm_list_records",
    "GET /api/crm/modules": "crm_list_modules",
    "GET /api/crm/modules/{}/records": "crm_list_module_records",
    "GET /api/crm/modules/{}/records/{}": "crm_get_module_record",
    "GET /api/crm/owners": "crm_list_owners",
    "GET /api/crm/pipeline": "get_pipeline",
    "GET /api/crm/pipelines": "crm_list_pipelines",
    "GET /api/crm/reports/funnel": "crm_report",
    "GET /api/crm/reports/owners": "crm_report",
    "GET /api/crm/reports/pipeline": "crm_report",
    "GET /api/crm/reports/win-loss": "crm_report",
    "GET /api/crm/statuses/{}": "crm_aggregate",
    "GET /api/crm/{}": "search_crm",
    "GET /api/crm/{}/{}": "get_record",
    "GET /api/crm/{}/{}/timeline": "get_timeline",
    "PATCH /api/crm/{}/{}": "update_deal_status",
    "POST /api/crm/leads/{}/convert": "convert_lead",
    "POST /api/crm/{}": "create_lead",
    "POST /api/crm/{}/{}/activities": "log_activity",
    # ── WhatsApp ──
    "GET /api/whatsapp/accounts": "list_whatsapp_accounts",
    "GET /api/whatsapp/chats": "list_whatsapp_chats",
    "GET /api/whatsapp/chats/{}/context": "whatsapp_chat_context",
    "GET /api/whatsapp/chats/{}/messages": "read_whatsapp_chat",
    "POST /api/whatsapp/chats/{}/draft": "draft_whatsapp_reply",
    "POST /api/whatsapp/commitments/{}/nudge": "draft_waiting_on_nudge",
    "POST /api/whatsapp/messages/{}/transcribe": "transcribe_whatsapp_voice_note",
    # ── People ──
    "GET /api/tasks/people": "my_tasks_people",
    # ── Chat ──
    "GET /api/agent/artifacts": "list_artifacts",
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
}

#: An action -> (kind, reason). The kind is a key of :data:`EXEMPT_KINDS`.
EXEMPT: dict[str, tuple[str, str]] = {
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
    # ── CRM ──
    "GET /api/crm/export/all.zip": (
        "download",
        "The ZIP file of every CRM record for a download. The chat reads the records.",
    ),
    "POST /api/crm/connection/zoho/start": (
        "oauth",
        "Starts the Zoho sign-in. The browser goes to Zoho, and the member signs in there.",
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
    # ── Chat ──
    "GET /api/agent/artifacts/file": (
        "download",
        "The bytes of an agent's file for a preview or a download. The chat lists files with list_artifacts.",
    ),
    "GET /api/agent/workspace/{}/file": (
        "download",
        "The bytes of a chat file for the viewer and the download button. The chat writes and reads its files itself.",
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
    "POST /api/agent-access/authorize/approve": (
        "oauth",
        "The member's approval on the consent screen of an outside agent (MCP OAuth).",
    ),
    "POST /api/agent-access/authorize/check": (
        "oauth",
        "The consent screen of an outside agent (MCP OAuth). The member answers it in the browser.",
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
}

#: An action -> the wave that gives the chat a tool for it.
DEBT: dict[str, str] = {
    # ── Tasks ──
    "GET /api/tasks/settings": "P-1",
    "POST /api/tasks/ai/backfill-context": "P-1",
    "POST /api/tasks/capture/from-email": "P-1",
    "POST /api/tasks/capture/from-email/create": "P-1",
    "POST /api/tasks/capture/from-email/enhance": "P-1",
    "POST /api/tasks/capture/from-email/preview": "P-1",
    "POST /api/tasks/capture/from-reply/create": "P-1",
    "POST /api/tasks/capture/from-reply/detect": "P-1",
    "POST /api/tasks/items/{}/enrich": "P-1",
    "POST /api/tasks/items/{}/suggest-title": "P-1",
    "PUT /api/tasks/settings": "P-1",
    # ── Calendar ──
    "GET /api/tasks/calendar/day-state": "P-1",
    # ── Email ──
    "DELETE /api/email/messages/{}": "P-2",
    "GET /api/email/accounts/{}/folders": "P-2",
    "GET /api/email/accounts/{}/storage/older": "P-2",
    "GET /api/email/admin/connections": "P-2",
    "GET /api/email/messages/facets": "P-2",
    "GET /api/email/search": "P-2",
    "POST /api/email/accounts/{}/backfill": "P-2",
    "POST /api/email/accounts/{}/storage/remove-older": "P-2",
    "POST /api/email/drafts/save": "P-2",
    "POST /api/email/reply-card/bcc": "P-2",
    "POST /api/email/reply-card/discard": "P-2",
    "POST /api/email/rules/generate": "P-2",
    "POST /api/email/rules/patterns/review": "P-2",
    # ── CRM ──
    "DELETE /api/crm/deals/{}/contacts/{}": "P-3",
    "DELETE /api/crm/field-sections": "P-3",
    "DELETE /api/crm/fields": "P-3",
    "DELETE /api/crm/lost-reasons/{}": "P-3",
    "DELETE /api/crm/questions/{}/dismiss": "P-3",
    "DELETE /api/crm/questions/{}/mine": "P-3",
    "DELETE /api/crm/rules/{}": "P-3",
    "DELETE /api/crm/statuses/{}/{}": "P-3",
    "DELETE /api/crm/views/{}": "P-3",
    "DELETE /api/crm/{}/{}": "P-3",
    "GET /api/crm/analysis/status": "P-3",
    "GET /api/crm/connection": "P-3",
    "GET /api/crm/conversion-mapping": "P-3",
    "GET /api/crm/cutover/preview": "P-3",
    "GET /api/crm/cutover/status": "P-3",
    "GET /api/crm/digests": "P-3",
    "GET /api/crm/export/{}": "P-3",
    "GET /api/crm/field-rules": "P-3",
    "GET /api/crm/fields": "P-3",
    "GET /api/crm/mapping/plans": "P-3",
    "GET /api/crm/mapping/plans/{}": "P-3",
    "GET /api/crm/mapping/suggestions": "P-3",
    "GET /api/crm/modules/{}/records/{}/timeline": "P-3",
    "GET /api/crm/questions": "P-3",
    "GET /api/crm/questions/mine": "P-3",
    "GET /api/crm/questions/{}/sum-check": "P-3",
    "GET /api/crm/rules": "P-3",
    "GET /api/crm/settings": "P-3",
    "GET /api/crm/settings/counts": "P-3",
    "GET /api/crm/sync/health": "P-3",
    "GET /api/crm/sync/status": "P-3",
    "GET /api/crm/views": "P-3",
    "PATCH /api/crm/activities/{}": "P-3",
    "PATCH /api/crm/field-rules/{}": "P-3",
    "PATCH /api/crm/field-sections": "P-3",
    "PATCH /api/crm/fields": "P-3",
    "PATCH /api/crm/lost-reasons/{}": "P-3",
    "PATCH /api/crm/modules/{}/records/{}": "P-3",
    "PATCH /api/crm/rules/{}": "P-3",
    "PATCH /api/crm/settings": "P-3",
    "PATCH /api/crm/statuses/{}/{}": "P-3",
    "PATCH /api/crm/views/{}": "P-3",
    "POST /api/crm/analysis/rerun": "P-3",
    "POST /api/crm/connection/disconnect": "P-3",
    "POST /api/crm/cutover": "P-3",
    "POST /api/crm/cutover/start": "P-3",
    "POST /api/crm/deals/{}/contacts": "P-3",
    "POST /api/crm/field-rules": "P-3",
    "POST /api/crm/field-sections": "P-3",
    "POST /api/crm/fields": "P-3",
    "POST /api/crm/import/zoho/stages": "P-3",
    "POST /api/crm/lost-reasons": "P-3",
    "POST /api/crm/mapping/plans": "P-3",
    "POST /api/crm/mapping/plans/{}/approve": "P-3",
    "POST /api/crm/mapping/plans/{}/restore": "P-3",
    "POST /api/crm/mapping/preview": "P-3",
    "POST /api/crm/mapping/suggestions/{}/approve": "P-3",
    "POST /api/crm/mapping/suggestions/{}/dismiss": "P-3",
    "POST /api/crm/mapping/suggestions/{}/undo": "P-3",
    "POST /api/crm/questions/accept-best": "P-3",
    "POST /api/crm/questions/{}/answer": "P-3",
    "POST /api/crm/questions/{}/dismiss": "P-3",
    "POST /api/crm/reset": "P-3",
    "POST /api/crm/rules": "P-3",
    "POST /api/crm/statuses/{}": "P-3",
    "POST /api/crm/statuses/{}/order/reset": "P-3",
    "POST /api/crm/sync/zoho": "P-3",
    "POST /api/crm/views": "P-3",
    "PUT /api/crm/catalogue/{}": "P-3",
    "PUT /api/crm/conversion-mapping": "P-3",
    "PUT /api/crm/field-layout": "P-3",
    "PUT /api/crm/settings/labels": "P-3",
    "PUT /api/crm/statuses/{}/order": "P-3",
    "PUT /api/crm/sync/modules/{}": "P-3",
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
    "GET /api/me/whatsapp-link": "P-4",
    "GET /api/whatsapp/calls": "P-4",
    "GET /api/whatsapp/calls/diagnostics": "P-4",
    "GET /api/whatsapp/calls/{}/events": "P-4",
    "GET /api/whatsapp/categories": "P-4",
    "GET /api/whatsapp/connection/info": "P-4",
    "GET /api/whatsapp/labels": "P-4",
    "GET /api/whatsapp/pulse": "P-4",
    "GET /api/whatsapp/rules/preview": "P-4",
    "GET /api/whatsapp/saved-replies": "P-4",
    "GET /api/whatsapp/streams": "P-4",
    "GET /api/whatsapp/templates": "P-4",
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
    "GET /api/me/reach/preview": "P-5",
    "GET /api/me/reach/status": "P-5",
    "GET /api/people": "P-5",
    "GET /api/people/me": "P-5",
    "GET /api/people/me/work": "P-5",
    "GET /api/people/{}/work": "P-5",
    "PATCH /api/people/{}": "P-5",
    "PATCH /api/tasks/people/{}": "P-5",
    "POST /api/me/reach/brief": "P-5",
    "POST /api/people/{}/absences": "P-5",
    "POST /api/people/{}/avatar": "P-5",
    "POST /api/people/{}/resume": "P-5",
    "POST /api/tasks/people": "P-5",
    "POST /api/tasks/people/{}/resume": "P-5",
    "PUT /api/people/schedule": "P-5",
    "PUT /api/people/{}/credentials": "P-5",
    "PUT /api/people/{}/skills": "P-5",
    # ── Memory ──
    "DELETE /api/memory/{}/{}": "P-5",
    "GET /api/memory/{}": "P-5",
    "GET /api/memory/{}/status": "P-5",
    "POST /api/chat/memories": "P-5",
    "POST /api/memory/{}/search": "P-5",
    # ── Workflows ──
    "DELETE /api/workflows/modules/{}": "P-6",
    "DELETE /api/workflows/{}": "P-6",
    "GET /api/workflows": "P-6",
    "GET /api/workflows/catalog": "P-6",
    "GET /api/workflows/catalog/search": "P-6",
    "GET /api/workflows/modules": "P-6",
    "GET /api/workflows/modules/{}": "P-6",
    "GET /api/workflows/runs/{}": "P-6",
    "GET /api/workflows/{}": "P-6",
    "GET /api/workflows/{}/runs": "P-6",
    "POST /api/workflows": "P-6",
    "POST /api/workflows/modules": "P-6",
    "POST /api/workflows/modules/generate": "P-6",
    "POST /api/workflows/modules/test": "P-6",
    "POST /api/workflows/{}/copilot": "P-6",
    "POST /api/workflows/{}/disable": "P-6",
    "POST /api/workflows/{}/duplicate": "P-6",
    "POST /api/workflows/{}/enable": "P-6",
    "POST /api/workflows/{}/publish": "P-6",
    "POST /api/workflows/{}/run": "P-6",
    "POST /api/workflows/{}/validate": "P-6",
    "POST /api/workflows/{}/versions/{}/rollback": "P-6",
    "PUT /api/workflows/modules/{}": "P-6",
    "PUT /api/workflows/{}": "P-6",
    # ── Custom Apps ──
    "DELETE /api/apps/{}/data/{}/{}": "P-6",
    "DELETE /api/apps/{}/files/content": "P-6",
    "DELETE /api/apps/{}/grants/{}": "P-6",
    "DELETE /api/apps/{}/pin": "P-6",
    "GET /api/apps": "P-6",
    "GET /api/apps/pins": "P-6",
    "GET /api/apps/{}": "P-6",
    "GET /api/apps/{}/checkpoints": "P-6",
    "GET /api/apps/{}/data/{}": "P-6",
    "GET /api/apps/{}/data/{}/{}": "P-6",
    "GET /api/apps/{}/files": "P-6",
    "GET /api/apps/{}/files/content": "P-6",
    "GET /api/apps/{}/grants": "P-6",
    "GET /api/apps/{}/me": "P-6",
    "GET /api/apps/{}/usage": "P-6",
    "GET /api/apps/{}/versions": "P-6",
    "PATCH /api/apps/{}": "P-6",
    "POST /api/apps": "P-6",
    "POST /api/apps/{}/ai/complete": "P-6",
    "POST /api/apps/{}/build": "P-6",
    "POST /api/apps/{}/fork": "P-6",
    "POST /api/apps/{}/grants": "P-6",
    "POST /api/apps/{}/publish": "P-6",
    "POST /api/apps/{}/restore": "P-6",
    "POST /api/apps/{}/rollback": "P-6",
    "POST /api/apps/{}/sync": "P-6",
    "POST /api/apps/{}/tools/{}": "P-6",
    "PUT /api/apps/{}/data/{}/{}": "P-6",
    "PUT /api/apps/{}/files/content": "P-6",
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
    "POST /api/documents/pdf": "P-7",
    "POST /api/feedback": "P-7",
    "PUT /api/agent/workspace/{}/file": "P-7",
    # ── Shell, Home and approvals ──
    "DELETE /api/push/subscriptions": "P-7",
    "GET /api/actions/pending": "P-7",
    "GET /api/brief/today": "P-7",
    "GET /api/push/public-key": "P-7",
    "GET /api/shell/feed": "P-7",
    "GET /api/shell/feed/prefs": "P-7",
    "GET /api/shell/needs": "P-7",
    "GET /api/shell/search": "P-7",
    "POST /api/actions/pending/{}/{}": "P-7",
    "POST /api/push/subscriptions": "P-7",
    "POST /api/push/subscriptions/status": "P-7",
    "POST /api/push/test": "P-7",
    "POST /api/shell/feed/state": "P-7",
    "POST /api/shell/intent": "P-7",
    "PUT /api/shell/feed/prefs": "P-7",
    # ── Settings and the assistant profile ──
    "DELETE /api/auth/me/assistant-profile": "P-8",
    "DELETE /api/auth/me/assistant-profile/interview": "P-8",
    "DELETE /api/auth/me/assistant-profile/interview/{}": "P-8",
    "DELETE /api/settings/branding": "P-8",
    "GET /api/auth/me": "P-8",
    "GET /api/auth/me/agent-access": "P-8",
    "GET /api/auth/me/assistant-profile": "P-8",
    "GET /api/auth/me/assistant-profile/address": "P-8",
    "GET /api/auth/me/assistant-profile/block": "P-8",
    "GET /api/auth/me/assistant-profile/export": "P-8",
    "GET /api/auth/me/shell": "P-8",
    "GET /api/settings/appearance": "P-8",
    "GET /api/settings/branding": "P-8",
    "GET /api/settings/llm": "P-8",
    "GET /api/settings/number-system": "P-8",
    "PUT /api/auth/me/agent-access": "P-8",
    "PUT /api/auth/me/shell": "P-8",
    "PUT /api/settings/appearance": "P-8",
    "PUT /api/settings/branding": "P-8",
    "PUT /api/settings/number-system": "P-8",
    "PUT /api/settings/number-system/me": "P-8",
    # ── Admin, members and billing ──
    "DELETE /api/admin/groups/{}": "P-8",
    "DELETE /api/admin/groups/{}/members/{}": "P-8",
    "DELETE /api/admin/members/{}": "P-8",
    "DELETE /api/admin/members/{}/purge": "P-8",
    "DELETE /api/admin/roles/{}": "P-8",
    "GET /api/admin/features": "P-8",
    "GET /api/admin/groups": "P-8",
    "GET /api/admin/members": "P-8",
    "GET /api/admin/members/requests": "P-8",
    "GET /api/admin/members/{}/access": "P-8",
    "GET /api/admin/members/{}/seal-preview": "P-8",
    "GET /api/admin/roles": "P-8",
    "GET /api/billing/catalog": "P-8",
    "GET /api/billing/members": "P-8",
    "GET /api/billing/orders/{}": "P-8",
    "GET /api/billing/seats": "P-8",
    "GET /api/billing/summary": "P-8",
    "GET /api/billing/usage/activity": "P-8",
    "GET /api/billing/usage/apps": "P-8",
    "GET /api/billing/usage/members": "P-8",
    "GET /api/org/seats": "P-8",
    "PATCH /api/admin/groups/{}": "P-8",
    "PATCH /api/admin/members/{}": "P-8",
    "PATCH /api/admin/organization": "P-8",
    "POST /api/admin/groups": "P-8",
    "POST /api/admin/groups/{}/members": "P-8",
    "POST /api/admin/members/invite": "P-8",
    "POST /api/admin/members/requests/{}/{}": "P-8",
    "POST /api/admin/roles": "P-8",
    "POST /api/billing/orders": "P-8",
    "POST /api/billing/orders/{}/redeem": "P-8",
    "POST /api/billing/seats/assign": "P-8",
    "POST /api/billing/seats/release": "P-8",
    "POST /api/billing/seats/{}": "P-8",
    "PUT /api/admin/members/{}/overrides": "P-8",
    "PUT /api/admin/members/{}/roles": "P-8",
    # ── Integrations ──
    "DELETE /api/agent-access/connections/{}": "P-8",
    "DELETE /api/integrations/agent-connections/{}": "P-8",
    "DELETE /api/integrations/github": "P-8",
    "DELETE /api/integrations/plugins/{}": "P-8",
    "GET /api/agent-access/connections": "P-8",
    "GET /api/integrations/agent-connections": "P-8",
    "GET /api/integrations/connections": "P-8",
    "GET /api/integrations/github/account": "P-8",
    "GET /api/integrations/plugins": "P-8",
    "GET /api/integrations/skills": "P-8",
    "GET /api/integrations/status": "P-8",
    "GET /api/integrations/test": "P-8",
    "PATCH /api/integrations/mcp/{}": "P-8",
    "POST /api/integrations/connections/{}/disconnect": "P-8",
    "POST /api/integrations/custom": "P-8",
    "POST /api/integrations/discover": "P-8",
    "POST /api/integrations/mcp": "P-8",
    "POST /api/integrations/mcp/{}/test": "P-8",
    "POST /api/integrations/plugins": "P-8",
}

#: The most debt that each app may carry. A count may go down, never up.
#: Lower the number in the change that pays the debt.
DEBT_CEILING: dict[str, int] = {
    "projects": 7,  # F3: UI_PLANNED, and UI_ACTIONS rows whose tool is PLANNED.
    "tasks": 11,
    "calendar": 1,
    "email": 13,
    "crm": 74,
    "notes": 51,
    "whatsapp": 25,
    "people": 19,
    "knowledge": 17,  # The Knowledge manifest's planned rows.
    "memory": 5,
    "workflows": 24,
    "apps": 29,
    "agents": 20,
    "chat": 25,
    "shell": 15,
    "settings": 21,
    "admin": 36,
    "integrations": 19,
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
    "lib/shell/reminderOpen.ts": 4,
}
