// Operator usage display — WS-31, `specs/ai_metering_and_analytics.md` §5/§6.
//
// ⚠️ **The subject is what a colour CLAIMS.** The Console already refuses to
// answer where it cannot (a null margin, a null runway). The failure this file
// guards is the console painting those nulls green — telling an operator we are
// profitable on a number nobody computed.

import { describe, expect, it } from "vitest";

import {
  type OrgUsageRow,
  isWalled,
  marginLabel,
  marginTone,
  orgFlags,
  runwayLabel,
  runwayTone,
  sparklinePath,
  usageHeadline,
} from "./usage";

const ROW = (over: Partial<OrgUsageRow> = {}): OrgUsageRow => ({
  slug: "acme",
  name: "Acme",
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
