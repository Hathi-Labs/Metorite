/**
 * WS-27bn R5g: the live Overview, full page. The markup half.
 *
 * Spec: `project-docs/specs/projects_reports.md` §6.7 and §8 R5g. The vitest
 * environment has no DOM, so these tests render the parts with
 * `renderToStaticMarkup`. The pure rules are `lib/overviewLive.test.ts`.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { RenderedReportBody } from "../lib/api";
import { BODY_ORDER } from "../lib/overviewLive";
import {
  OverviewFilters,
  OverviewHeader,
  RenderedBody,
  SectionTile,
} from "./ReportsView";

const html = (el: React.ReactElement) => renderToStaticMarkup(el);
const VIEW = readFileSync(join(__dirname, "ReportsView.tsx"), "utf-8");

function body(sections: Record<string, unknown>): RenderedReportBody {
  return {
    report: {
      id: "r-1",
      project_id: null,
      scope: "portfolio",
      name: "Overview",
      config: { weeks: 12, skip_current_week: false, include_subtree: true, sections: Object.keys(sections) },
      created_by: "a@x.io",
      created_at: "2026-09-30T00:00:00Z",
    },
    period_start: "2026-07-08",
    period_end: "2026-09-30",
    sections,
  } as unknown as RenderedReportBody;
}

// Two sections with rows, so neither draws a clear row.
const FINISHED = {
  total_completed: 5,
  total_cancelled: 0,
  projects: [{ project_id: "p1", name: "Printers", completed: 5, cancelled: 0 }],
};
const STUCK = {
  overdue_total: 2,
  overdue: [{ project_id: "p1", name: "Printers", overdue: 2 }],
  blocked: [],
  blocked_total: 0,
  stale: [],
  stale_total: 0,
};

const toolbar = (over: Partial<Parameters<typeof OverviewHeader>[0]> = {}) =>
  html(
    createElement(OverviewHeader, {
      summary: "Whole organization · The last 12 weeks · Updated 2 min ago",
      filtersOpen: false,
      filtersId: "f-panel",
      filtersButtonId: "f-button",
      filterCount: 0,
      refreshing: false,
      saveDisabled: false,
      onToggleFilters: () => undefined,
      onRefresh: () => undefined,
      onSaveAs: () => undefined,
      ...over,
    })
  );

describe("R5g rule 1: Overview panels flow in a grid, full width", () => {
  const grid = html(
    createElement(RenderedBody, {
      body: body({ finished: FINISHED, stuck: STUCK }),
      layout: "grid",
      showTitle: false,
    })
  );

  it("one column below xl, two at xl, each cell at its own height", () => {
    expect(grid).toMatch(/class="grid grid-cols-1 items-start gap-3 xl:grid-cols-2"/);
  });

  it("has no reading measure in Overview", () => {
    expect(grid).not.toContain("max-w-3xl");
  });

  it("a panel that reads better wide spans both columns", () => {
    const three = html(
      createElement(RenderedBody, {
        body: body({ finished: FINISHED, stuck: STUCK, load: { total_tasks: 1, people: [{ assignee: "a@x.io", open_tasks: 1, overdue: 0 }] } }),
        layout: "grid",
        showTitle: false,
      })
    );
    expect(three).toContain("xl:col-span-2");
  });
});

describe("R5g rule 1: the grid reads the order RenderedBody draws", () => {
  it("BODY_ORDER is the order of the sections in RenderedBody", () => {
    const from = VIEW.indexOf("export function RenderedBody(");
    const to = VIEW.indexOf("\n}\n", from);
    const drawn = [...VIEW.slice(from, to).matchAll(/\{sections\.(\w+) &&\n\s*cell\(/g)].map(
      (m) => m[1]
    );
    expect(drawn).toEqual([...BODY_ORDER]);
  });
});

describe("R5g: a saved report and the builder keep their layout", () => {
  const column = html(
    createElement(RenderedBody, { body: body({ finished: FINISHED, stuck: STUCK }) })
  );

  it("the default body is one readable column", () => {
    expect(column).toContain('class="max-w-3xl space-y-3"');
    expect(column).not.toContain("xl:grid-cols-2");
    expect(column).not.toContain("col-span");
  });

  it("the saved report and the builder pass no grid layout", () => {
    expect(VIEW).toContain("<RenderedBody body={body} headerLine={header} />");
    expect(VIEW.match(/layout=\{inOverview \? "grid" : "column"\}/g)?.length).toBe(1);
    expect(VIEW).toContain("xl:grid-cols-[18rem_minmax(0,1fr)]");
  });
});

describe("R5g rule 2: one slim toolbar", () => {
  const bar = toolbar();

  it("says the summary line on the left", () => {
    expect(bar).toContain("Whole organization · The last 12 weeks · Updated 2 min ago");
  });

  it("names Filters, Refresh and Save as report", () => {
    const filters = bar.match(/<button[^>]*id="f-button"[^>]*>/)?.[0] ?? "";
    expect(filters).toContain('aria-expanded="false"');
    expect(filters).toContain('aria-controls="f-panel"');
    expect(filters).not.toContain("aria-pressed");
    expect(bar).toContain(">Filters<");
    expect(bar).toMatch(/<button[^>]*aria-label="Refresh"/);
    expect(bar).toContain("Save as report");
  });

  it("opens no menu and no dialog", () => {
    expect(bar).not.toMatch(/role="(menu|dialog)"/);
    expect(bar).not.toContain("aria-haspopup");
  });

  it("the Filters button says when it is open", () => {
    expect(toolbar({ filtersOpen: true })).toMatch(/aria-expanded="true"/);
  });

  it("shows a spinner while it refreshes, and keeps the button's name", () => {
    const busy = toolbar({ refreshing: true });
    expect(busy).toContain("animate-spin");
    expect(busy).toMatch(/<button[^>]*aria-label="Refresh"/);
    expect(bar).not.toContain("animate-spin");
  });
});

describe("R5g rule 2: the filter count badge", () => {
  it("is absent at the defaults", () => {
    expect(toolbar({ filterCount: 0 })).not.toMatch(/filters? changed/);
  });

  it("counts the changed filters, and a screen reader hears it", () => {
    const two = toolbar({ filterCount: 2 });
    expect(two).toMatch(/>2</);
    expect(two).toContain("2 filters changed");
    expect(toolbar({ filterCount: 1 })).toContain("1 filter changed");
  });
});

describe("R5g rule 3: Filters expands in place", () => {
  const panel = (open: boolean, resetShown = true) =>
    html(
      createElement(OverviewFilters, {
        id: "f-panel",
        labelledBy: "f-button",
        open,
        who: createElement("span", null, "WHO"),
        work: createElement("span", null, "WORK"),
        time: createElement("span", null, "TIME"),
        sections: createElement("span", null, "SECTIONS"),
        resetShown,
        onReset: () => undefined,
      })
    );

  it("is labelled by its button", () => {
    expect(panel(true)).toMatch(/id="f-panel"[^>]*role="region"[^>]*aria-labelledby="f-button"|role="region"[^>]*aria-labelledby="f-button"[^>]*id="f-panel"|aria-labelledby="f-button"[^>]*id="f-panel"[^>]*role="region"|id="f-panel"[^>]*aria-labelledby="f-button"/);
  });

  it("is hidden when closed, so the button still controls a real element", () => {
    expect(panel(false)).toMatch(/<div[^>]*hidden=""/);
    expect(panel(true)).not.toMatch(/hidden=""/);
  });

  it("holds the three choices in one wrapping row, then the sections", () => {
    const open = panel(true);
    expect(open).toMatch(/flex flex-wrap[^"]*"[^>]*>.*WHO.*WORK.*TIME/);
    expect(open.indexOf("TIME")).toBeLessThan(open.indexOf("SECTIONS"));
  });

  it("offers Reset only away from the defaults", () => {
    expect(panel(true, true)).toContain("Reset");
    expect(panel(true, false)).not.toContain("Reset");
  });

  it("the builder reads Filters closed by default, and never at render", () => {
    expect(VIEW).toContain("const [filtersOpen, setFiltersOpen] = useState(false);");
    expect(VIEW).toMatch(/useEffect\(\(\) => \{\s*if \(!inOverview\) return;\s*\/\/[^\n]*\n\s*\/\* eslint-disable[^\n]*\*\/\s*setFiltersOpen\(readFiltersOpen\(browserStorage\)\)/);
  });

  it("the phone disclosure is gone", () => {
    expect(VIEW).not.toContain("Change what you see");
  });
});

describe("R5g rule 3: section chips keep the tile rules", () => {
  const chip = (p: { on: boolean; blocked?: boolean; last?: boolean }) =>
    html(
      createElement(SectionTile, {
        sectionKey: "load",
        label: "Open work",
        on: p.on,
        blocked: p.blocked ?? false,
        last: p.last ?? false,
        variant: "chip",
        onToggle: () => undefined,
      })
    );

  it("a chosen chip is pressed, and an unchosen one is not", () => {
    expect(chip({ on: true })).toContain('aria-pressed="true"');
    expect(chip({ on: false })).toContain('aria-pressed="false"');
  });

  it("is a small rounded chip, not a full-width tile", () => {
    expect(chip({ on: true })).toContain("rounded-full");
    expect(chip({ on: true })).not.toContain("w-full");
  });

  it("the last chip is aria-disabled and says why", () => {
    const last = chip({ on: true, last: true });
    expect(last).toContain('aria-disabled="true"');
    expect(last).toContain("A report needs at least one section.");
  });

  it("a blocked chip is disabled and says why", () => {
    const blocked = chip({ on: false, blocked: true });
    expect(blocked).toContain("disabled");
    expect(blocked).toContain("Off for a person or team.");
  });
});

describe("R5g rule 4: the live refresh in the builder", () => {
  it("keeps the last body on screen, with no skeleton during a refresh", () => {
    // The body shows while `refreshing`; only the first load has no preview.
    expect(VIEW).toMatch(/refreshing=\{refreshing \|\| updating\}/);
    expect(VIEW).not.toMatch(/refreshing \? \(\s*<Skeleton/);
  });

  it("guards each answer with the shared rule", () => {
    expect(VIEW.match(/answerIsCurrent\(\{/g)?.length).toBeGreaterThanOrEqual(2);
  });

  it("listens for visibility, runs the interval, and clears both", () => {
    expect(VIEW).toContain('document.addEventListener("visibilitychange", onVisibility)');
    expect(VIEW).toContain('document.removeEventListener("visibilitychange", onVisibility)');
    expect(VIEW).toMatch(/setInterval\([^)]*LIVE_INTERVAL_MS\)/);
    expect(VIEW).toMatch(/clearInterval\(/);
  });
});
