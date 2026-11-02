import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import type { PageDef, ViewDef } from "./manifest";
import { PAGE_CAPS, buildPage, defaultPage, readPage, resolveTab } from "./pageModel";

const view = (id: string): ViewDef => ({ id, block: "table" });
const views = [view("a"), view("b"), view("c")];

describe("default page", () => {
  it("holds all views, one column, in order, with no tab and no side view", () => {
    const { page, tab, cells } = buildPage({ views });
    expect(page.cols).toBe(1);
    expect(page.tabs).toEqual([]);
    expect(page.side).toBeNull();
    expect(cells.map((c) => c.view)).toEqual(["a", "b", "c"]);
    expect(cells.every((c) => c.span === 1)).toBe(true);
    expect(tab).toBeNull();
  });

  it("is built for no manifest views, and when no page is readable", () => {
    expect(buildPage({}).cells).toEqual([]);
    expect(buildPage({ views, pages: [{ id: "BAD ID" } as PageDef] }).page).toEqual(defaultPage(views));
  });
});

describe("tabs", () => {
  const pages: PageDef[] = [
    {
      id: "main",
      tabs: [
        { id: "one", title: "One", cells: [{ view: "a" }] },
        { id: "two", title: "Two", cells: [{ view: "b" }, { view: "c", span: 2 }] },
      ],
    },
  ];

  it("resolves a declared tab", () => {
    const b = buildPage({ views, pages }, "main", "two");
    expect(b.tab).toBe("two");
    expect(b.cells.map((c) => c.view)).toEqual(["b", "c"]);
  });

  it("falls back to the first tab for an unknown tab, or none", () => {
    expect(buildPage({ views, pages }, "main", "nope").tab).toBe("one");
    expect(buildPage({ views, pages }).tab).toBe("one");
    expect(resolveTab({ tabs: [] }, "one")).toBeNull();
  });

  it("keeps tabs when a page has both tabs and cells", () => {
    const page = readPage({ id: "p", tabs: pages[0].tabs, cells: [{ view: "a" }] });
    expect(page?.cells).toEqual([]);
    expect(page?.tabs).toHaveLength(2);
  });
});

describe("a page that cannot be read well", () => {
  it("drops a bad cell and a cell of an unknown view", () => {
    const b = buildPage({
      views,
      pages: [{ id: "p", cells: [{ view: "a" }, { view: "zzz" }, null, { view: 7 }, "x"] } as unknown as PageDef],
    });
    expect(b.cells.map((c) => c.view)).toEqual(["a"]);
  });

  it("clamps a span to the columns", () => {
    const b = buildPage({
      views,
      pages: [{ id: "p", cols: 2, cells: [{ view: "a", span: 4 }, { view: "b", span: 0 }, { view: "c", span: 1.5 }] }],
    });
    expect(b.cells.map((c) => c.span)).toEqual([2, 1, 1]);
  });

  it("uses 2 columns when cols is missing or bad, and drops an unreadable side view", () => {
    expect(readPage({ id: "p" })?.cols).toBe(2);
    expect(readPage({ id: "p", cols: 9 })?.cols).toBe(2);
    expect(readPage({ id: "p", side: { view: "zzz" } }, new Set(["a"]))?.side).toBeNull();
    expect(readPage({ id: "p", side: { view: "a" } }, new Set(["a"]))?.side).toBe("a");
  });

  it("applies the caps, and drops a duplicate tab id", () => {
    const many = Array.from({ length: 30 }, (_, i) => ({ view: "a", span: i }));
    expect(readPage({ id: "p", cells: many })?.cells).toHaveLength(PAGE_CAPS.cellsPerList);
    const tabs = Array.from({ length: 20 }, (_, i) => ({ id: `t${i}`, title: "T", cells: [] }));
    expect(readPage({ id: "p", tabs })?.tabs).toHaveLength(PAGE_CAPS.tabsPerPage);
    expect(readPage({ id: "p", tabs: [tabs[0], tabs[0]] })?.tabs).toHaveLength(1);
    const pages = Array.from({ length: 20 }, (_, i) => ({ id: `p${i}`, cells: [{ view: "a" }] }));
    expect(buildPage({ views, pages }, "p12").page.id).toBe("p0");
  });

  it("never throws on junk", () => {
    for (const junk of [null, undefined, 1, "x", [], { id: 3 }]) {
      expect(readPage(junk)).toBeNull();
    }
    expect(() => buildPage({ views, pages: "no" as unknown as PageDef[] })).not.toThrow();
  });
});

describe("caps", () => {
  it("equal the maxItems of studio_manifest.schema.json", () => {
    const file = resolve(__dirname, "../../../../../packages/acb_common/acb_common/studio_manifest.schema.json");
    const schema = JSON.parse(readFileSync(file, "utf-8"));
    const defs = schema.$defs;
    expect(schema.properties.pages.maxItems).toBe(PAGE_CAPS.pages);
    expect(defs.PageDef.properties.tabs.maxItems).toBe(PAGE_CAPS.tabsPerPage);
    expect(defs.PageDef.properties.cells.maxItems).toBe(PAGE_CAPS.cellsPerList);
    expect(defs.TabDef.properties.cells.maxItems).toBe(PAGE_CAPS.cellsPerList);
    expect(defs.PageDef.properties.cols.maximum).toBe(PAGE_CAPS.maxCols);
    expect(defs.CellDef.properties.span.anyOf[0].maximum).toBe(PAGE_CAPS.maxCols);
  });
});
