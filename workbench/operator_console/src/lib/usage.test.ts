// Operator usage display — WS-31, `specs/ai_metering_and_analytics.md` §5/§6.
//
// ⚠️ **The subject is what a colour CLAIMS.** The Console already refuses to
// answer where it cannot (a null margin, a null runway). The failure this file
// guards is the console painting those nulls green — telling an operator we are
// profitable on a number nobody computed.

import { describe, expect, it } from "vitest";

import {
  type OrgUsageRow,
  isOwing,
  isWalled,
  marginLabel,
  marginTone,
  orgFlags,
  rowRunwayLabel,
  runwayLabel,
  runwayTone,
  sparklinePath,
  usageHeadline,
  hasUnbilled,
  unbilledTotals,
  customerUsageState,
  breakdownCut,
  isLoss,
  readBreakdown,
} from "./usage";

const ROW = (over: Partial<OrgUsageRow> = {}): OrgUsageRow => ({
  slug: "acme",
  name: "Acme",
  // 022/025 — a clean row serves and bills. Zero here is the healthy state.
  unbilledCalls: 0,
  unbilledTokens: 0,
  calls: 10,
  credits: "100",
  members: 3,
  costUsd: "50",
  balance: "1000",
  lastSeen: "2026-08-29T00:00:00Z",
  marginRatio: "2.00",
  runwayDays: 40,
  silent: false,
  refusals: 0,
  ...over,
});

describe("margin", () => {
  it("reads healthy above the thin threshold", () => {
    expect(marginTone("2.00")).toBe("ok");
  });

  it("warns in the thin band", () => {
    expect(marginTone("1.20")).toBe("warn");
  });

  it("is DANGER below cost", () => {
    // Under 1× we bill less than the provider charges us.
    expect(marginTone("0.80")).toBe("danger");
  });

  it("🔴 paints an unmeasured margin NEUTRAL, never green", () => {
    // The Console returns null when provider cost is zero — "we have not
    // measured this", not "excellent". Green would assert profit on a number
    // nobody computed.
    expect(marginTone(null)).toBe("neutral");
    expect(marginLabel(null)).toBe("not measured");
  });

  it("treats a junk value as unmeasured rather than as zero", () => {
    // Zero would render DANGER and invent a loss.
    expect(marginTone("not-a-number")).toBe("neutral");
    expect(marginLabel("not-a-number")).toBe("not measured");
  });

  it("names the unit, so nobody reads the ratio as money", () => {
    expect(marginLabel("2.00")).toBe("2.00× cost");
  });
});

describe("runway", () => {
  it("is danger inside a week and warn inside a month", () => {
    expect(runwayTone(3)).toBe("danger");
    expect(runwayTone(20)).toBe("warn");
    expect(runwayTone(90)).toBe("ok");
  });

  it("🔴 says 'no burn', never 'forever'", () => {
    // ⚠️ Null means there is no rate to extrapolate from. Printing ∞ would
    // hide the more interesting fact, which the silent flag reports.
    expect(runwayTone(null)).toBe("neutral");
    expect(runwayLabel(null)).toBe("no burn");
  });

  it("says out of credit at zero rather than '0d left'", () => {
    expect(runwayLabel(0)).toBe("out of credit");
  });
});

describe("what wants a human", () => {
  it("says nothing about a healthy row", () => {
    expect(orgFlags(ROW())).toEqual([]);
  });

  it("leads with the thing that stops service", () => {
    const flags = orgFlags(ROW({ runwayDays: 2, marginRatio: "0.5", silent: true }));
    expect(flags[0].label).toContain("2d");
    expect(flags.map((f) => f.label)).toContain("below cost");
    expect(flags).toHaveLength(3);
  });

  it("does not flag an unmeasured margin as below cost", () => {
    expect(orgFlags(ROW({ marginRatio: null }))).toEqual([]);
  });
});

describe("walled — the customer who got NOTHING through (A5, §8.1)", () => {
  it("🔴 flags refusals with no answered call", () => {
    const row = ROW({ calls: 0, refusals: 41, silent: false });
    expect(isWalled(row)).toBe(true);
    expect(orgFlags(row).map((f) => f.label)).toContain("walled");
  });

  it("🔴 takes over from `silent`, which the refusal itself switched OFF", () => {
    // The handoff this flag exists for. A refusal moves `last_seen`, so the
    // Console stops calling a walled customer silent — and before this chip
    // existed nothing replaced it, which made the wall HARDER to see than
    // saying nothing.
    const walled = ROW({ calls: 0, refusals: 3, silent: false });
    const labels = orgFlags(walled).map((f) => f.label);
    expect(labels).toContain("walled");
    expect(labels).not.toContain("silent");
  });

  it("stays quiet when refusals sit beside real traffic", () => {
    // A customer using the product and occasionally meeting a limit is not a
    // support call. Only "nothing got through" is.
    expect(isWalled(ROW({ calls: 12, refusals: 3 }))).toBe(false);
    expect(orgFlags(ROW({ calls: 12, refusals: 3 }))).toEqual([]);
  });

  it("stays quiet for an organization that simply did nothing", () => {
    // Zero calls and zero refusals is a quiet week, not a wall.
    expect(isWalled(ROW({ calls: 0, refusals: 0 }))).toBe(false);
  });

  it("is DANGER, because the customer is getting no product at all", () => {
    const flag = orgFlags(ROW({ calls: 0, refusals: 1 }))
      .find((f) => f.label === "walled");
    expect(flag?.tone).toBe("danger");
  });

  it("counts into the headline, ahead of silent", () => {
    const line = usageHeadline([
      ROW(),
      ROW({ slug: "b", calls: 0, refusals: 9 }),
    ]);
    expect(line).toContain("1 walled");
    expect(line.indexOf("walled")).toBeLessThan(
      line.indexOf("silent") === -1 ? Infinity : line.indexOf("silent"),
    );
  });
});

describe("the sparkline", () => {
  it("draws a point per value", () => {
    const d = sparklinePath([0, 5, 10], 100, 20);
    expect(d.split(/[ML]/).filter(Boolean)).toHaveLength(3);
    expect(d.startsWith("M")).toBe(true);
  });

  it("🔴 draws a FLAT series through the middle, not off the chart", () => {
    // ⚠️ Equal values make the range zero, and dividing by it yields NaN —
    // which renders as an invisible path rather than an error. Mid-height is
    // the honest picture of "steady".
    const d = sparklinePath([7, 7, 7], 100, 20);
    expect(d).not.toContain("NaN");
    expect(d).toContain("10.0");
  });

  it("returns nothing for fewer than two points", () => {
    // One point is not a trend, and a lone moveto draws nothing anyway.
    expect(sparklinePath([5], 100, 20)).toBe("");
    expect(sparklinePath([], 100, 20)).toBe("");
  });

  it("survives a non-finite value without emitting NaN", () => {
    const d = sparklinePath([1, Number.NaN, 3], 100, 20);
    expect(d).not.toContain("NaN");
  });

  it("puts the highest value at the top of the box", () => {
    // y grows downward in SVG, so the max must land at y=0.
    expect(sparklinePath([0, 10], 100, 20)).toContain("100.0,0.0");
  });
});

describe("the headline", () => {
  it("🔴 explains an empty table instead of implying a quiet week", () => {
    // The shipped state. `usage_event` holds no rows until a provider
    // credential exists, the rate card is priced and the Router is serving.
    const line = usageHeadline([ROW({ calls: 0 })]);
    expect(line).toContain("provider credential");
    expect(line).toContain("Router");
  });

  it("counts what is active, and what is wrong", () => {
    const line = usageHeadline([
      ROW(),
      ROW({ slug: "b", runwayDays: 2 }),
      ROW({ slug: "c", silent: true }),
    ]);
    expect(line).toContain("3 organizations active");
    expect(line).toContain("1 nearly out of credit");
    expect(line).toContain("1 silent");
  });

  it("omits a count that is zero rather than printing '0 silent'", () => {
    expect(usageHeadline([ROW()])).not.toContain("0 silent");
  });
});

// ── Served and not billed (migrations 023, 025) ─────────────────────────────
//
// 🔴 The inverse of a refusal, and reading them the same way is the mistake
// these tests exist to prevent. A refusal is a customer we said NO to — they
// got nothing and owe nothing. This is a customer we said YES to and then did
// not charge: they hold their completion and we hold the vendor's bill.

describe("consumption we served and did not bill", () => {
  it("one call is enough to flag a row", () => {
    // A leak is not a threshold problem. An unbilled call means a provider
    // shape we cannot read or counts we cannot trust, and both get WORSE with
    // volume. Waiting for a round number is how the original silent
    // undercharge survived.
    expect(hasUnbilled(ROW({ unbilledCalls: 1 }))).toBe(true);
    expect(hasUnbilled(ROW({ unbilledCalls: 0 }))).toBe(false);
  });

  it("counts organizations AND calls, because they are different problems", () => {
    // "One customer, 400 calls" is a broken integration. "400 customers, one
    // call each" is a broken provider. A single number hides which you have.
    const rows = [
      ROW({ slug: "a", unbilledCalls: 400, unbilledTokens: 900_000 }),
      ROW({ slug: "b", unbilledCalls: 0, unbilledTokens: 0 }),
      ROW({ slug: "c", unbilledCalls: 2, unbilledTokens: 5_000 }),
    ];
    expect(unbilledTotals(rows)).toEqual({
      orgs: 2, calls: 402, tokens: 905_000,
    });
  });

  it("is zero across a healthy fleet", () => {
    expect(unbilledTotals([ROW(), ROW({ slug: "b" })])).toEqual({
      orgs: 0, calls: 0, tokens: 0,
    });
  });

  it("counts a row whose TOKENS are zero — that is the unreadable case", () => {
    // ⚠️ `usage_unreadable` means we could not read the counts, so its token
    // total is zero BY DEFINITION. A totals function that keyed off tokens
    // would miss precisely the worse of the two faults.
    const rows = [ROW({ unbilledCalls: 7, unbilledTokens: 0 })];
    expect(unbilledTotals(rows)).toEqual({ orgs: 1, calls: 7, tokens: 0 });
  });

  it("survives a Console that has not shipped the columns yet", () => {
    // R6: this app and the Console deploy apart. An older Console sends
    // neither field, and the board must read zero rather than NaN.
    const stale = { ...ROW(), unbilledCalls: undefined, unbilledTokens: undefined };
    expect(hasUnbilled(stale as never)).toBe(false);
    expect(unbilledTotals([stale as never])).toEqual({
      orgs: 0, calls: 0, tokens: 0,
    });
  });
});

// ── Past zero, and still being served ────────────────────────────────────
//
// 🔴 The board drew a customer at -2,956.7 credits with the same chip as one
// at exactly 0. With `CUSTOMER_CONSOLE_SPEND_GATE` off — the SHIPPED state —
// those are opposite facts: one has stopped costing us, the other is being
// given AI on our vendor bill and widening the hole with every call.
describe("a customer past zero", () => {
  it("🔴 is told apart from one that merely has nothing left", () => {
    expect(isOwing(ROW({ balance: "-2956.7040" }))).toBe(true);
    expect(isOwing(ROW({ balance: "0" }))).toBe(false);
    expect(isOwing(ROW({ balance: "0.0001" }))).toBe(false);
  });

  it("🔴 leads the chips, and takes the runway chip's place", () => {
    // Two chips saying one thing is how the expensive reading gets skimmed.
    const labels = orgFlags(
      ROW({ balance: "-500", runwayDays: 0 }),
    ).map((f) => f.label);
    expect(labels[0]).toBe("past zero — still serving");
    expect(labels).not.toContain("out of credit");
  });

  it("does not fire on a healthy balance", () => {
    expect(orgFlags(ROW()).map((f) => f.label)).not.toContain(
      "past zero — still serving",
    );
  });

  it("🔴 is counted ONCE in the headline, not as two problems", () => {
    const line = usageHeadline([
      ROW({ slug: "a", balance: "-500", runwayDays: 0 }),
    ]);
    expect(line).toContain("1 past zero and still being served");
    expect(line).not.toContain("nearly out of credit");
  });

  it("still reports a thin-but-positive customer as nearly out", () => {
    const line = usageHeadline([ROW({ slug: "b", balance: "5", runwayDays: 1 })]);
    expect(line).toContain("1 nearly out of credit");
    expect(line).not.toContain("past zero");
  });

  it("🔴 makes the runway COLUMN agree with the chip beside it", () => {
    // The two render independently. One row said "past zero — still serving"
    // and "out of credit" at once, two cells apart, which reads as two
    // different customers' facts on one line.
    const row = ROW({ balance: "-500", runwayDays: 0 });
    expect(rowRunwayLabel(row)).toBe("past zero");
    expect(orgFlags(row).map((f) => f.label)).not.toContain("out of credit");
  });

  it("leaves the column alone for a customer who is merely at zero", () => {
    expect(rowRunwayLabel(ROW({ balance: "0", runwayDays: 0 }))).toBe(
      "out of credit",
    );
    expect(rowRunwayLabel(ROW({ balance: "900", runwayDays: 12 }))).toBe(
      "12d left",
    );
  });

  it("survives a balance the Console sent in a shape we did not expect", () => {
    // A `NaN` must not become "owing" — that would put a red chip on every
    // row the moment the field changed shape.
    expect(isOwing(ROW({ balance: "unknown" }))).toBe(false);
  });
});

// ── H-133: what the customer page may claim ───────────────────────────────

describe("customerUsageState", () => {
  const ROW = {
    slug: "acme", name: "Acme", calls: 12, credits: "4.5", members: 3,
    costUsd: "0.9", balance: "100", lastSeen: null, marginRatio: "5",
    runwayDays: 30, silent: false, refusals: 0,
    unbilledCalls: 0, unbilledTokens: 0,
  };

  it("uses the fleet row when the organization is on the page", () => {
    const s = customerUsageState(ROW, []);
    expect(s.kind).toBe("full");
  });

  it("reports TRUNCATED when the series shows calls and the row is absent", () => {
    // 🔴 H-76. `/admin/usage/orgs` is capped and sorted by spend, and takes no
    // org filter. A quiet-but-real customer falls off it. Calling that "no
    // calls" would print the fleet board's truncation as a fact about them.
    const s = customerUsageState(null, [
      { day: "2026-09-01", calls: 4, credits: "0" },
    ]);
    expect(s.kind).toBe("truncated");
  });

  it("reports QUIET only when the series itself is empty", () => {
    expect(customerUsageState(null, []).kind).toBe("quiet");
    expect(
      customerUsageState(null, [{ day: "2026-09-01", calls: 0, credits: "0" }]).kind,
    ).toBe("quiet");
  });

  it("judges traffic by CALLS, never by credits", () => {
    // ⚠️ The shipped rate card is all zero, so every served call bills 0.
    // Judging by credits would call a busy month quiet until somebody prices
    // the card — and the page would say so on the customer most worth reading.
    const s = customerUsageState(null, [
      { day: "2026-09-01", calls: 900, credits: "0" },
    ]);
    expect(s.kind).toBe("truncated");
  });
});

describe("one customer's breakdown (usage slice 3)", () => {
  const body = {
    windowDays: 30,
    apps: [
      {
        app: "projects", calls: 5, credits: "81.4000", costUsd: "0.01417",
        realisedMargin: "0.98",
        agents: [{ agent: "projects-assistant", calls: 5, credits: "81.4000",
                   costUsd: "0.01417", realisedMargin: "0.98" }],
      },
    ],
    members: [
      { member: "dana@acme.com", calls: 5, credits: "81.4000",
        costUsd: "0.01417", realisedMargin: null },
    ],
    appsTotal: 1,
    membersTotal: 240,
  };

  it("reads the Console's shape, keeping money as strings", () => {
    const b = readBreakdown(body);
    expect(b?.apps[0].credits).toBe("81.4000");
    expect(b?.apps[0].agents[0].agent).toBe("projects-assistant");
    expect(b?.members[0].realisedMargin).toBeNull();
  });

  it("refuses a body it does not understand, rather than drawing it EMPTY", () => {
    // An empty table would say a busy customer spent nothing.
    expect(readBreakdown({ rows: [] })).toBeNull();
    expect(readBreakdown(null)).toBeNull();
    expect(readBreakdown("nope")).toBeNull();
  });

  it("says when a list was cut, and stays quiet when it was not", () => {
    const b = readBreakdown(body)!;
    expect(breakdownCut(b.members.length, b.membersTotal)).toBe("Showing 1 of 240");
    expect(breakdownCut(b.apps.length, b.appsTotal)).toBeNull();
  });

  it("an older Console without totals treats what it shows as the whole", () => {
    const { appsTotal: _a, membersTotal: _m, ...old } = body;
    const b = readBreakdown(old)!;
    expect(b.membersTotal).toBe(1);
  });

  it("calls a NEGATIVE margin a loss, and a missing one nothing at all", () => {
    expect(isLoss("-0.12")).toBe(true);
    expect(isLoss("0.40")).toBe(false);
    // NULL is neutral: no saved credit price, so no verdict.
    expect(isLoss(null)).toBe(false);
  });
});
