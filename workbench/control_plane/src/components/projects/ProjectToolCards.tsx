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
import FencedText from "@/components/FencedText";
import AppIcon from "@/components/Icon";
import EntityPill from "@/components/ui/EntityPill";
import Readout from "@/components/Readout";
import RollupCard, { RollupBody, RollupHeader } from "@/components/RollupCard";
import { renderTemplate } from "@/components/genUITemplates";
import { useRouter } from "next/navigation";
import { useEffect } from "react";
import type { ToolEvent } from "@/components/MarkdownMessage";
import { ToolCardShell } from "@/components/ToolCardShell";
import { placementOf } from "@/lib/chatPlacement";
import { parseDatasetTable } from "@/lib/datasetTable";
import { unfenced } from "@/lib/fencedText";
import { useDismissedToolCards, dismissToolCard } from "@/lib/dismissedTools";
import { StatusChip } from "@/components/StatusChip";
import { useOverflowTip } from "@/components/ui/OverflowTip";
import { LEGEND, parseTaskRows, taskRowFacts, type ProjectTaskRow } from "@/lib/projectToolRows";
import { statusAccent } from "@/lib/statusAccent";

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
  analytics_outlook: { icon: "Telescope", label: "Forecast" },
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
  // S7e — the on-the-fly table. It draws as a table (`lib/datasetTable.ts`).
  task_dataset: { icon: "Table2", label: "Task dataset" },
  my_led_projects: { icon: "FolderKanban", label: "Projects you lead" },
  my_areas: { icon: "Layers", label: "Your areas" },
};

/**
 * The app destination a read's card opens, when it has one. The analytics
 * reads, the summary and a rendered report all open the Reports app. Since
 * WS-27bn R5f its Overview draws what the Analytics app drew, and the
 * Analytics app is gone. Exported for `ProjectToolCards.test.ts`.
 */
export const OPENS_APP: Record<string, { app: "reports"; label: string }> = {
  project_summary: { app: "reports", label: "Open Reports" },
  analytics_stuck: { app: "reports", label: "Open Reports" },
  analytics_load: { app: "reports", label: "Open Reports" },
  analytics_throughput: { app: "reports", label: "Open Reports" },
  analytics_finished: { app: "reports", label: "Open Reports" },
  analytics_outlook: { app: "reports", label: "Open Reports" },
  team_capacity: { app: "reports", label: "Open Reports" },
  find_conflicts: { app: "reports", label: "Open Reports" },
  report_list: { app: "reports", label: "Open Reports" },
  report_render: { app: "reports", label: "Open Reports" },
  render_report: { app: "reports", label: "Open Reports" },
  status_report: { app: "reports", label: "Open Reports" },
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
  // WS-46 P13 — several new tasks in one project. Its receipt lists every
  // task it made, so it draws through `BatchReceiptCard`, not one link.
  create_tasks: { icon: "ListPlus", label: "Tasks created" },
  // H-273 — several new tags or types. The receipt lists every word it
  // added, so it draws through `BatchReceiptCard` too.
  create_tags: { icon: "Tags", label: "Tags added" },
  create_types: { icon: "Shapes", label: "Types added" },
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
  // Part of the batch exists and part does not: the member has work to do.
  if (outcome === "partial") return "bg-warning/5 border-warning/40 text-warning";
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
 * `LEGEND` lives in `lib/projectToolRows.ts`.
 */
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

/**
 * A READ is evidence (`lib/chatPlacement.ts`, spec §24 rule 2): it draws in
 * the working trail, under its step, and never as a card after the answer.
 * A Projects tool the map does not name counts as a read here. The fences
 * keep every exported tool named, so only a tool from a newer server lands
 * here, and a hidden receipt is the smaller harm than a wall of cards.
 */
function isProjectEvidence(e: ToolEvent): boolean {
  const p = placementOf(e.name);
  return p === "evidence" || p === undefined;
}

/** A card in the FLOW: a write's receipt, or a view that failed. */
function hasProjectCard(e: ToolEvent): boolean {
  if (e.status !== "done" && e.status !== "error") return false;
  if (!isProjectsTool(e) || isProjectEvidence(e)) return false;
  if (e.status === "done" && VIEW_TOOLS.has(e.name)) return false;
  return true;
}

/** Every tool name this file routes. The placement fence reads it. */
export const PROJECT_CARD_TOOLS: readonly string[] = [...PROJECT_TOOLS];

// ── Result-text parser ────────────────────────────────────────────────────────

// The row parser lives in `lib/projectToolRows.ts` (WS-27bm S9): the chat's
// entity pills read the same rows, so one parser serves both. Re-exported
// here so the cards' callers and their test keep their import path.
export { parseTaskRows, type ProjectTaskRow } from "@/lib/projectToolRows";

// ── Navigation ────────────────────────────────────────────────────────────────

/** Open a task in the Projects app by its deep link (`?task=<id>`). */
function useOpenTask() {
  const router = useRouter();
  return (id: string) => router.push(`/projects?task=${encodeURIComponent(id)}`);
}

// ── Cards ─────────────────────────────────────────────────────────────────────

/**
 * The box of one listed row in a card: a task, a tag, a type (owner
 * feedback, 2026-10-10). Rem padding, so compact density draws it denser.
 * On a touch screen the row is at least 40 px tall, in px, so a finger
 * keeps its target at every density.
 */
export const LIST_ROW_BOX = "px-1 py-0.5 pointer-coarse:min-h-[40px]";

/**
 * The list of rows in a card. It reaches 1 unit past the card's padding on
 * each side, and its rows pad back by the same unit (`LIST_ROW_BOX`). So the
 * text of a row starts at the card's content edge, in line with the
 * header's chevron, and a row's hover fill still has room round its text.
 */
export const LIST_ROWS_BOX = "-mx-1 space-y-px";

/**
 * One task row, on ONE line (owner feedback, 2026-10-10): `#141` muted, the
 * title (it truncates, and the whole text shows in a tip), the status as its
 * pill, the open icon. It was two lines, "#141 title" over "status Backlog".
 *
 * ⚠️ The row wraps by its content, not by a measure. The title and the pill
 * share one wrapping line box, and the title asks for 60 % of it
 * (`basis-3/5`). When the pill does not fit beside that, the pill drops under
 * the title. The open icon is outside that box, so it stays on the first line.
 *
 * The pill is the shared `StatusChip`, in the hue `statusAccent` gives the
 * lane. It drops the "status " word, which only the screen reader hears now.
 */
function TaskRowView({ row }: { row: ProjectTaskRow }) {
  const openTask = useOpenTask();
  const facts = taskRowFacts(row.meta);
  const whole = [`${row.number} ${row.title}`, facts.status, facts.rest].filter(Boolean).join(" · ");
  const { attachLabel, onPointerEnter, onPointerLeave, onFocus, onBlur, tip } = useOverflowTip(whole);
  return (
    <div
      data-task-row=""
      className="flex min-w-0 items-center gap-0.5 rounded-md border border-transparent transition-colors hover:border-border"
    >
      <button
        type="button"
        onClick={() => openTask(row.id)}
        onFocus={onFocus}
        onBlur={onBlur}
        className={`cc-link flex min-w-0 flex-1 flex-wrap items-center gap-x-2 gap-y-0.5 rounded-md text-left hover:bg-secondary/60 ${LIST_ROW_BOX}`}
      >
        <span
          ref={attachLabel}
          onPointerEnter={onPointerEnter}
          onPointerLeave={onPointerLeave}
          data-task-row-title=""
          className="min-w-0 flex-1 basis-3/5 truncate text-[11px] text-foreground"
        >
          <span className="mr-1 tabular-nums text-muted-foreground">{row.number}</span>
          {row.title}
          {facts.rest && <span className="text-muted-foreground"> · {facts.rest}</span>}
        </span>
        {facts.status && (
          <StatusChip
            accent={statusAccent({ category: facts.category, name: facts.status })}
            label={facts.status}
            ariaLabel={`Status: ${facts.status}`}
            className="shrink-0"
          />
        )}
      </button>
      <Button
        variant="ghost"
        size="icon-xs"
        radius="keep"
        layout="inline-flex items-center justify-center"
        onClick={() => openTask(row.id)}
        title="Open in Projects"
        aria-label="Open in Projects"
        className="shrink-0 rounded pointer-coarse:min-h-[40px] pointer-coarse:min-w-[40px]"
      >
        <AppIcon name="ExternalLink" size={11} />
      </Button>
      {tip}
    </div>
  );
}

/** The words a task-list read is known by: "Search · extruder", "My inbox". */
function listLabel(e: ToolEvent): string {
  const args = (e.args ?? {}) as Record<string, unknown>;
  if (e.name === "find_tasks") return `Search${args.query ? ` · ${String(args.query)}` : ""}`;
  if (e.name === "my_work") return String(args.view ?? "") === "inbox" ? "My inbox" : "Assigned to me";
  if (e.name === "calendar") return "Calendar";
  if (e.name === "intake_queue") return "Intake";
  if (e.name === "notifications") return "Notifications";
  return "Tasks";
}

// ── Evidence: a read, drawn inside its step (spec §24 rule 2) ─────────────────

/** The `task_dataset` table: the dataGrid template, a caption, the notes. */
function DatasetView({ result }: { result: string }) {
  const table = parseDatasetTable(result);
  if (!table) return <Readout result={result} legend={LEGEND} />;
  return (
    <div data-dataset-table="" className="min-w-0 space-y-1.5">
      {renderTemplate("dataGrid", {
        title: table.title,
        columns: table.columns,
        kinds: table.kinds,
        rows: table.rows,
      })}
      {(table.caption || table.notes.length > 0) && (
        <div className="space-y-0.5 text-[10px] text-muted-foreground">
          {table.caption && <div>{table.caption}</div>}
          {table.notes.map((n, i) => (
            <div key={i}>{n}</div>
          ))}
        </div>
      )}
    </div>
  );
}

/**
 * One Projects read as the member reads it, with no card chrome: the step
 * row above it already says what it is. Rows open the task, a table draws as
 * a table, and every other read draws through `Readout`.
 */
function ProjectEvidenceBody({ event: e }: { event: ToolEvent }) {
  const router = useRouter();
  const result = e.result || "";
  if (e.status === "error") {
    return (
      <div className="text-[11px] whitespace-pre-wrap text-destructive">
        <FencedText text={forPeopleFenced(result) || "(no result)"} pills={false} />
      </div>
    );
  }
  if (e.name === "task_dataset") return <DatasetView result={result} />;
  const rows = parseTaskRows(result);
  const opens = OPENS_APP[e.name];
  const body = withoutLegend(result);
  const link = body.match(/^\s*link:\s*(\/projects\?[\w=&-]+)\s*$/m)?.[1] ?? "";
  const isList = LIST_TOOLS.has(e.name) || !(e.name in INFO_META);
  return (
    <div className="rounded-md border border-border/60 bg-card/60 px-2.5 py-2 min-w-0 text-[11px]">
      <div className="-mx-1 max-h-72 overflow-y-auto overflow-x-hidden px-1 scrollbar-thin">
        {isList && rows.length > 0 ? (
          <>
            <div className="mb-1 text-[11px] font-medium text-foreground">
              {listLabel(e)} <span className="text-muted-foreground">{rows.length}</span>
            </div>
            <div className={LIST_ROWS_BOX}>
              {rows.map((r) => (
                <TaskRowView key={r.id} row={r} />
              ))}
            </div>
          </>
        ) : (
          <Readout result={result} legend={LEGEND} />
        )}
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
    </div>
  );
}

/**
 * The receipt of a Projects READ for the trail (`ThinkingContainer`), or
 * null when the event is not one. A finished or failed step only: a running
 * step has no result yet.
 */
export function projectEvidence(e: ToolEvent): React.ReactNode | null {
  if (e.status !== "done" && e.status !== "error") return null;
  if (!isProjectsTool(e) || !isProjectEvidence(e)) return null;
  return <ProjectEvidenceBody event={e} />;
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
 *
 * The marks go too. A card draws `forPeopleFenced` through `FencedText`
 * instead, so a name keeps its boundary as a quiet emphasis (owner report,
 * 2026-10-07: a stripped receipt read "Created Fix the extruder in Ops").
 */
export function forPeople(result: string): string {
  return forPeopleFenced(result).replace(/[«»]/g, "");
}

/** `forPeople` with the «marks» kept, for `FencedText` to draw. Pure. */
export function forPeopleFenced(result: string): string {
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
  const shown = forPeopleFenced(e.result || "");
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
      {/* A read draws as UI (`Readout`): no id, no `[key]`, a status as its
          chip. A failure stays one plain message. */}
      <div className="max-h-80 overflow-y-auto scrollbar-thin">
        {failed ? (
          <div className="text-[11px] whitespace-pre-wrap text-destructive">
            <FencedText text={shown || "(no result)"} pills={false} />
          </div>
        ) : (
          <Readout result={e.result || ""} legend={LEGEND} />
        )}
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
export type ActionOutcome = "done" | "partial" | "cancelled" | "refused" | "failed";

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

/**
 * The line a batch prints when it stops at its first refusal
 * (`forms.py::_stopped`). The receipt above it lists what exists.
 */
export const STOPPED_LINE = /^\s*stopped:\s*\S/im;

/**
 * A row of a batch whose create may or may not have landed
 * (`forms.py::_write_batch`, WS-46 P13 review round 2). Something MAY exist,
 * so the receipt is partial, never a muted "Not done".
 */
export const UNKNOWN_LINE = /^\s*unknown:\s*row\s/im;

export function classifyActionResult(
  result: string,
  status: ToolEvent["status"],
  tool = "",
): ActionOutcome {
  if (status === "error") return "failed";
  const text = (result || "").trim();
  if (text.startsWith(CANCELLED)) return "cancelled";
  // A batch that stopped part way (WS-27bm S7d, §13.6 rule 9): something
  // exists, and the receipt says what did not happen. It is not a success.
  if ((receiptIdOf(text) || UNKNOWN_LINE.test(text)) && STOPPED_LINE.test(text)) return "partial";
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
    if ((outcome === "done" || outcome === "partial") && isFreshReceipt(e)) announceChange(e.id);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `endedAt` is read once, at the transition to done
  }, [outcome, e.id]);
  const rowId = rowIdOf(result);
  const openTask = useOpenTask();
  const detail = forPeopleFenced(result);
  const tone = toneFor(outcome, e.name);
  const icon =
    outcome === "failed"
      ? "X"
      : outcome === "partial"
        ? "AlertTriangle"
        : outcome === "cancelled"
          ? "Ban"
          : outcome === "refused"
            ? "Info"
            : meta.icon;
  const heading =
    outcome === "failed"
      ? `${meta.label} — failed`
      : outcome === "partial"
        ? `${meta.label} — stopped part way`
        : outcome === "cancelled"
        ? "Cancelled"
        : outcome === "refused"
          ? "Not done"
          : meta.label;
  // A write that names its task rows ("Updated:", "Assigned:", a new task)
  // draws them as the one-line rows a batch draws (owner feedback,
  // 2026-10-10), not as a paragraph of facts and a separate link.
  const tasks = outcome === "done" || outcome === "partial" ? parseTaskRows(result) : [];
  const lead = tasks.length > 0 ? receiptLead(result) : "";
  const notes = tasks.length > 0 ? batchNotes(result) : [];
  // The rows open their task. The link stays only for a task the rows do not
  // list: the parent of new subtasks prints its id with no row.
  const linkOut = !!rowId && !tasks.some((t) => t.id === rowId);
  // A receipt is a card of the transcript: its arrival rolls up the long
  // cards above it (`components/RollupCard.tsx`). It is short, so it draws
  // no toggle of its own. Its title row is the header (`header="own"`).
  return (
    <RollupCard id={`tool:${e.id}`} title={heading} icon={icon} summary={unfenced(detail).split(NL)[0]} header="own">
    <div className={`rounded-lg border px-2.5 py-2 ${tone}`}>
      <RollupHeader />
      <RollupBody>
        {tasks.length > 0 ? (
          <>
            {lead && (
              <div className="mt-0.5 text-[10px] text-muted-foreground whitespace-pre-wrap">
                <FencedText text={lead} pills={false} />
              </div>
            )}
            <div className={`mt-1 ${LIST_ROWS_BOX}`}>
              {tasks.map((r) => (
                <TaskRowView key={r.id} row={r} />
              ))}
            </div>
            <ReceiptNotes notes={notes} />
          </>
        ) : (
          detail && (
            <div className="mt-0.5 text-[10px] text-muted-foreground whitespace-pre-wrap line-clamp-4">
              <FencedText text={detail} pills={false} />
            </div>
          )
        )}
        {(outcome === "done" || outcome === "partial") && linkOut && (
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
      </RollupBody>
    </div>
    </RollupCard>
  );
}

/** What one batch receipt lists, and its heading when nothing is known to exist. */
export interface BatchKind {
  rows: "task" | "tag" | "type";
  maybe: string;
}

/**
 * The write tools whose receipt lists SEVERAL rows, each with its own id
 * line. `BatchReceiptCard` draws a task as a row that opens the task, a tag
 * as a tag pill and a type as a pill, with the lines about the rows that
 * failed under them.
 */
export const BATCH_TOOLS: ReadonlyMap<string, BatchKind> = new Map<string, BatchKind>([
  ["create_tasks", { rows: "task", maybe: "Tasks may have been created" }],
  // H-273: `forms.py` `_vocab_receipt` prints `- tag «q4» · facts` and then
  // `  tag_id: <uuid>` for each word it added.
  ["create_tags", { rows: "tag", maybe: "Tags may have been added" }],
  ["create_types", { rows: "type", maybe: "Types may have been added" }],
]);

const NL = "\n";

/** One word a vocabulary batch added: a tag or a task type. */
export interface VocabRow {
  kind: "tag" | "type";
  id: string;
  name: string;
  meta: string;
}

const VOCAB_ROW = /^\s*-\s*(tag|type)\s+«([^»]*)»\s*(?:·\s*(.*))?$/;
const VOCAB_ID = /^\s*(tag|type)_id:\s*([0-9a-f-]{36})\s*$/i;

/**
 * Parse `- tag «name» · facts` + `  tag_id: <uuid>` pairs (and the same for
 * a type) out of a batch receipt. A row whose id line is missing, or names
 * the other kind, is not a row. Pure, exported for its test.
 */
export function parseVocabRows(result: string): VocabRow[] {
  const rows: VocabRow[] = [];
  const lines = result.split(NL);
  for (let k = 1; k < lines.length; k++) {
    const id = lines[k].match(VOCAB_ID);
    if (!id) continue;
    const head = (lines[k - 1] ?? "").match(VOCAB_ROW);
    if (!head || head[1] !== id[1].toLowerCase()) continue;
    rows.push({ kind: head[1] as "tag" | "type", id: id[2], name: head[2], meta: (head[3] ?? "").trim() });
  }
  return rows;
}

/**
 * A batch receipt's lines for a person, without the head line and the task
 * rows (the card draws those as rows). What stays: each row that failed, a
 * follow-up that did not land, the stop line and the rows left out. The line
 * that tells the MODEL not to make the tasks again is not for a person. Pure,
 * exported for its test.
 */
export function batchNotes(result: string): string[] {
  const lines = withoutLegend(result).split(NL);
  const kept: string[] = [];
  for (let k = 1; k < lines.length; k++) {
    const line = lines[k];
    const next = lines[k + 1] ?? "";
    if (/^\s*-\s*#\S+\s*«/.test(line) && /^\s*full_id:/i.test(next)) {
      k++; // the task row and its id line: drawn as a row
      continue;
    }
    if (VOCAB_ROW.test(line) && VOCAB_ID.test(next)) {
      k++; // a tag or a type and its id line: drawn as a pill (H-273)
      continue;
    }
    if (/^\s*full_id:/i.test(line) || !line.trim()) continue;
    if (/listed above exist\. Never create them again/.test(line)) continue;
    kept.push(line);
  }
  return forPeopleFenced(kept.join(NL)).split(NL).filter((l) => l.trim());
}

/**
 * The first line of a write's receipt, when it says more than the card's
 * title. A bare lead-in ("Updated:", "Assigned:") restates the title, and a
 * task row is drawn as a row. Pure, exported for its test.
 */
export function receiptLead(result: string): string {
  const first = withoutLegend(result).split(NL)[0] ?? "";
  if (/^\s*-\s*#\S+\s*«/.test(first)) return "";
  const shown = forPeopleFenced(first);
  return /^[A-Za-z]+:$/.test(shown.trim()) ? "" : shown;
}

/** The lines about a receipt's rows: a row that failed, a stop, a note. */
function ReceiptNotes({ notes }: { notes: string[] }) {
  if (notes.length === 0) return null;
  return (
    <div className="mt-1 space-y-0.5">
      {notes.map((n, i) => (
        <div key={i} className="text-[10px] text-muted-foreground whitespace-pre-wrap"
          style={{ overflowWrap: "anywhere" }}>
          <FencedText text={n} pills={false} />
        </div>
      ))}
    </div>
  );
}

/** One word a vocabulary batch added. A tag is the tag pill, a type a plain pill. */
function VocabRowView({ row }: { row: VocabRow }) {
  return (
    <div className={`flex min-w-0 items-center gap-2 ${LIST_ROW_BOX}`}>
      <span className="inline-flex min-w-0 max-w-full">
        <EntityPill fit kind={row.kind === "tag" ? "tag" : "unknown"} label={row.name} />
      </span>
      {row.meta && (
        <span className="min-w-0 truncate text-[10px] text-muted-foreground">
          <FencedText text={row.meta} pills={false} />
        </span>
      )}
    </div>
  );
}

/**
 * The receipt of a batch (`BATCH_TOOLS`): a heading with the count, one row
 * per task made (each opens the task in Projects) or one pill per word
 * added, and the lines about each row that failed. A partial batch wears the
 * warning tone, as `toneFor` gives it.
 */
function BatchReceiptCard({ event: e }: { event: ToolEvent }) {
  const meta = ACTION_META[e.name] ?? { icon: "ListPlus", label: genericLabel(e.name) };
  const kind: BatchKind = BATCH_TOOLS.get(e.name) ?? { rows: "task", maybe: "Tasks may have been created" };
  const result = (e.result || "").trim();
  const outcome = classifyActionResult(result, e.status, e.name);
  useEffect(() => {
    if ((outcome === "done" || outcome === "partial") && isFreshReceipt(e)) announceChange(e.id);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `endedAt` is read once, at the transition to done
  }, [outcome, e.id]);
  const tasks = kind.rows === "task" ? parseTaskRows(result) : [];
  const words = kind.rows === "task" ? [] : parseVocabRows(result);
  const rows: readonly unknown[] = kind.rows === "task" ? tasks : words;
  const head = forPeopleFenced(withoutLegend(result).split(NL)[0] ?? "");
  const notes = batchNotes(result);
  const icon =
    outcome === "failed" ? "X"
      : outcome === "partial" ? "AlertTriangle"
        : outcome === "cancelled" ? "Ban"
          : outcome === "refused" ? "Info"
            : meta.icon;
  // The header draws the count after the title ("Tasks created · 5"), in
  // both states, so the title does not carry it too.
  const heading =
    outcome === "failed" ? `${meta.label} — failed`
      : outcome === "partial" && rows.length === 0 ? kind.maybe
      : outcome === "partial" ? `${meta.label} — stopped part way`
        : outcome === "cancelled" ? "Cancelled"
          : outcome === "refused" ? "Not done"
            : rows.length === 0 ? `${meta.label} (0)`
              : meta.label;
  // The owner's tall "Tasks created (5)" (2026-10-10). Five rows are long,
  // so it rolls up when a newer card arrives, to its title row: its label,
  // its count and the rows' names on one line. That row is the card's own
  // (`header="own"`), so there is one title and one toggle.
  const names = kind.rows === "task"
    ? tasks.map((r) => `${r.number} ${unfenced(r.title)}`)
    : words.map((r) => r.name);
  return (
    <RollupCard
      id={`tool:${e.id}`}
      title={heading}
      icon={icon}
      rows={rows.length || undefined}
      summary={names.join(" · ") || unfenced(head)}
      header="own"
    >
    <div className={`rounded-lg border px-2.5 py-2 ${toneFor(outcome, e.name)}`}>
      <RollupHeader />
      <RollupBody>
          {head && (outcome !== "done" || rows.length === 0) && (
            <div className="mt-0.5 text-[10px] text-muted-foreground whitespace-pre-wrap">
              <FencedText text={head} pills={false} />
            </div>
          )}
          {rows.length > 0 && (
            <div className={`mt-1 max-h-80 overflow-y-auto overflow-x-hidden scrollbar-thin ${LIST_ROWS_BOX}`}>
              {tasks.map((r) => (
                <TaskRowView key={r.id} row={r} />
              ))}
              {words.map((r) => (
                <VocabRowView key={r.id} row={r} />
              ))}
            </div>
          )}
          <ReceiptNotes notes={notes} />
      </RollupBody>
    </div>
    </RollupCard>
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

  // Only the FLOW is here: the receipts of writes, and a view that failed.
  // Every read draws inside its step (`projectEvidence`, spec §24).
  const items: React.ReactNode[] = [];
  for (const e of all) {
    if (BATCH_TOOLS.has(e.name)) {
      items.push(<BatchReceiptCard key={e.id} event={e} />);
      continue;
    }
    const meta = INFO_META[e.name];
    if (meta) {
      items.push(<InfoCard key={e.id} event={e} icon={meta.icon} label={meta.label} />);
      continue;
    }
    // A write, known or new. A new one draws the generic receipt from its
    // name, so shipping the tool never waits on a card (spec §7.3).
    items.push(<ActionResultCard key={e.id} event={e} />);
  }

  return <div className="mt-3 space-y-2 min-w-0 overflow-hidden">{items}</div>;
}
