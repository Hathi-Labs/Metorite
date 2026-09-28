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
}

// ── the wizard ───────────────────────────────────────────────────────────────

export type Step = "upload" | "review" | "map" | "run";
export const STEPS: readonly { id: Step; label: string }[] = [
  { id: "upload", label: "Upload" },
  { id: "review", label: "Review" },
  { id: "map", label: "Map" },
  { id: "run", label: "Import" },
];

export function isTerminal(state: RunState | undefined): boolean {
  return state === "done" || state === "failed" || state === "discarded";
}

/** The mapping the admin's edits make, starting from what the plan shows. */
export function mappingFrom(
  run: ImportRun,
  people: Record<string, string | null>,
  stages: Record<string, Stage>,
  target: ImportMapping["target"],
): ImportMapping {
  const statuses: ImportMapping["statuses"] = {};
  for (const status of run.plan.statuses) {
    const category = stages[status.name] ?? status.category;
    statuses[status.name] = { category, name: status.becomes !== status.name ? status.becomes : null };
  }
  const chosen: Record<string, string | null> = {};
  for (const person of run.plan.people) {
    chosen[person.ref] = person.ref in people ? people[person.ref] : person.member;
  }
  return { people: chosen, statuses, target, grant: run.mapping?.grant || "org" };
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
 * or null when there is none. The server decides (`imports._mark_continuation`).
 */
export function continuationNote(plan: ImportPlan): string | null {
  if (!plan.inherited_from) return null;
  if (plan.continues) {
    return (
      "This export continues an earlier import. It starts from that import's choices and goes into the same " +
      "spaces. A space or list renamed in ClickUp since then is created again."
    );
  }
  return (
    "This export continues an earlier import, but the destination or the access changed. " +
    "It starts a new tree instead of going into the earlier spaces."
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
export function reportLines(report: ImportReport | null | undefined): string[] {
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
  return out;
}

function plural(n: number | undefined, noun: string): string {
  if (!n) return "";
  const word =
    n === 1
      ? noun
      : noun === "person"
        ? "people"
        : noun.endsWith("y")
          ? `${noun.slice(0, -1)}ies`
          : noun.endsWith("s")
            ? `${noun}es`
            : `${noun}s`;
  return `${n.toLocaleString()} ${word}`;
}
