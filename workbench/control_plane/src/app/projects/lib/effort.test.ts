/**
 * The rule under test: an hours figure never appears without its coverage,
 * and "nothing is sized" never renders as "0h".
 *
 * ⚠️ The server half is `tests/unit/test_projects_analytics_load.py`'s
 * `TestEffortLeftAndSpent`. That one proves the SQL sums and counts
 * correctly. This one proves the client refuses to draw a sum it cannot
 * stand behind — which is the half that turns a correct number into a
 * misleading screen.
 */
import { describe, expect, it } from "vitest";

import type { EffortReport, LoadRow } from "./api";
import { coverage, effortDisplay, hours, personEffort } from "./effort";

function effort(over: Partial<EffortReport> = {}): EffortReport {
  return {
    left_mins: 0,
    left_estimated: 0,
    left_tasks: 0,
    spent_mins: 0,
    spent_estimated: 0,
    spent_tasks: 0,
    basis: "estimate",
    ...over,
  };
}

function row(over: Partial<LoadRow> = {}): LoadRow {
  return {
    assignee: "ana@example.test",
    open_tasks: 0,
    overdue: 0,
    due_next_7d: 0,
    later: 0,
    ...over,
  };
}

describe("hours", () => {
  it("reads minutes under an hour as minutes", () => {
    expect(hours(45)).toBe("45m");
  });

  it("keeps the half hour while it still matters", () => {
    expect(hours(90)).toBe("1.5h");
    expect(hours(150)).toBe("2.5h");
  });

  it("drops to whole hours once a half is noise", () => {
    expect(hours(60 * 23)).toBe("23h");
  });

  it("⚠️ a day is EIGHT hours, not twenty-four", () => {
    // A person-day of work is a working day. Dividing by 24 would report a
    // fortnight of effort as five days, which is the kind of error that
    // survives because the number still looks plausible.
    expect(hours(60 * 80)).toBe("10d");
    expect(hours(60 * 160)).toBe("20d");
  });

  it("holds off on days until past two working weeks", () => {
    // A working week reads better as 40h than as 5d, and the switch only
    // earns its keep once the number of hours stops being graspable.
    expect(hours(60 * 40)).toBe("40h");
    expect(hours(60 * 79)).toBe("79h");
  });

  it("says nothing rather than zero when there is no figure", () => {
    expect(hours(null)).toBe("—");
    expect(hours(undefined)).toBe("—");
  });

  it("renders a real zero as zero", () => {
    expect(hours(0)).toBe("0h");
  });
});

describe("coverage", () => {
  it("reports the share that is sized, and what is missing", () => {
    const c = coverage(3, 30);
    expect(c.pct).toBe(10);
    expect(c.missing).toBe(27);
    expect(c.complete).toBe(false);
  });

  it("is complete when every task is sized", () => {
    expect(coverage(8, 8).complete).toBe(true);
  });

  it("⚠️ an EMPTY scope is complete, not zero-percent", () => {
    // A project with no open tasks has nothing unsized. Calling that 0%
    // would hang a warning on a finished project.
    const c = coverage(0, 0);
    expect(c.complete).toBe(true);
    expect(c.missing).toBe(0);
  });
});

describe("effortDisplay", () => {
  it("draws nothing at all when the server did not say", () => {
    // Absent is not zero, and a client must not invent a figure for it.
    expect(effortDisplay(undefined)).toBeNull();
    expect(effortDisplay(null)).toBeNull();
  });

  it("⚠️ says NOTHING IS SIZED rather than 0h", () => {
    // Measured 2026-09-17: the live tree carried zero estimates across 37
    // tasks. "0h left" on that project reads as "no work left".
    const d = effortDisplay(effort({ left_tasks: 12 }));
    expect(d).toEqual({ kind: "none", unsized: 12 });
  });

  it("reports both halves with their own coverage", () => {
    const d = effortDisplay(
      effort({
        left_mins: 1200,
        left_estimated: 8,
        left_tasks: 10,
        spent_mins: 600,
        spent_estimated: 5,
        spent_tasks: 5,
      })
    );
    expect(d).toMatchObject({ kind: "some", leftLabel: "20h", spentLabel: "10h" });
    if (d?.kind !== "some") throw new Error("expected some");
    expect(d.left.pct).toBe(80);
    expect(d.left.missing).toBe(2);
    expect(d.spent.complete).toBe(true);
  });

  it("gives a done share when both halves are well sized", () => {
    const d = effortDisplay(
      effort({
        left_mins: 300,
        left_estimated: 4,
        left_tasks: 4,
        spent_mins: 100,
        spent_estimated: 2,
        spent_tasks: 2,
      })
    );
    if (d?.kind !== "some") throw new Error("expected some");
    expect(d.donePct).toBe(25);
  });

  it("⚠️ refuses the done share when one half is barely sized", () => {
    // A well-estimated backlog against a barely-estimated history reports
    // progress that is an artefact of who filled in forms, not of work.
    const d = effortDisplay(
      effort({
        left_mins: 1200,
        left_estimated: 10,
        left_tasks: 10,
        spent_mins: 60,
        spent_estimated: 1,
        spent_tasks: 40,
      })
    );
    if (d?.kind !== "some") throw new Error("expected some");
    expect(d.donePct).toBeNull();
  });

  it("still compares when one half is legitimately empty", () => {
    // Nothing finished yet is not poor coverage — there is nothing to cover.
    const d = effortDisplay(
      effort({ left_mins: 480, left_estimated: 6, left_tasks: 6 })
    );
    if (d?.kind !== "some") throw new Error("expected some");
    expect(d.donePct).toBe(0);
  });
});

describe("personEffort", () => {
  it("⚠️ returns nothing for an unsized plate, never 0h", () => {
    // Nine unsized tasks is not zero hours of work, and a column of 0h
    // beside real figures says exactly that.
    expect(personEffort(row({ open_tasks: 9 }))).toBeNull();
  });

  it("carries the plate's own coverage beside its hours", () => {
    const got = personEffort(
      row({ open_tasks: 4, estimated: 2, est_mins: 240 })
    );
    expect(got?.label).toBe("4h");
    expect(got?.cover.pct).toBe(50);
    expect(got?.cover.missing).toBe(2);
  });
});
