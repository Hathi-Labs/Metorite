/**
 * HRD-17c: a send that a button or a card makes while the history loads
 * waits for the load (`chat_failure_modes.md` section 4.7a, point 5).
 *
 * The hold lives in memory, in one mount of the chat. It writes to no
 * storage and it does not use the outbox. A held send has no message id
 * until it posts, so a repeat of one id cannot happen here.
 *
 * - `hold` keeps one send. The first hold starts the deadline.
 * - `release` returns the held sends in order, once, and empties the hold.
 *   A second call returns nothing.
 * - `clear` drops the sends with no report. A thread switch and an unmount
 *   call it, so a send held for thread A never posts into thread B.
 * - The deadline drops the sends and reports them to `onExpire`. The sender
 *   gets its words back from that report.
 */

/** How many seconds a load may take before the hold gives up. */
export const HISTORY_HOLD_DEADLINE_SECONDS = 20;

export type HistoryHold<T> = {
  hold: (item: T) => void;
  release: () => T[];
  clear: () => void;
  size: () => number;
};

type Timers = {
  set: (fn: () => void, ms: number) => unknown;
  clear: (handle: unknown) => void;
};

const REAL_TIMERS: Timers = {
  set: (fn, ms) => setTimeout(fn, ms),
  clear: (h) => clearTimeout(h as ReturnType<typeof setTimeout>),
};

export function createHistoryHold<T>(
  onExpire: (dropped: T[]) => void,
  seconds: number = HISTORY_HOLD_DEADLINE_SECONDS,
  timers: Timers = REAL_TIMERS,
): HistoryHold<T> {
  let items: T[] = [];
  let handle: unknown = null;

  const stop = () => {
    if (handle !== null) timers.clear(handle);
    handle = null;
  };

  return {
    hold(item) {
      items.push(item);
      if (handle === null) {
        handle = timers.set(() => {
          handle = null;
          const dropped = items;
          items = [];
          if (dropped.length) onExpire(dropped);
        }, seconds * 1000);
      }
    },
    release() {
      stop();
      const out = items;
      items = [];
      return out;
    },
    clear() {
      stop();
      items = [];
    },
    size: () => items.length,
  };
}
