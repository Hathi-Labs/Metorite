/**
 * The thinking trail's geometry — one column for every icon, and a body that
 * sizes to its steps. Pure, so it is tested without a DOM.
 *
 * ## Two defects it removes (2026-10-07, the Projects rail)
 *
 * 1. **Dead space under the steps.** While a turn ran, the body was a FIXED
 *    `h-56` (14rem). Two steps fill about 3rem, so the panel drew some 10rem
 *    of empty border under them, for the whole turn. The answer streams
 *    while the turn runs, so that was most of the time a member looked at
 *    it. The body now takes a MAXIMUM height and no height, so it is as tall
 *    as its steps, up to the cap, and then it scrolls.
 * 2. **The header icon sat right of the step icons.** The header had its own
 *    `px-3` inset, and each step put its icon at `left-[5px]` on the axis. Two
 *    numbers in two places agreed on nothing. Now the header and every step
 *    start with the SAME cell, {@link TRAIL_ICON_CELL}, and the axis line is
 *    the centre of that cell. One column, so they cannot drift.
 *
 * Fence: `src/lib/trailLayout.test.ts`.
 */

/**
 * The cell every trail icon sits in: the header's and every step's. `w-8` is
 * the column (2rem, so it follows the member's density). Written whole, not
 * built from a number, because Tailwind finds a class only as a literal.
 */
export const TRAIL_ICON_CELL = "w-8 shrink-0 flex justify-center";

/** The axis line, at the centre of the icon column: half of `w-8`. The test
 *  reads both numbers and fails when they disagree. */
export const TRAIL_AXIS = "left-4";

/**
 * The body's height rule. A cap, never a height: the trail is as tall as its
 * steps. While the turn runs the cap is lower, so a long trail scrolls inside
 * itself (followed to the newest step) and does not push the answer down.
 * A finished trail that a member opens may grow to a larger cap, and never
 * past 60% of the screen, so on a phone the answer stays in view.
 */
export function trailBodySize(isActive: boolean): string {
  return isActive ? "max-h-56" : "max-h-[min(32rem,60vh)]";
}

/** The horizontal inset classes in a class string (`px-3`, `pl-2`, `ml-8`). */
export function horizontalInsets(className: string): string[] {
  return className
    .split(/\s+/)
    .filter((c) => /^-?(p|m)(x|l|s)-/.test(c));
}
