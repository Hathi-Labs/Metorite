/**
 * MOB-9 (task 2002): the capture sheets size to the visual viewport, and the
 * phone's bottom bar paints above them. The height stops at the bar's top edge.
 */
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import { heightAboveBar } from "./useVisualViewport";

describe("heightAboveBar", () => {
  it("takes the bar off when the visual viewport runs under it", () => {
    expect(heightAboveBar({ height: 844, offsetTop: 0 }, 788)).toBe(788);
  });
  it("takes nothing off when the keyboard lifts the viewport above the bar", () => {
    expect(heightAboveBar({ height: 450, offsetTop: 0 }, 788)).toBe(450);
  });
  it("takes nothing off with no bar", () => {
    expect(heightAboveBar({ height: 844, offsetTop: 0 }, null)).toBe(844);
  });
  it("follows a scrolled visual viewport", () => {
    expect(heightAboveBar({ height: 500, offsetTop: 400 }, 788)).toBe(388);
  });
});

describe("the sheets that size to the visual viewport", () => {
  it("read it through the one hook", () => {
    for (const f of ["QuickCapture", "ClarifyModal", "ReclarifyModal"]) {
      const src = readFileSync(new URL(`../components/${f}.tsx`, import.meta.url), "utf-8");
      expect(src, f).toContain("useVisualViewport()");
    }
    const hook = readFileSync(new URL("./useVisualViewport.ts", import.meta.url), "utf-8");
    expect(hook).toContain("height: heightAboveBar(vv, phoneBarTop())");
  });
});
