import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { startOfWeek } from "@/app/calendar/components/shared";
import {
  buildWeek,
  dayLabel,
  hourWindow,
  nowDayIndex,
  nowTop,
  shiftAnchor,
  windowOfDays,
} from "./weekModel";
import { rowsToEvents } from "./objectEvents";
import type { GridEvent } from "./types";

// Local constructors only. No ISO string with a Z, so the tests hold in any zone.
const L = (d: number, h = 0, mi = 0, mo = 10) => new Date(2026, mo, d, h, mi);
const ev = (id: string, s: Date, e: Date, allDay = false): GridEvent => ({
  id,
  title: id,
  start: s,
  end: e,
  allDay,
  tone: "primary",
});
// Monday 2026-11-09 to Sunday 2026-11-15. "Now" is Wednesday 2026-11-11 10:30.
const ANCHOR = L(12);
const NOW = L(11, 10, 30);
const W = { startHour: 7, endHour: 22 };
const week = (events: GridEvent[], window = W) => buildWeek(events, { anchor: ANCHOR, mode: "week", window });
const blocksOf = (m: ReturnType<typeof buildWeek>, i: number) => m.timed[i].blocks;

describe("buildWeek days", () => {
  it("starts the week on Monday and has 7 days", () => {
    const m = week([]);
    expect(m.days.map((d) => d.key)).toEqual([
      "2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13", "2026-11-14", "2026-11-15",
    ]);
    expect(m.days[0].date.getDay()).toBe(1);
    expect(m.days[0].date).toEqual(startOfWeek(ANCHOR));
    expect(m.timed).toHaveLength(7);
  });
  it("makes one day in day mode", () => {
    const m = buildWeek([], { anchor: L(12, 15), mode: "day", window: W });
    expect(m.days.map((d) => d.key)).toEqual(["2026-11-12"]);
  });
  it("does not throw on a bad anchor or a bad event", () => {
    const bad = ev("x", new Date(NaN), L(12));
    expect(() => buildWeek([bad], { anchor: new Date(NaN), mode: "week" })).not.toThrow();
  });
});

describe("golden pixel values (the formula of TimeGrid)", () => {
  it("puts 09:30 to 11:00 at 115 px and 67 px in a 7 to 22 window", () => {
    // TimeGrid: top = ((570 - 420) / 60) * 46, height = max(20, (90 / 60) * 46 - 2).
    const m = week([ev("a", L(10, 9, 30), L(10, 11))]);
    const [b] = blocksOf(m, 1);
    expect(b.topPx).toBe(((9.5 - 7) * 46));
    expect(b.topPx).toBe(115);
    expect(b.heightPx).toBe(1.5 * 46 - 2);
    expect(b.heightPx).toBe(67);
  });
  it("gives a short block the least height of 20 px", () => {
    const [b] = blocksOf(week([ev("a", L(10, 9), L(10, 9, 10))]), 1);
    expect(b.heightPx).toBe(20);
  });
  it("keeps a block at the end of the window at the least height, and moves it up to fit", () => {
    // 21:50 in a 7 to 22 window: the clamp alone would leave 7.67 px.
    const [b] = blocksOf(week([ev("a", L(10, 21, 50), L(10, 21, 55))]), 1);
    expect(b.heightPx).toBe(20);
    expect(b.topPx + b.heightPx).toBe(15 * 46);
  });
  it("keeps the top of a block that starts late but has room above the least height", () => {
    // 21:00 for 90 minutes: the window cuts it at 22:00, so 44 px, which is above 20 px.
    const [b] = blocksOf(week([ev("a", L(10, 21), L(10, 22, 30))]), 1);
    expect(b.topPx).toBe(14 * 46);
    expect(b.heightPx).toBe(44);
  });
  it("scales by the hour height of the caller", () => {
    const m = buildWeek([ev("a", L(10, 9), L(10, 10))], { anchor: ANCHOR, mode: "week", window: W, hourPx: 60 });
    const [b] = blocksOf(m, 1);
    expect(b.topPx).toBe(120);
    expect(b.heightPx).toBe(58);
  });
  it("puts the now line at the same pixel as TimeGrid", () => {
    expect(nowTop(NOW, W)).toBe(((10 * 60 + 30 - 7 * 60) / 60) * 46);
    expect(nowTop(NOW, W)).toBe(161);
  });
});

describe("timed blocks", () => {
  it("puts two overlapping events on Tuesday in 2 lanes", () => {
    const m = week([ev("a", L(10, 9), L(10, 10)), ev("b", L(10, 9, 30), L(10, 10, 30)), ev("c", L(10, 13), L(10, 14))]);
    const by = (id: string) => blocksOf(m, 1).find((b) => b.id === id)!;
    expect([by("a").lane, by("a").lanes]).toEqual([0, 2]);
    expect([by("b").lane, by("b").lanes]).toEqual([1, 2]);
    expect([by("c").lane, by("c").lanes]).toEqual([0, 1]);
  });
  it("clips an event that crosses midnight at the window edge and flags the next day", () => {
    const m = week([ev("n", L(10, 20), L(11, 1))]);
    const [d1] = blocksOf(m, 1);
    expect(d1).toMatchObject({ startMin: 20 * 60, endMin: 22 * 60, clippedStart: false, clippedEnd: true });
    expect(blocksOf(m, 2)).toHaveLength(0);
    expect(m.outOfWindow).toEqual([{ id: "n", dayIndex: 2, side: "before" }]);
  });
  it("splits a midnight crossing over two days in the default widened window", () => {
    const m = buildWeek([ev("n", L(10, 22), L(11, 1))], { anchor: ANCHOR, mode: "week" });
    expect(m.window).toEqual({ startHour: 0, endHour: 24 });
    expect(blocksOf(m, 1)[0]).toMatchObject({ startMin: 22 * 60, endMin: 24 * 60, topPx: 22 * 46 });
    expect(blocksOf(m, 2)[0]).toMatchObject({ startMin: 0, endMin: 60, topPx: 0 });
  });
  it("draws the full parts of a midnight crossing in a full-day window", () => {
    const m = week([ev("n", L(10, 22), L(11, 1))], { startHour: 0, endHour: 24 });
    expect(blocksOf(m, 1)[0]).toMatchObject({ startMin: 22 * 60, endMin: 24 * 60, clippedEnd: true });
    expect(blocksOf(m, 2)[0]).toMatchObject({ startMin: 0, endMin: 60, clippedStart: true, clippedEnd: false });
  });
  it("flags and clips a part outside a fixed window", () => {
    const m = week([ev("e", L(10, 6), L(10, 8)), ev("l", L(10, 21), L(10, 23)), ev("gone", L(10, 5), L(10, 6)), ev("late", L(10, 22, 30), L(10, 23))]);
    const by = (id: string) => blocksOf(m, 1).find((b) => b.id === id)!;
    expect(by("e")).toMatchObject({ startMin: 7 * 60, clippedStart: true, topPx: 0 });
    expect(by("l")).toMatchObject({ endMin: 22 * 60, clippedEnd: true });
    expect(blocksOf(m, 1).map((b) => b.id).sort()).toEqual(["e", "l"]);
    expect(m.outOfWindow).toEqual([
      { id: "gone", dayIndex: 1, side: "before" },
      { id: "late", dayIndex: 1, side: "after" },
    ]);
  });
  it("widens the window by default so no event is out of it", () => {
    const m = buildWeek([ev("e", L(10, 5, 30), L(10, 6)), ev("l", L(10, 22, 15), L(10, 23, 10))], { anchor: ANCHOR, mode: "week" });
    expect(m.window).toEqual({ startHour: 5, endHour: 24 });
    expect(m.outOfWindow).toEqual([]);
    expect(blocksOf(m, 1)).toHaveLength(2);
  });
  it("leaves out a timed event of another week", () => {
    const m = week([ev("a", L(20, 9), L(20, 10))]);
    expect(m.timed.every((t) => t.blocks.length === 0)).toBe(true);
  });
  it("does not change its input", () => {
    const events = [ev("a", L(10, 9), L(10, 10)), ev("b", L(10, 22), L(11, 1))];
    const before = JSON.stringify(events);
    week(events);
    expect(JSON.stringify(events)).toBe(before);
  });
});

describe("all-day row", () => {
  it("puts a date-only row in the all-day row", () => {
    const [e] = rowsToEvents([{ id: "d", values: { due: "2026-11-12", name: "Due" } }], { dateField: "due", titleField: "name" });
    const m = week([e]);
    expect(m.allDay).toEqual([{ id: "d", startCol: 3, span: 1, clippedLeft: false, clippedRight: false }]);
    expect(m.timed.every((t) => t.blocks.length === 0)).toBe(true);
  });
  it("clips a 3-day bar at the week edge", () => {
    const m = week([ev("trip", L(14), L(17), true)]);
    expect(m.allDay).toEqual([{ id: "trip", startCol: 5, span: 2, clippedLeft: false, clippedRight: true }]);
    const m2 = week([ev("trip", L(7), L(10), true)]);
    expect(m2.allDay).toEqual([{ id: "trip", startCol: 0, span: 1, clippedLeft: true, clippedRight: false }]);
  });
  it("sends a timed span of 24 hours or more to the all-day row", () => {
    const m = week([ev("long", L(10, 9), L(12, 9))]);
    expect(m.allDay).toEqual([{ id: "long", startCol: 1, span: 3, clippedLeft: false, clippedRight: false }]);
    expect(blocksOf(m, 1)).toHaveLength(0);
  });
  it("skips an all-day event outside the week", () => {
    expect(week([ev("x", L(20), L(21), true)]).allDay).toEqual([]);
  });
});

describe("rows from an object", () => {
  const src = { dateField: "due", titleField: "name", endField: "until" };
  it("skips a row with no date and reads an end before the start as the default length", () => {
    const events = rowsToEvents(
      [
        { id: "none", values: { name: "n" } },
        { id: "bad", values: { due: "2026-11-10T09:00:00", until: "2026-11-10T08:00:00", name: "b" } },
      ],
      src,
    );
    expect(events.map((e) => e.id)).toEqual(["bad"]);
    const [b] = blocksOf(week(events), 1);
    expect(b).toMatchObject({ startMin: 9 * 60, endMin: 10 * 60 });
  });
});

describe("hourWindow", () => {
  it("is the default with no events and never narrower", () => {
    expect(hourWindow([])).toEqual({ startHour: 7, endHour: 22 });
    expect(hourWindow([ev("a", L(10, 9), L(10, 10))])).toEqual({ startHour: 7, endHour: 22 });
    expect(hourWindow([ev("a", L(10, 9), L(10, 10))], 8, 18)).toEqual({ startHour: 8, endHour: 18 });
  });
  it("widens to whole hours for an early and a late event", () => {
    expect(hourWindow([ev("a", L(10, 5, 45), L(10, 6, 30))])).toEqual({ startHour: 5, endHour: 22 });
    expect(hourWindow([ev("a", L(10, 21), L(10, 22, 10))])).toEqual({ startHour: 7, endHour: 23 });
  });
  it("clamps to 0 to 24 and ignores all-day events", () => {
    expect(hourWindow([ev("a", L(10, 0), L(10, 23, 59))])).toEqual({ startHour: 0, endHour: 24 });
    expect(hourWindow([ev("a", L(10), L(11), true)])).toEqual({ startHour: 7, endHour: 22 });
    expect(hourWindow([], -5, 99)).toEqual({ startHour: 0, endHour: 24 });
  });
  it("keeps a valid window when the defaults are reversed", () => {
    const w = hourWindow([], 20, 10);
    expect(w.endHour).toBeGreaterThan(w.startHour);
  });
});

describe("now line", () => {
  const m = week([]);
  it("is a pixel inside the window and in the week", () => {
    expect(nowTop(NOW, W, 46, m.days)).toBe(161);
    expect(nowDayIndex(m.days, NOW)).toBe(2);
  });
  it("is null outside the window", () => {
    expect(nowTop(L(11, 6, 59), W)).toBeNull();
    expect(nowTop(L(11, 22, 1), W)).toBeNull();
    expect(nowTop(L(11, 22, 0), W)).toBe(15 * 46);
  });
  it("is null outside the visible days", () => {
    const far = L(20, 10, 30);
    expect(nowDayIndex(m.days, far)).toBeNull();
    expect(nowTop(far, W, 46, m.days)).toBeNull();
    expect(nowTop(new Date(NaN), W)).toBeNull();
  });
});

describe("helpers", () => {
  it("labels a day", () => {
    expect(dayLabel(L(9))).toBe("Mon 9");
    expect(dayLabel(L(15))).toBe("Sun 15");
  });
  it("shifts the anchor by 7 or 1 days", () => {
    expect(shiftAnchor(L(12), "week", 1)).toEqual(L(19));
    expect(shiftAnchor(L(12), "week", -1)).toEqual(L(5));
    expect(shiftAnchor(L(12), "day", 1)).toEqual(L(13));
    expect(shiftAnchor(L(30), "day", 1)).toEqual(new Date(2026, 11, 1));
  });
  it("gives the filter range: start of the first day to the start after the last", () => {
    const m = week([]);
    expect(windowOfDays(m.days)).toEqual({
      from: L(9).toISOString(),
      to: L(16).toISOString(),
    });
    expect(windowOfDays([])).toBeNull();
  });
  it("gives a TZ-independent range of 7 local days", () => {
    const r = windowOfDays(week([]).days)!;
    const from = new Date(r.from);
    const to = new Date(r.to);
    expect([from.getHours(), from.getMinutes(), from.getDate()]).toEqual([0, 0, 9]);
    expect([to.getHours(), to.getMinutes(), to.getDate()]).toEqual([0, 0, 16]);
  });
});

describe("daylight saving", () => {
  const saved = process.env.TZ;
  afterEach(() => {
    if (saved === undefined) delete process.env.TZ;
    else process.env.TZ = saved;
  });
  it("keeps 7 local days and midnight edges in a spring-forward week (US, 2026-03-08)", () => {
    process.env.TZ = "America/New_York";
    // Confirm that the zone took effect. If it did not, this test fails and does not skip.
    expect(new Date(2026, 2, 8, 12).getTimezoneOffset()).toBe(240);
    expect(new Date(2026, 2, 2, 12).getTimezoneOffset()).toBe(300);
    const m = buildWeek([ev("a", new Date(2026, 2, 8, 9), new Date(2026, 2, 8, 10))], {
      anchor: new Date(2026, 2, 4),
      mode: "week",
      window: W,
    });
    expect(m.days.map((d) => d.key)).toEqual([
      "2026-03-02", "2026-03-03", "2026-03-04", "2026-03-05", "2026-03-06", "2026-03-07", "2026-03-08",
    ]);
    expect(m.days.every((d) => d.date.getHours() === 0)).toBe(true);
    const r = windowOfDays(m.days)!;
    // The week has 6 days of 24 hours and 1 day of 23 hours.
    expect((Date.parse(r.to) - Date.parse(r.from)) / 3600000).toBe(167);
    // The wall clock sets the pixels. 09:00 is 2 hours into the window on the short day.
    expect(m.timed[6].blocks[0]).toMatchObject({ startMin: 9 * 60, topPx: 2 * 46 });
    expect(nowDayIndex(m.days, new Date(2026, 2, 8, 12))).toBe(6);
  });
  it("shifts a week across the spring-forward edge to a local Monday", () => {
    process.env.TZ = "America/New_York";
    const next = shiftAnchor(new Date(2026, 2, 4), "week", 1);
    expect(next).toEqual(new Date(2026, 2, 11));
    const m = buildWeek([], { anchor: next, mode: "week", window: W });
    expect(m.days[0].key).toBe("2026-03-09");
    expect(m.days[0].date.getHours()).toBe(0);
  });
});

describe("stress", () => {
  it("keeps every block in the grid and every lane below its count (200 events)", () => {
    let seed = 20261114;
    const rnd = () => {
      seed = (seed * 1664525 + 1013904223) % 4294967296;
      return seed / 4294967296;
    };
    const events: GridEvent[] = [];
    for (let i = 0; i < 200; i++) {
      const day = 9 + Math.floor(rnd() * 7);
      const start = L(day, Math.floor(rnd() * 24), Math.floor(rnd() * 4) * 15);
      const end = new Date(start.getTime() + (5 + Math.floor(rnd() * 600)) * 60000);
      events.push(ev(`e${i}`, start, end));
    }
    for (const window of [W, hourWindow(events), { startHour: 0, endHour: 24 }]) {
      const m = week(events, window);
      const h = (window.endHour - window.startHour) * 46;
      let count = 0;
      for (const t of m.timed) {
        for (const b of t.blocks) {
          count++;
          expect(b.topPx).toBeGreaterThanOrEqual(0);
          expect(b.topPx + b.heightPx).toBeLessThanOrEqual(h);
          expect(b.lane).toBeLessThan(b.lanes);
        }
      }
      expect(count + m.outOfWindow.length).toBeGreaterThanOrEqual(200);
    }
    const again = JSON.stringify(week(events));
    expect(JSON.stringify(week(events))).toBe(again);
  });
});

describe("source fences", () => {
  const dir = __dirname;
  const src = readFileSync(join(dir, "weekModel.ts"), "utf-8");
  const code = src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");
  it("imports the shared geometry and defines none of it", () => {
    expect(src).not.toMatch(/HOUR_PX\s*=/);
    expect(code).not.toMatch(/\b46\b/);
    expect(src).toMatch(/import\s*\{[^}]*\bHOUR_PX\b[^}]*\}\s*from/);
    expect(src).toMatch(/import\s*\{[^}]*\bstartOfWeek\b[^}]*\}\s*from/);
    expect(src).toMatch(/import\s*\{\s*layoutSpans\s*\}\s*from\s*"\.\/layout"/);
    expect(src).not.toMatch(/function (startOfWeek|hourLabel|layoutSpans)/);
  });
  it("defines no HOUR_PX anywhere in components/calendar", () => {
    for (const f of readdirSync(dir)) {
      if (!/\.(ts|tsx)$/.test(f) || f.endsWith(".test.ts")) continue;
      expect(readFileSync(join(dir, f), "utf-8"), f).not.toMatch(/\bHOUR_PX\s*=/);
    }
  });
  it("is pure: no clock, no random, no React", () => {
    expect(code).not.toMatch(/Date\.now|new Date\(\)|Math\.random|performance\.now/);
    expect(code).not.toMatch(/from\s+["']react/);
    expect(code).not.toMatch(/\bfetch\(|localStorage|document\./);
  });
});
