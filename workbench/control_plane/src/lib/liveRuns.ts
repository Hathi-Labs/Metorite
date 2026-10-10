/**
 * liveRuns — the ONE poller of `/api/chat/active-sessions` for the whole app.
 *
 * Until WS-51 S1 every mounted `useActiveSessions()` ran its own 5 s loop.
 * Five surfaces mount it (the shell's phone nav, /chat, and three assistant
 * rails), so one open tab sent up to five identical requests every 5 s, and
 * the sidebar had no count at all. Now one module-level loop serves every
 * subscriber. It polls every `VISIBLE_MS` while the tab is visible and every
 * `HIDDEN_MS` while it is hidden, and it stops when the last subscriber
 * leaves. Owning spec: `project-docs/specs/chat_run_continuity.md` §4 S1.
 *
 * Memory only. Nothing here reaches `localStorage`: a run list names another
 * member's threads in the same org. It is bound to the signed-in member like
 * the read cache: `bindIdentity` in `lib/dataCache.ts` empties it through
 * `onClear`, so it never rests on a page reload alone.
 *
 * Fence (R7): `liveRuns.test.ts` — one fetch for N subscribers, and the
 * hidden-tab interval.
 */

import { onClear } from "@/lib/dataCache";

/**
 * `needs_input`: the run waits on the member's answer to a card, or it parked
 * and an answer starts a new run (WS-51 S2, `chat_run_continuity.md` §4 S2).
 * A row with no state is `running`.
 */
export type RunState = "running" | "needs_input";

export type LiveRun = {
  threadId: string;
  /** The agent's slug. "unknown" when the run started before its session row. */
  agentName: string;
  title?: string | null;
  startedAt?: string | null;
  state?: RunState;
  /** The kind of card that waits, when `state` is `needs_input`. */
  askKind?: string | null;
  /**
   * The run's latest step, such as "Search tasks" (WS-51 S3). Plain text that
   * the server caps at 60 characters. The panel draws it as text, never HTML.
   */
  lastStep?: string | null;
};

/** The longest step this store keeps, whatever the server sends. */
export const LAST_STEP_MAX = 60;

function _step(v: unknown): string | null {
  if (typeof v !== "string") return null;
  const s = v.replace(/\s+/g, " ").trim();
  if (!s) return null;
  return s.length > LAST_STEP_MAX ? `${s.slice(0, LAST_STEP_MAX - 1)}…` : s;
}

export const VISIBLE_MS = 5_000;
export const HIDDEN_MS = 30_000;
const ENDPOINT = "/api/chat/active-sessions";
const TIMEOUT_MS = 5_000;

const EMPTY: readonly LiveRun[] = Object.freeze([]);

let _runs: readonly LiveRun[] = EMPTY;
let _key = "";
const _listeners = new Set<() => void>();
let _timer: ReturnType<typeof setTimeout> | null = null;
let _inflight = false;
let _onVisibility: (() => void) | null = null;
/** Bumped when the member changes. A poll that started under an older
 *  generation belongs to the member who asked, and publishes nothing. */
let _generation = 0;

function _hidden(): boolean {
  return typeof document !== "undefined" && document.visibilityState === "hidden";
}

/** The wait before the next poll: slower while the tab is hidden. */
export function pollIntervalMs(): number {
  return _hidden() ? HIDDEN_MS : VISIBLE_MS;
}

function _schedule(ms: number): void {
  if (_timer) clearTimeout(_timer);
  _timer = _listeners.size > 0 ? setTimeout(() => void _poll(), ms) : null;
}

function _publish(runs: readonly LiveRun[]): void {
  // Same contents → same reference, so a subscriber that puts the list in a
  // dependency array does not loop (React #185, see useActiveSessions).
  const key = runs
    .map((r) => `${r.threadId}:${r.agentName}:${r.title ?? ""}:${r.state ?? "running"}:${r.startedAt ?? ""}:${r.lastStep ?? ""}`)
    .sort()
    .join("\n");
  if (key === _key) return;
  _key = key;
  _runs = runs.length ? Object.freeze([...runs]) : EMPTY;
  _listeners.forEach((l) => l());
}

async function _poll(): Promise<void> {
  if (_listeners.size === 0) return;
  // One request at a time. A visibility change during a slow request must
  // not start a second one beside it.
  if (_inflight) return;
  _inflight = true;
  const generation = _generation;
  try {
    const res = await fetch(ENDPOINT, { signal: AbortSignal.timeout(TIMEOUT_MS) });
    if (res.ok && generation === _generation) {
      const data = (await res.json()) as unknown;
      // Checked again after the body: the member may change while it reads.
      if (Array.isArray(data) && generation === _generation) {
        _publish(
          data
            .filter((d): d is LiveRun => !!d && typeof (d as LiveRun).threadId === "string")
            .map((d) => ({
              threadId: d.threadId,
              agentName: typeof d.agentName === "string" && d.agentName ? d.agentName : "unknown",
              title: d.title ?? null,
              startedAt: d.startedAt ?? null,
              state: d.state === "needs_input" ? ("needs_input" as const) : ("running" as const),
              askKind: typeof d.askKind === "string" ? d.askKind : null,
              lastStep: _step(d.lastStep),
            })),
        );
      }
    }
  } catch {
    // Server unreachable — keep the last known list.
  } finally {
    _inflight = false;
    _schedule(pollIntervalMs());
  }
}

/**
 * Subscribe to the live-run list. The first subscriber starts the loop with
 * an immediate poll, and the last one to leave stops it.
 */
export function subscribeLiveRuns(listener: () => void): () => void {
  const first = _listeners.size === 0;
  _listeners.add(listener);
  if (first) {
    if (typeof document !== "undefined" && !_onVisibility) {
      // Back to a visible tab: poll now, not up to 30 s from now.
      _onVisibility = () => {
        if (!_hidden()) void _poll();
        else _schedule(HIDDEN_MS);
      };
      document.addEventListener("visibilitychange", _onVisibility);
    }
    void _poll();
  }
  return () => {
    _listeners.delete(listener);
    if (_listeners.size === 0) {
      if (_timer) clearTimeout(_timer);
      _timer = null;
      if (_onVisibility && typeof document !== "undefined") {
        document.removeEventListener("visibilitychange", _onVisibility);
      }
      _onVisibility = null;
    }
  };
}

// A new member on this browser starts with no runs, not the last member's.
onClear(() => {
  _generation += 1;
  if (_runs === EMPTY) return;
  _key = "";
  _runs = EMPTY;
  _listeners.forEach((l) => l());
});

/** The last list the server returned. A stable reference while unchanged. */
export function getLiveRuns(): readonly LiveRun[] {
  return _runs;
}

/** The server snapshot for `useSyncExternalStore`: always empty. */
export function getServerLiveRuns(): readonly LiveRun[] {
  return EMPTY;
}

/** Tests only: forget every subscriber, timer and result. */
export function _resetLiveRunsForTests(): void {
  _listeners.clear();
  if (_timer) clearTimeout(_timer);
  _timer = null;
  _inflight = false;
  _runs = EMPTY;
  _key = "";
  if (_onVisibility && typeof document !== "undefined") {
    document.removeEventListener("visibilitychange", _onVisibility);
  }
  _onVisibility = null;
}
