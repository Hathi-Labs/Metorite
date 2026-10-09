// The money model — what we charged a customer, what their AI cost us, and
// what is left. WS-50 slice 1, decision D94.
//
// Spec: `project-docs/specs/operator_console_money.md` §3.
//
// 🔴 **This file is the ONE place a money word is computed.** The owner could
// not tell "Our cost" from what we charge, because six labels named the vendor
// cost and "Margin" named four different numbers. Every page now asks this file,
// and `GLOSSARY` in `glossary.ts` gives each word one meaning.
//
// D94, in four rules:
//   1. A FREE credit is not revenue. Its AI cost is "Given away".
//   2. Rupees everywhere. A dollar converts at the planning rate saved on
//      /pricing, and the explanation shows the dollar figure.
//   3. "We charged" is the value of what the customer USED in the window:
//      seats plus paid credits spent. Cash received is a different figure.
//   4. An estimate is LABELLED, never hidden. Each figure carries `estimated`.
//
// ⚠️ **Pure functions only.** This app's suite has no React renderer, so the
// arithmetic AND the sentences that explain it live here, where
// `money.test.ts` can reach them.

import type { OrgUsageRow } from "./usage";

/** The saved credit price, as numbers. `null` until the owner saves one. */
export type Price = {
  /** What one credit costs a customer, in rupees. */
  inrPerCredit: number;
  /** The planning rate: rupees per US dollar. */
  inrPerUsd: number;
};

/** One figure, with the sentence that shows how it was reached. */
export type Figure = {
  /** `null` means unanswerable (usually: no credit price saved). Never zero. */
  value: number | null;
  /** True when any part of the value is an estimate. The page says so. */
  estimated: boolean;
  /** The worked arithmetic, with this customer's own numbers. */
  how: string;
};

export type DaysLeft = {
  /** Whole days, or `null` when there was no use to project from. */
  days: number | null;
  /** Average credits a day over the burn window, or `null` if unknown. */
  perDay: number | null;
  state: "ok" | "owing" | "idle";
  how: string;
};

export type CustomerMoney = {
  windowDays: number;
  charged: Figure;
  seats: Figure;
  paidCredits: Figure;
  aiCost: Figure;
  givenAway: Figure;
  profit: Figure;
  /** A FRACTION (0.93 is 93 percent). */
  margin: Figure;
  /** What the customer owes for credits spent below zero. NOT in `charged`. */
  owed: Figure;
  creditsUsed: number;
  creditsLeft: number;
  daysLeft: DaysLeft;
  /** Why any figure above is an estimate, in plain words. Empty when exact. */
  estimateNotes: string[];
};

/** The window the runway is judged over. Mirrors `analytics.BURN_WINDOW_DAYS`. */
export const BURN_WINDOW_DAYS = 7;

/** Below this much, a residue of untraced credits is rounding, not a gap. */
const TRACE_TOLERANCE = 0.01;

const num = (v: unknown): number => {
  const n = Number(v);
  return Number.isFinite(n) ? n : 0;
};

// ── Formatting ──────────────────────────────────────────────────────────────

/** Rupees, en-IN grouping. Whole rupees at ₹100 and above, paise below. */
export function formatInr(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return "—";
  const sign = value < 0 ? "−" : "";
  const abs = Math.abs(value);
  const digits = abs >= 100 ? 0 : 2;
  return `${sign}₹${abs.toLocaleString("en-IN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}`;
}

/** US dollars, for the vendor's own figure inside an explanation. */
export function formatUsdPlain(value: number): string {
  const digits = Math.abs(value) >= 1 ? 2 : 4;
  return `$${value.toLocaleString("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}`;
}

/** Credits, en-IN grouping. Whole credits at 100 and above, two decimals below. */
export function formatCr(value: number): string {
  return value.toLocaleString("en-IN", { maximumFractionDigits: Math.abs(value) >= 100 ? 0 : 2 });
}

/** A fraction as a whole percent: 0.934 → "93%". */
export function formatPct(fraction: number | null): string {
  if (fraction === null || !Number.isFinite(fraction)) return "—";
  return `${Math.round(fraction * 100)}%`;
}

const shortDate = (d: Date): string =>
  d.toLocaleDateString("en-IN", { day: "numeric", month: "short", timeZone: "UTC" });

// ── The price ───────────────────────────────────────────────────────────────

/** The two saved rates as numbers, or `null` if either is missing. */
export function priceFrom(
  inrPerCredit: string | number | null | undefined,
  usdToInr: string | number | null | undefined,
): Price | null {
  const c = Number(inrPerCredit);
  const fx = Number(usdToInr);
  if (!Number.isFinite(c) || c <= 0 || !Number.isFinite(fx) || fx <= 0) return null;
  return { inrPerCredit: c, inrPerUsd: fx };
}

// ── The model ───────────────────────────────────────────────────────────────

export type MoneyInput = {
  row: OrgUsageRow;
  price: Price | null;
  /** Seat revenue a month, in rupees (`mrr_paise / 100`). `null` if unknown. */
  seatsMonthlyInr: number | null;
  /** Seats bought, for the explanation only. */
  seatsBought: number | null;
  windowDays: number;
  /** When the Console began to record draws (migration 036), or `null`. */
  drawsSince: string | null;
  now: Date;
};

const NO_PRICE =
  "Save the credit price on the Pricing page to see this in rupees.";

/** Every money figure for one customer over one window. */
export function customerMoney(input: MoneyInput): CustomerMoney {
  const { row, price, windowDays, now } = input;
  const notes: string[] = [];

  const creditsUsed = num(row.credits);
  const costUsd = num(row.costUsd);
  const balance = num(row.balance);

  // ── What the draws say, and what they cannot ──────────────────────────
  const paidN = num(row.paidCredits);
  const paidValue = num(row.paidValueInr);
  const unpricedN = num(row.unpricedPaidCredits);
  const freeN = num(row.freeCredits);
  const unbackedN = num(row.unbackedCredits);
  const traced = paidN + freeN + unbackedN;
  const untraced = Math.max(0, creditsUsed - traced);
  const hasUntraced = untraced > TRACE_TOLERANCE;

  // The lifetime lot mix splits the untraced part (D94 rule 4).
  const lifePaid = num(row.lifePaidUsed);
  const lifeFree = num(row.lifeFreeUsed);
  const lifeTotal = lifePaid + lifeFree;
  const paidShare = lifeTotal > 0 ? lifePaid / lifeTotal : 1;
  const untracedPaid = untraced * paidShare;
  const untracedFree = untraced - untracedPaid;
  const lifeValue = num(row.lifePaidValueInr);
  // ⚠️ `lifePaidValueInr` covers PRICED lots only, so the divisor leaves the
  // unpriced bought credits out. Dividing by every bought credit let a large
  // grant with no price drag the average toward ₹0 (review, S1+S2).
  const lifePricedUsed = lifePaid - num(row.lifeUnpricedPaidUsed);
  const perPaidCredit =
    lifePricedUsed > 0 && lifeValue > 0 ? lifeValue / lifePricedUsed : price?.inrPerCredit ?? null;

  if (hasUntraced) {
    const since = input.drawsSince ? ` before ${shortDate(new Date(input.drawsSince))}` : "";
    notes.push(
      lifeTotal > 0
        ? `${formatCr(untraced)} credits were spent${since}, when the Console did not yet record ` +
            `which lots paid for each call. They are split by this customer's lifetime mix: ` +
            `${formatPct(paidShare)} bought, ${formatPct(1 - paidShare)} free.`
        : `${formatCr(untraced)} credits were spent${since}, and no lot records whether they ` +
            `were bought or given. They are counted as bought, at the current credit price.`,
    );
  }
  if (unpricedN > TRACE_TOLERANCE) {
    notes.push(
      `${formatCr(unpricedN)} bought credits came from a grant with no rupee amount recorded. ` +
        `They are valued at the current credit price.`,
    );
  }

  // ── Seats ─────────────────────────────────────────────────────────────
  const seatsMonthly = input.seatsMonthlyInr;
  const seatsValue = seatsMonthly === null ? null : (seatsMonthly * windowDays) / 30;
  const perSeat =
    seatsMonthly !== null && input.seatsBought ? seatsMonthly / input.seatsBought : null;
  // ⚠️ From the subscription as it is NOW. A plan that started, changed or
  // ended inside the window is not reflected, and the sentence says so.
  const SEATS_NOW = " This uses the subscription as it is today.";
  const seats: Figure = {
    value: seatsValue,
    estimated: false,
    how:
      seatsMonthly === null
        ? "The subscription read did not answer, so seat revenue is unknown."
        : seatsMonthly === 0
          ? "No active paid subscription today, so seats earn nothing." + SEATS_NOW
          : (perSeat !== null
              ? `${input.seatsBought} seats × ${formatInr(perSeat)} a month = ${formatInr(seatsMonthly)} a month`
              : `${formatInr(seatsMonthly)} a month`) +
            (windowDays === 30 ? "." : `, × ${windowDays}/30 days = ${formatInr(seatsValue)}.`) +
            SEATS_NOW,
  };

  // ── Paid credits ──────────────────────────────────────────────────────
  const unpricedValue = price ? unpricedN * price.inrPerCredit : null;
  const untracedValue = perPaidCredit !== null ? untracedPaid * perPaidCredit : null;
  const needsPrice = unpricedN > TRACE_TOLERANCE || (hasUntraced && untracedPaid > TRACE_TOLERANCE);
  const paidKnown = !needsPrice || (unpricedValue !== null && untracedValue !== null);
  const paidCreditsValue = paidKnown
    ? paidValue + (unpricedValue ?? 0) + (hasUntraced ? untracedValue ?? 0 : 0)
    : null;
  const paidCount = paidN + (hasUntraced ? untracedPaid : 0);
  const paidCredits: Figure = {
    value: paidCreditsValue,
    estimated: needsPrice,
    how: paidKnown
      ? `${formatCr(paidCount)} of the ${formatCr(creditsUsed)} credits used were bought. ` +
        `At the price each lot was sold at, they are worth ${formatInr(paidCreditsValue)}.`
      : `${formatCr(paidCount)} bought credits were used. ${NO_PRICE}`,
  };

  // ── We charged ────────────────────────────────────────────────────────
  const chargedValue =
    seatsValue === null || paidCreditsValue === null ? null : seatsValue + paidCreditsValue;
  const charged: Figure = {
    value: chargedValue,
    estimated: paidCredits.estimated,
    how:
      chargedValue === null
        ? seatsValue === null
          ? seats.how
          : `Seats ${formatInr(seatsValue)} + bought credits (unknown). ${NO_PRICE}`
        : `Seats ${formatInr(seatsValue)} + bought credits used ${formatInr(paidCreditsValue)} = ${formatInr(chargedValue)}.`,
  };

  // ── AI cost ───────────────────────────────────────────────────────────
  // 🔴 `costUsd` sums only the calls that carry a vendor cost. When some do
  // not, the AI cost is TOO LOW and the profit too high, so both say so
  // (`analytics.margin_ratio` guarded this with `costedShare`, review S1+S2).
  const costedShare =
    row.costedShare === null || row.costedShare === undefined ? null : num(row.costedShare);
  const costPartial = row.calls > 0 && costedShare !== null && costedShare < 0.995;
  if (costPartial) {
    notes.push(
      `Only ${formatPct(costedShare)} of this customer's calls have a recorded vendor cost. ` +
        "The AI cost covers those calls only, so the real AI cost is higher and the profit lower.",
    );
  }
  const aiCostValue = price ? costUsd * price.inrPerUsd : null;
  const aiCost: Figure = {
    value: aiCostValue,
    estimated: costPartial,
    how:
      (price
        ? `The AI vendors billed us ${formatUsdPlain(costUsd)} for this customer's calls. ` +
          `× ₹${price.inrPerUsd} a dollar (the planning rate on Pricing) = ${formatInr(aiCostValue)}.`
        : `The AI vendors billed us ${formatUsdPlain(costUsd)}. ${NO_PRICE}`) +
      (costPartial ? ` Only ${formatPct(costedShare)} of calls have a recorded cost.` : ""),
  };

  // ── Given away ────────────────────────────────────────────────────────
  const freeCount = freeN + (hasUntraced ? untracedFree : 0);
  const freeShare = creditsUsed > 0 ? freeCount / creditsUsed : 0;
  const givenValue = aiCostValue === null ? null : aiCostValue * freeShare;
  const givenAway: Figure = {
    value: givenValue,
    estimated: true,
    how:
      freeCount <= TRACE_TOLERANCE
        ? "No free credits (trial, promotion or grant) were used in this window."
        : `${formatCr(freeCount)} of the ${formatCr(creditsUsed)} credits used were free ` +
          `(trial, promotion or grant), so they earned nothing. Their share of the AI cost: ` +
          `${formatPct(freeShare)} of ${formatInr(aiCostValue)} = ${formatInr(givenValue)}.`,
  };

  // ── Profit and margin ─────────────────────────────────────────────────
  const profitValue =
    chargedValue === null || aiCostValue === null ? null : chargedValue - aiCostValue;
  const profit: Figure = {
    value: profitValue,
    estimated: charged.estimated || aiCost.estimated,
    how:
      profitValue === null
        ? NO_PRICE
        : `${formatInr(chargedValue)} we charged − ${formatInr(aiCostValue)} AI cost = ${formatInr(profitValue)}.`,
  };
  const marginValue =
    profitValue === null || chargedValue === null || chargedValue <= 0
      ? null
      : profitValue / chargedValue;
  const margin: Figure = {
    value: marginValue,
    estimated: charged.estimated || aiCost.estimated,
    how:
      marginValue === null
        ? chargedValue === 0
          ? "We charged nothing in this window, so there is no margin to measure."
          : NO_PRICE
        : `${formatInr(profitValue)} profit ÷ ${formatInr(chargedValue)} we charged = ${formatPct(marginValue)}.`,
  };

  // ── Owed ──────────────────────────────────────────────────────────────
  // ⚠️ The balance NOW, never the window's unbacked draws: a customer who went
  // below zero and then bought credits owes nothing (review, S1+S2).
  const owedCredits = balance < 0 ? -balance : 0;
  const owedValue = price ? owedCredits * price.inrPerCredit : null;
  const owed: Figure = {
    value: owedCredits > 0 ? owedValue : 0,
    estimated: false,
    how:
      owedCredits <= 0
        ? "The balance is not below zero."
        : `The balance is ${formatCr(balance)} credits. We kept serving after it reached zero. ` +
          (price
            ? `At ${formatInr(price.inrPerCredit)} a credit, they owe ${formatInr(owedValue)}.`
            : NO_PRICE),
  };

  return {
    windowDays,
    charged,
    seats,
    paidCredits,
    aiCost,
    givenAway,
    profit,
    margin,
    owed,
    creditsUsed,
    creditsLeft: balance,
    daysLeft: daysLeft(row, now),
    estimateNotes: notes,
  };
}

/** How long the credits last at recent use. Mirrors `analytics.runway_days`. */
export function daysLeft(row: OrgUsageRow, now: Date): DaysLeft {
  const balance = num(row.balance);
  const last7 =
    row.creditsLast7Days !== undefined && row.creditsLast7Days !== null
      ? num(row.creditsLast7Days)
      : null;
  const perDay = last7 !== null ? last7 / BURN_WINDOW_DAYS : null;

  if (balance < 0) {
    return {
      days: 0,
      perDay,
      state: "owing",
      how:
        `The balance is ${formatCr(balance)} credits: below zero. ` +
        "AI calls still go through, so every call adds to what they owe.",
    };
  }
  const days = row.runwayDays;
  if (days === null || days === undefined) {
    return {
      days: null,
      perDay,
      state: "idle",
      how:
        `${formatCr(balance)} credits left, and no AI use in the last ${BURN_WINDOW_DAYS} days. ` +
        "With no recent use there is nothing to project from.",
    };
  }
  const runOut = new Date(now.getTime() + days * 86_400_000);
  const rate =
    perDay !== null
      ? `${formatCr(perDay)} credits a day (the average of the last ${BURN_WINDOW_DAYS} days)`
      : `the average daily use of the last ${BURN_WINDOW_DAYS} days`;
  return {
    days,
    perDay,
    state: "ok",
    how:
      `${formatCr(balance)} credits left ÷ ${rate} = about ${days} days. ` +
      `At this rate the credits run out around ${shortDate(runOut)}.`,
  };
}

/** The words for a days-left cell. */
export function daysLeftLabel(d: DaysLeft): string {
  if (d.state === "owing") return "below zero";
  if (d.state === "idle") return "no recent use";
  if (d.days === 0) return "run out";
  return `≈ ${d.days} days`;
}

/** What one app, agent or person cost and earned, from the customer's rates.
 *
 * ⚠️ **An ESTIMATE, always.** The draws are recorded per charge, not per app,
 * so a row is valued at the customer's average rupees per credit used in the
 * window. The explanation says so.
 */
export function rowMoney(
  part: { credits: string; costUsd: string },
  whole: CustomerMoney,
  price: Price | null,
): { charged: number | null; aiCost: number | null; profit: number | null; margin: number | null } {
  const credits = num(part.credits);
  const perCredit =
    whole.paidCredits.value !== null && whole.creditsUsed > 0
      ? whole.paidCredits.value / whole.creditsUsed
      : null;
  const charged = perCredit === null ? null : credits * perCredit;
  const aiCost = price ? num(part.costUsd) * price.inrPerUsd : null;
  const profit = charged === null || aiCost === null ? null : charged - aiCost;
  const margin = profit === null || charged === null || charged <= 0 ? null : profit / charged;
  return { charged, aiCost, profit, margin };
}

/** The margin on AI alone: bought credits used against the AI cost. Seats
 *  are left out, because they carry no AI cost and pull the margin up.
 *  A FRACTION, or null when either side is unknown or nothing was bought. */
export function aiMarginOf(m: CustomerMoney): number | null {
  const rev = m.paidCredits.value;
  const cost = m.aiCost.value;
  if (rev === null || cost === null || rev <= 0) return null;
  return (rev - cost) / rev;
}

/** The average rupees a credit earned this customer in the window. */
export function earnedPerCredit(whole: CustomerMoney): number | null {
  return whole.paidCredits.value !== null && whole.creditsUsed > 0
    ? whole.paidCredits.value / whole.creditsUsed
    : null;
}
