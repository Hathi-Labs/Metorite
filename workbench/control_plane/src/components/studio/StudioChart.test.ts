/**
 * STU-D4 fence: the Chart and Stat blocks draw their states in plain words, and the one
 * renderer (`GenerativeUINode`) binds them to the page door, with a failed read as an error.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  usePathname: () => "/studio/blocks-preview",
}));

import GenerativeUINode from "@/components/GenerativeUINode";
import StudioChart from "@/components/studio/StudioChart";
import StudioStat, { sparkPoints } from "@/components/studio/StudioStat";
import { clearAll } from "@/lib/dataCache";
import { chartBlock, statBlock } from "@/lib/studio/aggregateModel";
import { StudioDataContext } from "@/lib/studio/dataContext";
import type { Row, StudioDataDoor } from "@/lib/studio/dataDoor";

beforeEach(() => clearAll());

const g = (key: unknown, count: number, ...values: number[]) => ({ key, count, values });
const AGG: Row = {
  measures: [{ op: "count" }], currencies: [null], other: null, folded: 0,
  groups: [g("2026-10", 10, 10), g("2026-11", 15, 15)], total: g(null, 25, 25),
};
const EMPTY: Row = { ...AGG, groups: [], total: g(null, 0, 0) };

describe("StudioChart", () => {
  it("draws the box of a chart for an aggregate", () => {
    const html = renderToStaticMarkup(createElement(StudioChart, { data: { type: "bar", title: "Deals" }, rows: [AGG] }));
    expect(html).toContain('data-studio-chart="bar"');
    expect(html).not.toContain("data-chart-empty");
  });

  it("says No data for an empty aggregate", () => {
    const html = renderToStaticMarkup(createElement(StudioChart, { data: { type: "bar" }, rows: [EMPTY] }));
    expect(html).toContain("data-chart-empty");
    expect(html).toContain("No data");
  });

  it("says why a kind cannot draw the result", () => {
    const html = renderToStaticMarkup(createElement(StudioChart, { data: { type: "radar" }, rows: [AGG] }));
    expect(html).toContain("A radar chart needs 3 or more groups.");
  });

  it("says a note when the groups are left out", () => {
    const more: Row = { ...AGG, folded: 4 };
    const html = renderToStaticMarkup(createElement(StudioChart, { data: { type: "bar" }, rows: [more] }));
    expect(html).toContain("4 more are not drawn.");
  });
});

describe("StudioStat icon (STU-LOOK-2)", () => {
  const draw = (data: unknown) => renderToStaticMarkup(createElement(StudioStat, { data, rows: [AGG] }));
  it("draws an icon of the list before the heading", () => {
    const html = draw({ label: "Deals", icon: "Truck" });
    expect(html).toContain("<svg");
    expect(html.indexOf("<svg")).toBeLessThan(html.indexOf("data-stat-heading"));
    expect(html).toContain('data-stat-heading=""');
  });
  it("draws no icon for a name outside the list, a non-string or no icon", () => {
    for (const icon of ["Skull", 5, null, undefined, ""]) {
      const html = draw({ label: "Deals", icon });
      expect(html.slice(0, html.indexOf("data-stat-heading"))).not.toContain("<svg");
    }
  });
});

describe("StudioStat", () => {
  it("shows the value and the trend", () => {
    const html = renderToStaticMarkup(createElement(StudioStat, { data: { label: "Deals" }, rows: [AGG] }));
    expect(html).toContain("Deals");
    expect(html).toContain(">25<");
    expect(html).toContain("+50%");
    expect(html).toMatch(/ vs <span[^>]*>Oct 2026</);
    expect(html).toContain("<polyline");
  });

  it("colours a fall as bad, and as good when a fall is good", () => {
    const down: Row = { ...AGG, groups: [g("2026-10", 15, 15), g("2026-11", 10, 10)] };
    const bad = renderToStaticMarkup(createElement(StudioStat, { data: {}, rows: [down] }));
    const good = renderToStaticMarkup(createElement(StudioStat, { data: { good: "down" }, rows: [down] }));
    expect(bad).not.toBe(good);
    expect(bad).toContain("−33.3%");
  });

  it("says there is no trend without a date group, and No data without a result", () => {
    const flat: Row = { ...AGG, groups: [g("open", 25, 25)] };
    expect(renderToStaticMarkup(createElement(StudioStat, { data: {}, rows: [flat] }))).toContain('data-stat-trend="none"');
    expect(renderToStaticMarkup(createElement(StudioStat, { data: {}, rows: [flat] }))).not.toContain("Group by a date");
    expect(renderToStaticMarkup(createElement(StudioStat, { data: {}, rows: [{ id: "x" }] }))).toContain("No data");
  });

  it("draws a line from two points or more", () => {
    expect(sparkPoints([1])).toBe("");
    expect(sparkPoints([1, 3])).toBe("0.0,32.0 120.0,0.0");
  });
});

describe("the bound blocks", () => {
  const door = (read: StudioDataDoor["read"]): StudioDataDoor => ({ scope: "t:draft", read, run: async () => ({ ok: true }) });
  const SOURCE = { action: "object:deal.aggregate", args: {} };
  const draw = (d: StudioDataDoor, node: unknown) =>
    renderToStaticMarkup(createElement(StudioDataContext.Provider, { value: d },
      createElement(GenerativeUINode, { spec: node as never })));

  it("shows the loading state while the read waits", () => {
    const html = draw(door(() => new Promise<Row[]>(() => {})), chartBlock("bar", SOURCE, "Deals"));
    expect(html).toContain('aria-busy="true"');
  });

  it("is a node of the one renderer: the chart and the stat template", () => {
    expect(chartBlock("bar", SOURCE).props.name).toBe("chart");
    expect(statBlock(SOURCE).props.name).toBe("sparkTrend");
  });
});

describe("the mapped views (D4c)", () => {
  const ctx = { actions: [{ id: "agg", uses: "object:deal.aggregate" }] };
  const door: StudioDataDoor = { scope: "t:draft", read: async () => [AGG], run: async () => ({ ok: true }) };
  const draw = (node: unknown) =>
    renderToStaticMarkup(createElement(StudioDataContext.Provider, { value: door },
      createElement(GenerativeUINode, { spec: node as never })));

  it("a chart view draws through the one renderer, in its loading state", async () => {
    const { chartViewBlock, statViewBlock } = await import("@/lib/studio/aggregateModel");
    const chart = chartViewBlock({ id: "c", title: "Deals", source: { action: "agg", args: { kind: "bar" } } }, ctx);
    const stat = statViewBlock({ id: "s", title: "Deals", source: { action: "agg", args: {} } }, ctx);
    expect(chart.ok && stat.ok).toBe(true);
    if (!chart.ok || !stat.ok) return;
    expect(draw(chart.node)).toContain('aria-busy="true"');
    expect(draw(stat.node)).toContain('aria-busy="true"');
  });

  it("the sample door answers the aggregate of a mapped view with no live door", async () => {
    const { makeSampleDoor } = await import("@/lib/studio/sampleDoor");
    const { parseAggregate, chartFromAggregate, statFromAggregate } = await import("@/lib/studio/aggregateModel");
    const rows = await makeSampleDoor(undefined, undefined, 0).read({
      action: "object:visit.aggregate",
      args: { group_by: { field: "visit_date", bucket: "day" }, measures: [{ op: "count" }] },
    });
    const agg = parseAggregate(rows);
    expect(agg?.total.count).toBe(3);
    expect(chartFromAggregate("bar", agg).ok).toBe(true);
    expect(statFromAggregate(agg)?.trend).not.toBeNull();
  });
});

/**
 * STU-LY-7: a number never breaks between two digits, and it is never cut. This test has no layout
 * engine. So it reads the line rules and the size rule that the markup gives, and works them out
 * against the true glyph widths of the app font.
 */
describe("the value of a stat in a narrow tile", () => {
  /** Advance widths in em of semibold tabular figures, a little over what Chromium and SF draw. */
  const trueEms = (v: string) => [...v].reduce((w, c) => w + (/[0-9]/.test(c) ? 0.66 : /[., ]/.test(c) ? 0.3 : 0.75), 0);
  const draw = (value: number, currency: string | null) => {
    const row: Row = { ...AGG, currencies: [currency], total: { key: null, count: 1, values: [value] }, groups: [
      { key: "2026-11-02", count: 1, values: [1] }, { key: "2026-11-03", count: 1, values: [2] },
    ] };
    return renderToStaticMarkup(createElement(StudioStat, { data: {}, rows: [row] }));
  };
  // The content width of a tile in px: 2 columns of a 320 px phone, and the full row.
  const NARROW = 116;
  const FULL = 262;

  it.each([[987654321, "INR"], [18456000, "INR"], [1234567, null], [12, null], [98765432100, "INR"]] as const)(
    "draws %s on one line inside its tile", (n, currency) => {
      const html = draw(n, currency);
      const [, attrs, text] = /<div data-stat-value=""([^>]*)>([^<]*)<\/div>/.exec(html)!;
      // No rule lets the text break inside itself, and no rule cuts it.
      expect(attrs).toContain("whitespace-nowrap");
      expect(attrs).not.toMatch(/overflow-wrap|word-break|break-all|break-words|truncate|overflow-hidden|text-ellipsis/);
      const size = /font-size:min\(1\.5rem, calc\(100cqi \/ ([\d.]+)\)\)/.exec(attrs);
      expect(size, attrs).not.toBeNull();
      const wide = html.includes("data-stat-wide");
      const room = wide ? FULL : NARROW;
      const px = Math.min(24, room / Number(size![1]));
      expect(trueEms(text) * px, text).toBeLessThanOrEqual(room);
      // The number stays large enough to read.
      expect(px, text).toBeGreaterThanOrEqual(12);
    });

  it("keeps the date of the trend on one line", () => {
    expect(draw(12, null)).toMatch(/data-stat-against="" class="whitespace-nowrap">2026-11-02</);
  });
});
