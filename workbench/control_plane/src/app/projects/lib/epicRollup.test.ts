/**
 * SP-9 — the words of the roll-up line of an epic.
 *
 * Mutations caught, by test:
 * - the line reads `subtasks` (direct children) and not the roll-up: the
 *   first test, which sets the roll-up and no `subtasks`;
 * - the waits part draws when the count is zero: "draws no wait words";
 * - `hidden` is drawn at zero: "draws no hidden words".
 */
import { describe, expect, it } from "vitest";

import { type EpicRollup, rollupLine } from "./epicRollup";

function rollup(over: Partial<EpicRollup>): EpicRollup {
  return {
    slices: { done: 4, total: 12 },
    estimate_mins: { done: 360, total: 1200 },
    waits: { count: 0, items: [] },
    hidden: 0,
    spend: null,
    forecast: null,
    health: null,
    hill: null,
    ...over,
  };
}

describe("the roll-up line of an epic", () => {
  it("words the slices and the estimate", () => {
    expect(rollupLine(rollup({}))).toBe("4 of 12 slices done · 6h of 20h");
  });

  it("draws no wait words and no hidden words when there are none", () => {
    const line = rollupLine(rollup({}));
    expect(line).not.toContain("wait");
    expect(line).not.toContain("hidden");
  });

  it("adds the waits and the hidden count", () => {
    const line = rollupLine(
      rollup({ waits: { count: 2, items: [] }, hidden: 3 }),
    );
    expect(line).toBe(
      "4 of 12 slices done · 6h of 20h · 2 wait on a person · 3 hidden from you",
    );
  });

  it("uses the singular for one wait and one slice", () => {
    expect(
      rollupLine(
        rollup({
          slices: { done: 0, total: 1 },
          estimate_mins: { done: 0, total: 0 },
          waits: { count: 1, items: [] },
        }),
      ),
    ).toBe("0 of 1 slice done · 1 waits on a person");
  });

  it("says so when the epic has no slices", () => {
    expect(
      rollupLine(
        rollup({ slices: { done: 0, total: 0 }, estimate_mins: { done: 0, total: 0 } }),
      ),
    ).toBe("No slices yet");
  });
});
