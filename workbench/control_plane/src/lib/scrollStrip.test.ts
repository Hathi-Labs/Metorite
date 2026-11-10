import { describe, expect, it } from "vitest";
import { EDGE_ALLOWANCE, edgeMask, hiddenEdges, pageTarget, wheelToRow, type Span } from "./scrollStrip";

/** Ten chips, 100px wide, 8px apart, from 0. */
const SPANS: Span[] = Array.from({ length: 10 }, (_, i) => ({ left: i * 108, right: i * 108 + 100 }));
const WIDTH = 10 * 108 - 8; // 1072
const VIEW = 400;

describe("hiddenEdges", () => {
  it("reports no hidden edge when the row fits", () => {
    expect(hiddenEdges(0, 500, 500)).toEqual({ start: false, end: false });
  });

  it("reports the end at the start, both in the middle, the start at the end", () => {
    expect(hiddenEdges(0, VIEW, WIDTH)).toEqual({ start: false, end: true });
    expect(hiddenEdges(200, VIEW, WIDTH)).toEqual({ start: true, end: true });
    expect(hiddenEdges(WIDTH - VIEW, VIEW, WIDTH)).toEqual({ start: true, end: false });
  });

  it("treats a subpixel remainder as the edge, as browser zoom leaves one", () => {
    expect(hiddenEdges(WIDTH - VIEW - 0.5, VIEW, WIDTH).end).toBe(false);
    expect(hiddenEdges(0.5, VIEW, WIDTH).start).toBe(false);
  });
});

describe("pageTarget", () => {
  it("forward: the first chip cut by the right edge becomes the first at the left", () => {
    // View 0..400 less the arrow: 364. Chip 3 (324..424) is cut.
    expect(pageTarget(1, 0, VIEW, WIDTH, SPANS)).toBe(324 - EDGE_ALLOWANCE);
  });

  it("back: the last chip cut by the left edge becomes the last at the right", () => {
    const from = 324 - EDGE_ALLOWANCE; // 288. Start plus the arrow: 324. Chip 2 (216..316) is cut.
    expect(pageTarget(-1, from, VIEW, WIDTH, SPANS)).toBe(0);
    // From 600, start plus the arrow is 636. Chip 5 (540..640) is cut, so its
    // right edge lands at the right, behind room for the arrow.
    expect(pageTarget(-1, 600, VIEW, WIDTH, SPANS)).toBe(640 - VIEW + EDGE_ALLOWANCE);
  });

  it("never pages past either end", () => {
    const max = WIDTH - VIEW;
    expect(pageTarget(1, max - 10, VIEW, WIDTH, SPANS)).toBe(max);
    expect(pageTarget(-1, 10, VIEW, WIDTH, SPANS)).toBe(0);
  });

  it("every press moves forward, page after page, until the end", () => {
    let at = 0;
    const seen: number[] = [];
    for (let i = 0; i < 10 && at < WIDTH - VIEW; i++) {
      const next = pageTarget(1, at, VIEW, WIDTH, SPANS);
      expect(next).toBeGreaterThan(at);
      seen.push(next);
      at = next;
    }
    expect(at).toBe(WIDTH - VIEW);
    expect(seen.length).toBeLessThan(5);
  });

  it("a chip wider than the view still moves, by most of the view", () => {
    const wide: Span[] = [{ left: 0, right: 900 }, { left: 908, right: 1000 }];
    expect(pageTarget(1, 0, VIEW, 1000, wide)).toBe(VIEW * 0.8);
    // Back from 600, the wide chip's right edge comes first (536). From there
    // it would page by zero, so the press falls back to most of the view.
    expect(pageTarget(-1, 600, VIEW, 1000, wide)).toBe(900 - VIEW + EDGE_ALLOWANCE);
    expect(pageTarget(-1, 536, VIEW, 1000, wide)).toBe(536 - VIEW * 0.8);
  });
});

describe("wheelToRow", () => {
  const middle = { start: true, end: true };

  it("turns a vertical wheel into a sideways move", () => {
    expect(wheelToRow(0, 100, middle)).toBe(100);
    expect(wheelToRow(0, -100, middle)).toBe(-100);
  });

  it("leaves a sideways swipe to the browser", () => {
    expect(wheelToRow(80, 10, middle)).toBe(0);
  });

  it("hands the wheel back to the page at the end of the row", () => {
    expect(wheelToRow(0, 100, { start: true, end: false })).toBe(0);
    expect(wheelToRow(0, -100, { start: false, end: true })).toBe(0);
    expect(wheelToRow(0, 100, { start: false, end: false })).toBe(0);
  });
});

describe("edgeMask", () => {
  it("draws no mask when the row fits", () => {
    expect(edgeMask({ start: false, end: false })).toBeUndefined();
  });

  it("fades only the edges with chips behind them", () => {
    const end = edgeMask({ start: false, end: true })!;
    expect(end.startsWith("linear-gradient(to right, var(--foreground) 0,")).toBe(true);
    expect(end.endsWith("transparent 100%)")).toBe(true);
    const start = edgeMask({ start: true, end: false })!;
    expect(start).toContain("transparent 0");
    expect(start.endsWith("var(--foreground) 100%)")).toBe(true);
  });
});
