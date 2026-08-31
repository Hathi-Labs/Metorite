/**
 * The fence for S4 item 3: an empty canvas must say WHICH kind of empty it is.
 *
 * The defect these assertions describe shipped as one sentence carrying both
 * causes — "Clear a filter, or this project has no statuses yet" — so the tests
 * that matter are the ones about what each state does NOT say.
 */

import { describe, expect, it } from "vitest";

import { isKnownIcon } from "@/lib/icons";

import { EMPTY_FILTERS, isFiltered } from "./grouping";
import { emptyStateCopy } from "./emptyState";

const noMatch = (canvas: "board" | "list") =>
  emptyStateCopy({ canvas, filtered: true, onStatusAxis: canvas === "board" });

describe("the filtered state", () => {
  it("blames the filters and offers the way out", () => {
    for (const canvas of ["board", "list"] as const) {
      const copy = noMatch(canvas);
      expect(copy.filtered).toBe(true);
      expect(copy.message).toMatch(/filter/i);
    }
  });

  it("never mentions the OTHER cause", () => {
    // The whole defect: a reader who filtered has no reason to be told the
    // project might have no statuses, and cannot tell which half applies.
    for (const canvas of ["board", "list"] as const) {
      const copy = noMatch(canvas);
      expect(`${copy.message} ${copy.hint ?? ""}`).not.toMatch(/status/i);
    }
  });

  it("reads the same on both canvases — one fact, one sentence", () => {
    expect(noMatch("board")).toEqual(noMatch("list"));
  });
});

describe("the genuinely-empty state", () => {
  it("never mentions filters", () => {
    const cases = [
      emptyStateCopy({ canvas: "list", filtered: false }),
      emptyStateCopy({ canvas: "board", filtered: false, onStatusAxis: false }),
      emptyStateCopy({ canvas: "board", filtered: false, onStatusAxis: true }),
    ];
    for (const copy of cases) {
      expect(copy.filtered).toBe(false);
      expect(`${copy.message} ${copy.hint ?? ""}`).not.toMatch(/filter/i);
    }
  });

  it("says what an empty status axis actually means", () => {
    const copy = emptyStateCopy({
      canvas: "board",
      filtered: false,
      onStatusAxis: true,
    });
    expect(copy.message).toMatch(/status/i);
    expect(copy.hint).toMatch(/column/i);
  });

  it("claims nothing about statuses off the status axis", () => {
    // Grouped by assignee or priority, "no columns" means "no tasks" — the
    // project's statuses are not what is missing.
    const copy = emptyStateCopy({
      canvas: "board",
      filtered: false,
      onStatusAxis: false,
    });
    expect(`${copy.message} ${copy.hint ?? ""}`).not.toMatch(/status/i);
    expect(copy).toEqual(emptyStateCopy({ canvas: "list", filtered: false }));
  });
});

/**
 * WS-27am — the third arm. The fence is *present-but-disabled*, and the mutant
 * it is measured against is dropping the action (i.e. hiding the CTA), which is
 * the shipped behaviour everywhere else and reads as correct in review.
 */
describe("the no-permission state", () => {
  const denied = (canvas: "board" | "list" = "list") =>
    emptyStateCopy({ canvas, filtered: false, canCreate: false });

  it("offers the action DISABLED — never absent, never enabled", () => {
    for (const canvas of ["board", "list"] as const) {
      const copy = denied(canvas);
      expect(
        copy.action,
        "Hiding the CTA teaches the reader the action does not exist here.",
      ).toBeDefined();
      expect(copy.action?.label).toBeTruthy();
      expect(copy.action?.disabled).toBe(true);
    }
  });

  it("says why, in a sentence, not only by greying a control", () => {
    const copy = denied();
    expect(copy.action?.disabledReason).toBeTruthy();
    // The three lines must be three different facts: what is here, why you
    // cannot change it, what to do about it.
    expect(copy.action?.disabledReason).not.toBe(copy.hint);
    expect(copy.hint).not.toBe(copy.message);
  });

  it("carries no handler — a disabled action has nothing to run", () => {
    expect(denied().action?.onClick).toBeUndefined();
  });

  it("never blames the filters", () => {
    const copy = denied();
    expect(copy.filtered).toBe(false);
    expect(`${copy.message} ${copy.hint ?? ""}`).not.toMatch(/filter/i);
  });

  it("is outranked by filters and outranks the status axis", () => {
    // Filters win: `Clear filters` is a way out a read-only reader really has.
    const filtered = emptyStateCopy({
      canvas: "list",
      filtered: true,
      canCreate: false,
    });
    expect(filtered.message).toMatch(/filter/i);
    expect(filtered.action).toBeUndefined();

    // The status axis loses: "add a status" is an instruction this reader
    // cannot follow, and unfollowable advice reads as a broken app.
    const board = emptyStateCopy({
      canvas: "board",
      filtered: false,
      onStatusAxis: true,
      canCreate: false,
    });
    expect(`${board.message} ${board.hint ?? ""}`).not.toMatch(/add (a )?status/i);
    expect(board.action?.disabled).toBe(true);
  });

  it("changes nothing for a caller that does not pass it", () => {
    // The additive-only rule, as an assertion: the two shipped arms are
    // byte-identical with `canCreate` omitted and with it true.
    for (const filtered of [true, false]) {
      for (const canvas of ["board", "list"] as const) {
        expect(emptyStateCopy({ canvas, filtered, onStatusAxis: true })).toEqual(
          emptyStateCopy({ canvas, filtered, onStatusAxis: true, canCreate: true }),
        );
      }
    }
  });

  it("names icons that actually exist", () => {
    for (const name of [denied().icon, denied().action?.icon]) {
      expect(name, "an unnamed icon renders nothing").toBeTruthy();
      expect(isKnownIcon(name!), `${name} is not a real Lucide icon`).toBe(true);
    }
  });
});

describe("the two states are distinguishable", () => {
  it("differ in message and icon, not only in tone", () => {
    const filtered = noMatch("list");
    const empty = emptyStateCopy({ canvas: "list", filtered: false });
    expect(filtered.message).not.toBe(empty.message);
    expect(filtered.icon).not.toBe(empty.icon);
  });

  it("every icon actually exists", () => {
    // An unknown name silently renders the `Zap` fallback — a lightning bolt
    // where an empty state should be, which reads as a bug rather than as a
    // missing glyph. (This asserted pack mappings until 2026-08-31; with one
    // pack, "is it real" is both the stronger question and the only one left,
    // because a hallucinated name was always mapped nowhere AND unreal.)
    const icons = [
      emptyStateCopy({ canvas: "list", filtered: true }).icon,
      emptyStateCopy({ canvas: "list", filtered: false }).icon,
      emptyStateCopy({ canvas: "board", filtered: false, onStatusAxis: true }).icon,
    ];
    for (const icon of icons) {
      expect(isKnownIcon(icon), `${icon} is not a real Lucide icon`).toBe(true);
    }
  });

  it("the caller's predicate is the shared one", () => {
    // Not a re-derivation: `isFiltered` is what the toolbar's Clear button and
    // the `filtered` badge already read, and the empty state must agree with
    // the control that caused it.
    expect(isFiltered(EMPTY_FILTERS)).toBe(false);
    expect(isFiltered({ ...EMPTY_FILTERS, overdue: true })).toBe(true);
  });
});
