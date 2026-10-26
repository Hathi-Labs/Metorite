/**
 * WS-27bn RA-5 — "Quality", the panel of whether the speed is real, as pure
 * rules.
 *
 * Spec: `project-docs/specs/projects_reports.md` §6.8 B, E and F, the §5 rows
 * "Agent PR outcome", "Rework" and "CI red", and §8 RA-5.
 *
 * The server decides every count and every rate (`analytics_quality.py`),
 * and it writes the one summary sentence. This module only says how the
 * panel, its table and the email word what the server sent. It adds no
 * count and takes no rate.
 *
 * ⚠️ **No rank of people (§6.8 E1).** Rework has two groups, agents and
 * people. Nothing here sorts or groups by person.
 *
 * ⚠️ **An agent never hides its person (§6.8 E2).** Each failing row names
 * the agent and the member it works for, through `agentLine`.
 *
 * ⚠️ **No 100% target (§6.8 E4).** The merge rate shows its trend, and no
 * text calls 100% the goal. `qualityCopy` lists every string of this module
 * so a test can scan them.
 *
 * Fence: `qualityReport.test.ts`.
 */
import { accentForSlot, type CategoricalAccent } from "@/lib/categorical";

import type { QualityFailingRow, QualityReopened, QualityReport } from "./api";
import { asList } from "./analyticsRead";
import { type WaitingLine, agentLine, taskLabel, waitedFor } from "./waiting";

/** The two groups of rework, in the server's order, with their words. */
export const REWORK_GROUPS: readonly {
  key: "agents" | "people";
  label: string;
  noun: string;
}[] = [
  { key: "agents", label: "Agents", noun: "agents" },
  { key: "people", label: "People", noun: "people" },
];

/**
 * The hue of each mark. ⚠️ An explicit pick of slots of the ramp, as RA-2's
 * `actorAccent` and RA-4's `outcomeAccent` pick theirs, never an index of a
 * changing list. A group keeps the slot that RA-2 gives it (agents 3,
 * people 0), so one hue means one group in every panel. The label beside
 * each dot carries the meaning, never the hue alone.
 */
const SLOT = {
  merged: 2,
  closed: 4,
  open: 8,
  agents: 3,
  people: 0,
} as const;

export function qualityAccent(key: keyof typeof SLOT): CategoricalAccent {
  return accentForSlot(SLOT[key]);
}

/** The three pull request outcomes, in the order the bar draws them. */
export const PR_SEGMENTS: readonly {
  key: "merged" | "closed" | "open";
  label: string;
  title: string;
}[] = [
  { key: "merged", label: "Merged", title: "Merged: the pull request merged in the window." },
  {
    key: "closed",
    label: "Closed, not merged",
    title: "Closed with no merge: somebody closed the pull request in the window.",
  },
  { key: "open", label: "Open now", title: "Open now: open or draft, on a task that is not done." },
];

/** The rule of each figure, said once. The InfoTips and the test read it. */
export const QUALITY_RULES = {
  merge: (days: number) =>
    `The pull requests on tasks that an agent worked before the close, merged or closed in the last ${days} days. The rate is the merged ones over the merged and the closed ones. An open or draft pull request does not count. The trend compares the ${days} days before. The KPI strip shows the same rate.`,
  reopened: (days: number) =>
    `Tasks that went back from done to an open status in the last ${days} days. A move to cancelled is not a reopen. A reopen belongs to the group of the finish that it undoes: agents (agent-finished or agent-assisted) or people. The rate is the reopened tasks over the tasks that the same group finished in the same days.`,
  retried: (days: number) =>
    `Runs that failed in the last ${days} days, and how many of them have a later run on the same task. A later run is one created after the failed run ended.`,
  failing: () =>
    "Open or draft pull requests on tasks that an agent worked, whose checks fail now. The oldest comes first, by the time the task linked the pull request.",
  mergedRed: (days: number) =>
    `Agent pull requests that merged in the last ${days} days when the last checks read before the merge said failing. A read after the merge does not count, because a late check can rewrite the state. So the count can miss a pull request, and it never adds one.`,
} as const;

/** How many failing rows the panel shows. The table shows them all. */
export const PANEL_QUALITY_ROWS = 5;

/** A whole percent of a share from 0 to 1. */
export function percentWords(rate: number): string {
  return `${Math.round(rate * 100)}%`;
}

/** The merge rate as its figure, or the words for no rate. */
export function mergeFigure(section: Pick<QualityReport, "prs">): string {
  const rate = section.prs?.rate;
  return typeof rate === "number" ? percentWords(rate) : "No rate yet";
}

/**
 * The trend of the merge rate in words: "up from 70%", "same as the 14 days
 * before", or null when either window has no rate. ⚠️ It says which way the
 * rate moved, and never how far it is from 100% (§6.8 E4).
 */
export function mergeTrend(section: Pick<QualityReport, "prs" | "days">): string | null {
  const p = section.prs;
  if (!p || p.trend === null || p.rate_before === null) return null;
  if (p.trend === "flat") return `same as the ${section.days} days before`;
  return `${p.trend === "up" ? "up" : "down"} from ${percentWords(p.rate_before)}`;
}

/** "2 of 10", the reopened tasks over the finished ones. */
export function reopenedFigure(g: Pick<QualityReopened, "reopened" | "finished">): string {
  return `${g.reopened} of ${g.finished}`;
}

/** "20%", or "No finish yet" when the group finished nothing. */
export function reopenedRate(g: Pick<QualityReopened, "rate">): string {
  return typeof g.rate === "number" ? percentWords(g.rate) : "No finish yet";
}

/** The group in the window before, as words: "before: 1 of 8". */
export function reopenedBefore(g: Pick<QualityReopened, "reopened_before" | "finished_before">): string {
  return `before: ${g.reopened_before} of ${g.finished_before}`;
}

/** The retried runs as words: "3 of 5 failed runs got a new run". */
export function retriedLine(section: Pick<QualityReport, "rework" | "on">): string {
  if (!section.on) return "Agent runs are off, so no retry is counted";
  const r = section.rework?.retried;
  if (!r || r.failed === 0) return "No run failed";
  return `${r.retried} of ${r.failed} failed ${r.failed === 1 ? "run" : "runs"} got a new run`;
}

/** The failing pull requests as words, in the server's order. */
export function failingLines(
  section: Pick<QualityReport, "checks">,
  name: (email: string) => string = (e) => e
): WaitingLine[] {
  return asList<QualityFailingRow>(section.checks?.failing).map((r) => ({
    key: `failing:${r.id}:${r.ref}`,
    id: r.id,
    label: taskLabel(r),
    detail: `${agentLine(r, name)} · ${r.ref} · linked ${waitedFor(r.age_hours)} ago`,
  }));
}

/** The rows the table names: every failing pull request the server sent. */
export function qualityRowCount(section: QualityReport): number {
  return failingLines(section).length;
}

/** The pull requests that the section counts, in the window and open now. */
function prCount(section: QualityReport): number {
  const p = section.prs;
  return (p?.merged ?? 0) + (p?.closed ?? 0) + (p?.open ?? 0);
}

/** The reopened tasks of both groups, in the window. */
function reopenCount(section: QualityReport): number {
  const r = section.rework?.reopened;
  return (r?.agents?.reopened ?? 0) + (r?.people?.reopened ?? 0);
}

/**
 * True when nothing is in the window: no agent pull request merged, closed
 * or open, none fails or merged red, no task reopened, and no run failed.
 */
export function qualityIsClear(section: QualityReport | null | undefined): boolean {
  if (!section) return false;
  return (
    prCount(section) === 0 &&
    (section.checks?.failing_total ?? 0) === 0 &&
    (section.checks?.merged_red ?? 0) === 0 &&
    reopenCount(section) === 0 &&
    (section.rework?.retried?.failed ?? 0) === 0
  );
}

/**
 * True when the Overview hides the panel: the scope has no agent pull
 * request, no reopen and no failed run, and nothing is in the window. It is
 * RA-3's rule for `waitingHidden`. A report and the builder never hide it,
 * and show a clear row in its place.
 */
export function qualityHidden(section: QualityReport | null | undefined): boolean {
  return !!section && section.quality_seen === false && qualityIsClear(section);
}

/**
 * Every string of copy that this module owns, for the scan that refuses a
 * 100% goal (§6.8 E4). A rule that a function builds is called with 14 days.
 */
export function qualityCopy(): string[] {
  return [
    ...REWORK_GROUPS.flatMap((g) => [g.label, g.noun]),
    ...PR_SEGMENTS.flatMap((s) => [s.label, s.title]),
    QUALITY_RULES.merge(14),
    QUALITY_RULES.reopened(14),
    QUALITY_RULES.retried(14),
    QUALITY_RULES.failing(),
    QUALITY_RULES.mergedRed(14),
  ];
}
