/**
 * STU-AF11a fence: the layout edits keep `pages` valid, never change their input, never throw,
 * and return the input itself for a bad request.
 *
 * Mutation 1: drop the span-over-cols refusal in `setCellSpan`. "refuses a span over cols" and the loop turn red.
 * Mutation 2: allow the 13th cell in `addCell`. "refuses the 13th cell" and the loop turn red.
 * Mutation 3: make `moveCell` change its input. "never changes its input" turns red.
 * Mutation 4: make a function throw for an unknown page. "returns the input for an unknown page" turns red.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import type { CellDef, PageDef, StudioManifest } from "./manifest";
import { addCell, cellCapRoom, clampSpans, moveCell, moveCellToTab, removeCell, setCellSpan, type Loc } from "./pageLayout";
import { PAGE_CAPS } from "./pageModel";

const FIXTURE = resolve(__dirname, "__fixtures__/pageLayout_outputs.json");

function deepFreeze<T>(x: T): T {
  if (x && typeof x === "object" && !Object.isFrozen(x)) {
    Object.freeze(x);
    for (const v of Object.values(x as object)) deepFreeze(v);
  }
  return x;
}

const cell = (view: string, span?: number): CellDef => (span === undefined ? { view } : { view, span });
const views = ["board", "week", "check_in"].map((id) => ({ id }));

function base(): StudioManifest {
  return {
    studio: 1,
    id: "layout-app",
    views: [...views],
    pages: [
      { id: "home", cols: 3, cells: [cell("board", 2), cell("week"), cell("check_in")], side: { view: "week" } },
      { id: "work", cols: 2, tabs: [
        { id: "a", title: "A", cells: [cell("board"), cell("check_in", 2)] },
        { id: "b", title: "B", cells: [cell("week")] },
      ] },
      { id: "solo", cols: 4, cells: [cell("check_in", 4)] },
    ],
  } as unknown as StudioManifest;
}

const pg = (m: StudioManifest, id: string) => (m.pages as PageDef[]).find((p) => p.id === id) as PageDef;
const names = (cells: CellDef[] | undefined) => (cells ?? []).map((c) => c.view);
const frozen = () => deepFreeze(base());

/** A manifest with a list of n cells on a page with no tabs, plus one more page for the cross-tab case. */
function full(n: number, tabs = false): StudioManifest {
  const m = base();
  const cells = Array.from({ length: n }, () => cell("board"));
  m.pages = tabs
    ? [{ id: "work", cols: 2, tabs: [{ id: "a", title: "A", cells: [cell("week")] }, { id: "b", title: "B", cells }] }]
    : [{ id: "home", cols: 2, cells }];
  return deepFreeze(m);
}

/** The checks of `_check_pages` and the caps of the schema, in TS. */
function problems(m: StudioManifest): string[] {
  const out: string[] = [];
  const ids = new Set((m.views ?? []).map((v) => v.id));
  const pages = m.pages ?? [];
  if (pages.length > PAGE_CAPS.pages) out.push("too many pages");
  if (new Set(pages.map((p) => p.id)).size !== pages.length) out.push("duplicate page id");
  const cellsOk = (cells: CellDef[], where: string, cols: number) => {
    if (cells.length > PAGE_CAPS.cellsPerList) out.push(`${where}: too many cells`);
    for (const c of cells) {
      if (!ids.has(c.view)) out.push(`${where}: undeclared view ${c.view}`);
      if (c.span !== undefined && c.span !== null) {
        if (!Number.isInteger(c.span) || c.span < 1 || c.span > PAGE_CAPS.maxCols) out.push(`${where}: span out of range`);
        if (c.span > cols) out.push(`${where}: span over cols`);
      }
    }
  };
  for (const p of pages) {
    const cols = p.cols ?? 2;
    if (cols < 1 || cols > PAGE_CAPS.maxCols) out.push(`${p.id}: cols out of range`);
    const tabs = p.tabs ?? [];
    const cells = p.cells ?? [];
    if (tabs.length && cells.length) out.push(`${p.id}: tabs and cells`);
    if (!tabs.length && !cells.length) out.push(`${p.id}: no layout`);
    if (tabs.length > PAGE_CAPS.tabsPerPage) out.push(`${p.id}: too many tabs`);
    if (new Set(tabs.map((t) => t.id)).size !== tabs.length) out.push(`${p.id}: duplicate tab id`);
    for (const t of tabs) cellsOk(t.cells, `${p.id}/${t.id}`, cols);
    cellsOk(cells, p.id, cols);
    if (p.side && !ids.has(p.side.view)) out.push(`${p.id}: undeclared side view`);
  }
  return out;
}

describe("pageLayout caps", () => {
  it("equal the maxItems of the schema file", () => {
    const file = resolve(__dirname, "../../../../../packages/acb_common/acb_common/studio_manifest.schema.json");
    const schema = JSON.parse(readFileSync(file, "utf-8"));
    expect(schema.properties.pages.maxItems).toBe(PAGE_CAPS.pages);
    expect(cellCapRoom(full(0), "home", null)).toBe(PAGE_CAPS.cellsPerList);
  });
});

describe("moveCell", () => {
  it("moves a cell inside a list", () => {
    const m = frozen();
    expect(names(pg(moveCell(m, "home", { tabId: null, index: 0 }, { tabId: null, index: 1 }), "home").cells)).toEqual(["week", "board", "check_in"]);
  });
  it("moves a cell to the end", () => {
    const m = frozen();
    expect(names(pg(moveCell(m, "home", { tabId: null, index: 0 }, { tabId: null, index: 99 }), "home").cells)).toEqual(["week", "check_in", "board"]);
  });
  it("moves a cell to the start", () => {
    const m = frozen();
    const next = moveCell(m, "home", { tabId: null, index: 2 }, { tabId: null, index: 0 });
    expect(names(pg(next, "home").cells)).toEqual(["check_in", "board", "week"]);
    expect(pg(next, "home").cells?.[1].span).toBe(2);
  });
  it("moves a cell across two tabs", () => {
    const m = frozen();
    const next = moveCell(m, "work", { tabId: "a", index: 1 }, { tabId: "b", index: 0 });
    const tabs = pg(next, "work").tabs!;
    expect(names(tabs[0].cells)).toEqual(["board"]);
    expect(tabs[1].cells).toEqual([cell("check_in", 2), cell("week")]);
    expect(problems(next)).toEqual([]);
  });
  it("lets a tab go empty, as the server does", () => {
    const m = frozen();
    const next = moveCell(m, "work", { tabId: "b", index: 0 }, { tabId: "a", index: 0 });
    expect(pg(next, "work").tabs![1].cells).toEqual([]);
    expect(problems(next)).toEqual([]);
  });
  it("refuses a move that would leave a page list empty", () => {
    // A page-level list has no second list. The only moves are inside it, and they keep its length.
    const m = frozen();
    expect(moveCell(m, "solo", { tabId: null, index: 0 }, { tabId: "a", index: 0 })).toBe(m);
    expect(moveCell(m, "work", { tabId: "a", index: 0 }, { tabId: null, index: 0 })).toBe(m);
  });
  it("refuses a move into a full tab", () => {
    const m = full(PAGE_CAPS.cellsPerList, true);
    expect(moveCell(m, "work", { tabId: "a", index: 0 }, { tabId: "b", index: 0 })).toBe(m);
  });
  it("allows a move inside a full list", () => {
    const m = full(PAGE_CAPS.cellsPerList);
    expect(moveCell(m, "home", { tabId: null, index: 0 }, { tabId: null, index: 5 })).not.toBe(m);
  });
  it("returns the input for a move that changes nothing", () => {
    const m = frozen();
    expect(moveCell(m, "home", { tabId: null, index: 1 }, { tabId: null, index: 1 })).toBe(m);
  });
});

describe("moveCellToTab", () => {
  it("appends to the end of the target tab", () => {
    const m = frozen();
    const next = moveCellToTab(m, "work", { tabId: "b", index: 0 }, "a");
    expect(names(pg(next, "work").tabs![0].cells)).toEqual(["board", "check_in", "week"]);
  });
  it("refuses a page with no tabs", () => {
    const m = frozen();
    expect(moveCellToTab(m, "home", { tabId: null, index: 0 }, "a")).toBe(m);
    expect(moveCellToTab(m, "home", { tabId: "a", index: 0 }, "b")).toBe(m);
  });
  it("refuses an unknown target tab", () => {
    const m = frozen();
    expect(moveCellToTab(m, "work", { tabId: "a", index: 0 }, "zz")).toBe(m);
  });
  it("refuses a move into a full tab", () => {
    const m = full(PAGE_CAPS.cellsPerList, true);
    expect(moveCellToTab(m, "work", { tabId: "a", index: 0 }, "b")).toBe(m);
  });
});

describe("setCellSpan", () => {
  it("sets a valid span", () => {
    const m = frozen();
    expect(pg(setCellSpan(m, "home", { tabId: null, index: 1 }, 3), "home").cells?.[1].span).toBe(3);
  });
  it("refuses a span over cols", () => {
    const m = frozen();
    expect(setCellSpan(m, "home", { tabId: null, index: 1 }, 4)).toBe(m);
    expect(setCellSpan(m, "work", { tabId: "a", index: 0 }, 3)).toBe(m);
  });
  it("refuses a span that is not 1 to 4", () => {
    const m = frozen();
    for (const bad of [0, -1, 1.5, Number.NaN, 5]) expect(setCellSpan(m, "solo", { tabId: null, index: 0 }, bad)).toBe(m);
  });
  it("clears a span with null", () => {
    const m = frozen();
    const next = setCellSpan(m, "home", { tabId: null, index: 0 }, null);
    expect("span" in pg(next, "home").cells![0]).toBe(false);
    expect(setCellSpan(next, "home", { tabId: null, index: 0 }, null)).toBe(next);
  });
});

describe("addCell and removeCell", () => {
  it("adds a cell at an index", () => {
    const m = frozen();
    expect(names(pg(addCell(m, "home", { tabId: null, index: 1 }, "check_in"), "home").cells)).toEqual(["board", "check_in", "week", "check_in"]);
  });
  it("allows a view twice", () => {
    const m = frozen();
    expect(names(pg(addCell(m, "home", { tabId: null, index: 0 }, "board"), "home").cells).filter((v) => v === "board")).toHaveLength(2);
  });
  it("refuses an undeclared view", () => {
    const m = frozen();
    expect(addCell(m, "home", { tabId: null, index: 0 }, "nope")).toBe(m);
  });
  it("refuses the 13th cell", () => {
    const m = full(PAGE_CAPS.cellsPerList);
    expect(addCell(m, "home", { tabId: null, index: 0 }, "board")).toBe(m);
    const eleven = full(PAGE_CAPS.cellsPerList - 1);
    expect(pg(addCell(eleven, "home", { tabId: null, index: 0 }, "board"), "home").cells).toHaveLength(PAGE_CAPS.cellsPerList);
  });
  it("removes a cell", () => {
    const m = frozen();
    expect(names(pg(removeCell(m, "home", { tabId: null, index: 1 }), "home").cells)).toEqual(["board", "check_in"]);
  });
  it("refuses to empty the only list of a page", () => {
    const m = frozen();
    expect(removeCell(m, "solo", { tabId: null, index: 0 })).toBe(m);
  });
  it("lets a tab go empty", () => {
    const m = frozen();
    expect(pg(removeCell(m, "work", { tabId: "b", index: 0 }), "work").tabs![1].cells).toEqual([]);
  });
});

describe("cellCapRoom", () => {
  it("counts the free slots", () => {
    const m = frozen();
    expect(cellCapRoom(m, "home", null)).toBe(PAGE_CAPS.cellsPerList - 3);
    expect(cellCapRoom(m, "work", "b")).toBe(PAGE_CAPS.cellsPerList - 1);
    expect(cellCapRoom(full(PAGE_CAPS.cellsPerList), "home", null)).toBe(0);
  });
  it("is zero for an unknown place", () => {
    const m = frozen();
    expect(cellCapRoom(m, "zz", null)).toBe(0);
    expect(cellCapRoom(m, "home", "a")).toBe(0);
    expect(cellCapRoom(m, "work", null)).toBe(0);
  });
});

describe("clampSpans", () => {
  it("lowers a span to the page cols", () => {
    const page: PageDef = { id: "p", cols: 2, cells: [cell("board", 4), cell("week", 1), cell("check_in")] };
    expect(clampSpans(page).cells).toEqual([cell("board", 2), cell("week", 1), cell("check_in")]);
    expect(page.cells![0].span).toBe(4);
  });
  it("lowers a span inside tabs", () => {
    const page: PageDef = { id: "p", cols: 1, tabs: [{ id: "a", title: "A", cells: [cell("board", 3)] }, { id: "b", title: "B", cells: [cell("week")] }] };
    const next = clampSpans(page);
    expect(next.tabs![0].cells[0].span).toBe(1);
    expect(next.tabs![1]).toBe(page.tabs![1]);
  });
  it("returns the same page when no span is too wide", () => {
    const page: PageDef = { id: "p", cols: 3, cells: [cell("board", 3)] };
    expect(clampSpans(page)).toBe(page);
  });
});

describe("bad requests", () => {
  const home: Loc = { tabId: null, index: 0 };
  it("returns the input for an unknown page, tab or index", () => {
    const m = frozen();
    expect(moveCell(m, "zz", home, home)).toBe(m);
    expect(moveCell(m, "work", { tabId: "zz", index: 0 }, { tabId: "a", index: 0 })).toBe(m);
    expect(moveCell(m, "work", { tabId: "a", index: 0 }, { tabId: "zz", index: 0 })).toBe(m);
    expect(moveCell(m, "home", { tabId: null, index: 9 }, home)).toBe(m);
    expect(moveCell(m, "home", { tabId: null, index: -1 }, home)).toBe(m);
    expect(moveCell(m, "home", home, { tabId: null, index: 1.5 })).toBe(m);
    expect(moveCellToTab(m, "zz", home, "a")).toBe(m);
    expect(setCellSpan(m, "zz", home, 1)).toBe(m);
    expect(setCellSpan(m, "home", { tabId: null, index: 9 }, 1)).toBe(m);
    expect(addCell(m, "zz", home, "board")).toBe(m);
    expect(removeCell(m, "zz", home)).toBe(m);
    expect(removeCell(m, "home", { tabId: null, index: 9 })).toBe(m);
  });
  it("returns the input for every edit on a page that has both tabs and cells", () => {
    // The server refuses such a page. An edit must not make it look valid.
    const m = base();
    m.pages = [{ id: "mixed", cols: 3, cells: [cell("board"), cell("week")], tabs: [{ id: "a", title: "A", cells: [cell("check_in")] }, { id: "b", title: "B", cells: [] }] }] as unknown as StudioManifest["pages"];
    deepFreeze(m);
    const top: Loc = { tabId: null, index: 0 };
    const inA: Loc = { tabId: "a", index: 0 };
    expect(moveCell(m, "mixed", top, { tabId: null, index: 1 })).toBe(m);
    expect(moveCell(m, "mixed", inA, { tabId: "b", index: 0 })).toBe(m);
    expect(moveCellToTab(m, "mixed", inA, "b")).toBe(m);
    expect(setCellSpan(m, "mixed", inA, 2)).toBe(m);
    expect(setCellSpan(m, "mixed", top, 2)).toBe(m);
    expect(addCell(m, "mixed", inA, "week")).toBe(m);
    expect(addCell(m, "mixed", top, "week")).toBe(m);
    expect(removeCell(m, "mixed", inA)).toBe(m);
    expect(removeCell(m, "mixed", top)).toBe(m);
  });
  it("does not throw on a manifest with no pages", () => {
    const m = deepFreeze({ studio: 1, id: "x" } as unknown as StudioManifest);
    expect(moveCell(m, "home", home, home)).toBe(m);
    expect(addCell(m, "home", home, "board")).toBe(m);
    expect(cellCapRoom(m, "home", null)).toBe(0);
  });
  it("never changes its input", () => {
    const m = frozen();
    const copy = JSON.stringify(m);
    moveCell(m, "home", home, { tabId: null, index: 2 });
    moveCell(m, "work", { tabId: "a", index: 0 }, { tabId: "b", index: 0 });
    moveCellToTab(m, "work", { tabId: "b", index: 0 }, "a");
    setCellSpan(m, "home", home, 1);
    setCellSpan(m, "home", home, null);
    addCell(m, "work", { tabId: "a", index: 0 }, "week");
    removeCell(m, "home", home);
    clampSpans(pg(m, "home"));
    expect(JSON.stringify(m)).toBe(copy);
  });
});

/** A small deterministic generator, so the run is the same every time. */
function rng(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** 200 random edits, valid and not. Each result passes the checker. Some manifests are kept. */
function run(seed: number): Array<{ seed: number; op: number; manifest: StudioManifest }> {
  const r = rng(seed);
  const pick = <T>(xs: readonly T[]): T => xs[Math.floor(r() * xs.length)];
  let m = frozen();
  const kept: Array<{ seed: number; op: number; manifest: StudioManifest }> = [];
  for (let op = 1; op <= 200; op++) {
    const pageId = pick(["home", "work", "solo", "zz"]);
    const tabIds = [null, "a", "b", "zz"] as const;
    const loc = (): Loc => ({ tabId: pick(tabIds), index: Math.floor(r() * 14) - 1 });
    const before = JSON.stringify(m);
    let next: StudioManifest;
    switch (Math.floor(r() * 5)) {
      case 0: next = moveCell(m, pageId, loc(), loc()); break;
      case 1: next = setCellSpan(m, pageId, loc(), pick([null, 0, 1, 2, 3, 4, 5])); break;
      case 2: next = moveCellToTab(m, pageId, loc(), pick(["a", "b", "zz"])); break;
      case 3: next = addCell(m, pageId, loc(), pick(["board", "week", "check_in", "nope"])); break;
      default: next = removeCell(m, pageId, loc());
    }
    expect(JSON.stringify(m), `op ${op} changed its input`).toBe(before);
    expect(problems(next), `op ${op} broke the pages`).toEqual([]);
    m = deepFreeze(next);
    if (op % 50 === 0) kept.push({ seed, op, manifest: JSON.parse(JSON.stringify(m)) });
  }
  return kept;
}

describe("a random run", () => {
  it("keeps every result valid and matches the fixture the server check reads", () => {
    const outputs = [11, 23, 47].flatMap((seed) => run(seed));
    expect(outputs.length).toBeGreaterThan(0);
    const expected = JSON.parse(readFileSync(FIXTURE, "utf-8"));
    expect(outputs).toEqual(expected);
  });
});
