import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { startPoller } from "./poller";

describe("the sync poller", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  const make = (over: Partial<Parameters<typeof startPoller>[0]> = {}) => {
    const refresh = vi.fn();
    const poller = startPoller({
      refresh,
      isRunning: () => false,
      isHidden: () => false,
      ms: 1000,
      limit: 3,
      ...over,
    });
    return { refresh, poller };
  };

  it("ends a watch after the limit, and a new poller polls again", () => {
    const first = make();
    vi.advanceTimersByTime(10_000);
    expect(first.refresh).toHaveBeenCalledTimes(3);
    // The next watch is a new poller (a new token re-arms the effect).
    const second = make();
    vi.advanceTimersByTime(2000);
    expect(second.refresh).toHaveBeenCalledTimes(2);
  });

  it("polls a running sync past the limit", () => {
    const { refresh } = make({ isRunning: () => true });
    vi.advanceTimersByTime(10_000);
    expect(refresh).toHaveBeenCalledTimes(10);
  });

  it("skips ticks in a hidden tab and refreshes once on show", () => {
    let hidden = true;
    const { refresh, poller } = make({ isHidden: () => hidden, isRunning: () => true });
    vi.advanceTimersByTime(5000);
    expect(refresh).not.toHaveBeenCalled();
    poller.onVisible();
    expect(refresh).not.toHaveBeenCalled();
    hidden = false;
    poller.onVisible();
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it("stops on stop()", () => {
    const { refresh, poller } = make({ isRunning: () => true });
    poller.stop();
    vi.advanceTimersByTime(5000);
    expect(refresh).not.toHaveBeenCalled();
  });
});
