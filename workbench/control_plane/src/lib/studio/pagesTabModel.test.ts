/**
 * STU-AF-8b-2 fence: the reads and the act rule of the Pages tab, with no DOM.
 *
 * Mutation 1: make `applyFrame` send when the result is the same object. "sends nothing for a bad request" turns red.
 * Mutation 2: draw the default pages as declared in `pagesView`. "reads the built-in pages" turns red.
 * Mutation 3: drop the floor from `colsOptions`. "disables a column under the widest cell" turns red.
 */
import { describe, expect, it, vi } from "vitest";

import { addPage, addRow, addSection, materializePages, moveRow, movePage, removePage, removeSection, setPageCols } from "./frameEdit";
import { COLS_HINT, applyFrame, colsOptions, pagesView, railView } from "./pagesTabModel";

const base = {
  studio: 1, id: "shop", name: "Shop", purpose: "p",
  objects: [{ name: "order", label: "Orders", fields: [] }],
  views: [
    { id: "orders", block: "table", object: "order", title: "Orders" },
    { id: "board", block: "board", object: "order" },
  ],
};
const declared = {
  ...base,
  pages: [
    { id: "home", title: "Home", cells: [{ view: "orders", span: 2 }] },
    { id: "extra", cells: [{ view: "board" }] },
  ],
  nav: [{ title: "Work", rows: [{ page: "home" }, { page: "extra", label: "More" }] }, { title: "Other", rows: [] }],
};

/** Run one act the way the tab does, and return the manifest that it sent, or null. */
function run(m: unknown, fn: (m: unknown) => unknown): unknown {
  const send = vi.fn();
  applyFrame(m, send, fn);
  return send.mock.calls.length ? send.mock.calls[0][0] : null;
}

describe("pagesView and railView", () => {
  it("reads the built-in pages of a manifest with no pages", () => {
    const v = pagesView(base);
    expect(v.declared).toBe(false);
    expect(v.pages.map((p) => p.id)).toEqual(["order"]);
  });

  it("reads the declared pages, with the floor of the columns and the cells as text", () => {
    const v = pagesView(declared);
    expect(v.declared).toBe(true);
    expect(v.pages.map((p) => [p.id, p.floor])).toEqual([["home", 2], ["extra", 1]]);
    expect(v.pages[0].parts).toEqual(["orders (spans 2)"]);
  });

  it("keeps the index of each section and row, and reads the default rail when nav is absent", () => {
    const r = railView(declared);
    expect(r.declared).toBe(true);
    expect(r.sections.map((s) => s.rows.length)).toEqual([2, 0]);
    expect(r.sections[0].rows[1]).toMatchObject({ page: "extra", label: "More", pageName: "extra" });
    const d = railView({ ...declared, nav: undefined });
    expect(d.declared).toBe(false);
    expect(d.sections[0].rows.map((x) => x.page)).toEqual(["home", "extra"]);
  });

  it("disables a column under the widest cell", () => {
    const o = colsOptions(3);
    expect(o.map((x) => !!x.disabled)).toEqual([true, true, false, false]);
    expect(o[0].hint).toBe(COLS_HINT);
  });
});

describe("applyFrame", () => {
  it("sends nothing for a bad request", () => {
    expect(run(declared, (m) => removePage(m, "nope"))).toBeNull();
    expect(run(declared, (m) => addPage(m, "x", "no-view"))).toBeNull();
    expect(run(declared, (m) => setPageCols(m, "home", 1))).toBeNull();
    expect(run(base, (m) => addPage(m, "x", "orders"))).toBeNull();
  });

  it("add a page, then the manifest has it", () => {
    const next = run(declared, (m) => addPage(m, "Orders list", "orders")) as typeof declared;
    expect(next.pages.map((p) => p.id)).toEqual(["home", "extra", "orders-list"]);
  });

  it("delete a page drops its rail rows", () => {
    const next = run(declared, (m) => removePage(m, "extra")) as typeof declared;
    expect(next.pages.map((p) => p.id)).toEqual(["home"]);
    expect(next.nav[0].rows).toEqual([{ page: "home" }]);
  });

  it("move a page and a row", () => {
    expect((run(declared, (m) => movePage(m, "extra", -1)) as typeof declared).pages.map((p) => p.id)).toEqual(["extra", "home"]);
    expect((run(declared, (m) => moveRow(m, 0, 1, -1)) as typeof declared).nav[0].rows.map((r) => r.page)).toEqual(["extra", "home"]);
  });

  it("Edit the pages on a manifest with no pages writes pages", () => {
    const next = run(base, materializePages) as { pages?: { id: string }[] };
    expect(next.pages?.map((p) => p.id)).toEqual(["order"]);
    expect(run(next, materializePages)).toBeNull();
  });

  it("Add a section on a manifest with no nav writes the default rail first", () => {
    const next = run({ ...declared, nav: undefined }, (m) => addSection(m, "Work")) as typeof declared;
    expect(next.nav.map((s) => s.title)).toEqual(["Pages", "Work"]);
    expect(run({ ...declared, nav: undefined }, (m) => addSection(m, "Pages"))).toBeNull();
  });

  it("section delete and add a row each send one manifest", () => {
    expect((run(declared, (m) => removeSection(m, 1)) as typeof declared).nav).toHaveLength(1);
    expect((run(declared, (m) => addRow(m, 1, "home")) as typeof declared).nav[1].rows).toEqual([{ page: "home" }]);
  });
});
