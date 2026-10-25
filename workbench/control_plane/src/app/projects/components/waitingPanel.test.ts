/**
 * WS-27bn RA-3 — "Waiting on people", the markup half.
 *
 * Spec: `project-docs/specs/projects_reports.md` §6.8 C and E, §8 RA-3. The
 * vitest environment has no DOM, so these tests render the panel with
 * `renderToStaticMarkup`. The pure rules are `lib/waiting.test.ts`.
 *
 * Done-when of RA-3: "each row opens its task". A row is a real link to the
 * task's deep link, and a plain click goes through the page's opener.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { RenderedReportBody, WaitingReport, WaitingRow } from "../lib/api";
import { OpenTaskContext, WaitingPanel } from "./AnalyticsPanels";
import { RenderedBody } from "./ReportsView";

const html = (el: React.ReactElement) => renderToStaticMarkup(el);
const VIEW = readFileSync(join(__dirname, "ReportsView.tsx"), "utf-8");
const PAGE = readFileSync(join(__dirname, "..", "page.tsx"), "utf-8");

const ROW: WaitingRow = {
  id: "q-old",
  task_number: 12,
  title: "Choose the nozzle",
  project_id: "p1",
  project_name: "Rig",
  since: "2026-10-22T06:00:00+00:00",
  age_hours: 72,
  agent: "Claude Code",
  client_via: "mcp:claude.ai:claude-code",
  member: "ana@example.test",
  member_name: "Ana Rao",
};

const BUSY: WaitingReport = {
  summary: "2 agent questions wait for an answer. The oldest is 3 days old.",
  agent_runs_seen: true,
  questions: [ROW, { ...ROW, id: "q-new", title: "Pick a port", age_hours: 2, member_name: "Bo" }],
  questions_total: 2,
  reviews: [],
  reviews_total: 0,
  handoffs: [
    {
      ...ROW,
      id: "h1",
      title: "Wire the board",
      agent: "coder",
      client_via: null,
      member: null,
      member_name: null,
      blocker_id: "b1",
      blocker_number: 3,
      blocker_title: "Order steel",
      agent_side: "blocked",
      person: "dee@example.test",
      person_name: "Dee",
    },
  ],
  handoffs_total: 1,
  answer_time: { asked: 3, answered: 2, unanswered: 1, median_hours: 3, p90_hours: 5 },
  max_rows: 20,
};

const QUIET: WaitingReport = {
  ...BUSY,
  summary: "Nobody is waiting on a person.",
  agent_runs_seen: false,
  questions: [],
  questions_total: 0,
  handoffs: [],
  handoffs_total: 0,
  answer_time: { asked: 0, answered: 0, unanswered: 0, median_hours: null, p90_hours: null },
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
  const panel = html(createElement(WaitingPanel, { data: BUSY }));

  it("says the server's summary before any list", () => {
    const at = panel.indexOf(BUSY.summary);
    expect(at).toBeGreaterThan(-1);
    expect(at).toBeLessThan(panel.indexOf("Agent questions with no answer"));
  });

  it("says the answer time with its denominator", () => {
    expect(panel).toContain("Answer time: median 3 hours, p90 5 hours, over 2 answered.");
  });

  it("draws no list that has no row", () => {
    expect(panel).not.toContain("Agent work waiting for a review");
    expect(panel).toContain("Handoffs that block work");
  });
});

describe("each row opens its task (RA-3 done-when)", () => {
  const panel = html(createElement(WaitingPanel, { data: BUSY }));

  it("is a real link to the task's deep link", () => {
    expect(panel).toContain('href="/projects?task=q-old"');
    expect(panel).toContain('href="/projects?task=h1"');
    expect(panel).toContain('title="Open #12 Choose the nozzle"');
  });

  it("hands a plain click to the page's opener, through one context", () => {
    // A static render cannot click. The source holds the two halves: the
    // panel reads the context, and the view provides the page's opener.
    const panels = readFileSync(join(__dirname, "AnalyticsPanels.tsx"), "utf-8");
    expect(panels).toContain("const open = useContext(OpenTaskContext);");
    expect(panels).toMatch(/onActivate=\{\(\) => \(open \? open\(line\.id\)/);
    expect(VIEW).toContain("<OpenTaskContext.Provider value={onOpenTask ?? null}>");
    expect(PAGE).toMatch(/onOpenTask=\{\(taskId\) => router\.push\(`\/projects\?task=\$\{encodeURIComponent\(taskId\)\}`\)\}/);
  });

  it("renders under a provider too", () => {
    const opened: string[] = [];
    const under = html(
      createElement(OpenTaskContext.Provider, { value: (id: string) => opened.push(id) },
        createElement(WaitingPanel, { data: BUSY }))
    );
    expect(under).toContain('href="/projects?task=q-old"');
  });
});

describe("§6.8 E1 and E2: no rank, and the agent names its person", () => {
  const panel = html(createElement(WaitingPanel, { data: BUSY }));

  it("keeps the server's order: the oldest question first", () => {
    expect(panel.indexOf("Choose the nozzle")).toBeLessThan(panel.indexOf("Pick a port"));
  });

  it("names the agent and the member on each question", () => {
    expect(panel).toContain("Claude Code for Ana Rao");
    expect(panel).toContain("Claude Code for Bo");
  });

  it("names the in-app agent and the person who holds the blocker", () => {
    expect(panel).toContain("coder · 3 days · Blocked by Dee: Order steel");
  });

  it("puts a title on each count", () => {
    expect(panel).toMatch(/title="2 in total, the longest wait first"/);
  });
});

describe("the Overview hides the panel when the scope has no agent run", () => {
  it("draws no Waiting card in the live grid", () => {
    const grid = html(
      createElement(RenderedBody, {
        body: body({ stuck: STUCK, waiting: QUIET }),
        layout: "grid",
        showTitle: false,
      })
    );
    expect(grid).not.toContain("Waiting on people");
    expect(grid).toContain("Stuck work");
  });

  it("a saved report keeps it, as one clear row", () => {
    const column = html(createElement(RenderedBody, { body: body({ stuck: STUCK, waiting: QUIET }) }));
    expect(column).toContain("Waiting on people");
    expect(column).toContain("Nobody is waiting on a person.");
  });

  it("the live grid draws it when agents work in the scope", () => {
    const grid = html(
      createElement(RenderedBody, {
        body: body({ stuck: STUCK, waiting: BUSY }),
        layout: "grid",
        showTitle: false,
      })
    );
    expect(grid).toContain("Waiting on people");
    expect(grid).toContain(BUSY.summary);
  });
});
