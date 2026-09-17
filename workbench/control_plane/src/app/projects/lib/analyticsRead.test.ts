/**
 * Three silent blank sections, pinned so there is not a fourth.
 *
 * Each case below is a shape that actually reached this client and rendered
 * nothing. None of them threw, and none of them failed a typecheck, because
 * `api.call` casts the response instead of validating it.
 */
import { describe, expect, it } from "vitest";

import { asList, bandCount, staleBands } from "./analyticsRead";

describe("staleBands", () => {
  it("⚠️ reads the LIST the server actually sends", () => {
    // The real payload, copied from a live response 2026-09-17. The panel
    // asked `"under_7d" in data.stale`, which on an array tests indices —
    // always false — so the histogram drew an empty bar and no legend.
    const got = staleBands([
      { band: "under_7d", n: 16 },
      { band: "days_7_to_14", n: 0 },
      { band: "days_14_to_30", n: 0 },
      { band: "over_30d", n: 2 },
    ]);
    expect(got).toEqual([
      { key: "under_7d", n: 16 },
      { key: "days_7_to_14", n: 0 },
      { key: "days_14_to_30", n: 0 },
      { key: "over_30d", n: 2 },
    ]);
  });

  it("reads the RECORD the type used to declare", () => {
    // Accepting both is deliberate: either could be true of the deployment
    // this client is talking to, and the panel must not go blank on the one
    // it did not expect.
    expect(staleBands({ under_7d: 3, over_30d: 1 })).toEqual([
      { key: "under_7d", n: 3 },
      { key: "over_30d", n: 1 },
    ]);
  });

  it("keeps the server's order, because the bands ascend", () => {
    const got = staleBands([
      { band: "over_30d", n: 1 },
      { band: "under_7d", n: 9 },
    ]);
    expect(got.map((b) => b.key)).toEqual(["over_30d", "under_7d"]);
  });

  it("survives absent, null and the wrong type entirely", () => {
    expect(staleBands(undefined)).toEqual([]);
    expect(staleBands(null)).toEqual([]);
    expect(staleBands(42)).toEqual([]);
    expect(staleBands("under_7d")).toEqual([]);
  });

  it("drops an entry it cannot name, rather than drawing a blank row", () => {
    expect(staleBands([{ n: 4 }, null, { band: "over_30d", n: 2 }])).toEqual([
      { key: "over_30d", n: 2 },
    ]);
  });

  it("reads a missing or non-numeric count as zero", () => {
    expect(staleBands([{ band: "under_7d" }])).toEqual([
      { key: "under_7d", n: 0 },
    ]);
    expect(staleBands([{ band: "under_7d", n: "16" }])).toEqual([
      { key: "under_7d", n: 0 },
    ]);
  });
});

describe("bandCount", () => {
  it("answers zero for a band the server did not send", () => {
    const bands = staleBands([{ band: "under_7d", n: 5 }]);
    expect(bandCount(bands, "under_7d")).toBe(5);
    expect(bandCount(bands, "over_30d")).toBe(0);
  });
});

describe("asList", () => {
  it("⚠️ turns the scalar that broke Overdue into an empty section", () => {
    // `stuck.overdue` was an integer while the type said a list. Nothing
    // threw: `number.length` is undefined and `undefined > 0` is false, so
    // the section rendered nothing and nobody could tell why.
    expect(asList(7)).toEqual([]);
    expect(asList(undefined)).toEqual([]);
    expect(asList(null)).toEqual([]);
  });

  it("passes a real list straight through", () => {
    expect(asList([1, 2])).toEqual([1, 2]);
  });
});
