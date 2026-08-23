import { describe, it, expect } from "vitest";
import {
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
  it("offers Suspend for a live org", () => {
    expect(lifecycleActions("active")).toEqual([
      { label: "Suspend access", target: "suspended" },
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

  it("offers Resume for a suspended org", () => {
    expect(lifecycleActions("suspended")).toEqual([
      { label: "Resume access", target: "active" },
    ]);
  });

  it("offers nothing on a terminal lifecycle", () => {
    expect(lifecycleActions("cancelled")).toEqual([]);
    expect(lifecycleActions("deleted")).toEqual([]);
  });

  // The button renders `a.label` verbatim, so a target arriving without a
  // distinct label ships a mislabelled control — which is how "Resume access"
  // would have appeared on a trial org's Activate button.
  it("gives every offered target a distinct, non-empty label", () => {
    for (const status of ["active", "trial", "past_due", "suspended"]) {
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
