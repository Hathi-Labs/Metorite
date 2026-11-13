/**
 * Timeline block · the time scale (zoom, window, range).
 *
 * Moved from `app/projects/lib/timeline.ts` (STU-D6). Nothing here knows what a
 * row is. A row is a pair of day keys, or no pair at all.
 */

import { DAY_MS, fromDayKey, shiftDay } from "./days";

/**
 * ── ZOOM (WS-27t S2) ──────────────────────────────────────────────────────
 *
 * One pixels-per-day number was the single worst thing about the first
 * timeline: at 24px a quarter of work is four screens wide, and a one-day task
 * is a 24px stub with no room for its own name. Plane solves it with three
 * fixed zooms rather than a continuous slider (`gantt-chart/data/index.ts`,
 * AGPL-3.0 — read, not copied), and fixed steps are the right call: a slider
 * gives you infinite widths that all need a header layout, and the header is
 * the part that has to stay legible.
 *
 * `tier` is what the LOWER header row draws. Day cells need ~28px to fit a
 * weekday letter and a date, so only the widest zoom gets them.
 *
 * ⚠️ `month` is 24px because that is what the chart has always drawn, and the
 * existing geometry fences assert against `PX_PER_DAY`. Changing the default
 * would have been a silent re-layout hiding inside a feature.
 */
export type TimelineZoom = "week" | "month" | "quarter";

export interface ZoomSpec {
  key: TimelineZoom;
  label: string;
  dayWidth: number;
  /** Granularity of the lower header row. */
  tier: "day" | "week";
}

export const ZOOMS: Record<TimelineZoom, ZoomSpec> = {
  week: { key: "week", label: "Week", dayWidth: 44, tier: "day" },
  month: { key: "month", label: "Month", dayWidth: 24, tier: "week" },
  quarter: { key: "quarter", label: "Quarter", dayWidth: 7, tier: "week" },
};

export const ZOOM_ORDER: readonly TimelineZoom[] = ["week", "month", "quarter"];

export const DEFAULT_ZOOM: TimelineZoom = "month";

/**
 * ── THE WINDOW (WS-27t S3) ────────────────────────────────────────────────
 *
 * How many days of tasks the timeline asks the server for, per zoom.
 *
 * **The timeline used to borrow the CALENDAR's window**, which is one month
 * padded to whole weeks, and that was wrong from the day it shipped — it only
 * became visible once bars could be dragged. Move a task past the end of the
 * month and the reload no longer returns it, so the row silently disappears.
 * A calendar's resource is a month. A timeline's is "the work", and its whole
 * question is what runs alongside what.
 *
 * Sized after Plane's `approxFilterRange` (`gantt-chart/data/index.ts`), which
 * widens the same way: the narrower the zoom, the less calendar fits on screen
 * and the less there is any point fetching.
 *
 * ⚠️ **Capped by the SERVER, not by taste.** `calendar.py` refuses a window
 * over `MAX_WINDOW_DAYS = 400` with a 422 — refuses rather than clamps, so a
 * client that asks for two years draws nothing at all. Every span below is
 * comfortably inside it, and `WINDOW_LIMIT_DAYS` is the mirror that keeps it
 * that way.
 */
export const WINDOW_LIMIT_DAYS = 400;

/** Days either side of the anchor, per zoom. Both ends, so double it. */
const WINDOW_RADIUS: Record<TimelineZoom, number> = {
  week: 60,
  month: 120,
  quarter: 180,
};

export interface TimelineWindow {
  from: string;
  to: string;
}

/** The span of dates the timeline loads at a zoom, centred on a day. */
export function windowFor(zoom: TimelineZoom, anchor: string): TimelineWindow {
  const radius = WINDOW_RADIUS[zoom];
  return { from: shiftDay(anchor, -radius), to: shiftDay(anchor, radius) };
}

/** A window's middle day — what a zoom change re-scopes around. */
export function windowCentre(window: TimelineWindow): string {
  const span = Math.round(
    (fromDayKey(window.to).getTime() - fromDayKey(window.from).getTime()) / DAY_MS,
  );
  return shiftDay(window.from, Math.floor(span / 2));
}

/**
 * The same window, slid or stretched to contain `day`.
 *
 * Dragging a bar to the window's edge and past it is a legitimate reschedule,
 * and the task must not vanish for having been moved somewhere the last fetch
 * did not cover. Stretching is preferred — nothing already on screen leaves it
 * — and the window SLIDES only when stretching would break the server's cap,
 * because a 422 draws an empty chart and a slid window draws the right one.
 */
export function windowIncluding(
  window: TimelineWindow,
  day: string,
): TimelineWindow {
  if (day >= window.from && day <= window.to) return window;
  const from = day < window.from ? day : window.from;
  const to = day > window.to ? day : window.to;
  const span =
    Math.round((fromDayKey(to).getTime() - fromDayKey(from).getTime()) / DAY_MS);
  if (span <= WINDOW_LIMIT_DAYS) return { from, to };
  // Too wide to ask for. Keep the full width and put the new day at the end it
  // ran off, so the thing that was just moved is certainly on screen.
  return day < window.from
    ? { from: day, to: shiftDay(day, WINDOW_LIMIT_DAYS) }
    : { from: shiftDay(day, -WINDOW_LIMIT_DAYS), to: day };
}

/**
 * Chart pixels per calendar day at the DEFAULT zoom.
 *
 * Kept as a named export because the geometry fences read it, and because a
 * chart drawn at one zoom is still the common case. Anything that has a
 * `TimelineRange` in hand must read `range.pxPerDay` instead — that is the
 * value the chart was actually laid out with.
 */
export const PX_PER_DAY = ZOOMS[DEFAULT_ZOOM].dayWidth;

/** Days of breathing room either side of the data's own range. */
export const PAD_DAYS = 7;

export interface TimelineRange {
  from: string;
  to: string;
  days: number;
  widthPx: number;
  /** The zoom this range was laid out at. */
  zoom: TimelineZoom;
  /** Pixels per day at that zoom — every x in this chart derives from it. */
  pxPerDay: number;
}

/** One interval of days, both ends inclusive. */
export interface DaySpan {
  from: string;
  to: string;
}

/** The interval that covers every span given, or `null` for none. */
export function unionSpan(spans: readonly DaySpan[]): DaySpan | null {
  if (spans.length === 0) return null;
  return {
    from: spans.reduce((lo, s) => (s.from < lo ? s.from : lo), spans[0]!.from),
    to: spans.reduce((hi, s) => (s.to > hi ? s.to : hi), spans[0]!.to),
  };
}

/**
 * The date range the chart covers: the data's own span, padded.
 *
 * Fitted to the data rather than to a fixed month. It falls back to a fortnight
 * around today when no span exists, so an empty chart still has an axis.
 *
 * The chart covers the WINDOW when there is one, not just the data. Two bugs
 * come from fitting to the data alone. There is nowhere to drag TO, and the
 * chart re-lays-out under your hand when the earliest task moves. The window
 * and the data are unioned, so a task already loaded is always reachable.
 */
export function rangeForSpans(
  spans: readonly DaySpan[],
  todayKey: string,
  zoom: TimelineZoom = DEFAULT_ZOOM,
  window?: TimelineWindow,
): TimelineRange {
  const all = unionSpan(spans);
  const dataFrom = all ? shiftDay(all.from, -PAD_DAYS) : shiftDay(todayKey, -14);
  const dataTo = all ? shiftDay(all.to, PAD_DAYS) : shiftDay(todayKey, 14);
  const from = window ? (window.from < dataFrom ? window.from : dataFrom) : dataFrom;
  const to = window ? (window.to > dataTo ? window.to : dataTo) : dataTo;
  const days =
    Math.round((fromDayKey(to).getTime() - fromDayKey(from).getTime()) / DAY_MS) + 1;
  const pxPerDay = ZOOMS[zoom].dayWidth;
  return { from, to, days, widthPx: days * pxPerDay, zoom, pxPerDay };
}
