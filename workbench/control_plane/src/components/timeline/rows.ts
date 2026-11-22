/**
 * Timeline block · records to rows.
 *
 * `rowsToTimelineRows` reads any list of records that have a start field and an
 * end field. It gives the block its row shape. The Studio renderer does not
 * call it yet. A later slice does that.
 */

import { dayKey, fromDayKey } from "./days";
import { type Bar, barForRange } from "./geometry";
import {
  DEFAULT_ZOOM,
  rangeForSpans,
  type DaySpan,
  type TimelineRange,
  type TimelineWindow,
  type TimelineZoom,
} from "./scale";
import type { TimelineRow } from "./types";

export interface RowFields {
  /** The field that holds the first day. */
  startField: string;
  /** The field that holds the last day. */
  endField: string;
  /** The field that holds the label of the row. */
  titleField: string;
}

/**
 * A value as a whole local day, or `null` when it cannot be read.
 *
 * A date-only value (`YYYY-MM-DD`) is that whole day. Never pass it through
 * `new Date(string)`, because that reads it as UTC midnight. A value with a
 * time is read as an instant and snaps to the local day.
 */
export function readDay(value: unknown): Date | null {
  if (value instanceof Date) {
    return Number.isNaN(value.getTime()) ? null : fromDayKey(dayKey(value));
  }
  if (typeof value !== "string") return null;
  const text = value.trim();
  const only = /^(\d{4})-(\d{2})-(\d{2})$/.exec(text);
  if (only) {
    const date = new Date(Number(only[1]), Number(only[2]) - 1, Number(only[3]));
    // Reject 2026-02-31, which `Date` would roll into March.
    return dayKey(date) === text ? date : null;
  }
  if (!/^\d{4}-\d{2}-\d{2}[T ]/.test(text)) return null;
  const instant = new Date(text);
  return Number.isNaN(instant.getTime()) ? null : fromDayKey(dayKey(instant));
}

/**
 * Records to timeline rows.
 *
 * * A record with no readable start is dropped.
 * * A missing or unreadable end makes the end equal to the start.
 * * An end before the start is made equal to the start.
 * * The `id` field of the record is the row id. A record with no id gets its
 *   position in the list.
 */
export function rowsToTimelineRows(
  records: readonly Record<string, unknown>[],
  fields: RowFields,
): TimelineRow<Record<string, unknown>>[] {
  const out: TimelineRow<Record<string, unknown>>[] = [];
  records.forEach((record, index) => {
    const start = readDay(record[fields.startField]);
    if (!start) return;
    const read = readDay(record[fields.endField]);
    const end = read && read.getTime() >= start.getTime() ? read : start;
    const title = record[fields.titleField];
    const id = record.id;
    out.push({
      id: typeof id === "string" || typeof id === "number" ? String(id) : String(index),
      label: typeof title === "string" ? title : title == null ? "" : String(title),
      start,
      end,
      tone: null,
      data: record,
    });
  });
  return out;
}

/** The days a row covers, or `null` for a row with no bar. */
export function rowSpan(
  row: Pick<TimelineRow, "start" | "end" | "derived">,
): (DaySpan & { derived: boolean }) | null {
  if (!row.start && !row.end) return null;
  const a = dayKey((row.start ?? row.end) as Date);
  const b = dayKey((row.end ?? row.start) as Date);
  return { from: a <= b ? a : b, to: a <= b ? b : a, derived: row.derived ?? false };
}

/** The range of the chart for these rows. */
export function rangeForRows(
  rows: readonly Pick<TimelineRow, "start" | "end" | "derived">[],
  todayKey: string,
  zoom: TimelineZoom = DEFAULT_ZOOM,
  window?: TimelineWindow,
): TimelineRange {
  const spans = rows.map(rowSpan).filter(Boolean) as DaySpan[];
  return rangeForSpans(spans, todayKey, zoom, window);
}

/** Where the bar of a row sits, or `null` for a row with no dates. */
export function barForRow(
  row: Pick<TimelineRow, "start" | "end" | "derived">,
  range: TimelineRange,
): Bar | null {
  return barForRange(rowSpan(row), range);
}
