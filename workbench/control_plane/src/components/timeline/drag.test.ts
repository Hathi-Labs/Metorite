import { describe, expect, it } from "vitest";

import { clampEndDay, clampStartDay, orderedSpan } from "./drag";

const SPAN = { from: "2026-08-10", to: "2026-08-14" };

describe("drag date maths", () => {
  it("orders a reversed pair", () => {
    expect(orderedSpan("2026-08-14", "2026-08-10")).toEqual(SPAN);
    expect(orderedSpan("2026-08-10", "2026-08-14")).toEqual(SPAN);
  });
  it("stops the left edge at the right edge, and never swaps", () => {
    expect(clampStartDay(SPAN, "2026-08-20")).toBe("2026-08-14");
    expect(clampStartDay(SPAN, "2026-08-01")).toBe("2026-08-01");
  });
  it("stops the right edge at the left edge", () => {
    expect(clampEndDay(SPAN, "2026-08-01")).toBe("2026-08-10");
    expect(clampEndDay(SPAN, "2026-08-20")).toBe("2026-08-20");
  });
});
