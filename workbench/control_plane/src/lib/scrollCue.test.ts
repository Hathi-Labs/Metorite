import { describe, expect, it } from "vitest";

import { hasMoreToTheRight, stepRight } from "./scrollCue";

describe("hasMoreToTheRight", () => {
  it("is true while lanes sit past the right edge", () => {
    expect(hasMoreToTheRight(600, 360, 0)).toBe(true);
    expect(hasMoreToTheRight(600, 360, 200)).toBe(true);
  });

  it("is false at the end, and when everything fits", () => {
    expect(hasMoreToTheRight(600, 360, 240)).toBe(false);
    expect(hasMoreToTheRight(360, 360, 0)).toBe(false);
  });

  it("ignores a sub-pixel remainder", () => {
    expect(hasMoreToTheRight(361.5, 360, 0)).toBe(false);
  });
});

describe("stepRight", () => {
  it("steps four fifths of the view and never zero", () => {
    expect(stepRight(1000)).toBe(800);
    expect(stepRight(0)).toBe(1);
  });
});
