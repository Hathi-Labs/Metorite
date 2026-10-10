/**
 * The rail row on a PHONE (review, 2026-10-10). `useViewMode` is mocked to
 * a phone, so the markup is the one a 390 px drawer draws.
 *
 * Two defects this file holds shut:
 *   1. A row that opens hid its icon on a phone, so a space lost its glyph
 *      and a parent project lost its run-state wheel. The phone now draws
 *      the chevron in its own column, beside the icon.
 *   2. The row's controls showed on the SELECTED row only, and choosing a
 *      row closes the drawer. Each row now draws its `phoneActions`.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/components/ViewModeProvider", () => ({
  useViewMode: () => ({ isMobile: true, isNarrow: true, mounted: true }),
}));

import { RailRow, type RailRowProps } from "./RailRow";

const icon = createElement("svg", { "aria-label": "Space — Fracktal Care" });
const draw = (props: Partial<RailRowProps> = {}) =>
  renderToStaticMarkup(
    createElement(RailRow, {
      label: "Fracktal Care",
      icon,
      tree: true,
      expand: { expanded: true, onToggle: () => {} },
      actions: createElement("button", { type: "button", "aria-label": "Hover action" }),
      phoneActions: createElement("button", { type: "button", "aria-label": "Actions for Fracktal Care" }),
      ...props,
    }),
  );

describe("a row that opens, on a phone", () => {
  it("draws the chevron in its own column, and keeps the icon visible", () => {
    const html = draw();
    expect(html).toMatch(/aria-label="Collapse Fracktal Care"/);
    // No swap: neither the chevron nor the icon is hidden by a hover rule.
    expect(html).not.toContain("rail-row-chevron");
    expect(html).not.toContain("rail-row-icon");
    expect(html).not.toContain("opacity-0");
    expect(html).toMatch(/<span data-rail-icon="" class="inline-flex shrink-0"><svg aria-label="Space — Fracktal Care"/);
    // The column comes before the label button, as the tree drew it before.
    expect(html.indexOf("data-rail-toggle")).toBeLessThan(html.indexOf("data-rail-label"));
  });

  it("keeps an empty chevron column on a leaf, so the icons line up", () => {
    const leaf = draw({ expand: undefined });
    expect(leaf).not.toContain("data-rail-toggle");
    expect(leaf).toMatch(/<span class="-ml-1 mr-1 flex w-6 shrink-0 items-center justify-center"><\/span>/);
  });

  it("is 40 px tall", () => {
    expect(draw()).toContain("min-h-10");
  });
});

describe("a row's controls, on a phone", () => {
  it("draws phoneActions on EVERY row, selected or not, and not the hover set", () => {
    for (const selected of [false, true]) {
      const html = draw({ selected });
      expect(html).toContain('aria-label="Actions for Fracktal Care"');
      expect(html).toContain("data-rail-phone-actions");
      expect(html).not.toContain('aria-label="Hover action"');
      expect(html).not.toContain("rail-row-actions");
      // Nothing swaps, so the count stays put.
      expect(html).not.toContain("data-has-actions");
    }
  });

  it("falls back to the selected row's own actions when a rail gives no phoneActions", () => {
    expect(draw({ phoneActions: undefined, selected: true })).toContain('data-pinned=""');
    expect(draw({ phoneActions: undefined, selected: false })).not.toContain("data-pinned");
  });
});
