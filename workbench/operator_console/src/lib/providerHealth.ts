// Provider balance and health — what the console DRAWS from
// `GET /providers/health`. Owner request, 2026-09-28.
//
// 🔴 **Why this exists.** From 2026-09-26 to 2026-09-28 the DeepSeek account
// held -0.05 USD. DeepSeek refused every call with 402, the Router answered
// 502, and all AI on the platform failed for two days with nobody told.
//
// ⚠️ **The STATUS is the Console's, never ours.** `provider_balance.assess`
// decides it, with the vendor's flag, the balance, the refusals and the
// runway in hand. This file maps it to a tone and a sentence. A second copy of
// the rule here would drift from the one that logs the alert.
//
// ⚠️ **`unknown` is NEUTRAL, never green.** "We cannot see the balance" is not
// "the balance is fine". The chip says so, and the site banner stays quiet for
// it, because an alert that is always on teaches people to ignore it.
//
// 🔴 **The headline names the CAUSE** (PR #524 review). A 402 means "top up",
// a 401 or 403 means "fix the key", and a 429 means "we are calling too fast".
// One red "top up" line for all three sends somebody to pay for the wrong fix.
//
// Pure and client-safe: the section's client component imports it.

import type { VendorHealth, VendorHealthCause, VendorHealthStatus } from "./contract";
import type { Tone } from "./tone";

const STATUSES: readonly VendorHealthStatus[] = [
  "out",
  "refusing",
  "rate_limited",
  "low",
  "probe_failed",
  "unknown",
  "ok",
];

const CAUSES: readonly VendorHealthCause[] = [
  "payment",
  "key",
  "rate_limit",
  "balance",
  "probe",
  "invisible",
];

const TONE: Record<VendorHealthStatus, Tone> = {
  out: "danger",
  refusing: "danger",
  // ⚠️ AMBER. A rate limit is not an empty account and not a dead key.
  rate_limited: "warn",
  low: "warn",
  probe_failed: "warn",
  unknown: "neutral",
  ok: "ok",
};

const LABEL: Record<VendorHealthStatus, string> = {
  out: "Out of credit",
  refusing: "Key rejected",
  rate_limited: "Rate-limited",
  low: "Low balance",
  probe_failed: "Check failed",
  unknown: "Balance not visible",
  ok: "OK",
};

/** The cause a status implies, for a Console that sends none. */
const DEFAULT_CAUSE: Partial<Record<VendorHealthStatus, VendorHealthCause>> = {
  out: "payment",
  refusing: "key",
  rate_limited: "rate_limit",
  low: "balance",
};

function asStatus(value: unknown): VendorHealthStatus {
  // ⚠️ A status this app does not know is drawn as `unknown`, never as `ok`.
  // A newer Console may add one, and green is the one wrong guess.
  return typeof value === "string" && (STATUSES as readonly string[]).includes(value)
    ? (value as VendorHealthStatus)
    : "unknown";
}

function asCause(value: unknown, status: VendorHealthStatus): VendorHealthCause | null {
  if (typeof value === "string" && (CAUSES as readonly string[]).includes(value)) {
    return value as VendorHealthCause;
  }
  return DEFAULT_CAUSE[status] ?? null;
}

const str = (v: unknown): string | null => (typeof v === "string" && v !== "" ? v : null);
const num = (v: unknown): number => (typeof v === "number" && Number.isFinite(v) ? v : 0);

/** The wire rows, read defensively. A row without a provider is dropped. */
export function healthFromWire(body: unknown): VendorHealth[] {
  const rows = (body as { providers?: unknown })?.providers;
  if (!Array.isArray(rows)) return [];
  return rows
    .filter((r): r is Record<string, unknown> => !!r && typeof r === "object")
    .filter((r) => typeof r.provider === "string" && r.provider !== "")
    .map((r) => {
      const status = asStatus(r.status);
      return {
        provider: r.provider as string,
        status,
        cause: asCause(r.cause, status),
        reason: str(r.reason) ?? "",
        balance: str(r.balance),
        currency: str(r.currency),
        available: typeof r.available === "boolean" ? r.available : null,
        balanceCheckedAt: str(r.balance_checked_at),
        probeError: str(r.probe_error),
        balanceExposed: r.balance_exposed === true,
        threshold: str(r.threshold),
        daysLeft: str(r.days_left),
        lastRefusalStatus:
          typeof r.last_refusal_status === "number" ? r.last_refusal_status : null,
        lastRefusalAt: str(r.last_refusal_at),
        lastSuccessAt: str(r.last_success_at),
        refusals24h: num(r.refusals_24h),
        serverErrors24h: num(r.server_errors_24h),
      };
    });
}

export function healthTone(status: VendorHealthStatus): Tone {
  return TONE[status] ?? "neutral";
}

export function healthLabel(status: VendorHealthStatus): string {
  return LABEL[status] ?? LABEL.unknown;
}

/** The balance, in the vendor's own currency. Nothing is converted. */
export function describeBalance(row: VendorHealth): string {
  if (row.balance === null) {
    return row.balanceExposed ? "not read yet" : "not exposed";
  }
  const n = Number(row.balance);
  const shown = Number.isFinite(n)
    ? n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 4 })
    : row.balance;
  return row.currency ? `${shown} ${row.currency}` : shown;
}

export function describeDaysLeft(row: VendorHealth): string {
  if (row.daysLeft === null) return "—";
  const n = Number(row.daysLeft);
  if (!Number.isFinite(n)) return "—";
  return n === 1 ? "about 1 day" : `about ${n} days`;
}

export function describeRefusals(row: VendorHealth): string {
  if (row.refusals24h === 0 && row.serverErrors24h === 0) return "none in 24 h";
  const parts: string[] = [];
  if (row.refusals24h > 0) {
    parts.push(
      `${row.refusals24h} refused` +
        (row.lastRefusalStatus !== null ? ` (last ${row.lastRefusalStatus})` : ""),
    );
  }
  if (row.serverErrors24h > 0) parts.push(`${row.serverErrors24h} vendor errors`);
  return `${parts.join(", ")} in 24 h`;
}

export type HealthAlert = {
  tone: "danger" | "warn";
  /** Names the cause: top up, fix the key, or slow down. */
  headline: string;
  text: string;
  providers: string[];
};

const causeOf = (r: VendorHealth): VendorHealthCause | null =>
  r.cause ?? DEFAULT_CAUSE[r.status] ?? null;

function names(rows: VendorHealth[]): { list: string; verb: string; it: string } {
  const n = rows.map((r) => r.provider);
  return {
    list: joinNames(n),
    verb: n.length === 1 ? "is" : "are",
    it: n.length === 1 ? "it" : "them",
  };
}

/** The site-wide banner, or null.
 *
 * 🔴 `out` and `refusing` are DANGER: AI through that vendor is failing now.
 * `rate_limited` and `low` are WARNINGS, amber. `unknown` and `probe_failed`
 * alone draw nothing — the Providers page shows them, and a banner that never
 * goes away is a banner nobody reads.
 *
 * ⚠️ The Console already demands a REPEAT before a 403 or a 429 counts, and a
 * served call clears it. So one moderated prompt never reaches this banner. */
export function healthAlert(rows: VendorHealth[]): HealthAlert | null {
  const failing = rows.filter((r) => r.status === "out" || r.status === "refusing");
  if (failing.length > 0) {
    const payment = failing.filter((r) => causeOf(r) === "payment");
    const key = failing.filter((r) => causeOf(r) !== "payment");
    const sentences: string[] = [];
    if (payment.length > 0) {
      const w = names(payment);
      sentences.push(
        `${w.list} ${w.verb} out of credit. Every AI call through ${w.it} fails ` +
          "until the account is topped up.",
      );
    }
    if (key.length > 0) {
      const w = names(key);
      sentences.push(
        `${w.list} ${w.verb} rejecting our key. Every AI call through ${w.it} fails ` +
          "until the key is fixed.",
      );
    }
    return {
      tone: "danger",
      headline:
        key.length === 0
          ? "Out of credit — top up."
          : payment.length === 0
            ? "Key rejected."
            : "AI is failing.",
      text: sentences.join(" "),
      providers: failing.map((r) => r.provider),
    };
  }

  const limited = rows.filter((r) => r.status === "rate_limited");
  const low = rows.filter((r) => r.status === "low");
  if (limited.length === 0 && low.length === 0) return null;
  const sentences: string[] = [];
  if (limited.length > 0) {
    const w = names(limited);
    sentences.push(
      `${w.list} ${w.verb} rate-limiting our calls. Some AI calls through ${w.it} ` +
        "may fail or wait. The account is not empty.",
    );
  }
  if (low.length > 0) {
    const w = names(low);
    sentences.push(
      `${w.list} ${w.verb} running low. Top up before the balance reaches zero, ` +
        `or AI through ${w.it} stops.`,
    );
  }
  return {
    tone: "warn",
    headline:
      low.length === 0 ? "Rate-limited." : limited.length === 0 ? "Balance low." : "Vendor warning.",
    text: sentences.join(" "),
    providers: [...limited, ...low].map((r) => r.provider),
  };
}

/** The banner's class. `globals.css` defines `.banner.danger` and
 * `.banner.warn`, and `.banner` alone already draws the warning look. The
 * warning class is named anyway, so the tone is stated and never implied. */
export function alertBannerClass(alert: HealthAlert): string {
  return alert.tone === "danger" ? "banner danger" : "banner warn";
}

export function alertHeadline(alert: HealthAlert): string {
  return alert.headline;
}

function joinNames(n: string[]): string {
  if (n.length <= 1) return n.join("");
  return `${n.slice(0, -1).join(", ")} and ${n[n.length - 1]}`;
}
