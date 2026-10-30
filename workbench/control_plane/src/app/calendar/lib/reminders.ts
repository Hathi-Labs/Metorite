/**
 * The reminders of the Calendar (WS-66 RM-5, `calendar_events_reminders.md`
 * §5.1-5.3, §6.1, §6.2, §6.4, §9, §10.1 row RM-5).
 *
 * A reminder is a point in time. It draws as a line with no height, so it
 * takes no capacity and no lane, and it never enters the task store. This file
 * holds the pure rules (zone to ISO, the split by state, the pin, the month
 * chip, the form check) and the client of the list, of create and of
 * `/reschedule`.
 *
 * **The routes.** `/api/projects/my/reminders` is the BFF proxy of the
 * gateway. A 404 means the flag is off for this member. A 403 means the member
 * has `feature:tasks` and not `feature:projects`. Both mean the same here:
 * draw nothing, say nothing (`"absent"`). A network fault or a 5xx keeps the
 * last good rows, and a first read that fails tries again a few times. There
 * is no client flag (§9).
 *
 * **One client of `/resolve` and `/reopen`.** Done, Drop and Undo go through
 * `runReminderAct` in `lib/shell/reminderOpen.ts`, the client of the chat
 * chips. Every refusal here reads `refusalWords` from the same file.
 *
 * **The zone.** The grid, the blocks and the day columns all read the
 * browser's wall clock. So every reminder computation uses the browser's zone
 * too (`gridZone`): the pin top, the day match, the month chip, a drop, a key
 * move and the form default. `user_settings.timezone` is not used, because it
 * holds the store default "UTC" until settings load, and the pin would draw at
 * the wrong minute. A new time goes out as an ISO string WITH the offset,
 * beside `tz`.
 *
 * Fence (R7): `reminders.test.ts` beside this file.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { useLiveRefresh } from "@/lib/liveRefresh";
import { knownZone, refusalWords, UNREACHABLE_WORDS } from "@/lib/shell/reminderOpen";

// ── The rows ─────────────────────────────────────────────────────────────────

export type ReminderKind = "action" | "notice";

/** The open states of a reminder (§5.3). The Calendar lists no other. */
export type ReminderState = "scheduled" | "due" | "unconfirmed";

/** The fields of a reminder row that the Calendar reads. */
export interface CalReminder {
  id: string;
  title: string;
  notes: string | null;
  kind: ReminderKind;
  state: ReminderState;
  fireAt: string;
  /** The ms of `fireAt`. */
  fireMs: number;
  tz: string | null;
}

const OPEN: ReadonlySet<string> = new Set(["scheduled", "due", "unconfirmed"]);

const str = (v: unknown): string | null => (typeof v === "string" ? v : null);

/** A row from the list, or null when it is not an open reminder with a time. */
export function readRow(value: unknown): CalReminder | null {
  if (!value || typeof value !== "object") return null;
  const v = value as Record<string, unknown>;
  const id = str(v.id);
  const state = str(v.state);
  const fireAt = str(v.fire_at);
  if (!id || !state || !OPEN.has(state) || !fireAt) return null;
  const fireMs = Date.parse(fireAt);
  if (Number.isNaN(fireMs)) return null;
  return {
    id,
    title: str(v.title) ?? "",
    notes: str(v.notes),
    kind: v.kind === "notice" ? "notice" : "action",
    state: state as ReminderState,
    fireAt,
    fireMs,
    tz: str(v.tz),
  };
}

/** The route that opens a reminder in a fresh chat (RM-4, `reminderParam`). */
export function reminderChatPath(id: string): string {
  return `/chat?do=new-chat&reminder=${encodeURIComponent(id)}`;
}

export interface ReminderGroups {
  /** state `scheduled` */
  upcoming: CalReminder[];
  /** state `due` */
  waiting: CalReminder[];
  /** state `unconfirmed` */
  unconfirmed: CalReminder[];
}

/** The three lists of the panel, each in time order. */
export function splitByState(rows: readonly CalReminder[]): ReminderGroups {
  const sorted = [...rows].sort((a, b) => a.fireMs - b.fireMs);
  return {
    upcoming: sorted.filter((r) => r.state === "scheduled"),
    waiting: sorted.filter((r) => r.state === "due"),
    unconfirmed: sorted.filter((r) => r.state === "unconfirmed"),
  };
}

/**
 * The first `limit` rows of the panel, in the order the panel draws them:
 * Waiting on you, Unconfirmed, then Upcoming. `hidden` counts the rows that
 * the cap leaves out. A null `limit` keeps every row.
 */
export function capGroups(g: ReminderGroups, limit: number | null): { groups: ReminderGroups; hidden: number } {
  if (limit === null) return { groups: g, hidden: 0 };
  let left = Math.max(0, limit);
  const take = (rows: CalReminder[]) => {
    const out = rows.slice(0, left);
    left -= out.length;
    return out;
  };
  const groups = { waiting: take(g.waiting), unconfirmed: take(g.unconfirmed), upcoming: take(g.upcoming) };
  const shown = groups.waiting.length + groups.unconfirmed.length + groups.upcoming.length;
  return { groups, hidden: g.waiting.length + g.unconfirmed.length + g.upcoming.length - shown };
}

// ── The zone ─────────────────────────────────────────────────────────────────

const formatters = new Map<string, Intl.DateTimeFormat>();

/**
 * The zone of the grid: the browser's zone, else UTC. The grid draws its blocks
 * and day columns on the browser's wall clock, so a reminder must use the same
 * frame. The store's `settings.timezone` plays no part here. `knownZone` is the
 * one check of a zone name (`lib/shell/reminderOpen.ts`).
 */
export function gridZone(browserZone?: string): string {
  return knownZone(browserZone) ?? "UTC";
}

export interface WallParts {
  year: number;
  month: number; // 1-12
  day: number;
  hour: number;
  minute: number;
  second: number;
}

/** The wall clock of an instant in a zone. */
export function wallParts(ms: number, zone: string): WallParts {
  let fmt = formatters.get(zone);
  if (!fmt) {
    fmt = new Intl.DateTimeFormat("en-US", {
      timeZone: zone,
      hourCycle: "h23",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
    formatters.set(zone, fmt);
  }
  const out: Record<string, number> = {};
  for (const p of fmt.formatToParts(new Date(ms))) {
    if (p.type !== "literal") out[p.type] = Number(p.value);
  }
  return {
    year: out.year,
    month: out.month,
    day: out.day,
    hour: out.hour % 24,
    minute: out.minute,
    second: out.second,
  };
}

/** The offset of a zone at an instant, in minutes east of UTC. */
export function offsetMinutes(ms: number, zone: string): number {
  const p = wallParts(ms, zone);
  const wallAsUtc = Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, p.second);
  return Math.round((wallAsUtc - Math.floor(ms / 1000) * 1000) / 60000);
}

const pad = (n: number, w = 2) => String(Math.abs(n)).padStart(w, "0");

/** "+05:30" or "-04:00". */
export function offsetText(minutes: number): string {
  return `${minutes < 0 ? "-" : "+"}${pad(Math.floor(Math.abs(minutes) / 60))}:${pad(Math.abs(minutes) % 60)}`;
}

/** "2026-10-31" for a wall date. */
export function dateText(year: number, month: number, day: number): string {
  return `${pad(year, 4)}-${pad(month)}-${pad(day)}`;
}

/** The instant of a date and a time on the wall of a zone, as ISO with the offset. */
export interface ZonedTime {
  iso: string;
  ms: number;
}

/**
 * A wall date ("2026-10-31"), a wall time ("09:00") and an IANA zone, as an
 * ISO string WITH the offset ("2026-10-31T09:00:00+05:30"), or null when any
 * part is not valid.
 *
 * **A clock change.** A time that happens twice (the autumn overlap) takes its
 * first run. A time that never happens (the spring gap) moves forward by the
 * gap, so 02:30 in a skipped hour becomes 03:30. The offset in the text is the
 * offset of the instant that came out.
 */
export function zonedToIso(date: string, time: string, zone: string): ZonedTime | null {
  const d = /^(\d{4})-(\d{2})-(\d{2})$/.exec(date);
  const t = /^(\d{2}):(\d{2})$/.exec(time);
  if (!d || !t || !knownZone(zone)) return null;
  const [y, mo, da] = [Number(d[1]), Number(d[2]), Number(d[3])];
  const [h, mi] = [Number(t[1]), Number(t[2])];
  const wall = Date.UTC(y, mo - 1, da, h, mi);
  const back = new Date(wall);
  if (back.getUTCFullYear() !== y || back.getUTCMonth() !== mo - 1 || back.getUTCDate() !== da || h > 23 || mi > 59) {
    return null;
  }
  const before = offsetMinutes(wall - 86_400_000, zone);
  const after = offsetMinutes(wall + 86_400_000, zone);
  const fits = [...new Set([before, after])]
    .map((o) => ({ o, ms: wall - o * 60_000 }))
    .filter((c) => offsetMinutes(c.ms, zone) === c.o);
  const ms = fits.length > 0 ? Math.min(...fits.map((c) => c.ms)) : wall - before * 60_000;
  return { ms, iso: instantIso(ms, zone) };
}

/**
 * An instant on a whole minute as ISO WITH the offset of `zone` at that
 * instant ("2026-10-25T02:15:00+01:00"). The text and the instant agree, also
 * in the hour that a clock change repeats.
 */
export function instantIso(ms: number, zone: string): string {
  const p = wallParts(ms, zone);
  return `${dateText(p.year, p.month, p.day)}T${pad(p.hour)}:${pad(p.minute)}:00${offsetText(offsetMinutes(ms, zone))}`;
}

/** The wall minute of the day (0-1439) of an instant in a zone. */
export function wallMinutes(ms: number, zone: string): number {
  const p = wallParts(ms, zone);
  return p.hour * 60 + p.minute;
}

/** "9:30 AM" for an instant on the wall of a zone. */
export function clockText(ms: number, zone: string): string {
  return new Intl.DateTimeFormat(undefined, { timeZone: zone, hour: "numeric", minute: "2-digit" }).format(new Date(ms));
}

/** "Sat, 31 Oct, 9:30 AM" for an instant on the wall of a zone. */
export function whenText(ms: number, zone: string): string {
  return new Intl.DateTimeFormat(undefined, {
    timeZone: zone,
    weekday: "short",
    day: "numeric",
    month: "short",
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(ms));
}

/**
 * The default time of the form: the next half hour after `nowMs`, on the wall
 * of the zone. After 23:30 it is 00:00 of the next day.
 */
export function defaultWhen(nowMs: number, zone: string): { date: string; time: string } {
  const p = wallParts(nowMs, zone);
  const next = (Math.floor((p.hour * 60 + p.minute) / 30) + 1) * 30;
  const day = new Date(Date.UTC(p.year, p.month - 1, p.day + Math.floor(next / 1440)));
  const mins = next % 1440;
  return {
    date: dateText(day.getUTCFullYear(), day.getUTCMonth() + 1, day.getUTCDate()),
    time: `${pad(Math.floor(mins / 60))}:${pad(mins % 60)}`,
  };
}

/** The date and the time of the form for an instant, on the wall of a zone. */
export function wallInputs(ms: number, zone: string): { date: string; time: string } {
  const p = wallParts(ms, zone);
  return { date: dateText(p.year, p.month, p.day), time: `${pad(p.hour)}:${pad(p.minute)}` };
}

// ── The form ─────────────────────────────────────────────────────────────────

export const TITLE_MAX = 200;

/** The words under the time field of the form when the time is not after now. */
export const PAST_FIELD_WORDS = "Pick a time after now.";

/** The words of a drag or a key move to a time that is not after now. */
export const PAST_MOVE_WORDS = "That time has passed. Move the reminder to a time after now.";

/**
 * Why a move to `toMs` is refused, or null when it may go. The rule of the
 * form: a reminder moves only to a time after now. The loop fires a past
 * `scheduled` reminder within a minute, so a move to the past would fire it
 * at once.
 */
export function moveRefusal(toMs: number, nowMs: number): string | null {
  return toMs <= nowMs ? PAST_MOVE_WORDS : null;
}

/** The time of a reminder before a move, for the Undo of that move. */
export interface MoveBack {
  fireAt: string;
  tz: string;
  ms: number;
}

/**
 * The Undo of a move: the time and the zone that `row` had, or null when a
 * move of it has no Undo. Only a `scheduled` reminder whose time is still
 * after now goes back as it was. A `due` or `unconfirmed` reminder would come
 * back as a `scheduled` one at a past time, and the loop would fire it again.
 */
export function moveBack(row: CalReminder, nowMs: number, zone: string): MoveBack | null {
  if (row.state !== "scheduled" || row.fireMs <= nowMs) return null;
  return { fireAt: row.fireAt, tz: row.tz ?? zone, ms: row.fireMs };
}

export interface FormCheck {
  titleError?: string;
  whenError?: string;
  /** The checked time, when the form can be sent. */
  when?: ZonedTime;
  title: string;
}

/**
 * Check the form of §6.1 (the reminder arm). The title is required and has at
 * most 200 characters. The time must be a real time after now. `needTitle`
 * is false for a move, which keeps the title.
 */
export function checkForm(a: {
  title: string;
  date: string;
  time: string;
  zone: string;
  nowMs: number;
  needTitle?: boolean;
}): FormCheck {
  const title = a.title.trim();
  const out: FormCheck = { title };
  if (a.needTitle !== false) {
    if (!title) out.titleError = "Add a title for the reminder.";
    else if (title.length > TITLE_MAX) out.titleError = `Keep the title to ${TITLE_MAX} characters.`;
  }
  const when = zonedToIso(a.date, a.time, a.zone);
  if (!a.date || !a.time) out.whenError = "Pick a date and a time.";
  else if (!when) out.whenError = "This date or time is not valid.";
  else if (moveRefusal(when.ms, a.nowMs)) out.whenError = PAST_FIELD_WORDS;
  else out.when = when;
  return out;
}

// ── The grid ─────────────────────────────────────────────────────────────────

export interface PinAt {
  row: CalReminder;
  /** The wall minute of the day in the zone of the grid. */
  minutes: number;
}

/** The reminders whose wall date in `zone` is the local calendar date of `day`. */
export function remindersOnDay(rows: readonly CalReminder[], day: Date, zone: string): PinAt[] {
  const out: PinAt[] = [];
  for (const row of rows) {
    const p = wallParts(row.fireMs, zone);
    if (p.year === day.getFullYear() && p.month === day.getMonth() + 1 && p.day === day.getDate()) {
      out.push({ row, minutes: p.hour * 60 + p.minute });
    }
  }
  return out.sort((a, b) => a.minutes - b.minutes || a.row.fireMs - b.row.fireMs);
}

/** The top of a pin in px: its wall minute from the first hour of the grid. */
export function pinTop(minutes: number, dayStartHour: number, hourPx: number): number {
  return ((minutes - dayStartHour * 60) / 60) * hourPx;
}

/**
 * The time of a drop: the local calendar date of the column `day` and a
 * minute of that day, read on the wall of the grid's zone.
 */
export function dropTime(day: Date, minutes: number, zone: string): ZonedTime | null {
  const m = Math.max(0, Math.min(minutes, 1439));
  return zonedToIso(
    dateText(day.getFullYear(), day.getMonth() + 1, day.getDate()),
    `${pad(Math.floor(m / 60))}:${pad(m % 60)}`,
    zone,
  );
}

/** One step of the keyboard move, in minutes (the grid's `SNAP_MINS`). */
export const NUDGE_MINS = 15;

/**
 * The next grid line above (-1) or below (+1) a reminder. A reminder at 09:07
 * goes to 09:15, then 09:30.
 *
 * **The step works on the instant**, not on the wall clock. It reads the wall
 * time of the instant in `zone`, finds the next 15-minute line, and adds that
 * difference to the instant. So a key press always moves the reminder 1 to 15
 * minutes of real time. In the hour that a clock change repeats, 02:15 of the
 * second run goes to 02:30 of the second run, and never back to the first run.
 */
export function nudgeTime(row: CalReminder, dir: 1 | -1, zone: string): ZonedTime | null {
  if (!knownZone(zone)) return null;
  const step = NUDGE_MINS * 60_000;
  // The wall time of the instant in `zone`, counted as ms. The lines sit on it.
  const wall = row.fireMs + offsetMinutes(row.fireMs, zone) * 60_000;
  const line = dir > 0 ? (Math.floor(wall / step) + 1) * step : (Math.ceil(wall / step) - 1) * step;
  const ms = row.fireMs + (line - wall);
  return { ms, iso: instantIso(ms, zone) };
}

/** The drag type of a reminder pin. A task block uses `DRAG_TYPE`. */
export const REMINDER_DRAG_TYPE = "application/x-cc-cal-reminder";

/**
 * The pin line style for a state. Tokens only (§6.2). The three read apart:
 * due is the solid accent line, scheduled is a solid neutral line, and
 * unconfirmed is a dashed muted line. `border-border` is too faint on a dark
 * grid, so scheduled uses the foreground token at 50 percent.
 */
export function pinTone(state: ReminderState): string {
  if (state === "due") return "border-primary";
  if (state === "unconfirmed") return "border-dashed border-muted-foreground";
  return "border-foreground/50";
}

/** A span of a task block in a day column, in minutes of the day. */
export interface BlockSpan {
  start: number;
  end: number;
}

/** The full pin label spans 10 px above the line and 6 px below it. */
const LABEL_ABOVE_PX = 10;
const LABEL_BELOW_PX = 6;

/**
 * True when the full pin label would sit on a task block, so the pin must draw
 * its compact form (the line and a small bell, with no text). The label covers
 * the line minus 10 px to the line plus 6 px. The pin is compact when that band
 * overlaps any block. That covers a pin inside a block, and a pin so near a
 * block edge that the label reaches the block.
 */
export function pinNeedsCompact(minutes: number, spans: readonly BlockSpan[], hourPx: number): boolean {
  const reachMins = (LABEL_ABOVE_PX / hourPx) * 60;
  const belowMins = (LABEL_BELOW_PX / hourPx) * 60;
  return spans.some((s) => minutes + belowMins > s.start && minutes - reachMins < s.end);
}

/** A task block as the grid draws it: its top and height in px, and its lane. */
export interface BlockBox {
  top: number;
  height: number;
  lane: number;
  lanes: number;
}

/** The pin line is 2 px thick (`border-t-2`). */
const LINE_PX = 2;

/**
 * The parts of a pin line at `y` px that stay off the task blocks, as
 * fractions of the column width (0 to 1). The line stops at the edge of each
 * lane that a block holds at that height. So a line never runs through the
 * text of a block, where it would read as a strike-through. A pin with no
 * block at its height gets one part, the full width.
 */
export function pinSegments(y: number, boxes: readonly BlockBox[]): Array<{ from: number; to: number }> {
  const held = boxes
    .filter((b) => y + LINE_PX > b.top && y < b.top + b.height)
    .map((b) => ({ from: b.lane / b.lanes, to: (b.lane + 1) / b.lanes }))
    .sort((a, b) => a.from - b.from);
  const out: Array<{ from: number; to: number }> = [];
  let at = 0;
  for (const h of held) {
    if (h.from - at > 1e-9) out.push({ from: at, to: h.from });
    at = Math.max(at, h.to);
  }
  if (1 - at > 1e-9) out.push({ from: at, to: 1 });
  return out;
}

/** The glyph colour for a state. */
export function pinGlyphTone(state: ReminderState): string {
  if (state === "due") return "text-primary";
  if (state === "unconfirmed") return "text-muted-foreground";
  return "text-foreground/60";
}

export interface MonthChip {
  id: string;
  label: string;
  time: string;
  state: ReminderState;
}

/** The chips of a month cell for `day`: a time and a title, in time order. */
export function monthChips(rows: readonly CalReminder[], day: Date, zone: string): MonthChip[] {
  return remindersOnDay(rows, day, zone).map(({ row }) => ({
    id: row.id,
    label: row.title,
    time: clockText(row.fireMs, zone),
    state: row.state,
  }));
}

/** The hint under a pin: a move restarts the reminder (§5.4). */
export const MOVE_NOTE = "A move starts the reminder again.";

// ── The client ───────────────────────────────────────────────────────────────

const BASE = "/api/projects/my/reminders";

export type ListResult =
  | { kind: "ok"; rows: CalReminder[]; total: number }
  | { kind: "absent" }
  | { kind: "error" };

/**
 * One call with no state and limit 500: the open rows (§5.1). A 404 or a 403
 * is "absent". A network fault or any other status is "error".
 */
export async function fetchReminders(fetchImpl: typeof fetch = fetch): Promise<ListResult> {
  try {
    const res = await fetchImpl(`${BASE}?limit=500`, { cache: "no-store" });
    if (res.status === 404 || res.status === 403) return { kind: "absent" };
    if (!res.ok) return { kind: "error" };
    const body = (await res.json()) as { rows?: unknown; total?: unknown } | null;
    const raw = Array.isArray(body?.rows) ? body.rows : [];
    const rows = raw.map(readRow).filter((r): r is CalReminder => r !== null);
    const total = typeof body?.total === "number" ? body.total : rows.length;
    return { kind: "ok", rows, total };
  } catch {
    return { kind: "error" };
  }
}

export interface ReminderList {
  /** `loading` and `absent` draw nothing. Only `ready` draws. */
  status: "loading" | "ready" | "absent";
  rows: CalReminder[];
  total: number;
}

export const EMPTY_LIST: ReminderList = { status: "loading", rows: [], total: 0 };

/** The list after a fetch: an error keeps the last good rows. */
export function applyList(prev: ReminderList, got: ListResult): ReminderList {
  if (got.kind === "absent") return { status: "absent", rows: [], total: 0 };
  if (got.kind === "error") return prev;
  return { status: "ready", rows: got.rows, total: got.total };
}

/**
 * The waits before each new try of a first read that failed, in ms. A
 * gateway restart answers 502 for some seconds, and the Calendar must not
 * wait for a focus or a chat write to show the reminders.
 */
export const FIRST_READ_RETRY_MS: readonly number[] = [1_000, 2_000, 4_000, 8_000, 16_000];

/**
 * The wait before the next try of the first read, or null for no more tries.
 * `settled` is true once any read answered (rows, or `absent`). Then the
 * live refresh and the writes keep the list, and no retry runs. The list
 * stays `loading`, which draws nothing, while it tries. No error shows.
 */
export function firstReadRetry(settled: boolean, attempt: number): number | null {
  if (settled) return null;
  return FIRST_READ_RETRY_MS[attempt] ?? null;
}

export type WriteResult = { ok: true } | { ok: false; words: string };

/** The words of a create or a move that the server refused for no named reason. */
const SAVE_FAILED_WORDS = "Metorite could not save the reminder. Try again.";

async function write(
  url: string,
  body: Record<string, unknown>,
  fetchImpl: typeof fetch,
): Promise<WriteResult> {
  try {
    const res = await fetchImpl(url, {
      method: "POST",
      cache: "no-store",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (res.ok) return { ok: true };
    const data: unknown = await res.json().catch(() => null);
    return { ok: false, words: refusalWords(res.status, data, SAVE_FAILED_WORDS) };
  } catch {
    return { ok: false, words: UNREACHABLE_WORDS };
  }
}

/** Create a standalone reminder. No subject fields (the link picker waits for CE-3). */
export function createReminder(
  a: { title: string; kind: ReminderKind; fireAt: string; tz: string },
  fetchImpl: typeof fetch = fetch,
): Promise<WriteResult> {
  return write(BASE, { title: a.title, kind: a.kind, fire_at: a.fireAt, tz: a.tz }, fetchImpl);
}

/** Move a reminder to a named time through `/reschedule` (§5.4). */
export function moveReminder(
  id: string,
  a: { fireAt: string; tz: string },
  fetchImpl: typeof fetch = fetch,
): Promise<WriteResult> {
  return write(`${BASE}/${encodeURIComponent(id)}/reschedule`, { fire_at: a.fireAt, tz: a.tz }, fetchImpl);
}

/** The words of the busy guard: a second write while one runs does nothing. */
export const BUSY_WORDS = "Wait for the last change to finish.";

/**
 * The reminders of the member. It reads on mount, after an own write, and on
 * a live refresh of the calendar app. A first read that fails tries again
 * (`firstReadRetry`). Every write runs once at a time (M3).
 */
export function useReminders() {
  const [list, setList] = useState<ReminderList>(EMPTY_LIST);
  const busy = useRef(false);
  // True once any read answered with rows or `absent`.
  const settled = useRef(false);

  const take = useCallback((got: ListResult) => {
    if (got.kind !== "error") settled.current = true;
    setList((prev) => applyList(prev, got));
  }, []);

  const reload = useCallback(async () => {
    take(await fetchReminders());
  }, [take]);

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const attempt = async (n: number) => {
      const got = await fetchReminders();
      if (!alive) return;
      take(got);
      const wait = firstReadRetry(settled.current, n);
      if (wait !== null) timer = setTimeout(() => void attempt(n + 1), wait);
    };
    // eslint-disable-next-line react-hooks/set-state-in-effect -- the read sets state after the await
    void attempt(0);
    return () => {
      alive = false;
      if (timer !== undefined) clearTimeout(timer);
    };
  }, [take]);

  useLiveRefresh(() => void reload(), "calendar");

  /** Run one write, then read again. A second call while one runs does nothing. */
  const run = useCallback(
    async (act: () => Promise<WriteResult>): Promise<WriteResult> => {
      if (busy.current) return { ok: false, words: BUSY_WORDS };
      busy.current = true;
      try {
        const res = await act();
        await reload();
        return res;
      } finally {
        busy.current = false;
      }
    },
    [reload],
  );

  return { list, reload, run };
}
