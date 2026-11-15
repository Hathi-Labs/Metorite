/**
 * STU-LY-4 fence: the sideways-scroll cue reads three numbers and steps four fifths of the view.
 */
import { describe, expect, it } from "vitest";

import { hasMoreRight, stepRight } from "./scrollCue";

describe("hasMoreRight", () => {
  it("is false when the content fits, and for a one pixel rounding gap", () => {
    expect(hasMoreRight({ scrollWidth: 800, clientWidth: 800, scrollLeft: 0 })).toBe(false);
    expect(hasMoreRight({ scrollWidth: 801, clientWidth: 800, scrollLeft: 0 })).toBe(false);
  });
  it("is true when a lane hides past the right edge, and false again at the end", () => {
    expect(hasMoreRight({ scrollWidth: 1200, clientWidth: 800, scrollLeft: 0 })).toBe(true);
    expect(hasMoreRight({ scrollWidth: 1200, clientWidth: 800, scrollLeft: 398 })).toBe(true);
    expect(hasMoreRight({ scrollWidth: 1200, clientWidth: 800, scrollLeft: 400 })).toBe(false);
  });
});

describe("stepRight", () => {
  it("steps four fifths of the view and never zero", () => {
    expect(stepRight(1000)).toBe(800);
    expect(stepRight(0)).toBe(1);
  });
});
