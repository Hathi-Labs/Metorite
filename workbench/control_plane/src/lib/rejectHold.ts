/**
 * Undo after Reject: the REJECT POST waits 5 s (#916).
 *
 * A Reject cannot be taken back once the server has it, because the tool
 * stops. So the chat holds the POST for `REJECT_HOLD_MS`. The receipt reads
 * "Rejected · … — Undo" in that time. An Undo inside the window cancels the
 * POST and puts the card back, with no error. After the window the POST goes
 * out as before.
 *
 * The rule that keeps a run from parking: a hold NEVER drops its REJECT.
 * A session switch or an unmount inside the window calls `flushAll`, which
 * sends every held REJECT at once. The agent waits on a Future, so a REJECT
 * that is lost would leave that run parked until its timeout.
 *
 * Pure, with the timers injected, because `vitest.config.ts` runs in the node
 * environment. Fence: `src/lib/rejectHold.test.ts`.
 */

export const REJECT_HOLD_MS = 5000;

export interface HoldTimers {
  set: (fn: () => void, ms: number) => unknown;
  clear: (handle: unknown) => void;
}

export const REAL_TIMERS: HoldTimers = {
  set: (fn, ms) => setTimeout(fn, ms),
  clear: (handle) => clearTimeout(handle as ReturnType<typeof setTimeout>),
};

export interface RejectHold {
  /** Hold `send` for the window. A second hold on the same key sends the first now. */
  start: (key: string, send: () => void) => void;
  /** Cancel the held send. True when one was held, false when it already went. */
  undo: (key: string) => boolean;
  /** Send every held REJECT now: a session switch, an unmount. */
  flushAll: () => void;
  has: (key: string) => boolean;
}

export function createRejectHold(
  timers: HoldTimers = REAL_TIMERS,
  windowMs: number = REJECT_HOLD_MS,
): RejectHold {
  const held = new Map<string, { handle: unknown; send: () => void }>();

  const release = (key: string) => {
    const entry = held.get(key);
    if (!entry) return;
    held.delete(key);
    timers.clear(entry.handle);
    entry.send();
  };

  return {
    start(key, send) {
      release(key);
      const handle = timers.set(() => release(key), windowMs);
      held.set(key, { handle, send });
    },
    undo(key) {
      const entry = held.get(key);
      if (!entry) return false;
      held.delete(key);
      timers.clear(entry.handle);
      return true;
    },
    flushAll() {
      for (const key of [...held.keys()]) release(key);
    },
    has: (key) => held.has(key),
  };
}
