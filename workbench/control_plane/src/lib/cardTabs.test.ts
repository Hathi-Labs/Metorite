import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { restoredFirst, tabKeyTarget, tabsCount, tabsLabel } from "./cardTabs";

const read = (rel: string) => readFileSync(join(__dirname, "..", rel), "utf-8");

describe("the tabs of one approval card (owner, 2026-11-03, D173)", () => {
  it("the count leads the row", () => {
    expect(tabsCount(3)).toBe("3 to approve");
    expect(tabsLabel(2, "changes")).toBe("2 changes to approve. Use the arrow keys to see each one.");
  });

  it("the arrow keys, Home and End move, and the ends do not wrap", () => {
    expect(tabKeyTarget("ArrowRight", 0, 3)).toBe(1);
    expect(tabKeyTarget("ArrowRight", 2, 3)).toBe(2);
    expect(tabKeyTarget("ArrowLeft", 0, 3)).toBe(0);
    expect(tabKeyTarget("ArrowLeft", 2, 3)).toBe(1);
    expect(tabKeyTarget("Home", 2, 3)).toBe(0);
    expect(tabKeyTarget("End", 0, 3)).toBe(2);
    expect(tabKeyTarget("a", 0, 3)).toBeNull();
    expect(tabKeyTarget("End", 0, 0)).toBeNull();
  });

  it("a card that comes back first opens, and a card that was there does not", () => {
    expect(restoredFirst(["a", "b"], ["x", "a", "b"])).toBe("x");
    expect(restoredFirst(["a", "b"], ["a", "b", "c"])).toBeNull();
    expect(restoredFirst(["a", "b"], ["b"])).toBeNull();
    expect(restoredFirst([], ["a"])).toBeNull();
  });

  it("the queue draws ONE card with tabs, and no stack", () => {
    const q = read("components/ConfirmationQueue.tsx");
    expect(q).toContain("<CardTabs");
    expect(q).not.toContain("<CardStack");
    expect(q).not.toMatch(/swapStackCard|stackDepth|swipeStep|edgePose/);
    const tabs = read("components/ui/CardTabs.tsx");
    for (const word of ['role="tablist"', 'role="tab"', "aria-selected", "aria-controls", "tabIndex", "tabKeyTarget"]) {
      expect(tabs).toContain(word);
    }
    expect(tabs).not.toMatch(/onPointer|swapStackCard|data-card-stack-edge/);
  });

  it("the card carries the tab panel role and the tab row inside its frame", () => {
    const card = read("components/ConfirmationCard.tsx");
    expect(card).toContain("tabPanel");
    expect(card).toContain("{tabs}");
  });

  it("the stack's motion is gone from the CSS, and the spring still stops for reduced motion", () => {
    const css = read("app/globals.css");
    expect(css).not.toMatch(/card-stack-nudge|card-stack-lift|card-swap/);
    const reduce = css.slice(css.indexOf("@media (prefers-reduced-motion: reduce)"));
    expect(reduce).toContain("--spring-duration: 0.01ms");
  });
});
