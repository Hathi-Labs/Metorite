// ── Display formatting — pure, so it is testable and shared ───────────────
//
// Every number and instant this app renders goes through here. Fracktal sells
// in INR only (specs/crm_app.md §1 non-goals: a `currency` column exists so
// multi-currency is additive later), which is why the money helpers take the
// currency rather than assuming it — the assumption belongs in the data, not
// in the formatter.

/**
 * ₹1,20,000 — the Indian digit grouping, not the western one.
 *
 * `Intl` with `en-IN` produces lakh/crore grouping natively; hand-rolling it
 * is the classic place a CRM shows ₹120,000 to a team that reads ₹1,20,000.
 */
export function money(
  amount: number | null | undefined,
  currency = "INR"
): string {
  if (amount === null || amount === undefined || Number.isNaN(amount)) return "—";
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: currency || "INR",
    maximumFractionDigits: 0,
  }).format(amount);
}

/**
 * A lane header's total: ₹12.4L / ₹3.2Cr, because a kanban column is 220px
 * wide and "₹1,24,00,000" wraps.
 */
export function compactMoney(
  amount: number | null | undefined,
  currency = "INR"
): string {
  if (amount === null || amount === undefined || Number.isNaN(amount)) return "—";
  const symbol = currency === "INR" || !currency ? "₹" : `${currency} `;
  const abs = Math.abs(amount);
  const sign = amount < 0 ? "-" : "";
  if (abs >= 1e7) return `${sign}${symbol}${trim(abs / 1e7)}Cr`;
  if (abs >= 1e5) return `${sign}${symbol}${trim(abs / 1e5)}L`;
  if (abs >= 1e3) return `${sign}${symbol}${trim(abs / 1e3)}K`;
  return `${sign}${symbol}${Math.round(abs)}`;
}

function trim(value: number): string {
  return value.toFixed(1).replace(/\.0$/, "");
}

/**
 * How long a deal has sat where it is (§3.4's stage-age clock).
 *
 * Returns null rather than "0d" when there is no timestamp: zero days is a
 * measurement and "we were never told" is not one — the same rule the
 * gateway's `_dwell_seconds` follows.
 */
export function stageAgeDays(
  since: string | null | undefined,
  now: Date = new Date()
): number | null {
  if (!since) return null;
  const at = new Date(since);
  if (Number.isNaN(at.getTime())) return null;
  const days = Math.floor((now.getTime() - at.getTime()) / 86_400_000);
  return days < 0 ? 0 : days;
}

/** "today" / "3d" / "5w" — a card badge, not a sentence. */
export function stageAgeLabel(
  since: string | null | undefined,
  now: Date = new Date()
): string {
  const days = stageAgeDays(since, now);
  if (days === null) return "—";
  if (days === 0) return "today";
  if (days < 7) return `${days}d`;
  if (days < 60) return `${Math.floor(days / 7)}w`;
  return `${Math.floor(days / 30)}mo`;
}

/** "sat in Proposal for 11 days" — the part of a timeline a human reads. */
export function dwellLabel(seconds: number | null | undefined): string | null {
  if (seconds === null || seconds === undefined) return null;
  if (seconds < 3600) return `${Math.max(1, Math.round(seconds / 60))} min`;
  if (seconds < 86_400) return `${Math.round(seconds / 3600)} hr`;
  const days = Math.round(seconds / 86_400);
  return days === 1 ? "1 day" : `${days} days`;
}

/** A short absolute date — CRM history is scanned, not read line by line. */
export function shortDate(value: string | null | undefined): string {
  if (!value) return "—";
  const at = new Date(value);
  if (Number.isNaN(at.getTime())) return "—";
  return at.toLocaleDateString("en-IN", {
    day: "numeric",
    month: "short",
    year: at.getFullYear() === new Date().getFullYear() ? undefined : "numeric",
  });
}

export function dateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const at = new Date(value);
  if (Number.isNaN(at.getTime())) return "—";
  return `${shortDate(value)}, ${at.toLocaleTimeString("en-IN", {
    hour: "numeric",
    minute: "2-digit",
  })}`;
}

/**
 * The day of a due date, counted from the member's today: 0 is today, -1 is
 * yesterday, 2 is the day after tomorrow. Null when there is no date.
 *
 * The day rule of `crm_platform.md` section 19.7, and the only copy of it.
 * A task's `due_at` holds a date at 00:00 UTC (the Zoho sync and the composer
 * both write it so), so for `allDay` the UTC date of `at` is the due day. A
 * call or a meeting is an instant, so its due day is its local date. Today is
 * always the local date of `now`.
 *
 * ⚠️ Comparing the instant with `now` instead calls a task due today
 * "overdue" for every member east of UTC, from 00:00 their time.
 */
export function dueDay(
  at: string | null | undefined,
  allDay: boolean,
  now: Date = new Date()
): number | null {
  if (!at) return null;
  const when = new Date(at);
  if (Number.isNaN(when.getTime())) return null;
  const due = allDay
    ? Date.UTC(when.getUTCFullYear(), when.getUTCMonth(), when.getUTCDate())
    : Date.UTC(when.getFullYear(), when.getMonth(), when.getDate());
  const today = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate());
  return Math.round((due - today) / 86_400_000);
}

/** The bit of an email address a card has room for. */
export function shortEmail(email: string | null | undefined): string {
  if (!email) return "—";
  return email.split("@")[0] || email;
}

/** Two letters for an avatar chip; never blank, never longer. */
export function initials(...parts: (string | null | undefined)[]): string {
  const letters = parts
    .map((p) => (p || "").trim())
    .filter(Boolean)
    .map((p) => p[0]!.toUpperCase());
  return letters.slice(0, 2).join("") || "?";
}
