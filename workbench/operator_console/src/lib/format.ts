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

// The lifecycle targets the suspend/resume control offers — ADVISORY UX only.
// The Console's `assert_transition` graph is the authority and re-checks every
// move (a refused transition is a 409); this list only decides which button to
// draw, so it is deliberately coarse and never the fence.
export function lifecycleActions(status: string): { label: string; target: string }[] {
  if (status === "suspended") return [{ label: "Resume", target: "active" }];
  if (status === "cancelled" || status === "deleted") return [];
  return [{ label: "Suspend", target: "suspended" }];
}

// Whether a customer can be sold a subscription right now — a fresh/trial org
// with no active subscription. Advisory: the Console 409s a re-activation of an
// already-active subscription regardless.
export function canActivate(subscriptionStatus: string | null): boolean {
  return subscriptionStatus !== "active";
}
