import { beforeEach, describe, expect, it, vi } from "vitest";
import type { CrmView } from "./urlState";

// ── The store's one rule, under test ──────────────────────────────────────
//
// "A write re-reads what is on screen, whatever the response was." Every
// loader and every write path in store.ts is written to that rule, and until
// this file existed the rule was asserted only by the comments claiming it.
//
// That gap had already produced a live bug: `refreshCollection()` grew a
// `reports` branch whose comment named `moveDeal` as its caller, while
// `moveDeal` hard-coded `api.getPipeline` and never called
// `refreshCollection()` at all. The branch was unreachable from the one write
// it was written for, and nothing failed. So the cases below pin WHICH request
// a write issues, not merely that the store settled into a plausible shape —
// the store's job is deciding what to re-read, and asserting on final state
// alone cannot see a decision that was never made.
//
// `api` is mocked because these are the store's routing decisions, not the
// BFF client's (which `api.test.ts` covers).

vi.mock("./api", () => ({
  CrmError: class CrmError extends Error {
    status: number;
    constructor(message: string, status: number) {
      super(message);
      this.status = status;
    }
  },
  moveDeal: vi.fn(async () => ({ id: "d1" })),
  getPipeline: vi.fn(async () => ({ lanes: [] })),
  getPipelineReport: vi.fn(async () => ({
    stages: [],
    count: 0,
    amount: 0,
    weighted: 0,
  })),
  getFunnelReport: vi.fn(async () => ({
    stages: [],
    deals: 0,
    transitions: 0,
    unmatched: [],
  })),
  getWinLossReport: vi.fn(async () => ({
    window_days: 90,
    since: "2026-05-09T00:00:00+00:00",
    won: 0,
    lost: 0,
    win_rate: 0,
    won_amount: 0,
    lost_amount: 0,
    average_cycle_days: null,
    closed_without_date: 0,
    lost_reasons: [],
  })),
  getOwnerLeaderboard: vi.fn(async () => ({
    owners: [],
    window_days: 90,
    omitted: 0,
  })),
  listRecords: vi.fn(async () => ({ rows: [], total: 0 })),
  patchRecord: vi.fn(async () => ({ id: "d1" })),
  getRecord: vi.fn(async () => ({ id: "d1" })),
  getTimeline: vi.fn(async () => ({ entries: [] })),
  listDealContacts: vi.fn(async () => ({ rows: [] })),
}));

import * as api from "./api";
import { useCrmStore } from "./store";

const BASE: CrmView = {
  tab: "deals",
  layout: "board",
  pipelineId: null,
  record: null,
  q: "",
  statusId: null,
  includeConverted: false,
  owner: null,
  sort: null,
  dir: "desc",
  source: null,
  ranges: {},
  custom: {},
  viewId: null,
};

const REPORTS_VIEW: CrmView = {
  ...BASE,
  tab: "reports",
  record: { param: "deal", entity: "deals", id: "d1" },
};

const MOVE = { dealId: "d1", fromStatusId: "s1", toStatusId: "s2" };

/** The four report reads, as one assertion target. */
function reportCallCounts(): number[] {
  return [
    vi.mocked(api.getPipelineReport).mock.calls.length,
    vi.mocked(api.getFunnelReport).mock.calls.length,
    vi.mocked(api.getWinLossReport).mock.calls.length,
    vi.mocked(api.getOwnerLeaderboard).mock.calls.length,
  ];
}

beforeEach(() => {
  vi.clearAllMocks();
  useCrmStore.setState({
    lanes: [],
    rows: [],
    total: 0,
    record: null,
    reports: null,
    error: null,
    loading: false,
    saving: false,
    lastView: null,
  });
});

describe("moveDeal re-reads what is on screen", () => {
  it("refreshes all four report blocks when the reports tab is showing", () => {
    // The regression this file exists for. `?tab=reports&deal=<id>` is a
    // supported link (urlState keeps the sheet open across tabs), so setting a
    // deal to Closed Won from the sheet's status pill is a real, one-click
    // path — and every figure on the screen behind it changes.
    useCrmStore.setState({ lastView: REPORTS_VIEW });
    return useCrmStore
      .getState()
      .moveDeal(MOVE)
      .then(() => {
        expect(reportCallCounts()).toEqual([1, 1, 1, 1]);
        expect(useCrmStore.getState().reports).not.toBeNull();
        // …and it does NOT fetch the board, whose state that tab never renders.
        expect(api.getPipeline).not.toHaveBeenCalled();
      });
  });

  it("still re-reads the board, under its owner filter, on the board tab", () => {
    // The behaviour the old hard-coded fetch defended, kept: re-reading
    // unfiltered would silently widen a scoped board into the whole pipeline
    // the moment somebody dragged a card on it.
    useCrmStore.setState({ lastView: { ...BASE, owner: "vj@fracktal.in" } });
    return useCrmStore
      .getState()
      .moveDeal(MOVE)
      .then(() => {
        expect(api.getPipeline).toHaveBeenCalledWith("vj@fracktal.in", null);
        expect(reportCallCounts()).toEqual([0, 0, 0, 0]);
      });
  });

  it("re-reads the list when the move came from a list tab's sheet", () => {
    useCrmStore.setState({ lastView: { ...BASE, tab: "deals", layout: "list" } });
    return useCrmStore
      .getState()
      .moveDeal(MOVE)
      .then(() => {
        expect(api.listRecords).toHaveBeenCalled();
        expect(api.getPipeline).not.toHaveBeenCalled();
      });
  });

  it("puts the card back and says why when the PATCH itself is refused", async () => {
    // Either alone is a lie: a reverted card with no message reads as a bug,
    // and a message with the card left moved reads as success.
    const lanes = useCrmStore.getState().lanes;
    vi.mocked(api.moveDeal).mockRejectedValueOnce(new Error("lost needs a reason"));
    useCrmStore.setState({ lastView: REPORTS_VIEW });

    await useCrmStore.getState().moveDeal(MOVE);

    expect(useCrmStore.getState().error).toBe("lost needs a reason");
    expect(useCrmStore.getState().lanes).toBe(lanes);
    expect(reportCallCounts()).toEqual([0, 0, 0, 0]);
  });

  it("does not revert an accepted move when only the RE-READ fails", async () => {
    // The move landed on the server. Reverting the card because a subsequent
    // GET failed would show the deal back in its old lane while it sits in the
    // new one — the refusal path's revert is for refusals only.
    vi.mocked(api.getFunnelReport).mockRejectedValueOnce(new Error("gateway 502"));
    useCrmStore.setState({ lastView: REPORTS_VIEW });

    await useCrmStore.getState().moveDeal(MOVE);

    expect(api.moveDeal).toHaveBeenCalledTimes(1);
    expect(useCrmStore.getState().error).toBe("gateway 502");
    expect(useCrmStore.getState().reports).toBeNull();
  });
});

describe("refreshCollection", () => {
  it("does nothing before anything has been loaded", async () => {
    await useCrmStore.getState().refreshCollection();
    expect(api.getPipeline).not.toHaveBeenCalled();
    expect(reportCallCounts()).toEqual([0, 0, 0, 0]);
  });

  it("is what patchRecord calls, so the reports follow an edit too", async () => {
    useCrmStore.setState({ lastView: REPORTS_VIEW });
    await useCrmStore.getState().patchRecord("deals", "d1", { amount: 5 });
    expect(reportCallCounts()).toEqual([1, 1, 1, 1]);
  });
});

describe("loadReports", () => {
  it("records lastView, so a later write knows the reports are on screen", async () => {
    await useCrmStore.getState().loadReports(REPORTS_VIEW);
    expect(useCrmStore.getState().lastView).toBe(REPORTS_VIEW);
  });

  it("drops the stale payload when a refresh fails", async () => {
    await useCrmStore.getState().loadReports(REPORTS_VIEW);
    expect(useCrmStore.getState().reports).not.toBeNull();

    vi.mocked(api.getWinLossReport).mockRejectedValueOnce(new Error("boom"));
    await useCrmStore.getState().loadReports(REPORTS_VIEW);

    // Nothing about a chart says how old it is, so last week's numbers under a
    // failed refresh are the same lie as a stale row after a 409.
    expect(useCrmStore.getState().reports).toBeNull();
    expect(useCrmStore.getState().error).toBe("boom");
  });
});
