/** STU-AF7 fence: removing a view also clears it from the sections of a page. */
import { describe, expect, it } from "vitest";

import { removeView, viewUses } from "./draftEdit";

const base = () => ({
  views: [{ id: "a", block: "table" }, { id: "b", block: "board" }],
  pages: [
    {
      id: "p",
      header: { text: "Hi." },
      sections: [
        { id: "one", title: "One", cells: [{ view: "a" }, { view: "b" }] },
        { id: "two", title: "Two", cells: [{ view: "a" }] },
      ],
    },
    { id: "q", cells: [{ view: "a" }] },
  ],
});

describe("a view in sections", () => {
  it("counts the section cells that show it", () => {
    expect(viewUses(base(), "a")).toBe(3);
  });

  it("removes the cell, drops a section that has no cell left, and keeps the header", () => {
    const out = removeView(base(), "a") as ReturnType<typeof base>;
    const p = out.pages[0];
    expect(p.sections.map((s) => s.id)).toEqual(["one"]);
    expect(p.sections[0].cells).toEqual([{ view: "b" }]);
    expect(p.header).toEqual({ text: "Hi." });
    expect(out.pages.map((x) => x.id)).toEqual(["p"]); // page q lost its last cell and goes
  });

  it("drops a page whose sections all go", () => {
    const m = { views: [{ id: "a", block: "table" }], pages: [{ id: "p", sections: [{ id: "s", title: "S", cells: [{ view: "a" }] }] }] };
    expect((removeView(m, "a") as typeof m).pages).toEqual([]);
  });
});
