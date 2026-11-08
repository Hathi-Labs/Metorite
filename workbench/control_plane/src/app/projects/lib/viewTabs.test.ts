/**
 * WS-64 OP-12 (D-OP-17): the fold rule of the view tab row. The runner has no
 * layout, so the arithmetic is tested here and the look is a capture.
 */
import { describe, expect, it } from "vitest";

import { VIEW_MODES } from "./commands";
import {
  ADD_WIDTH,
  ICON_ACTION,
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
    const out = layoutTabs({ tabs, active: ["board"], available: 1100, viewport: 1440 });
    expect(out.iconOnly).toBe(false);
    expect(out.folded).toEqual([]);
    expect(out.visible).toEqual(tabs.map((t) => t.id));
  });

  it("shows icons only below 1280 px of window", () => {
    const tabs = [...CANVASES, ...views(1)];
    expect(layoutTabs({ tabs, active: ["list"], available: 1100, viewport: ICON_ONLY_BELOW - 1 }).iconOnly).toBe(true);
    expect(layoutTabs({ tabs, active: ["list"], available: 1100, viewport: ICON_ONLY_BELOW }).iconOnly).toBe(false);
  });

  it("drops the labels before it folds a tab", () => {
    const wide = foldTabs({ tabs: CANVASES, active: ["list"], available: 10_000, iconOnly: false });
    expect(wide.folded).toEqual([]);
    // Too narrow for six labels, wide enough for six icons and "+ View".
    const narrow = layoutTabs({
      tabs: CANVASES,
      active: ["list"],
      available: 6 * 30 + 5 * 4 + 4 + ADD_WIDTH + 4,
      viewport: 1440,
    });
    expect(narrow.iconOnly).toBe(true);
    expect(narrow.folded).toEqual([]);
  });

  it("folds the extra tabs into More, in order, and loses none", () => {
    const tabs = [...CANVASES, ...views(8)];
    const out = layoutTabs({ tabs, active: ["board"], available: 700, viewport: 1024 });
    expect(out.iconOnly).toBe(true);
    expect(out.folded.length).toBeGreaterThan(0);
    expect(out.visible.slice(0, 6)).toEqual(CANVASES.map((c) => c.id));
    expect([...out.visible, ...out.folded].sort()).toEqual(tabs.map((t) => t.id).sort());
    expect(out.folded).toEqual(tabs.map((t) => t.id).filter((id) => out.folded.includes(id)));
  });

  it("keeps all six canvases while any saved view shows", () => {
    const tabs = [...CANVASES, ...views(8)];
    for (const available of [400, 450, 520, 600, 700, 900, 1100]) {
      const out = layoutTabs({ tabs, active: ["board"], available, viewport: 1024 });
      const canvasesKept = CANVASES.every((c) => out.visible.includes(c.id));
      const viewShown = out.visible.some((id) => id.startsWith("v"));
      if (viewShown) expect(canvasesKept, `width ${available}`).toBe(true);
    }
  });

  it("folds the saved views first, last first", () => {
    const tabs = [...CANVASES, ...views(8)];
    const out = layoutTabs({ tabs, active: ["board"], available: 560, viewport: 1024 });
    expect(out.visible.slice(0, 6)).toEqual(CANVASES.map((c) => c.id));
    expect(out.folded.length).toBeGreaterThan(0);
    expect(out.folded.every((id) => id.startsWith("v"))).toBe(true);
    // The first views stay, the last ones go.
    expect(out.folded).toEqual(tabs.map((t) => t.id).filter((id) => out.folded.includes(id)));
    expect(out.visible).toContain("v0");
    expect(out.folded).toContain("v7");
  });

  it("keeps the saved view on screen ahead of the others", () => {
    const tabs = [...CANVASES, ...views(8)];
    const out = layoutTabs({ tabs, active: ["board", "v7"], available: 560, viewport: 1024 });
    expect(out.visible).toContain("v7");
  });

  it("folds canvases only when even the six icons do not fit", () => {
    const tabs = [...CANVASES, ...views(2)];
    // Six icons, five gaps, the margin, "+ View" and "More": the least that holds all six.
    const least = 6 * 30 + 5 * 4 + 4 + ICON_ACTION + ICON_ACTION + 4;
    const fits = layoutTabs({ tabs, active: ["board"], available: least, viewport: 1024 });
    expect(CANVASES.every((c) => fits.visible.includes(c.id))).toBe(true);
    expect(fits.folded.every((id) => id.startsWith("v"))).toBe(true);
    const tight = layoutTabs({ tabs, active: ["table"], available: least - 40, viewport: 1024 });
    expect(tight.folded.some((id) => CANVASES.some((c) => c.id === id))).toBe(true);
    expect(tight.visible).toContain("table");
    expect(tight.folded).toContain("v0");
  });

  it("keeps the canvas on screen when nothing else fits", () => {
    const out = foldTabs({ tabs: [...CANVASES, ...views(3)], active: ["table"], available: 10, iconOnly: true });
    expect(out.visible).toEqual(["table"]);
  });

  it("selects no tab for the lens (null)", () => {
    const out = foldTabs({ tabs: CANVASES, active: [null], available: 10_000, iconOnly: false });
    expect(out.folded).toEqual([]);
  });

  it("is data-driven: one more canvas is one more entry", () => {
    const seven = [...CANVASES, { id: "backlog", label: "Backlog", kind: "canvas" as const }];
    const out = layoutTabs({ tabs: seven, active: ["board"], available: 10_000, viewport: 1440 });
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
