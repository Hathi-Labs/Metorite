// WS-50 slice 1 — the money model (D94). The fence for `lib/money.ts`.
//
// The failure modes these tests exist for:
//   1. A FREE credit counted as revenue. The old realised margin did this.
//   2. An estimate shown as a fact. Every estimated figure must say so.
//   3. A missing credit price drawn as ₹0. Unknown is `null`, never zero.
//   4. A number with no explanation. Every figure carries its arithmetic.

import { describe, expect, it } from "vitest";

import {
  aiMarginOf,
  customerMoney,
  daysLeft,
  daysLeftLabel,
  formatInr,
  formatPct,
  priceFrom,
  rowMoney,
  type MoneyInput,
} from "./money";
import type { OrgUsageRow } from "./usage";

const NOW = new Date("2026-10-09T00:00:00Z");
const PRICE = { inrPerCredit: 1, inrPerUsd: 85 };

function row(over: Partial<OrgUsageRow> = {}): OrgUsageRow {
  return {
    slug: "acme",
    name: "Acme",
    calls: 100,
    credits: "1000",
    members: 3,
    costUsd: "10",
    balance: "4000",
    lastSeen: null,
    marginRatio: null,
    runwayDays: 20,
    silent: false,
    refusals: 0,
    unbilledCalls: 0,
    unbilledTokens: 0,
    paidCredits: "1000",
    paidValueInr: "1200",
    unpricedPaidCredits: "0",
    freeCredits: "0",
    unbackedCredits: "0",
    lifePaidUsed: "5000",
    lifePaidValueInr: "6000",
    lifeFreeUsed: "0",
    creditsLast7Days: "1400",
    ...over,
  };
}

function input(over: Partial<MoneyInput> = {}): MoneyInput {
  return {
    row: row(),
    price: PRICE,
    seatsMonthlyInr: 5000,
    seatsBought: 10,
    windowDays: 30,
    drawsSince: "2026-09-01T00:00:00Z",
    now: NOW,
    ...over,
  };
}

describe("customerMoney — the exact case", () => {
  const m = customerMoney(input());

  it("charges seats plus bought credits at the price each lot was sold at", () => {
    expect(m.seats.value).toBe(5000);
    expect(m.paidCredits.value).toBe(1200);
    expect(m.charged.value).toBe(6200);
    expect(m.charged.estimated).toBe(false);
  });

  it("states the AI cost in rupees at the planning rate", () => {
    expect(m.aiCost.value).toBe(850);
    expect(m.aiCost.how).toContain("$10.00");
    expect(m.aiCost.how).toContain("₹85");
  });

  it("profit is charged minus AI cost, and margin is profit over charged", () => {
    expect(m.profit.value).toBe(5350);
    expect(m.margin.value).toBeCloseTo(5350 / 6200);
  });

  it("explains each figure with this customer's own numbers", () => {
    expect(m.charged.how).toBe("Seats ₹5,000 + bought credits used ₹1,200 = ₹6,200.");
    expect(m.profit.how).toBe("₹6,200 we charged − ₹850 AI cost = ₹5,350.");
    expect(m.seats.how).toContain("10 seats × ₹500 a month");
  });

  it("has no estimate notes when every credit is traced", () => {
    expect(m.estimateNotes).toEqual([]);
  });
});

describe("D94 rule 1 — a free credit is not revenue", () => {
  const m = customerMoney(
    input({ row: row({ paidCredits: "600", paidValueInr: "720", freeCredits: "400" }) }),
  );

  it("counts only the bought credits", () => {
    expect(m.paidCredits.value).toBe(720);
    expect(m.charged.value).toBe(5720);
  });

  it("puts the free share of the AI cost under Given away", () => {
    // 400 of 1,000 credits were free: 40 percent of ₹850.
    expect(m.givenAway.value).toBeCloseTo(340);
    expect(m.givenAway.how).toContain("400 of the 1,000 credits used were free");
  });
});

describe("D94 rule 4 — an estimate is labelled", () => {
  it("splits credits spent before draws began by the lifetime mix", () => {
    const m = customerMoney(
      input({
        row: row({
          paidCredits: "0",
          paidValueInr: "0",
          lifePaidUsed: "750",
          lifePaidValueInr: "900",
          lifeFreeUsed: "250",
        }),
      }),
    );
    // 1,000 untraced credits, 75 percent bought at ₹1.20 each.
    expect(m.paidCredits.value).toBeCloseTo(900);
    expect(m.paidCredits.estimated).toBe(true);
    expect(m.charged.estimated).toBe(true);
    expect(m.profit.estimated).toBe(true);
    expect(m.estimateNotes[0]).toContain("75% bought, 25% free");
    // The free 25 percent shows up as Given away.
    expect(m.givenAway.value).toBeCloseTo(850 * 0.25);
  });

  it("with no lot history at all, counts untraced credits as bought at the current price", () => {
    const m = customerMoney(
      input({
        row: row({
          paidCredits: undefined,
          paidValueInr: undefined,
          lifePaidUsed: undefined,
          lifePaidValueInr: undefined,
          lifeFreeUsed: undefined,
        }),
      }),
    );
    expect(m.paidCredits.value).toBe(1000);
    expect(m.paidCredits.estimated).toBe(true);
    expect(m.estimateNotes[0]).toContain("no lot records whether they were bought or given");
  });

  it("values a bought lot with no recorded price at the current credit price, and says so", () => {
    const m = customerMoney(
      input({
        row: row({ paidCredits: "1000", paidValueInr: "0", unpricedPaidCredits: "1000" }),
        price: { inrPerCredit: 1.5, inrPerUsd: 85 },
      }),
    );
    expect(m.paidCredits.value).toBe(1500);
    expect(m.paidCredits.estimated).toBe(true);
    expect(m.estimateNotes.some((n) => n.includes("no rupee amount recorded"))).toBe(true);
  });
});

describe("no credit price — unknown is null, never zero", () => {
  const m = customerMoney(
    input({ price: null, row: row({ unpricedPaidCredits: "10" }) }),
  );

  it("cannot state the AI cost, the profit or the margin in rupees", () => {
    expect(m.aiCost.value).toBeNull();
    expect(m.profit.value).toBeNull();
    expect(m.margin.value).toBeNull();
    expect(m.paidCredits.value).toBeNull();
    expect(m.aiCost.how).toContain("Save the credit price");
  });

  it("still states seats, which do not need it", () => {
    expect(m.seats.value).toBe(5000);
  });

  it("draws null as a dash", () => {
    expect(formatInr(null)).toBe("—");
    expect(formatPct(null)).toBe("—");
  });

  it("a fully traced, fully priced window needs no credit price for revenue", () => {
    const exact = customerMoney(input({ price: null }));
    expect(exact.paidCredits.value).toBe(1200);
    expect(exact.aiCost.value).toBeNull();
  });
});

describe("owed — credits spent below zero", () => {
  it("is nothing once a top-up brings the balance back above zero", () => {
    // Went to −20 in the window, then bought 100: balance 80, owes nothing.
    const m = customerMoney(input({ row: row({ balance: "80", unbackedCredits: "20" }) }));
    expect(m.owed.value).toBe(0);
  });

  it("is shown apart from We charged", () => {
    const m = customerMoney(
      input({ row: row({ balance: "-250", unbackedCredits: "250", runwayDays: 0 }) }),
    );
    expect(m.owed.value).toBe(250);
    expect(m.owed.how).toContain("they owe ₹250");
    expect(m.charged.value).toBe(6200);
    expect(m.daysLeft.state).toBe("owing");
    expect(daysLeftLabel(m.daysLeft)).toBe("below zero");
  });
});

describe("days left", () => {
  it("shows the arithmetic and the date the credits run out", () => {
    const d = daysLeft(row(), NOW);
    expect(d.perDay).toBe(200);
    expect(d.how).toBe(
      "4,000 credits left ÷ 200 credits a day (the average of the last 7 days) = about 20 days. " +
        "At this rate the credits run out around 29 Oct.",
    );
    expect(daysLeftLabel(d)).toBe("≈ 20 days");
  });

  it("says there is nothing to project from when there was no recent use", () => {
    const d = daysLeft(row({ runwayDays: null, creditsLast7Days: "0" }), NOW);
    expect(d.state).toBe("idle");
    expect(daysLeftLabel(d)).toBe("no recent use");
  });
});

describe("rowMoney — one app or person", () => {
  it("values a row at the customer's average earned per credit, and costs it exactly", () => {
    const whole = customerMoney(input());
    const r = rowMoney({ credits: "100", costUsd: "1" }, whole, PRICE);
    expect(r.charged).toBeCloseTo(120);
    expect(r.aiCost).toBe(85);
    expect(r.margin).toBeCloseTo(35 / 120);
  });
});

describe("priceFrom", () => {
  it("needs both rates, and both above zero", () => {
    expect(priceFrom("1.2", "85")).toEqual({ inrPerCredit: 1.2, inrPerUsd: 85 });
    expect(priceFrom(null, "85")).toBeNull();
    expect(priceFrom("1", "0")).toBeNull();
  });
});

describe("formatInr", () => {
  it("rounds to whole rupees at ₹100 and keeps paise below", () => {
    expect(formatInr(123456.7)).toBe("₹1,23,457");
    expect(formatInr(0.43)).toBe("₹0.43");
    expect(formatInr(-50)).toBe("−₹50.00");
  });
});

describe("partial vendor cost coverage", () => {
  it("flags the AI cost, the profit and the margin when some calls carry no cost", () => {
    const m = customerMoney(input({ row: row({ costedShare: "0.1" }) }));
    expect(m.aiCost.estimated).toBe(true);
    expect(m.profit.estimated).toBe(true);
    expect(m.margin.estimated).toBe(true);
    expect(m.estimateNotes.some((n) => n.includes("Only 10% of this customer's calls"))).toBe(true);
  });

  it("does not flag full coverage", () => {
    const m = customerMoney(input({ row: row({ costedShare: "1" }) }));
    expect(m.aiCost.estimated).toBe(false);
  });
});

describe("the lifetime average leaves unpriced bought credits out", () => {
  it("does not let a big grant with no price drag the value toward zero", () => {
    // 900 unpriced + 100 bought at ₹1 each. The priced average is ₹1, not ₹0.10.
    const m = customerMoney(
      input({
        row: row({
          paidCredits: "0",
          paidValueInr: "0",
          lifePaidUsed: "1000",
          lifeUnpricedPaidUsed: "900",
          lifePaidValueInr: "100",
          lifeFreeUsed: "0",
        }),
      }),
    );
    expect(m.paidCredits.value).toBeCloseTo(1000);
  });
});

describe("aiMarginOf — AI alone, seats left out", () => {
  it("is bought credits used against the AI cost of the paid calls", () => {
    const m = customerMoney(input());
    // ₹1,200 of bought credits, ₹850 of AI cost, nothing free.
    expect(aiMarginOf(m)).toBeCloseTo(350 / 1200);
    // The overall margin, with ₹5,000 of seats, reads far higher.
    expect(m.margin.value).toBeGreaterThan(0.8);
  });

  it("does not fall when a customer leans on free credits", () => {
    const m = customerMoney(
      input({ row: row({ paidCredits: "600", paidValueInr: "720", freeCredits: "400" }) }),
    );
    // ₹850 of AI cost, 40% on free credits: ₹510 paid cost against ₹720.
    expect(aiMarginOf(m)).toBeCloseTo((720 - 510) / 720);
  });

  it("is null with no credit price", () => {
    expect(aiMarginOf(customerMoney(input({ price: null, row: row({ unpricedPaidCredits: "1" }) })))).toBeNull();
  });
});
