/**
 * Timeline block · bar and column geometry.
 *
 * Moved from `app/projects/lib/timeline.ts` (STU-D6). Pure arithmetic over a
 * `TimelineRange`. No row type, no task type and no DOM.
 */

import { DAY_MS, dayKey, fromDayKey, mondayOffset, shiftDay } from "./days";
import type { TimelineRange } from "./scale";

/** Height of one task row, in pixels. Rows are uniform so `y` is index × this. */
export const ROW_H = 40;
/** Height of the bar inside a row, leaving the rest as the row's own padding. */
export const BAR_H = 26;
/** Days of breathing room either side of the data's own range. */
/** Narrowest a bar may be drawn — a one-day task must still be clickable. */
export const MIN_BAR_PX = 10;
/** A bar narrower than this cannot hold its own title, so the label sits beside it. */
export const LABEL_INSIDE_PX = 72;

/** How far a floating label sits from the bar it names. */
export const LABEL_GAP_PX = 8;
/** The most a floating label may take, however much room there is. */
export const LABEL_MAX_PX = 240;
/**
 * The least room worth putting a label in.
 *
 * Below this the label is one word and an ellipsis, which names nothing. It
 * goes to the other side instead, where there is usually the whole chart.
 */
export const LABEL_MIN_PX = 88;

/** Where a narrow bar's floating label goes. */
export interface LabelPlacement {
  side: "right" | "left";
  /** A CSS `left` when the side is right, a CSS `right` when it is left. */
  offsetPx: number;
  maxWidthPx: number;
}

/**
 * Which side of a narrow bar its label goes, and how wide it may be.
 *
 * The label used to be unconditionally on the right, at `leftPx + widthPx + 8`,
 * with a `max-w` and no bound against the canvas. `TimelineBar`'s own header
 * asserted that the right is "where there is always room". That is false at the
 * end of the chart. Measured at 1440 on 2026-09-05: a bar near the right edge
 * put its label 57px past the scroller, and the name was clipped.
 *
 * That is the worst case rather than a harmless one. A bar too narrow to hold
 * its title is exactly the bar nobody can identify without the label, and the
 * rail's copy of the name can be a whole screen away across 6000px of canvas.
 *
 * No text is measured. The side comes from the bar's own geometry, and the cap
 * is the room actually present on the chosen side. So a label can come out
 * short, and can never cross an edge.
 */
export function labelSide(
  bar: { leftPx: number; widthPx: number },
  canvasPx: number,
): LabelPlacement {
  const roomRight = canvasPx - (bar.leftPx + bar.widthPx + LABEL_GAP_PX);
  const roomLeft = bar.leftPx - LABEL_GAP_PX;

  // Right unless it does not fit AND the left does better. A tie keeps the
  // right, so an unremarkable chart never moves a label and the reading order
  // stays bar-then-name.
  if (roomRight >= LABEL_MIN_PX || roomRight >= roomLeft) {
    return {
      side: "right",
      offsetPx: bar.leftPx + bar.widthPx + LABEL_GAP_PX,
      maxWidthPx: Math.max(0, Math.min(LABEL_MAX_PX, roomRight)),
    };
  }
  return {
    side: "left",
    // Measured from the canvas's RIGHT edge, because that is what CSS `right`
    // is relative to. The label's right edge lands one gap left of the bar.
    offsetPx: canvasPx - bar.leftPx + LABEL_GAP_PX,
    maxWidthPx: Math.max(0, Math.min(LABEL_MAX_PX, roomLeft)),
  };
}

export interface Bar {
  leftPx: number;
  widthPx: number;
  /** Only one date is known, so the bar is a marker rather than a span. */
  singleDate: boolean;
  /** The interval came from this task's CHILDREN, not from its own dates. */
  derived: boolean;
}

/** Pixels from the chart's left edge to the START of a day. */
export function dayPx(day: string, range: TimelineRange): number {
  const offset =
    (fromDayKey(day).getTime() - fromDayKey(range.from).getTime()) / DAY_MS;
  return Math.round(offset) * range.pxPerDay;
}

/**
 * ── DRAGGING (WS-27t S2) ──────────────────────────────────────────────────
 *
 * How many whole days a horizontal drag of `dx` pixels means.
 *
 * **A drag is a DELTA, never an absolute position.** Plane's resize maps the
 * cursor's absolute x onto a day (`use-gantt-resizable.ts`), which forces it to
 * carry an `offsetX` correction for where inside the bar you grabbed — and gets
 * it only for the move, so grabbing a resize handle three pixels off its centre
 * jumps the edge by a day before you have moved the mouse. A delta has no
 * origin to correct: wherever you grabbed, the edge moves exactly as far as
 * your hand did.
 *
 * `Math.round` rather than `floor`, so the snap goes to the NEAREST day
 * boundary and a half-day drag resolves the way the cursor is leaning.
 */
export function dayStep(dx: number, range: TimelineRange): number {
  return Math.round(dx / range.pxPerDay);
}

/** The day occupying a pixel column, clamped into the range. */
export function dayAtPx(px: number, range: TimelineRange): string {
  const index = Math.floor(px / range.pxPerDay);
  const clamped = Math.min(Math.max(index, 0), range.days - 1);
  return shiftDay(range.from, clamped);
}

/** Saturday or Sunday — the shaded columns. Monday-week, as `calendar.ts` says. */
export function isWeekend(day: string): boolean {
  return mondayOffset(fromDayKey(day)) >= 5;
}

/**
 * The bar for a span, or `null` when the row has no dates anywhere.
 *
 * A bar covers its last day rather than stopping at that day's left edge. A row
 * that starts and ends on Tuesday must cover Tuesday, not be a zero-width line.
 */
export function barForRange(
  span: { from: string; to: string; derived?: boolean } | null,
  range: TimelineRange,
): Bar | null {
  if (!span) return null;
  return barForSpan(span.from, span.to, range, span.derived ?? false);
}

/**
 * The same geometry from two explicit days, for the bar being DRAGGED.
 *
 * A drag has no committed dates yet — the whole point is that nothing is
 * written until the mouse comes up — so the preview cannot go through `bar()`,
 * which reads the task. One function computes both, because a preview that
 * lands a pixel away from where the bar settles is the tell that there are two
 * geometries.
 */
export function barForSpan(
  from: string,
  to: string,
  range: TimelineRange,
  derived = false,
): Bar {
  const leftPx = dayPx(from, range);
  const rightPx = dayPx(to, range) + range.pxPerDay;
  return {
    leftPx,
    widthPx: Math.max(MIN_BAR_PX, rightPx - leftPx),
    singleDate: from === to,
    derived,
  };
}

export interface MonthCell {
  key: string;
  label: string;
  px: number;
  widthPx: number;
}

const MONTHS = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
];

/** Month header cells across the range, clipped to it at both ends. */
export function monthCells(range: TimelineRange): MonthCell[] {
  const out: MonthCell[] = [];
  let cursor = range.from;
  while (cursor <= range.to) {
    const [year, month] = cursor.split("-").map(Number);
    const firstOfNext = dayKey(new Date(year, month, 1));
    const end = firstOfNext <= range.to ? shiftDay(firstOfNext, -1) : range.to;
    const px = dayPx(cursor, range);
    out.push({
      key: cursor.slice(0, 7),
      label: `${MONTHS[month - 1]} ${year}`,
      px,
      widthPx: dayPx(end, range) + range.pxPerDay - px,
    });
    cursor = firstOfNext;
  }
  return out;
}

const WEEKDAY = ["M", "T", "W", "T", "F", "S", "S"];

export interface DayCell {
  day: string;
  px: number;
  widthPx: number;
  /** Weekday initial — "" at zooms with no room to draw one. */
  label: string;
  /** Day of the month, for the day tier. */
  date: number;
  weekend: boolean;
  today: boolean;
  /** This day starts a Monday week — where the vertical rule goes. */
  weekStart: boolean;
}

/**
 * Every day in the range, with the flags the chart shades by.
 *
 * One pass rather than a predicate called per cell per render, because the
 * chart draws this list three times over — the header tier, the column
 * shading and the gridlines — and they must agree about which column is
 * Saturday. Cheap: a year at quarter zoom is 365 objects, built once per range.
 */
export function dayCells(range: TimelineRange, todayKey: string): DayCell[] {
  const out: DayCell[] = [];
  for (let i = 0; i < range.days; i += 1) {
    const day = shiftDay(range.from, i);
    const offset = mondayOffset(fromDayKey(day));
    out.push({
      day,
      px: i * range.pxPerDay,
      widthPx: range.pxPerDay,
      label: WEEKDAY[offset] as string,
      date: Number(day.slice(8, 10)),
      weekend: offset >= 5,
      today: day === todayKey,
      weekStart: offset === 0,
    });
  }
  return out;
}

export interface WeekCell {
  key: string;
  /** First day of the week, clipped to the range. */
  from: string;
  to: string;
  px: number;
  widthPx: number;
  label: string;
  /** Today falls inside this week. */
  today: boolean;
}

/**
 * Monday weeks across the range, clipped at both ends.
 *
 * The lower header tier at the two narrower zooms, where a per-day cell would
 * be seven pixels of nothing. Clipped rather than overhanging so the first and
 * last cell line up with the chart's own edges — an overhanging week is how a
 * Gantt header ends up one day out of step with the bars underneath it.
 */
export function weekCells(range: TimelineRange, todayKey: string): WeekCell[] {
  const out: WeekCell[] = [];
  let cursor = range.from;
  while (cursor <= range.to) {
    const offset = mondayOffset(fromDayKey(cursor));
    const weekEnd = shiftDay(cursor, 6 - offset);
    const to = weekEnd <= range.to ? weekEnd : range.to;
    const px = dayPx(cursor, range);
    out.push({
      key: cursor,
      from: cursor,
      to,
      px,
      widthPx: dayPx(to, range) + range.pxPerDay - px,
      label: String(Number(cursor.slice(8, 10))),
      today: todayKey >= cursor && todayKey <= to,
    });
    cursor = shiftDay(to, 1);
  }
  return out;
}
