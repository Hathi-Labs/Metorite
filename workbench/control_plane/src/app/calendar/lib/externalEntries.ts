/**
 * Google and Outlook events on the Calendar (WS-CAL CE-5a,
 * `calendar_events_reminders.md` §6.2 to §6.4, dark behind
 * `NEXT_PUBLIC_CALENDAR_EVENTS`).
 *
 * The gateway read `GET /projects/my/calendar` carries an `entries` key when
 * `CALENDAR_EVENTS` is on. This file turns the `external` rows of it into a
 * view model, and holds the pure rules of the grid: the day segments, the
 * all-day bars and their fold, the Month chips and the words of the sheet.
 *
 * **The zone.** A timed entry is an instant. It lands on the wall of the
 * grid's zone (`gridZone`), the same frame the day columns and the reminder
 * pins use. An all-day entry is a DATE range. It uses no zone at all, so it
 * keeps its date for every member. `end_date` is EXCLUSIVE.
 *
 * **What is dropped.** A cancelled entry. Any kind but `external` (Metorite
 * events are another slice). An external entry with a `task_id` stays: the id
 * is a link ("Make a task from this"), not a copy (§3.3).
 *
 * Fence (R7): `externalEntries.test.ts` beside this file.
 */
import { clockText, dateText, wallParts } from "@/lib/reminderClock";

export type ExternalSource = "google" | "outlook";

/** One external entry, as the grid draws it. */
export interface ExternalView {
  id: string;
  source: ExternalSource;
  title: string;
  allDay: boolean;
  /** Timed: the instants in ms. All-day: 0. */
  startMs: number;
  endMs: number;
  /** All-day: `YYYY-MM-DD` and the EXCLUSIVE end. Timed: "". */
  startDate: string;
  endDate: string;
  /** `busy` false: the time is free, so the block looks lighter. */
  free: boolean;
  /** `status` tentative: a dashed border. */
  tentative: boolean;
  location: string | null;
  conferenceUrl: string | null;
  externalUrl: string | null;
}

const str = (v: unknown): string | null => (typeof v === "string" && v ? v : null);
const DATE = /^\d{4}-\d{2}-\d{2}$/;

/** `YYYY-MM-DD` plus `n` days, with no zone. */
export function addDateDays(date: string, n: number): string {
  const [y, m, d] = date.split("-").map(Number);
  const t = new Date(Date.UTC(y, m - 1, d + n));
  return dateText(t.getUTCFullYear(), t.getUTCMonth() + 1, t.getUTCDate());
}

/** The day key of a column: the local date of the `Date`. */
export function dayKey(day: Date): string {
  return dateText(day.getFullYear(), day.getMonth() + 1, day.getDate());
}

/** One wire row, or null when the grid must not draw it. */
export function readExternal(value: unknown): ExternalView | null {
  if (!value || typeof value !== "object") return null;
  const v = value as Record<string, unknown>;
  if (v.kind !== "external") return null;
  if (v.status === "cancelled") return null;
  const id = str(v.id);
  const source: ExternalSource | null = v.source === "google" || v.source === "outlook" ? v.source : null;
  if (!id || !source) return null;
  const base = {
    id,
    source,
    title: str(v.title) ?? "Busy",
    free: v.busy === false,
    tentative: v.status === "tentative",
    location: str(v.location),
    conferenceUrl: str(v.conference_url),
    externalUrl: str(v.external_url),
  };
  if (v.all_day === true) {
    const startDate = str(v.start_date);
    if (!startDate || !DATE.test(startDate)) return null;
    const rawEnd = str(v.end_date);
    // No end, or an end that is not after the start: one day.
    const endDate = rawEnd && DATE.test(rawEnd) && rawEnd > startDate ? rawEnd : addDateDays(startDate, 1);
    return { ...base, allDay: true, startMs: 0, endMs: 0, startDate, endDate };
  }
  const startMs = Date.parse(str(v.starts_at) ?? "");
  if (!Number.isFinite(startMs)) return null;
  const parsedEnd = Date.parse(str(v.ends_at) ?? "");
  const endMs = Number.isFinite(parsedEnd) && parsedEnd > startMs ? parsedEnd : startMs;
  return { ...base, allDay: false, startMs, endMs, startDate: "", endDate: "" };
}

/** The drawable external entries of a read. All-day first, then by start. */
export function externalViews(entries: unknown): ExternalView[] {
  if (!Array.isArray(entries)) return [];
  const out: ExternalView[] = [];
  for (const row of entries) {
    const view = readExternal(row);
    if (view) out.push(view);
  }
  return out.sort(compareViews);
}

function compareViews(a: ExternalView, b: ExternalView): number {
  if (a.allDay !== b.allDay) return a.allDay ? -1 : 1;
  if (a.allDay) return a.startDate.localeCompare(b.startDate) || a.title.localeCompare(b.title);
  return a.startMs - b.startMs || a.endMs - b.endMs || a.title.localeCompare(b.title);
}

// ── Timed: the day columns ───────────────────────────────────────────────────

/** The day number of a wall date. It is a plain count, so it has no zone. */
const dayNumber = (year: number, month: number, day: number) => Math.round(Date.UTC(year, month - 1, day) / 86_400_000);

/** The part of a timed entry inside one day column. Minutes of the wall day. */
export interface DaySegment {
  entry: ExternalView;
  startMin: number;
  endMin: number;
}

/** A segment is never shorter than this, so a zero-length entry stays visible. */
export const MIN_SEGMENT_MINS = 15;

/**
 * The timed entries on one day column, on the wall of `zone`. An entry across
 * midnight gives one segment to each day. An entry that ends at 00:00 gives
 * the next day nothing.
 */
export function segmentsForDay(views: readonly ExternalView[], day: Date, zone: string): DaySegment[] {
  const col = dayNumber(day.getFullYear(), day.getMonth() + 1, day.getDate());
  return segmentIndex(views, zone).get(col) ?? NO_SEGMENTS;
}

const NO_SEGMENTS: DaySegment[] = [];
/** The longest span, in days, that the index spreads over. */
const MAX_SPAN_DAYS = 62;
/** One index per entries array and zone, so a render does not redo the wall clock. */
const INDEX = new WeakMap<readonly ExternalView[], Map<string, Map<number, DaySegment[]>>>();

function segmentIndex(views: readonly ExternalView[], zone: string): Map<number, DaySegment[]> {
  let byZone = INDEX.get(views);
  if (!byZone) INDEX.set(views, (byZone = new Map()));
  const hit = byZone.get(zone);
  if (hit) return hit;
  const index = new Map<number, DaySegment[]>();
  for (const entry of views) {
    if (entry.allDay) continue;
    const s = wallParts(entry.startMs, zone);
    const e = wallParts(entry.endMs, zone);
    const first = dayNumber(s.year, s.month, s.day);
    const last = Math.min(dayNumber(e.year, e.month, e.day), first + MAX_SPAN_DAYS);
    for (let col = first; col <= last; col++) {
      const startMin = col === first ? s.hour * 60 + s.minute : 0;
      const endMin = col === last ? e.hour * 60 + e.minute : 1440;
      if (last > first && col === last && endMin === 0) continue;
      const seg = { entry, startMin, endMin: Math.min(1440, Math.max(endMin, startMin + MIN_SEGMENT_MINS)) };
      const list = index.get(col);
      if (list) list.push(seg);
      else index.set(col, [seg]);
    }
  }
  for (const list of index.values()) list.sort((a, b) => a.startMin - b.startMin || a.endMin - b.endMin);
  byZone.set(zone, index);
  return index;
}

/**
 * Splits a column's segments by the task blocks. A segment that shares time
 * with a task goes `beside`, and the grid draws it in a strip above the cards,
 * so the task never hides it. The rest keep the full column.
 */
export function splitByTasks(
  segs: readonly DaySegment[],
  tasks: ReadonlyArray<{ start: number; end: number }>,
): { clear: DaySegment[]; beside: DaySegment[] } {
  const clear: DaySegment[] = [];
  const beside: DaySegment[] = [];
  for (const seg of segs) {
    const hit = tasks.some((t) => t.start < seg.endMin && seg.startMin < t.end);
    (hit ? beside : clear).push(seg);
  }
  return { clear, beside };
}

/** The share of a column the overlap strip takes: Week columns are narrow. */
export const STRIP_SHARE_WEEK = 0.45;
export const STRIP_SHARE_DAY = 0.3;

/** The share a task card gives up: the strip's, when an event beside it shares its time. */
export function taskInset(
  beside: readonly DaySegment[],
  task: { start: number; end: number },
  share: number,
): number {
  return beside.some((s) => task.start < s.endMin && s.startMin < task.end) ? share : 0;
}

/** The line above the grid when the read of events failed (CE-5a). */
export const READ_FAILED_WORDS = "Could not read your Google and Outlook events. Try again in a minute.";

export function externalReadNote(error: string | null | undefined): string | null {
  return error ? READ_FAILED_WORDS : null;
}

/** Lanes for the segments of one column, so two that overlap sit side by side. */
export function laneSegments(segs: readonly DaySegment[]): Array<{ seg: DaySegment; lane: number; lanes: number }> {
  const laneEnds: number[] = [];
  const placed: Array<{ seg: DaySegment; lane: number }> = [];
  for (const seg of segs) {
    let lane = laneEnds.findIndex((end) => end <= seg.startMin);
    if (lane === -1) lane = laneEnds.length;
    laneEnds[lane] = seg.endMin;
    placed.push({ seg, lane });
  }
  const lanes = Math.max(1, laneEnds.length);
  return placed.map((p) => ({ ...p, lanes }));
}

// ── All-day: the row ─────────────────────────────────────────────────────────

/** The all-day entries on one date. `end_date` is exclusive. */
export function allDayOnDate(views: readonly ExternalView[], date: string): ExternalView[] {
  return views.filter((v) => v.allDay && v.startDate <= date && date < v.endDate);
}

/** An all-day bar: columns `from` to `to` (inclusive) of the visible days. */
export interface AllDayBar {
  entry: ExternalView;
  from: number;
  to: number;
  lane: number;
}

/** Bars to show in a day before the rest fold to "+N". */
export const ALL_DAY_LANES = 2;

export interface AllDayRow {
  bars: AllDayBar[];
  /** Per visible day: how many entries the fold hides. */
  more: number[];
  /** The lanes the row needs (at most `ALL_DAY_LANES`, or all when expanded). */
  laneCount: number;
}

/**
 * One bar for a span of days. A bar that does not fit in the first
 * `ALL_DAY_LANES` lanes folds: each of its days counts one in `more`.
 */
export function allDayRow(views: readonly ExternalView[], days: readonly Date[], expanded = false): AllDayRow {
  const keys = days.map(dayKey);
  const spans: Array<Omit<AllDayBar, "lane">> = [];
  for (const entry of views) {
    if (!entry.allDay) continue;
    const idx = keys.map((k, i) => (entry.startDate <= k && k < entry.endDate ? i : -1)).filter((i) => i >= 0);
    if (idx.length === 0) continue;
    spans.push({ entry, from: idx[0], to: idx[idx.length - 1] });
  }
  // Longer spans take the first lanes, so the bars stay one piece.
  spans.sort((a, b) => a.from - b.from || b.to - b.from - (a.to - a.from) || compareViews(a.entry, b.entry));
  const laneEnds: number[] = [];
  const more = keys.map(() => 0);
  const bars: AllDayBar[] = [];
  for (const span of spans) {
    let lane = laneEnds.findIndex((end) => end < span.from);
    if (lane === -1) lane = laneEnds.length;
    laneEnds[lane] = span.to;
    if (!expanded && lane >= ALL_DAY_LANES) {
      for (let i = span.from; i <= span.to; i++) more[i] += 1;
      continue;
    }
    bars.push({ ...span, lane });
  }
  const laneCount = bars.reduce((n, b) => Math.max(n, b.lane + 1), 0);
  return { bars, more, laneCount };
}

// ── Month ────────────────────────────────────────────────────────────────────

export interface ExternalChip {
  id: string;
  title: string;
  /** The clock of a timed entry, "" for an all-day one. */
  time: string;
  free: boolean;
  tentative: boolean;
}

/** The chips of one Month cell: all-day first, then by start. */
export function externalChips(views: readonly ExternalView[], day: Date, zone: string): ExternalChip[] {
  const key = dayKey(day);
  const chips: ExternalChip[] = allDayOnDate(views, key).map((v) => ({
    id: v.id,
    title: v.title,
    time: "",
    free: v.free,
    tentative: v.tentative,
  }));
  for (const { entry } of segmentsForDay(views, day, zone)) {
    chips.push({
      id: entry.id,
      title: entry.title,
      time: clockText(entry.startMs, zone),
      free: entry.free,
      tentative: entry.tentative,
    });
  }
  return chips;
}

// ── The sheet ────────────────────────────────────────────────────────────────

const PROVIDER: Record<ExternalSource, { name: string; open: string }> = {
  google: { name: "Google Calendar", open: "Open in Google Calendar" },
  outlook: { name: "Outlook", open: "Open in Outlook" },
};

/** The link of the provider, only when it is an https URL. */
export function safeHttps(url: string | null): string | null {
  if (!url) return null;
  try {
    return new URL(url).protocol === "https:" ? url : null;
  } catch {
    return null;
  }
}

function dayLabel(date: string): string {
  const [y, m, d] = date.split("-").map(Number);
  return new Intl.DateTimeFormat(undefined, { timeZone: "UTC", weekday: "short", month: "short", day: "numeric" }).format(
    new Date(Date.UTC(y, m - 1, d)),
  );
}

/** "Thu, Nov 12, 9:00 AM to 10:00 AM" for a timed entry, a date range for an all-day one. */
export function whenWords(v: ExternalView, zone: string): string {
  if (v.allDay) {
    const last = addDateDays(v.endDate, -1);
    return last > v.startDate ? `${dayLabel(v.startDate)} to ${dayLabel(last)}, all day` : `${dayLabel(v.startDate)}, all day`;
  }
  const fmt = (ms: number) =>
    new Intl.DateTimeFormat(undefined, { timeZone: zone, weekday: "short", month: "short", day: "numeric" }).format(new Date(ms));
  const s = wallParts(v.startMs, zone);
  const e = wallParts(v.endMs, zone);
  const sameDate = s.year === e.year && s.month === e.month && s.day === e.day;
  const start = `${fmt(v.startMs)}, ${clockText(v.startMs, zone)}`;
  if (v.endMs === v.startMs) return start;
  return sameDate ? `${start} to ${clockText(v.endMs, zone)}` : `${start} to ${fmt(v.endMs)}, ${clockText(v.endMs, zone)}`;
}

/** The words of the read-only sheet. */
export interface SheetFacts {
  title: string;
  when: string;
  zone: string | null;
  location: string | null;
  joinUrl: string | null;
  note: string | null;
  source: string;
  openLabel: string;
  openUrl: string | null;
}

export function sheetFacts(v: ExternalView, zone: string): SheetFacts {
  const p = PROVIDER[v.source];
  return {
    title: v.title,
    when: whenWords(v, zone),
    zone: v.allDay ? null : zone,
    location: v.location,
    joinUrl: safeHttps(v.conferenceUrl),
    note: v.free ? "Shown as free." : v.tentative ? "Not confirmed yet." : null,
    source: `From ${p.name}, read only.`,
    openLabel: p.open,
    openUrl: safeHttps(v.externalUrl),
  };
}

/** The provider name, for a title or a label. */
export function providerName(source: ExternalSource): string {
  return PROVIDER[source].name;
}
