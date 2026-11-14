/**
 * STU-AF8b-1 fence: the frame edits keep `pages` and `nav` valid, never change their input, never
 * throw, return the input itself for a bad request, and keep each key they do not know.
 *
 * Mutation 1: drop the nav prune in `dropViews` (`draftEdit.ts`). "drops the rail rows of a page that loses its last cell" in `draftEdit.test.ts` turns red.
 * Mutation 2: let `removePage` keep the rows of the page. "removePage drops every row that names it" and the loop turn red.
 * Mutation 3: allow the 9th page in `addPage`. "refuses the 9th page" and the loop turn red.
 * Mutation 4: make a function change its input. "never changes its input" turns red.
 * Mutation 5: let `setRowLabel` drop `count`. "keeps a key it does not know on a row" turns red.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import {
  addPage, addRow, addSection, colsFloor, materializePages, movePage, moveRow, moveSection, removePage, removeRow,
  removeSection, renamePage, renameSection, setPageCols, setRowIcon, setRowLabel, DEFAULT_SECTION_TITLE,
} from "./frameEdit";
import { MAX_NAV_ROWS, MAX_NAV_SECTIONS, NAV_ICONS } from "./manifest";
import { PAGE_CAPS } from "./pageModel";

const FIXTURE = resolve(__dirname, "__fixtures__/frameEdit_outputs.json");

type M = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any

function deepFreeze<T>(x: T): T {
  if (x && typeof x === "object" && !Object.isFrozen(x)) {
    Object.freeze(x);
    for (const v of Object.values(x as object)) deepFreeze(v);
  }
  return x;
}

const views = ["board", "week", "check_in"].map((id) => ({ id }));
const withPages = (): M => ({
  studio: 1,
  id: "frame-app",
  views: [...views],
  pages: [
    { id: "home", title: "Home", cols: 3, cells: [{ view: "board", span: 2 }, { view: "week" }] },
    { id: "work", cols: 2, tabs: [{ id: "a", title: "A", cells: [{ view: "check_in", span: 2 }] }] },
    { id: "solo", title: "Solo", cols: 1, cells: [{ view: "check_in" }] },
  ],
  nav: [
    { title: "Main", rows: [{ page: "home", label: "Start", count: true }, { page: "work", icon: "Table", count: true }, { page: "solo" }] },
    { title: "More", rows: [{ page: "solo", count: true }] },
  ],
});
const frozen = () => deepFreeze(withPages());
const noPages = (): M => ({ studio: 1, id: "frame-app", views: [...views] });

/** The rules of `_check_pages` and `_check_nav`, and the field rules of the schema, in TS. */
function problems(m: M): string[] {
  const out: string[] = [];
  const dashed = (s: unknown) => typeof s === "string" && /^[a-z][a-z0-9_-]{0,47}$/.test(s);
  const nonBlank = (s: unknown) => typeof s === "string" && s.length >= 1 && /\S/.test(s);
  const ids = new Set((m.views ?? []).map((v: M) => v.id));
  const pages: M[] = m.pages ?? [];
  if (pages.length > PAGE_CAPS.pages) out.push("too many pages");
  if (new Set(pages.map((p) => p.id)).size !== pages.length) out.push("duplicate page id");
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
    if (!dashed(p.id)) out.push(`${p.id}: bad id`);
    if (p.title !== undefined && typeof p.title !== "string") out.push(`${p.id}: bad title`);
    const cols = p.cols ?? 2;
    if (!Number.isInteger(cols) || cols < 1 || cols > PAGE_CAPS.maxCols) out.push(`${p.id}: cols out of range`);
    const tabs: M[] = p.tabs ?? [];
    const cells: M[] = p.cells ?? [];
    if (tabs.length && cells.length) out.push(`${p.id}: tabs and cells`);
    if (!tabs.length && !cells.length) out.push(`${p.id}: no layout`);
    if (tabs.length > PAGE_CAPS.tabsPerPage) out.push(`${p.id}: too many tabs`);
    if (new Set(tabs.map((t) => t.id)).size !== tabs.length) out.push(`${p.id}: duplicate tab id`);
    for (const t of tabs) cellsOk(t.cells, `${p.id}/${t.id}`, cols);
    cellsOk(cells, p.id, cols);
    if (p.side && !ids.has(p.side.view)) out.push(`${p.id}: undeclared side view`);
  }
  // _check_nav. A nav needs pages, so a row names a declared page.
  const nav: M[] = m.nav ?? [];
  const declared = new Set(pages.map((p) => p.id));
  if (nav.length > MAX_NAV_SECTIONS) out.push("too many sections");
  if (new Set(nav.map((s) => s.title)).size !== nav.length) out.push("duplicate section title");
  for (const s of nav) {
    if (!nonBlank(s.title)) out.push("blank section title");
    if (s.rows.length > MAX_NAV_ROWS) out.push(`${s.title}: too many rows`);
    for (const r of s.rows) {
      if (!dashed(r.page)) out.push(`${s.title}: bad row page`);
      if (!declared.has(r.page)) out.push(`${s.title}: row names undeclared page ${r.page}`);
      if (r.icon !== undefined && !(NAV_ICONS as readonly string[]).includes(r.icon)) out.push(`${s.title}: bad icon`);
      if (r.label !== undefined && !nonBlank(r.label)) out.push(`${s.title}: blank label`);
    }
  }
  return out;
}

const pg = (m: unknown, id: string) => (m as M).pages.find((p: M) => p.id === id);
const rows = (m: unknown, si: number) => (m as M).nav[si].rows;
const pagesOf = (m: unknown) => (m as M).pages.map((p: M) => p.id);

describe("caps", () => {
  it("equal the maxItems of the schema file", () => {
    const file = resolve(__dirname, "../../../../../packages/acb_common/acb_common/studio_manifest.schema.json");
    const schema = JSON.parse(readFileSync(file, "utf-8"));
    expect(schema.properties.pages.maxItems).toBe(PAGE_CAPS.pages);
    expect(schema.properties.nav.maxItems).toBe(MAX_NAV_SECTIONS);
  });
  it("the checker passes the start manifests", () => {
    expect(problems(withPages())).toEqual([]);
    expect(problems({ ...withPages(), pages: undefined, nav: undefined })).toEqual([]);
  });
});

describe("page acts", () => {
  it("materializes the default pages, and leaves other manifests alone", () => {
    const m = deepFreeze(noPages());
    const out = materializePages(m) as M;
    expect(out.pages).toEqual([{ id: "default", cols: 1, cells: [{ view: "board" }, { view: "week" }, { view: "check_in" }] }]);
    expect(problems(out)).toEqual([]);
    const have = frozen();
    expect(materializePages(have)).toBe(have);
    const none = deepFreeze({ studio: 1, id: "x" });
    expect(materializePages(none)).toBe(none);
    const empty = deepFreeze({ ...noPages(), pages: [] });
    expect(pagesOf(materializePages(empty))).toEqual(["default"]);
  });
  it("makes one page for each object, and a dashboard", () => {
    const m = deepFreeze({
      ...noPages(),
      objects: [{ name: "visit", label: "Visits" }],
      views: [{ id: "list", block: "table", object: "visit" }, { id: "total", block: "stat" }],
    });
    const out = materializePages(m) as M;
    expect(out.pages.map((p: M) => [p.id, p.title])).toEqual([["visit", "Visits"], ["dashboard", "Dashboard"]]);
    expect(problems({ ...out, views: m.views })).toEqual([]);
  });
  it("refuses when the default page holds more cells than the cap", () => {
    const many = deepFreeze({ studio: 1, id: "x", views: Array.from({ length: PAGE_CAPS.cellsPerList + 1 }, (_, i) => ({ id: `v${i}` })) });
    expect(materializePages(many)).toBe(many);
  });
  it("adds a page with one cell and a unique id", () => {
    const m = frozen();
    const a = addPage(m, "  Sales  board ", "board") as M;
    expect(a.pages.at(-1)).toEqual({ id: "sales-board", title: "Sales board", cells: [{ view: "board" }] });
    const b = addPage(a, "Solo", "week") as M;
    expect(b.pages.at(-1).id).toBe("solo-2");
    const c = addPage(m, "", "week") as M;
    expect(c.pages.at(-1)).toEqual({ id: "week", cells: [{ view: "week" }] });
    expect(problems(b)).toEqual([]);
  });
  it("refuses the 9th page, an unknown view and a manifest with no pages", () => {
    let m: unknown = withPages();
    for (let i = 0; pagesOf(m).length < PAGE_CAPS.pages; i++) m = addPage(m, `Extra ${i}`, "board");
    expect(pagesOf(m)).toHaveLength(PAGE_CAPS.pages);
    deepFreeze(m);
    expect(addPage(m, "Ninth", "board")).toBe(m);
    const f = frozen();
    expect(addPage(f, "X", "nope")).toBe(f);
    const n = deepFreeze(noPages());
    expect(addPage(n, "X", "board")).toBe(n);
  });
  it("renames the title and never the id", () => {
    const m = frozen();
    const out = renamePage(m, "home", " The   home ") as M;
    expect(pg(out, "home")).toEqual({ ...pg(m, "home"), title: "The home" });
    expect(renamePage(m, "home", "Home")).toBe(m);
  });
  it("deletes the title key for a blank title", () => {
    const m = frozen();
    const out = renamePage(m, "home", "   ");
    expect("title" in pg(out, "home")).toBe(false);
    expect(problems(out as M)).toEqual([]);
  });
  it("removePage drops every row that names it, and keeps the views", () => {
    const m = frozen();
    const out = removePage(m, "solo") as M;
    expect(pagesOf(out)).toEqual(["home", "work"]);
    expect(out.nav[0].rows.map((r: M) => r.page)).toEqual(["home", "work"]);
    expect(out.nav[1]).toEqual({ title: "More", rows: [] });
    expect(out.views).toEqual(views);
    expect(problems(out)).toEqual([]);
  });
  it("removePage of the last page leaves no row", () => {
    let m: unknown = frozen();
    for (const id of ["home", "work", "solo"]) m = removePage(m, id);
    expect((m as M).pages).toEqual([]);
    expect((m as M).nav.every((s: M) => s.rows.length === 0)).toBe(true);
    expect(problems(m as M)).toEqual([]);
  });
  it("moves a page, and changes the order only", () => {
    const m = frozen();
    const out = movePage(m, "home", 1) as M;
    expect(pagesOf(out)).toEqual(["work", "home", "solo"]);
    expect(pg(out, "home")).toBe(pg(m, "home"));
    expect(out.nav).toBe(m.nav);
    expect(pagesOf(movePage(m, "home", 99))).toEqual(["work", "solo", "home"]);
    expect(movePage(m, "home", -1)).toBe(m);
    expect(movePage(m, "home", 0)).toBe(m);
    expect(movePage(m, "home", 1.5)).toBe(m);
  });
  it("colsFloor is the widest span, in cells and tabs", () => {
    const m = frozen();
    expect(colsFloor(pg(m, "home"))).toBe(2);
    expect(colsFloor(pg(m, "work"))).toBe(2);
    expect(colsFloor(pg(m, "solo"))).toBe(1);
    expect(colsFloor(null)).toBe(1);
    expect(colsFloor({ cells: "x" })).toBe(1);
  });
  it("setPageCols refuses a value under a span and does not cut it", () => {
    const m = frozen();
    expect(setPageCols(m, "home", 1)).toBe(m);
    expect(setPageCols(m, "work", 1)).toBe(m);
    const out = setPageCols(m, "home", 2) as M;
    expect(pg(out, "home").cols).toBe(2);
    expect(pg(out, "home").cells[0].span).toBe(2);
    expect(pg(setPageCols(m, "solo", 4), "solo").cols).toBe(4);
    for (const bad of [0, 5, 2.5, NaN, "2" as never]) expect(setPageCols(m, "solo", bad)).toBe(m);
  });
});

describe("nav acts", () => {
  it("writes the default rail first, and adds the section", () => {
    const m = deepFreeze({ ...withPages(), nav: undefined });
    const out = addSection(m, "Reports") as M;
    expect(out.nav).toEqual([
      { title: DEFAULT_SECTION_TITLE, rows: [{ page: "home" }, { page: "work" }, { page: "solo" }] },
      { title: "Reports", rows: [] },
    ]);
    expect(problems(out)).toEqual([]);
  });
  it("writes the default pages with the default rail when the app has none", () => {
    const m = deepFreeze(noPages());
    const out = addSection(m, "Reports") as M;
    expect(pagesOf(out)).toEqual(["default"]);
    expect(out.nav[0]).toEqual({ title: DEFAULT_SECTION_TITLE, rows: [{ page: "default" }] });
    expect(problems(out)).toEqual([]);
  });
  it("adds an empty section when the app has no view and no page", () => {
    const m = deepFreeze({ studio: 1, id: "x" });
    expect((addSection(m, "A") as M).nav).toEqual([{ title: "A", rows: [] }]);
  });
  it("refuses a duplicate title, a blank title and the 5th section", () => {
    const m = frozen();
    expect(addSection(m, "main")).toBe(m);
    expect(addSection(m, "   ")).toBe(m);
    let n: unknown = m;
    for (let i = 0; (n as M).nav.length < MAX_NAV_SECTIONS; i++) n = addSection(n, `S${i}`);
    expect((n as M).nav).toHaveLength(MAX_NAV_SECTIONS);
    deepFreeze(n);
    expect(addSection(n, "Fifth")).toBe(n);
  });
  it("renames, removes and moves a section", () => {
    const m = frozen();
    expect((renameSection(m, 1, " Extra ") as M).nav[1]).toEqual({ ...m.nav[1], title: "Extra" });
    expect(renameSection(m, 1, "Main")).toBe(m);
    expect(renameSection(m, 1, "")).toBe(m);
    expect(renameSection(m, 5, "X")).toBe(m);
    expect((removeSection(m, 0) as M).nav.map((s: M) => s.title)).toEqual(["More"]);
    const last = removeSection(removeSection(m, 0), 0) as M;
    expect("nav" in last).toBe(false);
    const moved = moveSection(m, 0, 1) as M;
    expect(moved.nav.map((s: M) => s.title)).toEqual(["More", "Main"]);
    expect(moved.nav[1]).toBe(m.nav[0]);
    expect(moved.pages).toBe(m.pages);
    expect(moveSection(m, 0, -1)).toBe(m);
  });
  it("adds a row only for a declared page, and holds the row cap", () => {
    const m = frozen();
    expect(rows(addRow(m, 1, "home"), 1)).toEqual([...m.nav[1].rows, { page: "home" }]);
    expect(addRow(m, 1, "nope")).toBe(m);
    expect(addRow(m, 9, "home")).toBe(m);
    let n: unknown = withPages();
    while (rows(n, 1).length < MAX_NAV_ROWS) n = addRow(n, 1, "home");
    expect(problems(n as M)).toEqual([]);
    deepFreeze(n);
    expect(addRow(n, 1, "home")).toBe(n);
  });
  it("removes and moves a row, and changes the order only", () => {
    const m = frozen();
    expect(rows(removeRow(m, 0, 0), 0).map((r: M) => r.page)).toEqual(["work", "solo"]);
    expect(removeRow(m, 0, 7)).toBe(m);
    const out = moveRow(m, 0, 0, 2) as M;
    expect(out.nav[0].rows.map((r: M) => r.page)).toEqual(["work", "solo", "home"]);
    expect(out.nav[0].rows[2]).toBe(m.nav[0].rows[0]);
    expect(out.nav[1]).toBe(m.nav[1]);
    expect(moveRow(m, 0, 0, -1)).toBe(m);
  });
  it("sets and clears a label and an icon", () => {
    const m = frozen();
    expect(rows(setRowLabel(m, 0, 2, " Only  me "), 0)[2]).toEqual({ page: "solo", label: "Only me" });
    expect("label" in rows(setRowLabel(m, 0, 0, "  "), 0)[0]).toBe(false);
    expect("label" in rows(setRowLabel(m, 0, 0, null), 0)[0]).toBe(false);
    expect(rows(setRowIcon(m, 0, 2, "Star"), 0)[2]).toEqual({ page: "solo", icon: "Star" });
    expect("icon" in rows(setRowIcon(m, 0, 1, null), 0)[1]).toBe(false);
    expect(setRowIcon(m, 0, 2, "Nope")).toBe(m);
    expect(setRowIcon(m, 0, 2, null)).toBe(m);
    expect(setRowLabel(m, 0, 0, "Start")).toBe(m);
  });
  it("keeps a key it does not know on a row, in every row edit", () => {
    const m = frozen();
    const edits: unknown[] = [
      setRowLabel(m, 0, 0, "New"), setRowLabel(m, 0, 0, null), setRowIcon(m, 0, 0, "Star"), setRowIcon(m, 0, 1, null),
      moveRow(m, 0, 0, 1), removeRow(m, 0, 2), addRow(m, 0, "work"), renameSection(m, 0, "Other"), moveSection(m, 0, 1),
      addSection(m, "New"), removePage(m, "solo"), movePage(m, "home", 1), renamePage(m, "home", "H"), setPageCols(m, "solo", 3),
    ];
    for (const e of edits) {
      expect(e).not.toBe(m);
      // The row of "work" is never edited by an act that follows, so it keeps `count` in each result that has it.
      const work = ((e as M).nav as M[]).flatMap((x) => x.rows as M[]).filter((r) => r.page === "work" && r.count !== undefined);
      expect(work.every((r) => r.count === true)).toBe(true);
    }
    expect(rows(setRowLabel(m, 0, 0, null), 0)[0]).toEqual({ page: "home", count: true });
    expect(rows(setRowLabel(m, 0, 0, "New"), 0)[0]).toEqual({ page: "home", label: "New", count: true });
    expect(rows(setRowIcon(m, 0, 1, "Star"), 0)[1]).toEqual({ page: "work", icon: "Star", count: true });
    expect(rows(setRowIcon(m, 0, 1, null), 0)[1]).toEqual({ page: "work", count: true });
    expect(rows(moveRow(m, 0, 0, 1), 0)[1]).toEqual({ page: "home", label: "Start", count: true });
  });
  it("keeps an unknown key on a section and on a page", () => {
    const m = deepFreeze({ ...withPages(), nav: [{ title: "Main", flag: 1, rows: [{ page: "home" }] }] });
    expect((renameSection(m, 0, "Top") as M).nav[0].flag).toBe(1);
    expect((addRow(m, 0, "work") as M).nav[0].flag).toBe(1);
    const p = deepFreeze({ ...withPages(), pages: [{ id: "home", extra: 7, cells: [{ view: "board" }] }] });
    expect(pg(renamePage(p, "home", "H"), "home").extra).toBe(7);
    expect(pg(setPageCols(p, "home", 3), "home").extra).toBe(7);
  });
});

describe("bad requests", () => {
  const junk: unknown[] = [
    null, undefined, "text", 5, [], {}, { pages: 5 }, { pages: "x", nav: 5 }, { pages: [null, 5, "x"], nav: [null, { rows: 5 }] },
    { pages: [{ id: "a" }], nav: [{ title: "T", rows: [null, 3] }] }, { pages: [{ id: "a", cells: 4, tabs: "t" }], nav: "n" },
  ];
  const calls: Array<[string, (m: unknown) => unknown]> = [
    ["materializePages", (m) => materializePages(m)],
    ["addPage", (m) => addPage(m, "T", "board")],
    ["renamePage", (m) => renamePage(m, "a", "T")],
    ["removePage", (m) => removePage(m, "a")],
    ["movePage", (m) => movePage(m, "a", 1)],
    ["setPageCols", (m) => setPageCols(m, "a", 3)],
    ["addSection", (m) => addSection(m, "T")],
    ["renameSection", (m) => renameSection(m, 0, "T")],
    ["removeSection", (m) => removeSection(m, 0)],
    ["moveSection", (m) => moveSection(m, 0, 1)],
    ["addRow", (m) => addRow(m, 0, "a")],
    ["removeRow", (m) => removeRow(m, 0, 0)],
    ["moveRow", (m) => moveRow(m, 0, 0, 1)],
    ["setRowLabel", (m) => setRowLabel(m, 0, 0, "L")],
    ["setRowIcon", (m) => setRowIcon(m, 0, 0, "Star")],
  ];
  it("never throws on junk, and returns a non-object itself", () => {
    for (const [name, call] of calls) {
      for (const j of junk) {
        let out: unknown;
        expect(() => { out = call(j); }, name).not.toThrow();
        if (typeof j !== "object" || j === null || Array.isArray(j)) expect(out, name).toBe(j);
      }
    }
    expect(colsFloor(undefined)).toBe(1);
  });
  it("returns the input for a bad index, a bad id and a bad argument", () => {
    const m = frozen();
    const same: unknown[] = [
      removePage(m, "zz"), renamePage(m, "zz", "T"), movePage(m, "zz", 1), setPageCols(m, "zz", 2),
      renameSection(m, -1, "T"), renameSection(m, 1.5, "T"), removeSection(m, 2), moveSection(m, 9, 1),
      addRow(m, 2, "home"), removeRow(m, 0, -1), removeRow(m, 5, 0), moveRow(m, 0, 9, 1), setRowLabel(m, 0, 9, "x"), setRowIcon(m, 9, 0, "Star"),
      addPage(m, 5 as never, "board"), renamePage(m, "home", 5 as never), addSection(m, 5 as never), movePage(m, "home", "1" as never),
      setRowLabel(m, 0, 0, 5 as never), setRowIcon(m, 0, 0, 5 as never),
    ];
    for (const s of same) expect(s).toBe(m);
  });
  it("never changes its input", () => {
    const m = frozen();
    const copy = JSON.stringify(m);
    const bare = deepFreeze({ ...withPages(), nav: undefined });
    const none = deepFreeze(noPages());
    for (const [, call] of calls) { call(m); call(bare); call(none); }
    setPageCols(m, "solo", 4); addRow(m, 0, "home"); moveRow(m, 0, 0, 2); removePage(m, "home");
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

type Kept = { seed: number; op: number; manifest: M };

/** The start of a run: pages and a rail, no pages at all, or pages and no rail. */
function start(seed: number): M {
  const m = withPages();
  if (seed === 11) {
    // The server does not know `count` yet, so a run that the server reads has no row with it.
    m.nav = m.nav.map((s: M) => ({ ...s, rows: s.rows.map(({ count: _c, ...r }: M) => r) }));
    return m;
  }
  if (seed === 23) return noPages();
  delete m.nav;
  return m;
}

/** 200 random edits, valid and not. Each result passes the checker. Some manifests are kept. */
function run(seed: number): Kept[] {
  const r = rng(seed);
  const pick = <T,>(xs: readonly T[]): T => xs[Math.floor(r() * xs.length)];
  let m = deepFreeze(start(seed));
  const kept: Kept[] = [];
  for (let op = 1; op <= 200; op++) {
    const pageId = pick([...(m.pages ?? []).map((p: M) => p.id), "zz"]);
    const si = Math.floor(r() * 5) - 1;
    const ri = Math.floor(r() * 14) - 1;
    const delta = pick([-2, -1, 0, 1, 2, 1.5]);
    const title = pick(["Sales", "  More  ", "Main", "Pages", "", "Reports", "Ops", "Extra"]);
    const before = JSON.stringify(m);
    let next: unknown;
    switch (Math.floor(r() * 16)) {
      case 0: next = materializePages(m); break;
      case 1: case 2: next = addPage(m, title, pick(["board", "week", "check_in", "nope"])); break;
      case 3: next = renamePage(m, pageId, title); break;
      case 4: next = removePage(m, pageId); break;
      case 5: next = movePage(m, pageId, delta); break;
      case 6: next = setPageCols(m, pageId, pick([0, 1, 2, 3, 4, 5])); break;
      case 7: next = addSection(m, title); break;
      case 8: next = renameSection(m, si, title); break;
      case 9: next = r() < 0.5 ? removeSection(m, si) : moveSection(m, si, delta); break;
      case 10: case 11: next = addRow(m, si, pageId); break;
      case 12: next = r() < 0.5 ? removeRow(m, si, ri) : moveRow(m, si, ri, delta); break;
      case 13: next = setRowLabel(m, si, ri, pick(["Label", "  ", null, "Home page"])); break;
      case 14: next = setRowIcon(m, si, ri, pick(["Star", "Table", "Nope", null])); break;
      default: next = addPage(m, title, "week");
    }
    expect(JSON.stringify(m), `op ${op} changed its input`).toBe(before);
    expect(problems(next as M), `op ${op} broke the frame`).toEqual([]);
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
