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

  it("parses assumptions strictly", () => {
    expect(parseAssumption(" 90 ")).toBe(90);
    expect(parseAssumption("")).toBeNull();
    expect(parseAssumption("0")).toBeNull();
    expect(parseAssumption("-5")).toBeNull();
    expect(parseAssumption("ninety")).toBeNull();
  });
});

describe("the assumptions are stored NOWHERE", () => {
  it("🔴 neither this module nor the rate panel persists them", () => {
    // An invented exchange rate that SURVIVES becomes a fact. State only:
    // gone on reload, asserted on the page as "used only here".
    const HERE = join(__dirname);
    const pricing = readFileSync(join(HERE, "pricing.ts"), "utf8");
    const panel = readFileSync(
      join(HERE, "..", "app", "tiers", "TierPricing.tsx"), "utf8");
    for (const src of [pricing, panel]) {
      expect(src).not.toContain("localStorage");
      expect(src).not.toContain("sessionStorage");
      expect(src).not.toContain("document.cookie");
    }
    // And no fetch carries them: the POST body is built from named rate
    // fields, so the assumption names never appear near a body build.
    expect(panel).not.toMatch(/body:[\s\S]{0,400}inrPer/);
  });
});
