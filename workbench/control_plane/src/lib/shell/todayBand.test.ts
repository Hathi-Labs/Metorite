/**
 * `todayBand` as data (`package_host.md` §9.3, PKG-18a): at most five items,
 * in feed order (Now, then Needs you), each with a reason. A done or hidden
 * card never shows, and a card with no `why` never shows.
 *
 * Mutations, run by hand (2026-11-07): see `arrangement.test.ts`.
 */
import { describe, expect, it } from "vitest";

import { BAND_LIMIT, todayBand } from "./todayBand";
import { answerOf, card } from "./todayBandFixtures";

const none = new Set<string>();

describe("todayBand", () => {
  it("shows at most five items by default", () => {
    const items = todayBand(answerOf([1, 2, 3, 4, 5, 6, 7].map((n) => card(n))), none, none);
    expect(BAND_LIMIT).toBe(5);
    expect(items).toHaveLength(5);
  });

  it("takes the limit as an argument", () => {
    expect(todayBand(answerOf([1, 2, 3].map((n) => card(n))), none, none, 2)).toHaveLength(2);
  });

  it("keeps feed order, Now before Needs you", () => {
    const items = todayBand(
      answerOf([card(1, { group: "needs_you" }), card(2), card(3, { group: "needs_you" }), card(4)]),
      none,
      none,
    );
    expect(items.map((i) => i.title)).toEqual(["Card 2", "Card 4", "Card 1", "Card 3"]);
  });

  it("gives each item its card's reason, link and app", () => {
    const [item] = todayBand(answerOf([card(1)]), none, none);
    expect(item).toEqual({
      signalId: "tasks:t1",
      app: "tasks",
      title: "Card 1",
      href: "/projects?task=1",
      reason: "Reason 1",
    });
  });

  it("drops a hidden card", () => {
    const items = todayBand(answerOf([card(1), card(2)]), none, new Set(["tasks:t1"]));
    expect(items.map((i) => i.signalId)).toEqual(["tasks:t2"]);
  });

  it("drops a card that a done act closed", () => {
    const done = card(1, { kind: "overdue", acts: [{ id: "done", label: "Done", way: "tap", act: "done", act_ref: "t1" }] });
    const items = todayBand(answerOf([done, card(2)]), new Set(["tasks:t1"]), none);
    expect(items.map((i) => i.signalId)).toEqual(["tasks:t2"]);
  });

  it("skips a card with no reason, so no item lacks one", () => {
    const items = todayBand(
      answerOf([card(1, { why: null }), card(2, { why: "" }), card(3, { why: "  " }), card(4)]),
      none,
      none,
    );
    expect(items.map((i) => i.signalId)).toEqual(["tasks:t4"]);
    expect(items.every((i) => i.reason.length > 0)).toBe(true);
  });

  it("leaves out the groups that are not Now or Needs you", () => {
    const items = todayBand(answerOf([card(1, { group: "ready" }), card(2, { group: "fyi" }), card(3)]), none, none);
    expect(items.map((i) => i.signalId)).toEqual(["tasks:t3"]);
  });

  it("is empty for no answer and for an empty answer", () => {
    expect(todayBand(undefined, none, none)).toEqual([]);
    expect(todayBand(answerOf([]), none, none)).toEqual([]);
  });
});
