"""MT-1228: how a member asks for each pack tool, and the pack that it loads.

The reminder bug was one case of a class. A tool sat in a pack whose words did
not match the plain way a member asks for it, so the model never held the tool
and used a worse one. This table holds, for EVERY tool of EVERY pack, one or
more plain requests. The test runs the real pack choice (:func:`tp.choose`, the
function that the executor calls) on each request in a chat with no app
surface, and asserts that a chosen pack holds the tool.

The ratchet: a new pack tool has no row, so
:func:`test_every_pack_tool_has_a_request` fails until someone writes how a
member asks for it. The controls keep the packs of ordinary turns.

Run: ``uv run pytest tests/unit/test_pack_requests.py -q``.
"""
from __future__ import annotations

import pytest

from acb_skills import skill_families as sf
from acb_skills import tool_packs as tp
from tests.unit.test_tool_packs_choice import EVERY, MEMBER, ORG, SESSION

#: The base of the real run, in tools (the staging shape of OA-8e).
BASE = 56

REQUESTS: dict[str, tuple[str, ...]] = {
    # -- email --
    "bulk_manage_inbox": ("archive all the newsletters in my inbox",),
    "cancel_scheduled_send": ("cancel the scheduled email to Ravi",),
    "compose_reply": ("write a reply to Ravi's email",),
    "create_label": ("create an email label called Invoices",),
    "digest": ("give me my email digest",),
    "draft_reply": ("draft a reply to the email from Ravi",),
    "edit_draft": ("edit my email draft to Ravi",),
    "find_contact": ("find Ravi's email address",),
    "find_follow_ups": ("which emails am I waiting on a reply for?",),
    "find_priority": ("what are my priority emails?",),
    "forward_email": ("forward that email to Anita",),
    "get_account_overview": ("show an overview of my mailbox",),
    "list_accounts": ("which mailboxes are connected?",),
    "list_artifacts": ("list the files from my emails",),
    "list_labels": ("list my email labels",),
    "list_scheduled_sends": ("show my scheduled emails",),
    "manage_inbox": ("archive this email",),
    "mark_thread_done": ("mark this email thread as done",),
    "narrow_and_read": ("read the latest email from Ravi",),
    "present_email_groups": ("group my emails by sender",),
    "query_inbox": ("search my inbox for the invoice",),
    "query_insights": ("how many emails did I get this week?",),
    "read_email": ("open the email from Ravi",),
    "read_email_attachment": ("read the attachment in that email",),
    "read_thread": ("read the whole email thread",),
    "reclassify_reply_zero": ("re-check which emails need a reply",),
    "schedule_send": ("send this email tomorrow at 9",),
    "send_draft": ("send the email draft",),
    "send_email": ("send an email to Ravi about the quote",),
    "snooze_email": ("snooze this email until Monday",),
    "sync_account": ("sync my mailbox",),
    # -- email-setup --
    "apply_settings_to_mailboxes": ("apply these email rules to all my mailboxes",),
    "auto_categorize_inbox": ("auto-categorize my inbox",),
    "categorize_senders": ("categorize senders in my inbox",),
    "copy_rules": ("copy my email rules to the other mailbox",),
    "create_rule": ("create an email rule to label invoices",),
    "create_rules_from_prompt": ("set up email rules from this description",),
    "delete_knowledge": ("delete the email knowledge entry about pricing",),
    "delete_rule": ("delete my email rule for invoices",),
    "forget_pattern": ("forget the learned email rule pattern",),
    "generate_writing_style": ("learn my writing style from my sent emails",),
    "get_rules_and_settings": ("show my email rules",),
    "install_default_rules": ("install the default email rules",),
    "learn_rule_pattern": ("learn an email rule from this",),
    "list_knowledge": ("list the email knowledge you saved",),
    "list_patterns": ("list the learned patterns for my email rules",),
    "list_rule_history": ("show the email rule history",),
    "list_senders": ("list the senders in my inbox",),
    "resolve_execution": ("approve the pending email rule action",),
    "rule_guidance": ("how should I write an email rule for newsletters?",),
    "run_rules": ("run my email rules on the inbox",),
    "save_knowledge": ("save to the email knowledge: our refund window is 30 days",),
    "set_sender_status": ("mark this sender as a newsletter in my inbox",),
    "test_rule_match": ("test if this email rule matches the email",),
    "unsubscribe_sender": ("unsubscribe me from this sender",),
    "update_account": ("rename my mailbox account to Sales mail",),
    "update_assistant_settings": ("turn on auto-drafting of replies",),
    "update_rule": ("change my email rule for invoices",),
    "voice_profile": ("show my writing style",),
    # -- crm-records --
    "convert_lead": ("convert the lead Acme to a deal",),
    "create_lead": ("create a lead for Acme",),
    "crm_search_notes": ("search my crm notes for pricing",),
    "get_pipeline": ("show the sales pipeline",),
    "get_record": ("show the Acme record in the crm",),
    "get_timeline": ("show the timeline of the Acme deals",),
    "log_activity": ("log a call with Acme",),
    "search_crm": ("search the crm for Acme",),
    "update_deal_status": ("mark the Acme deal as won",),
    # -- projects (the core pack) --
    "add_subtasks": ("add subtasks to the website task",),
    "assign": ("assign the task to Ravi",),
    "calendar": ("what is on my agenda today?",),
    "comment": ("comment on the task that I will finish tomorrow",),
    "complete": ("mark the task as done",),
    "create_personal_task": ("create a private task to buy filters",),
    "create_task": ("create a task in Website Relaunch",),
    "defer": ("defer this task to next week",),
    "find_tasks": ("find the task about the extruder",),
    "list_tasks": ("list the tasks in Website Relaunch",),
    "move_task": ("move the task to the Done column",),
    "my_task": ("show my task about the quote",),
    "my_work": ("what should I work on today?",),
    "open_in_app": ("open the task in the app",),
    "people_for": ("who could take this task?",),
    "project_summary": ("give me a project summary of Website Relaunch",),
    "projects_tree": ("show me all my projects",),
    "render_board": ("show the board for Website Relaunch",),
    "render_tasks": ("show the tasks as a table",),
    "set_my_overlay": ("block two hours on my calendar for the task",),
    "task_detail": ("show me the details of the task",),
    "update_task": ("change the due date of the task",),
    "vocabulary": ("which statuses can I give a task?",),
    # -- projects-plan --
    "add_report_recipient": ("add Ravi to the weekly status report",),
    "add_task_ref": ("add a reference link to the task",),
    "analytics_finished": ("how many tasks did we finish last month?",),
    "analytics_load": ("what is the workload per person?",),
    "analytics_outlook": ("what is the outlook for the sprint?",),
    "analytics_stuck": ("which tasks are stuck?",),
    "analytics_throughput": ("what is our task throughput?",),
    "create_project": ("create a new project called Website Relaunch",),
    "edit_project": ("edit the project settings",),
    "export_tasks": ("export the tasks to CSV",),
    "find_conflicts": ("are there scheduling conflicts in the project plan?",),
    "fit_for_task": ("who is the best fit for this task?",),
    "link_tasks": ("make this task block the other task",),
    "link_tasks_batch": ("link these tasks in order as dependencies",),
    "needs_attention": ("which tasks need attention?",),
    "propose_plan": ("propose a project plan for launching the site",),
    "rebalance": ("rebalance the workload",),
    "recurrence": ("when does the recurring task repeat?",),
    "remove_report_recipient": ("remove Ravi from the status report",),
    "remove_task_ref": ("remove the reference from the task",),
    "render_report": ("show the status report",),
    "render_timeline": ("show a gantt of the project",),
    "report_delete": ("delete the status report",),
    "report_list": ("list my status reports",),
    "report_recipients": ("who gets the status report?",),
    "report_render": ("render the weekly status report",),
    "report_save": ("save this as a status report",),
    "set_recurrence": ("make this a recurring task",),
    "set_report_schedule": ("send the status report every Friday",),
    "status_report": ("give me a status report for the project",),
    "task_dataset": ("pull a dataset of all the tasks",),
    "team_capacity": ("what is the team capacity next week?",),
    "unlink_tasks": ("unlink these two tasks",),
    "update_project": ("change the project deadline",),
    "waiting_agent_requests": ("which agent requests are waiting for me?",),
    # -- projects-structure --
    "capture_intake": ("capture this into the intake queue",),
    "create_area": ("create an area called Health",),
    "create_field": ("add a custom field Budget",),
    "create_status": ("add a task status Blocked",),
    "create_tag": ("create a task tag urgent",),
    "create_tags": ("create task tags urgent and later",),
    "create_type": ("add a task type Bug",),
    "create_types": ("create task types Bug and Chore",),
    "delete_area": ("delete the area Health",),
    "delete_field": ("delete the custom field Budget",),
    "delete_status": ("delete the task status Blocked",),
    "delete_tag": ("delete the task tag urgent",),
    "delete_type": ("delete the task type Bug",),
    "delete_view": ("delete my saved view Overdue",),
    "grant_access": ("give Ravi project access",),
    "intake_queue": ("show my intake queue",),
    "mark_notifications_read": ("mark my notifications as read",),
    "merge_tags": ("merge the task tags urgent and asap",),
    "my_areas": ("list my areas",),
    "my_contexts": ("list my contexts",),
    "notifications": ("what are my notifications?",),
    "project_access": ("who has project access?",),
    "project_views": ("show my saved views",),
    "rename_area": ("rename the area Health to Fitness",),
    "revoke_access": ("revoke Ravi's project access",),
    "save_view": ("save this as a saved view",),
    "set_status_set": ("use the status set Kanban for the project",),
    "show_view": ("open my saved view Overdue",),
    "triage_intake": ("triage the intake queue",),
    "update_field": ("rename the custom field Budget",),
    "update_status": ("rename the task status Blocked",),
    "update_tag": ("rename the task tag urgent",),
    "update_type": ("rename the task type Bug",),
    "watchers": ("who watches this task?",),
    # -- projects-bulk --
    "archive_project": ("archive the project Website Relaunch",),
    "archive_task": ("archive this task",),
    "attach_to_task": ("attach this file to the task",),
    "bulk_update": ("bulk update the due date of these tasks",),
    "convert_folder_to_project": ("convert the folder Sales to a project",),
    "create_tasks": ("create tasks for each of these: brief, copy",),
    "delete_attachment": ("delete the attachment from the task",),
    "delete_comment": ("delete my comment on the task",),
    "delete_project": ("delete the project Old Site",),
    "delete_task": ("delete the task about the extruder",),
    "edit_comment": ("edit my comment on the task",),
    "edit_task": ("open the edit form for the task",),
    "file_tasks": ("file these tasks under Website Relaunch",),
    "merge_tasks": ("merge these two tasks",),
    "move_project": ("move the project under Sales",),
    "my_led_projects": ("which projects do I lead?",),
    "nudge_task": ("nudge the assignee of the quote task",),
    "revert_activity": ("undo that change",),
    "unarchive_project": ("unarchive the project Website Relaunch",),
    "unarchive_task": ("restore the archived task",),
    "watch": ("watch this task",),
    # -- studio (STU-G1) --
    "studio_draft": ("draft an app manifest in AI Studio",),
    "studio_interview": ("interview me about the studio app I want to build",),
    "studio_list_actions": ("which actions can my studio app use?",),
    "studio_outline": ("outline my studio app",),
    "studio_patch": ("change the title in the app manifest",),
    "studio_plan": ("plan the studio app",),
    "studio_validate": ("validate the app manifest",),
    # -- projects-reminders --
    "list_reminders": ("what reminders do I have?",),
    "promote_reminder": ("turn the reminder into a task",),
    "reopen_reminder": ("reopen the reminder about the quote",),
    "reschedule_reminder": ("move the reminder to tomorrow",),
    "resolve_reminder": ("mark the reminder as done",),
    "set_reminder": ("remind me to call Ravi at 5pm",),
    "update_reminder": ("change the reminder text",),
}

#: Known gaps, kept out of the table on purpose. A bare "calendar" or "project"
#: stays out of every word list (OA-8b audit: a Google, Notion or CRM turn uses
#: them, and ``test_the_bare_words_load_no_projects_pack`` holds that). So these
#: asks load no Projects pack in a chat with no app surface. The Calendar and
#: Projects apps load the core pack by surface, and the offered-pack lines in the
#: rules of the loader show the member's model where the tools are.
KNOWN_GAPS: dict[str, str] = {
    "show my calendar": "bare calendar is out (Google or Notion calendar)",
    "summarise the Website Relaunch project": "a name sits between 'the' and 'project'",
}

#: Ordinary turns and the packs that they load. A change of a word list must
#: not move any of them.
CONTROLS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("create a task to call Ravi", ("projects",)),
    ("show my overdue tasks", ("projects",)),
    ("summarise my inbox", ("email",)),
    ("what is the weather today?", ()),
    ("write a poem about autumn", ()),
    ("show the sales pipeline", ("crm-records",)),
    # MT-1228 repair: a word that was too broad once loaded a pack for these.
    ("nudge the prospect about the proposal", ()),
    ("nudge the lead about the deal", ()),
    ("undo that email I just sent", ("email",)),
    ("undo that send", ()),
    ("revert that draft", ()),
    ("revert that deal update", ()),
    ("what senders email me most about the crm deals", ("email", "crm-records")),
)


def _all_tools() -> list[tuple[str, str]]:
    return [(slug, name) for slug in sf.pack_slugs() for name in sf.SKILL_FAMILIES[slug]["tools"]]


def _chosen(words: str) -> tuple[str, ...]:
    turn = tp.PackTurn(ORG, SESSION, tp._member_part(MEMBER), 1, (), words, True, surface="")
    return tp.choose(turn, allowed=EVERY, base_tools=BASE).slugs


def holders(tool: str) -> set[str]:
    return {slug for slug in sf.pack_slugs() if tool in sf.SKILL_FAMILIES[slug]["tools"]}


def test_every_pack_tool_has_a_request() -> None:
    """The ratchet. A new pack tool fails here until a row says how a member
    asks for it. Mutation: add a tool to a pack family with no row -> red."""
    missing = [name for _slug, name in _all_tools() if not REQUESTS.get(name)]
    assert not missing, f"no plain request for: {sorted(missing)}"
    stale = sorted(set(REQUESTS) - {name for _s, name in _all_tools()})
    assert not stale, f"rows for tools that no pack holds: {stale}"


@pytest.mark.parametrize(("tool", "words"), [
    (tool, words) for tool, reqs in REQUESTS.items() for words in reqs])
def test_a_plain_request_loads_a_pack_that_holds_the_tool(tool: str, words: str) -> None:
    """The class fence. Mutation: remove a word from a pack, or move a tool to
    a pack that its request does not load -> that row is red."""
    slugs = _chosen(words)
    assert set(slugs) & holders(tool), (tool, words, slugs)


def test_the_known_gaps_stay_listed_with_a_reason() -> None:
    """A gap that a word fixes must leave this list. Mutation: add the bare
    word -> the gap loads a pack -> red, and the list must shrink."""
    for words, reason in KNOWN_GAPS.items():
        assert reason and not [s for s in _chosen(words) if s.startswith("projects")], words


@pytest.mark.parametrize(("words", "slugs"), CONTROLS)
def test_an_ordinary_turn_keeps_its_packs(words: str, slugs: tuple[str, ...]) -> None:
    """Mutation: add a bare common word to a pack -> a control gains a pack."""
    assert _chosen(words) == slugs
