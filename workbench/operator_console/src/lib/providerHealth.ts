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
// Pure and client-safe: the section's client component imports it.

import type { VendorHealth, VendorHealthStatus } from "./contract";
import type { Tone } from "./tone";

const STATUSES: readonly VendorHealthStatus[] = [
  "out",
  "refusing",
  "low",
  "probe_failed",
  "unknown",
  "ok",
];

const TONE: Record<VendorHealthStatus, Tone> = {
  out: "danger",
  refusing: "danger",
  low: "warn",
  probe_failed: "warn",
  unknown: "neutral",
  ok: "ok",
};

const LABEL: Record<VendorHealthStatus, string> = {
  out: "Out of funds",
  refusing: "Refusing calls",
  low: "Low balance",
  probe_failed: "Check failed",
  unknown: "Balance not visible",
  ok: "OK",
};

function asStatus(value: unknown): VendorHealthStatus {
  // ⚠️ A status this app does not know is drawn as `unknown`, never as `ok`.
  // A newer Console may add one, and green is the one wrong guess.
  return typeof value === "string" && (STATUSES as readonly string[]).includes(value)
    ? (value as VendorHealthStatus)
    : "unknown";
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
    .map((r) => ({
      provider: r.provider as string,
      status: asStatus(r.status),
      reason: str(r.reason) ?? "",
      balance: str(r.balance),
      currency: str(r.currency),
      available: typeof r.available === "boolean" ? r.available : null,
      balanceCheckedAt: str(r.balance_checked_at),
      probeError: str(r.probe_error),
      balanceExposed: r.balance_exposed === true,
      threshold: str(r.threshold),
      daysLeft: str(r.days_left),
      lastRefusalStatus: typeof r.last_refusal_status === "number" ? r.last_refusal_status : null,
      lastRefusalAt: str(r.last_refusal_at),
      refusals24h: num(r.refusals_24h),
      serverErrors24h: num(r.server_errors_24h),
    }));
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
  text: string;
  providers: string[];
};

/** The site-wide banner, or null.
 *
 * 🔴 `out` and `refusing` are DANGER: AI through that vendor is failing now.
 * `low` is a WARNING: it will fail soon. `unknown` and `probe_failed` alone
 * draw nothing — the Providers page shows them, and a banner that never goes
 * away is a banner nobody reads. */
export function healthAlert(rows: VendorHealth[]): HealthAlert | null {
  const failing = rows.filter((r) => r.status === "out" || r.status === "refusing");
  if (failing.length > 0) {
    const names = failing.map((r) => r.provider);
    return {
      tone: "danger",
      providers: names,
      text:
        `${list(names)} ${names.length === 1 ? "is" : "are"} refusing our calls. ` +
        "Every AI call that runs through " +
        (names.length === 1 ? "it" : "them") +
        " fails until the account is topped up or the key is fixed.",
    };
  }
  const low = rows.filter((r) => r.status === "low");
  if (low.length > 0) {
    const names = low.map((r) => r.provider);
    return {
      tone: "warn",
      providers: names,
      text:
        `${list(names)} ${names.length === 1 ? "is" : "are"} running low. ` +
        "Top up before the balance reaches zero, or AI through " +
        (names.length === 1 ? "it" : "them") +
        " stops.",
    };
  }
  return null;
}

/** The banner's class. ⚠️ `.banner` alone IS the warning look and
 * `.banner.danger` the red one. There is no `.banner.warn` in globals.css, and
 * a class it does not define would fall back to the warning look silently. */
export function alertBannerClass(alert: HealthAlert): string {
  return alert.tone === "danger" ? "banner danger" : "banner";
}

export function alertHeadline(alert: HealthAlert): string {
  return alert.tone === "danger" ? "AI is failing." : "A vendor balance is low.";
}

function list(names: string[]): string {
  if (names.length <= 1) return names.join("");
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}
