/**
 * The plan card's decisions, pure (WS-27bm S7d, spec §13.6).
 *
 * `genUITemplates.tsx` `PlanCard` draws what this module decides, so the
 * decisions are testable in the node-env suite where the card cannot render.
 *
 * Five rules live here:
 *
 * 1. **The editable fields are a closed list** (`PLAN_EDIT_COLS`, and the
 *    `after` keys). The fit, the hours and the marks come from the server's
 *    preview and are READ-ONLY. The member changes an owner, a date or what a
 *    row waits on, and the server marks the plan again after the submit
 *    (§13.6 rule 3).
 * 2. **The submit carries what round-trips** (`planSubmit`): the key, the
 *    start date, the `after` keys and the three scores. So the sort score
 *    after the submit is the score the card showed (§13.6 rule 10). It never
 *    carries the fit, the hours or the marks, because those are the server's.
 * 3. **A dropped row drops its links** (§13.6 rule 6). `planSubmit` strips an
 *    `after` key that no remaining row carries. The skill lists the dropped
 *    links on the confirm card.
 * 4. **A mark warns and never blocks** (§13.6 rule 3). `planIncomplete` reads
 *    the three required fields and a start after the due date, and no mark.
 *    The owner is NOT required (owner, 2026-10-17, spec §26). A row with no
 *    owner is an unassigned task: it shows no fit, no hours and no mark
 *    (`planFacts`, `isMarked`), and `ownerSummary` counts it apart.
 * 5. **The card cannot make a cycle** (WS-27bm S10, §16.2 rule 6).
 *    `afterOptions` offers a row every other row except the rows that wait on
 *    it, directly or through other rows. The server still refuses a cycle and
 *    a self-block, so this rule is a convenience and the server is the fence.
 *
 * There are no phase TASKS (owner, 2026-09-24). Since the redesign of
 * 2026-10-18, a row may carry a `phase` label. The card groups the rows
 * under it and folds each group. The label is display only: the submit never
 * sends it, and nothing stores it (`planGroups`).
 *
 * The redesign (owner, 2026-10-18) adds the read-first view. Each row is one
 * line of text, and a click opens one row for editing. The rest of this file
 * from "The read-first view" down holds its decisions: the effort in hours
 * and days, the summary line, the timeline strip, the badges, the phases,
 * the bulk edits and the ticked rows. The submit is still `planSubmit`, over
 * the ticked rows only, so a row the member unticks leaves the payload the
 * way a row the old card dropped did (golden test in `planCard.test.ts`).
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
  /** The priority level's two stated inputs (D78). `null` means the row
   *  does not state it. The card keeps both and sends both back (H-173). */
  important: boolean | null;
  leveraged: boolean | null;
  impact: number;
  urgency: number;
  effort: number;
  priority: number | null;
  /** Read-only, from the server's preview. Absent without the HR grant. */
  fit?: string;
  hours?: string;
  marks: string[];
  warnings: string[];
  /** The group the card draws the row under, or "". Display only: the
   *  submit never sends it (owner, 2026-10-18). */
  phase: string;
}

/** The columns the member may edit, in order. Nothing else is an input. */
export const PLAN_EDIT_COLS: ReadonlyArray<{ key: keyof PlanRow; label: string; type: PlanCell }> = [
  { key: "title", label: "Task", type: "text" },
  { key: "owner", label: "Owner", type: "text" },
  { key: "effort_mins", label: "Effort", type: "number" },
  { key: "start", label: "Start", type: "date" },
  { key: "due", label: "Due", type: "date" },
];

/** The fields the server owns. The card shows them and never edits them.
 *  `after` left this list in WS-27bm S10: the "Waits on" control edits it. */
export const PLAN_READ_ONLY: ReadonlyArray<keyof PlanRow> = ["fit", "hours", "marks", "warnings"];

const text = (v: unknown): string => (typeof v === "string" ? v : v == null ? "" : String(v));
const whole = (v: unknown, f: number): number => {
  const n = typeof v === "number" ? v : parseFloat(String(v));
  return Number.isFinite(n) ? Math.round(n) : f;
};
const bool = (v: unknown): boolean | null =>
  v === true || v === "true" ? true : v === false || v === "false" ? false : null;
const score = (v: unknown): number => Math.max(1, Math.min(5, whole(v, 3)));
const strings = (v: unknown): string[] =>
  Array.isArray(v) ? v.map((x) => text(x).trim()).filter(Boolean) : [];

/** One row from the skill's `tasks` array, with every field defined. */
export function planRowFrom(raw: unknown, index: number): PlanRow {
  const r = (raw ?? {}) as Record<string, unknown>;
  const row: PlanRow = {
    key: text(r.key).trim() || `t${index + 1}`,
    title: text(r.title),
    owner: text(r.owner),
    effort_mins: whole(r.effort_mins, 0),
    start: text(r.start).slice(0, 10),
    due: text(r.due).slice(0, 10),
    after: strings(r.after),
    important: bool(r.important),
    leveraged: bool(r.leveraged),
    impact: score(r.impact),
    urgency: score(r.urgency),
    effort: score(r.effort),
    priority: r.priority == null ? null : whole(r.priority, 0),
    marks: strings(r.marks),
    warnings: strings(r.warnings),
    phase: text(r.phase).trim().slice(0, 80),
  };
  if (r.fit != null) row.fit = text(r.fit);
  if (r.hours != null) row.hours = text(r.hours);
  return row;
}

export function planRowsFrom(tasks: unknown): PlanRow[] {
  return (Array.isArray(tasks) ? tasks : []).map((t, i) => planRowFrom(t, i));
}

/** Does this row have no owner? Then it is an unassigned task (§26). */
export function isUnassigned(row: PlanRow): boolean {
  return !row.owner.trim();
}

/** What an empty owner field shows. The member can type an owner over it. */
export const UNASSIGNED_LABEL = "Unassigned";

/** Is this row marked? A marked row still submits (rule 3). A mark is about
 *  an owner, so a row with no owner carries none, even a stale one from
 *  the owner the member cleared (§26). */
export function isMarked(row: PlanRow): boolean {
  return !isUnassigned(row) && row.marks.length > 0;
}

/**
 * The read-only line under a row: what it waits on, then the fit and the
 * hours. An unassigned row shows no fit and no hours, because nobody is
 * there to measure (§26). A fit for an owner the member cleared is stale,
 * so it does not show either.
 */
export function planFacts(row: PlanRow, rows: PlanRow[]): string[] {
  const after = afterLabel(row, rows);
  const owned = !isUnassigned(row);
  return [
    after ? `After: ${after}` : "",
    owned && row.fit != null ? `Fit: ${row.fit}` : "",
    owned && row.hours != null ? `Hours: ${row.hours}` : "",
  ].filter(Boolean);
}

/**
 * The line that counts the owners of the plan, or "" when every task has
 * one. The unassigned tasks are a count of their own, and the line follows
 * the member's edits. The skill's capacity line on the plan card leaves the
 * count to this line, so the card says it once. Nothing here divides, so a
 * plan with no owner at all is safe (§26).
 */
export function ownerSummary(rows: PlanRow[]): string {
  const nobody = rows.filter(isUnassigned).length;
  if (nobody === 0) return "";
  if (nobody === rows.length) {
    return nobody === 1
      ? "The task is unassigned, so it shows no fit or hours."
      : `All ${nobody} tasks are unassigned, so no row shows a fit or hours.`;
  }
  const said = nobody === 1 ? "is unassigned, so it shows" : "are unassigned, so they show";
  return `${nobody} of ${rows.length} tasks ${said} no fit or hours.`;
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

/** Why the submit is off, or "" when the plan can go. Marks never block,
 *  and an empty owner never blocks (§26). */
export function planIncomplete(name: string, rows: PlanRow[]): string {
  if (!name.trim()) return "The project needs a name.";
  if (rows.length === 0) return "The plan needs at least one task.";
  for (const r of rows) {
    if (!r.title.trim() || !r.due.trim() || !(r.effort_mins > 0)) {
      return "Every task needs a title, an effort and a due date.";
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
      important: r.important,
      leveraged: r.leveraged,
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

/** Every row that waits on `row`, directly or through other rows. */
function waitersOf(row: PlanRow, rows: PlanRow[]): Set<string> {
  const found = new Set<string>();
  const queue = [row.key];
  while (queue.length > 0) {
    const key = queue.shift() as string;
    for (const r of rows) {
      if (r.after.includes(key) && !found.has(r.key)) {
        found.add(r.key);
        queue.push(r.key);
      }
    }
  }
  return found;
}

/**
 * The rows `row` may wait on: every other row, except a row that already
 * waits on `row`. A tick on one of those would make a cycle (rule 5).
 */
export function afterOptions(row: PlanRow, rows: PlanRow[]): PlanRow[] {
  const waiters = waitersOf(row, rows);
  return rows.filter((r) => r.key !== row.key && !waiters.has(r.key));
}

/** `row.after` with `key` ticked or cleared, in the order the rows show. */
export function withAfter(row: PlanRow, rows: PlanRow[], key: string, on: boolean): string[] {
  const next = new Set(row.after.filter((k) => k !== key));
  if (on) next.add(key);
  const order = rows.map((r) => r.key);
  return [...next].sort((a, b) => order.indexOf(a) - order.indexOf(b));
}

/** How many rows `row` waits on. A key of a dropped row does not count. */
export function afterCount(row: PlanRow, rows: PlanRow[]): number {
  const keys = new Set(rows.map((r) => r.key));
  return row.after.filter((k) => keys.has(k)).length;
}

/** The "Waits on" control's accessible name. The visible label stays
 *  "Waits on", so the name starts with it (WCAG 2.5.3). */
export function waitsOnName(row: PlanRow): string {
  return `Waits on (for ${row.title.trim() || "Untitled task"})`;
}

// ── The read-first view (owner, 2026-10-18) ─────────────────────────────────
//
// The owner said the card "feels intimidating and the UI looks poor", and
// approved nine changes. Each row is one line of text, and a click opens one
// row for editing. The decisions below are pure, so the node-env suite can
// test them where the card cannot render.

/**
 * A working day, in minutes. The app's one convention: `ESTIMATE_PRESETS`
 * in `TaskBody.tsx` ends on 480, "a working day" (D77).
 */
export const WORKDAY_MINS = 480;

/** The unit an effort shows in, and edits in. */
export type EffortUnit = "m" | "h" | "d";

export const EFFORT_UNITS: ReadonlyArray<{ unit: EffortUnit; label: string; mins: number }> = [
  { unit: "m", label: "min", mins: 1 },
  { unit: "h", label: "hours", mins: 60 },
  { unit: "d", label: "days", mins: WORKDAY_MINS },
];

/** Days when the effort is whole or half working days, and one day or
 *  more. Hours when it is a whole or half hour. Minutes under an hour, or
 *  when neither unit says it exactly. */
export function effortUnit(mins: number): EffortUnit {
  if (mins >= WORKDAY_MINS && mins % (WORKDAY_MINS / 2) === 0) return "d";
  if (mins >= 60 && mins % 30 === 0) return "h";
  return "m";
}

/** The effort in a unit, as the editor shows it: at most two decimals. */
export function effortIn(mins: number, unit: EffortUnit): number {
  const per = EFFORT_UNITS.find((u) => u.unit === unit)?.mins ?? 1;
  return Math.round((mins / per) * 100) / 100;
}

/** A value in a unit, back to whole minutes, which is what the submit sends. */
export function effortMins(value: number, unit: EffortUnit): number {
  const per = EFFORT_UNITS.find((u) => u.unit === unit)?.mins ?? 1;
  return Number.isFinite(value) ? Math.max(0, Math.round(value * per)) : 0;
}

/** `360` → "6h", `480` → "1d", `720` → "1.5d", `90` → "1h 30m", `45` → "45m". */
export function effortLabel(mins: number): string {
  if (!(mins > 0)) return "0m";
  if (effortUnit(mins) === "d") return `${effortIn(mins, "d")}d`;
  if (mins < 60) return `${mins}m`;
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  return m ? `${h}h ${m}m` : `${h}h`;
}

const MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(" ");

/**
 * "22 Oct", or "22 Oct 2027" for a date outside `year`. Day first, the
 * app's date order (`formatCardDate`), so a reader in India and a reader in
 * the US read the same day. Parsed by hand, never through `new Date()`: a
 * floating date at midnight UTC names the wrong day west of Greenwich.
 */
export function planDay(iso: string, year: number): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!m) return iso;
  const month = MONTHS[Number(m[2]) - 1];
  if (!month) return iso;
  const day = `${Number(m[3])} ${month}`;
  return Number(m[1]) === year ? day : `${day} ${m[1]}`;
}

/** Days since 1970 for a YYYY-MM-DD, or NaN. Calendar arithmetic only. */
function dayNumber(iso: string): number {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  return m ? Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])) / 86_400_000 : NaN;
}

function isoOf(day: number): string {
  return new Date(day * 86_400_000).toISOString().slice(0, 10);
}

/** A row's start, or its due date when it states no start. */
export function rowStart(row: PlanRow): string {
  return row.start || row.due;
}

/** "22 Oct → 24 Oct", or one day when the row starts and ends on it. */
export function rowSpan(row: PlanRow, year: number): string {
  const from = rowStart(row);
  if (!from) return "";
  if (!row.due || from === row.due) return planDay(from, year);
  return `${planDay(from, year)} → ${planDay(row.due, year)}`;
}

/** A short owner for the chip: the name part of an address, in words.
 *  `priya.sharma@x.io` → "Priya Sharma". The whole owner is the chip's tip. */
export function ownerLabel(owner: string): string {
  const raw = owner.trim();
  if (!raw.includes("@")) return raw;
  return raw
    .split("@")[0]
    .split(/[._-]+/)
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

/** The rows the member left ticked, in the card's order. */
export function keptRows(rows: PlanRow[], off: ReadonlySet<string>): PlanRow[] {
  return rows.filter((r) => !off.has(r.key));
}

/** What the card sends back: `planSubmit` over the ticked rows only. An
 *  unticked row leaves the payload as a dropped row did (rule 3). */
export function planSubmitTicked(
  project: Record<string, unknown>,
  name: string,
  rows: PlanRow[],
  off: ReadonlySet<string>,
): ReturnType<typeof planSubmit> {
  return planSubmit(project, name, keptRows(rows, off));
}

/** The footer button: "Create 7 tasks". It counts the ticked rows. */
export function createLabel(ticked: number): string {
  return ticked === 1 ? "Create 1 task" : `Create ${ticked} tasks`;
}

/** The line under the footer button. One approval follows, and it creates
 *  every ticked task (§26.2). Nothing is made before it. */
export function footerNote(ticked: number): string {
  if (ticked === 0) return "Tick at least one task to create it.";
  const what = ticked === 1 ? "the task" : ticked === 2 ? "both tasks" : `all ${ticked} tasks`;
  return `Next, one approval creates ${what}. Nothing is created before it. A badge warns and never blocks.`;
}

export interface PlanSummary {
  tasks: number;
  owners: number;
  /** The first start and the last due date, or "" with no dated row. */
  from: string;
  to: string;
  mins: number;
}

/** The facts of the summary line, over the ticked rows. */
export function planSummary(rows: PlanRow[]): PlanSummary {
  const owners = new Set(rows.filter((r) => !isUnassigned(r)).map((r) => r.owner.trim().toLowerCase()));
  const starts = rows.map(rowStart).filter(Boolean).sort();
  const dues = rows.map((r) => r.due).filter(Boolean).sort();
  return {
    tasks: rows.length,
    owners: owners.size,
    from: starts[0] ?? "",
    to: dues[dues.length - 1] ?? "",
    mins: rows.reduce((n, r) => n + (r.effort_mins > 0 ? r.effort_mins : 0), 0),
  };
}

/** The summary line's parts after the name: "8 tasks", "3 owners",
 *  "22 Oct → 15 Nov", "46h". */
export function summaryParts(s: PlanSummary, year: number): string[] {
  const span = !s.from || !s.to ? ""
    : s.from === s.to ? planDay(s.from, year)
    : `${planDay(s.from, year)} → ${planDay(s.to, year)}`;
  return [
    s.tasks === 1 ? "1 task" : `${s.tasks} tasks`,
    s.owners === 0 ? "no owners" : s.owners === 1 ? "1 owner" : `${s.owners} owners`,
    span,
    s.mins > 0 ? effortLabel(s.mins) : "",
  ].filter(Boolean);
}

export interface TimelineBar {
  key: string;
  title: string;
  /** Where the bar starts and how wide it is, as shares of the plan's span. */
  left: number;
  width: number;
  marked: boolean;
  unassigned: boolean;
}

export interface PlanTimeline {
  from: string;
  to: string;
  bars: TimelineBar[];
}

/** The strip under the summary: one bar per ticked row, from its start to
 *  its due date, on the plan's own span. `null` with no dated row. */
export function planTimeline(rows: PlanRow[]): PlanTimeline | null {
  const dated = rows.filter((r) => Number.isFinite(dayNumber(r.due)));
  if (dated.length === 0) return null;
  const startOf = (r: PlanRow) => {
    const s = dayNumber(rowStart(r));
    const d = dayNumber(r.due);
    return Number.isFinite(s) && s <= d ? s : d;
  };
  const first = Math.min(...dated.map(startOf));
  const last = Math.max(...dated.map((r) => dayNumber(r.due)));
  const days = last - first + 1;
  return {
    from: isoOf(first),
    to: isoOf(last),
    bars: dated.map((r) => {
      const s = startOf(r);
      return {
        key: r.key,
        title: r.title,
        left: (s - first) / days,
        width: (dayNumber(r.due) - s + 1) / days,
        marked: isMarked(r),
        unassigned: isUnassigned(r),
      };
    }),
  };
}

export type PlanBadgeTone = "warning" | "neutral";

export interface PlanBadge {
  label: string;
  tone: PlanBadgeTone;
}

/** The badge words for a server mark. A mark the map does not know shows
 *  as itself, so no mark is ever lost. */
export const MARK_BADGES: Readonly<Record<string, string>> = {
  "short of hours": "Overloaded",
  "no skill match": "Skill gap",
  "not in the directory": "Not in directory",
  "owner not resolved": "Unknown owner",
};

/** The small warning badges on a row's line. The full words of each mark
 *  show when the row opens (`markLine`). A row with no owner gets none: its
 *  owner chip already reads "Unassigned", in the neutral tone, because no
 *  owner is a fact and not a warning (§26). A stale mark of an owner the
 *  member cleared does not show either (`isMarked`). */
export function rowBadges(row: PlanRow): PlanBadge[] {
  if (!isMarked(row)) return [];
  const seen = new Set<string>();
  const out: PlanBadge[] = [];
  for (const mark of row.marks) {
    const label = MARK_BADGES[mark] ?? mark.charAt(0).toUpperCase() + mark.slice(1);
    if (!seen.has(label)) {
      seen.add(label);
      out.push({ label, tone: "warning" });
    }
  }
  return out;
}

export interface PlanGroup {
  /** The group's identity. A phase that the model names "Other tasks" is
   *  still its own group: the rows with no phase use a sentinel key. */
  key: string;
  /** The label the fold shows. */
  phase: string;
  rows: PlanRow[];
}

/** The label of the group for the rows that carry no phase, in a plan
 *  where other rows carry one. */
export const NO_PHASE = "Other tasks";
/** The key of that group. A phase key always starts with "phase:", so no
 *  phase label can reach it. */
export const NO_PHASE_KEY = "no-phase";

/**
 * The rows under their phases, in the order each phase first shows, or
 * `null` when no row carries a phase. Then the card draws one flat list.
 */
export function planGroups(rows: PlanRow[]): PlanGroup[] | null {
  if (!rows.some((r) => r.phase)) return null;
  const groups = new Map<string, PlanGroup>();
  for (const r of rows) {
    const key = r.phase ? `phase:${r.phase}` : NO_PHASE_KEY;
    const g = groups.get(key) ?? { key, phase: r.phase || NO_PHASE, rows: [] };
    g.rows.push(r);
    groups.set(key, g);
  }
  const out = [...groups.values()];
  // The rows with no phase go last, whatever their place.
  return [...out.filter((g) => g.key !== NO_PHASE_KEY), ...out.filter((g) => g.key === NO_PHASE_KEY)];
}

/** What a ticked row lacks before the plan can go, in two or three words,
 *  or "". The same checks as `planIncomplete`, named on the row itself. */
export function rowProblem(row: PlanRow): string {
  if (!row.title.trim()) return "Needs a title";
  if (!(row.effort_mins > 0)) return "Needs an effort";
  if (!row.due.trim()) return "Needs a due date";
  if (row.start && row.start > row.due) return "Starts after due";
  return "";
}

/** A YYYY-MM-DD moved by whole days. An empty date stays empty. */
export function shiftDay(iso: string, days: number): string {
  if (!iso) return iso;
  const n = dayNumber(iso);
  return Number.isFinite(n) ? isoOf(n + days) : iso;
}

/** Bulk: one owner for every selected row. An empty owner unassigns them. */
export function withOwner(rows: PlanRow[], keys: ReadonlySet<string>, owner: string): PlanRow[] {
  return rows.map((r) => (keys.has(r.key) ? { ...r, owner } : r));
}

/** Bulk: move the start and the due date of every selected row together. */
export function withShift(rows: PlanRow[], keys: ReadonlySet<string>, days: number): PlanRow[] {
  // Whole days only. A half day has no date, and rounding moved +1.5 by 2
  // but -1.5 by 1 (review of #1136).
  if (!Number.isInteger(days) || days === 0) return rows;
  return rows.map((r) => (keys.has(r.key) ? { ...r, start: shiftDay(r.start, days), due: shiftDay(r.due, days) } : r));
}

/** The bulk bar's day field, as whole days, or `null` when it holds
 *  anything else ("1.5", "", "2x"). The Move button stays off on `null`. */
export function wholeDays(text: string): number | null {
  const t = text.trim();
  return /^-?\d+$/.test(t) ? Number(t) : null;
}

/** The owners already on the plan, for the owner field's suggestions. */
export function planOwners(rows: PlanRow[]): string[] {
  const seen = new Map<string, string>();
  for (const r of rows) {
    const o = r.owner.trim();
    if (o && !seen.has(o.toLowerCase())) seen.set(o.toLowerCase(), o);
  }
  return [...seen.values()];
}

/** A set with `key` in or out. */
export function toggled(set: ReadonlySet<string>, key: string, on: boolean): Set<string> {
  const next = new Set(set);
  if (on) next.add(key);
  else next.delete(key);
  return next;
}

/** The answer Cancel sends. It is no submit (`forms._answer` reads only
 *  "<label> — <json>"), so the tool says "not submitted" and writes nothing. */
export const PLAN_CANCEL = "Cancel";

/**
 * The rows `row` may wait on, as the card offers them (review of #1136).
 * The cycle exclusion reads ALL the rows, ticked or not: an unticked row
 * still carries its links, and comes back with them when it is ticked
 * again. Only the ticked rows are offered as choices.
 */
export function afterChoices(row: PlanRow, rows: PlanRow[], kept: PlanRow[]): PlanRow[] {
  const allowed = new Set(afterOptions(row, rows).map((r) => r.key));
  return kept.filter((r) => allowed.has(r.key));
}

/**
 * The ticked rows that sit on a circle of `after` links, or an empty set.
 * Kahn's sort, as `forms._block_cycle` does: what cannot be removed is on a
 * circle. The server refuses a circle and the member loses the edits, so
 * the card names it on the row and holds the submit.
 */
export function planCycle(kept: PlanRow[]): Set<string> {
  const keys = new Set(kept.map((r) => r.key));
  const waiting = new Map(kept.map((r) => [r.key, new Set(r.after.filter((k) => keys.has(k)))]));
  const ready = [...waiting].filter(([, b]) => b.size === 0).map(([k]) => k);
  while (ready.length) {
    const done = ready.pop() as string;
    waiting.delete(done);
    for (const [k, before] of waiting) {
      if (before.delete(done) && before.size === 0) ready.push(k);
    }
  }
  return new Set(waiting.keys());
}

/** The words of a ticked row on a circle. */
export const CYCLE_BADGE = "Waits in a circle";

/** Why the submit is off, or "": `planIncomplete`, then a circle. */
export function planBlocked(name: string, kept: PlanRow[]): string {
  const missing = planIncomplete(name, kept);
  if (missing) return missing;
  return planCycle(kept).size ? "Some tasks wait on each other in a circle. Fix Waits on." : "";
}

/** "Priority 12 · a sorting aid", or "" when the row has no score. The
 *  open row shows it, so the line at rest stays short (review of #1136). */
export function priorityLine(row: PlanRow): string {
  return row.priority == null ? "" : `Priority ${row.priority} · a sorting aid`;
}

export interface AnsweredPlan {
  done: "sent" | "cancelled";
  name: string;
  rows: PlanRow[];
  off: Set<string>;
}

/**
 * The card as the member left it, from the answer the run already got
 * (`lib/askAnswers.ts`). A reload draws an answered card locked, as the
 * option picker does. A submit names the rows it sent, so the rows it left
 * out draw unticked. Each sent row keeps the card's own read-only fields
 * (the fit, the marks, the phase) by its key. `null` with no answer.
 */
export function answeredPlan(name: string, rows: PlanRow[], answered: string): AnsweredPlan | null {
  if (!answered) return null;
  if (answered === PLAN_CANCEL || !answered.includes(" — ")) return { done: "cancelled", name, rows, off: new Set() };
  let sent: { project?: { name?: unknown }; tasks?: unknown };
  try {
    sent = JSON.parse(answered.slice(answered.indexOf(" — ") + 3));
  } catch {
    return { done: "sent", name, rows, off: new Set() };
  }
  const got = planRowsFrom(sent.tasks);
  const byKey = new Map(got.map((r) => [r.key, r]));
  const merged = rows.map((r) => {
    const g = byKey.get(r.key);
    return g ? { ...r, title: g.title, owner: g.owner, effort_mins: g.effort_mins, start: g.start, due: g.due, after: g.after } : r;
  });
  const known = new Set(rows.map((r) => r.key));
  return {
    done: "sent",
    name: typeof sent.project?.name === "string" ? sent.project.name : name,
    rows: [...merged, ...got.filter((r) => !known.has(r.key))],
    off: new Set(rows.filter((r) => !byKey.has(r.key)).map((r) => r.key)),
  };
}
