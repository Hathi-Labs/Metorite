/**
 * STU-AF-8b-3b fence: the cells editor draws each state, and its acts run through the pure functions.
 * The runner has no DOM, so each state renders to a string. The acts run through the same functions
 * that the buttons call, and `applyFrame` decides whether anything is sent.
 *
 * Mutation 1: drop the cap disable on Add cell. "disables Add cell and says why for a full list" turns red.
 * Mutation 2: drop `disabled={locked` from a control. "disables every control when locked" turns red.
 * Mutation 3: drop the removeWhy disable on Remove. "disables Remove for a counted cell" turns red.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { addCell, moveCell, moveCellToTab, setCellSpan } from "@/lib/studio/pageLayout";
import { BROKEN_NOTE, CELL_CAP_NOTE, TAB_CAP_NOTE, TAB_NAME_HINT, USE_TABS_HINT, applyFrame, layoutView, viewChoices } from "@/lib/studio/pagesTabModel";
import { COUNT_WHY, LAST_CELL_WHY, addTab, layoutAct, removeCellGuarded, removeTab } from "@/lib/studio/tabEdit";
import CellsEditor from "./CellsEditor";

type M = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
const base = (pages: unknown[], nav?: unknown[]): M => ({
  studio: 1, id: "shop", name: "Shop", purpose: "p",
  objects: [{ name: "order", label: "Orders", fields: [] }],
  views: [
    { id: "orders", block: "table", object: "order", title: "Order list" },
    { id: "form", block: "form", object: "order" },
    { id: "board", block: "board", object: "order" },
  ],
  pages, ...(nav ? { nav } : {}),
});
const flat = base([{ id: "home", cols: 3, cells: [{ view: "orders", span: 2 }, { view: "form" }] }]);
const tabbed = base([{ id: "home", tabs: [
  { id: "a", title: "Alpha", cells: [{ view: "orders" }, { view: "form" }] },
  { id: "b", title: "Beta", cells: [{ view: "board" }] },
] }]);
const counted = base(
  [{ id: "home", cells: [{ view: "orders" }, { view: "form" }] }],
  [{ title: "W", rows: [{ page: "home", count: true }] }],
);

function draw(m: unknown, over: { locked?: boolean; open?: boolean } = {}): string {
  return renderToStaticMarkup(createElement(CellsEditor, {
    page: { ...layoutView(m, "home"), id: "home", title: "" }, views: viewChoices(m), manifest: m,
    act: () => undefined, onRemoveTab: () => undefined, onToggle: () => undefined,
    locked: over.locked ?? false, open: over.open ?? true,
  }));
}
/** The opening tag of the control that carries this aria-label. */
const tagOf = (html: string, label: string): string => {
  const hits = html.match(new RegExp(`<[^>]*aria-label="${label}"[^>]*>`, "g")) ?? [];
  expect(hits.length, label).toBeGreaterThan(0);
  return hits[0] ?? "";
};
const off = (html: string, label: string) => /\sdisabled=""/.test(tagOf(html, label));

function run(m: unknown, fn: (m: unknown) => unknown): unknown {
  const send = vi.fn();
  applyFrame(m, send, fn);
  return send.mock.calls.length ? send.mock.calls[0][0] : null;
}
/** Run a cell act the way the editor does: through the adapter. */
const cellAct = (fn: (t: never) => unknown) => (m: unknown) => layoutAct(m, fn as never);

describe("layoutView", () => {
  it("reads a flat page as one list", () => {
    const v = layoutView(flat, "home");
    expect(v).toMatchObject({ flat: true, cols: 3, tabCount: 0, tabFull: false, broken: false });
    expect(v.lists).toHaveLength(1);
    expect(v.lists[0].tabId).toBeNull();
    expect(v.lists[0].cells.map((c) => [c.viewLabel, c.span, c.canUp, c.canDown])).toEqual([
      ["Order list", 2, false, true], ["form", null, true, false],
    ]);
    expect(v.lists[0].room).toBe(10);
  });
  it("reads a tabbed page as one list per tab", () => {
    const v = layoutView(tabbed, "home");
    expect(v.flat).toBe(false);
    expect(v.lists.map((l) => [l.tabId, l.title, l.tabCanUp, l.tabCanDown])).toEqual([["a", "Alpha", false, true], ["b", "Beta", true, false]]);
  });
  it("marks a page with both lists, or neither, as broken", () => {
    const both = base([{ id: "home", cells: [{ view: "orders" }], tabs: [{ id: "a", title: "A", cells: [] }] }]);
    expect(layoutView(both, "home")).toMatchObject({ broken: true, lists: [] });
    expect(layoutView(base([{ id: "home" }]), "home").broken).toBe(true);
  });
  it("never throws, and junk gives an empty view", () => {
    const junk = [null, undefined, 5, "x", [], {}, { pages: "x" }, { pages: [null, 3] }, base([{ id: "home", cells: [null, 7, { view: 4 }] }])];
    for (const j of junk) expect(() => layoutView(j, "home")).not.toThrow();
    expect(layoutView(null, "home")).toMatchObject({ lists: [], broken: false });
    expect(layoutView(flat, "nope").lists).toEqual([]);
  });
  it("carries the refusal sentences", () => {
    const one = base([{ id: "home", cells: [{ view: "orders" }] }]);
    expect(layoutView(one, "home").lists[0].cells[0].removeWhy).toBe(LAST_CELL_WHY);
    expect(layoutView(flat, "home").lists[0].cells[0].removeWhy).toBeNull();
    expect(layoutView(counted, "home").lists[0].cells[0].removeWhy).toBe(COUNT_WHY);
  });
});

describe("CellsEditor markup", () => {
  it("draws a closed page as the Layout button alone", () => {
    const html = draw(flat, { open: false });
    expect(html).toContain("Layout");
    expect(html).toContain('aria-expanded="false"');
    expect(html).not.toContain("Add cell");
  });
  it("draws a flat page with its cells, the Use tabs row and the hint", () => {
    const html = draw(flat);
    expect(html).toContain('aria-expanded="true"');
    expect(html).toContain("Order list");
    expect(html).toContain("Span of cell 1 in Cells");
    expect(html).toContain("Use tabs");
    expect(html).toContain(USE_TABS_HINT);
    expect(html).not.toContain("Move to tab");
    expect(off(html, "Move cell 1 in Cells up")).toBe(true);
    expect(off(html, "Move cell 2 in Cells up")).toBe(false);
    expect(off(html, "Move cell 2 in Cells down")).toBe(true);
  });
  it("draws a tabbed page with a block per tab", () => {
    const html = draw(tabbed);
    for (const t of ["Move the Alpha tab down", "Remove the Beta tab", "Title of the a tab", "Title of the b tab"]) expect(html).toContain(t);
    expect(html).toContain("Move to tab");
    expect(html).toContain("Add a tab");
    expect(off(html, "Move the Alpha tab up")).toBe(true);
    expect(off(html, "Remove the Alpha tab")).toBe(false);
  });
  it("disables Add cell and says why for a full list", () => {
    const full = base([{ id: "home", cells: Array.from({ length: 12 }, () => ({ view: "orders" })) }]);
    const html = draw(full);
    expect(html).toContain(CELL_CAP_NOTE);
    expect(CELL_CAP_NOTE).toBe("A list holds at most 12 cells.");
    expect(/<button[^>]*\sdisabled=""[^>]*>(?:(?!<\/button>)[\s\S])*Add cell/.test(html)).toBe(true);
    const open = draw(flat);
    expect(open).not.toContain(CELL_CAP_NOTE);
    expect(/<button[^>]*\sdisabled=""[^>]*>(?:(?!<\/button>)[\s\S])*Add cell/.test(open)).toBe(false);
  });
  it("keeps Use tabs and Add a tab on for a blank title (AF-8b-3b fix)", () => {
    for (const [m, label] of [[flat, "Use tabs"], [tabbed, "Add a tab"]] as const) {
      const html = draw(m);
      expect(/<button[^>]*\sdisabled=""[^>]*>(?:(?!<\/button>)[\s\S])*?(?:Use tabs|Add a tab)/.test(html), label).toBe(false);
      expect(html).toContain(TAB_NAME_HINT);
    }
    expect(draw(flat, { locked: true })).toMatch(/<button[^>]*\sdisabled=""[^>]*>(?:(?!<\/button>)[\s\S])*?Use tabs/);
  });
  it("disables Add a tab at the tab cap", () => {
    const tabs = Array.from({ length: 8 }, (_, i) => ({ id: `t${i}`, title: `T${i}`, cells: [{ view: "orders" }] }));
    const html = draw(base([{ id: "home", tabs }]));
    expect(html).toContain(TAB_CAP_NOTE);
    expect(TAB_CAP_NOTE).toBe("A page holds at most 8 tabs.");
    expect(/<button[^>]*\sdisabled=""[^>]*>(?:(?!<\/button>)[\s\S])*Add a tab/.test(html)).toBe(true);
  });
  it("disables Remove for a counted cell and says why", () => {
    const html = draw(counted);
    expect(html).toContain(COUNT_WHY);
    expect(off(html, "Remove cell 1 from Cells")).toBe(true);
    expect(off(html, "Remove cell 2 from Cells")).toBe(false);
  });
  it("disables Remove for the last flat cell and says why", () => {
    const html = draw(base([{ id: "home", cells: [{ view: "orders" }] }]));
    expect(html).toContain(LAST_CELL_WHY);
    expect(off(html, "Remove cell 1 from Cells")).toBe(true);
  });
  it("disables every control when locked", () => {
    for (const m of [flat, tabbed]) {
      const html = draw(m, { locked: true });
      const controls = html.match(/<(?:button|input)\b[^>]*>/g) ?? [];
      expect(controls.length).toBeGreaterThan(5);
      for (const c of controls) expect(c, c).toMatch(/\sdisabled=""/);
    }
  });
  it("draws one sentence and no control for a broken page", () => {
    const html = draw(base([{ id: "home", cells: [{ view: "orders" }], tabs: [{ id: "a", title: "A", cells: [] }] }]));
    expect(html).toContain(BROKEN_NOTE);
    expect(html).not.toContain("Add cell");
    expect(html).not.toContain("<input");
  });
});

describe("the acts of the editor", () => {
  it("Use tabs on a flat page moves the cells into the first tab", () => {
    const next = run(flat, (m) => addTab(m, "home", "Overview")) as M;
    expect(next.pages[0].cells).toBeUndefined();
    expect(next.pages[0].tabs[0]).toMatchObject({ title: "Overview", cells: flat.pages[0].cells });
  });
  it("removing the last tab returns the cells", () => {
    const one = run(flat, (m) => addTab(m, "home", "Overview")) as M;
    const back = run(one, (m) => removeTab(m, "home", one.pages[0].tabs[0].id)) as M;
    expect(back.pages[0].tabs).toBeUndefined();
    expect(back.pages[0].cells).toEqual(flat.pages[0].cells);
  });
  it("moves a cell to another tab", () => {
    const next = run(tabbed, cellAct((t) => moveCellToTab(t, "home", { tabId: "a", index: 0 }, "b"))) as M;
    expect(next.pages[0].tabs[0].cells).toHaveLength(1);
    expect(next.pages[0].tabs[1].cells.map((c: M) => c.view)).toEqual(["board", "orders"]);
  });
  it("moves a cell down, and sets a span", () => {
    const down = run(flat, cellAct((t) => moveCell(t, "home", { tabId: null, index: 0 }, { tabId: null, index: 1 }))) as M;
    expect(down.pages[0].cells.map((c: M) => c.view)).toEqual(["form", "orders"]);
    const span = run(flat, cellAct((t) => setCellSpan(t, "home", { tabId: null, index: 1 }, 3))) as M;
    expect(span.pages[0].cells[1].span).toBe(3);
    expect(run(flat, cellAct((t) => setCellSpan(t, "home", { tabId: null, index: 1 }, 4)))).toBeNull();
  });
  it("adds a cell, and refuses at the cap with nothing sent", () => {
    const next = run(flat, cellAct((t) => addCell(t, "home", { tabId: null, index: 2 }, "orders"))) as M;
    expect(next.pages[0].cells).toHaveLength(3);
    const full = base([{ id: "home", cells: Array.from({ length: 12 }, () => ({ view: "orders" })) }]);
    const add = cellAct((t) => addCell(t, "home", { tabId: null, index: 12 }, "form"));
    expect(add(full)).toBe(full);
    expect(run(full, add)).toBeNull();
  });
  it("refuses a counted removal with nothing sent", () => {
    expect(run(counted, (x) => removeCellGuarded(x, "home", { tabId: null, index: 0 }))).toBeNull();
    expect(run(counted, (x) => removeCellGuarded(x, "home", { tabId: null, index: 1 }))).not.toBeNull();
  });
});

describe("the preset editor (STU-LY-8b)", () => {
  const presetPage = base([{ id: "home", layout: "list_detail", regions: { list: ["orders"] } }]);
  it("draws the picker, the note and a row for each region, not the broken note", () => {
    const html = draw(presetPage);
    expect(html).not.toContain(BROKEN_NOTE);
    expect(html).toContain('data-preset="list_detail"');
    expect(html).toContain("This page uses a preset layout.");
    expect(html).toContain('aria-label="Layout preset of the home page"');
    expect(html).toContain('data-region="list"');
    expect(html).toContain('data-region="detail"');
    expect(html).toContain("Add a detail view. The detail region needs one.");
  });
  it("offers the free grid and the presets on a grid page too", () => {
    expect(draw(flat)).toContain('aria-label="Layout preset of the home page"');
  });
  it("disables Remove for the only view of a required region, and all controls when locked", () => {
    const html = draw(presetPage);
    expect(tagOf(html, "Remove Order list from the list region")).toContain("disabled");
    const locked = draw(presetPage, { locked: true });
    expect(tagOf(locked, "Layout preset of the home page")).toContain("disabled");
  });
});

describe("CellsEditor drag layer (AF-11b-2)", () => {
  it("draws a handle for each cell row, named by the cell and its list", () => {
    const html = draw(tabbed);
    for (const label of ["Drag cell 1 in Alpha", "Drag cell 2 in Alpha", "Drag cell 1 in Beta"]) {
      const tag = tagOf(html, label);
      expect(tag).toContain('draggable="true"');
      expect(tag).not.toMatch(/\sdisabled=""/);
    }
    expect(html.match(/aria-label="Drag cell /g)).toHaveLength(3);
  });

  it("draws a strip with one draggable chip for each view of the draft", () => {
    const html = draw(flat);
    const from = html.indexOf("Drag a view onto a list, or use Add cell.");
    expect(from).toBeGreaterThan(-1);
    const strip = html.slice(from, html.indexOf('role="status"'));
    expect(strip.match(/draggable="true"/g)).toHaveLength(3);
    for (const label of ["Order list", "form", "board"]) expect(strip).toContain(label);
  });

  it("turns every handle and chip off when the editor is locked", () => {
    const html = draw(tabbed, { locked: true });
    const tags = html.match(/<button[^>]*draggable="true"[^>]*>/g) ?? [];
    expect(tags.length).toBeGreaterThanOrEqual(6);
    for (const tag of tags) expect(tag).toMatch(/\sdisabled=""/);
  });

  it("hides the handles, the strip and the line slot on a touch screen, where nothing drags", () => {
    const html = draw(tabbed);
    for (const label of ["Drag cell 1 in Alpha"]) expect(tagOf(html, label)).toContain("pointer-coarse:hidden");
    expect(html).toMatch(/<div class="flex flex-col gap-1 pointer-coarse:hidden"><p[^>]*>Drag a view onto a list/);
    expect(html).toMatch(/<div class="min-h-5 pointer-coarse:hidden"><p role="status"/);
  });

  it("keeps a status region at rest, and no alert and no empty-list zone", () => {
    const html = draw(tabbed);
    expect(html).toMatch(/<p role="status"[^>]*><\/p>/);
    expect(html).not.toContain('role="alert"');
    expect(html).not.toContain("Drop here to start this list.");
  });

  it("draws no handle and no strip for a closed page", () => {
    const html = draw(flat, { open: false });
    expect(html).not.toContain("Drag cell");
    expect(html).not.toContain("Drag a view");
  });
});
