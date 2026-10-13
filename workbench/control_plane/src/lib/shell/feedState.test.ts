/**
 * The life of a card in the browser (`my_day_feed.md` §6.2, §6.8 and the
 * MD-2b ticket, WS-58).
 *
 * Six claims, each written so the obvious wrong build fails it:
 *
 *   1. A state item is `{signal_id, state_fp, kind, action, until}`, and a
 *      card with no `state_fp` is never sent.
 *   2. The queue sends one POST of 50 items at most, one at a time. A 422
 *      drops the batch, and a 5xx, a 502 or no answer keeps it.
 *   3. A card is seen after 1.5 s in view on a visible tab, and only then.
 *   4. The queue sends each 10 s, and every item on `pagehide`, with
 *      `keepalive`.
 *   5. A menu act hides the card through its own overlay, and NEVER through
 *      `needsActs`, so the bell's count does not change. A failed write
 *      shows the card again with an error line. Undo posts `restore`.
 *   6. The overlay ends when an answer fetched after the write lands, and a
 *      failed source never carries a card that a menu act hid.
 *
 * Mutations, run by hand (2026-10-14), each fails its test:
 *   - drop the `STATE_BATCH` slice in `flush`: claim 2, "50 at most";
 *   - keep a batch on a 422: claim 2, "a 422 drops";
 *   - drop the `retry` re-queue: claim 2, "a 5xx keeps";
 *   - send a sighting at once in `enter`: claim 3, "1.5 s";
 *   - drop the `pagehide` listener: claim 4;
 *   - mark the needs row in `runMenuAct`: claim 5, "the bell's count";
 *   - drop `forgetCarry` in `runMenuAct`: claim 6, the carry case.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { clearAll } from "@/lib/dataCache";

import { type FeedCard, fetchFeed, readFeedAnswer } from "./feed";
import {
  MENU_CHOICES,
  MENU_PENDING,
  SEEN_AGAIN_MS,
  SEEN_DWELL_MS,
  SEEN_FLUSH_MS,
  STATE_BATCH,
  UNDO_FAILED,
  UNDO_MS,
  type MenuDeps,
  type MenuOverlay,
  type StateItem,
  createDwell,
  createStateQueue,
  menuHidden,
  menuSettle,
  postState,
  runMenuAct,
  stateItem,
  undoMenuAct,
} from "./feedState";
import { acquireReader, actsSnapshot } from "./needsActs";

const card = (id: string, extra: Partial<FeedCard> = {}): FeedCard =>
  readFeedAnswer({
    cards: [
      {
        signal_id: `tasks:${id}`,
        app: "tasks",
        kind: "overdue",
        folded: [],
        group: "needs_you",
        score: 50,
        title: `Card ${id}`,
        why: null,
        at: null,
        href: `/projects?task=${id}`,
        acts: [],
        state: "new",
        state_fp: "a41f09c2",
        ...extra,
      },
    ],
  }).cards[0];

const seenOf = (id: string, fp = "a41f09c2"): StateItem => ({
  signal_id: `tasks:${id}`,
  state_fp: fp,
  kind: "overdue",
  action: "seen",
  until: null,
});

/** A post that records each body and answers each status in turn. */
function recorder(...statuses: number[]) {
  const bodies: { items: StateItem[]; keepalive: boolean }[] = [];
  const post = vi.fn(async (items: StateItem[], keepalive = false) => {
    bodies.push({ items, keepalive });
    return statuses.length > 1 ? (statuses.shift() as number) : (statuses[0] ?? 200);
  });
  return { post, bodies };
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  clearAll();
});

describe("1. the item of §6.8", () => {
  it("is the five keys, and `until` only on a snooze", () => {
    expect(stateItem(card("t1"), "seen")).toEqual({
      signal_id: "tasks:t1",
      state_fp: "a41f09c2",
      kind: "overdue",
      action: "seen",
      until: null,
    });
    expect(stateItem(card("t1"), "snooze", "tomorrow").until).toBe("tomorrow");
    expect(stateItem(card("t1"), "dismiss", "tomorrow").until).toBeNull();
    expect(stateItem(card("t1"), "restore").action).toBe("restore");
  });

  it("posts the body to the BFF route as JSON, with keepalive when asked", async () => {
    const calls: { url: string; init: RequestInit }[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init: RequestInit) => {
        calls.push({ url, init });
        return new Response("{}", { status: 422 });
      }),
    );
    expect(await postState([seenOf("t1")], true)).toBe(422);
    expect(calls[0].url).toBe("/api/shell/feed/state");
    expect(calls[0].init.method).toBe("POST");
    expect(calls[0].init.keepalive).toBe(true);
    expect(JSON.parse(String(calls[0].init.body))).toEqual({ items: [seenOf("t1")] });
  });

  it("answers 0 for a network failure, so the queue keeps the batch", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("offline"))));
    expect(await postState([seenOf("t1")])).toBe(0);
  });
});

describe("2. the queue", () => {
  it("sends 50 items at most in one POST, and the rest on the next send", async () => {
    const { post, bodies } = recorder(200);
    const queue = createStateQueue(post);
    for (let i = 0; i < 60; i++) queue.see(seenOf(`t${i}`));
    await queue.flush();
    expect(bodies.map((b) => b.items.length)).toEqual([STATE_BATCH]);
    expect(STATE_BATCH).toBe(50);
    await queue.flush();
    expect(bodies.map((b) => b.items.length)).toEqual([50, 10]);
    expect(queue.pending()).toEqual([]);
  });

  it("drops a batch on a 422, because the same body never passes", async () => {
    const { post } = recorder(422);
    const queue = createStateQueue(post);
    queue.see(seenOf("t1"));
    await queue.flush();
    expect(queue.pending()).toEqual([]);
  });

  it.each([500, 502, 503, 0])("keeps a batch on %i, and sends it again", async (status) => {
    const { post, bodies } = recorder(status, 200);
    const queue = createStateQueue(post);
    queue.see(seenOf("t1"));
    await queue.flush();
    expect(queue.pending()).toEqual([seenOf("t1")]);
    await queue.flush();
    expect(bodies.map((b) => b.items)).toEqual([[seenOf("t1")], [seenOf("t1")]]);
    expect(queue.pending()).toEqual([]);
  });

  it("runs one send at a time", async () => {
    let open: (status: number) => void = () => {};
    const post = vi.fn(() => new Promise<number>((resolve) => (open = resolve)));
    const queue = createStateQueue(post);
    queue.see(seenOf("t1"));
    const first = queue.flush();
    queue.see(seenOf("t2"));
    await queue.flush();
    expect(post).toHaveBeenCalledTimes(1);
    open(200);
    await first;
    expect(queue.pending()).toEqual([seenOf("t2")]);
  });

  it("sends one sighting of a card in 30 minutes, and a new fingerprint at once", () => {
    let now = 0;
    const queue = createStateQueue(recorder().post, () => now);
    expect(queue.see(seenOf("t1"))).toBe(true);
    now = SEEN_AGAIN_MS - 1;
    expect(queue.see(seenOf("t1"))).toBe(false);
    expect(queue.see(seenOf("t1", "0000bbbb"))).toBe(true);
    now = SEEN_AGAIN_MS;
    expect(queue.see(seenOf("t1"))).toBe(true);
  });

  it("holds one item for each card and action, and keeps `acted` beside `seen`", () => {
    const queue = createStateQueue(recorder().post);
    queue.see(seenOf("t1"));
    queue.see(seenOf("t1", "0000bbbb"));
    queue.add({ ...seenOf("t1"), action: "acted" });
    expect(queue.pending().map((i) => [i.action, i.state_fp])).toEqual([
      ["seen", "0000bbbb"],
      ["acted", "a41f09c2"],
    ]);
  });

  it("puts a failed batch back BEHIND nothing newer of the same slot", async () => {
    let open: (status: number) => void = () => {};
    const post = vi.fn(() => new Promise<number>((resolve) => (open = resolve)));
    const queue = createStateQueue(post);
    queue.see(seenOf("t1"));
    const sent = queue.flush();
    queue.see(seenOf("t1", "0000bbbb")); // newer, while the old one is out
    open(503);
    await sent;
    expect(queue.pending().map((i) => i.state_fp)).toEqual(["0000bbbb"]);
  });
});

describe("3. the dwell (§6.2)", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  it("sees a card after 1.5 s in view, and not before", () => {
    const seen: string[] = [];
    const dwell = createDwell((id) => seen.push(id), { visible: () => true });
    dwell.enter("a");
    vi.advanceTimersByTime(SEEN_DWELL_MS - 1);
    expect(seen).toEqual([]);
    vi.advanceTimersByTime(1);
    expect(seen).toEqual(["a"]);
    expect(SEEN_DWELL_MS).toBe(1_500);
  });

  it("does not see a card that left the view before 1.5 s", () => {
    const seen: string[] = [];
    const dwell = createDwell((id) => seen.push(id), { visible: () => true });
    dwell.enter("a");
    vi.advanceTimersByTime(1_000);
    dwell.leave("a");
    vi.advanceTimersByTime(5_000);
    expect(seen).toEqual([]);
  });

  it("counts nothing on a hidden tab, and counts again when it shows", () => {
    const seen: string[] = [];
    let visible = false;
    const dwell = createDwell((id) => seen.push(id), { visible: () => visible });
    dwell.enter("a");
    vi.advanceTimersByTime(5_000);
    expect(seen).toEqual([]);
    visible = true;
    dwell.visibility(true);
    vi.advanceTimersByTime(SEEN_DWELL_MS);
    expect(seen).toEqual(["a"]);
  });

  it("stops the count when the tab hides", () => {
    const seen: string[] = [];
    let visible = true;
    const dwell = createDwell((id) => seen.push(id), { visible: () => visible });
    dwell.enter("a");
    vi.advanceTimersByTime(1_000);
    visible = false;
    dwell.visibility(false);
    vi.advanceTimersByTime(5_000);
    expect(seen).toEqual([]);
  });
});

describe("4. one POST each 10 s, and the rest on pagehide", () => {
  it("flushes on the tick, and drains with keepalive on pagehide and at the stop", async () => {
    vi.useFakeTimers();
    const win = Object.assign(new EventTarget(), {
      setInterval: (fn: () => void, ms: number) => setInterval(fn, ms),
      clearInterval: (id: ReturnType<typeof setInterval>) => clearInterval(id),
    });
    vi.stubGlobal("window", win);
    const { sendStateQueue } = await import("./FeedCard");
    const { post, bodies } = recorder(200);
    const queue = createStateQueue(post);
    const stop = sendStateQueue(queue);
    queue.see(seenOf("t1"));
    await vi.advanceTimersByTimeAsync(SEEN_FLUSH_MS - 1);
    expect(bodies).toEqual([]);
    await vi.advanceTimersByTimeAsync(1);
    expect(bodies).toEqual([{ items: [seenOf("t1")], keepalive: false }]);
    expect(SEEN_FLUSH_MS).toBe(10_000);

    for (let i = 0; i < 51; i++) queue.see(seenOf(`p${i}`));
    win.dispatchEvent(new Event("pagehide"));
    expect(bodies.slice(1).map((b) => [b.items.length, b.keepalive])).toEqual([
      [50, true],
      [1, true],
    ]);

    queue.see(seenOf("last"));
    stop();
    expect(bodies.at(-1)).toEqual({ items: [seenOf("last")], keepalive: true });
    // After the stop, the tick and pagehide send nothing.
    queue.see(seenOf("after"));
    win.dispatchEvent(new Event("pagehide"));
    await vi.advanceTimersByTimeAsync(SEEN_FLUSH_MS * 2);
    expect(bodies.at(-1)?.items).toEqual([seenOf("last")]);
  }, 30_000);
});

describe("5. a menu act (§6.4, §6.6)", () => {
  /** The deps of a menu act, with a real overlay and a log. */
  function deps(post: MenuDeps["post"], at = 1_000) {
    const log: string[] = [];
    let overlay: MenuOverlay = new Map();
    const toasts: Parameters<MenuDeps["toast"]>[0][] = [];
    const errors: Record<string, string | null> = {};
    const d: MenuDeps = {
      post,
      overlay: (change) => {
        overlay = change(overlay);
      },
      refresh: () => log.push("refresh"),
      toast: (spec) => toasts.push(spec),
      setError: (id, message) => {
        errors[id] = message;
      },
      now: () => at,
    };
    return { d, log, toasts, errors, overlay: () => overlay };
  }

  it("hides the card while its write runs, and the write is the item of the choice", async () => {
    let open: (status: number) => void = () => {};
    const bodies: StateItem[][] = [];
    const post = vi.fn((items: StateItem[]) => {
      bodies.push(items);
      return new Promise<number>((resolve) => (open = resolve));
    });
    const t = deps(post);
    const tomorrow = MENU_CHOICES.find((c) => c.until === "tomorrow")!;
    const run = runMenuAct(card("t1"), tomorrow, t.d);
    expect(t.overlay().get("tasks:t1")).toBe(MENU_PENDING);
    expect(bodies).toEqual([[{ ...seenOf("t1"), action: "snooze", until: "tomorrow" }]]);
    open(200);
    expect(await run).toBe("done");
    expect(t.overlay().get("tasks:t1")).toBe(1_000);
    expect(t.log).toEqual(["refresh"]);
  });

  it("leaves the bell's count and the sidebar counts unchanged: no needs mark", async () => {
    const release = acquireReader();
    try {
      const before = actsSnapshot();
      for (const choice of MENU_CHOICES) {
        const t = deps(recorder(200).post);
        await runMenuAct(card("t1"), choice, t.d);
      }
      expect(actsSnapshot()).toBe(before);
      expect(actsSnapshot().removed.size).toBe(0);
    } finally {
      release();
    }
  });

  it("never names the needs store", async () => {
    const { readFileSync } = await import("node:fs");
    const src = readFileSync(new URL("./feedState.ts", import.meta.url), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
    expect(src).not.toMatch(/needsActs|removeRow|setRowError/);
  });

  it("offers Undo for 10 seconds, on the shared toast", async () => {
    const t = deps(recorder(200).post);
    await runMenuAct(card("t1"), MENU_CHOICES.find((c) => c.action === "dismiss")!, t.d);
    expect(t.toasts).toHaveLength(1);
    expect(t.toasts[0]).toMatchObject({ key: "feed-menu:tasks:t1", title: "Dismissed until it changes", timeout: UNDO_MS });
    expect(t.toasts[0].action?.label).toBe("Undo");
    expect(UNDO_MS).toBe(10_000);
  });

  it("shows the card again, with an error line, when the write fails", async () => {
    const t = deps(recorder(502).post);
    const out = await runMenuAct(card("t1"), MENU_CHOICES[0], t.d);
    expect(out).toBe("failed");
    expect(t.overlay().has("tasks:t1")).toBe(false);
    expect(t.errors["tasks:t1"]).toBe("Could not snooze this card. Try again.");
    expect(t.toasts).toEqual([]);
    expect(t.log).toEqual([]);
  });

  it("runs nothing for a card with no state_fp", async () => {
    const { post } = recorder(200);
    const t = deps(post);
    expect(await runMenuAct(card("t1", { state_fp: undefined }), MENU_CHOICES[0], t.d)).toBeNull();
    expect(post).not.toHaveBeenCalled();
  });

  it("posts `restore` on Undo, and the card may show again", async () => {
    const { post, bodies } = recorder(200);
    const t = deps(post);
    await runMenuAct(card("t1"), MENU_CHOICES[0], t.d);
    t.toasts[0].action?.onClick();
    await vi.waitFor(() => expect(bodies).toHaveLength(2));
    expect(bodies[1].items).toEqual([{ ...seenOf("t1"), action: "restore" }]);
    await vi.waitFor(() => expect(t.overlay().has("tasks:t1")).toBe(false));
    expect(t.log).toEqual(["refresh", "refresh"]);
  });

  it("says so when Undo fails, and keeps the card hidden", async () => {
    const t = deps(recorder(200).post);
    await runMenuAct(card("t1"), MENU_CHOICES[0], t.d);
    t.d.post = recorder(503).post;
    expect(await undoMenuAct(card("t1"), t.d)).toBe("failed");
    expect(t.overlay().has("tasks:t1")).toBe(true);
    expect(t.toasts.at(-1)).toMatchObject({ title: UNDO_FAILED, variant: "error" });
  });

  it("offers the four snoozes, then Dismiss and Not useful", () => {
    expect(MENU_CHOICES.map((c) => [c.action, c.until])).toEqual([
      ["snooze", "later_today"],
      ["snooze", "tomorrow"],
      ["snooze", "next_week"],
      ["snooze", "on_change"],
      ["dismiss", null],
      ["not_useful", null],
    ]);
  });
});

describe("6. the overlay ends with an answer fetched after the write", () => {
  it("hides the card from an older answer, and shows a newer answer's truth", () => {
    const overlay: MenuOverlay = new Map([
      ["tasks:a", 1_000],
      ["tasks:b", MENU_PENDING],
    ]);
    expect([...menuHidden(overlay, 999)].sort()).toEqual(["tasks:a", "tasks:b"]);
    expect([...menuHidden(overlay, 1_000)].sort()).toEqual(["tasks:a", "tasks:b"]);
    expect([...menuHidden(overlay, 1_001)]).toEqual(["tasks:b"]);
    expect([...menuSettle(overlay, 1_001).keys()]).toEqual(["tasks:b"]);
    expect(menuSettle(overlay, 500)).toBe(overlay);
  });

  it("never carries a card that a menu act hid while its source fails, and carries it again after Undo", async () => {
    const answer = (cards: unknown[], tasks: string) => ({
      cards,
      groups: cards.length ? [{ id: "needs_you", count: cards.length }] : [],
      sources: { tasks },
      capped: [],
    });
    const raw = {
      signal_id: "tasks:t1",
      app: "tasks",
      kind: "overdue",
      folded: [],
      group: "needs_you",
      title: "Card t1",
      href: "/projects?task=t1",
      acts: [],
      state: "seen",
      state_fp: "a41f09c2",
    };
    const replies = [answer([raw], "ok"), answer([], "failed"), answer([raw], "ok"), answer([], "failed")];
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify(replies.shift()), { status: 200 })),
    );
    const first = await fetchFeed();
    expect(first.cards.map((c) => c.signal_id)).toEqual(["tasks:t1"]);
    const t = { overlay: () => {}, refresh: () => {}, toast: () => {}, setError: () => {} };
    const at = Date.now() - 1;
    await runMenuAct(first.cards[0], MENU_CHOICES[4], { ...t, post: recorder(200).post, now: () => at });
    // The source fails: the dismissed card is NOT carried from the answer before.
    expect((await fetchFeed()).cards).toEqual([]);
    // It comes back from a sound answer, so the carry may keep it again.
    expect((await fetchFeed()).cards.map((c) => c.signal_id)).toEqual(["tasks:t1"]);
    expect((await fetchFeed()).cards.map((c) => c.signal_id)).toEqual(["tasks:t1"]);
  });
});
