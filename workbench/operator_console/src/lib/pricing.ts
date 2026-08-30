// Pricing arithmetic — margins, suggestions, and the honesty rules for both.
//
// 🔴 **The credit's REAL price lives in `credit_price` (migration 017),
// saved from the Pricing page — H-42's mechanism.** This module still runs
// on ASSUMPTIONS handed to it: the page seeds them from the saved price
// when one exists, and the operator can overtype them to explore a
// what-if. The module itself touches no storage and no network
// (`pricing.test.ts` fences it), so a what-if never becomes a fact by
// accident — saving the fact is the CreditPrice panel's explicit POST.
//
// ⚠️ **Every function answers null before it guesses.** A margin computed from
// a missing vendor price, a zero credit price, or an unparsable number is not
// a low-confidence margin — it is fiction with a percent sign.
//
// ⚠️ Units, written once so nobody re-derives them wrong:
//   vendor price   USD per 1,000,000 tokens   (model_profile, "we pay")
//   rate card      credits per 1,000 tokens   (tier_rate_card, "we charge")
//   assumptions    ₹ per credit · ₹ per USD

/** The two numbers the operator asserts. Both must be positive to be usable. */
export type Assumptions = {
  inrPerCredit: number | null;
  inrPerUsd: number | null;
};

export function parseAssumption(raw: string): number | null {
  const t = raw.trim();
  if (!t) return null;
  const n = Number(t);
  return Number.isFinite(n) && n > 0 ? n : null;
}

export function usable(a: Assumptions): boolean {
  return a.inrPerCredit !== null && a.inrPerUsd !== null;
}

/** What 1,000 tokens cost US, in credits, under the assumptions.
 *
 * vendorPer1M USD/1M → /1000 is USD per 1k → ×₹/$ is ₹ per 1k → /₹-per-credit
 * is credits per 1k. Null in, null out. */
export function vendorCostCreditsPer1k(
  vendorPer1M: number | null,
  a: Assumptions,
): number | null {
  if (vendorPer1M === null || !usable(a)) return null;
  if (vendorPer1M < 0) return null;
  return (vendorPer1M / 1000) * (a.inrPerUsd as number) / (a.inrPerCredit as number);
}

/** The margin on one per-1k price, as a fraction of the CHARGE.
 *
 * (charge − cost) / charge. 0.6 means 60 % of what the customer pays is
 * ours. Negative means we sell below cost. Null when either side is unknown
 * or the charge is zero — a margin on a zero price divides by zero and means
 * nothing anyway. */
export function marginFraction(
  chargePer1k: number | null,
  vendorPer1M: number | null,
  a: Assumptions,
): number | null {
  const cost = vendorCostCreditsPer1k(vendorPer1M, a);
  if (cost === null || chargePer1k === null) return null;
  if (!Number.isFinite(chargePer1k) || chargePer1k <= 0) return null;
  return (chargePer1k - cost) / chargePer1k;
}

/** What ONE natural unit (an image, a minute, a second…) costs us, in
 * credits, from the vendor's per-unit dollar price. Same conversion as the
 * token path, without the /1000. Null in, null out. */
export function creditsPerUnitFromUsd(
  usdPerUnit: number | null,
  a: Assumptions,
): number | null {
  if (usdPerUnit === null || !usable(a)) return null;
  if (usdPerUnit < 0 || !Number.isFinite(usdPerUnit)) return null;
  return (usdPerUnit * (a.inrPerUsd as number)) / (a.inrPerCredit as number);
}

/** The charge that yields a target margin over a cost, in the SAME unit.
 *
 * charge = cost / (1 − margin). A 100 %-or-more target asks for an infinite
 * price and answers null instead — so does a missing cost. */
export function chargeForMargin(
  costCredits: number | null,
  targetMarginFraction: number,
): number | null {
  if (costCredits === null) return null;
  if (targetMarginFraction >= 1 || targetMarginFraction < 0) return null;
  return costCredits / (1 - targetMarginFraction);
}

/** The per-1k credit price that yields a target margin over the vendor cost.
 *  The token-unit face of `chargeForMargin` — one formula, two units. */
export function priceForMargin(
  vendorPer1M: number | null,
  a: Assumptions,
  targetMarginFraction: number,
): number | null {
  return chargeForMargin(
    vendorCostCreditsPer1k(vendorPer1M, a),
    targetMarginFraction,
  );
}

/** Draw a fraction as a whole percent the operator can read at a glance. */
export function marginLabelPct(fraction: number | null): string {
  if (fraction === null) return "—";
  return `${Math.round(fraction * 100)}%`;
}

/** Round a suggested credit price to something a person would actually set.
 *
 * ⚠️ Four significant figures, not four decimals: DeepSeek-class prices live
 * near 0.0005 credits/1k where fixed decimals round to zero, and a suggestion
 * of zero reads as "free is fine". */
export function roundCredits(value: number | null): string {
  if (value === null || !Number.isFinite(value) || value <= 0) return "";
  return value.toPrecision(4).replace(/\.?0+$/, "");
}
