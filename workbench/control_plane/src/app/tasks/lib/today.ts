/**
 * Tasks · the Today rule (WS-64 OP-2, `one_projects_app.md` §9 OP-2 item 3).
 *
 * No server filter for "Today" exists, so it is a client rule over the
 * hydrated lens. ONE pure function owns it, so My work in Projects and
 * My Tasks cannot disagree. Fence: `today.test.ts`.
 *
 * A task is in Today when ALL of these hold:
 * - it is open (not done, not archived, not trashed);
 * - its disposition is NEXT and it is mine. The lens already derives the
 *   disposition by D53, so a stated and a derived NEXT read the same here;
 * - it is not deferred past today (a `deferUntil` that falls on a later day);
 * - its start date has come, as `isTickled` reads it, and it has a date:
 *   `dueAt` or `startDate` on or before today. An overdue task is in Today.
 *
 * "Today" is the date of `GET /projects/my/today`, in the member's zone.
 */

import { localDate } from "./utils";
import type { MyTask } from "./types";

type TodayFields = Pick<
  MyTask,
  "disposition" | "isMine" | "archivedAt" | "completedAt" | "dueAt" | "startDate" | "deferUntil"
>;

/** The calendar day of an ISO date or instant, in the member's zone. */
function dayOf(iso: string, timeZone?: string): string | null {
  if (/^\d{4}-\d{2}-\d{2}$/.test(iso)) return iso;
  const ms = Date.parse(iso);
  return Number.isNaN(ms) ? null : localDate(ms, timeZone);
}

/** Is this task in the member's Today? `today` is "YYYY-MM-DD". */
export function isInToday(item: TodayFields, today: string, timeZone?: string): boolean {
  if (item.archivedAt || item.completedAt) return false;
  if (item.disposition !== "NEXT" || !item.isMine) return false;
  if (item.deferUntil) {
    const deferred = dayOf(item.deferUntil, timeZone);
    if (deferred && deferred > today) return false;
  }
  const start = item.startDate ? dayOf(item.startDate, timeZone) : null;
  if (start && start > today) return false;
  const due = item.dueAt ? dayOf(item.dueAt, timeZone) : null;
  return Boolean((start && start <= today) || (due && due <= today));
}

/** The rows of Today, in the order they came. */
export function todayRows<T extends TodayFields>(
  items: readonly T[],
  today: string,
  timeZone?: string,
): T[] {
  return items.filter((item) => isInToday(item, today, timeZone));
}
