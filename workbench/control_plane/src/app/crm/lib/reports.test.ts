import { describe, expect, it } from "vitest";
import {
  barWidth,
  byWeight,
  dwellSummary,
  plusLabel,
  funnelWidths,
  hasFunnel,
  hasOwners,
  hasPipeline,
  hasWinLoss,
  ownerLabel,
  percentLabel,
  pipelineWidths,
  shareOfForecast,
  undatedNotice,
  windowLabel,
} from "./reports";
import type {
  FunnelReport,
  PipelineReport,
  OwnerLeaderboard,
  Status,
  WinLossReport,
} from "./types";

// ── Fixtures ──────────────────────────────────────────────────────────────
//
// Shaped exactly like routes/crm/reports.py's payloads. Nothing here computes
// a report figure: this file tests the LABELS and the GEOMETRY, because the
// math is server-side and duplicating any of it in the browser is the defect
// these helpers exist to avoid (the whole-lane weighted lesson, WS-26f f3).

function status(over: Partial<Status> & { id: string; name: string }): Status {
  return {
    color: "gray",
    position: 10,
    type: "open",
    is_default: false,
    probability: 0,
    ...over,
  };
}

const QUALIFICATION = status({ id: "s1", name: "Qualification", position: 10 });
const PROPOSAL = status({
  id: "s2",
  name: "Proposal",
  position: 30,
  type: "ongoing",
});

const PIPELINE: PipelineReport = {
  stages: [
    {
      status: QUALIFICATION,
      count: 12,
      currency: "INR",
      amount: 600000,
      weighted: 60000,
      others: [],
    },
    {
      status: PROPOSAL,
      count: 4,
      currency: "INR",
      amount: 800000,
      weighted: 400000,
      others: [{ currency: "USD", amount: 2500, weighted: 900 }],
    },
  ],
  count: 16,
  currency: "INR",
  amount: 1400000,
  weighted: 460000,
  others: [{ currency: "USD", amount: 2500, weighted: 900 }],
};

const FUNNEL: FunnelReport = {
  stages: [
    {
      status: QUALIFICATION,
      entered: 20,
      advanced: 8,
      conversion_forward: 40,
      median_dwell_days: 3,
      dwell_samples: 8,
    },
    {
      status: PROPOSAL,
      entered: 8,
      advanced: 0,
      conversion_forward: 0,
      median_dwell_days: null,
      dwell_samples: 0,
    },
  ],
  deals: 20,
  transitions: 8,
  unmatched: [],
};

const WIN_LOSS: WinLossReport = {
  window_days: 90,
  since: "2026-05-09T00:00:00+00:00",
  won: 2,
  lost: 1,
  win_rate: 66.7,
  currency: "INR",
  won_amount: 800000,
  lost_amount: 100000,
  others: [],
  average_cycle_days: 30,
  closed_without_date: 0,
  lost_reasons: [],
};

const OWNERS: OwnerLeaderboard = {
  owners: [
    {
      owner_email: "priya@fracktal.in",
      open_deals: 2,
      currency: "INR",
      weighted: 210000,
      won_amount: 0,
      others: [],
    },
  ],
  window_days: 90,
  omitted: 0,
};

// ── Geometry ──────────────────────────────────────────────────────────────

describe("barWidth", () => {
  it("scales against the biggest value in the set", () => {
    expect(barWidth(20, [20, 8, 4])).toBe(100);
    expect(barWidth(8, [20, 8, 4])).toBe(40);
  });

  it("is 0 rather than NaN for an empty set", () => {
    // `width: NaN%` collapses the chart to nothing, which reads as a failed
    // load rather than as an empty pipeline.
    expect(barWidth(0, [])).toBe(0);
    expect(barWidth(5, [0, 0])).toBe(0);
  });

  it("never renders a negative bar", () => {
    expect(barWidth(-3, [10])).toBe(0);
  });

  it("scales to the MAX, not to the first stage", () => {
    // A funnel is not guaranteed to decrease: the visited-set rule counts a
    // demoted deal in both lanes, so a later stage can legitimately be wider.
    // Scaling to stage one would render a bar wider than the chart.
    expect(barWidth(30, [10, 30])).toBe(100);
    expect(barWidth(10, [10, 30])).toBe(33);
  });
});

describe("funnelWidths / pipelineWidths", () => {
  it("emits one width per stage, in the payload's order", () => {
    expect(funnelWidths(FUNNEL)).toEqual([100, 40]);
    expect(pipelineWidths(PIPELINE)).toEqual([15, 100]);
  });

  it("survives an unloaded report", () => {
    expect(funnelWidths(null)).toEqual([]);
    expect(pipelineWidths(null)).toEqual([]);
  });
});

describe("byWeight", () => {
  it("orders the forecast by money without mutating the payload", () => {
    // The server sends `position` order (the board's order) and the surface
    // renders both from the same array.
    expect(byWeight(PIPELINE).map((s) => s.status.name)).toEqual([
      "Proposal",
      "Qualification",
    ]);
    expect(PIPELINE.stages.map((s) => s.status.name)).toEqual([
      "Qualification",
      "Proposal",
    ]);
  });
});

describe("shareOfForecast", () => {
  it("is a share of the whole board's weighted total", () => {
    expect(shareOfForecast({ weighted: 400000 }, { weighted: 460000 })).toBe(87);
  });

  it("is 0 rather than NaN when the board forecasts nothing", () => {
    expect(shareOfForecast({ weighted: 0 }, { weighted: 0 })).toBe(0);
  });
});

// ── Labels ────────────────────────────────────────────────────────────────

describe("dwellSummary", () => {
  it("carries the sample size with the median", () => {
    // `dwell_seconds` is stamped only when a deal LEAVES a stage, so this can
    // legitimately be a median of one. Presenting it without saying so is how
    // it gets quoted in a meeting.
    expect(dwellSummary({ median_dwell_days: 3, dwell_samples: 8 })).toBe(
      "3 days · 8 departures"
    );
    expect(dwellSummary({ median_dwell_days: 1, dwell_samples: 1 })).toBe(
      "1 day · 1 departure"
    );
  });

  it("says no departures rather than 0 days", () => {
    // Zero is a measurement; "nobody has left this stage" is not one — the
    // same rule the gateway's `_dwell_seconds` follows.
    expect(dwellSummary({ median_dwell_days: null, dwell_samples: 0 })).toBe(
      "no departures yet"
    );
  });
});

describe("percentLabel", () => {
  it("renders the number the server already rounded", () => {
    // Never recomputed here: the rounding rule lives in one language.
    expect(percentLabel(66.7)).toBe("66.7%");
    expect(percentLabel(0)).toBe("0%");
  });
});

describe("ownerLabel", () => {
  it("folds NULL and blank into one word", () => {
    // The server reports them as two rows because the per-owner aggregate can
    // bind `IS NULL` or `lower(owner_email) = :owner` and not an OR. Folding
    // them into a WORD is safe; folding them into a COUNT would not be.
    expect(ownerLabel(null)).toBe("Unassigned");
    expect(ownerLabel("")).toBe("Unassigned");
    expect(ownerLabel("   ")).toBe("Unassigned");
    expect(ownerLabel("vj@fracktal.in")).toBe("vj@fracktal.in");
  });
});

describe("windowLabel", () => {
  it("reads the window off the payload rather than assuming one", () => {
    // Two places that must agree about a constant is how a report ends up
    // labelled 30 days while showing 90.
    expect(windowLabel(90)).toBe("in the last 90 days");
    expect(windowLabel(30)).toBe("in the last 30 days");
  });
});

describe("undatedNotice", () => {
  it("explains a zero win rate on a board full of closed deals", () => {
    // Imported closed deals carry no `closed_at` until the owner runs the
    // pipeline repair, so they are outside every window by construction. A 0%
    // win rate with no explanation reads as a broken report.
    const notice = undatedNotice({ ...WIN_LOSS, closed_without_date: 551 });
    expect(notice).toContain("551 closed deals are outside every window");
  });

  it("says nothing when there is nothing to explain", () => {
    expect(undatedNotice(WIN_LOSS)).toBeNull();
    expect(undatedNotice(null)).toBeNull();
  });

  it("agrees with itself about one deal", () => {
    const notice = undatedNotice({ ...WIN_LOSS, closed_without_date: 1 });
    expect(notice).toContain("1 closed deal is outside");
  });
});

// ── Emptiness ─────────────────────────────────────────────────────────────

describe("plusLabel", () => {
  it("names each other currency with its own code, and nothing when there is none", () => {
    expect(plusLabel(PIPELINE.others, (o) => o.amount)).toContain("2,500");
    expect(plusLabel(PIPELINE.others, (o) => o.amount)).toMatch(/^plus .*\$/);
    expect(plusLabel([], () => 0)).toBe("");
    expect(plusLabel(undefined, () => 0)).toBe("");
  });
});

describe("the has* guards", () => {
  it("distinguish a loaded-but-empty CRM from an unloaded one", () => {
    // An empty CRM renders "nothing yet", not a grid of zeros — the same
    // courtesy the board's empty lanes get, in the other direction.
    expect(hasPipeline(PIPELINE)).toBe(true);
    expect(
      hasPipeline({
        stages: [],
        count: 0,
        currency: "INR",
        amount: 0,
        weighted: 0,
        others: [],
      })
    ).toBe(false);
    expect(hasPipeline(null)).toBe(false);

    expect(hasFunnel(FUNNEL)).toBe(true);
    expect(
      hasFunnel({ stages: [], deals: 0, transitions: 0, unmatched: [] })
    ).toBe(false);

    expect(hasWinLoss(WIN_LOSS)).toBe(true);
    expect(
      hasWinLoss({ ...WIN_LOSS, won: 0, lost: 0, closed_without_date: 0 })
    ).toBe(false);
  });

  it("counts undated closed deals as something worth rendering", () => {
    // The block has to appear to carry the notice explaining the zero.
    expect(
      hasWinLoss({ ...WIN_LOSS, won: 0, lost: 0, closed_without_date: 551 })
    ).toBe(true);
  });

  it("reads the leaderboard's own rows", () => {
    expect(hasOwners(OWNERS)).toBe(true);
    expect(hasOwners({ ...OWNERS, owners: [] })).toBe(false);
    expect(hasOwners(null)).toBe(false);
  });
});
