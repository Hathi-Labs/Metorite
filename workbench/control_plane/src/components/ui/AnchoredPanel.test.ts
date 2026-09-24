/**
 * `AnchoredPanel`'s placement (S6g repair round). The `align` prop is additive:
 * the default must still hang the panel from the anchor's LEFT edge, as every
 * caller before S6g expects.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { panelBox, panelStyle } from "./AnchoredPanel";

const rect = { left: 100, right: 180, top: 40, bottom: 70, width: 80 };
const viewport = { width: 1440, height: 900 };

describe("panelBox / panelStyle", () => {
  it("the default align gives left = box.left, and no right", () => {
    const box = panelBox(rect, viewport, 256);
    expect(box.left).toBe(100);
    const style = panelStyle(box, "start", 256);
    expect(style.left).toBe(box.left);
    expect(style).not.toHaveProperty("right");
    expect(style).toMatchObject({ top: 72, minWidth: 80, maxHeight: 256 });
  });

  it("align end hangs from the right edge, measured from the window", () => {
    const box = panelBox(rect, viewport, 256);
    const style = panelStyle(box, "end", 256);
    expect(style.right).toBe(1440 - 180);
    expect(style).not.toHaveProperty("left");
  });

  it("flips above when there is no room below, and never off the top", () => {
    const low = { ...rect, top: 800, bottom: 830 };
    expect(panelBox(low, viewport, 256).top).toBe(800 - 256 - 2);
    const tall = panelBox({ ...rect, top: 100, bottom: 130 }, { width: 1440, height: 200 }, 256);
    // No room below and more above: it flips, clamped to 8px from the top.
    expect(tall.top).toBe(8);
  });

  it("the component's own default is start", () => {
    const src = readFileSync(resolve(__dirname, "AnchoredPanel.tsx"), "utf-8");
    expect(src).toMatch(/align = "start",/);
  });
});
