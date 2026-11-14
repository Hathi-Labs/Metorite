import { describe, expect, it } from "vitest";

import { dayKey, fromDayKey } from "./days";
import { barForRow, rangeForRows, readDay, rowSpan, rowsToTimelineRows } from "./rows";

const FIELDS = { startField: "from", endField: "to", titleField: "name" };

describe("readDay", () => {
  it("reads a date-only value as that whole local day", () => {
    const day = readDay("2026-08-07");
    expect(day && dayKey(day)).toBe("2026-08-07");
    expect(day?.getHours()).toBe(0);
  });
  it("snaps an instant to its local day", () => {
    const noon = fromDayKey("2026-08-07");
    noon.setHours(12, 30);
    const day = readDay(noon.toISOString());
    expect(day && dayKey(day)).toBe("2026-08-07");
  });
  it("reads a Date and drops its time", () => {
    const d = fromDayKey("2026-08-07");
    d.setHours(23, 59);
    expect(dayKey(readDay(d) as Date)).toBe("2026-08-07");
  });
  it("refuses what it cannot read", () => {
    for (const bad of [null, undefined, "", "soon", "2026-02-31", 12, {}, new Date("x")]) {
      expect(readDay(bad)).toBeNull();
    }
  });
});

describe("rowsToTimelineRows", () => {
  it("maps start, end and title fields to rows and keeps the record", () => {
    const record = { id: "r1", from: "2026-08-03", to: "2026-08-07", name: "Launch" };
    const [row] = rowsToTimelineRows([record], FIELDS);
    expect(row.id).toBe("r1");
    expect(row.label).toBe("Launch");
    expect(dayKey(row.start as Date)).toBe("2026-08-03");
    expect(dayKey(row.end as Date)).toBe("2026-08-07");
    expect(row.tone).toBeNull();
    expect(row.data).toBe(record);
  });
  it("drops a record with no readable start", () => {
    const rows = rowsToTimelineRows(
      [
        { id: "a", from: null, to: "2026-08-07", name: "A" },
        { id: "b", from: "never", to: "2026-08-07", name: "B" },
        { id: "c", from: "2026-08-01", to: "2026-08-02", name: "C" },
      ],
      FIELDS,
    );
    expect(rows.map((r) => r.id)).toEqual(["c"]);
  });
  it("makes a date-only value a whole day, so a one-day row has one day", () => {
    const [row] = rowsToTimelineRows(
      [{ id: "a", from: "2026-08-03", to: "2026-08-03", name: "A" }],
      FIELDS,
    );
    expect(rowSpan(row)).toEqual({ from: "2026-08-03", to: "2026-08-03", derived: false });
  });
  it("makes an end before the start equal to the start", () => {
    const [row] = rowsToTimelineRows(
      [{ id: "a", from: "2026-08-10", to: "2026-08-03", name: "A" }],
      FIELDS,
    );
    expect(dayKey(row.end as Date)).toBe("2026-08-10");
  });
  it("makes a missing end equal to the start", () => {
    const [row] = rowsToTimelineRows([{ id: "a", from: "2026-08-10", name: "A" }], FIELDS);
    expect(dayKey(row.end as Date)).toBe("2026-08-10");
  });
  it("uses the position as the id when the record has none", () => {
    const rows = rowsToTimelineRows(
      [
        { from: "2026-08-01", name: "A" },
        { from: "2026-08-02", name: "B" },
      ],
      FIELDS,
    );
    expect(rows.map((r) => r.id)).toEqual(["0", "1"]);
  });
  it("lays records out as bars on a range", () => {
    const rows = rowsToTimelineRows(
      [{ id: "a", from: "2026-08-03", to: "2026-08-05", name: "A" }],
      FIELDS,
    );
    const range = rangeForRows(rows, "2026-08-04");
    expect(barForRow(rows[0], range)?.widthPx).toBe(3 * range.pxPerDay);
  });
});

describe("rowSpan, rangeForRows and barForRow", () => {
  it("has no span or bar for an unscheduled row", () => {
    const row = { start: null, end: null };
    expect(rowSpan(row)).toBeNull();
    expect(barForRow(row, rangeForRows([row], "2026-08-15"))).toBeNull();
  });
  it("reads one known date as a one-day span", () => {
    expect(rowSpan({ start: fromDayKey("2026-08-03"), end: null })).toEqual({
      from: "2026-08-03",
      to: "2026-08-03",
      derived: false,
    });
  });
  it("carries the derived flag to the bar", () => {
    const row = { start: fromDayKey("2026-08-03"), end: fromDayKey("2026-08-04"), derived: true };
    expect(barForRow(row, rangeForRows([row], "2026-08-03"))?.derived).toBe(true);
  });
});
