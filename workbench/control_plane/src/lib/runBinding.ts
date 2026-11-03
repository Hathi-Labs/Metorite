/**
 * One reader follows one run (task 1533, bug B3).
 *
 * A thread has ONE event stream. A newer send cancels the running run and
 * resets that stream, so the reader of the old send then sees the NEW run's
 * events. Without this binding it saved them under the old reply's id, and the
 * chat showed one reply twice. A second `RUN_STARTED` with another `runId`
 * marks the switch. The reader stops there.
 *
 * Fence: `runBinding.test.ts`. `app/api/agent/chat/route.ts` is the caller.
 */
export interface RunBinding {
  /** Feed one stream event. Returns false when it belongs to another run. */
  accept(ev: { type?: unknown; runId?: unknown }): boolean;
  readonly superseded: boolean;
}

export function bindToRun(seed?: string | null): RunBinding {
  // A seed is the run id the gateway named in its `X-Run-Id` header. With it,
  // ANY other runId is another run, even the first one this reader sees: a
  // cancelled send reads the stream from "0" and meets the newer run first.
  // With no seed (an older gateway during a deploy), the first one wins.
  let own = seed ?? "";
  let superseded = false;
  return {
    accept(ev) {
      if (superseded) return false;
      if (ev.type !== "RUN_STARTED") return true;
      const rid = typeof ev.runId === "string" ? ev.runId : "";
      if (!rid) return true;
      if (!own) {
        own = rid;
        return true;
      }
      if (rid !== own) {
        superseded = true;
        return false;
      }
      return true;
    },
    get superseded() {
      return superseded;
    },
  };
}
