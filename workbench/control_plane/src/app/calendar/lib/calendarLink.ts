/**
 * The Calendar's link to one day, and to a time on it:
 * `/calendar?date=YYYY-MM-DD&at=HH:MM` (WS-58 MD-3a, `my_day_feed.md` §5.8
 * item 9).
 *
 * The My Day feed builds the link (`day_href` in
 * `gateway/routes/shell/feed.py`). This file reads the two names back. The
 * page opens the day view at `date` and scrolls to `at`.
 *
 * Each part is read on its own, and a bad part is ignored:
 *
 * - a bad `date` opens today, as a bare `/calendar` does;
 * - a bad `at` scrolls to the current hour, as a bare `/calendar` does.
 *
 * When neither part is good, the reader answers `null`, and the page does
 * exactly what it does today. `at` is a clock on the member's own day, so it
 * carries no zone.
 *
 * Fence: `calendarLink.test.ts`.
 */

import { HOUR_PX } from "../components/shared";

/** The query names of the link. The gateway writes them by these names. */
export const DATE_PARAM = "date";
export const AT_PARAM = "at";

/** A clock on the grid, in the member's own zone. */
export interface ClockTime {
  hour: number;
  minute: number;
}

/** What a link asks for. A part that was missing or bad is `null`. */
export interface CalendarLink {
  /** Local midnight of the day to open, or `null` for today. */
  date: Date | null;
  /** The time to scroll to, or `null` for the current hour. */
  at: ClockTime | null;
}

const DATE = /^(\d{4})-(\d{2})-(\d{2})$/;
const CLOCK = /^([01]\d|2[0-3]):([0-5]\d)$/;

/** Local midnight of a `YYYY-MM-DD` day, or `null` when it is no real day. */
export function parseLinkDate(raw: string | null | undefined): Date | null {
  const m = DATE.exec((raw ?? "").trim());
  if (!m) return null;
  const [y, mo, d] = [Number(m[1]), Number(m[2]), Number(m[3])];
  const day = new Date(y, mo - 1, d);
  // `new Date` rolls 2026-02-30 over to 2 March, and maps a two-digit year
  // to the 1900s. A day that does not read back the same is not a real day.
  if (day.getFullYear() !== y || day.getMonth() !== mo - 1 || day.getDate() !== d) {
    return null;
  }
  return day;
}

/** An `HH:MM` clock, 24-hour, or `null`. */
export function parseLinkClock(raw: string | null | undefined): ClockTime | null {
  const m = CLOCK.exec((raw ?? "").trim());
  return m ? { hour: Number(m[1]), minute: Number(m[2]) } : null;
}

/** The link a query string names, or `null` when it names nothing good. */
export function calendarLinkFromSearch(
  search: { get(name: string): string | null } | null,
): CalendarLink | null {
  const date = parseLinkDate(search?.get(DATE_PARAM));
  const at = parseLinkClock(search?.get(AT_PARAM));
  return date || at ? { date, at } : null;
}

/**
 * The grid's scroll for a clock: one hour before it, as the page scrolls to
 * one hour before the current time. So the member lands just above the time.
 */
export function scrollTopFor(at: ClockTime, hourPx: number = HOUR_PX): number {
  return Math.max(0, (at.hour - 1 + at.minute / 60) * hourPx);
}
