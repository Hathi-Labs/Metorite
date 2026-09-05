// The rate card in words — what a customer is billed, said so an operator can
// check it against the contract.
//
// 🔴 **The failure this exists to prevent.** An operator reading "0.4 per 1k"
// when the card actually charges 0.4 per MINUTE would sign off on a price
// forty times wrong, and nothing downstream would catch it — both numbers are
// real, they just belong to different columns.
//
// ⚠️ **The type is `ModelRate` from `contract.ts`, not a second shape.** This
// file used to carry a snake_case `RateRow` mirroring the Console's JSON. The
// mapping happens once in `read.ts` now, so everything downstream speaks one
// language.

import type { ModelRate, TierRate } from "./contract";

export type { ModelRate };

/** Singular form of a unit, for "0.4 per minute" rather than "per minutes". */
export function singular(unit: string): string {
  if (unit.endsWith("ies")) return `${unit.slice(0, -3)}y`;
  if (unit.endsWith("s")) return unit.slice(0, -1);
  return unit;
}

/** How this card charges, in words an operator can check against the contract.
 *
 * ⚠️ **Reads the UNIT, never the mode.** A card can be `priced` in minutes or
 * in tokens, and the two use different columns — `creditsPerUnit` versus the
 * three per-1k rates. Choosing the wrong column shows a number that is real
 * and belongs to something else.
 */
export function describeRate(
  // Structural on purpose: the model-keyed card and the tier card (D67)
  // share every field this reads, and one describer keeps one phrasing.
  // ⚠️ The TIER card reads per MILLION (025) and the retired MODEL card still
  // reads per 1k, so the describer takes the numbers rather than the row and
  // one phrasing survives both. `describeTierRate` below is the tier caller.
  row: Pick<ModelRate, "mode" | "unit" | "creditsPerUnit"> & {
    inputPer1k: string;
    outputPer1k: string;
  },
): string {
  if (row.mode === "absorbed") {
    // D19.2: deliberately free, and NOT the same as unpriced. Saying "free"
    // where the card means "nobody has set this yet" is how a draft ships.
    return "absorbed into the seat price";
  }
  if (row.mode !== "priced") return "not priced";
  if (row.unit === "tokens") {
    return `${row.inputPer1k} in / ${row.outputPer1k} out per 1k`;
  }
  return `${row.creditsPerUnit} per ${singular(row.unit)}`;
}


/** The TIER card's line, per MILLION tokens (migration 025, slice 3).
 *
 * 🔴 **Per million is the scale of record from 2026-09-04** (owner directive).
 * Every vendor quotes per million, so the card a customer is billed against
 * now speaks the same unit as the cost it was derived from — and an operator
 * comparing the two carries no factor of 1000 in their head.
 *
 * ⚠️ Kept beside `describeRate` rather than folded into it, because the two
 * cards are on different scales during release one and a single function
 * taking a scale flag is how the wrong flag reaches the wrong card. */
export function describeTierRate(
  row: Pick<TierRate, "mode" | "unit" | "inputPer1m" | "outputPer1m" | "creditsPerUnit">,
): string {
  if (row.mode === "absorbed") return "absorbed into the seat price";
  if (row.mode !== "priced") return "not priced";
  if (row.unit === "tokens") {
    return `${row.inputPer1m} in / ${row.outputPer1m} out per 1M`;
  }
  return `${row.creditsPerUnit} per ${singular(row.unit)}`;
}
