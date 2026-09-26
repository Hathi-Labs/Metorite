/**
 * WS-27bn R2b — the visual report body, rendered.
 *
 * Spec: `project-docs/specs/projects_reports.md` §8 R2b, "Done when". This
 * file is the fence the spec names, in three parts:
 *
 *   (a) RENDER. `RenderedBody` draws each of the nine sections as its
 *       Analytics panel, with its table folded under it (WS-27bn R3a adds
 *       `outlook`, and the ageing bands in `stuck`. R3b adds `rebalance`,
 *       and its HR hint. R3c adds `hygiene`). A sentinel figure
 *       per section appears in the panel AND in the table ("agree"). A body
 *       in the report's own shape carries no "undefined", no "NaN" and no
 *       "No open work in this scope" unless every band is zero
 *       ("degrade"). A tile hides when its section is not in the report.
 *   (b) SOURCE. `ReportsView.tsx` imports the nine panels from
 *       `./AnalyticsPanels` and renders each one. It holds no inline width
 *       or height style and no function named for a bar or a chart. A
 *       self-test proves a hand-drawn bar fires the fence.
 *   (c) ADAPTERS. `lib/reportPanels.ts` maps each section and computes
 *       nothing, with the arithmetic check of `capacity.test.ts`.
 *
 * Rendered through `react-dom/server` in the node environment, as
 * `TimelineView.test.ts` does. That proves the markup, not the layout: the
 * look at 390 px and in light mode is the visual review's job.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { CapacityRow, HygieneKind, RenderedReportBody } from "../lib/api";
import {
  capacityPanelData,
  conflictsPanelData,
  finishedPanelData,
  hygienePanelData,
  loadPanelData,
  outlookPanelData,
  rebalancePanelData,
  reportTiles,
  stuckPanelData,
  throughputPanelData,
} from "../lib/reportPanels";
import { rebalancePickups, rebalanceTasks } from "../lib/rebalance";
import {
  CapacityPanel,
  OutlookPanel,
  StuckPanel,
  ThroughputPanel,
} from "./AnalyticsPanels";
import { RenderedBody } from "./ReportsView";

type Sections = RenderedReportBody["sections"];

const capacityRow = (over: Partial<CapacityRow> = {}): CapacityRow => ({
  assignee: "cy@example.test",
  name: "Cy",
  kind: "person",
  in_directory: true,
  open_tasks: 7501,
  overdue: 1,
  due_next_7d: 2,
  later: 3,
  estimated_hours_left: 12,
  estimated: 4,
  all_work: { open_tasks: 9, overdue: 1, unestimated: 0, in_progress: 2 },
  hours_basis: true,
  working_hours_horizon: 40,
  committed_hours_horizon: 46,
  spare_hours_horizon: 0,
  pill: "overloaded",
  ...over,
});

/** The outlook route's body, converging and 23 days late (WS-27bn R3a). */
const OUTLOOK: NonNullable<Sections["outlook"]> = {
  project_id: null,
  scope: "portfolio",
  weeks: 6,
  velocity: {
    weeks_sampled: 6,
    finished_per_week: 4.2,
    created_per_week: 1.1,
    net_per_week: 3.1,
    remaining_tasks: 7701,
    weeks_remaining: 10,
    finish_date: "2027-03-12",
    verdict: "converging",
  },
  capacity: {
    hours_per_week: 60,
    hours_left: 240,
    estimate_coverage: 1,
    weeks_remaining: 4,
    finish_date: "2026-10-15",
    verdict: "ok",
  },
  plan: {
    planned_finish: "2027-02-17T00:00:00+00:00",
    dated: 31,
    tasks: 31,
    slip_days: 23,
  },
  people: {
    holding_open_work: 5,
    with_stated_capacity: 5,
    with_schedule_only: 0,
    hours_per_week: 60,
    leaving_within_90d: 0,
  },
};

/** The same scope with no forecast date: a finding, not a date. */
const NOT_CONVERGING: NonNullable<Sections["outlook"]> = {
  ...OUTLOOK,
  velocity: {
    ...OUTLOOK.velocity,
    net_per_week: -1,
    created_per_week: 5.2,
    weeks_remaining: null,
    finish_date: null,
    verdict: "not_converging",
  },
  plan: { ...OUTLOOK.plan, planned_finish: null, dated: 0, slip_days: null },
};

/** The rebalance route's body, as a report copies it (WS-27bn R3b). */
const REBALANCE: NonNullable<Sections["rebalance"]> = {
  project_id: null,
  scope: "portfolio",
  include_subtree: true,
  horizon_days: 14,
  hr_visible: true,
  window: { starts_on: "2026-09-24", ends_on: "2026-10-07", days: 14 },
  hours_scope: "all_visible_work",
  partial: false,
  at_risk: [
    {
      task_id: "t9",
      title: "Weld the gantry 7801",
      project_name: "Rig",
      due_on: "2026-09-27",
      shortfall_hours: 12,
      holder: { person_id: "p-hal", name: "Hal", email: "hal@example.test" },
      holders: [{ person_id: "p-hal", name: "Hal", email: "hal@example.test" }],
      candidates: [
        { person_id: "p-ivy", name: "Ivy", email: "ivy@example.test", skill_points: 3,
          matched_skills: ["weld"], spare_hours: 20, away: null, rank: 60 },
        { person_id: "p-bo", name: "Bo", email: "bo@example.test", skill_points: 2,
          matched_skills: ["weld"], spare_hours: 10, away: null, rank: 20 },
      ],
      hours_basis: true,
    },
  ],
  at_risk_total: 11,
  pickups: [
    {
      person_id: "p-ivy",
      name: "Ivy",
      email: "ivy@example.test",
      tasks: [
        { task_id: "t10", title: "Weld a jig", project_name: "Rig",
          kind: "unassigned", skill_points: 3, matched_skills: ["weld"] },
      ],
    },
  ],
  pickups_total: 25,
  idle_total: 26,
  truncated: true,
};

/** The same scope for a reader without `admin:members:read`: no lists. */
const REBALANCE_HIDDEN: NonNullable<Sections["rebalance"]> = {
  project_id: null,
  scope: "portfolio",
  include_subtree: true,
  horizon_days: 14,
  hr_visible: false,
  window: { starts_on: "2026-09-24", ends_on: "2026-10-07", days: 14 },
};

/** Titles of one kind: seven, so the panel prints five and "…and M more". */
const hygieneTitles = (kind: HygieneKind, n: number, first: string) =>
  Array.from({ length: n }, (_, i) => ({
    kind,
    id: `${kind}-${i}`,
    title: i === 0 ? first : `${kind} task ${i}`,
    task_number: i,
    project_id: "p1",
    project_name: "Rig",
    due_at: null,
    updated_at: "2026-09-01T08:00:00+00:00",
  }));

/** `hygiene_body`, as a report carries it (WS-27bn R3c). */
const HYGIENE: NonNullable<Sections["hygiene"]> = {
  open_total: 9901,
  stale_days: 14,
  by_kind: {
    no_assignee: 7901,
    no_due_date: 7,
    no_estimate: 0,
    stale_in_progress: 1,
  },
  rows: [
    ...hygieneTitles("no_assignee", 7, "Order steel 7901"),
    ...hygieneTitles("no_due_date", 7, "Paint the frame"),
    ...hygieneTitles("stale_in_progress", 1, "Weld the base"),
  ],
};

/**
 * Every section, in the shape `render_body` sends since R3a. Each carries one
 * SENTINEL figure (71xx to 77xx) that nothing else in the body repeats.
 */
const SECTIONS: Required<Sections> = {
  finished: {
    projects: [
      {
        project_id: "p1",
        name: "Mobile App",
        completed: 7101,
        cancelled: 2,
        median_hours: 30,
      },
      { project_id: "p2", name: "Billing", completed: 5, cancelled: 0, median_hours: null },
    ],
    total_completed: 9102,
    total_cancelled: 2,
  },
  throughput: {
    series: [
      { week_start: "2026-09-07", completed: 3 },
      { week_start: "2026-09-14", completed: 7201 },
    ],
    median_hours: 27,
    measured: 15,
    p90_hours: 80,
    no_start: 1,
    cancelled: 2,
  },
  load: {
    people: [
      { assignee: "ana@example.test", open_tasks: 7401, overdue: 2, due_next_7d: 3, later: 7396 },
      { assignee: null, open_tasks: 4, overdue: 0, due_next_7d: 1, later: 3 },
    ],
    total_tasks: 9401,
  },
  capacity: {
    people: [capacityRow(), capacityRow({ assignee: null, name: null, kind: "unassigned", open_tasks: 2, all_work: undefined, hours_basis: undefined })],
    people_total: 1,
    total_tasks: 9501,
    hr_visible: true,
    horizon_days: 14,
    windows: {
      week: { starts_on: "2026-09-21", ends_on: "2026-09-27", used_for: "pill" },
      horizon: { starts_on: "2026-09-24", ends_on: "2026-10-07", days: 14, used_for: "spare" },
    },
  },
  outlook: OUTLOOK,
  stuck: {
    overdue: [
      { project_id: "p1", name: "Mobile App", overdue: 7301 },
      { project_id: "p2", name: "Billing", overdue: 1 },
    ],
    overdue_total: 9302,
    // WS-27bn R3a. The route's bands, so the panel draws its band chart.
    stale: [
      { band: "under_7d", n: 5 },
      { band: "days_7_to_14", n: 3 },
      { band: "days_14_to_30", n: 0 },
      { band: "over_30d", n: 2 },
    ],
  },
  conflicts: {
    rows: [
      {
        kind: "blocker_late",
        severity: "high",
        task_ids: ["t1"],
        people: [],
        sentence: "Order steel 7601 was due and is still open.",
        due_on: "2026-09-20",
      },
    ],
    total: 3,
    by_kind: { blocker_late: 3 },
    hr_visible: true,
    horizon_days: 14,
    window: { starts_on: "2026-09-24", ends_on: "2026-10-07", days: 14, ignored_by: [] },
  },
  rebalance: REBALANCE,
  hygiene: HYGIENE,
};

const SENTINEL: Record<keyof Sections, string> = {
  finished: "7101",
  throughput: "7201",
  outlook: "7701",
  stuck: "7301",
  load: "7401",
  capacity: "7501",
  conflicts: "7601",
  rebalance: "7801",
  hygiene: "7901",
};

function body(sections: Sections): RenderedReportBody {
  return {
    report: {
      id: "r1",
      project_id: null,
      scope: "portfolio",
      name: "Sentinel report",
      config: {
        weeks: 2,
        skip_current_week: true,
        include_subtree: true,
        sections: Object.keys(sections),
      },
      created_by: "ana@example.test",
      created_at: "2026-09-24T00:00:00Z",
    },
    period_start: "2026-09-07",
    period_end: "2026-09-20",
    sections,
  };
}

function draw(sections: Sections): string {
  return renderToStaticMarkup(createElement(RenderedBody, { body: body(sections) }));
}

/** The markup before the section's folded table, and the table itself. */
function panelAndTable(markup: string): { panel: string; table: string } {
  const at = markup.indexOf(", as a table<");
  expect(at, "the section draws no table").toBeGreaterThan(-1);
  return { panel: markup.slice(0, at), table: markup.slice(at) };
}

const PANEL_TITLE: Record<keyof Sections, string> = {
  finished: "What we finished",
  throughput: "Are we getting faster",
  outlook: "Will this land",
  stuck: "Where work is stuck",
  load: "Who is overloaded",
  capacity: "Who has the hours",
  conflicts: "Where the plan conflicts",
  rebalance: "Who could help",
  hygiene: "What open tasks are missing",
};

// ── (a) The render ───────────────────────────────────────────────────────────

describe("RenderedBody draws each section as its panel, then its table", () => {
  it("draws all nine panels and nine tables in one report", () => {
    const html = draw(SECTIONS);
    for (const title of Object.values(PANEL_TITLE)) {
      expect(html, title).toContain(title);
    }
    expect(html.split(", as a table<").length).toBe(10);
    // The panel names its region, so a screen reader announces the title.
    expect(html.match(/<section[^>]*aria-labelledby=/g)?.length).toBe(9);
  });

  it("hygiene draws one bar a kind, five titles, then the rest as a count", () => {
    const { panel, table } = panelAndTable(draw({ hygiene: HYGIENE }));
    for (const line of ["7901 of 9901", "7 of 9901", "0 of 9901", "1 of 9901"]) {
      expect(panel, line).toContain(line);
    }
    // One bar for each kind, with its figures as its accessible name.
    expect(panel.match(/role="img"/g)?.length).toBe(4);
    expect(panel).toContain('aria-label="No assignee: 7901 of 9901"');
    expect(panel).toContain('aria-label="None of 9901"');
    // Five titles, then the server's count less the five.
    expect(panel).toContain("Order steel 7901");
    expect(panel).toContain("no_assignee task 4");
    expect(panel).not.toContain("no_assignee task 5");
    expect(panel).toContain("…and 7896 more");
    expect(panel).toContain("…and 2 more");
    expect(panel).toContain(
      "A task can miss more than one thing, so the counts do not add up to the open total."
    );
    // The table names every row the server sent.
    expect(table).toContain("no_assignee task 6");
    expect(table).toContain("last change 1 Sep 2026");
  });

  it("hygiene with no open work says so, and draws no bar", () => {
    const html = draw({
      hygiene: { open_total: 0, stale_days: 14, by_kind: {}, rows: [] },
    });
    expect(html).toContain("No open tasks in this scope.");
    const { panel } = panelAndTable(html);
    expect(panel).not.toContain('role="img"');
  });

  it("rebalance draws each task with its holder and helpers, then pickups, then the caps", () => {
    const { panel, table } = panelAndTable(draw({ rebalance: REBALANCE }));
    expect(panel).toContain("Held by Hal · due 27 Sep 2026");
    expect(panel).toContain("Could help: Ivy, Bo");
    expect(panel).toContain("Could take: Weld a jig");
    expect(panel).toContain("11 at risk · 26 idle");
    expect(panel).toContain("Showing 1 of 11 tasks at risk.");
    expect(panel).toContain("Showing 1 of 25 people who could take work.");
    // The task comes before the pickup, in the panel and in the table.
    expect(panel.indexOf("Weld the gantry")).toBeLessThan(panel.indexOf("Could take:"));
    expect(table).toContain("helpers Ivy, Bo");
    expect(table).toContain("could take: Weld a jig");
    // Read only: no Assign, no Dismiss, no link.
    for (const word of ["Assign", "Dismiss", "<a "]) expect(panel + table).not.toContain(word);
  });

  it("rebalance counts the tasks and the pickups in its table title (H-186 item 2)", () => {
    // One task and three people, so a count of tasks alone reads 1, not 4.
    const pickup = REBALANCE.pickups![0];
    const data = {
      ...REBALANCE,
      pickups: [
        pickup,
        { ...pickup, person_id: "p-bo", name: "Bo", email: "bo@example.test" },
        { ...pickup, person_id: "p-cy", name: "Cy", email: "cy@example.test" },
      ],
    };
    const tasks = rebalanceTasks(data).length;
    const pickups = rebalancePickups(data).length;
    expect([tasks, pickups]).toEqual([1, 3]);
    const html = draw({ rebalance: data });
    const count = html.match(
      /Who could help, as a table<\/span><span[^>]*>· (?:<!-- -->)?(\d+)<\/span>/
    );
    expect(count, "the table title carries no count").not.toBeNull();
    expect(Number(count![1])).toBe(tasks + pickups);
  });

  it("rebalance without the HR grant shows the hint and no zero rows", () => {
    const html = draw({ rebalance: REBALANCE_HIDDEN });
    const { panel, table } = panelAndTable(html);
    const hint = "Rebalancing needs HR read access. An admin can see it.";
    expect(panel).toContain(hint);
    expect(table).toContain(hint);
    for (const word of ["0 at risk", "Could help", "Could take", "Nobody", "undefined"]) {
      expect(html, word).not.toContain(word);
    }
  });

  it("stuck draws its band chart in a report, from the server's bands", () => {
    const html = draw({ stuck: SECTIONS.stuck });
    expect(html).toContain("&lt; 7d");
    expect(html).toContain("Over 30 days: 2 of 10");
    expect(html).not.toContain("No open work in this scope");
  });

  it("outlook draws the slip as a range bar, with both dates and the slip in text", () => {
    const html = draw({ outlook: OUTLOOK });
    expect(html).toContain("Plan 17 Feb 2027, forecast 12 Mar 2027: 23 days late.");
    expect(html).toContain("Plan 17 Feb 2027");
    expect(html).toContain("Forecast 12 Mar 2027");
    expect(html).toContain("23 days late");
  });

  for (const name of Object.keys(SENTINEL) as (keyof Sections)[]) {
    it(`agree: ${name}'s sentinel is in the panel AND in the table`, () => {
      const { panel, table } = panelAndTable(
        draw({ [name]: SECTIONS[name] } as Sections)
      );
      expect(panel, "panel").toContain(PANEL_TITLE[name]);
      expect(panel, "panel").toContain(SENTINEL[name]);
      expect(table, "table").toContain(SENTINEL[name]);
    });
  }

  it("folds the table closed by default, and keeps it in the page", () => {
    const html = draw({ load: SECTIONS.load });
    const { table } = panelAndTable(html);
    // Closed: the trigger says so, and the panel is hidden, not removed.
    expect(html).toMatch(
      /aria-expanded="false"[^>]*>(?:(?!<\/button>)[\s\S])*, as a table</
    );
    expect(table).toMatch(/<div[^>]*data-closed=""[^>]*hidden=""/);
    expect(table).toContain("ana@example.test");
  });

  it("drops the report's own section heading where a panel draws", () => {
    const html = draw(SECTIONS);
    expect(html).not.toMatch(/<h4/);
  });

  it("draws the committed-hours bar with both figures in text", () => {
    const html = draw({ capacity: SECTIONS.capacity });
    expect(html).toContain("46 of 40 h");
    expect(html).toContain('role="img"');
  });
});

describe("degrade: a report-shaped body prints no broken words", () => {
  const BROKEN = ["undefined", "NaN", "No open work in this scope"];

  /** The body a server before R2b sent: no pass-through figures at all. */
  const OLD: Sections = {
    finished: {
      ...SECTIONS.finished,
      projects: SECTIONS.finished.projects.map(
        ({ median_hours: _m, ...p }) => p
      ),
    },
    throughput: {
      series: SECTIONS.throughput.series,
      median_hours: 27,
      measured: 15,
    },
    load: {
      people: SECTIONS.load.people.map(
        ({ due_next_7d: _d, later: _l, ...p }) => p
      ),
      total_tasks: 9401,
    },
    capacity: SECTIONS.capacity,
    // Before R3a: no ageing bands.
    stuck: { overdue: SECTIONS.stuck.overdue, overdue_total: 9302 },
    conflicts: SECTIONS.conflicts,
  };

  for (const [label, sections] of [
    ["since R3a", SECTIONS],
    ["before R2b", OLD],
    ["with nothing overdue", { stuck: { overdue: [], overdue_total: 0 } }],
    ["with no median", { throughput: { series: [], median_hours: null, measured: 0 } }],
    ["with no forecast date", { outlook: NOT_CONVERGING }],
    ["without the HR grant", { rebalance: REBALANCE_HIDDEN }],
    ["with no open work to check", {
      hygiene: { open_total: 0, stale_days: 14, by_kind: {}, rows: [] },
    }],
  ] as [string, Sections][]) {
    it(`prints none of them ${label}`, () => {
      const html = draw(sections);
      for (const word of BROKEN) expect(html, word).not.toContain(word);
    });
  }

  it("says 'No open work in this scope' only when every band is zero", () => {
    const zero = draw({
      stuck: {
        overdue: [],
        overdue_total: 0,
        stale: SECTIONS.stuck.stale!.map((b) => ({ band: b.band, n: 0 })),
      },
    });
    expect(zero).toContain("No open work in this scope.");
    expect(draw({ stuck: SECTIONS.stuck })).not.toContain("No open work in this scope");
  });

  it("draws no slip bar and shows the verdict when the forecast has no date", () => {
    const html = draw({ outlook: NOT_CONVERGING });
    expect(html).toContain("Not converging");
    expect(html).not.toContain("days late");
    expect(html).not.toMatch(/aria-label="Plan /);
    // The folded table says each absent date as a dash.
    expect(html).toMatch(/>Forecast finish<\/dt><dd[^>]*>—</);
  });

  it("says nothing overdue in words, and draws no band chart", () => {
    const html = draw({ stuck: { overdue: [], overdue_total: 0 } });
    expect(html).toContain("Nothing is past its due date in this scope.");
    expect(html).not.toContain("&lt; 7d");
    expect(html).not.toContain("Blocked:");
  });

  it("says a null median in words, never as a zero", () => {
    const html = draw({ throughput: { series: [], median_hours: null, measured: 0 } });
    expect(html).toContain("not measured");
  });
});

describe("the summary tiles", () => {
  it("draws four tiles for a report with every section", () => {
    const labels = reportTiles(SECTIONS).map((t) => t.label);
    expect(labels).toEqual(["Finished", "Open", "Overdue", "Median cycle time"]);
    const html = draw(SECTIONS);
    for (const figure of ["9102", "9401", "9302", "27h"]) {
      expect(html, figure).toContain(figure);
    }
  });

  it("hides a tile whose section is not in the report", () => {
    const html = draw({ finished: SECTIONS.finished });
    expect(html).toContain(">Finished<");
    expect(html).not.toContain("Median cycle time");
    expect(html).not.toContain(">Open<");
    expect(html).not.toContain(">Overdue<");
    expect(reportTiles({ finished: SECTIONS.finished }).map((t) => t.key)).toEqual([
      "finished",
    ]);
  });

  it("takes Open from capacity when load is absent", () => {
    const tiles = reportTiles({ capacity: SECTIONS.capacity });
    expect(tiles).toEqual([
      expect.objectContaining({ key: "open", value: 9501 }),
    ]);
  });

  it("takes Overdue from stuck's total, never from the rows per person", () => {
    // The rows per person say 2 overdue. The total counts tasks, once each.
    const tiles = reportTiles({ load: SECTIONS.load, stuck: SECTIONS.stuck });
    expect(tiles.find((t) => t.key === "overdue")?.value).toBe(9302);
  });

  it("shows a null median as 'not measured'", () => {
    const html = draw({ throughput: { series: [], median_hours: null, measured: 0 } });
    const tile = html.slice(html.indexOf("Median cycle time"));
    expect(tile.slice(0, 300)).toContain("not measured");
  });
});

describe("the Analytics app renders as before", () => {
  it("still draws the band chart and its empty state from the route's shape", () => {
    const html = renderToStaticMarkup(
      createElement(StuckPanel, {
        data: {
          project_id: null,
          scope: "portfolio",
          stale: [],
          blocked: [],
          blocked_total: 0,
          overdue: [],
          overdue_total: 0,
        },
      })
    );
    expect(html).toContain("No open work in this scope.");
  });

  it("still draws every throughput cell from the route's shape", () => {
    const html = renderToStaticMarkup(
      createElement(ThroughputPanel, {
        data: {
          project_id: null,
          scope: "portfolio",
          weeks: 2,
          current_week_partial: true,
          series: [
            { week_start: "2026-09-14", completed: 2, cancelled: 0, measured: 2, no_start: 0, median_hours: null, p90_hours: null },
          ],
          summary: { completed: 2, cancelled: 0, measured: 2, no_start: 0, median_hours: null, p90_hours: null },
        },
      })
    );
    for (const cell of [">Finished<", ">Median<", ">Slowest 10%<"]) {
      expect(html, cell).toContain(cell);
    }
    expect(html).toContain("no measurable cycle time");
  });

  it("says zero working hours in words, not 'Nothing in this scope'", () => {
    const html = renderToStaticMarkup(
      createElement(CapacityPanel, {
        data: capacityPanelData(
          {
            ...SECTIONS.capacity,
            people: [capacityRow({ working_hours_horizon: 0, committed_hours_horizon: 6 })],
          },
          body({})
        ),
      })
    );
    expect(html).toContain("No working time in the next 14 days");
    expect(html).not.toContain("Nothing in this scope yet");
  });

  it("the Outlook panel draws the slip bar from the route's own body", () => {
    // WS-27bn R3a. The Analytics app and the node dashboard share this panel.
    const html = renderToStaticMarkup(createElement(OutlookPanel, { data: OUTLOOK }));
    expect(html).toContain("23 days late");
    expect(html).toContain(
      'aria-label="Plan 17 Feb 2027, forecast 12 Mar 2027: 23 days late."'
    );
    const none = renderToStaticMarkup(
      createElement(OutlookPanel, { data: NOT_CONVERGING })
    );
    expect(none).toContain("Not converging");
    expect(none).not.toMatch(/aria-label="Plan /);
    for (const word of ["undefined", "NaN"]) expect(none).not.toContain(word);
  });

  it("draws no committed bar without hours_basis", () => {
    const html = renderToStaticMarkup(
      createElement(CapacityPanel, {
        data: capacityPanelData(
          { ...SECTIONS.capacity, people: [capacityRow({ hours_basis: false })] },
          body({})
        ),
      })
    );
    expect(html).not.toContain(" of 40 h");
  });
});

// ── (b) The source ───────────────────────────────────────────────────────────

const PANELS = [
  "FinishedPanel",
  "ThroughputPanel",
  "OutlookPanel",
  "LoadPanel",
  "CapacityPanel",
  "StuckPanel",
  "ConflictsPanel",
  "RebalancePanel",
  "HygienePanel",
];

/** Everything that would make `RenderedBody` a second chart component. */
function handDrawnChart(source: string): string[] {
  const found: string[] = [];
  if (/style=\{\{[^}]*\b(width|height)\b/.test(source)) {
    found.push("an inline width or height style");
  }
  const named = source.match(
    /\b(?:function|const|let)\s+([A-Za-z_]*(?:Bar|Chart|bar|chart)[A-Za-z_]*)\b/g
  );
  if (named) found.push(...named);
  return found;
}

describe("ReportsView draws with the Analytics panels, and draws nothing itself", () => {
  const source = readFileSync(join(__dirname, "ReportsView.tsx"), "utf-8");

  it("imports the nine panels from ./AnalyticsPanels", () => {
    const block = source.match(/import\s*\{([^}]*)\}\s*from\s*"\.\/AnalyticsPanels"/);
    expect(block, "no import from ./AnalyticsPanels").not.toBeNull();
    for (const panel of PANELS) expect(block![1]).toMatch(new RegExp(`\\b${panel}\\b`));
  });

  it("renders each of the nine", () => {
    for (const panel of PANELS) expect(source).toContain(`<${panel}`);
  });

  it("holds no inline width or height and no bar or chart function", () => {
    expect(handDrawnChart(source)).toEqual([]);
  });

  it("would catch a hand-drawn bar if one arrived", () => {
    const bar = `function MiniBar({ n }) {\n  return <div style={{ width: \`\${n}%\` }} />;\n}`;
    expect(handDrawnChart(bar).length).toBeGreaterThan(0);
    expect(handDrawnChart("const weekChart = () => null;")).toEqual([
      "const weekChart",
    ]);
    expect(handDrawnChart('<div style={{ height: "4px" }} />')).toEqual([
      "an inline width or height style",
    ]);
  });
});

// ── (c) The adapters ─────────────────────────────────────────────────────────

describe("lib/reportPanels maps each section and computes nothing", () => {
  const frame = body(SECTIONS);

  it("finished: the window and the scope are the server's", () => {
    const got = finishedPanelData(SECTIONS.finished, frame);
    expect(got.period_start).toBe("2026-09-07");
    expect(got.period_end).toBe("2026-09-20");
    expect(got.weeks).toBe(2);
    expect(got.skip_current_week).toBe(true);
    expect(got.scope).toBe("portfolio");
    expect(got.total_completed).toBe(9102);
    expect(got.projects[0].median_hours).toBe(30);
  });

  it("throughput: absent figures stay absent, and the week is partial", () => {
    const got = throughputPanelData(SECTIONS.throughput, frame);
    expect(got.summary.p90_hours).toBe(80);
    expect(got.summary.no_start).toBe(1);
    expect(got.summary.cancelled).toBe(2);
    expect("completed" in got.summary).toBe(false);
    expect(got.series[1]).toEqual({ week_start: "2026-09-14", completed: 7201 });
    expect(got.current_week_partial).toBe(true);
    expect(
      throughputPanelData({ series: [], median_hours: null, measured: 0 }, frame)
        .current_week_partial
    ).toBe(false);
  });

  it("load: the buckets pass through, and no people_total is invented", () => {
    const got = loadPanelData(SECTIONS.load, frame);
    expect(got.people[0]).toMatchObject({ due_next_7d: 3, later: 7396 });
    expect(got.people_total).toBeUndefined();
    expect(got.total_tasks).toBe(9401);
  });

  it("capacity: the report's people become the panel's rows", () => {
    const got = capacityPanelData(SECTIONS.capacity, frame);
    expect(got.rows).toHaveLength(2);
    expect(got.rows[0].open_tasks).toBe(7501);
    expect(got.hr_visible).toBe(true);
  });

  it("stuck: the bands pass through, and absent bands stay absent", () => {
    const got = stuckPanelData(SECTIONS.stuck, frame);
    expect(got.stale).toEqual(SECTIONS.stuck.stale);
    expect(got.overdue_total).toBe(9302);
    const old = stuckPanelData(
      { overdue: SECTIONS.stuck.overdue, overdue_total: 9302 },
      frame
    );
    expect("stale" in old).toBe(false);
  });

  it("outlook: the section is the route's body, copied", () => {
    const got = outlookPanelData(OUTLOOK);
    expect(got).toEqual(OUTLOOK);
    expect(got).not.toBe(OUTLOOK);
  });

  it("conflicts: the rows and the counts are the server's", () => {
    const got = conflictsPanelData(SECTIONS.conflicts, frame);
    expect(got.total).toBe(3);
    expect(got.by_kind).toEqual({ blocker_late: 3 });
    expect(got.rows[0].sentence).toContain("7601");
    expect(got.truncated).toBeUndefined();
  });

  it("rebalance: the section is the route's body, copied, lists absent stay absent", () => {
    const got = rebalancePanelData(REBALANCE);
    expect(got).toEqual(REBALANCE);
    expect(got).not.toBe(REBALANCE);
    const hidden = rebalancePanelData(REBALANCE_HIDDEN);
    expect("at_risk" in hidden).toBe(false);
    expect("pickups" in hidden).toBe(false);
  });

  it("hygiene: the section is the body, copied, and nothing is added", () => {
    const got = hygienePanelData(HYGIENE);
    expect(got).toEqual(HYGIENE);
    expect(got).not.toBe(HYGIENE);
    expect(got.by_kind).not.toBe(HYGIENE.by_kind);
    expect(Object.keys(got).sort()).toEqual(
      ["by_kind", "open_total", "rows", "stale_days"]
    );
  });

  describe("the browser counts nothing", () => {
    // The check of `capacity.test.ts`, applied to this module.
    const ARITHMETIC = /\.reduce\(|Math\.|\s[-*/]\s|\+=|-=/;

    function code(text: string): string {
      return text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/[^\n]*/g, "");
    }

    it("holds no arithmetic in the adapters' module", () => {
      const source = readFileSync(
        join(__dirname, "..", "lib", "reportPanels.ts"),
        "utf-8"
      );
      expect(code(source)).not.toMatch(ARITHMETIC);
    });

    it("would catch a sum if one arrived", () => {
      expect("rows.reduce((n, r) => n + r.overdue, 0)").toMatch(ARITHMETIC);
      expect("const left = total - done;").toMatch(ARITHMETIC);
    });
  });
});
