/** STU-AF2b fence: the pure model of the app rail. */
import { describe, expect, it } from "vitest";

import { MAX_NAV_ROWS, MAX_NAV_SECTIONS } from "@/lib/studio/manifest";
import { DEFAULT_RAIL_ICON, hasRail, railFor } from "@/lib/studio/railModel";

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
