// The pricing arithmetic — and the honesty rules that outrank it.
//
// 🔴 A wrong margin is renegotiated on, so the refusals matter more than the
// division. Every "cannot say" case must answer null, never zero and never a
// guess.

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import {
  type Assumptions,
  chargeForMargin,
  creditsPerUnitFromUsd,
  marginFraction,
  marginLabelPct,
  parseAssumption,
  priceForMargin,
  roundCredits,
  vendorCostCreditsPer1k,
} from "./pricing";

// 1 credit = ₹1, $1 = ₹90. Round numbers so a reader can check by eye.
const A: Assumptions = { inrPerCredit: 1, inrPerUsd: 90 };
const NONE: Assumptions = { inrPerCredit: null, inrPerUsd: null };

describe("what 1k tokens cost us, in credits", () => {
  it("converts through both assumptions", () => {
    // $3/1M = $0.003/1k = ₹0.27/1k = 0.27 credits/1k at ₹1 per credit.
    expect(vendorCostCreditsPer1k(3, A)).toBeCloseTo(0.27, 10);
  });

  it("🔴 answers null without assumptions, never a guess", () => {
    expect(vendorCostCreditsPer1k(3, NONE)).toBeNull();
    expect(vendorCostCreditsPer1k(3, { inrPerCredit: 1, inrPerUsd: null }))
      .toBeNull();
  });

  it("answers null for a missing or negative vendor price", () => {
    expect(vendorCostCreditsPer1k(null, A)).toBeNull();
    expect(vendorCostCreditsPer1k(-1, A)).toBeNull();
  });
});

describe("the margin on a price", () => {
  it("reads as the share of the charge that is ours", () => {
    // Cost 0.27, charge 1 credit/1k → 73% margin.
    expect(marginFraction(1, 3, A)).toBeCloseTo(0.73, 10);
  });

  it("goes NEGATIVE when we sell below cost, loudly", () => {
    const m = marginFraction(0.1, 3, A);
    expect(m).not.toBeNull();
    expect(m as number).toBeLessThan(0);
  });

  it("🔴 refuses a margin on a zero or missing charge", () => {
    // A zero charge divides by zero; "-∞%" helps nobody decide anything.
    expect(marginFraction(0, 3, A)).toBeNull();
    expect(marginFraction(null, 3, A)).toBeNull();
  });

  it("refuses when the vendor price is unknown", () => {
    expect(marginFraction(1, null, A)).toBeNull();
  });
});

describe("a NON-token unit's cost, in credits", () => {
  it("converts a per-image dollar price through both assumptions", () => {
    // $0.04/image at Rs 90/$ and Rs 1/credit = 3.6 credits per image.
    expect(creditsPerUnitFromUsd(0.04, A)).toBeCloseTo(3.6, 10);
  });

  it("🔴 refuses to guess without assumptions or with garbage", () => {
    expect(creditsPerUnitFromUsd(0.04, NONE)).toBeNull();
    expect(creditsPerUnitFromUsd(null, A)).toBeNull();
    expect(creditsPerUnitFromUsd(-1, A)).toBeNull();
    expect(creditsPerUnitFromUsd(Number.POSITIVE_INFINITY, A)).toBeNull();
  });
});

describe("the one margin formula", () => {
  it("charge = cost / (1 - margin), in whatever unit the cost came in", () => {
    expect(chargeForMargin(3.6, 0.7)).toBeCloseTo(12, 10);
    expect(chargeForMargin(0.27, 0.7)).toBeCloseTo(0.9, 10);
  });

  it("refuses an impossible target or a missing cost", () => {
    expect(chargeForMargin(null, 0.7)).toBeNull();
    expect(chargeForMargin(3.6, 1)).toBeNull();
    expect(chargeForMargin(3.6, -0.1)).toBeNull();
  });

  it("the token path IS this formula — no second copy to drift", () => {
    expect(priceForMargin(3, A, 0.7)).toBe(
      chargeForMargin(vendorCostCreditsPer1k(3, A), 0.7),
    );
  });
});

describe("the suggested price for a target margin", () => {
  it("inverts the margin formula", () => {
    // Cost 0.27, target 70% → charge 0.9.
    expect(priceForMargin(3, A, 0.7)).toBeCloseTo(0.9, 10);
    // And the round trip agrees with marginFraction.
    expect(marginFraction(0.9, 3, A)).toBeCloseTo(0.7, 10);
  });

  it("refuses a target of 100% or more — that price is infinite", () => {
    expect(priceForMargin(3, A, 1)).toBeNull();
    expect(priceForMargin(3, A, 1.5)).toBeNull();
  });
});

describe("rendering", () => {
  it("labels an unknown margin as a dash, never 0%", () => {
    expect(marginLabelPct(null)).toBe("—");
    expect(marginLabelPct(0.6)).toBe("60%");
  });

  it("🔴 keeps a DeepSeek-class suggestion above zero", () => {
    // $0.27/1M at the test assumptions is 0.0243 credits/1k. Fixed 2-decimal
    // rounding would print 0.02; fixed 0 decimals would print "0", which
    // reads as "free is fine".
    const s = roundCredits(priceForMargin(0.27, A, 0.7));
    expect(Number(s)).toBeGreaterThan(0);
  });

  it("🔴 a whole-number price keeps every digit (1000 is not 1)", () => {
    // Shipped bug: toPrecision(4) + a dot-optional zero-strip regex turned
    // "1000" into "1" — a 100× underprice, one Apply click from billing.
    expect(roundCredits(1000)).toBe("1000");
    expect(roundCredits(8800)).toBe("8800");
    expect(roundCredits(1200)).toBe("1200");
    expect(roundCredits(999.96)).toBe("1000");
  });

  it("never ships exponent notation, at either end", () => {
    expect(roundCredits(10000)).toBe("10000");
    expect(roundCredits(12340)).toBe("12340");
    expect(roundCredits(0.0000008)).toBe("0.0000008");
  });

  it("no string at all for nothing, zero, or fiction", () => {
    expect(roundCredits(null)).toBe("");
    expect(roundCredits(0)).toBe("");
    expect(roundCredits(-3)).toBe("");
    expect(roundCredits(Number.NaN)).toBe("");
    expect(roundCredits(1e16)).toBe("");
  });

  it("parses assumptions strictly", () => {
    expect(parseAssumption(" 90 ")).toBe(90);
    expect(parseAssumption("")).toBeNull();
    expect(parseAssumption("0")).toBeNull();
    expect(parseAssumption("-5")).toBeNull();
    expect(parseAssumption("ninety")).toBeNull();
  });
});

describe("assumptions are the SAVED frame, stored by no surface", () => {
  it("🔴 neither this module nor the hand form persists or retypes them", () => {
    // The consolidation (owner read, 2026-08-30): the hand form lost its
    // what-if boxes, so the saved credit price is the ONE frame — no
    // local copy to disagree with it, nothing persisted from a page.
    const HERE = join(__dirname);
    const pricing = readFileSync(join(HERE, "pricing.ts"), "utf8");
    const panel = readFileSync(
      join(HERE, "..", "app", "pricing", "TierPricing.tsx"), "utf8");
    for (const src of [pricing, panel]) {
      expect(src).not.toContain("localStorage");
      expect(src).not.toContain("sessionStorage");
      expect(src).not.toContain("document.cookie");
    }
    expect(panel).toContain("savedAssumptions(");
    expect(panel).not.toContain("setInrPerCredit");
    expect(panel).not.toMatch(/body:[\s\S]{0,400}inrPer/);
  });
});

describe("the /pricing page wiring", () => {
  const HERE = join(__dirname);
  const read = (p: string) => readFileSync(join(HERE, p), "utf8");

  it("🔴 /pricing mounts the cockpit, and /tiers no longer does", () => {
    // The move is only real if the old page LOST the panel. Two mounts
    // would be two places to set a price, and they would drift.
    expect(read("../app/pricing/page.tsx")).toContain("<TierPricing");
    const tiersPage = read("../app/tiers/page.tsx");
    expect(tiersPage).not.toContain("TierPricing");
    // The board still tells the operator where prices went.
    expect(tiersPage).toContain("/pricing");
  });

  it("the sidebar reaches it", () => {
    const header = read("../app/Header.tsx");
    expect(header).toContain('href: "/pricing"');
    expect(header).toContain('label: "Pricing"');
  });

  it("the hand form carries no second price table and no what-if boxes", () => {
    // The price list and the method board own reading; this panel only
    // WRITES. A second table or a second pair of ₹ boxes is the echo the
    // owner flagged ("is this section repeated?").
    const panel = read("../app/pricing/TierPricing.tsx");
    expect(panel).not.toContain("Show the prices");
    expect(panel).not.toContain("assumptions");
  });

  it("🔴 a priced card refuses BLANK boxes — 0 is typed, never assumed", () => {
    // The form once coerced every blank to "0", so a skipped cached box
    // billed cache hits FREE. Unknown never bills as free; free is typed.
    const panel = read("../app/pricing/TierPricing.tsx");
    expect(panel).toContain("a blank box is not a decision");
    expect(panel.indexOf("a blank box is not a decision"))
      .toBeLessThan(panel.indexOf("await fetch("));
  });

  it("the hand form refreshes the page it claims to change", () => {
    // "The card takes effect now" while the price list above still showed
    // the old card was a lie of staleness.
    const panel = read("../app/pricing/TierPricing.tsx");
    expect(panel).toContain("if (res.ok) router.refresh();");
  });

  it("🔴 the credit price panel sits ABOVE the cockpit it seeds (017)", () => {
    const page = read("../app/pricing/page.tsx");
    expect(page).toContain("<CreditPrice");
    expect(page.indexOf("<CreditPrice")).toBeLessThan(
      page.indexOf("<TierPricing"));
  });

  it("the hand form's hints read the SAVED price, nothing local", () => {
    const panel = read("../app/pricing/TierPricing.tsx");
    expect(panel).toContain("savedAssumptions(creditPrice)");
  });
});
