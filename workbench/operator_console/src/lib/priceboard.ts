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
// costs-blind model) gets an empty suggestion and says why, never a guessed
// number.
//
// ⚠️ **A per-unit job now HAS a recorded cost** (H-78, §6A.11a clauses 5-7).
// `recordedVendorUsd` below reads the profile's per-minute, per-character or
// per-image price, so the operator stops typing the vendor's dollar figure
// for an `image`, `transcribe` or `speak` job. The typed box stays, because
// a model nobody has profiled still has no cost.

import { singular } from "./catalog";
import type {
  AiCatalog, CatalogModel, CreditPrice, Task, Tier, TierRate,
} from "./contract";
import {
  type Assumptions,
  chargeForMargin,
  creditsPerUnitFromUsd,
  fixedDecimal,
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
  // A $0-listed leg is a KNOWN cost, but its charge is 0 — and 0 is the
  // absorbed DECISION, which only the hand form writes. Unknown and free
  // both step aside here, so the board never arms a blank Apply.
  if (inC === null || outC === null || inC <= 0 || outC <= 0) return null;
  const cachedC = chargeForMargin(
    vendorCostCreditsPer1k(m.cachedInputPer1M, a), margin);
  return {
    in1k: roundCredits(inC),
    out1k: roundCredits(outC),
    // No cached price recorded → charge the full input rate for cached
    // tokens rather than 0: unknown must never bill as free. A vendor that
    // LISTS cached at $0 is different — that is a fact, and the card says
    // "0" explicitly while the in/out legs still bill.
    cached1k: cachedC === null ? roundCredits(inC)
      : cachedC <= 0 ? "0"
        : roundCredits(cachedC),
  };
}

/** Which profile column a non-token task takes its vendor cost from
 *  (H-78). The judgement lives HERE and not in `PriceFromCost.tsx`, because
 *  this app carries no React renderer and logic inside a component is
 *  untested by construction.
 *
 * 🔴 **One task, one column, and the units already agree.** `task_catalog`
 *  (010) prices `transcribe` in minutes, and the profile column holds
 *  minutes. The Console did the per-second-to-per-minute conversion once, in
 *  the feed read, so nothing on this path multiplies anything.
 *
 *  Null for every other task. A `video` or `music` job has no cost source at
 *  all, and inventing one would put a made-up price on the board. */
export function recordedVendorUsd(
  task: string,
  m: CatalogModel | undefined,
): number | null {
  if (!m) return null;
  if (task === "transcribe") return m.perMinuteUsd;
  if (task === "speak") return m.perCharacterUsd;
  if (task === "image") return m.perImageUsd;
  return null;
}

/** What the vendor-cost box on the board SHOWS for a per-unit job: what the
 *  operator typed, else the recorded profile price, else empty.
 *
 * ⚠️ **The typed value wins even when it is empty.** An operator who clears
 *  the box means "ignore the recorded price", and re-filling it under their
 *  cursor would make the box impossible to empty. */
export function vendorUsdBox(
  typed: string | undefined,
  recorded: number | null,
): string {
  if (typed !== undefined) return typed;
  // ⚠️ `String(recorded)` put "3e-7" in the box, and that box is both what
  // the operator checks and what the suggestion reads. `fixedDecimal` is
  // the console's one plain-digits renderer.
  return recorded === null ? "" : fixedDecimal(recorded);
}

/** A per-unit job's suggestion from a vendor dollar price. */
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
  const rounded = inr >= 100 ? Math.round(inr)
    : Number(inr.toPrecision(3));
  // ⚠️ **Grouped, and that became necessary with migration 024.** At the
  // per-1k scale these read "₹4" and "₹12" and grouping bought nothing. At
  // per MILLION every figure gains three digits, and "₹204000" is a number an
  // operator has to count digits on before they can compare it to "₹34000".
  //
  // 🔴 `en-IN` on purpose: the product is priced in rupees for an Indian
  // market, so the local grouping (₹2,04,000) is the one the reader already
  // uses. This is DISPLAY only — nothing parses this string back.
  return `₹${rounded.toLocaleString("en-IN")}`;
}

/** A priced card's rupee line — "≈ ₹2000 in / ₹6000 out per 1M", or "≈ ₹36
 *  per image" — or null when no credit price is saved, the card is not
 *  priced, or a number fails to parse. The list then shows credits alone.
 *
 * ⚠️ **Per MILLION, matching the credits line directly above it** (migration
 *  024). These two strings sit on one row of the price list, and a rupee
 *  figure at a different scale from the credit figure beside it is worse than
 *  no rupee figure at all — it reads as a contradiction the operator has to
 *  resolve, and the resolution is a factor of 1000. */
export function inrRateLine(
  rate: TierRate,
  price: CreditPrice | null,
  tasks: Task[],
): string | null {
  if (rate.mode !== "priced") return null;
  if (rate.unit.includes("token")) {
    const inr = inrLabel(rate.inputPer1m, price);
    const out = inrLabel(rate.outputPer1m, price);
    if (inr === null || out === null) return null;
    return `≈ ${inr} in / ${out} out per 1M tokens`;
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

// ── The margin monitor (migration 028, credit_pricing.md §4.3) ─────────────

/** How a realised margin reads against the floor it was given.
 *
 * 🔴 **`null` on either side is NEUTRAL, never a pass and never a failure.**
 * A tier with no floor has not been given a threshold. A tier with no realised
 * margin has no saved credit price to compute one from. Both are unanswered
 * questions, and an alarm on an unanswered question teaches an operator to
 * ignore alarms. */
export function marginTone(
  realised: string | null,
  floor: string | null,
): "alarm" | "ok" | "muted" {
  if (realised === null || floor === null) return "muted";
  const r = Number(realised);
  const f = Number(floor);
  if (!Number.isFinite(r) || !Number.isFinite(f)) return "muted";
  return r < f ? "alarm" : "ok";
}

/** A margin as a percentage string, or a dash. Display only. */
export function marginPct(value: string | null): string {
  if (value === null) return "—";
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return `${(n * 100).toFixed(1)}%`;
}

/** The tiers worth showing on the monitor.
 *
 * ⚠️ **A tier with no traffic AND no margin set is dropped.** The slate is
 * eleven tiers on a clean database and several hundred on one a test suite has
 * been run against, and a monitor nobody can scan is a monitor nobody reads.
 * A tier the owner has priced always survives, even at zero traffic — that is
 * the row whose price nobody has checked. */
export function monitorRows<
  T extends { calls: number; marginMultiplier: string | null; marginFloor: string | null },
>(rows: T[]): T[] {
  return rows.filter(
    (r) => r.calls > 0 || r.marginMultiplier !== null || r.marginFloor !== null,
  );
}

/** The margin box's starting value, from what the owner has actually set.
 *
 * 🔴 **Empty when no tier carries a multiplier**, which is the shipped state —
 * `tier_margin` ships empty and every figure in it is the owner's (H-42). The
 * board used to open at 70 percent: a commercial number nobody chose,
 * presented to an operator as an answer.
 *
 * ⚠️ **The MEDIAN when several tiers differ, never the mean.** These are
 * multipliers, and the design document's own spread runs 1.4 to 2.5. A mean is
 * dragged by whichever end has more tiers; the median lands on a value some
 * tier actually carries. It is still only a starting point — the operator
 * types the tier they mean. */
export function defaultMarginPct(catalog: {
  tierMargins: { marginMultiplier: string | null }[];
}): string {
  const ms = catalog.tierMargins
    .map((m) => Number(m.marginMultiplier))
    .filter((n) => Number.isFinite(n) && n >= 1)
    .sort((a, b) => a - b);
  if (ms.length === 0) return "";
  const m = ms[Math.floor(ms.length / 2)];
  // margin = 1 - 1/M, as a percentage.
  return String(Math.round((1 - 1 / m) * 100));
}
