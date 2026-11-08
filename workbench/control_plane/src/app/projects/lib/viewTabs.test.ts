/**
 * WS-64 OP-12 (D-OP-17): the fold rule of the view tab row. The runner has no
 * layout, so the arithmetic is tested here and the look is a capture.
 */
import { describe, expect, it } from "vitest";

import { VIEW_MODES } from "./commands";
import {
  ADD_WIDTH,
  ICON_ONLY_BELOW,
  type TabSpec,
  canvasTitle,
  foldTabs,
  layoutTabs,
  tabWidth,
  viewTabIcon,
} from "./viewTabs";

const label = (id: string) => id.charAt(0).toUpperCase() + id.slice(1);
const CANVASES: TabSpec[] = VIEW_MODES.map((m) => ({ id: m.id, label: label(m.id), kind: "canvas" }));
const views = (n: number): TabSpec[] =>
  Array.from({ length: n }, (_, i) => ({ id: `v${i}`, label: `Saved view ${i}`, kind: "view" as const }));

describe("view tab row", () => {
  it("draws every tab when the row is wide", () => {
    const tabs = [...CANVASES, ...views(2)];
    const out = layoutTabs({ tabs, activeId: "board", available: 1100, viewport: 1440 });
    expect(out.iconOnly).toBe(false);
    expect(out.folded).toEqual([]);
    expect(out.visible).toEqual(tabs.map((t) => t.id));
  });

  it("shows icons only below 1280 px of window", () => {
    const tabs = [...CANVASES, ...views(1)];
    expect(layoutTabs({ tabs, activeId: "list", available: 1100, viewport: ICON_ONLY_BELOW - 1 }).iconOnly).toBe(true);
    expect(layoutTabs({ tabs, activeId: "list", available: 1100, viewport: ICON_ONLY_BELOW }).iconOnly).toBe(false);
  });

  it("drops the labels before it folds a tab", () => {
    const wide = foldTabs({ tabs: CANVASES, activeId: "list", available: 10_000, iconOnly: false });
    expect(wide.folded).toEqual([]);
    // Too narrow for six labels, wide enough for six icons and "+ View".
    const narrow = layoutTabs({
      tabs: CANVASES,
      activeId: "list",
      available: 6 * 34 + 5 * 4 + 4 + ADD_WIDTH + 4,
      viewport: 1440,
    });
    expect(narrow.iconOnly).toBe(true);
    expect(narrow.folded).toEqual([]);
  });

  it("folds the extra tabs into More, in order, and loses none", () => {
    const tabs = [...CANVASES, ...views(8)];
    const out = layoutTabs({ tabs, activeId: "board", available: 700, viewport: 1024 });
    expect(out.iconOnly).toBe(true);
    expect(out.folded.length).toBeGreaterThan(0);
    expect(out.visible.slice(0, 6)).toEqual(CANVASES.map((c) => c.id));
    expect([...out.visible, ...out.folded].sort()).toEqual(tabs.map((t) => t.id).sort());
    expect(out.folded).toEqual(tabs.map((t) => t.id).filter((id) => out.folded.includes(id)));
  });

  it("never folds the tab on screen", () => {
    const tabs = [...CANVASES, ...views(8)];
    const out = layoutTabs({ tabs, activeId: "v7", available: 520, viewport: 768 });
    expect(out.visible).toContain("v7");
    expect(out.folded).not.toContain("v7");
    expect(out.folded.length).toBeGreaterThan(0);
  });

  it("keeps the active tab even when nothing else fits", () => {
    const out = foldTabs({ tabs: [...CANVASES, ...views(3)], activeId: "v2", available: 10, iconOnly: true });
    expect(out.visible).toEqual(["v2"]);
  });

  it("selects no tab for the lens (null)", () => {
    const out = foldTabs({ tabs: CANVASES, activeId: null, available: 10_000, iconOnly: false });
    expect(out.folded).toEqual([]);
  });

  it("is data-driven: one more canvas is one more entry", () => {
    const seven = [...CANVASES, { id: "backlog", label: "Backlog", kind: "canvas" as const }];
    const out = layoutTabs({ tabs: seven, activeId: "board", available: 10_000, viewport: 1440 });
    expect(out.visible).toContain("backlog");
  });

  it("sizes a tab by its label, and caps a saved view name", () => {
    const long: TabSpec = { id: "x", label: "A very long saved view name indeed", kind: "view" };
    expect(tabWidth(CANVASES[0], true)).toBeLessThan(tabWidth(CANVASES[0], false));
    expect(tabWidth(long, true)).toBe(tabWidth(long, false));
    expect(tabWidth(long, false)).toBeLessThanOrEqual(176);
  });

  it("names the key in a tooltip, and the canvas of a view in its icon", () => {
    expect(canvasTitle("Board", "b")).toBe("Board (b)");
    expect(canvasTitle("Board", undefined)).toBe("Board");
    expect(viewTabIcon("board")).toBe("Kanban");
    expect(viewTabIcon("list")).toBe("List");
    expect(VIEW_MODES.map((m) => m.key)).toEqual(["o", "b", "l", "t", "c", "m"]);
  });
});
