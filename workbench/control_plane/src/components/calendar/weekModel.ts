/** The pure model of a Day or Week time grid. It holds no React and no clock.
 *  The caller passes `now`. All times are the local time zone of the viewer,
 *  as in Month and in the Calendar reminders. The week starts on Monday,
 *  because `startOfWeek` in the Calendar app uses a Monday start.
 *
 *  The minute to pixel formula is the formula of `TimeGrid.tsx`. A golden test
 *  holds the two in step until the shared geometry moves out of `TimeGrid`. */
import {
  DAY_END_HOUR,
  DAY_START_HOUR,
  DOW,
  HOUR_PX,
  startOfWeek,
} from "@/app/calendar/components/shared";
import { addDays, startOfDay } from "@/app/tasks/lib/scheduling";
import { layoutSpans } from "./layout";
import type { GridEvent } from "./types";

export type WeekMode = "day" | "week";
export type HourWindow = { startHour: number; endHour: number };
export type WeekDay = { date: Date; key: string };

export type TimedBlock = {
  id: string;
  topPx: number;
  heightPx: number;
  lane: number;
  lanes: number;
  /** Minutes into the day, on the wall clock, after the window clip. */
  startMin: number;
  endMin: number;
  /** The event goes on before this block (a midnight or a window edge). */
  clippedStart: boolean;
  /** The event goes on after this block. */
  clippedEnd: boolean;
};

export type AllDayBar = {
  id: string;
  startCol: number;
  span: number;
  clippedLeft: boolean;
  clippedRight: boolean;
};

export type OutOfWindow = { id: string; dayIndex: number; side: "before" | "after" };

export type WeekModel = {
  days: WeekDay[];
  window: HourWindow;
  hourPx: number;
  timed: { dayIndex: number; blocks: TimedBlock[] }[];
  allDay: AllDayBar[];
  /** Timed parts that lie wholly outside a window that the caller fixed. */
  outOfWindow: OutOfWindow[];
};

const DAY_MINS = 24 * 60;
/** The least height of a block, as in `TimeGrid`. */
const MIN_BLOCK_PX = 20;

const valid = (d: Date) => d instanceof Date && !Number.isNaN(d.getTime());
const wallMin = (d: Date) => d.getHours() * 60 + d.getMinutes();
const pad = (n: number) => String(n).padStart(2, "0");
const dayKey = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;

/** An all-day row event: flagged all day, or 24 hours or more long. */
function isAllDay(e: GridEvent): boolean {
  return e.allDay || e.end.getTime() - e.start.getTime() >= DAY_MINS * 60000;
}

/** The part of a timed event on one local day, as wall-clock minutes. */
function segmentOn(e: GridEvent, day: Date): { s: number; e: number } | null {
  const next = addDays(day, 1);
  const end = e.end.getTime() < e.start.getTime() ? e.start : e.end;
  const startsToday = e.start >= day && e.start < next;
  if (!startsToday && !(e.start < day && end > day)) return null;
  const s = e.start < day ? 0 : wallMin(e.start);
  const t = end >= next ? DAY_MINS : wallMin(end);
  return { s, e: Math.max(s, t) };
}

/** Widen the hour window to hold every timed event. It is never narrower than
 *  the defaults. The hours are whole and lie in 0 to 24. */
export function hourWindow(
  events: GridEvent[],
  defaultStart: number = DAY_START_HOUR,
  defaultEnd: number = DAY_END_HOUR,
): HourWindow {
  let start = Math.min(24, Math.max(0, Math.floor(Number.isFinite(defaultStart) ? defaultStart : DAY_START_HOUR)));
  let end = Math.min(24, Math.max(0, Math.ceil(Number.isFinite(defaultEnd) ? defaultEnd : DAY_END_HOUR)));
  if (end <= start) {
    if (start >= 24) start = 23;
    end = start + 1;
  }
  for (const e of events) {
    if (!valid(e.start) || !valid(e.end) || isAllDay(e)) continue;
    const lastDay = startOfDay(e.end.getTime() > e.start.getTime() ? new Date(e.end.getTime() - 1) : e.start);
    for (let d = startOfDay(e.start); d <= lastDay; d = addDays(d, 1)) {
      const seg = segmentOn(e, d);
      if (!seg) continue;
      start = Math.min(start, Math.floor(seg.s / 60));
      end = Math.max(end, Math.ceil(seg.e / 60));
    }
  }
  return { startHour: Math.max(0, start), endHour: Math.min(24, end) };
}

/** The pixel height of the hour window. */
export const windowHeight = (w: HourWindow, hourPx: number = HOUR_PX) =>
  (w.endHour - w.startHour) * hourPx;

/** Build the days, the timed blocks and the all-day bars of a Day or Week. */
export function buildWeek(
  events: GridEvent[],
  opts: { anchor: Date; mode: WeekMode; window?: HourWindow; hourPx?: number },
): WeekModel {
  const anchor = valid(opts.anchor) ? opts.anchor : new Date(1970, 0, 5);
  const hourPx = opts.hourPx && opts.hourPx > 0 ? opts.hourPx : HOUR_PX;
  const usable = events.filter((e) => valid(e.start) && valid(e.end));
  const window = opts.window ?? hourWindow(usable);
  const first = opts.mode === "week" ? startOfWeek(anchor) : startOfDay(anchor);
  const count = opts.mode === "week" ? 7 : 1;
  const days: WeekDay[] = [];
  for (let i = 0; i < count; i++) {
    const date = addDays(first, i);
    days.push({ date, key: dayKey(date) });
  }
  const winStart = window.startHour * 60;
  const winEnd = window.endHour * 60;
  const winPx = windowHeight(window, hourPx);

  const allDay: AllDayBar[] = [];
  const timed = days.map((_, dayIndex) => ({ dayIndex, blocks: [] as TimedBlock[] }));
  const outOfWindow: OutOfWindow[] = [];
  const firstDay = days[0].date;
  const lastDay = days[days.length - 1].date;

  for (const e of usable) {
    if (!isAllDay(e)) continue;
    const evFirst = startOfDay(e.start);
    const evLast = startOfDay(e.end > e.start ? new Date(e.end.getTime() - 1) : e.start);
    let startCol = -1;
    let endCol = -1;
    days.forEach((d, i) => {
      if (d.date >= evFirst && d.date <= evLast) {
        if (startCol < 0) startCol = i;
        endCol = i;
      }
    });
    if (startCol < 0) continue;
    allDay.push({
      id: e.id,
      startCol,
      span: endCol - startCol + 1,
      clippedLeft: evFirst < firstDay,
      clippedRight: evLast > lastDay,
    });
  }
  allDay.sort(
    (a, b) => a.startCol - b.startCol || b.span - a.span || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0),
  );

  type Seg = {
    id: string;
    start: Date;
    end: Date;
    s: number;
    e: number;
    clippedStart: boolean;
    clippedEnd: boolean;
  };
  days.forEach((wd, dayIndex) => {
    const day = wd.date;
    const segs: Seg[] = [];
    for (const e of usable) {
      if (isAllDay(e)) continue;
      const raw = segmentOn(e, day);
      if (!raw) continue;
      const before = raw.e <= winStart && raw.s < winStart;
      const after = raw.s >= winEnd && raw.e > winEnd;
      if (before || after) {
        outOfWindow.push({ id: e.id, dayIndex, side: before ? "before" : "after" });
        continue;
      }
      const s = Math.max(raw.s, winStart);
      const t = Math.min(raw.e, winEnd);
      // The lane span is as long as the least block, as in `TimeGrid`.
      const laneEnd = Math.max(t, s + (MIN_BLOCK_PX / hourPx) * 60);
      segs.push({
        id: e.id,
        start: new Date(s * 60000),
        end: new Date(laneEnd * 60000),
        s,
        e: t,
        clippedStart: e.start < day || raw.s < winStart,
        clippedEnd: e.end >= addDays(day, 1) || raw.e > winEnd,
      });
    }
    for (const { block: b, lane, lanes } of layoutSpans(segs)) {
      const topPx = Math.max(0, ((b.s - winStart) / 60) * hourPx);
      const natural = Math.max(MIN_BLOCK_PX, ((b.e - b.s) / 60) * hourPx - 2);
      // A block never leaves the grid, so a late block can end shorter.
      const heightPx = Math.max(0, Math.min(natural, winPx - topPx));
      timed[dayIndex].blocks.push({
        id: b.id,
        topPx,
        heightPx,
        lane,
        lanes,
        startMin: b.s,
        endMin: b.e,
        clippedStart: b.clippedStart,
        clippedEnd: b.clippedEnd,
      });
    }
  });

  return { days, window, hourPx, timed, allDay, outOfWindow };
}

/** The index of the day that holds `now`, or null when no visible day does. */
export function nowDayIndex(days: WeekDay[], now: Date): number | null {
  if (!valid(now)) return null;
  const key = dayKey(now);
  const i = days.findIndex((d) => d.key === key);
  return i < 0 ? null : i;
}

/** The pixel top of the now line, or null. It is null when `now` lies outside
 *  the hour window, or when `days` is given and none of them holds `now`. */
export function nowTop(
  now: Date,
  window: HourWindow,
  hourPx: number = HOUR_PX,
  days?: WeekDay[],
): number | null {
  if (!valid(now)) return null;
  if (days && nowDayIndex(days, now) === null) return null;
  const m = wallMin(now);
  if (m < window.startHour * 60 || m > window.endHour * 60) return null;
  return ((m - window.startHour * 60) / 60) * hourPx;
}

/** A short header text, such as `Mon 9`. */
export function dayLabel(date: Date): string {
  if (!valid(date)) return "";
  return `${DOW[(date.getDay() + 6) % 7]} ${date.getDate()}`;
}

/** Move the anchor one week or one day, `delta` times. */
export function shiftAnchor(anchor: Date, mode: WeekMode, delta: number): Date {
  return addDays(valid(anchor) ? anchor : new Date(1970, 0, 5), delta * (mode === "week" ? 7 : 1));
}

/** The range for a door filter: `gte` the start of the first day and `lt` the
 *  start of the day after the last day. Null when no day is valid. */
export function windowOfDays(days: WeekDay[]): { from: string; to: string } | null {
  if (days.length === 0) return null;
  const from = days[0].date;
  const to = addDays(days[days.length - 1].date, 1);
  if (!valid(from) || !valid(to)) return null;
  return { from: from.toISOString(), to: to.toISOString() };
}
