/**
 * Projects · the calendar grid, as arithmetic (WS-27q; week layout WS-27ac).
 *
 * Every decision a month view makes that can be wrong without looking wrong —
 * which days a grid covers, which cells a task occupies, what dragging it to a
 * day should write — lives here as a pure function, because a calendar bug is
 * almost never a crash. It is a task on the wrong Tuesday, and the only way to
 * catch that is to assert on the arithmetic rather than to look at it.
 *
 * **WS-27ac added a WEEK layout to this arithmetic, not beside it.** A second
 * week-math helper is precisely the defect this module exists to prevent: two
 * implementations agree right up until one of them is fixed, and the surface
 * that disagrees is the one nobody is looking at. `monthGrid` and `weekGrid`
 * share `mondayOffset` (which day starts the week) and `runOfDays` (how a run
 * of days is walked), so the week grid draws *exactly* the row the month grid
 * would have drawn for the same date — asserted in `calendar.test.ts` rather
 * than assumed. Everything downstream (`calendarWindow`, `taskDays`,
 * `placeTasks`, `rescheduleTo`) reads `grid.days` and so never learned there
 * was a second layout at all.
 *
 * **Dates are handled as `YYYY-MM-DD` keys, not as `Date` objects, wherever a
 * DAY is meant.** `new Date("2026-08-07")` is midnight UTC, which is the 6th in
 * any western timezone — the single most common way a calendar loses a day. A
 * `Date` is used only for the arithmetic of walking a month, always through
 * local-time constructors and accessors, never through `toISOString()`.
 */

import type { TaskRow } from "./api";

const DAY_MS = 86_400_000;

/** `YYYY-MM-DD` for a Date, read in LOCAL time. */
export function dayKey(date: Date): string {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

/** A `YYYY-MM-DD` key back to a local-midnight Date. */
export function fromDayKey(key: string): Date {
  const [y, m, d] = key.split("-").map(Number);
  return new Date(y, (m ?? 1) - 1, d ?? 1);
}

/** `n` days after a key, as a key. Month and year roll over. */
export function shiftDay(key: string, days: number): string {
  const date = fromDayKey(key);
  date.setDate(date.getDate() + days);
  return dayKey(date);
}

/** Which shape of grid a viewer asked for. */
export type CalendarLayout = "month" | "week";

export interface CalendarGrid {
  /** Which layout built it — the one thing downstream code may branch on. */
  layout: CalendarLayout;
  /**
   * The month the grid is *about*, as `YYYY-MM`.
   *
   * For a week that straddles a month boundary this is the month its MONDAY
   * falls in, so `grid.month` never becomes a reason to dim half a week: see
   * `isPadding`, which is the only reader that cares.
   */
  month: string;
  /** Every day drawn, in order — always whole weeks. */
  days: string[];
  /** Rows of seven. */
  weeks: string[][];
}

/**
 * How many days a week has already run when `date` arrives — Monday is 0.
 *
 * **Monday, not Sunday.** The workspace this is for runs a Monday week, and a
 * grid whose weekend is split across two rows makes "what is left this week" a
 * question you have to count rather than see. `getDay()` is 0=Sunday, so the
 * `+ 6` rotation is what makes Monday the start — and it is written ONCE,
 * because a month grid and a week grid that rotate differently draw the same
 * seven days in two different orders.
 */
export function mondayOffset(date: Date): number {
  return (date.getDay() + 6) % 7;
}

/** `count` consecutive day keys from `startKey`, and the same run in rows of
 *  seven. The one place a grid's days are enumerated. */
function runOfDays(startKey: string, count: number): Pick<CalendarGrid, "days" | "weeks"> {
  const days: string[] = [];
  for (let i = 0; i < count; i += 1) days.push(shiftDay(startKey, i));
  const weeks: string[][] = [];
  for (let i = 0; i < days.length; i += 7) weeks.push(days.slice(i, i + 7));
  return { days, weeks };
}

/**
 * The days a month view draws, padded to whole weeks from Monday.
 *
 * The grid is padded to *whole weeks only* — not to a fixed six rows. A fixed
 * six always shows days from two neighbouring months and, in a 28-day February
 * starting on a Monday, an entire extra week of March. Padding to the week
 * boundary is the smallest grid that is still rectangular.
 */
export function monthGrid(anchor: Date): CalendarGrid {
  const year = anchor.getFullYear();
  const month = anchor.getMonth();
  const first = new Date(year, month, 1);
  const last = new Date(year, month + 1, 0);

  const leading = mondayOffset(first);
  const trailing = 6 - mondayOffset(last);

  return {
    layout: "month",
    month: `${year}-${String(month + 1).padStart(2, "0")}`,
    ...runOfDays(shiftDay(dayKey(first), -leading), leading + last.getDate() + trailing),
  };
}

/**
 * The seven days of the Monday week containing `anchor` (WS-27ac).
 *
 * **This is the month grid's own row, not a second calculation.** It walks back
 * to Monday with the SAME `mondayOffset` the month grid pads with and enumerates
 * with the SAME `runOfDays`, which is why `weekGrid(d).days` is byte-identical
 * to the `monthGrid` week containing `d`. Written independently — "start of week
 * is `date - date.getDay() + 1`" is the usual form — it would disagree on
 * Sundays, and only on Sundays, which is the bug that survives a demo.
 */
export function weekGrid(anchor: Date): CalendarGrid {
  const monday = shiftDay(dayKey(anchor), -mondayOffset(anchor));
  return { layout: "week", month: monday.slice(0, 7), ...runOfDays(monday, 7) };
}

/** The grid for a layout — the one call a surface makes. */
export function calendarGrid(layout: CalendarLayout, anchor: Date): CalendarGrid {
  return layout === "week" ? weekGrid(anchor) : monthGrid(anchor);
}

/**
 * The window to ask the server for, with a day of slack on each side.
 *
 * **The slack is not defensive padding, it is the contract.** The server reads
 * the window in UTC because a `start_date` is a floating date and a `due_at` is
 * an instant, and no single frame makes both exact. So it OVER-selects and the
 * browser — the only party that knows the viewer's timezone — does the
 * placement. Without the extra day, a viewer in UTC+5:30 loses every task due
 * in the first 5½ hours of the grid's first day.
 *
 * `to` is EXCLUSIVE, matching the endpoint's half-open window, so two
 * consecutive months tile instead of both claiming the tasks on the boundary.
 */
export function calendarWindow(grid: CalendarGrid): { from: string; to: string } {
  const first = grid.days[0];
  const last = grid.days[grid.days.length - 1];
  return { from: shiftDay(first, -1), to: shiftDay(last, 2) };
}

/**
 * The day keys a task occupies, clamped to the grid.
 *
 * A task with both dates is a BAR and belongs on every day it covers — the
 * whole reason the endpoint filters on overlap. A task with one date is a
 * single day. A task with neither is nowhere, and returns an empty list rather
 * than being placed on today, which would be a task appearing on a day nobody
 * scheduled it for.
 *
 * Clamping matters as much as the span: a task running June to December must
 * occupy all of August's cells and none outside them, and an unclamped range
 * would try to render 180 cells that do not exist.
 */
export function taskDays(task: TaskRow, grid: CalendarGrid): string[] {
  const startKey = task.start_date ? task.start_date.slice(0, 10) : null;
  // `due_at` is an instant, so it is read in the VIEWER's timezone — that is
  // the day they would say it is due. `start_date` is a floating date and is
  // taken as written; converting it through a Date would move it.
  const dueKey = task.due_at ? dayKey(new Date(task.due_at)) : null;
  if (!startKey && !dueKey) return [];

  const from = startKey ?? (dueKey as string);
  const to = dueKey ?? (startKey as string);
  // A due date before the start date is bad data, not a reason to render
  // nothing: show it on both endpoints rather than swallowing the task.
  const lo = from <= to ? from : to;
  const hi = from <= to ? to : from;

  const gridFrom = grid.days[0];
  const gridTo = grid.days[grid.days.length - 1];
  const first = lo > gridFrom ? lo : gridFrom;
  const lastDay = hi < gridTo ? hi : gridTo;
  if (first > lastDay) return [];

  const out: string[] = [];
  const span = Math.round(
    (fromDayKey(lastDay).getTime() - fromDayKey(first).getTime()) / DAY_MS,
  );
  for (let i = 0; i <= span; i += 1) out.push(shiftDay(first, i));
  return out;
}

/** Tasks bucketed by day key. Every grid day gets an entry, empty or not. */
export function placeTasks(
  tasks: readonly TaskRow[],
  grid: CalendarGrid,
): Map<string, TaskRow[]> {
  const byDay = new Map<string, TaskRow[]>(grid.days.map((d) => [d, []]));
  for (const task of tasks) {
    for (const day of taskDays(task, grid)) byDay.get(day)?.push(task);
  }
  return byDay;
}

/**
 * The PATCH that moves a task to a day, or `null` if it is already there.
 *
 * **Dragging a bar moves the whole bar.** A task starting Monday and due Friday
 * dropped on Wednesday runs Wednesday to Sunday — the span is the estimate
 * somebody made, and a drag that silently shortens it to a single day destroys
 * information the user did not offer to change. This is the rule every
 * calendar-drag implementation gets wrong first, by writing only the date the
 * card was dropped on and leaving the other end where it was, which inverts
 * the interval as soon as you drag left.
 *
 * The `due_at` TIME OF DAY is preserved for the same reason: "due Friday at 5"
 * dragged to Monday is due Monday at 5, not Monday at midnight.
 *
 * Returns `null` for a no-op so a drop that did not move anything writes
 * nothing — an activity row saying a task moved from Tuesday to Tuesday is
 * noise in the one place people go to find out what changed.
 */
/**
 * The day a whole-bar move is measured FROM: the start when there is one, the
 * due day otherwise.
 *
 * Exported because the timeline's drag is a delta — "shift this bar three days"
 * — and has to land the anchor on `anchor + 3`. It must agree with
 * `rescheduleTo` about which day that is, so it reads the same function rather
 * than re-deriving the rule and disagreeing on start-less tasks.
 */
export function anchorDay(task: TaskRow): string | null {
  if (task.start_date) return task.start_date.slice(0, 10);
  return task.due_at ? dayKey(new Date(task.due_at)) : null;
}

export function rescheduleTo(
  task: TaskRow,
  day: string,
): { start_date?: string | null; due_at?: string | null } | null {
  const startKey = task.start_date ? task.start_date.slice(0, 10) : null;
  const dueDate = task.due_at ? new Date(task.due_at) : null;
  const dueKey = dueDate ? dayKey(dueDate) : null;
  if (!startKey && !dueKey) return null;

  const anchor = anchorDay(task) as string;
  if (anchor === day) return null;

  const offset = Math.round(
    (fromDayKey(day).getTime() - fromDayKey(anchor).getTime()) / DAY_MS,
  );
  const patch: { start_date?: string | null; due_at?: string | null } = {};
  // The anchor IS the start when there is one, so the new start is simply the
  // day it was dropped on. Written as `shiftDay(startKey, offset)` first, which
  // is the same value by construction and reads as if it could differ.
  if (startKey) patch.start_date = day;
  if (dueKey && dueDate) {
    const moved = fromDayKey(shiftDay(dueKey, offset));
    moved.setHours(
      dueDate.getHours(), dueDate.getMinutes(),
      dueDate.getSeconds(), dueDate.getMilliseconds(),
    );
    patch.due_at = moved.toISOString();
  }
  return patch;
}

const MONTHS = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

/** "7 August 2026" — a day key in words, without a `Date` in sight. */
function dayLabel(key: string, withMonth = true, withYear = true): string {
  const [year, month, day] = key.split("-").map(Number);
  const parts = [String(day ?? 1)];
  if (withMonth) parts.push(MONTHS[(month ?? 1) - 1] as string);
  if (withYear) parts.push(String(year));
  return parts.join(" ");
}

/**
 * The grid's heading — "August 2026", or the week's own span.
 *
 * A week heading names BOTH ends and repeats the month or year only when they
 * differ ("31 August – 6 September 2026"). A week labelled with one month is
 * the heading that quietly lies for five weeks of every year.
 */
export function gridLabel(grid: CalendarGrid): string {
  if (grid.layout === "month") {
    const [year, month] = grid.month.split("-").map(Number);
    return `${MONTHS[(month ?? 1) - 1]} ${year}`;
  }
  const first = grid.days[0] as string;
  const last = grid.days[grid.days.length - 1] as string;
  const sameYear = first.slice(0, 4) === last.slice(0, 4);
  const sameMonth = first.slice(0, 7) === last.slice(0, 7);
  return `${dayLabel(first, !sameMonth, !sameYear)} – ${dayLabel(last)}`;
}

/**
 * True when a cell is only there to keep the grid rectangular.
 *
 * Padding exists in a MONTH grid, which pads to whole weeks and so draws days
 * belonging to a neighbour. A week grid has none: every one of its seven days
 * is the subject, and dimming the ones after a month boundary would grey out
 * half of five weeks a year for no reason a viewer could name.
 */
export function isPadding(day: string, grid: CalendarGrid): boolean {
  return grid.layout === "month" && day.slice(0, 7) !== grid.month;
}

/**
 * The anchor `steps` periods from this grid — months for a month grid, weeks
 * for a week grid. One stepper, so "next" always means one of whatever is on
 * screen.
 *
 * The month branch anchors on the 1st: `setMonth(+1)` on the 31st gives March
 * 3rd, which is how a "next month" button skips February.
 */
export function shiftGrid(grid: CalendarGrid, steps: number): Date {
  if (grid.layout === "week") {
    return fromDayKey(shiftDay(grid.days[0] as string, steps * 7));
  }
  const [year, month] = grid.month.split("-").map(Number);
  return new Date(year, (month ?? 1) - 1 + steps, 1);
}

/**
 * How many task chips a day cell shows before it collapses the rest.
 *
 * A month cell is one seventh of a row of six; a week cell is one seventh of
 * the whole canvas and can honestly show more. These are the only two numbers,
 * kept here rather than in the component so the overflow arithmetic and the
 * limit it applies cannot drift apart.
 */
export const DAY_LIMITS: Record<CalendarLayout, number> = { month: 3, week: 8 };

export interface DayFill {
  /** Chips drawn in the cell. */
  shown: number;
  /** Chips folded behind "+N more" — the TRUE remainder, never a cap. */
  hidden: number;
}

/**
 * How a day cell's `count` tasks split into drawn and folded (WS-27ac).
 *
 * **`hidden` is `count - shown` and nothing else.** The failure this replaces
 * is the cell that simply renders its first three and stops: a day with eleven
 * tasks looks exactly like a day with three, and a calendar that under-reports
 * is worse than one that admits it is full — the same honesty `truncated` and
 * `undated` already carry at the window level.
 *
 * **One over the limit is not folded.** A "+1 more" row occupies the row it
 * would have saved, so folding there costs a click and buys nothing. The rule
 * fires at `limit + 2`, which is the first count where folding actually frees
 * space. Expanded, a cell shows everything and reports nothing hidden — the
 * expansion is the answer, and leaving a stale "+N more" under it would be a
 * count of tasks the viewer is already looking at.
 */
export function dayFill(count: number, limit: number, expanded = false): DayFill {
  if (expanded || count <= limit + 1) return { shown: count, hidden: 0 };
  return { shown: limit, hidden: count - limit };
}

/**
 * Why this task cannot be dropped on a day, or `null` (WS-27ac, WS-27y's rule).
 *
 * Distinct from `board.dropRefusal`, which answers a question about the AXIS
 * ("this column is computed, nothing can be dropped into it"); this one is
 * about the TASK. Two questions, two functions, and neither surface has to know
 * the other's.
 *
 * A drop that cannot be honoured must SAY so. Both cases below currently end in
 * `rescheduleTo` returning `null`, i.e. in nothing happening at all, which
 * reads as a broken drag rather than as a refusal — and a gesture that
 * sometimes silently does nothing is a gesture people stop trusting everywhere.
 */
export function dayDropRefusal(task: TaskRow | undefined): string | null {
  if (!task) {
    // Dragged from a surface this calendar's window does not cover, so there
    // is no row to patch and no way to tell whether the write would be legal.
    return "That task is not on this calendar.";
  }
  if (!task.start_date && !task.due_at) {
    // Which of the two dates would a drop set? Guessing writes whichever the
    // implementation happened to prefer — see `rescheduleTo`'s own refusal.
    return "This task has no start or due date, so there is no schedule to move.";
  }
  return null;
}
