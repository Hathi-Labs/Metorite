/**
 * WS-62 CF-4 (`chat_first_surface.md` §4.6): the starter pills of the empty
 * chat, as one pure rule. Each branch of `starterPills` has a case here. The
 * source block in `lib/shell/assistant.test.ts` pins that `AgentChat` reads
 * the rule, and only with the flag on. `e2e/chat-starter-pills.spec.ts` sees
 * it in a browser.
 */
import { describe, expect, it } from "vitest";

import { STARTER_PILL_CHARS, STARTER_PILL_COUNT, starterPills, trimHeadline } from "./starterPills";

const FIXED = ["What can you help me with?", "Summarize recent activity", "Create a new task"];

const card = (title: string) => ({ title });

const FEED = [
  card("3 tasks are due today"),
  card("Reply to Priya Nair"),
  card("The Acme deal has not moved in 9 days"),
  card("Standup starts in 20 minutes"),
  card("Ravi is away on Friday"),
];

describe("starterPills", () => {
  it("shows three pills, in the feed's order, with the flag on", () => {
    expect(STARTER_PILL_COUNT).toBe(3);
    expect(starterPills({ chatFirst: true, cards: FEED, fallback: FIXED })).toEqual([
      "3 tasks are due today",
      "Reply to Priya Nair",
      "The Acme deal has not moved in 9 days",
    ]);
  });

  it("shows the fixed list with the flag off, whatever the feed holds", () => {
    expect(starterPills({ chatFirst: false, cards: FEED, fallback: FIXED })).toEqual(FIXED);
  });

  it("shows the fixed list while the feed loads, after it failed, and when it is empty", () => {
    expect(starterPills({ chatFirst: true, cards: undefined, fallback: FIXED })).toEqual(FIXED);
    expect(starterPills({ chatFirst: true, cards: null, fallback: FIXED })).toEqual(FIXED);
    expect(starterPills({ chatFirst: true, cards: [], fallback: FIXED })).toEqual(FIXED);
  });

  it("gives fewer pills for fewer cards, never a filler from the fixed list", () => {
    expect(starterPills({ chatFirst: true, cards: FEED.slice(0, 1), fallback: FIXED })).toEqual(["3 tasks are due today"]);
    expect(starterPills({ chatFirst: true, cards: FEED.slice(0, 2), fallback: FIXED })).toEqual([
      "3 tasks are due today",
      "Reply to Priya Nair",
    ]);
  });

  it("drops a repeated headline, case folded, and takes the next card", () => {
    const cards = [card("Reply to Priya Nair"), card("reply to Priya Nair"), card("Reply to Priya Nair "), ...FEED];
    expect(starterPills({ chatFirst: true, cards, fallback: FIXED })).toEqual([
      "Reply to Priya Nair",
      "3 tasks are due today",
      "The Acme deal has not moved in 9 days",
    ]);
  });

  it("skips a card with no headline, and falls back when none is left", () => {
    expect(starterPills({ chatFirst: true, cards: [card(""), card("   "), card("Reply to Priya Nair")], fallback: FIXED })).toEqual([
      "Reply to Priya Nair",
    ]);
    expect(starterPills({ chatFirst: true, cards: [card(""), card(" \n ")], fallback: FIXED })).toEqual(FIXED);
    // A card a gateway sent with no `title` field at all.
    const broken = [{ title: undefined as unknown as string }, card("Reply to Priya Nair")];
    expect(starterPills({ chatFirst: true, cards: broken, fallback: FIXED })).toEqual(["Reply to Priya Nair"]);
  });

  it("trims a long headline at a word, to about 60 characters", () => {
    const long = card("The quarterly review of every open deal in the pipeline needs your sign-off before Friday afternoon");
    const [pill] = starterPills({ chatFirst: true, cards: [long], fallback: FIXED });
    expect(pill.endsWith("…")).toBe(true);
    expect(pill.length).toBeLessThanOrEqual(STARTER_PILL_CHARS + 1);
    // Cut at a word: the text before the ellipsis is a prefix that ends on a whole word.
    const head = pill.slice(0, -1);
    expect(long.title.startsWith(head)).toBe(true);
    expect(long.title.charAt(head.length)).toBe(" ");
  });

  it("returns the fallback as a copy, so a caller cannot change the fixed list", () => {
    const out = starterPills({ chatFirst: false, cards: FEED, fallback: FIXED });
    expect(out).not.toBe(FIXED);
    out.push("x");
    expect(FIXED).toHaveLength(3);
  });
});

describe("trimHeadline", () => {
  it("keeps a short headline whole, with its whitespace folded", () => {
    expect(trimHeadline("3 tasks are due today")).toBe("3 tasks are due today");
    expect(trimHeadline("  Reply   to\nPriya Nair  ")).toBe("Reply to Priya Nair");
    expect(trimHeadline("x".repeat(STARTER_PILL_CHARS))).toBe("x".repeat(STARTER_PILL_CHARS));
  });

  it("cuts at the last word inside the room, and drops the punctuation before the ellipsis", () => {
    expect(trimHeadline("one two three four five, six seven", 20)).toBe("one two three four…");
    expect(trimHeadline("Reply to Priya Nair about the invoice, today.", 30)).toBe("Reply to Priya Nair about the…");
  });

  it("cuts one long word at the room's edge, so it still gives a pill", () => {
    expect(trimHeadline("a".repeat(80), 20)).toBe(`${"a".repeat(20)}…`);
    // A word break that would leave less than half the room is not taken.
    expect(trimHeadline(`ab ${"c".repeat(40)}`, 20)).toBe(`ab ${"c".repeat(17)}…`);
  });

  it("gives nothing for a headline that is only punctuation past the room", () => {
    expect(trimHeadline(".".repeat(80), 20)).toBe("");
    expect(trimHeadline("")).toBe("");
  });
});
