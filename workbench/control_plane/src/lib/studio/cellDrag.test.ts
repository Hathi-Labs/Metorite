/**
 * STU-AF11b-2 fence: the drag state of the cells editor. Each step is pure, never throws, keeps its
 * input, and hands back the SAME state when nothing changed, so the editor does not re-render for it.
 *
 * Mutation 1: highlight a place that `canDrop` refuses. "a refused row takes the drop and has no highlight" turns red.
 * Mutation 2: keep the held item after a drop. "a drop lets go of the item" turns red.
 * Mutation 3: say nothing for a refusal. "a refusal keeps its reason as an alert" turns red.
 * Mutation 4: drop the `s.drag` guard of `drop`. "a drop with nothing held changes nothing" turns red.
 * Mutation 5: give `listEnd` the first slot for a full list. "a drop on the empty part of a list lands after the last row" turns red.
 */
import { describe, expect, it } from "vitest";

import { IDLE, begin, drop, end, hover, isOver, leave, listEnd, type DragState } from "./cellDrag";
import type { CellDef, PageDef, StudioManifest } from "./manifest";
import type { Loc } from "./pageLayout";
import { PAGE_CAPS } from "./pageModel";

const VIEWS = ["board", "week", "form", "list"];
const cell = (view: string): CellDef => ({ view });
const loc = (tabId: string | null, index: number): Loc => ({ tabId, index });
const flat = (n: number): PageDef => ({ id: "home", cols: 3, cells: Array.from({ length: n }, (_, i) => cell(VIEWS[i % 4])) }) as PageDef;

function manifest(pages: PageDef[]): StudioManifest {
  return { studio: 1, id: "app", views: VIEWS.map((id) => ({ id })), pages } as unknown as StudioManifest;
}
const tabbed = (): StudioManifest => manifest([{
  id: "home", cols: 2,
  tabs: [
    { id: "a", title: "Alpha", cells: [cell("board"), cell("week")] },
    { id: "b", title: "Beta", cells: [] },
  ],
} as unknown as PageDef]);

const held = (item: Parameters<typeof begin>[0]): DragState => begin(item);
type Shape = { pages: { cells?: { view: string }[]; tabs?: { cells: { view: string }[] }[] }[] };
const cellOf = (m: unknown, page = 0): string[] => ((m as Shape).pages[page]?.cells ?? []).map((c) => c.view);
const tabCells = (m: unknown, tab: number): string[] => ((m as Shape).pages[0]?.tabs?.[tab]?.cells ?? []).map((c) => c.view);

describe("begin, end and leave", () => {
  it("begin holds the item and clears the last line", () => {
    const s = begin({ kind: "view", viewId: "board" });
    expect(s.drag).toEqual({ kind: "view", viewId: "board" });
    expect(s.over).toBeNull();
    expect(s.line).toBeNull();
    const after = begin({ kind: "view", viewId: "week" });
    expect(after.line).toBeNull();
  });

  it("leave clears the target and keeps the item", () => {
    const m = manifest([flat(3)]);
    const h = hover(held({ kind: "cell", from: loc(null, 0) }), m, "home", loc(null, 2), 1, 10);
    expect(h.state.over).not.toBeNull();
    const l = leave(h.state);
    expect(l.over).toBeNull();
    expect(l.position).toBeNull();
    expect(l.drag).not.toBeNull();
  });

  it("leave and end return the same state when there is nothing to clear", () => {
    expect(leave(IDLE)).toBe(IDLE);
    expect(end(IDLE)).toBe(IDLE);
  });

  it("end lets go of the item and keeps the line", () => {
    const m = manifest([flat(3)]);
    const d = drop(held({ kind: "cell", from: loc(null, 0) }), m, "home", loc(null, 2), 9, 10);
    expect(d.state.line?.kind).toBe("status");
    const e = end({ ...d.state, drag: { kind: "view", viewId: "x" } });
    expect(e.drag).toBeNull();
    expect(e.line).toEqual(d.state.line);
  });
});

describe("hover", () => {
  it("allows a row of the same list and marks the half of the pointer", () => {
    const m = manifest([flat(3)]);
    const up = hover(held({ kind: "cell", from: loc(null, 0) }), m, "home", loc(null, 2), 2, 10);
    expect(up.allow).toBe(true);
    expect(up.state.over).toEqual(loc(null, 2));
    expect(up.state.position).toBe("before");
    const down = hover(up.state, m, "home", loc(null, 2), 8, 10);
    expect(down.state.position).toBe("after");
    expect(isOver(down.state, loc(null, 2))).toBe(true);
    expect(isOver(down.state, loc(null, 1))).toBe(false);
  });

  it("gives the same state when the pointer stays on the same half of the same row", () => {
    const m = manifest([flat(3)]);
    const first = hover(held({ kind: "cell", from: loc(null, 0) }), m, "home", loc(null, 2), 2, 10);
    const again = hover(first.state, m, "home", loc(null, 2), 3, 10);
    expect(again.state).toBe(first.state);
  });

  it("holds nothing: no drop, no highlight, the same state", () => {
    const m = manifest([flat(3)]);
    const h = hover(IDLE, m, "home", loc(null, 1), 5, 10);
    expect(h.allow).toBe(false);
    expect(h.state).toBe(IDLE);
  });

  it("a refused row takes the drop and has no highlight", () => {
    const m = manifest([flat(PAGE_CAPS.cellsPerList)]);
    const ok = hover(begin({ kind: "cell", from: loc(null, 0) }), m, "home", loc(null, 1), 1, 10);
    expect(ok.allow).toBe(true);
    expect(ok.state.over).not.toBeNull();
    const view = begin({ kind: "view", viewId: "board" });
    const full = hover({ ...view, over: loc(null, 1), position: "before" }, m, "home", loc(null, 1), 1, 10);
    expect(full.allow).toBe(true);
    expect(full.state.over).toBeNull();
    expect(full.state.position).toBeNull();
    expect(full.state.drag).not.toBeNull();
  });

  it("a cell does not join a flat page to a tab list, and the drop says why", () => {
    const m = tabbed();
    const s = held({ kind: "cell", from: loc("a", 0) });
    const h = hover(s, m, "home", loc(null, 0), 1, 10);
    expect(h.allow).toBe(true);
    expect(h.state.over).toBeNull();
    const d = drop(s, m, "home", loc(null, 0), 1, 10);
    expect(d.next).toBe(m);
    expect(d.state.line?.kind).toBe("alert");
  });

  it("an empty tab takes its first slot", () => {
    const m = tabbed();
    const h = hover(held({ kind: "cell", from: loc("a", 0) }), m, "home", loc("b", 0), 1, 10);
    expect(h.allow).toBe(true);
  });
});

describe("drop", () => {
  it("moves a cell inside one list, says so, and lets go of the item", () => {
    const m = manifest([flat(3)]);
    const before = JSON.stringify(m);
    const d = drop(held({ kind: "cell", from: loc(null, 0) }), m, "home", loc(null, 2), 9, 10);
    expect(cellOf(d.next)).toEqual(["week", "form", "board"]);
    expect(d.state.drag).toBeNull();
    expect(d.state.line?.kind).toBe("status");
    expect(d.state.line?.text).toContain("Moved board");
    expect(JSON.stringify(m)).toBe(before);
  });

  it("moves a cell to another tab", () => {
    const m = tabbed();
    const d = drop(held({ kind: "cell", from: loc("a", 1) }), m, "home", loc("b", 0), 1, 10);
    expect(tabCells(d.next, 0)).toEqual(["board"]);
    expect(tabCells(d.next, 1)).toEqual(["week"]);
    expect(d.state.line?.text).toContain("Beta");
  });

  it("adds a view dragged from the strip", () => {
    const m = tabbed();
    const d = drop(held({ kind: "view", viewId: "list" }), m, "home", loc("b", 0), 1, 10);
    expect(tabCells(d.next, 1)).toEqual(["list"]);
    expect(d.state.line?.kind).toBe("status");
  });

  it("a refusal keeps its reason as an alert, and the manifest itself", () => {
    const m = manifest([flat(PAGE_CAPS.cellsPerList)]);
    const d = drop(held({ kind: "view", viewId: "board" }), m, "home", loc(null, 0), 1, 10);
    expect(d.next).toBe(m);
    expect(d.state.line?.kind).toBe("alert");
    expect(d.state.line?.text.length).toBeGreaterThan(0);
    expect(d.state.drag).toBeNull();
  });

  it("a no-op drop says nothing and returns the same manifest", () => {
    const m = manifest([flat(3)]);
    const d = drop(held({ kind: "cell", from: loc(null, 1) }), m, "home", loc(null, 1), 1, 10);
    expect(d.next).toBe(m);
    expect(d.state.line).toBeNull();
    expect(d.state.drag).toBeNull();
  });

  it("a drop with nothing held changes nothing", () => {
    const m = manifest([flat(3)]);
    const d = drop(IDLE, m, "home", loc(null, 1), 1, 10);
    expect(d.next).toBe(m);
    expect(d.state).toBe(IDLE);
  });
});

describe("listEnd", () => {
  it("a drop on the empty part of a list lands after the last row", () => {
    const m = manifest([flat(3)]);
    const z = listEnd(3, null);
    expect(z.loc).toEqual(loc(null, 2));
    const d = drop(held({ kind: "cell", from: loc(null, 0) }), m, "home", z.loc, z.offsetY, z.height);
    expect(cellOf(d.next)).toEqual(["week", "form", "board"]);
  });

  it("an empty list takes the first slot", () => {
    const m = tabbed();
    const z = listEnd(0, "b");
    expect(z.loc).toEqual(loc("b", 0));
    const d = drop(held({ kind: "cell", from: loc("a", 0) }), m, "home", z.loc, z.offsetY, z.height);
    expect(tabCells(d.next, 1)).toEqual(["board"]);
  });
});

describe("never throws", () => {
  it("takes junk for the manifest, the page and the place", () => {
    const junk: unknown[] = [null, undefined, 3, "x", [], {}, { pages: 5 }, { pages: [null] }];
    for (const m of junk) {
      const s = held({ kind: "cell", from: loc(null, 0) });
      expect(() => hover(s, m, "home", loc(null, 0), 1, 10)).not.toThrow();
      expect(() => drop(s, m, "home", loc(null, 0), 1, 10)).not.toThrow();
      expect(drop(s, m, "home", loc(null, 0), 1, 10).next).toBe(m);
    }
    expect(() => hover(IDLE, tabbed(), 7 as unknown as string, loc(null, -1), Number.NaN, 0)).not.toThrow();
    expect(() => drop(held({ kind: "view", viewId: 5 as unknown as string }), tabbed(), "home", loc("a", 0), 1, 10)).not.toThrow();
  });
});
