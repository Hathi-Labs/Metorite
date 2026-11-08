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

  // A short window (fix round 1, 2026-10-10). At 1280x420 the More actions
  // menu ran to y611, because the panel kept its full max height.
  it("keeps the panel inside a short window, below and above", () => {
    const short = { width: 1280, height: 420 };
    const below = panelBox({ ...rect, top: 100, bottom: 130 }, short, 480);
    // 420 - 130 - 2 - 8 = 280 below, and 100 above: it opens below.
    expect(below.top).toBe(132);
    expect(panelStyle(below, "start", 480).maxHeight).toBe(280);
    expect(below.top + below.height).toBeLessThanOrEqual(420 - 8);
    const above = panelBox({ ...rect, top: 300, bottom: 330 }, short, 480);
    // 80 below and 300 above: it flips, and fits the 290px above.
    expect(panelStyle(above, "start", 480).maxHeight).toBe(290);
    expect(above.top).toBe(8);
  });

  // Metorite #412 (2026-10-25). The CANCELLED ⓘ at the bottom of the
  // Statuses dialog opened its 120px tip at the TOP of the dialog: a flipped
  // panel took its `top` from the most it MAY be (`InfoTip`'s 480), not its
  // real height. A flipped panel now hangs from its bottom edge, 2px above
  // the anchor, whatever its height turns out to be.
  it("a flipped panel hangs from its bottom edge, right on the anchor", () => {
    const icon = { ...rect, top: 790, bottom: 806 };
    for (const maxHeight of [120, 480]) {
      const box = panelBox(icon, viewport, maxHeight);
      expect(box.bottom).toBe(900 - 790 + 2);
      const style = panelStyle(box, "start", maxHeight);
      expect(style.bottom).toBe(900 - 790 + 2);
      expect(style).not.toHaveProperty("top");
      expect(style.maxHeight).toBe(maxHeight);
    }
  });

  it("a panel that opens below has no bottom", () => {
    const box = panelBox(rect, viewport, 256);
    expect(box.bottom).toBeNull();
    expect(panelStyle(box, "start", 256)).not.toHaveProperty("bottom");
  });

  // MOB-9 (task 2002): the phone's bottom bar paints above every panel. The
  // room below an anchor ends at the bar's top edge, not at the window's foot.
  describe("with the phone's bottom bar", () => {
    const phone = { width: 390, height: 844 };
    const barTop = 844 - 56;

    it("measures the room below against the bar's top edge", () => {
      const anchor = { ...rect, top: 600, bottom: 630 };
      const bare = panelBox(anchor, phone, 480);
      const clear = panelBox(anchor, { ...phone, floor: barTop }, 480);
      // Without the bar: 844 - 630 - 10 = 204 below, and 600 above. It flips.
      // With the bar: 788 - 630 - 10 = 148 below, and it flips the same way.
      expect(bare.bottom).not.toBeNull();
      expect(clear.bottom).not.toBeNull();
      const roomy = { ...rect, top: 100, bottom: 130 };
      const down = panelBox(roomy, { ...phone, floor: barTop }, 900);
      expect(down.bottom).toBeNull();
      expect(down.top + down.height).toBeLessThanOrEqual(barTop - 8);
      const noBar = panelBox(roomy, phone, 900);
      expect(noBar.height).toBe(844 - 130 - 10);
      expect(down.height).toBe(barTop - 130 - 10);
    });

    it("hangs a flipped panel from the window's real bottom", () => {
      const anchor = { ...rect, top: 700, bottom: 730 };
      const box = panelBox(anchor, { ...phone, floor: barTop }, 480);
      expect(box.bottom).toBe(844 - 700 + 2);
    });

    it("reads the bar only while the phone layout sets the attribute", () => {
      const src = readFileSync(resolve(__dirname, "AnchoredPanel.tsx"), "utf-8");
      expect(src).toMatch(/hasAttribute\("data-phone-nav"\)/);
      expect(src).toMatch(/querySelector\("\[data-phone-nav-bar\]"\)/);
      expect(src).toMatch(/floor: phoneBarTop\(\) \?\? undefined/);
    });
  });

  it("the component's own default is start", () => {
    const src = readFileSync(resolve(__dirname, "AnchoredPanel.tsx"), "utf-8");
    expect(src).toMatch(/align = "start",/);
  });
});
