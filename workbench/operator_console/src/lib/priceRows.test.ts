// The fence for the pricing board's judgements.
//
// ⚠️ **The scale tests are the point of this file.** A factor of 1000 loose in
// a pricing surface ships a price 1000x wrong, and it reads as plausible at
// both scales. `rowCost` and `suggestFor` each get an arithmetic test with
// numbers a person can check by hand.

import { describe, expect, it } from "vitest";

import type { AiCatalog, CatalogModel, Tier, TierMargin, TierRate } from "./contract";
import { EMPTY_CATALOG } from "./contract";
import {
  PER_1M,
  floorOfPct,
  floorPctOf,
  marginPctOf,
  multiplierOfPct,
  perUnitVendorUsd,
  plannedMargin,
  priceState,
  pricingAlert,
  rowCost,
  suggestFor,
  tierPriceRows,
  type TierPriceRow,
} from "./priceRows";

// ₹1 per credit and ₹100 per dollar, so every figure below divides cleanly
// and a reader can check the arithmetic without a calculator.
const A = { inrPerCredit: 1, inrPerUsd: 100 };

function model(over: Partial<CatalogModel> = {}): CatalogModel {
  return {
    id: "deepseek/deepseek-chat",
    provider: "deepseek",
    inputPer1M: 10,
    outputPer1M: 20,
    cachedInputPer1M: 5,
    perMinuteUsd: null,
    perCharacterUsd: null,
    perImageUsd: null,
  } as unknown as CatalogModel;
}

function tier(over: Partial<Tier> = {}): Tier {
  return {
    slug: "tier-fast",
    label: "Fast",
    blurb: "",
    registered: true,
    task: "chat",
    jobs: [{ task: "chat", chain: [{ model: "deepseek/deepseek-chat", rank: 1 }] }],
    ...over,
  } as unknown as Tier;
}

function row(over: Partial<TierPriceRow> = {}): TierPriceRow {
  return {
    tier: tier(),
    task: "chat",
    unit: "tokens",
    tokenPriced: true,
    primary: model(),
    primaryId: "deepseek/deepseek-chat",
    rate: null,
    margin: null,
    ...over,
  };
}

describe("rowCost", () => {
  it("returns credits PER MILLION, not per thousand", () => {
    // $10 per 1M input × ₹100/$ = ₹1000 per 1M ÷ ₹1 per credit = 1000 credits.
    const c = rowCost(row(), A);
    expect(c.input).toBe(1000);
    expect(c.output).toBe(2000);
  });

  it("is a thousand times the per-thousand figure, by construction", () => {
    const c = rowCost(row(), A);
    // The per-1k cost of $10/1M at these assumptions is 1 credit.
    expect(c.input).toBe(1 * PER_1M);
  });

  it("answers null for an unbound tier rather than zero", () => {
    expect(rowCost(row({ primary: null, primaryId: null }), A).input).toBeNull();
  });

  it("answers null with no saved credit price", () => {
    expect(rowCost(row(), null).input).toBeNull();
  });

  it("reads the per-unit column for a non-token task, unscaled", () => {
    const r = row({
      task: "transcribe",
      unit: "minutes",
      tokenPriced: false,
      primary: model({ perMinuteUsd: 0.006 } as Partial<CatalogModel>),
    });
    // The profile column is already per minute, so nothing divides by 1000.
    expect(rowCost({ ...r, primary: { ...model(), perMinuteUsd: 0.006 } as CatalogModel }, A).input)
      .toBeCloseTo(0.6, 10);
  });
});

describe("perUnitVendorUsd", () => {
  it("picks the column the task prices in", () => {
    const m = { ...model(), perMinuteUsd: 1, perCharacterUsd: 2, perImageUsd: 3 } as CatalogModel;
    expect(perUnitVendorUsd("transcribe", m)).toBe(1);
    expect(perUnitVendorUsd("speak", m)).toBe(2);
    expect(perUnitVendorUsd("image", m)).toBe(3);
  });

  it("answers null for a task we hold no cost source for", () => {
    const m = { ...model(), perMinuteUsd: 1 } as CatalogModel;
    expect(perUnitVendorUsd("video", m)).toBeNull();
    expect(perUnitVendorUsd("music", m)).toBeNull();
  });
});

describe("suggestFor", () => {
  it("prices at cost ÷ (1 − margin), per million", () => {
    // Cost 1000 in / 2000 out per 1M. At a 50% margin: 2000 in / 4000 out.
    const s = suggestFor(row(), A, 0.5);
    expect(s).not.toBeNull();
    expect(Number(s!.input)).toBeCloseTo(2000, 6);
    expect(Number(s!.output)).toBeCloseTo(4000, 6);
  });

  it("charges the FULL input rate for cached tokens when none is recorded", () => {
    const m = { ...model(), cachedInputPer1M: null } as CatalogModel;
    const s = suggestFor(row({ primary: m }), A, 0.5);
    expect(s!.cached).toBe(s!.input);
  });

  it("bills a vendor-stated zero cached price as zero", () => {
    const m = { ...model(), cachedInputPer1M: 0 } as CatalogModel;
    const s = suggestFor(row({ primary: m }), A, 0.5);
    expect(s!.cached).toBe("0");
  });

  it("refuses a suggestion when either leg is unknown", () => {
    const m = { ...model(), outputPer1M: null } as CatalogModel;
    expect(suggestFor(row({ primary: m }), A, 0.5)).toBeNull();
  });

  it("refuses a suggestion with no margin typed", () => {
    expect(suggestFor(row(), A, null)).toBeNull();
  });

  it("refuses a suggestion with no saved credit price", () => {
    expect(suggestFor(row(), null, 0.5)).toBeNull();
  });
});

describe("plannedMargin", () => {
  it("reads the margin the current price leaves", () => {
    const rate = { mode: "priced", inputPer1m: "2000", unit: "tokens" } as TierRate;
    // Cost 1000, charge 2000 → half of what the customer pays is ours.
    expect(plannedMargin(row({ rate }), A)).toBeCloseTo(0.5, 10);
  });

  it("is null for an absorbed or unpriced card, never zero", () => {
    expect(plannedMargin(row({ rate: { mode: "absorbed" } as TierRate }), A)).toBeNull();
    expect(plannedMargin(row({ rate: { mode: "unpriced" } as TierRate }), A)).toBeNull();
    expect(plannedMargin(row({ rate: null }), A)).toBeNull();
  });
});

describe("priceState", () => {
  it("names unbound before unpriced — a tier with no model cannot be priced", () => {
    expect(priceState(row({ primaryId: null, primary: null }))).toBe("unbound");
  });

  it("separates deliberately free from nobody-set-this", () => {
    expect(priceState(row({ rate: { mode: "absorbed" } as TierRate }))).toBe("absorbed");
    expect(priceState(row({ rate: { mode: "unpriced" } as TierRate }))).toBe("unpriced");
    expect(priceState(row({ rate: null }))).toBe("unpriced");
    expect(priceState(row({ rate: { mode: "priced" } as TierRate }))).toBe("priced");
  });
});

describe("tierPriceRows", () => {
  const cat = (over: Partial<AiCatalog>): AiCatalog => ({
    ...EMPTY_CATALOG,
    tasks: [
      { slug: "chat", label: "Chat", natural_unit: "tokens" },
      { slug: "image", label: "Image", natural_unit: "images" },
    ],
    ...over,
  });

  it("drops a ghost tier, which the Console refuses to price anyway", () => {
    const groups = tierPriceRows(
      cat({ tiers: [tier({ registered: false })] }),
    );
    expect(groups).toEqual([]);
  });

  it("drops an uncategorised tier", () => {
    const groups = tierPriceRows(cat({ tiers: [tier({ task: null })] }));
    expect(groups).toEqual([]);
  });

  it("splits chat from the capability tiers, like the tier board", () => {
    const groups = tierPriceRows(
      cat({
        tiers: [
          tier(),
          tier({ slug: "tier-image", label: "Image", task: "image", jobs: [] }),
        ],
      }),
    );
    expect(groups.map((g) => g.title)).toEqual([
      "Chat — the quality bands",
      "One tier per capability",
    ]);
  });

  it("takes the FIRST model in the chain as the cost yardstick", () => {
    const t = tier({
      jobs: [
        {
          task: "chat",
          chain: [
            { model: "second", rank: 2 },
            { model: "first", rank: 1 },
          ],
        },
      ],
    } as Partial<Tier>);
    const groups = tierPriceRows(cat({ tiers: [t] }));
    expect(groups[0].rows[0].primaryId).toBe("first");
  });

  it("joins the rate and the margin onto the row", () => {
    const rate = { tier: "tier-fast", task: "chat", mode: "priced" } as TierRate;
    const margin = { tier: "tier-fast", realisedMargin: "0.4" } as TierMargin;
    const groups = tierPriceRows(
      cat({ tiers: [tier()], tierRates: [rate], tierMargins: [margin] }),
    );
    expect(groups[0].rows[0].rate).toBe(rate);
    expect(groups[0].rows[0].margin).toBe(margin);
  });
});

describe("pricingAlert", () => {
  const price = { inrPerCredit: "1", usdToInr: "100", effectiveFrom: null };
  const g = (rows: TierPriceRow[]) => [{ rows }];

  it("names the credit price above everything, because it blocks everything", () => {
    const a = pricingAlert(g([row()]), null);
    expect(a.tone).toBe("danger");
    expect(a.title).toMatch(/credit is worth/i);
  });

  it("ranks a tier under its floor above an unpriced tier", () => {
    const under = row({
      rate: { mode: "priced" } as TierRate,
      margin: { marginFloor: "0.5", realisedMargin: "0.2" } as TierMargin,
    });
    const a = pricingAlert(g([under, row()]), price);
    expect(a.tone).toBe("danger");
    expect(a.title).toMatch(/less than its floor/);
  });

  it("does not alarm on a tier with no floor", () => {
    const noFloor = row({
      rate: { mode: "priced" } as TierRate,
      margin: { marginFloor: null, realisedMargin: "0.01" } as TierMargin,
    });
    expect(pricingAlert(g([noFloor]), price).tone).toBe("ok");
  });

  it("does not alarm on a floor with no realised margin", () => {
    const noReal = row({
      rate: { mode: "priced" } as TierRate,
      margin: { marginFloor: "0.5", realisedMargin: null } as TierMargin,
    });
    expect(pricingAlert(g([noReal]), price).tone).toBe("ok");
  });

  it("does NOT count an unbound tier as one that bills nothing", () => {
    // An unbound tier cannot answer a customer at all, so saying it "answers
    // customers and bills nothing" claims work that is not happening.
    const a = pricingAlert(g([row(), row({ primaryId: null, primary: null })]), price);
    expect(a.tone).toBe("warn");
    expect(a.title).toBe("1 tier answers customers and bills nothing");
    expect(a.detail).toMatch(/1 more tier runs on no model at all/);
    expect(a.detail).toMatch(/Tiers & backups/);
  });

  it("warns about unbound tiers even when every servable tier is priced", () => {
    const priced = row({ rate: { mode: "priced" } as TierRate });
    const a = pricingAlert(g([priced, row({ primaryId: null, primary: null })]), price);
    expect(a.tone).toBe("warn");
    expect(a.title).toBe("1 tier runs on no model");
  });

  it("uses the singular for one tier", () => {
    const a = pricingAlert(g([row()]), price);
    expect(a.title).toBe("1 tier answers customers and bills nothing");
  });

  it("says so when everything is priced", () => {
    const priced = row({ rate: { mode: "priced" } as TierRate });
    expect(pricingAlert(g([priced]), price).tone).toBe("ok");
  });

  it("treats an absorbed tier as priced — free on purpose is a decision", () => {
    const free = row({ rate: { mode: "absorbed" } as TierRate });
    expect(pricingAlert(g([free]), price).tone).toBe("ok");
  });
});

describe("the margin's two units", () => {
  it("🔴 a round trip through percent and back is the identity", () => {
    // The conversion this repo is most likely to ship backwards. 60% kept
    // means charge 2.5x cost: 1 - 1/2.5 = 0.6. Checked both directions and
    // then round-tripped, because one direction alone can be wrong twice and
    // still agree with itself.
    for (const pct of ["0", "25", "50", "60", "75", "90", "99"]) {
      const mult = multiplierOfPct(pct);
      expect(mult).not.toBeNull();
      expect(marginPctOf(mult)).toBe(pct);
    }
  });

  it("converts the worked example the design document uses", () => {
    // 2.5 on Fast and 1.4 on Powerful are the document's own numbers.
    expect(marginPctOf("2.5")).toBe("60");
    expect(marginPctOf("1.4")).toBe("29");
    expect(Number(multiplierOfPct("60"))).toBeCloseTo(2.5, 10);
  });

  it("🔴 an EMPTY box is null, never a zero", () => {
    // Blank means "offer no suggestion" and "never alarm". A zero would be a
    // number somebody chose, and 0% kept means selling at exactly cost.
    expect(multiplierOfPct("")).toBeNull();
    expect(multiplierOfPct("   ")).toBeNull();
    expect(floorOfPct("")).toBeNull();
    // ...and 0 typed on purpose is NOT blank.
    expect(multiplierOfPct("0")).toBe("1");
    expect(floorOfPct("0")).toBe("0");
  });

  it("refuses 100 percent or more — that asks for an infinite price", () => {
    expect(multiplierOfPct("100")).toBeNull();
    expect(multiplierOfPct("150")).toBeNull();
    expect(floorOfPct("100")).toBeNull();
  });

  it("refuses a negative share", () => {
    expect(multiplierOfPct("-10")).toBeNull();
    expect(floorOfPct("-1")).toBeNull();
  });

  it("shows an unset margin as an empty box, not a zero", () => {
    expect(marginPctOf(null)).toBe("");
    expect(floorPctOf(null)).toBe("");
  });

  it("ignores a multiplier below 1, which can only come from a hand-written row", () => {
    // The column's CHECK refuses it, so it cannot arrive through the route.
    // Drawing it as a negative percent would be worse than drawing nothing.
    expect(marginPctOf("0.8")).toBe("");
  });

  it("round-trips a floor through percent and back", () => {
    for (const pct of ["0", "25", "45", "50", "99"]) {
      expect(floorPctOf(floorOfPct(pct))).toBe(pct);
    }
  });
});
