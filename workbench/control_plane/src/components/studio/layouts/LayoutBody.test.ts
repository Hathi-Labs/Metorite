/**
 * STU-LY-2 fence: a page with a preset draws its region in place of the cell grid, with no colour
 * and no state of its own, and the frame draws the shared states for the list of a table workspace.
 *
 * Mutation 1: draw the `main` region of `table_workspace` through the cell grid. "draws the main region" turns red.
 * Mutation 2: let AppFrame skip `LayoutList` for a table workspace. "the frame wraps the main table" turns red.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import LayoutBody, { DRAWN_LAYOUTS } from "@/components/studio/layouts/LayoutBody";
import type { LayoutName } from "@/lib/studio/manifest";

const draw = (layout: LayoutName, regions: Record<string, string[]>) =>
  renderToStaticMarkup(createElement(LayoutBody, {
    layout, regions, renderView: (id) => createElement("div", { "data-view": id }, id), titleOf: (id) => `Title ${id}`,
  }));

describe("LayoutBody", () => {
  it("draws the main region of a table workspace in a labelled section", () => {
    const html = draw("table_workspace", { main: ["all"] });
    expect(html).toContain('data-layout="table_workspace"');
    expect(html).toContain('aria-label="Title all"');
    expect(html).toContain('data-view="all"');
  });
  it("keeps a table readable beside the record panel: it scrolls in its own strip at a minimum width", () => {
    const html = draw("table_workspace", { main: ["all"] });
    expect(html).toMatch(/data-table-strip[^>]*overflow-x-auto[^>]*><div class="min-w-\[40rem\]">/);
    expect(html.indexOf("data-table-strip")).toBeLessThan(html.indexOf('data-view="all"'));
  });
  it("draws the header control above the table of a table workspace only", () => {
    const withHeader = (layout: "table_workspace" | "board") => renderToStaticMarkup(createElement(LayoutBody, {
      layout, regions: { main: ["t"] }, renderView: (id) => createElement("div", { "data-view": id }, id),
      titleOf: (id) => id, tableHeader: createElement("div", { "data-columns-menu": "" }, "Columns"),
    }));
    const t = withHeader("table_workspace");
    expect(t.indexOf("data-columns-menu")).toBeLessThan(t.indexOf("data-table-strip"));
    expect(withHeader("board")).not.toContain("data-columns-menu");
  });
  it("says so when the main region has no view", () => {
    expect(draw("table_workspace", { main: [] })).toContain("no table to show");
  });
  it("says that a preset of a later slice is not drawn yet", () => {
    const html = draw("work_queue", { list: ["b"], detail: ["d"] });
    expect(html).toContain('data-layout-pending="work_queue"');
    expect(html).not.toContain("data-view");
  });
  it("draws the main region of a board in a labelled section", () => {
    const html = draw("board", { main: ["pipeline"] });
    expect(html).toContain('data-layout="board"');
    expect(html).toContain('data-view="pipeline"');
    expect(html).not.toContain("data-layout-pending");
  });
  it("wraps a board in the sideways-scroll cue, which draws nothing until a lane hides", () => {
    const html = draw("board", { main: ["pipeline"] });
    expect(html).toContain('data-scroll-cue="none"');
    expect(html).not.toContain("More columns");
    expect(html.indexOf("data-scroll-cue")).toBeLessThan(html.indexOf('data-view="pipeline"'));
    expect(draw("table_workspace", { main: ["t"] })).not.toContain("data-scroll-cue");
  });
  it("points the cue at the element that scrolls: the board, not the page", () => {
    const read = (p: string) => readFileSync(join(process.cwd(), "src", p), "utf8");
    const cue = read("components/studio/layouts/ScrollCue.tsx");
    expect(cue).toContain(`BOARD_SCROLLER = '[aria-label="Board"]'`);
    expect(cue).toContain("querySelector<HTMLElement>(BOARD_SCROLLER)");
    expect(cue).not.toContain("data-page-scroller");
    expect(read("components/studio/StudioBoard.tsx")).toContain('aria-label="Board"');
    expect(read("components/board/Board.tsx")).toContain("overflow-x-auto");
  });
  it("names a board, not a table, when the board region is empty", () => {
    expect(draw("board", { main: [] })).toContain("no board to show");
  });
  it("draws only the presets that a slice built", () => {
    expect([...DRAWN_LAYOUTS]).toEqual(["table_workspace", "board", "dashboard"]);
  });
  it("adds no colour and no pixel size", () => {
    const src = readFileSync(join(__dirname, "LayoutBody.tsx"), "utf-8");
    expect(src).not.toMatch(/#[0-9a-fA-F]{3,8}\b|\b(?:bg|text|border)-(?:red|green|blue|amber|yellow|gray|slate)-|\b\d+px\b/);
  });
});

describe("the dashboard", () => {
  const html = draw("dashboard", { kpis: ["k1", "k2"], charts: ["c1"], worklist: ["w1"] });
  it("draws the stats first in a 2-column grid that gets 4 columns in a wide page only", () => {
    expect(html.indexOf("data-dashboard-kpis")).toBeLessThan(html.indexOf("data-dashboard-charts"));
    expect(html).toMatch(/data-layout="dashboard"[^>]*@container/);
    expect(html).toMatch(/data-dashboard-kpis[^>]*grid-cols-2[^>]*@3xl:grid-cols-4/);
    expect(html).not.toMatch(/(?:sm|md):grid-cols-4/);
  });
  it("draws each tile in its own labelled section, charts in one column on a phone, then the worklist", () => {
    for (const id of ["k1", "k2", "c1", "w1"]) expect(html).toContain(`aria-label="Title ${id}"`);
    expect(html).toMatch(/data-dashboard-charts[^>]*grid-cols-1[^>]*lg:grid-cols-2/);
    expect(html.indexOf("data-dashboard-charts")).toBeLessThan(html.indexOf("data-dashboard-worklist"));
  });
  it("leaves out the chart and worklist rows when the page names none", () => {
    const only = draw("dashboard", { kpis: ["k1"] });
    expect(only).not.toContain("data-dashboard-charts");
    expect(only).not.toContain("data-dashboard-worklist");
  });
  it("says so when there is no stat", () => {
    expect(draw("dashboard", { charts: ["c1"] })).toContain("no stat to show");
  });
});

describe("the frame", () => {
  const frame = readFileSync(join(__dirname, "../../../app/studio/package-preview/AppFrame.tsx"), "utf-8");
  it("hides the columns of the URL in the main table, and draws the Columns menu", () => {
    expect(frame).toContain("hideNode(model.node, parseHidden(state.hide))");
    expect(frame).toContain("<ColumnsMenu");
    expect(frame).toContain("toggleHidden(s, k, tableColumns)");
  });
  it("wraps the main table of a table workspace in LayoutList, which draws StudioBlockState", () => {
    expect(frame).toContain('preset === "table_workspace" || preset === "board"');
    expect(frame).toContain('shape={preset === "board" ? "board" : "rows"}');
    expect(frame).toContain("<LayoutList");
    expect(frame).toMatch(/function LayoutList[\s\S]*<StudioBlockState/);
  });
  it("passes retryable to the shared state and draws no list bar over a dashboard", () => {
    expect(frame).toContain("retryable={canRetryRead(rows.error)}");
    expect(frame).toContain('if (built.page.layout === "dashboard") return null;');
  });
  it("gives each stat and chart tile of a dashboard its own state", () => {
    expect(frame).toContain('preset === "dashboard"');
    expect(frame).toContain('view?.block === "stat" || view?.block === "chart"');
    expect(frame).toMatch(/<LayoutList[^>]*tile shape="tile"/);
  });
  it("gives the worklist of a dashboard the same shared states and Retry", () => {
    expect(frame).toContain('preset === "dashboard" && src && built.page.regions.worklist?.includes(id)');
    expect(frame).toMatch(/<LayoutList[^>]*tile shape="rows"/);
  });
});
