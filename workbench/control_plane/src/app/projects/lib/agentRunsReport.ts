/**
 * WS-27bn RA-4 — "Agent runs", the panel of how agent runs end, as pure rules.
 *
 * Spec: `project-docs/specs/projects_reports.md` §6.8 B, E and F, the §5 rows
 * "Run outcome", "Success rate", "Silent run", "Run size" and "Long run", and
 * §8 RA-4.
 *
 * The server decides every row and every count (`analytics_agent_runs.py`),
 * and it writes the one summary sentence and the flag of the long runs. This
 * module only says how the panel, its table and the email word what the
 * server sent. It adds no count and takes no rate.
 *
 * ⚠️ **No rank of people (§6.8 E1).** The silent rows keep the server's
 * order, the longest silence first, and the long rows the longest run first.
 * Nothing here sorts or groups by person.
 *
 * ⚠️ **An agent never hides its person (§6.8 E2).** Each row names the agent
 * and the member it works for, through `agentLine`.
 *
 * ⚠️ **No 100% target (§6.8 E4).** The success rate shows its trend, and no
 * text calls 100% the goal.
 *
 * Fence: `agentRunsReport.test.ts`.
 */
import { accentForSlot, type CategoricalAccent } from "@/lib/categorical";

import type {
  AgentRunsReport,
  LongRunRow,
  RunOutcome,
  RunOutcomeDay,
  RunSize,
  SilentRunRow,
} from "./api";
import { asList } from "./analyticsRead";
import { type WaitingLine, agentLine, taskLabel, waitedFor } from "./waiting";

/** The five outcomes, in the server's order, with the rule of each. */
export const RUN_OUTCOME_SEGMENTS: readonly { key: RunOutcome; label: string; title: string }[] = [
  {
    key: "completed",
    label: "Completed",
    title:
      "Completed: the agent finished the run, or Metorite closed it after its task closed or was archived.",
  },
  { key: "failed", label: "Failed", title: "Failed: the agent reported an error, and the run ended." },
  { key: "stopped", label: "Stopped", title: "Stopped: a person told the agent to stop." },
  {
    key: "abandoned",
    label: "Abandoned",
    title:
      "Abandoned: the agent sent nothing for longer than its lease, and another session took the task over.",
  },
  {
    key: "released",
    label: "Released",
    title: "Released: the agent or a person gave the task back, with no outcome.",
  },
];

/**
 * The hue of each outcome. ⚠️ An explicit pick of five slots of the ramp, as
 * RA-2's `actorAccent` picks its three, never an index of a changing list.
 * The keys are fixed words, so the hues never move. The label beside each
 * dot carries the meaning, never the hue alone.
 */
const OUTCOME_SLOT: Readonly<Record<RunOutcome, number>> = {
  completed: 2,
  failed: 4,
  stopped: 1,
  abandoned: 6,
  released: 7,
};

export function outcomeAccent(key: RunOutcome): CategoricalAccent {
  return accentForSlot(OUTCOME_SLOT[key]);
}

/** The four sizes of a completed run, shortest first, with their words. */
export const RUN_SIZES: readonly { key: RunSize; label: string }[] = [
  { key: "under_15m", label: "Under 15 min" },
  { key: "under_1h", label: "15 min to 1 h" },
  { key: "under_4h", label: "1 h to 4 h" },
  { key: "over_4h", label: "4 h or more" },
];

/** The size that the panel flags. */
export const LONG_SIZE: RunSize = "over_4h";

/** The rule of each figure, said once. The InfoTips and the test read it. */
export const AGENT_RUN_RULES = {
  outcomes: (days: number) =>
    `The agent runs that ended in the last ${days} days, today included, by outcome. A day starts at midnight UTC. A run ends when it reaches one of the five outcomes.`,
  success: (days: number) =>
    `Completed runs over the runs that completed, failed or were abandoned in the last ${days} days. A stop and a release are left out: a person chose the stop, and a release gives the task back with no outcome. The trend compares the ${days} days before.`,
  silent: (minutes: number) =>
    `Open runs in progress on open tasks whose agent sent no activity for ${minutes} minutes or more. A run with no activity yet counts from its start. The longest silence comes first.`,
  sizes: (days: number, hours: number) =>
    `The runs that completed in the last ${days} days, by agent time: from the start of the run to the last act of its agent, less each wait for an answer from a person. A run of ${hours} hours or more can mean a poorly specified task.`,
} as const;

/** How many rows of each list the panel shows. The table shows them all. */
export const PANEL_RUN_ROWS = 5;

/** A whole percent of a share from 0 to 1. */
export function percentWords(rate: number): string {
  return `${Math.round(rate * 100)}%`;
}

/** The success rate as its figure, or the words for no rate. */
export function successFigure(section: Pick<AgentRunsReport, "success">): string {
  const rate = section.success?.rate;
  return typeof rate === "number" ? percentWords(rate) : "No rate yet";
}

/**
 * The trend of the success rate in words: "up from 70%", "same as the 14
 * days before", or null when either window has no rate. ⚠️ It says which way
 * the rate moved, and never how far it is from 100% (§6.8 E4).
 */
export function successTrend(section: Pick<AgentRunsReport, "success" | "days">): string | null {
  const s = section.success;
  if (!s || s.trend === null || s.rate_before === null) return null;
  if (s.trend === "flat") return `same as the ${section.days} days before`;
  return `${s.trend === "up" ? "up" : "down"} from ${percentWords(s.rate_before)}`;
}

/** The day with the most runs, for the scale of the chart. One at least. */
export function peakDay(series: readonly RunOutcomeDay[] | undefined): number {
  return Math.max(1, ...asList<RunOutcomeDay>(series).map((d) => d.total));
}

/** The outcomes in one line: "9 completed · 2 failed · 1 stopped". */
export function outcomeLine(section: Pick<AgentRunsReport, "outcomes">): string {
  return RUN_OUTCOME_SEGMENTS.map(
    (seg) => `${section.outcomes?.[seg.key] ?? 0} ${seg.label.toLowerCase()}`
  ).join(" · ");
}

/** The silent runs as words, in the server's order. */
export function silentLines(
  section: Pick<AgentRunsReport, "silent">,
  name: (email: string) => string = (e) => e
): WaitingLine[] {
  return asList<SilentRunRow>(section.silent).map((r) => ({
    key: `silent:${r.run_id}`,
    id: r.id,
    label: taskLabel(r),
    detail: `${agentLine(r, name)} · silent for ${waitedFor(r.age_hours)}`,
  }));
}

/** The long runs as words, the longest first, as the server sent them. */
export function longLines(
  section: Pick<AgentRunsReport, "long_runs">,
  name: (email: string) => string = (e) => e
): WaitingLine[] {
  return asList<LongRunRow>(section.long_runs).map((r) => ({
    key: `long:${r.run_id}`,
    id: r.id,
    label: taskLabel(r),
    detail: `${agentLine(r, name)} · ${waitedFor(r.hours)} of agent time`,
  }));
}

/** The rows the table names: every silent run and long run the server sent. */
export function agentRunsRowCount(section: AgentRunsReport): number {
  return silentLines(section).length + longLines(section).length;
}

/** True when no run ended in the window and none is silent now. */
export function agentRunsIsClear(section: AgentRunsReport | null | undefined): boolean {
  if (!section) return false;
  return (section.ended_total ?? 0) === 0 && (section.silent_total ?? 0) === 0;
}

/**
 * True when the Overview hides the panel: no task of the scope has an agent
 * run, and nothing is in it (the done-when of RA-4). It is RA-3's rule for
 * `waitingHidden`. A report and the builder never hide it, and show a clear
 * row in its place.
 */
export function agentRunsHidden(section: AgentRunsReport | null | undefined): boolean {
  return !!section && section.agent_runs_seen === false && agentRunsIsClear(section);
}
