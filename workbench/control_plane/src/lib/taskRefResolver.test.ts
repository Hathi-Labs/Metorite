/**
 * The task reference lookup (owner report, 2026-10-13): one batched call per
 * answer, a cache per chat session, and text until the lookup answers.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  MAX_REFS_PER_CALL,
  RETRY_MS,
  createTaskRefResolver,
  pickTask,
  type TaskRefHit,
} from "@/lib/taskRefResolver";

const hit = (n: number, extra: Partial<TaskRefHit> = {}): TaskRefHit => ({
  id: `id-${n}${extra.archived ? "-old" : ""}`,
  task_number: n,
  title: `Task ${n}`,
  ...extra,
});

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe("the batch", () => {
  it("sends every reference asked for in one tick in ONE call", async () => {
    const fetcher = vi.fn(async (ns: number[]) => ns.map((n) => hit(n)));
    const r = createTaskRefResolver(fetcher);
    for (const n of [171, 87, 172, 87]) r.request("s", n);
    await vi.runOnlyPendingTimersAsync();
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher).toHaveBeenCalledWith([87, 171, 172]);
    expect(r.get("s", 171)).toEqual({ state: "done", hits: [hit(171)] });
  });

  it("never asks for a number twice in one session", async () => {
    const fetcher = vi.fn(async (ns: number[]) => ns.map((n) => hit(n)));
    const r = createTaskRefResolver(fetcher);
    r.request("s", 5);
    await vi.runOnlyPendingTimersAsync();
    r.request("s", 5);
    await vi.runOnlyPendingTimersAsync();
    expect(fetcher).toHaveBeenCalledTimes(1);
    // Another chat session asks again.
    r.request("t", 5);
    await vi.runOnlyPendingTimersAsync();
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("splits a very long answer at the gateway's cap", async () => {
    const fetcher = vi.fn(async (ns: number[]) => ns.map((n) => hit(n)));
    const r = createTaskRefResolver(fetcher);
    for (let n = 1; n <= MAX_REFS_PER_CALL + 1; n += 1) r.request("s", n);
    await vi.runOnlyPendingTimersAsync();
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(fetcher.mock.calls[0][0]).toHaveLength(MAX_REFS_PER_CALL);
  });
});

describe("what a reference shows", () => {
  it("leaves an unknown number with no hit, so it stays text", async () => {
    const r = createTaskRefResolver(async () => []);
    r.request("s", 404);
    expect(r.get("s", 404)).toEqual({ state: "pending" });
    await vi.runOnlyPendingTimersAsync();
    const entry = r.get("s", 404);
    expect(entry).toEqual({ state: "done", hits: [] });
    expect(pickTask(entry!.state === "done" ? entry!.hits : [])).toBeNull();
  });

  it("stays text when the lookup fails, then upgrades when it answers", async () => {
    let up = false;
    const fetcher = vi.fn(async (ns: number[]) => {
      if (!up) throw new Error("gateway restarting");
      return ns.map((n) => hit(n));
    });
    const r = createTaskRefResolver(fetcher);
    const seen = vi.fn();
    r.subscribe(seen);
    r.request("s", 171);
    await vi.runOnlyPendingTimersAsync();
    expect(r.get("s", 171)).toEqual({ state: "failed", attempts: 1 });
    up = true;
    await vi.advanceTimersByTimeAsync(RETRY_MS[0]);
    await vi.runOnlyPendingTimersAsync();
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(r.get("s", 171)).toEqual({ state: "done", hits: [hit(171)] });
    // A pill subscribed to the store hears both changes.
    expect(seen).toHaveBeenCalledTimes(2);
  });

  it("gives up after the retry ladder, and stays text", async () => {
    const fetcher = vi.fn(async () => {
      throw new Error("down");
    });
    const r = createTaskRefResolver(fetcher);
    r.request("s", 9);
    await vi.runAllTimersAsync();
    expect(fetcher).toHaveBeenCalledTimes(RETRY_MS.length + 1);
    expect(r.get("s", 9)).toEqual({ state: "failed", attempts: RETRY_MS.length + 1 });
  });
});

describe("pickTask", () => {
  it("links one hit", () => {
    expect(pickTask([hit(5)])).toEqual(hit(5));
  });

  it("links none of two open tasks with one number (two roots)", () => {
    expect(pickTask([hit(5), { ...hit(5), id: "other-root" }])).toBeNull();
  });

  it("prefers the one open task over an archived twin", () => {
    expect(pickTask([hit(5), hit(5, { archived: true })])).toEqual(hit(5));
  });

  it("with a title, links only the hit that carries it", () => {
    const twin = { ...hit(5), id: "other-root", title: "Something else" };
    expect(pickTask([hit(5), twin], "task 5")).toEqual(hit(5));
    expect(pickTask([hit(5)], "A different title")).toBeNull();
  });
});
