/**
 * The arithmetic behind "the chart scrolls itself while you drag".
 *
 * `vitest.config.ts` is `environment: "node"`, so nothing here can watch a
 * scroll happen. That is why the decisions live in `edgeScroll.ts` and the
 * `requestAnimationFrame` loop stays in the component: this file can check
 * every rule, and the part it cannot check is three lines long.
 */
import { describe, expect, it } from "vitest";

import {
  DRAG_SLOP,
  EDGE_ZONE,
  MAX_SPEED_X,
  MAX_SPEED_Y,
  applyScroll,
  edgeVelocity,
  frameStep,
  ramp,
} from "./edgeScroll";

/** A 1000×600 chart with its top-left at (300, 100). */
const BOX = { left: 300, right: 1300, top: 100, bottom: 700 };

const at = (x: number, y = 400) => edgeVelocity({ x, y }, BOX);

describe("edgeVelocity", () => {
  it("does nothing in the middle, which is where a drag usually is", () => {
    expect(at(800)).toEqual({ x: 0, y: 0 });
  });

  it("pulls LEFT near the left edge and RIGHT near the right one", () => {
    expect(at(BOX.left + 10).x).toBeLessThan(0);
    expect(at(BOX.right - 10).x).toBeGreaterThan(0);
  });

  it("keeps pulling once the pointer has left the box", () => {
    // ⚠️ The case that decides whether this feels broken. The pointer leaves
    // constantly — over the sticky task column, over the header, off the
    // window — and each is the clearest statement yet of which way you want
    // to go. Stopping there would stop exactly when somebody pushes hardest.
    expect(at(BOX.left - 200).x).toBe(-MAX_SPEED_X);
    expect(at(BOX.right + 500).x).toBe(MAX_SPEED_X);
  });

  it("is gentle on entry and fast at the edge", () => {
    const justInside = Math.abs(at(BOX.left + EDGE_ZONE - 1).x);
    const halfway = Math.abs(at(BOX.left + EDGE_ZONE / 2).x);
    const atEdge = Math.abs(at(BOX.left).x);

    expect(justInside).toBeLessThan(MAX_SPEED_X * 0.05);
    // Quadratic: halfway in is a QUARTER speed, not half. Linear here reads as
    // drift rather than as a control, because a 64px zone is somewhere you sit
    // while working near an edge rather than somewhere you visit.
    expect(halfway).toBeCloseTo(MAX_SPEED_X * 0.25, 5);
    expect(atEdge).toBe(MAX_SPEED_X);
  });

  it("never exceeds its top speed", () => {
    for (const x of [-10_000, 0, 299, 300, 700, 1299, 1300, 99_999]) {
      expect(Math.abs(at(x).x)).toBeLessThanOrEqual(MAX_SPEED_X);
    }
  });

  it("treats the vertical axis the same way, but slower", () => {
    expect(edgeVelocity({ x: 800, y: BOX.top - 50 }, BOX).y).toBe(-MAX_SPEED_Y);
    expect(edgeVelocity({ x: 800, y: BOX.bottom + 50 }, BOX).y).toBe(MAX_SPEED_Y);
    // Rows are 40px. Faster and you cannot see what you are aiming an arrow at.
    expect(MAX_SPEED_Y).toBeLessThan(MAX_SPEED_X);
  });

  it("moves BOTH ways in a corner", () => {
    const corner = edgeVelocity({ x: BOX.left, y: BOX.bottom }, BOX);
    expect(corner.x).toBe(-MAX_SPEED_X);
    expect(corner.y).toBe(MAX_SPEED_Y);
  });

  it("does not let two zones overlap in a narrow box", () => {
    // ⚠️ A box thinner than two zones would have both edge tests match, and
    // whichever was written first would win — so one side of a narrow chart
    // would scroll the wrong way. Halving makes them meet in the middle.
    const thin = { left: 0, right: 80, top: 0, bottom: 600 };
    expect(edgeVelocity({ x: 10, y: 300 }, thin).x).toBeLessThan(0);
    expect(edgeVelocity({ x: 70, y: 300 }, thin).x).toBeGreaterThan(0);
    expect(edgeVelocity({ x: 40, y: 300 }, thin).x).toBe(0);
  });

  it("survives a box with no width at all", () => {
    // A chart measured before layout. Returning NaN here would feed NaN into
    // `scrollLeft`, and the whole chart jumps to 0.
    const empty = { left: 500, right: 500, top: 0, bottom: 0 };
    const got = edgeVelocity({ x: 500, y: 0 }, empty);
    expect(got).toEqual({ x: 0, y: 0 });
    expect(Number.isNaN(got.x)).toBe(false);
  });
});

describe("ramp", () => {
  it("clamps outside 0..1 rather than extrapolating", () => {
    expect(ramp(-3)).toBe(0);
    expect(ramp(9)).toBe(1);
  });
});

describe("frameStep", () => {
  it("is time-based, so speed does not follow the refresh rate", () => {
    // ⚠️ A fixed step per frame travels twice as far on a 120Hz display, and
    // crawls whenever the main thread is busy — which, during a drag that
    // re-renders a Gantt, is precisely when it is busy.
    expect(frameStep(1000, 16)).toBeCloseTo(16, 5);
    expect(frameStep(1000, 8)).toBeCloseTo(8, 5);
  });

  it("clamps a long gap instead of leaping a third of a year", () => {
    // A backgrounded tab or one long task hands back seconds. At full speed
    // that is thousands of pixels in one frame.
    expect(frameStep(1000, 5000)).toBe(frameStep(1000, 50));
    expect(frameStep(1000, 5000)).toBeLessThanOrEqual(50);
  });

  it("does not run backwards on a negative gap", () => {
    expect(frameStep(1000, -16)).toBe(0);
  });

  it("keeps a sub-pixel step rather than rounding it away", () => {
    // The first half of the zone is well under a pixel per frame. Round each
    // frame and that half is dead.
    expect(frameStep(30, 16)).toBeGreaterThan(0);
    expect(frameStep(30, 16)).toBeLessThan(1);
  });
});

describe("applyScroll", () => {
  it("moves the element and reports what moved", () => {
    const el = { scrollLeft: 100, scrollTop: 50 };
    expect(applyScroll(el, 20, -10)).toEqual({ dx: 20, dy: -10 });
    expect(el).toEqual({ scrollLeft: 120, scrollTop: 40 });
  });

  it("reports ZERO when the DOM refuses the scroll", () => {
    // 🔴 The assertion the whole return value exists for. A real element
    // clamps `scrollLeft` at 0 and at its maximum. The caller feeds this back
    // into the drag's origin to keep the bar under the cursor — so a scroll
    // that did not happen must not be compensated for, or the bar drifts away
    // from the pointer every time somebody drags against the start of the
    // calendar. That is the one place they push hardest.
    const el = {
      _x: 0,
      get scrollLeft() {
        return this._x;
      },
      set scrollLeft(next: number) {
        this._x = Math.max(0, next); // what a real element does
      },
      scrollTop: 0,
    };
    expect(applyScroll(el, -500, 0).dx).toBe(0);
    expect(el.scrollLeft).toBe(0);
  });

  it("does not touch an axis it was given nothing for", () => {
    // Writing `scrollTop = scrollTop` on a real element is not free: it
    // cancels a smooth scroll in progress and can fire a scroll event.
    let writes = 0;
    const el = {
      scrollLeft: 0,
      _y: 30,
      get scrollTop() {
        return this._y;
      },
      set scrollTop(next: number) {
        writes += 1;
        this._y = next;
      },
    };
    applyScroll(el, 10, 0);
    expect(writes).toBe(0);
  });
});

describe("DRAG_SLOP", () => {
  it("is small enough that a real drag never notices it", () => {
    // A deliberate drag travels tens of pixels before it means anything —
    // half a day-column is 12px at the month zoom. Slop larger than that
    // would swallow the start of genuine drags.
    expect(DRAG_SLOP).toBeLessThan(8);
  });

  it("is large enough to absorb the hand movement in a click", () => {
    // 🔴 Zero here is the defect it exists for: the loop arms on the press,
    // the chart scrolls during it, the drag's origin follows, and the click
    // commits a date change instead of opening the task.
    expect(DRAG_SLOP).toBeGreaterThan(1);
  });

  it("is smaller than the edge zone it gates", () => {
    // Otherwise a pointer could cross the whole trigger zone before the loop
    // is allowed to look at it.
    expect(DRAG_SLOP).toBeLessThan(EDGE_ZONE);
  });
});
