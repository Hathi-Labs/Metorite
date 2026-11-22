/**
 * The Do first band as it draws (PKG-18a). The runner has no DOM, so the band
 * renders to a string and the test reads the markup: the items, the reason
 * under each, the wrap of the text, and nothing at all when no item shows.
 *
 * Mutations, run by hand (2026-11-07):
 *   - take `max-sm:break-words` or `min-w-0` out of `BAND_WRAP`: "wraps the
 *     title and the reason" fails;
 *   - draw the reason with no `BAND_WRAP`: the same test fails;
 *   - `TodayBand` calls `todayBand` with no hold: the hold fence fails;
 *   - My Day gives the band no visit number, or the column no `onTakeNew`:
 *     "My Day shares the column's visit number" fails.
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import TodayBand from "./TodayBand";
import { BAND_HEADING, BAND_WRAP } from "./todayBand";
import { answerOf, card } from "./todayBandFixtures";

const draw = (cards: ReturnType<typeof card>[] | undefined, hidden: string[] = []) =>
  renderToStaticMarkup(
    createElement(TodayBand, {
      feed: { answer: cards ? answerOf(cards) : undefined, marked: new Set<string>(), hidden: new Set(hidden) },
    }),
  );

describe("TodayBand", () => {
  it("draws the Do first heading, each title as a link, and its reason", () => {
    const html = draw([card(1), card(2)]);
    expect(BAND_HEADING).toBe("Do first");
    expect(html).toContain(`>${BAND_HEADING}<`);
    expect(html).not.toContain(">Today<");
    expect(html).toContain('data-testid="today-band"');
    expect(html).toContain('href="/projects?task=1"');
    expect(html).toContain("Card 2");
    expect(html).toContain("Reason 1");
    expect((html.match(/data-today-band-item=""/g) ?? []).length).toBe(2);
    expect((html.match(/data-today-band-why=""/g) ?? []).length).toBe(2);
  });

  it("draws five items at most", () => {
    const html = draw([1, 2, 3, 4, 5, 6].map((n) => card(n)));
    expect((html.match(/data-today-band-item=""/g) ?? []).length).toBe(5);
    expect(html).not.toContain("Card 6");
  });

  it("draws a title with no link when the card has no door", () => {
    const html = draw([card(1, { href: "" })]);
    expect(html).toContain("Card 1");
    expect(html).not.toContain("<a ");
  });

  it("draws nothing with no answer, no card, only reasonless cards, or all hidden", () => {
    expect(draw(undefined)).toBe("");
    expect(draw([])).toBe("");
    expect(draw([card(1, { why: null })])).toBe("");
    expect(draw([card(1)], ["tasks:t1"])).toBe("");
  });
});

describe("TodayBand wrap", () => {
  const long = "x".repeat(200);
  const classesOf = (html: string, marker: string): string[] =>
    [...html.matchAll(new RegExp(`<[a-z]+[^>]*class="([^"]*)"[^>]*${marker}[^>]*>`, "g"))].map((m) => m[1]);

  it("uses the wrap of the column's title", () => {
    // The column's title (`FeedCard.tsx`) carries the same classes.
    for (const c of ["min-w-0", "max-sm:line-clamp-2", "max-sm:break-words", "sm:truncate"]) {
      expect(BAND_WRAP.split(" ")).toContain(c);
    }
  });

  it("wraps the title and the reason, with a door and without one", () => {
    for (const href of ["/projects?task=1", ""]) {
      const html = draw([card(1, { title: long, why: long, href })]);
      const titles = classesOf(html, 'data-today-band-title=""');
      expect(titles).toHaveLength(1);
      for (const c of BAND_WRAP.split(" ")) expect(titles[0].split(" ")).toContain(c);
      // The reason is the span after the icon in the why line.
      const why = html.slice(html.indexOf('data-today-band-why=""'));
      const reason = why.match(new RegExp(`<span class="([^"]*)">${long}</span>`));
      expect(reason?.[1]).toBe(BAND_WRAP);
      expect(classesOf(html, 'data-today-band-why=""')[0].split(" ")).toContain("min-w-0");
    }
  });
});

describe("TodayBand hold", () => {
  it("keeps the hold in state and draws the items of bandStep", () => {
    const here = dirname(fileURLToPath(import.meta.url));
    const code = readFileSync(join(here, "TodayBand.tsx"), "utf8");
    expect(code).toMatch(/useState<BandHold \| null>\(null\)/);
    expect(code).toMatch(
      /bandStep\(hold, feed\.answer, feed\.marked, feed\.hidden, \{ fresh: !feed\.refreshing, visit \}\)/,
    );
    expect(code).toMatch(/if \(step\.hold !== hold\) setHold\(step\.hold\)/);
    expect(code).not.toMatch(/\btodayBand\(/);
  });
});

describe("the visit number", () => {
  it("My Day shares the column's visit number with the band", () => {
    const here = dirname(fileURLToPath(import.meta.url));
    const page = readFileSync(join(here, "MyDayPage.tsx"), "utf8");
    expect(page).toMatch(/<TodayBand feed=\{feed\} visit=\{feedVisit\}/);
    expect(page).toMatch(/onTakeNew=\{\(\) => setFeedVisit\(\(n\) => n \+ 1\)\}/);
    const column = readFileSync(join(here, "FeedCard.tsx"), "utf8");
    expect(column).toMatch(/const takeNew = \(\) => \{\s*setVisit\([^;]+;\s*onTakeNew\?\.\(\);/);
  });
});
