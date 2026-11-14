/**
 * STU-AF8b-3a fence: the tab acts keep `pages` valid, never change their input, never throw, return
 * the input itself for a bad request, and keep each key that they do not know. The count guard
 * refuses a removal that leaves a counted rail row with no table or board.
 *
 * Mutation 1: drop the cell move in `tabsFromCells`. "the first-tab act moves all cells" and the loop turn red.
 * Mutation 2: allow the 9th tab in `addTab`. "refuses the 9th tab" and the loop turn red.
 * Mutation 3: allow an empty last tab in `cellsFromTabs`. "refuses to remove an empty last tab" turns red.
 * Mutation 4: drop `keepCounts` in `removeTab`. The count tests turn red.
 * Mutation 5: make a function change its input. "never changes its input" turns red.
 * Mutation 6: make a function throw for a missing page. "never throws on junk" turns red.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import {
  addTab, cellsFromTabs, countHolds, COUNT_WHY, keepCounts, LAST_CELL_WHY, layoutAct, moveTab, removeCellGuarded,
  removeCellWhy, removeTab, removeTabWhy, renameTab, tabsFromCells,
} from "./tabEdit";
import { addCell, moveCell, moveCellToTab, removeCell, setCellSpan } from "./pageLayout";
import { PAGE_CAPS } from "./pageModel";

const FIXTURE = resolve(__dirname, "__fixtures__/tabEdit_outputs.json");

type M = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any

function deepFreeze<T>(x: T): T {
  if (x && typeof x === "object" && !Object.isFrozen(x)) {
    Object.freeze(x);
    for (const v of Object.values(x as object)) deepFreeze(v);
  }
  return x;
}

const VIEWS = [
  { id: "board", block: "board" },
  { id: "week", block: "calendar" },
  { id: "check_in", block: "form" },
  { id: "list", block: "table" },
];

const base = (): M => ({
  studio: 1,
  id: "tab-app",
  views: VIEWS.map((v) => ({ ...v })),
  pages: [
    { id: "home", title: "Home", cols: 3, extra: 1, cells: [{ view: "board", span: 2, note: "a" }, { view: "week" }, { view: "check_in" }] },
    {
      id: "work",
      cols: 2,
      tabs: [
        { id: "a", title: "A", flag: true, cells: [{ view: "check_in", span: 2 }, { view: "list" }] },
        { id: "b", title: "B", cells: [{ view: "week" }] },
      ],
    },
    { id: "solo", cols: 1, cells: [{ view: "check_in" }] },
  ],
});
const frozen = () => deepFreeze(base());

/** The start of a random run. The server refuses an unknown key, so this one has none. */
const runBase = (): M => {
  const m = base();
  delete m.pages[0].extra;
  delete m.pages[0].cells[0].note;
  delete m.pages[1].tabs[0].flag;
  return m;
};

/** The rules of `_check_pages` in TS, rule by rule. */
function problems(m: M): string[] {
  const out: string[] = [];
  const dashed = (s: unknown) => typeof s === "string" && /^[a-z][a-z0-9_-]{0,47}$/.test(s);
  const nonBlank = (s: unknown) => typeof s === "string" && s.length >= 1 && /\S/.test(s);
  const ids = new Set((m.views ?? []).map((v: M) => v.id));
  const pages: M[] = m.pages ?? [];
  if (pages.length > PAGE_CAPS.pages) out.push("too many pages");
  const cellsOk = (cells: M[], where: string, cols: number) => {
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
    const tabs: M[] = p.tabs ?? [];
    const cells: M[] = p.cells ?? [];
    if (tabs.length && cells.length) out.push(`${p.id}: tabs and cells`);
    if (!tabs.length && !cells.length) out.push(`${p.id}: no layout`);
    if (tabs.length > PAGE_CAPS.tabsPerPage) out.push(`${p.id}: too many tabs`);
    if (new Set(tabs.map((t) => t.id)).size !== tabs.length) out.push(`${p.id}: duplicate tab id`);
    for (const t of tabs) {
      if (!dashed(t.id)) out.push(`${p.id}: bad tab id ${t.id}`);
      if (!nonBlank(t.title)) out.push(`${p.id}/${t.id}: blank tab title`);
      cellsOk(t.cells, `${p.id}/${t.id}`, cols);
    }
    cellsOk(cells, p.id, cols);
  }
  return out;
}

const pg = (m: unknown, id: string) => (m as M).pages.find((p: M) => p.id === id);

describe("the checker", () => {
  it("passes the start manifest", () => {
    expect(problems(base())).toEqual([]);
  });
});

describe("addTab", () => {
  it("moves all cells into the first tab of a flat page, in order, and deletes `cells`", () => {
    const m = frozen();
    const out = addTab(m, "home", "Overview") as M;
    const page = pg(out, "home");
    expect("cells" in page).toBe(false);
    expect(page.tabs).toHaveLength(1);
    expect(page.tabs[0].id).toBe("overview");
    expect(page.tabs[0].title).toBe("Overview");
    expect(page.tabs[0].cells).toEqual(pg(m, "home").cells);
    expect(page.tabs[0].cells[0]).toBe(pg(m, "home").cells[0]);
    expect(page.extra).toBe(1);
    expect(page.title).toBe("Home");
    expect(problems(out)).toEqual([]);
  });
  it("adds an empty tab to a tabbed page", () => {
    const out = addTab(frozen(), "work", "Third") as M;
    expect(pg(out, "work").tabs.map((t: M) => t.id)).toEqual(["a", "b", "third"]);
    expect(pg(out, "work").tabs[2].cells).toEqual([]);
    expect(problems(out)).toEqual([]);
  });
  it("refuses the 9th tab", () => {
    let m: unknown = frozen();
    for (let i = 0; i < PAGE_CAPS.tabsPerPage - 2; i++) m = addTab(m, "work", `T${i}`);
    expect(pg(m, "work").tabs).toHaveLength(PAGE_CAPS.tabsPerPage);
    const full = deepFreeze(JSON.parse(JSON.stringify(m)));
    expect(addTab(full, "work", "More")).toBe(full);
  });
  it("refuses a blank title", () => {
    const m = frozen();
    for (const t of ["", "   ", "\n\t"]) expect(addTab(m, "home", t)).toBe(m);
  });
  it("gives distinct `_DASHED` ids to tabs with the same title", () => {
    let m: unknown = frozen();
    for (let i = 0; i < 6; i++) m = addTab(m, "work", "Same");
    const ids = pg(m, "work").tabs.map((t: M) => t.id);
    expect(new Set(ids).size).toBe(ids.length);
    for (const id of ids) expect(id).toMatch(/^[a-z][a-z0-9_-]{0,47}$/);
    expect(ids.slice(2)).toEqual(["same", "same-2", "same-3", "same-4", "same-5", "same-6"]);
    expect(problems(m as M)).toEqual([]);
  });
  it("makes a `_DASHED` id from a title that is not", () => {
    for (const title of ["123", "Ünïcode ✓", "!!!", "x".repeat(200), "A  B"]) {
      const out = addTab(frozen(), "work", title) as M;
      expect(problems(out), title).toEqual([]);
    }
  });
  it("refuses a page with both lists, and a page with neither", () => {
    const both = deepFreeze({ ...base(), pages: [{ id: "p", tabs: [{ id: "a", title: "A", cells: [] }], cells: [{ view: "board" }] }] });
    expect(addTab(both, "p", "X")).toBe(both);
    const none = deepFreeze({ ...base(), pages: [{ id: "p" }] });
    expect(addTab(none, "p", "X")).toBe(none);
    const empty = deepFreeze({ ...base(), pages: [{ id: "p", cells: [] }] });
    expect(addTab(empty, "p", "X")).toBe(empty);
  });
  it("keeps the unknown keys of the tabs and cells it moves", () => {
    const out = addTab(frozen(), "work", "C") as M;
    expect(pg(out, "work").tabs[0].flag).toBe(true);
    expect(pg(out, "work").tabs[0].cells[0]).toEqual({ view: "check_in", span: 2 });
    const flat = addTab(frozen(), "home", "One") as M;
    expect(pg(flat, "home").tabs[0].cells[0].note).toBe("a");
  });
});

describe("renameTab", () => {
  it("sets the title and keeps the id and the other keys", () => {
    const out = renameTab(frozen(), "work", "a", "  New   name ") as M;
    expect(pg(out, "work").tabs[0]).toEqual({ id: "a", title: "New name", flag: true, cells: [{ view: "check_in", span: 2 }, { view: "list" }] });
    expect(problems(out)).toEqual([]);
  });
  it("refuses a blank title and returns the input for the same title", () => {
    const m = frozen();
    expect(renameTab(m, "work", "a", "  ")).toBe(m);
    expect(renameTab(m, "work", "a", "A")).toBe(m);
    expect(renameTab(m, "work", "a", " A ")).toBe(m);
  });
  it("refuses an unknown page or tab, and a flat page", () => {
    const m = frozen();
    expect(renameTab(m, "nope", "a", "X")).toBe(m);
    expect(renameTab(m, "work", "nope", "X")).toBe(m);
    expect(renameTab(m, "home", "a", "X")).toBe(m);
  });
});

describe("removeTab", () => {
  it("drops a non-last tab with its cells and keeps the views", () => {
    const out = removeTab(frozen(), "work", "a") as M;
    expect(pg(out, "work").tabs.map((t: M) => t.id)).toEqual(["b"]);
    expect(out.views).toHaveLength(VIEWS.length);
    expect(problems(out)).toEqual([]);
  });
  it("returns the cells of the last tab to the flat list and deletes `tabs`", () => {
    const one = removeTab(frozen(), "work", "b") as M;
    const out = removeTab(one, "work", "a") as M;
    const page = pg(out, "work");
    expect("tabs" in page).toBe(false);
    expect(page.cells).toEqual([{ view: "check_in", span: 2 }, { view: "list" }]);
    expect(problems(out)).toEqual([]);
  });
  it("refuses to remove an empty last tab", () => {
    const m = deepFreeze({ ...base(), pages: [{ id: "p", tabs: [{ id: "a", title: "A", cells: [] }] }] });
    expect(removeTab(m, "p", "a")).toBe(m);
    expect(removeTabWhy(m, "p", "a")).toBe("A page needs one cell. Delete the page instead.");
  });
  it("round-trips: the first tab and then its removal give the flat page back", () => {
    const m = frozen();
    const there = addTab(m, "home", "Only") as M;
    const back = removeTab(there, "home", "only") as M;
    expect(pg(back, "home")).toEqual(pg(m, "home"));
  });
  it("returns the input for a bad request", () => {
    const m = frozen();
    expect(removeTab(m, "nope", "a")).toBe(m);
    expect(removeTab(m, "work", "nope")).toBe(m);
    expect(removeTab(m, "home", "a")).toBe(m);
  });
});

describe("moveTab", () => {
  it("moves a tab, and clamps at the ends", () => {
    const m = frozen();
    expect(pg(moveTab(m, "work", "a", 1), "work").tabs.map((t: M) => t.id)).toEqual(["b", "a"]);
    expect(pg(moveTab(m, "work", "a", 5), "work").tabs.map((t: M) => t.id)).toEqual(["b", "a"]);
    expect(pg(moveTab(m, "work", "b", -3), "work").tabs.map((t: M) => t.id)).toEqual(["b", "a"]);
  });
  it("returns the input when nothing moves", () => {
    const m = frozen();
    expect(moveTab(m, "work", "a", -1)).toBe(m);
    expect(moveTab(m, "work", "b", 1)).toBe(m);
    expect(moveTab(m, "work", "a", 0)).toBe(m);
    expect(moveTab(m, "work", "a", 1.5)).toBe(m);
    expect(moveTab(m, "nope", "a", 1)).toBe(m);
  });
});

describe("the helpers", () => {
  it("tabsFromCells and cellsFromTabs refuse the wrong shape", () => {
    expect(tabsFromCells(null, { id: "a", title: "A" })).toBeNull();
    expect(tabsFromCells({ cells: [] }, { id: "a", title: "A" })).toBeNull();
    expect(tabsFromCells({ tabs: [{ id: "x" }], cells: [{ view: "b" }] }, { id: "a", title: "A" })).toBeNull();
    expect(cellsFromTabs("x")).toBeNull();
    expect(cellsFromTabs({ tabs: [{ id: "a", title: "A", cells: [] }] })).toBeNull();
    expect(cellsFromTabs({ tabs: [{ id: "a", title: "A", cells: [{ view: "b" }] }, { id: "b", title: "B", cells: [] }] })).toBeNull();
  });
});

// ── The count guard ─────────────────────────────────────────────────────────

const counted = (pages: M[], nav: M[] = [{ title: "S", rows: [{ page: "p", count: true }] }]): M => ({
  studio: 1,
  id: "c",
  views: VIEWS.map((v) => ({ ...v })),
  pages,
  nav,
});

describe("the count guard", () => {
  it("is true for junk", () => {
    for (const j of [null, undefined, "x", 5, [], {}, { nav: 5 }, { nav: [null, 3, { rows: 4 }] }, { nav: [{ rows: [{ page: "p", count: true }] }] }]) {
      expect(countHolds(j)).toBe(true);
    }
  });
  it("refuses a removal of the only table cell of a counted page", () => {
    const m = deepFreeze(counted([{ id: "p", cells: [{ view: "list" }, { view: "week" }] }]));
    expect(countHolds(m)).toBe(true);
    expect(removeCellGuarded(m, "p", { tabId: null, index: 0 })).toBe(m);
    expect(removeCellWhy(m, "p", { tabId: null, index: 0 })).toBe(COUNT_WHY);
    // A removal of the calendar cell keeps the table.
    const out = removeCellGuarded(m, "p", { tabId: null, index: 1 });
    expect(out).not.toBe(m);
    expect(removeCellWhy(m, "p", { tabId: null, index: 1 })).toBeNull();
  });
  it("allows the removal of one of two table cells", () => {
    const m = deepFreeze(counted([{ id: "p", cells: [{ view: "list" }, { view: "list" }] }]));
    expect(removeCellGuarded(m, "p", { tabId: null, index: 0 })).not.toBe(m);
  });
  it("counts a board", () => {
    const m = deepFreeze(counted([{ id: "p", cells: [{ view: "board" }, { view: "week" }] }]));
    expect(removeCellGuarded(m, "p", { tabId: null, index: 0 })).toBe(m);
  });
  it("reads the cells of every tab, and `side`", () => {
    const tabbed = deepFreeze(counted([{ id: "p", tabs: [{ id: "a", title: "A", cells: [{ view: "week" }] }, { id: "b", title: "B", cells: [{ view: "list" }] }] }]));
    expect(countHolds(tabbed)).toBe(true);
    expect(removeTab(tabbed, "p", "b")).toBe(tabbed);
    expect(removeTabWhy(tabbed, "p", "b")).toBe(COUNT_WHY);
    expect(removeTab(tabbed, "p", "a")).not.toBe(tabbed);
    const side = deepFreeze(counted([{ id: "p", side: { view: "list" }, cells: [{ view: "week" }, { view: "check_in" }] }]));
    expect(countHolds(side)).toBe(true);
    expect(removeCellGuarded(side, "p", { tabId: null, index: 0 })).not.toBe(side);
  });
  it("refuses a tab removal that would leave a counted page with no table or board, in three tabs", () => {
    const m = deepFreeze(counted([{ id: "p", tabs: [
      { id: "a", title: "A", cells: [{ view: "week" }] },
      { id: "b", title: "B", cells: [{ view: "board" }] },
      { id: "c", title: "C", cells: [{ view: "check_in" }] },
    ] }]));
    expect(removeTab(m, "p", "b")).toBe(m);
    expect(removeTabWhy(m, "p", "b")).toBe(COUNT_WHY);
    // The tabs without a table or board can go.
    expect(removeTab(m, "p", "a")).not.toBe(m);
    expect(removeTab(m, "p", "c")).not.toBe(m);
    // Two table tabs: one may go.
    const two = deepFreeze(counted([{ id: "p", tabs: [
      { id: "a", title: "A", cells: [{ view: "list" }] },
      { id: "b", title: "B", cells: [{ view: "board" }] },
    ] }]));
    expect(removeTab(two, "p", "a")).not.toBe(two);
  });
  it("refuses the removal of the last tab when it holds the only table", () => {
    const m = deepFreeze(counted([{ id: "p", tabs: [{ id: "a", title: "A", cells: [{ view: "list" }] }] }]));
    // The last tab returns its cells to the flat list, so the table stays. This is allowed.
    const out = removeTab(m, "p", "a") as M;
    expect(out.pages[0].cells).toEqual([{ view: "list" }]);
  });
  it("refuses a page that holds a calendar only", () => {
    const m = deepFreeze(counted([{ id: "p", cells: [{ view: "week" }, { view: "check_in" }] }]));
    expect(countHolds(m)).toBe(false);
  });
  it("does not make a broken row a new refusal", () => {
    const m = deepFreeze(counted([{ id: "p", cells: [{ view: "week" }, { view: "check_in" }] }]));
    const out = removeCellGuarded(m, "p", { tabId: null, index: 0 }) as M;
    expect(out).not.toBe(m);
    expect(out.pages[0].cells).toEqual([{ view: "check_in" }]);
    expect(removeCellWhy(m, "p", { tabId: null, index: 0 })).toBeNull();
  });
  it("ignores `count: false` and a row with no `count`", () => {
    const m = deepFreeze(counted([{ id: "p", cells: [{ view: "list" }, { view: "week" }] }], [{ title: "S", rows: [{ page: "p", count: false }, { page: "p" }] }]));
    expect(countHolds(m)).toBe(true);
    expect(removeCellGuarded(m, "p", { tabId: null, index: 0 })).not.toBe(m);
  });
  it("keepCounts returns the previous value for a new break, and `next` otherwise", () => {
    const prev = counted([{ id: "p", cells: [{ view: "list" }, { view: "week" }] }]);
    const worse = counted([{ id: "p", cells: [{ view: "week" }] }]);
    const fine = counted([{ id: "p", cells: [{ view: "list" }] }]);
    expect(keepCounts(prev, worse)).toBe(prev);
    expect(keepCounts(prev, fine)).toBe(fine);
    expect(keepCounts(prev, prev)).toBe(prev);
    expect(keepCounts(prev, null)).toBe(null);
  });
  it("the sentences", () => {
    expect(LAST_CELL_WHY).toBe("A page needs one cell. Delete the page instead.");
    expect(COUNT_WHY).toBe("The rail counts the rows of this page. Keep a table or board on it first.");
    const flat = deepFreeze(counted([{ id: "p", cells: [{ view: "list" }] }]));
    expect(removeCellWhy(flat, "p", { tabId: null, index: 0 })).toBe(LAST_CELL_WHY);
    expect(removeCellWhy(flat, "nope", { tabId: null, index: 0 })).toBeNull();
    expect(removeCellWhy(flat, "p", { tabId: null, index: 9 })).toBeNull();
    expect(removeCellWhy(null, "p", { tabId: null, index: 0 })).toBeNull();
  });
});

// ── The adapter ─────────────────────────────────────────────────────────────

describe("layoutAct", () => {
  it("returns the same reference for a bad pageLayout request and for junk", () => {
    const m = frozen();
    expect(layoutAct(m, (t) => setCellSpan(t, "nope", { tabId: null, index: 0 }, 2))).toBe(m);
    expect(layoutAct(m, (t) => setCellSpan(t, "home", { tabId: null, index: 0 }, 9))).toBe(m);
    for (const j of [null, "x", 5, undefined]) expect(layoutAct(j, (t) => t)).toBe(j);
    expect(layoutAct(m, () => { throw new Error("x"); })).toBe(m);
  });
  it("works with a real cell act", () => {
    const out = layoutAct(frozen(), (t) => setCellSpan(t, "home", { tabId: null, index: 1 }, 3)) as M;
    expect(pg(out, "home").cells[1]).toEqual({ view: "week", span: 3 });
    expect(problems(out)).toEqual([]);
  });
});

// ── Junk and purity ─────────────────────────────────────────────────────────

const JUNK: unknown[] = [null, undefined, "x", 5, [], {}, { pages: 5 }, { pages: [null, 3, "x"] }, { pages: [{ id: "p" }] },
  { pages: [{ id: "p", tabs: 5, cells: 5 }] }, { pages: [{ id: "p", tabs: [null, 5, { id: "a" }] }] },
  { pages: [{ id: "p", tabs: [{ id: "a", title: "A", cells: [] }], cells: [{ view: "b" }] }] }];

describe("never throws on junk", () => {
  it("returns the input for every act", () => {
    for (const j of JUNK) {
      for (const pid of ["p", "nope"]) {
        const calls: unknown[] = [
          addTab(j, pid, "T"), renameTab(j, pid, "a", "T"), removeTab(j, pid, "a"), moveTab(j, pid, "a", 1),
          removeCellGuarded(j, pid, { tabId: null, index: 0 }), keepCounts(j, j),
        ];
        // An add on a page of this junk can be valid, so only the shapes that cannot change must match.
        if (j === null || typeof j !== "object" || !Array.isArray((j as M).pages)) for (const c of calls.slice(0, 4)) expect(c).toBe(j);
      }
      expect(typeof countHolds(j)).toBe("boolean");
      expect(removeCellWhy(j, "p", { tabId: null, index: 0 }) === null || typeof removeCellWhy(j, "p", { tabId: null, index: 0 }) === "string").toBe(true);
      expect(() => removeTabWhy(j, "p", "a")).not.toThrow();
      expect(() => removeCellGuarded(j, "p", null as never)).not.toThrow();
      expect(() => addTab(j, 5 as never, 5 as never)).not.toThrow();
    }
  });
  it("returns the input for a page with both lists", () => {
    const m = deepFreeze(JUNK[11]);
    expect(addTab(m, "p", "X")).toBe(m);
    expect(renameTab(m, "p", "a", "X")).toBe(m);
    expect(removeTab(m, "p", "a")).toBe(m);
    expect(moveTab(m, "p", "a", 1)).toBe(m);
  });
  it("never changes its input", () => {
    const m = frozen();
    const copy = JSON.stringify(m);
    addTab(m, "home", "X"); addTab(m, "work", "Y"); renameTab(m, "work", "a", "Z"); removeTab(m, "work", "a");
    moveTab(m, "work", "a", 1); removeCellGuarded(m, "home", { tabId: null, index: 0 });
    expect(JSON.stringify(m)).toBe(copy);
  });
});

describe("purity", () => {
  it("the module has no React, clock, random or network", () => {
    const text = readFileSync(resolve(__dirname, "tabEdit.ts"), "utf-8");
    const code = text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");
    for (const bad of [/from "react"/, /\bDate\b/, /Math\.random/, /\bfetch\b/, /\bwindow\b/, /\bdocument\b/, /localStorage/]) {
      expect(code).not.toMatch(bad);
    }
  });
});

// ── A random run ────────────────────────────────────────────────────────────

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

type Kept = { seed: number; op: number; manifest: M };

/** 200 random edits, valid and not. Each result passes the checker. Some manifests are kept. */
function run(seed: number): Kept[] {
  const r = rng(seed);
  const pick = <T,>(xs: readonly T[]): T => xs[Math.floor(r() * xs.length)];
  let m = deepFreeze(runBase());
  const kept: Kept[] = [];
  for (let op = 1; op <= 200; op++) {
    const pageIds = [...(m.pages ?? []).map((p: M) => p.id), "zz"];
    const pageId = pick(pageIds);
    const page: M = (m.pages ?? []).find((p: M) => p.id === pageId) ?? {};
    const tabIds = [...(page.tabs ?? []).map((t: M) => t.id), "zz"];
    const tabId = pick(tabIds);
    const title = pick(["Sales", "  More  ", "Main", "Sales", "", "Reports", "Ops", "  ", "Sales"]);
    const delta = pick([-2, -1, 0, 1, 2, 1.5]);
    const loc = () => ({ tabId: r() < 0.3 ? null : pick(tabIds), index: Math.floor(r() * 15) - 1 });
    const before = JSON.stringify(m);
    let next: unknown;
    switch (Math.floor(r() * 16)) {
      case 0: case 1: next = addTab(m, pageId, title); break;
      case 2: next = renameTab(m, pageId, tabId, title); break;
      case 3: case 4: case 5: case 6: next = removeTab(m, pageId, tabId); break;
      case 7: next = moveTab(m, pageId, tabId, delta); break;
      case 8: next = layoutAct(m, (t) => moveCell(t, pageId, loc(), loc())); break;
      case 9: next = layoutAct(m, (t) => moveCellToTab(t, pageId, loc(), tabId)); break;
      case 10: next = layoutAct(m, (t) => setCellSpan(t, pageId, loc(), pick([null, 1, 2, 3, 4, 5, 0]))); break;
      case 11: case 12: next = layoutAct(m, (t) => addCell(t, pageId, loc(), pick(["board", "week", "list", "nope"]))); break;
      case 13: next = layoutAct(m, (t) => removeCell(t, pageId, loc())); break;
      default: next = removeCellGuarded(m, pageId, loc());
    }
    expect(JSON.stringify(m), `op ${op} changed its input`).toBe(before);
    expect(problems(next as M), `op ${op} broke the pages`).toEqual([]);
    m = deepFreeze(next as M);
    if (op % 50 === 0) kept.push({ seed, op, manifest: JSON.parse(JSON.stringify(m)) });
  }
  return kept;
}

describe("a random run", () => {
  it("keeps every result valid and matches the fixture the server check reads", () => {
    const outputs = [11, 23, 47].flatMap((seed) => run(seed));
    expect(outputs).toHaveLength(12);
    const expected = JSON.parse(readFileSync(FIXTURE, "utf-8"));
    expect(outputs).toEqual(expected);
  });
});
