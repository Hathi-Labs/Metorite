import { describe, expect, it } from "vitest";

import { clampMenuPos } from "./menuClamp";

const view = { width: 390, height: 800 };
const size = { width: 200, height: 160 };

describe("clampMenuPos (MOB-9 fix round)", () => {
  it("leaves a menu that fits where it is", () => {
    expect(clampMenuPos({ x: 20, y: 100 }, size, view, null)).toEqual({ x: 20, y: 100 });
  });

  it("with no bar, the foot is the window's", () => {
    expect(clampMenuPos({ x: 20, y: 700 }, size, view, null)).toEqual({ x: 20, y: 636 });
  });

  it("with a bar, the foot is the bar's top edge, so no row sits under a tab", () => {
    const bar = 744;
    const at = clampMenuPos({ x: 20, y: 700 }, size, view, bar);
    expect(at.y + size.height).toBeLessThanOrEqual(bar);
    expect(at.y).toBe(bar - size.height - 4);
  });

  it("keeps the menu inside the width, and never above 4px", () => {
    expect(clampMenuPos({ x: 380, y: 0 }, size, view, 744).x).toBe(186);
    expect(clampMenuPos({ x: 0, y: 0 }, size, { width: 390, height: 100 }, 80).y).toBe(4);
  });
});
