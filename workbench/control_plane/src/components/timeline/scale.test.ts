import { describe, expect, it } from "vitest";

import {
  WINDOW_LIMIT_DAYS,
  ZOOMS,
  rangeForSpans,
  unionSpan,
  windowCentre,
  windowFor,
  windowIncluding,
} from "./scale";

describe("unionSpan", () => {
  it("covers every span, or is null for none", () => {
    expect(unionSpan([])).toBeNull();
    expect(
      unionSpan([
        { from: "2026-08-05", to: "2026-08-06" },
        { from: "2026-08-01", to: "2026-08-02" },
        { from: "2026-08-09", to: "2026-08-12" },
      ]),
    ).toEqual({ from: "2026-08-01", to: "2026-08-12" });
  });
});

describe("rangeForSpans", () => {
  it("pads the data by a week each side", () => {
    const range = rangeForSpans([{ from: "2026-08-10", to: "2026-08-12" }], "2026-08-11");
    expect(range.from).toBe("2026-08-03");
    expect(range.to).toBe("2026-08-19");
    expect(range.days).toBe(17);
    expect(range.widthPx).toBe(17 * ZOOMS.month.dayWidth);
  });
  it("falls back to a fortnight around today with no span", () => {
    const range = rangeForSpans([], "2026-08-15");
    expect([range.from, range.to]).toEqual(["2026-08-01", "2026-08-29"]);
  });
  it("unions the window with the data", () => {
    const range = rangeForSpans(
      [{ from: "2026-08-10", to: "2026-08-12" }],
      "2026-08-11",
      "week",
      { from: "2026-07-01", to: "2026-09-30" },
    );
    expect([range.from, range.to]).toEqual(["2026-07-01", "2026-09-30"]);
    expect(range.pxPerDay).toBe(ZOOMS.week.dayWidth);
  });
});

describe("the window", () => {
  it("is centred on the anchor and stays inside the server cap", () => {
    for (const zoom of ["week", "month", "quarter"] as const) {
      expect(windowCentre(windowFor(zoom, "2026-08-15"))).toBe("2026-08-15");
    }
    const far = windowIncluding(windowFor("quarter", "2026-08-15"), "2030-01-01");
    expect(far.to).toBe("2030-01-01");
    const span = (Date.parse(far.to) - Date.parse(far.from)) / 86_400_000;
    expect(span).toBeLessThanOrEqual(WINDOW_LIMIT_DAYS);
  });
});
