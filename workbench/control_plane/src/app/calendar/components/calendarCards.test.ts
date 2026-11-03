/**
 * The Calendar's cards in the one look (owner, 2026-11-03: the tinted cards
 * "do not fit with the overall theme of Metorite").
 *
 * - A block on the grid has ONE carrier of colour, its left bar. No state
 *   tints the card or its border, and the Now and Overlaps marks are words,
 *   not filled pills.
 * - The Unscheduled rail draws the boards' task card (`TaskCardShell`).
 * - The rail's due days follow RX-5's local-day rule (`dueDays`). A rounded-up
 *   count of hours called a task three weeks late "due today".
 *
 * Source pins, because the rules are about which parts a file draws.
 * `openConflicts` has behaviour tests in `shared.test.ts`.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const read = (rel: string) =>
  readFileSync(fileURLToPath(new URL(rel, import.meta.url)), "utf-8");

const grid = read("./TimeGrid.tsx");
const rail = read("./UnscheduledRail.tsx");
const view = read("../CalendarView.tsx");

describe("a block on the grid", () => {
  it("takes its colour from the bar alone", () => {
    const tones = grid.slice(grid.indexOf("const CARD_TONE"), grid.indexOf("const RAISED"));
    expect(tones).toContain("bar:");
    expect(tones).not.toMatch(/border-|color-mix/);
  });

  it("has no state-coloured surface or border", () => {
    const surfaces = grid.slice(grid.indexOf("const RAISED"), grid.indexOf("// ── Day / Week hour grid"));
    expect(surfaces).not.toMatch(/(primary|warning|destructive|success)_\d+%/);
    expect(surfaces).not.toMatch(/border-(warning|destructive|success)/);
  });

  it("draws Now and Overlaps as words, not Badge pills", () => {
    expect(grid).not.toContain("<Badge");
    expect(grid).toContain("text-info");
  });

  it("marks only a clash of open blocks", () => {
    expect(grid).toMatch(/const conflict = lanes > 1 && clashes\.has\(b\.item\.id\)/);
  });
});

describe("the Unscheduled rail", () => {
  it("draws the boards' task card, in its dense box", () => {
    expect(rail).toContain("<TaskCardShell");
    expect(rail).toMatch(/<TaskCardShell[\s\S]*?\bdense\b/);
    expect(rail).toContain("<TaskCardTitle");
    expect(rail).toContain("taskMeta(");
  });
});

describe("the due days of the rail", () => {
  it("count local days with the shared rule", () => {
    const block = view.slice(view.indexOf("const dueSoon"), view.indexOf("const doneStats"));
    expect(block).toContain("dueDays(i.dueAt, nowMs)");
    expect(block).not.toContain("Math.ceil");
  });
});
