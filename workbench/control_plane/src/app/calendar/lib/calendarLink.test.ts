/**
 * The Calendar's link to a day and a time (WS-58 MD-3a, `my_day_feed.md`
 * §5.8 item 9).
 *
 * Mutations caught: the parser takes a day that does not exist; a bad part
 * spoils the good one; a link with nothing good in it moves the page; the
 * scroll lands on the time instead of an hour above it; the page stops
 * reading the link, or reads it outside a Suspense boundary.
 */
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import { isInAppPath } from "@/lib/inAppLink";
import {
  AT_PARAM,
  DATE_PARAM,
  calendarLinkFromSearch,
  parseLinkClock,
  parseLinkDate,
  scrollTopFor,
} from "./calendarLink";

const q = (s: string) => new URLSearchParams(s);

describe("parseLinkDate", () => {
  it("reads a real day as its local midnight", () => {
    const d = parseLinkDate("2026-10-14")!;
    expect([d.getFullYear(), d.getMonth(), d.getDate()]).toEqual([2026, 9, 14]);
    expect([d.getHours(), d.getMinutes(), d.getSeconds()]).toEqual([0, 0, 0]);
    expect(parseLinkDate("2028-02-29")).not.toBeNull(); // a leap day
  });

  it("refuses a day that does not exist, so the page stays on today", () => {
    for (const bad of [
      "2026-02-30", "2026-02-29", "2026-13-01", "2026-00-10", "2026-10-00",
      "2026-10-32", "0050-01-01",
    ]) {
      expect(parseLinkDate(bad), bad).toBeNull();
    }
  });

  it("refuses any other shape", () => {
    for (const bad of [
      "", "today", "2026-1-5", "26-10-14", "2026/10/14", "2026-10-14T09:00",
      "2026-10-14x", " 2026-10-1 4", "1e3", null, undefined,
    ]) {
      expect(parseLinkDate(bad), String(bad)).toBeNull();
    }
  });
});

describe("parseLinkClock", () => {
  it("reads a 24-hour HH:MM", () => {
    expect(parseLinkClock("14:00")).toEqual({ hour: 14, minute: 0 });
    expect(parseLinkClock("00:00")).toEqual({ hour: 0, minute: 0 });
    expect(parseLinkClock("23:59")).toEqual({ hour: 23, minute: 59 });
    expect(parseLinkClock("09:05")).toEqual({ hour: 9, minute: 5 });
  });

  it("refuses a time that is not HH:MM on a 24-hour clock", () => {
    for (const bad of ["24:00", "12:60", "9:00", "0900", "2pm", "14:00:00", "-1:00", "", null]) {
      expect(parseLinkClock(bad), String(bad)).toBeNull();
    }
  });
});

describe("calendarLinkFromSearch", () => {
  it("reads both parts", () => {
    const link = calendarLinkFromSearch(q(`${DATE_PARAM}=2026-10-14&${AT_PARAM}=14:00`))!;
    expect(link.date?.getDate()).toBe(14);
    expect(link.at).toEqual({ hour: 14, minute: 0 });
  });

  it("reads the gateway's link, where the colon may arrive encoded", () => {
    const link = calendarLinkFromSearch(q("date=2026-10-14&at=14%3A30"))!;
    expect(link.at).toEqual({ hour: 14, minute: 30 });
  });

  it("keeps the good part when the other part is bad", () => {
    const badTime = calendarLinkFromSearch(q("date=2026-10-14&at=25:00"))!;
    expect(badTime.date?.getDate()).toBe(14);
    expect(badTime.at).toBeNull();
    const badDay = calendarLinkFromSearch(q("date=2026-02-30&at=09:00"))!;
    expect(badDay.date).toBeNull();
    expect(badDay.at).toEqual({ hour: 9, minute: 0 });
  });

  it("answers null when nothing good is named, so the page does what it does today", () => {
    expect(calendarLinkFromSearch(q(""))).toBeNull();
    expect(calendarLinkFromSearch(q("date=nope&at=nope"))).toBeNull();
    expect(calendarLinkFromSearch(q("task=t-1"))).toBeNull();
    expect(calendarLinkFromSearch(null)).toBeNull();
  });

  it("is an in-app path, so the feed and the chat open it in this tab", () => {
    expect(isInAppPath("/calendar?date=2026-10-14&at=14:00")).toBe(true);
    expect(isInAppPath("/calendar?date=2026-10-14")).toBe(true);
  });
});

describe("scrollTopFor", () => {
  it("lands one hour above the time, as the page lands above the current hour", () => {
    expect(scrollTopFor({ hour: 14, minute: 0 }, 46)).toBe(13 * 46);
    expect(scrollTopFor({ hour: 14, minute: 30 }, 40)).toBe(13.5 * 40);
  });

  it("never scrolls above the top of the grid", () => {
    expect(scrollTopFor({ hour: 0, minute: 0 }, 46)).toBe(0);
    expect(scrollTopFor({ hour: 0, minute: 45 }, 40)).toBe(0);
  });
});

describe("the page reads the link", () => {
  const read = (p: string) => readFileSync(new URL(p, import.meta.url), "utf-8");

  it("the reader parses the query here and sits inside Suspense", () => {
    const reader = read("../components/CalendarDeepLink.tsx");
    expect(reader).toMatch(/useSearchParams\(\)/);
    expect(reader).toMatch(/calendarLinkFromSearch\(/);
    const view = read("../CalendarView.tsx");
    expect(view).toMatch(/<Suspense fallback=\{null\}>\s*<CalendarDeepLink onLink=\{openLink\} \/>/);
  });

  it("a link opens the day view and scrolls by the link's time", () => {
    const view = read("../CalendarView.tsx");
    const open = view.slice(view.indexOf("const openLink"));
    const body = open.slice(0, open.indexOf("}, []);"));
    expect(body).toMatch(/setAnchor\(link\.date \?\? startOfDay\(new Date\(\)\)\)/);
    expect(body).toMatch(/setMode\("day"\)/);
    expect(body).toMatch(/setJumpAt\(link\.at\)/);
    expect(view).toMatch(/jumpAt\s*\?\s*scrollTopFor\(jumpAt\)/);
  });
});
