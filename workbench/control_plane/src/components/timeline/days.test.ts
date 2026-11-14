import { describe, expect, it } from "vitest";

import { dayKey, fromDayKey, mondayOffset, shiftDay } from "./days";

describe("day keys", () => {
  it("round trips a key through a local Date", () => {
    expect(dayKey(fromDayKey("2026-08-07"))).toBe("2026-08-07");
    expect(fromDayKey("2026-08-07").getHours()).toBe(0);
  });
  it("rolls a month and a year over", () => {
    expect(shiftDay("2026-08-31", 1)).toBe("2026-09-01");
    expect(shiftDay("2026-01-01", -1)).toBe("2025-12-31");
  });
  it("counts the week from Monday", () => {
    expect(mondayOffset(fromDayKey("2026-08-10"))).toBe(0);
    expect(mondayOffset(fromDayKey("2026-08-16"))).toBe(6);
  });
});
