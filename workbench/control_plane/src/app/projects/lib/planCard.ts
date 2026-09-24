/**
 * The plan card's decisions, pure (WS-27bm S7d, spec §13.6).
 *
 * `genUITemplates.tsx` `PlanCard` draws what this module decides, so the
 * decisions are testable in the node-env suite where the card cannot render.
 *
 * Four rules live here:
 *
 * 1. **The editable fields are a closed list** (`PLAN_EDIT_COLS`). The fit,
 *    the hours and the marks come from the server's preview and are READ-ONLY.
 *    The member changes an owner or a date, and the server marks the plan
 *    again after the submit (§13.6 rule 3).
 * 2. **The submit carries what round-trips** (`planSubmit`): the key, the
 *    start date, the `after` keys and the three scores. So the sort score
 *    after the submit is the score the card showed (§13.6 rule 10). It never
 *    carries the fit, the hours or the marks, because those are the server's.
 * 3. **A dropped row drops its links** (§13.6 rule 6). `planSubmit` strips an
 *    `after` key that no remaining row carries. The skill lists the dropped
 *    links on the confirm card.
 * 4. **A mark warns and never blocks** (§13.6 rule 3). `planIncomplete` reads
 *    the four required fields and a start after the due date, and no mark.
 *
 * There are no phases (owner, 2026-09-24).
 */

export type PlanCell = "text" | "number" | "date";

export interface PlanRow {
  key: string;
  title: string;
  owner: string;
  effort_mins: number;
  start: string;
  due: string;
  after: string[];
  importance: number | null;
  impact: number;
  urgency: number;
  effort: number;
  priority: number | null;
  /** Read-only, from the server's preview. Absent without the HR grant. */
  fit?: string;
  hours?: string;
  marks: string[];
  warnings: string[];
}

/** The columns the member may edit, in order. Nothing else is an input. */
export const PLAN_EDIT_COLS: ReadonlyArray<{ key: keyof PlanRow; label: string; type: PlanCell }> = [
  { key: "title", label: "Task", type: "text" },
  { key: "owner", label: "Owner", type: "text" },
  { key: "effort_mins", label: "Effort (min)", type: "number" },
  { key: "start", label: "Start", type: "date" },
  { key: "due", label: "Due", type: "date" },
];

/** The fields the server owns. The card shows them and never edits them. */
export const PLAN_READ_ONLY: ReadonlyArray<keyof PlanRow> = ["fit", "hours", "marks", "warnings", "after"];

const text = (v: unknown): string => (typeof v === "string" ? v : v == null ? "" : String(v));
const whole = (v: unknown, f: number): number => {
  const n = typeof v === "number" ? v : parseFloat(String(v));
  return Number.isFinite(n) ? Math.round(n) : f;
};
const score = (v: unknown): number => Math.max(1, Math.min(5, whole(v, 3)));
const strings = (v: unknown): string[] =>
  Array.isArray(v) ? v.map((x) => text(x).trim()).filter(Boolean) : [];

/** One row from the skill's `tasks` array, with every field defined. */
export function planRowFrom(raw: unknown, index: number): PlanRow {
  const r = (raw ?? {}) as Record<string, unknown>;
  const imp = r.importance;
  const row: PlanRow = {
    key: text(r.key).trim() || `t${index + 1}`,
    title: text(r.title),
    owner: text(r.owner),
    effort_mins: whole(r.effort_mins, 0),
    start: text(r.start).slice(0, 10),
    due: text(r.due).slice(0, 10),
    after: strings(r.after),
    importance: imp == null || imp === "" ? null : whole(imp, 0),
    impact: score(r.impact),
    urgency: score(r.urgency),
    effort: score(r.effort),
    priority: r.priority == null ? null : whole(r.priority, 0),
    marks: strings(r.marks),
    warnings: strings(r.warnings),
  };
  if (r.fit != null) row.fit = text(r.fit);
  if (r.hours != null) row.hours = text(r.hours);
  return row;
}

export function planRowsFrom(tasks: unknown): PlanRow[] {
  return (Array.isArray(tasks) ? tasks : []).map((t, i) => planRowFrom(t, i));
}

/** Is this row marked? A marked row still submits (rule 3). */
export function isMarked(row: PlanRow): boolean {
  return row.marks.length > 0;
}

/** The one line a marked row shows under its cells. */
export function markLine(row: PlanRow): string {
  return [...row.marks, ...row.warnings].join(" · ");
}

/** A key no row carries yet, for a row the member adds on the card. */
export function newRowKey(rows: PlanRow[]): string {
  const taken = new Set(rows.map((r) => r.key));
  for (let n = 1; ; n++) {
    const key = `new${n}`;
    if (!taken.has(key)) return key;
  }
}

/** A blank row the member adds. It carries no fit until the server marks it. */
export function blankRow(rows: PlanRow[]): PlanRow {
  return planRowFrom({ key: newRowKey(rows), effort_mins: 60 }, rows.length);
}

/** Why the submit is off, or "" when the plan can go. Marks never block. */
export function planIncomplete(name: string, rows: PlanRow[]): string {
  if (!name.trim()) return "The project needs a name.";
  if (rows.length === 0) return "The plan needs at least one task.";
  for (const r of rows) {
    if (!r.title.trim() || !r.owner.trim() || !r.due.trim() || !(r.effort_mins > 0)) {
      return "Every task needs a title, an owner, an effort and a due date.";
    }
    if (r.start && r.due && r.start > r.due) return "A task starts after its due date.";
  }
  return "";
}

/** What the card sends back. The server's fields never travel. */
export function planSubmit(
  project: Record<string, unknown>,
  name: string,
  rows: PlanRow[],
): { project: Record<string, unknown>; tasks: Array<Record<string, unknown>> } {
  const kept = new Set(rows.map((r) => r.key));
  return {
    project: { ...project, name: name.trim() },
    tasks: rows.map((r) => ({
      key: r.key,
      title: r.title.trim(),
      owner: r.owner.trim(),
      effort_mins: r.effort_mins,
      start: r.start ? r.start.slice(0, 10) : null,
      due: r.due.slice(0, 10),
      after: r.after.filter((k) => kept.has(k)),
      importance: r.importance,
      impact: r.impact,
      urgency: r.urgency,
      effort: r.effort,
    })),
  };
}

/** The `after` cell: the TITLES of the rows that block this one. */
export function afterLabel(row: PlanRow, rows: PlanRow[]): string {
  const title = new Map(rows.map((r) => [r.key, r.title]));
  return row.after
    .filter((k) => title.has(k))
    .map((k) => title.get(k) || k)
    .join(", ");
}
