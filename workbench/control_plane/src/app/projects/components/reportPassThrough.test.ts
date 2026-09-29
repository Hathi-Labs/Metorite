/**
 * WS-27bn R5f repair round 1: the report sections carry what the routes carry.
 *
 * Spec: `project-docs/specs/projects_reports.md` §8 R5f, as built in round 1.
 * `render_body` now passes the effort, the weekly throughput figures and the
 * blocked list through. These tests prove that the adapters copy them, and
 * that each panel then draws them, as it did in the Analytics app.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { PreviewReportBody } from "../lib/api";
import { loadPanelData, stuckPanelData, throughputPanelData } from "../lib/reportPanels";
import { LoadPanel, StuckPanel, ThroughputPanel } from "./AnalyticsPanels";

const FRAME = {
  report: {
    id: null,
    project_id: null,
    name: "Overview",
    scope: "portfolio",
    config: { weeks: 12, skip_current_week: false, include_subtree: true, sections: [] },
  },
  period_start: "2026-07-06",
  period_end: "2026-09-27",
} as unknown as PreviewReportBody;

const EFFORT = {
  left_mins: 600,
  left_estimated: 4,
  left_tasks: 5,
  spent_mins: 120,
  spent_estimated: 2,
  spent_tasks: 2,
  basis: "estimate" as const,
};

describe("load: the effort line draws again", () => {
  const section = {
    people: [
      { assignee: "a@x.io", open_tasks: 3, overdue: 1, due_next_7d: 1, later: 1, est_mins: 300, estimated: 2 },
    ],
    total_tasks: 5,
    people_total: 1,
    effort: EFFORT,
  };

  it("copies people_total, effort and the row estimates", () => {
    const data = loadPanelData(section, FRAME);
    expect(data.people_total).toBe(1);
    expect(data.effort).toEqual(EFFORT);
    expect(data.people[0].est_mins).toBe(300);
    expect(data.people[0].estimated).toBe(2);
  });

  it("LoadPanel draws the effort left", () => {
    const html = renderToStaticMarkup(createElement(LoadPanel, { data: loadPanelData(section, FRAME) }));
    expect(html).toMatch(/10h/);
    expect(html).toContain("left");
  });

  it("a body from before R5f keeps the keys absent", () => {
    const { people_total: _p, effort: _e, ...old } = section;
    const data = loadPanelData(old, FRAME);
    expect("effort" in data).toBe(false);
    expect("people_total" in data).toBe(false);
  });
});

describe("stuck: the blocked list draws again", () => {
  const section = {
    overdue: [{ project_id: "p1", name: "Printer X2", overdue: 1 }],
    overdue_total: 1,
    stale: [{ band: "under_7d", n: 2 }],
    blocked: [{ id: "t1", title: "Fit the extruder", task_number: 7, due_at: null }],
    blocked_total: 1,
  };

  it("copies blocked and blocked_total", () => {
    const data = stuckPanelData(section, FRAME);
    expect(data.blocked).toEqual(section.blocked);
    expect(data.blocked_total).toBe(1);
  });

  it("StuckPanel names the blocked task", () => {
    const html = renderToStaticMarkup(createElement(StuckPanel, { data: stuckPanelData(section, FRAME) }));
    expect(html).toContain("Blocked: 1 of 1");
    expect(html).toContain("Fit the extruder");
  });
});

describe("throughput: the weekly figures and the Finished cell", () => {
  const section = {
    series: [
      { week_start: "2026-09-14", completed: 2, cancelled: 1, measured: 2, no_start: 0, median_hours: 20, p90_hours: 30 },
    ],
    completed: 2,
    median_hours: 20,
    measured: 2,
    p90_hours: 30,
    no_start: 0,
    cancelled: 1,
  };

  it("copies each week and the summary's completed count", () => {
    const data = throughputPanelData(section, FRAME);
    expect(data.series[0]).toEqual(section.series[0]);
    expect(data.summary.completed).toBe(2);
  });

  it("ThroughputPanel draws the Finished cell", () => {
    const html = renderToStaticMarkup(
      createElement(ThroughputPanel, { data: throughputPanelData(section, FRAME) })
    );
    expect(html).toContain("Finished");
  });
});
