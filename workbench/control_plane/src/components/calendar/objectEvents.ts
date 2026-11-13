import type { GridEvent } from "./types";

/** One row of an object, as the grid reads it. The row stays opaque. */
export type ObjectRow = { id: string; values: Record<string, unknown> };

export type ObjectEventSource = {
  /** The field that holds the date or the datetime of the row. */
  dateField: string;
  /** The field that holds the title of the row. */
  titleField: string;
  /** Minutes that a datetime row lasts. The default is 60. */
  durationMins?: number;
};

const DATE_ONLY = /^\d{4}-\d{2}-\d{2}$/;

/** Read a field as a date. A bare `YYYY-MM-DD` is local midnight and all day.
 *  A full datetime is a time. Anything else is null. */
function readDate(v: unknown): { at: Date; allDay: boolean } | null {
  if (v instanceof Date) return Number.isNaN(v.getTime()) ? null : { at: v, allDay: false };
  if (typeof v !== "string" || !v.trim()) return null;
  const s = v.trim();
  if (DATE_ONLY.test(s)) {
    const [y, m, d] = s.split("-").map(Number);
    return { at: new Date(y, m - 1, d), allDay: true };
  }
  const t = new Date(s);
  return Number.isNaN(t.getTime()) ? null : { at: t, allDay: false };
}

/** Map the rows of an object to grid events by a date or datetime field.
 *  A row with no readable date has no place on the grid, so it is left out. */
export function rowsToEvents(rows: ObjectRow[], src: ObjectEventSource): GridEvent[] {
  const mins = src.durationMins ?? 60;
  const out: GridEvent[] = [];
  for (const row of rows) {
    const d = readDate(row.values[src.dateField]);
    if (!d) continue;
    const title = row.values[src.titleField];
    const end = d.allDay
      ? new Date(d.at.getFullYear(), d.at.getMonth(), d.at.getDate() + 1)
      : new Date(d.at.getTime() + mins * 60000);
    out.push({
      id: row.id,
      title: typeof title === "string" && title.trim() ? title : "Untitled",
      start: d.at,
      end,
      allDay: d.allDay,
      tone: "primary",
      data: row,
    });
  }
  return out;
}
