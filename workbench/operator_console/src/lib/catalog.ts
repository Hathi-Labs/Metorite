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

import type { ModelRate } from "./contract";

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
  row: Pick<ModelRate, "mode" | "unit" | "inputPer1k" | "outputPer1k" | "creditsPerUnit">,
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
