/**
 * STU-D5-W2 fence: the date window of the Studio calendar. All dates are local, so the tests
 * hold in any zone. The week comes from `weekModel.ts`, and the month is the six-week grid.
 */
import { describe, expect, it } from "vitest";

import { startOfMonthGrid } from "@/app/calendar/components/shared";
import { buildWeek, windowOfDays } from "@/components/calendar/weekModel";
import type { GridEvent } from "@/components/calendar/types";
import {
  CAP_SENTENCE, DEFAULT_MODE, MAX_FILTERS, capSentence, eventsOnDay, joinFilters, periodRange, periodSource,
  periodTitle, stepPeriod, switchMode, visibleDays, windowConditions,
} from "./calendarWindow";

const L = (d: number, h = 0, mo = 10) => new Date(2026, mo, d, h);
const ANCHOR = L(12, 15); // Thursday 2026-11-12

describe("the period range", () => {
  it("is the window of the model for a week and for a day", () => {
    for (const mode of ["week", "day"] as const) {
      const days = buildWeek([], { anchor: ANCHOR, mode }).days;
      expect(periodRange(mode, ANCHOR, "datetime")).toEqual(windowOfDays(days));
    }
    expect(periodRange("week", ANCHOR, "datetime")).toEqual({
      from: L(9).toISOString(), to: L(16).toISOString(),
    });
    expect(periodRange("day", ANCHOR, "datetime")).toEqual({ from: L(12).toISOString(), to: L(13).toISOString() });
  });

  it("covers the six weeks of the month grid", () => {
    const days = visibleDays("month", ANCHOR);
    expect(days).toHaveLength(42);
    expect(days[0].date).toEqual(startOfMonthGrid(ANCHOR));
    expect(days[0].key).toBe("2026-10-26");
    expect(days[41].key).toBe("2026-12-06");
    expect(periodRange("month", ANCHOR, "datetime")).toEqual({
      from: new Date(2026, 9, 26).toISOString(), to: new Date(2026, 11, 7).toISOString(),
    });
  });

  it("gives day strings for a date field, so the store takes them", () => {
    expect(periodRange("week", ANCHOR, "date")).toEqual({ from: "2026-11-09", to: "2026-11-16" });
    expect(periodRange("month", ANCHOR, "date")).toEqual({ from: "2026-10-26", to: "2026-12-07" });
  });
});

describe("the window filter", () => {
  it("is one gte and one lt on the start field", () => {
    expect(windowConditions("visit_at", "datetime", "week", ANCHOR)).toEqual([
      { field: "visit_at", op: "gte", value: L(9).toISOString() },
      { field: "visit_at", op: "lt", value: L(16).toISOString() },
    ]);
  });

  it("puts the own conditions of the view first", () => {
    const own = [{ field: "status", op: "eq", value: "open" }];
    const out = joinFilters(own, windowConditions("d", "date", "day", ANCHOR));
    expect(out).toHaveLength(3);
    expect(out[0]).toBe(own[0]);
    expect(out.slice(1).map((c) => (c as { op: string }).op)).toEqual(["gte", "lt"]);
  });

  it("keeps the whole window when the own conditions would pass the cap", () => {
    const own = Array.from({ length: 12 }, (_, i) => ({ field: `f${i}`, op: "eq", value: i }));
    const out = joinFilters(own, windowConditions("d", "date", "day", ANCHOR));
    expect(out).toHaveLength(MAX_FILTERS);
    expect(out.slice(-2).map((c) => (c as { op: string }).op)).toEqual(["gte", "lt"]);
    expect((out[MAX_FILTERS - 3] as { field: string }).field).toBe("f5");
  });

  it("makes the source of a period and keeps the limit and the action", () => {
    const base = { action: "object:visit.list", args: { limit: 100, filter: [{ field: "s", op: "eq", value: 1 }] } };
    const src = periodSource(base, "d", "date", "week", ANCHOR);
    expect(src.action).toBe("object:visit.list");
    expect(src.args.limit).toBe(100);
    expect(src.args.filter).toHaveLength(3);
    expect(base.args.filter).toHaveLength(1);
  });

  it("changes the source when the range changes, so the read runs again", () => {
    const base = { action: "a", args: { limit: 100 } };
    const a = JSON.stringify(periodSource(base, "d", "date", "week", ANCHOR));
    expect(JSON.stringify(periodSource(base, "d", "date", "week", stepPeriod("week", ANCHOR, 1)))).not.toBe(a);
    expect(JSON.stringify(periodSource(base, "d", "date", "month", ANCHOR))).not.toBe(a);
  });
});

describe("the cap sentence", () => {
  it("speaks when the read reached the limit or the store says there is more", () => {
    expect(capSentence(100, 100, false)).toBe(CAP_SENTENCE);
    expect(capSentence(40, 100, true)).toBe(CAP_SENTENCE);
    expect(capSentence(40, 100, false)).toBeNull();
    expect(CAP_SENTENCE).toBe("Showing the first 100 records of this period. Narrow the view to see the rest.");
  });
});

describe("the steps and the mode", () => {
  it("opens in a week", () => {
    expect(DEFAULT_MODE).toBe("week");
  });

  it("steps a week by seven days, a day by one, and a month to the first of the next month", () => {
    expect(stepPeriod("week", ANCHOR, 1).getDate()).toBe(19);
    expect(stepPeriod("week", ANCHOR, -1).getDate()).toBe(5);
    expect(stepPeriod("day", ANCHOR, 1).getDate()).toBe(13);
    const m = stepPeriod("month", ANCHOR, 1);
    expect([m.getFullYear(), m.getMonth(), m.getDate()]).toEqual([2026, 11, 1]);
    expect(stepPeriod("month", ANCHOR, -1).getMonth()).toBe(9);
  });

  it("keeps the anchor when the mode changes", () => {
    expect(switchMode("month", "week", ANCHOR)).toBe(ANCHOR);
    expect(switchMode("week", "day", ANCHOR)).toBe(ANCHOR);
  });

  it("names the period", () => {
    expect(periodTitle("month", ANCHOR)).toBe("November 2026");
    expect(periodTitle("week", ANCHOR)).toBe("Nov 9 – 15, 2026");
    expect(periodTitle("week", L(1))).toContain("Oct 26");
    expect(periodTitle("day", ANCHOR)).toBe("Thursday, November 12, 2026");
  });
});

describe("the events of a day", () => {
  const ev = (id: string, s: Date, e: Date): GridEvent => ({ id, title: id, start: s, end: e, allDay: false, tone: "primary" });
  it("lists the events that start on the day or run into it, in start order", () => {
    const list = [ev("late", L(12, 15), L(12, 16)), ev("early", L(12, 8), L(12, 9)), ev("other", L(13, 8), L(13, 9)),
      ev("over", L(11, 22), L(12, 2))];
    expect(eventsOnDay(list, L(12)).map((e) => e.id)).toEqual(["over", "early", "late"]);
  });
});
