/**
 * `todayBand` as data (`package_host.md` §9.3, PKG-18a): at most five items,
 * in feed order (Now, then Needs you), each with a reason. A done or hidden
 * card never shows, a reminder card never shows, and a card with no `why`
 * never shows. The items hold for the visit (PKG-Q15), and one gate decides
 * whether My Day draws the band.
 *
 * Mutations, run by hand (2026-11-07):
 *   - `bandStep` ignores the hold and computes again on each poll: "a later
 *     answer adds no item" fails;
 *   - `eligible` keeps a reminder card: "leaves out the reminder cards" fails;
 *   - `showTodayBand` ignores `flagOn`: "draws only with the flag" fails;
 *   - `MyDayPage.tsx` draws `<TodayBand` with no gate: "My Day reads the one
 *     gate" fails.
 *   See `arrangement.test.ts` and `todayBandRender.test.ts` for the others.
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { BAND_LIMIT, type BandHold, bandStep, todayBand } from "./todayBand";
import { showTodayBand } from "./todayBandFlag";
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

describe("the reminders", () => {
  it("leaves out the reminder cards, which the Reminders band draws", () => {
    const items = todayBand(
      answerOf([
        card(1, { kind: "reminder_due", signal_id: "reminders:r1" }),
        card(2, { kind: "reminder_check", signal_id: "reminders:r2", group: "needs_you" }),
        card(3),
      ]),
      none,
      none,
    );
    expect(items.map((i) => i.signalId)).toEqual(["tasks:t3"]);
  });
});

describe("one item per signal id", () => {
  it("shows a card that the answer repeats once", () => {
    const items = todayBand(answerOf([card(1), card(1, { title: "Again" }), card(2)]), none, none);
    expect(items.map((i) => i.signalId)).toEqual(["tasks:t1", "tasks:t2"]);
  });
});

describe("the hold (PKG-Q15: stable within a visit)", () => {
  const first = answerOf([card(3), card(4), card(5)]);
  // A poll: a new high card in Now, and the order of the old cards changed.
  const later = answerOf([card(1), card(5), card(2, { group: "needs_you" }), card(4), card(3, { title: "Card 3 new" })]);

  it("starts with the first answer that holds a card", () => {
    expect(bandStep(null, undefined, none, none)).toEqual({ hold: null, items: [] });
    expect(bandStep(null, answerOf([]), none, none)).toEqual({ hold: null, items: [] });
    const s = bandStep(null, first, none, none);
    expect(s.hold).toEqual({ ids: ["tasks:t3", "tasks:t4", "tasks:t5"] });
  });

  it("a later answer adds no item and reorders none", () => {
    const { hold } = bandStep(null, first, none, none);
    const next = bandStep(hold, later, none, none);
    expect(next.hold).toBe(hold);
    expect(next.items.map((i) => i.signalId)).toEqual(["tasks:t3", "tasks:t4", "tasks:t5"]);
    // A held item takes the new content of its card.
    expect(next.items[0].title).toBe("Card 3 new");
  });

  it("drops an item at once when the member marks it done or hides it", () => {
    const done = (n: number) =>
      card(n, { kind: "overdue", acts: [{ id: "done", label: "Done", way: "tap", act: "done", act_ref: `t${n}` }] });
    const a = answerOf([done(3), card(4), card(5)]);
    const { hold } = bandStep(null, a, none, none);
    const marked = bandStep(hold, a, new Set(["tasks:t3"]), none);
    expect(marked.items.map((i) => i.signalId)).toEqual(["tasks:t4", "tasks:t5"]);
    const hidden = bandStep(hold, a, none, new Set(["tasks:t4"]));
    expect(hidden.items.map((i) => i.signalId)).toEqual(["tasks:t3", "tasks:t5"]);
  });

  it("drops an item whose card left the answer, and lets no new card in its place", () => {
    const { hold } = bandStep(null, first, none, none);
    const next = bandStep(hold, answerOf([card(1), card(4), card(5)]), none, none);
    expect(next.items.map((i) => i.signalId)).toEqual(["tasks:t4", "tasks:t5"]);
  });

  it("a reload, a new mount with no hold, computes the items again", () => {
    const { hold } = bandStep(null, first, none, none);
    expect(bandStep(hold, later, none, none).items.map((i) => i.signalId)).toEqual(["tasks:t3", "tasks:t4", "tasks:t5"]);
    const reload = bandStep(null, later, none, none);
    expect(reload.items.map((i) => i.signalId)).toEqual(["tasks:t1", "tasks:t5", "tasks:t4", "tasks:t3", "tasks:t2"]);
  });

  it("holds an empty band too, when the first answer gives no item", () => {
    const { hold } = bandStep(null, answerOf([card(1, { group: "fyi" })]), none, none);
    expect(hold).toEqual({ ids: [] } satisfies BandHold);
    expect(bandStep(hold, later, none, none).items).toEqual([]);
  });
});

describe("the gate", () => {
  it("draws only with the flag, the feed and the needs card all on", () => {
    expect(showTodayBand(true, true, true)).toBe(true);
    expect(showTodayBand(false, true, true)).toBe(false);
    expect(showTodayBand(true, false, true)).toBe(false);
    expect(showTodayBand(true, true, false)).toBe(false);
  });

  it("My Day reads the one gate, and draws the band only in the feed branch", () => {
    const here = dirname(fileURLToPath(import.meta.url));
    const page = readFileSync(join(here, "MyDayPage.tsx"), "utf8");
    const draws = page.match(/<TodayBand\b/g) ?? [];
    expect(draws).toHaveLength(1);
    expect(page).toMatch(/\{showTodayBand\(bandOn, feedOn, cards\.needs\) \? <TodayBand\b/);
    const branch = page.indexOf(") : feedOn ? (");
    const at = page.indexOf("<TodayBand");
    expect(branch).toBeGreaterThan(0);
    expect(at).toBeGreaterThan(branch);
    // The band sits before the next branch of the ternary, the cards layout.
    expect(at).toBeLessThan(page.indexOf(") : (", branch));
  });
});
