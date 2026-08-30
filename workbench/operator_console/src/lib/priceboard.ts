// The pricing METHOD's judgements (owner ask, 2026-08-30): from what the
// vendors charge US to what a customer pays, one margin knob, per tier.
//
// 🔴 **The method, in three sentences.** A credit has ONE rupee value
// (`credit_price`, saved above the boards). Each bound (tier, job) has a
// COST: the primary model's vendor price, converted to credits per natural
// unit. The charge is cost ÷ (1 − margin), rounded — expensive capabilities
// (image, video) come out expensive by construction, because their cost
// does.
//
// ⚠️ Pure functions only; `PriceList.tsx` and `PriceFromCost.tsx` render
// what these decide and `priceboard.test.ts` is the fence.
//
// ⚠️ **No fake suggestions.** A job whose vendor cost is unknown (a
// costs-blind model, or a per-image price the feed does not carry yet —
// H-78) gets an empty suggestion and says why, never a guessed number.

import { singular } from "./catalog";
import type { AiCatalog, CreditPrice, Task, Tier, TierRate } from "./contract";
import {
  type Assumptions,
  chargeForMargin,
  creditsPerUnitFromUsd,
  roundCredits,
  vendorCostCreditsPer1k,
} from "./pricing";

/** One bound (tier, job) — the unit the method prices. */
export type BoundJob = {
  tier: string;
  task: string;
  /** The task's natural unit ("1k tokens", "image", "minute"…). */
  unit: string;
  tokenPriced: boolean;
  /** The chain's first model — the cost yardstick (D67). */
  primary: string;
};

/** Every bound job on a registered, categorised tier, in board order. */
export function boundJobs(cat: AiCatalog): BoundJob[] {
  const unitOf = new Map(cat.tasks.map((t) => [t.slug, t.natural_unit]));
  const out: BoundJob[] = [];
  for (const tier of cat.tiers) {
    if (!tier.registered) continue;
    for (const job of tier.jobs) {
      if (job.chain.length === 0) continue;
      const unit = unitOf.get(job.task) ?? "tokens";
      out.push({
        tier: tier.slug,
        task: job.task,
        unit,
        tokenPriced: unit.includes("token"),
        primary: [...job.chain].sort((x, y) => x.rank - y.rank)[0].model,
      });
    }
  }
  return out;
}

/** The saved credit price as arithmetic-ready assumptions — or null, which
 *  means the method cannot run yet and the board says to save one first. */
export function savedAssumptions(price: CreditPrice | null): Assumptions | null {
  if (price === null) return null;
  const credit = Number(price.inrPerCredit);
  const fx = Number(price.usdToInr);
  if (!Number.isFinite(credit) || credit <= 0) return null;
  if (!Number.isFinite(fx) || fx <= 0) return null;
  return { inrPerCredit: credit, inrPerUsd: fx };
}

/** "70" → 0.7. Null for anything outside 1–95 — a 0% margin is a choice
 *  the manual form can still make, but the KNOB refuses nonsense. */
export function parseMarginPct(raw: string): number | null {
  const n = Number(raw.trim());
  if (!Number.isFinite(n) || n < 1 || n > 95) return null;
  return n / 100;
}

/** A token job's suggestion: the three legs, rounded, all-or-nothing on
 *  the in/out pair (a card with an input price and no output price bills
 *  lopsided and reads as an accident). */
export function tokenSuggestion(
  m: { inputPer1M: number | null; outputPer1M: number | null;
       cachedInputPer1M: number | null },
  a: Assumptions,
  margin: number,
): { in1k: string; out1k: string; cached1k: string } | null {
  const inC = chargeForMargin(vendorCostCreditsPer1k(m.inputPer1M, a), margin);
  const outC = chargeForMargin(vendorCostCreditsPer1k(m.outputPer1M, a), margin);
  if (inC === null || outC === null) return null;
  const cachedC = chargeForMargin(
    vendorCostCreditsPer1k(m.cachedInputPer1M, a), margin);
  return {
    in1k: roundCredits(inC),
    out1k: roundCredits(outC),
    // No cached price recorded → charge the full input rate for cached
    // tokens rather than 0: unknown must never bill as free.
    cached1k: cachedC === null ? roundCredits(inC) : roundCredits(cachedC),
  };
}

/** A per-unit job's suggestion from a typed vendor dollar price. */
export function unitSuggestion(
  usdPerUnit: number | null,
  a: Assumptions,
  margin: number,
): string {
  return roundCredits(chargeForMargin(creditsPerUnitFromUsd(usdPerUnit, a), margin));
}

// ── The price list (what a customer pays today, in plain words) ────────────

/** "2 in / 6 out per 1k" → "≈ ₹2 in / ₹6 out per 1k tokens" needs the saved
 *  credit price; this converts ONE credit amount to a rupee label. Null
 *  when there is no saved price or the amount does not parse. */
export function inrLabel(
  credits: string,
  price: CreditPrice | null,
): string | null {
  const a = savedAssumptions(price);
  const n = Number(credits);
  if (a === null || !Number.isFinite(n)) return null;
  const inr = n * (a.inrPerCredit as number);
  const rounded = inr >= 100 ? Math.round(inr).toString()
    : Number(inr.toPrecision(3)).toString();
  return `₹${rounded}`;
}

/** A priced card's rupee line — "≈ ₹2 in / ₹6 out per 1k", or "≈ ₹36 per
 *  image" — or null when no credit price is saved, the card is not priced,
 *  or a number fails to parse. The list then shows credits alone. */
export function inrRateLine(
  rate: TierRate,
  price: CreditPrice | null,
  tasks: Task[],
): string | null {
  if (rate.mode !== "priced") return null;
  if (rate.unit.includes("token")) {
    const inr = inrLabel(rate.inputPer1k, price);
    const out = inrLabel(rate.outputPer1k, price);
    if (inr === null || out === null) return null;
    return `≈ ${inr} in / ${out} out per 1k tokens`;
  }
  const per = inrLabel(rate.creditsPerUnit, price);
  if (per === null) return null;
  const unit = tasks.find((t) => t.slug === rate.task)?.natural_unit ?? rate.unit;
  return `≈ ${per} per ${singular(unit)}`;
}

export type PriceRow = {
  tier: Tier;
  rate: TierRate | null;
};

/** The list's grouping — the tier board's own three-way split, reused so
 *  the two pages never disagree about what a tier IS. */
export function priceGroups(cat: AiCatalog): {
  title: string;
  rows: PriceRow[];
}[] {
  const rateOf = new Map(cat.tierRates.map((r) => [`${r.tier}::${r.task}`, r]));
  const row = (t: Tier): PriceRow => ({
    tier: t,
    rate: t.task ? (rateOf.get(`${t.slug}::${t.task}`) ?? null) : null,
  });
  const groups = [
    {
      title: "Chat — the quality bands",
      rows: cat.tiers.filter((t) => t.registered && t.task === "chat").map(row),
    },
    {
      title: "One tier per capability",
      rows: cat.tiers
        .filter((t) => t.registered && t.task && t.task !== "chat")
        .map(row),
    },
  ];
  return groups.filter((g) => g.rows.length > 0);
}
