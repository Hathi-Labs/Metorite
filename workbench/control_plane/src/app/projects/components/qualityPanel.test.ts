/**
 * WS-27bn RA-5 — "Quality", the markup half.
 *
 * Spec: `project-docs/specs/projects_reports.md` §6.8 B and E, §8 RA-5. The
 * vitest environment has no DOM, so these tests render the panel with
 * `renderToStaticMarkup`. The pure rules are `lib/qualityReport.test.ts`.
 *
 * Done-when of RA-5: "the merge rate shows a trend, with no 100% target in
 * its text". Each figure's rule is reachable by keyboard and touch
 * (`InfoTip`), and each failing row opens its task.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { QualityReport, RenderedReportBody } from "../lib/api";
import { QualityPanel } from "./AnalyticsPanels";
import { RenderedBody } from "./ReportsView";

const html = (el: React.ReactElement) => renderToStaticMarkup(el);

const ROW = {
  project_id: "p1",
  project_name: "Rig",
  since: null,
  url: null,
  pr_title: null,
  pr_state: "open",
  agent: "Claude Code",
  client_via: "mcp:claude.ai:claude-code",
  member: "ana@example.test",
  member_name: "Ana Rao",
};

const GROUP = {
  finished: 10,
  undone: 2,
  reopened: 3,
  finished_before: 8,
  undone_before: 1,
  reopened_before: 1,
  rate: 0.2,
  rate_before: 0.125,
  trend: "up" as const,
};

const BUSY: QualityReport = {
  summary:
    "8 agent pull requests merged and 2 closed with no merge in the last 14 days." +
    " The merge rate is 80%, up from 70% in the 14 days before." +
    " 3 agent pull requests are open now, and 2 fail their checks.",
  on: true,
  quality_seen: true,
  days: 14,
  prs: {
    merged: 8,
    closed: 2,
    rate: 0.8,
    merged_before: 7,
    closed_before: 3,
    rate_before: 0.7,
    trend: "up",
    open: 3,
  },
  checks: {
    failing_total: 2,
    failing: [
      { ...ROW, id: "q-old", task_number: 31, title: "Fix the checks", age_hours: 50, ref: "acme/rig#12" },
      { ...ROW, id: "q-new", task_number: 32, title: "Mend the build", age_hours: 3, ref: "acme/rig#14", member_name: "Bo" },
    ],
    merged_red: 1,
    merged_red_before: 0,
  },
  rework: {
    reopened: { agents: GROUP, people: { ...GROUP, finished: 20, undone: 1, reopened: 1, undone_before: 2, rate: 0.05 } },
    retried: { failed: 5, retried: 3, failed_before: 4, retried_before: 1, rate: 0.6, rate_before: 0.25, trend: "up" },
  },
  max_rows: 5,
};

const QUIET: QualityReport = {
  ...BUSY,
  summary: "No agent pull request merged or closed in the last 14 days.",
  quality_seen: false,
  prs: { merged: 0, closed: 0, rate: null, merged_before: 0, closed_before: 0, rate_before: null, trend: null, open: 0 },
  checks: { failing_total: 0, failing: [], merged_red: 0, merged_red_before: 0 },
  rework: {
    reopened: {
      agents: { ...GROUP, finished: 0, undone: 0, reopened: 0, undone_before: 0, rate: null, rate_before: null, trend: null },
      people: { ...GROUP, finished: 0, undone: 0, reopened: 0, undone_before: 0, rate: null, rate_before: null, trend: null },
    },
    retried: { failed: 0, retried: 0, failed_before: 0, retried_before: 0, rate: null, rate_before: null, trend: null },
  },
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
      created_at: "2026-10-26T00:00:00Z",
    },
    period_start: "2026-08-04",
    period_end: "2026-10-26",
    sections,
  } as unknown as RenderedReportBody;
}

describe("the panel opens with one plain sentence (§6.8 E5)", () => {
  const panel = html(createElement(QualityPanel, { data: BUSY }));

  it("says the server's summary before any figure", () => {
    expect(panel).toContain("Quality");
    expect(panel.indexOf(BUSY.summary)).toBeGreaterThan(-1);
    expect(panel.indexOf(BUSY.summary)).toBeLessThan(panel.indexOf("Merge rate"));
  });

  it("draws the merge rate with its trend, and no 100% target (the done-when)", () => {
    expect(panel).toContain(">80%<");
    expect(panel).toContain("up from 70%");
    expect(panel).toContain('title="8 merged of 10 agent pull requests that merged or closed in the last 14 days"');
    expect(panel).not.toMatch(/\b(goal|target)\b/i);
    expect(panel).not.toMatch(/100\s*%/);
  });

  it("draws the outcomes with a legend, and the window before in words", () => {
    for (const label of ["Merged", "Closed, not merged", "Open now"]) expect(panel).toContain(label);
    // The hue is never alone: each legend dot sits beside its label.
    expect(panel).toMatch(/bg-cat-3[^>]*aria-hidden="true"[^>]*><\/span>Merged/);
    expect(panel).toContain("Before: 7 merged · 3 closed, not merged");
  });

  it("draws the rework of the two groups and never one person", () => {
    expect(panel).toContain("Reopened after done");
    expect(panel).toContain(">2 of 10<");
    expect(panel).toContain(">1 of 20<");
    // The distinct tasks are said as tasks, and the rule says what is counted.
    expect(panel).toContain(">3 tasks reopened<");
    expect(panel).toContain(">1 task reopened<");
    expect(panel).toContain("were reopened later");
    expect(panel).toContain("before: 1 of 8");
    expect(panel).toContain("Agents");
    expect(panel).toContain("People");
    expect(panel).toContain("3 of 5 failed runs got a new run");
  });

  it("says the merged while red count", () => {
    expect(panel).toContain("Merged while red");
  });
});

describe("each figure's rule is reachable by keyboard and touch (InfoTip)", () => {
  const panel = html(createElement(QualityPanel, { data: BUSY }));

  it("puts an InfoTip beside each figure that has a rule", () => {
    for (const label of [
      "How the merge rate is counted",
      "How failing checks are counted",
      "How reopened tasks are counted",
      "How retried runs are counted",
      "How merged while red is counted",
    ]) {
      expect(panel).toContain(`aria-label="${label}"`);
    }
  });

  it("a closed tip is a button that says it opens", () => {
    const button = panel.match(/<button[^>]*aria-label="How the merge rate is counted"[^>]*>/);
    expect(button).not.toBeNull();
    expect(button![0]).toContain('aria-expanded="false"');
  });
});

describe("each failing row opens its task, and names the agent and its member (§6.8 E2)", () => {
  const panel = html(createElement(QualityPanel, { data: BUSY }));

  it("is a real link to the task's deep link, oldest first", () => {
    expect(panel).toContain('href="/projects?task=q-old"');
    expect(panel).toContain('href="/projects?task=q-new"');
    expect(panel.indexOf("q-old")).toBeLessThan(panel.indexOf("q-new"));
  });

  it("names the agent for its member and the pull request on each row", () => {
    expect(panel).toContain("Claude Code for Ana Rao · acme/rig#12 · linked 2 days ago");
    expect(panel).toContain("Claude Code for Bo · acme/rig#14 · linked 3 hours ago");
  });

  it("reads the one opener of a report row", () => {
    const panels = readFileSync(join(__dirname, "AnalyticsPanels.tsx"), "utf-8");
    const start = panels.indexOf("export function QualityPanel(");
    const fn = panels.slice(start, panels.indexOf("\n}\n", start));
    expect(fn).toContain("const open = useContext(OpenTaskContext);");
    expect(fn).toContain("<WaitingTaskRow key={r.key} line={r} open={open} />");
  });

  it("draws no failing list when nothing fails", () => {
    const calm = html(
      createElement(QualityPanel, {
        data: { ...BUSY, checks: { ...BUSY.checks, failing_total: 0, failing: [], merged_red: 0 } },
      })
    );
    expect(calm).not.toContain("Checks fail now");
    expect(calm).not.toContain("Merged while red");
  });
});

describe("the Overview hides the panel when nothing is in the scope (RA-5 done-when)", () => {
  it("draws no Quality card in the live grid", () => {
    const grid = html(
      createElement(RenderedBody, {
        body: body({ stuck: STUCK, quality: QUIET }),
        layout: "grid",
        showTitle: false,
      })
    );
    expect(grid).not.toContain("Quality");
    expect(grid).toContain("Stuck work");
  });

  it("a saved report keeps it, as one clear row", () => {
    const column = html(createElement(RenderedBody, { body: body({ stuck: STUCK, quality: QUIET }) }));
    expect(column).toContain("Quality");
    expect(column).toContain("No agent pull request, reopen or failed run.");
  });

  it("the live grid draws it when agent pull requests are in the scope", () => {
    const grid = html(
      createElement(RenderedBody, {
        body: body({ stuck: STUCK, quality: BUSY }),
        layout: "grid",
        showTitle: false,
      })
    );
    expect(grid).toContain("Quality");
    expect(grid).toContain(BUSY.summary);
  });

  it("the table under the panel names every failing pull request", () => {
    const column = html(createElement(RenderedBody, { body: body({ quality: BUSY }) }));
    expect(column).toContain("Merge rate 80%, up from 70%");
    expect(column).toContain("#31 Fix the checks");
    expect(column).toContain("#32 Mend the build");
  });
});
