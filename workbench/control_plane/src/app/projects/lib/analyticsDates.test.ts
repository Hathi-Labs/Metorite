import { describe, expect, it } from "vitest";
import { day, period } from "../components/AnalyticsPanels";

/**
 * A `finished` payload with no `period_start` threw in `day()` and froze the
 * whole Analytics page in the dev build, even inside a LayoutBoundary (S12
 * visual review, 2026-09-28). The helpers now never throw.
 */
describe("the Analytics date helpers", () => {
  it("format a real period", () => {
    expect(day("2026-09-20")).toBe("20 Sep 2026");
    expect(period("2026-06-29", "2026-09-20")).toBe("29 Jun – 20 Sep 2026");
    expect(period("2025-12-29", "2026-01-04")).toBe("29 Dec 2025 – 4 Jan 2026");
  });

  it.each([undefined, null, "", "not a date", 20260920, "2026-13-01"])(
    "never throw on %p",
    (bad) => {
      expect(() => day(bad)).not.toThrow();
      expect(day(bad)).toBe("");
      expect(period(bad, "2026-09-20")).toBe("this period");
      expect(period("2026-09-20", bad)).toBe("this period");
    },
  );
});
