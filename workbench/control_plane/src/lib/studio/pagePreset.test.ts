/**
 * STU-LY-0 fence: the page presets read, the drop of a view keeps a layout page valid, no cell edit
 * works on a layout page, and a page with no preset reads as before.
 *
 * Mutation 1: let `readLayoutPage` keep a region name outside the preset. "drops a region" turns red.
 * Mutation 2: drop the `regions` strip in `dropViews`. "strips a deleted view from a region" turns red.
 * Mutation 3: remove `if (page.layout) return true` in `pageLayout.ts`. "refuses a cell edit" turns red.
 */
import { describe, expect, it } from "vitest";

import { removeView } from "./draftEdit";
import { LAYOUTS, PRESET_REGIONS, type PageDef, type StudioManifest } from "./manifest";
import { moveCell, setCellSpan } from "./pageLayout";
import { buildPage, readPage } from "./pageModel";

const board: PageDef = { id: "main", layout: "board", regions: { main: ["b"] } };
const dash: PageDef = { id: "dash", layout: "dashboard", regions: { kpis: ["s1", "s2"], charts: ["c1"] } };

describe("readPage with a preset", () => {
  it("reads the regions of a preset page and no cells", () => {
    const p = readPage(board);
    expect(p?.layout).toBe("board");
    expect(p?.regions).toEqual({ main: ["b"] });
    expect(p?.cells).toEqual([]);
    expect(p?.tabs).toEqual([]);
    expect(p?.side).toBeNull();
  });
  it("reads a page with no preset as before", () => {
    const p = readPage({ id: "p", cells: [{ view: "a" }] });
    expect(p?.layout).toBeNull();
    expect(p?.regions).toEqual({});
    expect(p?.cells).toHaveLength(1);
  });
  it("drops a region that the preset does not name, and keeps every region of the preset", () => {
    const p = readPage({ id: "p", layout: "board", regions: { main: ["b"], kpis: ["s1"] } });
    expect(Object.keys(p!.regions)).toEqual(["main"]);
    expect(Object.keys(readPage(dash)!.regions)).toEqual(["kpis", "charts", "worklist"]);
  });
  it("drops a view that is not declared, and cuts a region at its cap", () => {
    const p = readPage({ id: "p", layout: "table_workspace", regions: { main: ["ghost", "t", "t2"] } }, new Set(["t", "t2"]));
    expect(p?.regions.main).toEqual(["t"]);
  });
  it("returns null for an unknown preset or a preset page with a grid or a side view", () => {
    expect(readPage({ id: "p", layout: "calendar", regions: {} })).toBeNull();
    expect(readPage({ ...board, cells: [{ view: "b" }] })).toBeNull();
    expect(readPage({ ...board, side: { view: "b" } })).toBeNull();
  });
  it("builds a page whose cells are empty", () => {
    const built = buildPage({ views: [{ id: "b", block: "board" }], pages: [board] });
    expect(built.page.layout).toBe("board");
    expect(built.cells).toEqual([]);
  });
});

describe("the preset table", () => {
  it("names a region of the closed list for each preset, with one required", () => {
    for (const name of LAYOUTS) {
      const rules = PRESET_REGIONS[name];
      expect(Object.keys(rules).length).toBeGreaterThan(0);
      expect(Object.values(rules).some((r) => r.required)).toBe(true);
    }
  });
});

describe("edits on a preset page", () => {
  const m = {
    studio: 1,
    views: [{ id: "b", block: "board" }, { id: "s1", block: "stat" }, { id: "s2", block: "stat" }, { id: "c1", block: "chart" }],
    pages: [board, dash],
  } as unknown as StudioManifest;

  it("strips a deleted view from a region", () => {
    const out = removeView(m, "s2") as StudioManifest;
    expect(out.pages?.find((p) => p.id === "dash")?.regions?.kpis).toEqual(["s1"]);
  });
  it("drops a preset page that loses its last required view, with its rail rows", () => {
    const withNav = { ...m, nav: [{ title: "Work", rows: [{ page: "main" }, { page: "dash" }] }] } as unknown as StudioManifest;
    const out = removeView(withNav, "b") as StudioManifest;
    expect(out.pages?.map((p) => p.id)).toEqual(["dash"]);
    expect(JSON.stringify(out.nav)).not.toContain('"main"');
  });
  it("refuses a cell edit on a preset page and returns the input", () => {
    expect(setCellSpan(m, "main", { tabId: null, index: 0 }, 2)).toBe(m);
    expect(moveCell(m, "main", { tabId: null, index: 0 }, { tabId: null, index: 1 })).toBe(m);
  });
});
