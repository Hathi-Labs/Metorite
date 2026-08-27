// The model catalog's display logic (CP-10 slice 3).
//
// Extracted from `app/models/CatalogAdmin.tsx` so it can be tested — this
// console's suite is `lib/*.test.ts` and carries no React renderer. What is
// worth testing here is the RATE line: an operator reading "0.4 per 1k" when
// the card actually charges 0.4 per MINUTE would sign off on a price forty
// times wrong, and nothing downstream would catch it.

export type RateRow = {
  model: string;
  task: string;
  unit: string;
  pricing_mode: string;
  input_per_1k: string;
  output_per_1k: string;
  cached_input_per_1k: string;
  credits_per_unit: string;
};

/** Singular form of a unit, for "0.4 per minute" rather than "per minutes". */
export function singular(unit: string): string {
  if (unit.endsWith("ies")) return `${unit.slice(0, -3)}y`;
  if (unit.endsWith("s")) return unit.slice(0, -1);
  return unit;
}

/** How this card charges, in words an operator can check against the contract.
 *
 * ⚠️ **Reads the UNIT, never the mode.** A card can be `priced` in minutes or
 * in tokens, and the two use different columns — `credits_per_unit` versus the
 * three per-1k rates. Choosing the wrong column shows a number that is real
 * and belongs to something else.
 */
export function describeRate(row: RateRow): string {
  if (row.pricing_mode === "absorbed") {
    // D19.2: deliberately free, and NOT the same as unpriced. Saying "free"
    // where the card means "nobody has set this yet" is how a draft ships.
    return "absorbed into the seat price";
  }
  if (row.pricing_mode !== "priced") return "not priced";
  if (row.unit === "tokens") {
    return `${row.input_per_1k} in / ${row.output_per_1k} out per 1k`;
  }
  return `${row.credits_per_unit} per ${singular(row.unit)}`;
}
