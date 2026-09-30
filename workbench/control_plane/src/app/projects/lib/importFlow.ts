/**
 * Projects · file import — the wizard's pure logic (WS-41 I-4).
 *
 * Spec: `project-docs/specs/project_import.md` §7.7 · decision D80.
 *
 * Everything the dialog decides that does not need React lives here, so it is
 * tested in node (`importFlow.test.ts`). The SERVER plans; this module only
 * reads the plan, keeps the admin's edits, and says what to show. It computes
 * no count of its own: a wizard that recounted the file could disagree with
 * the writer.
 */

import type { SelectOption } from "@/components/ui/SelectButton";
import type { Access } from "@/lib/access";
import { hasCapability } from "@/lib/access";

/** The permission every import route checks (gateway `imports.IMPORT_PERMISSION`). */
export const IMPORT_PERMISSION = "admin:access:manage";

/**
 * The flag. ⚠️ Only the LITERAL `process.env.NEXT_PUBLIC_PROJECTS_IMPORT` is
 * inlined into the browser bundle, so the default reads it literally and a
 * test passes an explicit `env` instead. `publicFlags.test.ts` fences it.
 */
export function importEnabled(env?: Record<string, string | undefined>): boolean {
  const raw = env ? env.NEXT_PUBLIC_PROJECTS_IMPORT : process.env.NEXT_PUBLIC_PROJECTS_IMPORT;
  return ["1", "true", "on", "yes"].includes(String(raw ?? "").trim().toLowerCase());
}

/**
 * Show the entry? A courtesy only: the gateway refuses anybody without the
 * permission. Hidden until access has loaded, so it never flashes.
 */
export function canImport(access: Access, loading: boolean): boolean {
  return !loading && hasCapability(access, IMPORT_PERMISSION);
}

/**
 * The Next proxy in front of the gateway buffers a request body up to 10 MB
 * and cuts the rest with only a log warning (`experimental.proxyClientMaxBodySize`).
 * So the client refuses anything larger, with a reason, instead of sending a
 * truncated file. 9.5 MB leaves room for the multipart envelope, and holds
 * about 20,000 ClickUp tasks — the server's own limit for one run.
 */
export const MAX_UPLOAD_BYTES = Math.floor(9.5 * 1024 * 1024);
export const MAX_FILES = 5;

export function uploadProblem(files: readonly { name: string; size: number }[]): string | null {
  if (files.length === 0) return "Choose the ClickUp export file.";
  if (files.length > MAX_FILES) return `Choose at most ${MAX_FILES} files.`;
  const total = files.reduce((sum, f) => sum + f.size, 0);
  if (total > MAX_UPLOAD_BYTES) {
    return (
      `These files come to ${megabytes(total)}. One import takes at most ` +
      `${megabytes(MAX_UPLOAD_BYTES)}. Export one space at a time and import each.`
    );
  }
  const wrong = files.find((f) => !/\.csv$/i.test(f.name));
  if (wrong) return `${wrong.name} is not a CSV file. ClickUp's workspace export is a CSV.`;
  return null;
}

export function megabytes(bytes: number): string {
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

// ── the server's shapes (gateway `imports.run_view`, `importer/plan.build_plan`) ──

export type RunState = "uploaded" | "planned" | "applying" | "done" | "failed" | "discarded";
export type Stage = "backlog" | "todo" | "in_progress" | "done" | "cancelled";

export interface PlanPerson {
  ref: string;
  display_name: string;
  email: string | null;
  tasks: number;
  comments: number;
  proposed: string | null;
  match: "email" | "name" | "ambiguous" | null;
  member: string | null;
}

export interface PlanStatus {
  name: string;
  tasks: number;
  lists: number;
  proposed: Stage;
  category: Stage;
  becomes: string;
}

export interface PlanWarning {
  code: string;
  message: string;
  count: number;
}

export interface PlanLoss {
  what: string;
  why: string;
}

/** One source Space, Folder or List, with the admin's choice (I-8). */
export interface PlanTreeNode {
  ref: string;
  kind: "space" | "folder" | "project";
  name: string;
  parent_ref: string | null;
  tasks: number;
  becomes?: string;
  skip?: boolean;
  skipped?: boolean;
}

/** A column the importer does not read, and what the file holds in it (I-8). */
export interface PlanColumn {
  name: string;
  tasks: number;
  samples: string[];
  choice: ColumnChoice;
}

export type ColumnChoice = "skip" | "description";
export interface ContainerChoice {
  name?: string | null;
  skip?: boolean;
}

export interface ImportPlan {
  summary: {
    tasks: number;
    subtasks: number;
    spaces: number;
    folders: number;
    projects: number;
    people: number;
    comments: number;
    rows_read: number;
  };
  warnings: PlanWarning[];
  losses: PlanLoss[];
  people: PlanPerson[];
  statuses: PlanStatus[];
  closed_tasks: number;
  completed_at_estimated: number;
  done_status_added: number;
  skip: { written_by_old_importer: number; total: number };
  to_update: number;
  existing_comments_checked?: number;
  to_write: { tasks: number; comments: number };
  /** I-8. Absent on a run planned before I-8. */
  tree?: PlanTreeNode[];
  columns?: PlanColumn[];
  skipped_by_choice?: { containers: number; tasks: number };
  target: { kind: "new_space" | "existing"; name: string | null; project_id: string | null };
  grant: string;
  errors: string[];
  ready: boolean;
  inherited_from?: string | null;
  /** The earlier run's mapping still holds, so the writer reuses its spaces. */
  continues?: boolean;
}

export interface ImportMapping {
  people: Record<string, string | null>;
  statuses: Record<string, { category: Stage; name?: string | null }>;
  target: { kind: "new_space" | "existing"; name?: string | null; project_id?: string | null };
  grant: string;
  columns?: Record<string, ColumnChoice>;
  containers?: Record<string, ContainerChoice>;
}

export interface ImportReport {
  created?: { spaces?: number; folders?: number; projects?: number; reused?: number };
  tasks_written?: number;
  tasks_updated?: number;
  tasks_unchanged?: number;
  conflicts_kept?: number;
  tasks_skipped?: number;
  tasks_moved?: number;
  comments_written?: number;
  completed_at_estimated?: number;
  done_status_added?: number;
  lanes_added?: number;
  tags_dropped?: number;
  people_unassigned?: number;
  space_ids?: string[];
  error?: string;
}

export interface ImportRun {
  id: string;
  source: string;
  state: RunState;
  created_by: string;
  created_at: string | null;
  files: { name: string; bytes: number }[];
  mapping: ImportMapping;
  plan: ImportPlan;
  progress?: { cursor?: number } | null;
  report?: ImportReport | null;
  /** Until when "Discard this import" is offered (§6.9), or null. */
  discard_until?: string | null;
  /** The server's answer to "offer Discard now?". */
  discardable?: boolean;
}

// ── discard (I-6) ────────────────────────────────────────────────────────────

/** What a discard removed, from `import_discard.discard_written_run`. */
export interface DiscardCounts {
  nodes?: number;
  spaces?: number;
  tasks?: number;
  comments_elsewhere?: number;
  updates_kept?: number;
}

/** One row of `GET /projects/import/runs`. */
export interface ImportRunSummary {
  id: string;
  source: string;
  state: RunState;
  created_by: string;
  created_at: string | null;
  finished_at: string | null;
  summary: ImportPlan["summary"] | null;
  tasks_written: number;
  discarded: DiscardCounts | null;
  discard_until: string | null;
  discardable: boolean;
}

/** A 409 from the discard route: what stops it, by name. */
export interface DiscardRefusal {
  message: string;
  blocking: { id: string; title: string }[];
}

/** Read the route's `{message, blocking}` detail, or null for any other shape. */
export function discardRefusal(detail: unknown): DiscardRefusal | null {
  if (!detail || typeof detail !== "object" || Array.isArray(detail)) return null;
  const { message, blocking } = detail as { message?: unknown; blocking?: unknown };
  if (typeof message !== "string") return null;
  const rows = Array.isArray(blocking) ? blocking : [];
  return {
    message,
    blocking: rows
      .filter((b): b is { id: string; title?: string } => typeof (b as { id?: unknown })?.id === "string")
      .map((b) => ({ id: b.id, title: String(b.title ?? "") })),
  };
}

/** What the discard did, in the admin's words. */
export function discardLines(counts: DiscardCounts | null | undefined): string[] {
  const c = counts ?? {};
  const out: string[] = [];
  const removed = [plural(c.spaces, "space"), plural(c.tasks, "task")].filter(Boolean);
  if (removed.length) out.push(`Removed ${removed.join(" and ")}.`);
  const others = (c.nodes ?? 0) - (c.spaces ?? 0);
  if (others > 0) out.push(`Removed ${plural(others, "folder or list", "folders and lists")} it created.`);
  if (c.comments_elsewhere)
    out.push(`Removed ${plural(c.comments_elsewhere, "comment")} it added to tasks an earlier import made.`);
  if (c.updates_kept)
    out.push(`${plural(c.updates_kept, "update")} to tasks an earlier import made stay. The earlier values are not kept.`);
  if (!out.length) out.push("The import had written nothing, so nothing was removed.");
  return out;
}

const STATE_LABEL: Record<RunState, string> = {
  uploaded: "Not started",
  planned: "Not started",
  applying: "Running",
  done: "Done",
  failed: "Stopped",
  discarded: "Discarded",
};

/** A run in the list: when, how much, and where it stands. */
export function runLine(run: ImportRunSummary): { when: string; what: string; state: string } {
  const at = run.finished_at ?? run.created_at;
  const when = at ? new Date(at).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "";
  if (run.state === "discarded") {
    const removed = run.discarded?.tasks;
    return { when, what: removed ? `Removed ${plural(removed, "task")}` : "Nothing was written", state: STATE_LABEL.discarded };
  }
  const tasks = run.state === "done" || run.state === "failed" ? run.tasks_written : run.summary?.tasks ?? 0;
  const what = run.state === "done" || run.state === "failed" ? `${plural(tasks, "new task") || "No new tasks"}` : `${plural(tasks, "task")} in the file`;
  return { when, what, state: STATE_LABEL[run.state] ?? run.state };
}

// ── the wizard ───────────────────────────────────────────────────────────────

export type Step = "upload" | "review" | "tree" | "map" | "run";
export const STEPS: readonly { id: Step; label: string }[] = [
  { id: "upload", label: "Upload" },
  { id: "review", label: "Review" },
  { id: "tree", label: "Spaces" },
  { id: "map", label: "Map" },
  { id: "run", label: "Import" },
];

export function isTerminal(state: RunState | undefined): boolean {
  return state === "done" || state === "failed" || state === "discarded";
}

/** The poll interval. It backs off after a failed poll, and never stops. */
export const POLL_MS = 1500;
export function pollDelay(misses: number): number {
  return Math.min(POLL_MS * 2 ** Math.max(0, misses), 30_000);
}

/**
 * The gateway treats a writer whose heartbeat is older than 120 s as dead,
 * and `POST …/apply` then resumes it (§7.3). The wizard offers "Resume" a
 * little after that. On a live writer the server refuses with 409.
 */
export const STALL_MS = 150_000;
export function isStalled(run: ImportRun | null, lastMoveAt: number, now: number): boolean {
  return run?.state === "applying" && now - lastMoveAt > STALL_MS;
}

/**
 * Keep the run when the dialog opens again? Yes while it writes, and once
 * more after it ends if the admin has not seen its report. Otherwise start
 * a fresh wizard.
 */
export function keepOnReopen(run: ImportRun | null, reportSeen: boolean): boolean {
  if (!run) return false;
  if (run.state === "applying") return true;
  return (run.state === "done" || run.state === "failed") && !reportSeen;
}

/** The I-8 choices the wizard holds beside people and stages. */
export interface WizardChoices {
  grant?: string;
  /** Source status name → the name it takes in Metorite. */
  statusNames?: Record<string, string>;
  containers?: Record<string, ContainerChoice>;
  columns?: Record<string, ColumnChoice>;
}

/** The mapping the admin's edits make, starting from what the plan shows. */
export function mappingFrom(
  run: ImportRun,
  people: Record<string, string | null>,
  stages: Record<string, Stage>,
  target: ImportMapping["target"],
  choices: WizardChoices = {},
): ImportMapping {
  const statuses: ImportMapping["statuses"] = {};
  for (const status of run.plan.statuses) {
    const category = stages[status.name] ?? status.category;
    const typed = choices.statusNames?.[status.name];
    const becomes = typed !== undefined ? cleanName(typed) || status.name : status.becomes;
    statuses[status.name] = { category, name: becomes !== status.name ? becomes : null };
  }
  const chosen: Record<string, string | null> = {};
  for (const person of run.plan.people) {
    chosen[person.ref] = person.ref in people ? people[person.ref] : person.member;
  }
  const containers: Record<string, ContainerChoice> = {};
  for (const [ref, choice] of Object.entries(choices.containers ?? run.mapping?.containers ?? {})) {
    const name = cleanName(choice.name ?? "") || null;
    if (choice.skip || name) containers[ref] = { name, skip: Boolean(choice.skip) };
  }
  const columns: Record<string, ColumnChoice> = {};
  for (const [col, how] of Object.entries(choices.columns ?? run.mapping?.columns ?? {})) {
    if (how === "description") columns[col] = how;
  }
  return {
    people: chosen,
    statuses,
    target,
    grant: choices.grant || run.mapping?.grant || "org",
    columns,
    containers,
  };
}

/** Spaces collapsed, as the gateway's validators do. */
export function cleanName(raw: string): string {
  return raw.split(/\s+/).filter(Boolean).join(" ");
}

// ── the tree step (I-8) ──────────────────────────────────────────────────────

export interface TreeRow extends PlanTreeNode {
  depth: number;
  /** Left out by its own skip. */
  skipSelf: boolean;
  /** Left out because a space or folder above it is. */
  skipInherited: boolean;
  /** The name it lands with. */
  shownName: string;
}

/**
 * The source tree in reading order, parents first, with the admin's choices.
 * It mirrors the gateway's `skipped_containers`, which stays the authority:
 * the plan the server answers is what the Import button acts on.
 */
export function treeRows(tree: readonly PlanTreeNode[], choices: Record<string, ContainerChoice>): TreeRow[] {
  const children = new Map<string | null, PlanTreeNode[]>();
  const refs = new Set(tree.map((n) => n.ref));
  for (const node of tree) {
    const parent = node.parent_ref && refs.has(node.parent_ref) ? node.parent_ref : null;
    children.set(parent, [...(children.get(parent) ?? []), node]);
  }
  const out: TreeRow[] = [];
  const walk = (parent: string | null, depth: number, inherited: boolean) => {
    for (const node of children.get(parent) ?? []) {
      const choice = choices[node.ref];
      const skipSelf = Boolean(choice?.skip);
      out.push({
        ...node,
        depth,
        skipSelf,
        skipInherited: inherited,
        shownName: cleanName(choice?.name ?? "") || node.name,
      });
      walk(node.ref, depth + 1, inherited || skipSelf);
    }
  };
  walk(null, 0, false);
  return out;
}

/** What the tree step's footer says: the lists and tasks that will land. */
export function treeTotals(rows: readonly TreeRow[]): { lists: number; skippedLists: number; tasks: number } {
  let lists = 0;
  let skippedLists = 0;
  let tasks = 0;
  for (const row of rows) {
    if (row.kind !== "project") continue;
    if (row.skipSelf || row.skipInherited) skippedLists += 1;
    else {
      lists += 1;
      tasks += row.tasks;
    }
  }
  return { lists, skippedLists, tasks };
}

// ── statuses: rename and merge (I-8, §6.3) ───────────────────────────────────

/**
 * Source status name → the OTHER source names that land in the same Metorite
 * status. Names compare without case, as the gateway merges them.
 */
export function statusMerges(
  statuses: readonly PlanStatus[],
  names: Record<string, string>,
): Record<string, string[]> {
  const landing = (s: PlanStatus) => (cleanName(names[s.name] ?? "") || s.becomes || s.name).toLowerCase();
  const groups = new Map<string, string[]>();
  for (const s of statuses) groups.set(landing(s), [...(groups.get(landing(s)) ?? []), s.name]);
  const out: Record<string, string[]> = {};
  for (const s of statuses) out[s.name] = (groups.get(landing(s)) ?? []).filter((n) => n !== s.name);
  return out;
}

// ── who can see a new space (§5.3) ───────────────────────────────────────────

export const GRANT_ORG = "org";
export function grantOptions(groups: readonly { slug: string; display_name?: string | null }[]): SelectOption[] {
  return [
    { value: GRANT_ORG, label: "Everyone in the organization" },
    ...groups.map((g) => ({ value: `group:${g.slug}`, label: g.display_name || g.slug, hint: "Only this group" })),
  ];
}

/**
 * Tasks written so far, out of the tasks to go through. The cursor counts
 * every task the writer has looked at: written, updated or skipped.
 */
export function progressOf(run: ImportRun | null): { done: number; total: number; percent: number } {
  const total = run?.plan?.summary?.tasks ?? 0;
  const done = Math.min(run?.progress?.cursor ?? 0, total);
  if (run?.state === "done") return { done: total, total, percent: 100 };
  return { done, total, percent: total ? Math.floor((done / total) * 100) : 0 };
}

/**
 * What the review step says about an earlier import of the same workspace,
 * or null when there is none. The server decides (`imports._continues`, the writer's own rule).
 */
export function continuationNote(plan: ImportPlan): string | null {
  // The writer can continue a run by the same file alone, with no task it
  // wrote and so no inherited mapping. `continues` is the truth either way.
  if (plan.continues) {
    const start = plan.inherited_from ? " It starts from that import's choices." : "";
    return (
      `This export continues an earlier import.${start} It goes into the same spaces. ` +
      "A space or list renamed in ClickUp since then is created again."
    );
  }
  if (!plan.inherited_from) return null;
  return (
    "This export continues an earlier import, but it cannot go into the earlier spaces. " +
    "The destination or the access changed, or those spaces were moved or archived. It starts a new tree."
  );
}

/**
 * Stop once before the apply? Yes when the saved mapping breaks a continuation
 * the admin was told about: the re-plan says a new tree, and the admin has not
 * seen that yet. The second "Import" goes ahead.
 */
export function mustConfirmNewTree(plan: ImportPlan, confirmed: boolean): boolean {
  return Boolean(plan.inherited_from) && !plan.continues && !confirmed;
}

/** How the people step says where a proposal came from. */
export function matchLabel(person: PlanPerson): string {
  if (person.match === "email") return "Matched by email";
  if (person.match === "name") return "Matched by name";
  if (person.match === "ambiguous") return "Two members share this name";
  return person.email ? "No member with this email" : "No member with this name";
}

/** One line per report number worth saying, in the admin's words. */
export function reportLines(
  report: ImportReport | null | undefined,
  skipped?: ImportPlan["skipped_by_choice"],
): string[] {
  if (!report) return [];
  const out: string[] = [];
  const created = report.created ?? {};
  const made = [
    plural(created.spaces, "space"),
    plural(created.folders, "folder"),
    plural(created.projects, "project"),
  ].filter(Boolean);
  if (made.length) out.push(`Created ${made.join(", ")}.`);
  if (created.reused) out.push(`Continued in ${created.reused.toLocaleString()} spaces, folders and lists from the earlier import.`);
  if (report.tasks_written) out.push(`Wrote ${plural(report.tasks_written, "new task")}.`);
  if (report.tasks_updated) out.push(`Updated ${plural(report.tasks_updated, "task")} that changed in ClickUp.`);
  if (report.tasks_unchanged) out.push(`${plural(report.tasks_unchanged, "task")} had not changed.`);
  if (report.conflicts_kept)
    out.push(`Kept ${plural(report.conflicts_kept, "edit")} made in Metorite over ClickUp's change.`);
  if (report.comments_written) out.push(`Added ${plural(report.comments_written, "comment")}.`);
  if (report.completed_at_estimated)
    out.push(`Estimated the completion date of ${plural(report.completed_at_estimated, "closed task")}.`);
  if (report.done_status_added) out.push(`Added a Done status to ${plural(report.done_status_added, "list")}.`);
  if (report.lanes_added)
    out.push(`Added ${plural(report.lanes_added, "status")} to lists that were already in Metorite.`);
  if (report.tasks_skipped)
    out.push(`Skipped ${plural(report.tasks_skipped, "task")} the earlier ClickUp connector wrote.`);
  if (report.tasks_moved) out.push(`${plural(report.tasks_moved, "task")} had moved, and kept status and people.`);
  if (report.tags_dropped) out.push(`Dropped ${plural(report.tags_dropped, "tag")} over the tag limits.`);
  if (report.people_unassigned)
    out.push(`${plural(report.people_unassigned, "person")} had no member, so their tasks are unassigned.`);
  // I-8: what the admin unticked is not a loss, but it is not in Metorite either.
  if (skipped?.tasks) out.push(`Left out ${plural(skipped.tasks, "task")} in the spaces and lists you unticked.`);
  return out;
}

function plural(n: number | undefined, noun: string, many?: string): string {
  if (!n) return "";
  const word =
    n === 1
      ? noun
      : many
        ? many
      : noun === "person"
        ? "people"
        : noun.endsWith("y")
          ? `${noun.slice(0, -1)}ies`
          : noun.endsWith("s")
            ? `${noun}es`
            : `${noun}s`;
  return `${n.toLocaleString()} ${word}`;
}
