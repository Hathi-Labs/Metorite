// Shared timeboxing geometry — the pure date/slot helpers used by BOTH the
// calendar grid (CalendarView) and the Schedule popup (SchedulePopup), so
// "when is the next free slot" is computed one way everywhere. No React, no
// store — just Date math over the already-loaded items.

import { MyTask } from "./types";

/** Default block length (min) when a task has no time estimate. */
export const DEFAULT_BLOCK_MINS = 30;

/** Midnight local of the given date. */
export const startOfDay = (d: Date) =>
  new Date(d.getFullYear(), d.getMonth(), d.getDate());

/** `d` shifted by `n` whole days (from its local midnight). */
export const addDays = (d: Date, n: number) => {
  const x = startOfDay(d);
  x.setDate(x.getDate() + n);
  return x;
};

/** Same calendar day (local). */
export const sameDay = (a: Date, b: Date) =>
  a.getFullYear() === b.getFullYear() &&
  a.getMonth() === b.getMonth() &&
  a.getDate() === b.getDate();

/** A scheduled task resolved to a concrete start/end block. */
export type Block = { item: MyTask; start: Date; end: Date };

/** Timeboxed blocks that fall on `day` (end defaults to start + estimate). */
export function blocksForDay(items: MyTask[], day: Date): Block[] {
  const out: Block[] = [];
  for (const item of items) {
    if (!item.scheduledStart) continue;
    const start = new Date(item.scheduledStart);
    if (!sameDay(start, day)) continue;
    const end = item.scheduledEnd
      ? new Date(item.scheduledEnd)
      : new Date(
          start.getTime() +
            (item.timeEstimateMins ?? DEFAULT_BLOCK_MINS) * 60000,
        );
    out.push({ item, start, end });
  }
  return out.sort((a, b) => a.start.getTime() - b.start.getTime());
}

/** First 30-min-aligned free slot on `day` at/after the window start (or now, if
 *  today), that fits `mins` without overlapping an existing block. */
export function firstFreeSlot(
  dayBlocks: Block[],
  day: Date,
  mins: number,
  startHour: number,
  endHour: number,
): Date {
  const now = new Date();
  const earliest = new Date(day);
  earliest.setHours(startHour, 0, 0, 0);
  if (sameDay(day, now) && now > earliest) {
    // round up to the next 30
    const m = now.getMinutes();
    earliest.setHours(now.getHours(), m <= 30 ? 30 : 60, 0, 0);
  }
  const dayEnd = new Date(day);
  dayEnd.setHours(endHour, 0, 0, 0);
  let cursor = earliest;
  const sorted = [...dayBlocks].sort(
    (a, b) => a.start.getTime() - b.start.getTime(),
  );
  for (const b of sorted) {
    if (b.end <= cursor) continue;
    if (cursor.getTime() + mins * 60000 <= b.start.getTime()) break; // fits before b
    if (b.end > cursor) cursor = new Date(b.end);
  }
  if (cursor.getTime() + mins * 60000 > dayEnd.getTime()) return earliest; // overflow → stack at top
  return cursor;
}
