/**
 * The wall clock of a zone: the pure rules that turn a date, a time and a zone
 * into an instant, and an instant back into words (WS-66 RM-5, RX-6).
 *
 * These lived in `app/calendar/lib/reminders.ts`. Tasks may not import from the
 * Calendar app (D54, `calendarBoundary.test.ts`), and the Tasks quick capture
 * now sets a real reminder, so the rules moved here and the Calendar file
 * re-exports them. One implementation, two callers. The tests stay in
 * `app/calendar/lib/reminders.test.ts`.
 */
import { knownZone } from "@/lib/shell/reminderOpen";

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

export const pad = (n: number, w = 2) => String(Math.abs(n)).padStart(w, "0");

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

/** The words under a time field when the time is not after now. One text for the Calendar and Tasks. */
export const PAST_FIELD_WORDS = "Pick a time after now.";

// ── The words of a time (RX-2) ───────────────────────────────────────────────

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;

/** The wall date ("2026-11-02") of an instant in a zone. */
function wallDate(ms: number, zone: string): string {
  const p = wallParts(ms, zone);
  return dateText(p.year, p.month, p.day);
}

/** The wall date `days` after a wall date, as text. */
function addDays(date: string, days: number): string {
  const [y, m, d] = date.split("-").map(Number);
  const next = new Date(Date.UTC(y, m - 1, d + days));
  return dateText(next.getUTCFullYear(), next.getUTCMonth() + 1, next.getUTCDate());
}

/**
 * The exact time in the member's words: "Today, 5:00 PM IST", "Tomorrow, 9:00
 * AM IST", or "Sat 7 Nov, 9:00 AM IST". The day is the day on the wall of
 * `zone`. The zone name is `abbr` when the caller has one, else the one the
 * browser gives for it.
 */
export function exactWhen(ms: number, zone: string, nowMs: number = Date.now(), abbr?: string | null): string {
  const today = wallDate(nowMs, zone);
  const day = wallDate(ms, zone);
  // The browser names a zone in its own locale ("GMT+5:30"). The server names
  // it as the member knows it ("IST"), so a receipt passes that name in `abbr`.
  const base = new Intl.DateTimeFormat(undefined, {
    timeZone: zone,
    hour: "numeric",
    minute: "2-digit",
    ...(abbr ? {} : { timeZoneName: "short" as const }),
  }).format(new Date(ms));
  const clock = abbr ? `${base} ${abbr}` : base;
  if (day === today) return `Today, ${clock}`;
  if (day === addDays(today, 1)) return `Tomorrow, ${clock}`;
  if (day === addDays(today, -1)) return `Yesterday, ${clock}`;
  const label = new Intl.DateTimeFormat(undefined, { timeZone: zone, weekday: "short", day: "numeric", month: "short" }).format(
    new Date(ms),
  );
  return `${label}, ${clock}`;
}

/** "in 2 hours", "in 20 minutes", "in 3 days", or "5 minutes ago". Never finer than a minute. */
export function relativeWhen(ms: number, nowMs: number = Date.now()): string {
  const diff = ms - nowMs;
  const abs = Math.abs(diff);
  if (abs < MINUTE) return diff >= 0 ? "in under a minute" : "just now";
  let n: number;
  let unit: string;
  if (abs < HOUR) {
    n = Math.round(abs / MINUTE);
    unit = "minute";
  } else if (abs < 36 * HOUR) {
    n = Math.round(abs / HOUR);
    unit = "hour";
  } else {
    n = Math.round(abs / (24 * HOUR));
    unit = "day";
  }
  const words = `${n} ${unit}${n === 1 ? "" : "s"}`;
  return diff >= 0 ? `in ${words}` : `${words} ago`;
}

// ── The presets (RX-2) ───────────────────────────────────────────────────────

export interface LaterPreset {
  label: string;
  time: ZonedTime;
}

/** The labels of the presets of "Later", in order (RX-2). */
export const LATER_LABELS = ["1 hour", "This evening", "Tomorrow morning"] as const;

/**
 * The presets of "Later" and of "Change time": 1 hour from now, this evening
 * at 18:00, and tomorrow morning at 09:00, all on the wall of `zone`. This
 * evening is left out when it is less than 30 minutes away or past.
 */
export function laterPresets(nowMs: number, zone: string): LaterPreset[] {
  const out: LaterPreset[] = [];
  const hourMs = Math.floor((nowMs + HOUR) / MINUTE) * MINUTE;
  out.push({ label: LATER_LABELS[0], time: { ms: hourMs, iso: instantIso(hourMs, zone) } });
  const today = wallDate(nowMs, zone);
  const evening = zonedToIso(today, "18:00", zone);
  if (evening && evening.ms - nowMs >= 30 * MINUTE) out.push({ label: LATER_LABELS[1], time: evening });
  const morning = zonedToIso(addDays(today, 1), "09:00", zone);
  if (morning) out.push({ label: LATER_LABELS[2], time: morning });
  return out;
}
