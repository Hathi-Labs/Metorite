/**
 * Timeline block · day keys.
 *
 * A day is a `YYYY-MM-DD` key, read in LOCAL time. `new Date("2026-08-07")` is
 * midnight UTC, which is the 6th west of Greenwich. So each helper here builds
 * a Date through the local constructor and never through `toISOString()`.
 *
 * These four moved from `app/projects/lib/calendar.ts` (STU-D6). That file
 * re-exports them, so each existing import still works.
 */

export const DAY_MS = 86_400_000;

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

/**
 * How many days a week has already run when `date` arrives. Monday is 0.
 *
 * **Monday, not Sunday.** `getDay()` is 0 for Sunday, so the `+ 6` rotation is
 * what makes Monday the start. It is written once, so every grid rotates alike.
 */
export function mondayOffset(date: Date): number {
  return (date.getDay() + 6) % 7;
}
