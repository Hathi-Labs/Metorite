/**
 * WS-27bn R5g round 1: the live wiring of Overview, as behaviour.
 *
 * Spec: `project-docs/specs/projects_reports.md` §6.7 D and §8 R5g round 1.
 * The wiring takes its timers, clock and visibility from `LiveTimerEnv`, so
 * a fake one drives each rule here. The preview controller takes its fetch,
 * so each answer arrives when the test says.
 */
import { describe, expect, it } from "vitest";

import {
  LIVE_INTERVAL_MS,
  type LiveTimerEnv,
  bodyDimmed,
  createPreviewController,
  startLive,
  startTicker,
} from "./overviewLive";

/** A clock, interval timers and a visibility that the test moves. */
function fakeEnv(start = 1_000_000) {
  let t = start;
  let visible = true;
  let nextId = 0;
  const timers = new Map<number, { fn: () => void; ms: number; next: number }>();
  const listeners = new Set<() => void>();
  const env: LiveTimerEnv = {
    now: () => t,
    visibility: () => (visible ? "visible" : "hidden"),
    setInterval: (fn, ms) => {
      const id = ++nextId;
      timers.set(id, { fn, ms, next: t + ms });
      return id;
    },
    clearInterval: (id) => {
      timers.delete(id as number);
    },
    onVisibilityChange: (fn) => {
      listeners.add(fn);
      return () => {
        listeners.delete(fn);
      };
    },
  };
  return {
    env,
    advance(ms: number) {
      const end = t + ms;
      for (;;) {
        let due: { fn: () => void; ms: number; next: number } | null = null;
        for (const timer of timers.values()) if (timer.next <= end && (!due || timer.next < due.next)) due = timer;
        if (!due) break;
        t = due.next;
        due.next += due.ms;
        due.fn();
      }
      t = end;
    },
    setVisible(v: boolean) {
      visible = v;
      for (const fn of [...listeners]) fn();
    },
    get timers() {
      return timers.size;
    },
    get listeners() {
      return listeners.size;
    },
  };
}

/** A controller stand-in for `startLive`. */
function counter(updatedAt: number | null, busy = false) {
  const live = { n: 0, updatedAt, busy };
  return {
    live,
    target: {
      busy: () => live.busy,
      updatedAt: () => live.updatedAt,
      refresh: () => {
        live.n += 1;
      },
    },
  };
}

describe("R5g round 1: the live timer", () => {
  it("refreshes every 5 minutes while the tab is visible", () => {
    const f = fakeEnv();
    const c = counter(f.env.now());
    startLive(f.env, c.target);
    expect(c.live.n).toBe(0);
    f.advance(LIVE_INTERVAL_MS);
    expect(c.live.n).toBe(1);
    f.advance(LIVE_INTERVAL_MS);
    expect(c.live.n).toBe(2);
  });

  it("a hidden tab runs nothing, and holds no timer", () => {
    const f = fakeEnv();
    const c = counter(f.env.now());
    startLive(f.env, c.target);
    f.setVisible(false);
    expect(f.timers).toBe(0);
    f.advance(4 * LIVE_INTERVAL_MS);
    expect(c.live.n).toBe(0);
  });

  it("a tab hidden from the start starts no timer", () => {
    const f = fakeEnv();
    f.setVisible(false);
    const c = counter(null);
    startLive(f.env, c.target);
    expect(f.timers).toBe(0);
    f.advance(4 * LIVE_INTERVAL_MS);
    expect(c.live.n).toBe(0);
  });

  it("a return refreshes an answer over 60 s old, and not a newer one", () => {
    const f = fakeEnv();
    const c = counter(f.env.now());
    startLive(f.env, c.target);
    f.setVisible(false);
    f.advance(30_000);
    f.setVisible(true);
    expect(c.live.n).toBe(0);
    f.setVisible(false);
    f.advance(40_000);
    f.setVisible(true);
    expect(c.live.n).toBe(1);
    expect(f.timers).toBe(1);
  });

  it("a return does not refresh while a request is in flight", () => {
    const f = fakeEnv();
    const c = counter(f.env.now() - 10 * 60_000, true);
    startLive(f.env, c.target);
    f.setVisible(false);
    f.setVisible(true);
    expect(c.live.n).toBe(0);
  });

  it("a mount refreshes a kept answer over 60 s old (item 4)", () => {
    const f = fakeEnv();
    const old = counter(f.env.now() - 2 * 60_000);
    startLive(f.env, old.target);
    expect(old.live.n).toBe(1);
    const fresh = counter(f.env.now() - 10_000);
    startLive(f.env, fresh.target);
    expect(fresh.live.n).toBe(0);
    const none = counter(null);
    startLive(f.env, none.target);
    expect(none.live.n).toBe(0);
  });

  it("unmount clears the timer and the listener", () => {
    const f = fakeEnv();
    const c = counter(f.env.now());
    const stop = startLive(f.env, c.target);
    expect(f.timers).toBe(1);
    expect(f.listeners).toBe(1);
    stop();
    expect(f.timers).toBe(0);
    expect(f.listeners).toBe(0);
    f.advance(4 * LIVE_INTERVAL_MS);
    f.setVisible(false);
    f.setVisible(true);
    expect(c.live.n).toBe(0);
  });

  it("the clock ticker of Updated pauses on a hidden tab too", () => {
    const f = fakeEnv();
    let ticks = 0;
    const stop = startTicker(f.env, { intervalMs: 30_000, onTick: () => (ticks += 1), onShow: () => undefined });
    f.advance(60_000);
    expect(ticks).toBe(2);
    f.setVisible(false);
    f.advance(60_000);
    expect(ticks).toBe(2);
    stop();
    expect(f.timers).toBe(0);
  });
});

/** A fetch whose answers the test settles, in any order. */
function deferredFetch() {
  const calls: { key: string; resolve: (b: string) => void; reject: (e: unknown) => void }[] = [];
  return {
    calls,
    fetch: (req: { key: string }) =>
      new Promise<string>((resolve, reject) => calls.push({ key: req.key, resolve, reject })),
  };
}

const key = (n: number) =>
  JSON.stringify({ project_id: null, config: { sections: ["finished"], weeks: n }, blocked: false, round: 0 });
const BLOCKED = JSON.stringify({ project_id: null, config: { sections: [] }, blocked: true, round: 0 });
const flush = () => new Promise((r) => setTimeout(r, 0));

function controller(initial: { key: string; body: string; at?: number } | null, current: { key: string }) {
  const d = deferredFetch();
  const answers: { body: string; key: string }[] = [];
  const changes: unknown[] = [];
  const ctl = createPreviewController<string>({
    initial,
    fetch: d.fetch,
    currentKey: () => current.key,
    now: () => 5_000,
    onChange: (s) => changes.push(s),
    onAnswer: (body, k) => answers.push({ body, key: k }),
    errorMessage: () => "failed",
  });
  return { ctl, d, answers, changes };
}

describe("R5g round 1: a refresh keeps the key and the body", () => {
  it("keeps the body on screen and never dims it while it loads", async () => {
    const cur = { key: key(12) };
    const { ctl, d } = controller({ key: key(12), body: "old", at: 1 }, cur);
    expect(ctl.refresh()).toBe(true);
    expect(ctl.state.refreshing).toBe(true);
    expect(ctl.state.preview).toEqual({ key: key(12), body: "old" });
    expect(bodyDimmed(ctl.state, cur.key)).toBe(false);
    d.calls[0].resolve("new");
    await flush();
    expect(ctl.state.preview).toEqual({ key: key(12), body: "new" });
    expect(ctl.state.refreshing).toBe(false);
    expect(bodyDimmed(ctl.state, cur.key)).toBe(false);
  });

  it("writes the same key on screen and to Home", async () => {
    const cur = { key: key(12) };
    const { ctl, d, answers } = controller({ key: key(12), body: "old", at: 1 }, cur);
    ctl.refresh();
    d.calls[0].resolve("new");
    await flush();
    expect(ctl.state.shownKey).toBe(key(12));
    expect(answers).toEqual([{ body: "new", key: key(12) }]);
    expect(ctl.needed(key(12), false)).toBe(false);
    expect(ctl.state.updatedAt).toBe(5_000);
  });

  it("sends the key on screen", () => {
    const cur = { key: key(12) };
    const { ctl, d } = controller({ key: key(12), body: "old" }, cur);
    ctl.refresh();
    expect(d.calls.map((c) => c.key)).toEqual([key(12)]);
  });

  it("ignores a second click while it loads", () => {
    const cur = { key: key(12) };
    const { ctl, d } = controller({ key: key(12), body: "old" }, cur);
    expect(ctl.refresh()).toBe(true);
    expect(ctl.refresh()).toBe(false);
    expect(d.calls).toHaveLength(1);
  });

  it("asks nothing for a blocked key", () => {
    const cur = { key: BLOCKED };
    const { ctl, d } = controller(null, cur);
    expect(ctl.refresh()).toBe(false);
    expect(d.calls).toHaveLength(0);
  });

  it("a failed refresh keeps the body, clears the spinner and dims nothing", async () => {
    const cur = { key: key(12) };
    const { ctl, d } = controller({ key: key(12), body: "old" }, cur);
    ctl.refresh();
    d.calls[0].reject(new Error("502"));
    await flush();
    expect(ctl.state.refreshing).toBe(false);
    expect(ctl.state.refreshFailed).toBe(true);
    expect(ctl.state.preview).toEqual({ key: key(12), body: "old" });
    expect(ctl.state.error).toBeNull();
    expect(bodyDimmed(ctl.state, cur.key)).toBe(false);
  });
});

describe("R5g round 1: a tick never drops the answer to a filter", () => {
  it("a refresh during a filter request asks nothing, and the filter answer lands", async () => {
    const cur = { key: key(12) };
    const { ctl, d } = controller({ key: key(12), body: "old" }, cur);
    cur.key = key(4);
    ctl.request(key(4));
    expect(ctl.refresh()).toBe(false);
    expect(d.calls).toHaveLength(1);
    d.calls[0].resolve("four weeks");
    await flush();
    expect(ctl.state.preview).toEqual({ key: key(4), body: "four weeks" });
    expect(ctl.state.changing).toBe(false);
  });

  it("a refresh during the first request asks nothing, and the first answer lands", async () => {
    const cur = { key: key(12) };
    const { ctl, d } = controller(null, cur);
    ctl.request(key(12));
    expect(ctl.refresh()).toBe(false);
    expect(d.calls).toHaveLength(1);
    d.calls[0].resolve("first");
    await flush();
    expect(ctl.state.preview).toEqual({ key: key(12), body: "first" });
  });

  it("a late answer of an older change drops", async () => {
    const cur = { key: key(4) };
    const { ctl, d } = controller({ key: key(12), body: "old" }, cur);
    ctl.request(key(4));
    cur.key = key(1);
    ctl.request(key(1));
    d.calls[1].resolve("one week");
    await flush();
    d.calls[0].resolve("four weeks, late");
    await flush();
    expect(ctl.state.preview).toEqual({ key: key(1), body: "one week" });
  });

  it("a rebind after unmount lets answers land again (StrictMode)", async () => {
    const cur = { key: key(12) };
    const { ctl, d } = controller({ key: key(12), body: "old" }, cur);
    ctl.dispose();
    ctl.bind({ currentKey: () => cur.key, onChange: () => undefined });
    ctl.refresh();
    d.calls[0].resolve("new");
    await flush();
    expect(ctl.state.preview?.body).toBe("new");
  });

  it("a refresh while a change waits for its delay asks nothing", () => {
    const cur = { key: key(12) };
    const { ctl, d } = controller({ key: key(12), body: "old" }, cur);
    cur.key = key(4);
    expect(ctl.refresh()).toBe(false);
    expect(d.calls).toHaveLength(0);
  });

  it("a change supersedes a refresh in flight, and the spinner stops", async () => {
    const cur = { key: key(12) };
    const { ctl, d } = controller({ key: key(12), body: "old" }, cur);
    ctl.refresh();
    cur.key = key(4);
    ctl.request(key(4));
    d.calls[0].resolve("stale twelve");
    await flush();
    expect(ctl.state.preview?.body).toBe("old");
    expect(ctl.state.refreshing).toBe(false);
    d.calls[1].resolve("four weeks");
    await flush();
    expect(ctl.state.preview).toEqual({ key: key(4), body: "four weeks" });
  });

  it("a late error of an older change drops too", async () => {
    const cur = { key: key(4) };
    const { ctl, d } = controller({ key: key(12), body: "old" }, cur);
    ctl.request(key(4));
    cur.key = key(1);
    ctl.request(key(1));
    d.calls[0].reject(new Error("late"));
    await flush();
    expect(ctl.state.error).toBeNull();
    expect(ctl.state.changing).toBe(true);
    d.calls[1].resolve("one week");
    await flush();
    expect(ctl.state.preview).toEqual({ key: key(1), body: "one week" });
    expect(ctl.state.changing).toBe(false);
  });

  it("an error for the choices on screen shows", async () => {
    const cur = { key: key(4) };
    const { ctl, d } = controller({ key: key(12), body: "old" }, cur);
    ctl.request(key(4));
    d.calls[0].reject(new Error("500"));
    await flush();
    expect(ctl.state.error).toEqual({ key: key(4), message: "failed" });
    expect(ctl.state.changing).toBe(false);
  });

  it("no answer lands after unmount", async () => {
    const cur = { key: key(12) };
    const { ctl, d, changes, answers } = controller({ key: key(12), body: "old" }, cur);
    ctl.refresh();
    const before = changes.length;
    ctl.dispose();
    d.calls[0].resolve("new");
    await flush();
    expect(changes.length).toBe(before);
    expect(answers).toHaveLength(0);
  });

  it("a kept preview seeds the key on screen, so a return asks nothing", () => {
    const cur = { key: key(12) };
    expect(controller({ key: key(12), body: "old" }, cur).ctl.needed(key(12), false)).toBe(false);
    expect(controller(null, cur).ctl.needed(key(12), false)).toBe(true);
  });
});
