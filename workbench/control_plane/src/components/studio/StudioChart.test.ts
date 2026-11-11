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

describe("StudioStat", () => {
  it("shows the value and the trend", () => {
    const html = renderToStaticMarkup(createElement(StudioStat, { data: { label: "Deals" }, rows: [AGG] }));
    expect(html).toContain("Deals");
    expect(html).toContain(">25<");
    expect(html).toContain("+50%");
    expect(html).toContain("vs Oct 2026");
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
    expect(renderToStaticMarkup(createElement(StudioStat, { data: {}, rows: [flat] }))).toContain("No trend");
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
