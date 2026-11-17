/**
 * HRD-17c: the hold of a send while the history loads.
 *
 * Mutations this suite catches (R7):
 * - the drain returns the sends twice (a second `release` is not empty);
 * - the drain posts before the load settles (a hold is not kept until `release`);
 * - a thread switch does not clear the hold (`clear` keeps the sends);
 * - the deadline never fires (no report of the dropped sends);
 * - the order of the held sends changes.
 */
import { describe, expect, it, vi } from "vitest";
import { createHistoryHold, HISTORY_HOLD_DEADLINE_SECONDS } from "@/lib/historyHold";

function fakeTimers() {
  let fn: (() => void) | null = null;
  let ms = -1;
  return {
    timers: {
      set: (f: () => void, m: number) => { fn = f; ms = m; return 1; },
      clear: () => { fn = null; },
    },
    fire: () => fn?.(),
    armed: () => fn !== null,
    ms: () => ms,
  };
}

describe("historyHold", () => {
  it("keeps a send until the load settles, then returns it once", () => {
    const t = fakeTimers();
    const hold = createHistoryHold<string>(() => {}, 20, t.timers);
    hold.hold("yes");
    expect(hold.size()).toBe(1);
    expect(hold.release()).toEqual(["yes"]);
  });

  it("returns the sends in the order they were held", () => {
    const hold = createHistoryHold<string>(() => {}, 20, fakeTimers().timers);
    hold.hold("a");
    hold.hold("b");
    hold.hold("c");
    expect(hold.release()).toEqual(["a", "b", "c"]);
  });

  it("is single-shot: a second release returns nothing", () => {
    const hold = createHistoryHold<string>(() => {}, 20, fakeTimers().timers);
    hold.hold("a");
    expect(hold.release()).toHaveLength(1);
    expect(hold.release()).toEqual([]);
    expect(hold.size()).toBe(0);
  });

  it("drops the sends on a thread switch, with no report", () => {
    const t = fakeTimers();
    const expired = vi.fn();
    const hold = createHistoryHold<string>(expired, 20, t.timers);
    hold.hold("for thread A");
    hold.clear();
    expect(hold.release()).toEqual([]);
    expect(t.armed()).toBe(false);
    t.fire();
    expect(expired).not.toHaveBeenCalled();
  });

  it("drops the sends at the deadline and reports them", () => {
    const t = fakeTimers();
    const expired = vi.fn();
    const hold = createHistoryHold<string>(expired, 20, t.timers);
    hold.hold("a");
    hold.hold("b");
    expect(t.ms()).toBe(20_000);
    t.fire();
    expect(expired).toHaveBeenCalledTimes(1);
    expect(expired).toHaveBeenCalledWith(["a", "b"]);
    expect(hold.release()).toEqual([]);
  });

  it("stops the deadline when the load settles first", () => {
    const t = fakeTimers();
    const expired = vi.fn();
    const hold = createHistoryHold<string>(expired, 20, t.timers);
    hold.hold("a");
    hold.release();
    t.fire();
    expect(expired).not.toHaveBeenCalled();
  });

  it("uses one exported deadline constant, 20 seconds", () => {
    expect(HISTORY_HOLD_DEADLINE_SECONDS).toBe(20);
    const t = fakeTimers();
    createHistoryHold<string>(() => {}, undefined, t.timers).hold("a");
    expect(t.ms()).toBe(HISTORY_HOLD_DEADLINE_SECONDS * 1000);
  });
});
