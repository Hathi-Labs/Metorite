/**
 * The My Tasks lists rail folds below `lg`, and keeps the member's choice
 * inside a band. See `railFold.ts` for the measurement that asked for it.
 */

import { describe, expect, it } from "vitest";

import { RAIL_WIDE_QUERY, railOnBand, railStart, railToggle } from "./railFold";

describe("the rail folds itself on tablet widths", () => {
  it("starts folded below lg and open at or above it", () => {
    expect(railStart("narrow").open).toBe(false);
    expect(railStart("wide").open).toBe(true);
  });

  it("the band edge is Tailwind's lg", () => {
    expect(RAIL_WIDE_QUERY).toBe("(min-width: 1024px)");
  });

  it("the member's choice holds while the width stays in its band", () => {
    const opened = railToggle(railStart("narrow"));
    expect(opened.open).toBe(true);
    // A resize inside the band is not a reason to undo what they chose.
    expect(railOnBand(opened, "narrow")).toEqual(opened);
    const closed = railToggle(railStart("wide"));
    expect(railOnBand(closed, "wide").open).toBe(false);
  });

  it("crossing into the other band applies that band's default", () => {
    const opened = railToggle(railStart("narrow"));
    expect(railOnBand(opened, "wide")).toEqual({ open: true, band: "wide" });
    const closed = railToggle(railStart("wide"));
    expect(railOnBand(closed, "narrow")).toEqual({ open: false, band: "narrow" });
    // And back up: a rail the member folded on a desktop comes back open
    // after a trip through the tablet band.
    expect(railOnBand(railOnBand(closed, "narrow"), "wide").open).toBe(true);
  });
});
