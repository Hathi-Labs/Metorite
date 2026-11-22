import { describe, expect, it } from "vitest";

import {
  MIN_BAR_PX,
  barForRange,
  barForSpan,
  dayAtPx,
  dayCells,
  dayPx,
  dayStep,
  isWeekend,
  labelSide,
  monthCells,
  weekCells,
} from "./geometry";
import { rangeForSpans } from "./scale";

const RANGE = rangeForSpans([{ from: "2026-08-03", to: "2026-08-30" }], "2026-08-15");

describe("bar geometry", () => {
  it("puts a bar at the start of its first day and over its last day", () => {
    const bar = barForSpan("2026-08-10", "2026-08-12", RANGE);
    expect(bar.leftPx).toBe(dayPx("2026-08-10", RANGE));
    expect(bar.widthPx).toBe(3 * RANGE.pxPerDay);
    expect(bar.singleDate).toBe(false);
  });
  it("keeps a one-day bar wide enough to click", () => {
    const narrow = rangeForSpans([], "2026-08-15", "quarter");
    expect(barForSpan("2026-08-10", "2026-08-10", narrow).widthPx).toBe(MIN_BAR_PX);
  });
  it("has no bar for no span", () => {
    expect(barForRange(null, RANGE)).toBeNull();
  });
});

describe("pixels and days", () => {
  it("round trips a day through its pixel column", () => {
    expect(dayAtPx(dayPx("2026-08-10", RANGE) + 1, RANGE)).toBe("2026-08-10");
  });
  it("snaps a drag to the nearest whole day", () => {
    expect(dayStep(RANGE.pxPerDay * 2.4, RANGE)).toBe(2);
    expect(dayStep(-RANGE.pxPerDay * 2.6, RANGE)).toBe(-3);
  });
  it("clamps a pixel past either end into the range", () => {
    expect(dayAtPx(-50, RANGE)).toBe(RANGE.from);
    expect(dayAtPx(RANGE.widthPx + 500, RANGE)).toBe(RANGE.to);
  });
  it("shades Saturday and Sunday", () => {
    expect(isWeekend("2026-08-08")).toBe(true);
    expect(isWeekend("2026-08-09")).toBe(true);
    expect(isWeekend("2026-08-10")).toBe(false);
  });
});

describe("header cells", () => {
  it("builds one day cell per day, flagged", () => {
    const cells = dayCells(RANGE, "2026-08-10");
    expect(cells).toHaveLength(RANGE.days);
    expect(cells.find((c) => c.day === "2026-08-10")).toMatchObject({
      today: true,
      weekStart: true,
      weekend: false,
      label: "M",
    });
  });
  it("clips month and week cells to the range", () => {
    const months = monthCells(RANGE);
    expect(months[0].px).toBe(0);
    const last = months[months.length - 1];
    expect(last.px + last.widthPx).toBe(RANGE.widthPx);
    const weeks = weekCells(RANGE, "2026-08-10");
    expect(weeks[0].px).toBe(0);
    expect(weeks.filter((w) => w.today)).toHaveLength(1);
  });
});

describe("labelSide", () => {
  it("keeps the label on the right while there is room", () => {
    expect(labelSide({ leftPx: 10, widthPx: 20 }, 1000).side).toBe("right");
  });
  it("moves the label left at the end of the chart", () => {
    const placed = labelSide({ leftPx: 900, widthPx: 90 }, 1000);
    expect(placed.side).toBe("left");
    expect(placed.maxWidthPx).toBeGreaterThan(0);
  });
});
