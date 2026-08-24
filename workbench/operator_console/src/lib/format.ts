// Pure display helpers for the operator console.
//
// ⚠️ NO money arithmetic happens here beyond formatting. MRR and prices arrive
// as integer PAISE from the Console (the ONE denomination, customer_console.md
// §9.2) and are only formatted, never summed, on this side — the browser
// formats, it never converts. Credit balances arrive as decimal strings and are
// rendered verbatim. This mirrors the customer workbench's rule that the client
// names no price and sums no basket.

export type SeatRow = {
  plan_slug: string;
  purchased: number;
  assigned: number;
  available: number;
  oversubscribed: boolean;
};

export type OrgRow = {
  slug: string;
  name: string;
  status: string;
  subscription_status: string | null;
  provider: string | null;
  trial_ends_at: string | null;
  current_period_end: string | null;
  export_until: string | null;
  credit_balance: string;
  mrr_paise: number;
  seats: SeatRow[];
};

export type OrgList = { organizations: OrgRow[] };
/**
 * One member of a customer's organization, from `GET /billing/summary`
 * (D49 · `launch_surface.md` §7 / LS-9).
 *
 * The operator console could already assign and release a seat by typed email;
 * what it could not do was SEE whom to act on. `seats` is the member's live
 * plan slugs — **empty means Unassigned** — from the same pair of store reads
 * the customer's own Organisation surface uses, so an operator and a customer
 * admin looking at one organization cannot be shown different answers.
 */
export type MemberRow = {
  email: string;
  role: string;
  status: string;
  seats: string[];
};

/** Whether this member holds any live seat. The whole of Seated vs Unassigned. */
export function isSeated(m: MemberRow): boolean {
  return Array.isArray(m.seats) && m.seats.length > 0;
}

/**
 * Read the roster off a `/billing/summary` body, tolerating its absence.
 *
 * A Console predating LS-9 sends no `members` key at all, and this console is
 * deployed independently of it — so `[]` here must mean "no roster arrived",
 * which the caller renders as a notice rather than as "this customer has no
 * members". A malformed row is dropped rather than crashing the page: an
 * operator surface that white-screens on one bad row is worse than one showing
 * the other nine.
 */
export function readMembers(body: unknown): MemberRow[] {
  const raw = (body as { members?: unknown } | null)?.members;
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((m): m is MemberRow => typeof (m as MemberRow)?.email === "string")
    .map((m) => ({
      email: m.email,
      role: typeof m.role === "string" ? m.role : "",
      status: typeof m.status === "string" ? m.status : "",
      seats: Array.isArray(m.seats) ? m.seats : [],
    }));
}

/**
 * How many people hold a seat, and how many do not.
 *
 * ⚠️ **These count PEOPLE, not seats.** One person on two plans is one seated
 * row and two assigned seats; the seat counts are `seatsTotals`' — the
 * Console's own `seat_counts` — and presenting this tally as them would be
 * quietly wrong exactly where it matters.
 */
export function memberTally(
  members: MemberRow[],
): { total: number; seated: number; unassigned: number } {
  const seated = members.filter(isSeated).length;
  return { total: members.length, seated, unassigned: members.length - seated };
}


export type CatalogPlan = {
  slug: string;
  name: string;
  kind: string;
  price_paise: number;
  sort_order: number;
};

export type Catalog = { plans: CatalogPlan[] };

const INR = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 2,
});

// Integer paise → a ₹ string. Division by 100 is the only arithmetic, and it is
// exact for the integer paise the Console emits.
export function formatPaise(paise: number): string {
  return INR.format(paise / 100);
}

// A short "core 1/2, sales 0/3" seat digest for the list — assigned/purchased
// per plan, in the order the Console returned them (catalog order).
export function seatsDigest(seats: SeatRow[]): string {
  if (seats.length === 0) return "—";
  return seats
    .map((s) => `${s.plan_slug} ${s.assigned}/${s.purchased}`)
    .join(", ");
}

// A trial-expiry / date field for display: the ISO string's date part, or "—"
// when the column is null (never a coerced sentinel date).
export function formatDate(iso: string | null): string {
  if (!iso) return "—";
  return iso.slice(0, 10);
}

// The lifecycle targets the Access control offers — ADVISORY UX only.
// The Console's `assert_transition` graph is the authority and re-checks every
// move (a refused transition is a 409); this list only decides which button to
// draw, so it is deliberately coarse and never the fence.
//
// ⚠️ `trial` and `past_due` MUST offer `active`. Activating a subscription
// deliberately does NOT touch `organization.status` — a customer who pays while
// suspended holds a paid term and stays suspended until an operator posts the
// transition — so the lifecycle move is a separate operator act. Until
// 2026-08-23 this returned only Suspend for those two states, so the console
// could sell a subscription and then had no control that could take the
// organization off trial: `hathilabs` sat at `subscription=active` +
// `organization=trial` with no button able to change it.
//
// The display label lives HERE rather than in the button, so a new target
// cannot arrive with the button silently calling it "Resume access".
//
// CP-2g added the offboarding edges: cancel opens the export window from any
// live state, delete (offered from `cancelled` ONLY — the graph's rule) ends
// it. The PURGE in `deleted` is deliberately NOT an edge here — it destroys
// data rather than moving state, so it is its own act with its own typed
// confirmation (`Actions.tsx`'s DangerPanel).
export function lifecycleActions(status: string): { label: string; target: string }[] {
  if (status === "suspended") {
    return [
      { label: "Resume access", target: "active" },
      { label: "Cancel account (export window)", target: "cancelled" },
    ];
  }
  if (status === "cancelled") {
    return [
      { label: "Reinstate account", target: "active" },
      { label: "Mark deleted (ends export window)", target: "deleted" },
    ];
  }
  if (status === "deleted") return [];
  if (status === "trial" || status === "past_due") {
    return [
      { label: "Activate account", target: "active" },
      { label: "Suspend access", target: "suspended" },
      { label: "Cancel account (export window)", target: "cancelled" },
    ];
  }
  return [
    { label: "Suspend access", target: "suspended" },
    { label: "Cancel account (export window)", target: "cancelled" },
  ];
}

// A purged organization's tombstone slug (CP-2g): `<slug>-purged-<hex6>`.
// ONE home on this side — `Actions.tsx` imports it to suppress the
// DangerPanel; the Console door's `_TOMBSTONE_RE` (customer_console/main.py)
// is the other-language twin and the real fence (it 409s a tombstone
// server-side). Fenced in `format.test.ts`.
export const TOMBSTONE_RE = /-purged-[0-9a-f]{6}$/;

/**
 * Split the Console's org list into the CUSTOMER ROSTER and the purge
 * tombstones (CP-2g follow-up, owner question 2026-08-24: "should we rather
 * remove HathiLabs completely?"). A tombstone is a bookkeeping record — kept
 * because deleting the row would cascade the billing ledger away with it —
 * but it is not a customer, so it must not sit in the roster or inflate the
 * headline count. Partitioned on the SLUG shape, not on `status`: an org at
 * `deleted` that has NOT been purged yet must stay visible, because the
 * DangerPanel it needs is on its detail page.
 */
export function partitionRoster<T extends { slug: string }>(
  rows: T[],
): { roster: T[]; purged: T[] } {
  return {
    roster: rows.filter((o) => !TOMBSTONE_RE.test(o.slug)),
    purged: rows.filter((o) => TOMBSTONE_RE.test(o.slug)),
  };
}

// The nudge that closes the gap between the two statuses, or `null`.
//
// `organization.status` (the lifecycle) and `org_subscription.status` (the
// billing truth) move independently and legitimately diverge — that is why both
// are on the wire. The one pairing that is almost always an unfinished job is a
// PAID subscription under a `trial` lifecycle: the operator activated the plan
// and had no way to know a second act existed. Measured 2026-08-23.
export function lifecycleHint(
  orgStatus: string,
  subscriptionStatus: string | null,
): string | null {
  if (orgStatus === "trial" && subscriptionStatus === "active") {
    return (
      "Their subscription is already active — the account itself is still " +
      "marked trial. Use “Activate account” under Access below to finish it."
    );
  }
  return null;
}

// Whether a customer can be sold a subscription right now — a fresh/trial org
// with no active subscription. Advisory: the Console 409s a re-activation of an
// already-active subscription regardless.
export function canActivate(subscriptionStatus: string | null): boolean {
  return subscriptionStatus !== "active";
}

// Overall seats across plans — SEAT counts, never money (the no-summing rule at
// the top of this file is about paise; seats are not a denomination).
export function seatsTotals(
  seats: SeatRow[],
): { assigned: number; purchased: number; oversubscribed: boolean } | null {
  if (seats.length === 0) return null;
  let assigned = 0;
  let purchased = 0;
  let oversubscribed = false;
  for (const s of seats) {
    assigned += s.assigned;
    purchased += s.purchased;
    oversubscribed = oversubscribed || s.oversubscribed;
  }
  return { assigned, purchased, oversubscribed };
}

// Whole days from `now` until the ISO timestamp (negative = past). Takes `now`
// as a parameter so the maths is testable; callers default it. Null/garbage in,
// null out — never a coerced sentinel date.
export function daysUntil(iso: string | null, now: Date): number | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return null;
  // `|| 0` normalises Math.ceil's -0 (a few hours into the past) to +0, so
  // "today" compares equal under Object.is in callers and tests alike.
  return Math.ceil((t - now.getTime()) / 86_400_000) || 0;
}

// A human trial-expiry hint: "ends in 12 days" / "ends today" / "ended 3 days
// ago". Display copy only — the Console decides what an expired trial means.
export function trialHint(iso: string | null, now: Date): string | null {
  const d = daysUntil(iso, now);
  if (d === null) return null;
  if (d > 1) return `ends in ${d} days`;
  if (d === 1) return "ends tomorrow";
  if (d === 0) return "ends today";
  if (d === -1) return "ended yesterday";
  return `ended ${-d} days ago`;
}

// Plain-language meaning of an org lifecycle status, shown beside the pill so
// the operator never has to guess. Unknown statuses get a neutral line rather
// than an invented meaning.
export function statusHelp(status: string): string {
  switch (status) {
    case "active":
      return "Fully operational — members can sign in and use the app.";
    case "trial":
      return "Trying Metorite for free. Activate a paid plan once they subscribe.";
    case "suspended":
      return "Every sign-in for this customer is refused until you resume them.";
    case "past_due":
      return "Payment is overdue — access continues while you follow up.";
    case "cancelled":
      return (
        "Cancelled — the export window is open: sign-in works, features are " +
        "locked, data is retained. Reinstate or, when the window ends, mark " +
        "deleted."
      );
    case "deleted":
      return (
        "Deleted — sign-in refused, export window over. Data still exists " +
        "until you run the purge below."
      );
    default:
      return "";
  }
}

// Suggest a URL-safe slug from a company name (lowercase, hyphen-separated).
// Advisory autofill only — the operator can edit it, and the Console remains
// the authority on validity/uniqueness.
export function suggestSlug(name: string): string {
  return name
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 40);
}

// Why the Plan pickers can be empty, in the operator's language — or `null`
// when the ladder arrived fine.
//
// ⚠️ This exists because the page used to fold ANY non-200 catalog read into
// `plans: []`. That rendered an empty dropdown over a permanently disabled
// Activate button and said nothing, so the operator could not tell "this
// customer cannot be activated" from "this console cannot reach the price
// list" — measured 2026-08-23 against `hathilabs`, where the real cause was
// the Console's catalog door refusing the operator token.
//
// The two ways the picker ends up empty are DIFFERENT problems with different
// fixes, so they get different sentences: a failed read is a wiring fault, an
// empty-but-successful read is a catalog with no active row.
export function plansNotice(
  status: number,
  planCount: number,
): string | null {
  if (status === 401 || status === 403) {
    return (
      `The Customer Console refused this console's operator token for the ` +
      `price list (${status}). Nothing can be activated until it accepts it.`
    );
  }
  if (status === 503) {
    return "The Customer Console is not configured to serve the price list (503).";
  }
  if (status !== 200) {
    return `The Customer Console returned ${status} for the price list.`;
  }
  if (planCount === 0) {
    return (
      "The Customer Console returned an empty price list — no plan is active " +
      "in the catalog, so there is nothing to activate on."
    );
  }
  return null;
}
