import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import {
  addDateDays,
  allDayOnDate,
  allDayRow,
  externalChips,
  externalViews,
  laneSegments,
  splitByTasks,
  taskInset,
  STRIP_SHARE_DAY,
  STRIP_SHARE_WEEK,
  externalReadNote,
  READ_FAILED_WORDS,
  segmentsForDay as segs,
  readExternal,
  safeHttps,
  segmentsForDay,
  sheetFacts,
  type ExternalView,
} from "./externalEntries";

const base = {
  id: "e1",
  kind: "external",
  source: "google",
  title: "Standup",
  all_day: false,
  starts_at: "2026-11-12T09:00:00Z",
  ends_at: "2026-11-12T10:00:00Z",
  status: "confirmed",
  busy: true,
  task_id: null,
  external_url: "https://calendar.google.com/event?eid=1",
  conference_url: "https://meet.google.com/abc",
};
const row = (over: Record<string, unknown>) => ({ ...base, ...over });
const allDay = (over: Record<string, unknown>) =>
  row({ all_day: true, starts_at: null, ends_at: null, start_date: "2026-11-12", end_date: "2026-11-13", ...over });
const view = (over: Record<string, unknown>): ExternalView => readExternal(row(over))!;
const day = (y: number, m: number, d: number) => new Date(y, m - 1, d);

describe("what the grid draws", () => {
  it("drops a cancelled entry", () => {
    expect(readExternal(row({ status: "cancelled" }))).toBeNull();
  });
  it("keeps only the external kind", () => {
    expect(readExternal(row({ kind: "event" }))).toBeNull();
    expect(readExternal(row({}))).not.toBeNull();
  });
  it("keeps an entry that links a task, because task_id is a link", () => {
    expect(readExternal(row({ task_id: "t-1" }))).not.toBeNull();
  });
  it("marks busy=false as free and tentative as tentative", () => {
    expect(view({ busy: false }).free).toBe(true);
    expect(view({}).free).toBe(false);
    expect(view({ status: "tentative" }).tentative).toBe(true);
  });
  it("sorts all-day first, then by start", () => {
    const out = externalViews([
      row({ id: "b", starts_at: "2026-11-12T11:00:00Z", ends_at: "2026-11-12T12:00:00Z" }),
      row({ id: "a" }),
      allDay({ id: "d" }),
      { nonsense: 1 },
      null,
    ]);
    expect(out.map((v) => v.id)).toEqual(["d", "a", "b"]);
  });
  it("reads no entries from a missing key", () => {
    expect(externalViews(undefined)).toEqual([]);
  });
});

describe("timed entries in the member zone", () => {
  it("lands 09:00 UTC at 14:30 in Asia/Kolkata", () => {
    const segs = segmentsForDay([view({})], day(2026, 11, 12), "Asia/Kolkata");
    expect(segs).toHaveLength(1);
    expect(segs[0].startMin).toBe(14 * 60 + 30);
    expect(segs[0].endMin).toBe(15 * 60 + 30);
  });
  it("moves to the previous day in America/Los_Angeles", () => {
    const e = view({ starts_at: "2026-11-12T03:00:00Z", ends_at: "2026-11-12T04:00:00Z" });
    expect(segmentsForDay([e], day(2026, 11, 12), "America/Los_Angeles")).toHaveLength(0);
    const s = segmentsForDay([e], day(2026, 11, 11), "America/Los_Angeles");
    expect(s[0].startMin).toBe(19 * 60);
  });
  it("splits an entry across midnight, and gives an end at 00:00 nothing", () => {
    const e = view({ starts_at: "2026-11-12T22:00:00Z", ends_at: "2026-11-13T02:00:00Z" });
    expect(segmentsForDay([e], day(2026, 11, 12), "UTC")[0]).toMatchObject({ startMin: 1320, endMin: 1440 });
    expect(segmentsForDay([e], day(2026, 11, 13), "UTC")[0]).toMatchObject({ startMin: 0, endMin: 120 });
    const f = view({ starts_at: "2026-11-12T22:00:00Z", ends_at: "2026-11-13T00:00:00Z" });
    expect(segmentsForDay([f], day(2026, 11, 13), "UTC")).toHaveLength(0);
  });
  it("keeps the wall clock on a DST day", () => {
    // 2026-03-08: New York springs forward at 02:00. 12:00Z is 08:00 EDT.
    const e = view({ starts_at: "2026-03-08T12:00:00Z", ends_at: "2026-03-08T13:00:00Z" });
    expect(segmentsForDay([e], day(2026, 3, 8), "America/New_York")[0].startMin).toBe(8 * 60);
    // A day before, 12:00Z is 07:00 EST.
    const f = view({ starts_at: "2026-03-07T12:00:00Z", ends_at: "2026-03-07T13:00:00Z" });
    expect(segmentsForDay([f], day(2026, 3, 7), "America/New_York")[0].startMin).toBe(7 * 60);
  });
  it("gives a zero-length entry a visible height", () => {
    const e = view({ ends_at: "2026-11-12T09:00:00Z" });
    const s = segmentsForDay([e], day(2026, 11, 12), "UTC")[0];
    expect(s.endMin - s.startMin).toBeGreaterThan(0);
  });
  it("puts two overlapping entries in two lanes", () => {
    const a = view({ id: "a" });
    const b = view({ id: "b", starts_at: "2026-11-12T09:30:00Z", ends_at: "2026-11-12T10:30:00Z" });
    const laid = laneSegments(segmentsForDay([a, b], day(2026, 11, 12), "UTC"));
    expect(laid.map((l) => [l.lane, l.lanes])).toEqual([
      [0, 2],
      [1, 2],
    ]);
  });
});

describe("all-day entries keep their date", () => {
  const e = readExternal(allDay({ start_date: "2026-11-12", end_date: "2026-11-14" }))!;
  it("treats end_date as exclusive", () => {
    expect(allDayOnDate([e], "2026-11-11")).toHaveLength(0);
    expect(allDayOnDate([e], "2026-11-12")).toHaveLength(1);
    expect(allDayOnDate([e], "2026-11-13")).toHaveLength(1);
    expect(allDayOnDate([e], "2026-11-14")).toHaveLength(0);
  });
  it("shows the same day in every zone", () => {
    for (const zone of ["Pacific/Kiritimati", "UTC", "Pacific/Pago_Pago"]) {
      expect(externalChips([e], day(2026, 11, 12), zone)).toHaveLength(1);
      expect(externalChips([e], day(2026, 11, 14), zone)).toHaveLength(0);
    }
  });
  it("counts a missing end as one day", () => {
    const one = readExternal(allDay({ end_date: null }))!;
    expect(one.endDate).toBe("2026-11-13");
  });
  it("adds days across a month edge", () => {
    expect(addDateDays("2026-11-30", 1)).toBe("2026-12-01");
    expect(addDateDays("2026-12-31", 1)).toBe("2027-01-01");
    expect(addDateDays("2026-03-01", -1)).toBe("2026-02-28");
  });
  it("runs a span over a month edge in the Month cell", () => {
    const span = readExternal(allDay({ start_date: "2026-11-29", end_date: "2026-12-02" }))!;
    expect(externalChips([span], day(2026, 11, 30), "UTC")).toHaveLength(1);
    expect(externalChips([span], day(2026, 12, 1), "UTC")).toHaveLength(1);
    expect(externalChips([span], day(2026, 12, 2), "UTC")).toHaveLength(0);
  });
});

describe("the all-day row", () => {
  const week = Array.from({ length: 7 }, (_, i) => day(2026, 11, 9 + i));
  it("draws a span of days as one bar", () => {
    const e = readExternal(allDay({ start_date: "2026-11-10", end_date: "2026-11-13" }))!;
    const r = allDayRow([e], week);
    expect(r.bars).toHaveLength(1);
    expect([r.bars[0].from, r.bars[0].to]).toEqual([1, 3]);
  });
  it("folds more than two in a day to +N", () => {
    const four = ["a", "b", "c", "d"].map(
      (id) => readExternal(allDay({ id, start_date: "2026-11-11", end_date: "2026-11-12" }))!,
    );
    const r = allDayRow(four, week);
    expect(r.bars).toHaveLength(2);
    expect(r.more[2]).toBe(2);
    expect(r.more.filter(Boolean)).toHaveLength(1);
    expect(allDayRow(four, week, true).bars).toHaveLength(4);
  });
  it("keeps two in a day unfolded", () => {
    const two = ["a", "b"].map((id) => readExternal(allDay({ id }))!);
    expect(allDayRow(two, week).more.every((n) => n === 0)).toBe(true);
  });
  it("clips a span that starts before the week", () => {
    const e = readExternal(allDay({ start_date: "2026-11-01", end_date: "2026-11-11" }))!;
    const r = allDayRow([e], week);
    expect([r.bars[0].from, r.bars[0].to]).toEqual([0, 1]);
  });
});

describe("the Month chips", () => {
  it("lists all-day first, then by start", () => {
    const early = view({ id: "x", starts_at: "2026-11-12T06:00:00Z", ends_at: "2026-11-12T07:00:00Z" });
    const late = view({ id: "y" });
    const all = readExternal(allDay({ id: "z" }))!;
    const chips = externalChips([all, early, late], day(2026, 11, 12), "UTC");
    expect(chips.map((c) => c.id)).toEqual(["z", "x", "y"]);
    expect(chips[0].time).toBe("");
    expect(chips[1].time).not.toBe("");
  });
});

describe("the sheet", () => {
  it("names the source and the open link", () => {
    const f = sheetFacts(view({}), "UTC");
    expect(f.source).toBe("From Google Calendar, read only.");
    expect(f.openLabel).toBe("Open in Google Calendar");
    expect(f.openUrl).toBe("https://calendar.google.com/event?eid=1");
    expect(f.joinUrl).toBe("https://meet.google.com/abc");
  });
  it("names Outlook", () => {
    const f = sheetFacts(view({ source: "outlook" }), "UTC");
    expect(f.source).toBe("From Outlook, read only.");
    expect(f.openLabel).toBe("Open in Outlook");
  });
  it("links only an https URL", () => {
    expect(safeHttps("http://x.test")).toBeNull();
    expect(safeHttps("javascript:alert(1)")).toBeNull();
    expect(safeHttps("not a url")).toBeNull();
    expect(safeHttps(null)).toBeNull();
    expect(sheetFacts(view({ external_url: "http://x.test", conference_url: "javascript:1" }), "UTC")).toMatchObject({
      openUrl: null,
      joinUrl: null,
    });
  });
  it("says so for a free or an unconfirmed entry", () => {
    expect(sheetFacts(view({ busy: false }), "UTC").note).toBe("Shown as free.");
    expect(sheetFacts(view({ status: "tentative" }), "UTC").note).toBe("Not confirmed yet.");
    expect(sheetFacts(view({}), "UTC").note).toBeNull();
  });
  it("shows no zone for an all-day entry", () => {
    const f = sheetFacts(readExternal(allDay({}))!, "Asia/Kolkata");
    expect(f.zone).toBeNull();
    expect(f.when).toMatch(/all day/);
  });
});

describe("the flag off", () => {
  const src = (p: string) => readFileSync(new URL(p, import.meta.url), "utf8");
  it("asks for no entries, and the hook holds still", () => {
    const hook = src("./useExternalEntries.ts");
    expect(hook).toMatch(/on \? projectsKey\([^)]*\) : null/);
    expect(hook).toContain("calendarEventsOn()");
  });
  it("keeps lensFetchScheduled on the task rows", () => {
    const lens = src("../../tasks/lib/lens.ts");
    expect(lens).toMatch(/lensFetchScheduled\(start: string, end: string\): Promise<MyTask\[\]>/);
    expect(lens).toContain("export async function lensFetchCalendar(");
  });
  it("lets only the Calendar read the entries", () => {
    expect(src("../components/TodayCard.tsx")).not.toContain("lensFetchCalendar");
    expect(src("../../tasks/lib/lens.ts")).not.toContain("externalEntries");
  });
});

describe("an event beside a task", () => {
  it("takes the strip when a task shares its time, and the column when none does", () => {
    const meet = view({ id: "m", starts_at: "2026-11-12T10:00:00Z", ends_at: "2026-11-12T11:00:00Z" });
    const lone = view({ id: "l", starts_at: "2026-11-12T14:00:00Z", ends_at: "2026-11-12T15:00:00Z" });
    const list = segmentsForDay([meet, lone], day(2026, 11, 12), "UTC");
    const { clear, beside } = splitByTasks(list, [{ start: 600, end: 660 }]);
    expect(beside.map((x) => x.entry.id)).toEqual(["m"]);
    expect(clear.map((x) => x.entry.id)).toEqual(["l"]);
    // A task that only touches the end does not overlap.
    expect(splitByTasks(list, [{ start: 660, end: 700 }]).beside).toHaveLength(0);
    expect(splitByTasks(list, []).clear).toHaveLength(2);
  });
  it("draws the strip above the cards in the grid", () => {
    const grid = readFileSync(new URL("../components/TimeGrid.tsx", import.meta.url), "utf8");
    expect(grid).toContain("splitByTasks(");
    expect(grid).toContain('strip ? "z-10"');
  });
});

describe("the read error", () => {
  it("says one short line on a failed read, and nothing otherwise", () => {
    expect(externalReadNote("HTTP 503")).toBe(READ_FAILED_WORDS);
    expect(READ_FAILED_WORDS).toBe("Could not read your Google and Outlook events. Try again in a minute.");
    expect(externalReadNote(null)).toBeNull();
  });
  it("keeps the last good list and shows the line in the Calendar", () => {
    const src = (p: string) => readFileSync(new URL(p, import.meta.url), "utf8");
    expect(src("./useExternalEntries.ts")).toContain("last.current");
    expect(src("../CalendarView.tsx")).toContain("externalNote");
  });
});

describe("the day index", () => {
  it("gives the same segments on a second call, from one index", () => {
    const list = [view({})];
    expect(segs(list, day(2026, 11, 12), "UTC")).toBe(segs(list, day(2026, 11, 12), "UTC"));
  });
});

describe("Month order", () => {
  it("puts the external chips first in the cell", () => {
    const grid = readFileSync(new URL("./gridAdapter.ts", import.meta.url), "utf8");
    expect(grid.indexOf("externalChips(")).toBeLessThan(grid.indexOf("...blocks.map"));
  });
});

describe("the overlap strip widths", () => {
  it("takes 45 percent in Week and 30 in Day", () => {
    expect(STRIP_SHARE_WEEK).toBe(0.45);
    expect(STRIP_SHARE_DAY).toBe(0.3);
  });
  it("shrinks each overlapped task by the same share, and no other", () => {
    const meet = view({ starts_at: "2026-11-12T10:00:00Z", ends_at: "2026-11-12T11:00:00Z" });
    const { beside } = splitByTasks(segmentsForDay([meet], day(2026, 11, 12), "UTC"), [{ start: 600, end: 660 }]);
    expect(taskInset(beside, { start: 600, end: 630 }, 0.45)).toBe(0.45);
    expect(taskInset(beside, { start: 630, end: 700 }, 0.45)).toBe(0.45);
    expect(taskInset(beside, { start: 660, end: 700 }, 0.45)).toBe(0);
    expect(taskInset([], { start: 600, end: 630 }, 0.45)).toBe(0);
  });
  it("wraps the title to two lines in the grid", () => {
    const grid = readFileSync(new URL("../components/TimeGrid.tsx", import.meta.url), "utf8");
    expect(grid).toContain("line-clamp-2");
    expect(grid).toContain("taskInset(");
  });
});
