// The pricing method — and its honesty rules: no fake suggestion, no
// silent free tier, no second grouping vocabulary.

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import type { CatalogModel } from "./contract";
import type { Assumptions } from "./pricing";
import {
  boundJobs,
  inrLabel,
  inrRateLine,
  parseMarginPct,
  priceGroups,
  recordedVendorUsd,
  savedAssumptions,
  tokenSuggestion,
  unitSuggestion,
  vendorUsdBox,
} from "./priceboard";
import { SAMPLE_CATALOG } from "./sample";

// 1 credit = ₹1, $1 = ₹90 — the round numbers the pricing suite also uses.
const A: Assumptions = { inrPerCredit: 1, inrPerUsd: 90 };

describe("the bound jobs the method prices", () => {
  const jobs = boundJobs(SAMPLE_CATALOG);

  it("one row per bound (tier, job), primary = rank 1, unit = the task's", () => {
    const fast = jobs.find((j) => j.tier === "tier-fast");
    expect(fast).toBeDefined();
    expect(fast?.task).toBe("chat");
    expect(fast?.tokenPriced).toBe(true);
    // The chain's FIRST model, by rank — not by array order luck.
    expect(fast?.primary).toBeTruthy();
  });

  it("skips empty tiers — a suggestion needs a model to cost", () => {
    for (const j of jobs) {
      expect(j.primary).not.toBe("");
    }
    expect(jobs.some((j) => j.tier === "tier-video")).toBe(false);
  });
});

describe("the saved credit price as arithmetic", () => {
  it("parses the saved strings", () => {
    expect(
      savedAssumptions({ inrPerCredit: "2", usdToInr: "90", effectiveFrom: null }),
    ).toEqual({ inrPerCredit: 2, inrPerUsd: 90 });
  });

  it("🔴 null when unset or garbage — the method must not run on air", () => {
    expect(savedAssumptions(null)).toBeNull();
    expect(
      savedAssumptions({ inrPerCredit: "0", usdToInr: "90", effectiveFrom: null }),
    ).toBeNull();
    expect(
      savedAssumptions({ inrPerCredit: "x", usdToInr: "90", effectiveFrom: null }),
    ).toBeNull();
  });
});

describe("the margin knob", () => {
  it("reads whole percents inside 1–95", () => {
    expect(parseMarginPct("70")).toBeCloseTo(0.7, 10);
    expect(parseMarginPct(" 50 ")).toBeCloseTo(0.5, 10);
  });

  it("refuses nonsense — 0%, 100%, words", () => {
    expect(parseMarginPct("0")).toBeNull();
    expect(parseMarginPct("100")).toBeNull();
    expect(parseMarginPct("free")).toBeNull();
  });
});

describe("token suggestions", () => {
  const M = { inputPer1M: 3, outputPer1M: 15, cachedInputPer1M: 0.3 };

  it("prices all three legs at the margin", () => {
    // $3/1M → 0.27 cr/1k cost → 0.9 at 70%. Output ×5. Cached ÷10.
    expect(tokenSuggestion(M, A, 0.7)).toEqual({
      in1k: "0.9",
      out1k: "4.5",
      cached1k: "0.09",
    });
  });

  it("🔴 an unknown cached rate charges the FULL input rate, never zero", () => {
    const s = tokenSuggestion({ ...M, cachedInputPer1M: null }, A, 0.7);
    expect(s?.cached1k).toBe("0.9");
  });

  it("🔴 a costs-blind model gets NO suggestion, not a guess", () => {
    expect(tokenSuggestion({ ...M, inputPer1M: null }, A, 0.7)).toBeNull();
    expect(tokenSuggestion({ ...M, outputPer1M: null }, A, 0.7)).toBeNull();
  });
});

describe("per-unit suggestions", () => {
  it("derives from the typed vendor dollar price", () => {
    // $0.04/image → 3.6 credits cost → 12 at 70%.
    expect(unitSuggestion(0.04, A, 0.7)).toBe("12");
  });

  it("empty without a usable vendor price", () => {
    expect(unitSuggestion(null, A, 0.7)).toBe("");
    expect(unitSuggestion(0, A, 0.7)).toBe("");
  });
});

describe("the recorded per-unit vendor cost (H-78)", () => {
  const M = (over: Partial<CatalogModel> = {}): CatalogModel => ({
    id: "openai/whisper-1", label: "Whisper", provider: "openai",
    kinds: ["transcribe"], contextWindow: null, maxOutput: null,
    inputPer1M: null, outputPer1M: null, cachedInputPer1M: null,
    perMinuteUsd: null, perCharacterUsd: null, perImageUsd: null,
    description: "", declared: true, ...over,
  });

  it("🔴 one task, one column — and the units already agree", () => {
    // `task_catalog` (010) prices transcribe in MINUTES and the profile
    // column holds minutes, because the Console did the per-second to
    // per-minute conversion once, in the feed read. Nothing here converts.
    const m = M({ perMinuteUsd: 0.006, perCharacterUsd: 0.000015,
                  perImageUsd: 0.04 });
    expect(recordedVendorUsd("transcribe", m)).toBe(0.006);
    expect(recordedVendorUsd("speak", m)).toBe(0.000015);
    expect(recordedVendorUsd("image", m)).toBe(0.04);
  });

  it("no cost source at all for a task nothing prices per unit", () => {
    // `video` and `music` sit on the slate with no vendor data behind them.
    // A made-up number on the pricing board is worse than a blank box.
    const m = M({ perMinuteUsd: 0.006, perImageUsd: 0.04 });
    expect(recordedVendorUsd("video", m)).toBeNull();
    expect(recordedVendorUsd("music", m)).toBeNull();
    expect(recordedVendorUsd("chat", m)).toBeNull();
  });

  it("a model with no profile row has no recorded cost", () => {
    expect(recordedVendorUsd("image", undefined)).toBeNull();
    expect(recordedVendorUsd("image", M())).toBeNull();
  });

  it("the box opens on the recorded price and a typed one wins", () => {
    expect(vendorUsdBox(undefined, 0.04)).toBe("0.04");
    expect(vendorUsdBox(undefined, null)).toBe("");
    expect(vendorUsdBox("0.09", 0.04)).toBe("0.09");
  });

  it("🔴 a tiny recorded price fills the box as PLAIN DIGITS", () => {
    // `String(3e-7)` is "3e-7", which is not a number an operator can check
    // — and this box is POSTed back verbatim, so the notation would reach
    // the database too.
    expect(vendorUsdBox(undefined, 3e-7)).toBe("0.0000003");
    expect(vendorUsdBox(undefined, 3e-10)).toBe("0.0000000003");
  });

  it("🔴 a CLEARED box stays cleared", () => {
    // An operator who empties the box means "ignore the recorded price".
    // Re-filling it under their cursor makes the box impossible to empty.
    expect(vendorUsdBox("", 0.04)).toBe("");
  });
});

describe("the rupee labels on the price list", () => {
  const PRICE = { inrPerCredit: "2", usdToInr: "90", effectiveFrom: null };
  const TASKS = [
    { slug: "chat", label: "Chat", natural_unit: "1k tokens" },
    { slug: "image", label: "Image", natural_unit: "images" },
  ];

  it("converts a credit amount at the saved price", () => {
    expect(inrLabel("6", PRICE)).toBe("₹12");
    expect(inrLabel("6", null)).toBeNull();
  });

  it("writes a token card as in/out and a unit card per SINGULAR unit", () => {
    expect(
      inrRateLine(
        { tier: "t", task: "chat", unit: "tokens", mode: "priced",
          inputPer1k: "2", outputPer1k: "6", cachedInputPer1k: "0.5",
          creditsPerUnit: "0" },
        PRICE, TASKS,
      ),
    ).toBe("≈ ₹4 in / ₹12 out per 1k tokens");
    expect(
      inrRateLine(
        { tier: "t", task: "image", unit: "images", mode: "priced",
          inputPer1k: "0", outputPer1k: "0", cachedInputPer1k: "0",
          creditsPerUnit: "18" },
        PRICE, TASKS,
      ),
    ).toBe("≈ ₹36 per image");
  });

  it("says nothing for an unpriced or absorbed card", () => {
    expect(
      inrRateLine(
        { tier: "t", task: "chat", unit: "tokens", mode: "absorbed",
          inputPer1k: "0", outputPer1k: "0", cachedInputPer1k: "0",
          creditsPerUnit: "0" },
        PRICE, TASKS,
      ),
    ).toBeNull();
  });
});

describe("the price list's groups", () => {
  it("reuses the tier board's own three-way split — chat bands first", () => {
    const groups = priceGroups(SAMPLE_CATALOG);
    expect(groups[0].title).toContain("Chat");
    expect(groups[0].rows.every((r) => r.tier.task === "chat")).toBe(true);
    const caps = groups.find((g) => g.title.includes("capability"));
    expect(caps).toBeDefined();
    // Every registered categorised tier appears — empty ones included,
    // because the slate is the product surface.
    expect(caps?.rows.some((r) => r.tier.slug === "tier-video")).toBe(true);
  });
});

describe("the wiring", () => {
  const read = (p: string) => readFileSync(join(__dirname, p), "utf8");

  it("🔴 the page shows list → method → cockpit, under the credit price", () => {
    const page = read("../app/pricing/page.tsx");
    const order = [
      page.indexOf("<CreditPrice"),
      page.indexOf("<PriceList"),
      page.indexOf("<PriceFromCost"),
      page.indexOf("<TierPricing"),
    ];
    for (const at of order) expect(at).toBeGreaterThan(-1);
    expect([...order].sort((a, b) => a - b)).toEqual(order);
  });

  it("the method board runs ONLY on the saved credit price", () => {
    // No second set of assumption boxes: one saved frame, one method.
    const src = read("../app/pricing/PriceFromCost.tsx");
    expect(src).toContain("savedAssumptions(");
    expect(src).not.toContain("setInrPerCredit");
  });

  it("applying writes through the SAME route as the manual form", () => {
    const src = read("../app/pricing/PriceFromCost.tsx");
    expect(src).toContain('fetch("/api/operator/catalog/tier-rates"');
    expect(src.match(/fetch\(/g)).toHaveLength(1);
  });
});

describe("a $0-listed vendor price", () => {
  it("🔴 arms nothing: free is the hand form's decision, not the board's", () => {
    // charge = 0 ÷ (1 − m) = 0, and a "priced" card at 0 is the absorbed
    // decision in disguise. The board once rendered a blank-but-armed
    // Apply here and POSTed "" into a Decimal — a 422 nobody could read.
    expect(tokenSuggestion(
      { inputPer1M: 0, outputPer1M: 9, cachedInputPer1M: null }, A, 0.7,
    )).toBeNull();
    expect(tokenSuggestion(
      { inputPer1M: 3, outputPer1M: 0, cachedInputPer1M: null }, A, 0.7,
    )).toBeNull();
  });

  it("a cached leg LISTED at $0 prices 0 explicitly while in/out bill", () => {
    const s = tokenSuggestion(
      { inputPer1M: 3, outputPer1M: 15, cachedInputPer1M: 0 }, A, 0.7);
    expect(s).not.toBeNull();
    expect(s?.cached1k).toBe("0");
    expect(Number(s?.in1k)).toBeGreaterThan(0);
  });
});
