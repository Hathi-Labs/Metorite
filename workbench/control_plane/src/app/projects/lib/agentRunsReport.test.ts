/**
 * WS-27bn RA-4 — "Agent runs", the pure rules of the client.
 *
 * Spec: `project-docs/specs/projects_reports.md` §6.8 B, E and F, §8 RA-4.
 * The server decides every figure (`analytics_agent_runs.py`). This file
 * holds the words, the explicit hues, the clear rule and the hide rule that
 * the Overview reads. The markup half is `components/agentRunsPanel.test.ts`.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { accentForSlot } from "@/lib/categorical";

import {
  AGENT_RUN_RULES,
  RUN_OUTCOME_SEGMENTS,
  RUN_SIZES,
  agentRunsHidden,
  agentRunsIsClear,
  agentRunsRowCount,
  longLines,
  outcomeAccent,
  outcomeLine,
  peakDay,
  silentLines,
  successFigure,
  successTrend,
} from "./agentRunsReport";
import type { AgentRunsReport, RenderedReportBody } from "./api";
import { overviewShown } from "./overviewLive";
import { PERIOD_FREE_SECTIONS, NO_SUBJECT_SECTIONS, OVERVIEW_SECTIONS } from "./reportBuilder";
import { SECTION_CLEAR_LINES, sectionIsClear } from "./sectionEmpty";

const ROW = {
  project_id: "p1",
  project_name: "Rig",
  agent: "Claude Code",
  client_via: "mcp:claude.ai:claude-code",
  member: "ana@example.test",
  member_name: "Ana Rao",
};

function section(over: Partial<AgentRunsReport> = {}): AgentRunsReport {
  return {
    summary: "No agent run ended in the last 14 days.",
    on: true,
    agent_runs_seen: true,
    days: 14,
    window_start: "2026-10-12",
    outcomes: { completed: 0, failed: 0, stopped: 0, abandoned: 0, released: 0, server_closed: 0 },
    ended_total: 0,
    outcomes_before: { completed: 0, failed: 0, stopped: 0, abandoned: 0, released: 0, server_closed: 0 },
    series: [],
    success: { rate: null, completed: 0, rated: 0, rate_before: null, rated_before: 0, trend: null },
    silent: [],
    silent_total: 0,
    silent_after_minutes: 15,
    sizes: { under_15m: 0, under_1h: 0, under_4h: 0, over_4h: 0, unmeasured: 0 },
    sized_total: 0,
    long_runs: [],
    long_total: 0,
    long_after_hours: 4,
    long_line: null,
    max_rows: 20,
    ...over,
  };
}

describe("the outcomes", () => {
  it("are the six outcomes of the server, in its order", () => {
    expect(RUN_OUTCOME_SEGMENTS.map((s) => s.key)).toEqual([
      "completed",
      "failed",
      "stopped",
      "abandoned",
      "released",
      "server_closed",
    ]);
    for (const seg of RUN_OUTCOME_SEGMENTS) expect(seg.title.startsWith(`${seg.label}:`)).toBe(true);
  });

  it("take an explicit slot of the ramp each, and no two share one", () => {
    const dots = RUN_OUTCOME_SEGMENTS.map((s) => outcomeAccent(s.key).dot);
    expect(new Set(dots).size).toBe(6);
    // RA-4b. A server close has its own slot and its own legend words.
    expect(outcomeAccent("server_closed")).toBe(accentForSlot(9));
    expect(RUN_OUTCOME_SEGMENTS.find((s) => s.key === "server_closed")?.label).toBe(
      "Closed by Metorite"
    );
    expect(outcomeAccent("completed")).toBe(accentForSlot(2));
    expect(outcomeAccent("failed")).toBe(accentForSlot(4));
  });

  it("say themselves in one line", () => {
    const s = section({
      outcomes: {
        completed: 9,
        failed: 2,
        stopped: 1,
        abandoned: 0,
        released: 3,
        server_closed: 4,
      },
    });
    expect(outcomeLine(s)).toBe(
      "9 completed · 2 failed · 1 stopped · 0 abandoned · 3 released · 4 closed by metorite"
    );
  });

  it("scale the chart to the busiest day, and never to zero", () => {
    const day = { day: "2026-10-20", completed: 0, failed: 0, stopped: 0, abandoned: 0, released: 0, server_closed: 0 };
    expect(peakDay([])).toBe(1);
    expect(peakDay([{ ...day, total: 3 }, { ...day, total: 7 }])).toBe(7);
  });
});

describe("the success rate", () => {
  it("is a whole percent, or words when no run has an outcome it counts", () => {
    expect(successFigure(section({ success: { ...section().success, rate: 0.6 } }))).toBe("60%");
    expect(successFigure(section())).toBe("No rate yet");
  });

  it("says its trend in words, with no target (§6.8 E4)", () => {
    const up = section({ success: { ...section().success, rate: 0.6, rate_before: 0.5, trend: "up" } });
    expect(successTrend(up)).toBe("up from 50%");
    const flat = section({ success: { ...section().success, rate: 0.5, rate_before: 0.5, trend: "flat" } });
    expect(successTrend(flat)).toBe("same as the 14 days before");
    expect(successTrend(section())).toBeNull();
  });

  it("names the rule: a stop and a release are left out", () => {
    const rule = AGENT_RUN_RULES.success(14);
    expect(rule).toContain("completed, failed or were abandoned");
    expect(rule).toContain("A stop, a release and a run that Metorite closed are left out");
    const source = readFileSync(join(__dirname, "agentRunsReport.ts"), "utf-8").toLowerCase();
    for (const phrase of ["target of 100", "aim for 100", "goal of 100", "should be 100"]) {
      expect(source).not.toContain(phrase);
    }
  });
});

describe("the rows: no rank, and the agent names its person (§6.8 E1, E2)", () => {
  const busy = section({
    silent: [
      { ...ROW, id: "t1", run_id: "r1", task_number: 7, title: "Calibrate", since: null, age_hours: 3 },
      { ...ROW, id: "t2", run_id: "r2", task_number: null, title: "Port", since: null, age_hours: 0.3, member_name: null, member: "bo@x.io" },
    ],
    silent_total: 2,
    long_runs: [{ ...ROW, id: "t3", run_id: "r3", task_number: 9, title: "Refactor", hours: 6, ended_at: null }],
    long_total: 1,
  });

  it("keeps the server's order and names the agent for its member", () => {
    const lines = silentLines(busy);
    expect(lines.map((l) => l.id)).toEqual(["t1", "t2"]);
    expect(lines[0]).toEqual({
      key: "silent:r1",
      id: "t1",
      label: "#7 Calibrate",
      detail: "Claude Code for Ana Rao · silent for 3 hours",
    });
    expect(lines[1].detail).toBe("Claude Code for bo@x.io · silent for under an hour");
  });

  it("says the agent time of a long run", () => {
    expect(longLines(busy)).toEqual([
      { key: "long:r3", id: "t3", label: "#9 Refactor", detail: "Claude Code for Ana Rao · 6 hours of agent time" },
    ]);
    expect(agentRunsRowCount(busy)).toBe(3);
  });
});

describe("the sizes", () => {
  it("are the server's four, shortest first", () => {
    expect(RUN_SIZES.map((s) => s.key)).toEqual(["under_15m", "under_1h", "under_4h", "over_4h"]);
    expect(AGENT_RUN_RULES.sizes(14, 4)).toContain("poorly specified task");
  });
});

describe("the clear rule and the hide rule (the done-when of RA-4)", () => {
  it("is clear when no run ended and none is silent", () => {
    expect(agentRunsIsClear(section())).toBe(true);
    expect(agentRunsIsClear(section({ ended_total: 1 }))).toBe(false);
    expect(agentRunsIsClear(section({ silent_total: 1 }))).toBe(false);
    expect(agentRunsIsClear(undefined)).toBe(false);
    // RA-4b. With runs off the section counts nothing, so it is never clear.
    expect(agentRunsIsClear(section({ on: false }))).toBe(false);
    expect(sectionIsClear("agent_runs", section({ on: false }))).toBe(false);
    // The Overview still hides an empty panel with runs off (no permanent card).
    expect(agentRunsHidden(section({ on: false, agent_runs_seen: false }))).toBe(true);
    expect(agentRunsHidden(section({ on: false, agent_runs_seen: false, silent_total: 1 }))).toBe(false);
    expect(sectionIsClear("agent_runs", section())).toBe(true);
    expect(SECTION_CLEAR_LINES.agent_runs).toBe("No run ended in 14 days, and none is silent.");
  });

  it("hides only a clear section of a scope with no agent run", () => {
    expect(agentRunsHidden(section({ agent_runs_seen: false }))).toBe(true);
    expect(agentRunsHidden(section({ agent_runs_seen: true }))).toBe(false);
    expect(agentRunsHidden(section({ agent_runs_seen: false, silent_total: 1 }))).toBe(false);
  });

  it("the Overview drops it, as it drops waiting, and keeps a body of hidden panels alone", () => {
    const quiet = section({ agent_runs_seen: false });
    const stuck = { overdue: [], overdue_total: 0 };
    const both = { agent_runs: quiet, stuck } as RenderedReportBody["sections"];
    expect(Object.keys(overviewShown(both))).toEqual(["stuck"]);
    const alone = { agent_runs: quiet } as RenderedReportBody["sections"];
    expect(Object.keys(overviewShown(alone))).toEqual(["agent_runs"]);
    const busy = { agent_runs: section({ ended_total: 2 }), stuck } as RenderedReportBody["sections"];
    expect(Object.keys(overviewShown(busy)).sort()).toEqual(["agent_runs", "stuck"]);
  });

  it("is on the Overview, refuses a subject, and ignores the report period", () => {
    expect(OVERVIEW_SECTIONS).toContain("agent_runs");
    expect(NO_SUBJECT_SECTIONS).toContain("agent_runs");
    expect(PERIOD_FREE_SECTIONS).toContain("agent_runs");
  });
});
