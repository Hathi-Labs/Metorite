"use client";

/**
 * ProjectToolCards — interactive AG-UI cards for the projects-assistant's tools.
 *
 * Spec: `project-docs/specs/projects_ai_chat.md` §4.2.
 *
 * The Projects twin of TaskToolCards: rendered by the shared <AgentChat> for
 * any assistant message whose tool events include a `skill-projects` tool,
 * and a no-op otherwise — so the same cards appear in the main chat app and
 * in the Projects app's AI chat.
 *
 * Two kinds in slice 1, plus the default:
 *   • TaskListCard  — rows parsed from a list, a search, the member's work or
 *                     a detail; each row opens the task in the Projects app.
 *   • InfoCard      — a titled scrollable text block for every other read.
 *   • (default)     — any tool name the manifest adds later renders as an
 *                     InfoCard from its name, so a new tool never appears as
 *                     raw text and never needs a change here to ship (§7.3).
 *
 * Rows self-source ids from the tool result text: every task row is
 * `- #<n> «title» · <facts>` and the NEXT line is `  full_id: <uuid>`.
 */

import Button from "@/components/ui/Button";
import AppIcon from "@/components/Icon";
import { useRouter } from "next/navigation";
import { useEffect } from "react";
import type { ToolEvent } from "@/components/MarkdownMessage";
import { ToolCardShell } from "@/components/ToolCardShell";
import { useDismissedToolCards, dismissToolCard } from "@/lib/dismissedTools";

// ── Tool → card routing ───────────────────────────────────────────────────────

/** Tools whose result is a list of task rows. */
const LIST_TOOLS = new Set([
  "list_tasks",
  "find_tasks",
  "my_work",
  // S5 — each prints task rows with a `full_id` line, so the list card fits.
  "calendar",
  "intake_queue",
  "notifications",
]);

/** Every other read, with the icon and label its card wears. */
const INFO_META: Record<string, { icon: string; label: string }> = {
  projects_tree: { icon: "FolderTree", label: "Projects" },
  project_summary: { icon: "LayoutDashboard", label: "Summary" },
  task_detail: { icon: "ClipboardList", label: "Task" },
  people_for: { icon: "Users", label: "People" },
  vocabulary: { icon: "Tags", label: "Vocabulary" },
  analytics_stuck: { icon: "Hourglass", label: "Stuck work" },
  analytics_load: { icon: "Scale", label: "Load" },
  analytics_throughput: { icon: "TrendingUp", label: "Throughput" },
  analytics_finished: { icon: "CheckCircle2", label: "Finished" },
  analytics_outlook: { icon: "Telescope", label: "Outlook" },
  // S7a — who holds the work, and whether they have the hours.
  team_capacity: { icon: "Gauge", label: "Capacity" },
  // S7b — who fits one task, and who could help whom.
  fit_for_task: { icon: "UserCheck", label: "Fit" },
  rebalance: { icon: "Scale", label: "Rebalance" },
  find_conflicts: { icon: "TriangleAlert", label: "Conflicts" },
  report_list: { icon: "FileText", label: "Reports" },
  report_render: { icon: "FileText", label: "Report" },
  recurrence: { icon: "Repeat", label: "Repeat rule" },
  // The overlay line is the point of this read, and a list card drops it.
  my_task: { icon: "UserRound", label: "My task" },
  // S4 — the views. Each drew a template card beside this one; this card
  // keeps the facts as text, dismissable, and offers the app.
  render_timeline: { icon: "History", label: "Timeline" },
  render_board: { icon: "Kanban", label: "Board" },
  render_tasks: { icon: "Table2", label: "Task table" },
  render_report: { icon: "FileText", label: "Report" },
  status_report: { icon: "Flag", label: "Status report" },
  // S5 — the rest of the reads
  project_access: { icon: "Users", label: "Access" },
  project_views: { icon: "LayoutList", label: "Views" },
  my_contexts: { icon: "AtSign", label: "Contexts" },
  watchers: { icon: "Eye", label: "Watchers" },
  // S6 — navigation. The page usually opens the row itself; this card keeps
  // the link for a member who is not on the Projects page.
  open_in_app: { icon: "ExternalLink", label: "Open in Projects" },
};

/**
 * The app destination a read's card opens, when it has one. The five
 * analytics reads and the summary open the Analytics app, where the same
 * numbers are drawn; a rendered report opens the Reports app on the list.
 */
const OPENS_APP: Record<string, { app: "analytics" | "reports"; label: string }> = {
  project_summary: { app: "analytics", label: "Open Analytics" },
  analytics_stuck: { app: "analytics", label: "Open Analytics" },
  analytics_load: { app: "analytics", label: "Open Analytics" },
  analytics_throughput: { app: "analytics", label: "Open Analytics" },
  analytics_finished: { app: "analytics", label: "Open Analytics" },
  analytics_outlook: { app: "analytics", label: "Open Analytics" },
  team_capacity: { app: "analytics", label: "Open Analytics" },
  find_conflicts: { app: "analytics", label: "Open Analytics" },
  report_list: { app: "reports", label: "Open Reports" },
  report_render: { app: "reports", label: "Open Reports" },
  render_report: { app: "reports", label: "Open Reports" },
  status_report: { app: "analytics", label: "Open Analytics" },
};

/**
 * The class B writes (S2), with the label each result card wears. Every one
 * showed the member a confirmation card BEFORE it ran — that card is the
 * shared `ConfirmationCard` the chat already renders for `request_confirmation`.
 * This card is the receipt: what changed, and a jump to the row.
 */
const ACTION_META: Record<string, { icon: string; label: string }> = {
  create_task: { icon: "Plus", label: "Task created" },
  update_task: { icon: "PenLine", label: "Task updated" },
  assign: { icon: "UserPlus", label: "Assignees changed" },
  comment: { icon: "MessageSquare", label: "Comment posted" },
  add_subtasks: { icon: "ListTree", label: "Subtasks added" },
  link_tasks: { icon: "Link", label: "Tasks linked" },
  unlink_tasks: { icon: "Unlink", label: "Link removed" },
  move_task: { icon: "MoveRight", label: "Task moved" },
  watch: { icon: "Eye", label: "Watching changed" },
  complete: { icon: "CheckCircle2", label: "Task done" },
  defer: { icon: "CalendarClock", label: "Deferred" },
  unarchive_task: { icon: "ArchiveRestore", label: "Task restored" },
  create_project: { icon: "FolderPlus", label: "Project created" },
  update_project: { icon: "PenLine", label: "Project updated" },
  report_save: { icon: "FileText", label: "Report saved" },
  // S2b — the rest of class B
  create_status: { icon: "Columns3", label: "Status added" },
  update_status: { icon: "PenLine", label: "Status updated" },
  create_type: { icon: "Shapes", label: "Type added" },
  update_type: { icon: "PenLine", label: "Type updated" },
  create_field: { icon: "TextCursorInput", label: "Field added" },
  update_field: { icon: "PenLine", label: "Field updated" },
  create_tag: { icon: "Tag", label: "Tag added" },
  update_tag: { icon: "PenLine", label: "Tag updated" },
  edit_comment: { icon: "MessageSquare", label: "Comment edited" },
  set_recurrence: { icon: "Repeat", label: "Repeat rule set" },
  create_personal_task: { icon: "Plus", label: "Private task captured" },
  set_my_overlay: { icon: "SlidersHorizontal", label: "Your triage updated" },
  // S3 — the guarded acts. Same receipt: the card BEFORE the act carried
  // the counts, this one says what the route reported.
  archive_project: { icon: "Archive", label: "Project archived" },
  unarchive_project: { icon: "ArchiveRestore", label: "Project restored" },
  move_project: { icon: "FolderInput", label: "Project moved" },
  archive_task: { icon: "Archive", label: "Task archived" },
  merge_tasks: { icon: "Merge", label: "Tasks merged" },
  bulk_update: { icon: "ListChecks", label: "Tasks changed" },
  delete_comment: { icon: "MessageSquareX", label: "Comment deleted" },
  revert_activity: { icon: "Undo2", label: "Change reverted" },
  delete_status: { icon: "Trash2", label: "Status deleted" },
  set_status_set: { icon: "Columns3", label: "Status set switched" },
  delete_type: { icon: "Trash2", label: "Type deleted" },
  delete_field: { icon: "Trash2", label: "Field deleted" },
  delete_tag: { icon: "Trash2", label: "Tag deleted" },
  merge_tags: { icon: "Merge", label: "Tags merged" },
  delete_view: { icon: "Trash2", label: "View deleted" },
  report_delete: { icon: "Trash2", label: "Report deleted" },
  delete_attachment: { icon: "Paperclip", label: "File detached" },
  // S4 — the forms. The receipt is the wrapped tool's.
  edit_task: { icon: "PenLine", label: "Task updated" },
  edit_project: { icon: "PenLine", label: "Project updated" },
  propose_plan: { icon: "ListTodo", label: "Plan created" },
  // S5 — the rest of the writes
  save_view: { icon: "LayoutList", label: "View saved" },
  capture_intake: { icon: "Inbox", label: "Captured into intake" },
  triage_intake: { icon: "Inbox", label: "Intake decided" },
  mark_notifications_read: { icon: "BellOff", label: "Bell cleared" },
};

/**
 * The event the Projects page listens for to reload the board after a chat
 * write (S4). Fired once per receipt that reports a done write. The page
 * owns its loaders, so this is the one seam between the two; a card never
 * reaches into the page's state.
 */
export const PROJECTS_CHANGED_EVENT = "cc-projects-changed";
const announced = new Set<string>();

/**
 * How recent a receipt must be to announce. A write the member just made
 * reloads the board. A receipt replayed from history must not: with the chat
 * docked on the board, every past receipt in the conversation mounted on each
 * page load and reloaded the board once per receipt (review of PR #415).
 */
export const FRESH_RECEIPT_MS = 60_000;

/** Did this tool finish just now, in this page's life? Stored events keep the
 *  `endedAt` they finished with, so a replay is old; one with no time at all
 *  predates the field and is old too. */
export function isFreshReceipt(e: { endedAt?: number }, now: number = Date.now()): boolean {
  if (typeof e.endedAt !== "number") return false;
  // A negative age is a server stamp ahead of this machine's clock: not fresh.
  const age = now - e.endedAt;
  return age >= 0 && age < FRESH_RECEIPT_MS;
}

function announceChange(eventId: string): void {
  if (announced.has(eventId)) return;
  announced.add(eventId);
  try {
    window.dispatchEvent(new CustomEvent(PROJECTS_CHANGED_EVENT, { detail: { eventId } }));
  } catch {
    /* a non-browser render */
  }
}

/**
 * The first line of a cancelled write, verbatim from `writes.py::CANCELLED`.
 * `ProjectToolCards.test.ts` reads that file and holds the two equal, so an
 * edit on either side fails a test instead of painting a decline green.
 */
export const CANCELLED = "Cancelled — nothing was changed.";

/**
 * The class C tools: the guarded acts (archive, merge, bulk edit, delete a
 * status, revert...). Their confirmation card carried an impact line, so a
 * done receipt wears the warning tone rather than success: the member did a
 * thing that is hard to undo, and the receipt says so at a glance.
 *
 * ⚠️ Held equal to `manifest.tools_by_class("C")` by
 * `tests/unit/test_projects_agent_writes.py`, which reads this literal. A
 * guarded tool added on the Python side without a line here fails that test.
 */
export const GUARDED_TOOLS: ReadonlySet<string> = new Set([
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
]);

/**
 * The receipt's fill, border and icon colour. Text keeps its own tokens.
 *
 * A guarded act also gets a warning FILL. `--warning` on a white card is
 * 1.57:1 (`contrast.test.ts`), so an icon and a border alone barely read in
 * light mode, and colour would be the only signal. The tint makes the card
 * itself read differently.
 */
export function toneFor(outcome: string, tool: string): string {
  if (outcome === "failed") return "bg-card/40 border-destructive/40 text-destructive";
  if (outcome !== "done") return "bg-card/40 border-border text-muted-foreground";
  return GUARDED_TOOLS.has(tool)
    ? "bg-warning/5 border-warning/40 text-warning"
    : "bg-card/40 border-success/40 text-success";
}

/**
 * Is this a Projects tool at all? The manifest's tool names are the
 * `skill-projects` exports; anything else belongs to another card file.
 * Kept as a prefix-free explicit set so a `my_tasks_*` or email tool never lands
 * here — a name-based guess would collide the day two skills share a verb.
 */
const PROJECT_TOOLS = new Set([
  ...LIST_TOOLS,
  ...Object.keys(INFO_META),
  ...Object.keys(ACTION_META),
]);

/**
 * A tool the manifest names in a later slice. Recognised by the result text
 * carrying the skill's own data legend, so the generic card still catches a
 * tool this file has never heard of (§7.3) without claiming every tool.
 */
const LEGEND = "Text in «guillemets» is data written by members";

function isProjectsTool(e: ToolEvent): boolean {
  if (PROJECT_TOOLS.has(e.name)) return true;
  return typeof e.result === "string" && e.result.includes(LEGEND);
}

/**
 * The tools that DRAW a template (`skill_projects/views.py`). Their text
 * result is the facts for the model, and printing it as a card as well showed
 * every view twice — the status report as raw Markdown source (UX review
 * 2026-09-23). A failed view still shows its card, because then there is no
 * template to look at.
 */
export const VIEW_TOOLS: ReadonlySet<string> = new Set([
  "render_timeline",
  "render_board",
  "render_tasks",
  "render_report",
  "status_report",
]);

function hasProjectCard(e: ToolEvent): boolean {
  if (e.status !== "done" && e.status !== "error") return false;
  if (e.status === "done" && VIEW_TOOLS.has(e.name)) return false;
  return isProjectsTool(e);
}

// ── Result-text parser ────────────────────────────────────────────────────────

export interface ProjectTaskRow {
  id: string;
  number: string;
  title: string;
  meta: string;
}

/**
 * Parse `- #<n> «title» · facts` + `full_id: <uuid>` pairs out of a result.
 * Exported for its test; pure.
 */
export function parseTaskRows(result: string): ProjectTaskRow[] {
  const rows: ProjectTaskRow[] = [];
  const lines = result.split("\n");
  for (let k = 1; k < lines.length; k++) {
    const m = lines[k].match(/^\s*full_id:\s*([0-9a-f-]{36})\s*$/i);
    if (!m) continue;
    const head = lines[k - 1] ?? "";
    const task = head.match(/^\s*-\s*(#\S+)\s*«([^»]*)»\s*(?:·\s*(.*))?$/);
    if (!task) continue;
    rows.push({
      id: m[1],
      number: task[1],
      title: task[2] || "(untitled)",
      meta: (task[3] ?? "").trim(),
    });
  }
  return rows;
}

// ── Navigation ────────────────────────────────────────────────────────────────

/** Open a task in the Projects app by its deep link (`?task=<id>`). */
function useOpenTask() {
  const router = useRouter();
  return (id: string) => router.push(`/projects?task=${encodeURIComponent(id)}`);
}

// ── Cards ─────────────────────────────────────────────────────────────────────

function TaskRowView({ row }: { row: ProjectTaskRow }) {
  const openTask = useOpenTask();
  return (
    <div className="flex items-center gap-1 rounded-md border border-transparent hover:border-border transition-colors">
      <button
        type="button"
        onClick={() => openTask(row.id)}
        className="flex-1 min-w-0 text-left px-1.5 py-1 rounded-md hover:bg-secondary/60"
        title="Open in Projects"
      >
        <span className="block text-[11px] text-foreground truncate">
          <span className="text-muted-foreground mr-1">{row.number}</span>
          {row.title}
        </span>
        {row.meta && (
          <span className="block text-[10px] text-muted-foreground truncate">
            {row.meta.replace(/[«»]/g, "")}
          </span>
        )}
      </button>
      <Button
        variant="ghost"
        size="icon-xs"
        radius="keep"
        layout=""
        onClick={() => openTask(row.id)}
        title="Open in Projects"
        aria-label="Open in Projects"
        className="rounded mr-0.5"
      >
        <AppIcon name="ExternalLink" size={11} />
      </Button>
    </div>
  );
}

function TaskListCard({ event: e }: { event: ToolEvent }) {
  const result = e.result || "";
  const rows = parseTaskRows(result);
  const args = (e.args ?? {}) as Record<string, unknown>;
  const label =
    e.name === "find_tasks"
      ? `Search${args.query ? ` · ${String(args.query)}` : ""}`
      : e.name === "my_work"
        ? String(args.view ?? "") === "inbox" ? "My inbox" : "Assigned to me"
        : e.name === "calendar"
          ? "Calendar"
          : e.name === "intake_queue"
            ? "Intake"
            : e.name === "notifications"
              ? "Notifications"
              : "Tasks";
  const title = `${label} (${rows.length})`;
  if (rows.length === 0) {
    return <InfoCard event={e} icon="ListChecks" label={label} />;
  }
  return (
    <ToolCardShell
      title={title}
      icon={<AppIcon name="ListChecks" size={12} />}
      onDismiss={() => dismissToolCard(e.id)}
    >
      <div className="space-y-0.5 max-h-80 overflow-y-auto overflow-x-hidden scrollbar-thin">
        {rows.map((r) => (
          <TaskRowView key={r.id} row={r} />
        ))}
      </div>
    </ToolCardShell>
  );
}

/** Strip the data legend: the model needs it, a person does not. */
function withoutLegend(result: string): string {
  return result
    .split("\n")
    .filter((line) => !line.startsWith(LEGEND))
    .join("\n")
    .trim();
}

/**
 * A tool's result as a PERSON reads it. The skill writes for the model: member
 * text inside «guillemets» (a fence against prompt injection), and machine
 * lines (`full_id:`, `link:`, `<kind>_id:`, `done:`) the cards parse. None of
 * that is for the member; the other chat apps' cards show plain text
 * (visual review, 2026-09-23). Exported for its test; pure.
 */
export function forPeople(result: string): string {
  return withoutLegend(result)
    .split("\n")
    .filter((l) => !/^\s*(?:[a-z_]+_id|link|done):/.test(l))
    // A gateway refusal leads with the route (`Projects PUT /projects/...: `).
    // The member needs the reason, not the address it came from.
    .map((l) =>
      l
        .replace(/^Projects (?:GET|POST|PATCH|PUT|DELETE) \S+: /, "")
        // Ids the model carries forward and a member cannot use (UX review
        // 2026-09-23): "· project_id <uuid>", "(activity id a1)".
        .replace(/\s*·\s*[a-z_]+_id:? [0-9a-f-]{8,}/gi, "")
        .replace(/\s*\(activity id [^)]*\)/g, "")
        .replace(/[«»]/g, "")
        .replace(/^- /, ""),
    )
    .join("\n")
    .trim();
}

function InfoCard({
  event: e,
  icon,
  label,
}: {
  event: ToolEvent;
  icon: string;
  label: string;
}) {
  const router = useRouter();
  const body = withoutLegend(e.result || "");
  const shown = forPeople(e.result || "");
  const failed = e.status === "error";
  const opens = failed ? undefined : OPENS_APP[e.name];
  // `open_in_app` prints `link: /projects?...`. Only an in-app link becomes
  // a button; anything else stays text.
  const link = failed ? "" : (body.match(/^\s*link:\s*(\/projects\?[\w=&-]+)\s*$/m)?.[1] ?? "");
  return (
    <ToolCardShell
      title={label}
      icon={<AppIcon name={failed ? "AlertTriangle" : icon} size={12} />}
      onDismiss={() => dismissToolCard(e.id)}
    >
      <div
        className={`text-[11px] whitespace-pre-wrap max-h-80 overflow-y-auto scrollbar-thin ${
          failed ? "text-destructive" : "text-muted-foreground"
        }`}
      >
        {shown || "(no result)"}
      </div>
      {link && !opens && (
        <div className="mt-2">
          <Button variant="secondary" size="sm" icon="ExternalLink" onClick={() => router.push(link)}>
            Open in Projects
          </Button>
        </div>
      )}
      {opens && (
        <div className="mt-2">
          <Button
            variant="secondary"
            size="sm"
            icon="ExternalLink"
            onClick={() => router.push(`/projects?app=${opens.app}`)}
          >
            {opens.label}
          </Button>
        </div>
      )}
    </ToolCardShell>
  );
}

/** What a write tool's result says happened. Pure, so the card is testable. */
export type ActionOutcome = "done" | "cancelled" | "refused" | "failed";

/**
 * Classify a write tool's result.
 *
 * A write that HAPPENED carries an id line — `full_id:` for a task the
 * card can jump to, or `status_id:` / `type_id:` / `field_id:` / `tag_id:`
 * for a vocabulary row (S2b) that has no page of its own. Every success
 * return in `writes.py` prints one. A result without one is a refusal or a
 * no-op the tool reported in prose ("A task needs a title.", "Nothing to
 * change."). The first version painted those green under "Task moved"; the
 * S2 verifier caught it. Now: no id, no success.
 */
/** The tools whose success carries no row, only a `done:` line. */
const DONE_LINE_TOOLS = new Set(["mark_notifications_read"]);

export function classifyActionResult(
  result: string,
  status: ToolEvent["status"],
  tool = "",
): ActionOutcome {
  if (status === "error") return "failed";
  const text = (result || "").trim();
  if (text.startsWith(CANCELLED)) return "cancelled";
  if (receiptIdOf(text)) return "done";
  return DONE_LINE_TOOLS.has(tool) && /^\s*done:\s*\S/im.test(text) ? "done" : "refused";
}

/** The task a write touched, from its `full_id:` line, or "". Only a task
 *  has a deep link, so only this id makes the "Open in Projects" button. */
export function rowIdOf(result: string): string {
  return result.match(/full_id:\s*([0-9a-f-]{36})/i)?.[1] ?? "";
}

/**
 * Any `<kind>_id: <uuid>` receipt line — a task, or a vocabulary row — or a
 * `done: …` line for a write that touches no single row (clearing the
 * bell). Both are printed by the skill only after the write returned.
 */
export function receiptIdOf(result: string): string {
  return result.match(/^\s*[a-z_]+_id:\s*([0-9a-f-]{36})\s*$/im)?.[1] ?? "";
}

/**
 * The receipt for a class B or class C write. Four states, each with its own
 * tone from the theme's tokens (`toneFor`): done (success, or warning for a
 * guarded act), refused by the tool in prose (muted), cancelled at the card
 * (muted), failed (destructive). The confirmation
 * card BEFORE the write is the shared `ConfirmationCard`; this is the
 * receipt after it.
 */
function ActionResultCard({ event: e }: { event: ToolEvent }) {
  const meta = ACTION_META[e.name] ?? { icon: "Wrench", label: genericLabel(e.name) };
  const result = (e.result || "").trim();
  const outcome = classifyActionResult(result, e.status, e.name);
  useEffect(() => {
    if (outcome === "done" && isFreshReceipt(e)) announceChange(e.id);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `endedAt` is read once, at the transition to done
  }, [outcome, e.id]);
  const rowId = rowIdOf(result);
  const openTask = useOpenTask();
  const detail = forPeople(result);
  const tone = toneFor(outcome, e.name);
  const icon =
    outcome === "failed" ? "X" : outcome === "cancelled" ? "Ban" : outcome === "refused" ? "Info" : meta.icon;
  const heading =
    outcome === "failed"
      ? `${meta.label} — failed`
      : outcome === "cancelled"
        ? "Cancelled"
        : outcome === "refused"
          ? "Not done"
          : meta.label;
  return (
    <div className={`rounded-lg border px-2.5 py-2 ${tone}`}>
      <div className="flex items-start gap-2">
        <span className="mt-0.5 flex-shrink-0">
          <AppIcon name={icon} size={13} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="text-[11px] font-medium text-foreground">{heading}</div>
          {detail && (
            <div className="mt-0.5 text-[10px] text-muted-foreground whitespace-pre-wrap line-clamp-4">
              {detail}
            </div>
          )}
          {outcome === "done" && rowId && (
            <div className="mt-1">
              <Button
                variant="text"
                size="none"
                icon="ExternalLink"
                onClick={() => openTask(rowId)}
                className="gap-1 text-[10px]"
              >
                Open in Projects
              </Button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/** A label for a tool this file has no entry for — the generic card. */
function genericLabel(name: string): string {
  const words = name.replace(/_/g, " ").trim();
  return words ? words[0].toUpperCase() + words.slice(1) : "Result";
}

// ── Entry point ───────────────────────────────────────────────────────────────

export default function ProjectToolCards({ toolEvents }: { toolEvents?: ToolEvent[] }) {
  const dismissed = useDismissedToolCards();
  const all = (toolEvents ?? []).filter(
    (e) => !dismissed.has(e.id) && hasProjectCard(e),
  );
  if (all.length === 0) return null;

  const items: React.ReactNode[] = [];
  for (const e of all) {
    if (LIST_TOOLS.has(e.name)) {
      items.push(<TaskListCard key={e.id} event={e} />);
      continue;
    }
    const meta = INFO_META[e.name];
    if (meta) {
      items.push(<InfoCard key={e.id} event={e} icon={meta.icon} label={meta.label} />);
      continue;
    }
    if (e.name in ACTION_META) {
      items.push(<ActionResultCard key={e.id} event={e} />);
      continue;
    }
    // A tool the manifest added after this file was written. The generic
    // card renders it from its name, so shipping the tool never waits on a
    // card (spec §7.3). If it prints task rows, it gets the list card.
    if (parseTaskRows(e.result || "").length > 0) {
      items.push(<TaskListCard key={e.id} event={e} />);
      continue;
    }
    items.push(<InfoCard key={e.id} event={e} icon="Wrench" label={genericLabel(e.name)} />);
  }

  return <div className="mt-3 space-y-2 min-w-0 overflow-hidden">{items}</div>;
}
