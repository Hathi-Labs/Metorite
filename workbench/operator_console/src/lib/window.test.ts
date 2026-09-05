import { describe, expect, it } from "vitest";

import { windowProblem, wrapsMidnight } from "./window";

// Spec: `project-docs/specs/credit_pricing.md` §4.1 (slice 2). Migration 024.
//
// 🔴 The shape this file refuses is the one the DATABASE refuses:
// `model_profile_offpeak_range_complete`. Refusing it here as well only moves
// where the operator finds out — beside the box, instead of in a 422 body.

describe("the off-peak window's shape", () => {
  it("accepts both boxes empty, which is how almost every vendor works", () => {
    expect(windowProblem("", "")).toBeNull();
  });

  it("accepts a well-formed pair", () => {
    expect(windowProblem("16:30", "00:30")).toBeNull();
  });

  it("REFUSES one bound alone, in either direction", () => {
    // ⚠️ One bound cannot say whether the operator meant all day or nothing.
    expect(windowProblem("16:30", "")?.kind).toBe("half");
    expect(windowProblem("", "00:30")?.kind).toBe("half");
  });

  it("refuses a time Postgres would read as something else", () => {
    for (const bad of ["25:00", "16:70", "half four", "1630"]) {
      expect(windowProblem(bad, "00:30")?.kind).toBe("badTime");
    }
  });

  it("refuses a ragged hour rather than padding it", () => {
    // Postgres would take `9:00`. A column of times where some are padded and
    // some are not is a column somebody misreads.
    expect(windowProblem("9:00", "00:30")?.kind).toBe("badTime");
  });

  it("names the field in the message, so the operator knows which box", () => {
    const p = windowProblem("16:30", "nope");
    expect(p?.kind).toBe("badTime");
    expect(p?.message).toContain("off-peak end");
  });

  it("ignores surrounding whitespace rather than refusing it", () => {
    expect(windowProblem("  16:30  ", " 00:30 ")).toBeNull();
  });
});

describe("wrapping midnight", () => {
  it("recognises DeepSeek's window, which crosses the night", () => {
    expect(wrapsMidnight("16:30", "00:30")).toBe(true);
  });

  it("does not claim a plain daytime window wraps", () => {
    expect(wrapsMidnight("02:00", "06:00")).toBe(false);
  });

  it("answers false rather than throwing on a malformed pair", () => {
    // The form calls this while the operator is still typing.
    expect(wrapsMidnight("16:3", "")).toBe(false);
  });
});
