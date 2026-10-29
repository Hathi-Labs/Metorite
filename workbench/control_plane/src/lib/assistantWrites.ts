/**
 * ── THE ONE "THE ASSISTANT WROTE" SIGNAL ───────────────────────────────────
 *
 * Task #124. A create, update or delete that the assistant makes through a
 * tool must show in the open app at once. Before this module only Projects
 * did, and only through a receipt card: `announceChange` in
 * `ProjectToolCards` raised `PROJECTS_CHANGED_EVENT` when a fresh card drew in
 * the chat. CRM, Email, Calendar, Knowledge and Artifacts had no listener, so
 * a member read a stale list until a manual reload.
 *
 * THE DESIGN, and it adds no second path:
 *
 *   1. The chat stream (`useAgentChat`) calls `noteToolStart` and
 *      `noteToolEnd` on its LIVE loop. A replay never calls them.
 *   2. A tool that ended with success and is a write maps to the apps it
 *      changes (`appsForWrite`). The map is here, in one table.
 *   3. `announceAssistantWrite` raises `ASSISTANT_WROTE_EVENT` with those apps.
 *   4. `wireLiveRefresh` (`lib/liveRefresh.ts`) listens for it, next to the
 *      Projects event it already listened for. An app that calls
 *      `useLiveRefresh(run, app)` re-reads at once.
 *
 * A tool that no table row names reads as a read, so it signals nothing. The
 * fence `assistantWrites.test.ts` holds every write tool of `chatPlacement`
 * on a row, and every live app that has write tools on a `useLiveRefresh`.
 *
 * Pure and DOM-guarded, so the node-env vitest holds it.
 */

import { placementOf } from "@/lib/chatPlacement";
import { bareToolName } from "@/lib/toolSteps";

/** The window event name. */
export const ASSISTANT_WROTE_EVENT = "cc-assistant-wrote";

/** The live apps (`nav.ts`, launch `live`) that can show an assistant write. */
export type LiveApp =
  | "projects"
  | "tasks"
  | "calendar"
  | "email"
  | "crm"
  | "knowledge"
  | "artifacts";

/**
 * Live apps that no assistant tool writes to, with the reason. The fence
 * reads this so a new write tool for one of them has to leave the list.
 */
export const APPS_WITHOUT_WRITE_TOOLS: Readonly<Record<string, string>> = {
  people: "no assistant tool writes a person. `people_for` is a read, and the profile tools write the settings page.",
};

/** Email write tools (`chatPlacement.ts`, the Email block of WRITE). */
const EMAIL_WRITES: ReadonlySet<string> = new Set([
  "digest", "generate_writing_style", "draft_reply", "draft_email", "create_rule", "update_rule",
  "update_assistant_settings", "create_rules_from_prompt", "manage_inbox", "apply_labels",
  "move_to_folder", "create_label", "send_email", "compose_reply", "forward_email", "send_reply",
  "send_draft", "delete_rule", "reset_rules", "run_rules_now", "run_rules", "update_rule_state",
  "resolve_execution", "learn_rule_pattern", "install_default_rules", "process_past_emails",
  "approve_execution", "reject_execution", "undo_execution", "import_artifact", "add_knowledge",
  "update_knowledge", "save_knowledge", "delete_knowledge", "delete_learned_pattern",
  "delete_rule_pattern", "forget_pattern", "find_follow_ups", "mark_thread_done",
  "reclassify_reply_zero", "unsubscribe_sender", "keep_newsletter", "set_cold_sender",
  "set_sender_status", "categorize_senders", "send_digest", "sync_account", "resync_account",
]);

/** CRM write tools (`agent-crm`). Each asks the member first. */
const CRM_WRITES: ReadonlySet<string> = new Set([
  "create_lead", "update_deal_status", "log_activity", "convert_lead",
]);

/** Knowledge write tools: a proposal lands in the review queue. */
const KNOWLEDGE_WRITES: ReadonlySet<string> = new Set(["propose_page", "propose_edit"]);

/** Tools that make a file in the chat's workspace, which Artifacts lists. */
const ARTIFACT_WRITES: ReadonlySet<string> = new Set([
  "write_artifact", "make_document", "make_report", "package_artifacts",
]);

/** The assistant profile tools write the settings page, which no live app shows. */
const PROFILE_WRITES = /^profile_/;

/**
 * Projects, My Tasks and Calendar are ONE task store (D52, D53, D54). A write
 * to it changes all three, so each of them re-reads.
 */
const TASK_STORE: readonly LiveApp[] = ["projects", "tasks", "calendar"];

/** The apps one tool call changes, or an empty list for a read. */
export function appsForWrite(toolName: string): LiveApp[] {
  const bare = bareToolName(toolName);
  if (!bare) return [];
  if (EMAIL_WRITES.has(bare)) return ["email"];
  if (CRM_WRITES.has(bare)) return ["crm"];
  if (KNOWLEDGE_WRITES.has(bare)) return ["knowledge"];
  if (ARTIFACT_WRITES.has(bare)) return ["artifacts"];
  if (PROFILE_WRITES.test(bare)) return [];
  if (placementOf(bare) === "write") return [...TASK_STORE];
  return [];
}

/** Every app that some tool writes to. The fence walks this. */
export const APPS_WITH_WRITE_TOOLS: readonly LiveApp[] = [
  "projects", "tasks", "calendar", "email", "crm", "knowledge", "artifacts",
];

// ── The announcement ────────────────────────────────────────────────────────

/** Raise the event for one finished write. Returns the apps it named. */
export function announceAssistantWrite(toolName: string): LiveApp[] {
  const apps = appsForWrite(toolName);
  if (apps.length === 0) return apps;
  try {
    window.dispatchEvent(new CustomEvent(ASSISTANT_WROTE_EVENT, { detail: { apps, tool: bareToolName(toolName) } }));
  } catch {
    /* a non-browser render */
  }
  return apps;
}

/** Names of the tools that started, by call id. A sub-agent end has no name. */
const started = new Map<string, string>();
const MAX_TRACKED = 500;

/** Remember what a call is, so its end can be classified. Live loop only. */
export function noteToolStart(id: string, name: string): void {
  if (!id || !name) return;
  if (started.size >= MAX_TRACKED) started.clear();
  started.set(id, name);
}

/**
 * A call ended. Announce it when it succeeded and is a write.
 * `name` is optional because `tool_end` and `sub_agent_tool_end` may omit it.
 */
export function noteToolEnd(id: string, name: string | undefined, success: boolean): LiveApp[] {
  const known = name || started.get(id) || "";
  started.delete(id);
  if (!success || !known) return [];
  return announceAssistantWrite(known);
}

/** Does this event detail name `app`? */
export function eventNamesApp(detail: unknown, app: LiveApp): boolean {
  const apps = (detail as { apps?: unknown } | null | undefined)?.apps;
  return Array.isArray(apps) && apps.includes(app);
}
