/**
 * WS-27bn RA-2 — the KPI strip and "Who did the work", the pure rules.
 *
 * Spec: `project-docs/specs/projects_reports.md` §6.8 B, D and E, §8 RA-2.
 * The server decides each figure (`analytics_agents.py`, pinned by
 * `test_projects_report_kpis.py` and its R8 twin). These tests pin the
 * words, the order, and the rule in each tooltip (the done-when).
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import type { ActorWeek, KpisReport, PreviewReportBody, WaitingReport } from "./api";
import {
  ACTOR_SEGMENTS,
  TILE_RULES,
  actorAccent,
  hiddenMembersLine,
  lastWeek,
  memberAgentLine,
  overviewTiles,
  whoDidTheWorkShown,
} from "./kpiStrip";
import { overviewState, sectionsFor } from "./reportBuilder";
import { whoDidTheWorkPanelData } from "./reportPanels";

const WEEK: ActorWeek = {
  week_start: "2026-10-19",
  agent_finished: 2,
  agent_assisted: 1,
  person: 4,
  agents: 3,
  people: 4,
  completed: 7,
};

const KPIS: KpisReport = {
  runs: {
    on: true,
    working: 3,
    silent: 1,
    failed_today: 2,
    silent_after_minutes: 15,
    members: [{ member: "ana@x.test", member_name: "Ana Rao", working: 3, silent: 1 }],
  },
  merge: {
    days: 14,
    merged: 8,
    closed: 2,
    rate: 0.8,
    merged_before: 7,
    closed_before: 3,
    rate_before: 0.7,
    trend: "up",
  },
  time: { finished: 10, run_tasks: 4, median_hours: 2, total_hours: 9, basis: "run_start_to_end" },
  cost: {
    per_task: null,
    source: null,
    known_tasks: 0,
    finished: 10,
    share: 0,
    reason: "Usage capture (SW-20) is not built.",
  },
};

const WAITING = {
  waiting_total: 5,
  oldest_hours: 72,
} as unknown as WaitingReport;

function body(over: Partial<PreviewReportBody> = {}): PreviewReportBody {
  return {
    report: {
      id: null,
      project_id: null,
      scope: "portfolio",
      name: "Overview",
      config: { weeks: 12, skip_current_week: false, include_subtree: true, sections: [] },
    },
    period_start: "2026-08-03",
    period_end: "2026-10-25",
    sections: {
      finished: {
        projects: [],
        total_completed: 7,
        total_cancelled: 0,
        actor_split: { agent_finished: 2, agent_assisted: 1, person: 4 },
        actor_summary: "7 tasks finished in this period.",
        actor_series: [{ ...WEEK, week_start: "2026-10-12", completed: 1, people: 1, agents: 0 }, WEEK],
        agent_members: [],
      },
      throughput: {
        series: [],
        median_hours: 30,
        measured: 5,
        actor_cycle: {
          people: { completed: 4, assisted: 0, measured: 3, median_hours: 48, p90_hours: 60 },
          agents: { completed: 3, assisted: 1, measured: 2, median_hours: 5, p90_hours: 9 },
        },
      },
      waiting: WAITING,
    },
    kpis: KPIS,
    ...over,
  } as PreviewReportBody;
}

describe("the six tiles", () => {
  it("come in the order of §6.8 B", () => {
    expect(overviewTiles(body()).map((t) => t.key)).toEqual([
      "finished",
      "cycle",
      "waiting",
      "runs",
      "merge",
      "cost",
    ]);
  });

  it("is no strip without the kpis block, so the report tiles draw", () => {
    expect(overviewTiles(body({ kpis: undefined }))).toEqual([]);
  });

  it("finished this week reads the last week and names the split", () => {
    const t = overviewTiles(body())[0];
    expect(t.label).toBe("Finished this week");
    expect(t.display).toBe("7");
    expect(t.detail).toEqual(["4 by people · 3 by agents"]);
    expect(t.title).toBe(TILE_RULES.finished);
  });

  it("finished names last week for a period that drops the running week", () => {
    const b = body();
    b.report.config.skip_current_week = true;
    const t = overviewTiles(b)[0];
    expect(t.label).toBe("Finished last week");
    expect(t.title).toBe(TILE_RULES.finishedLast);
  });

  it("median cycle time names both medians", () => {
    const t = overviewTiles(body())[1];
    expect(t.display).toBe("30 hours");
    expect(t.detail).toEqual(["People 2 days · Agents 5 hours"]);
    expect(t.title).toMatch(/also when a person finished them/);
  });

  it("waiting on a person reads the waiting section's own total and oldest", () => {
    const t = overviewTiles(body())[2];
    expect(t.display).toBe("5");
    expect(t.detail).toEqual(["Agents wait on people · oldest 3 days"]);
    expect(t.title).toMatch(/counts two times/);
  });

  it("agents working now names silent, failed today and each member", () => {
    const t = overviewTiles(body())[3];
    expect(t.display).toBe("3");
    expect(t.detail).toEqual(["1 silent · 2 failed today"]);
    expect(t.title).toMatch(/15 minutes or more/);
    expect(t.title).toMatch(/They work for Ana Rao\./);
  });

  it("agents working now says off, and counts no run, with the flag off", () => {
    const b = body({ kpis: { ...KPIS, runs: { ...KPIS.runs, on: false, working: 0 } } });
    const t = overviewTiles(b)[3];
    expect(t.display).toBe("Off");
    expect(t.title).toBe(TILE_RULES.runsOff);
  });

  it("the merge rate shows its trend and no 100% target (§6.8 E4)", () => {
    const t = overviewTiles(body())[4];
    expect(t.display).toBe("80%");
    expect(t.label).toBe("Agent PR merge rate");
    expect(t.detail).toEqual(["8 merged · 2 closed in 14 days · up from 70%"]);
    const words = `${t.label} ${t.title} ${t.detail.join(" ")}`;
    expect(words).not.toMatch(/100\s*%/);
    expect(words).not.toMatch(/\b(target|goal|aim)\b/i);
  });

  it("the merge rate says so when no pull request closed", () => {
    const b = body({ kpis: { ...KPIS, merge: { ...KPIS.merge, rate: null, trend: null } } });
    expect(overviewTiles(b)[4].display).toBe("No PR closed");
  });

  it("cost is unknown and never $0, with its share and the measured time", () => {
    const t = overviewTiles(body())[5];
    expect(t.display).toBe("Unknown");
    expect(t.display).not.toMatch(/\$|0/);
    expect(t.detail).toEqual([
      "0% of finished tasks have cost data",
      "Agent time 2 hours a task (time, measured)",
    ]);
    expect(t.title).toMatch(/less each wait for an answer from a person/);
  });

  it("hides the waiting tile for a subject, because `waiting` refuses one", () => {
    // RA-2 review: `waiting_body` takes no subject, so a count for the whole
    // scope beside the subject's other tiles would mislead. The config drops
    // the section, and the tile goes with it.
    const sections = sectionsFor({
      ...overviewState(),
      subject: { kind: "person", email: "ana@x.test" },
    });
    expect(sections).not.toContain("waiting");
    const b = body();
    delete b.sections.waiting;
    expect(overviewTiles(b).map((t) => t.key)).not.toContain("waiting");
  });

  it("dates a merge rate by GitHub, and says so", () => {
    expect(TILE_RULES.merge(14)).toMatch(/last update on GitHub/);
    expect(TILE_RULES.merge(14)).toMatch(/no date from GitHub/);
  });

  it("says the agent time leaves out the waits for a person", () => {
    expect(TILE_RULES.cost).toMatch(/last act of its agent/);
    expect(TILE_RULES.cost).toMatch(/less each wait for an answer from a person/);
    expect(TILE_RULES.cost).not.toMatch(/includes the time a question waited/);
  });

  it("leaves out a tile whose section the reader turned off", () => {
    const b = body();
    delete b.sections.waiting;
    expect(overviewTiles(b).map((t) => t.key)).not.toContain("waiting");
  });

  it("says nothing it was not sent: an old waiting section has no figure", () => {
    const b = body();
    b.sections.waiting = {} as WaitingReport;
    const t = overviewTiles(b)[2];
    expect(t.display).toBeUndefined();
    expect(t.detail).toEqual([]);
  });

  it("each tile names people or agents, and carries its rule", () => {
    for (const t of overviewTiles(body())) {
      expect(`${t.label} ${t.title} ${t.detail.join(" ")}`).toMatch(/people|agent/i);
      expect(t.title.length).toBeGreaterThan(40);
    }
  });

  it("lastWeek is the running week, or null for no series", () => {
    expect(lastWeek([WEEK])).toEqual(WEEK);
    expect(lastWeek(undefined)).toBeNull();
  });
});

describe("Who did the work", () => {
  it("replaces What we finished for a node whose work sits in one project", () => {
    const b = body();
    b.report.scope = "node";
    b.sections.finished!.projects = [
      { project_id: "p", name: "Rig", completed: 7, cancelled: 0 },
    ];
    expect(whoDidTheWorkShown(b)).toBe(true);
  });

  it("keeps What we finished for the organization and for many projects", () => {
    expect(whoDidTheWorkShown(body())).toBe(false);
    const b = body();
    b.report.scope = "node";
    b.sections.finished!.projects = [
      { project_id: "p", name: "Rig", completed: 4, cancelled: 0 },
      { project_id: "q", name: "Jig", completed: 3, cancelled: 0 },
    ];
    expect(whoDidTheWorkShown(b)).toBe(false);
  });

  it("keeps What we finished for a body from a server before RA-2", () => {
    const b = body();
    b.report.scope = "node";
    delete b.sections.finished!.actor_series;
    expect(whoDidTheWorkShown(b)).toBe(false);
  });

  it("the adapter copies the server's figures, and dims the running week only", () => {
    const b = body();
    const data = whoDidTheWorkPanelData(b.sections.finished!, b);
    expect(data.actor_series).toEqual(b.sections.finished!.actor_series);
    expect(data.current_week_partial).toBe(true);
    b.report.config.skip_current_week = true;
    expect(whoDidTheWorkPanelData(b.sections.finished!, b).current_week_partial).toBe(false);
    // A key the section lacks stays absent.
    expect("hidden_people" in data).toBe(false);
  });

  it("the segments use the §5 words, and each hue is a fixed ramp slot", () => {
    expect(ACTOR_SEGMENTS.map((s) => s.label)).toEqual(["People", "Agent-assisted", "Agent-finished"]);
    const dots = ACTOR_SEGMENTS.map((s) => actorAccent(s.key).dot);
    expect(new Set(dots).size).toBe(3);
    for (const dot of dots) expect(dot).toMatch(/^bg-cat-\d+$/);
  });

  it("names each agent's finishes and help in words", () => {
    expect(
      memberAgentLine({
        agent: "Claude Code",
        client_via: "mcp:x",
        agent_finished: 2,
        agent_assisted: 1,
        total: 3,
      })
    ).toBe("Claude Code: 2 finished, 1 helped");
  });

  it("counts the hidden members in one line", () => {
    expect(hiddenMembersLine(undefined)).toBeNull();
    expect(hiddenMembersLine(1)).toMatch(/1 other person/);
    expect(hiddenMembersLine(3)).toMatch(/3 other people/);
  });

  it("computes no figure: no sum and no sort in the module", () => {
    const src = readFileSync(join(__dirname, "kpiStrip.ts"), "utf-8");
    expect(src).not.toMatch(/\.reduce\(/);
    expect(src).not.toMatch(/\.sort\(/);
  });
});
