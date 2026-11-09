/**
 * Projects · project templates, the web half (WS-39 TPL-1b).
 *
 * Pure: no React, no fetch. The routes are `routes/projects/templates.py`
 * (TPL-1a, spec `project_management_app.md` §14.8). `PROJECTS_TEMPLATES_ORGS`
 * is the gate: while it does not hold the org, every route answers 404, and
 * the page draws neither the menu item nor the picker. The page learns that
 * from one read of the list (`templatesApi.list` gives `null` on a 404).
 */

export interface TemplateCounts {
  statuses: number;
  types: number;
  fields: number;
  tags: number;
  tasks: number;
}

export interface TemplateRow {
  id: string;
  kind: string;
  name: string;
  description: string | null;
  /** WM-0 has not landed, so this is null today. Shown only when set. */
  work_style: string | null;
  counts: TemplateCounts;
  created_by: string;
  created_at: string | null;
  updated_at: string | null;
}

export interface TemplateApplyResult {
  project: { id: string; name: string; parent_project_id?: string | null };
  template_id: string;
  replayed: boolean;
  /** True when the new project owns a copy of the template's statuses. */
  own_statuses: boolean;
  created: TemplateCounts;
}

/** What a template keeps. The Save dialog draws this list. */
export const TEMPLATE_KEEPS: readonly string[] = [
  "Statuses",
  "Task types",
  "Custom fields",
  "Tags",
  "The task tree, with its dates as days from the project start",
];

/** What a template leaves out. The Save dialog draws this list. */
export const TEMPLATE_LEAVES_OUT: readonly string[] = [
  "Assignees",
  "Comments",
  "History",
];

/** The value of the "Blank project" choice in the picker. */
export const BLANK_CHOICE = "";

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

/** "12 tasks, 5 statuses, 2 fields, 3 tags". Zero counts are left out. */
export function countsLine(counts: TemplateCounts): string {
  const parts: string[] = [];
  if (counts.tasks > 0) parts.push(plural(counts.tasks, "task", "tasks"));
  if (counts.statuses > 0) parts.push(plural(counts.statuses, "status", "statuses"));
  if (counts.types > 0) parts.push(plural(counts.types, "task type", "task types"));
  if (counts.fields > 0) parts.push(plural(counts.fields, "field", "fields"));
  if (counts.tags > 0) parts.push(plural(counts.tags, "tag", "tags"));
  return parts.length > 0 ? parts.join(", ") : "No tasks, statuses or fields";
}

/** The label of one choice in the picker: the name, then its counts. */
export function templateOptionLabel(t: TemplateRow): string {
  return `${t.name} — ${countsLine(t.counts)}`;
}

/** Does the page offer "Save as template" and the picker? Only with the flag. */
export function templatesOffered(flagOn: boolean): boolean {
  return flagOn === true;
}

/** Only a project or a subproject holds tasks, so only those can be saved. */
export function canSaveAsTemplate(
  flagOn: boolean,
  level: "space" | "folder" | "project" | "subproject",
): boolean {
  return templatesOffered(flagOn) && (level === "project" || level === "subproject");
}

const DAY = /^\d{4}-\d{2}-\d{2}$/;

/** Is this a real calendar day in the years the routes accept (1900 to 2999)? */
export function isStartDate(value: string): boolean {
  if (!DAY.test(value)) return false;
  const [y, m, d] = value.split("-").map(Number);
  if (y < 1900 || y > 2999) return false;
  const dt = new Date(Date.UTC(y, m - 1, d));
  return dt.getUTCFullYear() === y && dt.getUTCMonth() === m - 1 && dt.getUTCDate() === d;
}

/**
 * Can the picker go on? A blank project needs a name. A template needs a
 * start date, because every starter date counts from it.
 */
export function pickerReady(input: {
  templateId: string;
  name: string;
  startDate: string;
}): boolean {
  if (input.templateId === BLANK_CHOICE) return input.name.trim().length > 0;
  return isStartDate(input.startDate);
}

/** Today as `YYYY-MM-DD` in the browser's own zone. */
export function todayLocal(now: Date = new Date()): string {
  const p = (n: number) => String(n).padStart(2, "0");
  return `${now.getFullYear()}-${p(now.getMonth() + 1)}-${p(now.getDate())}`;
}

/** The note under the picker about statuses (step 4 of the apply). */
export function statusesNote(parentName: string): string {
  return (
    "The new project takes the template's statuses if you may edit statuses. " +
    `Without that right it uses the statuses of ${parentName}.`
  );
}

/** The toast after an apply, in words a member understands. */
export function appliedMessage(result: TemplateApplyResult, parentName: string): string {
  const tasks = plural(result.created.tasks, "starter task", "starter tasks");
  if (result.own_statuses) {
    return `Made “${result.project.name}” with ${tasks} and the template's statuses.`;
  }
  return (
    `Made “${result.project.name}” with ${tasks}. It uses the statuses of ${parentName}, ` +
    "because changing statuses needs the projects:settings:write permission. Ask an organization admin."
  );
}

/**
 * The words for a refusal. The routes send `{error, message}` and the
 * message already says what to do, so it wins. This is the fallback for a
 * status that arrives without one.
 */
export function refusalText(status: number, message: string | null | undefined): string {
  const said = (message ?? "").trim();
  if (status === 404) {
    return said && said !== "Not Found"
      ? said
      : "Templates are not switched on for your organization, or the template or project is gone. Reload the page and try again.";
  }
  if (said) return said;
  if (status === 403) {
    return "You may not do this. Ask an owner of the project or an organization admin for access.";
  }
  if (status === 409) return "That did not go through at the same moment. Try again.";
  if (status === 422) return "The template or the dates are not valid. Check them and try again.";
  return "Could not finish. Check your connection and try again.";
}

/** A fresh key for one apply. The same key sent again never makes a second project. */
export function newApplyKey(): string {
  const rand =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
  return `web-${rand}`;
}
