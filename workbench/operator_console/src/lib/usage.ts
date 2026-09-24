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
  /** 🔴 **Calls this customer RECEIVED that we did not bill** (migrations 023
   *  and 025). The meter failed, so we absorbed the cost rather than send a
   *  number we could not defend.
   *
   * ⚠️ **This is not a refusal.** A refusal is a customer we said no to. This
   *  is a customer we said YES to and then did not charge — they hold their
   *  completion and we hold the vendor's bill. What is missing is our money,
   *  never their service.
   *
   * ⚠️ It is deliberately a COUNT and a token total, never an estimate of the
   *  money. Estimating the charge is the exact defect the partition assert
   *  exists to stop, and doing it here would be that defect wearing a
   *  different hat. */
  unbilledCalls: number;
  /** The tokens those calls consumed. ⚠️ Zero on an unreadable row BY
   *  DEFINITION — we could not read them — so a large count beside a small
   *  token total is itself the signal that the provider's SHAPE broke, not
   *  our arithmetic. */
  unbilledTokens: number;
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

/** The runway cell for one ROW, which is not the same as for one NUMBER.
 *
 * 🔴 **`runwayLabel(0)` says "out of credit" and that is right for a customer
 * at zero and wrong for one below it.** The chip list already replaces the
 * runway chip on an owing row; this is the same correction for the COLUMN,
 * which renders on its own. Without it one row said "past zero — still
 * serving" and "out of credit" at the same time, two cells apart. Measured on
 * the seeded fleet, 2026-09-20.
 */
export function rowRunwayLabel(row: OrgUsageRow): string {
  if (isOwing(row)) return "past zero";
  return runwayLabel(row.runwayDays);
}

/** Is this organization PAST zero — served on credit it does not hold?
 *
 * 🔴 **This is not the same fact as "out of credit", and the difference is
 * money.** `CUSTOMER_CONSOLE_SPEND_GATE` ships OFF (H-42: price the card,
 * THEN arm the wall), so nothing refuses a call at a zero balance. The
 * balance simply keeps going down. A customer at exactly 0 has stopped
 * costing us; a customer at -2,956 is being given AI we have paid the vendor
 * for, and every further call widens it.
 *
 * ⚠️ **The board read both as "out of credit" until 2026-09-20**, which is
 * the calmer of the two readings — it sounds like somebody who has been cut
 * off. Measured on a seeded fleet: a customer sat at -2,956.7 wearing the
 * same chip as one sat at 0.
 *
 * ⚠️ Deliberately NOT gated on reading the flag. This console cannot see the
 * customer's box (`golive.ts` says so for the same reason), and a negative
 * balance is worth a human either way: with the gate on it should have been
 * impossible, which is a bug, and with it off it is a bill.
 */
export function isOwing(row: OrgUsageRow): boolean {
  const n = Number(row.balance);
  return Number.isFinite(n) && n < 0;
}

/** What wants a human on this row, most urgent first.
 *
 * ⚠️ Ordered, because the caller renders them in order and a row with three
 * chips must lead with the one that costs money. */
export function orgFlags(row: OrgUsageRow): { label: string; tone: Tone }[] {
  const out: { label: string; tone: Tone }[] = [];
  // 🔴 Ahead of the runway chip, and it REPLACES it. "Past zero" and "out of
  // credit" are the same row seen twice, and the expensive reading leads.
  if (isOwing(row)) {
    out.push({ label: "past zero — still serving", tone: "danger" });
  } else if (row.runwayDays !== null && row.runwayDays <= SHORT_RUNWAY_DAYS) {
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
  // ⚠️ `!isOwing` — an organization past zero has a runway of 0, so without
  // this it is counted twice and the line reads "1 past zero · 1 nearly out
  // of credit" for ONE customer. The chip list excludes the same row for the
  // same reason.
  const short = rows.filter(
    (r) =>
      !isOwing(r) && r.runwayDays !== null && r.runwayDays <= SHORT_RUNWAY_DAYS,
  ).length;
  const walled = rows.filter(isWalled).length;
  const owing = rows.filter(isOwing).length;
  const parts = [`${active} organization${active === 1 ? "" : "s"} active`];
  // 🔴 First, because it is the only line here that is already costing money.
  if (owing) {
    parts.push(
      `${owing} past zero and still being served`,
    );
  }
  if (short) parts.push(`${short} nearly out of credit`);
  // Ahead of `silent`, for the reason `orgFlags` gives: a walled customer is
  // the one who stopped being silent.
  if (walled) parts.push(`${walled} walled`);
  if (silent) parts.push(`${silent} silent`);
  return `${parts.join(" · ")}.`;
}


/** Does this organization hold unbilled consumption?
 *
 * 🔴 **One call is enough to show it.** A leak is not a threshold problem: an
 * unbilled call means a provider shape we cannot read or counts we cannot
 * trust, and both get WORSE with volume rather than better. Waiting for a
 * round number to react is how the original silent undercharge survived. */
export function hasUnbilled(row: Pick<OrgUsageRow, "unbilledCalls">): boolean {
  return (row.unbilledCalls ?? 0) > 0;
}

/** The fleet total, for the banner. Counts organizations AND calls, because
 *  "one customer, 400 calls" and "400 customers, one call each" are different
 *  problems and a single number hides which one you have. */
export function unbilledTotals(rows: OrgUsageRow[]): {
  orgs: number;
  calls: number;
  tokens: number;
} {
  const hit = rows.filter(hasUnbilled);
  return {
    orgs: hit.length,
    calls: hit.reduce((n, r) => n + (r.unbilledCalls ?? 0), 0),
    tokens: hit.reduce((n, r) => n + (r.unbilledTokens ?? 0), 0),
  };
}

/** What the customer page can honestly say about one organization (H-133).
 *
 * 🔴 **Two reads, and only one of them is authoritative about "did they use
 * anything".** `GET /admin/usage/daily?org_slug=` is per-organization and
 * unfiltered. `GET /admin/usage/orgs` is a CAPPED page sorted by spend, and it
 * takes no org filter — so a quiet customer can have real traffic and still be
 * absent from it. That is H-76, and reading the absence as "no calls" would
 * put the fleet board's truncation on a customer's own page as a fact.
 *
 * ⚠️ So the SERIES decides whether anything happened, and the ROW only
 * enriches it. When they disagree, the panel says which.
 */
export type CustomerUsageState =
  | { kind: "full"; row: OrgUsageRow }
  | { kind: "truncated" }
  | { kind: "quiet" };

export function customerUsageState(
  row: OrgUsageRow | null,
  days: UsageDay[],
): CustomerUsageState {
  if (row) return { kind: "full", row };
  // ⚠️ `calls`, not `credits`. An unpriced rate card bills zero, which is the
  // shipped state — so judging traffic by credits would call every served
  // call a quiet month until the day somebody prices the card.
  const served = days.some((d) => d.calls > 0);
  return served ? { kind: "truncated" } : { kind: "quiet" };
}

// ── One customer's breakdown (usage slice 3) ────────────────────────────────

/** An agent inside an app, with our cost. `GET /admin/usage/breakdown`. */
export interface BreakdownAgent {
  agent: string;
  calls: number;
  credits: string;
  costUsd: string;
  /** A FRACTION, and NULL until a credit price is saved. NULL is neutral. */
  realisedMargin: string | null;
}

export interface BreakdownApp extends Omit<BreakdownAgent, "agent"> {
  app: string;
  agents: BreakdownAgent[];
}

export interface BreakdownMember extends Omit<BreakdownAgent, "agent"> {
  member: string;
}

export interface UsageBreakdown {
  windowDays: number;
  apps: BreakdownApp[];
  members: BreakdownMember[];
  appsTotal: number;
  membersTotal: number;
}

const asRow = (r: Record<string, unknown>) => ({
  calls: Number(r.calls) || 0,
  credits: String(r.credits ?? "0"),
  costUsd: String(r.costUsd ?? "0"),
  realisedMargin:
    r.realisedMargin === null || r.realisedMargin === undefined
      ? null
      : String(r.realisedMargin),
});

/**
 * The breakdown body, or `null` when it is not the shape this build expects.
 *
 * ⚠️ **`null` means "cannot show", never "nothing spent".** A Console that
 * predates the route answers 404 before this is reached. A body that parses
 * but lacks `apps` is a Console this build does not understand, and drawing
 * it as an empty table would tell the operator a busy customer spent nothing.
 */
export function readBreakdown(body: unknown): UsageBreakdown | null {
  if (!body || typeof body !== "object") return null;
  const b = body as Record<string, unknown>;
  if (!Array.isArray(b.apps) || !Array.isArray(b.members)) return null;
  const apps = (b.apps as Record<string, unknown>[]).map((a) => ({
    app: String(a.app ?? ""),
    ...asRow(a),
    agents: (Array.isArray(a.agents) ? (a.agents as Record<string, unknown>[]) : []).map(
      (g) => ({ agent: String(g.agent ?? ""), ...asRow(g) }),
    ),
  }));
  const members = (b.members as Record<string, unknown>[]).map((m) => ({
    member: String(m.member ?? ""),
    ...asRow(m),
  }));
  return {
    windowDays: Number(b.windowDays) || 0,
    apps,
    members,
    // A Console without the totals is one that never cuts silently for
    // fewer than a page, so the rows shown ARE the total.
    appsTotal: Number(b.appsTotal) || apps.length,
    membersTotal: Number(b.membersTotal) || members.length,
  };
}

/**
 * "Showing 100 of 240" when the list was cut, else `null`.
 *
 * 🔴 **The lists stop at a page, and a list that stops without saying so
 * reads as complete** (H-76, the fleet board's own lesson).
 */
export function breakdownCut(shown: number, total: number): string | null {
  return total > shown ? `Showing ${shown} of ${total}` : null;
}

/**
 * Whether a realised margin is below zero — we paid the vendor more than the
 * customer paid us. The only verdict this table draws. A margin above zero
 * but under a tier's floor is the tier monitor's job, and this table has no
 * floor to judge against.
 */
export function isLoss(realisedMargin: string | null): boolean {
  if (realisedMargin === null) return false;
  const n = Number(realisedMargin);
  return Number.isFinite(n) && n < 0;
}
