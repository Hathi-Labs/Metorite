/**
 * Timeline block · the date maths of a drag.
 *
 * Moved out of `app/projects/lib/timeline.ts` (STU-D6). It holds the part of a
 * move, a resize and a create that does not depend on what the row is. The
 * field names a drag writes stay in the app.
 */

import type { DaySpan } from "./scale";

/** Two days as a span, earliest first. Reversed input is shown, not dropped. */
export function orderedSpan(a: string, b: string): DaySpan {
  return a <= b ? { from: a, to: b } : { from: b, to: a };
}

/**
 * Where the left edge may go. It never passes the right edge.
 *
 * **Clamped, never swapped.** A bar that turns inside out under the cursor is
 * not something anybody asked for.
 */
export function clampStartDay(span: DaySpan, day: string): string {
  return day > span.to ? span.to : day;
}

/** Where the right edge may go. It never passes the left edge. */
export function clampEndDay(span: DaySpan, day: string): string {
  return day < span.from ? span.from : day;
}
