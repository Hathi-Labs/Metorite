// The customer roster — WS-31, the operator UX rebuild.
//
// ⚠️ The subject is what an operator SEES FIRST. The old page listed customers
// in arrival order with four lifecycle counts, and answered neither question a
// person opens it with: *is anything wrong*, and *with whom*.
//
// This app's suite has no React renderer, so the ordering and the flags are
// pure functions and this file is the fence.

import { describe, expect, it } from "vitest";

import type { OrgRow, SeatRow } from "./format";
import {
  attentionFlags,
  filterRoster,
  matchesQuery,
  rosterTotals,
  sortRoster,
} from "./roster";

const NOW = new Date("2026-08-29T00:00:00Z");
const inDays = (n: number) =>
  new Date(NOW.getTime() + n * 86400_000).toISOString();

// ⚠️ `oversubscribed` is the SERVER's flag, not something `seatsTotals`
// derives — it ORs the flag across plans while summing the counts. The fixture
// therefore sets it explicitly, and the default mirrors the server's own rule
// so a test cannot accidentally assert against an impossible row.
const SEATS = (
  purchased: number,
  assigned: number,
  oversubscribed = assigned > purchased,
): SeatRow[] => [
  {
    plan_slug: "core",
    purchased,
    assigned,
    available: Math.max(0, purchased - assigned),
    oversubscribed,
  },
];

const ORG = (over: Partial<OrgRow> = {}): OrgRow => ({
  slug: "acme",
  name: "Acme Ltd",
  status: "active",
  subscription_status: "active",
  provider: null,
  trial_ends_at: null,
  current_period_end: null,
  export_until: null,
  credit_balance: "0",
  mrr_paise: 100_000,
  seats: SEATS(10, 4),
  ...over,
});

describe("what wants a human today", () => {
  it("says nothing about a healthy paying customer", () => {
    expect(attentionFlags(ORG(), NOW)).toEqual([]);
  });

  it("flags a trial about to end", () => {
    const f = attentionFlags(
      ORG({ status: "trial", trial_ends_at: inDays(3) }),
      NOW,
    );
    expect(f.map((x) => x.kind)).toContain("trial-ending");
    expect(f[0].label).toContain("3d");
  });

  it("distinguishes an EXPIRED trial from one merely ending", () => {
    // 🔴 Different actions. One is a sales call this week; the other is a
    // customer already locked out who thinks we are broken.
    const f = attentionFlags(
      ORG({ status: "trial", trial_ends_at: inDays(-2) }),
      NOW,
    );
    expect(f[0].kind).toBe("trial-expired");
    expect(f[0].tone).toBe("danger");
  });

  it("does not flag a trial that is comfortably far off", () => {
    expect(
      attentionFlags(ORG({ status: "trial", trial_ends_at: inDays(30) }), NOW),
    ).toEqual([]);
  });

  it("🔴 flags an ACTIVE customer who is oversubscribed", () => {
    // ⚠️ The whole reason attention is not a status. This customer is active,
    // paying, and using six seats nobody is billed for.
    const f = attentionFlags(ORG({ seats: SEATS(4, 10) }), NOW);
    expect(f[0].kind).toBe("oversubscribed");
    expect(f[0].label).toContain("6");
  });

  it("🔴 does not print '0 unpaid seats' when the flag and the sum disagree", () => {
    // ⚠️ `seatsTotals` ORs `oversubscribed` across plans while SUMMING the
    // counts. An org over on one plan and under on another is genuinely
    // oversubscribed with a difference of zero — and the first version of this
    // label subtracted, so it rendered "0 unpaid seats" and, on the
    // under-heavy case, a negative number.
    const f = attentionFlags(ORG({ seats: SEATS(10, 10, true) }), NOW);
    expect(f[0].kind).toBe("oversubscribed");
    expect(f[0].label).toBe("seats oversubscribed");
    expect(f[0].label).not.toContain("0 unpaid");
  });

  it("still quantifies when the sum agrees with the flag", () => {
    const f = attentionFlags(ORG({ seats: SEATS(4, 10) }), NOW);
    expect(f[0].label).toBe("6 unpaid seats");
  });

  it("leads with the thing that costs money when several apply", () => {
    // Ordering is contractual: the caller renders these in order and a row with
    // three chips must lead with the most expensive.
    const f = attentionFlags(
      ORG({
        status: "trial",
        subscription_status: "past_due",
        trial_ends_at: inDays(1),
        seats: SEATS(1, 5),
      }),
      NOW,
    );
    expect(f[0].kind).toBe("past-due");
    expect(f).toHaveLength(3);
  });
});

describe("the headline numbers", () => {
  it("sums MRR across the roster", () => {
    // The number the owner opens this page for, and it was not on it.
    const t = rosterTotals([ORG(), ORG({ slug: "b", mrr_paise: 50_000 })], NOW);
    expect(t.mrrPaise).toBe(150_000);
    expect(t.customers).toBe(2);
  });

  it("counts how many need attention, not just how many exist", () => {
    const t = rosterTotals(
      [ORG(), ORG({ slug: "b", seats: SEATS(1, 9) })],
      NOW,
    );
    expect(t.needsAttention).toBe(1);
  });

  it("survives a missing MRR without producing NaN", () => {
    // A NaN in the headline renders as "NaN" and destroys trust in every other
    // number on the page.
    const t = rosterTotals(
      [ORG({ mrr_paise: undefined as unknown as number })],
      NOW,
    );
    expect(t.mrrPaise).toBe(0);
  });
});

describe("finding one customer", () => {
  it("matches the SLUG, not only the display name", () => {
    // ⚠️ The slug is what is in a URL, a ticket and a log line — it is what an
    // operator has in their clipboard.
    expect(matchesQuery(ORG(), "acme")).toBe(true);
    expect(matchesQuery(ORG({ name: "Totally Different" }), "acme")).toBe(true);
  });

  it("is case-insensitive and ignores surrounding space", () => {
    expect(matchesQuery(ORG(), "  ACME ")).toBe(true);
  });

  it("an empty query matches everything rather than nothing", () => {
    expect(matchesQuery(ORG(), "")).toBe(true);
  });
});

describe("filtering", () => {
  const ROWS = [
    ORG({ slug: "healthy" }),
    ORG({ slug: "trialling", status: "trial", trial_ends_at: inDays(2) }),
    ORG({ slug: "susp", status: "suspended" }),
  ];

  it("has an ATTENTION filter, which is the point of the page", () => {
    expect(filterRoster(ROWS, "", "attention", NOW).map((o) => o.slug)).toEqual([
      "trialling",
    ]);
  });

  it("combines the query with the filter", () => {
    expect(filterRoster(ROWS, "susp", "suspended", NOW)).toHaveLength(1);
    expect(filterRoster(ROWS, "susp", "active", NOW)).toHaveLength(0);
  });

  it("returns everything under `all` with no query", () => {
    expect(filterRoster(ROWS, "", "all", NOW)).toHaveLength(3);
  });
});

describe("ordering", () => {
  it("🔴 puts whoever needs a human at the top", () => {
    // The old default was arrival order, which is meaningless.
    const rows = [
      ORG({ slug: "fine", mrr_paise: 900_000 }),
      ORG({ slug: "broken", mrr_paise: 1, seats: SEATS(1, 4) }),
    ];
    expect(sortRoster(rows, NOW)[0].slug).toBe("broken");
  });

  it("then ranks by revenue", () => {
    const rows = [
      ORG({ slug: "small", mrr_paise: 1 }),
      ORG({ slug: "big", mrr_paise: 900_000 }),
    ];
    expect(sortRoster(rows, NOW).map((o) => o.slug)).toEqual(["big", "small"]);
  });

  it("does not mutate the array it was given", () => {
    const rows = [ORG({ slug: "a", mrr_paise: 1 }), ORG({ slug: "b", mrr_paise: 2 })];
    sortRoster(rows, NOW);
    expect(rows.map((o) => o.slug)).toEqual(["a", "b"]);
  });
});
