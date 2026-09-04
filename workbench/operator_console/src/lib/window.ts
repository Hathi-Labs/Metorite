// The off-peak window, as the operator types it — SHAPE ONLY.
//
// Spec: `project-docs/specs/credit_pricing.md` §4.1 (slice 2). Migration 023.
//
// 🔴 **A judgement, so it lives here rather than inside the form.** The repo's
// rule (`priceboard.ts`, `pricing.ts`) is that anything with a right and a
// wrong answer is a pure function with its own test, because a rule buried in
// a component is a rule nobody can exercise without a browser.
//
// ⚠️ **This checks SHAPE, never truth.** Whether 16:30 is really when DeepSeek
// gets cheaper is the operator's knowledge and nothing here can verify it. All
// this refuses is a value the database would either reject or silently read as
// a different time.

/** `HH:MM`, 24-hour, zero-padded. `9:00` is refused: Postgres would take it,
 *  and a reader scanning a column of times should not have to wonder whether
 *  a ragged one means 09:00 or something else. */
const HH_MM = /^([01]\d|2[0-3]):[0-5]\d$/;

export type WindowProblem =
  | { kind: "half"; message: string }
  | { kind: "badTime"; field: string; message: string };

/** Check the pair the operator typed. `null` means the shape is fine —
 *  including the common case where both boxes are empty, which means this
 *  vendor charges one rate all day.
 *
 * ⚠️ **Both or neither, and the database agrees.**
 * `model_profile_offpeak_range_complete` (023) refuses one bound alone for the
 * same reason: a single bound cannot say whether the operator meant all day or
 * nothing, and a reader would have to guess. Refusing in the form as well only
 * changes WHERE the operator finds out — here, beside the box, instead of in a
 * 422 body. */
export function windowProblem(
  start: string,
  end: string,
): WindowProblem | null {
  const s = start.trim();
  const e = end.trim();

  if ((s.length > 0) !== (e.length > 0)) {
    return {
      kind: "half",
      message:
        "An off-peak window needs BOTH times, or neither. Fill the other " +
        "box, or clear them both to price this model the same all day.",
    };
  }

  for (const [field, value] of [
    ["off-peak start", s],
    ["off-peak end", e],
  ] as const) {
    if (value.length > 0 && !HH_MM.test(value)) {
      return {
        kind: "badTime",
        field,
        message: `"${value}" is not a time (${field}). Write HH:MM in UTC, for example 16:30.`,
      };
    }
  }
  return null;
}

/** Does this window cross midnight? DeepSeek's does — 16:30 to 00:30.
 *
 * 🔴 **The form says so out loud**, because an operator reading "starts 16:30,
 * ends 00:30" would otherwise reasonably wonder whether that means eight hours
 * or none. The server-side reader (`pricing_window.py`) handles the wrap; this
 * is only how the surface explains it. */
export function wrapsMidnight(start: string, end: string): boolean {
  const s = start.trim();
  const e = end.trim();
  if (!HH_MM.test(s) || !HH_MM.test(e)) return false;
  return s > e;
}
