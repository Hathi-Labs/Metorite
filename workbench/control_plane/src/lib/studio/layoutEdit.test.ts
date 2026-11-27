/**
 * STU-LY-8b fence: the edits of a page preset keep the draft valid, never change their input, never throw,
 * and return the input itself for a request that they refuse.
 *
 * Mutation 1: drop the block check in `setRegionViews`. "refuses a view of another block" turns red.
 * Mutation 2: let `setPageLayout` keep `cells` on a preset page. "leaves no grid on a preset page" turns red.
 * Mutation 3: drop the `keepCounts` call. "refuses a change that breaks a counted rail row" turns red.
 */
import { describe, expect, it } from "vitest";

import { FREE_GRID, fillRegions, presetDrops, presetOptions, regionChoices, setPageLayout, setRegionViews } from "./layoutEdit";
import { LAYOUTS } from "./manifest";

const deepFreeze = <T,>(x: T): T => {
  if (x && typeof x === "object" && !Object.isFrozen(x)) { Object.freeze(x); for (const v of Object.values(x as object)) deepFreeze(v); }
  return x;
};

const views = [
  { id: "t1", block: "table" }, { id: "t2", block: "table" }, { id: "b1", block: "board" },
  { id: "d1", block: "detail" }, { id: "s1", block: "stat" }, { id: "c1", block: "chart" },
];
const grid = (cells: string[]) => deepFreeze({ studio: 1, views, pages: [{ id: "p", title: "P", cells: cells.map((view) => ({ view })) }] });

describe("presetOptions", () => {
  it("offers the free grid first and each preset of the closed list", () => {
    const o = presetOptions();
    expect(o[0]).toEqual({ value: FREE_GRID, label: "Free grid" });
    expect(o.slice(1).map((x) => x.value)).toEqual([...LAYOUTS]);
  });
});

describe("fillRegions", () => {
  const blocks = new Map(views.map((v) => [v.id, v.block]));
  it("puts each view into the first region that takes its block and has room", () => {
    expect(fillRegions("list_detail", ["d1", "t1"], blocks).regions).toEqual({ list: ["t1"], detail: ["d1"] });
    expect(fillRegions("table_workspace", ["t1", "t2"], blocks)).toEqual({ regions: { main: ["t1"] }, dropped: 1 });
  });
  it("drops a view that fits no region, once each, and counts it", () => {
    expect(fillRegions("board", ["t1", "t1", "c1"], blocks)).toEqual({ regions: { main: [] }, dropped: 2 });
  });
});

describe("setPageLayout", () => {
  it("turns a grid page into a preset page and leaves no grid on it", () => {
    const m = grid(["t1", "t2"]);
    const out = setPageLayout(m, "p", "table_workspace") as { pages: Record<string, unknown>[] };
    const p = out.pages[0];
    expect(p.layout).toBe("table_workspace");
    expect(p.regions).toEqual({ main: ["t1"] });
    expect("cells" in p || "tabs" in p || "side" in p).toBe(false);
    expect(p.title).toBe("P");
  });
  it("turns a dashboard into the grid again, one cell for each view", () => {
    const dash = setPageLayout(grid(["s1", "c1", "t1"]), "p", "dashboard");
    const back = setPageLayout(dash, "p", null) as { pages: Record<string, unknown>[] };
    expect(back.pages[0].cells).toEqual([{ view: "s1" }, { view: "c1" }, { view: "t1" }]);
    expect("layout" in back.pages[0] || "regions" in back.pages[0]).toBe(false);
  });
  it("moves the views of one preset into another, by block", () => {
    const a = setPageLayout(grid(["t1", "d1"]), "p", "list_detail");
    const b = setPageLayout(a, "p", "work_queue") as { pages: { regions: unknown }[] };
    expect(b.pages[0].regions).toEqual({ list: ["t1"], detail: ["d1"] });
  });
  it("refuses an unknown preset, an unknown page and the same preset", () => {
    const m = grid(["t1"]);
    expect(setPageLayout(m, "p", "gallery" as never)).toBe(m);
    expect(setPageLayout(m, "ghost", "board")).toBe(m);
    const p = setPageLayout(m, "p", "table_workspace");
    expect(setPageLayout(p, "p", "table_workspace")).toBe(p);
  });
  it("refuses to go back to the grid with no view, and a grid page that is already a grid", () => {
    const m = grid(["t1"]);
    expect(setPageLayout(m, "p", null)).toBe(m);
    const empty = setPageLayout(grid(["s1"]), "p", "board");
    expect(setPageLayout(empty, "p", null)).toBe(empty);
  });
  it("refuses a change that breaks a counted rail row", () => {
    const m = deepFreeze({ studio: 1, views, nav: [{ title: "S", rows: [{ page: "p", count: true }] }],
      pages: [{ id: "p", cells: [{ view: "t1" }] }] });
    // A board page holds no table, and the preset moves t1 into the board's region only if it is a board: it is not.
    expect(setPageLayout(m, "p", "board")).toBe(m);
  });
  it("never throws on junk", () => {
    for (const j of [null, undefined, 5, "x", [], {}, { pages: 5 }, { pages: [null] }]) {
      expect(setPageLayout(j, "p", "board")).toBe(j);
    }
  });
});

describe("presetDrops (LY-8b: nothing goes silently)", () => {
  it("counts the views that a switch to a preset leaves out, the same count as fillRegions", () => {
    const m = grid(["t1", "t2"]);
    expect(presetDrops(m, "p", "table_workspace")).toBe(1);
    const after = setPageLayout(m, "p", "table_workspace") as { pages: { regions: Record<string, string[]> }[] };
    expect(Object.values(after.pages[0].regions).flat()).toEqual(["t1"]);
  });
  it("is 0 for the free grid, an unknown page and the current preset", () => {
    const m = grid(["t1", "t2"]);
    expect(presetDrops(m, "p", null)).toBe(0);
    expect(presetDrops(m, "nope", "table_workspace")).toBe(0);
    const preset = setPageLayout(m, "p", "table_workspace");
    expect(presetDrops(preset, "p", "table_workspace")).toBe(0);
  });
});

describe("setRegionViews", () => {
  const preset = () => setPageLayout(grid(["t1"]), "p", "list_detail");
  it("sets the views of a region", () => {
    const out = setRegionViews(setPageLayout(grid(["s1"]), "p", "dashboard"), "p", "kpis", ["s1"]) as { pages: { regions: Record<string, string[]> }[] };
    expect(out.pages[0].regions.kpis).toEqual(["s1"]);
  });
  it("refuses a view of another block", () => {
    const m = preset();
    expect(setRegionViews(m, "p", "list", ["b1"])).toBe(m);
  });
  it("refuses more views than the cap, a repeated view, an unknown region and a page with no preset", () => {
    const m = preset();
    expect(setRegionViews(m, "p", "list", ["t1", "t2"])).toBe(m);
    expect(setRegionViews(m, "p", "bogus", ["t1"])).toBe(m);
    const g = grid(["t1"]);
    expect(setRegionViews(g, "p", "list", ["t1"])).toBe(g);
    const dash = setPageLayout(grid(["s1"]), "p", "dashboard");
    expect(setRegionViews(dash, "p", "kpis", ["s1", "s1"])).toBe(dash);
  });
  it("refuses to empty a required region, and allows an empty optional region", () => {
    const dash = setPageLayout(grid(["s1", "c1"]), "p", "dashboard");
    expect(setRegionViews(dash, "p", "kpis", [])).toBe(dash);
    const out = setRegionViews(dash, "p", "charts", []) as { pages: { regions: Record<string, string[]> }[] };
    expect(out.pages[0].regions.charts).toEqual([]);
  });
});

describe("regionChoices", () => {
  it("lists the views of the block of the region that the region does not hold yet", () => {
    const m = setPageLayout(grid(["t1"]), "p", "list_detail");
    expect(regionChoices(m, "p", "list")).toEqual(["t2"]);
    expect(regionChoices(m, "p", "detail")).toEqual(["d1"]);
    expect(regionChoices(m, "p", "bogus")).toEqual([]);
    expect(regionChoices(grid(["t1"]), "p", "list")).toEqual([]);
  });
});
