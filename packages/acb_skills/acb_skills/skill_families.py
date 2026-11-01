"""Skill-family registry — WS-23 S1 (specs/skills_registry.md §3, §4).

``SKILL_FAMILIES`` is the single code-declared mapping of family slug →
{label, description, tool names} covering EVERY platform tool the injection
seam (``orchestrator._tool_injection``) offers an agent. One registry, three
consumers (per the spec's single-source rule): the read-only catalog API
(``GET /integrations/skills``), the S2 toggle enforcement (future), and the
S3 generated addendum (future). ``tests/unit/test_skills_registry.py`` is the
drift gate: it fails CI if a tool is injected but missing here, appears in two
families, or is registered here but no longer injectable.

Family-set reality check (S1 acceptance says follow the CODE, not the spec's
provisional list): the spec named files-artifacts / email / whatsapp /
tasks-gtd / calendar / notes / web-research / diagrams families, but

* files-artifacts, notes, web-research and most of coding are inside the
  guaranteed ``_CORE_STANDARD_TOOL_NAMES`` floor (which grew after the spec's
  list was drafted) — they are the non-toggleable ``core`` family;
* email / whatsapp / tasks-gtd / calendar tools are agents' OWN repo-baked
  tools (governed by ``own_tool_scope``), never injected by
  ``_tool_injection.py``. Since WS-67 OA-7a (D117) the email tools join as
  TOOL PACKS (below), not as injected families;
* ``diagrams`` was explicitly "(future)" in the spec and stays out.

What remains outside the core floor: memory, history, crm, coding extras,
workflows, and the dynamic Custom-App action tools.

**Tool packs (WS-67 OA-7a, ``specs/one_assistant_context.md`` §4.2.1).** A
family with an ``owner`` key is a PACK. Its tools are the OWNER agent's own
tools (``own_tool_scope``), which injection never offers. The main assistant
loads a pack for the turns that need it (OA-7c), and
``acb_skills.tool_packs`` holds its rules block, references, words and the
flag reader. Until OA-7c no run loads a pack, so every consumer that speaks
about INJECTED tools reads :func:`injected_families` and skips a pack: the
injected scope resolve, the skill index, the per-agent matrix and the
``total_injected_tokens`` of the catalog. ``build_catalog`` still prices a
pack, from its owner's tools and its rules block, and an admin toggle of a
pack is stored and changes nothing yet.

This module is pure data + dependency-injected helpers: it never imports the
orchestrator or gateway (packages sit below apps in the import graph). The
gateway route wires in the real addendum renderer, tool callables, and the
``assemble_run_context`` tokenizer (``acb_llm.context.count_message_tokens``).
"""
from __future__ import annotations

import hashlib
import inspect
import json
import typing
from collections.abc import Callable, Mapping
from typing import Any

# ---------------------------------------------------------------------------
# The registry (code-declared data; keep alphabetised inside each family)
# ---------------------------------------------------------------------------
# ``scope_governed`` — whether a ``config.json: tool_scope`` narrows this
# family today. The core floor is always injected (never narrowable), and the
# workflow trio + granted-app tools are added AFTER scope filtering in
# ``_inject_agent_tools`` (deliberately: a grant is a per-agent permission,
# not a platform tool a static scope names).
# ``dynamic`` — tools exist only at run time (per-grant ``app_<slug>_<action>``
# functions); the static ``tools`` tuple is empty and drift-checking skips it.
# ``owner`` — WS-67 OA-7a: the agent whose OWN tools the family holds. A family
# with an owner is a tool pack (see the module docstring). Its tools are in the
# owner's ``config.json: own_tool_scope``, each in one pack only, and a pack
# holds at most ``tool_packs.MAX_PACK_TOOLS``. The drift gate checks them
# against the owner, never against injection (tests/unit/test_skills_registry.py
# and tests/unit/test_tool_packs_registry.py).

SKILL_FAMILIES: dict[str, dict[str, Any]] = {
    "core": {
        "label": "Core floor",
        # ``summary`` — ONE sentence, the line the QM-2 skill INDEX carries in
        # the prompt when ``SKILLS_INDEX_ONLY`` is on (acb_skills.skill_index).
        # It must be enough to decide "do I need this family right now?"; the
        # full guidance is the on-demand body. ``description`` stays the
        # longer admin-facing catalog text.
        "summary": (
            "Web access, file writing and rich generative UI, todo tracking, "
            "asking the user a question, diagnostics, working notes, the "
            "coding skill, delegation to other agents, fast typed "
            "decisions, and the text of a file attached in this chat."
        ),
        "description": (
            "The guaranteed baseline every agent receives regardless of "
            "tool_scope: web access, file writing & generative UI, task "
            "tracking, HITL questions, diagnostics, working notes, the "
            "coding skill, inter-agent delegation, fast typed "
            "decisions (decide, WS-31 CP-13d), and the text of a chat "
            "attachment (read_attachment, H-229). Not toggleable "
            "(skills_registry.md rule 2)."
        ),
        "tools": (
            "ask_questions",
            "call_agent",
            "call_agent_background",
            "call_agents_parallel",
            "code_task",
            "decide",
            "emit_generative_ui",
            "fetch_page",
            "get_errors",
            "list_integrations",
            "load_artifact_kit",
            "load_design_system",
            "manage_todo_list",
            "package_artifacts",
            "read_attachment",
            "recall_notes",
            "run_diagnostics",
            "run_script",
            "save_note",
            "share_artifact",
            "web_search",
            "write_artifact",
        ),
        "core": True,
        "scope_governed": False,
        "dynamic": False,
    },
    "memory": {
        "label": "Memory & knowledge graph",
        "summary": (
            "Read and write durable memory in three scopes (this user, this "
            "agent, the whole organisation) plus timeline recall and episode "
            "capture."
        ),
        "description": (
            "Episodic + bi-temporal memory across the three Mem0 scopes: "
            "this user's private memory, the agent's shared memory, and "
            "organisation-wide memory, plus timeline recall and episode "
            "capture."
        ),
        "tools": (
            "recall_agent",
            "recall_org",
            "recall_timeline",
            "remember",
            "save_agent_memory",
            "save_episode",
            "save_memory",
            "save_org_memory",
        ),
        "core": False,
        "scope_governed": True,
        "dynamic": False,
    },
    # H-315 (WS-47 WAC-12, owner 2026-10-15): a deck or a report as a PDF
    # file in the chat, through the one PDF seam. A family, not the floor:
    # its spec is long, and only the assistants that make documents hold it.
    # projects_ai_chat.md §14.10 (2026-10-16): make_report joins it. A report
    # is a card in the chat, a full view and the same PDF.
    "documents": {
        "label": "Documents",
        "summary": (
            "Make a report or a deck, with charts, tiles and tables, from "
            "Metorite's data."
        ),
        "description": (
            "A report shows as a card in the chat, with a full view and a "
            "PDF (make_report). A sales or pitch deck (16:9 slides) or A4 "
            "pages come as a PDF file (make_document). The gateway's PDF seam "
            "lays out both, with the product's charts and cards. WhatsApp "
            "sends the same PDF as a file."
        ),
        "tools": ("make_document", "make_report"),
        "core": False,
        "scope_governed": True,
        "dynamic": False,
    },
    "history": {
        "label": "Conversation history",
        "summary": (
            "Recall what was discussed in earlier sessions by searching the "
            "chat history."
        ),
        "description": (
            "Search past conversations by text, thread, agent or recency — "
            "recall what was discussed in prior sessions."
        ),
        "tools": ("query_history",),
        "core": False,
        "scope_governed": True,
        "dynamic": False,
    },
    # WS-53 CRM-C7 (crm_platform.md §20.10 item 5): the CRM read tools of
    # ``acb_skills.crm_tools``. Each one reads a ``/crm`` route as the member,
    # so the kill switch, ``feature:crm`` and the access rule decide.
    "crm": {
        "label": "CRM",
        "summary": (
            "Read the CRM as the member: records, fields, pipelines, deal "
            "contacts, owners, reports, totals by group, stage dwell and "
            "notes by meaning."
        ),
        "description": (
            "Read-only CRM tools over the /crm routes, called as the acting "
            "member: list and open records with field labels, the fields, "
            "pipelines, deal contacts and owners, the four reports, "
            "server-side totals by group, stage dwell, the mirrored Zoho "
            "modules, and a search of note text by meaning. A member without "
            "CRM access gets a refusal."
        ),
        "tools": (
            "crm_aggregate",
            "crm_deal_contacts",
            "crm_get_module_record",
            "crm_get_record",
            "crm_list_fields",
            "crm_list_module_records",
            "crm_list_modules",
            "crm_list_owners",
            "crm_list_pipelines",
            "crm_list_records",
            "crm_report",
            "crm_search_notes",
            "crm_stage_dwell",
            # WS-53 CRM-Q32-5: the org-facts read loads with this family only,
            # so the core floor does not change (crm_platform.md §18.14.4).
            "org_facts",
            # WS-53 CRM-Q32-8: the reply of a CRM question card, on the same
            # terms. The gateway gates it by CRM_QUESTIONS_ORGS.
            "crm_answer_question",
            # WS-53 CRM-Q33: a business rule from the chat, held for the
            # member's confirm. The gateway gates it by CRM_ANSWER_FIRST_ORGS.
            "crm_propose_rule",
        ),
        "core": False,
        "scope_governed": True,
        "dynamic": False,
    },
    # There is no "attachments" family since 2026-10-09: read_attachment is
    # in the core floor, because every chat can attach a file. A stored
    # toggle row for the old slug is ignored (``_resolve_declared_scope``
    # reads only slugs that are here).
    "coding": {
        "label": "Coding extras",
        "summary": (
            "Install a Python package into the agent runtime mid-task, and "
            "search public GitHub repositories for code."
        ),
        "description": (
            "Additions beyond the core coding floor (code_task/run_script "
            "are core): runtime Python package install and GitHub code "
            "search."
        ),
        "tools": ("github_repo_search", "github_search", "install_dependency"),
        "core": False,
        "scope_governed": True,
        "dynamic": False,
    },
    "workflows": {
        "label": "Workflows",
        "summary": (
            "List the organisation's published workflows, start a run, and "
            "check a run's status."
        ),
        "description": (
            "Run governed automations from the Workflows app: list published "
            "workflows, start a run, check a run's status. Injected for "
            "every agent regardless of tool_scope (writes inside a workflow "
            "are already broker-gated)."
        ),
        "tools": ("get_workflow_run", "list_workflows", "run_workflow"),
        "core": False,
        "scope_governed": False,
        "dynamic": False,
    },
    "apps": {
        "label": "Custom Apps",
        "summary": (
            "Actions of the Custom Apps granted to you, one tool per declared "
            "manifest action (app_<slug>_<action>)."
        ),
        "description": (
            "Actions of Custom Apps granted to an agent (app_grants), "
            "injected as one tool per declared manifest action "
            "(app_<slug>_<action>). Per-grant and resolved at run time — "
            "no static tool list."
        ),
        "tools": (),
        "core": False,
        "scope_governed": False,
        "dynamic": True,
        "tool_pattern": "app_<slug>_<action>",
    },
    # ── Tool packs (WS-67 OA-7a, one_assistant_context.md §4.2.1) ──────────
    # The email assistant's 59 own tools, split in two so that a mail turn
    # does not carry the setup tools (OA-5 measured about 20,800 input tokens
    # for 58 of them). ``scope_governed`` is True only so that no surface
    # calls a pack "always injected": every scope helper skips a pack before
    # it reads that key. From OA-7c a run loads a pack only where the owner
    # turned tool packs on, and the description says so, because the admin
    # reads it beside the toggle. Turned off, the toggle stops the pack.
    "email": {
        "label": "Email",
        "summary": (
            "The member's mailboxes: find, read and summarise mail, reply, "
            "send, forward, drafts, send later, follow-ups and inbox acts."
        ),
        "description": (
            "A tool pack: the Email Assistant's own tools for daily mail "
            "work, for the main assistant to load in an email turn. It holds "
            "the tools to read, find, summarise, reply, send, forward, keep "
            "drafts, send later, follow up and act on the inbox. A run loads "
            "a pack only where tool packs are on, so until then this switch "
            "has no effect."
        ),
        "tools": (
            "bulk_manage_inbox",
            "cancel_scheduled_send",
            "compose_reply",
            "create_label",
            "digest",
            "draft_reply",
            "edit_draft",
            "find_contact",
            "find_follow_ups",
            "find_priority",
            "forward_email",
            "get_account_overview",
            "list_accounts",
            "list_artifacts",
            "list_labels",
            "list_scheduled_sends",
            "manage_inbox",
            "mark_thread_done",
            # Built only while NARROWING_AGENTS names the owner
            # (agents.py ``_narrow_tool``). It is in own_tool_scope.
            "narrow_and_read",
            "present_email_groups",
            "query_inbox",
            "query_insights",
            "read_email",
            "read_email_attachment",
            "read_thread",
            "reclassify_reply_zero",
            "schedule_send",
            "send_draft",
            "send_email",
            "snooze_email",
            "sync_account",
        ),
        "owner": "email-assistant",
        "core": False,
        "scope_governed": True,
        "dynamic": False,
    },
    "email-setup": {
        "label": "Email setup",
        "summary": (
            "Set up the email assistant: rules, settings, knowledge, writing "
            "voice, senders and categories."
        ),
        "description": (
            "A tool pack: the Email Assistant's own tools to set it up, for "
            "the main assistant to load when a turn needs them. It holds the "
            "rules, the mailbox and assistant settings, the knowledge, the "
            "writing voice, the senders and the categories. A run loads a "
            "pack only where tool packs are on, so until then this switch "
            "has no effect."
        ),
        "tools": (
            "apply_settings_to_mailboxes",
            "auto_categorize_inbox",
            "categorize_senders",
            "copy_rules",
            "create_rule",
            "create_rules_from_prompt",
            "delete_knowledge",
            "delete_rule",
            "forget_pattern",
            "generate_writing_style",
            "get_rules_and_settings",
            "install_default_rules",
            "learn_rule_pattern",
            "list_knowledge",
            "list_patterns",
            "list_rule_history",
            "list_senders",
            "resolve_execution",
            "rule_guidance",
            "run_rules",
            "save_knowledge",
            "set_sender_status",
            "test_rule_match",
            "unsubscribe_sender",
            "update_account",
            "update_assistant_settings",
            "update_rule",
            "voice_profile",
        ),
        "owner": "email-assistant",
        "core": False,
        "scope_governed": True,
        "dynamic": False,
    },
    # WS-67 OA-8a (one_assistant_context.md §4.3.2): the CRM assistant's own
    # nine tools, for the one assistant to hold in a CRM turn. The four reads
    # and the four writes are the specialist's own tools. ``crm_search_notes``
    # is also in the injected ``crm`` family: the orchestrator does not hold
    # it in its ``tool_scope``, and a run that holds it skips the pack copy.
    "crm-records": {
        "label": "CRM records",
        "summary": (
            "Work the CRM records: find a lead, deal or contact, read the "
            "pipeline and a record, read its timeline, create a lead, move "
            "a deal, log an activity and convert a lead."
        ),
        "description": (
            "A tool pack: the CRM Assistant's own tools for daily record "
            "work, for the main assistant to load in a CRM turn. It holds "
            "the tools to search, read the pipeline, open a record and its "
            "timeline, search notes, create a lead, move a deal, log an "
            "activity and convert a lead. Each write asks the member first. "
            "A run loads a pack only where tool packs are on, so until then "
            "this switch has no effect."
        ),
        "tools": (
            "convert_lead",
            "create_lead",
            "crm_search_notes",
            "get_pipeline",
            "get_record",
            "get_timeline",
            "log_activity",
            "search_crm",
            "update_deal_status",
        ),
        "owner": "crm-assistant",
        "core": False,
        "scope_governed": True,
        "dynamic": False,
    },
    # WS-67 OA-8b (one_assistant_context.md §4.3.3): the Projects assistant's
    # 120 own tools in four packs. Each holds 35 tools or fewer. The core pack
    # holds 23, so that it fits beside the 31 email tools and the measured
    # base under the 110-tool request cap. The four together equal
    # ``skill_projects.__all__`` and the owner's ``own_tool_scope``.
    "projects": {
        "label": "Projects",
        "summary": (
            "The daily work on tasks: my work, find and read tasks and "
            "projects, the tree, a summary, the calendar, draw a board or a "
            "task table, create, update, assign, comment, complete, defer "
            "and move a task, subtasks, private tasks and the member's own "
            "triage."
        ),
        "description": (
            "A tool pack: the Projects Assistant's own tools for the daily "
            "work on tasks, for the main assistant to load in a Projects, "
            "Tasks or Calendar turn. It holds the tools to read the tree, a "
            "project and its tasks, find a task, show the member's own work "
            "and the calendar, draw a board, and to create, update, assign, "
            "comment on, complete, defer and move a task. Each write asks "
            "the member first. A run loads a pack only where tool packs are "
            "on, so until then this switch has no effect."
        ),
        "tools": (
            "add_subtasks",
            "assign",
            "calendar",
            "comment",
            "complete",
            "create_personal_task",
            "create_task",
            "defer",
            "find_tasks",
            "list_tasks",
            "move_task",
            "my_task",
            "my_work",
            "open_in_app",
            "people_for",
            "project_summary",
            "projects_tree",
            "render_board",
            "render_tasks",
            "set_my_overlay",
            "task_detail",
            "update_task",
            "vocabulary",
        ),
        "owner": "projects-assistant",
        "core": False,
        "scope_governed": True,
        "dynamic": False,
    },
    "projects-plan": {
        "label": "Projects planning and reports",
        "summary": (
            "Plan and report on projects: analytics, team capacity, who fits "
            "a task, conflicts, a task table, export, reports and their "
            "schedule, a timeline, a plan from a goal, task links, repeat "
            "rules, new projects and project settings."
        ),
        "description": (
            "A tool pack: the Projects Assistant's own tools to plan and to "
            "report, for the main assistant to load when a turn needs them. "
            "It holds the five analytics reads, the team's capacity, who "
            "fits a task, plan conflicts, the task table and its export, the "
            "saved reports with their recipients and schedule, the "
            "timeline, a plan from a goal, links between tasks, repeat "
            "rules, and a new project with its settings. A run loads a pack "
            "only where tool packs are on, so until then this switch has no "
            "effect."
        ),
        "tools": (
            "add_report_recipient",
            "add_task_ref",
            "analytics_finished",
            "analytics_load",
            "analytics_outlook",
            "analytics_stuck",
            "analytics_throughput",
            "create_project",
            "edit_project",
            "export_tasks",
            "find_conflicts",
            "fit_for_task",
            "link_tasks",
            "link_tasks_batch",
            "needs_attention",
            "propose_plan",
            "rebalance",
            "recurrence",
            "remove_report_recipient",
            "remove_task_ref",
            "render_report",
            "render_timeline",
            "report_delete",
            "report_list",
            "report_recipients",
            "report_render",
            "report_save",
            "set_recurrence",
            "set_report_schedule",
            "status_report",
            "task_dataset",
            "team_capacity",
            "unlink_tasks",
            "update_project",
            "waiting_agent_requests",
        ),
        "owner": "projects-assistant",
        "core": False,
        "scope_governed": True,
        "dynamic": False,
    },
    "projects-structure": {
        "label": "Projects structure",
        "summary": (
            "Shape a project: the statuses, types, fields and tags, who can "
            "see a project, saved views, watchers, the member's own areas, "
            "the intake queue and notifications."
        ),
        "description": (
            "A tool pack: the Projects Assistant's own tools to shape a "
            "project, for the main assistant to load when a turn needs "
            "them. It holds the tools for statuses, task types, fields and "
            "tags, who can see a project, saved views, watchers, the "
            "member's own areas, the intake queue and the notifications. "
            "A run loads a pack only where tool packs are on, so until "
            "then this switch has no effect."
        ),
        "tools": (
            "capture_intake",
            "create_area",
            "create_field",
            "create_status",
            "create_tag",
            "create_tags",
            "create_type",
            "create_types",
            "delete_area",
            "delete_field",
            "delete_status",
            "delete_tag",
            "delete_type",
            "delete_view",
            "grant_access",
            "intake_queue",
            "mark_notifications_read",
            "merge_tags",
            "my_areas",
            "my_contexts",
            "notifications",
            "project_access",
            "project_views",
            "rename_area",
            "revoke_access",
            "save_view",
            "set_status_set",
            "show_view",
            "triage_intake",
            "update_field",
            "update_status",
            "update_tag",
            "update_type",
            "watchers",
        ),
        "owner": "projects-assistant",
        "core": False,
        "scope_governed": True,
        "dynamic": False,
    },
    "projects-bulk": {
        "label": "Projects bulk and guarded acts",
        "summary": (
            "Many tasks at once, and the acts that are hard to undo: "
            "create, file, merge or bulk-edit tasks, archive, restore, "
            "move or delete a task or a project, comments and attachments, "
            "reminders, a nudge, a revert, watch, and the edit forms."
        ),
        "description": (
            "A tool pack: the Projects Assistant's own tools for bulk work "
            "and for guarded acts, for the main assistant to load when a "
            "turn needs them. It holds the tools to create, file, merge and "
            "bulk-edit tasks, to archive, restore, move and delete a task "
            "or a project, to edit or delete a comment or an attachment, "
            "the reminders, a nudge, a revert, a watch, and the edit forms. "
            "Each act asks the member first. A run loads a pack only where "
            "tool packs are on, so until then this switch has no effect."
        ),
        "tools": (
            "archive_project",
            "archive_task",
            "attach_to_task",
            "bulk_update",
            "convert_folder_to_project",
            "create_tasks",
            "delete_attachment",
            "delete_comment",
            "delete_project",
            "delete_task",
            "edit_comment",
            "edit_task",
            "file_tasks",
            "list_reminders",
            "merge_tasks",
            "move_project",
            "my_led_projects",
            "nudge_task",
            "promote_reminder",
            "reopen_reminder",
            "reschedule_reminder",
            "resolve_reminder",
            "revert_activity",
            "set_reminder",
            "unarchive_project",
            "unarchive_task",
            "update_reminder",
            "watch",
        ),
        "owner": "projects-assistant",
        "core": False,
        "scope_governed": True,
        "dynamic": False,
    },
    # STU-G1 (ai_studio.md section 6.2): the AI Studio builder tools, for the
    # one assistant to hold in a Studio turn. The owner is `studio-builder`,
    # which is NOT a second assistant. Eight stateless tools (G1b added the describe tool, G2 the interview and the plan card): no draft store
    # exists. A run loads a pack only where tool packs are on, and the pack
    # builds nothing for an org that STUDIO_ORGS does not hold.
    "studio": {
        "label": "AI Studio builder",
        "summary": (
            "Build an AI Studio app: draft a manifest from a brief, patch it, "
            "check it, outline it, list the actions it may use, read the live schema, ask the interview and show the plan card."
        ),
        "description": (
            "A tool pack: the builder tools of AI Studio, for the main "
            "assistant to load in a Studio turn. It holds the tools to draft "
            "an app manifest, patch it, validate it, outline it and list the "
            "actions an app may use. The tools keep no draft: the chat holds "
            "the manifest. A run loads a pack only where tool packs are on, "
            "so until then this switch has no effect."
        ),
        "tools": (
            "studio_describe",
            "studio_draft",
            "studio_interview",
            "studio_list_actions",
            "studio_outline",
            "studio_patch",
            "studio_plan",
            "studio_validate",
        ),
        "owner": "studio-builder",
        "core": False,
        "scope_governed": True,
        "dynamic": False,
    },
}


# ---------------------------------------------------------------------------
# The named default profile (WS-23 S3 — prepared, NOT active)
# ---------------------------------------------------------------------------
# The GENERAL skill set an agent without a ``config.json: tool_scope`` receives
# WHEN the owner-gated fail-closed flip is ON (``SKILLS_FAIL_CLOSED`` in
# ``orchestrator._tool_injection`` — ships OFF; flipping it is an OWNER
# decision, work_plan.md §6). Membership is evidence-based, from the WS-23
# scope-out (specs/skills_scope_out.md):
#
# * ``core``      — the guaranteed floor; automatic, listed for completeness.
# * ``memory``    — the one toggleable family that is genuinely general: 5 of
#   the 6 registered agents declare memory tools in their tool_scope, and the
#   orchestrator/email-assistant prompts instruct calling them; cross-session
#   personalisation is platform-wide behaviour, not a specialty.
# * ``workflows`` — injected scope-independently by design (spec §2 decision
#   note: org-governed automations offered to every agent); listed so the
#   profile documents the real default surface.
# * ``apps``      — dynamic, governed by app_grants (its own surface), never
#   by profiles; listed so "what does a default agent get" reads complete.
# * ``crm``       — the CRM read tools (WS-53 CRM-C7). Owner decision D2,
#   2026-10-13: "ensure the data is available for any agent". The gateway
#   (kill switch, ``feature:crm``, the access rule) is the only gate.
#
# Deliberately EXCLUDED (specialised — per-agent tool_scope/toggle only):
# ``history`` (only the orchestrator declares query_history) and ``coding``
# extras (only apis-config declares install_dependency; nobody declares the
# GitHub search pair). A tool pack (WS-67 OA-7a) never joins the profile: its
# tools are its owner's, and injection never offers them.

DEFAULT_PROFILE: tuple[str, ...] = ("core", "memory", "crm", "workflows", "apps")


def default_profile_tools() -> frozenset[str]:
    """Static tool names of the DEFAULT_PROFILE families.

    What the fail-closed resolve unions with the core floor for an unscoped
    agent. Dynamic families (``apps``) contribute nothing here — grants are
    appended at their own site; the ``workflows`` trio is likewise appended
    per-agent after scope filtering, so listing it here is documentation, not
    the mechanism that injects it.
    """
    return frozenset(
        name
        for slug in DEFAULT_PROFILE
        for name in SKILL_FAMILIES[slug]["tools"]
    )


# ---------------------------------------------------------------------------
# Pure lookups
# ---------------------------------------------------------------------------

def family_of(tool_name: str) -> str | None:
    """Family slug a statically-registered tool belongs to (or None)."""
    for slug, fam in SKILL_FAMILIES.items():
        if tool_name in fam["tools"]:
            return slug
    return None


def all_registered_tool_names() -> frozenset[str]:
    """Every statically-declared tool name across all families, packs too.

    The answer to "is this name in any family?". For the names that
    injection can offer, read :func:`injected_tool_names`.
    """
    return frozenset(
        name for fam in SKILL_FAMILIES.values() for name in fam["tools"]
    )


def is_pack(slug: str) -> bool:
    """True when family *slug* is a tool pack: it names an ``owner`` (OA-7a)."""
    fam = SKILL_FAMILIES.get(slug)
    return bool(fam and fam.get("owner"))


def pack_slugs() -> tuple[str, ...]:
    """The slug of each tool pack, in registry order."""
    return tuple(slug for slug in SKILL_FAMILIES if is_pack(slug))


def injected_families() -> dict[str, dict[str, Any]]:
    """The families that injection offers: every family that is not a pack.

    The ONE view for each consumer that speaks about injected tools: the
    scope resolve in ``orchestrator._tool_injection``, the skill index, the
    per-agent matrix and the catalog total. With no pack in the registry it
    equals ``SKILL_FAMILIES``, so each of them stays byte-identical.
    """
    return {
        slug: fam for slug, fam in SKILL_FAMILIES.items() if not is_pack(slug)
    }


def injected_tool_names() -> frozenset[str]:
    """Every static tool name that injection can offer (no pack tool)."""
    return frozenset(
        name for fam in injected_families().values() for name in fam["tools"]
    )


def registry_content_hash() -> str:
    """Deterministic registry-content hash (the token-cost cache key)."""
    canon = json.dumps(
        {slug: {**fam, "tools": list(fam["tools"])}
         for slug, fam in SKILL_FAMILIES.items()},
        sort_keys=True, ensure_ascii=True,
    )
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Token-cost measurement (measured, not estimated — spec §3)
# ---------------------------------------------------------------------------

def tool_json_schema(fn: Any) -> dict[str, Any]:
    """Derive the OpenAI-style function schema an LLM runtime would see.

    Both runtimes build their tool schema from the callable's signature +
    docstring, so rendering the same shape here prices what the model context
    actually pays per tool.

    A MAF ``FunctionTool`` already holds the schema that MAF sends, so it is
    priced by that schema. Its signature and docstring are the ones of the
    ``FunctionTool`` class, which the model never sees. The MCP tools of a run
    are such objects (WS-8c item 24, ``orchestrator._maf_mcp``).
    """
    spec = _maf_function_schema(fn)
    if spec is not None:
        return spec
    name = getattr(fn, "__name__", None) or getattr(fn, "name", str(fn))
    doc = inspect.getdoc(fn) or ""
    props: dict[str, Any] = {}
    required: list[str] = []
    hidden = _hidden_params(fn)
    try:
        sig = inspect.signature(fn)
        for pname, param in sig.parameters.items():
            if pname in ("self", "cls") or pname in hidden or param.kind in (
                inspect.Parameter.VAR_POSITIONAL,
                inspect.Parameter.VAR_KEYWORD,
            ):
                continue
            props[pname] = {"type": _schema_type(param.annotation)}
            if param.default is inspect.Parameter.empty:
                required.append(pname)
    except (TypeError, ValueError):
        pass
    return {
        "type": "function",
        "function": {
            "name": str(name),
            "description": doc,
            "parameters": {
                "type": "object",
                "properties": props,
                "required": required,
            },
        },
    }


def _maf_function_schema(fn: Any) -> dict[str, Any] | None:
    """The schema of a MAF ``FunctionTool``, as MAF sends it, or None."""
    try:
        from agent_framework import FunctionTool
    except ImportError:  # pragma: no cover — MAF is a dependency
        return None
    if not isinstance(fn, FunctionTool):
        return None
    try:
        spec = fn.to_json_schema_spec()
    except Exception:  # a pricing helper, never a failure
        return None
    return spec if isinstance(spec, dict) else None


def _hidden_params(fn: Any) -> frozenset[str]:
    """The parameters the runtime keeps OUT of the schema the model reads.

    A removed tool argument stays in the signature as ``SkipJsonSchema[...]``
    so the tool can refuse a stale call by name (H-196). Pydantic, and so the
    agent framework, drops it from the JSON schema. This price follows.
    """
    try:
        hints = typing.get_type_hints(fn, include_extras=True)
    except Exception:  # a pricing helper, never a failure
        return frozenset()
    return frozenset(
        name
        for name, hint in hints.items()
        if typing.get_origin(hint) is typing.Annotated
        and any(type(m).__name__ == "SkipJsonSchema" for m in typing.get_args(hint)[1:])
    )


def _schema_type(annotation: Any) -> str:
    txt = str(annotation)
    if annotation is inspect.Parameter.empty:
        return "string"
    if "int" in txt:
        return "integer"
    if "float" in txt:
        return "number"
    if "bool" in txt:
        return "boolean"
    if "list" in txt or "List" in txt:
        return "array"
    if "dict" in txt or "Dict" in txt:
        return "object"
    return "string"


# In-process cost cache: (family slug | "__total__", registry hash) → tokens.
# NOTE: the key deliberately does NOT include the injected dependencies — in a
# gateway process they are process-stable. Tests that swap fakes in must call
# :func:`clear_cost_cache` first.
_COST_CACHE: dict[tuple[str, str], int] = {}


def clear_cost_cache() -> None:
    """Reset the in-process token-cost cache (tests / registry hot-reload)."""
    _COST_CACHE.clear()


def _schema_tokens(
    names: tuple[str, ...] | frozenset[str],
    tools_by_name: Mapping[str, Any],
    count_tokens: Callable[[str], int],
) -> int:
    total = 0
    for n in sorted(names):
        fn = tools_by_name.get(n)
        if fn is not None:
            total += count_tokens(json.dumps(tool_json_schema(fn)))
    return total


def build_catalog(
    *,
    tools_by_name: Mapping[str, Any],
    addendum_for_scope: Callable[[frozenset[str] | None], str],
    count_tokens: Callable[[str], int],
    core_tool_names: frozenset[str],
) -> dict[str, Any]:
    """The full catalog with per-family MEASURED token cost.

    Cost per family = the marginal system-prompt addendum the family adds on
    top of the core floor (rendered with the real addendum builder) + the JSON
    schemas of its tools, counted with the injected tokenizer (the same one
    ``assemble_run_context`` uses). Cached per (family, registry-content hash)
    in-process.

    Dependencies are parameters so this package never imports the orchestrator
    (the gateway route composes the real ones; tests inject fakes).

    A tool pack (WS-67 OA-7a) is priced as a turn that loads it pays: its
    rules block (``acb_skills.tool_packs``) plus the schemas of its owner's
    tools. The caller puts the owner's tools in *tools_by_name*; injection
    never offers them, so a name that is absent prices nothing. A pack is not
    in ``total_injected_tokens``, because no run is injected with it.
    """
    rhash = registry_content_hash()
    core_floor = frozenset(core_tool_names)
    baseline_key = ("__core_addendum__", rhash)
    if baseline_key not in _COST_CACHE:
        _COST_CACHE[baseline_key] = count_tokens(
            addendum_for_scope(core_floor)
        )
    baseline = _COST_CACHE[baseline_key]

    families: list[dict[str, Any]] = []
    for slug, fam in SKILL_FAMILIES.items():
        key = (slug, rhash)
        if key not in _COST_CACHE:
            if fam.get("dynamic"):
                cost = 0
            elif is_pack(slug):
                # No addendum section names a pack tool, so the addendum
                # delta is not what a pack costs. Its rules block is.
                from acb_skills.tool_packs import rules_for

                cost = count_tokens(rules_for(slug)) + _schema_tokens(
                    fam["tools"], tools_by_name, count_tokens,
                )
            elif fam.get("core"):
                cost = baseline + _schema_tokens(
                    fam["tools"], tools_by_name, count_tokens,
                )
            else:
                with_fam = count_tokens(
                    addendum_for_scope(core_floor | frozenset(fam["tools"]))
                )
                cost = max(0, with_fam - baseline) + _schema_tokens(
                    fam["tools"], tools_by_name, count_tokens,
                )
            _COST_CACHE[key] = cost
        families.append({
            "slug": slug,
            "label": fam["label"],
            "description": fam["description"],
            "tools": list(fam["tools"]),
            "tool_count": len(fam["tools"]),
            "core": bool(fam.get("core")),
            "scope_governed": bool(fam.get("scope_governed")),
            "dynamic": bool(fam.get("dynamic")),
            **({"tool_pattern": fam["tool_pattern"]}
               if "tool_pattern" in fam else {}),
            # WS-67 OA-7a: the agent whose own tools a pack holds.
            **({"owner": fam["owner"]} if is_pack(slug) else {}),
            "token_cost": _COST_CACHE[key],
        })

    total_key = ("__total__", rhash)
    if total_key not in _COST_CACHE:
        # The WS-12 baseline: the full unscoped addendum + every static
        # schema that injection offers (no pack tool, WS-67 OA-7a).
        _COST_CACHE[total_key] = count_tokens(
            addendum_for_scope(None)
        ) + _schema_tokens(
            injected_tool_names(), tools_by_name, count_tokens,
        )
    return {
        "registry_hash": rhash,
        "families": families,
        "total_injected_tokens": _COST_CACHE[total_key],
    }


def families_for_scope(
    tool_scope: list[str] | None,
    *,
    resolved_scope: frozenset[str] | None,
) -> list[str]:
    """Which family slugs an agent's resolved injected scope intersects.

    ``resolved_scope`` is what ``_resolve_injected_scope`` returned (already
    core-floor-unioned); ``None`` means the agent declares no ``tool_scope``
    and — injection being fail-open today (spec rule 3) — receives every
    family. Families that are not scope-governed (core floor, workflows,
    apps) are always present.

    A tool pack is never in the list (WS-67 OA-7a): no agent is injected
    with one. Its owner holds the tools as own tools, which this matrix of
    injected families does not show.
    """
    del tool_scope  # reserved for S2 (declared-vs-effective display)
    out: list[str] = []
    for slug, fam in injected_families().items():
        if not fam.get("scope_governed") or resolved_scope is None or resolved_scope & frozenset(fam["tools"]):
            out.append(slug)
    return out
