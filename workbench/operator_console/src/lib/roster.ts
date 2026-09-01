// The customer roster — what an operator needs to SEE, not just what the
// Console returns.
//
// 🔴 **The list was sorted by nothing and filtered by nothing.** That is fine
// at two customers and useless at fifty, which is the number this product is
// being built for. Worse, the four headline stats counted lifecycle states and
// none of them answered the question an operator actually opens this page
// with: *is anything wrong, and with whom.*
//
// ⚠️ **Attention is not a status.** A customer can be `active`, paying, and
// still need somebody today — their trial converts tomorrow, or they have
// assigned more seats than they bought. The Console has no column for that
// because it is a JOIN of facts, so it is computed here.
//
// Logic lives in this file rather than the component for the same reason
// `readiness.ts` does: this app's suite carries no React renderer, so anything
// expressed in JSX is untested by construction.

import { daysUntil, seatsTotals, type OrgRow } from "./format";
import type { Tone } from "./tone";

/** How soon a trial counts as "about to convert or lapse". Seven days is one
 *  working week — long enough that an operator can act before the customer
 *  notices, short enough that the flag does not become wallpaper. */
export const TRIAL_SOON_DAYS = 7;

export type Attention = {
  kind: "trial-ending" | "trial-expired" | "oversubscribed" | "past-due";
  label: string;
  tone: Tone;
};

/** Everything about this customer that wants a human today.
 *
 * ⚠️ Ordered most-urgent first, because the caller renders them in order and a
 * row showing three chips must lead with the one that costs money. */
export function attentionFlags(org: OrgRow, now: Date): Attention[] {
  const out: Attention[] = [];

  if ((org.subscription_status ?? "").trim().toLowerCase() === "past_due") {
    out.push({ kind: "past-due", label: "payment failed", tone: "danger" });
  }

  if (org.status === "trial") {
    const days = daysUntil(org.trial_ends_at, now);
    if (days !== null && days < 0) {
      out.push({ kind: "trial-expired", label: "trial expired", tone: "danger" });
    } else if (days !== null && days <= TRIAL_SOON_DAYS) {
      out.push({
        kind: "trial-ending",
        label: days === 0 ? "trial ends today" : `trial ends in ${days}d`,
        tone: "warn",
      });
    }
  }

  // ⚠️ Oversubscription is a BILLING fact, not a technical one — the seats are
  // already in use and we are not charging for them.
  //
  // ⚠️ **The flag is the SERVER's, and the arithmetic is not a substitute for
  // it.** `seatsTotals` ORs `oversubscribed` across plans while SUMMING the
  // counts, so an org over on one plan and under on another is genuinely
  // oversubscribed with a difference of zero or less. Deriving the label from
  // subtraction alone printed "0 unpaid seats" and, on the under-heavy case, a
  // negative. Trust the flag, and only quantify when the sum agrees.
  const totals = seatsTotals(org.seats);
  if (totals?.oversubscribed) {
    const over = totals.assigned - totals.purchased;
    out.push({
      kind: "oversubscribed",
      label: over > 0 ? `${over} unpaid seats` : "seats oversubscribed",
      tone: "warn",
    });
  }

  return out;
}

export type RosterTotals = {
  customers: number;
  active: number;
  trial: number;
  suspended: number;
  /** Summed across the roster. Tombstones are excluded by the caller. */
  mrrPaise: number;
  needsAttention: number;
};

export function rosterTotals(rows: OrgRow[], now: Date): RosterTotals {
  const has = (s: string) => rows.filter((o) => o.status === s).length;
  return {
    customers: rows.length,
    active: has("active"),
    trial: has("trial"),
    suspended: has("suspended"),
    mrrPaise: rows.reduce((sum, o) => sum + (o.mrr_paise || 0), 0),
    needsAttention: rows.filter((o) => attentionFlags(o, now).length > 0).length,
  };
}

/** Free-text match over the two things an operator actually types.
 *
 * ⚠️ Name AND slug. The slug is what appears in a URL, a support ticket and a
 * log line, so an operator pasting one must find the customer — searching only
 * the display name would fail on exactly the input they are most likely to
 * have in their clipboard. */
export function matchesQuery(org: OrgRow, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return (
    org.name.toLowerCase().includes(q) || org.slug.toLowerCase().includes(q)
  );
}

export type RosterFilter = "all" | "attention" | "active" | "trial" | "suspended";

export function filterRoster(
  rows: OrgRow[],
  query: string,
  filter: RosterFilter,
  now: Date,
): OrgRow[] {
  return rows.filter((o) => {
    if (!matchesQuery(o, query)) return false;
    if (filter === "all") return true;
    if (filter === "attention") return attentionFlags(o, now).length > 0;
    return o.status === filter;
  });
}

/** Attention first, then by revenue, then by name.
 *
 * 🔴 The default order was insertion order, which is arrival order, which is
 * meaningless. An operator opening this page should see the customer who needs
 * them at the top without sorting anything. */
export function sortRoster(rows: OrgRow[], now: Date): OrgRow[] {
  return [...rows].sort((a, b) => {
    const aFlags = attentionFlags(a, now).length;
    const bFlags = attentionFlags(b, now).length;
    if (aFlags !== bFlags) return bFlags - aFlags;
    if (a.mrr_paise !== b.mrr_paise) return b.mrr_paise - a.mrr_paise;
    return a.name.localeCompare(b.name);
  });
}
