import { describe, it, expect } from "vitest";
import {
  formatPaise,
  seatsDigest,
  formatDate,
  lifecycleActions,
  canActivate,
  type SeatRow,
} from "./format";

const seat = (over: Partial<SeatRow>): SeatRow => ({
  plan_slug: "core",
  purchased: 2,
  assigned: 1,
  available: 1,
  oversubscribed: false,
  ...over,
});

describe("formatPaise", () => {
  it("renders integer paise as ₹, dividing by 100 exactly", () => {
    // 300000 paise = ₹3,000 — the MRR of core 2 + sales 3 seats at ₹600.
    expect(formatPaise(300000)).toContain("3,000");
    expect(formatPaise(0)).toContain("0");
    expect(formatPaise(150050)).toContain("1,500.50");
  });
});

describe("seatsDigest", () => {
  it("summarises assigned/purchased per plan in catalog order", () => {
    expect(
      seatsDigest([
        seat({ plan_slug: "core", assigned: 1, purchased: 2 }),
        seat({ plan_slug: "sales", assigned: 0, purchased: 3 }),
      ]),
    ).toBe("core 1/2, sales 0/3");
  });

  it("shows a dash for an org with no seats", () => {
    expect(seatsDigest([])).toBe("—");
  });
});

describe("formatDate", () => {
  it("takes the date part, and shows a dash for null", () => {
    expect(formatDate("2026-09-01T12:00:00+00:00")).toBe("2026-09-01");
    expect(formatDate(null)).toBe("—");
  });
});

describe("lifecycleActions (advisory UX only)", () => {
  it("offers Suspend for a live org", () => {
    expect(lifecycleActions("active")).toEqual([
      { label: "Suspend", target: "suspended" },
    ]);
    expect(lifecycleActions("trial")).toEqual([
      { label: "Suspend", target: "suspended" },
    ]);
  });

  it("offers Resume for a suspended org", () => {
    expect(lifecycleActions("suspended")).toEqual([
      { label: "Resume", target: "active" },
    ]);
  });

  it("offers nothing on a terminal lifecycle", () => {
    expect(lifecycleActions("cancelled")).toEqual([]);
    expect(lifecycleActions("deleted")).toEqual([]);
  });
});

describe("canActivate", () => {
  it("is false only for an already-active subscription", () => {
    expect(canActivate("trial")).toBe(true);
    expect(canActivate(null)).toBe(true);
    expect(canActivate("active")).toBe(false);
  });
});
