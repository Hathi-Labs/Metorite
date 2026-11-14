/**
 * The date window of the Studio calendar block (STU-D5-W2, `ai_studio.md` D5).
 * Pure: no React, no clock, no I/O.
 *
 * The block reads only the records of the visible period. This file builds that period, the
 * filter for it, and the move from one period to the next. The week and the day come from the
 * model of `components/calendar/weekModel.ts`. The month is the six weeks that `MonthGrid` draws.
 * The window filters the START field only: a record that starts before the period and ends inside
 * it is not read. That is the rule of D5-W2.
 */
import { addMonths, startOfMonthGrid } from "@/app/calendar/components/shared";
import { addDays } from "@/app/tasks/lib/scheduling";
import { buildWeek, shiftAnchor, windowOfDays, type WeekDay } from "@/components/calendar/weekModel";
import type { GridEvent } from "@/components/calendar/types";
import { MAX_FILTERS } from "./viewBlocks";

export type CalendarMode = "month" | "week" | "day";

/** The mode that a calendar opens in. A week shows the hours of a visit and stays readable. */
export const DEFAULT_MODE: CalendarMode = "week";

/** The days of the month grid: six weeks. */
export const MONTH_CELLS = 42;

export interface WindowCondition { field: string; op: "gte" | "lt"; value: string }

const pad = (n: number) => String(n).padStart(2, "0");
const dayString = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;

/** The visible days of a mode. A week and a day come from the model. */
export function visibleDays(mode: CalendarMode, anchor: Date): WeekDay[] {
  if (mode === "month") {
    const first = startOfMonthGrid(anchor);
    return Array.from({ length: MONTH_CELLS }, (_, i) => {
      const date = addDays(first, i);
      return { date, key: dayString(date) };
    });
  }
  return buildWeek([], { anchor, mode }).days;
}

/**
 * The range of the visible period: `from` is the start of the first day, `to` is the start of the
 * day after the last one. A `date` field takes a day string. A `datetime` field takes an instant.
 */
export function periodRange(
  mode: CalendarMode, anchor: Date, startType: "date" | "datetime",
): { from: string; to: string } | null {
  const days = visibleDays(mode, anchor);
  if (days.length === 0) return null;
  if (startType === "date") {
    return { from: dayString(days[0].date), to: dayString(addDays(days[days.length - 1].date, 1)) };
  }
  return windowOfDays(days);
}

/** The two conditions that cut the read to the period. Empty when the anchor is not a date. */
export function windowConditions(
  startField: string, startType: "date" | "datetime", mode: CalendarMode, anchor: Date,
): WindowCondition[] {
  const range = periodRange(mode, anchor, startType);
  if (!range) return [];
  return [
    { field: startField, op: "gte", value: range.from },
    { field: startField, op: "lt", value: range.to },
  ];
}

/**
 * The filter of the read: the view's own conditions first, then the window. The own conditions
 * are already resolved (`ownListArgs`). The window always stays whole, so only the own conditions
 * are cut, to `MAX_FILTERS - 2`.
 */
export function joinFilters(own: readonly unknown[], window: readonly WindowCondition[]): unknown[] {
  return [...own.slice(0, Math.max(0, MAX_FILTERS - window.length)), ...window];
}

export interface CalendarSourceInput {
  action: string;
  args: Record<string, unknown>;
}

/** The `list` source of one period. The `filter` of the view keeps its place before the window. */
export function periodSource(
  base: CalendarSourceInput, startField: string, startType: "date" | "datetime", mode: CalendarMode, anchor: Date,
): CalendarSourceInput {
  const own = Array.isArray(base.args.filter) ? base.args.filter : [];
  return {
    action: base.action,
    args: { ...base.args, filter: joinFilters(own, windowConditions(startField, startType, mode, anchor)) },
  };
}

/** The sentence for a read that hit the cap, or null. The caller says if the read hit it. */
export const CAP_SENTENCE = "Showing the first 100 records of this period. Narrow the view to see the rest.";
export function capSentence(rowCount: number, limit: number, hasMore: boolean): string | null {
  return rowCount >= limit || hasMore ? CAP_SENTENCE : null;
}

/** One step back (`-1`) or forward (`1`). A month moves to the first of the month. */
export function stepPeriod(mode: CalendarMode, anchor: Date, delta: number): Date {
  if (mode === "month") return addMonths(anchor, delta);
  return shiftAnchor(anchor, mode, delta);
}

/** A mode change keeps the anchor, so the member stays on the same date. */
export function switchMode(_from: CalendarMode, _to: CalendarMode, anchor: Date): Date {
  return anchor;
}

/** The events that touch one day, in start order. A record with no length counts on its start day. */
export function eventsOnDay(events: readonly GridEvent[], day: Date): GridEvent[] {
  const next = addDays(day, 1);
  return events
    .filter((e) => (e.start >= day && e.start < next) || (e.start < day && e.end > day))
    .sort((a, b) => a.start.getTime() - b.start.getTime());
}

/** The words above the grid: `November 2026`, or the span of a week, or one day. */
export function periodTitle(mode: CalendarMode, anchor: Date): string {
  const fmt = (d: Date, o: Intl.DateTimeFormatOptions) => d.toLocaleDateString("en-US", o);
  if (mode === "month") return fmt(anchor, { month: "long", year: "numeric" });
  const days = visibleDays(mode, anchor);
  if (mode === "day") return fmt(days[0].date, { weekday: "long", month: "long", day: "numeric", year: "numeric" });
  const a = days[0].date;
  const b = days[days.length - 1].date;
  const mon = (d: Date) => fmt(d, { month: "short" });
  const tail = a.getMonth() === b.getMonth() ? `${b.getDate()}` : `${mon(b)} ${b.getDate()}`;
  return `${mon(a)} ${a.getDate()} – ${tail}, ${b.getFullYear()}`;
}
