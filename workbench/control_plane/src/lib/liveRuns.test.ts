/**
 * The ONE poller of `/api/chat/active-sessions` (WS-51 S1,
 * `chat_run_continuity.md` §4 S1).
 *
 * Before S1, each of five surfaces ran its own 5 s loop, so one tab sent up
 * to five identical requests every 5 s. This file holds the loop to one fetch
 * per tick for any number of subscribers, to a slower tick in a hidden tab,
 * and to no tick at all once nobody listens. It also sweeps the tree, so a
 * sixth surface cannot start a loop of its own.
 */

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  HIDDEN_MS,
  VISIBLE_MS,
  _resetLiveRunsForTests,
  getLiveRuns,
  pollIntervalMs,
  subscribeLiveRuns,
} from "./liveRuns";

type FakeDoc = EventTarget & { visibilityState: "visible" | "hidden" };

let doc: FakeDoc;
let fetchMock: ReturnType<typeof vi.fn>;
let payload: unknown[] = [];

/** Let the awaited fetch and json() settle. */
async function flush(): Promise<void> {
  for (let i = 0; i < 5; i++) await Promise.resolve();
}

function setHidden(hidden: boolean): void {
  doc.visibilityState = hidden ? "hidden" : "visible";
  doc.dispatchEvent(new Event("visibilitychange"));
}

beforeEach(() => {
  vi.useFakeTimers();
  doc = Object.assign(new EventTarget(), { visibilityState: "visible" as const }) as FakeDoc;
  vi.stubGlobal("document", doc);
  payload = [];
  fetchMock = vi.fn(async () => ({ ok: true, json: async () => payload }));
  vi.stubGlobal("fetch", fetchMock);
  _resetLiveRunsForTests();
});

afterEach(() => {
  _resetLiveRunsForTests();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("one poller for the whole app", () => {
  it("sends ONE request per tick for any number of subscribers", async () => {
    const offs = Array.from({ length: 5 }, () => subscribeLiveRuns(() => {}));
    await flush();
    expect(fetchMock).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(VISIBLE_MS);
    await flush();
    expect(fetchMock).toHaveBeenCalledTimes(2);

    await vi.advanceTimersByTimeAsync(VISIBLE_MS * 3);
    await flush();
    expect(fetchMock).toHaveBeenCalledTimes(5);
    offs.forEach((off) => off());
  });

  it("tells every subscriber, and keeps one list reference while unchanged", async () => {
    payload = [{ threadId: "t1", agentName: "orchestrator", title: "A" }];
    const seen: number[] = [0, 0, 0];
    const offs = seen.map((_, i) => subscribeLiveRuns(() => { seen[i] += 1; }));
    await flush();
    expect(seen).toEqual([1, 1, 1]);
    const first = getLiveRuns();
    expect(first.map((r) => r.threadId)).toEqual(["t1"]);

    await vi.advanceTimersByTimeAsync(VISIBLE_MS);
    await flush();
    expect(seen).toEqual([1, 1, 1]);
    expect(getLiveRuns()).toBe(first);
    offs.forEach((off) => off());
  });

  it("stops polling when the last subscriber leaves", async () => {
    const a = subscribeLiveRuns(() => {});
    const b = subscribeLiveRuns(() => {});
    await flush();
    a();
    b();
    await vi.advanceTimersByTimeAsync(HIDDEN_MS * 2);
    await flush();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("names an agentless run 'unknown' and drops a row with no thread", async () => {
    payload = [{ threadId: "t1" }, { agentName: "x" }, null];
    const off = subscribeLiveRuns(() => {});
    await flush();
    expect(getLiveRuns()).toEqual([
      { threadId: "t1", agentName: "unknown", title: null, startedAt: null },
    ]);
    off();
  });
});

describe("a hidden tab polls slower", () => {
  it("waits HIDDEN_MS between polls while hidden", async () => {
    expect(HIDDEN_MS).toBe(30_000);
    expect(VISIBLE_MS).toBe(5_000);
    const off = subscribeLiveRuns(() => {});
    await flush();
    expect(fetchMock).toHaveBeenCalledTimes(1);

    setHidden(true);
    expect(pollIntervalMs()).toBe(HIDDEN_MS);
    // Three visible intervals pass with no poll.
    await vi.advanceTimersByTimeAsync(VISIBLE_MS * 3);
    await flush();
    expect(fetchMock).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(HIDDEN_MS);
    await flush();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    off();
  });

  it("polls at once when the tab is visible again", async () => {
    const off = subscribeLiveRuns(() => {});
    await flush();
    setHidden(true);
    await flush();
    const before = fetchMock.mock.calls.length;
    setHidden(false);
    await flush();
    expect(fetchMock.mock.calls.length).toBe(before + 1);
    expect(pollIntervalMs()).toBe(VISIBLE_MS);
    off();
  });
});

describe("no surface polls the run list on its own", () => {
  // The structural half: a new loop anywhere in the app fails here.
  const SRC = fileURLToPath(new URL("..", import.meta.url));
  const ALLOWED = new Set([
    "lib/liveRuns.ts",
    // One read on mount, to decide whether to reattach. Not a loop.
    "hooks/useAgentChat.ts",
  ]);

  function walk(dir: string): string[] {
    return readdirSync(dir).flatMap((name) => {
      const full = join(dir, name);
      if (statSync(full).isDirectory()) return walk(full);
      return /\.(ts|tsx)$/.test(name) && !/\.test\.tsx?$/.test(name) ? [full] : [];
    });
  }

  it("only lib/liveRuns.ts fetches /api/chat/active-sessions repeatedly", () => {
    const offenders = walk(SRC)
      .map((f) => relative(SRC, f).replace(/\\/g, "/"))
      .filter((rel) => !rel.startsWith("app/api/"))
      .filter((rel) => readFileSync(join(SRC, rel), "utf8").includes('"/api/chat/active-sessions"'))
      .filter((rel) => !ALLOWED.has(rel));
    expect(offenders).toEqual([]);
  });
});
