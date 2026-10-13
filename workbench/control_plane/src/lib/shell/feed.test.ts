/**
 * My Day's live feed in the browser (`my_day_feed.md` §14.1 and §14.1a,
 * WS-57 MD-1b).
 *
 * Seven claims:
 *
 *   1. An answer in the shape of §14.1a reads whole, and an odd card is
 *      dropped, never a throw.
 *   2. The groups come in the order of `groups`, and the cards keep the
 *      server's order inside each group. The page shows 20 cards at a time.
 *   3. The feed answer fills the needs cache key when a network answer lands,
 *      and never with an answer from before a write.
 *   4. A `tap` act runs through the owning app's code (`runAct`), as the
 *      needs row of the needs view. An `open` act is a link.
 *   5. Every mark keys on the needs rows an act closes, in the one store
 *      `needsActs.ts`. A card comes back with a live signal, and a mark ends
 *      by the store's rules (fix round 1, cases a to d).
 *   6. One client: only `feed.ts` fetches `/api/shell/feed`, and nothing
 *      writes the needs key from an effect.
 *   7. The feed's poll covers the needs key only after a read that held a
 *      needs view (fix round 1, P2).
 *
 * Mutations, run by hand (2026-10-13):
 *   - drop `if (answer.needs) putNeeds(answer.needs);`: "fills the needs key" fails;
 *   - reverse the cards inside a group in `feedGroups`: "keeps the server's order" fails;
 *   - make `needsRowFor` take the card id: "the needs row of a folded notification" fails;
 *   - make `cardHidden` ignore the folded kinds: cases (a) and (b) fail;
 *   - make `coversNeeds` always true: both P2 path tests fail;
 *   - make `feedHiddenRows` ignore the overlay: F1 (i), (ii) and (iv) fail;
 *   - make every needs mark hide, overlay or not: F1 (iii) fails;
 *   - drop the "blocked" guard of `runFeedAct`: F1 (ii) fails;
 *   - make `joinFeedPoll` always cover: the F3 test fails.
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * The My Tasks store's gesture, shaped like the real one: it hides the row,
 * then calls `keep` once the write begins. "parent" asks, and the answer is no.
 * While `hydrate.gate` is set, `keep` waits for it: the cold hydrate of the
 * first Done of a page session (fix round 3).
 */
type Row = { hide(): void; show(): void; keep?(): void };
const hydrate: { gate: Promise<void> | null; open: () => void } = { gate: null, open: () => {} };
const markDoneFromHome = vi.fn(async (id: string, row?: Row) => {
  row?.hide();
  if (id === "parent") {
    row?.show();
    return "kept";
  }
  if (hydrate.gate) await hydrate.gate;
  row?.keep?.();
  return "done";
});
vi.mock("@/app/tasks/lib/completeFromHome", () => ({
  markDoneFromHome: (id: string, row?: Row) => markDoneFromHome(id, row),
}));

import { PROJECTS_CACHE } from "@/app/projects/lib/api";
import { cacheKey, clearAll, invalidate, peek, read } from "@/lib/dataCache";
import { applyData, applyError, initialState } from "@/lib/useCachedResource";

import {
  FEED_CACHE,
  FEED_SHOWN,
  PENDING,
  type ActOverlay,
  type FeedAnswer,
  type FeedCard as Card,
  actLeavesCard,
  actRows,
  cardHidden,
  coversNeeds,
  drawnActs,
  feedAppIcon,
  feedFailedLines,
  feedGroups,
  feedHiddenRows,
  feedKey,
  fetchFeed,
  needsRowFor,
  overlayAdd,
  overlayExpired,
  overlayNextEnd,
  readFeedAnswer,
  sourceLine,
} from "./feed";
import {
  type FeedActDeps,
  FeedCard,
  OVERLAY_MIN_WAIT_MS,
  expireFeedActs,
  feedActMover,
  joinFeedPoll,
  runFeedAct,
  watchOverlayEnds,
} from "./FeedCard";
import { needsKey, runAct } from "./needs";
import { ACT_HOLD_MS, acquireReader, actsSnapshot, pruneActs, removeRow } from "./needsActs";
import { BELL_POLL_MS, joinNeedsPoll } from "./ShellBell";

const read_ = (rel: string) => readFileSync(new URL(rel, import.meta.url), "utf8");
/** Source with comments removed, so prose about a call is not a call. */
const code = (text: string) => text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(?<![:"'/\\])\/\/[^\n]*/g, "");

/** The card of §14.1a. */
const CARD = {
  signal_id: "tasks:9b2e",
  app: "tasks",
  kind: "overdue",
  folded: ["block_soon"],
  group: "needs_you",
  score: 87,
  entity: { type: "task", id: "9b2e", label: "Send the Q3 BOM" },
  title: "Send the Q3 BOM to purchase",
  why: "Due Monday. 2 days late.",
  why_key: "days_late",
  facts: { due_at: "2026-10-12T17:00:00+05:30", days_late: 2 },
  fingerprint: "a41f09c2",
  urgent_at: null,
  expires_at: null,
  at: "2026-10-12T17:00:00+05:30",
  href: "/projects?task=9b2e",
  acts: [
    { id: "done", label: "Done", class: "A", way: "tap", act: "done", act_ref: "9b2e" },
    { id: "open-day", label: "Open the day", class: "A", way: "open", href: "/calendar" },
  ],
};

const NEEDS_VIEW = {
  count: 1,
  total: 1,
  items: [
    {
      id: "tasks:9b2e",
      app: "tasks",
      kind: "overdue",
      title: "Send the Q3 BOM to purchase",
      detail: null,
      href: "/projects?task=9b2e",
      at: "2026-10-12T17:00:00+05:30",
      act: "done",
      act_ref: "9b2e",
    },
  ],
  sources: { tasks: "ok", projects: "ok" },
  totals: { tasks: 1 },
  capped: [],
};

const ANSWER = {
  cards: [CARD],
  groups: [{ id: "needs_you", count: 1 }],
  needs: NEEDS_VIEW,
  sources: { tasks: "ok", projects: "ok", email: "absent", approvals: "absent", calendar: "ok" },
  capped: [],
};

/** A card in a group, with its own id. */
const card = (id: string, group: string, extra: Record<string, unknown> = {}): Card =>
  readFeedAnswer({ cards: [{ ...CARD, signal_id: id, group, title: `Card ${id}`, ...extra }] }).cards[0];

afterEach(() => {
  vi.unstubAllGlobals();
  markDoneFromHome.mockClear();
  hydrate.gate = null;
  clearAll();
});

describe("1. the answer of §14.1a", () => {
  it("reads the whole answer", () => {
    const answer = readFeedAnswer(ANSWER);
    expect(answer.cards).toHaveLength(1);
    const c = answer.cards[0];
    expect(c).toMatchObject({
      signal_id: "tasks:9b2e",
      app: "tasks",
      kind: "overdue",
      folded: ["block_soon"],
      group: "needs_you",
      title: "Send the Q3 BOM to purchase",
      why: "Due Monday. 2 days late.",
      href: "/projects?task=9b2e",
    });
    expect(c.acts.map((a) => a.way)).toEqual(["tap", "open"]);
    expect(answer.groups).toEqual([{ id: "needs_you", count: 1 }]);
    expect(answer.needs?.items.map((i) => i.id)).toEqual(["tasks:9b2e"]);
    expect(answer.sources.calendar).toBe("ok");
  });

  it("reads a missing field as empty, and drops a card it cannot draw", () => {
    expect(readFeedAnswer(undefined)).toEqual({
      cards: [],
      groups: [],
      needs: null,
      sources: {},
      capped: [],
      fetchedAt: 0,
    });
    const answer = readFeedAnswer({
      cards: [CARD, { ...CARD, signal_id: 7 }, { ...CARD, group: "someday" }, { ...CARD, href: undefined }],
      groups: [{ id: "needs_you", count: 1 }, { id: "someday", count: 1 }],
    });
    expect(answer.cards.map((c) => c.signal_id)).toEqual(["tasks:9b2e"]);
    expect(answer.groups.map((g) => g.id)).toEqual(["needs_you"]);
    expect(answer.needs).toBeNull();
  });

  it("names Calendar, which the needs view does not know", () => {
    expect(sourceLine({ app: "calendar", kind: "block_soon" })).toBe("Calendar · Starts soon");
    expect(sourceLine({ app: "tasks", kind: "overdue" })).toBe("My Tasks · Overdue");
    expect(sourceLine({ app: "email", kind: "something_new" })).toBe("Email");
    expect(feedAppIcon("calendar")).toBe("Calendar");
    expect(feedFailedLines({ calendar: "failed", email: "failed" })).toEqual([
      "Email did not answer, so its replies may be missing.",
      "Calendar did not answer, so its time blocks may be missing.",
    ]);
  });

  it("puts the key in the Projects family, so a Done drops it with the needs key", () => {
    expect(FEED_CACHE.startsWith(PROJECTS_CACHE)).toBe(true);
    expect(feedKey()).toBe(cacheKey(FEED_CACHE, { limit: 40 }));
  });
});

describe("2. the groups and the cut", () => {
  it("draws the groups in the order of `groups`, and keeps the server's order inside each", () => {
    const answer = {
      cards: [card("a", "needs_you"), card("b", "now"), card("c", "needs_you"), card("d", "fyi"), card("e", "now")],
      groups: [
        { id: "now" as const, count: 2 },
        { id: "needs_you" as const, count: 2 },
        { id: "fyi" as const, count: 1 },
      ],
    };
    const { groups, hidden } = feedGroups(answer, FEED_SHOWN);
    expect(groups.map((g) => [g.label, g.cards.map((c) => c.signal_id)])).toEqual([
      ["Now", ["b", "e"]],
      ["Needs you", ["a", "c"]],
      ["For your information", ["d"]],
    ]);
    expect(hidden).toBe(0);
  });

  it("takes the server's group order even when it is not the usual one", () => {
    const answer = {
      cards: [card("a", "now"), card("b", "needs_you")],
      groups: [
        { id: "needs_you" as const, count: 1 },
        { id: "now" as const, count: 1 },
      ],
    };
    expect(feedGroups(answer, FEED_SHOWN).groups.map((g) => g.id)).toEqual(["needs_you", "now"]);
  });

  it("shows 20 cards at a time, cut over the whole feed and not per group", () => {
    const cards = Array.from({ length: 33 }, (_, i) => card(`c${i}`, i < 25 ? "needs_you" : "fyi"));
    const answer = { cards, groups: [{ id: "needs_you" as const, count: 25 }, { id: "fyi" as const, count: 8 }] };
    const first = feedGroups(answer, FEED_SHOWN);
    expect(FEED_SHOWN).toBe(20);
    expect(first.groups.map((g) => g.cards.length)).toEqual([20]);
    expect(first.hidden).toBe(13);
    const next = feedGroups(answer, FEED_SHOWN * 2);
    expect(next.groups.map((g) => g.cards.length)).toEqual([25, 8]);
    expect(next.hidden).toBe(0);
  });

  it("leaves out a card an act took away, and holds a group missing from `groups`", () => {
    const doneOn = (ref: string) => ({ acts: [{ id: "done", label: "Done", way: "tap", act: "done", act_ref: ref }] });
    const answer = {
      cards: [card("a", "now", doneOn("a")), card("b", "fyi", doneOn("b"))],
      groups: [{ id: "fyi" as const, count: 1 }],
    };
    // The mark is the needs row of b's Done, never the card's id.
    const { groups } = feedGroups(answer, FEED_SHOWN, new Set(["tasks:b"]));
    expect(groups.map((g) => [g.id, g.cards.map((c) => c.signal_id)])).toEqual([["now", ["a"]]]);
  });
});

describe("3. the feed answer fills the needs key", () => {
  function stubFetch(body: unknown, status = 200) {
    const urls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        urls.push(String(input));
        return new Response(JSON.stringify(body), { status });
      }),
    );
    return urls;
  }

  it("fills the needs key with the needs view when a network answer lands", async () => {
    const urls = stubFetch(ANSWER);
    const answer = await read(feedKey(), () => fetchFeed());
    expect(urls).toEqual(["/api/shell/feed?limit=40"]);
    expect(answer.cards).toHaveLength(1);
    expect(peek(needsKey())?.data).toEqual(NEEDS_VIEW);
  });

  it("writes nothing to the needs key from an answer with no needs view", async () => {
    stubFetch({ ...ANSWER, needs: undefined });
    await read(feedKey(), () => fetchFeed());
    expect(peek(needsKey())).toBeUndefined();
  });

  it("throws on a refusal, so the column can say so", async () => {
    stubFetch({ error: "feed_unavailable" }, 502);
    await expect(fetchFeed()).rejects.toThrow("Your feed could not load.");
    expect(peek(needsKey())).toBeUndefined();
  });

  it("never stores a needs view from before a Done", async () => {
    let release: () => void = () => {};
    let calls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        calls += 1;
        if (calls === 1) await new Promise<void>((res) => (release = res));
        const needs = calls === 1 ? NEEDS_VIEW : { ...NEEDS_VIEW, count: 0, total: 0, items: [] };
        return new Response(JSON.stringify({ ...ANSWER, needs }), { status: 200 });
      }),
    );
    const pending = read(feedKey(), () => fetchFeed());
    await Promise.resolve();
    invalidate(PROJECTS_CACHE); // a Done, while the feed read is in flight
    release();
    await pending;
    // The answer from before the Done held the task. Only the one after it
    // reached the needs key.
    expect(peek(needsKey())?.data).toMatchObject({ count: 0, items: [] });
  });

  it("is the fetcher that writes it, never an effect on the feed's data", () => {
    const feed = code(read_("./feed.ts"));
    expect(feed).toMatch(/const putNeeds = guardedPut<NeedsFeed>\(feedKey\(limit\), needsKey\(\)\);/);
    const card = code(read_("./FeedCard.tsx"));
    expect(card).not.toMatch(/\bput\(|guardedPut|needsKey\(\)\s*,\s*readFeed/);
    expect(card).toMatch(/useCachedResource<FeedAnswer>\(enabled \? feedKey\(\) : null, \(\) => fetchFeed\(\)\)/);
    // The one poll: the feed's refresh owns its key, and covers the needs
    // key only by `coversNeeds` (section 7).
    expect(card).toMatch(/const covers = coversNeeds\(feed\.data, feed\.error\);/);
    expect(card).toMatch(/joinNeedsPoll\(refresh, feedKey\(\), covers \? \[needsKey\(\)\] : \[\]\)/);
  });
});

describe("4. the acts go through the owning app", () => {
  const folded = card("tasks:t1", "needs_you", {
    kind: "overdue",
    acts: [
      { id: "done", label: "Done", class: "A", way: "tap", act: "done", act_ref: "t1" },
      { id: "read", label: "Mark read", class: "A", way: "tap", act: "read", act_ref: "n7" },
    ],
  });

  it("draws a tap act only with its act and ref, an open act only with its link, and no expand act", () => {
    const c = card("x", "now", {
      acts: [
        { id: "a", label: "Done", way: "tap", act: "done", act_ref: "x" },
        { id: "b", label: "Done", way: "tap", act: "done" },
        { id: "c", label: "Archive", way: "tap", act: "archive", act_ref: "x" },
        { id: "d", label: "Send", way: "expand" },
        { id: "e", label: "Open", way: "open", href: "/email" },
        { id: "f", label: "Open", way: "open" },
        { id: "g", label: "Teleport", way: "warp" },
      ],
    });
    expect(drawnActs(c).map((a) => a.id)).toEqual(["a", "e"]);
  });

  it("runs a done as the needs row of the task, through the My Tasks store's gesture", async () => {
    const c = readFeedAnswer(ANSWER).cards[0];
    const item = needsRowFor(c, c.acts[0]);
    expect(item).toMatchObject({ id: "tasks:9b2e", app: "tasks", act: "done", act_ref: "9b2e" });
    const log: string[] = [];
    const row = { hide: () => log.push("hide"), show: () => log.push("show"), fail: () => log.push("fail"), keep: () => {} };
    expect(await runAct(item!, row)).toBe("done");
    expect(markDoneFromHome.mock.calls[0][0]).toBe("9b2e");
  });

  it("names the needs row of a folded notification, not the card", async () => {
    const item = needsRowFor(folded, folded.acts[1]);
    expect(item).toMatchObject({ id: "projects:n7", app: "projects", act: "read", act_ref: "n7" });
    const calls: { url: string; body: unknown }[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        calls.push({ url: String(input), body: init?.body ? JSON.parse(String(init.body)) : null });
        return new Response("{}", { status: 200 });
      }),
    );
    const row = { hide: () => {}, show: () => {}, fail: () => {}, keep: () => {} };
    expect(await runAct(item!, row)).toBe("done");
    // The Projects bell's own route.
    expect(calls).toEqual([{ url: "/api/projects/notifications/read", body: { ids: ["n7"] } }]);
  });

  it("gives no needs row to an open act", () => {
    const c = readFeedAnswer(ANSWER).cards[0];
    expect(needsRowFor(c, c.acts[1])).toBeNull();
  });

  it("marks EVERY note of a merged read: one post, and each row leaves the bell", async () => {
    const merged = card("tasks:t5", "needs_you", {
      kind: "notification",
      acts: [{ id: "read", label: "Mark read", way: "tap", act: "read", act_ref: "n9", act_refs: ["n9", "n8"] }],
    });
    const a = merged.acts[0];
    expect(actRows(a)).toEqual(["projects:n9", "projects:n8"]);
    const item = needsRowFor(merged, a);
    expect(item).toMatchObject({ id: "projects:n9", act: "read", act_ref: "n9", act_refs: ["n9", "n8"] });
    const calls: unknown[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
        calls.push(init?.body ? JSON.parse(String(init.body)) : null);
        return new Response("{}", { status: 200 });
      }),
    );
    const release = acquireReader();
    try {
      expect(await runAct(item!, feedActMover(actRows(a)))).toBe("done");
      expect(calls).toEqual([{ ids: ["n9", "n8"] }]);
      expect([...actsSnapshot().removed].sort()).toEqual(["projects:n8", "projects:n9"]);
    } finally {
      release();
    }
  });

  it("reads a read with no act_refs as [act_ref], and a done by its act_ref only", () => {
    expect(actRows({ way: "tap", act: "read", act_ref: "n1" })).toEqual(["projects:n1"]);
    expect(actRows({ way: "tap", act: "done", act_ref: "t1", act_refs: ["x"] })).toEqual(["tasks:t1"]);
  });

  it("puts every row back, with the error, when the read fails", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 500 })));
    const release = acquireReader();
    try {
      const raw = { id: "read", label: "Mark read", way: "tap", act: "read", act_ref: "n9", act_refs: ["n9", "n8"] };
      const c = card("tasks:t5", "needs_you", { kind: "notification", acts: [raw] });
      const item = needsRowFor(c, c.acts[0]);
      expect(await runAct(item!, feedActMover(actRows(c.acts[0])))).toBe("failed");
      expect(actsSnapshot().removed.size).toBe(0);
      expect(actsSnapshot().errors["projects:n9"]).toBe("Could not mark it read. Try again.");
    } finally {
      release();
    }
  });

  it("wires each act through runAct and the one store's marks", () => {
    const src = code(read_("./FeedCard.tsx"));
    // The hook runs each act through `runFeedAct`, which runs `runAct` with
    // the row half that marks every needs row (sections 5 and 5b drive it).
    expect(src).toMatch(/void runFeedAct\(card, a, from, \{/);
    expect(src).toMatch(/const mover = feedActMover\(rows, from, now\);/);
    // A row is PENDING from the hide until `keep` (fix round 3).
    expect(src).toMatch(/deps\.overlay\(\(o\) => overlayAdd\(o, rows, PENDING\)\);/);
    expect(src).toMatch(/const out = await runAct\(item, row\);/);
    expect(src).toMatch(/for \(const row of rows\) removeRow\(row, gone, now\(\)\);/);
    // The feed keeps no private removed set: it reads the one store.
    expect(src).toMatch(/useSyncExternalStore\(subscribeActs, actsSnapshot, \(\) => EMPTY_ACTS\)/);
    expect(src).not.toMatch(/keepRemoved|useState<ReadonlySet<string>>/);
    // No second act path: the owning app's code runs only through runAct.
    expect(src).not.toMatch(/markDoneFromHome|notificationsApi/);
    // Its one mention of the My Tasks module is a type.
    expect(src.match(/completeFromHome/g)).toEqual(["completeFromHome"]);
    expect(src).toContain('import type { DoneRow } from "@/app/tasks/lib/completeFromHome";');
  });

  it("draws a tap act as a Button and an open act as a link, in the HomeCard frame", () => {
    const c = readFeedAnswer(ANSWER).cards[0];
    const html = renderToStaticMarkup(
      createElement(FeedCard, { card: c, nowMs: Date.parse("2026-10-14T12:00:00+05:30"), onAct: () => {} }),
    );
    expect(html).toContain('data-home-card=""');
    // The headline is the door.
    expect(html).toMatch(/<h2[^>]*><a[^>]*href="\/projects\?task=9b2e"[^>]*>Send the Q3 BOM to purchase<\/a><\/h2>/);
    expect(html).toContain("My Tasks · Overdue");
    expect(html).toContain("Due 2d ago");
    expect(html).toContain("Due Monday. 2 days late.");
    // The tap act is the house Button, marked as a row act.
    expect(html).toMatch(/<button[^>]*data-row-act=""[^>]*aria-label="Done: Send the Q3 BOM to purchase"/);
    expect(html).toContain("cc-control");
    // The open act is a link.
    expect(html).toMatch(/<a\b(?=[^>]*data-feed-open="")(?=[^>]*href="\/calendar")[^>]*>/);
    expect(html.match(/<button\b/g)).toHaveLength(1);
  });
});

describe("5. what an act takes away (fix round 1)", () => {
  // The page reads its marks from the one store, as `useFeed` does, and
  // draws the cards that `feedGroups` keeps.
  let release: () => void = () => {};
  beforeEach(() => {
    release = acquireReader();
  });
  afterEach(() => release());

  const marked = () => actsSnapshot().removed;
  const shownIds = (cards: Card[]) =>
    feedGroups({ cards, groups: [] }, FEED_SHOWN, marked()).groups.flatMap((g) => g.cards.map((c) => c.signal_id));
  /** Mark an act's rows the way the hook does. */
  const tap = (c: Card, id: string, at = Date.now()) => {
    for (const row of actRows(c.acts.find((a) => a.id === id)!)) removeRow(row, true, at);
  };
  const draw = (c: Card) =>
    renderToStaticMarkup(createElement(FeedCard, { card: c, nowMs: null, marked: marked(), onAct: () => {} }));
  const done = (ref: string) => ({ id: "done", label: "Done", way: "tap", act: "done", act_ref: ref });
  const readAct = (...refs: string[]) => ({
    id: "read",
    label: "Mark read",
    way: "tap",
    act: "read",
    act_ref: refs[0],
    act_refs: refs,
  });
  const openDay = { id: "open-day", label: "Open the day", way: "open", href: "/calendar" };

  it("a Done takes a plain task card away at once, and a Done in the bell does too", () => {
    const plain = card("tasks:t0", "needs_you", { kind: "overdue", folded: [], acts: [done("t0")] });
    tap(plain, "done");
    expect(shownIds([plain])).toEqual([]);
    // The §14.1a card (a block folded in): the bell's Done marks the same row.
    const c = readFeedAnswer(ANSWER).cards[0];
    expect(shownIds([c])).toEqual(["tasks:9b2e"]);
    removeRow("tasks:9b2e", true);
    expect(shownIds([c])).toEqual([]);
  });

  it("(a) a Done on a task with an unread note keeps the note, and the note's card comes back", () => {
    const before = card("tasks:t1", "needs_you", {
      kind: "overdue",
      folded: ["notification"],
      acts: [done("t1"), readAct("n7")],
    });
    expect(actLeavesCard(before, before.acts[0])).toBe(false);
    tap(before, "done");
    // The card stays, because the note still needs the member. Its Done is spent.
    expect(shownIds([before])).toEqual(["tasks:t1"]);
    expect(draw(before)).not.toContain('aria-label="Done:');
    expect(draw(before)).toContain("Mark read");
    // The next answer: the task is done, and the note is live. Its bell row is not marked.
    const after = card("tasks:t1", "needs_you", { kind: "notification", folded: [], acts: [readAct("n7")] });
    expect(shownIds([after])).toEqual(["tasks:t1"]);
    expect(marked().has("projects:n7")).toBe(false);
  });

  it("(b) a Mark read on a note card with a block that starts soon keeps the live block", () => {
    const note = card("tasks:t2", "now", { kind: "notification", folded: ["block_soon"], acts: [readAct("n1"), openDay] });
    expect(actLeavesCard(note, note.acts[0])).toBe(false);
    tap(note, "read");
    expect(shownIds([note])).toEqual(["tasks:t2"]);
    const block = card("tasks:t2", "now", { kind: "block_soon", folded: [], acts: [openDay] });
    expect(shownIds([block])).toEqual(["tasks:t2"]);
    // A note card with nothing folded does leave.
    const lone = card("tasks:t6", "fyi", { kind: "notification", folded: [], acts: [readAct("n5")] });
    expect(actLeavesCard(lone, lone.acts[0])).toBe(true);
    tap(lone, "read");
    expect(shownIds([lone])).toEqual([]);
  });

  it("(c) a new note on the same task shows its read act and its card again", () => {
    const first = card("tasks:t3", "needs_you", {
      kind: "overdue",
      folded: ["notification"],
      acts: [done("t3"), readAct("n1")],
    });
    tap(first, "read");
    expect(draw(first)).not.toContain("Mark read");
    // n2 arrives: the merged read names n2 and n1, and n2 is not marked.
    const next = card("tasks:t3", "needs_you", {
      kind: "overdue",
      folded: ["notification"],
      acts: [done("t3"), readAct("n2", "n1")],
    });
    expect(draw(next)).toContain("Mark read");
    const noteOnly = card("tasks:t7", "needs_you", { kind: "notification", folded: [], acts: [readAct("n1")] });
    tap(noteOnly, "read");
    expect(shownIds([noteOnly])).toEqual([]);
    const again = card("tasks:t7", "needs_you", { kind: "notification", folded: [], acts: [readAct("n2", "n1")] });
    expect(shownIds([again])).toEqual(["tasks:t7"]);
  });

  it("hides by the needs rows, never by a card id", () => {
    const c = card("tasks:t8", "now", { kind: "overdue", folded: [], acts: [done("t8")] });
    expect(cardHidden(c, new Set(["tasks:t8#done"]))).toBe(false);
    expect(cardHidden(c, new Set(["tasks:t8"]))).toBe(true);
  });
});

describe("5b. the feed's own acts outlive a needs answer (fix round 2, F1)", () => {
  // The needs read answers before the feed read (4 s, no Calendar). The
  // feed's overlay ends against the FEED answer, so a needs answer cannot
  // bring a done card back while the feed answer on screen predates the act.
  let release: () => void = () => {};
  beforeEach(() => {
    release = acquireReader();
  });
  afterEach(() => release());

  const T_FETCH = 1_000;
  const T_ACT = 2_000;
  const doneOn = (ref: string) => ({ id: "done", label: "Done", way: "tap", act: "done", act_ref: ref });
  const taskCard = (ref: string) => ({ ...CARD, signal_id: `tasks:${ref}`, title: `Task ${ref}`, folded: [], acts: [doneOn(ref)] });

  /** The page, as `useFeed` runs it: the answer on screen, the overlay and the clock. */
  function page(cards: unknown[], fetchedAt = T_FETCH) {
    const state = {
      answer: { ...readFeedAnswer({ cards, groups: [{ id: "needs_you", count: cards.length }] }), fetchedAt },
      overlay: new Map() as ActOverlay,
      now: T_ACT,
    };
    const deps: FeedActDeps = {
      held: () => feedHiddenRows(actsSnapshot().removed, state.overlay, state.answer.fetchedAt),
      overlay: (change) => {
        state.overlay = change(state.overlay);
      },
      now: () => state.now,
    };
    const shown = () =>
      feedGroups(state.answer, 80, deps.held()).groups.flatMap((g) => g.cards.map((c) => c.signal_id));
    const tapDone = (i = 0) => runFeedAct(state.answer.cards[i], state.answer.cards[i].acts[0], null, deps);
    return { state, deps, shown, tapDone };
  }

  it("(i) a needs answer without the row, while the feed is still stale, keeps the card hidden", async () => {
    const p = page([taskCard("t1")]);
    expect(await p.tapDone()).toBe("done");
    expect(p.shown()).toEqual([]);
    pruneActs([]); // the needs read answers first, without the row
    expect(actsSnapshot().removed.has("tasks:t1")).toBe(false);
    expect(p.shown()).toEqual([]);
  });

  it("(ii) a feed re-read that fails keeps it hidden for the hold, and the act cannot run twice", async () => {
    const p = page([taskCard("t1")]);
    await p.tapDone();
    pruneActs([]);
    // The feed's re-read fails, so the answer on screen stays the stale one.
    p.state.now = T_ACT + ACT_HOLD_MS - 1;
    expect(await p.tapDone()).toBe("blocked");
    expect(markDoneFromHome).toHaveBeenCalledTimes(1);
    expect(p.shown()).toEqual([]);
    // The hold ends: the card shows again, and its Done runs again.
    p.state.overlay = expireFeedActs(p.state.overlay, T_ACT + ACT_HOLD_MS);
    expect(p.state.overlay.size).toBe(0);
    expect(p.shown()).toEqual(["tasks:t1"]);
    expect(await p.tapDone()).toBe("done");
    expect(markDoneFromHome).toHaveBeenCalledTimes(2);
  });

  it("(iii) a feed answer fetched after the act that still holds the task shows it: really reopened", async () => {
    const p = page([taskCard("t1")]);
    await p.tapDone();
    // The needs view still holds the row inside its hold, so its mark stays.
    pruneActs(["tasks:t1"], T_ACT + 500);
    expect(actsSnapshot().removed.has("tasks:t1")).toBe(true);
    // The feed answer from after the act is the truth.
    p.state.answer = { ...p.state.answer, fetchedAt: T_ACT + 1_000 };
    expect(p.shown()).toEqual(["tasks:t1"]);
    const html = renderToStaticMarkup(
      createElement(FeedCard, { card: p.state.answer.cards[0], nowMs: null, marked: p.deps.held(), onAct: () => {} }),
    );
    expect(html).toContain('aria-label="Done: Task t1"');
  });

  it("(iv) a card outside the needs top 30 keeps its hidden state", async () => {
    const cards = Array.from({ length: 40 }, (_, i) => taskCard(`c${i}`));
    const p = page(cards);
    expect(await p.tapDone(35)).toBe("done");
    // The needs view holds only the first 30 rows, never `tasks:c35`.
    pruneActs(cards.slice(0, 30).map((c) => c.signal_id));
    expect(p.shown()).toHaveLength(39);
    expect(p.shown()).not.toContain("tasks:c35");
  });

  it("(v) a feed answer with the task, between hide and keep (a cold hydrate), keeps the card hidden", async () => {
    // Fix round 3, item 1. The first Done of a page session hydrates the
    // store before it writes, so `keep` comes late. A feed read lands in
    // that window, fetched after the tap, and it still holds the task.
    hydrate.gate = new Promise<void>((open) => (hydrate.open = open));
    const p = page([taskCard("t1")]);
    const first = p.tapDone();
    p.state.answer = { ...p.state.answer, fetchedAt: T_ACT + 1_500 };
    p.state.now = T_ACT + 1_500;
    expect(p.shown()).toEqual([]);
    expect(await p.tapDone()).toBe("blocked");
    expect(markDoneFromHome).toHaveBeenCalledTimes(1);
    // The write begins: the overlay takes its real time from `keep`.
    p.state.now = T_ACT + 2_000;
    hydrate.open();
    expect(await first).toBe("done");
    expect(p.state.overlay.get("tasks:t1")).toBe(T_ACT + 2_000);
    expect(p.shown()).toEqual([]);
    // Only an answer fetched after the write began is the truth.
    p.state.answer = { ...p.state.answer, fetchedAt: T_ACT + 2_500 };
    expect(p.shown()).toEqual(["tasks:t1"]);
  });

  it("a pending row has no hold to count down, and never expires on its own", () => {
    const o = overlayAdd(new Map(), ["tasks:p"], PENDING);
    expect(overlayNextEnd(o, T_ACT)).toBeNull();
    expect(overlayExpired(o, Number.MAX_SAFE_INTEGER)).toEqual([]);
    expect(feedHiddenRows(new Set(), o, Number.MAX_SAFE_INTEGER)).toEqual(new Set(["tasks:p"]));
  });

  it("re-books a pass when the timer fires early, so a row never stays for the life of the page", () => {
    // Fix round 3, item 2. Node may fire a timer 1 ms early, or the clock
    // may step back. The pass then expires nothing, and it must book again.
    vi.useFakeTimers();
    let clock = 0;
    let o: ActOverlay = overlayAdd(new Map(), ["tasks:a"], 0);
    const stop = watchOverlayEnds(
      () => o,
      (now) => {
        o = expireFeedActs(o, now);
      },
      () => clock,
    );
    try {
      clock = ACT_HOLD_MS - 1; // the timer fires, and the clock reads 1 ms short
      vi.advanceTimersByTime(ACT_HOLD_MS);
      expect(o.has("tasks:a")).toBe(true);
      clock = ACT_HOLD_MS - 1 + OVERLAY_MIN_WAIT_MS;
      vi.advanceTimersByTime(OVERLAY_MIN_WAIT_MS);
      expect(o.size).toBe(0);
    } finally {
      stop();
      vi.useRealTimers();
    }
  });

  it("the end of the feed's act keeps a NEWER needs mark that the bell set on the same row", () => {
    // Fix round 3, item 3.
    const o = overlayAdd(new Map(), ["tasks:a", "tasks:b"], 1_000);
    removeRow("tasks:a", true, 900); // the feed's own mark, from its hide
    removeRow("tasks:b", true, 5_000); // the bell's Done, after the feed's act
    const left = expireFeedActs(o, 1_000 + ACT_HOLD_MS);
    expect(left.size).toBe(0);
    expect(actsSnapshot().removed.has("tasks:a")).toBe(false);
    expect(actsSnapshot().removed.has("tasks:b")).toBe(true);
  });

  it("(d) a Done that asks and gets no shows the card again at once", async () => {
    const p = page([taskCard("parent")]);
    expect(await p.tapDone()).toBe("kept");
    expect(p.state.overlay.size).toBe(0);
    expect(p.shown()).toEqual(["tasks:parent"]);
  });

  it("the overlay counts down from the last time of the act", () => {
    const o = overlayAdd(new Map(), ["tasks:a"], T_ACT);
    expect(overlayNextEnd(o, T_ACT + 1_000)).toBe(ACT_HOLD_MS - 1_000);
    expect(overlayNextEnd(new Map(), T_ACT)).toBeNull();
    expect(feedHiddenRows(new Set(), o, T_ACT)).toEqual(new Set(["tasks:a"]));
    expect(feedHiddenRows(new Set(), o, T_ACT + 1)).toEqual(new Set());
    // A row the overlay does not hold follows its needs mark (a Done in the bell).
    expect(feedHiddenRows(new Set(["tasks:b"]), o, T_ACT + 1)).toEqual(new Set(["tasks:b"]));
  });

  it("stamps the fetch time BEFORE the request", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify(ANSWER), { status: 200 })),
    );
    const before = Date.now();
    const answer = await fetchFeed();
    expect(answer.fetchedAt).toBeGreaterThanOrEqual(before);
    expect(answer.fetchedAt).toBeLessThanOrEqual(Date.now());
    expect(readFeedAnswer(ANSWER).fetchedAt).toBe(0);
  });
});

describe("7. the poll covers the needs key only after a needs view (fix round 1, P2)", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  const withNeeds = readFeedAnswer(ANSWER);
  const noNeeds = readFeedAnswer({ ...ANSWER, needs: undefined });

  it("covers after a read that held a needs view, and only then", () => {
    expect(coversNeeds(withNeeds, null)).toBe(true);
    expect(coversNeeds(withNeeds, "Your feed could not load.")).toBe(false);
    expect(coversNeeds(noNeeds, null)).toBe(false);
    expect(coversNeeds(undefined, null)).toBe(false);
  });

  /** One tick, with the bell on the needs key and the feed on its own key. */
  function tick(covers: boolean) {
    vi.useFakeTimers();
    const bell = vi.fn();
    const feed = vi.fn();
    const leaveBell = joinNeedsPoll(bell);
    const leaveFeed = joinFeedPoll(feed, covers);
    vi.advanceTimersByTime(BELL_POLL_MS);
    leaveBell();
    leaveFeed();
    return { bell: bell.mock.calls.length, feed: feed.mock.calls.length };
  }

  it("drives the hook's own state through a FAILED feed read, and the needs poller reads next tick", async () => {
    // Fix round 2, F3. The real fetch, the real cache read, and the hook's own
    // state rules (`useCachedResource`): a good answer, then a 502.
    let calls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        calls += 1;
        return calls === 1
          ? new Response(JSON.stringify(ANSWER), { status: 200 })
          : new Response(JSON.stringify({ error: "feed_unavailable" }), { status: 502 });
      }),
    );
    let state = initialState<FeedAnswer>(undefined);
    state = applyData(await read(feedKey(), () => fetchFeed()));
    expect(coversNeeds(state.data, state.error)).toBe(true);
    try {
      await read(feedKey(), () => fetchFeed(), { force: true });
    } catch (err) {
      state = applyError(state, (err as Error).message);
    }
    // The hook keeps the last good answer, and raises the error beside it.
    expect(state.data?.cards).toHaveLength(1);
    expect(state.error).toBe("Your feed could not load.");
    expect(tick(coversNeeds(state.data, state.error))).toEqual({ bell: 1, feed: 1 });
    // And the hook wires exactly that rule into the poll.
    const src = code(read_("./FeedCard.tsx"));
    expect(src).toMatch(/const covers = coversNeeds\(feed\.data, feed\.error\);/);
    expect(src).toMatch(/joinFeedPoll\(refresh, covers\)/);
  });

  it("after a FAILED feed read, the bell's needs poller reads on the next tick", () => {
    expect(tick(coversNeeds(withNeeds, "Your feed could not load."))).toEqual({ bell: 1, feed: 1 });
  });

  it("after an answer with NO needs view, the bell's needs poller reads on the next tick", () => {
    expect(tick(coversNeeds(noNeeds, null))).toEqual({ bell: 1, feed: 1 });
  });

  it("after a good answer, the feed's read is the only one", () => {
    expect(tick(coversNeeds(withNeeds, null))).toEqual({ bell: 0, feed: 1 });
  });
});

describe("6. one client", () => {
  it("only feed.ts fetches /api/shell/feed", () => {
    const root = fileURLToPath(new URL("../../", import.meta.url));
    const hits: string[] = [];
    const walk = (dir: string) => {
      for (const name of readdirSync(dir)) {
        const path = join(dir, name);
        if (statSync(path).isDirectory()) {
          walk(path);
          continue;
        }
        if (!/\.(ts|tsx)$/.test(name) || /\.test\.tsx?$/.test(name)) continue;
        const rel = relative(root, path).replace(/\\/g, "/");
        // The BFF route itself is the proxy, not a reader.
        if (rel.startsWith("app/api/shell/feed/")) continue;
        if (/["'`]\/api\/shell\/feed/.test(code(readFileSync(path, "utf8")))) hits.push(rel);
      }
    };
    walk(root);
    expect(hits).toEqual(["lib/shell/feed.ts"]);
  }, 60_000);
});
