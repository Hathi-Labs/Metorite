/** STU-AF7 fence: the header and the sections of a page, read by the page model. */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { PAGE_CAPS, buildPage, readPage } from "./pageModel";

const cell = { view: "a-table" };
const views = [{ id: "a-table", block: "table" as const }, { id: "b-board", block: "board" as const }];

describe("header and sections", () => {
  it("a page with no header has header null and no sections", () => {
    const p = readPage({ id: "p", cells: [cell] });
    expect(p?.header).toBeNull();
    expect(p?.sections).toEqual([]);
    expect(p?.cells).toHaveLength(1);
  });

  it("reads the header and its count", () => {
    const p = readPage({ id: "p", cells: [cell], header: { text: "One sentence.", count: { object: "visit", label: "visits" } } });
    expect(p?.header).toEqual({ text: "One sentence.", count: { object: "visit", label: "visits" } });
    expect(readPage({ id: "p", cells: [cell], header: { text: "Hi." } })?.header).toEqual({ text: "Hi.", count: null });
  });

  it("drops a header that A2 refuses", () => {
    const bad = [{ text: "" }, { text: "   " }, { text: "a\u0000b" }, { text: "a\nb" }, { text: "x".repeat(PAGE_CAPS.headerText + 1) }, { count: { object: "v" } }, "text"];
    for (const header of bad) expect(readPage({ id: "p", cells: [cell], header })?.header).toBeNull();
    expect(readPage({ id: "p", cells: [cell], header: { text: "x".repeat(PAGE_CAPS.headerText) } })?.header).not.toBeNull();
  });

  it("reads sections in order, and the page holds one layout only", () => {
    const sections = [
      { id: "open", title: "Open", cells: [cell] },
      { id: "done", title: "Done", cells: [{ view: "b-board" }] },
    ];
    const p = readPage({ id: "p", sections }, new Set(["a-table", "b-board"]));
    expect(p?.sections.map((s) => s.id)).toEqual(["open", "done"]);
    expect(p?.cells).toEqual([]);
    expect(readPage({ id: "p", sections, cells: [cell] })).toBeNull();
    expect(readPage({ id: "p", sections, tabs: [{ id: "t", title: "T", cells: [cell] }] })).toBeNull();
  });

  it("drops a bad section, a repeated id, an empty section and the sections past the cap", () => {
    const ok = (i: number) => ({ id: `s${i}`, title: "S", cells: [cell] });
    const p = readPage({
      id: "p",
      sections: [ok(1), ok(1), { id: "bad id", title: "S", cells: [cell] }, { id: "e", title: "S", cells: [] }, { id: "t", title: "", cells: [cell] }, ok(2)],
    });
    expect(p?.sections.map((s) => s.id)).toEqual(["s1", "s2"]);
    const many = readPage({ id: "p", sections: Array.from({ length: 9 }, (_, i) => ok(i)) });
    expect(many?.sections).toHaveLength(PAGE_CAPS.sectionsPerPage);
  });

  it("drops a cell of an unknown view and keeps the section", () => {
    const p = readPage({ id: "p", sections: [{ id: "s", title: "S", cells: [cell, { view: "ghost" }] }] }, new Set(["a-table"]));
    expect(p?.sections[0].cells).toEqual([{ view: "a-table", span: 1 }]);
  });

  it("buildPage gives the cells of all sections in order", () => {
    const built = buildPage({ views, pages: [{ id: "p", sections: [
      { id: "x", title: "X", cells: [{ view: "b-board" }] }, { id: "y", title: "Y", cells: [cell] },
    ] }] }, "p");
    expect(built.cells.map((c) => c.view)).toEqual(["b-board", "a-table"]);
  });

  it("holds its caps equal to the schema file", () => {
    const file = resolve(__dirname, "../../../../../packages/acb_common/acb_common/studio_manifest.schema.json");
    const defs = JSON.parse(readFileSync(file, "utf-8")).$defs;
    expect(defs.PageDef.properties.sections.maxItems).toBe(PAGE_CAPS.sectionsPerPage);
    expect(defs.SectionDef.properties.cells.maxItems).toBe(PAGE_CAPS.cellsPerSection);
    expect(defs.SectionDef.properties.title.maxLength).toBe(PAGE_CAPS.sectionTitle);
    expect(defs.HeaderDef.properties.text.maxLength).toBe(PAGE_CAPS.headerText);
    expect(defs.HeaderCount.properties.label.anyOf[0].maxLength).toBe(PAGE_CAPS.countLabel);
  });
});

describe("length counts code points, as A2 does", () => {
  const emoji = "\u{1F600}";
  const page = (extra: object) => readPage({ id: "p", cells: [cell], ...extra });
  it("a title of 59 letters and an emoji is 60 code points and stays", () => {
    const at = (t: string) => readPage({ id: "p", sections: [{ id: "s", title: t, cells: [cell] }] })?.sections.length;
    expect(at("x".repeat(59) + emoji)).toBe(1);
    expect(at("x".repeat(60) + emoji)).toBe(0);
  });
  it("a header text at 160 and 161 code points", () => {
    expect(page({ header: { text: "x".repeat(159) + emoji } })?.header).not.toBeNull();
    expect(page({ header: { text: "x".repeat(160) + emoji } })?.header).toBeNull();
  });
  it("a count label at 40 and 41 code points", () => {
    const label = (l: string) => page({ header: { text: "Hi.", count: { object: "visit", label: l } } })?.header?.count?.label;
    expect(label("x".repeat(39) + emoji)).toBeTruthy();
    expect(label("x".repeat(40) + emoji)).toBeNull();
  });
});
