/**
 * Timeline block · the row shape.
 *
 * One row of the chart, whatever it came from. A Projects task, a Studio
 * record with a start field and an end field, or any other thing with dates
 * all reach the block as this shape. The block never reads `data`. It hands
 * `data` back, so the caller can find its own record again.
 *
 * Dates are `Date` values here. The block reads them in LOCAL time and snaps
 * each one to a whole day. A time of day on a value is dropped.
 */

import type { StatusAccent } from "@/lib/statusAccent";

export interface TimelineRow<T = unknown> {
  id: string;
  label: string;
  /** First day of the bar. `null` for an unscheduled row, which has no bar. */
  start: Date | null;
  /** Last day of the bar, inclusive. `null` for an unscheduled row. */
  end: Date | null;
  /** The band this row sits in. Rows with no group share the top level. */
  group?: string;
  /** Ids of the rows that must finish first. Each id makes one arrow. */
  dependsOn?: readonly string[];
  /**
   * The colour of the bar.
   *
   * It is a `StatusAccent` from `src/lib/statusAccent.ts`, the one colour
   * vocabulary of the product. `null` is the neutral tone.
   */
  tone: StatusAccent | null;
  /**
   * The dates came from child rows, not from this row. The bar is drawn, but a
   * drag has no field to write, so the caller refuses it.
   */
  derived?: boolean;
  /** The caller's own record. The block never reads it. */
  data: T;
}
