/**
 * WS-27bn RA-6 — the Forecast and Open work panels, the markup half.
 *
 * Spec: `project-docs/specs/projects_reports.md` §6.8 B, E and F, §8 RA-6.
 * The vitest environment has no DOM, so these tests render the panels with
 * `renderToStaticMarkup`. The pure rules are `lib/outlook.test.ts` and
 * `lib/needsOwner.test.ts`.
 *
 * * The Forecast prints the server's sentence first, draws the two dates as
 *   one band, and gives its rule to a keyboard (`InfoTip`).
 * * "Nothing sized" no longer draws: the estimates add a line only when
 *   they exist.
 * * "Needs an owner" is the first group of Open work when it leads, and the
 *   last when it does not. Each row opens its task.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { LoadReport, NeedsOwner, OutlookReport, RenderedReportBody } from "../lib/api";
import { LoadPanel, OpenTaskContext, OutlookPanel } from "./AnalyticsPanels";
import { RenderedBody } from "./ReportsView";

const html = (el: React.ReactElement) => renderToStaticMarkup(el);

const OUTLOOK: OutlookReport = {
  project_id: "p1",
  scope: "node",
  weeks: 6,
  velocity: {
    weeks_sampled: 6,
    finished_per_week: 3.5,
    created_per_week: 0.5,
    net_per_week: 3,
    remaining_tasks: 26,
    weeks_remaining: 9,
    finish_date: "2026-12-23",
    verdict: "converging",
    weeks_with_finishes: 5,
    finished_in_window: 21,
    min_finished: 3,
    min_finish_weeks: 2,
    trials: 2000,
    cap_weeks: 52,
    reason: null,
    converge_per_week: null,
    p50_weeks: 9,
    p50_date: "2026-12-23",
    p85_weeks: 12,
    p85_date: "2027-01-13",
    summary:
      "Half of 2,000 runs finish the 26 open tasks by 23 Dec 2026, and 85% finish by 13 Jan 2027.",
  },
  capacity: {
    hours_per_week: 40,
    hours_left: 0,
    estimate_coverage: 0,
    weeks_remaining: null,
    finish_date: null,
    verdict: "no_estimates",
  },
  plan: { planned_finish: null, dated: 0, tasks: 26, slip_days: null },
  people: { holding_open_work: 1, in_directory: 1, hours_per_week: 40, absences_applied: true },
};

const NOT_CONVERGING: OutlookReport = {
  ...OUTLOOK,
  velocity: {
    ...OUTLOOK.velocity,
    verdict: "not_converging",
    reason: "arrivals",
    created_per_week: 5.2,
    finished_per_week: 4.2,
    converge_per_week: 6.6,
    p50_weeks: null,
    p50_date: null,
    p85_weeks: null,
    p85_date: null,
    weeks_remaining: null,
    finish_date: null,
    summary:
      "Not converging: work arrives at least as fast as it finishes (5.2 a week against 4.2). To converge, it needs 6.6 finished tasks a week.",
  },
};

const THIN: OutlookReport = {
  ...NOT_CONVERGING,
  velocity: {
    ...NOT_CONVERGING.velocity,
    verdict: "no_history",
    reason: null,
    converge_per_week: null,
    finished_in_window: 2,
    weeks_with_finishes: 1,
    summary:
      "Not enough history to forecast: 2 tasks finished in 1 week of the last 6. A forecast needs 3 finished tasks, in 2 weeks or more.",
  },
};

/** RA-6 fix round 1: past the cap, the panel names no rate. */
const PAST_CAP: OutlookReport = {
  ...NOT_CONVERGING,
  velocity: {
    ...NOT_CONVERGING.velocity,
    reason: "cap",
    converge_per_week: null,
    created_per_week: 0.67,
    finished_per_week: 2.5,
    summary:
      "Not converging: at the current rate, the 85% date is more than 52 weeks away. A steadier flow of finished tasks, or smaller tasks, brings it closer.",
  },
};

describe("OutlookPanel — the forecast by count", () => {
  it("prints the sentence first and draws the two dates as one band", () => {
    const out = html(createElement(OutlookPanel, { data: OUTLOOK }));
    expect(out).toContain("On track for 23 Dec 2026");
    expect(out).toContain("Half of 2,000 runs finish the 26 open tasks by 23 Dec 2026");
    expect(out).toContain('aria-label="50% by 23 Dec 2026, 85% by 13 Jan 2027."');
    expect(out).toContain("50% by 23 Dec 2026</span>");
    expect(out).toContain("85% by 13 Jan 2027</span>");
    // The explicit ramp slot, never a raw palette class. RA-6 fix round 1:
    // the 50% part is solid, and the 85% part an outline in the full hue.
    expect(out).toContain("bg-cat-10");
    // The bar's 85% part, and not only the legend dot (fix round 1).
    expect(out).toContain('class="absolute inset-y-0 rounded-r-full border-2 border-current text-cat-10"');
    expect(out).not.toMatch(/opacity-50 bg-cat-10/);
    // The rule is a button, so a keyboard and a touch reach it.
    expect(out).toContain('aria-label="How the forecast is counted"');
  });

  it("⚠️ draws no Nothing sized: estimates add a line only when they exist", () => {
    const out = html(createElement(OutlookPanel, { data: OUTLOOK }));
    expect(out).not.toContain("Nothing sized");
    expect(out).not.toContain("If the plan holds");
    const sized = html(
      createElement(OutlookPanel, {
        data: { ...OUTLOOK, capacity: { ...OUTLOOK.capacity, verdict: "ok", finish_date: "2027-01-01", weeks_remaining: 10, estimate_coverage: 1 } },
      })
    );
    expect(sized).toContain("If the plan holds");
  });

  it("not converging says why and the rate that would converge it, with no band", () => {
    const out = html(createElement(OutlookPanel, { data: NOT_CONVERGING }));
    expect(out).toContain("Not converging");
    expect(out).toContain("To converge, it needs 6.6 finished tasks a week.");
    expect(out).not.toMatch(/aria-label="50% by/);
    expect(out).toContain('aria-label="How the forecast is counted"');
    for (const word of ["undefined", "NaN"]) expect(out).not.toContain(word);
  });

  it("not enough history says the rule, with no band", () => {
    const out = html(createElement(OutlookPanel, { data: THIN }));
    expect(out).toContain("Not enough history");
    expect(out).toContain("A forecast needs 3 finished tasks, in 2 weeks or more.");
    expect(out).not.toMatch(/aria-label="50% by/);
  });

  it("⚠️ past the cap it names no rate, so it never asks for a rate the team has", () => {
    const out = html(createElement(OutlookPanel, { data: PAST_CAP }));
    expect(out).toContain("Not converging");
    expect(out).toContain("the 85% date is more than 52 weeks away");
    expect(out).not.toContain("To converge");
    expect(out).not.toMatch(/aria-label="50% by/);
  });
});

function owner(over: Partial<NeedsOwner> = {}): NeedsOwner {
  return {
    total: 25,
    leads: true,
    max_rows: 20,
    tasks: [
      { id: "t1", task_number: 12, title: "Choose the nozzle", project_id: "p1", due_at: null },
      { id: "t2", task_number: 13, title: "Order steel", project_id: "p1", due_at: "2026-11-02T00:00:00+00:00" },
    ],
    ...over,
  };
}

const LOAD: LoadReport = {
  project_id: "p1",
  scope: "node",
  total_tasks: 26,
  people_total: 2,
  people: [
    { assignee: null, open_tasks: 25, overdue: 0, due_next_7d: 0, later: 25 },
    { assignee: "ana@example.test", open_tasks: 1, overdue: 0, due_next_7d: 1, later: 0 },
  ],
  needs_owner: owner(),
};

describe("LoadPanel — Needs an owner", () => {
  it("is the first group when it leads, with its count and rule", () => {
    const out = html(createElement(LoadPanel, { data: LOAD }));
    const group = out.indexOf("Needs an owner");
    expect(group).toBeGreaterThan(-1);
    // Before the people rows.
    expect(group).toBeLessThan(out.indexOf("Unassigned"));
    expect(out).toContain("25 of 26 open tasks have no owner.");
    expect(out).toContain('aria-label="How Needs an owner is counted"');
    expect(out).toContain("…and 23 more");
  });

  it("each row opens its task", () => {
    const opened: string[] = [];
    const out = html(
      createElement(
        OpenTaskContext.Provider,
        { value: (id: string) => opened.push(id) },
        createElement(LoadPanel, { data: LOAD })
      )
    );
    expect(out).toContain('href="/projects?task=t1"');
    expect(out).toContain("#12 Choose the nozzle");
    expect(out).toContain("Due 2 Nov 2026");
  });

  it("is the last group when it does not lead", () => {
    const out = html(
      createElement(LoadPanel, {
        data: { ...LOAD, needs_owner: owner({ total: 1, leads: false, tasks: owner().tasks.slice(0, 1) }) },
      })
    );
    expect(out.indexOf("Needs an owner")).toBeGreaterThan(out.indexOf("ana@example.test"));
    expect(out).toContain("1 of 26 open tasks has no owner.");
  });

  it("draws no group when none needs an owner, or an older server sent none", () => {
    expect(html(createElement(LoadPanel, { data: { ...LOAD, needs_owner: owner({ total: 0, tasks: [] }) } }))).not.toContain("Needs an owner");
    const { needs_owner: _gone, ...older } = LOAD;
    expect(html(createElement(LoadPanel, { data: older }))).not.toContain("Needs an owner");
  });
});

/** A report body with one `load` section, for the table under the panel. */
function reportBody(load: LoadReport): RenderedReportBody {
  return {
    report: {
      id: "r-1",
      project_id: null,
      scope: "portfolio",
      name: "Overview",
      config: { weeks: 12, skip_current_week: false, include_subtree: true, sections: ["load"] },
      created_by: "a@x.io",
      created_at: "2026-10-25T00:00:00Z",
    },
    period_start: "2026-08-04",
    period_end: "2026-10-25",
    sections: { load },
  } as unknown as RenderedReportBody;
}

/** The folded table of a report, the part after the panel. */
function tableOf(out: string): string {
  const at = out.indexOf('aria-label="Open work, as a table');
  expect(at, "the table of Open work").toBeGreaterThan(-1);
  return out.slice(at);
}

describe("the table of Open work (RA-6 fix round 1)", () => {
  it("puts Needs an owner first when it leads, as the panel does", () => {
    const table = tableOf(html(createElement(RenderedBody, { body: reportBody(LOAD) })));
    const owner = table.indexOf("Needs an owner");
    expect(owner).toBeGreaterThan(-1);
    expect(owner).toBeLessThan(table.indexOf("Unassigned"));
    expect(table).toContain("#12 Choose the nozzle");
  });

  it("puts it last when it does not lead", () => {
    const table = tableOf(
      html(
        createElement(RenderedBody, {
          body: reportBody({ ...LOAD, needs_owner: owner({ total: 1, leads: false, tasks: owner().tasks.slice(0, 1) }) }),
        })
      )
    );
    expect(table.indexOf("Needs an owner")).toBeGreaterThan(table.indexOf("Unassigned"));
  });
});
