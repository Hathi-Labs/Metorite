/**
 * Handled and the honest summary, in the browser (`my_day_feed.md` §6.7,
 * §14.1a and the MD-2c ticket of §16, WS-58).
 *
 * Six claims:
 *
 *   1. `readFeedAnswer` reads `handled` and `handled_ok` defensively. An older
 *      gateway sends neither: no Handled group, and no claim. A bad entry is
 *      dropped, and a card in the group `handled` never enters `cards`.
 *   2. The client writes the words of `by`: "You", the name, the agent's
 *      name, "Metorite" and "Email rules".
 *   3. The column draws Handled FOLDED after Later, as "Handled today" and
 *      its count. A row names the entity, who closed it and the time.
 *   4. Handled never counts in the 20-card cut, the "N new" pill or the visit.
 *   5. With the feed on, "N things need you" counts the open cards of Now and
 *      Needs you in the feed answer, after the client's own hides.
 *   6. "The rest is handled." shows only when Handled is not empty, the
 *      server says `handled_ok`, and no source failed (the rule of NS-3).
 *
 * The summary with the feed flag off is `myDay.test.ts`, unchanged.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { fmtClock } from "@/app/tasks/lib/utils";

import {
  FEED_SHOWN,
  type FeedAnswer,
  type HandledEntry,
  columnView,
  feedGroups,
  feedNeedsCount,
  handledBy,
  handledClaim,
  handledMeta,
  readFeedAnswer,
} from "./feed";
import { type Feed, FeedColumn, HandledRow } from "./FeedCard";
import { summaryLine } from "./myDay";

/** A card of §14.1a in a group, with its own id and a Done. */
const card = (id: string, group: string, extra: Record<string, unknown> = {}) => ({
  signal_id: `tasks:${id}`,
  app: "tasks",
  kind: "overdue",
  folded: [],
  group,
  score: 50,
  title: `Card ${id}`,
  why: null,
  at: null,
  href: `/projects?task=${id}`,
  acts: [{ id: "done", label: "Done", class: "A", way: "tap", act: "done", act_ref: id }],
  ...extra,
});

/** An entry of §14.1a. */
const entry = (title: string, by: Record<string, unknown>, extra: Record<string, unknown> = {}) => ({
  app: "tasks",
  title,
  href: "/tasks?task=x",
  by,
  at: "2026-10-14T09:30:00+05:30",
  ...extra,
});

const HANDLED = [
  entry("Send the quote", { kind: "you", name: null }),
  entry("Archived 3 receipts", { kind: "rules", name: null }, { app: "email", href: "/email" }),
  entry("Approve the PO", { kind: "person", name: "ravi" }, { app: "approvals", href: "/approvals" }),
];

const answerOf = (raw: Record<string, unknown>): FeedAnswer => readFeedAnswer(raw);

describe("1. the reader", () => {
  it("reads the entries and the claim flag", () => {
    const answer = answerOf({ cards: [], handled: HANDLED, handled_ok: true });
    expect(answer.handled).toEqual([
      { app: "tasks", title: "Send the quote", href: "/tasks?task=x", by: { kind: "you", name: null }, at: "2026-10-14T09:30:00+05:30" },
      { app: "email", title: "Archived 3 receipts", href: "/email", by: { kind: "rules", name: null }, at: "2026-10-14T09:30:00+05:30" },
      { app: "approvals", title: "Approve the PO", href: "/approvals", by: { kind: "person", name: "ravi" }, at: "2026-10-14T09:30:00+05:30" },
    ]);
    expect(answer.handled_ok).toBe(true);
  });

  it("reads an older gateway as no Handled and no claim", () => {
    const answer = answerOf({ cards: [card("a", "needs_you")], sources: { tasks: "ok" } });
    expect(answer.handled).toEqual([]);
    expect(answer.handled_ok).toBe(false);
    expect(handledClaim(answer)).toBe(false);
    // A field of the wrong type reads the same way.
    const odd = answerOf({ handled: "three", handled_ok: "yes" });
    expect(odd.handled).toEqual([]);
    expect(odd.handled_ok).toBe(false);
  });

  it("drops each entry it cannot draw, and keeps the rest", () => {
    const good = entry("Kept", { kind: "agent", name: "Jev" });
    const answer = answerOf({
      handled: [
        null,
        "a string",
        entry("", { kind: "you", name: null }),
        { ...good, title: 7 },
        { ...good, href: undefined },
        // Only an in-app path is a link: never another site, never a script.
        { ...good, href: "https://evil.example/x" },
        { ...good, href: "//evil.example/x" },
        { ...good, href: "javascript:alert(1)" },
        { ...good, by: undefined },
        { ...good, by: { kind: "robot", name: "R2" } },
        { ...good, app: 3 },
        good,
      ],
      handled_ok: true,
    });
    expect(answer.handled.map((e) => e.title)).toEqual(["Kept"]);
  });

  it("reads a time it cannot use as none, and a name it cannot use as none", () => {
    const [e] = answerOf({ handled: [entry("T", { kind: "person", name: 4 }, { at: 12 })] }).handled;
    expect(e.at).toBeNull();
    expect(e.by).toEqual({ kind: "person", name: null });
  });

  it("never puts a card of the group `handled` in `cards` or `groups`", () => {
    const answer = answerOf({
      cards: [card("a", "needs_you"), card("h", "handled")],
      groups: [
        { id: "needs_you", count: 1 },
        { id: "handled", count: 1 },
      ],
    });
    expect(answer.cards.map((c) => c.signal_id)).toEqual(["tasks:a"]);
    expect(answer.groups.map((g) => g.id)).toEqual(["needs_you"]);
  });
});

describe("2. the words of `by`", () => {
  it("writes one word for each kind of the `by` table", () => {
    expect(handledBy({ kind: "you", name: null })).toBe("You");
    expect(handledBy({ kind: "person", name: "ravi" })).toBe("ravi");
    expect(handledBy({ kind: "agent", name: "Jev" })).toBe("Jev");
    expect(handledBy({ kind: "system", name: null })).toBe("Metorite");
    expect(handledBy({ kind: "rules", name: null })).toBe("Email rules");
    // A name the server left out: the bell's own word, and never a blank.
    expect(handledBy({ kind: "person", name: null })).toBe("Somebody");
    expect(handledBy({ kind: "agent", name: null })).toBe("An agent");
  });

  it("writes who closed it, then the time, in the card's own clock", () => {
    const now = new Date(2026, 9, 14, 12, 0).getTime();
    const at = new Date(2026, 9, 14, 9, 30);
    const e: HandledEntry = { app: "tasks", title: "T", href: "/tasks", by: { kind: "you", name: null }, at: at.toISOString() };
    expect(handledMeta(e, now)).toBe(`You · ${fmtClock(at)}`);
    expect(handledMeta({ ...e, at: null }, now)).toBe("You");
    // Before the client clock exists, no time.
    expect(handledMeta(e, null)).toBe("You");
  });
});

/** A feed with this answer and nothing hidden. */
const feedOf = (answer: FeedAnswer | undefined, extra: Partial<Feed> = {}): Feed => ({
  answer,
  loading: false,
  error: null,
  refresh: () => {},
  marked: new Set(),
  errors: {},
  act: () => {},
  hidden: new Set(),
  menuErrors: {},
  menu: () => {},
  seen: () => {},
  ...extra,
});

const column = (answer: FeedAnswer) =>
  renderToStaticMarkup(createElement(FeedColumn, { feed: feedOf(answer), now: null }));

describe("3. the Handled group", () => {
  it("draws Handled folded, after Later, with its count", () => {
    const html = column(
      answerOf({
        cards: [card("a", "needs_you"), card("l", "later")],
        groups: [
          { id: "needs_you", count: 1 },
          { id: "later", count: 1 },
        ],
        handled: HANDLED,
        handled_ok: true,
      }),
    );
    expect(html).toMatch(/aria-expanded="false"[^>]*>[\s\S]*?Handled today[\s\S]*?data-feed-handled-count="">3</);
    expect(html.indexOf('data-feed-group="handled"')).toBeGreaterThan(html.indexOf('data-feed-group="later"'));
    // Folded: none of its rows until it opens.
    expect(html).not.toContain("Send the quote");
    expect(html).not.toContain("data-feed-handled-row");
  });

  it("draws no Handled group when the list is empty, or the gateway sent none", () => {
    expect(column(answerOf({ cards: [card("a", "needs_you")], handled: [], handled_ok: true }))).not.toContain(
      'data-feed-group="handled"',
    );
    expect(column(answerOf({ cards: [card("a", "needs_you")] }))).not.toContain("Handled today");
  });

  it("draws Handled on a day with no open card too", () => {
    const html = column(answerOf({ cards: [], handled: HANDLED, handled_ok: true, sources: { tasks: "ok" } }));
    expect(html).toContain("Nothing needs you right now.");
    expect(html).toContain('data-feed-group="handled"');
  });

  it("names the entity as a link, who closed it, and the time", () => {
    const at = new Date(2026, 9, 14, 9, 30);
    const now = new Date(2026, 9, 14, 12, 0).getTime();
    const draw = (e: HandledEntry) => renderToStaticMarkup(createElement(HandledRow, { entry: e, nowMs: now }));
    const html = draw({ app: "approvals", title: "Approve the PO", href: "/approvals", by: { kind: "person", name: "ravi" }, at: at.toISOString() });
    expect(html).toMatch(/<a\b[^>]*href="\/approvals"/);
    expect(html).toContain("Approve the PO");
    expect(html).toContain(`ravi · ${fmtClock(at)}`);
    const rules = draw({ app: "email", title: "Receipts", href: "/email", by: { kind: "rules", name: null }, at: null });
    expect(rules).toContain("Email rules");
  });
});

describe("4. Handled never counts in the cut, the pill or the visit", () => {
  const twenty = Array.from({ length: FEED_SHOWN }, (_, i) => card(`c${i}`, "needs_you"));
  const many = Array.from({ length: 15 }, (_, i) => entry(`Closed ${i}`, { kind: "you", name: null }));

  it("leaves the 20-card cut whole", () => {
    const answer = answerOf({
      cards: [...twenty, card("h", "handled")],
      groups: [{ id: "needs_you", count: 20 }],
      handled: many,
      handled_ok: true,
    });
    const { groups, hidden } = feedGroups(answer, FEED_SHOWN);
    expect(hidden).toBe(0);
    expect(groups.map((g) => [g.id, g.cards.length])).toEqual([["needs_you", 20]]);
    const html = column(answer);
    expect(html).not.toContain("more</");
    expect(html).toContain("You are caught up.");
  });

  it("brings no pill and moves no card of the visit", () => {
    const first = answerOf({ cards: twenty.slice(0, 2), groups: [{ id: "needs_you", count: 2 }] });
    const opts = { shown: FEED_SHOWN, marked: new Set<string>(), hidden: new Set<string>() };
    const start = columnView(first, null, opts).visit;
    const next = answerOf({ cards: twenty.slice(0, 2), groups: [{ id: "needs_you", count: 2 }], handled: many, handled_ok: true });
    const view = columnView(next, start, opts);
    expect(view.fresh).toBe(0);
    expect(view.hidden).toBe(0);
    expect(view.groups.map((g) => g.id)).toEqual(["needs_you"]);
    expect([...view.visit!.place.keys()]).toEqual(["tasks:c0", "tasks:c1"]);
  });
});

describe("5. the summary counts the feed", () => {
  const answer = answerOf({
    cards: [
      card("n1", "now"),
      card("y1", "needs_you"),
      card("y2", "needs_you"),
      card("r1", "ready"),
      card("f1", "fyi"),
      card("l1", "later"),
    ],
    groups: [],
    handled: HANDLED,
    handled_ok: true,
  });

  it("counts the open cards of Now and Needs you, and nothing else", () => {
    expect(feedNeedsCount(answer, new Set(), new Set())).toBe(3);
  });

  it("counts after the client's own hides: an act, and a menu act", () => {
    // A Done marks the task's needs row (`tasks:<id>`), and the card leaves.
    expect(feedNeedsCount(answer, new Set(["tasks:y1"]), new Set())).toBe(2);
    // A snooze hides the card by its id, and the bell still counts it.
    expect(feedNeedsCount(answer, new Set(), new Set(["tasks:n1"]))).toBe(2);
    expect(feedNeedsCount(answer, new Set(["tasks:y1"]), new Set(["tasks:n1", "tasks:f1"]))).toBe(1);
  });

  it("counts a card once, whatever the answer repeats", () => {
    const twice = answerOf({ cards: [card("y1", "needs_you"), card("y1", "needs_you")] });
    expect(feedNeedsCount(twice, new Set(), new Set())).toBe(1);
  });
});

describe("6. the claim", () => {
  const base = { cards: [card("y1", "needs_you")], sources: { tasks: "ok", email: "ok", approvals: "absent", calendar: "ok" } };

  it("holds only when Handled is not empty, the server says ok, and no source failed", () => {
    expect(handledClaim(answerOf({ ...base, handled: HANDLED, handled_ok: true }))).toBe(true);
    expect(handledClaim(answerOf({ ...base, handled: [], handled_ok: true }))).toBe(false);
    expect(handledClaim(answerOf({ ...base, handled: HANDLED, handled_ok: false }))).toBe(false);
    expect(handledClaim(answerOf({ ...base, handled: HANDLED }))).toBe(false);
    // A failed source silences the claim (the rule of NS-3), Calendar too.
    for (const failed of ["tasks", "email", "projects", "approvals", "calendar"]) {
      const sources = { ...base.sources, [failed]: "failed" };
      expect(handledClaim(answerOf({ ...base, sources, handled: HANDLED, handled_ok: true })), failed).toBe(false);
    }
  });

  it("writes the claim after a count of needs, and never with the caveat", () => {
    expect(summaryLine(3, null, false, true)).toBe("3 things need you. The rest is handled.");
    expect(summaryLine(1, 2, false, true)).toBe("1 thing needs you, and 2 are on your calendar today. The rest is handled.");
    // A failed source: the count stays a fact, and the claim goes.
    expect(summaryLine(3, null, true, true)).toBe("3 things need you.");
    // Nothing needs the member: that sentence is the whole truth already.
    expect(summaryLine(0, null, false, true)).toBe("Nothing needs you right now.");
    expect(summaryLine(0, 0, false, true)).toBe("Nothing needs you right now, and your calendar is clear.");
    // With no claim, the line of NS-3, word for word.
    expect(summaryLine(3, null, false, false)).toBe(summaryLine(3, null));
  });
});
