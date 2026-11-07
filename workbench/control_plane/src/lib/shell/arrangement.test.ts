/**
 * The R7 fence of `package_host.md` §14: a stable region moves only on a
 * member action (PKG-18a). Three checks on the Today band.
 *
 * What this proves, and what it does not. It proves the BAND moves nothing,
 * by construction: the band is a pure function of the feed answer, the two
 * band files import nothing that writes the arrangement, and the sidebar and
 * card order stay equal across two different feed answers. It does not prove
 * that no other code moves a stable region. PKG-19 and PKG-17 add their own
 * checks beside these.
 *
 * Mutations, run by hand (2026-11-07):
 *   - `todayBand` ignores `limit`: "shows at most five items" and "the band
 *     changes only its items" fail;
 *   - `todayBand` keeps a card with no `why`: "skips a card with no reason"
 *     fails;
 *   - `TodayBand.tsx` imports `saveShellPrefs` from `./shellPrefs`: "imports
 *     nothing that writes the arrangement" fails.
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { NAV_SECTIONS } from "@/lib/nav";

import { cardOrderFor } from "./myDay";
import { BUILT_CARDS } from "./presets";
import { homePane, shellSidebar } from "./shellNav";
import { todayBand } from "./todayBand";
import { answerOf, card } from "./todayBandFixtures";

const here = dirname(fileURLToPath(import.meta.url));
const source = (f: string) =>
  // Comments may name what the file must not do, so the scan reads code only.
  readFileSync(join(here, f), "utf8")
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/(^|[^:])\/\/.*$/gm, "$1");

const none = new Set<string>();
const feedA = answerOf([1, 2, 3, 4, 5, 6, 7].map((n) => card(n)));
const feedB = answerOf([card(9, { group: "needs_you" }), card(8, { why: null }), card(7)]);

describe("check 1: the band is a pure function of the feed answer", () => {
  it("gives equal output for equal input", () => {
    expect(todayBand(feedA, none, none)).toEqual(todayBand(feedA, none, none));
  });

  it("changes only its items when the feed answer changes", () => {
    const a = todayBand(feedA, none, none);
    const b = todayBand(feedB, none, none);
    expect(a).not.toEqual(b);
    // The input is not touched: the same answer gives the same band after.
    expect(todayBand(feedA, none, none)).toEqual(a);
    expect(feedA.cards).toHaveLength(7);
    expect(a.length).toBeLessThanOrEqual(5);
    expect(b.map((i) => i.signalId)).toEqual(["tasks:t7", "tasks:t9"]);
  });
});

describe("check 2: the band code imports nothing that writes the arrangement", () => {
  const FILES = ["todayBand.ts", "TodayBand.tsx"];
  const FORBIDDEN: [string, RegExp][] = [
    ["the shell prefs route", /api\/auth\/me\/shell|SHELL_PREFS_PATH/],
    ["a shell prefs module", /["']\.\/shellPrefs["']|["']\.\/shellCache["']|["']\.\/sidebarFolds["']/],
    ["a shell prefs setter", /\b(saveShellPrefs|markTourSeen|markChatFirstMessage|markAsked|setHelloPending|markBuddyHello|writeFolds|clearSidebarFolds)\b/],
    ["the sidebar order", /["']\.\/shellNav["']|\bshellSidebar\b|\bstarredHrefs\b/],
    ["a browser store", /\b(localStorage|sessionStorage)\b/],
    ["a network write", /\bfetch\s*\(|\bmethod\s*:\s*["'](PUT|POST|PATCH|DELETE)["']/],
  ];
  for (const file of FILES) {
    it(`${file} imports nothing that writes the arrangement`, () => {
      const code = source(file);
      for (const [what, re] of FORBIDDEN) expect(code, `${file}: ${what}`).not.toMatch(re);
    });
  }
});

describe("check 3: the stable regions are equal before and after the band", () => {
  const shell = { enabled: true, layout: { cards: ["today", "needs", "next"] } };
  const snapshot = () => ({
    sidebar: shellSidebar(NAV_SECTIONS, null, false).map((s) => ({
      id: s.id,
      hrefs: s.items.map((p) => p.href),
    })),
    cards: cardOrderFor(BUILT_CARDS, shell),
    home: homePane(true),
  });

  it("keeps the sidebar section order, the card order and the home pane", () => {
    const before = snapshot();
    todayBand(feedA, none, none);
    todayBand(feedB, none, none);
    const after = snapshot();
    expect(after).toEqual(before);
    expect(before.sidebar.length).toBeGreaterThan(0);
    expect(before.cards).toEqual(["today", "needs", "next"]);
  });
});
