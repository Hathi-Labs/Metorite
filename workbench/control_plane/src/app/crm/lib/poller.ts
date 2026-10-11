// The sync-status poller (WS-53 CRM-Z7), apart from React so a test can drive it.

export type PollerDeps = {
  refresh: () => void;
  /** Is a sync running now? Read at each tick. */
  isRunning: () => boolean;
  /** Is the tab hidden? A hidden tab is not polled. */
  isHidden: () => boolean;
  ms: number;
  /** A watch with no running sync ends after this many ticks. */
  limit: number;
};

/**
 * Start a poll. Returns the stop function.
 *
 * A tick in a hidden tab does nothing. When the tab shows again, `onVisible`
 * refreshes once. A watch ends after `limit` ticks unless a sync runs.
 */
export function startPoller(deps: PollerDeps): { stop: () => void; onVisible: () => void } {
  let polls = 0;
  const timer = setInterval(() => {
    polls += 1;
    if (!deps.isRunning() && polls > deps.limit) {
      clearInterval(timer);
      return;
    }
    if (deps.isHidden()) return;
    deps.refresh();
  }, deps.ms);
  return {
    stop: () => clearInterval(timer),
    onVisible: () => {
      if (!deps.isHidden()) deps.refresh();
    },
  };
}
