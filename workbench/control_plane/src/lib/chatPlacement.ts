/**
 * chatPlacement — where each element of an assistant turn goes.
 *
 * Spec: `project-docs/specs/projects_ai_chat.md` §24, the placement rule of
 * record (owner, 2026-10-08). The owner's report: an option picker they had
 * to answer "got lost above in the chat", under four read receipts that the
 * turn drew after the answer. One of the four was a pipe-delimited dump.
 *
 * Every element of a turn is one of four kinds:
 *
 * 1. `ask` — NEEDS THE MEMBER: a confirmation card, an option picker, a
 *    form, `ask_questions`. It stays in the flow, in order. While it waits,
 *    the pin above the composer (`lib/askPin.ts`) keeps it in view.
 * 2. `evidence` — the result of a READ. It draws INSIDE the working trail
 *    (`ThinkingContainer`), under the step that made it, closed by default.
 *    It never draws as a card after the answer.
 * 3. `write` — the receipt of a WRITE: done, part done, unknown, refused. It
 *    draws compact, in the flow, after the answer text.
 * 4. `answer` — a card the model chose to draw: a plan, a board, a report, a
 *    table. At most one per answer, after the text. The agent instructions
 *    and `evals/projects_ops` hold the "one", not this file.
 *
 * **This map is the ONE place a tool name gets a placement.** The card files
 * (`ProjectToolCards`, `TaskToolCards`, `EmailToolCards`) and the trail read
 * it, and none of them guesses from a name. The client cannot see the tools'
 * annotations, so the map is written out. Two fences hold it:
 *
 * - `src/lib/chatPlacement.test.ts` fails on a tool name that a card file
 *   draws and this map does not name;
 * - `tests/unit/test_chat_placement_classes.py` fails on a `skill-projects`
 *   tool this map does not name, and on a tool whose placement disagrees
 *   with its class in `skill_projects/manifest.py` (A is a read, B and C are
 *   writes, H-236).
 *
 * A tool that no card file draws (a script run, `manage_todo_list`) has no
 * receipt to place. It is a step row in the trail, as before. The CRM reads
 * draw through `components/crm/CrmEvidence.tsx` since the follow-up of #716
 * and #735, and `test_chat_placement_classes.py` holds them to the CRM
 * agent's own annotations.
 *
 * Pure and framework-free, so the node-env vitest holds it.
 */

import { bareToolName } from "@/lib/toolSteps";

export type Placement = "ask" | "evidence" | "write" | "answer";

/** Tools whose result the member must answer. The chat draws them itself
 *  (`AgentChat`'s confirmation queue and question cards). */
const ASK: readonly string[] = ["request_confirmation", "ask_questions", "ask_user"];

/**
 * Tools that draw a card the model chose. `emit_generative_ui` is here, and
 * its SPEC decides between `ask` and `answer` (see {@link genUiPlacement}).
 */
const ANSWER: readonly string[] = [
  "emit_generative_ui",
  // Projects views: each draws a template. Its text result is for the model.
  "render_timeline",
  "render_board",
  "render_tasks",
  "render_report",
  "status_report",
  // Email: the categorized board the agent builds from its reads.
  "present_email_groups",
  // Tasks: a day plan, with an Apply on a proposal.
  "my_tasks_plan_day",
  "my_tasks_replan_day",
  "my_tasks_rollover",
];

/** Reads. Projects names are manifest class A (held by the Python fence). */
const EVIDENCE: readonly string[] = [
  // ── Projects (skill-projects, class A) ─────────────────────────────────
  "projects_tree",
  "project_summary",
  "list_tasks",
  "find_tasks",
  "task_detail",
  "task_dataset",
  "my_work",
  "my_task",
  "my_contexts",
  "my_led_projects",
  "my_areas",
  "people_for",
  "vocabulary",
  "calendar",
  "intake_queue",
  "notifications",
  "watchers",
  "project_access",
  "project_views",
  "recurrence",
  "team_capacity",
  "fit_for_task",
  "rebalance",
  "find_conflicts",
  "analytics_stuck",
  "analytics_load",
  "analytics_throughput",
  "analytics_finished",
  "analytics_outlook",
  "report_list",
  "report_render",
  "open_in_app",
  // ── Tasks (skill-my-tasks) ─────────────────────────────────────────────
  "my_tasks_list",
  "my_tasks_list_schedule",
  "my_tasks_inbox_insights",
  "my_tasks_day_digest",
  "my_tasks_estimate_stats",
  "my_tasks_accounts",
  "my_tasks_people",
  "my_tasks_list_projects",
  "my_tasks_subtasks",
  "my_tasks_detail",
  "my_tasks_clarify",
  // ── Email (agent-email-assistant) ──────────────────────────────────────
  "query_inbox",
  "find_priority",
  "get_important_emails",
  "search_emails",
  "find_urgent",
  "find_needs_reply",
  "read_email",
  "read_thread",
  "list_accounts",
  "get_unread_count",
  "list_labels",
  "list_artifacts",
  "get_sender_categories",
  "list_senders",
  "get_rules_and_settings",
  "list_rule_history",
  "list_knowledge",
  "list_cold_senders",
  "suggest_unsubscribes",
  "get_account_overview",
  "get_digest",
  "test_rule_match",
  "list_learned_patterns",
  "list_rule_patterns",
  "list_patterns",
  // ── CRM (agent-crm, `read_only=True`) ──────────────────────────────────
  "search_crm",
  "get_pipeline",
  "get_record",
  "get_timeline",
];

/** Writes. Projects names are manifest class B or C. */
const WRITE: readonly string[] = [
  // ── Projects (skill-projects, class B) ─────────────────────────────────
  "create_task",
  "create_tasks",
  "update_task",
  "assign",
  "comment",
  "add_subtasks",
  "link_tasks",
  "unlink_tasks",
  "move_task",
  "watch",
  "complete",
  "defer",
  "unarchive_task",
  "create_project",
  "update_project",
  "report_save",
  "create_status",
  "update_status",
  "create_type",
  "update_type",
  "create_field",
  "update_field",
  "create_tag",
  "update_tag",
  "edit_comment",
  "set_recurrence",
  "create_personal_task",
  "set_my_overlay",
  "edit_task",
  "edit_project",
  "propose_plan",
  "save_view",
  "capture_intake",
  "triage_intake",
  "mark_notifications_read",
  // ── Projects (class C, the guarded acts) ───────────────────────────────
  "archive_project",
  "unarchive_project",
  "move_project",
  "archive_task",
  "merge_tasks",
  "bulk_update",
  "delete_comment",
  "revert_activity",
  "delete_status",
  "set_status_set",
  "delete_type",
  "delete_field",
  "delete_tag",
  "merge_tags",
  "delete_view",
  "report_delete",
  "delete_attachment",
  // ── Tasks ──────────────────────────────────────────────────────────────
  "my_tasks_capture",
  "my_tasks_capture_many",
  "my_tasks_organize",
  "my_tasks_update",
  "my_tasks_complete",
  "my_tasks_move",
  "my_tasks_set_stage",
  "my_tasks_delegate",
  "my_tasks_add_subtasks",
  "my_tasks_archive",
  "my_tasks_schedule",
  "my_tasks_unschedule",
  "my_tasks_set_one_thing",
  "my_tasks_sync",
  "my_tasks_plan_project",
  // ── Email ──────────────────────────────────────────────────────────────
  // `digest` can send the digest, and `generate_writing_style` saves the
  // style it derives: both receipts belong in the flow (review round 1).
  "digest",
  "generate_writing_style",
  "draft_reply",
  "draft_email",
  "create_rule",
  "update_rule",
  "update_assistant_settings",
  "create_rules_from_prompt",
  "manage_inbox",
  "apply_labels",
  "move_to_folder",
  "create_label",
  "send_email",
  "send_reply",
  "send_draft",
  "delete_rule",
  "reset_rules",
  "run_rules_now",
  "run_rules",
  "update_rule_state",
  "resolve_execution",
  "learn_rule_pattern",
  "install_default_rules",
  "process_past_emails",
  "approve_execution",
  "reject_execution",
  "undo_execution",
  "import_artifact",
  "add_knowledge",
  "update_knowledge",
  "save_knowledge",
  "delete_knowledge",
  "delete_learned_pattern",
  "delete_rule_pattern",
  "forget_pattern",
  "find_follow_ups",
  "mark_thread_done",
  "reclassify_reply_zero",
  "unsubscribe_sender",
  "keep_newsletter",
  "set_cold_sender",
  "set_sender_status",
  "categorize_senders",
  "send_digest",
  "sync_account",
  "resync_account",
  // ── CRM (agent-crm): each asks the member first ────────────────────────
  "create_lead",
  "update_deal_status",
  "log_activity",
  "convert_lead",
];

function build(): Readonly<Record<string, Placement>> {
  const out: Record<string, Placement> = {};
  const add = (names: readonly string[], p: Placement) => {
    for (const n of names) {
      // A name in two lists is a defect, and the test says which.
      if (n in out) throw new Error(`chatPlacement: ${n} is both ${out[n]} and ${p}`);
      out[n] = p;
    }
  };
  add(ASK, "ask");
  add(ANSWER, "answer");
  add(EVIDENCE, "evidence");
  add(WRITE, "write");
  return Object.freeze(out);
}

/** Every classified tool, by bare name. Exported for the fences. */
export const PLACEMENT: Readonly<Record<string, Placement>> = build();

/**
 * Where a tool's receipt goes, or `undefined` for a tool the map does not
 * name. The caller decides what an unnamed tool does, and the two fences
 * keep every tool a card file draws named.
 */
export function placementOf(name: string): Placement | undefined {
  const bare = bareToolName(name);
  return Object.hasOwn(PLACEMENT, bare) ? PLACEMENT[bare] : undefined;
}

/** True for a read: its receipt draws in the trail, under its step. */
export function isEvidenceTool(name: string): boolean {
  return placementOf(name) === "evidence";
}

// ── Generative UI: an ask or an answer ─────────────────────────────────────

/** The templates the member fills in and submits. */
export const ASK_TEMPLATES: ReadonlySet<string> = new Set(["formCard", "optionPicker", "planCard"]);

const MAX_DEPTH = 20;

function hasControl(node: unknown, depth: number): boolean {
  if (depth > MAX_DEPTH || !node || typeof node !== "object") return false;
  const n = node as Record<string, unknown>;
  const props = (n.props && typeof n.props === "object" ? n.props : {}) as Record<string, unknown>;
  if (n.type === "button" && typeof props.action === "string" && props.action) return true;
  if (n.type === "template" && typeof props.name === "string" && ASK_TEMPLATES.has(props.name)) {
    return true;
  }
  const kids = Array.isArray(n.children) ? n.children : [];
  return kids.some((k) => hasControl(k, depth + 1));
}

/**
 * A `generative_ui` spec is an `ask` when the member must act on it: it
 * blocks the run (`request_id`, from `"hitl": true`), or it holds a form, a
 * picker, a plan to submit, or a button. Every other spec is an `answer`.
 */
export function genUiPlacement(spec: unknown): "ask" | "answer" {
  if (!spec || typeof spec !== "object") return "answer";
  const s = spec as Record<string, unknown>;
  if (typeof s.request_id === "string" && s.request_id) return "ask";
  const root = s.root ?? s.view ?? s;
  return hasControl(root, 0) ? "ask" : "answer";
}

/** The words a waiting spec is known by: its title, else a plain name. */
export function genUiTitle(spec: unknown): string {
  if (!spec || typeof spec !== "object") return "";
  const s = spec as Record<string, unknown>;
  if (typeof s.title === "string" && s.title.trim()) return s.title.trim();
  const root = (s.root ?? s.view ?? s) as Record<string, unknown>;
  const props = (root?.props && typeof root.props === "object" ? root.props : {}) as Record<string, unknown>;
  const data = (props.data && typeof props.data === "object" ? props.data : {}) as Record<string, unknown>;
  for (const v of [data.title, props.title, props.text]) {
    if (typeof v === "string" && v.trim()) return v.trim();
  }
  return "";
}
