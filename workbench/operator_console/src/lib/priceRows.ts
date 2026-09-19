// One row per tier — what it costs us, what we charge, what that leaves.
//
// 🔴 **Why this file exists.** /pricing answered one question across three
// panels. A list said what a tier costs a customer, a method board suggested a
// price, and a hand form wrote one. An operator pricing "Fast" read it in the
// first, then acted in the second or the third, with nothing joining the two.
// The tier is the thing being priced, so the tier is the row.
//
// ⚠️ **Every judgement here, and none in the component.** This app carries no
// React renderer, so logic inside JSX is untested by construction — the same
// rule `fallback.ts` and `priceboard.ts` already follow. `priceRows.test.ts`
// is the fence.
//
// ⚠️ **PER MILLION is the scale of record** (owner directive 2026-09-04). The
// card reads per million, the vendor quotes per million, and this file speaks
// per million everywhere. `vendorCostCreditsPer1k` returns per THOUSAND, so
// every call to it is multiplied here, once, by `PER_1M`. A factor of 1000
// loose in a pricing surface is how a price ships 100x wrong, and this repo
// has shipped that once already.

import type {
  AiCatalog,
  CatalogModel,
  CreditPrice,
  Tier,
  TierMargin,
  TierRate,
} from "./contract";
import {
  type Assumptions,
  chargeForMargin,
  creditsPerUnitFromUsd,
  roundCredits,
  vendorCostCreditsPer1k,
} from "./pricing";
import { savedAssumptions } from "./priceboard";

/** Tokens per million. Named once, for the reason in the header. */
export const PER_1M = 1000;

/** One priceable tier, with everything a decision about its price needs. */
export type TierPriceRow = {
  tier: Tier;
  /** The ONE job this tier serves (D68). */
  task: string;
  /** The task's natural unit — "tokens", "minutes", "images"… */
  unit: string;
  tokenPriced: boolean;
  /** The model that answers FIRST — the cost yardstick (D67). Null when the
   *  tier is bound to nothing, which is a different problem from an unpriced
   *  tier and the board says so in different words. */
  primary: CatalogModel | null;
  primaryId: string | null;
  /** What a customer pays today. Null when nobody has priced it. */
  rate: TierRate | null;
  /** What it actually earned. Null when the tier has no margin row. */
  margin: TierMargin | null;
};

/** What one tier costs US, in credits, at the scale its card is written in.
 *
 * ⚠️ Null means UNKNOWN, never free. A model with no recorded vendor price,
 * an unbound tier, and an unsaved credit price all land here, and each of
 * them must stop a suggestion rather than produce a zero. */
export function rowCost(
  row: TierPriceRow,
  a: Assumptions | null,
): { input: number | null; output: number | null } {
  if (a === null || row.primary === null) return { input: null, output: null };
  if (!row.tokenPriced) {
    // A per-unit task prices from the profile column its task uses. The
    // conversion already happened in the feed read, so nothing scales here.
    const usd = perUnitVendorUsd(row.task, row.primary);
    const c = creditsPerUnitFromUsd(usd, a);
    return { input: c, output: null };
  }
  const inC = vendorCostCreditsPer1k(row.primary.inputPer1M, a);
  const outC = vendorCostCreditsPer1k(row.primary.outputPer1M, a);
  return {
    input: inC === null ? null : inC * PER_1M,
    output: outC === null ? null : outC * PER_1M,
  };
}

/** Which profile column a non-token task takes its vendor cost from (H-78).
 *  Null for a task we hold no cost source for at all — `video` and `music`
 *  have none, and inventing one would put a made-up price on the board. */
export function perUnitVendorUsd(
  task: string,
  m: CatalogModel | null,
): number | null {
  if (m === null) return null;
  if (task === "transcribe") return m.perMinuteUsd;
  if (task === "speak") return m.perCharacterUsd;
  if (task === "image") return m.perImageUsd;
  return null;
}

/** The margin the CURRENT price leaves, as a fraction. Null when either side
 *  is unknown, or the tier is not priced — a margin on a free or unset price
 *  is not a small number, it is no number. */
export function plannedMargin(
  row: TierPriceRow,
  a: Assumptions | null,
): number | null {
  if (row.rate === null || row.rate.mode !== "priced") return null;
  const cost = rowCost(row, a);
  const charge = row.tokenPriced
    ? Number(row.rate.inputPer1m)
    : Number(row.rate.creditsPerUnit);
  const c = cost.input;
  if (c === null || !Number.isFinite(charge) || charge <= 0) return null;
  return (charge - c) / charge;
}

/** What to charge for a target margin, at the card's own scale.
 *
 * ⚠️ All-or-nothing on the in/out pair for a token job. A card with an input
 * price and no output price bills lopsided and reads as an accident. */
export function suggestFor(
  row: TierPriceRow,
  a: Assumptions | null,
  marginFraction: number | null,
): { input: string; output: string; cached: string } | null {
  if (a === null || marginFraction === null) return null;
  const cost = rowCost(row, a);
  if (!row.tokenPriced) {
    const ch = chargeForMargin(cost.input, marginFraction);
    if (ch === null || ch <= 0) return null;
    return { input: roundCredits(ch), output: "0", cached: "0" };
  }
  const inC = chargeForMargin(cost.input, marginFraction);
  const outC = chargeForMargin(cost.output, marginFraction);
  if (inC === null || outC === null || inC <= 0 || outC <= 0) return null;
  // No cached price recorded → charge the full input rate rather than 0.
  // Unknown must never bill as free. A vendor that LISTS cached at $0 is a
  // different thing: that is a fact, and it bills as the zero it states.
  const cachedRaw = vendorCostCreditsPer1k(row.primary?.cachedInputPer1M ?? null, a);
  const cachedC =
    cachedRaw === null
      ? null
      : chargeForMargin(cachedRaw * PER_1M, marginFraction);
  return {
    input: roundCredits(inC),
    output: roundCredits(outC),
    cached: cachedC === null ? roundCredits(inC) : cachedC <= 0 ? "0" : roundCredits(cachedC),
  };
}

/** Every priceable tier, in the tier board's own grouping so the two pages
 *  never disagree about what a tier IS. */
export function tierPriceRows(
  cat: AiCatalog,
): { title: string; rows: TierPriceRow[] }[] {
  const unitOf = new Map(cat.tasks.map((t) => [t.slug, t.natural_unit]));
  const modelById = new Map(cat.models.map((m) => [m.id, m]));
  const rateOf = new Map(cat.tierRates.map((r) => [`${r.tier}::${r.task}`, r]));
  const marginOf = new Map(cat.tierMargins.map((m) => [m.tier, m]));

  const build = (tier: Tier): TierPriceRow => {
    const task = tier.task ?? "chat";
    const unit = unitOf.get(task) ?? "tokens";
    const job = tier.jobs.find((j) => j.task === task);
    const first =
      job && job.chain.length > 0
        ? [...job.chain].sort((x, y) => x.rank - y.rank)[0].model
        : null;
    return {
      tier,
      task,
      unit,
      tokenPriced: unit.includes("token"),
      primaryId: first,
      primary: first === null ? null : (modelById.get(first) ?? null),
      rate: rateOf.get(`${tier.slug}::${task}`) ?? null,
      margin: marginOf.get(tier.slug) ?? null,
    };
  };

  // ⚠️ Only REGISTERED, CATEGORISED tiers can carry a price — the Console
  // refuses both a ghost and the wrong kind of job (D68). A board that
  // offered either would arm a button the Console always refuses.
  const priceable = cat.tiers.filter((t) => t.registered && t.task);
  const groups = [
    {
      title: "Chat — the quality bands",
      rows: priceable.filter((t) => t.task === "chat").map(build),
    },
    {
      title: "One tier per capability",
      rows: priceable.filter((t) => t.task !== "chat").map(build),
    },
  ];
  return groups.filter((g) => g.rows.length > 0);
}

/** How a tier's price reads at a glance. The chip, and the sort order of
 *  attention: an unbound tier cannot be priced, so it is named first. */
export type PriceState = "unbound" | "unpriced" | "absorbed" | "priced";

export function priceState(row: TierPriceRow): PriceState {
  if (row.primaryId === null) return "unbound";
  if (row.rate === null || row.rate.mode === "unpriced") return "unpriced";
  if (row.rate.mode === "absorbed") return "absorbed";
  return "priced";
}

/** The ONE line at the top of the page — the thing to do next, or nothing.
 *
 * 🔴 **One blocker, said once.** The page used to state the unsaved credit
 * price in three panels, and a reader who fixed it in the first still met it
 * twice more. The order here is the order a person can act in: a credit with
 * no value blocks every other act on the page, so it outranks everything.
 *
 * ⚠️ **A tier below its floor outranks an unpriced tier.** An unpriced tier
 * bills nothing, which costs us the sale. A tier under its floor bills at a
 * loss, which costs us money on every call. */
export function pricingAlert(
  groups: { rows: TierPriceRow[] }[],
  creditPrice: CreditPrice | null,
): { tone: "danger" | "warn" | "ok"; title: string; detail: string } {
  const rows = groups.flatMap((g) => g.rows);

  if (savedAssumptions(creditPrice) === null) {
    return {
      tone: "danger",
      title: "Set what a credit is worth",
      detail:
        "Nothing below can be priced until a credit has a rupee value. " +
        "Every suggestion converts a vendor's dollars into credits, and a " +
        "credit with no value converts to nothing.",
    };
  }

  const under = rows.filter((r) => {
    const f = r.margin?.marginFloor;
    const got = r.margin?.realisedMargin;
    if (f == null || got == null) return false;
    return Number(got) < Number(f);
  });
  if (under.length > 0) {
    return {
      tone: "danger",
      title:
        under.length === 1
          ? "1 tier earned less than its floor"
          : `${under.length} tiers earned less than their floor`,
      detail:
        "These bill below the margin they were given. Real traffic says so, " +
        "not a forecast. Reprice them, or lower the floor on purpose.",
    };
  }

  // ⚠️ **Unbound is NOT a kind of unpriced.** A tier with no model cannot
  // answer a customer at all, so counting it here would claim work is being
  // done for free when no work is possible. It gets its own sentence.
  const unbound = rows.filter((r) => priceState(r) === "unbound");
  const unpriced = rows.filter((r) => priceState(r) === "unpriced");
  const boundNote =
    unbound.length === 0
      ? ""
      : ` ${unbound.length} more ${plural(unbound.length, "tier")} ${unbound.length === 1 ? "runs" : "run"} on no model at all — bind ${unbound.length === 1 ? "it" : "them"} on Tiers & backups first, because a price needs a cost to stand on.`;

  if (unpriced.length > 0) {
    return {
      tone: "warn",
      title:
        unpriced.length === 1
          ? "1 tier answers customers and bills nothing"
          : `${unpriced.length} tiers answer customers and bill nothing`,
      detail:
        "Price each one below. Until then the work is real and the invoice is empty." +
        boundNote,
    };
  }

  if (unbound.length > 0) {
    return {
      tone: "warn",
      title:
        unbound.length === 1
          ? "1 tier runs on no model"
          : `${unbound.length} tiers run on no model`,
      detail:
        "Every tier that can serve carries a price. These cannot serve yet — " +
        "bind them on Tiers & backups, then price them here.",
    };
  }

  return {
    tone: "ok",
    title: "Every tier carries a price",
    detail:
      "Each one bills, and none is under the margin floor it was given. " +
      "Repricing is an insert, so history stays.",
  };
}

/** "1 tier" / "2 tiers". Display only. */
export function plural(n: number, word: string): string {
  return n === 1 ? word : `${word}s`;
}
