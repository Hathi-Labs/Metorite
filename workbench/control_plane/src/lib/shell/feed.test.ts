/**
 * My Day's live feed in the browser (`my_day_feed.md` §14.1 and §14.1a,
 * WS-57 MD-1b).
 *
 * Six claims:
 *
 *   1. An answer in the shape of §14.1a reads whole, and an odd card is
 *      dropped, never a throw.
 *   2. The groups come in the order of `groups`, and the cards keep the
 *      server's order inside each group. The page shows 20 cards at a time.
 *   3. The feed answer fills the needs cache key when a network answer lands,
 *      and never with an answer from before a write.
 *   4. A `tap` act runs through the owning app's code (`runAct`), as the
 *      needs row of the needs view. An `open` act is a link.
 *   5. A read on a task card takes away its act, not the overdue task.
 *   6. One client: only `feed.ts` fetches `/api/shell/feed`, and nothing
 *      writes the needs key from an effect.
 *
 * Mutations, run by hand (2026-10-13):
 *   - drop `if (answer.needs) putNeeds(answer.needs);`: "fills the needs key" fails;
 *   - sort the cards by score in `feedGroups`: "keeps the server's order" fails;
 *   - make `needsRowFor` take the card id: "the needs row of a folded notification" fails.
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

const markDoneFromHome = vi.fn(async (id: string) => (id === "parent" ? "kept" : "done"));
vi.mock("@/app/tasks/lib/completeFromHome", () => ({
  markDoneFromHome: (id: string) => markDoneFromHome(id),
}));

import { PROJECTS_CACHE } from "@/app/projects/lib/api";
import { cacheKey, clearAll, invalidate, peek, read } from "@/lib/dataCache";

import {
  FEED_CACHE,
  FEED_SHOWN,
  type FeedCard as Card,
  actKey,
  actLeavesCard,
  drawnActs,
  feedAppIcon,
  feedFailedLines,
  feedGroups,
  feedKey,
  fetchFeed,
  heldIds,
  needsRowFor,
  readFeedAnswer,
  sourceLine,
} from "./feed";
import { FeedCard } from "./FeedCard";
import { needsKey, runAct } from "./needs";

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
    expect(readFeedAnswer(undefined)).toEqual({ cards: [], groups: [], needs: null, sources: {}, capped: [] });
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
    const answer = { cards: [card("a", "now"), card("b", "fyi")], groups: [{ id: "fyi" as const, count: 1 }] };
    const { groups } = feedGroups(answer, FEED_SHOWN, new Set(["b"]));
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
    // The one poll: the feed's refresh owns its key and covers the needs key.
    expect(card).toMatch(/joinNeedsPoll\(refresh, feedKey\(\), \[needsKey\(\)\]\)/);
  });
});

describe("4. the acts go through the owning app", () => {
  const read_card = card("tasks:t9", "needs_you", {
    kind: "notification",
    acts: [{ id: "read", label: "Mark read", class: "A", way: "tap", act: "read", act_ref: "n1" }],
  });
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
    expect(markDoneFromHome).toHaveBeenCalledWith("9b2e");
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

  it("wires each act through runAct and rowMover, and marks the needs row for the bell", () => {
    const src = code(read_("./FeedCard.tsx"));
    expect(src).toMatch(/void runAct\(item, rowMover\(id, move, setError, from\)\);/);
    expect(src).toMatch(/removeRow\(item\.id, gone\);/);
    // No second act path: the owning app's code runs only through runAct.
    expect(src).not.toMatch(/markDoneFromHome|notificationsApi|completeFromHome/);
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

describe("5. what an act takes away", () => {
  const folded = card("tasks:t1", "needs_you", {
    kind: "overdue",
    acts: [
      { id: "done", label: "Done", way: "tap", act: "done", act_ref: "t1" },
      { id: "read", label: "Mark read", way: "tap", act: "read", act_ref: "n7" },
    ],
  });

  it("a done takes the card, a read takes the card only when the notification is the card", () => {
    expect(actLeavesCard(folded, folded.acts[0])).toBe(true);
    expect(actLeavesCard(folded, folded.acts[1])).toBe(false);
    expect(actLeavesCard({ kind: "notification" }, { act: "read" })).toBe(true);
  });

  it("a spent act leaves its card, and the card stays", () => {
    const removed = new Set([actKey(folded, folded.acts[1])]);
    const html = renderToStaticMarkup(createElement(FeedCard, { card: folded, nowMs: null, removed, onAct: () => {} }));
    expect(html).toContain("Card tasks:t1");
    expect(html).not.toContain("Mark read");
    expect(html.match(/data-row-act=""/g)).toHaveLength(1);
    expect(heldIds([folded])).toEqual(["tasks:t1", "tasks:t1#done", "tasks:t1#read"]);
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
