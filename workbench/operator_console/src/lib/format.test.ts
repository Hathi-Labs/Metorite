import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import {
  readCreditLots,
  formatPaise,
  seatsDigest,
  formatDate,
  lifecycleActions,
  canActivate,
  seatsTotals,
  daysUntil,
  trialHint,
  statusHelp,
  suggestSlug,
  plansNotice,
  lifecycleHint,
  TOMBSTONE_RE,
  partitionRoster,
  isSeated,
  memberTally,
  readMembers,
  readKeys,
  readLedger,
  ledgerAdds,
  liveKeys,
  type SeatRow,
} from "./format";

const seat = (over: Partial<SeatRow>): SeatRow => ({
  plan_slug: "core",
  purchased: 2,
  assigned: 1,
  available: 1,
  oversubscribed: false,
  ...over,
});

describe("formatPaise", () => {
  it("renders integer paise as ₹, dividing by 100 exactly", () => {
    // 300000 paise = ₹3,000 — the MRR of core 2 + sales 3 seats at ₹600.
    expect(formatPaise(300000)).toContain("3,000");
    expect(formatPaise(0)).toContain("0");
    expect(formatPaise(150050)).toContain("1,500.50");
  });
});

describe("seatsDigest", () => {
  it("summarises assigned/purchased per plan in catalog order", () => {
    expect(
      seatsDigest([
        seat({ plan_slug: "core", assigned: 1, purchased: 2 }),
        seat({ plan_slug: "sales", assigned: 0, purchased: 3 }),
      ]),
    ).toBe("core 1/2, sales 0/3");
  });

  it("shows a dash for an org with no seats", () => {
    expect(seatsDigest([])).toBe("—");
  });
});

describe("formatDate", () => {
  it("takes the date part, and shows a dash for null", () => {
    expect(formatDate("2026-09-01T12:00:00+00:00")).toBe("2026-09-01");
    expect(formatDate(null)).toBe("—");
  });
});

describe("lifecycleActions (advisory UX only)", () => {
  it("offers Suspend and Cancel for a live org", () => {
    expect(lifecycleActions("active").map((a) => a.target)).toEqual([
      "suspended",
      "cancelled",
    ]);
  });

  // The regression this exists for: a `trial` org offered ONLY Suspend, so an
  // operator who had just activated the subscription had no control that could
  // move the organization off trial. `hathilabs`, 2026-08-23.
  it("offers Activate on a trial org, not just Suspend", () => {
    const targets = lifecycleActions("trial").map((a) => a.target);
    expect(targets).toContain("active");
    expect(targets).toContain("suspended");
  });

  it("offers Activate on past_due too — the Console permits it", () => {
    expect(lifecycleActions("past_due").map((a) => a.target)).toContain(
      "active",
    );
  });

  it("offers Resume and Cancel for a suspended org", () => {
    expect(lifecycleActions("suspended").map((a) => a.target)).toEqual([
      "active",
      "cancelled",
    ]);
  });

  // CP-2g: the offboarding path. `cancelled` is the export window — the org
  // can come back (reinstate) or go forward (deleted, the terminal). And
  // `deleted` offers NO transition: the purge is deliberately not an edge
  // here — it destroys data rather than moving state, so it lives behind its
  // own typed confirmation in the DangerPanel.
  it("offers Reinstate and Delete inside the export window", () => {
    expect(lifecycleActions("cancelled").map((a) => a.target)).toEqual([
      "active",
      "deleted",
    ]);
  });

  it("offers no transition out of deleted — the purge is not an edge", () => {
    expect(lifecycleActions("deleted")).toEqual([]);
  });

  // The button renders `a.label` verbatim, so a target arriving without a
  // distinct label ships a mislabelled control — which is how "Resume access"
  // would have appeared on a trial org's Activate button.
  it("gives every offered target a distinct, non-empty label", () => {
    for (const status of [
      "active",
      "trial",
      "past_due",
      "suspended",
      "cancelled",
    ]) {
      const actions = lifecycleActions(status);
      expect(actions.length).toBeGreaterThan(0);
      expect(actions.every((a) => a.label.trim().length > 0)).toBe(true);
      expect(new Set(actions.map((a) => a.label)).size).toBe(actions.length);
    }
  });

  // Never offer a move the Console's `_TRANSITIONS` graph would 409.
  it("never offers a target off the Console's transition graph", () => {
    const permitted: Record<string, string[]> = {
      trial: ["active", "cancelled", "suspended"],
      active: ["past_due", "suspended", "cancelled"],
      past_due: ["active", "suspended", "cancelled"],
      suspended: ["active", "cancelled"],
      cancelled: ["active", "deleted"],
      deleted: [],
    };
    for (const [status, allowed] of Object.entries(permitted)) {
      for (const a of lifecycleActions(status)) {
        expect(allowed).toContain(a.target);
      }
    }
  });
});

describe("partitionRoster (CP-2g — tombstones leave the customer roster)", () => {
  it("shelves tombstones and keeps everything else, including un-purged deleted orgs", () => {
    const rows = [
      { slug: "acme", status: "active" },
      // Deleted but NOT purged: must STAY in the roster — its detail page
      // carries the DangerPanel the operator still needs to reach.
      { slug: "beta-co", status: "deleted" },
      { slug: "hathilabs-purged-a1b2c3", status: "deleted" },
    ];
    const { roster, purged } = partitionRoster(rows);
    expect(roster.map((o) => o.slug)).toEqual(["acme", "beta-co"]);
    expect(purged.map((o) => o.slug)).toEqual(["hathilabs-purged-a1b2c3"]);
  });
});

describe("TOMBSTONE_RE (CP-2g — the purged-slug shape)", () => {
  it("matches a tombstone and nothing that merely resembles one", () => {
    expect(TOMBSTONE_RE.test("acme-purged-a1b2c3")).toBe(true);
    expect(TOMBSTONE_RE.test("acme")).toBe(false);
    // A customer whose real slug ENDS in the word is not a tombstone —
    // the suffix demands exactly six hex characters.
    expect(TOMBSTONE_RE.test("acme-purged")).toBe(false);
    expect(TOMBSTONE_RE.test("acme-purged-xyzxyz")).toBe(false);
    expect(TOMBSTONE_RE.test("acme-purged-a1b2c")).toBe(false);
    // Double-purge relics from before the server-side 409 still match.
    expect(TOMBSTONE_RE.test("acme-purged-a1b2c3-purged-d4e5f6")).toBe(true);
  });
});

describe("lifecycleHint", () => {
  it("nudges when a paid subscription sits under a trial lifecycle", () => {
    const hint = lifecycleHint("trial", "active");
    expect(hint).not.toBeNull();
    expect(hint).toContain("Activate account");
  });

  it("stays silent when the two statuses agree, or when unpaid", () => {
    expect(lifecycleHint("trial", "trial")).toBeNull();
    expect(lifecycleHint("trial", null)).toBeNull();
    expect(lifecycleHint("active", "active")).toBeNull();
    expect(lifecycleHint("suspended", "active")).toBeNull();
  });
});

describe("canActivate", () => {
  it("is false only for an already-active subscription", () => {
    expect(canActivate("trial")).toBe(true);
    expect(canActivate(null)).toBe(true);
    expect(canActivate("active")).toBe(false);
  });
});

describe("seatsTotals", () => {
  it("sums seats (not money) across plans and ORs oversubscription", () => {
    expect(
      seatsTotals([
        seat({ plan_slug: "core", assigned: 1, purchased: 2 }),
        seat({ plan_slug: "sales", assigned: 4, purchased: 3, oversubscribed: true }),
      ]),
    ).toEqual({ assigned: 5, purchased: 5, oversubscribed: true });
  });

  it("is null for an org with no seats", () => {
    expect(seatsTotals([])).toBeNull();
  });
});

describe("daysUntil / trialHint", () => {
  const now = new Date("2026-08-23T12:00:00Z");
  it("counts whole days, negative for the past, null for null/garbage", () => {
    expect(daysUntil("2026-09-04T12:00:00Z", now)).toBe(12);
    expect(daysUntil("2026-08-23T11:00:00Z", now)).toBe(0);
    expect(daysUntil("2026-08-20T12:00:00Z", now)).toBe(-3);
    expect(daysUntil(null, now)).toBeNull();
    expect(daysUntil("not-a-date", now)).toBeNull();
  });

  it("renders the human hint for future, today and past", () => {
    expect(trialHint("2026-09-04T12:00:00Z", now)).toBe("ends in 12 days");
    expect(trialHint("2026-08-24T12:00:00Z", now)).toBe("ends tomorrow");
    expect(trialHint("2026-08-23T11:00:00Z", now)).toBe("ends today");
    expect(trialHint("2026-08-20T12:00:00Z", now)).toBe("ended 3 days ago");
    expect(trialHint(null, now)).toBeNull();
  });
});

describe("statusHelp", () => {
  it("explains every known status and stays silent on unknown ones", () => {
    for (const s of ["active", "trial", "suspended", "past_due", "cancelled", "deleted"]) {
      expect(statusHelp(s).length).toBeGreaterThan(0);
    }
    expect(statusHelp("something-new")).toBe("");
  });

  it("🔴 suspended tells lifecycle.py's truth: sign-in works, features lock", () => {
    // suspended keeps LOGIN working (so they can pay) while locking
    // features — the exact distinction the backend's module note calls the
    // one people get wrong. Three surfaces said the opposite once.
    expect(statusHelp("suspended")).toContain("Sign-in still works");
    expect(statusHelp("suspended")).not.toContain("refused");
    const page = readFileSync(
      join(__dirname, "..", "app", "customers", "[slug]", "page.tsx"), "utf8");
    expect(page).toContain("Sign-in still works so they can pay");
    const actions = readFileSync(
      join(__dirname, "..", "app", "customers", "[slug]", "Actions.tsx"), "utf8");
    expect(actions).toContain("Sign-in KEEPS working so they can pay");
  });
});

describe("suggestSlug", () => {
  it("lowercases, hyphenates and strips accents/symbols", () => {
    expect(suggestSlug("Fracktal Works Pvt. Ltd.")).toBe("fracktal-works-pvt-ltd");
    expect(suggestSlug("  Café  Nine!  ")).toBe("cafe-nine");
    expect(suggestSlug("---")).toBe("");
  });
});

describe("plansNotice", () => {
  // The regression this whole change exists for: the catalog read failed, the
  // page folded it into `plans: []`, and the operator saw an empty dropdown
  // over a disabled Activate button with no reason given.
  it("names a refused operator token rather than staying silent", () => {
    for (const status of [401, 403]) {
      const notice = plansNotice(status, 0);
      expect(notice).not.toBeNull();
      expect(notice).toContain(String(status));
      expect(notice!.toLowerCase()).toContain("refused");
    }
  });

  it("tells an unconfigured Console apart from a refused one", () => {
    expect(plansNotice(503, 0)).toContain("not configured");
    expect(plansNotice(503, 0)).not.toBe(plansNotice(401, 0));
  });

  it("still reports a status it has no special sentence for", () => {
    expect(plansNotice(500, 0)).toContain("500");
    expect(plansNotice(418, 0)).toContain("418");
  });

  // The OTHER way the picker ends up empty, and a different fix — so it must
  // not borrow the wiring-fault sentence.
  it("distinguishes an empty catalog from a failed read", () => {
    const empty = plansNotice(200, 0);
    expect(empty).toContain("empty price list");
    expect(empty).not.toBe(plansNotice(401, 0));
  });

  it("is silent when the ladder actually arrived", () => {
    expect(plansNotice(200, 12)).toBeNull();
    expect(plansNotice(200, 1)).toBeNull();
  });
});

describe("the customer's member roster (LS-9 · launch_surface.md §7)", () => {
  it("reads the roster off a summary body, defaulting seats to empty", () => {
    const rows = readMembers({
      members: [
        { email: "a@x.test", role: "owner", status: "active", seats: ["core"] },
        { email: "b@x.test", role: "member", status: "invited", seats: [] },
        // A Console predating LS-7 sends no `seats` key for a member.
        { email: "c@x.test", role: "member", status: "active" },
      ],
    });
    expect(rows.map((m) => m.email)).toEqual(["a@x.test", "b@x.test", "c@x.test"]);
    expect(rows.map(isSeated)).toEqual([true, false, false]);
  });

  it("returns nothing when the body carries no members key", () => {
    // A Console predating LS-9. The CALLER must render this as "no roster
    // arrived", not "this customer has no members" — the page decides that, and
    // this only has to not invent rows.
    expect(readMembers({})).toEqual([]);
    expect(readMembers(null)).toEqual([]);
    expect(readMembers({ members: "core" })).toEqual([]);
  });

  it("drops a malformed row rather than crashing the page", () => {
    // An operator surface that white-screens on one bad row is worse than one
    // showing the other nine.
    const rows = readMembers({
      members: [
        { role: "member" },
        null,
        { email: "ok@x.test", role: "member", status: "active", seats: ["core"] },
      ],
    });
    expect(rows.map((m) => m.email)).toEqual(["ok@x.test"]);
  });

  it("coerces a malformed seats field to empty rather than trusting it", () => {
    const rows = readMembers({
      members: [{ email: "a@x.test", role: "", status: "", seats: "core" }],
    });
    expect(rows[0].seats).toEqual([]);
    expect(isSeated(rows[0])).toBe(false);
  });

  it("tallies PEOPLE, counting a multi-plan holder once", () => {
    // The trap: presenting this as the seat count. One person on two plans is
    // one seated row and two assigned seats — the seat counts are seatsTotals'.
    const rows = readMembers({
      members: [
        { email: "a@x.test", role: "", status: "", seats: ["core", "qa-second-seat"] },
        { email: "b@x.test", role: "", status: "", seats: [] },
        { email: "c@x.test", role: "", status: "", seats: [] },
      ],
    });
    expect(memberTally(rows)).toEqual({ total: 3, seated: 1, unassigned: 2 });
  });

  it("tallies an empty roster without dividing by anything", () => {
    expect(memberTally([])).toEqual({ total: 0, seated: 0, unassigned: 0 });
  });
});

describe("readKeys", () => {
  it("reads prefix, label, created_at and revoked off a GET /keys body", () => {
    const rows = readKeys({
      keys: [
        { prefix: "cc_live_a8f3", label: "prod", created_at: "2026-08-27T10:00:00Z", revoked: false },
        { prefix: "cc_live_b1c2", label: null, created_at: "2026-08-01T10:00:00Z", revoked: true },
      ],
    });
    expect(rows).toHaveLength(2);
    expect(rows[0]).toEqual({
      prefix: "cc_live_a8f3",
      label: "prod",
      created_at: "2026-08-27T10:00:00Z",
      revoked: false,
    });
    expect(rows[1].label).toBeNull();
    expect(rows[1].revoked).toBe(true);
  });

  it("returns [] when the key is absent, so a caller can say WHICH failure", () => {
    // A Console that answered without a `keys` key, or a 403 body. Both mean
    // "no list arrived" — never "this customer has no keys".
    expect(readKeys({})).toEqual([]);
    expect(readKeys(null)).toEqual([]);
    expect(readKeys({ keys: "not an array" })).toEqual([]);
  });

  it("drops a malformed row instead of white-screening the surface", () => {
    // An operator may be on this page to revoke a key that has leaked. Losing
    // the whole table to one bad row is the worse failure.
    const rows = readKeys({
      keys: [{ prefix: "cc_live_ok", created_at: "x", revoked: false }, { label: "no prefix" }, 7],
    });
    expect(rows).toHaveLength(1);
    expect(rows[0].prefix).toBe("cc_live_ok");
  });

  it("never surfaces a token or a hash, because neither is in the payload", () => {
    // Structural: `store.list_keys` excludes key_hash at the SQL level and the
    // token was never stored. This pins that the reader adds no field either.
    const rows = readKeys({
      keys: [{ prefix: "cc_live_x", created_at: "x", revoked: false, key_hash: "LEAK", token: "LEAK" }],
    });
    expect(Object.keys(rows[0]).sort()).toEqual([
      "created_at",
      "label",
      "prefix",
      "revoked",
    ]);
  });

  it("treats a non-boolean `revoked` as NOT revoked only when explicitly true", () => {
    // Fail toward showing the key. A live key hidden from the table is a
    // credential nobody can revoke.
    const rows = readKeys({
      keys: [
        { prefix: "a", created_at: "x", revoked: "true" },
        { prefix: "b", created_at: "x" },
      ],
    });
    expect(rows[0].revoked).toBe(false);
    expect(rows[1].revoked).toBe(false);
  });
});

describe("liveKeys", () => {
  it("keeps only the keys that still work", () => {
    const rows = readKeys({
      keys: [
        { prefix: "live", created_at: "x", revoked: false },
        { prefix: "dead", created_at: "x", revoked: true },
      ],
    });
    expect(liveKeys(rows).map((k) => k.prefix)).toEqual(["live"]);
  });

  it("returns an empty list rather than throwing on no keys", () => {
    expect(liveKeys([])).toEqual([]);
  });
});

describe("the credit ledger read (manual payments)", () => {
  it("parses entries and keeps money as the STRINGS the Console sent", () => {
    const rows = readLedger({
      entries: [
        { delta: "500.0000", reason: "manual", ref: "UTR-1",
          created_at: "2026-08-30T10:00:00Z" },
        { delta: "-1.2900", reason: "usage", ref: null,
          created_at: "2026-08-30T11:00:00Z" },
      ],
    });
    expect(rows).toHaveLength(2);
    expect(rows[0].delta).toBe("500.0000");
    expect(rows[1].ref).toBeNull();
  });

  it("a Console predating the read yields empty, not a crash", () => {
    expect(readLedger({})).toEqual([]);
    expect(readLedger(null)).toEqual([]);
    expect(readLedger({ entries: "nope" })).toEqual([]);
  });

  it("ledgerAdds reads the sign, and only the sign", () => {
    expect(ledgerAdds({ delta: "500.0000", reason: "manual", ref: null,
      created_at: "" })).toBe(true);
    expect(ledgerAdds({ delta: "-1.2900", reason: "usage", ref: null,
      created_at: "" })).toBe(false);
  });

  it("the wiring: the page reads the ledger, the form demands a reference for manual", () => {
    // No renderer runs here, so the wiring is fenced by source scan - the
    // same idiom as catalog.test.ts. An unverifiable manual grant must not
    // be one click away.
    const page = readFileSync(
      join(__dirname, "..", "app", "customers", "[slug]", "page.tsx"), "utf8");
    expect(page).toContain("creditLedger");
    expect(page).toContain("Credit ledger");
    const actions = readFileSync(
      join(__dirname, "..", "app", "customers", "[slug]", "Actions.tsx"), "utf8");
    expect(actions).toContain('reason === "manual" && !ref.trim()');
  });
});

// ── Credit lots (migration 027, credit_pricing.md §6) ──────────────────────

describe("reading credit lots", () => {
  it("distinguishes a MISSING key from an empty list", () => {
    // 🔴 A Console predating migration 027 sends no `credit_lots` key. That is
    // not "this customer has no lots", and drawing an empty table over a
    // missing feature would say something untrue.
    expect(readCreditLots({})).toBeUndefined();
    expect(readCreditLots({ credit_lots: [] })).toEqual([]);
  });

  it("keeps a NULL price null and never the string 'null'", () => {
    // ⚠️ Nobody paid, versus somebody paid nothing on purpose. A refund must
    // tell them apart, so the board must draw them apart.
    const [granted, promoted] = readCreditLots({
      credit_lots: [
        { id: 1, source: "grant", credits: "100", credits_used: "0",
          remaining: "100", price_paid_inr: null, expires_at: null },
        { id: 2, source: "promo", credits: "50", credits_used: "0",
          remaining: "50", price_paid_inr: "0", expires_at: null },
      ],
    })!;
    expect(granted.pricePaidInr).toBeNull();
    expect(promoted.pricePaidInr).toBe("0");
  });

  it("keeps money as the STRING the Console sent", () => {
    const [lot] = readCreditLots({
      credit_lots: [
        { id: 3, source: "purchase", credits: "110000", credits_used: "250",
          remaining: "109750", price_paid_inr: "999.00",
          expires_at: "2027-01-01T00:00:00+00:00" },
      ],
    })!;
    expect(lot.pricePaidInr).toBe("999.00");
    expect(lot.remaining).toBe("109750");
    expect(lot.expiresAt).toBe("2027-01-01T00:00:00+00:00");
  });

  it("survives a malformed payload rather than throwing", () => {
    expect(readCreditLots(null)).toBeUndefined();
    expect(readCreditLots({ credit_lots: "nope" })).toBeUndefined();
  });
});
