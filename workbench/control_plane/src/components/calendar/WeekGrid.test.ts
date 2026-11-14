/**
 * STU-D5-W2 fence: the read-only Day and Week grid. The vitest env has no DOM, so the grid draws
 * from props with `renderToStaticMarkup`. All times are local, so the tests hold in any zone.
 */
import { readFileSync } from "node:fs";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { EMPTY_SENTENCE, WeekGrid } from "./WeekGrid";
import type { GridEvent } from "./types";

const L = (d: number, h = 0, mi = 0) => new Date(2026, 10, d, h, mi);
const ev = (id: string, s: Date, e: Date, allDay = false): GridEvent => ({
  id, title: id, start: s, end: e, allDay, tone: "primary",
});
// Monday 2026-11-09 to Sunday 2026-11-15. The clock reads Wednesday 2026-11-11 10:30.
const ANCHOR = L(12);
const NOW = L(11, 10, 30);
const EVENTS = [
  ev("alpha", L(11, 9), L(11, 10, 30)),
  ev("beta", L(11, 9, 30), L(11, 10)),
  ev("trip", L(10), L(12), true),
];
const draw = (over: Record<string, unknown> = {}) =>
  renderToStaticMarkup(createElement(WeekGrid, { events: EVENTS, mode: "week", anchor: ANCHOR, now: NOW, ...over }));
const count = (html: string, re: RegExp) => (html.match(re) ?? []).length;

describe("WeekGrid: the frame", () => {
  it("draws seven day columns in a week and one in a day", () => {
    expect(count(draw(), /data-column="/g)).toBe(7);
    expect(count(draw({ mode: "day" }), /data-column="/g)).toBe(1);
    expect(draw({ mode: "day" })).toContain('data-column="2026-11-12"');
  });

  it("draws a sticky header with the day names and dates, and marks today", () => {
    const html = draw();
    expect(html).toContain("sticky top-0 z-10");
    expect(html).toContain("relative z-0 flex");
    for (const name of ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]) expect(html).toContain(`>${name}<`);
    expect(count(html, /aria-current="date"/g)).toBe(1);
    expect(html).toMatch(/aria-current="date"[^>]*>11</);
    expect(count(draw({ now: L(20) }), /aria-current="date"/g)).toBe(0);
  });

  it("draws the hour gutter with the hour names and one line for each hour", () => {
    const html = draw();
    for (const label of ["7 AM", "12 PM", "9 PM"]) expect(html).toContain(`>${label}<`);
    // 15 hours (7 to 22) in each of 7 columns.
    expect(count(html, /border-b border-border\/60/g)).toBe(15 * 7);
  });
});

describe("WeekGrid: the blocks", () => {
  it("places a timed block at the top and the height of the model", () => {
    const html = draw();
    // 9:00 is 2 hours into the window at 46 px, and 1.5 hours is 69 px less 2.
    expect(html).toMatch(/data-block="alpha"[^>]*style="top:92px;height:67px;/);
  });

  it("gives two overlapping blocks a lane each, as percentages", () => {
    const html = draw();
    expect(html).toMatch(/data-block="alpha"[^>]*left:calc\(0% \+ 1px\);width:calc\(50% - 2px\)/);
    expect(html).toMatch(/data-block="beta"[^>]*left:calc\(50% \+ 1px\);width:calc\(50% - 2px\)/);
  });

  it("draws the all-day bars across their columns", () => {
    const html = draw();
    expect(html).toContain("data-all-day");
    expect(html).toMatch(/title="trip"[^>]*grid-column:2 \/ span 2/);
    expect(draw({ events: [EVENTS[0]] })).not.toContain("data-all-day");
  });

  it("squares the end of a bar that goes on past the week", () => {
    const html = draw({ events: [ev("long", L(14), L(18), true)] });
    expect(html).toMatch(/rounded-r-none[^"]*[^>]*title="long"|title="long"[^>]*rounded-r-none/);
  });

  it("makes a chip a link only when the caller gives an address", () => {
    const html = draw({ hrefOf: (e: GridEvent) => `?ro=visit&rec=${e.id}` });
    expect(html).toContain('<a href="?ro=visit&amp;rec=alpha"');
    expect(draw()).not.toContain("<a ");
  });

  it("shows the title and the time range, and lets the caller replace them", () => {
    expect(draw()).toContain("9:00 am – 10:30 am");
    const html = draw({ renderEvent: (e: GridEvent) => createElement("i", null, `custom-${e.id}`) });
    expect(html).toContain("custom-alpha");
  });
});

describe("WeekGrid: the now line", () => {
  it("draws the line once, in the column of today", () => {
    const html = draw();
    expect(count(html, /data-now-line/g)).toBe(1);
    const col = html.slice(html.indexOf('data-column="2026-11-11"'));
    expect(col.indexOf("data-now-line")).toBeGreaterThan(-1);
    expect(col.indexOf("data-now-line")).toBeLessThan(col.indexOf('data-column="2026-11-12"'));
    // 10:30 is 3.5 hours into the window.
    expect(html).toContain("top:161px");
  });

  it("draws no line when now is outside the window or outside the days", () => {
    expect(draw({ now: L(20, 10) })).not.toContain("data-now-line");
    expect(draw({ now: L(11, 3) })).not.toContain("data-now-line");
  });

  it("draws no line in a day view of another day", () => {
    expect(draw({ mode: "day", anchor: L(13) })).not.toContain("data-now-line");
    expect(count(draw({ mode: "day", anchor: L(11) }), /data-now-line/g)).toBe(1);
  });
});

describe("WeekGrid: chips", () => {
  const many = [ev("a", L(11, 9), L(11, 10)), ev("b", L(11, 9), L(11, 10)), ev("c", L(11, 9), L(11, 10))];
  it("hides an hour line behind an opaque wrapper of the card token", () => {
    expect(draw()).toMatch(/data-block="alpha" class="absolute rounded bg-card"/);
  });
  it("keeps the time line in a wide lane and drops it in a narrow lane", () => {
    expect(draw({ mode: "day", anchor: L(11), events: [EVENTS[0]] })).toContain("9:00 am – 10:30 am");
    const narrow = draw({ mode: "week", events: many });
    expect(narrow).not.toContain("9:00 am – 10:00 am</span>");
    expect(narrow).toContain('title="a, 9:00 am – 10:00 am"');
    expect(draw({ mode: "day", anchor: L(11), events: many.slice(0, 2) })).toContain("9:00 am – 10:00 am</span>");
    expect(draw({ mode: "day", anchor: L(11), events: many })).not.toContain("10:00 am</span>");
  });
  it("shows one line and no time for a block under 30 px", () => {
    const html = draw({ mode: "day", anchor: L(11), events: [ev("quick", L(11, 9), L(11, 9, 5))] });
    expect(html).toContain('title="quick, 9:00 am – 9:05 am"');
    expect(html).not.toContain("9:05 am</span>");
  });
  it("gives the body room above the first hour label", () => {
    expect(draw()).toContain('class="pt-2"');
  });
});

describe("WeekGrid: the empty state", () => {
  it("says that nothing is scheduled when no event is given", () => {
    expect(EMPTY_SENTENCE).toBe("Nothing is scheduled in this period.");
    // The block draws the sentence above the scroll box. The grid does not draw it.
    expect(draw({ events: [] })).not.toContain(EMPTY_SENTENCE);
  });
});

describe("WeekGrid: source pins", () => {
  const src = readFileSync(new URL("./WeekGrid.tsx", import.meta.url), "utf-8");
  it("uses no hex colour, no lucide import and no raw HTML", () => {
    expect(src).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    expect(src).not.toContain("lucide-react");
    expect(src).not.toContain("dangerouslySetInnerHTML");
  });
  it("hand-rolls no control: no raw button and no key handler", () => {
    expect(src).not.toMatch(/<button\b/);
    expect(src).not.toContain("onKeyDown");
  });
  it("defines no HOUR_PX and takes its colours from the Month grid", () => {
    expect(src).not.toMatch(/\bHOUR_PX\s*=/);
    expect(src).toMatch(/import\s*\{[^}]*\bTONE\b[^}]*\}\s*from "\.\/MonthGrid"/);
  });
});
