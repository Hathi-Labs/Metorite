/**
 * The one signal that this page is leaving its account (WS-58 MD-2b fix
 * round 2).
 *
 * An account switch changes the cookie first, and then it loads `/`. Between
 * the two, the page still runs as the leaving account, while every request
 * goes out as the next one. A part that WRITES the leaving account's state on
 * the way out (`pagehide`, an unmount) must stop before that. This module
 * tells it.
 *
 * ⚠️ It is a signal, and nothing more. `leaveAccount` does not touch the read
 * cache, wake a reader or close a pane. A cache clear here woke every mounted
 * reader of the leaving page, and each read went out as the next account
 * (fix round 1, measured). The page reloads anyway, and the reload is the one
 * reset that misses nothing (`accountSwitch.ts`, rule 3).
 *
 * Listeners today: the state queue of My Day's feed (`lib/shell/feedState.ts`).
 * Fence: `accountLeave.test.ts` and `accountSwitchLeave.test.ts`.
 */

const listeners = new Set<() => void>();

/** Hear the page leave its account. Returns the unsubscribe. */
export function onAccountLeave(cb: () => void): () => void {
  listeners.add(cb);
  return () => {
    listeners.delete(cb);
  };
}

/**
 * Tell each listener that this page leaves its account. Call it just before
 * the navigation. Each listener runs in its own `try`, so a throw never
 * blocks the navigation or the listeners after it.
 */
export function leaveAccount(): void {
  for (const cb of [...listeners]) {
    try {
      cb();
    } catch {
      /* a listener's failure must not keep the page on the old account */
    }
  }
}
