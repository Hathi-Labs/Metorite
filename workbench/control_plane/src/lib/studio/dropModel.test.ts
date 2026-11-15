/**
 * STU-AF11b-1 fence: the drop model puts a cell where the member dropped it, refuses a full list,
 * never changes its input, never throws, and returns the input itself for a refusal or a no-op.
 *
 * Mutation 1: drop the `s > from.index ? s - 1` shift in `dropIndex`. "all 16 combinations" turns red.
 * Mutation 2: allow a drop into a full list. "refuses a drop into a full tab" turns red.
 * Mutation 3: make `applyDrop` change its input. "never changes its input" turns red.
 * Mutation 4: make `pageOf` throw for a missing page (and drop the catch). "never throws" turns red.
 * Mutation 5: drop the cross move refusal for a flat page. "refuses a cross move for a flat page" turns red.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import { applyDrop, canDrop, CELL_FULL_NOTE, dropIndex, dropSlot, dropVerdict, listName, positionOf, type DragItem, type Position } from "./dropModel";
import type { CellDef, PageDef, StudioManifest } from "./manifest";
import type { Loc } from "./pageLayout";
import { PAGE_CAPS } from "./pageModel";

const FIXTURE = resolve(__dirname, "__fixtures__/dropModel_outputs.json");

function deepFreeze<T>(x: T): T {
  if (x && typeof x === "object" && !Object.isFrozen(x)) {
    Object.freeze(x);
    for (const v of Object.values(x as object)) deepFreeze(v);
  }
  return x;
}

const VIEWS = ["board", "week", "check_in", "list"];
const cell = (view: string, span?: number): CellDef => (span === undefined ? { view } : { view, span });
const loc = (tabId: string | null, index: number): Loc => ({ tabId, index });
const flat = (id: string, n: number): PageDef =>
  ({ id, cols: 3, cells: Array.from({ length: n }, (_, i) => cell(VIEWS[i % 4])) }) as PageDef;

function base(): StudioManifest {
  return {
    studio: 1,
    id: "drop-app",
    views: VIEWS.map((id) => ({ id })),
    pages: [
      { id: "home", cols: 3, cells: [cell("board", 2), cell("week"), cell("check_in"), cell("list")] },
      {
        id: "work",
        cols: 2,
        tabs: [
          { id: "a", title: "Plan", cells: VIEWS.map((v) => cell(v)) },
          { id: "b", title: "Do", cells: [cell("week"), cell("list")] },
          { id: "c", title: "Done", cells: [] },
        ],
      },
    ],
  } as unknown as StudioManifest;
}

const pg = (m: unknown, id: string) => ((m as StudioManifest).pages as PageDef[]).find((p) => p.id === id) as PageDef;
const names = (cells: CellDef[] | undefined) => (cells ?? []).map((c) => c.view);
const tab = (m: unknown, id: string) => pg(m, "work").tabs!.find((t) => t.id === id)!;

/** The checks of `_check_pages` and the caps of the schema, in TS. */
function problems(m: StudioManifest): string[] {
  const out: string[] = [];
  const DASHED = /^[a-z][a-z0-9_-]{0,47}$/;
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
    if (!DASHED.test(p.id)) out.push(`${p.id}: bad page id`);
    const cols = p.cols ?? 2;
    if (cols < 1 || cols > PAGE_CAPS.maxCols) out.push(`${p.id}: cols out of range`);
    const tabs = p.tabs ?? [];
    const cells = p.cells ?? [];
    if (tabs.length && cells.length) out.push(`${p.id}: tabs and cells`);
    if (!tabs.length && !cells.length) out.push(`${p.id}: no layout`);
    if (tabs.length > PAGE_CAPS.tabsPerPage) out.push(`${p.id}: too many tabs`);
    if (new Set(tabs.map((t) => t.id)).size !== tabs.length) out.push(`${p.id}: duplicate tab id`);
    for (const t of tabs) {
      if (!DASHED.test(t.id)) out.push(`${p.id}/${t.id}: bad tab id`);
      if (typeof t.title !== "string" || !t.title.trim()) out.push(`${p.id}/${t.id}: blank title`);
      cellsOk(t.cells, `${p.id}/${t.id}`, cols);
    }
    cellsOk(cells, p.id, cols);
  }
  return out;
}

describe("dropIndex", () => {
  const same = (f: number, o: number, p: Position) => dropIndex(loc(null, f), loc(null, o), p);
  it("reads the table for one list", () => {
    expect(same(0, 1, "after")).toBe(1);
    expect(same(0, 0, "before")).toBe(0);
    expect(same(2, 0, "before")).toBe(0);
    expect(same(2, 1, "after")).toBe(2);
    expect(same(3, 3, "after")).toBe(3);
    expect(same(1, 3, "after")).toBe(3);
  });
  it("uses the slot for another tab", () => {
    expect(dropIndex(loc("a", 2), loc("b", 0), "before")).toBe(0);
    expect(dropIndex(loc("a", 2), loc("b", 1), "before")).toBe(1);
    expect(dropIndex(loc("a", 2), loc("b", 1), "after")).toBe(2);
    expect(dropIndex(loc("a", 0), loc("b", 0), "after")).toBe(1);
  });
  it("gives the first slot of an empty tab", () => {
    expect(dropIndex(loc("a", 0), loc("c", 0), "before")).toBe(0);
  });
  it("returns -1 for a bad number or a bad position", () => {
    for (const bad of [-1, Number.NaN, 1.5, Number.POSITIVE_INFINITY]) {
      expect(dropIndex(loc(null, bad), loc(null, 0), "before")).toBe(-1);
      expect(dropIndex(loc(null, 0), loc(null, bad), "before")).toBe(-1);
    }
    expect(dropIndex(loc(null, 0), loc(null, 1), "middle" as Position)).toBe(-1);
    expect(dropIndex(null as unknown as Loc, loc(null, 1), "before")).toBe(-1);
  });
  it("makes a slot from a row and a position", () => {
    expect(dropSlot(2, "before")).toBe(2);
    expect(dropSlot(2, "after")).toBe(3);
  });
});

describe("positionOf", () => {
  it("splits a row in two halves", () => {
    expect(positionOf(0, 40)).toBe("before");
    expect(positionOf(19.9, 40)).toBe("before");
    expect(positionOf(20, 40)).toBe("after");
    expect(positionOf(39, 40)).toBe("after");
  });
  it("gives after for a bad height or offset", () => {
    expect(positionOf(5, 0)).toBe("after");
    expect(positionOf(5, -3)).toBe("after");
    expect(positionOf(5, Number.NaN)).toBe("after");
    expect(positionOf(Number.NaN, 40)).toBe("after");
  });
});

/** The oracle, from the definition: take the item out, put it before or after the row it was dropped on. */
function oracle(items: string[], from: number, over: number, position: Position): string[] {
  if (from === over) return items.slice();
  const moved = items[from];
  const target = items[over];
  const rest = items.filter((_, i) => i !== from);
  const at = rest.indexOf(target) + (position === "after" ? 1 : 0);
  return [...rest.slice(0, at), moved, ...rest.slice(at)];
}

describe("applyDrop through the real moveCell", () => {
  for (const [label, tabId] of [["a flat list", null], ["a tab", "a"]] as const) {
    it(`matches the oracle for all 16 combinations in ${label}`, () => {
      const m = deepFreeze(base());
      const page = tabId === null ? "home" : "work";
      const read = (x: unknown) => names(tabId === null ? pg(x, "home").cells : tab(x, "a").cells);
      const start = read(m);
      expect(start).toHaveLength(4);
      let n = 0;
      for (let from = 0; from < 4; from++) {
        for (let over = 0; over < 4; over++) {
          for (const position of ["before", "after"] as const) {
            const next = applyDrop(m, page, { kind: "cell", from: loc(tabId, from) }, loc(tabId, over), position);
            expect(read(next), `${from} ${over} ${position}`).toEqual(oracle(start, from, over, position));
            if (from === over) expect(next).toBe(m);
            n++;
          }
        }
      }
      expect(n).toBe(16 * 2);
    });
  }

  it("changes nothing for the same place, with an empty sentence", () => {
    const m = deepFreeze(base());
    const v = dropVerdict(m, "home", { kind: "cell", from: loc(null, 1) }, loc(null, 0), "after");
    expect(v).toEqual({ next: m, refused: null, said: "" });
    expect(v.next).toBe(m);
  });

  it("drops across tabs at the start, the middle and the end", () => {
    const m = deepFreeze(base());
    const from = { kind: "cell", from: loc("a", 1) } as DragItem;
    expect(names(tab(applyDrop(m, "work", from, loc("b", 0), "before"), "b").cells)).toEqual(["week", "week", "list"]);
    expect(names(tab(applyDrop(m, "work", from, loc("b", 1), "before"), "b").cells)).toEqual(["week", "week", "list"]);
    expect(names(tab(applyDrop(m, "work", from, loc("b", 1), "after"), "b").cells)).toEqual(["week", "list", "week"]);
    const next = applyDrop(m, "work", { kind: "cell", from: loc("a", 0) }, loc("b", 0), "before");
    expect(names(tab(next, "b").cells)).toEqual(["board", "week", "list"]);
    expect(names(tab(next, "a").cells)).toEqual(["week", "check_in", "list"]);
    expect(problems(next as StudioManifest)).toEqual([]);
  });

  it("drops into an empty tab", () => {
    const m = deepFreeze(base());
    const next = applyDrop(m, "work", { kind: "cell", from: loc("a", 2) }, loc("c", 0), "before");
    expect(names(tab(next, "c").cells)).toEqual(["check_in"]);
    expect(tab(next, "a").cells).toHaveLength(3);
    // A tab may go empty, as the server allows.
    const back = applyDrop(next, "work", { kind: "cell", from: loc("c", 0) }, loc("a", 0), "before");
    expect(tab(back, "c").cells).toEqual([]);
  });

  it("refuses a drop into a full tab with the exact sentence", () => {
    const m = base();
    (m.pages as PageDef[])[1].tabs![1].cells = Array.from({ length: PAGE_CAPS.cellsPerList }, () => cell("board"));
    deepFreeze(m);
    const v = dropVerdict(m, "work", { kind: "cell", from: loc("a", 0) }, loc("b", 3), "after");
    expect(v.refused).toBe("A list holds at most 12 cells.");
    expect(CELL_FULL_NOTE).toBe("A list holds at most 12 cells.");
    expect(v.next).toBe(m);
    expect(v.said).toBe("");
    // A move inside the full list is fine.
    expect(dropVerdict(m, "work", { kind: "cell", from: loc("b", 0) }, loc("b", 3), "after").refused).toBeNull();
  });

  it("refuses a view dropped into a full list", () => {
    const m = base();
    (m.pages as PageDef[])[0].cells = Array.from({ length: PAGE_CAPS.cellsPerList }, () => cell("board"));
    deepFreeze(m);
    const v = dropVerdict(m, "home", { kind: "view", viewId: "week" }, loc(null, 2), "before");
    expect(v.refused).toBe(CELL_FULL_NOTE);
    expect(v.next).toBe(m);
  });

  it("drops a view at the start, the middle and the end", () => {
    const m = deepFreeze(base());
    const view = { kind: "view", viewId: "list" } as DragItem;
    expect(names(pg(applyDrop(m, "home", view, loc(null, 0), "before"), "home").cells)).toEqual(["list", "board", "week", "check_in", "list"]);
    expect(names(pg(applyDrop(m, "home", view, loc(null, 1), "after"), "home").cells)).toEqual(["board", "week", "list", "check_in", "list"]);
    expect(names(pg(applyDrop(m, "home", view, loc(null, 3), "after"), "home").cells)).toEqual(["board", "week", "check_in", "list", "list"]);
    expect(names(tab(applyDrop(m, "work", view, loc("c", 0), "before"), "c").cells)).toEqual(["list"]);
  });

  it("refuses an unknown view", () => {
    const m = deepFreeze(base());
    const v = dropVerdict(m, "home", { kind: "view", viewId: "nope" }, loc(null, 0), "before");
    expect(v.refused).toBe("That drop is not possible.");
    expect(v.next).toBe(m);
  });

  it("refuses a cross move for a flat page", () => {
    const m = deepFreeze(base());
    const v = dropVerdict(m, "home", { kind: "cell", from: loc(null, 0) }, loc("a", 0), "before");
    expect(v.refused).toBe("A cell moves between the tabs of one page.");
    expect(v.next).toBe(m);
    expect(dropVerdict(m, "home", { kind: "cell", from: loc(null, 0) }, loc(null, 1), "after").refused).toBeNull();
  });

  it("refuses a page with tabs and cells both", () => {
    const m = deepFreeze({ ...base(), pages: [{ id: "x", cols: 2, tabs: [{ id: "a", title: "A", cells: [cell("board")] }], cells: [cell("week")] }] });
    const v = dropVerdict(m, "x", { kind: "cell", from: loc("a", 0) }, loc("a", 0), "after");
    expect(v.refused).toBe("That drop is not possible.");
    expect(v.next).toBe(m);
  });

  it("refuses a move from a tab to a page list with the sentence of one page", () => {
    const m = deepFreeze(base());
    const v = dropVerdict(m, "work", { kind: "cell", from: loc("a", 0) }, loc(null, 0), "before");
    expect(v.refused).toBe("A cell moves between the tabs of one page.");
    expect(v.next).toBe(m);
  });
});

describe("said", () => {
  const m = deepFreeze(base());
  it("says a move in a list", () => {
    const v = dropVerdict(m, "home", { kind: "cell", from: loc(null, 0) }, loc(null, 3), "after");
    expect(v.said).toBe("Moved board to position 4 of 4 in the page.");
  });
  it("says a move into a tab by its title", () => {
    const v = dropVerdict(m, "work", { kind: "cell", from: loc("a", 1) }, loc("b", 1), "after");
    expect(v.said).toBe("Moved week to position 3 of 3 in Do.");
  });
  it("says an add", () => {
    const v = dropVerdict(m, "work", { kind: "view", viewId: "board" }, loc("b", 0), "before");
    expect(v.said).toBe("Added board at position 1 in Do.");
  });
  it("names a list", () => {
    expect(listName(m, "work", "c")).toBe("Done");
    expect(listName(m, "home", null)).toBe("the page");
    expect(listName(m, "nope", null)).toBe("the page");
  });
});

describe("canDrop", () => {
  const m = deepFreeze(base());
  it("allows a drop in a list, even a no-op", () => {
    expect(canDrop(m, "home", { kind: "cell", from: loc(null, 1) }, loc(null, 1))).toBe(true);
    expect(canDrop(m, "home", { kind: "cell", from: loc(null, 1) }, loc(null, 3))).toBe(true);
    expect(canDrop(m, "work", { kind: "view", viewId: "week" }, loc("c", 0))).toBe(true);
  });
  it("refuses an unknown place and an unknown view", () => {
    expect(canDrop(m, "home", { kind: "cell", from: loc(null, 1) }, loc(null, 9))).toBe(false);
    expect(canDrop(m, "nope", { kind: "cell", from: loc(null, 1) }, loc(null, 0))).toBe(false);
    expect(canDrop(m, "home", { kind: "view", viewId: "nope" }, loc(null, 0))).toBe(false);
  });
  it("refuses a full list", () => {
    const f = base();
    (f.pages as PageDef[])[0] = flat("home", 12);
    expect(canDrop(f, "home", { kind: "view", viewId: "week" }, loc(null, 0))).toBe(false);
  });
});

describe("safety", () => {
  it("is pure, in its source text", () => {
    const text = readFileSync(resolve(__dirname, "dropModel.ts"), "utf-8").replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");
    for (const bad of ["react", "Date", "Math.random", "fetch", "window", "document"]) {
      expect(text.includes(bad), bad).toBe(false);
    }
  });

  it("never changes its input", () => {
    const m = base();
    const before = JSON.stringify(m);
    deepFreeze(m);
    applyDrop(m, "home", { kind: "cell", from: loc(null, 0) }, loc(null, 3), "after");
    applyDrop(m, "work", { kind: "cell", from: loc("a", 0) }, loc("b", 0), "before");
    applyDrop(m, "work", { kind: "view", viewId: "list" }, loc("c", 0), "before");
    expect(JSON.stringify(m)).toBe(before);
  });

  it("never throws on junk and returns the same reference", () => {
    const drag = { kind: "cell", from: loc(null, 0) } as DragItem;
    const junk: unknown[] = [null, undefined, "text", 5, [], {}, { pages: 5 }, { pages: [null, 3, "x"] }, { pages: [{ id: "home", cells: 5 }] }];
    for (const j of junk) {
      expect(() => applyDrop(j, "home", drag, loc(null, 0), "before")).not.toThrow();
      expect(applyDrop(j, "home", drag, loc(null, 0), "before")).toBe(j);
    }
    const m = deepFreeze(base());
    const ids = ["__proto__", "constructor", "toString", "", "nope"];
    for (const id of ids) {
      expect(applyDrop(m, id, drag, loc(null, 0), "before")).toBe(m);
      expect(applyDrop(m, "home", drag, loc(id, 0), "before")).toBe(m);
      expect(applyDrop(m, "home", { kind: "view", viewId: id }, loc(null, 0), "before")).toBe(m);
    }
    for (const bad of [null, "x", { kind: "other" }, { kind: "cell" }, { kind: "cell", from: null }] as unknown[]) {
      expect(applyDrop(m, "home", bad as DragItem, loc(null, 0), "before")).toBe(m);
    }
    expect(applyDrop(m, "home", drag, null as unknown as Loc, "before")).toBe(m);
    expect(applyDrop(m, "home", drag, loc(null, 1.5), "before")).toBe(m);
    expect(applyDrop(m, "home", drag, loc(null, 0), "mid" as Position)).toBe(m);
    expect(applyDrop(m, 7 as unknown as string, drag, loc(null, 0), "before")).toBe(m);
  });
});

// ── A deterministic run ─────────────────────────────────────────────────────
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

function run(seed: number) {
  const r = rng(seed);
  const pick = <T,>(xs: T[]): T => xs[Math.floor(r() * xs.length)];
  let m: unknown = base();
  const kept: Array<{ seed: number; op: number; manifest: unknown }> = [];
  let applied = 0;
  let refused = 0;
  for (let op = 1; op <= 200; op++) {
    const pageId = pick(["home", "work"]);
    const page = pg(m, pageId);
    const lists: Array<string | null> = page.tabs ? page.tabs.map((t) => t.id) : [null];
    const lenOf = (id: string | null) => (id === null ? page.cells! : page.tabs!.find((t) => t.id === id)!.cells).length;
    const overList = pick(lists);
    const over = loc(overList, Math.floor(r() * Math.max(lenOf(overList), 1)));
    const position: Position = r() < 0.5 ? "before" : "after";
    let drag: DragItem;
    const fromList = pick(lists);
    if (r() < 0.3 || lenOf(fromList) === 0) drag = { kind: "view", viewId: pick(VIEWS) };
    else drag = { kind: "cell", from: loc(fromList, Math.floor(r() * lenOf(fromList))) };
    const next = applyDrop(m, pageId, drag, over, position);
    if (next === m) refused++;
    else applied++;
    m = next;
    expect(problems(m as StudioManifest), `seed ${seed} op ${op}`).toEqual([]);
    if (op % 50 === 0) kept.push({ seed, op, manifest: JSON.parse(JSON.stringify(m)) });
  }
  expect(applied).toBeGreaterThan(20);
  expect(refused).toBeGreaterThan(0);
  return kept;
}

describe("200 random drops", () => {
  it("keeps every page valid and matches the fixture", () => {
    const outputs = [11, 23, 47].flatMap((seed) => run(seed));
    expect(outputs).toHaveLength(12);
    const expected = JSON.parse(readFileSync(FIXTURE, "utf-8"));
    expect(outputs).toEqual(expected);
  });
});
