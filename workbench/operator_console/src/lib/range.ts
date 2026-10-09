// The period a money page covers — WS-50 slice 7, owner request 2026-10-09:
// "I should be able to change the date range."
//
// 🔴 **The URL is the state.** `?range=30d`, `?range=month`, or
// `?range=custom&from=2026-08-01&to=2026-08-31`. A server page reads it, so a
// range can be bookmarked and sent to a colleague, and no client state can
// disagree with what the server fetched.
//
// ⚠️ **Dates are India calendar days**, the same as the Console's
// `store.RANGE_TZ`. "This month" on the 1st at 02:00 IST is October, never
// September, whatever the server's own clock zone is.
//
// ⚠️ Pure functions only. `range.test.ts` is the fence.

export type RangeKey = "7d" | "30d" | "90d" | "month" | "last-month" | "custom";

export type UsageRange = {
  key: RangeKey;
  /** Inclusive India dates, or null for a rolling "last N days" preset. */
  from: string | null;
  to: string | null;
  /** How many days the period spans, for seats prorated by the day. */
  days: number;
  /** The words a page prints: "last 30 days", "1 Aug – 31 Aug 2026". */
  label: string;
  /** Set when a custom range in the URL could not be used, and why. */
  error: string | null;
};

export const PRESETS: { key: Exclude<RangeKey, "custom">; label: string }[] = [
  { key: "7d", label: "Last 7 days" },
  { key: "30d", label: "Last 30 days" },
  { key: "90d", label: "Last 90 days" },
  { key: "month", label: "This month" },
  { key: "last-month", label: "Last month" },
];

/** The longest span the Console answers. Mirrors `store.USAGE_MAX_DAYS`. */
export const MAX_DAYS = 365;

const TZ = "Asia/Kolkata";
const ISO = /^\d{4}-\d{2}-\d{2}$/;

/** Today's date in India, as YYYY-MM-DD. */
export function todayIst(now: Date): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: TZ,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(now);
}

const toUtcDate = (iso: string): Date => new Date(`${iso}T00:00:00Z`);
const isoOf = (d: Date): string => d.toISOString().slice(0, 10);
const spanDays = (from: string, to: string): number =>
  Math.round((toUtcDate(to).getTime() - toUtcDate(from).getTime()) / 86_400_000) + 1;

function shortDate(iso: string, withYear: boolean): string {
  return toUtcDate(iso).toLocaleDateString("en-IN", {
    day: "numeric",
    month: "short",
    ...(withYear ? { year: "numeric" } : {}),
    timeZone: "UTC",
  });
}

function rangeLabel(from: string, to: string): string {
  if (from === to) return shortDate(from, true);
  const sameYear = from.slice(0, 4) === to.slice(0, 4);
  return `${shortDate(from, !sameYear)} – ${shortDate(to, true)}`;
}

function rolling(key: "7d" | "30d" | "90d", error: string | null = null): UsageRange {
  const days = key === "7d" ? 7 : key === "90d" ? 90 : 30;
  return { key, from: null, to: null, days, label: `last ${days} days`, error };
}

function fixed(key: RangeKey, from: string, to: string): UsageRange {
  return { key, from, to, days: spanDays(from, to), label: rangeLabel(from, to), error: null };
}

/** The range a page's URL asks for. An unusable custom range falls back to
 *  the last 30 days and says why, rather than guessing what was meant. */
export function rangeFrom(
  params: { range?: string | string[]; from?: string | string[]; to?: string | string[] },
  now: Date,
): UsageRange {
  const one = (v: string | string[] | undefined) => (Array.isArray(v) ? v[0] : v) ?? "";
  const key = one(params.range);
  const today = todayIst(now);

  if (key === "7d" || key === "90d") return rolling(key);
  if (key === "month") return fixed("month", `${today.slice(0, 8)}01`, today);
  if (key === "last-month") {
    const first = toUtcDate(`${today.slice(0, 8)}01`);
    const lastOfPrev = new Date(first.getTime() - 86_400_000);
    return fixed("last-month", `${isoOf(lastOfPrev).slice(0, 8)}01`, isoOf(lastOfPrev));
  }
  if (key === "custom") {
    const from = one(params.from);
    const to = one(params.to) || today;
    if (!ISO.test(from) || !ISO.test(to)) {
      return rolling("30d", "Choose both dates to see a custom range.");
    }
    if (to < from) return rolling("30d", "The end date is before the start date.");
    if (spanDays(from, to) > MAX_DAYS) {
      return rolling("30d", `A range can be at most ${MAX_DAYS} days.`);
    }
    return fixed("custom", from, to);
  }
  return rolling("30d");
}

/** The query string the Console takes for this range. */
export function consoleQuery(r: UsageRange): string {
  return r.from && r.to
    ? `from=${encodeURIComponent(r.from)}&to=${encodeURIComponent(r.to)}`
    : `days=${r.days}`;
}

/** A page link for a preset, keeping any other parameters (such as a tab). */
export function presetHref(path: string, key: RangeKey, keep: Record<string, string> = {}): string {
  const q = new URLSearchParams({ ...keep, ...(key === "30d" ? {} : { range: key }) });
  const s = q.toString();
  return s ? `${path}?${s}` : path;
}
