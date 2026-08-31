/**
 * Projects · the timeline, as arithmetic (WS-27t).
 *
 * A Gantt chart is bar geometry plus two rules that are not geometry at all,
 * and both were decisions rather than defaults:
 *
 * * **D-PM-11 — what earns a bar.** Hierarchy depth: top-level tasks get rows,
 *   subtasks fold into their parent and expand on demand. Paca's Timeline
 *   pre-filters to a reserved `Epic` type instead; that was rejected because
 *   `pm_task_types` is per-project data with no reserved names (D-PM-2), so
 *   "Epic" would have to become either a seeded row every project inherits or
 *   a name-match that silently stops working the day somebody renames a type.
 *   `parent_task_id` already says what depth means and cannot be renamed.
 *
 * * **D-PM-12 — an arrow WARNS, it does not push.** A `blocks` edge whose
 *   blocker finishes after the blocked task starts is drawn in the danger tone
 *   and says so. Nothing is rescheduled. Jira drags the dependents forward;
 *   that was rejected because it contradicts WS-27p's "derived and shown, never
 *   enforced" and turns one drag into an unbounded cascade of real `PATCH`es,
 *   each with its own activity row and notification.
 *
 * Dates are `YYYY-MM-DD` keys throughout, for the reason `lib/calendar.ts`
 * gives at length: `new Date("2026-08-07")` is midnight UTC, which is the 6th
 * anywhere west of Greenwich.
 */

import { dayKey, fromDayKey, mondayOffset, shiftDay } from "./calendar";
import { dueInstantForDay } from "./quickAdd";

import type { TaskRow } from "./api";

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

/** Height of one task row, in pixels. Rows are uniform so `y` is index × this. */
export const ROW_H = 40;
/** Height of the bar inside a row, leaving the rest as the row's own padding. */
export const BAR_H = 26;
/** Days of breathing room either side of the data's own range. */
export const PAD_DAYS = 7;
/** Narrowest a bar may be drawn — a one-day task must still be clickable. */
export const MIN_BAR_PX = 10;
/** A bar narrower than this cannot hold its own title, so the label sits beside it. */
export const LABEL_INSIDE_PX = 72;

const DAY_MS = 86_400_000;

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

export interface Bar {
  leftPx: number;
  widthPx: number;
  /** Only one date is known, so the bar is a marker rather than a span. */
  singleDate: boolean;
  /** The interval came from this task's CHILDREN, not from its own dates. */
  derived: boolean;
}

export interface TimelineRow {
  task: TaskRow;
  depth: number;
  /** Subtasks of this row present in the window — drawn when expanded. */
  children: TaskRow[];
}

export interface Edge {
  id: string;
  blocker_id: string;
  blocked_id: string;
}

/** A task's own scheduled interval, or `null` when it has no dates. */
export function interval(task: TaskRow): { from: string; to: string } | null {
  const start = task.start_date ? task.start_date.slice(0, 10) : null;
  const due = task.due_at ? dayKey(new Date(task.due_at)) : null;
  if (!start && !due) return null;
  const a = start ?? (due as string);
  const b = due ?? (start as string);
  // Bad data — due before start — is shown as the span it implies rather than
  // dropped: the timeline is the one view that would have made it obvious.
  return a <= b ? { from: a, to: b } : { from: b, to: a };
}

/**
 * The interval a ROW occupies, folding in its children.
 *
 * A parent with no dates of its own still gets a bar when its subtasks have
 * them — that is the point of grouping by depth, and a parent drawn as a blank
 * row while its children carry the schedule would make the default view look
 * empty. `derived` marks it so the UI can say the dates were not typed here.
 */
export function rowInterval(
  task: TaskRow,
  children: readonly TaskRow[],
): { from: string; to: string; derived: boolean } | null {
  const own = interval(task);
  if (own) return { ...own, derived: false };
  const spans = children.map(interval).filter(Boolean) as { from: string; to: string }[];
  if (spans.length === 0) return null;
  return {
    from: spans.reduce((lo, s) => (s.from < lo ? s.from : lo), spans[0].from),
    to: spans.reduce((hi, s) => (s.to > hi ? s.to : hi), spans[0].to),
    derived: true,
  };
}

/**
 * Group the window's tasks into rows by hierarchy depth (D-PM-11).
 *
 * **A subtask whose parent is not in the window is promoted to a row of its
 * own**, rather than hidden under a parent that is not there. Hiding it would
 * make a filtered timeline silently drop work — the same failure the `undated`
 * count exists to prevent, one level down.
 */
export function timelineRows(tasks: readonly TaskRow[]): TimelineRow[] {
  const present = new Set(tasks.map((t) => t.id));
  const childrenOf = new Map<string, TaskRow[]>();
  const roots: TaskRow[] = [];

  for (const task of tasks) {
    const parent = task.parent_task_id;
    if (parent && present.has(parent)) {
      const kids = childrenOf.get(parent) ?? [];
      kids.push(task);
      childrenOf.set(parent, kids);
    } else {
      roots.push(task);
    }
  }

  return roots.map((task) => ({
    task,
    depth: 0,
    children: childrenOf.get(task.id) ?? [],
  }));
}

/**
 * The date range the chart covers: the data's own span, padded.
 *
 * Fitted to the data rather than to a fixed month, because a timeline's
 * question is "what runs alongside what" and a window that clips the answer is
 * the wrong window. Falls back to a fortnight around today when nothing in the
 * set has a date at all, so an empty chart still has an axis to read.
 */
export function timelineRange(
  rows: readonly TimelineRow[],
  todayKey: string,
  zoom: TimelineZoom = DEFAULT_ZOOM,
  window?: TimelineWindow,
): TimelineRange {
  const spans = rows
    .map((r) => rowInterval(r.task, r.children))
    .filter(Boolean) as { from: string; to: string }[];

  const dataFrom = spans.length
    ? shiftDay(spans.reduce((lo, s) => (s.from < lo ? s.from : lo), spans[0].from), -PAD_DAYS)
    : shiftDay(todayKey, -14);
  const dataTo = spans.length
    ? shiftDay(spans.reduce((hi, s) => (s.to > hi ? s.to : hi), spans[0].to), PAD_DAYS)
    : shiftDay(todayKey, 14);

  /**
   * The chart covers the WINDOW when there is one, not just the data.
   *
   * Two bugs fall out of fitting to the data alone, and both look like the
   * chart is broken rather than the range:
   *
   * 1. There is nowhere to drag TO. A bar dragged past the last dated day goes
   *    past `widthPx`, outside the scrollable area, and vanishes under the
   *    cursor.
   * 2. The chart re-lays-out under your hand. Move the earliest task later and
   *    `from` jumps forward, so every other bar slides left mid-gesture.
   *
   * Unioned rather than replaced, so a task already loaded is always reachable
   * even if the window moved out from under it.
   */
  const from = window ? (window.from < dataFrom ? window.from : dataFrom) : dataFrom;
  const to = window ? (window.to > dataTo ? window.to : dataTo) : dataTo;

  const days =
    Math.round((fromDayKey(to).getTime() - fromDayKey(from).getTime()) / DAY_MS) + 1;
  const pxPerDay = ZOOMS[zoom].dayWidth;
  return { from, to, days, widthPx: days * pxPerDay, zoom, pxPerDay };
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
 * Where a row's bar sits, or `null` when it has no dates anywhere.
 *
 * A bar covers its last day rather than stopping at that day's left edge — a
 * task starting and ending on Tuesday must cover Tuesday, not be a zero-width
 * line at its start.
 */
export function bar(
  task: TaskRow,
  children: readonly TaskRow[],
  range: TimelineRange,
): Bar | null {
  const span = rowInterval(task, children);
  if (!span) return null;
  return barForSpan(span.from, span.to, range, span.derived);
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

/**
 * Does this dependency disagree with the schedule? (D-PM-12)
 *
 * A `blocks` edge asserts the blocker finishes before the blocked task starts.
 * It is violated when the blocker's END is strictly after the blocked task's
 * START — the two overlap, so the sequence the arrow claims cannot happen.
 *
 * **Equal dates are NOT a conflict.** A blocker due on the 10th and a task
 * starting on the 10th is the normal way people schedule a handover; flagging
 * it would make the warning fire on half a healthy plan and be ignored within a
 * week.
 *
 * **An edge with a date missing on either end is not a conflict either** — it
 * is unknowable, and a warning that fires on absent data teaches people that
 * the warning means nothing.
 *
 * **A finished blocker never conflicts.** It has already happened; the dates
 * are history, and WS-27p's rule that a resolved blocker blocks nothing applies
 * to the warning exactly as it applies to the badge.
 */
export function conflicts(
  blocker: Pick<TaskRow, "start_date" | "due_at" | "completed_at">,
  blocked: Pick<TaskRow, "start_date" | "due_at">,
): boolean {
  if (blocker.completed_at) return false;
  const before = interval(blocker as TaskRow);
  const after = interval(blocked as TaskRow);
  if (!before || !after) return false;
  return before.to > after.from;
}

/** A one-sentence explanation of a conflict, for the warning's title. */
export function conflictLabel(blockerTitle: string): string {
  return `Starts before "${blockerTitle}" is due to finish. Nothing has been ` +
    `rescheduled — the dates are yours to fix.`;
}

/**
 * The elbow path from one bar's right edge to another's left edge.
 *
 * Elbowed rather than straight, and routed OUT of the source before turning,
 * because a straight diagonal across six rows crosses every bar between them
 * and stops being followable at exactly the density where you need it.
 *
 * Returns `null` when either end has no bar: an arrow to a task with no dates
 * has nowhere to land, and drawing it to the row's left margin would invent a
 * date the task does not have.
 */
export function edgePath(
  from: { bar: Bar | null; row: number },
  to: { bar: Bar | null; row: number },
): string | null {
  if (!from.bar || !to.bar) return null;
  const y1 = from.row * ROW_H + ROW_H / 2;
  const y2 = to.row * ROW_H + ROW_H / 2;
  const x1 = from.bar.leftPx + from.bar.widthPx;
  const x2 = to.bar.leftPx;
  const stub = 10;

  // Room to route forwards: out, across, in.
  if (x2 >= x1 + stub * 2) {
    const mid = (x1 + x2) / 2;
    return `M ${x1} ${y1} H ${mid} V ${y2} H ${x2}`;
  }
  // The blocked bar starts at or before the blocker ends — the conflict case,
  // and the one a naive path draws backwards through both bars. Route around
  // below/above instead so the arrow stays readable while it is wrong.
  const lane = (Math.max(y1, y2) + ROW_H / 2 + Math.min(y1, y2)) / 2;
  return (
    `M ${x1} ${y1} H ${x1 + stub} V ${lane} H ${x2 - stub} V ${y2} H ${x2}`
  );
}

/**
 * ── WHAT A DRAG WRITES (WS-27t S2) ────────────────────────────────────────
 *
 * Three gestures, three patches, all of them one `PATCH /tasks/{id}` through
 * the page's existing `moveTask` — the same seam the calendar's drag already
 * uses, so a date moved on the timeline produces the same activity row, the
 * same validation and the same revert as one moved on the calendar.
 *
 * **A move reuses `rescheduleTo` outright** rather than growing a second
 * whole-bar shift here. That function already owns the two rules this needed —
 * the span is preserved, and the due TIME of day survives the move — and both
 * are rules a second implementation would get wrong in the same order.
 *
 * ⚠️ **Dragging still never touches anything but the task you dragged.**
 * D-PM-12 says an arrow warns and does not push, and that survives here: move a
 * blocker onto its dependent and the arrow turns red, exactly as it would if
 * you had typed the date. Nothing cascades.
 */

/** A due instant on `day`, keeping the time of day the task already had. */
function dueAtOnDay(day: string, previous: string | null | undefined): string {
  if (!previous) return dueInstantForDay(day);
  const had = new Date(previous);
  const moved = fromDayKey(day);
  moved.setHours(had.getHours(), had.getMinutes(), had.getSeconds(), had.getMilliseconds());
  return moved.toISOString();
}

/**
 * ⚠️ **A resize moves ONE edge and PINS the other.** This is the whole rule,
 * and getting it half-right is the bug the owner hit on 2026-08-31.
 *
 * A task with only a due date draws as a one-day bar on that day. Pull its
 * right edge three days out and the obvious implementation writes
 * `due_at += 3` — which is correct as far as it goes, and completely wrong as a
 * gesture: the task still has no start date, so it is still a ONE-DAY bar,
 * three days later. The bar does not grow. **It jumps.** The same defect
 * mirrors on a start-only task pulled by its left edge.
 *
 * So when the far edge has no stored date, the resize writes it, using where
 * the bar already is. Extending a point makes a span; it never relocates the
 * point. `previewBar` in the view has always drawn it this way — which is why
 * the bar looked right during the drag and moved on release.
 */

/**
 * Drag the LEFT edge to `day` — the task starts then.
 *
 * **Clamped, never swapped.** Pushing the start past the due date could either
 * flip the interval or stop at it; stopping is right, because a bar that turns
 * inside out under the cursor is not something anybody meant to ask for, and
 * the inverted span would then be written as real dates.
 */
export function resizeStart(
  task: TaskRow,
  day: string,
): { start_date: string; due_at?: string } | null {
  const span = interval(task);
  if (!span) return null;
  const capped = day > span.to ? span.to : day;
  const current = task.start_date ? task.start_date.slice(0, 10) : null;
  if (capped === current) return null;
  // Pin the right edge. Without a stored `due_at` the bar's right edge is only
  // implied by the start, so moving the start would carry it along.
  return task.due_at
    ? { start_date: capped }
    : { start_date: capped, due_at: dueInstantForDay(span.to) };
}

/**
 * Drag the RIGHT edge to `day` — the task is due then.
 *
 * Clamped at the start for the same reason. The time of day is preserved when
 * there was one.
 */
export function resizeEnd(
  task: TaskRow,
  day: string,
): { due_at: string; start_date?: string } | null {
  const span = interval(task);
  if (!span) return null;
  const floored = day < span.from ? span.from : day;
  const current = task.due_at ? dayKey(new Date(task.due_at)) : null;
  if (floored === current) return null;
  const due_at = dueAtOnDay(floored, task.due_at);
  // Pin the left edge — the case the owner reported.
  return task.start_date ? { due_at } : { due_at, start_date: span.from };
}

/**
 * Draw a span on an UNSCHEDULED row — the task runs `from` to `to`.
 *
 * The row of a task with no dates is otherwise dead space carrying the word
 * "unscheduled", which names the problem and offers nothing. Dragging across it
 * is the shortest path from "no dates" to "these dates", and it writes both
 * ends at once so the task never passes through a half-scheduled state.
 */
export function spanFor(
  from: string,
  to: string,
): { start_date: string; due_at: string } {
  const [a, b] = from <= to ? [from, to] : [to, from];
  return { start_date: a, due_at: dueInstantForDay(b) };
}

/**
 * Why this bar cannot be dragged, or `null` when it can.
 *
 * A DERIVED bar has no dates of its own — the span is its children's — so there
 * is no field for a drag to write. Dragging it would have to either invent
 * dates on the parent or silently rewrite every child, and both are worse than
 * refusing. This is the same rule the link handle already applied, said once
 * and now read by both.
 */
export function dragRefusal(barToDrag: Bar | null): string | null {
  if (!barToDrag) return "This task has no dates to move.";
  if (barToDrag.derived) {
    return "These dates come from the subtasks. Move those instead.";
  }
  return null;
}

/**
 * May this drag create a link?
 *
 * Only the cheap, local refusals — a task cannot block itself, and an edge that
 * already exists is a no-op rather than a duplicate. **The cycle check is NOT
 * duplicated here**: `assert_no_block_cycle` owns it, bounded and tested, and a
 * second implementation in the browser would be the one that drifts. The drop
 * posts and reports the gateway's own refusal message.
 */
export function canLink(
  blockerId: string,
  blockedId: string,
  existing: readonly Edge[],
): { ok: true } | { ok: false; reason: string } {
  if (blockerId === blockedId) {
    return { ok: false, reason: "A task cannot block itself." };
  }
  if (existing.some((e) => e.blocker_id === blockerId && e.blocked_id === blockedId)) {
    return { ok: false, reason: "That dependency is already there." };
  }
  return { ok: true };
}
