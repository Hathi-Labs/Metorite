// WS-50 slice 4 — money across every customer (D94). The fence for `lib/fleet.ts`.

import { describe, expect, it } from "vitest";

import { fleetMoney, sortFleet, totalsHow } from "./fleet";
import type { OrgRow } from "./format";
import type { OrgUsageRow, OrgUsageView } from "./usage";

const NOW = new Date("2026-10-09T00:00:00Z");

function org(slug: string, mrrPaise: number, seats = 2): OrgRow {
  return {
    slug,
    name: slug.toUpperCase(),
    status: "active",
    subscription_status: "active",
    provider: null,
    trial_ends_at: null,
    current_period_end: null,
    export_until: null,
    credit_balance: "100",
    mrr_paise: mrrPaise,
    seats: [{ plan_slug: "core", purchased: seats, assigned: seats, available: 0, oversubscribed: false }],
  } as OrgRow;
}

function usage(slug: string, over: Partial<OrgUsageRow> = {}): OrgUsageRow {
  return {
    slug,
    name: slug,
    calls: 10,
    credits: "100",
    members: 1,
    costUsd: "1",
    balance: "500",
    lastSeen: null,
    marginRatio: null,
    runwayDays: 30,
    silent: false,
    refusals: 0,
    unbilledCalls: 0,
    unbilledTokens: 0,
    paidCredits: "100",
    paidValueInr: "100",
    unpricedPaidCredits: "0",
    freeCredits: "0",
    unbackedCredits: "0",
    costedShare: "1",
    ...over,
  };
}

function view(rows: OrgUsageRow[], over: Partial<OrgUsageView> = {}): Partial<OrgUsageView> {
  return {
    windowDays: 30,
    rows,
    drawsSince: "2026-08-01T00:00:00Z",
    inrPerCredit: "1",
    usdToInr: "85",
    ...over,
  };
}

describe("fleetMoney", () => {
  const fleet = fleetMoney(
    [org("a", 100_000), org("b", 0)],
    view([usage("a"), usage("b", { costUsd: "10" })]),
    NOW,
  );

  it("joins each customer to its usage row through the one money model", () => {
    const a = fleet.rows.find((r) => r.org.slug === "a")!;
    // Seats ₹1,000 + bought credits ₹100; AI cost $1 × 85.
    expect(a.money?.charged.value).toBe(1100);
    expect(a.money?.aiCost.value).toBe(85);
  });

  it("totals charged, AI cost and profit across customers", () => {
    expect(fleet.totals.charged).toBe(1200);
    expect(fleet.totals.aiCost).toBe(85 + 850);
    expect(fleet.totals.profit).toBe(1200 - 935);
  });

  it("states the AI margin apart from seats", () => {
    // AI alone: ₹200 of bought credits against ₹935 of AI cost.
    expect(fleet.totals.aiRevenue).toBe(200);
    expect(fleet.totals.aiMargin).toBeCloseTo((200 - 935) / 200);
  });

  it("counts the customers that lose money", () => {
    // b: ₹100 charged, ₹850 AI cost.
    expect(fleet.totals.losing).toBe(1);
  });

  it("sorts biggest first, or least profit first", () => {
    expect(sortFleet(fleet.rows, "charged").map((r) => r.org.slug)).toEqual(["a", "b"]);
    expect(sortFleet(fleet.rows, "profit").map((r) => r.org.slug)).toEqual(["b", "a"]);
  });
});

describe("a customer missing from the usage read", () => {
  const fleet = fleetMoney([org("a", 100_000), org("gone", 50_000)], view([usage("a")]), NOW);

  it("is unknown, never zero", () => {
    expect(fleet.rows.find((r) => r.org.slug === "gone")?.money).toBeNull();
  });

  it("is left out of the totals, and the totals say so", () => {
    expect(fleet.totals.missing).toBe(1);
    expect(fleet.totals.estimated).toBe(true);
    expect(totalsHow(fleet.totals, 2)).toContain("1 customer is missing from the usage read");
  });

  it("sorts last", () => {
    expect(sortFleet(fleet.rows, "profit").at(-1)?.org.slug).toBe("gone");
  });
});

describe("a failed usage read", () => {
  it("leaves every total unknown, never ₹0", () => {
    const fleet = fleetMoney([org("a", 100_000)], null, NOW);
    expect(fleet.totals.charged).toBeNull();
    expect(fleet.totals.aiCost).toBeNull();
    expect(fleet.totals.profit).toBeNull();
    expect(fleet.totals.missing).toBe(1);
  });
});

describe("no credit price", () => {
  it("cannot total the money, and says the price was not saved", () => {
    const fleet = fleetMoney(
      [org("a", 100_000)],
      view([usage("a")], { inrPerCredit: null, usdToInr: null }),
      NOW,
    );
    expect(fleet.price).toBeNull();
    expect(fleet.priceReported).toBe(true);
    expect(fleet.totals.aiCost).toBeNull();
    expect(fleet.totals.profit).toBeNull();
  });

  it("tells an old Console (no price field) apart from an unsaved price", () => {
    const old = view([usage("a")]);
    delete old.inrPerCredit;
    delete old.usdToInr;
    expect(fleetMoney([org("a", 0)], old, NOW).priceReported).toBe(false);
  });
});
