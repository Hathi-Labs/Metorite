// WS-50 slice 7 — the period a money page covers.

import { describe, expect, it } from "vitest";

import { consoleQuery, presetHref, rangeFrom, todayIst } from "./range";

// 2026-10-09 22:30 UTC is already 10 October in India.
const LATE = new Date("2026-10-09T22:30:00Z");
const NOON = new Date("2026-10-09T06:30:00Z");

describe("todayIst", () => {
  it("is the India date, not the server's", () => {
    expect(todayIst(LATE)).toBe("2026-10-10");
    expect(todayIst(NOON)).toBe("2026-10-09");
  });
});

describe("rangeFrom", () => {
  it("defaults to the last 30 days, rolling", () => {
    const r = rangeFrom({}, NOON);
    expect(r).toMatchObject({ key: "30d", from: null, days: 30, label: "last 30 days" });
    expect(consoleQuery(r)).toBe("days=30");
  });

  it("reads this month in India", () => {
    const r = rangeFrom({ range: "month" }, LATE);
    expect(r).toMatchObject({ from: "2026-10-01", to: "2026-10-10", days: 10 });
    expect(consoleQuery(r)).toBe("from=2026-10-01&to=2026-10-10");
  });

  it("reads last month whole", () => {
    const r = rangeFrom({ range: "last-month" }, NOON);
    expect(r).toMatchObject({ from: "2026-09-01", to: "2026-09-30", days: 30 });
    expect(r.label).toBe("1 Sept – 30 Sept 2026");
  });

  it("takes a custom range and labels it", () => {
    const r = rangeFrom({ range: "custom", from: "2026-08-01", to: "2026-08-31" }, NOON);
    expect(r).toMatchObject({ key: "custom", days: 31, error: null });
  });

  it("refuses an unusable custom range, falls back to 30 days, and says why", () => {
    expect(rangeFrom({ range: "custom", from: "2026-08-31", to: "2026-08-01" }, NOON).error).toContain(
      "before the start",
    );
    expect(rangeFrom({ range: "custom", from: "nope" }, NOON).error).toContain("both dates");
    expect(rangeFrom({ range: "custom", from: "2024-01-01", to: "2026-08-01" }, NOON).key).toBe("30d");
  });
});

describe("presetHref", () => {
  it("keeps other parameters, and leaves the default out of the URL", () => {
    expect(presetHref("/money", "30d")).toBe("/money");
    expect(presetHref("/money", "month")).toBe("/money?range=month");
    expect(presetHref("/customers/a", "7d", { tab: "overview" })).toBe("/customers/a?tab=overview&range=7d");
  });
});
