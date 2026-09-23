/**
 * People Center · the people-management dashboard, the pure half (WS-28j1).
 *
 * Spec: `project-docs/specs/people_center_app.md` §5.7.1, §5.7.2 · **D-PC-14**.
 *
 * **No arithmetic lives here.** The pills, the reasons, the hours and the
 * at-risk list are all the server's — computed once in `gateway/workload.py` so
 * that the department rollup (j2), the rebalancing suggester (j3) and the
 * Center landing rollup (§5.9) read the same numbers. A second computation in
 * the browser would be a second answer to "is this person overloaded", and the
 * two would disagree the first time either changed.
 *
 * What is here is presentation: which hue a pill wears, how a bar is sized, how
 * a date reads, and the sort order that puts what needs a decision at the top.
 *
 * ⚠️ **This surface never ranks people** (D-PC-14). `sortRows` orders by the
 * URGENCY OF THE WORK — behind first, then at risk — which is a queue of
 * conversations to have, not a league table. There is no score, no percentile,
 * and no per-person trend line: every figure here is trivially gamed and
 * trivially misread, and the distinction that makes the owner's ask compatible
 * with the refusal is that ranking TASKS by risk is the product while ranking
 * PEOPLE by output is not.
 */

import { type AccentHue } from "@/lib/statusAccent";

export type Pill = "behind" | "at_risk" | "overloaded" | "idle" | "on_track";

export interface AtRiskTask {
  task_id: string;
  title: string;
  project_name?: string | null;
  due_on: string;
  own_hours: number;
  needed_hours: number;
  available_hours: number;
  shortfall_hours: number;
}

export interface DashboardRow {
  person_id: string | null;
  name: string;
  email?: string | null;
  department?: string | null;
  team?: string | null;
  avatar?: string | null;
  /** `person` or `agent`. Agents hold tasks the same way (D-PM-4). */
  kind: "person" | "agent";

  open_tasks: number;
  overdue: number;
  unestimated: number;
  committed_hours: number;
  committed_this_week: number;
  contracted_hours: number;
  hours_available_this_week?: number | null;
  spare_hours_this_week?: number | null;
  next_due_at?: string | null;
  last_activity_at?: string | null;
  projects: Array<{ id: string; name: string }>;
  projects_total: number;
  at_risk: AtRiskTask[];
  away?: { kind: string; until: string } | null;

  /** An absence covering any day from today to Sunday — wider than `away`. */
  away_this_week: boolean;

  /** `null` for an agent, by design — see §5.7.5. */
  pill: Pill | null;
  reason?: string | null;
  flags: Pill[];
  hours_basis: boolean;
  note?: string | null;
}

/** One end of a department's spread — always hours, with the percent beside. */
export interface SpreadEnd {
  person_id: string | null;
  name: string;
  committed_hours: number;
  contracted_hours: number;
  percent: number | null;
}

export interface Rollup {
  department: string;
  headcount: number;
  contracted_hours: number;
  committed_hours: number;
  pills: Record<Pill, number>;
  /** Named, not counted — "two people are away" does not answer the question. */
  away: string[];
  no_open_work: string[];
  unestimated_people: number;
  needs_attention: number;
  /** The share of the group with something to act on, 0–1. */
  strain: number;
  spread: {
    gap_hours: number;
    most: SpreadEnd;
    least: SpreadEnd;
  } | null;
  /** Org row only. */
  departments?: number;
  agents?: number;
}

export interface DashboardResponse {
  rows: DashboardRow[];
  total: number;
  partial: boolean;
  work_visible: boolean;
  /** WS-28j2 — the server's projection of `rows`, never a second count. */
  departments: Rollup[];
  org: Rollup;
  can_manage: boolean;
  self_person_id?: string | null;
  horizon_days: number;
  idle_fraction: number;
  pills: Pill[];
}

/**
 * Pill → hue, through the SHARED vocabulary.
 *
 * `src/lib/statusAccent.ts` is the one place a status becomes a colour in this
 * product (AGENTS.md rule 4), so these are hue NAMES resolved there rather than
 * class strings written here. A palette local to this page is exactly the thing
 * that looks fine until somebody switches the theme.
 */
export const PILL_HUE: Record<Pill, AccentHue> = {
  behind: "red",
  at_risk: "amber",
  overloaded: "violet",
  idle: "blue",
  on_track: "green",
};

export const PILL_LABEL: Record<Pill, string> = {
  behind: "Behind",
  at_risk: "At risk",
  overloaded: "Overloaded",
  idle: "Idle",
  on_track: "On track",
};

/** Sort weight — lower comes first. Unpilled rows (agents) sink to the end. */
const PILL_ORDER: Record<Pill, number> = {
  behind: 0,
  at_risk: 1,
  overloaded: 2,
  idle: 3,
  on_track: 4,
};

/**
 * The order the rows are read in: **what needs a decision, first.**
 *
 * Within a pill, by how much of it there is — most overdue tasks first among
 * the behind, largest shortfall first among the at-risk. That is still an
 * ordering of WORK: two people wearing the same pill are separated by the size
 * of the problem in front of them, never by an assessment of them.
 *
 * Alphabetical within everything else, so the bottom of the list is stable and
 * boring — which is the correct treatment for rows nobody has to act on.
 */
export function sortRows(rows: readonly DashboardRow[]): DashboardRow[] {
  return [...rows].sort((a, b) => {
    const rank =
      (a.pill ? PILL_ORDER[a.pill] : 9) - (b.pill ? PILL_ORDER[b.pill] : 9);
    if (rank !== 0) return rank;
    if (a.pill === "behind" && a.overdue !== b.overdue) {
      return b.overdue - a.overdue;
    }
    if (a.pill === "at_risk") {
      const gap = worstShortfall(b) - worstShortfall(a);
      if (gap !== 0) return gap;
    }
    return a.name.localeCompare(b.name);
  });
}

export function worstShortfall(row: DashboardRow): number {
  return row.at_risk.reduce((worst, t) => Math.max(worst, t.shortfall_hours), 0);
}

/** Group for the section headers, the same shape the directory uses. */
export const NO_DEPARTMENT = "Unassigned";

export function groupByDepartment(
  rows: readonly DashboardRow[]
): Array<{ department: string; rows: DashboardRow[] }> {
  const groups = new Map<string, DashboardRow[]>();
  for (const row of rows) {
    const key = row.department?.trim() || NO_DEPARTMENT;
    const bucket = groups.get(key);
    if (bucket) bucket.push(row);
    else groups.set(key, [row]);
  }
  return [...groups.entries()]
    .map(([department, list]) => ({ department, rows: list }))
    .sort((a, b) => {
      // Unassigned trails, however it sorts alphabetically.
      if (a.department === NO_DEPARTMENT) return 1;
      if (b.department === NO_DEPARTMENT) return -1;
      return a.department.localeCompare(b.department);
    });
}

export interface CapacityBar {
  /** 0-100, clamped. */
  percent: number;
  label: string;
  /** True when the figure rests on nothing and must not be drawn as a number. */
  unknown: boolean;
}

/**
 * Committed against contracted, for the week.
 *
 * Refuses to draw a percentage where nothing is estimated — the same call
 * `loadBar` already made on the person page, for the same reason: a row of
 * thirty un-estimated tasks sums to zero hours, and a confident 0% bar is how
 * somebody gets handed a thirty-first.
 */
export function capacityBar(row: DashboardRow): CapacityBar {
  const tail = row.unestimated > 0 ? ` · ${row.unestimated} with no estimate` : "";
  if (!row.hours_basis || row.contracted_hours <= 0) {
    return {
      percent: 0,
      unknown: true,
      label:
        row.open_tasks === 0
          ? "Nothing assigned"
          : `${row.open_tasks} open${tail || ", none estimated"}`,
    };
  }
  return {
    percent: Math.min(
      100,
      Math.round((row.committed_this_week / row.contracted_hours) * 100)
    ),
    unknown: false,
    label: `${hours(row.committed_this_week)} of ${hours(
      row.contracted_hours
    )} this week${tail}`,
  };
}

/** `40` → `"40h"`, `37.5` → `"37.5h"`. Re-exported shape of `schedule.ts`'s. */
export function hours(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return Number.isInteger(value) ? `${value}h` : `${Number(value.toFixed(1))}h`;
}

/**
 * A deadline, as a person reads one: "overdue by 3 days", "due tomorrow".
 *
 * ⚠️ Rendered through `toLocaleDateString` for anything further out, so it
 * reads in the VIEWER's locale — "10 Aug" here, "Aug 10" there. Pinning a
 * locale to make an assertion tidy would make the product wrong to make the
 * test simple, so the tests assert by parts (the call `absence.ts` already
 * made).
 */
export function describeDeadline(
  iso: string | null | undefined,
  today = new Date()
): string {
  if (!iso) return "No deadline";
  const when = new Date(iso);
  if (Number.isNaN(when.getTime())) return iso;
  const days = daysBetween(today, when);
  if (days < -1) return `Overdue by ${Math.abs(days)} days`;
  if (days === -1) return "Overdue by a day";
  if (days === 0) return "Due today";
  if (days === 1) return "Due tomorrow";
  if (days <= 7) return `Due in ${days} days`;
  return `Due ${when.toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
  })}`;
}

/**
 * How long since they last did anything — the difference between "quiet
 * because nothing is due" and "quiet because nothing is happening".
 *
 * `null` reads as "No recent activity", never as "0 days ago": an absent
 * timestamp and a fresh one must not render alike.
 */
export function describeActivity(
  iso: string | null | undefined,
  today = new Date()
): string {
  if (!iso) return "No recent activity";
  const when = new Date(iso);
  if (Number.isNaN(when.getTime())) return iso;
  const days = -daysBetween(today, when);
  if (days <= 0) return "Active today";
  if (days === 1) return "Active yesterday";
  if (days < 14) return `Active ${days} days ago`;
  return `Active ${Math.floor(days / 7)} weeks ago`;
}

/** Whole days from `from` to `to`, both truncated to their date. */
export function daysBetween(from: Date, to: Date): number {
  const a = Date.UTC(from.getFullYear(), from.getMonth(), from.getDate());
  const b = Date.UTC(to.getFullYear(), to.getMonth(), to.getDate());
  return Math.round((b - a) / 86_400_000);
}

/** The counts across the visible rows — the strip above the table. */
export function pillTotals(rows: readonly DashboardRow[]): Record<Pill, number> {
  const totals: Record<Pill, number> = {
    behind: 0,
    at_risk: 0,
    overloaded: 0,
    idle: 0,
    on_track: 0,
  };
  for (const row of rows) {
    if (row.pill) totals[row.pill] += 1;
  }
  return totals;
}

/**
 * The one line a department's row has to earn (§5.7.3).
 *
 * ⚠️ It leads with **what to act on**, not with headcount. "12 people" is true
 * and useless; "3 of 12 need attention" is where to look. And it is a statement
 * about work in every clause — the rollup orders departments by strain because
 * a rollup nobody can act on is a table, not because departments are being
 * graded (D-PC-14).
 */
export function describeRollup(group: Rollup): string {
  const parts: string[] = [];
  if (group.needs_attention > 0) {
    parts.push(
      `${group.needs_attention} of ${group.headcount} need attention`
    );
  } else {
    parts.push(`${group.headcount} ${group.headcount === 1 ? "person" : "people"}, nothing overdue`);
  }
  parts.push(
    `${hours(group.committed_hours)} due against ${hours(
      group.contracted_hours
    )}`
  );
  if (group.no_open_work.length > 0) {
    parts.push(
      `${group.no_open_work.length} with no open work`
    );
  }
  if (group.away.length > 0) {
    parts.push(`${group.away.join(", ")} away this week`);
  }
  if (group.unestimated_people > 0) {
    // The caveat travels with the total for the same reason `unestimated`
    // travels with a person's hours: a sum over rows that are half unestimated
    // is a confident number built on missing data.
    parts.push(
      `${group.unestimated_people} with nothing estimated`
    );
  }
  return parts.join(" · ");
}

/**
 * The spread, as the sentence that starts the conversation (§5.7.3).
 *
 * ⚠️ Both people are named and both figures are in HOURS. That is the whole
 * point — "Priya has 46h due, Ravi has 6h" is arguable and actionable, where a
 * bare percentage gap is a score with two names attached. `null` where the
 * spread means nothing rather than a reassuring "0h".
 */
/**
 * "a" or "an", decided by how the number is SAID, not how it is spelt.
 *
 * ⚠️ The article was hardcoded `a`, so the sentence read "a 18h gap" whenever
 * the gap was 8, 11, 18, or in the eighties — which is a large share of real
 * gaps, not an edge case. Measured on `/people/dashboard` and
 * `/people/overview`, both of which render this one helper.
 *
 * The rule is the leading SOUND: 8 ("eight"), 11 ("eleven"), 18 ("eighteen")
 * and 80-89 ("eighty…") all begin with a vowel sound. Every other leading
 * digit does not, and the digits after the first never change the opening
 * sound — 118 is "one hundred and eighteen", so it takes "a".
 */
export function article(value: number): "a" | "an" {
  const n = Math.floor(Math.abs(value));
  if (n === 8 || n === 11 || n === 18) return "an";
  if (n >= 80 && n <= 89) return "an";
  // A longer number is read from its FIRST group, so only the leading digits
  // matter: 800 is "eight hundred", 1800 is "one thousand eight hundred".
  const lead = String(n);
  if (lead.length === 3 && lead[0] === "8") return "an";
  return "a";
}

export function describeSpread(group: Rollup): string | null {
  if (!group.spread) return null;
  const { most, least, gap_hours } = group.spread;
  return `${most.name} has ${hours(most.committed_hours)} due this week, ${
    least.name
  } has ${hours(least.committed_hours)} — ${article(gap_hours)} ${hours(
    gap_hours,
  )} gap`;
}

/**
 * The sentence a filtered, partially-scoped page has to say about itself.
 *
 * §5.7.5: where the viewer's grants hide rows, the surface says the figures are
 * partial rather than reporting a smaller number as though it were the whole. A
 * silently-truncated total is worse than no total, because it looks
 * authoritative.
 */
export function describeScope(res: {
  partial: boolean;
  work_visible: boolean;
}): string | null {
  if (!res.work_visible) {
    return "Task and project figures need the Projects app — the roster below is everyone, without their work.";
  }
  if (res.partial) {
    return "Counts cover the projects you can open, not every project in the company.";
  }
  return null;
}
