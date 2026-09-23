import { describe, expect, it } from "vitest";
import type { MyTask } from "./types";
import { blocksForDay, firstFreeSlot, startOfDay, type Block } from "./scheduling";

// Minimal MyTask factory — only the fields the geometry reads matter.
const item = (over: Partial<MyTask>): MyTask =>
  ({ id: "x", title: "t", disposition: "NEXT", ...over }) as MyTask;

// A fixed future day so "today" logic (firstFreeSlot's now-clamp) never fires.
const DAY = new Date(2099, 5, 15); // 15 Jun 2099, local
// Accepts a decimal hour (10.5 → 10:30); setHours truncates, so split it here.
const at = (dec: number) => {
  const d = startOfDay(DAY);
  const h = Math.floor(dec);
  d.setHours(h, Math.round((dec - h) * 60), 0, 0);
  return d;
};

describe("blocksForDay", () => {
  it("keeps only items scheduled on the day, sorted by start", () => {
    const items = [
      item({ id: "b", scheduledStart: at(11).toISOString(), scheduledEnd: at(12).toISOString() }),
      item({ id: "a", scheduledStart: at(9).toISOString(), scheduledEnd: at(10).toISOString() }),
      item({ id: "other-day", scheduledStart: new Date(2099, 5, 16, 9).toISOString() }),
      item({ id: "unscheduled" }),
    ];
    const blocks = blocksForDay(items, DAY);
    expect(blocks.map((b) => b.item.id)).toEqual(["a", "b"]);
  });

  it("derives end from the time estimate when scheduledEnd is absent", () => {
    const [b] = blocksForDay(
      [item({ scheduledStart: at(9).toISOString(), timeEstimateMins: 45 })],
      DAY,
    );
    expect((b.end.getTime() - b.start.getTime()) / 60000).toBe(45);
  });
});

describe("firstFreeSlot", () => {
  const blocks = (spans: [number, number][]): Block[] =>
    spans.map(([s, e], i) => ({
      item: item({ id: `blk${i}` }),
      start: at(s),
      end: at(e),
    }));

  it("returns the window start on an empty day", () => {
    expect(firstFreeSlot([], DAY, 60, 9, 17)).toEqual(at(9));
  });

  it("packs into the first gap that fits", () => {
    // busy 9–10 and 10:30–12; a 30-min task fits 10:00–10:30.
    const got = firstFreeSlot(blocks([[9, 10], [10.5, 12]]), DAY, 30, 9, 17);
    expect(got).toEqual(at(10));
  });

  it("skips a gap too small and lands after the blocking block", () => {
    // busy 9–10 then 10:15–12; a 30-min task can't fit the 15-min gap → 12:00.
    const got = firstFreeSlot(blocks([[9, 10], [10.25, 12]]), DAY, 30, 9, 17);
    expect(got).toEqual(at(12));
  });

  it("falls back to the window start when nothing fits before day end", () => {
    // full day booked → overflow returns the earliest (documented 'stack at top').
    const got = firstFreeSlot(blocks([[9, 17]]), DAY, 60, 9, 17);
    expect(got).toEqual(at(9));
  });
});
