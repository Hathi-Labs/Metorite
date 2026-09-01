import { describe, expect, it, vi } from "vitest";

import {
  applyData,
  applyError,
  applyRefreshing,
  coalesce,
  initialState,
  type ResourceState,
} from "./useCachedResource";

/**
 * The hook itself needs a DOM and this suite runs in `environment: "node"`
 * (vitest.config.ts), so the decisions live in these four pure functions and
 * the hook is the thin wiring around them. What is fenced here is the part
 * that was actually wrong before: WHEN a surface is allowed to go blank.
 */

describe("initialState", () => {
  it("a cache HIT starts with content and never loads", () => {
    // The whole fix in one assertion. A revisit paints real rows on the first
    // frame — no skeleton, no flash, no "Loading projects…".
    const s = initialState({ data: { rows: [1, 2] } });
    expect(s.loading).toBe(false);
    expect(s.data).toEqual({ rows: [1, 2] });
    expect(s.refreshing).toBe(true);
  });

  it("a MISS is the only thing that earns a skeleton", () => {
    const s = initialState(undefined);
    expect(s.loading).toBe(true);
    expect(s.data).toBeUndefined();
    expect(s.refreshing).toBe(false);
  });
});

describe("applyData", () => {
  it("clears a stale error from the read before it", () => {
    const s = applyData({ rows: [] });
    expect(s).toEqual({ data: { rows: [] }, loading: false, refreshing: false, error: null });
  });
});

describe("applyError", () => {
  it("⚠️ KEEPS the rows already on screen", () => {
    // Blanking on a failed revalidation hides the last thing that worked, at
    // the moment it is most useful.
    const prev: ResourceState<string> = {
      data: "good rows",
      loading: false,
      refreshing: true,
      error: null,
    };
    const s = applyError(prev, "gateway 500");
    expect(s.data).toBe("good rows");
    expect(s.error).toBe("gateway 500");
  });

  it("stops loading even with nothing to show, so no skeleton spins forever", () => {
    const prev: ResourceState<string> = {
      data: undefined,
      loading: true,
      refreshing: false,
      error: null,
    };
    const s = applyError(prev, "offline");
    expect(s.loading).toBe(false);
    expect(s.error).toBe("offline");
  });
});

describe("applyRefreshing", () => {
  it("over existing data it refreshes, and does NOT go back to loading", () => {
    const prev: ResourceState<number> = {
      data: 1,
      loading: false,
      refreshing: false,
      error: null,
    };
    const s = applyRefreshing(prev);
    expect(s.loading).toBe(false);
    expect(s.refreshing).toBe(true);
    expect(s.data).toBe(1);
  });

  it("with nothing to show it IS loading — there is no content to refresh over", () => {
    const prev: ResourceState<number> = {
      data: undefined,
      loading: false,
      refreshing: false,
      error: null,
    };
    const s = applyRefreshing(prev);
    expect(s.loading).toBe(true);
    expect(s.refreshing).toBe(false);
  });
});

describe("coalesce — the write-storm guard", () => {
  it("collapses a BURST of writes into one revalidation", () => {
    // Re-ordering a board patches one task per drop, and every patch
    // invalidates the family. Ten drops used to mean ten forced re-reads of
    // the same list, each a ~124 ms round trip answering a question the next
    // patch was about to change again.
    vi.useFakeTimers();
    const run = vi.fn();
    const c = coalesce(run, 150);
    for (let i = 0; i < 10; i += 1) {
      c.trigger();
      vi.advanceTimersByTime(20); // drops land faster than the window
    }
    expect(run).not.toHaveBeenCalled();
    vi.advanceTimersByTime(150);
    expect(run).toHaveBeenCalledTimes(1);
    vi.useRealTimers();
  });

  it("is TRAILING — it reads after the last write, never before", () => {
    // A leading edge would read before the writes have finished and cache the
    // value they were about to replace.
    vi.useFakeTimers();
    const run = vi.fn();
    const c = coalesce(run, 150);
    c.trigger();
    expect(run).not.toHaveBeenCalled();
    vi.advanceTimersByTime(149);
    expect(run).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(run).toHaveBeenCalledTimes(1);
    vi.useRealTimers();
  });

  it("cancel stops a pending run, so an unmounted view does not fetch", () => {
    vi.useFakeTimers();
    const run = vi.fn();
    const c = coalesce(run, 150);
    c.trigger();
    c.cancel();
    vi.advanceTimersByTime(1000);
    expect(run).not.toHaveBeenCalled();
    vi.useRealTimers();
  });

  it("runs again for a LATER, separate burst", () => {
    vi.useFakeTimers();
    const run = vi.fn();
    const c = coalesce(run, 150);
    c.trigger();
    vi.advanceTimersByTime(200);
    c.trigger();
    vi.advanceTimersByTime(200);
    expect(run).toHaveBeenCalledTimes(2);
    vi.useRealTimers();
  });
});

describe("the invariant the two flags exist to keep", () => {
  it("loading and refreshing are never both true", () => {
    // They mean different things and one surface reads both: `loading` draws a
    // skeleton, `refreshing` draws a quiet indicator over real content. Both
    // at once is a surface claiming to be empty and full at the same time.
    const states: ResourceState<number>[] = [
      initialState<number>({ data: 1 }),
      initialState<number>(undefined),
      applyData(1),
      applyError<number>({ data: 1, loading: false, refreshing: true, error: null }, "x"),
      applyError<number>(
        { data: undefined, loading: true, refreshing: false, error: null },
        "x"
      ),
      applyRefreshing({ data: 1, loading: false, refreshing: false, error: null }),
      applyRefreshing<number>({
        data: undefined,
        loading: false,
        refreshing: false,
        error: null,
      }),
    ];
    for (const s of states) expect(s.loading && s.refreshing).toBe(false);
  });
});
