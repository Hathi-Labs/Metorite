// Operator usage — display logic.
//
// Spec: `specs/ai_metering_and_analytics.md` §5 and §6.
//
// ⚠️ **Every number arrives as a STRING.** The Console sends credits and costs
// as strings because they are money and `float` is the standard way to make a
// total disagree with the sum of its rows. Parse only to compare or to draw,
// never to re-render — the string the Console sent is the one to show.
//
// ⚠️ This app's suite has no React renderer, so anything in JSX is untested by
// construction. Every judgement below is a pure function and `usage.test.ts` is
// the fence.

import type { Tone } from "./tone";

export type OrgUsageRow = {
  slug: string;
  name: string;
  calls: number;
  credits: string;
  members: number;
  costUsd: string;
  balance: string;
  lastSeen: string | null;
  /** NULL means unanswerable, never "good". See `analytics.margin_ratio`. */
  marginRatio: string | null;
  /** NULL means "no burn to extrapolate", never "forever". */
  runwayDays: number | null;
  silent: boolean;
  /** How many times the Router REFUSED this org in the window (A5, §8.1).
   *
   * 🔴 **A refusal moves `lastSeen`, so a walled customer is NOT silent.**
   * Before this count existed, hitting a wall made a customer HARDER to find
   * than saying nothing did: `silent` switched off and no other signal
   * switched on. Refusals with no calls is the row support wants. */
  refusals: number;
};

export type UsageDay = { day: string; calls: number; credits: string };

/** Below this many credits per dollar, the row is worth a look. */
export const THIN_MARGIN = 1.5;

/** Fewer days than this and somebody should call the customer. */
export const SHORT_RUNWAY_DAYS = 7;
export const WATCH_RUNWAY_DAYS = 30;

/** How a margin ratio reads.
 *
 * 🔴 **`null` is NEUTRAL, and that is the whole point.** The Console returns
 * null when provider cost is zero, which means "we have not measured what this
 * traffic cost us". Colouring that green would tell an operator we are
 * profitable on a number nobody computed.
 */
export function marginTone(ratio: string | null): Tone {
  if (ratio === null || ratio === undefined) return "neutral";
  const n = Number(ratio);
  if (!Number.isFinite(n)) return "neutral";
  if (n < 1) return "danger";
  if (n < THIN_MARGIN) return "warn";
  return "ok";
}

/** The words beside the ratio. An operator should not have to know the unit. */
export function marginLabel(ratio: string | null): string {
  if (ratio === null || ratio === undefined) return "not measured";
  const n = Number(ratio);
  if (!Number.isFinite(n)) return "not measured";
  return `${ratio}× cost`;
}

/** How a runway reads. `null` is neutral for the same reason as margin. */
export function runwayTone(days: number | null): Tone {
  if (days === null || days === undefined) return "neutral";
  if (days <= SHORT_RUNWAY_DAYS) return "danger";
  if (days <= WATCH_RUNWAY_DAYS) return "warn";
  return "ok";
}

export function runwayLabel(days: number | null): string {
  if (days === null || days === undefined) return "no burn";
  if (days === 0) return "out of credit";
  return `${days}d left`;
}

/** Does this organization hold refusals and no answered call at all?
 *
 * 🔴 **The narrow test is the point.** Some refusals beside real traffic is a
 * customer using their product and occasionally meeting a limit. NO answered
 * call is a customer getting nothing, which is a support call that has not
 * been made yet — A5's "before they write in".
 */
export function isWalled(row: OrgUsageRow): boolean {
  return (row.refusals ?? 0) > 0 && row.calls === 0;
}

/** What wants a human on this row, most urgent first.
 *
 * ⚠️ Ordered, because the caller renders them in order and a row with three
 * chips must lead with the one that costs money. */
export function orgFlags(row: OrgUsageRow): { label: string; tone: Tone }[] {
  const out: { label: string; tone: Tone }[] = [];
  if (row.runwayDays !== null && row.runwayDays <= SHORT_RUNWAY_DAYS) {
    out.push({ label: runwayLabel(row.runwayDays), tone: "danger" });
  }
  if (marginTone(row.marginRatio) === "danger") {
    out.push({ label: "below cost", tone: "danger" });
  }
  // 🔴 **Immediately above `silent`, because it REPLACES it.** A refusal moves
  // `lastSeen`, so the moment a customer hits a wall the silent flag switches
  // off — and until this chip existed nothing switched on in its place. The
  // two are one signal handed from one flag to the other, and they cannot
  // both fire on one row.
  if (isWalled(row)) {
    out.push({ label: "walled", tone: "danger" });
  }
  if (row.silent) {
    out.push({ label: "silent", tone: "warn" });
  }
  return out;
}

/** An SVG path for a sparkline, in a 0,0 → w,h box.
 *
 * ⚠️ **A flat series draws through the MIDDLE, not along the floor.** When
 * every value is equal the range is zero, and dividing by it yields NaN — which
 * renders as an invisible path rather than an error. A flat line at mid-height
 * is the honest picture of "steady".
 *
 * ⚠️ Returns an empty string for fewer than two points. One point is not a
 * trend, and a single moveto draws nothing anyway.
 */
export function sparklinePath(
  values: number[], w: number, h: number,
): string {
  if (!Array.isArray(values) || values.length < 2) return "";
  const clean = values.map((v) => (Number.isFinite(v) ? v : 0));
  const max = Math.max(...clean);
  const min = Math.min(...clean);
  const range = max - min;
  const stepX = w / (clean.length - 1);
  return clean
    .map((v, i) => {
      const y = range === 0 ? h / 2 : h - ((v - min) / range) * h;
      return `${i === 0 ? "M" : "L"}${(i * stepX).toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
}

/** The line above the table.
 *
 * 🔴 The zero case is the shipped state — `usage_event` holds no rows until a
 * provider credential exists, the rate card is priced and the Router is on. An
 * empty table reads as a quiet week. It must read as a system nobody has used
 * yet, and say why.
 */
export function usageHeadline(rows: OrgUsageRow[]): string {
  const active = rows.filter((r) => r.calls > 0).length;
  if (active === 0) {
    return (
      "No organization has made an AI call in this window. That is expected " +
      "until a provider credential exists, the rate card is priced and the " +
      "Router is serving."
    );
  }
  const silent = rows.filter((r) => r.silent).length;
  const short = rows.filter(
    (r) => r.runwayDays !== null && r.runwayDays <= SHORT_RUNWAY_DAYS,
  ).length;
  const walled = rows.filter(isWalled).length;
  const parts = [`${active} organization${active === 1 ? "" : "s"} active`];
  if (short) parts.push(`${short} nearly out of credit`);
  // Ahead of `silent`, for the reason `orgFlags` gives: a walled customer is
  // the one who stopped being silent.
  if (walled) parts.push(`${walled} walled`);
  if (silent) parts.push(`${silent} silent`);
  return `${parts.join(" · ")}.`;
}
