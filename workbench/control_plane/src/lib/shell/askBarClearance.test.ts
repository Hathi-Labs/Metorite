/**
 * Room under the last line (task 2242). The rules are pure: which scroller
 * under the Ask bar gets room, and how much. The layout itself is measured by
 * `e2e/askbar-clearance.spec.ts`.
 */
import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import { type ScrollerFacts, barFootprint, declaredSideways, planClearance } from "./askBarClearance";

/** A 1200 x 800 column at the origin: the bar's top is 800 - 20 - 46 = 734. */
const COLUMN = { left: 0, top: 0, right: 1200, bottom: 800 };
const BAR = barFootprint(COLUMN);

function pane(over: Partial<ScrollerFacts> & { id: number }): ScrollerFacts {
  return {
    rect: { left: 0, top: 100, right: 1200, bottom: 800 },
    overflowY: "auto",
    display: "block",
    flexDirection: "row",
    clientHeight: 700,
    overflows: false,
    horizontalOnly: false,
    paddingBottom: 0,
    clipBottom: 800,
    ancestors: [],
    ...over,
  };
}

describe("barFootprint", () => {
  it("is the full field, centred, 36rem wide, 20 px above the foot", () => {
    expect(BAR).toEqual({ left: 312, right: 888, top: 734, bottom: 780 });
  });
});

describe("planClearance", () => {
  it("gives a pane that ends under the bar the room it needs, plus the gap", () => {
    const plan = planClearance(BAR, [pane({ id: 1 })]);
    // bottom 800 - bar top 734 + 16
    expect(plan.get(1)).toBe(82);
  });

  it("gives a pane that does not overflow the same room (People's directory)", () => {
    const plan = planClearance(BAR, [pane({ id: 1, clientHeight: 300 })]);
    expect(plan.get(1)).toBe(82);
  });

  it("gives nothing when no bar is on the page", () => {
    expect(planClearance(null, [pane({ id: 1 })]).size).toBe(0);
  });

  it("subtracts the padding the pane already has", () => {
    expect(planClearance(BAR, [pane({ id: 1, paddingBottom: 60 })]).get(1)).toBe(22);
  });

  it("gives nothing where the padding already leaves the room (.pb-askbar is 88 px)", () => {
    expect(planClearance(BAR, [pane({ id: 1, paddingBottom: 88 })]).size).toBe(0);
  });

  it("counts only the deepest pane", () => {
    const outer = pane({ id: 1 });
    const inner = pane({ id: 2, ancestors: [1], rect: { left: 0, top: 200, right: 1200, bottom: 790 } });
    const plan = planClearance(BAR, [outer, inner]);
    expect(plan.has(1)).toBe(false);
    expect(plan.get(2)).toBe(72);
  });

  it("falls back to the outer pane when the inner one is clear of the bar", () => {
    const outer = pane({ id: 1 });
    const inner = pane({ id: 2, ancestors: [1], rect: { left: 0, top: 200, right: 1200, bottom: 600 } });
    const plan = planClearance(BAR, [outer, inner]);
    expect(plan.get(1)).toBe(82);
    expect(plan.has(2)).toBe(false);
  });

  it("skips a row flex strip but takes a column flex pane", () => {
    const row = pane({ id: 1, display: "flex", flexDirection: "row" });
    const col = pane({ id: 2, display: "flex", flexDirection: "column" });
    const plan = planClearance(BAR, [row, col]);
    expect(plan.has(1)).toBe(false);
    expect(plan.has(2)).toBe(true);
  });

  it("takes a grid and skips a pane that does not scroll", () => {
    const plan = planClearance(BAR, [pane({ id: 1, display: "grid" }), pane({ id: 2, overflowY: "hidden" })]);
    expect(plan.has(1)).toBe(true);
    expect(plan.has(2)).toBe(false);
  });

  it("requires the pane to overlap the bar sideways", () => {
    const left = pane({ id: 1, rect: { left: 0, top: 100, right: 300, bottom: 800 } });
    const right = pane({ id: 2, rect: { left: 900, top: 100, right: 1200, bottom: 800 } });
    expect(planClearance(BAR, [left, right]).size).toBe(0);
  });

  it("requires the pane to end below the bar's top", () => {
    const high = pane({ id: 1, rect: { left: 0, top: 100, right: 1200, bottom: 700 } });
    expect(planClearance(BAR, [high]).size).toBe(0);
  });

  it("skips a box that an ancestor clips: it grows, and the ancestor scrolls (Projects lists)", () => {
    const outer = pane({ id: 1 });
    const list = pane({ id: 2, ancestors: [1], rect: { left: 0, top: 200, right: 1200, bottom: 2400 } });
    const plan = planClearance(BAR, [outer, list]);
    expect(plan.get(1)).toBe(82);
    expect(plan.has(2)).toBe(false);
  });

  it("skips a short widget and a sideways scroller", () => {
    const short = pane({ id: 1, clientHeight: 60 });
    const strip = pane({ id: 2, horizontalOnly: true });
    expect(planClearance(BAR, [short, strip]).size).toBe(0);
  });

  it("does not let a sideways scroller hide the pane around it", () => {
    const outer = pane({ id: 1 });
    const strip = pane({ id: 2, ancestors: [1], horizontalOnly: true });
    expect(planClearance(BAR, [outer, strip]).get(1)).toBe(82);
  });

  it("gives the room to the page when a nested box ends above its foot (a list mid-page)", () => {
    const page = pane({ id: 1 });
    const list = pane({ id: 2, ancestors: [1], rect: { left: 0, top: 500, right: 1200, bottom: 760 }, clientHeight: 260 });
    const plan = planClearance(BAR, [page, list]);
    expect(plan.get(1)).toBe(82);
    expect(plan.has(2)).toBe(false);
  });

  it("lets a lane a few pixels above its holder's foot keep the room (a board's padding)", () => {
    const main = pane({ id: 1 });
    const lane = pane({ id: 2, ancestors: [1], rect: { left: 0, top: 100, right: 400, bottom: 780 }, clientHeight: 680 });
    const plan = planClearance(BAR, [main, lane]);
    expect(plan.get(2)).toBe(62);
    expect(plan.has(1)).toBe(false);
  });

  it("leaves no lane without room when a sibling at the foot takes the holder (CRM)", () => {
    const main = pane({ id: 1 });
    const plain = pane({ id: 2, ancestors: [1], rect: { left: 320, top: 100, right: 600, bottom: 788 }, clientHeight: 688 });
    const showMore = pane({ id: 3, ancestors: [1], rect: { left: 600, top: 100, right: 880, bottom: 756 }, clientHeight: 656 });
    const plan = planClearance(BAR, [main, plain, showMore]);
    expect(plan.has(1)).toBe(false);
    expect(plan.get(2)).toBe(70);
    expect(plan.get(3)).toBe(38);
  });

  it("gives the room to a page that scrolls, even when a capped box ends it (a transcript)", () => {
    const main = pane({ id: 1 });
    const page = pane({ id: 2, ancestors: [1], overflows: true });
    const box = pane({ id: 3, ancestors: [1, 2], rect: { left: 0, top: 500, right: 1200, bottom: 790 }, clientHeight: 290, overflows: true });
    const plan = planClearance(BAR, [main, page, box]);
    expect(plan.get(2)).toBe(82);
    expect(plan.has(1)).toBe(false);
    expect(plan.has(3)).toBe(false);
  });

  it("measures the foot inside the holder's own padding", () => {
    const holder = pane({ id: 1, paddingBottom: 30 });
    const inner = pane({ id: 2, ancestors: [1], rect: { left: 0, top: 100, right: 1200, bottom: 760 }, clientHeight: 660 });
    expect(planClearance(BAR, [holder, inner]).has(2)).toBe(true);
  });
});

describe("the line that closes a shortened page column", () => {
  it("is a hairline with no net height, so the column cannot grow a scroll", () => {
    const css = readFileSync(new URL("../../app/globals.css", import.meta.url), "utf-8");
    const rule = css.match(/main\[data-askbar-clear\]::after\s*\{([^}]*)\}/)?.[1] ?? "";
    expect(rule, "the rule moved or changed shape").toContain("height: 1px");
    expect(rule).toContain("margin-top: -1px");
    expect(rule).toContain("background: var(--border)");
  });
});

describe("declaredSideways", () => {
  it("names a box written to scroll sideways only", () => {
    expect(declaredSideways("overflow-x-auto rounded-lg border")).toBe(true);
    expect(declaredSideways("w-full sm:overflow-x-scroll")).toBe(true);
  });

  it("leaves a box that also scrolls down, and any other box", () => {
    expect(declaredSideways("overflow-x-auto overflow-y-auto")).toBe(false);
    expect(declaredSideways("overflow-auto")).toBe(false);
    expect(declaredSideways("flex-1 overflow-y-auto")).toBe(false);
    expect(declaredSideways("overflow-x-hidden overflow-y-auto")).toBe(false);
  });
});
