/**
 * Follow one live run of the Process flow block (`ai_studio.md` §5.1, slice STU-D8).
 *
 * The block opens the SSE stream of the run console and hands each frame to `onFrame`. A `run`
 * frame that ends the run closes the stream. If the stream is not there, or it drops, the block
 * reads the run again every `pollMs` until the run ends. `stop` closes the stream and the poll.
 * No React import, so a test drives it with a fake stream and fake timers.
 * Fence: `flowFollow.test.ts`.
 */
import { isLive, runStatusOf } from "./flowModel";

/** The part of `EventSource` that the follower uses. */
export interface StreamLike {
  onmessage: ((m: { data: string }) => void) | null;
  onerror: (() => void) | null;
  close(): void;
}

export interface RunRead {
  status: string;
  node_results: unknown;
}

export interface FollowDeps {
  /** Opens the stream of a run, or is null where there is no `EventSource`. */
  openStream: ((url: string) => StreamLike) | null;
  read: (runId: string) => Promise<RunRead>;
  /** A frame of the stream, already parsed. */
  onFrame: (frame: unknown) => void;
  /** A run that the poll read again. */
  onRead: (run: RunRead) => void;
  pollMs: number;
}

export function followRun(runId: string, deps: FollowDeps): () => void {
  let stream: StreamLike | null = null;
  let timer: ReturnType<typeof setInterval> | null = null;
  let dead = false;

  const stop = () => {
    dead = true;
    stream?.close();
    stream = null;
    if (timer) clearInterval(timer);
    timer = null;
  };

  const poll = () => {
    if (timer || dead) return;
    timer = setInterval(() => {
      deps.read(runId).then((run) => {
        if (dead) return;
        deps.onRead(run);
        if (!isLive(run.status)) stop();
      }).catch(() => { /* the next tick tries again */ });
    }, deps.pollMs);
  };

  if (!deps.openStream) {
    poll();
    return stop;
  }
  const es = deps.openStream(`/api/workflows/runs/${encodeURIComponent(runId)}/stream`);
  stream = es;
  es.onmessage = (m) => {
    if (dead) return;
    let frame: unknown;
    try { frame = JSON.parse(m.data); } catch { return; }
    deps.onFrame(frame);
    const status = runStatusOf(frame);
    if (status && !isLive(status)) stop();
  };
  // A dropped stream is not an error: read the run again until it ends.
  es.onerror = () => {
    es.close();
    stream = null;
    poll();
  };
  return stop;
}
