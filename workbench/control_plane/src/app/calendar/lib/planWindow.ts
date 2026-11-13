/**
 * The time window that "Plan my day" and "Rebuild my day" send to the
 * planner (task 3163).
 *
 * The window starts at the working-day start hour of the target day. The end
 * comes from the "Plan through" choice. A from-now choice ("3 more hours") or
 * a custom clock time can end BEFORE the working day starts: at 00:43 with a
 * 09:00 start, "3 more hours" ends at 03:43. The gateway refuses a window
 * whose end is not after its start (`routes/tasks/calendar.py` `_window_of`),
 * so the member saw "Valid day_start/day_end (ISO) required.".
 *
 * The rule: when the member plans today with a from-now or custom choice, and
 * now is before the working-day start, the window starts now. Any other case
 * keeps the working-day start, so a plan in the middle of the day is the same
 * as before. A window that is still empty gives `null`, and the panel says so
 * in plain words and sends nothing.
 */

/** The "Plan through" choice that plans the standing working hours. */
export const WORK_HORIZON = "work";

/** What the panel shows when the chosen end is not after the start. */
export const EMPTY_WINDOW_TEXT =
  "The time you picked is already over. Pick a later time.";

export function planWindow({
  target,
  dayStartHour,
  end,
  nowMs,
  planningToday,
  horizon,
}: {
  /** The day that the panel plans. */
  target: Date;
  /** The first hour of the working day, 0 to 23. */
  dayStartHour: number;
  /** The end of the window, from the "Plan through" choice. */
  end: Date;
  /** The time of the tap. */
  nowMs: number;
  /** True when the target day is today. */
  planningToday: boolean;
  /** The "Plan through" choice: "work", "custom" or a count of hours. */
  horizon: string;
}): { start: Date; end: Date } | null {
  const start = new Date(target);
  start.setHours(dayStartHour, 0, 0, 0);
  const fromNow = planningToday && horizon !== WORK_HORIZON;
  const begin =
    fromNow && nowMs < start.getTime() ? new Date(nowMs) : start;
  if (end.getTime() <= begin.getTime()) return null;
  return { start: begin, end };
}
