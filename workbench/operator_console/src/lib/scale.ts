// Restating a money string at a different scale — WITHOUT touching a float.
//
// Spec: `project-docs/specs/credit_pricing.md` §4.2 (slice 3). Migration 025.
//
// 🔴 **`Number(v) * 1000` is a money bug, and it is not theoretical.** Over the
// 20000 rates between 0.0001 and 2.0000, **4773 of them** come back with a
// float artefact: `0.0041` becomes `4.1000000000000005`. That string then
// reaches a price column, a display, or a comparison, and the number stops
// matching itself — the exact failure `read.ts` warns about at the top of the
// file for every other money value it passes through as a string.
//
// ⚠️ **So this moves the decimal point instead of multiplying.** Shifting a
// digit string is exact at every scale, has no rounding mode to get wrong, and
// needs no dependency.

/** Multiply a decimal STRING by 1000 by moving the point. Exact.
 *
 * Returns the input unchanged when it is not a plain decimal — a caller with
 * something unparseable is better served by its own value reaching the display
 * than by a silent zero. */
export function timesThousand(value: string): string {
  const raw = (value ?? "").trim();
  const m = /^(-?)(\d*)(?:\.(\d*))?$/.exec(raw);
  if (!m || (m[2] === "" && (m[3] ?? "") === "")) return raw;

  const sign = m[1];
  let whole = m[2] || "0";
  let frac = m[3] ?? "";

  // Move the point three places right, padding when the fraction runs out.
  const shift = 3;
  if (frac.length <= shift) {
    whole += frac.padEnd(shift, "0");
    frac = "";
  } else {
    whole += frac.slice(0, shift);
    frac = frac.slice(shift);
  }

  whole = whole.replace(/^0+(?=\d)/, "");
  frac = frac.replace(/0+$/, "");
  return sign + whole + (frac ? "." + frac : "");
}
