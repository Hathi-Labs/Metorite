/**
 * STU-LY-1 fence: which shared state a block draws, and the words of each.
 */
import { describe, expect, it } from "vitest";

import { blockStateCopy, blockStateKind, pluralOf, type BlockStateInput } from "@/lib/studio/blockState";

const base: BlockStateInput = { loading: false, error: null, forbidden: false, rows: 3, filtersActive: false };

describe("blockStateKind", () => {
  it("draws nothing when the block has rows", () => {
    expect(blockStateKind(base)).toBeNull();
  });
  it("orders no-permission over error over loading over empty", () => {
    const all = { ...base, rows: 0, loading: true, error: "x", forbidden: true };
    expect(blockStateKind(all)).toBe("no-permission");
    expect(blockStateKind({ ...all, forbidden: false })).toBe("error");
    expect(blockStateKind({ ...all, forbidden: false, error: null })).toBe("loading");
    expect(blockStateKind({ ...all, forbidden: false, error: null, loading: false })).toBe("empty");
  });
  it("draws not-found for a missing record, under no-permission and over error", () => {
    expect(blockStateKind({ ...base, rows: 0, missing: true })).toBe("not-found");
    expect(blockStateKind({ ...base, rows: 0, missing: true, error: "x" })).toBe("not-found");
    expect(blockStateKind({ ...base, rows: 0, missing: true, forbidden: true })).toBe("no-permission");
  });
  it("tells a filtered empty from a true empty", () => {
    expect(blockStateKind({ ...base, rows: 0, filtersActive: true })).toBe("filtered");
    expect(blockStateKind({ ...base, rows: 0 })).toBe("empty");
  });
  it("never draws a blank page: zero rows always gives a state", () => {
    for (const filtersActive of [true, false]) {
      expect(blockStateKind({ ...base, rows: 0, filtersActive })).not.toBeNull();
    }
  });
});

describe("blockStateCopy", () => {
  const w = { noun: "visits", create: "visit" };
  it("gives the empty state one next action, only when the viewer can create", () => {
    expect(blockStateCopy("empty", w).action).toBe("New visit");
    expect(blockStateCopy("empty", { noun: "visits" }).action).toBeUndefined();
  });
  it("offers Clear filters on a filtered empty", () => {
    expect(blockStateCopy("filtered", w).action).toBe("Clear filters");
  });
  it("says what failed, says the data is safe, and has Retry", () => {
    const c = blockStateCopy("error", w);
    expect(c.message).toBe("The visits did not load.");
    expect(c.hint).toContain("data is safe");
    expect(c.action).toBe("Retry");
  });
  it("says one sentence for a record that is gone and one the viewer may not read", () => {
    const c = blockStateCopy("not-found", { noun: "visit" });
    expect(c.message).toBe("This visit was not found, or you cannot open it.");
    expect(c.action).toBeUndefined();
  });
  it("offers no Retry and no try again for an error that a second try cannot change", () => {
    const c = blockStateCopy("error", { ...w, retryable: false });
    expect(c.action).toBeUndefined();
    expect(c.message).toBe("The visits cannot be shown.");
    expect(c.hint).not.toMatch(/try again/i);
    expect(c.hint).toContain("Your data is safe");
    expect(blockStateCopy("error", { ...w, retryable: true }).action).toBe("Retry");
    expect(blockStateCopy("error", w).action).toBe("Retry");
  });
  it("says who to ask for access", () => {
    expect(blockStateCopy("no-permission", { ...w, askWho: "an admin" }).hint).toBe("Ask an admin for access.");
    expect(blockStateCopy("no-permission", w).hint).toContain("owner");
    expect(blockStateCopy("no-permission", w).action).toBeUndefined();
  });
});

describe("pluralOf", () => {
  it("makes the lower-case plural of an object label", () => {
    expect(pluralOf("Visit")).toBe("visits");
    expect(pluralOf("Company")).toBe("companies");
    expect(pluralOf("Day")).toBe("days");
    expect(pluralOf("Box")).toBe("boxes");
    expect(pluralOf("  ")).toBe("records");
  });
});
