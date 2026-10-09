/**
 * The logo pipeline's pure half (`logoImage.ts`). Each case is a kind of file
 * an admin really has: a PNG on transparency, a JPEG on white, a wordmark too
 * long for the server, a black logo that vanishes on the dark sidebar.
 */
import { describe, expect, it } from "vitest";

import {
  adviseDarkStyle,
  contentBounds,
  detectBackground,
  fitAspect,
  inkStats,
  outputSize,
  removeSolid,
  toWhite,
  type Pixels,
} from "./logoImage";

/** A w×h image of `fill`, with a `box` painted in `ink`. */
function img(w: number, h: number, fill: number[], ink: number[], box = { x: 0, y: 0, w: 0, h: 0 }): Pixels {
  const data = new Uint8ClampedArray(w * h * 4);
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const inside = x >= box.x && x < box.x + box.w && y >= box.y && y < box.y + box.h;
      data.set(inside ? ink : fill, (y * w + x) * 4);
    }
  }
  return { data, width: w, height: h };
}

const CLEAR = [0, 0, 0, 0];
const WHITE = [255, 255, 255, 255];
const BLACK = [0, 0, 0, 255];
const BRAND_BLUE = [20, 90, 230, 255];

describe("what surrounds the logo", () => {
  it("knows a transparent PNG", () => {
    expect(detectBackground(img(40, 20, CLEAR, BLACK, { x: 5, y: 5, w: 10, h: 10 }))).toEqual({ kind: "transparent" });
  });
  it("knows a JPEG on white by its four corners", () => {
    expect(detectBackground(img(40, 20, WHITE, BLACK, { x: 5, y: 5, w: 10, h: 10 }))).toEqual({
      kind: "solid",
      rgb: [255, 255, 255],
    });
  });
  it("calls a full-bleed image none", () => {
    const p = img(4, 4, WHITE, BLACK, { x: 0, y: 0, w: 2, h: 4 });
    expect(detectBackground(p).kind).toBe("none");
  });
});

describe("the empty margin is trimmed", () => {
  it("on transparency", () => {
    expect(contentBounds(img(100, 50, CLEAR, BLACK, { x: 10, y: 5, w: 60, h: 20 }))).toEqual({ x: 10, y: 5, w: 60, h: 20 });
  });
  it("on a solid white background", () => {
    expect(contentBounds(img(100, 50, WHITE, BLACK, { x: 30, y: 12, w: 40, h: 25 }))).toEqual({ x: 30, y: 12, w: 40, h: 25 });
  });
  it("keeps the whole image when nothing stands out", () => {
    expect(contentBounds(img(30, 30, WHITE, WHITE))).toEqual({ x: 0, y: 0, w: 30, h: 30 });
  });
});

describe("the shape always fits, so the server never refuses it", () => {
  it("gives a 20:1 wordmark enough height for 8:1", () => {
    const b = fitAspect({ x: 0, y: 0, w: 400, h: 20 });
    expect(b.w / b.h).toBeLessThanOrEqual(8);
    expect(b.w).toBe(400);
  });
  it("gives a tall mark enough width for 1:2", () => {
    const b = fitAspect({ x: 0, y: 0, w: 10, h: 100 });
    expect(b.w / b.h).toBeGreaterThanOrEqual(0.5);
  });
  it("leaves a normal shape alone", () => {
    const b = { x: 1, y: 2, w: 300, h: 100 };
    expect(fitAspect(b)).toBe(b);
  });
});

describe("the output size", () => {
  it("is the slot's height at 3x for a normal wordmark", () => {
    expect(outputSize({ x: 0, y: 0, w: 1200, h: 400 })).toEqual({ width: 252, height: 84 });
  });
  it("never exceeds the slot's width at 3x for a long one", () => {
    const s = outputSize({ x: 0, y: 0, w: 2000, h: 250 });
    expect(s.width).toBeLessThanOrEqual(540);
    expect(s.height).toBeLessThanOrEqual(84);
  });
  it("stays above the server's 32px minimum", () => {
    const s = outputSize({ x: 0, y: 0, w: 40, h: 20 });
    expect(Math.max(s.width, s.height)).toBeGreaterThanOrEqual(32);
  });
});

describe("dark mode", () => {
  it("leaves a light logo as it is", () => {
    expect(adviseDarkStyle(inkStats(img(10, 10, CLEAR, WHITE, { x: 0, y: 0, w: 5, h: 5 })))).toBe("same");
  });
  it("makes a black logo white", () => {
    expect(adviseDarkStyle(inkStats(img(10, 10, CLEAR, BLACK, { x: 0, y: 0, w: 5, h: 5 })))).toBe("white");
  });
  it("keeps a dark colourful logo's colours, on a light card", () => {
    const navy = [20, 30, 120, 255];
    expect(adviseDarkStyle(inkStats(img(10, 10, CLEAR, navy, { x: 0, y: 0, w: 5, h: 5 })))).toBe("plate");
  });
  it("leaves a bright brand colour as it is", () => {
    const sky = [80, 190, 255, 255];
    expect(adviseDarkStyle(inkStats(img(10, 10, CLEAR, sky, { x: 0, y: 0, w: 5, h: 5 })))).toBe("same");
  });

  // ⚠️ Measured 2026-10-09: a bright mark beside dark text lifted the old
  // whole-logo AVERAGE over the bar, and the text vanished on dark.
  const twoTone = (left: number[], right: number[]) => {
    const p = img(20, 10, CLEAR, left, { x: 0, y: 0, w: 10, h: 10 });
    for (let y = 0; y < 10; y++) for (let x = 10; x < 20; x++) p.data.set(right, (y * 20 + x) * 4);
    return p;
  };
  const ORANGE = [242, 107, 29, 255];
  const NAVY = [20, 40, 110, 255];

  it("does not let a bright mark hide dark navy text: a light card", () => {
    expect(adviseDarkStyle(inkStats(twoTone(ORANGE, NAVY)))).toBe("plate");
  });
  it("turns black text white beside a bright mark", () => {
    expect(adviseDarkStyle(inkStats(twoTone(ORANGE, BLACK)))).toBe("white");
  });
});

describe("the pixel edits", () => {
  it("clears a white background and keeps the ink", () => {
    const p = img(4, 1, WHITE, BLACK, { x: 0, y: 0, w: 2, h: 1 });
    removeSolid(p, [255, 255, 255]);
    expect(Array.from(p.data.slice(3, 4))).toEqual([255]);
    expect(Array.from(p.data.slice(15, 16))).toEqual([0]);
  });
  it("turns a dark, neutral pixel white and keeps its alpha", () => {
    const p = img(2, 1, CLEAR, [30, 30, 30, 128], { x: 0, y: 0, w: 1, h: 1 });
    toWhite(p);
    expect(Array.from(p.data.slice(0, 4))).toEqual([255, 255, 255, 128]);
    expect(Array.from(p.data.slice(4, 8))).toEqual([0, 0, 0, 0]);
  });
  it("keeps a brand colour as it is in the white version", () => {
    const p = img(2, 1, CLEAR, BRAND_BLUE, { x: 0, y: 0, w: 2, h: 1 });
    toWhite(p);
    expect(Array.from(p.data.slice(0, 4))).toEqual(BRAND_BLUE);
  });
});
