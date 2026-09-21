/**
 * The spend breakdown's judgements (H-134).
 *
 * The property that matters most here is the ZERO one: the rate card ships
 * unpriced (H-42), so every `billed_credits` is 0 on a live deployment today.
 * A surface that ranked or drew by credits would show a busy month as an
 * empty table and read as broken, on exactly the customer with the most to
 * look at.
 */
import { describe, expect, it } from "vitest";

import {
  activityLabel,
  sortSpend,
  spendIsMeasured,
  spendShare,
  spendTotals,
  UNATTRIBUTED,
  type ActivityRow,
} from "./spend";

const row = (activity: string, calls: number, credits: string): ActivityRow => ({
  activity,
  calls,
  credits,
});

describe("spendIsMeasured", () => {
  it("is false while the rate card is unpriced — the shipped state", () => {
    expect(spendIsMeasured([row("tasks", 900, "0"), row("chat", 40, "0")])).toBe(
      false,
    );
  });

  it("is true as soon as one row carries money", () => {
    expect(spendIsMeasured([row("tasks", 9, "0"), row("chat", 1, "0.4")])).toBe(
      true,
    );
  });

  it("is false for no rows at all", () => {
    expect(spendIsMeasured([])).toBe(false);
  });

  it("treats an unparseable figure as zero, never as NaN", () => {
    expect(spendIsMeasured([row("tasks", 1, "not-a-number")])).toBe(false);
  });
});

describe("sortSpend", () => {
  it("ranks by credits once the card is priced", () => {
    const out = sortSpend([
      row("small", 900, "1.0"),
      row("big", 2, "90.0"),
    ]);
    expect(out.map((r) => r.activity)).toEqual(["big", "small"]);
  });

  it("ranks by CALLS while every figure is zero", () => {
    // 🔴 Sorting by credits here would order the table by a column of zeros
    // and present an arbitrary order as a ranking.
    const out = sortSpend([row("quiet", 2, "0"), row("busy", 900, "0")]);
    expect(out.map((r) => r.activity)).toEqual(["busy", "quiet"]);
  });

  it("does not mutate what it was given", () => {
    const rows = [row("a", 1, "0"), row("b", 2, "0")];
    sortSpend(rows);
    expect(rows.map((r) => r.activity)).toEqual(["a", "b"]);
  });
});

describe("spendShare", () => {
  it("is the credit fraction once priced", () => {
    const rows = [row("a", 1, "25"), row("b", 1, "75")];
    expect(spendShare(rows, rows[0])).toBeCloseTo(0.25);
    expect(spendShare(rows, rows[1])).toBeCloseTo(0.75);
  });

  it("falls back to the call fraction while unpriced", () => {
    const rows = [row("a", 1, "0"), row("b", 3, "0")];
    expect(spendShare(rows, rows[0])).toBeCloseTo(0.25);
  });

  it("is 0 and never NaN when there is nothing at all", () => {
    // A bar of width NaN renders as nothing, which reads as "no usage"
    // rather than as the defect it is.
    const rows = [row("a", 0, "0")];
    expect(spendShare(rows, rows[0])).toBe(0);
    expect(Number.isNaN(spendShare(rows, rows[0]))).toBe(false);
  });
});

describe("spendTotals", () => {
  it("adds both measures", () => {
    expect(spendTotals([row("a", 2, "1.5"), row("b", 3, "2.5")])).toEqual({
      calls: 5,
      credits: 4,
    });
  });
});

describe("activityLabel", () => {
  it("names the unattributed bucket in words", () => {
    expect(activityLabel(UNATTRIBUTED)).toBe("Not attributed");
  });

  it("passes an unknown slug through UNCHANGED", () => {
    // ⚠️ The Console groups by agent, else module — both open vocabularies
    // that grow without this file. Replacing an unknown slug with "Other"
    // would hide the app the customer is actually paying for.
    expect(activityLabel("meeting-notetaker")).toBe("meeting-notetaker");
  });
});
