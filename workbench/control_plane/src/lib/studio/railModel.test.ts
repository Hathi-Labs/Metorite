/** STU-AF2b fence: the pure model of the app rail. */
import { describe, expect, it } from "vitest";

import { MAX_NAV_COUNTS, MAX_NAV_ROWS, MAX_NAV_SECTIONS } from "@/lib/studio/manifest";
import { DEFAULT_RAIL_ICON, hasRail, objectReadable, railFor, visibleRail } from "@/lib/studio/railModel";

const PAGES = [
  { id: "home", title: "Home" },
  { id: "jobs", title: "  " },
  { id: "people", title: "People" },
];

describe("railFor", () => {
  it("with no nav gives one untitled section, a row for each page", () => {
    for (const nav of [undefined, null, []]) {
      const out = railFor(PAGES, nav);
      expect(out).toHaveLength(1);
      expect(out[0].title).toBe("");
      expect(out[0].rows).toEqual([
        { page: "home", label: "Home", icon: DEFAULT_RAIL_ICON },
        { page: "jobs", label: "jobs", icon: DEFAULT_RAIL_ICON },
        { page: "people", label: "People", icon: DEFAULT_RAIL_ICON },
      ]);
    }
  });

  it("with a declared nav keeps the order, the label and the icon", () => {
    const out = railFor(PAGES, [
      { title: "Work", rows: [{ page: "people", label: "Team", icon: "Users" }, { page: "home" }] },
      { title: "More", rows: [{ page: "jobs" }] },
    ]);
    expect(out.map((s) => s.title)).toEqual(["Work", "More"]);
    expect(out[0].rows).toEqual([
      { page: "people", label: "Team", icon: "Users" },
      { page: "home", label: "Home", icon: DEFAULT_RAIL_ICON },
    ]);
  });

  it("drops a row for a page that was not built", () => {
    const out = railFor(PAGES, [{ title: "Work", rows: [{ page: "ghost" }, { page: "home" }] }]);
    expect(out[0].rows.map((r) => r.page)).toEqual(["home"]);
  });

  it("drops a section left empty", () => {
    const out = railFor(PAGES, [
      { title: "Gone", rows: [{ page: "ghost" }] },
      { title: "Kept", rows: [{ page: "home" }] },
    ]);
    expect(out.map((s) => s.title)).toEqual(["Kept"]);
  });

  it("cuts to the caps of the manifest", () => {
    const many = Array.from({ length: 20 }, (_, i) => ({ id: `p${i}`, title: `P${i}` }));
    const sections = Array.from({ length: 7 }, (_, s) => ({
      title: `S${s}`, rows: many.map((p) => ({ page: p.id })),
    }));
    const out = railFor(many, sections);
    expect(out).toHaveLength(MAX_NAV_SECTIONS);
    for (const s of out) expect(s.rows).toHaveLength(MAX_NAV_ROWS);
    expect(railFor(many)[0].rows).toHaveLength(MAX_NAV_ROWS);
  });

  it("gives no section for no page", () => {
    expect(railFor([], undefined)).toEqual([]);
  });
});

describe("hasRail", () => {
  it("is false for one page or none, true for two", () => {
    expect(hasRail([])).toBe(false);
    expect(hasRail([{ id: "a" }])).toBe(false);
    expect(hasRail([{ id: "a" }, { id: "b" }])).toBe(true);
  });
});

describe("the count of a row (STU-AF3b)", () => {
  const rows = (n: number) => Array.from({ length: n }, (_, i) => ({ id: "p" + i, title: "P" + i }));
  it("carries count on a row, and no key on a row without it", () => {
    const out = railFor(PAGES, [{ title: "W", rows: [{ page: "home", count: true }, { page: "jobs", count: false }, { page: "people" }] }]);
    expect(out[0].rows.map((r) => r.count)).toEqual([true, undefined, undefined]);
    expect("count" in out[0].rows[1]).toBe(false);
  });
  it("keeps the count on the first 8 rows of the rail only", () => {
    const pages = rows(12);
    const nav = [{ title: "A", rows: pages.slice(0, 6).map((p) => ({ page: p.id, count: true })) },
      { title: "B", rows: pages.slice(6).map((p) => ({ page: p.id, count: true })) }];
    const flat = railFor(pages, nav).flatMap((s) => s.rows);
    expect(flat.filter((r) => r.count)).toHaveLength(MAX_NAV_COUNTS);
    expect(flat.slice(0, 8).every((r) => r.count)).toBe(true);
  });
});

describe("visibleRail: who reads a page (C8, rule 5)", () => {
  const WHERE = [{ role: "hr", read: { where: "owner = $me" } }];
  const ALL = [{ role: "hr", read: "all", write: "all" }];
  const OBJECTS = new Map<string, readonly Record<string, unknown>[] | null>([
    ["notes", null], ["empty", []], ["pay", WHERE], ["shared", ALL],
  ]);
  const objectOf = (page: string) => ({ n: "notes", e: "empty", p: "pay", s: "shared", g: "ghost" } as Record<string, string>)[page] ?? null;
  const rail = railFor(
    [{ id: "n" }, { id: "e" }, { id: "p" }, { id: "s" }, { id: "g" }, { id: "d" }],
    [{ title: "Work", rows: [{ page: "n" }, { page: "e" }, { page: "s" }] },
      { title: "Private", rows: [{ page: "p" }, { page: "g" }] }, { title: "Misc", rows: [{ page: "d" }] }]);
  const pages = (out: ReturnType<typeof visibleRail>) => out.flatMap((s) => s.rows.map((r) => r.page));

  it("an owner reads every installed object", () => {
    expect(pages(visibleRail(rail, { owner: true, objects: OBJECTS, objectOf }))).toEqual(["n", "e", "s", "p", "d"]);
  });
  it("a viewer loses an object with access and no read all", () => {
    expect(pages(visibleRail(rail, { owner: false, objects: OBJECTS, objectOf }))).toEqual(["n", "e", "s", "d"]);
  });
  it("an object with no access, and one that grants read all, stay for a viewer", () => {
    expect(objectReadable(null, false)).toBe(true);
    expect(objectReadable(undefined, false)).toBe(true);
    expect(objectReadable([], false)).toBe(true);
    expect(objectReadable(ALL, false)).toBe(true);
    expect(objectReadable(WHERE, false)).toBe(false);
    expect(objectReadable(WHERE, true)).toBe(true);
  });
  it("an object that is not installed drops out, and a page with no object stays", () => {
    const out = visibleRail(rail, { owner: true, objects: OBJECTS, objectOf });
    expect(pages(out)).not.toContain("g");
    expect(pages(out)).toContain("d");
  });
  it("a section left empty is dropped", () => {
    const out = visibleRail(rail, { owner: false, objects: OBJECTS, objectOf });
    expect(out.map((s) => s.title)).toEqual(["Work", "Misc"]);
  });
  it("keeps the count flag of a row that stays", () => {
    const counted = railFor([{ id: "n" }], [{ title: "W", rows: [{ page: "n", count: true }] }]);
    expect(visibleRail(counted, { owner: false, objects: OBJECTS, objectOf })[0].rows[0].count).toBe(true);
  });
});
