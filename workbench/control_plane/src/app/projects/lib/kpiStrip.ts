/**
 * WS-27bn RA-2 — the KPI strip of the Overview, and "Who did the work", as
 * pure rules.
 *
 * Spec: `project-docs/specs/projects_reports.md` §6.8 B, D, E and F, §8 RA-2.
 *
 * ⚠️ **The server decides every figure.** Three tiles read a section
 * (`finished`, `throughput`, `waiting`), and three read the `kpis` block of
 * the Overview's preview (`analytics_agents.kpis_body`). This module chooses
 * the words and the order. It adds no count and takes no median.
 *
 * ⚠️ **Each tile names the people and agents split, and its rule is its
 * tooltip** (the done-when of RA-2). The words of a rule live here once.
 *
 * ⚠️ **No rank of people (§6.8 E1), and no 100% target (§6.8 E4).**
 *
 * Fence: `kpiStrip.test.ts`.
 */
import { accentForSlot, type CategoricalAccent } from "@/lib/categorical";

import type { ActorWeek, KpisReport, PreviewReportBody, WhoDidTheWorkReport } from "./api";
import { asList } from "./analyticsRead";
import { waitedFor } from "./waiting";

/** What the strip reads of a rendered body: a saved report or a preview. */
type Body = Pick<PreviewReportBody, "sections"> & {
  report: Pick<PreviewReportBody["report"], "scope" | "config">;
  kpis?: KpisReport;
};

/** One tile of the strip. `display` is the figure as words. */
export interface KpiTile {
  key: "finished" | "cycle" | "waiting" | "runs" | "merge" | "cost";
  label: string;
  /** The figure. Absent when the server did not send it: the tile says so. */
  display?: string;
  /** The split, in one or two short lines. */
  detail: string[];
  /** The rule of the tile, in plain words (the done-when). */
  title: string;
  tone?: "done";
}

/** The rule of each tile, said once. The tooltip and the test read it. */
export const TILE_RULES = {
  finished:
    "Tasks that moved to done in this week, from Monday in UTC. Agents counts each task that an agent worked before it finished, also when a person finished it. People counts each task that no agent worked.",
  finishedLast:
    "Tasks that moved to done in the last whole week, from Monday in UTC. Agents counts each task that an agent worked before it finished, also when a person finished it. People counts each task that no agent worked.",
  cycle:
    "Half of the measured tasks of this period took less than this, from the first move to In progress until done. People: tasks that no agent worked. Agents: tasks that an agent worked, also when a person finished them.",
  waiting:
    "Agent questions with no answer, agent work that waits for a review, and blocked handoffs between people and agents. A task that waits in two ways counts two times. The oldest is the longest wait now.",
  runs: (minutes: number) =>
    `Agent runs in progress now, on open tasks. A run is silent when its agent sent no activity for ${minutes} minutes or more. Failed today counts the runs that failed since midnight, in UTC.`,
  runsOff: "Agent runs are off for this organization, so no run is counted.",
  merge: (days: number) =>
    `Of the pull requests on tasks that an agent worked, merged or closed in the last ${days} days: the part that merged. A pull request closed with no merge counts against it, on the date of its last update on GitHub. An open or draft one does not count yet, and neither does one with no date from GitHub. The trend compares the ${days} days before.`,
  cost:
    "No cost source is on yet, because usage capture (SW-20) is not built. Unknown is not zero. Agent time is the median time that agents worked on each finished task: from the start of each run to the last act of its agent, less each wait for an answer from a person. It is time, measured, and it is not money.",
} as const;

/** A whole percent of a share from 0 to 1. Words for the tile only. */
function percent(share: number): string {
  return `${Math.round(share * 100)}%`;
}

/** The last week of the series: this week, or last week for a report. */
export function lastWeek(series: readonly ActorWeek[] | undefined): ActorWeek | null {
  const list = asList<ActorWeek>(series);
  return list.length > 0 ? list[list.length - 1] : null;
}

function finishedTile(body: Body): KpiTile | null {
  const section = body.sections.finished;
  if (!section) return null;
  const skip = body.report.config.skip_current_week;
  const week = lastWeek(section.actor_series);
  return {
    key: "finished",
    label: skip ? "Finished last week" : "Finished this week",
    display: week ? String(week.completed) : undefined,
    detail: week ? [`${week.people} by people · ${week.agents} by agents`] : [],
    title: skip ? TILE_RULES.finishedLast : TILE_RULES.finished,
    tone: "done",
  };
}

/** "3 hours", or "not measured" for no median. */
function median(hours: number | null | undefined): string {
  return typeof hours === "number" ? waitedFor(hours) : "not measured";
}

function cycleTile(body: Body): KpiTile | null {
  const section = body.sections.throughput;
  if (!section) return null;
  const cycle = section.actor_cycle;
  return {
    key: "cycle",
    label: "Median cycle time",
    display: section.median_hours === undefined ? undefined : median(section.median_hours),
    detail: cycle
      ? [`People ${median(cycle.people.median_hours)} · Agents ${median(cycle.agents.median_hours)}`]
      : [],
    title: TILE_RULES.cycle,
  };
}

function waitingTile(body: Body): KpiTile | null {
  const section = body.sections.waiting;
  if (!section) return null;
  const total = section.waiting_total;
  return {
    key: "waiting",
    label: "Waiting on a person",
    display: typeof total === "number" ? String(total) : undefined,
    detail:
      typeof total !== "number"
        ? []
        : typeof section.oldest_hours === "number"
          ? [`Agents wait on people · oldest ${waitedFor(section.oldest_hours)}`]
          : ["Nothing waits on a person"],
    title: TILE_RULES.waiting,
  };
}

function runsTile(kpis: KpisReport): KpiTile {
  const r = kpis.runs;
  if (!r.on) {
    return {
      key: "runs",
      label: "Agents working now",
      display: "Off",
      detail: ["Agent runs are off"],
      title: TILE_RULES.runsOff,
    };
  }
  // §6.8 E2. The tooltip names the member of each run, in the server's
  // order, which is by name and never by count.
  const names = asList<KpisReport["runs"]["members"][number]>(r.members).map(
    (m) => m.member_name || m.member
  );
  const hidden = typeof r.hidden_people === "number" ? r.hidden_people : 0;
  if (hidden > 0) names.push(hidden === 1 ? "1 other person" : `${hidden} other people`);
  const forLine = names.length > 0 ? ` They work for ${names.join(", ")}.` : "";
  return {
    key: "runs",
    label: "Agents working now",
    display: String(r.working),
    detail: [`${r.silent} silent · ${r.failed_today} failed today`],
    title: `${TILE_RULES.runs(r.silent_after_minutes)}${forLine}`,
  };
}

function trendLine(m: KpisReport["merge"]): string | null {
  if (m.trend === null || m.rate_before === null) return null;
  if (m.trend === "flat") return `same as the ${m.days} days before`;
  return `${m.trend === "up" ? "up" : "down"} from ${percent(m.rate_before)}`;
}

function mergeTile(kpis: KpisReport): KpiTile {
  const m = kpis.merge;
  const trend = trendLine(m);
  return {
    key: "merge",
    label: `Agent PR merge rate, ${m.days} days`,
    display: m.rate === null ? "No PR closed" : percent(m.rate),
    detail: [`${m.merged} merged · ${m.closed} closed${trend ? ` · ${trend}` : ""}`],
    title: TILE_RULES.merge(m.days),
  };
}

function costTile(kpis: KpisReport): KpiTile {
  const { cost, time } = kpis;
  const share =
    cost.share === null ? "No task finished" : `${percent(cost.share)} of finished tasks have cost data`;
  const agentTime =
    typeof time.median_hours === "number"
      ? `Agent time ${waitedFor(time.median_hours)} a task (time, measured)`
      : "No agent time measured";
  return {
    key: "cost",
    label: "Cost per finished task",
    // ⚠️ Never "$0" (§6.8 D). No source holds a cost yet.
    display: cost.per_task === null ? "Unknown" : String(cost.per_task),
    detail: [share, agentTime],
    title: TILE_RULES.cost,
  };
}

/**
 * The six tiles of the Overview, in the order of §6.8 B. A tile whose
 * section is not in the body is left out, as `reportTiles` rules. So a
 * subject hides "Waiting on a person": `waiting` refuses a subject
 * (`NO_SUBJECT_SECTIONS`), and `sectionsFor` leaves it out of the config. With no
 * `kpis` block the strip is empty, and the caller draws the report tiles.
 */
export function overviewTiles(body: Body): KpiTile[] {
  if (!body.kpis) return [];
  return [
    finishedTile(body),
    cycleTile(body),
    waitingTile(body),
    runsTile(body.kpis),
    mergeTile(body.kpis),
    costTile(body.kpis),
  ].filter((t): t is KpiTile => t !== null);
}

// ── "Who did the work" ──────────────────────────────────────────────────────

/** The title of the panel that replaces "What we finished" for one project. */
export const WHO_DID_THE_WORK = "Who did the work";

/**
 * True when the Overview draws "Who did the work" in place of "What we
 * finished" (§6.8 B): the scope is a node, and its finished work sits in one
 * project or none. A bar of one project inside that project says nothing.
 * A scope of more than one project keeps "What we finished".
 */
export function whoDidTheWorkShown(body: Pick<Body, "report" | "sections">): boolean {
  const section = body.sections.finished;
  if (!section || body.report.scope !== "node") return false;
  if (!Array.isArray(section.actor_series) || typeof section.actor_summary !== "string") {
    return false;
  }
  return asList(section.projects).length <= 1;
}

/** The segments of a week, bottom to top, and the legend, in one order. */
export const ACTOR_SEGMENTS: readonly {
  key: "person" | "agent_assisted" | "agent_finished";
  label: string;
  title: string;
}[] = [
  { key: "person", label: "People", title: "People: tasks that no agent worked before they finished." },
  {
    key: "agent_assisted",
    label: "Agent-assisted",
    title: "Agent-assisted: an agent worked the task, and a person, a workflow or GitHub finished it.",
  },
  {
    key: "agent_finished",
    label: "Agent-finished",
    title: "Agent-finished: an agent moved the task to done.",
  },
];

/**
 * The hue of each segment. ⚠️ An explicit pick of three slots of the ramp,
 * as a space marker picks its slot, never an index of a changing list. The
 * keys are fixed words, so the hues never move. The label beside each dot
 * carries the meaning, never the hue alone.
 */
const ACTOR_SLOT: Readonly<Record<(typeof ACTOR_SEGMENTS)[number]["key"], number>> = {
  person: 0,
  agent_assisted: 5,
  agent_finished: 3,
};

export function actorAccent(key: (typeof ACTOR_SEGMENTS)[number]["key"]): CategoricalAccent {
  return accentForSlot(ACTOR_SLOT[key]);
}

/** The words of one agent of a member: "Claude Code: 2 finished, 1 helped". */
export function memberAgentLine(a: WhoDidTheWorkReport["agent_members"][number]["agents"][number]): string {
  const parts = [
    a.agent_finished ? `${a.agent_finished} finished` : null,
    a.agent_assisted ? `${a.agent_assisted} helped` : null,
  ].filter(Boolean);
  return `${a.agent}: ${parts.join(", ") || "0"}`;
}

/** "This report hides 2 other people", or null. */
export function hiddenMembersLine(n: number | null | undefined): string | null {
  if (typeof n !== "number" || n <= 0) return null;
  return n === 1
    ? "This report hides the agent work of 1 other person."
    : `This report hides the agent work of ${n} other people.`;
}
