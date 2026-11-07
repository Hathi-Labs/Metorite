/**
 * The Today band as it draws (PKG-18a). The runner has no DOM, so the band
 * renders to a string and the test reads the markup: the items, the reason
 * under each, and nothing at all when no item shows.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import TodayBand from "./TodayBand";
import { answerOf, card } from "./todayBandFixtures";

const draw = (cards: ReturnType<typeof card>[] | undefined, hidden: string[] = []) =>
  renderToStaticMarkup(
    createElement(TodayBand, {
      feed: { answer: cards ? answerOf(cards) : undefined, marked: new Set<string>(), hidden: new Set(hidden) },
    }),
  );

describe("TodayBand", () => {
  it("draws a Today heading, each title as a link, and its reason", () => {
    const html = draw([card(1), card(2)]);
    expect(html).toContain(">Today<");
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
