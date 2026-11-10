/**
 * The Calendar's link to a day and a time (WS-58 MD-3a, `my_day_feed.md`
 * §5.8 item 9).
 *
 * Mutations caught: the parser takes a day that does not exist; a bad part
 * spoils the good one; a link with nothing good in it moves the page; the
 * scroll lands on the time instead of an hour above it; the page stops
 * reading the link, or reads it outside a Suspense boundary; the scroll stops
 * re-running on a link; a change by hand keeps the link's time.
 */
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import { isInAppPath } from "@/lib/inAppLink";
import {
  AT_PARAM,
  DATE_PARAM,
  calendarLinkFromSearch,
  linkQuery,
  parseLinkClock,
  parseLinkDate,
  REMINDER_PARAM,
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

describe("the reminder link (WS-66 RX-2)", () => {
  const RID = "e1f8fad5-d9cb-469f-a165-70867728950e";

  it("reads /calendar?reminder=<id> as a link that names a reminder and no day", () => {
    const link = calendarLinkFromSearch(q(`${REMINDER_PARAM}=${RID}`))!;
    expect(link.reminder).toBe(RID);
    expect(link.date).toBeNull();
    expect(link.at).toBeNull();
    expect(calendarLinkFromSearch(q(`reminder=${RID.toUpperCase()}`))!.reminder).toBe(RID);
  });

  it("keeps the day and the time when the link has them too", () => {
    const link = calendarLinkFromSearch(q(`date=2026-10-14&at=14:00&reminder=${RID}`))!;
    expect(link.reminder).toBe(RID);
    expect(link.date?.getDate()).toBe(14);
    expect(link.at).toEqual({ hour: 14, minute: 0 });
  });

  it("refuses an id that is not a UUID, so the page does what a bare /calendar does", () => {
    for (const bad of ["", "abc", "../x", `${RID}x`, "javascript:alert(1)"]) {
      expect(calendarLinkFromSearch(q(`reminder=${encodeURIComponent(bad)}`)), bad).toBeNull();
    }
    expect(calendarLinkFromSearch(q(`date=2026-10-14&reminder=nope`))!.reminder).toBeNull();
  });

  it("the page marks the row, opens the sheet on a phone, and says so for a reminder that is not open", () => {
    const view = readFileSync(new URL("../CalendarView.tsx", import.meta.url), "utf-8").replace(/\r\n/g, "\n");
    expect(view).toMatch(/setMarkReminder\(link\.reminder\)/);
    expect(view).toContain("marked={markReminder}");
    expect(view).toContain("if (isMobile) setRemSheet(true);");
    expect(view).toContain("That reminder is closed, or it is not yours.");
  });
});

describe("the New reminder job (WS-66 RX-6)", () => {
  const view = readFileSync(new URL("../CalendarView.tsx", import.meta.url), "utf-8").replace(/\r\n/g, "\n");

  it("?do=new-reminder opens the form through the one job door, and saves nothing", () => {
    expect(view).toContain('<ShellJob');
    expect(view).toContain('id="new-reminder"');
    expect(view).toContain("ready={remShown}");
    // The opener only opens the form: it calls no create.
    const open = view.slice(view.indexOf('id="new-reminder"'));
    expect(open.slice(0, open.indexOf("/>")).includes("createReminder")).toBe(false);
    expect(open).toContain('setRemForm({ mode: "new"');
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

  it("another key of the address does not open the day again (ORC-3)", () => {
    // The task card writes `?ttab=` under an open Calendar. The reader's key
    // stays the same, so its effect does not run, and the week stays.
    const before = linkQuery(q("date=2026-11-12&at=15:00"));
    expect(linkQuery(q("date=2026-11-12&ttab=files&at=15:00"))).toBe(before);
    expect(linkQuery(q("ttab=discussion"))).toBe("");
    expect(linkQuery(q("at=15:00&date=2026-11-12"))).toBe(before);
    expect(read("../components/CalendarDeepLink.tsx")).toMatch(/const query = linkQuery\(params\)/);
  });

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
    // The scroll runs again when a link arrives, not only on a mode change.
    expect(view).toMatch(/\}, \[mode, jumpAt\]\)/);
  });

  it("a change of mode or day by hand ends the link's time", () => {
    const view = read("../CalendarView.tsx");
    const body = (name: string) => {
      const from = view.indexOf(`const ${name} = (`);
      expect(from, name).toBeGreaterThan(-1);
      return view.slice(from, view.indexOf("};", from));
    };
    expect(body("pickMode")).toMatch(/setJumpAt\(null\);\s*setMode\(m\)/);
    expect(body("pickAnchor")).toMatch(/setJumpAt\(null\);\s*setAnchor\(d\)/);
    // Every hand control goes through them. Only the link sets the two
    // directly, and the month grid's pick sets the mode after pickAnchor.
    expect(view).toMatch(/onClick=\{\(\) => pickMode\(m\)\}/);
    expect(view).toMatch(/onClick=\{\(\) => pickAnchor\(startOfDay\(new Date\(\)\)\)\}/);
    expect(view).toMatch(/const step = \(dir: 1 \| -1\) =>\s*pickAnchor\(/);
    expect(view).toMatch(/pickAnchor\(d\);\s*setMode\("day"\)/);
    // The link, pickAnchor, and the day of a linked reminder (RX-2).
    expect(view.match(/setAnchor\(/g)).toHaveLength(3);
    expect(view.match(/setMode\(/g)).toHaveLength(3); // the link, pickMode, the month pick
  });
});
