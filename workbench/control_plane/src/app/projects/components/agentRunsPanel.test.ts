/**
 * WS-27bn RA-4 — "Agent runs", the markup half.
 *
 * Spec: `project-docs/specs/projects_reports.md` §6.8 B and E, §8 RA-4. The
 * vitest environment has no DOM, so these tests render the panel with
 * `renderToStaticMarkup`. The pure rules are `lib/agentRunsReport.test.ts`.
 *
 * Done-when of RA-4: "the panel hides itself when the scope has no agent
 * runs". Each figure's rule is reachable by keyboard and touch (`InfoTip`),
 * and each row opens its task.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { AgentRunsReport, RenderedReportBody } from "../lib/api";
import { AgentRunsPanel } from "./AnalyticsPanels";
import { RenderedBody } from "./ReportsView";

const html = (el: React.ReactElement) => renderToStaticMarkup(el);

const ROW = {
  project_id: "p1",
  project_name: "Rig",
  agent: "Claude Code",
  client_via: "mcp:claude.ai:claude-code",
  member: "ana@example.test",
  member_name: "Ana Rao",
};

const DAY = { completed: 0, failed: 0, stopped: 0, abandoned: 0, released: 0, server_closed: 0, total: 0 };

const BUSY: AgentRunsReport = {
  summary:
    "9 agent runs ended in the last 14 days. The success rate is 75%, down from 80% in the 14 days before." +
    " 2 runs are silent now. 1 completed run took 4 hours or more of agent time: maybe a poorly specified task.",
  on: true,
  agent_runs_seen: true,
  days: 14,
  window_start: "2026-10-12",
  outcomes: { completed: 6, failed: 1, stopped: 1, abandoned: 1, released: 0, server_closed: 0 },
  ended_total: 9,
  outcomes_before: { completed: 4, failed: 1, stopped: 0, abandoned: 0, released: 0, server_closed: 0 },
  series: [
    { ...DAY, day: "2026-10-12" },
    { ...DAY, day: "2026-10-24", completed: 2, failed: 1, total: 3 },
    { ...DAY, day: "2026-10-25", completed: 4, stopped: 1, abandoned: 1, total: 6 },
  ],
  success: { rate: 0.75, completed: 6, rated: 8, rate_before: 0.8, rated_before: 5, trend: "down" },
  silent: [
    { ...ROW, id: "s-old", run_id: "r1", task_number: 21, title: "Calibrate the bed", since: null, age_hours: 2 },
    { ...ROW, id: "s-new", run_id: "r2", task_number: 22, title: "Flash the board", since: null, age_hours: 0.4, member_name: "Bo" },
  ],
  silent_total: 2,
  silent_after_minutes: 15,
  sizes: { under_15m: 2, under_1h: 2, under_4h: 1, over_4h: 1, unmeasured: 0 },
  sized_total: 6,
  long_runs: [{ ...ROW, id: "l1", run_id: "r3", task_number: 23, title: "Port the firmware", hours: 6.5, ended_at: null }],
  long_total: 1,
  long_after_hours: 4,
  long_line: "1 completed run took 4 hours or more of agent time: maybe a poorly specified task.",
  max_rows: 20,
};

const QUIET: AgentRunsReport = {
  ...BUSY,
  summary: "No agent run ended in the last 14 days.",
  agent_runs_seen: false,
  outcomes: { completed: 0, failed: 0, stopped: 0, abandoned: 0, released: 0, server_closed: 0 },
  ended_total: 0,
  series: [],
  success: { rate: null, completed: 0, rated: 0, rate_before: null, rated_before: 0, trend: null },
  silent: [],
  silent_total: 0,
  sizes: { under_15m: 0, under_1h: 0, under_4h: 0, over_4h: 0, unmeasured: 0 },
  sized_total: 0,
  long_runs: [],
  long_total: 0,
  long_line: null,
};

const STUCK = {
  overdue: [{ project_id: "p1", name: "Rig", overdue: 2 }],
  overdue_total: 2,
  blocked: [],
  blocked_total: 0,
  stale: [],
};

function body(sections: Record<string, unknown>): RenderedReportBody {
  return {
    report: {
      id: "r-1",
      project_id: null,
      scope: "portfolio",
      name: "Overview",
      config: { weeks: 12, skip_current_week: false, include_subtree: true, sections: Object.keys(sections) },
      created_by: "a@x.io",
      created_at: "2026-10-25T00:00:00Z",
    },
    period_start: "2026-08-04",
    period_end: "2026-10-25",
    sections,
  } as unknown as RenderedReportBody;
}

describe("the panel opens with one plain sentence (§6.8 E5)", () => {
  const panel = html(createElement(AgentRunsPanel, { data: BUSY }));

  it("says the server's summary before any figure", () => {
    expect(panel).toContain("Agent runs");
    expect(panel.indexOf(BUSY.summary)).toBeGreaterThan(-1);
    expect(panel.indexOf(BUSY.summary)).toBeLessThan(panel.indexOf("Success rate"));
  });

  it("draws the success rate with its trend, the ended count and the silent count", () => {
    expect(panel).toContain(">75%<");
    expect(panel).toContain("down from 80%");
    expect(panel).toContain(">9<");
    expect(panel).toContain("in 14 days");
    expect(panel).toContain('title="6 of 8 runs that completed, failed or were abandoned"');
  });

  it("stacks each day by outcome, names both ends of the axis and the legend", () => {
    expect(panel).toContain("runs ended per day");
    expect(panel).toContain(">12 Oct<");
    expect(panel).toContain(">today<");
    expect(panel).toContain('title="25 Oct: 6 ended, 4 completed, 1 stopped, 1 abandoned"');
    for (const label of ["Completed", "Failed", "Stopped", "Abandoned", "Released"]) {
      expect(panel).toContain(label);
    }
    // The hue is never alone: each legend dot sits beside its label.
    expect(panel).toMatch(/bg-cat-3[^>]*aria-hidden="true"[^>]*><\/span>Completed/);
  });

  it("flags the long runs in words, with the count, and lists them", () => {
    expect(panel).toContain(BUSY.long_line as string);
    expect(panel).toContain("4 h or more");
    expect(panel).toContain("Port the firmware");
    expect(panel).toContain("7 hours of agent time");
  });
});

describe("each figure's rule is reachable by keyboard and touch (InfoTip)", () => {
  const panel = html(createElement(AgentRunsPanel, { data: BUSY }));

  it("puts an InfoTip beside each figure that has a rule", () => {
    for (const label of [
      "How the success rate is counted",
      "How a silent run is counted",
      "How the run outcomes are counted",
      "How the run sizes are counted",
    ]) {
      expect(panel).toContain(`aria-label="${label}"`);
    }
  });

  it("a closed tip is a button that says it opens", () => {
    const button = panel.match(/<button[^>]*aria-label="How the success rate is counted"[^>]*>/);
    expect(button).not.toBeNull();
    expect(button![0]).toContain('aria-expanded="false"');
  });
});

describe("each row opens its task, and names the agent and its member (§6.8 E2)", () => {
  const panel = html(createElement(AgentRunsPanel, { data: BUSY }));

  it("is a real link to the task's deep link, in the server's order", () => {
    expect(panel).toContain('href="/projects?task=s-old"');
    expect(panel).toContain('href="/projects?task=l1"');
    expect(panel.indexOf("s-old")).toBeLessThan(panel.indexOf("s-new"));
  });

  it("names the agent for its member on each row", () => {
    expect(panel).toContain("Claude Code for Ana Rao · silent for 2 hours");
    expect(panel).toContain("Claude Code for Bo · silent for under an hour");
  });

  it("reads the one opener of a report row", () => {
    const panels = readFileSync(join(__dirname, "AnalyticsPanels.tsx"), "utf-8");
    const start = panels.indexOf("export function AgentRunsPanel(");
    const fn = panels.slice(start, panels.indexOf("\n}\n", start));
    expect(fn).toContain("const open = useContext(OpenTaskContext);");
    expect(fn).toContain("<WaitingTaskRow key={r.key} line={r} open={open} />");
  });
});

describe("the Overview hides the panel when the scope has no agent run (RA-4 done-when)", () => {
  it("draws no Agent runs card in the live grid", () => {
    const grid = html(
      createElement(RenderedBody, {
        body: body({ stuck: STUCK, agent_runs: QUIET }),
        layout: "grid",
        showTitle: false,
      })
    );
    expect(grid).not.toContain("Agent runs");
    expect(grid).toContain("Stuck work");
  });

  it("a saved report keeps it, as one clear row", () => {
    const column = html(createElement(RenderedBody, { body: body({ stuck: STUCK, agent_runs: QUIET }) }));
    expect(column).toContain("Agent runs");
    expect(column).toContain("No run ended in 14 days, and none is silent.");
  });

  it("the live grid draws it when agents work in the scope", () => {
    const grid = html(
      createElement(RenderedBody, {
        body: body({ stuck: STUCK, agent_runs: BUSY }),
        layout: "grid",
        showTitle: false,
      })
    );
    expect(grid).toContain("Agent runs");
    expect(grid).toContain(BUSY.summary);
  });

  it("the table under the panel names every silent run and long run", () => {
    const column = html(createElement(RenderedBody, { body: body({ agent_runs: BUSY }) }));
    expect(column).toContain("Silent now");
    expect(column).toContain("Runs of 4 hours or more");
    expect(column).toContain("6 completed · 1 failed · 1 stopped · 1 abandoned · 0 released · 0 closed by metorite");
  });
});
