/**
 * The filter row's arrow rule — H-94 direction 2, pinned.
 *
 * ⚠️ Owner direction, 2026-08-26: *"Every dropdown in the row becomes a
 * button. At the default value, draw a two-headed arrow in place of the single
 * down arrow."*
 *
 * A row of four controls all showing a down chevron says nothing about which
 * of them are doing anything. The arrow is the only cue besides the active
 * tint, and it is the one that survives when colour does not — so it is worth
 * a test rather than a screenshot.
 *
 * ⚠️ **Why a pure function and not a rendered assertion.**
 * `vitest.config.ts` is `environment: "node"`, so there is no DOM to render
 * into. The alternative was a source-text fence, and this repo has now watched
 * two of those pass on a value they existed to reject — `"7to14d".isalnum()`
 * and a regex that could not see an f-string hole. A function that takes the
 * two inputs and returns the answer cannot lie about itself.
 */
import { describe, expect, it } from "vitest";

import { arrowFor } from "./SelectButton";

describe("arrowFor", () => {
  it("draws TWO heads at the default", () => {
    expect(arrowFor("", "")).toBe("ChevronsUpDown");
    expect(arrowFor("status", "status")).toBe("ChevronsUpDown");
  });

  it("draws ONE head once the control is doing something", () => {
    expect(arrowFor("assignee", "status")).toBe("ChevronDown");
    expect(arrowFor("todo", "")).toBe("ChevronDown");
  });

  it("⚠️ treats an empty default as a real default, not as absent", () => {
    // Status and Assignee both default to `""` — "Any status", "Anyone". A
    // falsy check instead of an equality check would give those two a single
    // chevron forever, which is exactly the state the row is meant to escape.
    expect(arrowFor("", "")).toBe("ChevronsUpDown");
  });

  it("is not fooled by a value that merely looks empty", () => {
    expect(arrowFor(" ", "")).toBe("ChevronDown");
    expect(arrowFor("0", "")).toBe("ChevronDown");
  });

  it("returns names the icon pack actually has", () => {
    // Both are Lucide names. A typo here renders nothing at all, and an
    // absent glyph in a 28px button is very easy to miss.
    for (const name of [arrowFor("a", "a"), arrowFor("a", "b")]) {
      expect(name).toMatch(/^Chevron/);
    }
  });
});
